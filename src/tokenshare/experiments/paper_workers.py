"""Worker death harness for paper robustness experiments.

该模块只属于论文实验层：它制造独立 worker 进程死亡、等待现有
LeaseManager 的 lease expiry 规则生效，再记录 replacement attempt。
记录对象围绕 generic AI unit / attempt / dependency graph 设计，不假设
Lean child 数量、证明形状或单层 proof 结构。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Iterable

from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import JsonObject, ProtocolConfig, TaskState, TaskUnit
from tokenshare.core.scheduling import SchedulingDecision
from tokenshare.experiments.paper_models import digest_json
from tokenshare.storage.artifacts import ArtifactStore


WORKER_DEATH_RECORD_SCHEMA_VERSION = "tokenshare.paper_worker_death.v1"
DEPENDENCY_GRAPH_SCHEMA_VERSION = "tokenshare.paper_ai_dependency_graph.v1"
WORKER_ATTEMPT_SCHEMA_VERSION = "tokenshare.paper_worker_attempt_snapshot.v1"


class WorkerDeathKillPoint(str, Enum):
    PROGRESS_25 = "progress_25"
    PROGRESS_50 = "progress_50"
    PROGRESS_75 = "progress_75"


@dataclass(frozen=True, kw_only=True)
class PaperAIUnit:
    task_id: str
    unit_id: str
    unit_kind: str
    dependencies: tuple[str, ...] = ()
    depth: int = 0
    domain: str | None = None
    metadata: JsonObject | None = None
    schema_version: str = "tokenshare.paper_ai_unit.v1"

    def __post_init__(self) -> None:
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("unit_id", self.unit_id)
        _require_non_empty("unit_kind", self.unit_kind)
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must be unique")

    def to_dict(self) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "unit_kind": self.unit_kind,
            "dependencies": list(self.dependencies),
            "depth": self.depth,
            "domain": self.domain,
            "metadata": dict(self.metadata or {}),
        }
        return body


@dataclass(frozen=True, kw_only=True)
class WorkerDeathRecord:
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    target_ai_unit: JsonObject
    dependency_graph: JsonObject
    worker_id: str
    worker_pid: int
    worker_process_exitcode: int | None
    kill_point: WorkerDeathKillPoint | str
    progress_before_kill: int
    worker_started_at: str
    killed_at: str
    initial_lease: JsonObject
    lease_expiry: JsonObject
    dead_attempt: JsonObject
    replacement_worker_id: str
    replacement_worker_pid: int
    replacement_process_exitcode: int | None
    replacement_attempt: JsonObject
    reassignment: JsonObject
    coordinator: JsonObject
    canonical_pollution: bool
    provider_tokens_attributed: int
    created_at: str
    schema_version: str = WORKER_DEATH_RECORD_SCHEMA_VERSION

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "target_ai_unit": dict(self.target_ai_unit),
            "dependency_graph": _json_value(self.dependency_graph),
            "worker_id": self.worker_id,
            "worker_pid": self.worker_pid,
            "worker_process_exitcode": self.worker_process_exitcode,
            "kill_point": _enum_value(self.kill_point),
            "progress_before_kill": self.progress_before_kill,
            "worker_started_at": self.worker_started_at,
            "killed_at": self.killed_at,
            "initial_lease": _json_value(self.initial_lease),
            "lease_expiry": _json_value(self.lease_expiry),
            "dead_attempt": _json_value(self.dead_attempt),
            "replacement_worker_id": self.replacement_worker_id,
            "replacement_worker_pid": self.replacement_worker_pid,
            "replacement_process_exitcode": self.replacement_process_exitcode,
            "replacement_attempt": _json_value(self.replacement_attempt),
            "reassignment": _json_value(self.reassignment),
            "coordinator": _json_value(self.coordinator),
            "canonical_pollution": self.canonical_pollution,
            "provider_tokens_attributed": self.provider_tokens_attributed,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class WorkerDeathOutcome:
    record: WorkerDeathRecord
    record_ref: Any
    schema_version: str = "tokenshare.paper_worker_death_outcome.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "record": self.record.to_dict(),
            "record_ref": self.record_ref.to_dict(),
        }


@dataclass(frozen=True)
class _WorkerProcessResult:
    worker_pid: int
    progress_percent: int
    exitcode: int | None
    stderr: str


def validate_ai_unit_dependency_graph(
    ai_units: Iterable[PaperAIUnit],
) -> JsonObject:
    units = tuple(ai_units)
    if not units:
        raise ValueError("ai_units must not be empty")
    by_id: dict[str, PaperAIUnit] = {}
    for unit in units:
        if unit.unit_id in by_id:
            raise ValueError("unit_ids must be unique")
        by_id[unit.unit_id] = unit
    task_ids = {unit.task_id for unit in units}
    if len(task_ids) != 1:
        raise ValueError("ai_units in one dependency graph must share task_id")

    for unit in units:
        for dependency in unit.dependencies:
            if dependency not in by_id:
                raise ValueError(
                    f"missing dependency: {dependency} -> {unit.unit_id}"
                )
    depths = _calculated_depths(by_id)
    for unit in units:
        if unit.depth != depths[unit.unit_id]:
            raise ValueError(
                f"unit depth does not match dependency graph: {unit.unit_id}"
            )
    dependency_edges = sorted(
        (
            {
                "source_unit_id": dependency,
                "target_unit_id": unit.unit_id,
            }
            for unit in units
            for dependency in unit.dependencies
        ),
        key=lambda edge: (edge["source_unit_id"], edge["target_unit_id"]),
    )
    depended_on = {edge["source_unit_id"] for edge in dependency_edges}
    terminal_unit_ids = sorted(unit_id for unit_id in by_id if unit_id not in depended_on)
    dependency_free_unit_ids = sorted(
        unit.unit_id for unit in units if not unit.dependencies
    )
    body: JsonObject = {
        "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
        "task_id": units[0].task_id,
        "expected_ai_unit_count": len(units),
        "units": [unit.to_dict() for unit in sorted(units, key=lambda item: item.unit_id)],
        "unit_ids": sorted(by_id),
        "dependency_edges": dependency_edges,
        "dependency_free_unit_ids": dependency_free_unit_ids,
        "terminal_unit_ids": terminal_unit_ids,
        "max_depth": max(depths.values()),
    }
    body["graph_digest"] = digest_json(body)
    return body


def run_worker_death_harness(
    *,
    artifact_store: ArtifactStore,
    condition_id: str,
    repeat_id: int,
    run_id: str,
    ai_units: Iterable[PaperAIUnit],
    target_unit_id: str,
    kill_point: WorkerDeathKillPoint | str,
    started_at: str,
    process_tick_seconds: float = 0.05,
    lease_ttl_seconds: int = 10,
    worker_id: str | None = None,
    replacement_worker_id: str | None = None,
) -> WorkerDeathOutcome:
    _require_non_empty("condition_id", condition_id)
    _require_non_empty("run_id", run_id)
    graph = validate_ai_unit_dependency_graph(ai_units)
    units_by_id = {
        unit["unit_id"]: unit
        for unit in graph["units"]
    }
    if target_unit_id not in units_by_id:
        raise ValueError(f"target_unit_id not found in dependency graph: {target_unit_id}")
    target_unit = units_by_id[target_unit_id]
    target = PaperAIUnit(
        task_id=str(target_unit["task_id"]),
        unit_id=str(target_unit["unit_id"]),
        unit_kind=str(target_unit["unit_kind"]),
        dependencies=tuple(str(item) for item in target_unit["dependencies"]),
        depth=int(target_unit["depth"]),
        domain=target_unit.get("domain"),
        metadata=dict(target_unit.get("metadata", {})),
    )
    normalized_kill_point, target_progress = _normalize_kill_point(kill_point)
    safe_unit_id = _safe_id(target.unit_id)
    initial_worker_id = worker_id or f"worker_{safe_unit_id}_initial"
    replacement_id = replacement_worker_id or f"worker_{safe_unit_id}_replacement"

    config = _paper_worker_protocol_config(lease_ttl_seconds=lease_ttl_seconds)
    lease_manager = LeaseManager(protocol_config=config)
    initial_claim = lease_manager.claim(
        decision=_scheduling_decision(
            decision_id=f"decision_{safe_unit_id}_initial",
            target=target,
            worker_id=initial_worker_id,
            created_at=started_at,
            dependency_graph_digest=str(graph["graph_digest"]),
        ),
        lease_id=f"lease_{safe_unit_id}_initial",
        attempt_id=f"attempt_{safe_unit_id}_initial",
        fencing_token=f"fence_{safe_unit_id}_initial",
        now=started_at,
    )

    killed_process = _kill_worker_at_progress(
        worker_id=initial_worker_id,
        unit_id=target.unit_id,
        target_progress=target_progress,
        tick_seconds=process_tick_seconds,
    )
    killed_at = _add_seconds(started_at, max(1, target_progress // 25))
    task_unit = _task_unit_from_ai_unit(target, state=TaskState.PROCESSING, now=started_at)
    expiry = lease_manager.expire(
        lease=initial_claim.lease,
        attempt=initial_claim.running_attempt,
        task_unit=task_unit,
        now=initial_claim.lease.expires_at,
        recovery_action_id=f"recovery_{safe_unit_id}_lease_expired",
        retry_count=1,
    )
    replacement_claim = lease_manager.claim(
        decision=_scheduling_decision(
            decision_id=f"decision_{safe_unit_id}_replacement",
            target=target,
            worker_id=replacement_id,
            created_at=initial_claim.lease.expires_at,
            dependency_graph_digest=str(graph["graph_digest"]),
        ),
        lease_id=f"lease_{safe_unit_id}_replacement",
        attempt_id=f"attempt_{safe_unit_id}_replacement",
        fencing_token=f"fence_{safe_unit_id}_replacement",
        now=initial_claim.lease.expires_at,
    )
    replacement_process = _run_worker_to_completion(
        worker_id=replacement_id,
        unit_id=target.unit_id,
        tick_seconds=process_tick_seconds,
    )

    dead_attempt = _worker_attempt_snapshot(
        role="killed_worker",
        unit_id=target.unit_id,
        attempt_id=initial_claim.running_attempt.attempt_id,
        worker_id=initial_worker_id,
        worker_pid=killed_process.worker_pid,
        lease_id=initial_claim.lease.lease_id,
        core_attempt_before=initial_claim.running_attempt.to_dict(),
        core_attempt_after=expiry.attempt.to_dict(),
        harness_status="worker_died_then_lease_expired",
        started_at=started_at,
        ended_at=initial_claim.lease.expires_at,
        process_exitcode=killed_process.exitcode,
    )
    replacement_attempt = _worker_attempt_snapshot(
        role="replacement_worker",
        unit_id=target.unit_id,
        attempt_id=replacement_claim.running_attempt.attempt_id,
        worker_id=replacement_id,
        worker_pid=replacement_process.worker_pid,
        lease_id=replacement_claim.lease.lease_id,
        core_attempt_before=replacement_claim.running_attempt.to_dict(),
        core_attempt_after=replacement_claim.running_attempt.to_dict(),
        harness_status="replacement_completed",
        started_at=initial_claim.lease.expires_at,
        ended_at=_add_seconds(initial_claim.lease.expires_at, 1),
        process_exitcode=replacement_process.exitcode,
    )
    record = WorkerDeathRecord(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=run_id,
        task_id=target.task_id,
        target_ai_unit=target.to_dict(),
        dependency_graph=graph,
        worker_id=initial_worker_id,
        worker_pid=killed_process.worker_pid,
        worker_process_exitcode=killed_process.exitcode,
        kill_point=normalized_kill_point,
        progress_before_kill=killed_process.progress_percent,
        worker_started_at=started_at,
        killed_at=killed_at,
        initial_lease=initial_claim.lease.to_dict(),
        lease_expiry={
            "schema_version": "tokenshare.paper_worker_lease_expiry.v1",
            "trigger": "lease_expired",
            "lease_id": expiry.lease.lease_id,
            "attempt_id": expiry.attempt.attempt_id,
            "expired_at": initial_claim.lease.expires_at,
            "expired_lease": expiry.lease.to_dict(),
            "superseded_attempt": expiry.attempt.to_dict(),
            "recovery_action": expiry.recovery_action,
            "next_task_state": expiry.next_task_state.value,
        },
        dead_attempt=dead_attempt,
        replacement_worker_id=replacement_id,
        replacement_worker_pid=replacement_process.worker_pid,
        replacement_process_exitcode=replacement_process.exitcode,
        replacement_attempt=replacement_attempt,
        reassignment={
            "schema_version": "tokenshare.paper_worker_reassignment.v1",
            "from_worker_id": initial_worker_id,
            "to_worker_id": replacement_id,
            "reassigned_at": initial_claim.lease.expires_at,
            "target_unit_id": target.unit_id,
            "original_attempt_id": initial_claim.running_attempt.attempt_id,
            "replacement_attempt_id": replacement_claim.running_attempt.attempt_id,
            "replacement_lease_id": replacement_claim.lease.lease_id,
            "dependency_graph_digest": graph["graph_digest"],
        },
        coordinator={
            "schema_version": "tokenshare.paper_worker_coordinator.v1",
            "pid": os.getpid(),
            "survived": True,
            "waited_for_lease_expiry": True,
        },
        canonical_pollution=False,
        provider_tokens_attributed=0,
        created_at=_add_seconds(initial_claim.lease.expires_at, 1),
    )
    record_ref = artifact_store.save_json(
        record.to_dict(),
        artifact_id=f"worker_death_{safe_unit_id}_{_enum_value(normalized_kill_point)}",
        artifact_type="WorkerDeathRecord",
        artifact_schema_id="tokenshare.paper_worker_death",
        artifact_schema_version="v1",
        source={
            "kind": "paper_worker_death_harness",
            "condition_id": condition_id,
            "run_id": run_id,
        },
        metadata={
            "unit_id": target.unit_id,
            "kill_point": _enum_value(normalized_kill_point),
            "dependency_graph_digest": graph["graph_digest"],
        },
        created_at=record.created_at,
    )
    return WorkerDeathOutcome(record=record, record_ref=record_ref)


def _calculated_depths(by_id: dict[str, PaperAIUnit]) -> dict[str, int]:
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(unit_id: str) -> int:
        if unit_id in depths:
            return depths[unit_id]
        if unit_id in visiting:
            raise ValueError("dependency graph contains cycle")
        visiting.add(unit_id)
        unit = by_id[unit_id]
        depth = 0
        if unit.dependencies:
            depth = max(visit(dependency) for dependency in unit.dependencies) + 1
        visiting.remove(unit_id)
        depths[unit_id] = depth
        return depth

    for current_unit_id in by_id:
        visit(current_unit_id)
    return depths


def _normalize_kill_point(
    kill_point: WorkerDeathKillPoint | str,
) -> tuple[WorkerDeathKillPoint, int]:
    value = _enum_value(kill_point)
    normalized = WorkerDeathKillPoint(value)
    return normalized, int(normalized.value.removeprefix("progress_"))


def _paper_worker_protocol_config(*, lease_ttl_seconds: int) -> ProtocolConfig:
    base = ProtocolConfig.default(
        config_id="paper_worker_death_harness_v1",
        artifact_store_uri="memory://paper-worker-death",
        event_log_uri="memory://paper-worker-death",
        metadata={"experiment_layer": "paper_worker_death"},
    )
    return replace(base, lease_ttl_seconds=lease_ttl_seconds, max_retries=3)


def _scheduling_decision(
    *,
    decision_id: str,
    target: PaperAIUnit,
    worker_id: str,
    created_at: str,
    dependency_graph_digest: str,
) -> SchedulingDecision:
    return SchedulingDecision(
        decision_id=decision_id,
        task_id=target.task_id,
        unit_id=target.unit_id,
        client_id=worker_id,
        policy_id="paper_worker_death_harness_v1",
        matched_capabilities=["executor", "ai_unit"],
        lease_kind="exclusive",
        reason="paper_worker_death_harness",
        created_at=created_at,
        input_summary={
            "unit_kind": target.unit_kind,
            "dependency_graph_digest": dependency_graph_digest,
        },
    )


def _task_unit_from_ai_unit(
    unit: PaperAIUnit,
    *,
    state: TaskState,
    now: str,
) -> TaskUnit:
    return TaskUnit(
        unit_id=unit.unit_id,
        task_id=unit.task_id,
        parent_unit_id=None,
        depth=unit.depth,
        unit_type=unit.unit_kind,
        state=state,
        input_refs={},
        canonical_output_refs={},
        required_capabilities={"executor": "ai_api"},
        weight=1.0,
        budget_limit=None,
        deadline=None,
        plugin_payload={"paper_ai_unit": unit.to_dict()},
        metadata={"paper_worker_death_harness": True},
        created_at=now,
        updated_at=now,
    )


def _worker_attempt_snapshot(
    *,
    role: str,
    unit_id: str,
    attempt_id: str,
    worker_id: str,
    worker_pid: int,
    lease_id: str,
    core_attempt_before: JsonObject,
    core_attempt_after: JsonObject,
    harness_status: str,
    started_at: str,
    ended_at: str,
    process_exitcode: int | None,
) -> JsonObject:
    return {
        "schema_version": WORKER_ATTEMPT_SCHEMA_VERSION,
        "role": role,
        "unit_id": unit_id,
        "attempt_id": attempt_id,
        "worker_id": worker_id,
        "worker_pid": worker_pid,
        "lease_id": lease_id,
        "harness_status": harness_status,
        "started_at": started_at,
        "ended_at": ended_at,
        "process_exitcode": process_exitcode,
        "core_attempt_before": core_attempt_before,
        "core_attempt_after": core_attempt_after,
    }


def _kill_worker_at_progress(
    *,
    worker_id: str,
    unit_id: str,
    target_progress: int,
    tick_seconds: float,
) -> _WorkerProcessResult:
    process = _start_progress_worker(
        worker_id=worker_id,
        unit_id=unit_id,
        tick_seconds=tick_seconds,
    )
    progress = 0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            event = json.loads(line)
            progress = int(event["progress_percent"])
            if progress >= target_progress:
                process.terminate()
                return _finish_process(process, progress=progress)
        return _finish_process(process, progress=progress)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def _run_worker_to_completion(
    *,
    worker_id: str,
    unit_id: str,
    tick_seconds: float,
) -> _WorkerProcessResult:
    process = _start_progress_worker(
        worker_id=worker_id,
        unit_id=unit_id,
        tick_seconds=tick_seconds,
    )
    progress = 0
    stdout, stderr = process.communicate(timeout=10)
    for line in stdout.splitlines():
        event = json.loads(line)
        progress = int(event["progress_percent"])
    return _WorkerProcessResult(
        worker_pid=process.pid,
        progress_percent=progress,
        exitcode=process.returncode,
        stderr=stderr,
    )


def _start_progress_worker(
    *,
    worker_id: str,
    unit_id: str,
    tick_seconds: float,
) -> subprocess.Popen[str]:
    script = (
        "import json, os, sys, time\n"
        "worker_id = sys.argv[1]\n"
        "unit_id = sys.argv[2]\n"
        "tick_seconds = float(sys.argv[3])\n"
        "for progress in (25, 50, 75, 100):\n"
        "    print(json.dumps({\n"
        "        'worker_id': worker_id,\n"
        "        'unit_id': unit_id,\n"
        "        'pid': os.getpid(),\n"
        "        'progress_percent': progress,\n"
        "    }, sort_keys=True), flush=True)\n"
        "    time.sleep(tick_seconds)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-u", "-c", script, worker_id, unit_id, str(tick_seconds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_process(
    process: subprocess.Popen[str],
    *,
    progress: int,
) -> _WorkerProcessResult:
    try:
        _stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        _stdout, stderr = process.communicate(timeout=5)
    return _WorkerProcessResult(
        worker_pid=process.pid,
        progress_percent=progress,
        exitcode=process.returncode,
        stderr=stderr,
    )


def _add_seconds(value: str, seconds: int) -> str:
    parsed = _parse_utc(value)
    return (
        parsed.astimezone(UTC)
        .replace(microsecond=0)
        .__add__(timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
