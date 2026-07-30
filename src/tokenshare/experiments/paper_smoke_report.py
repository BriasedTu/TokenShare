"""从持久化 smoke evidence 派生统一 JSON/CSV 与审计报告。"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


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
    "accepted_validity",
    "provider_attempt_count",
    "total_tokens",
    "cost_estimate",
    "provider_actual_billing",
    "provider_actual_billing_available",
    "wall_clock_ms",
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
        _row_from_persisted_evidence(
            root=root,
            item=item,
            capturing=capturing,
            baseline_policy=str(
                plan.get("baseline_policy", "required_by_formal_plan")
            ),
        )
        for item in items
    ]
    summary = {
        "schema_version": SMOKE_SUMMARY_SCHEMA_VERSION,
        "suite_id": suite.get("suite_id"),
        "suite_status": suite.get("status"),
        "row_count": len(rows),
        "completed_root_count": sum(
            row["root_status"] == "completed" for row in rows
        ),
        "failed_or_blocked_root_count": sum(
            row["root_status"] != "completed" for row in rows
        ),
        "provider_attempt_count": sum(
            int(row["provider_attempt_count"]) for row in rows
        ),
        "total_tokens": sum(int(row["total_tokens"]) for row in rows),
        "cost_estimate": sum(float(row["cost_estimate"]) for row in rows),
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "expected_root_count": len(rows),
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
                row["root_status"] != "completed" for row in rows
            ),
            "rows": [row for row in rows if row["root_status"] != "completed"],
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
        )
    tasks = _read_jsonl(generation / "per_task_results.jsonl")
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
        for attempt in _read_jsonl(generation / "per_attempt_results.jsonl")
        if attempt.get("task_id") == case_id
    ]
    events = [
        event
        for event in _read_jsonl(generation / "events" / "event_log.jsonl")
        if event.get("task_id") in {None, case_id}
    ]
    artifacts = [
        artifact
        for artifact in _read_jsonl(
            generation / "artifacts" / "artifact_index.jsonl"
        )
        if artifact.get("task_id") in {None, case_id}
    ]
    faults = [
        fault
        for fault in _read_jsonl(generation / "fault_injections.jsonl")
        if fault.get("task_id") in {None, case_id}
    ]
    provider_attempt_count, total_tokens, cost_estimate = _actual_usage(
        attempts,
        capturing=capturing,
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
    evidence_integrity = task.get("evidence_integrity", "complete")
    if evidence_integrity not in {"complete", "missing", "invalid", "corrupt"}:
        raise ValueError("persisted smoke task evidence_integrity is invalid")
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
        "accepted_validity": bool(
            task.get(
                "accepted_validity",
                task.get("final_deterministic_validity", False),
            )
        ),
        "provider_attempt_count": provider_attempt_count,
        "total_tokens": total_tokens,
        "cost_estimate": cost_estimate,
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "wall_clock_ms": int(task.get("wall_clock_ms", 0) or 0),
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
            str(
                fault.get("record_ref", {}).get("path")
                if isinstance(fault.get("record_ref"), Mapping)
                else fault.get("fault_injection_id")
            )
            for fault in faults
            if fault.get("record_ref") is not None
            or fault.get("fault_injection_id") is not None
        ),
        "smoke_execution_status": (
            "completed" if root_status == "completed" else "failed"
        ),
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
) -> dict[str, Any]:
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
        "accepted_validity": False,
        "provider_attempt_count": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
        "provider_actual_billing": None,
        "provider_actual_billing_available": False,
        "wall_clock_ms": 0,
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


def _actual_usage(
    attempts: Sequence[Mapping[str, Any]],
    *,
    capturing: bool,
) -> tuple[int, int, float]:
    if capturing:
        return 0, 0, 0.0
    provider_attempts = 0
    tokens = 0
    cost = 0.0
    for attempt in attempts:
        recorded_count = attempt.get("provider_attempt_count")
        if not isinstance(recorded_count, int) or isinstance(recorded_count, bool):
            recorded_count = 0
        recorded_count = max(0, recorded_count)
        if isinstance(attempt.get("model_execution_record_ref"), Mapping):
            # no_return 等 hook 会在 provider 已返回后抑制 submission；这类
            # attempt 的旧聚合字段可能为 0，但持久化 execution record 证明
            # 至少发生过一次真实 provider call。
            recorded_count = max(1, recorded_count)
        elif recorded_count == 0:
            # 兼容早期只保存 provider_attempt_index 的回归夹具。
            index = attempt.get("provider_attempt_index", 0)
            if isinstance(index, int) and not isinstance(index, bool) and index > 0:
                recorded_count = 1
        provider_attempts += recorded_count
        usage = attempt.get("usage_summary")
        if not isinstance(usage, Mapping):
            usage = {}
        total = attempt.get("total_tokens")
        if not isinstance(total, int) or isinstance(total, bool):
            total = usage.get("total_tokens")
        if not isinstance(total, int) or isinstance(total, bool):
            total = sum(
                int(attempt.get(name, usage.get(name, 0)) or 0)
                for name in ("prompt_tokens", "completion_tokens")
            )
        tokens += max(0, int(total))
        cost_value = attempt.get("cost_estimate")
        if not isinstance(cost_value, (int, float)) or isinstance(cost_value, bool):
            cost_value = usage.get("cost_estimate", 0.0)
        if isinstance(cost_value, (int, float)) and not isinstance(cost_value, bool):
            cost += max(0.0, float(cost_value))
    return provider_attempts, tokens, cost


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
