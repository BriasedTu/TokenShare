"""正式论文 full plan 的只读冻结与完整性验证。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tokenshare.core.models import (
    Attempt,
    AttemptState,
    Lease,
    LeaseState,
    ProtocolConfig,
)
from tokenshare.executors.ai_api import prepare_ai_api_outbound_request
from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequest,
    validate_prepared_request,
)
from tokenshare.experiments.paper_budget import build_paper_budget_split_profile
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    default_lean_paper_environment_manifest,
)
from tokenshare.experiments.paper_catalog_execution_view import (
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_dispatcher import (
    PaperExperimentDispatchPlan,
    plan_paper_experiment,
    registered_paper_experiment_ids,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    EXP3_EXPERIMENT_ID,
    formal_exp3_scheduled_target_unit_ids_by_case,
)
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
    PaperExecutionContext,
)
from tokenshare.experiments.paper_formal_runner import (
    APPROVED_ENDPOINT_BINDINGS_KEY,
    validate_paper_formal_root_case_filter,
    validate_paper_formal_suite_plan,
)
from tokenshare.experiments.paper_model_identity import (
    PaperModelEndpointIdentity,
    build_fixed_entry_executor_requirements,
    prepare_fixed_entry_execution_config,
    validate_condition_fixed_entry_identity,
    validate_fixed_entry_config_identity,
)
from tokenshare.experiments.paper_models import (
    FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID,
    PaperBudgetResult,
    PaperExperimentCondition,
    digest_json,
)
from tokenshare.experiments.paper_response_bank import replacement_slots_for
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_ID as FACTORIZATION_PLUGIN_ID,
    PLUGIN_VERSION as FACTORIZATION_PLUGIN_VERSION,
)
from tokenshare.plugins.lean_proof.schemas import (
    PLUGIN_ID as LEAN_PLUGIN_ID,
    PLUGIN_VERSION as LEAN_PLUGIN_VERSION,
)
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class FormalEndpointControls:
    provider_config_id: str
    model_entry_id: str
    provider_family: str
    provider_model_id: str
    source_provider_config_digest: str
    model_endpoint_identity_digest: str
    reasoning_profile_id: str
    max_tokens: int
    timeout_seconds: int
    max_provider_attempts: int
    stream: bool
    request_controls_digest: str


@dataclass(frozen=True, kw_only=True)
class FormalConditionSnapshot:
    condition: PaperExperimentCondition
    binding: FrozenConditionSelectionBinding
    endpoint_controls: FormalEndpointControls


@dataclass(frozen=True, kw_only=True)
class FormalRootSnapshot:
    condition: PaperExperimentCondition
    binding: FrozenConditionSelectionBinding
    case_id: str
    case_record_digest: str
    condition_digest: str
    selection_digest: str
    seed: int
    repeat_id: int
    split_profile_id: str | None
    split_profile_digest: str
    planned_ai_unit_ids: tuple[str, ...]
    plugin_id: str
    plugin_version: str
    endpoint_controls: FormalEndpointControls


@dataclass(frozen=True, kw_only=True)
class FormalPlanSnapshot:
    conditions: tuple[FormalConditionSnapshot, ...]
    roots: tuple[FormalRootSnapshot, ...]
    condition_count: int
    root_run_count: int
    first_attempt_ai_unit_count: int
    provider_calls_made: int
    budget_digest: str
    schema_version: str = "tokenshare.paper_formal_plan_snapshot.v1"

    @property
    def snapshot_digest(self) -> str:
        return digest_json(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "snapshot_digest": self.snapshot_digest}

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "condition_count": self.condition_count,
            "root_run_count": self.root_run_count,
            "first_attempt_ai_unit_count": self.first_attempt_ai_unit_count,
            "provider_calls_made": self.provider_calls_made,
            "budget_digest": self.budget_digest,
            "conditions": [
                {
                    "condition_id": item.condition.condition_id,
                    "condition_digest": item.condition.condition_digest,
                    "selection_id": item.binding.selection.selection_id,
                    "selection_digest": item.binding.selection.selection_digest,
                    "endpoint_controls": _endpoint_controls_body(
                        item.endpoint_controls
                    ),
                }
                for item in self.conditions
            ],
            "roots": [
                {
                    "condition_id": item.condition.condition_id,
                    "condition_digest": item.condition_digest,
                    "selection_id": item.binding.selection.selection_id,
                    "selection_digest": item.selection_digest,
                    "case_id": item.case_id,
                    "case_record_digest": item.case_record_digest,
                    "seed": item.seed,
                    "repeat_id": item.repeat_id,
                    "split_profile_id": item.split_profile_id,
                    "split_profile_digest": item.split_profile_digest,
                    "planned_ai_unit_ids": list(item.planned_ai_unit_ids),
                    "plugin_id": item.plugin_id,
                    "plugin_version": item.plugin_version,
                    "endpoint_controls_digest": (
                        item.endpoint_controls.request_controls_digest
                    ),
                    "model_endpoint_identity_digest": (
                        item.endpoint_controls.model_endpoint_identity_digest
                    ),
                }
                for item in self.roots
            ],
        }


@dataclass(frozen=True, kw_only=True)
class FormalRepresentativeCoverage:
    """只引用 full snapshot authority 的 repeat/root 执行子集。"""

    conditions: tuple[PaperExperimentCondition, ...]
    bindings: tuple[FrozenConditionSelectionBinding, ...]
    roots: tuple[FormalRootSnapshot, ...]
    root_case_filter: Mapping[str, tuple[str, ...]]
    source_snapshot_digest: str
    condition_count: int
    root_run_count: int
    provider_calls_made: int = 0
    schema_version: str = "tokenshare.paper_formal_representative_coverage.v1"

    def __post_init__(self) -> None:
        conditions = tuple(self.conditions)
        bindings = tuple(self.bindings)
        roots = tuple(self.roots)
        root_filter = {
            str(condition_id): tuple(case_ids)
            for condition_id, case_ids in self.root_case_filter.items()
        }
        if not conditions or len(conditions) != len(bindings):
            raise ValueError("representative coverage condition/binding mismatch")
        if self.condition_count != len(conditions):
            raise ValueError("representative coverage condition count mismatch")
        if self.root_run_count != len(roots):
            raise ValueError("representative coverage root count mismatch")
        if set(root_filter) != {condition.condition_id for condition in conditions}:
            raise ValueError("representative coverage root filter mismatch")
        if self.provider_calls_made != 0:
            raise ValueError("representative coverage must not call providers")
        _required_digest(self.source_snapshot_digest, "source_snapshot_digest")
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "root_case_filter", MappingProxyType(root_filter))

    @property
    def coverage_digest(self) -> str:
        return digest_json(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "coverage_digest": self.coverage_digest}

    def _body(self) -> dict[str, Any]:
        roots_by_condition: dict[str, list[FormalRootSnapshot]] = {}
        for root in self.roots:
            roots_by_condition.setdefault(root.condition.condition_id, []).append(root)
        return {
            "schema_version": self.schema_version,
            "source_snapshot_digest": self.source_snapshot_digest,
            "condition_count": self.condition_count,
            "root_run_count": self.root_run_count,
            "provider_calls_made": self.provider_calls_made,
            "conditions": [
                {
                    "experiment_id": condition.experiment_id,
                    "condition_id": condition.condition_id,
                    "condition_digest": condition.condition_digest,
                    "selection_id": binding.selection.selection_id,
                    "selection_digest": binding.selection.selection_digest,
                    "seed": condition.seed,
                    "repeat_id": condition.repeat_id,
                    "split_profile_id": getattr(
                        binding.selection,
                        "split_profile_id",
                        None,
                    ),
                    "root_case_ids": list(
                        self.root_case_filter[condition.condition_id]
                    ),
                }
                for condition, binding in zip(
                    self.conditions,
                    self.bindings,
                    strict=True,
                )
            ],
            "roots": [
                {
                    "condition_id": root.condition.condition_id,
                    "condition_digest": root.condition_digest,
                    "selection_digest": root.selection_digest,
                    "case_id": root.case_id,
                    "seed": root.seed,
                    "repeat_id": root.repeat_id,
                    "split_profile_id": root.split_profile_id,
                    "split_profile_digest": root.split_profile_digest,
                    "plugin_id": root.plugin_id,
                    "plugin_version": root.plugin_version,
                    "endpoint_controls_digest": (
                        root.endpoint_controls.request_controls_digest
                    ),
                }
                for condition in self.conditions
                for root in roots_by_condition[condition.condition_id]
            ],
        }


@dataclass(frozen=True, kw_only=True)
class FormalPreparedRequestRecord:
    """一条正式 first-attempt AI unit 的 exact prepared request。"""

    condition: PaperExperimentCondition
    binding: FrozenConditionSelectionBinding
    case_id: str
    case_record_digest: str
    planned_ai_unit_id: str
    sample_slot_index: int
    base_replacement_slot: int
    replacement_slot_ids: tuple[int, ...]
    replacement_policy_id: str
    source_provider_config_digest: str
    prepared_execution_config_digest: str
    provider_family: str
    provider_config_id: str
    model_entry_id: str
    provider_model_id: str
    reasoning_profile_id: str
    model_endpoint_identity_digest: str
    request_max_tokens: int
    request_timeout_seconds: int
    request_max_provider_attempts: int
    request_controls_digest: str
    prompt_profile_digest: str
    provider_request_identity: Mapping[str, Any]
    prepared_request: PreparedOutboundRequest
    provider_calls_made: int = 0

    @property
    def body_digest(self) -> str:
        return self.prepared_request.body_digest

    @property
    def inference_request_digest(self) -> str:
        return self.prepared_request.inference_request_digest

    @property
    def request_identity_digest(self) -> str:
        return digest_json(self._body())

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "request_identity_digest": self.request_identity_digest}

    def _body(self) -> dict[str, Any]:
        prepared = validate_prepared_request(self.prepared_request)
        _validate_provider_request_identity_against_prepared(
            provider_request_identity=self.provider_request_identity,
            prepared=prepared,
            provider_family=self.provider_family,
        )
        return {
            "condition_id": self.condition.condition_id,
            "condition_digest": self.condition.condition_digest,
            "selection_id": self.binding.selection.selection_id,
            "selection_digest": self.binding.selection.selection_digest,
            "case_id": self.case_id,
            "case_record_digest": self.case_record_digest,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "sample_slot_index": self.sample_slot_index,
            "base_replacement_slot": self.base_replacement_slot,
            "replacement_slot_ids": list(self.replacement_slot_ids),
            "replacement_policy_id": self.replacement_policy_id,
            "source_provider_config_digest": self.source_provider_config_digest,
            "prepared_execution_config_digest": self.prepared_execution_config_digest,
            "provider_family": self.provider_family,
            "provider_config_id": self.provider_config_id,
            "model_entry_id": self.model_entry_id,
            "provider_model_id": self.provider_model_id,
            "reasoning_profile_id": self.reasoning_profile_id,
            "model_endpoint_identity_digest": self.model_endpoint_identity_digest,
            "request_max_tokens": self.request_max_tokens,
            "request_timeout_seconds": self.request_timeout_seconds,
            "request_max_provider_attempts": self.request_max_provider_attempts,
            "request_controls_digest": self.request_controls_digest,
            "prompt_profile_digest": self.prompt_profile_digest,
            "provider_request_identity": dict(self.provider_request_identity),
            "prepared_request": prepared.provenance_dict(),
            "provider_calls_made": self.provider_calls_made,
        }


@dataclass(frozen=True, kw_only=True)
class FormalPreparedRequestInventory:
    """完整正式计划的 first-attempt prepared request inventory。"""

    records: tuple[FormalPreparedRequestRecord, ...]
    record_count: int
    unique_inference_request_count: int
    provider_calls_made: int
    source_snapshot_digest: str
    schema_version: str = "tokenshare.paper_formal_prepared_request_inventory.v1"

    @property
    def inventory_digest(self) -> str:
        return digest_json(
            {
                "schema_version": self.schema_version,
                "record_count": self.record_count,
                "unique_inference_request_count": self.unique_inference_request_count,
                "provider_calls_made": self.provider_calls_made,
                "source_snapshot_digest": self.source_snapshot_digest,
                "request_identity_digests": [
                    record.request_identity_digest for record in self.records
                ],
            }
        )


@dataclass(frozen=True, kw_only=True)
class _PreparedRuntimeTemplate:
    planned_ai_unit_id: str
    source_provider_config_digest: str
    prepared_execution_config_digest: str
    provider_request_identity: Mapping[str, Any]
    prepared_request: PreparedOutboundRequest


@dataclass(frozen=True, kw_only=True)
class _FormalPreparedRecordAuthority:
    source_config: Any
    source_entry: Any
    prepared_config: Any


def validate_formal_prepared_request_record(
    *,
    record: FormalPreparedRequestRecord,
    root: FormalRootSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> None:
    """独立重建 runtime template，再审计一条持久化 prepared record。"""

    if type(record) is not FormalPreparedRequestRecord:
        raise TypeError("record must be a FormalPreparedRequestRecord")
    if type(root) is not FormalRootSnapshot:
        raise TypeError("root must be a FormalRootSnapshot")
    templates = _independently_prepare_formal_root_templates(
        root=root,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        artifact_root=Path(planning_artifact_root),
    )
    _validate_formal_prepared_records_with_templates(
        records=(record,),
        root=root,
        templates=templates,
        ai_api_configs=ai_api_configs,
        require_complete_root=False,
    )


def validate_formal_prepared_request_inventory(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> None:
    """从正式 catalog/runtime 独立重建 templates 后审计 persisted inventory。"""

    if type(inventory) is not FormalPreparedRequestInventory:
        raise TypeError("inventory must be a FormalPreparedRequestInventory")
    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("snapshot must be a FormalPlanSnapshot")
    _validate_formal_prepared_inventory_metadata(
        inventory=inventory,
        snapshot=snapshot,
    )
    templates_by_root_key = _independently_prepare_formal_snapshot_templates(
        snapshot=snapshot,
        catalog_manifest=catalog_manifest,
        ai_api_configs=ai_api_configs,
        artifact_root=Path(planning_artifact_root),
    )
    _validate_formal_prepared_inventory_with_templates(
        inventory=inventory,
        snapshot=snapshot,
        ai_api_configs=ai_api_configs,
        templates_by_root_key=templates_by_root_key,
    )


def _validate_formal_prepared_inventory_metadata(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
) -> None:
    expected_count = sum(len(root.planned_ai_unit_ids) for root in snapshot.roots)
    if (
        inventory.schema_version
        != "tokenshare.paper_formal_prepared_request_inventory.v1"
        or snapshot.provider_calls_made != 0
        or inventory.provider_calls_made != 0
        or len(snapshot.roots) != snapshot.root_run_count
        or len(snapshot.conditions) != snapshot.condition_count
        or expected_count != snapshot.first_attempt_ai_unit_count
        or inventory.record_count != len(inventory.records)
        or inventory.record_count != expected_count
        or inventory.source_snapshot_digest != snapshot.snapshot_digest
    ):
        raise ValueError("formal prepared inventory metadata drift")


def _validate_formal_prepared_inventory_with_templates(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
    ai_api_configs: Mapping[str, Any],
    templates_by_root_key: Mapping[
        tuple[str, str], tuple[_PreparedRuntimeTemplate, ...]
    ],
) -> None:
    _validate_formal_prepared_inventory_metadata(
        inventory=inventory,
        snapshot=snapshot,
    )

    roots_by_key: dict[tuple[str, str, str], FormalRootSnapshot] = {}
    authority_by_root_key: dict[
        tuple[str, str], _FormalPreparedRecordAuthority
    ] = {}
    expected_keys: set[tuple[str, str, str]] = set()
    conditions_by_id = {
        item.condition.condition_id: item for item in snapshot.conditions
    }
    if len(conditions_by_id) != len(snapshot.conditions):
        raise ValueError("formal prepared inventory condition coverage drift")
    for root in snapshot.roots:
        condition_authority = conditions_by_id.get(root.condition.condition_id)
        if (
            condition_authority is None
            or condition_authority.condition is not root.condition
            or condition_authority.binding is not root.binding
            or condition_authority.endpoint_controls != root.endpoint_controls
        ):
            raise ValueError("formal prepared inventory condition authority drift")
        root_key = (root.condition.condition_id, root.case_id)
        authority_by_root_key[root_key] = _resolve_formal_prepared_record_authority(
            root=root,
            ai_api_configs=ai_api_configs,
        )
        for planned_ai_unit_id in root.planned_ai_unit_ids:
            key = (*root_key, planned_ai_unit_id)
            if key in expected_keys:
                raise ValueError("formal prepared inventory snapshot has duplicate unit")
            expected_keys.add(key)
            roots_by_key[key] = root

    actual_keys: set[tuple[str, str, str]] = set()
    for record in inventory.records:
        key = (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        )
        if key in actual_keys or key not in roots_by_key:
            raise ValueError("formal prepared inventory record coverage drift")
        actual_keys.add(key)
        try:
            root = roots_by_key[key]
            authority = authority_by_root_key[
                (root.condition.condition_id, root.case_id)
            ]
            _validate_formal_prepared_record_fields(
                record=record,
                root=root,
                expected_template=_template_for_planned_unit(
                    templates_by_root_key[
                        (root.condition.condition_id, root.case_id)
                    ],
                    record.planned_ai_unit_id,
                ),
                source_config=authority.source_config,
                source_entry=authority.source_entry,
                prepared_config=authority.prepared_config,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"formal prepared inventory record drift: {exc}"
            ) from exc
    if actual_keys != expected_keys:
        raise ValueError("formal prepared inventory unit coverage drift")
    unique_count = len(
        {record.inference_request_digest for record in inventory.records}
    )
    if unique_count != inventory.unique_inference_request_count:
        raise ValueError("formal prepared inventory unique request count drift")


def freeze_formal_root_prepared_requests(
    *,
    root: FormalRootSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> tuple[FormalPreparedRequestRecord, ...]:
    """经正式 runtime adapter 冻结单个 full-plan root 的 base requests。"""

    if not isinstance(root, FormalRootSnapshot):
        raise TypeError("root must be a FormalRootSnapshot")
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    try:
        case = cases_by_id[root.case_id]
    except KeyError as exc:
        raise ValueError("formal prepared root is absent from catalog") from exc
    templates = _prepare_formal_root_templates(
        root=root,
        case=case,
        ai_api_configs=ai_api_configs,
        artifact_root=Path(planning_artifact_root),
    )
    records = _records_from_templates(root=root, templates=templates)
    _validate_formal_prepared_records_with_templates(
        records=records,
        root=root,
        templates=templates,
        ai_api_configs=ai_api_configs,
        require_complete_root=True,
    )
    return records


def freeze_paper_formal_prepared_request_inventory(
    *,
    snapshot: FormalPlanSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> FormalPreparedRequestInventory:
    """冻结 full plan 全部 first-attempt request；不读取 secret 或调用 provider。"""

    if not isinstance(snapshot, FormalPlanSnapshot):
        raise TypeError("snapshot must be a FormalPlanSnapshot")
    if (
        snapshot.provider_calls_made != 0
        or len(snapshot.roots) != snapshot.root_run_count
        or sum(len(root.planned_ai_unit_ids) for root in snapshot.roots)
        != snapshot.first_attempt_ai_unit_count
    ):
        raise ValueError("formal plan snapshot is not internally complete")
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    artifact_root = Path(planning_artifact_root)
    template_cache: dict[str, tuple[_PreparedRuntimeTemplate, ...]] = {}
    templates_by_root_key: dict[
        tuple[str, str], tuple[_PreparedRuntimeTemplate, ...]
    ] = {}
    records: list[FormalPreparedRequestRecord] = []
    for root in snapshot.roots:
        try:
            case = cases_by_id[root.case_id]
        except KeyError as exc:
            raise ValueError("formal prepared root is absent from catalog") from exc
        cache_key = _formal_prepared_template_cache_key(root)
        templates = template_cache.get(cache_key)
        if templates is None:
            templates = _prepare_formal_root_templates(
                root=root,
                case=case,
                ai_api_configs=ai_api_configs,
                artifact_root=artifact_root / cache_key.removeprefix("sha256:"),
            )
            template_cache[cache_key] = templates
        if tuple(item.planned_ai_unit_id for item in templates) != root.planned_ai_unit_ids:
            raise ValueError("prepared runtime AI-unit ids do not match formal snapshot")
        templates_by_root_key[(root.condition.condition_id, root.case_id)] = templates
        records.extend(_records_from_templates(root=root, templates=templates))
    if len(records) != snapshot.first_attempt_ai_unit_count:
        raise ValueError("prepared request inventory does not cover all first attempts")
    unique_inference = {
        record.inference_request_digest: record.prepared_request
        for record in records
    }
    for record in records:
        if unique_inference[record.inference_request_digest] != record.prepared_request:
            raise ValueError("one inference digest maps to multiple prepared requests")
    inventory = FormalPreparedRequestInventory(
        records=tuple(records),
        record_count=len(records),
        unique_inference_request_count=len(unique_inference),
        provider_calls_made=0,
        source_snapshot_digest=snapshot.snapshot_digest,
    )
    _validate_formal_prepared_inventory_with_templates(
        inventory=inventory,
        snapshot=snapshot,
        ai_api_configs=ai_api_configs,
        templates_by_root_key=templates_by_root_key,
    )
    return inventory


def _prepare_formal_root_templates(
    *,
    root: FormalRootSnapshot,
    case: Mapping[str, Any],
    ai_api_configs: Mapping[str, Any],
    artifact_root: Path,
) -> tuple[_PreparedRuntimeTemplate, ...]:
    source_config, source_entry, controls = _resolved_request_controls(
        condition=root.condition,
        ai_api_configs=ai_api_configs,
    )
    _validate_root_endpoint_controls(
        root=root,
        source_config=source_config,
        source_entry=source_entry,
        controls=controls,
    )
    validated_binding = validate_condition_fixed_entry_identity(
        condition=root.condition,
        source_config=source_config,
    )
    if validated_binding is None:
        raise ValueError("formal condition requires complete model endpoint identity")
    adapter_metadata_key = (
        "factorization_paper_adapter"
        if root.condition.domain == "factorization"
        else "lean_paper_adapter"
    )
    prepared_config = prepare_fixed_entry_execution_config(
        source_config=source_config,
        binding=validated_binding,
        max_tokens=root.endpoint_controls.max_tokens,
        timeout_seconds=root.endpoint_controls.timeout_seconds,
        adapter_metadata_key=adapter_metadata_key,
    )
    prepared_entries = tuple(
        entry for entry in prepared_config.entries if entry.enabled
    )
    if len(prepared_entries) != 1 or prepared_entries[0].entry_id != source_entry.entry_id:
        raise ValueError("formal prepared config must contain its one enabled entry")
    if (
        int(controls.get("max_tokens", 0)) != root.endpoint_controls.max_tokens
        or int(controls.get("timeout_seconds", 0))
        != root.endpoint_controls.timeout_seconds
        or int(controls.get("max_provider_attempts", 0))
        != root.endpoint_controls.max_provider_attempts
    ):
        raise ValueError("formal prepared request controls drift")
    executor_requirements = build_fixed_entry_executor_requirements(
        config=prepared_config,
        binding=validated_binding,
    )
    planned_case = _case_with_formal_split_profile(
        case,
        split_profile_id=root.split_profile_id,
    )
    protocol_config = replace(
        ProtocolConfig.default(
            config_id=f"formal_request_plan_{root.condition.domain}_{root.case_id}",
            artifact_store_uri="file://formal-request-plan",
            event_log_uri="file://formal-request-plan/events.jsonl",
        ),
        max_children_per_unit=64,
    )
    store = ArtifactStore(artifact_root)
    adapter: FactorizationRuntimeAdapter | LeanRuntimeAdapter
    if root.condition.domain == "factorization":
        adapter = FactorizationRuntimeAdapter(
            provider_family=prepared_config.provider_family,
            seed=root.seed,
            protocol_config=protocol_config,
            executor_requirements=executor_requirements,
            max_tokens=root.endpoint_controls.max_tokens,
            timeout_seconds=root.endpoint_controls.timeout_seconds,
        )
    elif root.condition.domain == "lean_proof":
        adapter = LeanRuntimeAdapter(
            provider_family=prepared_config.provider_family,
            environment_manifest=default_lean_paper_environment_manifest(),
            seed=root.seed,
            protocol_config=protocol_config,
            executor_requirements=executor_requirements,
            max_tokens=root.endpoint_controls.max_tokens,
            timeout_seconds=root.endpoint_controls.timeout_seconds,
        )
    else:
        raise ValueError("unsupported formal prepared request domain")
    units = adapter.plan_units(planned_case, artifact_store=store)
    ai_units = tuple(
        (unit, planned_ai_unit_id)
        for unit in units
        if (planned_ai_unit_id := adapter.planned_ai_unit_id(unit)) is not None
    )
    if tuple(unit_id for _unit, unit_id in ai_units) != root.planned_ai_unit_ids:
        raise ValueError("runtime planned AI-unit ids do not match formal commitment")
    templates: list[_PreparedRuntimeTemplate] = []
    for unit, planned_ai_unit_id in ai_units:
        attempt, lease = _formal_planning_attempt_and_lease(
            case_id=root.case_id,
            unit_id=unit.unit_id,
            task_id=unit.task_id,
        )
        if isinstance(adapter, LeanRuntimeAdapter):
            request = adapter.build_planning_execution_request(
                unit,
                attempt=attempt,
                lease=lease,
            )
        else:
            request = adapter.build_execution_request(
                unit,
                attempt=attempt,
                lease=lease,
            )
        request = _bind_pre_acquisition_slot(
            request=request,
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=root.repeat_id,
            replacement_slot=0,
        )
        if request.prompt_package_ref is None:
            raise ValueError("formal planned AI request is missing prompt package")
        prompt = json.loads(
            store.read_bytes(request.prompt_package_ref).decode("utf-8")
        )
        outbound = prepare_ai_api_outbound_request(
            config=prepared_config,
            request=request,
            prompt=prompt,
            entry=prepared_entries[0],
        )
        prepared = outbound.prepared_request
        if (
            prepared.planned_ai_unit_id != planned_ai_unit_id
            or prepared.sample_slot_index != root.repeat_id
            or prepared.replacement_slot != 0
            or prepared.configured_model != root.endpoint_controls.provider_model_id
        ):
            raise ValueError("formal prepared request identity drift")
        templates.append(
            _PreparedRuntimeTemplate(
                planned_ai_unit_id=planned_ai_unit_id,
                source_provider_config_digest=source_config.config_digest,
                prepared_execution_config_digest=prepared_config.config_digest,
                provider_request_identity=dict(outbound.provider_request_identity),
                prepared_request=prepared,
            )
        )
    return tuple(templates)


def _independently_prepare_formal_root_templates(
    *,
    root: FormalRootSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    artifact_root: Path,
) -> tuple[_PreparedRuntimeTemplate, ...]:
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    try:
        case = cases_by_id[root.case_id]
    except KeyError as exc:
        raise ValueError("formal prepared root is absent from catalog") from exc
    templates = _prepare_formal_root_templates(
        root=root,
        case=case,
        ai_api_configs=ai_api_configs,
        artifact_root=artifact_root,
    )
    if tuple(item.planned_ai_unit_id for item in templates) != root.planned_ai_unit_ids:
        raise ValueError("independent runtime template AI-unit identity drift")
    return templates


def _independently_prepare_formal_snapshot_templates(
    *,
    snapshot: FormalPlanSnapshot,
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    artifact_root: Path,
) -> dict[tuple[str, str], tuple[_PreparedRuntimeTemplate, ...]]:
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    template_cache: dict[str, tuple[_PreparedRuntimeTemplate, ...]] = {}
    templates_by_root_key: dict[
        tuple[str, str], tuple[_PreparedRuntimeTemplate, ...]
    ] = {}
    for root in snapshot.roots:
        try:
            case = cases_by_id[root.case_id]
        except KeyError as exc:
            raise ValueError("formal prepared root is absent from catalog") from exc
        cache_key = _formal_prepared_template_cache_key(root)
        templates = template_cache.get(cache_key)
        if templates is None:
            templates = _prepare_formal_root_templates(
                root=root,
                case=case,
                ai_api_configs=ai_api_configs,
                artifact_root=artifact_root / cache_key.removeprefix("sha256:"),
            )
            template_cache[cache_key] = templates
        if tuple(item.planned_ai_unit_id for item in templates) != root.planned_ai_unit_ids:
            raise ValueError("independent runtime template AI-unit identity drift")
        root_key = (root.condition.condition_id, root.case_id)
        if root_key in templates_by_root_key:
            raise ValueError("independent runtime template root identity is duplicate")
        templates_by_root_key[root_key] = templates
    return templates_by_root_key


def _template_for_planned_unit(
    templates: Sequence[_PreparedRuntimeTemplate],
    planned_ai_unit_id: str,
) -> _PreparedRuntimeTemplate:
    matches = tuple(
        item for item in templates if item.planned_ai_unit_id == planned_ai_unit_id
    )
    if len(matches) != 1:
        raise ValueError("independent runtime template unit identity drift")
    return matches[0]


def _validate_formal_prepared_records_with_templates(
    *,
    records: Sequence[FormalPreparedRequestRecord],
    root: FormalRootSnapshot,
    templates: Sequence[_PreparedRuntimeTemplate],
    ai_api_configs: Mapping[str, Any],
    require_complete_root: bool,
) -> None:
    if require_complete_root and len(records) != len(templates):
        raise ValueError("formal prepared root record coverage drift")
    authority = _resolve_formal_prepared_record_authority(
        root=root,
        ai_api_configs=ai_api_configs,
    )
    observed_units: set[str] = set()
    for record in records:
        if record.planned_ai_unit_id in observed_units:
            raise ValueError("formal prepared root record unit is duplicate")
        observed_units.add(record.planned_ai_unit_id)
        _validate_formal_prepared_record_fields(
            record=record,
            root=root,
            expected_template=_template_for_planned_unit(
                templates,
                record.planned_ai_unit_id,
            ),
            source_config=authority.source_config,
            source_entry=authority.source_entry,
            prepared_config=authority.prepared_config,
        )
    if require_complete_root and observed_units != set(root.planned_ai_unit_ids):
        raise ValueError("formal prepared root AI-unit coverage drift")


def _validate_root_endpoint_controls(
    *,
    root: FormalRootSnapshot,
    source_config: Any,
    source_entry: Any,
    controls: Mapping[str, Any],
) -> None:
    endpoint = root.endpoint_controls
    expected = {
        "provider_config_id": root.condition.provider_config_id,
        "model_entry_id": root.condition.model_entry_id,
        "provider_family": source_config.provider_family,
        "provider_model_id": source_entry.model,
        "source_provider_config_digest": source_config.config_digest,
        "model_endpoint_identity_digest": (
            root.condition.model_endpoint_identity_digest
        ),
        "reasoning_profile_id": root.condition.reasoning_profile_id,
        "max_tokens": _positive_int(controls.get("max_tokens"), "max_tokens"),
        "timeout_seconds": _positive_int(
            controls.get("timeout_seconds"),
            "timeout_seconds",
        ),
        "max_provider_attempts": _positive_int(
            controls.get("max_provider_attempts"),
            "max_provider_attempts",
        ),
        "stream": _required_bool(controls.get("stream"), "stream"),
        "request_controls_digest": digest_json(controls),
    }
    actual = _endpoint_controls_body(endpoint)
    if actual != expected:
        raise ValueError("formal root endpoint controls drift")


def _resolve_formal_prepared_record_authority(
    *,
    root: FormalRootSnapshot,
    ai_api_configs: Mapping[str, Any],
) -> _FormalPreparedRecordAuthority:
    source_config, source_entry, controls = _resolved_request_controls(
        condition=root.condition,
        ai_api_configs=ai_api_configs,
    )
    _validate_root_endpoint_controls(
        root=root,
        source_config=source_config,
        source_entry=source_entry,
        controls=controls,
    )
    validated_binding = validate_condition_fixed_entry_identity(
        condition=root.condition,
        source_config=source_config,
    )
    if validated_binding is None:
        raise ValueError("formal prepared record endpoint binding is incomplete")
    prepared_config = prepare_fixed_entry_execution_config(
        source_config=source_config,
        binding=validated_binding,
        max_tokens=root.endpoint_controls.max_tokens,
        timeout_seconds=root.endpoint_controls.timeout_seconds,
        adapter_metadata_key=(
            "factorization_paper_adapter"
            if root.condition.domain == "factorization"
            else "lean_paper_adapter"
        ),
    )
    return _FormalPreparedRecordAuthority(
        source_config=source_config,
        source_entry=source_entry,
        prepared_config=prepared_config,
    )


def _validate_formal_prepared_record_fields(
    *,
    record: FormalPreparedRequestRecord,
    root: FormalRootSnapshot,
    expected_template: _PreparedRuntimeTemplate,
    source_config: Any,
    source_entry: Any,
    prepared_config: Any,
) -> None:
    if record.condition is not root.condition:
        raise ValueError("condition authority identity drift")
    if record.binding is not root.binding:
        raise ValueError("selection binding authority identity drift")
    if (
        root.condition_digest != root.condition.condition_digest
        or root.binding.condition_id != root.condition.condition_id
        or root.binding.condition_digest != root.condition.condition_digest
        or root.selection_digest != root.binding.selection.selection_digest
        or root.seed != root.condition.seed
        or root.repeat_id != root.condition.repeat_id
        or root.case_id not in root.binding.selection.ordered_case_ids
    ):
        raise ValueError("formal prepared record root identity drift")
    expected_replacement_slots = replacement_slots_for(
        experiment_id=root.condition.experiment_id,
        fault_type=str(root.condition.fault_type),
        ablation_mode=str(root.condition.ablation_mode),
    )
    if (
        expected_template.source_provider_config_digest
        != source_config.config_digest
        or expected_template.prepared_execution_config_digest
        != prepared_config.config_digest
    ):
        raise ValueError("independent runtime template config identity drift")
    expected_fields = {
        "case_id": root.case_id,
        "case_record_digest": root.case_record_digest,
        "sample_slot_index": root.repeat_id,
        "base_replacement_slot": 0,
        "replacement_slot_ids": expected_replacement_slots,
        "replacement_policy_id": "formal_attempt_budget.v1",
        "source_provider_config_digest": (
            expected_template.source_provider_config_digest
        ),
        "prepared_execution_config_digest": (
            expected_template.prepared_execution_config_digest
        ),
        "provider_family": root.endpoint_controls.provider_family,
        "provider_config_id": root.endpoint_controls.provider_config_id,
        "model_entry_id": root.endpoint_controls.model_entry_id,
        "provider_model_id": root.endpoint_controls.provider_model_id,
        "reasoning_profile_id": root.endpoint_controls.reasoning_profile_id,
        "model_endpoint_identity_digest": (
            root.endpoint_controls.model_endpoint_identity_digest
        ),
        "request_max_tokens": root.endpoint_controls.max_tokens,
        "request_timeout_seconds": root.endpoint_controls.timeout_seconds,
        "request_max_provider_attempts": (
            root.endpoint_controls.max_provider_attempts
        ),
        "request_controls_digest": root.endpoint_controls.request_controls_digest,
        "provider_calls_made": 0,
    }
    for field_name, expected in expected_fields.items():
        if getattr(record, field_name) != expected:
            raise ValueError(f"formal prepared record {field_name} drift")
    if record.planned_ai_unit_id not in root.planned_ai_unit_ids:
        raise ValueError("formal prepared record planned_ai_unit_id drift")

    prepared = validate_prepared_request(record.prepared_request)
    _validate_provider_request_identity_against_prepared(
        provider_request_identity=record.provider_request_identity,
        prepared=prepared,
        provider_family=record.provider_family,
    )
    expected_prepared = validate_prepared_request(expected_template.prepared_request)
    expected_identity = _validate_provider_request_identity_against_prepared(
        provider_request_identity=expected_template.provider_request_identity,
        prepared=expected_prepared,
        provider_family=root.endpoint_controls.provider_family,
    )
    if (
        expected_identity["entry_id"] != source_entry.entry_id
        or expected_identity["configured_model"] != source_entry.model
        or expected_identity["requested_model"] != source_entry.model
    ):
        raise ValueError("independent runtime template provider identity drift")
    if dict(record.provider_request_identity) != dict(
        expected_template.provider_request_identity
    ):
        raise ValueError("independent runtime template provider identity drift")
    if prepared != expected_prepared:
        raise ValueError("independent runtime template PreparedOutboundRequest drift")
    expected_prompt_digest = digest_json(
        {
            "body_digest": expected_prepared.body_digest,
            "prompt_profile_id": expected_prepared.prompt_profile_id,
            "prompt_serialization_schema": (
                expected_prepared.prompt_serialization_schema
            ),
        }
    )
    if record.prompt_profile_digest != expected_prompt_digest:
        raise ValueError("formal prepared record prompt_profile_digest drift")


def _validate_provider_request_identity_against_prepared(
    *,
    provider_request_identity: Mapping[str, Any],
    prepared: PreparedOutboundRequest,
    provider_family: str,
) -> dict[str, Any]:
    if not isinstance(provider_request_identity, Mapping):
        raise ValueError("provider request identity must be a mapping")
    control_keys = (
        "stream",
        "temperature",
        "top_p",
        "max_tokens",
        "response_format",
        "reasoning_effort",
        "enable_thinking",
        "thinking_budget",
        "thinking",
    )
    effective_controls = {
        key: prepared.body_obj[key]
        for key in control_keys
        if key in prepared.body_obj
    }
    reasoning_controls = {
        key: effective_controls[key]
        for key in (
            "reasoning_effort",
            "enable_thinking",
            "thinking_budget",
            "thinking",
        )
        if key in effective_controls
    }
    expected = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": provider_family,
        "entry_id": prepared.entry_id,
        "configured_model": prepared.configured_model,
        "requested_model": prepared.body_obj.get("model"),
        "reasoning_controls": reasoning_controls,
        "effective_request_controls_digest": digest_json(effective_controls),
    }
    if dict(provider_request_identity) != expected:
        raise ValueError("provider request identity does not match prepared request")
    if prepared.effective_controls_digest != expected[
        "effective_request_controls_digest"
    ]:
        raise ValueError("prepared request effective controls identity drift")
    return expected


def _records_from_templates(
    *,
    root: FormalRootSnapshot,
    templates: Sequence[_PreparedRuntimeTemplate],
) -> tuple[FormalPreparedRequestRecord, ...]:
    replacement_slot_ids = replacement_slots_for(
        experiment_id=root.condition.experiment_id,
        fault_type=str(root.condition.fault_type),
        ablation_mode=str(root.condition.ablation_mode),
    )
    records: list[FormalPreparedRequestRecord] = []
    for template in templates:
        prepared = template.prepared_request
        records.append(
            FormalPreparedRequestRecord(
                condition=root.condition,
                binding=root.binding,
                case_id=root.case_id,
                case_record_digest=root.case_record_digest,
                planned_ai_unit_id=template.planned_ai_unit_id,
                sample_slot_index=root.repeat_id,
                base_replacement_slot=0,
                replacement_slot_ids=replacement_slot_ids,
                replacement_policy_id="formal_attempt_budget.v1",
                source_provider_config_digest=template.source_provider_config_digest,
                prepared_execution_config_digest=(
                    template.prepared_execution_config_digest
                ),
                provider_family=root.endpoint_controls.provider_family,
                provider_config_id=root.endpoint_controls.provider_config_id,
                model_entry_id=root.endpoint_controls.model_entry_id,
                provider_model_id=root.endpoint_controls.provider_model_id,
                reasoning_profile_id=root.endpoint_controls.reasoning_profile_id,
                model_endpoint_identity_digest=(
                    root.endpoint_controls.model_endpoint_identity_digest
                ),
                request_max_tokens=root.endpoint_controls.max_tokens,
                request_timeout_seconds=root.endpoint_controls.timeout_seconds,
                request_max_provider_attempts=(
                    root.endpoint_controls.max_provider_attempts
                ),
                request_controls_digest=(
                    root.endpoint_controls.request_controls_digest
                ),
                prompt_profile_digest=digest_json(
                    {
                        "body_digest": prepared.body_digest,
                        "prompt_profile_id": prepared.prompt_profile_id,
                        "prompt_serialization_schema": (
                            prepared.prompt_serialization_schema
                        ),
                    }
                ),
                provider_request_identity=template.provider_request_identity,
                prepared_request=prepared,
                provider_calls_made=0,
            )
        )
    return tuple(records)


def _formal_prepared_template_cache_key(root: FormalRootSnapshot) -> str:
    return digest_json(
        {
            "case_id": root.case_id,
            "case_record_digest": root.case_record_digest,
            "seed": root.seed,
            "repeat_id": root.repeat_id,
            "split_profile_id": root.split_profile_id,
            "split_profile_digest": root.split_profile_digest,
            "plugin_id": root.plugin_id,
            "plugin_version": root.plugin_version,
            "provider_family": root.endpoint_controls.provider_family,
            "provider_config_id": root.endpoint_controls.provider_config_id,
            "source_provider_config_digest": (
                root.endpoint_controls.source_provider_config_digest
            ),
            "model_entry_id": root.endpoint_controls.model_entry_id,
            "provider_model_id": root.endpoint_controls.provider_model_id,
            "model_endpoint_identity_digest": (
                root.endpoint_controls.model_endpoint_identity_digest
            ),
            "request_controls_digest": (
                root.endpoint_controls.request_controls_digest
            ),
        }
    )


def _bind_pre_acquisition_slot(
    *,
    request: Any,
    planned_ai_unit_id: str,
    sample_slot_index: int,
    replacement_slot: int,
) -> Any:
    """冻结尚未有 bank binding 时可知的正式 semantic-slot 身份。"""

    hints = dict(request.soft_hints or {})
    existing = hints.get("planned_ai_unit_id")
    if existing is not None and existing != planned_ai_unit_id:
        raise ValueError("runtime request planned AI-unit identity drift")
    hints.update(
        {
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "replacement_slot": replacement_slot,
        }
    )
    return replace(request, soft_hints=hints)


def _case_with_formal_split_profile(
    case: Mapping[str, Any],
    *,
    split_profile_id: str | None,
) -> dict[str, Any]:
    if split_profile_id is None:
        return dict(case)
    split_params = case.get("split_params")
    if not isinstance(split_params, Mapping):
        raise ValueError("formal split profile requires factorization split_params")
    return {
        **dict(case),
        "split_params": {
            "strategy_id": split_params.get("strategy_id"),
            "range_policy": split_params.get("range_policy"),
            "split_profile_id": split_profile_id,
        },
    }


def _formal_planning_attempt_and_lease(
    *,
    case_id: str,
    unit_id: str,
    task_id: str,
) -> tuple[Attempt, Lease]:
    identity = digest_json(
        {"case_id": case_id, "unit_id": unit_id, "attempt_ordinal": 0}
    ).removeprefix("sha256:")
    attempt_id = f"formal_request_plan_{identity}"
    lease_id = f"lease_{identity}"
    attempt = Attempt(
        attempt_id=attempt_id,
        task_id=task_id,
        unit_id=unit_id,
        lease_id=lease_id,
        client_id="formal_request_planner",
        state=AttemptState.RUNNING,
        attempt_kind="primary",
        created_at="2026-07-14T00:00:00Z",
        started_at="2026-07-14T00:00:00Z",
    )
    return attempt, Lease(
        lease_id=lease_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        client_id=attempt.client_id,
        state=LeaseState.ACTIVE,
        fencing_token=f"fence_{identity}",
        issued_at="2026-07-14T00:00:00Z",
        expires_at="2026-07-14T00:10:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="execution",
        terminated_at=None,
        terminated_reason=None,
        metadata={},
    )


def validate_paper_formal_plan_bindings(
    *,
    conditions: Sequence[PaperExperimentCondition],
    bindings: Sequence[FrozenConditionSelectionBinding],
    catalog_manifest: PaperInputCatalogManifest,
) -> None:
    """验证原 condition/binding 与真实 catalog 的一一对应关系。"""

    cases_by_id = _unique_catalog_cases(catalog_manifest)
    conditions_by_id: dict[str, PaperExperimentCondition] = {}
    for condition in conditions:
        if condition.condition_id in conditions_by_id:
            raise ValueError("duplicate formal condition")
        conditions_by_id[condition.condition_id] = condition

    bindings_by_id: dict[str, FrozenConditionSelectionBinding] = {}
    for binding in bindings:
        if binding.condition_id in bindings_by_id:
            raise ValueError("duplicate condition binding")
        condition = conditions_by_id.get(binding.condition_id)
        if condition is None:
            raise ValueError("extra condition binding")
        if binding.condition_digest != condition.condition_digest:
            raise ValueError("condition binding condition digest mismatch")
        canonical = FrozenConditionSelectionBinding.from_condition(
            condition,
            binding.selection,
        )
        if canonical.to_dict() != binding.to_dict():
            raise ValueError("condition binding is not canonical")
        for case_id in binding.selection.ordered_case_ids:
            case = cases_by_id.get(case_id)
            if case is None:
                raise ValueError("selection case is absent from formal catalog")
            _validate_case_selection_shape(
                case=case,
                condition=condition,
                binding=binding,
            )
        bindings_by_id[binding.condition_id] = binding
    if set(bindings_by_id) != set(conditions_by_id):
        raise ValueError("missing condition binding")


def validate_paper_formal_budget_commitments(
    *,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    budget: PaperBudgetResult,
) -> None:
    """把 budget selection/unit commitments 逐项绑定回正式 plan authority。"""

    plans = tuple(dispatch_plans)
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    budget_commitments = _budget_commitments_body(budget)
    frozen_selections = budget_commitments.get("frozen_selections")
    if isinstance(frozen_selections, (str, bytes, bytearray)) or not isinstance(
        frozen_selections,
        Sequence,
    ):
        raise ValueError("formal budget frozen selections are missing")
    expected_frozen = tuple(
        {
            **binding.selection.to_dict(),
            "condition_id": condition.condition_id,
            "condition_digest": condition.condition_digest,
        }
        for plan in plans
        for condition, binding in _ordered_condition_bindings(plan)
    )
    if tuple(frozen_selections) != expected_frozen:
        raise ValueError("formal budget frozen selection mismatch")
    frozen_by_condition_id = {
        str(selection["condition_id"]): selection
        for selection in expected_frozen
    }

    commitments = _budget_ai_unit_commitments(budget)
    expected_keys: set[tuple[str, str]] = set()
    split_profiles: dict[tuple[str, str | None, str | None], Mapping[str, Any]] = {}
    for plan in plans:
        for condition, binding in _ordered_condition_bindings(plan):
            exact_selection = frozen_by_condition_id[condition.condition_id]
            selection_ai_units = 0
            for case_id in binding.selection.ordered_case_ids:
                key = (condition.condition_id, case_id)
                commitment = commitments.get(key)
                if commitment is None:
                    raise ValueError("formal budget is missing AI-unit commitment")
                case = cases_by_id[case_id]
                split_key = (
                    case_id,
                    (
                        condition.experiment_id
                        if condition.experiment_id
                        == "exp2_real_ai_scalability"
                        else None
                    ),
                    getattr(binding.selection, "split_profile_id", None),
                )
                split_profile = split_profiles.get(split_key)
                if split_profile is None:
                    split_profile = build_paper_budget_split_profile(
                        case=dict(case),
                        condition=condition,
                        frozen_selection=dict(exact_selection),
                    )
                    split_profiles[split_key] = split_profile
                expected = {
                    "condition_id": condition.condition_id,
                    "condition_digest": condition.condition_digest,
                    "case_id": case_id,
                    "planned_ai_unit_ids": [
                        str(unit_id) for unit_id in split_profile["ai_unit_order"]
                    ],
                    "case_digest": digest_json(case),
                    "split_profile_digest": digest_json(split_profile),
                    "commitment_digest": digest_json(
                        {
                            "case": case,
                            "split_profile": split_profile,
                            "seed": condition.seed,
                        }
                    ),
                }
                if dict(commitment) != expected:
                    raise ValueError("formal budget AI-unit commitment mismatch")
                selection_ai_units += len(expected["planned_ai_unit_ids"])
                expected_keys.add(key)
            if selection_ai_units != binding.selection.expected_ai_unit_count:
                raise ValueError("formal selection AI-unit commitment total mismatch")
    if set(commitments) != expected_keys:
        raise ValueError("formal budget contains extra AI-unit commitment")


def freeze_paper_formal_plan_snapshot(
    *,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    budget: PaperBudgetResult,
    ai_api_configs: Mapping[str, Any],
    output_root: str | Path,
) -> FormalPlanSnapshot:
    """冻结完整正式计划；只读运行，不构造 smoke authority 或调用 provider。"""

    plans = tuple(dispatch_plans)
    _validate_canonical_formal_plans(
        plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        ai_api_configs=ai_api_configs,
    )
    validate_paper_formal_suite_plan(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        output_root=output_root,
        ai_api_configs=ai_api_configs,
        hard_limits={
            "max_total_provider_attempts": budget.max_provider_attempts,
            "max_total_tokens": budget.token_upper_bound,
            "max_cost_estimate": budget.cost_upper_bound,
        },
    )
    all_conditions = tuple(
        condition for plan in plans for condition in plan.conditions
    )
    all_bindings = tuple(
        binding
        for plan in plans
        for binding in plan.condition_selection_bindings
    )
    validate_paper_formal_plan_bindings(
        conditions=all_conditions,
        bindings=all_bindings,
        catalog_manifest=catalog_manifest,
    )
    validate_paper_formal_budget_commitments(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
    )
    commitments = _budget_ai_unit_commitments(budget)
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    condition_rows: list[FormalConditionSnapshot] = []
    root_rows: list[FormalRootSnapshot] = []
    seen_commitment_keys: set[tuple[str, str]] = set()
    for plan in plans:
        if plan.provider_calls_made != 0:
            raise ValueError("formal dispatch planning made provider calls")
        bindings_by_id = {
            binding.condition_id: binding
            for binding in plan.condition_selection_bindings
        }
        for condition in plan.conditions:
            binding = bindings_by_id[condition.condition_id]
            endpoint_controls = _freeze_endpoint_controls(
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            condition_rows.append(
                FormalConditionSnapshot(
                    condition=condition,
                    binding=binding,
                    endpoint_controls=endpoint_controls,
                )
            )
            selection_ai_units = 0
            for case_id in binding.selection.ordered_case_ids:
                key = (condition.condition_id, case_id)
                commitment = commitments.get(key)
                if commitment is None:
                    raise ValueError("formal budget is missing root AI-unit commitment")
                if key in seen_commitment_keys:
                    raise ValueError("duplicate formal root AI-unit commitment")
                seen_commitment_keys.add(key)
                if commitment.get("condition_digest") != condition.condition_digest:
                    raise ValueError("budget commitment condition digest mismatch")
                case = cases_by_id[case_id]
                case_digest = digest_json(case)
                if commitment.get("case_digest") != case_digest:
                    raise ValueError("budget commitment case digest mismatch")
                unit_ids = _string_tuple(
                    commitment.get("planned_ai_unit_ids"),
                    "planned AI-unit ids",
                )
                split_digest = _required_digest(
                    commitment.get("split_profile_digest"),
                    "split_profile_digest",
                )
                plugin_id, plugin_version = _plugin_identity(condition.domain)
                root_rows.append(
                    FormalRootSnapshot(
                        condition=condition,
                        binding=binding,
                        case_id=case_id,
                        case_record_digest=case_digest,
                        condition_digest=condition.condition_digest,
                        selection_digest=binding.selection.selection_digest,
                        seed=condition.seed,
                        repeat_id=condition.repeat_id,
                        split_profile_id=getattr(
                            binding.selection,
                            "split_profile_id",
                            None,
                        ),
                        split_profile_digest=split_digest,
                        planned_ai_unit_ids=unit_ids,
                        plugin_id=plugin_id,
                        plugin_version=plugin_version,
                        endpoint_controls=endpoint_controls,
                    )
                )
                selection_ai_units += len(unit_ids)
            if selection_ai_units != binding.selection.expected_ai_unit_count:
                raise ValueError("selection AI-unit total does not match budget commitments")

    if seen_commitment_keys != set(commitments):
        raise ValueError("budget contains extra root AI-unit commitments")
    if len(condition_rows) != budget.planned_conditions:
        raise ValueError("formal snapshot condition total does not match budget")
    if len(root_rows) != budget.planned_root_runs:
        raise ValueError("formal snapshot root total does not match budget")
    ai_unit_count = sum(len(root.planned_ai_unit_ids) for root in root_rows)
    if ai_unit_count != budget.planned_ai_units:
        raise ValueError("formal snapshot AI-unit total does not match budget")
    return FormalPlanSnapshot(
        conditions=tuple(condition_rows),
        roots=tuple(root_rows),
        condition_count=len(condition_rows),
        root_run_count=len(root_rows),
        first_attempt_ai_unit_count=ai_unit_count,
        provider_calls_made=0,
        budget_digest=budget.budget_digest,
    )


def derive_paper_formal_representative_coverage(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    repeat_ids: Sequence[int] = (0,),
) -> FormalRepresentativeCoverage:
    """从已验证 full snapshot 派生正式 repeat 子集与独立 root filter。"""

    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("snapshot must be a FormalPlanSnapshot")
    repeats = _representative_repeat_ids(repeat_ids)
    if snapshot.provider_calls_made != 0:
        raise ValueError("formal snapshot made provider calls")
    if (
        snapshot.condition_count != len(snapshot.conditions)
        or snapshot.root_run_count != len(snapshot.roots)
        or snapshot.first_attempt_ai_unit_count
        != sum(len(root.planned_ai_unit_ids) for root in snapshot.roots)
    ):
        raise ValueError("formal snapshot totals drift")

    plans = tuple(dispatch_plans)
    planned_conditions: dict[str, PaperExperimentCondition] = {}
    planned_bindings: dict[str, FrozenConditionSelectionBinding] = {}
    for plan in plans:
        if plan.status != "planned":
            continue
        bindings_by_id = {
            binding.condition_id: binding
            for binding in plan.condition_selection_bindings
        }
        for condition in plan.conditions:
            if condition.condition_id in planned_conditions:
                raise ValueError("duplicate formal condition authority")
            planned_conditions[condition.condition_id] = condition
            planned_bindings[condition.condition_id] = bindings_by_id[
                condition.condition_id
            ]
    if set(planned_conditions) != {
        item.condition.condition_id for item in snapshot.conditions
    }:
        raise ValueError("formal snapshot/dispatch condition coverage drift")
    for item in snapshot.conditions:
        condition_id = item.condition.condition_id
        if (
            item.condition is not planned_conditions[condition_id]
            or item.binding is not planned_bindings[condition_id]
        ):
            raise ValueError("formal snapshot must retain dispatch object references")

    selected_condition_rows = tuple(
        item for item in snapshot.conditions if item.condition.repeat_id in repeats
    )
    if not selected_condition_rows:
        raise ValueError("representative repeat subset selected no formal conditions")
    formal_experiment_ids = tuple(registered_paper_experiment_ids())
    selected_experiment_ids = {
        item.condition.experiment_id for item in selected_condition_rows
    }
    if selected_experiment_ids != set(formal_experiment_ids):
        raise ValueError("representative repeat subset misses a formal experiment")

    roots_by_condition: dict[str, list[FormalRootSnapshot]] = {}
    for root in snapshot.roots:
        condition_id = root.condition.condition_id
        formal_condition = planned_conditions.get(condition_id)
        formal_binding = planned_bindings.get(condition_id)
        if root.condition is not formal_condition or root.binding is not formal_binding:
            raise ValueError("formal root must retain condition/binding references")
        if (
            root.condition_digest != root.condition.condition_digest
            or root.selection_digest != root.binding.selection.selection_digest
            or root.seed != root.condition.seed
            or root.repeat_id != root.condition.repeat_id
        ):
            raise ValueError("formal root identity drift")
        roots_by_condition.setdefault(condition_id, []).append(root)

    root_filter: dict[str, tuple[str, ...]] = {}
    selected_roots: list[FormalRootSnapshot] = []
    for item in selected_condition_rows:
        condition = item.condition
        roots = tuple(roots_by_condition.get(condition.condition_id, ()))
        expected_case_ids = tuple(item.binding.selection.ordered_case_ids)
        if tuple(root.case_id for root in roots) != expected_case_ids:
            raise ValueError("formal snapshot root order/selection drift")
        selected_case_ids = _representative_case_ids_for_condition(
            condition=condition,
            roots=roots,
        )
        root_filter[condition.condition_id] = selected_case_ids
        selected_set = set(selected_case_ids)
        selected_roots.extend(
            root for root in roots if root.case_id in selected_set
        )

    normalized_filter = validate_paper_formal_root_case_filter(
        dispatch_plans=plans,
        selected_condition_ids=tuple(
            item.condition.condition_id for item in selected_condition_rows
        ),
        root_case_filter=root_filter,
    )
    return FormalRepresentativeCoverage(
        conditions=tuple(item.condition for item in selected_condition_rows),
        bindings=tuple(item.binding for item in selected_condition_rows),
        roots=tuple(selected_roots),
        root_case_filter=normalized_filter,
        source_snapshot_digest=snapshot.snapshot_digest,
        condition_count=len(selected_condition_rows),
        root_run_count=len(selected_roots),
        provider_calls_made=0,
    )


def _representative_repeat_ids(value: Sequence[int]) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("representative repeat ids must be a sequence")
    repeats = tuple(value)
    if (
        not repeats
        or any(
            isinstance(repeat, bool)
            or not isinstance(repeat, int)
            or repeat < 0
            for repeat in repeats
        )
        or len(set(repeats)) != len(repeats)
    ):
        raise ValueError(
            "representative repeat ids must be unique non-negative integers"
        )
    return repeats


def _representative_case_ids_for_condition(
    *,
    condition: PaperExperimentCondition,
    roots: Sequence[FormalRootSnapshot],
) -> tuple[str, ...]:
    if not roots:
        raise ValueError("representative condition has no formal roots")
    if condition.experiment_id != EXP3_EXPERIMENT_ID:
        return (roots[0].case_id,)
    targets_by_case = formal_exp3_scheduled_target_unit_ids_by_case(
        condition=condition,
        ordered_case_ids=tuple(root.case_id for root in roots),
        planned_ai_unit_ids_by_case={
            root.case_id: root.planned_ai_unit_ids for root in roots
        },
    )
    if condition.fault_type == "worker_death":
        max_target_count = max(len(targets) for targets in targets_by_case.values())
        if max_target_count < 1:
            raise ValueError("representative worker-death root cannot schedule a target")
        return (
            next(
                root.case_id
                for root in roots
                if len(targets_by_case[root.case_id]) == max_target_count
            ),
        )
    for root in roots:
        if targets_by_case[root.case_id]:
            return (root.case_id,)
    raise ValueError("representative rate-fault root cannot schedule a target")


def _validate_canonical_formal_plans(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    budget: PaperBudgetResult,
    ai_api_configs: Mapping[str, Any],
) -> None:
    expected_experiment_ids = registered_paper_experiment_ids()
    if tuple(plan.experiment_id for plan in plans) != expected_experiment_ids:
        raise ValueError("canonical formal experiment order mismatch")
    baseline_binding, exp5_binding, request_limits = _formal_endpoint_authorities(
        budget=budget,
        ai_api_configs=ai_api_configs,
    )
    for plan in plans:
        if plan.catalog_execution_view is None:
            raise ValueError("formal plan requires catalog execution view")
        catalog_view = restore_catalog_execution_view(
            plan.catalog_execution_view,
            catalog_manifest=catalog_manifest,
        )
        endpoint_binding = (
            exp5_binding
            if plan.experiment_id == FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
            else baseline_binding
        )
        expected = plan_paper_experiment(
            context=PaperExecutionContext(
                context_id=f"formal_canonical_{plan.experiment_id}",
                catalog=catalog_view,
                approved_endpoint_binding=endpoint_binding,
                request_limits=request_limits,
                hard_limits={"max_total_provider_attempts": 0},
                output_root=plan.output_root,
                artifact_store=object(),
                event_store=object(),
                execution_callback=_forbidden_plan_execution,
            ),
            experiment_id=plan.experiment_id,
        )
        if plan.to_dict() != expected.to_dict():
            raise ValueError(
                f"canonical formal plan mismatch: {plan.experiment_id}"
            )


def _formal_endpoint_authorities(
    *,
    budget: PaperBudgetResult,
    ai_api_configs: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    commitments = _budget_commitments_body(budget)
    endpoint_identity = commitments.get("endpoint_identity")
    if not isinstance(endpoint_identity, Mapping):
        raise ValueError("formal budget endpoint identity is missing")
    baseline = endpoint_identity.get("baseline")
    exp5_budget = endpoint_identity.get("model_endpoint_cohort_preflight")
    if not isinstance(baseline, Mapping) or not isinstance(exp5_budget, Mapping):
        raise ValueError("formal endpoint authority is incomplete")

    scoped_bindings = ai_api_configs.get(APPROVED_ENDPOINT_BINDINGS_KEY)
    exp5_config = (
        scoped_bindings.get(FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID)
        if isinstance(scoped_bindings, Mapping)
        else None
    )
    if not isinstance(exp5_config, Mapping) or dict(exp5_budget) != dict(exp5_config):
        raise ValueError("formal Exp5 endpoint authority mismatch")

    baseline_body = dict(baseline)
    selected_entry_id = str(baseline_body.get("selected_entry_id") or "")
    identity = PaperModelEndpointIdentity(
        schema_version=str(baseline_body.get("schema_version") or ""),
        model_cohort_id=str(baseline_body.get("model_cohort_id") or ""),
        model_cohort_digest=str(baseline_body.get("model_cohort_digest") or ""),
        cohort_member_id=str(baseline_body.get("cohort_member_id") or ""),
        provider_config_id=str(baseline_body.get("provider_config_id") or ""),
        selected_entry_id=selected_entry_id,
        provider_family=str(baseline_body.get("provider_family") or ""),
        provider_model_id=str(baseline_body.get("provider_model_id") or ""),
        reasoning_profile_id=str(baseline_body.get("reasoning_profile_id") or ""),
        effective_reasoning_controls=dict(
            baseline_body.get("effective_reasoning_controls") or {}
        ),
        source_provider_config_digest=str(
            baseline_body.get("source_provider_config_digest") or ""
        ),
    )
    config = ai_api_configs.get(identity.provider_config_id)
    if config is None:
        raise ValueError("formal baseline provider config is missing")
    validated = validate_fixed_entry_config_identity(
        expected_identity=identity,
        provider_config_id=identity.provider_config_id,
        source_config=config,
    )
    request_controls = {
        **dict(validated.source_config.defaults),
        **dict(validated.selected_entry.request_overrides),
    }
    expected_baseline = {
        **identity.to_dict(),
        "model_entry_id": selected_entry_id,
        "request_controls": request_controls,
    }
    if baseline_body != expected_baseline:
        raise ValueError("formal baseline endpoint authority mismatch")

    frozen_request_limits = commitments.get("request_limits")
    if not isinstance(frozen_request_limits, Mapping):
        raise ValueError("formal budget request limits are missing")
    if dict(frozen_request_limits) != request_controls:
        raise ValueError("formal budget request limits mismatch")
    return baseline_body, dict(exp5_budget), dict(frozen_request_limits)


def _forbidden_plan_execution(**_kwargs: Any) -> None:
    raise AssertionError("formal canonical plan validation must not execute")


def _budget_ai_unit_commitments(
    budget: PaperBudgetResult,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    budget_commitments = _budget_commitments_body(budget)
    values = budget_commitments.get("ai_unit_commitments")
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values,
        Sequence,
    ):
        raise ValueError("formal AI-unit commitments are missing")
    commitments: dict[tuple[str, str], Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("formal AI-unit commitment must be a mapping")
        key = (str(value.get("condition_id") or ""), str(value.get("case_id") or ""))
        if not all(key) or key in commitments:
            raise ValueError("duplicate formal root AI-unit commitment")
        commitments[key] = value
    return commitments


def _budget_commitments_body(budget: PaperBudgetResult) -> Mapping[str, Any]:
    budget_commitments = budget.quota_preflight.get("budget_commitments")
    if not isinstance(budget_commitments, Mapping):
        raise ValueError("formal budget commitments are missing")
    return budget_commitments


def _ordered_condition_bindings(
    plan: PaperExperimentDispatchPlan,
) -> tuple[tuple[PaperExperimentCondition, FrozenConditionSelectionBinding], ...]:
    bindings_by_id = {
        binding.condition_id: binding
        for binding in plan.condition_selection_bindings
    }
    return tuple(
        (condition, bindings_by_id[condition.condition_id])
        for condition in plan.conditions
    )


def _freeze_endpoint_controls(
    *,
    condition: PaperExperimentCondition,
    ai_api_configs: Mapping[str, Any],
) -> FormalEndpointControls:
    _config, _entry, controls = _resolved_request_controls(
        condition=condition,
        ai_api_configs=ai_api_configs,
    )
    return FormalEndpointControls(
        provider_config_id=str(condition.provider_config_id),
        model_entry_id=str(condition.model_entry_id),
        provider_family=str(condition.provider_family),
        provider_model_id=str(condition.provider_model_id),
        source_provider_config_digest=str(condition.source_provider_config_digest),
        model_endpoint_identity_digest=str(condition.model_endpoint_identity_digest),
        reasoning_profile_id=str(condition.reasoning_profile_id),
        max_tokens=_positive_int(controls.get("max_tokens"), "max_tokens"),
        timeout_seconds=_positive_int(
            controls.get("timeout_seconds"),
            "timeout_seconds",
        ),
        max_provider_attempts=_positive_int(
            controls.get("max_provider_attempts"),
            "max_provider_attempts",
        ),
        stream=_required_bool(controls.get("stream"), "stream"),
        request_controls_digest=digest_json(controls),
    )


def _resolved_request_controls(
    *,
    condition: PaperExperimentCondition,
    ai_api_configs: Mapping[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    config = ai_api_configs.get(condition.provider_config_id)
    if config is None:
        raise ValueError("condition provider config is missing")
    entries = tuple(
        entry
        for entry in getattr(config, "entries", ())
        if entry.enabled and entry.entry_id == condition.model_entry_id
    )
    if len(entries) != 1:
        raise ValueError("condition model entry is not uniquely configured")
    entry = entries[0]
    controls = {
        **dict(getattr(config, "defaults", {})),
        **dict(entry.request_overrides),
    }
    if getattr(config, "provider_family", None) != condition.provider_family:
        raise ValueError("condition provider family does not match config")
    if entry.model != condition.provider_model_id:
        raise ValueError("condition provider model does not match config")
    if getattr(config, "config_digest", None) != condition.source_provider_config_digest:
        raise ValueError("condition provider config digest mismatch")
    return config, entry, controls


def _endpoint_controls_body(value: FormalEndpointControls) -> dict[str, Any]:
    return {
        "provider_config_id": value.provider_config_id,
        "model_entry_id": value.model_entry_id,
        "provider_family": value.provider_family,
        "provider_model_id": value.provider_model_id,
        "source_provider_config_digest": value.source_provider_config_digest,
        "model_endpoint_identity_digest": value.model_endpoint_identity_digest,
        "reasoning_profile_id": value.reasoning_profile_id,
        "max_tokens": value.max_tokens,
        "timeout_seconds": value.timeout_seconds,
        "max_provider_attempts": value.max_provider_attempts,
        "stream": value.stream,
        "request_controls_digest": value.request_controls_digest,
    }


def _unique_catalog_cases(
    catalog_manifest: PaperInputCatalogManifest,
) -> dict[str, Mapping[str, Any]]:
    cases: dict[str, Mapping[str, Any]] = {}
    for case in (
        catalog_manifest.factorization_cases
        + catalog_manifest.lean_cases
        + catalog_manifest.lean_lemma_graph_cases
    ):
        case_id = case.get("case_id") if isinstance(case, Mapping) else None
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("formal catalog case_id is invalid")
        if case_id in cases:
            raise ValueError("duplicate case_id in formal catalog")
        cases[case_id] = case
    return cases


def _validate_case_selection_shape(
    *,
    case: Mapping[str, Any],
    condition: PaperExperimentCondition,
    binding: FrozenConditionSelectionBinding,
) -> None:
    selection = binding.selection
    schema_version = case.get("schema_version")
    if schema_version == "tokenshare.paper_factorization_case.v1":
        case_domain = "factorization"
        difficulty = str(case.get("difficulty") or "")
        if difficulty not in {"easy", "medium", "hard"} or case.get(
            "paper_difficulty"
        ) != difficulty:
            raise ValueError("formal Factorization case difficulty mismatch")
    elif schema_version == "tokenshare.paper_lean_case.v1":
        case_domain = "lean_proof"
        if (
            case.get("difficulty") not in {"easy", "medium", "hard"}
            or case.get("paper_difficulty") != "simple"
            or case.get("topic_family") != "pure_logic"
        ):
            raise ValueError("formal Lean simple case difficulty mismatch")
    elif schema_version == "tokenshare.paper_lean_lemma_graph_case.v1":
        case_domain = "lean_proof"
        expected_difficulty = {
            "simple": "easy",
            "medium_lemma_dag": "medium",
            "hard_frontier": "hard",
        }.get(str(case.get("paper_difficulty") or ""))
        if expected_difficulty is None or case.get("difficulty") != expected_difficulty:
            raise ValueError("formal Lean lemma graph case difficulty mismatch")
    else:
        raise ValueError("unsupported formal catalog case schema")
    if case_domain != condition.domain or selection.domain != condition.domain:
        raise ValueError("selection case domain mismatch")
    exp3_mixed_factor_rate = (
        condition.experiment_id == EXP3_EXPERIMENT_ID
        and condition.domain == "factorization"
        and condition.fault_type != "worker_death"
    )
    difficulty_mismatch = (
        schema_version != "tokenshare.paper_lean_case.v1"
        and case.get("difficulty") != condition.difficulty
    )
    if not exp3_mixed_factor_rate and (
        case.get("paper_difficulty") != selection.paper_difficulty
        or difficulty_mismatch
    ):
        raise ValueError(
            "selection case difficulty mismatch: "
            f"{condition.condition_id}:{case.get('case_id')}"
        )
    if (
        selection.topic_family is not None
        and case.get("topic_family") != selection.topic_family
    ):
        raise ValueError("selection case topic_family mismatch")


def _plugin_identity(domain: str) -> tuple[str, str]:
    if domain == "factorization":
        return FACTORIZATION_PLUGIN_ID, FACTORIZATION_PLUGIN_VERSION
    if domain == "lean_proof":
        return LEAN_PLUGIN_ID, LEAN_PLUGIN_VERSION
    raise ValueError("unsupported formal plugin domain")


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a sequence")
    normalized = tuple(str(item) for item in value)
    if not normalized or any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be non-empty and unique")
    return normalized


def _required_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a digest")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value
