"""Slim V2 单次 provider 调用与冻结成本投影。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import ctypes
from time import monotonic_ns
from typing import Any
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from tokenshare.executors.ai_api_config import AIAPIProviderEntry
from tokenshare.executors.ai_api_transport import (
    build_deepseek_chat_body,
    build_siliconflow_chat_body,
    parse_deepseek_response,
    parse_siliconflow_response,
)

from .schema import (
    LEGACY_PRICING_VERSION,
    PRICING_VERSION,
    ProviderCallResultV1,
    ProviderEntryViewV1,
    ProviderRequestControlV1,
)
from .storage import RootKeyV1, RunStore, StorageConflictError


RESPONSE_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProviderCallContextV1:
    call_key: str
    root_key: RootKeyV1
    planned_ai_unit_id: str
    attempt_ordinal: int

    def validate(self) -> None:
        if not isinstance(self.call_key, str) or not self.call_key:
            raise ValueError("call_key must be non-empty text")
        if (
            not isinstance(self.root_key, tuple)
            or len(self.root_key) != 4
            or not all(isinstance(value, str) and value for value in self.root_key[:3])
            or isinstance(self.root_key[3], bool)
            or not isinstance(self.root_key[3], int)
            or self.root_key[3] < 0
        ):
            raise ValueError("root_key must be a valid Slim root identity")
        if not isinstance(self.planned_ai_unit_id, str) or not self.planned_ai_unit_id:
            raise ValueError("planned_ai_unit_id must be non-empty text")
        if (
            isinstance(self.attempt_ordinal, bool)
            or not isinstance(self.attempt_ordinal, int)
            or self.attempt_ordinal < 0
        ):
            raise ValueError("attempt_ordinal must be natural")


@dataclass(frozen=True, slots=True)
class CostProjectionV1:
    pricing_version: str
    pricing_tier: str | None
    cost_estimate_cny: float | None


@dataclass(frozen=True, slots=True)
class _ParserResponse:
    status_code: int
    body: Any
    text: str


_SILICONFLOW_RATES = {
    "zai-org/GLM-5.2": (8.0, 28.0, 2.0),
    "Qwen/Qwen3-14B": (0.5, 2.0, None),
    "MiniMaxAI/MiniMax-M2.5": (2.1, 8.4, 0.21),
    "Pro/deepseek-ai/DeepSeek-V3": (2.0, 8.0, 0.2),
}


def project_cost(
    *,
    provider_family: str,
    configured_model: str,
    provider_request_started_at_utc: str | None,
    prompt_tokens: int | None,
    prompt_cache_hit_tokens: int | None,
    prompt_cache_miss_tokens: int | None,
    completion_tokens: int | None,
) -> CostProjectionV1:
    """按 metrics authority 1.4 纯投影成本；缺输入只返回 null。"""

    if provider_family == "deepseek" and configured_model == "deepseek-v4-flash":
        if (
            prompt_tokens is None
            or prompt_cache_hit_tokens is None
            or prompt_cache_miss_tokens is None
            or completion_tokens is None
            or prompt_tokens != prompt_cache_hit_tokens + prompt_cache_miss_tokens
        ):
            return CostProjectionV1(PRICING_VERSION, "flat", None)
        cost = (
            prompt_cache_hit_tokens * 0.05
            + prompt_cache_miss_tokens * 1.5
            + completion_tokens * 4.5
        ) / 1_000_000
        return CostProjectionV1(PRICING_VERSION, "flat", cost)

    if provider_family == "deepseek":
        tier = _deepseek_tier(provider_request_started_at_utc)
        if (
            configured_model != "deepseek-v4-pro"
            or
            tier is None
            or prompt_tokens is None
            or prompt_cache_hit_tokens is None
            or prompt_cache_miss_tokens is None
            or completion_tokens is None
            or prompt_tokens != prompt_cache_hit_tokens + prompt_cache_miss_tokens
        ):
            return CostProjectionV1(LEGACY_PRICING_VERSION, tier, None)
        hit, miss, output = (0.30, 9.0, 27.0) if tier == "peak" else (0.15, 4.5, 13.5)
        cost = (
            prompt_cache_hit_tokens * hit
            + prompt_cache_miss_tokens * miss
            + completion_tokens * output
        ) / 1_000_000
        return CostProjectionV1(LEGACY_PRICING_VERSION, tier, cost)

    if provider_family == "siliconflow":
        rates = _SILICONFLOW_RATES.get(configured_model)
        if rates is None or prompt_tokens is None or completion_tokens is None:
            return CostProjectionV1(LEGACY_PRICING_VERSION, "flat", None)
        input_rate, output_rate, cache_hit_rate = rates
        if cache_hit_rate is None:
            cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
        elif (
            prompt_cache_hit_tokens is None
            or prompt_cache_miss_tokens is None
            or prompt_tokens != prompt_cache_hit_tokens + prompt_cache_miss_tokens
        ):
            cost = None
        else:
            cost = (
                prompt_cache_hit_tokens * cache_hit_rate
                + prompt_cache_miss_tokens * input_rate
                + completion_tokens * output_rate
            ) / 1_000_000
        return CostProjectionV1(LEGACY_PRICING_VERSION, "flat", cost)

    return CostProjectionV1(PRICING_VERSION, None, None)


def _deepseek_tier(started_at: str | None) -> str | None:
    if not isinstance(started_at, str):
        return None
    try:
        instant = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return None
        local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
    except (ValueError, TypeError):
        return None
    minute = local.hour * 60 + local.minute
    return "peak" if 540 <= minute < 720 or 840 <= minute < 1080 else "off_peak"


def _entry(entry: ProviderEntryViewV1) -> AIAPIProviderEntry:
    return AIAPIProviderEntry(
        entry_id=str(entry.entry_id),
        enabled=True,
        base_url=str(entry.base_url),
        api_key_env=str(entry.api_key_env),
        model=str(entry.configured_model),
        endpoint=str(entry.endpoint),
        supports_json_mode=bool(entry.supports_json_mode),
        supports_streaming=False,
        request_overrides=dict(entry.request_overrides),
        pricing={},
        tags=[],
    )


def _open_response(request: urllib.request.Request, timeout_seconds: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout_seconds)


def call_provider_once(
    entry: ProviderEntryViewV1,
    prompt: str,
    control: ProviderRequestControlV1,
    context: ProviderCallContextV1,
    store: RunStore,
) -> ProviderCallResultV1:
    """记录一次 intent，最多发送一次，并在同一 store 写 response/terminal。"""

    entry.validate()
    control.validate()
    context.validate()
    if not isinstance(prompt, str):
        raise TypeError("prompt must be text")
    if not isinstance(store, RunStore):
        raise TypeError("store must be RunStore")
    if store.call_intent_path(context.call_key).exists() or store.call_terminal_path(context.call_key).exists():
        raise StorageConflictError(f"provider call journal key already exists: {context.call_key}")

    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    intent = {
        "call_key": context.call_key,
        "root_key": list(context.root_key),
        "planned_ai_unit_id": context.planned_ai_unit_id,
        "attempt_ordinal": context.attempt_ordinal,
        "provider_family": entry.provider_family,
        "provider_entry_id": entry.entry_id,
        "configured_model": entry.configured_model,
        "provider_request_started_at_utc": started,
        "owner_pid": os.getpid(),
        "owner_started_at_utc": started,
    }
    store.write_call_intent(context.call_key, intent)

    started_ns = monotonic_ns()
    response: Any | None = None
    raw: bytes | None = None
    status: int | None = None
    try:
        configured = _entry(entry)
        api_key = os.environ.get(configured.api_key_env, "")
        if not api_key:
            return _finish_failure(
                store, context, entry, started, started_ns,
                "provider_configuration_invalid", f"missing API key env var: {configured.api_key_env}", None,
            )
        builder = build_deepseek_chat_body if entry.provider_family == "deepseek" else build_siliconflow_chat_body
        if entry.provider_family not in {"deepseek", "siliconflow"}:
            return _finish_failure(
                store, context, entry, started, started_ns,
                "provider_configuration_invalid", f"unsupported provider family: {entry.provider_family}", None,
            )
        body = builder(
            entry=configured,
            prompt_text=prompt,
            defaults={"temperature": 0.0, "top_p": 1.0, "max_tokens": control.max_tokens},
            request_limits={"max_tokens": control.max_tokens},
            soft_hints={},
            require_json_mode=bool(control.require_json_mode),
        )
        endpoint = f"{entry.base_url.rstrip('/')}/{entry.endpoint.lstrip('/')}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = _open_response(request, float(control.timeout_seconds))
        except urllib.error.HTTPError as exc:
            response = exc
        raw = response.read(RESPONSE_MAX_BYTES + 1)
        status = int(getattr(response, "status", getattr(response, "code", 200)))
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_transport_error", str(exc), status,
        )
    except (TypeError, ValueError) as exc:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_configuration_invalid", str(exc), status,
        )
    except Exception as exc:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_transport_error", str(exc), status,
        )
    finally:
        if response is not None:
            response.close()

    if raw is None:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_transport_error", "provider response was absent", status,
        )
    if len(raw) > RESPONSE_MAX_BYTES:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_response_too_large", f"provider response exceeds {RESPONSE_MAX_BYTES} bytes", status,
        )

    text = raw.decode("utf-8", errors="replace")
    try:
        body_value = json.loads(text)
    except json.JSONDecodeError:
        store.write_response(
            context.call_key,
            {
                "http_status": status,
                "raw_text": text,
                "provider_latency_ms": _elapsed_ms(started_ns),
            },
        )
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_envelope_invalid", "provider response is not valid JSON", status,
        )
    store.write_response(
        context.call_key,
        {
            "http_status": status,
            "body": body_value,
            "provider_latency_ms": _elapsed_ms(started_ns),
        },
    )
    if int(status or 0) < 400 and not _content_is_string(body_value):
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_envelope_invalid", "assistant message content must be a string", status,
        )

    parser_response = _ParserResponse(status_code=int(status or 0), body=body_value, text=text)
    try:
        parsed = (
            parse_deepseek_response(parser_response)
            if entry.provider_family == "deepseek"
            else parse_siliconflow_response(parser_response)
        )
    except Exception as exc:
        error_kind = getattr(exc, "error_kind", None)
        projected_kind = (
            str(error_kind)
            if error_kind and error_kind != "invalid_output"
            else "provider_envelope_invalid"
        )
        return _finish_failure(
            store, context, entry, started, started_ns,
            projected_kind, str(exc), status,
        )
    if parsed.response_model_status != "present" or parsed.resolved_model != entry.configured_model:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_model_mismatch", "provider resolved model differs from configured model", status,
            raw_response=body_value, resolved_model=parsed.resolved_model,
        )
    try:
        usage, usage_status = _usage(parsed.usage)
    except ValueError as exc:
        return _finish_failure(
            store, context, entry, started, started_ns,
            "provider_usage_invalid", str(exc), status,
            raw_response=body_value, resolved_model=parsed.resolved_model,
        )
    result = ProviderCallResultV1(
        ok=True,
        content_text=parsed.content_text,
        reasoning_content=getattr(parsed, "reasoning_content", None),
        raw_response_json=body_value,
        **usage,
        provider_request_started_at_utc=started,
        provider_latency_ms=_elapsed_ms(started_ns),
        configured_model=entry.configured_model,
        requested_model=entry.configured_model,
        resolved_model=parsed.resolved_model,
        provider_response_id=parsed.provider_response_id,
        finish_reason=parsed.finish_reason,
        http_status=status,
        error_kind=None,
        error_message=None,
        usage_status=usage_status,
    )
    result.validate()
    store.write_call_terminal(context.call_key, _terminal(store, context, result))
    return result


def _content_is_string(body: Any) -> bool:
    try:
        return isinstance(body["choices"][0]["message"]["content"], str)
    except (KeyError, IndexError, TypeError):
        return False


def _usage(value: Any) -> tuple[dict[str, int | None], str]:
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    empty = {
        "prompt_tokens": None,
        "prompt_cache_hit_tokens": None,
        "prompt_cache_miss_tokens": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }
    if value is None:
        return empty, "usage_missing"
    if not isinstance(value, dict):
        raise ValueError("provider usage must be an object")
    for name in names:
        token = value.get(name)
        if isinstance(token, bool) or not isinstance(token, int) or token < 0:
            raise ValueError(f"provider usage {name} must be a natural integer")
    hit = value.get("prompt_cache_hit_tokens")
    miss = value.get("prompt_cache_miss_tokens")
    details = value.get("prompt_tokens_details")
    if isinstance(details, dict):
        hit = details.get("cached_tokens", hit)
        miss = details.get("cache_miss_tokens", miss)
    completion_details = value.get("completion_tokens_details")
    reasoning = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    for name, token in (("prompt_cache_hit_tokens", hit), ("prompt_cache_miss_tokens", miss), ("reasoning_tokens", reasoning)):
        if token is not None and (isinstance(token, bool) or not isinstance(token, int) or token < 0):
            raise ValueError(f"provider usage {name} must be a natural integer")
    if reasoning is not None and reasoning > value["completion_tokens"]:
        raise ValueError("reasoning_tokens must be a completion subset")
    return {
        "prompt_tokens": value["prompt_tokens"],
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "completion_tokens": value["completion_tokens"],
        "reasoning_tokens": reasoning,
        "total_tokens": value["total_tokens"],
    }, "usage_complete"


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (monotonic_ns() - started_ns) // 1_000_000)


def _finish_failure(
    store: RunStore,
    context: ProviderCallContextV1,
    entry: ProviderEntryViewV1,
    started: str,
    started_ns: int,
    kind: str,
    message: str,
    status: int | None,
    *,
    raw_response: Any | None = None,
    resolved_model: str | None = None,
) -> ProviderCallResultV1:
    result = ProviderCallResultV1(
        ok=False,
        content_text=None,
        reasoning_content=None,
        raw_response_json=raw_response,
        prompt_tokens=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        provider_request_started_at_utc=started,
        provider_latency_ms=_elapsed_ms(started_ns),
        configured_model=entry.configured_model,
        requested_model=entry.configured_model,
        resolved_model=resolved_model,
        provider_response_id=None,
        finish_reason=None,
        http_status=status,
        error_kind=kind,
        error_message=message,
        usage_status="usage_unavailable",
    )
    result.validate()
    store.write_call_terminal(context.call_key, _terminal(store, context, result))
    return result


def _recovered_failure(
    *,
    entry: ProviderEntryViewV1,
    started: str,
    latency_ms: int | None,
    kind: str,
    message: str,
    status: int | None,
    raw_response: Any | None = None,
    resolved_model: str | None = None,
) -> ProviderCallResultV1:
    result = ProviderCallResultV1(
        ok=False,
        content_text=None,
        reasoning_content=None,
        raw_response_json=raw_response,
        prompt_tokens=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        provider_request_started_at_utc=started,
        provider_latency_ms=latency_ms,
        configured_model=entry.configured_model,
        requested_model=entry.configured_model,
        resolved_model=resolved_model,
        provider_response_id=None,
        finish_reason=None,
        http_status=status,
        error_kind=kind,
        error_message=message,
        usage_status="usage_unavailable",
    )
    result.validate()
    return result


def _pid_is_alive(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _intent_matches(
    intent: dict[str, Any],
    *,
    entry: ProviderEntryViewV1,
    context: ProviderCallContextV1,
) -> bool:
    return (
        intent.get("call_key") == context.call_key
        and intent.get("root_key") == list(context.root_key)
        and intent.get("planned_ai_unit_id") == context.planned_ai_unit_id
        and intent.get("attempt_ordinal") == context.attempt_ordinal
        and intent.get("provider_family") == entry.provider_family
        and intent.get("provider_entry_id") == entry.entry_id
        and intent.get("configured_model") == entry.configured_model
        and isinstance(intent.get("provider_request_started_at_utc"), str)
    )


def _recover_response_result(
    *,
    entry: ProviderEntryViewV1,
    started: str,
    response: dict[str, Any],
) -> ProviderCallResultV1:
    status = response.get("http_status")
    latency = response.get("provider_latency_ms")
    if isinstance(status, bool) or not isinstance(status, int):
        raise ValueError("stored provider response lacks integer http_status")
    if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
        raise ValueError("stored provider response lacks natural provider_latency_ms")
    if "body" not in response:
        return _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=latency,
            kind="provider_envelope_invalid",
            message="stored provider response is not valid JSON",
            status=status,
        )
    body = response["body"]
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if status < 400 and not _content_is_string(body):
        return _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=latency,
            kind="provider_envelope_invalid",
            message="assistant message content must be a string",
            status=status,
        )
    envelope = _ParserResponse(status_code=status, body=body, text=text)
    try:
        parsed = (
            parse_deepseek_response(envelope)
            if entry.provider_family == "deepseek"
            else parse_siliconflow_response(envelope)
        )
    except Exception as exc:
        error_kind = getattr(exc, "error_kind", None)
        kind = (
            str(error_kind)
            if error_kind and error_kind != "invalid_output"
            else "provider_envelope_invalid"
        )
        return _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=latency,
            kind=kind,
            message=str(exc),
            status=status,
        )
    if (
        parsed.response_model_status != "present"
        or parsed.resolved_model != entry.configured_model
    ):
        return _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=latency,
            kind="provider_model_mismatch",
            message="provider resolved model differs from configured model",
            status=status,
            raw_response=body,
            resolved_model=parsed.resolved_model,
        )
    try:
        usage, usage_status = _usage(parsed.usage)
    except ValueError as exc:
        return _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=latency,
            kind="provider_usage_invalid",
            message=str(exc),
            status=status,
            raw_response=body,
            resolved_model=parsed.resolved_model,
        )
    result = ProviderCallResultV1(
        ok=True,
        content_text=parsed.content_text,
        reasoning_content=getattr(parsed, "reasoning_content", None),
        raw_response_json=body,
        **usage,
        provider_request_started_at_utc=started,
        provider_latency_ms=latency,
        configured_model=entry.configured_model,
        requested_model=entry.configured_model,
        resolved_model=parsed.resolved_model,
        provider_response_id=parsed.provider_response_id,
        finish_reason=parsed.finish_reason,
        http_status=status,
        error_kind=None,
        error_message=None,
        usage_status=usage_status,
    )
    result.validate()
    return result


def recover_interrupted_call(
    entry: ProviderEntryViewV1,
    context: ProviderCallContextV1,
    store: RunStore,
) -> ProviderCallResultV1:
    """只读既有 intent/response，绝不发送 provider 请求。"""

    entry.validate()
    context.validate()
    intent = store.read_call_intent(context.call_key)
    if not _intent_matches(intent, entry=entry, context=context):
        raise StorageConflictError("provider intent identity/configuration differs")
    if _pid_is_alive(intent.get("owner_pid")):
        raise StorageConflictError(
            f"provider call intent has an active owner: {context.call_key}"
        )
    started = str(intent["provider_request_started_at_utc"])
    if store.response_path(context.call_key).is_file():
        result = _recover_response_result(
            entry=entry,
            started=started,
            response=store.read_response(context.call_key),
        )
    else:
        result = _recovered_failure(
            entry=entry,
            started=started,
            latency_ms=None,
            kind="unknown_transport_outcome",
            message="provider intent owner ended before a response was persisted",
            status=None,
        )
    store.write_call_terminal(
        context.call_key,
        _terminal(store, context, result),
    )
    return result


def _terminal(
    store: RunStore,
    context: ProviderCallContextV1,
    result: ProviderCallResultV1,
) -> dict[str, Any]:
    projected = asdict(result)
    projected["raw_response_json"] = None
    return {
        "schema_version": "slim_v2.provider_terminal.v1",
        "call_key": context.call_key,
        "root_key": list(context.root_key),
        "planned_ai_unit_id": context.planned_ai_unit_id,
        "attempt_ordinal": context.attempt_ordinal,
        "ok": result.ok,
        "error_kind": result.error_kind,
        "provider_call_made": result.error_kind != "provider_configuration_invalid",
        "http_status": result.http_status,
        "resolved_model": result.resolved_model,
        "usage_status": result.usage_status,
        "response_relative_path": (
            str(store.response_path(context.call_key).relative_to(store.run_dir))
            if result.raw_response_json is not None
            else None
        ),
        "result": projected,
    }


__all__ = [
    "CostProjectionV1",
    "ProviderCallContextV1",
    "RESPONSE_MAX_BYTES",
    "call_provider_once",
    "project_cost",
    "recover_interrupted_call",
]
