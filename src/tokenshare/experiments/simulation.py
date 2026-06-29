"""Phase 8 故障注入和消融 wrapper 的纯决策记录。"""

from __future__ import annotations

from tokenshare.experiments.models import JsonObject, SimulationProfile


SUPPORTED_FAULTS = [
    "offline",
    "slow",
    "executor_error",
    "invalid_output",
    "late_submission",
]

SUPPORTED_ABLATIONS = [
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_ALL_REQUIRED_MERGE_GATE",
    "NO_SLOT_INTEGRITY_CHECK",
]


class SimulationWrapper:
    """记录实验 wrapper 决策，不修改 protocol core 默认语义。"""

    def __init__(self, profile: SimulationProfile) -> None:
        self.profile = profile

    def fault_decision(self, target: str) -> JsonObject:
        selected = self.profile.fault_profile
        if selected == "none":
            selected = "none"
        elif selected not in SUPPORTED_FAULTS:
            selected = _normalize_factorization_fault(selected)
        return {
            "schema_version": "phase8.simulation_decision.v1",
            "profile_id": self.profile.profile_id,
            "seed": self.profile.seed,
            "target": target,
            "decision_kind": "fault",
            "selected": selected,
            "supported_faults": list(SUPPORTED_FAULTS),
        }

    def ablation_decision(self) -> JsonObject:
        selected = self.profile.ablation_mode
        if selected not in SUPPORTED_ABLATIONS:
            raise ValueError(f"unsupported ablation mode: {selected}")
        return {
            "schema_version": "phase8.simulation_decision.v1",
            "profile_id": self.profile.profile_id,
            "seed": self.profile.seed,
            "target": "protocol_wrapper",
            "decision_kind": "ablation",
            "selected": selected,
            "supported_ablations": list(SUPPORTED_ABLATIONS),
        }


def _normalize_factorization_fault(fault_profile: str) -> str:
    mapping = {
        "invalid_found_factor": "invalid_output",
        "false_no_factor_in_range": "invalid_output",
        "parse_failure_raw_only_forbidden": "invalid_output",
        "worker_crash_expired_lease": "late_submission",
        "no_factor_recheck_budget_exceeded": "invalid_output",
    }
    return mapping.get(fault_profile, fault_profile)
