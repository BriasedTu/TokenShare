"""Subprocess bridge for checking Lean proof artifacts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.executors.contracts import EnvironmentRef
from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload, canonical_json_digest
from tokenshare.plugins.lean_proof.schemas import (
    LEAN_CHECKER_REPORT_SCHEMA_VERSION,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
)
from tokenshare.storage.artifacts import ArtifactStore


class LeanCheckerMode(str, Enum):
    DIRECT_PROOF = "direct_proof"
    CHILD_PROOF = "child_proof"
    MERGE_PROOF = "merge_proof"


class LeanCheckerStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ENVIRONMENT_ERROR = "environment_error"
    HELPER_ERROR = "helper_error"


_PROOF_CANDIDATE_REQUIRED_FIELDS = (
    "schema_version",
    "proof_candidate_id",
    "theorem_payload_digest",
    "proof_source",
    "created_at",
)
_FORBIDDEN_PROOF_PLACEHOLDER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_'.])(?P<placeholder>sorry|admit)(?![A-Za-z0-9_'.])"
)
_LEAN_ENVIRONMENT_ALLOWLIST = (
    "ELAN_HOME",
    "LAKE_HOME",
    "LEAN_AR",
    "LEAN_CC",
    "LEAN_CXX",
    "LEAN_PATH",
    "LEAN_SRC_PATH",
    "LEAN_SYSROOT",
    "PATH",
)
_STALE_LAKE_CONFIGURATION_ERROR = (
    "compiled configuration is invalid; run with '-R' to reconfigure"
)
_PREPARED_LEAN_ENVIRONMENT_CACHE: dict[tuple[str, str, str, str], dict[str, str]] = {}


class LeanEnvironmentBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeanCheckerRequest:
    request_id: str
    theorem_payload_ref: ArtifactRef
    proof_candidate_ref: ArtifactRef
    environment_ref: EnvironmentRef
    checker_mode: LeanCheckerMode
    timeout_seconds: int
    max_output_bytes: int
    created_at: str
    schema_version: str = "lean_proof.checker_request.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "theorem_payload_ref": self.theorem_payload_ref.to_dict(),
            "proof_candidate_ref": self.proof_candidate_ref.to_dict(),
            "environment_ref": self.environment_ref.to_dict(),
            "checker_mode": self.checker_mode.value,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class LeanCheckerReport:
    report_id: str
    request_id: str
    status: LeanCheckerStatus
    exit_code: int | None
    stdout_ref: ArtifactRef | None
    stderr_ref: ArtifactRef | None
    generated_source_ref: ArtifactRef | None
    proof_artifact_ref: ArtifactRef | None
    report_ref: ArtifactRef | None
    diagnostics: JsonObject
    normalized_theorem_digest: str
    proof_digest: str | None
    environment_ref: EnvironmentRef
    command_summary: JsonObject
    duration_ms: int
    schema_version: str = LEAN_CHECKER_REPORT_SCHEMA_VERSION

    def to_dict(self, *, include_report_ref: bool = True) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout_ref": _json_value(self.stdout_ref),
            "stderr_ref": _json_value(self.stderr_ref),
            "generated_source_ref": _json_value(self.generated_source_ref),
            "proof_artifact_ref": _json_value(self.proof_artifact_ref),
            "diagnostics": _json_value(self.diagnostics),
            "normalized_theorem_digest": self.normalized_theorem_digest,
            "proof_digest": self.proof_digest,
            "environment_ref": self.environment_ref.to_dict(),
            "command_summary": _json_value(self.command_summary),
            "duration_ms": self.duration_ms,
        }
        if include_report_ref:
            body["report_ref"] = _json_value(self.report_ref)
        return body


class LeanChecker(Protocol):
    def __call__(
        self,
        request: LeanCheckerRequest,
        *,
        artifact_store: ArtifactStore,
        environment_manifest: LeanEnvironmentManifest,
    ) -> LeanCheckerReport: ...


def check_lean_proof(
    request: LeanCheckerRequest,
    *,
    artifact_store: ArtifactStore,
    environment_manifest: LeanEnvironmentManifest,
) -> LeanCheckerReport:
    _verify_environment_matches_request(request, environment_manifest)
    theorem_payload = LeanTheoremPayload.from_dict(
        json.loads(artifact_store.read_bytes(request.theorem_payload_ref).decode("utf-8"))
    )
    proof_body = _load_json_object(
        artifact_store.read_bytes(request.proof_candidate_ref).decode("utf-8"),
        artifact_name="proof candidate artifact",
    )
    proof_source = _validated_proof_source(proof_body, theorem_payload=theorem_payload)
    forbidden_placeholder = _forbidden_proof_placeholder(proof_source)

    generated_source = render_lean_source(theorem_payload, proof_source)
    normalized_theorem_digest = canonical_json_digest(
        {
            "theorem_name": theorem_payload.theorem_name,
            "imports": theorem_payload.imports,
            "namespace": theorem_payload.namespace,
            "parameters_source": theorem_payload.parameters_source,
            "statement_source": theorem_payload.statement_source,
        }
    )
    proof_digest = canonical_json_digest(
        {
            "theorem_payload_digest": theorem_payload.payload_digest,
            "proof_source": proof_source,
        }
    )

    generated_source_ref = artifact_store.save_bytes(
        generated_source.encode("utf-8"),
        artifact_id=_artifact_id(request.request_id, "generated_source.lean"),
        artifact_type="LeanGeneratedSource",
        media_type="text/x-lean",
        artifact_schema_id="lean_proof.generated_source",
        artifact_schema_version="v1",
        source={"kind": "lean_checker", "request_id": request.request_id},
        metadata={"checker_mode": request.checker_mode.value},
        created_at=request.created_at,
    )

    started = monotonic()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    failure_kind: str | None = None
    failure_message: str | None = None
    status: LeanCheckerStatus
    command = [
        environment_manifest.lean_executable,
        str(_temporary_source_path(request.request_id)),
    ]
    try:
        prepared_environment = prepared_lean_environment(environment_manifest)
        checker_environment = dict(os.environ)
        checker_environment.update(prepared_environment)
        with tempfile.TemporaryDirectory(prefix="tokenshare_lean_") as temp_dir:
            source_path = Path(temp_dir) / "TokenShareGeneratedCheck.lean"
            source_path.write_text(generated_source, encoding="utf-8")
            command[-1] = str(source_path)
            completed = subprocess.run(
                command,
                cwd=environment_manifest.project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=request.timeout_seconds,
                env=checker_environment,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout[: request.max_output_bytes]
            stderr = completed.stderr[: request.max_output_bytes]
            status = (
                LeanCheckerStatus.ACCEPTED
                if completed.returncode == 0
                else LeanCheckerStatus.REJECTED
            )
            warning_placeholder = _placeholder_from_lean_output(stdout, stderr)
            blocked_placeholder = forbidden_placeholder or warning_placeholder
            if blocked_placeholder is not None:
                status = LeanCheckerStatus.REJECTED
                failure_kind = "forbidden_proof_placeholder"
                failure_message = (
                    "Lean proof candidate contains forbidden placeholder: "
                    f"{blocked_placeholder}"
                )
    except LeanEnvironmentBootstrapError as exc:
        status = LeanCheckerStatus.ENVIRONMENT_ERROR
        exit_code = None
        failure_kind = "lean_environment_bootstrap_failed"
        failure_message = str(exc)
        stderr = str(exc)[: request.max_output_bytes]
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "")[: request.max_output_bytes]
        stderr = (exc.stderr or "")[: request.max_output_bytes]
        status = LeanCheckerStatus.TIMEOUT
        exit_code = None

    duration_ms = int((monotonic() - started) * 1000)
    stdout_ref = _save_text_artifact(
        artifact_store,
        stdout,
        request=request,
        suffix="stdout.txt",
        artifact_type="LeanCheckerStdout",
    )
    stderr_ref = _save_text_artifact(
        artifact_store,
        stderr,
        request=request,
        suffix="stderr.txt",
        artifact_type="LeanCheckerStderr",
    )
    proof_artifact_ref = None
    if status == LeanCheckerStatus.ACCEPTED:
        proof_artifact_ref = artifact_store.save_bytes(
            proof_source.encode("utf-8"),
            artifact_id=_artifact_id(request.request_id, "proof_artifact.lean"),
            artifact_type="LeanProofArtifact",
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.proof_artifact",
            artifact_schema_version="v1",
            source={"kind": "lean_checker", "request_id": request.request_id},
            metadata={
                "checker_mode": request.checker_mode.value,
                "proof_digest": proof_digest,
            },
            created_at=request.created_at,
        )

    diagnostics = _checker_diagnostics(
        stdout=stdout,
        stderr=stderr,
        failure_kind=failure_kind,
        failure_message=failure_message,
    )
    report_without_ref = LeanCheckerReport(
        report_id=f"lean_checker_report:{_safe_id(request.request_id)}",
        request_id=request.request_id,
        status=status,
        exit_code=exit_code,
        stdout_ref=stdout_ref,
        stderr_ref=stderr_ref,
        generated_source_ref=generated_source_ref,
        proof_artifact_ref=proof_artifact_ref,
        report_ref=None,
        diagnostics=diagnostics,
        normalized_theorem_digest=normalized_theorem_digest,
        proof_digest=proof_digest if status == LeanCheckerStatus.ACCEPTED else None,
        environment_ref=request.environment_ref,
        command_summary={
            "backend": "lean_direct_cached_environment",
            "executable": environment_manifest.lean_executable,
            "args": ["<generated_source>"],
            "cwd": environment_manifest.project_root,
        },
        duration_ms=duration_ms,
    )
    report_ref = artifact_store.save_json(
        report_without_ref.to_dict(include_report_ref=False),
        artifact_id=_artifact_id(request.request_id, "checker_report.json"),
        artifact_type="LeanCheckerReport",
        artifact_schema_id="lean_proof.checker_report",
        artifact_schema_version="v1",
        source={"kind": "lean_checker", "request_id": request.request_id},
        metadata={"checker_mode": request.checker_mode.value, "status": status.value},
        created_at=request.created_at,
    )
    return LeanCheckerReport(
        **{
            **report_without_ref.__dict__,
            "report_ref": report_ref,
        }
    )


def render_lean_source(payload: LeanTheoremPayload, proof_source: str) -> str:
    lines: list[str] = [f"import {item}" for item in payload.imports]
    options = {"autoImplicit": False, **payload.options}
    for key, value in sorted(options.items()):
        if isinstance(value, bool):
            lean_value = "true" if value else "false"
        else:
            lean_value = str(value)
        lines.append(f"set_option {key} {lean_value}")
    if payload.namespace:
        lines.append(f"namespace {payload.namespace}")
    for namespace in payload.open_namespaces:
        lines.append(f"open {namespace}")
    parameters = f" {payload.parameters_source}" if payload.parameters_source else ""
    lines.append(
        f"theorem {payload.theorem_name}{parameters} : "
        f"{payload.statement_source} := {proof_source}"
    )
    if payload.namespace:
        lines.append(f"end {payload.namespace}")
    lines.append("")
    return "\n".join(lines)


# 保留原有 private 名称，避免历史内部引用在迁移期间失效。
_render_lean_source = render_lean_source


def prepared_lean_environment(
    manifest: LeanEnvironmentManifest,
) -> dict[str, str]:
    """Ask Lake for its environment once and retain only fixed allowlisted keys."""

    cache_key = (
        str(manifest.environment_digest),
        manifest.lake_executable,
        manifest.lean_executable,
        manifest.project_root,
    )
    cached = _PREPARED_LEAN_ENVIRONMENT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    script = (
        "import json, os; "
        f"keys={list(_LEAN_ENVIRONMENT_ALLOWLIST)!r}; "
        "print(json.dumps({key: os.environ[key] for key in keys if key in os.environ}))"
    )
    command = [manifest.lake_executable, "env", sys.executable, "-c", script]
    run_kwargs = {
        "cwd": manifest.project_root,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "timeout": int(manifest.resource_limits.get("timeout_seconds", 30)),
        "env": _subprocess_env(manifest),
        "check": False,
    }
    try:
        completed = subprocess.run(command, **run_kwargs)
        if (
            completed.returncode != 0
            and _STALE_LAKE_CONFIGURATION_ERROR in completed.stderr
        ):
            completed = subprocess.run(
                [manifest.lake_executable, "-R", *command[1:]],
                **run_kwargs,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LeanEnvironmentBootstrapError(
            f"Lake environment bootstrap failed: {exc}"
        ) from exc
    if completed.returncode != 0:
        excerpt = (completed.stdout + completed.stderr)[:1200]
        raise LeanEnvironmentBootstrapError(
            f"Lake environment bootstrap exited {completed.returncode}: {excerpt}"
        )
    try:
        raw_environment = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LeanEnvironmentBootstrapError(
            "Lake environment bootstrap returned invalid JSON"
        ) from exc
    if not isinstance(raw_environment, dict):
        raise LeanEnvironmentBootstrapError(
            "Lake environment bootstrap must return a JSON object"
        )
    prepared = {
        key: str(raw_environment[key])
        for key in _LEAN_ENVIRONMENT_ALLOWLIST
        if key in raw_environment and isinstance(raw_environment[key], str)
    }
    _PREPARED_LEAN_ENVIRONMENT_CACHE[cache_key] = dict(prepared)
    return prepared


def _clear_prepared_lean_environment_cache() -> None:
    _PREPARED_LEAN_ENVIRONMENT_CACHE.clear()


def _load_json_object(text: str, *, artifact_name: str) -> JsonObject:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact_name} must contain JSON") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{artifact_name} must contain a JSON object")
    return body


def _validated_proof_source(
    body: JsonObject,
    *,
    theorem_payload: LeanTheoremPayload,
) -> str:
    if body.get("schema_version") != LEAN_PROOF_CANDIDATE_SCHEMA_VERSION:
        raise ValueError(
            "proof candidate schema_version must be lean_proof.proof_candidate.v1"
        )
    for field_name in _PROOF_CANDIDATE_REQUIRED_FIELDS:
        if field_name not in body:
            raise ValueError(f"proof candidate missing required field: {field_name}")
    for field_name in (
        "proof_candidate_id",
        "theorem_payload_digest",
        "proof_source",
        "created_at",
    ):
        if not isinstance(body.get(field_name), str) or not body.get(field_name):
            raise ValueError(f"{field_name} must be a non-empty string")
    if body["theorem_payload_digest"] != theorem_payload.payload_digest:
        raise ValueError("theorem_payload_digest does not match theorem payload")
    if not str(body["proof_candidate_id"]).startswith("proof_candidate:"):
        raise ValueError("proof_candidate_id must start with proof_candidate:")
    return str(body["proof_source"])


def _forbidden_proof_placeholder(proof_source: str) -> str | None:
    match = _FORBIDDEN_PROOF_PLACEHOLDER_PATTERN.search(proof_source)
    if match is None:
        return None
    return match.group("placeholder")


def _placeholder_from_lean_output(stdout: str, stderr: str) -> str | None:
    lowered = f"{stdout}\n{stderr}".lower()
    if "uses 'sorry'" in lowered or 'uses "sorry"' in lowered:
        return "sorry"
    if "uses 'admit'" in lowered or 'uses "admit"' in lowered:
        return "admit"
    return None


def _checker_diagnostics(
    *,
    stdout: str,
    stderr: str,
    failure_kind: str | None,
    failure_message: str | None,
) -> JsonObject:
    combined = (stdout + stderr)[:8000]
    diagnostics: JsonObject = {
        "stdout_excerpt": stdout[:4000],
        "stderr_excerpt": stderr[:4000],
        "combined_excerpt": combined,
    }
    if failure_kind is not None:
        diagnostics["failure_kind"] = failure_kind
        diagnostics["message"] = failure_message or failure_kind
        diagnostics["combined_excerpt"] = (
            (failure_message or failure_kind) + "\n" + combined
        )[:8000]
    return diagnostics


def _verify_environment_matches_request(
    request: LeanCheckerRequest,
    manifest: LeanEnvironmentManifest,
) -> None:
    if request.environment_ref.environment_digest != manifest.environment_digest:
        raise ValueError("environment_ref digest does not match LeanEnvironmentManifest")


def _save_text_artifact(
    store: ArtifactStore,
    text: str,
    *,
    request: LeanCheckerRequest,
    suffix: str,
    artifact_type: str,
) -> ArtifactRef:
    return store.save_bytes(
        text.encode("utf-8"),
        artifact_id=_artifact_id(request.request_id, suffix),
        artifact_type=artifact_type,
        media_type="text/plain",
        artifact_schema_id="lean_proof.checker_log",
        artifact_schema_version="v1",
        source={"kind": "lean_checker", "request_id": request.request_id},
        metadata={"checker_mode": request.checker_mode.value},
        created_at=request.created_at,
    )


def _subprocess_env(manifest: LeanEnvironmentManifest) -> dict[str, str]:
    env = dict(os.environ)
    lean_exe = Path(manifest.lean_executable)
    elan_home = lean_exe.parent.parent
    env["ELAN_HOME"] = str(elan_home)
    env["PATH"] = f"{lean_exe.parent}{os.pathsep}{env.get('PATH', '')}"
    return env


def _temporary_source_path(request_id: str) -> Path:
    return Path(f"{_safe_id(request_id)}.lean")


def _artifact_id(request_id: str, suffix: str) -> str:
    return f"{_safe_id(request_id)}_{_safe_id(suffix)}"


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ArtifactRef):
        return value.to_dict()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
