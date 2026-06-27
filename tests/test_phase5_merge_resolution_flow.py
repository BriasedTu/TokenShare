from __future__ import annotations

import pytest

from tests.phase2_fixtures import make_artifact_ref
from tests.phase5_fixtures import (
    _artifact_ref_with_updates,
    _record_verified_canonical_output,
    make_merge_creation_context,
)
from tokenshare.core.contribution import ContributionCoordinator
from tokenshare.core.expansion import ExpectedOutputRef
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord, digest_json
from tokenshare.core.merge_coordinator import MergeCoordinator
from tokenshare.storage.events import EventType


NOW = "2026-06-26T00:00:00Z"


def test_merge_resolution_records_merge_and_expected_output_resolution_in_one_batch(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    before_count = len(context.ledger.read_all())

    result = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_merge_resolution",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert result.merge_record == merge_record
    assert result.expected_output_resolutions == (resolution,)
    assert [event.event_type for event in new_events] == [
        EventType.MERGE_RECORDED,
        EventType.EXPECTED_OUTPUT_RESOLVED,
    ]
    assert {event.batch_id for event in new_events} == {
        f"merge_resolution_batch:{merge_record.merge_record_id}"
    }
    assert new_events[0].batch_index == 1
    assert new_events[1].batch_index == 2
    assert all(event.batch_size == 2 for event in new_events)
    assert not any(event.event_type == EventType.TASK_UNIT_STATE_CHANGED for event in new_events)
    assert new_events[0].payload["merge_record"]["merge_record_id"] == merge_record.merge_record_id
    assert (
        new_events[1].payload["expected_output_resolution"][
            "expected_output_resolution_id"
        ]
        == resolution.expected_output_resolution_id
    )
    assert _resolved_expected_output_ids(context.ledger.read_all()) == {
        resolution.expected_output_id
    }


def test_merge_resolution_requires_merge_unit_canonical_outputs_bound(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    bad_record = _replace_merge_record(
        merge_record,
        canonical_selection_id="canonical_selection:task_demo:missing_merge_unit",
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="CANONICAL_OUTPUTS_BOUND"):
        context.engine.record_merge_resolution(
            merge_record=bad_record,
            expected_output_resolutions=[resolution],
            correlation_id="corr_merge_without_canonical",
        )

    assert len(context.ledger.read_all()) == before_count
    assert not any(
        event.event_type == EventType.MERGE_RECORDED
        and event.object_id == bad_record.merge_record_id
        for event in context.ledger.read_all()
    )


def test_merge_resolution_rejects_losing_or_late_merge_attempt(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    loser_record = _replace_merge_record(
        merge_record,
        canonical_selection_id=merge_record.canonical_selection_id,
        selected_attempt_id="attempt_losing_or_late",
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="canonical selection"):
        context.engine.record_merge_resolution(
            merge_record=loser_record,
            expected_output_resolutions=[resolution],
            correlation_id="corr_merge_loser",
        )

    assert len(context.ledger.read_all()) == before_count
    assert not any(event.event_type == EventType.MERGE_RECORDED for event in context.ledger.read_all())


def test_merge_resolution_resolves_each_required_parent_output_once(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)

    with pytest.raises(ValueError, match="required parent outputs"):
        context.engine.record_merge_resolution(
            merge_record=merge_record,
            expected_output_resolutions=[],
            correlation_id="corr_merge_missing_resolution",
        )

    with pytest.raises(ValueError, match="duplicate expected output"):
        context.engine.record_merge_resolution(
            merge_record=merge_record,
            expected_output_resolutions=[resolution, resolution],
            correlation_id="corr_merge_duplicate_resolution",
        )


def test_merge_resolution_same_payload_is_idempotent(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)

    first = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_merge_idempotent",
    )
    second = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_merge_idempotent",
    )

    assert first.events == second.events
    assert len(
        [
            event
            for event in context.ledger.read_all()
            if event.batch_id == f"merge_resolution_batch:{merge_record.merge_record_id}"
        ]
    ) == 2


def test_merge_resolution_different_output_digest_conflicts(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_merge_conflict",
    )
    conflicting_ref = _artifact_ref_with_updates(
        make_artifact_ref("artifact_merge_answer_conflict"),
        artifact_type="canonical_output",
    )
    conflicting_record = _replace_merge_record(
        merge_record,
        merge_output_bundle_digest=digest_json({"answer": conflicting_ref.to_dict()}),
        merge_output_refs={"answer": conflicting_ref.to_dict()},
    )
    conflicting_resolution = _expected_output_resolution(context, conflicting_record)

    with pytest.raises(ValueError, match="conflict"):
        context.engine.record_merge_resolution(
            merge_record=conflicting_record,
            expected_output_resolutions=[conflicting_resolution],
            correlation_id="corr_merge_conflict",
        )


def test_incomplete_merge_resolution_batch_is_projection_inconsistent(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    context.ledger.append(
        event_type=EventType.MERGE_RECORDED,
        object_type="MergeRecord",
        object_id=merge_record.merge_record_id,
        task_id=merge_record.task_id,
        actor={"kind": "protocol_engine"},
        correlation_id="corr_merge_half_batch",
        idempotency_key=(
            f"merge_record:{merge_record.merge_plan_id}:"
            f"{merge_record.merge_unit_id}:{merge_record.canonical_selection_id}"
        ),
        payload={
            "schema_version": "phase5.merge_recorded.v1",
            "merge_record": merge_record.to_dict(),
            "task_id": merge_record.task_id,
        },
        occurred_at=NOW,
    )

    assert _resolved_expected_output_ids(context.ledger.read_all()) == set()

    with pytest.raises(ValueError, match="projection inconsistent"):
        context.engine.record_merge_resolution(
            merge_record=merge_record,
            expected_output_resolutions=[_expected_output_resolution(context, merge_record)],
            correlation_id="corr_merge_after_half_batch",
        )


def test_parent_completion_waits_for_all_required_expected_outputs_resolved(tmp_path) -> None:
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=False,
    )
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="required expected outputs"):
        context.engine.record_parent_completion(
            owner_unit=context.parent_unit,
            expected_output_refs=expected_refs,
            expected_output_resolutions=resolutions,
            expand_contributions=[expand_contribution],
            now=NOW,
            correlation_id="corr_parent_waits",
        )

    assert len(context.ledger.read_all()) == before_count
    assert not any(
        event.batch_id and event.batch_id.startswith("parent_completion_batch:")
        for event in context.ledger.read_all()
    )


def test_parent_completion_rejects_partial_expected_output_resolution(tmp_path) -> None:
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
    )
    missing_ref = _extra_required_expected_output_ref(context)
    before_count = len(context.ledger.read_all())

    with pytest.raises(ValueError, match="required expected outputs"):
        context.engine.record_parent_completion(
            owner_unit=context.parent_unit,
            expected_output_refs=[*expected_refs, missing_ref],
            expected_output_resolutions=resolutions,
            expand_contributions=[expand_contribution],
            now=NOW,
            correlation_id="corr_parent_partial",
        )

    assert len(context.ledger.read_all()) == before_count
    assert not any(
        event.batch_id and event.batch_id.startswith("parent_completion_batch:")
        for event in context.ledger.read_all()
    )


def test_parent_completion_same_resolved_set_is_idempotent(tmp_path) -> None:
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
    )

    first = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=list(reversed(expected_refs)),
        expected_output_resolutions=list(reversed(resolutions)),
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_parent_idempotent",
    )
    count_after_first = len(context.ledger.read_all())
    second = context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_parent_idempotent",
    )

    expected_digest = digest_json(
        [
            {
                "expected_output_id": resolutions[0].expected_output_id,
                "output_name": resolutions[0].expected_output_name,
                "resolved_output_digest": resolutions[0].resolved_output_digest,
            }
        ]
    )
    assert first.events == second.events
    assert first.resolved_output_set_digest == expected_digest
    assert first.resolved_output_set_digest == second.resolved_output_set_digest
    assert len(context.ledger.read_all()) == count_after_first


def test_parent_completion_different_resolved_set_conflicts(tmp_path) -> None:
    context, expected_refs, resolutions, expand_contribution = _make_parent_completion_context(
        tmp_path,
        record_resolution=True,
    )
    context.engine.record_parent_completion(
        owner_unit=context.parent_unit,
        expected_output_refs=expected_refs,
        expected_output_resolutions=resolutions,
        expand_contributions=[expand_contribution],
        now=NOW,
        correlation_id="corr_parent_conflict",
    )
    conflicting_ref = _artifact_ref_with_updates(
        make_artifact_ref("artifact_parent_completion_conflict"),
        artifact_type="canonical_output",
    )
    conflicting_resolution = _replace_expected_output_resolution(
        resolutions[0],
        resolved_output_ref=conflicting_ref.to_dict(),
        resolved_output_digest=conflicting_ref.content_hash,
    )

    with pytest.raises(ValueError, match="conflict"):
        context.engine.record_parent_completion(
            owner_unit=context.parent_unit,
            expected_output_refs=expected_refs,
            expected_output_resolutions=[conflicting_resolution],
            expand_contributions=[expand_contribution],
            now=NOW,
            correlation_id="corr_parent_conflict",
        )


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
        now=NOW,
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
        now=NOW,
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
        created_at=NOW,
    )


def _expected_output_resolution(context, merge_record: MergeRecord) -> ExpectedOutputResolution:
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
        resolved_at=NOW,
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


def _extra_required_expected_output_ref(context) -> ExpectedOutputRef:
    base_ref = _expected_output_refs(context)[0]
    return ExpectedOutputRef(
        expected_output_id=f"{base_ref.expected_output_id}:summary",
        task_id=base_ref.task_id,
        owner_unit_id=base_ref.owner_unit_id,
        output_name="summary",
        schema_ref={"schema": "text"},
        resolution_kind="merge_plan_output",
        resolution_status="expected",
        canonical_selection_id=base_ref.canonical_selection_id,
        canonical_output_bundle_digest=base_ref.canonical_output_bundle_digest,
        source_proposal_id=base_ref.source_proposal_id,
        source_expansion_decision_id=base_ref.source_expansion_decision_id,
        created_event_seq=base_ref.created_event_seq,
        merge_plan_id=base_ref.merge_plan_id,
    )


def _replace_merge_record(merge_record: MergeRecord, **updates) -> MergeRecord:
    data = merge_record.to_dict()
    data.pop("schema_version")
    data.update(updates)
    return MergeRecord(**data)


def _replace_expected_output_resolution(
    resolution: ExpectedOutputResolution, **updates
) -> ExpectedOutputResolution:
    data = resolution.to_dict()
    data.pop("schema_version")
    data.update(updates)
    return ExpectedOutputResolution(**data)


def _resolved_expected_output_ids(events) -> set[str]:
    complete_resolution_batch_ids = {
        event.batch_id
        for event in events
        if event.event_type == EventType.MERGE_RECORDED
        and event.batch_id
        and _is_complete_batch(events, event.batch_id)
    }
    return {
        event.object_id
        for event in events
        if event.event_type == EventType.EXPECTED_OUTPUT_RESOLVED
        and event.batch_id in complete_resolution_batch_ids
    }


def _is_complete_batch(events, batch_id: str) -> bool:
    batch = [event for event in events if event.batch_id == batch_id]
    if not batch:
        return False
    batch_sizes = {event.batch_size for event in batch}
    return len(batch_sizes) == 1 and next(iter(batch_sizes)) == len(batch)
