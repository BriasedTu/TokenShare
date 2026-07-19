import json
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.experiments.paper_budget import (
    PaperBudgetApprovalError,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import PaperExperimentCondition, PaperStatus
from tokenshare.experiments.paper_runner import expand_plan_conditions


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


def test_budget_digest_covers_gate_c_execution_commitments() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    conditions = _sample_conditions(catalog.catalog_digest)
    commitments = {
        "frozen_selections": [
            {
                "selection_id": "selection_1",
                "ordered_case_ids": ["factor_easy_01"],
                "selection_digest": "sha256:" + "1" * 64,
            }
        ],
        "ai_unit_commitments": [
            {
                "case_id": "factor_easy_01",
                "planned_ai_unit_ids": ["range_0", "range_1"],
                "commitment_digest": "sha256:" + "2" * 64,
            }
        ],
        "endpoint_identity": {
            "model_entry_id": "glm_5_2_exp1_baseline",
            "model_endpoint_identity_digest": "sha256:" + "3" * 64,
        },
        "request_limits": {"max_tokens": 1024, "timeout_seconds": 30},
        "hard_limits": {"max_total_provider_attempts": 40, "max_total_tokens": 40960},
        "suite_identity": {
            "suite_version": "paper_v1",
            "execution_scope": "formal_matrix",
            "experiment_ids": ["exp1_real_ai_feasibility"],
        },
        "output_identity": {
            "output_root": "outputs/experiments/paper_v1/exp1",
            "cross_experiment_evidence_allowed": False,
        },
    }

    def plan(**overrides):
        return plan_paper_suite(
            catalog_manifest=catalog,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=1024,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            **{**commitments, **overrides},
        )

    baseline = plan()
    assert baseline.quota_preflight["budget_commitments"] == commitments
    drifts = (
        {
            "frozen_selections": [
                {
                    **commitments["frozen_selections"][0],
                    "ordered_case_ids": ["factor_easy_02"],
                }
            ]
        },
        {
            "ai_unit_commitments": [
                {
                    **commitments["ai_unit_commitments"][0],
                    "planned_ai_unit_ids": ["range_0"],
                }
            ]
        },
        {
            "endpoint_identity": {
                **commitments["endpoint_identity"],
                "model_entry_id": "other",
            }
        },
        {"request_limits": {**commitments["request_limits"], "max_tokens": 2048}},
        {"hard_limits": {**commitments["hard_limits"], "max_total_tokens": 81920}},
        {
            "suite_identity": {
                **commitments["suite_identity"],
                "suite_version": "paper_v2",
            }
        },
        {
            "output_identity": {
                **commitments["output_identity"],
                "output_root": "outputs/experiments/paper_v1/other",
            }
        },
    )
    assert all(plan(**drift).budget_digest != baseline.budget_digest for drift in drifts)


def test_exp5_source_config_drift_changes_budget_and_invalidates_old_approval() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    original_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=_exp5_preflight(
            source_digest="sha256:" + "3" * 64
        ),
    )
    changed_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=_exp5_preflight(
            source_digest="sha256:" + "4" * 64
        ),
    )
    original_condition = original_conditions[0]
    changed_condition = changed_conditions[0]

    assert original_condition.condition_digest != changed_condition.condition_digest
    original_budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(original_condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )
    changed_budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(changed_condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert original_budget.budget_digest != changed_budget.budget_digest
    with pytest.raises(PaperBudgetApprovalError, match="budget digest mismatch"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=(changed_condition,),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=False,
            approve_budget_digest=original_budget.budget_digest,
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

    assert budget.planned_root_runs == 15
    assert budget.planned_ai_units == 75
    assert budget.max_provider_attempts == 75


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

    assert len(selected) == 45
    assert {
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
    } <= {case["case_id"] for case in selected}
    assert all(case["paper_difficulty"] == "medium_lemma_dag" for case in selected)
    assert budget.planned_root_runs == len(selected)
    assert budget.planned_ai_units == 210


def test_budget_selects_checker_backed_hard_topic_without_blocked_fallback() -> None:
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

    selected = catalog.cases_for(
        domain=condition.domain,
        difficulty=condition.difficulty,
        paper_difficulty=condition.paper_difficulty,
        topic_family=condition.topic_family,
    )
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=(condition,),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert len(selected) == 25
    assert all(case["topic_family"] == "function_set" for case in selected)
    checker_backed = [
        case
        for case in selected
        if case["preflight_status"] == "passed"
        and isinstance(case.get("oracle_proof_package_ref"), dict)
    ]
    blocked = [
        case
        for case in selected
        if case["preflight_status"] == "structured_blocked"
        and case["oracle_proof_package_ref"] is None
    ]
    assert len(checker_backed) == 15
    assert len(blocked) == 10
    assert budget.planned_root_runs == 15
    assert budget.planned_ai_units == 90


def test_exp1_minimal_pilot_budget_uses_frozen_case_order_and_real_request_profiles() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    profile = load_exp1_pilot_profile(
        Path("benchmarks/paper/exp1_minimal_pilot_profile.v1.json")
    )

    budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=True,
    )

    body = budget.to_dict()
    pilot = body["quota_preflight"]["exp1_pilot"]
    assert pilot["suite_id"] == "paper_exp1_minimal_pilot_v1"
    assert pilot["domains"] == ["factorization", "lean_proof"]
    assert pilot["worker_count"] == 10
    assert pilot["repeat_count"] == 1
    assert pilot["seed_family"] == [1]
    assert pilot["case_order"] == [
        "factor_easy_01",
        "factor_medium_01",
        "factor_hard_01",
        "lean_easy_01",
        "lean_v2_medium_lemma_dag_01",
        "lean_v2_medium_function_set_dx_subset_chain_01",
        "lean_v2_medium_induction_nat_predicate_chain_01",
        "lean_v2_hard_frontier_01",
    ]
    assert body["planned_root_runs"] == 8
    assert pilot["executable_root_runs"] == 7
    assert pilot["blocked_root_runs"] == 1
    assert body["planned_ai_units"] == 22
    assert body["max_provider_attempts"] == 22
    assert body["token_upper_bound"] == 97280
    assert body["cost_upper_bound"] == pytest.approx(0.116736)
    assert body["wall_clock_estimate"] == 660.0
    assert body["disk_estimate"]["bytes"] == 26214400
    assert body["quota_preflight"]["provider_calls_made"] == 0
    assert pilot["approval_status"] == "awaiting_user_approval"

    profiles = {
        item["case_id"]: item for item in pilot["case_request_profiles"]
    }
    assert profiles["factor_easy_01"]["split_profile"]["ranges"] == [
        {"range_start": "2", "range_end": "5"},
        {"range_start": "6", "range_end": "9"},
    ]
    assert profiles["factor_medium_01"]["ai_unit_count"] == 2
    assert profiles["factor_hard_01"]["ai_unit_count"] == 2
    assert profiles["lean_easy_01"]["paper_difficulty"] == "simple"
    assert profiles["lean_easy_01"]["legacy_difficulty"] == "easy"
    assert profiles["lean_v2_medium_lemma_dag_01"]["ai_unit_count"] == 5
    assert profiles["lean_v2_medium_function_set_dx_subset_chain_01"][
        "ai_unit_count"
    ] == 4
    assert profiles["lean_v2_medium_induction_nat_predicate_chain_01"][
        "ai_unit_count"
    ] == 5
    blocked = profiles["lean_v2_hard_frontier_01"]
    assert blocked["execution_status"] == "structured_blocked"
    assert blocked["blocked_reason"] == "missing_oracle_proof_package"
    assert blocked["ai_unit_count"] == 0
    assert blocked["provider_attempt_upper_bound"] == 0
    assert blocked["token_upper_bound"] == 0
    assert blocked["cost_upper_bound"] == 0.0


def test_exp1_pilot_budget_digest_is_stable_and_invalidates_drifted_approval(
    tmp_path: Path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    source_profile_path = Path(
        "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
    )
    source_config_path = Path(
        "benchmarks/paper/exp1_baseline_provider_config.v1.json"
    )
    original = load_exp1_pilot_profile(source_profile_path)
    same = load_exp1_pilot_profile(source_profile_path)
    original_budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=original,
        plan_only=True,
    )
    same_budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=same,
        plan_only=True,
    )
    assert original.profile_digest == same.profile_digest
    assert original_budget.budget_digest == same_budget.budget_digest

    profile_body = json.loads(source_profile_path.read_text(encoding="utf-8"))
    config_body = json.loads(source_config_path.read_text(encoding="utf-8"))
    fixture_baseline = _write_pilot_fixture(
        tmp_path / "fixture_baseline",
        profile_body=profile_body,
        config_body=config_body,
    )
    fixture_baseline_budget = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=fixture_baseline,
        plan_only=True,
    )

    changed_profile_body = json.loads(json.dumps(profile_body))
    changed_profile_body["seed_family"] = [2]
    profile_drift = _write_pilot_fixture(
        tmp_path / "profile_drift",
        profile_body=changed_profile_body,
        config_body=config_body,
    )

    changed_config_body = json.loads(json.dumps(config_body))
    changed_config_body["metadata"]["approval_snapshot"] = "drifted"
    provider_config_drift = _write_pilot_fixture(
        tmp_path / "provider_config_drift",
        profile_body=profile_body,
        config_body=changed_config_body,
    )

    changed_entry_profile = json.loads(json.dumps(profile_body))
    changed_entry_config = json.loads(json.dumps(config_body))
    changed_entry_profile["baseline_provider"]["selected_entry_id"] = (
        "glm_5_2_exp1_baseline_v2"
    )
    changed_entry_config["entries"][0]["entry_id"] = "glm_5_2_exp1_baseline_v2"
    entry_identity_drift = _write_pilot_fixture(
        tmp_path / "entry_identity_drift",
        profile_body=changed_entry_profile,
        config_body=changed_entry_config,
    )

    changed_request_body = json.loads(json.dumps(profile_body))
    changed_request_body["request_policy"]["domain_limits"]["lean_proof"][
        "max_tokens"
    ] = 1000
    request_profile_drift = _write_pilot_fixture(
        tmp_path / "request_profile_drift",
        profile_body=changed_request_body,
        config_body=config_body,
    )

    changed_catalog = replace(
        catalog,
        catalog_digest="sha256:" + "9" * 64,
    )
    drifted_budgets = [
        plan_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=profile,
            plan_only=True,
        )
        for profile in (
            profile_drift,
            provider_config_drift,
            entry_identity_drift,
            request_profile_drift,
        )
    ]
    drifted_budgets.append(
        plan_exp1_pilot(
            catalog_manifest=changed_catalog,
            pilot_profile=fixture_baseline,
            plan_only=True,
        )
    )
    assert all(
        item.budget_digest != fixture_baseline_budget.budget_digest
        for item in drifted_budgets
    )

    with pytest.raises(PaperBudgetApprovalError, match="budget digest mismatch"):
        plan_exp1_pilot(
            catalog_manifest=catalog,
            pilot_profile=request_profile_drift,
            plan_only=False,
            approve_budget_digest=fixture_baseline_budget.budget_digest,
        )


def _write_pilot_fixture(
    root: Path,
    *,
    profile_body: dict,
    config_body: dict,
):
    root.mkdir(parents=True)
    profile = json.loads(json.dumps(profile_body))
    profile["baseline_provider"]["provider_config_path"] = "provider.json"
    (root / "provider.json").write_text(
        json.dumps(config_body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    profile_path = root / "profile.json"
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return load_exp1_pilot_profile(profile_path)


def _exp5_preflight(*, source_digest: str) -> dict:
    member_specs = {
        "glm_5_2_siliconflow": (
            "siliconflow",
            "glm-entry",
            "zai-org/GLM-5.2",
            "default",
            "5",
        ),
        "qwen3_6_27b_siliconflow": (
            "siliconflow",
            "qwen-entry",
            "Qwen/Qwen3.6-27B",
            "default",
            "6",
        ),
        "gpt_5_6_sol_high_openai": (
            "openai",
            "gpt-entry",
            "gpt-5.6-sol",
            "high",
            "7",
        ),
    }
    member_plans = {}
    for member_id, (
        provider_config_id,
        entry_id,
        model_id,
        reasoning_profile_id,
        digest_character,
    ) in member_specs.items():
        member_plans[member_id] = {
            "status": "planned",
            "cohort_id": "tokenshare.paper.model_endpoint_cohort.v1",
            "cohort_member_id": member_id,
            "provider_config_id": provider_config_id,
            "selected_entry_id": entry_id,
            "provider_family": provider_config_id,
            "provider_model_id": model_id,
            "reasoning_profile_id": reasoning_profile_id,
            "source_provider_config_digest": source_digest,
            "model_endpoint_identity_digest": (
                "sha256:" + digest_character * 64
            ),
        }
    return {
        "status": "planned",
        "model_cohort_digest": "sha256:" + "2" * 64,
        "member_plans": member_plans,
    }
