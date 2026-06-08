import pytest

from tests.phase2_fixtures import make_unit
from tokenshare.core.models import Attempt, AttemptState, Lease, LeaseState, TaskState
from tokenshare.core.state_machines import transition_attempt, transition_lease, transition_task_unit


def test_task_unit_state_machine_allows_phase2_transitions_and_rejects_future_completion() -> None:
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
            processing,
            new_state=TaskState.COMPLETED,
            reason="phase4_not_enabled",
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
            reason="phase4_not_enabled",
        )
