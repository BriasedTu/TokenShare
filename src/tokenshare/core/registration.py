"""Root task registration orchestration for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass

from tokenshare.core.models import ArtifactRef, JsonObject, ProtocolConfig, TaskSpec, TaskUnit
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


@dataclass(frozen=True)
class RootTaskRegistrationRequest:
    """Input envelope for the Phase 1 root registration happy path."""

    task_id: str
    root_unit_id: str
    root_artifact_id: str
    description: str
    plugin_id: str
    plugin_version: str
    split_strategy_id: str
    split_strategy_params: JsonObject
    root_input_bytes: bytes
    root_input_media_type: str
    root_input_schema_id: str
    root_input_schema_version: str
    protocol_config: ProtocolConfig
    required_capabilities: JsonObject
    plugin_payload: JsonObject
    metadata: JsonObject
    created_at: str
    root_budget: float | None = None
    root_deadline: str | None = None


@dataclass(frozen=True)
class RootTaskRegistrationResult:
    """Objects and events created by one root registration operation."""

    root_input_ref: ArtifactRef
    task_spec: TaskSpec
    root_unit: TaskUnit
    events: tuple[LedgerEvent, LedgerEvent, LedgerEvent]


class RootTaskRegistrar:
    """Small Phase 1 coordinator.

    The full ``ProtocolEngine`` arrives in later phases. This class exists so
    Phase 1 can already prove the first protocol loop: persist input artifact,
    append registration facts, and create a Ready root TaskUnit.
    """

    def __init__(self, *, artifact_store: ArtifactStore, event_ledger: EventLedger) -> None:
        self._artifact_store = artifact_store
        self._event_ledger = event_ledger

    def register_root_task(self, request: RootTaskRegistrationRequest) -> RootTaskRegistrationResult:
        root_input_ref = self._artifact_store.save_bytes(
            request.root_input_bytes,
            artifact_id=request.root_artifact_id,
            artifact_type="root_input",
            media_type=request.root_input_media_type,
            artifact_schema_id=request.root_input_schema_id,
            artifact_schema_version=request.root_input_schema_version,
            source={"kind": "client_input", "task_id": request.task_id},
            metadata=request.metadata,
            created_at=request.created_at,
        )
        artifact_event = self._event_ledger.append(
            event_type=EventType.ARTIFACT_STORED,
            object_type="ArtifactRef",
            object_id=root_input_ref.artifact_id,
            task_id=request.task_id,
            actor={"kind": "protocol"},
            idempotency_key=f"artifact:{root_input_ref.content_hash}",
            payload={"artifact_ref": root_input_ref.to_dict()},
            occurred_at=request.created_at,
        )

        task_spec = TaskSpec(
            task_id=request.task_id,
            description=request.description,
            plugin_id=request.plugin_id,
            plugin_version=request.plugin_version,
            split_strategy_id=request.split_strategy_id,
            split_strategy_params=request.split_strategy_params,
            root_input_ref=root_input_ref,
            root_budget=request.root_budget,
            root_deadline=request.root_deadline,
            protocol_config=request.protocol_config,
            metadata=request.metadata,
            created_at=request.created_at,
        )
        task_event = self._event_ledger.append(
            event_type=EventType.TASK_REGISTERED,
            object_type="TaskSpec",
            object_id=task_spec.task_id,
            task_id=task_spec.task_id,
            actor={"kind": "protocol"},
            idempotency_key=f"register_task:{task_spec.task_id}",
            payload={"task_spec": task_spec.to_dict()},
            occurred_at=request.created_at,
        )

        root_unit = TaskUnit.create_root(
            task_spec=task_spec,
            unit_id=request.root_unit_id,
            required_capabilities=request.required_capabilities,
            plugin_payload=request.plugin_payload,
            now=request.created_at,
        )
        unit_event = self._event_ledger.append(
            event_type=EventType.TASK_UNIT_CREATED,
            object_type="TaskUnit",
            object_id=root_unit.unit_id,
            task_id=root_unit.task_id,
            actor={"kind": "protocol"},
            idempotency_key=f"task_unit_created:{root_unit.unit_id}",
            payload={"task_unit": root_unit.to_dict()},
            occurred_at=request.created_at,
        )

        return RootTaskRegistrationResult(
            root_input_ref=root_input_ref,
            task_spec=task_spec,
            root_unit=root_unit,
            events=(artifact_event, task_event, unit_event),
        )
