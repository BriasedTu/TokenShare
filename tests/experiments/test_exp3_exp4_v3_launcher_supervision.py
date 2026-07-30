from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "local" / "run_exp3_exp4_v3_smoke.ps1"


def test_exp3_exp4_v3_launcher_freezes_identity_before_real_transport() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "paper_smoke_exp3_exp4_profile.v1.json" in source
    assert source.count('"--smoke-profile", $profilePath') == 2
    assert "exp1_baseline_provider_config.v3.json" in source
    assert "--smoke-identity-only" in source
    assert "--real-transport" in source
    assert source.index("--smoke-identity-only") < source.index("--real-transport")
    assert source.count("Invoke-TokenShareNativeProcessWithLogs") == 2
    assert "--resume" not in source
    assert "--restart" not in source


def test_exp3_exp4_v3_launcher_rejects_existing_paths_and_baseline_drift() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "Test-Path -LiteralPath $OutputRoot" in source
    assert "Test-Path -LiteralPath $SupervisorRoot" in source
    assert '"paper_smoke_exp3_exp4_v1"' in source
    assert '"omitted_for_smoke_regression"' in source
    assert '"smoke_baseline_not_requested"' in source
    assert "$semantics.baseline -ne $null" in source
    assert "$semantics.baseline_comparison_eligible -ne $false" in source
    assert "$semantics.cross_experiment_evidence_allowed -ne $false" in source
    assert "$semantics.supporting_baseline_root_runs -ne 0" in source
    assert "$semantics.supporting_baseline_ai_units -ne 0" in source
    assert "$semantics.supporting_provider_attempt_upper_bound -ne 0" in source


def test_exp3_exp4_v3_launcher_freezes_canonical_denominators_and_provider_controls() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "$semantics.direct_root_runs -ne 11" in source
    assert "$semantics.actual_scheduled_root_runs -ne 11" in source
    assert "$semantics.planned_first_attempt_ai_units -ne 22" in source
    assert "$semantics.budget_provider_attempt_upper_bound -ne 54" in source
    assert "$semantics.provider_retry_limit -ne 0" in source
    assert '$semantics.worker_counts -join ",") -ne "10"' in source
    assert '$semantics.repeat_ids -join ",") -ne "0"' in source
    assert '$semantics.model_endpoint.provider_model_id -ne "deepseek-v4-pro"' in source
    assert '$semantics.model_endpoint.reasoning_profile_id -ne "high"' in source
    assert "$semantics.model_endpoint.provider_inflight_limit -ne 50" in source
    assert "$semantics.request_limits.max_provider_attempts -ne 1" in source
    assert "$semantics.request_limits.max_tokens -ne 300000" in source
    assert "$semantics.request_limits.timeout_seconds -ne 600" in source
    assert "$semantics.request_limits.stream -ne $false" in source
    assert '$semantics.request_limits.thinking.type -ne "enabled"' in source
    assert '$semantics.request_limits.reasoning_effort -ne "high"' in source
