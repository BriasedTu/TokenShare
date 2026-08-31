from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin

from tests.phase2_fixtures import make_artifact_ref, make_unit
from tests.phase3_fixtures import make_plugin_descriptor
from tests.phase5_fixtures import (
    _artifact_ref_with_updates,
    _record_verified_canonical_output,
    make_merge_creation_context,
)
from tokenshare.core.contribution import (
    ContributionCoordinator,
    ContributionRecord,
    ContributionState,
    SettlementRecord,
    build_sandbox_equal_weight_settlement_entries,
    digest_settlement_entries,
    transition_contribution,
)
from tokenshare.core.expansion import (
    DecompositionProposal,
    ExpansionDecision,
    ExpectedOutputRef,
    MergePlan,
    SplitStrategyInvocation,
)
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord, digest_json
from tokenshare.core.merge_coordinator import BatchView, MergeCoordinator
from tokenshare.core.models import TaskState, TaskUnit
from tokenshare.storage.events import EventDraft, EventType


_PHASE4_NOW = "2026-06-25T00:00:00Z"
_PHASE5_NOW = "2026-06-26T00:00:00Z"
STRATEGY_ID = "structured_report_sections_v1"
PARAMS_DIGEST = "sha256:params"
SCOPE_HASH = "sha256:scope_expand"


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
        started_at=_PHASE4_NOW,
        completed_at=_PHASE4_NOW,
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
            "created_at": _PHASE4_NOW,
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
            "created_at": _PHASE4_NOW,
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
        decided_at=_PHASE4_NOW,
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


def _make_resolution_context(tmp_path):
    context = make_merge_creation_context(tmp_path)
    coordinator = MergeCoordinator(
        event_ledger=context.ledger,
        artifact_store=context.store,
        protocol_config=context.engine._protocol_config,
    )
    creation = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=_PHASE5_NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_merge_creation_for_resolution",
    )[0]
    merge_output_ref = _artifact_ref_with_updates(
        make_artifact_ref("artifact_merge_answer"),
        artifact_type="canonical_output",
    )
    merge_canonical = _record_verified_canonical_output(
        context.engine,
        unit_id=creation.merge_task_unit.unit_id,
        attempt_id="attempt_merge_winner",
        output_ref=merge_output_ref,
    )
    return context, creation.merge_task_link, merge_canonical


def _make_parent_completion_context(tmp_path, *, record_resolution: bool):
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    if record_resolution:
        context.engine.record_merge_resolution(
            merge_record=merge_record,
            expected_output_resolutions=[resolution],
            correlation_id="corr_merge_for_parent",
        )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)
    expand_contribution = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[context.expansion_batch],
        merge_resolution_batches=[],
        now=_PHASE5_NOW,
        correlation_id="corr_expand_contribution_for_parent",
    )[0].contribution
    return context, _expected_output_refs(context), [resolution], expand_contribution


def _merge_record(context, merge_task_link, merge_canonical) -> MergeRecord:
    canonical_selection = merge_canonical.canonical_selection
    merge_plan = context.merge_plan
    return MergeRecord(
        merge_record_id=(
            f"merge_record:{merge_task_link.merge_plan_id}:"
            f"{merge_task_link.merge_unit_id}:{canonical_selection.canonical_selection_id}"
        ),
        task_id=merge_task_link.task_id,
        parent_unit_id=merge_task_link.parent_unit_id,
        merge_plan_id=merge_task_link.merge_plan_id,
        merge_unit_id=merge_task_link.merge_unit_id,
        merge_task_link_id=merge_task_link.merge_task_link_id,
        merge_input_bundle_ref=merge_task_link.merge_input_bundle_ref,
        merge_input_bundle_digest=merge_task_link.merge_input_bundle_digest,
        required_slot_bindings_digest=merge_task_link.required_slot_bindings_digest,
        merge_policy_id=merge_task_link.merge_policy_id,
        merge_policy_version=merge_task_link.merge_policy_version,
        merge_policy_descriptor_digest=merge_task_link.merge_policy_descriptor_digest,
        merge_policy_params_digest=merge_plan.merge_policy_ref["merge_policy_params_digest"],
        canonical_selection_id=canonical_selection.canonical_selection_id,
        canonical_event_seq=merge_canonical.event.event_seq,
        selected_verification_report_id=canonical_selection.selected_verification_report_id,
        selected_verification_event_seq=canonical_selection.selected_verification_event_seq,
        selected_submission_id=canonical_selection.selected_submission_id,
        selected_submission_event_seq=canonical_selection.selected_submission_event_seq,
        selected_attempt_id=canonical_selection.selected_attempt_id,
        merge_output_bundle_digest=canonical_selection.canonical_output_bundle_digest,
        merge_output_refs={
            name: ref.to_dict()
            for name, ref in canonical_selection.canonical_output_refs.items()
        },
        parent_output_mapping_digest=digest_json(merge_plan.parent_output_mapping),
        created_at=_PHASE5_NOW,
    )


def _expected_output_resolution(
    context,
    merge_record: MergeRecord,
) -> ExpectedOutputResolution:
    expected_output_id = _expected_output_id(context)
    resolved_output_ref = merge_record.merge_output_refs["answer"]
    return ExpectedOutputResolution(
        expected_output_resolution_id=(
            f"expected_output_resolved:{expected_output_id}:{merge_record.merge_record_id}"
        ),
        task_id=merge_record.task_id,
        owner_unit_id=merge_record.parent_unit_id,
        expected_output_id=expected_output_id,
        expected_output_name="answer",
        resolution_source_type="merge_record",
        merge_record_id=merge_record.merge_record_id,
        merge_plan_id=merge_record.merge_plan_id,
        merge_unit_id=merge_record.merge_unit_id,
        merge_canonical_selection_id=merge_record.canonical_selection_id,
        resolved_output_ref=resolved_output_ref,
        resolved_output_digest=resolved_output_ref["content_hash"],
        resolved_at=_PHASE5_NOW,
    )


def _expected_output_id(context) -> str:
    task_expanded = next(
        event
        for event in context.expansion_batch.events
        if event.event_type == EventType.TASK_EXPANDED
    )
    return task_expanded.payload["expected_output_ids"][0]


def _expected_output_refs(context) -> list[ExpectedOutputRef]:
    task_expanded = next(
        event
        for event in context.expansion_batch.events
        if event.event_type == EventType.TASK_EXPANDED
    )
    expected_output = context.merge_plan.parent_output_mapping[0]
    return [
        ExpectedOutputRef(
            expected_output_id=_expected_output_id(context),
            task_id="task_demo",
            owner_unit_id=context.parent_unit.unit_id,
            output_name=expected_output["parent_output_name"],
            schema_ref=expected_output["result_schema_ref"],
            resolution_kind=expected_output["resolution_kind"],
            resolution_status="expected",
            canonical_selection_id=context.canonical_selection.canonical_selection_id,
            canonical_output_bundle_digest=(
                context.canonical_selection.canonical_output_bundle_digest
            ),
            source_proposal_id=context.merge_plan.merge_plan_header[
                "decomposition_proposal_id"
            ],
            source_expansion_decision_id=context.merge_plan.merge_plan_header[
                "expansion_decision_id"
            ],
            created_event_seq=task_expanded.event_seq,
            merge_plan_id=context.merge_plan.merge_plan_header["merge_plan_id"],
        )
    ]


def _batch(events) -> BatchView:
    event_tuple = tuple(events)
    return BatchView(batch_id=event_tuple[0].batch_id or "", events=event_tuple)


def _make_root_settlement_context(tmp_path):
    context, expected_refs, resolutions, expand_contribution = (
        _make_parent_completion_context(
            tmp_path,
            record_resolution=True,
        )
    )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)
    merge_batch = _batch(
        event
        for event in context.ledger.read_all()
        if event.batch_id and event.batch_id.startswith("merge_resolution_batch:")
    )
    merge_contribution = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[],
        merge_resolution_batches=[merge_batch],
        now=_PHASE5_NOW,
        correlation_id="corr_merge_contribution_for_settlement",
    )[0].contribution
    parent_completion = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=_PHASE5_NOW,
        correlation_id="corr_parent_completion_for_settlement",
    )
    return (
        context,
        parent_completion.events[0].event_seq,
        [merge_contribution, parent_completion.expand_contributions[0]],
    )


def _append_malformed_settlement_batch(
    context,
    *,
    root_completion_event_seq: int,
    contributions: list[ContributionRecord],
    mutation: str,
    batch_id: str | None = None,
) -> None:
    entries = build_sandbox_equal_weight_settlement_entries(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        settlement_policy_version="v1",
        scale="1",
        created_at=_PHASE5_NOW,
    )
    entries_ref = context.store.save_json(
        [entry.to_dict() for entry in entries],
        artifact_id=f"settlement_entries_malformed_{mutation}",
        artifact_type="SettlementEntrySet",
        artifact_schema_id="phase5.settlement_entries",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"mutation": mutation},
        created_at=_PHASE5_NOW,
    )
    digest = digest_settlement_entries(entries)
    record_ref = entries_ref.to_dict()
    if mutation == "missing_ref":
        record_ref = None
    if mutation == "digest_mismatch":
        digest = "sha256:not_the_entries_artifact_digest"

    record_data = {
        "settlement_record_id": (
            f"settlement:task_demo:unit_parent:{root_completion_event_seq}"
        ),
        "task_id": "task_demo",
        "root_unit_id": context.parent_unit.unit_id,
        "root_completion_event_seq": root_completion_event_seq,
        "settlement_policy_id": "sandbox_equal_weight_v1",
        "settlement_policy_version": "v1",
        "root_budget": 10,
        "scale": "1",
        "total_reward": 10,
        "entry_count": len(entries),
        "settlement_entries_digest": digest,
        "settlement_entries_ref": record_ref,
        "settlement_summary": _settlement_summary(entries),
        "created_at": _PHASE5_NOW,
        "schema_version": "phase5.settlement_record.v1",
    }
    marker_record = (
        record_data
        if mutation == "missing_ref"
        else SettlementRecord(**record_data).to_dict()
    )
    settled_contributions = [
        transition_contribution(
            contribution,
            new_state=ContributionState.SETTLED,
            changed_at=_PHASE5_NOW,
            reason="settlement_batch",
            source_batch_kind="settlement_batch",
        )
        for contribution in contributions
    ]
    drafts: list[EventDraft] = []
    for contribution, settled, entry in zip(
        contributions,
        settled_contributions,
        entries,
        strict=True,
    ):
        entry_payload = entry.to_dict()
        if mutation == "event_entry_mismatch" and contribution == contributions[0]:
            entry_payload = {**entry_payload, "reward_units": entry.reward_units + 1}
        drafts.append(
            EventDraft(
                event_type=EventType.CONTRIBUTION_STATE_CHANGED,
                object_type="ContributionRecord",
                object_id=contribution.contribution_id,
                task_id=contribution.task_id,
                actor={"kind": "test"},
                correlation_id=f"corr_malformed_{mutation}",
                idempotency_key=(
                    f"malformed:{mutation}:contribution:state:"
                    f"{contribution.contribution_id}:Eligible:Settled"
                ),
                payload={
                    "schema_version": "phase5.contribution_state_changed.v1",
                    "contribution": settled.to_dict(),
                    "old_state": ContributionState.ELIGIBLE.value,
                    "new_state": ContributionState.SETTLED.value,
                    "reason": "settlement_batch",
                    "task_id": contribution.task_id,
                    "unit_id": contribution.unit_id,
                    "kind": contribution.kind,
                    "canonical_selection_id": contribution.canonical_selection_id,
                    "canonical_event_seq": contribution.canonical_event_seq,
                    "source_batch_id": contribution.source_batch_id,
                    "source_terminal_event_seq": contribution.source_terminal_event_seq,
                    "settlement_record_id": marker_record["settlement_record_id"],
                    "settlement_entry": entry_payload,
                    "changed_at": _PHASE5_NOW,
                },
                occurred_at=_PHASE5_NOW,
            )
        )
    drafts.append(
        EventDraft(
            event_type=EventType.SETTLEMENT_RECORDED,
            object_type="SettlementRecord",
            object_id=marker_record["settlement_record_id"],
            task_id="task_demo",
            actor={"kind": "test"},
            correlation_id=f"corr_malformed_{mutation}",
            idempotency_key=f"settlement:task_demo:unit_parent:{root_completion_event_seq}",
            payload={
                "schema_version": "phase5.settlement_recorded.v1",
                "settlement_record": marker_record,
                "task_id": "task_demo",
                "root_unit_id": context.parent_unit.unit_id,
                "root_completion_event_seq": root_completion_event_seq,
                "settlement_policy_id": marker_record["settlement_policy_id"],
                "settlement_policy_version": marker_record[
                    "settlement_policy_version"
                ],
                "root_budget": marker_record["root_budget"],
                "scale": marker_record["scale"],
                "total_reward": marker_record["total_reward"],
                "entry_count": marker_record["entry_count"],
                "settlement_entries_digest": marker_record[
                    "settlement_entries_digest"
                ],
                "settlement_entries_ref": marker_record.get("settlement_entries_ref"),
                "settlement_summary": marker_record["settlement_summary"],
                "created_at": marker_record["created_at"],
            },
            occurred_at=_PHASE5_NOW,
        )
    )
    context.ledger.append_batch(
        drafts,
        batch_id=batch_id
        or f"settlement_batch:task_demo:unit_parent:{root_completion_event_seq}",
    )


def _settlement_summary(entries) -> dict:
    return {
        "entry_count": len(entries),
        "kind_counts": {
            kind: sum(1 for entry in entries if entry.kind == kind)
            for kind in sorted({entry.kind for entry in entries})
        },
        "client_count": len({entry.source_client_id for entry in entries}),
        "total_reward": sum(entry.reward_units for entry in entries),
    }


def _make_completed_parent_context(tmp_path):
    context, expected_refs, resolutions, expand_contribution = (
        _make_parent_completion_context(
            tmp_path,
            record_resolution=True,
        )
    )
    parent_completion = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=_PHASE5_NOW,
        correlation_id="corr_parent_completion_for_pruning",
    )
    return context, parent_completion.events[0].event_seq


def _pruning_policy_ref(context, **updates) -> dict:
    merge_policy = context.merge_plan.merge_policy_ref
    policy_ref = {
        "pruning_policy_id": merge_policy["merge_policy_id"],
        "pruning_policy_version": merge_policy["merge_policy_version"],
        "pruning_policy_plugin_id": merge_policy["plugin_id"],
        "pruning_policy_plugin_version": merge_policy["plugin_version"],
        "pruning_policy_descriptor_digest": merge_policy[
            "merge_policy_descriptor_digest"
        ],
        "policy_source_type": "merge_plan",
        "policy_source_id": context.merge_plan.merge_plan_header["merge_plan_id"],
        "policy_source_event_seq": context.merge_plan_event.event_seq,
    }
    policy_ref.update(updates)
    return policy_ref


def _descendant(
    unit_id: str,
    state: TaskState,
    *,
    parent_unit_id: str = "unit_parent",
    canonical_output_refs: dict | None = None,
) -> TaskUnit:
    return replace(
        make_unit(
            unit_id,
            state=state,
            canonical_output_refs=canonical_output_refs,
            depth=1,
        ),
        parent_unit_id=parent_unit_id,
    )


def make_config_dict():
    return {
        "schema_version": "phase7.ai_api_executor_config.v1",
        "executor_id": "executor_ai_api",
        "provider_family": "siliconflow",
        "selection_policy": {
            "kind": "uniform_random_without_weights",
            "seed_source": "request_or_environment_seed",
        },
        "defaults": {
            "timeout_seconds": 30,
            "max_tokens": 128,
            "temperature": 0.2,
            "top_p": 0.9,
            "stream": False,
            "max_provider_attempts": 3,
        },
        "entries": [
            {
                "entry_id": "sf_qwen",
                "enabled": True,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key_env": "SILICONFLOW_API_KEY_A",
                "model": "Qwen/Qwen2.5-7B-Instruct",
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": {"temperature": 0.1},
                "pricing": {
                    "currency": "CNY",
                    "input_per_million_tokens": 0.0,
                    "output_per_million_tokens": 0.0,
                    "observed_at": "2026-06-28",
                    "source_note": "test fixture price",
                },
                "tags": ["json_mode", "test"],
            },
            {
                "entry_id": "sf_deepseek",
                "enabled": True,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key_env": "SILICONFLOW_API_KEY_B",
                "model": "deepseek-ai/DeepSeek-V3",
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": {},
                "pricing": {
                    "currency": "CNY",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                    "observed_at": "2026-06-28",
                    "source_note": "test fixture price",
                },
                "tags": ["json_mode", "test"],
            },
        ],
        "local_concurrency": {"max_in_flight_global": 4},
        "metadata": {"purpose": "phase7-tests"},
    }


@dataclass
class FakeProviderResponse:
    status_code: int
    body: dict[str, Any] | None = None
    text: str = ""
    error: str | None = None


def prepared_transport_kwargs(
    entry,
    body: dict[str, Any],
    *,
    provider_family: str,
) -> dict[str, Any]:
    if provider_family not in {"siliconflow", "openai", "deepseek"}:
        raise ValueError("unsupported test transport provider family")
    return {
        "body_bytes": json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        "normalized_absolute_endpoint": urljoin(
            entry.base_url.rstrip("/") + "/",
            entry.endpoint.lstrip("/"),
        ),
        "content_type": "application/json",
    }
