"""Phase 6 factorization 插件纯数据模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from math import isqrt
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.plugins.factorization.schemas import (
    ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
    CANDIDATE_RANGE_COVERAGE_PROOF_SCHEMA_VERSION,
    CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION,
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
    FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION,
    MERGE_RESULT_KINDS,
    MERGE_RESULT_NONTRIVIAL_FACTOR,
    MERGE_RESULT_PRIME_CERTIFICATE,
    MERGE_RESULT_PRIME_FACTORIZATION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_KINDS,
    RANGE_RESULT_NO_FACTOR,
    RANGE_RESULT_SCHEMA_VERSION,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    ROOT_INPUT_SCHEMA_VERSION,
    TRIAL_DIVISION_PRIMALITY_POLICY_ID,
)


@dataclass(frozen=True, kw_only=True)
class RootInput:
    target_n: str
    requested_output: str
    case_label: str | None = None
    input_digest: str | None = None
    schema_version: str = ROOT_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, ROOT_INPUT_SCHEMA_VERSION)
        _parse_decimal_integer("target_n", self.target_n, min_value=2)
        if self.requested_output != REQUESTED_OUTPUT_PRIME_FACTORIZATION:
            raise ValueError("requested_output must be prime_factorization_result")
        _set_or_check_digest(self, "input_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "target_n": self.target_n,
            "requested_output": self.requested_output,
            "case_label": self.case_label,
            "input_digest": self.input_digest,
        }

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "target_n": self.target_n,
            "requested_output": self.requested_output,
            "case_label": self.case_label,
        }


@dataclass(frozen=True, kw_only=True)
class FactorIntegerSubject:
    subject_id: str
    task_id: str
    unit_id: str
    target_n: str
    source_kind: str
    source_ref: JsonObject
    requested_output: str
    created_at: str
    target_n_digest: str | None = None
    schema_version: str = FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION)
        _parse_decimal_integer("target_n", self.target_n, min_value=2)
        _require_non_empty("subject_id", self.subject_id)
        _require_non_empty("task_id", self.task_id)
        _require_non_empty("unit_id", self.unit_id)
        if self.source_kind not in {"root_input", "recursive_factor", "merge_output"}:
            raise ValueError("source_kind must be root_input, recursive_factor, or merge_output")
        if not isinstance(self.source_ref, dict) or not self.source_ref:
            raise ValueError("source_ref must be a non-empty object")
        if self.requested_output != REQUESTED_OUTPUT_PRIME_FACTORIZATION:
            raise ValueError("requested_output must be prime_factorization_result")
        _require_non_empty("created_at", self.created_at)
        _set_or_check_digest(self, "target_n_digest", {"target_n": self.target_n})

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "subject_id": self.subject_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "target_n": self.target_n,
            "target_n_digest": self.target_n_digest,
            "source_kind": self.source_kind,
            "source_ref": _json_value(self.source_ref),
            "requested_output": self.requested_output,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class CandidateRangePartitionParams:
    strategy_id: str
    target_n: str
    min_divisor: str
    max_divisor: str
    requested_child_count: int
    actual_child_count: int
    range_policy: str
    small_prime_precheck: JsonObject
    params_digest: str | None = None
    schema_version: str = CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION)
        if self.strategy_id != CANDIDATE_RANGE_PARTITION_STRATEGY_ID:
            raise ValueError("strategy_id must be factorization.candidate_range_partition.v1")
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        min_divisor = _parse_decimal_integer("min_divisor", self.min_divisor, min_value=2)
        max_divisor = _parse_decimal_integer("max_divisor", self.max_divisor, min_value=0)
        _require_integer("requested_child_count", self.requested_child_count)
        _require_integer("actual_child_count", self.actual_child_count)
        if self.requested_child_count < 1:
            raise ValueError("requested_child_count must be >= 1")
        if self.actual_child_count < 0:
            raise ValueError("actual_child_count must be >= 0")
        if self.actual_child_count > self.requested_child_count:
            raise ValueError("actual_child_count must be <= requested_child_count")
        if max_divisor >= min_divisor and self.actual_child_count < 1:
            raise ValueError("actual_child_count must be >= 1 for a non-empty domain")
        if max_divisor > isqrt(target_n):
            raise ValueError("max_divisor must be <= floor_sqrt(target_n)")
        if self.range_policy != "contiguous":
            raise ValueError("range_policy must be contiguous")
        if not isinstance(self.small_prime_precheck, dict):
            raise ValueError("small_prime_precheck must be an object")
        _set_or_check_digest(self, "params_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["params_digest"] = self.params_digest
        return body

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "target_n": self.target_n,
            "min_divisor": self.min_divisor,
            "max_divisor": self.max_divisor,
            "requested_child_count": self.requested_child_count,
            "actual_child_count": self.actual_child_count,
            "range_policy": self.range_policy,
            "small_prime_precheck": _json_value(self.small_prime_precheck),
        }


@dataclass(frozen=True, kw_only=True)
class CandidateRangeCoverageProof:
    coverage_id: str
    target_n: str
    domain_start: str
    domain_end: str
    range_count: int
    ranges_digest: str
    no_gap: bool
    no_overlap: bool
    full_domain_covered: bool
    sqrt_bound_checked: bool
    created_by_strategy_id: str
    schema_version: str = CANDIDATE_RANGE_COVERAGE_PROOF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, CANDIDATE_RANGE_COVERAGE_PROOF_SCHEMA_VERSION)
        _require_non_empty("coverage_id", self.coverage_id)
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        domain_start = _parse_decimal_integer("domain_start", self.domain_start, min_value=2)
        domain_end = _parse_decimal_integer("domain_end", self.domain_end, min_value=0)
        if domain_start <= domain_end and domain_end > isqrt(target_n):
            raise ValueError("domain_end must be <= floor_sqrt(target_n)")
        _require_integer("range_count", self.range_count)
        if self.range_count < 0:
            raise ValueError("range_count must be >= 0")
        _require_digest("ranges_digest", self.ranges_digest)
        if not (self.no_gap and self.no_overlap and self.full_domain_covered and self.sqrt_bound_checked):
            raise ValueError("coverage proof flags must all be true")
        if self.created_by_strategy_id != CANDIDATE_RANGE_PARTITION_STRATEGY_ID:
            raise ValueError("created_by_strategy_id must be factorization.candidate_range_partition.v1")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "coverage_id": self.coverage_id,
            "target_n": self.target_n,
            "domain_start": self.domain_start,
            "domain_end": self.domain_end,
            "range_count": self.range_count,
            "ranges_digest": self.ranges_digest,
            "no_gap": self.no_gap,
            "no_overlap": self.no_overlap,
            "full_domain_covered": self.full_domain_covered,
            "sqrt_bound_checked": self.sqrt_bound_checked,
            "created_by_strategy_id": self.created_by_strategy_id,
        }


@dataclass(frozen=True, kw_only=True)
class FactorSearchRangeInput:
    target_n: str
    range_start: str
    range_end: str
    coverage_id: str
    child_index: int
    child_count: int
    partition_params_digest: str
    range_digest: str | None = None
    schema_version: str = FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION)
        self._validate_range()
        _require_non_empty("coverage_id", self.coverage_id)
        _require_integer("child_index", self.child_index)
        _require_integer("child_count", self.child_count)
        if self.child_index < 0:
            raise ValueError("child_index must be >= 0")
        if self.child_count < 1:
            raise ValueError("child_count must be >= 1")
        if self.child_index >= self.child_count:
            raise ValueError("child_index must be < child_count")
        _require_digest("partition_params_digest", self.partition_params_digest)
        _set_or_check_digest(self, "range_digest", self._digest_body())

    def to_dict(self) -> JsonObject:
        body = self._digest_body()
        body["range_digest"] = self.range_digest
        return body

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "target_n": self.target_n,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "coverage_id": self.coverage_id,
            "child_index": self.child_index,
            "child_count": self.child_count,
            "partition_params_digest": self.partition_params_digest,
        }

    def _validate_range(self) -> None:
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        range_start = _parse_decimal_integer("range_start", self.range_start, min_value=2)
        range_end = _parse_decimal_integer("range_end", self.range_end, min_value=2)
        if range_start > range_end:
            raise ValueError("range_start must be <= range_end")
        if range_end > isqrt(target_n):
            raise ValueError("range_end must be <= floor_sqrt(target_n)")


@dataclass(frozen=True, kw_only=True)
class FactorSearchInstruction:
    instruction_id: str
    request_id: str
    unit_id: str
    target_n: str
    range_start: str
    range_end: str
    output_schema_version: str
    allowed_result_kinds: list[str]
    determinism_requirement: str
    schema_version: str = FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION)
        _require_non_empty("instruction_id", self.instruction_id)
        _require_non_empty("request_id", self.request_id)
        _require_non_empty("unit_id", self.unit_id)
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        range_start = _parse_decimal_integer("range_start", self.range_start, min_value=2)
        range_end = _parse_decimal_integer("range_end", self.range_end, min_value=2)
        if range_start > range_end:
            raise ValueError("range_start must be <= range_end")
        if range_end > isqrt(target_n):
            raise ValueError("range_end must be <= floor_sqrt(target_n)")
        if self.output_schema_version != RANGE_RESULT_SCHEMA_VERSION:
            raise ValueError("output_schema_version must be factorization.range_result.v1")
        if self.allowed_result_kinds != RANGE_RESULT_KINDS:
            raise ValueError("allowed_result_kinds must be found_factor, no_factor_in_range")
        if self.determinism_requirement != "range_recheckable":
            raise ValueError("determinism_requirement must be range_recheckable")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "instruction_id": self.instruction_id,
            "request_id": self.request_id,
            "unit_id": self.unit_id,
            "target_n": self.target_n,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "output_schema_version": self.output_schema_version,
            "allowed_result_kinds": list(self.allowed_result_kinds),
            "determinism_requirement": self.determinism_requirement,
        }


@dataclass(frozen=True, kw_only=True)
class RangeResult:
    range_result_id: str
    result_kind: str
    target_n: str
    range_start: str
    range_end: str
    coverage_id: str
    child_index: int
    partition_params_digest: str
    found_factor: str | None
    cofactor: str | None
    checked_divisor_count: int
    executor_summary: JsonObject
    created_at: str
    schema_version: str = RANGE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, RANGE_RESULT_SCHEMA_VERSION)
        _require_non_empty("range_result_id", self.range_result_id)
        if self.result_kind not in RANGE_RESULT_KINDS:
            raise ValueError("result_kind must be found_factor or no_factor_in_range")
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        range_start = _parse_decimal_integer("range_start", self.range_start, min_value=2)
        range_end = _parse_decimal_integer("range_end", self.range_end, min_value=2)
        if range_start > range_end:
            raise ValueError("range_start must be <= range_end")
        if range_end > isqrt(target_n):
            raise ValueError("range_end must be <= floor_sqrt(target_n)")
        _require_non_empty("coverage_id", self.coverage_id)
        _require_integer("child_index", self.child_index)
        if self.child_index < 0:
            raise ValueError("child_index must be >= 0")
        _require_digest("partition_params_digest", self.partition_params_digest)
        _require_integer("checked_divisor_count", self.checked_divisor_count)
        if self.checked_divisor_count < 0:
            raise ValueError("checked_divisor_count must be >= 0")
        if not isinstance(self.executor_summary, dict):
            raise ValueError("executor_summary must be an object")
        _require_non_empty("created_at", self.created_at)
        if self.result_kind == RANGE_RESULT_FOUND_FACTOR:
            self._validate_found_factor(target_n, range_start, range_end)
        else:
            self._validate_no_factor()

    def _validate_found_factor(self, target_n: int, range_start: int, range_end: int) -> None:
        if self.found_factor is None:
            raise ValueError("found_factor result requires found_factor")
        if self.cofactor is None:
            raise ValueError("found_factor result requires cofactor")
        found_factor = _parse_decimal_integer("found_factor", self.found_factor, min_value=2)
        cofactor = _parse_decimal_integer("cofactor", self.cofactor, min_value=2)
        if not range_start <= found_factor <= range_end:
            raise ValueError("found_factor must be inside range")
        if found_factor >= target_n:
            raise ValueError("found_factor must be smaller than target_n")
        if target_n % found_factor != 0:
            raise ValueError("found_factor must divide target_n")
        if target_n // found_factor != cofactor:
            raise ValueError("cofactor must equal target_n / found_factor")

    def _validate_no_factor(self) -> None:
        if self.found_factor is not None or self.cofactor is not None:
            raise ValueError("no_factor_in_range requires found_factor and cofactor to be null")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "range_result_id": self.range_result_id,
            "result_kind": self.result_kind,
            "target_n": self.target_n,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "coverage_id": self.coverage_id,
            "child_index": self.child_index,
            "partition_params_digest": self.partition_params_digest,
            "found_factor": self.found_factor,
            "cofactor": self.cofactor,
            "checked_divisor_count": self.checked_divisor_count,
            "executor_summary": _json_value(self.executor_summary),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class FactorizationMergeResult:
    merge_result_id: str
    target_n: str
    coverage_id: str
    partition_params_digest: str
    result_kind: str
    range_result_count: int
    required_slot_count: int
    coverage_digest: str
    slot_result_digests: list[str]
    found_factor: str | None
    cofactor: str | None
    prime_factorization_ref: JsonObject | None
    created_at: str
    limitation_reason: str | None = None
    schema_version: str = FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION)
        _require_non_empty("merge_result_id", self.merge_result_id)
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        _require_non_empty("coverage_id", self.coverage_id)
        _require_digest("partition_params_digest", self.partition_params_digest)
        if self.result_kind not in MERGE_RESULT_KINDS:
            raise ValueError("result_kind must be a supported factorization merge result kind")
        _require_integer("range_result_count", self.range_result_count)
        _require_integer("required_slot_count", self.required_slot_count)
        if self.range_result_count < 1:
            raise ValueError("range_result_count must be >= 1")
        if self.required_slot_count < 1:
            raise ValueError("required_slot_count must be >= 1")
        if self.range_result_count != self.required_slot_count:
            raise ValueError("range_result_count must equal required_slot_count")
        _require_digest("coverage_digest", self.coverage_digest)
        if not all(isinstance(item, str) and item.startswith("sha256:") for item in self.slot_result_digests):
            raise ValueError("slot_result_digests must contain sha256 digests")
        if len(self.slot_result_digests) != self.required_slot_count:
            raise ValueError("slot_result_digests must match required_slot_count")
        if self.result_kind == MERGE_RESULT_PRIME_CERTIFICATE:
            if self.found_factor is not None or self.cofactor is not None:
                raise ValueError("prime_certificate must not include found_factor or cofactor")
        else:
            if self.found_factor is None or self.cofactor is None:
                raise ValueError(f"{self.result_kind} requires found_factor and cofactor")
            found_factor = _parse_decimal_integer("found_factor", self.found_factor, min_value=2)
            cofactor = _parse_decimal_integer("cofactor", self.cofactor, min_value=2)
            if target_n != found_factor * cofactor:
                raise ValueError("found_factor and cofactor product must equal target_n")
        if self.result_kind == MERGE_RESULT_PRIME_FACTORIZATION and self.prime_factorization_ref is None:
            raise ValueError("prime_factorization_result requires prime_factorization_ref")
        if self.result_kind == MERGE_RESULT_NONTRIVIAL_FACTOR and self.prime_factorization_ref is not None:
            raise ValueError("nontrivial_factor_found must not include prime_factorization_ref")
        _require_non_empty("created_at", self.created_at)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "merge_result_id": self.merge_result_id,
            "target_n": self.target_n,
            "coverage_id": self.coverage_id,
            "partition_params_digest": self.partition_params_digest,
            "result_kind": self.result_kind,
            "range_result_count": self.range_result_count,
            "required_slot_count": self.required_slot_count,
            "coverage_digest": self.coverage_digest,
            "slot_result_digests": list(self.slot_result_digests),
            "found_factor": self.found_factor,
            "cofactor": self.cofactor,
            "prime_factorization_ref": _json_value(self.prime_factorization_ref),
            "limitation_reason": self.limitation_reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class PrimeFactor:
    prime: str
    exponent: int

    def __post_init__(self) -> None:
        _parse_decimal_integer("prime", self.prime, min_value=2)
        _require_integer("exponent", self.exponent)
        if self.exponent < 1:
            raise ValueError("exponent must be a positive integer")

    def to_dict(self) -> JsonObject:
        return {"prime": self.prime, "exponent": self.exponent}


@dataclass(frozen=True, kw_only=True)
class PrimeFactorizationResult:
    result_id: str
    target_n: str
    prime_factors: list[PrimeFactor]
    source_kind: str
    created_at: str
    source_merge_result_id: str | None = None
    factor_multiset_digest: str | None = None
    product_check_passed: bool = True
    primality_check_policy_id: str = TRIAL_DIVISION_PRIMALITY_POLICY_ID
    primality_evidence: JsonObject | None = None
    schema_version: str = PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema(self.schema_version, PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION)
        _require_non_empty("result_id", self.result_id)
        target_n = _parse_decimal_integer("target_n", self.target_n, min_value=2)
        if not self.prime_factors:
            raise ValueError("prime_factors must not be empty")
        if self.source_kind not in {"prime_certificate", "semiprime_merge"}:
            raise ValueError("source_kind must be prime_certificate or semiprime_merge")
        if self.primality_check_policy_id != TRIAL_DIVISION_PRIMALITY_POLICY_ID:
            raise ValueError("primality_check_policy_id must be factorization.trial_division_primality.v1")
        if self.product_check_passed is not True:
            raise ValueError("product_check_passed must be true")
        factor_dicts = [self._normalize_factor(item) for item in self.prime_factors]
        factor_values = [int(item["prime"]) for item in factor_dicts]
        if factor_values != sorted(factor_values):
            raise ValueError("prime_factors must be in numeric ascending order")
        if len(set(factor_values)) != len(factor_values):
            raise ValueError("prime_factors must contain unique prime entries")
        _validate_primality_evidence(
            self.primality_evidence,
            expected_prime_values=factor_values,
            policy_id=self.primality_check_policy_id,
        )
        product = 1
        for item in factor_dicts:
            prime_value = int(item["prime"])
            exponent = int(item["exponent"])
            product *= prime_value**exponent
        if product != target_n:
            raise ValueError("prime factor product must equal target_n")
        _set_or_check_digest(self, "factor_multiset_digest", factor_dicts)
        _require_non_empty("created_at", self.created_at)

    def _normalize_factor(self, item: PrimeFactor) -> JsonObject:
        if not isinstance(item, PrimeFactor):
            raise TypeError("prime_factors must contain PrimeFactor objects")
        return item.to_dict()

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "target_n": self.target_n,
            "prime_factors": [item.to_dict() for item in self.prime_factors],
            "factor_multiset_digest": self.factor_multiset_digest,
            "product_check_passed": self.product_check_passed,
            "primality_check_policy_id": self.primality_check_policy_id,
            "primality_evidence": _json_value(self.primality_evidence),
            "source_kind": self.source_kind,
            "source_merge_result_id": self.source_merge_result_id,
            "created_at": self.created_at,
        }


def canonical_json_digest(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{sha256(encoded).hexdigest()}"


def _set_or_check_digest(instance: object, field_name: str, data: Any) -> None:
    expected = canonical_json_digest(data)
    current = getattr(instance, field_name)
    if current is None:
        object.__setattr__(instance, field_name, expected)
        return
    if current != expected:
        raise ValueError(f"{field_name} must match canonical JSON digest")


def _parse_decimal_integer(field_name: str, value: str, *, min_value: int) -> int:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a decimal string")
    if not value or not value.isdecimal():
        raise ValueError(f"{field_name} must be a decimal string")
    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{field_name} must be a decimal string without leading zeros")
    parsed = int(value)
    if parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}")
    return parsed


def _require_integer(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")


def _require_schema(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"schema_version must be {expected}")


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _validate_primality_evidence(
    evidence: JsonObject | None,
    *,
    expected_prime_values: list[int],
    policy_id: str,
) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("primality_evidence is required for prime_factors")
    if evidence.get("policy_id") != policy_id:
        raise ValueError("primality_evidence policy_id mismatch")
    if evidence.get("verification_scope") not in {
        "merge_policy_budgeted_check",
        "direct_small_prime_check",
    }:
        raise ValueError("primality_evidence verification_scope mismatch")
    values = evidence.get("verified_prime_values")
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("primality_evidence verified_prime_values must be decimal strings")
    if values != [str(value) for value in expected_prime_values]:
        raise ValueError("primality_evidence verified_prime_values mismatch")


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
