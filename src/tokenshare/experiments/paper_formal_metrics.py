"""Serial publication of contract-bound intermediate paper metric drafts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence
import uuid

from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.trace_backed import TraceSourceBinding
from tokenshare.experiments.paper_formal_evidence import (
    FormalEvidenceStore,
    LineageSourceIndex,
    build_canonical_lineage_inputs,
    export_lineage_source_index,
    lineage_input_identity_digest,
)
from tokenshare.experiments.paper_models import (
    CanonicalDirectRootEvidence,
    PaperEvidenceEligibilityFacts,
)
from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    capture_metric_computation_traces,
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_metric_observations import (
    MetricObservationPublication,
    PaperMetricObservation,
    materialize_metric_observations,
)
from tokenshare.experiments.paper_metric_registry import (
    MetricTableDraft,
    PaperMetricRegistry,
    load_paper_metric_registry,
)


@dataclass(frozen=True, kw_only=True)
class FormalMetricsResult:
    table_drafts: tuple[MetricTableDraft, ...]
    metric_observations: tuple[PaperMetricObservation, ...]
    lineage_source_index: LineageSourceIndex
    metrics_digest: str
    observations_digest: str
    output_refs: tuple[dict[str, Any], ...]
    blocked_table_ids: tuple[str, ...] = ()
    provider_calls: int = 0
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = "tokenshare.paper_formal_metrics.v2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_drafts": [draft.to_dict() for draft in self.table_drafts],
            "metric_observations": [
                observation.to_dict() for observation in self.metric_observations
            ],
            "lineage_source_index": self.lineage_source_index.to_dict(),
            "metrics_digest": self.metrics_digest,
            "observations_digest": self.observations_digest,
            "output_refs": [dict(ref) for ref in self.output_refs],
            "blocked_table_ids": list(self.blocked_table_ids),
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
    canonical_runtime_evidence: Sequence[CanonicalDirectRootEvidence] = (),
    requested_lineage_root_ids: Sequence[str] | None = None,
    current_trace_wrappers_by_root: Mapping[str, Sequence[CurrentTraceWrapper]] | None = None,
    trace_source_bindings_by_root: Mapping[str, Sequence[TraceSourceBinding]] | None = None,
    eligibility_facts_by_root: Mapping[str, PaperEvidenceEligibilityFacts] | None = None,
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
    input_identity_digest = lineage_input_identity_digest(canonical_direct_rows)
    canonical_lineage_inputs = build_canonical_lineage_inputs(
        canonical_direct_rows,
        canonical_runtime_evidence=canonical_runtime_evidence,
        requested_root_ids=requested_lineage_root_ids,
        current_trace_wrappers_by_root=current_trace_wrappers_by_root,
        trace_source_bindings_by_root=trace_source_bindings_by_root,
        eligibility_facts_by_root=eligibility_facts_by_root,
    )
    source_index = export_lineage_source_index(
        FormalEvidenceStore(output_root),
        canonical_lineage_inputs,
        input_identity_digest=input_identity_digest,
    )
    with capture_metric_computation_traces() as computation_traces:
        drafts = tuple(
            current_registry.project_all(
                canonical_direct_rows,
                global_infra_invalid=not global_infrastructure_valid,
            )
        )
    observation_publication = materialize_metric_observations(
        contract=current_contract,
        table_drafts=drafts,
        computation_traces=tuple(computation_traces),
        source_index=source_index,
        expected_source_input_identity_digest=input_identity_digest,
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
        "lineage_source_index_id": source_index.index_id,
        "lineage_source_index_digest": source_index.index_digest,
        "metric_observations_digest": observation_publication.observations_digest,
        "metric_observation_count": len(observation_publication.observations),
        "numeric_draft_cell_count": observation_publication.numeric_draft_cell_count,
        "blocked_table_ids": list(observation_publication.blocked_table_ids),
    }
    metrics_digest = _digest(body)
    output_refs = _publish_drafts(
        Path(output_root),
        drafts,
        body={**body, "metrics_digest": metrics_digest},
        source_index=source_index,
        observation_publication=observation_publication,
    )
    return FormalMetricsResult(
        table_drafts=drafts,
        metric_observations=observation_publication.observations,
        lineage_source_index=source_index,
        metrics_digest=metrics_digest,
        observations_digest=observation_publication.observations_digest,
        output_refs=output_refs,
        blocked_table_ids=observation_publication.blocked_table_ids,
    )


def recompute_paper_formal_metrics(
    output_root: str | Path,
    canonical_direct_rows: Mapping[str, object],
    *,
    global_infrastructure_valid: bool = True,
    registry: PaperMetricRegistry | None = None,
    contract: PaperMetricContract | None = None,
    canonical_runtime_evidence: Sequence[CanonicalDirectRootEvidence] = (),
    requested_lineage_root_ids: Sequence[str] | None = None,
    current_trace_wrappers_by_root: Mapping[str, Sequence[CurrentTraceWrapper]] | None = None,
    trace_source_bindings_by_root: Mapping[str, Sequence[TraceSourceBinding]] | None = None,
    eligibility_facts_by_root: Mapping[str, PaperEvidenceEligibilityFacts] | None = None,
) -> FormalMetricsResult:
    """Package-compatible name for the canonical-row delegation boundary."""

    return publish_paper_formal_metric_drafts(
        output_root,
        canonical_direct_rows,
        global_infrastructure_valid=global_infrastructure_valid,
        registry=registry,
        contract=contract,
        canonical_runtime_evidence=canonical_runtime_evidence,
        requested_lineage_root_ids=requested_lineage_root_ids,
        current_trace_wrappers_by_root=current_trace_wrappers_by_root,
        trace_source_bindings_by_root=trace_source_bindings_by_root,
        eligibility_facts_by_root=eligibility_facts_by_root,
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
    source_index: LineageSourceIndex,
    observation_publication: MetricObservationPublication,
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
    source_path = root / "metrics" / "paper_lineage_source_index.v1.jsonl"
    source_payload = [record.to_dict() for record in source_index.records]
    _atomic_write_jsonl(source_path, source_payload)
    refs.append(
        _output_ref(
            root,
            source_path,
            {"records": source_payload},
            "lineage_source_index",
        )
    )
    observations_path = root / "metrics" / "paper_metric_observations.v1.jsonl"
    observation_payload = [
        observation.to_dict() for observation in observation_publication.observations
    ]
    _atomic_write_jsonl(observations_path, observation_payload)
    refs.append(
        _output_ref(
            root,
            observations_path,
            {"records": observation_payload},
            "metric_observations",
        )
    )
    observation_manifest = {
        "schema_version": "tokenshare.paper_metric_observation_manifest.v1",
        "lineage_source_index": source_index.to_dict(),
        **observation_publication.to_dict(),
        "lineage_source_index_path": "metrics/paper_lineage_source_index.v1.jsonl",
        "metric_observations_path": "metrics/paper_metric_observations.v1.jsonl",
        "provider_calls": 0,
        "recompute_only": True,
        "paper_eligible": False,
    }
    observation_manifest_path = (
        root / "metrics" / "paper_metric_observations_manifest.v1.json"
    )
    _atomic_write_json(observation_manifest_path, observation_manifest)
    refs.append(
        _output_ref(
            root,
            observation_manifest_path,
            observation_manifest,
            "metric_observation_manifest",
        )
    )
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


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


def _atomic_write_jsonl(path: Path, payloads: list[Mapping[str, Any]]) -> None:
    content = "".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for payload in payloads
    ).encode("utf-8")
    _atomic_write_bytes(path, content)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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
