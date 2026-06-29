import pytest

from tokenshare.experiments.models import (
    ExperimentCase,
    ExperimentRun,
    ExperimentStatus,
    SimulationProfile,
)


def test_simulation_profile_digest_is_stable_and_records_fault_and_ablation():
    profile = SimulationProfile(
        profile_id="phase8-full-deterministic",
        seed=1,
        executor_profile="deterministic_local",
        fault_profile="invalid_found_factor",
        ablation_mode="NO_VERIFICATION",
    )
    same_profile = SimulationProfile(
        profile_id="phase8-full-deterministic",
        seed=1,
        executor_profile="deterministic_local",
        fault_profile="invalid_found_factor",
        ablation_mode="NO_VERIFICATION",
    )

    assert profile.schema_version == "phase8.simulation_profile.v1"
    assert profile.profile_digest == same_profile.profile_digest
    assert profile.to_dict()["fault_profile"] == "invalid_found_factor"
    assert profile.to_dict()["ablation_mode"] == "NO_VERIFICATION"


def test_experiment_run_id_includes_experiment_case_profile_and_seed():
    profile = SimulationProfile(profile_id="full", seed=7)
    case = ExperimentCase(
        experiment_id="exp1_factorization_e2e",
        case_id="semiprime_range_flow",
        plugin_id="factorization",
        plugin_version="0.1.0",
        fixture_name="semiprime_range_flow",
        expected_event_types=["SETTLEMENT_RECORDED"],
    )
    run = ExperimentRun.create(case=case, profile=profile, created_at="2026-06-29T00:00:00Z")

    assert run.schema_version == "phase8.experiment_run_manifest.v1"
    assert run.run_id.startswith("exp1_factorization_e2e__semiprime_range_flow__")
    assert run.profile_digest == profile.profile_digest
    assert run.seed == 7
    assert run.status == ExperimentStatus.PENDING


def test_blocked_run_requires_structured_blocked_reason():
    profile = SimulationProfile(profile_id="full", seed=1)
    case = ExperimentCase(
        experiment_id="exp4_real_plugin_generality",
        case_id="lean_direct_proof",
        plugin_id="lean_proof",
        plugin_version="0.1.0",
        fixture_name="lean_direct_proof",
    )
    run = ExperimentRun.create(case=case, profile=profile, created_at="2026-06-29T00:00:00Z")

    with pytest.raises(ValueError, match="blocked_reason"):
        run.with_status(ExperimentStatus.BLOCKED)

    blocked = run.with_status(
        ExperimentStatus.BLOCKED,
        blocked_reason={
            "blocker_kind": "pending_real_lean_plugin",
            "required_capabilities": ["fixed_lean_environment_ref"],
        },
    )
    assert blocked.blocked_reason["blocker_kind"] == "pending_real_lean_plugin"
