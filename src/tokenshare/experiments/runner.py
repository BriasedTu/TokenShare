"""Phase 8 通用 ExperimentRunner。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tokenshare.experiments.adapters import AdapterRegistry
from tokenshare.experiments.factorization_adapter import FactorizationExperimentAdapter
from tokenshare.experiments.lean_adapter import LeanProofExperimentAdapter
from tokenshare.experiments.models import (
    AdapterRunResult,
    ExperimentCase,
    ExperimentResult,
    ExperimentRun,
    ExperimentStatus,
    JsonObject,
    SimulationProfile,
)
from tokenshare.experiments.report import SUMMARY_COLUMNS
from tokenshare.experiments.report import write_run_outputs
from tokenshare.storage.events import utc_now


class ExperimentRunner:
    """运行 Phase 8 实验 case 并写结构化本地输出。"""

    def __init__(self, *, adapter_registry: AdapterRegistry, output_root: str | Path) -> None:
        self.adapter_registry = adapter_registry
        self.output_root = Path(output_root)

    def run(self, case: ExperimentCase, *, profile: SimulationProfile) -> ExperimentResult:
        adapter = self.adapter_registry.get(case.plugin_id, case.plugin_version)
        created_at = utc_now()
        run = ExperimentRun.create(case=case, profile=profile, created_at=created_at)
        output_dir = self.output_root / run.run_id
        preflight = adapter.preflight(case, profile)
        if not preflight.ready:
            run = run.with_status(
                ExperimentStatus.BLOCKED,
                blocked_reason=preflight.blocked_reason,
            )
            adapter_result = adapter.run_case(case, profile, output_dir)
        else:
            adapter_result = adapter.run_case(case, profile, output_dir)
            run = run.with_status(adapter_result.status)
        run = run.with_evidence(
            plugin_descriptors=adapter_result.plugin_descriptors
            or preflight.plugin_descriptors,
            executor_descriptors=adapter_result.executor_descriptors
            or preflight.executor_descriptors,
            event_log_ref=_event_log_manifest_ref(adapter_result.case_report),
            artifact_root=_artifact_root_manifest_ref(adapter_result.case_report),
        )
        metrics = _with_run_id(adapter_result.metrics, run.run_id)
        paths = write_run_outputs(
            output_dir=output_dir,
            run=run,
            case_report=_with_run_id(adapter_result.case_report, run.run_id),
            metrics=metrics,
        )
        return ExperimentResult(
            run=run,
            output_dir=output_dir,
            manifest_path=paths["manifest"],
            case_report_path=paths["case_report"],
            metrics_path=paths["metrics"],
            summary_csv_path=paths["summary_csv"],
        )


def default_experiment_cases() -> tuple[ExperimentCase, ...]:
    """返回最新实验设计的默认 Phase 8 case 矩阵。"""

    lifecycle = [
        "REGISTRY_SNAPSHOT_RECORDED",
        "EXECUTION_REQUEST_RECORDED",
        "EXECUTION_SUBMISSION_RECORDED",
        "VERIFICATION_RECORDED",
        "CANONICAL_OUTPUTS_BOUND",
        "SPLIT_STRATEGY_INVOCATION_RECORDED",
        "DECOMPOSITION_PROPOSAL_RECORDED",
        "EXPANSION_DECISION_RECORDED",
        "MERGE_PLAN_RECORDED",
        "TASK_EXPANDED",
        "MERGE_TASK_LINK_RECORDED",
        "MERGE_RECORDED",
        "EXPECTED_OUTPUT_RESOLVED",
        "CONTRIBUTION_STATE_CHANGED",
        "SETTLEMENT_RECORDED",
    ]
    exp1 = (
        ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="small_prime_direct_complete",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="small_prime_direct_complete",
            expected_event_types=[
                "REGISTRY_SNAPSHOT_RECORDED",
                "EXECUTION_REQUEST_RECORDED",
                "EXECUTION_SUBMISSION_RECORDED",
                "VERIFICATION_RECORDED",
                "CANONICAL_OUTPUTS_BOUND",
                "SPLIT_STRATEGY_INVOCATION_RECORDED",
                "EXPANSION_DECISION_RECORDED",
                "CONTRIBUTION_STATE_CHANGED",
                "SETTLEMENT_RECORDED",
            ],
            expected_outputs={"target_n": "2", "prime_factors": ["2"]},
        ),
        ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="prime_range_flow",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="prime_range_flow",
            expected_event_types=lifecycle,
            expected_outputs={"target_n": "97", "prime_factors": ["97"]},
        ),
        ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="semiprime_range_flow",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="semiprime_range_flow",
            expected_event_types=lifecycle,
            expected_outputs={"target_n": "91", "prime_factors": ["7", "13"]},
        ),
        ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="extended_semiprime_benchmark",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="extended_semiprime_benchmark",
            expected_event_types=lifecycle,
            expected_outputs={"target_n": "8051", "prime_factors": ["83", "97"]},
        ),
    )
    exp2_case_ids = (
        "invalid_found_factor",
        "false_no_factor_in_range",
        "parse_failure_raw_only_forbidden",
        "worker_crash_expired_lease",
        "no_factor_recheck_budget_exceeded",
    )
    exp2 = tuple(
        ExperimentCase(
            experiment_id="exp2_failure_recovery",
            case_id=case_id,
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name=case_id,
            expected_outputs={"target_n": "91"},
        )
        for case_id in exp2_case_ids
    )
    exp3_modes = (
        ("no_verification", "NO_VERIFICATION"),
        ("no_parser_policy", "NO_PARSER_POLICY"),
        ("no_requeue", "NO_REQUEUE"),
        ("no_all_required_merge_gate", "NO_ALL_REQUIRED_MERGE_GATE"),
        ("no_slot_integrity_check", "NO_SLOT_INTEGRITY_CHECK"),
    )
    exp3 = tuple(
        ExperimentCase(
            experiment_id="exp3_protocol_ablation",
            case_id=case_id,
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name=case_id,
            expected_outputs={"target_n": "91"},
            metadata={"ablation_mode": ablation_mode},
        )
        for case_id, ablation_mode in exp3_modes
    )
    exp4 = (
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="factorization_semiprime_lifecycle",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="semiprime_range_flow",
            expected_event_types=lifecycle,
            expected_outputs={"target_n": "91", "prime_factors": ["7", "13"]},
        ),
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_direct_proof",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_direct_proof",
        ),
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_decomposition_merge",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_decomposition_merge",
        ),
    )
    return exp1 + exp2 + exp3 + exp4


def run_phase8_default_suite(*, output_root: str | Path, seed: int = 1) -> JsonObject:
    """运行默认 Phase 8 deterministic baseline suite。"""

    root = Path(output_root)
    registry = AdapterRegistry()
    registry.register(FactorizationExperimentAdapter())
    registry.register(LeanProofExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=root / "runs")
    results: list[ExperimentResult] = []
    cases = default_experiment_cases()
    profiles: list[SimulationProfile] = []
    for case in cases:
        metadata = case.metadata or {}
        profile = SimulationProfile(
            profile_id=f"{case.experiment_id}:{case.case_id}",
            seed=seed,
            executor_profile="deterministic_local",
            fault_profile=case.case_id if case.experiment_id == "exp2_failure_recovery" else "none",
            ablation_mode=str(metadata.get("ablation_mode", "FULL")),
        )
        profiles.append(profile)
        results.append(runner.run(case, profile=profile))

    summary_path = root / "phase8_suite_summary.csv"
    suite_report_path = root / "phase8_suite_report.json"
    settings_path = root / "phase8_experiment_settings.json"
    _write_suite_summary(summary_path, results)
    _write_suite_settings(
        settings_path=settings_path,
        output_root=root,
        cases=cases,
        profiles=profiles,
        results=results,
        summary_csv_path=summary_path,
        suite_report_path=suite_report_path,
    )
    suite_report = _suite_report(
        results=results,
        summary_csv_path=summary_path,
        suite_report_path=suite_report_path,
        settings_path=settings_path,
    )
    suite_report_path.write_text(
        json.dumps(suite_report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return suite_report


def _event_log_manifest_ref(case_report: JsonObject) -> JsonObject | None:
    ref = case_report.get("event_log_ref")
    if isinstance(ref, dict) and ref:
        return dict(ref)
    return None


def _artifact_root_manifest_ref(case_report: JsonObject) -> JsonObject | None:
    ref = case_report.get("artifact_root")
    if isinstance(ref, dict) and ref:
        return dict(ref)
    return None


def _with_run_id(body: JsonObject, run_id: str) -> JsonObject:
    copied = dict(body)
    copied["run_id"] = run_id
    if copied.get("paper_table_rows"):
        copied["paper_table_rows"] = [
            {**row, "run_id": run_id} for row in copied["paper_table_rows"]
        ]
    return copied


def _write_suite_summary(path: Path, results: list[ExperimentResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for result in results:
            with result.summary_csv_path.open(encoding="utf-8") as source:
                reader = csv.DictReader(source)
                for row in reader:
                    writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


def _write_suite_settings(
    *,
    settings_path: Path,
    output_root: Path,
    cases: tuple[ExperimentCase, ...],
    profiles: list[SimulationProfile],
    results: list[ExperimentResult],
    summary_csv_path: Path,
    suite_report_path: Path,
) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema_version": "phase8.experiment_suite_settings.v1",
        "seed": profiles[0].seed if profiles else None,
        "output_root": output_root.as_posix(),
        "runner_output_root": (output_root / "runs").as_posix(),
        "summary_csv_path": summary_csv_path.as_posix(),
        "suite_report_path": suite_report_path.as_posix(),
        "settings_path": settings_path.as_posix(),
        "total_cases": len(cases),
        "experiment_cases": [case.to_dict() for case in cases],
        "simulation_profiles": [profile.to_dict() for profile in profiles],
        "run_manifest_paths": [result.manifest_path.as_posix() for result in results],
        "case_report_paths": [result.case_report_path.as_posix() for result in results],
        "metrics_paths": [result.metrics_path.as_posix() for result in results],
    }
    settings_path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _suite_report(
    *,
    results: list[ExperimentResult],
    summary_csv_path: Path,
    suite_report_path: Path,
    settings_path: Path,
) -> JsonObject:
    statuses = [result.run.status.value for result in results]
    blocked_by_kind: dict[str, int] = {}
    for result in results:
        if result.run.status != ExperimentStatus.BLOCKED:
            continue
        blocker_kind = str((result.run.blocked_reason or {}).get("blocker_kind", "unknown"))
        blocked_by_kind[blocker_kind] = blocked_by_kind.get(blocker_kind, 0) + 1
    return {
        "schema_version": "phase8.experiment_suite_report.v1",
        "total_runs": len(results),
        "passed_runs": statuses.count(ExperimentStatus.PASSED.value),
        "inconclusive_runs": statuses.count(ExperimentStatus.INCONCLUSIVE.value),
        "blocked_runs": statuses.count(ExperimentStatus.BLOCKED.value),
        "failed_runs": statuses.count(ExperimentStatus.FAILED.value),
        "blocked_by_kind": blocked_by_kind,
        "summary_csv_path": summary_csv_path.as_posix(),
        "suite_report_path": suite_report_path.as_posix(),
        "settings_path": settings_path.as_posix(),
        "run_manifest_paths": [result.manifest_path.as_posix() for result in results],
    }
