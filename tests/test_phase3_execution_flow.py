import sqlite3
from dataclasses import replace

import pytest

from tokenshare.core.models import AttemptState, LeaseState, TaskState
from tokenshare.core.recovery import evaluate_retry
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.mock_ai import MockAIExecutor, MockAIExecutorProfile
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex

from tests.phase2_fixtures import make_client, make_config, make_unit
from tests.phase3_fixtures import (
    make_environment_ref,
    make_executor_descriptor,
    make_output_contract,
    make_plugin_descriptor,
)
from tokenshare.core.task_graph import TaskGraph
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry


def test_phase3_request_and_submission_flow_uses_artifacts_and_advances_attempt_to_submitted(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    unit = make_unit(required_capabilities={"executor": "mock_ai"})
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    plugin_registry = PluginRegistry()
    executor_registry = ExecutorRegistry()
    plugin_registry.register(make_plugin_descriptor())
    executor_registry.register(make_executor_descriptor())

    snapshot_result = engine.record_registry_snapshot(
        task_id="task_demo",
        registry_snapshot_id="registry_snapshot_1",
        plugin_registry=plugin_registry,
        executor_registry=executor_registry,
        now="2026-06-23T00:00:00Z",
        correlation_id="corr_registry_1",
    )
    scheduled = engine.schedule_ready_unit(
        graph=graph,
        clients=[make_client(capabilities={"executor": "mock_ai"}, status="active")],
        now="2026-06-23T00:00:01Z",
        correlation_id="corr_schedule_1",
        decision_id="decision_1",
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
    )
    request = ExecutionRequest(
        request_id="request_1",
        task_id="task_demo",
        unit_id=scheduled.task_unit.unit_id,
        attempt_id=scheduled.attempt.attempt_id,
        lease_id=scheduled.lease.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        plugin=snapshot_result.snapshot.plugin_entries[0],
        executor=snapshot_result.snapshot.executor_entries[0],
        registry_snapshot_id=snapshot_result.snapshot.registry_snapshot_id,
        allocation_decision={
            "decision_id": "allocation_1",
            "selected_executor_id": "executor_mock_ai",
            "eligible_executor_ids": ["executor_mock_ai"],
            "rejected_executor_reasons": {},
            "tie_break": ["executor_id"],
        },
        capability_snapshot={"executor": "mock_ai", "status": "Available"},
        task_unit_snapshot=scheduled.task_unit.to_dict(),
        input_artifact_refs=scheduled.task_unit.input_refs,
        output_contract=make_output_contract(),
        hard_requirements={"executor": "mock_ai"},
        soft_hints={"prefer": "deterministic-fixture"},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={"timeout_seconds": 30},
        created_at="2026-06-23T00:00:02Z",
    )

    request_result = engine.record_execution_request(
        request=request,
        correlation_id="corr_request_1",
        causation_event_id=scheduled.events[-1].event_id,
    )
    submission = MockAIExecutor(
        executor_id="executor_mock_ai",
        executor_version="0.1.0",
        artifact_store=store,
        profile=MockAIExecutorProfile(
            raw_text='{"answer":"forty-two"}',
            parsed_output={"answer": "forty-two"},
        ),
    ).execute(
        request,
        submission_id="submission_1",
        submitted_at="2026-06-23T00:00:03Z",
    )
    submission_result = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id="corr_submission_1",
        causation_event_id=request_result.event.event_id,
    )

    events = ledger.read_all()

    assert request_result.event.event_type == EventType.EXECUTION_REQUEST_RECORDED
    assert request_result.event.payload["request_digest"] == request_result.request_ref.content_hash
    assert "task_unit_snapshot" not in request_result.event.payload
    assert submission_result.event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
    assert submission_result.event.payload["submission_digest"] == (
        submission_result.submission_ref.content_hash
    )
    assert submission_result.event.payload["schema_version"] == (
        "phase3.execution_submission_record.v2"
    )
    assert submission_result.event.payload["acceptance_status"] == "accepted"
    assert submission_result.event.payload["rejection_reason"] is None
    assert submission_result.attempt is not None
    assert submission_result.attempt.state == AttemptState.SUBMITTED
    assert submission_result.attempt.submitted_at == "2026-06-23T00:00:03Z"
    assert submission_result.attempt_event is not None
    assert submission_result.attempt_event.payload["old_state"] == "Running"
    assert submission_result.attempt_event.payload["new_state"] == "Submitted"
    assert submission_result.attempt.finished_at is None
    assert [event.event_type for event in events[-3:]] == [
        EventType.EXECUTION_REQUEST_RECORDED,
        EventType.EXECUTION_SUBMISSION_RECORDED,
        EventType.ATTEMPT_STATE_CHANGED,
    ]


def test_phase3_mismatched_submission_is_audit_only_and_does_not_advance_attempt(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    unit = make_unit(required_capabilities={"executor": "mock_ai"})
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    scheduled = engine.schedule_ready_unit(
        graph=graph,
        clients=[make_client(capabilities={"executor": "mock_ai"}, status="active")],
        now="2026-06-23T00:00:01Z",
        correlation_id="corr_schedule_1",
        decision_id="decision_1",
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
    )
    submission = ExecutionSubmission(
        submission_id="submission_wrong_attempt",
        request_id="request_wrong",
        task_id=scheduled.attempt.task_id,
        unit_id=scheduled.attempt.unit_id,
        attempt_id="attempt_other",
        lease_id=scheduled.attempt.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        executor_id="executor_mock_ai",
        executor_version="0.1.0",
        result_kind="succeeded",
        raw_output_ref=None,
        parsed_output_ref=None,
        candidate_output_refs={},
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=make_environment_ref(),
        environment_summary={"runtime": "python"},
        provenance_ref=None,
        usage_summary={},
        error=None,
        submitted_at="2026-06-23T00:00:03Z",
    )

    submission_result = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id="corr_submission_1",
    )

    assert submission_result.event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
    assert submission_result.attempt is None
    assert submission_result.attempt_event is None
    assert [event.event_type for event in ledger.read_all()][-1] == (
        EventType.EXECUTION_SUBMISSION_RECORDED
    )


def test_phase3_late_submission_is_rejected_audit_only_with_stable_reason(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    unit = make_unit(state=TaskState.PROCESSING)
    attempt = replace(
        _scheduled_attempt(),
        task_id=unit.task_id,
        unit_id=unit.unit_id,
    )
    lease = _active_lease(attempt=attempt)
    submission = _submission_for(
        attempt=attempt,
        lease=lease,
        submission_id="submission_late",
        submitted_at="2026-07-22T00:05:01Z",
    )

    result = engine.record_execution_submission(
        submission=submission,
        attempt=attempt,
        lease=lease,
        correlation_id="corr_submission_late",
    )

    assert result.event.payload["schema_version"] == "phase3.execution_submission_record.v2"
    assert result.event.payload["acceptance_status"] == "rejected"
    assert result.event.payload["rejection_reason"] == "lease_deadline_exceeded"
    assert result.attempt is None
    assert result.attempt_event is None
    assert [event.event_type for event in ledger.read_all()] == [
        EventType.EXECUTION_SUBMISSION_RECORDED
    ]


def test_phase3_submission_on_expired_lease_is_rejected_audit_only(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    attempt = _scheduled_attempt()
    lease = replace(_active_lease(attempt=attempt), state=LeaseState.EXPIRED)
    submission = _submission_for(attempt=attempt, lease=lease)

    result = engine.record_execution_submission(
        submission=submission,
        attempt=attempt,
        lease=lease,
        correlation_id="corr_submission_expired",
    )

    assert result.event.payload["acceptance_status"] == "rejected"
    assert result.event.payload["rejection_reason"] == "lease_not_active"
    assert result.attempt is None
    assert result.attempt_event is None


def test_protocol_engine_records_generic_recovery_as_atomic_batch_and_releases_lease(
    tmp_path,
) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    attempt = _scheduled_attempt()
    task_unit = make_unit(state=TaskState.PROCESSING)
    decision = evaluate_retry(trigger="executor_error", retry_count=1, max_retries=3)

    result = engine.record_recovery_decision(
        decision=decision,
        attempt=attempt,
        lease=_active_lease(attempt=attempt),
        task_unit=task_unit,
        recovery_action_id="recovery_executor_error_1",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_recovery_executor_error_1",
    )

    assert result.lease.state == LeaseState.RELEASED
    assert result.lease.terminated_reason == "executor_error"
    assert result.attempt.state == AttemptState.FAILED
    assert result.task_unit.state == TaskState.READY
    assert result.recovery_action["trigger"] == "executor_error"
    assert result.recovery_action["retry_allowed"] is True
    assert [event.event_type for event in result.events] == [
        EventType.LEASE_STATE_CHANGED,
        EventType.ATTEMPT_STATE_CHANGED,
        EventType.RECOVERY_ACTION_RECORDED,
        EventType.TASK_UNIT_STATE_CHANGED,
    ]
    assert {event.batch_id for event in result.events} == {
        "recovery_batch:recovery_executor_error_1"
    }
    assert [event.batch_index for event in result.events] == [1, 2, 3, 4]
    assert {event.batch_size for event in result.events} == {4}


def test_protocol_engine_rejects_submitted_attempt_on_parser_failure(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    attempt = replace(
        _scheduled_attempt(),
        state=AttemptState.SUBMITTED,
        submitted_at="2026-07-22T00:00:30Z",
    )
    decision = evaluate_retry(trigger="parser_failure", retry_count=1, max_retries=3)

    result = engine.record_recovery_decision(
        decision=decision,
        attempt=attempt,
        lease=_active_lease(attempt=attempt),
        task_unit=make_unit(state=TaskState.PROCESSING),
        recovery_action_id="recovery_parser_failure_1",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_recovery_parser_failure_1",
    )

    assert result.attempt.state == AttemptState.REJECTED
    assert result.attempt.failure_kind == "invalid_output"
    assert result.task_unit.state == TaskState.READY


def test_protocol_engine_rejects_retry_decision_from_different_retry_budget(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config)
    foreign_decision = evaluate_retry(
        trigger="executor_error",
        retry_count=config.max_retries + 1,
        max_retries=config.max_retries + 1,
    )
    attempt = _scheduled_attempt()

    with pytest.raises(ValueError, match="does not match protocol config"):
        engine.record_recovery_decision(
            decision=foreign_decision,
            attempt=attempt,
            lease=_active_lease(attempt=attempt),
            task_unit=make_unit(state=TaskState.PROCESSING),
            recovery_action_id="recovery_wrong_budget",
            now="2026-07-22T00:01:00Z",
            correlation_id="corr_recovery_wrong_budget",
        )

    assert ledger.read_all() == []


def test_protocol_engine_rejects_mismatched_recovery_lease_without_writes(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config)
    attempt = _scheduled_attempt()

    with pytest.raises(ValueError, match="recovery lease does not match attempt"):
        engine.record_recovery_decision(
            decision=evaluate_retry(trigger="no_return", retry_count=1, max_retries=3),
            attempt=attempt,
            lease=replace(_active_lease(attempt=attempt), lease_id="lease_other"),
            task_unit=make_unit(state=TaskState.PROCESSING),
            recovery_action_id="recovery_wrong_lease",
            now="2026-07-22T00:01:00Z",
            correlation_id="corr_recovery_wrong_lease",
        )

    assert ledger.read_all() == []


def test_protocol_engine_records_retry_limit_as_failed_task(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = make_config()
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=config)

    attempt = _scheduled_attempt()
    result = engine.record_recovery_decision(
        decision=evaluate_retry(
            trigger="executor_error",
            retry_count=config.max_retries + 1,
            max_retries=config.max_retries,
        ),
        attempt=attempt,
        lease=_active_lease(attempt=attempt),
        task_unit=make_unit(state=TaskState.PROCESSING),
        recovery_action_id="recovery_retry_limit",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_recovery_retry_limit",
    )

    assert result.recovery_action["retry_allowed"] is False
    assert result.task_unit.state == TaskState.FAILED
    assert result.events[-1].payload["task_unit"]["state"] == "Failed"


def test_protocol_engine_recovery_batch_is_idempotent_and_causally_linked(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    attempt = _scheduled_attempt()
    arguments = {
        "decision": evaluate_retry(trigger="executor_error", retry_count=1, max_retries=3),
        "attempt": attempt,
        "lease": _active_lease(attempt=attempt),
        "task_unit": make_unit(state=TaskState.PROCESSING),
        "recovery_action_id": "recovery_idempotent",
        "now": "2026-07-22T00:01:00Z",
        "correlation_id": "corr_recovery_idempotent",
    }

    first = engine.record_recovery_decision(**arguments)
    second = engine.record_recovery_decision(**arguments)

    assert second.events == first.events
    assert ledger.read_all() == list(first.events)
    lease_event, attempt_event, recovery_event, task_event = first.events
    assert attempt_event.causation_event_id == lease_event.event_id
    assert recovery_event.causation_event_id == attempt_event.event_id
    assert task_event.causation_event_id == recovery_event.event_id
    assert (
        task_event.payload["task_unit_state_change"]["causation_event_id"]
        == recovery_event.event_id
    )


def test_protocol_engine_conflicting_recovery_duplicate_adds_no_partial_events(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    attempt = _scheduled_attempt()
    common = {
        "attempt": attempt,
        "lease": _active_lease(attempt=attempt),
        "task_unit": make_unit(state=TaskState.PROCESSING),
        "recovery_action_id": "recovery_conflict",
        "now": "2026-07-22T00:01:00Z",
        "correlation_id": "corr_recovery_conflict",
    }
    engine.record_recovery_decision(
        decision=evaluate_retry(trigger="executor_error", retry_count=1, max_retries=3),
        **common,
    )
    before = ledger.read_all()

    with pytest.raises(ValueError):
        engine.record_recovery_decision(
            decision=evaluate_retry(trigger="executor_error", retry_count=2, max_retries=3),
            **common,
        )

    assert ledger.read_all() == before


def test_protocol_engine_rejects_cross_trigger_recovery_from_stale_snapshots(
    tmp_path,
) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-22T00:00:00Z",
        correlation_id="corr_schedule_stale_recovery",
        decision_id="decision_stale_recovery",
        lease_id="lease_stale_recovery",
        attempt_id="attempt_stale_recovery",
        fencing_token="fence_stale_recovery",
    )
    engine.record_recovery_decision(
        decision=evaluate_retry(trigger="executor_error", retry_count=1, max_retries=3),
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        task_unit=scheduled.task_unit,
        recovery_action_id="recovery_first_terminal",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_first_terminal",
    )
    before = ledger.read_all()

    with pytest.raises(ValueError, match="recovery lease is not latest"):
        engine.record_lease_expiry(
            lease=scheduled.lease,
            attempt=scheduled.attempt,
            task_unit=scheduled.task_unit,
            now="2026-07-22T00:06:00Z",
            correlation_id="corr_stale_expiry",
            recovery_action_id="recovery_stale_expiry",
            retry_count=2,
        )

    assert ledger.read_all() == before


def test_phase3_submission_uses_latest_ledger_attempt_and_lease_snapshots(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-22T00:00:00Z",
        correlation_id="corr_schedule_stale_submission",
        decision_id="decision_stale_submission",
        lease_id="lease_stale_submission",
        attempt_id="attempt_stale_submission",
        fencing_token="fence_stale_submission",
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
        recovery_action_id="recovery_before_stale_submission",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_recovery_before_stale_submission",
    )
    submission = _submission_for(
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        submission_id="submission_after_recovery",
        submitted_at="2026-07-22T00:02:00Z",
    )

    result = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id="corr_stale_submission_after_recovery",
    )

    assert result.acceptance_decision.acceptance_status == "rejected"
    assert result.acceptance_decision.rejection_reason == "attempt_not_running"
    assert result.attempt is None
    assert result.attempt_event is None
    attempt_edges = [
        (event.payload.get("old_state"), event.payload.get("new_state"))
        for event in ledger.read_all()
        if event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.object_id == scheduled.attempt.attempt_id
    ]
    assert ("Running", "Submitted") not in attempt_edges

    index_path = tmp_path / "index.sqlite"
    SQLiteMaterializedIndex(
        index_path,
        artifact_store=store,
    ).rebuild_from_events(ledger.read_all())
    with sqlite3.connect(index_path) as connection:
        attempt_state = connection.execute(
            "select state from attempts where attempt_id = ?",
            (scheduled.attempt.attempt_id,),
        ).fetchone()
        lease_state = connection.execute(
            "select state from leases where lease_id = ?",
            (scheduled.lease.lease_id,),
        ).fetchone()
        submission_row = connection.execute(
            """
            select acceptance_status, rejection_reason
            from execution_submissions
            where submission_id = ?
            """,
            (submission.submission_id,),
        ).fetchone()

    assert attempt_state == ("Failed",)
    assert lease_state == ("Released",)
    assert submission_row == ("rejected", "attempt_not_running")


def test_protocol_engine_generic_expiry_rejects_lease_before_deadline(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(event_ledger=ledger, protocol_config=make_config())
    attempt = _scheduled_attempt()

    with pytest.raises(ValueError, match="lease has not reached expires_at"):
        engine.record_recovery_decision(
            decision=evaluate_retry(trigger="lease_expired", retry_count=1, max_retries=3),
            attempt=attempt,
            lease=_active_lease(attempt=attempt),
            task_unit=make_unit(state=TaskState.PROCESSING),
            recovery_action_id="recovery_early_expiry",
            now="2026-07-22T00:01:00Z",
            correlation_id="corr_early_expiry",
        )

    assert ledger.read_all() == []


def _scheduled_attempt():
    from tokenshare.core.models import Attempt

    return Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at="2026-07-22T00:00:00Z",
        started_at="2026-07-22T00:00:00Z",
    )


def _active_lease(*, attempt):
    from tokenshare.core.models import Lease

    return Lease(
        lease_id=attempt.lease_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        client_id=attempt.client_id,
        state=LeaseState.ACTIVE,
        fencing_token="token_1",
        issued_at="2026-07-22T00:00:00Z",
        expires_at="2026-07-22T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="primary",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )


def _submission_for(
    *,
    attempt,
    lease,
    submission_id="submission_1",
    submitted_at="2026-07-22T00:01:00Z",
):
    return ExecutionSubmission(
        submission_id=submission_id,
        request_id="request_1",
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
        executor_id="executor_mock_ai",
        executor_version="0.1.0",
        result_kind="succeeded",
        raw_output_ref=None,
        parsed_output_ref=None,
        candidate_output_refs={},
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=make_environment_ref(),
        environment_summary={"runtime": "python"},
        provenance_ref=None,
        usage_summary={},
        error=None,
        submitted_at=submitted_at,
    )
