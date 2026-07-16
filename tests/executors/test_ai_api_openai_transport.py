from tests.phase7_fixtures import FakeProviderResponse, make_config_dict
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    OpenAIProviderError,
    UrlLibOpenAITransport,
    build_openai_chat_body,
    parse_openai_response,
)


def _openai_config():
    body = make_config_dict()
    body["provider_family"] = "openai"
    body["defaults"] = {
        **body["defaults"],
        "max_tokens": 256,
        "temperature": 0.2,
        "top_p": 1.0,
    }
    body["entries"] = [
        {
            "entry_id": "openai_gpt_5_6_sol_high",
            "enabled": True,
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-5.6-sol",
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": {"reasoning_effort": "high", "temperature": 0.0},
            "pricing": {
                "currency": "USD",
                "input_per_million_tokens": 0.0,
                "output_per_million_tokens": 0.0,
            },
            "tags": ["paper", "openai", "reasoning:high"],
        }
    ]
    return load_ai_api_config(body)


def test_build_openai_chat_body_uses_reasoning_effort_without_siliconflow_fields() -> None:
    config = _openai_config()
    entry = config.entries[0]

    body = build_openai_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 64},
        soft_hints={"temperature": 0.3},
        require_json_mode=True,
    )

    assert body["model"] == "gpt-5.6-sol"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "Return JSON."}
    assert body["stream"] is False
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.0
    assert body["reasoning_effort"] == "high"
    assert body["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in body


def test_parse_openai_response_extracts_content_usage_and_resolved_model() -> None:
    response = FakeProviderResponse(
        status_code=200,
        body={
            "id": "chatcmpl-openai-1",
            "model": "gpt-5.6-sol-2026-07-01",
            "choices": [
                {
                    "message": {"role": "assistant", "content": '{"answer":"ok"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )

    parsed = parse_openai_response(response)

    assert parsed.provider_response_id == "chatcmpl-openai-1"
    assert parsed.model == "gpt-5.6-sol-2026-07-01"
    assert parsed.content_text == '{"answer":"ok"}'
    assert parsed.finish_reason == "stop"
    assert parsed.usage["total_tokens"] == 18


def test_parse_openai_response_maps_rate_limit_error() -> None:
    response = FakeProviderResponse(
        status_code=429,
        body={"error": {"type": "rate_limit_exceeded", "message": "too many requests"}},
    )

    try:
        parse_openai_response(response)
    except OpenAIProviderError as exc:
        assert exc.error_kind == "rate_limited"
        assert exc.http_status == 429
    else:
        raise AssertionError("expected OpenAIProviderError")


def test_openai_urllib_transport_maps_invalid_json_body_to_provider_error(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        return _FakeUrlOpenResponse(200, b"not-json")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = _openai_config()
    entry = config.entries[0]

    try:
        UrlLibOpenAITransport().post_chat_completion(
            entry=entry,
            api_key="secret",
            body={"model": entry.model},
            timeout_seconds=30,
        )
    except OpenAIProviderError as exc:
        assert exc.error_kind == "invalid_output"
        assert exc.http_status == 200
    else:
        raise AssertionError("expected OpenAIProviderError")


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
