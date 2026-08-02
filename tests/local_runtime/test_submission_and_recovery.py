from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tokenshare.core.models import ArtifactRef, ProtocolConfig
from tokenshare.core.verification import build_verification_report
from tokenshare.executors.ai_api import _stable_slot_index
from tokenshare.experiments.paper_ablation import (
    PaperAblationMode,
    runtime_controls_for_mode,
)
from tokenshare.local_runtime import (
    ProtocolMechanismPolicy,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    SequentialWorkerBackend,
)
from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    LogicalSourceLatencyScheduler,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.local_runtime.test_coordinator_full_lifecycle import (
    _ArtifactExecutor,
    _Clock,
    _DirectCompletePluginRuntime,
    _ExpandedPluginRuntime,
)


class _FailFirstExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self.delegate = delegate
        self.calls = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("executor unavailable")
        return self.delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )


class _ReturnFailureFirstExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self.delegate = delegate
        self.calls = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.calls += 1
        submission = self.delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )
        if self.calls != 1:
            return submission
        return replace(
            submission,
            result_kind="failed",
            error={"kind": "executor_error", "message": "executor returned failure"},
        )


class _NoReturnFirstExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self.delegate = delegate
        self.calls = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.calls += 1
        submission = self.delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )
        if self.calls != 1:
            return submission
        return replace(
            submission,
            result_kind="no_return",
            raw_output_ref=None,
            parsed_output_ref=None,
            candidate_output_refs={},
        )


class _FailChildExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self.delegate = delegate

    def execute(self, request, *, submission_id: str, submitted_at: str):
        if request.unit_id != "unit_ready":
            raise RuntimeError("child executor unavailable")
        return self.delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )


class _RejectFirstSubmissionExecutor:
    def __init__(self, delegate: _ArtifactExecutor, *, rejection: str) -> None:
        self.delegate = delegate
        self.rejection = rejection
        self.calls = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.calls += 1
        submission = self.delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )
        if self.calls != 1:
            return submission
        if self.rejection == "stale_fencing":
            return replace(submission, fencing_token="stale")
        return replace(submission, submitted_at="2026-07-22T00:10:00Z")


class _RejectFirstVerificationPlugin(_ExpandedPluginRuntime):
    first_status = "rejected"

    def __init__(self, config: ProtocolConfig) -> None:
        super().__init__(config)
        self._verification_count = 0

    def verify_submission(self, submission, *, unit):
        self._verification_count += 1
        if self._verification_count != 1:
            return super().verify_submission(submission, unit=unit)
        self.verify_calls.append(unit.unit_id)
        return build_verification_report(
            verification_report_id=f"verification_{submission.attempt_id}",
            task_id=submission.task_id,
            unit_id=submission.unit_id,
            attempt_id=submission.attempt_id,
            submission_id=submission.submission_id,
            submission_event_seq=1,
            candidate_output_refs=submission.candidate_output_refs,
            required_output_names=["answer"],
            output_contract_id="contract_answer",
            validator_policy_id="structured_report_stub_validator_v1",
            plugin_id=self.descriptor.plugin_id,
            plugin_version=self.descriptor.plugin_version,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            status=self.first_status,
            expected_artifact_hashes={
                name: ref.content_hash
                for name, ref in submission.candidate_output_refs.items()
            },
            required_evidence_ref_ids=[],
            available_evidence_ref_ids=[],
            plugin_domain_status=(
                "rejected" if self.first_status == "rejected" else "passed"
            ),
            audit_status="passed",
            verification_environment={"runtime": "pytest"},
            verifier={"verifier_id": "runtime_spy", "verifier_version": "1"},
            started_at=submission.submitted_at,
            completed_at=submission.submitted_at,
        )


class _ErrorFirstVerificationPlugin(_RejectFirstVerificationPlugin):
    first_status = "error"


class _CapacityTwoBackend:
    def __init__(self, delegate: SequentialWorkerBackend) -> None:
        self._delegate = delegate

    @property
    def capacity(self) -> int:
        return 2

    def execute(self, request):
        return self._delegate.execute(request)


class _PollutedReplacementSlotPlugin(_DirectCompletePluginRuntime):
    def build_execution_request(self, unit, *, attempt, lease):
        request = super().build_execution_request(
            unit,
            attempt=attempt,
            lease=lease,
        )
        return replace(request, soft_hints={"replacement_slot": 99})


class _ObserveReplacementSlotExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self._delegate = delegate
        self.observed_slots: list[int] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.observed_slots.append(_stable_slot_index(request, "replacement_slot"))
        if len(self.observed_slots) == 1:
            raise RuntimeError("force replacement attempt")
        return self._delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )


def _runtime(
    tmp_path,
    *,
    max_retries: int,
    plugin_type=_ExpandedPluginRuntime,
    lease_ttl_seconds: int = 300,
):
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_recovery_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=max_retries,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    clock = _Clock()
    return store, ledger, plugin_type(config), clock, ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    )


def _recovery_actions(ledger: EventLedger) -> list[dict]:
    return [
        event.payload["recovery_action"]
        for event in ledger.read_all()
        if event.event_type == EventType.RECOVERY_ACTION_RECORDED
    ]


@pytest.mark.parametrize(
    ("failure_kind", "trigger"),
    [
        ("executor_error", "executor_error"),
        ("stale_fencing", "executor_error"),
        ("expired", "lease_expired"),
        ("verification_rejected", "verification_rejected"),
    ],
)
def test_failure_is_recorded_by_engine_then_replacement_is_scheduled(
    tmp_path,
    failure_kind: str,
    trigger: str,
) -> None:
    plugin_type = (
        _RejectFirstVerificationPlugin
        if failure_kind == "verification_rejected"
        else _ExpandedPluginRuntime
    )
    store, ledger, plugin, clock, coordinator = _runtime(
        tmp_path,
        max_retries=2,
        plugin_type=plugin_type,
    )
    delegate = _ArtifactExecutor(store)
    if failure_kind == "executor_error":
        executor = _FailFirstExecutor(delegate)
    elif failure_kind in {"stale_fencing", "expired"}:
        executor = _RejectFirstSubmissionExecutor(
            delegate,
            rejection=failure_kind,
        )
    else:
        executor = delegate
    backend = SequentialWorkerBackend(executor=executor, submitted_at=clock)

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id=f"run_{failure_kind}",
            root_input={"failure": failure_kind},
            plugin_runtime=plugin,
            worker_backend=backend,
        )
    )

    assert result.status == "completed"
    recovery_actions = _recovery_actions(ledger)
    assert recovery_actions[0]["trigger"] == trigger
    assert recovery_actions[0]["retry_allowed"] is True
    recovery_events = [
        event
        for event in ledger.read_all()
        if event.batch_id == f"recovery_batch:{recovery_actions[0]['recovery_action_id']}"
    ]
    expected_batch_size = 3 if failure_kind == "verification_rejected" else 4
    assert len(recovery_events) == expected_batch_size
    assert {event.batch_size for event in recovery_events} == {expected_batch_size}
    assert recovery_events[0].event_type == EventType.LEASE_STATE_CHANGED
    assert recovery_events[0].payload["old_state"] == "Active"
    assert recovery_events[0].payload["new_state"] == (
        "Expired" if failure_kind == "expired" else "Released"
    )
    if failure_kind == "expired":
        assert recovery_events[0].occurred_at == "2026-07-22T00:10:00Z"
        assert recovery_events[0].occurred_at >= recovery_events[0].payload["lease"][
            "expires_at"
        ]
    _assert_attempt_state_chain(ledger)
    root_attempt_ids = [
        attempt["attempt_id"]
        for attempt in result.summary["attempts"]
        if attempt["unit_id"] == "unit_ready"
    ]
    assert len(root_attempt_ids) == 2
    assert len(set(root_attempt_ids)) == 2
    assert root_attempt_ids == [
        f"run_{failure_kind}_attempt_1",
        f"run_{failure_kind}_attempt_2",
    ]


def test_one_retry_budget_creates_exactly_one_replacement(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=1)
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    attempt_counts: dict[str, int] = {}
    dispatched_requests = []

    def completion_schedule(request, _submission, _failure_kind):
        dispatched_requests.append(request)
        attempt_ordinal = attempt_counts.get(request.unit_id, 0)
        attempt_counts[request.unit_id] = attempt_ordinal + 1
        source_latency_ms = (
            100
            if request.unit_id == "unit_ready" and attempt_ordinal == 0
            else 200
            if request.unit_id == "unit_ready"
            else 0
        )
        return WorkerCompletionSchedule(
            source_latency_ms=source_latency_ms,
            attempt_ordinal=attempt_ordinal,
        )

    backend = SequentialWorkerBackend(
        executor=_FailFirstExecutor(_ArtifactExecutor(store)),
        submitted_at=scheduler.now_timestamp,
        completion_schedule=completion_schedule,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_retry_limit",
            root_input={"failure": "executor_error"},
            plugin_runtime=plugin,
            worker_backend=backend,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert _recovery_actions(ledger)[0]["retry_allowed"] is True
    root_attempt_ids = [
        attempt["attempt_id"]
        for attempt in result.summary["attempts"]
        if attempt["unit_id"] == "unit_ready"
    ]
    assert root_attempt_ids == [
        "run_retry_limit_attempt_1",
        "run_retry_limit_attempt_2",
    ]
    root_events = [
        event for event in scheduler.pop_history if event.unit_id == "unit_ready"
    ]
    assert [event.event_kind for event in root_events] == [
        "worker_completion",
        "retry",
        "requeue",
        "worker_completion",
    ]
    assert [event.attempt_ordinal for event in root_events] == [0, 0, 0, 1]
    assert [event.logical_time_ms for event in root_events] == [100, 100, 100, 300]
    root_requests = [
        item for item in dispatched_requests if item.unit_id == "unit_ready"
    ]
    assert [item.attempt_ordinal for item in root_requests] == [0, 1]
    assert all(
        isinstance(item.source_binding_digest, str)
        and item.source_binding_digest.startswith("sha256:")
        for item in root_requests
    )
    assert len({item.source_binding_digest for item in root_requests}) == 2
    first_recovery = next(
        event
        for event in ledger.read_all()
        if event.event_type == EventType.RECOVERY_ACTION_RECORDED
    )
    replacement_lease = next(
        event
        for event in ledger.read_all()
        if event.event_type == EventType.LEASE_STATE_CHANGED
        and event.payload.get("new_state") == "Active"
        and event.payload.get("lease", {}).get("attempt_id")
        == "run_retry_limit_attempt_2"
    )
    assert first_recovery.occurred_at == "2026-07-22T00:00:00.100000Z"
    assert replacement_lease.occurred_at == first_recovery.occurred_at
    assert result.summary["runtime_observation"]["runtime_wall_clock_ms"] == 300.0


def test_actual_replacement_resolver_uses_persisted_ordinal_and_replays_same_slots(
    tmp_path,
) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(
        tmp_path,
        max_retries=1,
        plugin_type=_PollutedReplacementSlotPlugin,
    )
    executor = _ObserveReplacementSlotExecutor(_ArtifactExecutor(store))

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_replacement_ordinal",
            root_input={"prompt": "replace once"},
            plugin_runtime=plugin,
            worker_backend=SequentialWorkerBackend(
                executor=executor,
                submitted_at=clock,
            ),
        )
    )

    assert result.status == "completed"
    assert executor.observed_slots == [0, 1]

    def replayed_slots():
        slots = []
        for event in ledger.read_verified_snapshot().events:
            if event.event_type != EventType.EXECUTION_REQUEST_RECORDED:
                continue
            request_ref = ArtifactRef.from_dict(event.payload["request_ref"])
            persisted = json.loads(store.read_bytes(request_ref).decode("utf-8"))
            slots.append(
                (
                    persisted["attempt_ordinal"],
                    persisted["soft_hints"]["replacement_slot"],
                )
            )
        return slots

    assert replayed_slots() == [(0, 0), (1, 1)]
    assert replayed_slots() == [(0, 0), (1, 1)]


def test_lease_deadline_retry_and_requeue_share_coordinator_queue(tmp_path) -> None:
    store, ledger, plugin, _, coordinator = _runtime(
        tmp_path,
        max_retries=1,
        plugin_type=_DirectCompletePluginRuntime,
        lease_ttl_seconds=1,
    )
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    attempts = 0

    def completion_schedule(_request, _submission, _failure_kind):
        nonlocal attempts
        ordinal = attempts
        attempts += 1
        return WorkerCompletionSchedule(
            source_latency_ms=0 if ordinal == 0 else 1,
            attempt_ordinal=ordinal,
        )

    backend = SequentialWorkerBackend(
        executor=_NoReturnFirstExecutor(_ArtifactExecutor(store)),
        submitted_at=scheduler.now_timestamp,
        completion_schedule=completion_schedule,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_logical_deadline",
            root_input={"failure": "no_return"},
            plugin_runtime=plugin,
            worker_backend=backend,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert [event.event_kind for event in scheduler.pop_history] == [
        "lease_deadline",
        "retry",
        "requeue",
        "worker_completion",
    ]
    assert [event.logical_time_ms for event in scheduler.pop_history] == [
        1000,
        1000,
        1000,
        1001,
    ]
    assert _recovery_actions(ledger)[0]["trigger"] == "lease_expired"


def test_returned_failure_submission_recovers_from_submitted_attempt(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=2)
    backend = SequentialWorkerBackend(
        executor=_ReturnFailureFirstExecutor(_ArtifactExecutor(store)),
        submitted_at=clock,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_returned_failure",
            root_input={"failure": "returned_executor_error"},
            plugin_runtime=plugin,
            worker_backend=backend,
        )
    )

    assert result.status == "completed"
    first_attempt_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.object_id == "run_returned_failure_attempt_1"
    ]
    assert [
        (event.payload["old_state"], event.payload["new_state"])
        for event in first_attempt_events
    ] == [
        (None, "Created"),
        ("Created", "Running"),
        ("Running", "Submitted"),
        ("Submitted", "Failed"),
    ]
    _assert_attempt_state_chain(ledger)


def test_verification_error_recovers_from_submitted_attempt(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(
        tmp_path,
        max_retries=2,
        plugin_type=_ErrorFirstVerificationPlugin,
    )
    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=clock,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_verification_error",
            root_input={"failure": "verification_error"},
            plugin_runtime=plugin,
            worker_backend=backend,
        )
    )

    assert result.status == "completed"
    assert _recovery_actions(ledger)[0]["trigger"] == "verification_rejected"
    first_attempt_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.object_id == "run_verification_error_attempt_1"
    ]
    assert [
        (event.payload["old_state"], event.payload["new_state"])
        for event in first_attempt_events
    ][-2:] == [("Running", "Submitted"), ("Submitted", "Rejected")]
    _assert_attempt_state_chain(ledger)


def _assert_attempt_state_chain(ledger: EventLedger) -> None:
    latest: dict[str, str] = {}
    for event in ledger.read_all():
        if event.event_type != EventType.ATTEMPT_STATE_CHANGED:
            continue
        old_state = event.payload["old_state"]
        assert old_state == latest.get(event.object_id)
        latest[event.object_id] = event.payload["new_state"]


def test_child_retry_exhaustion_fails_closed_instead_of_returning_processing(
    tmp_path,
) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=1)
    backend = SequentialWorkerBackend(
        executor=_FailChildExecutor(_ArtifactExecutor(store)),
        submitted_at=clock,
    )

    with pytest.raises(RuntimeError, match="child unit failed"):
        coordinator.run_root(
            ProtocolRunRequest(
                run_id="run_child_retry_limit",
                root_input={"failure": "child_executor_error"},
                plugin_runtime=plugin,
                worker_backend=backend,
            )
        )

    assert any(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.payload.get("task_unit_state_change", {}).get("new_state")
        == "Failed"
        and event.object_id != "unit_ready"
        for event in ledger.read_all()
    )
    assert not any(
        event.event_type == EventType.SETTLEMENT_RECORDED
        for event in ledger.read_all()
    )


def test_task7_no_verification_skips_plugin_gate_but_uses_engine_canonical(
    tmp_path,
) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=2)
    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=clock,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_no_verification",
            root_input={"mode": "NO_VERIFICATION"},
            plugin_runtime=plugin,
            worker_backend=backend,
            mechanism_policy=replace(
                ProtocolMechanismPolicy(),
                verification_enabled=False,
            ),
        )
    )

    assert result.status == "completed"
    assert plugin.verify_calls == []
    verification_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.VERIFICATION_RECORDED
    ]
    assert verification_events
    assert {
        event.payload["verification_report"]["validator_policy_id"]
        for event in verification_events
    } == {plugin.descriptor.validator_policy_id}
    assert all(
        event.payload["verification_report"]["metadata"] == {
            "ablation_mode": "NO_VERIFICATION",
            "domain_verifier_invoked": False,
        }
        for event in verification_events
    )
    assert any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        for event in ledger.read_all()
    )


def test_task7_no_requeue_stops_after_engine_records_recovery(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=2)
    executor = _FailFirstExecutor(_ArtifactExecutor(store))
    controls = runtime_controls_for_mode(PaperAblationMode.NO_REQUEUE)

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_no_requeue",
            root_input={"mode": "NO_REQUEUE"},
            plugin_runtime=plugin,
            worker_backend=SequentialWorkerBackend(
                executor=executor,
                submitted_at=clock,
            ),
            mechanism_policy=controls.mechanism_policy,
            hooks=controls.hooks,
        )
    )

    assert executor.calls == 1
    actions = _recovery_actions(ledger)
    assert len(actions) == 1
    assert actions[0]["retry_allowed"] is True
    assert result.status == "ready"
    observation = result.summary["runtime_hook_observations"][0]
    assert observation["kind"] == "EXPERIMENT_ABLATION_GATE_APPLIED"
    assert observation["payload"]["disabled_mechanism"] == "requeue"
    assert observation["payload"]["protocol_event_refs"]
    assert observation["observation_digest"].startswith("sha256:")
    assert not any(
        event.event_type == EventType.SETTLEMENT_RECORDED
        for event in ledger.read_all()
    )


def test_task3_runtime_rejects_worker_capacity_above_one(tmp_path) -> None:
    store, _, plugin, clock, coordinator = _runtime(tmp_path, max_retries=2)
    backend = _CapacityTwoBackend(
        SequentialWorkerBackend(
            executor=_ArtifactExecutor(store),
            submitted_at=clock,
        )
    )

    with pytest.raises(ValueError, match="single-worker"):
        coordinator.run_root(
            ProtocolRunRequest(
                run_id="run_capacity_two",
                root_input={"mode": "capacity_two"},
                plugin_runtime=plugin,
                worker_backend=backend,
            )
        )


def test_task3_runtime_rejects_empty_run_id_before_registration(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path, max_retries=2)
    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=clock,
    )

    with pytest.raises(ValueError, match="run_id"):
        coordinator.run_root(
            ProtocolRunRequest(
                run_id="",
                root_input={"mode": "empty_run_id"},
                plugin_runtime=plugin,
                worker_backend=backend,
            )
        )

    assert ledger.read_all() == []
