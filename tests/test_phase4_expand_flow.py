import json
from dataclasses import replace
from hashlib import sha256

import pytest

from tokenshare.core.expansion import DecompositionProposal, ExpansionDecision, MergePlan, SplitStrategyInvocation
from tokenshare.core.models import Attempt, AttemptState, TaskState
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import build_verification_report
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.phase2_fixtures import make_artifact_ref, make_config, make_unit
from tests.phase3_fixtures import make_executor_descriptor, make_plugin_descriptor


NOW = "2026-06-25T00:00:00Z"
STRATEGY_ID = "structured_report_sections_v1"
PARAMS_DIGEST = "sha256:params"
SCOPE_HASH = "sha256:scope_expand"


def test_valid_expand_records_proposal_decision_merge_plan_child_units_relations_and_task_expanded_in_one_batch(
    tmp_path,
) -> None:
    context = _make_expand_context(tmp_path)
    before_count = len(context.ledger.read_all())

    result = context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert result.decision == context.decision
    assert result.proposal_ref.artifact_id == context.proposal_id
    assert result.merge_plan_ref.artifact_id == context.merge_plan_id
    assert result.events == tuple(new_events)
    assert [event.event_type for event in new_events] == [
        EventType.DECOMPOSITION_PROPOSAL_RECORDED,
        EventType.EXPANSION_DECISION_RECORDED,
        EventType.MERGE_PLAN_RECORDED,
        EventType.TASK_UNIT_CREATED,
        EventType.TASK_UNIT_CREATED,
        EventType.TASK_RELATION_CREATED,
        EventType.TASK_EXPANDED,
    ]
    assert {event.batch_id for event in new_events} == {
        f"expansion_batch:{context.decision.expansion_decision_id}"
    }
    assert [event.batch_index for event in new_events] == list(range(1, 8))
    assert {event.batch_size for event in new_events} == {7}
    assert new_events[-1].event_type == EventType.TASK_EXPANDED
    assert new_events[-1].payload["child_unit_ids"] == [
        unit.unit_id for unit in result.child_units
    ]
    assert new_events[-1].payload["relation_ids"] == [
        relation.relation_id for relation in result.relations
    ]
    assert set(result.task_graph.units) == {
        "unit_ready",
        result.child_units[0].unit_id,
        result.child_units[1].unit_id,
    }
    assert result.task_graph.ready_unit_ids() == [result.child_units[0].unit_id]
    assert context.ledger.verify_hash_chain()


def test_task_expanded_is_last_semantic_event(tmp_path) -> None:
    context = _make_expand_context(tmp_path)

    result = context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand_marker",
    )

    assert result.events[-1].event_type == EventType.TASK_EXPANDED
    assert all(event.event_type != EventType.TASK_EXPANDED for event in result.events[:-1])


def test_invalid_proposal_does_not_mutate_graph_or_write_authoritative_events(tmp_path) -> None:
    context = _make_expand_context(
        tmp_path,
        expected_outputs=[
            _expected_output("summary", merge_slot_id="slot_intro"),
        ],
    )
    before_count = len(context.ledger.read_all())
    before_unit_ids = set(context.graph.units)

    with pytest.raises(ValueError, match="parent required output"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_invalid",
        )

    assert set(context.graph.units) == before_unit_ids
    new_events = context.ledger.read_all()[before_count:]
    authoritative_types = {
        EventType.DECOMPOSITION_PROPOSAL_RECORDED,
        EventType.EXPANSION_DECISION_RECORDED,
        EventType.MERGE_PLAN_RECORDED,
        EventType.TASK_UNIT_CREATED,
        EventType.TASK_RELATION_CREATED,
        EventType.TASK_EXPANDED,
    }
    assert not any(event.event_type in authoritative_types for event in new_events)


def test_staged_artifact_may_exist_but_is_non_authoritative_without_recorded_event(tmp_path) -> None:
    context = _make_expand_context(
        tmp_path,
        expected_outputs=[
            _expected_output("summary", merge_slot_id="slot_intro"),
        ],
    )

    with pytest.raises(ValueError, match="parent required output"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_staged",
        )

    assert (context.store.artifact_dir / context.proposal_id).exists()
    assert not any(
        event.event_type == EventType.DECOMPOSITION_PROPOSAL_RECORDED
        and event.object_id == context.proposal_id
        for event in context.ledger.read_all()
    )


def test_duplicate_expansion_same_payload_is_idempotent(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    first = context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand_idempotent",
    )
    count_after_first = len(context.ledger.read_all())

    retry = context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand_idempotent",
    )

    assert [event.event_id for event in retry.events] == [
        event.event_id for event in first.events
    ]
    assert [unit.unit_id for unit in retry.child_units] == [
        unit.unit_id for unit in first.child_units
    ]
    assert [relation.relation_id for relation in retry.relations] == [
        relation.relation_id for relation in first.relations
    ]
    assert len(context.ledger.read_all()) == count_after_first


def test_duplicate_expansion_different_payload_conflicts_without_second_effective_expansion(
    tmp_path,
) -> None:
    context = _make_expand_context(tmp_path)
    context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand_conflict",
    )
    count_after_first = len(context.ledger.read_all())
    conflicting = _make_expand_context(
        tmp_path,
        intro_weight=2.0,
    )

    with pytest.raises(ValueError, match="conflict|partial"):
        conflicting.engine.record_expand_decision(
            decision=conflicting.decision,
            proposal=conflicting.proposal,
            merge_plan=conflicting.merge_plan,
            parent_unit=conflicting.parent_unit,
            graph=conflicting.graph,
            correlation_id="corr_expand_conflict",
        )

    assert len(context.ledger.read_all()) == count_after_first
    expanded_markers = [
        event
        for event in context.ledger.read_all()
        if event.event_type == EventType.TASK_EXPANDED
    ]
    assert len(expanded_markers) == 1


def test_expand_rejects_proposal_body_digest_mismatch(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    before_count = len(context.ledger.read_all())
    before_unit_ids = set(context.graph.units)
    context.proposal.child_specs[0]["weight"] = 2.0

    with pytest.raises(ValueError, match="proposal body digest"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_bad_proposal_digest",
        )

    assert set(context.graph.units) == before_unit_ids
    assert len(context.ledger.read_all()) == before_count


def test_expand_rejects_merge_plan_body_digest_mismatch(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    before_count = len(context.ledger.read_all())
    before_unit_ids = set(context.graph.units)
    context.merge_plan.required_slots[0]["output_schema_digest"] = "sha256:changed"

    with pytest.raises(ValueError, match="merge plan body digest"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_bad_merge_digest",
        )

    assert set(context.graph.units) == before_unit_ids
    assert len(context.ledger.read_all()) == before_count


def test_expand_requires_processing_parent_unit(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    context.parent_unit = make_unit("unit_ready", state=TaskState.READY)
    context.graph.units[context.parent_unit.unit_id] = context.parent_unit
    before_count = len(context.ledger.read_all())
    before_unit_ids = set(context.graph.units)

    with pytest.raises(ValueError, match="Processing"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_parent_not_processing",
        )

    assert set(context.graph.units) == before_unit_ids
    assert len(context.ledger.read_all()) == before_count


def test_expand_rejects_action_body_count_mismatch(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    evidence = {
        **context.decision.action_body["expand_evidence"],
        "child_count": 99,
    }
    bad_decision = replace(
        context.decision,
        action_body={"expand_evidence": evidence},
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="expand_evidence"):
        context.engine.record_expand_decision(
            decision=bad_decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_bad_evidence_count",
        )

    assert len(context.ledger.read_all()) == before_count


def test_expand_rejects_merge_policy_descriptor_digest_mismatch(tmp_path) -> None:
    context = _make_expand_context(tmp_path)
    context.merge_plan.merge_policy_ref["merge_policy_descriptor_digest"] = "sha256:other"
    merge_plan_digest = _merge_plan_body_digest(context.merge_plan)
    merge_plan_id = f"merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
    context.merge_plan.merge_plan_header["merge_plan_id"] = merge_plan_id
    context.merge_plan.merge_plan_header["merge_plan_digest"] = merge_plan_digest
    bad_evidence = {
        **context.decision.action_body["expand_evidence"],
        "merge_plan_id": merge_plan_id,
        "merge_plan_digest": merge_plan_digest,
    }
    bad_decision = replace(
        context.decision,
        merge_plan_id=merge_plan_id,
        merge_plan_digest=merge_plan_digest,
        action_body={"expand_evidence": bad_evidence},
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="merge policy descriptor"):
        context.engine.record_expand_decision(
            decision=bad_decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_bad_merge_descriptor",
        )

    assert len(context.ledger.read_all()) == before_count


@pytest.mark.parametrize(
    ("append_invocation", "invocation_status", "error_match"),
    [
        (False, "succeeded", "invocation"),
        (True, "failed", "succeeded"),
    ],
)
def test_source_invocation_missing_or_failed_rejects_expand(
    tmp_path,
    append_invocation,
    invocation_status,
    error_match,
) -> None:
    context = _make_expand_context(
        tmp_path,
        append_invocation=append_invocation,
        invocation_status=invocation_status,
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match=error_match):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_bad_invocation",
        )

    assert len(context.ledger.read_all()) == before_count


def test_canonical_selection_mismatch_rejects_expand(tmp_path) -> None:
    context = _make_expand_context(
        tmp_path,
        canonical_selection_id="canonical_selection:task_demo:other_unit",
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="canonical"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_canonical_mismatch",
        )

    assert len(context.ledger.read_all()) == before_count


@pytest.mark.parametrize(
    "overrides",
    [
        {"plugin_descriptor_digest": "sha256:not_frozen_descriptor"},
        {"split_strategy_id": "missing_strategy_v1"},
        {
            "invocation_split_strategy_params_digest": "sha256:original_params",
            "split_strategy_params_digest": "sha256:different_params",
        },
    ],
)
def test_descriptor_digest_strategy_id_or_params_digest_mismatch_rejects_expand(
    tmp_path,
    overrides,
) -> None:
    context = _make_expand_context(tmp_path, **overrides)
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="descriptor|strategy|params"):
        context.engine.record_expand_decision(
            decision=context.decision,
            proposal=context.proposal,
            merge_plan=context.merge_plan,
            parent_unit=context.parent_unit,
            graph=context.graph,
            correlation_id="corr_expand_identity_mismatch",
        )

    assert len(context.ledger.read_all()) == before_count


def test_child_unit_and_relation_ids_are_deterministic(tmp_path) -> None:
    first = _make_expand_context(tmp_path / "first")
    second = _make_expand_context(tmp_path / "second")

    first_result = first.engine.record_expand_decision(
        decision=first.decision,
        proposal=first.proposal,
        merge_plan=first.merge_plan,
        parent_unit=first.parent_unit,
        graph=first.graph,
        correlation_id="corr_expand_deterministic",
    )
    second_result = second.engine.record_expand_decision(
        decision=second.decision,
        proposal=second.proposal,
        merge_plan=second.merge_plan,
        parent_unit=second.parent_unit,
        graph=second.graph,
        correlation_id="corr_expand_deterministic",
    )

    assert [unit.unit_id for unit in first_result.child_units] == [
        unit.unit_id for unit in second_result.child_units
    ]
    assert [relation.relation_id for relation in first_result.relations] == [
        relation.relation_id for relation in second_result.relations
    ]
    assert [unit.metadata["child_logical_key"] for unit in first_result.child_units] == [
        "intro",
        "summary",
    ]


def test_child_initial_states_are_derived_not_plugin_supplied(tmp_path) -> None:
    context = _make_expand_context(tmp_path)

    result = context.engine.record_expand_decision(
        decision=context.decision,
        proposal=context.proposal,
        merge_plan=context.merge_plan,
        parent_unit=context.parent_unit,
        graph=context.graph,
        correlation_id="corr_expand_state",
    )

    states_by_key = {
        unit.metadata["child_logical_key"]: unit.state for unit in result.child_units
    }
    assert states_by_key == {
        "intro": TaskState.READY,
        "summary": TaskState.BLOCKED,
    }
    assert result.child_units[1].plugin_payload["section_role"] == "summary"


class _ExpandContext:
    def __init__(
        self,
        *,
        engine,
        ledger,
        store,
        parent_unit,
        graph,
        invocation,
        decision,
        proposal,
        merge_plan,
        proposal_id,
        merge_plan_id,
    ) -> None:
        self.engine = engine
        self.ledger = ledger
        self.store = store
        self.parent_unit = parent_unit
        self.graph = graph
        self.invocation = invocation
        self.decision = decision
        self.proposal = proposal
        self.merge_plan = merge_plan
        self.proposal_id = proposal_id
        self.merge_plan_id = merge_plan_id


def _make_expand_context(
    tmp_path,
    *,
    append_invocation: bool = True,
    invocation_status: str = "succeeded",
    canonical_selection_id: str | None = None,
    plugin_descriptor_digest: str | None = None,
    split_strategy_id: str = STRATEGY_ID,
    invocation_split_strategy_params_digest: str | None = None,
    split_strategy_params_digest: str = PARAMS_DIGEST,
    expected_outputs: list[dict] | None = None,
    intro_weight: float = 1.0,
) -> _ExpandContext:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    store = ArtifactStore(tmp_path)
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    descriptor = make_plugin_descriptor()
    descriptor_digest = plugin_descriptor_digest or descriptor.descriptor_digest
    _record_registry_snapshot(engine)
    canonical_selection = _record_canonical_outputs(engine)
    parent_unit = make_unit("unit_ready", state=TaskState.PROCESSING)
    graph = TaskGraph(
        task_id="task_demo",
        units={parent_unit.unit_id: parent_unit},
        relations=[],
        canonical_outputs_by_unit_id={
            parent_unit.unit_id: canonical_selection.canonical_output_refs
        },
        protocol_config=make_config(),
    )
    invocation = _split_invocation(
        canonical_selection=canonical_selection,
        plugin_descriptor_digest=descriptor_digest,
        status=invocation_status,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=(
            invocation_split_strategy_params_digest or split_strategy_params_digest
        ),
    )
    if append_invocation:
        engine.record_split_strategy_invocation(
            invocation=invocation,
            correlation_id="corr_split_prereq",
        )

    effective_canonical_selection_id = (
        canonical_selection_id or canonical_selection.canonical_selection_id
    )
    proposal = _proposal(
        proposal_id="decomposition_proposal_pending",
        proposal_digest="sha256:pending_proposal_digest",
        canonical_selection_id=effective_canonical_selection_id,
        canonical_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        plugin_descriptor_digest=descriptor_digest,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=split_strategy_params_digest,
        expected_outputs=expected_outputs,
        intro_weight=intro_weight,
    )
    proposal_digest = _proposal_body_digest(proposal)
    proposal_id = f"decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
    proposal.proposal_header["proposal_id"] = proposal_id
    proposal.proposal_header["proposal_digest"] = proposal_digest
    child_unit_ids_by_key = _expected_child_unit_ids(
        proposal_digest=proposal_digest,
        parent_unit_id=parent_unit.unit_id,
    )
    merge_plan = _merge_plan(
        merge_plan_id="merge_plan_pending",
        merge_plan_digest="sha256:pending_merge_plan_digest",
        proposal_id=proposal_id,
        decision_id=f"expansion_decision:{SCOPE_HASH}",
        canonical_selection_id=effective_canonical_selection_id,
        child_unit_ids_by_key=child_unit_ids_by_key,
    )
    merge_plan_digest = _merge_plan_body_digest(merge_plan)
    merge_plan_id = f"merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
    merge_plan.merge_plan_header["merge_plan_id"] = merge_plan_id
    merge_plan.merge_plan_header["merge_plan_digest"] = merge_plan_digest
    decision = _expand_decision(
        canonical_selection=canonical_selection,
        canonical_selection_id=effective_canonical_selection_id,
        plugin_descriptor_digest=descriptor_digest,
        source_invocation_id=invocation.invocation_id,
        proposal_id=proposal_id,
        proposal_digest=proposal_digest,
        merge_plan_id=merge_plan_id,
        merge_plan_digest=merge_plan_digest,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=split_strategy_params_digest,
    )
    return _ExpandContext(
        engine=engine,
        ledger=ledger,
        store=store,
        parent_unit=parent_unit,
        graph=graph,
        invocation=invocation,
        decision=decision,
        proposal=proposal,
        merge_plan=merge_plan,
        proposal_id=proposal_id,
        merge_plan_id=merge_plan_id,
    )


def _record_registry_snapshot(engine: ProtocolEngine) -> None:
    plugin_registry = PluginRegistry()
    executor_registry = ExecutorRegistry()
    plugin_registry.register(make_plugin_descriptor())
    executor_registry.register(make_executor_descriptor())
    engine.record_registry_snapshot(
        task_id="task_demo",
        registry_snapshot_id="registry_snapshot_1",
        plugin_registry=plugin_registry,
        executor_registry=executor_registry,
        now=NOW,
        correlation_id="corr_registry",
    )


def _record_canonical_outputs(engine: ProtocolEngine):
    answer_ref = make_artifact_ref("artifact_answer")
    attempt = Attempt(
        attempt_id="attempt_verified",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_attempt_verified",
        client_id="client_local",
        state=AttemptState.SUBMITTED,
        attempt_kind="primary",
        created_at=NOW,
        started_at=NOW,
        submitted_at=NOW,
        candidate_output_refs={"answer": answer_ref},
        metadata={},
    )
    report = build_verification_report(
        verification_report_id="verification_report_expand",
        task_id="task_demo",
        unit_id="unit_ready",
        attempt_id=attempt.attempt_id,
        submission_id="submission_expand",
        submission_event_seq=3,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_answer",
        validator_policy_id="structured_report_stub_validator_v1",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=make_plugin_descriptor().descriptor_digest,
        status="passed",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={"runtime": "pytest"},
        verifier={"verifier_id": "verifier_local", "verifier_version": "1"},
        started_at=NOW,
        completed_at=NOW,
    )
    verification = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id="corr_verification",
    )
    return engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[verification.event],
        attempts_by_id={verification.attempt.attempt_id: verification.attempt},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id="corr_canonical",
    ).canonical_selection


def _split_invocation(
    *,
    canonical_selection,
    plugin_descriptor_digest: str,
    status: str,
    split_strategy_id: str = STRATEGY_ID,
    split_strategy_params_digest: str = PARAMS_DIGEST,
) -> SplitStrategyInvocation:
    return SplitStrategyInvocation(
        invocation_id="split_invocation:scope_expand:attempt:1",
        invocation_attempt_no=1,
        expansion_scope_hash=SCOPE_HASH,
        task_id="task_demo",
        unit_id="unit_ready",
        canonical_selection_id=canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=plugin_descriptor_digest,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=split_strategy_params_digest,
        status=status,
        result_action="expand" if status == "succeeded" else None,
        result_digest="sha256:split_expand_result" if status == "succeeded" else None,
        error_kind="exception" if status != "succeeded" else None,
        error_summary="split invocation failed" if status != "succeeded" else None,
        started_at=NOW,
        completed_at=NOW,
    )


def _proposal(
    *,
    proposal_id: str,
    proposal_digest: str,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    split_strategy_id: str,
    split_strategy_params_digest: str,
    expected_outputs: list[dict] | None = None,
    intro_weight: float = 1.0,
) -> DecompositionProposal:
    return DecompositionProposal(
        proposal_header={
            "proposal_id": proposal_id,
            "proposal_schema_version": "phase4.decomposition_proposal.v1",
            "task_id": "task_demo",
            "parent_unit_id": "unit_ready",
            "canonical_selection_id": canonical_selection_id,
            "canonical_output_bundle_digest": canonical_output_bundle_digest,
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_digest": plugin_descriptor_digest,
            "split_strategy_id": split_strategy_id,
            "split_strategy_params_digest": split_strategy_params_digest,
            "expansion_scope_hash": SCOPE_HASH,
            "proposal_digest": proposal_digest,
            "created_at": NOW,
        },
        child_specs=[
            _child_spec(
                "intro",
                input_bindings={
                    "parent_answer": {
                        "kind": "parent_output",
                        "output_name": "answer",
                    }
                },
                weight=intro_weight,
            ),
            _child_spec(
                "summary",
                plugin_payload={"section_role": "summary"},
            ),
        ],
        dependency_edges=[
            {
                "edge_logical_key": "edge_intro_summary",
                "source_child_key": "intro",
                "target_child_key": "summary",
                "source_output_name": "answer",
                "target_input_name": "intro_answer",
                "relation_type": "depends_on_output",
            }
        ],
        expected_outputs=expected_outputs
        or [_expected_output("answer", merge_slot_id="slot_intro")],
        merge_slots=[
            {
                "slot_id": "slot_intro",
                "child_key": "intro",
                "child_output_name": "answer",
                "schema_ref": {"schema": "text"},
                "required": True,
                "missing_policy": "block_merge",
            }
        ],
        promotion_guard_evidence={
            "typed_io_checked": True,
            "independently_schedulable_checked": True,
            "validator_policy_checked": True,
            "output_contract_checked": True,
            "no_freeform_thought_checked": True,
            "max_depth_checked": True,
            "max_children_checked": True,
            "evidence_ref": None,
        },
    )


def _merge_plan(
    *,
    merge_plan_id: str,
    merge_plan_digest: str,
    proposal_id: str,
    decision_id: str,
    canonical_selection_id: str,
    child_unit_ids_by_key: dict[str, str],
) -> MergePlan:
    return MergePlan(
        merge_plan_header={
            "merge_plan_id": merge_plan_id,
            "merge_plan_schema_version": "phase4.merge_plan.v1",
            "task_id": "task_demo",
            "parent_unit_id": "unit_ready",
            "canonical_selection_id": canonical_selection_id,
            "decomposition_proposal_id": proposal_id,
            "expansion_decision_id": decision_id,
            "created_by_plugin_id": "structured_report_stub",
            "created_by_plugin_version": "0.1.0",
            "merge_plan_digest": merge_plan_digest,
            "created_at": NOW,
        },
        merge_policy_ref={
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "merge_policy_id": "structured_report_stub_merge_v1",
            "merge_policy_version": "v1",
            "merge_policy_descriptor_digest": make_plugin_descriptor().descriptor_digest,
            "merge_policy_params_digest": "sha256:merge_params",
        },
        required_slots=[
            {
                "slot_key": "slot_intro",
                "source_child_logical_key": "intro",
                "source_child_unit_id": child_unit_ids_by_key["intro"],
                "source_output_name": "answer",
                "output_schema_ref": {"schema": "text"},
                "output_schema_digest": "sha256:schema",
                "required": True,
                "missing_policy": "block_merge",
            }
        ],
        parent_output_mapping=[
            {
                "parent_output_name": "answer",
                "resolution_kind": "merge_plan_output",
                "merge_slot_keys": ["slot_intro"],
                "result_schema_ref": {"schema": "text"},
                "result_schema_digest": "sha256:schema",
            }
        ],
        hash_recording_requirements={
            "record_child_canonical_output_digest": True,
            "record_slot_source_artifact_digest": True,
            "record_merge_input_bundle_digest": True,
        },
        merge_validation_requirements={
            "all_required_slots_canonical": True,
            "slot_schema_check_required": True,
            "merged_output_schema_check_required": True,
            "plugin_merge_validator_policy_id": "structured_report_stub_merge_validator_v1",
        },
        plugin_payload={
            "plugin_defined_schema_ref": {"schema": "structured_report_merge_payload.v1"},
            "plugin_defined_body_digest": "sha256:merge_payload",
            "plugin_defined_body": {"notes": "phase4 expand test"},
        },
    )


def _expand_decision(
    *,
    canonical_selection,
    canonical_selection_id: str,
    plugin_descriptor_digest: str,
    source_invocation_id: str,
    proposal_id: str,
    proposal_digest: str,
    merge_plan_id: str,
    merge_plan_digest: str,
    split_strategy_id: str,
    split_strategy_params_digest: str,
) -> ExpansionDecision:
    return ExpansionDecision(
        expansion_decision_id=f"expansion_decision:{SCOPE_HASH}",
        task_id="task_demo",
        unit_id="unit_ready",
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        expansion_scope_hash=SCOPE_HASH,
        action="expand",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=plugin_descriptor_digest,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=split_strategy_params_digest,
        source_invocation_id=source_invocation_id,
        proposal_id=proposal_id,
        proposal_digest=proposal_digest,
        merge_plan_id=merge_plan_id,
        merge_plan_digest=merge_plan_digest,
        action_body={
            "expand_evidence": {
                "proposal_id": proposal_id,
                "proposal_digest": proposal_digest,
                "merge_plan_id": merge_plan_id,
                "merge_plan_digest": merge_plan_digest,
                "child_count": 2,
                "relation_count": 1,
                "expected_output_count": 1,
                "required_merge_slot_count": 1,
            }
        },
        decided_at=NOW,
    )


def _child_spec(
    child_logical_key: str,
    *,
    input_bindings: dict | None = None,
    plugin_payload: dict | None = None,
    weight: float = 1.0,
) -> dict:
    return {
        "child_logical_key": child_logical_key,
        "unit_type": "section",
        "input_bindings": input_bindings or {},
        "required_outputs": ["answer"],
        "output_contract_refs": {"answer": {"schema": "text"}},
        "validator_policy_id": "structured_report_stub_validator_v1",
        "budget_limit": None,
        "deadline": None,
        "weight": weight,
        "required_capabilities": {"executor": "mock_ai"},
        "plugin_payload": plugin_payload or {},
        "promotion_guard_ref": None,
    }


def _expected_output(output_name: str, *, merge_slot_id: str) -> dict:
    return {
        "output_name": output_name,
        "schema_ref": {"schema": "text"},
        "resolution_kind": "merge_plan_output",
        "child_key": None,
        "child_output_name": None,
        "merge_slot_id": merge_slot_id,
        "required": True,
    }


def _expected_child_unit_ids(*, proposal_digest: str, parent_unit_id: str) -> dict[str, str]:
    stable_suffix = proposal_digest.removeprefix("sha256:").replace(":", "_")
    return {
        "intro": f"unit_{parent_unit_id}_{stable_suffix}_intro",
        "summary": f"unit_{parent_unit_id}_{stable_suffix}_summary",
    }


def _proposal_body_digest(proposal: DecompositionProposal) -> str:
    data = proposal.to_dict()
    header = dict(data["proposal_header"])
    header.pop("proposal_id", None)
    header.pop("proposal_digest", None)
    data["proposal_header"] = header
    return _canonical_digest(data)


def _merge_plan_body_digest(merge_plan: MergePlan) -> str:
    data = merge_plan.to_dict()
    header = dict(data["merge_plan_header"])
    header.pop("merge_plan_id", None)
    header.pop("merge_plan_digest", None)
    data["merge_plan_header"] = header
    return _canonical_digest(data)


def _canonical_digest(data: dict) -> str:
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(canonical).hexdigest()}"
