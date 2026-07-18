"""Audited report writers for paper experiment evidence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_metrics import (
    PaperMetricsResult,
    recompute_exp1_pilot_metrics,
)
from tokenshare.experiments.paper_models import JsonObject, digest_json
from tokenshare.storage.artifacts import ArtifactStore


TASK_METRIC_FIELDS = (
    "condition_id",
    "run_id",
    "case_id",
    "task_id",
    "domain",
    "difficulty",
    "paper_difficulty",
    "topic_family",
    "execution_status",
    "root_status",
    "attempted",
    "completed",
    "accepted_validity",
    "failure_stage",
    "failure_kind",
    "attempt_count",
    "provider_attempt_count",
    "parser_failure_count",
    "verifier_rejection_count",
    "checker_rejection_count",
    "provider_error_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "provider_latency_ms",
    "wall_clock_ms",
    "cost_estimate",
    "artifact_ref_count",
    "paper_eligible",
    "pilot_only",
)
SUMMARY_FIELDS = (
    "summary_scope",
    "domain",
    "paper_difficulty",
    "planned_root_count",
    "attempted_root_count",
    "completed_root_count",
    "failed_root_count",
    "blocked_root_count",
    "accepted_valid_root_count",
    "attempt_count",
    "provider_attempt_count",
    "parser_failure_count",
    "verifier_rejection_count",
    "checker_rejection_count",
    "provider_error_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "provider_latency_ms",
    "wall_clock_ms",
    "cost_estimate",
    "completion_rate",
    "accepted_validity_rate",
    "failure_stage_counts",
    "paper_eligible",
    "pilot_only",
)
FEASIBILITY_FIELDS = (
    "suite_id",
    "pilot_only",
    "domain",
    "paper_difficulty",
    "attempted_root_count",
    "completed_root_count",
    "blocked_root_count",
    "completion_rate",
    "accepted_valid_root_count",
    "accepted_validity_rate",
    "provider_attempt_count",
    "total_tokens",
    "provider_latency_ms",
    "cost_estimate",
    "parser_failure_count",
    "verifier_rejection_count",
    "checker_rejection_count",
    "provider_error_count",
    "failure_stage_counts",
)
_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(rb"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]{12,}", re.I),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


@dataclass(frozen=True, kw_only=True)
class PaperReportResult:
    suite_id: str
    paper_eligible: bool
    pilot_only: bool
    output_paths: tuple[str, ...]
    feasibility_rows_exported: int
    schema_version: str = "tokenshare.paper_report_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "paper_eligible": self.paper_eligible,
            "pilot_only": self.pilot_only,
            "output_paths": list(self.output_paths),
            "feasibility_rows_exported": self.feasibility_rows_exported,
        }


def write_exp1_pilot_report(
    suite_root: str | Path,
    *,
    secret_values: Iterable[str] = (),
    allow_audit_limitation: bool = False,
) -> PaperReportResult:
    """先完成 evidence/secret 审计，再原子写 pilot-only 输出。"""

    root = Path(suite_root)
    secret_scan = _scan_suite_for_secrets(root, secret_values=secret_values)
    if not secret_scan["passed"]:
        raise ValueError("paper report secret scan failed")
    if int(secret_scan["secret_checked_count"]) > 0:
        _write_suite_secret_scan_attestation(root, secret_scan=secret_scan)
    try:
        metrics = recompute_exp1_pilot_metrics(root)
    except ValueError as exc:
        if not allow_audit_limitation:
            raise
        return _write_audit_limited_report(
            root,
            validation_error=exc,
            secret_scan=secret_scan,
        )
    if (
        int(secret_scan["secret_checked_count"]) == 0
        and metrics.audit.get("suite_secret_scan_attestation_valid") is True
    ):
        secret_scan = _json_copy(metrics.audit["attested_secret_scan"])
    feasibility_rows = _feasibility_rows(metrics)

    metrics_path = root / "metrics" / "metrics.json"
    task_metrics_path = root / "metrics" / "task_metrics.csv"
    summary_path = root / "metrics" / "summary.csv"
    audit_path = root / "audit" / "audit_summary.json"
    report_path = root / "report.md"
    feasibility_path = root / "metrics" / "exp1_pilot_feasibility.csv"
    output_paths = (
        metrics_path,
        task_metrics_path,
        summary_path,
        audit_path,
        report_path,
        feasibility_path,
    )

    audit = {
        "schema_version": "tokenshare.paper_audit_summary.v1",
        "suite_id": metrics.suite_id,
        "pilot_only": metrics.pilot_only,
        "paper_eligible": metrics.paper_eligible,
        "ineligibility_reasons": list(metrics.ineligibility_reasons),
        **metrics.audit,
        "secret_scan": secret_scan,
        "feasibility_rows_exported": len(feasibility_rows),
        "formal_paper_table_generated": False,
    }
    _atomic_write_json(metrics_path, metrics.to_dict())
    _atomic_write_text(
        task_metrics_path,
        _csv_text(metrics.task_metrics, fieldnames=TASK_METRIC_FIELDS),
    )
    _atomic_write_text(
        summary_path,
        _csv_text(metrics.summary_rows, fieldnames=SUMMARY_FIELDS),
    )
    _atomic_write_json(audit_path, audit)
    _atomic_write_text(report_path, _report_markdown(metrics, audit))
    _atomic_write_text(
        feasibility_path,
        _csv_text(feasibility_rows, fieldnames=FEASIBILITY_FIELDS),
    )
    return PaperReportResult(
        suite_id=metrics.suite_id,
        paper_eligible=metrics.paper_eligible,
        pilot_only=metrics.pilot_only,
        output_paths=tuple(path.as_posix() for path in output_paths),
        feasibility_rows_exported=len(feasibility_rows),
    )


def _write_audit_limited_report(
    root: Path,
    *,
    validation_error: ValueError,
    secret_scan: JsonObject,
) -> PaperReportResult:
    """证据无法通过硬门禁时只写明确的 ineligible 诊断报告。"""

    suite = json.loads((root / "suite_manifest.json").read_text(encoding="utf-8"))
    suite_id = str(suite.get("suite_id") or root.name)
    pilot_only = suite.get("pilot_only") is True
    limitation = str(validation_error)
    diagnostics = _audit_limited_timing_diagnostics(root)
    metrics_body: JsonObject = {
        "schema_version": "tokenshare.paper_metrics_audit_limited.v1",
        "suite_id": suite_id,
        "pilot_only": pilot_only,
        "paper_eligible": False,
        "calculation_status": "audit_limited",
        "ineligibility_reasons": ["evidence_validation_failed"],
        "audit_limitations": [limitation],
        "diagnostic_totals": diagnostics,
        "task_metrics": [],
        "summary_rows": [],
    }
    audit: JsonObject = {
        "schema_version": "tokenshare.paper_audit_summary.v1",
        "suite_id": suite_id,
        "pilot_only": pilot_only,
        "paper_eligible": False,
        "evidence_validation_status": "failed",
        "ineligibility_reasons": ["evidence_validation_failed"],
        "audit_limitations": [limitation],
        "secret_scan": _json_copy(secret_scan),
        **_json_copy(diagnostics),
        "provider_calls_made_for_report": 0,
        "feasibility_rows_exported": 0,
        "formal_paper_table_generated": False,
    }
    metrics_path = root / "metrics" / "metrics.json"
    task_metrics_path = root / "metrics" / "task_metrics.csv"
    summary_path = root / "metrics" / "summary.csv"
    audit_path = root / "audit" / "audit_summary.json"
    report_path = root / "report.md"
    feasibility_path = root / "metrics" / "exp1_pilot_feasibility.csv"
    output_paths = (
        metrics_path,
        task_metrics_path,
        summary_path,
        audit_path,
        report_path,
        feasibility_path,
    )
    _atomic_write_json(metrics_path, metrics_body)
    _atomic_write_text(task_metrics_path, _csv_text((), fieldnames=TASK_METRIC_FIELDS))
    _atomic_write_text(summary_path, _csv_text((), fieldnames=SUMMARY_FIELDS))
    _atomic_write_json(audit_path, audit)
    _atomic_write_text(
        report_path,
        _audit_limited_report_markdown(
            suite_id=suite_id,
            pilot_only=pilot_only,
            limitation=limitation,
            diagnostics=diagnostics,
        ),
    )
    _atomic_write_text(
        feasibility_path,
        _csv_text((), fieldnames=FEASIBILITY_FIELDS),
    )
    return PaperReportResult(
        suite_id=suite_id,
        paper_eligible=False,
        pilot_only=pilot_only,
        output_paths=tuple(path.as_posix() for path in output_paths),
        feasibility_rows_exported=0,
    )


def scan_artifact_store_for_secrets(
    store: ArtifactStore,
    *,
    secret_values: Iterable[str],
) -> JsonObject:
    """扫描已落盘 artifact；报告只记录计数和 artifact id，不保存 secret。"""

    exact_values = _encoded_secret_values(secret_values)
    scanned_ids: list[str] = []
    leaked_ids: list[str] = []
    integrity_failure_ids: list[str] = []
    for manifest_path in sorted(store.artifact_dir.glob("*.manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact_id = str(manifest["artifact_id"])
            artifact_ref = ArtifactRef.from_dict(manifest)
            if not store.verify(artifact_ref):
                integrity_failure_ids.append(artifact_id)
                continue
            data = store.read_bytes(artifact_ref)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            integrity_failure_ids.append(manifest_path.name)
            continue
        scanned_ids.append(artifact_id)
        if _contains_secret(data, exact_values=exact_values):
            leaked_ids.append(artifact_id)
    passed = not leaked_ids and not integrity_failure_ids and bool(exact_values)
    return {
        "schema_version": "tokenshare.paper_secret_scan_report.v1",
        "status": "passed" if passed else "failed",
        "leak_count": len(leaked_ids),
        "secret_checked_count": len(exact_values),
        "scanned_artifact_ids": scanned_ids,
        "leaked_artifact_ids": leaked_ids,
        "integrity_failure_count": len(integrity_failure_ids),
        "integrity_failure_ids": integrity_failure_ids,
        "scan_scope": "artifact_store_exact_secret_and_high_confidence_patterns",
    }


def _feasibility_rows(metrics: PaperMetricsResult) -> list[JsonObject]:
    # 非真实、被篡改或不完整 evidence 不得进入论文导出；pilot 也永远不是主表。
    if not metrics.paper_eligible or not metrics.pilot_only:
        return []
    rows: list[JsonObject] = []
    for summary in metrics.summary_rows:
        if summary["summary_scope"] != "domain_paper_difficulty":
            continue
        rows.append(
            {
                "suite_id": metrics.suite_id,
                "pilot_only": True,
                **{
                    field_name: _json_copy(summary.get(field_name))
                    for field_name in FEASIBILITY_FIELDS
                    if field_name not in {"suite_id", "pilot_only"}
                },
            }
        )
    return rows


def _scan_suite_for_secrets(
    root: Path, *, secret_values: Iterable[str]
) -> JsonObject:
    exact_values = _encoded_secret_values(secret_values)
    scanned = 0
    leak_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        scanned += 1
        if _contains_secret(data, exact_values=exact_values):
            leak_count += 1
    return {
        "schema_version": "tokenshare.paper_secret_scan.v1",
        "passed": leak_count == 0,
        "files_scanned": scanned,
        "secret_checked_count": len(exact_values),
        "leak_count": leak_count,
    }


def _write_suite_secret_scan_attestation(
    root: Path,
    *,
    secret_scan: JsonObject,
) -> None:
    runs = _read_jsonl(root / "run_results.jsonl")
    attested_run_ids = [
        str(run["run_id"])
        for run in runs
        if _run_has_only_adapter_scan_placeholder(run)
    ]
    if not attested_run_ids:
        return
    evidence_manifest = json.loads(
        (root / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    suite = json.loads((root / "suite_manifest.json").read_text(encoding="utf-8"))
    body: JsonObject = {
        "schema_version": "tokenshare.paper_suite_secret_scan_attestation.v1",
        "suite_id": suite["suite_id"],
        "source_evidence_manifest_digest": evidence_manifest[
            "evidence_manifest_digest"
        ],
        "attested_run_ids": attested_run_ids,
        "superseded_adapter_ineligibility_reasons": [
            "secret_scan_failed",
            "adapter_task_not_paper_eligible",
            "adapter_eligibility_report_not_paper_eligible",
        ],
        "secret_scan": _json_copy(secret_scan),
        "provider_calls_made_for_attestation": 0,
    }
    body["attestation_digest"] = digest_json(body)
    _atomic_write_json(root / "audit" / "suite_secret_scan_attestation.json", body)


def _run_has_only_adapter_scan_placeholder(run: JsonObject) -> bool:
    expected = {
        "secret_scan_failed",
        "adapter_task_not_paper_eligible",
        "adapter_eligibility_report_not_paper_eligible",
    }
    transport = run.get("transport_evidence")
    return (
        run.get("execution_status") == "executable"
        and run.get("paper_eligible") is False
        and isinstance(transport, dict)
        and transport.get("real_transport") is True
        and transport.get("transport_kind") == "ai_api"
        and set(run.get("ineligibility_reasons", ())) == expected
    )


def _read_jsonl(path: Path) -> list[JsonObject]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _encoded_secret_values(secret_values: Iterable[str]) -> tuple[bytes, ...]:
    return tuple(
        dict.fromkeys(
            value.encode("utf-8")
            for value in secret_values
            if isinstance(value, str) and len(value) >= 8
        )
    )


def _contains_secret(data: bytes, *, exact_values: tuple[bytes, ...]) -> bool:
    return any(value in data for value in exact_values) or any(
        pattern.search(data) for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS
    )


def _audit_limited_timing_diagnostics(root: Path) -> JsonObject:
    attempts = _read_jsonl(root / "per_attempt_results.jsonl")
    events = _read_jsonl(root / "events" / "event_log.jsonl")
    suite = json.loads((root / "suite_manifest.json").read_text(encoding="utf-8"))
    provider_latency_ms = 0
    for attempt in attempts:
        value = attempt.get("latency_ms")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("paper audit-limited attempt latency is invalid")
        provider_latency_ms += value

    starts_by_run: dict[str, list[JsonObject]] = {}
    terminals_by_run: dict[str, list[JsonObject]] = {}
    for event in events:
        run_id = event.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        if event.get("event_type") == "task_started":
            starts_by_run.setdefault(run_id, []).append(event)
        elif event.get("event_type") in {
            "task_completed",
            "task_blocked",
            "task_budget_exhausted",
        }:
            terminals_by_run.setdefault(run_id, []).append(event)

    wall_clock_ms = 0
    timed_task_count = 0
    for run_id, starts in starts_by_run.items():
        terminals = terminals_by_run.get(run_id, [])
        if len(starts) != 1 or len(terminals) != 1:
            continue
        started_at = _diagnostic_event_timestamp(starts[0])
        terminal_at = _diagnostic_event_timestamp(terminals[0])
        if started_at is None or terminal_at is None or terminal_at < started_at:
            continue
        delta = terminal_at - started_at
        total_microseconds = (
            (delta.days * 86400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        wall_clock_ms += (total_microseconds + 500) // 1000
        timed_task_count += 1
    return {
        "diagnostic_only": True,
        "historical_provider_attempt_count": len(attempts),
        "current_provider_calls_made": int(suite.get("provider_calls_made", -1)),
        "replayed_run_count": int(suite.get("replayed_run_count", -1)),
        "provider_latency_ms": provider_latency_ms,
        "wall_clock_ms": wall_clock_ms,
        "wall_clock_timed_task_count": timed_task_count,
        "wall_clock_source": "task_started_to_terminal_event_recorded_at",
        "provider_latency_source": "sum_of_attempt_latency_ms",
    }


def _diagnostic_event_timestamp(event: JsonObject) -> datetime | None:
    value = event.get("recorded_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _audit_limited_report_markdown(
    *,
    suite_id: str,
    pilot_only: bool,
    limitation: str,
    diagnostics: JsonObject,
) -> str:
    return "\n".join(
        [
            "# Exp1 Minimal Real Pilot Audit Limitation Report",
            "",
            "本报告是 pilot-only 离线复核结果。严格 evidence 门禁未通过，因此不得进入论文主表。",
            "",
            f"- Suite: `{suite_id}`",
            f"- Pilot only: `{str(pilot_only).lower()}`",
            "- Paper eligible evidence: `false`",
            "- Evidence validation: `failed`",
            "- Report regeneration provider calls: `0`",
            f"- Historical provider attempts: `{diagnostics['historical_provider_attempt_count']}`",
            f"- Current/replay provider calls: `{diagnostics['current_provider_calls_made']}`",
            f"- Provider latency sum (diagnostic): `{diagnostics['provider_latency_ms']}` ms",
            f"- Event-derived task wall-clock sum (diagnostic): `{diagnostics['wall_clock_ms']}` ms",
            "",
            "## audit limitation",
            "",
            f"- `{limitation}`",
            "",
            "诊断时间只用于说明现有 raw event/attempt evidence；由于字段绑定失败，不生成 feasibility 行。",
        ]
    ) + "\n"


def _report_markdown(metrics: PaperMetricsResult, audit: JsonObject) -> str:
    overall = next(
        row for row in metrics.summary_rows if row["summary_scope"] == "overall"
    )
    lines = [
        "# Exp1 Minimal Real Pilot Report",
        "",
        "本报告是 pilot-only 可行性审计，不得进入论文主表，也不代表正式 Experiment 1。",
        "",
        f"- Suite: `{metrics.suite_id}`",
        f"- Paper eligible evidence: `{str(metrics.paper_eligible).lower()}`",
        f"- Planned roots: `{overall['planned_root_count']}`",
        f"- Attempted roots: `{overall['attempted_root_count']}`",
        f"- Completed roots: `{overall['completed_root_count']}`",
        f"- Structured blocked roots: `{overall['blocked_root_count']}`",
        f"- Provider attempts: `{overall['provider_attempt_count']}`",
        f"- Total tokens: `{overall['total_tokens']}`",
        f"- Cost estimate: `{overall['cost_estimate']}`",
        "",
        "## Domain / difficulty summary",
        "",
        "| Domain | Paper difficulty | Completion | Accepted validity | Tokens | Cost |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in metrics.summary_rows:
        if row["summary_scope"] != "domain_paper_difficulty":
            continue
        lines.append(
            "| {domain} | {difficulty} | {completed}/{attempted} | "
            "{accepted}/{attempted} | {tokens} | {cost} |".format(
                domain=row["domain"],
                difficulty=row["paper_difficulty"],
                completed=row["completed_root_count"],
                accepted=row["accepted_valid_root_count"],
                attempted=row["attempted_root_count"],
                tokens=row["total_tokens"],
                cost=row["cost_estimate"],
            )
        )
    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"- Evidence manifest: `{str(audit['evidence_manifest_valid']).lower()}`",
            f"- Artifact integrity: `{str(audit['artifact_integrity_valid']).lower()}`",
            f"- Real transport evidence: `{str(audit['real_transport_evidence_valid']).lower()}`",
            f"- Secret scan: `{str(audit['secret_scan']['passed']).lower()}`",
            f"- Feasibility rows exported: `{audit['feasibility_rows_exported']}`",
        ]
    )
    if metrics.ineligibility_reasons:
        lines.extend(["", "## Ineligibility reasons", ""])
        lines.extend(f"- `{reason}`" for reason in metrics.ineligibility_reasons)
    return "\n".join(lines) + "\n"


def _csv_text(
    rows: Iterable[JsonObject], *, fieldnames: tuple[str, ...]
) -> str:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field_name: _csv_value(row.get(field_name))
                for field_name in fieldnames
            }
        )
    return handle.getvalue()


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _atomic_write_json(path: Path, body: JsonObject) -> None:
    _atomic_write_text(
        path,
        json.dumps(
            body,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    temporary.replace(path)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
