"""论文表格逐 cell 的确定性复算与只读外部 evidence 校验。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copyreg
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import pickle
import shutil
import tempfile
from types import MappingProxyType
from typing import Any
import uuid

from tokenshare.executors.response_bank import (
    ResponseBankBlockedError,
    ResponseBankResolver,
)
from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    load_paper_metric_contract,
)
from tokenshare.experiments.paper_metric_observations import (
    PaperMetricObservation,
    recompute_observation_value,
)
from tokenshare.experiments.paper_models import ArtifactIdentitySnapshot


L1_SYNTHETIC_COMPONENT = "L1_synthetic_component"
L4_ARTIFACT_ROOT = "L4_artifact_root"
PAPER_CELL_LINEAGE_PATH = "audit/paper_cell_lineage.jsonl"
_REPLAY_INPUT_DESCRIPTOR = "paper_traceability_replay_input_root.v1.json"
_CURRENT_PROVIDER_ROLES = frozenset(
    {
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "provider_attempt",
        "model_record",
    }
)


def _restore_mapping_proxy(value: dict[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(value)


def _reduce_mapping_proxy(value: Mapping[Any, Any]) -> tuple[Any, tuple[Any, ...]]:
    return _restore_mapping_proxy, (dict(value),)


copyreg.pickle(type(MappingProxyType({})), _reduce_mapping_proxy)


class TraceabilityBlockedError(ValueError):
    """外部对象或 role 无法只读闭合时阻断 replay。"""


@dataclass(frozen=True, kw_only=True)
class CellLineageRecomputation:
    records: tuple[dict[str, Any], ...]
    cell_lineage_digest: str
    audit_level: str
    regression_only: bool
    provider_calls: int = 0
    source_write_count: int = 0
    schema_version: str = "tokenshare.paper_cell_lineage_recomputation.v1"


@dataclass(frozen=True, kw_only=True)
class PaperTraceabilityReplayResult:
    output_root: Path
    observations_digest: str
    tables_digest: str
    cell_lineage_digest: str
    report: Any
    loaded_direct_inputs: Mapping[str, object]
    loaded_current_inputs: Mapping[str, object]
    loaded_source_inputs: Mapping[str, object]
    audit_level: str = L4_ARTIFACT_ROOT
    regression_only: bool = False
    provider_calls: int = 0
    source_write_count: int = 0
    online_fill_count: int = 0
    schema_version: str = "tokenshare.paper_traceability_replay.v1"


@dataclass(frozen=True, kw_only=True)
class ProtectedReplayInputRoot:
    root_path: Path
    descriptor_path: Path
    descriptor_digest: str
    classification: str
    _producer_validated: bool = False

    def __post_init__(self) -> None:
        if not self._producer_validated:
            raise ValueError("protected replay input root requires runner factory")
        if self.classification != "normal_formal_artifact_root":
            raise ValueError("protected replay input classification mismatch")


@dataclass(frozen=True, kw_only=True)
class _LoadedReplayInputs:
    direct: Mapping[str, object]
    current: Mapping[str, object]
    source: Mapping[str, object]
    current_evidence_files: tuple[tuple[str, bytes], ...]


def recompute_cell_lineage(
    *,
    contract: PaperMetricContract,
    observations: Sequence[PaperMetricObservation],
    audit_level: str,
) -> CellLineageRecomputation:
    """只调用既有 contract evaluator，按 cell identity 生成 canonical audit。"""

    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if audit_level not in {L1_SYNTHETIC_COMPONENT, L4_ARTIFACT_ROOT}:
        raise ValueError("unsupported traceability audit level")
    values = tuple(observations)
    if any(not isinstance(value, PaperMetricObservation) for value in values):
        raise TypeError("observations must contain PaperMetricObservation values")
    canonical = tuple(
        sorted(
            values,
            key=lambda item: (
                item.table_id,
                item.row_identity_digest,
                item.column_id,
                item.observation_id,
            ),
        )
    )
    records: list[dict[str, Any]] = []
    for item in canonical:
        metric = contract.require_metric(item.table_id, item.metric_id)
        if item.column_id != item.metric_id or item.formula_id != metric.formula_id:
            raise ValueError("cell metric/formula identity mismatch")
        if tuple(item.denominator_inventory_ids) != tuple(item.member_facts_by_id):
            raise ValueError("cell denominator inventory is not fully preserved")
        formula_value = recompute_observation_value(contract, item)
        if item.numeric_value is None:
            if not item.null_reason:
                raise ValueError("null cell must preserve an explicit reason")
            recomputed_value: Decimal | int | None = None
        else:
            if formula_value != item.numeric_value:
                raise ValueError("numeric cell independent recomputation mismatch")
            recomputed_value = formula_value

        provider_refs: Any = [
            _json_value(value) for value in item.current_provider_object_refs
        ]
        source_locators: Any = [
            _json_value(value) for value in item.source_bank_object_locators
        ]
        if item.evidence_class == "online_real_provider":
            if source_locators:
                raise ValueError("online cell must not contain source bank locators")
            source_locators = "N/A"
        elif item.evidence_class == "real_model_trace_protocol_run":
            if provider_refs:
                raise ValueError("trace cell must not contain current provider refs")
            provider_refs = "N/A"
        records.append(
            {
                "schema_version": "tokenshare.paper_cell_lineage_row.v1",
                "table_id": item.table_id,
                "row_identity_digest": item.row_identity_digest,
                "column_id": item.column_id,
                "observation_id": item.observation_id,
                "observation_digest": item.observation_digest,
                "formula_id": item.formula_id,
                "formula_recomputed": True,
                "formula_value": _json_value(formula_value),
                "numeric_value": _json_value(item.numeric_value),
                "recomputed_value": _json_value(recomputed_value),
                "null_reason": item.null_reason,
                "publish_blocked": item.publish_blocked,
                "denominator_inventory_ids": list(item.denominator_inventory_ids),
                "preserved_denominator_inventory_ids": list(
                    item.denominator_inventory_ids
                ),
                "audit_denominator_member_ids": list(
                    item.audit_denominator_member_ids
                ),
                "required_current_provider_roles": list(
                    item.required_current_provider_roles
                ),
                "covered_current_provider_roles": list(
                    item.covered_current_provider_roles
                ),
                "required_source_bank_roles": list(item.required_source_bank_roles),
                "covered_source_bank_roles": list(item.covered_source_bank_roles),
                "current_provider_object_refs": provider_refs,
                "source_bank_object_locators": source_locators,
                "evidence_class": item.evidence_class,
            }
        )
    return CellLineageRecomputation(
        records=tuple(records),
        cell_lineage_digest=_digest_json(records),
        audit_level=audit_level,
        regression_only=audit_level == L1_SYNTHETIC_COMPONENT,
    )


def canonicalize_metric_observations(
    observations: Sequence[PaperMetricObservation],
) -> tuple[tuple[PaperMetricObservation, ...], str]:
    """为 renderer/lineage/replay 生成唯一 table,row,column,id 顺序。"""

    values = tuple(observations)
    if any(not isinstance(value, PaperMetricObservation) for value in values):
        raise TypeError("observations must contain PaperMetricObservation values")
    canonical = tuple(
        sorted(
            values,
            key=lambda item: (
                item.table_id,
                item.row_identity_digest,
                item.column_id,
                item.observation_id,
            ),
        )
    )
    identities = tuple(
        (item.table_id, item.row_identity_digest, item.column_id, item.observation_id)
        for item in canonical
    )
    if len(set(identities)) != len(identities):
        raise ValueError("canonical observation sequence contains duplicate cells")
    return canonical, _digest_json([item.to_dict() for item in canonical])


def verify_external_source_locators(
    observations: Sequence[PaperMetricObservation],
    resolvers: Mapping[str, ResponseBankResolver],
) -> int:
    """仅通过 validated resolver 读取 locator；不扫描 raw tree、不补写对象。"""

    unique = {
        (
            locator.bank_root_id,
            locator.manifest_digest,
            locator.entry_id,
            locator.object_role,
            locator.object_digest,
        ): locator
        for observation in observations
        for locator in observation.source_bank_object_locators
    }
    for key in sorted(unique):
        locator = unique[key]
        resolver = resolvers.get(locator.bank_root_id)
        if resolver is None:
            raise TraceabilityBlockedError(
                f"missing external object resolver: {locator.bank_root_id}"
            )
        try:
            resolver.read_verified(locator)
        except ResponseBankBlockedError as exc:
            raise TraceabilityBlockedError(
                f"missing external object: {locator.object_digest}"
            ) from exc
    return len(unique)


def write_cell_lineage_audit(
    *,
    output_root: str | Path,
    contract: PaperMetricContract,
    observations: Sequence[PaperMetricObservation],
) -> tuple[CellLineageRecomputation, dict[str, str]]:
    result = recompute_cell_lineage(
        contract=contract,
        observations=observations,
        audit_level=L4_ARTIFACT_ROOT,
    )
    root = Path(output_root)
    path = root / PAPER_CELL_LINEAGE_PATH
    content = "".join(_canonical_json(record) + "\n" for record in result.records)
    _atomic_write_text(path, content)
    return result, {
        "path": PAPER_CELL_LINEAGE_PATH,
        "content_hash": "sha256:" + sha256(content.encode("utf-8")).hexdigest(),
    }


def digest_table_refs(refs: Sequence[Mapping[str, str]]) -> str:
    canonical = [
        {"path": str(ref["path"]), "content_hash": str(ref["content_hash"])}
        for ref in sorted(refs, key=lambda value: str(value["path"]))
    ]
    return _digest_json(canonical)


def _persist_protected_replay_input_root(
    *,
    replay_input_root: str | Path,
    canonical_direct_rows: Mapping[str, object],
    global_infrastructure_valid: bool = True,
    canonical_runtime_evidence: Sequence[Any] = (),
    requested_lineage_root_ids: Sequence[str] | None = None,
    current_trace_wrappers_by_root: Mapping[str, Sequence[Any]] | None = None,
    trace_source_bindings_by_root: Mapping[str, Sequence[Any]] | None = None,
    eligibility_facts_by_root: Mapping[str, Any] | None = None,
    source_resolvers: Mapping[str, ResponseBankResolver] | None = None,
    current_provider_object_files: Mapping[str, str | Path] | None = None,
    current_evidence_root: str | Path | None = None,
) -> ProtectedReplayInputRoot:
    """仅由 formal runner 调用，冻结 L4 direct/current/source 三类输入。"""

    runtime_evidence = tuple(canonical_runtime_evidence)
    if not runtime_evidence:
        from tokenshare.experiments.paper_direct_results import PaperDirectRootResult

        direct_root_rows = _walk_direct_root_candidates(
            canonical_direct_rows,
            PaperDirectRootResult,
        )
        if not direct_root_rows or any(
            type(row) is not PaperDirectRootResult
            or row.root_status != "not_started"
            for row in direct_root_rows
        ):
            raise TraceabilityBlockedError(
                "protected L4 replay requires persisted canonical runtime evidence"
            )
    root = Path(replay_input_root).resolve(strict=False)
    if root.exists():
        raise FileExistsError(f"replay input root already exists: {root}")
    root.mkdir(parents=True)
    direct = {
        str(table_id): tuple(
            sorted(
                rows,
                key=lambda row: _canonical_json(_json_value(row)),
            )
        )
        for table_id, rows in sorted(
            canonical_direct_rows.items(),
            key=lambda item: str(item[0]),
        )
    }
    current = {
        "global_infrastructure_valid": global_infrastructure_valid,
        "canonical_runtime_evidence": runtime_evidence,
        "requested_lineage_root_ids": (
            None
            if requested_lineage_root_ids is None
            else tuple(requested_lineage_root_ids)
        ),
        "current_trace_wrappers_by_root": (
            None
            if current_trace_wrappers_by_root is None
            else dict(current_trace_wrappers_by_root)
        ),
        "eligibility_facts_by_root": (
            None
            if eligibility_facts_by_root is None
            else dict(eligibility_facts_by_root)
        ),
    }
    source = {
        "trace_source_bindings_by_root": (
            None
            if trace_source_bindings_by_root is None
            else dict(trace_source_bindings_by_root)
        ),
        "source_resolvers": dict(source_resolvers or {}),
    }
    input_refs = {}
    for name, value in (("direct", direct), ("current", current), ("source", source)):
        path = root / f"{name}_inputs.pickle"
        content = pickle.dumps(value, protocol=5)
        _atomic_write_bytes(path, content)
        input_refs[name] = _file_ref(root, path)

    snapshots = {
        value.artifact_id: value
        for value in _walk_instances((direct, current), ArtifactIdentitySnapshot)
        if value.source_role in _CURRENT_PROVIDER_ROLES
    }
    provided_files = dict(current_provider_object_files or {})
    if snapshots and set(provided_files) != set(snapshots):
        raise TraceabilityBlockedError(
            "protected L4 replay current-provider object closure is incomplete"
        )
    if not snapshots and provided_files:
        raise TraceabilityBlockedError(
            "protected L4 replay current-provider object closure must be empty"
        )
    current_object_refs = []
    for artifact_id, snapshot in sorted(snapshots.items()):
        source_path = Path(provided_files[artifact_id])
        content = _verified_current_provider_content(source_path, snapshot)
        target = root / "current_provider_objects" / f"{artifact_id}.bin"
        _atomic_write_bytes(target, content)
        current_object_refs.append(
            {
                **_file_ref(root, target),
                "artifact_id": artifact_id,
                "source_role": snapshot.source_role,
            }
        )

    if current_evidence_root is None:
        raise TraceabilityBlockedError(
            "protected L4 replay requires persisted current formal evidence"
        )
    evidence_root = Path(current_evidence_root).resolve(strict=False)
    try:
        from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore

        FormalEvidenceStore(evidence_root)._validate_evidence_manifest()
    except (FileNotFoundError, TypeError, ValueError) as exc:
        raise TraceabilityBlockedError(
            "protected L4 replay current formal evidence is invalid"
        ) from exc
    evidence_refs = []
    for evidence_relative_path, content in _collect_formal_evidence_closure(
        evidence_root
    ):
        target = root / "current_evidence" / evidence_relative_path
        _atomic_write_bytes(target, content)
        evidence_refs.append(
            {
                **_file_ref(root, target),
                "evidence_relative_path": evidence_relative_path,
            }
        )
    if not evidence_refs:
        raise TraceabilityBlockedError(
            "protected L4 replay current formal evidence closure is empty"
        )

    external_source_refs = []
    resolver_map = dict(source_resolvers or {})
    for locator in sorted(
        _walk_instances((direct, current, source), _external_locator_type()),
        key=lambda value: (
            value.bank_root_id,
            value.entry_id,
            value.object_role,
            value.object_digest,
        ),
    ):
        resolver = resolver_map.get(locator.bank_root_id)
        if resolver is None:
            raise TraceabilityBlockedError("protected L4 source resolver is missing")
        resolver.read_verified(locator)
        external_path = (
            resolver.root_path
            / "objects"
            / locator.object_digest.removeprefix("sha256:")
        ).resolve()
        external_source_refs.append(
            {
                "external_path": external_path.as_posix(),
                "content_hash": locator.object_digest,
                "entry_id": locator.entry_id,
                "object_role": locator.object_role,
            }
        )
    descriptor_body = {
        "schema_version": "tokenshare.paper_traceability_replay_input_root.v1",
        "classification": "normal_formal_artifact_root",
        "producer": "paper_formal_runner",
        "audit_level": L4_ARTIFACT_ROOT,
        "input_refs": input_refs,
        "current_provider_object_refs": current_object_refs,
        "current_evidence_refs": evidence_refs,
        "external_source_object_refs": external_source_refs,
    }
    descriptor_digest = _digest_json(descriptor_body)
    descriptor = {**descriptor_body, "descriptor_digest": descriptor_digest}
    descriptor_path = root / _REPLAY_INPUT_DESCRIPTOR
    _atomic_write_text(descriptor_path, json.dumps(
        descriptor, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n")
    return ProtectedReplayInputRoot(
        root_path=root,
        descriptor_path=descriptor_path,
        descriptor_digest=descriptor_digest,
        classification="normal_formal_artifact_root",
        _producer_validated=True,
    )


def _load_protected_replay_inputs(
    value: ProtectedReplayInputRoot,
) -> _LoadedReplayInputs:
    if not isinstance(value, ProtectedReplayInputRoot) or not value._producer_validated:
        raise TraceabilityBlockedError("L4 replay requires protected runner descriptor")
    try:
        descriptor = json.loads(value.descriptor_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise TraceabilityBlockedError("replay input closure descriptor is missing") from exc
    digest_body = dict(descriptor)
    persisted_digest = digest_body.pop("descriptor_digest", None)
    if (
        persisted_digest != value.descriptor_digest
        or _digest_json(digest_body) != value.descriptor_digest
        or descriptor.get("classification") != value.classification
        or descriptor.get("producer") != "paper_formal_runner"
        or descriptor.get("audit_level") != L4_ARTIFACT_ROOT
    ):
        raise TraceabilityBlockedError("replay input closure descriptor mismatch")
    loaded = {}
    current_evidence_files = []
    try:
        for name in ("direct", "current", "source"):
            ref = descriptor["input_refs"][name]
            content = _verified_root_ref(value.root_path, ref)
            loaded[name] = pickle.loads(content)
        for ref in descriptor["current_provider_object_refs"]:
            _verified_root_ref(value.root_path, ref)
        for ref in descriptor["current_evidence_refs"]:
            relative_path = Path(str(ref["evidence_relative_path"]))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise TraceabilityBlockedError(
                    "replay input closure evidence path is invalid"
                )
            current_evidence_files.append(
                (relative_path.as_posix(), _verified_root_ref(value.root_path, ref))
            )
        for ref in descriptor["external_source_object_refs"]:
            path = Path(ref["external_path"])
            if _sha256_file(path) != ref["content_hash"]:
                raise TraceabilityBlockedError("replay input closure source mismatch")
    except (KeyError, FileNotFoundError, pickle.UnpicklingError, EOFError) as exc:
        raise TraceabilityBlockedError("replay input closure is missing or invalid") from exc
    if not all(isinstance(loaded[name], Mapping) for name in loaded):
        raise TraceabilityBlockedError("replay input closure did not restore typed mappings")
    return _LoadedReplayInputs(
        direct=loaded["direct"],
        current=loaded["current"],
        source=loaded["source"],
        current_evidence_files=tuple(current_evidence_files),
    )


def recompute_paper_traceability(
    *,
    output_root: str | Path,
    replay_input_root: ProtectedReplayInputRoot,
    registry: Any = None,
    contract: PaperMetricContract | None = None,
) -> PaperTraceabilityReplayResult:
    """从同一组 immutable direct/current/source inputs 独立重跑 17→20→21。"""

    from tokenshare.experiments.paper_formal_metrics import (
        recompute_paper_formal_metrics,
    )
    from tokenshare.experiments.paper_formal_report import (
        generate_paper_formal_report,
    )

    loaded = _load_protected_replay_inputs(replay_input_root)
    output_path = Path(output_root)
    if output_path.exists() and any(output_path.iterdir()):
        raise FileExistsError(f"traceability output root is not fresh: {output_path}")
    for relative_path, content in loaded.current_evidence_files:
        _atomic_write_bytes(output_path / relative_path, content)
    current_inputs = loaded.current
    source_inputs = loaded.source
    current_contract = contract or load_paper_metric_contract()
    metrics = recompute_paper_formal_metrics(
        output_root,
        loaded.direct,
        global_infrastructure_valid=bool(
            current_inputs["global_infrastructure_valid"]
        ),
        registry=registry,
        contract=current_contract,
        canonical_runtime_evidence=current_inputs["canonical_runtime_evidence"],
        requested_lineage_root_ids=current_inputs["requested_lineage_root_ids"],
        current_trace_wrappers_by_root=current_inputs[
            "current_trace_wrappers_by_root"
        ],
        trace_source_bindings_by_root=source_inputs[
            "trace_source_bindings_by_root"
        ],
        eligibility_facts_by_root=current_inputs["eligibility_facts_by_root"],
    )
    locators_present = any(
        observation.source_bank_object_locators
        for observation in metrics.metric_observations
    )
    if locators_present:
        verify_external_source_locators(
            metrics.metric_observations,
            source_inputs["source_resolvers"],
        )
    report = generate_paper_formal_report(
        output_root=output_root,
        metrics=metrics,
        contract=current_contract,
    )
    if report.tables_digest is None or report.cell_lineage_digest is None:
        raise TraceabilityBlockedError("artifact-root traceability publication blocked")
    return PaperTraceabilityReplayResult(
        output_root=Path(output_root),
        observations_digest=report.observations_digest,
        tables_digest=report.tables_digest,
        cell_lineage_digest=report.cell_lineage_digest,
        report=report,
        loaded_direct_inputs=loaded.direct,
        loaded_current_inputs=loaded.current,
        loaded_source_inputs=loaded.source,
    )


def promote_completed_report_generation(
    *,
    staging_root: Path,
    output_root: Path,
    renderer_refs: Sequence[Mapping[str, str]],
    cell_lineage_ref: Mapping[str, str],
    report_refs: Sequence[Mapping[str, str]],
) -> None:
    """以一个 backup/promote/rollback transaction 晋升完整 renderer/report。"""

    from tokenshare.experiments.paper_exp5_artifacts import (
        PAPER_RENDERER_AUDIT_FILE,
        PAPER_RENDERER_MANIFEST_FILE,
        PAPER_TABLE_CSV_FILES,
        PAPER_TABLE_TEX_FILES,
        _RETIRED_OUTPUTS,
    )

    renderer_expected = {
        *PAPER_TABLE_CSV_FILES,
        *PAPER_TABLE_TEX_FILES,
        PAPER_RENDERER_AUDIT_FILE,
        PAPER_RENDERER_MANIFEST_FILE,
    }
    report_expected = {
        "audit/secret_scan_report.json",
        "audit/paper_eligibility_report.json",
        "audit/paper_formal_report_manifest.v2.json",
        "formal_report_result.json",
    }
    renderer_paths = {str(ref.get("path")) for ref in renderer_refs}
    report_paths = {str(ref.get("path")) for ref in report_refs}
    if renderer_paths != renderer_expected or report_paths != report_expected:
        raise ValueError("complete report generation owned-set mismatch")
    all_refs = tuple(renderer_refs) + (cell_lineage_ref,) + tuple(report_refs)
    expected = renderer_expected | report_expected | {PAPER_CELL_LINEAGE_PATH}
    contents: dict[str, bytes] = {}
    for ref in all_refs:
        relative_path = str(ref.get("path"))
        path = staging_root / relative_path
        content = path.read_bytes()
        if (
            relative_path not in expected
            or "sha256:" + sha256(content).hexdigest() != ref.get("content_hash")
        ):
            raise ValueError("staged complete-report artifact mismatch")
        contents[relative_path] = content
    if set(contents) != expected:
        raise ValueError("complete report generation is incomplete")

    owned = tuple(sorted(expected | set(_RETIRED_OUTPUTS)))
    root = output_root.resolve(strict=False)
    present_before = {
        relative_path for relative_path in owned if (root / relative_path).is_file()
    }
    for relative_path in owned:
        target = root / relative_path
        if target.exists() and not target.is_file():
            raise ValueError(f"complete report owned path is not a file: {relative_path}")
    root.mkdir(parents=True, exist_ok=True)
    transaction_stage = Path(
        tempfile.mkdtemp(prefix=".paper-report-transaction-stage-", dir=root)
    )
    backup = Path(
        tempfile.mkdtemp(prefix=".paper-report-transaction-backup-", dir=root)
    )
    moved_to_backup: list[str] = []
    try:
        for relative_path, content in contents.items():
            _atomic_write_bytes(transaction_stage / relative_path, content)
        for relative_path in expected:
            staged = transaction_stage / relative_path
            if not staged.is_file():
                raise RuntimeError(
                    f"staged complete report output is missing: {relative_path}"
                )
        for relative_path in owned:
            target = root / relative_path
            if target.is_file():
                saved = backup / relative_path
                saved.parent.mkdir(parents=True, exist_ok=True)
                target.replace(saved)
                moved_to_backup.append(relative_path)
        final_manifests = (
            PAPER_RENDERER_MANIFEST_FILE,
            "audit/paper_formal_report_manifest.v2.json",
        )
        promotion_order = tuple(
            sorted(expected - set(final_manifests))
        ) + final_manifests
        for relative_path in promotion_order:
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            (transaction_stage / relative_path).replace(target)
    except Exception:
        for relative_path in expected:
            target = root / relative_path
            if relative_path not in present_before and target.is_file():
                target.unlink()
        for relative_path in reversed(moved_to_backup):
            saved = backup / relative_path
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if saved.is_file():
                saved.replace(target)
        raise
    finally:
        shutil.rmtree(transaction_stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _walk_instances(value: Any, expected_type: type[Any]) -> tuple[Any, ...]:
    found: list[Any] = []
    seen: set[int] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(item, expected_type):
            found.append(item)
            continue
        if isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, (tuple, list)):
            stack.extend(item)
        elif is_dataclass(item):
            stack.extend(
                getattr(item, field.name) for field in fields(item) if field.repr
            )
    return tuple(found)


def _walk_direct_root_candidates(
    value: Any,
    direct_root_type: type[Any],
) -> tuple[Any, ...]:
    """收集 exact direct row 以及试图冒充该 ABI 的 duck/subclass。"""

    found: list[Any] = []
    seen: set[int] = set()
    stack = [value]
    identity_fields = (
        "preregistered_root_run_id",
        "experiment_id",
        "condition_id",
        "case_id",
        "evidence_class",
        "root_status",
    )
    while stack:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(item, direct_root_type) or (
            not isinstance(item, Mapping)
            and all(hasattr(item, field_name) for field_name in identity_fields)
        ):
            found.append(item)
            continue
        if isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, (tuple, list)):
            stack.extend(item)
        elif is_dataclass(item):
            stack.extend(
                getattr(item, field.name) for field in fields(item) if field.repr
            )
    return tuple(found)


def _external_locator_type() -> type[Any]:
    from tokenshare.experiments.paper_models import ExternalBankObjectLocator

    return ExternalBankObjectLocator


def _verified_current_provider_content(
    path: Path,
    snapshot: ArtifactIdentitySnapshot,
) -> bytes:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise TraceabilityBlockedError(
            "protected L4 current-provider object is missing"
        ) from exc
    if (
        "sha256:" + sha256(content).hexdigest() != snapshot.content_hash
        or len(content) != snapshot.size_bytes
    ):
        raise TraceabilityBlockedError(
            "protected L4 current-provider object identity mismatch"
        )
    return content


def _collect_formal_evidence_closure(
    evidence_root: Path,
) -> tuple[tuple[str, bytes], ...]:
    """只沿 v2 manifest/current/generation/artifact refs 收集正式输入闭包。"""

    manifest_path = evidence_root / "evidence_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise TraceabilityBlockedError(
            "protected L4 formal evidence manifest is missing or invalid"
        ) from exc
    if manifest.get("schema_version") != "tokenshare.paper_evidence_manifest.v2":
        raise TraceabilityBlockedError(
            "protected L4 formal evidence requires manifest v2"
        )
    collected: dict[str, bytes] = {}

    def add_path(relative_path: str) -> bytes:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise TraceabilityBlockedError(
                "protected L4 formal evidence path is invalid"
            )
        path = (evidence_root / relative).resolve(strict=False)
        root = evidence_root.resolve(strict=False)
        if root != path and root not in path.parents:
            raise TraceabilityBlockedError(
                "protected L4 formal evidence path escapes root"
            )
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise TraceabilityBlockedError(
                "protected L4 formal evidence reachable file is missing"
            ) from exc
        normalized = relative.as_posix()
        prior = collected.setdefault(normalized, content)
        if prior != content:
            raise TraceabilityBlockedError(
                "protected L4 formal evidence duplicate path mismatch"
            )
        return content

    def add_file_ref(
        ref: Any,
        *,
        relative_path: str | None = None,
    ) -> bytes:
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence file ref is invalid"
            )
        path = str(ref["path"]) if relative_path is None else relative_path
        content = add_path(path)
        if (
            ref.get("content_sha256") != "sha256:" + sha256(content).hexdigest()
            or ref.get("size") != len(content)
        ):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence file ref digest mismatch"
            )
        return content

    add_path("evidence_manifest.json")
    static_names = {
        "conditions.jsonl",
        "input_catalog_manifest.json",
        "paper_dispatch_plans.json",
        "run_budget.json",
        "suite_manifest.json",
    }
    static_refs = manifest.get("files")
    condition_entries = manifest.get("conditions")
    if not isinstance(static_refs, list) or not isinstance(condition_entries, list):
        raise TraceabilityBlockedError(
            "protected L4 formal evidence inventories are invalid"
        )
    for ref in static_refs:
        if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence static ref is invalid"
            )
        relative = Path(str(ref["path"]))
        is_experiment_manifest = (
            len(relative.parts) == 3
            and relative.parts[0] == "experiments"
            and relative.parts[2] == "experiment_manifest.json"
        )
        if relative.as_posix() not in static_names and not is_experiment_manifest:
            raise TraceabilityBlockedError(
                "protected L4 formal evidence contains derived or forbidden path"
            )
        add_file_ref(ref)

    run_files = {
        "run_manifest.json",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
    }
    for entry in condition_entries:
        if not isinstance(entry, Mapping):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence condition entry is invalid"
            )
        condition_ref = entry.get("condition_manifest_ref")
        if not isinstance(condition_ref, Mapping):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence condition ref is missing"
            )
        condition_relative = Path(str(condition_ref.get("path", "")))
        if not (
            len(condition_relative.parts) == 6
            and condition_relative.parts[0] == "experiments"
            and condition_relative.parts[2] == "runs"
            and condition_relative.name == "condition_manifest.json"
        ):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence condition path is forbidden"
            )
        condition = json.loads(add_file_ref(condition_ref).decode("utf-8"))
        current_ref = condition.get("current_ref")
        generation_ref = condition.get("generation_manifest_ref")
        if not isinstance(current_ref, Mapping) or not isinstance(
            generation_ref, Mapping
        ):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence condition closure is incomplete"
            )
        run_root = condition_relative.parent
        if Path(str(current_ref.get("path", ""))) != run_root / "CURRENT.json":
            raise TraceabilityBlockedError(
                "protected L4 formal evidence CURRENT path is forbidden"
            )
        add_file_ref(current_ref)
        head_manifest_path = Path(str(generation_ref.get("path", "")))
        if not (
            head_manifest_path.parent.parent == run_root / ".generations"
            and head_manifest_path.name == "generation_manifest.json"
        ):
            raise TraceabilityBlockedError(
                "protected L4 formal evidence generation path is forbidden"
            )
        next_manifest_path: Path | None = head_manifest_path
        next_manifest_ref: Mapping[str, Any] | None = generation_ref
        seen_generations: set[str] = set()
        while next_manifest_path is not None:
            generation_id = next_manifest_path.parent.name
            if generation_id in seen_generations:
                raise TraceabilityBlockedError(
                    "protected L4 formal evidence generation cycle"
                )
            seen_generations.add(generation_id)
            content = (
                add_file_ref(next_manifest_ref)
                if next_manifest_ref is not None
                else add_path(next_manifest_path.as_posix())
            )
            generation = json.loads(content.decode("utf-8"))
            generation_root = next_manifest_path.parent
            file_refs = generation.get("files")
            if not isinstance(file_refs, list):
                raise TraceabilityBlockedError(
                    "protected L4 formal evidence generation inventory is invalid"
                )
            artifact_index_content: bytes | None = None
            for file_ref in file_refs:
                relative_file = str(file_ref.get("path", "")) if isinstance(
                    file_ref, Mapping
                ) else ""
                if relative_file not in run_files:
                    raise TraceabilityBlockedError(
                        "protected L4 formal evidence generation contains forbidden role"
                    )
                file_content = add_file_ref(
                    file_ref,
                    relative_path=(generation_root / relative_file).as_posix(),
                )
                if relative_file == "artifacts/artifact_index.jsonl":
                    artifact_index_content = file_content
            if artifact_index_content is None:
                raise TraceabilityBlockedError(
                    "protected L4 formal evidence artifact inventory is missing"
                )
            for line in artifact_index_content.decode("utf-8").splitlines():
                record = json.loads(line)
                payload_relative = Path(str(record.get("path", "")))
                legacy_artifact_path = (
                    len(payload_relative.parts) == 7
                    and payload_relative.parts[:5] == run_root.parts
                    and payload_relative.parts[5] == "artifacts"
                    and payload_relative.suffix == ".bin"
                )
                current_artifact_path = (
                    len(payload_relative.parts) == 8
                    and payload_relative.parts[:5] == run_root.parts
                    and payload_relative.parts[5] == "artifacts"
                )
                if not (legacy_artifact_path or current_artifact_path):
                    raise TraceabilityBlockedError(
                        "protected L4 formal evidence artifact path is forbidden"
                    )
                payload = add_path(payload_relative.as_posix())
                if record.get("content_hash") != (
                    "sha256:" + sha256(payload).hexdigest()
                ):
                    raise TraceabilityBlockedError(
                        "protected L4 formal evidence artifact digest mismatch"
                    )
                expected_size = record.get("size_bytes")
                if expected_size is not None and (
                    isinstance(expected_size, bool)
                    or not isinstance(expected_size, int)
                    or expected_size != len(payload)
                ):
                    raise TraceabilityBlockedError(
                        "protected L4 formal evidence artifact digest mismatch"
                    )
            parent_id = generation.get("parent_generation_id")
            if parent_id is None:
                next_manifest_path = None
                next_manifest_ref = None
            elif isinstance(parent_id, str) and parent_id:
                next_manifest_path = (
                    run_root
                    / ".generations"
                    / parent_id
                    / "generation_manifest.json"
                )
                next_manifest_ref = None
            else:
                raise TraceabilityBlockedError(
                    "protected L4 formal evidence parent generation is invalid"
                )
    return tuple(sorted(collected.items()))


def _file_ref(root: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "content_hash": "sha256:" + sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _verified_root_ref(root: Path, ref: Mapping[str, Any]) -> bytes:
    path = (root / str(ref["path"])).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_root != path and resolved_root not in path.parents:
        raise TraceabilityBlockedError("replay input closure path escapes root")
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise TraceabilityBlockedError("replay input closure file is missing") from exc
    if (
        len(content) != ref.get("size_bytes")
        or "sha256:" + sha256(content).hexdigest() != ref.get("content_hash")
    ):
        raise TraceabilityBlockedError("replay input closure digest mismatch")
    return content


def _sha256_file(path: Path) -> str:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise TraceabilityBlockedError("replay input closure source is missing") from exc
    return "sha256:" + sha256(content).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if field.repr
        }
    raise TypeError(f"unsupported traceability value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest_json(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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


__all__ = [
    "CellLineageRecomputation",
    "L1_SYNTHETIC_COMPONENT",
    "L4_ARTIFACT_ROOT",
    "PAPER_CELL_LINEAGE_PATH",
    "PaperTraceabilityReplayResult",
    "ProtectedReplayInputRoot",
    "TraceabilityBlockedError",
    "canonicalize_metric_observations",
    "digest_table_refs",
    "promote_completed_report_generation",
    "recompute_cell_lineage",
    "recompute_paper_traceability",
    "verify_external_source_locators",
    "write_cell_lineage_audit",
]
