from tests.phase2_fixtures import make_client, make_config, make_unit
from tests.phase3_fixtures import make_environment_ref, make_output_contract
from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_config_dict,
    make_prompt_ref,
)
from tokenshare.core.models import AttemptState
from tokenshare.core.task_graph import TaskGraph
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def test_phase7_ai_api_submission_uses_existing_phase3_submission_flow(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    unit = make_unit(required_capabilities={"executor": "ai_api", "provider_family": "siliconflow"})
    graph = TaskGraph(task_id="task_demo", units={unit.unit_id: unit}, relations=[])
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    scheduled = engine.schedule_ready_unit(
        graph=graph,
        clients=[
            make_client(
                capabilities={"executor": "ai_api", "provider_family": "siliconflow"},
                status="active",
            )
        ],
        now="2026-06-28T00:00:01Z",
        correlation_id="corr_schedule_ai",
        decision_id="decision_ai",
        lease_id="lease_ai",
        attempt_id="attempt_ai",
        fencing_token="fence_ai",
    )
    request = ExecutionRequest(
        request_id="request_ai_protocol",
        task_id="task_demo",
        unit_id=scheduled.task_unit.unit_id,
        attempt_id=scheduled.attempt.attempt_id,
        lease_id=scheduled.lease.lease_id,
        fencing_token=scheduled.lease.fencing_token,
        plugin={
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "ai_output_parser_policy_id": "structured_report.answer_parser.v1",
        },
        executor={
            "executor_id": "executor_ai_api",
            "executor_version": "0.1.0",
        },
        registry_snapshot_id="registry_snapshot_ai",
        allocation_decision={
            "decision_id": "allocation_ai",
            "selected_executor_id": "executor_ai_api",
            "eligible_executor_ids": ["executor_ai_api"],
        },
        capability_snapshot={"executor": "ai_api", "provider_family": "siliconflow"},
        task_unit_snapshot=scheduled.task_unit.to_dict(),
        input_artifact_refs=scheduled.task_unit.input_refs,
        output_contract=make_output_contract(),
        hard_requirements={"executor": "ai_api", "provider_family": "siliconflow"},
        soft_hints={"temperature": 0.2},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=make_prompt_ref(store, request_id="request_ai_protocol"),
        limits={"timeout_seconds": 30, "max_tokens": 128},
        created_at="2026-06-28T00:00:02Z",
    )
    request_result = engine.record_execution_request(
        request=request,
        correlation_id="corr_request_ai",
        causation_event_id=scheduled.events[-1].event_id,
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(make_config_dict()),
        transport=FakeSiliconFlowTransport(
            [
                FakeProviderResponse(
                    status_code=200,
                    body={
                        "id": "sf-protocol-flow",
                        "model": "Qwen/Qwen2.5-7B-Instruct",
                        "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )
            ]
        ),
        parser=lambda raw: {"answer": "ok"},
    )
    submission = executor.execute(
        request,
        submission_id="submission_ai_protocol",
        submitted_at="2026-06-28T00:00:03Z",
    )

    submission_result = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id="corr_submission_ai",
        causation_event_id=request_result.event.event_id,
    )

    assert submission_result.event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
    assert submission_result.attempt is not None
    assert submission_result.attempt.state == AttemptState.SUBMITTED
    assert submission_result.attempt_event is not None
    assert submission_result.attempt_event.payload["new_state"] == "Submitted"
    assert [event.event_type for event in ledger.read_all()][-3:] == [
        EventType.EXECUTION_REQUEST_RECORDED,
        EventType.EXECUTION_SUBMISSION_RECORDED,
        EventType.ATTEMPT_STATE_CHANGED,
    ]
