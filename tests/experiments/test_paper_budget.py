from pathlib import Path

import pytest

from tokenshare.experiments.paper_budget import PaperBudgetApprovalError, plan_paper_suite
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import PaperExperimentCondition, PaperStatus


def _sample_conditions(catalog_digest: str) -> tuple[PaperExperimentCondition, ...]:
    return (
        PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_factorization_easy_repeat0",
            domain="factorization",
            difficulty="easy",
            worker_count=10,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest=catalog_digest,
        ),
        PaperExperimentCondition(
            experiment_id="exp1_real_ai_feasibility",
            condition_id="exp1_lean_easy_repeat0",
            domain="lean_proof",
            difficulty="easy",
            worker_count=10,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            repeat_id=0,
            seed=1,
            catalog_digest=catalog_digest,
        ),
    )


def test_plan_only_budget_expands_conditions_without_provider_calls() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=_sample_conditions(catalog.catalog_digest),
        max_provider_attempts_per_ai_unit=2,
        token_upper_bound_per_provider_attempt=1024,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    body = budget.to_dict()
    assert body["status"] == PaperStatus.PLANNED.value
    assert body["planned_conditions"] == 2
    assert body["planned_root_runs"] == 20
    assert body["planned_ai_units"] == 40
    assert body["max_provider_attempts"] == 80
    assert body["token_upper_bound"] == 81920
    assert body["cost_upper_bound"] == 0.8
    assert body["quota_preflight"]["provider_calls_made"] == 0
    assert body["budget_digest"].startswith("sha256:")


def test_budget_approval_requires_matching_digest() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    conditions = _sample_conditions(catalog.catalog_digest)
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        approve_budget_digest=None,
    )

    approved = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=False,
        approve_budget_digest=budget.budget_digest,
    )

    assert approved.budget_digest == budget.budget_digest
    with pytest.raises(PaperBudgetApprovalError, match="budget digest mismatch"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=False,
            approve_budget_digest="sha256:" + "0" * 64,
        )


def test_lean_medium_lemma_dag_budget_uses_v2_expected_ai_unit_count() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_lemma_dag_pure_logic_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        topic_family="pure_logic",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert budget.planned_root_runs == 1
    assert budget.planned_ai_units == 5
    assert budget.max_provider_attempts == 5


def test_lean_medium_lemma_dag_budget_does_not_select_v1_shallow_medium() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_medium_lemma_dag_repeat0",
        domain="lean_proof",
        difficulty="medium",
        paper_difficulty="medium_lemma_dag",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
    )

    selected = catalog.cases_for(
        domain=condition.domain,
        difficulty=condition.difficulty,
        paper_difficulty=condition.paper_difficulty,
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert {case["case_id"] for case in selected} == {
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
    }
    assert all(case["paper_difficulty"] == "medium_lemma_dag" for case in selected)
    assert budget.planned_root_runs == len(selected)
    assert budget.planned_ai_units == 14


def test_budget_rejects_missing_topic_family_cell_without_fallback() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    condition = PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_hard_function_set_repeat0",
        domain="lean_proof",
        difficulty="hard",
        paper_difficulty="hard_frontier",
        topic_family="function_set",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog.catalog_digest,
    )

    with pytest.raises(ValueError, match="insufficient catalog"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=(condition,),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
        )
