"""正式论文实验套件的最小通用调度层。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import shutil
from threading import Lock
from typing import Any

from tokenshare.executors.ai_api_config import AIAPIExecutorConfig
from tokenshare.experiments.paper_catalog import estimated_ai_units_for_case
from tokenshare.experiments.paper_catalog import PaperInputCatalogManifest
from tokenshare.experiments.paper_catalog_execution_view import (
    restore_catalog_execution_view,
)
from tokenshare.experiments.paper_dispatcher import (
    PaperExperimentDispatchPlan,
    dispatch_paper_case,
    dispatch_paper_condition,
)
from tokenshare.experiments.paper_experiment_contracts import PaperExecutionContext
from tokenshare.experiments.paper_formal_evidence import (
    FormalEvidenceStore,
    SharedEvidenceError,
)
from tokenshare.experiments.paper_formal_callbacks import (
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_faults import PaperFaultRuntimeHooks
from tokenshare.experiments.paper_exp2_scalability import EXP2_EXPERIMENT_ID
from tokenshare.experiments.paper_model_policy import (
    EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
    EXP5_DOMAIN_EXECUTION_CONTRACTS,
    exp5_provider_specific_reasoning_controls,
)
from tokenshare.experiments.paper_models import (
    PaperBudgetResult,
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    PaperSuiteResult,
    digest_json,
)
from tokenshare.experiments.paper_runner import validate_experiment_dependency_order
from tokenshare.experiments.paper_terminal_outcomes import (
    PaperEvidenceIntegrity,
    PaperOutcomeStatus,
    PaperTerminalOutcome,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ParsedCandidateContext,
    ParsedCandidateDirective,
    RawOutputContext,
    WorkerTerminationPolicy,
)
from tokenshare.plugins.factorization.schemas import (
    PLUGIN_VERSION as FACTORIZATION_PLUGIN_VERSION,
    RANGE_RESULT_SCHEMA_VERSION,
)
from tokenshare.plugins.lean_proof.schemas import (
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    PLUGIN_VERSION as LEAN_PLUGIN_VERSION,
    PROOF_CANDIDATE_PARSER_ID,
)


EXP5_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
APPROVED_ENDPOINT_BINDINGS_KEY = "__approved_endpoint_bindings__"


class PaperInfrastructureBlockedError(Exception):
    """基础设施/身份依赖失败；必须停止新的 provider dispatch。"""

    def __init__(
        self,
        message: str,
        *,
        evidence_integrity: PaperEvidenceIntegrity,
        failure_stage: str,
        failure_kind: str,
        condition_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.terminal_outcome = PaperTerminalOutcome(
            outcome_status=PaperOutcomeStatus.BLOCKED_DEPENDENCY,
            evidence_integrity=evidence_integrity,
            failure_stage=failure_stage,
            failure_kind=failure_kind,
        )
        self.condition_id = condition_id
        self.task_id = task_id

    def to_summary(self) -> dict[str, Any]:
        body: dict[str, Any] = self.terminal_outcome.to_dict()
        if self.condition_id is not None:
            body["condition_id"] = self.condition_id
        if self.task_id is not None:
            body["task_id"] = self.task_id
        return body


class _Exp3RuntimeHookBridge(NoOpRuntimeHooks):
    """同一 Exp3 hook 同时跨接 executor raw 点与 coordinator parsed 点。"""

    def __init__(
        self,
        *,
        condition: Any,
        case_id: str,
        fault_type: str,
        selected_unit_ids: Sequence[str],
        reserve_unit_ids: Sequence[str],
        runtime_records: list[dict[str, Any]],
    ) -> None:
        self._condition = condition
        self._case_id = case_id
        self._fault_type = fault_type
        self._selected_unit_ids = tuple(selected_unit_ids)
        self._reserve_unit_ids = tuple(reserve_unit_ids)
        self._runtime_records = runtime_records
        self._runtime_hook: PaperFaultRuntimeHooks | None = None
        self._lock = Lock()

    def __call__(self, **context: Any) -> Mapping[str, Any] | None:
        with self._lock:
            return self._after_raw_output(**context)

    def _after_raw_output(self, **context: Any) -> Mapping[str, Any] | None:
        request = context["request"]
        artifact_store = context["artifact_store"]
        runtime_hook = self._ensure_runtime_hook(artifact_store)
        submitted_at = str(context["submitted_at"])
        lease_deadline_at = request.soft_hints.get("lease_deadline_at")
        if self._fault_type in {"no_return", "late_submission"} and (
            not isinstance(lease_deadline_at, str)
            or not lease_deadline_at
        ):
            raise ValueError(f"{self._fault_type} requires protocol lease deadline")
        usage = dict(context["usage_summary"])
        before_count = len(runtime_hook.records)
        directive = runtime_hook.after_raw_output_persisted(
            RawOutputContext(
                run_id=f"{self._condition.condition_id}_{self._case_id}",
                task_id=request.task_id,
                unit_id=request.unit_id,
                attempt_id=request.attempt_id,
                worker_id=str(
                    request.allocation_decision.get(
                        "worker_id",
                        request.allocation_decision.get(
                            "client_id",
                            "formal-worker",
                        ),
                    )
                ),
                raw_output_ref=context["raw_output_ref"],
                provenance_ref=context["provenance_ref"],
                usage_ref=context["usage_ref"],
                content_text=str(context["content_text"]),
                provider=str(context["provider_family"]),
                model=str(context["model"]),
                entry_id=str(context["entry_id"]),
                usage_summary={
                    **usage,
                    "prompt_tokens": usage.get(
                        "prompt_tokens",
                        usage.get("input_tokens", 0),
                    ),
                    "completion_tokens": usage.get(
                        "completion_tokens",
                        usage.get("output_tokens", 0),
                    ),
                },
                submitted_at=submitted_at,
                experiment_unit_id=self._experiment_unit_id(request),
                lease_deadline_at=lease_deadline_at,
            )
        )
        self._capture_new_records(runtime_hook, before_count)
        if directive is None:
            return None
        return {
            key: value
            for key, value in {
                "content_text": directive.content_text,
                "result_kind": directive.result_kind,
            }.items()
            if value is not None
        }

    def after_parsed_candidate_persisted(
        self,
        context: ParsedCandidateContext,
    ) -> ParsedCandidateDirective | None:
        with self._lock:
            experiment_unit_id = (
                f"{self._case_id}:{context.experiment_unit_id}"
                if context.experiment_unit_id
                else None
            )
            target_id = experiment_unit_id or context.unit_id
            if target_id not in {
                *self._selected_unit_ids,
                *self._reserve_unit_ids,
            }:
                return None
            if self._runtime_hook is None:
                raise ValueError(
                    "Exp3 parsed-candidate hook requires prior raw observation"
                )
            before_count = len(self._runtime_hook.records)
            directive = self._runtime_hook.after_parsed_candidate_persisted(
                replace(
                    context,
                    experiment_unit_id=experiment_unit_id,
                )
            )
            self._capture_new_records(self._runtime_hook, before_count)
            return directive

    def _ensure_runtime_hook(
        self,
        artifact_store: ArtifactStore,
    ) -> PaperFaultRuntimeHooks:
        if not isinstance(artifact_store, ArtifactStore):
            raise ValueError("Exp3 post-raw hook requires ArtifactStore")
        if self._runtime_hook is None:
            self._runtime_hook = PaperFaultRuntimeHooks(
                artifact_store=artifact_store,
                condition_id=self._condition.condition_id,
                repeat_id=self._condition.repeat_id,
                fault_type=self._fault_type,
                seed=int(self._condition.seed),
                selected_unit_ids=self._selected_unit_ids,
                reserve_unit_ids=self._reserve_unit_ids,
            )
        return self._runtime_hook

    def _experiment_unit_id(self, request: Any) -> str | None:
        planned_ai_unit_id = request.soft_hints.get("planned_ai_unit_id")
        return (
            f"{self._case_id}:{planned_ai_unit_id}"
            if isinstance(planned_ai_unit_id, str) and planned_ai_unit_id
            else None
        )

    def _capture_new_records(
        self,
        runtime_hook: PaperFaultRuntimeHooks,
        before_count: int,
    ) -> None:
        self._runtime_records.extend(
            dict(record) for record in runtime_hook.records[before_count:]
        )


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
    root_case_filter: Mapping[str, Sequence[str]] | None = None,
    execution_classification: Mapping[str, Any] | None = None,
    suite_id: str = "paper_formal_suite",
    pre_execution_documents: Mapping[str, Any] | None = None,
    recovery_documents: Mapping[str, Any] | None = None,
) -> PaperSuiteResult:
    """校验冻结计划并通过注册 dispatcher 顺序执行 planned conditions。"""

    plans = tuple(dispatch_plans)
    if resume and replay_only:
        raise ValueError("resume and replay_only are mutually exclusive")
    if replay_only:
        return replay_paper_formal_suite(output_root=output_root)
    normalized_root_filter = _normalize_root_case_filter(
        plans=plans,
        root_case_filter=root_case_filter,
    )
    classification = _normalize_execution_classification(
        execution_classification
    )
    frozen_pre_execution_documents = _normalize_pre_execution_documents(
        pre_execution_documents
    )
    frozen_recovery_documents = _normalize_recovery_documents(
        recovery_documents
    )
    if frozen_recovery_documents and not resume:
        raise ValueError("recovery documents require resume mode")
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
        root_case_filter=normalized_root_filter,
    )
    suite_root = Path(output_root)
    started_at = _utc_now()
    bodies = _evidence_bodies(
        plans=plans,
        catalog_manifest=catalog_manifest,
        budget=budget,
        hard_limits=hard_limits,
        ai_api_configs=ai_api_configs,
        root_case_filter=normalized_root_filter,
        execution_classification=classification,
        suite_id=suite_id,
        pre_execution_documents=frozen_pre_execution_documents,
    )
    if resume:
        evidence_store = FormalEvidenceStore(suite_root)
        evidence_store.archive_uncheckpointed_adapter_runs()
        evidence_store.repair_stale_compatibility_manifest()
        loaded = FormalEvidenceStore.load(
            output_root=suite_root,
            **{f"expected_{name}": body for name, body in bodies.items()},
        )
        _persist_pre_execution_documents(
            suite_root=suite_root,
            documents=frozen_recovery_documents,
        )
        evidence_store._refresh_evidence_manifest()
        completed_task_keys = set(loaded.completed_task_keys)
        if _all_selected_roots_completed(
            bound_plans,
            completed_task_keys,
            root_case_filter=normalized_root_filter,
        ):
            return _suite_result_from_evidence(suite_root)
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
    _persist_pre_execution_documents(
        suite_root=suite_root,
        documents=frozen_pre_execution_documents,
    )
    evidence_store._refresh_evidence_manifest()
    results: list[PaperConditionResult] = []
    try:
        _dispatch_formal_conditions(
            bound_plans=bound_plans,
            catalog_manifest=catalog_manifest,
            ai_api_configs=ai_api_configs,
            transport=transport,
            real_transport=real_transport,
            hard_limits=hard_limits,
            evidence_store=evidence_store,
            completed_task_keys=completed_task_keys,
            usage=usage,
            budget=budget,
            normalized_root_filter=normalized_root_filter,
            classification=classification,
            results=results,
        )
    except Exception as error:
        blocked_error = (
            error
            if isinstance(error, PaperInfrastructureBlockedError)
            else PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="runner_internal",
                failure_kind=type(error).__name__,
            )
        )
        return _close_blocked_formal_suite(
            suite_root=suite_root,
            suite_id=suite_id,
            started_at=started_at,
            plans=plans,
            bound_plans=bound_plans,
            condition_results=results,
            blocked_error=blocked_error,
            evidence_store=evidence_store,
            root_case_filter=normalized_root_filter,
            usage=usage,
            budget=budget,
            budget_approval=budget_approval,
            execution_classification=classification,
        )

    suite_result = PaperSuiteResult(
        suite_id=suite_id,
        status=_classified_suite_status(
            _suite_status(plans=plans, results=results),
            classification=classification,
        ),
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
        total_cost_estimate=usage.reportable_total_cost_estimate(),
        cost_estimate_by_currency=(
            dict(sorted(usage.cost_estimate_by_currency.items()))
            if usage.cost_estimate_by_currency
            else None
        ),
        total_cost_estimate_status=usage.cost_estimate_status(),
        paper_eligible=_suite_paper_eligible(
            plans=plans,
            results=results,
            transport=transport,
            real_transport=real_transport,
            execution_classification=classification,
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
        execution_classification=classification,
    )
    # runner 结果同样属于可 replay 的 suite evidence，写入后刷新索引。
    FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    return suite_result


def _dispatch_formal_conditions(
    *,
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    catalog_manifest: Any,
    ai_api_configs: Mapping[str, Any],
    transport: Any,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    evidence_store: FormalEvidenceStore,
    completed_task_keys: set[tuple[str, str, str, str]],
    usage: "_UsageTotals",
    budget: PaperBudgetResult,
    normalized_root_filter: Mapping[str, tuple[str, ...]],
    classification: Mapping[str, Any] | None,
    results: list[PaperConditionResult],
) -> None:
    for plan, bound_items in bound_plans:
        if plan.status == "blocked":
            continue
        plan_root = Path(plan.output_root)
        plan_root.mkdir(parents=True, exist_ok=True)
        for condition, _selection in bound_items:
            endpoint_binding, request_limits, config = _condition_endpoint_contract(
                experiment_id=plan.experiment_id,
                condition=condition,
                ai_api_configs=ai_api_configs,
            )
            context_catalog = _execution_catalog_for_plan(
                plan=plan,
                catalog_manifest=catalog_manifest,
            )
            callback = _FormalConditionExecutionCallback(
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
                root_case_ids=normalized_root_filter.get(
                    condition.condition_id
                ),
                execution_classification=classification,
            )
            context = PaperExecutionContext(
                context_id=(
                    f"paper_formal_{plan.experiment_id}_{condition.condition_id}"
                ),
                catalog=context_catalog,
                approved_endpoint_binding=endpoint_binding,
                request_limits=request_limits,
                hard_limits=dict(hard_limits),
                output_root=plan_root.as_posix(),
                artifact_store=object(),
                event_store=object(),
                execution_callback=callback,
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


def _close_blocked_formal_suite(
    *,
    suite_root: Path,
    suite_id: str,
    started_at: str,
    plans: Sequence[PaperExperimentDispatchPlan],
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    condition_results: Sequence[PaperConditionResult],
    blocked_error: PaperInfrastructureBlockedError,
    evidence_store: FormalEvidenceStore,
    root_case_filter: Mapping[str, tuple[str, ...]],
    usage: "_UsageTotals",
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    execution_classification: Mapping[str, Any] | None,
) -> PaperSuiteResult:
    summary = blocked_error.to_summary()
    persistence_failures: list[dict[str, Any]] = []
    failure_checkpointed = False
    selected_task_count = 0
    for plan, bound_items in bound_plans:
        for condition, selection in bound_items:
            case_ids = root_case_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            selected_task_count += len(case_ids)
            existing = _validated_condition_task_ids(
                evidence_store=evidence_store,
                condition=condition,
            )
            for case_id in case_ids:
                if case_id in existing:
                    continue
                is_failure_root = (
                    blocked_error.condition_id == condition.condition_id
                    and blocked_error.task_id == case_id
                )
                if (
                    not failure_checkpointed
                    and blocked_error.condition_id is None
                ):
                    is_failure_root = True
                root_status = "blocked" if is_failure_root else "not_started"
                try:
                    _checkpoint_dependency_outcome(
                        evidence_store=evidence_store,
                        suite_root=suite_root,
                        condition=condition,
                        task_id=case_id,
                        root_status=root_status,
                        blocked_error=blocked_error,
                        execution_classification=execution_classification,
                    )
                    if is_failure_root:
                        failure_checkpointed = True
                except Exception as persistence_error:
                    persistence_failures.append(
                        {
                            "condition_id": condition.condition_id,
                            "task_id": case_id,
                            "failure_kind": type(persistence_error).__name__,
                            "message": str(persistence_error),
                        }
                    )

    closed_results = _blocked_condition_results(
        bound_plans=bound_plans,
        suite_root=suite_root,
        root_case_filter=root_case_filter,
        existing_results=condition_results,
    )
    terminal_status: PaperStatus | str = (
        PaperStatus.INCOMPLETE
        if persistence_failures
        else PaperStatus.BLOCKED
    )
    error_summary: tuple[dict[str, Any], ...] = (
        summary,
        *(
            (
                {
                    "outcome_status": "blocked_dependency",
                    "evidence_integrity": "invalid",
                    "failure_stage": "manifest_closure",
                    "failure_kind": "persistence_failure",
                    "failures": persistence_failures,
                },
            )
            if persistence_failures
            else ()
        ),
    )
    suite_result = PaperSuiteResult(
        suite_id=suite_id,
        status=terminal_status,
        output_root=suite_root.as_posix(),
        started_at=started_at,
        ended_at=_utc_now(),
        experiment_ids=tuple(plan.experiment_id for plan in plans),
        condition_count=sum(len(items) for _plan, items in bound_plans),
        run_count=len(closed_results),
        task_count=selected_task_count,
        provider_attempt_count=usage.provider_attempt_count,
        total_tokens=usage.total_tokens,
        total_cost_estimate=usage.reportable_total_cost_estimate(),
        cost_estimate_by_currency=(
            dict(sorted(usage.cost_estimate_by_currency.items()))
            if usage.cost_estimate_by_currency
            else None
        ),
        total_cost_estimate_status=usage.cost_estimate_status(),
        paper_eligible=False,
        eligibility_report_ref=None,
        budget_ref={
            "budget_digest": budget.budget_digest,
            "approval_mode": budget_approval["approval_mode"],
        },
        metrics_refs=(),
        audit_refs=(),
        error_summary=error_summary,
    )
    _write_json(suite_root / "formal_runner_result.json", suite_result.to_dict())
    _finalize_formal_manifests(
        suite_root=suite_root,
        suite_result=suite_result,
        plans=plans,
        condition_results=closed_results,
        execution_classification=execution_classification,
    )
    try:
        FormalEvidenceStore(suite_root)._refresh_evidence_manifest()
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        suite_result = replace(
            suite_result,
            status=PaperStatus.INCOMPLETE,
            error_summary=(
                *suite_result.error_summary,
                {
                    "outcome_status": "blocked_dependency",
                    "evidence_integrity": "invalid",
                    "failure_stage": "evidence_manifest_closure",
                    "failure_kind": type(error).__name__,
                    "message": str(error),
                },
            ),
        )
        _write_json(suite_root / "formal_runner_result.json", suite_result.to_dict())
        _finalize_formal_manifests(
            suite_root=suite_root,
            suite_result=suite_result,
            plans=plans,
            condition_results=closed_results,
            execution_classification=execution_classification,
        )
    return suite_result


def _validated_condition_task_ids(
    *,
    evidence_store: FormalEvidenceStore,
    condition: Any,
) -> set[str]:
    run_root = (
        evidence_store.output_root
        / "experiments"
        / condition.experiment_id
        / "runs"
        / condition.condition_id
        / str(condition.repeat_id)
    )
    if not run_root.is_dir():
        return set()
    try:
        tasks = evidence_store._validate_run(
            run_root,
            experiment_id=condition.experiment_id,
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return set()
    return {str(task["task_id"]) for task in tasks}


def _checkpoint_dependency_outcome(
    *,
    evidence_store: FormalEvidenceStore,
    suite_root: Path,
    condition: Any,
    task_id: str,
    root_status: str,
    blocked_error: PaperInfrastructureBlockedError,
    execution_classification: Mapping[str, Any] | None,
) -> None:
    terminal = blocked_error.terminal_outcome.to_dict()
    event_type = (
        "EXPERIMENT_DEPENDENCY_BLOCKED"
        if root_status == "blocked"
        else "EXPERIMENT_NOT_STARTED_AFTER_DEPENDENCY_BLOCK"
    )
    event_id = (
        f"formal-{event_type.lower()}-{condition.condition_id}-"
        f"{condition.repeat_id}-{task_id}"
    )
    artifact = _write_runner_artifact(
        suite_root=suite_root,
        condition=condition,
        task_id=task_id,
        artifact_name=(
            "dependency-blocked.json"
            if root_status == "blocked"
            else "not-started.json"
        ),
        body={
            **terminal,
            "root_status": root_status,
            "condition_id": condition.condition_id,
            "task_id": task_id,
            "message": str(blocked_error),
        },
    )
    common = {
        "experiment_id": condition.experiment_id,
        "condition_id": condition.condition_id,
        "repeat_id": condition.repeat_id,
        "task_id": task_id,
        **terminal,
        **_evidence_flags(
            paper_eligible=False,
            execution_classification=execution_classification,
        ),
        "paper_ineligibility_reasons": ["blocked_dependency"],
        "record_scope": "experiment",
    }
    task = {
        **common,
        "root_status": root_status,
        "error_kind": terminal["failure_kind"],
        "event_refs": [{"event_id": event_id, "event_type": event_type}],
        "evidence_artifact_refs": [artifact],
    }
    attempt = {
        **common,
        "attempt_id": (
            f"experiment-{root_status}-{condition.condition_id}-"
            f"{condition.repeat_id}-{task_id}"
        ),
        "attempt_status": (
            "blocked_dependency" if root_status == "blocked" else "not_started"
        ),
        "provider_attempt_index": 0,
        "error_kind": terminal["failure_kind"],
    }
    event = {
        **common,
        "event_id": event_id,
        "event_type": event_type,
    }
    evidence_store.checkpoint_root(
        experiment_id=condition.experiment_id,
        condition=_as_json(condition),
        repeat_id=condition.repeat_id,
        task=task,
        attempts=[attempt],
        faults=[],
        events=[event],
        artifact_refs=[artifact],
    )


def _blocked_condition_results(
    *,
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    suite_root: Path,
    root_case_filter: Mapping[str, tuple[str, ...]],
    existing_results: Sequence[PaperConditionResult],
) -> list[PaperConditionResult]:
    existing_by_id = {result.condition_id: result for result in existing_results}
    results: list[PaperConditionResult] = []
    for _plan, bound_items in bound_plans:
        for condition, selection in bound_items:
            prior = existing_by_id.get(condition.condition_id)
            if prior is not None:
                results.append(prior)
                continue
            case_ids = root_case_filter.get(
                condition.condition_id,
                tuple(selection.ordered_case_ids),
            )
            run_root = (
                suite_root
                / "experiments"
                / condition.experiment_id
                / "runs"
                / condition.condition_id
                / str(condition.repeat_id)
            )
            tasks: list[dict[str, Any]] = []
            attempts: list[dict[str, Any]] = []
            try:
                store = FormalEvidenceStore(suite_root)
                tasks = store._validate_run(
                    run_root,
                    experiment_id=condition.experiment_id,
                )
                pointer = json.loads(
                    (run_root / "CURRENT.json").read_text(encoding="utf-8")
                )
                generation_root = (
                    run_root / ".generations" / str(pointer["generation_id"])
                )
                attempts = _read_jsonl_records(
                    generation_root / "per_attempt_results.jsonl"
                )
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                pass
            task_integrities = {
                str(task.get("evidence_integrity"))
                for task in tasks
                if task.get("evidence_integrity")
                in {"complete", "missing", "invalid", "corrupt"}
            }
            condition_integrity = (
                next(iter(task_integrities))
                if len(task_integrities) == 1
                else "missing"
            )
            outcome_counts = {
                "succeeded": sum(
                    _status_value(task.get("root_status", "")) == "completed"
                    for task in tasks
                ),
                "failed_experimental": sum(
                    task.get("outcome_status") == "failed_experimental"
                    for task in tasks
                ),
                "blocked_dependency": sum(
                    task.get("outcome_status") == "blocked_dependency"
                    for task in tasks
                ),
                "not_started": sum(
                    task.get("root_status") == "not_started" for task in tasks
                ),
            }
            results.append(
                PaperConditionResult(
                    condition_id=condition.condition_id,
                    status=PaperStatus.BLOCKED,
                    repeat_count=1,
                    task_count=len(case_ids),
                    completed_root_count=outcome_counts["succeeded"],
                    failed_root_count=outcome_counts["failed_experimental"],
                    blocked_root_count=outcome_counts["blocked_dependency"],
                    provider_attempt_count=_persisted_provider_attempt_count(
                        attempts
                    ),
                    metrics_ref={
                        "paper_eligible": False,
                        "paper_ineligibility_reasons": [
                            "blocked_dependency"
                        ],
                        "evidence_refs": [],
                        "infrastructure_blocked": True,
                        "evidence_integrity": condition_integrity,
                        "outcome_counts": outcome_counts,
                    },
                )
            )
    return results


def _dependency_integrity_for_error(
    error: Exception,
) -> PaperEvidenceIntegrity:
    if isinstance(error, SharedEvidenceError):
        return PaperEvidenceIntegrity(error.evidence_integrity)
    if isinstance(error, FileNotFoundError):
        return PaperEvidenceIntegrity.MISSING
    if isinstance(error, json.JSONDecodeError):
        return PaperEvidenceIntegrity.CORRUPT
    return PaperEvidenceIntegrity.INVALID


def _execution_version_identity(
    *,
    domain: str,
    split_profile_digest: str,
    runtime_generation_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if domain == "factorization":
        plugin_version = FACTORIZATION_PLUGIN_VERSION
        parser_version = "factorization.range_result.parser.v1"
        verifier_version = (
            f"factorization.plugin_verifier@{FACTORIZATION_PLUGIN_VERSION}"
        )
        prompt_version = RANGE_RESULT_SCHEMA_VERSION
    elif domain == "lean_proof":
        plugin_version = LEAN_PLUGIN_VERSION
        parser_version = PROOF_CANDIDATE_PARSER_ID
        verifier_version = f"lean_proof.local_checker@{LEAN_PLUGIN_VERSION}"
        prompt_version = LEAN_PROOF_CANDIDATE_SCHEMA_VERSION
    else:
        raise ValueError("shared reference domain is unsupported")
    body: dict[str, Any] = {
        "schema_version": "tokenshare.paper_execution_version_identity.v1",
        "plugin_version": plugin_version,
        "parser_version": parser_version,
        "verifier_version": verifier_version,
        "executor_version": "0.1.0",
        "prompt_version": prompt_version,
        "split_profile_digest": split_profile_digest,
        "runtime_generation_schema_version": (
            "tokenshare.paper_runtime_generation_identity.v1"
        ),
    }
    if runtime_generation_identity is not None:
        body["runtime_generation_identity_digest"] = digest_json(
            runtime_generation_identity
        )
    return body


def _budget_split_profile_digest(
    *,
    budget: Any,
    condition_id: str,
    case_id: str,
) -> str | None:
    quota_preflight = getattr(budget, "quota_preflight", None)
    commitments = (
        quota_preflight.get("budget_commitments")
        if isinstance(quota_preflight, Mapping)
        else None
    )
    items = (
        commitments.get("ai_unit_commitments")
        if isinstance(commitments, Mapping)
        else None
    )
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return None
    matches = [
        item.get("split_profile_digest")
        for item in items
        if isinstance(item, Mapping)
        and item.get("condition_id") == condition_id
        and item.get("case_id") == case_id
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        return None
    return matches[0]


def _shared_reference_hash_matches(reference: Mapping[str, Any]) -> bool:
    source_hash = reference.get("source_hash")
    core = {
        key: value
        for key, value in reference.items()
        if key
        not in {
            "source_hash",
            "source_reference_id",
            "planned_source_reference_id",
            "reference_policy_id",
        }
    }
    return isinstance(source_hash, str) and source_hash == digest_json(core)


def _shared_reference_versions_match(
    reference: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    actual = reference.get("source_versions")
    if not isinstance(actual, Mapping):
        return False
    if any(
        actual.get(field_name) != expected_value
        for field_name, expected_value in expected.items()
    ):
        return False
    runtime_digest = actual.get("runtime_generation_identity_digest")
    return (
        isinstance(runtime_digest, str)
        and runtime_digest.startswith("sha256:")
        and len(runtime_digest) == 71
    )


def _execution_catalog_for_plan(
    *,
    plan: PaperExperimentDispatchPlan,
    catalog_manifest: Any,
) -> Any:
    """从 plan 冻结 identity 恢复执行 view；旧 synthetic plan 保持兼容。"""

    frozen = plan.catalog_execution_view
    if frozen is None:
        return catalog_manifest
    if not isinstance(catalog_manifest, PaperInputCatalogManifest):
        raise ValueError(
            "frozen catalog execution view requires PaperInputCatalogManifest"
        )
    return restore_catalog_execution_view(
        frozen,
        catalog_manifest=catalog_manifest,
    )


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
    root_case_filter: Mapping[str, tuple[str, ...]],
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
    if any(
        not isinstance(plan, PaperExperimentDispatchPlan) for plan in dispatch_plans
    ):
        raise ValueError("dispatch_plans must contain dispatch plans")
    validate_experiment_dependency_order(
        tuple(plan.experiment_id for plan in dispatch_plans)
    )

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
    catalog_cases = _catalog_cases_by_id(catalog_manifest)
    headline_root_runs_by_experiment = {
        plan.experiment_id: sum(
            len(
                root_case_filter.get(
                    condition.condition_id,
                    tuple(selection.ordered_case_ids),
                )
            )
            for condition, selection in items
        )
        for plan, items in bound_plans
        if plan.status == "planned"
    }
    headline_ai_units_by_experiment = {
        plan.experiment_id: sum(
            (
                _selected_ai_unit_count(
                    selection=selection,
                    case_ids=root_case_filter[condition.condition_id],
                    catalog_cases=catalog_cases,
                )
                if root_case_filter
                else selection.expected_ai_unit_count
            )
            for condition, selection in items
        )
        for plan, items in bound_plans
        if plan.status == "planned"
    }
    _validate_budget_execution_totals(
        budget=budget,
        planned_experiment_ids=tuple(
            plan.experiment_id for plan in planned
        ),
        headline_root_runs_by_experiment=headline_root_runs_by_experiment,
        headline_ai_units_by_experiment=headline_ai_units_by_experiment,
    )
    return tuple(bound_plans)


def _selected_ai_unit_count(
    *,
    selection: Any,
    case_ids: Sequence[str],
    catalog_cases: Mapping[str, Mapping[str, Any]],
) -> int:
    """按 selection 冻结的逐 case 口径计算 smoke AI-unit 分母。"""

    raw_case_counts = getattr(selection, "case_expected_ai_unit_counts", None)
    if raw_case_counts is None:
        return sum(
            estimated_ai_units_for_case(dict(catalog_cases[case_id]))
            for case_id in case_ids
        )
    if not isinstance(raw_case_counts, Mapping):
        raise ValueError("selection case AI-unit commitments must be a mapping")
    ordered_case_ids = tuple(str(case_id) for case_id in selection.ordered_case_ids)
    if set(raw_case_counts) != set(ordered_case_ids):
        raise ValueError("selection case AI-unit commitment coverage mismatch")
    normalized_counts: dict[str, int] = {}
    for case_id, count in raw_case_counts.items():
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("selection case AI-unit commitment ID is invalid")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("selection case AI-unit commitment count is invalid")
        normalized_counts[case_id] = count
    if sum(normalized_counts.values()) != selection.expected_ai_unit_count:
        raise ValueError("selection case AI-unit commitment total mismatch")
    if any(case_id not in normalized_counts for case_id in case_ids):
        raise ValueError("selected case is absent from AI-unit commitments")
    return sum(normalized_counts[case_id] for case_id in case_ids)


def _validate_budget_execution_totals(
    *,
    budget: PaperBudgetResult,
    planned_experiment_ids: tuple[str, ...],
    headline_root_runs_by_experiment: Mapping[str, int],
    headline_ai_units_by_experiment: Mapping[str, int],
) -> None:
    """校验 headline plan 与额外支撑执行共同组成的冻结预算身份。"""

    quota_preflight = budget.quota_preflight
    commitments = (
        quota_preflight.get("budget_commitments")
        if isinstance(quota_preflight, Mapping)
        else None
    )
    identity = (
        commitments.get("experiment_budget_identity")
        if isinstance(commitments, Mapping)
        else None
    )
    if identity is None:
        if budget.planned_root_runs != sum(
            headline_root_runs_by_experiment.values()
        ):
            raise ValueError("budget planned root run count mismatch")
        if budget.planned_ai_units != sum(
            headline_ai_units_by_experiment.values()
        ):
            raise ValueError("budget planned AI unit count mismatch")
        return
    if not isinstance(identity, Mapping):
        raise ValueError("budget experiment identity must be a mapping")
    if (
        identity.get("schema_version")
        != "tokenshare.paper_experiment_budget_identity.v1"
    ):
        raise ValueError("unsupported budget experiment identity schema")

    experiment_ids = set(planned_experiment_ids)

    def require_count_map(
        field_name: str,
        *,
        allow_omitted_zeroes: bool = False,
    ) -> dict[str, int]:
        value = identity.get(field_name)
        if (
            not isinstance(value, Mapping)
            or (
                set(value) > experiment_ids
                if allow_omitted_zeroes
                else set(value) != experiment_ids
            )
        ):
            raise ValueError(
                f"budget experiment identity {field_name} coverage mismatch"
            )
        result: dict[str, int] = (
            {experiment_id: 0 for experiment_id in experiment_ids}
            if allow_omitted_zeroes
            else {}
        )
        for experiment_id, count in value.items():
            if (
                not isinstance(experiment_id, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                raise ValueError(
                    f"budget experiment identity {field_name} is invalid"
                )
            result[experiment_id] = count
        return result

    frozen_headline_roots = require_count_map(
        "headline_root_runs_by_experiment"
    )
    supporting_roots = require_count_map(
        "supporting_baseline_root_runs_by_experiment",
        allow_omitted_zeroes=True,
    )
    actual_roots = require_count_map(
        "actual_scheduled_root_runs_by_experiment"
    )
    frozen_headline_ai_units = require_count_map(
        "headline_ai_units_by_experiment"
    )
    supporting_ai_units = require_count_map(
        "supporting_baseline_ai_units_by_experiment",
        allow_omitted_zeroes=True,
    )
    first_attempt_ai_units = require_count_map(
        "planned_first_attempt_ai_units_by_experiment"
    )
    if frozen_headline_roots != dict(headline_root_runs_by_experiment):
        raise ValueError("budget headline root run identity mismatch")
    if frozen_headline_ai_units != dict(headline_ai_units_by_experiment):
        raise ValueError("budget headline AI unit identity mismatch")
    if actual_roots != {
        experiment_id: (
            frozen_headline_roots[experiment_id]
            + supporting_roots[experiment_id]
        )
        for experiment_id in planned_experiment_ids
    }:
        raise ValueError("budget actual scheduled root run identity mismatch")
    if first_attempt_ai_units != {
        experiment_id: (
            frozen_headline_ai_units[experiment_id]
            + supporting_ai_units[experiment_id]
        )
        for experiment_id in planned_experiment_ids
    }:
        raise ValueError("budget first-attempt AI unit identity mismatch")
    if budget.planned_root_runs != sum(actual_roots.values()):
        raise ValueError("budget planned root run count mismatch")
    if budget.planned_ai_units != sum(first_attempt_ai_units.values()):
        raise ValueError("budget planned AI unit count mismatch")


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


def _classified_suite_status(
    status: PaperStatus,
    *,
    classification: Mapping[str, Any] | None,
) -> PaperStatus:
    """smoke 的 root 失败保留证据并收敛为 completed_with_failures。"""

    if classification is None:
        return status
    if status in {PaperStatus.FAILED, PaperStatus.BLOCKED}:
        return PaperStatus.COMPLETED_WITH_FAILURES
    return status


def _normalize_execution_classification(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    body = dict(value)
    expected = {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "execution_scope": "smoke_suite",
    }
    if any(body.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("unsupported execution classification")
    reasons = body.get("ineligibility_reasons")
    if not isinstance(reasons, list) or not {
        "smoke_suite",
        "pilot_only",
    }.issubset(reasons):
        raise ValueError("smoke execution classification reasons are incomplete")
    if any(not isinstance(reason, str) or not reason for reason in reasons):
        raise ValueError("smoke execution classification reasons are invalid")
    baseline_policy = body.get("baseline_policy", "required_by_formal_plan")
    if baseline_policy not in {
        "required_by_formal_plan",
        "omitted_for_smoke_regression",
    }:
        raise ValueError("unsupported smoke baseline policy")
    if (
        baseline_policy == "omitted_for_smoke_regression"
        and "smoke_baseline_not_requested" not in reasons
    ):
        raise ValueError("smoke baseline omission reason is missing")
    return {
        **expected,
        "ineligibility_reasons": list(dict.fromkeys(reasons)),
        "baseline_policy": baseline_policy,
    }


def _normalize_root_case_filter(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    root_case_filter: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    if root_case_filter is None:
        return {}
    if not isinstance(root_case_filter, Mapping):
        raise ValueError("root_case_filter must be a mapping")
    expected: dict[str, tuple[str, ...]] = {
        condition.condition_id: tuple(selection.ordered_case_ids)
        for plan in plans
        if plan.status == "planned"
        for condition, selection in plan.bound_items()
    }
    if set(root_case_filter) != set(expected):
        raise ValueError("root_case_filter condition coverage mismatch")
    normalized: dict[str, tuple[str, ...]] = {}
    for condition_id, raw_case_ids in root_case_filter.items():
        if isinstance(raw_case_ids, (str, bytes, bytearray)) or not isinstance(
            raw_case_ids, Sequence
        ):
            raise ValueError("root_case_filter values must be sequences")
        case_ids = tuple(str(case_id) for case_id in raw_case_ids)
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("root_case_filter values must be non-empty and unique")
        if any(not case_id or case_id not in expected[condition_id] for case_id in case_ids):
            raise ValueError("root_case_filter references a non-canonical case")
        normalized[str(condition_id)] = case_ids
    return normalized


def _normalize_pre_execution_documents(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("pre_execution_documents must be a mapping")
    allowed = {
        "smoke_profile.json",
        "smoke_execution_plan.json",
        "smoke_launch_manifest.json",
    }
    if set(value).difference(allowed):
        raise ValueError("unsupported pre-execution document")
    normalized: dict[str, Any] = {}
    for name, body in value.items():
        if not isinstance(body, Mapping):
            raise ValueError("pre-execution documents must be JSON objects")
        normalized[str(name)] = _as_json(body)
    return normalized


def _normalize_recovery_documents(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("recovery_documents must be a mapping")
    normalized: dict[str, Any] = {}
    for raw_name, body in value.items():
        name = str(raw_name).replace("\\", "/")
        parts = name.split("/")
        filename = parts[-1] if parts else ""
        if (
            len(parts) != 2
            or parts[0] != "smoke_recoveries"
            or not filename.startswith("smoke_recovery_")
            or not filename.endswith(".json")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("invalid recovery document path")
        if not isinstance(body, Mapping):
            raise ValueError("recovery documents must be JSON objects")
        normalized[name] = _as_json(body)
    return normalized


def _persist_pre_execution_documents(
    *,
    suite_root: Path,
    documents: Mapping[str, Any],
) -> None:
    for name, body in documents.items():
        path = suite_root / name
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != body:
                raise ValueError(f"pre-execution document identity mismatch: {name}")
            continue
        _write_json(path, body)


def _suite_paper_eligible(
    *,
    plans: tuple[PaperExperimentDispatchPlan, ...],
    results: list[PaperConditionResult],
    transport: Any,
    real_transport: bool,
    execution_classification: Mapping[str, Any] | None = None,
) -> bool:
    if execution_classification is not None:
        return False
    if not real_transport or _is_offline_capturing_transport(transport):
        return False
    if not results or any(not plan.paper_eligible_possible for plan in plans):
        return False
    expected_condition_ids = {
        condition.condition_id
        for plan in plans
        if plan.status != "blocked"
        for condition in plan.conditions
    }
    if {result.condition_id for result in results} != expected_condition_ids:
        return False
    return all(
        isinstance(result.metrics_ref, Mapping)
        and result.metrics_ref.get("paper_eligible") is True
        and bool(result.metrics_ref.get("evidence_refs"))
        and int(result.metrics_ref.get("task_evidence_count", 0))
        == result.task_count
        and int(result.metrics_ref.get("attempt_evidence_count", 0)) > 0
        for result in results
    )


def _audit_persisted_condition_evidence(
    *,
    suite_root: Path,
    experiment_id: str,
    condition_id: str,
    repeat_id: int,
    expected_task_ids: Sequence[str],
) -> dict[str, Any]:
    """从已提交 generation 聚合 attempt→task→condition，不读取 adapter 汇总布尔值。"""

    reasons: list[str] = []
    run_root = (
        suite_root
        / "experiments"
        / experiment_id
        / "runs"
        / condition_id
        / str(repeat_id)
    )
    try:
        tasks = FormalEvidenceStore(suite_root)._validate_run(
            run_root,
            experiment_id=experiment_id,
        )
        pointer = json.loads((run_root / "CURRENT.json").read_text(encoding="utf-8"))
        generation_root = run_root / ".generations" / str(pointer["generation_id"])
        attempts = _read_jsonl_records(
            generation_root / "per_attempt_results.jsonl"
        )
        events = _read_jsonl_records(generation_root / "events" / "event_log.jsonl")
        artifacts = _read_jsonl_records(
            generation_root / "artifacts" / "artifact_index.jsonl"
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        tasks, attempts, events, artifacts = [], [], [], []
        generation_root = run_root
        reasons.append("persisted_condition_evidence_invalid")

    expected = tuple(str(task_id) for task_id in expected_task_ids)
    actual = tuple(str(task.get("task_id")) for task in tasks)
    if len(set(expected)) != len(expected) or set(actual) != set(expected):
        reasons.append("condition_task_inventory_mismatch")
    if not tasks or any(task.get("paper_eligible") is not True for task in tasks):
        reasons.append("persisted_task_not_paper_eligible")
    if any(task.get("record_scope") != "protocol" for task in tasks):
        reasons.append("persisted_task_not_protocol_scoped")
    if not attempts or any(
        attempt.get("paper_eligible") is not True for attempt in attempts
    ):
        reasons.append("persisted_attempt_not_paper_eligible")
    if any(attempt.get("record_scope") != "protocol" for attempt in attempts):
        reasons.append("persisted_attempt_not_protocol_scoped")
    task_ids = set(actual)
    if not events or any(
        event.get("task_id") not in task_ids for event in events
    ):
        reasons.append("persisted_event_inventory_incomplete")
    artifact_task_ids = {
        str(artifact.get("task_id")) for artifact in artifacts if artifact.get("task_id")
    }
    if not artifacts or not task_ids.issubset(artifact_task_ids):
        reasons.append("persisted_artifact_inventory_incomplete")
    event_ids = {
        str(event.get("event_id"))
        for event in events
        if isinstance(event.get("event_id"), str) and event.get("event_id")
    }
    if any(
        not _persisted_task_refs_resolve(
            task,
            event_ids=event_ids,
            artifacts=artifacts,
        )
        for task in tasks
    ) or any(
        not _persisted_attempt_refs_resolve(attempt, artifacts=artifacts)
        for attempt in attempts
    ):
        reasons.append("persisted_evidence_ref_unresolved")

    completed = sum(
        _status_value(task.get("root_status") or "") == "completed" for task in tasks
    )
    blocked = sum(
        _status_value(task.get("root_status") or "") == "blocked" for task in tasks
    )
    failed = len(expected) - completed - blocked
    evidence_refs: list[dict[str, Any]] = []
    for relative in (
        "per_task_results.jsonl",
        "per_attempt_results.jsonl",
        "events/event_log.jsonl",
        "artifacts/artifact_index.jsonl",
    ):
        path = generation_root / relative
        if not path.is_file():
            reasons.append("persisted_condition_evidence_ref_missing")
            continue
        evidence_refs.append(
            {
                "path": path.relative_to(suite_root).as_posix(),
                "content_hash": _sha256_bytes(path.read_bytes()),
            }
        )
    stable_reasons = list(dict.fromkeys(reasons))
    return {
        "paper_eligible": not stable_reasons,
        "paper_ineligibility_reasons": stable_reasons,
        "task_evidence_count": len(tasks),
        "attempt_evidence_count": len(attempts),
        "completed_root_count": completed,
        "failed_root_count": max(0, failed),
        "blocked_root_count": blocked,
        "provider_attempt_count": _persisted_provider_attempt_count(attempts),
        "evidence_refs": evidence_refs,
    }


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _persisted_provider_attempt_count(
    attempts: Sequence[Mapping[str, Any]],
) -> int:
    """只统计 protocol attempt 中实际持久化的 provider 调用 inventory。"""

    count = 0
    for attempt in attempts:
        if attempt.get("record_scope") != "protocol":
            continue
        provider_attempts = attempt.get("provider_attempts")
        if isinstance(provider_attempts, Sequence) and not isinstance(
            provider_attempts,
            (str, bytes, bytearray),
        ):
            count += len(provider_attempts)
            continue
        persisted_count = attempt.get("provider_attempt_count")
        if (
            isinstance(persisted_count, int)
            and not isinstance(persisted_count, bool)
            and persisted_count > 0
        ):
            count += persisted_count
            continue
        provider_attempt_index = attempt.get("provider_attempt_index")
        if (
            isinstance(provider_attempt_index, int)
            and not isinstance(provider_attempt_index, bool)
            and provider_attempt_index > 0
            and isinstance(attempt.get("provider"), str)
            and bool(attempt.get("provider"))
        ):
            count += 1
    return count


def _persisted_task_refs_resolve(
    task: Mapping[str, Any],
    *,
    event_ids: set[str],
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    event_refs = task.get("event_refs")
    artifact_refs = task.get("evidence_artifact_refs") or task.get("artifact_refs")
    if (
        not isinstance(event_refs, Sequence)
        or isinstance(event_refs, (str, bytes))
        or not event_refs
        or not isinstance(artifact_refs, Sequence)
        or isinstance(artifact_refs, (str, bytes))
        or not artifact_refs
    ):
        return False
    return all(
        isinstance(ref, Mapping) and ref.get("event_id") in event_ids
        for ref in event_refs
    ) and all(
        _persisted_artifact_ref_resolves(ref, artifacts)
        for ref in artifact_refs
    )


def _persisted_attempt_refs_resolve(
    attempt: Mapping[str, Any],
    *,
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    field_names = [
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ]
    status = _status_value(attempt.get("attempt_status") or "")
    if status in {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    }:
        field_names.append("raw_output_ref")
    if status == "parse_failed":
        field_names.append("parse_failure_ref")
    return all(
        _persisted_artifact_ref_resolves(attempt.get(field_name), artifacts)
        for field_name in field_names
    )


def _persisted_artifact_ref_resolves(
    value: Any,
    artifacts: Sequence[Mapping[str, Any]],
) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for artifact in artifacts:
        if value.get("artifact_id") is not None and (
            artifact.get("artifact_id") != value.get("artifact_id")
        ):
            continue
        if value.get("path") is not None and artifact.get("path") != value.get("path"):
            continue
        if value.get("content_hash") is not None and (
            artifact.get("content_hash") != value.get("content_hash")
        ):
            continue
        if any(
            value.get(field_name) is not None
            for field_name in ("artifact_id", "path", "uri")
        ):
            return True
    return False


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
    if provider_family == "siliconflow":
        expected_thinking = reasoning_profile_id == "thinking"
        if request_limits.get("enable_thinking") is not expected_thinking:
            raise ValueError(
                "SiliconFlow formal execution thinking control does not match profile"
            )
    if provider_family == "deepseek" and (
        request_limits.get("thinking") != {"type": "enabled"}
        or request_limits.get("reasoning_effort") != "high"
    ):
        raise ValueError(
            "DeepSeek formal execution requires thinking enabled and high reasoning"
        )
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
    if not isinstance(member_controls, Mapping):
        raise ValueError("Exp5 approved member request controls are invalid")
    comparable = member_controls.get("comparable")
    if not isinstance(comparable, Mapping):
        raise ValueError("Exp5 approved member comparable controls are missing")
    comparable_fields = approved_binding.get(
        "comparable_request_control_fields",
        EXP5_COMPARABLE_REQUEST_CONTROL_FIELDS,
    )
    if not isinstance(comparable_fields, (list, tuple)):
        raise ValueError("Exp5 approved comparable control field list is invalid")
    expected_comparable = {
        field_name: derived_binding["request_controls"].get(field_name)
        for field_name in comparable_fields
    }
    expected_comparable["domain_contracts"] = {
        domain: dict(contract)
        for domain, contract in EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
    }
    if dict(comparable) != expected_comparable:
        raise ValueError("Exp5 approved member request controls mismatch")
    if member_controls.get("comparable_digest") != digest_json(
        expected_comparable
    ):
        raise ValueError("Exp5 approved member request controls digest mismatch")
    reasoning_controls = member_controls.get("provider_specific_reasoning")
    if not isinstance(reasoning_controls, Mapping):
        raise ValueError("Exp5 approved member reasoning controls are missing")
    expected_reasoning_controls = exp5_provider_specific_reasoning_controls(
        derived_binding["request_controls"]
    )
    if dict(reasoning_controls) != expected_reasoning_controls:
        raise ValueError("Exp5 approved member reasoning controls mismatch")
    if member_controls.get("provider_specific_reasoning_digest") != digest_json(
        expected_reasoning_controls
    ):
        raise ValueError("Exp5 approved member reasoning digest mismatch")
    snapshot = approved_binding.get("request_controls_snapshot")
    if (
        not isinstance(snapshot, Mapping)
        or dict(snapshot) != expected_comparable
        or approved_binding.get("request_controls_snapshot_digest")
        != digest_json(expected_comparable)
    ):
        raise ValueError("Exp5 approved cohort request controls snapshot mismatch")
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
    cost_estimate_currency: str | None = None
    cost_estimate_status: str | None = None
    paper_eligible: bool = False
    provider_latency_ms: float = 0.0
    provider_error_kind: str | None = None
    error: Exception | None = None
    runtime_records: tuple[dict[str, Any], ...] = ()
    experiment_records: tuple[dict[str, Any], ...] = ()
    matched_baseline_evidence_ref: dict[str, Any] | None = None


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
    root_case_ids: tuple[str, ...] | None
    execution_classification: Mapping[str, Any] | None
    usage_lock: Lock = field(default_factory=Lock, compare=False, repr=False)
    def __call__(self, **kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        cases_by_id = _catalog_cases_by_id(self.catalog_manifest)
        selected_case_ids = (
            self.root_case_ids
            if self.root_case_ids is not None
            else tuple(selection.ordered_case_ids)
        )
        completed = sum(
            _formal_task_key(condition, case_id) in self.completed_task_keys
            for case_id in selected_case_ids
        )
        blocked = 0
        failed = 0
        provider_attempts = 0
        budget_exhausted = False
        pending_case_ids = tuple(
            case_id
            for case_id in selected_case_ids
            if _formal_task_key(condition, case_id) not in self.completed_task_keys
        )
        if self.hard_limits.get("stop_after_current_task") is True:
            pending_case_ids = pending_case_ids[:1]
        # root case 始终由 runner 顺序观察；condition.worker_count 交给 runtime backend。
        worker_count = 1
        strategy = run_scheduled_cases(
            ordered_case_ids=pending_case_ids,
            worker_count=worker_count,
            execute_case=lambda case_id, worker_id: self._dispatch_root_case(
                condition=condition,
                selection=selection,
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
            self._checkpoint_adapter_result(
                condition=condition,
                task_id=case_id,
                task=outcome.task,
                adapter_result=outcome.adapter_result,
                adapter_root=outcome.adapter_root,
                extra_events=scheduling_events,
            )

        self._publish_compatibility_view(condition=condition)

        persisted = _audit_persisted_condition_evidence(
            suite_root=self.evidence_store.output_root,
            experiment_id=condition.experiment_id,
            condition_id=condition.condition_id,
            repeat_id=condition.repeat_id,
            expected_task_ids=selected_case_ids,
        )
        completed = int(persisted["completed_root_count"])
        failed = int(persisted["failed_root_count"])
        blocked = int(persisted["blocked_root_count"])
        provider_attempts = int(persisted["provider_attempt_count"])
        status = (
            PaperStatus.BUDGET_EXHAUSTED
            if budget_exhausted
            else PaperStatus.COMPLETED
            if completed == len(selected_case_ids)
            else PaperStatus.BLOCKED
            if blocked == len(selected_case_ids)
            else PaperStatus.COMPLETED_WITH_FAILURES
        )
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=status,
            repeat_count=1,
            task_count=len(selected_case_ids),
            completed_root_count=completed,
            failed_root_count=failed,
            blocked_root_count=blocked,
            provider_attempt_count=provider_attempts,
            metrics_ref={
                "paper_eligible": persisted["paper_eligible"],
                "paper_ineligibility_reasons": persisted[
                    "paper_ineligibility_reasons"
                ],
                "task_evidence_count": persisted["task_evidence_count"],
                "attempt_evidence_count": persisted["attempt_evidence_count"],
                "evidence_refs": persisted["evidence_refs"],
                **strategy.metrics,
            },
        )

    def _dispatch_root_case(
        self,
        *,
        condition: Any,
        selection: Any,
        case_id: str,
        case: Any,
        worker_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        if case is None:
            raise ValueError("frozen selection case is absent from catalog")
        case = _case_with_selection_split_profile(case, selection)
        condition = _condition_with_frozen_case_metadata(
            condition=condition,
            case=case,
        )
        try:
            matched_baseline_evidence_ref = (
                self._ensure_exp3_shared_exp1_reference(
                    condition=condition,
                    case_id=case_id,
                    case=case,
                    callback_kwargs=callback_kwargs,
                )
            )
        except (RuntimeError, OSError, TimeoutError) as error:
            raise PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="shared_exp1_reference",
                failure_kind=type(error).__name__,
                condition_id=condition.condition_id,
                task_id=case_id,
            ) from error
        reservation = (
            _root_budget_reservation(
                case=case,
                request_limits=self.request_limits,
                budget=self.budget,
                currency=_config_currency(self.config),
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
        experiment_records: list[dict[str, Any]] = []
        post_raw_output_hook = self._exp3_post_raw_output_hook(
            condition=condition,
            case_id=case_id,
            callback_kwargs=callback_kwargs,
            runtime_records=experiment_records,
        )
        ablation_mode = None
        if condition.experiment_id == "exp4_real_ai_protocol_ablation":
            mode_config = callback_kwargs.get("mode_config")
            ablation_mode = str(
                _optional_field(mode_config, "ablation_mode")
                or condition.ablation_mode
            )
        worker_termination_policy = self._exp3_worker_termination_policy(
            condition=condition,
            case_id=case_id,
            callback_kwargs=callback_kwargs,
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
                worker_termination_policy=worker_termination_policy,
            )
        except (RuntimeError, OSError, TimeoutError) as error:
            with self.usage_lock:
                _settle_hard_limit_reservation(
                    usage=self.usage,
                    reservation=reservation,
                )
            raise PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="adapter_runtime",
                failure_kind=type(error).__name__,
                condition_id=condition.condition_id,
                task_id=case_id,
            ) from error
        task = _required_field(adapter_result, "task_result")
        provider_attempt_count = int(
            _required_field(task, "provider_attempt_count")
        )
        total_tokens = int(_optional_field(task, "total_tokens") or 0)
        cost_estimate = float(_optional_field(task, "cost_estimate") or 0.0)
        cost_estimate_currency = _optional_field(task, "cost_estimate_currency")
        cost_estimate_status = _optional_field(task, "cost_estimate_status")
        with self.usage_lock:
            _settle_hard_limit_reservation(
                usage=self.usage,
                reservation=reservation,
                provider_attempt_count=provider_attempt_count,
                total_tokens=total_tokens,
                total_cost_estimate=cost_estimate,
                cost_estimate_currency=(
                    str(cost_estimate_currency)
                    if isinstance(cost_estimate_currency, str)
                    else None
                ),
                cost_estimate_status=(
                    str(cost_estimate_status)
                    if isinstance(cost_estimate_status, str)
                    else None
                ),
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
            cost_estimate_currency=(
                str(cost_estimate_currency)
                if isinstance(cost_estimate_currency, str)
                else None
            ),
            cost_estimate_status=(
                str(cost_estimate_status)
                if isinstance(cost_estimate_status, str)
                else None
            ),
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
            experiment_records=tuple(experiment_records),
            matched_baseline_evidence_ref=matched_baseline_evidence_ref,
        )

    def _ensure_exp3_shared_exp1_reference(
        self,
        *,
        condition: Any,
        case_id: str,
        case: Mapping[str, Any],
        callback_kwargs: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        execution_manifest = _required_mapping(
            callback_kwargs.get("execution_manifest"),
            "Exp3 execution_manifest",
        )
        baseline_policy = (
            self.execution_classification.get("baseline_policy")
            if isinstance(self.execution_classification, Mapping)
            else None
        ) or execution_manifest.get(
            "baseline_policy",
            "shared_exp1_reference",
        )
        if baseline_policy == "omitted_for_smoke_regression":
            if self.execution_classification is None:
                raise ValueError(
                    "formal Exp3 scope cannot omit shared Exp1 evidence"
                )
            return None
        if baseline_policy != "shared_exp1_reference":
            raise ValueError("unsupported Exp3 baseline policy")
        reference_policy = _required_mapping(
            execution_manifest.get("matched_baseline"),
            "Exp3 shared Exp1 reference policy",
        )
        if (
            reference_policy.get("source_kind") != "shared_exp1_reference"
            or reference_policy.get("comparison_kind") != "shared_reference"
            or reference_policy.get("additional_execution_required") is not False
            or reference_policy.get("source_experiment_id")
            != "exp1_real_ai_feasibility"
            or reference_policy.get("source_repeat_id") != 0
            or reference_policy.get("source_seed") != 1
            or reference_policy.get("source_worker_count") != 10
        ):
            raise ValueError("Exp3 shared Exp1 reference policy identity mismatch")
        planned_ids = _required_mapping(
            reference_policy.get("source_reference_ids_by_case"),
            "Exp3 source_reference_ids_by_case",
        )
        planned_reference_id = planned_ids.get(case_id)
        if not isinstance(planned_reference_id, str) or not planned_reference_id:
            raise ValueError("Exp3 shared Exp1 case reference is missing")
        request_limits = _required_mapping(
            reference_policy.get("request_limits"),
            "Exp3 shared reference request limits",
        )
        if dict(request_limits) != dict(self.request_limits):
            raise ValueError("Exp3 shared reference request limits mismatch")
        expected_identity = {
            "domain": condition.domain,
            "difficulty": condition.difficulty,
            "paper_difficulty": condition.paper_difficulty,
            "topic_family": condition.topic_family,
            "worker_count": 10,
            "repeat_id": 0,
            "seed": 1,
            "catalog_digest": condition.catalog_digest,
            "provider_config_id": condition.provider_config_id,
            "model_entry_id": condition.model_entry_id,
            "provider_family": condition.provider_family,
            "provider_model_id": condition.provider_model_id,
            "reasoning_profile_id": condition.reasoning_profile_id,
            "source_provider_config_digest": (
                condition.source_provider_config_digest
            ),
            "model_endpoint_identity_digest": (
                condition.model_endpoint_identity_digest
            ),
            "request_limits": dict(self.request_limits),
        }
        split_profile_digest = _budget_split_profile_digest(
            budget=getattr(self, "budget", None),
            condition_id=condition.condition_id,
            case_id=case_id,
        ) or digest_json(case.get("split_params", {}))
        expected_source_versions = _execution_version_identity(
            domain=condition.domain,
            split_profile_digest=split_profile_digest,
            runtime_generation_identity=None,
        )
        try:
            reference = self.evidence_store.build_shared_root_reference(
                source_experiment_id="exp1_real_ai_feasibility",
                case_id=case_id,
                source_repeat_id=0,
                expected_condition_identity=expected_identity,
                expected_source_versions=expected_source_versions,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PaperInfrastructureBlockedError(
                str(error),
                evidence_integrity=_dependency_integrity_for_error(error),
                failure_stage="shared_exp1_reference",
                failure_kind="source_evidence_unavailable",
                condition_id=condition.condition_id,
                task_id=case_id,
            ) from error
        if not _shared_reference_versions_match(
            reference,
            expected_source_versions,
        ):
            raise PaperInfrastructureBlockedError(
                "shared Exp1 reference version identity mismatch",
                evidence_integrity=PaperEvidenceIntegrity.INVALID,
                failure_stage="shared_exp1_reference",
                failure_kind="source_version_identity_mismatch",
                condition_id=condition.condition_id,
                task_id=case_id,
            )
        if not _shared_reference_hash_matches(reference):
            raise PaperInfrastructureBlockedError(
                "shared Exp1 reference hash mismatch",
                evidence_integrity=PaperEvidenceIntegrity.CORRUPT,
                failure_stage="shared_exp1_reference",
                failure_kind="source_reference_hash_mismatch",
                condition_id=condition.condition_id,
                task_id=case_id,
            )
        return {
            **reference,
            "planned_source_reference_id": planned_reference_id,
            "reference_policy_id": reference_policy.get("reference_policy_id"),
        }

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
        """构造同时覆盖 raw 与 parsed-candidate 边界的稳定 runtime hook。"""

        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        manifest = callback_kwargs.get("execution_manifest")
        if not isinstance(manifest, Mapping):
            return None
        target_manifest = manifest.get("fault_target_manifest")
        if not isinstance(target_manifest, Mapping):
            return None
        selected = _string_sequence(
            target_manifest.get("selected_target_ai_unit_ids", ()),
            "selected_target_ai_unit_ids",
        )
        reserve = _string_sequence(
            target_manifest.get("reserve_target_ai_unit_ids", ()),
            "reserve_target_ai_unit_ids",
        )
        fault_type = str(target_manifest.get("fault_type") or condition.fault_type)
        return _Exp3RuntimeHookBridge(
            condition=condition,
            case_id=case_id,
            fault_type=fault_type,
            selected_unit_ids=selected,
            reserve_unit_ids=reserve,
            runtime_records=runtime_records,
        )

    def _exp3_worker_termination_policy(
        self,
        *,
        condition: Any,
        case_id: str,
        callback_kwargs: Mapping[str, Any],
    ) -> WorkerTerminationPolicy | None:
        if condition.experiment_id != "exp3_real_ai_fault_recovery":
            return None
        manifest = callback_kwargs.get("execution_manifest")
        if not isinstance(manifest, Mapping):
            return None
        worker_manifest = manifest.get("worker_death_manifest")
        if not isinstance(worker_manifest, Mapping):
            return None
        targets_by_case = _required_mapping(
            worker_manifest.get("selected_target_ai_unit_ids_by_case"),
            "selected_target_ai_unit_ids_by_case",
        )
        frozen_targets = _string_sequence(
            targets_by_case.get(case_id),
            f"selected worker death targets for {case_id}",
        )
        prefix = f"{case_id}:"
        planned_targets = tuple(
            target[len(prefix) :] if target.startswith(prefix) else target
            for target in frozen_targets
        )
        expected_count = int(worker_manifest["dead_worker_count_target"])
        if not planned_targets or len(planned_targets) > expected_count:
            raise ValueError(
                "worker death planned targets do not fit manifest condition"
            )
        progress = int(worker_manifest["kill_progress_target_percent"])
        return WorkerTerminationPolicy(
            target_planned_ai_unit_ids=planned_targets,
            termination_count_target=expected_count,
            kill_point=f"progress_{progress}",
            total_planned_ai_unit_count=int(
                _required_mapping(
                    worker_manifest.get("planned_ai_unit_count_by_case"),
                    "planned_ai_unit_count_by_case",
                )[case_id]
            ),
            process_timeout_seconds=max(
                30.0,
                float(self.request_limits["timeout_seconds"]) + 30.0,
            ),
        )

    def _apply_exp3_rate_fault(
        self,
        *,
        condition: Any,
        case: Mapping[str, Any],
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        """只投影 runtime 已执行的 fault/recovery，不再次调用 adapter。"""

        del case
        task_body = self._exp3_task_with_baseline(
            task=_as_json(outcome.task),
            outcome=outcome,
            manifest=manifest,
        )
        target_manifest = _required_mapping(
            manifest.get("fault_target_manifest"),
            "Exp3 fault_target_manifest",
        )
        selected_unit_ids = set(
            _string_sequence(
                target_manifest.get("selected_target_ai_unit_ids"),
                "selected_target_ai_unit_ids",
            )
        )
        reserve_unit_ids = set(
            _string_sequence(
                target_manifest.get("reserve_target_ai_unit_ids", ()),
                "reserve_target_ai_unit_ids",
            )
        )
        if not selected_unit_ids:
            return replace(
                outcome,
                task=task_body,
                adapter_result=_adapter_result_with(
                    outcome.adapter_result,
                    task_result=task_body,
                    attempts=_sequence_field(
                        outcome.adapter_result,
                        "attempt_results",
                        "attempts",
                    ),
                    faults=_sequence_field(
                        outcome.adapter_result,
                        "fault_records",
                        "faults",
                    ),
                    events=_sequence_field(
                        outcome.adapter_result,
                        "event_records",
                    ),
                ),
            )
        candidate_target_ids = selected_unit_ids | reserve_unit_ids
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        runtime_faults = tuple(
            dict(record)
            for record in outcome.experiment_records
            if str(record.get("selected_target_ai_unit_id"))
            in candidate_target_ids
            and record.get("applicability_status", "injected") == "injected"
        )
        not_applicable_records = tuple(
            dict(record)
            for record in outcome.experiment_records
            if str(record.get("selected_target_ai_unit_id"))
            in candidate_target_ids
            and record.get("applicability_status") == "not_applicable"
        )
        fault_attempt_ids = {
            str(record.get("attempt_id"))
            for record in runtime_faults
            if record.get("attempt_id") is not None
        }
        faulted_protocol_unit_ids = {
            str(record.get("unit_id"))
            for record in runtime_faults
            if record.get("unit_id") is not None
        }
        replacement_attempts = tuple(
            attempt
            for attempt in attempts
            if str(_required_field(attempt, "unit_id"))
            in faulted_protocol_unit_ids
            and str(_required_field(attempt, "attempt_id")) not in fault_attempt_ids
        )
        approved_identity = {
            "provider_family": condition.provider_family,
            "provider_model_id": condition.provider_model_id,
            "model_entry_id": condition.model_entry_id,
        }
        for attempt in replacement_attempts:
            _validate_runtime_attempt_identity(attempt, approved_identity)
        task_body["fault_injected"] = bool(runtime_faults)
        task_body["fault_applicability"] = {
            "candidate_target_count": int(
                target_manifest.get(
                    "candidate_target_count",
                    len(candidate_target_ids),
                )
            ),
            "selected_target_count": int(
                target_manifest.get(
                    "selected_target_count",
                    len(selected_unit_ids),
                )
            ),
            "eligible_target_count": len(runtime_faults),
            "injected_target_count": len(runtime_faults),
            "not_applicable_target_count": len(not_applicable_records),
            "injection_denominator": len(runtime_faults),
            "denominator_kind": "eligible_parsed_candidates",
        }
        task_body["fault_applicability_records"] = list(
            not_applicable_records
        )
        task_body["recovered_fault_target_count"] = len(
            {
                str(_required_field(attempt, "unit_id"))
                for attempt in replacement_attempts
            }
        )
        protocol_event_refs = tuple(
            dict(ref)
            for ref in task_body.get("event_refs", ())
            if isinstance(ref, Mapping)
        )
        observation_events = tuple(
            {
                "event_type": "EXPERIMENT_FAULT_OBSERVED",
                "condition_id": condition.condition_id,
                "unit_id": record.get("unit_id"),
                "selected_target_ai_unit_id": record.get(
                    "selected_target_ai_unit_id"
                ),
                "attempt_id": record.get("attempt_id"),
                "fault_type": record.get("fault_type"),
                "protocol_event_refs": list(protocol_event_refs),
                "artifact_refs": [
                    dict(ref)
                    for ref in (
                        record.get("original_output_ref"),
                        record.get("original_provenance_ref"),
                        record.get("pre_fault_usage_ref"),
                        record.get("mutated_output_ref"),
                        record.get("record_ref"),
                    )
                    if isinstance(ref, Mapping)
                ],
            }
            for record in runtime_faults
        )
        adapter_result = _adapter_result_with(
            outcome.adapter_result,
            task_result=task_body,
            attempts=attempts,
            faults=(
                *_sequence_field(outcome.adapter_result, "fault_records", "faults"),
                *runtime_faults,
            ),
            events=(
                *_sequence_field(outcome.adapter_result, "event_records"),
                *observation_events,
            ),
        )
        return replace(outcome, task=task_body, adapter_result=adapter_result)

    def _exp3_task_with_baseline(
        self,
        *,
        task: dict[str, Any],
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        policy = (
            self.execution_classification.get("baseline_policy")
            if isinstance(self.execution_classification, Mapping)
            else None
        ) or manifest.get("baseline_policy", "shared_exp1_reference")
        if policy == "omitted_for_smoke_regression":
            task.update(
                {
                    "baseline": None,
                    "baseline_comparison_eligible": False,
                    "baseline_unavailable_reason": (
                        "smoke_baseline_not_requested"
                    ),
                }
            )
            return task
        reference = outcome.matched_baseline_evidence_ref
        if reference is None:
            raise ValueError("Exp3 result is missing shared Exp1 evidence")
        task.update(
            {
                "baseline": dict(reference),
                "matched_baseline_condition_id": str(
                    reference["source_condition_id"]
                ),
                "matched_baseline_evidence_ref": dict(reference),
                "shared_exp1_reference": dict(reference),
                "baseline_comparison_eligible": bool(
                    reference.get("baseline_comparison_eligible")
                ),
                "baseline_unavailable_reason": reference.get(
                    "baseline_unavailable_reason"
                ),
            }
        )
        if isinstance(manifest.get("matched_baseline"), Mapping):
            task["matched_baseline"] = _as_json(manifest["matched_baseline"])
        return task

    def _apply_exp3_worker_death(
        self,
        *,
        condition: Any,
        case_id: str,
        outcome: "_RootExecutionOutcome",
        manifest: Mapping[str, Any],
    ) -> "_RootExecutionOutcome":
        del case_id
        worker_manifest = _required_mapping(
            manifest.get("worker_death_manifest"),
            "Exp3 worker_death_manifest",
        )
        attempts = _sequence_field(
            outcome.adapter_result,
            "attempt_results",
            "attempts",
        )
        faults = tuple(
            item
            for item in _sequence_field(
                outcome.adapter_result,
                "fault_records",
                "faults",
            )
            if _optional_field(item, "fault_type") == "worker_death"
            or _optional_field(item, "schema_version")
            in {
                "tokenshare.paper_worker_death.v1",
                "tokenshare.paper_worker_death_incomplete.v1",
            }
        )
        dead_attempts = tuple(
            attempt
            for attempt in attempts
            if _status_value(_optional_field(attempt, "attempt_status") or "")
            == "worker_died"
        )
        dead_attempt_units = {
            str(_required_field(attempt, "unit_id"))
            for attempt in dead_attempts
        }
        recovered_units = {
            str(_required_field(attempt, "unit_id"))
            for attempt in attempts
            if _status_value(_optional_field(attempt, "attempt_status") or "")
            == "succeeded"
            and str(_required_field(attempt, "unit_id")) in dead_attempt_units
        }
        attempt_count_by_dead_unit = {
            unit_id: sum(
                1
                for attempt in attempts
                if str(_required_field(attempt, "unit_id")) == unit_id
            )
            for unit_id in dead_attempt_units
        }
        replacement_process_count = sum(
            max(0, count - 1)
            for count in attempt_count_by_dead_unit.values()
        )
        target_count = int(worker_manifest["dead_worker_count_target"])
        coordinator_survived = bool(faults) and all(
            _optional_field(
                _required_mapping(
                    _optional_field(record, "coordinator"),
                    "worker death coordinator evidence",
                ),
                "survived",
            )
            is True
            for record in faults
        )
        evidence_complete = (
            len(faults) == target_count
            and len(dead_attempts) == target_count
            and recovered_units == dead_attempt_units
            and replacement_process_count >= target_count
            and coordinator_survived
        )
        task_body = self._exp3_task_with_baseline(
            task=_as_json(outcome.task),
            outcome=outcome,
            manifest=manifest,
        )
        task_body.update(
            {
                "dead_worker_count_target": target_count,
                "kill_progress_target_percent": int(
                    worker_manifest["kill_progress_target_percent"]
                ),
                "worker_death_count": len(faults),
                "worker_replacement_count": replacement_process_count,
                "coordinator_survived": coordinator_survived,
                "worker_death_evidence_complete": evidence_complete,
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
            events=_sequence_field(outcome.adapter_result, "event_records"),
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
            "protocol_event_refs": tuple(
                dict(ref)
                for ref in (_optional_field(outcome.task, "event_refs") or ())
                if isinstance(ref, Mapping)
            ),
            "artifact_refs": tuple(
                dict(ref)
                for ref in (_optional_field(outcome.task, "artifact_refs") or ())
                if isinstance(ref, Mapping)
            ),
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
        runtime_body = _as_json(runtime or {})
        if not isinstance(runtime_body, Mapping):
            runtime_body = {}
        hook_observations = [
            {**dict(item), "ablation_mode": strategy.mode}
            for item in runtime_body.get("hook_observations", ())
            if isinstance(item, Mapping)
        ]
        target_mechanism = {
            "NO_VERIFICATION": "verification",
            "NO_PARSER_POLICY": "parser_policy",
            "NO_REQUEUE": "requeue",
            "NO_MERGE_GATE": "merge_gate",
        }.get(strategy.mode)
        target_observed = any(
            item.get("disabled_mechanism") == target_mechanism
            for item in hook_observations
        )
        if (
            target_mechanism is not None
            and not target_observed
            and strategy.metrics.get("applicable") is False
        ):
            hook_observations.append(
                {
                    "event_type": "EXPERIMENT_ABLATION_NOT_APPLICABLE",
                    "ablation_mode": strategy.mode,
                    "disabled_mechanism": target_mechanism,
                    "applicability": "not_applicable",
                    "not_applicable_reason": "target_lifecycle_boundary_not_reached",
                    "protocol_event_refs": list(
                        observation["protocol_event_refs"]
                    ),
                    "artifact_refs": list(observation["artifact_refs"]),
                    "hook_input": {
                        key: value
                        for key, value in observation.items()
                        if key
                        not in {"protocol_event_refs", "artifact_refs"}
                    },
                    "hook_result": {"applicable": False},
                }
            )
        runtime_body = {
            **dict(runtime_body),
            "condition_id": condition.condition_id,
            "case_id": outcome.case_id,
            "repeat_id": condition.repeat_id,
            "mode": strategy.mode,
            "attempt_observations": [
                {**dict(item), "ablation_mode": strategy.mode}
                for item in runtime_body.get("attempt_observations", ())
                if isinstance(item, Mapping)
            ],
            "hook_observations": hook_observations,
        }
        task_body["ablation_runtime"] = runtime_body
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
        task_body = _record_with_context(
            outcome.task,
            condition=condition,
            task_id=outcome.case_id,
        )
        task_body.setdefault("paper_eligible", outcome.paper_eligible)
        strategy = run_exp5_identity_strategy(
            attempts=attempts,
            approved_identity={
                "provider_family": condition.provider_family,
                "provider_model_id": condition.provider_model_id,
                "model_entry_id": condition.model_entry_id,
            },
            condition_id=condition.condition_id,
            cohort_member_id=str(condition.cohort_member_id),
            adapter_root=outcome.adapter_root,
            task=task_body,
            transport_kind=("ai_api" if self.real_transport else "capturing"),
            model_policy=str(condition.model_policy),
            pilot_only=bool(self.execution_classification),
        )
        identity_status_by_attempt = strategy.metrics[
            "identity_status_by_attempt"
        ]
        enriched_attempts = tuple(
            {
                **_as_json(attempt),
                "cohort_member_id": condition.cohort_member_id,
                "model_identity_audit": identity_status_by_attempt[
                    str(_required_field(attempt, "attempt_id"))
                ],
            }
            for attempt in attempts
        )
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
    ) -> bool:
        task_body = _record_with_context(
            task,
            condition=condition,
            task_id=task_id,
        )
        run_evidence = _optional_field(adapter_result, "run_evidence")
        protocol_runtime = (
            run_evidence.get("protocol_runtime")
            if isinstance(run_evidence, Mapping)
            and isinstance(run_evidence.get("protocol_runtime"), Mapping)
            else None
        )
        generation_identity = (
            protocol_runtime.get("generation_identity")
            if protocol_runtime is not None
            else None
        )
        if isinstance(generation_identity, Mapping):
            task_body["runtime_generation_identity"] = dict(generation_identity)
            split_profile_digest = _budget_split_profile_digest(
                budget=self.budget,
                condition_id=condition.condition_id,
                case_id=task_id,
            )
            if split_profile_digest is not None:
                task_body["execution_version_identity"] = (
                    _execution_version_identity(
                        domain=condition.domain,
                        split_profile_digest=split_profile_digest,
                        runtime_generation_identity=generation_identity,
                    )
                )
        runtime_observation = (
            protocol_runtime.get("runtime_observation")
            if protocol_runtime is not None
            else None
        )
        if isinstance(runtime_observation, Mapping):
            task_body["runtime_observation"] = dict(runtime_observation)
        if protocol_runtime is not None:
            for field_name in (
                "case_id",
                "factor_position_quantile",
                "execution_scope",
                "selected_ai_unit_ids",
            ):
                value = protocol_runtime.get(field_name)
                if value is not None:
                    task_body[field_name] = value
        task_body["record_scope"] = (
            "protocol" if protocol_runtime is not None else "experiment"
        )
        root_status = _status_value(task_body.get("root_status", "failed"))
        task_body.update(
            PaperTerminalOutcome(
                outcome_status=(
                    PaperOutcomeStatus.SUCCEEDED
                    if root_status == "completed"
                    else PaperOutcomeStatus.FAILED_EXPERIMENTAL
                ),
                evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
                **(
                    {}
                    if root_status == "completed"
                    else {
                        "failure_stage": "experiment_runtime",
                        "failure_kind": str(
                            task_body.get("error_kind") or root_status
                        ),
                    }
                ),
            ).to_dict()
        )
        source_task_eligible = task_body.get("paper_eligible") is True
        task_body.update(self._evidence_flags(paper_eligible=False))
        task_body["source_projection_paper_eligible"] = source_task_eligible
        attempt_values = _sequence_field(adapter_result, "attempt_results", "attempts")
        attempts = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in attempt_values
        ]
        if not attempts:
            if protocol_runtime is not None:
                raise ValueError(
                    "protocol checkpoint requires real attempt evidence"
                )
            attempts = [_record_with_context(
                _experiment_attempt_record(
                    condition=condition,
                    task_id=task_id,
                    status="blocked",
                    error_kind="experiment_blocked",
                ),
                condition=condition,
                task_id=task_id,
            )]
        for attempt in attempts:
            attempt.setdefault("record_scope", task_body["record_scope"])
            source_attempt_eligible = attempt.get("paper_eligible") is True
            attempt_reasons = _attempt_checkpoint_ineligibility_reasons(
                attempt=attempt,
                protocol_runtime=protocol_runtime,
                real_transport=self.real_transport,
                transport=self.transport,
                source_attempt_eligible=source_attempt_eligible,
            )
            attempt.update(self._evidence_flags(paper_eligible=not attempt_reasons))
            attempt["source_projection_paper_eligible"] = source_attempt_eligible
            attempt["paper_ineligibility_reasons"] = attempt_reasons
        protocol_event_values = _sequence_field(adapter_result, "event_records")
        if protocol_runtime is not None and not protocol_event_values:
            raise ValueError("protocol checkpoint requires real lifecycle events")
        events = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in protocol_event_values
        ]
        if not events:
            events = [_record_with_context(
                _experiment_event_record(
                    condition=condition,
                    task_id=task_id,
                    event_type="EXPERIMENT_BLOCKED",
                ),
                condition=condition,
                task_id=task_id,
            )]
        for event in events:
            event.setdefault(
                "record_scope",
                "experiment"
                if str(event.get("event_type", "")).startswith("EXPERIMENT_")
                else task_body["record_scope"],
            )
        experiment_events = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in extra_events
        ]
        for event in experiment_events:
            event.setdefault("record_scope", "experiment")
        events.extend(experiment_events)
        for index, event in enumerate(events):
            event.setdefault(
                "event_id",
                (
                    f"formal-{str(event.get('event_type', 'event')).lower()}-"
                    f"{condition.condition_id}-{condition.repeat_id}-{task_id}-{index}"
                ),
            )
            event.update(
                self._evidence_flags(
                    paper_eligible=(
                        event.get("record_scope") == "protocol"
                        and isinstance(event.get("event_id"), str)
                        and bool(event.get("event_id"))
                    )
                )
            )
        faults = [
            _record_with_context(item, condition=condition, task_id=task_id)
            for item in _sequence_field(adapter_result, "fault_records", "faults")
        ]
        for fault in faults:
            fault.update(self._evidence_flags(paper_eligible=False))
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
        shared_reference = task_body.get("shared_exp1_reference")
        if isinstance(shared_reference, Mapping):
            shared_reference_ref = _write_runner_artifact(
                suite_root=self.evidence_store.output_root,
                condition=condition,
                task_id=task_id,
                artifact_name="shared-exp1-reference.json",
                body=shared_reference,
            )
            artifact_refs.append(shared_reference_ref)
            task_body["shared_exp1_reference_ref"] = shared_reference_ref
        task_body["evidence_artifact_refs"] = list(artifact_refs)
        task_reasons = _task_checkpoint_ineligibility_reasons(
            task=task_body,
            attempts=attempts,
            events=events,
            protocol_runtime=protocol_runtime,
            real_transport=self.real_transport,
            transport=self.transport,
            source_task_eligible=source_task_eligible,
        )
        task_body.update(self._evidence_flags(paper_eligible=not task_reasons))
        task_body["source_projection_paper_eligible"] = source_task_eligible
        task_body["paper_ineligibility_reasons"] = task_reasons
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
        return task_body["paper_eligible"] is True

    def _checkpoint_exception(
        self,
        *,
        condition: Any,
        task_id: str,
        error: Exception,
        extra_events: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        task = _record_with_context(
            {
                "task_id": task_id,
                "root_status": "failed",
                "error_kind": type(error).__name__,
                "record_scope": "experiment",
                **PaperTerminalOutcome(
                    outcome_status=PaperOutcomeStatus.FAILED_EXPERIMENTAL,
                    evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
                    failure_stage="experiment_runtime",
                    failure_kind=type(error).__name__,
                ).to_dict(),
            },
            condition=condition,
            task_id=task_id,
        )
        task.update(self._evidence_flags())
        attempt = _record_with_context(
            _experiment_attempt_record(
                condition=condition,
                task_id=task_id,
                status="failed",
                error_kind=type(error).__name__,
            ),
            condition=condition,
            task_id=task_id,
        )
        attempt.update(
            {
                "attempt_status": "failed",
                "error_kind": type(error).__name__,
                "outcome_status": "failed_experimental",
                "evidence_integrity": "complete",
                **self._evidence_flags(),
            }
        )
        events = [
            _record_with_context(
                _experiment_event_record(
                    condition=condition,
                    task_id=task_id,
                    event_type="EXPERIMENT_RUNNER_EXCEPTION",
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
            event.update(self._evidence_flags())
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
        self.evidence_store._refresh_evidence_manifest()

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
                "record_scope": "experiment",
            },
            condition=condition,
            task_id=task_id,
        )
        task.update(self._evidence_flags())
        attempt = _record_with_context(
            _experiment_attempt_record(
                condition=condition,
                task_id=task_id,
                status="cancelled_by_budget",
                error_kind="budget_limit",
            ),
            condition=condition,
            task_id=task_id,
        )
        attempt.update(
            {
                "attempt_status": "cancelled_by_budget",
                "error_kind": "budget_limit",
                **self._evidence_flags(),
            }
        )
        events = [
            _record_with_context(
                {
                    "event_id": (
                        f"formal-budget-exhausted-{condition.condition_id}-"
                        f"{condition.repeat_id}-{task_id}"
                    ),
                    "event_type": "EXPERIMENT_BUDGET_EXHAUSTED",
                    "record_scope": "experiment",
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
            event.update(self._evidence_flags())
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
                    "total_cost_estimate": self.usage.reportable_total_cost_estimate(),
                    "cost_estimate_by_currency": dict(
                        sorted(self.usage.cost_estimate_by_currency.items())
                    ),
                    "cost_estimate_status": self.usage.cost_estimate_status(),
                    "usage_missing_count": self.usage.usage_missing_count,
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

    def _evidence_flags(self, *, paper_eligible: bool = False) -> dict[str, Any]:
        return _evidence_flags(
            paper_eligible=paper_eligible,
            execution_classification=self.execution_classification,
        )


@dataclass
class _UsageTotals:
    """当前 runner 进程内已经消费的受限资源。"""

    provider_attempt_count: int = 0
    total_tokens: int = 0
    total_cost_estimate: float = 0.0
    cost_estimate_by_currency: dict[str, float] = field(default_factory=dict)
    usage_missing_count: int = 0
    reserved_provider_attempt_count: int = 0
    reserved_total_tokens: int = 0
    reserved_total_cost_estimate: float = 0.0
    reserved_cost_estimate_by_currency: dict[str, float] = field(default_factory=dict)

    def reportable_total_cost_estimate(self) -> float:
        if len(self.cost_estimate_by_currency) > 1:
            return 0.0
        if self.cost_estimate_by_currency:
            return next(iter(self.cost_estimate_by_currency.values()))
        return self.total_cost_estimate

    def cost_estimate_status(self) -> str:
        if len(self.cost_estimate_by_currency) > 1:
            return "mixed_currency_not_aggregated"
        if self.usage_missing_count:
            return "usage_missing"
        if self.cost_estimate_by_currency:
            return "single_currency_estimate"
        return "single_currency_or_legacy"


@dataclass(frozen=True, slots=True)
class _RootBudgetReservation:
    provider_attempt_count: int
    total_tokens: int
    total_cost_estimate: float
    currency: str | None = None


def _evidence_bodies(
    *,
    plans: Sequence[PaperExperimentDispatchPlan],
    catalog_manifest: Any,
    budget: PaperBudgetResult,
    hard_limits: Mapping[str, Any],
    ai_api_configs: Mapping[str, Any],
    root_case_filter: Mapping[str, tuple[str, ...]],
    execution_classification: Mapping[str, Any] | None,
    suite_id: str,
    pre_execution_documents: Mapping[str, Any],
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
            "suite_id": suite_id,
            "experiment_ids": [plan.experiment_id for plan in plans],
            **(
                {
                    "execution_classification": dict(
                        execution_classification
                    ),
                    "root_case_filter": {
                        condition_id: list(case_ids)
                        for condition_id, case_ids in sorted(
                            root_case_filter.items()
                        )
                    },
                }
                if execution_classification is not None
                else {}
            ),
            **(
                {
                    "pre_execution_document_digests": {
                        name: digest_json(body)
                        for name, body in sorted(
                            pre_execution_documents.items()
                        )
                    }
                }
                if pre_execution_documents
                else {}
            ),
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
        cost_estimate_by_currency=_mapping_or_none(
            body.get("cost_estimate_by_currency")
        ),
        total_cost_estimate_status=str(
            body.get("total_cost_estimate_status", "single_currency_or_legacy")
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
    currency_costs = body.get("cost_estimate_by_currency")
    normalized_currency_costs = (
        {
            str(currency): float(value)
            for currency, value in currency_costs.items()
        }
        if isinstance(currency_costs, Mapping)
        else {}
    )
    return _UsageTotals(
        provider_attempt_count=int(body.get("provider_attempt_count", 0)),
        total_tokens=int(body.get("total_tokens", 0)),
        total_cost_estimate=(
            0.0
            if normalized_currency_costs
            else float(body.get("total_cost_estimate", 0.0))
        ),
        cost_estimate_by_currency=normalized_currency_costs,
        usage_missing_count=(
            1 if body.get("total_cost_estimate_status") == "usage_missing" else 0
        ),
    )


def _all_selected_roots_completed(
    bound_plans: Sequence[tuple[PaperExperimentDispatchPlan, Sequence[tuple[Any, Any]]]],
    completed_task_keys: set[tuple[str, str, str, str]],
    *,
    root_case_filter: Mapping[str, tuple[str, ...]],
) -> bool:
    return all(
        _formal_task_key(condition, case_id) in completed_task_keys
        for plan, items in bound_plans
        if plan.status == "planned"
        for condition, selection in items
        for case_id in root_case_filter.get(
            condition.condition_id,
            tuple(selection.ordered_case_ids),
        )
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
    currency: str | None,
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
        currency=currency,
    )


def _config_currency(config: AIAPIExecutorConfig) -> str | None:
    currencies = {
        str(entry.pricing["currency"])
        for entry in config.entries
        if entry.enabled and isinstance(entry.pricing.get("currency"), str)
    }
    if len(currencies) > 1:
        raise ValueError("one prepared provider config cannot mix pricing currencies")
    return next(iter(currencies), None)


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
    projected_costs = _projected_cost_estimates_by_currency(
        usage=usage,
        reservation=reservation,
    )
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
    )
    for field_name, value in projected:
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and value > limit
        ):
            return False
    for field_name in ("max_cost_estimate", "max_total_cost_estimate"):
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and any(value > limit for value in projected_costs.values())
        ):
            return False
    usage.reserved_provider_attempt_count += reservation.provider_attempt_count
    usage.reserved_total_tokens += reservation.total_tokens
    if reservation.currency is None:
        usage.reserved_total_cost_estimate += reservation.total_cost_estimate
    else:
        usage.reserved_cost_estimate_by_currency[reservation.currency] = (
            usage.reserved_cost_estimate_by_currency.get(reservation.currency, 0.0)
            + reservation.total_cost_estimate
        )
    return True


def _settle_hard_limit_reservation(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
    provider_attempt_count: int = 0,
    total_tokens: int = 0,
    total_cost_estimate: float = 0.0,
    cost_estimate_currency: str | None = None,
    cost_estimate_status: str | None = None,
) -> None:
    usage.reserved_provider_attempt_count -= reservation.provider_attempt_count
    usage.reserved_total_tokens -= reservation.total_tokens
    if reservation.currency is None:
        usage.reserved_total_cost_estimate -= reservation.total_cost_estimate
    else:
        remaining = (
            usage.reserved_cost_estimate_by_currency.get(reservation.currency, 0.0)
            - reservation.total_cost_estimate
        )
        if abs(remaining) <= 1e-12:
            usage.reserved_cost_estimate_by_currency.pop(reservation.currency, None)
        else:
            usage.reserved_cost_estimate_by_currency[reservation.currency] = remaining
    usage.provider_attempt_count += provider_attempt_count
    usage.total_tokens += total_tokens
    if cost_estimate_currency is None:
        usage.total_cost_estimate += total_cost_estimate
    else:
        if reservation.currency not in (None, cost_estimate_currency):
            raise ValueError("provider usage currency drifted from the reservation")
        usage.cost_estimate_by_currency[cost_estimate_currency] = (
            usage.cost_estimate_by_currency.get(cost_estimate_currency, 0.0)
            + total_cost_estimate
        )
    if cost_estimate_status == "usage_missing":
        usage.usage_missing_count += 1


def _hard_limit_reached(usage: _UsageTotals, hard_limits: Mapping[str, Any]) -> bool:
    limits = (
        ("max_total_provider_attempts", usage.provider_attempt_count),
        ("max_provider_attempts", usage.provider_attempt_count),
        ("max_total_tokens", usage.total_tokens),
        ("max_tokens", usage.total_tokens),
    )
    for field_name, consumed in limits:
        limit = hard_limits.get(field_name)
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and consumed >= limit:
            return True
    cost_values = (
        tuple(usage.cost_estimate_by_currency.values())
        if usage.cost_estimate_by_currency
        else (usage.total_cost_estimate,)
    )
    for field_name in ("max_cost_estimate", "max_total_cost_estimate"):
        limit = hard_limits.get(field_name)
        if (
            isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and any(consumed >= limit for consumed in cost_values)
        ):
            return True
    return False


def _projected_cost_estimates_by_currency(
    *,
    usage: _UsageTotals,
    reservation: _RootBudgetReservation,
) -> dict[str, float]:
    if reservation.currency is None:
        return {
            "legacy_or_unspecified": (
                usage.total_cost_estimate
                + usage.reserved_total_cost_estimate
                + reservation.total_cost_estimate
            )
        }
    currencies = set(usage.cost_estimate_by_currency) | set(
        usage.reserved_cost_estimate_by_currency
    )
    currencies.add(reservation.currency)
    return {
        currency: (
            usage.cost_estimate_by_currency.get(currency, 0.0)
            + usage.reserved_cost_estimate_by_currency.get(currency, 0.0)
            + (
                reservation.total_cost_estimate
                if currency == reservation.currency
                else 0.0
            )
        )
        for currency in currencies
    }


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


def _attempt_checkpoint_ineligibility_reasons(
    *,
    attempt: Mapping[str, Any],
    protocol_runtime: Mapping[str, Any] | None,
    real_transport: bool,
    transport: Any,
    source_attempt_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    if not real_transport or _is_offline_capturing_transport(transport):
        reasons.append("capturing_or_non_real_transport")
    if protocol_runtime is None or attempt.get("record_scope") != "protocol":
        reasons.append("attempt_not_from_protocol_runtime")
    if protocol_runtime is not None and (
        protocol_runtime.get("execution_scope") != "whole_root"
        or bool(protocol_runtime.get("selected_ai_unit_ids"))
    ):
        reasons.append("partial_or_selected_unit_execution_scope")
    if not source_attempt_eligible:
        reasons.append("source_attempt_evidence_incomplete")
    for field_name in (
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "worker_id",
        "started_at",
        "ended_at",
    ):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"missing_attempt_{field_name}")
    for field_name in (
        "request_ref",
        "provenance_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        if not _complete_evidence_ref(attempt.get(field_name)):
            reasons.append(f"missing_attempt_{field_name}")
    status = _status_value(attempt.get("attempt_status") or "")
    if status in {
        "succeeded",
        "parse_failed",
        "verification_rejected",
        "checker_rejected",
        "late_rejected",
        "model_identity_mismatch",
    } and not _complete_evidence_ref(attempt.get("raw_output_ref")):
        reasons.append("missing_attempt_raw_output_ref")
    if status == "parse_failed" and not _complete_evidence_ref(
        attempt.get("parse_failure_ref")
    ):
        reasons.append("missing_attempt_parse_failure_ref")
    provider_attempt_count = attempt.get("provider_attempt_count")
    if (
        isinstance(provider_attempt_count, bool)
        or not isinstance(provider_attempt_count, int)
        or provider_attempt_count < 1
    ):
        reasons.append("missing_provider_attempt_inventory")
    if attempt.get("synthetic") is True or attempt.get("synthetic_fallback") is True:
        reasons.append("synthetic_attempt_forbidden")
    return list(dict.fromkeys(reasons))


def _task_checkpoint_ineligibility_reasons(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    protocol_runtime: Mapping[str, Any] | None,
    real_transport: bool,
    transport: Any,
    source_task_eligible: bool,
) -> list[str]:
    reasons: list[str] = []
    if not real_transport or _is_offline_capturing_transport(transport):
        reasons.append("capturing_or_non_real_transport")
    if protocol_runtime is None or task.get("record_scope") != "protocol":
        reasons.append("task_not_from_protocol_runtime")
    if protocol_runtime is not None and (
        protocol_runtime.get("execution_scope") != "whole_root"
        or bool(protocol_runtime.get("selected_ai_unit_ids"))
    ):
        reasons.append("partial_or_selected_unit_execution_scope")
    if not source_task_eligible:
        reasons.append("source_task_evidence_incomplete")
    if _status_value(task.get("root_status") or "") == "partial":
        reasons.append("partial_root_result")
    if not attempts or any(
        attempt.get("paper_eligible") is not True for attempt in attempts
    ):
        reasons.append("attempt_evidence_incomplete")
    protocol_events = [
        event
        for event in events
        if event.get("record_scope") == "protocol"
        and isinstance(event.get("event_id"), str)
        and event.get("event_id")
    ]
    if not protocol_events:
        reasons.append("missing_protocol_lifecycle_events")
    refs = task.get("evidence_artifact_refs")
    if (
        not isinstance(refs, Sequence)
        or isinstance(refs, (str, bytes))
        or not refs
        or any(not _complete_evidence_ref(ref) for ref in refs)
    ):
        reasons.append("missing_task_artifact_evidence_refs")
    if task.get("synthetic") is True or task.get("synthetic_fallback") is True:
        reasons.append("synthetic_task_forbidden")
    return list(dict.fromkeys(reasons))


def _complete_evidence_ref(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return bool(
        value.get("artifact_id")
        or (
            isinstance(value.get("path"), str)
            and value.get("path")
            and isinstance(value.get("content_hash"), str)
            and value.get("content_hash")
        )
    )


def _evidence_flags(
    *,
    paper_eligible: bool = False,
    execution_classification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if execution_classification is None:
        return {
            "formal": True,
            "pilot_only": False,
            "paper_eligible": paper_eligible,
        }
    return {
        "formal": False,
        "pilot_only": True,
        "regression_only": True,
        "paper_eligible": False,
        "ineligibility_reasons": list(
            execution_classification["ineligibility_reasons"]
        ),
    }


def _sequence_field(value: Any, *field_names: str) -> tuple[Any, ...]:
    for field_name in field_names:
        result = _optional_field(value, field_name)
        if result is None:
            continue
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
            return tuple(result)
        raise ValueError(f"adapter field {field_name} must be a sequence")
    return ()


def _experiment_attempt_record(
    *,
    condition: Any,
    task_id: str,
    status: str,
    error_kind: str,
) -> dict[str, Any]:
    return {
        "attempt_id": (
            f"experiment-{condition.condition_id}-{condition.repeat_id}-{task_id}"
        ),
        "attempt_status": status,
        "provider_attempt_index": 0,
        "error_kind": error_kind,
        "record_scope": "experiment",
    }


def _experiment_event_record(
    *, condition: Any, task_id: str, event_type: str
) -> dict[str, Any]:
    return {
        "event_id": (
            f"experiment-{event_type.lower()}-{condition.condition_id}-"
            f"{condition.repeat_id}-{task_id}"
        ),
        "event_type": event_type,
        "record_scope": "experiment",
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
            "model_execution_record_ref",
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
        source = adapter_root / uri
        if not source.is_file():
            matches = _find_nested_adapter_artifacts(
                adapter_root=adapter_root,
                uri=uri,
                expected_content_hash=source_ref.get("content_hash"),
            )
            if len(matches) != 1:
                raise ValueError(
                    f"adapter artifact evidence is missing: {uri}"
                )
            source = matches[0]
        name = Path(uri).name or f"artifact-{index}"
        if name in names:
            name = f"{index}-{name}"
        names.add(name)
        target = artifact_root / name
        shutil.copyfile(source, target)
        materialized_ref = {
            "experiment_id": condition.experiment_id,
            "condition_id": condition.condition_id,
            "repeat_id": condition.repeat_id,
            "task_id": task_id,
            "path": target.relative_to(suite_root).as_posix(),
            "content_hash": _sha256_bytes(target.read_bytes()),
        }
        if isinstance(source_ref.get("artifact_id"), str) and source_ref.get(
            "artifact_id"
        ):
            materialized_ref["artifact_id"] = source_ref["artifact_id"]
        result.append(materialized_ref)
    return result


def _find_nested_adapter_artifacts(
    *,
    adapter_root: Path,
    uri: str,
    expected_content_hash: Any,
) -> list[Path]:
    """定位 adapter 内层 run root；显式构造路径以兼容 Windows ADS 名称。"""

    uri_path = Path(uri)
    parts = uri_path.parts
    candidates: list[Path] = []
    if parts:
        for anchor in adapter_root.rglob(parts[0]):
            if anchor.is_dir():
                candidates.append(anchor.joinpath(*parts[1:]))
    candidates.extend(adapter_root.rglob(uri_path.name))
    matches: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen or not candidate.is_file():
            continue
        seen.add(candidate_key)
        if (
            isinstance(expected_content_hash, str)
            and _sha256_bytes(candidate.read_bytes())
            != expected_content_hash
        ):
            continue
        matches.append(candidate)
    return matches


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
        "run_evidence": _optional_field(original, "run_evidence"),
        "output_root": _optional_field(original, "output_root"),
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


def _last_complete_event_ref(suite_root: Path) -> dict[str, Any] | None:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for pointer_path in sorted(suite_root.glob("experiments/*/runs/*/*/CURRENT.json")):
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            generation_root = (
                pointer_path.parent
                / ".generations"
                / str(pointer["generation_id"])
            )
            event_path = generation_root / "events" / "event_log.jsonl"
            for event in _read_jsonl_records(event_path):
                if event.get("event_type") in {
                    "TASK_COMPLETED",
                    "EXPERIMENT_FAULT_OBSERVED",
                }:
                    candidates.append((event_path, event))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    path, event = candidates[-1]
    return {
        "path": path.relative_to(suite_root).as_posix(),
        "event_id": event.get("event_id"),
        "task_id": event.get("task_id"),
        "record_hash": digest_json(event),
    }


def _finalize_formal_manifests(
    *,
    suite_root: Path,
    suite_result: PaperSuiteResult,
    plans: Sequence[PaperExperimentDispatchPlan],
    condition_results: Sequence[PaperConditionResult],
    execution_classification: Mapping[str, Any] | None,
) -> None:
    suite_path = suite_root / "suite_manifest.json"
    suite_manifest = json.loads(suite_path.read_text(encoding="utf-8"))
    suite_manifest["status"] = _status_value(suite_result.status)
    suite_manifest["paper_eligible"] = suite_result.paper_eligible
    if suite_result.error_summary:
        suite_manifest["terminal_failure"] = _as_json(
            suite_result.error_summary[0]
        )
        suite_manifest["last_complete_event_ref"] = _last_complete_event_ref(
            suite_root
        )
    _write_json(suite_path, suite_manifest)
    results_by_id = {result.condition_id: result for result in condition_results}
    condition_rows: list[dict[str, Any]] = []
    for plan in plans:
        plan_results = [
            results_by_id[condition.condition_id]
            for condition in plan.conditions
            if condition.condition_id in results_by_id
        ]
        infrastructure_blocked = any(
            isinstance(result.metrics_ref, Mapping)
            and result.metrics_ref.get("infrastructure_blocked") is True
            for result in plan_results
        )
        if _status_value(suite_result.status) == "incomplete" and infrastructure_blocked:
            experiment_status = PaperStatus.INCOMPLETE
        elif plan.status == "blocked" or infrastructure_blocked:
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
        expected_ids = {condition.condition_id for condition in plan.conditions}
        actual_ids = {result.condition_id for result in plan_results}
        manifest["paper_eligible"] = (
            execution_classification is None
            and
            plan.paper_eligible_possible
            and plan.status != "blocked"
            and bool(expected_ids)
            and actual_ids == expected_ids
            and all(
                isinstance(result.metrics_ref, Mapping)
                and result.metrics_ref.get("paper_eligible") is True
                and bool(result.metrics_ref.get("evidence_refs"))
                for result in plan_results
            )
        )
        _write_json(manifest_path, manifest)
        for condition in plan.conditions:
            result = results_by_id.get(condition.condition_id)
            if result is None:
                continue
            row = {
                    **result.to_dict(),
                    "experiment_id": plan.experiment_id,
                    "repeat_id": condition.repeat_id,
                    "formal": execution_classification is None,
                    "pilot_only": execution_classification is not None,
                    "regression_only": execution_classification is not None,
                    "execution_scope": (
                        "formal_matrix"
                        if execution_classification is None
                        else "smoke_suite"
                    ),
                    "ineligibility_reasons": (
                        []
                        if execution_classification is None
                        else list(
                            execution_classification["ineligibility_reasons"]
                        )
                    ),
                    "paper_eligible": (
                        execution_classification is None
                        and
                        isinstance(result.metrics_ref, Mapping)
                        and result.metrics_ref.get("paper_eligible") is True
                    ),
                }
            if isinstance(result.metrics_ref, Mapping):
                outcome_counts = result.metrics_ref.get("outcome_counts")
                if isinstance(outcome_counts, Mapping):
                    row["outcome_counts"] = dict(outcome_counts)
                    row["evidence_integrity"] = result.metrics_ref.get(
                        "evidence_integrity",
                        "invalid"
                        if result.metrics_ref.get("infrastructure_blocked")
                        else "complete",
                    )
            condition_rows.append(row)
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


def _case_with_selection_split_profile(
    case: Mapping[str, Any],
    selection: Any,
) -> dict[str, Any]:
    profile_id = getattr(selection, "split_profile_id", None)
    if profile_id is None:
        return dict(case)
    split_params = case.get("split_params")
    if not isinstance(split_params, Mapping):
        raise ValueError("split profile requires factorization split_params")
    return {
        **dict(case),
        "split_params": {
            "strategy_id": split_params.get("strategy_id"),
            "range_policy": split_params.get("range_policy"),
            "split_profile_id": profile_id,
        },
    }


def _condition_with_frozen_case_metadata(
    *,
    condition: PaperExperimentCondition,
    case: Mapping[str, Any],
) -> PaperExperimentCondition:
    """把冻结 case 的逐题元数据绑定到临时 adapter 执行 condition。"""

    if condition.domain == "factorization":
        difficulty = case.get("difficulty")
        paper_difficulty = case.get("paper_difficulty")
        if difficulty is None and paper_difficulty is None:
            return condition
        if not isinstance(difficulty, str) or not difficulty:
            raise ValueError("frozen factorization case difficulty is required")
        if not isinstance(paper_difficulty, str) or not paper_difficulty:
            raise ValueError(
                "frozen factorization case paper_difficulty is required"
            )
        return replace(
            condition,
            difficulty=difficulty,
            paper_difficulty=paper_difficulty,
        )
    if condition.domain != "lean_proof":
        return condition
    updates: dict[str, Any] = {}
    for field_name in (
        "paper_difficulty",
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
                f"condition {field_name} must match frozen Lean case metadata"
            )
        if condition_value is None:
            updates[field_name] = case_value
    return replace(condition, **updates) if updates else condition


def _required_field(value: Any, field_name: str) -> Any:
    result = _optional_field(value, field_name)
    if result is None:
        raise ValueError(f"adapter result requires {field_name}")
    return result


def _validate_runtime_attempt_identity(
    attempt: Any,
    approved_identity: Mapping[str, Any],
) -> None:
    expected_by_field = {
        "provider": approved_identity.get("provider_family"),
        "model": approved_identity.get("provider_model_id"),
        "entry_id": approved_identity.get("model_entry_id"),
    }
    for field_name, expected in expected_by_field.items():
        if expected is None or _required_field(attempt, field_name) != expected:
            raise ValueError(f"runtime replacement identity drift: {field_name}")


def _optional_field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)
