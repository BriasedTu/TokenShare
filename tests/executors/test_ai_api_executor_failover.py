import json
from dataclasses import replace

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def test_ai_api_executor_failover_after_rate_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=429, body={"message": "rate limit"}),
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-response-2",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": '{"answer":"fallback"}'}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
                },
            ),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "fallback"},
    )

    submission = executor.execute(
        request,
        submission_id="submission_failover",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["provider_attempt_count"] == 2
    assert len(transport.calls) == 2
    provenance = store.read_bytes(submission.provenance_ref).decode("utf-8")
    assert "rate_limited" in provenance
    assert "secret-a" not in provenance
    assert "secret-b" not in provenance


def test_ai_api_executor_failover_after_client_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_timeout_failover")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=0, error="timeout"),
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-timeout-fallback",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": '{"answer":"timeout-fallback"}'}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
                },
            ),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "timeout-fallback"},
    )

    submission = executor.execute(
        request,
        submission_id="submission_timeout_failover",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["provider_attempt_count"] == 2
    provenance = store.read_bytes(submission.provenance_ref).decode("utf-8")
    assert "timeout" in provenance


def test_ai_api_executor_returns_executor_error_after_all_entries_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_all_fail")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=503, body={"message": "overloaded"}),
            FakeProviderResponse(status_code=504, body={"message": "timeout"}),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
    )

    submission = executor.execute(
        request,
        submission_id="submission_all_fail",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "executor_error"
    assert submission.raw_output_ref is None
    assert submission.provenance_ref is not None
    assert submission.error["kind"] == "executor_error"
    assert submission.usage_summary["provider_attempt_count"] == 2


def test_ai_api_executor_invalid_provider_envelope_does_not_failover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_invalid_envelope")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(status_code=200, body={"id": "bad-envelope", "choices": []}),
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "would-be-retry",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": '{"answer":"late"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            ),
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
    )

    submission = executor.execute(
        request,
        submission_id="submission_invalid_envelope",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "invalid_output"
    assert submission.parse_failure_ref is not None
    assert submission.raw_output_ref is None
    assert len(transport.calls) == 1
    assert b"missing assistant message content" in store.read_bytes(submission.parse_failure_ref)


def test_ai_api_executor_skips_missing_secret_entry_and_returns_artifact_backed_error(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SILICONFLOW_API_KEY_A", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY_B", raising=False)
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_no_secret")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([])
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
    )

    submission = executor.execute(
        request,
        submission_id="submission_no_secret",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "executor_error"
    assert submission.provenance_ref is not None
    assert submission.parse_failure_ref is None
    assert submission.error["kind"] == "executor_error"
    assert submission.error["reason"] == "no_eligible_entries"
    assert len(transport.calls) == 0
    provenance = store.read_bytes(submission.provenance_ref).decode("utf-8")
    assert "SILICONFLOW_API_KEY_A" in provenance
    assert "SILICONFLOW_API_KEY_B" in provenance


def test_ai_api_executor_failover_after_transport_network_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")

    class NetworkErrorTransport(FakeSiliconFlowTransport):
        def post_chat_completion(self, **kwargs):
            if not self.calls:
                self.calls.append(
                    {
                        "entry_id": kwargs["entry"].entry_id,
                        "model": kwargs["entry"].model,
                        "body": kwargs["body"],
                        "timeout_seconds": kwargs["timeout_seconds"],
                        "api_key_seen": bool(kwargs["api_key"]),
                    }
                )
                raise OSError("network unreachable")
            return super().post_chat_completion(**kwargs)

    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_network_failover")
    config = load_ai_api_config(make_config_dict())
    transport = NetworkErrorTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-network-fallback",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": '{"answer":"network-fallback"}'}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
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
        parser=lambda raw: {"answer": "network-fallback"},
    )

    submission = executor.execute(
        request,
        submission_id="submission_network_failover",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["provider_attempt_count"] == 2
    assert len(transport.calls) == 2
    provenance = store.read_bytes(submission.provenance_ref).decode("utf-8")
    assert "connection_error" in provenance


def test_ai_api_executor_rejects_string_prompt_json_mode_constraint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_bad_prompt_constraint")
    prompt_body = json.loads(store.read_bytes(request.prompt_package_ref).decode("utf-8"))
    prompt_body["constraints"]["requires_json_mode"] = "false"
    prompt_ref = store.save_json(
        prompt_body,
        artifact_id="prompt_request_bad_prompt_constraint_string_bool",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "phase7_test"},
        metadata={},
        created_at=prompt_body["created_at"],
    )
    request = replace(request, prompt_package_ref=prompt_ref)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([])
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
    )

    submission = executor.execute(
        request,
        submission_id="submission_bad_prompt_constraint",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "executor_error"
    assert submission.error["reason"] == "invalid_prompt_package"
    assert "requires_json_mode" in submission.error["message"]
    assert submission.provenance_ref is not None
    assert len(transport.calls) == 0
