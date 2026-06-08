"""LeaseManager pure rules for Phase 2."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from tokenshare.core.models import Attempt, AttemptState, JsonObject, Lease, LeaseState, ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.scheduling import SchedulingDecision
from tokenshare.core.state_machines import transition_attempt, transition_lease


@dataclass(frozen=True)
class LeaseClaim:
    """Objects produced by claiming one scheduling decision."""

    lease: Lease
    created_attempt: Attempt
    running_attempt: Attempt


@dataclass(frozen=True)
class LeaseExpiryDecision:
    """Recovery facts produced when an active lease expires."""

    lease: Lease
    attempt: Attempt
    recovery_action: JsonObject
    next_task_state: TaskState


class LeaseManager:
    """Create, heartbeat, and expire leases without touching storage."""

    def __init__(self, *, protocol_config: ProtocolConfig) -> None:
        self.protocol_config = protocol_config

    def claim(
        self,
        *,
        decision: SchedulingDecision,
        lease_id: str,
        attempt_id: str,
        fencing_token: str,
        now: str,
    ) -> LeaseClaim:
        lease = Lease(
            lease_id=lease_id,
            task_id=decision.task_id,
            unit_id=decision.unit_id,
            attempt_id=attempt_id,
            client_id=decision.client_id,
            state=LeaseState.ACTIVE,
            fencing_token=fencing_token,
            issued_at=now,
            expires_at=_add_seconds(now, self.protocol_config.lease_ttl_seconds),
            last_heartbeat_at=None,
            heartbeat_count=0,
            lease_kind=decision.lease_kind,
            terminated_at=None,
            terminated_reason=None,
            metadata={},
        )
        created_attempt = Attempt(
            attempt_id=attempt_id,
            task_id=decision.task_id,
            unit_id=decision.unit_id,
            lease_id=lease_id,
            client_id=decision.client_id,
            state=AttemptState.CREATED,
            attempt_kind=decision.lease_kind,
            created_at=now,
        )
        running_attempt = transition_attempt(
            created_attempt,
            new_state=AttemptState.RUNNING,
            changed_at=now,
            reason="executor_started",
        )
        return LeaseClaim(
            lease=lease,
            created_attempt=created_attempt,
            running_attempt=running_attempt,
        )

    def heartbeat(self, lease: Lease, *, now: str) -> Lease:
        if lease.state != LeaseState.ACTIVE:
            raise ValueError(f"terminal lease cannot heartbeat: {lease.state.value}")
        return replace(
            lease,
            last_heartbeat_at=now,
            heartbeat_count=lease.heartbeat_count + 1,
            expires_at=_add_seconds(now, self.protocol_config.lease_ttl_seconds),
        )

    def expire(
        self,
        *,
        lease: Lease,
        attempt: Attempt,
        task_unit: TaskUnit,
        now: str,
        recovery_action_id: str,
        retry_count: int,
    ) -> LeaseExpiryDecision:
        if lease.state != LeaseState.ACTIVE:
            raise ValueError(f"only Active leases can expire: {lease.state.value}")
        if attempt.state not in {AttemptState.CREATED, AttemptState.RUNNING}:
            raise ValueError(f"only Created or Running attempts can be superseded: {attempt.state.value}")

        expired_lease = transition_lease(
            lease,
            new_state=LeaseState.EXPIRED,
            changed_at=now,
            reason="lease_expired",
        )
        superseded_attempt = transition_attempt(
            attempt,
            new_state=AttemptState.SUPERSEDED,
            changed_at=now,
            reason="lease_expired",
        )
        retry_allowed = retry_count < self.protocol_config.max_retries
        next_task_state = TaskState.READY if retry_allowed else TaskState.FAILED
        reason = "lease_expired_retry" if retry_allowed else "retry_limit_reached"
        recovery_action = {
            "schema_version": "phase2.recovery_action.v1",
            "recovery_action_id": recovery_action_id,
            "task_id": task_unit.task_id,
            "unit_id": task_unit.unit_id,
            "trigger": "lease_expired",
            "lease_id": lease.lease_id,
            "attempt_id": attempt.attempt_id,
            "old_task_state": task_unit.state.value,
            "new_task_state": next_task_state.value,
            "retry_count": retry_count,
            "retry_allowed": retry_allowed,
            "reason": reason,
            "created_at": now,
            "metadata": {},
        }
        return LeaseExpiryDecision(
            lease=expired_lease,
            attempt=superseded_attempt,
            recovery_action=recovery_action,
            next_task_state=next_task_state,
        )


def _add_seconds(value: str, seconds: int) -> str:
    return _format_utc(_parse_utc(value) + timedelta(seconds=seconds))


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
