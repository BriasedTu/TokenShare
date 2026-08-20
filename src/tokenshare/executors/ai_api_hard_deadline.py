"""AI provider 单次调用的可终止硬总时限边界。"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic_ns, sleep
from typing import Literal

from tokenshare.core.models import JsonObject


HARD_DEADLINE_API_KEY_ENV = "TOKENSHARE_HARD_DEADLINE_API_KEY"
HARD_DEADLINE_PROOF_SCHEMA = "tokenshare.ai_api_hard_deadline_quiescence.v1"


@dataclass(frozen=True, kw_only=True)
class HardDeadlineTransportResponse:
    """子进程返回、由父进程唯一消费的临时 transport response。"""

    status_code: int
    body: JsonObject
    text: str


@dataclass(frozen=True, kw_only=True)
class HardDeadlineDispatchOutcome:
    """单次 child dispatch 的结果与父进程 quiescence 证明。"""

    status: Literal[
        "completed", "provider_failure", "no_response", "pre_dispatch_failure"
    ]
    response: HardDeadlineTransportResponse | None
    failure_kind: str | None
    provider_latency_ms: int | None
    http_status: int | None
    error_message: str | None
    transport_call_count: int
    observed_wall_clock_ms: int
    quiescence_evidence: JsonObject


class HardDeadlineDispatchSession:
    """先证明 child 可进入 transport，再由 exact transport 边界放行一次调用。"""

    def __init__(
        self,
        *,
        provider_family: str,
        api_key: str,
        body_bytes: bytes,
        normalized_absolute_endpoint: str,
        content_type: str,
        hard_total_seconds: float,
    ) -> None:
        self.provider_family = provider_family
        self._api_key = api_key
        self._hard_total_seconds = float(hard_total_seconds)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="tokenshare-ai-hard-deadline-"
        )
        work_root = Path(self._temporary.name)
        self._request_path = work_root / "request.v1.json"
        self._ready_path = work_root / "network-start-ready.v1.json"
        self._gate_path = work_root / "network-start.gate.v1.json"
        self._result_path = work_root / "result.v1.json"
        self._started_ns = monotonic_ns()
        self._deadline_ns = self._started_ns + int(
            self._hard_total_seconds * 1_000_000_000
        )
        request_body = {
            "schema_version": "tokenshare.ai_api_hard_deadline_request.v1",
            "provider_family": provider_family,
            "body_bytes_base64": base64.b64encode(body_bytes).decode("ascii"),
            "normalized_absolute_endpoint": normalized_absolute_endpoint,
            "content_type": content_type,
            "parent_deadline_monotonic_ns": self._deadline_ns,
            # socket timeout 只作下层故障边界；父进程的总截止更早且拥有权威。
            "socket_timeout_seconds": max(
                self._hard_total_seconds + 5.0,
                self._hard_total_seconds * 2.0,
            ),
        }
        _write_json_atomic(self._request_path, request_body)
        self._request_file_sha256 = _file_sha256(self._request_path)
        child_env = _minimal_child_environment(api_key)
        arguments = (
            sys.executable,
            "-m",
            "tokenshare.executors.ai_api_hard_deadline_child",
            str(self._request_path),
            str(self._result_path),
            str(self._ready_path),
            str(self._gate_path),
        )
        self._process = subprocess.Popen(
            arguments,
            cwd=str(Path(__file__).resolve().parents[3]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=child_env,
        )
        self._terminate_attempted = False
        self._kill_attempted = False
        self._dispatched = False
        self._closed = False
        self.pre_dispatch_outcome = self._await_ready_or_pre_dispatch_failure()

    def dispatch_for_transport(
        self, provider_family: str
    ) -> HardDeadlineDispatchOutcome:
        """由已经被 accounting wrapper 观察到的 exact transport 调用一次。"""

        if provider_family != self.provider_family:
            raise ValueError("hard-deadline transport provider identity drift")
        if self.pre_dispatch_outcome is not None:
            raise RuntimeError("hard-deadline child did not reach transport boundary")
        if self._dispatched:
            raise RuntimeError("hard-deadline session cannot be dispatched twice")
        self._dispatched = True
        _write_json_atomic(
            self._gate_path,
            {
                "schema_version": "tokenshare.ai_api_hard_deadline_gate.v1",
                "request_file_sha256": self._request_file_sha256,
            },
        )
        result_observed_before_deadline = False
        while True:
            if self._result_path.is_file():
                observed_ns = monotonic_ns()
                if observed_ns <= self._deadline_ns:
                    result_observed_before_deadline = True
                break
            if self._process.poll() is not None:
                # child 可能在上一次 path 检查与 poll 之间原子提交后退出。
                if self._result_path.is_file():
                    result_observed_before_deadline = (
                        monotonic_ns() <= self._deadline_ns
                    )
                break
            now_ns = monotonic_ns()
            if now_ns >= self._deadline_ns:
                break
            sleep(min(0.01, (self._deadline_ns - now_ns) / 1_000_000_000))

        if not result_observed_before_deadline:
            self._terminate_and_reap()
        else:
            self._reap()
        result_body = (
            _read_result(self._result_path)
            if self._result_path.is_file()
            else None
        )
        completed_monotonic_ns = (
            None
            if result_body is None
            else _required_nonnegative_int(
                result_body.get("completed_monotonic_ns"),
                "completed_monotonic_ns",
            )
        )
        child_completed_before_parent_deadline = (
            completed_monotonic_ns is not None
            and completed_monotonic_ns <= self._deadline_ns
        )
        result_commit_count = 1 if result_body is not None else 0
        late_result_rejected = (
            result_body is not None
            and (
                not result_observed_before_deadline
                or not child_completed_before_parent_deadline
            )
        )
        accepted_result = (
            result_body
            if result_observed_before_deadline
            and child_completed_before_parent_deadline
            else None
        )
        if late_result_rejected:
            self._result_path.unlink(missing_ok=True)
        proof = self._proof(
            network_start_acknowledged=True,
            result_observed_before_deadline=result_observed_before_deadline,
            result_commit_count=result_commit_count,
            accepted_result_commit_count=(1 if accepted_result is not None else 0),
            late_result_rejected=late_result_rejected,
        )
        proof["child_completed_before_parent_deadline"] = (
            child_completed_before_parent_deadline
        )
        if accepted_result is None:
            return HardDeadlineDispatchOutcome(
                status="no_response",
                response=None,
                failure_kind="no_response",
                provider_latency_ms=None,
                http_status=None,
                error_message="provider child exceeded the hard total deadline",
                transport_call_count=1,
                observed_wall_clock_ms=int(proof["observed_wall_clock_ms"]),
                quiescence_evidence=proof,
            )
        return _outcome_from_result(
            accepted_result,
            api_key=self._api_key,
            transport_call_count=1,
            proof=proof,
        )

    def close(self) -> None:
        if self._closed:
            return
        if self._process.poll() is None:
            self._terminate_and_reap()
        self._temporary.cleanup()
        self._closed = True

    def _await_ready_or_pre_dispatch_failure(
        self,
    ) -> HardDeadlineDispatchOutcome | None:
        ready_observed_before_deadline = False
        result_observed_before_deadline = False
        while True:
            if self._ready_path.is_file():
                if monotonic_ns() <= self._deadline_ns:
                    ready_observed_before_deadline = True
                break
            if self._result_path.is_file():
                if monotonic_ns() <= self._deadline_ns:
                    result_observed_before_deadline = True
                break
            if self._process.poll() is not None:
                if self._result_path.is_file():
                    result_observed_before_deadline = (
                        monotonic_ns() <= self._deadline_ns
                    )
                break
            now_ns = monotonic_ns()
            if now_ns >= self._deadline_ns:
                break
            sleep(min(0.01, (self._deadline_ns - now_ns) / 1_000_000_000))
        if ready_observed_before_deadline:
            ready = _read_ready(self._ready_path)
            if ready.get("request_file_sha256") != self._request_file_sha256:
                self._terminate_and_reap()
                raise ValueError("hard-deadline ready request digest drift")
            return None

        self._terminate_and_reap()
        result_body = (
            _read_result(self._result_path)
            if self._result_path.is_file()
            else None
        )
        completed_monotonic_ns = (
            None
            if result_body is None
            else _required_nonnegative_int(
                result_body.get("completed_monotonic_ns"),
                "completed_monotonic_ns",
            )
        )
        child_completed_before_parent_deadline = (
            completed_monotonic_ns is not None
            and completed_monotonic_ns <= self._deadline_ns
        )
        accepted_result = (
            result_body
            if result_observed_before_deadline
            and child_completed_before_parent_deadline
            else None
        )
        late_result_rejected = result_body is not None and accepted_result is None
        if late_result_rejected:
            self._result_path.unlink(missing_ok=True)
        message = "provider child failed before the transport boundary"
        if (
            accepted_result is not None
            and accepted_result.get("status") == "pre_dispatch_failure"
        ):
            candidate = accepted_result.get("error_message")
            if isinstance(candidate, str) and candidate:
                message = candidate
        proof = self._proof(
            network_start_acknowledged=False,
            result_observed_before_deadline=result_observed_before_deadline,
            result_commit_count=(1 if result_body is not None else 0),
            accepted_result_commit_count=(1 if accepted_result is not None else 0),
            late_result_rejected=late_result_rejected,
        )
        proof["child_completed_before_parent_deadline"] = (
            child_completed_before_parent_deadline
        )
        return HardDeadlineDispatchOutcome(
            status="pre_dispatch_failure",
            response=None,
            failure_kind="executor_error",
            provider_latency_ms=None,
            http_status=None,
            error_message=_redact(message, self._api_key),
            transport_call_count=0,
            observed_wall_clock_ms=int(proof["observed_wall_clock_ms"]),
            quiescence_evidence=proof,
        )

    def _terminate_and_reap(self) -> None:
        if self._process.poll() is None:
            self._terminate_attempted = True
            self._process.terminate()
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._kill_attempted = True
                self._process.kill()
                self._process.wait(timeout=2.0)
        self._reap()

    def _reap(self) -> None:
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._terminate_attempted = True
                self._process.terminate()
                try:
                    self._process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._kill_attempted = True
                    self._process.kill()
                    self._process.wait(timeout=2.0)
        if self._process.poll() is None:
            raise RuntimeError("hard-deadline child could not be reaped")

    def _proof(
        self,
        *,
        network_start_acknowledged: bool,
        result_observed_before_deadline: bool,
        result_commit_count: int,
        accepted_result_commit_count: int,
        late_result_rejected: bool,
    ) -> JsonObject:
        if not _secret_absent_from_files(
            self._api_key,
            self._request_path,
            self._ready_path,
            self._gate_path,
            self._result_path,
        ):
            raise RuntimeError("hard-deadline ephemeral file contains API secret")
        return {
            "schema_version": HARD_DEADLINE_PROOF_SCHEMA,
            "deadline_enforced": True,
            "hard_total_seconds": self._hard_total_seconds,
            "observed_wall_clock_ms": max(
                0, int((monotonic_ns() - self._started_ns) / 1_000_000)
            ),
            "child_pid": int(self._process.pid),
            "child_exit_code": int(self._process.returncode),
            "terminate_attempted": self._terminate_attempted,
            "kill_attempted": self._kill_attempted,
            "child_reaped": True,
            "network_start_acknowledged": network_start_acknowledged,
            "result_observed_before_deadline": result_observed_before_deadline,
            "result_commit_count": result_commit_count,
            "accepted_result_commit_count": accepted_result_commit_count,
            "late_result_rejected": late_result_rejected,
            # 已接受的是截止前观察值；被拒的 late file 会在 proof 前移除。
            "post_reap_late_result_absent": True,
            "parent_only_evidence_consumer": True,
            "ephemeral_files_secret_free": True,
            "request_file_sha256": self._request_file_sha256,
        }


def prepare_provider_transport_hard_deadline_session(
    *,
    provider_family: str,
    api_key: str,
    body_bytes: bytes,
    normalized_absolute_endpoint: str,
    content_type: str,
    hard_total_seconds: float,
) -> HardDeadlineDispatchSession:
    """启动 child 并只等待 secret/request/transport bootstrap 的原子 ready ack。"""

    if provider_family not in {"deepseek", "siliconflow", "openai"}:
        raise ValueError("unsupported hard-deadline provider family")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("hard-deadline API key must be non-empty")
    if not isinstance(body_bytes, bytes):
        raise TypeError("hard-deadline body_bytes must be bytes")
    if not normalized_absolute_endpoint or not content_type:
        raise ValueError("hard-deadline request identity is incomplete")
    if isinstance(hard_total_seconds, bool) or hard_total_seconds <= 0:
        raise ValueError("hard_total_seconds must be positive")
    return HardDeadlineDispatchSession(
        provider_family=provider_family,
        api_key=api_key,
        body_bytes=body_bytes,
        normalized_absolute_endpoint=normalized_absolute_endpoint,
        content_type=content_type,
        hard_total_seconds=hard_total_seconds,
    )


def _minimal_child_environment(api_key: str) -> dict[str, str]:
    allowed_names = (
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "PYTHONHOME",
        "PYTHONPATH",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    )
    environment = {
        name: os.environ[name]
        for name in allowed_names
        if name in os.environ
    }
    source_root = str(Path(__file__).resolve().parents[2])
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, existing_pythonpath) if value
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment[HARD_DEADLINE_API_KEY_ENV] = api_key
    return environment


def _write_json_atomic(path: Path, body: JsonObject) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(body, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_result(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if type(body) is not dict:
        raise ValueError("hard-deadline result must be a JSON object")
    if body.get("schema_version") != "tokenshare.ai_api_hard_deadline_result.v1":
        raise ValueError("hard-deadline result schema drift")
    return body


def _read_ready(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if type(body) is not dict:
        raise ValueError("hard-deadline ready ack must be a JSON object")
    if body.get("schema_version") != "tokenshare.ai_api_hard_deadline_ready.v1":
        raise ValueError("hard-deadline ready ack schema drift")
    return body


def _file_sha256(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _outcome_from_result(
    result_body: JsonObject,
    *,
    api_key: str,
    transport_call_count: int,
    proof: JsonObject,
) -> HardDeadlineDispatchOutcome:
    status = result_body.get("status")
    provider_latency_ms = _optional_nonnegative_int(
        result_body.get("provider_latency_ms"), "provider_latency_ms"
    )
    observed_wall_clock_ms = _required_nonnegative_int(
        proof.get("observed_wall_clock_ms"), "observed_wall_clock_ms"
    )
    if status == "completed":
        response_body = result_body.get("body")
        if type(response_body) is not dict:
            raise ValueError("hard-deadline response body is invalid")
        status_code = _required_nonnegative_int(
            result_body.get("status_code"), "status_code"
        )
        text = result_body.get("text")
        if not isinstance(text, str):
            raise ValueError("hard-deadline response text is invalid")
        return HardDeadlineDispatchOutcome(
            status="completed",
            response=HardDeadlineTransportResponse(
                status_code=status_code,
                body=dict(response_body),
                text=text,
            ),
            failure_kind=None,
            provider_latency_ms=provider_latency_ms,
            http_status=status_code,
            error_message=None,
            transport_call_count=transport_call_count,
            observed_wall_clock_ms=observed_wall_clock_ms,
            quiescence_evidence=proof,
        )
    if status != "provider_failure":
        raise ValueError("hard-deadline child status is invalid")
    failure_kind = result_body.get("failure_kind")
    message = result_body.get("error_message")
    if not isinstance(failure_kind, str) or not failure_kind:
        raise ValueError("hard-deadline failure kind is invalid")
    if not isinstance(message, str) or not message:
        raise ValueError("hard-deadline failure message is invalid")
    return HardDeadlineDispatchOutcome(
        status="provider_failure",
        response=None,
        failure_kind=failure_kind,
        provider_latency_ms=provider_latency_ms,
        http_status=_optional_nonnegative_int(
            result_body.get("http_status"), "http_status"
        ),
        error_message=_redact(message, api_key),
        transport_call_count=transport_call_count,
        observed_wall_clock_ms=observed_wall_clock_ms,
        quiescence_evidence=proof,
    )


def _secret_absent_from_files(secret: str, *paths: Path) -> bool:
    encoded = secret.encode("utf-8")
    return all(not path.is_file() or encoded not in path.read_bytes() for path in paths)


def _required_nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"hard-deadline {name} is invalid")
    return value


def _optional_nonnegative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _required_nonnegative_int(value, name)


def _redact(message: str, secret: str) -> str:
    return message.replace(secret, "[REDACTED_API_KEY]") if secret else message


__all__ = [
    "HARD_DEADLINE_API_KEY_ENV",
    "HARD_DEADLINE_PROOF_SCHEMA",
    "HardDeadlineDispatchOutcome",
    "HardDeadlineTransportResponse",
    "prepare_provider_transport_hard_deadline_session",
]
