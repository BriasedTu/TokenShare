"""Experiment-specific runtime strategies for formal paper execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, current_thread
from time import perf_counter
from typing import Any

from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    run_worker_death_harness,
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
    execute_case: Callable[[str, str], Any],
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
    execute_case: Callable[[str, str], Any],
    supported_worker_counts: Sequence[int] | None = None,
) -> FormalStrategyResult:
    """用实际线程 slot 调度 root cases，并保留冻结返回顺序。"""

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

    state_lock = Lock()
    events_by_index: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    outcomes_by_index: dict[int, Any] = {}
    active_slots = 0
    observed_max_slots = 0

    def run_one(index: int, case_id: str) -> Any:
        nonlocal active_slots, observed_max_slots
        worker_id = current_thread().name
        started_at = _utc_now()
        started_tick = perf_counter()
        with state_lock:
            active_slots += 1
            observed_max_slots = max(observed_max_slots, active_slots)
        start_event = {
            "event_type": "AI_UNIT_STARTED",
            "case_id": case_id,
            "unit_id": case_id,
            "worker_id": worker_id,
            "started_at": started_at,
            "dependencies": [],
        }
        try:
            outcome = execute_case(case_id, worker_id)
            return outcome
        finally:
            ended_at = _utc_now()
            duration_ms = max(0.001, (perf_counter() - started_tick) * 1000.0)
            end_event = {
                "event_type": "AI_UNIT_ENDED",
                "case_id": case_id,
                "unit_id": case_id,
                "worker_id": worker_id,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "dependencies": [],
            }
            with state_lock:
                active_slots -= 1
                events_by_index[index] = (start_event, end_event)

    condition_started_at = _utc_now()
    condition_started_tick = perf_counter()
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="paper-formal-worker",
    ) as executor:
        futures = {
            executor.submit(run_one, index, case_id): index
            for index, case_id in enumerate(case_ids)
        }
        for future in as_completed(futures):
            outcomes_by_index[futures[future]] = future.result()
    merge_started_at = _utc_now()
    merge_started_tick = perf_counter()
    merge_ended_at = _utc_now()
    merge_duration_ms = max(0.001, (perf_counter() - merge_started_tick) * 1000.0)
    wall_clock_ms = max(0.001, (perf_counter() - condition_started_tick) * 1000.0)
    condition_ended_at = _utc_now()

    ordered_outcomes = tuple(outcomes_by_index[index] for index in range(len(case_ids)))
    ordered_events = tuple(
        event
        for index in range(len(case_ids))
        for event in events_by_index[index]
    )
    merge_events = (
        {
            "event_type": "MERGE_GATE_OPENED",
            "started_at": merge_started_at,
            "dependencies": list(case_ids),
        },
        {
            "event_type": "MERGE_GATE_COMPLETED",
            "started_at": merge_started_at,
            "ended_at": merge_ended_at,
            "duration_ms": merge_duration_ms,
            "dependencies": list(case_ids),
        },
    )
    longest_unit_ms = max(
        float(events_by_index[index][1]["duration_ms"])
        for index in range(len(case_ids))
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
        events=ordered_events + merge_events,
        metrics={
            "worker_count": worker_count,
            "observed_max_parallel_slots": observed_max_slots,
            "condition_started_at": condition_started_at,
            "condition_ended_at": condition_ended_at,
            "wall_clock_ms": wall_clock_ms,
            "critical_path_ms": longest_unit_ms + merge_duration_ms,
            "provider_latency_sum_ms": provider_latency_sum_ms,
            "provider_error_count": provider_error_count,
            "dependency_edges": [
                {"source_unit_id": case_id, "target_unit_id": "merge_gate"}
                for case_id in case_ids
            ],
            "merge_gate_duration_ms": merge_duration_ms,
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
                "event_type": "PROVIDER_RAW_PERSISTED",
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
                "event_type": "FAULT_INJECTED",
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
                    "event_type": "REPLACEMENT_REQUIRED",
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
                    "event_type": "REPLACEMENT_ATTEMPT_STARTED",
                    "attempt_id": _required_field(replacement, "attempt_id"),
                    "unit_id": unit_id,
                },
                {
                    "event_type": "REPLACEMENT_ACCEPTED",
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
) -> FormalStrategyResult:
    """通过现有独立 process harness 执行 worker death/reassignment。"""

    unit_ids = tuple(str(item) for item in ai_unit_ids)
    if dead_worker_count < 1 or dead_worker_count > len(unit_ids):
        raise ValueError("dead_worker_count exceeds available AI units")
    store = ArtifactStore(Path(artifact_root))
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
    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for index, target_unit_id in enumerate(unit_ids[:dead_worker_count]):
        outcome = run_worker_death_harness(
            artifact_store=store,
            condition_id=condition_id,
            repeat_id=repeat_id,
            run_id=f"{condition_id}-{task_id}-death-{index}",
            ai_units=units,
            target_unit_id=target_unit_id,
            kill_point=kill_point,
            started_at=started_at,
            process_tick_seconds=process_tick_seconds,
        )
        record = outcome.record.to_dict()
        record["record_ref"] = outcome.record_ref.to_dict()
        records.append(record)
        events.extend(
            (
                {
                    "event_type": "WORKER_TERMINATED",
                    "unit_id": target_unit_id,
                    "worker_pid": record["worker_pid"],
                    "exitcode": record["worker_process_exitcode"],
                    "occurred_at": record["killed_at"],
                },
                {
                    "event_type": "LEASE_EXPIRED",
                    "unit_id": target_unit_id,
                    "attempt_id": record["dead_attempt"]["attempt_id"],
                    "occurred_at": record["lease_expiry"]["expired_at"],
                },
                {
                    "event_type": "UNIT_REASSIGNED",
                    "unit_id": target_unit_id,
                    "attempt_id": record["replacement_attempt"]["attempt_id"],
                    "occurred_at": record["reassignment"]["reassigned_at"],
                },
                {
                    "event_type": "REPLACEMENT_ACCEPTED",
                    "unit_id": target_unit_id,
                    "attempt_id": record["replacement_attempt"]["attempt_id"],
                    "occurred_at": record["replacement_attempt"]["ended_at"],
                },
            )
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
        },
        fault_records=tuple(records),
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
        "event_type": "ABLATION_BOUNDARY_APPLIED",
        "mode": normalized_mode,
        "disabled_mechanism": _disabled_mechanism(normalized_mode),
        "applicable": applicable,
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
