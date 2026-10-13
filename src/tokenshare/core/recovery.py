"""提交接受性与通用重试的纯协议决策。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState


ACCEPTED = "accepted"
REJECTED = "rejected"

_SUPPORTED_RETRY_TRIGGERS = {
    "lease_expired",
    "executor_error",
    "parser_failure",
    "verification_rejected",
    "checker_rejected",
    "no_return",
}

_RETRY_ATTEMPT_STATES = {
    "lease_expired": AttemptState.SUPERSEDED,
    "no_return": AttemptState.SUPERSEDED,
    "executor_error": AttemptState.FAILED,
    "parser_failure": AttemptState.REJECTED,
    "verification_rejected": AttemptState.REJECTED,
    "checker_rejected": AttemptState.REJECTED,
}


class SubmissionForAcceptance(Protocol):
    """接受性规则所需的最小 submission 结构。"""

    task_id: str
    unit_id: str
    attempt_id: str
    lease_id: str
    fencing_token: str
    submitted_at: str


@dataclass(frozen=True)
class SubmissionAcceptanceDecision:
    """submission 是否可推进 attempt 的纯决策。"""

    acceptance_status: str
    rejection_reason: str | None


@dataclass(frozen=True)
class RetryDecision:
    """一次失败是否重试以及应写入的状态结论。"""

    trigger: str
    retry_allowed: bool
    next_task_state: TaskState
    superseded_attempt_state: AttemptState
    retry_count: int
    reason: str

    def __post_init__(self) -> None:
        if self.trigger not in _SUPPORTED_RETRY_TRIGGERS:
            raise ValueError("invalid RetryDecision: unsupported trigger")
        if isinstance(self.retry_count, bool) or not isinstance(self.retry_count, int):
            raise ValueError("invalid RetryDecision: retry_count must be a non-bool integer")
        if self.retry_count < 0:
            raise ValueError("invalid RetryDecision: retry_count must be non-negative")
        if not isinstance(self.next_task_state, TaskState) or not isinstance(
            self.superseded_attempt_state,
            AttemptState,
        ):
            raise ValueError("invalid RetryDecision: state fields must use enums")
        if self.superseded_attempt_state != _RETRY_ATTEMPT_STATES[self.trigger]:
            raise ValueError("invalid RetryDecision: attempt state does not match trigger")

        expected_task_state = TaskState.READY if self.retry_allowed else TaskState.FAILED
        if self.next_task_state != expected_task_state:
            raise ValueError("invalid RetryDecision: task state does not match retry_allowed")

        expected_reason = f"{self.trigger}_retry" if self.retry_allowed else "retry_limit_reached"
        if self.reason != expected_reason:
            raise ValueError("invalid RetryDecision: reason does not match retry conclusion")


def evaluate_submission_acceptance(
    *,
    submission: SubmissionForAcceptance,
    attempt: Attempt,
    lease: Lease,
) -> SubmissionAcceptanceDecision:
    """检查 submission 是否仍由当前 active lease 授权。"""

    if attempt.state != AttemptState.RUNNING:
        return _rejected("attempt_not_running")
    if lease.state != LeaseState.ACTIVE:
        return _rejected("lease_not_active")
    if submission.task_id != attempt.task_id or lease.task_id != attempt.task_id:
        return _rejected("task_id_mismatch")
    if submission.unit_id != attempt.unit_id or lease.unit_id != attempt.unit_id:
        return _rejected("unit_id_mismatch")
    if submission.attempt_id != attempt.attempt_id or lease.attempt_id != attempt.attempt_id:
        return _rejected("attempt_id_mismatch")
    if submission.lease_id != attempt.lease_id or lease.lease_id != attempt.lease_id:
        return _rejected("lease_id_mismatch")
    if submission.fencing_token != lease.fencing_token:
        return _rejected("fencing_token_mismatch")
    if _parse_utc(submission.submitted_at) > _parse_utc(lease.expires_at):
        return _rejected("lease_deadline_exceeded")
    return SubmissionAcceptanceDecision(
        acceptance_status=ACCEPTED,
        rejection_reason=None,
    )


def evaluate_retry(*, trigger: str, retry_count: int, max_retries: int) -> RetryDecision:
    """按统一 retry budget 计算 recovery 结论，不读写任何外部状态。"""

    if trigger not in _SUPPORTED_RETRY_TRIGGERS:
        raise ValueError(f"unsupported recovery trigger: {trigger}")
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        raise ValueError("retry_count must be a non-bool integer")
    if isinstance(max_retries, bool) or not isinstance(max_retries, int):
        raise ValueError("max_retries must be a non-bool integer")
    if retry_count < 0:
        raise ValueError("retry_count must be non-negative")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    # ``max_retries`` 表示初始 attempt 之后允许创建的 replacement 数量。
    # 因此第一次 recovery 的 retry_count=1 在 max_retries=1 时仍应放行。
    retry_allowed = retry_count <= max_retries
    return RetryDecision(
        trigger=trigger,
        retry_allowed=retry_allowed,
        next_task_state=TaskState.READY if retry_allowed else TaskState.FAILED,
        superseded_attempt_state=_RETRY_ATTEMPT_STATES[trigger],
        retry_count=retry_count,
        reason=f"{trigger}_retry" if retry_allowed else "retry_limit_reached",
    )


def _rejected(reason: str) -> SubmissionAcceptanceDecision:
    return SubmissionAcceptanceDecision(
        acceptance_status=REJECTED,
        rejection_reason=reason,
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
