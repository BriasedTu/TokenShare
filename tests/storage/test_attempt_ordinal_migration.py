from __future__ import annotations

import sqlite3
from dataclasses import replace

from tests.phase2_fixtures import make_config, make_unit
from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import TaskState
from tokenshare.core.scheduling import SchedulingDecision
from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex


def _decision(unit_id: str, index: int) -> SchedulingDecision:
    return SchedulingDecision(
        decision_id=f"decision_{index}",
        task_id="task_demo",
        unit_id=unit_id,
        client_id="client_local",
        policy_id="fifo_ready_v1",
        matched_capabilities=["executor"],
        lease_kind="primary",
        reason="ready_and_available",
        created_at="2026-08-02T00:00:00Z",
        input_summary={"ready_queue_size": 1},
    )


def _claim(manager: LeaseManager, unit, index: int):
    return manager.claim(
        decision=_decision(unit.unit_id, index),
        task_unit=unit,
        lease_id=f"lease_{index}",
        attempt_id=f"attempt_{index}",
        fencing_token=f"fence_{index}",
        now="2026-08-02T00:00:00Z",
    )


def test_per_unit_attempt_ordinal_is_zero_based_contiguous_and_not_global_schedule_ordinal() -> None:
    manager = LeaseManager(protocol_config=make_config())
    first_a = _claim(manager, make_unit("unit_a"), 8)
    first_b = _claim(manager, make_unit("unit_b"), 99)
    replacement_a = _claim(
        manager,
        replace(first_a.task_unit, state=TaskState.READY),
        1042,
    )

    assert first_a.created_attempt.attempt_ordinal == 0
    assert first_b.created_attempt.attempt_ordinal == 0
    assert replacement_a.created_attempt.attempt_ordinal == 1
    assert replacement_a.task_unit.last_attempt_ordinal == 1


def test_claim_or_requeue_new_attempt_atomically_persists_next_ordinal() -> None:
    manager = LeaseManager(protocol_config=make_config())
    claim = _claim(manager, make_unit(), 1)

    assert claim.task_unit.last_attempt_ordinal == 0
    assert claim.created_attempt.attempt_ordinal == claim.task_unit.last_attempt_ordinal
    assert claim.running_attempt.attempt_ordinal == claim.task_unit.last_attempt_ordinal
    assert claim.task_unit.to_dict()["last_attempt_ordinal"] == 0
    assert claim.running_attempt.to_dict()["attempt_ordinal"] == 0


def test_event_sqlite_migration_replays_legacy_and_v2_rows(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    legacy_unit = make_unit("unit_legacy").to_dict()
    legacy_unit.pop("last_attempt_ordinal", None)
    current_unit = replace(
        make_unit("unit_current"),
        last_attempt_ordinal=3,
        schema_version="TaskUnit.v2",
    ).to_dict()
    for index, unit in enumerate((legacy_unit, current_unit), start=1):
        ledger.append(
            event_type=EventType.TASK_UNIT_CREATED,
            object_type="TaskUnit",
            object_id=unit["unit_id"],
            task_id="task_demo",
            payload={"task_unit": unit},
            idempotency_key=f"unit:{index}",
        )
    manager = LeaseManager(protocol_config=make_config())
    legacy_claim = _claim(manager, make_unit("unit_legacy_attempt"), 11)
    current_claim = _claim(
        manager,
        replace(make_unit("unit_current_attempt"), last_attempt_ordinal=2),
        12,
    )
    legacy_lease = legacy_claim.lease.to_dict()
    legacy_lease.update(schema_version="phase2.lease.v1")
    legacy_lease.pop("attempt_ordinal")
    legacy_lease.pop("binding_digest")
    legacy_attempt = legacy_claim.running_attempt.to_dict()
    legacy_attempt.update(schema_version="phase2.attempt.v1")
    legacy_attempt.pop("attempt_ordinal")
    for index, (event_type, object_type, object_id, payload) in enumerate(
        (
            (
                EventType.LEASE_STATE_CHANGED,
                "Lease",
                legacy_lease["lease_id"],
                {"lease": legacy_lease},
            ),
            (
                EventType.LEASE_STATE_CHANGED,
                "Lease",
                current_claim.lease.lease_id,
                {"lease": current_claim.lease.to_dict()},
            ),
            (
                EventType.ATTEMPT_STATE_CHANGED,
                "Attempt",
                legacy_attempt["attempt_id"],
                {"attempt": legacy_attempt},
            ),
            (
                EventType.ATTEMPT_STATE_CHANGED,
                "Attempt",
                current_claim.running_attempt.attempt_id,
                {"attempt": current_claim.running_attempt.to_dict()},
            ),
        ),
        start=1,
    ):
        ledger.append(
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            task_id="task_demo",
            payload=payload,
            idempotency_key=f"ordinal-migration:{index}",
        )

    index = SQLiteMaterializedIndex(tmp_path / "index.sqlite")
    index.rebuild_from_events(ledger.read_all())
    with sqlite3.connect(index.path) as connection:
        rows = connection.execute(
            "select unit_id, last_attempt_ordinal from task_units order by unit_id"
        ).fetchall()
        lease_rows = connection.execute(
            "select lease_id, attempt_ordinal from leases order by lease_id"
        ).fetchall()
        attempt_rows = connection.execute(
            "select attempt_id, attempt_ordinal from attempts order by attempt_id"
        ).fetchall()

    assert rows == [("unit_current", 3), ("unit_legacy", -1)]
    assert lease_rows == [("lease_11", 0), ("lease_12", 3)]
    assert attempt_rows == [("attempt_11", 0), ("attempt_12", 3)]


def test_replay_restores_same_ordinal_and_replacement_slot(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    unit = replace(
        make_unit("unit_replay"),
        last_attempt_ordinal=2,
        schema_version="TaskUnit.v2",
    )
    ledger.append(
        event_type=EventType.TASK_UNIT_CREATED,
        object_type="TaskUnit",
        object_id=unit.unit_id,
        task_id=unit.task_id,
        payload={"task_unit": unit.to_dict()},
        idempotency_key="unit:replay",
    )
    index = SQLiteMaterializedIndex(tmp_path / "index.sqlite")

    observed = []
    for _ in range(2):
        index.rebuild_from_events(ledger.read_all())
        with sqlite3.connect(index.path) as connection:
            observed.append(
                connection.execute(
                    "select last_attempt_ordinal from task_units where unit_id = ?",
                    (unit.unit_id,),
                ).fetchone()[0]
            )

    assert observed == [2, 2]
