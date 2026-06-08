"""Phase 1 protocol objects.

The names and JSON keys in this module intentionally mirror
Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md.
Dataclasses are in-memory helpers; their ``to_dict`` methods are the stable
wire format used by JSONL events, artifact manifests, and SQLite indexes.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any


JsonObject = dict[str, Any]


class TaskState(str, Enum):
    """Stable TaskUnit lifecycle strings.

    Phase 1 only creates root units in ``Ready``. The other values are reserved
    here so later state-machine code can reuse the same serialized spelling.
    Keep this enum at TaskUnit granularity: lease validity belongs to the
    future Lease state machine, and submission/verification progress belongs to
    the future Attempt state machine.
    """

    CREATED = "Created"
    BLOCKED = "Blocked"
    READY = "Ready"
    PROCESSING = "Processing"
    WAITING_FOR_CHILDREN = "WaitingForChildren"
    MERGE_READY = "MergeReady"
    MERGING = "Merging"
    COMPLETED = "Completed"
    MERGE_FAILED = "MergeFailed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class LeaseState(str, Enum):
    """Stable Lease lifecycle strings for Phase 2."""

    ACTIVE = "Active"
    RELEASED = "Released"
    EXPIRED = "Expired"
    REVOKED = "Revoked"


class AttemptState(str, Enum):
    """Stable Attempt lifecycle strings.

    Phase 2 only actively uses Created, Running, Failed, and Superseded. The
    later verification/canonical states are listed so event snapshots keep one
    spelling across phases, but Phase 2 state machines do not allow them yet.
    """

    CREATED = "Created"
    RUNNING = "Running"
    SUBMITTED = "Submitted"
    VERIFYING = "Verifying"
    VERIFIED = "Verified"
    CANONICAL = "Canonical"
    REJECTED = "Rejected"
    FAILED = "Failed"
    SUPERSEDED = "Superseded"


def _json_value(value: Any) -> Any:
    """Convert nested protocol objects into JSON-safe values.

    The helper keeps serialization centralized so schema changes happen in one
    place instead of being hand-coded by every event or storage component.
    """

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to persisted artifact content.

    ``ArtifactRef`` lives in core because protocol events should only need the
    reference and integrity metadata. Filesystem path handling stays in
    ``tokenshare.storage.artifacts``.
    """

    artifact_id: str
    artifact_type: str
    uri: str
    content_hash: str
    size_bytes: int
    media_type: str
    artifact_schema_id: str
    artifact_schema_version: str
    source: JsonObject
    metadata: JsonObject
    created_at: str
    schema_version: str = "ArtifactRef.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "artifact_schema_id": self.artifact_schema_id,
            "artifact_schema_version": self.artifact_schema_version,
            "source": _json_value(self.source),
            "metadata": _json_value(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "ArtifactRef":
        return cls(
            artifact_id=data["artifact_id"],
            artifact_type=data["artifact_type"],
            uri=data["uri"],
            content_hash=data["content_hash"],
            size_bytes=int(data["size_bytes"]),
            media_type=data["media_type"],
            artifact_schema_id=data["artifact_schema_id"],
            artifact_schema_version=data["artifact_schema_version"],
            source=dict(data.get("source", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=data["created_at"],
            schema_version=data.get("schema_version", "ArtifactRef.v1"),
        )


@dataclass(frozen=True)
class ProtocolConfig:
    """Versioned run-policy snapshot stored inside TaskSpec."""

    config_id: str
    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    max_retries: int
    retry_backoff_seconds: int
    allow_shadow_execution: bool
    scheduling_policy: str
    canonical_output_policy: str
    max_depth: int
    max_children_per_unit: int
    max_total_units: int
    max_expansions_per_unit: int
    artifact_store_uri: str
    event_log_uri: str
    base_reward_rates: JsonObject
    metadata: JsonObject
    schema_version: str = "ProtocolConfig.v1"

    @classmethod
    def default(
        cls,
        *,
        config_id: str,
        artifact_store_uri: str,
        event_log_uri: str,
        metadata: JsonObject | None = None,
    ) -> "ProtocolConfig":
        """Create the conservative Phase 1 default policy snapshot."""

        return cls(
            config_id=config_id,
            lease_ttl_seconds=300,
            heartbeat_interval_seconds=60,
            max_retries=3,
            retry_backoff_seconds=30,
            allow_shadow_execution=False,
            scheduling_policy="fifo_ready",
            canonical_output_policy="first_verified_bundle",
            max_depth=8,
            max_children_per_unit=16,
            max_total_units=1000,
            max_expansions_per_unit=1,
            artifact_store_uri=artifact_store_uri,
            event_log_uri=event_log_uri,
            base_reward_rates={
                "root_completion": 1.0,
                "verified_output": 1.0,
                "expansion": 0.25,
            },
            metadata=metadata or {},
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "config_id": self.config_id,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "allow_shadow_execution": self.allow_shadow_execution,
            "scheduling_policy": self.scheduling_policy,
            "canonical_output_policy": self.canonical_output_policy,
            "max_depth": self.max_depth,
            "max_children_per_unit": self.max_children_per_unit,
            "max_total_units": self.max_total_units,
            "max_expansions_per_unit": self.max_expansions_per_unit,
            "artifact_store_uri": self.artifact_store_uri,
            "event_log_uri": self.event_log_uri,
            "base_reward_rates": _json_value(self.base_reward_rates),
            "metadata": _json_value(self.metadata),
        }


@dataclass(frozen=True)
class TaskSpec:
    """Root task registration snapshot."""

    task_id: str
    description: str
    plugin_id: str
    plugin_version: str
    split_strategy_id: str
    split_strategy_params: JsonObject
    root_input_ref: ArtifactRef
    protocol_config: ProtocolConfig
    metadata: JsonObject
    created_at: str
    root_budget: float | None = None
    root_deadline: str | None = None
    schema_version: str = "TaskSpec.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "description": self.description,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "split_strategy_id": self.split_strategy_id,
            "split_strategy_params": _json_value(self.split_strategy_params),
            "root_input_ref": self.root_input_ref.to_dict(),
            "root_budget": self.root_budget,
            "root_deadline": self.root_deadline,
            "protocol_config": self.protocol_config.to_dict(),
            "metadata": _json_value(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TaskUnit:
    """A node in the protocol task graph."""

    unit_id: str
    task_id: str
    parent_unit_id: str | None
    depth: int
    unit_type: str
    state: TaskState
    input_refs: dict[str, ArtifactRef]
    canonical_output_refs: dict[str, ArtifactRef]
    required_capabilities: JsonObject
    weight: float
    budget_limit: float | None
    deadline: str | None
    plugin_payload: JsonObject
    metadata: JsonObject
    created_at: str
    updated_at: str
    schema_version: str = "TaskUnit.v1"

    @classmethod
    def create_root(
        cls,
        *,
        task_spec: TaskSpec,
        unit_id: str,
        required_capabilities: JsonObject,
        plugin_payload: JsonObject,
        now: str,
    ) -> "TaskUnit":
        """Create the root unit required by Phase 1 registration.

        Root units are immediately ``Ready`` because Phase 1 has no dependency
        graph yet. Later phases will create child units through TaskGraph.
        """

        return cls(
            unit_id=unit_id,
            task_id=task_spec.task_id,
            parent_unit_id=None,
            depth=0,
            unit_type="root",
            state=TaskState.READY,
            input_refs={"root_input": task_spec.root_input_ref},
            canonical_output_refs={},
            required_capabilities=required_capabilities,
            weight=1.0,
            budget_limit=task_spec.root_budget,
            deadline=task_spec.root_deadline,
            plugin_payload=plugin_payload,
            metadata=dict(task_spec.metadata),
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "unit_id": self.unit_id,
            "task_id": self.task_id,
            "parent_unit_id": self.parent_unit_id,
            "depth": self.depth,
            "unit_type": self.unit_type,
            "state": self.state.value,
            "input_refs": _json_value(self.input_refs),
            "canonical_output_refs": _json_value(self.canonical_output_refs),
            "required_capabilities": _json_value(self.required_capabilities),
            "weight": self.weight,
            "budget_limit": self.budget_limit,
            "deadline": self.deadline,
            "plugin_payload": _json_value(self.plugin_payload),
            "metadata": _json_value(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskRelation:
    """Relationship between TaskUnit nodes.

    Phase 1 defines the schema but root registration does not create relations.
    """

    relation_id: str
    task_id: str
    relation_type: str
    source_unit_id: str
    target_unit_id: str
    source_output_name: str | None
    target_input_name: str | None
    created_reason: str
    metadata: JsonObject
    created_at: str
    schema_version: str = "TaskRelation.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "relation_id": self.relation_id,
            "task_id": self.task_id,
            "relation_type": self.relation_type,
            "source_unit_id": self.source_unit_id,
            "target_unit_id": self.target_unit_id,
            "source_output_name": self.source_output_name,
            "target_input_name": self.target_input_name,
            "created_reason": self.created_reason,
            "metadata": _json_value(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ClientRecord:
    """Local simulated client/executor capability record."""

    client_id: str
    executor_type: str
    executor_id: str
    executor_version: str
    capabilities: JsonObject
    status: str
    stats: JsonObject
    metadata: JsonObject
    registered_at: str
    last_seen_at: str | None = None
    schema_version: str = "ClientRecord.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "client_id": self.client_id,
            "executor_type": self.executor_type,
            "executor_id": self.executor_id,
            "executor_version": self.executor_version,
            "capabilities": _json_value(self.capabilities),
            "status": self.status,
            "stats": _json_value(self.stats),
            "metadata": _json_value(self.metadata),
            "registered_at": self.registered_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class Lease:
    """A fenced, time-limited execution claim for one TaskUnit."""

    lease_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    client_id: str
    state: LeaseState
    fencing_token: str
    issued_at: str
    expires_at: str
    last_heartbeat_at: str | None
    heartbeat_count: int
    lease_kind: str
    terminated_at: str | None
    terminated_reason: str | None
    metadata: JsonObject
    schema_version: str = "phase2.lease.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "lease_id": self.lease_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "client_id": self.client_id,
            "state": self.state.value,
            "fencing_token": self.fencing_token,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "heartbeat_count": self.heartbeat_count,
            "lease_kind": self.lease_kind,
            "terminated_at": self.terminated_at,
            "terminated_reason": self.terminated_reason,
            "metadata": _json_value(self.metadata),
        }


@dataclass(frozen=True)
class Attempt:
    """One execution attempt authorized by a Lease."""

    attempt_id: str
    task_id: str
    unit_id: str
    lease_id: str
    client_id: str
    state: AttemptState
    attempt_kind: str
    created_at: str
    started_at: str | None = None
    submitted_at: str | None = None
    finished_at: str | None = None
    environment_summary: JsonObject | None = None
    input_artifact_refs: dict[str, ArtifactRef] | None = None
    raw_output_ref: ArtifactRef | None = None
    parsed_output_ref: ArtifactRef | None = None
    candidate_output_refs: dict[str, ArtifactRef] | None = None
    log_ref: ArtifactRef | None = None
    failure_kind: str | None = None
    failure_reason: str | None = None
    superseded_by_attempt_id: str | None = None
    metadata: JsonObject | None = None
    schema_version: str = "phase2.attempt.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "lease_id": self.lease_id,
            "client_id": self.client_id,
            "state": self.state.value,
            "attempt_kind": self.attempt_kind,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "submitted_at": self.submitted_at,
            "finished_at": self.finished_at,
            "environment_summary": _json_value(self.environment_summary or {}),
            "input_artifact_refs": _json_value(self.input_artifact_refs or {}),
            "raw_output_ref": _json_value(self.raw_output_ref),
            "parsed_output_ref": _json_value(self.parsed_output_ref),
            "candidate_output_refs": _json_value(self.candidate_output_refs or {}),
            "log_ref": _json_value(self.log_ref),
            "failure_kind": self.failure_kind,
            "failure_reason": self.failure_reason,
            "superseded_by_attempt_id": self.superseded_by_attempt_id,
            "metadata": _json_value(self.metadata or {}),
        }
