from __future__ import annotations

from dataclasses import replace

import pytest

from tests.phase2_fixtures import make_artifact_ref, make_unit
from tests.test_phase5_merge_resolution_flow import (
    _make_parent_completion_context,
)
from tokenshare.core.contribution import ContributionRecord, ContributionState
from tokenshare.core.models import TaskState, TaskUnit
from tokenshare.storage.events import EventDraft, EventType


NOW = "2026-06-26T00:00:00Z"


def test_subtree_pruning_after_parent_completion_cancels_unfinished_descendants(
    tmp_path,
) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    descendants = [
        _descendant("unit_ready_to_prune", TaskState.READY),
        _descendant("unit_processing_to_prune", TaskState.PROCESSING),
        _descendant("unit_blocked_to_prune", TaskState.BLOCKED),
    ]
    before_count = len(context.ledger.read_all())

    result = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=descendants,
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_prune",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert result.subtree_prune_record is not None
    assert set(result.cancelled_units) == {
        transition.unit_id for transition in descendants
    }
    assert [event.event_type for event in new_events] == [
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.SUBTREE_PRUNED,
    ]
    assert [event.batch_index for event in new_events] == [1, 2, 3, 4]
    assert all(event.batch_size == 4 for event in new_events)
    assert {event.batch_id for event in new_events} == {
        f"subtree_pruning_batch:unit_parent:{parent_completed_event_seq}"
    }
    assert all(
        event.payload["new_state"] == TaskState.CANCELLED.value
        for event in new_events[:-1]
    )
    marker_payload = new_events[-1].payload
    assert marker_payload["subtree_prune_record"]["cancelled_unit_count"] == 3
    assert marker_payload["subtree_prune_record"]["pruning_policy_plugin_id"] == (
        "structured_report_stub"
    )
    assert marker_payload["policy_source_event_seq"] == context.merge_plan_event.event_seq


def test_subtree_pruning_preserves_completed_canonical_and_settlement_units(
    tmp_path,
) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    cancellable = _descendant("unit_only_ready_is_cancelled", TaskState.READY)
    completed = _descendant("unit_completed_preserved", TaskState.COMPLETED)
    canonical = _descendant(
        "unit_canonical_preserved",
        TaskState.READY,
        canonical_output_refs={"answer": make_artifact_ref("artifact_canonical")},
    )
    settled = _descendant("unit_settled_preserved", TaskState.PROCESSING)
    _append_settled_contribution(context.ledger, unit_id=settled.unit_id)

    result = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=[cancellable, completed, canonical, settled],
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_prune_preserve",
    )

    assert result.cancelled_units == (cancellable.unit_id,)
    assert result.subtree_prune_record.preserved_completed_unit_count == 3
    state_events = [
        event
        for event in result.events
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
    ]
    assert [event.object_id for event in state_events] == [cancellable.unit_id]


def test_subtree_pruning_requires_plugin_declared_policy(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="pruning policy"):
        context.engine.record_subtree_pruning(
            parent_unit_id=context.parent_unit.unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            candidate_descendant_units=[_descendant("unit_ready_to_prune", TaskState.READY)],
            pruning_policy_ref={
                "pruning_policy_id": "freeform_prune",
                "pruning_policy_version": "v1",
            },
            now=NOW,
            correlation_id="corr_subtree_prune_freeform",
        )

    assert len(context.ledger.read_all()) == before_count


def test_subtree_pruning_same_payload_is_idempotent(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    descendants = [
        _descendant("unit_ready_to_prune", TaskState.READY),
        _descendant("unit_blocked_to_prune", TaskState.BLOCKED),
    ]

    first = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=descendants,
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_prune_idempotent",
    )
    count_after_first = len(context.ledger.read_all())
    second = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=list(reversed(descendants)),
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_prune_idempotent",
    )

    assert second.events == first.events
    assert second.subtree_prune_record == first.subtree_prune_record
    assert len(context.ledger.read_all()) == count_after_first


def test_subtree_pruning_different_cancelled_set_conflicts(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=[_descendant("unit_first", TaskState.READY)],
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_prune_conflict",
    )

    with pytest.raises(ValueError, match="subtree pruning conflict"):
        context.engine.record_subtree_pruning(
            parent_unit_id=context.parent_unit.unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            candidate_descendant_units=[
                _descendant("unit_first", TaskState.READY),
                _descendant("unit_second", TaskState.READY),
            ],
            pruning_policy_ref=_pruning_policy_ref(context),
            now=NOW,
            correlation_id="corr_subtree_prune_conflict",
        )


def test_subtree_pruning_batch_without_marker_is_projection_inconsistent(
    tmp_path,
) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    unit = _descendant("unit_half_batch", TaskState.READY)
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id=unit.unit_id,
                task_id=unit.task_id,
                actor={"kind": "test"},
                correlation_id="corr_subtree_half_batch",
                idempotency_key=f"half_batch:{unit.unit_id}",
                payload={
                    "schema_version": "phase5.subtree_pruning_task_unit_state_changed.v1",
                    "task_unit": replace(unit, state=TaskState.CANCELLED).to_dict(),
                    "old_state": TaskState.READY.value,
                    "new_state": TaskState.CANCELLED.value,
                },
                occurred_at=NOW,
            )
        ],
        batch_id=f"subtree_pruning_batch:unit_parent:{parent_completed_event_seq}",
    )

    with pytest.raises(ValueError, match="projection inconsistent"):
        context.engine.record_subtree_pruning(
            parent_unit_id=context.parent_unit.unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            candidate_descendant_units=[unit],
            pruning_policy_ref=_pruning_policy_ref(context),
            now=NOW,
            correlation_id="corr_subtree_after_half_batch",
        )


def test_subtree_pruning_rejects_policy_without_descriptor_provenance(
    tmp_path,
) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    policy_ref = _pruning_policy_ref(context)
    policy_ref.pop("pruning_policy_descriptor_digest")

    with pytest.raises(ValueError, match="descriptor provenance"):
        context.engine.record_subtree_pruning(
            parent_unit_id=context.parent_unit.unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            candidate_descendant_units=[_descendant("unit_ready_to_prune", TaskState.READY)],
            pruning_policy_ref=policy_ref,
            now=NOW,
            correlation_id="corr_subtree_missing_descriptor",
        )


def test_subtree_pruning_rejects_policy_source_event_mismatch(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    policy_ref = {
        **_pruning_policy_ref(context),
        "policy_source_event_seq": context.merge_plan_event.event_seq + 1,
    }

    with pytest.raises(ValueError, match="policy source event"):
        context.engine.record_subtree_pruning(
            parent_unit_id=context.parent_unit.unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            candidate_descendant_units=[_descendant("unit_ready_to_prune", TaskState.READY)],
            pruning_policy_ref=policy_ref,
            now=NOW,
            correlation_id="corr_subtree_policy_source_mismatch",
        )


def test_subtree_pruning_noop_when_no_cancellable_descendants(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    before_count = len(context.ledger.read_all())

    result = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=[
            _descendant("unit_completed_preserved", TaskState.COMPLETED),
            _descendant(
                "unit_canonical_preserved",
                TaskState.READY,
                canonical_output_refs={"answer": make_artifact_ref("artifact_canonical")},
            ),
        ],
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_subtree_noop",
    )

    assert result.subtree_prune_record is None
    assert result.cancelled_units == ()
    assert result.events == ()
    assert len(context.ledger.read_all()) == before_count


def _make_completed_parent_context(tmp_path):
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
    )
    parent_completion = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=NOW,
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


def _append_settled_contribution(ledger, *, unit_id: str) -> None:
    contribution = ContributionRecord(
        contribution_id=f"contribution:settled:{unit_id}",
        task_id="task_demo",
        unit_id=unit_id,
        kind="merge_canonical",
        state=ContributionState.SETTLED,
        source_attempt_id=f"attempt_{unit_id}",
        source_client_id="client_settled",
        canonical_selection_id=f"canonical_{unit_id}",
        canonical_event_seq=41,
        verification_report_id=f"verification_{unit_id}",
        verification_event_seq=40,
        source_decision_id=None,
        merge_record_id=f"merge_record_{unit_id}",
        source_batch_id=f"merge_resolution_batch:{unit_id}",
        source_terminal_event_seq=42,
        reward_weight=1,
        created_at=NOW,
        updated_at=NOW,
    )
    ledger.append(
        event_type=EventType.CONTRIBUTION_STATE_CHANGED,
        object_type="ContributionRecord",
        object_id=contribution.contribution_id,
        task_id=contribution.task_id,
        actor={"kind": "test"},
        correlation_id=f"corr_settled_{unit_id}",
        idempotency_key=f"test:settled:{unit_id}",
        payload={
            "schema_version": "phase5.contribution_state_changed.v1",
            "contribution": contribution.to_dict(),
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
            "changed_at": contribution.updated_at,
        },
        occurred_at=NOW,
    )
