import pytest

from tests.phase2_fixtures import make_client, make_config, make_unit
from tokenshare.core.models import AttemptState, LeaseState, TaskState
from tokenshare.core.recovery import evaluate_retry
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
    assert [event.batch_id for event in events[-4:]] == [
        "recovery_batch:recovery_1",
        "recovery_batch:recovery_1",
        "recovery_batch:recovery_1",
        "recovery_batch:recovery_1",
    ]
    assert [event.batch_index for event in events[-4:]] == [1, 2, 3, 4]
    assert {event.batch_size for event in events[-4:]} == {4}
    assert all(event.correlation_id for event in events)


def test_protocol_engine_prevents_duplicate_active_lease_from_ledger(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    unit = make_unit()
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config)

    engine.schedule_ready_unit(
        graph=graph,
        clients=[make_client()],
        now="2026-06-08T00:00:00Z",
        correlation_id="corr_schedule_1",
        decision_id="decision_1",
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
    )

    with pytest.raises(ValueError, match="no schedulable ready unit"):
        engine.schedule_ready_unit(
            graph=graph,
            clients=[make_client()],
            now="2026-06-08T00:00:01Z",
            correlation_id="corr_schedule_2",
            decision_id="decision_2",
            lease_id="lease_2",
            attempt_id="attempt_2",
            fencing_token="token_2",
        )

    assert len(ledger.read_all()) == 4


def test_phase2_stale_heartbeat_cannot_reactivate_released_lease(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-23T00:00:00Z",
        correlation_id="corr_schedule_stale_heartbeat",
        decision_id="decision_stale_heartbeat",
        lease_id="lease_stale_heartbeat",
        attempt_id="attempt_stale_heartbeat",
        fencing_token="fence_stale_heartbeat",
    )
    engine.record_recovery_decision(
        decision=evaluate_retry(
            trigger="executor_error",
            retry_count=1,
            max_retries=make_config().max_retries,
        ),
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        task_unit=scheduled.task_unit,
        recovery_action_id="recovery_before_stale_heartbeat",
        now="2026-07-23T00:00:30Z",
        correlation_id="corr_recovery_before_stale_heartbeat",
    )
    before = ledger.read_all()

    with pytest.raises(
        ValueError,
        match="terminal lease cannot heartbeat: Released",
    ):
        engine.record_lease_heartbeat(
            lease=scheduled.lease,
            now="2026-07-23T00:01:00Z",
            correlation_id="corr_stale_heartbeat",
        )

    assert ledger.read_all() == before


def test_phase2_heartbeat_advances_from_latest_active_lease_snapshot(
    tmp_path,
) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-23T00:00:00Z",
        correlation_id="corr_schedule_heartbeat_chain",
        decision_id="decision_heartbeat_chain",
        lease_id="lease_heartbeat_chain",
        attempt_id="attempt_heartbeat_chain",
        fencing_token="fence_heartbeat_chain",
    )
    first = engine.record_lease_heartbeat(
        lease=scheduled.lease,
        now="2026-07-23T00:00:30Z",
        correlation_id="corr_heartbeat_1",
    )
    second = engine.record_lease_heartbeat(
        lease=scheduled.lease,
        now="2026-07-23T00:01:00Z",
        correlation_id="corr_heartbeat_2",
    )

    assert first.lease.heartbeat_count == 1
    assert second.lease.heartbeat_count == 2
    assert second.lease.expires_at > first.lease.expires_at
