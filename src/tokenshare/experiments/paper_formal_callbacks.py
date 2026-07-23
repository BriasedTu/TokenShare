"""Experiment-specific runtime strategies for formal paper execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    freeze_worker_death_plan,
    record_worker_death_observation,
)
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class FormalStrategyResult:
    ordered_case_ids: tuple[str, ...]
    outcomes: tuple[Any, ...]
    events: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    status: str = "completed"
    fault_records: tuple[dict[str, Any], ...] = ()
    replacement_attempts: tuple[Any, ...] = ()
    model_execution_records: tuple[dict[str, Any], ...] = ()
    schema_version: str = "tokenshare.paper_formal_strategy.v1"


@dataclass(frozen=True, kw_only=True)
class FormalAblationResult:
    mode: str
    runtime_flags: dict[str, bool]
    events: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    schema_version: str = "tokenshare.paper_formal_ablation_strategy.v1"


def run_exp1_normal_strategy(
    *,
    ordered_case_ids: Sequence[str],
    execute_case: Callable[[str, int], Any],
) -> FormalStrategyResult:
    """按冻结顺序执行完整 Exp1 selection。"""

    return run_scheduled_cases(
        ordered_case_ids=ordered_case_ids,
        worker_count=1,
        execute_case=execute_case,
    )


def run_scheduled_cases(
    *,
    ordered_case_ids: Sequence[str],
    worker_count: int,
    execute_case: Callable[[str, int], Any],
    supported_worker_counts: Sequence[int] | None = None,
) -> FormalStrategyResult:
    """把 worker_count 传给每个 root runtime，并从 unit 事实派生指标。"""

    case_ids = tuple(str(case_id) for case_id in ordered_case_ids)
    if worker_count < 1:
        raise ValueError("worker_count must be positive")
    if supported_worker_counts is not None and worker_count not in {
        int(item) for item in supported_worker_counts
    }:
        return FormalStrategyResult(
            ordered_case_ids=case_ids,
            outcomes=(),
            events=(),
            metrics={
                "worker_count": worker_count,
                "observed_max_parallel_slots": 0,
                "wall_clock_ms": 0,
                "critical_path_ms": 0,
                "provider_latency_sum_ms": 0,
                "provider_error_count": 0,
                "applicability": "unsupported_worker_level",
            },
            status="unsupported_worker_level",
        )
    if not case_ids:
        return FormalStrategyResult(
            ordered_case_ids=(),
            outcomes=(),
            events=(),
            metrics={
                "worker_count": worker_count,
                "observed_max_parallel_slots": 0,
                "wall_clock_ms": 0,
                "critical_path_ms": 0,
                "provider_latency_sum_ms": 0,
                "provider_error_count": 0,
                "applicability": "empty_selection",
            },
        )

    ordered_outcomes = tuple(
        execute_case(case_id, worker_count) for case_id in case_ids
    )
    records_by_case = {
        case_id: _runtime_records(outcome)
        for case_id, outcome in zip(case_ids, ordered_outcomes, strict=True)
    }
    all_records = tuple(
        record for case_id in case_ids for record in records_by_case[case_id]
    )
    ordered_events = tuple(
        event
        for outcome in ordered_outcomes
        for event in _protocol_events(outcome)
    )
    intervals = tuple(
        (_timestamp(record["started_at"]), _timestamp(record["ended_at"]))
        for record in all_records
    )
    observed_max_slots = _observed_parallel_slots(intervals)
    if observed_max_slots > worker_count:
        raise ValueError("runtime facts exceed configured worker capacity")
    wall_clock_ms = (
        round(
            (
                max(end for _start, end in intervals)
                - min(start for start, _end in intervals)
            )
            * 1000.0,
            3,
        )
        if intervals
        else 0.0
    )
    critical_path_ms = round(
        sum(_critical_path_ms(records_by_case[case_id]) for case_id in case_ids),
        3,
    )
    provider_latency_sum_ms = sum(
        _numeric_field(outcome, "provider_latency_ms") for outcome in ordered_outcomes
    )
    provider_error_count = sum(
        _field(outcome, "provider_error_kind") is not None
        for outcome in ordered_outcomes
    )
    return FormalStrategyResult(
        ordered_case_ids=case_ids,
        outcomes=ordered_outcomes,
        events=ordered_events,
        metrics={
            "worker_count": worker_count,
            "observed_max_parallel_slots": observed_max_slots,
            "condition_started_at": (
                min(record["started_at"] for record in all_records)
                if all_records
                else None
            ),
            "condition_ended_at": (
                max(record["ended_at"] for record in all_records)
                if all_records
                else None
            ),
            "wall_clock_ms": wall_clock_ms,
            "critical_path_ms": critical_path_ms,
            "provider_latency_sum_ms": provider_latency_sum_ms,
            "provider_error_count": provider_error_count,
            "throughput_completed_units_per_second": (
                sum(record.get("result_kind") == "succeeded" for record in all_records)
                / (wall_clock_ms / 1000.0)
                if wall_clock_ms > 0
                else 0.0
            ),
            "dependency_edges": [
                {
                    "case_id": case_id,
                    "source_unit_id": dependency,
                    "target_unit_id": record["unit_id"],
                }
                for case_id in case_ids
                for record in records_by_case[case_id]
                for dependency in record["dependencies"]
            ],
            "applicability": "supported",
        },
    )


def run_exp3_post_ai_strategy(
    *,
    attempts: Sequence[Any],
    selected_target_ai_unit_ids: Sequence[str],
    fault_type: str,
    inject_fault: Callable[[Any, str], Mapping[str, Any]],
    execute_replacement: Callable[[Any], Any] | None,
    approved_identity: Mapping[str, Any],
) -> FormalStrategyResult:
    """对已持久化 provider output 注入 Exp3 fault，并按需恢复。"""

    selected = {str(item) for item in selected_target_ai_unit_ids}
    events: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    replacements: list[Any] = []
    for attempt in attempts:
        unit_id = str(_required_field(attempt, "unit_id"))
        if unit_id not in selected:
            continue
        raw_ref = _required_field(attempt, "raw_output_ref")
        provenance_ref = _required_field(attempt, "provenance_ref")
        if not isinstance(raw_ref, Mapping) or not isinstance(provenance_ref, Mapping):
            raise ValueError("fault injection requires persisted raw and provenance refs")
        events.append(
            {
                "event_type": "EXPERIMENT_PROVIDER_RAW_OBSERVED",
                "attempt_id": _required_field(attempt, "attempt_id"),
                "unit_id": unit_id,
                "raw_output_ref": dict(raw_ref),
                "provenance_ref": dict(provenance_ref),
            }
        )
        fault = dict(inject_fault(attempt, fault_type))
        fault.setdefault("attempt_id", _required_field(attempt, "attempt_id"))
        fault.setdefault("unit_id", unit_id)
        fault.setdefault("fault_type", fault_type)
        faults.append(fault)
        events.append(
            {
                "event_type": "EXPERIMENT_FAULT_INJECTED",
                "attempt_id": _required_field(attempt, "attempt_id"),
                "unit_id": unit_id,
                "fault_type": fault_type,
            }
        )
        if fault.get("requires_replacement") is not True:
            continue
        if execute_replacement is None:
            events.append(
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_REQUIRED",
                    "attempt_id": _required_field(attempt, "attempt_id"),
                    "unit_id": unit_id,
                }
            )
            continue
        replacement = execute_replacement(attempt)
        _validate_fixed_identity(replacement, approved_identity)
        replacements.append(replacement)
        events.extend(
            (
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_ATTEMPT_STARTED",
                    "attempt_id": _required_field(replacement, "attempt_id"),
                    "unit_id": unit_id,
                },
                {
                    "event_type": "EXPERIMENT_REPLACEMENT_ACCEPTED",
                    "attempt_id": _required_field(replacement, "attempt_id"),
                    "unit_id": unit_id,
                },
            )
        )
    return FormalStrategyResult(
        ordered_case_ids=(),
        outcomes=tuple(attempts),
        events=tuple(events),
        metrics={
            "selected_target_count": len(selected),
            "injected_fault_count": len(faults),
            "replacement_attempt_count": len(replacements),
        },
        fault_records=tuple(faults),
        replacement_attempts=tuple(replacements),
    )


def run_exp3_worker_death_strategy(
    *,
    artifact_root: str | Path,
    condition_id: str,
    repeat_id: int,
    task_id: str,
    ai_unit_ids: Sequence[str],
    dead_worker_count: int,
    kill_point: WorkerDeathKillPoint | str,
    started_at: str,
    process_tick_seconds: float = 0.01,
    runtime_observations: Sequence[Mapping[str, Any]] | None = None,
) -> FormalStrategyResult:
    """冻结 kill plan，并只从 runtime/backend 事实投影实验记录。"""

    del process_tick_seconds
    unit_ids = tuple(str(item) for item in ai_unit_ids)
    if dead_worker_count < 1 or dead_worker_count > len(unit_ids):
        raise ValueError("dead_worker_count exceeds available AI units")
    units = tuple(
        PaperAIUnit(
            task_id=task_id,
            unit_id=unit_id,
            unit_kind="formal_adapter_ai_unit",
            dependencies=(),
            depth=0,
        )
        for unit_id in unit_ids
    )
    plan = freeze_worker_death_plan(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=f"{condition_id}-{task_id}-worker-death",
        ai_units=units,
        target_unit_ids=unit_ids[:dead_worker_count],
        kill_point=kill_point,
    )
    plan_body = plan.to_dict()
    plan_event = {
        "event_type": "EXPERIMENT_WORKER_DEATH_PLAN_FROZEN",
        "condition_id": condition_id,
        "task_id": task_id,
        "plan_digest": digest_json(plan_body),
        "selected_target_unit_ids": list(plan.selected_target_unit_ids),
        "occurred_at": started_at,
    }
    if runtime_observations is None:
        return FormalStrategyResult(
            ordered_case_ids=(task_id,),
            outcomes=(),
            events=(plan_event,),
            metrics={
                "worker_death_target_count": dead_worker_count,
                "worker_death_count": 0,
                "replacement_attempt_count": 0,
                "coordinator_survived": False,
                "applicability": "awaiting_runtime_evidence",
            },
            status="planned",
        )
    observations = tuple(runtime_observations)
    if len(observations) != dead_worker_count:
        raise ValueError("runtime observation count must match dead_worker_count")
    store = ArtifactStore(Path(artifact_root))
    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [plan_event]
    replacements: list[dict[str, Any]] = []
    for target_unit_id, observation in zip(
        plan.selected_target_unit_ids,
        observations,
        strict=True,
    ):
        outcome = record_worker_death_observation(
            artifact_store=store,
            plan=plan,
            target_unit_id=target_unit_id,
            worker_fact=_required_field(observation, "worker_fact"),
            replacement_fact=_required_field(observation, "replacement_fact"),
            protocol_events=_required_field(observation, "protocol_events"),
            coordinator_pid=int(_required_field(observation, "coordinator_pid")),
            created_at=str(_field(observation, "created_at") or started_at),
        )
        record = outcome.record.to_dict()
        record["record_ref"] = outcome.record_ref.to_dict()
        records.append(record)
        replacements.append(dict(record["replacement_attempt"]))
        events.append(
            {
                "event_type": "EXPERIMENT_WORKER_DEATH_OBSERVED",
                "condition_id": condition_id,
                "task_id": task_id,
                "unit_id": target_unit_id,
                "record_ref": outcome.record_ref.to_dict(),
                "protocol_event_refs": list(record["protocol_event_refs"]),
                "occurred_at": record["created_at"],
            }
        )
    return FormalStrategyResult(
        ordered_case_ids=(task_id,),
        outcomes=(),
        events=tuple(events),
        metrics={
            "worker_death_count": len(records),
            "replacement_attempt_count": len(records),
            "coordinator_survived": all(
                record["coordinator"]["survived"] is True for record in records
            ),
            "applicability": "runtime_evidence_observed",
        },
        fault_records=tuple(records),
        replacement_attempts=tuple(replacements),
    )


def run_exp4_ablation_strategy(
    *,
    mode: str,
    adapter_observation: Mapping[str, Any],
) -> FormalAblationResult:
    """只在 Experiment 4 wrapper 边界改变指定机制。"""

    normalized_mode = str(mode).upper()
    supported = {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    }
    if normalized_mode not in supported:
        raise ValueError("unsupported Experiment 4 mode")
    flags = {
        "default_protocol_behavior": normalized_mode == "FULL",
        "wrong_canonical_exposed": normalized_mode == "NO_VERIFICATION",
        "raw_only_exposed": normalized_mode == "NO_PARSER_POLICY",
        "stuck_after_rejection": normalized_mode == "NO_REQUEUE",
        "premature_merge_attempted": normalized_mode == "NO_MERGE_GATE",
        "slot_mismatch_exposed": normalized_mode == "NO_SLOT_INTEGRITY",
        "deterministic_validity_audit_retained": True,
    }
    applicable = normalized_mode != "FULL" and _ablation_applicable(
        normalized_mode,
        adapter_observation,
    )
    exposed = int(applicable)
    escaped = int(
        applicable
        and normalized_mode
        in {"NO_VERIFICATION", "NO_PARSER_POLICY", "NO_SLOT_INTEGRITY"}
        and adapter_observation.get("deterministic_validity") is False
    )
    event = {
        "event_type": "EXPERIMENT_ABLATION_OBSERVED",
        "mode": normalized_mode,
        "disabled_mechanism": _disabled_mechanism(normalized_mode),
        "applicable": applicable,
        "protocol_event_refs": [
            dict(ref)
            for ref in adapter_observation.get("protocol_event_refs", ())
            if isinstance(ref, Mapping)
        ],
        "artifact_refs": [
            dict(ref)
            for ref in adapter_observation.get("artifact_refs", ())
            if isinstance(ref, Mapping)
        ],
        "deterministic_validity": adapter_observation.get(
            "deterministic_validity"
        ),
    }
    return FormalAblationResult(
        mode=normalized_mode,
        runtime_flags=flags,
        events=(event,),
        metrics={
            "applicable": applicable,
            "exposed_error_count": exposed,
            "escaped_error_count": escaped,
            "final_deterministic_validity": adapter_observation.get(
                "deterministic_validity"
            ),
        },
    )


def run_exp5_identity_strategy(
    *,
    attempts: Sequence[Any],
    approved_identity: Mapping[str, Any],
    condition_id: str,
    cohort_member_id: str,
) -> FormalStrategyResult:
    """验证 Exp5 首次与恢复 attempts 始终使用冻结 entry。"""

    records: list[dict[str, Any]] = []
    for attempt in attempts:
        _validate_fixed_identity(attempt, approved_identity)
        records.append(
            {
                "schema_version": "tokenshare.paper_formal_model_execution.v1",
                "condition_id": condition_id,
                "cohort_member_id": cohort_member_id,
                "attempt_id": str(_required_field(attempt, "attempt_id")),
                "unit_id": str(_required_field(attempt, "unit_id")),
                "provider": str(_required_field(attempt, "provider")),
                "model": str(_required_field(attempt, "model")),
                "entry_id": str(_required_field(attempt, "entry_id")),
                "request_ref": _field(attempt, "request_ref"),
                "raw_output_ref": _field(attempt, "raw_output_ref"),
                "parsed_output_ref": _field(attempt, "parsed_output_ref"),
                "parse_failure_ref": _field(attempt, "parse_failure_ref"),
                "provenance_ref": _field(attempt, "provenance_ref"),
                "usage_ref": _field(attempt, "usage_ref"),
                "model_execution_ref": _field(attempt, "model_execution_ref"),
            }
        )
    return FormalStrategyResult(
        ordered_case_ids=(),
        outcomes=tuple(attempts),
        events=(),
        metrics={"model_execution_record_count": len(records)},
        model_execution_records=tuple(records),
    )


def _ablation_applicable(mode: str, observation: Mapping[str, Any]) -> bool:
    field_by_mode = {
        "NO_VERIFICATION": "candidate_rejected",
        "NO_PARSER_POLICY": "parse_failed",
        "NO_REQUEUE": "replacement_created",
        "NO_MERGE_GATE": "merge_gate_blocked",
        "NO_SLOT_INTEGRITY": "slot_binding_valid",
    }
    field = field_by_mode.get(mode)
    if field is None:
        return False
    if mode == "NO_SLOT_INTEGRITY":
        return observation.get(field) is True
    return observation.get(field) is True


def _disabled_mechanism(mode: str) -> str | None:
    return {
        "FULL": None,
        "NO_VERIFICATION": "verification",
        "NO_PARSER_POLICY": "parser_policy",
        "NO_REQUEUE": "requeue",
        "NO_MERGE_GATE": "merge_gate",
        "NO_SLOT_INTEGRITY": "slot_integrity",
    }[mode]


def _validate_fixed_identity(attempt: Any, approved: Mapping[str, Any]) -> None:
    expected = {
        "provider": approved.get("provider", approved.get("provider_family")),
        "model": approved.get("model", approved.get("provider_model_id")),
        "entry_id": approved.get("entry_id", approved.get("model_entry_id")),
    }
    for field_name, expected_value in expected.items():
        if expected_value is None:
            raise ValueError(f"approved identity is missing {field_name}")
        if _field(attempt, field_name) != expected_value:
            raise ValueError(f"model failover detected for {field_name}")


def _runtime_records(outcome: Any) -> tuple[dict[str, Any], ...]:
    raw_records = _field(outcome, "runtime_records") or ()
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_records:
        if isinstance(raw, Mapping):
            record = dict(raw)
        else:
            to_dict = getattr(raw, "to_dict", None)
            if not callable(to_dict) or not isinstance(to_dict(), Mapping):
                raise TypeError("runtime record must be a mapping or expose to_dict")
            record = dict(to_dict())
        unit_id = record.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("runtime record unit_id is required")
        if unit_id in seen:
            raise ValueError("runtime record unit_id must be unique within one root")
        seen.add(unit_id)
        for timestamp_field in ("started_at", "ended_at"):
            if not isinstance(record.get(timestamp_field), str):
                raise ValueError(f"runtime record {timestamp_field} is required")
            _timestamp(record[timestamp_field])
        if _timestamp(record["ended_at"]) < _timestamp(record["started_at"]):
            raise ValueError("runtime record ended_at precedes started_at")
        dependencies = record.get("dependencies", ())
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError("runtime record dependencies must be a sequence")
        record["dependencies"] = [str(item) for item in dependencies]
        records.append(record)
    return tuple(records)


def _protocol_events(outcome: Any) -> tuple[dict[str, Any], ...]:
    raw_events = _field(outcome, "protocol_events") or ()
    events: list[dict[str, Any]] = []
    for raw in raw_events:
        if isinstance(raw, Mapping):
            events.append(dict(raw))
            continue
        to_dict = getattr(raw, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("protocol event must be a mapping or expose to_dict")
        body = to_dict()
        if not isinstance(body, Mapping):
            raise TypeError("protocol event to_dict must return a mapping")
        events.append(dict(body))
    return tuple(events)


def _observed_parallel_slots(intervals: Sequence[tuple[float, float]]) -> int:
    boundaries = sorted(
        (
            boundary
            for started_at, ended_at in intervals
            for boundary in ((started_at, 1), (ended_at, -1))
        ),
        key=lambda item: (item[0], item[1]),
    )
    active = 0
    observed = 0
    for _timestamp_value, delta in boundaries:
        active += delta
        observed = max(observed, active)
    return observed


def _critical_path_ms(records: Sequence[Mapping[str, Any]]) -> float:
    if not records:
        return 0.0
    by_id = {str(record["unit_id"]): record for record in records}
    memo: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(unit_id: str) -> float:
        if unit_id in memo:
            return memo[unit_id]
        if unit_id in visiting:
            raise ValueError("runtime dependency graph contains a cycle")
        visiting.add(unit_id)
        record = by_id[unit_id]
        dependency_costs: list[float] = []
        for dependency in record["dependencies"]:
            if dependency not in by_id:
                raise ValueError("runtime dependency graph references an unknown unit")
            dependency_costs.append(visit(dependency))
        duration_ms = round(
            (
                _timestamp(str(record["ended_at"]))
                - _timestamp(str(record["started_at"]))
            )
            * 1000.0,
            3,
        )
        visiting.remove(unit_id)
        memo[unit_id] = duration_ms + max(dependency_costs, default=0.0)
        return memo[unit_id]

    return max(visit(unit_id) for unit_id in by_id)


def _timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _required_field(value: Any, field_name: str) -> Any:
    result = _field(value, field_name)
    if result is None:
        raise ValueError(f"required field is missing: {field_name}")
    return result


def _numeric_field(value: Any, field_name: str) -> float:
    result = _field(value, field_name)
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return float(result)
    return 0.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
