"""Paper experiment condition expansion and Exp1 pilot orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

from tokenshare.core.models import ArtifactRef
from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.experiments.lean_paper_adapter import (
    build_lean_lemma_graph_oracle_evidence,
)
from tokenshare.experiments.paper_budget import Exp1PilotProfile
from tokenshare.experiments.paper_catalog import (
    LEAN_V2_SCHEMA_VERSION,
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
    lean_case_semantic_fingerprint,
)
from tokenshare.experiments.paper_ablation import ablation_modes
from tokenshare.experiments.paper_models import (
    LEAN_PAPER_DIFFICULTIES,
    LEAN_TOPIC_FAMILIES,
    PAPER_FORMAL_AI_TIMEOUT_SECONDS,
    JsonObject,
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    PaperTaskStatus,
    digest_json,
)
from tokenshare.experiments.paper_model_identity import (
    normalize_reasoning_identity,
    validate_fixed_entry_config_identity,
)
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
)
from tokenshare.experiments.paper_dispatcher import (
    PaperExperimentDispatchPlan,
    dispatch_paper_case,
    dispatch_paper_condition,
    plan_paper_experiment,
)
from tokenshare.experiments.paper_exp1 import EXP1_FORMAL_REQUEST_CONTROLS
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelection,
    PaperExecutionContext,
    canonical_contract_digest,
)
from tokenshare.experiments.paper_factorization_sampling import (
    FACTOR_PAPER_DIFFICULTIES,
    FactorizationSamplingProfile,
)
from tokenshare.experiments.paper_suite_scale import (
    PaperSuiteScaleProfile,
    build_paper_suite_scale_policy,
)
from tokenshare.experiments.paper_catalog_execution_view import (
    EXP3_VIEW_KIND,
    EXP4_VIEW_KIND,
    build_manifest_execution_view,
    build_prepared_mapping_execution_view,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_case_ai_unit_bindings,
)
from tokenshare.runtime_paths import resolve_persisted_data_path
from tokenshare.storage.artifacts import ArtifactStore


EXPERIMENT_ALIASES = {
    "exp1": "exp1_real_ai_feasibility",
    "exp2": "exp2_real_ai_scalability",
    "exp3": "exp3_real_ai_fault_recovery",
    "exp4": "exp4_real_ai_protocol_ablation",
    "exp5": "exp5_real_ai_model_endpoint_comparison",
}
LEAN_3X3_TARGET_CASE_COUNT = 15


@dataclass(frozen=True, kw_only=True)
class _FormalPaperCatalogView:
    manifest: PaperInputCatalogManifest
    task15_budget_input: JsonObject
    lean_task14_readiness: JsonObject
    suite_version: str = "paper_v1"
    lean_semantic_readiness_passed: bool = True
    optional_worker_preflight: JsonObject | None = None
    factorization_sampling_profile: FactorizationSamplingProfile | None = None
    paper_suite_scale_profile: PaperSuiteScaleProfile | None = None
    paper_suite_scale_policy: JsonObject | None = None
    selected_factorization_by_experiment: Mapping[
        str, Mapping[str, tuple[Mapping[str, Any], ...]]
    ] | None = None

    @property
    def catalog_id(self) -> str:
        return self.manifest.catalog_id

    @property
    def catalog_version(self) -> str:
        return self.manifest.catalog_version

    @property
    def catalog_digest(self) -> str:
        return self.manifest.catalog_digest

    @property
    def factorization_cases(self) -> tuple[JsonObject, ...]:
        return self.manifest.factorization_cases

    @property
    def lean_cases(self) -> tuple[JsonObject, ...]:
        return self.manifest.lean_cases

    @property
    def lean_lemma_graph_cases(self) -> tuple[JsonObject, ...]:
        return self.manifest.lean_lemma_graph_cases

    @property
    def task14_readiness(self) -> JsonObject:
        return self.lean_task14_readiness

    def cases_for(self, **kwargs: Any) -> tuple[JsonObject, ...]:
        return self.manifest.cases_for(**kwargs)


@dataclass(frozen=True)
class Exp1PilotExecutionPlan:
    """冻结 condition、root task 和 AI-unit 顺序的可执行 Exp1 计划。"""

    suite_id: str
    budget_digest: str
    profile_digest: str
    catalog_digest: str
    conditions: tuple[PaperExperimentCondition, ...]
    tasks: tuple[JsonObject, ...]
    schema_version: str = "tokenshare.paper_exp1_pilot_execution_plan.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "budget_digest": self.budget_digest,
            "profile_digest": self.profile_digest,
            "catalog_digest": self.catalog_digest,
            "planned_conditions": len(self.conditions),
            "planned_root_runs": len(self.tasks),
            "planned_ai_units": sum(
                len(task["ai_units"]) for task in self.tasks
            ),
            "conditions": [condition.to_dict() for condition in self.conditions],
            "tasks": [_json_copy(task) for task in self.tasks],
        }


@dataclass(frozen=True, kw_only=True)
class Exp1PilotExecutionResult:
    suite_id: str
    status: str
    output_root: str
    budget_digest: str
    profile_digest: str
    catalog_digest: str
    condition_count: int
    run_count: int
    task_count: int
    blocked_run_count: int
    provider_attempt_count: int
    provider_calls_made: int
    total_tokens: int
    total_cost_estimate: float
    replayed_run_count: int
    paper_eligible: bool
    stop_reason: str | None
    hard_limits: JsonObject
    started_at: str
    ended_at: str
    pilot_only: bool = True
    schema_version: str = "tokenshare.paper_exp1_pilot_execution_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "status": self.status,
            "output_root": self.output_root,
            "budget_digest": self.budget_digest,
            "profile_digest": self.profile_digest,
            "catalog_digest": self.catalog_digest,
            "condition_count": self.condition_count,
            "run_count": self.run_count,
            "task_count": self.task_count,
            "blocked_run_count": self.blocked_run_count,
            "provider_attempt_count": self.provider_attempt_count,
            "provider_calls_made": self.provider_calls_made,
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "replayed_run_count": self.replayed_run_count,
            "paper_eligible": self.paper_eligible,
            "stop_reason": self.stop_reason,
            "hard_limits": _json_copy(self.hard_limits),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "pilot_only": self.pilot_only,
        }


@dataclass(frozen=True, kw_only=True)
class GateCPilotExecutionResult:
    """单 condition/case 的 Gate C pilot-only 执行与 replay 摘要。"""

    suite_id: str
    experiment_id: str
    condition_id: str
    case_id: str
    ai_unit_id: str | None
    status: str
    output_root: str
    budget_digest: str
    condition_digest: str
    selection_digest: str
    provider_attempt_count: int
    provider_calls_made: int
    transport_calls_observed: int
    total_tokens: int
    total_cost_estimate: float
    paper_eligible: bool
    replayed: bool
    pilot_only: bool = True
    schema_version: str = "tokenshare.paper_gate_c_pilot_execution_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "case_id": self.case_id,
            "ai_unit_id": self.ai_unit_id,
            "status": self.status,
            "output_root": self.output_root,
            "budget_digest": self.budget_digest,
            "condition_digest": self.condition_digest,
            "selection_digest": self.selection_digest,
            "provider_attempt_count": self.provider_attempt_count,
            "provider_calls_made": self.provider_calls_made,
            "transport_calls_observed": self.transport_calls_observed,
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "paper_eligible": self.paper_eligible,
            "replayed": self.replayed,
            "pilot_only": self.pilot_only,
        }


@dataclass
class _GateCCaseExecutionCallback:
    catalog_manifest: PaperInputCatalogManifest
    case_id: str
    ai_unit_id: str | None
    execution_output_root: Path
    ai_api_configs: Mapping[str, AIAPIExecutorConfig]
    transport: Any | None
    real_transport: bool
    adapter_result: Any | None = None
    transport_calls_observed: int = 0

    def __call__(self, **kwargs: Any) -> PaperConditionResult:
        context = kwargs["context"]
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        if not isinstance(context, PaperExecutionContext):
            raise ValueError("Gate C execution callback requires PaperExecutionContext")
        if not isinstance(condition, PaperExperimentCondition):
            raise ValueError("Gate C execution callback requires canonical condition")
        if self.case_id not in selection.ordered_case_ids:
            raise ValueError("selected case is not present in frozen selection")
        cases_by_id = _paper_cases_by_id(self.catalog_manifest)
        case = cases_by_id.get(self.case_id)
        if case is None:
            raise ValueError("selected case is not present in formal catalog")
        planned_ai_unit_ids = tuple(_planned_ai_unit_ids(case))
        if self.ai_unit_id is not None and self.ai_unit_id not in planned_ai_unit_ids:
            raise ValueError("selected AI unit is not present in deterministic split")
        _validate_gate_c_pilot_condition_scope(condition)
        provider_config_id = condition.provider_config_id
        if not isinstance(provider_config_id, str) or not provider_config_id:
            raise ValueError("condition provider_config_id is required for execution")
        config = self.ai_api_configs.get(provider_config_id)
        if config is None:
            raise ValueError("approved provider config is unavailable for condition")
        _validate_gate_c_execution_config(condition=condition, config=config)
        request_limits = dict(context.request_limits)
        calls_before = _transport_call_count(self.transport)
        adapter_condition = _condition_for_selected_case(
            condition=condition,
            case=case,
        )
        self.adapter_result = dispatch_paper_case(
            case=case,
            condition=adapter_condition,
            output_root=(
                self.execution_output_root / "runs" / condition.condition_id
            ).as_posix(),
            transport=self.transport,
            real_transport=self.real_transport,
            ai_api_config=config,
            entry_id=condition.model_entry_id,
            max_tokens=int(request_limits["max_tokens"]),
            timeout_seconds=int(request_limits["timeout_seconds"]),
            selected_ai_unit_id=self.ai_unit_id,
        )
        calls_after = _transport_call_count(self.transport)
        self.transport_calls_observed = max(0, calls_after - calls_before)
        task = self.adapter_result.task_result
        completed = int(task.root_status == PaperTaskStatus.COMPLETED)
        failed = int(task.root_status == PaperTaskStatus.FAILED)
        blocked = int(task.root_status == PaperTaskStatus.BLOCKED)
        status = (
            PaperStatus.COMPLETED
            if completed
            else PaperStatus.BLOCKED
            if blocked
            else PaperStatus.COMPLETED_WITH_FAILURES
        )
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=status,
            repeat_count=1,
            task_count=1,
            completed_root_count=completed,
            failed_root_count=failed,
            blocked_root_count=blocked,
            provider_attempt_count=int(task.provider_attempt_count),
            metrics_ref={
                "transport_kind": self.adapter_result.run_evidence[
                    "transport_evidence"
                ]["transport_kind"],
                "paper_eligible": False,
                "pilot_only": True,
            },
        )


def build_exp1_pilot_execution_plan(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    pilot_profile: Exp1PilotProfile,
    budget: PaperBudgetResult,
) -> Exp1PilotExecutionPlan:
    """从已冻结 profile/budget 展开稳定的 root 与 AI-unit 计划。"""

    catalog_slice = tuple(pilot_profile.body.get("catalog_slice", ()))
    if not catalog_slice:
        raise ValueError("Exp1 pilot execution request expands to zero conditions")
    pilot_summary = _approved_exp1_pilot_summary(budget)
    case_profiles = tuple(pilot_summary.get("case_request_profiles", ()))
    if len(case_profiles) != len(catalog_slice):
        raise ValueError("Exp1 pilot budget case profile count does not match profile")
    if pilot_summary.get("profile_digest") != pilot_profile.profile_digest:
        raise ValueError("Exp1 pilot budget profile digest does not match profile")
    if pilot_summary.get("catalog_digest") != catalog_manifest.catalog_digest:
        raise ValueError("Exp1 pilot budget catalog digest does not match catalog")

    cases_by_id = _paper_cases_by_id(catalog_manifest)
    endpoint_identity = pilot_profile.model_endpoint_identity
    conditions: list[PaperExperimentCondition] = []
    tasks: list[JsonObject] = []
    repeat_count = int(pilot_profile.body["repeat_count"])
    seeds = tuple(int(seed) for seed in pilot_profile.body["seed_family"])
    worker_count = int(pilot_profile.body["worker_count"])
    for repeat_id in range(repeat_count):
        seed = seeds[repeat_id % len(seeds)]
        for selection, case_profile in zip(catalog_slice, case_profiles):
            case_id = str(selection["case_id"])
            if case_id != str(case_profile.get("case_id")):
                raise ValueError("Exp1 pilot budget case order does not match profile")
            case = cases_by_id.get(case_id)
            if case is None:
                raise ValueError(f"Exp1 pilot catalog case not found: {case_id}")
            ordinal = int(case_profile["ordinal"])
            condition_id = f"exp1_pilot_c{ordinal:02d}_{case_id}_r{repeat_id}"
            run_id = f"{condition_id}_{case_id}"
            condition = PaperExperimentCondition(
                experiment_id="exp1_real_ai_feasibility",
                condition_id=condition_id,
                domain=str(case_profile["domain"]),
                difficulty=str(case["difficulty"]),
                paper_difficulty=str(case["paper_difficulty"]),
                topic_family=case.get("topic_family"),
                topic_family_version=case.get("topic_family_version"),
                construction_rule_id=case.get("construction_rule_id"),
                oracle_package_group=case.get("oracle_package_group"),
                proof_assembly_shape=case.get("proof_assembly_shape"),
                worker_count=worker_count,
                fault_type="none",
                fault_rate=0.0,
                ablation_mode="FULL",
                model_policy="fixed_entry",
                model_cohort_id=endpoint_identity.model_cohort_id,
                cohort_member_id=endpoint_identity.cohort_member_id,
                provider_config_id=endpoint_identity.provider_config_id,
                model_entry_id=endpoint_identity.selected_entry_id,
                provider_family=endpoint_identity.provider_family,
                provider_model_id=endpoint_identity.provider_model_id,
                reasoning_profile_id=endpoint_identity.reasoning_profile_id,
                model_cohort_digest=endpoint_identity.model_cohort_digest,
                source_provider_config_digest=(
                    endpoint_identity.source_provider_config_digest
                ),
                model_endpoint_identity_digest=(
                    endpoint_identity.model_endpoint_identity_digest
                ),
                repeat_id=repeat_id,
                seed=seed,
                catalog_digest=catalog_manifest.catalog_digest,
            )
            execution_status = str(case_profile["execution_status"])
            ai_units = (
                []
                if execution_status == "structured_blocked"
                else list(case_profile["split_profile"]["ai_unit_order"])
            )
            if len(ai_units) != int(case_profile["ai_unit_count"]):
                raise ValueError(
                    f"Exp1 pilot AI-unit order does not match budget for {case_id}"
                )
            budget_bindings = list(case_profile.get("ai_unit_bindings", []))
            ai_unit_bindings = (
                []
                if execution_status == "structured_blocked"
                else build_case_ai_unit_bindings(
                    case,
                    seed=seed,
                    include_request_artifacts=True,
                    executor_requirements=dict(
                        case_profile["executor_requirements"]
                    ),
                )
            )
            if [item["planned_ai_unit_id"] for item in ai_unit_bindings] != ai_units:
                raise ValueError(
                    f"Exp1 pilot AI-unit bindings do not match split order for {case_id}"
                )
            if [
                item.get("core_binding_digest") for item in ai_unit_bindings
            ] != [item.get("core_binding_digest") for item in budget_bindings]:
                raise ValueError(
                    f"Exp1 pilot AI-unit core bindings do not match budget for {case_id}"
                )
            conditions.append(condition)
            tasks.append(
                {
                    "schema_version": "tokenshare.paper_exp1_pilot_task_plan.v1",
                    "ordinal": len(tasks),
                    "catalog_ordinal": ordinal,
                    "condition_id": condition_id,
                    "condition_digest": condition.condition_digest,
                    "repeat_id": repeat_id,
                    "seed": seed,
                    "run_id": run_id,
                    "task_id": _adapter_task_id(
                        domain=str(case_profile["domain"]),
                        case_id=case_id,
                    ),
                    "case_id": case_id,
                    "domain": case_profile["domain"],
                    "difficulty": case["difficulty"],
                    "paper_difficulty": case["paper_difficulty"],
                    "topic_family": case.get("topic_family"),
                    "execution_status": execution_status,
                    "blocked_reason": case_profile.get("blocked_reason"),
                    "ai_units": ai_units,
                    "ai_unit_bindings": ai_unit_bindings,
                    "provider_attempt_upper_bound": int(
                        case_profile["provider_attempt_upper_bound"]
                    ),
                    "token_upper_bound": int(case_profile["token_upper_bound"]),
                    "cost_upper_bound": float(case_profile["cost_upper_bound"]),
                    "request_limits": _json_copy(case_profile["request_limits"]),
                    "split_profile": _json_copy(case_profile["split_profile"]),
                }
            )
    if not conditions:
        raise ValueError("Exp1 pilot execution request expands to zero conditions")
    if len(tasks) != budget.planned_root_runs:
        raise ValueError("Exp1 pilot execution root count does not match budget")
    if sum(len(task["ai_units"]) for task in tasks) != budget.planned_ai_units:
        raise ValueError("Exp1 pilot execution AI-unit count does not match budget")
    return Exp1PilotExecutionPlan(
        suite_id=str(pilot_profile.body["suite_id"]),
        budget_digest=budget.budget_digest,
        profile_digest=pilot_profile.profile_digest,
        catalog_digest=catalog_manifest.catalog_digest,
        conditions=tuple(conditions),
        tasks=tuple(tasks),
    )


def execute_exp1_pilot(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    pilot_profile: Exp1PilotProfile,
    budget: PaperBudgetResult,
    approved_budget_digest: str,
    baseline_entry_id: str,
    output_base: str | Path,
    real_transport: bool,
    execution_output_root: str | Path | None = None,
    provider_attempt_limit: int | None = None,
    token_limit: int | None = None,
    cost_limit: float | None = None,
    resume: bool = False,
    replay_only: bool = False,
    case_id: str | None = None,
    ai_unit_id: str | None = None,
    transport: Any | None = None,
    factorization_adapter: Callable[..., Any] | None = None,
    lean_adapter: Callable[..., Any] | None = None,
) -> Exp1PilotExecutionResult:
    """执行已批准的 Exp1 pilot；resume/replay 不重做已有 root。"""

    uses_default_adapter = factorization_adapter is None or lean_adapter is None
    _validate_exp1_execution_gate(
        pilot_profile=pilot_profile,
        budget=budget,
        approved_budget_digest=approved_budget_digest,
        baseline_entry_id=baseline_entry_id,
        real_transport=real_transport,
    )
    if (case_id is None) != (ai_unit_id is None):
        raise ValueError("case_id and ai_unit_id must be provided together")
    if case_id is not None and execution_output_root is None:
        raise ValueError(
            "case selector requires an independent execution_output_root for "
            "whole-root execution; ai_unit_id only validates the frozen split plan"
        )
    if case_id is not None and execution_output_root is not None:
        validate_exp1_selector_output_root(
            output_base=output_base,
            suite_id=str(pilot_profile.body["suite_id"]),
            execution_output_root=execution_output_root,
        )
    plan = build_exp1_pilot_execution_plan(
        catalog_manifest=catalog_manifest,
        pilot_profile=pilot_profile,
        budget=budget,
    )
    if case_id is not None and ai_unit_id is not None:
        plan = _select_exp1_pilot_root_plan(
            plan=plan,
            case_id=case_id,
            ai_unit_id=ai_unit_id,
        )
    secret_values: tuple[str, ...] = ()
    if uses_default_adapter and not (resume or replay_only):
        secret_values = (
            _resolve_selected_api_key(
                pilot_profile=pilot_profile,
                baseline_entry_id=baseline_entry_id,
            ),
        )
    hard_limits = _execution_hard_limits(
        budget=budget,
        provider_attempt_limit=provider_attempt_limit,
        token_limit=token_limit,
        cost_limit=cost_limit,
    )
    suite_root = (
        Path(execution_output_root)
        if execution_output_root is not None
        else Path(output_base) / plan.suite_id
    )
    plan_body = plan.to_dict()
    plan_body.update(
        {
            "provider_calls_made_before_execution": 0,
            "baseline_entry_id": baseline_entry_id,
            "source_provider_config_digest": (
                pilot_profile.source_provider_config.config_digest
            ),
            "model_endpoint_identity_digest": (
                pilot_profile.model_endpoint_identity.model_endpoint_identity_digest
            ),
            "hard_limits": _json_copy(hard_limits),
            "stop_policy": "stop_after_current_task",
            "pilot_only": True,
            "selected_case_id": case_id,
            "requested_ai_unit_id": ai_unit_id,
            "execution_scope": (
                "selected_ai_unit_protocol"
                if ai_unit_id is not None
                else (
                    "whole_root_protocol"
                    if case_id is not None
                    else "approved_pilot_matrix"
                )
            ),
        }
    )
    plan_body["execution_plan_digest"] = digest_json(plan_body)

    evidence = _load_or_initialize_execution_evidence(
        suite_root=suite_root,
        plan_body=plan_body,
        resume=resume,
        replay_only=replay_only,
    )
    existing_run_ids = {str(item["run_id"]) for item in evidence["runs"]}
    replayed_run_count = len(existing_run_ids)
    if resume or replay_only:
        expected_run_ids = {str(task["run_id"]) for task in plan.tasks}
        if existing_run_ids != expected_run_ids:
            raise ValueError(
                "resume/replay requires complete existing Exp1 pilot evidence"
            )
        expected_condition_ids = {
            str(condition.condition_id) for condition in plan.conditions
        }
        existing_condition_ids = {
            str(item.get("condition_id")) for item in evidence["tasks"]
        }
        if existing_condition_ids != expected_condition_ids:
            raise ValueError(
                "resume/replay requires complete existing Exp1 pilot task evidence"
            )
        result = _execution_result_from_evidence(
            plan=plan,
            suite_root=suite_root,
            hard_limits=hard_limits,
            evidence=evidence,
            provider_calls_made=0,
            replayed_run_count=replayed_run_count,
            stop_reason=_existing_stop_reason(evidence["events"]),
        )
        if replay_only:
            return result
        return _finalize_exp1_pilot_result(
            result=result,
            suite_root=suite_root,
            secret_values=(),
            write_report=uses_default_adapter or result.paper_eligible,
        )

    cases_by_id = _paper_cases_by_id(catalog_manifest)
    provider_calls_made = 0
    stop_reason: str | None = None
    started_at = _utc_now()
    if not evidence["events"]:
        evidence["events"].append(
            _orchestrator_event(
                event_id="suite_started",
                event_type="suite_started",
                suite_id=plan.suite_id,
            )
        )
        _checkpoint_execution_evidence(
            suite_root=suite_root,
            evidence=evidence,
            plan=plan,
        )
    for condition, task in zip(plan.conditions, plan.tasks):
        run_id = str(task["run_id"])
        if run_id in existing_run_ids:
            continue
        if task["execution_status"] == "structured_blocked":
            task_result = _blocked_task_result(
                condition=condition,
                task=task,
                root_status="blocked",
                failure_kind=None,
            )
            run_result = _run_result_from_task(
                condition=condition,
                task=task,
                task_result=task_result,
                suite_root=suite_root,
                status="blocked",
                ineligibility_reasons=[str(task["blocked_reason"])],
                transport_evidence={
                    "real_transport": False,
                    "transport_kind": "not_called_structured_blocked",
                },
            )
            evidence["tasks"].append(task_result)
            evidence["runs"].append(run_result)
            evidence["events"].append(
                _orchestrator_event(
                    event_id=f"{run_id}:blocked",
                    event_type="task_blocked",
                    suite_id=plan.suite_id,
                    task=task,
                )
            )
            _checkpoint_execution_evidence(
                suite_root=suite_root,
                evidence=evidence,
                plan=plan,
            )
            continue
        limit_reason = _next_task_limit_reason(
            evidence=evidence,
            task=task,
            hard_limits=hard_limits,
        )
        if stop_reason is None and limit_reason is not None:
            stop_reason = limit_reason
        if stop_reason is not None:
            task_result = _blocked_task_result(
                condition=condition,
                task=task,
                root_status="budget_exhausted",
                failure_kind="budget_limit",
            )
            run_result = _run_result_from_task(
                condition=condition,
                task=task,
                task_result=task_result,
                suite_root=suite_root,
                status="budget_exhausted",
                ineligibility_reasons=[stop_reason],
                transport_evidence={
                    "real_transport": False,
                    "transport_kind": "not_called_budget_exhausted",
                },
            )
            evidence["tasks"].append(task_result)
            evidence["runs"].append(run_result)
            evidence["events"].append(
                _orchestrator_event(
                    event_id=f"{run_id}:budget_exhausted",
                    event_type="task_budget_exhausted",
                    suite_id=plan.suite_id,
                    task=task,
                    detail={"stop_reason": stop_reason},
                )
            )
            _checkpoint_execution_evidence(
                suite_root=suite_root,
                evidence=evidence,
                plan=plan,
            )
            continue

        evidence["events"].append(
            _orchestrator_event(
                event_id=f"{run_id}:started",
                event_type="task_started",
                suite_id=plan.suite_id,
                task=task,
            )
        )
        _checkpoint_execution_evidence(
            suite_root=suite_root,
            evidence=evidence,
            plan=plan,
        )
        request_limits = dict(task["request_limits"])
        adapter = (
            factorization_adapter
            if task["domain"] == "factorization"
            else lean_adapter
        )
        adapter_kwargs = {
            "case": cases_by_id[str(task["case_id"])],
            "condition": condition,
            "output_root": suite_root / "runs" / run_id,
            "real_transport": real_transport,
            "ai_api_config": pilot_profile.source_provider_config,
            "entry_id": baseline_entry_id,
            "max_tokens": int(request_limits["max_tokens"]),
            "timeout_seconds": int(request_limits["timeout_seconds"]),
            "selected_ai_unit_id": task.get("requested_ai_unit_id"),
        }
        if adapter is None:
            adapter_result = dispatch_paper_case(
                **adapter_kwargs,
                transport=transport,
            )
        else:
            adapter_result = adapter(**adapter_kwargs)
        result_body = adapter_result.to_dict()
        task_result = _json_copy(result_body["task_result"])
        attempts = [_json_copy(item) for item in result_body["attempt_results"]]
        runner_eligible, runner_ineligibility_reasons = (
            _runner_verified_adapter_eligibility(result_body)
        )
        task_result["paper_eligible"] = runner_eligible
        provider_calls_made += int(task_result["provider_attempt_count"])
        evidence["tasks"].append(task_result)
        evidence["attempts"].extend(attempts)
        evidence["artifacts"].extend(
            _artifact_index_records(
                run_id=run_id,
                task_result=task_result,
                attempts=attempts,
            )
        )
        run_status = _run_status_from_root_status(str(task_result["root_status"]))
        ineligibility_reasons = list(
            result_body.get("eligibility_report", {}).get(
                "ineligibility_reasons", ()
            )
        )
        ineligibility_reasons.extend(runner_ineligibility_reasons)
        evidence["runs"].append(
            _run_result_from_task(
                condition=condition,
                task=task,
                task_result=task_result,
                suite_root=suite_root,
                status=run_status,
                ineligibility_reasons=ineligibility_reasons,
                adapter_output_root=str(result_body.get("output_root", "")),
                transport_evidence=(
                    result_body.get("run_evidence", {}).get("transport_evidence")
                    if isinstance(result_body.get("run_evidence"), dict)
                    else None
                ),
            )
        )
        evidence["events"].append(
            _orchestrator_event(
                event_id=f"{run_id}:completed",
                event_type="task_completed",
                suite_id=plan.suite_id,
                task=task,
                detail={
                    "root_status": task_result["root_status"],
                    "provider_attempt_count": task_result[
                        "provider_attempt_count"
                    ],
                    "total_tokens": task_result["total_tokens"],
                    "cost_estimate": task_result["cost_estimate"],
                },
            )
        )
        observed_profile_violation = _observed_profile_violation(
            task=task,
            task_result=task_result,
        )
        if observed_profile_violation is not None:
            stop_reason = observed_profile_violation
        _checkpoint_execution_evidence(
            suite_root=suite_root,
            evidence=evidence,
            plan=plan,
        )

    evidence["events"].append(
        _orchestrator_event(
            event_id=f"suite_finished:{len(evidence['events'])}",
            event_type="suite_finished",
            suite_id=plan.suite_id,
            detail={"stop_reason": stop_reason},
        )
    )
    _checkpoint_execution_evidence(
        suite_root=suite_root,
        evidence=evidence,
        plan=plan,
    )
    result = _execution_result_from_evidence(
        plan=plan,
        suite_root=suite_root,
        hard_limits=hard_limits,
        evidence=evidence,
        provider_calls_made=provider_calls_made,
        replayed_run_count=replayed_run_count,
        stop_reason=stop_reason,
        started_at=started_at,
    )
    return _finalize_exp1_pilot_result(
        result=result,
        suite_root=suite_root,
        secret_values=secret_values,
        write_report=uses_default_adapter or result.paper_eligible,
    )


def _select_exp1_pilot_root_plan(
    *,
    plan: Exp1PilotExecutionPlan,
    case_id: str,
    ai_unit_id: str,
) -> Exp1PilotExecutionPlan:
    matches = [
        (condition, task)
        for condition, task in zip(plan.conditions, plan.tasks, strict=True)
        if task.get("case_id") == case_id
    ]
    if not matches:
        raise ValueError("case is not present in approved pilot plan")
    if len(matches) != 1:
        raise ValueError("case selector must identify exactly one approved pilot run")
    condition, task = matches[0]
    ai_units = tuple(str(value) for value in task.get("ai_units", ()))
    if ai_unit_id not in ai_units:
        raise ValueError("AI unit is not present in approved pilot plan")
    selected_bindings = [
        binding
        for binding in task.get("ai_unit_bindings", ())
        if binding.get("planned_ai_unit_id") == ai_unit_id
    ]
    if len(selected_bindings) != 1:
        raise ValueError("approved pilot AI-unit binding is missing or ambiguous")
    selected_task = _json_copy(task)
    selected_task["pilot_case_selector"] = case_id
    selected_task["requested_ai_unit_id"] = ai_unit_id
    selected_task["selector_execution_scope"] = "selected_ai_unit_protocol"
    return Exp1PilotExecutionPlan(
        suite_id=plan.suite_id,
        budget_digest=plan.budget_digest,
        profile_digest=plan.profile_digest,
        catalog_digest=plan.catalog_digest,
        conditions=(condition,),
        tasks=(selected_task,),
    )


def validate_exp1_selector_output_root(
    *,
    output_base: str | Path,
    suite_id: str,
    execution_output_root: str | Path,
) -> None:
    """拒绝与完整 pilot canonical tree 重叠的单 case 整 root 输出目录。"""

    canonical_root = (Path(output_base) / suite_id).resolve(strict=False)
    selector_root = Path(execution_output_root).resolve(strict=False)
    if (
        selector_root == canonical_root
        or selector_root in canonical_root.parents
        or canonical_root in selector_root.parents
    ):
        raise ValueError(
            "whole-root case selector output root must not overlap canonical "
            "Exp1 pilot root"
        )


def validate_gate_c_pilot_output_root(
    *,
    output_base: str | Path,
    experiment_id: str,
    execution_output_root: str | Path,
) -> None:
    plan_root = Path(output_base).resolve(strict=False)
    experiment_root = (Path(output_base) / experiment_id).resolve(strict=False)
    pilot_root = Path(execution_output_root).resolve(strict=False)
    if pilot_root == plan_root:
        raise ValueError("Gate C pilot output root must differ from plan output root")
    if (
        pilot_root == experiment_root
        or pilot_root in experiment_root.parents
        or experiment_root in pilot_root.parents
    ):
        raise ValueError(
            "Gate C pilot output root must not overlap canonical experiment root"
        )


def execute_gate_c_pilot_case(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    lean_3x3_matrix: JsonObject,
    experiment_id: str,
    baseline_endpoint_binding: JsonObject,
    model_endpoint_cohort_preflight: JsonObject | None,
    budget: PaperBudgetResult,
    approved_budget_digest: str,
    condition_id: str,
    case_id: str,
    ai_unit_id: str | None,
    execution_output_root: str | Path,
    ai_api_configs: Mapping[str, AIAPIExecutorConfig],
    transport: Any | None,
    real_transport: bool,
    factorization_sampling_profile: FactorizationSamplingProfile | None = None,
    paper_suite_scale_profile: PaperSuiteScaleProfile | None = None,
    resume: bool = False,
    replay_only: bool = False,
) -> GateCPilotExecutionResult:
    """经注册模块执行一个批准的完整 root；AI unit 参数只校验冻结计划。"""

    if not real_transport:
        raise ValueError("Gate C pilot execution requires real_transport=True")
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if approved_budget_digest != budget.budget_digest:
        raise ValueError("budget digest mismatch")
    if experiment_id not in budget.planned_experiments:
        raise ValueError("approved budget does not include selected experiment")
    output_root = Path(execution_output_root)
    callback = _GateCCaseExecutionCallback(
        catalog_manifest=catalog_manifest,
        case_id=case_id,
        ai_unit_id=ai_unit_id,
        execution_output_root=output_root,
        ai_api_configs=ai_api_configs,
        transport=transport,
        real_transport=real_transport,
    )
    context = _gate_c_context(
        catalog_manifest=catalog_manifest,
        lean_3x3_matrix=lean_3x3_matrix,
        experiment_id=experiment_id,
        baseline_endpoint_binding=baseline_endpoint_binding,
        model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
        output_root=output_root,
        execution_callback=callback,
        hard_limits={
            "max_total_provider_attempts": budget.max_provider_attempts,
            "max_total_tokens": budget.token_upper_bound,
            "max_cost_estimate": budget.cost_upper_bound,
        },
        factorization_sampling_profile=factorization_sampling_profile,
        paper_suite_scale_profile=paper_suite_scale_profile,
    )
    plan = plan_paper_experiment(context=context, experiment_id=experiment_id)
    try:
        condition, selection = plan.bound_condition(condition_id)
    except ValueError as exc:
        raise ValueError(
            "condition is not present exactly once in canonical plan"
        ) from exc
    if selection.is_blocked:
        raise ValueError("selected Gate C pilot condition is structured blocked")
    if case_id not in selection.ordered_case_ids:
        raise ValueError("selected case is not present in frozen selection")
    _validate_gate_c_budget_commitment(
        budget=budget,
        condition=condition,
        selection=selection,
        case_id=case_id,
        ai_unit_id=ai_unit_id,
        request_limits=context.request_limits,
    )
    suite_id = (
        f"gate_c_pilot_{experiment_id}_{condition_id}_{case_id}"
        + (f"_{ai_unit_id}" if ai_unit_id is not None else "")
    )
    plan_body: JsonObject = {
        "schema_version": "tokenshare.paper_gate_c_pilot_execution_plan.v1",
        "suite_id": suite_id,
        "experiment_id": experiment_id,
        "budget_digest": budget.budget_digest,
        "catalog_digest": catalog_manifest.catalog_digest,
        "condition_id": condition.condition_id,
        "condition_digest": condition.condition_digest,
        "selection_id": selection.selection_id,
        "selection_digest": selection.selection_digest,
        "ordered_case_ids": list(selection.ordered_case_ids),
        "selected_case_id": case_id,
        "requested_ai_unit_id": ai_unit_id,
        "execution_scope": (
            "selected_ai_unit_protocol"
            if ai_unit_id is not None
            else "whole_root_protocol"
        ),
        "request_limits": dict(context.request_limits),
        "hard_limits": dict(context.hard_limits),
        "output_root": output_root.as_posix(),
        "pilot_only": True,
        "provider_calls_made_before_execution": 0,
    }
    plan_body["execution_plan_digest"] = digest_json(plan_body)
    if resume or replay_only:
        return _replay_gate_c_pilot(
            output_root=output_root,
            expected_plan=plan_body,
        )
    if (output_root / "execution_plan.json").exists():
        raise ValueError("Gate C pilot output root already contains execution evidence")

    dispatch_result = dispatch_paper_condition(
        context=context,
        plan=plan,
        condition_id=condition.condition_id,
    )
    if callback.adapter_result is None:
        raise ValueError("registered module did not invoke Gate C execution callback")
    if dispatch_result.condition_id != condition.condition_id:
        raise ValueError("registered module returned a different condition")
    result = _gate_c_result_from_adapter(
        suite_id=suite_id,
        condition=condition,
        selection_digest=selection.selection_digest,
        case_id=case_id,
        ai_unit_id=ai_unit_id,
        output_root=output_root,
        budget=budget,
        adapter_result=callback.adapter_result,
        transport_calls_observed=callback.transport_calls_observed,
        replayed=False,
    )
    _persist_gate_c_pilot_evidence(
        output_root=output_root,
        plan_body=plan_body,
        catalog_manifest=catalog_manifest,
        condition=condition,
        selection=selection,
        budget=budget,
        result=result,
        adapter_result=callback.adapter_result,
    )
    return result


def build_gate_c_dispatch_plans(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    lean_3x3_matrix: JsonObject,
    experiment_ids: tuple[str, ...],
    baseline_endpoint_binding: JsonObject,
    model_endpoint_cohort_preflight: JsonObject | None,
    output_root: str | Path,
    factorization_sampling_profile: FactorizationSamplingProfile | None = None,
    paper_suite_scale_profile: PaperSuiteScaleProfile | None = None,
) -> tuple[PaperExperimentDispatchPlan, ...]:
    """通过各模块的 Gate B Protocol 实现生成共享调度计划。"""

    base_output = Path(output_root)
    plans: list[PaperExperimentDispatchPlan] = []
    for experiment_id in experiment_ids:
        context = _gate_c_context(
            catalog_manifest=catalog_manifest,
            lean_3x3_matrix=lean_3x3_matrix,
            experiment_id=experiment_id,
            baseline_endpoint_binding=baseline_endpoint_binding,
            model_endpoint_cohort_preflight=model_endpoint_cohort_preflight,
            output_root=base_output / experiment_id,
            execution_callback=_forbidden_plan_execution,
            hard_limits={"max_total_provider_attempts": 0},
            factorization_sampling_profile=factorization_sampling_profile,
            paper_suite_scale_profile=paper_suite_scale_profile,
        )
        plans.append(
            plan_paper_experiment(
                context=context,
                experiment_id=experiment_id,
            )
        )
    return tuple(plans)


def _gate_c_context(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    lean_3x3_matrix: JsonObject,
    experiment_id: str,
    baseline_endpoint_binding: JsonObject,
    model_endpoint_cohort_preflight: JsonObject | None,
    output_root: str | Path,
    execution_callback: Callable[..., PaperConditionResult],
    hard_limits: Mapping[str, Any],
    factorization_sampling_profile: FactorizationSamplingProfile | None,
    paper_suite_scale_profile: PaperSuiteScaleProfile | None,
) -> PaperExecutionContext:
    bound_lean_matrix = _bind_lean_matrix_to_catalog(
        lean_3x3_matrix,
        catalog_manifest=catalog_manifest,
    )
    task15_budget_input = bound_lean_matrix.get("task15_budget_input")
    if not isinstance(task15_budget_input, dict):
        raise ValueError("Gate C planning requires Task14 task15_budget_input")
    suite_policy: JsonObject | None = None
    selected_factorization: dict[
        str, dict[str, tuple[Mapping[str, Any], ...]]
    ] | None = None
    if catalog_manifest.catalog_version == "v2":
        if paper_suite_scale_profile is None:
            raise ValueError(
                "catalog v2 planning requires an explicit paper suite scale profile"
            )
        candidates_by_difficulty = {
            difficulty: catalog_manifest.cases_for(
                domain="factorization",
                difficulty=difficulty,
                paper_difficulty=difficulty,
            )
            for difficulty in FACTOR_PAPER_DIFFICULTIES
        }
        suite_policy, selected_factorization = build_paper_suite_scale_policy(
            profile=paper_suite_scale_profile,
            catalog_id=catalog_manifest.catalog_id,
            catalog_version=catalog_manifest.catalog_version,
            catalog_digest=catalog_manifest.catalog_digest,
            candidates_by_difficulty=candidates_by_difficulty,
        )
    formal_catalog = _FormalPaperCatalogView(
        manifest=catalog_manifest,
        task15_budget_input=_json_copy(task15_budget_input),
        lean_task14_readiness=_json_copy(bound_lean_matrix),
        optional_worker_preflight={},
        factorization_sampling_profile=factorization_sampling_profile,
        paper_suite_scale_profile=paper_suite_scale_profile,
        paper_suite_scale_policy=suite_policy,
        selected_factorization_by_experiment=selected_factorization,
    )
    selected_case_ids = None
    if selected_factorization is not None and experiment_id in {
        "exp1_real_ai_feasibility",
        "exp2_real_ai_scalability",
    }:
        selected_case_ids = tuple(
            str(case["case_id"])
            for difficulty in FACTOR_PAPER_DIFFICULTIES
            for case in selected_factorization[experiment_id].get(difficulty, ())
        )
    manifest_view = build_manifest_execution_view(
        manifest=catalog_manifest,
        task15_budget_input=task15_budget_input,
        lean_task14_readiness=bound_lean_matrix,
        optional_worker_preflight={},
        factorization_case_ids=selected_case_ids,
        paper_suite_scale_policy=suite_policy,
    )
    if experiment_id == "exp3_real_ai_fault_recovery":
        catalog: Any = build_prepared_mapping_execution_view(
            view_kind=EXP3_VIEW_KIND,
            catalog_manifest_digest=catalog_manifest.catalog_digest,
            view_body=_exp3_catalog_view(formal_catalog),
        )
    elif experiment_id == "exp4_real_ai_protocol_ablation":
        catalog = build_prepared_mapping_execution_view(
            view_kind=EXP4_VIEW_KIND,
            catalog_manifest_digest=catalog_manifest.catalog_digest,
            view_body=_exp4_catalog_view(formal_catalog),
        )
    else:
        catalog = manifest_view
    endpoint_binding = (
        model_endpoint_cohort_preflight
        if experiment_id == "exp5_real_ai_model_endpoint_comparison"
        else baseline_endpoint_binding
    )
    if endpoint_binding is None:
        endpoint_binding = {"status": "blocked", "provider_calls_made": 0}
    return PaperExecutionContext(
        context_id=f"gate_c_{experiment_id}",
        catalog=catalog,
        approved_endpoint_binding=endpoint_binding,
        request_limits=dict(EXP1_FORMAL_REQUEST_CONTROLS),
        hard_limits=dict(hard_limits),
        output_root=Path(output_root).as_posix(),
        artifact_store=object(),
        event_store=object(),
        execution_callback=execution_callback,
    )


def _bind_lean_matrix_to_catalog(
    lean_3x3_matrix: Mapping[str, Any],
    *,
    catalog_manifest: PaperInputCatalogManifest,
) -> JsonObject:
    bound = _json_copy(lean_3x3_matrix)
    task15 = bound.get("task15_budget_input")
    if not isinstance(task15, dict):
        raise ValueError("Gate C planning requires Task14 task15_budget_input")
    source_matrix_digest = digest_json(_lean_matrix_digest_body(bound))
    if (
        bound.get("matrix_digest") != source_matrix_digest
        or task15.get("matrix_digest") != source_matrix_digest
    ):
        raise ValueError("Lean readiness matrix digest drift")
    if (
        bound.get("catalog_digest") == catalog_manifest.catalog_digest
        and task15.get("catalog_digest") == catalog_manifest.catalog_digest
    ):
        return bound
    if catalog_manifest.catalog_version != "v2":
        raise ValueError("Lean readiness catalog digest drift")

    task15["catalog_digest"] = catalog_manifest.catalog_digest
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_manifest.catalog_digest,
            "environment_digest": task15.get("environment_digest"),
            "oracle_package_digests": task15.get("oracle_package_digests"),
            "target_case_count": task15.get("target_case_count"),
            "selected_case_ids_by_cell": task15.get("selected_case_ids_by_cell"),
            "semantic_fingerprint_digests_by_cell": task15.get(
                "semantic_fingerprint_digests_by_cell"
            ),
            "golden_case_ids_by_cell": task15.get("golden_case_ids_by_cell"),
        }
    )
    task15["selection_digest"] = selection_digest
    task15["catalog_slice_digest"] = selection_digest
    bound["catalog_digest"] = catalog_manifest.catalog_digest
    bound["catalog_version"] = catalog_manifest.catalog_version
    matrix_digest = digest_json(_lean_matrix_digest_body(bound))
    bound["matrix_digest"] = matrix_digest
    task15["matrix_digest"] = matrix_digest
    return bound


def _validate_gate_c_execution_config(
    *,
    condition: PaperExperimentCondition,
    config: AIAPIExecutorConfig,
) -> None:
    if condition.source_provider_config_digest != config.config_digest:
        raise ValueError("source provider config digest drift before transport")
    if condition.provider_family != config.provider_family:
        raise ValueError("provider family drift before transport")
    matching_entries = [
        entry
        for entry in config.entries
        if entry.enabled and entry.entry_id == condition.model_entry_id
    ]
    if len(matching_entries) != 1:
        raise ValueError("fixed model entry is unavailable before transport")
    entry = matching_entries[0]
    if entry.model != condition.provider_model_id:
        raise ValueError("provider model drift before transport")
    if not entry.supports_json_mode:
        raise ValueError("fixed model entry must support JSON mode")
    reasoning = normalize_reasoning_identity(
        provider_family=config.provider_family,
        request_overrides=entry.request_overrides,
    )
    if reasoning.reasoning_profile_id != condition.reasoning_profile_id:
        raise ValueError("reasoning profile drift before transport")
    if config.provider_family == "deepseek":
        if "temperature" in entry.request_overrides or "top_p" in entry.request_overrides:
            raise ValueError("DeepSeek thinking mode must omit temperature and top_p")
        if "temperature" in config.defaults or "top_p" in config.defaults:
            raise ValueError("DeepSeek thinking mode defaults must omit temperature and top_p")
    else:
        temperature = entry.request_overrides.get(
            "temperature",
            config.defaults.get("temperature"),
        )
        if temperature is not None and float(temperature) != 0.0:
            raise ValueError("paper execution requires temperature=0 when configured")


def _validate_gate_c_pilot_condition_scope(
    condition: PaperExperimentCondition,
) -> None:
    if condition.experiment_id == "exp3_real_ai_fault_recovery":
        raise ValueError(
            "Gate C single-case pilot cannot execute Exp3 fault/death semantics; "
            "use the formal or smoke runner production callback"
        )
    if (
        condition.experiment_id == "exp4_real_ai_protocol_ablation"
        and condition.ablation_mode != "FULL"
    ):
        raise ValueError(
            "Gate C pilot only wires the Exp4 FULL provider baseline; "
            "ablation execution belongs to Prompt L"
        )


def _condition_for_selected_case(
    *,
    condition: PaperExperimentCondition,
    case: JsonObject,
) -> PaperExperimentCondition:
    if condition.domain != "lean_proof":
        return condition
    updates: dict[str, Any] = {}
    for field_name in (
        "topic_family",
        "topic_family_version",
        "construction_rule_id",
        "oracle_package_group",
        "proof_assembly_shape",
    ):
        condition_value = getattr(condition, field_name)
        case_value = case.get(field_name)
        if condition_value is not None and condition_value != case_value:
            raise ValueError(
                f"canonical condition {field_name} conflicts with frozen Lean case"
            )
        updates[field_name] = case_value
    return replace(condition, **updates)


def _transport_call_count(transport: Any | None) -> int:
    if transport is None:
        return 0
    calls = getattr(transport, "calls", 0)
    if isinstance(calls, list):
        return len(calls)
    if isinstance(calls, int) and not isinstance(calls, bool):
        return calls
    return 0


def _exp3_catalog_view(catalog: _FormalPaperCatalogView) -> JsonObject:
    factor_by_difficulty = {
        difficulty: catalog.cases_for(
            domain="factorization",
            difficulty=difficulty,
            paper_difficulty=difficulty,
        )
        for difficulty in FACTOR_PAPER_DIFFICULTIES
    }
    medium_lean_by_topic = {
        topic: catalog.cases_for(
            domain="lean_proof",
            difficulty="medium",
            paper_difficulty="medium_lemma_dag",
            topic_family=topic,
        )
        for topic in ("pure_logic", "function_set", "induction")
    }
    if catalog.catalog_version == "v2":
        factor_selection_policy, selected_factor_by_difficulty = (
            _factor_sampling_policy_and_slices(
                catalog,
                factor_by_difficulty,
                experiment_id="exp3_real_ai_fault_recovery",
            )
        )
        factor_rate = tuple(
            str(case["case_id"])
            for difficulty in FACTOR_PAPER_DIFFICULTIES
            for case in selected_factor_by_difficulty[difficulty]
        )
    else:
        selected_factor_by_difficulty = factor_by_difficulty
        factor_rate = tuple(
            str(case["case_id"])
            for case in factor_by_difficulty["medium"][:5]
        )
        factor_selection_policy = None
    rate_lean = {
        topic: (str(cases[0]["case_id"]),)
        for topic, cases in medium_lean_by_topic.items()
        if cases
    }
    death_factor = {
        difficulty: tuple(
            str(case["case_id"])
            for case in (
                selected_factor_by_difficulty[difficulty]
                if catalog.catalog_version == "v2"
                else cases[:1]
            )
        )
        for difficulty, cases in factor_by_difficulty.items()
        if cases
    }
    death_lean = dict(rate_lean)
    selected_case_ids = list(factor_rate)
    selected_case_ids.extend(value[0] for value in rate_lean.values())
    selected_case_ids.extend(value[0] for value in death_factor.values())
    selected_case_ids.extend(value[0] for value in death_lean.values())
    cases_by_id = {
        str(case["case_id"]): case
        for case in (
            catalog.factorization_cases
            + catalog.lean_cases
            + catalog.lean_lemma_graph_cases
        )
    }
    body = {
        "catalog_id": catalog.catalog_id,
        "catalog_digest": catalog.catalog_digest,
        "catalog_version": catalog.catalog_version,
        "suite_version": catalog.suite_version,
        "exp3_rate_fault_factorization_case_ids": factor_rate,
        "exp3_rate_fault_lean_case_ids_by_topic": rate_lean,
        "exp3_worker_death_factorization_case_ids_by_difficulty": death_factor,
        "exp3_worker_death_lean_case_ids_by_topic": death_lean,
        "ai_units_by_case_id": {
            case_id: tuple(
                f"{case_id}:{planned_ai_unit_id}"
                for planned_ai_unit_id in _planned_ai_unit_ids(
                    cases_by_id[case_id]
                )
            )
            for case_id in dict.fromkeys(selected_case_ids)
        },
    }
    if factor_selection_policy is not None:
        body["factorization_selection_policy"] = factor_selection_policy
        body["paper_suite_scale_policy"] = factor_selection_policy
    return body


def _exp4_catalog_view(catalog: _FormalPaperCatalogView) -> JsonObject:
    factor_by_difficulty = {
        difficulty: catalog.cases_for(
            domain="factorization",
            difficulty=difficulty,
            paper_difficulty=difficulty,
        )
        for difficulty in FACTOR_PAPER_DIFFICULTIES
    }
    selected_factor_by_difficulty = (
        _factor_sampling_policy_and_slices(
            catalog,
            factor_by_difficulty,
            experiment_id="exp4_real_ai_protocol_ablation",
        )[1]
        if catalog.catalog_version == "v2"
        else {
            difficulty: cases[:5]
            for difficulty, cases in factor_by_difficulty.items()
        }
    )
    factorization_slices = {
        difficulty: [
            _case_plan_summary(case)
            for case in selected_factor_by_difficulty[difficulty]
        ]
        for difficulty in FACTOR_PAPER_DIFFICULTIES
    }
    allocations = {
        "simple": {"pure_logic": 2, "function_set": 2, "induction": 1},
        "medium_lemma_dag": {"pure_logic": 1, "function_set": 2, "induction": 2},
        "hard_frontier": {"pure_logic": 2, "function_set": 1, "induction": 2},
    }
    selected_by_cell = catalog.task15_budget_input["selected_case_ids_by_cell"]
    cases_by_id = {
        str(case["case_id"]): case
        for case in catalog.lean_cases + catalog.lean_lemma_graph_cases
    }
    shared_lean_slices: dict[str, dict[str, list[JsonObject]]] = {}
    for paper_difficulty, topic_counts in allocations.items():
        shared_lean_slices[paper_difficulty] = {}
        for topic_family, count in topic_counts.items():
            cell_key = f"{paper_difficulty}/{topic_family}"
            shared_lean_slices[paper_difficulty][topic_family] = [
                _case_plan_summary(cases_by_id[str(case_id)])
                for case_id in selected_by_cell[cell_key][:count]
            ]
    shared_digests = {
        paper_difficulty: canonical_contract_digest(
            {
                "paper_difficulty": paper_difficulty,
                "topic_allocations": {
                    topic: len(cases) for topic, cases in by_topic.items()
                },
                "ordered_cases": [
                    case
                    for topic in ("pure_logic", "function_set", "induction")
                    for case in by_topic[topic]
                ],
            }
        )
        for paper_difficulty, by_topic in shared_lean_slices.items()
    }
    body = {
        "schema_version": "tokenshare.paper_exp4_catalog_view.v1",
        "catalog_source_kind": "paper_input_catalog_manifest",
        "catalog_id": catalog.catalog_id,
        "suite_version": catalog.suite_version,
        "catalog_version": catalog.catalog_version,
        "catalog_digest": catalog.catalog_digest,
        "lean_semantic_readiness_status": "ready",
        "lean_semantic_readiness_reason": None,
        "factorization_slices": factorization_slices,
        "shared_lean_slice_experiment_ids": ["exp2", "exp4", "exp5"],
        "shared_lean_slices": shared_lean_slices,
        "shared_lean_slice_digests": shared_digests,
    }
    if catalog.catalog_version == "v2":
        body["factorization_selection_policy"] = (
            _factor_sampling_policy_and_slices(
                catalog,
                factor_by_difficulty,
                experiment_id="exp4_real_ai_protocol_ablation",
            )[0]
        )
        body["paper_suite_scale_policy"] = body[
            "factorization_selection_policy"
        ]
    return body


def _factor_sampling_policy_and_slices(
    catalog: _FormalPaperCatalogView,
    factor_by_difficulty: Mapping[str, tuple[JsonObject, ...]],
    *,
    experiment_id: str,
) -> tuple[JsonObject, dict[str, tuple[Mapping[str, Any], ...]]]:
    if (
        catalog.paper_suite_scale_policy is None
        or catalog.selected_factorization_by_experiment is None
    ):
        raise ValueError(
            "catalog v2 planning requires an explicit paper suite scale policy"
        )
    selected = catalog.selected_factorization_by_experiment.get(experiment_id)
    if selected is None:
        raise ValueError("paper suite scale experiment selection is missing")
    return catalog.paper_suite_scale_policy, dict(selected)


def _case_plan_summary(case: JsonObject) -> JsonObject:
    return {
        "case_id": str(case["case_id"]),
        "expected_ai_unit_count": estimated_ai_units_for_case(case),
    }


def _planned_ai_unit_ids(case: JsonObject) -> list[str]:
    schema_version = str(case["schema_version"])
    if schema_version == "tokenshare.paper_factorization_case.v1":
        requested = int(case["split_params"]["requested_child_count"])
        domain_size = int(case["candidate_end"]) - int(case["candidate_start"]) + 1
        return [f"range_{index}" for index in range(min(requested, domain_size))]
    if schema_version == "tokenshare.paper_lean_case.v1":
        return [f"child_{index}" for index in range(int(case["expected_child_count"]))]
    declared = case.get("merge_plan_shape", {}).get("dependency_order")
    if isinstance(declared, list):
        return [str(node_id) for node_id in declared]
    return [str(node["node_id"]) for node in case["lemma_graph"]["nodes"]]


def _validate_gate_c_budget_commitment(
    *,
    budget: PaperBudgetResult,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
    case_id: str,
    ai_unit_id: str | None,
    request_limits: Mapping[str, Any],
) -> None:
    commitments = budget.quota_preflight.get("budget_commitments")
    if not isinstance(commitments, Mapping):
        raise ValueError("approved budget is missing exact commitments")
    frozen = commitments.get("frozen_selections")
    if not isinstance(frozen, list):
        raise ValueError("approved budget is missing frozen selections")
    matching_selections = [
        item
        for item in frozen
        if isinstance(item, Mapping)
        and item.get("condition_id") == condition.condition_id
    ]
    if len(matching_selections) != 1:
        raise ValueError("approved budget selection commitment is ambiguous")
    approved_selection = matching_selections[0]
    expected_selection = {
        "condition_digest": condition.condition_digest,
        "selection_digest": selection.selection_digest,
        "catalog_digest": selection.catalog_digest,
        "ordered_case_ids": list(selection.ordered_case_ids),
        "expected_ai_unit_count": selection.expected_ai_unit_count,
        "blocked_reason": selection.blocked_reason,
    }
    for field_name, expected in expected_selection.items():
        if approved_selection.get(field_name) != expected:
            raise ValueError(
                f"approved budget frozen selection drift: {field_name}"
            )
    ai_commitments = commitments.get("ai_unit_commitments")
    if not isinstance(ai_commitments, list):
        raise ValueError("approved budget is missing AI-unit commitments")
    matching_cases = [
        item
        for item in ai_commitments
        if isinstance(item, Mapping)
        and item.get("condition_id") == condition.condition_id
        and item.get("case_id") == case_id
    ]
    if len(matching_cases) != 1:
        raise ValueError("approved budget case commitment is ambiguous")
    planned_ai_unit_ids = matching_cases[0].get("planned_ai_unit_ids")
    if not isinstance(planned_ai_unit_ids, list) or not planned_ai_unit_ids:
        raise ValueError("approved budget case has no AI-unit commitment")
    if ai_unit_id is not None and ai_unit_id not in planned_ai_unit_ids:
        raise ValueError("selected AI unit is outside approved budget commitment")
    approved_request_limits = commitments.get("request_limits")
    if approved_request_limits != dict(request_limits):
        raise ValueError("approved budget request limits drift")


def _gate_c_result_from_adapter(
    *,
    suite_id: str,
    condition: PaperExperimentCondition,
    selection_digest: str,
    case_id: str,
    ai_unit_id: str | None,
    output_root: Path,
    budget: PaperBudgetResult,
    adapter_result: Any,
    transport_calls_observed: int,
    replayed: bool,
) -> GateCPilotExecutionResult:
    task = adapter_result.task_result
    transport_evidence = adapter_result.run_evidence["transport_evidence"]
    external_provider_calls = (
        int(task.provider_attempt_count)
        if transport_evidence.get("real_transport") is True
        and transport_evidence.get("transport_kind") == "ai_api"
        else 0
    )
    status = (
        "completed"
        if task.root_status == PaperTaskStatus.COMPLETED
        else "blocked"
        if task.root_status == PaperTaskStatus.BLOCKED
        else "completed_with_failures"
    )
    return GateCPilotExecutionResult(
        suite_id=suite_id,
        experiment_id=condition.experiment_id,
        condition_id=condition.condition_id,
        case_id=case_id,
        ai_unit_id=ai_unit_id,
        status=status,
        output_root=output_root.as_posix(),
        budget_digest=budget.budget_digest,
        condition_digest=condition.condition_digest,
        selection_digest=selection_digest,
        provider_attempt_count=int(task.provider_attempt_count),
        provider_calls_made=external_provider_calls,
        transport_calls_observed=transport_calls_observed,
        total_tokens=int(task.total_tokens),
        total_cost_estimate=float(task.cost_estimate),
        paper_eligible=False,
        replayed=replayed,
    )


def _persist_gate_c_pilot_evidence(
    *,
    output_root: Path,
    plan_body: JsonObject,
    catalog_manifest: PaperInputCatalogManifest,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
    budget: PaperBudgetResult,
    result: GateCPilotExecutionResult,
    adapter_result: Any,
) -> None:
    adapter_body = adapter_result.to_dict()
    task = _json_copy(adapter_body["task_result"])
    attempts = [_json_copy(item) for item in adapter_body["attempt_results"]]
    run_id = f"{condition.condition_id}_{result.case_id}"
    task.update(
        {
            "run_id": run_id,
            "case_id": result.case_id,
            "pilot_only": True,
            "paper_eligible": False,
        }
    )
    for attempt in attempts:
        attempt["pilot_only"] = True
        attempt["paper_eligible"] = False
    transport_evidence = _json_copy(
        adapter_body["run_evidence"]["transport_evidence"]
    )
    run_manifest: JsonObject = {
        "schema_version": "tokenshare.paper_gate_c_pilot_run_manifest.v1",
        "suite_id": result.suite_id,
        "experiment_id": result.experiment_id,
        "condition_id": result.condition_id,
        "condition_digest": result.condition_digest,
        "adapter_execution_condition_digest": adapter_body["condition"][
            "condition_digest"
        ],
        "selection_digest": result.selection_digest,
        "case_id": result.case_id,
        "ai_unit_id": result.ai_unit_id,
        "run_id": run_id,
        "status": result.status,
        "budget_digest": result.budget_digest,
        "adapter_output_root": adapter_body["output_root"],
        "artifact_root": f"runs/{condition.condition_id}/{result.case_id}/artifacts",
        "event_log_ref": {"path": "events/event_log.jsonl"},
        "transport_evidence": transport_evidence,
        "paper_eligible": False,
        "ineligibility_reasons": list(
            dict.fromkeys(
                [
                    "pilot_only",
                    *adapter_body["eligibility_report"].get(
                        "ineligibility_reasons", []
                    ),
                ]
            )
        ),
        "provider_calls_made": result.provider_calls_made,
        "transport_calls_observed": result.transport_calls_observed,
        "pilot_only": True,
    }
    run_result = {
        **run_manifest,
        "run_manifest_ref": {"path": "run_manifest.json"},
        "per_task_results_ref": {"path": "per_task_results.jsonl"},
        "per_attempt_results_ref": {"path": "per_attempt_results.jsonl"},
        "fault_injections_ref": {"path": "fault_injections.jsonl"},
    }
    now = _utc_now()
    events = [
        {
            "schema_version": "tokenshare.paper_gate_c_pilot_event.v1",
            "event_id": f"{result.suite_id}:suite_started",
            "event_type": "suite_started",
            "timestamp": now,
            "suite_id": result.suite_id,
        },
        {
            "schema_version": "tokenshare.paper_gate_c_pilot_event.v1",
            "event_id": f"{run_id}:task_started",
            "event_type": "task_started",
            "timestamp": now,
            "suite_id": result.suite_id,
            "condition_id": result.condition_id,
            "run_id": run_id,
            "case_id": result.case_id,
        },
        {
            "schema_version": "tokenshare.paper_gate_c_pilot_event.v1",
            "event_id": f"{run_id}:task_completed",
            "event_type": "task_completed",
            "timestamp": now,
            "suite_id": result.suite_id,
            "condition_id": result.condition_id,
            "run_id": run_id,
            "case_id": result.case_id,
            "root_status": task["root_status"],
        },
        {
            "schema_version": "tokenshare.paper_gate_c_pilot_event.v1",
            "event_id": f"{result.suite_id}:suite_finished",
            "event_type": "suite_finished",
            "timestamp": now,
            "suite_id": result.suite_id,
            "status": result.status,
        },
    ]
    artifacts = _artifact_index_records(
        run_id=run_id,
        task_result=task,
        attempts=attempts,
    )
    adapter_run_root = Path(str(adapter_body["output_root"]))
    adapter_store = ArtifactStore(adapter_run_root)
    model_execution_records = [
        json.loads(
            adapter_store.read_bytes(
                ArtifactRef.from_dict(attempt["model_execution_record_ref"])
            ).decode("utf-8")
        )
        for attempt in attempts
        if isinstance(attempt.get("model_execution_record_ref"), dict)
    ]
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / "execution_plan.json", plan_body)
    _write_json(output_root / "run_budget.json", budget.to_dict())
    _write_json(
        output_root / "input_catalog_manifest.json",
        catalog_manifest.to_dict(),
    )
    _write_jsonl(output_root / "conditions.jsonl", [condition.to_dict()])
    _write_json(output_root / "run_manifest.json", run_manifest)
    _write_json(adapter_run_root / "run_manifest.json", run_manifest)
    _write_jsonl(output_root / "run_results.jsonl", [run_result])
    _write_jsonl(output_root / "per_task_results.jsonl", [task])
    _write_jsonl(output_root / "per_attempt_results.jsonl", attempts)
    _write_jsonl(output_root / "fault_injections.jsonl", [])
    _write_jsonl(
        output_root / "model_execution_records.jsonl",
        model_execution_records,
    )
    _write_jsonl(output_root / "events" / "event_log.jsonl", events)
    _write_jsonl(output_root / "artifacts" / "artifact_index.jsonl", artifacts)
    from tokenshare.experiments.paper_metrics import write_gate_c_pilot_metrics

    write_gate_c_pilot_metrics(output_root)
    eligibility = {
        **_json_copy(adapter_body["eligibility_report"]),
        "paper_eligible": False,
        "pilot_only": True,
        "ineligibility_reasons": run_manifest["ineligibility_reasons"],
        "provider_calls_made": result.provider_calls_made,
    }
    _write_json(
        output_root / "audit" / "paper_eligibility_report.json",
        eligibility,
    )
    secret_scan = _json_copy(
        adapter_body["run_evidence"]["secret_scan_report"]
    )
    secret_scan["pilot_only"] = True
    _write_json(
        output_root / "audit" / "secret_scan_report.json",
        secret_scan,
    )
    _write_json(output_root / "suite_manifest.json", result.to_dict())
    _write_gate_c_evidence_manifest(
        output_root,
        provider_calls_made=result.provider_calls_made,
    )


_GATE_C_EVIDENCE_JSON_FILES = (
    "execution_plan.json",
    "run_budget.json",
    "input_catalog_manifest.json",
    "run_manifest.json",
    "suite_manifest.json",
)
_GATE_C_EVIDENCE_JSONL_FILES = (
    "conditions.jsonl",
    "run_results.jsonl",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "model_execution_records.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)


def _write_gate_c_evidence_manifest(
    output_root: Path,
    *,
    provider_calls_made: int,
) -> None:
    files: JsonObject = {}
    for relative_path in _GATE_C_EVIDENCE_JSON_FILES:
        records = [_read_json(output_root / relative_path)]
        files[relative_path] = {
            "record_count": 1,
            "records_digest": digest_json(records),
        }
    for relative_path in _GATE_C_EVIDENCE_JSONL_FILES:
        records = _read_jsonl(output_root / relative_path)
        files[relative_path] = {
            "record_count": len(records),
            "records_digest": digest_json(records),
        }
    manifest: JsonObject = {
        "schema_version": "tokenshare.paper_gate_c_evidence_manifest.v1",
        "execution_plan_digest": _read_json(
            output_root / "execution_plan.json"
        )["execution_plan_digest"],
        "files": files,
        "provider_calls_made": provider_calls_made,
    }
    manifest["evidence_manifest_digest"] = digest_json(manifest)
    _write_json(output_root / "evidence_manifest.json", manifest)


def _validate_gate_c_evidence_manifest(output_root: Path) -> None:
    manifest = _read_json(output_root / "evidence_manifest.json")
    stored_digest = manifest.get("evidence_manifest_digest")
    body = {
        key: value
        for key, value in manifest.items()
        if key != "evidence_manifest_digest"
    }
    if stored_digest != digest_json(body):
        raise ValueError("Gate C evidence manifest digest is invalid")
    expected_paths = set(_GATE_C_EVIDENCE_JSON_FILES) | set(
        _GATE_C_EVIDENCE_JSONL_FILES
    )
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != expected_paths:
        raise ValueError("Gate C evidence manifest file inventory is incomplete")
    for relative_path in _GATE_C_EVIDENCE_JSON_FILES:
        records = [_read_json(output_root / relative_path)]
        actual = {
            "record_count": 1,
            "records_digest": digest_json(records),
        }
        if files[relative_path] != actual:
            raise ValueError(f"Gate C evidence drift: {relative_path}")
    for relative_path in _GATE_C_EVIDENCE_JSONL_FILES:
        records = _read_jsonl(output_root / relative_path)
        actual = {
            "record_count": len(records),
            "records_digest": digest_json(records),
        }
        if files[relative_path] != actual:
            raise ValueError(f"Gate C evidence drift: {relative_path}")


def _replay_gate_c_pilot(
    *,
    output_root: Path,
    expected_plan: JsonObject,
) -> GateCPilotExecutionResult:
    required_paths = (
        "execution_plan.json",
        "suite_manifest.json",
        "run_budget.json",
        "input_catalog_manifest.json",
        "conditions.jsonl",
        "run_results.jsonl",
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "fault_injections.jsonl",
        "model_execution_records.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
        "metrics/per_condition_summary.csv",
        "audit/paper_eligibility_report.json",
        "audit/secret_scan_report.json",
        "evidence_manifest.json",
    )
    for relative_path in required_paths:
        if not (output_root / relative_path).is_file():
            raise ValueError(
                f"replay requires complete Gate C pilot evidence: {relative_path}"
            )
    stored_plan = _read_json(output_root / "execution_plan.json")
    if stored_plan != expected_plan:
        raise ValueError("replay execution plan drift")
    _validate_gate_c_evidence_manifest(output_root)
    from tokenshare.experiments.paper_metrics import (
        recompute_gate_c_pilot_metrics,
    )

    recompute_gate_c_pilot_metrics(output_root)
    suite = _read_json(output_root / "suite_manifest.json")
    return GateCPilotExecutionResult(
        suite_id=str(suite["suite_id"]),
        experiment_id=str(suite["experiment_id"]),
        condition_id=str(suite["condition_id"]),
        case_id=str(suite["case_id"]),
        ai_unit_id=(
            str(suite["ai_unit_id"])
            if suite.get("ai_unit_id") is not None
            else None
        ),
        status=str(suite["status"]),
        output_root=output_root.as_posix(),
        budget_digest=str(suite["budget_digest"]),
        condition_digest=str(suite["condition_digest"]),
        selection_digest=str(suite["selection_digest"]),
        provider_attempt_count=int(suite["provider_attempt_count"]),
        provider_calls_made=0,
        transport_calls_observed=0,
        total_tokens=int(suite["total_tokens"]),
        total_cost_estimate=float(suite["total_cost_estimate"]),
        paper_eligible=False,
        replayed=True,
    )


def _forbidden_plan_execution(**_kwargs: Any) -> PaperConditionResult:
    raise AssertionError("Gate C plan-only must not execute a condition")


def normalize_experiment_ids(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        normalized.append(EXPERIMENT_ALIASES.get(item, item))
    return tuple(dict.fromkeys(normalized))


def validate_experiment_dependency_order(experiment_ids: Sequence[str]) -> None:
    """验证共享 Exp1 evidence 的执行顺序，不静默重排用户输入。"""

    exp1 = "exp1_real_ai_feasibility"
    exp3 = "exp3_real_ai_fault_recovery"
    if (
        exp1 in experiment_ids
        and exp3 in experiment_ids
        and experiment_ids.index(exp1) > experiment_ids.index(exp3)
    ):
        raise ValueError(
            "Exp1 must precede Exp3 when shared Exp1 reference evidence is required"
        )


def expand_plan_conditions(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    experiment_ids: tuple[str, ...],
    worker_levels: tuple[int, ...],
    repeats: int,
    seed_family: tuple[int, ...],
    model_endpoint_cohort_preflight: JsonObject | None = None,
) -> tuple[PaperExperimentCondition, ...]:
    conditions: list[PaperExperimentCondition] = []
    for experiment_id in experiment_ids:
        worker_count = worker_levels[0] if worker_levels else 10
        for repeat_index in range(repeats):
            seed = seed_family[repeat_index % len(seed_family)] if seed_family else repeat_index
            if experiment_id == "exp5_real_ai_model_endpoint_comparison":
                for member_plan in _planned_model_endpoint_member_plans(
                    model_endpoint_cohort_preflight
                ):
                    for domain in ("factorization", "lean_proof"):
                        for difficulty in ("easy", "medium", "hard"):
                            paper_difficulty = (
                                difficulty if domain == "factorization" else "simple"
                            )
                            topic_family = (
                                None if domain == "factorization" else "pure_logic"
                            )
                            topic_family_version = (
                                None if domain == "factorization" else "shallow_v1"
                            )
                            member_id = str(member_plan["cohort_member_id"])
                            conditions.append(
                                PaperExperimentCondition(
                                    experiment_id=experiment_id,
                                    condition_id=(
                                        "exp5_"
                                        f"{domain}_{difficulty}_{member_id}"
                                        f"_w{worker_count}_r{repeat_index}"
                                    ),
                                    domain=domain,
                                    difficulty=difficulty,
                                    paper_difficulty=paper_difficulty,
                                    topic_family=topic_family,
                                    topic_family_version=topic_family_version,
                                    worker_count=worker_count,
                                    fault_type="none",
                                    fault_rate=0.0,
                                    ablation_mode="FULL",
                                    model_policy="fixed_entry",
                                    model_cohort_id=str(member_plan["cohort_id"]),
                                    cohort_member_id=member_id,
                                    provider_config_id=str(
                                        member_plan["provider_config_id"]
                                    ),
                                    model_entry_id=str(member_plan["selected_entry_id"]),
                                    provider_family=str(member_plan["provider_family"]),
                                    provider_model_id=str(member_plan["provider_model_id"]),
                                    reasoning_profile_id=str(
                                        member_plan["reasoning_profile_id"]
                                    ),
                                    model_cohort_digest=str(
                                        model_endpoint_cohort_preflight[
                                            "model_cohort_digest"
                                        ]
                                    ),
                                    source_provider_config_digest=str(
                                        member_plan[
                                            "source_provider_config_digest"
                                        ]
                                    ),
                                    model_endpoint_identity_digest=str(
                                        member_plan[
                                            "model_endpoint_identity_digest"
                                        ]
                                    ),
                                    repeat_id=repeat_index,
                                    seed=seed,
                                    catalog_digest=catalog_manifest.catalog_digest,
                                )
                            )
                continue
            for domain in ("factorization", "lean_proof"):
                for difficulty in ("easy", "medium", "hard"):
                    paper_difficulty = difficulty if domain == "factorization" else "simple"
                    topic_family = None if domain == "factorization" else "pure_logic"
                    topic_family_version = None if domain == "factorization" else "shallow_v1"
                    if experiment_id == "exp1_real_ai_feasibility":
                        conditions.append(
                            PaperExperimentCondition(
                                experiment_id=experiment_id,
                                condition_id=(
                                    f"exp1_{domain}_{difficulty}_w{worker_count}_r{repeat_index}"
                                ),
                                domain=domain,
                                difficulty=difficulty,
                                paper_difficulty=paper_difficulty,
                                topic_family=topic_family,
                                topic_family_version=topic_family_version,
                                worker_count=worker_count,
                                fault_type="none",
                                fault_rate=0.0,
                                ablation_mode="FULL",
                                model_policy="fixed_entry",
                                repeat_id=repeat_index,
                                seed=seed,
                                catalog_digest=catalog_manifest.catalog_digest,
                            )
                        )
                    elif experiment_id == "exp4_real_ai_protocol_ablation":
                        for ablation_mode in ablation_modes():
                            conditions.append(
                                PaperExperimentCondition(
                                    experiment_id=experiment_id,
                                    condition_id=(
                                        "exp4_"
                                        f"{domain}_{difficulty}_{ablation_mode.value}"
                                        f"_w{worker_count}_r{repeat_index}"
                                    ),
                                    domain=domain,
                                    difficulty=difficulty,
                                    paper_difficulty=paper_difficulty,
                                    topic_family=topic_family,
                                    topic_family_version=topic_family_version,
                                    worker_count=worker_count,
                                    fault_type="none",
                                    fault_rate=0.0,
                                    ablation_mode=ablation_mode.value,
                                    model_policy="fixed_entry",
                                    repeat_id=repeat_index,
                                    seed=seed,
                                    catalog_digest=catalog_manifest.catalog_digest,
                                    paper_eligible_required=not (
                                        domain == "lean_proof"
                                        and difficulty in {"medium", "hard"}
                                    ),
                                )
                            )
    return tuple(conditions)


def _planned_model_endpoint_member_plans(
    model_endpoint_cohort_preflight: JsonObject | None,
) -> tuple[JsonObject, ...]:
    if (
        not isinstance(model_endpoint_cohort_preflight, dict)
        or model_endpoint_cohort_preflight.get("status") != "planned"
    ):
        return ()
    member_plans = model_endpoint_cohort_preflight.get("member_plans")
    if not isinstance(member_plans, dict):
        return ()
    model_cohort_digest = model_endpoint_cohort_preflight.get(
        "model_cohort_digest"
    )
    if not isinstance(model_cohort_digest, str) or not model_cohort_digest:
        return ()
    planned: list[JsonObject] = []
    required_plan_fields = (
        "cohort_id",
        "cohort_member_id",
        "provider_config_id",
        "selected_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    )
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS:
        plan = member_plans.get(member_id)
        if not isinstance(plan, dict) or plan.get("status") != "planned":
            return ()
        for field_name in required_plan_fields:
            value = plan.get(field_name)
            if not isinstance(value, str) or not value:
                return ()
        planned.append(dict(plan))
    return tuple(planned)


def build_lean_3x3_matrix_plan(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    target_case_count: int = LEAN_3X3_TARGET_CASE_COUNT,
) -> JsonObject:
    if target_case_count < 1:
        raise ValueError("target_case_count must be >= 1")
    cells: list[JsonObject] = []
    environment_digest = str(catalog_manifest.lean_preflight_summary["environment_digest"])
    oracle_package_digests = _lean_oracle_package_digests(catalog_manifest)
    topic_family_expected_ai_unit_counts = {
        topic_family: 0 for topic_family in LEAN_TOPIC_FAMILIES
    }
    topic_family_available_case_counts = {
        topic_family: 0 for topic_family in LEAN_TOPIC_FAMILIES
    }
    for paper_difficulty in LEAN_PAPER_DIFFICULTIES:
        for topic_family in LEAN_TOPIC_FAMILIES:
            cases = catalog_manifest.cases_for(
                domain="lean_proof",
                paper_difficulty=paper_difficulty,
                topic_family=topic_family,
            )
            checker_backed_cases = tuple(
                case for case in cases if _is_checker_backed_lean_case(case)
            )
            selected_cases = _lean_select_semantically_distinct_cases(
                checker_backed_cases,
                target_case_count=target_case_count,
            )
            available_case_count = len(selected_cases)
            expected_ai_unit_count = sum(
                estimated_ai_units_for_case(case) for case in selected_cases
            )
            preflight_status = _lean_cell_preflight_summary(selected_cases)
            catalog_blocked_reason = _lean_cell_blocked_reason(
                cases=cases,
                checker_backed_case_count=len(checker_backed_cases),
                available_case_count=available_case_count,
                target_case_count=target_case_count,
            )
            semantic_fingerprints = [
                lean_case_semantic_fingerprint(case) for case in selected_cases
            ]
            golden_case_ids = _lean_cell_golden_case_ids(selected_cases)
            golden_evidence_by_case_id = _lean_cell_golden_evidence_by_case_id(
                cases=selected_cases,
                golden_case_ids=golden_case_ids,
                blocked_reason=catalog_blocked_reason,
            )
            readiness = _lean_cell_readiness(
                cases=selected_cases,
                checker_backed_cases=selected_cases,
                blocked_reason=catalog_blocked_reason,
                golden_case_ids=golden_case_ids,
                golden_evidence_by_case_id=golden_evidence_by_case_id,
            )
            blocked_reason = readiness["blocker"]
            paper_eligible_possible = blocked_reason is None
            topic_family_expected_ai_unit_counts[topic_family] += expected_ai_unit_count
            topic_family_available_case_counts[topic_family] += available_case_count
            cells.append(
                {
                    "schema_version": "tokenshare.lean_3x3_matrix_cell_plan.v1",
                    "domain": "lean_proof",
                    "status": "planned" if paper_eligible_possible else "blocked",
                    "paper_difficulty": paper_difficulty,
                    "topic_family": topic_family,
                    "catalog_case_count": len(selected_cases),
                    "catalog_pool_case_count": len(cases),
                    "checker_backed_pool_case_count": len(checker_backed_cases),
                    "available_case_count": available_case_count,
                    "target_case_count": target_case_count,
                    "expected_ai_unit_count": expected_ai_unit_count,
                    "preflight_status": preflight_status,
                    "paper_eligible_possible": paper_eligible_possible,
                    "blocked_reason": blocked_reason,
                    "semantic_fingerprint_count": len(set(semantic_fingerprints)),
                    "semantic_fingerprint_digest": digest_json(semantic_fingerprints),
                    "semantic_fingerprint_digests": semantic_fingerprints,
                    "golden_case_ids": golden_case_ids,
                    "golden_evidence_by_case_id": golden_evidence_by_case_id,
                    "case_ids": [str(case["case_id"]) for case in selected_cases],
                    "oracle_package_digest": _lean_cell_oracle_package_digest(
                        selected_cases
                    ),
                    "readiness": readiness,
                }
            )
    body: JsonObject = {
        "schema_version": "tokenshare.lean_3x3_matrix_plan.v1",
        "domain": "lean_proof",
        "catalog_digest": catalog_manifest.catalog_digest,
        "environment_digest": environment_digest,
        "oracle_package_digests": oracle_package_digests,
        "provider_calls_made": 0,
        "cell_count": len(cells),
        "target_case_count": target_case_count,
        "cells": cells,
        "topic_family_expected_ai_unit_counts": topic_family_expected_ai_unit_counts,
        "topic_family_available_case_counts": topic_family_available_case_counts,
        "paper_eligible_possible_cell_count": sum(
            1 for cell in cells if cell["paper_eligible_possible"] is True
        ),
        "blocked_cell_count": sum(1 for cell in cells if cell["status"] == "blocked"),
        "task15_boundary": {
            "formal_exp1_started": False,
            "exp2_to_exp5_started": False,
            "real_ai_api_calls_allowed": False,
            "provider_calls_made": 0,
        },
    }
    selected_case_ids_by_cell = {
        f"{cell['paper_difficulty']}/{cell['topic_family']}": list(cell["case_ids"])
        for cell in cells
    }
    semantic_fingerprint_digests_by_cell = {
        f"{cell['paper_difficulty']}/{cell['topic_family']}": list(
            cell["semantic_fingerprint_digests"]
        )
        for cell in cells
    }
    golden_case_ids_by_cell = {
        f"{cell['paper_difficulty']}/{cell['topic_family']}": list(
            cell["golden_case_ids"]
        )
        for cell in cells
    }
    selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_manifest.catalog_digest,
            "environment_digest": environment_digest,
            "oracle_package_digests": oracle_package_digests,
            "target_case_count": target_case_count,
            "selected_case_ids_by_cell": selected_case_ids_by_cell,
            "semantic_fingerprint_digests_by_cell": (
                semantic_fingerprint_digests_by_cell
            ),
            "golden_case_ids_by_cell": golden_case_ids_by_cell,
        }
    )
    body["task15_budget_input"] = {
        "schema_version": "tokenshare.lean_task15_budget_input.v1",
        "catalog_digest": catalog_manifest.catalog_digest,
        "environment_digest": environment_digest,
        "oracle_package_digests": oracle_package_digests,
        "target_case_count": target_case_count,
        "executable_cell_count": body["paper_eligible_possible_cell_count"],
        "blocked_cell_count": body["blocked_cell_count"],
        "expected_ai_unit_count": sum(topic_family_expected_ai_unit_counts.values()),
        "selected_case_count": sum(len(case_ids) for case_ids in selected_case_ids_by_cell.values()),
        "selected_case_ids_by_cell": selected_case_ids_by_cell,
        "semantic_fingerprint_digests_by_cell": semantic_fingerprint_digests_by_cell,
        "golden_case_ids_by_cell": golden_case_ids_by_cell,
        "case_counts_by_cell": {
            cell_key: len(case_ids)
            for cell_key, case_ids in selected_case_ids_by_cell.items()
        },
        "selection_digest": selection_digest,
        "catalog_slice_digest": selection_digest,
        "construction_sampling_rules": {
            "selection_rule": (
                "stable catalog order; first target_case_count checker-backed "
                "preflight-passed cases per paper_difficulty/topic_family cell"
            ),
            "simple_pure_logic_rule": (
                "legacy shallow Lean v1 rows remain simple/pure_logic and are "
                "sliced to exactly target_case_count"
            ),
            "blocked_rows_counted": False,
            "provider_calls_made": 0,
        },
        "provider_calls_made": 0,
    }
    body["matrix_digest"] = digest_json(
        _lean_matrix_digest_body(body, require_authority_metadata=True)
    )
    body["task15_budget_input"]["matrix_digest"] = body["matrix_digest"]
    return body


def _lean_matrix_digest_body(
    body: JsonObject,
    *,
    require_authority_metadata: bool = False,
) -> JsonObject:
    ignored_wrapper_fields = {
        "blocked_cell_map",
        "catalog_path",
        "catalog_version",
        "executable_cell_map",
        "matrix_digest",
    }
    digest_body = {
        key: value
        for key, value in body.items()
        if key not in ignored_wrapper_fields
    }
    digest_body["schema_version"] = "tokenshare.lean_3x3_matrix_plan.v1"
    task15_budget_input = digest_body.get("task15_budget_input")
    if isinstance(task15_budget_input, Mapping):
        digest_body["task15_budget_input"] = {
            key: value
            for key, value in task15_budget_input.items()
            if key != "matrix_digest"
        }
    digest_body["cells"] = [
        _lean_cell_digest_projection(
            cell,
            require_authority_metadata=require_authority_metadata,
        )
        for cell in body["cells"]
    ]
    return digest_body


def _lean_cell_digest_projection(
    cell: JsonObject,
    *,
    require_authority_metadata: bool = False,
) -> JsonObject:
    projected = {
        key: value
        for key, value in cell.items()
        if key != "golden_evidence_by_case_id"
    }
    projected["golden_evidence_digest_by_case_id"] = {
        case_id: digest_json(
            _lean_golden_evidence_digest_body(
                evidence,
                require_authority_metadata=require_authority_metadata,
            )
        )
        for case_id, evidence in sorted(
            dict(cell.get("golden_evidence_by_case_id", {})).items()
        )
    }
    return projected


def _lean_golden_evidence_digest_body(
    evidence: JsonObject,
    *,
    require_authority_metadata: bool = False,
) -> JsonObject:
    certificate_ref = evidence.get("split_certificate_ref")
    if not isinstance(certificate_ref, Mapping):
        raise ValueError("Lean golden evidence split_certificate_ref is missing")
    certificate_metadata = certificate_ref.get("metadata")
    if not isinstance(certificate_metadata, Mapping):
        raise ValueError("Lean golden evidence certificate metadata is missing")
    authority_environment_digest = certificate_metadata.get(
        "authority_environment_digest"
    )
    authority_normalized_certificate_digest = certificate_metadata.get(
        "authority_normalized_certificate_digest"
    )
    for field_name, digest in (
        ("runtime environment_digest", evidence.get("environment_digest")),
        ("runtime split_certificate_digest", evidence.get("split_certificate_digest")),
    ):
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError(f"Lean golden evidence {field_name} is incomplete")
    has_authority_metadata = (
        authority_environment_digest is not None
        or authority_normalized_certificate_digest is not None
    )
    if require_authority_metadata or has_authority_metadata:
        for field_name, digest in (
            ("authority_environment_digest", authority_environment_digest),
            (
                "authority_normalized_certificate_digest",
                authority_normalized_certificate_digest,
            ),
        ):
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise ValueError(f"Lean golden evidence {field_name} is incomplete")
    else:
        # 历史 readiness / regression fixture 只保留原有 typed runtime 身份；
        # 它不会被提升为 authority-normalized、可发布的 Lean 证据。
        return {
            "schema_version": evidence.get("schema_version"),
            "evidence_source": evidence.get("evidence_source"),
            "case_id": evidence.get("case_id"),
            "environment_digest": evidence.get("environment_digest"),
            "oracle_package_digest": evidence.get("oracle_package_digest"),
            "split_certificate_digest": evidence.get("split_certificate_digest"),
            "deterministic_split": evidence.get("deterministic_split"),
            "child_proof_file_construction": evidence.get(
                "child_proof_file_construction"
            ),
            "checker_preflight": evidence.get("checker_preflight"),
            "dependency_aware_merge": evidence.get("dependency_aware_merge"),
            "root_recheck": evidence.get("root_recheck"),
            "provider_calls_made": evidence.get("provider_calls_made"),
            "node_ids": sorted(dict(evidence.get("node_checker_report_refs", {}))),
        }
    return {
        "schema_version": evidence.get("schema_version"),
        "evidence_source": evidence.get("evidence_source"),
        "case_id": evidence.get("case_id"),
        "environment_digest": authority_environment_digest,
        "oracle_package_digest": evidence.get("oracle_package_digest"),
        "split_certificate_digest": authority_normalized_certificate_digest,
        "deterministic_split": evidence.get("deterministic_split"),
        "child_proof_file_construction": evidence.get(
            "child_proof_file_construction"
        ),
        "checker_preflight": evidence.get("checker_preflight"),
        "dependency_aware_merge": evidence.get("dependency_aware_merge"),
        "root_recheck": evidence.get("root_recheck"),
        "provider_calls_made": evidence.get("provider_calls_made"),
        "node_ids": sorted(dict(evidence.get("node_checker_report_refs", {}))),
    }


def _lean_cell_preflight_summary(cases: tuple[JsonObject, ...]) -> JsonObject:
    status_counts: dict[str, int] = {}
    for case in cases:
        status = _lean_case_preflight_status(case)
        status_counts[status] = status_counts.get(status, 0) + 1
    if not cases:
        summary = "missing"
    elif set(status_counts) == {"passed"}:
        summary = "passed"
    elif "structured_blocked" in status_counts:
        summary = "structured_blocked"
    else:
        summary = "mixed"
    return {
        "summary": summary,
        "status_counts": dict(sorted(status_counts.items())),
        "checker_backed_case_count": sum(
            1 for case in cases if _is_checker_backed_lean_case(case)
        ),
    }


def _lean_select_semantically_distinct_cases(
    cases: tuple[JsonObject, ...],
    *,
    target_case_count: int,
) -> tuple[JsonObject, ...]:
    selected: list[JsonObject] = []
    seen_fingerprints: set[str] = set()
    for case in cases:
        fingerprint = lean_case_semantic_fingerprint(case)
        if fingerprint in seen_fingerprints:
            continue
        selected.append(case)
        seen_fingerprints.add(fingerprint)
        if len(selected) == target_case_count:
            break
    return tuple(selected)


def _lean_case_preflight_status(case: JsonObject) -> str:
    if case.get("schema_version") == "tokenshare.paper_lean_case.v1":
        proof_ref = case.get("oracle_proof_ref")
        if isinstance(proof_ref, dict):
            return str(proof_ref.get("preflight_status") or "unknown")
        return "unknown"
    return str(case.get("preflight_status") or "unknown")


def _is_checker_backed_lean_case(case: JsonObject) -> bool:
    status = _lean_case_preflight_status(case)
    if status != "passed":
        return False
    if case.get("schema_version") == "tokenshare.paper_lean_case.v1":
        proof_ref = case.get("oracle_proof_ref")
        return isinstance(proof_ref, dict) and bool(proof_ref.get("proof_source"))
    oracle_ref = case.get("oracle_proof_package_ref")
    return isinstance(oracle_ref, dict) and bool(oracle_ref.get("node_proof_sources"))


def _lean_oracle_package_digests(
    catalog_manifest: PaperInputCatalogManifest,
) -> JsonObject:
    result: JsonObject = {}
    for case in catalog_manifest.lean_lemma_graph_cases:
        oracle_ref = case.get("oracle_proof_package_ref")
        if not isinstance(oracle_ref, dict):
            continue
        group = str(case["oracle_package_group"])
        digest = str(oracle_ref["content_hash"])
        existing = result.get(group)
        if existing is not None and existing != digest:
            raise ValueError("Lean oracle package group has multiple digests")
        result[group] = digest
    return dict(sorted(result.items()))


def _lean_cell_blocked_reason(
    *,
    cases: tuple[JsonObject, ...],
    checker_backed_case_count: int,
    available_case_count: int,
    target_case_count: int,
) -> str | None:
    if available_case_count >= target_case_count:
        return None
    if cases and all(_is_hard_frontier_no_oracle_case(case) for case in cases):
        return "missing_oracle_proof_package"
    if checker_backed_case_count >= target_case_count:
        return "semantic_duplicate"
    return "insufficient_catalog"


def _lean_cell_golden_case_ids(cases: tuple[JsonObject, ...]) -> list[str]:
    checker_backed = [
        case
        for case in cases
        if _is_checker_backed_lean_case(case)
        and case.get("schema_version") == LEAN_V2_SCHEMA_VERSION
    ]
    return [str(case["case_id"]) for case in checker_backed[:2]]


def _lean_cell_golden_evidence_by_case_id(
    *,
    cases: tuple[JsonObject, ...],
    golden_case_ids: list[str],
    blocked_reason: str | None,
) -> JsonObject:
    if blocked_reason is not None:
        return {}
    cases_by_id = {str(case["case_id"]): case for case in cases}
    evidence: JsonObject = {}
    for case_id in golden_case_ids:
        case = cases_by_id.get(case_id)
        if case is None or not _is_checker_backed_lean_case(case):
            continue
        try:
            evidence[case_id] = _lean_task14_golden_evidence(case)
        except Exception as exc:
            evidence[case_id] = _lean_blocked_golden_evidence(
                case_id=case_id,
                error=str(exc),
            )
    return evidence


def _lean_blocked_golden_evidence(*, case_id: str, error: str) -> JsonObject:
    return {
        "schema_version": "tokenshare.lean_task14_golden_evidence.v1",
        "evidence_source": "local_oracle_lemma_graph",
        "case_id": case_id,
        "deterministic_split": "blocked",
        "child_proof_file_construction": "blocked",
        "checker_preflight": "blocked",
        "dependency_aware_merge": "blocked",
        "root_recheck": "blocked",
        "provider_calls_made": 0,
        "error": error,
    }


def _lean_task14_golden_evidence(case: JsonObject) -> JsonObject:
    return _lean_task14_golden_evidence_from_json(
        json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


@lru_cache(maxsize=64)
def _lean_task14_golden_evidence_from_json(case_json: str) -> JsonObject:
    return build_lean_lemma_graph_oracle_evidence(case=json.loads(case_json))


def _lean_cell_oracle_package_digest(cases: tuple[JsonObject, ...]) -> str | None:
    digests = {
        str(case["oracle_proof_package_ref"]["content_hash"])
        for case in cases
        if isinstance(case.get("oracle_proof_package_ref"), dict)
    }
    if not digests:
        return None
    if len(digests) != 1:
        return "mixed"
    return next(iter(digests))


def _lean_cell_readiness(
    *,
    cases: tuple[JsonObject, ...],
    checker_backed_cases: tuple[JsonObject, ...],
    blocked_reason: str | None,
    golden_case_ids: list[str],
    golden_evidence_by_case_id: JsonObject,
) -> JsonObject:
    if cases and all(_is_hard_frontier_no_oracle_case(case) for case in cases):
        return {
            "oracle_package": "missing",
            "local_checker_preflight": "structured_blocked",
            "deterministic_split_rule": "frontier_stress",
            "proof_file_assembly": "blocked_missing_oracle",
            "dependency_aware_merge": "blocked_missing_oracle",
            "root_recheck": "blocked_missing_oracle",
            "golden_evidence": "blocked",
            "provider_calls_made": 0,
            "blocker": blocked_reason,
        }
    if blocked_reason is not None:
        return _lean_blocked_readiness(
            blocker=blocked_reason,
            oracle_package="present" if checker_backed_cases else "missing",
        )
    if checker_backed_cases:
        golden_evidence_status = _lean_golden_evidence_status(
            cases=cases,
            golden_case_ids=golden_case_ids,
            golden_evidence_by_case_id=golden_evidence_by_case_id,
        )
        if golden_evidence_status != "passed":
            return _lean_blocked_readiness(
                blocker="missing_golden_evidence",
                oracle_package="present",
                golden_evidence=golden_evidence_status,
            )
        return {
            "oracle_package": "present",
            "local_checker_preflight": "passed",
            "deterministic_split_rule": "ready",
            "proof_file_assembly": "ready",
            "dependency_aware_merge": "ready",
            "root_recheck": "ready",
            "golden_evidence": "passed",
            "provider_calls_made": 0,
            "blocker": blocked_reason,
        }
    return {
        "oracle_package": "missing",
        "local_checker_preflight": "missing",
        "deterministic_split_rule": "missing",
        "proof_file_assembly": "missing",
        "dependency_aware_merge": "missing",
        "root_recheck": "missing",
        "golden_evidence": "missing_golden_evidence",
        "provider_calls_made": 0,
        "blocker": blocked_reason,
    }


def _lean_blocked_readiness(
    *,
    blocker: str,
    oracle_package: str,
    golden_evidence: str = "blocked",
) -> JsonObject:
    return {
        "oracle_package": oracle_package,
        "local_checker_preflight": "blocked",
        "deterministic_split_rule": "blocked",
        "proof_file_assembly": "blocked",
        "dependency_aware_merge": "blocked",
        "root_recheck": "blocked",
        "golden_evidence": golden_evidence,
        "provider_calls_made": 0,
        "blocker": blocker,
    }


def _lean_golden_evidence_status(
    *,
    cases: tuple[JsonObject, ...],
    golden_case_ids: list[str],
    golden_evidence_by_case_id: JsonObject,
) -> str:
    if not 1 <= len(golden_case_ids) <= 2:
        return "missing_golden_evidence"
    if len(set(golden_case_ids)) != len(golden_case_ids):
        return "missing_golden_evidence"
    selected_cases_by_id = {str(case["case_id"]): case for case in cases}
    for case_id in golden_case_ids:
        case = selected_cases_by_id.get(case_id)
        if case is None:
            return "missing_golden_evidence"
        if case.get("schema_version") != LEAN_V2_SCHEMA_VERSION:
            return "missing_golden_evidence"
        if not _is_checker_backed_lean_case(case):
            return "missing_golden_evidence"
    if any(case_id not in golden_evidence_by_case_id for case_id in golden_case_ids):
        return "missing_golden_evidence"
    for case_id in golden_case_ids:
        evidence = golden_evidence_by_case_id.get(case_id)
        if not isinstance(evidence, dict):
            return "missing_golden_evidence"
        if str(evidence.get("case_id")) != case_id:
            return "blocked"
        if evidence.get("evidence_source") != "local_oracle_lemma_graph":
            return "blocked"
        if evidence.get("provider_calls_made") != 0:
            return "blocked"
        for stage in (
            "deterministic_split",
            "child_proof_file_construction",
            "checker_preflight",
            "dependency_aware_merge",
            "root_recheck",
        ):
            if evidence.get(stage) != "passed":
                return "blocked"
    return "passed"


def _is_hard_frontier_no_oracle_case(case: JsonObject) -> bool:
    return (
        case.get("paper_difficulty") == "hard_frontier"
        and case.get("oracle_proof_package_ref") is None
        and _lean_case_preflight_status(case) in {"structured_blocked", "frontier_stress"}
    )


def _approved_exp1_pilot_summary(budget: PaperBudgetResult) -> JsonObject:
    quota = budget.quota_preflight
    if quota.get("provider_calls_made") != 0:
        raise ValueError("Exp1 pilot budget preflight must have zero provider calls")
    summary = quota.get("exp1_pilot")
    if not isinstance(summary, dict):
        raise ValueError("Exp1 pilot budget summary is missing")
    if summary.get("approval_status") != "approved":
        raise ValueError("Exp1 pilot budget is not approved")
    return summary


def _paper_cases_by_id(
    catalog_manifest: PaperInputCatalogManifest,
) -> dict[str, JsonObject]:
    return {
        str(case["case_id"]): case
        for case in (
            catalog_manifest.factorization_cases
            + catalog_manifest.lean_cases
            + catalog_manifest.lean_lemma_graph_cases
        )
    }


def _adapter_task_id(*, domain: str, case_id: str) -> str:
    if domain == "factorization":
        return f"paper_factorization_{case_id}"
    if domain == "lean_proof":
        return f"paper_lean_{case_id}"
    raise ValueError(f"unsupported Exp1 pilot domain: {domain}")


def _runner_verified_adapter_eligibility(
    result_body: JsonObject,
) -> tuple[bool, list[str]]:
    """不要让注入 adapter 用布尔字段绕过真实 transport evidence。"""

    reasons: list[str] = []
    task_result = result_body.get("task_result")
    eligibility_report = result_body.get("eligibility_report")
    run_evidence = result_body.get("run_evidence")
    transport_evidence = (
        run_evidence.get("transport_evidence")
        if isinstance(run_evidence, dict)
        else None
    )
    if not isinstance(task_result, dict) or task_result.get("paper_eligible") is not True:
        reasons.append("adapter_task_not_paper_eligible")
    if (
        not isinstance(eligibility_report, dict)
        or eligibility_report.get("paper_eligible") is not True
    ):
        reasons.append("adapter_eligibility_report_not_paper_eligible")
    if (
        not isinstance(transport_evidence, dict)
        or transport_evidence.get("real_transport") is not True
        or transport_evidence.get("transport_kind") != "ai_api"
    ):
        reasons.append("non_real_transport_evidence")
    return not reasons, reasons


def _validate_exp1_execution_gate(
    *,
    pilot_profile: Exp1PilotProfile,
    budget: PaperBudgetResult,
    approved_budget_digest: str,
    baseline_entry_id: str,
    real_transport: bool,
) -> None:
    if approved_budget_digest != budget.budget_digest:
        raise ValueError("approved budget digest does not match execution budget")
    _approved_exp1_pilot_summary(budget)
    if baseline_entry_id != pilot_profile.model_endpoint_identity.selected_entry_id:
        raise ValueError("baseline entry does not match the approved pilot profile")
    if not real_transport:
        raise ValueError("Exp1 pilot execution requires real transport")
    validate_fixed_entry_config_identity(
        expected_identity=pilot_profile.model_endpoint_identity,
        provider_config_id=pilot_profile.model_endpoint_identity.provider_config_id,
        source_config=pilot_profile.source_provider_config,
    )


def _execution_hard_limits(
    *,
    budget: PaperBudgetResult,
    provider_attempt_limit: int | None,
    token_limit: int | None,
    cost_limit: float | None,
) -> JsonObject:
    limits: JsonObject = {
        "provider_attempt_limit": (
            budget.max_provider_attempts
            if provider_attempt_limit is None
            else provider_attempt_limit
        ),
        "token_limit": (
            budget.token_upper_bound if token_limit is None else token_limit
        ),
        "cost_limit": (
            budget.cost_upper_bound if cost_limit is None else cost_limit
        ),
    }
    if not isinstance(limits["provider_attempt_limit"], int) or isinstance(
        limits["provider_attempt_limit"], bool
    ):
        raise ValueError("provider_attempt_limit must be an integer")
    if int(limits["provider_attempt_limit"]) < 0:
        raise ValueError("provider_attempt_limit must be non-negative")
    if not isinstance(limits["token_limit"], int) or isinstance(
        limits["token_limit"], bool
    ):
        raise ValueError("token_limit must be an integer")
    if int(limits["token_limit"]) < 0:
        raise ValueError("token_limit must be non-negative")
    if not isinstance(limits["cost_limit"], (int, float)) or isinstance(
        limits["cost_limit"], bool
    ):
        raise ValueError("cost_limit must be numeric")
    if not math.isfinite(float(limits["cost_limit"])):
        raise ValueError("cost_limit must be finite")
    if float(limits["cost_limit"]) < 0:
        raise ValueError("cost_limit must be non-negative")
    if int(limits["provider_attempt_limit"]) > budget.max_provider_attempts:
        raise ValueError("provider_attempt_limit exceeds the approved budget")
    if int(limits["token_limit"]) > budget.token_upper_bound:
        raise ValueError("token_limit exceeds the approved budget")
    if float(limits["cost_limit"]) > budget.cost_upper_bound + 1e-12:
        raise ValueError("cost_limit exceeds the approved budget")
    limits["provider_attempt_limit"] = int(limits["provider_attempt_limit"])
    limits["token_limit"] = int(limits["token_limit"])
    limits["cost_limit"] = float(limits["cost_limit"])
    return limits


def _load_or_initialize_execution_evidence(
    *,
    suite_root: Path,
    plan_body: JsonObject,
    resume: bool,
    replay_only: bool,
) -> dict[str, list[JsonObject]]:
    plan_path = suite_root / "execution_plan.json"
    if suite_root.exists():
        if not (resume or replay_only):
            raise FileExistsError(
                "Exp1 pilot suite output already exists; use resume or replay-only"
            )
        if not plan_path.exists():
            raise ValueError("existing Exp1 pilot suite is missing execution plan")
        existing_plan = _read_json(plan_path)
        stored_plan_digest = existing_plan.get("execution_plan_digest")
        plan_without_digest = {
            key: value
            for key, value in existing_plan.items()
            if key != "execution_plan_digest"
        }
        if stored_plan_digest != digest_json(plan_without_digest):
            raise ValueError("existing Exp1 pilot execution plan digest is invalid")
        if existing_plan.get("execution_plan_digest") != plan_body.get(
            "execution_plan_digest"
        ):
            raise ValueError("existing Exp1 pilot execution plan has drifted")
        evidence = {
            "runs": _read_jsonl(suite_root / "run_results.jsonl"),
            "tasks": _read_jsonl(suite_root / "per_task_results.jsonl"),
            "attempts": _read_jsonl(suite_root / "per_attempt_results.jsonl"),
            "events": _read_jsonl(suite_root / "events" / "event_log.jsonl"),
            "artifacts": _read_jsonl(
                suite_root / "artifacts" / "artifact_index.jsonl"
            ),
        }
        _validate_evidence_manifest(
            suite_root=suite_root,
            execution_plan=existing_plan,
            evidence=evidence,
        )
        _validate_execution_evidence_consistency(
            suite_root=suite_root,
            execution_plan=existing_plan,
            evidence=evidence,
        )
        return evidence
    if resume or replay_only:
        raise ValueError("resume or replay requires an existing Exp1 pilot suite")
    suite_root.mkdir(parents=True, exist_ok=False)
    _write_json(plan_path, plan_body)
    _write_jsonl(
        suite_root / "conditions.jsonl",
        [_json_copy(item) for item in plan_body["conditions"]],
    )
    return {"runs": [], "tasks": [], "attempts": [], "events": [], "artifacts": []}


def _next_task_limit_reason(
    *,
    evidence: dict[str, list[JsonObject]],
    task: JsonObject,
    hard_limits: JsonObject,
) -> str | None:
    used_provider_attempts = sum(
        int(item.get("provider_attempt_count", 0)) for item in evidence["tasks"]
    )
    used_tokens = sum(int(item.get("total_tokens", 0)) for item in evidence["tasks"])
    used_cost = sum(float(item.get("cost_estimate", 0.0)) for item in evidence["tasks"])
    if (
        used_provider_attempts + int(task["provider_attempt_upper_bound"])
        > int(hard_limits["provider_attempt_limit"])
    ):
        return "provider_attempt_limit"
    if (
        used_tokens + int(task["token_upper_bound"])
        > int(hard_limits["token_limit"])
    ):
        return "token_limit"
    if (
        used_cost + float(task["cost_upper_bound"])
        > float(hard_limits["cost_limit"]) + 1e-12
    ):
        return "cost_limit"
    return None


def _observed_profile_violation(
    *,
    task: JsonObject,
    task_result: JsonObject,
) -> str | None:
    if int(task_result["provider_attempt_count"]) > int(
        task["provider_attempt_upper_bound"]
    ):
        return "observed_provider_attempts_exceeded_approved_profile"
    total_tokens = task_result["total_tokens"]
    if total_tokens is None:
        return "observed_token_usage_missing"
    if int(total_tokens) > int(task["token_upper_bound"]):
        return "observed_tokens_exceeded_approved_profile"
    cost_estimate = task_result["cost_estimate"]
    if cost_estimate is None:
        return "observed_cost_usage_missing"
    if float(cost_estimate) > float(task["cost_upper_bound"]) + 1e-12:
        return "observed_cost_exceeded_approved_profile"
    return None


def _blocked_task_result(
    *,
    condition: PaperExperimentCondition,
    task: JsonObject,
    root_status: str,
    failure_kind: str | None,
) -> JsonObject:
    return {
        "schema_version": "tokenshare.paper_task_result.v1",
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task["task_id"],
        "domain": condition.domain,
        "difficulty": condition.difficulty,
        "paper_difficulty": condition.paper_difficulty,
        "topic_family": condition.topic_family,
        "topic_family_version": condition.topic_family_version,
        "construction_rule_id": condition.construction_rule_id,
        "oracle_package_group": condition.oracle_package_group,
        "proof_assembly_shape": condition.proof_assembly_shape,
        "root_status": root_status,
        "accepted_validity": False,
        "failure_stage": None,
        "failure_kind": failure_kind,
        "attempt_count": 0,
        "provider_attempt_count": 0,
        "wall_clock_ms": 0,
        "total_tokens": 0,
        "cost_estimate": 0.0,
        "event_refs": [],
        "artifact_refs": [],
        "paper_eligible": False,
    }


def _run_result_from_task(
    *,
    condition: PaperExperimentCondition,
    task: JsonObject,
    task_result: JsonObject,
    suite_root: Path,
    status: str,
    ineligibility_reasons: list[str],
    adapter_output_root: str | None = None,
    transport_evidence: JsonObject | None = None,
) -> JsonObject:
    run_id = str(task["run_id"])
    return {
        "schema_version": "tokenshare.paper_run_result.v1",
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "run_id": run_id,
        "case_id": task["case_id"],
        "execution_status": task["execution_status"],
        "status": status,
        "run_manifest_ref": (
            {"path": f"runs/{run_id}/{task['case_id']}/run_manifest.json"}
            if adapter_output_root
            else None
        ),
        "per_task_results_ref": {"path": "per_task_results.jsonl"},
        "per_attempt_results_ref": {"path": "per_attempt_results.jsonl"},
        "fault_injections_ref": None,
        "event_log_ref": {"path": "events/event_log.jsonl"},
        "artifact_root": (
            adapter_output_root
            if adapter_output_root
            else (suite_root / "runs" / run_id).as_posix()
        ),
        "transport_evidence": _json_copy(transport_evidence or {}),
        "paper_eligible": bool(task_result.get("paper_eligible", False)),
        "ineligibility_reasons": list(dict.fromkeys(ineligibility_reasons)),
    }


def _resolve_selected_api_key(
    *,
    pilot_profile: Exp1PilotProfile,
    baseline_entry_id: str,
) -> str:
    matches = [
        entry
        for entry in pilot_profile.source_provider_config.entries
        if entry.entry_id == baseline_entry_id and entry.enabled
    ]
    if len(matches) != 1:
        raise ValueError("Exp1 pilot selected provider entry is missing or disabled")
    try:
        return matches[0].resolve_api_key()
    except ValueError as exc:
        raise ValueError(
            f"Exp1 pilot API key environment is missing: {matches[0].api_key_env}"
        ) from exc


def _finalize_exp1_pilot_result(
    *,
    result: Exp1PilotExecutionResult,
    suite_root: Path,
    secret_values: tuple[str, ...],
    write_report: bool,
) -> Exp1PilotExecutionResult:
    manifest = result.to_dict()
    _write_json(suite_root / "suite_manifest.json", manifest)
    if not write_report:
        return result

    from tokenshare.experiments.paper_report import write_exp1_pilot_report

    report = write_exp1_pilot_report(
        suite_root,
        secret_values=secret_values,
    )
    if result.paper_eligible and not report.paper_eligible:
        raise ValueError("Exp1 pilot report audit rejected execution evidence")
    effective_result = (
        result
        if result.paper_eligible == report.paper_eligible
        else replace(result, paper_eligible=report.paper_eligible)
    )
    manifest = effective_result.to_dict()
    relative_paths = [
        Path(path).relative_to(suite_root).as_posix()
        for path in report.output_paths
    ]
    manifest["metrics_refs"] = [
        {"path": path}
        for path in relative_paths
        if path.startswith("metrics/")
    ]
    manifest["audit_refs"] = [
        {"path": path}
        for path in relative_paths
        if path.startswith("audit/")
    ]
    attestation_path = suite_root / "audit" / "suite_secret_scan_attestation.json"
    if attestation_path.is_file():
        manifest["audit_refs"].append(
            {"path": "audit/suite_secret_scan_attestation.json"}
        )
    manifest["report_ref"] = {"path": "report.md"}
    _write_json(suite_root / "suite_manifest.json", manifest)
    return effective_result


def _run_status_from_root_status(root_status: str) -> str:
    if root_status == "completed":
        return "completed"
    if root_status == "blocked":
        return "blocked"
    if root_status == "budget_exhausted":
        return "budget_exhausted"
    return "failed"


def _artifact_index_records(
    *,
    run_id: str,
    task_result: JsonObject,
    attempts: list[JsonObject],
) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for ref in task_result.get("artifact_refs", ()):
        if isinstance(ref, dict):
            refs.append(ref)
    for attempt in attempts:
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "parse_failure_ref",
            "provenance_ref",
            "usage_ref",
            "model_execution_record_ref",
        ):
            ref = attempt.get(field_name)
            if isinstance(ref, dict):
                refs.append(ref)
    records: list[JsonObject] = []
    seen: set[str] = set()
    for ref in refs:
        key = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "schema_version": "tokenshare.paper_artifact_index_record.v1",
                "run_id": run_id,
                "task_id": task_result["task_id"],
                "artifact_ref": _json_copy(ref),
            }
        )
    return records


def _orchestrator_event(
    *,
    event_id: str,
    event_type: str,
    suite_id: str,
    task: JsonObject | None = None,
    detail: JsonObject | None = None,
) -> JsonObject:
    return {
        "schema_version": "tokenshare.paper_orchestrator_event.v1",
        "event_id": event_id,
        "event_type": event_type,
        "suite_id": suite_id,
        "run_id": task.get("run_id") if task is not None else None,
        "task_id": task.get("task_id") if task is not None else None,
        "case_id": task.get("case_id") if task is not None else None,
        "ordinal": task.get("ordinal") if task is not None else None,
        "detail": _json_copy(detail or {}),
        "recorded_at": _utc_now(),
    }


def _sort_execution_evidence(
    *,
    evidence: dict[str, list[JsonObject]],
    plan: Exp1PilotExecutionPlan,
) -> None:
    run_order = {str(task["run_id"]): index for index, task in enumerate(plan.tasks)}
    condition_order = {
        str(task["condition_id"]): index for index, task in enumerate(plan.tasks)
    }
    evidence["runs"].sort(key=lambda item: run_order[str(item["run_id"])])
    evidence["tasks"].sort(
        key=lambda item: condition_order[str(item["condition_id"])]
    )


def _persist_execution_evidence(
    *,
    suite_root: Path,
    evidence: dict[str, list[JsonObject]],
) -> None:
    _write_jsonl(suite_root / "run_results.jsonl", evidence["runs"])
    _write_jsonl(suite_root / "per_task_results.jsonl", evidence["tasks"])
    _write_jsonl(suite_root / "per_attempt_results.jsonl", evidence["attempts"])
    _write_jsonl(suite_root / "events" / "event_log.jsonl", evidence["events"])
    _write_jsonl(
        suite_root / "artifacts" / "artifact_index.jsonl", evidence["artifacts"]
    )


def _checkpoint_execution_evidence(
    *,
    suite_root: Path,
    evidence: dict[str, list[JsonObject]],
    plan: Exp1PilotExecutionPlan,
) -> None:
    _sort_execution_evidence(evidence=evidence, plan=plan)
    execution_plan = _read_json(suite_root / "execution_plan.json")
    _validate_execution_evidence_consistency(
        suite_root=suite_root,
        execution_plan=execution_plan,
        evidence=evidence,
    )
    _persist_execution_evidence(suite_root=suite_root, evidence=evidence)
    _write_evidence_manifest(
        suite_root=suite_root,
        execution_plan=execution_plan,
        evidence=evidence,
    )


def _write_evidence_manifest(
    *,
    suite_root: Path,
    execution_plan: JsonObject,
    evidence: dict[str, list[JsonObject]],
) -> None:
    _write_json(
        suite_root / "evidence_manifest.json",
        _expected_evidence_manifest(
            suite_root=suite_root,
            execution_plan=execution_plan,
            evidence=evidence,
        ),
    )


def _validate_evidence_manifest(
    *,
    suite_root: Path,
    execution_plan: JsonObject,
    evidence: dict[str, list[JsonObject]],
) -> None:
    manifest_path = suite_root / "evidence_manifest.json"
    if not manifest_path.exists():
        raise ValueError("Exp1 pilot evidence manifest is missing")
    actual = _read_json(manifest_path)
    stored_digest = actual.get("evidence_manifest_digest")
    actual_without_digest = {
        key: value for key, value in actual.items() if key != "evidence_manifest_digest"
    }
    if stored_digest != digest_json(actual_without_digest):
        raise ValueError("Exp1 pilot evidence manifest digest is invalid")
    expected = _expected_evidence_manifest(
        suite_root=suite_root,
        execution_plan=execution_plan,
        evidence=evidence,
    )
    if actual != expected:
        raise ValueError("Exp1 pilot evidence manifest mismatch")


def _expected_evidence_manifest(
    *,
    suite_root: Path,
    execution_plan: JsonObject,
    evidence: dict[str, list[JsonObject]],
) -> JsonObject:
    conditions = _read_jsonl(suite_root / "conditions.jsonl")
    files: JsonObject = {
        "execution_plan.json": _evidence_file_record([execution_plan]),
        "conditions.jsonl": _evidence_file_record(conditions),
        "run_results.jsonl": _evidence_file_record(evidence["runs"]),
        "per_task_results.jsonl": _evidence_file_record(evidence["tasks"]),
        "per_attempt_results.jsonl": _evidence_file_record(evidence["attempts"]),
        "events/event_log.jsonl": _evidence_file_record(evidence["events"]),
        "artifacts/artifact_index.jsonl": _evidence_file_record(
            evidence["artifacts"]
        ),
    }
    body: JsonObject = {
        "schema_version": "tokenshare.paper_execution_evidence_manifest.v1",
        "execution_plan_digest": execution_plan["execution_plan_digest"],
        "files": files,
    }
    body["evidence_manifest_digest"] = digest_json(body)
    return body


def _evidence_file_record(records: list[JsonObject]) -> JsonObject:
    return {
        "record_count": len(records),
        "records_digest": digest_json(records),
    }


def _validate_execution_evidence_consistency(
    *,
    suite_root: Path,
    execution_plan: JsonObject,
    evidence: dict[str, list[JsonObject]],
) -> None:
    conditions = _read_jsonl(suite_root / "conditions.jsonl")
    if conditions != execution_plan.get("conditions"):
        raise ValueError("Exp1 pilot conditions evidence does not match execution plan")
    _require_unique_records(
        records=conditions,
        field_names=("condition_id",),
        label="condition",
    )
    _require_unique_records(
        records=evidence["runs"],
        field_names=("run_id",),
        label="run",
    )
    _require_unique_records(
        records=evidence["tasks"],
        field_names=("condition_id",),
        label="task",
    )
    _require_unique_records(
        records=evidence["attempts"],
        field_names=("condition_id", "attempt_id"),
        label="attempt",
    )
    _require_unique_records(
        records=evidence["events"],
        field_names=("event_id",),
        label="event",
    )

    planned_runs = {
        str(item["run_id"]): item for item in execution_plan.get("tasks", ())
    }
    planned_conditions = {
        str(item["condition_id"]): item
        for item in execution_plan.get("tasks", ())
    }
    runs_by_id = {str(item["run_id"]): item for item in evidence["runs"]}
    tasks_by_condition = {
        str(item["condition_id"]): item for item in evidence["tasks"]
    }
    if not set(runs_by_id).issubset(planned_runs):
        raise ValueError("Exp1 pilot run evidence contains an unplanned run")
    if not set(tasks_by_condition).issubset(planned_conditions):
        raise ValueError("Exp1 pilot task evidence contains an unplanned condition")
    for run in evidence["runs"]:
        condition_id = str(run["condition_id"])
        task = tasks_by_condition.get(condition_id)
        if task is None:
            raise ValueError("Exp1 pilot run evidence is missing its task result")
        planned_run = planned_runs[str(run["run_id"])]
        if condition_id != str(planned_run["condition_id"]):
            raise ValueError("Exp1 pilot run/condition evidence mismatch")
        manifest_ref = run.get("run_manifest_ref")
        if isinstance(manifest_ref, dict) and isinstance(manifest_ref.get("path"), str):
            if not (suite_root / str(manifest_ref["path"])).is_file():
                raise ValueError("Exp1 pilot run manifest reference is missing")

    attempts_by_condition: dict[str, list[JsonObject]] = {}
    for attempt in evidence["attempts"]:
        condition_id = str(attempt["condition_id"])
        if condition_id not in planned_conditions:
            raise ValueError("Exp1 pilot attempt evidence contains an unplanned condition")
        attempts_by_condition.setdefault(condition_id, []).append(attempt)
        _require_non_negative_int_metric(attempt, "total_tokens", "attempt")
        _require_finite_non_negative_metric(attempt, "cost_estimate", "attempt")
    for condition_id, task in tasks_by_condition.items():
        attempts = attempts_by_condition.get(condition_id, [])
        _require_non_negative_int_metric(task, "attempt_count", "task")
        _require_non_negative_int_metric(task, "provider_attempt_count", "task")
        _require_non_negative_int_metric(task, "total_tokens", "task")
        _require_finite_non_negative_metric(task, "cost_estimate", "task")
        if int(task["attempt_count"]) != len(attempts):
            raise ValueError("Exp1 pilot task/attempt count mismatch")
        if int(task["provider_attempt_count"]) != len(attempts):
            raise ValueError("Exp1 pilot task/provider-attempt count mismatch")
        if int(task["total_tokens"]) != sum(
            int(item["total_tokens"]) for item in attempts
        ):
            raise ValueError("Exp1 pilot task/attempt token total mismatch")
        if not math.isclose(
            float(task["cost_estimate"]),
            sum(float(item["cost_estimate"]) for item in attempts),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("Exp1 pilot task/attempt cost total mismatch")

    indexed_refs: set[tuple[str, str]] = set()
    for record in evidence["artifacts"]:
        run_id = str(record.get("run_id"))
        ref = record.get("artifact_ref")
        if run_id not in runs_by_id or not isinstance(ref, dict):
            raise ValueError("Exp1 pilot artifact index contains an invalid record")
        key = (run_id, _canonical_json(ref))
        if key in indexed_refs:
            raise ValueError("Exp1 pilot artifact index contains a duplicate record")
        indexed_refs.add(key)
        uri = ref.get("uri")
        if isinstance(uri, str) and uri:
            artifact_root = resolve_persisted_data_path(
                str(runs_by_id[run_id]["artifact_root"]),
                relative_to=suite_root,
            )
            try:
                artifact_ref = ArtifactRef.from_dict(ref)
                verified = ArtifactStore(artifact_root).verify(artifact_ref)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Exp1 pilot indexed artifact integrity metadata is invalid"
                ) from exc
            if not verified:
                raise ValueError(
                    "Exp1 pilot indexed artifact integrity verification failed"
                )
    condition_to_run = {
        str(run["condition_id"]): str(run["run_id"]) for run in evidence["runs"]
    }
    for condition_id, task in tasks_by_condition.items():
        run_id = condition_to_run.get(condition_id)
        if run_id is None:
            raise ValueError("Exp1 pilot task evidence is missing its run result")
        for ref in _result_artifact_refs(task, attempts_by_condition.get(condition_id, [])):
            if (run_id, _canonical_json(ref)) not in indexed_refs:
                raise ValueError("Exp1 pilot artifact reference is missing from index")


def _require_unique_records(
    *,
    records: list[JsonObject],
    field_names: tuple[str, ...],
    label: str,
) -> None:
    seen: set[tuple[str, ...]] = set()
    for record in records:
        key = tuple(str(record.get(field_name)) for field_name in field_names)
        if any(value in {"", "None"} for value in key) or key in seen:
            raise ValueError(f"Exp1 pilot {label} evidence is missing or duplicate")
        seen.add(key)


def _require_non_negative_int_metric(
    body: JsonObject,
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Exp1 pilot {label} {field_name} must be non-negative integer")


def _require_finite_non_negative_metric(
    body: JsonObject,
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"Exp1 pilot {label} {field_name} must be finite and non-negative")


def _result_artifact_refs(
    task: JsonObject,
    attempts: list[JsonObject],
) -> list[JsonObject]:
    refs: list[JsonObject] = []
    for ref in task.get("artifact_refs", ()):
        if isinstance(ref, dict):
            refs.append(ref)
    for attempt in attempts:
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "parse_failure_ref",
            "provenance_ref",
            "usage_ref",
            "model_execution_record_ref",
        ):
            ref = attempt.get(field_name)
            if isinstance(ref, dict):
                refs.append(ref)
    return refs


def _canonical_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _execution_result_from_evidence(
    *,
    plan: Exp1PilotExecutionPlan,
    suite_root: Path,
    hard_limits: JsonObject,
    evidence: dict[str, list[JsonObject]],
    provider_calls_made: int,
    replayed_run_count: int,
    stop_reason: str | None,
    started_at: str | None = None,
) -> Exp1PilotExecutionResult:
    root_statuses = [str(item.get("root_status")) for item in evidence["tasks"]]
    if stop_reason is not None or "budget_exhausted" in root_statuses:
        status = PaperStatus.BUDGET_EXHAUSTED.value
    elif any(status not in {"completed", "blocked"} for status in root_statuses):
        status = PaperStatus.COMPLETED_WITH_FAILURES.value
    elif "blocked" in root_statuses:
        status = PaperStatus.COMPLETED_WITH_FAILURES.value
    else:
        status = PaperStatus.COMPLETED.value
    executable_tasks = [
        item
        for item in evidence["tasks"]
        if item.get("root_status") not in {"blocked", "budget_exhausted"}
    ]
    planned_executable_count = sum(
        task["execution_status"] == "executable" for task in plan.tasks
    )
    complete_executable_evidence = (
        len(executable_tasks) == planned_executable_count
        and "budget_exhausted" not in root_statuses
    )
    return Exp1PilotExecutionResult(
        suite_id=plan.suite_id,
        status=status,
        output_root=suite_root.as_posix(),
        budget_digest=plan.budget_digest,
        profile_digest=plan.profile_digest,
        catalog_digest=plan.catalog_digest,
        condition_count=len(plan.conditions),
        run_count=len(evidence["runs"]),
        task_count=len(evidence["tasks"]),
        blocked_run_count=sum(status == "blocked" for status in root_statuses),
        provider_attempt_count=sum(
            int(item.get("provider_attempt_count", 0)) for item in evidence["tasks"]
        ),
        provider_calls_made=provider_calls_made,
        total_tokens=sum(
            int(item.get("total_tokens", 0)) for item in evidence["tasks"]
        ),
        total_cost_estimate=round(
            sum(float(item.get("cost_estimate", 0.0)) for item in evidence["tasks"]),
            12,
        ),
        replayed_run_count=replayed_run_count,
        paper_eligible=stop_reason is None
        and complete_executable_evidence
        and bool(executable_tasks)
        and all(bool(item.get("paper_eligible")) for item in executable_tasks),
        stop_reason=stop_reason,
        hard_limits=_json_copy(hard_limits),
        started_at=started_at or _existing_started_at(evidence["events"]),
        ended_at=_utc_now(),
    )


def _existing_started_at(events: list[JsonObject]) -> str:
    event = _unique_suite_lifecycle_event(events, "suite_started")
    value = event.get("recorded_at")
    if not isinstance(value, str) or not value:
        raise ValueError("Exp1 pilot suite_started evidence is missing recorded_at")
    return value


def _existing_stop_reason(events: list[JsonObject]) -> str | None:
    event = _unique_suite_lifecycle_event(events, "suite_finished")
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise ValueError("Exp1 pilot suite_finished evidence is missing detail")
    value = detail.get("stop_reason")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("Exp1 pilot suite_finished stop_reason is invalid")
    return value


def _unique_suite_lifecycle_event(
    events: list[JsonObject], event_type: str
) -> JsonObject:
    matches = [event for event in events if event.get("event_type") == event_type]
    if len(matches) != 1:
        raise ValueError(
            f"Exp1 pilot evidence requires exactly one {event_type} event"
        )
    return matches[0]


def _read_json(path: Path) -> JsonObject:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"expected JSON object: {path}")
    return body


def _read_jsonl(path: Path) -> list[JsonObject]:
    if not path.exists():
        return []
    records: list[JsonObject] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        body = json.loads(line)
        if not isinstance(body, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        records.append(body)
    return records


def _write_json(path: Path, body: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            body,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, records: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
