from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.phase2_fixtures import make_artifact_ref
from tests.phase5_fixtures import make_merge_creation_context
from tests.test_phase4_complete_flow import (
    _make_complete_context,
    _verification_report,
)
from tests.test_phase5_merge_resolution_flow import (
    _expected_output_resolution,
    _make_parent_completion_context,
    _make_resolution_context,
    _merge_record,
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
from tokenshare.core.merge_coordinator import BatchView
from tokenshare.core.models import ArtifactRef, Attempt, AttemptState
from tokenshare.storage.events import EventDraft, EventType


NOW = "2026-06-26T00:00:00Z"


def test_complete_batch_creates_complete_canonical_eligible_contribution(tmp_path) -> None:
    context = _make_complete_context(tmp_path)
    complete = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_for_contribution",
    )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)

    results = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[_batch(complete.events)],
        expansion_batches=[],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_complete",
    )

    assert len(results) == 1
    contribution = results[0].contribution
    assert contribution.contribution_id == (
        "contribution:complete_canonical:task_demo:"
        f"unit_ready:{context.canonical_selection.canonical_selection_id}"
    )
    assert contribution.kind == "complete_canonical"
    assert contribution.state == ContributionState.ELIGIBLE
    assert contribution.source_attempt_id == context.canonical_selection.selected_attempt_id
    assert contribution.source_client_id == "client_local"
    assert contribution.source_decision_id == context.decision.expansion_decision_id
    assert contribution.merge_record_id is None
    assert contribution.source_batch_id == f"completion_batch:{context.decision.expansion_decision_id}"
    assert contribution.source_terminal_event_seq == complete.events[1].event_seq
    assert results[0].event.event_type == EventType.CONTRIBUTION_STATE_CHANGED
    assert results[0].event.payload["old_state"] is None
    assert results[0].event.payload["new_state"] == "Eligible"


def test_expansion_batch_creates_expand_canonical_pending_contribution(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = ContributionCoordinator(event_ledger=context.ledger)

    results = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[context.expansion_batch],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_expand",
    )

    assert len(results) == 1
    contribution = results[0].contribution
    assert contribution.contribution_id == (
        "contribution:expand_canonical:task_demo:"
        f"unit_parent:{context.canonical_selection.canonical_selection_id}"
    )
    assert contribution.kind == "expand_canonical"
    assert contribution.state == ContributionState.PENDING
    assert contribution.source_attempt_id == context.canonical_selection.selected_attempt_id
    assert contribution.source_decision_id == context.merge_plan.merge_plan_header[
        "expansion_decision_id"
    ]
    assert contribution.merge_record_id is None
    assert contribution.source_batch_id == context.expansion_batch.batch_id
    assert contribution.source_terminal_event_seq == context.expansion_batch.events[-1].event_seq
    assert results[0].event.payload["new_state"] == "Pending"


def test_merge_resolution_batch_creates_merge_canonical_eligible_contribution(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    merge_resolution = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_merge_for_contribution",
    )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)

    results = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[],
        merge_resolution_batches=[_batch(merge_resolution.events)],
        now=NOW,
        correlation_id="corr_contribution_merge",
    )

    assert len(results) == 1
    contribution = results[0].contribution
    assert contribution.contribution_id == (
        "contribution:merge_canonical:task_demo:"
        f"{merge_record.merge_unit_id}:{merge_record.canonical_selection_id}"
    )
    assert contribution.kind == "merge_canonical"
    assert contribution.state == ContributionState.ELIGIBLE
    assert contribution.source_attempt_id == merge_record.selected_attempt_id
    assert contribution.source_decision_id is None
    assert contribution.merge_record_id == merge_record.merge_record_id
    assert contribution.source_batch_id == (
        f"merge_resolution_batch:{merge_record.merge_record_id}"
    )
    assert contribution.source_terminal_event_seq == merge_resolution.events[0].event_seq


def test_losing_attempts_and_canonical_losers_do_not_create_contribution(tmp_path) -> None:
    context = _make_complete_context(tmp_path, duplicate_report_id_decoy=True)
    _record_late_shadow_verification(context)
    complete = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_with_noise",
    )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)

    results = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[_batch(complete.events)],
        expansion_batches=[],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_noise",
    )

    assert len(results) == 1
    assert results[0].contribution.source_attempt_id == "attempt_verified"
    contribution_events = [
        event
        for event in context.ledger.read_all()
        if event.event_type == EventType.CONTRIBUTION_STATE_CHANGED
    ]
    assert len(contribution_events) == 1
    assert "attempt_decoy" not in str(contribution_events[0].payload)
    assert "attempt_late_shadow" not in str(contribution_events[0].payload)


def test_duplicate_contribution_creation_is_idempotent(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = ContributionCoordinator(event_ledger=context.ledger)

    first = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[context.expansion_batch],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_retry",
    )
    count_after_first = len(context.ledger.read_all())
    second = coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[],
        expansion_batches=[context.expansion_batch],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_retry",
    )

    assert first[0].event == second[0].event
    assert first[0].contribution == second[0].contribution
    assert len(context.ledger.read_all()) == count_after_first


def test_contribution_creation_conflicts_on_different_source_fact(tmp_path) -> None:
    context = _make_complete_context(tmp_path)
    complete = context.engine.record_complete_decision(
        decision=context.decision,
        task_unit=context.task_unit,
        correlation_id="corr_complete_conflict",
    )
    coordinator = ContributionCoordinator(event_ledger=context.ledger)
    coordinator.record_canonical_contributions(
        task_id="task_demo",
        completion_batches=[_batch(complete.events)],
        expansion_batches=[],
        merge_resolution_batches=[],
        now=NOW,
        correlation_id="corr_contribution_conflict",
    )
    conflicting_decision = replace(
        complete.events[0],
        object_id="expansion_decision:conflicting_source_fact",
    )

    with pytest.raises(ValueError, match="conflict"):
        coordinator.record_canonical_contributions(
            task_id="task_demo",
            completion_batches=[_batch((conflicting_decision, complete.events[1]))],
            expansion_batches=[],
            merge_resolution_batches=[],
            now=NOW,
            correlation_id="corr_contribution_conflict",
        )


def test_incomplete_expansion_batch_does_not_create_contribution(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, incomplete_expansion_batch=True)
    coordinator = ContributionCoordinator(event_ledger=context.ledger)
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="incomplete expansion_batch"):
        coordinator.record_canonical_contributions(
            task_id="task_demo",
            completion_batches=[],
            expansion_batches=[context.expansion_batch],
            merge_resolution_batches=[],
            now=NOW,
            correlation_id="corr_incomplete_expansion_contribution",
        )

    assert len(context.ledger.read_all()) == before_count


def test_parent_completion_batch_completes_owner_and_promotes_expand_contribution(tmp_path) -> None:
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
    )
    before_count = len(context.ledger.read_all())

    result = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_parent_completion",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert result.task_unit.state.value == "Completed"
    assert [contribution.state for contribution in result.expand_contributions] == [
        ContributionState.ELIGIBLE
    ]
    assert [event.event_type for event in new_events] == [
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.CONTRIBUTION_STATE_CHANGED,
    ]
    assert new_events[0].payload["old_state"] == "Processing"
    assert new_events[0].payload["new_state"] == "Completed"
    assert new_events[1].payload["old_state"] == "Pending"
    assert new_events[1].payload["new_state"] == "Eligible"
    assert new_events[1].payload["contribution"]["contribution_id"] == (
        expand_contribution.contribution_id
    )
    assert {event.batch_id for event in new_events} == {
        f"parent_completion_batch:unit_parent:{result.resolved_output_set_digest}"
    }
    assert [event.batch_index for event in new_events] == [1, 2]
    assert all(event.batch_size == 2 for event in new_events)


def test_root_completion_settles_all_eligible_contributions_in_one_batch(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    before_count = len(context.ledger.read_all())

    result = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=11,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert [event.event_type for event in new_events] == [
        EventType.CONTRIBUTION_STATE_CHANGED,
        EventType.CONTRIBUTION_STATE_CHANGED,
        EventType.SETTLEMENT_RECORDED,
    ]
    assert [event.batch_index for event in new_events] == [1, 2, 3]
    assert all(event.batch_size == 3 for event in new_events)
    assert {event.batch_id for event in new_events} == {
        f"settlement_batch:task_demo:unit_parent:{root_completion_event_seq}"
    }
    assert {
        event.payload["contribution"]["contribution_id"] for event in new_events[:-1]
    } == {contribution.contribution_id for contribution in contributions}
    assert all(
        event.payload["old_state"] == "Eligible"
        and event.payload["new_state"] == "Settled"
        for event in new_events[:-1]
    )
    assert result.settlement_record.entry_count == len(contributions)
    assert result.settlement_record.total_reward == 11
    assert sum(entry.reward_units for entry in result.settlement_entries) == 11
    assert new_events[-1].payload["settlement_entries_ref"] == (
        result.settlement_record.settlement_entries_ref
    )
    assert _artifact_entries(
        context.store,
        result.settlement_record.settlement_entries_ref,
    ) == [entry.to_dict() for entry in result.settlement_entries]


def test_settlement_batch_ends_with_settlement_recorded_marker(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )

    result = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=list(reversed(contributions)),
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement_marker",
    )

    assert result.events[-1].event_type == EventType.SETTLEMENT_RECORDED
    assert result.events[-1].batch_index == len(result.events)
    assert result.events[-1].payload["settlement_record"][
        "settlement_record_id"
    ] == result.settlement_record.settlement_record_id
    assert all(
        event.event_type == EventType.CONTRIBUTION_STATE_CHANGED
        for event in result.events[:-1]
    )


def test_settlement_rejects_partial_settled_contributions(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )

    with pytest.raises(ValueError, match="partial settlement"):
        context.engine.record_root_settlement(
            task_id="task_demo",
            root_unit_id=context.parent_unit.unit_id,
            root_completion_event_seq=root_completion_event_seq,
            eligible_contributions=[contributions[0]],
            root_budget=10,
            settlement_policy_id="sandbox_equal_weight_v1",
            now=NOW,
            correlation_id="corr_root_settlement_partial",
        )


def test_settlement_same_payload_is_idempotent(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )

    first = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement_idempotent",
    )
    count_after_first = len(context.ledger.read_all())
    second = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=list(reversed(contributions)),
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement_idempotent",
    )

    assert first.events == second.events
    assert first.settlement_record == second.settlement_record
    assert len(context.ledger.read_all()) == count_after_first


def test_settlement_different_entries_digest_conflicts(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement_conflict",
    )

    with pytest.raises(ValueError, match="settlement conflict"):
        context.engine.record_root_settlement(
            task_id="task_demo",
            root_unit_id=context.parent_unit.unit_id,
            root_completion_event_seq=root_completion_event_seq,
            eligible_contributions=contributions,
            root_budget=11,
            settlement_policy_id="sandbox_equal_weight_v1",
            now=NOW,
            correlation_id="corr_root_settlement_conflict",
        )


def test_settlement_does_not_include_pending_invalidated_or_late_contributions(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    pending = _manual_contribution(
        "contribution:manual:pending",
        state=ContributionState.PENDING,
        source_terminal_event_seq=root_completion_event_seq,
    )
    invalidated = _manual_contribution(
        "contribution:manual:invalidated",
        state=ContributionState.ELIGIBLE,
        source_terminal_event_seq=root_completion_event_seq,
    )
    late = _manual_contribution(
        "contribution:manual:late",
        state=ContributionState.ELIGIBLE,
        source_terminal_event_seq=root_completion_event_seq + 1,
    )
    _append_contribution_event(
        context.ledger,
        contribution=pending,
        old_state=None,
        new_state=ContributionState.PENDING,
        reason="manual_pending",
    )
    _append_contribution_event(
        context.ledger,
        contribution=invalidated,
        old_state=None,
        new_state=ContributionState.ELIGIBLE,
        reason="manual_invalidated_create",
    )
    _append_contribution_event(
        context.ledger,
        contribution=transition_contribution(
            invalidated,
            new_state=ContributionState.INVALIDATED,
            changed_at=NOW,
            reason="manual_invalidated",
        ),
        old_state=ContributionState.ELIGIBLE,
        new_state=ContributionState.INVALIDATED,
        reason="manual_invalidated",
    )
    _append_contribution_event(
        context.ledger,
        contribution=late,
        old_state=None,
        new_state=ContributionState.ELIGIBLE,
        reason="manual_late",
    )

    result = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=[*contributions, pending, invalidated, late],
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_root_settlement_filters",
    )

    assert {entry.contribution_id for entry in result.settlement_entries} == {
        contribution.contribution_id for contribution in contributions
    }


def test_root_completion_generates_exactly_one_settlement_record(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )

    context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_one_settlement",
    )
    context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_one_settlement",
    )

    settlement_markers = [
        event
        for event in context.ledger.read_all()
        if event.event_type == EventType.SETTLEMENT_RECORDED
    ]
    assert len(settlement_markers) == 1


def test_settlement_requires_entries_artifact_ref(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _append_malformed_settlement_batch(
        context,
        root_completion_event_seq=root_completion_event_seq,
        contributions=contributions,
        mutation="missing_ref",
    )

    with pytest.raises(ValueError, match="settlement_entries_ref"):
        context.engine.record_root_settlement(
            task_id="task_demo",
            root_unit_id=context.parent_unit.unit_id,
            root_completion_event_seq=root_completion_event_seq,
            eligible_contributions=contributions,
            root_budget=10,
            settlement_policy_id="sandbox_equal_weight_v1",
            now=NOW,
            correlation_id="corr_missing_entries_ref",
        )


def test_settlement_rejects_entries_artifact_digest_mismatch(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _append_malformed_settlement_batch(
        context,
        root_completion_event_seq=root_completion_event_seq,
        contributions=contributions,
        mutation="digest_mismatch",
    )

    with pytest.raises(ValueError, match="settlement entries artifact digest"):
        context.engine.record_root_settlement(
            task_id="task_demo",
            root_unit_id=context.parent_unit.unit_id,
            root_completion_event_seq=root_completion_event_seq,
            eligible_contributions=contributions,
            root_budget=10,
            settlement_policy_id="sandbox_equal_weight_v1",
            now=NOW,
            correlation_id="corr_entries_digest_mismatch",
        )


def test_settlement_entries_must_match_settled_contribution_events(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _append_malformed_settlement_batch(
        context,
        root_completion_event_seq=root_completion_event_seq,
        contributions=contributions,
        mutation="event_entry_mismatch",
    )

    with pytest.raises(ValueError, match="settlement entries mismatch"):
        context.engine.record_root_settlement(
            task_id="task_demo",
            root_unit_id=context.parent_unit.unit_id,
            root_completion_event_seq=root_completion_event_seq,
            eligible_contributions=contributions,
            root_budget=10,
            settlement_policy_id="sandbox_equal_weight_v1",
            now=NOW,
            correlation_id="corr_entries_event_mismatch",
        )


def test_root_budget_zero_keeps_entries_with_zero_rewards(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )

    result = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=0,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_zero_budget",
    )

    assert result.settlement_record.entry_count == len(contributions)
    assert result.settlement_record.entry_count > 0
    assert result.settlement_record.total_reward == 0
    assert all(entry.reward_units == 0 for entry in result.settlement_entries)


def _batch(events) -> BatchView:
    event_tuple = tuple(events)
    return BatchView(batch_id=event_tuple[0].batch_id or "", events=event_tuple)


def _record_late_shadow_verification(context) -> None:
    answer_ref = make_artifact_ref("artifact_late_shadow")
    attempt = Attempt(
        attempt_id="attempt_late_shadow",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_attempt_late_shadow",
        client_id="client_shadow",
        state=AttemptState.SUBMITTED,
        attempt_kind="shadow",
        created_at=NOW,
        started_at=NOW,
        submitted_at=NOW,
        candidate_output_refs={"answer": answer_ref},
        metadata={},
    )
    context.engine.record_verification(
        report=_verification_report(
            verification_report_id="verification_report_late_shadow",
            attempt=attempt,
            submission_id="submission_late_shadow",
            submission_event_seq=99,
            answer_ref=answer_ref,
            validator_policy_id="structured_report_stub_validator_v1",
        ),
        attempt=attempt,
        correlation_id="corr_late_shadow_verification",
    )


def _make_root_settlement_context(tmp_path):
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
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
        now=NOW,
        correlation_id="corr_merge_contribution_for_settlement",
    )[0].contribution
    parent_completion = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_parent_completion_for_settlement",
    )
    return (
        context,
        parent_completion.events[0].event_seq,
        [merge_contribution, parent_completion.expand_contributions[0]],
    )


def _artifact_entries(store, ref_data: dict) -> list[dict]:
    artifact_ref = ArtifactRef.from_dict(ref_data)
    return json.loads(store.read_bytes(artifact_ref).decode("utf-8"))


def _manual_contribution(
    contribution_id: str,
    *,
    state: ContributionState,
    source_terminal_event_seq: int,
) -> ContributionRecord:
    return ContributionRecord(
        contribution_id=contribution_id,
        task_id="task_demo",
        unit_id=f"unit_{contribution_id.split(':')[-1]}",
        kind="merge_canonical",
        state=state,
        source_attempt_id=f"attempt_{contribution_id.split(':')[-1]}",
        source_client_id=f"client_{contribution_id.split(':')[-1]}",
        canonical_selection_id=f"canonical_{contribution_id.split(':')[-1]}",
        canonical_event_seq=41,
        verification_report_id=f"verification_{contribution_id.split(':')[-1]}",
        verification_event_seq=40,
        source_decision_id=None,
        merge_record_id=f"merge_record_{contribution_id.split(':')[-1]}",
        source_batch_id=f"merge_resolution_batch:{contribution_id}",
        source_terminal_event_seq=source_terminal_event_seq,
        reward_weight=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _append_contribution_event(
    ledger,
    *,
    contribution: ContributionRecord,
    old_state: ContributionState | None,
    new_state: ContributionState,
    reason: str,
    settlement_entry: dict | None = None,
) -> None:
    payload = {
        "schema_version": "phase5.contribution_state_changed.v1",
        "contribution": contribution.to_dict(),
        "old_state": old_state.value if old_state is not None else None,
        "new_state": new_state.value,
        "reason": reason,
        "task_id": contribution.task_id,
        "unit_id": contribution.unit_id,
        "kind": contribution.kind,
        "canonical_selection_id": contribution.canonical_selection_id,
        "canonical_event_seq": contribution.canonical_event_seq,
        "source_batch_id": contribution.source_batch_id,
        "source_terminal_event_seq": contribution.source_terminal_event_seq,
        "changed_at": contribution.updated_at,
    }
    if settlement_entry is not None:
        payload["settlement_entry"] = settlement_entry
    ledger.append(
        event_type=EventType.CONTRIBUTION_STATE_CHANGED,
        object_type="ContributionRecord",
        object_id=contribution.contribution_id,
        task_id=contribution.task_id,
        actor={"kind": "test"},
        correlation_id=f"corr_{reason}",
        idempotency_key=(
            f"test:{reason}:{contribution.contribution_id}:"
            f"{old_state.value if old_state is not None else 'None'}:{new_state.value}"
        ),
        payload=payload,
        occurred_at=NOW,
    )


def _append_malformed_settlement_batch(
    context,
    *,
    root_completion_event_seq: int,
    contributions: list[ContributionRecord],
    mutation: str,
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
        created_at=NOW,
    )
    entries_ref = context.store.save_json(
        [entry.to_dict() for entry in entries],
        artifact_id=f"settlement_entries_malformed_{mutation}",
        artifact_type="SettlementEntrySet",
        artifact_schema_id="phase5.settlement_entries",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"mutation": mutation},
        created_at=NOW,
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
        "created_at": NOW,
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
            changed_at=NOW,
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
                    "changed_at": NOW,
                },
                occurred_at=NOW,
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
                "settlement_entries_ref": marker_record.get(
                    "settlement_entries_ref"
                ),
                "settlement_summary": marker_record["settlement_summary"],
                "created_at": marker_record["created_at"],
            },
            occurred_at=NOW,
        )
    )
    context.ledger.append_batch(
        drafts,
        batch_id=f"settlement_batch:task_demo:unit_parent:{root_completion_event_seq}",
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
