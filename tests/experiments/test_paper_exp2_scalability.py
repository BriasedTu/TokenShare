from __future__ import annotations

import importlib
import importlib.util
import json
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

import pytest

from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelection,
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
SOURCE_CONFIG_DIGEST = "sha256:" + "3" * 64
REQUEST_LIMITS = {
    "max_tokens": 1024,
    "timeout_seconds": 30,
    "max_provider_attempts": 1,
    "temperature": 0.0,
    "enable_thinking": False,
}


def test_exp2_module_implements_contract_and_freezes_600_root_runs() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert isinstance(exp2, PaperExperimentModule)
    assert len(conditions) == 120
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
    assert all(
        condition.provider_config_id == "exp1_baseline_siliconflow"
        for condition in conditions
    )
    assert all(
        condition.reasoning_profile_id == "temperature_0_enable_thinking_false"
        for condition in conditions
    )
    assert all(
        condition.source_provider_config_digest == SOURCE_CONFIG_DIGEST
        for condition in conditions
    )
    assert all(
        condition.model_endpoint_identity_digest == ENDPOINT_DIGEST
        for condition in conditions
    )
    assert all(condition.fault_type == "none" for condition in conditions)
    assert all(condition.ablation_mode == "FULL" for condition in conditions)


def test_lean_conditions_are_full_5_task_batches_stable_across_worker_and_repeat() -> None:
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
    by_difficulty: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    by_digest: dict[str, set[str]] = defaultdict(set)

    for condition, selection in zip(conditions, selections, strict=True):
        if condition.domain != "lean_proof":
            continue
        by_difficulty[condition.paper_difficulty].add(tuple(selection.ordered_case_ids))
        by_digest[condition.paper_difficulty].add(selection.selection_digest)
        assert condition.topic_family is None
        assert selection.topic_family is None
        body = selection.to_dict()
        assert body["topic_family"] is None
        assert body["topic_family_marker"] == "mixed_topic_family"
        assert len(selection.ordered_case_ids) == 5
        assert _topic_counts(selection.ordered_case_ids) == expected_allocations[
            condition.paper_difficulty
        ]
        assert body["topic_family_counts"] == expected_allocations[
            condition.paper_difficulty
        ]

    assert set(by_difficulty) == set(expected_allocations)
    assert all(len(case_id_sets) == 1 for case_id_sets in by_difficulty.values())
    assert all(len(digests) == 1 for digests in by_digest.values())


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


def test_run_condition_rejects_model_or_selection_drift_before_callback() -> None:
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

    reversed_selection = FrozenCaseSelection(
        selection_id=selection.selection_id,
        experiment_id=selection.experiment_id,
        suite_version=selection.suite_version,
        catalog_version=selection.catalog_version,
        domain=selection.domain,
        paper_difficulty=selection.paper_difficulty,
        topic_family=selection.topic_family,
        ordered_case_ids=tuple(reversed(selection.ordered_case_ids)),
        catalog_digest=selection.catalog_digest,
        expected_ai_unit_count=selection.expected_ai_unit_count,
        paper_eligible_required=selection.paper_eligible_required,
    )
    with pytest.raises(ValueError, match="canonical frozen selection"):
        exp2.run_condition(context, condition, reversed_selection)
    assert calls == [condition.condition_id]

    wrong_digest_selection = FrozenCaseSelection(
        selection_id=selection.selection_id,
        experiment_id=selection.experiment_id,
        suite_version=selection.suite_version,
        catalog_version=selection.catalog_version,
        domain=selection.domain,
        paper_difficulty=selection.paper_difficulty,
        topic_family=selection.topic_family,
        ordered_case_ids=selection.ordered_case_ids,
        catalog_digest="sha256:" + "9" * 64,
        expected_ai_unit_count=selection.expected_ai_unit_count,
        paper_eligible_required=selection.paper_eligible_required,
    )
    with pytest.raises(ValueError, match="canonical frozen selection"):
        exp2.run_condition(context, condition, wrong_digest_selection)
    assert calls == [condition.condition_id]


def test_run_condition_validates_approved_endpoint_binding_before_callback() -> None:
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
    valid_context = _context(callback=callback)
    conditions = exp2.expand_conditions(valid_context)
    selections = exp2.freeze_case_selections(valid_context, conditions)
    condition = conditions[0]
    selection = selections[0]

    incomplete_context = _context(
        binding={"model_endpoint_identity_digest": ENDPOINT_DIGEST},
        callback=callback,
    )
    with pytest.raises(ValueError, match="approved endpoint binding"):
        exp2.run_condition(incomplete_context, condition, selection)
    assert calls == []

    drifted_reasoning = replace(condition, reasoning_profile_id="thinking_enabled")
    with pytest.raises(ValueError, match="reasoning_profile_id"):
        exp2.run_condition(valid_context, drifted_reasoning, selection)
    assert calls == []

    thinking_context = _context(
        binding={
            **_baseline_binding(),
            "request_controls": {"temperature": 0.0, "enable_thinking": True},
        },
        callback=callback,
    )
    with pytest.raises(ValueError, match="approved endpoint binding"):
        exp2.run_condition(thinking_context, condition, selection)
    assert calls == []

    temperature_context = _context(
        request_limits={
            "max_tokens": 1024,
            "temperature": 0.7,
            "enable_thinking": False,
        },
        callback=callback,
    )
    with pytest.raises(ValueError, match="request controls"):
        exp2.run_condition(temperature_context, condition, selection)
    assert calls == []


def test_run_condition_rejects_noncanonical_condition_and_full_request_policy_drift() -> None:
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
    drifts = {
        "repeat": replace(condition, repeat_id=1),
        "seed": replace(condition, seed=condition.seed + 1),
        "catalog": replace(condition, catalog_digest="sha256:" + "9" * 64),
        "condition_id": replace(condition, condition_id="foreign_condition"),
        "difficulty": replace(condition, difficulty="medium"),
        "real_transport": replace(condition, real_transport_required=False),
        "paper_eligibility": replace(condition, paper_eligible_required=False),
    }
    for drifted in drifts.values():
        with pytest.raises(ValueError, match="canonical Experiment 2 condition"):
            exp2.run_condition(context, drifted, selection)
    assert calls == []

    request_policy_drifts = (
        {**REQUEST_LIMITS, "max_tokens": 2048},
        {key: value for key, value in REQUEST_LIMITS.items() if key != "timeout_seconds"},
        {**REQUEST_LIMITS, "top_p": 0.9},
    )
    for request_limits in request_policy_drifts:
        with pytest.raises(ValueError, match="request limit policy"):
            exp2.run_condition(
                _context(request_limits=request_limits, callback=callback),
                condition,
                selection,
            )
    assert calls == []

    binding = _baseline_binding()
    binding["request_controls"] = {
        **binding["request_controls"],
        "top_p": 0.9,
    }
    with pytest.raises(ValueError, match="approved endpoint binding"):
        exp2.run_condition(
            _context(binding=binding, callback=callback),
            condition,
            selection,
        )
    assert calls == []

    binding = _baseline_binding()
    binding["request_limits"] = {
        **binding["request_limits"],
        "top_p": 0.9,
    }
    with pytest.raises(ValueError, match="approved request limit policy"):
        exp2.run_condition(
            _context(binding=binding, callback=callback),
            condition,
            selection,
        )
    assert calls == []


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
    assert scaled["task_batch_id"] == scaled_selection.selection_id


def test_summary_critical_path_includes_dependency_waiting_gap() -> None:
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

    summary = exp2.summarize(
        {
            "condition_evidence": [
                _condition_evidence(
                    condition,
                    selection,
                    task_wall_clock_ms=600,
                    task_critical_path_ms=100,
                    merge_start_ms=500,
                    merge_end_ms=600,
                ),
            ]
        }
    )
    row = next(iter(summary.rows))

    assert row["critical_path_ms"] == 600
    assert row["critical_path_median_ms"] == 600


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


def test_summary_requires_all_tasks_to_be_paper_eligible_for_batch_eligibility() -> None:
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
        transport_kind="ai_api",
    )
    evidence["tasks"][1]["paper_eligible"] = False
    evidence["tasks"][1]["transport_kind"] = "ai_api"

    summary = exp2.summarize({"condition_evidence": [evidence]})
    row = next(iter(summary.rows))

    assert row["paper_eligible"] is False
    assert row["paper_eligible_task_count"] == 4
    assert row["ineligible_task_count"] == 1


def test_summary_rejects_record_task_transport_conflicts() -> None:
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
        transport_kind="ai_api",
    )
    for task in evidence["tasks"]:
        task["transport_kind"] = "scripted"

    with pytest.raises(
        ValueError,
        match="transport conflict|transport cannot be paper eligible",
    ):
        exp2.summarize({"condition_evidence": [evidence]})

    evidence = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=False,
        transport_kind="ai_api",
    )
    evidence["tasks"][0]["transport_kind"] = "scripted"
    with pytest.raises(ValueError, match="transport conflict"):
        exp2.summarize({"condition_evidence": [evidence]})


def test_summary_audits_attempt_identity_transport_and_formal_provenance() -> None:
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

    probes = []
    scripted = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    scripted["tasks"][0]["attempts"][0]["transport_kind"] = "scripted"
    probes.append((scripted, "attempt transport"))

    foreign_condition = json.loads(json.dumps(scripted))
    foreign_condition["tasks"][0]["attempts"][0]["transport_kind"] = "ai_api"
    foreign_condition["tasks"][0]["condition_id"] = "foreign_condition"
    probes.append((foreign_condition, "task condition_id"))

    wrong_repeat = json.loads(json.dumps(foreign_condition))
    wrong_repeat["tasks"][0]["condition_id"] = condition.condition_id
    wrong_repeat["tasks"][0]["repeat_id"] = 999
    probes.append((wrong_repeat, "task repeat_id"))

    wrong_model = json.loads(json.dumps(wrong_repeat))
    wrong_model["tasks"][0]["repeat_id"] = condition.repeat_id
    wrong_model["tasks"][0]["attempts"][0]["entry_id"] = "other_model"
    probes.append((wrong_model, "attempt model identity"))

    wrong_task_model = json.loads(json.dumps(wrong_model))
    wrong_task_model["tasks"][0]["attempts"][0]["entry_id"] = (
        condition.model_entry_id
    )
    wrong_task_model["tasks"][0]["model_entry_id"] = "other_model"
    probes.append((wrong_task_model, "task model identity"))

    missing_attempt = json.loads(json.dumps(wrong_model))
    missing_attempt["tasks"][0]["attempts"][0]["entry_id"] = (
        condition.model_entry_id
    )
    missing_attempt["tasks"][0]["attempts"] = []
    probes.append((missing_attempt, "attempt evidence"))

    for evidence, expected_error in probes:
        with pytest.raises(ValueError, match=expected_error):
            exp2.summarize({"condition_evidence": [evidence]})

    pilot = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    pilot["pilot_only"] = True
    pilot["formal"] = False
    with pytest.raises(ValueError, match="pilot output cannot enter formal"):
        exp2.summarize({"condition_evidence": [pilot]})


def test_summary_binds_actual_task_order_and_factorization_range_children() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="medium",
        worker_count=3,
        repeat_id=0,
    )

    forged = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    forged["tasks"][0]["task_id"] = "forged_task"
    with pytest.raises(ValueError, match="frozen ordered_case_ids"):
        exp2.summarize({"condition_evidence": [forged]})

    independent = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    independent["tasks"][0]["actual_parallelism_scope"] = (
        "independent_integer_batch"
    )
    with pytest.raises(ValueError, match="within-root range children"):
        exp2.summarize({"condition_evidence": [independent]})

    conflicting_scope = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    conflicting_scope["tasks"][0]["parallelism_scope"] = (
        "independent_integer_batch"
    )
    with pytest.raises(ValueError, match="within-root range children"):
        exp2.summarize({"condition_evidence": [conflicting_scope]})

    wrong_root = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    wrong_root["tasks"][0]["ai_units"][0]["root_task_id"] = "other_root"
    with pytest.raises(ValueError, match="same factorization root"):
        exp2.summarize({"condition_evidence": [wrong_root]})

    broken_ranges = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    broken_ranges["tasks"][0]["ai_units"][1]["range_start"] += 1
    with pytest.raises(ValueError, match="contiguous range partition"):
        exp2.summarize({"condition_evidence": [broken_ranges]})


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


def test_summary_aggregates_matched_five_repeats_with_median_and_iqr() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []
    baseline_wall_clocks = [1000, 1100, 1200, 1300, 1400]
    scaled_wall_clocks = [500, 550, 600, 650, 700]

    for repeat_id, wall_clock in enumerate(baseline_wall_clocks):
        condition, selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="medium",
            worker_count=1,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                condition,
                selection,
                task_wall_clock_ms=wall_clock,
                task_critical_path_ms=wall_clock,
            )
        )
    for repeat_id, wall_clock in enumerate(scaled_wall_clocks):
        condition, selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="medium",
            worker_count=3,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                condition,
                selection,
                task_wall_clock_ms=wall_clock,
                task_critical_path_ms=wall_clock,
            )
        )

    summary = exp2.summarize({"condition_evidence": evidence_rows})
    rows = {
        row["worker_count"]: row
        for row in summary.rows
        if row["paper_difficulty"] == "medium"
    }

    assert len(rows) == 2
    assert rows[1]["repeat_count"] == 5
    assert rows[1]["wall_clock_median_ms"] == 1200
    assert rows[1]["wall_clock_iqr_ms"] == 300
    assert rows[3]["repeat_count"] == 5
    assert rows[3]["wall_clock_median_ms"] == 600
    assert rows[3]["wall_clock_iqr_ms"] == 150
    assert rows[3]["speedup_median"] == 2.0
    assert rows[3]["speedup_iqr"] == 0.0
    assert rows[3]["task_batch_id"] == rows[1]["task_batch_id"]
    assert rows[3]["root_run_count"] == 25


def test_summary_marks_incomplete_repeat_groups_ineligible() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(4):
        condition, selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
            worker_count=1,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                condition,
                selection,
                task_wall_clock_ms=1000 + repeat_id * 100,
                task_critical_path_ms=1000 + repeat_id * 100,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )

    summary = exp2.summarize({"condition_evidence": evidence_rows})
    row = next(iter(summary.rows))

    assert row["repeat_count"] == 4
    assert row["repeat_set_status"] == "incomplete_or_duplicate"
    assert row["expected_repeat_ids"] == (0, 1, 2, 3, 4)
    assert row["missing_repeat_ids"] == (4,)
    assert row["duplicate_repeat_ids"] == ()
    assert row["expected_root_run_count"] == 25
    assert row["root_run_count"] == 20
    assert row["paper_eligible"] is False


def test_summary_keeps_no_429_sensitivity_stats_for_complete_repeat_groups() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(5):
        baseline_condition, baseline_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="easy",
            worker_count=1,
            repeat_id=repeat_id,
        )
        scaled_condition, scaled_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="easy",
            worker_count=3,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                baseline_condition,
                baseline_selection,
                task_wall_clock_ms=1000,
                task_critical_path_ms=1000,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )
        evidence_rows.append(
            _condition_evidence(
                scaled_condition,
                scaled_selection,
                task_wall_clock_ms=500,
                task_critical_path_ms=500,
                rate_limited_429_count=1 if repeat_id == 2 else 0,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )

    summary = exp2.summarize({"condition_evidence": evidence_rows})
    rows = {
        row["worker_count"]: row
        for row in summary.rows
        if row["paper_difficulty"] == "easy"
    }

    assert rows[3]["repeat_set_status"] == "complete"
    assert rows[3]["repeat_count"] == 5
    assert rows[3]["root_run_count"] == 25
    assert rows[3]["paper_eligible"] is True
    assert rows[3]["rate_limit_sensitivity"] == "rate_limited"
    assert rows[3]["included_in_rate_limit_excluded_view"] is False
    assert rows[3]["rate_limit_excluded_repeat_count"] == 4
    assert rows[3]["rate_limit_excluded_root_run_count"] == 20
    assert rows[3]["rate_limit_excluded_wall_clock_median_ms"] == 500
    assert rows[3]["rate_limit_excluded_speedup_median"] == 2.0


def test_summary_excludes_repeat_when_matched_worker_one_baseline_has_429() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(5):
        baseline_condition, baseline_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="easy",
            worker_count=1,
            repeat_id=repeat_id,
        )
        scaled_condition, scaled_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="easy",
            worker_count=3,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                baseline_condition,
                baseline_selection,
                task_wall_clock_ms=1000,
                task_critical_path_ms=1000,
                rate_limited_429_count=1 if repeat_id == 2 else 0,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )
        evidence_rows.append(
            _condition_evidence(
                scaled_condition,
                scaled_selection,
                task_wall_clock_ms=500,
                task_critical_path_ms=500,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )

    summary = exp2.summarize({"condition_evidence": evidence_rows})
    rows = {
        row["worker_count"]: row
        for row in summary.rows
        if row["paper_difficulty"] == "easy"
    }

    assert rows[1]["rate_limit_excluded_repeat_count"] == 4
    assert rows[3]["rate_limit_excluded_repeat_count"] == 4
    assert rows[3]["rate_limit_excluded_root_run_count"] == 20
    assert rows[3]["rate_limit_excluded_speedup_median"] == 2.0
    assert rows[3]["matched_baseline_rate_limited_429_count"] == 5
    assert rows[3]["rate_limit_sensitivity"] == "matched_baseline_rate_limited"


def test_summary_uses_authoritative_batch_bounds_for_wall_clock() -> None:
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
        task_wall_clock_ms=600,
        task_critical_path_ms=600,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    evidence["batch_started_at_ms"] = 0
    evidence["batch_ended_at_ms"] = 1000
    for task in evidence["tasks"]:
        task["started_at_ms"] = 200
        task["ended_at_ms"] = 800

    row = next(iter(exp2.summarize({"condition_evidence": [evidence]}).rows))

    assert row["wall_clock_ms"] == 1000
    assert row["batch_timing_status"] == "authoritative"
    assert row["throughput_completed_roots_per_second"] == 5.0

    fallback = json.loads(json.dumps(evidence))
    fallback.pop("batch_started_at_ms")
    fallback.pop("batch_ended_at_ms")
    fallback_row = next(
        iter(exp2.summarize({"condition_evidence": [fallback]}).rows)
    )
    assert fallback_row["wall_clock_ms"] == 600
    assert fallback_row["batch_timing_status"] == "missing_authoritative_batch_bounds"
    assert fallback_row["paper_eligible"] is False


def _load_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "Exp2 scalability module must exist"
    return importlib.import_module(MODULE_NAME)


def _context(
    *,
    catalog: dict[str, Any] | None = None,
    binding: dict[str, Any] | None = None,
    callback=None,
    hard_limits: dict[str, Any] | None = None,
    request_limits: dict[str, Any] | None = None,
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
        approved_endpoint_binding=binding or _baseline_binding(),
        request_limits=request_limits or dict(REQUEST_LIMITS),
        hard_limits=hard_limits or {"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp2_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback or default_callback,
    )


def _baseline_binding() -> dict[str, Any]:
    return {
        "provider_config_id": "exp1_baseline_siliconflow",
        "selected_entry_id": "glm_5_2_exp1_baseline",
        "model_entry_id": "glm_5_2_exp1_baseline",
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "temperature_0_enable_thinking_false",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": {"temperature": 0.0, "enable_thinking": False},
        "request_limits": dict(REQUEST_LIMITS),
    }


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
    merge_start_ms: int | None = None,
    merge_end_ms: int | None = None,
) -> dict[str, Any]:
    tasks = []
    for index, case_id in enumerate(selection.ordered_case_ids):
        left_unit_id = f"{case_id}_left"
        right_unit_id = f"{case_id}_right"
        unit_kind = (
            "factorization_range_child"
            if condition.domain == "factorization"
            else "lean_proof_child"
        )
        ai_units = [
            {
                "unit_id": left_unit_id,
                "root_task_id": case_id,
                "unit_kind": unit_kind,
                "plugin_generated": True,
                "range_start": 2,
                "range_end": 3,
                "dependencies": [],
                "started_at_ms": 0,
                "ended_at_ms": task_critical_path_ms // 2,
                "provider_latency_ms": provider_latency_ms_per_task,
            },
            {
                "unit_id": right_unit_id,
                "root_task_id": case_id,
                "unit_kind": unit_kind,
                "plugin_generated": True,
                "range_start": 4,
                "range_end": 5,
                "dependencies": [],
                "started_at_ms": 0,
                "ended_at_ms": task_critical_path_ms // 2,
                "provider_latency_ms": provider_latency_ms_per_task,
            },
        ]
        attempts = [
            {
                "attempt_id": f"{unit['unit_id']}_attempt_0",
                "condition_id": condition.condition_id,
                "repeat_id": condition.repeat_id,
                "task_id": case_id,
                "unit_id": unit["unit_id"],
                "transport_kind": transport_kind,
                "provider": condition.provider_family,
                "model": condition.provider_model_id,
                "entry_id": condition.model_entry_id,
                "reasoning_profile_id": condition.reasoning_profile_id,
                "source_provider_config_digest": (
                    condition.source_provider_config_digest
                ),
                "model_endpoint_identity_digest": (
                    condition.model_endpoint_identity_digest
                ),
                "request_ref": {"artifact_id": f"request_{unit['unit_id']}"},
                "raw_output_ref": {"artifact_id": f"raw_{unit['unit_id']}"},
                "provenance_ref": {"artifact_id": f"provenance_{unit['unit_id']}"},
                "usage_ref": {"artifact_id": f"usage_{unit['unit_id']}"},
                "model_execution_record_ref": {
                    "artifact_id": f"model_record_{unit['unit_id']}"
                },
                "paper_eligible": paper_eligible,
            }
            for unit in ai_units
        ]
        tasks.append(
            {
                "task_id": case_id,
                "condition_id": condition.condition_id,
                "repeat_id": condition.repeat_id,
                "domain": condition.domain,
                "paper_difficulty": condition.paper_difficulty,
                "model_entry_id": condition.model_entry_id,
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
                "root_task_id": case_id,
                "actual_parallelism_scope": (
                    "within_root_range_children"
                    if condition.domain == "factorization"
                    else "dependency_graph_proof_units"
                ),
                "candidate_start": 2,
                "candidate_end": 5,
                "ai_units": ai_units,
                "attempts": attempts,
                "merge_gates": [
                    {
                        "gate_id": f"{case_id}_merge",
                        "dependencies": [left_unit_id, right_unit_id],
                        "started_at_ms": (
                            task_critical_path_ms // 2
                            if merge_start_ms is None
                            else merge_start_ms
                        ),
                        "ended_at_ms": (
                            task_critical_path_ms
                            if merge_end_ms is None
                            else merge_end_ms
                        ),
                    }
                ],
            }
        )
    return {
        "condition": condition.to_dict(),
        "selection": selection.to_dict(),
        "transport_kind": transport_kind,
        "paper_eligible": paper_eligible,
        "formal": True,
        "pilot_only": False,
        "batch_started_at_ms": 0,
        "batch_ended_at_ms": task_wall_clock_ms,
        "tasks": tasks,
    }


def _topic_counts(case_ids) -> dict[str, int]:
    counts = Counter()
    for case_id in case_ids:
        for topic_family in ("pure_logic", "function_set", "induction"):
            if f"_{topic_family}_" in case_id:
                counts[topic_family] += 1
                break
    return dict(counts)
