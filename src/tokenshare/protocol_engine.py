"""Minimal Phase 2 application service for event-backed scheduling flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import Attempt, AttemptState, ClientRecord, JsonObject, Lease, LeaseState, ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.scheduling import Scheduler, SchedulingDecision
from tokenshare.core.state_machines import transition_task_unit
from tokenshare.core.task_graph import TaskGraph
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


class ProtocolEngine:
    """Write core Phase 2 decisions to the append-only event ledger."""

    def __init__(
        self,
        *,
        event_ledger: EventLedger,
        protocol_config: ProtocolConfig,
        scheduler: Scheduler | None = None,
        lease_manager: LeaseManager | None = None,
    ) -> None:
        self._event_ledger = event_ledger
        self._protocol_config = protocol_config
        self._scheduler = scheduler or Scheduler()
        self._lease_manager = lease_manager or LeaseManager(protocol_config=protocol_config)

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
        decision = self._scheduler.select_next(
            graph=graph,
            clients=clients,
            protocol_config=self._protocol_config,
            active_leases_by_unit_id=active_leases_by_unit_id or {},
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
