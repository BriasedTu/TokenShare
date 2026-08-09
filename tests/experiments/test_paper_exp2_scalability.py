from __future__ import annotations

import importlib
import importlib.util
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments.paper_experiment_contracts import (
    FrozenCaseSelection,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_models import (
    PaperConditionResult,
    PaperExperimentCondition,
    PaperStatus,
    digest_json,
)
from tokenshare.experiments.paper_model_identity import build_model_endpoint_identity
from tokenshare.experiments.paper_formal_callbacks import run_scheduled_cases
from tokenshare.experiments.paper_runner import normalize_experiment_ids
from tokenshare.experiments.paper_suite_scale import (
    build_paper_suite_scale_policy,
    load_paper_suite_scale_profile,
)


MODULE_NAME = "tokenshare.experiments.paper_exp2_scalability"
CATALOG_DIGEST = "sha256:" + "1" * 64
ENDPOINT_DIGEST = "sha256:" + "2" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "3" * 64
REQUEST_LIMITS = {
    "max_tokens": 300_000,
    "timeout_seconds": 600,
    "max_provider_attempts": 1,
    "stream": False,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
}


def test_exp2_runtime_capacity_and_metrics_come_from_same_root_unit_facts() -> None:
    calls: list[tuple[str, int]] = []

    def execute(case_id: str, worker_capacity: int) -> dict[str, Any]:
        calls.append((case_id, worker_capacity))
        return {
            "case_id": case_id,
            "provider_latency_ms": 9000,
            "runtime_records": (
                _runtime_record(
                    "root",
                    "2026-07-22T00:00:00.000Z",
                    "2026-07-22T00:00:00.100Z",
                ),
                _runtime_record(
                    "child-left",
                    "2026-07-22T00:00:00.100Z",
                    "2026-07-22T00:00:00.300Z",
                    dependencies=("root",),
                ),
                _runtime_record(
                    "child-right",
                    "2026-07-22T00:00:00.100Z",
                    "2026-07-22T00:00:00.250Z",
                    dependencies=("root",),
                ),
                _runtime_record(
                    "merge",
                    "2026-07-22T00:00:00.300Z",
                    "2026-07-22T00:00:00.350Z",
                    dependencies=("child-left", "child-right"),
                ),
            ),
            "protocol_events": (
                {"event_id": "event-lease", "event_type": "LEASE_STATE_CHANGED"},
            ),
        }

    result = run_scheduled_cases(
        ordered_case_ids=("case-one-root",),
        worker_count=3,
        execute_case=execute,
    )

    assert calls == [("case-one-root", 3)]
    assert result.metrics["worker_count"] == 3
    assert result.metrics["observed_max_parallel_slots"] == 2
    assert result.metrics["wall_clock_ms"] == 350
    assert result.metrics["critical_path_ms"] == 350
    assert result.metrics["provider_latency_sum_ms"] == 9000
    assert result.metrics["throughput_completed_units_per_second"] == pytest.approx(
        4 / 0.35
    )
    assert result.events == (
        {"event_id": "event-lease", "event_type": "LEASE_STATE_CHANGED"},
    )
    assert not any(
        event.get("event_type") in {"AI_UNIT_STARTED", "AI_UNIT_ENDED"}
        for event in result.events
    )


def _runtime_record(
    unit_id: str,
    started_at: str,
    ended_at: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "dependencies": list(dependencies),
        "result_kind": "succeeded",
    }


def test_exp2_module_implements_hard_only_600_root_run_contract() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    catalog, _readiness = _formal_catalog_from_json_files()
    context = _context(catalog=catalog)

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert isinstance(exp2, PaperExperimentModule)
    assert len(conditions) == 12
    assert len(selections) == len(conditions)
    assert module.count_exp2_root_runs(conditions, selections) == 600
    assert {condition.worker_count for condition in conditions} == {1, 3, 7, 10, 30, 50}
    assert {condition.repeat_id for condition in conditions} == {0, 1}
    assert {condition.domain for condition in conditions} == {"factorization"}
    assert {condition.paper_difficulty for condition in conditions} == {"hard"}
    assert all(len(selection.ordered_case_ids) == 50 for selection in selections)
    assert all(
        selection.to_dict()["split_profile_id"]
        == "factorization.exp2_contiguous_20way.v1"
        for selection in selections
    )
    assert sum(selection.expected_ai_unit_count for selection in selections) == 12_000
    assert all(condition.experiment_id == module.EXP2_EXPERIMENT_ID for condition in conditions)
    assert all(condition.model_policy == "fixed_entry" for condition in conditions)
    assert all(
        condition.model_entry_id == "deepseek_v4_pro_exp1_baseline"
        for condition in conditions
    )
    assert all(condition.provider_family == "deepseek" for condition in conditions)
    assert all(condition.provider_model_id == "deepseek-v4-pro" for condition in conditions)
    assert all(
        condition.provider_config_id == "exp1_baseline_deepseek"
        for condition in conditions
    )
    assert all(
        condition.reasoning_profile_id == "high"
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


def test_exp2_experiment_id_matches_authoritative_runner_and_budget_id() -> None:
    module = _load_module()

    assert normalize_experiment_ids(["exp2"]) == ("exp2_real_ai_scalability",)
    assert module.EXP2_EXPERIMENT_ID == "exp2_real_ai_scalability"


def test_exp2_accepts_shared_baseline_identity_and_full_request_controls() -> None:
    module = _load_module()
    callback_calls: list[dict[str, Any]] = []

    def callback(**kwargs: Any) -> PaperConditionResult:
        callback_calls.append(kwargs)
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

    context = _context(
        binding=_shared_baseline_identity(),
        request_limits=dict(REQUEST_LIMITS),
        callback=callback,
    )
    exp2 = module.Experiment2ScalabilityModule()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert {condition.reasoning_profile_id for condition in conditions} == {"high"}
    exp2.run_condition(context, conditions[0], selections[0])
    assert len(callback_calls) == 1


def test_formal_json_catalog_freezes_canonical_50_hard_roots_for_each_condition() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    catalog, readiness = _formal_catalog_from_json_files()
    context = _context(catalog=catalog)

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert len(conditions) == 12
    assert len(selections) == 12
    assert module.count_exp2_root_runs(conditions, selections) == 600
    expected_ids = tuple(
        catalog["paper_suite_scale_policy"]["ordered_case_ids_by_scope"]
        ["exp2_real_ai_scalability"]["hard"]
    )
    for condition, selection in zip(conditions, selections, strict=True):
        body = selection.to_dict()
        assert body["catalog_source_kind"] == "formal_paper_catalog"
        assert body["slice_digest"].startswith("sha256:")
        assert body["split_metadata_digest"].startswith("sha256:")
        assert len(body["case_expected_ai_unit_counts"]) == 50
        assert set(body["case_expected_ai_unit_counts"].values()) == {20}
        assert body["split_profile_id"] == module.EXP2_SPLIT_PROFILE_ID
        assert selection.ordered_case_ids == expected_ids

    tampered = json.loads(json.dumps(catalog))
    tampered["factorization_cases"][0]["split_params"][
        "requested_child_count"
    ] += 1
    with pytest.raises(ValueError, match="catalog digest"):
        exp2.expand_conditions(_context(catalog=tampered))


def test_exp2_has_no_lean_conditions() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()

    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)

    assert conditions
    assert selections
    assert all(condition.domain == "factorization" for condition in conditions)
    assert all(selection.domain == "factorization" for selection in selections)


def test_exp2_exposes_no_regression_smoke_condition_or_selection_seam() -> None:
    module = _load_module()
    forbidden = {
        "build_exp2_regression_smoke_lean_bindings",
        "build_exp2_regression_smoke_factor_binding",
        "validate_exp2_regression_smoke_factor_condition",
        "validate_exp2_regression_smoke_lean_condition",
    }

    assert forbidden.isdisjoint(vars(module))


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
        assert selection.to_dict()["split_profile_id"] == module.EXP2_SPLIT_PROFILE_ID
        assert selection.expected_ai_unit_count > len(selection.ordered_case_ids)

    assert set(by_difficulty) == {"hard"}
    assert all(len(case_id_sets) == 1 for case_id_sets in by_difficulty.values())

    bad_catalog = _catalog()
    hard_case = next(
        case
        for case in bad_catalog["factorization_cases"]
        if case["paper_difficulty"] == "hard"
    )
    hard_case["split_params"]["strategy_id"] = "independent_integer_batch"
    _refresh_catalog_digest(bad_catalog)
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

    assert support == ()


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
    with pytest.raises(ValueError, match="DeepSeek-V4-Pro baseline model identity"):
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
        paper_difficulty="hard",
        worker_count=1,
        repeat_id=0,
    )
    scaled_condition, scaled_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
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
    assert scaled["provider_latency_sum_ms"] == (
        scaled_selection.expected_ai_unit_count * 9999
    )
    assert scaled["speedup"] == 2.5
    assert scaled["efficiency"] == pytest.approx(2.5 / 3)
    assert scaled["throughput_completed_roots_per_second"] == 12.5
    assert scaled["rate_limited_429_count"] == 10
    assert scaled["retry_count"] == 5
    assert scaled["rate_limit_sensitivity"] == "rate_limited"
    assert scaled["included_in_rate_limit_excluded_view"] is False
    assert scaled["task_batch_id"] == scaled_selection.selection_id


def test_summary_accepts_standard_results_with_exp2_scheduler_evidence() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context(binding=_shared_baseline_identity())
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
        worker_count=1,
        repeat_id=0,
    )
    record = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=1000,
        task_critical_path_ms=800,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    for task in record["tasks"]:
        case_id = task["task_id"]
        standard_task_id = f"paper_factorization_{case_id}"
        task["case_id"] = case_id
        task["task_id"] = standard_task_id
        task.pop("model_entry_id")
        for attempt in task["attempts"]:
            attempt["task_id"] = standard_task_id
            for field_name in (
                "transport_kind",
                "reasoning_profile_id",
                "source_provider_config_digest",
                "model_endpoint_identity_digest",
            ):
                attempt.pop(field_name, None)

    summary = exp2.summarize({"condition_evidence": [record]})

    assert len(summary.rows) == 1
    assert summary.rows[0]["transport_kind"] == "ai_api"
    assert summary.rows[0]["wall_clock_ms"] == 1000
    assert summary.rows[0]["batch_timing_status"] == "authoritative"
    assert summary.rows[0]["formal_matrix_status"] == "incomplete"


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
        paper_difficulty="hard",
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


def test_summary_keeps_normal_scripted_evidence_paper_ineligible() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
        worker_count=1,
        repeat_id=0,
    )

    evidence = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=False,
        transport_kind="scripted",
    )

    summary = exp2.summarize({"condition_evidence": [evidence]})

    assert len(summary.rows) == 1
    assert summary.rows[0]["transport_kind"] == "scripted"
    assert summary.rows[0]["paper_eligible"] is False


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
        paper_difficulty="hard",
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
        paper_difficulty="hard",
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
        paper_difficulty="hard",
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
        paper_difficulty="hard",
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


def test_summary_audits_ai_unit_worker_commitments_and_optional_preflight() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
        worker_count=30,
        repeat_id=0,
    )

    missing_unit = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    task = missing_unit["tasks"][0]
    task["ai_units"].pop()
    task["attempts"].pop()
    task["candidate_end"] = task["ai_units"][-1]["range_end"]
    task["merge_gates"][0]["dependencies"] = [
        unit["unit_id"] for unit in task["ai_units"]
    ]
    with pytest.raises(ValueError, match="AI-unit inventory"):
        exp2.summarize({"condition_evidence": [missing_unit]})

    missing_worker = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    missing_worker["tasks"][0]["attempts"][0].pop("worker_id")
    with pytest.raises(ValueError, match="worker execution evidence"):
        exp2.summarize({"condition_evidence": [missing_worker]})

    optional = replace(
        condition,
        condition_id="exp2_factorization_hard_w100_r0",
        worker_count=100,
    )
    optional_evidence = _condition_evidence(
        optional,
        selection,
        task_wall_clock_ms=100,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    with pytest.raises(ValueError, match="unsupported worker_count"):
        exp2.summarize({"condition_evidence": [optional_evidence]})


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
        paper_difficulty="hard",
        worker_count=1,
        repeat_id=1,
    )
    scaled_condition, scaled_selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
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


def test_summary_aggregates_matched_two_repeats() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []
    baseline_wall_clocks = [1000, 1400]
    scaled_wall_clocks = [500, 700]

    for repeat_id, wall_clock in enumerate(baseline_wall_clocks):
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
                task_wall_clock_ms=wall_clock,
                task_critical_path_ms=wall_clock,
            )
        )
    for repeat_id, wall_clock in enumerate(scaled_wall_clocks):
        condition, selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
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
        if row["paper_difficulty"] == "hard"
    }

    assert len(rows) == 2
    assert rows[1]["repeat_count"] == 2
    assert rows[1]["wall_clock_median_ms"] == 1200
    assert rows[1]["wall_clock_iqr_ms"] == 400
    assert rows[3]["repeat_count"] == 2
    assert rows[3]["wall_clock_median_ms"] == 600
    assert rows[3]["wall_clock_iqr_ms"] == 200
    assert rows[3]["speedup_median"] == 2.0
    assert rows[3]["speedup_iqr"] == 0.0
    assert rows[3]["task_batch_id"] == rows[1]["task_batch_id"]
    assert rows[3]["root_run_count"] == 10


def test_summary_marks_incomplete_repeat_groups_ineligible() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(1):
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

    assert row["repeat_count"] == 1
    assert row["repeat_set_status"] == "incomplete_or_duplicate"
    assert row["expected_repeat_ids"] == (0, 1)
    assert row["missing_repeat_ids"] == (1,)
    assert row["duplicate_repeat_ids"] == ()
    assert row["expected_root_run_count"] == 10
    assert row["root_run_count"] == 5
    assert row["paper_eligible"] is False


def test_summary_keeps_no_429_sensitivity_stats_for_complete_repeat_groups() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(2):
        baseline_condition, baseline_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
            worker_count=1,
            repeat_id=repeat_id,
        )
        scaled_condition, scaled_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
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
                rate_limited_429_count=1 if repeat_id == 1 else 0,
                paper_eligible=True,
                transport_kind="ai_api",
            )
        )

    summary = exp2.summarize({"condition_evidence": evidence_rows})
    rows = {
        row["worker_count"]: row
        for row in summary.rows
        if row["paper_difficulty"] == "hard"
    }

    assert rows[3]["repeat_set_status"] == "complete"
    assert rows[3]["repeat_count"] == 2
    assert rows[3]["root_run_count"] == 10
    assert rows[3]["paper_eligible"] is False
    assert rows[3]["formal_matrix_status"] == "incomplete"
    assert rows[3]["formal_matrix_group_count"] == 2
    assert rows[3]["formal_matrix_root_run_count"] == 20
    assert rows[3]["rate_limit_sensitivity"] == "rate_limited"
    assert rows[3]["included_in_rate_limit_excluded_view"] is False
    assert rows[3]["rate_limit_excluded_repeat_count"] == 1
    assert rows[3]["rate_limit_excluded_root_run_count"] == 5
    assert rows[3]["rate_limit_excluded_wall_clock_median_ms"] == 500
    assert rows[3]["rate_limit_excluded_speedup_median"] == 2.0


def test_formal_matrix_audit_accepts_exact_six_group_600_root_run_matrix() -> None:
    module = _load_module()
    rows = []
    for worker_count in module.MANDATORY_WORKER_LEVELS:
        rows.append(
            {
                "domain": "factorization",
                "catalog_version": "v2",
                "paper_difficulty": "hard",
                "worker_count": worker_count,
                "condition_ids": tuple(
                    f"exp2_factorization_hard_w{worker_count}_r{repeat_id}"
                    for repeat_id in range(2)
                ),
                "repeat_set_status": "complete",
                "root_run_count": 100,
                "paper_eligible": True,
            }
        )

    audited = module._apply_formal_matrix_audit(rows)

    assert len(audited) == 6
    assert all(row["formal_matrix_status"] == "complete" for row in audited)
    assert all(row["formal_matrix_condition_count"] == 12 for row in audited)
    assert all(row["formal_matrix_root_run_count"] == 600 for row in audited)
    assert all(row["paper_eligible"] is True for row in audited)


def test_summary_excludes_repeat_when_matched_worker_one_baseline_has_429() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    evidence_rows = []

    for repeat_id in range(2):
        baseline_condition, baseline_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
            worker_count=1,
            repeat_id=repeat_id,
        )
        scaled_condition, scaled_selection = _find_condition(
            conditions,
            selections,
            domain="factorization",
            paper_difficulty="hard",
            worker_count=3,
            repeat_id=repeat_id,
        )
        evidence_rows.append(
            _condition_evidence(
                baseline_condition,
                baseline_selection,
                task_wall_clock_ms=1000,
                task_critical_path_ms=1000,
                rate_limited_429_count=1 if repeat_id == 1 else 0,
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
        if row["paper_difficulty"] == "hard"
    }

    assert rows[1]["rate_limit_excluded_repeat_count"] == 1
    assert rows[3]["rate_limit_excluded_repeat_count"] == 1
    assert rows[3]["rate_limit_excluded_root_run_count"] == 5
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
        paper_difficulty="hard",
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


def test_critical_path_uses_batch_origin_and_attempt_timestamps() -> None:
    module = _load_module()
    exp2 = module.Experiment2ScalabilityModule()
    context = _context()
    conditions = exp2.expand_conditions(context)
    selections = exp2.freeze_case_selections(context, conditions)
    condition, selection = _find_condition(
        conditions,
        selections,
        domain="factorization",
        paper_difficulty="hard",
        worker_count=1,
        repeat_id=0,
    )
    evidence = _condition_evidence(
        condition,
        selection,
        task_wall_clock_ms=600,
        task_critical_path_ms=100,
        paper_eligible=True,
        transport_kind="ai_api",
    )
    for task in evidence["tasks"]:
        for unit, attempt in zip(task["ai_units"], task["attempts"], strict=True):
            unit["started_at_ms"] = 500
            unit["ended_at_ms"] = 600
            attempt["started_at_ms"] = 500
            attempt["ended_at_ms"] = 600
        task["merge_gates"][0]["started_at_ms"] = 600
        task["merge_gates"][0]["ended_at_ms"] = 600
    row = next(iter(exp2.summarize({"condition_evidence": [evidence]}).rows))
    assert row["critical_path_ms"] == 600

    outside_batch = json.loads(json.dumps(evidence))
    outside_batch["tasks"][0]["attempts"][0]["started_at_ms"] = 650
    outside_batch["tasks"][0]["attempts"][0]["ended_at_ms"] = 700
    with pytest.raises(ValueError, match="attempt timestamps.*batch"):
        exp2.summarize({"condition_evidence": [outside_batch]})


def _load_module():
    spec = importlib.util.find_spec(MODULE_NAME)
    assert spec is not None, "Exp2 scalability module must exist"
    return importlib.import_module(MODULE_NAME)


def _context(
    *,
    catalog: dict[str, Any] | None = None,
    binding: Any | None = None,
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
        "provider_config_id": "exp1_baseline_deepseek",
        "selected_entry_id": "deepseek_v4_pro_exp1_baseline",
        "model_entry_id": "deepseek_v4_pro_exp1_baseline",
        "provider_family": "deepseek",
        "provider_model_id": "deepseek-v4-pro",
        "reasoning_profile_id": "high",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_controls": dict(REQUEST_LIMITS),
        "request_limits": dict(REQUEST_LIMITS),
    }


def _shared_baseline_identity():
    config_path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "paper"
        / "exp1_baseline_provider_config.v3.json"
    )
    source_config = load_ai_api_config(
        json.loads(config_path.read_text(encoding="utf-8"))
    )
    return build_model_endpoint_identity(
        model_cohort_id="exp1_to_exp4_deepseek_baseline",
        model_cohort_digest="sha256:" + "8" * 64,
        cohort_member_id="exp1_deepseek_v4_pro_official_high_v2",
        provider_config_id="exp1_baseline_deepseek",
        selected_entry_id="deepseek_v4_pro_exp1_baseline",
        expected_provider_family="deepseek",
        expected_provider_model_id="deepseek-v4-pro",
        expected_reasoning_profile_id="high",
        source_config=source_config,
    )


def _formal_catalog_from_json_files() -> tuple[dict[str, Any], dict[str, Any]]:
    factorization_cases = _read_jsonl(
        Path("benchmarks/paper/factorization_catalog.v2.jsonl")
    )
    for case in factorization_cases:
        case["paper_difficulty"] = case["difficulty"]
    lean_cases = _read_jsonl(Path("benchmarks/paper/lean_catalog.v1.jsonl"))
    for case in lean_cases:
        case["paper_difficulty"] = "simple"
        case["topic_family"] = "pure_logic"
        case["topic_family_version"] = "shallow_v1"
    lean_lemma_graph_cases = _read_jsonl(
        Path("benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
    )
    readiness = json.loads(
        Path("benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(
            encoding="utf-8"
        )
    )
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v2",
        "factorization_cases": factorization_cases,
        "lean_cases": lean_cases,
        "lean_lemma_graph_cases": lean_lemma_graph_cases,
    }
    profile = load_paper_suite_scale_profile(
        Path("benchmarks/paper/paper_suite_scale_profile.v1.json")
    )
    catalog_digest = digest_json(digest_body)
    candidates_by_difficulty = {
        difficulty: tuple(
            case
            for case in factorization_cases
            if case["paper_difficulty"] == difficulty
        )
        for difficulty in ("easy", "medium", "hard")
    }
    suite_policy, _ = build_paper_suite_scale_policy(
        profile=profile,
        catalog_id=profile.catalog_id,
        catalog_version=profile.catalog_version,
        catalog_digest=catalog_digest,
        candidates_by_difficulty=candidates_by_difficulty,
    )
    return (
        {
            **digest_body,
            "suite_version": "paper_v1",
            "catalog_digest": catalog_digest,
            "paper_suite_scale_policy": suite_policy,
            "task15_budget_input": readiness["task15_budget_input"],
            "lean_task14_readiness": readiness,
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
        readiness,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _catalog() -> dict[str, Any]:
    factorization_cases = [
        {
            "schema_version": "tokenshare.paper_factorization_case.v1",
            "case_id": f"factorization_{difficulty}_{index:02d}",
            "difficulty": difficulty,
            "paper_difficulty": difficulty,
            "candidate_start": "2",
            "candidate_end": str(1 + 2 * (3 + index)),
            "split_params": {
                "strategy_id": "factorization.candidate_range_partition.v1",
                "range_policy": "contiguous",
                "requested_child_count": 3 + index,
            },
        }
        for difficulty in ("easy", "medium", "hard")
        for index in range(1, 6)
    ]
    lean_cases = [
        case
        for paper_difficulty, expected_count in (
            ("simple", 2),
            ("medium_lemma_dag", 4),
            ("hard_frontier", 6),
        )
        for topic_family in ("pure_logic", "function_set", "induction")
        for case in _lean_cases(
            paper_difficulty,
            topic_family,
            15,
            expected_count,
        )
    ]
    digest_body = {
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": factorization_cases,
        "lean_cases": [],
        "lean_lemma_graph_cases": lean_cases,
    }
    catalog_digest = digest_json(digest_body)
    selected_by_cell = {
        f"{paper_difficulty}/{topic_family}": [
            case["case_id"]
            for case in lean_cases
            if case["paper_difficulty"] == paper_difficulty
            and case["topic_family"] == topic_family
        ]
        for paper_difficulty in ("simple", "medium_lemma_dag", "hard_frontier")
        for topic_family in ("pure_logic", "function_set", "induction")
    }
    semantic_by_cell = {
        cell_key: [
            digest_json({"cell": cell_key, "index": index})
            for index in range(15)
        ]
        for cell_key in selected_by_cell
    }
    golden_by_cell = {
        cell_key: case_ids[:2] for cell_key, case_ids in selected_by_cell.items()
    }
    environment_digest = "sha256:" + "4" * 64
    oracle_package_digests = {"synthetic_oracle.v1": "sha256:" + "5" * 64}
    readiness_selection_digest = digest_json(
        {
            "schema_version": "tokenshare.lean_task14_selected_cases.v1",
            "catalog_digest": catalog_digest,
            "environment_digest": environment_digest,
            "oracle_package_digests": oracle_package_digests,
            "target_case_count": 15,
            "selected_case_ids_by_cell": selected_by_cell,
            "semantic_fingerprint_digests_by_cell": semantic_by_cell,
            "golden_case_ids_by_cell": golden_by_cell,
        }
    )
    return {
        **digest_body,
        "suite_version": "paper_v1",
        "catalog_digest": catalog_digest,
        "task15_budget_input": {
            "schema_version": "tokenshare.lean_task15_budget_input.v1",
            "target_case_count": 15,
            "selected_case_count": 135,
            "executable_cell_count": 9,
            "blocked_cell_count": 0,
            "provider_calls_made": 0,
            "catalog_digest": catalog_digest,
            "expected_ai_unit_count": sum(
                case["expected_ai_unit_count"] for case in lean_cases
            ),
            "environment_digest": environment_digest,
            "oracle_package_digests": oracle_package_digests,
            "selected_case_ids_by_cell": selected_by_cell,
            "semantic_fingerprint_digests_by_cell": semantic_by_cell,
            "golden_case_ids_by_cell": golden_by_cell,
            "selection_digest": readiness_selection_digest,
            "catalog_slice_digest": readiness_selection_digest,
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
    }


def _lean_cases(
    paper_difficulty: str,
    topic_family: str,
    count: int,
    expected_ai_unit_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": "tokenshare.paper_lean_lemma_graph_case.v2",
            "case_id": f"lean_{paper_difficulty}_{topic_family}_{index:02d}",
            "domain": "lean_proof",
            "difficulty": {
                "simple": "easy",
                "medium_lemma_dag": "medium",
                "hard_frontier": "hard",
            }[paper_difficulty],
            "paper_difficulty": paper_difficulty,
            "topic_family": topic_family,
            "expected_ai_unit_count": expected_ai_unit_count,
            "expected_split_kind": "recursive_lemma_dag",
            "dependency_edges": [],
            "environment_digest": "sha256:" + "4" * 64,
            "oracle_proof_ref": {"preflight_status": "passed"},
        }
        for index in range(1, count + 1)
    ]


def _refresh_catalog_digest(catalog: dict[str, Any]) -> None:
    catalog["catalog_digest"] = digest_json(
        {
            "catalog_id": catalog["catalog_id"],
            "catalog_version": catalog["catalog_version"],
            "factorization_cases": catalog["factorization_cases"],
            "lean_cases": catalog["lean_cases"],
            "lean_lemma_graph_cases": catalog["lean_lemma_graph_cases"],
        }
    )


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
    selection_body = selection.to_dict()
    case_unit_counts = selection_body["case_expected_ai_unit_counts"]
    worker_capacity = min(condition.worker_count, selection.expected_ai_unit_count)
    tasks = []
    global_unit_index = 0
    for index, case_id in enumerate(selection.ordered_case_ids):
        expected_unit_count = case_unit_counts[case_id]
        unit_kind = (
            "factorization_range_child"
            if condition.domain == "factorization"
            else "lean_proof_child"
        )
        ai_units = [
            {
                "unit_id": f"{case_id}_unit_{unit_index:02d}",
                "root_task_id": case_id,
                "unit_kind": unit_kind,
                "plugin_generated": True,
                "range_start": 2 + unit_index,
                "range_end": 2 + unit_index,
                "dependencies": [],
                "started_at_ms": 0,
                "ended_at_ms": task_critical_path_ms // 2,
                "provider_latency_ms": provider_latency_ms_per_task,
            }
            for unit_index in range(expected_unit_count)
        ]
        attempts = []
        task_total_tokens = 100 + index
        token_base, token_remainder = divmod(task_total_tokens, expected_unit_count)
        for unit_index, unit in enumerate(ai_units):
            worker_slot = global_unit_index % worker_capacity
            worker_id = f"worker_{condition.condition_id}_{worker_slot:03d}"
            attempt_id = f"{unit['unit_id']}_attempt_0"
            token_count = token_base + (1 if unit_index < token_remainder else 0)
            attempts.append(
                {
                "attempt_id": attempt_id,
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
                "worker_id": worker_id,
                "started_at_ms": unit["started_at_ms"],
                "ended_at_ms": unit["ended_at_ms"],
                "total_tokens": token_count,
                "cost_estimate": 0.01 / expected_unit_count,
                "paper_eligible": paper_eligible,
                }
            )
            global_unit_index += 1
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
                "total_tokens": task_total_tokens,
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
                "candidate_end": 1 + expected_unit_count,
                "ai_units": ai_units,
                "attempts": attempts,
                "merge_gates": [
                    {
                        "gate_id": f"{case_id}_merge",
                        "dependencies": [unit["unit_id"] for unit in ai_units],
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
