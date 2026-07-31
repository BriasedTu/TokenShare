"""Experiment-specific runtime strategies for formal paper execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_exp5_model_comparison import (
    build_exp5_model_execution_rows,
)
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


@dataclass
class ScheduledConditionAccumulator:
    """只保留condition指标所需compact facts，不持有完整root outcome。"""

    worker_count: int
    case_ids: list[str]
    events: list[dict[str, Any]]
    intervals: list[tuple[str, str]]
    dependency_edges: list[dict[str, Any]]
    missing_runtime_case_count: int = 0
    total_runtime_record_count: int = 0
    dependency_fact_count: int = 0
    critical_path_sum_ms: float = 0.0
    succeeded_unit_count: int = 0
    condition_started_at: str | None = None
    condition_ended_at: str | None = None
    provider_latency_sum_ms: float = 0.0
    provider_latency_observed: bool = False
    provider_zero_call_count: int = 0
    provider_latency_missing_reason: str | None = None
    provider_error_count: int = 0

    @classmethod
    def create(cls, worker_count: int) -> "ScheduledConditionAccumulator":
        return cls(
            worker_count=worker_count,
            case_ids=[],
            events=[],
            intervals=[],
            dependency_edges=[],
        )

    def observe(self, case_id: str, outcome: Any) -> None:
        records = _runtime_records(outcome)
        self.case_ids.append(case_id)
        self.events.extend(_protocol_events(outcome))
        if not records:
            self.missing_runtime_case_count += 1
        else:
            window = _runtime_window(outcome, records)
            if window is None:
                self.missing_runtime_case_count += 1
            else:
                started_at, ended_at = window
                if self.condition_started_at is None or _timestamp(
                    started_at
                ) < _timestamp(self.condition_started_at):
                    self.condition_started_at = started_at
                if self.condition_ended_at is None or _timestamp(
                    ended_at
                ) > _timestamp(self.condition_ended_at):
                    self.condition_ended_at = ended_at
        self.total_runtime_record_count += len(records)
        dependency_count = sum(
            bool(record["_dependency_evidence_present"])
            for record in records
        )
        self.dependency_fact_count += dependency_count
        if records and dependency_count == len(records):
            self.critical_path_sum_ms += _critical_path_ms(records)
        for record in records:
            self.intervals.append((record["started_at"], record["ended_at"]))
            self.succeeded_unit_count += int(
                record.get("result_kind") == "succeeded"
            )
            if record["_dependency_evidence_present"]:
                self.dependency_edges.extend(
                    {
                        "case_id": case_id,
                        "source_unit_id": dependency,
                        "target_unit_id": record["unit_id"],
                    }
                    for dependency in record["dependencies"]
                )
        provider_attempt_count = _field(outcome, "provider_attempt_count")
        latency_ms = _field(outcome, "provider_latency_ms")
        if (
            isinstance(provider_attempt_count, int)
            and not isinstance(provider_attempt_count, bool)
            and provider_attempt_count == 0
        ):
            self.provider_zero_call_count += 1
        elif (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            self.provider_latency_sum_ms += float(latency_ms)
            self.provider_latency_observed = True
        else:
            reason = _field(outcome, "provider_latency_unavailable_reason")
            self.provider_latency_missing_reason = (
                str(reason)
                if isinstance(reason, str) and reason
                else "missing_provider_latency_evidence"
            )
        self.provider_error_count += int(
            _field(outcome, "provider_error_kind") is not None
        )

    def finish(self, *, outcomes: tuple[Any, ...]) -> FormalStrategyResult:
        case_count = len(self.case_ids)
        complete_runtime = self.missing_runtime_case_count == 0
        observed_slots = (
            _observed_parallel_slots(
                tuple(
                    (_timestamp(started), _timestamp(ended))
                    for started, ended in self.intervals
                )
            )
            if complete_runtime
            else None
        )
        if observed_slots is not None and observed_slots > self.worker_count:
            raise ValueError("runtime facts exceed configured worker capacity")
        wall_clock_ms = (
            round(
                (
                    _timestamp(self.condition_ended_at)
                    - _timestamp(self.condition_started_at)
                )
                * 1000.0,
                3,
            )
            if self.condition_started_at is not None
            and self.condition_ended_at is not None
            and complete_runtime
            else None
        )
        dependency_complete = (
            complete_runtime
            and self.total_runtime_record_count > 0
            and self.dependency_fact_count == self.total_runtime_record_count
        )
        critical_status = (
            "complete"
            if dependency_complete
            else "unavailable"
            if not complete_runtime or self.dependency_fact_count == 0
            else "incomplete"
        )
        critical_reason = (
            None
            if dependency_complete
            else "missing_worker_execution_facts"
            if not complete_runtime
            else "missing_protocol_dependency_evidence"
            if self.dependency_fact_count == 0
            else "incomplete_protocol_dependency_evidence"
        )
        runtime_status = (
            "complete"
            if complete_runtime
            else "unavailable"
            if self.missing_runtime_case_count == case_count
            else "incomplete"
        )
        runtime_reason = (
            None
            if complete_runtime
            else "missing_worker_execution_facts"
            if self.missing_runtime_case_count == case_count
            else "incomplete_worker_execution_facts"
        )
        if self.provider_latency_missing_reason is not None:
            provider_latency: float | None = None
            provider_status = "incomplete"
            provider_reason = self.provider_latency_missing_reason
        elif self.provider_latency_observed:
            provider_latency = self.provider_latency_sum_ms
            provider_status = "complete"
            provider_reason = None
        elif case_count and self.provider_zero_call_count == case_count:
            provider_latency = 0.0
            provider_status = "not_applicable"
            provider_reason = "no_provider_attempts"
        else:
            provider_latency = None
            provider_status = "incomplete"
            provider_reason = "missing_provider_latency_evidence"
        return FormalStrategyResult(
            ordered_case_ids=tuple(self.case_ids),
            outcomes=outcomes,
            events=tuple(self.events),
            metrics={
                "worker_count": self.worker_count,
                "observed_max_parallel_slots": observed_slots,
                "condition_started_at": self.condition_started_at,
                "condition_ended_at": self.condition_ended_at,
                "wall_clock_ms": wall_clock_ms,
                "critical_path_ms": (
                    round(self.critical_path_sum_ms, 3)
                    if dependency_complete
                    else None
                ),
                "critical_path_evidence_status": critical_status,
                "critical_path_unavailable_reason": critical_reason,
                "provider_latency_sum_ms": provider_latency,
                "provider_latency_evidence_status": provider_status,
                "provider_latency_unavailable_reason": provider_reason,
                "provider_error_count": self.provider_error_count,
                "throughput_completed_units_per_second": (
                    self.succeeded_unit_count / (wall_clock_ms / 1000.0)
                    if wall_clock_ms is not None and wall_clock_ms > 0
                    else None
                ),
                "dependency_edges": self.dependency_edges,
                "applicability": "supported",
                "runtime_evidence_status": runtime_status,
                "runtime_evidence_unavailable_reason": runtime_reason,
            },
        )


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
    should_continue_after_case: Callable[[str, Any], bool] | None = None,
    on_case_complete: Callable[[str, Any], None] | None = None,
    retain_outcomes: bool = True,
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
                "critical_path_evidence_status": "not_applicable",
                "critical_path_unavailable_reason": None,
                "provider_latency_sum_ms": 0,
                "provider_latency_evidence_status": "not_applicable",
                "provider_latency_unavailable_reason": "no_provider_attempts",
                "provider_error_count": 0,
                "applicability": "unsupported_worker_level",
                "runtime_evidence_status": "not_applicable",
                "runtime_evidence_unavailable_reason": None,
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
                "critical_path_evidence_status": "not_applicable",
                "critical_path_unavailable_reason": None,
                "provider_latency_sum_ms": 0,
                "provider_latency_evidence_status": "not_applicable",
                "provider_latency_unavailable_reason": "no_provider_attempts",
                "provider_error_count": 0,
                "applicability": "empty_selection",
                "runtime_evidence_status": "not_applicable",
                "runtime_evidence_unavailable_reason": None,
            },
        )

    if not isinstance(retain_outcomes, bool):
        raise ValueError("retain_outcomes must be a boolean")
    if on_case_complete is not None or not retain_outcomes:
        accumulator = ScheduledConditionAccumulator.create(worker_count)
        retained: list[Any] = []
        for case_id in case_ids:
            outcome = execute_case(case_id, worker_count)
            accumulator.observe(case_id, outcome)
            if on_case_complete is not None:
                on_case_complete(case_id, outcome)
            if retain_outcomes:
                retained.append(outcome)
            should_continue = not (
                should_continue_after_case is not None
                and not should_continue_after_case(case_id, outcome)
            )
            if not retain_outcomes:
                del outcome
            if not should_continue:
                break
        return accumulator.finish(outcomes=tuple(retained))

    executed_case_ids: list[str] = []
    outcome_values: list[Any] = []
    for case_id in case_ids:
        outcome = execute_case(case_id, worker_count)
        executed_case_ids.append(case_id)
        outcome_values.append(outcome)
        if (
            should_continue_after_case is not None
            and not should_continue_after_case(case_id, outcome)
        ):
            break
    case_ids = tuple(executed_case_ids)
    ordered_outcomes = tuple(outcome_values)
    records_by_case = {
        case_id: _runtime_records(outcome)
        for case_id, outcome in zip(case_ids, ordered_outcomes, strict=True)
    }
    windows_by_case = {
        case_id: _runtime_window(outcome, records_by_case[case_id])
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
    missing_case_ids = tuple(
        case_id for case_id in case_ids if not records_by_case[case_id]
    )
    complete_runtime_evidence = not missing_case_ids
    intervals = tuple(
        (_timestamp(record["started_at"]), _timestamp(record["ended_at"]))
        for record in all_records
    )
    observed_max_slots = (
        _observed_parallel_slots(intervals) if complete_runtime_evidence else None
    )
    if observed_max_slots is not None and observed_max_slots > worker_count:
        raise ValueError("runtime facts exceed configured worker capacity")
    condition_window = (
        (
            min(
                (window for window in windows_by_case.values() if window is not None),
                key=lambda window: _timestamp(window[0]),
            )[0],
            max(
                (window for window in windows_by_case.values() if window is not None),
                key=lambda window: _timestamp(window[1]),
            )[1],
        )
        if complete_runtime_evidence
        else None
    )
    wall_clock_ms = (
        round(
            (
                _timestamp(condition_window[1])
                - _timestamp(condition_window[0])
            )
            * 1000.0,
            3,
        )
        if condition_window is not None
        else None
    )
    dependency_fact_count = sum(
        record["_dependency_evidence_present"] for record in all_records
    )
    dependency_evidence_complete = (
        complete_runtime_evidence
        and bool(all_records)
        and dependency_fact_count == len(all_records)
    )
    critical_path_evidence_status = (
        "complete"
        if dependency_evidence_complete
        else "unavailable"
        if not complete_runtime_evidence or dependency_fact_count == 0
        else "incomplete"
    )
    critical_path_unavailable_reason = (
        None
        if dependency_evidence_complete
        else (
            "missing_worker_execution_facts"
            if not complete_runtime_evidence
            else "missing_protocol_dependency_evidence"
            if dependency_fact_count == 0
            else "incomplete_protocol_dependency_evidence"
        )
    )
    critical_path_ms = (
        round(
            sum(
                _critical_path_ms(records_by_case[case_id])
                for case_id in case_ids
            ),
            3,
        )
        if dependency_evidence_complete
        else None
    )
    runtime_evidence_status = (
        "complete"
        if complete_runtime_evidence
        else "unavailable"
        if len(missing_case_ids) == len(case_ids)
        else "incomplete"
    )
    runtime_evidence_unavailable_reason = (
        None
        if complete_runtime_evidence
        else "missing_worker_execution_facts"
        if len(missing_case_ids) == len(case_ids)
        else "incomplete_worker_execution_facts"
    )
    (
        provider_latency_sum_ms,
        provider_latency_evidence_status,
        provider_latency_unavailable_reason,
    ) = _provider_latency_aggregate(
        ordered_outcomes
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
                condition_window[0]
                if condition_window is not None
                else None
            ),
            "condition_ended_at": (
                condition_window[1]
                if condition_window is not None
                else None
            ),
            "wall_clock_ms": wall_clock_ms,
            "critical_path_ms": critical_path_ms,
            "critical_path_evidence_status": critical_path_evidence_status,
            "critical_path_unavailable_reason": critical_path_unavailable_reason,
            "provider_latency_sum_ms": provider_latency_sum_ms,
            "provider_latency_evidence_status": provider_latency_evidence_status,
            "provider_latency_unavailable_reason": (
                provider_latency_unavailable_reason
            ),
            "provider_error_count": provider_error_count,
            "throughput_completed_units_per_second": (
                sum(record.get("result_kind") == "succeeded" for record in all_records)
                / (wall_clock_ms / 1000.0)
                if wall_clock_ms is not None and wall_clock_ms > 0
                else None
            ),
            "dependency_edges": [
                {
                    "case_id": case_id,
                    "source_unit_id": dependency,
                    "target_unit_id": record["unit_id"],
                }
                for case_id in case_ids
                for record in records_by_case[case_id]
                for dependency in (
                    record["dependencies"]
                    if record["_dependency_evidence_present"]
                    else ()
                )
            ],
            "applicability": "supported",
            "runtime_evidence_status": runtime_evidence_status,
            "runtime_evidence_unavailable_reason": (
                runtime_evidence_unavailable_reason
            ),
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
    }
    if normalized_mode not in supported:
        raise ValueError("unsupported Experiment 4 mode")
    flags = {
        "default_protocol_behavior": normalized_mode == "FULL",
        "wrong_canonical_exposed": normalized_mode == "NO_VERIFICATION",
        "raw_only_exposed": normalized_mode == "NO_PARSER_POLICY",
        "stuck_after_rejection": normalized_mode == "NO_REQUEUE",
        "premature_merge_attempted": normalized_mode == "NO_MERGE_GATE",
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
        in {"NO_VERIFICATION", "NO_PARSER_POLICY"}
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
    adapter_root: str | Path,
    task: Mapping[str, Any],
    transport_kind: str,
    model_policy: str,
    pilot_only: bool,
) -> FormalStrategyResult:
    """从 adapter 持久化的 v2 identity artifacts 构造严格 join 输入。"""

    store = ArtifactStore(adapter_root)
    records: list[dict[str, Any]] = []
    for attempt in attempts:
        _validate_fixed_identity(attempt, approved_identity)
        attempt_body = _json_mapping(attempt, "Experiment 5 attempt")
        record_ref = _required_artifact_ref(
            attempt_body,
            "model_execution_record_ref",
        )
        record_body = _read_json_artifact(store, record_ref)
        if (
            record_body.get("schema_version")
            != "tokenshare.paper_model_execution_record.v2"
        ):
            raise ValueError(
                "formal Experiment 5 requires persisted model execution v2"
            )
        if record_body.get("condition_id") != condition_id:
            raise ValueError("model execution record condition_id mismatch")
        expected_identity = record_body.get("expected_identity")
        if (
            not isinstance(expected_identity, Mapping)
            or expected_identity.get("cohort_member_id") != cohort_member_id
        ):
            raise ValueError("model execution record cohort member mismatch")
        raw_ref = _optional_artifact_ref(attempt_body, "raw_output_ref")
        request_ref = _required_artifact_ref(attempt_body, "request_ref")
        provenance_ref = _required_artifact_ref(attempt_body, "provenance_ref")
        usage_ref = _required_artifact_ref(attempt_body, "usage_ref")
        records.append(
            {
                "record": record_body,
                "record_ref": record_ref,
                "raw_output": (
                    _read_json_artifact(store, raw_ref)
                    if raw_ref is not None
                    else None
                ),
                "request": _read_json_artifact(store, request_ref),
                "provenance": _read_json_artifact(store, provenance_ref),
                "usage": _read_json_artifact(store, usage_ref),
                "attempt": attempt_body,
                "task": dict(task),
                "transport_kind": transport_kind,
                "model_policy": model_policy,
                "provider_errors": [],
                "pilot_only": pilot_only,
                "formal_strict_join": True,
            }
        )
    rows = build_exp5_model_execution_rows(
        {"model_execution_records": records}
    )
    return FormalStrategyResult(
        ordered_case_ids=(),
        outcomes=tuple(attempts),
        events=(),
        metrics={
            "model_execution_record_count": len(records),
            "identity_status_by_attempt": {
                str(row["attempt_id"]): str(row["identity_status"])
                for row in rows
            },
        },
        model_execution_records=tuple(records),
    )


def finalize_exp5_identity_evidence(
    *,
    attempts: Sequence[Any],
    task: Mapping[str, Any],
    identity_status_by_attempt: Mapping[str, str],
    cohort_member_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], bool]:
    """把身份审计结论转成不可误报成功的 task/attempt 终态。"""

    identity_mismatch = any(
        status == "model_identity_mismatch"
        for status in identity_status_by_attempt.values()
    )
    enriched_attempts = tuple(
        {
            **_json_mapping(attempt, "Experiment 5 attempt"),
            "cohort_member_id": cohort_member_id,
            "model_identity_audit": identity_status_by_attempt[
                str(_required_field(attempt, "attempt_id"))
            ],
            **(
                {
                    "attempt_status": "model_identity_mismatch",
                    "error_kind": "model_identity_mismatch",
                    "paper_eligible": False,
                }
                if identity_status_by_attempt[
                    str(_required_field(attempt, "attempt_id"))
                ]
                == "model_identity_mismatch"
                else {}
            ),
        }
        for attempt in attempts
    )
    task_body = dict(task)
    task_body["cohort_member_id"] = cohort_member_id
    if identity_mismatch:
        task_body.update(
            {
                "root_status": "ineligible",
                "error_kind": "model_identity_mismatch",
                "paper_eligible": False,
            }
        )
    return task_body, enriched_attempts, identity_mismatch


def exp5_identity_fail_stop_required(adapter_result: Any) -> bool:
    """只让实际持久化的 resolved-model 身份失败触发 condition-local 停机。"""

    if adapter_result is None:
        return False
    attempts = _field(adapter_result, "attempt_results")
    if attempts is None:
        attempts = _field(adapter_result, "attempts")
    return any(
        _field(attempt, "model_identity_audit") == "model_identity_mismatch"
        for attempt in (attempts or ())
    )


def _ablation_applicable(mode: str, observation: Mapping[str, Any]) -> bool:
    field_by_mode = {
        "NO_VERIFICATION": "candidate_rejected",
        "NO_PARSER_POLICY": "parse_failed",
        "NO_REQUEUE": "replacement_created",
        "NO_MERGE_GATE": "merge_gate_blocked",
    }
    field = field_by_mode.get(mode)
    if field is None:
        return False
    return observation.get(field) is True


def _disabled_mechanism(mode: str) -> str | None:
    return {
        "FULL": None,
        "NO_VERIFICATION": "verification",
        "NO_PARSER_POLICY": "parser_policy",
        "NO_REQUEUE": "requeue",
        "NO_MERGE_GATE": "merge_gate",
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
    if not raw_records:
        observation = _runtime_observation(outcome)
        raw_records = (
            observation.get("worker_execution_facts", ())
            if observation is not None
            else ()
        )
    if not isinstance(raw_records, Sequence) or isinstance(
        raw_records,
        (str, bytes),
    ):
        raise ValueError("runtime records must be a sequence")
    records: list[dict[str, Any]] = []
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
        for timestamp_field in ("started_at", "ended_at"):
            if not isinstance(record.get(timestamp_field), str):
                raise ValueError(f"runtime record {timestamp_field} is required")
            _timestamp(record[timestamp_field])
        if _timestamp(record["ended_at"]) < _timestamp(record["started_at"]):
            raise ValueError("runtime record ended_at precedes started_at")
        dependency_evidence_present = "dependencies" in record
        dependencies = record.get("dependencies", ())
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError("runtime record dependencies must be a sequence")
        record["dependencies"] = [str(item) for item in dependencies]
        record["_dependency_evidence_present"] = dependency_evidence_present
        records.append(record)
    unit_counts: dict[str, int] = {}
    for record in records:
        unit_id = str(record["unit_id"])
        unit_counts[unit_id] = unit_counts.get(unit_id, 0) + 1
    seen_node_ids: set[str] = set()
    for record in records:
        attempt_id = record.get("attempt_id")
        if attempt_id is not None and (
            not isinstance(attempt_id, str) or not attempt_id
        ):
            raise ValueError("runtime record attempt_id must be a non-empty string")
        unit_id = str(record["unit_id"])
        if attempt_id is None and unit_counts[unit_id] > 1:
            raise ValueError(
                "repeated runtime unit records require unique attempt_id values"
            )
        node_id = str(attempt_id or unit_id)
        if node_id in seen_node_ids:
            raise ValueError("runtime record attempt identity must be unique")
        seen_node_ids.add(node_id)
        record["_runtime_node_id"] = node_id
    return tuple(records)


def _runtime_observation(outcome: Any) -> dict[str, Any] | None:
    candidates = (
        _field(outcome, "runtime_observation"),
        _field(_field(outcome, "task"), "runtime_observation"),
        _field(
            _field(
                _field(_field(outcome, "adapter_result"), "run_evidence"),
                "protocol_runtime",
            ),
            "runtime_observation",
        ),
    )
    raw = next((item for item in candidates if item is not None), None)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("runtime observation must be a mapping")
    observation = dict(raw)
    schema_version = observation.get("schema_version")
    if schema_version not in (None, "tokenshare.protocol_runtime_observation.v1"):
        raise ValueError("runtime observation schema is unsupported")
    facts = observation.get("worker_execution_facts")
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        raise ValueError("runtime observation worker facts are required")
    return observation


def _runtime_window(
    outcome: Any,
    records: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    if not records:
        return None
    observation = _runtime_observation(outcome)
    if observation is not None:
        started_at = observation.get("runtime_started_at")
        ended_at = observation.get("runtime_ended_at")
        if not isinstance(started_at, str) or not isinstance(ended_at, str):
            raise ValueError("runtime observation window is required")
        if _timestamp(ended_at) < _timestamp(started_at):
            raise ValueError("runtime observation ended before it started")
        return started_at, ended_at
    return (
        min(records, key=lambda record: _timestamp(str(record["started_at"])))[
            "started_at"
        ],
        max(records, key=lambda record: _timestamp(str(record["ended_at"])))[
            "ended_at"
        ],
    )


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
    if any(record.get("_dependency_evidence_present") is not True for record in records):
        raise ValueError("critical path requires explicit protocol dependency evidence")
    by_id = {str(record["_runtime_node_id"]): record for record in records}
    records_by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        records_by_unit.setdefault(str(record["unit_id"]), []).append(record)
    for unit_records in records_by_unit.values():
        unit_records.sort(
            key=lambda record: (
                int(record["execution_index"])
                if isinstance(record.get("execution_index"), int)
                and not isinstance(record.get("execution_index"), bool)
                else 2**63 - 1,
                _timestamp(str(record["started_at"])),
                str(record["_runtime_node_id"]),
            )
        )
    dependency_nodes: dict[str, tuple[str, ...]] = {}
    for unit_records in records_by_unit.values():
        previous: Mapping[str, Any] | None = None
        for record in unit_records:
            node_id = str(record["_runtime_node_id"])
            current_started_at = _timestamp(str(record["started_at"]))
            resolved: list[str] = []
            if (
                previous is not None
                and _timestamp(str(previous["ended_at"])) <= current_started_at
            ):
                resolved.append(str(previous["_runtime_node_id"]))
            for dependency in record["dependencies"]:
                if dependency in by_id:
                    candidate = by_id[dependency]
                    if candidate is record:
                        raise ValueError("runtime dependency graph contains a cycle")
                else:
                    candidates = [
                        candidate
                        for candidate in records_by_unit.get(dependency, ())
                        if candidate is not record
                        and _timestamp(str(candidate["ended_at"]))
                        <= current_started_at
                    ]
                    if not candidates:
                        raise ValueError(
                            "runtime dependency graph references an unknown unit"
                        )
                    candidate = max(
                        candidates,
                        key=lambda item: (
                            _timestamp(str(item["ended_at"])),
                            str(item["_runtime_node_id"]),
                        ),
                    )
                candidate_id = str(candidate["_runtime_node_id"])
                if candidate_id not in resolved:
                    resolved.append(candidate_id)
            dependency_nodes[node_id] = tuple(resolved)
            previous = record
    memo: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> float:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            raise ValueError("runtime dependency graph contains a cycle")
        visiting.add(node_id)
        record = by_id[node_id]
        dependency_costs = [
            visit(dependency) for dependency in dependency_nodes[node_id]
        ]
        duration_ms = round(
            (
                _timestamp(str(record["ended_at"]))
                - _timestamp(str(record["started_at"]))
            )
            * 1000.0,
            3,
        )
        visiting.remove(node_id)
        memo[node_id] = duration_ms + max(dependency_costs, default=0.0)
        return memo[node_id]

    return max(visit(node_id) for node_id in by_id)


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


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        body = to_dict()
        if isinstance(body, Mapping):
            return {str(key): item for key, item in body.items()}
    if is_dataclass(value):
        body = asdict(value)
        if isinstance(body, Mapping):
            return {str(key): item for key, item in body.items()}
    raise ValueError(f"{label} must be a JSON mapping")


def _required_artifact_ref(
    body: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any]:
    value = body.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f"formal Experiment 5 requires {field_name}")
    return dict(value)


def _optional_artifact_ref(
    body: Mapping[str, Any],
    field_name: str,
) -> dict[str, Any] | None:
    value = body.get(field_name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"formal Experiment 5 {field_name} must be a ref")
    return dict(value)


def _read_json_artifact(
    store: ArtifactStore,
    ref_body: Mapping[str, Any],
) -> dict[str, Any]:
    ref = ArtifactRef.from_dict(dict(ref_body))
    if not store.verify(ref):
        raise ValueError("formal Experiment 5 artifact verification failed")
    try:
        body = json.loads(store.read_bytes(ref).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("formal Experiment 5 artifact must be JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("formal Experiment 5 artifact body must be an object")
    return body


def _provider_latency_aggregate(
    outcomes: Sequence[Any],
) -> tuple[float | None, str, str | None]:
    """把 zero-call 与缺失真实 latency 分开，避免缺失值进入求和。"""

    latency_sum_ms = 0.0
    observed_latency = False
    explicit_zero_call_count = 0
    missing_reason: str | None = None
    for outcome in outcomes:
        provider_attempt_count = _field(outcome, "provider_attempt_count")
        latency_ms = _field(outcome, "provider_latency_ms")
        if (
            isinstance(provider_attempt_count, int)
            and not isinstance(provider_attempt_count, bool)
            and provider_attempt_count == 0
        ):
            explicit_zero_call_count += 1
            continue
        if (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            latency_sum_ms += float(latency_ms)
            observed_latency = True
            continue
        outcome_reason = _field(outcome, "provider_latency_unavailable_reason")
        missing_reason = (
            str(outcome_reason)
            if isinstance(outcome_reason, str) and outcome_reason
            else "missing_provider_latency_evidence"
        )
    if missing_reason is not None:
        return None, "incomplete", missing_reason
    if observed_latency:
        return latency_sum_ms, "complete", None
    if outcomes and explicit_zero_call_count == len(outcomes):
        return 0.0, "not_applicable", "no_provider_attempts"
    return None, "incomplete", "missing_provider_latency_evidence"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
