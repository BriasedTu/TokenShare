"""正式论文 full plan 的只读冻结与完整性验证。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_catalog_execution_view import (
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_dispatcher import PaperExperimentDispatchPlan
from tokenshare.experiments.paper_exp3_fault_recovery import (
    EXP3_EXPERIMENT_ID,
    validate_exp3_condition_matrix,
)
from tokenshare.experiments.paper_exp4_ablation_runner import (
    EXP4_EXPERIMENT_ID,
    validate_exp4_condition_matrix,
)
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenConditionSelectionBinding,
)
from tokenshare.experiments.paper_formal_runner import (
    validate_paper_formal_suite_plan,
)
from tokenshare.experiments.paper_models import (
    PaperBudgetResult,
    PaperExperimentCondition,
    digest_json,
)
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_ID as FACTORIZATION_PLUGIN_ID,
    PLUGIN_VERSION as FACTORIZATION_PLUGIN_VERSION,
)
from tokenshare.plugins.lean_proof.schemas import (
    PLUGIN_ID as LEAN_PLUGIN_ID,
    PLUGIN_VERSION as LEAN_PLUGIN_VERSION,
)


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
    _validate_complete_experiment_matrices(
        plans=plans,
        catalog_manifest=catalog_manifest,
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


def _validate_complete_experiment_matrices(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: PaperInputCatalogManifest,
) -> None:
    for plan in plans:
        if plan.experiment_id == EXP3_EXPERIMENT_ID:
            if plan.catalog_execution_view is None:
                raise ValueError("Experiment 3 formal plan requires catalog execution view")
            catalog_view = restore_catalog_execution_view(
                plan.catalog_execution_view,
                catalog_manifest=catalog_manifest,
            )
            validate_exp3_condition_matrix(
                plan.conditions,
                plan.selections,
                catalog=catalog_view,
            )
        elif plan.experiment_id == EXP4_EXPERIMENT_ID:
            validate_exp4_condition_matrix(plan.conditions, plan.selections)


def _budget_ai_unit_commitments(
    budget: PaperBudgetResult,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    preflight = budget.quota_preflight
    budget_commitments = preflight.get("budget_commitments")
    if not isinstance(budget_commitments, Mapping):
        raise ValueError("formal budget commitments are missing")
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


def _freeze_endpoint_controls(
    *,
    condition: PaperExperimentCondition,
    ai_api_configs: Mapping[str, Any],
) -> FormalEndpointControls:
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
    case_domain = (
        "factorization"
        if schema_version == "tokenshare.paper_factorization_case.v1"
        else "lean_proof"
    )
    if case_domain != condition.domain or selection.domain != condition.domain:
        raise ValueError("selection case domain mismatch")
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
