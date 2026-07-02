import csv
import json
from pathlib import Path

from tokenshare.experiments.runner import default_experiment_cases, run_phase8_default_suite


def test_default_suite_runs_experiment_1_to_4_with_lean_blocked_gate(tmp_path: Path):
    cases = default_experiment_cases()

    assert [
        (case.experiment_id, case.case_id)
        for case in cases
        if case.experiment_id == "exp1_factorization_e2e"
    ] == [
        ("exp1_factorization_e2e", "small_prime_direct_complete"),
        ("exp1_factorization_e2e", "prime_range_flow"),
        ("exp1_factorization_e2e", "semiprime_range_flow"),
        ("exp1_factorization_e2e", "extended_semiprime_benchmark"),
    ]
    assert {
        case.case_id
        for case in cases
        if case.experiment_id == "exp2_failure_recovery"
    } == {
        "invalid_found_factor",
        "false_no_factor_in_range",
        "parse_failure_raw_only_forbidden",
        "worker_crash_expired_lease",
        "no_factor_recheck_budget_exceeded",
    }
    assert {
        case.case_id
        for case in cases
        if case.experiment_id == "exp3_protocol_ablation"
    } == {
        "no_verification",
        "no_parser_policy",
        "no_requeue",
        "no_all_required_merge_gate",
        "no_slot_integrity_check",
    }
    assert [
        (case.case_id, case.plugin_id)
        for case in cases
        if case.experiment_id == "exp4_real_plugin_generality"
    ] == [
        ("factorization_semiprime_lifecycle", "factorization"),
        ("lean_direct_proof", "lean_proof"),
        ("lean_decomposition_merge", "lean_proof"),
    ]

    suite = run_phase8_default_suite(output_root=tmp_path, seed=3)

    assert suite["schema_version"] == "phase8.experiment_suite_report.v1"
    assert suite["total_runs"] == len(cases)
    assert suite["passed_runs"] == 12
    assert suite["inconclusive_runs"] == 5
    assert suite["blocked_runs"] == 0
    assert suite["blocked_by_kind"] == {}
    assert suite["summary_csv_path"].endswith("phase8_suite_summary.csv")
    assert suite["settings_path"].endswith("phase8_experiment_settings.json")

    settings = json.loads(Path(suite["settings_path"]).read_text(encoding="utf-8"))
    assert settings["schema_version"] == "phase8.experiment_suite_settings.v1"
    assert settings["seed"] == 3
    assert settings["total_cases"] == len(cases)
    assert {
        item["experiment_id"] for item in settings["experiment_cases"]
    } == {
        "exp1_factorization_e2e",
        "exp2_failure_recovery",
        "exp3_protocol_ablation",
        "exp4_real_plugin_generality",
    }
    assert any(
        profile["ablation_mode"] == "NO_VERIFICATION"
        for profile in settings["simulation_profiles"]
    )
    assert len(settings["run_manifest_paths"]) == len(cases)

    rows = list(csv.DictReader(Path(suite["summary_csv_path"]).open(encoding="utf-8")))
    assert len(rows) == len(cases)
    assert any(row["case_id"] == "semiprime_range_flow" and row["status"] == "passed" for row in rows)
    assert any(
        row["case_id"] == "factorization_semiprime_lifecycle" and row["status"] == "passed"
        for row in rows
    )
    assert any(
        row["case_id"] == "lean_direct_proof"
        and row["status"] == "passed"
        and row["real_checker_evidence"] == "true"
        for row in rows
    )
    assert any(
        row["case_id"] == "lean_decomposition_merge"
        and row["status"] == "passed"
        and row["real_checker_evidence"] == "true"
        for row in rows
    )

    suite_report = json.loads(Path(suite["suite_report_path"]).read_text(encoding="utf-8"))
    assert suite_report["blocked_by_kind"] == {}
