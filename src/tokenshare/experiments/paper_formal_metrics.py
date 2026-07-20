"""Recompute formal Experiment 1-5 metrics from persisted evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
from typing import Any


EXP1 = "exp1_real_ai_feasibility"
EXP2 = "exp2_real_ai_scalability"
EXP3 = "exp3_real_ai_fault_recovery"
EXP4 = "exp4_real_ai_protocol_ablation"
EXP5 = "exp5_real_ai_model_endpoint_comparison"
FORMAL_EXPERIMENT_IDS = (EXP1, EXP2, EXP3, EXP4, EXP5)


@dataclass(frozen=True, kw_only=True)
class FormalMetricsResult:
    condition_rows: tuple[dict[str, Any], ...]
    experiment_rows: dict[str, tuple[dict[str, Any], ...]]
    metrics_digest: str
    paper_eligible: bool
    capturing: bool
    output_refs: tuple[dict[str, Any], ...]
    schema_version: str = "tokenshare.paper_formal_metrics.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "condition_rows": [dict(row) for row in self.condition_rows],
            "experiment_rows": {
                experiment_id: [dict(row) for row in rows]
                for experiment_id, rows in self.experiment_rows.items()
            },
            "metrics_digest": self.metrics_digest,
            "paper_eligible": self.paper_eligible,
            "capturing": self.capturing,
            "output_refs": [dict(ref) for ref in self.output_refs],
        }


def recompute_paper_formal_metrics(
    output_root: str | Path,
) -> FormalMetricsResult:
    """只从 formal checkpoint generation 复算，并写出固定 metrics contract。"""

    root = Path(output_root)
    suite = _read_json(root / "suite_manifest.json")
    if suite.get("formal") is not True or suite.get("pilot_only") is not False:
        raise ValueError("formal metrics require formal non-pilot evidence")
    if suite.get("execution_scope") != "formal_matrix":
        raise ValueError("formal metrics require formal_matrix evidence")
    conditions = {
        str(row["condition_id"]): row
        for row in _read_jsonl(root / "conditions.jsonl")
    }
    condition_rows: list[dict[str, Any]] = []
    run_bundles: dict[str, dict[str, Any]] = {}
    for run_root in _run_roots(root):
        condition_id = run_root.parent.name
        condition = conditions.get(condition_id)
        if condition is None:
            raise ValueError(f"condition evidence is missing: {condition_id}")
        generation = _current_generation(run_root)
        bundle = {
            "condition": condition,
            "tasks": _read_jsonl(generation / "per_task_results.jsonl"),
            "attempts": _read_jsonl(generation / "per_attempt_results.jsonl"),
            "faults": _read_jsonl(generation / "fault_injections.jsonl"),
            "events": _read_jsonl(generation / "events" / "event_log.jsonl"),
        }
        if not bundle["tasks"] or not bundle["attempts"] or not bundle["events"]:
            raise ValueError(f"incomplete formal evidence: {condition_id}")
        run_bundles[condition_id] = bundle
        condition_rows.append(_condition_metrics(bundle))

    rows_by_id = {row["condition_id"]: row for row in condition_rows}
    experiment_rows = {
        EXP1: tuple(_exp1_rows(rows_by_id, run_bundles)),
        EXP2: tuple(_exp2_rows(rows_by_id, run_bundles)),
        EXP3: tuple(_exp3_rows(rows_by_id, run_bundles)),
        EXP4: tuple(_exp4_rows(rows_by_id, run_bundles)),
        EXP5: tuple(_exp5_rows(rows_by_id, run_bundles)),
    }
    metrics_body = {
        "schema_version": "tokenshare.paper_formal_metrics_body.v1",
        "formal": True,
        "pilot_only": False,
        "execution_scope": "formal_matrix",
        "capturing": suite.get("regression_only") is True,
        "paper_eligible": suite.get("paper_eligible") is True,
        "condition_rows": condition_rows,
        "experiment_rows": {
            key: list(value) for key, value in experiment_rows.items()
        },
    }
    metrics_digest = _digest(metrics_body)
    metrics_body["metrics_digest"] = metrics_digest
    output_refs = _write_metrics_outputs(
        root=root,
        metrics_body=metrics_body,
        condition_rows=condition_rows,
        experiment_rows=experiment_rows,
        run_bundles=run_bundles,
    )
    return FormalMetricsResult(
        condition_rows=tuple(condition_rows),
        experiment_rows=experiment_rows,
        metrics_digest=metrics_digest,
        paper_eligible=suite.get("paper_eligible") is True,
        capturing=suite.get("regression_only") is True,
        output_refs=tuple(output_refs),
    )


def _condition_metrics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    condition = bundle["condition"]
    tasks = bundle["tasks"]
    attempts = bundle["attempts"]
    events = bundle["events"]
    completed = sum(_status(task.get("root_status")) == "completed" for task in tasks)
    accepted_valid = sum(_task_validity(task) for task in tasks)
    task_count = len(tasks)
    token_values = [int(_number(attempt.get("total_tokens"))) for attempt in attempts]
    latency_values = [float(_number(attempt.get("latency_ms"))) for attempt in attempts]
    failure_breakdown: dict[str, int] = defaultdict(int)
    for task in tasks:
        status = _status(task.get("root_status"))
        if status != "completed":
            failure_breakdown[status] += 1
    for attempt in attempts:
        status = _status(attempt.get("attempt_status"))
        if status not in {"succeeded", "completed"}:
            failure_breakdown[status] += 1
    wall_clock_ms = _wall_clock_ms(events)
    critical_path_ms = _critical_path_ms(events)
    return {
        "experiment_id": str(condition["experiment_id"]),
        "condition_id": str(condition["condition_id"]),
        "repeat_id": int(condition.get("repeat_id", 0)),
        "domain": condition.get("domain"),
        "difficulty": condition.get("difficulty"),
        "paper_difficulty": condition.get("paper_difficulty"),
        "topic_family": condition.get("topic_family"),
        "worker_count": int(condition.get("worker_count", 1)),
        "fault_type": condition.get("fault_type", "none"),
        "fault_rate": float(condition.get("fault_rate", 0.0)),
        "ablation_mode": condition.get("ablation_mode", "FULL"),
        "provider_family": condition.get("provider_family"),
        "provider_model_id": condition.get("provider_model_id"),
        "model_entry_id": condition.get("model_entry_id"),
        "cohort_member_id": condition.get("cohort_member_id"),
        "task_count": task_count,
        "completed_root_count": completed,
        "failed_root_count": task_count - completed,
        "completion_rate": _rate(completed, task_count),
        "accepted_validity_rate": _rate(accepted_valid, task_count),
        "provider_attempt_count": len(attempts),
        "total_tokens": sum(token_values),
        "total_cost_estimate": sum(
            float(_number(attempt.get("cost_estimate"))) for attempt in attempts
        ),
        "wall_clock_ms": wall_clock_ms,
        "critical_path_ms": critical_path_ms,
        "provider_latency_sum_ms": sum(latency_values),
        "wall_clock_p50": _quantile([wall_clock_ms], 0.5),
        "wall_clock_p95": _quantile([wall_clock_ms], 0.95),
        "token_p50": _quantile(token_values, 0.5),
        "token_p95": _quantile(token_values, 0.95),
        "provider_error_count": sum(
            _status(attempt.get("attempt_status"))
            in {"provider_error", "executor_error"}
            or attempt.get("error_kind") is not None
            for attempt in attempts
        ),
        "rate_limited_attempt_count": sum(
            attempt.get("error_kind") in {"rate_limited", "429"}
            for attempt in attempts
        ),
        "retry_attempt_count": max(
            0,
            len(attempts)
            - len({str(attempt.get("unit_id")) for attempt in attempts}),
        ),
        "failure_breakdown": dict(sorted(failure_breakdown.items())),
        "paper_eligible": False,
        "formal": True,
        "pilot_only": False,
        "execution_scope": "formal_matrix",
    }


def _exp1_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(rows[condition_id])
        for condition_id, bundle in bundles.items()
        if bundle["condition"]["experiment_id"] == EXP1
    ]


def _exp2_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source = [
        dict(rows[condition_id])
        for condition_id, bundle in bundles.items()
        if bundle["condition"]["experiment_id"] == EXP2
    ]
    baselines: dict[tuple[Any, ...], float] = {}
    for row in source:
        if row["worker_count"] == 1:
            baselines[_scalability_key(row)] = float(row["wall_clock_ms"])
    result: list[dict[str, Any]] = []
    for row in source:
        baseline = baselines.get(_scalability_key(row))
        wall_clock = float(row["wall_clock_ms"])
        speedup = (
            baseline / wall_clock
            if baseline is not None and wall_clock > 0
            else None
        )
        result.append(
            {
                **row,
                "throughput_roots_per_second": (
                    row["completed_root_count"] / (wall_clock / 1000.0)
                    if wall_clock > 0
                    else None
                ),
                "speedup": speedup,
                "efficiency": (
                    speedup / row["worker_count"] if speedup is not None else None
                ),
                "critical_path_source": "dependency_and_merge_gate_events",
                "provider_latency_is_wall_clock": False,
            }
        )
    return result


def _exp3_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id, bundle in bundles.items():
        if bundle["condition"]["experiment_id"] != EXP3:
            continue
        row = dict(rows[condition_id])
        faults = bundle["faults"]
        events = bundle["events"]
        injected = len(faults)
        detected = sum(
            fault.get("detected") is True
            or fault.get("canonical_pollution") is False
            for fault in faults
        )
        false_accept = sum(fault.get("canonical_pollution") is True for fault in faults)
        recovery_required = sum(
            fault.get("recovery_required") is True
            or fault.get("requires_replacement") is True
            for fault in faults
        )
        recovered = sum(
            event.get("event_type") == "REPLACEMENT_ACCEPTED" for event in events
        )
        worker_faults = [fault for fault in faults if "worker_pid" in fault]
        tasks = bundle["tasks"]
        baseline_manifest = next(
            (
                task.get("matched_baseline")
                for task in tasks
                if isinstance(task.get("matched_baseline"), Mapping)
            ),
            {},
        )
        baseline_condition_id = baseline_manifest.get("condition_id")
        baseline_row = rows.get(str(baseline_condition_id))
        if baseline_row is not None:
            baseline_wall = float(baseline_row["wall_clock_ms"])
            baseline_tokens = int(baseline_row["total_tokens"])
            baseline_cost = float(baseline_row["total_cost_estimate"])
            baseline_source = "matched_condition_evidence"
        else:
            baseline_wall = sum(
                float(_number(task.get("matched_baseline_wall_clock_ms")))
                for task in tasks
            )
            baseline_tokens = sum(
                int(_number(task.get("matched_baseline_total_tokens")))
                for task in tasks
            )
            baseline_cost = sum(
                float(_number(task.get("matched_baseline_cost_estimate")))
                for task in tasks
            )
            baseline_source = (
                "worker_death_pre_harness_snapshot"
                if baseline_manifest
                else "not_available"
            )
        wall_delta = float(row["wall_clock_ms"]) - baseline_wall
        token_delta = int(row["total_tokens"]) - baseline_tokens
        cost_delta = float(row["total_cost_estimate"]) - baseline_cost
        result.append(
            {
                **row,
                "injected_fault_count": injected,
                "detection_rate": _rate(detected, injected),
                "false_accept_rate": _rate(false_accept, injected),
                "recovery_rate": _rate(recovered, recovery_required),
                "worker_death_count": len(worker_faults),
                "worker_replacement_complete_rate": _rate(
                    sum(
                        fault.get("replacement_process_exitcode") == 0
                        and fault.get("coordinator", {}).get("survived") is True
                        for fault in worker_faults
                    ),
                    len(worker_faults),
                ),
                "matched_baseline_condition_id": baseline_condition_id,
                "matched_baseline_source": baseline_source,
                "wall_clock_overhead_ms": wall_delta,
                "wall_clock_overhead_ratio": (
                    wall_delta / baseline_wall if baseline_wall > 0 else None
                ),
                "token_overhead": token_delta,
                "token_overhead_ratio": (
                    token_delta / baseline_tokens if baseline_tokens > 0 else None
                ),
                "cost_overhead_delta": cost_delta,
                "cost_overhead_ratio": (
                    cost_delta / baseline_cost if baseline_cost > 0 else None
                ),
            }
        )
    return result


def _exp4_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id, bundle in bundles.items():
        if bundle["condition"]["experiment_id"] != EXP4:
            continue
        tasks = bundle["tasks"]
        result.append(
            {
                **dict(rows[condition_id]),
                "wrong_canonical_count": sum(
                    task.get("canonical_accepted_by_ablation") is True
                    and task.get("final_deterministic_validity") is False
                    for task in tasks
                ),
                "raw_only_count": sum(
                    task.get("ablation_runtime_flags", {}).get("raw_only_exposed")
                    is True
                    for task in tasks
                ),
                "stuck_count": sum(
                    task.get("ablation_runtime_flags", {}).get("stuck_after_rejection")
                    is True
                    for task in tasks
                ),
                "premature_merge_count": sum(
                    task.get("ablation_runtime_flags", {}).get(
                        "premature_merge_attempted"
                    )
                    is True
                    for task in tasks
                ),
                "slot_mismatch_count": sum(
                    task.get("ablation_runtime_flags", {}).get(
                        "slot_mismatch_exposed"
                    )
                    is True
                    for task in tasks
                ),
                "exposed_error_count": sum(
                    int(_number(task.get("exposed_error_count"))) for task in tasks
                ),
                "escaped_error_count": sum(
                    int(_number(task.get("escaped_error_count"))) for task in tasks
                ),
            }
        )
    return result


def _exp5_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id, bundle in bundles.items():
        condition = bundle["condition"]
        if condition["experiment_id"] != EXP5:
            continue
        attempts = bundle["attempts"]
        matches = sum(
            attempt.get("model_identity_audit") == "fixed_entry_match"
            or (
                attempt.get("provider") == condition.get("provider_family")
                and attempt.get("model") == condition.get("provider_model_id")
                and attempt.get("entry_id") == condition.get("model_entry_id")
            )
            for attempt in attempts
        )
        result.append(
            {
                **dict(rows[condition_id]),
                "model_identity_match_rate": _rate(matches, len(attempts)),
                "endpoint_error_count": sum(
                    _status(attempt.get("attempt_status"))
                    not in {"succeeded", "completed"}
                    for attempt in attempts
                ),
            }
        )
    return result


def _write_metrics_outputs(
    *,
    root: Path,
    metrics_body: Mapping[str, Any],
    condition_rows: Sequence[Mapping[str, Any]],
    experiment_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    run_bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    paths_and_content = {
        "metrics/per_condition_summary.csv": _csv_text(condition_rows),
        "metrics/paper_table_feasibility.csv": _csv_text(experiment_rows[EXP1]),
        "metrics/paper_plot_scalability.csv": _csv_text(experiment_rows[EXP2]),
        "metrics/paper_plot_robustness.csv": _csv_text(experiment_rows[EXP3]),
        "metrics/paper_table_ablation.csv": _csv_text(experiment_rows[EXP4]),
        "metrics/model_execution_records.jsonl": _model_record_text(run_bundles),
        "metrics/failure_examples.json": json.dumps(
            _failure_examples(run_bundles),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        "metrics/formal_metrics.json": json.dumps(
            metrics_body,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    }
    refs: list[dict[str, Any]] = []
    for relative_path, content in paths_and_content.items():
        path = root / relative_path
        _write_text(path, content)
        refs.append(
            {
                "path": relative_path,
                "content_hash": _hash_bytes(path.read_bytes()),
            }
        )
    return refs


def _model_record_text(bundles: Mapping[str, Mapping[str, Any]]) -> str:
    records: list[dict[str, Any]] = []
    for bundle in bundles.values():
        condition = bundle["condition"]
        if condition["experiment_id"] != EXP5:
            continue
        for attempt in bundle["attempts"]:
            records.append(
                {
                    "schema_version": "tokenshare.paper_formal_model_execution.v1",
                    "condition_id": condition["condition_id"],
                    "cohort_member_id": condition.get("cohort_member_id"),
                    "provider": attempt.get("provider"),
                    "model": attempt.get("model"),
                    "entry_id": attempt.get("entry_id"),
                    "attempt_id": attempt.get("attempt_id"),
                    "request_ref": attempt.get("request_ref"),
                    "raw_output_ref": attempt.get("raw_output_ref"),
                    "parsed_output_ref": attempt.get("parsed_output_ref"),
                    "parse_failure_ref": attempt.get("parse_failure_ref"),
                    "provenance_ref": attempt.get("provenance_ref"),
                    "usage_ref": attempt.get("usage_ref"),
                    "model_execution_ref": attempt.get("model_execution_ref"),
                    "model_identity_audit": attempt.get("model_identity_audit"),
                }
            )
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def _failure_examples(bundles: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for bundle in bundles.values():
        for task in bundle["tasks"]:
            if _status(task.get("root_status")) != "completed":
                examples.append(dict(task))
                if len(examples) == 20:
                    return examples
    return examples


def _run_roots(root: Path) -> list[Path]:
    experiments_root = root / "experiments"
    if not experiments_root.is_dir():
        return []
    result: list[Path] = []
    for experiment_root in experiments_root.iterdir():
        runs_root = experiment_root / "runs"
        if not experiment_root.is_dir() or not runs_root.is_dir():
            continue
        for condition_root in runs_root.iterdir():
            if not condition_root.is_dir():
                continue
            result.extend(
                repeat_root
                for repeat_root in condition_root.iterdir()
                if repeat_root.is_dir()
                and (repeat_root / "CURRENT.json").is_file()
            )
    return sorted(result)


def _current_generation(run_root: Path) -> Path:
    pointer = _read_json(run_root / "CURRENT.json")
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("CURRENT generation_id is missing")
    generation = run_root / ".generations" / generation_id
    if not generation.is_dir():
        raise ValueError("CURRENT generation is missing")
    return generation


def _wall_clock_ms(events: Sequence[Mapping[str, Any]]) -> float:
    offsets = [
        float(event["offset_ms"])
        for event in events
        if isinstance(event.get("offset_ms"), (int, float))
    ]
    if offsets:
        return max(offsets) - min(0.0, min(offsets))
    timestamps = [
        timestamp
        for event in events
        for timestamp in _event_timestamps(event)
    ]
    if len(timestamps) >= 2:
        return max(0.0, (max(timestamps) - min(timestamps)).total_seconds() * 1000.0)
    return max(
        (float(_number(event.get("duration_ms"))) for event in events),
        default=0.0,
    )


def _critical_path_ms(events: Sequence[Mapping[str, Any]]) -> float:
    unit_ms = max(
        (
            float(_number(event.get("duration_ms")))
            for event in events
            if event.get("event_type") == "AI_UNIT_ENDED"
        ),
        default=0.0,
    )
    merge_ms = sum(
        float(_number(event.get("duration_ms")))
        for event in events
        if event.get("event_type") == "MERGE_GATE_COMPLETED"
    )
    calculated = unit_ms + merge_ms
    return calculated if calculated > 0 else _wall_clock_ms(events)


def _event_timestamps(event: Mapping[str, Any]) -> list[datetime]:
    result: list[datetime] = []
    for field_name in ("started_at", "ended_at", "occurred_at", "created_at"):
        value = event.get(field_name)
        if not isinstance(value, str):
            continue
        try:
            result.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            continue
    return result


def _task_validity(task: Mapping[str, Any]) -> bool:
    if "accepted_validity" in task:
        return task.get("accepted_validity") is True
    if "final_deterministic_validity" in task:
        return task.get("final_deterministic_validity") is True
    return _status(task.get("root_status")) == "completed"


def _scalability_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("domain"),
        row.get("difficulty"),
        row.get("paper_difficulty"),
        row.get("topic_family"),
        row.get("repeat_id"),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _quantile(values: Sequence[int | float], probability: float) -> int | float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _number(value: Any) -> int | float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _status(value: Any) -> str:
    return str(getattr(value, "value", value or "unknown"))


def _csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    fields = sorted({key for row in rows for key in row}) or ["no_rows"]
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list, tuple))
                    else value
                )
                for field, value in row.items()
            }
        )
    return handle.getvalue()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required formal evidence is missing: {path.name}")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"formal evidence must be an object: {path.name}")
    return body


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"required formal evidence is missing: {path.name}")
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        body = json.loads(line)
        if not isinstance(body, dict):
            raise ValueError(f"formal JSONL record must be an object: {path.name}")
        records.append(body)
    return records


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _digest(value: Any) -> str:
    return _hash_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _hash_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
