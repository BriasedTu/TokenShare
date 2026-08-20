"""Serial publication of contract-bound intermediate paper metric drafts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.trace_backed import TraceSourceBinding
from tokenshare.experiments.paper_formal_evidence import (
    _CANONICAL_LINEAGE_DIGEST_FACTORY_TOKEN,
    FormalEvidenceStore,
    LineageSourceIndex,
    _merge_lineage_source_records,
    build_canonical_lineage_inputs,
    export_lineage_source_index,
    lineage_input_identity_digest,
)
from tokenshare.experiments.paper_direct_results import (
    PaperDirectRootResult,
    _DIRECT_RESULT_FACTORY_TOKEN,
)
from tokenshare.experiments.paper_exp1_metrics import (
    EXP1_EVIDENCE_CLASSES,
    Exp1HydratedDirectRow,
)
from tokenshare.experiments.paper_exp2_metrics import (
    Exp2OnlineHydratedRoot,
    Exp2TraceHydratedRoot,
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


def _export_metric_lineage_source_index(
    *,
    output_root: str | Path,
    canonical_lineage_inputs: Sequence[Any],
    input_identity_digest: str,
    evidence_roots_by_experiment: Mapping[str, str | Path] | None,
) -> LineageSourceIndex:
    """按 persisted suite 分区校验 lineage，再合并成同一输入身份索引。"""

    inputs = tuple(canonical_lineage_inputs)
    if evidence_roots_by_experiment is None:
        return export_lineage_source_index(
            FormalEvidenceStore(output_root),
            inputs,
            input_identity_digest=input_identity_digest,
        )
    if not isinstance(evidence_roots_by_experiment, Mapping):
        raise TypeError("lineage evidence roots must be a mapping")
    grouped: dict[str, list[Any]] = {}
    for lineage_input in inputs:
        direct = getattr(lineage_input, "direct_result", None)
        experiment_id = getattr(direct, "experiment_id", None)
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ValueError("lineage input experiment identity is missing")
        grouped.setdefault(experiment_id, []).append(lineage_input)
    if set(evidence_roots_by_experiment) != set(grouped):
        raise ValueError("lineage evidence root inventory mismatch")
    root_ids = tuple(
        getattr(getattr(value, "direct_result", None), "preregistered_root_run_id", None)
        for value in inputs
    )
    if (
        any(not isinstance(root_id, str) or not root_id for root_id in root_ids)
        or len(set(root_ids)) != len(root_ids)
    ):
        raise ValueError("duplicate lineage root or missing root identity")
    inputs_by_resolved_root: dict[Path, list[Any]] = {}
    for experiment_id in sorted(grouped):
        values = tuple(grouped[experiment_id])
        if not values or any(
            getattr(getattr(value, "direct_result", None), "experiment_id", None)
            != experiment_id
            for value in values
        ):
            raise ValueError("lineage experiment bucket identity mismatch")
        root = evidence_roots_by_experiment[experiment_id]
        if not isinstance(root, (str, Path)):
            raise TypeError("lineage evidence root path is invalid")
        inputs_by_resolved_root.setdefault(Path(root).resolve(strict=False), []).extend(
            values
        )
    records = []
    for root in sorted(inputs_by_resolved_root, key=lambda value: str(value)):
        partition = export_lineage_source_index(
            FormalEvidenceStore(root),
            tuple(inputs_by_resolved_root[root]),
            input_identity_digest=input_identity_digest,
        )
        records.extend(partition.records)
    return LineageSourceIndex.create(
        records=_merge_lineage_source_records(records),
        input_identity_digest=input_identity_digest,
    )


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
    metric_projection_rows: Mapping[str, object] | None = None,
    lineage_evidence_roots_by_experiment: Mapping[str, str | Path] | None = None,
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
    projected_rows = _resolve_metric_projection_rows(
        canonical_direct_rows,
        metric_projection_rows,
    )
    input_identity_digest = lineage_input_identity_digest(canonical_direct_rows)
    canonical_lineage_inputs = build_canonical_lineage_inputs(
        canonical_direct_rows,
        canonical_runtime_evidence=canonical_runtime_evidence,
        requested_root_ids=requested_lineage_root_ids,
        current_trace_wrappers_by_root=current_trace_wrappers_by_root,
        trace_source_bindings_by_root=trace_source_bindings_by_root,
        eligibility_facts_by_root=eligibility_facts_by_root,
        _precomputed_input_identity_digest=input_identity_digest,
        _digest_factory_token=_CANONICAL_LINEAGE_DIGEST_FACTORY_TOKEN,
    )
    source_index = _export_metric_lineage_source_index(
        output_root=output_root,
        canonical_lineage_inputs=canonical_lineage_inputs,
        input_identity_digest=input_identity_digest,
        evidence_roots_by_experiment=lineage_evidence_roots_by_experiment,
    )
    with capture_metric_computation_traces() as computation_traces:
        drafts = tuple(
            current_registry.project_all(
                projected_rows,
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
    metric_projection_rows: Mapping[str, object] | None = None,
    lineage_evidence_roots_by_experiment: Mapping[str, str | Path] | None = None,
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
        metric_projection_rows=metric_projection_rows,
        lineage_evidence_roots_by_experiment=(
            lineage_evidence_roots_by_experiment
        ),
    )


def derive_paper_metric_projection_rows(
    canonical_direct_rows: Mapping[str, object],
) -> Mapping[str, object]:
    """确定性生成 metric identity 投影；不改变 authoritative lineage rows。"""

    if not isinstance(canonical_direct_rows, Mapping):
        raise TypeError("canonical direct rows must be a mapping")
    changed = False
    projected: dict[str, object] = {}
    for input_key, value in canonical_direct_rows.items():
        rows = _projection_sequence(value, label=f"canonical {input_key}")
        if input_key not in {
            "exp1_feasibility",
            "exp2_trace_scalability",
            "exp2_online_concurrency",
        }:
            projected[input_key] = value
            continue
        metric_rows = []
        for row in rows:
            direct = _metric_direct_result(row, input_key=input_key)
            target_id = _metric_experiment_id(
                input_key=input_key,
                direct=direct,
            )
            if target_id == direct.experiment_id:
                metric_rows.append(row)
                continue
            metric_direct = replace(
                direct,
                experiment_id=target_id,
                _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
            )
            if isinstance(row, PaperDirectRootResult):
                metric_rows.append(metric_direct)
            else:
                metric_rows.append(
                    replace(row, direct_result=metric_direct)
                )
            changed = True
        projected[input_key] = tuple(metric_rows)
    return projected if changed else canonical_direct_rows


def _resolve_metric_projection_rows(
    canonical_direct_rows: Mapping[str, object],
    metric_projection_rows: Mapping[str, object] | None,
) -> Mapping[str, object]:
    expected = derive_paper_metric_projection_rows(canonical_direct_rows)
    if metric_projection_rows is None:
        return expected
    if not isinstance(metric_projection_rows, Mapping):
        raise TypeError("metric projection rows must be a mapping")
    if set(metric_projection_rows) != set(canonical_direct_rows):
        raise ValueError("metric projection keys must match canonical rows")
    for input_key in canonical_direct_rows:
        authoritative = _projection_sequence(
            canonical_direct_rows[input_key],
            label=f"canonical {input_key}",
        )
        projected = _projection_sequence(
            metric_projection_rows[input_key],
            label=f"metric projection {input_key}",
        )
        expected_rows = _projection_sequence(
            expected[input_key],
            label=f"expected metric projection {input_key}",
        )
        if len(projected) != len(authoritative):
            raise ValueError("metric projection row count mismatch")
        for ordinal, (source_row, projected_row, expected_row) in enumerate(
            zip(authoritative, projected, expected_rows, strict=True)
        ):
            source_root_id = _metric_projection_root_id(source_row)
            projected_root_id = _metric_projection_root_id(projected_row)
            if source_root_id != projected_root_id:
                raise ValueError(
                    f"metric projection root/order mismatch at {input_key}[{ordinal}]"
                )
            if projected_row != expected_row:
                raise ValueError(
                    "metric projection differs beyond allowed identity view"
                )
    return metric_projection_rows


def _metric_projection_root_id(row: object) -> object:
    direct = getattr(row, "direct_result", row)
    return getattr(direct, "preregistered_root_run_id", None)


def _metric_direct_result(row: object, *, input_key: str) -> PaperDirectRootResult:
    expected_types = {
        "exp1_feasibility": (PaperDirectRootResult, Exp1HydratedDirectRow),
        "exp2_trace_scalability": (Exp2TraceHydratedRoot,),
        "exp2_online_concurrency": (Exp2OnlineHydratedRoot,),
    }
    if not isinstance(row, expected_types[input_key]):
        raise TypeError(f"{input_key} metric projection row type mismatch")
    direct = row if isinstance(row, PaperDirectRootResult) else row.direct_result
    if not isinstance(direct, PaperDirectRootResult):
        raise TypeError(f"{input_key} metric projection requires a direct result")
    return direct


def _metric_experiment_id(
    *,
    input_key: str,
    direct: PaperDirectRootResult,
) -> str:
    if input_key == "exp1_feasibility":
        if direct.experiment_id == "experiment_1":
            if direct.evidence_class not in EXP1_EVIDENCE_CLASSES:
                raise ValueError("Exp1 metric identity route mismatch")
            return direct.experiment_id
        if (
            direct.experiment_id != "exp1_real_ai_feasibility"
            or direct.evidence_class not in EXP1_EVIDENCE_CLASSES
        ):
            raise ValueError("Exp1 metric identity route mismatch")
        return "experiment_1"
    expected = {
        "exp2_trace_scalability": (
            "real_model_trace_protocol_run",
            "experiment_2_trace",
        ),
        "exp2_online_concurrency": (
            "online_real_provider",
            "experiment_2_online",
        ),
    }[input_key]
    if (
        direct.experiment_id == expected[1]
        and direct.evidence_class == expected[0]
    ):
        return direct.experiment_id
    if (
        direct.experiment_id != "exp2_real_ai_scalability"
        or direct.evidence_class != expected[0]
    ):
        raise ValueError("Exp2 metric identity route mismatch")
    return expected[1]


def _projection_sequence(value: object, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError(f"{label} rows must be a sequence")
    return value


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
    source_digest = _atomic_write_jsonl_records(source_path, source_index.records)
    refs.append(
        _output_ref(
            root,
            source_path,
            None,
            "lineage_source_index",
            content_digest=source_digest,
        )
    )
    observations_path = root / "metrics" / "paper_metric_observations.v1.jsonl"
    observation_digest = _atomic_write_jsonl_records(
        observations_path,
        observation_publication.observations,
    )
    refs.append(
        _output_ref(
            root,
            observations_path,
            None,
            "metric_observations",
            content_digest=observation_digest,
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
    payload: Mapping[str, Any] | None,
    table_id: str,
    *,
    content_digest: str | None = None,
) -> dict[str, Any]:
    if (payload is None) == (content_digest is None):
        raise ValueError("metric output ref requires exactly one digest source")
    return {
        "table_id": table_id,
        "path": path.relative_to(root).as_posix(),
        "content_digest": _digest(payload) if payload is not None else content_digest,
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


def _atomic_write_jsonl_records(path: Path, records: Sequence[Any]) -> str:
    """逐条写出 JSONL，并并行计算与 ``_digest({'records': ...})`` 相同的摘要。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    digest.update(b'{"records":[')
    try:
        with temporary.open("xb") as handle:
            for index, record in enumerate(records):
                payload = record if isinstance(record, Mapping) else record.to_dict()
                if not isinstance(payload, Mapping):
                    raise TypeError("metric JSONL record must serialize to a mapping")
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                handle.write(encoded)
                handle.write(b"\n")
                if index:
                    digest.update(b",")
                digest.update(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        digest.update(b"]}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return "sha256:" + digest.hexdigest()


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
