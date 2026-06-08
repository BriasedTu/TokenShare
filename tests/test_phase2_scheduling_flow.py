from tests.phase2_fixtures import make_client, make_config, make_unit
from tokenshare.core.models import AttemptState, LeaseState, TaskState
from tokenshare.core.task_graph import TaskGraph
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.events import EventLedger, EventType


def test_phase2_schedule_and_lease_expiry_flow_writes_ordered_events(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    unit = make_unit()
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config)

    scheduled = engine.schedule_ready_unit(
        graph=graph,
        clients=[make_client()],
        now="2026-06-08T00:00:00Z",
        correlation_id="corr_schedule_1",
        decision_id="decision_1",
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
    )
    heartbeat = engine.record_lease_heartbeat(
        lease=scheduled.lease,
        now="2026-06-08T00:01:00Z",
        correlation_id="corr_heartbeat_1",
    )
    expired = engine.record_lease_expiry(
        lease=heartbeat.lease,
        attempt=scheduled.attempt,
        task_unit=scheduled.task_unit,
        now="2026-06-08T00:06:01Z",
        correlation_id="corr_expire_1",
        recovery_action_id="recovery_1",
        retry_count=1,
    )

    events = ledger.read_all()

    assert scheduled.lease.state == LeaseState.ACTIVE
    assert scheduled.attempt.state == AttemptState.RUNNING
    assert scheduled.task_unit.state == TaskState.PROCESSING
    assert heartbeat.lease.heartbeat_count == 1
    assert heartbeat.lease.expires_at == "2026-06-08T00:06:00Z"
    assert expired.lease.state == LeaseState.EXPIRED
    assert expired.attempt.state == AttemptState.SUPERSEDED
    assert expired.task_unit.state == TaskState.READY
    assert [event.event_type for event in events] == [
        EventType.LEASE_STATE_CHANGED,
        EventType.ATTEMPT_STATE_CHANGED,
        EventType.ATTEMPT_STATE_CHANGED,
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.LEASE_STATE_CHANGED,
        EventType.LEASE_STATE_CHANGED,
        EventType.ATTEMPT_STATE_CHANGED,
        EventType.RECOVERY_ACTION_RECORDED,
        EventType.TASK_UNIT_STATE_CHANGED,
    ]
    assert events[4].payload["old_state"] == "Active"
    assert events[4].payload["new_state"] == "Active"
    assert all(event.correlation_id for event in events)
