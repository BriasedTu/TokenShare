from __future__ import annotations

import os
from dataclasses import replace
from threading import Lock
from time import sleep

from tokenshare.core.models import ProtocolConfig
from tokenshare.local_runtime import (
    ProcessWorkerBackend,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ThreadWorkerBackend,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.local_runtime.test_coordinator_full_lifecycle import (
    _ArtifactExecutor,
    _Clock,
    _ExpandedPluginRuntime,
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


def _runtime(tmp_path, *, max_retries: int = 2):
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
    backend = ThreadWorkerBackend(
        executor=executor,
        capacity=2,
        submitted_at=clock,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_thread_capacity",
            root_input={"mode": "thread_capacity"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
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


def test_process_worker_death_uses_engine_lease_expiry_and_replacement(tmp_path) -> None:
    store, ledger, plugin, clock, coordinator = _runtime(tmp_path)
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
            run_id="run_process_death",
            root_input={"mode": "process_death"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
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
