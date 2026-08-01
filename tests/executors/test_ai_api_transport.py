from dataclasses import replace

import pytest

from tests.phase7_fixtures import FakeProviderResponse, make_config_dict, prepared_wire_kwargs
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
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "Return JSON."}
    assert body["stream"] is False
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert body["enable_thinking"] is False


def test_build_siliconflow_chat_body_disables_qwen_thinking_for_json_mode() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = replace(config.entries[0], model="Qwen/Qwen3.6-27B")

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 1024},
        soft_hints={"temperature": 0.0},
        require_json_mode=True,
    )

    assert body["messages"][0]["role"] == "system"
    assert "valid JSON object" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "Return JSON."}
    assert body["enable_thinking"] is False


def test_build_siliconflow_chat_body_preserves_explicit_thinking_override() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = replace(
        config.entries[0],
        request_overrides={
            **config.entries[0].request_overrides,
            "enable_thinking": True,
        },
    )

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 1024},
        soft_hints={"temperature": 0.0},
        require_json_mode=True,
    )

    assert body["enable_thinking"] is True


def test_build_siliconflow_chat_body_sends_explicit_thinking_budget_controls() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = replace(
        config.entries[0],
        request_overrides={
            "temperature": 0.0,
            "top_p": 1.0,
            "enable_thinking": True,
            "thinking_budget": 32768,
        },
    )

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 32768},
        soft_hints={},
        require_json_mode=True,
    )

    assert {
        key: body[key]
        for key in (
            "enable_thinking",
            "thinking_budget",
            "temperature",
            "top_p",
            "max_tokens",
        )
    } == {
        "enable_thinking": True,
        "thinking_budget": 32768,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 32768,
    }


def test_build_siliconflow_chat_body_sends_explicit_non_thinking_without_budget() -> None:
    config = load_ai_api_config(make_config_dict())
    entry = replace(
        config.entries[0],
        request_overrides={
            "temperature": 0.0,
            "top_p": 1.0,
            "enable_thinking": False,
        },
    )

    body = build_siliconflow_chat_body(
        entry=entry,
        prompt_text="Return JSON.",
        defaults=config.defaults,
        request_limits={"max_tokens": 32768},
        soft_hints={},
        require_json_mode=True,
    )

    assert body["enable_thinking"] is False
    assert "thinking_budget" not in body


@pytest.mark.parametrize("thinking_budget", [True, 0, -1, 1.5, "32768"])
def test_build_siliconflow_chat_body_rejects_invalid_thinking_budget(
    thinking_budget,
) -> None:
    config = load_ai_api_config(make_config_dict())
    entry = replace(
        config.entries[0],
        request_overrides={
            "enable_thinking": True,
            "thinking_budget": thinking_budget,
        },
    )

    with pytest.raises(
        ValueError,
        match="request_overrides.thinking_budget must be a positive integer",
    ):
        build_siliconflow_chat_body(
            entry=entry,
            prompt_text="Return JSON.",
            defaults=config.defaults,
            request_limits={"max_tokens": 32768},
            soft_hints={},
            require_json_mode=True,
        )


@pytest.mark.parametrize("enable_thinking", [None, False])
def test_build_siliconflow_chat_body_requires_thinking_for_budget(
    enable_thinking: bool | None,
) -> None:
    config = load_ai_api_config(make_config_dict())
    request_overrides = {"thinking_budget": 32768}
    if enable_thinking is not None:
        request_overrides["enable_thinking"] = enable_thinking
    entry = replace(config.entries[0], request_overrides=request_overrides)

    with pytest.raises(
        ValueError,
        match="thinking_budget requires enable_thinking=true",
    ):
        build_siliconflow_chat_body(
            entry=entry,
            prompt_text="Return JSON.",
            defaults=config.defaults,
            request_limits={"max_tokens": 32768},
            soft_hints={},
            require_json_mode=True,
        )


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
    assert parsed.resolved_model == "deepseek-ai/DeepSeek-V3"
    assert parsed.response_model_status == "present"
    assert parsed.content_text == '{"answer":"ok"}'
    assert parsed.usage["total_tokens"] == 15


@pytest.mark.parametrize(
    ("response_model", "include_model", "expected_resolved", "expected_status"),
    [
        ("deepseek-ai/DeepSeek-V3-wrong", True, "deepseek-ai/DeepSeek-V3-wrong", "present"),
        (None, False, None, "missing"),
        (None, True, None, "null"),
        ("", True, None, "empty"),
        (123, True, None, "invalid_type"),
    ],
)
def test_parse_siliconflow_response_classifies_response_model_without_fallback(
    response_model,
    include_model: bool,
    expected_resolved: str | None,
    expected_status: str,
) -> None:
    body = {
        "id": "sf-response-model-shape",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    }
    if include_model:
        body["model"] = response_model

    parsed = parse_siliconflow_response(FakeProviderResponse(status_code=200, body=body))

    assert parsed.resolved_model == expected_resolved
    assert parsed.response_model_status == expected_status
    assert parsed.raw_response_json == body


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
            api_key="secret",
            **prepared_wire_kwargs(
                entry,
                {"model": entry.model, "messages": []},
                provider_family="siliconflow",
            ),
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
