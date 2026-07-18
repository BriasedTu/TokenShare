from __future__ import annotations

import importlib
import importlib.util
import json
from collections import defaultdict
from dataclasses import replace
from typing import Any

import pytest

from tokenshare.experiments.paper_experiment_contracts import (
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
)


MODULE_NAME = "tokenshare.experiments.paper_exp2_scalability"
CATALOG_DIGEST = "sha256:" + "1" * 64
ENDPOINT_DIGEST = "sha256:" + "2" * 64


def test_exp2_module_implements_contract_and_freezes_600_root_runs() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert isinstance(exp2, PaperExperimentModule)
    assert len(conditions) == 240
    assert len(selections) == len(conditions)
    assert module.count_exp2_root_runs(conditions, selections) == 600
    assert {condition.worker_count for condition in conditions} == {1, 3, 10, 30}
    assert {condition.repeat_id for condition in conditions} == {0, 1, 2, 3, 4}
    assert {condition.domain for condition in conditions} == {
        "factorization",
        "lean_proof",
    }
    assert all(condition.experiment_id == module.EXP2_EXPERIMENT_ID for condition in conditions)
    assert all(condition.model_policy == "fixed_entry" for condition in conditions)
    assert all(condition.model_entry_id == "glm_5_2_exp1_baseline" for condition in conditions)
    assert all(condition.provider_family == "siliconflow" for condition in conditions)
    assert all(condition.provider_model_id == "zai-org/GLM-5.2" for condition in conditions)
    assert all(condition.fault_type == "none" for condition in conditions)
    assert all(condition.ablation_mode == "FULL" for condition in conditions)


def test_lean_slice_ids_are_stable_2_2_1_across_worker_and_repeat() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    expected_allocations = {
        "simple": {"pure_logic": 2, "function_set": 2, "induction": 1},
        "medium_lemma_dag": {"pure_logic": 1, "function_set": 2, "induction": 2},
        "hard_frontier": {"pure_logic": 2, "function_set": 1, "induction": 2},
    }
    by_slice: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    by_digest: dict[tuple[str, str], set[str]] = defaultdict(set)
    combined_digest_by_run: dict[tuple[str, int, int], set[str]] = defaultdict(set)

    for condition, selection in zip(conditions, selections, strict=True):
        if condition.domain != "lean_proof":
            continue
        key = (condition.paper_difficulty, condition.topic_family)
        by_slice[key].add(tuple(selection.ordered_case_ids))
        by_digest[key].add(selection.selection_digest)
        combined_digest_by_run[
            (condition.paper_difficulty, condition.worker_count, condition.repeat_id)
        ].add(selection.selection_digest)

    for paper_difficulty, topic_counts in expected_allocations.items():
        for topic_family, expected_count in topic_counts.items():
            key = (paper_difficulty, topic_family)
            assert len(by_slice[key]) == 1
            assert len(next(iter(by_slice[key]))) == expected_count
            assert len(by_digest[key]) == 1

    for paper_difficulty, _worker_count, _repeat_id in combined_digest_by_run:
        expected_topic_count = len(expected_allocations[paper_difficulty])
        assert len(combined_digest_by_run[(paper_difficulty, _worker_count, _repeat_id)]) == expected_topic_count


def test_factorization_batches_are_stable_and_require_within_root_parallelism() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    by_difficulty: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for condition, selection in zip(conditions, selections, strict=True):
        if condition.domain != "factorization":
            continue
        by_difficulty[condition.paper_difficulty].add(tuple(selection.ordered_case_ids))
        assert len(selection.ordered_case_ids) == 5
        assert selection.expected_ai_unit_count > len(selection.ordered_case_ids)

    assert set(by_difficulty) == {"easy", "medium", "hard"}
    assert all(len(case_id_sets) == 1 for case_id_sets in by_difficulty.values())

    bad_catalog = _catalog()
    bad_catalog["exp2"]["factorization"]["medium"][0]["parallelism_scope"] = (
        "independent_integer_batch"
    )
    bad_context = _context(catalog=bad_catalog)
    with pytest.raises(ValueError, match="within-root range children"):
        exp2.freeze_case_selections(
            bad_context,
            exp2.expand_conditions(bad_context),
        )


def test_optional_worker_levels_require_ai_units_quota_and_real_worker_preflight() -> None:
    module = _load_module()
    context = _context()

    support = module.evaluate_exp2_optional_worker_levels(context)

    assert [row["worker_count"] for row in support] == [100, 300]
    assert all(row["status"] == "unsupported_worker_level" for row in support)
    assert all(row["provider_calls_made"] == 0 for row in support)
    assert "ai_unit" in support[0]["unsupported_reasons"]
    assert "quota" in support[1]["unsupported_reasons"]
    assert "real_worker_preflight" in support[1]["unsupported_reasons"]

    supported_catalog = _catalog()
    supported_catalog["exp2"]["optional_worker_preflight"]["100"] = {
        "ai_unit_count_available": 120,
        "quota_status": "passed",
        "real_worker_preflight_status": "passed",
    }
    mixed_support = module.evaluate_exp2_optional_worker_levels(
        _context(catalog=supported_catalog)
    )

    assert mixed_support[0]["worker_count"] == 100
    assert mixed_support[0]["status"] == "supported"
    assert mixed_support[1]["worker_count"] == 300
    assert mixed_support[1]["status"] == "unsupported_worker_level"


def test_run_condition_rejects_model_drift_before_callback() -> None:
    module = _load_module()
    calls: list[str] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        calls.append(kwargs["condition"].condition_id)
        return PaperConditionResult(
            condition_id=kwargs["condition"].condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=len(kwargs["selection"].ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=len(kwargs["selection"].ordered_case_ids),
            provider_attempt_count=0,
            metrics_ref=None,
        )

    exp2 = module.Experiment2ScalabilityModule()
    context = _context(callback=callback)
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition = conditions[0]
    selection = selections[0]

    assert exp2.run_condition(context, condition, selection).condition_id == (
        condition.condition_id
    )
    assert calls == [condition.condition_id]

    drifted = replace(condition, model_entry_id="qwen3_6_27b_siliconflow")
    with pytest.raises(ValueError, match="GLM-5.2 baseline model identity"):
        exp2.run_condition(context, drifted, selection)
    assert calls == [condition.condition_id]


def test_summary_uses_wall_clock_critical_path_and_not_provider_latency_sum() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    baseline_condition, baseline_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="easy",
        worker_count=1,
        repeat_id=0,
    )
    scaled_condition, scaled_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="easy",
        worker_count=3,
        repeat_id=0,
    )

    summary = exp2.summarize(
        {
            "condition_evidence": [
                _condition_evidence(
                    baseline_condition,
                    baseline_selection,
                    task_wall_clock_ms=1000,
                    task_critical_path_ms=1000,
                    rate_limited_429_count=0,
                    retry_count=0,
                ),
                _condition_evidence(
                    scaled_condition,
                    scaled_selection,
                    task_wall_clock_ms=400,
                    task_critical_path_ms=250,
                    rate_limited_429_count=2,
                    retry_count=1,
                    provider_latency_ms_per_task=9999,
                ),
            ]
        }
    )

    rows = {row["condition_id"]: row for row in summary.rows}
    scaled = rows[scaled_condition.condition_id]

    assert scaled["wall_clock_ms"] == 400
    assert scaled["critical_path_ms"] == 250
    assert scaled["provider_latency_sum_ms"] == 5 * 2 * 9999
    assert scaled["speedup"] == 2.5
    assert scaled["efficiency"] == pytest.approx(2.5 / 3)
    assert scaled["throughput_completed_roots_per_second"] == 12.5
    assert scaled["rate_limited_429_count"] == 10
    assert scaled["retry_count"] == 5
    assert scaled["rate_limit_sensitivity"] == "rate_limited"
    assert scaled["included_in_rate_limit_excluded_view"] is False


def test_summary_rejects_scripted_evidence_claimed_as_paper_eligible() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="easy",
        worker_count=1,
        repeat_id=0,
    )

    evidence = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="scripted",
    )

    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        exp2.summarize({"condition_evidence": [evidence]})


def test_summary_zero_baseline_returns_null_without_nan_or_infinity() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    baseline_condition, baseline_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="easy",
        worker_count=1,
        repeat_id=1,
    )
    scaled_condition, scaled_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="easy",
        worker_count=10,
        repeat_id=1,
    )

    summary = exp2.summarize(
        {
            "condition_evidence": [
                _condition_evidence(
                    baseline_condition,
                    baseline_selection,
                    task_wall_clock_ms=0,
                    task_critical_path_ms=0,
                ),
                _condition_evidence(
                    scaled_condition,
                    scaled_selection,
                    task_wall_clock_ms=250,
                    task_critical_path_ms=200,
                ),
            ]
        }
    )
    row = {
        candidate["condition_id"]: candidate for candidate in summary.rows
    }[scaled_condition.condition_id]
    encoded = json.dumps(row, sort_keys=True)

    assert row["speedup"] is None
    assert row["efficiency"] is None
    assert row["speedup_applicability"] == "zero_baseline_denominator"
    assert "NaN" not in encoded
    assert "Infinity" not in encoded


def _load_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "Exp2 scalability module must exist"
    return importlib.import_module(MODULE_NAME)


def _context(
    *,
    catalog: dict[str, Any] | None = None,
    callback=None,
    hard_limits: dict[str, Any] | None = None,
) -> PaperExecutionContext:
    def default_callback(**kwargs: Any) -> PaperConditionResult:
        condition = kwargs["condition"]
        selection = kwargs["selection"]
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.BLOCKED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=len(selection.ordered_case_ids),
            provider_attempt_count=0,
            metrics_ref=None,
        )

    return PaperExecutionContext(
        context_id="exp2_test_context",
        catalog=catalog or _catalog(),
        approved_endpoint_binding={"model_endpoint_identity_digest": ENDPOINT_DIGEST},
        request_limits={"max_tokens": 1024},
        hard_limits=hard_limits or {"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp2_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or default_callback,
    )


def _catalog() -> dict[str, Any]:
    return {
        "suite_version": "paper_v1",
        "catalog_version": "v1",
        "catalog_digest": CATALOG_DIGEST,
        "exp2": {
            "factorization": {
                difficulty: [
                    {
                        "case_id": f"factorization_{difficulty}_{index:02d}",
                        "expected_ai_unit_count": 3 + index,
                        "parallelism_scope": "within_root_range_children",
                    }
                    for index in range(1, 6)
                ]
                for difficulty in ("easy", "medium", "hard")
            },
            "lean_proof": {
                "simple": {
                    "pure_logic": _lean_cases("simple", "pure_logic", 2, 2),
                    "function_set": _lean_cases("simple", "function_set", 2, 2),
                    "induction": _lean_cases("simple", "induction", 1, 2),
                },
                "medium_lemma_dag": {
                    "pure_logic": _lean_cases("medium_lemma_dag", "pure_logic", 1, 4),
                    "function_set": _lean_cases("medium_lemma_dag", "function_set", 2, 4),
                    "induction": _lean_cases("medium_lemma_dag", "induction", 2, 4),
                },
                "hard_frontier": {
                    "pure_logic": _lean_cases("hard_frontier", "pure_logic", 2, 6),
                    "function_set": _lean_cases("hard_frontier", "function_set", 1, 6),
                    "induction": _lean_cases("hard_frontier", "induction", 2, 6),
                },
            },
            "optional_worker_preflight": {
                "100": {
                    "ai_unit_count_available": 90,
                    "quota_status": "passed",
                    "real_worker_preflight_status": "passed",
                },
                "300": {
                    "ai_unit_count_available": 320,
                    "quota_status": "blocked",
                    "real_worker_preflight_status": "not_checked",
                },
            },
        },
    }


def _lean_cases(
    paper_difficulty: str,
    topic_family: str,
    count: int,
    expected_ai_unit_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "case_id": f"lean_{paper_difficulty}_{topic_family}_{index:02d}",
            "expected_ai_unit_count": expected_ai_unit_count,
        }
        for index in range(1, count + 1)
    ]


def _find_condition(
    conditions: tuple[PaperExperimentCondition, ...],
    selections,
    *,
    domain: str,
    paper_difficulty: str,
    worker_count: int,
    repeat_id: int,
):
    for condition, selection in zip(conditions, selections, strict=True):
        if (
            condition.domain == domain
            and condition.paper_difficulty == paper_difficulty
            and condition.worker_count == worker_count
            and condition.repeat_id == repeat_id
        ):
            return condition, selection
    raise AssertionError("condition not found")


def _condition_evidence(
    condition: PaperExperimentCondition,
    selection,
    *,
    task_wall_clock_ms: int,
    task_critical_path_ms: int,
    rate_limited_429_count: int = 0,
    retry_count: int = 0,
    provider_latency_ms_per_task: int = 100,
    paper_eligible: bool = False,
    transport_kind: str = "scripted",
) -> dict[str, Any]:
    tasks = []
    for index, case_id in enumerate(selection.ordered_case_ids):
        tasks.append(
            {
                "task_id": case_id,
                "root_status": "completed",
                "accepted_validity": True,
                "started_at_ms": 0,
                "ended_at_ms": task_wall_clock_ms,
                "total_tokens": 100 + index,
                "cost_estimate": 0.01,
                "rate_limited_429_count": rate_limited_429_count,
                "retry_count": retry_count,
                "paper_eligible": paper_eligible,
                "transport_kind": transport_kind,
                "ai_units": [
                    {
                        "unit_id": f"{case_id}_left",
                        "dependencies": [],
                        "started_at_ms": 0,
                        "ended_at_ms": task_critical_path_ms // 2,
                        "provider_latency_ms": provider_latency_ms_per_task,
                    },
                    {
                        "unit_id": f"{case_id}_right",
                        "dependencies": [],
                        "started_at_ms": 0,
                        "ended_at_ms": task_critical_path_ms // 2,
                        "provider_latency_ms": provider_latency_ms_per_task,
                    },
                ],
                "merge_gates": [
                    {
                        "gate_id": f"{case_id}_merge",
                        "dependencies": [f"{case_id}_left", f"{case_id}_right"],
                        "started_at_ms": task_critical_path_ms // 2,
                        "ended_at_ms": task_critical_path_ms,
                    }
                ],
            }
        )
    return {
        "condition": condition.to_dict(),
        "selection": selection.to_dict(),
        "transport_kind": transport_kind,
        "paper_eligible": paper_eligible,
        "tasks": tasks,
    }
