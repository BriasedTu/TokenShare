import copy
import json
from pathlib import Path

from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.experiments import (
    factorization_paper_adapter,
    lean_paper_adapter,
    paper_exp1,
    paper_exp2_scalability,
    paper_exp3_fault_recovery,
    paper_exp4_ablation_runner,
    paper_formal_runner,
    paper_model_policy,
    run_paper_experiments,
)
from tokenshare.experiments.paper_models import digest_json


ROOT = Path(__file__).resolve().parents[2]
BASELINE_V1 = ROOT / "benchmarks/paper/exp1_baseline_provider_config.v1.json"
BASELINE_V2 = ROOT / "benchmarks/paper/exp1_baseline_provider_config.v2.json"
BASELINE_V3 = ROOT / "benchmarks/paper/exp1_baseline_provider_config.v3.json"
PROFILE_V1 = ROOT / "benchmarks/paper/exp1_minimal_pilot_profile.v1.json"
PROFILE_V2 = ROOT / "benchmarks/paper/exp1_minimal_pilot_profile.v2.json"
PROFILE_V3 = ROOT / "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
COHORT_V1 = ROOT / "benchmarks/paper/model_comparison_cohort.v1.json"
COHORT_V2 = ROOT / "benchmarks/paper/model_comparison_cohort.v2.json"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_exp1_to_exp4_defaults_are_official_deepseek_v4_pro() -> None:
    expected = {
        "provider_config_id": "exp1_baseline_deepseek",
        "entry_id": "deepseek_v4_pro_exp1_baseline",
        "provider_family": "deepseek",
        "model": "deepseek-v4-pro",
        "reasoning_profile_id": "high",
    }
    modules = (
        paper_exp1,
        paper_exp2_scalability,
        paper_exp3_fault_recovery,
        paper_exp4_ablation_runner,
    )

    for module in modules:
        assert getattr(
            module,
            "EXP1_BASELINE_PROVIDER_CONFIG_ID",
            getattr(module, "BASELINE_PROVIDER_CONFIG_ID", None),
        ) == expected["provider_config_id"]
        assert getattr(
            module,
            "EXP1_BASELINE_ENTRY_ID",
            getattr(module, "BASELINE_MODEL_ENTRY_ID", None),
        ) == expected["entry_id"]
        assert getattr(
            module,
            "EXP1_BASELINE_PROVIDER_FAMILY",
            getattr(module, "BASELINE_PROVIDER_FAMILY", None),
        ) == expected["provider_family"]
        assert getattr(
            module,
            "EXP1_BASELINE_PROVIDER_MODEL_ID",
            getattr(module, "BASELINE_PROVIDER_MODEL_ID", None),
        ) == expected["model"]

    controls = paper_exp1.EXP1_FORMAL_REQUEST_CONTROLS
    assert controls == {
        "max_tokens": 300_000,
        "timeout_seconds": 600,
        "max_provider_attempts": 1,
        "stream": False,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert paper_exp2_scalability.MANDATORY_WORKER_LEVELS == (1, 3, 7, 10, 30, 50)


def test_baseline_v3_is_fixed_deepseek_config_and_cli_default() -> None:
    body = _read_json(BASELINE_V3)
    config = load_ai_api_config(body)
    entry = config.entries[0]

    assert config.provider_family == "deepseek"
    assert config.local_concurrency["max_in_flight_global"] == 50
    assert entry.base_url == "https://api.deepseek.com"
    assert entry.endpoint == "/chat/completions"
    assert entry.model == "deepseek-v4-pro"
    assert entry.api_key_env == "DEEPSEEK_API_KEY"
    assert entry.request_overrides == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert config.defaults["timeout_seconds"] == 600
    assert config.defaults["max_tokens"] == 300_000
    assert "temperature" not in config.defaults
    assert "top_p" not in config.defaults
    assert run_paper_experiments.DEFAULT_EXP1_PILOT_PROFILE.as_posix().endswith(
        "exp1_minimal_pilot_profile.v3.json"
    )
    profile = _read_json(PROFILE_V3)
    assert profile["baseline_provider"]["provider_config_path"] == (
        "exp1_baseline_provider_config.v3.json"
    )
    assert {
        domain: {
            "timeout_seconds": limits["timeout_seconds"],
            "max_tokens": limits["max_tokens"],
        }
        for domain, limits in profile["request_policy"]["domain_limits"].items()
    } == {
        "factorization": {"timeout_seconds": 600, "max_tokens": 300_000},
        "lean_proof": {"timeout_seconds": 600, "max_tokens": 300_000},
    }


def test_factorization_and_lean_real_adapters_route_deepseek_transport() -> None:
    factor_type = factorization_paper_adapter._real_transport_type("deepseek")
    lean_type = lean_paper_adapter._real_transport_type("deepseek")

    assert factor_type.__name__ == "UrlLibDeepSeekTransport"
    assert lean_type is factor_type


def test_model_comparison_cohort_v2_is_exact_three_endpoint_contract() -> None:
    body = _read_json(COHORT_V2)
    members = {item["cohort_member_id"]: item for item in body["members"]}

    assert body["cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v2"
    assert set(members) == {
        "glm_5_2_siliconflow",
        "deepseek_v4_pro_deepseek",
        "gpt_5_6_sol_high_openai",
    }
    assert all(member["max_tokens"] == 8192 for member in members.values())
    assert members["glm_5_2_siliconflow"]["request_overrides"] == {
        "enable_thinking": True
    }
    assert members["deepseek_v4_pro_deepseek"]["request_overrides"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert members["gpt_5_6_sol_high_openai"]["request_overrides"] == {
        "reasoning_effort": "high"
    }
    assert paper_model_policy.PAPER_MODEL_ENDPOINT_COHORT_ID == body["cohort_id"]
    assert tuple(paper_model_policy.PAPER_MODEL_ENDPOINT_COHORT_MEMBER_IDS) == tuple(
        members
    )


def test_exp1_to_exp4_budget_token_bound_covers_v3_request_ceiling() -> None:
    assert run_paper_experiments.FORMAL_TOKEN_UPPER_BOUND_PER_PROVIDER_ATTEMPT == (
        300_000 + 4_096
    )


def test_v1_configs_remain_loadable_with_original_digests_and_v2_is_sensitive() -> None:
    baseline_v1 = load_ai_api_config(_read_json(BASELINE_V1))
    cohort_v1_body = _read_json(COHORT_V1)
    cohort_v1 = paper_model_policy.load_model_endpoint_cohort(COHORT_V1)

    assert baseline_v1.config_digest == (
        "sha256:a602b7597691089a40e1d036b60f426d272e906e8f59d067938e9364e411fbcf"
    )
    assert cohort_v1["model_cohort_digest"] == (
        "sha256:6a8745c4fdd419f616d540a92bb899fedf68f8c8bf110061680f054876aa2e02"
    )
    assert _read_json(PROFILE_V1)["baseline_provider"]["provider_config_path"] == (
        "exp1_baseline_provider_config.v1.json"
    )
    assert "qwen3_6_27b_siliconflow" in {
        member["cohort_member_id"] for member in cohort_v1_body["members"]
    }

    baseline_v2_body = _read_json(BASELINE_V2)
    baseline_v2 = load_ai_api_config(baseline_v2_body)
    assert baseline_v2.config_digest == (
        "sha256:b000e54782c8dbe8a7df122ec8fc4d6c9b98d1b1f2d6f9dbc94f7029aebaa99f"
    )
    assert _read_json(PROFILE_V2)["baseline_provider"]["provider_config_path"] == (
        "exp1_baseline_provider_config.v2.json"
    )

    baseline_v3_body = _read_json(BASELINE_V3)
    baseline_v3 = load_ai_api_config(baseline_v3_body)
    changed_baseline = copy.deepcopy(baseline_v3_body)
    changed_baseline["defaults"]["max_tokens"] = 299_999
    assert baseline_v3.config_digest != load_ai_api_config(changed_baseline).config_digest
    changed_timeout = copy.deepcopy(baseline_v3_body)
    changed_timeout["defaults"]["timeout_seconds"] = 599
    assert baseline_v3.config_digest != load_ai_api_config(changed_timeout).config_digest

    cohort_v2_body = _read_json(COHORT_V2)
    assert digest_json(cohort_v2_body) == (
        "sha256:4be1c6e981e636ab524009a2f521a522fc63409f88255a33c2afb82412862fd8"
    )
    assert all(member["max_tokens"] == 8192 for member in cohort_v2_body["members"])
    changed_cohort = copy.deepcopy(cohort_v2_body)
    changed_cohort["members"][0]["max_tokens"] = 8191
    assert digest_json(cohort_v2_body) != digest_json(changed_cohort)


def test_mixed_currency_cost_estimates_are_kept_separate() -> None:
    usage = paper_formal_runner._UsageTotals()
    for currency, amount in (("CNY", 1.25), ("USD", 0.75)):
        reservation = paper_formal_runner._RootBudgetReservation(
            provider_attempt_count=0,
            total_tokens=0,
            total_cost_estimate=0.0,
            currency=currency,
        )
        paper_formal_runner._settle_hard_limit_reservation(
            usage=usage,
            reservation=reservation,
            total_cost_estimate=amount,
            cost_estimate_currency=currency,
        )

    assert usage.cost_estimate_by_currency == {"CNY": 1.25, "USD": 0.75}
    assert usage.reportable_total_cost_estimate() == 0.0
    assert usage.cost_estimate_status() == "mixed_currency_not_aggregated"
