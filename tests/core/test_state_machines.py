import pytest

from tests.phase2_fixtures import make_unit
from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState
from tokenshare.core.state_machines import transition_attempt, transition_lease, transition_task_unit


def test_task_unit_state_machine_allows_phase2_transitions() -> None:
    ready = make_unit(state=TaskState.READY)

    processing = transition_task_unit(
        ready,
        new_state=TaskState.PROCESSING,
        reason="scheduled",
        trigger="scheduler",
        changed_at="2026-06-08T00:00:01Z",
    )

    assert processing.state == TaskState.PROCESSING
    assert processing.updated_at == "2026-06-08T00:00:01Z"

    with pytest.raises(ValueError, match="illegal TaskUnit transition"):
        transition_task_unit(
            ready,
            new_state=TaskState.COMPLETED,
            reason="skip_processing_forbidden",
            trigger="verifier",
            changed_at="2026-06-08T00:00:02Z",
        )


def test_lease_and_attempt_state_machines_keep_lifecycle_boundaries_separate() -> None:
    lease = Lease(
        lease_id="lease_1",
        task_id="task_demo",
        unit_id="unit_ready",
        attempt_id="attempt_1",
        client_id="client_local",
        state=LeaseState.ACTIVE,
        fencing_token="token_1",
        issued_at="2026-06-08T00:00:00Z",
        expires_at="2026-06-08T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="primary",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )
    attempt = Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=AttemptState.CREATED,
        attempt_kind="primary",
        created_at="2026-06-08T00:00:00Z",
    )

    released = transition_lease(
        lease,
        new_state=LeaseState.RELEASED,
        changed_at="2026-06-08T00:01:00Z",
        reason="completed",
    )
    running = transition_attempt(
        attempt,
        new_state=AttemptState.RUNNING,
        changed_at="2026-06-08T00:00:01Z",
        reason="executor_started",
    )

    assert released.state == LeaseState.RELEASED
    assert released.terminated_reason == "completed"
    assert running.state == AttemptState.RUNNING
    assert running.started_at == "2026-06-08T00:00:01Z"

    with pytest.raises(ValueError, match="illegal Lease transition"):
        transition_lease(
            released,
            new_state=LeaseState.ACTIVE,
            changed_at="2026-06-08T00:02:00Z",
            reason="reuse_forbidden",
        )

    with pytest.raises(ValueError, match="illegal Attempt transition"):
        transition_attempt(
            running,
            new_state=AttemptState.CANONICAL,
            changed_at="2026-06-08T00:02:00Z",
            reason="skip_submission_forbidden",
        )


def test_attempt_state_machine_allows_phase3_submission_but_rejects_verifying_state() -> None:
    running = Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at="2026-06-08T00:00:00Z",
        started_at="2026-06-08T00:00:01Z",
    )

    submitted = transition_attempt(
        running,
        new_state=AttemptState.SUBMITTED,
        changed_at="2026-06-08T00:02:00Z",
        reason="execution_submission_recorded",
        environment_summary={"runtime": "python"},
        raw_output_ref=None,
        parsed_output_ref=None,
        candidate_output_refs={},
        log_ref=None,
    )

    assert submitted.state == AttemptState.SUBMITTED
    assert submitted.submitted_at == "2026-06-08T00:02:00Z"
    assert submitted.environment_summary == {"runtime": "python"}
    assert submitted.finished_at is None

    with pytest.raises(ValueError, match="illegal Attempt transition"):
        transition_attempt(
            submitted,
            new_state=AttemptState.VERIFYING,
            changed_at="2026-06-08T00:03:00Z",
            reason="verification_error_must_not_self_loop",
        )

    with pytest.raises(ValueError, match="illegal Attempt transition"):
        transition_attempt(
            submitted,
            new_state=AttemptState.SUBMITTED,
            changed_at="2026-06-08T00:03:00Z",
            reason="verification_error_self_loop_forbidden",
        )


def test_phase4_attempt_verification_rejection_and_canonical_transitions() -> None:
    submitted = Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=AttemptState.SUBMITTED,
        attempt_kind="primary",
        created_at="2026-06-08T00:00:00Z",
        started_at="2026-06-08T00:00:01Z",
        submitted_at="2026-06-08T00:02:00Z",
    )

    verified = transition_attempt(
        submitted,
        new_state=AttemptState.VERIFIED,
        changed_at="2026-06-08T00:03:00Z",
        reason="verification_passed",
    )
    canonical = transition_attempt(
        verified,
        new_state=AttemptState.CANONICAL,
        changed_at="2026-06-08T00:04:00Z",
        reason="canonical_selected",
        metadata={
            "canonical_selection_id": "canonical_selection:task_demo:unit_ready",
            "canonical_output_bundle_digest": "sha256:bundle",
        },
    )
    rejected = transition_attempt(
        submitted,
        new_state=AttemptState.REJECTED,
        changed_at="2026-06-08T00:03:30Z",
        reason="verification_rejected",
    )

    assert verified.state == AttemptState.VERIFIED
    assert verified.finished_at == "2026-06-08T00:03:00Z"
    assert verified.failure_kind is None
    assert canonical.state == AttemptState.CANONICAL
    assert canonical.finished_at == verified.finished_at
    assert canonical.metadata["canonical_selection_id"] == "canonical_selection:task_demo:unit_ready"
    assert rejected.state == AttemptState.REJECTED
    assert rejected.failure_kind == "invalid_output"


def test_phase4_attempt_rejects_direct_submitted_to_canonical() -> None:
    submitted = Attempt(
        attempt_id="attempt_1",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_1",
        client_id="client_local",
        state=AttemptState.SUBMITTED,
        attempt_kind="primary",
        created_at="2026-06-08T00:00:00Z",
    )

    with pytest.raises(ValueError, match="illegal Attempt transition"):
        transition_attempt(
            submitted,
            new_state=AttemptState.CANONICAL,
            changed_at="2026-06-08T00:04:00Z",
            reason="canonical_without_verification_forbidden",
        )


def test_phase4_task_unit_can_complete_from_processing() -> None:
    processing = make_unit(state=TaskState.PROCESSING)

    completed = transition_task_unit(
        processing,
        new_state=TaskState.COMPLETED,
        reason="complete_decision_accepted",
        trigger="phase4_complete_decision",
        changed_at="2026-06-08T00:05:00Z",
    )

    assert completed.state == TaskState.COMPLETED
    assert completed.updated_at == "2026-06-08T00:05:00Z"
