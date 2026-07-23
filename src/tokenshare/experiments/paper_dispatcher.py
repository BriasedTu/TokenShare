"""Gate C 论文实验模块与领域 adapter 的共享 dispatcher。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tokenshare.experiments.factorization_paper_adapter import (
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_paper_adapter import run_lean_paper_case
from tokenshare.experiments.paper_exp1 import (
    EXP1_FORMAL_EXPERIMENT_ID,
    Exp1FormalModule,
)
from tokenshare.experiments.paper_exp2_scalability import (
    EXP2_EXPERIMENT_ID,
    Experiment2ScalabilityModule,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    EXP3_EXPERIMENT_ID,
    Experiment3FaultRecoveryModule,
)
from tokenshare.experiments.paper_exp4_ablation_runner import (
    EXP4_EXPERIMENT_ID,
    Experiment4AblationModule,
)
from tokenshare.experiments.paper_exp5_model_comparison import (
    EXP5_EXPERIMENT_ID,
    EXP5_INCOMPLETE_COHORT_REASON,
    Experiment5ModelComparisonModule,
)
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelectionBatch,
    FrozenCaseSelection,
    FrozenConditionSelectionBinding,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
)
from tokenshare.local_runtime import ProtocolRunRequest, ProtocolRunResult


_MODULES: tuple[tuple[str, PaperExperimentModule], ...] = (
    (EXP1_FORMAL_EXPERIMENT_ID, Exp1FormalModule()),
    (EXP2_EXPERIMENT_ID, Experiment2ScalabilityModule()),
    (EXP3_EXPERIMENT_ID, Experiment3FaultRecoveryModule()),
    (EXP4_EXPERIMENT_ID, Experiment4AblationModule()),
    (EXP5_EXPERIMENT_ID, Experiment5ModelComparisonModule()),
)


@dataclass(frozen=True, kw_only=True)
class PaperExperimentDispatchPlan:
    experiment_id: str
    output_root: str
    conditions: tuple[PaperExperimentCondition, ...]
    condition_selection_bindings: tuple[FrozenConditionSelectionBinding, ...]
    status: str = "planned"
    blocked_reason: str | None = None
    paper_eligible_possible: bool = True
    provider_calls_made: int = 0
    schema_version: str = "tokenshare.paper_experiment_dispatch_plan.v2"

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("experiment_id is required")
        if not self.output_root:
            raise ValueError("output_root is required")
        if self.status not in {"planned", "blocked"}:
            raise ValueError("dispatch status must be planned or blocked")
        if self.status == "blocked":
            if self.conditions or self.condition_selection_bindings:
                raise ValueError("blocked dispatch plan must not contain work")
            if not self.blocked_reason:
                raise ValueError("blocked dispatch plan requires blocked_reason")
            if self.paper_eligible_possible:
                raise ValueError("blocked dispatch plan cannot be paper eligible")
        elif self.blocked_reason is not None:
            raise ValueError("planned dispatch plan must not declare blocked_reason")
        if self.provider_calls_made != 0:
            raise ValueError("Gate C dispatch planning must not call providers")
        conditions_by_id: dict[str, PaperExperimentCondition] = {}
        condition_digests: set[str] = set()
        for condition in self.conditions:
            if condition.experiment_id != self.experiment_id:
                raise ValueError("dispatch condition experiment_id mismatch")
            if condition.condition_id in conditions_by_id:
                raise ValueError("duplicate condition_id in dispatch plan")
            if condition.condition_digest in condition_digests:
                raise ValueError("duplicate condition_digest in dispatch plan")
            conditions_by_id[condition.condition_id] = condition
            condition_digests.add(condition.condition_digest)

        bindings_by_id: dict[str, FrozenConditionSelectionBinding] = {}
        for binding in self.condition_selection_bindings:
            if binding.condition_id in bindings_by_id:
                raise ValueError("duplicate condition selection binding")
            condition = conditions_by_id.get(binding.condition_id)
            if condition is None:
                raise ValueError("extra condition selection binding")
            if binding.condition_digest != condition.condition_digest:
                raise ValueError("condition selection binding condition digest mismatch")
            _validate_bound_selection(
                experiment_id=self.experiment_id,
                condition=condition,
                selection=binding.selection,
            )
            bindings_by_id[binding.condition_id] = binding

        if set(conditions_by_id).difference(bindings_by_id):
            raise ValueError("missing condition selection binding")

    @property
    def selections(self) -> tuple[FrozenCaseSelection, ...]:
        """按完整 condition identity 提供旧只读 selection 视图。"""

        return tuple(
            self.bound_condition(condition.condition_id)[1]
            for condition in self.conditions
        )

    def bound_condition(
        self,
        condition_id: str,
    ) -> tuple[PaperExperimentCondition, FrozenCaseSelection]:
        conditions = tuple(
            condition
            for condition in self.conditions
            if condition.condition_id == condition_id
        )
        bindings = tuple(
            binding
            for binding in self.condition_selection_bindings
            if binding.condition_id == condition_id
        )
        if len(conditions) != 1 or len(bindings) != 1:
            raise ValueError(
                f"condition is not present exactly once in dispatch plan: {condition_id}"
            )
        condition = conditions[0]
        binding = bindings[0]
        if binding.condition_digest != condition.condition_digest:
            raise ValueError("condition selection binding condition digest mismatch")
        return condition, binding.selection

    def bound_items(
        self,
    ) -> tuple[tuple[PaperExperimentCondition, FrozenCaseSelection], ...]:
        return tuple(
            self.bound_condition(condition.condition_id)
            for condition in self.conditions
        )

    def to_dict(self) -> dict[str, Any]:
        ordered_bindings = tuple(
            next(
                binding
                for binding in self.condition_selection_bindings
                if binding.condition_id == condition.condition_id
            )
            for condition in self.conditions
        )
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "output_root": self.output_root,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "paper_eligible_possible": self.paper_eligible_possible,
            "provider_calls_made": self.provider_calls_made,
            "condition_count": len(self.conditions),
            "selection_count": len(self.condition_selection_bindings),
            "condition_selection_binding_count": len(ordered_bindings),
            "conditions": [condition.to_dict() for condition in self.conditions],
            "selections": [selection.to_dict() for selection in self.selections],
            "condition_selection_bindings": [
                binding.to_dict() for binding in ordered_bindings
            ],
        }


def registered_paper_experiment_ids() -> tuple[str, ...]:
    """返回确定性的 Gate C 模块加载顺序。"""

    return tuple(experiment_id for experiment_id, _module in _MODULES)


def load_paper_experiment_module(experiment_id: str) -> PaperExperimentModule:
    """加载一个固定实验模块，不提供 synthetic fallback。"""

    for registered_id, module in _MODULES:
        if registered_id != experiment_id:
            continue
        if not isinstance(module, PaperExperimentModule):
            raise RuntimeError(
                f"paper experiment module does not satisfy Protocol: {experiment_id}"
            )
        return module
    raise ValueError(f"unsupported paper experiment: {experiment_id}")


def plan_paper_experiment(
    *,
    context: PaperExecutionContext,
    experiment_id: str,
) -> PaperExperimentDispatchPlan:
    """把 condition expansion 和 selection freeze 委托给已注册模块。"""

    module = load_paper_experiment_module(experiment_id)
    conditions = tuple(module.expand_conditions(context))
    frozen = module.freeze_case_selections(context, conditions)
    if not isinstance(frozen, FrozenCaseSelectionBatch):
        raise ValueError(
            "paper experiment module must return explicit condition selection bindings"
        )
    condition_selection_bindings = frozen.condition_selection_bindings
    status = "planned"
    blocked_reason = None
    paper_eligible_possible = True
    if not conditions:
        binding = context.approved_endpoint_binding
        if (
            experiment_id != EXP5_EXPERIMENT_ID
            or not isinstance(binding, Mapping)
            or binding.get("status") != "blocked"
        ):
            raise ValueError(
                "paper experiment produced no conditions without a blocked preflight"
            )
        status = "blocked"
        blocked_reason = str(
            binding.get("blocked_reason") or EXP5_INCOMPLETE_COHORT_REASON
        )
        paper_eligible_possible = False
    return PaperExperimentDispatchPlan(
        experiment_id=experiment_id,
        output_root=context.output_root,
        conditions=conditions,
        condition_selection_bindings=condition_selection_bindings,
        status=status,
        blocked_reason=blocked_reason,
        paper_eligible_possible=paper_eligible_possible,
    )


def dispatch_paper_condition(
    *,
    context: PaperExecutionContext,
    plan: PaperExperimentDispatchPlan,
    condition_id: str,
) -> PaperConditionResult:
    """通过生成计划的同一注册模块运行一个 condition。"""

    if context.output_root != plan.output_root:
        raise ValueError("dispatch output_root drift")
    module = load_paper_experiment_module(plan.experiment_id)
    condition, selection = plan.bound_condition(condition_id)
    return module.run_condition(context, condition, selection)


def dispatch_paper_case(
    *,
    case: dict[str, Any],
    condition: PaperExperimentCondition,
    output_root: str,
    transport: Any | None,
    real_transport: bool,
    ai_api_config: Any | None,
    entry_id: str | None,
    max_tokens: int,
    timeout_seconds: int,
    selected_ai_unit_id: str | None = None,
    post_raw_output_hook: Any | None = None,
    ablation_mode: str | None = None,
    checker: Any | None = None,
) -> Any:
    """把一个冻结 paper case 路由到所属插件的 adapter。"""

    adapter_kwargs = dict(
        case=case,
        condition=condition,
        output_root=output_root,
        transport=transport,
        real_transport=real_transport,
        ai_api_config=ai_api_config,
        entry_id=entry_id,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        selected_ai_unit_id=selected_ai_unit_id,
        post_raw_output_hook=post_raw_output_hook,
        ablation_mode=ablation_mode,
        protocol_run_dispatcher=execute_protocol_request,
    )
    if condition.domain == "factorization":
        if checker is not None:
            raise ValueError("Factorization paper dispatch does not accept a Lean checker")
        return run_factorization_paper_case(**adapter_kwargs)
    if checker is None:
        return run_lean_paper_case(**adapter_kwargs)
    return run_lean_paper_case(**adapter_kwargs, checker=checker)


def execute_protocol_request(
    *,
    coordinator: Any,
    request: ProtocolRunRequest,
) -> ProtocolRunResult:
    """校验共享 request 边界，并由 dispatcher 调用系统 coordinator。"""

    if not isinstance(request, ProtocolRunRequest):
        raise TypeError("paper dispatcher requires ProtocolRunRequest")
    run_root = getattr(coordinator, "run_root", None)
    if not callable(run_root):
        raise TypeError("paper dispatcher requires ProtocolRunCoordinator-compatible object")
    result = run_root(request)
    if not isinstance(result, ProtocolRunResult):
        raise TypeError("paper coordinator must return ProtocolRunResult")
    return result


def _validate_bound_selection(
    *,
    experiment_id: str,
    condition: PaperExperimentCondition,
    selection: FrozenCaseSelection,
) -> None:
    if selection.experiment_id != experiment_id:
        raise ValueError("dispatch selection experiment_id mismatch")
    if (
        selection.domain != condition.domain
        or selection.paper_difficulty != condition.paper_difficulty
        or selection.topic_family != condition.topic_family
        or selection.catalog_digest != condition.catalog_digest
    ):
        raise ValueError("selection does not match bound condition")
