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
    reasons: list[str] = []
    if metrics.capturing:
        reasons.extend(("capturing_transport", "regression_only"))
    if not metrics.paper_eligible:
        reasons.append("suite_paper_ineligible")
    if not scan["passed"]:
        reasons.append("secret_scan_failed")
    if excluded_rows:
        reasons.append("non_formal_condition_status_present")
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
        "metrics_digest": metrics.metrics_digest,
        "secret_scan_report_ref": _ref(root, scan_path),
    }
    eligibility_path = root / "audit" / "paper_eligibility_report.json"
    _write_json(eligibility_path, eligibility)

    report_path = root / (
        "formal_paper_report.md" if paper_eligible else "formal_regression_report.md"
    )
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
        "",
        "## Generated Tables",
        "",
        "- metrics/paper_table_feasibility.csv",
        "- metrics/paper_plot_scalability.csv",
        "- metrics/paper_plot_robustness.csv",
        "- metrics/paper_table_ablation.csv",
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
