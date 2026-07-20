"""正式论文实验套件的最小通用调度层。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import shutil
from threading import Lock
from typing import Any

from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.experiments.paper_catalog import estimated_ai_units_for_case
from tokenshare.experiments.paper_dispatcher import (
    PaperExperimentDispatchPlan,
    dispatch_paper_case,
    dispatch_paper_condition,
)
from tokenshare.experiments.paper_experiment_contracts import PaperExecutionContext
from tokenshare.experiments.paper_formal_evidence import FormalEvidenceStore
from tokenshare.experiments.paper_formal_callbacks import (
    run_exp3_post_ai_strategy,
    run_exp3_worker_death_strategy,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    inject_exp3_post_ai_fault,
)
from tokenshare.experiments.paper_exp2_scalability import EXP2_EXPERIMENT_ID
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperBudgetResult,
    PaperConditionResult,
    PaperStatus,
    PaperSuiteResult,
)
from tokenshare.storage.artifacts import ArtifactStore


EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
APPROVED_ENDPOINT_BINDINGS_KEY = "__approved_endpoint_bindings__"


def replay_paper_formal_suite(*, output_root: str | Path) -> PaperSuiteResult:
    """只从历史 frozen identity 和 runner result 重放，不读取当前 config。"""

    suite_root = Path(output_root)
    bodies = _stored_evidence_bodies(suite_root)
    FormalEvidenceStore.load(
        output_root=suite_root,
        **{f"expected_{name}": body for name, body in bodies.items()},
    )
    return _suite_result_from_evidence(suite_root)


def execute_paper_formal_suite(
    *,
    dispatch_plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    output_root: str | Path,
    ai_api_configs: Mapping[str, Any],
    transport: Any,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    resume: bool = False,
    replay_only: bool = False,
) -> PaperSuiteResult:
    """校验冻结计划并通过注册 dispatcher 顺序执行 planned conditions。"""

    plans = tuple(dispatch_plans)
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if replay_only:
        return replay_paper_formal_suite(output_root=output_root)
    bound_plans = _validate_suite_inputs(
        dispatch_plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        budget_approval=budget_approval,
        output_root=output_root,
        ai_api_configs=ai_api_configs,
        hard_limits=hard_limits,
        resume=resume,
        replay_only=replay_only,
    )
    suite_root = Path(output_root)
    started_at = _utc_now()
    bodies = _evidence_bodies(
        plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        hard_limits=hard_limits,
        ai_api_configs=ai_api_configs,
    )
    if resume:
        loaded = FormalEvidenceStore.load(
            output_root=suite_root,
            **{f"expected_{name}": body for name, body in bodies.items()},
        )
        completed_task_keys = set(loaded.completed_task_keys)
        if _all_selected_roots_completed(bound_plans, completed_task_keys):
            return _suite_result_from_evidence(suite_root)
        evidence_store = FormalEvidenceStore(suite_root)
        usage = _usage_from_evidence(suite_root)
        if _hard_limit_reached(usage, hard_limits):
            return _suite_result_from_evidence(suite_root)
    else:
        evidence_store = FormalEvidenceStore.initialize(
            output_root=suite_root,
            **bodies,
            capturing=(not real_transport or _is_offline_capturing_transport(transport)),
        )
        completed_task_keys = set()
        usage = _UsageTotals()
    results: list[PaperConditionResult] = []

    for plan, bound_items in bound_plans:
        if plan.status == "blocked":
            continue
        plan_root = Path(plan.output_root)
        plan_root.mkdir(parents=True, exist_ok=True)
        for condition, selection in bound_items:
            endpoint_binding, request_limits, config = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            context = PaperExecutionContext(
                context_id=(
                    f"paper_formal_{plan.experiment_id}_{condition.condition_id}"
                ),
                catalog=catalog_manifest,
                approved_endpoint_binding=endpoint_binding,
                request_limits=request_limits,
                hard_limits=dict(hard_limits),
                output_root=plan_root.as_posix(),
                artifact_store=object(),
                event_store=object(),
                execution_callback=_FormalConditionExecutionCallback(
                    catalog_manifest=catalog_manifest,
                    config=config,
                    transport=transport,
                    real_transport=real_transport,
                    output_root=plan_root,
                    request_limits=request_limits,
                    evidence_store=evidence_store,
                    completed_task_keys=completed_task_keys,
                    usage=usage,
                    budget=budget,
                    hard_limits=hard_limits,
                ),
            )
            result = dispatch_paper_condition(
                context=context,
                plan=plan,
                condition_id=condition.condition_id,
            )
            if not isinstance(result, PaperConditionResult):
                raise ValueError("dispatcher must return PaperConditionResult")
            if result.condition_id != condition.condition_id:
                raise ValueError("dispatcher returned a mismatched condition_id")
            results.append(result)

    suite_result = PaperSuiteResult(
        suite_id="paper_formal_suite",
        status=_suite_status(plans=plans, results=results),
        output_root=suite_root.as_posix(),
        started_at=started_at,
        ended_at=_utc_now(),
        experiment_ids=tuple(plan.experiment_id for plan in plans),
        condition_count=sum(len(items) for _plan, items in bound_plans),
        run_count=sum(result.repeat_count for result in results),
        task_count=sum(result.task_count for result in results),
        provider_attempt_count=max(
            usage.provider_attempt_count,
            sum(result.provider_attempt_count for result in results),
        ),
        total_tokens=usage.total_tokens,
        total_cost_estimate=usage.total_cost_estimate,
        paper_eligible=_suite_paper_eligible(
            plans=plans,
            results=results,
            transport=transport,
            real_transport=real_transport,
        ),
        eligibility_report_ref=None,
        budget_ref={
            "budget_digest": budget.budget_digest,
            "approval_mode": budget_approval["approval_mode"],
        },
        metrics_refs=(),
        audit_refs=(),
        error_summary=(),
    )
    _write_json(suite_root / "formal_runner_result.json", suite_result.to_dict())
    _finalize_formal_manifests(
        suite_root=suite_root,
        suite_result=suite_result,
        plans=plans,
        condition_results=results,
    )
    # runner 结果同样属于可 replay 的 suite evidence，写入后刷新索引。
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    return suite_result


def _validate_suite_inputs(
    *,
    dispatch_plans: tuple[PaperExperimentDispatchPlan, ...],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    output_root: str | Path,
    ai_api_configs: Mapping[str, Any],
    hard_limits: Mapping[str, Any],
    resume: bool,
    replay_only: bool,
) -> tuple[
    tuple[
        PaperExperimentDispatchPlan,
        tuple[tuple[Any, Any], ...],
    ],
    ...,
]:
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if not isinstance(budget, PaperBudgetResult):
        raise ValueError("budget must be PaperBudgetResult")
    if not isinstance(budget_approval, Mapping):
        raise ValueError("budget_approval must be a mapping")
    approval_mode = budget_approval.get("approval_mode")
    if not isinstance(approval_mode, str) or not approval_mode:
        raise ValueError("budget_approval approval_mode is required")
    approved_digest = budget_approval.get("budget_digest")
    if approved_digest != budget.budget_digest:
        raise ValueError("budget approval digest mismatch")
    if not isinstance(ai_api_configs, Mapping):
        raise ValueError("ai_api_configs must be a mapping")
    if not isinstance(hard_limits, Mapping):
        raise ValueError("hard_limits must be a mapping")

    catalog_digest = (
        catalog_manifest.get("catalog_digest")
        if isinstance(catalog_manifest, Mapping)
        else getattr(catalog_manifest, "catalog_digest", None)
    )
    if not isinstance(catalog_digest, str) or not catalog_digest:
        raise ValueError("catalog manifest digest is required")
    suite_root = Path(output_root).resolve(strict=False)
    seen_experiments: set[str] = set()
    bound_plans: list[
        tuple[PaperExperimentDispatchPlan, tuple[tuple[Any, Any], ...]]
    ] = []

    for plan in dispatch_plans:
        if not isinstance(plan, PaperExperimentDispatchPlan):
            raise ValueError("dispatch_plans must contain dispatch plans")
        if plan.experiment_id in seen_experiments:
            raise ValueError("duplicate dispatch plan experiment_id")
        seen_experiments.add(plan.experiment_id)
        expected_root = (suite_root / plan.experiment_id).resolve(strict=False)
        if Path(plan.output_root).resolve(strict=False) != expected_root:
            raise ValueError("dispatch plan output_root is not experiment isolated")
        bound_items = plan.bound_items()
        for condition, selection in bound_items:
            if condition.experiment_id != plan.experiment_id:
                raise ValueError("dispatch plan experiment_id mismatch")
            if (
                condition.catalog_digest != catalog_digest
                or selection.catalog_digest != catalog_digest
            ):
                raise ValueError("dispatch plan catalog digest mismatch")
            if plan.status == "planned" and not replay_only:
                _condition_endpoint_contract(
                    experiment_id=plan.experiment_id,
                    condition=condition,
                    ai_api_configs=ai_api_configs,
                )
        bound_plans.append((plan, bound_items))

    planned = tuple(plan for plan in dispatch_plans if plan.status == "planned")
    planned_items = tuple(
        item
        for plan, items in bound_plans
        if plan.status == "planned"
        for item in items
    )
    if tuple(budget.planned_experiments) != tuple(
        plan.experiment_id for plan in planned
    ):
        raise ValueError("budget planned experiments mismatch")
    if budget.planned_conditions != len(planned_items):
        raise ValueError("budget planned condition count mismatch")
    if budget.planned_root_runs != sum(
        len(selection.ordered_case_ids) for _condition, selection in planned_items
    ):
        raise ValueError("budget planned root run count mismatch")
    if budget.planned_ai_units != sum(
        selection.expected_ai_unit_count for _condition, selection in planned_items
    ):
        raise ValueError("budget planned AI unit count mismatch")
    return tuple(bound_plans)


def _suite_status(
    *,
    plans: tuple[PaperExperimentDispatchPlan, ...],
    results: list[PaperConditionResult],
) -> PaperStatus:
    if not results:
        return PaperStatus.BLOCKED if plans else PaperStatus.COMPLETED
    statuses = {_status_value(result.status) for result in results}
    if statuses == {PaperStatus.COMPLETED.value}:
        return (
            PaperStatus.COMPLETED_WITH_FAILURES
            if any(plan.status == "blocked" for plan in plans)
            else PaperStatus.COMPLETED
        )
    if PaperStatus.BUDGET_EXHAUSTED.value in statuses:
        return PaperStatus.BUDGET_EXHAUSTED
    if PaperStatus.FAILED.value in statuses:
        return PaperStatus.FAILED
    return PaperStatus.COMPLETED_WITH_FAILURES


def _suite_paper_eligible(
    *,
    plans: tuple[PaperExperimentDispatchPlan, ...],
    results: list[PaperConditionResult],
    transport: Any,
    real_transport: bool,
) -> bool:
    if not real_transport or _is_offline_capturing_transport(transport):
        return False
    if not results or any(not plan.paper_eligible_possible for plan in plans):
        return False
    return all(
        isinstance(result.metrics_ref, Mapping)
        and result.metrics_ref.get("paper_eligible") is True
        for result in results
    )


def _is_offline_capturing_transport(transport: Any) -> bool:
    return (
        transport is not None
        and getattr(transport, "tokenshare_offline_capturing_transport", False)
        is True
    )


def _status_value(status: PaperStatus | str) -> str:
    return str(getattr(status, "value", status))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _condition_endpoint_contract(
    *,
    experiment_id: str,
    condition: Any,
    ai_api_configs: Mapping[str, Any],
) -> tuple[Mapping[str, Any], dict[str, Any], AIAPIExecutorConfig]:
    provider_config_id = condition.provider_config_id
    if not isinstance(provider_config_id, str) or not provider_config_id:
        raise ValueError("formal condition provider_config_id is required")
    config = ai_api_configs.get(provider_config_id)
    if not isinstance(config, AIAPIExecutorConfig):
        raise ValueError("formal condition provider config is unavailable")
    if config.config_digest != condition.source_provider_config_digest:
        raise ValueError("formal condition source provider config digest mismatch")
    if config.provider_family != condition.provider_family:
        raise ValueError("formal condition provider family mismatch")

    entries = tuple(
        entry
        for entry in config.entries
        if entry.enabled and entry.entry_id == condition.model_entry_id
    )
    if len(entries) != 1:
        raise ValueError("formal condition selected model entry is unavailable")
    entry = entries[0]
    if entry.model != condition.provider_model_id:
        raise ValueError("formal condition provider model mismatch")
    if not isinstance(condition.reasoning_profile_id, str):
        raise ValueError("formal condition reasoning profile is required")
    if not isinstance(condition.model_endpoint_identity_digest, str):
        raise ValueError("formal condition endpoint identity digest is required")

    request_limits = {**dict(config.defaults), **dict(entry.request_overrides)}
    _validate_reasoning_controls(
        provider_family=config.provider_family,
        reasoning_profile_id=condition.reasoning_profile_id,
        request_limits=request_limits,
    )
    for field_name in ("max_tokens", "timeout_seconds"):
        value = request_limits.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"formal request control {field_name} must be positive")

    derived_binding: dict[str, Any] = {
        "provider_config_id": provider_config_id,
        "selected_entry_id": condition.model_entry_id,
        "model_entry_id": condition.model_entry_id,
        "provider_family": condition.provider_family,
        "provider_model_id": condition.provider_model_id,
        "reasoning_profile_id": condition.reasoning_profile_id,
        "source_provider_config_digest": condition.source_provider_config_digest,
        "model_endpoint_identity_digest": (
            condition.model_endpoint_identity_digest
        ),
        "request_controls": request_limits,
    }
    for field_name in (
        "model_cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
    ):
        value = getattr(condition, field_name)
        if value is not None:
            derived_binding[field_name] = value

    if experiment_id != EXP5_EXPERIMENT_ID:
        return derived_binding, request_limits, config

    # Exp5 的完整 cohort preflight 无法由单个 condition/config 重建；调用方必须
    # 在保留键下按 experiment_id 提供已批准 binding，普通 config map 不能冒充。
    scoped_bindings = ai_api_configs.get(APPROVED_ENDPOINT_BINDINGS_KEY)
    if not isinstance(scoped_bindings, Mapping):
        raise ValueError("Exp5 requires explicit approved endpoint bindings")
    approved_binding = scoped_bindings.get(experiment_id)
    if not isinstance(approved_binding, Mapping):
        raise ValueError("Exp5 approved endpoint binding is unavailable")
    _validate_exp5_binding(
        approved_binding=approved_binding,
        condition=condition,
        derived_binding=derived_binding,
    )
    return dict(approved_binding), request_limits, config


def _validate_reasoning_controls(
    *,
    provider_family: str,
    reasoning_profile_id: str,
    request_limits: Mapping[str, Any],
) -> None:
    if provider_family == "siliconflow" and (
        request_limits.get("enable_thinking") is not False
    ):
        raise ValueError("SiliconFlow formal execution requires enable_thinking=false")
    if reasoning_profile_id == "high" and (
        request_limits.get("reasoning_effort") != "high"
    ):
        raise ValueError("high reasoning profile requires reasoning_effort=high")


def _validate_exp5_binding(
    *,
    approved_binding: Mapping[str, Any],
    condition: Any,
    derived_binding: Mapping[str, Any],
) -> None:
    if approved_binding.get("status") != "planned":
        raise ValueError("Exp5 approved endpoint binding is not executable")
    approved_cohort_id = approved_binding.get(
        "cohort_id",
        approved_binding.get("model_cohort_id"),
    )
    if approved_cohort_id != condition.model_cohort_id:
        raise ValueError("Exp5 approved preflight cohort ID mismatch")
    if approved_binding.get("model_cohort_digest") != condition.model_cohort_digest:
        raise ValueError("Exp5 approved preflight cohort digest mismatch")
    member_plans = approved_binding.get("member_plans")
    if not isinstance(member_plans, Mapping):
        raise ValueError("Exp5 approved endpoint binding lacks member plans")
    member_plan = member_plans.get(condition.cohort_member_id)
    if not isinstance(member_plan, Mapping):
        raise ValueError("Exp5 condition lacks an approved cohort member plan")
    member_cohort_id = member_plan.get(
        "cohort_id",
        member_plan.get("model_cohort_id"),
    )
    if member_cohort_id != condition.model_cohort_id:
        raise ValueError("Exp5 approved member cohort ID mismatch")
    if member_plan.get("model_cohort_digest") != condition.model_cohort_digest:
        raise ValueError("Exp5 approved member cohort digest mismatch")
    if member_plan.get("cohort_member_id") != condition.cohort_member_id:
        raise ValueError("Exp5 approved member ID mismatch")
    member_controls = member_plan.get("request_controls")
    if member_controls is not None:
        if not isinstance(member_controls, Mapping):
            raise ValueError("Exp5 approved member request controls are invalid")
        if dict(member_controls) != dict(derived_binding["request_controls"]):
            raise ValueError("Exp5 approved member request controls mismatch")
    for field_name in (
        "provider_config_id",
        "selected_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    ):
        if member_plan.get(field_name) != derived_binding.get(field_name):
            raise ValueError(f"Exp5 approved member {field_name} mismatch")


@dataclass(frozen=True, kw_only=True)
class _RootExecutionOutcome:
    case_id: str
    root_status: str
    adapter_root: Path
    worker_id: str
    task: Any = None
    adapter_result: Any = None
    provider_attempt_count: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    paper_eligible: bool = False
    provider_latency_ms: float = 0.0
    provider_error_kind: str | None = None
    error: Exception | None = None
    runtime_records: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _FormalConditionExecutionCallback:
    catalog_manifest: Any
    config: AIAPIExecutorConfig
    transport: Any
    real_transport: bool
    output_root: Path
    request_limits: Mapping[str, Any]
    evidence_store: FormalEvidenceStore
    completed_task_keys: set[tuple[str, str, str, str]]
    usage: "_UsageTotals"
    budget: PaperBudgetResult
    hard_limits: Mapping[str, Any]
    usage_lock: Lock = field(default_factory=Lock, compare=False, repr=False)

    def __call__(self, **kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        cases_by_id = _catalog_cases_by_id(self.catalog_manifest)
        completed = sum(
            _formal_task_key(condition, case_id) in self.completed_task_keys
            for case_id in selection.ordered_case_ids
        )
        blocked = 0
        failed = 0
        provider_attempts = 0
        adapter_eligible: list[bool] = []
        budget_exhausted = False
        pending_case_ids = tuple(
            case_id
            for case_id in selection.ordered_case_ids
            if _formal_task_key(condition, case_id) not in self.completed_task_keys
        )
        if self.hard_limits.get("stop_after_current_task") is True:
            pending_case_ids = pending_case_ids[:1]
        worker_count = (
            condition.worker_count
            if condition.experiment_id == EXP2_EXPERIMENT_ID
            else 1
        )
        strategy = run_scheduled_cases(
            ordered_case_ids=pending_case_ids,
            worker_count=worker_count,
            execute_case=lambda case_id, worker_id: self._dispatch_root_case(
                condition=condition,
                case_id=case_id,
                case=cases_by_id.get(case_id),
                worker_id=worker_id,
                callback_kwargs=kwargs,
            ),
        )
        for case_id, outcome in zip(
            strategy.ordered_case_ids,
            strategy.outcomes,
            strict=True,
        ):
            outcome = self._apply_experiment_runtime(
                condition=condition,
                case_id=case_id,
                case=cases_by_id[case_id],
                outcome=outcome,
                callback_kwargs=kwargs,
            )
            scheduling_events = tuple(
                event
                for event in strategy.events
                if event.get("case_id") == case_id
            )
            if case_id == strategy.ordered_case_ids[-1]:
                scheduling_events += tuple(
                    event
                    for event in strategy.events
                    if event.get("event_type", "").startswith("MERGE_GATE_")
                )
            if outcome.root_status == "budget_exhausted":
                budget_exhausted = True
                failed += 1
                self._checkpoint_budget_exhausted(
                    condition=condition,
                    task_id=case_id,
                    extra_events=scheduling_events,
                )
                continue
            if outcome.error is not None:
                failed += 1
                self._checkpoint_exception(
                    condition=condition,
                    task_id=case_id,
                    error=outcome.error,
                    extra_events=scheduling_events,
                )
                continue
            if outcome.root_status == "completed":
                completed += 1
            elif outcome.root_status == "blocked":
                blocked += 1
            else:
                failed += 1
            provider_attempts += outcome.provider_attempt_count
            adapter_eligible.append(outcome.paper_eligible)
            self._checkpoint_adapter_result(
                condition=condition,
                task_id=case_id,
                task=outcome.task,
                adapter_result=outcome.adapter_result,
                adapter_root=outcome.adapter_root,
                extra_events=scheduling_events,
            )

        self._publish_compatibility_view(condition=condition)

        status = (
            PaperStatus.BUDGET_EXHAUSTED
            if budget_exhausted
            else PaperStatus.COMPLETED
            if completed == len(selection.ordered_case_ids)
            else PaperStatus.BLOCKED
            if blocked == len(selection.ordered_case_ids)
            else PaperStatus.COMPLETED_WITH_FAILURES
        )
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=status,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=completed,
            failed_root_count=failed,
            blocked_root_count=blocked,
            provider_attempt_count=provider_attempts,
            metrics_ref={
                "paper_eligible": bool(adapter_eligible) and all(adapter_eligible),
                **strategy.metrics,
            },
        )

    def _dispatch_root_case(
        self,
        *,
        condition: Any,
        case_id: str,
        case: Any,
        worker_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        if case is None:
            raise ValueError("frozen selection case is absent from catalog")
        reservation = (
            _root_budget_reservation(
                case=case,
                request_limits=self.request_limits,
                budget=self.budget,
            )
            if _has_resource_hard_limit(self.hard_limits)
            else _RootBudgetReservation(0, 0, 0.0)
        )
        with self.usage_lock:
            if not _reserve_hard_limit_capacity(
                usage=self.usage,
                reservation=reservation,
                hard_limits=self.hard_limits,
            ):
                return _RootExecutionOutcome(
                    case_id=case_id,
                    root_status="budget_exhausted",
                    adapter_root=self.output_root,
                    worker_id=worker_id,
                )
        adapter_root = self.output_root / "runs" / condition.condition_id / case_id
        runtime_records: list[dict[str, Any]] = []
        post_raw_output_hook = self._exp3_post_raw_output_hook(
            condition=condition,
            case_id=case_id,
            callback_kwargs=callback_kwargs,
            runtime_records=runtime_records,
        )
        ablation_mode = None
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            mode_config = callback_kwargs.get("mode_config")
            ablation_mode = str(
                _optional_field(mode_config, "ablation_mode")
                or condition.ablation_mode
            )
        try:
            adapter_result = dispatch_paper_case(
                case=case,
                condition=condition,
                output_root=adapter_root.as_posix(),
                transport=self.transport,
                real_transport=self.real_transport,
                ai_api_config=self.config,
                entry_id=condition.model_entry_id,
                max_tokens=int(self.request_limits["max_tokens"]),
                timeout_seconds=int(self.request_limits["timeout_seconds"]),
                post_raw_output_hook=post_raw_output_hook,
                ablation_mode=ablation_mode,
            )
        except (RuntimeError, OSError, TimeoutError) as error:
            with self.usage_lock:
                _settle_hard_limit_reservation(
                    usage=self.usage,
                    reservation=reservation,
                )
            return _RootExecutionOutcome(
                case_id=case_id,
                root_status="failed",
                adapter_root=adapter_root,
                worker_id=worker_id,
                error=error,
            )
        task = _required_field(adapter_result, "task_result")
        provider_attempt_count = int(
            _required_field(task, "provider_attempt_count")
        )
        total_tokens = int(_optional_field(task, "total_tokens") or 0)
        cost_estimate = float(_optional_field(task, "cost_estimate") or 0.0)
        with self.usage_lock:
            _settle_hard_limit_reservation(
                usage=self.usage,
                reservation=reservation,
                provider_attempt_count=provider_attempt_count,
                total_tokens=total_tokens,
                total_cost_estimate=cost_estimate,
            )
        eligibility = _optional_field(adapter_result, "eligibility_report")
        attempts = _sequence_field(adapter_result, "attempt_results", "attempts")
        return _RootExecutionOutcome(
            case_id=case_id,
            root_status=_status_value(_required_field(task, "root_status")),
            adapter_root=adapter_root,
            worker_id=worker_id,
            task=task,
            adapter_result=adapter_result,
            provider_attempt_count=provider_attempt_count,
            total_tokens=total_tokens,
            cost_estimate=cost_estimate,
            paper_eligible=_optional_field(eligibility, "paper_eligible") is True,
            provider_latency_ms=sum(
                float(_optional_field(attempt, "latency_ms") or 0.0)
                for attempt in attempts
            ),
            provider_error_kind=next(
                (
                    str(_optional_field(attempt, "error_kind"))
                    for attempt in attempts
                    if _optional_field(attempt, "error_kind") is not None
                ),
                None,
            ),
            runtime_records=tuple(runtime_records),
        )

    def _apply_experiment_runtime(
        self,
        *,
        condition: Any,
        case_id: str,
        case: Mapping[str, Any],
        outcome: "_RootExecutionOutcome",
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        if outcome.error is not None or outcome.adapter_result is None:
            return outcome
        if condition.experiment_id == "exp3_real_ai_fault_recovery":
            manifest = _required_mapping(
                callback_kwargs.get("execution_manifest"),
                "Exp3 execution_manifest",
            )
            if isinstance(manifest.get("fault_target_manifest"), Mapping):
                return self._apply_exp3_rate_fault(
                    condition=condition,
                    case=case,
                    outcome=outcome,
                    manifest=manifest,
                )
            if isinstance(manifest.get("worker_death_manifest"), Mapping):
                return self._apply_exp3_worker_death(
                    condition=condition,
                    case_id=case_id,
                    outcome=outcome,
                    manifest=manifest,
                )
            raise ValueError("Exp3 execution_manifest has no executable strategy")
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            return self._apply_exp4_mode(
                condition=condition,
                outcome=outcome,
                callback_kwargs=callback_kwargs,
            )
        if condition.experiment_id == EXP5_EXPERIMENT_ID:
            return self._apply_exp5_identity(condition=condition, outcome=outcome)
        return outcome

    def _exp3_post_raw_output_hook(
        self,
        *,
        condition: Any,
        case_id: str,
        callback_kwargs: Mapping[str, Any],
        runtime_records: list[dict[str, Any]],
    ) -> Any | None:
        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        manifest = callback_kwargs.get("execution_manifest")
        if not isinstance(manifest, Mapping):
            return None
        target_manifest = manifest.get("fault_target_manifest")
        if not isinstance(target_manifest, Mapping):
            return None
        selected = set(
            _string_sequence(
                target_manifest.get("selected_target_ai_unit_ids", ()),
                "selected_target_ai_unit_ids",
            )
        )
        fault_type = str(
            target_manifest.get("fault_type") or condition.fault_type
        )

        def hook(**context: Any) -> Mapping[str, Any] | None:
            request = context["request"]
            if request.unit_id not in selected:
                return None
            artifact_store = context["artifact_store"]
            if not isinstance(artifact_store, ArtifactStore):
                raise ValueError("Exp3 post-raw hook requires ArtifactStore")
            parsed_ref = None
            if fault_type in {"false_positive", "false_negative"}:
                try:
                    parsed_body = json.loads(str(context["content_text"]))
                except json.JSONDecodeError:
                    parsed_body = {"candidate": str(context["content_text"])}
                if not isinstance(parsed_body, dict):
                    parsed_body = {"candidate": parsed_body}
                parsed_ref = artifact_store.save_json(
                    parsed_body,
                    artifact_id=(
                        f"exp3_pre_fault_candidate_{context['submission_id']}"
                    ),
                    artifact_type="Exp3PreFaultCandidate",
                    artifact_schema_id="tokenshare.paper_exp3_pre_fault_candidate",
                    artifact_schema_version="v1",
                    source={
                        "kind": "paper_formal_exp3_post_raw_hook",
                        "request_id": request.request_id,
                    },
                    metadata={"unit_id": request.unit_id},
                    created_at=str(context["submitted_at"]),
                )
            usage = context["usage_summary"]
            injected_at = datetime.now(timezone.utc)
            late = fault_type == "late_submission"
            attempt = PaperAttemptResult(
                condition_id=condition.condition_id,
                repeat_id=condition.repeat_id,
                run_id=f"formal-{condition.condition_id}-{case_id}",
                task_id=case_id,
                unit_id=request.unit_id,
                attempt_id=request.attempt_id,
                worker_id=str(
                    request.allocation_decision.get("worker_id", "formal-worker")
                ),
                provider_attempt_index=1,
                attempt_status="succeeded",
                provider=str(context["provider_family"]),
                model=str(context["model"]),
                entry_id=str(context["entry_id"]),
                request_ref=None,
                raw_output_ref=context["raw_output_ref"].to_dict(),
                parsed_output_ref=(
                    parsed_ref.to_dict() if parsed_ref is not None else None
                ),
                parse_failure_ref=None,
                provenance_ref=context["provenance_ref"].to_dict(),
                usage_ref=context["usage_ref"].to_dict(),
                started_at=str(context["submitted_at"]),
                ended_at=str(context["submitted_at"]),
                latency_ms=0,
                prompt_tokens=int(
                    usage.get("prompt_tokens", usage.get("input_tokens", 0))
                ),
                completion_tokens=int(
                    usage.get(
                        "completion_tokens",
                        usage.get("output_tokens", 0),
                    )
                ),
                total_tokens=int(usage.get("total_tokens", 0)),
                cost_estimate=float(usage.get("cost_estimate", 0.0)),
                error_kind=None,
                fault_injection_ref=None,
                paper_eligible=False,
            )
            injected_at_text = injected_at.isoformat().replace("+00:00", "Z")
            fault_outcome = inject_exp3_post_ai_fault(
                artifact_store=artifact_store,
                attempt=attempt,
                fault_type=fault_type,
                seed=int(condition.seed),
                created_at=injected_at_text,
                lease_deadline_at=(
                    (injected_at - timedelta(seconds=2))
                    .isoformat()
                    .replace("+00:00", "Z")
                    if late
                    else None
                ),
                submitted_at=(
                    (injected_at - timedelta(seconds=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                    if late
                    else None
                ),
                fault_target={
                    "unit_id": request.unit_id,
                    "attempt_id": request.attempt_id,
                    "artifact_ref": (
                        parsed_ref.to_dict()
                        if parsed_ref is not None
                        else context["raw_output_ref"].to_dict()
                    ),
                },
            )
            record = dict(fault_outcome.record)
            record.update(
                {
                    "fault_injection_id": str(record["fault_id"]),
                    "requires_replacement": True,
                    "record_ref": fault_outcome.record_ref.to_dict(),
                    "pre_fault_usage_ref": context["usage_ref"].to_dict(),
                    "hook_stage": "after_raw_provenance_usage_before_parser",
                }
            )
            runtime_records.append(record)
            if fault_type in {"no_return", "late_submission", "executor_error"}:
                return {"result_kind": fault_type}
            mutated_body = json.loads(
                artifact_store.read_bytes(
                    fault_outcome.primitive_outcome.mutated_output_ref
                ).decode("utf-8")
            )
            return {
                "content_text": json.dumps(
                    mutated_body,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            }

        return hook

    def _apply_exp3_rate_fault(
        self,
        *,
        condition: Any,
        case: Mapping[str, Any],
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        target_manifest = _required_mapping(
            manifest.get("fault_target_manifest"),
            "Exp3 fault_target_manifest",
        )
        selected_unit_ids = _string_sequence(
            target_manifest.get("selected_target_ai_unit_ids"),
            "selected_target_ai_unit_ids",
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        if not selected_unit_ids:
            return outcome
        artifact_store = ArtifactStore(
            _adapter_store_root(
                adapter_root=outcome.adapter_root,
                case_id=outcome.case_id,
                attempts=attempts,
            )
        )
        fault_type = str(target_manifest.get("fault_type") or condition.fault_type)
        injected_at = datetime.now(timezone.utc)
        injected_at_text = injected_at.isoformat().replace("+00:00", "Z")
        replacement_results: list[Any] = []
        runtime_faults_by_unit = {
            str(record.get("unit_id")): record
            for record in outcome.runtime_records
            if record.get("unit_id") is not None
        }

        def inject_fault(attempt: Any, selected_fault_type: str) -> Mapping[str, Any]:
            runtime_record = runtime_faults_by_unit.get(
                str(_required_field(attempt, "unit_id"))
            )
            if runtime_record is not None:
                return runtime_record
            if not isinstance(attempt, PaperAttemptResult):
                raise ValueError("Exp3 adapter attempts must be PaperAttemptResult")
            late = selected_fault_type == "late_submission"
            primitive = inject_exp3_post_ai_fault(
                artifact_store=artifact_store,
                attempt=attempt,
                fault_type=selected_fault_type,
                seed=int(condition.seed),
                created_at=injected_at_text,
                lease_deadline_at=(
                    (injected_at - timedelta(seconds=2))
                    .isoformat()
                    .replace("+00:00", "Z")
                    if late
                    else None
                ),
                submitted_at=(
                    (injected_at - timedelta(seconds=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                    if late
                    else None
                ),
                fault_target={
                    "unit_id": attempt.unit_id,
                    "attempt_id": attempt.attempt_id,
                    "artifact_ref": attempt.parsed_output_ref
                    or attempt.raw_output_ref,
                },
            )
            record = dict(primitive.record)
            record["fault_injection_id"] = str(
                record.get("fault_id", f"fault-{attempt.attempt_id}")
            )
            record["requires_replacement"] = True
            record["record_ref"] = primitive.record_ref.to_dict()
            return record

        def execute_replacement(attempt: Any) -> Any:
            replacement_root = (
                outcome.adapter_root
                / "recovery"
                / _artifact_task_directory(str(_required_field(attempt, "attempt_id")))
            )
            replacement_result = dispatch_paper_case(
                case=dict(case),
                condition=condition,
                output_root=replacement_root.as_posix(),
                transport=self.transport,
                real_transport=self.real_transport,
                ai_api_config=self.config,
                entry_id=condition.model_entry_id,
                max_tokens=int(self.request_limits["max_tokens"]),
                timeout_seconds=int(self.request_limits["timeout_seconds"]),
                selected_ai_unit_id=str(_required_field(attempt, "unit_id")),
            )
            replacement_results.append(replacement_result)
            replacement_task = _required_field(replacement_result, "task_result")
            provider_attempt_count = int(
                _required_field(replacement_task, "provider_attempt_count")
            )
            total_tokens = int(_optional_field(replacement_task, "total_tokens") or 0)
            cost_estimate = float(
                _optional_field(replacement_task, "cost_estimate") or 0.0
            )
            with self.usage_lock:
                self.usage.provider_attempt_count += provider_attempt_count
                self.usage.total_tokens += total_tokens
                self.usage.total_cost_estimate += cost_estimate
            replacement_attempts = _sequence_field(
                replacement_result,
                "attempt_results",
                "attempts",
            )
            if not replacement_attempts:
                raise ValueError("Exp3 replacement produced no attempt evidence")
            return replacement_attempts[0]

        strategy = run_exp3_post_ai_strategy(
            attempts=attempts,
            selected_target_ai_unit_ids=selected_unit_ids,
            fault_type=fault_type,
            inject_fault=inject_fault,
            execute_replacement=execute_replacement,
            approved_identity={
                "provider_family": condition.provider_family,
                "provider_model_id": condition.provider_model_id,
                "model_entry_id": condition.model_entry_id,
            },
        )
        replacement_tasks = [
            _required_field(result, "task_result") for result in replacement_results
        ]
        task_body = _as_json(outcome.task)
        task_body["provider_attempt_count"] = outcome.provider_attempt_count + sum(
            int(_required_field(task, "provider_attempt_count"))
            for task in replacement_tasks
        )
        task_body["total_tokens"] = outcome.total_tokens + sum(
            int(_optional_field(task, "total_tokens") or 0)
            for task in replacement_tasks
        )
        task_body["cost_estimate"] = outcome.cost_estimate + sum(
            float(_optional_field(task, "cost_estimate") or 0.0)
            for task in replacement_tasks
        )
        task_body["fault_injected"] = bool(strategy.fault_records)
        task_body["recovered_fault_target_count"] = len(
            strategy.replacement_attempts
        )
        if isinstance(manifest.get("matched_baseline"), Mapping):
            task_body["matched_baseline"] = _as_json(
                manifest["matched_baseline"]
            )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=tuple(attempts) + strategy.replacement_attempts,
            faults=(
                *_sequence_field(outcome.adapter_result, "fault_records", "faults"),
                *strategy.fault_records,
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *strategy.events,
            ),
        )
        return replace(
            outcome,
            task=task_body,
            adapter_result=adapter_result,
            root_status=str(task_body.get("root_status", outcome.root_status)),
            provider_attempt_count=int(task_body["provider_attempt_count"]),
            total_tokens=int(task_body["total_tokens"]),
            cost_estimate=float(task_body["cost_estimate"]),
        )

    def _apply_exp3_worker_death(
        self,
        *,
        condition: Any,
        case_id: str,
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        worker_manifest = _required_mapping(
            manifest.get("worker_death_manifest"),
            "Exp3 worker_death_manifest",
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        unit_ids = tuple(
            str(_required_field(attempt, "unit_id")) for attempt in attempts
        )
        dead_worker_count = int(worker_manifest["dead_worker_count_target"])
        if dead_worker_count > len(unit_ids):
            unit_ids += tuple(
                f"{case_id}-formal-unit-{index}"
                for index in range(len(unit_ids), dead_worker_count)
            )
        strategy = run_exp3_worker_death_strategy(
            artifact_root=outcome.adapter_root / "worker-death",
            condition_id=condition.condition_id,
            repeat_id=condition.repeat_id,
            task_id=case_id,
            ai_unit_ids=unit_ids,
            dead_worker_count=dead_worker_count,
            kill_point=(
                f"progress_{int(worker_manifest['kill_progress_target_percent'])}"
            ),
            started_at=_utc_now(),
            process_tick_seconds=0.005,
        )
        replacement_attempts = tuple(
            {
                **dict(record["replacement_attempt"]),
                "attempt_status": "succeeded",
                "provider": condition.provider_family,
                "model": condition.provider_model_id,
                "entry_id": condition.model_entry_id,
            }
            for record in strategy.fault_records
        )
        faults = tuple(
            {
                **record,
                "fault_injection_id": (
                    f"worker-death-{condition.condition_id}-"
                    f"{record['target_ai_unit']['unit_id']}"
                ),
            }
            for record in strategy.fault_records
        )
        task_body = _as_json(outcome.task)
        task_body.update(
            {
                "worker_death_count": len(faults),
                "worker_replacement_count": len(replacement_attempts),
                "coordinator_survived": strategy.metrics[
                    "coordinator_survived"
                ],
                "matched_baseline_provider_attempt_count": (
                    outcome.provider_attempt_count
                ),
                "matched_baseline_total_tokens": outcome.total_tokens,
                "matched_baseline_cost_estimate": outcome.cost_estimate,
                "matched_baseline_provider_latency_ms": (
                    outcome.provider_latency_ms
                ),
                "matched_baseline_wall_clock_ms": float(
                    _optional_field(outcome.task, "wall_clock_ms") or 0.0
                ),
            }
        )
        if isinstance(manifest.get("matched_baseline"), Mapping):
            task_body["matched_baseline"] = _as_json(
                manifest["matched_baseline"]
            )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=tuple(attempts) + replacement_attempts,
            faults=(
                *_sequence_field(outcome.adapter_result, "fault_records", "faults"),
                *faults,
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *strategy.events,
            ),
        )
        return replace(outcome, task=task_body, adapter_result=adapter_result)

    def _apply_exp4_mode(
        self,
        *,
        condition: Any,
        outcome: "_RootExecutionOutcome",
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        mode_config = callback_kwargs.get("mode_config")
        mode = (
            _optional_field(mode_config, "ablation_mode")
            or condition.ablation_mode
        )
        outcome = self._apply_exp4_requeue_boundary(
            condition=condition,
            outcome=outcome,
            mode=str(mode),
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        statuses = {
            _status_value(_optional_field(attempt, "attempt_status") or "")
            for attempt in attempts
        }
        run_evidence = _optional_field(outcome.adapter_result, "run_evidence")
        runtime = _optional_field(run_evidence, "ablation_runtime")
        observation = {
            "candidate_rejected": bool(
                statuses & {"verification_rejected", "checker_rejected"}
            ),
            "parse_failed": (
                "parse_failed" in statuses
                or _optional_field(runtime, "raw_only_exposed") is True
            ),
            "replacement_created": len(attempts) > 1,
            "merge_gate_blocked": (
                outcome.root_status != "completed"
                or _optional_field(runtime, "premature_merge_attempted") is True
            ),
            "slot_binding_valid": (
                _optional_field(runtime, "slot_integrity_violation") is not True
            ),
            "deterministic_validity": (
                _optional_field(runtime, "root_validity_audit_passed")
                if runtime is not None
                else outcome.root_status == "completed"
            ),
        }
        strategy = run_exp4_ablation_strategy(
            mode=str(mode),
            adapter_observation=observation,
        )
        task_body = _as_json(outcome.task)
        task_body.update(
            {
                "ablation_mode": strategy.mode,
                "ablation_runtime_flags": strategy.runtime_flags,
                "ablation_applicable": strategy.metrics["applicable"],
                "exposed_error_count": strategy.metrics[
                    "exposed_error_count"
                ],
                "escaped_error_count": strategy.metrics[
                    "escaped_error_count"
                ],
                "final_deterministic_validity": strategy.metrics[
                    "final_deterministic_validity"
                ],
            }
        )
        task_body["ablation_runtime"] = _as_json(runtime or {})
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=attempts,
            faults=_sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *strategy.events,
            ),
        )
        return replace(
            outcome,
            task=task_body,
            adapter_result=adapter_result,
            root_status=outcome.root_status,
        )

    def _apply_exp4_requeue_boundary(
        self,
        *,
        condition: Any,
        outcome: "_RootExecutionOutcome",
        mode: str,
    ) -> "_RootExecutionOutcome":
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        rejected = any(
            _status_value(_optional_field(attempt, "attempt_status") or "")
            in {"parse_failed", "verification_rejected", "checker_rejected"}
            for attempt in attempts
        )
        if not rejected:
            return outcome
        if mode == "NO_REQUEUE":
            task_body = _as_json(outcome.task)
            task_body.update(
                {
                    "requeue_suppressed_by_ablation": True,
                    "stuck_after_rejection": True,
                    "replacement_attempt_count": 0,
                }
            )
            adapter_result = _adapter_result_with(
                outcome.adapter_result,
                task_result=task_body,
                attempts=attempts,
                faults=_sequence_field(
                    outcome.adapter_result,
                    "fault_records",
                    "faults",
                ),
                events=(
                    *_sequence_field(outcome.adapter_result, "event_records"),
                    {
                        "event_type": "REQUEUE_SUPPRESSED_BY_ABLATION",
                        "ablation_mode": mode,
                    },
                ),
            )
            return replace(outcome, task=task_body, adapter_result=adapter_result)

        case = _catalog_cases_by_id(self.catalog_manifest)[outcome.case_id]
        reservation = _root_budget_reservation(
            case=case,
            request_limits=self.request_limits,
            budget=self.budget,
        )
        with self.usage_lock:
            reserved = _reserve_hard_limit_capacity(
                usage=self.usage,
                reservation=reservation,
                hard_limits=self.hard_limits,
            )
        if not reserved:
            return outcome
        replacement_root = outcome.adapter_root / "requeue-1"
        try:
            replacement_result = dispatch_paper_case(
                case=case,
                condition=condition,
                output_root=replacement_root.as_posix(),
                transport=self.transport,
                real_transport=self.real_transport,
                ai_api_config=self.config,
                entry_id=condition.model_entry_id,
                max_tokens=int(self.request_limits["max_tokens"]),
                timeout_seconds=int(self.request_limits["timeout_seconds"]),
                ablation_mode=mode,
            )
        except (RuntimeError, OSError, TimeoutError):
            with self.usage_lock:
                _settle_hard_limit_reservation(
                    usage=self.usage,
                    reservation=reservation,
                )
            return outcome
        replacement_task = _required_field(replacement_result, "task_result")
        replacement_attempt_count = int(
            _required_field(replacement_task, "provider_attempt_count")
        )
        replacement_tokens = int(
            _optional_field(replacement_task, "total_tokens") or 0
        )
        replacement_cost = float(
            _optional_field(replacement_task, "cost_estimate") or 0.0
        )
        with self.usage_lock:
            _settle_hard_limit_reservation(
                usage=self.usage,
                reservation=reservation,
                provider_attempt_count=replacement_attempt_count,
                total_tokens=replacement_tokens,
                total_cost_estimate=replacement_cost,
            )
        replacement_attempts = tuple(
            {
                **_as_json(attempt),
                "attempt_id": (
                    f"{str(_required_field(attempt, 'attempt_id'))}:requeue-1"
                ),
                "replacement_for_attempt_id": (
                    str(_required_field(attempts[0], "attempt_id"))
                    if attempts
                    else None
                ),
            }
            for attempt in _sequence_field(
                replacement_result,
                "attempt_results",
                "attempts",
            )
        )
        task_body = _as_json(replacement_task)
        task_body.update(
            {
                "requeue_created": True,
                "stuck_after_rejection": False,
                "replacement_attempt_count": len(replacement_attempts),
            }
        )
        combined_result = _adapter_result_with(
            replacement_result,
            task_result=task_body,
            attempts=tuple(attempts) + replacement_attempts,
            faults=(
                *_sequence_field(outcome.adapter_result, "fault_records", "faults"),
                *_sequence_field(replacement_result, "fault_records", "faults"),
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                {
                    "event_type": "REQUEUE_CREATED",
                    "ablation_mode": mode,
                    "replacement_attempt_count": len(replacement_attempts),
                },
                *_sequence_field(replacement_result, "event_records"),
            ),
        )
        replacement_status = _status_value(
            _required_field(replacement_task, "root_status")
        )
        replacement_eligibility = _optional_field(
            replacement_result,
            "eligibility_report",
        )
        return replace(
            outcome,
            root_status=replacement_status,
            task=task_body,
            adapter_result=combined_result,
            provider_attempt_count=(
                outcome.provider_attempt_count + replacement_attempt_count
            ),
            total_tokens=outcome.total_tokens + replacement_tokens,
            cost_estimate=outcome.cost_estimate + replacement_cost,
            paper_eligible=(
                outcome.paper_eligible
                and _optional_field(replacement_eligibility, "paper_eligible") is True
            ),
        )

    def _apply_exp5_identity(
        self,
        *,
        condition: Any,
        outcome: "_RootExecutionOutcome",
    ) -> "_RootExecutionOutcome":
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        strategy = run_exp5_identity_strategy(
            attempts=attempts,
            approved_identity={
                "provider_family": condition.provider_family,
                "provider_model_id": condition.provider_model_id,
                "model_entry_id": condition.model_entry_id,
            },
            condition_id=condition.condition_id,
            cohort_member_id=str(condition.cohort_member_id),
        )
        enriched_attempts = tuple(
            {
                **_as_json(attempt),
                "cohort_member_id": condition.cohort_member_id,
                "model_identity_audit": "fixed_entry_match",
            }
            for attempt in attempts
        )
        task_body = _as_json(outcome.task)
        task_body["model_execution_records"] = list(
            strategy.model_execution_records
        )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=enriched_attempts,
            faults=_sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            ),
            events=_sequence_field(outcome.adapter_result, "event_records"),
        )
        return replace(outcome, task=task_body, adapter_result=adapter_result)

    def _checkpoint_adapter_result(
        self,
        *,
        condition: Any,
        task_id: str,
        task: Any,
        adapter_result: Any,
        adapter_root: Path,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task_body = _record_with_context(
            task,
            condition=condition,
            task_id=task_id,
        )
        task_body.update(_evidence_flags())
        attempt_values = _sequence_field(adapter_result, "attempt_results", "attempts")
        attempts = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in attempt_values
        ]
        if not attempts:
            attempts = [
                _record_with_context(
                    _synthetic_attempt(condition=condition, task_id=task_id),
                    condition=condition,
                    task_id=task_id,
                )
            ]
        for attempt in attempts:
            attempt.update(_evidence_flags())
        events = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in (
                *_sequence_field(adapter_result, "event_records"),
                *extra_events,
            )
        ]
        if not events:
            events = [
                _record_with_context(
                    _event_record(
                        condition=condition,
                        task_id=task_id,
                        event_type="TASK_RECORDED",
                    ),
                    condition=condition,
                    task_id=task_id,
                )
            ]
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                (
                    f"formal-{str(event.get('event_type', 'event')).lower()}-"
                    f"{condition.condition_id}-{condition.repeat_id}-{task_id}-{index}"
                ),
            )
            event.update(_evidence_flags())
        faults = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in _sequence_field(adapter_result, "fault_records", "faults")
        ]
        for fault in faults:
            fault.update(_evidence_flags())
        artifact_refs = _materialize_artifacts(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            adapter_root=adapter_root,
            source_refs=_adapter_artifact_refs(task, attempts, faults),
        )
        if not artifact_refs:
            artifact_refs = [_write_runner_artifact(
                suite_root=self.evidence_store.output_root,
                condition=condition,
                task_id=task_id,
                artifact_name="task-record.json",
                body=task_body,
            )]
        self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task_body,
            attempts=attempts,
            faults=faults,
            events=events,
            artifact_refs=artifact_refs,
        )
        if _status_value(task_body.get("root_status", "failed")) == "completed":
            self.completed_task_keys.add(_formal_task_key(condition, task_id))

    def _checkpoint_exception(
        self,
        *,
        condition: Any,
        task_id: str,
        error: Exception,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task = _record_with_context(
            {"task_id": task_id, "root_status": "failed", "error_kind": type(error).__name__},
            condition=condition,
            task_id=task_id,
        )
        task.update(_evidence_flags())
        attempt = _record_with_context(
            _synthetic_attempt(condition=condition, task_id=task_id),
            condition=condition,
            task_id=task_id,
        )
        attempt.update({"attempt_status": "failed", "error_kind": type(error).__name__, **_evidence_flags()})
        events = [
            _record_with_context(
                _event_record(
                    condition=condition,
                    task_id=task_id,
                    event_type="RUNNER_EXCEPTION",
                ),
                condition=condition,
                task_id=task_id,
            ),
            *(
                _record_with_context(item, condition=condition, task_id=task_id)
                for item in extra_events
            ),
        ]
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                f"formal-exception-{condition.condition_id}-{task_id}-{index}",
            )
            event.update(_evidence_flags())
        artifact = _write_runner_artifact(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            artifact_name="runner-error.json",
            body={"error_type": type(error).__name__, "message": str(error)},
        )
        self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task,
            attempts=[attempt],
            faults=[],
            events=events,
            artifact_refs=[artifact],
        )

    def _publish_compatibility_view(self, *, condition: Any) -> None:
        """保留 dispatcher output_root 下的只读兼容运行视图。"""

        source = self.evidence_store.output_root / "experiments" / condition.experiment_id
        if source == self.output_root:
            return
        shutil.copytree(source, self.output_root, dirs_exist_ok=True)

    def _checkpoint_budget_exhausted(
        self,
        *,
        condition: Any,
        task_id: str,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task = _record_with_context(
            {
                "task_id": task_id,
                "root_status": "budget_exhausted",
                "error_kind": "budget_limit",
            },
            condition=condition,
            task_id=task_id,
        )
        task.update(_evidence_flags())
        attempt = _record_with_context(
            _synthetic_attempt(condition=condition, task_id=task_id),
            condition=condition,
            task_id=task_id,
        )
        attempt.update(
            {
                "attempt_status": "cancelled_by_budget",
                "error_kind": "budget_limit",
                **_evidence_flags(),
            }
        )
        events = [
            _record_with_context(
                {
                    "event_id": (
                        f"formal-budget-exhausted-{condition.condition_id}-"
                        f"{condition.repeat_id}-{task_id}"
                    ),
                    "event_type": "BUDGET_EXHAUSTED",
                },
                condition=condition,
                task_id=task_id,
            ),
            *(
                _record_with_context(item, condition=condition, task_id=task_id)
                for item in extra_events
            ),
        ]
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                f"formal-budget-{condition.condition_id}-{task_id}-{index}",
            )
            event.update(_evidence_flags())
        artifact = _write_runner_artifact(
            suite_root=self.evidence_store.output_root,
            condition=condition,
            task_id=task_id,
            artifact_name="budget-exhausted.json",
            body={
                "status": "budget_exhausted",
                "hard_limits": _as_json(self.hard_limits),
                "usage": {
                    "provider_attempt_count": self.usage.provider_attempt_count,
                    "total_tokens": self.usage.total_tokens,
                    "total_cost_estimate": self.usage.total_cost_estimate,
                },
            },
        )
        self.evidence_store.checkpoint_root(
            experiment_id=condition.experiment_id,
            condition=_as_json(condition),
            repeat_id=condition.repeat_id,
            task=task,
            attempts=[attempt],
            faults=[],
            events=events,
            artifact_refs=[artifact],
        )


@dataclass
class _UsageTotals:
    """当前 runner 进程内已经消费的受限资源。"""

    provider_attempt_count: int = 0
    total_tokens: int = 0
    total_cost_estimate: float = 0.0
    reserved_provider_attempt_count: int = 0
    reserved_total_tokens: int = 0
    reserved_total_cost_estimate: float = 0.0


@dataclass(frozen=True, slots=True)
class _RootBudgetReservation:
    provider_attempt_count: int
    total_tokens: int
    total_cost_estimate: float


def _evidence_bodies(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    hard_limits: Mapping[str, Any],
    ai_api_configs: Mapping[str, Any],
) -> dict[str, Any]:
    """构造 FormalEvidenceStore 所需的稳定 suite identity bodies。"""

    plan_bodies = [_as_json(plan) for plan in plans]
    endpoint_plans: list[dict[str, Any]] = []
    request_limits: list[dict[str, Any]] = []
    for plan in plans:
        for condition, _selection in plan.bound_items():
            binding, limits, _config = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            endpoint_plans.append(
                {
                    "experiment_id": plan.experiment_id,
                    "condition_id": condition.condition_id,
                    "endpoint_binding": _as_json(binding),
                }
            )
            request_limits.append(
                {
                    "experiment_id": plan.experiment_id,
                    "condition_id": condition.condition_id,
                    "limits": _as_json(limits),
                }
            )
    return {
        "suite": {
            "schema_version": "tokenshare.paper_formal_runner.v1",
            "suite_id": "paper_formal_suite",
            "experiment_ids": [plan.experiment_id for plan in plans],
        },
        "dispatch": {
            "schema_version": "tokenshare.paper_dispatch.v1",
            "plans": plan_bodies,
        },
        "budget": _as_json(budget),
        "catalog": _as_json(catalog_manifest),
        "identity": {
            "schema_version": "tokenshare.paper_formal_runner_identity.v1",
            "endpoints": endpoint_plans,
        },
        "request_limits": {
            "schema_version": "tokenshare.paper_formal_runner_request_limits.v1",
            "conditions": request_limits,
        },
        "hard_limits": _as_json(hard_limits),
    }


def _stored_evidence_bodies(suite_root: Path) -> dict[str, Any]:
    """从冻结 suite manifest 恢复 replay 所需 identity，不读取 adapter。"""

    path = suite_root / "suite_manifest.json"
    if not path.is_file():
        raise ValueError("required evidence file is missing: suite_manifest.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    frozen = body.get("suite_identity")
    if not isinstance(frozen, Mapping):
        raise ValueError("suite identity evidence is missing")
    result: dict[str, Any] = {}
    for name in (
        "suite",
        "dispatch",
        "budget",
        "catalog",
        "identity",
        "request_limits",
        "hard_limits",
    ):
        component = frozen.get(name)
        if not isinstance(component, Mapping) or "body" not in component:
            raise ValueError(f"suite identity evidence is missing: {name}")
        result[name] = component["body"]
    return result


def _suite_result_from_evidence(suite_root: Path) -> PaperSuiteResult:
    path = suite_root / "formal_runner_result.json"
    if not path.is_file():
        raise ValueError("required evidence file is missing: formal_runner_result.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, Mapping):
        raise ValueError("formal runner result must be a JSON object")
    return PaperSuiteResult(
        suite_id=str(body["suite_id"]),
        status=PaperStatus(str(body["status"])),
        output_root=str(body["output_root"]),
        started_at=str(body["started_at"]),
        ended_at=body.get("ended_at"),
        experiment_ids=tuple(str(item) for item in body.get("experiment_ids", ())),
        condition_count=int(body["condition_count"]),
        run_count=int(body["run_count"]),
        task_count=int(body["task_count"]),
        provider_attempt_count=int(body["provider_attempt_count"]),
        total_tokens=int(body["total_tokens"]),
        total_cost_estimate=float(body["total_cost_estimate"]),
        paper_eligible=body["paper_eligible"] is True,
        eligibility_report_ref=_mapping_or_none(body.get("eligibility_report_ref")),
        budget_ref=_mapping_or_none(body.get("budget_ref")),
        metrics_refs=tuple(_as_json(item) for item in body.get("metrics_refs", ())),
        audit_refs=tuple(_as_json(item) for item in body.get("audit_refs", ())),
        error_summary=tuple(_as_json(item) for item in body.get("error_summary", ())),
        model_policy_preflight=_mapping_or_none(body.get("model_policy_preflight")),
        model_endpoint_cohort_preflight=_mapping_or_none(
            body.get("model_endpoint_cohort_preflight")
        ),
        schema_version=str(body.get("schema_version", "tokenshare.paper_suite_result.v1")),
    )


def _usage_from_evidence(suite_root: Path) -> _UsageTotals:
    """resume 时以已持久化 suite result 继续累计硬预算。"""

    path = suite_root / "formal_runner_result.json"
    if not path.is_file():
        return _UsageTotals()
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, Mapping):
        raise ValueError("formal runner result must be a JSON object")
    return _UsageTotals(
        provider_attempt_count=int(body.get("provider_attempt_count", 0)),
        total_tokens=int(body.get("total_tokens", 0)),
        total_cost_estimate=float(body.get("total_cost_estimate", 0.0)),
    )


def _all_selected_roots_completed(
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    completed_task_keys: set[tuple[str, str, str, str]],
) -> bool:
    return all(
        _formal_task_key(condition, case_id) in completed_task_keys
        for plan, items in bound_plans
        if plan.status == "planned"
        for condition, selection in items
        for case_id in selection.ordered_case_ids
    )


def _formal_task_key(condition: Any, task_id: str) -> tuple[str, str, str, str]:
    return (
        str(condition.experiment_id),
        str(condition.condition_id),
        str(condition.repeat_id),
        str(task_id),
    )


def _root_budget_reservation(
    *,
    case: Mapping[str, Any],
    request_limits: Mapping[str, Any],
    budget: PaperBudgetResult,
) -> _RootBudgetReservation:
    """按冻结 case 和 suite budget 上界预留一个 root 的并发额度。"""

    ai_unit_count = (
        estimated_ai_units_for_case(dict(case))
        if "schema_version" in case or "expected_ai_unit_count" in case
        else 1
    )
    attempts_per_unit = int(request_limits.get("max_provider_attempts", 1))
    attempt_count = ai_unit_count * max(1, attempts_per_unit)
    if budget.max_provider_attempts > 0:
        token_upper_per_attempt = (
            budget.token_upper_bound + budget.max_provider_attempts - 1
        ) // budget.max_provider_attempts
        cost_upper_per_attempt = (
            budget.cost_upper_bound / budget.max_provider_attempts
        )
    else:
        token_upper_per_attempt = int(request_limits.get("max_tokens", 0))
        cost_upper_per_attempt = 0.0
    return _RootBudgetReservation(
        provider_attempt_count=attempt_count,
        total_tokens=attempt_count * token_upper_per_attempt,
        total_cost_estimate=attempt_count * cost_upper_per_attempt,
    )


def _has_resource_hard_limit(hard_limits: Mapping[str, Any]) -> bool:
    return any(
        isinstance(hard_limits.get(field_name), (int, float))
        and not isinstance(hard_limits.get(field_name), bool)
        for field_name in (
            "max_total_provider_attempts",
            "max_provider_attempts",
            "max_total_tokens",
            "max_tokens",
            "max_cost_estimate",
            "max_total_cost_estimate",
        )
    )


def _reserve_hard_limit_capacity(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
    hard_limits: Mapping[str, Any],
) -> bool:
    projected = (
        (
            "max_total_provider_attempts",
            usage.provider_attempt_count
            + usage.reserved_provider_attempt_count
            + reservation.provider_attempt_count,
        ),
        (
            "max_provider_attempts",
            usage.provider_attempt_count
            + usage.reserved_provider_attempt_count
            + reservation.provider_attempt_count,
        ),
        (
            "max_total_tokens",
            usage.total_tokens
            + usage.reserved_total_tokens
            + reservation.total_tokens,
        ),
        (
            "max_tokens",
            usage.total_tokens
            + usage.reserved_total_tokens
            + reservation.total_tokens,
        ),
        (
            "max_cost_estimate",
            usage.total_cost_estimate
            + usage.reserved_total_cost_estimate
            + reservation.total_cost_estimate,
        ),
        (
            "max_total_cost_estimate",
            usage.total_cost_estimate
            + usage.reserved_total_cost_estimate
            + reservation.total_cost_estimate,
        ),
    )
    for field_name, value in projected:
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and value > limit
        ):
            return False
    usage.reserved_provider_attempt_count += reservation.provider_attempt_count
    usage.reserved_total_tokens += reservation.total_tokens
    usage.reserved_total_cost_estimate += reservation.total_cost_estimate
    return True


def _settle_hard_limit_reservation(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
    provider_attempt_count: int = 0,
    total_tokens: int = 0,
    total_cost_estimate: float = 0.0,
) -> None:
    usage.reserved_provider_attempt_count -= reservation.provider_attempt_count
    usage.reserved_total_tokens -= reservation.total_tokens
    usage.reserved_total_cost_estimate -= reservation.total_cost_estimate
    usage.provider_attempt_count += provider_attempt_count
    usage.total_tokens += total_tokens
    usage.total_cost_estimate += total_cost_estimate


def _hard_limit_reached(usage: _UsageTotals, hard_limits: Mapping[str, Any]) -> bool:
    limits = (
        ("max_total_provider_attempts", usage.provider_attempt_count),
        ("max_provider_attempts", usage.provider_attempt_count),
        ("max_total_tokens", usage.total_tokens),
        ("max_tokens", usage.total_tokens),
        ("max_cost_estimate", usage.total_cost_estimate),
        ("max_total_cost_estimate", usage.total_cost_estimate),
    )
    for field_name, consumed in limits:
        limit = hard_limits.get(field_name)
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and consumed >= limit:
            return True
    return False


def _condition_result(
    *,
    condition: Any,
    selection: Any,
    completed: int,
    blocked: int,
    failed: int,
    provider_attempts: int,
    paper_eligible: bool,
    status: PaperStatus,
) -> PaperConditionResult:
    return PaperConditionResult(
        condition_id=condition.condition_id,
        status=status,
        repeat_count=1,
        task_count=len(selection.ordered_case_ids),
        completed_root_count=completed,
        failed_root_count=failed,
        blocked_root_count=blocked,
        provider_attempt_count=provider_attempts,
        metrics_ref={"paper_eligible": paper_eligible},
    )


def _record_with_context(value: Any, *, condition: Any, task_id: str) -> dict[str, Any]:
    record = _as_json(value)
    if not isinstance(record, Mapping):
        raise ValueError("adapter evidence record must be an object")
    return {
        **dict(record),
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
    }


def _evidence_flags() -> dict[str, bool]:
    return {"formal": True, "pilot_only": False, "paper_eligible": False}


def _sequence_field(value: Any, *field_names: str) -> tuple[Any, ...]:
    for field_name in field_names:
        result = _optional_field(value, field_name)
        if result is None:
            continue
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
            return tuple(result)
        raise ValueError(f"adapter field {field_name} must be a sequence")
    return ()


def _synthetic_attempt(*, condition: Any, task_id: str) -> dict[str, Any]:
    return {
        "attempt_id": f"runner-{condition.condition_id}-{condition.repeat_id}-{task_id}",
        "attempt_status": "failed",
        "provider_attempt_index": 0,
        "error_kind": "runner_checkpoint",
    }


def _event_record(*, condition: Any, task_id: str, event_type: str) -> dict[str, Any]:
    return {
        "event_id": f"runner-{event_type.lower()}-{condition.condition_id}-{condition.repeat_id}-{task_id}",
        "event_type": event_type,
    }


def _adapter_artifact_refs(
    task: Any,
    attempts: Sequence[Mapping[str, Any]],
    faults: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    refs: list[Mapping[str, Any]] = []
    task_refs = _optional_field(task, "artifact_refs")
    if isinstance(task_refs, Sequence) and not isinstance(task_refs, (str, bytes, bytearray)):
        refs.extend(item for item in task_refs if isinstance(item, Mapping))
    for attempt in attempts:
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "parse_failure_ref",
            "provenance_ref",
            "usage_ref",
            "fault_injection_ref",
        ):
            ref = attempt.get(field_name)
            if isinstance(ref, Mapping):
                refs.append(ref)
    for fault in faults:
        for field_name in (
            "record_ref",
            "primitive_fault_record_ref",
            "original_raw_output_ref",
            "original_output_ref",
            "mutated_output_ref",
            "suppressed_output_ref",
            "original_provenance_ref",
            "mutated_provenance_ref",
            "pre_fault_usage_ref",
        ):
            ref = fault.get(field_name)
            if isinstance(ref, Mapping):
                refs.append(ref)
    unique: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        uri = ref.get("uri")
        content_hash = ref.get("content_hash")
        if isinstance(uri, str) and isinstance(content_hash, str) and (uri, content_hash) not in seen:
            seen.add((uri, content_hash))
            unique.append(ref)
    return tuple(unique)


def _materialize_artifacts(
    *,
    suite_root: Path,
    condition: Any,
    task_id: str,
    adapter_root: Path,
    source_refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    artifact_root = (
        suite_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
        / "artifacts"
        / _artifact_task_directory(task_id)
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, source_ref in enumerate(source_refs):
        uri = source_ref.get("uri")
        if not isinstance(uri, str) or not uri:
            continue
        source = (adapter_root / uri).resolve(strict=False)
        if not source.is_file():
            matches = [
                candidate
                for candidate in adapter_root.rglob(Path(uri).name)
                if candidate.is_file()
                and (
                    not isinstance(source_ref.get("content_hash"), str)
                    or _sha256_bytes(candidate.read_bytes())
                    == source_ref["content_hash"]
                )
            ]
            if len(matches) != 1:
                raise ValueError("adapter artifact evidence is missing")
            source = matches[0]
        name = Path(uri).name or f"artifact-{index}"
        if name in names:
            name = f"{index}-{name}"
        names.add(name)
        target = artifact_root / name
        shutil.copyfile(source, target)
        result.append(
            {
                "experiment_id": condition.experiment_id,
                "condition_id": condition.condition_id,
                "repeat_id": condition.repeat_id,
                "task_id": task_id,
                "path": target.relative_to(suite_root).as_posix(),
                "content_hash": _sha256_bytes(target.read_bytes()),
            }
        )
    return result


def _write_runner_artifact(
    *,
    suite_root: Path,
    condition: Any,
    task_id: str,
    artifact_name: str,
    body: Any,
) -> dict[str, Any]:
    path = (
        suite_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
        / "artifacts"
        / _artifact_task_directory(task_id)
        / artifact_name
    )
    _write_json(path, body)
    return {
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
        "path": path.relative_to(suite_root).as_posix(),
        "content_hash": _sha256_bytes(path.read_bytes()),
    }


def _as_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _as_json(value.to_dict())
    if is_dataclass(value):
        return _as_json(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_json(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "__dict__"):
        return _as_json(vars(value))
    raise ValueError(f"value is not JSON serializable: {type(value).__name__}")


def _artifact_task_directory(task_id: str) -> str:
    """避免同一 condition 内不同 root 的同名 adapter artifact 相互覆盖。"""

    return str(task_id).replace("\\", "_").replace("/", "_")


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _string_sequence(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{label} must be a sequence")
    return tuple(str(item) for item in value)


def _adapter_store_root(
    *,
    adapter_root: Path,
    case_id: str,
    attempts: Sequence[Any],
) -> Path:
    candidates = (adapter_root, adapter_root / case_id)
    for candidate in candidates:
        for attempt in attempts:
            raw_ref = _optional_field(attempt, "raw_output_ref")
            uri = raw_ref.get("uri") if isinstance(raw_ref, Mapping) else None
            if isinstance(uri, str) and (candidate / uri).is_file():
                return candidate
    raise ValueError("adapter artifact store root cannot be resolved")


def _adapter_result_with(
    original: Any,
    *,
    task_result: Any,
    attempts: Sequence[Any],
    faults: Sequence[Any],
    events: Sequence[Any],
) -> dict[str, Any]:
    return {
        "task_result": task_result,
        "attempt_results": tuple(attempts),
        "fault_records": tuple(faults),
        "event_records": tuple(events),
        "eligibility_report": _optional_field(original, "eligibility_report"),
    }


def _mapping_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_json(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_as_json(body), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _finalize_formal_manifests(
    *,
    suite_root: Path,
    suite_result: PaperSuiteResult,
    plans: Sequence[PaperExperimentDispatchPlan],
    condition_results: Sequence[PaperConditionResult],
) -> None:
    suite_path = suite_root / "suite_manifest.json"
    suite_manifest = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_manifest["status"] = _status_value(suite_result.status)
    suite_manifest["paper_eligible"] = suite_result.paper_eligible
    _write_json(suite_path, suite_manifest)
    results_by_id = {result.condition_id: result for result in condition_results}
    condition_rows: list[dict[str, Any]] = []
    for plan in plans:
        plan_results = [
            results_by_id[condition.condition_id]
            for condition in plan.conditions
            if condition.condition_id in results_by_id
        ]
        if plan.status == "blocked":
            experiment_status = PaperStatus.BLOCKED
        elif plan_results:
            experiment_status = _suite_status(plans=(plan,), results=plan_results)
        else:
            experiment_status = PaperStatus.PLANNED
        manifest_path = (
            suite_root
            / "experiments"
            / plan.experiment_id
            / "experiment_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = _status_value(experiment_status)
        manifest["paper_eligible"] = bool(plan_results) and all(
            isinstance(result.metrics_ref, Mapping)
            and result.metrics_ref.get("paper_eligible") is True
            for result in plan_results
        )
        _write_json(manifest_path, manifest)
        for condition in plan.conditions:
            result = results_by_id.get(condition.condition_id)
            if result is None:
                continue
            condition_rows.append(
                {
                    **result.to_dict(),
                    "experiment_id": plan.experiment_id,
                    "repeat_id": condition.repeat_id,
                    "formal": True,
                    "pilot_only": False,
                    "execution_scope": "formal_matrix",
                    "paper_eligible": (
                        isinstance(result.metrics_ref, Mapping)
                        and result.metrics_ref.get("paper_eligible") is True
                    ),
                }
            )
    path = suite_root / "condition_results.jsonl"
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in condition_rows
        ),
        encoding="utf-8",
    )


def _catalog_cases_by_id(catalog_manifest: Any) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for field_name in (
        "factorization_cases",
        "lean_cases",
        "lean_lemma_graph_cases",
    ):
        values = (
            catalog_manifest.get(field_name, ())
            if isinstance(catalog_manifest, Mapping)
            else getattr(catalog_manifest, field_name, ())
        )
        for case in values:
            if not isinstance(case, Mapping) or not isinstance(
                case.get("case_id"), str
            ):
                raise ValueError("formal catalog case is invalid")
            case_id = str(case["case_id"])
            if case_id in cases:
                raise ValueError("duplicate case_id in formal catalog")
            cases[case_id] = dict(case)
    return cases


def _required_field(value: Any, field_name: str) -> Any:
    result = _optional_field(value, field_name)
    if result is None:
        raise ValueError(f"adapter result requires {field_name}")
    return result


def _optional_field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)
