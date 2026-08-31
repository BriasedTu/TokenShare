import json

import pytest

from tests.retained_fixtures import (
    FakeProviderResponse,
    make_config_dict,
    prepared_transport_kwargs,
)
from tokenshare.executors import ai_api_transport
from tokenshare.executors.ai_api_config import load_ai_api_config


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
            api_key="offline-test-secret",
            **prepared_transport_kwargs(
                _deepseek_config().entries[0],
                {"model": "deepseek-v4-pro", "messages": []},
                provider_family="deepseek",
            ),
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
        api_key="offline-test-secret",
        **prepared_transport_kwargs(
            _deepseek_config().entries[0],
            {"model": "deepseek-v4-pro", "messages": []},
            provider_family="deepseek",
        ),
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
            api_key="offline-test-secret",
            **prepared_transport_kwargs(
                _deepseek_config().entries[0],
                {"model": "deepseek-v4-pro", "messages": []},
                provider_family="deepseek",
            ),
            timeout_seconds=30,
        )
    assert error.value.error_kind == "timeout"
    assert error.value.http_status is None


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
