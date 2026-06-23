"""Phase 3 unified executor contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.plugins.contracts import OutputContract


class ExecutorStatus(str, Enum):
    """Explicit Phase 3 executor availability contract."""

    AVAILABLE = "Available"
    BUSY = "Busy"
    OFFLINE = "Offline"
    DISABLED = "Disabled"


@dataclass(frozen=True)
class EnvironmentRef:
    """Immutable execution environment identity shared by request and submission."""

    environment_id: str
    environment_digest: str
    runtime: str
    tool_versions: JsonObject
    resource_limits: JsonObject
    fixture_profile_digest: str
    seed: int | None
    clock_policy: str
    created_at: str
    schema_version: str = "phase3.environment_ref.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "environment_id": self.environment_id,
            "environment_digest": self.environment_digest,
            "runtime": self.runtime,
            "tool_versions": _json_value(self.tool_versions),
            "resource_limits": _json_value(self.resource_limits),
            "fixture_profile_digest": self.fixture_profile_digest,
            "seed": self.seed,
            "clock_policy": self.clock_policy,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExecutorDescriptor:
    """Versioned local executor capability descriptor."""

    executor_id: str
    executor_type: str
    executor_version: str
    supported_request_schema_versions: list[str]
    capabilities: JsonObject
    environment_policy: JsonObject
    status: ExecutorStatus | str
    metadata: JsonObject | None = None
    schema_version: str = "phase3.executor_descriptor.v1"

    @property
    def normalized_status(self) -> ExecutorStatus:
        return ExecutorStatus(self.status)

    @property
    def descriptor_digest(self) -> str:
        return _sha256_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "executor_id": self.executor_id,
            "executor_type": self.executor_type,
            "executor_version": self.executor_version,
            "supported_request_schema_versions": list(self.supported_request_schema_versions),
            "capabilities": _json_value(self.capabilities),
            "environment_policy": _json_value(self.environment_policy),
            "status": self.normalized_status.value,
            "metadata": _json_value(self.metadata or {}),
        }
        if include_digest:
            body["descriptor_digest"] = self.descriptor_digest
        return body


@dataclass(frozen=True)
class PromptPackage:
    """AI/mock-AI prompt artifact body."""

    prompt_package_id: str
    request_id: str
    task_id: str
    unit_id: str
    prompt_text: str
    input_summary: JsonObject
    output_schema: JsonObject
    constraints: JsonObject
    seed: int | None
    fixture_profile: str
    created_at: str
    schema_version: str = "phase3.prompt_package.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "prompt_package_id": self.prompt_package_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "prompt_text": self.prompt_text,
            "input_summary": _json_value(self.input_summary),
            "output_schema": _json_value(self.output_schema),
            "constraints": _json_value(self.constraints),
            "seed": self.seed,
            "fixture_profile": self.fixture_profile,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExecutionRequest:
    """Artifact-backed authorization for one executor attempt."""

    request_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    lease_id: str
    fencing_token: str
    plugin: JsonObject
    executor: JsonObject
    registry_snapshot_id: str
    allocation_decision: JsonObject
    capability_snapshot: JsonObject
    task_unit_snapshot: JsonObject
    input_artifact_refs: dict[str, ArtifactRef]
    output_contract: OutputContract
    hard_requirements: JsonObject
    soft_hints: JsonObject | None
    environment_ref: EnvironmentRef
    execution_instruction_ref: ArtifactRef | None
    prompt_package_ref: ArtifactRef | None
    limits: JsonObject
    created_at: str
    schema_version: str = "phase3.execution_request.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "fencing_token": self.fencing_token,
            "plugin": _json_value(self.plugin),
            "executor": _json_value(self.executor),
            "registry_snapshot_id": self.registry_snapshot_id,
            "allocation_decision": _json_value(self.allocation_decision),
            "capability_snapshot": _json_value(self.capability_snapshot),
            "task_unit_snapshot": _json_value(self.task_unit_snapshot),
            "input_artifact_refs": _json_value(self.input_artifact_refs),
            "output_contract": self.output_contract.to_dict(),
            "hard_requirements": _json_value(self.hard_requirements),
            "soft_hints": _json_value(self.soft_hints or {}),
            "environment_ref": self.environment_ref.to_dict(),
            "execution_instruction_ref": _json_value(self.execution_instruction_ref),
            "prompt_package_ref": _json_value(self.prompt_package_ref),
            "limits": _json_value(self.limits),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExecutionSubmission:
    """Artifact-backed executor response for one execution request."""

    submission_id: str
    request_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    lease_id: str
    fencing_token: str
    executor_id: str
    executor_version: str
    result_kind: str
    raw_output_ref: ArtifactRef | None
    parsed_output_ref: ArtifactRef | None
    candidate_output_refs: dict[str, ArtifactRef]
    parse_failure_ref: ArtifactRef | None
    log_ref: ArtifactRef | None
    environment_ref: EnvironmentRef
    environment_summary: JsonObject
    provenance_ref: ArtifactRef | None
    usage_summary: JsonObject | None
    error: JsonObject | None
    submitted_at: str
    schema_version: str = "phase3.execution_submission.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "submission_id": self.submission_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "fencing_token": self.fencing_token,
            "executor_id": self.executor_id,
            "executor_version": self.executor_version,
            "result_kind": self.result_kind,
            "raw_output_ref": _json_value(self.raw_output_ref),
            "parsed_output_ref": _json_value(self.parsed_output_ref),
            "candidate_output_refs": _json_value(self.candidate_output_refs),
            "parse_failure_ref": _json_value(self.parse_failure_ref),
            "log_ref": _json_value(self.log_ref),
            "environment_ref": self.environment_ref.to_dict(),
            "environment_summary": _json_value(self.environment_summary),
            "provenance_ref": _json_value(self.provenance_ref),
            "usage_summary": _json_value(self.usage_summary or {}),
            "error": _json_value(self.error),
            "submitted_at": self.submitted_at,
        }


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


def _sha256_json(data: JsonObject) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"
