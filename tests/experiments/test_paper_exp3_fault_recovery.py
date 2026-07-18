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
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperStatus,
)
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_workers import (
    PaperAIUnit,
    WorkerDeathKillPoint,
    run_worker_death_harness,
)
from tokenshare.storage.artifacts import ArtifactStore


CATALOG_DIGEST = "sha256:" + "a" * 64
ENDPOINT_DIGEST = "sha256:" + "b" * 64
SOURCE_CONFIG_DIGEST = "sha256:" + "d" * 64


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
    assert {selection.topic_family for selection in lean_rate_selections.values()} == {
        None
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
    assert positive["cost_overhead_delta"] == pytest.approx(0.25)
    assert positive["cost_overhead"] == pytest.approx(0.5)
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
    assert worker_death["cost_overhead_delta"] == pytest.approx(0.3)
    assert worker_death["cost_overhead"] == pytest.approx(0.5)
    assert worker_death["cost_overhead_ratio"] == pytest.approx(0.5)
    assert worker_death["completion_rate"] == 0.0
    assert worker_death["wasted_actual_tokens"] == 0


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
            condition_id="fault_zero_with_record",
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
    assert good["root_output_complete"] is False
    assert good["accepted_validity"] is False
    assert good["condition_included"] is False
    assert good["failure_reason"] == "incomplete_recovery"

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


def test_exp3_accepts_integration_prepared_baseline_endpoint_view() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _integration_context()

    conditions = module.expand_conditions(context)

    assert len(conditions) == 273
    assert {condition.reasoning_profile_id for condition in conditions} == {"default"}
    assert {
        condition.source_provider_config_digest for condition in conditions
    } == {SOURCE_CONFIG_DIGEST}
    assert {
        condition.model_endpoint_identity_digest for condition in conditions
    } == {ENDPOINT_DIGEST}


def test_exp3_run_condition_passes_frozen_execution_manifest_to_callback() -> None:
    callback_calls: list[dict[str, Any]] = []

    def callback(
        *,
        context,
        condition,
        selection,
        experiment_id,
        execution_manifest,
    ) -> PaperConditionResult:
        callback_calls.append(
            {
                "context": context,
                "condition": condition,
                "selection": selection,
                "experiment_id": experiment_id,
                "execution_manifest": execution_manifest,
            }
        )
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.PLANNED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        )

    module = Experiment3FaultRecoveryModule()
    context = replace(_integration_context(), execution_callback=callback)
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    index = next(
        index
        for index, condition in enumerate(conditions)
        if condition.domain == "factorization"
        and condition.fault_type == "false_positive"
        and condition.fault_rate == 0.5
        and condition.repeat_id == 0
    )

    result = module.run_condition(context, conditions[index], selections[index])

    assert result.condition_id == conditions[index].condition_id
    assert len(callback_calls) == 1
    call = callback_calls[0]
    assert call["context"] is context
    assert call["experiment_id"] == EXP3_EXPERIMENT_ID
    assert call["condition"].condition_digest == conditions[index].condition_digest
    assert call["selection"] is selections[index]
    manifest = call["execution_manifest"]
    assert manifest["condition_digest"] == conditions[index].condition_digest
    assert manifest["selection_digest"] == selections[index].selection_digest
    assert manifest["fault_target_manifest"]["fault_rate_percent"] == 50
    assert manifest["fault_target_manifest"]["selected_target_ai_unit_ids"]
    assert manifest["matched_baseline"]["condition_id"].endswith(
        "__false_positive__r0__rep0"
    )


def test_exp3_plan_freezes_matched_baselines_and_comparison_seeds() -> None:
    module = Experiment3FaultRecoveryModule()
    context = _integration_context()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)

    plan = build_exp3_plan_manifest(conditions, selections, catalog=context.catalog)

    baseline_by_condition = plan["matched_baseline_by_condition"]
    assert set(baseline_by_condition) == {
        condition.condition_id for condition in conditions
    }
    assert len(plan["matched_baseline_manifest"]) == 24
    assert plan["matched_baseline_root_run_counts"] == {
        "reused_rate_fault_zero": 24,
        "dedicated_worker_death": 18,
        "additional": 18,
        "budgeted_total": 831,
    }
    rate_group = [
        condition
        for condition in conditions
        if condition.domain == "factorization" and condition.repeat_id == 0
        and condition.fault_type != "worker_death"
    ]
    assert len({condition.seed for condition in rate_group}) == 1
    assert {
        baseline_by_condition[condition.condition_id] for condition in rate_group
    } == {"exp3_rate_fault_factorization__false_positive__r0__rep0"}
    worker_group = [
        condition
        for condition in conditions
        if condition.condition_id.startswith("exp3_worker_death_factorization__easy")
        and condition.repeat_id == 0
    ]
    assert len({condition.seed for condition in worker_group}) == 1
    assert len(
        {baseline_by_condition[condition.condition_id] for condition in worker_group}
    ) == 1
    assert next(
        baseline_by_condition[condition.condition_id] for condition in worker_group
    ).startswith("exp3_matched_baseline_worker_death_factorization__easy")


def test_exp3_summary_accepts_standard_paper_attempt_result_shape() -> None:
    run = _rate_fault_run(
        condition_id="standard_attempt_condition",
        matched_baseline_condition_id="standard_attempt_baseline",
        injected_fault_count=0,
        detected_fault_count=0,
        false_accept_count=0,
        recoverable_fault_target_count=0,
        recovered_fault_target_count=0,
    )
    run["fault_rate_percent"] = 0
    run["fault_target_manifest"]["fault_rate_percent"] = 0
    run["wasted_actual_tokens"] = 0
    run["wasted_attempt_ids"] = []
    run["attempts"] = [
        _standard_attempt(
            condition_id="standard_attempt_condition",
            attempt_id="standard_attempt",
            total_tokens=45,
            cost_estimate=0.75,
        )
    ]
    run["baseline_attempts"] = [
        _standard_attempt(
            condition_id="standard_attempt_baseline",
            attempt_id="standard_baseline_attempt",
            total_tokens=30,
            cost_estimate=0.5,
        )
    ]

    row = summarize_exp3(
        {"rate_fault_runs": [run], "worker_death_runs": []}
    ).rows[0]

    assert row["total_actual_tokens"] == 45
    assert row["paper_eligible"] is False


def test_exp3_summary_uses_null_recovery_latency_without_successful_recovery() -> None:
    run = _rate_fault_run(
        condition_id="no_recovery_latency_condition",
        matched_baseline_condition_id="no_recovery_latency_baseline",
        injected_fault_count=1,
        detected_fault_count=1,
        false_accept_count=0,
        recoverable_fault_target_count=0,
        recovered_fault_target_count=0,
    )
    run["recovery_latency_ms"] = None

    row = summarize_exp3(
        {"rate_fault_runs": [run], "worker_death_runs": []}
    ).rows[0]

    assert row["recovery_latency_ms"] is None


def test_exp3_run_condition_rejects_all_canonical_input_drift_before_callback() -> None:
    callback_calls: list[str] = []

    def callback(*, condition, selection) -> PaperConditionResult:
        callback_calls.append(condition.condition_id)
        return PaperConditionResult(
            condition_id=condition.condition_id,
            status=PaperStatus.PLANNED,
            repeat_count=1,
            task_count=len(selection.ordered_case_ids),
            completed_root_count=0,
            failed_root_count=0,
            blocked_root_count=0,
            provider_attempt_count=0,
            metrics_ref=None,
        )

    module = Experiment3FaultRecoveryModule()
    context = replace(_context(), execution_callback=callback)
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    condition = conditions[0]
    selection = selections[0]

    drifted_inputs = (
        (replace(condition, seed=condition.seed + 1), selection, context),
        (
            condition,
            replace(selection, ordered_case_ids=tuple(reversed(selection.ordered_case_ids))),
            context,
        ),
        (
            condition,
            selection,
            replace(
                context,
                catalog={**_catalog(), "catalog_digest": "sha256:" + "c" * 64},
            ),
        ),
        (
            condition,
            selection,
            replace(
                context,
                approved_endpoint_binding={
                    **dict(context.approved_endpoint_binding),
                    "provider_family": "openai",
                    "provider_model_id": "drift-model",
                    "request_limits": {
                        "max_tokens": 1024,
                        "timeout_seconds": 30,
                        "max_provider_attempts": 1,
                        "temperature": 0.9,
                        "enable_thinking": True,
                    },
                },
            ),
        ),
    )

    for drifted_condition, drifted_selection, drifted_context in drifted_inputs:
        with pytest.raises(ValueError):
            module.run_condition(
                drifted_context,
                drifted_condition,
                drifted_selection,
            )

    assert callback_calls == []


def test_exp3_summary_rejects_incomplete_formal_matrix_and_attempt_transport_lie() -> None:
    incomplete = _rate_fault_run(
        condition_id="exp3_rate_fault_factorization__false_positive__r50__rep0",
        matched_baseline_condition_id="baseline_incomplete",
    )
    incomplete["transport_kind"] = "ai_api"
    incomplete["paper_eligible"] = True
    with pytest.raises(ValueError, match="complete formal matrix"):
        summarize_exp3({"rate_fault_runs": [incomplete], "worker_death_runs": []})

    transport_lie = _rate_fault_run(
        condition_id="fault_transport_lie",
        matched_baseline_condition_id="baseline_transport_lie",
    )
    transport_lie["transport_kind"] = "ai_api"
    transport_lie["attempts"][0]["transport_kind"] = "scripted"
    with pytest.raises(ValueError, match="attempt transport"):
        summarize_exp3(
            {"rate_fault_runs": [transport_lie], "worker_death_runs": []}
        )


def test_exp3_formal_matrix_completeness_rejects_canonical_run_field_drift() -> None:
    from tokenshare.experiments.paper_exp3_fault_recovery import (
        _summary_matrix_complete,
    )

    context = _context()
    module = Experiment3FaultRecoveryModule()
    conditions = module.expand_conditions(context)
    selections = module.freeze_case_selections(context, conditions)
    plan = build_exp3_plan_manifest(
        conditions,
        selections,
        catalog=context.catalog,
    )
    target_manifest_by_condition = {
        row["condition_id"]: row for row in plan["fault_target_manifest"]
    }
    rate_runs: list[dict[str, Any]] = []
    worker_runs: list[dict[str, Any]] = []
    for condition, selection in zip(conditions, selections, strict=True):
        run = {
            "condition_id": condition.condition_id,
            "domain": condition.domain,
            "fault_type": condition.fault_type,
            "fault_rate_percent": int(condition.fault_rate * 100),
            "repeat_id": condition.repeat_id,
            "task_count": len(selection.ordered_case_ids),
            "condition_evidence": {
                "condition_id": condition.condition_id,
                "condition_digest": condition.condition_digest,
                "selection_digest": selection.selection_digest,
                "ordered_case_ids": list(selection.ordered_case_ids),
                "repeat_id": condition.repeat_id,
                "seed": condition.seed,
                "worker_count": condition.worker_count,
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
                "request_limits": dict(context.request_limits),
            },
        }
        if condition.fault_type == "worker_death":
            parts = condition.condition_id.split("__")
            run["target_dead_worker_count"] = int(parts[-3].removeprefix("dead"))
            run["target_kill_progress_percent"] = int(
                parts[-2].removeprefix("p")
            )
            worker_runs.append(run)
        else:
            run["fault_target_manifest"] = dict(
                target_manifest_by_condition[condition.condition_id]
            )
            rate_runs.append(run)

    matrix_evidence = {
        "conditions": conditions,
        "selections": selections,
        "catalog": context.catalog,
    }
    assert _summary_matrix_complete(
        matrix_evidence,
        rate_runs=rate_runs,
        worker_runs=worker_runs,
    ) is True

    rate_runs[0]["repeat_id"] = 99
    with pytest.raises(ValueError, match="canonical formal condition"):
        _summary_matrix_complete(
            matrix_evidence,
            rate_runs=rate_runs,
            worker_runs=worker_runs,
        )
    rate_runs[0]["repeat_id"] = conditions[0].repeat_id
    rate_runs[0]["condition_evidence"]["selection_digest"] = (
        "sha256:" + "f" * 64
    )
    with pytest.raises(ValueError, match="formal condition evidence"):
        _summary_matrix_complete(
            matrix_evidence,
            rate_runs=rate_runs,
            worker_runs=worker_runs,
        )
    rate_runs[0]["condition_evidence"]["selection_digest"] = selections[
        0
    ].selection_digest
    rate_runs[0]["fault_target_manifest"]["selected_target_ai_unit_ids"] = [
        "drifted_target"
    ]
    with pytest.raises(ValueError, match="frozen target manifest"):
        _summary_matrix_complete(
            matrix_evidence,
            rate_runs=rate_runs,
            worker_runs=worker_runs,
        )


def test_exp3_summary_binds_baseline_and_aggregate_usage_evidence() -> None:
    baseline_drift = _rate_fault_run(
        condition_id="fault_baseline_evidence_drift",
        matched_baseline_condition_id="baseline_evidence_drift",
    )
    comparison = {
        "selection_digest": "sha256:" + "1" * 64,
        "ordered_case_ids": ["factor_rate_0"],
        "repeat_id": 0,
        "seed": 330001,
        "worker_count": 10,
        "catalog_digest": CATALOG_DIGEST,
        "provider_config_id": "exp1_baseline_siliconflow",
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "default",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_limits": dict(_context().request_limits),
        "prompt_version": "prompt_exp3_v1",
        "parser_version": "parser_v1",
        "plugin_version": "plugin_v1",
        "executor_version": "ai_api_executor_v1",
    }
    baseline_drift["condition_evidence"] = {
        **comparison,
        "condition_id": baseline_drift["condition_id"],
        "condition_digest": "sha256:" + "2" * 64,
    }
    baseline_drift["baseline_evidence"] = {
        **comparison,
        "condition_id": baseline_drift["matched_baseline_condition_id"],
        "condition_digest": "sha256:" + "3" * 64,
        "selection_digest": "sha256:" + "4" * 64,
    }
    with pytest.raises(ValueError, match="baseline comparison"):
        summarize_exp3(
            {"rate_fault_runs": [baseline_drift], "worker_death_runs": []}
        )

    version_drift = _rate_fault_run(
        condition_id="fault_baseline_version_drift",
        matched_baseline_condition_id="baseline_version_drift",
    )
    version_drift["baseline_evidence"]["parser_version"] = "drifted_parser"
    with pytest.raises(ValueError, match="baseline comparison"):
        summarize_exp3(
            {"rate_fault_runs": [version_drift], "worker_death_runs": []}
        )

    baseline_digest_drift = _rate_fault_run(
        condition_id="fault_baseline_digest_drift",
        matched_baseline_condition_id="baseline_digest_drift",
    )
    baseline_digest_drift["baseline_evidence"]["condition_digest"] = (
        "sha256:" + "9" * 64
    )
    with pytest.raises(ValueError, match="expected baseline evidence"):
        summarize_exp3(
            {
                "rate_fault_runs": [baseline_digest_drift],
                "worker_death_runs": [],
            }
        )

    token_drift = _rate_fault_run(
        condition_id="fault_token_drift",
        matched_baseline_condition_id="baseline_token_drift",
    )
    token_drift["attempts"][0]["total_tokens"] = 2
    token_drift["attempts"][0]["cost_estimate"] = 0.01
    token_drift["total_tokens"] = 999
    with pytest.raises(ValueError, match="total_tokens"):
        summarize_exp3(
            {"rate_fault_runs": [token_drift], "worker_death_runs": []}
        )


def test_exp3_summary_requires_full_attempt_identity_and_real_artifact_evidence() -> None:
    identity_drift = _rate_fault_run(
        condition_id="fault_attempt_identity_drift",
        matched_baseline_condition_id="baseline_attempt_identity_drift",
    )
    identity_drift["attempts"][0]["source_provider_config_digest"] = (
        "sha256:" + "9" * 64
    )
    with pytest.raises(ValueError, match="attempt model identity"):
        summarize_exp3(
            {"rate_fault_runs": [identity_drift], "worker_death_runs": []}
        )

    fake_ai = _rate_fault_run(
        condition_id="fault_fake_ai_attempt",
        matched_baseline_condition_id="baseline_fake_ai_attempt",
    )
    fake_ai["transport_kind"] = "ai_api"
    for attempt in (*fake_ai["attempts"], *fake_ai["baseline_attempts"]):
        attempt["transport_kind"] = "ai_api"
        attempt["paper_eligible"] = False
    row = summarize_exp3(
        {"rate_fault_runs": [fake_ai], "worker_death_runs": []}
    ).rows[0]
    assert row["paper_eligible"] is False
    assert any(
        "request_ref" in reason for reason in row["ineligibility_reasons"]
    )


def test_exp3_summary_binds_fault_type_to_injection_point_and_recovery_facts() -> None:
    wrong_point = _rate_fault_run(
        condition_id="fault_wrong_injection_point",
        matched_baseline_condition_id="baseline_wrong_injection_point",
    )
    assert wrong_point["fault_type"] == "false_positive"
    wrong_point["fault_records"][0]["injection_point"] = (
        "after_raw_output_before_parser_bridge"
    )
    assert wrong_point["fault_records"][0]["injection_point"] == (
        "after_raw_output_before_parser_bridge"
    )
    with pytest.raises(ValueError, match="injection point"):
        summarize_exp3(
            {"rate_fault_runs": [wrong_point], "worker_death_runs": []}
        )

    incomplete_recovery = _worker_death_run(
        condition_id="worker_death_incomplete_recovery",
        target_dead_worker_count=3,
        actual_dead_worker_count=3,
        required_slot_count=4,
        recovered_slot_count=3,
    )
    row = summarize_exp3(
        {"rate_fault_runs": [], "worker_death_runs": [incomplete_recovery]}
    ).rows[0]
    assert row["root_output_complete"] is False
    assert row["accepted_validity"] is False
    assert row["condition_included"] is False
    assert row["paper_eligible"] is False


def test_exp3_summary_binds_fault_records_to_frozen_target_manifest() -> None:
    run = _rate_fault_run(
        condition_id="fault_target_manifest_drift",
        matched_baseline_condition_id="baseline_target_manifest_drift",
        injected_fault_count=1,
        detected_fault_count=1,
        false_accept_count=0,
        recoverable_fault_target_count=0,
        recovered_fault_target_count=0,
    )
    run["fault_target_manifest"] = {
        "condition_id": run["condition_id"],
        "fault_type": run["fault_type"],
        "fault_rate_percent": run["fault_rate_percent"],
        "repeat_id": run["repeat_id"],
        "selection_digest": run["condition_evidence"]["selection_digest"],
        "target_seed": 300300,
        "selected_target_ai_unit_ids": ["different_unit"],
    }
    with pytest.raises(ValueError, match="target manifest"):
        summarize_exp3({"rate_fault_runs": [run], "worker_death_runs": []})


def test_exp3_fault_adapter_requires_persisted_provenance_and_feeds_summary(
    tmp_path,
) -> None:
    from tokenshare.experiments.paper_exp3_fault_recovery import (
        inject_exp3_post_ai_fault,
    )

    store = ArtifactStore(tmp_path)
    raw_ref = _save_test_artifact(
        store,
        artifact_id="exp3_raw_1",
        artifact_type="RawModelOutput",
        schema_id="phase7.raw_model_output",
        created_at="2026-07-19T00:00:00Z",
        body={"text": '{"result_kind":"no_factor"}'},
    )
    parsed_ref = _save_test_artifact(
        store,
        artifact_id="exp3_parsed_1",
        artifact_type="ParsedModelOutput",
        schema_id="phase7.parsed_model_output",
        created_at="2026-07-19T00:00:00Z",
        body={
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )
    provenance_ref = _save_test_artifact(
        store,
        artifact_id="exp3_provenance_1",
        artifact_type="AIProviderCallProvenance",
        schema_id="phase7.ai_provider_call_provenance",
        created_at="2026-07-19T00:00:00Z",
        body={"provider_family": "siliconflow"},
    )
    attempt = PaperAttemptResult(
        condition_id="fault_primitive_integration",
        repeat_id=0,
        run_id="run_primitive_integration",
        task_id="task_primitive_integration",
        unit_id="unit_primitive_integration",
        attempt_id="attempt_primitive_integration",
        worker_id="worker_primitive_integration",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="zai-org/GLM-5.2",
        entry_id=BASELINE_MODEL_ENTRY_ID,
        request_ref={"artifact_id": "request_primitive_integration"},
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict(),
        parse_failure_ref=None,
        provenance_ref=provenance_ref.to_dict(),
        usage_ref={"artifact_id": "usage_primitive_integration"},
        started_at="2026-07-19T00:00:00Z",
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=20,
        completion_tokens=25,
        total_tokens=45,
        cost_estimate=0.75,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )
    missing_provenance = dict(provenance_ref.to_dict())
    missing_provenance["artifact_id"] = "missing_provenance"
    missing_provenance["uri"] = "artifacts/missing_provenance"
    with pytest.raises(ValueError, match="persisted provenance"):
        inject_exp3_post_ai_fault(
            artifact_store=store,
            attempt=replace(attempt, provenance_ref=missing_provenance),
            fault_type="false_positive",
            seed=300300,
            created_at="2026-07-19T00:00:02Z",
        )

    outcome = inject_exp3_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type="false_positive",
        seed=300300,
        created_at="2026-07-19T00:00:02Z",
    )
    assert outcome.primitive_outcome.record.to_dict()["fault_type"] == (
        "false_positive"
    )
    assert outcome.record["original_provenance_ref"] == provenance_ref.to_dict()
    assert outcome.record["mutated_provenance_ref"] == (
        outcome.mutation_provenance_ref.to_dict()
    )
    assert outcome.record["provenance_persisted_at"] <= outcome.record["injected_at"]

    run = _rate_fault_run(
        condition_id="fault_primitive_integration",
        matched_baseline_condition_id="baseline_primitive_integration",
        injected_fault_count=1,
        detected_fault_count=1,
        false_accept_count=0,
        recoverable_fault_target_count=0,
        recovered_fault_target_count=0,
    )
    run["original_output_refs"] = [outcome.record["original_output_ref"]]
    run["mutated_output_refs"] = [outcome.record["mutated_output_ref"]]
    run["fault_records"] = [outcome.record]
    run["fault_target_manifest"]["selected_target_ai_unit_ids"] = [
        "unit_primitive_integration"
    ]
    row = summarize_exp3(
        {"rate_fault_runs": [run], "worker_death_runs": []}
    ).rows[0]
    assert row["injected_fault_count"] == 1
    assert row["paper_eligible"] is False


def test_exp3_summary_accepts_public_worker_death_primitive_output(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    condition_id = "worker_death_primitive_integration"
    outcome = run_worker_death_harness(
        artifact_store=store,
        condition_id=condition_id,
        repeat_id=0,
        run_id="run_worker_death_primitive_integration",
        ai_units=(
            PaperAIUnit(
                task_id="task_worker_death_primitive_integration",
                unit_id="unit_worker_death_primitive_integration",
                unit_kind="generic_ai_unit",
                dependencies=(),
                depth=0,
                domain="factorization",
                metadata={},
            ),
        ),
        target_unit_id="unit_worker_death_primitive_integration",
        kill_point=WorkerDeathKillPoint.PROGRESS_25,
        started_at="2026-07-19T00:00:00Z",
        process_tick_seconds=0.001,
        lease_ttl_seconds=1,
    )
    run = _worker_death_run(
        condition_id=condition_id,
        target_dead_worker_count=1,
        actual_dead_worker_count=1,
        required_slot_count=1,
        recovered_slot_count=1,
    )
    run["target_kill_progress_percent"] = 25
    run["actual_kill_progress_percent"] = 25
    run["worker_death_records"] = [outcome.record.to_dict()]
    run["worker_death_record_refs"] = [outcome.record_ref.to_dict()]

    row = summarize_exp3(
        {"rate_fault_runs": [], "worker_death_runs": [run]}
    ).rows[0]

    assert row["actual_dead_worker_count"] == 1
    assert row["coordinator_continued"] is True
    assert row["paper_eligible"] is False


def _save_test_artifact(
    store: ArtifactStore,
    *,
    artifact_id: str,
    artifact_type: str,
    schema_id: str,
    created_at: str,
    body: dict[str, Any],
):
    return store.save_json(
        body,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_schema_id=schema_id,
        artifact_schema_version="v1",
        source={"kind": "ai_api_executor"},
        metadata={},
        created_at=created_at,
    )


def _context(catalog: dict[str, Any] | None = None) -> PaperExecutionContext:
    def callback(
        *,
        context,
        condition,
        selection,
        experiment_id,
        execution_manifest,
    ) -> PaperConditionResult:
        assert experiment_id == EXP3_EXPERIMENT_ID
        assert execution_manifest["condition_id"] == condition.condition_id
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
        approved_endpoint_binding={
            "provider_config_id": "exp1_baseline_siliconflow",
            "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
            "model_entry_id": BASELINE_MODEL_ENTRY_ID,
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "reasoning_profile_id": "default",
            "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
            "model_endpoint_identity_digest": ENDPOINT_DIGEST,
            "request_controls": {
                "max_tokens": 1024,
                "timeout_seconds": 30,
                "max_provider_attempts": 1,
                "temperature": 0.0,
                "top_p": 1.0,
                "stream": False,
                "enable_thinking": False,
            },
        },
        request_limits={
            "max_tokens": 1024,
            "timeout_seconds": 30,
            "max_provider_attempts": 1,
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
            "enable_thinking": False,
        },
        hard_limits={"max_total_provider_attempts": 0},
        output_root="outputs/experiments/exp3_test",
        artifact_store=object(),
        event_store=object(),
        execution_callback=callback,
    )


def _integration_context() -> PaperExecutionContext:
    request_controls = {
        "max_tokens": 1024,
        "timeout_seconds": 30,
        "max_provider_attempts": 1,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "enable_thinking": False,
    }
    context = _context()
    return replace(
        context,
        approved_endpoint_binding={
            "provider_config_id": "exp1_baseline_siliconflow",
            "selected_entry_id": BASELINE_MODEL_ENTRY_ID,
            "model_entry_id": BASELINE_MODEL_ENTRY_ID,
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "reasoning_profile_id": "default",
            "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
            "model_endpoint_identity_digest": ENDPOINT_DIGEST,
            "request_controls": request_controls,
        },
        request_limits=request_controls,
    )


def _standard_attempt(
    *,
    condition_id: str,
    attempt_id: str,
    total_tokens: int,
    cost_estimate: float,
) -> dict[str, Any]:
    return PaperAttemptResult(
        condition_id=condition_id,
        repeat_id=0,
        run_id=f"run_{condition_id}",
        task_id=f"task_{condition_id}",
        unit_id=f"unit_{condition_id}",
        attempt_id=attempt_id,
        worker_id=f"worker_{condition_id}",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="zai-org/GLM-5.2",
        entry_id=BASELINE_MODEL_ENTRY_ID,
        request_ref=None,
        raw_output_ref=None,
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-19T00:00:00Z",
        ended_at="2026-07-19T00:00:01Z",
        latency_ms=1000,
        prompt_tokens=total_tokens,
        completion_tokens=0,
        total_tokens=total_tokens,
        cost_estimate=cost_estimate,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    ).to_dict()


def _expected_condition_seed(condition_id: str) -> int:
    parts = condition_id.split("__")
    if parts[0] == "exp3_rate_fault_factorization":
        matrix_kind = "rate_fault"
        domain = "factorization"
        task_slice_key = "factorization"
        repeat_id = int(parts[-1].removeprefix("rep"))
    elif parts[0] == "exp3_rate_fault_lean":
        matrix_kind = "rate_fault"
        domain = "lean_proof"
        task_slice_key = parts[1]
        repeat_id = int(parts[-1].removeprefix("rep"))
    elif parts[0] == "exp3_worker_death_factorization":
        matrix_kind = "worker_death"
        domain = "factorization"
        task_slice_key = parts[1]
        repeat_id = int(parts[-1].removeprefix("rep"))
    else:
        matrix_kind = "worker_death"
        domain = "lean_proof"
        task_slice_key = parts[1]
        repeat_id = int(parts[-1].removeprefix("rep"))
    digest = digest_json(
        {
            "schema_version": "tokenshare.paper_exp3_comparison_seed.v1",
            "matrix_kind": matrix_kind,
            "domain": domain,
            "task_slice_key": task_slice_key,
            "repeat_id": repeat_id,
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


def _comparison_evidence(
    *,
    condition_id: str,
    condition_digest: str,
    ordered_case_ids: list[str],
) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "condition_digest": condition_digest,
        "selection_digest": "sha256:" + "1" * 64,
        "ordered_case_ids": ordered_case_ids,
        "repeat_id": 0,
        "seed": 330001,
        "worker_count": 10,
        "catalog_digest": CATALOG_DIGEST,
        "provider_config_id": "exp1_baseline_siliconflow",
        "model_entry_id": BASELINE_MODEL_ENTRY_ID,
        "provider_family": "siliconflow",
        "provider_model_id": "zai-org/GLM-5.2",
        "reasoning_profile_id": "default",
        "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
        "model_endpoint_identity_digest": ENDPOINT_DIGEST,
        "request_limits": dict(_context().request_limits),
        "prompt_version": "prompt_exp3_v1",
        "parser_version": "parser_v1",
        "plugin_version": "plugin_v1",
        "executor_version": "ai_api_executor_v1",
    }


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
    condition_evidence = _comparison_evidence(
        condition_id=condition_id,
        condition_digest="sha256:" + "5" * 64,
        ordered_case_ids=[f"factor_rate_{index}" for index in range(5)],
    )
    baseline_evidence = {
        **condition_evidence,
        "condition_id": matched_baseline_condition_id,
        "condition_digest": "sha256:" + "6" * 64,
    }
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
                condition_id=condition_id,
                original_output_ref=original_refs[index],
                mutated_output_ref=mutated_refs[index],
            )
            for index in range(injected_fault_count)
        ],
        "attempts": [
            {
                "attempt_id": f"attempt_{condition_id}",
                "condition_id": condition_id,
                "repeat_id": 0,
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider_config_id": "exp1_baseline_siliconflow",
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "default",
                "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
                "model_endpoint_identity_digest": ENDPOINT_DIGEST,
                "request_limits": dict(_context().request_limits),
                "transport_kind": "scripted",
                "paper_eligible": False,
                "total_tokens": total_tokens,
                "cost_estimate": cost_estimate,
                "wasted_actual_tokens": 7,
            }
        ],
        "baseline_attempts": [
            {
                "attempt_id": f"attempt_{matched_baseline_condition_id}",
                "condition_id": matched_baseline_condition_id,
                "repeat_id": 0,
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider_config_id": "exp1_baseline_siliconflow",
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "default",
                "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
                "model_endpoint_identity_digest": ENDPOINT_DIGEST,
                "request_limits": dict(_context().request_limits),
                "transport_kind": "scripted",
                "paper_eligible": False,
                "total_tokens": baseline_total_tokens,
                "cost_estimate": baseline_cost_estimate,
                "wasted_actual_tokens": 0,
            }
        ],
        "condition_evidence": condition_evidence,
        "baseline_evidence": baseline_evidence,
        "expected_baseline_evidence": dict(baseline_evidence),
        "fault_target_manifest": {
            "condition_id": condition_id,
            "fault_type": "false_positive",
            "fault_rate_percent": 50,
            "repeat_id": 0,
            "selection_digest": condition_evidence["selection_digest"],
            "target_seed": 300300,
            "selected_target_ai_unit_ids": [
                f"unit_{index}" for index in range(injected_fault_count)
            ],
        },
        "transport_kind": "scripted",
        "paper_eligible": False,
    }


def _fault_record(
    *,
    index: int,
    condition_id: str,
    original_output_ref: dict[str, str],
    mutated_output_ref: dict[str, str],
) -> dict[str, Any]:
    target_context = {
        "schema_version": "tokenshare.paper_fault_target.v1",
        "target_kind": "ai_unit",
        "unit_id": f"unit_{index}",
        "attempt_id": f"attempt_{index}",
    }
    return {
        "condition_id": condition_id,
        "repeat_id": 0,
        "fault_type": "false_positive",
        "seed": 300300,
        "unit_id": f"unit_{index}",
        "attempt_id": f"attempt_{index}",
        "target_context": target_context,
        "target_selection_digest": digest_json(target_context),
        "injection_point": "after_parsed_candidate_before_verification",
        "original_raw_output_ref": {"artifact_id": f"raw_{index}"},
        "original_provenance_ref": {"artifact_id": f"provenance_{index}"},
        "original_output_ref": original_output_ref,
        "mutated_output_ref": mutated_output_ref,
        "mutated_provenance_ref": {"artifact_id": f"mutated_provenance_{index}"},
        "raw_persisted_at": "2026-07-19T00:00:00Z",
        "provenance_persisted_at": "2026-07-19T00:00:00Z",
        "injected_at": "2026-07-19T00:00:01Z",
        "mutation_provenance_persisted_at": "2026-07-19T00:00:01Z",
        "canonical_pollution": False,
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
    complete = recovered_slot_count == required_slot_count
    condition_evidence = _comparison_evidence(
        condition_id=condition_id,
        condition_digest="sha256:" + "7" * 64,
        ordered_case_ids=[f"worker_case_{condition_id}"],
    )
    baseline_evidence = {
        **condition_evidence,
        "condition_id": matched_baseline_condition_id,
        "condition_digest": "sha256:" + "8" * 64,
    }
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
        "root_output_complete": complete,
        "accepted_validity": complete,
        "verifier_evidence": {
            "artifact_ref": {"artifact_id": f"verifier_{condition_id}"},
            "root_output_complete": complete,
            "accepted_validity": complete,
        },
        "recovery_latency_ms": 30,
        "retry_count": 1,
        "reassignment_count": 1,
        "wasted_actual_tokens": 0,
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
                "attempt_id": f"attempt_{condition_id}",
                "condition_id": condition_id,
                "repeat_id": 0,
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider_config_id": "exp1_baseline_siliconflow",
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "default",
                "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
                "model_endpoint_identity_digest": ENDPOINT_DIGEST,
                "request_limits": dict(_context().request_limits),
                "transport_kind": "scripted",
                "paper_eligible": False,
                "total_tokens": 61,
                "cost_estimate": 0.9,
                "wasted_actual_tokens": 0,
            }
        ],
        "baseline_attempts": [
            {
                "attempt_id": f"attempt_{matched_baseline_condition_id}",
                "condition_id": matched_baseline_condition_id,
                "repeat_id": 0,
                "entry_id": BASELINE_MODEL_ENTRY_ID,
                "provider_config_id": "exp1_baseline_siliconflow",
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "reasoning_profile_id": "default",
                "source_provider_config_digest": SOURCE_CONFIG_DIGEST,
                "model_endpoint_identity_digest": ENDPOINT_DIGEST,
                "request_limits": dict(_context().request_limits),
                "transport_kind": "scripted",
                "paper_eligible": False,
                "total_tokens": baseline_total_tokens,
                "cost_estimate": baseline_cost_estimate,
                "wasted_actual_tokens": 0,
            }
        ],
        "condition_evidence": condition_evidence,
        "baseline_evidence": baseline_evidence,
        "expected_baseline_evidence": dict(baseline_evidence),
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
