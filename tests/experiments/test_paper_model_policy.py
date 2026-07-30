import importlib
import importlib.util
import json
from pathlib import Path
from typing import Callable

import pytest

from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    run_factorization_paper_case,
)
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.experiments.paper_runner import expand_plan_conditions
from tokenshare.experiments.run_paper_experiments import main


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"
V3_COHORT_PATH = Path("benchmarks/paper/model_comparison_cohort.v3.json")
V3_ENTRY_MAP_PATH = Path("benchmarks/paper/model_comparison_entry_map.v3.json")
V3_PROVIDER_CONFIG_PATH = Path(
    "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
)


def test_model_endpoint_policy_preflights_v3_four_member_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    members = (
        (
            "glm_5_2_siliconflow",
            "glm_5_2_exp5_v3",
            "zai-org/GLM-5.2",
            "thinking",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "currency": "CNY",
                "cached_input_per_million_tokens": 2.0,
                "uncached_input_per_million_tokens": 8.0,
                "output_per_million_tokens": 28.0,
                "source_url": "https://siliconflow.cn/pricing",
                "accessed_at": "2026-07-29",
            },
        ),
        (
            "qwen3_14b_siliconflow",
            "qwen3_14b_exp5_v3",
            "Qwen/Qwen3-14B",
            "thinking",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "currency": "CNY",
                "input_per_million_tokens": 0.5,
                "output_per_million_tokens": 2.0,
                "source_url": "https://siliconflow.cn/pricing",
                "accessed_at": "2026-07-29",
            },
        ),
        (
            "minimax_m2_5_siliconflow",
            "minimax_m2_5_exp5_v3",
            "MiniMaxAI/MiniMax-M2.5",
            "thinking",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "currency": "CNY",
                "cached_input_per_million_tokens": 0.21,
                "uncached_input_per_million_tokens": 2.1,
                "output_per_million_tokens": 8.4,
                "source_url": "https://siliconflow.cn/pricing",
                "accessed_at": "2026-07-29",
            },
        ),
        (
            "deepseek_v3_pro_siliconflow",
            "deepseek_v3_pro_exp5_v3",
            "Pro/deepseek-ai/DeepSeek-V3",
            "default",
            {"enable_thinking": False},
            {
                "currency": "CNY",
                "input_per_million_tokens": 2.0,
                "output_per_million_tokens": 8.0,
                "source_url": "https://siliconflow.cn/pricing",
                "accessed_at": "2026-07-29",
            },
        ),
    )
    cohort_path = tmp_path / "cohort-v3.json"
    cohort_path.write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_model_endpoint_cohort.v3",
                "cohort_id": "tokenshare.paper.model_endpoint_cohort.v3",
                "members": [
                    {
                        "cohort_member_id": member_id,
                        "provider_family": "siliconflow",
                        "provider_model_id": model,
                        "reasoning_profile_id": reasoning_profile,
                        "request_overrides": request_overrides,
                        "max_tokens": 32768,
                    }
                    for (
                        member_id,
                        _entry_id,
                        model,
                        reasoning_profile,
                        request_overrides,
                        _pricing,
                    ) in members
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    entry_map_path = tmp_path / "entry-map-v3.json"
    entry_map_path.write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_model_entry_map.v1",
                "cohort_id": "tokenshare.paper.model_endpoint_cohort.v3",
                "members": {
                    member_id: {
                        "provider_config_id": "siliconflow",
                        "entry_id": entry_id,
                        "smoke_evidence_ref": _smoke_evidence(
                            member_id=member_id,
                            entry_id=entry_id,
                            provider_family="siliconflow",
                            provider_model_id=model,
                            reasoning_profile_id=reasoning_profile,
                        ),
                    }
                    for (
                        member_id,
                        entry_id,
                        model,
                        reasoning_profile,
                        _request_overrides,
                        _pricing,
                    ) in members
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "provider-config-v3.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "phase7.ai_api_executor_config.v1",
                "executor_id": "executor_ai_api_exp5_siliconflow_v3",
                "provider_family": "siliconflow",
                "selection_policy": {
                    "kind": "uniform_random_without_weights",
                    "seed_source": "request_or_environment_seed",
                },
                "defaults": {
                    "timeout_seconds": 600,
                    "max_tokens": 32768,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "stream": False,
                    "max_provider_attempts": 1,
                },
                "entries": [
                    {
                        "entry_id": entry_id,
                        "enabled": True,
                        "base_url": "https://api.siliconflow.cn/v1",
                        "api_key_env": "SILICONFLOW_API_KEY",
                        "model": model,
                        "endpoint": "/chat/completions",
                        "supports_json_mode": True,
                        "supports_streaming": False,
                        "request_overrides": request_overrides,
                        "pricing": pricing,
                        "tags": ["paper", "exp5", "cohort:v3"],
                    }
                    for (
                        _member_id,
                        entry_id,
                        model,
                        _reasoning_profile,
                        request_overrides,
                        pricing,
                    ) in members
                ],
                "local_concurrency": {"max_in_flight_global": 3},
                "metadata": {"secret_policy": "api_key_env_only"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")

    cohort = policy.load_model_endpoint_cohort(cohort_path)
    entry_map = policy.load_model_entry_map(entry_map_path)
    provider_configs = policy.load_provider_config_map(
        {"siliconflow": config_path}
    )
    config = provider_configs["siliconflow"]
    members_by_id = {
        member["cohort_member_id"]: member for member in cohort["members"]
    }
    for member_id, spec in entry_map["members"].items():
        spec["smoke_evidence_ref"] = _v3_smoke_evidence(
            policy=policy,
            cohort=cohort,
            config=config,
            member=members_by_id[member_id],
            entry_id=spec["entry_id"],
        )
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
    )

    assert cohort["cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v3"
    assert cohort["model_cohort_digest"].startswith("sha256:")
    assert preflight["status"] == "planned"
    assert preflight["provider_calls_made"] == 0
    assert preflight["expected_member_ids"] == [
        member_id for member_id, *_rest in members
    ]
    assert preflight["request_controls_snapshot"] == {
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "timeout_seconds": 600,
        "max_tokens": 32768,
        "max_provider_attempts": 1,
        "domain_contracts": preflight["request_controls_snapshot"][
            "domain_contracts"
        ],
    }
    assert all(
        plan["pricing_snapshot_digest"].startswith("sha256:")
        for plan in preflight["member_plans"].values()
    )


def test_tracked_v3_cohort_entry_map_and_provider_config_are_exact_and_safe() -> None:
    policy = _policy_module()
    cohort = policy.load_model_endpoint_cohort(V3_COHORT_PATH)
    entry_map = policy.load_model_entry_map(V3_ENTRY_MAP_PATH)
    config = policy.load_provider_config_map(
        {"siliconflow": V3_PROVIDER_CONFIG_PATH}
    )["siliconflow"]

    expected_members = [
        (
            "glm_5_2_siliconflow",
            "glm_5_2_exp5_v3",
            "zai-org/GLM-5.2",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "cached_input_per_million_tokens": 2.0,
                "uncached_input_per_million_tokens": 8.0,
                "output_per_million_tokens": 28.0,
            },
        ),
        (
            "qwen3_14b_siliconflow",
            "qwen3_14b_exp5_v3",
            "Qwen/Qwen3-14B",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "input_per_million_tokens": 0.5,
                "output_per_million_tokens": 2.0,
            },
        ),
        (
            "minimax_m2_5_siliconflow",
            "minimax_m2_5_exp5_v3",
            "MiniMaxAI/MiniMax-M2.5",
            {"enable_thinking": True, "thinking_budget": 32768},
            {
                "cached_input_per_million_tokens": 0.21,
                "uncached_input_per_million_tokens": 2.1,
                "output_per_million_tokens": 8.4,
            },
        ),
        (
            "deepseek_v3_pro_siliconflow",
            "deepseek_v3_pro_exp5_v3",
            "Pro/deepseek-ai/DeepSeek-V3",
            {"enable_thinking": False},
            {
                "input_per_million_tokens": 2.0,
                "output_per_million_tokens": 8.0,
            },
        ),
    ]
    assert cohort["schema_version"] == "tokenshare.paper_model_endpoint_cohort.v3"
    assert cohort["cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v3"
    assert [member["cohort_member_id"] for member in cohort["members"]] == [
        member_id for member_id, *_rest in expected_members
    ]
    assert entry_map["cohort_id"] == cohort["cohort_id"]
    assert set(entry_map["members"]) == {
        member_id for member_id, *_rest in expected_members
    }
    assert {
        spec["provider_config_id"] for spec in entry_map["members"].values()
    } == {"siliconflow"}
    assert all(
        spec["smoke_evidence_ref"] is None
        for spec in entry_map["members"].values()
    )

    assert config.provider_family == "siliconflow"
    assert config.defaults == {
        "timeout_seconds": 600,
        "max_tokens": 32768,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "max_provider_attempts": 1,
    }
    assert config.local_concurrency == {"max_in_flight_global": 3}
    assert [entry.entry_id for entry in config.entries] == [
        entry_id for _member_id, entry_id, *_rest in expected_members
    ]
    assert len({entry.entry_id for entry in config.entries}) == 4
    expected_by_entry = {
        entry_id: (model, reasoning, pricing)
        for _member_id, entry_id, model, reasoning, pricing in expected_members
    }
    for entry in config.entries:
        model, reasoning, pricing = expected_by_entry[entry.entry_id]
        assert entry.model == model
        assert entry.api_key_env == "SILICONFLOW_API_KEY"
        assert entry.request_overrides == reasoning
        assert entry.pricing["currency"] == "CNY"
        assert entry.pricing["source_url"] == "https://siliconflow.cn/pricing"
        assert entry.pricing["accessed_at"] == "2026-07-29"
        assert {key: entry.pricing[key] for key in pricing} == pricing
    assert all("api_key" not in entry for entry in _load_json(V3_PROVIDER_CONFIG_PATH)["entries"])


def test_v3_smoke_bootstrap_preflight_does_not_require_prior_smoke_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    cohort = policy.load_model_endpoint_cohort(V3_COHORT_PATH)
    entry_map = policy.load_model_entry_map(V3_ENTRY_MAP_PATH)
    provider_configs = policy.load_provider_config_map(
        {"siliconflow": V3_PROVIDER_CONFIG_PATH}
    )

    formal_preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
    )
    smoke_bootstrap = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
        require_smoke_evidence=False,
    )

    assert formal_preflight["status"] == "blocked"
    assert all(
        plan["blocked_reasons"] == ["missing_smoke_evidence"]
        for plan in formal_preflight["member_plans"].values()
    )
    assert smoke_bootstrap["status"] == "planned"
    assert smoke_bootstrap["provider_calls_made"] == 0
    assert all(
        plan["status"] == "planned" and plan["blocked_reasons"] == []
        for plan in smoke_bootstrap["member_plans"].values()
    )


def test_v3_preflight_blocks_missing_key_and_disabled_member_before_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    missing_key = _preflight_from_tracked_v3(
        policy=policy,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        set_key=False,
    )
    assert missing_key["status"] == "blocked"
    assert missing_key["provider_calls_made"] == 0
    assert all(
        "missing_api_key_env" in plan["blocked_reasons"]
        for plan in missing_key["member_plans"].values()
    )

    def disable_qwen(config: dict) -> None:
        next(
            entry
            for entry in config["entries"]
            if entry["entry_id"] == "qwen3_14b_exp5_v3"
        )["enabled"] = False

    disabled = _preflight_from_tracked_v3(
        policy=policy,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_config=disable_qwen,
    )
    assert disabled["status"] == "blocked"
    assert disabled["provider_calls_made"] == 0
    assert "selected_entry_disabled" in disabled["member_plans"][
        "qwen3_14b_siliconflow"
    ]["blocked_reasons"]


def test_v3_preflight_blocks_common_reasoning_and_pricing_drift_before_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()

    def drift_common_control(config: dict) -> None:
        config["defaults"]["timeout_seconds"] = 100

    common_drift = _preflight_from_tracked_v3(
        policy=policy,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_config=drift_common_control,
    )
    assert common_drift["status"] == "blocked"
    assert common_drift["provider_calls_made"] == 0
    assert all(
        "formal_ai_timeout_seconds_mismatch" in plan["blocked_reasons"]
        for plan in common_drift["member_plans"].values()
    )

    def drift_reasoning(config: dict) -> None:
        next(
            entry
            for entry in config["entries"]
            if entry["entry_id"] == "glm_5_2_exp5_v3"
        )["request_overrides"]["thinking_budget"] = 32767

    reasoning_drift = _preflight_from_tracked_v3(
        policy=policy,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_config=drift_reasoning,
    )
    assert reasoning_drift["status"] == "blocked"
    assert reasoning_drift["provider_calls_made"] == 0
    assert "entry_reasoning_controls_mismatch" in reasoning_drift[
        "member_plans"
    ]["glm_5_2_siliconflow"]["blocked_reasons"]

    def drift_pricing(config: dict) -> None:
        next(
            entry
            for entry in config["entries"]
            if entry["entry_id"] == "qwen3_14b_exp5_v3"
        )["pricing"]["input_per_million_tokens"] = 0.6

    pricing_drift = _preflight_from_tracked_v3(
        policy=policy,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_config=drift_pricing,
    )
    assert pricing_drift["status"] == "blocked"
    assert pricing_drift["provider_calls_made"] == 0
    assert "pricing_snapshot_rate_mismatch" in pricing_drift["member_plans"][
        "qwen3_14b_siliconflow"
    ]["blocked_reasons"]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("enable_thinking", 1),
        ("thinking_budget", 32768.0),
    ),
)
def test_v3_cohort_reasoning_overrides_require_strict_json_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    invalid_value: object,
) -> None:
    def mutate_cohort(cohort: dict) -> None:
        cohort["members"][0]["request_overrides"][field_name] = invalid_value

    preflight = _preflight_from_tracked_v3(
        policy=_policy_module(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_cohort=mutate_cohort,
    )

    assert preflight["status"] == "blocked"
    assert preflight["provider_calls_made"] == 0
    assert "cohort_request_overrides_mismatch" in preflight["member_plans"][
        "glm_5_2_siliconflow"
    ]["blocked_reasons"]


def test_v3_smoke_v2_binds_actual_identity_controls_and_pricing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = _preflight_from_tracked_v3(
        policy=_policy_module(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert preflight["status"] == "planned"
    assert preflight["provider_calls_made"] == 0


@pytest.mark.parametrize(
    ("case_name", "mutate_smoke"),
    (
        (
            "legacy_v1",
            lambda _member_id, smoke: smoke.update(
                {
                    "schema_version": (
                        "tokenshare.paper_model_endpoint_smoke_evidence.v1"
                    )
                }
            ),
        ),
        (
            "cohort_digest",
            lambda _member_id, smoke: smoke.update(
                {"model_cohort_digest": "sha256:" + "a" * 64}
            ),
        ),
        (
            "source_config_digest",
            lambda _member_id, smoke: smoke.update(
                {"source_provider_config_digest": "sha256:" + "b" * 64}
            ),
        ),
        (
            "endpoint_identity_digest",
            lambda _member_id, smoke: smoke.update(
                {"model_endpoint_identity_digest": "sha256:" + "c" * 64}
            ),
        ),
        (
            "request_controls_digest",
            lambda _member_id, smoke: smoke.update(
                {"request_controls_digest": "sha256:" + "d" * 64}
            ),
        ),
        (
            "pricing_snapshot_digest",
            lambda _member_id, smoke: smoke.update(
                {"pricing_snapshot_digest": "sha256:" + "e" * 64}
            ),
        ),
        (
            "missing_capability",
            lambda _member_id, smoke: smoke["capability_checks"].pop(
                "timeout_path_verified"
            ),
        ),
        (
            "false_capability",
            lambda _member_id, smoke: smoke["capability_checks"].update(
                {"thinking_breakdown_verified": False}
            ),
        ),
    ),
)
def test_v3_smoke_rejects_legacy_dummy_or_incomplete_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    mutate_smoke: Callable[[str, dict], None],
) -> None:
    preflight = _preflight_from_tracked_v3(
        policy=_policy_module(),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        mutate_smoke=mutate_smoke,
    )

    assert preflight["status"] == "blocked", case_name
    assert preflight["paper_eligible_possible"] is False
    assert preflight["provider_calls_made"] == 0
    assert all(
        plan["blocked_reasons"]
        for plan in preflight["member_plans"].values()
    )


def test_v1_v2_preflight_keep_historical_100_8192_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    for env_name in (
        "TOKENSHARE_GLM_KEY",
        "TOKENSHARE_QWEN_KEY",
        "TOKENSHARE_GPT_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.setenv(env_name, "test-key")

    v1_cohort = policy.load_model_endpoint_cohort(
        _write_cohort(tmp_path / "historical-v1-cohort.json")
    )
    v1_entry_map = policy.load_model_entry_map(
        _write_entry_map(
            tmp_path / "historical-v1-entry-map.json",
            include_gpt=True,
        )
    )
    v1_planned = policy.build_model_endpoint_cohort_preflight(
        cohort=v1_cohort,
        entry_map=v1_entry_map,
        provider_configs=policy.load_provider_config_map(
            _valid_provider_config_paths(tmp_path, max_tokens=8192)
        ),
    )
    assert v1_planned["status"] == "planned"
    assert v1_planned["required_common_controls"]["timeout_seconds"] == 100
    assert v1_planned["required_common_controls"]["max_tokens"] == 8192

    v1_drifted = policy.build_model_endpoint_cohort_preflight(
        cohort=v1_cohort,
        entry_map=v1_entry_map,
        provider_configs=policy.load_provider_config_map(
            _valid_provider_config_paths(tmp_path, max_tokens=512)
        ),
    )
    assert v1_drifted["status"] == "blocked"
    assert v1_drifted["provider_calls_made"] == 0

    v2_cohort = policy.load_model_endpoint_cohort(
        _write_active_cohort(tmp_path / "historical-v2-cohort.json")
    )
    v2_entry_map = policy.load_model_entry_map(
        _write_active_entry_map(tmp_path / "historical-v2-entry-map.json")
    )
    v2_planned = policy.build_model_endpoint_cohort_preflight(
        cohort=v2_cohort,
        entry_map=v2_entry_map,
        provider_configs=policy.load_provider_config_map(
            _active_provider_config_paths(tmp_path, max_tokens=8192)
        ),
    )
    assert v2_planned["status"] == "planned"
    assert v2_planned["required_common_controls"]["timeout_seconds"] == 100
    assert v2_planned["required_common_controls"]["max_tokens"] == 8192

    v2_drifted = policy.build_model_endpoint_cohort_preflight(
        cohort=v2_cohort,
        entry_map=v2_entry_map,
        provider_configs=policy.load_provider_config_map(
            _active_provider_config_paths(tmp_path, max_tokens=4096)
        ),
    )
    assert v2_drifted["status"] == "blocked"
    assert v2_drifted["provider_calls_made"] == 0


def test_paper_condition_accepts_fixed_entry_model_policy_and_records_cohort_fields() -> None:
    condition = _condition(
        domain="factorization",
        difficulty="easy",
        model_policy="fixed_entry",
        model_cohort_id="tokenshare.paper.model_endpoint_cohort.v1",
        cohort_member_id="glm_5_2_siliconflow",
        model_entry_id="glm-entry",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        reasoning_profile_id="default",
        model_cohort_digest="sha256:" + "2" * 64,
    )

    body = condition.to_dict()

    assert body["model_policy"] == "fixed_entry"
    assert body["model_cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v1"
    assert body["cohort_member_id"] == "glm_5_2_siliconflow"
    assert body["model_entry_id"] == "glm-entry"
    assert body["provider_family"] == "siliconflow"
    assert body["provider_model_id"] == "zai-org/GLM-5.2"
    assert body["reasoning_profile_id"] == "default"
    assert body["model_cohort_digest"] == "sha256:" + "2" * 64

    with pytest.raises(ValueError, match="model_policy"):
        _condition(
            domain="factorization",
            difficulty="easy",
            model_policy="strongest_available",
        )


def test_model_endpoint_cohort_preflight_selects_explicit_fixed_entries_and_blocks_incomplete_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    cohort_path = _write_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    sf_config_path = _write_provider_config(
        tmp_path / "siliconflow.json",
        provider_family="siliconflow",
        entries={
            "glm-entry": ("zai-org/GLM-5.2", "TOKENSHARE_GLM_KEY"),
            "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
        },
    )
    openai_config_path = _write_provider_config(
        tmp_path / "openai.json",
        provider_family="openai",
        entries={"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
    )
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    cohort = policy.load_model_endpoint_cohort(cohort_path)
    entry_map = policy.load_model_entry_map(entry_map_path)
    provider_configs = policy.load_provider_config_map(
        {"siliconflow": sf_config_path, "openai": openai_config_path}
    )
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=provider_configs,
    )

    assert preflight["status"] == "planned"
    assert preflight["model_policy"] == "fixed_entry"
    assert preflight["provider_calls_made"] == 0
    assert set(preflight["member_plans"]) == {
        "glm_5_2_siliconflow",
        "qwen3_6_27b_siliconflow",
        "gpt_5_6_sol_high_openai",
    }
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["selected_entry_id"] == (
        "gpt-entry"
    )
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["provider_family"] == "openai"
    assert preflight["member_plans"]["gpt_5_6_sol_high_openai"]["reasoning_profile_id"] == (
        "high"
    )
    gpt_plan = preflight["member_plans"]["gpt_5_6_sol_high_openai"]
    gpt_identity = gpt_plan["endpoint_identity"]
    assert gpt_plan["provider_config_id"] == "openai"
    assert (
        gpt_plan["source_provider_config_digest"]
        == provider_configs["openai"].config_digest
    )
    assert gpt_plan["model_endpoint_identity_digest"].startswith("sha256:")
    assert (
        gpt_plan["model_endpoint_identity_digest"]
        == gpt_identity["model_endpoint_identity_digest"]
    )
    assert gpt_identity["selected_entry_id"] == "gpt-entry"
    assert gpt_identity["effective_reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert "api_key_env" not in gpt_identity

    incomplete = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_entry_map(tmp_path / "entry_map_missing_gpt.json", include_gpt=False)
        ),
        provider_configs=provider_configs,
    )

    assert incomplete["status"] == "blocked"
    assert incomplete["blocked_reason"] == "incomplete_model_cohort"
    assert incomplete["paper_eligible_possible"] is False
    assert incomplete["provider_calls_made"] == 0
    assert "gpt_5_6_sol_high_openai" in incomplete["missing_members"]


def test_exp5_preflight_normalizes_and_compares_public_request_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    for env_name in (
        "TOKENSHARE_GLM_KEY",
        "TOKENSHARE_QWEN_KEY",
        "TOKENSHARE_GPT_KEY",
    ):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(
        _write_cohort(tmp_path / "cohort-controls.json")
    )
    entry_map = policy.load_model_entry_map(
        _write_entry_map(
            tmp_path / "entry-map-controls.json",
            include_gpt=True,
        )
    )
    config_paths = _valid_provider_config_paths(tmp_path)
    configs = policy.load_provider_config_map(config_paths)

    planned = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=configs,
    )

    assert planned["status"] == "planned"
    snapshots = [
        plan["request_controls"]
        for plan in planned["member_plans"].values()
    ]
    assert len(
        {
            json.dumps(
                snapshot["comparable"],
                sort_keys=True,
            )
            for snapshot in snapshots
        }
    ) == 1
    common = snapshots[0]["comparable"]
    assert common["temperature"] == 0.0
    assert common["top_p"] == 0.9
    assert common["stream"] is False
    assert common["timeout_seconds"] == 100
    assert common["max_tokens"] == 8192
    assert common["max_provider_attempts"] == 1
    assert set(common["domain_contracts"]) == {
        "factorization",
        "lean_proof",
    }
    assert planned["member_plans"]["gpt_5_6_sol_high_openai"][
        "request_controls"
    ]["provider_specific_reasoning"] == {"reasoning_effort": "high"}

    siliconflow_body = json.loads(
        config_paths["siliconflow"].read_text(encoding="utf-8")
    )
    next(
        entry
        for entry in siliconflow_body["entries"]
        if entry["entry_id"] == "qwen-entry"
    )["request_overrides"]["top_p"] = 0.8
    config_paths["siliconflow"].write_text(
        json.dumps(siliconflow_body),
        encoding="utf-8",
    )
    drifted = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=policy.load_provider_config_map(config_paths),
    )

    assert drifted["status"] == "blocked"
    assert "cross_member_request_controls_mismatch" in drifted[
        "cohort_level_reasons"
    ]

    timeout_drift_paths = _valid_provider_config_paths(
        tmp_path,
        timeout_seconds=30,
    )
    timeout_drifted = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=entry_map,
        provider_configs=policy.load_provider_config_map(timeout_drift_paths),
    )

    assert timeout_drifted["status"] == "blocked"
    assert all(
        "formal_ai_timeout_seconds_mismatch" in member["blocked_reasons"]
        for member in timeout_drifted["member_plans"].values()
    )


def test_model_endpoint_cohort_preflight_blocks_identity_entry_map_and_smoke_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    for env_name in (
        "TOKENSHARE_GLM_KEY",
        "TOKENSHARE_QWEN_KEY",
        "TOKENSHARE_GPT_KEY",
        "TOKENSHARE_WRONG_PROVIDER_KEY",
    ):
        monkeypatch.setenv(env_name, "test-key")

    def run_preflight(
        *,
        cohort_overrides: dict[str, dict] | None = None,
        entry_map_overrides: dict[str, dict] | None = None,
        provider_configs: dict[str, Path] | None = None,
        entry_map_cohort_id: str = "tokenshare.paper.model_endpoint_cohort.v1",
        include_gpt: bool = True,
        extra_member: bool = False,
        omit_member_id: str | None = None,
    ) -> dict:
        cohort = policy.load_model_endpoint_cohort(
            _write_cohort(
                tmp_path / f"cohort_{len(list(tmp_path.glob('cohort_*.json')))}.json",
                member_overrides=cohort_overrides,
                extra_member=extra_member,
                omit_member_id=omit_member_id,
            )
        )
        entry_map = policy.load_model_entry_map(
            _write_entry_map(
                tmp_path
                / f"entry_map_{len(list(tmp_path.glob('entry_map_*.json')))}.json",
                include_gpt=include_gpt,
                cohort_id=entry_map_cohort_id,
                member_overrides=entry_map_overrides,
            )
        )
        configs = provider_configs or _valid_provider_config_paths(tmp_path)
        return policy.build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=policy.load_provider_config_map(configs),
        )

    wrong_model_configs = _valid_provider_config_paths(
        tmp_path,
        glm_model="wrong/Not-GLM-5.2",
    )
    assert _blocked(
        run_preflight(
            cohort_overrides={
                "glm_5_2_siliconflow": {"provider_model_id": "wrong/Not-GLM-5.2"}
            },
            provider_configs=wrong_model_configs,
        )
    )

    wrong_provider_configs = _valid_provider_config_paths(
        tmp_path,
        openai_entries={
            "gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY"),
            "glm-openai-entry": (
                "zai-org/GLM-5.2",
                "TOKENSHARE_WRONG_PROVIDER_KEY",
            ),
        },
    )
    assert _blocked(
        run_preflight(
            cohort_overrides={
                "glm_5_2_siliconflow": {"provider_family": "openai"}
            },
            entry_map_overrides={
                "glm_5_2_siliconflow": {
                    "provider_config_id": "openai",
                    "entry_id": "glm-openai-entry",
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="glm_5_2_siliconflow",
                        entry_id="glm-openai-entry",
                        provider_family="openai",
                        provider_model_id="zai-org/GLM-5.2",
                        reasoning_profile_id="default",
                    ),
                }
            },
            provider_configs=wrong_provider_configs,
        )
    )

    assert _blocked(
        run_preflight(
            cohort_overrides={
                "gpt_5_6_sol_high_openai": {"reasoning_profile_id": "low"}
            },
            entry_map_overrides={
                "gpt_5_6_sol_high_openai": {
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="gpt_5_6_sol_high_openai",
                        entry_id="gpt-entry",
                        provider_family="openai",
                        provider_model_id="gpt-5.6-sol",
                        reasoning_profile_id="low",
                    )
                }
            },
        )
    )

    assert _blocked(run_preflight(entry_map_cohort_id="wrong.cohort"))
    assert _blocked(
        run_preflight(
            entry_map_overrides={
                "glm_5_2_siliconflow": {"smoke_evidence_ref": None}
            }
        )
    )
    assert _blocked(
        run_preflight(
            entry_map_overrides={
                "glm_5_2_siliconflow": {
                    "smoke_evidence_ref": _smoke_evidence(
                        member_id="glm_5_2_siliconflow",
                        entry_id="glm-entry",
                        provider_family="openai",
                        provider_model_id="zai-org/GLM-5.2",
                        reasoning_profile_id="default",
                    )
                }
            }
        )
    )
    assert _blocked(run_preflight(extra_member=True))
    missing = run_preflight(omit_member_id="qwen3_6_27b_siliconflow")
    assert _blocked(missing)
    assert missing["provider_calls_made"] == 0


def test_expand_plan_conditions_uses_exp5_preflight_fixed_entries_without_breaking_exp1_exp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "DEEPSEEK_API_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(_write_active_cohort(tmp_path / "cohort.json"))
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_active_entry_map(tmp_path / "entry_map.json", include_gpt=True)
        ),
        provider_configs=policy.load_provider_config_map(
            _active_provider_config_paths(tmp_path)
        ),
    )

    exp5_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert len(exp5_conditions) == 18
    assert {condition.model_policy for condition in exp5_conditions} == {"fixed_entry"}
    assert {condition.experiment_id for condition in exp5_conditions} == {
        "exp5_real_ai_model_endpoint_comparison"
    }
    assert {condition.cohort_member_id for condition in exp5_conditions} == {
        "glm_5_2_siliconflow",
        "deepseek_v4_pro_deepseek",
        "gpt_5_6_sol_high_openai",
    }
    assert {condition.provider_family for condition in exp5_conditions} == {
        "siliconflow",
        "deepseek",
        "openai",
    }
    entry_by_member = {
        member_id: plan["selected_entry_id"]
        for member_id, plan in preflight["member_plans"].items()
    }
    assert all(
        condition.model_entry_id == entry_by_member[condition.cohort_member_id]
        for condition in exp5_conditions
    )
    plan_by_member = preflight["member_plans"]
    assert all(
        condition.provider_config_id
        == plan_by_member[condition.cohort_member_id]["provider_config_id"]
        and condition.source_provider_config_digest
        == plan_by_member[condition.cohort_member_id][
            "source_provider_config_digest"
        ]
        and condition.model_endpoint_identity_digest
        == plan_by_member[condition.cohort_member_id][
            "model_endpoint_identity_digest"
        ]
        for condition in exp5_conditions
    )

    core_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=(
            "exp1_real_ai_feasibility",
            "exp4_real_ai_protocol_ablation",
        ),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert core_conditions
    assert {condition.model_policy for condition in core_conditions} == {"fixed_entry"}
    assert all(
        condition.provider_config_id is None
        and condition.source_provider_config_digest is None
        and condition.model_endpoint_identity_digest is None
        for condition in core_conditions
    )
    assert "exp5_real_ai_model_endpoint_comparison" not in {
        condition.experiment_id for condition in core_conditions
    }


def test_source_config_drift_changes_condition_identity_without_changing_endpoint_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "DEEPSEEK_API_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(_write_active_cohort(tmp_path / "cohort.json"))
    entry_map = policy.load_model_entry_map(
        _write_active_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    )
    original_paths = _active_provider_config_paths(
        tmp_path,
        openai_metadata={"approval": "original"},
    )
    changed_paths = _active_provider_config_paths(
        tmp_path,
        openai_metadata={"approval": "changed-after-planning"},
    )

    def build_preflight(openai_path: Path) -> dict:
        return policy.build_model_endpoint_cohort_preflight(
            cohort=cohort,
            entry_map=entry_map,
            provider_configs=policy.load_provider_config_map(
                {
                    "siliconflow": original_paths["siliconflow"],
                    "deepseek": original_paths["deepseek"],
                    "openai": openai_path,
                }
            ),
        )

    original = build_preflight(original_paths["openai"])
    changed = build_preflight(changed_paths["openai"])
    original_gpt = original["member_plans"]["gpt_5_6_sol_high_openai"]
    changed_gpt = changed["member_plans"]["gpt_5_6_sol_high_openai"]

    assert original_gpt["model_endpoint_identity_digest"] == changed_gpt[
        "model_endpoint_identity_digest"
    ]
    assert original_gpt["source_provider_config_digest"] != changed_gpt[
        "source_provider_config_digest"
    ]

    original_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=original,
    )
    changed_conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=changed,
    )
    original_gpt_condition = next(
        condition
        for condition in original_conditions
        if condition.cohort_member_id == "gpt_5_6_sol_high_openai"
    )
    changed_gpt_condition = next(
        condition
        for condition in changed_conditions
        if condition.cohort_member_id == "gpt_5_6_sol_high_openai"
    )

    assert original_gpt_condition.condition_digest != changed_gpt_condition.condition_digest


def test_exp5_blocked_cohort_does_not_expand_extra_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy_module()
    catalog = _catalog()
    for env_name in ("TOKENSHARE_GLM_KEY", "TOKENSHARE_QWEN_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")
    cohort = policy.load_model_endpoint_cohort(
        _write_cohort(tmp_path / "cohort_extra.json", extra_member=True)
    )
    preflight = policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(
            _write_entry_map(tmp_path / "entry_map.json", include_gpt=True)
        ),
        provider_configs=policy.load_provider_config_map(
            _valid_provider_config_paths(tmp_path)
        ),
    )

    conditions = expand_plan_conditions(
        catalog_manifest=catalog,
        experiment_ids=("exp5_real_ai_model_endpoint_comparison",),
        worker_levels=(10,),
        repeats=1,
        seed_family=(1,),
        model_endpoint_cohort_preflight=preflight,
    )

    assert preflight["status"] == "blocked"
    assert preflight["blocked_reason"] == "incomplete_model_cohort"
    assert preflight["provider_calls_made"] == 0
    assert len(conditions) == 0


def test_cli_plan_only_exp5_writes_fixed_endpoint_cohort_evidence_without_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort_path = _write_active_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_active_entry_map(tmp_path / "entry_map.json", include_gpt=True)
    config_paths = _active_provider_config_paths(tmp_path)
    for env_name in ("TOKENSHARE_GLM_KEY", "DEEPSEEK_API_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    exit_code = main(
        [
            "--output-root",
            str(tmp_path / "out"),
            "--experiments",
            "exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={config_paths['siliconflow']}",
            "--provider-config",
            f"deepseek={config_paths['deepseek']}",
            "--provider-config",
            f"openai={config_paths['openai']}",
        ]
    )

    output_root = tmp_path / "out"
    suite = json.loads((output_root / "suite_manifest.json").read_text(encoding="utf-8"))
    budget = json.loads((output_root / "run_budget.json").read_text(encoding="utf-8"))
    cohort_plan = json.loads(
        (output_root / "model_endpoint_cohort_plan.json").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert suite["status"] == "planned"
    assert suite["model_endpoint_cohort_preflight"]["status"] == "planned"
    assert suite["model_endpoint_cohort_preflight"]["model_policy"] == "fixed_entry"
    assert suite["model_policy_preflight"] is None
    assert budget["quota_preflight"]["provider_calls_made"] == 0
    assert budget["quota_preflight"]["model_endpoint_cohort_preflight"]["provider_calls_made"] == 0
    assert cohort_plan["status"] == "planned"
    assert set(cohort_plan["member_plans"]) == {
        "glm_5_2_siliconflow",
        "deepseek_v4_pro_deepseek",
        "gpt_5_6_sol_high_openai",
    }
    assert suite["condition_count"] == 36
    assert suite["run_count"] == 1_899
    assert suite["task_count"] == 1_899
    assert budget["planned_conditions"] == 36
    assert budget["planned_root_runs"] == 1_899
    assert budget["planned_ai_units"] == 14_652
    assert cohort_plan["request_controls_snapshot_digest"].startswith("sha256:")


def test_cli_exp5_missing_member_is_structured_blocked_but_exp1_plan_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort_path = _write_active_cohort(tmp_path / "cohort.json")
    entry_map_path = _write_active_entry_map(
        tmp_path / "entry_map_missing_gpt.json", include_gpt=False
    )
    config_paths = _active_provider_config_paths(tmp_path)
    for env_name in ("TOKENSHARE_GLM_KEY", "DEEPSEEK_API_KEY", "TOKENSHARE_GPT_KEY"):
        monkeypatch.setenv(env_name, "test-key")

    exp5_exit = main(
        [
            "--output-root",
            str(tmp_path / "exp5"),
            "--experiments",
            "exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={config_paths['siliconflow']}",
            "--provider-config",
            f"deepseek={config_paths['deepseek']}",
            "--provider-config",
            f"openai={config_paths['openai']}",
        ]
    )
    exp5_plan = json.loads(
        (tmp_path / "exp5" / "model_endpoint_cohort_plan.json").read_text(encoding="utf-8")
    )
    exp5_suite = json.loads(
        (tmp_path / "exp5" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    exp5_dispatch = json.loads(
        (tmp_path / "exp5" / "paper_dispatch_plans.json").read_text(encoding="utf-8")
    )

    assert exp5_exit == 0
    assert exp5_suite["status"] == "blocked"
    assert exp5_plan["status"] == "blocked"
    assert exp5_plan["blocked_reason"] == "incomplete_model_cohort"
    assert exp5_plan["paper_eligible_possible"] is False
    assert exp5_plan["provider_calls_made"] == 0
    assert exp5_dispatch["plans"][0]["status"] == "blocked"
    assert exp5_dispatch["plans"][0]["blocked_reason"] == "incomplete_model_cohort"
    assert exp5_dispatch["plans"][0]["paper_eligible_possible"] is False

    exp1_exit = main(
        [
            "--output-root",
            str(tmp_path / "exp1"),
            "--experiments",
            "exp1",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={config_paths['siliconflow']}",
            "--provider-config",
            f"deepseek={config_paths['deepseek']}",
            "--provider-config",
            f"openai={config_paths['openai']}",
        ]
    )
    exp1_suite = json.loads(
        (tmp_path / "exp1" / "suite_manifest.json").read_text(encoding="utf-8")
    )

    assert exp1_exit == 0
    assert exp1_suite["status"] == "planned"
    assert exp1_suite["condition_count"] > 0
    assert exp1_suite["model_endpoint_cohort_preflight"] is None

    combined_exit = main(
        [
            "--output-root",
            str(tmp_path / "combined"),
            "--experiments",
            "exp1,exp5",
            "--plan-only",
            "--model-cohort-file",
            str(cohort_path),
            "--model-entry-map",
            str(entry_map_path),
            "--provider-config",
            f"siliconflow={config_paths['siliconflow']}",
            "--provider-config",
            f"deepseek={config_paths['deepseek']}",
            "--provider-config",
            f"openai={config_paths['openai']}",
        ]
    )
    combined_suite = json.loads(
        (tmp_path / "combined" / "suite_manifest.json").read_text(encoding="utf-8")
    )
    combined_dispatch = json.loads(
        (tmp_path / "combined" / "paper_dispatch_plans.json").read_text(
            encoding="utf-8"
        )
    )

    assert combined_exit == 0
    assert combined_suite["status"] == "planned"
    assert combined_suite["condition_count"] == 12
    assert [plan["status"] for plan in combined_dispatch["plans"]] == [
        "planned",
        "blocked",
    ]
    assert combined_dispatch["plans"][1]["blocked_reason"] == "incomplete_model_cohort"
    assert combined_dispatch["provider_calls_made"] == 0


def test_formal_exp5_schema_and_cli_reject_legacy_strong_weak_mixed(
    tmp_path: Path,
) -> None:
    for legacy_policy in ("strong_only", "weak_only", "mixed"):
        with pytest.raises(ValueError, match="model_policy"):
            _condition(
                domain="factorization",
                difficulty="easy",
                model_policy=legacy_policy,
            )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--output-root",
                str(tmp_path / "out"),
                "--experiments",
                "exp5",
                "--plan-only",
                "--model-policies",
                "mixed",
            ]
        )
    assert exc.value.code == 2


def test_factorization_and_lean_adapters_keep_attempt_model_identity_records(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    factor_case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    lean_case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]

    factor_result = run_factorization_paper_case(
        case=factor_case,
        condition=_condition(
            domain="factorization",
            difficulty="easy",
            model_policy="fixed_entry",
            catalog_digest=catalog.catalog_digest,
            model_entry_id="glm-entry",
        ),
        output_root=tmp_path / "factorization",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    lean_result = run_lean_paper_case(
        case=lean_case,
        condition=_condition(
            domain="lean_proof",
            difficulty="easy",
            model_policy="fixed_entry",
            catalog_digest=catalog.catalog_digest,
            model_entry_id="gpt-entry",
            paper_difficulty=lean_case["paper_difficulty"],
            topic_family=lean_case["topic_family"],
            topic_family_version=lean_case["topic_family_version"],
            construction_rule_id=lean_case.get("construction_rule_id"),
            oracle_package_group=lean_case.get("oracle_package_group"),
            proof_assembly_shape=lean_case.get("proof_assembly_shape"),
        ),
        output_root=tmp_path / "lean",
        transport=ScriptedLeanPaperProofTransport(),
        real_transport=False,
    )

    factor_attempt = factor_result.attempt_results[0]
    lean_attempt = lean_result.attempt_results[0]
    assert factor_attempt.entry_id == "glm-entry"
    assert factor_attempt.provider == "siliconflow"
    assert factor_attempt.model == "TokenShare/Scripted-Factorization-Range"
    assert lean_attempt.entry_id == "gpt-entry"
    assert lean_attempt.provider == "siliconflow"
    assert lean_attempt.model == "TokenShare/Scripted-Lean-Paper-Prover"

    with pytest.raises(ValueError, match="model_entry_id"):
        run_factorization_paper_case(
            case=factor_case,
            condition=_condition(
                domain="factorization",
                difficulty="easy",
                model_policy="fixed_entry",
                catalog_digest=catalog.catalog_digest,
                model_entry_id="glm-entry",
            ),
            output_root=tmp_path / "factorization_mismatch",
            transport=ScriptedFactorizationRangeTransport(),
            real_transport=False,
            entry_id="other-entry",
        )


def _policy_module():
    module_name = "tokenshare.experiments.paper_model_policy"
    assert importlib.util.find_spec(module_name) is not None
    return importlib.import_module(module_name)


def _condition(
    *,
    domain: str,
    difficulty: str,
    model_policy: str,
    catalog_digest: str = "sha256:" + "1" * 64,
    model_cohort_id: str | None = None,
    cohort_member_id: str | None = None,
    model_entry_id: str | None = None,
    provider_family: str | None = None,
    provider_model_id: str | None = None,
    reasoning_profile_id: str | None = None,
    model_cohort_digest: str | None = None,
    paper_difficulty: str | None = None,
    topic_family: str | None = None,
    topic_family_version: str | None = None,
    construction_rule_id: str | None = None,
    oracle_package_group: str | None = None,
    proof_assembly_shape: str | None = None,
) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"cond_{domain}_{difficulty}_{model_policy}",
        domain=domain,
        difficulty=difficulty,
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy=model_policy,
        model_cohort_id=model_cohort_id,
        cohort_member_id=cohort_member_id,
        model_entry_id=model_entry_id,
        provider_family=provider_family,
        provider_model_id=provider_model_id,
        reasoning_profile_id=reasoning_profile_id,
        model_cohort_digest=model_cohort_digest,
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
        paper_difficulty=paper_difficulty,
        topic_family=topic_family,
        topic_family_version=topic_family_version,
        construction_rule_id=construction_rule_id,
        oracle_package_group=oracle_package_group,
        proof_assembly_shape=proof_assembly_shape,
    )


def _catalog():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )


def _write_cohort(
    path: Path,
    *,
    member_overrides: dict[str, dict] | None = None,
    extra_member: bool = False,
    omit_member_id: str | None = None,
) -> Path:
    members = [
        member
        for member in _cohort_members(member_overrides=member_overrides)
        if member["cohort_member_id"] != omit_member_id
    ]
    if extra_member:
        members.append(
            {
                "cohort_member_id": "extra_endpoint_not_in_design",
                "provider_family": "openai",
                "provider_model_id": "not-authorized",
                "reasoning_profile_id": "default",
            }
        )
    body = {
        "schema_version": "tokenshare.paper_model_endpoint_cohort.v1",
        "cohort_id": "tokenshare.paper.model_endpoint_cohort.v1",
        "members": members,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _cohort_members(*, member_overrides: dict[str, dict] | None = None) -> list[dict]:
    overrides = member_overrides or {}
    members = [
        {
            "cohort_member_id": "glm_5_2_siliconflow",
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "reasoning_profile_id": "default",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 51,
            "benchmark_variant": "max",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
        {
            "cohort_member_id": "qwen3_6_27b_siliconflow",
            "provider_family": "siliconflow",
            "provider_model_id": "Qwen/Qwen3.6-27B",
            "reasoning_profile_id": "default",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 37,
            "benchmark_variant": "reasoning",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
        {
            "cohort_member_id": "gpt_5_6_sol_high_openai",
            "provider_family": "openai",
            "provider_model_id": "gpt-5.6-sol",
            "reasoning_profile_id": "high",
            "external_benchmark_source": "Artificial Analysis Intelligence Index",
            "index_version": "v4.1",
            "index_score": 56,
            "benchmark_variant": "high",
            "benchmark_match_status": "exact_or_provider_listing",
            "source_url": "https://artificialanalysis.ai/",
            "observed_at": "2026-07-16",
        },
    ]
    for member in members:
        member.update(overrides.get(member["cohort_member_id"], {}))
    return members


def _write_entry_map(
    path: Path,
    *,
    include_gpt: bool,
    cohort_id: str = "tokenshare.paper.model_endpoint_cohort.v1",
    member_overrides: dict[str, dict] | None = None,
) -> Path:
    members = {
        "glm_5_2_siliconflow": {
            "provider_config_id": "siliconflow",
            "entry_id": "glm-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="glm_5_2_siliconflow",
                entry_id="glm-entry",
                provider_family="siliconflow",
                provider_model_id="zai-org/GLM-5.2",
                reasoning_profile_id="default",
            ),
        },
        "qwen3_6_27b_siliconflow": {
            "provider_config_id": "siliconflow",
            "entry_id": "qwen-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="qwen3_6_27b_siliconflow",
                entry_id="qwen-entry",
                provider_family="siliconflow",
                provider_model_id="Qwen/Qwen3.6-27B",
                reasoning_profile_id="default",
            ),
        },
    }
    if include_gpt:
        members["gpt_5_6_sol_high_openai"] = {
            "provider_config_id": "openai",
            "entry_id": "gpt-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="gpt_5_6_sol_high_openai",
                entry_id="gpt-entry",
                provider_family="openai",
                provider_model_id="gpt-5.6-sol",
                reasoning_profile_id="high",
            ),
        }
    for member_id, override in (member_overrides or {}).items():
        if member_id in members:
            members[member_id].update(override)
    body = {
        "schema_version": "tokenshare.paper_model_entry_map.v1",
        "cohort_id": cohort_id,
        "members": members,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _smoke_evidence(
    *,
    member_id: str,
    entry_id: str,
    provider_family: str,
    provider_model_id: str,
    reasoning_profile_id: str,
) -> dict:
    return {
        "schema_version": "tokenshare.paper_model_endpoint_smoke_evidence.v1",
        "status": "passed",
        "cohort_member_id": member_id,
        "entry_id": entry_id,
        "provider_family": provider_family,
        "provider_model_id": provider_model_id,
        "reasoning_profile_id": reasoning_profile_id,
        "provider_attempt_count": 1,
        "raw_output_ref": {"artifact_id": f"raw_{entry_id}", "content_hash": "sha256:" + "3" * 64},
        "provenance_ref": {
            "artifact_id": f"provenance_{entry_id}",
            "content_hash": "sha256:" + "4" * 64,
        },
        "usage_ref": {"artifact_id": f"usage_{entry_id}", "content_hash": "sha256:" + "5" * 64},
    }


def _blocked(preflight: dict) -> bool:
    assert preflight["provider_calls_made"] == 0
    assert preflight["paper_eligible_possible"] is False
    assert preflight["blocked_reason"] == "incomplete_model_cohort"
    return preflight["status"] == "blocked"


def _valid_provider_config_paths(
    tmp_path: Path,
    *,
    glm_model: str = "zai-org/GLM-5.2",
    openai_entries: dict[str, tuple[str, str]] | None = None,
    timeout_seconds: int = 100,
    max_tokens: int = 8192,
) -> dict[str, Path]:
    return {
        "siliconflow": _write_provider_config(
            tmp_path / f"siliconflow_{len(list(tmp_path.glob('siliconflow_*.json')))}.json",
            provider_family="siliconflow",
            entries={
                "glm-entry": (glm_model, "TOKENSHARE_GLM_KEY"),
                "qwen-entry": ("Qwen/Qwen3.6-27B", "TOKENSHARE_QWEN_KEY"),
            },
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        ),
        "openai": _write_provider_config(
            tmp_path / f"openai_{len(list(tmp_path.glob('openai_*.json')))}.json",
            provider_family="openai",
            entries=openai_entries
            or {"gpt-entry": ("gpt-5.6-sol", "TOKENSHARE_GPT_KEY")},
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        ),
    }


def _write_provider_config(
    path: Path,
    *,
    provider_family: str,
    entries: dict[str, tuple[str, str]],
    metadata: dict | None = None,
    timeout_seconds: int = 100,
    max_tokens: int = 8192,
) -> Path:
    body = {
        "schema_version": "phase7.ai_api_executor_config.v1",
        "executor_id": "executor_ai_api",
        "provider_family": provider_family,
        "selection_policy": {
            "kind": "uniform_random_without_weights",
            "seed_source": "request_or_environment_seed",
        },
        "defaults": {
            "timeout_seconds": timeout_seconds,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "top_p": 0.9,
            "stream": False,
            "max_provider_attempts": 1,
        },
        "entries": [
            {
                "entry_id": entry_id,
                "enabled": True,
                "base_url": (
                    "https://api.openai.com/v1"
                    if provider_family == "openai"
                    else "https://api.siliconflow.cn/v1"
                ),
                "api_key_env": api_key_env,
                "model": model,
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": (
                    {"temperature": 0.0, "reasoning_effort": "high"}
                    if provider_family == "openai"
                    else {"temperature": 0.0}
                ),
                "pricing": {
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                "tags": ["paper", provider_family],
            }
            for entry_id, (model, api_key_env) in entries.items()
        ],
        "local_concurrency": {"max_in_flight_global": 1},
        "metadata": metadata or {"purpose": "paper-model-endpoint-test"},
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_active_cohort(path: Path) -> Path:
    source = Path("benchmarks/paper/model_comparison_cohort.v2.json")
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _write_active_entry_map(path: Path, *, include_gpt: bool = True) -> Path:
    members = {
        "glm_5_2_siliconflow": {
            "provider_config_id": "siliconflow",
            "entry_id": "glm-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="glm_5_2_siliconflow",
                entry_id="glm-entry",
                provider_family="siliconflow",
                provider_model_id="zai-org/GLM-5.2",
                reasoning_profile_id="thinking",
            ),
        },
        "deepseek_v4_pro_deepseek": {
            "provider_config_id": "deepseek",
            "entry_id": "deepseek-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="deepseek_v4_pro_deepseek",
                entry_id="deepseek-entry",
                provider_family="deepseek",
                provider_model_id="deepseek-v4-pro",
                reasoning_profile_id="high",
            ),
        },
    }
    if include_gpt:
        members["gpt_5_6_sol_high_openai"] = {
            "provider_config_id": "openai",
            "entry_id": "gpt-entry",
            "smoke_evidence_ref": _smoke_evidence(
                member_id="gpt_5_6_sol_high_openai",
                entry_id="gpt-entry",
                provider_family="openai",
                provider_model_id="gpt-5.6-sol",
                reasoning_profile_id="high",
            ),
        }
    body = {
        "schema_version": "tokenshare.paper_model_entry_map.v1",
        "cohort_id": "tokenshare.paper.model_endpoint_cohort.v2",
        "members": members,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _active_provider_config_paths(
    tmp_path: Path,
    *,
    openai_metadata: dict | None = None,
    max_tokens: int = 8192,
) -> dict[str, Path]:
    return {
        "siliconflow": _write_active_provider_config(
            tmp_path / f"active_sf_{len(list(tmp_path.glob('active_sf_*.json')))}.json",
            provider_family="siliconflow",
            entry_id="glm-entry",
            model="zai-org/GLM-5.2",
            api_key_env="TOKENSHARE_GLM_KEY",
            max_tokens=max_tokens,
        ),
        "deepseek": _write_active_provider_config(
            tmp_path / f"active_ds_{len(list(tmp_path.glob('active_ds_*.json')))}.json",
            provider_family="deepseek",
            entry_id="deepseek-entry",
            model="deepseek-v4-pro",
            api_key_env="DEEPSEEK_API_KEY",
            max_tokens=max_tokens,
        ),
        "openai": _write_active_provider_config(
            tmp_path / f"active_oa_{len(list(tmp_path.glob('active_oa_*.json')))}.json",
            provider_family="openai",
            entry_id="gpt-entry",
            model="gpt-5.6-sol",
            api_key_env="TOKENSHARE_GPT_KEY",
            metadata=openai_metadata,
            max_tokens=max_tokens,
        ),
    }


def _write_active_provider_config(
    path: Path,
    *,
    provider_family: str,
    entry_id: str,
    model: str,
    api_key_env: str,
    metadata: dict | None = None,
    max_tokens: int = 8192,
) -> Path:
    overrides = {
        "siliconflow": {"enable_thinking": True},
        "deepseek": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        "openai": {"reasoning_effort": "high"},
    }[provider_family]
    base_url = {
        "siliconflow": "https://api.siliconflow.cn/v1",
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
    }[provider_family]
    pricing = (
        {
            "currency": "CNY",
            "cached_input_per_million_tokens": 0.025,
            "uncached_input_per_million_tokens": 3.0,
            "output_per_million_tokens": 6.0,
        }
        if provider_family == "deepseek"
        else {
            "currency": "USD",
            "input_per_million_tokens": 1.0,
            "output_per_million_tokens": 2.0,
        }
    )
    body = {
        "schema_version": "phase7.ai_api_executor_config.v1",
        "executor_id": f"executor_{provider_family}_active_test",
        "provider_family": provider_family,
        "selection_policy": {
            "kind": "uniform_random_without_weights",
            "seed_source": "request_or_environment_seed",
        },
        "defaults": {
            "timeout_seconds": 100,
            "max_tokens": max_tokens,
            "stream": False,
            "max_provider_attempts": 1,
        },
        "entries": [{
            "entry_id": entry_id,
            "enabled": True,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "model": model,
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": overrides,
            "pricing": pricing,
            "tags": ["paper", provider_family],
        }],
        "local_concurrency": {"max_in_flight_global": 50},
        "metadata": metadata or {"purpose": "active-paper-model-endpoint-test"},
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _load_json(path: Path) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


def _preflight_from_tracked_v3(
    *,
    policy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate_cohort: Callable[[dict], None] | None = None,
    mutate_config: Callable[[dict], None] | None = None,
    mutate_smoke: Callable[[str, dict], None] | None = None,
    set_key: bool = True,
) -> dict:
    cohort_body = _load_json(V3_COHORT_PATH)
    if mutate_cohort is not None:
        mutate_cohort(cohort_body)
    cohort_path = tmp_path / f"cohort-{len(list(tmp_path.glob('cohort-*.json')))}.json"
    cohort_path.write_text(
        json.dumps(cohort_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cohort = policy.load_model_endpoint_cohort(cohort_path)
    smoke_config = policy.load_provider_config_map(
        {"siliconflow": V3_PROVIDER_CONFIG_PATH}
    )["siliconflow"]
    entry_map_body = _load_json(V3_ENTRY_MAP_PATH)
    cohort_members = {
        member["cohort_member_id"]: member for member in cohort["members"]
    }
    for member_id, spec in entry_map_body["members"].items():
        member = cohort_members[member_id]
        smoke = _v3_smoke_evidence(
            policy=policy,
            cohort=cohort,
            config=smoke_config,
            member=member,
            entry_id=spec["entry_id"],
        )
        if mutate_smoke is not None:
            mutate_smoke(member_id, smoke)
        spec["smoke_evidence_ref"] = smoke
    entry_map_path = tmp_path / f"entry-map-{len(list(tmp_path.glob('entry-map-*.json')))}.json"
    entry_map_path.write_text(
        json.dumps(entry_map_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    config_body = _load_json(V3_PROVIDER_CONFIG_PATH)
    if mutate_config is not None:
        mutate_config(config_body)
    config_path = tmp_path / f"provider-config-{len(list(tmp_path.glob('provider-config-*.json')))}.json"
    config_path.write_text(
        json.dumps(config_body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    provider_configs = policy.load_provider_config_map(
        {"siliconflow": config_path}
    )
    if set_key:
        monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    else:
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    return policy.build_model_endpoint_cohort_preflight(
        cohort=cohort,
        entry_map=policy.load_model_entry_map(entry_map_path),
        provider_configs=provider_configs,
    )


def _v3_smoke_evidence(
    *,
    policy,
    cohort: dict,
    config,
    member: dict,
    entry_id: str,
) -> dict:
    identity = policy.build_model_endpoint_identity(
        model_cohort_id=cohort["cohort_id"],
        model_cohort_digest=cohort["model_cohort_digest"],
        cohort_member_id=member["cohort_member_id"],
        provider_config_id="siliconflow",
        selected_entry_id=entry_id,
        expected_provider_family=member["provider_family"],
        expected_provider_model_id=member["provider_model_id"],
        expected_reasoning_profile_id=member["reasoning_profile_id"],
        source_config=config,
    )
    entry = next(item for item in config.entries if item.entry_id == entry_id)
    controls = policy._normalized_exp5_request_controls(
        config=config,
        selected_entry=entry,
        endpoint_identity=identity,
        comparable_fields=tuple(
            policy._COHORT_DEFINITIONS[cohort["cohort_id"]][
                "comparable_fields"
            ]
        ),
    )
    request_controls_digest = policy.digest_json(
        {
            "comparable": controls["comparable"],
            "provider_specific_reasoning": controls[
                "provider_specific_reasoning"
            ],
        }
    )
    return {
        "schema_version": "tokenshare.paper_model_endpoint_smoke_evidence.v2",
        "status": "passed",
        "cohort_member_id": member["cohort_member_id"],
        "entry_id": entry_id,
        "provider_family": member["provider_family"],
        "provider_model_id": member["provider_model_id"],
        "reasoning_profile_id": member["reasoning_profile_id"],
        "model_cohort_id": cohort["cohort_id"],
        "model_cohort_digest": cohort["model_cohort_digest"],
        "source_provider_config_digest": config.config_digest,
        "model_endpoint_identity_digest": identity.model_endpoint_identity_digest,
        "request_controls_digest": request_controls_digest,
        "pricing_snapshot_digest": policy.digest_json(dict(entry.pricing)),
        "capability_checks": {
            "resolved_model_match": True,
            "request_controls_match": True,
            "usage_schema_verified": True,
            "thinking_breakdown_verified": True,
            "timeout_path_verified": True,
        },
        "provider_attempt_count": 1,
        "raw_output_ref": {
            "artifact_id": f"raw_{entry_id}",
            "content_hash": "sha256:" + "3" * 64,
        },
        "provenance_ref": {
            "artifact_id": f"provenance_{entry_id}",
            "content_hash": "sha256:" + "4" * 64,
        },
        "usage_ref": {
            "artifact_id": f"usage_{entry_id}",
            "content_hash": "sha256:" + "5" * 64,
        },
    }
