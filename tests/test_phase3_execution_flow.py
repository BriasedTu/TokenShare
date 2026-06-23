from tokenshare.core.models import AttemptState
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.mock_ai import MockAIExecutor, MockAIExecutorProfile
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

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
