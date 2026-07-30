"""Experiment 5 v3 audit and paper artifact renderer.

The renderer consumes parsed metric rows only.  It never opens persisted raw
model output or reasoning artifacts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from xml.sax.saxutils import escape as xml_escape


EXP5_AUDIT_FILES = (
    "metrics/exp5_model_overall.csv",
    "metrics/exp5_model_by_domain_topic.csv",
    "metrics/exp5_paired_comparisons.csv",
    "metrics/exp5_model_execution_records.jsonl",
    "metrics/exp5_order_and_concurrency.csv",
    "metrics/exp5_failure_taxonomy.csv",
)
EXP5_PAPER_FILES = (
    "paper/exp5_model_overall.tex",
    "paper/exp5_model_by_domain_topic.tex",
    "paper/exp5_completion_validity.pdf",
    "paper/exp5_completion_validity.svg",
    "paper/exp5_tokens_latency.pdf",
    "paper/exp5_tokens_latency.svg",
    "paper/exp5_results_summary.md",
    "paper/exp5_failure_appendix.md",
)

_MODEL_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_14b_siliconflow",
    "minimax_m2_5_siliconflow",
    "deepseek_v3_pro_siliconflow",
)
_MODEL_COLORS = ("#2F6BFF", "#F28E2B", "#59A14F", "#B07AA1")
_UNSAFE_PARSED_FIELDS = frozenset(
    {
        "raw_reasoning",
        "reasoning_content",
        "chain_of_thought",
        "raw_output",
        "raw_output_text",
    }
)

_OVERALL_FIELDS = (
    "cohort_member_id",
    "model_label",
    "root_count",
    "completion_count",
    "completion_rate",
    "completion_ci_low",
    "completion_ci_high",
    "accepted_validity_count",
    "accepted_validity_rate",
    "accepted_validity_ci_low",
    "accepted_validity_ci_high",
    "provider_attempt_count",
    "prompt_tokens",
    "reasoning_tokens",
    "visible_output_tokens",
    "total_tokens",
    "total_tokens_median",
    "total_tokens_ci_low",
    "total_tokens_ci_high",
    "cost_estimate",
    "cost_currency",
    "cost_estimate_status",
    "pricing_snapshot_digest",
    "wall_clock_ms",
    "provider_latency_ms",
    "provider_latency_ms_median",
    "provider_latency_ms_ci_low",
    "provider_latency_ms_ci_high",
    "rate_limit_429_count",
    "timeout_count",
    "retry_count",
    "identity_coverage",
    "reasoning_tokens_missing_count",
    "visible_output_tokens_missing_count",
    "cost_estimate_missing_count",
    "paper_eligible",
    "identity_complete",
)
_DOMAIN_FIELDS = (
    "cohort_member_id",
    "model_label",
    "domain",
    "topic_family",
    "root_count",
    "completion_count",
    "completion_rate",
    "accepted_validity_count",
    "accepted_validity_rate",
    "paper_eligible",
    "identity_complete",
)
_PAIRED_FIELDS = (
    "schema_version",
    "metric",
    "metric_kind",
    "stratum_id",
    "domain",
    "topic_family",
    "model_a",
    "model_b",
    "pairing_key",
    "pairing_denominator",
    "paired_sample_size",
    "paired_case_count",
    "model_a_estimate",
    "model_b_estimate",
    "paired_difference",
    "ci_95_low",
    "ci_95_high",
    "confidence_level",
    "method",
    "raw_p_value",
    "holm_adjusted_p_value",
    "p_value_applicability",
    "effect_direction",
    "missing_model_a_count",
    "missing_model_b_count",
    "missing_both_count",
    "missingness_reasons",
    "bootstrap_cluster",
    "bootstrap_seed",
    "bootstrap_resamples",
    "paper_eligible",
    "identity_complete",
)
_EXECUTION_FIELDS = (
    "schema_version",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "run_id",
    "task_id",
    "unit_id",
    "attempt_id",
    "model_policy",
    "model_cohort_id",
    "model_cohort_digest",
    "cohort_member_id",
    "provider_config_id",
    "selected_entry_id",
    "provider_family",
    "configured_model",
    "requested_model",
    "resolved_model",
    "response_model_status",
    "reasoning_profile_id",
    "source_provider_config_digest",
    "prepared_execution_config_digest",
    "model_execution_record_digest",
    "identity_status",
    "worker_id",
    "provider_attempt_index",
    "attempt_status",
    "provider",
    "model",
    "entry_id",
    "request_ref",
    "provenance_ref",
    "raw_output_ref",
    "usage_ref",
    "parsed_output_ref",
    "parse_failure_ref",
    "fault_injection_ref",
    "model_execution_record_ref",
    "request_evidence_joined",
    "provenance_evidence_joined",
    "usage_evidence_joined",
    "started_at",
    "ended_at",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "visible_output_tokens",
    "visible_output_basis",
    "total_tokens",
    "cost_estimate",
    "cost_estimate_currency",
    "cost_estimate_status",
    "provider_errors",
    "error_kind",
    "failure_reasons",
    "transport_kind",
    "pilot_only",
    "attempt_paper_eligible",
    "task_paper_eligible",
    "identity_complete",
    "paper_eligible",
)
_EXECUTION_REF_FIELDS = frozenset(
    {
        "request_ref",
        "provenance_ref",
        "raw_output_ref",
        "usage_ref",
        "parsed_output_ref",
        "parse_failure_ref",
        "fault_injection_ref",
        "model_execution_record_ref",
    }
)
_EXECUTION_STRING_LIST_FIELDS = frozenset(
    {
        "provider_errors",
        "failure_reasons",
    }
)
_SAFE_REFERENCE_FIELDS = frozenset(
    {
        "artifact_id",
        "path",
        "uri",
        "content_hash",
        "schema_version",
        "media_type",
        "byte_size",
        "record_digest",
        "digest",
    }
)
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+%-]{0,511}\Z")
_SAFE_PATH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:/@%+\\-]{0,1023}\Z")
_SAFE_MEDIA_TYPE_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*\Z"
)
_SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ORDER_FIELDS = (
    "schema_version",
    "row_type",
    "row_scope",
    "analysis_role",
    "repeat_id",
    "cohort_member_id",
    "order_slot",
    "predecessor_member_id",
    "arm_started_at",
    "arm_ended_at",
    "previous_arm_ended_at",
    "arm_overlap",
    "condition_count",
    "condition_ids",
    "global_peak_in_flight",
    "global_in_flight_limit",
    "global_capacity_compliant",
    "repeat_ids",
    "case_repeat_denominator",
    "root_completion_sample_size",
    "root_completion_missing_count",
    "root_completion_rate",
    "accepted_validity_sample_size",
    "accepted_validity_missing_count",
    "completion_rate",
    "accepted_validity_rate",
    "total_tokens_sample_size",
    "total_tokens_missing_count",
    "total_tokens_median",
    "provider_latency_ms_sample_size",
    "provider_latency_ms_missing_count",
    "provider_latency_ms_median",
    "condition_windows_overlap",
    "observed_peak_concurrency",
    "paper_eligible",
    "identity_complete",
)
_PAIRED_SCHEMA_VERSION = "tokenshare.paper_exp5_paired_comparison.v1"
_ORDER_SCHEMA_VERSIONS = frozenset(
    {
        "tokenshare.paper_exp5_order_audit.v1",
        "tokenshare.paper_exp5_order_sensitivity.v1",
    }
)
_EXECUTION_SCHEMA_VERSION = "tokenshare.paper_exp5_model_execution_row.v1"
_FAILURE_FIELDS = (
    "cohort_member_id",
    "domain",
    "topic_family",
    "failure_stage",
    "failure_kind",
    "count",
    "evidence_ref",
    "short_summary",
)


class PdfBackendUnavailable(RuntimeError):
    """Raised when the configured PDF renderer cannot produce an artifact."""


@dataclass(frozen=True, kw_only=True)
class PlotPoint:
    cohort_member_id: str
    model_label: str
    value: float
    ci_low: float
    ci_high: float
    sample_size: int


@dataclass(frozen=True, kw_only=True)
class PlotSeries:
    series_id: str
    label: str
    axis_label: str
    points: tuple[PlotPoint, ...]
    fixed_maximum: float | None = None


@dataclass(frozen=True, kw_only=True)
class PlotSpec:
    artifact_id: str
    title: str
    series: tuple[PlotSeries, ...]
    width: int = 900
    height: int = 520


@dataclass(frozen=True, kw_only=True)
class Exp5PaperArtifactResult:
    status: str
    paper_eligible: bool
    artifact_refs: tuple[dict[str, str], ...]
    failure_reason: str | None = None
    schema_version: str = "tokenshare.paper_exp5_artifact_result.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "paper_eligible": self.paper_eligible,
            "artifact_refs": [dict(ref) for ref in self.artifact_refs],
            "failure_reason": self.failure_reason,
        }


PdfBackend = Callable[[Path, PlotSpec], None]


def render_exp5_paper_artifacts(
    *,
    output_root: str | Path,
    suite_status: str,
    paper_eligible: bool,
    identity_complete: bool,
    overall_rows: Sequence[Mapping[str, Any]],
    domain_topic_rows: Sequence[Mapping[str, Any]],
    paired_comparison_rows: Sequence[Mapping[str, Any]],
    model_execution_rows: Sequence[Mapping[str, Any]],
    order_concurrency_rows: Sequence[Mapping[str, Any]],
    failure_taxonomy_rows: Sequence[Mapping[str, Any]],
    pdf_backend: PdfBackend | None = None,
) -> Exp5PaperArtifactResult:
    """Write audit outputs, then render paper artifacts when all gates pass."""

    root = Path(output_root)
    try:
        return _render_exp5_paper_artifacts(
            root=root,
            suite_status=suite_status,
            paper_eligible=paper_eligible,
            identity_complete=identity_complete,
            overall_rows=overall_rows,
            domain_topic_rows=domain_topic_rows,
            paired_comparison_rows=paired_comparison_rows,
            model_execution_rows=model_execution_rows,
            order_concurrency_rows=order_concurrency_rows,
            failure_taxonomy_rows=failure_taxonomy_rows,
            pdf_backend=pdf_backend,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        _clear_exp5_paper_artifacts(root)
        return Exp5PaperArtifactResult(
            status="paper_artifact_render_failed",
            paper_eligible=False,
            artifact_refs=_existing_artifact_refs(root, EXP5_AUDIT_FILES),
            failure_reason=str(exc),
        )


def _render_exp5_paper_artifacts(
    *,
    root: Path,
    suite_status: str,
    paper_eligible: bool,
    identity_complete: bool,
    overall_rows: Sequence[Mapping[str, Any]],
    domain_topic_rows: Sequence[Mapping[str, Any]],
    paired_comparison_rows: Sequence[Mapping[str, Any]],
    model_execution_rows: Sequence[Mapping[str, Any]],
    order_concurrency_rows: Sequence[Mapping[str, Any]],
    failure_taxonomy_rows: Sequence[Mapping[str, Any]],
    pdf_backend: PdfBackend | None,
) -> Exp5PaperArtifactResult:
    normalized = {
        "overall": _parsed_rows(overall_rows, "overall_rows"),
        "domain": _parsed_rows(domain_topic_rows, "domain_topic_rows"),
        "paired": _parsed_rows(
            paired_comparison_rows,
            "paired_comparison_rows",
        ),
        "execution": _parsed_rows(model_execution_rows, "model_execution_rows"),
        "order": _parsed_rows(order_concurrency_rows, "order_concurrency_rows"),
        "failure": _parsed_rows(failure_taxonomy_rows, "failure_taxonomy_rows"),
    }
    if (
        suite_status != "completed"
        or paper_eligible is not True
        or identity_complete is not True
    ):
        _write_audit_outputs(root, normalized)
        audit_refs = _artifact_refs(root, EXP5_AUDIT_FILES)
        _clear_exp5_paper_artifacts(root)
        return Exp5PaperArtifactResult(
            status="paper_artifact_ineligible",
            paper_eligible=False,
            artifact_refs=audit_refs,
            failure_reason="formal eligibility or identity gate failed",
        )

    _validate_four_model_inventory(normalized["overall"])
    _write_audit_outputs(root, normalized)
    audit_refs = _artifact_refs(root, EXP5_AUDIT_FILES)

    row_identity_complete = all(
        _row_is_paper_eligible(row, group_name)
        for group_name in ("overall", "domain", "paired", "execution", "order")
        for row in normalized[group_name]
    )
    if not row_identity_complete:
        _clear_exp5_paper_artifacts(root)
        return Exp5PaperArtifactResult(
            status="paper_artifact_ineligible",
            paper_eligible=False,
            artifact_refs=audit_refs,
            failure_reason="formal eligibility or identity gate failed",
        )

    overall = _ordered_overall(normalized["overall"])
    completion_spec = _completion_validity_plot_spec(overall)
    token_spec = _tokens_latency_plot_spec(overall)
    backend = pdf_backend or _pdflatex_pdf_backend
    staging = root / ".exp5-paper-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _write_text(staging / "exp5_model_overall.tex", _overall_tex(overall))
        _write_text(
            staging / "exp5_model_by_domain_topic.tex",
            _domain_topic_tex(normalized["domain"], overall),
        )
        _write_text(
            staging / "exp5_completion_validity.svg",
            _svg_plot(completion_spec),
        )
        _write_text(
            staging / "exp5_tokens_latency.svg",
            _svg_plot(token_spec),
        )
        _write_text(
            staging / "exp5_results_summary.md",
            _results_summary(
                overall,
                normalized["paired"],
                normalized["order"],
            ),
        )
        _write_text(
            staging / "exp5_failure_appendix.md",
            _failure_appendix(overall, normalized["failure"]),
        )
        backend(staging / "exp5_completion_validity.pdf", completion_spec)
        backend(staging / "exp5_tokens_latency.pdf", token_spec)
        for pdf_path in (
            staging / "exp5_completion_validity.pdf",
            staging / "exp5_tokens_latency.pdf",
        ):
            if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
                raise PdfBackendUnavailable("PDF backend produced an empty file")
    except (PdfBackendUnavailable, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        _clear_exp5_paper_artifacts(root)
        return Exp5PaperArtifactResult(
            status="paper_artifact_render_failed",
            paper_eligible=False,
            artifact_refs=audit_refs,
            failure_reason=str(exc),
        )

    paper_root = root / "paper"
    paper_root.mkdir(parents=True, exist_ok=True)
    for relative_path in EXP5_PAPER_FILES:
        name = Path(relative_path).name
        (staging / name).replace(paper_root / name)
    staging.rmdir()
    return Exp5PaperArtifactResult(
        status="rendered",
        paper_eligible=True,
        artifact_refs=audit_refs + _artifact_refs(root, EXP5_PAPER_FILES),
    )


def _row_is_paper_eligible(row: Mapping[str, Any], group_name: str) -> bool:
    if row.get("paper_eligible") is False or row.get("identity_complete") is False:
        return False
    schema_version = row.get("schema_version")
    if group_name == "paired":
        if schema_version is not None:
            return schema_version == _PAIRED_SCHEMA_VERSION
    elif group_name == "order":
        if schema_version is not None:
            return schema_version in _ORDER_SCHEMA_VERSIONS
    elif group_name == "execution":
        if schema_version is not None and schema_version != _EXECUTION_SCHEMA_VERSION:
            return False
        if schema_version == _EXECUTION_SCHEMA_VERSION:
            return row.get("identity_status") == "matched"
    if row.get("paper_eligible") is not True:
        return False
    if row.get("identity_complete") is True:
        return True
    return False


def _parsed_rows(
    values: Sequence[Mapping[str, Any]],
    field_name: str,
) -> tuple[dict[str, Any], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field_name} must be parsed rows")
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must contain objects")
        _validate_no_unsafe_content(value, field_name)
        rows.append(dict(value))
    return tuple(rows)


def _validate_no_unsafe_content(value: Any, field_path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_path} contains a non-string field name")
            normalized = key.strip().lower().replace("-", "_")
            safe_reference_key = normalized.endswith("_ref")
            unsafe_payload_key = (
                normalized in _UNSAFE_PARSED_FIELDS
                or "chain_of_thought" in normalized
                or (
                    not safe_reference_key
                    and any(
                        marker in normalized
                        for marker in ("raw", "reasoning", "output", "token")
                    )
                    and any(
                        marker in normalized
                        for marker in ("payload", "content", "text", "body")
                    )
                )
            )
            if unsafe_payload_key:
                raise ValueError(
                    f"{field_path}.{key} contains raw reasoning/output payload"
                )
            _validate_no_unsafe_content(nested, f"{field_path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_no_unsafe_content(nested, f"{field_path}[{index}]")


def _validate_four_model_inventory(rows: Sequence[Mapping[str, Any]]) -> None:
    if len(rows) != 4 or {row.get("cohort_member_id") for row in rows} != set(
        _MODEL_IDS
    ):
        raise ValueError("Exp5 paper artifacts require the exact four-model inventory")


def _write_audit_outputs(
    root: Path,
    rows: Mapping[str, tuple[dict[str, Any], ...]],
) -> None:
    _write_text(
        root / EXP5_AUDIT_FILES[0],
        _csv_text(rows["overall"], _OVERALL_FIELDS),
    )
    _write_text(
        root / EXP5_AUDIT_FILES[1],
        _csv_text(rows["domain"], _DOMAIN_FIELDS),
    )
    _write_text(
        root / EXP5_AUDIT_FILES[2],
        _csv_text(rows["paired"], _PAIRED_FIELDS),
    )
    execution_lines = [
        json.dumps(
            _execution_audit_row(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in rows["execution"]
    ]
    _write_text(
        root / EXP5_AUDIT_FILES[3],
        "".join(line + "\n" for line in execution_lines),
    )
    _write_text(
        root / EXP5_AUDIT_FILES[4],
        _csv_text(rows["order"], _ORDER_FIELDS),
    )
    _write_text(
        root / EXP5_AUDIT_FILES[5],
        _csv_text(
            tuple(_failure_audit_row(row) for row in rows["failure"]),
            _FAILURE_FIELDS,
        ),
    )


def _execution_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _EXECUTION_FIELDS:
        value = row.get(field)
        if field == "latency_ms" and value is None:
            value = row.get("provider_latency_ms")
        elif field == "cost_estimate_currency" and value is None:
            value = row.get("cost_currency")
        if field in _EXECUTION_REF_FIELDS:
            _validate_safe_reference(value, field)
        elif field in _EXECUTION_STRING_LIST_FIELDS:
            if value is None:
                value = []
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"{field} must be a list of safe identifiers")
            value = list(value)
        elif isinstance(value, (Mapping, list, tuple)):
            raise ValueError(f"{field} must be a safe scalar")
        result[field] = value
    return result


def _failure_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    evidence_ref = row.get("evidence_ref")
    if isinstance(evidence_ref, Mapping):
        _validate_safe_reference(evidence_ref, "evidence_ref")
    elif evidence_ref is not None and not isinstance(evidence_ref, str):
        raise ValueError("evidence_ref must be a safe reference or path")
    return dict(row)


def _validate_safe_reference(value: Any, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a safe artifact reference or null")
    unknown_fields = set(value).difference(_SAFE_REFERENCE_FIELDS)
    if unknown_fields:
        raise ValueError(
            f"{field_name} contains unsupported reference fields: "
            f"{sorted(unknown_fields)}"
        )
    if not value or not isinstance(value.get("artifact_id"), str):
        raise ValueError(f"{field_name} requires an artifact_id")
    for reference_field, nested in value.items():
        if reference_field == "byte_size":
            if type(nested) is not int or nested < 0:
                raise ValueError(f"{field_name}.byte_size must be non-negative")
            continue
        if not isinstance(nested, str) or not nested:
            raise ValueError(
                f"{field_name}.{reference_field} must be a non-empty string"
            )
        if reference_field in {"content_hash", "record_digest", "digest"}:
            pattern = _SHA256_DIGEST_PATTERN
        elif reference_field in {"path", "uri"}:
            pattern = _SAFE_PATH_PATTERN
        elif reference_field == "media_type":
            pattern = _SAFE_MEDIA_TYPE_PATTERN
        else:
            pattern = _SAFE_IDENTIFIER_PATTERN
        if pattern.fullmatch(nested) is None:
            raise ValueError(
                f"{field_name}.{reference_field} has an invalid safe-scalar format"
            )


def _csv_text(
    rows: Sequence[Mapping[str, Any]],
    preferred_fields: Sequence[str],
) -> str:
    # 审计产物使用冻结字段白名单，避免把未审阅的 provider payload 透传到论文目录。
    fieldnames = list(preferred_fields)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: _audit_cell(row.get(field))
                for field in fieldnames
            }
        )
    return stream.getvalue()


def _audit_cell(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def _ordered_overall(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    by_member = {str(row["cohort_member_id"]): dict(row) for row in rows}
    return tuple(by_member[member_id] for member_id in _MODEL_IDS)


def _completion_validity_plot_spec(
    rows: Sequence[Mapping[str, Any]],
) -> PlotSpec:
    return PlotSpec(
        artifact_id="exp5_completion_validity",
        title="Completion / accepted validity rate",
        series=(
            _series_from_rows(
                rows,
                series_id="completion",
                label="Completion rate",
                axis_label="Rate",
                value_field="completion_rate",
                low_field="completion_ci_low",
                high_field="completion_ci_high",
                fixed_maximum=1.0,
            ),
            _series_from_rows(
                rows,
                series_id="accepted_validity",
                label="Accepted validity rate",
                axis_label="Rate",
                value_field="accepted_validity_rate",
                low_field="accepted_validity_ci_low",
                high_field="accepted_validity_ci_high",
                fixed_maximum=1.0,
            ),
        ),
    )


def _tokens_latency_plot_spec(
    rows: Sequence[Mapping[str, Any]],
) -> PlotSpec:
    return PlotSpec(
        artifact_id="exp5_tokens_latency",
        title="Token and provider latency comparison",
        series=(
            _series_from_rows(
                rows,
                series_id="tokens",
                label="Median total tokens",
                axis_label="Median total tokens",
                value_field="total_tokens_median",
                low_field="total_tokens_ci_low",
                high_field="total_tokens_ci_high",
            ),
            _series_from_rows(
                rows,
                series_id="latency",
                label="Median provider latency",
                axis_label="Provider latency (ms)",
                value_field="provider_latency_ms_median",
                low_field="provider_latency_ms_ci_low",
                high_field="provider_latency_ms_ci_high",
            ),
        ),
    )


def _series_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    series_id: str,
    label: str,
    axis_label: str,
    value_field: str,
    low_field: str,
    high_field: str,
    fixed_maximum: float | None = None,
) -> PlotSeries:
    points: list[PlotPoint] = []
    for row in rows:
        points.append(
            PlotPoint(
                cohort_member_id=str(row["cohort_member_id"]),
                model_label=str(row.get("model_label") or row["cohort_member_id"]),
                value=_number(row.get(value_field), value_field),
                ci_low=_number(row.get(low_field), low_field),
                ci_high=_number(row.get(high_field), high_field),
                sample_size=_exact_int(row.get("root_count"), "root_count"),
            )
        )
    return PlotSeries(
        series_id=series_id,
        label=label,
        axis_label=axis_label,
        points=tuple(points),
        fixed_maximum=fixed_maximum,
    )


def _svg_plot(spec: PlotSpec) -> str:
    left = 110.0
    right = 790.0
    top = 80.0
    bottom = 405.0
    plot_height = bottom - top
    x_step = (right - left) / len(_MODEL_IDS)
    axes = tuple(dict.fromkeys(series.axis_label for series in spec.series))
    maximum_by_axis = _plot_maximum_by_axis(spec)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{spec.width}" '
            f'height="{spec.height}" viewBox="0 0 {spec.width} {spec.height}">'
        ),
        '<rect width="900" height="520" fill="#ffffff"/>',
        (
            f'<text x="450" y="34" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="20" font-weight="700">'
            f'{xml_escape(spec.title)}</text>'
        ),
        '<text x="450" y="55" text-anchor="middle" font-family="Arial, sans-serif" '
        'font-size="12">Whiskers show 95% CI; n is the eligible root denominator.</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#222"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#222"/>',
    ]
    for tick in range(6):
        y = bottom - plot_height * tick / 5
        lines.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" '
            'stroke="#e0e0e0"/>'
        )
    for axis_index, axis in enumerate(axes):
        x = 25 if axis_index == 0 else 875
        rotation = -90 if axis_index == 0 else 90
        lines.append(
            f'<text x="{x}" y="245" transform="rotate({rotation} {x} 245)" '
            'text-anchor="middle" font-family="Arial, sans-serif" font-size="13">'
            f'{xml_escape(axis)}</text>'
        )
        label_x = left - 8 if axis_index == 0 else right + 8
        anchor = "end" if axis_index == 0 else "start"
        maximum = maximum_by_axis[axis]
        for tick in range(6):
            y = bottom - plot_height * tick / 5
            tick_value = maximum * tick / 5
            lines.append(
                f'<text x="{label_x:.2f}" y="{y + 4:.2f}" text-anchor="{anchor}" '
                f'data-tick-axis="{xml_escape(axis)}" font-family="Arial, sans-serif" '
                f'font-size="10">{_format_tick(tick_value, maximum)}</text>'
            )
    for model_index, member_id in enumerate(_MODEL_IDS):
        base_x = left + x_step * (model_index + 0.5)
        label = spec.series[0].points[model_index].model_label
        lines.append(
            f'<text x="{base_x:.2f}" y="435" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="11">{xml_escape(label)}</text>'
        )
        lines.append(
            f'<text x="{base_x:.2f}" y="451" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="10">'
            f'n={spec.series[0].points[model_index].sample_size}</text>'
        )
        for series_index, series in enumerate(spec.series):
            point = series.points[model_index]
            maximum = maximum_by_axis[series.axis_label]
            offset = (series_index - (len(spec.series) - 1) / 2) * 18
            x = base_x + offset
            value_y = _plot_y(point.value, maximum, top, bottom)
            low_y = _plot_y(point.ci_low, maximum, top, bottom)
            high_y = _plot_y(point.ci_high, maximum, top, bottom)
            color = _MODEL_COLORS[model_index]
            lines.extend(
                (
                    f'<line x1="{x:.2f}" y1="{low_y:.2f}" x2="{x:.2f}" '
                    f'y2="{high_y:.2f}" stroke="{color}" stroke-width="3"/>',
                    f'<line x1="{x - 5:.2f}" y1="{low_y:.2f}" x2="{x + 5:.2f}" '
                    f'y2="{low_y:.2f}" stroke="{color}"/>',
                    f'<line x1="{x - 5:.2f}" y1="{high_y:.2f}" x2="{x + 5:.2f}" '
                    f'y2="{high_y:.2f}" stroke="{color}"/>',
                    f'<circle cx="{x:.2f}" cy="{value_y:.2f}" r="5" fill="{color}" '
                    f'data-series="{xml_escape(series.series_id)}"/>',
                )
            )
    legend_x = 170
    for index, series in enumerate(spec.series):
        lines.append(
            f'<text x="{legend_x + index * 250}" y="490" '
            f'font-family="Arial, sans-serif" font-size="12">'
            f'{xml_escape(series.label)}</text>'
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _plot_maximum_by_axis(spec: PlotSpec) -> dict[str, float]:
    axes = tuple(dict.fromkeys(series.axis_label for series in spec.series))
    return {
        axis: max(
            (
                series.fixed_maximum
                if series.fixed_maximum is not None
                else max(point.ci_high for point in series.points) * 1.1
            )
            for series in spec.series
            if series.axis_label == axis
        )
        for axis in axes
    }


def _format_tick(value: float, maximum: float) -> str:
    if maximum <= 1.0:
        return f"{value:.3f}"
    if maximum >= 1000:
        return f"{value:,.0f}"
    return f"{value:.0f}"


def _plot_y(value: float, maximum: float, top: float, bottom: float) -> float:
    if maximum <= 0:
        return bottom
    bounded = max(0.0, min(value, maximum))
    return bottom - (bottom - top) * bounded / maximum


def _overall_tex(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        r"\begin{tabular}{lrrrrr}",
        r"\toprule",
        r"Model & $n$ & Completion (95\% CI) & Validity (95\% CI) & Tokens & Cost \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(
            "{} & {} & {} & {} & {} & {} {} \\\\".format(
                _latex_escape(str(row.get("model_label") or row["cohort_member_id"])),
                _exact_int(row.get("root_count"), "root_count"),
                _rate_ci(row, "completion"),
                _rate_ci(row, "accepted_validity"),
                _format_number(row.get("total_tokens")),
                _format_number(row.get("cost_estimate")),
                _latex_escape(str(row.get("cost_currency") or "")),
            )
        )
    lines.extend((r"\bottomrule", r"\end{tabular}", ""))
    return "\n".join(lines)


def _domain_topic_tex(
    rows: Sequence[Mapping[str, Any]],
    overall: Sequence[Mapping[str, Any]],
) -> str:
    labels = {
        str(row["cohort_member_id"]): str(
            row.get("model_label") or row["cohort_member_id"]
        )
        for row in overall
    }
    lines = [
        r"\begin{tabular}{lllrrrr}",
        r"\toprule",
        r"Model & Domain & Topic & $n$ & Completed & Completion & Validity \\",
        r"\midrule",
    ]
    for row in rows:
        member_id = str(row["cohort_member_id"])
        lines.append(
            "{} & {} & {} & {} & {} & {:.3f} & {:.3f} \\\\".format(
                _latex_escape(labels.get(member_id, member_id)),
                _latex_escape(str(row.get("domain") or "")),
                _latex_escape(str(row.get("topic_family") or "--")),
                _exact_int(row.get("root_count"), "root_count"),
                _exact_int(row.get("completion_count"), "completion_count"),
                _number(row.get("completion_rate"), "completion_rate"),
                _number(
                    row.get("accepted_validity_rate"),
                    "accepted_validity_rate",
                ),
            )
        )
    lines.extend((r"\bottomrule", r"\end{tabular}", ""))
    return "\n".join(lines)


def _results_summary(
    overall: Sequence[Mapping[str, Any]],
    paired_rows: Sequence[Mapping[str, Any]],
    order_rows: Sequence[Mapping[str, Any]],
) -> str:
    labels = {
        str(row["cohort_member_id"]): str(
            row.get("model_label") or row["cohort_member_id"]
        )
        for row in overall
    }
    significant = [
        row
        for row in paired_rows
        if _is_primary_paired_metric(row)
        and _optional_number(row.get("holm_adjusted_p_value")) is not None
        and float(row["holm_adjusted_p_value"]) < 0.05
    ]
    lines = [
        "# Experiment 5 Results Summary / 实验 5 结果摘要",
        "",
        "## English",
        "",
        "### Primary outcomes",
        "",
    ]
    for row in overall:
        label = labels[str(row["cohort_member_id"])]
        lines.append(
            f"- {label}: completion {_rate_ci_markdown(row, 'completion')}; "
            f"accepted validity {_rate_ci_markdown(row, 'accepted_validity')} "
            f"(n={_exact_int(row.get('root_count'), 'root_count')})."
        )
    lines.extend(("", "### Paired evidence", ""))
    if significant:
        for row in significant:
            lines.append(
                "- {} vs {}: paired difference {:.3f} [95% CI {:.3f}, {:.3f}], "
                "Holm-adjusted p={:.3f}; direction={}.".format(
                    labels.get(str(row.get("model_a")), str(row.get("model_a"))),
                    labels.get(str(row.get("model_b")), str(row.get("model_b"))),
                    _number(row.get("paired_difference"), "paired_difference"),
                    _number(row.get("ci_95_low"), "ci_95_low"),
                    _number(row.get("ci_95_high"), "ci_95_high"),
                    _number(
                        row.get("holm_adjusted_p_value"),
                        "holm_adjusted_p_value",
                    ),
                    row.get("effect_direction"),
                )
            )
    else:
        lines.append(
            "No primary paired comparison provided sufficient evidence after Holm adjustment."
        )
    lines.extend(("", "### Secondary outcomes and missingness", ""))
    for row in overall:
        label = labels[str(row["cohort_member_id"])]
        lines.append(
            "- {}: median total tokens {}; median provider latency {} ms; cost estimate {} {}; "
            "missingness reasoning={}, visible-output={}, cost={}.".format(
                label,
                _format_number(row.get("total_tokens_median")),
                _format_number(row.get("provider_latency_ms_median")),
                _format_number(row.get("cost_estimate")),
                row.get("cost_currency") or "",
                _exact_int(
                    row.get("reasoning_tokens_missing_count", 0),
                    "reasoning_tokens_missing_count",
                ),
                _exact_int(
                    row.get("visible_output_tokens_missing_count", 0),
                    "visible_output_tokens_missing_count",
                ),
                _exact_int(
                    row.get("cost_estimate_missing_count", 0),
                    "cost_estimate_missing_count",
                ),
            )
        )
    lines.extend(("", "### Order sensitivity and limitation", ""))
    lines.extend(_order_sensitivity_lines(overall, order_rows, language="en"))
    lines.append(
        "This is a model-endpoint comparison with a same-provider serving-profile limitation; "
        "serving configuration, reasoning profile, pricing, and platform resources remain bundled with model identity."
    )
    lines.extend(("", "## 中文", "", "### 主要结果", ""))
    for row in overall:
        label = labels[str(row["cohort_member_id"])]
        lines.append(
            f"- {label}：完成率 {_rate_ci_markdown(row, 'completion')}；"
            f"有效性 {_rate_ci_markdown(row, 'accepted_validity')}"
            f"（n={_exact_int(row.get('root_count'), 'root_count')}）。"
        )
    lines.extend(("", "### 配对证据", ""))
    if significant:
        for row in significant:
            lines.append(
                "- {} 与 {}：配对差异 {:.3f} [95% CI {:.3f}, {:.3f}]，"
                "Holm 校正 p={:.3f}；方向={}.".format(
                    labels.get(str(row.get("model_a")), str(row.get("model_a"))),
                    labels.get(str(row.get("model_b")), str(row.get("model_b"))),
                    _number(row.get("paired_difference"), "paired_difference"),
                    _number(row.get("ci_95_low"), "ci_95_low"),
                    _number(row.get("ci_95_high"), "ci_95_high"),
                    _number(
                        row.get("holm_adjusted_p_value"),
                        "holm_adjusted_p_value",
                    ),
                    row.get("effect_direction"),
                )
            )
    else:
        lines.append("主要配对比较未观察到足够证据（Holm 校正后）。")
    lines.extend(("", "### 次要结果、缺失性与顺序敏感性", ""))
    for row in overall:
        label = labels[str(row["cohort_member_id"])]
        lines.append(
            "- {}：total token 中位数 {}；provider latency 中位数 {} ms；"
            "成本估计 {} {}；missingness reasoning={}、visible-output={}、cost={}。".format(
                label,
                _format_number(row.get("total_tokens_median")),
                _format_number(row.get("provider_latency_ms_median")),
                _format_number(row.get("cost_estimate")),
                row.get("cost_currency") or "",
                _exact_int(row.get("reasoning_tokens_missing_count", 0), "missing"),
                _exact_int(row.get("visible_output_tokens_missing_count", 0), "missing"),
                _exact_int(row.get("cost_estimate_missing_count", 0), "missing"),
            )
        )
    lines.extend(_order_sensitivity_lines(overall, order_rows, language="zh"))
    lines.append(
        "该结果属于同一 provider serving-profile 局限下的 model-endpoint comparison；"
        "serving 配置、reasoning profile、定价与平台资源仍和模型身份共同变化。"
    )
    lines.append("")
    return "\n".join(lines)


def _is_primary_paired_metric(row: Mapping[str, Any]) -> bool:
    schema_version = row.get("schema_version")
    metric = row.get("metric")
    if schema_version == _PAIRED_SCHEMA_VERSION:
        return metric in {"root_completion", "accepted_validity"}
    if schema_version is None:
        return metric in {"root_completion_rate", "accepted_validity_rate"}
    return False


def _order_sensitivity_lines(
    overall: Sequence[Mapping[str, Any]],
    order_rows: Sequence[Mapping[str, Any]],
    *,
    language: str,
) -> list[str]:
    labels = {
        str(row["cohort_member_id"]): str(
            row.get("model_label") or row["cohort_member_id"]
        )
        for row in overall
    }
    lines: list[str] = []
    for member_id in _MODEL_IDS:
        rates = [
            _optional_number(
                row.get("root_completion_rate")
                if row.get("schema_version")
                == "tokenshare.paper_exp5_order_sensitivity.v1"
                else row.get("completion_rate")
            )
            for row in order_rows
            if row.get("cohort_member_id") == member_id
        ]
        available = [value for value in rates if value is not None]
        if not available:
            continue
        if language == "en":
            lines.append(
                f"- Order sensitivity for {labels[member_id]}: observed completion-rate range "
                f"{min(available):.3f} to {max(available):.3f}."
            )
        else:
            lines.append(
                f"- {labels[member_id]} 的顺序敏感性：观察到的完成率范围为 "
                f"{min(available):.3f} 至 {max(available):.3f}。"
            )
    return lines


def _failure_appendix(
    overall: Sequence[Mapping[str, Any]],
    failure_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Experiment 5 Failure and Missingness Appendix / 失败与缺失性附录",
        "",
        "## Failure taxonomy / 失败分类",
        "",
        "| Model | Domain | Topic | Stage | Failure kind | Count | Evidence ref | Short summary |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in failure_rows:
        cells = (
            row.get("cohort_member_id"),
            row.get("domain"),
            row.get("topic_family") or "--",
            row.get("failure_stage"),
            row.get("failure_kind"),
            row.get("count"),
            row.get("evidence_ref"),
            row.get("short_summary"),
        )
        lines.append("| " + " | ".join(_markdown_cell(value) for value in cells) + " |")
    lines.extend(
        (
            "",
            "## Missingness / 缺失性",
            "",
            "| Model | Reasoning tokens missing | Visible-output tokens missing | Cost estimate missing |",
            "| --- | ---: | ---: | ---: |",
        )
    )
    for row in overall:
        lines.append(
            "| {} | {} | {} | {} |".format(
                _markdown_cell(row.get("model_label") or row["cohort_member_id"]),
                _exact_int(row.get("reasoning_tokens_missing_count", 0), "missing"),
                _exact_int(row.get("visible_output_tokens_missing_count", 0), "missing"),
                _exact_int(row.get("cost_estimate_missing_count", 0), "missing"),
            )
        )
    lines.extend(
        (
            "",
            "Only parsed failure summaries and evidence references are rendered; "
            "reasoning content and chain-of-thought are neither read nor reproduced.",
            "",
        )
    )
    return "\n".join(lines)


def _pdflatex_pdf_backend(path: Path, spec: PlotSpec) -> None:
    executable = shutil.which("pdflatex")
    if executable is None:
        raise PdfBackendUnavailable("pdflatex unavailable")
    with tempfile.TemporaryDirectory(prefix="tokenshare-exp5-pdf-") as temp_value:
        temp = Path(temp_value)
        tex_path = temp / f"{spec.artifact_id}.tex"
        tex_path.write_text(_plot_tex_document(spec), encoding="utf-8")
        completed = subprocess.run(
            [
                executable,
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={temp}",
                str(tex_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        generated = temp / f"{spec.artifact_id}.pdf"
        if completed.returncode != 0 or not generated.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()
            raise PdfBackendUnavailable(
                "pdflatex failed: " + detail[-500:]
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated, path)


def _plot_tex_document(spec: PlotSpec) -> str:
    lines = [
        r"\documentclass{article}",
        r"\usepackage[paperwidth=9in,paperheight=5.2in,margin=0.35in]{geometry}",
        r"\usepackage{graphicx}",
        r"\pagestyle{empty}",
        r"\setlength{\unitlength}{1pt}",
        r"\begin{document}",
        r"\begin{center}",
        r"{\Large\bfseries " + _latex_escape(spec.title) + r"}\\[4pt]",
        r"Whiskers show 95\% CI; $n$ is the eligible root denominator.",
        r"\end{center}",
        r"\begin{picture}(580,260)",
        r"\put(55,35){\line(1,0){500}}",
        r"\put(55,35){\line(0,1){200}}",
    ]
    axes = tuple(dict.fromkeys(series.axis_label for series in spec.series))
    maximum_by_axis = _plot_maximum_by_axis(spec)
    if len(axes) > 1:
        lines.append(r"\put(555,35){\line(0,1){200}}")
    for axis_index, axis in enumerate(axes):
        maximum = maximum_by_axis[axis]
        tick_x = 48 if axis_index == 0 else 562
        alignment = "r" if axis_index == 0 else "l"
        axis_x = 12 if axis_index == 0 else 590
        rotation = 90 if axis_index == 0 else -90
        lines.append(
            rf"\put({axis_x},135){{\rotatebox{{{rotation}}}{{\scriptsize {_latex_escape(axis)}}}}}"
        )
        for tick in range(6):
            y = 35 + round(200 * tick / 5)
            value = maximum * tick / 5
            lines.append(
                rf"\put({tick_x},{y}){{\makebox(0,0)[{alignment}]{{\scriptsize "
                + _latex_escape(_format_tick(value, maximum))
                + "}}"
            )
    for model_index, member_id in enumerate(_MODEL_IDS):
        x = 115 + model_index * 120
        label = spec.series[0].points[model_index].model_label
        lines.append(
            r"\put("
            + str(x)
            + r",14){\makebox(0,0){\scriptsize \shortstack{"
            + _latex_escape(label)
            + r"\\n="
            + str(spec.series[0].points[model_index].sample_size)
            + "}}}"
        )
        for series_index, series in enumerate(spec.series):
            point = series.points[model_index]
            maximum = maximum_by_axis[series.axis_label]
            y = 35 + round(200 * max(0.0, min(point.value, maximum)) / maximum)
            low = 35 + round(200 * max(0.0, min(point.ci_low, maximum)) / maximum)
            high = 35 + round(200 * max(0.0, min(point.ci_high, maximum)) / maximum)
            px = x + (series_index * 12 - 6)
            lines.extend(
                (
                    rf"\put({px},{low}){{\line(0,1){{{max(1, high-low)}}}}}",
                    rf"\put({px},{y}){{\circle*{{5}}}}",
                )
            )
    lines.extend(
        (
            r"\put(285,245){\makebox(0,0){\small 95\% CI}}",
            r"\end{picture}",
            r"\begin{center}",
            " / ".join(_latex_escape(series.label) for series in spec.series),
            r"\end{center}",
            r"\end{document}",
            "",
        )
    )
    return "\n".join(lines)


def _rate_ci(row: Mapping[str, Any], prefix: str) -> str:
    return "{:.3f} [{:.3f}, {:.3f}]".format(
        _number(row.get(f"{prefix}_rate"), f"{prefix}_rate"),
        _number(row.get(f"{prefix}_ci_low"), f"{prefix}_ci_low"),
        _number(row.get(f"{prefix}_ci_high"), f"{prefix}_ci_high"),
    )


def _rate_ci_markdown(row: Mapping[str, Any], prefix: str) -> str:
    return "{:.3f} [95% CI {:.3f}, {:.3f}]".format(
        _number(row.get(f"{prefix}_rate"), f"{prefix}_rate"),
        _number(row.get(f"{prefix}_ci_low"), f"{prefix}_ci_low"),
        _number(row.get(f"{prefix}_ci_high"), f"{prefix}_ci_high"),
    )


def _latex_escape(value: str) -> str:
    replacements = {
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
        "{": r"\{",
        "}": r"\}",
    }
    return "".join(replacements.get(character, character) for character in value)


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "null"
    return str(value).replace("|", r"\|").replace("\n", " ")


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _number(value, "optional numeric value")


def _exact_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an exact integer")
    return value


def _format_number(value: Any) -> str:
    if value is None:
        return "null"
    numeric = _number(value, "numeric value")
    if numeric.is_integer():
        return f"{int(numeric):,}"
    return f"{numeric:.3f}"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _artifact_refs(
    root: Path,
    relative_paths: Sequence[str],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "path": relative_path,
            "content_hash": "sha256:"
            + sha256((root / relative_path).read_bytes()).hexdigest(),
        }
        for relative_path in relative_paths
    )


def _existing_artifact_refs(
    root: Path,
    relative_paths: Sequence[str],
) -> tuple[dict[str, str], ...]:
    return _artifact_refs(
        root,
        tuple(path for path in relative_paths if (root / path).is_file()),
    )


def _clear_exp5_paper_artifacts(root: Path) -> None:
    shutil.rmtree(root / ".exp5-paper-staging", ignore_errors=True)
    paper_root = root / "paper"
    for relative_path in EXP5_PAPER_FILES:
        (root / relative_path).unlink(missing_ok=True)
    if paper_root.is_dir():
        try:
            paper_root.rmdir()
        except OSError:
            pass


__all__ = [
    "EXP5_AUDIT_FILES",
    "EXP5_PAPER_FILES",
    "Exp5PaperArtifactResult",
    "PdfBackendUnavailable",
    "PlotPoint",
    "PlotSeries",
    "PlotSpec",
    "render_exp5_paper_artifacts",
]
