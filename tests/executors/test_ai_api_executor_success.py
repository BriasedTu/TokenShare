import json

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def parse_answer(raw_text: str):
    return {"answer": json.loads(raw_text)["answer"]}


def test_ai_api_executor_persists_raw_parsed_provenance_usage_and_cost(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-response-1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [
                        {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id="submission_ai_1",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is not None
    assert submission.provenance_ref is not None
    assert submission.candidate_output_refs["answer"] == submission.parsed_output_ref
    assert submission.usage_summary["total_tokens"] == 15
    assert submission.usage_summary["provider_attempt_count"] == 1
    assert submission.usage_summary["cost_estimate_status"] == "estimated"
    assert store.verify(submission.raw_output_ref)
    assert store.verify(submission.parsed_output_ref)
    assert store.verify(submission.provenance_ref)
    assert b"secret-a" not in store.read_bytes(submission.provenance_ref)
    assert b"secret-b" not in store.read_bytes(submission.provenance_ref)


def test_ai_api_executor_never_persists_api_key_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "super-secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "super-secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_secret_scan")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-secret-scan",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"safe"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "safe"},
    )

    executor.execute(
        request,
        submission_id="submission_secret_scan",
        submitted_at="2026-06-28T00:00:02Z",
    )

    for path in store.artifact_dir.glob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"super-secret-a" not in data
            assert b"super-secret-b" not in data


def test_ai_api_executor_marks_successful_response_missing_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_missing_usage")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-missing-usage",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"safe"}'}}],
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "safe"},
    )

    submission = executor.execute(
        request,
        submission_id="submission_missing_usage",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["cost_estimate_status"] == "usage_missing"
    assert submission.usage_summary["prompt_tokens"] is None
    assert submission.usage_summary["completion_tokens"] is None
    assert submission.usage_summary["total_tokens"] is None
    assert submission.usage_summary["cost_estimate"] is None
