"""论文 worker-death 的冻结计划、选择和 runtime 事实投影。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from tokenshare.core.models import JsonObject
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
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "unit_kind": self.unit_kind,
            "dependencies": list(self.dependencies),
            "depth": self.depth,
            "domain": self.domain,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, kw_only=True)
class WorkerDeathPlan:
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    dependency_graph: JsonObject
    selected_target_unit_ids: tuple[str, ...]
    kill_point: WorkerDeathKillPoint
    progress_percent: int
    schema_version: str = "tokenshare.paper_worker_death_plan.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "dependency_graph": _json_value(self.dependency_graph),
            "selected_target_unit_ids": list(self.selected_target_unit_ids),
            "kill_point": self.kill_point.value,
            "progress_percent": self.progress_percent,
        }


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
    protocol_event_refs: tuple[str, ...]
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
            "protocol_event_refs": list(self.protocol_event_refs),
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
                raise ValueError(f"missing dependency: {dependency} -> {unit.unit_id}")
    depths = _calculated_depths(by_id)
    for unit in units:
        if unit.depth != depths[unit.unit_id]:
            raise ValueError(f"unit depth does not match dependency graph: {unit.unit_id}")
    dependency_edges = sorted(
        (
            {"source_unit_id": dependency, "target_unit_id": unit.unit_id}
            for unit in units
            for dependency in unit.dependencies
        ),
        key=lambda edge: (edge["source_unit_id"], edge["target_unit_id"]),
    )
    depended_on = {edge["source_unit_id"] for edge in dependency_edges}
    body: JsonObject = {
        "schema_version": DEPENDENCY_GRAPH_SCHEMA_VERSION,
        "task_id": units[0].task_id,
        "expected_ai_unit_count": len(units),
        "units": [unit.to_dict() for unit in sorted(units, key=lambda item: item.unit_id)],
        "unit_ids": sorted(by_id),
        "dependency_edges": dependency_edges,
        "dependency_free_unit_ids": sorted(
            unit.unit_id for unit in units if not unit.dependencies
        ),
        "terminal_unit_ids": sorted(
            unit_id for unit_id in by_id if unit_id not in depended_on
        ),
        "max_depth": max(depths.values()),
    }
    body["graph_digest"] = digest_json(body)
    return body


def freeze_worker_death_plan(
    *,
    condition_id: str,
    repeat_id: int,
    run_id: str,
    ai_units: Iterable[PaperAIUnit],
    target_unit_ids: Iterable[str],
    kill_point: WorkerDeathKillPoint | str,
) -> WorkerDeathPlan:
    """冻结预注册 kill selection；不创建 lease、attempt 或协议状态。"""

    _require_non_empty("condition_id", condition_id)
    _require_non_empty("run_id", run_id)
    graph = validate_ai_unit_dependency_graph(ai_units)
    selected = tuple(str(item) for item in target_unit_ids)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("target_unit_ids must be non-empty and unique")
    known = set(str(item) for item in graph["unit_ids"])
    missing = [unit_id for unit_id in selected if unit_id not in known]
    if missing:
        raise ValueError(f"target_unit_ids not found in dependency graph: {missing}")
    normalized, progress = _normalize_kill_point(kill_point)
    return WorkerDeathPlan(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=run_id,
        task_id=str(graph["task_id"]),
        dependency_graph=graph,
        selected_target_unit_ids=selected,
        kill_point=normalized,
        progress_percent=progress,
    )


def record_worker_death_observation(
    *,
    artifact_store: ArtifactStore,
    plan: WorkerDeathPlan,
    target_unit_id: str,
    worker_fact: Mapping[str, Any] | object,
    replacement_fact: Mapping[str, Any] | object,
    protocol_events: Iterable[Mapping[str, Any] | object],
    coordinator_pid: int,
    created_at: str,
) -> WorkerDeathOutcome:
    """把 backend 事实和 engine ledger 事件投影成实验记录。"""

    if target_unit_id not in plan.selected_target_unit_ids:
        raise ValueError("target_unit_id is not selected by worker death plan")
    killed = _mapping(worker_fact, "worker_fact")
    replacement = _mapping(replacement_fact, "replacement_fact")
    if killed.get("unit_id") != target_unit_id or replacement.get("unit_id") != target_unit_id:
        raise ValueError("worker facts must reference the selected target unit")
    if killed.get("result_kind") != "worker_terminated":
        raise ValueError("worker_fact must prove worker_terminated")
    if replacement.get("result_kind") != "succeeded":
        raise ValueError("replacement_fact must prove succeeded")
    if killed.get("attempt_id") == replacement.get("attempt_id"):
        raise ValueError("replacement attempt must be distinct")

    events = tuple(_event_mapping(event) for event in protocol_events)
    evidence = _engine_worker_death_evidence(
        events=events,
        unit_id=target_unit_id,
        initial_attempt_id=_required_str(killed, "attempt_id"),
        initial_lease_id=_required_str(killed, "lease_id"),
        replacement_attempt_id=_required_str(replacement, "attempt_id"),
        replacement_lease_id=_required_str(replacement, "lease_id"),
    )
    units_by_id = {
        str(unit["unit_id"]): dict(unit)
        for unit in plan.dependency_graph["units"]
    }
    initial_lease = dict(evidence["initial_lease"]["payload"]["lease"])
    expired_lease = dict(evidence["expired_lease"]["payload"]["lease"])
    superseded_attempt = dict(evidence["superseded"]["payload"]["attempt"])
    recovery_action = dict(evidence["recovery"]["payload"]["recovery_action"])
    replacement_lease = dict(evidence["replacement_lease"]["payload"]["lease"])
    worker_id = _required_str(killed, "worker_id")
    replacement_worker_id = _required_str(replacement, "worker_id")
    worker_pid = _positive_int(killed, "worker_pid")
    replacement_worker_pid = _positive_int(replacement, "worker_pid")
    worker_exitcode = _required_int(killed, "process_exitcode")
    replacement_exitcode = _required_int(replacement, "process_exitcode")
    if worker_exitcode == 0 or replacement_exitcode != 0:
        raise ValueError("worker process facts do not prove death and replacement success")
    if worker_pid == replacement_worker_pid:
        raise ValueError("killed and replacement workers must be distinct processes")
    started_at = str(killed.get("started_at") or initial_lease.get("issued_at") or created_at)
    killed_at = str(killed.get("ended_at") or evidence["expired_lease"].get("occurred_at") or created_at)
    replacement_started_at = str(
        replacement.get("started_at")
        or replacement_lease.get("issued_at")
        or evidence["replacement_lease"].get("occurred_at")
        or created_at
    )
    replacement_ended_at = str(replacement.get("ended_at") or created_at)
    record = WorkerDeathRecord(
        condition_id=plan.condition_id,
        repeat_id=plan.repeat_id,
        run_id=plan.run_id,
        task_id=plan.task_id,
        target_ai_unit=units_by_id[target_unit_id],
        dependency_graph=plan.dependency_graph,
        worker_id=worker_id,
        worker_pid=worker_pid,
        worker_process_exitcode=worker_exitcode,
        kill_point=plan.kill_point,
        progress_before_kill=plan.progress_percent,
        worker_started_at=started_at,
        killed_at=killed_at,
        initial_lease=initial_lease,
        lease_expiry={
            "schema_version": "tokenshare.paper_worker_lease_expiry.v1",
            "trigger": "lease_expired",
            "lease_id": expired_lease["lease_id"],
            "attempt_id": superseded_attempt["attempt_id"],
            "expired_at": evidence["expired_lease"].get("occurred_at")
            or expired_lease.get("expires_at"),
            "expired_lease": expired_lease,
            "superseded_attempt": superseded_attempt,
            "recovery_action": recovery_action,
            "next_task_state": "Ready",
        },
        dead_attempt=_attempt_snapshot(
            role="killed_worker",
            fact=killed,
            core_attempt=superseded_attempt,
            harness_status="worker_died_then_lease_expired",
            started_at=started_at,
            ended_at=str(evidence["expired_lease"].get("occurred_at") or killed_at),
        ),
        replacement_worker_id=replacement_worker_id,
        replacement_worker_pid=replacement_worker_pid,
        replacement_process_exitcode=replacement_exitcode,
        replacement_attempt=_attempt_snapshot(
            role="replacement_worker",
            fact=replacement,
            core_attempt={
                "attempt_id": replacement_lease["attempt_id"],
                "lease_id": replacement_lease["lease_id"],
                "unit_id": replacement_lease["unit_id"],
                "state": "Succeeded",
            },
            harness_status="replacement_completed",
            started_at=replacement_started_at,
            ended_at=replacement_ended_at,
        ),
        reassignment={
            "schema_version": "tokenshare.paper_worker_reassignment.v1",
            "from_worker_id": worker_id,
            "to_worker_id": replacement_worker_id,
            "reassigned_at": evidence["replacement_lease"].get("occurred_at")
            or replacement_started_at,
            "target_unit_id": target_unit_id,
            "original_attempt_id": killed["attempt_id"],
            "replacement_attempt_id": replacement["attempt_id"],
            "replacement_lease_id": replacement["lease_id"],
            "dependency_graph_digest": plan.dependency_graph["graph_digest"],
        },
        coordinator={
            "schema_version": "tokenshare.paper_worker_coordinator.v1",
            "pid": coordinator_pid,
            "survived": True,
            "waited_for_lease_expiry": True,
        },
        canonical_pollution=False,
        provider_tokens_attributed=0,
        protocol_event_refs=tuple(
            str(evidence[name]["event_id"])
            for name in (
                "initial_lease",
                "expired_lease",
                "superseded",
                "ready",
                "recovery",
                "replacement_lease",
            )
        ),
        created_at=created_at,
    )
    record_ref = artifact_store.save_json(
        record.to_dict(),
        artifact_id=(
            f"worker_death_{_safe_id(target_unit_id)}_{plan.kill_point.value}"
        ),
        artifact_type="WorkerDeathRecord",
        artifact_schema_id="tokenshare.paper_worker_death",
        artifact_schema_version="v1",
        source={
            "kind": "paper_worker_runtime_projection",
            "condition_id": plan.condition_id,
            "run_id": plan.run_id,
        },
        metadata={
            "unit_id": target_unit_id,
            "kill_point": plan.kill_point.value,
            "dependency_graph_digest": plan.dependency_graph["graph_digest"],
        },
        created_at=created_at,
    )
    return WorkerDeathOutcome(record=record, record_ref=record_ref)


def run_worker_death_harness(**kwargs: Any) -> WorkerDeathOutcome:
    """兼容导出：只转发 runtime 事实投影，不执行进程或协议状态迁移。"""

    return record_worker_death_observation(**kwargs)


def _engine_worker_death_evidence(
    *,
    events: tuple[JsonObject, ...],
    unit_id: str,
    initial_attempt_id: str,
    initial_lease_id: str,
    replacement_attempt_id: str,
    replacement_lease_id: str,
) -> dict[str, JsonObject]:
    evidence: dict[str, JsonObject] = {}
    for event in events:
        event_type = event.get("event_type")
        payload = _mapping_or_empty(event.get("payload"))
        if event_type == "LEASE_STATE_CHANGED":
            lease = _mapping_or_empty(payload.get("lease"))
            if lease.get("unit_id") != unit_id:
                continue
            if lease.get("lease_id") == initial_lease_id:
                if payload.get("new_state") == "Active":
                    evidence["initial_lease"] = event
                elif payload.get("new_state") == "Expired":
                    evidence["expired_lease"] = event
            if (
                lease.get("lease_id") == replacement_lease_id
                and lease.get("attempt_id") == replacement_attempt_id
                and payload.get("new_state") == "Active"
            ):
                evidence["replacement_lease"] = event
        elif event_type == "ATTEMPT_STATE_CHANGED":
            attempt = _mapping_or_empty(payload.get("attempt"))
            if (
                attempt.get("unit_id") == unit_id
                and attempt.get("attempt_id") == initial_attempt_id
                and payload.get("new_state") == "Superseded"
            ):
                evidence["superseded"] = event
        elif event_type == "TASK_UNIT_STATE_CHANGED" and event.get("object_id") == unit_id:
            state_change = _mapping_or_empty(payload.get("task_unit_state_change"))
            if payload.get("new_state") == "Ready" or state_change.get("new_state") == "Ready":
                evidence["ready"] = event
        elif event_type == "RECOVERY_ACTION_RECORDED":
            action = _mapping_or_empty(payload.get("recovery_action"))
            if (
                action.get("unit_id") == unit_id
                and action.get("attempt_id") == initial_attempt_id
                and action.get("trigger") == "lease_expired"
                and action.get("retry_allowed") is True
            ):
                evidence["recovery"] = event
    required = {
        "initial_lease",
        "expired_lease",
        "superseded",
        "ready",
        "recovery",
        "replacement_lease",
    }
    if set(evidence) != required:
        missing = sorted(required - set(evidence))
        raise ValueError(f"engine worker-death evidence is incomplete: {missing}")
    return evidence


def _attempt_snapshot(
    *,
    role: str,
    fact: Mapping[str, Any],
    core_attempt: JsonObject,
    harness_status: str,
    started_at: str,
    ended_at: str,
) -> JsonObject:
    return {
        "schema_version": WORKER_ATTEMPT_SCHEMA_VERSION,
        "role": role,
        "unit_id": fact["unit_id"],
        "attempt_id": fact["attempt_id"],
        "worker_id": fact["worker_id"],
        "worker_pid": fact["worker_pid"],
        "lease_id": fact["lease_id"],
        "harness_status": harness_status,
        "started_at": started_at,
        "ended_at": ended_at,
        "process_exitcode": fact["process_exitcode"],
        "core_attempt_before": dict(core_attempt),
        "core_attempt_after": dict(core_attempt),
    }


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
    normalized = WorkerDeathKillPoint(_enum_value(kill_point))
    return normalized, int(normalized.value.removeprefix("progress_"))


def _event_mapping(value: Mapping[str, Any] | object) -> JsonObject:
    body = _mapping(value, "protocol_event")
    if not isinstance(body.get("event_id"), str) or not body["event_id"]:
        raise ValueError("protocol_event event_id is required")
    if not isinstance(body.get("event_type"), str) or not body["event_type"]:
        raise ValueError("protocol_event event_type is required")
    return body


def _mapping(value: Mapping[str, Any] | object, name: str) -> JsonObject:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        body = to_dict()
        if isinstance(body, Mapping):
            return {str(key): _json_value(item) for key, item in body.items()}
    raise TypeError(f"{name} must be a mapping or expose to_dict")


def _mapping_or_empty(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _required_str(body: Mapping[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _positive_int(body: Mapping[str, Any], field: str) -> int:
    value = _required_int(body, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _required_int(body: Mapping[str, Any], field: str) -> int:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


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
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
