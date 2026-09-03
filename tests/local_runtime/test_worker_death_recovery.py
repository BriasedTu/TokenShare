from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from threading import Lock
from time import sleep

import pytest

import tokenshare.local_runtime.workers as worker_module
from tokenshare.core.models import ProtocolConfig
from tokenshare.local_runtime import (
    ProcessWorkerBackend,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ThreadWorkerBackend,
    WorkerTerminationPolicy,
)
from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    LogicalSourceLatencyScheduler,
)
from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.local_runtime.test_coordinator_full_lifecycle import (
    _ArtifactExecutor,
    _Clock,
    _ExpandedPluginRuntime,
)
from tests.phase3_fixtures import (
    make_environment_ref,
    make_executor_descriptor,
    make_output_contract,
    make_plugin_descriptor,
)


class _ConcurrentArtifactExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self._delegate = delegate
        self._lock = Lock()
        self._active = 0
        self.max_active = 0

    def execute(self, request, *, submission_id: str, submitted_at: str):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            sleep(0.03)
            return self._delegate.execute(
                request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
        finally:
            with self._lock:
                self._active -= 1


class _ProcessCaptureExecutor:
    def __init__(self, delegate: _ArtifactExecutor) -> None:
        self._delegate = delegate
        self.accepted_process_results: list[dict[str, str]] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        return self._delegate.execute(
            request,
            submission_id=submission_id,
            submitted_at=submitted_at,
        )

    def export_process_result(self, request, submission):
        return {
            "attempt_id": request.attempt_id,
            "submission_id": submission.submission_id,
        }

    def ingest_process_result(self, result) -> None:
        self.accepted_process_results.append(dict(result))


def _restore_bootstrap_retry_executor(
    marker_path: str,
    artifact_root: str,
) -> _ArtifactExecutor:
    marker = Path(marker_path)
    if not marker.is_file():
        marker.write_text("first bootstrap failed\n", encoding="utf-8")
        raise OSError(6, "simulated invalid bootstrap handle")
    return _ArtifactExecutor(ArtifactStore(Path(artifact_root)))


class _BootstrapRetryExecutor:
    def __init__(self, *, marker_path: Path, artifact_root: Path) -> None:
        self._marker_path = marker_path
        self._artifact_root = artifact_root

    def __reduce__(self):
        return (
            _restore_bootstrap_retry_executor,
            (str(self._marker_path), str(self._artifact_root)),
        )


def _runtime(tmp_path, *, max_retries: int = 2, clock_microsecond: int = 0):
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_worker_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=max_retries,
        lease_ttl_seconds=2,
    )
    clock = _Clock()
    clock._value = clock._value.replace(microsecond=clock_microsecond)
    coordinator = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    )
    return store, ledger, _ExpandedPluginRuntime(config), clock, coordinator


def test_thread_capacity_is_applied_to_units_of_one_runtime_root(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path)
    executor = _ConcurrentArtifactExecutor(_ArtifactExecutor(store))
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    logical_key_by_unit_id: dict[str, str] = {}

    def completion_schedule(request, _submission, _failure_kind):
        metadata = request.task_unit_snapshot.get("metadata", {})
        logical_key = metadata.get("child_logical_key")
        if isinstance(logical_key, str):
            logical_key_by_unit_id[request.unit_id] = logical_key
        return WorkerCompletionSchedule(
            source_latency_ms=(
                300
                if logical_key == "intro"
                else 200
                if logical_key == "summary"
                else 0
            ),
            attempt_ordinal=0,
        )

    backend = ThreadWorkerBackend(
        executor=executor,
        capacity=2,
        submitted_at=scheduler.now_timestamp,
        completion_schedule=completion_schedule,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_thread_capacity",
            root_input={"mode": "thread_capacity"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert executor.max_active == 2
    scheduled_units = [
        event.payload["scheduling_decision"]["unit_id"]
        for event in ledger.read_all()
        if event.event_type == EventType.LEASE_STATE_CHANGED
        and event.payload.get("new_state") == "Active"
    ]
    child_units = [unit_id for unit_id in scheduled_units if unit_id != "unit_ready"]
    assert len(set(child_units)) >= 2
    assert all(
        any(fact.unit_id == unit_id for fact in backend.execution_facts)
        for unit_id in set(child_units)
    )
    observation = result.summary["runtime_observation"]
    projected_fact_ids = {
        (fact["unit_id"], fact["attempt_id"])
        for fact in observation["worker_execution_facts"]
    }
    assert projected_fact_ids == {
        (fact.unit_id, fact.attempt_id) for fact in backend.execution_facts
    }
    assert all(
        fact["started_at"].startswith("2026-07-22T00:00:00")
        and fact["ended_at"].startswith("2026-07-22T00:00:00")
        for fact in observation["worker_execution_facts"]
    )
    assert observation["observed_peak_concurrency"] == 2
    assert observation["runtime_wall_clock_ms"] == 300.0
    child_pop_order = [
        logical_key_by_unit_id[event.unit_id]
        for event in scheduler.pop_history
        if event.unit_id in logical_key_by_unit_id
    ]
    assert child_pop_order == ["summary", "intro"]


def test_process_worker_death_uses_engine_lease_expiry_and_replacement(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(
        tmp_path,
        clock_microsecond=500_126,
    )
    coordinator_pid = os.getpid()
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    attempts_by_unit_id: dict[str, int] = {}

    def completion_schedule(request, _submission, _failure_kind):
        attempt_ordinal = attempts_by_unit_id.get(request.unit_id, 0)
        attempts_by_unit_id[request.unit_id] = attempt_ordinal + 1
        is_child = request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        return WorkerCompletionSchedule(
            source_latency_ms=100 if is_child else 0,
            attempt_ordinal=attempt_ordinal,
        )

    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=2,
        submitted_at=scheduler.now_timestamp,
        terminate_once=lambda request: (
            request.unit_id != "unit_ready"
            and request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        ),
        kill_point="progress_25",
        completion_schedule=completion_schedule,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_process_death",
            root_input={"mode": "process_death"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert os.getpid() == coordinator_pid
    killed = [fact for fact in backend.execution_facts if fact.result_kind == "worker_terminated"]
    assert len(killed) == 1
    assert killed[0].worker_pid not in (None, coordinator_pid)
    assert killed[0].process_exitcode not in (None, 0)

    events = ledger.read_all()
    recovery = next(
        event
        for event in events
        if event.event_type == EventType.RECOVERY_ACTION_RECORDED
        and event.payload["recovery_action"]["trigger"] == "lease_expired"
    )
    dead_unit_id = recovery.payload["recovery_action"]["unit_id"]
    attempt_states = [
        event.payload.get("new_state")
        for event in events
        if event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.payload.get("attempt", {}).get("unit_id") == dead_unit_id
    ]
    unit_states = [
        event.payload.get("task_unit_state_change", {}).get("new_state")
        for event in events
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == dead_unit_id
    ]
    replacement_leases = [
        event.payload["lease"]
        for event in events
        if event.event_type == EventType.LEASE_STATE_CHANGED
        and event.payload.get("new_state") == "Active"
        and event.payload.get("lease", {}).get("unit_id") == dead_unit_id
    ]
    assert "Superseded" in attempt_states
    assert "Ready" in unit_states
    assert len(replacement_leases) == 2
    assert replacement_leases[0]["lease_id"] != replacement_leases[1]["lease_id"]
    death_event = next(
        event
        for event in scheduler.pop_history
        if event.event_kind == "worker_death"
    )
    replacement_event = next(
        event
        for event in scheduler.pop_history
        if event.unit_id == dead_unit_id and event.attempt_ordinal == 1
    )
    assert death_event.unit_id == dead_unit_id
    assert death_event.logical_time_ms == 2000
    assert replacement_event.logical_time_ms == 2100
    dead_unit_events = [
        event for event in scheduler.pop_history if event.unit_id == dead_unit_id
    ]
    assert [event.event_kind for event in dead_unit_events] == [
        "worker_death",
        "retry",
        "requeue",
        "worker_completion",
    ]
    assert [event.logical_time_ms for event in dead_unit_events] == [
        2000,
        2000,
        2000,
        2100,
    ]
    assert recovery.occurred_at == "2026-07-22T00:00:02.500126Z"
    assert replacement_leases[1]["issued_at"] == recovery.occurred_at


def test_process_worker_death_does_not_depend_on_multiprocessing_spawn_pipe(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_spawn_context(_method: str):
        raise OSError(6, "multiprocessing bootstrap handle is invalid")

    monkeypatch.setattr(
        worker_module,
        "get_context",
        broken_spawn_context,
        raising=False,
    )
    store, _ledger, plugin, clock, coordinator = _runtime(tmp_path)
    coordinator_pid = os.getpid()
    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=2,
        submitted_at=clock,
        terminate_once=lambda request: (
            request.unit_id != "unit_ready"
            and request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        ),
        kill_point="progress_25",
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_process_death_without_mp_spawn_pipe",
            root_input={"mode": "process_death"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
        )
    )

    killed = [
        fact
        for fact in backend.execution_facts
        if fact.result_kind == "worker_terminated"
    ]
    assert result.status == "completed"
    assert len(killed) == 1
    assert killed[0].worker_pid not in (None, coordinator_pid)
    assert killed[0].process_exitcode not in (None, 0)


def test_process_backend_retries_bootstrap_before_releasing_executor(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    marker_path = tmp_path / "first-bootstrap-failed"
    backend = ProcessWorkerBackend(
        executor=_BootstrapRetryExecutor(
            marker_path=marker_path,
            artifact_root=artifact_root,
        ),
        capacity=1,
        submitted_at=lambda: "2026-07-25T00:00:00Z",
    )

    outcome = backend.execute_batch(
        (_execution_request(0, planned_ai_unit_id="planned_0"),)
    )[0]

    assert marker_path.read_text(encoding="utf-8") == "first bootstrap failed\n"
    assert outcome.submission is not None
    assert outcome.fact.result_kind == "succeeded"


def test_process_backend_retries_transient_process_creation_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open_process_worker = worker_module._open_process_worker
    launch_count = 0

    def flaky_open_process_worker(arguments):
        nonlocal launch_count
        launch_count += 1
        if launch_count == 1:
            raise OSError(8, "simulated transient process creation failure")
        return original_open_process_worker(arguments)

    monkeypatch.setattr(
        worker_module,
        "_open_process_worker",
        flaky_open_process_worker,
    )
    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(ArtifactStore(tmp_path / "artifacts")),
        capacity=1,
        submitted_at=lambda: "2026-07-25T00:00:00Z",
    )

    outcome = backend.execute_batch(
        (_execution_request(0, planned_ai_unit_id="planned_0"),)
    )[0]

    assert launch_count == 2
    assert outcome.submission is not None
    assert outcome.fact.result_kind == "succeeded"


def test_process_backend_can_terminate_multiple_selected_workers_and_return_sidecars(
    tmp_path,
) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path)
    executor = _ProcessCaptureExecutor(_ArtifactExecutor(store))
    backend = ProcessWorkerBackend(
        executor=executor,
        capacity=2,
        submitted_at=clock,
        terminate_once=lambda request: (
            request.unit_id != "unit_ready"
            and request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        ),
        termination_limit=2,
        kill_point="progress_50",
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_process_multiple_deaths",
            root_input={"mode": "process_multiple_deaths"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
        )
    )

    killed = [
        fact
        for fact in backend.execution_facts
        if fact.result_kind == "worker_terminated"
    ]
    assert result.status == "completed"
    assert len(killed) == 2
    assert len({fact.unit_id for fact in killed}) == 2
    assert {
        fact.attempt_id for fact in killed
    }.issubset(
        {
            item["attempt_id"]
            for item in executor.accepted_process_results
        }
    )
    assert all(
        any(
            fact.unit_id == killed_fact.unit_id
            and fact.result_kind == "succeeded"
            and fact.attempt_id != killed_fact.attempt_id
            for fact in backend.execution_facts
        )
        for killed_fact in killed
    )


def test_process_backend_arms_worker_death_only_after_real_completed_progress(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=2,
        submitted_at=lambda: "2026-07-25T00:00:00Z",
        termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("planned_1",),
            termination_count_target=1,
            kill_point="progress_50",
            total_planned_ai_unit_count=4,
            process_timeout_seconds=30.0,
        ),
    )

    first_batch = backend.execute_batch(
        (
            _execution_request(0, planned_ai_unit_id="planned_0"),
            _execution_request(1, planned_ai_unit_id="planned_1"),
        )
    )

    assert first_batch[0].submission is not None
    killed = first_batch[1]
    assert killed.submission is None
    assert killed.fact.result_kind == "worker_terminated"
    assert killed.fact.kill_progress_target_ratio == pytest.approx(0.5)
    assert killed.fact.kill_progress_completed_ai_unit_count == 2
    assert killed.fact.kill_progress_total_ai_unit_count == 4
    assert killed.fact.kill_progress_actual_ratio == pytest.approx(0.5)
    assert killed.fact.kill_progress_observed_at
    assert killed.fact.kill_progress_error is None


def test_process_backend_kills_each_frozen_target_before_reusing_replacement(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    backend = ProcessWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=1,
        submitted_at=lambda: "2026-07-25T00:00:00Z",
        termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("planned_0", "planned_1"),
            termination_count_target=3,
            kill_point="progress_25",
            total_planned_ai_unit_count=2,
            process_timeout_seconds=30.0,
        ),
    )

    first = backend.execute_batch(
        (_execution_request(0, planned_ai_unit_id="planned_0"),)
    )[0]
    assert first.fact.result_kind == "worker_terminated"
    first_replacement = backend.execute_batch(
        (_execution_request(1, planned_ai_unit_id="planned_0"),)
    )[0]
    assert first_replacement.fact.result_kind == "succeeded"

    second = backend.execute_batch(
        (_execution_request(2, planned_ai_unit_id="planned_1"),)
    )[0]
    assert second.fact.result_kind == "worker_terminated"
    repeated_target = backend.execute_batch(
        (_execution_request(3, planned_ai_unit_id="planned_1"),)
    )[0]
    assert repeated_target.fact.result_kind == "worker_terminated"
    final_replacement = backend.execute_batch(
        (_execution_request(4, planned_ai_unit_id="planned_1"),)
    )[0]
    assert final_replacement.fact.result_kind == "succeeded"

    killed = [
        fact
        for fact in backend.execution_facts
        if fact.result_kind == "worker_terminated"
    ]
    assert len(killed) == 3
    assert len({fact.worker_pid for fact in killed}) == 3
    assert [fact.unit_id for fact in killed] == ["unit_0", "unit_2", "unit_3"]


def _execution_request(
    index: int,
    *,
    planned_ai_unit_id: str,
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=f"request_{index}",
        task_id="task_progress",
        unit_id=f"unit_{index}",
        attempt_id=f"attempt_{index}",
        lease_id=f"lease_{index}",
        fencing_token=index + 1,
        plugin=make_plugin_descriptor().to_dict(),
        executor=make_executor_descriptor().to_dict(),
        registry_snapshot_id="registry_snapshot_progress",
        allocation_decision={"client_id": "client_local"},
        capability_snapshot={"executor": "mock_ai"},
        task_unit_snapshot={"unit_id": f"unit_{index}"},
        input_artifact_refs={},
        output_contract=make_output_contract(),
        hard_requirements={"executor": "mock_ai"},
        soft_hints={"planned_ai_unit_id": planned_ai_unit_id},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={},
        created_at="2026-07-25T00:00:00Z",
    )
