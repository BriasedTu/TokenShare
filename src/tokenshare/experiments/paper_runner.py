"""Minimal paper experiment condition expansion for plan-only runs."""

from __future__ import annotations

from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    estimated_ai_units_for_case,
)
from tokenshare.experiments.paper_ablation import ablation_modes
from tokenshare.experiments.paper_models import (
    LEAN_PAPER_DIFFICULTIES,
    LEAN_TOPIC_FAMILIES,
    JsonObject,
    PaperExperimentCondition,
)
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS,
)


EXPERIMENT_ALIASES = {
    "exp1": "exp1_real_ai_feasibility",
    "exp2": "exp2_real_ai_scalability",
    "exp3": "exp3_real_ai_fault_recovery",
    "exp4": "exp4_real_ai_protocol_ablation",
    "exp5": "exp5_real_ai_model_endpoint_comparison",
}
LEAN_3X3_TARGET_CASE_COUNT = 10


def normalize_experiment_ids(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        normalized.append(EXPERIMENT_ALIASES.get(item, item))
    return tuple(dict.fromkeys(normalized))


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
    planned: list[JsonObject] = []
    for member_id in PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS:
        plan = member_plans.get(member_id)
        if not isinstance(plan, dict) or plan.get("status") != "planned":
            return ()
        selected_entry_id = plan.get("selected_entry_id")
        if not isinstance(selected_entry_id, str) or not selected_entry_id:
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
            expected_ai_unit_count = sum(
                estimated_ai_units_for_case(case) for case in cases
            )
            checker_backed_cases = tuple(
                case for case in cases if _is_checker_backed_lean_case(case)
            )
            available_case_count = len(checker_backed_cases)
            paper_eligible_possible = available_case_count >= target_case_count
            blocked_reason = (
                None if paper_eligible_possible else "insufficient_catalog"
            )
            topic_family_expected_ai_unit_counts[topic_family] += expected_ai_unit_count
            topic_family_available_case_counts[topic_family] += available_case_count
            cells.append(
                {
                    "schema_version": "tokenshare.lean_3x3_matrix_cell_plan.v1",
                    "domain": "lean_proof",
                    "status": "planned" if paper_eligible_possible else "blocked",
                    "paper_difficulty": paper_difficulty,
                    "topic_family": topic_family,
                    "catalog_case_count": len(cases),
                    "available_case_count": available_case_count,
                    "target_case_count": target_case_count,
                    "expected_ai_unit_count": expected_ai_unit_count,
                    "preflight_status": _lean_cell_preflight_summary(cases),
                    "paper_eligible_possible": paper_eligible_possible,
                    "blocked_reason": blocked_reason,
                }
            )
    return {
        "schema_version": "tokenshare.lean_3x3_matrix_plan.v1",
        "domain": "lean_proof",
        "cell_count": len(cells),
        "target_case_count": target_case_count,
        "cells": cells,
        "topic_family_expected_ai_unit_counts": topic_family_expected_ai_unit_counts,
        "topic_family_available_case_counts": topic_family_available_case_counts,
        "paper_eligible_possible_cell_count": sum(
            1 for cell in cells if cell["paper_eligible_possible"] is True
        ),
        "blocked_cell_count": sum(1 for cell in cells if cell["status"] == "blocked"),
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
