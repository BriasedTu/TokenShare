import csv
import json
from pathlib import Path

from tokenshare.experiments.adapters import AdapterRegistry
from tokenshare.experiments.factorization_adapter import FactorizationExperimentAdapter
from tokenshare.experiments.lean_adapter import LeanProofExperimentAdapter
from tokenshare.experiments.metrics import build_metrics
from tokenshare.experiments.models import ExperimentCase, ExperimentStatus, SimulationProfile
from tokenshare.experiments.runner import ExperimentRunner
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


def test_runner_writes_factorization_semiprime_report_and_metrics(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(FactorizationExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)
    profile = SimulationProfile(profile_id="full", seed=1, executor_profile="deterministic_local")

    result = runner.run(
        ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="semiprime_range_flow",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="semiprime_range_flow",
            expected_event_types=[
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
            ],
            expected_outputs={"target_n": "91", "prime_factors": ["7", "13"]},
        ),
        profile=profile,
    )

    assert result.run.status == ExperimentStatus.PASSED
    manifest = json.loads((result.output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    case_report = json.loads((result.output_dir / "case_report.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))
    summary_rows = list(
        csv.DictReader((result.output_dir / "metrics" / "paper_summary.csv").open(encoding="utf-8"))
    )

    assert manifest["schema_version"] == "phase8.experiment_run_manifest.v1"
    assert manifest["event_log_ref"]["event_count"] > 0
    assert manifest["plugin_descriptors"][0]["plugin_id"] == "factorization"
    assert manifest["plugin_descriptors"][0]["plugin_version"] == "0.1.0"
    assert case_report["status"] == "passed"
    assert case_report["evidence_refs"]["event_log_path"].endswith("events/event_log.jsonl")
    assert metrics["schema_version"] == "phase8.experiment_metrics.v1"
    assert metrics["common_metrics"]["artifact_link_success"] is True
    assert metrics["common_metrics"]["settlement_success"] is True
    assert metrics["experiment_metrics"]["final_correctness"] is True
    assert metrics["experiment_metrics"]["canonical_pollution_count"] == 0
    assert metrics["experiment_metrics"]["prompt_package_coverage"] == 1.0
    assert metrics["common_metrics"]["work"]["execution_request_count"] > 0
    assert metrics["common_metrics"]["critical_path"]["event_count"] == metrics["common_metrics"]["event_count"]
    assert metrics["common_metrics"]["retry_wasted_work"]["rejected_attempt_count"] == 0
    assert metrics["common_metrics"]["shadow_benefit"]["shadow_execution_enabled"] is False
    assert metrics["experiment_metrics"]["ai_api_executor_effect"]["executor_profile"] == "deterministic_local"
    assert summary_rows[0]["experiment_id"] == "exp1_factorization_e2e"
    assert summary_rows[0]["status"] == "passed"
    assert summary_rows[0]["final_correctness"] == "true"


def test_runner_runs_lean_direct_proof_with_real_checker_evidence(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(LeanProofExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)

    result = runner.run(
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_direct_proof",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_direct_proof",
        ),
        profile=SimulationProfile(profile_id="full", seed=1),
    )

    manifest = json.loads((result.output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))

    assert result.run.status == ExperimentStatus.PASSED
    assert manifest["status"] == "passed"
    assert manifest["plugin_descriptors"][0]["plugin_id"] == "lean_proof"
    assert metrics["experiment_metrics"]["real_checker_evidence"] is True
    assert metrics["experiment_metrics"]["environment_ref_complete"] is True
    assert metrics["experiment_metrics"]["lean_decomposition_lifecycle_coverage"] == 0.0
    assert metrics["common_metrics"]["settlement_success"] is True
    assert metrics["common_metrics"]["event_count"] > 0
    assert metrics["common_metrics"]["artifact_count"] > 0


def test_runner_runs_lean_decomposition_merge_with_lifecycle_coverage(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(LeanProofExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)

    result = runner.run(
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_decomposition_merge",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_decomposition_merge",
        ),
        profile=SimulationProfile(profile_id="full", seed=1),
    )

    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))

    assert result.run.status == ExperimentStatus.PASSED
    assert metrics["experiment_metrics"]["real_checker_evidence"] is True
    assert metrics["experiment_metrics"]["environment_ref_complete"] is True
    assert metrics["experiment_metrics"]["lean_decomposition_lifecycle_coverage"] == 1.0
    assert metrics["experiment_metrics"]["merge_recheck_success"] is True
    assert metrics["common_metrics"]["settlement_success"] is True


def test_runner_can_still_report_lean_blocked_when_preflight_is_injected(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(LeanProofExperimentAdapter(preflight_ready=False))
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)

    result = runner.run(
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_direct_proof",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_direct_proof",
        ),
        profile=SimulationProfile(profile_id="full", seed=1),
    )

    manifest = json.loads((result.output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))

    assert result.run.status == ExperimentStatus.BLOCKED
    assert manifest["blocked_reason"]["blocker_kind"] == "pending_real_lean_plugin"
    assert metrics["experiment_metrics"]["real_checker_evidence"] is False


def test_lean_adapter_preflight_checks_real_toolchain_by_default(tmp_path: Path):
    adapter = LeanProofExperimentAdapter(
        project_root=tmp_path / "missing_lean_project",
        lean_executable=tmp_path / "missing_tools" / "lean.exe",
        lake_executable=tmp_path / "missing_tools" / "lake.exe",
    )

    preflight = adapter.preflight(
        ExperimentCase(
            experiment_id="exp4_real_plugin_generality",
            case_id="lean_direct_proof",
            plugin_id="lean_proof",
            plugin_version="0.1.0",
            fixture_name="lean_direct_proof",
        ),
        SimulationProfile(profile_id="full", seed=1),
    )

    assert preflight.ready is False
    assert preflight.blocked_reason["blocker_kind"] == "blocked_missing_toolchain"
    assert str(tmp_path / "missing_tools" / "lean.exe") in preflight.blocked_reason[
        "missing_paths"
    ]


def test_runner_writes_failure_injection_case_without_canonical_pollution(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(FactorizationExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)

    result = runner.run(
        ExperimentCase(
            experiment_id="exp2_failure_recovery",
            case_id="invalid_found_factor",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="invalid_found_factor",
            expected_outputs={"target_n": "91"},
        ),
        profile=SimulationProfile(
            profile_id="invalid-found-factor",
            seed=1,
            fault_profile="invalid_found_factor",
        ),
    )

    case_report = json.loads((result.output_dir / "case_report.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))

    assert result.run.status == ExperimentStatus.PASSED
    assert case_report["simulation"]["fault_profile"] == "invalid_found_factor"
    assert case_report["fault_injection"]["failure_kind"] == "invalid_output"
    assert case_report["fault_injection"]["canonical_pollution"] is False
    assert metrics["experiment_metrics"]["canonical_pollution_count"] == 0
    assert metrics["experiment_metrics"]["detection_rate"] == 1.0


def test_runner_writes_ablation_case_with_expected_degradation(tmp_path: Path):
    registry = AdapterRegistry()
    registry.register(FactorizationExperimentAdapter())
    runner = ExperimentRunner(adapter_registry=registry, output_root=tmp_path)

    result = runner.run(
        ExperimentCase(
            experiment_id="exp3_protocol_ablation",
            case_id="no_all_required_merge_gate",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="no_all_required_merge_gate",
            expected_outputs={"target_n": "91"},
        ),
        profile=SimulationProfile(
            profile_id="ablation-no-merge-gate",
            seed=1,
            ablation_mode="NO_ALL_REQUIRED_MERGE_GATE",
        ),
    )

    case_report = json.loads((result.output_dir / "case_report.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.output_dir / "metrics" / "metrics.json").read_text(encoding="utf-8"))

    assert result.run.status == ExperimentStatus.INCONCLUSIVE
    assert case_report["ablation"]["mode"] == "NO_ALL_REQUIRED_MERGE_GATE"
    assert case_report["ablation"]["expected_degradation"] == "premature_merge_risk"
    assert metrics["experiment_metrics"]["premature_merge_rate"] == 1.0


def test_metrics_collects_ai_api_usage_cost_from_submission_artifact(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "ai_api.jsonl")
    submitted_at = "2026-06-29T00:00:00Z"
    submission_ref = store.save_json(
        {
            "schema_version": "phase3.execution_submission.v1",
            "submission_id": "submission_ai_metrics",
            "request_id": "request_ai_metrics",
            "task_id": "task_ai_metrics",
            "unit_id": "unit_ai_metrics",
            "attempt_id": "attempt_ai_metrics",
            "lease_id": "lease_ai_metrics",
            "fencing_token": "fence_ai_metrics",
            "executor_id": "executor_ai_api",
            "executor_version": "0.1.0",
            "result_kind": "succeeded",
            "raw_output_ref": None,
            "parsed_output_ref": None,
            "candidate_output_refs": {},
            "parse_failure_ref": None,
            "log_ref": None,
            "environment_ref": {
                "schema_version": "phase3.environment_ref.v1",
                "environment_id": "env_ai_metrics",
                "environment_digest": "sha256:env",
                "runtime": "python",
                "tool_versions": {},
                "resource_limits": {},
                "fixture_profile_digest": "sha256:fixture",
                "seed": 1,
                "clock_policy": "fixed",
                "created_at": submitted_at,
            },
            "environment_summary": {"provider_family": "siliconflow"},
            "provenance_ref": None,
            "usage_summary": {
                "provider_family": "siliconflow",
                "provider_attempt_count": 2,
                "total_tokens": 17,
                "cost_estimate": 0.125,
                "cost_estimate_status": "estimated",
            },
            "error": None,
            "submitted_at": submitted_at,
        },
        artifact_id="submission_ai_metrics",
        artifact_type="ExecutionSubmission",
        artifact_schema_id="phase3.execution_submission",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"task_id": "task_ai_metrics"},
        created_at=submitted_at,
    )
    ledger.append(
        event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
        object_type="ExecutionSubmission",
        object_id="submission_ai_metrics",
        task_id="task_ai_metrics",
        actor={"kind": "test"},
        correlation_id="corr_ai_metrics",
        idempotency_key="execution_submission:submission_ai_metrics",
        payload={
            "schema_version": "phase3.execution_submission_record.v1",
            "submission_id": "submission_ai_metrics",
            "submission_ref": submission_ref.to_dict(),
            "submission_digest": submission_ref.content_hash,
            "result_kind": "succeeded",
            "submitted_at": submitted_at,
        },
        occurred_at=submitted_at,
    )

    metrics = build_metrics(
        run_id="run_ai_metrics",
        status="passed",
        case=ExperimentCase(
            experiment_id="exp1_factorization_e2e",
            case_id="ai_api_profile",
            plugin_id="factorization",
            plugin_version="0.1.0",
            fixture_name="semiprime_range_flow",
        ),
        profile=SimulationProfile(
            profile_id="ai-api",
            seed=1,
            executor_profile="ai_api",
        ),
        event_log_path=ledger.path,
        artifact_root=store.artifact_dir,
        experiment_metrics={"final_correctness": True},
    )

    assert metrics["common_metrics"]["ai_api_usage_cost"] == {
        "provider_attempt_count": 2,
        "latency_ms_total": 0,
        "cost_estimate_total": 0.125,
    }
    assert metrics["experiment_metrics"]["ai_api_executor_effect"] == {
        "executor_profile": "ai_api",
        "real_api_called": True,
        "provider_attempt_count": 2,
        "cost_estimate_total": 0.125,
    }
