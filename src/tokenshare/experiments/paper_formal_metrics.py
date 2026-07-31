"""Recompute formal Experiment 1-5 metrics from persisted evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from tokenshare.experiments.paper_exp5_model_comparison import (
    build_exp5_model_execution_rows,
)
from tokenshare.experiments.paper_exp5_statistics import (
    EXP5_BOOTSTRAP_RESAMPLES,
    EXP5_BOOTSTRAP_SEED,
    _cluster_bootstrap_ci,
    build_exp5_order_and_concurrency_rows,
    build_exp5_paired_comparison_rows,
)


EXP1 = "exp1_real_ai_feasibility"
EXP2 = "exp2_real_ai_scalability"
EXP3 = "exp3_real_ai_fault_recovery"
EXP4 = "exp4_real_ai_protocol_ablation"
EXP5 = "exp5_real_ai_model_endpoint_comparison"
FORMAL_EXPERIMENT_IDS = (EXP1, EXP2, EXP3, EXP4, EXP5)
_SHARED_SOURCE_SUCCESS_STATUSES = {"accepted", "completed", "success", "succeeded"}
_SHARED_SOURCE_TERMINAL_STATUSES = _SHARED_SOURCE_SUCCESS_STATUSES | {
    "blocked",
    "budget_exhausted",
    "failed",
    "ineligible",
    "partial",
    "timeout",
}


@dataclass(frozen=True, kw_only=True)
class FormalMetricsResult:
    condition_rows: tuple[dict[str, Any], ...]
    experiment_rows: dict[str, tuple[dict[str, Any], ...]]
    metrics_digest: str
    paper_eligible: bool
    capturing: bool
    output_refs: tuple[dict[str, Any], ...]
    exp5_artifact_rows: Mapping[str, Any] | None = None
    exp5_artifact_rows_digest: str | None = None
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
            "exp5_artifact_rows_digest": self.exp5_artifact_rows_digest,
        }


def _compare_path_component_keys(left: str, right: str) -> int:
    """按旧 WindowsPath 的规范化组件 tuple 语义比较 SQLite key。"""

    left_components = tuple(json.loads(left))
    right_components = tuple(json.loads(right))
    return (left_components > right_components) - (
        left_components < right_components
    )


class LazyFormalRunBundleMapping(Mapping[str, Mapping[str, Any]]):
    """把 run 索引放在临时 SQLite，并在取值时只加载一个 generation。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._temporary_directory = TemporaryDirectory(
            prefix=".tokenshare-formal-metrics-",
            dir=str(self._root.parent),
        )
        self.work_directory = Path(self._temporary_directory.name)
        self._connection = sqlite3.connect(
            self.work_directory / "run-index.sqlite3"
        )
        self._connection.create_collation(
            "PATH_COMPONENTS",
            _compare_path_component_keys,
        )
        try:
            self._initialize_index()
        except BaseException:
            self.close()
            raise

    def _initialize_index(self) -> None:
        self._connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = FILE;
            CREATE TABLE conditions (
                condition_id TEXT PRIMARY KEY,
                body_json TEXT NOT NULL
            );
            CREATE TABLE runs (
                condition_id TEXT PRIMARY KEY,
                run_root TEXT NOT NULL,
                generation_root TEXT NOT NULL,
                run_sort_key TEXT COLLATE PATH_COMPONENTS NOT NULL,
                discovery_order INTEGER NOT NULL
            );
            """
        )
        for condition in _iter_jsonl(self._root / "conditions.jsonl"):
            condition_id = str(condition["condition_id"])
            self._connection.execute(
                """
                INSERT INTO conditions(condition_id, body_json)
                VALUES (?, ?)
                ON CONFLICT(condition_id) DO UPDATE SET
                    body_json = excluded.body_json
                """,
                (
                    condition_id,
                    json.dumps(condition, ensure_ascii=False),
                ),
            )
        for discovery_order, run_root in enumerate(
            _iter_run_roots(self._root)
        ):
            condition_id = run_root.parent.name
            condition_row = self._connection.execute(
                "SELECT 1 FROM conditions WHERE condition_id = ?",
                (condition_id,),
            ).fetchone()
            if condition_row is None:
                raise ValueError(
                    f"condition evidence is missing: {condition_id}"
                )
            generation = _current_generation(run_root)
            self._connection.execute(
                """
                INSERT INTO runs(
                    condition_id,
                    run_root,
                    generation_root,
                    run_sort_key,
                    discovery_order
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(condition_id) DO UPDATE SET
                    run_root = excluded.run_root,
                    generation_root = excluded.generation_root,
                    run_sort_key = excluded.run_sort_key,
                    discovery_order = excluded.discovery_order
                WHERE excluded.run_sort_key > runs.run_sort_key
                   OR (
                       excluded.run_sort_key = runs.run_sort_key
                       AND excluded.discovery_order > runs.discovery_order
                   )
                """,
                (
                    condition_id,
                    str(run_root),
                    str(generation),
                    json.dumps(
                        tuple(
                            os.path.normcase(component)
                            for component in run_root.parts
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    discovery_order,
                ),
            )
        self._connection.commit()

    def __getitem__(self, condition_id: str) -> Mapping[str, Any]:
        row = self._connection.execute(
            """
            SELECT c.body_json, r.generation_root
            FROM runs AS r
            JOIN conditions AS c USING (condition_id)
            WHERE r.condition_id = ?
            """,
            (condition_id,),
        ).fetchone()
        if row is None:
            raise KeyError(condition_id)
        condition = json.loads(str(row[0]))
        if not isinstance(condition, dict):
            raise ValueError("formal condition evidence must be an object")
        return _load_run_bundle(
            suite_root=self._root,
            generation=Path(str(row[1])),
            condition=condition,
        )

    def condition(self, condition_id: str) -> Mapping[str, Any]:
        row = self._connection.execute(
            """
            SELECT c.body_json
            FROM runs AS r
            JOIN conditions AS c USING (condition_id)
            WHERE r.condition_id = ?
            """,
            (condition_id,),
        ).fetchone()
        if row is None:
            raise KeyError(condition_id)
        value = json.loads(str(row[0]))
        if not isinstance(value, dict):
            raise ValueError("formal condition evidence must be an object")
        return value

    def __iter__(self) -> Iterator[str]:
        cursor = self._connection.execute(
            """
            SELECT condition_id
            FROM runs
            ORDER BY run_sort_key COLLATE PATH_COMPONENTS, discovery_order
            """
        )
        for row in cursor:
            yield str(row[0])

    def __len__(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM runs").fetchone()
        return int(row[0]) if row is not None else 0

    def __enter__(self) -> "LazyFormalRunBundleMapping":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None
        temporary_directory = getattr(self, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()
            self._temporary_directory = None


class _RunBundleKeyView(Mapping[str, Mapping[str, Any]]):
    """只保留 condition key，value 始终委托给上游 lazy Mapping。"""

    def __init__(
        self,
        source: Mapping[str, Mapping[str, Any]],
        keys: Sequence[str],
    ) -> None:
        self._source = source
        self._keys = tuple(keys)
        self._key_set = frozenset(self._keys)

    def __getitem__(self, key: str) -> Mapping[str, Any]:
        if key not in self._key_set:
            raise KeyError(key)
        return self._source[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


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
    with LazyFormalRunBundleMapping(root) as run_bundles:
        return _recompute_metrics_from_bundles(
            root=root,
            suite=suite,
            run_bundles=run_bundles,
        )


def _recompute_metrics_from_bundles(
    *,
    root: Path,
    suite: Mapping[str, Any],
    run_bundles: Mapping[str, Mapping[str, Any]],
) -> FormalMetricsResult:
    condition_rows: list[dict[str, Any]] = []
    for condition_id in run_bundles:
        bundle = run_bundles[condition_id]
        condition_rows.append(_condition_metrics(bundle))
        del bundle

    rows_by_id = {row["condition_id"]: row for row in condition_rows}
    experiment_rows = {
        EXP1: tuple(_exp1_rows(rows_by_id, run_bundles)),
        EXP2: tuple(_exp2_rows(rows_by_id, run_bundles)),
        EXP3: tuple(_exp3_rows(rows_by_id, run_bundles)),
        EXP4: tuple(_exp4_rows(rows_by_id, run_bundles)),
        EXP5: tuple(_exp5_rows(rows_by_id, run_bundles)),
    }
    experiment_rows = {
        experiment_id: tuple(
            _with_repeat_aggregates(experiment_id, rows)
        )
        for experiment_id, rows in experiment_rows.items()
    }
    experiment_rows[EXP4] = tuple(
        _with_exp4_full_pairing(experiment_rows[EXP4])
    )
    capturing = (
        suite.get("regression_only") is True
        or suite.get("capturing") is True
    )
    required_experiment_ids = tuple(
        str(value)
        for value in suite.get("experiment_ids", ())
        if isinstance(value, str) and value
    )
    eligibility_reasons: list[str] = []
    if suite.get("paper_eligible") is not True:
        eligibility_reasons.append("suite_manifest_not_paper_eligible")
    if capturing:
        eligibility_reasons.append("capturing_or_regression_only")
    if not condition_rows or any(
        row.get("paper_eligible") is not True for row in condition_rows
    ):
        eligibility_reasons.append("condition_evidence_not_paper_eligible")
    if not required_experiment_ids or any(
        not experiment_rows.get(experiment_id)
        or any(
            row.get("paper_eligible") is not True
            for row in experiment_rows[experiment_id]
        )
        for experiment_id in required_experiment_ids
    ):
        eligibility_reasons.append("experiment_evidence_not_paper_eligible")
    paper_eligible = not eligibility_reasons
    exp5_artifact_rows = _exp5_renderer_rows(
        suite=suite,
        bundles=run_bundles,
    )
    exp5_artifact_rows_digest = (
        _digest(exp5_artifact_rows)
        if exp5_artifact_rows is not None
        else None
    )
    metrics_body = {
        "schema_version": "tokenshare.paper_formal_metrics_body.v1",
        "formal": True,
        "pilot_only": False,
        "execution_scope": "formal_matrix",
        "capturing": capturing,
        "paper_eligible": paper_eligible,
        "paper_ineligibility_reasons": eligibility_reasons,
        "exp5_artifact_rows_digest": exp5_artifact_rows_digest,
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
        exp5_artifact_rows=exp5_artifact_rows,
        require_complete_model_inventory=not capturing,
    )
    return FormalMetricsResult(
        condition_rows=tuple(condition_rows),
        experiment_rows=experiment_rows,
        metrics_digest=metrics_digest,
        paper_eligible=paper_eligible,
        capturing=capturing,
        output_refs=tuple(output_refs),
        exp5_artifact_rows=exp5_artifact_rows,
        exp5_artifact_rows_digest=exp5_artifact_rows_digest,
    )


def _condition_metrics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    condition = bundle["condition"]
    tasks = bundle["tasks"]
    attempts = bundle["attempts"]
    events = bundle["events"]
    artifacts = bundle.get("artifacts", ())
    completed = sum(_status(task.get("root_status")) == "completed" for task in tasks)
    accepted_valid = sum(_task_validity(task) for task in tasks)
    task_count = len(tasks)
    provider_attempts = _provider_attempt_records(attempts)
    token_values = _complete_numeric_values(provider_attempts, "total_tokens")
    cost_values = _complete_numeric_values(provider_attempts, "cost_estimate")
    latency_values = _complete_numeric_values(provider_attempts, "latency_ms")
    if not provider_attempts:
        provider_latency_sum_ms: float | None = 0.0
        provider_latency_evidence_status = "not_applicable"
        provider_latency_unavailable_reason: str | None = "no_provider_attempts"
    elif latency_values is None:
        provider_latency_sum_ms = None
        provider_latency_evidence_status = "incomplete"
        provider_latency_unavailable_reason = "missing_provider_latency_evidence"
    else:
        provider_latency_sum_ms = float(sum(latency_values))
        provider_latency_evidence_status = "complete"
        provider_latency_unavailable_reason = None
    failure_breakdown: dict[str, int] = defaultdict(int)
    for task in tasks:
        status = _status(task.get("root_status"))
        if status != "completed":
            failure_breakdown[status] += 1
    for attempt in attempts:
        status = _status(attempt.get("attempt_status"))
        if status not in {"succeeded", "completed"}:
            failure_breakdown[status] += 1
    interval_metrics = _worker_interval_metrics(attempts)
    wall_clock_ms = (
        interval_metrics["wall_clock_ms"]
        if interval_metrics is not None
        else _wall_clock_ms(events)
    )
    critical_paths = [
        _protocol_critical_path(
            task=task,
            attempts=_records_for_task(attempts, task, tasks),
            events=_records_for_task(events, task, tasks),
        )
        for task in tasks
    ]
    critical_path_complete = bool(critical_paths) and all(
        item["critical_path_ms"] is not None for item in critical_paths
    )
    critical_path_ms = (
        sum(float(item["critical_path_ms"]) for item in critical_paths)
        if critical_path_complete
        else None
    )
    critical_path_reasons = list(
        dict.fromkeys(
            str(reason)
            for item in critical_paths
            for reason in item["paper_ineligibility_reasons"]
        )
    )
    critical_path_refs = _stable_evidence_refs(
        [
            ref
            for item in critical_paths
            for ref in item["critical_path_evidence_refs"]
        ]
    )
    # 关键路径是 Exp2 的专项指标；其他实验的完整失败终局不要求成功 merge。
    critical_path_required = condition.get("experiment_id") == EXP2
    eligibility_reasons: list[str] = []
    if interval_metrics is None:
        eligibility_reasons.append("missing_worker_interval_evidence")
    if not tasks or any(task.get("paper_eligible") is not True for task in tasks):
        eligibility_reasons.append("task_not_paper_eligible")
    if not attempts or any(
        attempt.get("paper_eligible") is not True for attempt in attempts
    ):
        eligibility_reasons.append("attempt_not_paper_eligible")
    if not artifacts:
        eligibility_reasons.append("missing_artifact_inventory")
    if critical_path_required:
        eligibility_reasons.extend(critical_path_reasons)
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
        "provider_attempt_count": len(provider_attempts),
        "total_tokens": sum(token_values) if token_values is not None else None,
        "total_cost_estimate": (
            sum(cost_values) if cost_values is not None else None
        ),
        "token_usage_missing_count": (
            0 if token_values is not None else sum(
                not _is_number(attempt.get("total_tokens"))
                for attempt in provider_attempts
            )
        ),
        "token_usage_sample_size": sum(
            _is_number(attempt.get("total_tokens"))
            for attempt in provider_attempts
        ),
        "cost_estimate_missing_count": (
            0 if cost_values is not None else sum(
                not _is_number(attempt.get("cost_estimate"))
                for attempt in provider_attempts
            )
        ),
        "cost_estimate_sample_size": sum(
            _is_number(attempt.get("cost_estimate"))
            for attempt in provider_attempts
        ),
        "wall_clock_ms": wall_clock_ms,
        "wall_clock_source": (
            "worker_execution_intervals"
            if interval_metrics is not None
            else "protocol_event_fallback"
        ),
        "critical_path_ms": critical_path_ms,
        "critical_path_source": (
            "sum_of_protocol_root_dependency_paths"
            if critical_path_complete
            else "insufficient_protocol_dependency_evidence"
        ),
        "critical_path_evidence_refs": critical_path_refs,
        "observed_peak_concurrency": (
            interval_metrics["observed_peak_concurrency"]
            if interval_metrics is not None
            else None
        ),
        "provider_latency_sum_ms": provider_latency_sum_ms,
        "provider_latency_evidence_status": provider_latency_evidence_status,
        "provider_latency_unavailable_reason": (
            provider_latency_unavailable_reason
        ),
        "provider_latency_missing_count": (
            0 if latency_values is not None else sum(
                not _is_number(attempt.get("latency_ms"))
                for attempt in provider_attempts
            )
        ),
        "provider_latency_sample_size": sum(
            _is_number(attempt.get("latency_ms"))
            for attempt in provider_attempts
        ),
        "token_p50": (
            _quantile(token_values, 0.5) if token_values is not None else None
        ),
        "token_p95": (
            _quantile(token_values, 0.95) if token_values is not None else None
        ),
        "provider_error_count": sum(
            _status(attempt.get("attempt_status")) == "provider_error"
            or (
                _status(attempt.get("attempt_status")) != "executor_error"
                and attempt.get("error_kind") is not None
            )
            for attempt in provider_attempts
        ),
        "rate_limited_attempt_count": sum(
            attempt.get("error_kind") in {"rate_limited", "429"}
            for attempt in provider_attempts
        ),
        "retry_attempt_count": max(
            0,
            len(provider_attempts)
            - len(
                {
                    str(attempt.get("unit_id"))
                    for attempt in provider_attempts
                }
            ),
        ),
        "failure_breakdown": dict(sorted(failure_breakdown.items())),
        "row_scope": "repeat_condition",
        "paper_eligible": not eligibility_reasons,
        "paper_ineligibility_reasons": eligibility_reasons,
        "formal": True,
        "pilot_only": False,
        "execution_scope": "formal_matrix",
    }


def _exp1_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        if bundle["condition"]["experiment_id"] == EXP1:
            result.append(dict(rows[condition_id]))
        del bundle
    return result


def _exp2_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        condition = bundle["condition"]
        if condition["experiment_id"] != EXP2:
            del bundle
            continue
        condition_row = dict(rows[condition_id])
        tasks = bundle["tasks"]
        for task in tasks:
            task_id = str(task.get("task_id") or "")
            task_attempts = [
                attempt
                for attempt in bundle["attempts"]
                if str(attempt.get("task_id") or "") == task_id
            ]
            if not task_attempts and len(tasks) == 1:
                task_attempts = list(bundle["attempts"])
            observation = task.get("runtime_observation")
            observation = (
                dict(observation) if isinstance(observation, Mapping) else {}
            )
            interval_metrics = _worker_interval_metrics(task_attempts)
            wall_clock_ms = _observed_time_ms(
                observation.get("runtime_wall_clock_ms")
            )
            if wall_clock_ms is None:
                wall_clock_ms = (
                    float(interval_metrics["wall_clock_ms"])
                    if interval_metrics is not None
                    else None
                )
            generation_identity = task.get("runtime_generation_identity")
            generation_identity = (
                generation_identity
                if isinstance(generation_identity, Mapping)
                else {}
            )
            run_id = generation_identity.get("run_id")
            task_events = _records_for_task(
                bundle["events"], task, tasks
            )
            critical_path = _protocol_critical_path(
                task=task,
                attempts=task_attempts,
                events=task_events,
            )
            planned_ids = _string_inventory(
                observation.get("planned_ai_unit_ids")
            )
            dispatched_ids = _string_inventory(
                observation.get("dispatched_ai_unit_ids")
            )
            completed_ids = _string_inventory(
                observation.get("completed_ai_unit_ids")
            )
            unscheduled_ids = _string_inventory(
                observation.get("unscheduled_ai_unit_ids")
            )
            in_flight_ids = _string_inventory(
                observation.get("in_flight_ai_unit_ids_at_witness")
            )
            inventory_reasons: list[str] = list(
                critical_path["paper_ineligibility_reasons"]
            )
            if not observation:
                inventory_reasons.append("missing_runtime_observation")
            elif (
                set(dispatched_ids) - set(planned_ids)
                or set(completed_ids) - set(dispatched_ids)
                or set(unscheduled_ids) != set(planned_ids) - set(dispatched_ids)
                or set(in_flight_ids) - set(dispatched_ids)
            ):
                inventory_reasons.append("invalid_runtime_ai_unit_inventory")
            if not isinstance(run_id, str) or not run_id:
                inventory_reasons.append("missing_runtime_run_identity")
                run_id = None
            if wall_clock_ms is None:
                inventory_reasons.append("missing_root_wall_clock_evidence")
            peak = observation.get("observed_peak_concurrency")
            if not isinstance(peak, int) or isinstance(peak, bool) or peak < 0:
                peak = (
                    interval_metrics["observed_peak_concurrency"]
                    if interval_metrics is not None
                    else condition_row.get("observed_peak_concurrency")
                )
            if isinstance(peak, (int, float)) and (
                peak > int(condition.get("worker_count", 1))
                or (dispatched_ids and peak > len(dispatched_ids))
            ):
                inventory_reasons.append("invalid_observed_peak_concurrency")
            provider_attempts = _provider_attempt_records(task_attempts)
            provider_latency_sum_ms = sum(
                float(_number(attempt.get("latency_ms")))
                for attempt in provider_attempts
            )
            provider_attempt_count = len(provider_attempts)
            unique_unit_count = len(
                {
                    str(attempt.get("unit_id"))
                    for attempt in provider_attempts
                    if attempt.get("unit_id") is not None
                }
            )
            worker_busy_ms = sum(
                max(
                    0.0,
                    float(_observed_time_ms(attempt["ended_at"]) or 0.0)
                    - float(_observed_time_ms(attempt["started_at"]) or 0.0),
                )
                for attempt in task_attempts
                if isinstance(attempt.get("started_at"), str)
                and isinstance(attempt.get("ended_at"), str)
            )
            worker_count = int(condition.get("worker_count", 1))
            source.append(
                _with_specialty_eligibility(
                    condition_row,
                    inventory_reasons,
                    {
                        **condition_row,
                        "task_count": 1,
                        "completed_root_count": int(
                            _status(task.get("root_status")) == "completed"
                        ),
                        "failed_root_count": int(
                            _status(task.get("root_status")) != "completed"
                        ),
                        "completion_rate": float(
                            _status(task.get("root_status")) == "completed"
                        ),
                        "accepted_validity_rate": float(
                            task.get("accepted_validity") is True
                        ),
                        "case_id": str(task.get("case_id") or task_id),
                        "task_id": task_id,
                        "run_id": run_id,
                        "factor_position_quantile": task.get(
                            "factor_position_quantile"
                        ),
                        "planned_ai_unit_count": len(planned_ids),
                        "executed_ai_unit_count": len(dispatched_ids),
                        "early_stop_unscheduled_count": len(unscheduled_ids),
                        "in_flight_after_witness_count": len(in_flight_ids),
                        "observed_peak_concurrency": peak,
                        "wall_clock_ms": wall_clock_ms,
                        "wall_clock_source": (
                            "runtime_observation"
                            if _observed_time_ms(
                                observation.get("runtime_wall_clock_ms")
                            )
                            is not None
                            else "worker_execution_intervals"
                            if interval_metrics is not None
                            else "insufficient_root_wall_clock_evidence"
                        ),
                        "critical_path_ms": critical_path["critical_path_ms"],
                        "critical_path_source": critical_path[
                            "critical_path_source"
                        ],
                        "critical_path_evidence_refs": critical_path[
                            "critical_path_evidence_refs"
                        ],
                        "provider_latency_sum_ms": provider_latency_sum_ms,
                        "provider_attempt_count": provider_attempt_count,
                        "total_tokens": sum(
                            int(_number(attempt.get("total_tokens")))
                            for attempt in provider_attempts
                        ),
                        "total_cost_estimate": sum(
                            float(_number(attempt.get("cost_estimate")))
                            for attempt in provider_attempts
                        ),
                        "cost": sum(
                            float(_number(attempt.get("cost_estimate")))
                            for attempt in provider_attempts
                        ),
                        "http_429_count": sum(
                            attempt.get("error_kind") in {"rate_limited", "429"}
                            for attempt in provider_attempts
                        ),
                        "rate_limited_attempt_count": sum(
                            attempt.get("error_kind") in {"rate_limited", "429"}
                            for attempt in provider_attempts
                        ),
                        "retry_count": max(
                            0,
                            provider_attempt_count - unique_unit_count,
                        ),
                        "retry_attempt_count": max(
                            0,
                            provider_attempt_count - unique_unit_count,
                        ),
                        "worker_utilization": (
                            min(
                                1.0,
                                worker_busy_ms
                                / (wall_clock_ms * worker_count),
                            )
                            if wall_clock_ms is not None and wall_clock_ms > 0
                            else None
                        ),
                        "row_scope": "root_run",
                    },
                )
            )
        del bundle
    baselines: dict[tuple[str, int], float] = {}
    for row in source:
        if (
            row["worker_count"] == 1
            and isinstance(row.get("wall_clock_ms"), (int, float))
            and not isinstance(row.get("wall_clock_ms"), bool)
        ):
            baselines[(str(row["case_id"]), int(row["repeat_id"]))] = float(
                row["wall_clock_ms"]
            )
    result: list[dict[str, Any]] = []
    for row in source:
        baseline = baselines.get(
            (str(row["case_id"]), int(row["repeat_id"]))
        )
        wall_clock = (
            float(row["wall_clock_ms"])
            if isinstance(row.get("wall_clock_ms"), (int, float))
            and not isinstance(row.get("wall_clock_ms"), bool)
            else 0.0
        )
        speedup = (
            baseline / wall_clock
            if baseline is not None and wall_clock > 0
            else None
        )
        result.append(
            {
                **row,
                "throughput": (
                    row["executed_ai_unit_count"] / (wall_clock / 1000.0)
                    if wall_clock > 0
                    else None
                ),
                "throughput_roots_per_second": (
                    row["completed_root_count"] / (wall_clock / 1000.0)
                    if wall_clock > 0
                    else None
                ),
                "speedup": speedup,
                "parallel_efficiency": (
                    speedup / row["worker_count"] if speedup is not None else None
                ),
                "efficiency": (
                    speedup / row["worker_count"] if speedup is not None else None
                ),
                "provider_latency_is_wall_clock": False,
            }
        )
    return result


def _exp3_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        if bundle["condition"]["experiment_id"] != EXP3:
            del bundle
            continue
        row = dict(rows[condition_id])
        faults = bundle["faults"]
        events = bundle["events"]
        attempts = bundle["attempts"]
        tasks = bundle["tasks"]
        outcomes, outcome_reasons = _exp3_fault_outcomes(
            faults=faults,
            attempts=attempts,
            events=events,
        )
        injected = len(faults)
        expected_faults = (
            bundle["condition"].get("fault_type") == "worker_death"
            or float(bundle["condition"].get("fault_rate", 0.0)) > 0
        )
        if expected_faults and not faults:
            outcome_reasons.append("missing_fault_outcome_evidence")
        detected_values = [outcome["detected"] for outcome in outcomes]
        false_accept_values = [
            outcome["wrongly_canonicalized"] for outcome in outcomes
        ]
        recoverable_values = [outcome["recoverable"] for outcome in outcomes]
        recovered_values = [outcome["recovered"] for outcome in outcomes]
        wasted_token_values = [
            outcome["wasted_actual_tokens"]
            for outcome in outcomes
            if _is_number(outcome.get("wasted_actual_tokens"))
        ]
        wasted_token_missing = len(outcomes) - len(wasted_token_values)
        reassignment_values = [
            outcome["reassignment_count"]
            for outcome in outcomes
            if _is_number(outcome.get("reassignment_count"))
        ]
        reassignment_missing = len(outcomes) - len(reassignment_values)
        complete_outcomes = sum(
            all(
                outcome[field_name] is not None
                for field_name in (
                    "detected",
                    "wrongly_canonicalized",
                    "recoverable",
                    "recovered",
                )
            )
            for outcome in outcomes
        )
        worker_faults = [
            fault
            for fault in faults
            if fault.get("fault_type") == "worker_death"
            or fault.get("schema_version") == "tokenshare.paper_worker_death.v1"
        ]
        progress, progress_reasons = _worker_kill_progress_metrics(worker_faults)
        outcome_reasons.extend(progress_reasons)
        if bundle["condition"].get("fault_type") == "worker_death":
            worker_death_metrics, worker_death_reasons = (
                _worker_death_recovery_metrics(
                    condition=bundle["condition"],
                    tasks=tasks,
                    attempts=attempts,
                    worker_faults=worker_faults,
                    events=events,
                )
            )
            outcome_reasons.extend(worker_death_reasons)
        else:
            worker_death_metrics = {
                "dead_worker_count": None,
                "actual_dead_worker_count": None,
                "target_dead_worker_count": None,
                "kill_progress": None,
                "target_kill_progress_percent": None,
                "coordinator_continued": None,
                "required_slot_count": None,
                "recovered_slot_count": None,
                "result_completeness_rate": None,
                "result_completeness_applicability": "not_applicable",
                "root_output_complete": None,
                "accepted_validity": None,
                "worker_death_evidence_refs": [],
            }
        baseline_manifest = next(
            (
                task.get("matched_baseline")
                for task in tasks
                if isinstance(task.get("matched_baseline"), Mapping)
            ),
            {},
        )
        (
            baseline_condition_id,
            baseline_condition_ids,
        ) = _matched_baseline_condition_identity(
            tasks=tasks,
            baseline_manifest=baseline_manifest,
        )
        baseline_reasons: list[str] = []
        shared_reference_source_usage: dict[str, Any] | None = None
        baseline_comparison_eligible: bool | None = None
        baseline_unavailable_reason: str | None = None
        comparison_kind = "matched_condition"
        current_condition_id = str(bundle["condition"].get("condition_id"))
        shared_references = [
            task.get("shared_exp1_reference")
            for task in tasks
            if isinstance(task.get("shared_exp1_reference"), Mapping)
        ]
        if shared_references:
            comparison_kind = "shared_reference"
            (
                baseline_metrics,
                baseline_reasons,
                shared_reference_source_usage,
                baseline_comparison_eligible,
                baseline_unavailable_reason,
            ) = _shared_exp1_reference_metrics(
                suite_root=Path(bundle["suite_root"]),
                tasks=tasks,
            )
            baseline_source = "shared_exp1_reference"
            if baseline_metrics is None:
                baseline_wall = None
                baseline_tokens = None
                baseline_cost = None
                baseline_latency = None
            else:
                baseline_wall = baseline_metrics["wall_clock_ms"]
                baseline_tokens = baseline_metrics["total_tokens"]
                baseline_cost = baseline_metrics["total_cost_estimate"]
                baseline_latency = baseline_metrics["provider_latency_sum_ms"]
        elif str(baseline_condition_id) == current_condition_id:
            baseline_row = None
            baseline_reasons.append("self_matched_baseline_forbidden")
            baseline_wall = None
            baseline_tokens = None
            baseline_cost = None
            baseline_latency = None
            baseline_source = "not_available"
        else:
            baseline_row = rows.get(str(baseline_condition_id))
        if not shared_references and baseline_row is not None:
            baseline_wall = _optional_number(baseline_row.get("wall_clock_ms"))
            baseline_tokens = _optional_number(baseline_row.get("total_tokens"))
            baseline_cost = _optional_number(
                baseline_row.get("total_cost_estimate")
            )
            baseline_latency = _optional_number(
                baseline_row.get("provider_latency_sum_ms")
            )
            baseline_source = "matched_condition_evidence"
        elif not shared_references and not baseline_reasons:
            baseline_metrics, baseline_reasons = _dedicated_baseline_metrics(
                suite_root=Path(bundle["suite_root"]),
                tasks=tasks,
                condition=bundle["condition"],
            )
            if baseline_metrics is None:
                baseline_wall = None
                baseline_tokens = None
                baseline_cost = None
                baseline_latency = None
                baseline_source = "not_available"
            else:
                baseline_wall = baseline_metrics["wall_clock_ms"]
                baseline_tokens = baseline_metrics["total_tokens"]
                baseline_cost = baseline_metrics["total_cost_estimate"]
                baseline_latency = baseline_metrics["provider_latency_sum_ms"]
                baseline_source = "dedicated_condition_evidence"
        specialty_reasons = list(
            dict.fromkeys([*outcome_reasons, *baseline_reasons])
        )
        wall_delta = (
            float(row["wall_clock_ms"]) - float(baseline_wall)
            if baseline_wall is not None
            else None
        )
        token_delta = (
            float(row["total_tokens"]) - float(baseline_tokens)
            if _is_number(row.get("total_tokens"))
            and baseline_tokens is not None
            else None
        )
        cost_delta = (
            float(row["total_cost_estimate"]) - float(baseline_cost)
            if _is_number(row.get("total_cost_estimate"))
            and baseline_cost is not None
            else None
        )
        latency_delta = (
            float(row["provider_latency_sum_ms"]) - float(baseline_latency)
            if _is_number(row.get("provider_latency_sum_ms"))
            and baseline_latency is not None
            else None
        )
        result.append(
            _with_specialty_eligibility(row, specialty_reasons, {
                **row,
                "injected_fault_count": injected,
                "detected_fault_count": _complete_boolean_sum(detected_values),
                "detection_rate": _complete_boolean_rate(detected_values),
                "wrongly_canonicalized_count": _complete_boolean_sum(
                    false_accept_values
                ),
                "false_accept_rate": _complete_boolean_rate(
                    false_accept_values
                ),
                "recoverable_fault_count": _complete_boolean_sum(
                    recoverable_values
                ),
                "recovered_fault_count": _complete_boolean_sum(
                    recovered_values
                ),
                "recovery_rate": _recovery_rate(
                    recoverable_values,
                    recovered_values,
                ),
                "outcome_evidence_complete": (
                    injected == complete_outcomes and not outcome_reasons
                ),
                "evidence_completeness_rate": _rate(
                    complete_outcomes,
                    injected,
                ),
                "recovery_latency_ms": _mean_or_none(
                    [
                        float(outcome["recovery_latency_ms"])
                        for outcome in outcomes
                        if outcome["recovered"] is True
                        and outcome["recovery_latency_ms"] is not None
                    ]
                ),
                "reassignment_count": (
                    sum(reassignment_values)
                    if reassignment_missing == 0
                    else None
                ),
                "reassignment_count_sample_size": len(reassignment_values),
                "reassignment_count_missing_count": reassignment_missing,
                "wasted_actual_tokens": (
                    sum(wasted_token_values)
                    if wasted_token_missing == 0
                    else None
                ),
                "wasted_actual_tokens_sample_size": len(wasted_token_values),
                "wasted_actual_tokens_missing_count": wasted_token_missing,
                "worker_death_count": len(worker_faults),
                "worker_replacement_complete_rate": _rate(
                    sum(value is True for value in recovered_values),
                    len(worker_faults),
                ),
                **progress,
                **worker_death_metrics,
                "matched_baseline_condition_id": baseline_condition_id,
                "matched_baseline_condition_ids": baseline_condition_ids,
                "matched_baseline_source": baseline_source,
                "matched_baseline_wall_clock_ms": baseline_wall,
                "matched_baseline_total_tokens": baseline_tokens,
                "matched_baseline_total_cost_estimate": baseline_cost,
                "matched_baseline_provider_latency_ms": baseline_latency,
                "comparison_kind": comparison_kind,
                "baseline_comparison_eligible": baseline_comparison_eligible,
                "baseline_unavailable_reason": baseline_unavailable_reason,
                "shared_reference_source_usage": shared_reference_source_usage,
                "wall_clock_overhead_ms": wall_delta,
                "wall_clock_overhead_ratio": (
                    wall_delta / baseline_wall
                    if wall_delta is not None
                    and baseline_wall is not None
                    and baseline_wall > 0
                    else None
                ),
                "token_overhead": token_delta,
                "token_overhead_ratio": (
                    token_delta / baseline_tokens
                    if token_delta is not None
                    and baseline_tokens is not None
                    and baseline_tokens > 0
                    else None
                ),
                "cost_overhead_delta": cost_delta,
                "cost_overhead_ratio": (
                    cost_delta / baseline_cost
                    if cost_delta is not None
                    and baseline_cost is not None
                    and baseline_cost > 0
                    else None
                ),
                "provider_latency_overhead_ms": latency_delta,
                "provider_latency_overhead_ratio": (
                    latency_delta / baseline_latency
                    if latency_delta is not None
                    and baseline_latency is not None
                    and baseline_latency > 0
                    else None
                ),
            })
        )
        if bundle["condition"].get("fault_type") == "worker_death":
            result.extend(
                _worker_death_task_rows(
                    base_row=row,
                    condition=bundle["condition"],
                    tasks=tasks,
                    attempts=attempts,
                    worker_faults=worker_faults,
                    events=events,
                )
            )
        del bundle
    return result


def _exp3_fault_outcomes(
    *,
    faults: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    attempts_by_id = {
        str(attempt["attempt_id"]): attempt
        for attempt in attempts
        if isinstance(attempt.get("attempt_id"), str)
        and attempt.get("attempt_id")
    }
    canonical_attempt_ids: set[str] = set()
    canonical_unit_ids: set[str] = set()
    completion_unit_ids: set[str] = set()
    verification_rejections: set[str] = set()
    late_rejections: set[str] = set()
    recovery_by_attempt: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        canonical = payload.get("canonical_selection")
        canonical = canonical if isinstance(canonical, Mapping) else payload
        selected_attempt_id = canonical.get("selected_attempt_id")
        if (
            event_type == "CANONICAL_OUTPUTS_BOUND"
            and isinstance(selected_attempt_id, str)
            and selected_attempt_id
            and _event_has_reference(event)
        ):
            canonical_attempt_ids.add(selected_attempt_id)
            unit_id = canonical.get("unit_id") or payload.get("unit_id")
            if isinstance(unit_id, str) and unit_id:
                canonical_unit_ids.add(unit_id)
        if event_type == "VERIFICATION_RECORDED":
            attempt_id = payload.get("attempt_id")
            report = payload.get("verification_report")
            report = report if isinstance(report, Mapping) else {}
            status = _status(
                payload.get("status")
                or report.get("status")
                or payload.get("new_state")
            )
            if (
                isinstance(attempt_id, str)
                and attempt_id
                and status
                and status
                not in {"passed", "accepted", "succeeded", "completed"}
                and _event_has_reference(event)
            ):
                verification_rejections.add(attempt_id)
        if event_type in {
            "LATE_SUBMISSION_REJECTED",
            "EXECUTION_SUBMISSION_REJECTED",
        }:
            attempt_id = payload.get("attempt_id")
            if (
                isinstance(attempt_id, str)
                and attempt_id
                and _event_has_reference(event)
            ):
                late_rejections.add(attempt_id)
        if event_type == "RECOVERY_ACTION_RECORDED":
            action = payload.get("recovery_action")
            action = action if isinstance(action, Mapping) else {}
            attempt_id = action.get("attempt_id")
            if (
                isinstance(attempt_id, str)
                and attempt_id
                and _event_has_reference(event)
            ):
                recovery_by_attempt[attempt_id].append(event)
        if event_type == "TASK_UNIT_STATE_CHANGED":
            state_change = payload.get("task_unit_state_change")
            state_change = (
                state_change if isinstance(state_change, Mapping) else payload
            )
            if _status(state_change.get("new_state")).lower() == "completed":
                unit_id = (
                    state_change.get("unit_id")
                    or payload.get("unit_id")
                    or event.get("object_id")
                )
                if (
                    isinstance(unit_id, str)
                    and unit_id
                    and _event_has_reference(event)
                ):
                    completion_unit_ids.add(unit_id)

    outcomes: list[dict[str, Any]] = []
    reasons: list[str] = []
    for fault in faults:
        dead_attempt = fault.get("dead_attempt")
        dead_attempt = dead_attempt if isinstance(dead_attempt, Mapping) else {}
        target_unit = fault.get("target_ai_unit")
        target_unit = target_unit if isinstance(target_unit, Mapping) else {}
        attempt_id = fault.get("attempt_id") or dead_attempt.get("attempt_id")
        unit_id = fault.get("unit_id") or target_unit.get("unit_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            reasons.append("missing_fault_attempt_evidence")
            outcomes.append(_empty_exp3_fault_outcome())
            continue
        if not isinstance(unit_id, str) or not unit_id:
            reasons.append("missing_fault_unit_evidence")
            outcomes.append(_empty_exp3_fault_outcome())
            continue
        attempt = attempts_by_id.get(attempt_id)
        if attempt is None:
            reasons.append("missing_fault_attempt_evidence")
            outcomes.append(_empty_exp3_fault_outcome())
            continue
        recovery_events = recovery_by_attempt.get(attempt_id, [])
        rejected = (
            attempt_id in verification_rejections
            or attempt_id in late_rejections
            or bool(recovery_events)
        )
        canonical_for_unit = (
            unit_id in canonical_unit_ids
            or any(
                str(item.get("unit_id") or "") == unit_id
                and item.get("canonical") is True
                for item in attempts
            )
        )
        if not canonical_for_unit:
            wrongly_canonicalized: bool | None = None
            reasons.append("missing_canonical_event_evidence")
        else:
            wrongly_canonicalized = (
                attempt_id in canonical_attempt_ids
                or attempt.get("canonical") is True
            )
        detected: bool | None
        if rejected:
            detected = True
        elif wrongly_canonicalized is True:
            detected = False
        else:
            detected = None
            reasons.append("missing_detection_event_evidence")
        retry_allowed = any(
            _recovery_action(event).get("retry_allowed") is True
            for event in recovery_events
        )
        replacements = [
            item
            for item in attempts
            if item.get("attempt_id") != attempt_id
            and str(item.get("unit_id") or "") == unit_id
        ]
        if detected is False:
            recoverable: bool | None = False
        elif recovery_events and retry_allowed and replacements:
            recoverable = True
        elif detected is True:
            recoverable = None
            reasons.append("missing_recovery_event_evidence")
        else:
            recoverable = None
        successful_replacements = [
            item
            for item in replacements
            if _status(item.get("attempt_status")) in {"succeeded", "completed"}
        ]
        canonical_replacement = next(
            (
                item
                for item in successful_replacements
                if item.get("attempt_id") in canonical_attempt_ids
                or item.get("canonical") is True
            ),
            None,
        )
        if recoverable is False:
            recovered: bool | None = False
        elif recoverable is True:
            if canonical_replacement is None:
                recovered = None
                reasons.append("missing_canonical_event_evidence")
            elif unit_id not in completion_unit_ids:
                recovered = None
                reasons.append("missing_completion_event_evidence")
            else:
                recovered = True
        else:
            recovered = None
        recovery_latency_ms = None
        if recovered is True and canonical_replacement is not None:
            ended_at = _observed_time_ms(attempt.get("ended_at"))
            replacement_started_at = _observed_time_ms(
                canonical_replacement.get("started_at")
            )
            if ended_at is None or replacement_started_at is None:
                reasons.append("missing_recovery_timing_evidence")
            else:
                interval_ms = replacement_started_at - ended_at
                if interval_ms < 0:
                    reasons.append("invalid_recovery_timing_evidence")
                else:
                    recovery_latency_ms = interval_ms
        outcomes.append(
            {
                "detected": detected,
                "wrongly_canonicalized": wrongly_canonicalized,
                "recoverable": recoverable,
                "recovered": recovered,
                "recovery_latency_ms": recovery_latency_ms,
                "reassignment_count": len(recovery_events),
                "wasted_actual_tokens": _optional_number(
                    attempt.get("total_tokens")
                ),
            }
        )
    return outcomes, list(dict.fromkeys(reasons))


def _empty_exp3_fault_outcome() -> dict[str, Any]:
    return {
        "detected": None,
        "wrongly_canonicalized": None,
        "recoverable": None,
        "recovered": None,
        "recovery_latency_ms": None,
        "reassignment_count": None,
        "wasted_actual_tokens": None,
    }


def _event_has_reference(event: Mapping[str, Any]) -> bool:
    event_id = event.get("event_id")
    return isinstance(event_id, str) and bool(event_id)


def _recovery_action(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    action = payload.get("recovery_action")
    return action if isinstance(action, Mapping) else {}


def _complete_boolean_sum(values: Sequence[bool | None]) -> int | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value is True for value in values)


def _complete_boolean_rate(values: Sequence[bool | None]) -> float | None:
    count = _complete_boolean_sum(values)
    return count / len(values) if count is not None and values else None


def _recovery_rate(
    recoverable_values: Sequence[bool | None],
    recovered_values: Sequence[bool | None],
) -> float | None:
    if (
        not recoverable_values
        or len(recoverable_values) != len(recovered_values)
        or any(value is None for value in recoverable_values)
        or any(value is None for value in recovered_values)
    ):
        return None
    denominator = sum(value is True for value in recoverable_values)
    if denominator == 0:
        return None
    return (
        sum(
            recoverable is True and recovered is True
            for recoverable, recovered in zip(
                recoverable_values,
                recovered_values,
                strict=True,
            )
        )
        / denominator
    )


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _worker_kill_progress_metrics(
    worker_faults: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    empty = {
        "kill_progress_target_ratio": None,
        "kill_progress_actual_ratio_min": None,
        "kill_progress_actual_ratio_max": None,
        "kill_progress_actual_ratio_mean": None,
        "kill_progress_completed_ai_unit_count": None,
        "kill_progress_total_ai_unit_count": None,
        "kill_progress_error_count": None,
    }
    if not worker_faults:
        return empty, []
    observations: list[dict[str, Any]] = []
    for fault in worker_faults:
        target_ratio = fault.get("kill_progress_target_ratio")
        actual_ratio = fault.get("kill_progress_actual_ratio")
        completed_count = fault.get("kill_progress_completed_ai_unit_count")
        total_count = fault.get("kill_progress_total_ai_unit_count")
        observed_at = fault.get("kill_progress_observed_at")
        error = fault.get("kill_progress_error")
        if (
            isinstance(target_ratio, bool)
            or not isinstance(target_ratio, (int, float))
            or isinstance(actual_ratio, bool)
            or not isinstance(actual_ratio, (int, float))
            or isinstance(completed_count, bool)
            or not isinstance(completed_count, int)
            or isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or not isinstance(observed_at, str)
            or not observed_at
            or total_count < 1
            or completed_count < 0
            or completed_count > total_count
            or abs(actual_ratio - completed_count / total_count) > 1e-9
            or actual_ratio < target_ratio
        ):
            return empty, ["missing_worker_kill_progress_evidence"]
        observations.append(
            {
                "target_ratio": float(target_ratio),
                "actual_ratio": float(actual_ratio),
                "completed_count": completed_count,
                "total_count": total_count,
                "error": error,
            }
        )
    target_ratios = {item["target_ratio"] for item in observations}
    total_counts = {item["total_count"] for item in observations}
    reasons = []
    if len(target_ratios) != 1 or len(total_counts) != 1:
        reasons.append("inconsistent_worker_kill_progress_evidence")
    error_count = sum(item["error"] is not None for item in observations)
    if error_count:
        reasons.append("worker_kill_progress_observation_error")
    actual_ratios = [item["actual_ratio"] for item in observations]
    return {
        "kill_progress_target_ratio": (
            next(iter(target_ratios)) if len(target_ratios) == 1 else None
        ),
        "kill_progress_actual_ratio_min": min(actual_ratios),
        "kill_progress_actual_ratio_max": max(actual_ratios),
        "kill_progress_actual_ratio_mean": _mean_or_none(actual_ratios),
        "kill_progress_completed_ai_unit_count": sum(
            item["completed_count"] for item in observations
        ),
        "kill_progress_total_ai_unit_count": (
            next(iter(total_counts)) if len(total_counts) == 1 else None
        ),
        "kill_progress_error_count": error_count,
    }, reasons


def _worker_death_recovery_metrics(
    *,
    condition: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    worker_faults: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """只从死亡记录与死亡后的协议事件投影 worker-death 正式字段。"""

    reasons: list[str] = []
    evidence_refs: list[dict[str, Any]] = []
    expected_dead = _first_int(
        condition.get("dead_worker_count"),
        condition.get("dead_worker_count_target"),
        *(task.get("dead_worker_count_target") for task in tasks),
    )
    target_progress_percent = _first_number(
        condition.get("kill_progress_percent"),
        condition.get("kill_progress_target_percent"),
        *(task.get("kill_progress_target_percent") for task in tasks),
    )
    if expected_dead is None or expected_dead < 1:
        reasons.append("missing_worker_death_target_evidence")
    if target_progress_percent is None:
        reasons.append("missing_worker_kill_progress_target_evidence")

    event_by_id = {
        str(event["event_id"]): event
        for event in events
        if isinstance(event.get("event_id"), str) and event.get("event_id")
    }
    graphs: list[tuple[str, ...]] = []
    actual_progress: list[float] = []
    killed_units: set[str] = set()
    replacement_attempt_by_unit: dict[str, str] = {}
    coordinator_continued = bool(worker_faults)
    for fault in worker_faults:
        schema_version = fault.get("schema_version")
        if schema_version == "tokenshare.paper_worker_death_incomplete.v1":
            if (
                fault.get("recovery_completed") is not False
                or fault.get("evidence_complete") is not False
                or fault.get("replacement_fact") is not None
            ):
                reasons.append("invalid_incomplete_worker_death_record")
            reasons.append("incomplete_worker_death_recovery")
        elif schema_version != "tokenshare.paper_worker_death.v1":
            reasons.append("worker_death_schema_mismatch")
        record_ref = fault.get("record_ref")
        if isinstance(record_ref, Mapping) and record_ref:
            evidence_refs.append(dict(record_ref))
        else:
            reasons.append("missing_worker_death_record_ref")
        target = fault.get("target_ai_unit")
        target = target if isinstance(target, Mapping) else {}
        unit_id = target.get("unit_id")
        replacement = fault.get("replacement_attempt")
        replacement = replacement if isinstance(replacement, Mapping) else {}
        replacement_attempt_id = replacement.get("attempt_id")
        if (
            not isinstance(unit_id, str)
            or not unit_id
            or unit_id in killed_units
            or replacement.get("unit_id") != unit_id
            or not isinstance(replacement_attempt_id, str)
            or not replacement_attempt_id
        ):
            reasons.append("invalid_worker_replacement_identity")
        else:
            killed_units.add(unit_id)
            replacement_attempt_by_unit[unit_id] = replacement_attempt_id
        death_exitcode = fault.get("worker_process_exitcode")
        replacement_exitcode = fault.get("replacement_process_exitcode")
        if (
            not isinstance(death_exitcode, int)
            or isinstance(death_exitcode, bool)
            or death_exitcode == 0
            or not isinstance(replacement_exitcode, int)
            or isinstance(replacement_exitcode, bool)
            or replacement_exitcode != 0
        ):
            reasons.append("worker_process_death_or_replacement_not_proven")

        graph = fault.get("dependency_graph")
        graph = graph if isinstance(graph, Mapping) else {}
        unit_ids = _string_inventory(graph.get("unit_ids"))
        graph_count = graph.get("expected_ai_unit_count")
        if (
            graph.get("schema_version")
            != "tokenshare.paper_ai_unit_dependency_graph.v1"
            or not isinstance(graph_count, int)
            or isinstance(graph_count, bool)
            or graph_count < 1
            or len(unit_ids) != graph_count
        ):
            reasons.append("invalid_worker_death_task_graph")
        else:
            graphs.append(unit_ids)

        actual_ratio = fault.get("kill_progress_actual_ratio")
        if isinstance(actual_ratio, (int, float)) and not isinstance(
            actual_ratio, bool
        ):
            actual_progress.append(float(actual_ratio))
        else:
            reasons.append("missing_worker_kill_progress_evidence")
        if target_progress_percent is not None:
            persisted_target = fault.get("kill_progress_target_ratio")
            if (
                not isinstance(persisted_target, (int, float))
                or isinstance(persisted_target, bool)
                or abs(float(persisted_target) - target_progress_percent / 100.0)
                > 1e-9
            ):
                reasons.append("worker_kill_progress_target_mismatch")

        killed_at = _observed_time_ms(fault.get("killed_at"))
        referenced_ids = {
            str(ref.get("event_id")) if isinstance(ref, Mapping) else str(ref)
            for ref in fault.get("protocol_event_refs", ())
            if (isinstance(ref, Mapping) and ref.get("event_id"))
            or (isinstance(ref, str) and ref)
        }
        post_death_events = [
            event_by_id[event_id]
            for event_id in referenced_ids
            if event_id in event_by_id
            and killed_at is not None
            and (_event_time_ms(event_by_id[event_id]) or float("-inf")) > killed_at
            and event_by_id[event_id].get("event_type")
            in {
                "LEASE_STATE_CHANGED",
                "RECOVERY_ACTION_RECORDED",
                "EXECUTION_ATTEMPT_CREATED",
                "EXECUTION_REQUESTED",
                "TASK_UNIT_STATE_CHANGED",
            }
        ]
        coordinator_continued = coordinator_continued and bool(post_death_events)
        for event in post_death_events:
            ref = _event_evidence_ref(event)
            if ref is not None:
                evidence_refs.append(ref)
    if expected_dead is not None and len(worker_faults) != expected_dead:
        reasons.append("actual_dead_worker_count_mismatch")
    if worker_faults and not coordinator_continued:
        reasons.append("coordinator_post_death_scheduling_not_proven")

    graph_inventory = {graph for graph in graphs}
    required_units = next(iter(graph_inventory)) if len(graph_inventory) == 1 else ()
    if worker_faults and len(graph_inventory) != 1:
        reasons.append("inconsistent_worker_death_task_graph")
    required_slot_count = len(required_units) if required_units else None
    merge_links = [
        event for event in events if event.get("event_type") == "MERGE_TASK_LINK_RECORDED"
    ]
    merge_required_counts = {
        _nested_mapping(event, "merge_task_link").get("required_slot_count")
        for event in merge_links
        if isinstance(
            _nested_mapping(event, "merge_task_link").get("required_slot_count"),
            int,
        )
    }
    if (
        required_slot_count is None
        or merge_required_counts != {required_slot_count}
        or not merge_links
    ):
        reasons.append("required_slot_inventory_not_proven")
    for event in merge_links:
        ref = _event_evidence_ref(event)
        if ref is not None:
            evidence_refs.append(ref)

    attempts_by_id = {
        str(attempt["attempt_id"]): attempt
        for attempt in attempts
        if isinstance(attempt.get("attempt_id"), str) and attempt.get("attempt_id")
    }
    canonical_by_unit: dict[str, str] = {}
    for event in events:
        if event.get("event_type") != "CANONICAL_OUTPUTS_BOUND":
            continue
        selection = _nested_mapping(event, "canonical_selection")
        unit_id = selection.get("unit_id")
        attempt_id = selection.get("selected_attempt_id")
        if isinstance(unit_id, str) and isinstance(attempt_id, str):
            canonical_by_unit[unit_id] = attempt_id
            ref = _event_evidence_ref(event)
            if ref is not None:
                evidence_refs.append(ref)
    recovered_units = {
        unit_id
        for unit_id in required_units
        if (attempt_id := canonical_by_unit.get(unit_id)) in attempts_by_id
        and (
            unit_id not in replacement_attempt_by_unit
            or replacement_attempt_by_unit[unit_id] == attempt_id
        )
    }
    recovered_slot_count = len(recovered_units) if required_slot_count is not None else None
    completeness_rate = _rate(recovered_slot_count, required_slot_count)
    completeness_applicability = (
        "observed"
        if required_slot_count is not None and required_slot_count > 0
        else "zero_denominator"
        if required_slot_count == 0
        else "insufficient_evidence"
    )
    if required_slot_count == 0:
        reasons.append("zero_required_slot_denominator")
    elif required_slot_count is not None and recovered_slot_count != required_slot_count:
        reasons.append("worker_death_result_incomplete")

    merge_records = [event for event in events if event.get("event_type") == "MERGE_RECORDED"]
    for event in merge_records:
        ref = _event_evidence_ref(event)
        if ref is not None:
            evidence_refs.append(ref)
    root_ids = {
        identity.get("root_unit_id")
        for task in tasks
        if isinstance((identity := task.get("runtime_generation_identity")), Mapping)
        and isinstance(identity.get("root_unit_id"), str)
    }
    root_events = [
        event
        for event in events
        if event.get("event_type") in {"ROOT_COMPLETED", "TASK_COMPLETED"}
        or (
            event.get("event_type") == "TASK_UNIT_STATE_CHANGED"
            and _status(
                _nested_mapping(event, "task_unit_state_change").get("new_state")
            ).lower()
            == "completed"
            and (
                not root_ids
                or event.get("object_id") in root_ids
                or _event_payload(event).get("unit_id") in root_ids
            )
        )
    ]
    for event in root_events:
        ref = _event_evidence_ref(event)
        if ref is not None:
            evidence_refs.append(ref)
    validity_values = [task.get("accepted_validity") for task in tasks]
    accepted_validity = (
        all(value is True for value in validity_values)
        if validity_values and all(isinstance(value, bool) for value in validity_values)
        else None
    )
    if accepted_validity is None:
        reasons.append("missing_root_checker_validity_evidence")
    root_output_complete = bool(
        required_slot_count
        and recovered_slot_count == required_slot_count
        and merge_records
        and root_events
        and accepted_validity is not None
    )
    if not root_output_complete:
        reasons.append("root_output_completion_not_proven")
    return {
        "dead_worker_count": len(worker_faults),
        "actual_dead_worker_count": len(worker_faults),
        "target_dead_worker_count": expected_dead,
        "kill_progress": _mean_or_none(actual_progress),
        "target_kill_progress_percent": target_progress_percent,
        "coordinator_continued": coordinator_continued,
        "required_slot_count": required_slot_count,
        "recovered_slot_count": recovered_slot_count,
        "result_completeness_rate": completeness_rate,
        "result_completeness_applicability": completeness_applicability,
        "root_output_complete": root_output_complete,
        "accepted_validity": accepted_validity,
        "worker_death_evidence_refs": _stable_evidence_refs(evidence_refs),
    }, list(dict.fromkeys(reasons))


def _worker_death_task_rows(
    *,
    base_row: Mapping[str, Any],
    condition: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
    worker_faults: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """逐 task 投影 worker-death 恢复事实，不用条件汇总替代单题证据。"""

    rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        task_attempts = _records_for_task(attempts, task, tasks)
        provider_attempts = _provider_attempt_records(task_attempts)
        token_values = _complete_numeric_values(provider_attempts, "total_tokens")
        cost_values = _complete_numeric_values(provider_attempts, "cost_estimate")
        task_faults = _records_for_task(worker_faults, task, tasks)
        task_events = _records_for_task(events, task, tasks)
        metrics, reasons = _worker_death_recovery_metrics(
            condition=condition,
            tasks=[task],
            attempts=task_attempts,
            worker_faults=task_faults,
            events=task_events,
        )
        completed = int(_status(task.get("root_status")) == "completed")
        rows.append(
            _with_specialty_eligibility(
                base_row,
                reasons,
                {
                    **dict(base_row),
                    "row_scope": "task",
                    "case_id": str(task.get("case_id") or task_id),
                    "task_id": task_id,
                    "task_count": 1,
                    "completed_root_count": completed,
                    "failed_root_count": 1 - completed,
                    "completion_rate": float(completed),
                    "provider_attempt_count": len(provider_attempts),
                    "total_tokens": (
                        sum(token_values) if token_values is not None else None
                    ),
                    "total_cost_estimate": (
                        sum(cost_values) if cost_values is not None else None
                    ),
                    "token_usage_sample_size": sum(
                        _is_number(attempt.get("total_tokens"))
                        for attempt in provider_attempts
                    ),
                    "token_usage_missing_count": sum(
                        not _is_number(attempt.get("total_tokens"))
                        for attempt in provider_attempts
                    ),
                    "cost_estimate_sample_size": sum(
                        _is_number(attempt.get("cost_estimate"))
                        for attempt in provider_attempts
                    ),
                    "cost_estimate_missing_count": sum(
                        not _is_number(attempt.get("cost_estimate"))
                        for attempt in provider_attempts
                    ),
                    **metrics,
                },
            )
        )
    return rows


def _first_int(*values: Any) -> int | None:
    return next(
        (
            value
            for value in values
            if isinstance(value, int) and not isinstance(value, bool)
        ),
        None,
    )


def _first_number(*values: Any) -> float | None:
    value = next(
        (
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ),
        None,
    )
    return float(value) if value is not None else None


def _matched_baseline_condition_identity(
    *,
    tasks: Sequence[Mapping[str, Any]],
    baseline_manifest: Mapping[str, Any],
) -> tuple[str | None, list[str]]:
    """聚合 condition 内全部 baseline source，避免首项冒充唯一来源。"""

    condition_ids = sorted(
        {
            value
            for task in tasks
            for value in (task.get("matched_baseline_condition_id"),)
            if isinstance(value, str) and value
        }
    )
    manifest_condition_id = baseline_manifest.get("condition_id")
    if not condition_ids and isinstance(manifest_condition_id, str):
        condition_ids.append(manifest_condition_id)
    return (
        condition_ids[0] if len(condition_ids) == 1 else None,
        condition_ids,
    )


def _shared_exp1_reference_metrics(
    *,
    suite_root: Path,
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    list[str],
    dict[str, Any] | None,
    bool,
    str | None,
]:
    references = [
        task.get("shared_exp1_reference")
        for task in tasks
        if isinstance(task.get("shared_exp1_reference"), Mapping)
    ]
    if len(references) != len(tasks) or not references:
        return None, ["missing_shared_exp1_reference"], None, False, (
            "missing_shared_exp1_reference"
        )

    source_usage: dict[str, Any] = {
        "provider_attempt_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
    }
    unavailable_reasons: list[str] = []
    frozen_usages: list[Mapping[str, Any] | None] = []
    for task, reference in zip(tasks, references, strict=True):
        if (
            reference.get("schema_version")
            != "tokenshare.paper_exp1_shared_reference.v1"
            or reference.get("source_experiment_id")
            != "exp1_real_ai_feasibility"
            or reference.get("source_case_id") != task.get("task_id")
            or reference.get("source_task_id") != task.get("task_id")
            or reference.get("source_condition_id")
            != task.get("matched_baseline_condition_id")
            or reference.get("evidence_integrity") != "complete"
        ):
            return None, ["shared_exp1_reference_identity_mismatch"], None, False, (
                "shared_exp1_reference_identity_mismatch"
            )
        reference_core = {
            key: value
            for key, value in reference.items()
            if key
            not in {
                "source_hash",
                "source_reference_id",
                "planned_source_reference_id",
                "reference_policy_id",
            }
        }
        if reference.get("source_hash") != _digest(reference_core):
            return None, ["shared_exp1_reference_hash_mismatch"], None, False, (
                "shared_exp1_reference_hash_mismatch"
            )
        if not _valid_shared_execution_versions(reference.get("source_versions")):
            return None, ["invalid_shared_exp1_source_versions"], None, False, (
                "invalid_shared_exp1_source_versions"
            )
        usage = reference.get("source_usage")
        frozen_usages.append(usage if isinstance(usage, Mapping) else None)
        if reference.get("baseline_comparison_eligible") is not True:
            reason = reference.get("baseline_unavailable_reason")
            unavailable_reasons.append(
                str(reason) if isinstance(reason, str) and reason else (
                    "shared_exp1_comparison_unavailable"
                )
            )

    totals = {
        "wall_clock_ms": 0.0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "provider_latency_sum_ms": 0.0,
    }
    usage_failure_reason: str | None = None
    usage_failure_value: dict[str, Any] | None = None
    for task, reference, frozen_usage in zip(
        tasks,
        references,
        frozen_usages,
        strict=True,
    ):
        task_record = _record_from_frozen_reference(
            suite_root=suite_root,
            reference=reference.get("source_task_ref"),
        )
        source_status = (
            _status(task_record.get("root_status")).lower()
            if task_record is not None
            else "unknown"
        )
        frozen_status = _status(reference.get("source_root_status")).lower()
        if (
            task_record is None
            or source_status not in _SHARED_SOURCE_TERMINAL_STATUSES
            or source_status != frozen_status
            or task_record.get("experiment_id") != reference.get("source_experiment_id")
            or task_record.get("condition_id") != reference.get("source_condition_id")
            or task_record.get("task_id") != reference.get("source_task_id")
            or task_record.get("repeat_id") != reference.get("source_repeat_id")
        ):
            return None, ["invalid_shared_exp1_task_reference"], source_usage, False, (
                "invalid_shared_exp1_task_reference"
            )
        source_versions = reference.get("source_versions")
        runtime_generation_identity = task_record.get("runtime_generation_identity")
        if (
            not isinstance(source_versions, Mapping)
            or not isinstance(runtime_generation_identity, Mapping)
            or source_versions.get("runtime_generation_schema_version")
            != runtime_generation_identity.get("schema_version")
            or source_versions.get("runtime_generation_identity_digest")
            != _digest(runtime_generation_identity)
        ):
            return None, ["shared_exp1_runtime_identity_mismatch"], source_usage, (
                False
            ), "shared_exp1_runtime_identity_mismatch"
        attempt_refs = reference.get("source_attempt_refs")
        if not isinstance(attempt_refs, Sequence) or isinstance(
            attempt_refs,
            (str, bytes),
        ) or not attempt_refs:
            return None, ["missing_shared_exp1_attempt_references"], source_usage, (
                False
            ), "missing_shared_exp1_attempt_references"
        attempts: list[Mapping[str, Any]] = []
        for attempt_ref in attempt_refs:
            attempt = _record_from_frozen_reference(
                suite_root=suite_root,
                reference=attempt_ref,
            )
            if attempt is None:
                return None, ["invalid_shared_exp1_attempt_reference"], (
                    source_usage
                ), False, "invalid_shared_exp1_attempt_reference"
            attempts.append(attempt)
        event_refs = reference.get("source_event_refs")
        if not isinstance(event_refs, Sequence) or isinstance(
            event_refs,
            (str, bytes),
        ) or not event_refs:
            return None, ["missing_shared_exp1_event_references"], source_usage, (
                False
            ), "missing_shared_exp1_event_references"
        if any(
            _record_from_frozen_reference(
                suite_root=suite_root,
                reference=event_ref,
            )
            is None
            for event_ref in event_refs
        ):
            return None, ["invalid_shared_exp1_event_reference"], source_usage, (
                False
            ), "invalid_shared_exp1_event_reference"
        artifact_refs = reference.get("source_artifact_refs")
        if not isinstance(artifact_refs, Sequence) or isinstance(
            artifact_refs,
            (str, bytes),
        ) or (
            not artifact_refs and source_status in _SHARED_SOURCE_SUCCESS_STATUSES
        ):
            return None, ["missing_shared_exp1_artifact_references"], source_usage, (
                False
            ), "missing_shared_exp1_artifact_references"
        if any(
            not _valid_frozen_artifact_reference(
                suite_root=suite_root,
                reference=artifact_ref,
            )
            for artifact_ref in artifact_refs
        ):
            return None, ["invalid_shared_exp1_artifact_reference"], source_usage, (
                False
            ), "invalid_shared_exp1_artifact_reference"

        recomputed_usage = _recompute_shared_source_usage(
            task=task_record,
            attempts=attempts,
        )
        if frozen_usage is None:
            current_usage_failure = "missing_shared_exp1_source_usage"
        elif not _shared_source_usage_matches(
            frozen=frozen_usage,
            recomputed=recomputed_usage,
        ):
            current_usage_failure = "shared_exp1_source_usage_mismatch"
        elif (
            recomputed_usage.get("usage_complete") is not True
            or recomputed_usage.get("usage_missing_provider_attempt_count") != 0
        ):
            current_usage_failure = "shared_exp1_source_usage_incomplete"
        else:
            current_usage_failure = None
        if current_usage_failure is not None:
            if usage_failure_reason is None:
                usage_failure_reason = current_usage_failure
                usage_failure_value = (
                    dict(frozen_usage) if frozen_usage is not None else None
                )
        else:
            assert frozen_usage is not None
            for field_name in (
                "provider_attempt_count",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                source_usage[field_name] += int(frozen_usage[field_name])
            source_usage["cost_estimate"] += float(frozen_usage["cost_estimate"])

        wall_clock_ms = _observed_time_ms(task_record.get("wall_clock_ms"))
        if wall_clock_ms is None:
            intervals = _worker_interval_metrics(attempts)
            wall_clock_ms = (
                float(intervals["wall_clock_ms"])
                if intervals is not None
                else None
            )
        if (
            wall_clock_ms is None
            and reference.get("baseline_comparison_eligible") is True
        ):
            return None, ["missing_shared_exp1_timing_evidence"], source_usage, (
                False
            ), "missing_shared_exp1_timing_evidence"
        totals["wall_clock_ms"] += wall_clock_ms or 0.0
        shared_provider_attempts = _shared_provider_attempt_records(attempts)
        latency_values = _complete_numeric_values(
            shared_provider_attempts,
            "latency_ms",
        )
        if latency_values is None or totals["provider_latency_sum_ms"] is None:
            totals["provider_latency_sum_ms"] = None
        else:
            totals["provider_latency_sum_ms"] += sum(latency_values)

    source_usage["cost_estimate"] = round(
        float(source_usage["cost_estimate"]),
        12,
    )
    if usage_failure_reason is not None:
        return None, [usage_failure_reason], usage_failure_value, False, (
            usage_failure_reason
        )
    if unavailable_reasons:
        reasons = list(dict.fromkeys(unavailable_reasons))
        return None, reasons, source_usage, False, reasons[0]
    totals["total_tokens"] = int(source_usage["total_tokens"])
    totals["total_cost_estimate"] = float(source_usage["cost_estimate"])
    return totals, [], source_usage, True, None


def _record_from_frozen_reference(
    *,
    suite_root: Path,
    reference: Any,
) -> Mapping[str, Any] | None:
    if not isinstance(reference, Mapping):
        return None
    relative_path = reference.get("path")
    record_hash = reference.get("record_hash")
    if not isinstance(relative_path, str) or not isinstance(record_hash, str):
        return None
    path = suite_root / relative_path
    if not path.is_file():
        return None
    matches = [record for record in _read_jsonl(path) if _digest(record) == record_hash]
    return matches[0] if len(matches) == 1 else None


def _valid_frozen_artifact_reference(
    *,
    suite_root: Path,
    reference: Any,
) -> bool:
    if not isinstance(reference, Mapping):
        return False
    relative_path = reference.get("path")
    content_hash = reference.get("content_hash")
    if not isinstance(relative_path, str) or not isinstance(content_hash, str):
        return False
    path = suite_root / relative_path
    return path.is_file() and content_hash == _hash_file(path)


def _valid_shared_execution_versions(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("schema_version") != "tokenshare.paper_execution_version_identity.v1":
        return False
    for field_name in (
        "plugin_version",
        "parser_version",
        "verifier_version",
        "executor_version",
        "prompt_version",
        "runtime_generation_schema_version",
    ):
        if not isinstance(value.get(field_name), str) or not value.get(field_name):
            return False
    return all(
        isinstance(value.get(field_name), str)
        and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value[field_name]))
        for field_name in (
            "split_profile_digest",
            "runtime_generation_identity_digest",
        )
    )


def _recompute_shared_source_usage(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provider_attempts = _shared_provider_attempt_records(attempts)
    expected_count = task.get("provider_attempt_count")
    count_complete = (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count > 0
        and expected_count == len(provider_attempts)
    )
    complete_attempts: list[Mapping[str, Any]] = []
    currencies: set[str] = set()
    cost_statuses: set[str] = set()
    for attempt in provider_attempts:
        token_values = [
            attempt.get(field_name)
            for field_name in ("prompt_tokens", "completion_tokens", "total_tokens")
        ]
        cost_value = attempt.get("cost_estimate")
        currency = attempt.get("cost_estimate_currency")
        cost_status = attempt.get("cost_estimate_status")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in token_values
            )
            or isinstance(cost_value, bool)
            or not isinstance(cost_value, (int, float))
            or float(cost_value) < 0
            or not isinstance(currency, str)
            or not currency
            or cost_status != "estimated"
            or attempt.get("usage_missing") is True
        ):
            continue
        complete_attempts.append(attempt)
        currencies.add(currency)
        cost_statuses.add(cost_status)
    usage_complete = (
        count_complete
        and len(complete_attempts) == len(provider_attempts)
        and len(currencies) == 1
        and cost_statuses == {"estimated"}
    )
    expected_count_value = (
        int(expected_count)
        if isinstance(expected_count, int) and not isinstance(expected_count, bool)
        else None
    )
    missing_count = max(expected_count_value or 0, len(provider_attempts)) - len(
        complete_attempts
    )
    return {
        "provider_attempt_count": len(provider_attempts),
        "expected_provider_attempt_count": expected_count_value,
        "prompt_tokens": (
            sum(int(item["prompt_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "completion_tokens": sum(
            int(item["completion_tokens"]) for item in complete_attempts
        ) if usage_complete else None,
        "total_tokens": (
            sum(int(item["total_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "cost_estimate": round(
            sum(float(item["cost_estimate"]) for item in complete_attempts),
            12,
        ) if usage_complete else None,
        "usage_complete": usage_complete,
        "usage_missing_provider_attempt_count": missing_count,
        "cost_estimate_status": "estimated" if usage_complete else "usage_missing",
        "cost_estimate_currency": next(iter(currencies)) if usage_complete else None,
    }


def _shared_source_usage_matches(
    *,
    frozen: Mapping[str, Any],
    recomputed: Mapping[str, Any],
) -> bool:
    """按 JSON 标量类型和值比较，避免 Python 的 ``True == 1`` 别名。"""

    return all(
        type(frozen.get(field_name)) is type(expected_value)
        and frozen.get(field_name) == expected_value
        for field_name, expected_value in recomputed.items()
    )


def _shared_provider_attempt_records(
    attempts: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        attempt
        for attempt in attempts
        if (
            isinstance(attempt.get("provider_attempt_count"), int)
            and not isinstance(attempt.get("provider_attempt_count"), bool)
            and int(attempt["provider_attempt_count"]) > 0
        )
        or isinstance(attempt.get("model_execution_record_ref"), Mapping)
    )


def _dedicated_baseline_metrics(
    *,
    suite_root: Path,
    tasks: Sequence[Mapping[str, Any]],
    condition: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    references = [
        task.get("matched_baseline_evidence_ref")
        for task in tasks
        if isinstance(task.get("matched_baseline_evidence_ref"), Mapping)
    ]
    if len(references) != len(tasks) or not references:
        return None, ["missing_matched_baseline_evidence"]
    bodies: list[Mapping[str, Any]] = []
    for task, reference in zip(tasks, references, strict=True):
        relative_path = reference.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            return None, ["missing_matched_baseline_evidence"]
        path = suite_root / relative_path
        if (
            not path.is_file()
            or reference.get("content_hash") != _hash_file(path)
        ):
            return None, ["invalid_matched_baseline_evidence_ref"]
        body = _read_json(path)
        condition_id = task.get("matched_baseline_condition_id")
        if (
            body.get("schema_version")
            != "tokenshare.paper_exp3_baseline_evidence.v1"
            or body.get("condition_id") != condition_id
            or reference.get("condition_id") != condition_id
            or body.get("case_id") != task.get("task_id")
            or reference.get("case_id") != task.get("task_id")
            or body.get("repeat_id") != condition.get("repeat_id", 0)
            or body.get("seed") != condition.get("seed")
            or body.get("worker_count") != condition.get("worker_count")
        ):
            return None, ["matched_baseline_identity_mismatch"]
        bodies.append(body)
    totals = {
        "wall_clock_ms": 0.0,
        "total_tokens": 0,
        "total_cost_estimate": 0.0,
        "provider_latency_sum_ms": 0.0,
    }
    for body in bodies:
        task = body.get("task_result")
        task = task if isinstance(task, Mapping) else {}
        attempts = body.get("attempt_results")
        attempts = (
            attempts
            if isinstance(attempts, Sequence)
            and not isinstance(attempts, (str, bytes))
            else ()
        )
        events = body.get("event_records")
        events = (
            events
            if isinstance(events, Sequence)
            and not isinstance(events, (str, bytes))
            else ()
        )
        if (
            _status(task.get("root_status")) != "completed"
            or not attempts
            or not events
            or not any(attempt.get("canonical") is True for attempt in attempts)
        ):
            return None, ["incomplete_matched_baseline_evidence"]
        wall_clock_ms = _observed_time_ms(task.get("wall_clock_ms"))
        if wall_clock_ms is None:
            run_evidence = body.get("run_evidence")
            run_evidence = (
                run_evidence if isinstance(run_evidence, Mapping) else {}
            )
            protocol_runtime = run_evidence.get("protocol_runtime")
            protocol_runtime = (
                protocol_runtime
                if isinstance(protocol_runtime, Mapping)
                else {}
            )
            runtime_observation = protocol_runtime.get("runtime_observation")
            runtime_observation = (
                runtime_observation
                if isinstance(runtime_observation, Mapping)
                else {}
            )
            wall_clock_ms = _observed_time_ms(
                runtime_observation.get("runtime_wall_clock_ms")
            )
        if wall_clock_ms is None:
            intervals = _worker_interval_metrics(attempts)
            wall_clock_ms = (
                float(intervals["wall_clock_ms"])
                if intervals is not None
                else None
            )
        if wall_clock_ms is None:
            return None, ["missing_matched_baseline_timing_evidence"]
        provider_attempts = _provider_attempt_records(attempts)
        token_values = _complete_numeric_values(provider_attempts, "total_tokens")
        cost_values = _complete_numeric_values(provider_attempts, "cost_estimate")
        latency_values = _complete_numeric_values(provider_attempts, "latency_ms")
        totals["wall_clock_ms"] += wall_clock_ms
        for field_name, values in (
            ("total_tokens", token_values),
            ("total_cost_estimate", cost_values),
            ("provider_latency_sum_ms", latency_values),
        ):
            if values is None or totals[field_name] is None:
                totals[field_name] = None
            else:
                totals[field_name] += sum(values)
    return totals, []


def _exp4_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        if bundle["condition"]["experiment_id"] != EXP4:
            del bundle
            continue
        base_row = rows[condition_id]
        task_rows = [
            _exp4_task_row(
                base_row=base_row,
                task=task,
                attempts=bundle["attempts"],
                events=bundle["events"],
                artifacts=bundle.get("artifacts", ()),
            )
            for task in bundle["tasks"]
        ]
        result.append(
            _exp4_rollup_row(
                base_row=base_row,
                source_rows=task_rows,
                row_scope="repeat_condition",
            )
        )
        result.extend(task_rows)
        del bundle
    return result


def _exp4_task_row(
    *,
    base_row: Mapping[str, Any],
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]] = (),
    artifacts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("case_id") or "unknown")
    task_attempts = [
        attempt
        for attempt in attempts
        if str(attempt.get("task_id") or task_id) == task_id
    ]
    runtime_audit = _exp4_runtime_audit(
        base_row=base_row,
        task=task,
        task_attempts=task_attempts,
        events=events,
        artifacts=artifacts,
    )
    attempt_observations = runtime_audit["attempt_observations"]
    hook_observations = runtime_audit["hook_observations"]

    invalid_attempt_ids = {
        str(observation.get("attempt_id") or f"observation-{index}")
        for index, observation in enumerate(attempt_observations)
        if observation.get("independent_candidate_validity") is False
    }
    wrong_canonical_ids = {
        str(observation.get("attempt_id") or f"observation-{index}")
        for index, observation in enumerate(attempt_observations)
        if observation.get("independent_candidate_validity") is False
        and bool(observation.get("canonical_output_refs"))
    }
    raw_only_observations = [
        observation
        for observation in attempt_observations
        if observation.get("raw_output_ref") is not None
        and observation.get("candidate_output_ref")
        == observation.get("raw_output_ref")
    ]
    raw_only_acceptances = [
        observation
        for observation in raw_only_observations
        if bool(observation.get("canonical_output_refs"))
    ]
    requeue_gate_observed = any(
        observation.get("event_type") == "EXPERIMENT_ABLATION_GATE_APPLIED"
        and observation.get("disabled_mechanism") == "requeue"
        and bool(observation.get("protocol_event_refs"))
        for observation in hook_observations
    )
    stuck_task_count = int(
        requeue_gate_observed
        and _status(task.get("root_status")) != "completed"
    )
    premature_merge_observations = [
        observation
        for observation in hook_observations
        if observation.get("event_type")
        == "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED"
    ]
    premature_merge_failure_count = sum(
        observation.get("root_check_passed") is False
        for observation in premature_merge_observations
    )
    premature_merge_escape_count = sum(
        observation.get("root_check_passed") is True
        for observation in premature_merge_observations
    )

    exposed_error_count = (
        len(invalid_attempt_ids)
        + len(premature_merge_observations)
        + int(stuck_task_count > 0 and not invalid_attempt_ids)
    )
    escaped_error_count = (
        len(wrong_canonical_ids) + premature_merge_escape_count
    )
    applicability, escape_rate = _exp4_error_escape(
        exposed_error_count=exposed_error_count,
        escaped_error_count=escaped_error_count,
        stuck_task_count=stuck_task_count,
        wrong_canonical_count=len(wrong_canonical_ids),
        premature_merge_attempt_count=len(premature_merge_observations),
    )
    evidence_complete = not runtime_audit["reasons"]
    wrong_canonical_count = len(wrong_canonical_ids) if evidence_complete else None
    raw_only_exposure_count = (
        len(raw_only_observations) if evidence_complete else None
    )
    raw_only_acceptance_count = (
        len(raw_only_acceptances) if evidence_complete else None
    )
    premature_merge_attempt_count = (
        len(premature_merge_observations) if evidence_complete else None
    )
    invalid_candidate_count = len(invalid_attempt_ids) if evidence_complete else None
    exposed_metric = exposed_error_count if evidence_complete else None
    escaped_metric = escaped_error_count if evidence_complete else None
    stuck_metric = stuck_task_count if evidence_complete else None
    applicability = applicability if evidence_complete else "insufficient_evidence"
    escape_rate = escape_rate if evidence_complete else None
    reasons = list(runtime_audit["reasons"])
    completed = int(_status(task.get("root_status")) == "completed")
    provider_attempts = _provider_attempt_records(task_attempts)
    token_values = _complete_numeric_values(provider_attempts, "total_tokens")
    cost_values = _complete_numeric_values(provider_attempts, "cost_estimate")
    total_tokens = sum(token_values) if token_values is not None else None
    total_cost = sum(cost_values) if cost_values is not None else None
    return _with_specialty_eligibility(
        base_row,
        reasons,
        {
            **dict(base_row),
            "case_id": str(task.get("case_id") or task_id),
            "task_id": task_id,
            "task_count": 1,
            "completed_root_count": completed,
            "failed_root_count": 1 - completed,
            "completion_rate": float(completed),
            "accepted_validity_rate": float(
                task.get("accepted_validity") is True
            ),
            "provider_attempt_count": len(provider_attempts),
            "total_tokens": total_tokens,
            "total_cost_estimate": total_cost,
            "cost": total_cost,
            "token_usage_sample_size": sum(
                _is_number(attempt.get("total_tokens"))
                for attempt in provider_attempts
            ),
            "token_usage_missing_count": sum(
                not _is_number(attempt.get("total_tokens"))
                for attempt in provider_attempts
            ),
            "cost_estimate_sample_size": sum(
                _is_number(attempt.get("cost_estimate"))
                for attempt in provider_attempts
            ),
            "cost_estimate_missing_count": sum(
                not _is_number(attempt.get("cost_estimate"))
                for attempt in provider_attempts
            ),
            "wrong_canonical_count": wrong_canonical_count,
            "wrong_canonical_acceptance_count": wrong_canonical_count,
            "invalid_candidate_count": invalid_candidate_count,
            "wrong_canonical_acceptance_rate": _rate(
                wrong_canonical_count,
                invalid_candidate_count,
            ),
            "wrong_canonical_acceptance_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_VERIFICATION",
                denominator=invalid_candidate_count,
                evidence_complete=evidence_complete,
            ),
            "raw_only_exposure_count": raw_only_exposure_count,
            "raw_only_count": raw_only_exposure_count,
            "raw_only_acceptance_count": raw_only_acceptance_count,
            "raw_only_acceptance_rate": _rate(
                raw_only_acceptance_count,
                raw_only_exposure_count,
            ),
            "raw_only_acceptance_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_PARSER_POLICY",
                denominator=raw_only_exposure_count,
                evidence_complete=evidence_complete,
            ),
            "stuck_task_count": stuck_metric,
            "stuck_count": stuck_metric,
            "stuck_task_rate": (
                float(stuck_metric) if stuck_metric is not None else None
            ),
            "stuck_task_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_REQUEUE",
                denominator=1 if evidence_complete else None,
                evidence_complete=evidence_complete,
            ),
            "premature_merge_attempt_count": premature_merge_attempt_count,
            "premature_merge_count": premature_merge_attempt_count,
            "premature_merge_failure_count": (
                premature_merge_failure_count if evidence_complete else None
            ),
            "premature_merge_failure_rate": _rate(
                premature_merge_failure_count if evidence_complete else None,
                premature_merge_attempt_count,
            ),
            "premature_merge_failure_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_MERGE_GATE",
                denominator=premature_merge_attempt_count,
                evidence_complete=evidence_complete,
            ),
            "slot_mismatch_count": None,
            "slot_mismatch_applicability": "not_applicable_removed_formal_mode",
            "exposed_error_count": exposed_metric,
            "escaped_error_count": escaped_metric,
            "error_escape_rate": escape_rate,
            "error_escape_applicability": applicability,
            "ablation_evidence_refs": runtime_audit["evidence_refs"],
            "row_scope": "task",
        },
    )


def _exp4_runtime_audit(
    *,
    base_row: Mapping[str, Any],
    task: Mapping[str, Any],
    task_attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    runtime = task.get("ablation_runtime")
    reasons: list[str] = []
    evidence_refs: list[dict[str, Any]] = []
    if not isinstance(runtime, Mapping):
        return {
            "attempt_observations": [],
            "hook_observations": [],
            "evidence_refs": [],
            "reasons": ["missing_ablation_observation"],
        }
    if runtime.get("schema_version") != "tokenshare.paper_ablation_runtime.v1":
        reasons.append("ablation_runtime_schema_mismatch")
    expected_identity = {
        "condition_id": base_row.get("condition_id"),
        "case_id": task.get("case_id") or task.get("task_id"),
        "repeat_id": base_row.get("repeat_id"),
        "mode": base_row.get("ablation_mode"),
    }
    for field_name, expected in expected_identity.items():
        if runtime.get(field_name) != expected:
            reasons.append(
                "ablation_mode_mismatch"
                if field_name == "mode"
                else f"ablation_{field_name}_mismatch"
            )
    raw_attempts = runtime.get("attempt_observations")
    raw_hooks = runtime.get("hook_observations")
    if not isinstance(raw_attempts, list) or any(
        not isinstance(item, Mapping) for item in raw_attempts
    ):
        reasons.append("invalid_ablation_attempt_observations")
        attempt_observations: list[Mapping[str, Any]] = []
    else:
        attempt_observations = list(raw_attempts)
    if not isinstance(raw_hooks, list) or any(
        not isinstance(item, Mapping) for item in raw_hooks
    ):
        reasons.append("invalid_ablation_hook_observations")
        hook_observations: list[Mapping[str, Any]] = []
    else:
        hook_observations = list(raw_hooks)

    expected_attempt_ids = {
        str(attempt.get("attempt_id"))
        for attempt in task_attempts
        if isinstance(attempt.get("attempt_id"), str)
        and attempt.get("attempt_id")
    }
    observed_attempt_ids = {
        str(observation.get("attempt_id"))
        for observation in attempt_observations
        if isinstance(observation.get("attempt_id"), str)
        and observation.get("attempt_id")
    }
    if not expected_attempt_ids or observed_attempt_ids != expected_attempt_ids:
        reasons.append("incomplete_ablation_attempt_inventory")
    for observation in attempt_observations:
        refs = _observation_refs(observation)
        if not refs:
            reasons.append("missing_ablation_attempt_evidence_ref")
        evidence_refs.extend(refs)

    mode = str(base_row.get("ablation_mode") or "")
    target_mechanism = {
        "FULL": None,
        "NO_VERIFICATION": "verification",
        "NO_PARSER_POLICY": "parser_policy",
        "NO_REQUEUE": "requeue",
        "NO_MERGE_GATE": "merge_gate",
    }.get(mode)
    if mode not in {
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
    }:
        reasons.append("unsupported_ablation_mode")
    for observation in hook_observations:
        observed_mode = observation.get("ablation_mode", observation.get("mode"))
        if observed_mode is not None and observed_mode != mode:
            reasons.append("ablation_hook_mode_mismatch")
        refs = _observation_refs(observation)
        if not refs:
            reasons.append("missing_ablation_hook_evidence_ref")
        evidence_refs.extend(refs)
    if any(
        not _ablation_observation_refs_resolve(
            observation,
            events=events,
            artifacts=artifacts,
        )
        for observation in (*attempt_observations, *hook_observations)
    ):
        reasons.append("unresolved_ablation_evidence_ref")
    if target_mechanism is not None:
        applied = [
            observation
            for observation in hook_observations
            if observation.get("event_type") == "EXPERIMENT_ABLATION_GATE_APPLIED"
            and observation.get("disabled_mechanism") == target_mechanism
        ]
        not_applicable = [
            observation
            for observation in hook_observations
            if observation.get("disabled_mechanism") == target_mechanism
            and observation.get("applicability") == "not_applicable"
            and isinstance(observation.get("not_applicable_reason"), str)
            and observation.get("not_applicable_reason")
        ]
        if not applied and not not_applicable:
            reasons.append("target_ablation_hook_not_observed")
        for observation in (*applied, *not_applicable):
            hook_input = observation.get("hook_input")
            hook_result = observation.get("hook_result")
            if (
                not isinstance(hook_input, Mapping)
                or not hook_input
                or not isinstance(hook_result, Mapping)
                or not hook_result
            ):
                reasons.append("incomplete_ablation_hook_observation")
                continue
            if observation in applied and (
                not isinstance(hook_result.get("bypass"), bool)
                or not isinstance(hook_result.get("stop"), bool)
            ):
                reasons.append("incomplete_ablation_hook_observation")
    if mode == "NO_MERGE_GATE":
        premature = [
            observation
            for observation in hook_observations
            if observation.get("event_type")
            == "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED"
        ]
        if not premature or any(
            observation.get("attempt_status") != "executed"
            or not isinstance(observation.get("root_check_passed"), bool)
            or not isinstance(observation.get("result_artifact_ref"), Mapping)
            for observation in premature
        ):
            reasons.append("missing_premature_merge_execution_evidence")
    return {
        "attempt_observations": attempt_observations,
        "hook_observations": hook_observations,
        "evidence_refs": _stable_evidence_refs(evidence_refs),
        "reasons": list(dict.fromkeys(reasons)),
    }


def _observation_refs(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for field_name in (
        "raw_output_ref",
        "candidate_output_ref",
        "result_artifact_ref",
        "merge_attempt_ref",
        "merge_result_ref",
        "root_check_ref",
    ):
        value = observation.get(field_name)
        if isinstance(value, Mapping):
            refs.append({"evidence_kind": field_name, **dict(value)})
    canonical = observation.get("canonical_output_refs")
    if isinstance(canonical, Mapping):
        refs.extend(
            {"evidence_kind": "canonical_output_ref", **dict(value)}
            for value in canonical.values()
            if isinstance(value, Mapping)
        )
    for field_name in ("protocol_event_refs", "artifact_refs"):
        values = observation.get(field_name)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            refs.extend(
                {"evidence_kind": field_name, **dict(value)}
                if isinstance(value, Mapping)
                else {"evidence_kind": field_name, "evidence_id": value}
                for value in values
                if isinstance(value, Mapping)
                or isinstance(value, str)
                and value
            )
    return refs


def _ablation_observation_refs_resolve(
    observation: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    event_ids = {
        str(event.get("event_id"))
        for event in events
        if isinstance(event.get("event_id"), str) and event.get("event_id")
    }
    protocol_refs = observation.get("protocol_event_refs", ())
    if isinstance(protocol_refs, Sequence) and not isinstance(
        protocol_refs, (str, bytes)
    ):
        for ref in protocol_refs:
            event_id = ref.get("event_id") if isinstance(ref, Mapping) else ref
            if not isinstance(event_id, str) or event_id not in event_ids:
                return False
    artifact_values: list[Any] = []
    for field_name in (
        "raw_output_ref",
        "candidate_output_ref",
        "result_artifact_ref",
        "merge_attempt_ref",
        "merge_result_ref",
        "root_check_ref",
    ):
        if observation.get(field_name) is not None:
            artifact_values.append(observation[field_name])
    canonical = observation.get("canonical_output_refs")
    if isinstance(canonical, Mapping):
        artifact_values.extend(canonical.values())
    explicit_artifacts = observation.get("artifact_refs", ())
    if isinstance(explicit_artifacts, Sequence) and not isinstance(
        explicit_artifacts, (str, bytes)
    ):
        artifact_values.extend(explicit_artifacts)
    return all(
        _artifact_ref_in_inventory(ref, artifacts) for ref in artifact_values
    )


def _artifact_ref_in_inventory(
    value: Any,
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    stable_identity_fields = ("artifact_id", "content_hash")
    if any(
        not isinstance(value.get(field_name), str)
        or not value.get(field_name)
        for field_name in stable_identity_fields
    ):
        return False
    comparable_fields = ("artifact_id", "content_hash", "path", "uri")
    provided_fields = tuple(
        field_name for field_name in comparable_fields if field_name in value
    )
    if any(
        not isinstance(value[field_name], str) or not value[field_name]
        for field_name in provided_fields
    ):
        return False
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        if any(
            artifact.get(field_name) != value[field_name]
            for field_name in provided_fields
        ):
            continue
        return True
    return False


def _exp4_rollup_row(
    *,
    base_row: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    row_scope: str,
) -> dict[str, Any]:
    task_count = sum(int(row["task_count"]) for row in source_rows)
    wrong_canonical_count = _sum_complete_field(source_rows, "wrong_canonical_count")
    invalid_candidate_count = _sum_complete_field(source_rows, "invalid_candidate_count")
    raw_only_exposure_count = _sum_complete_field(source_rows, "raw_only_exposure_count")
    raw_only_acceptance_count = _sum_complete_field(source_rows, "raw_only_acceptance_count")
    stuck_task_count = _sum_complete_field(source_rows, "stuck_task_count")
    premature_merge_attempt_count = _sum_complete_field(
        source_rows, "premature_merge_attempt_count"
    )
    premature_merge_failure_count = _sum_complete_field(
        source_rows, "premature_merge_failure_count"
    )
    exposed_error_count = _sum_complete_field(source_rows, "exposed_error_count")
    escaped_error_count = _sum_complete_field(source_rows, "escaped_error_count")
    if all(
        value is not None
        for value in (
            exposed_error_count,
            escaped_error_count,
            stuck_task_count,
            wrong_canonical_count,
            premature_merge_attempt_count,
        )
    ):
        applicability, escape_rate = _exp4_error_escape(
            exposed_error_count=exposed_error_count,
            escaped_error_count=escaped_error_count,
            stuck_task_count=stuck_task_count,
            wrong_canonical_count=wrong_canonical_count,
            premature_merge_attempt_count=premature_merge_attempt_count,
        )
    else:
        applicability, escape_rate = "insufficient_evidence", None
    reasons = list(
        dict.fromkeys(
            str(reason)
            for row in source_rows
            for reason in row.get("paper_ineligibility_reasons", ())
        )
    )
    return _with_specialty_eligibility(
        base_row,
        reasons,
        {
            **dict(base_row),
            "case_id": None,
            "task_id": None,
            "task_count": task_count,
            "completed_root_count": sum(
                int(row["completed_root_count"]) for row in source_rows
            ),
            "failed_root_count": sum(
                int(row["failed_root_count"]) for row in source_rows
            ),
            "completion_rate": _rate(
                sum(int(row["completed_root_count"]) for row in source_rows),
                task_count,
            ),
            "provider_attempt_count": sum(
                int(row["provider_attempt_count"]) for row in source_rows
            ),
            "total_tokens": _sum_complete_numeric_field(
                source_rows, "total_tokens"
            ),
            "total_cost_estimate": _sum_complete_numeric_field(
                source_rows, "total_cost_estimate"
            ),
            "cost": _sum_complete_numeric_field(
                source_rows, "total_cost_estimate"
            ),
            "token_usage_sample_size": sum(
                int(row.get("token_usage_sample_size", 0))
                for row in source_rows
            ),
            "token_usage_missing_count": sum(
                int(row.get("token_usage_missing_count", 0))
                for row in source_rows
            ),
            "cost_estimate_sample_size": sum(
                int(row.get("cost_estimate_sample_size", 0))
                for row in source_rows
            ),
            "cost_estimate_missing_count": sum(
                int(row.get("cost_estimate_missing_count", 0))
                for row in source_rows
            ),
            "wrong_canonical_count": wrong_canonical_count,
            "wrong_canonical_acceptance_count": wrong_canonical_count,
            "invalid_candidate_count": invalid_candidate_count,
            "wrong_canonical_acceptance_rate": _rate(
                wrong_canonical_count,
                invalid_candidate_count,
            ),
            "wrong_canonical_acceptance_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_VERIFICATION",
                denominator=invalid_candidate_count,
                evidence_complete=invalid_candidate_count is not None,
            ),
            "raw_only_exposure_count": raw_only_exposure_count,
            "raw_only_count": raw_only_exposure_count,
            "raw_only_acceptance_count": raw_only_acceptance_count,
            "raw_only_acceptance_rate": _rate(
                raw_only_acceptance_count,
                raw_only_exposure_count,
            ),
            "raw_only_acceptance_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_PARSER_POLICY",
                denominator=raw_only_exposure_count,
                evidence_complete=raw_only_exposure_count is not None,
            ),
            "stuck_task_count": stuck_task_count,
            "stuck_count": stuck_task_count,
            "stuck_task_rate": _rate(stuck_task_count, task_count),
            "stuck_task_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_REQUEUE",
                denominator=task_count,
                evidence_complete=stuck_task_count is not None,
            ),
            "premature_merge_attempt_count": premature_merge_attempt_count,
            "premature_merge_count": premature_merge_attempt_count,
            "premature_merge_failure_count": premature_merge_failure_count,
            "premature_merge_failure_rate": _rate(
                premature_merge_failure_count,
                premature_merge_attempt_count,
            ),
            "premature_merge_failure_applicability": _exp4_rate_applicability(
                mode=str(base_row.get("ablation_mode") or ""),
                target_mode="NO_MERGE_GATE",
                denominator=premature_merge_attempt_count,
                evidence_complete=premature_merge_attempt_count is not None,
            ),
            "slot_mismatch_count": None,
            "slot_mismatch_applicability": "not_applicable_removed_formal_mode",
            "exposed_error_count": exposed_error_count,
            "escaped_error_count": escaped_error_count,
            "error_escape_rate": escape_rate,
            "error_escape_applicability": applicability,
            "row_scope": row_scope,
        },
    )


def _sum_complete_field(
    rows: Sequence[Mapping[str, Any]], field_name: str
) -> int | None:
    values = [row.get(field_name) for row in rows]
    if not values or any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        return None
    return sum(values)


def _sum_complete_numeric_field(
    rows: Sequence[Mapping[str, Any]], field_name: str
) -> int | float | None:
    values = [row.get(field_name) for row in rows]
    if not values or any(not _is_number(value) for value in values):
        return None
    return sum(values)


def _exp4_error_escape(
    *,
    exposed_error_count: int,
    escaped_error_count: int,
    stuck_task_count: int,
    wrong_canonical_count: int,
    premature_merge_attempt_count: int,
) -> tuple[str, float | None]:
    if escaped_error_count > exposed_error_count:
        raise ValueError("escaped_error_count cannot exceed exposed_error_count")
    if exposed_error_count == 0:
        return "zero_denominator", None
    if (
        stuck_task_count > 0
        and wrong_canonical_count == 0
        and premature_merge_attempt_count == 0
    ):
        return "not_applicable", None
    return "applicable", escaped_error_count / exposed_error_count


def _exp4_rate_applicability(
    *,
    mode: str,
    target_mode: str,
    denominator: int | None,
    evidence_complete: bool,
) -> str:
    if not evidence_complete or denominator is None:
        return "insufficient_evidence"
    if mode != target_mode:
        return "not_applicable"
    if denominator == 0:
        return "zero_denominator"
    return "applicable"


def _strict_exp5_model_rows(
    bundle: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """返回通过完整 attempt inventory 对账后的 Exp5 join rows。"""

    return tuple(_exp5_identity_inventory(bundle)["rows"])


def _exp5_identity_inventory(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """将应有 attempt inventory 与持久化 v2 identity record 一一对账。"""

    expected: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    reasons: list[str] = []
    for attempt in bundle.get("attempts", ()):
        if not isinstance(attempt, Mapping):
            reasons.append("invalid_expected_attempt_record")
            continue
        key = _exp5_join_key(attempt)
        if key is None:
            reasons.append("missing_expected_attempt_identity")
            continue
        if key in expected:
            reasons.append("duplicate_expected_attempt_identity")
            continue
        expected[key] = attempt

    items: list[dict[str, Any]] = []
    for task in bundle.get("tasks", ()):
        if not isinstance(task, Mapping):
            reasons.append("invalid_model_execution_task")
            continue
        raw_items = task.get("model_execution_records", ())
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            reasons.append("invalid_model_execution_record_inventory")
            continue
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                reasons.append("invalid_model_execution_record")
                continue
            item = dict(raw_item)
            nested_task = item.get("task")
            task_body = dict(nested_task) if isinstance(nested_task, Mapping) else {}
            task_body.update(
                {
                    "condition_id": task.get("condition_id"),
                    "repeat_id": task.get("repeat_id"),
                    "task_id": task.get("task_id"),
                    "paper_eligible": task.get("paper_eligible"),
                }
            )
            item["task"] = task_body
            items.append(item)

    actual_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    invalid_record_count = 0
    for item in items:
        record = item.get("record", item)
        joined_attempt = item.get("attempt")
        if (
            not isinstance(record, Mapping)
            or record.get("schema_version")
            != "tokenshare.paper_model_execution_record.v2"
            or not isinstance(joined_attempt, Mapping)
        ):
            invalid_record_count += 1
            reasons.append("invalid_model_execution_v2_record")
            continue
        key = _exp5_join_key({**dict(record), **{
            "provider_attempt_index": joined_attempt.get("provider_attempt_index")
        }})
        if key is None:
            invalid_record_count += 1
            reasons.append("missing_model_execution_join_identity")
            continue
        actual_by_key[key].append(item)

    missing_keys = sorted(set(expected) - set(actual_by_key), key=str)
    orphan_keys = sorted(set(actual_by_key) - set(expected), key=str)
    duplicate_record_count = sum(
        max(0, len(records) - 1) for records in actual_by_key.values()
    )
    if missing_keys:
        reasons.append("missing_model_execution_v2_record")
    if orphan_keys:
        reasons.append("orphan_model_execution_v2_record")
    if duplicate_record_count:
        reasons.append("duplicate_model_execution_v2_record")

    rows: list[dict[str, Any]] = []
    mismatch_count = 0
    provider_attempt_denominator = 0
    provider_attempt_covered = 0
    for key, attempt in expected.items():
        provider_attempt_count = attempt.get("provider_attempt_count")
        if (
            isinstance(provider_attempt_count, bool)
            or not isinstance(provider_attempt_count, int)
            or provider_attempt_count < 1
        ):
            reasons.append("missing_provider_attempt_inventory")
            continue
        provider_attempt_denominator += provider_attempt_count
        records = actual_by_key.get(key, ())
        if len(records) != 1:
            continue
        item = records[0]
        try:
            joined = build_exp5_model_execution_rows(
                {"model_execution_records": [item]}
            )
        except (KeyError, TypeError, ValueError):
            mismatch_count += 1
            reasons.append("model_execution_join_invalid")
            continue
        if len(joined) != 1:
            mismatch_count += 1
            reasons.append("model_execution_join_invalid")
            continue
        row = dict(joined[0])
        row["provider_attempt_index"] = key[-1]
        row["provider_attempt_count"] = provider_attempt_count
        usage = item.get("usage")
        if isinstance(usage, Mapping):
            for field_name in (
                "reasoning_tokens",
                "visible_output_tokens",
                "visible_output_basis",
            ):
                row[field_name] = usage.get(field_name)
            pricing_snapshot = usage.get("pricing_snapshot")
            row["pricing_snapshot_digest"] = (
                _digest(pricing_snapshot)
                if isinstance(pricing_snapshot, Mapping)
                else None
            )
        else:
            row.update(
                {
                    "reasoning_tokens": None,
                    "visible_output_tokens": None,
                    "visible_output_basis": None,
                    "pricing_snapshot_digest": None,
                }
            )
        rows.append(row)
        record = item.get("record", item)
        provider_attempts = (
            record.get("actual_provider_attempts", ())
            if isinstance(record, Mapping)
            else ()
        )
        if (
            not isinstance(provider_attempts, Sequence)
            or isinstance(provider_attempts, (str, bytes))
            or len(provider_attempts) != provider_attempt_count
            or any(not isinstance(value, Mapping) for value in provider_attempts)
        ):
            mismatch_count += 1
            reasons.append("provider_attempt_identity_coverage_mismatch")
        elif _exp5_identity_is_auditable(row):
            provider_attempt_covered += provider_attempt_count
        else:
            mismatch_count += 1
            reasons.append("model_identity_mismatch")

    stable_reasons = list(dict.fromkeys(reasons))
    return {
        "rows": rows,
        "identity_denominator": len(expected),
        "model_execution_v2_record_count": len(items),
        "missing_record_count": len(missing_keys),
        "duplicate_record_count": duplicate_record_count,
        "orphan_record_count": len(orphan_keys),
        "mismatch_record_count": mismatch_count + invalid_record_count,
        "provider_attempt_identity_denominator": provider_attempt_denominator,
        "provider_attempt_identity_covered_count": provider_attempt_covered,
        "paper_eligible": bool(expected) and not stable_reasons,
        "paper_ineligibility_reasons": stable_reasons,
        "missing_join_keys": [list(key) for key in missing_keys],
        "orphan_join_keys": [list(key) for key in orphan_keys],
    }


def _exp5_join_key(record: Mapping[str, Any]) -> tuple[Any, ...] | None:
    values = (
        record.get("condition_id"),
        record.get("repeat_id"),
        record.get("run_id"),
        record.get("task_id"),
        record.get("unit_id"),
        record.get("attempt_id"),
        record.get("provider_attempt_index"),
    )
    if (
        any(value is None or value == "" for value in values)
        or isinstance(values[1], bool)
        or not isinstance(values[1], int)
        or isinstance(values[-1], bool)
        or not isinstance(values[-1], int)
    ):
        return None
    return values


def _exp5_rows(
    rows: Mapping[str, Mapping[str, Any]],
    bundles: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        condition = bundle["condition"]
        if condition["experiment_id"] != EXP5:
            del bundle
            continue
        identity_audit = _exp5_identity_inventory(bundle)
        strict_rows = tuple(identity_audit["rows"])
        matches = sum(
            _strict_exp5_identity_matches(record, condition)
            for record in strict_rows
        )
        auditable = sum(_exp5_identity_is_auditable(record) for record in strict_rows)
        identity_inventory_denominator = int(identity_audit["identity_denominator"])
        identity_not_observed = sum(
            record.get("identity_status") == "not_observed"
            and _exp5_identity_is_auditable(record)
            for record in strict_rows
        )
        identity_denominator = sum(
            record.get("identity_status") != "not_observed"
            for record in strict_rows
        )
        identity_status = (
            "not_observed"
            if not strict_rows
            else "matched"
            if matches == identity_inventory_denominator
            else "not_observed"
            if auditable == identity_inventory_denominator
            else "model_identity_mismatch"
        )
        specialty_reasons: list[str] = []
        specialty_reasons.extend(identity_audit["paper_ineligibility_reasons"])
        if auditable != identity_inventory_denominator:
            specialty_reasons.append("model_identity_mismatch")
        if any(not _exp5_identity_is_auditable(record) for record in strict_rows):
            specialty_reasons.append("model_execution_join_ineligible")
        base_row = rows[condition_id]
        result.append(
            _with_specialty_eligibility(
                base_row,
                specialty_reasons,
                {
                **dict(base_row),
                "model_execution_v2_record_count": identity_audit[
                    "model_execution_v2_record_count"
                ],
                "model_identity_match_count": matches,
                "model_identity_denominator": identity_denominator,
                "model_identity_match_rate": _rate(matches, identity_denominator),
                "model_identity_not_observed_count": identity_not_observed,
                "model_execution_attempt_denominator": identity_inventory_denominator,
                "identity_status": identity_status,
                "model_identity_missing_record_count": identity_audit[
                    "missing_record_count"
                ],
                "model_identity_duplicate_record_count": identity_audit[
                    "duplicate_record_count"
                ],
                "model_identity_orphan_record_count": identity_audit[
                    "orphan_record_count"
                ],
                "model_identity_mismatch_record_count": identity_audit[
                    "mismatch_record_count"
                ],
                "provider_attempt_identity_denominator": identity_audit[
                    "provider_attempt_identity_denominator"
                ],
                "provider_attempt_identity_covered_count": identity_audit[
                    "provider_attempt_identity_covered_count"
                ],
                "endpoint_error_count": int(base_row["provider_error_count"]),
                "provider_confounding": "model_provider_endpoint_pair",
                },
            )
        )
        del bundle
    return result


def _exp5_v3_case_records(
    bundles: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """从正式 checkpoint 生成 Task 7 所需的 root-level 配对记录。"""

    result: list[dict[str, Any]] = []
    for condition_id in sorted(bundles):
        bundle = bundles[condition_id]
        condition = bundle["condition"]
        if (
            condition.get("experiment_id") != EXP5
            or condition.get("schema_version") != "tokenshare.paper_condition.v3"
        ):
            del bundle
            continue
        identity_eligible = _exp5_identity_inventory(bundle)["paper_eligible"] is True
        attempts = tuple(
            attempt
            for attempt in bundle.get("attempts", ())
            if isinstance(attempt, Mapping)
        )
        timed_attempts = tuple(
            attempt
            for attempt in attempts
            if _observed_time_ms(attempt.get("started_at")) is not None
            and _observed_time_ms(attempt.get("ended_at")) is not None
        )
        started_values = [
            attempt.get("started_at")
            for attempt in timed_attempts
        ]
        ended_values = [
            attempt.get("ended_at")
            for attempt in timed_attempts
        ]
        condition_started_at = (
            min(
                started_values,
                key=lambda value: float(_observed_time_ms(value)),
            )
            if started_values
            else None
        )
        condition_ended_at = (
            max(
                ended_values,
                key=lambda value: float(_observed_time_ms(value)),
            )
            if ended_values
            else None
        )
        observed_peak = (
            _attempt_interval_peak(timed_attempts) if timed_attempts else None
        )
        tasks = tuple(
            task
            for task in bundle.get("tasks", ())
            if isinstance(task, Mapping)
        )
        for task in sorted(
            tasks,
            key=lambda value: str(value.get("case_id") or value.get("task_id")),
        ):
            case_id = task.get("case_id") or task.get("task_id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError("Exp5 v3 task requires a case identity")
            task_attempts = _records_for_task(attempts, task, tasks)
            provider_attempts = _provider_attempt_records(task_attempts)
            token_values = (
                _complete_numeric_values(provider_attempts, "total_tokens")
                if provider_attempts
                else None
            )
            latency_values = (
                _complete_numeric_values(provider_attempts, "latency_ms")
                if provider_attempts
                else None
            )
            root_status = task.get("root_status")
            root_completed = (
                _status(root_status) == "completed"
                if root_status is not None
                else None
            )
            accepted_validity = task.get("accepted_validity")
            if not isinstance(accepted_validity, bool):
                accepted_validity = None
            paper_eligible = (
                identity_eligible
                and task.get("paper_eligible") is True
                and all(
                    attempt.get("paper_eligible") is True
                    for attempt in task_attempts
                )
            )
            row = {
                "case_id": case_id,
                "repeat_id": condition.get("repeat_id"),
                "cohort_member_id": condition.get("cohort_member_id"),
                "condition_id": condition.get("condition_id"),
                "stratum_id": _exp5_v3_stratum_id(condition),
                "domain": condition.get("domain"),
                "topic_family": condition.get("topic_family"),
                "root_status": root_status,
                "root_completed": root_completed,
                "accepted_validity": accepted_validity,
                "total_tokens": (
                    sum(token_values) if token_values is not None else None
                ),
                "provider_latency_ms": (
                    sum(latency_values) if latency_values is not None else None
                ),
                "paper_eligible": paper_eligible,
                "order_slot": condition.get("order_slot"),
                "predecessor_member_id": condition.get(
                    "predecessor_member_id"
                ),
                "sequence_plan_digest": condition.get(
                    "sequence_plan_digest"
                ),
                "exp5_selection_digest": condition.get(
                    "exp5_selection_digest"
                ),
                "exp5_selection_parent_catalog_digest": condition.get(
                    "exp5_selection_parent_catalog_digest"
                ),
                "condition_started_at": condition_started_at,
                "condition_ended_at": condition_ended_at,
                "observed_peak_concurrency": observed_peak,
            }
            if root_completed is None:
                row["root_completed_unavailable_reason"] = (
                    "root_status_missing"
                )
            if accepted_validity is None:
                row["accepted_validity_unavailable_reason"] = (
                    "accepted_validity_missing"
                )
            if token_values is None:
                row["total_tokens_unavailable_reason"] = (
                    "provider_usage_missing"
                )
            if latency_values is None:
                row["provider_latency_ms_unavailable_reason"] = (
                    "provider_latency_missing"
                )
            result.append(row)
        del bundle
    return tuple(result)


def _exp5_renderer_rows(
    *,
    suite: Mapping[str, Any],
    bundles: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """从 formal checkpoint 派生 Exp5 renderer 的唯一输入。"""

    exp5_condition_ids: list[str] = []
    condition_reader = getattr(bundles, "condition", None)
    for condition_id in bundles:
        if callable(condition_reader):
            condition = condition_reader(condition_id)
        else:
            bundle = bundles[condition_id]
            condition = bundle.get("condition", {})
        if (
            isinstance(condition, Mapping)
            and condition.get("experiment_id") == EXP5
            and condition.get("schema_version")
            == "tokenshare.paper_condition.v3"
        ):
            exp5_condition_ids.append(condition_id)
        if not callable(condition_reader):
            del bundle
    exp5_bundles = _RunBundleKeyView(bundles, exp5_condition_ids)
    if not exp5_bundles:
        return None
    case_records = _exp5_v3_case_records(exp5_bundles)
    execution_rows = _exp5_v3_execution_rows(exp5_bundles)
    identity_complete = bool(execution_rows)
    if identity_complete:
        for condition_id in exp5_bundles:
            bundle = exp5_bundles[condition_id]
            condition_eligible = (
                _exp5_identity_inventory(bundle).get("paper_eligible") is True
            )
            del bundle
            if not condition_eligible:
                identity_complete = False
                break
    audit_statistics_records = _exp5_audit_statistics_records(case_records)
    try:
        paired_rows = tuple(
            {
                **dict(row),
                "paper_eligible": identity_complete,
                "identity_complete": identity_complete,
            }
            for row in build_exp5_paired_comparison_rows(
                audit_statistics_records
            )
        )
        order_rows = tuple(
            {
                **dict(row),
                "paper_eligible": identity_complete,
                "identity_complete": identity_complete,
            }
            for row in build_exp5_order_and_concurrency_rows(
                audit_statistics_records
            )
        )
    except ValueError:
        paired_rows = ()
        order_rows = ()
        identity_complete = False
    member_inventory: dict[str, None] = {}
    for condition_id in exp5_bundles:
        bundle = exp5_bundles[condition_id]
        member_inventory[str(bundle["condition"]["cohort_member_id"])] = None
        del bundle
    members = tuple(member_inventory)
    return {
        "suite_status": str(suite.get("status") or "incomplete"),
        "identity_complete": identity_complete,
        "overall_rows": _exp5_overall_rows(
            case_records,
            execution_rows,
            members=members,
            identity_complete=identity_complete,
        ),
        "domain_topic_rows": _exp5_domain_topic_rows(
            case_records,
            members=members,
            identity_complete=identity_complete,
        ),
        "paired_comparison_rows": paired_rows,
        "model_execution_rows": execution_rows,
        "order_concurrency_rows": order_rows,
        "failure_taxonomy_rows": _exp5_failure_taxonomy_rows(exp5_bundles),
    }


def _exp5_audit_statistics_records(
    case_records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """保留失败/未开始分母，最终输出仍由 identity_complete 标为不合格。"""

    return tuple(
        {
            **dict(row),
            # statistics validator 的该字段是输入完整性门；audit 视图不能因此
            # 丢掉已物化的失败或 not_started root。输出行会重新写入真实总门禁。
            "paper_eligible": True,
        }
        for row in case_records
    )


def _exp5_v3_execution_rows(
    bundles: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for condition_id in sorted(bundles):
        bundle = bundles[condition_id]
        rows.extend(dict(row) for row in _strict_exp5_model_rows(bundle))
        del bundle
    rows.sort(
        key=lambda row: (
            int(row.get("repeat_id", -1)),
            str(row.get("cohort_member_id")),
            str(row.get("condition_id")),
            str(row.get("task_id")),
            str(row.get("unit_id")),
            str(row.get("attempt_id")),
            int(row.get("provider_attempt_index", -1)),
        )
    )
    return tuple(rows)


def _exp5_overall_rows(
    case_records: Sequence[Mapping[str, Any]],
    execution_rows: Sequence[Mapping[str, Any]],
    *,
    members: Sequence[str],
    identity_complete: bool,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for member_id in members:
        cases = [
            row for row in case_records
            if row.get("cohort_member_id") == member_id
        ]
        executions = [
            row for row in execution_rows
            if row.get("cohort_member_id") == member_id
        ]
        completion = _exp5_metric_summary(
            cases, field_name="root_completed", statistic="mean"
        )
        validity = _exp5_metric_summary(
            cases, field_name="accepted_validity", statistic="mean"
        )
        tokens = _exp5_metric_summary(
            cases, field_name="total_tokens", statistic="median"
        )
        latency = _exp5_metric_summary(
            cases, field_name="provider_latency_ms", statistic="median"
        )
        prompt = _exp5_available_numbers(executions, "prompt_tokens")
        reasoning = _exp5_available_numbers(executions, "reasoning_tokens")
        visible = _exp5_available_numbers(executions, "visible_output_tokens")
        costs = _exp5_available_numbers(executions, "cost_estimate")
        currencies = {
            str(row["cost_estimate_currency"])
            for row in executions
            if isinstance(row.get("cost_estimate_currency"), str)
            and row.get("cost_estimate_currency")
        }
        pricing_digests = {
            str(row["pricing_snapshot_digest"])
            for row in executions
            if isinstance(row.get("pricing_snapshot_digest"), str)
            and row.get("pricing_snapshot_digest")
        }
        provider_attempt_count = sum(
            int(row.get("provider_attempt_count", 0))
            for row in executions
            if type(row.get("provider_attempt_count")) is int
        )
        reasoning_missing = len(executions) - len(reasoning)
        prompt_missing = len(executions) - len(prompt)
        visible_missing = len(executions) - len(visible)
        cost_missing = len(executions) - len(costs)
        row_eligible = (
            identity_complete
            and bool(cases)
            and bool(executions)
            and all(row.get("paper_eligible") is True for row in cases)
            and all(row.get("paper_eligible") is True for row in executions)
            and completion["value"] is not None
            and validity["value"] is not None
        )
        token_values = _exp5_available_numbers(cases, "total_tokens")
        latency_values = _exp5_available_numbers(
            cases, "provider_latency_ms"
        )
        rows.append(
            {
                "cohort_member_id": member_id,
                "model_label": _exp5_model_label(executions, member_id),
                "root_count": len(cases),
                "completion_count": sum(
                    row.get("root_completed") is True for row in cases
                ),
                "completion_rate": completion["value"],
                "completion_ci_low": completion["ci_low"],
                "completion_ci_high": completion["ci_high"],
                "accepted_validity_count": sum(
                    row.get("accepted_validity") is True for row in cases
                ),
                "accepted_validity_rate": validity["value"],
                "accepted_validity_ci_low": validity["ci_low"],
                "accepted_validity_ci_high": validity["ci_high"],
                "provider_attempt_count": provider_attempt_count,
                "prompt_tokens": sum(prompt) if prompt_missing == 0 else None,
                "prompt_tokens_sample_size": len(prompt),
                "prompt_tokens_missing_count": prompt_missing,
                "reasoning_tokens": (
                    sum(reasoning) if reasoning_missing == 0 else None
                ),
                "visible_output_tokens": (
                    sum(visible) if visible_missing == 0 else None
                ),
                "total_tokens": (
                    sum(token_values)
                    if cases and len(token_values) == len(cases)
                    else None
                ),
                "total_tokens_sample_size": tokens["sample_size"],
                "total_tokens_missing_count": len(cases) - len(token_values),
                "total_tokens_median": tokens["value"],
                "total_tokens_ci_low": tokens["ci_low"],
                "total_tokens_ci_high": tokens["ci_high"],
                "cost_estimate": (
                    sum(costs) if cost_missing == 0 and executions else None
                ),
                "cost_currency": (
                    next(iter(currencies)) if len(currencies) == 1 else None
                ),
                "cost_estimate_status": (
                    "available"
                    if cost_missing == 0 and executions and len(currencies) == 1
                    else "unavailable"
                ),
                "pricing_snapshot_digest": (
                    next(iter(pricing_digests))
                    if len(pricing_digests) == 1 else None
                ),
                "wall_clock_ms": _exp5_member_wall_clock_ms(cases),
                "provider_latency_ms": (
                    sum(latency_values)
                    if cases and len(latency_values) == len(cases)
                    else None
                ),
                "provider_latency_ms_sample_size": latency["sample_size"],
                "provider_latency_ms_missing_count": (
                    len(cases) - len(latency_values)
                ),
                "provider_latency_ms_median": latency["value"],
                "provider_latency_ms_ci_low": latency["ci_low"],
                "provider_latency_ms_ci_high": latency["ci_high"],
                "rate_limit_429_count": sum(
                    row.get("error_kind") in {"rate_limited", "429"}
                    for row in executions
                ),
                "timeout_count": sum(
                    row.get("error_kind") == "timeout"
                    for row in executions
                ),
                "retry_count": sum(
                    max(0, int(row.get("provider_attempt_count", 0)) - 1)
                    for row in executions
                    if type(row.get("provider_attempt_count")) is int
                ),
                "identity_coverage": _rate(
                    sum(
                        int(row.get("provider_attempt_count", 0))
                        for row in executions
                        if _exp5_identity_is_auditable(row)
                        and type(row.get("provider_attempt_count")) is int
                    ),
                    provider_attempt_count,
                ),
                "reasoning_tokens_missing_count": reasoning_missing,
                "visible_output_tokens_missing_count": visible_missing,
                "cost_estimate_missing_count": cost_missing,
                "reasoning_tokens_unavailable_reason": (
                    "provider_usage_missing" if reasoning_missing else None
                ),
                "prompt_tokens_unavailable_reason": (
                    "provider_usage_missing" if prompt_missing else None
                ),
                "visible_output_tokens_unavailable_reason": (
                    "provider_usage_missing" if visible_missing else None
                ),
                "cost_estimate_unavailable_reason": (
                    "provider_cost_evidence_missing" if cost_missing else None
                ),
                "paper_eligible": row_eligible,
                "identity_complete": identity_complete,
            }
        )
    return tuple(rows)


def _exp5_domain_topic_rows(
    case_records: Sequence[Mapping[str, Any]],
    *,
    members: Sequence[str],
    identity_complete: bool,
) -> tuple[dict[str, Any], ...]:
    strata = tuple(
        dict.fromkeys(
            (row.get("domain"), row.get("topic_family"))
            for row in case_records
        )
    )
    rows: list[dict[str, Any]] = []
    for member_id in members:
        for domain, topic_family in strata:
            cases = [
                row for row in case_records
                if row.get("cohort_member_id") == member_id
                and row.get("domain") == domain
                and row.get("topic_family") == topic_family
            ]
            if not cases:
                continue
            completion = _exp5_metric_summary(
                cases, field_name="root_completed", statistic="mean"
            )
            validity = _exp5_metric_summary(
                cases, field_name="accepted_validity", statistic="mean"
            )
            rows.append(
                {
                    "cohort_member_id": member_id,
                    "model_label": member_id,
                    "domain": domain,
                    "topic_family": topic_family,
                    "root_count": len(cases),
                    "completion_count": sum(
                        row.get("root_completed") is True for row in cases
                    ),
                    "completion_rate": completion["value"],
                    "accepted_validity_count": sum(
                        row.get("accepted_validity") is True for row in cases
                    ),
                    "accepted_validity_rate": validity["value"],
                    "paper_eligible": (
                        identity_complete
                        and all(
                            row.get("paper_eligible") is True
                            for row in cases
                        )
                        and completion["sample_size"] == len(cases)
                        and validity["sample_size"] == len(cases)
                    ),
                    "identity_complete": identity_complete,
                }
            )
    return tuple(rows)


def _exp5_metric_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    field_name: str,
    statistic: str,
) -> dict[str, float | int | None]:
    observed = []
    for row in rows:
        case_id = row.get("case_id")
        value = row.get(field_name)
        if (
            isinstance(case_id, str)
            and case_id
            and (
                isinstance(value, bool)
                or (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                )
            )
        ):
            observed.append((case_id, float(value), 0.0))
    values = [value for _, value, _ in observed]
    point = (
        sum(values) / len(values)
        if values and statistic == "mean"
        else float(_quantile(values, 0.5))
        if values else None
    )
    estimator = (
        (lambda sample: sum(sample) / len(sample))
        if statistic == "mean"
        else (lambda sample: float(_quantile(sample, 0.5)))
    )
    ci_low, ci_high = _cluster_bootstrap_ci(
        observed,
        statistic=estimator,
        seed=EXP5_BOOTSTRAP_SEED,
        resamples=EXP5_BOOTSTRAP_RESAMPLES,
    )
    return {
        "value": point,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "sample_size": len(values),
    }


def _exp5_available_numbers(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
) -> list[float]:
    return [
        float(row[field_name])
        for row in rows
        if not isinstance(row.get(field_name), bool)
        and isinstance(row.get(field_name), (int, float))
    ]


def _exp5_member_wall_clock_ms(
    case_records: Sequence[Mapping[str, Any]],
) -> float | None:
    windows = {
        str(row["condition_id"]): (
            float(_observed_time_ms(row["condition_started_at"])),
            float(_observed_time_ms(row["condition_ended_at"])),
        )
        for row in case_records
        if isinstance(row.get("condition_id"), str)
        and _observed_time_ms(row.get("condition_started_at")) is not None
        and _observed_time_ms(row.get("condition_ended_at")) is not None
    }
    return (
        sum(ended - started for started, ended in windows.values())
        if windows else None
    )


def _exp5_model_label(
    execution_rows: Sequence[Mapping[str, Any]],
    member_id: str,
) -> str:
    labels = {
        str(row["configured_model"])
        for row in execution_rows
        if isinstance(row.get("configured_model"), str)
        and row.get("configured_model")
    }
    return next(iter(labels)) if len(labels) == 1 else member_id


def _exp5_failure_taxonomy_rows(
    bundles: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str, Any, str, str], dict[str, Any]] = {}
    for condition_id in bundles:
        bundle = bundles[condition_id]
        condition = bundle["condition"]
        for task in bundle.get("tasks", ()):
            if (
                not isinstance(task, Mapping)
                or _status(task.get("root_status")) == "completed"
            ):
                continue
            failure_kind = str(
                task.get("failure_kind")
                or task.get("error_kind")
                or _status(task.get("root_status"))
                or "unknown_failure"
            )
            failure_stage = str(
                task.get("failure_stage") or "experiment_runtime"
            )
            key = (
                str(condition.get("cohort_member_id") or ""),
                str(condition.get("domain") or ""),
                condition.get("topic_family"),
                failure_stage,
                failure_kind,
            )
            references = task.get("evidence_artifact_refs", ())
            evidence_ref = (
                _exp5_audit_ref(references[0])
                if isinstance(references, Sequence)
                and not isinstance(references, (str, bytes))
                and references else None
            )
            row = grouped.setdefault(
                key,
                {
                    "cohort_member_id": key[0],
                    "domain": key[1],
                    "topic_family": key[2],
                    "failure_stage": failure_stage,
                    "failure_kind": failure_kind,
                    "count": 0,
                    "evidence_ref": evidence_ref,
                    "short_summary": (
                        f"Observed {failure_kind} at {failure_stage}; "
                        "see persisted evidence reference."
                    ),
                },
            )
            row["count"] += 1
        del bundle
    return tuple(grouped[key] for key in sorted(grouped, key=str))


def _exp5_audit_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    fields = (
        "artifact_id",
        "path",
        "uri",
        "content_hash",
        "schema_version",
        "media_type",
        "record_digest",
        "digest",
    )
    result = {
        field_name: value[field_name]
        for field_name in fields
        if value.get(field_name) is not None
    }
    if type(value.get("size_bytes")) is int:
        result["byte_size"] = value["size_bytes"]
    return result if isinstance(result.get("artifact_id"), str) else None

def _exp5_v3_analysis_outputs(
    bundles: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """仅在正式 v3 evidence 存在时生成冻结统计输出。"""

    has_v3 = False
    for condition_id in bundles:
        bundle = bundles[condition_id]
        condition = bundle.get("condition", {})
        has_v3 = (
            condition.get("experiment_id") == EXP5
            and condition.get("schema_version")
            == "tokenshare.paper_condition.v3"
        )
        del bundle
        if has_v3:
            break
    if not has_v3:
        return {}
    case_records = _exp5_v3_case_records(bundles)
    paired_rows = build_exp5_paired_comparison_rows(case_records)
    order_rows = build_exp5_order_and_concurrency_rows(case_records)
    return {
        "metrics/exp5_paired_comparisons.csv": _csv_text(paired_rows),
        "metrics/exp5_model_execution_records.jsonl": (
            _exp5_v3_execution_record_text(bundles)
        ),
        "metrics/exp5_order_and_concurrency.csv": _csv_text(order_rows),
    }


def _exp5_v3_execution_record_text(
    bundles: Mapping[str, Mapping[str, Any]],
) -> str:
    records: list[dict[str, Any]] = []
    for condition_id in sorted(bundles):
        bundle = bundles[condition_id]
        condition = bundle["condition"]
        if (
            condition.get("experiment_id") == EXP5
            and condition.get("schema_version") == "tokenshare.paper_condition.v3"
        ):
            records.extend(_strict_exp5_model_rows(bundle))
        del bundle
    records.sort(
        key=lambda row: (
            int(row.get("repeat_id", -1)),
            str(row.get("cohort_member_id")),
            str(row.get("condition_id")),
            str(row.get("task_id")),
            str(row.get("unit_id")),
            str(row.get("attempt_id")),
            int(row.get("provider_attempt_index", -1)),
        )
    )
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def _exp5_v3_stratum_id(condition: Mapping[str, Any]) -> str:
    domain = condition.get("domain")
    paper_difficulty = condition.get("paper_difficulty")
    if domain == "factorization":
        return f"factorization:{paper_difficulty}"
    if domain == "lean_proof" and condition.get("topic_family"):
        return (
            f"lean_proof:{paper_difficulty}:{condition['topic_family']}"
        )
    raise ValueError("Exp5 v3 condition has an invalid analysis stratum")


def _complete_numeric_values(
    records: Sequence[Mapping[str, Any]],
    field_name: str,
) -> list[float] | None:
    values: list[float] = []
    for record in records:
        value = record.get(field_name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            return None
        values.append(float(value))
    return values


def _attempt_interval_peak(
    attempts: Sequence[Mapping[str, Any]],
) -> int:
    points: list[tuple[float, int]] = []
    for attempt in attempts:
        started = _observed_time_ms(attempt.get("started_at"))
        ended = _observed_time_ms(attempt.get("ended_at"))
        if started is None or ended is None or ended < started:
            raise ValueError("Exp5 v3 condition timing evidence is invalid")
        points.extend(((started, 1), (ended, -1)))
    active = 0
    peak = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _write_metrics_outputs(
    *,
    root: Path,
    metrics_body: Mapping[str, Any],
    condition_rows: Sequence[Mapping[str, Any]],
    experiment_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    run_bundles: Mapping[str, Mapping[str, Any]],
    exp5_artifact_rows: Mapping[str, Any] | None = None,
    require_complete_model_inventory: bool = True,
) -> list[dict[str, Any]]:
    exp2_root_rows = [
        row for row in experiment_rows[EXP2] if row.get("row_scope") == "root_run"
    ]
    exp2_views = _exp2_rate_limit_views(exp2_root_rows)
    refs: list[dict[str, Any]] = []

    def publish(relative_path: str, chunks: Iterator[str | bytes]) -> None:
        path = root / relative_path
        content_hash = _write_chunks_atomic(path, chunks)
        refs.append(
            {
                "path": relative_path,
                "content_hash": content_hash,
            }
        )

    publish(
        "metrics/per_condition_summary.csv",
        _iter_csv_chunks(condition_rows),
    )
    publish(
        "metrics/paper_table_feasibility.csv",
        _iter_csv_chunks(experiment_rows[EXP1]),
    )
    publish(
        "metrics/paper_plot_scalability.csv",
        _iter_csv_chunks(experiment_rows[EXP2]),
    )
    publish(
        "metrics/paper_plot_scalability_all_runs.csv",
        _iter_csv_chunks(
            _annotated_rate_limit_view_rows(exp2_views["all_runs"])
        ),
    )
    publish(
        "metrics/paper_plot_scalability_rate_limit_excluded_sensitivity.csv",
        _iter_csv_chunks(
            _annotated_rate_limit_view_rows(
                exp2_views["rate_limit_excluded"]
            )
        ),
    )
    publish(
        "metrics/paper_plot_scalability_views.json",
        _iter_json_chunks(exp2_views, indent=2, trailing_newline=True),
    )
    publish(
        "metrics/paper_plot_robustness.csv",
        _iter_csv_chunks(experiment_rows[EXP3]),
    )
    publish(
        "metrics/paper_table_ablation.csv",
        _iter_csv_chunks(experiment_rows[EXP4]),
    )
    publish(
        "metrics/paper_table_model_comparison.csv",
        _iter_csv_chunks(experiment_rows[EXP5]),
    )
    publish(
        "metrics/paper_table_model_endpoint_comparison.csv",
        _iter_csv_chunks(experiment_rows[EXP5]),
    )
    canonical_model_path = "model_execution_records.jsonl"
    publish(
        canonical_model_path,
        _iter_model_record_chunks(
            run_bundles,
            require_complete_inventory=require_complete_model_inventory,
            work_parent=root.parent,
        ),
    )
    publish(
        "metrics/model_execution_records.jsonl",
        _iter_file_chunks(root / canonical_model_path),
    )
    publish(
        "metrics/failure_examples.json",
        _iter_json_chunks(
            _failure_examples(run_bundles),
            indent=2,
            trailing_newline=True,
        ),
    )
    publish(
        "metrics/formal_metrics.json",
        _iter_json_chunks(metrics_body, indent=2, trailing_newline=True),
    )
    if exp5_artifact_rows is not None:
        publish(
            "metrics/exp5_renderer_rows.json",
            _iter_json_chunks(
                exp5_artifact_rows,
                indent=2,
                trailing_newline=True,
            ),
        )
    return refs


def _annotated_rate_limit_view_rows(
    view: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metadata = {
        "analysis_view": view["analysis_view"],
        "view_sample_count": view["sample_count"],
        "excluded_run_ids": list(view["excluded_run_ids"]),
        "excluded_condition_ids": list(view["excluded_condition_ids"]),
        "exclusion_reason": view["exclusion_reason"],
    }
    return [{**dict(row), **metadata} for row in view["rows"]]


def _exp2_rate_limit_views(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """保留主 end-to-end 样本，并另建只排除 429 run 的敏感性视图。"""

    all_rows = [dict(row) for row in rows]
    excluded = [
        row
        for row in all_rows
        if int(_number(row.get("http_429_count"))) > 0
        or int(_number(row.get("rate_limited_attempt_count"))) > 0
    ]
    sensitivity_rows = [
        row
        for row in all_rows
        if int(_number(row.get("http_429_count"))) == 0
        and int(_number(row.get("rate_limited_attempt_count"))) == 0
    ]
    excluded_run_ids = sorted(
        {
            str(
                row.get("run_id")
                or row.get("condition_id")
                or row.get("task_id")
                or row.get("case_id")
            )
            for row in excluded
            if row.get("run_id")
            or row.get("condition_id")
            or row.get("task_id")
            or row.get("case_id")
        }
    )
    excluded_condition_ids = sorted(
        {
            str(row.get("condition_id"))
            for row in excluded
            if row.get("condition_id")
        }
    )
    return {
        "all_runs": {
            "analysis_view": "all_runs_end_to_end",
            "sample_count": len(all_rows),
            "excluded_run_ids": [],
            "excluded_condition_ids": [],
            "exclusion_reason": "none",
            "rows": all_rows,
        },
        "rate_limit_excluded": {
            "analysis_view": "rate_limit_excluded_sensitivity",
            "sample_count": len(sensitivity_rows),
            "excluded_run_ids": excluded_run_ids,
            "excluded_condition_ids": excluded_condition_ids,
            "exclusion_reason": (
                "provider_http_429" if excluded else "no_exclusions"
            ),
            "rows": sensitivity_rows,
        },
    }


def _model_record_text(
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    require_complete_inventory: bool = True,
) -> str:
    """小 fixture 兼容入口；正式输出使用 chunk iterator，避免全量文本。"""

    return "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for chunk in _iter_model_record_chunks(
            bundles,
            require_complete_inventory=require_complete_inventory,
        )
    )


def _iter_model_record_chunks(
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    require_complete_inventory: bool = True,
    work_parent: Path | None = None,
) -> Iterator[str]:
    """从 canonical artifact 逐条校验并输出 v2 record JSONL。"""

    directory_argument = str(work_parent) if work_parent is not None else None
    with TemporaryDirectory(
        prefix=".tokenshare-model-inventory-",
        dir=directory_argument,
    ) as temporary_directory:
        with closing(
            sqlite3.connect(Path(temporary_directory) / "seen.sqlite3")
        ) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = OFF;
                PRAGMA synchronous = OFF;
                PRAGMA temp_store = FILE;
                CREATE TABLE seen_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    CHECK (content_hash != '')
                );
                CREATE TABLE seen_attempts (
                    identity_json TEXT PRIMARY KEY
                );
                """
            )
            for condition_id in bundles:
                bundle = bundles[condition_id]
                condition = bundle.get("condition")
                if not isinstance(condition, Mapping):
                    raise ValueError(
                        "model execution condition identity is invalid"
                    )
                artifacts = bundle.get("artifacts", ())
                if not isinstance(artifacts, Sequence) or isinstance(
                    artifacts,
                    (str, bytes),
                ):
                    raise ValueError(
                        "model execution artifact inventory must be a sequence"
                    )
                for attempt in bundle.get("attempts", ()):
                    if not isinstance(attempt, Mapping):
                        raise ValueError(
                            "model execution attempt inventory is invalid"
                        )
                    model_ref = attempt.get("model_execution_record_ref")
                    if model_ref is None:
                        if (
                            require_complete_inventory
                            and _formal_attempt_requires_model_record(attempt)
                        ):
                            raise ValueError(
                                "missing canonical model execution record ref"
                            )
                        continue
                    if not isinstance(model_ref, Mapping):
                        raise ValueError(
                            "model execution record ref must be an object"
                        )
                    artifact_id = model_ref.get("artifact_id")
                    content_hash = model_ref.get("content_hash")
                    suite_root_value = bundle.get("suite_root")
                    if not isinstance(suite_root_value, (str, Path)):
                        raise ValueError(
                            "model execution suite root is invalid"
                        )
                    suite_root = Path(suite_root_value)
                    if not isinstance(artifact_id, str) or not isinstance(
                        content_hash,
                        str,
                    ):
                        raise ValueError(
                            "model execution record ref is incomplete"
                        )
                    try:
                        connection.execute(
                            "INSERT INTO seen_artifacts VALUES (?, ?)",
                            (artifact_id, content_hash),
                        )
                    except sqlite3.IntegrityError as exc:
                        existing = connection.execute(
                            """
                            SELECT content_hash
                            FROM seen_artifacts
                            WHERE artifact_id = ?
                            """,
                            (artifact_id,),
                        ).fetchone()
                        if existing is not None and existing[0] != content_hash:
                            raise ValueError(
                                "model execution artifact id has conflicting "
                                "content hash"
                            ) from exc
                        raise ValueError(
                            "duplicate model execution record ref"
                        ) from exc
                    match: Mapping[str, Any] | None = None
                    match_count = 0
                    for artifact in artifacts:
                        if (
                            isinstance(artifact, Mapping)
                            and artifact.get("artifact_id") == artifact_id
                            and artifact.get("content_hash") == content_hash
                        ):
                            match = artifact
                            match_count += 1
                    if (
                        match_count != 1
                        or match is None
                        or not isinstance(match.get("path"), str)
                    ):
                        raise ValueError(
                            "model execution record ref is unresolved"
                        )
                    path = suite_root / str(match["path"])
                    if (
                        not path.is_file()
                        or _hash_file(path) != content_hash
                    ):
                        raise ValueError(
                            "model execution record artifact verification failed"
                        )
                    record = _read_json(path)
                    expected_identity = {
                        "condition_id": condition.get("condition_id"),
                        "repeat_id": attempt.get("repeat_id"),
                        "run_id": attempt.get("run_id"),
                        "task_id": attempt.get(
                            "protocol_task_id",
                            attempt.get("task_id"),
                        ),
                        "unit_id": attempt.get("unit_id"),
                        "attempt_id": attempt.get("attempt_id"),
                    }
                    if record.get("schema_version") != (
                        "tokenshare.paper_model_execution_record.v2"
                    ) or any(
                        expected is not None
                        and record.get(field_name) != expected
                        for field_name, expected in expected_identity.items()
                    ):
                        raise ValueError(
                            "model execution record identity is invalid"
                        )
                    identity_json = json.dumps(
                        list(expected_identity.values()),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    try:
                        connection.execute(
                            "INSERT INTO seen_attempts VALUES (?)",
                            (identity_json,),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(
                            "duplicate model execution attempt identity"
                        ) from exc
                    yield from _iter_json_chunks(record, compact=False)
                    yield "\n"
                del bundle


def _formal_attempt_requires_model_record(attempt: Mapping[str, Any]) -> bool:
    """判定正式 attempt 是否必须绑定 canonical v2 model record。"""

    provider_attempt_count = attempt.get("provider_attempt_count")
    if (
        isinstance(provider_attempt_count, int)
        and not isinstance(provider_attempt_count, bool)
        and provider_attempt_count > 0
    ):
        return True
    ai_identity_present = (
        attempt.get("executor_type") == "ai_api"
        or any(
            isinstance(attempt.get(field_name), str) and attempt.get(field_name)
            for field_name in ("provider", "model", "entry_id")
        )
    )
    if not ai_identity_present:
        return False
    explicit_pre_provider_zero_call = (
        provider_attempt_count == 0
        and attempt.get("schema_version") == "tokenshare.paper_attempt_result.v2"
        and _status(attempt.get("attempt_status")) == "executor_error"
    )
    return not explicit_pre_provider_zero_call


def _failure_examples(bundles: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for condition_id in bundles:
        bundle = bundles[condition_id]
        for task in bundle["tasks"]:
            if _status(task.get("root_status")) != "completed":
                examples.append(dict(task))
                if len(examples) == 20:
                    del bundle
                    return examples
        del bundle
    return examples


def _worker_interval_metrics(
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, float | int] | None:
    """从真实 worker attempt interval 计算 wall clock 与峰值并发。"""

    intervals: list[tuple[float, float]] = []
    for attempt in attempts:
        started = _observed_time_ms(attempt.get("started_at"))
        ended = _observed_time_ms(attempt.get("ended_at"))
        worker_id = attempt.get("worker_id")
        if (
            started is None
            or ended is None
            or ended < started
            or not isinstance(worker_id, str)
            or not worker_id
        ):
            return None
        intervals.append((started, ended))
    if not intervals:
        return None
    points = sorted(
        (
            (timestamp, 1 if boundary == "start" else -1)
            for started, ended in intervals
            for timestamp, boundary in ((started, "start"), (ended, "end"))
        ),
        key=lambda item: (item[0], -item[1]),
    )
    active = 0
    peak = 0
    for _, delta in points:
        active += delta
        peak = max(peak, active)
    return {
        "wall_clock_ms": max(end for _, end in intervals)
        - min(start for start, _ in intervals),
        "observed_peak_concurrency": peak,
    }


def _protocol_critical_path(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """从 attempt、任务关系、canonical、merge 与 root 事件计算关键路径。"""

    reasons: list[str] = []
    interval_by_attempt: dict[str, tuple[str, float, float]] = {}
    attempts_by_unit: dict[str, list[str]] = defaultdict(list)
    evidence_refs: list[dict[str, Any]] = []
    root_registration = next(
        (
            event
            for event in events
            if event.get("event_type") == "TASK_REGISTERED"
        ),
        None,
    )
    root_started_at = _event_time_ms(root_registration)
    root_start_ref = _event_evidence_ref(root_registration)
    if root_started_at is None or root_start_ref is None:
        reasons.append("missing_root_start_evidence")
    else:
        evidence_refs.append(root_start_ref)

    created_unit_ids: set[str] = set()
    for event in events:
        if event.get("event_type") != "TASK_UNIT_CREATED":
            continue
        task_unit = _nested_mapping(event, "task_unit")
        unit_id = task_unit.get("unit_id") or event.get("object_id")
        ref = _event_evidence_ref(event)
        if not isinstance(unit_id, str) or not unit_id or ref is None:
            reasons.append("invalid_task_graph_unit_evidence")
            continue
        created_unit_ids.add(unit_id)
        evidence_refs.append(ref)
    for attempt in attempts:
        attempt_id = attempt.get("attempt_id")
        unit_id = attempt.get("unit_id")
        started = _observed_time_ms(attempt.get("started_at"))
        ended = _observed_time_ms(attempt.get("ended_at"))
        if (
            not isinstance(attempt_id, str)
            or not attempt_id
            or not isinstance(unit_id, str)
            or not unit_id
            or started is None
            or ended is None
            or ended < started
            or attempt_id in interval_by_attempt
        ):
            reasons.append("missing_attempt_timing_evidence")
            continue
        interval_by_attempt[attempt_id] = (unit_id, started, ended)
        attempts_by_unit[unit_id].append(attempt_id)
        evidence_refs.append(
            {"evidence_kind": "attempt_interval", "attempt_id": attempt_id}
        )
    if not attempts or len(interval_by_attempt) != len(attempts):
        reasons.append("missing_attempt_timing_evidence")
    if set(attempts_by_unit) - created_unit_ids:
        reasons.append("missing_task_graph_unit_evidence")

    predecessors: dict[str, set[str]] = {
        attempt_id: set() for attempt_id in interval_by_attempt
    }
    for unit_attempt_ids in attempts_by_unit.values():
        unit_attempt_ids.sort(key=lambda item: interval_by_attempt[item][1])
        for previous, current in zip(unit_attempt_ids, unit_attempt_ids[1:]):
            predecessors[current].add(previous)

    relation_count = 0
    for event in events:
        if event.get("event_type") != "TASK_RELATION_CREATED":
            continue
        relation = _nested_mapping(event, "task_relation")
        source_unit = relation.get("source_unit_id")
        target_unit = relation.get("target_unit_id")
        if (
            not isinstance(source_unit, str)
            or not isinstance(target_unit, str)
            or source_unit not in attempts_by_unit
            or target_unit not in attempts_by_unit
        ):
            reasons.append("invalid_task_dependency_evidence")
            continue
        source_attempt = attempts_by_unit[source_unit][-1]
        target_attempt = attempts_by_unit[target_unit][0]
        predecessors[target_attempt].add(source_attempt)
        relation_count += 1
        ref = _event_evidence_ref(event)
        if ref is None:
            reasons.append("missing_dependency_event_ref")
        else:
            evidence_refs.append(ref)

    declared_dependency = any(
        isinstance(attempt.get("dependency_path"), Sequence)
        and not isinstance(attempt.get("dependency_path"), (str, bytes))
        and bool(attempt.get("dependency_path"))
        for attempt in attempts
    )
    if declared_dependency and relation_count == 0:
        reasons.append("missing_task_dependency_evidence")

    memo: dict[str, float] = {}
    visiting: set[str] = set()

    def visit(attempt_id: str) -> float | None:
        if attempt_id in memo:
            return memo[attempt_id]
        if attempt_id in visiting:
            reasons.append("cyclic_task_dependency_evidence")
            return None
        visiting.add(attempt_id)
        _unit_id, started, ended = interval_by_attempt[attempt_id]
        duration = ended - started
        predecessor_costs: list[float] = []
        for predecessor_id in predecessors[attempt_id]:
            predecessor_cost = visit(predecessor_id)
            if predecessor_cost is None:
                continue
            predecessor_end = interval_by_attempt[predecessor_id][2]
            if started < predecessor_end:
                reasons.append("dependency_timing_contradiction")
                continue
            predecessor_costs.append(
                predecessor_cost + (started - predecessor_end)
            )
        visiting.remove(attempt_id)
        if predecessors[attempt_id] and not predecessor_costs:
            return None
        if predecessors[attempt_id]:
            prefix = max(predecessor_costs)
        elif root_started_at is None or started < root_started_at:
            reasons.append("dependency_timing_contradiction")
            return None
        else:
            prefix = started - root_started_at
        memo[attempt_id] = duration + prefix
        return memo[attempt_id]

    canonical_by_unit: dict[str, tuple[str, float, dict[str, Any]]] = {}
    for event in events:
        if event.get("event_type") != "CANONICAL_OUTPUTS_BOUND":
            continue
        canonical = _nested_mapping(event, "canonical_selection")
        unit_id = canonical.get("unit_id")
        attempt_id = canonical.get("selected_attempt_id")
        occurred = _event_time_ms(event)
        ref = _event_evidence_ref(event)
        if (
            isinstance(unit_id, str)
            and isinstance(attempt_id, str)
            and attempt_id in interval_by_attempt
            and occurred is not None
            and ref is not None
        ):
            canonical_by_unit[unit_id] = (attempt_id, occurred, ref)

    merge_link = next(
        (
            event
            for event in events
            if event.get("event_type") == "MERGE_TASK_LINK_RECORDED"
        ),
        None,
    )
    if merge_link is None:
        reasons.append("missing_merge_dependency_evidence")
        return _incomplete_critical_path(reasons, evidence_refs)
    merge_link_body = _nested_mapping(merge_link, "merge_task_link")
    bindings = merge_link_body.get("required_slot_bindings")
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
        reasons.append("missing_merge_dependency_evidence")
        return _incomplete_critical_path(reasons, evidence_refs)
    required_units = tuple(
        str(binding.get("source_child_unit_id"))
        for binding in bindings
        if isinstance(binding, Mapping)
        and isinstance(binding.get("source_child_unit_id"), str)
        and binding.get("source_child_unit_id")
    )
    if not required_units or len(required_units) != len(bindings):
        reasons.append("missing_merge_dependency_evidence")
    merge_link_time = _event_time_ms(merge_link)
    merge_link_ref = _event_evidence_ref(merge_link)
    if merge_link_time is None or merge_link_ref is None:
        reasons.append("missing_merge_gate_timing_evidence")
    else:
        evidence_refs.append(merge_link_ref)

    canonical_paths: list[float] = []
    if merge_link_time is not None:
        for unit_id in required_units:
            canonical = canonical_by_unit.get(unit_id)
            if canonical is None:
                reasons.append("missing_canonical_dependency_evidence")
                continue
            attempt_id, canonical_time, canonical_ref = canonical
            attempt_cost = visit(attempt_id)
            attempt_end = interval_by_attempt[attempt_id][2]
            if (
                attempt_cost is None
                or canonical_time < attempt_end
                or merge_link_time < canonical_time
            ):
                reasons.append("dependency_timing_contradiction")
                continue
            canonical_paths.append(
                attempt_cost
                + (canonical_time - attempt_end)
                + (merge_link_time - canonical_time)
            )
            evidence_refs.append(canonical_ref)
    if len(canonical_paths) != len(required_units):
        reasons.append("incomplete_canonical_dependency_inventory")

    merge_record = next(
        (
            event
            for event in events
            if event.get("event_type") == "MERGE_RECORDED"
            and (
                merge_link_time is None
                or (_event_time_ms(event) or float("-inf")) >= merge_link_time
            )
        ),
        None,
    )
    merge_record_time = _event_time_ms(merge_record) if merge_record else None
    merge_record_ref = _event_evidence_ref(merge_record) if merge_record else None
    if (
        merge_record_time is None
        or merge_link_time is None
        or merge_record_time < merge_link_time
        or merge_record_ref is None
    ):
        reasons.append("missing_merge_completion_evidence")
    else:
        evidence_refs.append(merge_record_ref)

    generation_identity = task.get("runtime_generation_identity")
    generation_identity = (
        generation_identity if isinstance(generation_identity, Mapping) else {}
    )
    root_unit_id = generation_identity.get("root_unit_id")
    root_completion = next(
        (
            event
            for event in events
            if event.get("event_type") in {
                "ROOT_COMPLETED",
                "TASK_COMPLETED",
                "TASK_UNIT_STATE_CHANGED",
            }
            and (
                event.get("event_type") != "TASK_UNIT_STATE_CHANGED"
                or (
                    _status(_nested_mapping(event, "task_unit_state_change").get("new_state")
                    or _event_payload(event).get("new_state")).lower()
                    == "completed"
                    and (
                        not isinstance(root_unit_id, str)
                        or event.get("object_id") == root_unit_id
                        or _event_payload(event).get("unit_id") == root_unit_id
                    )
                )
            )
        ),
        None,
    )
    root_time = _event_time_ms(root_completion) if root_completion else None
    root_ref = _event_evidence_ref(root_completion) if root_completion else None
    if (
        root_time is None
        or merge_record_time is None
        or root_time < merge_record_time
        or root_ref is None
    ):
        reasons.append("missing_root_completion_evidence")
    else:
        evidence_refs.append(root_ref)

    stable_reasons = list(dict.fromkeys(reasons))
    if stable_reasons or not canonical_paths:
        return _incomplete_critical_path(stable_reasons, evidence_refs)
    assert merge_link_time is not None
    assert merge_record_time is not None
    assert root_time is not None
    critical_path_ms = (
        max(canonical_paths)
        + (merge_record_time - merge_link_time)
        + (root_time - merge_record_time)
    )
    return {
        "critical_path_ms": critical_path_ms,
        "critical_path_source": "protocol_dependency_graph",
        "critical_path_evidence_refs": _stable_evidence_refs(evidence_refs),
        "paper_ineligibility_reasons": [],
    }


def _incomplete_critical_path(
    reasons: Sequence[str],
    evidence_refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "critical_path_ms": None,
        "critical_path_source": "insufficient_protocol_dependency_evidence",
        "critical_path_evidence_refs": _stable_evidence_refs(evidence_refs),
        "paper_ineligibility_reasons": list(dict.fromkeys(reasons)),
    }


def _nested_mapping(event: Mapping[str, Any] | None, field_name: str) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    payload = _event_payload(event)
    nested = payload.get(field_name)
    return nested if isinstance(nested, Mapping) else payload


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _event_time_ms(event: Mapping[str, Any] | None) -> float | None:
    if not isinstance(event, Mapping):
        return None
    for field_name in ("occurred_at", "ended_at", "started_at"):
        value = _observed_time_ms(event.get(field_name))
        if value is not None:
            return value
    offset = event.get("offset_ms")
    return (
        float(offset)
        if isinstance(offset, (int, float)) and not isinstance(offset, bool)
        else None
    )


def _event_evidence_ref(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(event, Mapping):
        return None
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    ref: dict[str, Any] = {
        "evidence_kind": "ledger_event",
        "event_id": event_id,
        "event_type": event.get("event_type"),
    }
    if isinstance(event.get("event_seq"), int):
        ref["event_seq"] = event["event_seq"]
    return ref


def _stable_evidence_refs(
    refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        body = dict(ref)
        key = json.dumps(body, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(body)
    return result


def _observed_time_ms(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            * 1000.0
        )
    except ValueError:
        return None


def _string_inventory(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    inventory = tuple(
        item for item in value if isinstance(item, str) and item
    )
    if len(inventory) != len(value) or len(set(inventory)) != len(inventory):
        return ()
    return inventory


def _records_for_task(
    records: Sequence[Mapping[str, Any]],
    task: Mapping[str, Any],
    all_tasks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """按持久化 task_id 隔离单个 root；单 root 兼容无上下文字段的旧夹具。"""

    task_id = task.get("task_id")
    protocol_event_ledger = task.get("protocol_event_ledger")
    protocol_event_hashes = (
        set(protocol_event_ledger.get("event_hashes", ()))
        if isinstance(protocol_event_ledger, Mapping)
        and isinstance(protocol_event_ledger.get("event_hashes"), list)
        else set()
    )
    matched = [
        record
        for record in records
        if (
            isinstance(task_id, str)
            and record.get("task_id") == task_id
        )
        or (
            isinstance(record.get("event_hash"), str)
            and record.get("event_hash") in protocol_event_hashes
        )
    ]
    if matched or len(all_tasks) != 1:
        return matched
    return list(records)


def _with_specialty_eligibility(
    base_row: Mapping[str, Any],
    specialty_reasons: Sequence[str],
    row: Mapping[str, Any],
) -> dict[str, Any]:
    reasons = list(base_row.get("paper_ineligibility_reasons", ()))
    reasons.extend(str(reason) for reason in specialty_reasons)
    stable_reasons = list(dict.fromkeys(reasons))
    return {
        **dict(row),
        "paper_eligible": (
            base_row.get("paper_eligible") is True and not stable_reasons
        ),
        "paper_ineligibility_reasons": stable_reasons,
    }


def _strict_exp5_identity_matches(
    record: Mapping[str, Any],
    condition: Mapping[str, Any],
) -> bool:
    return (
        record.get("identity_status") == "matched"
        and record.get("provider_family") == condition.get("provider_family")
        and record.get("selected_entry_id") == condition.get("model_entry_id")
        and record.get("configured_model") == condition.get("provider_model_id")
        and record.get("requested_model") == condition.get("provider_model_id")
        and record.get("resolved_model") == condition.get("provider_model_id")
    )


def _exp5_identity_is_auditable(record: Mapping[str, Any]) -> bool:
    """区分真实 provider 无响应与实际 model identity mismatch。"""

    if record.get("paper_eligible") is not True:
        return False
    if record.get("identity_status") == "matched":
        return True
    provider_errors = record.get("provider_errors")
    return (
        record.get("identity_status") == "not_observed"
        and record.get("response_model_status") == "unavailable"
        and record.get("resolved_model") is None
        and record.get("raw_output_ref") is None
        and record.get("attempt_status") == "provider_error"
        and isinstance(provider_errors, Sequence)
        and not isinstance(provider_errors, (str, bytes))
        and bool(provider_errors)
    )


def _with_repeat_aggregates(
    experiment_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """保留逐 repeat 行；只有跨 repeat 样本才生成 aggregate。"""

    repeat_rows = [dict(row) for row in rows]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in repeat_rows:
        if (
            experiment_id == EXP4
            and row.get("row_scope") != "repeat_condition"
        ):
            continue
        if (
            experiment_id == EXP3
            and row.get("row_scope") != "repeat_condition"
        ):
            continue
        grouped[_repeat_group_key(experiment_id, row)].append(row)
    aggregates: list[dict[str, Any]] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda row: int(row.get("repeat_id", 0)))
        wall_values = [float(row["wall_clock_ms"]) for row in ordered]
        minimum = min(wall_values)
        maximum = max(wall_values)
        reasons = list(
            dict.fromkeys(
                str(reason)
                for row in ordered
                for reason in row.get("paper_ineligibility_reasons", ())
            )
        )
        aggregate = {
            **ordered[0],
            "row_scope": "repeat_aggregate",
            "condition_id": None,
            "condition_ids": [row["condition_id"] for row in ordered],
            "repeat_id": None,
            "repeat_ids": [int(row.get("repeat_id", 0)) for row in ordered],
            "repeat_count": len(ordered),
            "wall_clock_ms": None,
            "wall_clock_min_ms": minimum,
            "wall_clock_max_ms": maximum,
            "wall_clock_relative_difference": (
                (maximum - minimum) / minimum if minimum > 0 else None
            ),
            "completed_root_count": sum(
                int(row["completed_root_count"]) for row in ordered
            ),
            "task_count": sum(int(row["task_count"]) for row in ordered),
            "total_tokens": _sum_complete_numeric_field(ordered, "total_tokens"),
            "total_cost_estimate": _sum_complete_numeric_field(
                ordered, "total_cost_estimate"
            ),
            "token_usage_sample_size": sum(
                int(row.get("token_usage_sample_size", 0)) for row in ordered
            ),
            "token_usage_missing_count": sum(
                int(row.get("token_usage_missing_count", 0)) for row in ordered
            ),
            "cost_estimate_sample_size": sum(
                int(row.get("cost_estimate_sample_size", 0)) for row in ordered
            ),
            "cost_estimate_missing_count": sum(
                int(row.get("cost_estimate_missing_count", 0)) for row in ordered
            ),
            "paper_eligible": all(
                row.get("paper_eligible") is True for row in ordered
            )
            and not reasons,
            "paper_ineligibility_reasons": reasons,
        }
        if len(ordered) >= 3:
            aggregate["wall_clock_p50"] = _quantile(wall_values, 0.5)
            aggregate["wall_clock_p95"] = _quantile(wall_values, 0.95)
        if experiment_id == EXP4:
            aggregate = _exp4_rollup_row(
                base_row=aggregate,
                source_rows=ordered,
                row_scope=(
                    "three_repeat_aggregate"
                    if len(ordered) == 3
                    else "repeat_aggregate"
                ),
            )
        if experiment_id == EXP3:
            for field_name in (
                "detection_rate",
                "false_accept_rate",
                "recovery_rate",
                "completion_rate",
                "recovery_latency_ms",
                "reassignment_count",
                "wasted_actual_tokens",
                "wall_clock_overhead_ms",
                "wall_clock_overhead_ratio",
                "token_overhead",
                "token_overhead_ratio",
                "cost_overhead_delta",
                "cost_overhead_ratio",
                "provider_latency_overhead_ms",
                "provider_latency_overhead_ratio",
                "evidence_completeness_rate",
                "kill_progress_actual_ratio_min",
                "kill_progress_actual_ratio_max",
                "kill_progress_actual_ratio_mean",
            ):
                values = [row.get(field_name) for row in ordered]
                aggregate[field_name] = None
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    for value in values
                ):
                    aggregate[f"{field_name}_min"] = None
                    aggregate[f"{field_name}_max"] = None
                    aggregate[f"{field_name}_relative_difference"] = None
                    continue
                numeric_values = [float(value) for value in values]
                field_minimum = min(numeric_values)
                field_maximum = max(numeric_values)
                aggregate[f"{field_name}_min"] = field_minimum
                aggregate[f"{field_name}_max"] = field_maximum
                aggregate[f"{field_name}_relative_difference"] = (
                    (field_maximum - field_minimum) / abs(field_minimum)
                    if field_minimum != 0
                    else 0.0
                    if field_maximum == 0
                    else None
                )
            if ordered[0].get("fault_type") == "worker_death":
                for field_name in (
                    "dead_worker_count",
                    "actual_dead_worker_count",
                    "target_dead_worker_count",
                    "required_slot_count",
                    "recovered_slot_count",
                ):
                    values = [row.get(field_name) for row in ordered]
                    aggregate[field_name] = (
                        sum(int(value) for value in values)
                        if all(
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                            for value in values
                        )
                        else None
                    )
                required_slots = aggregate.get("required_slot_count")
                recovered_slots = aggregate.get("recovered_slot_count")
                aggregate["result_completeness_rate"] = _rate(
                    recovered_slots,
                    required_slots,
                )
                aggregate["result_completeness_applicability"] = (
                    "applicable"
                    if isinstance(required_slots, int) and required_slots > 0
                    else "zero_denominator"
                )
                for field_name in (
                    "coordinator_continued",
                    "root_output_complete",
                    "accepted_validity",
                ):
                    values = [row.get(field_name) for row in ordered]
                    aggregate[field_name] = (
                        all(values)
                        if all(isinstance(value, bool) for value in values)
                        else None
                    )
                kill_progress_values = [
                    float(row["kill_progress"])
                    for row in ordered
                    if isinstance(row.get("kill_progress"), (int, float))
                    and not isinstance(row.get("kill_progress"), bool)
                ]
                aggregate["kill_progress"] = None
                aggregate["kill_progress_min"] = (
                    min(kill_progress_values) if kill_progress_values else None
                )
                aggregate["kill_progress_max"] = (
                    max(kill_progress_values) if kill_progress_values else None
                )
                aggregate["kill_progress_mean"] = (
                    sum(kill_progress_values) / len(kill_progress_values)
                    if kill_progress_values
                    else None
                )
                aggregate["worker_death_evidence_refs"] = _stable_evidence_refs(
                    [
                        ref
                        for row in ordered
                        for ref in row.get("worker_death_evidence_refs", ())
                        if isinstance(ref, Mapping)
                    ]
                )
        if experiment_id == EXP5:
            task_count = int(aggregate["task_count"])
            accepted_count = sum(
                float(row.get("accepted_validity_rate") or 0.0)
                * int(row["task_count"])
                for row in ordered
            )
            identity_matches = sum(
                int(row.get("model_identity_match_count") or 0)
                for row in ordered
            )
            identity_denominator = sum(
                int(row.get("model_identity_denominator") or 0)
                for row in ordered
            )
            aggregate.update(
                {
                    "row_scope": (
                        "three_repeat_aggregate"
                        if len(ordered) == 3
                        else "repeat_aggregate"
                    ),
                    "completion_rate": _rate(
                        int(aggregate["completed_root_count"]),
                        task_count,
                    ),
                    "accepted_validity_rate": _rate(
                        accepted_count,
                        task_count,
                    ),
                    "provider_attempt_count": sum(
                        int(row["provider_attempt_count"]) for row in ordered
                    ),
                    "provider_latency_sum_ms": sum(
                        float(row["provider_latency_sum_ms"])
                        for row in ordered
                    ),
                    "provider_error_count": sum(
                        int(row["provider_error_count"]) for row in ordered
                    ),
                    "rate_limited_attempt_count": sum(
                        int(row["rate_limited_attempt_count"])
                        for row in ordered
                    ),
                    "retry_attempt_count": sum(
                        int(row["retry_attempt_count"]) for row in ordered
                    ),
                    "endpoint_error_count": sum(
                        int(row["endpoint_error_count"]) for row in ordered
                    ),
                    "model_execution_v2_record_count": sum(
                        int(row["model_execution_v2_record_count"])
                        for row in ordered
                    ),
                    "model_identity_match_count": identity_matches,
                    "model_identity_denominator": identity_denominator,
                    "model_identity_match_rate": _rate(
                        identity_matches,
                        identity_denominator,
                    ),
                    "identity_status": (
                        "not_observed"
                        if identity_denominator == 0
                        else "matched"
                        if identity_matches == identity_denominator
                        else "model_identity_mismatch"
                    ),
                    "provider_confounding": "model_provider_endpoint_pair",
                }
            )
        aggregates.append(aggregate)
    return [*repeat_rows, *aggregates]


def _with_exp4_full_pairing(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    full_rows = {
        _exp4_pair_key(row): row
        for row in rows
        if row.get("ablation_mode") == "FULL"
    }
    paired_rows: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        full_row = full_rows.get(_exp4_pair_key(row))
        full_condition_id = (
            full_row.get("condition_id") if full_row is not None else None
        )
        full_condition_ids = (
            list(full_row.get("condition_ids", ()))
            if full_row is not None
            else []
        )
        if full_condition_id is not None and not full_condition_ids:
            full_condition_ids = [full_condition_id]
        row.update(
            {
                "paired_full_ablation_mode": (
                    "FULL" if full_row is not None else None
                ),
                "paired_full_case_id": (
                    full_row.get("case_id") if full_row is not None else None
                ),
                "paired_full_condition_id": full_condition_id,
                "paired_full_condition_ids": full_condition_ids,
                "paired_full_row_found": full_row is not None,
                "paired_full_paper_eligible": (
                    full_row.get("paper_eligible") is True
                    if full_row is not None
                    else False
                ),
            }
        )
        if row.get("ablation_mode") != "FULL":
            reasons = list(row.get("paper_ineligibility_reasons", ()))
            if full_row is None:
                reasons.append("missing_paired_full_evidence")
            elif full_row.get("paper_eligible") is not True:
                reasons.append("paired_full_not_paper_eligible")
            stable_reasons = list(dict.fromkeys(str(reason) for reason in reasons))
            row["paper_ineligibility_reasons"] = stable_reasons
            row["paper_eligible"] = (
                row.get("paper_eligible") is True and not stable_reasons
            )
        paired_rows.append(row)
    return paired_rows


def _exp4_pair_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    row_scope = row.get("row_scope")
    return (
        row_scope,
        row.get("domain"),
        row.get("difficulty"),
        row.get("paper_difficulty"),
        row.get("topic_family"),
        row.get("worker_count"),
        row.get("case_id") if row_scope == "task" else None,
        (
            row.get("repeat_id")
            if row_scope in {"task", "repeat_condition"}
            else None
        ),
    )


def _repeat_group_key(
    experiment_id: str,
    row: Mapping[str, Any],
) -> tuple[Any, ...]:
    return (
        experiment_id,
        row.get("domain"),
        row.get("difficulty"),
        row.get("paper_difficulty"),
        row.get("topic_family"),
        row.get("case_id"),
        row.get("worker_count"),
        row.get("fault_type"),
        row.get("fault_rate"),
        row.get("ablation_mode"),
        row.get("cohort_member_id"),
        row.get("provider_family"),
        row.get("provider_model_id"),
        row.get("model_entry_id"),
        row.get("worker_death_count"),
        row.get("kill_progress_target_ratio"),
    )


def _iter_run_roots(root: Path) -> Iterator[Path]:
    experiments_root = root / "experiments"
    if not experiments_root.is_dir():
        return
    for experiment_root in experiments_root.iterdir():
        runs_root = experiment_root / "runs"
        if not experiment_root.is_dir() or not runs_root.is_dir():
            continue
        for condition_root in runs_root.iterdir():
            if not condition_root.is_dir():
                continue
            for repeat_root in condition_root.iterdir():
                if repeat_root.is_dir() and (
                    repeat_root / "CURRENT.json"
                ).is_file():
                    yield repeat_root


def _run_roots(root: Path) -> list[Path]:
    """保留内部兼容入口；正式复算改由 SQLite 对 iterator 做磁盘排序。"""

    return sorted(_iter_run_roots(root))


def _current_generation(run_root: Path) -> Path:
    pointer = _read_json(run_root / "CURRENT.json")
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("CURRENT generation_id is missing")
    generation = run_root / ".generations" / generation_id
    if not generation.is_dir():
        raise ValueError("CURRENT generation is missing")
    manifest_path = generation / "generation_manifest.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        schema = manifest.get("schema_version")
        if (
            schema == "tokenshare.paper_checkpoint_generation.v3"
            and manifest.get("generation_kind") != "snapshot"
        ):
            raise ValueError(
                "terminal formal metrics require a v3 snapshot generation"
            )
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


def _rate(numerator: Any, denominator: Any) -> float | None:
    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, (int, float))
        or isinstance(denominator, bool)
        or not isinstance(denominator, (int, float))
        or denominator == 0
    ):
        return None
    return numerator / denominator


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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_number(value: Any) -> int | float | None:
    return value if _is_number(value) else None


def _status(value: Any) -> str:
    return str(getattr(value, "value", value or "unknown"))


def _provider_attempt_records(
    attempts: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        attempt
        for attempt in attempts
        if attempt.get("record_scope") != "experiment"
        and (
            (
                isinstance(attempt.get("provider_attempt_count"), int)
                and not isinstance(attempt.get("provider_attempt_count"), bool)
                and int(attempt["provider_attempt_count"]) > 0
            )
            or (
                attempt.get("provider_attempt_count") is None
                and _status(attempt.get("attempt_status")) != "executor_error"
            )
        )
    )


def _provider_attempt_count(attempts: Sequence[Mapping[str, Any]]) -> int:
    return len(_provider_attempt_records(attempts))


def _csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(_iter_csv_chunks(rows))


def _iter_csv_chunks(
    rows: Sequence[Mapping[str, Any]],
) -> Iterator[str]:
    fields = sorted({key for row in rows for key in row}) or ["no_rows"]
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    yield handle.getvalue()
    handle.seek(0)
    handle.truncate(0)
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
        yield handle.getvalue()
        handle.seek(0)
        handle.truncate(0)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required formal evidence is missing: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        body = json.load(handle)
    if not isinstance(body, dict):
        raise ValueError(f"formal evidence must be an object: {path.name}")
    return body


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl(path))


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"required formal evidence is missing: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            body = json.loads(line)
            if not isinstance(body, dict):
                raise ValueError(
                    f"formal JSONL record must be an object: {path.name}"
                )
            yield body


def _load_run_bundle(
    *,
    suite_root: Path,
    generation: Path,
    condition: Mapping[str, Any],
) -> dict[str, Any]:
    bundle = {
        "condition": dict(condition),
        "suite_root": suite_root,
        "tasks": _read_jsonl(generation / "per_task_results.jsonl"),
        "attempts": _read_jsonl(generation / "per_attempt_results.jsonl"),
        "faults": _read_jsonl(generation / "fault_injections.jsonl"),
        "events": _read_jsonl(generation / "events" / "event_log.jsonl"),
        "artifacts": (
            _read_jsonl(generation / "artifacts" / "artifact_index.jsonl")
            if (generation / "artifacts" / "artifact_index.jsonl").is_file()
            else []
        ),
    }
    if not bundle["tasks"] or not bundle["attempts"] or not bundle["events"]:
        raise ValueError(
            f"incomplete formal evidence: {condition.get('condition_id')}"
        )
    return bundle


def _write_text(path: Path, content: str) -> None:
    _write_chunks_atomic(path, iter((content,)))


def _write_chunks_atomic(
    path: Path,
    chunks: Iterator[str | bytes],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as handle:
            for chunk in chunks:
                encoded = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                handle.write(encoded)
                digest.update(encoded)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return "sha256:" + digest.hexdigest()


def _iter_json_chunks(
    value: Any,
    *,
    indent: int | None = None,
    trailing_newline: bool = False,
    compact: bool = True,
) -> Iterator[str]:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        separators=(",", ":") if indent is None and compact else None,
    )
    yield from encoder.iterencode(value)
    if trailing_newline:
        yield "\n"


def _iter_file_chunks(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            yield chunk


def _hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    for chunk in _iter_file_chunks(path, chunk_size=chunk_size):
        digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _digest(value: Any) -> str:
    digest = hashlib.sha256()
    for chunk in _iter_json_chunks(value):
        digest.update(chunk.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _hash_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
