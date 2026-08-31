from dataclasses import replace

import pytest

from tests.phase2_fixtures import make_config, make_unit
from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import AttemptState, LeaseState, TaskState
from tokenshare.core.scheduling import SchedulingDecision


def test_lease_manager_claims_heartbeats_and_expires_with_retry_recovery() -> None:
    config = make_config()
    manager = LeaseManager(protocol_config=config)
    decision = SchedulingDecision(
        decision_id="decision_1",
        task_id="task_demo",
        unit_id="unit_ready",
        client_id="client_local",
        policy_id="fifo_ready_v1",
        matched_capabilities=["executor"],
        lease_kind="primary",
        reason="ready_and_available",
        created_at="2026-06-08T00:00:00Z",
        input_summary={"ready_queue_size": 1},
    )

    claim = manager.claim(
        decision=decision,
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
        now="2026-06-08T00:00:00Z",
    )
    heartbeat = manager.heartbeat(claim.lease, now="2026-06-08T00:01:00Z")
    expiry = manager.expire(
        lease=heartbeat,
        attempt=claim.running_attempt,
        task_unit=make_unit(state=TaskState.PROCESSING),
        now="2026-06-08T00:06:01Z",
        recovery_action_id="recovery_1",
        retry_count=1,
    )

    assert claim.lease.state == LeaseState.ACTIVE
    assert claim.lease.expires_at == "2026-06-08T00:05:00Z"
    assert claim.running_attempt.state == AttemptState.RUNNING
    assert heartbeat.heartbeat_count == 1
    assert heartbeat.expires_at == "2026-06-08T00:06:00Z"
    assert expiry.lease.state == LeaseState.EXPIRED
    assert expiry.attempt.state == AttemptState.SUPERSEDED
    assert expiry.recovery_action["retry_allowed"] is True
    assert expiry.next_task_state == TaskState.READY


def test_lease_manager_rejects_early_expiry_and_late_heartbeat() -> None:
    config = make_config()
    manager = LeaseManager(protocol_config=config)
    decision = SchedulingDecision(
        decision_id="decision_1",
        task_id="task_demo",
        unit_id="unit_ready",
        client_id="client_local",
        policy_id="fifo_ready_v1",
        matched_capabilities=["executor"],
        lease_kind="primary",
        reason="ready_and_available",
        created_at="2026-06-08T00:00:00Z",
        input_summary={"ready_queue_size": 1},
    )
    claim = manager.claim(
        decision=decision,
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
        now="2026-06-08T00:00:00Z",
    )

    with pytest.raises(ValueError, match="has not reached expires_at"):
        manager.expire(
            lease=claim.lease,
            attempt=claim.running_attempt,
            task_unit=make_unit(state=TaskState.PROCESSING),
            now="2026-06-08T00:01:00Z",
            recovery_action_id="recovery_early",
            retry_count=0,
        )

    with pytest.raises(ValueError, match="cannot heartbeat"):
        manager.heartbeat(claim.lease, now=claim.lease.expires_at)


@pytest.mark.parametrize(
    "mismatch",
    [
        "task_id",
        "unit_id",
        "attempt_id",
        "lease_id",
        "client_id",
    ],
)
def test_lease_manager_rejects_mismatched_recovery_authority(mismatch: str) -> None:
    manager = LeaseManager(protocol_config=make_config())
    decision = SchedulingDecision(
        decision_id="decision_1",
        task_id="task_demo",
        unit_id="unit_ready",
        client_id="client_local",
        policy_id="fifo_ready_v1",
        matched_capabilities=["executor"],
        lease_kind="primary",
        reason="ready_and_available",
        created_at="2026-06-08T00:00:00Z",
        input_summary={"ready_queue_size": 1},
    )
    claim = manager.claim(
        decision=decision,
        lease_id="lease_1",
        attempt_id="attempt_1",
        fencing_token="token_1",
        now="2026-06-08T00:00:00Z",
    )
    lease = claim.lease
    attempt = claim.running_attempt
    task_unit = make_unit(state=TaskState.PROCESSING)
    if mismatch == "task_id":
        lease = replace(lease, task_id="task_other")
    elif mismatch == "unit_id":
        task_unit = replace(task_unit, unit_id="unit_other")
    elif mismatch == "attempt_id":
        lease = replace(lease, attempt_id="attempt_other")
    elif mismatch == "lease_id":
        lease = replace(lease, lease_id="lease_other")
    else:
        lease = replace(lease, client_id="client_other")

    with pytest.raises(ValueError, match="does not match"):
        manager.expire(
            lease=lease,
            attempt=attempt,
            task_unit=task_unit,
            now="2026-06-08T00:05:00Z",
            recovery_action_id="recovery_mismatch",
            retry_count=0,
        )
