"""Plan-only budget calculation for paper real-AI experiments."""

from __future__ import annotations

from dataclasses import replace

from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
)
from tokenshare.experiments.paper_models import (
    JsonObject,
    PaperBudgetResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)


class PaperBudgetApprovalError(ValueError):
    pass


def plan_paper_suite(
    *,
    catalog_manifest: PaperInputCatalogManifest,
    conditions: tuple[PaperExperimentCondition, ...] | list[PaperExperimentCondition],
    max_provider_attempts_per_ai_unit: int,
    token_upper_bound_per_provider_attempt: int,
    cost_upper_bound_per_provider_attempt: float,
    plan_only: bool,
    approve_budget_digest: str | None = None,
) -> PaperBudgetResult:
    if max_provider_attempts_per_ai_unit < 1:
        raise ValueError("max_provider_attempts_per_ai_unit must be >= 1")
    if token_upper_bound_per_provider_attempt < 1:
        raise ValueError("token_upper_bound_per_provider_attempt must be >= 1")
    if cost_upper_bound_per_provider_attempt < 0:
        raise ValueError("cost_upper_bound_per_provider_attempt must be >= 0")
    condition_tuple = tuple(conditions)
    planned_root_runs = 0
    planned_ai_units = 0
    for condition in condition_tuple:
        if condition.catalog_digest != catalog_manifest.catalog_digest:
            raise ValueError("condition catalog_digest does not match catalog manifest")
        cases = catalog_manifest.cases_for(
            domain=condition.domain,
            difficulty=condition.difficulty,
        )
        planned_root_runs += len(cases)
        planned_ai_units += sum(estimated_ai_units_for_case(case) for case in cases)
    max_provider_attempts = planned_ai_units * max_provider_attempts_per_ai_unit
    body: JsonObject = {
        "planned_experiments": sorted({item.experiment_id for item in condition_tuple}),
        "planned_conditions": len(condition_tuple),
        "planned_root_runs": planned_root_runs,
        "planned_ai_units": planned_ai_units,
        "max_provider_attempts": max_provider_attempts,
        "token_upper_bound": max_provider_attempts * token_upper_bound_per_provider_attempt,
        "cost_upper_bound": max_provider_attempts * cost_upper_bound_per_provider_attempt,
        "catalog_digest": catalog_manifest.catalog_digest,
        "condition_digests": [item.condition_digest for item in condition_tuple],
    }
    budget_digest = digest_json(body)
    if not plan_only and approve_budget_digest is None:
        raise PaperBudgetApprovalError("budget approval digest is required")
    if approve_budget_digest is not None and approve_budget_digest != budget_digest:
        raise PaperBudgetApprovalError("budget digest mismatch")
    return PaperBudgetResult(
        budget_digest=budget_digest,
        planned_experiments=body["planned_experiments"],
        planned_conditions=body["planned_conditions"],
        planned_root_runs=body["planned_root_runs"],
        planned_ai_units=body["planned_ai_units"],
        max_provider_attempts=body["max_provider_attempts"],
        token_upper_bound=body["token_upper_bound"],
        cost_upper_bound=body["cost_upper_bound"],
        wall_clock_estimate=float(max(1, planned_ai_units)),
        quota_preflight={
            "status": "not_checked",
            "provider_calls_made": 0,
            "plan_only": plan_only,
        },
        rate_limit_preflight={"status": "not_checked"},
        disk_estimate={
            "bytes": max(4096, planned_root_runs * 2048 + planned_ai_units * 1024)
        },
        status=PaperStatus.PLANNED,
    )


def budget_with_status(
    budget: PaperBudgetResult,
    status: PaperStatus,
) -> PaperBudgetResult:
    return replace(budget, status=status)
