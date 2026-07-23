from dataclasses import replace

import pytest

from tests.phase3_fixtures import make_environment_ref
from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState
from tokenshare.core.recovery import RetryDecision, evaluate_retry, evaluate_submission_acceptance
from tokenshare.executors.contracts import ExecutionSubmission


def _attempt(*, state: AttemptState = AttemptState.RUNNING) -> Attempt:
    return Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=state,
        attempt_kind="primary",
        created_at="2026-07-22T00:00:00Z",
        started_at="2026-07-22T00:00:00Z",
    )


def _lease(*, state: LeaseState = LeaseState.ACTIVE) -> Lease:
    return Lease(
        lease_id="lease_1",
        task_id="task_demo",
        unit_id="unit_ready",
        attempt_id="attempt_1",
        client_id="client_local",
        state=state,
        fencing_token="fence_1",
        issued_at="2026-07-22T00:00:00Z",
        expires_at="2026-07-22T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="primary",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )


def _submission(**overrides: object) -> ExecutionSubmission:
    values = {
        "submission_id": "submission_1",
        "request_id": "request_1",
        "task_id": "task_demo",
        "unit_id": "unit_ready",
        "attempt_id": "attempt_1",
        "lease_id": "lease_1",
        "fencing_token": "fence_1",
        "executor_id": "executor_local",
        "executor_version": "0.1.0",
        "result_kind": "succeeded",
        "raw_output_ref": None,
        "parsed_output_ref": None,
        "candidate_output_refs": {},
        "parse_failure_ref": None,
        "log_ref": None,
        "environment_ref": make_environment_ref(),
        "environment_summary": {},
        "provenance_ref": None,
        "usage_summary": {},
        "error": None,
        "submitted_at": "2026-07-22T00:05:00Z",
    }
    values.update(overrides)
    return ExecutionSubmission(**values)


def test_submission_acceptance_accepts_running_attempt_at_lease_deadline() -> None:
    decision = evaluate_submission_acceptance(
        submission=_submission(),
        attempt=_attempt(),
        lease=_lease(),
    )

    assert decision.acceptance_status == "accepted"
    assert decision.rejection_reason is None


@pytest.mark.parametrize(
    ("submission", "attempt", "lease", "reason"),
    [
        (_submission(), _attempt(state=AttemptState.SUBMITTED), _lease(), "attempt_not_running"),
        (_submission(), _attempt(), _lease(state=LeaseState.EXPIRED), "lease_not_active"),
        (_submission(task_id="task_other"), _attempt(), _lease(), "task_id_mismatch"),
        (_submission(unit_id="unit_other"), _attempt(), _lease(), "unit_id_mismatch"),
        (_submission(attempt_id="attempt_other"), _attempt(), _lease(), "attempt_id_mismatch"),
        (_submission(lease_id="lease_other"), _attempt(), _lease(), "lease_id_mismatch"),
        (_submission(fencing_token="stale"), _attempt(), _lease(), "fencing_token_mismatch"),
        (
            _submission(submitted_at="2026-07-22T00:05:00.000001Z"),
            _attempt(),
            _lease(),
            "lease_deadline_exceeded",
        ),
        (
            _submission(),
            _attempt(),
            replace(_lease(), attempt_id="attempt_other"),
            "attempt_id_mismatch",
        ),
    ],
)
def test_submission_acceptance_rejects_invalid_authority(
    submission: ExecutionSubmission,
    attempt: Attempt,
    lease: Lease,
    reason: str,
) -> None:
    decision = evaluate_submission_acceptance(
        submission=submission,
        attempt=attempt,
        lease=lease,
    )

    assert decision.acceptance_status == "rejected"
    assert decision.rejection_reason == reason


@pytest.mark.parametrize(
    ("trigger", "attempt_state"),
    [
        ("lease_expired", AttemptState.SUPERSEDED),
        ("no_return", AttemptState.SUPERSEDED),
        ("executor_error", AttemptState.FAILED),
        ("parser_failure", AttemptState.REJECTED),
        ("verification_rejected", AttemptState.REJECTED),
        ("checker_rejected", AttemptState.REJECTED),
    ],
)
def test_retry_decision_supports_all_runtime_recovery_triggers(
    trigger: str,
    attempt_state: AttemptState,
) -> None:
    decision = evaluate_retry(trigger=trigger, retry_count=1, max_retries=3)

    assert decision.retry_allowed is True
    assert decision.next_task_state == TaskState.READY
    assert decision.superseded_attempt_state == attempt_state
    assert decision.retry_count == 1
    assert decision.reason == f"{trigger}_retry"


def test_retry_decision_fails_task_when_retry_limit_is_reached() -> None:
    decision = evaluate_retry(trigger="executor_error", retry_count=3, max_retries=3)

    assert decision.retry_allowed is False
    assert decision.next_task_state == TaskState.FAILED
    assert decision.superseded_attempt_state == AttemptState.FAILED
    assert decision.retry_count == 3
    assert decision.reason == "retry_limit_reached"


def test_retry_decision_rejects_unknown_trigger_and_invalid_counts() -> None:
    with pytest.raises(ValueError, match="unsupported recovery trigger"):
        evaluate_retry(trigger="unsupported_trigger", retry_count=0, max_retries=3)
    with pytest.raises(ValueError, match="retry_count"):
        evaluate_retry(trigger="no_return", retry_count=-1, max_retries=3)
    with pytest.raises(ValueError, match="max_retries"):
        evaluate_retry(trigger="no_return", retry_count=0, max_retries=-1)


@pytest.mark.parametrize("retry_count", [True, 1.5])
def test_evaluate_retry_rejects_non_integer_retry_count(retry_count: object) -> None:
    with pytest.raises(ValueError, match="retry_count must be a non-bool integer"):
        evaluate_retry(trigger="no_return", retry_count=retry_count, max_retries=3)


@pytest.mark.parametrize("max_retries", [True, 3.5])
def test_evaluate_retry_rejects_non_integer_max_retries(max_retries: object) -> None:
    with pytest.raises(ValueError, match="max_retries must be a non-bool integer"):
        evaluate_retry(trigger="no_return", retry_count=0, max_retries=max_retries)


@pytest.mark.parametrize(
    ("next_task_state", "attempt_state"),
    [
        ("Ready", AttemptState.FAILED),
        (TaskState.READY, "Failed"),
    ],
)
def test_retry_decision_rejects_plain_string_states(
    next_task_state: object,
    attempt_state: object,
) -> None:
    with pytest.raises(ValueError, match="invalid RetryDecision: state fields must use enums"):
        RetryDecision(
            trigger="executor_error",
            retry_allowed=True,
            next_task_state=next_task_state,
            superseded_attempt_state=attempt_state,
            retry_count=0,
            reason="executor_error_retry",
        )


@pytest.mark.parametrize(
    "values",
    [
        {
            "trigger": "unknown",
            "retry_allowed": True,
            "next_task_state": TaskState.READY,
            "superseded_attempt_state": AttemptState.SUPERSEDED,
            "retry_count": 0,
            "reason": "unknown_retry",
        },
        {
            "trigger": "executor_error",
            "retry_allowed": True,
            "next_task_state": TaskState.READY,
            "superseded_attempt_state": AttemptState.FAILED,
            "retry_count": True,
            "reason": "executor_error_retry",
        },
        {
            "trigger": "executor_error",
            "retry_allowed": True,
            "next_task_state": TaskState.READY,
            "superseded_attempt_state": AttemptState.FAILED,
            "retry_count": -1,
            "reason": "executor_error_retry",
        },
        {
            "trigger": "executor_error",
            "retry_allowed": True,
            "next_task_state": TaskState.READY,
            "superseded_attempt_state": AttemptState.SUPERSEDED,
            "retry_count": 0,
            "reason": "executor_error_retry",
        },
        {
            "trigger": "executor_error",
            "retry_allowed": True,
            "next_task_state": TaskState.FAILED,
            "superseded_attempt_state": AttemptState.FAILED,
            "retry_count": 0,
            "reason": "executor_error_retry",
        },
        {
            "trigger": "executor_error",
            "retry_allowed": False,
            "next_task_state": TaskState.FAILED,
            "superseded_attempt_state": AttemptState.FAILED,
            "retry_count": 3,
            "reason": "executor_error_retry",
        },
    ],
)
def test_retry_decision_rejects_directly_constructed_contradictions(
    values: dict,
) -> None:
    with pytest.raises(ValueError, match="invalid RetryDecision"):
        RetryDecision(**values)
