from tokenshare.experiments.models import SimulationProfile
from tokenshare.experiments.simulation import SimulationWrapper


def test_simulation_wrapper_records_five_fault_kinds_and_ablation_decisions():
    profile = SimulationProfile(
        profile_id="failure-profile",
        seed=11,
        fault_profile="offline",
        ablation_mode="NO_REQUEUE",
    )
    wrapper = SimulationWrapper(profile)

    assert wrapper.fault_decision("range_worker") == {
        "schema_version": "phase8.simulation_decision.v1",
        "profile_id": "failure-profile",
        "seed": 11,
        "target": "range_worker",
        "decision_kind": "fault",
        "selected": "offline",
        "supported_faults": [
            "offline",
            "slow",
            "executor_error",
            "invalid_output",
            "late_submission",
        ],
    }
    assert wrapper.ablation_decision() == {
        "schema_version": "phase8.simulation_decision.v1",
        "profile_id": "failure-profile",
        "seed": 11,
        "target": "protocol_wrapper",
        "decision_kind": "ablation",
        "selected": "NO_REQUEUE",
        "supported_ablations": [
            "FULL",
            "NO_VERIFICATION",
            "NO_PARSER_POLICY",
            "NO_REQUEUE",
            "NO_ALL_REQUIRED_MERGE_GATE",
            "NO_SLOT_INTEGRITY_CHECK",
        ],
    }
