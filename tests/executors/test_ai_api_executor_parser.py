from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def _success_response(content: str):
    return FakeProviderResponse(
        status_code=200,
        body={
            "id": "sf-response-parser",
            "model": "deepseek-ai/DeepSeek-V3",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
        },
    )


def test_parse_failure_does_not_failover_after_provider_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("not-json")])

    def parser(_raw: str):
        raise ValueError("plugin parser rejected output")

    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
    )

    submission = executor.execute(
        request,
        submission_id="submission_parse_fail",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "parse_failed"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.parse_failure_ref is not None
    assert len(transport.calls) == 1
    assert b"plugin parser rejected output" in store.read_bytes(submission.parse_failure_ref)


def test_raw_only_mode_returns_succeeded_without_candidate_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("plain text answer")])
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=None,
    )

    submission = executor.execute(
        request,
        submission_id="submission_raw_only",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.candidate_output_refs == {}
    assert submission.parse_failure_ref is None
