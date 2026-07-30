import json
from dataclasses import replace
from pathlib import Path

import pytest

from tokenshare.experiments.paper_budget import (
    PAPER_EXPERIMENT_TASK_LIMITS,
    PaperBudgetApprovalError,
    load_exp1_pilot_profile,
    plan_exp1_pilot,
    plan_paper_suite,
)
from tokenshare.experiments.paper_catalog import (
    PaperInputCatalogManifest,
    load_paper_catalogs,
)
from tokenshare.experiments.paper_models import (
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_model_policy import (
    PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
    PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS,
)
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


def _raw_paper_catalog_manifest() -> PaperInputCatalogManifest:
    factorization_cases = tuple(
        {**case, "paper_difficulty": case["difficulty"]}
        for case in _read_jsonl(
            Path("benchmarks/paper/factorization_catalog.v1.jsonl")
        )
    )
    lean_cases = tuple(
        {
            **case,
            "paper_difficulty": "simple",
            "topic_family": "pure_logic",
            "topic_family_version": "shallow_v1",
        }
        for case in _read_jsonl(Path("benchmarks/paper/lean_catalog.v1.jsonl"))
    )
    lean_lemma_graph_cases = tuple(
        _read_jsonl(
            Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
        )
    )
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    return PaperInputCatalogManifest(
        catalog_id="tokenshare.paper.catalog",
        catalog_version="v1",
        catalog_digest=digest_json(digest_body),
        generator_version="budget_json_fixture",
        case_count=(
            len(factorization_cases)
            + len(lean_cases)
            + len(lean_lemma_graph_cases)
        ),
        domain_counts={},
        difficulty_counts={},
        paper_difficulty_counts={},
        topic_family_counts={},
        paper_difficulty_topic_family_counts={},
        oracle_validation_status="passed",
        lean_preflight_status="passed",
        lean_preflight_summary={},
        lean_lemma_graph_preflight_summary={},
        created_at="2026-07-20T00:00:00Z",
        source_files=[],
        factorization_cases=factorization_cases,
        lean_cases=lean_cases,
        lean_lemma_graph_cases=lean_lemma_graph_cases,
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def test_exp4_budget_counts_requeue_upper_bound_without_inflating_ai_units() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    base = _sample_conditions(catalog.catalog_digest)[0]
    conditions = (
        replace(
            base,
            experiment_id="exp4_real_ai_protocol_ablation",
            condition_id="exp4-factor-easy-full",
            ablation_mode="FULL",
        ),
        replace(
            base,
            experiment_id="exp4_real_ai_protocol_ablation",
            condition_id="exp4-factor-easy-no-requeue",
            ablation_mode="NO_REQUEUE",
        ),
    )

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
    )

    assert budget.planned_ai_units == 20
    assert budget.max_provider_attempts == 30
    assert budget.token_upper_bound == 3000
    assert budget.cost_upper_bound == pytest.approx(0.3)
    assert budget.quota_preflight["budget_commitments"][
        "exp4_requeue_ai_unit_upper_bound"
    ] == 10


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


def test_budget_approval_can_be_bypassed_without_changing_budget_identity() -> None:
    catalog = _raw_paper_catalog_manifest()
    conditions = _sample_conditions(catalog.catalog_digest)
    plan_kwargs = {
        "catalog_manifest": catalog,
        "conditions": conditions,
        "max_provider_attempts_per_ai_unit": 1,
        "token_upper_bound_per_provider_attempt": 100,
        "cost_upper_bound_per_provider_attempt": 0.01,
    }
    planned = plan_paper_suite(**plan_kwargs, plan_only=True)
    bypassed = plan_paper_suite(
        **plan_kwargs,
        plan_only=False,
        budget_approval_required=False,
    )

    assert bypassed.budget_digest == planned.budget_digest
    assert bypassed.quota_preflight["budget_approval"] == {
        "approval_required": False,
        "approval_mode": "user_bypassed",
        "authorization_source": "project_policy",
        "budget_digest": bypassed.budget_digest,
        "provided_approval_digest": None,
    }
    with pytest.raises(PaperBudgetApprovalError, match="budget digest mismatch"):
        plan_paper_suite(
            **plan_kwargs,
            plan_only=False,
            budget_approval_required=False,
            approve_budget_digest="sha256:" + "0" * 64,
        )


def test_explicit_unlimited_budget_records_intent_without_total_hard_limits() -> None:
    catalog = _raw_paper_catalog_manifest()
    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=_sample_conditions(catalog.catalog_digest),
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=False,
        hard_limits={},
        budget_mode="unlimited",
        budget_approval_required=False,
    )

    body = budget.to_dict()
    approval = body["quota_preflight"]["budget_approval"]
    assert body["budget_mode"] == "unlimited"
    assert body["approval_required"] is False
    assert body["approval_mode"] == "explicit_unlimited"
    assert body["authorization_source"] == "cli"
    assert body["hard_limits"] == {}
    assert approval["approval_required"] is False
    assert approval["approval_mode"] == "explicit_unlimited"
    assert approval["authorization_source"] == "cli"
    assert body["quota_preflight"]["budget_commitments"]["hard_limits"] == {}
    assert body["max_provider_attempts"] > 0
    assert body["token_upper_bound"] > 0
    assert body["cost_upper_bound"] > 0


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
    persisted_commitments = baseline.quota_preflight["budget_commitments"]
    assert all(
        persisted_commitments[key] == value
        for key, value in commitments.items()
    )
    assert persisted_commitments["experiment_budget_identity"][
        "schema_version"
    ] == "tokenshare.paper_experiment_budget_identity.v1"
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


def test_budget_digest_uses_stable_lean_matrix_identity() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v1.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    conditions = _sample_conditions(catalog.catalog_digest)
    matrix = {
        "schema_version": "tokenshare.lean_3x3_matrix_plan.v1",
        "catalog_digest": catalog.catalog_digest,
        "matrix_digest": "sha256:" + "1" * 64,
        "environment_digest": "sha256:" + "2" * 64,
        "oracle_package_digests": ["sha256:" + "3" * 64],
        "target_case_count": 15,
        "task15_budget_input": {
            "selection_digest": "sha256:" + "4" * 64,
            "selected_case_ids_by_cell": {
                "simple/pure_logic": ["lean_case_01"],
            },
        },
        "cells": [
            {
                "paper_difficulty": "simple",
                "topic_family": "pure_logic",
                "golden_evidence_by_case_id": {
                    "lean_case_01": {
                        "root_checker_report_ref": {
                            "content_hash": "sha256:" + "5" * 64,
                        },
                    },
                },
            },
        ],
    }

    def plan(lean_matrix):
        return plan_paper_suite(
            catalog_manifest=catalog,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=1024,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            lean_3x3_matrix=lean_matrix,
        )

    baseline = plan(matrix)
    refreshed_evidence = {
        **matrix,
        "cells": [
            {
                **matrix["cells"][0],
                "golden_evidence_by_case_id": {
                    "lean_case_01": {
                        "root_checker_report_ref": {
                            "content_hash": "sha256:" + "6" * 64,
                        },
                    },
                },
            },
        ],
    }
    changed_matrix = {
        **refreshed_evidence,
        "matrix_digest": "sha256:" + "7" * 64,
    }

    assert plan(refreshed_evidence).budget_digest == baseline.budget_digest
    assert plan(changed_matrix).budget_digest != baseline.budget_digest
    assert (
        plan(refreshed_evidence).quota_preflight["lean_3x3_matrix"]
        == refreshed_evidence
    )


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


def test_exp5_budget_has_no_five_root_cap_and_binds_request_controls() -> None:
    assert (
        "exp5_real_ai_model_endpoint_comparison"
        not in PAPER_EXPERIMENT_TASK_LIMITS
    )
    catalog = load_paper_catalogs(
        factorization_path=Path(
            "benchmarks/paper/factorization_catalog.v1.jsonl"
        ),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
    )
    condition = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=_exp5_preflight(
            source_digest="sha256:" + "3" * 64
        ),
    )[0]
    controls_a = _exp5_preflight(source_digest="sha256:" + "3" * 64)
    controls_b = json.loads(json.dumps(controls_a))
    controls_a["request_controls_snapshot"] = {
        "temperature": 0.0,
        "top_p": 0.9,
        "stream": False,
    }
    controls_b["request_controls_snapshot"] = {
        "temperature": 0.0,
        "top_p": 0.8,
        "stream": False,
    }

    def plan(preflight: dict) -> str:
        return plan_paper_suite(
            catalog_manifest=catalog,
            conditions=(condition,),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            model_endpoint_cohort_preflight=preflight,
        ).budget_digest

    assert plan(controls_a) != plan(controls_b)


def test_exp5_v3_budget_uses_per_endpoint_token_and_pricing_identity() -> None:
    (
        catalog,
        conditions,
        selections,
        preflight,
        token_ceilings,
    ) = _exp5_v3_budget_fixture()

    budget = plan_paper_suite(
        catalog_manifest=catalog,
        conditions=conditions,
        max_provider_attempts_per_ai_unit=1,
        token_upper_bound_per_provider_attempt=100,
        token_upper_bound_by_endpoint_identity_digest=token_ceilings,
        cost_upper_bound_per_provider_attempt=0.01,
        plan_only=True,
        frozen_selections=selections,
        model_endpoint_cohort_preflight=preflight,
    )

    expected_by_member = {
        "glm_5_2_siliconflow": 172_130_304,
        "qwen3_14b_siliconflow": 172_130_304,
        "minimax_m2_5_siliconflow": 172_130_304,
        "deepseek_v3_pro_siliconflow": 91_127_808,
    }
    assert budget.planned_ai_units == 9_888
    assert budget.max_provider_attempts == 9_888
    assert budget.token_upper_bound == 607_518_720
    assert budget.quota_preflight["provider_calls_made"] == 0
    assert budget.quota_preflight["token_upper_bound_by_member"] == (
        expected_by_member
    )
    identity = budget.quota_preflight["budget_commitments"][
        "endpoint_budget_identity"
    ]
    assert identity["token_upper_bound_by_endpoint_identity_digest"] == (
        token_ceilings
    )
    assert identity["token_upper_bound_by_member"] == expected_by_member
    assert set(identity["pricing_snapshot_digest_by_member"]) == set(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS
    )
    assert all(
        subtotal["planned_ai_unit_count"] == 2_472
        and subtotal["provider_attempt_upper_bound"] == 2_472
        and subtotal["token_upper_bound"] == expected_by_member[member_id]
        and subtotal["cost_upper_bound"] > 0
        for member_id, subtotal in identity["member_token_cost_subtotals"].items()
    )
    assert budget.cost_upper_bound == pytest.approx(
        sum(
            subtotal["cost_upper_bound"]
            for subtotal in identity["member_token_cost_subtotals"].values()
        )
    )


@pytest.mark.parametrize(
    "drift_kind",
    ("missing", "extra", "zero", "negative", "bool", "float"),
)
def test_exp5_v3_budget_rejects_invalid_endpoint_token_mapping(
    drift_kind: str,
) -> None:
    catalog, conditions, selections, preflight, token_ceilings = (
        _exp5_v3_budget_fixture()
    )
    drifted = dict(token_ceilings)
    first_digest = next(iter(drifted))
    if drift_kind == "missing":
        drifted.pop(first_digest)
    elif drift_kind == "extra":
        drifted["sha256:" + "f" * 64] = 36_864
    elif drift_kind == "zero":
        drifted[first_digest] = 0
    elif drift_kind == "negative":
        drifted[first_digest] = -1
    elif drift_kind == "bool":
        drifted[first_digest] = True
    else:
        drifted[first_digest] = 69_632.0

    with pytest.raises(ValueError, match="endpoint token ceiling"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            token_upper_bound_by_endpoint_identity_digest=drifted,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            frozen_selections=selections,
            model_endpoint_cohort_preflight=preflight,
        )


def test_exp5_v3_budget_rejects_condition_and_pricing_identity_drift() -> None:
    catalog, conditions, selections, preflight, token_ceilings = (
        _exp5_v3_budget_fixture()
    )
    drifted_conditions = list(conditions)
    drifted_conditions[0] = replace(
        drifted_conditions[0],
        model_endpoint_identity_digest="sha256:" + "f" * 64,
    )
    drifted_selections = list(selections)
    drifted_selections[0] = {
        **drifted_selections[0],
        "condition_digest": drifted_conditions[0].condition_digest,
    }
    with pytest.raises(ValueError, match="endpoint identity drift"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=tuple(drifted_conditions),
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            token_upper_bound_by_endpoint_identity_digest=token_ceilings,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            frozen_selections=drifted_selections,
            model_endpoint_cohort_preflight=preflight,
        )

    pricing_drift = json.loads(json.dumps(preflight))
    member_id = PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS[0]
    pricing_drift["member_plans"][member_id]["pricing_snapshot_digest"] = (
        "sha256:" + "e" * 64
    )
    with pytest.raises(ValueError, match="pricing snapshot digest drift"):
        plan_paper_suite(
            catalog_manifest=catalog,
            conditions=conditions,
            max_provider_attempts_per_ai_unit=1,
            token_upper_bound_per_provider_attempt=100,
            token_upper_bound_by_endpoint_identity_digest=token_ceilings,
            cost_upper_bound_per_provider_attempt=0.01,
            plan_only=True,
            frozen_selections=selections,
            model_endpoint_cohort_preflight=pricing_drift,
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
    assert body["wall_clock_estimate"] == 2200.0
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


def test_exp1_pilot_budget_bypass_records_policy_without_digest_drift() -> None:
    catalog = _raw_paper_catalog_manifest()
    profile = load_exp1_pilot_profile(
        Path("benchmarks/paper/exp1_minimal_pilot_profile.v1.json")
    )
    planned = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=True,
    )
    bypassed = plan_exp1_pilot(
        catalog_manifest=catalog,
        pilot_profile=profile,
        plan_only=False,
        budget_approval_required=False,
    )

    assert bypassed.budget_digest == planned.budget_digest
    assert bypassed.quota_preflight["budget_approval"]["approval_mode"] == (
        "user_bypassed"
    )
    assert bypassed.quota_preflight["exp1_pilot"]["approval_status"] == (
        "user_bypassed"
    )


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


def test_exp3_worker_death_budget_reuses_exp1_without_supporting_execution() -> None:
    catalog = load_paper_catalogs(
        factorization_path=Path("benchmarks/paper/factorization_catalog.v2.jsonl"),
        lean_path=Path("benchmarks/paper/lean_catalog.v1.jsonl"),
        lean_lemma_graph_path=Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"),
    )
    condition = PaperExperimentCondition(
        schema_version="tokenshare.paper_condition.v2",
        experiment_id="exp3_real_ai_fault_recovery",
        condition_id=(
            "exp3_worker_death_factorization__easy__dead1__p50__rep0"
        ),
        domain="factorization",
        difficulty="easy",
        paper_difficulty="easy",
        worker_count=10,
        fault_type="worker_death",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_entry_id="glm_5_2_exp1_baseline",
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
        plan_only=False,
        frozen_selections=[
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "ordered_case_ids": ["factor_v2_easy_001"],
            }
        ],
        hard_limits={},
        budget_approval_required=False,
        budget_mode="unlimited",
    )

    identity = budget.quota_preflight["budget_commitments"][
        "experiment_budget_identity"
    ]
    experiment_id = "exp3_real_ai_fault_recovery"
    assert identity["headline_root_runs_by_experiment"][experiment_id] == 1
    assert identity["supporting_baseline_root_runs_by_experiment"].get(
        experiment_id, 0
    ) == 0
    assert identity["supporting_baseline_ai_units_by_experiment"].get(
        experiment_id, 0
    ) == 0
    assert identity["actual_scheduled_root_runs_by_experiment"][experiment_id] == 1
    assert identity["supporting_baseline_commitments"] == []
    assert budget.planned_root_runs == 1
    assert budget.budget_mode == "unlimited"
    assert budget.approval_mode == "explicit_unlimited"
    assert budget.hard_limits == {}


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
            "thinking",
            "5",
        ),
        "deepseek_v4_pro_deepseek": (
            "deepseek",
            "deepseek-entry",
            "deepseek-v4-pro",
            "high",
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
            "cohort_id": "tokenshare.paper.model_endpoint_cohort.v2",
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


def _exp5_v3_budget_fixture() -> tuple[
    PaperInputCatalogManifest,
    tuple[PaperExperimentCondition, ...],
    list[dict],
    dict,
    dict[str, int],
]:
    source_catalog = _raw_paper_catalog_manifest()
    base_case = source_catalog.factorization_cases[0]
    case = {
        **base_case,
        "case_id": "exp5_v3_budget_2472_units",
        "target_n": 10_000_019,
        "candidate_start": 2,
        "candidate_end": 2_473,
        "candidate_divisor_count": 2_472,
        "difficulty": "hard",
        "paper_difficulty": "hard",
        "split_params": {
            **base_case["split_params"],
            "requested_child_count": 2_472,
        },
    }
    catalog_digest = digest_json(
        {
            "schema_version": "tokenshare.test.exp5_v3_budget_catalog.v1",
            "case": case,
        }
    )
    catalog = replace(
        source_catalog,
        catalog_digest=catalog_digest,
        case_count=1,
        factorization_cases=(case,),
        lean_cases=(),
        lean_lemma_graph_cases=(),
    )
    cohort_digest = "sha256:" + "a" * 64
    comparable_controls = {
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "timeout_seconds": 600,
        "max_tokens": 32_768,
        "max_provider_attempts": 1,
    }
    conditions: list[PaperExperimentCondition] = []
    selections: list[dict] = []
    member_plans: dict[str, dict] = {}
    token_ceilings: dict[str, int] = {}
    for index, member_id in enumerate(
        PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS,
        start=1,
    ):
        expected = PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBERS[member_id]
        source_digest = "sha256:" + f"{index}" * 64
        endpoint_digest = "sha256:" + f"{index + 4}" * 64
        reasoning_controls = dict(expected["request_overrides"])
        pricing_snapshot = dict(expected["pricing"])
        member_plans[member_id] = {
            "schema_version": "tokenshare.paper_model_endpoint_member_plan.v1",
            "status": "planned",
            "blocked_reasons": [],
            "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            "model_cohort_digest": cohort_digest,
            "cohort_member_id": member_id,
            "provider_config_id": "siliconflow",
            "selected_entry_id": f"{member_id}_entry",
            "provider_family": "siliconflow",
            "provider_model_id": expected["provider_model_id"],
            "reasoning_profile_id": expected["reasoning_profile_id"],
            "source_provider_config_digest": source_digest,
            "model_endpoint_identity_digest": endpoint_digest,
            "request_controls": {
                "schema_version": "tokenshare.paper_exp5_request_controls.v1",
                "comparable": comparable_controls,
                "comparable_digest": digest_json(comparable_controls),
                "provider_specific_reasoning": reasoning_controls,
                "provider_specific_reasoning_digest": digest_json(
                    reasoning_controls
                ),
            },
            "pricing_snapshot": pricing_snapshot,
            "pricing_snapshot_digest": digest_json(pricing_snapshot),
        }
        condition = PaperExperimentCondition(
            schema_version="tokenshare.paper_condition.v3",
            experiment_id="exp5_real_ai_model_endpoint_comparison",
            condition_id=f"exp5_v3_budget_{member_id}",
            domain="factorization",
            difficulty="hard",
            paper_difficulty="hard",
            worker_count=3,
            fault_type="none",
            fault_rate=0.0,
            ablation_mode="FULL",
            model_policy="fixed_entry",
            model_cohort_id=PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
            cohort_member_id=member_id,
            provider_config_id="siliconflow",
            model_entry_id=f"{member_id}_entry",
            provider_family="siliconflow",
            provider_model_id=str(expected["provider_model_id"]),
            reasoning_profile_id=str(expected["reasoning_profile_id"]),
            model_cohort_digest=cohort_digest,
            source_provider_config_digest=source_digest,
            model_endpoint_identity_digest=endpoint_digest,
            repeat_id=0,
            seed=1,
            catalog_digest=catalog_digest,
        )
        conditions.append(condition)
        selections.append(
            {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "ordered_case_ids": [case["case_id"]],
                "case_expected_ai_unit_counts": {case["case_id"]: 2_472},
            }
        )
        token_ceilings[endpoint_digest] = (
            69_632 if reasoning_controls["enable_thinking"] else 36_864
        )
    preflight = {
        "schema_version": "tokenshare.paper_model_endpoint_cohort_preflight.v1",
        "status": "planned",
        "paper_eligible_possible": True,
        "blocked_reason": None,
        "ineligibility_reasons": [],
        "provider_calls_made": 0,
        "model_policy": "fixed_entry",
        "cohort_id": PAPER_MODEL_ENDPOINT_COHORT_V3_ID,
        "model_cohort_digest": cohort_digest,
        "expected_member_ids": list(PAPER_MODEL_ENDPOINT_COHORT_V3_MEMBER_IDS),
        "member_plans": member_plans,
        "request_controls_snapshot": comparable_controls,
        "request_controls_snapshot_digest": digest_json(comparable_controls),
    }
    return catalog, tuple(conditions), selections, preflight, token_ceilings
