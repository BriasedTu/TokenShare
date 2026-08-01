"""论文 acquisition 的冻结价格资源核算。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class FrozenPricing:
    currency: str
    input_per_million_tokens: Decimal
    output_per_million_tokens: Decimal

    def __post_init__(self) -> None:
        if not self.currency:
            raise ValueError("pricing currency must be non-empty")
        if self.input_per_million_tokens < 0 or self.output_per_million_tokens < 0:
            raise ValueError("frozen pricing rates must be non-negative")


@dataclass(frozen=True, kw_only=True)
class ProviderUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("provider usage tokens must be non-negative")


@dataclass(frozen=True, kw_only=True)
class ResourceCharge:
    charged_tokens: int
    cost_estimate: Decimal
    usage_missing: bool


def account_terminal_usage(
    *,
    token_upper_bound: int,
    cost_upper_bound: Decimal,
    pricing: FrozenPricing,
    usage: ProviderUsage | None,
) -> ResourceCharge:
    """有 usage 时按冻结价格核算；缺失时保留完整 reservation upper。"""

    if usage is None:
        return ResourceCharge(
            charged_tokens=token_upper_bound,
            cost_estimate=cost_upper_bound,
            usage_missing=True,
        )
    charged_tokens = usage.input_tokens + usage.output_tokens
    if charged_tokens > token_upper_bound:
        raise ValueError("provider usage exceeds reserved token upper bound")
    cost_estimate = (
        Decimal(usage.input_tokens) * pricing.input_per_million_tokens
        + Decimal(usage.output_tokens) * pricing.output_per_million_tokens
    ) / Decimal(1_000_000)
    if cost_estimate > cost_upper_bound:
        raise ValueError("provider usage cost exceeds reserved CNY upper bound")
    return ResourceCharge(
        charged_tokens=charged_tokens,
        cost_estimate=cost_estimate,
        usage_missing=False,
    )
