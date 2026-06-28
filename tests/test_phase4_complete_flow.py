import pytest

from tokenshare.core.expansion import ExpansionDecision, SplitStrategyInvocation
from tokenshare.core.models import Attempt, AttemptState, TaskState
from tokenshare.core.verification import build_verification_report, digest_json
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
SCOPE_HASH = "sha256:scope_complete"


def test_complete_decision_records_decision_then_completed_state_in_completion_batch(
    tmp_path,
) -> None:
    context = _make_complete_context(tmp_path)
    before_count = len(context.ledger.read_all())

    result = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert result.decision == context.decision
    assert result.task_unit.state == TaskState.COMPLETED
    assert result.events == tuple(new_events)
    assert [event.event_type for event in new_events] == [
        EventType.EXPANSION_DECISION_RECORDED,
        EventType.TASK_UNIT_STATE_CHANGED,
    ]
    assert [event.batch_index for event in new_events] == [1, 2]
    assert {event.batch_size for event in new_events} == {2}
    assert {event.batch_id for event in new_events} == {
        f"completion_batch:{context.decision.expansion_decision_id}"
    }
    assert new_events[0].payload["action"] == "complete"
    assert new_events[0].payload["source_invocation_id"] == (
        context.invocation.invocation_id
    )
    assert new_events[1].payload["old_state"] == "Processing"
    assert new_events[1].payload["new_state"] == "Completed"
    assert new_events[1].causation_event_id == new_events[0].event_id
    assert context.ledger.verify_hash_chain()


def test_complete_decision_does_not_write_proposal_merge_plan_task_expanded_or_child_events(
    tmp_path,
) -> None:
    context = _make_complete_context(tmp_path)

    context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_no_graph",
    )

    forbidden = {
        EventType.DECOMPOSITION_PROPOSAL_RECORDED,
        EventType.MERGE_PLAN_RECORDED,
        EventType.TASK_EXPANDED,
        EventType.TASK_UNIT_CREATED,
        EventType.TASK_RELATION_CREATED,
    }
    assert not any(event.event_type in forbidden for event in context.ledger.read_all())


def test_missing_invocation_rejects_complete_decision(tmp_path) -> None:
    context = _make_complete_context(tmp_path, append_invocation=False)
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="invocation"):
        context.engine.record_complete_decision(
            decision=context.decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_missing_invocation",
        )

    assert len(context.ledger.read_all()) == before_count


def test_failed_invocation_rejects_complete_decision(tmp_path) -> None:
    context = _make_complete_context(
        tmp_path,
        invocation_status="failed",
        invocation_error_kind="exception",
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="succeeded"):
        context.engine.record_complete_decision(
            decision=context.decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_failed_invocation",
        )

    assert len(context.ledger.read_all()) == before_count


def test_canonical_selection_mismatch_rejects_complete_decision(tmp_path) -> None:
    context = _make_complete_context(
        tmp_path,
        decision_overrides={
            "canonical_selection_id": "canonical_selection:task_demo:other_unit"
        },
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="canonical"):
        context.engine.record_complete_decision(
            decision=context.decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_canonical_mismatch",
        )

    assert len(context.ledger.read_all()) == before_count


def test_scope_mismatch_rejects_complete_decision(tmp_path) -> None:
    context = _make_complete_context(
        tmp_path,
        decision_overrides={"expansion_scope_hash": "sha256:different_scope"},
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="scope"):
        context.engine.record_complete_decision(
            decision=context.decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_scope_mismatch",
        )

    assert len(context.ledger.read_all()) == before_count


@pytest.mark.parametrize(
    ("invocation_overrides", "decision_overrides"),
    [
        ({}, {"plugin_descriptor_digest": "sha256:not_the_frozen_descriptor"}),
        (
            {"split_strategy_id": "missing_strategy_v1"},
            {"split_strategy_id": "missing_strategy_v1"},
        ),
    ],
)
def test_descriptor_digest_or_strategy_id_mismatch_rejects_complete_decision(
    tmp_path,
    invocation_overrides,
    decision_overrides,
) -> None:
    context = _make_complete_context(
        tmp_path,
        invocation_overrides=invocation_overrides,
        decision_overrides=decision_overrides,
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="descriptor|strategy"):
        context.engine.record_complete_decision(
            decision=context.decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_descriptor_mismatch",
        )

    assert len(context.ledger.read_all()) == before_count


def test_duplicate_complete_decision_same_payload_idempotent_different_payload_conflicts(
    tmp_path,
) -> None:
    context = _make_complete_context(tmp_path)
    first = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_idempotent",
    )
    count_after_first = len(context.ledger.read_all())

    retry = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_idempotent",
    )
    conflicting_decision = _complete_decision(
        canonical_selection=context.canonical_selection,
        plugin_descriptor_digest=context.plugin_descriptor_digest,
        source_invocation_id=context.invocation.invocation_id,
        action_body={
            **context.decision.action_body,
            "completion_evidence": {
                **context.decision.action_body["completion_evidence"],
                "plugin_completion_summary": "changed summary",
            },
        },
    )

    with pytest.raises(ValueError, match="conflict"):
        context.engine.record_complete_decision(
            decision=conflicting_decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_idempotent",
        )

    assert retry.events[0].event_id == first.events[0].event_id
    assert retry.events[1].event_id == first.events[1].event_id
    assert len(context.ledger.read_all()) == count_after_first


def test_completion_evidence_fields_are_inline_in_action_body(tmp_path) -> None:
    context = _make_complete_context(tmp_path)

    result = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_evidence",
    )

    action_body = result.events[0].payload["action_body"]
    evidence = action_body["completion_evidence"]
    assert set(evidence) == {
        "completion_kind",
        "validator_policy_id",
        "verification_report_id",
        "canonical_selection_id",
        "canonical_output_bundle_digest",
        "completed_output_refs",
        "plugin_completion_summary",
    }
    assert evidence["canonical_selection_id"] == context.canonical_selection.canonical_selection_id
    assert evidence["canonical_output_bundle_digest"] == (
        context.canonical_selection.canonical_output_bundle_digest
    )
    assert evidence["completed_output_refs"]["answer"]["artifact_id"] == "artifact_answer"


@pytest.mark.parametrize(
    ("evidence_override", "error_match"),
    [
        ({"verification_report_id": "verification_report_other"}, "verification report"),
        ({"validator_policy_id": "other_validator"}, "validator policy"),
    ],
)
def test_completion_evidence_must_match_selected_verification_report(
    tmp_path, evidence_override: dict, error_match: str
) -> None:
    context = _make_complete_context(tmp_path)
    bad_evidence = {
        **context.decision.action_body["completion_evidence"],
        **evidence_override,
    }
    bad_decision = _complete_decision(
        canonical_selection=context.canonical_selection,
        plugin_descriptor_digest=context.plugin_descriptor_digest,
        source_invocation_id=context.invocation.invocation_id,
        action_body={"completion_evidence": bad_evidence},
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match=error_match):
        context.engine.record_complete_decision(
            decision=bad_decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_bad_evidence",
        )

    assert len(context.ledger.read_all()) == before_count


def test_completion_evidence_may_include_plugin_direct_outputs_but_not_drop_canonical_refs(
    tmp_path,
) -> None:
    context = _make_complete_context(tmp_path)
    extra_output = make_artifact_ref(artifact_id="artifact_plugin_direct_answer")
    expanded_evidence = {
        **context.decision.action_body["completion_evidence"],
        "completed_output_refs": {
            **context.decision.action_body["completion_evidence"]["completed_output_refs"],
            "plugin_direct_answer": extra_output.to_dict(),
        },
    }
    expanded_decision = _complete_decision(
        canonical_selection=context.canonical_selection,
        plugin_descriptor_digest=context.plugin_descriptor_digest,
        source_invocation_id=context.invocation.invocation_id,
        action_body={"completion_evidence": expanded_evidence},
    )

    result = context.engine.record_complete_decision(
        decision=expanded_decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_extra_outputs",
    )

    completed_refs = result.events[0].payload["action_body"]["completion_evidence"][
        "completed_output_refs"
    ]
    assert completed_refs["answer"]["artifact_id"] == "artifact_answer"
    assert completed_refs["plugin_direct_answer"]["artifact_id"] == (
        "artifact_plugin_direct_answer"
    )

    missing_canonical = {
        **context.decision.action_body["completion_evidence"],
        "completed_output_refs": {"plugin_direct_answer": extra_output.to_dict()},
    }
    bad_decision = _complete_decision(
        canonical_selection=context.canonical_selection,
        plugin_descriptor_digest=context.plugin_descriptor_digest,
        source_invocation_id=context.invocation.invocation_id,
        action_body={"completion_evidence": missing_canonical},
    )
    with pytest.raises(ValueError, match="canonical output refs"):
        context.engine.record_complete_decision(
            decision=bad_decision,
            task_unit=context.task_unit,
            correlation_id="corr_complete_missing_canonical",
        )


def test_complete_evidence_uses_selected_verification_event_seq_when_report_ids_repeat(
    tmp_path,
) -> None:
    context = _make_complete_context(tmp_path, duplicate_report_id_decoy=True)
    before_count = len(context.ledger.read_all())

    result = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_duplicate_report_id",
    )

    assert len(context.ledger.read_all()) == before_count + 2
    assert result.events[0].event_type == EventType.EXPANSION_DECISION_RECORDED
    assert result.events[1].event_type == EventType.TASK_UNIT_STATE_CHANGED


class _CompleteContext:
    def __init__(
        self,
        *,
        engine,
        ledger,
        task_unit,
        invocation,
        decision,
        canonical_selection,
        plugin_descriptor_digest,
    ) -> None:
        self.engine = engine
        self.ledger = ledger
        self.task_unit = task_unit
        self.invocation = invocation
        self.decision = decision
        self.canonical_selection = canonical_selection
        self.plugin_descriptor_digest = plugin_descriptor_digest


def _make_complete_context(
    tmp_path,
    *,
    append_invocation: bool = True,
    invocation_status: str = "succeeded",
    invocation_error_kind: str | None = None,
    invocation_overrides: dict | None = None,
    decision_overrides: dict | None = None,
    duplicate_report_id_decoy: bool = False,
) -> _CompleteContext:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    store = ArtifactStore(tmp_path)
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    descriptor = make_plugin_descriptor()
    _record_registry_snapshot(engine)
    canonical_selection = _record_canonical_outputs(
        engine,
        duplicate_report_id_decoy=duplicate_report_id_decoy,
    )
    task_unit = make_unit("unit_ready", state=TaskState.PROCESSING)
    invocation = _split_invocation(
        canonical_selection=canonical_selection,
        plugin_descriptor_digest=descriptor.descriptor_digest,
        status=invocation_status,
        error_kind=invocation_error_kind,
        **(invocation_overrides or {}),
    )
    if append_invocation:
        _append_invocation_event(ledger, invocation)
    decision_kwargs = {
        "canonical_selection": canonical_selection,
        "plugin_descriptor_digest": descriptor.descriptor_digest,
        "source_invocation_id": invocation.invocation_id,
        **(decision_overrides or {}),
    }
    decision = _complete_decision(**decision_kwargs)
    return _CompleteContext(
        engine=engine,
        ledger=ledger,
        task_unit=task_unit,
        invocation=invocation,
        decision=decision,
        canonical_selection=canonical_selection,
        plugin_descriptor_digest=descriptor.descriptor_digest,
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


def _record_canonical_outputs(
    engine: ProtocolEngine, *, duplicate_report_id_decoy: bool = False
):
    answer_ref = make_artifact_ref("artifact_answer")
    if duplicate_report_id_decoy:
        decoy_ref = make_artifact_ref("artifact_decoy")
        decoy_attempt = Attempt(
            attempt_id="attempt_decoy",
            task_id="task_demo",
            unit_id="unit_ready",
            lease_id="lease_attempt_decoy",
            client_id="client_local",
            state=AttemptState.SUBMITTED,
            attempt_kind="primary",
            created_at=NOW,
            started_at=NOW,
            submitted_at=NOW,
            candidate_output_refs={"answer": decoy_ref},
            metadata={},
        )
        engine.record_verification(
            report=_verification_report(
                verification_report_id="verification_report_complete",
                attempt=decoy_attempt,
                submission_id="submission_decoy",
                submission_event_seq=2,
                answer_ref=decoy_ref,
                validator_policy_id="decoy_validator_v1",
            ),
            attempt=decoy_attempt,
            correlation_id="corr_verification_decoy",
        )
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
    report = _verification_report(
        verification_report_id="verification_report_complete",
        attempt=attempt,
        submission_id="submission_complete",
        submission_event_seq=3,
        answer_ref=answer_ref,
        validator_policy_id="structured_report_stub_validator_v1",
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


def _verification_report(
    *,
    verification_report_id: str,
    attempt: Attempt,
    submission_id: str,
    submission_event_seq: int,
    answer_ref,
    validator_policy_id: str,
):
    return build_verification_report(
        verification_report_id=verification_report_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=submission_id,
        submission_event_seq=submission_event_seq,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_answer",
        validator_policy_id=validator_policy_id,
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


def _split_invocation(
    *,
    canonical_selection,
    plugin_descriptor_digest: str,
    status: str,
    error_kind: str | None = None,
    split_strategy_id: str = STRATEGY_ID,
) -> SplitStrategyInvocation:
    return SplitStrategyInvocation(
        invocation_id="split_invocation:scope_complete:attempt:1",
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
        split_strategy_params_digest=PARAMS_DIGEST,
        status=status,
        result_action="complete" if status == "succeeded" else None,
        result_digest="sha256:split_complete_result" if status == "succeeded" else None,
        error_kind=error_kind,
        error_summary="split invocation failed" if status != "succeeded" else None,
        started_at=NOW,
        completed_at=NOW,
    )


def _append_invocation_event(ledger: EventLedger, invocation: SplitStrategyInvocation) -> None:
    payload = {
        "schema_version": "phase4.split_strategy_invocation_record.v1",
        "invocation": invocation.to_dict(),
        "task_id": invocation.task_id,
        "unit_id": invocation.unit_id,
        "canonical_selection_id": invocation.canonical_selection_id,
        "canonical_output_bundle_digest": invocation.canonical_output_bundle_digest,
        "plugin_id": invocation.plugin_id,
        "plugin_version": invocation.plugin_version,
        "plugin_descriptor_digest": invocation.plugin_descriptor_digest,
        "split_strategy_id": invocation.split_strategy_id,
        "split_strategy_params_digest": invocation.split_strategy_params_digest,
        "expansion_scope_hash": invocation.expansion_scope_hash,
        "status": invocation.status,
        "result_action": invocation.result_action,
        "result_digest": invocation.result_digest,
        "error_kind": invocation.error_kind,
        "error_summary": invocation.error_summary,
        "started_at": invocation.started_at,
        "completed_at": invocation.completed_at,
    }
    ledger.append(
        event_type=EventType.SPLIT_STRATEGY_INVOCATION_RECORDED,
        object_type="SplitStrategyInvocation",
        object_id=invocation.invocation_id,
        task_id=invocation.task_id,
        actor={"kind": "protocol_engine"},
        correlation_id="corr_split_prereq",
        idempotency_key=(
            f"split_invocation:{invocation.expansion_scope_hash}:"
            f"attempt:{invocation.invocation_attempt_no}"
        ),
        payload=payload,
        occurred_at=invocation.completed_at,
    )


def _complete_decision(
    *,
    canonical_selection,
    plugin_descriptor_digest: str,
    source_invocation_id: str,
    plugin_id: str = "structured_report_stub",
    plugin_version: str = "0.1.0",
    split_strategy_id: str = STRATEGY_ID,
    split_strategy_params_digest: str = PARAMS_DIGEST,
    canonical_selection_id: str | None = None,
    canonical_output_bundle_digest: str | None = None,
    expansion_scope_hash: str = SCOPE_HASH,
    action_body: dict | None = None,
) -> ExpansionDecision:
    evidence = {
        "completion_kind": "canonical_bundle_complete",
        "validator_policy_id": "structured_report_stub_validator_v1",
        "verification_report_id": "verification_report_complete",
        "canonical_selection_id": canonical_selection_id
        or canonical_selection.canonical_selection_id,
        "canonical_output_bundle_digest": canonical_output_bundle_digest
        or canonical_selection.canonical_output_bundle_digest,
        "completed_output_refs": {
            name: ref.to_dict()
            for name, ref in canonical_selection.canonical_output_refs.items()
        },
        "plugin_completion_summary": "canonical output already satisfies parent unit",
    }
    return ExpansionDecision(
        expansion_decision_id=f"expansion_decision:{SCOPE_HASH}",
        task_id="task_demo",
        unit_id="unit_ready",
        canonical_selection_id=canonical_selection_id
        or canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest
        or canonical_selection.canonical_output_bundle_digest,
        expansion_scope_hash=expansion_scope_hash,
        action="complete",
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        plugin_descriptor_digest=plugin_descriptor_digest,
        split_strategy_id=split_strategy_id,
        split_strategy_params_digest=split_strategy_params_digest,
        source_invocation_id=source_invocation_id,
        action_body=action_body or {"completion_evidence": evidence},
        decided_at=NOW,
    )
