"""Phase 8 本地报告写入。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from tokenshare.experiments.models import ExperimentRun, JsonObject


SUMMARY_COLUMNS = (
    "experiment_id",
    "case_id",
    "plugin_id",
    "plugin_version",
    "executor_profile",
    "simulation_profile",
    "ablation_mode",
    "status",
    "blocker_kind",
    "final_correctness",
    "canonical_pollution_count",
    "completion_rate",
    "settlement_success",
    "real_checker_evidence",
    "ai_api_cost_estimate_total",
    "event_count",
    "artifact_count",
)


def write_run_outputs(
    *,
    output_dir: Path,
    run: ExperimentRun,
    case_report: JsonObject,
    metrics: JsonObject,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "run_manifest.json"
    case_report_path = output_dir / "case_report.json"
    metrics_path = metrics_dir / "metrics.json"
    summary_csv_path = metrics_dir / "paper_summary.csv"
    lifecycle_csv_path = metrics_dir / "lifecycle_coverage.csv"

    _write_json(manifest_path, run.to_dict())
    _write_json(case_report_path, case_report)
    _write_json(metrics_path, metrics)
    _write_summary_csv(summary_csv_path, metrics)
    _write_lifecycle_csv(lifecycle_csv_path, metrics)
    return {
        "manifest": manifest_path,
        "case_report": case_report_path,
        "metrics": metrics_path,
        "summary_csv": summary_csv_path,
        "lifecycle_csv": lifecycle_csv_path,
    }


def _write_json(path: Path, body: JsonObject) -> None:
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_summary_csv(path: Path, metrics: JsonObject) -> None:
    rows = metrics.get("paper_table_rows") or [_summary_row_from_metrics(metrics)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in SUMMARY_COLUMNS})


def _write_lifecycle_csv(path: Path, metrics: JsonObject) -> None:
    coverage = metrics.get("evidence_refs", {}).get("lifecycle_event_coverage", {})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_type", "observed_count", "expected"])
        writer.writeheader()
        for event_type, observed_count in sorted(coverage.items()):
            writer.writerow(
                {
                    "event_type": event_type,
                    "observed_count": observed_count,
                    "expected": "true",
                }
            )


def _summary_row_from_metrics(metrics: JsonObject) -> JsonObject:
    common = metrics.get("common_metrics", {})
    experiment = metrics.get("experiment_metrics", {})
    context = metrics.get("context", {})
    blocked = metrics.get("blocked_reason") or {}
    return {
        "experiment_id": context.get("experiment_id", ""),
        "case_id": context.get("case_id", ""),
        "plugin_id": context.get("plugin_id", ""),
        "plugin_version": context.get("plugin_version", ""),
        "executor_profile": context.get("executor_profile", ""),
        "simulation_profile": context.get("simulation_profile", ""),
        "ablation_mode": context.get("ablation_mode", ""),
        "status": metrics.get("status", ""),
        "blocker_kind": blocked.get("blocker_kind", ""),
        "final_correctness": experiment.get("final_correctness", False),
        "canonical_pollution_count": experiment.get(
            "canonical_pollution_count",
            common.get("canonical_pollution_count", 0),
        ),
        "completion_rate": common.get("completion_rate", 0),
        "settlement_success": common.get("settlement_success", False),
        "real_checker_evidence": experiment.get("real_checker_evidence", False),
        "ai_api_cost_estimate_total": experiment.get("ai_api_cost_estimate_total", 0),
        "event_count": common.get("event_count", 0),
        "artifact_count": common.get("artifact_count", 0),
    }


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)
