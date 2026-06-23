"""Minimal Phase 2 application service for event-backed scheduling flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import Attempt, AttemptState, ClientRecord, JsonObject, Lease, LeaseState, ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.scheduling import Scheduler, SchedulingDecision
from tokenshare.core.state_machines import transition_attempt, transition_task_unit
from tokenshare.core.task_graph import TaskGraph
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry, RegistrySnapshot
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


@dataclass(frozen=True)
class SchedulingFlowResult:
    lease: Lease
    attempt: Attempt
    task_unit: TaskUnit
    scheduling_decision: SchedulingDecision
    events: tuple[LedgerEvent, LedgerEvent, LedgerEvent, LedgerEvent]


@dataclass(frozen=True)
class LeaseExpiryFlowResult:
    lease: Lease
    attempt: Attempt
    task_unit: TaskUnit
    recovery_action: JsonObject
    events: tuple[LedgerEvent, LedgerEvent, LedgerEvent, LedgerEvent]


@dataclass(frozen=True)
class LeaseHeartbeatFlowResult:
    lease: Lease
    event: LedgerEvent


@dataclass(frozen=True)
class RegistrySnapshotFlowResult:
    snapshot: RegistrySnapshot
    registry_snapshot_ref: object
    event: LedgerEvent


@dataclass(frozen=True)
class ExecutionRequestFlowResult:
    request: ExecutionRequest
    request_ref: object
    event: LedgerEvent


@dataclass(frozen=True)
class ExecutionSubmissionFlowResult:
    submission: ExecutionSubmission
    submission_ref: object
    event: LedgerEvent
    attempt: Attempt | None
    attempt_event: LedgerEvent | None


class ProtocolEngine:
    """Write core Phase 2 decisions to the append-only event ledger."""

    def __init__(
        self,
        *,
        event_ledger: EventLedger,
        protocol_config: ProtocolConfig,
        artifact_store: ArtifactStore | None = None,
        scheduler: Scheduler | None = None,
        lease_manager: LeaseManager | None = None,
    ) -> None:
        self._event_ledger = event_ledger
        self._protocol_config = protocol_config
        self._artifact_store = artifact_store
        self._scheduler = scheduler or Scheduler()
        self._lease_manager = lease_manager or LeaseManager(protocol_config=protocol_config)

    def record_registry_snapshot(
        self,
        *,
        task_id: str,
        registry_snapshot_id: str,
        plugin_registry: PluginRegistry,
        executor_registry: ExecutorRegistry,
        now: str,
        correlation_id: str,
    ) -> RegistrySnapshotFlowResult:
        artifact_store = self._require_artifact_store()
        snapshot = plugin_registry.freeze(
            task_id=task_id,
            registry_snapshot_id=registry_snapshot_id,
            executor_registry=executor_registry,
            artifact_store=artifact_store,
            frozen_at=now,
        )
        snapshot_ref = artifact_store.save_json(
            snapshot.to_dict(),
            artifact_id=registry_snapshot_id,
            artifact_type="RegistrySnapshot",
            artifact_schema_id="phase3.registry_snapshot",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": task_id},
            created_at=now,
        )
        event = self._event_ledger.append(
            event_type=EventType.REGISTRY_SNAPSHOT_RECORDED,
            object_type="RegistrySnapshot",
            object_id=registry_snapshot_id,
            task_id=task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"registry_snapshot:{registry_snapshot_id}",
            payload={
                "schema_version": "phase3.registry_snapshot_record.v1",
                "registry_snapshot_id": registry_snapshot_id,
                "task_id": task_id,
                "registry_snapshot_ref": snapshot_ref.to_dict(),
                "registry_snapshot_digest": snapshot_ref.content_hash,
                "plugin_entries": [
                    _registry_plugin_entry_summary(entry) for entry in snapshot.plugin_entries
                ],
                "executor_entries": [
                    _registry_executor_entry_summary(entry) for entry in snapshot.executor_entries
                ],
                "frozen_at": now,
            },
            occurred_at=now,
        )
        return RegistrySnapshotFlowResult(
            snapshot=snapshot,
            registry_snapshot_ref=snapshot_ref,
            event=event,
        )

    def record_execution_request(
        self,
        *,
        request: ExecutionRequest,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ExecutionRequestFlowResult:
        artifact_store = self._require_artifact_store()
        request_ref = artifact_store.save_json(
            request.to_dict(),
            artifact_id=request.request_id,
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": request.task_id, "attempt_id": request.attempt_id},
            created_at=request.created_at,
        )
        event = self._event_ledger.append(
            event_type=EventType.EXECUTION_REQUEST_RECORDED,
            object_type="ExecutionRequest",
            object_id=request.request_id,
            task_id=request.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=f"execution_request:{request.request_id}",
            payload={
                "schema_version": "phase3.execution_request_record.v1",
                "request_id": request.request_id,
                "task_id": request.task_id,
                "unit_id": request.unit_id,
                "attempt_id": request.attempt_id,
                "lease_id": request.lease_id,
                "request_ref": request_ref.to_dict(),
                "request_digest": request_ref.content_hash,
                "plugin_id": request.plugin.get("plugin_id"),
                "executor_id": request.executor.get("executor_id"),
                "created_at": request.created_at,
            },
            occurred_at=request.created_at,
        )
        return ExecutionRequestFlowResult(request=request, request_ref=request_ref, event=event)

    def record_execution_submission(
        self,
        *,
        submission: ExecutionSubmission,
        attempt: Attempt,
        lease: Lease,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ExecutionSubmissionFlowResult:
        artifact_store = self._require_artifact_store()
        submission_ref = artifact_store.save_json(
            submission.to_dict(),
            artifact_id=submission.submission_id,
            artifact_type="ExecutionSubmission",
            artifact_schema_id="phase3.execution_submission",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": submission.task_id, "attempt_id": submission.attempt_id},
            created_at=submission.submitted_at,
        )
        event = self._event_ledger.append(
            event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
            object_type="ExecutionSubmission",
            object_id=submission.submission_id,
            task_id=submission.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=f"execution_submission:{submission.submission_id}",
            payload={
                "schema_version": "phase3.execution_submission_record.v1",
                "submission_id": submission.submission_id,
                "request_id": submission.request_id,
                "task_id": submission.task_id,
                "unit_id": submission.unit_id,
                "attempt_id": submission.attempt_id,
                "lease_id": submission.lease_id,
                "submission_ref": submission_ref.to_dict(),
                "submission_digest": submission_ref.content_hash,
                "result_kind": submission.result_kind,
                "submitted_at": submission.submitted_at,
            },
            occurred_at=submission.submitted_at,
        )
        if attempt.state != AttemptState.RUNNING or not _submission_matches_attempt_lease(
            submission=submission,
            attempt=attempt,
            lease=lease,
        ):
            return ExecutionSubmissionFlowResult(
                submission=submission,
                submission_ref=submission_ref,
                event=event,
                attempt=None,
                attempt_event=None,
            )
        submitted_attempt = transition_attempt(
            attempt,
            new_state=AttemptState.SUBMITTED,
            changed_at=submission.submitted_at,
            reason="execution_submission_recorded",
            environment_summary=submission.environment_summary,
            raw_output_ref=submission.raw_output_ref,
            parsed_output_ref=submission.parsed_output_ref,
            candidate_output_refs=submission.candidate_output_refs,
            log_ref=submission.log_ref,
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=submitted_attempt.attempt_id,
            task_id=submitted_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=event.event_id,
            idempotency_key=(
                f"attempt:state:{submitted_attempt.attempt_id}:Running:Submitted:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.RUNNING.value,
                "new_state": AttemptState.SUBMITTED.value,
                "attempt": submitted_attempt.to_dict(),
                "reason": "execution_submission_recorded",
                "correlation_id": correlation_id,
            },
            occurred_at=submission.submitted_at,
        )
        return ExecutionSubmissionFlowResult(
            submission=submission,
            submission_ref=submission_ref,
            event=event,
            attempt=submitted_attempt,
            attempt_event=attempt_event,
        )

    def schedule_ready_unit(
        self,
        *,
        graph: TaskGraph,
        clients: Iterable[ClientRecord],
        now: str,
        correlation_id: str,
        decision_id: str,
        lease_id: str,
        attempt_id: str,
        fencing_token: str,
        active_leases_by_unit_id: dict[str, object] | None = None,
    ) -> SchedulingFlowResult:
        active_leases = _merge_active_lease_maps(
            _active_leases_by_unit_id_from_events(self._event_ledger.read_all()),
            active_leases_by_unit_id or {},
        )
        decision = self._scheduler.select_next(
            graph=graph,
            clients=clients,
            protocol_config=self._protocol_config,
            active_leases_by_unit_id=active_leases,
            now=now,
            decision_id=decision_id,
        )
        if decision is None:
            raise ValueError("no schedulable ready unit")

        claim = self._lease_manager.claim(
            decision=decision,
            lease_id=lease_id,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            now=now,
        )
        unit = graph.units[decision.unit_id]
        processing_unit = transition_task_unit(
            unit,
            new_state=TaskState.PROCESSING,
            reason="scheduled",
            trigger="scheduler",
            changed_at=now,
        )

        lease_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=claim.lease.lease_id,
            task_id=claim.lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"lease:create:{claim.lease.lease_id}",
            payload={
                "old_state": None,
                "new_state": LeaseState.ACTIVE.value,
                "lease": claim.lease.to_dict(),
                "scheduling_decision": decision.to_dict(),
                "reason": "scheduled",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_created_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=claim.created_attempt.attempt_id,
            task_id=claim.created_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=lease_event.event_id,
            idempotency_key=(
                f"attempt:state:{claim.created_attempt.attempt_id}:null:Created:{correlation_id}"
            ),
            payload={
                "old_state": None,
                "new_state": AttemptState.CREATED.value,
                "attempt": claim.created_attempt.to_dict(),
                "reason": "scheduled",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_running_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=claim.running_attempt.attempt_id,
            task_id=claim.running_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_created_event.event_id,
            idempotency_key=(
                f"attempt:state:{claim.running_attempt.attempt_id}:Created:Running:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.CREATED.value,
                "new_state": AttemptState.RUNNING.value,
                "attempt": claim.running_attempt.to_dict(),
                "reason": "executor_started",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        task_unit_event = self._event_ledger.append(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id=processing_unit.unit_id,
            task_id=processing_unit.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_running_event.event_id,
            idempotency_key=(
                f"task_unit:state:{processing_unit.unit_id}:Ready:Processing:{correlation_id}"
            ),
            payload={
                "task_unit_state_change": _task_unit_state_change(
                    task_unit=processing_unit,
                    old_state=TaskState.READY,
                    new_state=TaskState.PROCESSING,
                    reason="scheduled",
                    trigger="scheduler",
                    correlation_id=correlation_id,
                    causation_event_id=attempt_running_event.event_id,
                    changed_at=now,
                ),
                "task_unit": processing_unit.to_dict(),
            },
            occurred_at=now,
        )

        return SchedulingFlowResult(
            lease=claim.lease,
            attempt=claim.running_attempt,
            task_unit=processing_unit,
            scheduling_decision=decision,
            events=(lease_event, attempt_created_event, attempt_running_event, task_unit_event),
        )

    def record_lease_heartbeat(
        self,
        *,
        lease: Lease,
        now: str,
        correlation_id: str,
    ) -> LeaseHeartbeatFlowResult:
        heartbeat_lease = self._lease_manager.heartbeat(lease, now=now)
        heartbeat_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=heartbeat_lease.lease_id,
            task_id=heartbeat_lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=(
                f"lease:heartbeat:{heartbeat_lease.lease_id}:{heartbeat_lease.heartbeat_count}"
            ),
            payload={
                "old_state": LeaseState.ACTIVE.value,
                "new_state": LeaseState.ACTIVE.value,
                "lease": heartbeat_lease.to_dict(),
                "reason": "heartbeat",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        return LeaseHeartbeatFlowResult(lease=heartbeat_lease, event=heartbeat_event)

    def record_lease_expiry(
        self,
        *,
        lease: Lease,
        attempt: Attempt,
        task_unit: TaskUnit,
        now: str,
        correlation_id: str,
        recovery_action_id: str,
        retry_count: int,
    ) -> LeaseExpiryFlowResult:
        expiry = self._lease_manager.expire(
            lease=lease,
            attempt=attempt,
            task_unit=task_unit,
            now=now,
            recovery_action_id=recovery_action_id,
            retry_count=retry_count,
        )
        reason = expiry.recovery_action["reason"]
        recovered_unit = transition_task_unit(
            task_unit,
            new_state=expiry.next_task_state,
            reason=reason,
            trigger="recovery",
            changed_at=now,
        )

        lease_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=expiry.lease.lease_id,
            task_id=expiry.lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"lease:terminal:{expiry.lease.lease_id}:Expired",
            payload={
                "old_state": LeaseState.ACTIVE.value,
                "new_state": LeaseState.EXPIRED.value,
                "lease": expiry.lease.to_dict(),
                "reason": "lease_expired",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=expiry.attempt.attempt_id,
            task_id=expiry.attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=lease_event.event_id,
            idempotency_key=(
                f"attempt:state:{expiry.attempt.attempt_id}:Running:Superseded:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.RUNNING.value,
                "new_state": AttemptState.SUPERSEDED.value,
                "attempt": expiry.attempt.to_dict(),
                "reason": "lease_expired",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        recovery_event = self._event_ledger.append(
            event_type=EventType.RECOVERY_ACTION_RECORDED,
            object_type="RecoveryAction",
            object_id=expiry.recovery_action["recovery_action_id"],
            task_id=expiry.recovery_action["task_id"],
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_event.event_id,
            idempotency_key=(
                f"recovery:{expiry.recovery_action['unit_id']}:lease_expired:"
                f"{expiry.recovery_action['attempt_id']}:{expiry.recovery_action['retry_count']}"
            ),
            payload={"recovery_action": expiry.recovery_action},
            occurred_at=now,
        )
        task_event = self._event_ledger.append(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id=recovered_unit.unit_id,
            task_id=recovered_unit.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=recovery_event.event_id,
            idempotency_key=(
                f"task_unit:state:{recovered_unit.unit_id}:{task_unit.state.value}:"
                f"{recovered_unit.state.value}:{correlation_id}"
            ),
            payload={
                "task_unit_state_change": _task_unit_state_change(
                    task_unit=recovered_unit,
                    old_state=task_unit.state,
                    new_state=recovered_unit.state,
                    reason=reason,
                    trigger="recovery",
                    correlation_id=correlation_id,
                    causation_event_id=recovery_event.event_id,
                    changed_at=now,
                    state_context={"retry_count": retry_count},
                ),
                "task_unit": recovered_unit.to_dict(),
            },
            occurred_at=now,
        )
        return LeaseExpiryFlowResult(
            lease=expiry.lease,
            attempt=expiry.attempt,
            task_unit=recovered_unit,
            recovery_action=expiry.recovery_action,
            events=(lease_event, attempt_event, recovery_event, task_event),
        )

    def _require_artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            raise ValueError("artifact_store is required for Phase 3 execution artifacts")
        return self._artifact_store


def _task_unit_state_change(
    *,
    task_unit: TaskUnit,
    old_state: TaskState,
    new_state: TaskState,
    reason: str,
    trigger: str,
    correlation_id: str,
    causation_event_id: str | None,
    changed_at: str,
    state_context: JsonObject | None = None,
) -> JsonObject:
    return {
        "schema_version": "phase2.task_unit_state_change.v1",
        "task_id": task_unit.task_id,
        "unit_id": task_unit.unit_id,
        "old_state": old_state.value,
        "new_state": new_state.value,
        "reason": reason,
        "trigger": trigger,
        "correlation_id": correlation_id,
        "causation_event_id": causation_event_id,
        "changed_at": changed_at,
        "state_context": dict(state_context or {}),
    }


def _active_leases_by_unit_id_from_events(events: Iterable[LedgerEvent]) -> dict[str, list[str]]:
    latest_leases: dict[str, JsonObject] = {}
    for event in events:
        if event.event_type != EventType.LEASE_STATE_CHANGED:
            continue
        lease = event.payload.get("lease")
        if not isinstance(lease, dict):
            continue
        lease_id = lease.get("lease_id")
        if isinstance(lease_id, str):
            latest_leases[lease_id] = lease

    active_by_unit_id: dict[str, list[str]] = {}
    for lease_id, lease in latest_leases.items():
        if lease.get("state") != LeaseState.ACTIVE.value:
            continue
        unit_id = lease.get("unit_id")
        if isinstance(unit_id, str):
            active_by_unit_id.setdefault(unit_id, []).append(lease_id)
    return active_by_unit_id


def _merge_active_lease_maps(
    ledger_active: dict[str, list[str]],
    supplied_active: dict[str, object],
) -> dict[str, object]:
    active: dict[str, object] = {unit_id: list(lease_ids) for unit_id, lease_ids in ledger_active.items()}
    for unit_id, value in supplied_active.items():
        active[unit_id] = value
    return active


def _submission_matches_attempt_lease(
    *,
    submission: ExecutionSubmission,
    attempt: Attempt,
    lease: Lease,
) -> bool:
    return (
        submission.task_id == attempt.task_id
        and submission.unit_id == attempt.unit_id
        and submission.attempt_id == attempt.attempt_id
        and submission.lease_id == attempt.lease_id
        and lease.task_id == attempt.task_id
        and lease.unit_id == attempt.unit_id
        and lease.attempt_id == attempt.attempt_id
        and submission.lease_id == lease.lease_id
        and submission.fencing_token == lease.fencing_token
    )


def _registry_plugin_entry_summary(entry: JsonObject) -> JsonObject:
    return {
        "plugin_id": entry.get("plugin_id"),
        "plugin_version": entry.get("plugin_version"),
        "descriptor_digest": entry.get("descriptor_digest"),
        "descriptor_ref": entry.get("descriptor_ref"),
        "supported_task_types": list(entry.get("supported_task_types", [])),
    }


def _registry_executor_entry_summary(entry: JsonObject) -> JsonObject:
    return {
        "executor_id": entry.get("executor_id"),
        "executor_type": entry.get("executor_type"),
        "executor_version": entry.get("executor_version"),
        "descriptor_digest": entry.get("descriptor_digest"),
        "descriptor_ref": entry.get("descriptor_ref"),
        "status": entry.get("status"),
    }
