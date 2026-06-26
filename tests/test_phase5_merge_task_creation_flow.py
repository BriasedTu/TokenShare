from __future__ import annotations

import pytest

from tests.phase2_fixtures import make_artifact_ref
from tokenshare.core.merge_coordinator import MergeCoordinator
from tokenshare.storage.events import EventType

from tests.phase5_fixtures import make_merge_creation_context


NOW = "2026-06-25T00:00:00Z"


def test_ready_merge_plan_creates_merge_task_link_and_ready_merge_unit_in_one_batch(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = _coordinator(context)
    before_count = len(context.ledger.read_all())

    result = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_merge_creation",
    )

    new_events = context.ledger.read_all()[before_count:]
    assert len(result) == 1
    assert [event.event_type for event in new_events] == [
        EventType.TASK_UNIT_CREATED,
        EventType.MERGE_TASK_LINK_RECORDED,
    ]
    assert new_events[0].payload["task_unit"]["state"] == "Ready"
    assert new_events[0].payload["task_unit"]["input_refs"]["merge_input_bundle"]["artifact_id"] == (
        "merge_input_bundle:merge_plan_" + context.merge_plan.merge_plan_header["merge_plan_id"].removeprefix("merge_plan_")
    )
    assert new_events[1].payload["merge_task_link"]["merge_plan_id"] == context.merge_plan.merge_plan_header["merge_plan_id"]
    assert new_events[1].payload["merge_task_link"]["readiness_reason"] == "all_required_slots_canonical"


def test_merge_task_creation_requires_all_required_slots_canonical(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, missing_required_slot=True)
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="required slots"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_missing_slot",
        )


def test_merge_task_creation_uses_canonical_child_outputs_not_submissions(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, canonical_output_artifact_type="submission_output")
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="canonical output"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_submission_output",
        )


def test_merge_task_creation_rejects_candidate_outputs(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, canonical_output_artifact_type="candidate_output")
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="canonical output"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_candidate_output",
        )


def test_merge_input_bundle_is_staged_until_link_marker_records_it(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = _coordinator(context)

    bundle_ref = coordinator._stage_merge_input_bundle(
        task_id="task_demo",
        parent_unit_id=context.parent_unit.unit_id,
        merge_plan=context.merge_plan,
        required_slot_bindings=[],
        coordinator_id="coordinator_local",
        now=NOW,
    )

    assert context.store.verify(bundle_ref)
    assert not any(
        event.event_type == EventType.MERGE_TASK_LINK_RECORDED
        and event.object_id == f"merge_task_link:{context.merge_plan.merge_plan_header['merge_plan_id']}"
        for event in context.ledger.read_all()
    )


def test_merge_task_creation_requires_task_expanded_marker_visible(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, drop_task_expanded_marker=True)
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="TASK_EXPANDED"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_no_marker",
        )


def test_merge_task_creation_rejects_merge_plan_from_incomplete_expansion_batch(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, incomplete_expansion_batch=True)
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="incomplete expansion_batch"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_incomplete_batch",
        )


def test_merge_task_creation_same_payload_is_idempotent(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = _coordinator(context)

    first = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_merge_idempotent",
    )
    second = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_merge_idempotent",
    )

    assert first == second


def test_merge_task_creation_different_slot_binding_conflicts_without_new_task(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path)
    coordinator = _coordinator(context)
    coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_merge_conflict",
    )

    context.canonical_events[0].payload["canonical_selection"]["canonical_output_refs"][
        "answer"
    ] = {
        **make_artifact_ref("artifact_child_answer_conflict").to_dict(),
        "artifact_type": "canonical_output",
    }

    with pytest.raises(ValueError, match="conflict"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_conflict",
        )


def test_merge_task_creation_batch_without_marker_is_projection_inconsistent(tmp_path) -> None:
    context = make_merge_creation_context(tmp_path, drop_task_expanded_marker=True)
    coordinator = _coordinator(context)

    with pytest.raises(ValueError, match="projection inconsistent"):
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_merge_projection_inconsistent",
        )


def _coordinator(context):
    return MergeCoordinator(
        event_ledger=context.ledger,
        artifact_store=context.store,
        protocol_config=context.engine._protocol_config,
    )
