"""Factorization candidate range partition helper."""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt

from tokenshare.core.models import JsonObject
from tokenshare.plugins.factorization.models import (
    CandidateRangeCoverageProof,
    CandidateRangePartitionParams,
    FactorSearchRangeInput,
    canonical_json_digest,
)
from tokenshare.plugins.factorization.schemas import CANDIDATE_RANGE_PARTITION_STRATEGY_ID


@dataclass(frozen=True, kw_only=True)
class CandidateRangePartitionResult:
    params: CandidateRangePartitionParams
    coverage_proof: CandidateRangeCoverageProof
    ranges: tuple[FactorSearchRangeInput, ...]

    def to_dict(self) -> JsonObject:
        return {
            "params": self.params.to_dict(),
            "coverage_proof": self.coverage_proof.to_dict(),
            "ranges": [item.to_dict() for item in self.ranges],
        }


def partition_candidate_ranges(
    *,
    target_n: str | int,
    requested_child_count: int,
    max_children_per_unit: int,
    min_divisor: str | int = "2",
    max_divisor: str | int | None = None,
) -> CandidateRangePartitionResult:
    """把候选因子搜索域切成稳定、连续、非空的 bounded ranges。"""

    target_n_value, target_n_text = _normalize_decimal_integer(
        "target_n", target_n, min_value=2
    )
    min_divisor_value, min_divisor_text = _normalize_decimal_integer(
        "min_divisor", min_divisor, min_value=2
    )
    if max_divisor is None:
        max_divisor_value = isqrt(target_n_value)
        max_divisor_text = str(max_divisor_value)
    else:
        max_divisor_value, max_divisor_text = _normalize_decimal_integer(
            "max_divisor", max_divisor, min_value=0
        )
    if max_divisor_value > isqrt(target_n_value):
        raise ValueError("max_divisor must be <= floor_sqrt(target_n)")
    if not isinstance(requested_child_count, int) or requested_child_count < 1:
        raise ValueError("requested_child_count must be >= 1")
    if not isinstance(max_children_per_unit, int) or max_children_per_unit < 1:
        raise ValueError("max_children_per_unit must be >= 1")

    domain_size = max(0, max_divisor_value - min_divisor_value + 1)
    actual_child_count = min(domain_size, requested_child_count, max_children_per_unit)

    params = CandidateRangePartitionParams(
        strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        target_n=target_n_text,
        min_divisor=min_divisor_text,
        max_divisor=max_divisor_text,
        requested_child_count=requested_child_count,
        actual_child_count=actual_child_count,
        range_policy="contiguous",
        small_prime_precheck={
            "policy_id": "factorization.small_prime_precheck.v1",
            "applied": False,
            "reason": "candidate_range_partition_only",
        },
    )
    target_n_digest = canonical_json_digest({"target_n": target_n_text})
    coverage_id = f"coverage:{target_n_digest}:{params.params_digest}"

    ranges = tuple(
        FactorSearchRangeInput(
            target_n=target_n_text,
            range_start=str(range_start),
            range_end=str(range_end),
            coverage_id=coverage_id,
            child_index=child_index,
            child_count=actual_child_count,
            partition_params_digest=params.params_digest,
        )
        for child_index, (range_start, range_end) in enumerate(
            _partition_contiguous_ranges(
                start=min_divisor_value,
                end=max_divisor_value,
                range_count=actual_child_count,
            )
        )
    )
    ranges_digest = canonical_json_digest([item.to_dict() for item in ranges])
    coverage_proof = CandidateRangeCoverageProof(
        coverage_id=coverage_id,
        target_n=target_n_text,
        domain_start=min_divisor_text,
        domain_end=max_divisor_text,
        range_count=len(ranges),
        ranges_digest=ranges_digest,
        no_gap=True,
        no_overlap=True,
        full_domain_covered=True,
        sqrt_bound_checked=True,
        created_by_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    )

    return CandidateRangePartitionResult(
        params=params,
        coverage_proof=coverage_proof,
        ranges=ranges,
    )


def _partition_contiguous_ranges(
    *,
    start: int,
    end: int,
    range_count: int,
) -> tuple[tuple[int, int], ...]:
    if range_count == 0:
        return ()
    domain_size = end - start + 1
    base_size = domain_size // range_count
    extra = domain_size % range_count
    ranges: list[tuple[int, int]] = []
    next_start = start
    for index in range(range_count):
        size = base_size + (1 if index < extra else 0)
        range_end = next_start + size - 1
        ranges.append((next_start, range_end))
        next_start = range_end + 1
    return tuple(ranges)


def _normalize_decimal_integer(
    field_name: str,
    value: str | int,
    *,
    min_value: int,
) -> tuple[int, str]:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a decimal string or integer")
    if isinstance(value, int):
        parsed = value
        text = str(value)
    elif isinstance(value, str):
        if not value or not value.isdecimal():
            raise ValueError(f"{field_name} must be a decimal string")
        if len(value) > 1 and value.startswith("0"):
            raise ValueError(f"{field_name} must be a decimal string without leading zeros")
        parsed = int(value)
        text = value
    else:
        raise TypeError(f"{field_name} must be a decimal string or integer")
    if parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}")
    return parsed, text
