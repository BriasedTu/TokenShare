from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from tokenshare.core.expansion import DecompositionProposal, ExpansionDecision, MergePlan, SplitStrategyInvocation
from tokenshare.core.merge_coordinator import BatchView
from tokenshare.core.models import Attempt, AttemptState, TaskState, TaskUnit
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import CanonicalSelection, build_verification_report
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent

from tests.phase2_fixtures import make_artifact_ref, make_config, make_unit
from tests.phase3_fixtures import make_executor_descriptor, make_plugin_descriptor


NOW = "2026-06-25T00:00:00Z"
STRATEGY_ID = "structured_report_sections_v1"
PARAMS_DIGEST = "sha256:params"
SCOPE_HASH = "sha256:scope_merge"


@dataclass(frozen=True)
class MergeCreationContext:
    engine: ProtocolEngine
    ledger: EventLedger
    store: ArtifactStore
    graph: TaskGraph
    parent_unit: TaskUnit
    canonical_selection: CanonicalSelection
    canonical_event: LedgerEvent
    canonical_attempt: Attempt
    merge_plan: MergePlan
    merge_plan_event: LedgerEvent
    expansion_batch: BatchView
    expansion_batches: list[BatchView]
    canonical_events: list[LedgerEvent]
    merge_plan_events: list[LedgerEvent]


def make_merge_creation_context(
    tmp_path,
    *,
    canonical_output_artifact_type: str = "canonical_output",
    child_canonical_output_content_hash: str | None = None,
    missing_required_slot: bool = False,
    incomplete_expansion_batch: bool = False,
    drop_task_expanded_marker: bool = False,
) -> MergeCreationContext:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    store = ArtifactStore(tmp_path)
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    _record_registry_snapshot(engine)

    parent_canonical = _record_verified_canonical_output(
        engine,
        unit_id="unit_parent",
        attempt_id="attempt_parent",
        output_ref=make_artifact_ref("artifact_parent_answer"),
    )
    parent_unit = make_unit("unit_parent", state=TaskState.PROCESSING)
    graph = TaskGraph(
        task_id="task_demo",
        units={parent_unit.unit_id: parent_unit},
        relations=[],
        canonical_outputs_by_unit_id={
            parent_canonical.canonical_selection.unit_id: parent_canonical.canonical_selection.canonical_output_refs
        },
        protocol_config=make_config(),
    )

    invocation = _split_invocation(
        canonical_selection=parent_canonical.canonical_selection,
        plugin_descriptor_digest=make_plugin_descriptor().descriptor_digest,
    )
    engine.record_split_strategy_invocation(invocation=invocation, correlation_id="corr_split")

    child_specs = [
        _child_spec("intro", parent_output_name="answer"),
        _child_spec("summary", parent_output_name=None),
    ]
    proposal = _proposal(
        canonical_selection=parent_canonical.canonical_selection,
        child_specs=child_specs,
        merge_slots=[_merge_slot("slot_intro", child_key="intro", child_output_name="answer")],
        expected_outputs=[_expected_output("answer", merge_slot_id="slot_intro")],
    )
    proposal_digest = _proposal_body_digest(proposal)
    proposal.proposal_header["proposal_id"] = f"decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
    proposal.proposal_header["proposal_digest"] = proposal_digest
    intro_child_unit_id = _derive_child_unit_id(
        parent_unit_id=parent_unit.unit_id,
        proposal_digest=proposal_digest,
        child_logical_key="intro",
    )
    summary_child_unit_id = _derive_child_unit_id(
        parent_unit_id=parent_unit.unit_id,
        proposal_digest=proposal_digest,
        child_logical_key="summary",
    )
    merge_slots = [_merge_slot("slot_intro", child_key="intro", child_output_name="answer")]
    if missing_required_slot:
        merge_slots.append(_merge_slot("slot_summary", child_key="summary", child_output_name="answer"))
    merge_plan = _merge_plan(
        canonical_selection=parent_canonical.canonical_selection,
        proposal_id=proposal.proposal_header["proposal_id"],
        required_slot_count=len(merge_slots),
        intro_child_unit_id=intro_child_unit_id,
        summary_child_unit_id=summary_child_unit_id,
    )
    merge_plan_digest = _merge_plan_body_digest(merge_plan)
    merge_plan.merge_plan_header["merge_plan_id"] = f"merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
    merge_plan.merge_plan_header["merge_plan_digest"] = merge_plan_digest

    decision = _expand_decision(
        canonical_selection=parent_canonical.canonical_selection,
        source_invocation_id=invocation.invocation_id,
        proposal_id=proposal.proposal_header["proposal_id"],
        proposal_digest=proposal_digest,
        merge_plan_id=merge_plan.merge_plan_header["merge_plan_id"],
        merge_plan_digest=merge_plan_digest,
        required_merge_slot_count=len(merge_slots),
    )
    expand_result = engine.record_expand_decision(
        decision=decision,
        proposal=proposal,
        merge_plan=merge_plan,
        parent_unit=parent_unit,
        graph=graph,
        correlation_id="corr_expand",
    )

    expansion_events = list(expand_result.events)
    if incomplete_expansion_batch:
        expansion_events = expansion_events[:-1]
    expansion_batch = BatchView(
        batch_id=f"expansion_batch:{decision.expansion_decision_id}",
        events=tuple(expansion_events),
        task_expanded_visible=not drop_task_expanded_marker,
    )

    child_intro_unit = expand_result.child_units[0]
    child_intro_canonical = _record_verified_canonical_output(
        engine,
        unit_id=child_intro_unit.unit_id,
        attempt_id="attempt_child_intro",
        output_ref=_artifact_ref_with_updates(
            make_artifact_ref("artifact_child_answer"),
            artifact_type=canonical_output_artifact_type,
            content_hash=child_canonical_output_content_hash,
        ),
    )
    canonical_events = [child_intro_canonical.event]

    return MergeCreationContext(
        engine=engine,
        ledger=ledger,
        store=store,
        graph=expand_result.task_graph,
        parent_unit=parent_unit,
        canonical_selection=parent_canonical.canonical_selection,
        canonical_event=parent_canonical.event,
        canonical_attempt=parent_canonical.attempt,
        merge_plan=merge_plan,
        merge_plan_event=next(
            event for event in expand_result.events if event.event_type == EventType.MERGE_PLAN_RECORDED
        ),
        expansion_batch=expansion_batch,
        expansion_batches=[expansion_batch],
        canonical_events=canonical_events,
        merge_plan_events=[
            event for event in expand_result.events if event.event_type == EventType.MERGE_PLAN_RECORDED
        ],
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


@dataclass(frozen=True)
class _CanonicalResult:
    event: LedgerEvent
    attempt: Attempt
    canonical_selection: CanonicalSelection


def _record_verified_canonical_output(
    engine: ProtocolEngine,
    *,
    unit_id: str,
    attempt_id: str,
    output_ref,
) -> _CanonicalResult:
    attempt = Attempt(
        attempt_id=attempt_id,
        task_id="task_demo",
        unit_id=unit_id,
        lease_id=f"lease_{attempt_id}",
        client_id="client_local",
        state=AttemptState.SUBMITTED,
        attempt_kind="primary",
        created_at=NOW,
        started_at=NOW,
        submitted_at=NOW,
        candidate_output_refs={"answer": output_ref},
        metadata={},
    )
    report = build_verification_report(
        verification_report_id=f"verification_report_{attempt_id}",
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=f"submission_{attempt_id}",
        submission_event_seq=10,
        candidate_output_refs={"answer": output_ref},
        required_output_names=["answer"],
        output_contract_id="contract_answer",
        validator_policy_id="validator_policy_v1",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=make_plugin_descriptor().descriptor_digest,
        status="passed",
        expected_artifact_hashes={"answer": output_ref.content_hash},
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
        correlation_id=f"corr_verify_{attempt_id}",
    )
    canonical = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id=attempt.unit_id,
        verification_events=[verification.event],
        attempts_by_id={verification.attempt.attempt_id: verification.attempt},
        policy="first_verified_bundle",
        now=NOW,
        correlation_id=f"corr_canonical_{attempt_id}",
    )
    return _CanonicalResult(
        event=canonical.event,
        attempt=canonical.attempt,
        canonical_selection=canonical.canonical_selection,
    )


def _split_invocation(
    *,
    canonical_selection: CanonicalSelection,
    plugin_descriptor_digest: str,
) -> SplitStrategyInvocation:
    return SplitStrategyInvocation(
        invocation_id=f"split_invocation:{SCOPE_HASH}:attempt:1",
        invocation_attempt_no=1,
        expansion_scope_hash=SCOPE_HASH,
        task_id="task_demo",
        unit_id="unit_parent",
        canonical_selection_id=canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=plugin_descriptor_digest,
        split_strategy_id=STRATEGY_ID,
        split_strategy_params_digest=PARAMS_DIGEST,
        status="succeeded",
        result_action="expand",
        result_digest="sha256:split_result",
        started_at=NOW,
        completed_at=NOW,
    )


def _proposal(
    *,
    canonical_selection: CanonicalSelection,
    child_specs: list[dict],
    merge_slots: list[dict],
    expected_outputs: list[dict],
) -> DecompositionProposal:
    return DecompositionProposal(
        proposal_header={
            "proposal_id": "decomposition_proposal_pending",
            "proposal_schema_version": "phase4.decomposition_proposal.v1",
            "task_id": "task_demo",
            "parent_unit_id": "unit_parent",
            "canonical_selection_id": canonical_selection.canonical_selection_id,
            "canonical_output_bundle_digest": canonical_selection.canonical_output_bundle_digest,
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_digest": make_plugin_descriptor().descriptor_digest,
            "split_strategy_id": STRATEGY_ID,
            "split_strategy_params_digest": PARAMS_DIGEST,
            "expansion_scope_hash": SCOPE_HASH,
            "proposal_digest": "sha256:pending",
            "created_at": NOW,
        },
        child_specs=child_specs,
        dependency_edges=[],
        expected_outputs=expected_outputs,
        merge_slots=merge_slots,
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
    canonical_selection: CanonicalSelection,
    proposal_id: str,
    required_slot_count: int,
    intro_child_unit_id: str,
    summary_child_unit_id: str,
) -> MergePlan:
    required_slots = [
        {
            "slot_key": "slot_intro",
            "source_child_logical_key": "intro",
            "source_child_unit_id": intro_child_unit_id,
            "source_output_name": "answer",
            "output_schema_ref": {"schema": "text"},
            "output_schema_digest": "sha256:schema_intro",
            "required": True,
            "missing_policy": "block_merge",
        }
    ]
    if required_slot_count > 1:
        required_slots.append(
            {
                "slot_key": "slot_summary",
                "source_child_logical_key": "summary",
                "source_child_unit_id": summary_child_unit_id,
                "source_output_name": "answer",
                "output_schema_ref": {"schema": "text"},
                "output_schema_digest": "sha256:schema_summary",
                "required": True,
                "missing_policy": "block_merge",
            }
        )
    return MergePlan(
        merge_plan_header={
            "merge_plan_id": "merge_plan_pending",
            "merge_plan_schema_version": "phase4.merge_plan.v1",
            "task_id": "task_demo",
            "parent_unit_id": "unit_parent",
            "canonical_selection_id": canonical_selection.canonical_selection_id,
            "decomposition_proposal_id": proposal_id,
            "expansion_decision_id": f"expansion_decision:{SCOPE_HASH}",
            "created_by_plugin_id": "structured_report_stub",
            "created_by_plugin_version": "0.1.0",
            "merge_plan_digest": "sha256:pending",
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
        required_slots=required_slots,
        parent_output_mapping=[
            {
                "parent_output_name": "answer",
                "resolution_kind": "merge_plan_output",
                "merge_slot_keys": ["slot_intro"],
                "result_schema_ref": {"schema": "text"},
                "result_schema_digest": "sha256:schema_answer",
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
            "plugin_defined_body": {"notes": "phase5 test"},
        },
    )


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


def _child_spec(child_logical_key: str, *, parent_output_name: str | None) -> dict:
    input_bindings = {}
    if parent_output_name is not None:
        input_bindings = {
            "parent_answer": {"kind": "parent_output", "output_name": parent_output_name}
        }
    return {
        "child_logical_key": child_logical_key,
        "unit_type": "section",
        "input_bindings": input_bindings,
        "required_outputs": ["answer"],
        "output_contract_refs": {"answer": {"schema": "text"}},
        "validator_policy_id": "structured_report_stub_validator_v1",
        "budget_limit": None,
        "deadline": None,
        "weight": 1.0,
        "required_capabilities": {"executor": "mock_ai"},
        "plugin_payload": {},
        "promotion_guard_ref": None,
    }


def _merge_slot(slot_key: str, *, child_key: str, child_output_name: str) -> dict:
    return {
        "slot_id": slot_key,
        "child_key": child_key,
        "child_output_name": child_output_name,
        "schema_ref": {"schema": "text"},
        "required": True,
        "missing_policy": "block_merge",
    }


def _expand_decision(
    *,
    canonical_selection: CanonicalSelection,
    source_invocation_id: str,
    proposal_id: str,
    proposal_digest: str,
    merge_plan_id: str,
    merge_plan_digest: str,
    required_merge_slot_count: int,
) -> ExpansionDecision:
    return ExpansionDecision(
        expansion_decision_id=f"expansion_decision:{SCOPE_HASH}",
        task_id="task_demo",
        unit_id="unit_parent",
        canonical_selection_id=canonical_selection.canonical_selection_id,
        canonical_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        expansion_scope_hash=SCOPE_HASH,
        action="expand",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest=make_plugin_descriptor().descriptor_digest,
        split_strategy_id=STRATEGY_ID,
        split_strategy_params_digest=PARAMS_DIGEST,
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
                "relation_count": 0,
                "expected_output_count": 1,
                "required_merge_slot_count": required_merge_slot_count,
            }
        },
        decided_at=NOW,
    )


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
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"


def _derive_child_unit_id(*, parent_unit_id: str, proposal_digest: str, child_logical_key: str) -> str:
    stable_suffix = proposal_digest.removeprefix("sha256:")
    stable_suffix = "".join(character if character.isalnum() or character == "_" else "_" for character in stable_suffix)
    child_component = "".join(
        character if character.isalnum() or character == "_" else "_" for character in child_logical_key
    )
    parent_component = "".join(
        character if character.isalnum() or character == "_" else "_" for character in parent_unit_id
    )
    return f"unit_{parent_component}_{stable_suffix}_{child_component}"


def _artifact_ref_with_updates(artifact_ref, **updates):
    artifact_ref_data = artifact_ref.to_dict()
    for key, value in updates.items():
        if value is not None:
            artifact_ref_data[key] = value
    return artifact_ref.__class__(**artifact_ref_data)
