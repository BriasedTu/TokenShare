"""正式论文 full plan 的只读冻结与完整性验证。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
from types import MappingProxyType
from typing import Any
from weakref import ReferenceType, ref

from tokenshare.core.models import (
    ArtifactRef,
    Attempt,
    AttemptState,
    JsonObject,
    Lease,
    LeaseState,
    ProtocolConfig,
)
from tokenshare.executors.ai_api import prepare_ai_api_outbound_request
from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequest,
    validate_prepared_request,
)
from tokenshare.executors.trace_backed import (
    TraceReplacementBinding,
    TraceSourceBinding,
    bind_trace_execution_request,
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
    formal_runtime_task_id,
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
from tokenshare.plugins.factorization.models import RangeResult
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_ID as FACTORIZATION_PLUGIN_ID,
    PLUGIN_VERSION as FACTORIZATION_PLUGIN_VERSION,
    RANGE_RESULT_FOUND_FACTOR,
)
from tokenshare.plugins.factorization.split_strategy import (
    partition_candidate_ranges,
    resolve_requested_child_count,
)
from tokenshare.plugins.factorization.validator import verify_range_result
from tokenshare.plugins.lean_proof.schemas import (
    PLUGIN_ID as LEAN_PLUGIN_ID,
    PLUGIN_VERSION as LEAN_PLUGIN_VERSION,
)
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.storage.artifacts import ArtifactStore


_FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK = RLock()
_FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE: dict[
    int,
    tuple[ReferenceType[Any], str],
] = {}


def _reset_formal_plan_snapshot_digest_cache_after_fork() -> None:
    global _FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK

    _FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK = RLock()
    _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        after_in_child=_reset_formal_plan_snapshot_digest_cache_after_fork
    )


def _memoized_formal_plan_snapshot_digest(snapshot: FormalPlanSnapshot) -> str:
    snapshot_id = id(snapshot)
    with _FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK:
        cached = _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE.get(snapshot_id)
        if cached is not None and cached[0]() is snapshot:
            return cached[1]
        if cached is not None:
            _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE.pop(snapshot_id, None)

        snapshot_digest = digest_json(snapshot._body())

        def _discard(reference: ReferenceType[Any]) -> None:
            with _FORMAL_PLAN_SNAPSHOT_DIGEST_LOCK:
                current = _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE.get(snapshot_id)
                if current is not None and current[0] is reference:
                    _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE.pop(snapshot_id, None)

        reference = ref(snapshot, _discard)
        _FORMAL_PLAN_SNAPSHOT_DIGEST_CACHE[snapshot_id] = (
            reference,
            snapshot_digest,
        )
        return snapshot_digest


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
        return _memoized_formal_plan_snapshot_digest(self)

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
class FormalExecutionCoverage:
    """只引用同一 full snapshot authority 的 typed 执行视图。"""

    conditions: tuple[PaperExperimentCondition, ...]
    bindings: tuple[FrozenConditionSelectionBinding, ...]
    roots: tuple[FormalRootSnapshot, ...]
    root_case_filter: Mapping[str, tuple[str, ...]]
    source_snapshot: FormalPlanSnapshot
    source_snapshot_digest: str
    condition_count: int
    root_run_count: int
    selection_kind: str = "filtered"
    selected_first_attempt_ai_unit_count: int = -1
    provider_calls_made: int = 0
    schema_version: str = "tokenshare.paper_formal_execution_coverage.v1"

    def __post_init__(self) -> None:
        conditions = tuple(self.conditions)
        bindings = tuple(self.bindings)
        roots = tuple(self.roots)
        root_filter = {
            str(condition_id): tuple(case_ids)
            for condition_id, case_ids in self.root_case_filter.items()
        }
        if not conditions or len(conditions) != len(bindings):
            raise ValueError("execution coverage condition/binding mismatch")
        if self.selection_kind not in {
            "full",
            "filtered",
            "full_exp1_exp3_exp5",
            "representative_exp1_exp3_exp5",
            "exp5_capability_smoke",
        }:
            raise ValueError("execution coverage selection_kind is not preregistered")
        if type(self.source_snapshot) is not FormalPlanSnapshot:
            raise TypeError("source_snapshot must be a FormalPlanSnapshot")
        if self.source_snapshot.snapshot_digest != self.source_snapshot_digest:
            raise ValueError("execution coverage source snapshot digest mismatch")
        if self.condition_count != len(conditions):
            raise ValueError("execution coverage condition count mismatch")
        if self.root_run_count != len(roots):
            raise ValueError("execution coverage root count mismatch")
        if set(root_filter) != {condition.condition_id for condition in conditions}:
            raise ValueError("execution coverage root filter mismatch")
        selected_ai_units = sum(len(root.planned_ai_unit_ids) for root in roots)
        if self.selected_first_attempt_ai_unit_count == -1:
            object.__setattr__(
                self,
                "selected_first_attempt_ai_unit_count",
                selected_ai_units,
            )
        elif self.selected_first_attempt_ai_unit_count != selected_ai_units:
            raise ValueError("execution coverage AI-unit count mismatch")
        source_conditions = {
            item.condition.condition_id: item
            for item in self.source_snapshot.conditions
        }
        for condition, binding in zip(conditions, bindings, strict=True):
            source = source_conditions.get(condition.condition_id)
            if (
                source is None
                or condition is not source.condition
                or binding is not source.binding
                or binding.condition_id != condition.condition_id
                or binding.condition_digest != condition.condition_digest
            ):
                raise ValueError(
                    "execution coverage condition/binding authority mismatch"
                )
            selected_case_ids = root_filter[condition.condition_id]
            if (
                not selected_case_ids
                or len(set(selected_case_ids)) != len(selected_case_ids)
                or tuple(
                    case_id
                    for case_id in binding.selection.ordered_case_ids
                    if case_id in set(selected_case_ids)
                )
                != selected_case_ids
            ):
                raise ValueError("execution coverage root filter order mismatch")
        source_root_ids = {id(root) for root in self.source_snapshot.roots}
        expected_root_authority = tuple(
            (condition, binding, case_id)
            for condition, binding in zip(conditions, bindings, strict=True)
            for case_id in root_filter[condition.condition_id]
        )
        if len(roots) != len(expected_root_authority):
            raise ValueError("execution coverage root filter count mismatch")
        for root, (condition, binding, case_id) in zip(
            roots,
            expected_root_authority,
            strict=True,
        ):
            if (
                id(root) not in source_root_ids
                or root.condition is not condition
                or root.binding is not binding
                or root.case_id != case_id
                or root.condition_digest != condition.condition_digest
                or root.selection_digest != binding.selection.selection_digest
                or root.seed != condition.seed
                or root.repeat_id != condition.repeat_id
            ):
                raise ValueError("execution coverage root order/authority mismatch")
        if self.selection_kind == "full" and (
            conditions
            != tuple(item.condition for item in self.source_snapshot.conditions)
            or bindings
            != tuple(item.binding for item in self.source_snapshot.conditions)
            or roots != self.source_snapshot.roots
            or self.condition_count != self.source_snapshot.condition_count
            or self.root_run_count != self.source_snapshot.root_run_count
            or self.selected_first_attempt_ai_unit_count
            != self.source_snapshot.first_attempt_ai_unit_count
        ):
            raise ValueError("full execution coverage must equal its source snapshot")
        if self.provider_calls_made != 0:
            raise ValueError("execution coverage must not call providers")
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
            "selection_kind": self.selection_kind,
            "source_snapshot_digest": self.source_snapshot_digest,
            "condition_count": self.condition_count,
            "root_run_count": self.root_run_count,
            "selected_first_attempt_ai_unit_count": (
                self.selected_first_attempt_ai_unit_count
            ),
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
                    "planned_ai_unit_ids": list(root.planned_ai_unit_ids),
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


# 历史调用方继续引用同一 concrete type，不复制 representative authority。
FormalRepresentativeCoverage = FormalExecutionCoverage


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
        body = self._body()
        body["prepared_request"] = self.prepared_request.to_dict()
        return {**body, "request_identity_digest": self.request_identity_digest}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        snapshot: "FormalPlanSnapshot",
        root_index: Mapping[
            tuple[str, str, str], "FormalRootSnapshot"
        ] | None = None,
    ) -> "FormalPreparedRequestRecord":
        """从 strict persisted row 恢复 snapshot-owned condition/binding。"""

        expected = {
            "condition_id",
            "condition_digest",
            "selection_id",
            "selection_digest",
            "case_id",
            "case_record_digest",
            "planned_ai_unit_id",
            "sample_slot_index",
            "base_replacement_slot",
            "replacement_slot_ids",
            "replacement_policy_id",
            "source_provider_config_digest",
            "prepared_execution_config_digest",
            "provider_family",
            "provider_config_id",
            "model_entry_id",
            "provider_model_id",
            "reasoning_profile_id",
            "model_endpoint_identity_digest",
            "request_max_tokens",
            "request_timeout_seconds",
            "request_max_provider_attempts",
            "request_controls_digest",
            "prompt_profile_digest",
            "provider_request_identity",
            "prepared_request",
            "provider_calls_made",
            "request_identity_digest",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("formal prepared request record fields do not match v1 schema")
        if type(snapshot) is not FormalPlanSnapshot:
            raise TypeError("formal prepared request record requires typed snapshot")
        string_fields = expected - {
            "sample_slot_index",
            "base_replacement_slot",
            "replacement_slot_ids",
            "request_max_tokens",
            "request_timeout_seconds",
            "request_max_provider_attempts",
            "provider_request_identity",
            "prepared_request",
            "provider_calls_made",
        }
        if any(
            not isinstance(value[field], str) or not value[field]
            for field in string_fields
        ):
            raise ValueError("formal prepared request record string field is invalid")
        for field in ("sample_slot_index", "base_replacement_slot"):
            item = value[field]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"formal prepared request record {field} is invalid")
        for field in (
            "request_max_tokens",
            "request_timeout_seconds",
            "request_max_provider_attempts",
        ):
            _positive_int(value[field], field)
        if (
            type(value["provider_calls_made"]) is not int
            or value["provider_calls_made"] != 0
        ):
            raise ValueError("formal prepared request record provider_calls_made drift")
        raw_slots = value["replacement_slot_ids"]
        if (
            isinstance(raw_slots, (str, bytes, bytearray))
            or not isinstance(raw_slots, Sequence)
            or not raw_slots
            or any(
                isinstance(slot, bool) or not isinstance(slot, int) or slot < 0
                for slot in raw_slots
            )
        ):
            raise ValueError("formal prepared request record replacement slots are invalid")
        slots = tuple(raw_slots)
        if tuple(sorted(set(slots))) != slots:
            raise ValueError("formal prepared request record replacement slots are not canonical")
        raw_identity = value["provider_request_identity"]
        raw_prepared = value["prepared_request"]
        if type(raw_identity) is not dict or not isinstance(raw_prepared, Mapping):
            raise ValueError("formal prepared request record nested body is invalid")
        key = (
            value["condition_id"],
            value["case_id"],
            value["planned_ai_unit_id"],
        )
        index = (
            _formal_prepared_inventory_root_index(snapshot)
            if root_index is None
            else root_index
        )
        root = index.get(key)
        if root is None:
            raise ValueError("formal prepared request record snapshot key is not unique")
        if (
            value["condition_digest"] != root.condition.condition_digest
            or value["selection_id"] != root.binding.selection.selection_id
            or value["selection_digest"]
            != root.binding.selection.selection_digest
        ):
            raise ValueError("formal prepared request record snapshot digest drift")
        prepared = PreparedOutboundRequest.from_dict(raw_prepared)
        record = cls(
            condition=root.condition,
            binding=root.binding,
            case_id=value["case_id"],
            case_record_digest=value["case_record_digest"],
            planned_ai_unit_id=value["planned_ai_unit_id"],
            sample_slot_index=value["sample_slot_index"],
            base_replacement_slot=value["base_replacement_slot"],
            replacement_slot_ids=slots,
            replacement_policy_id=value["replacement_policy_id"],
            source_provider_config_digest=value["source_provider_config_digest"],
            prepared_execution_config_digest=value[
                "prepared_execution_config_digest"
            ],
            provider_family=value["provider_family"],
            provider_config_id=value["provider_config_id"],
            model_entry_id=value["model_entry_id"],
            provider_model_id=value["provider_model_id"],
            reasoning_profile_id=value["reasoning_profile_id"],
            model_endpoint_identity_digest=value[
                "model_endpoint_identity_digest"
            ],
            request_max_tokens=value["request_max_tokens"],
            request_timeout_seconds=value["request_timeout_seconds"],
            request_max_provider_attempts=value[
                "request_max_provider_attempts"
            ],
            request_controls_digest=value["request_controls_digest"],
            prompt_profile_digest=value["prompt_profile_digest"],
            provider_request_identity=dict(raw_identity),
            prepared_request=prepared,
            provider_calls_made=0,
        )
        _validate_persisted_formal_prepared_record(record=record, root=root)
        if record.request_identity_digest != value["request_identity_digest"]:
            raise ValueError("formal prepared request record identity digest mismatch")
        return record

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_count": self.record_count,
            "unique_inference_request_count": self.unique_inference_request_count,
            "provider_calls_made": self.provider_calls_made,
            "source_snapshot_digest": self.source_snapshot_digest,
            "records": [record.to_dict() for record in self.records],
            "inventory_digest": self.inventory_digest,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        snapshot: "FormalPlanSnapshot",
    ) -> "FormalPreparedRequestInventory":
        expected = {
            "schema_version",
            "record_count",
            "unique_inference_request_count",
            "provider_calls_made",
            "source_snapshot_digest",
            "records",
            "inventory_digest",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("formal prepared inventory fields do not match v1 schema")
        raw_records = value["records"]
        if isinstance(raw_records, (str, bytes, bytearray)) or not isinstance(
            raw_records, Sequence
        ):
            raise ValueError("formal prepared inventory records must be a sequence")
        root_index = _formal_prepared_inventory_root_index(snapshot)
        records = tuple(
            FormalPreparedRequestRecord.from_dict(
                item,
                snapshot=snapshot,
                root_index=root_index,
            )
            for item in raw_records
        )
        for field in ("record_count", "unique_inference_request_count"):
            item = value[field]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"formal prepared inventory {field} is invalid")
        if (
            type(value["provider_calls_made"]) is not int
            or value["provider_calls_made"] != 0
        ):
            raise ValueError("formal prepared inventory provider_calls_made drift")
        inventory = cls(
            records=records,
            record_count=value["record_count"],
            unique_inference_request_count=value["unique_inference_request_count"],
            provider_calls_made=0,
            source_snapshot_digest=value["source_snapshot_digest"],
            schema_version=value["schema_version"],
        )
        _validate_persisted_formal_prepared_inventory(
            inventory=inventory,
            snapshot=snapshot,
            root_index=root_index,
        )
        if inventory.inventory_digest != value["inventory_digest"]:
            raise ValueError("formal prepared inventory digest mismatch")
        return inventory


@dataclass(frozen=True, kw_only=True)
class _PreparedRuntimeTemplate:
    planned_ai_unit_id: str
    source_provider_config_digest: str
    prepared_execution_config_digest: str
    provider_request_identity: Mapping[str, Any]
    prepared_request: PreparedOutboundRequest


class _TransientPlanningArtifactStore:
    """只供 deterministic request planning 使用的进程内 ArtifactStore。"""

    def __init__(self) -> None:
        self._bytes_by_uri: dict[str, bytes] = {}
        self._refs_by_uri: dict[str, ArtifactRef] = {}
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    @property
    def retained_artifact_count(self) -> int:
        return len(self._bytes_by_uri)

    def save_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str,
        artifact_type: str,
        media_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
        durability_hook: Any = None,
    ) -> ArtifactRef:
        self._require_active()
        if durability_hook is not None:
            raise ValueError("transient planning store does not provide durability hooks")
        encoded = bytes(data)
        artifact_filename = _planning_artifact_filename(artifact_id)
        uri = f"artifacts/{artifact_filename}"
        content_hash = f"sha256:{sha256(encoded).hexdigest()}"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=uri,
            content_hash=content_hash,
            size_bytes=len(encoded),
            media_type=media_type,
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
        )
        existing_bytes = self._bytes_by_uri.get(uri)
        existing_ref = self._refs_by_uri.get(uri)
        if existing_bytes is not None and existing_bytes != encoded:
            raise ValueError(
                f"artifact_id already exists with different content: {artifact_id}"
            )
        if existing_ref is not None and existing_ref != ref:
            raise ValueError(
                f"artifact_id already exists with different metadata: {artifact_id}"
            )
        self._bytes_by_uri[uri] = encoded
        self._refs_by_uri[uri] = ref
        return ref

    def save_content_addressed_bytes(
        self,
        data: bytes,
        *,
        artifact_type: str,
        media_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
        durability_hook: Any = None,
    ) -> ArtifactRef:
        encoded = bytes(data)
        return self.save_bytes(
            encoded,
            artifact_id=sha256(encoded).hexdigest(),
            artifact_type=artifact_type,
            media_type=media_type,
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
            durability_hook=durability_hook,
        )

    def save_json(
        self,
        data: JsonObject,
        *,
        artifact_id: str,
        artifact_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
    ) -> ArtifactRef:
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.save_bytes(
            encoded,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            media_type="application/json",
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
        )

    def read_bytes(self, artifact_ref: ArtifactRef) -> bytes:
        self._require_active()
        try:
            return self._bytes_by_uri[artifact_ref.uri]
        except KeyError as exc:
            raise FileNotFoundError(artifact_ref.uri) from exc

    def verify(self, artifact_ref: ArtifactRef) -> bool:
        try:
            data = self.read_bytes(artifact_ref)
        except FileNotFoundError:
            return False
        return (
            len(data) == artifact_ref.size_bytes
            and f"sha256:{sha256(data).hexdigest()}" == artifact_ref.content_hash
        )

    def release(self) -> None:
        self._bytes_by_uri.clear()
        self._refs_by_uri.clear()
        self._released = True

    def _require_active(self) -> None:
        if self._released:
            raise RuntimeError("transient planning artifact store has been released")


def _planning_artifact_filename(artifact_id: str) -> str:
    if ":" not in artifact_id:
        return artifact_id
    readable = artifact_id.replace(":", "_")
    identity_suffix = sha256(artifact_id.encode("utf-8")).hexdigest()[:16]
    return f"{readable}--{identity_suffix}"


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
        or type(inventory.provider_calls_made) is not int
        or inventory.provider_calls_made != 0
        or len(snapshot.roots) != snapshot.root_run_count
        or len(snapshot.conditions) != snapshot.condition_count
        or expected_count != snapshot.first_attempt_ai_unit_count
        or inventory.record_count != len(inventory.records)
        or inventory.record_count != expected_count
        or inventory.source_snapshot_digest != snapshot.snapshot_digest
    ):
        raise ValueError("formal prepared inventory metadata drift")


def _formal_prepared_inventory_root_index(
    snapshot: FormalPlanSnapshot,
) -> dict[tuple[str, str, str], FormalRootSnapshot]:
    index: dict[tuple[str, str, str], FormalRootSnapshot] = {}
    for root in snapshot.roots:
        for planned_ai_unit_id in root.planned_ai_unit_ids:
            key = (
                root.condition.condition_id,
                root.case_id,
                planned_ai_unit_id,
            )
            if key in index:
                raise ValueError(
                    "formal prepared inventory snapshot keyset is not unique"
                )
            index[key] = root
    return index


def _formal_prepared_inventory_ordered_keys(
    snapshot: FormalPlanSnapshot,
    *,
    root_index: Mapping[
        tuple[str, str, str], FormalRootSnapshot
    ] | None = None,
) -> tuple[tuple[str, str, str], ...]:
    index = (
        _formal_prepared_inventory_root_index(snapshot)
        if root_index is None
        else root_index
    )
    return tuple(index)


def _validate_persisted_formal_prepared_record(
    *,
    record: FormalPreparedRequestRecord,
    root: FormalRootSnapshot,
) -> None:
    if (
        type(record.provider_calls_made) is not int
        or record.provider_calls_made != 0
    ):
        raise ValueError("formal prepared request record provider_calls_made drift")
    if record.condition is not root.condition or record.binding is not root.binding:
        raise ValueError("formal prepared request record snapshot authority drift")
    expected_fields = {
        "case_id": root.case_id,
        "case_record_digest": root.case_record_digest,
        "sample_slot_index": root.repeat_id,
        "base_replacement_slot": 0,
        "replacement_slot_ids": replacement_slots_for(
            experiment_id=root.condition.experiment_id,
            fault_type=str(root.condition.fault_type),
            ablation_mode=str(root.condition.ablation_mode),
        ),
        "replacement_policy_id": "formal_attempt_budget.v1",
        "source_provider_config_digest": (
            root.endpoint_controls.source_provider_config_digest
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
            raise ValueError(
                f"formal prepared request record {field_name} drift"
            )
    for field_name in (
        "case_record_digest",
        "source_provider_config_digest",
        "prepared_execution_config_digest",
        "model_endpoint_identity_digest",
        "request_controls_digest",
        "prompt_profile_digest",
    ):
        _required_digest(getattr(record, field_name), field_name)
    prepared = validate_prepared_request(record.prepared_request)
    if (
        prepared.case_id
        != formal_runtime_task_id(root.condition.domain, record.case_id)
        or prepared.planned_ai_unit_id != record.planned_ai_unit_id
        or prepared.sample_slot_index != record.sample_slot_index
        or prepared.replacement_slot != record.base_replacement_slot
        or prepared.provider_config_digest
        != record.prepared_execution_config_digest
        or prepared.entry_id != record.model_entry_id
        or prepared.configured_model != record.provider_model_id
        or prepared.plugin_id != root.plugin_id
        or prepared.plugin_version != root.plugin_version
    ):
        raise ValueError("formal prepared request nested identity drift")
    _validate_provider_request_identity_against_prepared(
        provider_request_identity=record.provider_request_identity,
        prepared=prepared,
        provider_family=record.provider_family,
    )
    expected_prompt_digest = digest_json(
        {
            "body_digest": prepared.body_digest,
            "prompt_profile_id": prepared.prompt_profile_id,
            "prompt_serialization_schema": prepared.prompt_serialization_schema,
        }
    )
    if record.prompt_profile_digest != expected_prompt_digest:
        raise ValueError("formal prepared request prompt profile digest drift")


def _validate_persisted_formal_prepared_inventory(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
    root_index: Mapping[
        tuple[str, str, str], FormalRootSnapshot
    ] | None = None,
) -> None:
    _validate_formal_prepared_inventory_metadata(
        inventory=inventory,
        snapshot=snapshot,
    )
    index = (
        _formal_prepared_inventory_root_index(snapshot)
        if root_index is None
        else root_index
    )
    expected_keys = _formal_prepared_inventory_ordered_keys(
        snapshot,
        root_index=index,
    )
    actual_keys = tuple(
        (
            record.condition.condition_id,
            record.case_id,
            record.planned_ai_unit_id,
        )
        for record in inventory.records
    )
    if actual_keys != expected_keys:
        raise ValueError("formal prepared inventory ordered keyset drift")
    for record, key in zip(inventory.records, actual_keys, strict=True):
        _validate_persisted_formal_prepared_record(
            record=record,
            root=index[key],
        )
    unique_count = len(
        {record.inference_request_digest for record in inventory.records}
    )
    if inventory.unique_inference_request_count != unique_count:
        raise ValueError("formal prepared inventory unique request count drift")


FORMAL_PREPARED_INVENTORY_RECORDS_NAME = (
    "formal_prepared_request_inventory.v1.jsonl"
)
FORMAL_PREPARED_INVENTORY_MANIFEST_NAME = (
    "formal_prepared_request_inventory_manifest.v1.json"
)
FORMAL_PREPARED_INVENTORY_MANIFEST_SCHEMA_VERSION = (
    "tokenshare.paper_formal_prepared_request_inventory_manifest.v1"
)


def _formal_prepared_inventory_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _atomic_formal_prepared_inventory_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _formal_prepared_inventory_manifest_body(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
    records_sha256: str,
    records_size_bytes: int,
    root_index: Mapping[tuple[str, str, str], FormalRootSnapshot],
) -> dict[str, Any]:
    ordered_keys = _formal_prepared_inventory_ordered_keys(
        snapshot,
        root_index=root_index,
    )
    return {
        "schema_version": FORMAL_PREPARED_INVENTORY_MANIFEST_SCHEMA_VERSION,
        "inventory_schema_version": inventory.schema_version,
        "records_path": FORMAL_PREPARED_INVENTORY_RECORDS_NAME,
        "records_sha256": records_sha256,
        "records_size_bytes": records_size_bytes,
        "record_count": inventory.record_count,
        "unique_inference_request_count": inventory.unique_inference_request_count,
        "provider_calls_made": inventory.provider_calls_made,
        "source_snapshot_digest": inventory.source_snapshot_digest,
        "ordered_keyset_digest": digest_json(
            {"ordered_keys": [list(key) for key in ordered_keys]}
        ),
        "inventory_digest": inventory.inventory_digest,
    }


def persist_formal_prepared_request_inventory(
    *,
    inventory: FormalPreparedRequestInventory,
    snapshot: FormalPlanSnapshot,
    output_root: str | Path,
) -> Mapping[str, Any]:
    """stream 写入 canonical JSONL，并最后原子提交 digest-bound manifest。"""

    if type(inventory) is not FormalPreparedRequestInventory:
        raise TypeError("typed formal prepared inventory is required")
    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("typed formal plan snapshot is required")
    root_index = _formal_prepared_inventory_root_index(snapshot)
    _validate_persisted_formal_prepared_inventory(
        inventory=inventory,
        snapshot=snapshot,
        root_index=root_index,
    )
    root = Path(output_root).resolve(strict=False)
    final_records = (root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME).resolve(
        strict=False
    )
    final_manifest = (root / FORMAL_PREPARED_INVENTORY_MANIFEST_NAME).resolve(
        strict=False
    )
    if root not in final_records.parents or root not in final_manifest.parents:
        raise ValueError("formal prepared inventory path escaped output root")
    if root.exists():
        if not final_records.is_file() or not final_manifest.is_file():
            raise ValueError("formal prepared inventory persistence is partial")
        existing_manifest = _read_formal_prepared_inventory_manifest(final_manifest)
        existing = load_formal_prepared_request_inventory(
            output_root=root,
            snapshot=snapshot,
            expected_manifest=existing_manifest,
        )
        if existing != inventory:
            raise ValueError("formal prepared inventory existing authority drift")
        return MappingProxyType(dict(existing_manifest))
    root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f".{root.name}.",
            suffix=".staging",
            dir=root.parent,
        )
    ).resolve(strict=True)
    if staging_root.parent != root.parent:
        raise ValueError("formal prepared inventory staging path escaped parent")
    try:
        records_path = staging_root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME
        manifest_path = staging_root / FORMAL_PREPARED_INVENTORY_MANIFEST_NAME
        records_digest = sha256()
        records_size_bytes = 0
        with records_path.open("xb") as stream:
            for record in inventory.records:
                row = _formal_prepared_inventory_json_bytes(record.to_dict())
                stream.write(row)
                records_digest.update(row)
                records_size_bytes += len(row)
            stream.flush()
            os.fsync(stream.fileno())
        body = _formal_prepared_inventory_manifest_body(
            inventory=inventory,
            snapshot=snapshot,
            records_sha256=f"sha256:{records_digest.hexdigest()}",
            records_size_bytes=records_size_bytes,
            root_index=root_index,
        )
        manifest = {**body, "manifest_digest": digest_json(body)}
        _atomic_formal_prepared_inventory_bytes(
            manifest_path,
            _formal_prepared_inventory_json_bytes(manifest),
        )
        os.replace(staging_root, root)
    except Exception:
        if staging_root.exists() and staging_root.parent == root.parent:
            shutil.rmtree(staging_root)
        raise
    return MappingProxyType(manifest)


def _read_formal_prepared_inventory_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("formal prepared inventory manifest is unreadable") from exc
    expected = {
        "schema_version",
        "inventory_schema_version",
        "records_path",
        "records_sha256",
        "records_size_bytes",
        "record_count",
        "unique_inference_request_count",
        "provider_calls_made",
        "source_snapshot_digest",
        "ordered_keyset_digest",
        "inventory_digest",
        "manifest_digest",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("formal prepared inventory manifest fields drift")
    body = dict(value)
    declared = body.pop("manifest_digest")
    if (
        value["schema_version"]
        != FORMAL_PREPARED_INVENTORY_MANIFEST_SCHEMA_VERSION
        or value["inventory_schema_version"]
        != "tokenshare.paper_formal_prepared_request_inventory.v1"
        or value["records_path"] != FORMAL_PREPARED_INVENTORY_RECORDS_NAME
        or declared != digest_json(body)
    ):
        raise ValueError("formal prepared inventory manifest digest or schema drift")
    return dict(value)


def load_formal_prepared_request_inventory(
    *,
    output_root: str | Path,
    snapshot: FormalPlanSnapshot,
    expected_manifest: Mapping[str, Any] | None = None,
) -> FormalPreparedRequestInventory:
    """只消费 persisted JSONL；不调用 adapter、prepare 或 freeze。"""

    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("typed formal plan snapshot is required")
    root = Path(output_root).resolve(strict=False)
    manifest_path = (root / FORMAL_PREPARED_INVENTORY_MANIFEST_NAME).resolve(
        strict=False
    )
    records_path = (root / FORMAL_PREPARED_INVENTORY_RECORDS_NAME).resolve(
        strict=False
    )
    if root not in manifest_path.parents or root not in records_path.parents:
        raise ValueError("formal prepared inventory path escaped output root")
    manifest = _read_formal_prepared_inventory_manifest(manifest_path)
    if expected_manifest is not None and (
        not isinstance(expected_manifest, Mapping)
        or dict(expected_manifest) != manifest
    ):
        raise ValueError("formal prepared inventory expected manifest mismatch")
    for field in (
        "record_count",
        "unique_inference_request_count",
        "records_size_bytes",
    ):
        item = manifest[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"formal prepared inventory manifest {field} is invalid")
    if (
        type(manifest["provider_calls_made"]) is not int
        or manifest["provider_calls_made"] != 0
    ):
        raise ValueError(
            "formal prepared inventory manifest provider_calls_made drift"
        )
    if manifest["source_snapshot_digest"] != snapshot.snapshot_digest:
        raise ValueError("formal prepared inventory manifest snapshot lineage drift")
    root_index = _formal_prepared_inventory_root_index(snapshot)
    records: list[FormalPreparedRequestRecord] = []
    content_digest = sha256()
    content_size = 0
    try:
        with records_path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                content_digest.update(raw_line)
                content_size += len(raw_line)
                if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
                    raise ValueError(
                        f"formal prepared inventory JSONL line {line_number} is not canonical"
                    )
                try:
                    value = json.loads(raw_line[:-1].decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"formal prepared inventory JSONL line {line_number} is invalid"
                    ) from exc
                if _formal_prepared_inventory_json_bytes(value) != raw_line:
                    raise ValueError(
                        f"formal prepared inventory JSONL line {line_number} is not canonical"
                    )
                records.append(
                    FormalPreparedRequestRecord.from_dict(
                        value,
                        snapshot=snapshot,
                        root_index=root_index,
                    )
                )
    except OSError as exc:
        raise ValueError("formal prepared inventory records are unreadable") from exc
    if (
        len(records) != manifest["record_count"]
        or content_size != manifest["records_size_bytes"]
        or f"sha256:{content_digest.hexdigest()}" != manifest["records_sha256"]
    ):
        raise ValueError("formal prepared inventory records digest or count drift")
    inventory = FormalPreparedRequestInventory(
        records=tuple(records),
        record_count=manifest["record_count"],
        unique_inference_request_count=manifest[
            "unique_inference_request_count"
        ],
        provider_calls_made=0,
        source_snapshot_digest=manifest["source_snapshot_digest"],
        schema_version=manifest["inventory_schema_version"],
    )
    _validate_persisted_formal_prepared_inventory(
        inventory=inventory,
        snapshot=snapshot,
        root_index=root_index,
    )
    ordered_keyset_digest = digest_json(
        {
            "ordered_keys": [
                list(key)
                for key in _formal_prepared_inventory_ordered_keys(
                    snapshot,
                    root_index=root_index,
                )
            ]
        }
    )
    if (
        inventory.inventory_digest != manifest["inventory_digest"]
        or ordered_keyset_digest != manifest["ordered_keyset_digest"]
    ):
        raise ValueError("formal prepared inventory manifest authority digest drift")
    return inventory


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


def freeze_formal_root_prepared_replacement_requests(
    *,
    root: FormalRootSnapshot,
    base_records: Sequence[FormalPreparedRequestRecord],
    catalog_manifest: PaperInputCatalogManifest,
    ai_api_configs: Mapping[str, Any],
    planning_artifact_root: str | Path,
) -> tuple[PreparedOutboundRequest, ...]:
    """经正式 runtime/trace binding 重建 root 的全部 attempt ordinals。"""

    if type(root) is not FormalRootSnapshot:
        raise TypeError("root must be a FormalRootSnapshot")
    records = tuple(base_records)
    if (
        not records
        or tuple(record.planned_ai_unit_id for record in records)
        != root.planned_ai_unit_ids
    ):
        raise ValueError("formal replacement base-record coverage drift")
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    try:
        case = cases_by_id[root.case_id]
    except KeyError as exc:
        raise ValueError("formal replacement root is absent from catalog") from exc
    slot_ids = replacement_slots_for(
        experiment_id=root.condition.experiment_id,
        fault_type=str(root.condition.fault_type),
        ablation_mode=str(root.condition.ablation_mode),
    )
    if any(
        record.condition is not root.condition
        or record.binding is not root.binding
        or record.case_id != root.case_id
        or record.case_record_digest != root.case_record_digest
        or record.sample_slot_index != root.repeat_id
        or record.base_replacement_slot != 0
        or record.replacement_slot_ids != slot_ids
        or record.replacement_policy_id != "formal_attempt_budget.v1"
        or record.provider_calls_made != 0
        for record in records
    ):
        raise ValueError("formal replacement base-record identity drift")
    templates = _prepare_formal_root_slot_templates(
        root=root,
        case=case,
        ai_api_configs=ai_api_configs,
        artifact_root=Path(planning_artifact_root),
        replacement_slot_ids=slot_ids,
        bind_as_trace=True,
    )
    expected_order = tuple(
        (unit_id, slot)
        for unit_id in root.planned_ai_unit_ids
        for slot in slot_ids
    )
    actual_order = tuple(
        (template.planned_ai_unit_id, template.prepared_request.replacement_slot)
        for template in templates
    )
    if actual_order != expected_order:
        raise ValueError("formal replacement prepared-request order drift")
    base_by_unit = {record.planned_ai_unit_id: record for record in records}
    for template in templates:
        prepared = validate_prepared_request(template.prepared_request)
        base = base_by_unit[template.planned_ai_unit_id]
        if (
            prepared.provider_config_digest
            != base.prepared_execution_config_digest
            or template.source_provider_config_digest
            != base.source_provider_config_digest
            or prepared.entry_id != base.model_entry_id
            or prepared.configured_model != base.provider_model_id
        ):
            raise ValueError("formal replacement runtime request identity drift")
        if prepared.replacement_slot == 0 and (
            prepared != base.prepared_request
            or template.provider_request_identity
            != dict(base.provider_request_identity)
        ):
            raise ValueError("formal replacement runtime slot zero drift")
    return tuple(template.prepared_request for template in templates)


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
    return _prepare_formal_root_slot_templates(
        root=root,
        case=case,
        ai_api_configs=ai_api_configs,
        artifact_root=artifact_root,
        replacement_slot_ids=(0,),
        bind_as_trace=False,
    )


def _prepare_formal_root_slot_templates(
    *,
    root: FormalRootSnapshot,
    case: Mapping[str, Any],
    ai_api_configs: Mapping[str, Any],
    artifact_root: Path,
    replacement_slot_ids: Sequence[int],
    bind_as_trace: bool,
) -> tuple[_PreparedRuntimeTemplate, ...]:
    slots = tuple(replacement_slot_ids)
    if (
        not slots
        or tuple(sorted(set(slots))) != slots
        or any(type(slot) is not int or slot < 0 for slot in slots)
    ):
        raise ValueError("formal prepared replacement slots are invalid")
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
        allow_pricing_refresh=(
            root.condition.experiment_id == FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
        ),
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
    # `artifact_root` remains part of the public authority signature, but these
    # intermediate prompt artifacts are consumed before this function returns.
    # Persisted authority contains the exact prepared wire bytes, not these refs.
    del artifact_root
    store = _TransientPlanningArtifactStore()
    try:
        return _prepare_formal_root_slot_templates_in_store(
            root=root,
            planned_case=planned_case,
            source_config=source_config,
            prepared_config=prepared_config,
            prepared_entry=prepared_entries[0],
            executor_requirements=executor_requirements,
            protocol_config=protocol_config,
            slots=slots,
            bind_as_trace=bind_as_trace,
            store=store,
        )
    finally:
        store.release()


def _prepare_formal_root_slot_templates_in_store(
    *,
    root: FormalRootSnapshot,
    planned_case: Mapping[str, Any],
    source_config: Any,
    prepared_config: Any,
    prepared_entry: Any,
    executor_requirements: Any,
    protocol_config: ProtocolConfig,
    slots: tuple[int, ...],
    bind_as_trace: bool,
    store: _TransientPlanningArtifactStore,
) -> tuple[_PreparedRuntimeTemplate, ...]:
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
        trace_binding = _planning_trace_source_binding(
            root=root,
            planned_ai_unit_id=planned_ai_unit_id,
            replacement_slot_ids=slots,
        )
        for replacement_slot in slots:
            attempt, lease = _formal_planning_attempt_and_lease(
                case_id=root.case_id,
                unit_id=unit.unit_id,
                task_id=unit.task_id,
                attempt_ordinal=replacement_slot,
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
                replacement_slot=replacement_slot,
            )
            if bind_as_trace:
                # 正式 Factor/Lean trace runtime 都先把 persisted ordinal 写回
                # ExecutionRequest，再绑定 immutable source slot。
                request = bind_trace_execution_request(
                    replace(request, attempt_ordinal=attempt.attempt_ordinal),
                    trace_binding,
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
                entry=prepared_entry,
            )
            prepared = outbound.prepared_request
            if (
                prepared.planned_ai_unit_id != planned_ai_unit_id
                or prepared.sample_slot_index != root.repeat_id
                or prepared.replacement_slot != replacement_slot
                or prepared.configured_model
                != root.endpoint_controls.provider_model_id
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
        allow_pricing_refresh=(
            root.condition.experiment_id == FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
        ),
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


def _planning_trace_source_binding(
    *,
    root: FormalRootSnapshot,
    planned_ai_unit_id: str,
    replacement_slot_ids: Sequence[int],
) -> TraceSourceBinding:
    """构造仅用于冻结 wire identity 的 trace binding；不冒充 bank terminal。"""

    identity = {
        "schema_version": "tokenshare.formal_acquisition_trace_binding.v1",
        "condition_id": root.condition.condition_id,
        "case_id": root.case_id,
        "planned_ai_unit_id": planned_ai_unit_id,
        "sample_slot_index": root.repeat_id,
    }
    return TraceSourceBinding.create(
        planned_ai_unit_id=planned_ai_unit_id,
        sample_slot_index=root.repeat_id,
        bank_root_id=f"formal-acquisition-plan:{digest_json(identity)}",
        manifest_digest=digest_json({**identity, "kind": "planning-manifest"}),
        replacements=tuple(
            TraceReplacementBinding(
                replacement_slot=slot,
                entry_id=f"formal-acquisition-slot:{slot}",
                inference_request_digest=digest_json(
                    {**identity, "replacement_slot": slot}
                ),
            )
            for slot in replacement_slot_ids
        ),
        source_evidence_class="synthetic_regression",
    )


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
    attempt_ordinal: int = 0,
) -> tuple[Attempt, Lease]:
    if type(attempt_ordinal) is not int or attempt_ordinal < 0:
        raise ValueError("formal planning attempt ordinal must be non-negative")
    identity = digest_json(
        {
            "case_id": case_id,
            "unit_id": unit_id,
            "attempt_ordinal": attempt_ordinal,
        }
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
        attempt_ordinal=attempt_ordinal,
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


def freeze_paper_exp5_capability_smoke_coverage(
    *,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    budget: PaperBudgetResult,
    ai_api_configs: Mapping[str, Any],
    root_case_filter: Mapping[str, Sequence[str]],
) -> FormalExecutionCoverage:
    """冻结仅限 Exp5 capability smoke 的 selected-root authority，不扩张为 formal matrix。"""

    plans = tuple(dispatch_plans)
    if (
        len(plans) != 1
        or plans[0].experiment_id != FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
        or plans[0].status != "planned"
        or plans[0].paper_eligible_possible
    ):
        raise ValueError("Exp5 capability smoke dispatch authority is invalid")
    conditions_and_bindings = _ordered_condition_bindings(plans[0])
    if not conditions_and_bindings or any(
        condition.experiment_id != FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID
        for condition, _binding in conditions_and_bindings
    ):
        raise ValueError("Exp5 capability smoke must contain only planned Exp5 conditions")
    validate_paper_formal_plan_bindings(
        conditions=tuple(condition for condition, _binding in conditions_and_bindings),
        bindings=tuple(binding for _condition, binding in conditions_and_bindings),
        catalog_manifest=catalog_manifest,
    )
    normalized_filter = {
        str(condition_id): tuple(case_ids)
        for condition_id, case_ids in root_case_filter.items()
    }
    condition_ids = tuple(
        condition.condition_id for condition, _binding in conditions_and_bindings
    )
    if set(normalized_filter) != set(condition_ids):
        raise ValueError("Exp5 capability smoke root filter does not match conditions")
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    commitments = _budget_ai_unit_commitments(budget)
    condition_rows: list[FormalConditionSnapshot] = []
    root_rows: list[FormalRootSnapshot] = []
    expected_commitment_keys: set[tuple[str, str]] = set()
    for condition, binding in conditions_and_bindings:
        selected_case_ids = normalized_filter[condition.condition_id]
        if (
            not selected_case_ids
            or len(set(selected_case_ids)) != len(selected_case_ids)
            or tuple(
                case_id
                for case_id in binding.selection.ordered_case_ids
                if case_id in set(selected_case_ids)
            )
            != selected_case_ids
        ):
            raise ValueError("Exp5 capability smoke root filter order is invalid")
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
        for case_id in selected_case_ids:
            key = (condition.condition_id, case_id)
            commitment = commitments.get(key)
            case = cases_by_id.get(case_id)
            if case is None or commitment is None:
                raise ValueError("Exp5 capability smoke budget commitment is missing")
            if (
                commitment.get("condition_id") != condition.condition_id
                or commitment.get("condition_digest") != condition.condition_digest
                or commitment.get("case_id") != case_id
                or commitment.get("case_digest") != digest_json(case)
            ):
                raise ValueError("Exp5 capability smoke budget commitment drift")
            unit_ids = _string_tuple(
                commitment.get("planned_ai_unit_ids"),
                "Exp5 capability planned AI-unit ids",
            )
            split_digest = _required_digest(
                commitment.get("split_profile_digest"),
                "Exp5 capability split profile digest",
            )
            plugin_id, plugin_version = _plugin_identity(condition.domain)
            root_rows.append(
                FormalRootSnapshot(
                    condition=condition,
                    binding=binding,
                    case_id=case_id,
                    case_record_digest=digest_json(case),
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
            expected_commitment_keys.add(key)
    if set(commitments) != expected_commitment_keys:
        raise ValueError("Exp5 capability smoke budget contains unselected roots")
    selected_ai_units = sum(len(root.planned_ai_unit_ids) for root in root_rows)
    if (
        budget.planned_conditions != len(condition_rows)
        or budget.planned_root_runs != len(root_rows)
        or budget.planned_ai_units != selected_ai_units
    ):
        raise ValueError("Exp5 capability smoke budget totals drift")
    snapshot = FormalPlanSnapshot(
        conditions=tuple(condition_rows),
        roots=tuple(root_rows),
        condition_count=len(condition_rows),
        root_run_count=len(root_rows),
        first_attempt_ai_unit_count=selected_ai_units,
        provider_calls_made=0,
        budget_digest=budget.budget_digest,
    )
    return FormalExecutionCoverage(
        conditions=tuple(item.condition for item in condition_rows),
        bindings=tuple(item.binding for item in condition_rows),
        roots=tuple(root_rows),
        root_case_filter=normalized_filter,
        source_snapshot=snapshot,
        source_snapshot_digest=snapshot.snapshot_digest,
        condition_count=len(condition_rows),
        root_run_count=len(root_rows),
        selection_kind="exp5_capability_smoke",
        selected_first_attempt_ai_unit_count=selected_ai_units,
        provider_calls_made=0,
    )


def derive_paper_formal_execution_coverage(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    selected_condition_ids: Sequence[str],
    root_case_filter: Mapping[str, Sequence[str]],
    selection_kind: str,
) -> FormalExecutionCoverage:
    """从同一 full snapshot 按显式 condition/root filter 派生 typed 视图。"""

    plans, condition_rows, roots_by_condition = _validated_coverage_authority(
        snapshot=snapshot,
        dispatch_plans=dispatch_plans,
        catalog_manifest=catalog_manifest,
    )
    selected_ids = tuple(selected_condition_ids)
    if (
        not selected_ids
        or any(not isinstance(condition_id, str) or not condition_id for condition_id in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
    ):
        raise ValueError("execution coverage condition ids must be unique strings")
    rows_by_id = {
        item.condition.condition_id: item
        for item in condition_rows
    }
    try:
        selected_rows = tuple(rows_by_id[condition_id] for condition_id in selected_ids)
    except KeyError as exc:
        raise ValueError("execution coverage references an unknown condition") from exc
    normalized_filter = validate_paper_formal_root_case_filter(
        dispatch_plans=plans,
        selected_condition_ids=selected_ids,
        root_case_filter=root_case_filter,
    )
    selected_roots = tuple(
        root
        for item in selected_rows
        for root in roots_by_condition[item.condition.condition_id]
        if root.case_id in set(normalized_filter[item.condition.condition_id])
    )
    return FormalExecutionCoverage(
        conditions=tuple(item.condition for item in selected_rows),
        bindings=tuple(item.binding for item in selected_rows),
        roots=selected_roots,
        root_case_filter=normalized_filter,
        source_snapshot=snapshot,
        source_snapshot_digest=snapshot.snapshot_digest,
        condition_count=len(selected_rows),
        root_run_count=len(selected_roots),
        selection_kind=selection_kind,
        selected_first_attempt_ai_unit_count=sum(
            len(root.planned_ai_unit_ids) for root in selected_roots
        ),
        provider_calls_made=0,
    )


def derive_paper_formal_full_coverage(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
) -> FormalExecutionCoverage:
    """显式选择 snapshot 中全部 condition/root，不重建或复制 full plan。"""

    selected_condition_ids = tuple(
        item.condition.condition_id for item in snapshot.conditions
    )
    root_filter = {
        condition_id: tuple(
            root.case_id
            for root in snapshot.roots
            if root.condition.condition_id == condition_id
        )
        for condition_id in selected_condition_ids
    }
    return derive_paper_formal_execution_coverage(
        snapshot=snapshot,
        dispatch_plans=dispatch_plans,
        catalog_manifest=catalog_manifest,
        selected_condition_ids=selected_condition_ids,
        root_case_filter=root_filter,
        selection_kind="full",
    )


def derive_paper_formal_representative_coverage(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    repeat_ids: Sequence[int] = (0,),
) -> FormalRepresentativeCoverage:
    """以既有 repeat/case selector 委托通用 typed coverage derive。"""

    plans, condition_rows, roots_by_condition = _validated_coverage_authority(
        snapshot=snapshot,
        dispatch_plans=dispatch_plans,
        catalog_manifest=catalog_manifest,
    )
    cases_by_id = _unique_catalog_cases(catalog_manifest)
    repeats = _representative_repeat_ids(repeat_ids)
    selected_condition_rows = tuple(
        item for item in condition_rows if item.condition.repeat_id in repeats
    )
    if not selected_condition_rows:
        raise ValueError("representative repeat subset selected no formal conditions")
    formal_experiment_ids = tuple(registered_paper_experiment_ids())
    selected_experiment_ids = {
        item.condition.experiment_id for item in selected_condition_rows
    }
    if selected_experiment_ids != set(formal_experiment_ids):
        raise ValueError("representative repeat subset misses a formal experiment")
    root_filter = {
        item.condition.condition_id: _representative_case_ids_for_condition(
            condition=item.condition,
            roots=roots_by_condition[item.condition.condition_id],
            cases_by_id=cases_by_id,
        )
        for item in selected_condition_rows
    }
    return derive_paper_formal_execution_coverage(
        snapshot=snapshot,
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        selected_condition_ids=tuple(
            item.condition.condition_id for item in selected_condition_rows
        ),
        root_case_filter=root_filter,
        selection_kind="filtered",
    )


def derive_paper_formal_exp4_excluded_coverage(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
    selection: str,
) -> FormalExecutionCoverage:
    """从原 Full 或 representative typed view 派生唯一的 Exp4 排除视图。"""

    if selection == "full":
        base = derive_paper_formal_full_coverage(
            snapshot=snapshot,
            dispatch_plans=dispatch_plans,
            catalog_manifest=catalog_manifest,
        )
    elif selection == "representative":
        base = derive_paper_formal_representative_coverage(
            snapshot=snapshot,
            dispatch_plans=dispatch_plans,
            catalog_manifest=catalog_manifest,
            repeat_ids=(0,),
        )
    else:
        raise ValueError("Exp4-excluded coverage selection must be full or representative")
    excluded_experiment_id = "exp4_real_ai_protocol_ablation"
    selected_conditions = tuple(
        condition
        for condition in base.conditions
        if condition.experiment_id != excluded_experiment_id
    )
    if len(selected_conditions) == len(base.conditions) or not selected_conditions:
        raise ValueError("Exp4-excluded coverage base selection is invalid")
    return derive_paper_formal_execution_coverage(
        snapshot=snapshot,
        dispatch_plans=dispatch_plans,
        catalog_manifest=catalog_manifest,
        selected_condition_ids=tuple(
            condition.condition_id for condition in selected_conditions
        ),
        root_case_filter={
            condition.condition_id: base.root_case_filter[condition.condition_id]
            for condition in selected_conditions
        },
        selection_kind=(
            "full_exp1_exp3_exp5"
            if selection == "full"
            else "representative_exp1_exp3_exp5"
        ),
    )


def _validated_coverage_authority(
    *,
    snapshot: FormalPlanSnapshot,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
) -> tuple[
    tuple[PaperExperimentDispatchPlan, ...],
    tuple[FormalConditionSnapshot, ...],
    dict[str, tuple[FormalRootSnapshot, ...]],
]:
    """验证 coverage 只能保留 canonical dispatch/snapshot/catalog 原对象。"""

    if type(snapshot) is not FormalPlanSnapshot:
        raise TypeError("snapshot must be a FormalPlanSnapshot")
    if not isinstance(catalog_manifest, PaperInputCatalogManifest):
        raise TypeError("catalog_manifest must be a PaperInputCatalogManifest")
    cases_by_id = _unique_catalog_cases(catalog_manifest)
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
    planned_rows: list[
        tuple[PaperExperimentCondition, FrozenConditionSelectionBinding]
    ] = []
    for plan in plans:
        if plan.status != "planned":
            continue
        bindings_by_id = {
            binding.condition_id: binding
            for binding in plan.condition_selection_bindings
        }
        for condition in plan.conditions:
            if any(
                existing.condition_id == condition.condition_id
                for existing, _binding in planned_rows
            ):
                raise ValueError("duplicate formal condition authority")
            binding = bindings_by_id.get(condition.condition_id)
            if binding is None:
                raise ValueError("formal condition is missing its selection binding")
            planned_rows.append((condition, binding))
    if tuple(condition.condition_id for condition, _binding in planned_rows) != tuple(
        item.condition.condition_id for item in snapshot.conditions
    ):
        raise ValueError("formal snapshot/dispatch condition coverage drift")
    for item, (condition, binding) in zip(
        snapshot.conditions,
        planned_rows,
        strict=True,
    ):
        if (
            item.condition is not condition
            or item.binding is not binding
        ):
            raise ValueError("formal snapshot must retain dispatch object references")
    planned_conditions = {
        condition.condition_id: condition for condition, _binding in planned_rows
    }
    planned_bindings = {
        condition.condition_id: binding for condition, binding in planned_rows
    }

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
        case = cases_by_id.get(root.case_id)
        if case is None or digest_json(case) != root.case_record_digest:
            raise ValueError("formal representative catalog/root digest drift")
        roots_by_condition.setdefault(condition_id, []).append(root)

    frozen_roots_by_condition: dict[str, tuple[FormalRootSnapshot, ...]] = {}
    for item in snapshot.conditions:
        condition = item.condition
        roots = tuple(roots_by_condition.get(condition.condition_id, ()))
        expected_case_ids = tuple(item.binding.selection.ordered_case_ids)
        if tuple(root.case_id for root in roots) != expected_case_ids:
            raise ValueError("formal snapshot root order/selection drift")
        frozen_roots_by_condition[condition.condition_id] = roots
    return plans, tuple(snapshot.conditions), frozen_roots_by_condition


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
    cases_by_id: Mapping[str, Mapping[str, Any]],
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
        return (
            _select_representative_worker_death_root(
                condition=condition,
                roots=roots,
                targets_by_case=targets_by_case,
                cases_by_id=cases_by_id,
            ).case_id,
        )
    for root in roots:
        if targets_by_case[root.case_id]:
            return (root.case_id,)
    raise ValueError("representative rate-fault root cannot schedule a target")


def _select_representative_worker_death_root(
    *,
    condition: PaperExperimentCondition,
    roots: Sequence[FormalRootSnapshot],
    targets_by_case: Mapping[str, Sequence[str]],
    cases_by_id: Mapping[str, Mapping[str, Any]],
) -> FormalRootSnapshot:
    """按 formal selection 顺序选最大 target-count 且不会提前剪枝的 root。"""

    candidates = tuple(roots)
    if not candidates:
        raise ValueError("representative worker-death condition has no formal roots")
    max_target_count = max(
        len(tuple(targets_by_case.get(root.case_id, ()))) for root in candidates
    )
    if max_target_count < 1:
        raise ValueError("representative worker-death root cannot schedule a target")
    candidates = tuple(
        root
        for root in candidates
        if len(tuple(targets_by_case.get(root.case_id, ()))) == max_target_count
    )
    if condition.domain != "factorization":
        return candidates[0]
    viability = tuple(
        (
            root,
            _factorization_worker_death_root_viability(
                root=root,
                target_unit_ids=tuple(targets_by_case[root.case_id]),
                case=_required_formal_case(cases_by_id, root.case_id),
            ),
        )
        for root in candidates
    )
    targeted_success = tuple(
        root for root, status in viability if status == "targeted_success"
    )
    if targeted_success:
        return targeted_success[0]
    post_anchor_success = tuple(
        root for root, status in viability if status == "post_anchor_success"
    )
    if not post_anchor_success:
        raise ValueError(
            "representative Factor worker-death condition has no structurally viable "
            "formal root"
        )
    return post_anchor_success[0]


def _factorization_worker_death_root_is_structurally_viable(
    *,
    root: FormalRootSnapshot,
    target_unit_ids: Sequence[str],
    case: Mapping[str, Any],
) -> bool:
    """只用正式 catalog、partition 与 verifier 排除非 target 的结构性剪枝。"""

    return (
        _factorization_worker_death_root_viability(
            root=root,
            target_unit_ids=target_unit_ids,
            case=case,
        )
        is not None
    )


def _factorization_worker_death_root_viability(
    *,
    root: FormalRootSnapshot,
    target_unit_ids: Sequence[str],
    case: Mapping[str, Any],
) -> str | None:
    """优先让成功 unit 本身被 kill；否则要求最早成功不早于 progress anchor。"""

    if digest_json(case) != root.case_record_digest:
        raise ValueError("formal representative catalog/root digest drift")
    if case.get("schema_version") != "tokenshare.paper_factorization_case.v1":
        raise ValueError("Factor worker-death viability requires a formal Factor case")
    split_params = case.get("split_params")
    if not isinstance(split_params, dict):
        raise ValueError("Factor worker-death viability requires split_params")
    requested = resolve_requested_child_count(split_params)
    partition = partition_candidate_ranges(
        target_n=case.get("target_n"),
        requested_child_count=requested,
        max_children_per_unit=requested,
        min_divisor=case.get("candidate_start"),
        max_divisor=case.get("candidate_end"),
    )
    planned_unit_ids = tuple(
        f"range_{range_input.child_index}" for range_input in partition.ranges
    )
    if planned_unit_ids != root.planned_ai_unit_ids:
        raise ValueError("Factor worker-death partition/planned unit drift")
    prefix = f"{root.case_id}:"
    normalized_targets = tuple(
        target[len(prefix) :] if target.startswith(prefix) else target
        for target in target_unit_ids
    )
    if not normalized_targets or any(
        target not in planned_unit_ids for target in normalized_targets
    ):
        raise ValueError("Factor worker-death target is outside formal partition")
    target_indexes = {
        planned_unit_ids.index(target) for target in normalized_targets
    }
    accepted_success_indexes = set(
        _verified_factor_success_indexes(
            case=case,
            ranges=partition.ranges,
        )
    )
    if accepted_success_indexes <= target_indexes:
        return "targeted_success"
    if accepted_success_indexes and min(accepted_success_indexes) >= min(target_indexes):
        return "post_anchor_success"
    return None


def _verified_factor_success_indexes(
    *,
    case: Mapping[str, Any],
    ranges: Sequence[Any],
) -> tuple[int, ...]:
    target = int(str(case.get("target_n")))
    raw_factors = case.get("oracle_prime_factors")
    if not isinstance(raw_factors, Sequence) or isinstance(
        raw_factors, (str, bytes, bytearray)
    ):
        raise ValueError("formal Factor oracle factors are missing")
    accepted_indexes: list[int] = []
    for raw_factor in raw_factors:
        if not isinstance(raw_factor, Mapping):
            raise ValueError("formal Factor oracle factor is invalid")
        factor = int(str(raw_factor.get("prime")))
        if factor <= 1 or factor >= target or target % factor:
            raise ValueError("formal Factor oracle factor does not divide target")
        matching = tuple(
            range_input
            for range_input in ranges
            if int(range_input.range_start)
            <= factor
            <= int(range_input.range_end)
        )
        if not matching:
            # 正式 catalog 同时冻结较小因子和较大的互补因子；后者按设计位于
            # sqrt(target) 之外，不属于插件实际调度的 candidate partition。
            continue
        if len(matching) != 1:
            raise ValueError("formal Factor oracle factor matches multiple partitions")
        range_input = matching[0]
        candidate = RangeResult(
            range_result_id=(
                f"formal-coverage-probe:{case.get('case_id')}:"
                f"{range_input.child_index}"
            ),
            result_kind=RANGE_RESULT_FOUND_FACTOR,
            target_n=range_input.target_n,
            range_start=range_input.range_start,
            range_end=range_input.range_end,
            coverage_id=range_input.coverage_id,
            child_index=range_input.child_index,
            partition_params_digest=range_input.partition_params_digest,
            found_factor=str(factor),
            cofactor=str(target // factor),
            checked_divisor_count=(factor - int(range_input.range_start) + 1),
            executor_summary={
                "executor": "deterministic_formal_coverage_probe",
                "bounded_range_only": True,
                "checked_start": int(range_input.range_start),
                "checked_end": factor,
            },
            created_at="1970-01-01T00:00:00Z",
        )
        if verify_range_result(
            candidate.to_dict(),
            child_input=range_input,
        ).status == "passed":
            accepted_indexes.append(int(range_input.child_index))
    if not accepted_indexes:
        raise ValueError("formal Factor oracle has no verifier-accepted factor")
    return tuple(sorted(set(accepted_indexes)))


def _required_formal_case(
    cases_by_id: Mapping[str, Mapping[str, Any]],
    case_id: str,
) -> Mapping[str, Any]:
    case = cases_by_id.get(case_id)
    if case is None:
        raise ValueError("representative root references an unknown formal catalog case")
    return case


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
