from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tokenshare.executors.ai_api_config import AIAPIExecutorConfig, AIAPIProviderEntry
from tokenshare.experiments import paper_response_bank as response_bank
from tokenshare.experiments import run_paper_experiments, run_paper_pipeline as pipeline
from tokenshare.experiments.paper_models import digest_json
from tokenshare.experiments.paper_response_bank import AcquisitionRequest, FullAcquisitionBudget
from tokenshare.experiments.paper_resource_accounting import FrozenPricing


_CONFIG_ID = "exp1_baseline_deepseek"
_ENTRY_ID = "deepseek_v4_pro_exp1_baseline"


def _config(
    *,
    pricing: dict[str, object],
    model: str = "deepseek-v4-pro",
    base_url: str = "https://api.deepseek.com",
    max_tokens: int = 300_000,
) -> AIAPIExecutorConfig:
    return AIAPIExecutorConfig(
        schema_version="phase7.ai_api_executor_config.v1",
        executor_id="deepseek-exp1",
        provider_family="deepseek",
        selection_policy={"kind": "fixed"},
        defaults={"max_tokens": max_tokens, "timeout_seconds": 600},
        entries=[
            AIAPIProviderEntry(
                entry_id=_ENTRY_ID,
                enabled=True,
                base_url=base_url,
                api_key_env="DEEPSEEK_API_KEY",
                model=model,
                endpoint="/chat/completions",
                supports_json_mode=True,
                supports_streaming=False,
                request_overrides={"reasoning_effort": "high"},
                pricing=pricing,
                tags=["exp1"],
            )
        ],
        local_concurrency={"max_in_flight_global": 1},
        metadata={"cohort": "exp1"},
    )


def _frozen_pricing(config: AIAPIExecutorConfig) -> FrozenPricing:
    pricing = config.entries[0].pricing
    return FrozenPricing(
        currency=str(pricing["currency"]),
        input_per_million_tokens=Decimal(
            str(
                pricing.get(
                    "input_per_million_tokens",
                    pricing["uncached_input_per_million_tokens"],
                )
            )
        ),
        output_per_million_tokens=Decimal(
            str(pricing["output_per_million_tokens"])
        ),
    )


def _record(frozen: AIAPIExecutorConfig) -> SimpleNamespace:
    return SimpleNamespace(
        provider_family="deepseek",
        provider_config_id=_CONFIG_ID,
        model_entry_id=_ENTRY_ID,
        source_provider_config_digest=frozen.config_digest,
    )


def _approval(
    *,
    frozen: AIAPIExecutorConfig,
    current: AIAPIExecutorConfig,
    approved_budget_digest: str,
) -> dict[str, object]:
    current_pricing = dict(current.entries[0].pricing)
    nested = {
        "schema_version": "tokenshare.newfullrun_exp1_current_pricing_authority.v1",
        "provider_config_id": _CONFIG_ID,
        "provider_config_digest": current.config_digest,
        "source_provider_config_digest": frozen.config_digest,
        "source_execution_identity_digest": (
            run_paper_experiments._pricing_refresh_execution_identity(frozen)
        ),
        "execution_identity_digest": (
            run_paper_experiments._pricing_refresh_execution_identity(current)
        ),
        "pricing_by_entry": {_ENTRY_ID: current_pricing},
        "provider_calls_made": 0,
    }
    nested["authority_digest"] = digest_json(nested)
    return {
        "authority_digest": "sha256:" + "a" * 64,
        "exp1_acquisition_authority": {
            "budget": {"budget_digest": approved_budget_digest},
            "current_pricing_authority": nested,
        },
    }


def _acquisition_request(*, pricing: FrozenPricing) -> AcquisitionRequest:
    prepared = SimpleNamespace(estimated_prompt_tokens=1_000)
    row = SimpleNamespace()
    # FullAcquisitionBudget only consumes the verified request accounting fields.
    return AcquisitionRequest(
        inventory_row=row,
        prepared_request=prepared,
        provider_family="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        timeout_seconds=600,
        token_upper_bound=301_000,
        cost_upper_bound=(
            Decimal("1000") * pricing.input_per_million_tokens
            + Decimal("300000") * pricing.output_per_million_tokens
        ) / Decimal("1000000"),
        frozen_pricing=pricing,
        requested_at="2026-08-20T00:00:00Z",
    )


def _approval_with_current_price_change(
    *,
    frozen: AIAPIExecutorConfig,
    current: AIAPIExecutorConfig,
) -> dict[str, object]:
    current_pricing = _frozen_pricing(current)
    budget_digest = FullAcquisitionBudget.create(
        (_acquisition_request(pricing=current_pricing),)
    ).budget_digest
    return _approval(
        frozen=frozen,
        current=current,
        approved_budget_digest=budget_digest,
    )


def test_full_current_pricing_authority_materializes_current_budget_bound_by_a() -> None:
    frozen = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 1,
            "output_per_million_tokens": 2,
        }
    )
    current = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 3,
            "output_per_million_tokens": 6,
        }
    )
    approval = _approval_with_current_price_change(frozen=frozen, current=current)

    authority = pipeline._full_current_exp1_acquisition_pricing_authority(
        approval_authority=approval,
        frozen_ai_api_configs={_CONFIG_ID: frozen},
        current_exp1_config=current,
    )
    materialized_pricing = response_bank._resolve_acquisition_pricing(
        record=_record(frozen),
        source_config=frozen,
        current_config=current,
        supplied_pricing=_frozen_pricing(current),
        full_current_pricing_authority=authority,
    )
    materialized_budget = FullAcquisitionBudget.create(
        (_acquisition_request(pricing=materialized_pricing),)
    )

    assert materialized_pricing == _frozen_pricing(current)
    assert materialized_budget.budget_digest == authority.approved_exp1_budget_digest


def test_representative_changed_pricing_still_rejects_frozen_source_mismatch() -> None:
    frozen = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 1,
            "output_per_million_tokens": 2,
        }
    )
    current = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 3,
            "output_per_million_tokens": 6,
        }
    )

    with pytest.raises(ValueError, match="representative acquisition frozen pricing drift"):
        response_bank._resolve_acquisition_pricing(
            record=_record(frozen),
            source_config=frozen,
            current_config=frozen,
            supplied_pricing=_frozen_pricing(current),
            full_current_pricing_authority=None,
        )


@pytest.mark.parametrize(
    "current",
    [
        lambda: _config(
            pricing={
                "currency": "CNY",
                "uncached_input_per_million_tokens": 3,
                "output_per_million_tokens": 6,
            },
            model="drifted-model",
        ),
        lambda: _config(
            pricing={
                "currency": "CNY",
                "uncached_input_per_million_tokens": 3,
                "output_per_million_tokens": 6,
            },
            base_url="https://drifted.invalid",
        ),
        lambda: _config(
            pricing={
                "currency": "CNY",
                "uncached_input_per_million_tokens": 3,
                "output_per_million_tokens": 6,
            },
            max_tokens=123,
        ),
    ],
)
def test_full_current_pricing_authority_rejects_execution_identity_drift(current) -> None:
    frozen = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 1,
            "output_per_million_tokens": 2,
        }
    )
    current_config = current()
    approval = _approval_with_current_price_change(
        frozen=frozen,
        current=current_config,
    )

    with pytest.raises(ValueError, match="execution identity"):
        pipeline._full_current_exp1_acquisition_pricing_authority(
            approval_authority=approval,
            frozen_ai_api_configs={_CONFIG_ID: frozen},
            current_exp1_config=current_config,
        )


def test_full_current_pricing_authority_rejects_a_price_not_matching_current_config() -> None:
    frozen = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 1,
            "output_per_million_tokens": 2,
        }
    )
    current = _config(
        pricing={
            "currency": "CNY",
            "uncached_input_per_million_tokens": 3,
            "output_per_million_tokens": 6,
        }
    )
    approval = _approval_with_current_price_change(frozen=frozen, current=current)
    current_pricing_authority = approval["exp1_acquisition_authority"][  # type: ignore[index]
        "current_pricing_authority"
    ]
    current_pricing_authority["pricing_by_entry"][_ENTRY_ID][  # type: ignore[index]
        "uncached_input_per_million_tokens"
    ] = 999
    digest_body = {
        key: value
        for key, value in current_pricing_authority.items()  # type: ignore[union-attr]
        if key != "authority_digest"
    }
    current_pricing_authority["authority_digest"] = digest_json(digest_body)  # type: ignore[index]

    with pytest.raises(ValueError, match="pricing authority drift"):
        pipeline._full_current_exp1_acquisition_pricing_authority(
            approval_authority=approval,
            frozen_ai_api_configs={_CONFIG_ID: frozen},
            current_exp1_config=current,
        )
