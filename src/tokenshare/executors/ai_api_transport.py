"""SiliconFlow chat-completions transport boundary."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_config import AIAPIProviderEntry


@dataclass(frozen=True)
class SiliconFlowChatResult:
    provider_response_id: str | None
    model: str | None
    content_text: str
    finish_reason: str | None
    usage: JsonObject
    raw_response_json: JsonObject


class SiliconFlowProviderError(RuntimeError):
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
    body: JsonObject = {
        "model": entry.model,
        "messages": [{"role": "user", "content": prompt_text}],
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
        body["response_format"] = {"type": "json_object"}
    return body


def parse_siliconflow_response(response: Any) -> SiliconFlowChatResult:
    status_code = int(response.status_code)
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
    return SiliconFlowChatResult(
        provider_response_id=body.get("id"),
        model=body.get("model"),
        content_text=str(content),
        finish_reason=choice.get("finish_reason"),
        usage=dict(body.get("usage", {})),
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
                return _UrlLibResponse(response.status, json.loads(text), text)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                body_json = json.loads(text)
            except json.JSONDecodeError:
                body_json = {"message": text}
            return _UrlLibResponse(exc.code, body_json, text)


class _UrlLibResponse:
    def __init__(self, status_code: int, body: JsonObject, text: str) -> None:
        self.status_code = status_code
        self.body = body
        self.text = text
