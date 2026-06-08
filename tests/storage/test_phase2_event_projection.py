import sqlite3

from tests.phase2_fixtures import make_unit
from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState
from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex


def test_sqlite_index_rebuilds_phase2_state_from_event_ledger(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    task_unit = make_unit(state=TaskState.READY)
    processing_unit = make_unit(state=TaskState.PROCESSING)
    lease = Lease(
        lease_id="lease_1",
        task_id="task_demo",
        unit_id=task_unit.unit_id,
        attempt_id="attempt_1",
        client_id="client_local",
        state=LeaseState.ACTIVE,
        fencing_token="token_1",
        issued_at="2026-06-08T00:00:00Z",
        expires_at="2026-06-08T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="primary",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )
    attempt = Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id=task_unit.unit_id,
        lease_id=lease.lease_id,
        client_id="client_local",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at="2026-06-08T00:00:00Z",
        started_at="2026-06-08T00:00:01Z",
    )

    ledger.append(
        event_type=EventType.TASK_UNIT_CREATED,
        object_type="TaskUnit",
        object_id=task_unit.unit_id,
        payload={"task_unit": task_unit.to_dict()},
        task_id="task_demo",
        idempotency_key="task_unit_created:unit_ready",
        occurred_at="2026-06-08T00:00:00Z",
    )
    ledger.append(
        event_type=EventType.LEASE_STATE_CHANGED,
        object_type="Lease",
        object_id=lease.lease_id,
        payload={"old_state": None, "new_state": "Active", "lease": lease.to_dict()},
        task_id="task_demo",
        idempotency_key="lease:create:lease_1",
        occurred_at="2026-06-08T00:00:01Z",
    )
    ledger.append(
        event_type=EventType.ATTEMPT_STATE_CHANGED,
        object_type="Attempt",
        object_id=attempt.attempt_id,
        payload={"old_state": "Created", "new_state": "Running", "attempt": attempt.to_dict()},
        task_id="task_demo",
        idempotency_key="attempt:state:attempt_1:Created:Running:corr_1",
        occurred_at="2026-06-08T00:00:02Z",
    )
    ledger.append(
        event_type=EventType.TASK_UNIT_STATE_CHANGED,
        object_type="TaskUnit",
        object_id=task_unit.unit_id,
        payload={
            "task_unit_state_change": {
                "schema_version": "phase2.task_unit_state_change.v1",
                "task_id": "task_demo",
                "unit_id": task_unit.unit_id,
                "old_state": "Ready",
                "new_state": "Processing",
                "reason": "scheduled",
                "trigger": "scheduler",
                "correlation_id": "corr_1",
                "causation_event_id": None,
                "changed_at": "2026-06-08T00:00:03Z",
            },
            "task_unit": processing_unit.to_dict(),
        },
        task_id="task_demo",
        idempotency_key="task_unit:state:unit_ready:Ready:Processing:corr_1",
        occurred_at="2026-06-08T00:00:03Z",
    )
    ledger.append(
        event_type=EventType.RECOVERY_ACTION_RECORDED,
        object_type="RecoveryAction",
        object_id="recovery_1",
        payload={
            "recovery_action": {
                "schema_version": "phase2.recovery_action.v1",
                "recovery_action_id": "recovery_1",
                "task_id": "task_demo",
                "unit_id": task_unit.unit_id,
                "trigger": "lease_expired",
                "lease_id": lease.lease_id,
                "attempt_id": attempt.attempt_id,
                "old_task_state": "Processing",
                "new_task_state": "Ready",
                "retry_count": 1,
                "retry_allowed": True,
                "reason": "lease_expired_retry",
                "created_at": "2026-06-08T00:06:01Z",
                "metadata": {},
            }
        },
        task_id="task_demo",
        idempotency_key="recovery:unit_ready:lease_expired:attempt_1:1",
        occurred_at="2026-06-08T00:06:01Z",
    )

    index = SQLiteMaterializedIndex(tmp_path / "tokenshare.sqlite")
    index.rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "tokenshare.sqlite") as connection:
        task_row = connection.execute(
            "select state, updated_at, last_state_reason from task_units where unit_id = ?",
            (task_unit.unit_id,),
        ).fetchone()
        lease_row = connection.execute(
            "select state, client_id, heartbeat_count from leases where lease_id = ?",
            (lease.lease_id,),
        ).fetchone()
        attempt_row = connection.execute(
            "select state, lease_id, started_at from attempts where attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        recovery_count = connection.execute("select count(*) from recovery_actions").fetchone()[0]

    assert task_row == ("Processing", "2026-06-08T00:00:03Z", "scheduled")
    assert lease_row == ("Active", "client_local", 0)
    assert attempt_row == ("Running", "lease_1", "2026-06-08T00:00:01Z")
    assert recovery_count == 1
