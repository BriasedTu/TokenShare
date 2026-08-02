"""把 formal metrics 的 typed observations 接到 contract-only renderer。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from tokenshare.experiments.paper_exp5_artifacts import (
    PaperMetricArtifactResult,
    render_paper_metric_artifacts,
)
from tokenshare.experiments.paper_formal_evidence import LineageSourceIndex
from tokenshare.experiments.paper_formal_metrics import FormalMetricsResult
from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    load_paper_metric_contract,
)


PAPER_FORMAL_REPORT_MANIFEST = "audit/paper_formal_report_manifest.v2.json"
PAPER_ELIGIBILITY_REPORT = "audit/paper_eligibility_report.json"
PAPER_SECRET_SCAN_REPORT = "audit/secret_scan_report.json"


@dataclass(frozen=True, kw_only=True)
class FormalReportResult:
    paper_eligible: bool
    regression_only: bool
    formal_paper_table_generated: bool
    report_ref: dict[str, Any]
    eligibility_report_ref: dict[str, Any]
    secret_scan_report_ref: dict[str, Any]
    artifact_refs: tuple[dict[str, str], ...]
    captions: Mapping[str, str]
    renderer_manifest_ref: dict[str, str]
    schema_version: str = "tokenshare.paper_formal_report.v2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "captions", MappingProxyType(dict(self.captions)))

    @property
    def exp5_artifact_refs(self) -> tuple[dict[str, str], ...]:
        """兼容旧调用方字段名；内容现在是完整八表 artifacts。"""

        return self.artifact_refs

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "paper_eligible": self.paper_eligible,
            "regression_only": self.regression_only,
            "formal_paper_table_generated": self.formal_paper_table_generated,
            "report_ref": dict(self.report_ref),
            "eligibility_report_ref": dict(self.eligibility_report_ref),
            "secret_scan_report_ref": dict(self.secret_scan_report_ref),
            "artifact_refs": [dict(ref) for ref in self.artifact_refs],
            "captions": dict(self.captions),
            "renderer_manifest_ref": dict(self.renderer_manifest_ref),
        }


def generate_paper_formal_report(
    *,
    output_root: str | Path,
    metrics: FormalMetricsResult,
    secret_values: Iterable[str] = (),
    exp5_pdf_backend: Callable[..., None] | None = None,
    contract: PaperMetricContract | None = None,
) -> FormalReportResult:
    """只消费 Task 20 observations；不再读取 condition_rows/Exp5 row bundles。"""

    if not isinstance(metrics, FormalMetricsResult):
        raise ValueError("metrics must be FormalMetricsResult")
    # PDF 参数只为旧 CLI ABI 保留；当前 renderer 只生成数据表与审计文件。
    del exp5_pdf_backend
    secrets = tuple(secret_values)
    if any(not isinstance(value, str) for value in secrets):
        raise TypeError("secret_values must contain strings")
    secrets = tuple(value for value in secrets if value)
    current = contract or load_paper_metric_contract()
    root = Path(output_root)
    rendered: PaperMetricArtifactResult | None = None
    publication_error: str | None = None
    renderer_error: str | None = None
    try:
        _validate_task20_publication(root, metrics, current)
    except (TypeError, ValueError) as exc:
        publication_error = str(exc)
    if publication_error is None:
        try:
            rendered = render_paper_metric_artifacts(
                output_root=root,
                contract=current,
                observations=metrics.metric_observations,
                observations_digest=metrics.observations_digest,
            )
        except (TypeError, ValueError) as exc:
            renderer_error = str(exc)

    renderer_refs = () if rendered is None else rendered.artifact_refs
    secret_hits = tuple(
        sorted(
            ref["path"]
            for ref in renderer_refs
            if _artifact_contains_secret(root / ref["path"], secrets)
        )
    )
    secret_scan = {
        "schema_version": "tokenshare.paper_output_secret_scan.v1",
        "passed": not secret_hits,
        "secret_value_count": len(secrets),
        "scanned_artifact_count": len(renderer_refs),
        "scanned_artifact_refs": [dict(ref) for ref in renderer_refs],
        "hit_paths": list(secret_hits),
        "scope": "renderer_owned_paper_outputs_only",
    }
    secret_path = root / PAPER_SECRET_SCAN_REPORT
    _write_json(secret_path, secret_scan)
    secret_ref = _artifact_ref(root, secret_path)

    reasons: list[str] = []
    if publication_error is not None:
        reasons.append("task20_publication_invalid")
    elif rendered is None:
        reasons.append("renderer_input_invalid")
    elif not rendered.paper_eligible:
        reasons.append("renderer_publish_blocked")
    if secret_hits:
        reasons.append("secret_scan_failed")
    blocked = () if rendered is None else rendered.blocked_table_ids
    paper_eligible = rendered is not None and not reasons
    eligibility = {
        "schema_version": "tokenshare.paper_formal_eligibility_report.v2",
        "paper_eligible": paper_eligible,
        "regression_only": not paper_eligible,
        "formal_paper_table_generated": rendered is not None,
        "ineligibility_reasons": reasons,
        "blocked_table_ids": list(blocked),
        "renderer_error": renderer_error,
        "task20_publication_error": publication_error,
        "renderer_manifest_ref": (
            {} if rendered is None else dict(rendered.manifest_ref)
        ),
        "secret_scan_report_ref": secret_ref,
    }
    eligibility_path = root / PAPER_ELIGIBILITY_REPORT
    _write_json(eligibility_path, eligibility)
    eligibility_ref = _artifact_ref(root, eligibility_path)

    report_manifest = {
        "schema_version": "tokenshare.paper_formal_report_manifest.v2",
        "metrics_digest": metrics.metrics_digest,
        "observations_digest": metrics.observations_digest,
        "paper_eligible": paper_eligible,
        "regression_only": not paper_eligible,
        "formal_paper_table_generated": rendered is not None,
        "blocked_table_ids": list(blocked),
        "ineligibility_reasons": reasons,
        "task20_publication_error": publication_error,
        "eligibility_report_ref": eligibility_ref,
        "secret_scan_report_ref": secret_ref,
        "renderer_manifest_ref": (
            {} if rendered is None else dict(rendered.manifest_ref)
        ),
        "renderer_audit_ref": (
            {} if rendered is None else dict(rendered.audit_ref)
        ),
        "table_refs": (
            [] if rendered is None else [dict(ref) for ref in rendered.table_refs]
        ),
        "captions": {} if rendered is None else dict(rendered.captions),
        "provider_calls": 0,
        "output_kind": "data_tables_audit_manifest_only",
    }
    report_path = root / PAPER_FORMAL_REPORT_MANIFEST
    _write_json(report_path, report_manifest)
    report_ref = _artifact_ref(root, report_path)
    artifact_refs = renderer_refs + (secret_ref, eligibility_ref, report_ref)
    result = FormalReportResult(
        paper_eligible=paper_eligible,
        regression_only=not paper_eligible,
        formal_paper_table_generated=rendered is not None,
        report_ref=report_ref,
        eligibility_report_ref=eligibility_ref,
        secret_scan_report_ref=secret_ref,
        artifact_refs=artifact_refs,
        captions={} if rendered is None else rendered.captions,
        renderer_manifest_ref={} if rendered is None else rendered.manifest_ref,
    )
    _write_json(root / "formal_report_result.json", result.to_dict())
    return result


def _validate_task20_publication(
    root: Path,
    metrics: FormalMetricsResult,
    contract: PaperMetricContract,
) -> None:
    """验证 Task20 typed result 与其已持久化 sidecar 的 digest closure。"""

    source_index = metrics.lineage_source_index
    if not isinstance(source_index, LineageSourceIndex):
        raise TypeError("Task20 lineage_source_index must be typed")
    if (
        metrics.schema_version != "tokenshare.paper_formal_metrics.v2"
        or metrics.provider_calls != 0
        or metrics.recompute_only is not True
        or metrics.paper_eligible is not False
    ):
        raise ValueError("Task20 publication gate fields are invalid")
    for observation in metrics.metric_observations:
        if (
            observation.source_index_id != source_index.index_id
            or observation.source_index_digest != source_index.index_digest
        ):
            raise ValueError("Task20 observation source-index closure mismatch")

    draft_paths = {
        f"{draft.output_path}.draft.json": draft for draft in metrics.table_drafts
    }
    if len(draft_paths) != len(metrics.table_drafts):
        raise ValueError("Task20 draft output paths are not unique")
    required_paths = {
        *draft_paths,
        "metrics/paper_metric_drafts.v1.json",
        "metrics/paper_lineage_source_index.v1.jsonl",
        "metrics/paper_metric_observations.v1.jsonl",
        "metrics/paper_metric_observations_manifest.v1.json",
    }
    refs_by_path: dict[str, Mapping[str, Any]] = {}
    for ref in metrics.output_refs:
        if not isinstance(ref, Mapping):
            raise TypeError("Task20 output refs must be mappings")
        relative_path = ref.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("Task20 output ref path is invalid")
        if relative_path in refs_by_path:
            raise ValueError("Task20 output refs contain duplicate paths")
        refs_by_path[relative_path] = ref
    if set(refs_by_path) != required_paths:
        raise ValueError("Task20 output ref inventory mismatch")

    payloads: dict[str, Any] = {}
    resolved_root = root.resolve(strict=False)
    for relative_path, ref in refs_by_path.items():
        path = (root / relative_path).resolve(strict=False)
        if resolved_root != path and resolved_root not in path.parents:
            raise ValueError("Task20 output ref escapes publication root")
        if not path.is_file():
            raise ValueError(f"Task20 persisted output is missing: {relative_path}")
        if relative_path.endswith(".jsonl"):
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            payload: Any = {"records": records}
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        if ref.get("content_digest") != _digest_json(payload):
            raise ValueError(f"Task20 output ref digest mismatch: {relative_path}")
        payloads[relative_path] = payload

    for relative_path, draft in draft_paths.items():
        if payloads[relative_path] != draft.to_dict():
            raise ValueError("Task20 persisted draft payload mismatch")
        if refs_by_path[relative_path].get("table_id") != draft.table_id:
            raise ValueError("Task20 persisted draft table identity mismatch")
    source_payload = [record.to_dict() for record in source_index.records]
    if payloads["metrics/paper_lineage_source_index.v1.jsonl"] != {
        "records": source_payload
    }:
        raise ValueError("Task20 persisted source-index records mismatch")
    observation_payload = [
        observation.to_dict() for observation in metrics.metric_observations
    ]
    if payloads["metrics/paper_metric_observations.v1.jsonl"] != {
        "records": observation_payload
    }:
        raise ValueError("Task20 persisted observation records mismatch")

    draft_manifest = payloads["metrics/paper_metric_drafts.v1.json"]
    if not isinstance(draft_manifest, Mapping):
        raise TypeError("Task20 draft manifest must be an object")
    digest_body = dict(draft_manifest)
    persisted_metrics_digest = digest_body.pop("metrics_digest", None)
    if (
        persisted_metrics_digest != metrics.metrics_digest
        or _digest_json(digest_body) != metrics.metrics_digest
        or draft_manifest.get("metric_contract_id") != contract.contract_id
        or draft_manifest.get("metric_contract_digest") != contract.contract_digest
        or draft_manifest.get("lineage_source_index_id") != source_index.index_id
        or draft_manifest.get("lineage_source_index_digest") != source_index.index_digest
        or draft_manifest.get("metric_observations_digest") != metrics.observations_digest
        or draft_manifest.get("metric_observation_count") != len(metrics.metric_observations)
        or draft_manifest.get("blocked_table_ids") != list(metrics.blocked_table_ids)
        or draft_manifest.get("table_drafts")
        != [draft.to_dict() for draft in metrics.table_drafts]
    ):
        raise ValueError("Task20 metric manifest digest closure mismatch")

    observation_manifest = payloads[
        "metrics/paper_metric_observations_manifest.v1.json"
    ]
    if (
        not isinstance(observation_manifest, Mapping)
        or observation_manifest.get("schema_version")
        != "tokenshare.paper_metric_observation_publication.v1"
        or observation_manifest.get("lineage_source_index") != source_index.to_dict()
        or observation_manifest.get("observations_digest") != metrics.observations_digest
        or observation_manifest.get("observation_count") != len(metrics.metric_observations)
        or observation_manifest.get("blocked_table_ids")
        != list(metrics.blocked_table_ids)
        or observation_manifest.get("lineage_source_index_path")
        != "metrics/paper_lineage_source_index.v1.jsonl"
        or observation_manifest.get("metric_observations_path")
        != "metrics/paper_metric_observations.v1.jsonl"
    ):
        raise ValueError("Task20 observation manifest closure mismatch")


def _artifact_contains_secret(path: Path, secret_values: Iterable[str]) -> bool:
    if not path.is_file():
        raise ValueError(f"rendered artifact is missing: {path.as_posix()}")
    content = path.read_bytes()
    return any(value.encode("utf-8") in content for value in secret_values)


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _artifact_ref(root: Path, path: Path) -> dict[str, str]:
    digest = sha256(path.read_bytes()).hexdigest()
    return {
        "path": path.relative_to(root).as_posix(),
        "content_hash": "sha256:" + digest,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "FormalReportResult",
    "PAPER_ELIGIBILITY_REPORT",
    "PAPER_FORMAL_REPORT_MANIFEST",
    "PAPER_SECRET_SCAN_REPORT",
    "generate_paper_formal_report",
]
