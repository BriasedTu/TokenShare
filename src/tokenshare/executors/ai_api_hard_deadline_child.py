"""`ai_api_hard_deadline` 的无日志单调用 child 入口。"""

from __future__ import annotations

import base64
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from time import monotonic_ns, perf_counter, sleep

from tokenshare.core.models import JsonObject
from tokenshare.executors.ai_api_hard_deadline import HARD_DEADLINE_API_KEY_ENV
from tokenshare.executors.ai_api_transport import (
    DeepSeekProviderError,
    OpenAIProviderError,
    SiliconFlowProviderError,
    UrlLibDeepSeekTransport,
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4:
        return 2
    request_path, result_path, ready_path, gate_path = (
        Path(value) for value in arguments
    )
    api_key = ""
    try:
        request = _read_request(request_path)
        request_file_sha256 = _file_sha256(request_path)
        api_key = os.environ.pop(HARD_DEADLINE_API_KEY_ENV, "")
        if not api_key:
            raise ValueError("hard-deadline child API key is unavailable")
        provider_family = str(request["provider_family"])
        transport = _transport(provider_family)
        body_bytes = base64.b64decode(
            str(request["body_bytes_base64"]), validate=True
        )
        _write_json_atomic(
            ready_path,
            {
                "schema_version": "tokenshare.ai_api_hard_deadline_ready.v1",
                "request_file_sha256": request_file_sha256,
            },
        )
        while not gate_path.is_file():
            sleep(0.005)
    except BaseException:
        _write_json_atomic(
            result_path,
            {
                "schema_version": "tokenshare.ai_api_hard_deadline_result.v1",
                "status": "pre_dispatch_failure",
                "failure_kind": "executor_error",
                "error_message": "provider child failed before transport dispatch",
                "http_status": None,
                "provider_latency_ms": None,
                "completed_monotonic_ns": monotonic_ns(),
            },
        )
        return 3
    started = perf_counter()
    try:
        response = transport.post_chat_completion(
            api_key=api_key,
            body_bytes=body_bytes,
            normalized_absolute_endpoint=str(
                request["normalized_absolute_endpoint"]
            ),
            content_type=str(request["content_type"]),
            timeout_seconds=float(request["socket_timeout_seconds"]),
        )
        result: JsonObject = {
            "schema_version": "tokenshare.ai_api_hard_deadline_result.v1",
            "status": "completed",
            "status_code": int(response.status_code),
            "body": dict(response.body),
            "text": str(response.text),
            "provider_latency_ms": max(0, int((perf_counter() - started) * 1000)),
            "completed_monotonic_ns": monotonic_ns(),
        }
    except TimeoutError:
        result = _failure(
            failure_kind="timeout",
            message="provider request timed out",
            http_status=None,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
        )
    except OSError:
        result = _failure(
            failure_kind="connection_error",
            message="provider connection failed",
            http_status=None,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
        )
    except (SiliconFlowProviderError, OpenAIProviderError, DeepSeekProviderError) as error:
        result = _failure(
            failure_kind=error.error_kind,
            message=_redact(error.message, api_key),
            http_status=error.http_status,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
        )
    except BaseException:
        result = _failure(
            failure_kind="provider_error",
            message="provider child failed without reusable exception text",
            http_status=None,
            latency_ms=None,
        )
    del api_key
    _write_json_atomic(result_path, result)
    return 0


def _read_request(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if type(body) is not dict:
        raise ValueError("hard-deadline child request must be an object")
    if body.get("schema_version") != "tokenshare.ai_api_hard_deadline_request.v1":
        raise ValueError("hard-deadline child request schema drift")
    return body


def _transport(provider_family: str):
    transports = {
        "deepseek": UrlLibDeepSeekTransport,
        "openai": UrlLibOpenAITransport,
        "siliconflow": UrlLibSiliconFlowTransport,
    }
    try:
        return transports[provider_family]()
    except KeyError as exc:
        raise ValueError("unsupported hard-deadline child provider") from exc


def _failure(
    *,
    failure_kind: str,
    message: str,
    http_status: int | None,
    latency_ms: int | None,
) -> JsonObject:
    return {
        "schema_version": "tokenshare.ai_api_hard_deadline_result.v1",
        "status": "provider_failure",
        "failure_kind": failure_kind,
        "error_message": message[:500],
        "http_status": http_status,
        "provider_latency_ms": latency_ms,
        "completed_monotonic_ns": monotonic_ns(),
    }


def _write_json_atomic(path: Path, body: JsonObject) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(body, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _redact(message: str, secret: str) -> str:
    return message.replace(secret, "[REDACTED_API_KEY]") if secret else message


def _file_sha256(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


if __name__ == "__main__":
    raise SystemExit(main())
