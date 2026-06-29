from tests.phase7_fixtures import FakeProviderResponse, make_config_dict
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    SiliconFlowProviderError,
    UrlLibSiliconFlowTransport,
    build_siliconflow_chat_body,
    parse_siliconflow_response,
)


def test_build_siliconflow_chat_body_uses_prompt_and_json_mode() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = config.entries[0]

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 64},
        soft_hints={"temperature": 0.3},
        require_json_mode=True,
    )

    assert body["model"] == entry.model
    assert body["messages"] == [{"role": "user", "content": "Return JSON."}]
    assert body["stream"] is False
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.1
    assert body["response_format"] == {"type": "json_object"}


def test_parse_siliconflow_response_extracts_content_and_usage() -> None:
    response = FakeProviderResponse(
        status_code=200,
        body={
            "id": "sf-response-1",
            "model": "deepseek-ai/DeepSeek-V3",
            "choices": [
                {
                    "message": {"role": "assistant", "content": '{"answer":"ok"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )

    parsed = parse_siliconflow_response(response)

    assert parsed.provider_response_id == "sf-response-1"
    assert parsed.content_text == '{"answer":"ok"}'
    assert parsed.usage["total_tokens"] == 15


def test_parse_siliconflow_response_maps_rate_limit_error() -> None:
    response = FakeProviderResponse(
        status_code=429,
        body={"code": "rate_limit", "message": "too many requests"},
    )

    try:
        parse_siliconflow_response(response)
    except SiliconFlowProviderError as exc:
        assert exc.error_kind == "rate_limited"
        assert exc.http_status == 429
    else:
        raise AssertionError("expected SiliconFlowProviderError")


def test_urllib_transport_maps_invalid_json_body_to_provider_error(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        return _FakeUrlOpenResponse(200, b"not-json")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = load_ai_api_config(make_config_dict())
    entry = config.entries[0]

    try:
        UrlLibSiliconFlowTransport().post_chat_completion(
            entry=entry,
            api_key="secret",
            body={"model": entry.model},
            timeout_seconds=30,
        )
    except SiliconFlowProviderError as exc:
        assert exc.error_kind == "invalid_output"
        assert exc.http_status == 200
    else:
        raise AssertionError("expected SiliconFlowProviderError")


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
