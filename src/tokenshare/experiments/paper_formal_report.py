"""Audit-first report generation for formal paper experiment evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable

from tokenshare.experiments.paper_formal_metrics import FormalMetricsResult


@dataclass(frozen=True, kw_only=True)
class FormalReportResult:
    paper_eligible: bool
    regression_only: bool
    formal_paper_table_generated: bool
    report_ref: dict[str, Any]
    eligibility_report_ref: dict[str, Any]
    secret_scan_report_ref: dict[str, Any]
    schema_version: str = "tokenshare.paper_formal_report.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "paper_eligible": self.paper_eligible,
            "regression_only": self.regression_only,
            "formal_paper_table_generated": self.formal_paper_table_generated,
            "report_ref": dict(self.report_ref),
            "eligibility_report_ref": dict(self.eligibility_report_ref),
            "secret_scan_report_ref": dict(self.secret_scan_report_ref),
        }


def generate_paper_formal_report(
    *,
    output_root: str | Path,
    metrics: FormalMetricsResult,
    secret_values: Iterable[str] = (),
) -> FormalReportResult:
    """先扫描已持久化 evidence，再决定生成 regression 或 paper report。"""

    if not isinstance(metrics, FormalMetricsResult):
        raise ValueError("metrics must be FormalMetricsResult")
    root = Path(output_root)
    scan = _scan_formal_output(root, secret_values=secret_values)
    scan_path = root / "audit" / "secret_scan_report.json"
    _write_json(scan_path, scan)

    excluded_statuses = {"blocked", "budget_exhausted", "planned"}
    excluded_rows = [
        row["condition_id"]
        for row in metrics.condition_rows
        if excluded_statuses.intersection(row.get("failure_breakdown", {}))
    ]
    persisted_audit = _audit_persisted_report_evidence(root, metrics=metrics)
    reasons: list[str] = []
    if metrics.capturing:
        reasons.extend(("capturing_transport", "regression_only"))
    if not metrics.paper_eligible:
        reasons.append("suite_paper_ineligible")
    if not scan["passed"]:
        reasons.append("secret_scan_failed")
    if excluded_rows:
        reasons.append("non_formal_condition_status_present")
    reasons.extend(persisted_audit["ineligibility_reasons"])
    paper_eligible = not reasons
    eligibility = {
        "schema_version": "tokenshare.paper_formal_eligibility_report.v1",
        "formal": True,
        "pilot_only": False,
        "execution_scope": "formal_matrix",
        "paper_eligible": paper_eligible,
        "regression_only": not paper_eligible,
        "capturing": metrics.capturing,
        "formal_paper_table_generated": paper_eligible,
        "ineligibility_reasons": sorted(set(reasons)),
        "excluded_condition_ids": excluded_rows,
        "persisted_evidence_audit": persisted_audit,
        "metrics_digest": metrics.metrics_digest,
        "secret_scan_report_ref": _ref(root, scan_path),
    }
    eligibility_path = root / "audit" / "paper_eligibility_report.json"
    _write_json(eligibility_path, eligibility)

    report_path = root / (
        "formal_paper_report.md" if paper_eligible else "formal_regression_report.md"
    )
    if not paper_eligible:
        # 同一输出目录重算后不合格时，不能遗留上一轮的正式报告。
        (root / "formal_paper_report.md").unlink(missing_ok=True)
    markdown = (
        _paper_markdown(metrics)
        if paper_eligible
        else _regression_markdown(metrics, eligibility, scan)
    )
    _write_text(report_path, markdown)
    result = FormalReportResult(
        paper_eligible=paper_eligible,
        regression_only=not paper_eligible,
        formal_paper_table_generated=paper_eligible,
        report_ref=_ref(root, report_path),
        eligibility_report_ref=_ref(root, eligibility_path),
        secret_scan_report_ref=_ref(root, scan_path),
    )
    _write_json(root / "formal_report_result.json", result.to_dict())
    return result


def _audit_persisted_report_evidence(
    root: Path,
    *,
    metrics: FormalMetricsResult,
) -> dict[str, Any]:
    """独立核对 report 所依赖的 condition/task/attempt/evidence inventory。"""

    reasons: list[str] = []
    details: dict[str, Any] = {
        "condition_count": 0,
        "run_count": 0,
        "task_count": 0,
        "attempt_count": 0,
    }
    suite = _read_json_object(root / "suite_manifest.json")
    if suite is None:
        reasons.append("persisted_evidence_incomplete")
        suite = {}
    if (
        suite.get("formal") is not True
        or suite.get("pilot_only") is not False
        or suite.get("execution_scope") != "formal_matrix"
        or suite.get("paper_eligible") is not True
    ):
        reasons.append("persisted_suite_not_paper_eligible")
    if suite.get("capturing") is True or suite.get("regression_only") is True:
        reasons.append("persisted_capturing_or_regression_only")

    metric_rows = tuple(metrics.condition_rows)
    metric_keys = {
        (
            str(row.get("experiment_id", "")),
            str(row.get("condition_id", "")),
            int(row.get("repeat_id", 0)),
        )
        for row in metric_rows
    }
    if not metric_rows or len(metric_keys) != len(metric_rows):
        reasons.append("persisted_condition_inventory_mismatch")
    if any(row.get("paper_eligible") is not True for row in metric_rows):
        reasons.append("condition_not_paper_eligible")
    active_experiment_rows = [
        row
        for rows in metrics.experiment_rows.values()
        for row in rows
    ]
    if not active_experiment_rows or any(
        row.get("paper_eligible") is not True
        for row in active_experiment_rows
    ):
        reasons.append("experiment_not_paper_eligible")

    condition_results = _read_jsonl(root / "condition_results.jsonl")
    condition_keys = {
        (
            str(row.get("experiment_id", "")),
            str(row.get("condition_id", "")),
            int(row.get("repeat_id", 0)),
        )
        for row in condition_results
    }
    details["condition_count"] = len(condition_results)
    if (
        not condition_results
        or condition_keys != metric_keys
        or any(row.get("paper_eligible") is not True for row in condition_results)
    ):
        reasons.append("persisted_condition_not_paper_eligible")

    experiment_ids = {
        str(row.get("experiment_id", "")) for row in metric_rows if row.get("experiment_id")
    }
    for experiment_id in sorted(experiment_ids):
        manifest = _read_json_object(
            root / "experiments" / experiment_id / "experiment_manifest.json"
        )
        if manifest is None or manifest.get("paper_eligible") is not True:
            reasons.append("persisted_experiment_not_paper_eligible")

    observed_run_keys: set[tuple[str, str, int]] = set()
    for experiment_id, condition_id, repeat_id in sorted(metric_keys):
        run_root = (
            root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / str(repeat_id)
        )
        pointer = _read_json_object(run_root / "CURRENT.json")
        generation_id = pointer.get("generation_id") if pointer is not None else None
        if not isinstance(generation_id, str) or not generation_id:
            reasons.append("persisted_evidence_incomplete")
            continue
        generation = run_root / ".generations" / generation_id
        tasks = _read_jsonl(generation / "per_task_results.jsonl")
        attempts = _read_jsonl(generation / "per_attempt_results.jsonl")
        events = _read_jsonl(generation / "events" / "event_log.jsonl")
        artifacts = _read_jsonl(generation / "artifacts" / "artifact_index.jsonl")
        if not tasks or not attempts or not events or not artifacts:
            reasons.append("persisted_evidence_incomplete")
            continue
        observed_run_keys.add((experiment_id, condition_id, repeat_id))
        details["run_count"] += 1
        details["task_count"] += len(tasks)
        details["attempt_count"] += len(attempts)
        task_ids = {
            str(task.get("task_id", "")) for task in tasks if task.get("task_id")
        }
        event_task_ids = {
            str(event.get("task_id", "")) for event in events if event.get("task_id")
        }
        artifact_task_ids = {
            str(artifact.get("task_id", ""))
            for artifact in artifacts
            if artifact.get("task_id")
        }
        if not task_ids or not task_ids.issubset(event_task_ids | artifact_task_ids):
            reasons.append("persisted_evidence_incomplete")
        if any(not _report_task_evidence_complete(task) for task in tasks):
            reasons.append("persisted_task_not_paper_eligible")
        if any(not _report_attempt_evidence_complete(attempt) for attempt in attempts):
            reasons.append("persisted_attempt_not_paper_eligible")
        event_ids = {
            str(event.get("event_id"))
            for event in events
            if isinstance(event.get("event_id"), str) and event.get("event_id")
        }
        if any(
            not _task_refs_resolve(
                task,
                event_ids=event_ids,
                artifacts=artifacts,
            )
            for task in tasks
        ) or any(
            not _attempt_refs_resolve(attempt, artifacts=artifacts)
            for attempt in attempts
        ):
            reasons.append("persisted_evidence_ref_unresolved")
    if observed_run_keys != metric_keys:
        reasons.append("persisted_condition_inventory_mismatch")

    stable_reasons = list(dict.fromkeys(reasons))
    return {
        "passed": not stable_reasons,
        "ineligibility_reasons": stable_reasons,
        **details,
    }


def _report_task_evidence_complete(task: dict[str, Any]) -> bool:
    if task.get("paper_eligible") is not True or task.get("record_scope") != "protocol":
        return False
    event_refs = task.get("event_refs")
    artifact_refs = task.get("evidence_artifact_refs") or task.get("artifact_refs")
    return _nonempty_refs(event_refs, identity_field="event_id") and _nonempty_refs(
        artifact_refs
    )


def _report_attempt_evidence_complete(attempt: dict[str, Any]) -> bool:
    if (
        attempt.get("paper_eligible") is not True
        or attempt.get("record_scope") != "protocol"
    ):
        return False
    required_ids = ("run_id", "task_id", "unit_id", "attempt_id")
    if any(not isinstance(attempt.get(field), str) or not attempt.get(field) for field in required_ids):
        return False
    status = str(attempt.get("attempt_status") or "").lower()
    if status not in {
        "succeeded",
        "model_identity_mismatch",
        "provider_error",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "lease_expired",
        "late_rejected",
        "worker_died",
        "cancelled_by_budget",
    }:
        return False
    required_refs = (
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    )
    if not all(_complete_ref(attempt.get(field)) for field in required_refs):
        return False
    raw_required_statuses = {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    }
    if status in raw_required_statuses and not _complete_ref(
        attempt.get("raw_output_ref")
    ):
        return False
    if status == "parse_failed" and not _complete_ref(
        attempt.get("parse_failure_ref")
    ):
        return False
    return True


def _task_refs_resolve(
    task: dict[str, Any],
    *,
    event_ids: set[str],
    artifacts: list[dict[str, Any]],
) -> bool:
    event_refs = task.get("event_refs")
    artifact_refs = task.get("evidence_artifact_refs") or task.get("artifact_refs")
    if not isinstance(event_refs, (list, tuple)) or not isinstance(
        artifact_refs, (list, tuple)
    ):
        return False
    return all(
        isinstance(ref, dict) and ref.get("event_id") in event_ids
        for ref in event_refs
    ) and all(_artifact_ref_resolves(ref, artifacts) for ref in artifact_refs)


def _attempt_refs_resolve(
    attempt: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]],
) -> bool:
    field_names = [
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ]
    status = str(attempt.get("attempt_status") or "").lower()
    if status in {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    }:
        field_names.append("raw_output_ref")
    if status == "parse_failed":
        field_names.append("parse_failure_ref")
    return all(
        _artifact_ref_resolves(attempt.get(field_name), artifacts)
        for field_name in field_names
    )


def _artifact_ref_resolves(
    value: Any,
    artifacts: list[dict[str, Any]],
) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    for artifact in artifacts:
        if value.get("artifact_id") is not None and (
            artifact.get("artifact_id") != value.get("artifact_id")
        ):
            continue
        if value.get("path") is not None and artifact.get("path") != value.get("path"):
            continue
        if value.get("content_hash") is not None and (
            artifact.get("content_hash") != value.get("content_hash")
        ):
            continue
        if any(value.get(field) is not None for field in ("artifact_id", "path")):
            return True
    return False


def _nonempty_refs(value: Any, *, identity_field: str | None = None) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return False
    if identity_field is not None:
        return all(
            isinstance(item, dict)
            and isinstance(item.get(identity_field), str)
            and bool(item.get(identity_field))
            for item in value
        )
    return all(_complete_ref(item) for item in value)


def _complete_ref(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    if isinstance(value.get("artifact_id"), str) and value.get("artifact_id"):
        return True
    return bool(
        isinstance(value.get("path"), str)
        and value.get("path")
        and isinstance(value.get("content_hash"), str)
        and value.get("content_hash")
    )


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return [value for value in values if isinstance(value, dict)]


def _scan_formal_output(
    root: Path,
    *,
    secret_values: Iterable[str],
) -> dict[str, Any]:
    encoded = tuple(
        value.encode("utf-8")
        for value in secret_values
        if isinstance(value, str) and value
    )
    token_pattern = re.compile(rb"(?:sk|sf)-[A-Za-z0-9_-]{20,}")
    findings: list[dict[str, Any]] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {
            "audit/secret_scan_report.json",
            "audit/paper_eligibility_report.json",
        }:
            continue
        data = path.read_bytes()
        scanned += 1
        exact_match = any(secret in data for secret in encoded)
        pattern_match = token_pattern.search(data) is not None
        if exact_match or pattern_match:
            findings.append(
                {
                    "path": relative,
                    "finding_kind": (
                        "configured_secret_value" if exact_match else "api_key_pattern"
                    ),
                }
            )
    return {
        "schema_version": "tokenshare.paper_formal_secret_scan.v1",
        "passed": not findings,
        "files_scanned": scanned,
        "secret_checked_count": len(encoded),
        "finding_count": len(findings),
        "findings": findings,
    }


def _regression_markdown(
    metrics: FormalMetricsResult,
    eligibility: dict[str, Any],
    scan: dict[str, Any],
) -> str:
    lines = [
        "# Formal Experiment Regression Report",
        "",
        "- Execution scope: formal_matrix",
        "- Transport class: capturing/offline",
        "- Paper-eligible: false",
        "- Formal paper table generated: false",
        f"- Metrics digest: {metrics.metrics_digest}",
        f"- Secret scan passed: {str(scan['passed']).lower()}",
        "- Exp4 rows pair each ablation with FULL by case_id × repeat_id.",
        "- Exp5 endpoint comparisons are provider-confounded model-provider pairs.",
        "- Ineligibility reasons: "
        + ", ".join(eligibility["ineligibility_reasons"]),
        "",
        "## Experiment Conditions",
        "",
    ]
    for experiment_id, rows in metrics.experiment_rows.items():
        lines.append(f"- {experiment_id}: {len(rows)} condition rows")
    lines.extend(
        (
            "",
            "This report is regression evidence only and is not a paper result.",
            "",
        )
    )
    return "\n".join(lines)


def _paper_markdown(metrics: FormalMetricsResult) -> str:
    lines = [
        "# Formal Paper Experiment Report",
        "",
        "- Execution scope: formal_matrix",
        "- Paper-eligible: true",
        "- Formal paper table generated: true",
        f"- Metrics digest: {metrics.metrics_digest}",
        "- Exp4 rows pair each ablation with FULL by case_id × repeat_id.",
        "- Exp5 endpoint comparisons are provider-confounded model-provider pairs.",
        "",
        "## Generated Tables",
        "",
        "- metrics/paper_table_feasibility.csv",
        "- metrics/paper_plot_scalability.csv",
        "- metrics/paper_plot_robustness.csv",
        "- metrics/paper_table_ablation.csv",
        "- metrics/paper_table_model_comparison.csv",
        "- metrics/paper_table_model_endpoint_comparison.csv",
        "- metrics/model_execution_records.jsonl",
        "",
    ]
    return "\n".join(lines)


def _ref(root: Path, path: Path) -> dict[str, Any]:
    import hashlib

    return {
        "path": path.relative_to(root).as_posix(),
        "content_hash": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _write_json(path: Path, body: Any) -> None:
    _write_text(
        path,
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
