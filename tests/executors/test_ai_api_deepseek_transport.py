import json
from dataclasses import replace

import pytest

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors import ai_api_transport
from tokenshare.executors.ai_api import AIAPIExecutor, _usage_summary
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def _deepseek_config():
    body = make_config_dict()
    body["provider_family"] = "deepseek"
    body["defaults"] = {
        **body["defaults"],
        "max_tokens": 8192,
        "temperature": 0.7,
        "top_p": 0.8,
        "max_provider_attempts": 1,
    }
    body["local_concurrency"] = {"max_in_flight_global": 50}
    body["entries"] = [
        {
            "entry_id": "deepseek_v4_pro_exp1_baseline",
            "enabled": True,
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-v4-pro",
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                "temperature": 0.4,
                "top_p": 0.5,
            },
            "pricing": {
                "currency": "CNY",
                "cached_input_per_million_tokens": 0.025,
                "uncached_input_per_million_tokens": 3.0,
                "output_per_million_tokens": 6.0,
            },
            "tags": ["paper", "deepseek", "reasoning:high"],
        }
    ]
    return load_ai_api_config(body)


def _deepseek_symbol(name: str):
    value = getattr(ai_api_transport, name, None)
    assert value is not None, f"missing DeepSeek transport symbol: {name}"
    return value


def test_build_deepseek_chat_body_uses_fixed_thinking_controls_and_omits_sampling() -> None:
    config = _deepseek_config()
    builder = _deepseek_symbol("build_deepseek_chat_body")

    body = builder(
        entry=config.entries[0],
        prompt_text="Return one JSON object.",
        defaults=config.defaults,
        request_limits={"max_tokens": 8192},
        soft_hints={"temperature": 0.3, "top_p": 0.2},
        require_json_mode=True,
    )

    assert body == {
        "model": "deepseek-v4-pro",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You must output exactly one valid JSON object. "
                    "Do not output markdown or prose outside the assistant JSON content."
                ),
            },
            {"role": "user", "content": "Return one JSON object."},
        ],
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    assert "temperature" not in body
    assert "top_p" not in body


def test_parse_deepseek_response_preserves_reasoning_content_and_usage() -> None:
    parser = _deepseek_symbol("parse_deepseek_response")
    response = FakeProviderResponse(
        status_code=200,
        body={
            "id": "deepseek-response-1",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "完整推理证据",
                        "content": '{"answer":"ok"}',
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 110,
                "completion_tokens": 30,
                "total_tokens": 140,
                "prompt_cache_hit_tokens": 10,
                "prompt_cache_miss_tokens": 100,
                "completion_tokens_details": {"reasoning_tokens": 20},
            },
        },
    )

    parsed = parser(response)

    assert parsed.content_text == '{"answer":"ok"}'
    assert parsed.reasoning_content == "完整推理证据"
    assert parsed.usage["completion_tokens_details"]["reasoning_tokens"] == 20
    assert parsed.raw_response_json == response.body


def test_deepseek_usage_cost_uses_cache_breakdown_and_reasoning_tokens() -> None:
    entry = _deepseek_config().entries[0]

    summary = _usage_summary(
        "deepseek",
        entry,
        requested_model="deepseek-v4-pro",
        usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
            "total_tokens": 1_500_000,
            "prompt_cache_hit_tokens": 250_000,
            "prompt_cache_miss_tokens": 750_000,
            "completion_tokens_details": {"reasoning_tokens": 200_000},
        },
        attempt_count=1,
    )

    assert summary["prompt_cache_hit_tokens"] == 250_000
    assert summary["prompt_cache_miss_tokens"] == 750_000
    assert summary["reasoning_tokens"] == 200_000
    assert summary["currency"] == "CNY"
    assert summary["cost_estimate"] == pytest.approx(5.25625)
    assert summary["cost_estimate_status"] == "estimated"
    assert summary["cost_estimate_basis"] == "provider_cache_breakdown"


def test_deepseek_usage_without_cache_breakdown_is_conservatively_uncached() -> None:
    entry = _deepseek_config().entries[0]

    summary = _usage_summary(
        "deepseek",
        entry,
        requested_model="deepseek-v4-pro",
        usage={
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
            "total_tokens": 1_500_000,
        },
        attempt_count=1,
    )

    assert summary["cost_estimate"] == pytest.approx(6.0)
    assert summary["cost_estimate_basis"] == "all_prompt_tokens_uncached"
    assert summary["prompt_cache_hit_tokens"] is None
    assert summary["prompt_cache_miss_tokens"] is None


def test_deepseek_usage_missing_is_not_zero_cost() -> None:
    entry = _deepseek_config().entries[0]

    summary = _usage_summary(
        "deepseek",
        entry,
        requested_model="deepseek-v4-pro",
        usage=None,
        attempt_count=1,
    )

    assert summary["cost_estimate_status"] == "usage_missing"
    assert summary["cost_estimate"] is None
    assert summary["prompt_tokens"] is None
    assert summary["reasoning_tokens"] is None


def test_parse_deepseek_response_maps_429_and_schema_failure() -> None:
    parser = _deepseek_symbol("parse_deepseek_response")
    error_type = _deepseek_symbol("DeepSeekProviderError")

    with pytest.raises(error_type) as rate_error:
        parser(
            FakeProviderResponse(
                status_code=429,
                body={"error": {"message": "too many requests"}},
            )
        )
    assert rate_error.value.error_kind == "rate_limited"
    assert rate_error.value.http_status == 429

    with pytest.raises(error_type) as schema_error:
        parser(FakeProviderResponse(status_code=200, body={"choices": []}))
    assert schema_error.value.error_kind == "invalid_output"


@pytest.mark.parametrize("response_body", [b"", b"   \r\n", b"not-json"])
def test_deepseek_transport_rejects_empty_or_invalid_json(monkeypatch, response_body) -> None:
    transport_type = _deepseek_symbol("UrlLibDeepSeekTransport")
    error_type = _deepseek_symbol("DeepSeekProviderError")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: _FakeUrlOpenResponse(200, response_body),
    )

    with pytest.raises(error_type) as error:
        transport_type().post_chat_completion(
            entry=_deepseek_config().entries[0],
            api_key="offline-test-secret",
            body={"model": "deepseek-v4-pro"},
            timeout_seconds=30,
        )
    assert error.value.error_kind == "invalid_output"


def test_deepseek_transport_accepts_blank_keepalive_before_json(monkeypatch) -> None:
    transport_type = _deepseek_symbol("UrlLibDeepSeekTransport")
    response = {
        "id": "deepseek-keepalive",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: _FakeUrlOpenResponse(
            200,
            b"\r\n \n" + json.dumps(response).encode("utf-8"),
        ),
    )

    result = transport_type().post_chat_completion(
        entry=_deepseek_config().entries[0],
        api_key="offline-test-secret",
        body={"model": "deepseek-v4-pro"},
        timeout_seconds=30,
    )

    assert result.body == response


def test_deepseek_transport_classifies_timeout(monkeypatch) -> None:
    transport_type = _deepseek_symbol("UrlLibDeepSeekTransport")
    error_type = _deepseek_symbol("DeepSeekProviderError")

    def raise_timeout(_request, timeout):
        raise TimeoutError("offline timeout")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    with pytest.raises(error_type) as error:
        transport_type().post_chat_completion(
            entry=_deepseek_config().entries[0],
            api_key="offline-test-secret",
            body={"model": "deepseek-v4-pro"},
            timeout_seconds=30,
        )
    assert error.value.error_kind == "timeout"
    assert error.value.http_status is None


def test_deepseek_executor_persists_reasoning_and_safe_resolved_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-deepseek-test-secret")
    store = ArtifactStore(tmp_path)
    request = replace(
        make_ai_request(store, request_id="request_deepseek_success"),
        hard_requirements={"executor": "ai_api", "provider_family": "deepseek"},
        capability_snapshot={"executor": "ai_api", "provider_family": "deepseek"},
        limits={"timeout_seconds": 30, "max_tokens": 8192},
    )
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "deepseek-executor-success",
                    "model": "deepseek-v4-pro",
                    "choices": [
                        {
                            "message": {
                                "content": '{"answer":"ok"}',
                                "reasoning_content": "受控推理证据",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "prompt_cache_hit_tokens": 2,
                        "prompt_cache_miss_tokens": 8,
                        "completion_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            )
        ]
    )

    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=_deepseek_config(),
        transport=transport,
        parser=lambda raw: json.loads(raw),
    ).execute(
        request,
        submission_id="submission_deepseek_success",
        submitted_at="2026-07-27T00:00:00Z",
    )

    raw = json.loads(store.read_bytes(submission.raw_output_ref).decode("utf-8"))
    provenance = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))
    request_identity = provenance["attempts"][0]["provider_request_identity"]
    assert submission.result_kind == "succeeded"
    assert raw["content_text"] == '{"answer":"ok"}'
    assert raw["reasoning_content"] == "受控推理证据"
    assert request_identity["provider_family"] == "deepseek"
    assert request_identity["reasoning_controls"] == {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }
    assert "messages" not in request_identity
    assert "temperature" not in transport.calls[0]["body"]
    assert "top_p" not in transport.calls[0]["body"]
    assert submission.usage_summary["reasoning_tokens"] == 3
    assert b"offline-deepseek-test-secret" not in store.read_bytes(
        submission.provenance_ref
    )


def test_deepseek_executor_timeout_marks_usage_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-timeout-test-secret")
    store = ArtifactStore(tmp_path)
    request = replace(
        make_ai_request(store, request_id="request_deepseek_timeout"),
        hard_requirements={"executor": "ai_api", "provider_family": "deepseek"},
        capability_snapshot={"executor": "ai_api", "provider_family": "deepseek"},
        limits={"timeout_seconds": 30, "max_tokens": 8192},
    )
    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=_deepseek_config(),
        transport=FakeSiliconFlowTransport(
            [FakeProviderResponse(status_code=0, error="timeout")]
        ),
        parser=lambda raw: json.loads(raw),
    ).execute(
        request,
        submission_id="submission_deepseek_timeout",
        submitted_at="2026-07-27T00:00:00Z",
    )

    assert submission.result_kind == "timeout"
    assert submission.error["kind"] == "timeout"
    assert submission.usage_summary["provider_attempt_count"] == 1
    assert submission.usage_summary["cost_estimate_status"] == "usage_missing"
    assert submission.usage_summary["cost_estimate"] is None


class _FakeUrlOpenResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body
