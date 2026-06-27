from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from tests.test_phase5_contribution_settlement_flow import (
    _append_malformed_settlement_batch,
    _make_root_settlement_context,
)
from tests.test_phase5_merge_resolution_flow import (
    _expected_output_resolution,
    _make_resolution_context,
    _merge_record,
)
from tests.test_phase5_subtree_pruning_flow import (
    _descendant,
    _make_completed_parent_context,
    _pruning_policy_ref,
)
from tokenshare.core.contribution import ContributionState
from tokenshare.core.merge import digest_json
from tokenshare.core.merge_coordinator import MergeCoordinator
from tokenshare.core.models import TaskState
from tokenshare.storage.events import EventDraft, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex


NOW = "2026-06-26T00:00:00Z"


def test_sqlite_rebuilds_merge_task_links_and_slot_bindings_only_after_marker(
    tmp_path,
) -> None:
    context, coordinator = _make_merge_creation(tmp_path)
    _rebuild(context)

    with _connect(context) as connection:
        assert _count(connection, "merge_task_links") == 0
        assert _count(connection, "merge_slot_bindings") == 0

    result = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_sqlite_merge_creation",
    )[0]
    _rebuild(context)

    with _connect(context) as connection:
        link_rows = connection.execute(
            """
            select merge_task_link_id, merge_plan_id, merge_unit_id,
                merge_input_bundle_digest, recorded_event_seq, batch_id
            from merge_task_links
            """
        ).fetchall()
        binding_rows = connection.execute(
            """
            select merge_task_link_id, slot_key, source_child_unit_id,
                canonical_event_seq, canonical_output_digest
            from merge_slot_bindings
            """
        ).fetchall()

    assert link_rows == [
        (
            result.merge_task_link.merge_task_link_id,
            result.merge_task_link.merge_plan_id,
            result.merge_task_link.merge_unit_id,
            result.merge_task_link.merge_input_bundle_digest,
            result.events[-1].event_seq,
            result.events[-1].batch_id,
        )
    ]
    assert binding_rows == [
        (
            result.merge_task_link.merge_task_link_id,
            "slot_intro",
            result.merge_task_link.required_slot_bindings[0].source_child_unit_id,
            result.merge_task_link.required_slot_bindings[0].canonical_event_seq,
            result.merge_task_link.required_slot_bindings[0].canonical_output_digest,
        )
    ]


def test_sqlite_rebuilds_merge_records_and_expected_output_resolutions_only_after_complete_batch(
    tmp_path,
) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    _rebuild(context)

    with _connect(context) as connection:
        assert _count(connection, "merge_records") == 0
        assert _count(connection, "expected_output_resolutions") == 0

    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    result = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_sqlite_merge_resolution",
    )
    _rebuild(context)

    with _connect(context) as connection:
        merge_rows = connection.execute(
            """
            select merge_record_id, merge_plan_id, merge_unit_id,
                canonical_selection_id, recorded_event_seq, batch_id
            from merge_records
            """
        ).fetchall()
        resolution_rows = connection.execute(
            """
            select expected_output_resolution_id, expected_output_id,
                merge_record_id, resolved_output_digest, resolved_event_seq
            from expected_output_resolutions
            """
        ).fetchall()

    assert merge_rows == [
        (
            merge_record.merge_record_id,
            merge_record.merge_plan_id,
            merge_record.merge_unit_id,
            merge_record.canonical_selection_id,
            result.events[0].event_seq,
            result.events[0].batch_id,
        )
    ]
    assert resolution_rows == [
        (
            resolution.expected_output_resolution_id,
            resolution.expected_output_id,
            resolution.merge_record_id,
            resolution.resolved_output_digest,
            result.events[1].event_seq,
        )
    ]


def test_sqlite_rejects_merge_resolution_batch_id_mismatch(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    _append_merge_resolution_batch_with_wrong_id(context, merge_record, resolution)

    with pytest.raises(ValueError, match="incomplete merge_resolution_batch"):
        _rebuild(context)


def test_sqlite_updates_expected_output_refs_to_resolved(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    result = context.engine.record_merge_resolution(
        merge_record=merge_record,
        expected_output_resolutions=[resolution],
        correlation_id="corr_sqlite_expected_output_resolved",
    )

    _rebuild(context)

    with _connect(context) as connection:
        row = connection.execute(
            """
            select resolution_status, resolved_event_seq, canonical_selection_id,
                canonical_output_bundle_digest
            from expected_output_refs
            where expected_output_id = ?
            """,
            (resolution.expected_output_id,),
        ).fetchone()

    assert row == (
        "resolved",
        result.events[1].event_seq,
        resolution.merge_canonical_selection_id,
        merge_record.merge_output_bundle_digest,
    )


def test_sqlite_rebuilds_contribution_state_machine(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _rebuild(context)

    with _connect(context) as connection:
        before_rows = connection.execute(
            "select contribution_id, kind, state from contributions order by kind"
        ).fetchall()

    assert before_rows == [
        (contributions[1].contribution_id, "expand_canonical", "Eligible"),
        (contributions[0].contribution_id, "merge_canonical", "Eligible"),
    ]

    context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_sqlite_contribution_settled",
    )
    _rebuild(context)

    with _connect(context) as connection:
        after_rows = connection.execute(
            "select contribution_id, state from contributions order by contribution_id"
        ).fetchall()

    assert after_rows == [
        (contribution.contribution_id, ContributionState.SETTLED.value)
        for contribution in sorted(contributions, key=lambda item: item.contribution_id)
    ]


def test_sqlite_rebuilds_settlement_records_and_entries_only_after_marker(
    tmp_path,
) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _rebuild(context)

    with _connect(context) as connection:
        assert _count(connection, "settlement_records") == 0
        assert _count(connection, "settlement_entries") == 0

    result = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=11,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_sqlite_settlement",
    )
    _rebuild(context)

    with _connect(context) as connection:
        settlement_rows = connection.execute(
            """
            select settlement_record_id, root_completion_event_seq,
                total_reward, entry_count, settlement_event_seq
            from settlement_records
            """
        ).fetchall()
        entry_rows = connection.execute(
            """
            select settlement_record_id, contribution_id, reward_units
            from settlement_entries
            order by contribution_id
            """
        ).fetchall()

    assert settlement_rows == [
        (
            result.settlement_record.settlement_record_id,
            root_completion_event_seq,
            11,
            len(contributions),
            result.events[-1].event_seq,
        )
    ]
    assert entry_rows == [
        (
            result.settlement_record.settlement_record_id,
            entry.contribution_id,
            entry.reward_units,
        )
        for entry in sorted(result.settlement_entries, key=lambda item: item.contribution_id)
    ]


def test_sqlite_rebuilds_subtree_prunes_only_after_marker(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    _rebuild(context)

    with _connect(context) as connection:
        assert _count(connection, "subtree_prunes") == 0

    descendants = [
        _descendant("unit_ready_to_prune", TaskState.READY),
        _descendant("unit_processing_to_prune", TaskState.PROCESSING),
    ]
    result = context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=descendants,
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_sqlite_prune",
    )
    _rebuild(context)

    with _connect(context) as connection:
        prune_rows = connection.execute(
            """
            select subtree_prune_id, parent_unit_id, cancelled_unit_count,
                pruning_policy_descriptor_digest, recorded_event_seq
            from subtree_prunes
            """
        ).fetchall()

    assert prune_rows == [
        (
            result.subtree_prune_record.subtree_prune_id,
            context.parent_unit.unit_id,
            2,
            result.subtree_prune_record.pruning_policy_descriptor_digest,
            result.events[-1].event_seq,
        )
    ]


def test_sqlite_rejects_merge_task_creation_batch_id_mismatch(tmp_path) -> None:
    context, coordinator = _make_merge_creation(tmp_path)
    result = coordinator.create_ready_merge_tasks(
        task_id="task_demo",
        graph=context.graph,
        merge_plan_events=context.merge_plan_events,
        expansion_batches=context.expansion_batches,
        canonical_events=context.canonical_events,
        now=NOW,
        coordinator_id="coordinator_local",
        correlation_id="corr_sqlite_duplicate_link_first",
    )[0]
    _append_merge_task_link_batch_with_wrong_id(context, result)

    with pytest.raises(ValueError, match="incomplete merge_task_creation_batch"):
        _rebuild(context)


def test_sqlite_rejects_duplicate_expected_output_resolution(tmp_path) -> None:
    context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
    merge_record = _merge_record(context, merge_task_link, merge_canonical)
    resolution = _expected_output_resolution(context, merge_record)
    _append_duplicate_resolution_batch(context, merge_record, resolution)

    with pytest.raises(ValueError, match="duplicate expected output resolution"):
        _rebuild(context)


def test_sqlite_rejects_settlement_without_entries_artifact(tmp_path) -> None:
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
        _rebuild(context)


@pytest.mark.parametrize("mutation", ["digest_mismatch", "event_entry_mismatch"])
def test_sqlite_rejects_settlement_entries_digest_or_event_mismatch(
    tmp_path,
    mutation: str,
) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _append_malformed_settlement_batch(
        context,
        root_completion_event_seq=root_completion_event_seq,
        contributions=contributions,
        mutation=mutation,
    )

    with pytest.raises(
        ValueError,
        match="settlement entries artifact digest|settlement entries mismatch",
    ):
        _rebuild(context)


def test_sqlite_rejects_settlement_batch_id_mismatch(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    _append_malformed_settlement_batch(
        context,
        root_completion_event_seq=root_completion_event_seq,
        contributions=contributions,
        mutation="wrong_batch_id",
        batch_id="settlement_batch:task_demo:wrong_root:999",
    )

    with pytest.raises(ValueError, match="incomplete settlement_batch"):
        _rebuild(context)


def test_sqlite_rejects_pruning_policy_without_descriptor_provenance(
    tmp_path,
) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    _append_pruning_batch_without_descriptor(context, parent_completed_event_seq)

    with pytest.raises(ValueError, match="descriptor provenance"):
        _rebuild(context)


def test_sqlite_rejects_subtree_pruning_batch_id_mismatch(tmp_path) -> None:
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    _append_pruning_batch_with_wrong_id(context, parent_completed_event_seq)

    with pytest.raises(ValueError, match="incomplete subtree_pruning_batch"):
        _rebuild(context)


@pytest.mark.parametrize(
    "batch_prefix",
    [
        "merge_task_creation_batch:",
        "merge_resolution_batch:",
        "settlement_batch:",
        "subtree_pruning_batch:",
    ],
)
def test_sqlite_rejects_incomplete_phase5_batches(tmp_path, batch_prefix: str) -> None:
    context, target_event_type = _context_with_phase5_batch(tmp_path, batch_prefix)
    events = [
        event
        for event in context.ledger.read_all()
        if not (
            (event.batch_id or "").startswith(batch_prefix)
            and event.event_type == target_event_type
        )
    ]

    with pytest.raises(ValueError, match="incomplete .*batch"):
        _rebuild(context, events=events)


def test_phase5_complete_integration_merge_to_root_settlement(tmp_path) -> None:
    context, root_completion_event_seq, contributions = _make_root_settlement_context(
        tmp_path
    )
    settlement = context.engine.record_root_settlement(
        task_id="task_demo",
        root_unit_id=context.parent_unit.unit_id,
        root_completion_event_seq=root_completion_event_seq,
        eligible_contributions=contributions,
        root_budget=13,
        settlement_policy_id="sandbox_equal_weight_v1",
        now=NOW,
        correlation_id="corr_sqlite_integration",
    )

    _rebuild(context)

    with _connect(context) as connection:
        summary = {
            "merge_task_links": _count(connection, "merge_task_links"),
            "merge_records": _count(connection, "merge_records"),
            "expected_output_resolutions": _count(
                connection,
                "expected_output_resolutions",
            ),
            "settlement_records": _count(connection, "settlement_records"),
            "settlement_entries": _count(connection, "settlement_entries"),
        }
        parent_state = connection.execute(
            "select state from task_units where unit_id = ?",
            (context.parent_unit.unit_id,),
        ).fetchone()[0]
        contribution_states = connection.execute(
            "select state from contributions order by contribution_id"
        ).fetchall()
        settled_total = connection.execute(
            "select sum(reward_units) from settlement_entries"
        ).fetchone()[0]

    assert summary == {
        "merge_task_links": 1,
        "merge_records": 1,
        "expected_output_resolutions": 1,
        "settlement_records": 1,
        "settlement_entries": len(settlement.settlement_entries),
    }
    assert parent_state == "Completed"
    assert contribution_states == [("Settled",), ("Settled",)]
    assert settled_total == 13


def _make_merge_creation(tmp_path):
    from tests.phase5_fixtures import make_merge_creation_context

    context = make_merge_creation_context(tmp_path)
    coordinator = MergeCoordinator(
        event_ledger=context.ledger,
        artifact_store=context.store,
        protocol_config=context.engine._protocol_config,
    )
    return context, coordinator


def _rebuild(context, *, events=None) -> None:
    SQLiteMaterializedIndex(
        context.store.root_path / "phase5.sqlite",
        artifact_store=context.store,
    ).rebuild_from_events(list(events) if events is not None else context.ledger.read_all())


def _connect(context):
    return sqlite3.connect(context.store.root_path / "phase5.sqlite")


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"select count(*) from {table}").fetchone()[0]


def _append_merge_task_link_batch_with_wrong_id(context, result) -> None:
    original_link = result.merge_task_link.to_dict()
    duplicate_unit_id = f"{original_link['merge_unit_id']}:duplicate"
    duplicate_link = {
        **original_link,
        "merge_task_link_id": f"{original_link['merge_task_link_id']}:duplicate",
        "merge_unit_id": duplicate_unit_id,
    }
    duplicate_unit = {
        **result.merge_task_unit.to_dict(),
        "unit_id": duplicate_unit_id,
    }
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.TASK_UNIT_CREATED,
                object_type="TaskUnit",
                object_id=duplicate_unit_id,
                task_id="task_demo",
                idempotency_key=f"duplicate:task_unit:{duplicate_unit_id}",
                payload={
                    **result.events[0].payload,
                    "task_unit": duplicate_unit,
                    "merge_task_link_id": duplicate_link["merge_task_link_id"],
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.MERGE_TASK_LINK_RECORDED,
                object_type="MergeTaskLink",
                object_id=duplicate_link["merge_task_link_id"],
                task_id="task_demo",
                idempotency_key=f"duplicate:merge_task_link:{duplicate_link['merge_task_link_id']}",
                payload={
                    **result.events[1].payload,
                    "merge_task_link": duplicate_link,
                    "merge_unit_id": duplicate_unit_id,
                },
                occurred_at=NOW,
            ),
        ],
        batch_id=f"merge_task_creation_batch:wrong:{original_link['merge_plan_id']}",
    )


def _append_duplicate_resolution_batch(context, merge_record, resolution) -> None:
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.MERGE_RECORDED,
                object_type="MergeRecord",
                object_id=merge_record.merge_record_id,
                task_id=merge_record.task_id,
                idempotency_key=f"duplicate_resolution:merge_record:{merge_record.merge_record_id}",
                payload={
                    "schema_version": "phase5.merge_recorded.v1",
                    "merge_record": merge_record.to_dict(),
                    "task_id": merge_record.task_id,
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.EXPECTED_OUTPUT_RESOLVED,
                object_type="ExpectedOutputResolution",
                object_id=resolution.expected_output_id,
                task_id=resolution.task_id,
                idempotency_key=(
                    "duplicate_resolution:first:"
                    f"{resolution.expected_output_resolution_id}"
                ),
                payload={
                    "schema_version": "phase5.expected_output_resolved.v1",
                    "expected_output_resolution": resolution.to_dict(),
                    "task_id": resolution.task_id,
                    "expected_output_id": resolution.expected_output_id,
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.EXPECTED_OUTPUT_RESOLVED,
                object_type="ExpectedOutputResolution",
                object_id=resolution.expected_output_id,
                task_id=resolution.task_id,
                idempotency_key=(
                    "duplicate_resolution:second:"
                    f"{resolution.expected_output_resolution_id}"
                ),
                payload={
                    "schema_version": "phase5.expected_output_resolved.v1",
                    "expected_output_resolution": resolution.to_dict(),
                    "task_id": resolution.task_id,
                    "expected_output_id": resolution.expected_output_id,
                },
                occurred_at=NOW,
            ),
        ],
        batch_id=f"merge_resolution_batch:{merge_record.merge_record_id}",
    )


def _append_merge_resolution_batch_with_wrong_id(context, merge_record, resolution) -> None:
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.MERGE_RECORDED,
                object_type="MergeRecord",
                object_id=merge_record.merge_record_id,
                task_id=merge_record.task_id,
                idempotency_key=f"wrong_batch:merge_record:{merge_record.merge_record_id}",
                payload={
                    "schema_version": "phase5.merge_recorded.v1",
                    "merge_record": merge_record.to_dict(),
                    "task_id": merge_record.task_id,
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.EXPECTED_OUTPUT_RESOLVED,
                object_type="ExpectedOutputResolution",
                object_id=resolution.expected_output_id,
                task_id=resolution.task_id,
                idempotency_key=(
                    "wrong_batch:expected_output_resolution:"
                    f"{resolution.expected_output_resolution_id}"
                ),
                payload={
                    "schema_version": "phase5.expected_output_resolved.v1",
                    "expected_output_resolution": resolution.to_dict(),
                    "task_id": resolution.task_id,
                    "expected_output_id": resolution.expected_output_id,
                },
                occurred_at=NOW,
            ),
        ],
        batch_id="merge_resolution_batch:not_the_merge_record_id",
    )


def _append_pruning_batch_without_descriptor(context, parent_completed_event_seq: int) -> None:
    unit = _descendant("unit_prune_missing_descriptor", TaskState.READY)
    cancelled_unit_ids = [unit.unit_id]
    record = {
        "schema_version": "phase5.subtree_prune_record.v1",
        "subtree_prune_id": f"subtree_pruned:{context.parent_unit.unit_id}:{parent_completed_event_seq}",
        "task_id": "task_demo",
        "parent_unit_id": context.parent_unit.unit_id,
        "parent_completed_event_seq": parent_completed_event_seq,
        "pruning_policy_id": "structured_report_stub_merge_v1",
        "pruning_policy_version": "v1",
        "pruning_policy_plugin_id": "structured_report_stub",
        "policy_source_type": "merge_plan",
        "policy_source_id": context.merge_plan.merge_plan_header["merge_plan_id"],
        "policy_source_event_seq": context.merge_plan_event.event_seq,
        "cancelled_unit_count": 1,
        "cancelled_unit_ids_digest": digest_json(cancelled_unit_ids),
        "preserved_completed_unit_count": 0,
        "reason": "parent_completed_post_completion_pruning",
        "created_at": NOW,
    }
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id=unit.unit_id,
                task_id=unit.task_id,
                idempotency_key=f"missing_descriptor:state:{unit.unit_id}",
                payload={
                    "schema_version": "phase5.subtree_pruning_task_unit_state_changed.v1",
                    "old_state": TaskState.READY.value,
                    "new_state": TaskState.CANCELLED.value,
                    "task_unit": replace(unit, state=TaskState.CANCELLED).to_dict(),
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.SUBTREE_PRUNED,
                object_type="SubtreePruneRecord",
                object_id=record["subtree_prune_id"],
                task_id="task_demo",
                idempotency_key=f"missing_descriptor:subtree_pruned:{parent_completed_event_seq}",
                payload={
                    "schema_version": "phase5.subtree_pruned.v1",
                    "subtree_prune_record": record,
                    "task_id": "task_demo",
                    "parent_unit_id": context.parent_unit.unit_id,
                    "parent_completed_event_seq": parent_completed_event_seq,
                    "cancelled_unit_count": 1,
                    "cancelled_unit_ids": cancelled_unit_ids,
                    "cancelled_unit_ids_digest": record["cancelled_unit_ids_digest"],
                },
                occurred_at=NOW,
            ),
        ],
        batch_id=f"subtree_pruning_batch:{context.parent_unit.unit_id}:{parent_completed_event_seq}",
    )


def _append_pruning_batch_with_wrong_id(context, parent_completed_event_seq: int) -> None:
    unit = _descendant("unit_prune_wrong_batch", TaskState.READY)
    cancelled_unit_ids = [unit.unit_id]
    policy_ref = _pruning_policy_ref(context)
    record = {
        "schema_version": "phase5.subtree_prune_record.v1",
        "subtree_prune_id": f"subtree_pruned:{context.parent_unit.unit_id}:{parent_completed_event_seq}",
        "task_id": "task_demo",
        "parent_unit_id": context.parent_unit.unit_id,
        "parent_completed_event_seq": parent_completed_event_seq,
        "pruning_policy_id": policy_ref["pruning_policy_id"],
        "pruning_policy_version": policy_ref["pruning_policy_version"],
        "pruning_policy_plugin_id": policy_ref["pruning_policy_plugin_id"],
        "pruning_policy_descriptor_digest": policy_ref[
            "pruning_policy_descriptor_digest"
        ],
        "policy_source_type": policy_ref["policy_source_type"],
        "policy_source_id": policy_ref["policy_source_id"],
        "policy_source_event_seq": policy_ref["policy_source_event_seq"],
        "cancelled_unit_count": 1,
        "cancelled_unit_ids_digest": digest_json(cancelled_unit_ids),
        "preserved_completed_unit_count": 0,
        "reason": "parent_completed_post_completion_pruning",
        "created_at": NOW,
    }
    context.ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id=unit.unit_id,
                task_id=unit.task_id,
                idempotency_key=f"wrong_pruning_batch:state:{unit.unit_id}",
                payload={
                    "schema_version": "phase5.subtree_pruning_task_unit_state_changed.v1",
                    "old_state": TaskState.READY.value,
                    "new_state": TaskState.CANCELLED.value,
                    "task_unit": replace(unit, state=TaskState.CANCELLED).to_dict(),
                },
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.SUBTREE_PRUNED,
                object_type="SubtreePruneRecord",
                object_id=record["subtree_prune_id"],
                task_id="task_demo",
                idempotency_key=f"wrong_pruning_batch:subtree_pruned:{parent_completed_event_seq}",
                payload={
                    "schema_version": "phase5.subtree_pruned.v1",
                    "subtree_prune_record": record,
                    "task_id": "task_demo",
                    "parent_unit_id": context.parent_unit.unit_id,
                    "parent_completed_event_seq": parent_completed_event_seq,
                    "cancelled_unit_count": 1,
                    "cancelled_unit_ids": cancelled_unit_ids,
                    "cancelled_unit_ids_digest": record["cancelled_unit_ids_digest"],
                },
                occurred_at=NOW,
            ),
        ],
        batch_id="subtree_pruning_batch:wrong_parent:999",
    )


def _context_with_phase5_batch(tmp_path, batch_prefix: str):
    if batch_prefix == "merge_task_creation_batch:":
        context, coordinator = _make_merge_creation(tmp_path)
        coordinator.create_ready_merge_tasks(
            task_id="task_demo",
            graph=context.graph,
            merge_plan_events=context.merge_plan_events,
            expansion_batches=context.expansion_batches,
            canonical_events=context.canonical_events,
            now=NOW,
            coordinator_id="coordinator_local",
            correlation_id="corr_incomplete_merge_task_creation",
        )
        return context, EventType.MERGE_TASK_LINK_RECORDED
    if batch_prefix == "merge_resolution_batch:":
        context, merge_task_link, merge_canonical = _make_resolution_context(tmp_path)
        merge_record = _merge_record(context, merge_task_link, merge_canonical)
        context.engine.record_merge_resolution(
            merge_record=merge_record,
            expected_output_resolutions=[
                _expected_output_resolution(context, merge_record)
            ],
            correlation_id="corr_incomplete_merge_resolution",
        )
        return context, EventType.EXPECTED_OUTPUT_RESOLVED
    if batch_prefix == "settlement_batch:":
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
            correlation_id="corr_incomplete_settlement",
        )
        return context, EventType.SETTLEMENT_RECORDED
    context, parent_completed_event_seq = _make_completed_parent_context(tmp_path)
    context.engine.record_subtree_pruning(
        parent_unit_id=context.parent_unit.unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        candidate_descendant_units=[_descendant("unit_incomplete_prune", TaskState.READY)],
        pruning_policy_ref=_pruning_policy_ref(context),
        now=NOW,
        correlation_id="corr_incomplete_pruning",
    )
    return context, EventType.SUBTREE_PRUNED
