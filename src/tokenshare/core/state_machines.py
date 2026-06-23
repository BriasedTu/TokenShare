"""Phase 2 protocol state machine helpers."""

from __future__ import annotations

from dataclasses import replace

from tokenshare.core.models import ArtifactRef, Attempt, AttemptState, JsonObject, Lease, LeaseState, TaskState, TaskUnit


_TASK_UNIT_TRANSITIONS = {
    (TaskState.CREATED, TaskState.READY),
    (TaskState.CREATED, TaskState.BLOCKED),
    (TaskState.BLOCKED, TaskState.READY),
    (TaskState.READY, TaskState.PROCESSING),
    (TaskState.PROCESSING, TaskState.READY),
    (TaskState.PROCESSING, TaskState.FAILED),
    (TaskState.READY, TaskState.CANCELLED),
    (TaskState.PROCESSING, TaskState.CANCELLED),
    (TaskState.BLOCKED, TaskState.CANCELLED),
}

_LEASE_TRANSITIONS = {
    (LeaseState.ACTIVE, LeaseState.ACTIVE),
    (LeaseState.ACTIVE, LeaseState.RELEASED),
    (LeaseState.ACTIVE, LeaseState.EXPIRED),
    (LeaseState.ACTIVE, LeaseState.REVOKED),
}

_ATTEMPT_TRANSITIONS = {
    (AttemptState.CREATED, AttemptState.RUNNING),
    (AttemptState.CREATED, AttemptState.SUPERSEDED),
    (AttemptState.RUNNING, AttemptState.SUBMITTED),
    (AttemptState.RUNNING, AttemptState.FAILED),
    (AttemptState.RUNNING, AttemptState.SUPERSEDED),
}


def transition_task_unit(
    task_unit: TaskUnit,
    *,
    new_state: TaskState | str,
    reason: str,
    trigger: str,
    changed_at: str,
) -> TaskUnit:
    """Return a TaskUnit copy after validating a Phase 2 lifecycle transition."""

    target_state = TaskState(new_state)
    if (task_unit.state, target_state) not in _TASK_UNIT_TRANSITIONS:
        raise ValueError(
            f"illegal TaskUnit transition: {task_unit.state.value} -> {target_state.value}"
        )
    if not reason or not trigger:
        raise ValueError("TaskUnit transition requires reason and trigger")
    return replace(task_unit, state=target_state, updated_at=changed_at)


def transition_lease(
    lease: Lease,
    *,
    new_state: LeaseState | str,
    changed_at: str,
    reason: str,
) -> Lease:
    """Return a Lease copy after validating a Phase 2 lease transition."""

    target_state = LeaseState(new_state)
    if (lease.state, target_state) not in _LEASE_TRANSITIONS:
        raise ValueError(f"illegal Lease transition: {lease.state.value} -> {target_state.value}")
    if target_state == LeaseState.ACTIVE:
        return lease
    return replace(
        lease,
        state=target_state,
        terminated_at=changed_at,
        terminated_reason=reason,
    )


def transition_attempt(
    attempt: Attempt,
    *,
    new_state: AttemptState | str,
    changed_at: str,
    reason: str,
    environment_summary: JsonObject | None = None,
    raw_output_ref: ArtifactRef | None = None,
    parsed_output_ref: ArtifactRef | None = None,
    candidate_output_refs: dict[str, ArtifactRef] | None = None,
    log_ref: ArtifactRef | None = None,
    failure_kind: str | None = None,
    failure_reason: str | None = None,
    superseded_by_attempt_id: str | None = None,
) -> Attempt:
    """Return an Attempt copy after validating a protocol attempt transition."""

    target_state = AttemptState(new_state)
    if (attempt.state, target_state) not in _ATTEMPT_TRANSITIONS:
        raise ValueError(
            f"illegal Attempt transition: {attempt.state.value} -> {target_state.value}"
        )
    if not reason:
        raise ValueError("Attempt transition requires reason")
    if target_state == AttemptState.RUNNING:
        return replace(attempt, state=target_state, started_at=attempt.started_at or changed_at)
    if target_state == AttemptState.SUBMITTED:
        return replace(
            attempt,
            state=target_state,
            submitted_at=changed_at,
            environment_summary=environment_summary or attempt.environment_summary,
            raw_output_ref=raw_output_ref,
            parsed_output_ref=parsed_output_ref,
            candidate_output_refs=candidate_output_refs or {},
            log_ref=log_ref,
        )
    if target_state == AttemptState.FAILED:
        return replace(
            attempt,
            state=target_state,
            finished_at=changed_at,
            failure_kind=failure_kind or "executor_error",
            failure_reason=failure_reason or reason,
        )
    return replace(
        attempt,
        state=target_state,
        finished_at=changed_at,
        superseded_by_attempt_id=superseded_by_attempt_id,
    )
