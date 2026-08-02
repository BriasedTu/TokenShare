"""Serial publication of contract-bound intermediate paper metric drafts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_metric_registry import (
    MetricTableDraft,
    PaperMetricRegistry,
    load_paper_metric_registry,
)


@dataclass(frozen=True, kw_only=True)
class FormalMetricsResult:
    table_drafts: tuple[MetricTableDraft, ...]
    metrics_digest: str
    output_refs: tuple[dict[str, Any], ...]
    provider_calls: int = 0
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = "tokenshare.paper_formal_metric_drafts.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_drafts": [draft.to_dict() for draft in self.table_drafts],
            "metrics_digest": self.metrics_digest,
            "output_refs": [dict(ref) for ref in self.output_refs],
            "provider_calls": self.provider_calls,
            "recompute_only": self.recompute_only,
            "paper_eligible": self.paper_eligible,
        }


def publish_paper_formal_metric_drafts(
    output_root: str | Path,
    canonical_direct_rows: Mapping[str, object],
    *,
    global_infrastructure_valid: bool = True,
    registry: PaperMetricRegistry | None = None,
    contract: PaperMetricContract | None = None,
) -> FormalMetricsResult:
    """Delegate canonical persisted inputs and publish only intermediate drafts."""

    if type(global_infrastructure_valid) is not bool:
        raise ValueError("global_infrastructure_valid must be a bool")
    current_contract = contract or load_paper_metric_contract()
    if not isinstance(current_contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if registry is not None and not isinstance(registry, PaperMetricRegistry):
        raise TypeError("registry must be PaperMetricRegistry")
    current_registry = registry or load_paper_metric_registry(current_contract)
    _validate_registry_contract(current_registry, current_contract)
    drafts = tuple(
        current_registry.project_all(
            canonical_direct_rows,
            global_infra_invalid=not global_infrastructure_valid,
        )
    )
    expected_table_ids = tuple(table.table_id for table in current_contract.tables)
    actual_table_ids = tuple(draft.table_id for draft in drafts)
    if (
        actual_table_ids != expected_table_ids
        or len(set(actual_table_ids)) != len(actual_table_ids)
    ):
        raise ValueError("formal metric draft table inventory contract drift")
    for draft in drafts:
        current_registry.validate_draft(draft)
    body = {
        "schema_version": "tokenshare.paper_formal_metric_drafts_body.v1",
        "metric_contract_id": current_contract.contract_id,
        "metric_contract_digest": current_contract.contract_digest,
        "global_infrastructure_valid": global_infrastructure_valid,
        "provider_calls": 0,
        "recompute_only": True,
        "paper_eligible": False,
        "table_drafts": [draft.to_dict() for draft in drafts],
    }
    metrics_digest = _digest(body)
    output_refs = _publish_drafts(
        Path(output_root),
        drafts,
        body={**body, "metrics_digest": metrics_digest},
    )
    return FormalMetricsResult(
        table_drafts=drafts,
        metrics_digest=metrics_digest,
        output_refs=output_refs,
    )


def recompute_paper_formal_metrics(
    output_root: str | Path,
    canonical_direct_rows: Mapping[str, object],
    *,
    global_infrastructure_valid: bool = True,
    registry: PaperMetricRegistry | None = None,
    contract: PaperMetricContract | None = None,
) -> FormalMetricsResult:
    """Package-compatible name for the canonical-row delegation boundary."""

    return publish_paper_formal_metric_drafts(
        output_root,
        canonical_direct_rows,
        global_infrastructure_valid=global_infrastructure_valid,
        registry=registry,
        contract=contract,
    )


def _validate_registry_contract(
    registry: PaperMetricRegistry,
    contract: PaperMetricContract,
) -> None:
    registered = registry.contract
    if (
        registered.contract_id != contract.contract_id
        or registered.contract_version != contract.contract_version
        or registered.contract_digest != contract.contract_digest
        or registered.pipeline_profile_id != contract.pipeline_profile_id
        or registered.pipeline_profile_digest != contract.pipeline_profile_digest
    ):
        raise ValueError("registry contract identity or digest mismatch")


def _publish_drafts(
    root: Path,
    drafts: tuple[MetricTableDraft, ...],
    *,
    body: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for draft in drafts:
        path = root / f"{draft.output_path}.draft.json"
        payload = draft.to_dict()
        _write_json(path, payload)
        refs.append(_output_ref(root, path, payload, draft.table_id))
    manifest_path = root / "metrics" / "paper_metric_drafts.v1.json"
    _write_json(manifest_path, body)
    refs.append(_output_ref(root, manifest_path, body, "draft_manifest"))
    return tuple(refs)


def _output_ref(
    root: Path,
    path: Path,
    payload: Mapping[str, Any],
    table_id: str,
) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "path": path.relative_to(root).as_posix(),
        "content_digest": _digest(payload),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FormalMetricsResult",
    "publish_paper_formal_metric_drafts",
    "recompute_paper_formal_metrics",
]
