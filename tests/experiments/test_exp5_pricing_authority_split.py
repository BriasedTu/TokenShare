from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, AIAPIProviderEntry
from tokenshare.experiments import paper_formal_runner
from tokenshare.experiments import run_paper_experiments
from tokenshare.experiments.paper_model_identity import build_model_endpoint_identity


def _config(*, pricing: dict[str, object], model: str = "model-v3") -> AIAPIExecutorConfig:
    return AIAPIExecutorConfig(
        schema_version="phase7.ai_api_executor_config.v1",
        executor_id="siliconflow-v3",
        provider_family="siliconflow",
        selection_policy={"kind": "fixed"},
        defaults={"max_tokens": 32_768, "timeout_seconds": 600},
        entries=[
            AIAPIProviderEntry(
                entry_id="entry-v3",
                enabled=True,
                base_url="https://api.siliconflow.cn/v1",
                api_key_env="SILICONFLOW_API_KEY",
                model=model,
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={"enable_thinking": True, "thinking_budget": 32_768},
                pricing=pricing,
                tags=["exp5"],
            )
        ],
        local_concurrency={"max_in_flight_global": 1},
        metadata={"cohort": "v3"},
    )


def _condition(*, source_digest: str, model: str = "model-v3", max_tokens: int = 32_768):
    return SimpleNamespace(
        provider_config_id="siliconflow",
        provider_family="siliconflow",
        provider_model_id=model,
        model_entry_id="entry-v3",
        reasoning_profile_id="thinking",
        source_provider_config_digest=source_digest,
        model_endpoint_identity_digest="sha256:" + "0" * 64,
        model_cohort_id="cohort-v3",
        model_cohort_digest="sha256:" + "1" * 64,
        cohort_member_id="member-v3",
        endpoint_controls=SimpleNamespace(max_tokens=max_tokens),
    )


def _approved_binding(config: AIAPIExecutorConfig, condition) -> dict[str, object]:
    # Endpoint identity digest is filled from the formal binding helper so the
    # test isolates pricing drift rather than an unrelated forged identity.
    identity = build_model_endpoint_identity(
        model_cohort_id=condition.model_cohort_id,
        model_cohort_digest=condition.model_cohort_digest,
        cohort_member_id=condition.cohort_member_id,
        provider_config_id=condition.provider_config_id,
        selected_entry_id=condition.model_entry_id,
        expected_provider_family=condition.provider_family,
        expected_provider_model_id=condition.provider_model_id,
        expected_reasoning_profile_id=condition.reasoning_profile_id,
        source_config=config,
    )
    condition.model_endpoint_identity_digest = identity.model_endpoint_identity_digest
    comparable = {
        "max_tokens": 32_768,
        "timeout_seconds": 600,
        "domain_contracts": {
            domain: dict(contract)
            for domain, contract in paper_formal_runner.EXP5_DOMAIN_EXECUTION_CONTRACTS.items()
        },
    }
    member_controls = {
        "comparable": comparable,
        "comparable_digest": paper_formal_runner.digest_json(
            comparable
        ),
        "provider_specific_reasoning": {
            "enable_thinking": True,
            "thinking_budget": 32_768,
        },
        "provider_specific_reasoning_digest": paper_formal_runner.digest_json(
            {"enable_thinking": True, "thinking_budget": 32_768}
        ),
    }
    return {
        "status": "planned",
        "cohort_id": condition.model_cohort_id,
        "model_cohort_digest": condition.model_cohort_digest,
        "member_plans": {
            condition.cohort_member_id: {
                "cohort_id": condition.model_cohort_id,
                "model_cohort_digest": condition.model_cohort_digest,
                "cohort_member_id": condition.cohort_member_id,
                "provider_config_id": condition.provider_config_id,
                "selected_entry_id": condition.model_entry_id,
                "provider_family": condition.provider_family,
                "provider_model_id": condition.provider_model_id,
                "reasoning_profile_id": condition.reasoning_profile_id,
                "source_provider_config_digest": config.config_digest,
                "model_endpoint_identity_digest": identity.model_endpoint_identity_digest,
                "request_controls": member_controls,
            }
        },
        "comparable_request_control_fields": ["max_tokens", "timeout_seconds"],
        "request_controls_snapshot": member_controls["comparable"],
        "request_controls_snapshot_digest": member_controls["comparable_digest"],
    }


def test_exp5_accepts_pricing_only_refresh_against_frozen_source_digest() -> None:
    frozen_config = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0}
    )
    refreshed_config = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 0.5, "output_per_million_tokens": 2.0}
    )
    condition = _condition(source_digest=frozen_config.config_digest)
    binding = _approved_binding(refreshed_config, condition)
    # The current config is allowed to refresh pricing while preserving the
    # frozen source digest carried by the condition.
    binding["member_plans"][condition.cohort_member_id]["source_provider_config_digest"] = refreshed_config.config_digest
    result, _limits, resolved_config = paper_formal_runner._condition_endpoint_contract(
        experiment_id=paper_formal_runner.EXP5_EXPERIMENT_ID,
        condition=condition,
        ai_api_configs={
            condition.provider_config_id: refreshed_config,
            paper_formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                paper_formal_runner.EXP5_EXPERIMENT_ID: binding
            },
        },
    )
    assert result["member_plans"][condition.cohort_member_id][
        "source_provider_config_digest"
    ] == refreshed_config.config_digest


@pytest.mark.parametrize("field", ["provider_model_id", "model_entry_id"])
def test_exp5_rejects_endpoint_identity_drift(field: str) -> None:
    config = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 0.5, "output_per_million_tokens": 2.0}
    )
    condition = _condition(source_digest=config.config_digest)
    binding = _approved_binding(config, condition)
    drifted = SimpleNamespace(**vars(condition))
    setattr(drifted, field, "drifted")
    with pytest.raises(ValueError):
        paper_formal_runner._condition_endpoint_contract(
            experiment_id=paper_formal_runner.EXP5_EXPERIMENT_ID,
            condition=drifted,
            ai_api_configs={
                condition.provider_config_id: config,
                paper_formal_runner.APPROVED_ENDPOINT_BINDINGS_KEY: {
                    paper_formal_runner.EXP5_EXPERIMENT_ID: binding
                },
            },
        )


def test_non_exp5_conditions_still_reject_source_config_digest_drift() -> None:
    frozen = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0}
    )
    current = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 0.5, "output_per_million_tokens": 2.0}
    )
    condition = _condition(source_digest=frozen.config_digest)
    condition.experiment_id = "exp1_real_ai_feasibility"
    with pytest.raises(ValueError, match="source provider config digest"):
        paper_formal_runner._condition_endpoint_contract(
            experiment_id="exp1_real_ai_feasibility",
            condition=condition,
            ai_api_configs={condition.provider_config_id: current},
        )


def test_exp5_refresh_rejects_frozen_to_current_endpoint_drift() -> None:
    frozen = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0}
    )
    current = replace(
        frozen,
        entries=[replace(frozen.entries[0], base_url="https://drifted.invalid/v1")],
    )

    with pytest.raises(ValueError, match="pricing refresh execution identity drift"):
        run_paper_experiments._validate_pricing_refresh_execution_identity(
            base_ai_api_configs={"siliconflow": frozen},
            provider_configs={"siliconflow": current},
        )


def test_exp5_refresh_accepts_pricing_only_change_and_keeps_other_frozen_configs() -> None:
    frozen_siliconflow = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 1.0, "output_per_million_tokens": 2.0}
    )
    current_siliconflow = _config(
        pricing={"currency": "USD", "input_per_million_tokens": 0.5, "output_per_million_tokens": 2.0}
    )
    frozen_deepseek = _config(
        pricing={"currency": "CNY", "input_per_million_tokens": 3.0, "output_per_million_tokens": 6.0}
    )

    run_paper_experiments._validate_pricing_refresh_execution_identity(
        base_ai_api_configs={
            "siliconflow": frozen_siliconflow,
            "exp1_baseline_deepseek": frozen_deepseek,
        },
        provider_configs={"siliconflow": current_siliconflow},
    )
