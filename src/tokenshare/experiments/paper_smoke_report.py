"""从持久化 smoke evidence 派生统一 JSON/CSV 与审计报告。"""

from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore


SMOKE_SUMMARY_SCHEMA_VERSION = "tokenshare.paper_smoke_summary.v2"
_CSV_FIELDS = (
    "experiment_id",
    "condition_id",
    "condition_digest",
    "case_id",
    "repeat_id",
    "domain",
    "difficulty",
    "topic_family",
    "worker_count",
    "fault_type",
    "fault_rate",
    "dead_worker_count",
    "kill_progress_percent",
    "ablation_mode",
    "cohort_member_id",
    "provider_family",
    "provider_model_id",
    "root_status",
    "outcome_status",
    "evidence_integrity",
    "evidence_integrity_reasons",
    "accepted_validity",
    "accepted_validity_unavailable_reason",
    "correctness_numerator",
    "correctness_denominator",
    "correctness_rate",
    "correctness_missing_count",
    "correctness_unavailable_reason",
    "completion_numerator",
    "completion_denominator",
    "completion_rate",
    "provider_attempt_count",
    "provider_attempt_count_unavailable_reason",
    "provider_latency_ms",
    "provider_latency_sample_size",
    "provider_latency_missing_count",
    "provider_latency_unavailable_reason",
    "prompt_tokens",
    "prompt_tokens_sample_size",
    "prompt_tokens_missing_count",
    "prompt_tokens_unavailable_reason",
    "completion_tokens",
    "completion_tokens_sample_size",
    "completion_tokens_missing_count",
    "completion_tokens_unavailable_reason",
    "total_tokens",
    "total_tokens_sample_size",
    "total_tokens_missing_count",
    "total_tokens_unavailable_reason",
    "cost_estimate",
    "cost_estimate_sample_size",
    "cost_estimate_missing_count",
    "cost_estimate_unavailable_reason",
    "provider_actual_billing",
    "provider_actual_billing_available",
    "wall_clock_ms",
    "wall_clock_ms_unavailable_reason",
    "failure_stage",
    "failure_kind",
    "baseline_policy",
    "baseline_comparison_eligible",
    "baseline_unavailable_reason",
    "smoke_execution_status",
    "paper_eligible",
    "ineligibility_reasons",
    "event_refs",
    "artifact_refs",
    "fault_refs",
)


def generate_paper_smoke_report(
    *,
    output_root: str | Path,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    """只读取已经持久化的 task/attempt/event/artifact evidence。"""

    root = Path(output_root).resolve(strict=False)
    suite = _read_object(root / "suite_manifest.json")
    _validate_smoke_suite(suite)
    plan = _read_object(root / "smoke_execution_plan.json")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("smoke execution plan requires resolved items")
    capturing = suite.get("capturing") is True
    rows = [
        _with_row_metric_fields(
            _row_from_persisted_evidence(
                root=root,
                item=item,
                capturing=capturing,
                baseline_policy=str(
                    plan.get("baseline_policy", "required_by_formal_plan")
                ),
            )
        )
        for item in items
    ]
    completed_root_count = sum(
        row["smoke_execution_status"] == "completed" for row in rows
    )
    correctness_numerator = sum(
        int(row["correctness_numerator"]) for row in rows
    )
    correctness_missing_count = sum(
        int(row["correctness_missing_count"]) for row in rows
    )
    provider_latency_ms = _complete_sum(rows, "provider_latency_ms")
    provider_latency_sample_size = sum(
        int(row["provider_latency_sample_size"]) for row in rows
    )
    provider_latency_missing_count = sum(
        int(row["provider_latency_missing_count"]) for row in rows
    )
    summary = {
        "schema_version": SMOKE_SUMMARY_SCHEMA_VERSION,
        "suite_id": suite.get("suite_id"),
        "suite_status": suite.get("status"),
        "row_count": len(rows),
        "completed_root_count": completed_root_count,
        "failed_or_blocked_root_count": sum(
            row["smoke_execution_status"] != "completed" for row in rows
        ),
        "provider_attempt_count": _complete_sum(
            rows,
            "provider_attempt_count",
        ),
        "provider_attempt_count_sample_size": sum(
            row["provider_attempt_count"] is not None for row in rows
        ),
        "provider_attempt_count_missing_count": sum(
            row["provider_attempt_count"] is None for row in rows
        ),
        "provider_attempt_count_unavailable_reason": _summary_missing_reason(
            rows,
            "provider_attempt_count",
            "incomplete_provider_attempt_evidence",
        ),
        "provider_latency_ms": provider_latency_ms,
        "provider_latency_sample_size": provider_latency_sample_size,
        "provider_latency_missing_count": provider_latency_missing_count,
        "provider_latency_unavailable_reason": (
            "incomplete_provider_latency_evidence"
            if provider_latency_ms is None
            else (
                "no_provider_attempts"
                if provider_latency_sample_size == 0
                else None
            )
        ),
        "prompt_tokens": _complete_sum(rows, "prompt_tokens"),
        "prompt_tokens_sample_size": sum(
            int(row["prompt_tokens_sample_size"]) for row in rows
        ),
        "prompt_tokens_missing_count": sum(
            int(row["prompt_tokens_missing_count"]) for row in rows
        ),
        "prompt_tokens_unavailable_reason": _summary_missing_reason(
            rows,
            "prompt_tokens",
            "incomplete_prompt_tokens_evidence",
        ),
        "completion_tokens": _complete_sum(rows, "completion_tokens"),
        "completion_tokens_sample_size": sum(
            int(row["completion_tokens_sample_size"]) for row in rows
        ),
        "completion_tokens_missing_count": sum(
            int(row["completion_tokens_missing_count"]) for row in rows
        ),
        "completion_tokens_unavailable_reason": _summary_missing_reason(
            rows,
            "completion_tokens",
            "incomplete_completion_tokens_evidence",
        ),
        "total_tokens": _complete_sum(rows, "total_tokens"),
        "total_tokens_sample_size": sum(
            int(row["total_tokens_sample_size"]) for row in rows
        ),
        "total_tokens_missing_count": sum(
            int(row["total_tokens_missing_count"]) for row in rows
        ),
        "total_tokens_unavailable_reason": _summary_missing_reason(
            rows,
            "total_tokens",
            "incomplete_total_tokens_evidence",
        ),
        "cost_estimate": _complete_sum(rows, "cost_estimate"),
        "cost_estimate_sample_size": sum(
            int(row["cost_estimate_sample_size"]) for row in rows
        ),
        "cost_estimate_missing_count": sum(
            int(row["cost_estimate_missing_count"]) for row in rows
        ),
        "cost_estimate_unavailable_reason": _summary_missing_reason(
            rows,
            "cost_estimate",
            "incomplete_cost_estimate_evidence",
        ),
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "expected_root_count": len(rows),
        "correctness_numerator": correctness_numerator,
        "correctness_denominator": len(rows),
        "correctness_rate": (
            None
            if correctness_missing_count
            else correctness_numerator / len(rows)
        ),
        "correctness_missing_count": correctness_missing_count,
        "correctness_unavailable_reason": (
            "incomplete_correctness_evidence"
            if correctness_missing_count
            else None
        ),
        "completion_numerator": completed_root_count,
        "completion_denominator": len(rows),
        "completion_rate": completed_root_count / len(rows),
        "started_root_count": sum(
            row["root_status"] != "not_started" for row in rows
        ),
        "failed_experimental_root_count": sum(
            row["outcome_status"] == "failed_experimental" for row in rows
        ),
        "blocked_dependency_root_count": sum(
            row["outcome_status"] == "blocked_dependency" for row in rows
        ),
        "not_started_root_count": sum(
            row["root_status"] == "not_started" for row in rows
        ),
        "evidence_integrity_counts": {
            integrity: sum(row["evidence_integrity"] == integrity for row in rows)
            for integrity in ("complete", "missing", "invalid", "corrupt")
        },
        "baseline_policy": plan.get(
            "baseline_policy",
            "required_by_formal_plan",
        ),
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
        "rows": rows,
    }
    metrics_root = root / "metrics"
    audit_root = root / "audit"
    metrics_root.mkdir(parents=True, exist_ok=True)
    audit_root.mkdir(parents=True, exist_ok=True)
    _write_json(metrics_root / "smoke_summary.json", summary)
    _write_csv(metrics_root / "smoke_summary.csv", rows)
    _write_json(
        metrics_root / "smoke_failures.json",
        {
            "schema_version": "tokenshare.paper_smoke_failures.v1",
            "suite_id": suite.get("suite_id"),
            "failure_count": sum(
                row["smoke_execution_status"] != "completed" for row in rows
            ),
            "rows": [
                row
                for row in rows
                if row["smoke_execution_status"] != "completed"
            ],
            "paper_eligible": False,
        },
    )
    _write_json(
        audit_root / "smoke_eligibility_report.json",
        {
            "schema_version": "tokenshare.paper_smoke_eligibility_report.v1",
            "suite_id": suite.get("suite_id"),
            "formal": False,
            "pilot_only": True,
            "regression_only": True,
            "paper_eligible": False,
            "ineligibility_reasons": ["smoke_suite", "pilot_only"],
            "row_count": len(rows),
            "all_rows_ineligible": all(
                row["paper_eligible"] is False for row in rows
            ),
        },
    )
    _write_json(
        audit_root / "secret_scan_report.json",
        _secret_scan(root=root, secret_values=secret_values),
    )
    _write_json(
        audit_root / "smoke_evidence_manifest.json",
        _evidence_manifest(root),
    )
    return summary


def _row_from_persisted_evidence(
    *,
    root: Path,
    item: Any,
    capturing: bool,
    baseline_policy: str,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("smoke execution plan item must be an object")
    experiment_id = _required_string(item, "experiment_id")
    condition_id = _required_string(item, "condition_id")
    case_id = _required_string(item, "case_id")
    repeat_id = item.get("repeat_id")
    selector = item.get("condition_selector")
    if not isinstance(repeat_id, int) or not isinstance(selector, Mapping):
        raise ValueError("smoke execution item identity is incomplete")
    generation = _current_generation(
        root=root,
        experiment_id=experiment_id,
        condition_id=condition_id,
        repeat_id=repeat_id,
    )
    if generation is None:
        return _blocked_row(
            item=item,
            selector=selector,
            baseline_policy=baseline_policy,
            capturing=capturing,
        )
    logical = FormalEvidenceStore(root).load_logical_run_records(
        experiment_id=experiment_id,
        condition_id=condition_id,
        repeat_id=repeat_id,
    )
    tasks = logical["tasks"]
    matching_tasks = [task for task in tasks if task.get("task_id") == case_id]
    if len(matching_tasks) != 1:
        raise ValueError("persisted smoke task identity is missing or ambiguous")
    task = matching_tasks[0]
    for field_name, expected in (
        ("experiment_id", experiment_id),
        ("condition_id", condition_id),
        ("repeat_id", repeat_id),
    ):
        if task.get(field_name) != expected:
            raise ValueError(f"persisted smoke task {field_name} mismatch")
    if (
        task.get("formal") is not False
        or task.get("pilot_only") is not True
        or task.get("regression_only") is not True
        or task.get("paper_eligible") is not False
    ):
        raise ValueError("persisted smoke task eligibility flags are invalid")
    attempts = [
        attempt
        for attempt in logical["attempts"]
        if attempt.get("task_id") == case_id
    ]
    protocol_event_ledger = task.get("protocol_event_ledger")
    protocol_event_hashes = (
        set(protocol_event_ledger.get("event_hashes", ()))
        if isinstance(protocol_event_ledger, Mapping)
        and isinstance(protocol_event_ledger.get("event_hashes"), list)
        else set()
    )
    events = [
        event
        for event in logical["events"]
        if (
            event.get("event_hash") in protocol_event_hashes
            or event.get("task_id") in {None, case_id}
        )
    ]
    artifacts = [
        artifact
        for artifact in logical["artifacts"]
        if artifact.get("task_id") in {None, case_id}
    ]
    faults = [
        fault
        for fault in logical["faults"]
        if fault.get("task_id") in {None, case_id}
    ]
    usage = _actual_usage(
        attempts,
        root=root,
        artifacts=artifacts,
        capturing=capturing,
        task=task,
    )
    reasons = list(
        dict.fromkeys(
            [
                "smoke_suite",
                "pilot_only",
                *(
                    task.get("ineligibility_reasons", [])
                    if isinstance(task.get("ineligibility_reasons"), list)
                    else []
                ),
            ]
        )
    )
    root_status = str(task.get("root_status") or "failed")
    outcome_status = task.get("outcome_status")
    if outcome_status not in {
        "succeeded",
        "failed_experimental",
        "blocked_dependency",
    }:
        outcome_status = (
            "succeeded"
            if root_status == "completed"
            else (
                "blocked_dependency"
                if root_status in {"blocked", "not_started"}
                else "failed_experimental"
            )
        )
    declared_evidence_integrity = task.get("evidence_integrity")
    if declared_evidence_integrity is not None and declared_evidence_integrity not in {
        "complete",
        "missing",
        "invalid",
        "corrupt",
    }:
        raise ValueError("persisted smoke task evidence_integrity is invalid")
    accepted_validity = _optional_bool(
        task.get(
            "accepted_validity",
            task.get("final_deterministic_validity"),
        )
    )
    accepted_validity_unavailable_reason = (
        None
        if accepted_validity is not None
        else (
            "not_applicable_failed_experimental"
            if outcome_status == "failed_experimental"
            else (
                "not_applicable_blocked_dependency"
                if outcome_status == "blocked_dependency"
                else "missing_accepted_validity_evidence"
            )
        )
    )
    runtime_observation = task.get("runtime_observation")
    wall_clock_ms = _optional_non_negative_number(task.get("wall_clock_ms"))
    if wall_clock_ms is None and isinstance(runtime_observation, Mapping):
        wall_clock_ms = _optional_non_negative_number(
            runtime_observation.get("runtime_wall_clock_ms")
        )
    integrity_reasons = [
        *(
            [str(usage["evidence_issue"])]
            if usage.get("evidence_issue") is not None
            else []
        ),
        *(
            ["missing_accepted_validity_evidence"]
            if accepted_validity_unavailable_reason
            == "missing_accepted_validity_evidence"
            else []
        ),
        *(
            ["missing_wall_clock_evidence"]
            if wall_clock_ms is None
            else []
        ),
    ]
    derived_integrity = (
        "invalid"
        if usage.get("evidence_issue") == "invalid_model_execution_record_ref"
        else ("missing" if integrity_reasons else "complete")
    )
    evidence_integrity = _least_complete_integrity(
        declared_evidence_integrity,
        derived_integrity,
    )
    smoke_execution_status = (
        "incomplete"
        if evidence_integrity != "complete"
        else (
            "completed"
            if root_status == "completed"
            else (
                "blocked"
                if root_status in {"blocked", "not_started"}
                else "failed"
            )
        )
    )
    return {
        "item_id": item.get("item_id"),
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "condition_digest": item.get("condition_digest"),
        "selection_id": item.get("selection_id"),
        "selection_digest": item.get("selection_digest"),
        "case_id": case_id,
        "repeat_id": repeat_id,
        "domain": selector.get("domain", task.get("domain")),
        "difficulty": selector.get("difficulty", task.get("difficulty")),
        "paper_difficulty": selector.get(
            "paper_difficulty", task.get("paper_difficulty")
        ),
        "topic_family": selector.get("topic_family", task.get("topic_family")),
        "worker_count": selector.get("worker_count", task.get("worker_count")),
        "fault_type": selector.get("fault_type", task.get("fault_type")),
        "fault_rate": selector.get("fault_rate", task.get("fault_rate")),
        "dead_worker_count": selector.get(
            "dead_worker_count", task.get("worker_death_count")
        ),
        "kill_progress_percent": selector.get("kill_progress_percent"),
        "ablation_mode": selector.get(
            "ablation_mode", task.get("ablation_mode")
        ),
        "cohort_member_id": selector.get(
            "cohort_member_id", task.get("cohort_member_id")
        ),
        "provider_family": task.get("provider_family"),
        "provider_model_id": task.get("provider_model_id"),
        "root_status": root_status,
        "outcome_status": outcome_status,
        "evidence_integrity": evidence_integrity,
        "evidence_integrity_reasons": list(
            dict.fromkeys(
                [
                    *integrity_reasons,
                    *(
                        [f"declared_evidence_integrity:{declared_evidence_integrity}"]
                        if declared_evidence_integrity
                        not in {None, "complete"}
                        else []
                    ),
                ]
            )
        ),
        "accepted_validity": accepted_validity,
        "accepted_validity_unavailable_reason": (
            accepted_validity_unavailable_reason
        ),
        "provider_attempt_count": usage["provider_attempt_count"],
        "provider_attempt_count_unavailable_reason": usage[
            "provider_attempt_count_unavailable_reason"
        ],
        "provider_latency_ms": usage["provider_latency_ms"],
        "provider_latency_sample_size": usage[
            "provider_latency_sample_size"
        ],
        "provider_latency_missing_count": usage[
            "provider_latency_missing_count"
        ],
        "provider_latency_unavailable_reason": usage[
            "provider_latency_unavailable_reason"
        ],
        "prompt_tokens": usage["prompt_tokens"],
        "prompt_tokens_sample_size": usage["prompt_tokens_sample_size"],
        "prompt_tokens_missing_count": usage["prompt_tokens_missing_count"],
        "prompt_tokens_unavailable_reason": usage[
            "prompt_tokens_unavailable_reason"
        ],
        "completion_tokens": usage["completion_tokens"],
        "completion_tokens_sample_size": usage[
            "completion_tokens_sample_size"
        ],
        "completion_tokens_missing_count": usage[
            "completion_tokens_missing_count"
        ],
        "completion_tokens_unavailable_reason": usage[
            "completion_tokens_unavailable_reason"
        ],
        "total_tokens": usage["total_tokens"],
        "total_tokens_sample_size": usage["total_tokens_sample_size"],
        "total_tokens_missing_count": usage["total_tokens_missing_count"],
        "total_tokens_unavailable_reason": usage[
            "total_tokens_unavailable_reason"
        ],
        "cost_estimate": usage["cost_estimate"],
        "cost_estimate_sample_size": usage["cost_estimate_sample_size"],
        "cost_estimate_missing_count": usage["cost_estimate_missing_count"],
        "cost_estimate_unavailable_reason": usage[
            "cost_estimate_unavailable_reason"
        ],
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "wall_clock_ms": wall_clock_ms,
        "wall_clock_ms_unavailable_reason": (
            "missing_wall_clock_evidence"
            if wall_clock_ms is None
            else None
        ),
        "failure_stage": task.get("failure_stage"),
        "failure_kind": task.get("failure_kind", task.get("error_kind")),
        "baseline_policy": baseline_policy,
        "baseline": task.get("baseline"),
        "baseline_comparison_eligible": task.get(
            "baseline_comparison_eligible"
        ),
        "baseline_unavailable_reason": task.get(
            "baseline_unavailable_reason"
        ),
        "event_refs": sorted(
            str(event["event_id"])
            for event in events
            if isinstance(event.get("event_id"), str)
        ),
        "artifact_refs": sorted(
            str(artifact["path"])
            for artifact in artifacts
            if isinstance(artifact.get("path"), str)
        ),
        "fault_refs": sorted(
            {
                ref
                for fault in faults
                if (ref := _traceable_fault_ref(fault)) is not None
            }
        ),
        "smoke_execution_status": smoke_execution_status,
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": reasons,
    }


def _blocked_row(
    *,
    item: Mapping[str, Any],
    selector: Mapping[str, Any],
    baseline_policy: str,
    capturing: bool,
) -> dict[str, Any]:
    usage = (
        _actual_usage(
            (),
            root=Path(),
            artifacts=(),
            capturing=True,
        )
        if capturing
        else _missing_usage("missing_provider_attempt_evidence")
    )
    return {
        "item_id": item.get("item_id"),
        "experiment_id": item.get("experiment_id"),
        "condition_id": item.get("condition_id"),
        "condition_digest": item.get("condition_digest"),
        "selection_id": item.get("selection_id"),
        "selection_digest": item.get("selection_digest"),
        "case_id": item.get("case_id"),
        "repeat_id": item.get("repeat_id"),
        "domain": selector.get("domain"),
        "difficulty": selector.get("difficulty"),
        "paper_difficulty": selector.get("paper_difficulty"),
        "topic_family": selector.get("topic_family"),
        "worker_count": selector.get("worker_count"),
        "fault_type": selector.get("fault_type"),
        "fault_rate": selector.get("fault_rate"),
        "dead_worker_count": selector.get("dead_worker_count"),
        "kill_progress_percent": selector.get("kill_progress_percent"),
        "ablation_mode": selector.get("ablation_mode"),
        "cohort_member_id": selector.get("cohort_member_id"),
        "provider_family": None,
        "provider_model_id": None,
        "root_status": "blocked",
        "outcome_status": "blocked_dependency",
        "evidence_integrity": "missing",
        "evidence_integrity_reasons": ["missing_persisted_evidence"],
        "accepted_validity": None,
        "accepted_validity_unavailable_reason": (
            "missing_accepted_validity_evidence"
        ),
        "provider_attempt_count": usage["provider_attempt_count"],
        "provider_attempt_count_unavailable_reason": usage[
            "provider_attempt_count_unavailable_reason"
        ],
        "provider_latency_ms": usage["provider_latency_ms"],
        "provider_latency_sample_size": usage[
            "provider_latency_sample_size"
        ],
        "provider_latency_missing_count": usage[
            "provider_latency_missing_count"
        ],
        "provider_latency_unavailable_reason": usage[
            "provider_latency_unavailable_reason"
        ],
        "prompt_tokens": usage["prompt_tokens"],
        "prompt_tokens_sample_size": usage["prompt_tokens_sample_size"],
        "prompt_tokens_missing_count": usage["prompt_tokens_missing_count"],
        "prompt_tokens_unavailable_reason": usage[
            "prompt_tokens_unavailable_reason"
        ],
        "completion_tokens": usage["completion_tokens"],
        "completion_tokens_sample_size": usage[
            "completion_tokens_sample_size"
        ],
        "completion_tokens_missing_count": usage[
            "completion_tokens_missing_count"
        ],
        "completion_tokens_unavailable_reason": usage[
            "completion_tokens_unavailable_reason"
        ],
        "total_tokens": usage["total_tokens"],
        "total_tokens_sample_size": usage["total_tokens_sample_size"],
        "total_tokens_missing_count": usage["total_tokens_missing_count"],
        "total_tokens_unavailable_reason": usage[
            "total_tokens_unavailable_reason"
        ],
        "cost_estimate": usage["cost_estimate"],
        "cost_estimate_sample_size": usage["cost_estimate_sample_size"],
        "cost_estimate_missing_count": usage["cost_estimate_missing_count"],
        "cost_estimate_unavailable_reason": usage[
            "cost_estimate_unavailable_reason"
        ],
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "wall_clock_ms": None,
        "wall_clock_ms_unavailable_reason": "missing_wall_clock_evidence",
        "failure_stage": "preflight",
        "failure_kind": item.get("blocked_reason", "missing_persisted_evidence"),
        "baseline_policy": baseline_policy,
        "baseline": None,
        "baseline_comparison_eligible": False,
        "baseline_unavailable_reason": (
            "smoke_baseline_not_requested"
            if baseline_policy == "omitted_for_smoke_regression"
            else "missing_persisted_evidence"
        ),
        "event_refs": [],
        "artifact_refs": [],
        "fault_refs": [],
        "smoke_execution_status": "blocked",
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": ["smoke_suite", "pilot_only"],
    }


def _traceable_fault_ref(fault: Mapping[str, Any]) -> str | None:
    record_ref = fault.get("record_ref")
    candidates = (
        *(
            (
                record_ref.get("path"),
                record_ref.get("artifact_id"),
                record_ref.get("uri"),
            )
            if isinstance(record_ref, Mapping)
            else ()
        ),
        fault.get("fault_injection_id"),
    )
    return next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, str) and candidate.strip()
        ),
        None,
    )


def _with_row_metric_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    accepted_validity = row.get("accepted_validity")
    correctness_missing = accepted_validity is None
    correctness_numerator = int(accepted_validity is True)
    completed = row.get("smoke_execution_status") == "completed"
    return {
        **row,
        "correctness_numerator": correctness_numerator,
        "correctness_denominator": 1,
        "correctness_rate": (
            None if correctness_missing else float(correctness_numerator)
        ),
        "correctness_missing_count": int(correctness_missing),
        "correctness_unavailable_reason": (
            (
                row.get("accepted_validity_unavailable_reason")
                or "missing_accepted_validity_evidence"
            )
            if correctness_missing
            else None
        ),
        "completion_numerator": int(completed),
        "completion_denominator": 1,
        "completion_rate": float(completed),
    }


def _actual_usage(
    attempts: Sequence[Mapping[str, Any]],
    *,
    root: Path,
    artifacts: Sequence[Mapping[str, Any]],
    capturing: bool,
    task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trace_source_usage = (
        task.get("trace_source_usage") if isinstance(task, Mapping) else None
    )
    if trace_source_usage is not None:
        try:
            return _trace_source_actual_usage(trace_source_usage)
        except (ArithmeticError, TypeError, ValueError):
            return _missing_usage("invalid_trace_source_usage_evidence")
    if capturing:
        return {
            "provider_attempt_count": 0,
            "provider_attempt_count_unavailable_reason": None,
            "provider_latency_ms": 0.0,
            "provider_latency_sample_size": 0,
            "provider_latency_missing_count": 0,
            "provider_latency_unavailable_reason": "no_provider_attempts",
            "prompt_tokens": 0,
            "prompt_tokens_sample_size": 0,
            "prompt_tokens_missing_count": 0,
            "prompt_tokens_unavailable_reason": None,
            "completion_tokens": 0,
            "completion_tokens_sample_size": 0,
            "completion_tokens_missing_count": 0,
            "completion_tokens_unavailable_reason": None,
            "total_tokens": 0,
            "total_tokens_sample_size": 0,
            "total_tokens_missing_count": 0,
            "total_tokens_unavailable_reason": None,
            "cost_estimate": 0.0,
            "cost_estimate_sample_size": 0,
            "cost_estimate_missing_count": 0,
            "cost_estimate_unavailable_reason": None,
            "evidence_issue": None,
        }
    if not attempts:
        return _missing_usage("missing_provider_attempt_evidence")

    provider_attempts = 0
    latency_values: list[float] = []
    latency_missing = 0
    prompt_token_values: list[int] = []
    completion_token_values: list[int] = []
    token_values: list[int] = []
    cost_values: list[float] = []
    prompt_token_missing = 0
    completion_token_missing = 0
    token_missing = 0
    inconsistent_token_usage = 0
    cost_missing = 0
    dispatched_attempt_count = 0
    for attempt in attempts:
        if _explicit_zero_call_attempt(attempt):
            continue
        dispatched_attempt_count += 1
        try:
            record = _verified_model_execution_record(
                attempt=attempt,
                root=root,
                artifacts=artifacts,
            )
        except ValueError:
            return _missing_usage("invalid_model_execution_record_ref")
        actual_provider_attempts = record["actual_provider_attempts"]
        provider_attempts += len(actual_provider_attempts)
        latency_ms = attempt.get("latency_ms")
        if (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            latency_values.append(float(latency_ms))
        else:
            try:
                latency_values.extend(
                    _verified_provider_latency_values(
                        record=record,
                        root=root,
                        artifacts=artifacts,
                    )
                )
            except ValueError:
                latency_missing += len(actual_provider_attempts)

        usage = attempt.get("usage_summary")
        if not isinstance(usage, Mapping):
            usage = {}
        prompt = attempt.get("prompt_tokens")
        if not isinstance(prompt, int) or isinstance(prompt, bool):
            prompt = usage.get("prompt_tokens")
        completion = attempt.get("completion_tokens")
        if not isinstance(completion, int) or isinstance(completion, bool):
            completion = usage.get("completion_tokens")
        total = attempt.get("total_tokens")
        if not isinstance(total, int) or isinstance(total, bool):
            total = usage.get("total_tokens")

        prompt_valid = (
            isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0
        )
        completion_valid = (
            isinstance(completion, int)
            and not isinstance(completion, bool)
            and completion >= 0
        )
        total_valid = (
            isinstance(total, int) and not isinstance(total, bool) and total >= 0
        )
        if (
            prompt_valid
            and completion_valid
            and total_valid
            and total != prompt + completion
        ):
            prompt_token_missing += 1
            completion_token_missing += 1
            token_missing += 1
            inconsistent_token_usage += 1
        else:
            if prompt_valid:
                prompt_token_values.append(prompt)
            else:
                prompt_token_missing += 1
            if completion_valid:
                completion_token_values.append(completion)
            else:
                completion_token_missing += 1
            if total_valid:
                token_values.append(total)
            else:
                token_missing += 1

        cost_value = attempt.get("cost_estimate")
        if not isinstance(cost_value, (int, float)) or isinstance(cost_value, bool):
            cost_value = usage.get("cost_estimate")
        if (
            isinstance(cost_value, (int, float))
            and not isinstance(cost_value, bool)
            and float(cost_value) >= 0.0
        ):
            cost_values.append(float(cost_value))
        else:
            cost_missing += 1

    if dispatched_attempt_count == 0:
        return {
            "provider_attempt_count": 0,
            "provider_attempt_count_unavailable_reason": None,
            "provider_latency_ms": 0.0,
            "provider_latency_sample_size": 0,
            "provider_latency_missing_count": 0,
            "provider_latency_unavailable_reason": "no_provider_attempts",
            "prompt_tokens": 0,
            "prompt_tokens_sample_size": 0,
            "prompt_tokens_missing_count": 0,
            "prompt_tokens_unavailable_reason": None,
            "completion_tokens": 0,
            "completion_tokens_sample_size": 0,
            "completion_tokens_missing_count": 0,
            "completion_tokens_unavailable_reason": None,
            "total_tokens": 0,
            "total_tokens_sample_size": 0,
            "total_tokens_missing_count": 0,
            "total_tokens_unavailable_reason": None,
            "cost_estimate": 0.0,
            "cost_estimate_sample_size": 0,
            "cost_estimate_missing_count": 0,
            "cost_estimate_unavailable_reason": None,
            "evidence_issue": None,
        }
    return {
        "provider_attempt_count": provider_attempts,
        "provider_attempt_count_unavailable_reason": None,
        "provider_latency_ms": (
            sum(latency_values) if latency_missing == 0 else None
        ),
        "provider_latency_sample_size": len(latency_values),
        "provider_latency_missing_count": latency_missing,
        "provider_latency_unavailable_reason": (
            "missing_provider_latency_evidence" if latency_missing else None
        ),
        "prompt_tokens": (
            sum(prompt_token_values) if prompt_token_missing == 0 else None
        ),
        "prompt_tokens_sample_size": len(prompt_token_values),
        "prompt_tokens_missing_count": prompt_token_missing,
        "prompt_tokens_unavailable_reason": (
            "inconsistent_token_usage_evidence"
            if inconsistent_token_usage
            else "missing_prompt_tokens_evidence"
            if prompt_token_missing
            else None
        ),
        "completion_tokens": (
            sum(completion_token_values)
            if completion_token_missing == 0
            else None
        ),
        "completion_tokens_sample_size": len(completion_token_values),
        "completion_tokens_missing_count": completion_token_missing,
        "completion_tokens_unavailable_reason": (
            "inconsistent_token_usage_evidence"
            if inconsistent_token_usage
            else "missing_completion_tokens_evidence"
            if completion_token_missing
            else None
        ),
        "total_tokens": sum(token_values) if token_missing == 0 else None,
        "total_tokens_sample_size": len(token_values),
        "total_tokens_missing_count": token_missing,
        "total_tokens_unavailable_reason": (
            "inconsistent_token_usage_evidence"
            if inconsistent_token_usage
            else "missing_total_tokens_evidence"
            if token_missing
            else None
        ),
        "cost_estimate": sum(cost_values) if cost_missing == 0 else None,
        "cost_estimate_sample_size": len(cost_values),
        "cost_estimate_missing_count": cost_missing,
        "cost_estimate_unavailable_reason": (
            "missing_cost_estimate_evidence" if cost_missing else None
        ),
        "evidence_issue": (
            "missing_usage_evidence"
            if cost_missing or (token_missing and not inconsistent_token_usage)
            else None
        ),
    }


def _trace_source_actual_usage(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        raise ValueError("trace source usage summary must be a mapping")
    if (
        summary.get("schema_version") != "tokenshare.paper_trace_source_usage.v1"
        or summary.get("attribution_kind") != "immutable_response_bank"
        or summary.get("current_provider_call_count") != 0
    ):
        raise ValueError("trace source usage summary header is invalid")
    current_spend = Decimal(str(summary.get("current_provider_spend_cny")))
    if not current_spend.is_finite() or current_spend != 0:
        raise ValueError("trace source usage current provider spend is not zero")
    raw_consumptions = summary.get("consumptions")
    if not isinstance(raw_consumptions, Sequence) or isinstance(
        raw_consumptions, (str, bytes, bytearray)
    ):
        raise ValueError("trace source consumptions must be a sequence")
    consumptions = tuple(raw_consumptions)
    if (
        summary.get("committed_consumption_count") != len(consumptions)
        or any(not isinstance(value, Mapping) for value in consumptions)
    ):
        raise ValueError("trace source consumption count is invalid")
    consumption_ids = tuple(
        consumption.get("consumption_id") for consumption in consumptions
    )
    if (
        any(
            not isinstance(consumption_id, str) or not consumption_id
            for consumption_id in consumption_ids
        )
        or len(set(consumption_ids)) != len(consumption_ids)
    ):
        raise ValueError("trace source consumption identity is invalid")

    for consumption in consumptions:
        prompt = consumption.get("prompt_tokens")
        completion = consumption.get("completion_tokens")
        total = consumption.get("total_tokens")
        if (
            prompt is not None
            and completion is not None
            and total is not None
            and (
                type(prompt) is not int
                or type(completion) is not int
                or type(total) is not int
                or prompt < 0
                or completion < 0
                or total < 0
                or total != prompt + completion
            )
        ):
            raise ValueError("trace source token usage is inconsistent")

    latency = _trace_source_metric(
        consumptions,
        field_name="latency_ms",
        metric_kind="number",
        unavailable_reason="missing_trace_source_latency_evidence",
    )
    prompt = _trace_source_metric(
        consumptions,
        field_name="prompt_tokens",
        metric_kind="integer",
        unavailable_reason="missing_trace_source_prompt_tokens_evidence",
    )
    completion = _trace_source_metric(
        consumptions,
        field_name="completion_tokens",
        metric_kind="integer",
        unavailable_reason="missing_trace_source_completion_tokens_evidence",
    )
    total = _trace_source_metric(
        consumptions,
        field_name="total_tokens",
        metric_kind="integer",
        unavailable_reason="missing_trace_source_total_tokens_evidence",
    )
    cost = _trace_source_metric(
        consumptions,
        field_name="cost_estimate_cny",
        metric_kind="decimal",
        unavailable_reason="missing_trace_source_cost_estimate_evidence",
    )
    any_missing = any(
        metric["missing_count"] > 0
        for metric in (latency, prompt, completion, total, cost)
    )
    return {
        "provider_attempt_count": 0,
        "provider_attempt_count_unavailable_reason": None,
        "provider_latency_ms": latency["value"],
        "provider_latency_sample_size": latency["sample_size"],
        "provider_latency_missing_count": latency["missing_count"],
        "provider_latency_unavailable_reason": latency["unavailable_reason"],
        "prompt_tokens": prompt["value"],
        "prompt_tokens_sample_size": prompt["sample_size"],
        "prompt_tokens_missing_count": prompt["missing_count"],
        "prompt_tokens_unavailable_reason": prompt["unavailable_reason"],
        "completion_tokens": completion["value"],
        "completion_tokens_sample_size": completion["sample_size"],
        "completion_tokens_missing_count": completion["missing_count"],
        "completion_tokens_unavailable_reason": completion["unavailable_reason"],
        "total_tokens": total["value"],
        "total_tokens_sample_size": total["sample_size"],
        "total_tokens_missing_count": total["missing_count"],
        "total_tokens_unavailable_reason": total["unavailable_reason"],
        "cost_estimate": cost["value"],
        "cost_estimate_sample_size": cost["sample_size"],
        "cost_estimate_missing_count": cost["missing_count"],
        "cost_estimate_unavailable_reason": cost["unavailable_reason"],
        "evidence_issue": (
            "missing_trace_source_usage_evidence" if any_missing else None
        ),
    }


def _trace_source_metric(
    consumptions: Sequence[Mapping[str, Any]],
    *,
    field_name: str,
    metric_kind: str,
    unavailable_reason: str,
) -> dict[str, Any]:
    values: list[int | float | Decimal] = []
    missing_count = 0
    for consumption in consumptions:
        value = consumption.get(field_name)
        if value is None:
            missing_count += 1
            continue
        if metric_kind == "integer":
            if type(value) is not int or value < 0:
                raise ValueError(f"trace source {field_name} is invalid")
            values.append(value)
        elif metric_kind == "number":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"trace source {field_name} is invalid")
            values.append(float(value))
        elif metric_kind == "decimal":
            normalized = Decimal(str(value))
            if not normalized.is_finite() or normalized < 0:
                raise ValueError(f"trace source {field_name} is invalid")
            values.append(normalized)
        else:
            raise ValueError("trace source metric kind is unsupported")
    if not consumptions:
        missing_count = 1
    complete = missing_count == 0
    if metric_kind == "decimal":
        total: int | float | Decimal = sum(values, Decimal(0))
        value = float(total) if complete else None
    elif metric_kind == "number":
        value = float(sum(values)) if complete else None
    else:
        value = int(sum(values)) if complete else None
    return {
        "value": value,
        "sample_size": len(values),
        "missing_count": missing_count,
        "unavailable_reason": None if complete else unavailable_reason,
    }


def _missing_usage(reason: str) -> dict[str, Any]:
    return {
        "provider_attempt_count": None,
        "provider_attempt_count_unavailable_reason": reason,
        "provider_latency_ms": None,
        "provider_latency_sample_size": 0,
        "provider_latency_missing_count": 1,
        "provider_latency_unavailable_reason": reason,
        "prompt_tokens": None,
        "prompt_tokens_sample_size": 0,
        "prompt_tokens_missing_count": 1,
        "prompt_tokens_unavailable_reason": reason,
        "completion_tokens": None,
        "completion_tokens_sample_size": 0,
        "completion_tokens_missing_count": 1,
        "completion_tokens_unavailable_reason": reason,
        "total_tokens": None,
        "total_tokens_sample_size": 0,
        "total_tokens_missing_count": 1,
        "total_tokens_unavailable_reason": reason,
        "cost_estimate": None,
        "cost_estimate_sample_size": 0,
        "cost_estimate_missing_count": 1,
        "cost_estimate_unavailable_reason": reason,
        "evidence_issue": reason,
    }


def _explicit_zero_call_attempt(attempt: Mapping[str, Any]) -> bool:
    return (
        attempt.get("schema_version") == "tokenshare.paper_attempt_result.v2"
        and attempt.get("attempt_status") == "executor_error"
        and attempt.get("provider_attempt_count") == 0
        and attempt.get("model_execution_record_ref") is None
    )


def _verified_model_execution_record(
    *,
    attempt: Mapping[str, Any],
    root: Path,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    record_ref = attempt.get("model_execution_record_ref")
    if not isinstance(record_ref, Mapping):
        raise ValueError("model execution record ref is missing")
    artifact_id = record_ref.get("artifact_id")
    content_hash = record_ref.get("content_hash")
    if (
        not isinstance(artifact_id, str)
        or not artifact_id
        or not isinstance(content_hash, str)
        or not content_hash.startswith("sha256:")
    ):
        raise ValueError("model execution record ref is incomplete")
    matches = [
        artifact
        for artifact in artifacts
        if artifact.get("artifact_id") == artifact_id
        and artifact.get("content_hash") == content_hash
        and isinstance(artifact.get("path"), str)
    ]
    if len(matches) != 1:
        raise ValueError("model execution record ref is unresolved")
    path = root / str(matches[0]["path"])
    if (
        not path.is_file()
        or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != content_hash
    ):
        raise ValueError("model execution record artifact verification failed")
    record = _read_object(path)
    record_core = dict(record)
    record_digest = record_core.pop("record_digest", None)
    if (
        record.get("schema_version")
        != "tokenshare.paper_model_execution_record.v2"
        or record_digest != _digest_json(record_core)
    ):
        raise ValueError("model execution record is not canonical")
    expected_identity = {
        "condition_id": attempt.get("condition_id"),
        "repeat_id": attempt.get("repeat_id"),
        "run_id": attempt.get("run_id"),
        "task_id": attempt.get(
            "protocol_task_id",
            attempt.get("task_id"),
        ),
        "unit_id": attempt.get("unit_id"),
        "attempt_id": attempt.get("attempt_id"),
    }
    for field_name, expected in expected_identity.items():
        if expected is not None and record.get(field_name) != expected:
            raise ValueError("model execution record identity mismatch")
    provider_records = record.get("actual_provider_attempts")
    request_identities = record.get("actual_request_identities")
    if (
        not isinstance(provider_records, list)
        or not provider_records
        or not all(isinstance(item, Mapping) for item in provider_records)
        or not isinstance(request_identities, list)
        or len(request_identities) != len(provider_records)
        or not all(isinstance(item, Mapping) for item in request_identities)
    ):
        raise ValueError("model execution record provider attempts are invalid")
    return record


def _verified_provider_latency_values(
    *,
    record: Mapping[str, Any],
    root: Path,
    artifacts: Sequence[Mapping[str, Any]],
) -> tuple[float, ...]:
    provenance_ref = record.get("provenance_ref")
    if not isinstance(provenance_ref, Mapping):
        raise ValueError("provider provenance ref is missing")
    artifact_id = provenance_ref.get("artifact_id")
    content_hash = provenance_ref.get("content_hash")
    if (
        not isinstance(artifact_id, str)
        or not artifact_id
        or not isinstance(content_hash, str)
        or not content_hash.startswith("sha256:")
    ):
        raise ValueError("provider provenance ref is incomplete")
    matches = [
        artifact
        for artifact in artifacts
        if artifact.get("artifact_id") == artifact_id
        and artifact.get("content_hash") == content_hash
        and isinstance(artifact.get("path"), str)
    ]
    if len(matches) != 1:
        raise ValueError("provider provenance ref is unresolved")
    path = root / str(matches[0]["path"])
    if (
        not path.is_file()
        or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != content_hash
    ):
        raise ValueError("provider provenance artifact verification failed")
    provenance = _read_object(path)
    provenance_attempts = provenance.get("attempts")
    provider_attempts = record.get("actual_provider_attempts")
    if (
        provenance.get("schema_version")
        != "phase7.ai_provider_call_provenance.v2"
        or not isinstance(provenance_attempts, list)
        or not isinstance(provider_attempts, list)
        or len(provenance_attempts) != len(provider_attempts)
        or not provenance_attempts
    ):
        raise ValueError("provider provenance attempts are invalid")
    latency_values: list[float] = []
    for provenance_attempt, provider_attempt in zip(
        provenance_attempts,
        provider_attempts,
        strict=True,
    ):
        if not isinstance(provenance_attempt, Mapping) or not isinstance(
            provider_attempt, Mapping
        ):
            raise ValueError("provider provenance attempt is invalid")
        for field_name in (
            "provider_family",
            "configured_model",
            "entry_id",
            "result_kind",
        ):
            if provenance_attempt.get(field_name) != provider_attempt.get(field_name):
                raise ValueError("provider provenance attempt identity mismatch")
        latency_ms = provenance_attempt.get("latency_ms")
        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or latency_ms < 0
        ):
            raise ValueError("provider provenance latency is invalid")
        latency_values.append(float(latency_ms))
    return tuple(latency_values)


def _complete_sum(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
) -> int | float | None:
    values = [row.get(field_name) for row in rows]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        return None
    return sum(values)


def _summary_missing_reason(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
    reason: str,
) -> str | None:
    return reason if any(row.get(field_name) is None for row in rows) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_non_negative_number(value: Any) -> int | float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
    ):
        return None
    return value


def _least_complete_integrity(
    declared: Any,
    derived: str,
) -> str:
    order = {"complete": 0, "missing": 1, "invalid": 2, "corrupt": 3}
    declared_value = declared if declared in order else "complete"
    return max((declared_value, derived), key=order.__getitem__)


def _digest_json(body: Any) -> str:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _current_generation(
    *,
    root: Path,
    experiment_id: str,
    condition_id: str,
    repeat_id: int,
) -> Path | None:
    run_root = (
        root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    current_path = run_root / "CURRENT.json"
    if not current_path.is_file():
        return None
    current = _read_object(current_path)
    generation_id = _required_string(current, "generation_id")
    generation = run_root / ".generations" / generation_id
    if not generation.is_dir():
        raise ValueError("smoke CURRENT generation is missing")
    return generation


def _validate_smoke_suite(suite: Mapping[str, Any]) -> None:
    if (
        suite.get("formal") is not False
        or suite.get("pilot_only") is not True
        or suite.get("regression_only") is not True
        or suite.get("paper_eligible") is not False
        or suite.get("execution_scope") != "smoke_suite"
    ):
        raise ValueError("formal report input is not smoke evidence")
    reasons = suite.get("ineligibility_reasons")
    if not isinstance(reasons, list) or not {"smoke_suite", "pilot_only"}.issubset(
        reasons
    ):
        raise ValueError("smoke suite ineligibility reasons are incomplete")


def _secret_scan(*, root: Path, secret_values: Sequence[str]) -> dict[str, Any]:
    secrets = tuple(sorted({value for value in secret_values if value}))
    leaked_paths: list[str] = []
    checked = 0
    excluded = {
        "audit/secret_scan_report.json",
        "audit/smoke_evidence_manifest.json",
    }
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        data = path.read_bytes()
        checked += 1
        if any(secret.encode("utf-8") in data for secret in secrets):
            leaked_paths.append(relative)
    return {
        "schema_version": "tokenshare.paper_smoke_secret_scan.v1",
        "status": "passed" if not leaked_paths else "failed",
        "checked_file_count": checked,
        "secret_checked_count": len(secrets),
        "leak_count": len(leaked_paths),
        "leaked_paths": leaked_paths,
        "paper_eligible": False,
    }


def _evidence_manifest(root: Path) -> dict[str, Any]:
    excluded = {
        "audit/smoke_evidence_manifest.json",
        "evidence_manifest.json",
    }
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        files.append(
            {
                "path": relative,
                "content_hash": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "tokenshare.paper_smoke_evidence_manifest.v1",
        "file_count": len(files),
        "files": files,
        "paper_eligible": False,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field_name: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (list, dict))
                        else value
                    )
                    for field_name, value in row.items()
                }
            )


def _read_object(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"required JSON object is invalid: {path.name}")
    return body


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"JSONL evidence must contain objects: {path.name}")
    return rows


def _required_string(body: Mapping[str, Any], field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"smoke evidence requires {field_name}")
    return value


def _write_json(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
