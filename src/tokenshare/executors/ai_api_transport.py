"""SiliconFlow chat-completions transport boundary."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_artifacts import classify_response_model
from tokenshare.executors.ai_api_config import AIAPIProviderEntry


@dataclass(frozen=True)
class SiliconFlowChatResult:
    provider_response_id: str | None
    resolved_model: str | None
    response_model_status: str
    content_text: str
    finish_reason: str | None
    usage: JsonObject | None
    raw_response_json: JsonObject


@dataclass(frozen=True)
class OpenAIChatResult:
    provider_response_id: str | None
    resolved_model: str | None
    response_model_status: str
    content_text: str
    finish_reason: str | None
    usage: JsonObject | None
    raw_response_json: JsonObject


class SiliconFlowProviderError(RuntimeError):
    def __init__(self, *, error_kind: str, http_status: int | None, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.http_status = http_status
        self.message = message


class OpenAIProviderError(RuntimeError):
    def __init__(self, *, error_kind: str, http_status: int | None, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.http_status = http_status
        self.message = message


def build_siliconflow_chat_body(
    *,
    entry: AIAPIProviderEntry,
    prompt_text: str,
    defaults: JsonObject,
    request_limits: JsonObject,
    soft_hints: JsonObject,
    require_json_mode: bool,
) -> JsonObject:
    messages = []
    if require_json_mode:
        messages.append(
            {
                "role": "system",
                "content": (
                    "You must output exactly one valid JSON object. "
                    "Do not output markdown, prose, or hidden reasoning in assistant content."
                ),
            }
        )
    messages.append({"role": "user", "content": prompt_text})
    body: JsonObject = {
        "model": entry.model,
        "messages": messages,
        "stream": False,
        "temperature": _choose_number(
            entry.request_overrides.get("temperature"),
            soft_hints.get("temperature"),
            defaults.get("temperature"),
        ),
        "top_p": _choose_number(
            entry.request_overrides.get("top_p"),
            soft_hints.get("top_p"),
            defaults.get("top_p"),
        ),
        "max_tokens": int(request_limits.get("max_tokens", defaults.get("max_tokens", 1024))),
    }
    if require_json_mode:
        if not entry.supports_json_mode:
            raise ValueError(f"entry does not support json mode: {entry.entry_id}")
        body["temperature"] = 0.0
        body["response_format"] = {"type": "json_object"}
    thinking_override = entry.request_overrides.get("enable_thinking")
    if thinking_override is not None and not isinstance(thinking_override, bool):
        raise ValueError("request_overrides.enable_thinking must be a boolean")
    if require_json_mode:
        if thinking_override is True:
            raise ValueError(
                "request_overrides.enable_thinking must be false when json mode is required"
            )
        body["enable_thinking"] = False
    elif thinking_override is not None:
        body["enable_thinking"] = thinking_override
    return body


def build_openai_chat_body(
    *,
    entry: AIAPIProviderEntry,
    prompt_text: str,
    defaults: JsonObject,
    request_limits: JsonObject,
    soft_hints: JsonObject,
    require_json_mode: bool,
) -> JsonObject:
    messages = []
    if require_json_mode:
        messages.append(
            {
                "role": "system",
                "content": (
                    "You must output exactly one valid JSON object. "
                    "Do not output markdown or prose outside the assistant JSON content."
                ),
            }
        )
    messages.append({"role": "user", "content": prompt_text})
    body: JsonObject = {
        "model": entry.model,
        "messages": messages,
        "stream": False,
        "temperature": _choose_number(
            entry.request_overrides.get("temperature"),
            soft_hints.get("temperature"),
            defaults.get("temperature"),
        ),
        "top_p": _choose_number(
            entry.request_overrides.get("top_p"),
            soft_hints.get("top_p"),
            defaults.get("top_p"),
        ),
        "max_tokens": int(request_limits.get("max_tokens", defaults.get("max_tokens", 1024))),
    }
    reasoning_effort = entry.request_overrides.get("reasoning_effort")
    if reasoning_effort is not None:
        body["reasoning_effort"] = str(reasoning_effort)
    if require_json_mode:
        if not entry.supports_json_mode:
            raise ValueError(f"entry does not support json mode: {entry.entry_id}")
        body["temperature"] = 0.0
        body["response_format"] = {"type": "json_object"}
    return body


def parse_siliconflow_response(response: Any) -> SiliconFlowChatResult:
    status_code = int(response.status_code)
    if not isinstance(response.body, dict):
        raise SiliconFlowProviderError(
            error_kind="invalid_output",
            http_status=status_code,
            message="provider response body must be a JSON object",
        )
    body = dict(response.body or {})
    if status_code >= 400:
        raise SiliconFlowProviderError(
            error_kind=_map_http_error(status_code),
            http_status=status_code,
            message=str(body.get("message") or body.get("error") or response.text or status_code),
        )
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SiliconFlowProviderError(
            error_kind="invalid_output",
            http_status=status_code,
            message="missing assistant message content",
        ) from exc
    resolved_model, response_model_status = classify_response_model(body)
    return SiliconFlowChatResult(
        provider_response_id=body.get("id"),
        resolved_model=resolved_model,
        response_model_status=response_model_status,
        content_text=str(content),
        finish_reason=choice.get("finish_reason"),
        usage=dict(body["usage"]) if isinstance(body.get("usage"), dict) else None,
        raw_response_json=body,
    )


def parse_openai_response(response: Any) -> OpenAIChatResult:
    status_code = int(response.status_code)
    if not isinstance(response.body, dict):
        raise OpenAIProviderError(
            error_kind="invalid_output",
            http_status=status_code,
            message="provider response body must be a JSON object",
        )
    body = dict(response.body or {})
    if status_code >= 400:
        raise OpenAIProviderError(
            error_kind=_map_http_error(status_code),
            http_status=status_code,
            message=_openai_error_message(body, response.text, status_code),
        )
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAIProviderError(
            error_kind="invalid_output",
            http_status=status_code,
            message="missing assistant message content",
        ) from exc
    if isinstance(content, list):
        content_text = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    else:
        content_text = str(content)
    resolved_model, response_model_status = classify_response_model(body)
    return OpenAIChatResult(
        provider_response_id=body.get("id"),
        resolved_model=resolved_model,
        response_model_status=response_model_status,
        content_text=content_text,
        finish_reason=choice.get("finish_reason"),
        usage=dict(body["usage"]) if isinstance(body.get("usage"), dict) else None,
        raw_response_json=body,
    )


def _choose_number(*values: object) -> float:
    for value in values:
        if value is not None:
            return float(value)
    return 0.0


def _map_http_error(status_code: int) -> str:
    if status_code == 429:
        return "rate_limited"
    if status_code in {500, 503, 504}:
        return "provider_error"
    if status_code in {401, 403}:
        return "auth_error"
    if status_code in {400, 404}:
        return "client_error"
    return "provider_error"


def _openai_error_message(body: JsonObject, text: str, status_code: int) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or text or status_code)
    return str(body.get("message") or error or text or status_code)


class UrlLibSiliconFlowTransport:
    def post_chat_completion(
        self,
        *,
        entry: AIAPIProviderEntry,
        api_key: str,
        body: JsonObject,
        timeout_seconds: int,
    ):
        url = f"{entry.base_url}{entry.endpoint}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                text = response.read().decode("utf-8")
                try:
                    body_json = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise SiliconFlowProviderError(
                        error_kind="invalid_output",
                        http_status=response.status,
                        message="provider returned non-json response body",
                    ) from exc
                return _UrlLibResponse(response.status, body_json, text)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                body_json = json.loads(text)
            except json.JSONDecodeError:
                body_json = {"message": text}
            return _UrlLibResponse(exc.code, body_json, text)
        except urllib.error.URLError as exc:
            raise SiliconFlowProviderError(
                error_kind="connection_error",
                http_status=None,
                message="provider connection failed",
            ) from exc


class UrlLibOpenAITransport:
    def post_chat_completion(
        self,
        *,
        entry: AIAPIProviderEntry,
        api_key: str,
        body: JsonObject,
        timeout_seconds: int,
    ):
        url = f"{entry.base_url}{entry.endpoint}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                text = response.read().decode("utf-8")
                try:
                    body_json = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise OpenAIProviderError(
                        error_kind="invalid_output",
                        http_status=response.status,
                        message="provider returned non-json response body",
                    ) from exc
                return _UrlLibResponse(response.status, body_json, text)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                body_json = json.loads(text)
            except json.JSONDecodeError:
                body_json = {"message": text}
            return _UrlLibResponse(exc.code, body_json, text)
        except urllib.error.URLError as exc:
            raise OpenAIProviderError(
                error_kind="connection_error",
                http_status=None,
                message="provider connection failed",
            ) from exc


class _UrlLibResponse:
    def __init__(self, status_code: int, body: JsonObject, text: str) -> None:
        self.status_code = status_code
        self.body = body
        self.text = text
