from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tokenshare.experiments.paper_experiment_contracts import (
    PaperConditionResult,
    PaperExecutionContext,
    PaperExperimentModule,
)
from tokenshare.experiments.paper_exp3_fault_recovery import (
    BASELINE_MODEL_ENTRY_ID,
    EXP3_EXPERIMENT_ID,
    Experiment3FaultRecoveryModule,
    build_exp3_plan_manifest,
    summarize_exp3,
    validate_exp3_condition_matrix,
)
from tokenshare.experiments.paper_models import PaperStatus
from tokenshare.experiments.paper_models import digest_json


CATALOG_DIGEST = "sha256:" + "a" * 64
ENDPOINT_DIGEST = "sha256:" + "b" * 64


def test_exp3_expands_rate_fault_and_worker_death_root_run_counts() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _context()

    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    manifest = build_exp3_plan_manifest(
        conditions,
        selections,
        catalog=context.catalog,
    )

    assert manifest["experiment_id"] == EXP3_EXPERIMENT_ID
    assert manifest["provider_calls_made"] == 0
    assert manifest["condition_count"] == 273
    assert manifest["root_run_counts"] == {
        "rate_fault_factorization": 525,
        "rate_fault_lean_proof": 180,
        "worker_death": 108,
        "total": 813,
    }
    assert manifest["rate_fault"]["fault_types"] == [
        "false_positive",
        "false_negative",
        "no_return",
        "late_submission",
        "executor_error",
    ]
    assert manifest["rate_fault"]["factorization_rates_percent"] == [
        0,
        1,
        5,
        10,
        25,
        50,
        100,
    ]
    assert manifest["rate_fault"]["lean_rates_percent"] == [0, 10, 50, 100]
    assert manifest["worker_death"]["worker_count"] == 10
    assert manifest["worker_death"]["dead_worker_count_targets"] == [1, 3]
    assert manifest["worker_death"]["kill_progress_targets_percent"] == [25, 50, 75]
    assert {condition.model_entry_id for condition in conditions} == {
        BASELINE_MODEL_ENTRY_ID
    }
    assert {condition.provider_model_id for condition in conditions} == {
        "zai-org/GLM-5.2"
    }
    assert {condition.catalog_digest for condition in conditions} == {CATALOG_DIGEST}
    assert conditions[0].condition_id == (
        "exp3_rate_fault_factorization__false_positive__r0__rep0"
    )
    assert conditions[0].seed == _expected_condition_seed(conditions[0].condition_id)


def test_exp3_freezes_task_slices_and_fault_target_manifest_without_resampling() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _context()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    manifest = build_exp3_plan_manifest(
        conditions,
        selections,
        catalog=context.catalog,
    )

    by_condition_id = {
        condition.condition_id: selection
        for condition, selection in zip(conditions, selections, strict=True)
    }
    factor_rate_digests = {
        selection.selection_digest
        for condition, selection in by_condition_id.items()
        if condition.startswith("exp3_rate_fault_factorization__")
    }
    assert len(factor_rate_digests) == 1
    assert next(iter(factor_rate_digests)).startswith("sha256:")

    lean_rate_selections = {
        condition_id: selection
        for condition_id, selection in by_condition_id.items()
        if condition_id.startswith("exp3_rate_fault_lean__all_topics__")
    }
    assert len(lean_rate_selections) == 60
    assert {selection.selection_digest for selection in lean_rate_selections.values()} == {
        next(iter(lean_rate_selections.values())).selection_digest
    }
    assert {selection.ordered_case_ids for selection in lean_rate_selections.values()} == {
        (
            "lean_rate_pure_logic",
            "lean_rate_function_set",
            "lean_rate_induction",
        )
    }

    factor_100_rows = [
        row
        for row in manifest["fault_target_manifest"]
        if row["matrix_kind"] == "rate_fault"
        and row["domain"] == "factorization"
        and row["fault_rate_percent"] == 100
    ]
    assert factor_100_rows
    assert {
        tuple(row["ordered_case_ids"])
        for row in factor_100_rows
    } == {tuple(_catalog()["exp3_rate_fault_factorization_case_ids"])}
    assert all(
        set(row["selected_target_ai_unit_ids"])
        == {
            unit_id
            for case_id in _catalog()["exp3_rate_fault_factorization_case_ids"]
            for unit_id in _catalog()["ai_units_by_case_id"][case_id]
        }
        for row in factor_100_rows
    )
    lean_10_rows = [
        row
        for row in manifest["fault_target_manifest"]
        if row["matrix_kind"] == "rate_fault"
        and row["domain"] == "lean_proof"
        and row["fault_rate_percent"] == 10
    ]
    assert lean_10_rows
    assert {tuple(row["topic_family_slice"]) for row in lean_10_rows} == {
        ("pure_logic", "function_set", "induction")
    }
    assert {
        tuple(row["ordered_case_ids"])
        for row in lean_10_rows
    } == {
        (
            "lean_rate_pure_logic",
            "lean_rate_function_set",
            "lean_rate_induction",
        )
    }
    assert all(len(row["selected_target_ai_unit_ids"]) == 1 for row in lean_10_rows)


def test_exp3_validation_rejects_model_failover_and_slice_drift() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _context()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)

    drifted_model = replace(conditions[0], model_entry_id="qwen_failover")
    with pytest.raises(ValueError, match="model failover"):
        validate_exp3_condition_matrix(
            (drifted_model,) + conditions[1:],
            selections,
            catalog=context.catalog,
        )

    drifted_selection = replace(
        selections[0],
        ordered_case_ids=("wrong_factor_case",),
        expected_ai_unit_count=1,
    )
    with pytest.raises(ValueError, match="slice drift"):
        validate_exp3_condition_matrix(
            conditions,
            (drifted_selection,) + selections[1:],
            catalog=context.catalog,
        )

    stale_catalog_digest = replace(
        conditions[0],
        catalog_digest="sha256:" + "c" * 64,
    )
    with pytest.raises(ValueError, match="catalog digest drift"):
        validate_exp3_condition_matrix(
            (stale_catalog_digest,) + conditions[1:],
            selections,
            catalog=context.catalog,
        )

    drifted_fields = replace(
        conditions[0],
        fault_rate=0.99,
        ablation_mode="NO_VERIFICATION",
        real_transport_required=False,
        paper_eligible_required=False,
    )
    with pytest.raises(ValueError, match="condition field drift"):
        validate_exp3_condition_matrix(
            (drifted_fields,) + conditions[1:],
            selections,
            catalog=context.catalog,
        )

    with pytest.raises(ValueError, match="condition matrix drift"):
        validate_exp3_condition_matrix(
            conditions[:-1],
            selections[:-1],
            catalog=context.catalog,
        )

    with pytest.raises(ValueError, match="duplicate condition"):
        validate_exp3_condition_matrix(
            conditions + (conditions[-1],),
            selections + (selections[-1],),
            catalog=context.catalog,
        )

    blocked_misuse = replace(
        selections[0],
        ordered_case_ids=(),
        expected_ai_unit_count=0,
        blocked_reason="missing_exp3_catalog_slice",
    )
    with pytest.raises(ValueError, match="blocked selection conflicts"):
        validate_exp3_condition_matrix(
            conditions,
            (blocked_misuse,) + selections[1:],
            catalog=context.catalog,
        )

    stale_selection_digest = replace(
        selections[0],
        catalog_digest="sha256:" + "d" * 64,
    )
    with pytest.raises(ValueError, match="selection catalog digest drift"):
        validate_exp3_condition_matrix(
            conditions,
            (stale_selection_digest,) + selections[1:],
            catalog=context.catalog,
        )

    malformed_catalog = dict(_catalog())
    malformed_catalog["exp3_rate_fault_lean_case_ids_by_topic"] = {
        **malformed_catalog["exp3_rate_fault_lean_case_ids_by_topic"],
        "pure_logic": ("lean_rate_pure_logic", "lean_rate_pure_logic_extra"),
    }
    malformed_catalog["ai_units_by_case_id"] = {
        **malformed_catalog["ai_units_by_case_id"],
        "lean_rate_pure_logic_extra": ("lean_rate_pure_logic_extra_unit_0",),
    }
    with pytest.raises(ValueError, match="Lean rate-fault slice"):
        module.freeze_case_selections(_context(catalog=malformed_catalog), conditions)


def test_exp3_missing_catalog_slice_becomes_structured_blocked_selection() -> None:
    catalog = dict(_catalog())
    catalog.pop("exp3_rate_fault_lean_case_ids_by_topic")
    context = _context(catalog=catalog)
    module = Experiment3FaultRecoveryModule()

    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)

    blocked = [selection for selection in selections if selection.is_blocked]
    assert blocked
    assert {selection.blocked_reason for selection in blocked} == {
        "missing_exp3_catalog_slice"
    }
    assert all(selection.expected_ai_unit_count == 0 for selection in blocked)


def test_exp3_summary_computes_matched_baseline_overhead_and_zero_denominator() -> None:
    rows = summarize_exp3(
        {
            "rate_fault_runs": [
                _rate_fault_run(
                    condition_id="fault_positive",
                    matched_baseline_condition_id="baseline_positive",
                    wall_clock_ms=120,
                    baseline_wall_clock_ms=100,
                    total_tokens=45,
                    baseline_total_tokens=30,
                    cost_estimate=0.75,
                    baseline_cost_estimate=0.5,
                ),
                _rate_fault_run(
                    condition_id="fault_zero_baseline",
                    matched_baseline_condition_id="baseline_zero",
                    wall_clock_ms=120,
                    baseline_wall_clock_ms=0,
                    total_tokens=45,
                    baseline_total_tokens=0,
                    cost_estimate=0.75,
                    baseline_cost_estimate=0.0,
                ),
            ],
            "worker_death_runs": [],
        }
    ).rows

    positive = rows[0]
    zero = rows[1]
    assert positive["matched_baseline_condition_id"] == "baseline_positive"
    assert positive["wall_clock_overhead_ms"] == 20
    assert positive["wall_clock_overhead_ratio"] == pytest.approx(0.2)
    assert positive["token_overhead"] == 15
    assert positive["token_overhead_ratio"] == pytest.approx(0.5)
    assert positive["cost_overhead"] == pytest.approx(0.25)
    assert positive["cost_overhead_ratio"] == pytest.approx(0.5)
    assert positive["original_output_refs"] == [
        {"artifact_id": f"original_{index}"}
        for index in range(4)
    ]
    assert positive["mutated_output_refs"] == [
        {"artifact_id": f"mutated_{index}"}
        for index in range(4)
    ]
    assert positive["provider_tokens_attributed_by_mutation"] == 0
    assert positive["injected_fault_count"] == 4
    assert positive["detection_rate"] == pytest.approx(0.75)
    assert positive["false_accept_rate"] == pytest.approx(0.25)
    assert positive["recoverable_fault_target_count"] == 2
    assert positive["recovery_rate"] == pytest.approx(1.0)

    assert zero["wall_clock_overhead_ratio"] is None
    assert zero["wall_clock_overhead_applicability"] == "zero_baseline_denominator"
    assert zero["token_overhead_ratio"] is None
    assert zero["token_overhead_applicability"] == "zero_baseline_denominator"
    assert zero["cost_overhead_ratio"] is None
    assert zero["cost_overhead_applicability"] == "zero_baseline_denominator"

    zero_fault = summarize_exp3(
        {
            "rate_fault_runs": [
                _rate_fault_run(
                    condition_id="fault_zero_injected",
                    matched_baseline_condition_id="baseline_zero_injected",
                    injected_fault_count=0,
                    detected_fault_count=0,
                    false_accept_count=0,
                    recoverable_fault_target_count=0,
                    recovered_fault_target_count=0,
                ),
            ],
            "worker_death_runs": [],
        }
    ).rows[0]
    assert zero_fault["detection_rate"] is None
    assert zero_fault["detection_applicability"] == "zero_fault_denominator"
    assert zero_fault["false_accept_rate"] is None
    assert zero_fault["false_accept_applicability"] == "zero_fault_denominator"
    assert zero_fault["recovery_rate"] is None
    assert zero_fault["recovery_applicability"] == "zero_recoverable_denominator"

    worker_death = summarize_exp3(
        {
            "rate_fault_runs": [],
            "worker_death_runs": [
                _worker_death_run(
                    condition_id="worker_death_overhead",
                    target_dead_worker_count=1,
                    actual_dead_worker_count=1,
                    required_slot_count=2,
                    recovered_slot_count=1,
                    matched_baseline_condition_id="worker_baseline",
                    baseline_wall_clock_ms=160,
                    baseline_total_tokens=40,
                    baseline_cost_estimate=0.6,
                )
            ],
        }
    ).rows[0]
    assert worker_death["matched_baseline_condition_id"] == "worker_baseline"
    assert worker_death["wall_clock_overhead_ms"] == 40
    assert worker_death["wall_clock_overhead_ratio"] == pytest.approx(0.25)
    assert worker_death["token_overhead"] == 21
    assert worker_death["token_overhead_ratio"] == pytest.approx(0.525)
    assert worker_death["cost_overhead"] == pytest.approx(0.3)
    assert worker_death["cost_overhead_ratio"] == pytest.approx(0.5)


def test_exp3_summary_rejects_fault_timing_before_raw_persistence_and_model_failover() -> None:
    bad_timing = _rate_fault_run(
        condition_id="fault_bad_timing",
        matched_baseline_condition_id="baseline_bad_timing",
    )
    bad_timing["fault_records"][0]["injection_point"] = (
        "before_raw_output_persistence"
    )
    with pytest.raises(ValueError, match="raw persistence"):
        summarize_exp3({"rate_fault_runs": [bad_timing], "worker_death_runs": []})

    failover = _rate_fault_run(
        condition_id="fault_failover",
        matched_baseline_condition_id="baseline_failover",
    )
    failover["attempts"][0]["entry_id"] = "other_model_entry"
    with pytest.raises(ValueError, match="model failover"):
        summarize_exp3({"rate_fault_runs": [failover], "worker_death_runs": []})

    baseline_mismatch = _rate_fault_run(
        condition_id="fault_baseline_mismatch",
        matched_baseline_condition_id="baseline_wrong",
    )
    baseline_mismatch["expected_baseline_condition_id"] = "baseline_expected"
    with pytest.raises(ValueError, match="baseline mismatch"):
        summarize_exp3(
            {"rate_fault_runs": [baseline_mismatch], "worker_death_runs": []}
        )

    missing_expected = _rate_fault_run(
        condition_id="fault_missing_expected_baseline",
        matched_baseline_condition_id="baseline_missing_expected",
    )
    missing_expected.pop("expected_baseline_condition_id")
    with pytest.raises(ValueError, match="expected_baseline_condition_id"):
        summarize_exp3(
            {
                "rate_fault_runs": [missing_expected],
                "worker_death_runs": [],
            }
        )

    provider_drift = _rate_fault_run(
        condition_id="fault_provider_drift",
        matched_baseline_condition_id="baseline_provider_drift",
    )
    provider_drift["attempts"][0]["provider"] = "openai"
    with pytest.raises(ValueError, match="model failover"):
        summarize_exp3(
            {"rate_fault_runs": [provider_drift], "worker_death_runs": []}
        )

    no_attempts = _rate_fault_run(
        condition_id="fault_no_attempts",
        matched_baseline_condition_id="baseline_no_attempts",
    )
    no_attempts["attempts"] = []
    with pytest.raises(ValueError, match="attempt evidence"):
        summarize_exp3({"rate_fault_runs": [no_attempts], "worker_death_runs": []})

    bad_fault_rate = _rate_fault_run(
        condition_id="fault_bad_rate",
        matched_baseline_condition_id="baseline_bad_rate",
    )
    bad_fault_rate["fault_rate_percent"] = 33
    with pytest.raises(ValueError, match="frozen rate"):
        summarize_exp3(
            {"rate_fault_runs": [bad_fault_rate], "worker_death_runs": []}
        )

    missing_identity = _rate_fault_run(
        condition_id="fault_missing_identity",
        matched_baseline_condition_id="baseline_missing_identity",
    )
    missing_identity["attempts"] = [{"entry_id": BASELINE_MODEL_ENTRY_ID}]
    with pytest.raises(ValueError, match="model failover"):
        summarize_exp3(
            {"rate_fault_runs": [missing_identity], "worker_death_runs": []}
        )

    missing_fault_records = _rate_fault_run(
        condition_id="fault_missing_records",
        matched_baseline_condition_id="baseline_missing_records",
    )
    missing_fault_records["fault_records"] = []
    with pytest.raises(ValueError, match="fault record evidence"):
        summarize_exp3(
            {"rate_fault_runs": [missing_fault_records], "worker_death_runs": []}
        )

    partial_fault_records = _rate_fault_run(
        condition_id="fault_partial_records",
        matched_baseline_condition_id="baseline_partial_records",
    )
    partial_fault_records["fault_records"] = partial_fault_records[
        "fault_records"
    ][:1]
    with pytest.raises(ValueError, match="fault record count"):
        summarize_exp3(
            {"rate_fault_runs": [partial_fault_records], "worker_death_runs": []}
        )

    missing_provenance = _rate_fault_run(
        condition_id="fault_missing_provenance",
        matched_baseline_condition_id="baseline_missing_provenance",
    )
    missing_provenance["fault_records"][0].pop("original_provenance_ref")
    with pytest.raises(ValueError, match="provenance"):
        summarize_exp3(
            {"rate_fault_runs": [missing_provenance], "worker_death_runs": []}
        )

    malformed_artifact_ref = _rate_fault_run(
        condition_id="fault_malformed_artifact_ref",
        matched_baseline_condition_id="baseline_malformed_artifact_ref",
        injected_fault_count=1,
        detected_fault_count=1,
        false_accept_count=0,
        recoverable_fault_target_count=1,
        recovered_fault_target_count=1,
    )
    malformed_artifact_ref["original_output_refs"] = [{}]
    malformed_artifact_ref["fault_records"][0]["original_output_ref"] = {}
    with pytest.raises(ValueError, match="artifact ref"):
        summarize_exp3(
            {
                "rate_fault_runs": [malformed_artifact_ref],
                "worker_death_runs": [],
            }
        )

    malformed_provenance_ref = _rate_fault_run(
        condition_id="fault_malformed_provenance_ref",
        matched_baseline_condition_id="baseline_malformed_provenance_ref",
        injected_fault_count=1,
        detected_fault_count=1,
        false_accept_count=0,
        recoverable_fault_target_count=1,
        recovered_fault_target_count=1,
    )
    malformed_provenance_ref["fault_records"][0]["original_provenance_ref"] = {}
    with pytest.raises(ValueError, match="artifact ref"):
        summarize_exp3(
            {
                "rate_fault_runs": [malformed_provenance_ref],
                "worker_death_runs": [],
            }
        )

    zero_fault_with_records = _rate_fault_run(
        condition_id="fault_zero_with_records",
        matched_baseline_condition_id="baseline_zero_with_records",
        injected_fault_count=0,
        detected_fault_count=0,
        false_accept_count=0,
        recoverable_fault_target_count=0,
        recovered_fault_target_count=0,
    )
    zero_fault_with_records["original_output_refs"] = [{"artifact_id": "original_0"}]
    zero_fault_with_records["mutated_output_refs"] = [{"artifact_id": "mutated_0"}]
    zero_fault_with_records["fault_records"] = [
        _fault_record(
            index=0,
            original_output_ref={"artifact_id": "original_0"},
            mutated_output_ref={"artifact_id": "mutated_0"},
        )
    ]
    with pytest.raises(ValueError, match="zero injected faults"):
        summarize_exp3(
            {"rate_fault_runs": [zero_fault_with_records], "worker_death_runs": []}
        )

    bad_completion = _rate_fault_run(
        condition_id="fault_bad_completion",
        matched_baseline_condition_id="baseline_bad_completion",
    )
    bad_completion["completed_root_count"] = 6
    with pytest.raises(ValueError, match="completed_root_count"):
        summarize_exp3(
            {"rate_fault_runs": [bad_completion], "worker_death_runs": []}
        )

    unknown_domain = _rate_fault_run(
        condition_id="fault_unknown_domain",
        matched_baseline_condition_id="baseline_unknown_domain",
    )
    unknown_domain["domain"] = "structured_report"
    with pytest.raises(ValueError, match="domain"):
        summarize_exp3(
            {"rate_fault_runs": [unknown_domain], "worker_death_runs": []}
        )


def test_exp3_worker_death_summary_keeps_actual_dead_count_mismatch_failed() -> None:
    rows = summarize_exp3(
        {
            "rate_fault_runs": [],
            "worker_death_runs": [
                _worker_death_run(
                    condition_id="worker_death_good",
                    target_dead_worker_count=3,
                    actual_dead_worker_count=3,
                    required_slot_count=4,
                    recovered_slot_count=3,
                ),
                _worker_death_run(
                    condition_id="worker_death_mismatch",
                    target_dead_worker_count=3,
                    actual_dead_worker_count=2,
                    required_slot_count=4,
                    recovered_slot_count=3,
                ),
            ],
        }
    ).rows

    good = rows[0]
    mismatch = rows[1]
    assert good["summary_kind"] == "worker_death"
    assert good["target_dead_worker_count"] == 3
    assert good["actual_dead_worker_count"] == 3
    assert good["target_kill_progress_percent"] == 50
    assert good["actual_kill_progress_percent"] == 50
    assert good["coordinator_continued"] is True
    assert good["required_slot_count"] == 4
    assert good["recovered_slot_count"] == 3
    assert good["result_completeness_rate"] == pytest.approx(0.75)
    assert good["root_output_complete"] is True
    assert good["accepted_validity"] is True

    assert mismatch["condition_included"] is False
    assert mismatch["run_status"] == "failed"
    assert mismatch["failure_reason"] == "actual_dead_count_mismatch"

    coordinator_failed = summarize_exp3(
        {
            "rate_fault_runs": [],
            "worker_death_runs": [
                {
                    **_worker_death_run(
                        condition_id="worker_death_coordinator_failed",
                        target_dead_worker_count=1,
                        actual_dead_worker_count=1,
                        required_slot_count=1,
                        recovered_slot_count=1,
                    ),
                    "coordinator_continued": False,
                }
            ],
        }
    ).rows[0]
    assert coordinator_failed["condition_included"] is False
    assert coordinator_failed["run_status"] == "failed"
    assert coordinator_failed["failure_reason"] == "coordinator_not_continued"

    missing_records = _worker_death_run(
        condition_id="worker_death_missing_records",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    missing_records["worker_death_records"] = []
    missing_records["worker_death_record_refs"] = []
    with pytest.raises(ValueError, match="worker death record evidence"):
        summarize_exp3(
            {"rate_fault_runs": [], "worker_death_runs": [missing_records]}
        )

    bad_record_count = _worker_death_run(
        condition_id="worker_death_bad_record_count",
        target_dead_worker_count=3,
        actual_dead_worker_count=2,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    bad_record_count["worker_death_records"] = bad_record_count[
        "worker_death_records"
    ][:1]
    with pytest.raises(ValueError, match="worker death record count"):
        summarize_exp3(
            {"rate_fault_runs": [], "worker_death_runs": [bad_record_count]}
        )

    bad_process_evidence = _worker_death_run(
        condition_id="worker_death_bad_process",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    bad_process_evidence["worker_death_records"][0]["worker_process_exitcode"] = 0
    with pytest.raises(ValueError, match="worker death record"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [bad_process_evidence],
            }
        )

    duplicate_records = _worker_death_run(
        condition_id="worker_death_duplicate_records",
        target_dead_worker_count=3,
        actual_dead_worker_count=3,
        required_slot_count=3,
        recovered_slot_count=3,
    )
    duplicate_records["worker_death_records"] = [
        duplicate_records["worker_death_records"][0],
        duplicate_records["worker_death_records"][0],
        duplicate_records["worker_death_records"][0],
    ]
    duplicate_records["worker_death_record_refs"] = _worker_death_record_refs(
        condition_id="worker_death_duplicate_records",
        records=duplicate_records["worker_death_records"],
    )
    with pytest.raises(ValueError, match="distinct worker death records"):
        summarize_exp3(
            {"rate_fault_runs": [], "worker_death_runs": [duplicate_records]}
        )

    duplicate_refs = _worker_death_run(
        condition_id="worker_death_duplicate_refs",
        target_dead_worker_count=3,
        actual_dead_worker_count=3,
        required_slot_count=3,
        recovered_slot_count=3,
    )
    duplicate_refs["worker_death_record_refs"] = [
        duplicate_refs["worker_death_record_refs"][0],
        duplicate_refs["worker_death_record_refs"][0],
        duplicate_refs["worker_death_record_refs"][0],
    ]
    with pytest.raises(ValueError, match="worker death record refs must be unique"):
        summarize_exp3(
            {"rate_fault_runs": [], "worker_death_runs": [duplicate_refs]}
        )

    mismatched_record_ref = _worker_death_run(
        condition_id="worker_death_mismatched_ref",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    mismatched_record_ref["worker_death_record_refs"][0]["record_digest"] = (
        "sha256:" + "f" * 64
    )
    with pytest.raises(ValueError, match="worker death record ref"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [mismatched_record_ref],
            }
        )

    swapped_record_refs = _worker_death_run(
        condition_id="worker_death_swapped_refs",
        target_dead_worker_count=3,
        actual_dead_worker_count=3,
        required_slot_count=3,
        recovered_slot_count=3,
    )
    swapped_record_refs["worker_death_record_refs"][0]["record_digest"], (
        swapped_record_refs["worker_death_record_refs"][1]["record_digest"]
    ) = (
        swapped_record_refs["worker_death_record_refs"][1]["record_digest"],
        swapped_record_refs["worker_death_record_refs"][0]["record_digest"],
    )
    with pytest.raises(ValueError, match="worker death record ref"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [swapped_record_refs],
            }
        )

    progress_drift = _worker_death_run(
        condition_id="worker_death_progress_drift",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    progress_drift["worker_death_records"][0]["progress_before_kill"] = 75
    progress_drift["worker_death_record_refs"] = _worker_death_record_refs(
        condition_id="worker_death_progress_drift",
        records=progress_drift["worker_death_records"],
    )
    with pytest.raises(ValueError, match="actual kill progress"):
        summarize_exp3(
            {"rate_fault_runs": [], "worker_death_runs": [progress_drift]}
        )

    nested_process_drift = _worker_death_run(
        condition_id="worker_death_nested_process_drift",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    nested_process_drift["worker_death_records"][0]["dead_attempt"][
        "process_exitcode"
    ] = 0
    nested_process_drift["worker_death_record_refs"] = _worker_death_record_refs(
        condition_id="worker_death_nested_process_drift",
        records=nested_process_drift["worker_death_records"],
    )
    with pytest.raises(ValueError, match="worker death record"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [nested_process_drift],
            }
        )

    reassignment_drift = _worker_death_run(
        condition_id="worker_death_reassignment_drift",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    reassignment_drift["worker_death_records"][0]["reassignment"][
        "from_worker_id"
    ] = "other_worker"
    reassignment_drift["worker_death_record_refs"] = _worker_death_record_refs(
        condition_id="worker_death_reassignment_drift",
        records=reassignment_drift["worker_death_records"],
    )
    with pytest.raises(ValueError, match="worker death record"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [reassignment_drift],
            }
        )

    worker_baseline_mismatch = _worker_death_run(
        condition_id="worker_death_baseline_mismatch",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
        matched_baseline_condition_id="worker_baseline_wrong",
        expected_baseline_condition_id="worker_baseline_expected",
    )
    with pytest.raises(ValueError, match="baseline mismatch"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [worker_baseline_mismatch],
            }
        )

    worker_missing_expected = _worker_death_run(
        condition_id="worker_death_missing_expected_baseline",
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    worker_missing_expected.pop("expected_baseline_condition_id")
    with pytest.raises(ValueError, match="expected_baseline_condition_id"):
        summarize_exp3(
            {
                "rate_fault_runs": [],
                "worker_death_runs": [worker_missing_expected],
            }
        )


def test_exp3_module_protocol_run_condition_and_scripted_eligibility_guard() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _context()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)

    assert isinstance(module, PaperExperimentModule)
    result = module.run_condition(context, conditions[0], selections[0])
    assert result.condition_id == conditions[0].condition_id
    assert result.task_count == len(selections[0].ordered_case_ids)

    scripted = _rate_fault_run(
        condition_id="fault_scripted_eligible",
        matched_baseline_condition_id="baseline_scripted",
    )
    scripted["paper_eligible"] = True
    with pytest.raises(ValueError, match="scripted transport cannot be paper eligible"):
        module.summarize({"rate_fault_runs": [scripted], "worker_death_runs": []})


def _context(catalog: dict[str, Any] | None = None) -> PaperExecutionContext:
    def callback(*, condition, selection) -> PaperConditionResult:
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.BLOCKED if selection.is_blocked else PaperStatus.PLANNED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=1 if selection.is_blocked else 0,
            provider_attempt_count=0,
            metrics_ref=None,
        )

    return PaperExecutionContext(
        context_id="exp3_test_context",
        catalog=catalog or _catalog(),
        approved_endpoint_binding={"model_endpoint_identity_digest": ENDPOINT_DIGEST},
        request_limits={"max_tokens": 1024},
        hard_limits={"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp3_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback,
    )


def _expected_condition_seed(condition_id: str) -> int:
    digest = digest_json(
        {
            "schema_version": "tokenshare.paper_exp3_condition_seed.v1",
            "condition_id": condition_id,
        }
    )
    return 330000 + int(digest.removeprefix("sha256:")[:8], 16) % 100000


def _catalog() -> dict[str, Any]:
    factor_rate_cases = tuple(f"factor_rate_{index}" for index in range(5))
    catalog = {
        "catalog_digest": CATALOG_DIGEST,
        "catalog_version": "v1",
        "suite_version": "paper_v1",
        "exp3_rate_fault_factorization_case_ids": factor_rate_cases,
        "exp3_rate_fault_lean_case_ids_by_topic": {
            "pure_logic": ("lean_rate_pure_logic",),
            "function_set": ("lean_rate_function_set",),
            "induction": ("lean_rate_induction",),
        },
        "exp3_worker_death_factorization_case_ids_by_difficulty": {
            "easy": ("factor_death_easy",),
            "medium": ("factor_death_medium",),
            "hard": ("factor_death_hard",),
        },
        "exp3_worker_death_lean_case_ids_by_topic": {
            "pure_logic": ("lean_death_pure_logic",),
            "function_set": ("lean_death_function_set",),
            "induction": ("lean_death_induction",),
        },
        "ai_units_by_case_id": {},
    }
    case_ids = list(factor_rate_cases)
    for values in catalog["exp3_rate_fault_lean_case_ids_by_topic"].values():
        case_ids.extend(values)
    for values in catalog[
        "exp3_worker_death_factorization_case_ids_by_difficulty"
    ].values():
        case_ids.extend(values)
    for values in catalog["exp3_worker_death_lean_case_ids_by_topic"].values():
        case_ids.extend(values)
    catalog["ai_units_by_case_id"] = {
        case_id: (f"{case_id}_unit_0", f"{case_id}_unit_1")
        for case_id in case_ids
    }
    return catalog


def _rate_fault_run(
    *,
    condition_id: str,
    matched_baseline_condition_id: str,
    wall_clock_ms: int = 120,
    baseline_wall_clock_ms: int = 100,
    total_tokens: int = 45,
    baseline_total_tokens: int = 30,
    cost_estimate: float = 0.75,
    baseline_cost_estimate: float = 0.5,
    injected_fault_count: int = 4,
    detected_fault_count: int = 3,
    false_accept_count: int = 1,
    recoverable_fault_target_count: int = 2,
    recovered_fault_target_count: int = 2,
    expected_baseline_condition_id: str | None = None,
) -> dict[str, Any]:
    original_refs = [
        {"artifact_id": f"original_{index}"}
        for index in range(injected_fault_count)
    ]
    mutated_refs = [
        {"artifact_id": f"mutated_{index}"}
        for index in range(injected_fault_count)
    ]
    return {
        "condition_id": condition_id,
        "domain": "factorization",
        "fault_type": "false_positive",
        "fault_rate_percent": 50,
        "repeat_id": 0,
        "task_count": 5,
        "completed_root_count": 4,
        "injected_fault_count": injected_fault_count,
        "detected_fault_count": detected_fault_count,
        "false_accept_count": false_accept_count,
        "recoverable_fault_target_count": recoverable_fault_target_count,
        "recovered_fault_target_count": recovered_fault_target_count,
        "recovered_root_count": recovered_fault_target_count,
        "recovery_latency_ms": 17,
        "retry_count": 2,
        "reassignment_count": 1,
        "wasted_actual_tokens": 7,
        "wall_clock_ms": wall_clock_ms,
        "total_tokens": total_tokens,
        "cost_estimate": cost_estimate,
        "matched_baseline_condition_id": matched_baseline_condition_id,
        "expected_baseline_condition_id": (
            expected_baseline_condition_id or matched_baseline_condition_id
        ),
        "baseline_wall_clock_ms": baseline_wall_clock_ms,
        "baseline_total_tokens": baseline_total_tokens,
        "baseline_cost_estimate": baseline_cost_estimate,
        "original_output_refs": original_refs,
        "mutated_output_refs": mutated_refs,
        "fault_records": [
            _fault_record(
                index=index,
                original_output_ref=original_refs[index],
                mutated_output_ref=mutated_refs[index],
            )
            for index in range(injected_fault_count)
        ],
        "attempts": [
            {
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "temperature_0_thinking_false",
            }
        ],
        "transport_kind": "scripted",
        "paper_eligible": False,
    }


def _fault_record(
    *,
    index: int,
    original_output_ref: dict[str, str],
    mutated_output_ref: dict[str, str],
) -> dict[str, Any]:
    return {
        "injection_point": "after_raw_output_before_parser_bridge",
        "original_raw_output_ref": {"artifact_id": f"raw_{index}"},
        "original_provenance_ref": {"artifact_id": f"provenance_{index}"},
        "original_output_ref": original_output_ref,
        "mutated_output_ref": mutated_output_ref,
        "mutated_provenance_ref": {"artifact_id": f"mutated_provenance_{index}"},
        "provider_tokens_attributed": 0,
    }


def _worker_death_run(
    *,
    condition_id: str,
    target_dead_worker_count: int,
    actual_dead_worker_count: int,
    required_slot_count: int,
    recovered_slot_count: int,
    matched_baseline_condition_id: str = "worker_death_baseline",
    baseline_wall_clock_ms: int = 100,
    baseline_total_tokens: int = 50,
    baseline_cost_estimate: float = 0.7,
    expected_baseline_condition_id: str | None = None,
) -> dict[str, Any]:
    records = [
        _worker_death_record(
            condition_id=condition_id,
            index=index,
            target_progress=50,
        )
        for index in range(actual_dead_worker_count)
    ]
    return {
        "condition_id": condition_id,
        "domain": "lean_proof",
        "repeat_id": 0,
        "task_count": 1,
        "target_dead_worker_count": target_dead_worker_count,
        "actual_dead_worker_count": actual_dead_worker_count,
        "target_kill_progress_percent": 50,
        "actual_kill_progress_percent": 50,
        "coordinator_continued": True,
        "required_slot_count": required_slot_count,
        "recovered_slot_count": recovered_slot_count,
        "root_output_complete": True,
        "accepted_validity": True,
        "recovery_latency_ms": 30,
        "retry_count": 1,
        "reassignment_count": 1,
        "wall_clock_ms": 200,
        "total_tokens": 61,
        "cost_estimate": 0.9,
        "matched_baseline_condition_id": matched_baseline_condition_id,
        "expected_baseline_condition_id": (
            expected_baseline_condition_id or matched_baseline_condition_id
        ),
        "baseline_wall_clock_ms": baseline_wall_clock_ms,
        "baseline_total_tokens": baseline_total_tokens,
        "baseline_cost_estimate": baseline_cost_estimate,
        "worker_death_records": records,
        "worker_death_record_refs": _worker_death_record_refs(
            condition_id=condition_id,
            records=records,
        ),
        "attempts": [
            {
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "temperature_0_thinking_false",
            }
        ],
        "transport_kind": "scripted",
        "paper_eligible": False,
    }


def _worker_death_record(
    *,
    condition_id: str,
    index: int,
    target_progress: int,
) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.paper_worker_death.v1",
        "condition_id": condition_id,
        "repeat_id": 0,
        "run_id": f"run_{condition_id}",
        "task_id": f"task_{index}",
        "target_ai_unit": {
            "schema_version": "tokenshare.paper_ai_unit.v1",
            "task_id": f"task_{index}",
            "unit_id": f"unit_{index}",
            "unit_kind": "generic_ai_unit",
            "dependencies": [],
            "depth": 0,
            "domain": "lean_proof",
            "metadata": {},
        },
        "dependency_graph": {
            "schema_version": "tokenshare.paper_ai_dependency_graph.v1",
            "task_id": f"task_{index}",
            "expected_ai_unit_count": 1,
            "unit_ids": [f"unit_{index}"],
            "graph_digest": "sha256:" + f"{index:064x}"[-64:],
        },
        "worker_id": f"worker_{index}_initial",
        "worker_pid": 1000 + index,
        "worker_process_exitcode": -15,
        "kill_point": f"progress_{target_progress}",
        "progress_before_kill": target_progress,
        "worker_started_at": "2026-07-15T00:00:00Z",
        "killed_at": "2026-07-15T00:00:01Z",
        "initial_lease": {"lease_id": f"lease_{index}_initial"},
        "lease_expiry": {
            "trigger": "lease_expired",
            "lease_id": f"lease_{index}_initial",
            "attempt_id": f"attempt_{index}_initial",
        },
        "dead_attempt": {
            "role": "killed_worker",
            "attempt_id": f"attempt_{index}_initial",
            "unit_id": f"unit_{index}",
            "worker_id": f"worker_{index}_initial",
            "worker_pid": 1000 + index,
            "process_exitcode": -15,
        },
        "replacement_worker_id": f"worker_{index}_replacement",
        "replacement_worker_pid": 2000 + index,
        "replacement_process_exitcode": 0,
        "replacement_attempt": {
            "role": "replacement_worker",
            "attempt_id": f"attempt_{index}_replacement",
            "unit_id": f"unit_{index}",
            "worker_id": f"worker_{index}_replacement",
            "worker_pid": 2000 + index,
            "lease_id": f"lease_{index}_replacement",
            "process_exitcode": 0,
        },
        "reassignment": {
            "from_worker_id": f"worker_{index}_initial",
            "to_worker_id": f"worker_{index}_replacement",
            "target_unit_id": f"unit_{index}",
            "original_attempt_id": f"attempt_{index}_initial",
            "replacement_attempt_id": f"attempt_{index}_replacement",
            "replacement_lease_id": f"lease_{index}_replacement",
        },
        "coordinator": {
            "pid": 999,
            "survived": True,
            "waited_for_lease_expiry": True,
        },
        "canonical_pollution": False,
        "provider_tokens_attributed": 0,
        "created_at": "2026-07-15T00:00:02Z",
    }


def _worker_death_record_refs(
    *,
    condition_id: str,
    records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "artifact_id": f"worker_death_{condition_id}_{index}",
            "record_digest": digest_json(record),
        }
        for index, record in enumerate(records)
    ]
