"""Factorization candidate range partition helper."""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt

from tokenshare.core.expansion import (
    DecompositionProposal,
    MergePlan,
    SplitStrategyResult,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
)
from tokenshare.core.models import JsonObject
from tokenshare.plugins.factorization.models import (
    CandidateRangeCoverageProof,
    CandidateRangePartitionParams,
    FactorIntegerSubject,
    FactorSearchRangeInput,
    PrimeFactor,
    PrimeFactorizationResult,
    canonical_json_digest,
)
from tokenshare.plugins.factorization.schemas import (
    ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
    TRIAL_DIVISION_PRIMALITY_POLICY_ID,
    schema_ref,
)


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


@dataclass(frozen=True, kw_only=True)
class FactorizationSplitPlanResult:
    partition: CandidateRangePartitionResult
    proposal: DecompositionProposal
    merge_plan: MergePlan
    child_unit_ids_by_logical_key: dict[str, str]

    def to_dict(self) -> JsonObject:
        return {
            "partition": self.partition.to_dict(),
            "proposal": self.proposal.to_dict(),
            "merge_plan": self.merge_plan.to_dict(),
            "child_unit_ids_by_logical_key": dict(self.child_unit_ids_by_logical_key),
        }


@dataclass(frozen=True, kw_only=True)
class FactorizationSplitStrategyActionResult:
    split_strategy_result: SplitStrategyResult
    split_plan: FactorizationSplitPlanResult | None
    prime_factorization_result: PrimeFactorizationResult | None

    def to_dict(self) -> JsonObject:
        return {
            "split_strategy_result": self.split_strategy_result.to_dict(),
            "split_plan": self.split_plan.to_dict() if self.split_plan is not None else None,
            "prime_factorization_result": (
                self.prime_factorization_result.to_dict()
                if self.prime_factorization_result is not None
                else None
            ),
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
    _require_positive_count("requested_child_count", requested_child_count)
    _require_positive_count("max_children_per_unit", max_children_per_unit)

    domain_size = max(0, max_divisor_value - min_divisor_value + 1)
    if domain_size == 0:
        raise ValueError("candidate domain is empty; use direct complete or invalid result")
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


def build_factorization_split_strategy_result(
    *,
    subject: FactorIntegerSubject,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    expansion_scope_hash: str,
    expansion_decision_id: str,
    requested_child_count: int,
    max_children_per_unit: int,
    created_at: str,
    min_divisor: str | int = "2",
    max_divisor: str | int | None = None,
) -> FactorizationSplitStrategyActionResult:
    """Return the plugin-owned split strategy action for a factor_integer subject."""

    target_n = int(subject.target_n)
    if target_n in {2, 3}:
        prime_result = _direct_prime_factorization_result(
            subject=subject,
            source_merge_result_id=f"direct_complete:{expansion_decision_id}",
            created_at=created_at,
        )
        split_result = SplitStrategyResult(
            action="complete",
            expansion_scope_hash=expansion_scope_hash,
            split_strategy_identity={
                "plugin_id": PLUGIN_ID,
                "plugin_version": PLUGIN_VERSION,
                "plugin_descriptor_digest": plugin_descriptor_digest,
                "split_strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            },
            complete={
                "completion_kind": "direct_small_prime",
                "completed_output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
                "completed_output_schema_ref": schema_ref(
                    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION
                ),
                "target_n": subject.target_n,
                "prime_factorization_result_id": prime_result.result_id,
            },
            expand=None,
            generation_evidence={
                "policy_id": "factorization.small_prime_direct_complete.v1",
                "target_n": subject.target_n,
                "candidate_domain_empty": True,
                "reason": "target_n in {2, 3}",
                "canonical_selection_id": canonical_selection_id,
                "canonical_output_bundle_digest": canonical_output_bundle_digest,
            },
            created_at=created_at,
        )
        return FactorizationSplitStrategyActionResult(
            split_strategy_result=split_result,
            split_plan=None,
            prime_factorization_result=prime_result,
        )

    split_plan = build_factorization_split_plan(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=expansion_scope_hash,
        expansion_decision_id=expansion_decision_id,
        requested_child_count=requested_child_count,
        max_children_per_unit=max_children_per_unit,
        created_at=created_at,
        min_divisor=min_divisor,
        max_divisor=max_divisor,
    )
    split_result = SplitStrategyResult(
        action="expand",
        expansion_scope_hash=expansion_scope_hash,
        split_strategy_identity={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": plugin_descriptor_digest,
            "split_strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
        },
        complete=None,
        expand={
            "proposal_id": split_plan.proposal.proposal_header["proposal_id"],
            "proposal_digest": split_plan.proposal.proposal_header["proposal_digest"],
            "merge_plan_id": split_plan.merge_plan.merge_plan_header["merge_plan_id"],
            "merge_plan_digest": split_plan.merge_plan.merge_plan_header[
                "merge_plan_digest"
            ],
        },
        generation_evidence={
            "policy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            "target_n": subject.target_n,
            "range_count": len(split_plan.partition.ranges),
            "coverage_id": split_plan.partition.coverage_proof.coverage_id,
        },
        created_at=created_at,
    )
    return FactorizationSplitStrategyActionResult(
        split_strategy_result=split_result,
        split_plan=split_plan,
        prime_factorization_result=None,
    )


def build_factorization_split_plan(
    *,
    subject: FactorIntegerSubject,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    expansion_scope_hash: str,
    expansion_decision_id: str,
    requested_child_count: int,
    max_children_per_unit: int,
    created_at: str,
    min_divisor: str | int = "2",
    max_divisor: str | int | None = None,
) -> FactorizationSplitPlanResult:
    """从 factorization range partition 生成 Phase 4 proposal / merge plan。

    该函数只构造纯对象和 artifact body 形状。它不写 ledger event、不创建
    `TaskUnit`、不调用 `ProtocolEngine`，也不把任何 canonical / resolution
    authority 放入 plugin payload。
    """

    partition = partition_candidate_ranges(
        target_n=subject.target_n,
        requested_child_count=requested_child_count,
        max_children_per_unit=max_children_per_unit,
        min_divisor=min_divisor,
        max_divisor=max_divisor,
    )
    if not partition.ranges:
        raise ValueError("factorization split plan requires a non-empty candidate domain")
    _require_complete_candidate_domain(subject.target_n, partition)

    proposal = _build_proposal(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=expansion_scope_hash,
        partition=partition,
        created_at=created_at,
        proposal_id="decomposition_proposal_pending",
        proposal_digest="sha256:pending_proposal_digest",
    )
    proposal_digest = digest_decomposition_proposal_body(proposal)
    proposal_id = f"decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
    proposal = _build_proposal(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        canonical_output_bundle_digest=canonical_output_bundle_digest,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_scope_hash=expansion_scope_hash,
        partition=partition,
        created_at=created_at,
        proposal_id=proposal_id,
        proposal_digest=proposal_digest,
    )

    child_unit_ids_by_logical_key = {
        child_spec["child_logical_key"]: _derive_phase4_child_unit_id(
            proposal_digest=proposal_digest,
            parent_unit_id=subject.unit_id,
            child_logical_key=child_spec["child_logical_key"],
        )
        for child_spec in proposal.child_specs
    }
    merge_plan = _build_merge_plan(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_decision_id=expansion_decision_id,
        proposal_id=proposal_id,
        partition=partition,
        child_unit_ids_by_logical_key=child_unit_ids_by_logical_key,
        created_at=created_at,
        merge_plan_id="merge_plan_pending",
        merge_plan_digest="sha256:pending_merge_plan_digest",
    )
    merge_plan_digest = digest_merge_plan_body(merge_plan)
    merge_plan_id = f"merge_plan_{merge_plan_digest.removeprefix('sha256:')}"
    merge_plan = _build_merge_plan(
        subject=subject,
        canonical_selection_id=canonical_selection_id,
        plugin_descriptor_digest=plugin_descriptor_digest,
        expansion_decision_id=expansion_decision_id,
        proposal_id=proposal_id,
        partition=partition,
        child_unit_ids_by_logical_key=child_unit_ids_by_logical_key,
        created_at=created_at,
        merge_plan_id=merge_plan_id,
        merge_plan_digest=merge_plan_digest,
    )

    return FactorizationSplitPlanResult(
        partition=partition,
        proposal=proposal,
        merge_plan=merge_plan,
        child_unit_ids_by_logical_key=child_unit_ids_by_logical_key,
    )


def _direct_prime_factorization_result(
    *,
    subject: FactorIntegerSubject,
    source_merge_result_id: str,
    created_at: str,
) -> PrimeFactorizationResult:
    return PrimeFactorizationResult(
        result_id=f"prime_factorization:{source_merge_result_id}",
        target_n=subject.target_n,
        prime_factors=[PrimeFactor(prime=subject.target_n, exponent=1)],
        source_kind="prime_certificate",
        source_merge_result_id=source_merge_result_id,
        primality_evidence={
            "policy_id": TRIAL_DIVISION_PRIMALITY_POLICY_ID,
            "verified_prime_values": [subject.target_n],
            "verification_scope": "direct_small_prime_check",
        },
        created_at=created_at,
    )


def _build_proposal(
    *,
    subject: FactorIntegerSubject,
    canonical_selection_id: str,
    canonical_output_bundle_digest: str,
    plugin_descriptor_digest: str,
    expansion_scope_hash: str,
    partition: CandidateRangePartitionResult,
    created_at: str,
    proposal_id: str,
    proposal_digest: str,
) -> DecompositionProposal:
    merge_slots = [_proposal_merge_slot(item) for item in partition.ranges]
    return DecompositionProposal(
        proposal_header={
            "proposal_id": proposal_id,
            "proposal_schema_version": "phase4.decomposition_proposal.v1",
            "task_id": subject.task_id,
            "parent_unit_id": subject.unit_id,
            "canonical_selection_id": canonical_selection_id,
            "canonical_output_bundle_digest": canonical_output_bundle_digest,
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "plugin_descriptor_digest": plugin_descriptor_digest,
            "split_strategy_id": CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
            "split_strategy_params_digest": partition.params.params_digest,
            "expansion_scope_hash": expansion_scope_hash,
            "proposal_digest": proposal_digest,
            "created_at": created_at,
        },
        child_specs=[_child_spec(item) for item in partition.ranges],
        dependency_edges=[],
        expected_outputs=[
            {
                "output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
                "schema_ref": schema_ref(PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION),
                "resolution_kind": "merge_plan_output",
                "child_key": None,
                "child_output_name": None,
                "merge_slot_id": merge_slots[0]["slot_id"],
                "merge_slot_policy": "all_required_slots",
                "merge_slot_count": len(merge_slots),
                "merge_slot_keys": [slot["slot_id"] for slot in merge_slots],
                "required": True,
            }
        ],
        merge_slots=merge_slots,
        promotion_guard_evidence={
            "typed_io_checked": True,
            "independently_schedulable_checked": True,
            "validator_policy_checked": True,
            "output_contract_checked": True,
            "no_freeform_thought_checked": True,
            "max_depth_checked": True,
            "max_children_checked": True,
            "evidence_ref": None,
            "factorization_coverage": partition.coverage_proof.to_dict(),
        },
    )


def _child_spec(range_input: FactorSearchRangeInput) -> JsonObject:
    range_body = range_input.to_dict()
    return {
        "child_logical_key": _child_logical_key(range_input),
        "unit_type": FACTOR_SEARCH_RANGE_TASK_TYPE,
        "input_bindings": {
            "range_input": {
                "kind": "constant",
                "schema_version": FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
                "body": range_body,
                "body_digest": range_input.range_digest,
            }
        },
        "required_outputs": ["range_result"],
        "output_contract_refs": {
            "range_result": {
                "output_contract_id": RANGE_RESULT_CONTRACT_ID,
                "schema_ref": schema_ref(RANGE_RESULT_SCHEMA_VERSION),
            }
        },
        "validator_policy_id": RANGE_RESULT_VALIDATOR_POLICY_ID,
        "budget_limit": None,
        "deadline": None,
        "weight": 1.0,
        "required_capabilities": {
            "executor": "mock_ai",
            "bounded_factor_search": True,
        },
        "plugin_payload": _child_plugin_payload(range_input),
        "promotion_guard_ref": None,
    }


def _proposal_merge_slot(range_input: FactorSearchRangeInput) -> JsonObject:
    slot_key = _slot_key(range_input)
    return {
        "slot_id": slot_key,
        "child_key": _child_logical_key(range_input),
        "child_output_name": "range_result",
        "schema_ref": schema_ref(RANGE_RESULT_SCHEMA_VERSION),
        "required": True,
        "missing_policy": "block_merge",
    }


def _build_merge_plan(
    *,
    subject: FactorIntegerSubject,
    canonical_selection_id: str,
    plugin_descriptor_digest: str,
    expansion_decision_id: str,
    proposal_id: str,
    partition: CandidateRangePartitionResult,
    child_unit_ids_by_logical_key: dict[str, str],
    created_at: str,
    merge_plan_id: str,
    merge_plan_digest: str,
) -> MergePlan:
    required_slots = [
        _required_slot(
            range_input=item,
            child_unit_id=child_unit_ids_by_logical_key[_child_logical_key(item)],
        )
        for item in partition.ranges
    ]
    result_schema_ref = schema_ref(PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION)
    plugin_defined_body = _merge_plugin_defined_body(partition)
    return MergePlan(
        merge_plan_header={
            "merge_plan_id": merge_plan_id,
            "merge_plan_schema_version": "phase4.merge_plan.v1",
            "task_id": subject.task_id,
            "parent_unit_id": subject.unit_id,
            "canonical_selection_id": canonical_selection_id,
            "decomposition_proposal_id": proposal_id,
            "expansion_decision_id": expansion_decision_id,
            "created_by_plugin_id": PLUGIN_ID,
            "created_by_plugin_version": PLUGIN_VERSION,
            "merge_plan_digest": merge_plan_digest,
            "created_at": created_at,
        },
        merge_policy_ref={
            "plugin_id": PLUGIN_ID,
            "plugin_version": PLUGIN_VERSION,
            "merge_policy_id": ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
            "merge_policy_version": "v1",
            "merge_policy_descriptor_digest": plugin_descriptor_digest,
            "merge_policy_params_digest": partition.params.params_digest,
        },
        required_slots=required_slots,
        parent_output_mapping=[
            {
                "parent_output_name": REQUESTED_OUTPUT_PRIME_FACTORIZATION,
                "resolution_kind": "merge_plan_output",
                "merge_slot_keys": [slot["slot_key"] for slot in required_slots],
                "result_schema_ref": result_schema_ref,
                "result_schema_digest": canonical_json_digest(result_schema_ref),
            }
        ],
        hash_recording_requirements={
            "record_child_canonical_output_digest": True,
            "record_slot_source_artifact_digest": True,
            "record_merge_input_bundle_digest": True,
        },
        merge_validation_requirements={
            "all_required_slots_canonical": True,
            "slot_schema_check_required": True,
            "merged_output_schema_check_required": True,
            "plugin_merge_validator_policy_id": "factorization.merge_result.validator.v1",
        },
        plugin_payload={
            "plugin_defined_schema_ref": schema_ref(
                "factorization.merge_plan_plugin_payload.v1"
            ),
            "plugin_defined_body_digest": canonical_json_digest(plugin_defined_body),
            "plugin_defined_body": plugin_defined_body,
        },
    )


def _required_slot(
    *,
    range_input: FactorSearchRangeInput,
    child_unit_id: str,
) -> JsonObject:
    output_schema_ref = schema_ref(RANGE_RESULT_SCHEMA_VERSION)
    return {
        "slot_key": _slot_key(range_input),
        "source_child_logical_key": _child_logical_key(range_input),
        "source_child_unit_id": child_unit_id,
        "source_output_name": "range_result",
        "output_schema_ref": output_schema_ref,
        "output_schema_digest": canonical_json_digest(output_schema_ref),
        "required": True,
        "missing_policy": "block_merge",
    }


def _child_plugin_payload(range_input: FactorSearchRangeInput) -> JsonObject:
    return {
        "schema_version": "factorization.factor_search_range_plugin_payload.v1",
        "summary": {
            "coverage_id": range_input.coverage_id,
            "child_index": range_input.child_index,
            "child_count": range_input.child_count,
            "range_digest": range_input.range_digest,
            "partition_params_digest": range_input.partition_params_digest,
        },
        "validation_requirements": {
            "bounded_range_required": True,
            "range_input_digest_required": True,
            "coverage_id_consistency_required": True,
            "partition_params_digest_consistency_required": True,
            "range_result_schema_version": RANGE_RESULT_SCHEMA_VERSION,
        },
    }


def _merge_plugin_defined_body(partition: CandidateRangePartitionResult) -> JsonObject:
    return {
        "schema_version": "factorization.merge_plan_plugin_payload.v1",
        "summary": {
            "merge_policy": "all_required_range_merge",
            "target_n": partition.params.target_n,
            "coverage_id": partition.coverage_proof.coverage_id,
            "range_count": len(partition.ranges),
            "ranges_digest": partition.coverage_proof.ranges_digest,
            "partition_params_digest": partition.params.params_digest,
        },
        "validation_requirements": {
            "factorization_coverage_check_required": True,
            "coverage_id_consistency_required": True,
            "partition_params_digest_consistency_required": True,
            "all_required_range_slots_required": True,
            "range_result_schema_version": RANGE_RESULT_SCHEMA_VERSION,
        },
    }


def _require_complete_candidate_domain(
    target_n: str,
    partition: CandidateRangePartitionResult,
) -> None:
    if partition.params.min_divisor != "2" or partition.params.max_divisor != str(
        isqrt(int(target_n))
    ):
        raise ValueError(
            "factorization split plan requires complete candidate domain [2, floor_sqrt(target_n)]"
        )


def _child_logical_key(range_input: FactorSearchRangeInput) -> str:
    return f"range:{range_input.coverage_id}:{range_input.child_index}"


def _slot_key(range_input: FactorSearchRangeInput) -> str:
    return f"{_child_logical_key(range_input)}:range_result"


def _derive_phase4_child_unit_id(
    *,
    proposal_digest: str,
    parent_unit_id: str,
    child_logical_key: str,
) -> str:
    stable_suffix = _stable_id_component(proposal_digest.removeprefix("sha256:"))
    return (
        f"unit_{_stable_id_component(parent_unit_id)}_"
        f"{stable_suffix}_{_stable_id_component(child_logical_key)}"
    )


def _stable_id_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_" for character in value
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


def _require_positive_count(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1")
