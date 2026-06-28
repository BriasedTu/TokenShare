"""Factorization all-required range merge policy."""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import Iterable

from tokenshare.core.expansion import MergePlan
from tokenshare.core.models import JsonObject
from tokenshare.plugins.factorization.models import (
    FactorizationMergeResult,
    PrimeFactor,
    PrimeFactorizationResult,
    RangeResult,
    canonical_json_digest,
)
from tokenshare.plugins.factorization.schemas import (
    ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
    MERGE_RESULT_NONTRIVIAL_FACTOR,
    MERGE_RESULT_PRIME_CERTIFICATE,
    MERGE_RESULT_PRIME_FACTORIZATION,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    schema_ref,
)


DEFAULT_PRIMALITY_RECHECK_MAX_DIVISORS = 100_000


@dataclass(frozen=True, kw_only=True)
class RangeSlotMergeInput:
    slot_key: str
    range_result: RangeResult | JsonObject
    canonical_output_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.slot_key, str) or not self.slot_key:
            raise ValueError("slot_key must be a non-empty string")
        if isinstance(self.range_result, dict):
            object.__setattr__(self, "range_result", RangeResult(**self.range_result))
        elif not isinstance(self.range_result, RangeResult):
            raise TypeError("range_result must be a RangeResult or structured object")
        if self.canonical_output_digest is None:
            object.__setattr__(
                self,
                "canonical_output_digest",
                canonical_json_digest(self.range_result.to_dict()),
            )
        if not str(self.canonical_output_digest).startswith("sha256:"):
            raise ValueError("canonical_output_digest must be a sha256 digest")

    def to_dict(self) -> JsonObject:
        return {
            "slot_key": self.slot_key,
            "range_result": self.range_result.to_dict(),
            "canonical_output_digest": self.canonical_output_digest,
        }


@dataclass(frozen=True, kw_only=True)
class FactorizationMergePolicyResult:
    merge_result: FactorizationMergeResult
    prime_factorization_result: PrimeFactorizationResult | None
    expected_output_resolvable: bool
    resolved_output_name: str | None

    def to_dict(self) -> JsonObject:
        return {
            "merge_result": self.merge_result.to_dict(),
            "prime_factorization_result": (
                self.prime_factorization_result.to_dict()
                if self.prime_factorization_result is not None
                else None
            ),
            "expected_output_resolvable": self.expected_output_resolvable,
            "resolved_output_name": self.resolved_output_name,
        }


def merge_required_range_results(
    *,
    merge_plan: MergePlan,
    slot_results: Iterable[RangeSlotMergeInput | JsonObject],
    merge_unit_id: str,
    created_at: str,
    primality_recheck_max_divisors: int = DEFAULT_PRIMALITY_RECHECK_MAX_DIVISORS,
) -> FactorizationMergePolicyResult:
    """Merge canonical range results using the plugin all-required policy."""

    _validate_positive_integer_budget(
        "primality_recheck_max_divisors", primality_recheck_max_divisors
    )
    _validate_policy_ref(merge_plan)
    required_slots = list(merge_plan.required_slots)
    required_slot_keys = [slot["slot_key"] for slot in required_slots]
    provided = [_coerce_slot_input(item) for item in slot_results]
    provided_by_key = _slot_inputs_by_key(provided)
    _require_exact_required_slots(required_slot_keys, provided_by_key)

    ordered_inputs = [provided_by_key[slot_key] for slot_key in required_slot_keys]
    _validate_slot_result_bindings(required_slots=required_slots, ordered_inputs=ordered_inputs)
    range_results = [item.range_result for item in ordered_inputs]
    summary = _plugin_summary(merge_plan)
    target_n = _validate_range_result_set(
        range_results=range_results,
        required_slot_count=len(required_slots),
        summary=summary,
    )

    found_factor = _smallest_found_factor(range_results)
    merge_plan_id = str(merge_plan.merge_plan_header["merge_plan_id"])
    merge_result_id = f"factorization_merge:{merge_plan_id}:{merge_unit_id}"
    slot_result_digests = [item.canonical_output_digest for item in ordered_inputs]
    coverage_digest = str(summary["ranges_digest"])

    if found_factor is None:
        prime_result = _prime_factorization_result(
            target_n=target_n,
            prime_values=[target_n],
            source_kind="prime_certificate",
            source_merge_result_id=merge_result_id,
            created_at=created_at,
        )
        merge_result = _merge_result(
            merge_result_id=merge_result_id,
            target_n=target_n,
            summary=summary,
            result_kind=MERGE_RESULT_PRIME_CERTIFICATE,
            required_slot_count=len(required_slots),
            slot_result_digests=slot_result_digests,
            coverage_digest=coverage_digest,
            found_factor=None,
            cofactor=None,
            prime_factorization_result=prime_result,
            created_at=created_at,
        )
        return FactorizationMergePolicyResult(
            merge_result=merge_result,
            prime_factorization_result=prime_result,
            expected_output_resolvable=True,
            resolved_output_name=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        )

    factor, cofactor = found_factor
    factor_prime = _is_prime_with_budget(
        factor,
        max_divisor_checks=primality_recheck_max_divisors,
    )
    if factor_prime is True:
        cofactor_prime = _is_prime_with_budget(
            cofactor,
            max_divisor_checks=primality_recheck_max_divisors,
        )
    else:
        cofactor_prime = False
    if factor_prime is True and cofactor_prime is True:
        prime_result = _prime_factorization_result(
            target_n=target_n,
            prime_values=[factor, cofactor],
            source_kind="semiprime_merge",
            source_merge_result_id=merge_result_id,
            created_at=created_at,
        )
        merge_result = _merge_result(
            merge_result_id=merge_result_id,
            target_n=target_n,
            summary=summary,
            result_kind=MERGE_RESULT_PRIME_FACTORIZATION,
            required_slot_count=len(required_slots),
            slot_result_digests=slot_result_digests,
            coverage_digest=coverage_digest,
            found_factor=factor,
            cofactor=cofactor,
            prime_factorization_result=prime_result,
            created_at=created_at,
        )
        return FactorizationMergePolicyResult(
            merge_result=merge_result,
            prime_factorization_result=prime_result,
            expected_output_resolvable=True,
            resolved_output_name=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        )

    limitation_reason = (
        "primality_check_budget_exceeded"
        if factor_prime is None or cofactor_prime is None
        else "composite_cofactor_requires_future_recursive_resolution"
    )
    merge_result = _merge_result(
        merge_result_id=merge_result_id,
        target_n=target_n,
        summary=summary,
        result_kind=MERGE_RESULT_NONTRIVIAL_FACTOR,
        required_slot_count=len(required_slots),
        slot_result_digests=slot_result_digests,
        coverage_digest=coverage_digest,
        found_factor=factor,
        cofactor=cofactor,
        prime_factorization_result=None,
        created_at=created_at,
        limitation_reason=limitation_reason,
    )
    return FactorizationMergePolicyResult(
        merge_result=merge_result,
        prime_factorization_result=None,
        expected_output_resolvable=False,
        resolved_output_name=None,
    )


def _validate_policy_ref(merge_plan: MergePlan) -> None:
    if merge_plan.merge_policy_ref.get("merge_policy_id") != ALL_REQUIRED_RANGE_MERGE_POLICY_ID:
        raise ValueError("merge_plan must use factorization all-required range merge policy")


def _coerce_slot_input(item: RangeSlotMergeInput | JsonObject) -> RangeSlotMergeInput:
    if isinstance(item, RangeSlotMergeInput):
        return item
    if isinstance(item, dict):
        return RangeSlotMergeInput(**item)
    raise TypeError("slot_results must contain RangeSlotMergeInput objects")


def _slot_inputs_by_key(
    slot_inputs: list[RangeSlotMergeInput],
) -> dict[str, RangeSlotMergeInput]:
    by_key: dict[str, RangeSlotMergeInput] = {}
    for item in slot_inputs:
        if item.slot_key in by_key:
            raise ValueError(f"duplicate range slot: {item.slot_key}")
        by_key[item.slot_key] = item
    return by_key


def _require_exact_required_slots(
    required_slot_keys: list[str],
    provided_by_key: dict[str, RangeSlotMergeInput],
) -> None:
    required = set(required_slot_keys)
    provided = set(provided_by_key)
    missing = sorted(required.difference(provided))
    if missing:
        raise ValueError("missing required range slots: " + ", ".join(missing))
    unexpected = sorted(provided.difference(required))
    if unexpected:
        raise ValueError("unexpected range slots: " + ", ".join(unexpected))


def _validate_slot_result_bindings(
    *,
    required_slots: list[JsonObject],
    ordered_inputs: list[RangeSlotMergeInput],
) -> None:
    for slot, slot_input in zip(required_slots, ordered_inputs, strict=True):
        result = slot_input.range_result
        expected_child_key = f"range:{result.coverage_id}:{result.child_index}"
        expected_slot_key = f"{expected_child_key}:range_result"
        if slot.get("source_child_logical_key") != expected_child_key:
            raise ValueError("slot range result mismatch")
        if slot.get("slot_key") != expected_slot_key or slot_input.slot_key != expected_slot_key:
            raise ValueError("slot range result mismatch")


def _plugin_summary(merge_plan: MergePlan) -> JsonObject:
    body = merge_plan.plugin_payload.get("plugin_defined_body")
    if not isinstance(body, dict):
        raise ValueError("merge_plan plugin payload missing plugin_defined_body")
    summary = body.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("merge_plan plugin payload missing summary")
    return summary


def _validate_range_result_set(
    *,
    range_results: list[RangeResult],
    required_slot_count: int,
    summary: JsonObject,
) -> int:
    if len(range_results) != required_slot_count:
        raise ValueError("range_result_count must equal required_slot_count")
    if summary.get("range_count") != len(range_results):
        raise ValueError("merge plan range_count must match required slots")

    target_values = {item.target_n for item in range_results}
    coverage_ids = {item.coverage_id for item in range_results}
    params_digests = {item.partition_params_digest for item in range_results}
    if len(target_values) != 1:
        raise ValueError("range results must use the same target_n")
    if len(coverage_ids) != 1:
        raise ValueError("range results must use the same coverage_id")
    if len(params_digests) != 1:
        raise ValueError("range results must use the same partition_params_digest")

    target_n_text = next(iter(target_values))
    target_n = int(target_n_text)
    if summary.get("target_n") != target_n_text:
        raise ValueError("merge plan target_n does not match range results")
    if summary.get("coverage_id") != next(iter(coverage_ids)):
        raise ValueError("merge plan coverage_id does not match range results")
    if summary.get("partition_params_digest") != next(iter(params_digests)):
        raise ValueError("merge plan partition params digest does not match range results")
    ranges_digest = summary.get("ranges_digest")
    if not isinstance(ranges_digest, str) or not ranges_digest.startswith("sha256:"):
        raise ValueError("merge plan ranges_digest must be a sha256 digest")

    _validate_complete_candidate_coverage(target_n=target_n, range_results=range_results)
    return target_n


def _validate_complete_candidate_coverage(
    *,
    target_n: int,
    range_results: list[RangeResult],
) -> None:
    intervals = sorted((int(item.range_start), int(item.range_end)) for item in range_results)
    expected_start = 2
    for range_start, range_end in intervals:
        if range_start != expected_start:
            raise ValueError("range coverage must be complete without gap or overlap")
        if range_end < range_start:
            raise ValueError("range coverage contains an empty interval")
        expected_start = range_end + 1
    if expected_start - 1 != isqrt(target_n):
        raise ValueError("range coverage must end at floor_sqrt(target_n)")


def _smallest_found_factor(range_results: list[RangeResult]) -> tuple[int, int] | None:
    found: list[tuple[int, int]] = []
    for item in range_results:
        if item.result_kind == RANGE_RESULT_NO_FACTOR:
            continue
        if item.result_kind != RANGE_RESULT_FOUND_FACTOR:
            raise ValueError("unsupported range_result kind")
        if item.found_factor is None or item.cofactor is None:
            raise ValueError("found_factor range_result missing factor fields")
        found.append((int(item.found_factor), int(item.cofactor)))
    if not found:
        return None
    return min(found, key=lambda item: item[0])


def _prime_factorization_result(
    *,
    target_n: int,
    prime_values: list[int],
    source_kind: str,
    source_merge_result_id: str,
    created_at: str,
) -> PrimeFactorizationResult:
    factor_counts: dict[int, int] = {}
    for value in prime_values:
        factor_counts[value] = factor_counts.get(value, 0) + 1
    factors = [
        PrimeFactor(prime=str(prime), exponent=exponent)
        for prime, exponent in sorted(factor_counts.items())
    ]
    return PrimeFactorizationResult(
        result_id=f"prime_factorization:{source_merge_result_id}",
        target_n=str(target_n),
        prime_factors=factors,
        source_kind=source_kind,
        source_merge_result_id=source_merge_result_id,
        primality_evidence={
            "policy_id": "factorization.trial_division_primality.v1",
            "verified_prime_values": [str(prime) for prime in sorted(factor_counts)],
            "verification_scope": "merge_policy_budgeted_check",
        },
        created_at=created_at,
    )


def _merge_result(
    *,
    merge_result_id: str,
    target_n: int,
    summary: JsonObject,
    result_kind: str,
    required_slot_count: int,
    slot_result_digests: list[str],
    coverage_digest: str,
    found_factor: int | None,
    cofactor: int | None,
    prime_factorization_result: PrimeFactorizationResult | None,
    created_at: str,
    limitation_reason: str | None = None,
) -> FactorizationMergeResult:
    return FactorizationMergeResult(
        merge_result_id=merge_result_id,
        target_n=str(target_n),
        coverage_id=str(summary["coverage_id"]),
        partition_params_digest=str(summary["partition_params_digest"]),
        result_kind=result_kind,
        range_result_count=required_slot_count,
        required_slot_count=required_slot_count,
        coverage_digest=coverage_digest,
        slot_result_digests=slot_result_digests,
        found_factor=str(found_factor) if found_factor is not None else None,
        cofactor=str(cofactor) if cofactor is not None else None,
        prime_factorization_ref=(
            _prime_factorization_ref(prime_factorization_result)
            if prime_factorization_result is not None
            else None
        ),
        limitation_reason=limitation_reason,
        created_at=created_at,
    )


def _prime_factorization_ref(result: PrimeFactorizationResult) -> JsonObject:
    ref = schema_ref(result.schema_version)
    return {
        "artifact_id": result.result_id,
        "artifact_type": "canonical_output",
        "artifact_schema_id": ref["artifact_schema_id"],
        "artifact_schema_version": ref["artifact_schema_version"],
        "content_hash": canonical_json_digest(result.to_dict()),
        "metadata": {"output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION},
    }


def _validate_positive_integer_budget(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _is_prime_with_budget(value: int, *, max_divisor_checks: int) -> bool | None:
    if value < 2:
        return False
    if value == 2:
        return True
    divisor_checks = 1
    if value % 2 == 0:
        return False
    limit = isqrt(value)
    divisor = 3
    while divisor <= limit:
        if divisor_checks >= max_divisor_checks:
            return None
        divisor_checks += 1
        if value % divisor == 0:
            return False
        divisor += 2
    return True
