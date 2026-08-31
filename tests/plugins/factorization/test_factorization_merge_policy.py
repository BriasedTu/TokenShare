import pytest

from tokenshare.plugins.factorization import models as factor_models
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.merge_policy import (
    RangeSlotMergeInput,
    merge_required_range_results,
)
from tokenshare.plugins.factorization.models import FactorIntegerSubject, RangeResult
from tokenshare.plugins.factorization.schemas import (
    MERGE_RESULT_NONTRIVIAL_FACTOR,
    MERGE_RESULT_PRIME_CERTIFICATE,
    MERGE_RESULT_PRIME_FACTORIZATION,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)
from tokenshare.plugins.factorization.split_strategy import build_factorization_split_plan


NOW = "2026-06-27T00:00:00Z"


def test_merge_policy_outputs_prime_certificate_when_all_ranges_have_no_factor() -> None:
    plan = _build_split_plan(target_n="101")
    slot_inputs = _slot_inputs(plan)

    result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_prime_101",
        created_at=NOW,
    )

    assert result.expected_output_resolvable is True
    assert result.merge_result.result_kind == MERGE_RESULT_PRIME_CERTIFICATE
    assert result.merge_result.prime_factorization_ref is not None
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "101", "exponent": 1}
    ]


def test_merge_policy_outputs_prime_factorization_for_semiprime_factor_pair() -> None:
    plan = _build_split_plan(target_n="221")
    slot_inputs = _slot_inputs(plan, found_by_child_index={3: "13"})

    result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_semiprime_221",
        created_at=NOW,
    )

    assert result.expected_output_resolvable is True
    assert result.merge_result.result_kind == MERGE_RESULT_PRIME_FACTORIZATION
    assert result.merge_result.found_factor == "13"
    assert result.merge_result.cofactor == "17"
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "13", "exponent": 1},
        {"prime": "17", "exponent": 1},
    ]


def test_merge_policy_uses_budgeted_primality_evidence_without_model_rescan() -> None:
    plan = _build_split_plan(target_n="221")
    slot_inputs = _slot_inputs(plan, found_by_child_index={3: "13"})

    assert not hasattr(factor_models, "_is_prime")

    result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_semiprime_221_no_rescan",
        created_at=NOW,
    )

    assert result.expected_output_resolvable is True
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["primality_evidence"] == {
        "policy_id": "factorization.trial_division_primality.v1",
        "verified_prime_values": ["13", "17"],
        "verification_scope": "merge_policy_budgeted_check",
    }


def test_merge_policy_rejects_missing_or_duplicate_range_slot() -> None:
    plan = _build_split_plan(target_n="101")
    slot_inputs = _slot_inputs(plan)

    with pytest.raises(ValueError, match="missing required range slots"):
        merge_required_range_results(
            merge_plan=plan.merge_plan,
            slot_results=slot_inputs[:-1],
            merge_unit_id="merge_unit_missing_slot",
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="duplicate range slot"):
        merge_required_range_results(
            merge_plan=plan.merge_plan,
            slot_results=[*slot_inputs, slot_inputs[0]],
            merge_unit_id="merge_unit_duplicate_slot",
            created_at=NOW,
        )


def test_merge_policy_rejects_range_result_bound_to_wrong_slot() -> None:
    plan = _build_split_plan(target_n="101")
    slot_inputs = _slot_inputs(plan)
    swapped_inputs = [
        RangeSlotMergeInput(
            slot_key=slot_inputs[0].slot_key,
            range_result=slot_inputs[1].range_result,
            canonical_output_digest=slot_inputs[1].canonical_output_digest,
        ),
        RangeSlotMergeInput(
            slot_key=slot_inputs[1].slot_key,
            range_result=slot_inputs[0].range_result,
            canonical_output_digest=slot_inputs[0].canonical_output_digest,
        ),
        *slot_inputs[2:],
    ]

    with pytest.raises(ValueError, match="slot range result mismatch"):
        merge_required_range_results(
            merge_plan=plan.merge_plan,
            slot_results=swapped_inputs,
            merge_unit_id="merge_unit_swapped_slot",
            created_at=NOW,
        )


def test_merge_policy_does_not_resolve_composite_cofactor_as_final_result() -> None:
    plan = _build_split_plan(target_n="84")
    slot_inputs = _slot_inputs(
        plan,
        found_by_child_index={
            0: "2",
            1: "4",
            2: "6",
        },
    )

    result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_composite_84",
        created_at=NOW,
    )

    assert result.expected_output_resolvable is False
    assert result.merge_result.result_kind == MERGE_RESULT_NONTRIVIAL_FACTOR
    assert result.merge_result.found_factor == "2"
    assert result.merge_result.cofactor == "42"
    assert result.merge_result.prime_factorization_ref is None
    assert result.merge_result.limitation_reason == (
        "composite_cofactor_requires_future_recursive_resolution"
    )
    assert result.prime_factorization_result is None


def test_merge_policy_does_not_run_unbounded_primality_check() -> None:
    plan = _build_split_plan(target_n="2000006")
    slot_inputs = _slot_inputs(plan, found_by_child_index={0: "2"})

    result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_large_cofactor",
        created_at=NOW,
        primality_recheck_max_divisors=10,
    )

    assert result.expected_output_resolvable is False
    assert result.merge_result.result_kind == MERGE_RESULT_NONTRIVIAL_FACTOR
    assert result.merge_result.found_factor == "2"
    assert result.merge_result.cofactor == "1000003"
    assert result.merge_result.prime_factorization_ref is None
    assert result.merge_result.limitation_reason == "primality_check_budget_exceeded"
    assert result.prime_factorization_result is None


def _build_split_plan(*, target_n: str):
    subject = FactorIntegerSubject(
        subject_id=f"factor_subject:task_factor:unit_factor:{target_n}",
        task_id="task_factor",
        unit_id="unit_factor",
        target_n=target_n,
        source_kind="root_input",
        source_ref={"artifact_id": f"root_input_{target_n}", "content_hash": "sha256:root"},
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )
    return build_factorization_split_plan(
        subject=subject,
        canonical_selection_id=f"canonical_selection:task_factor:unit_factor:{target_n}",
        canonical_output_bundle_digest=f"sha256:factor_subject_bundle_{target_n}",
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        expansion_scope_hash=f"sha256:factorization_scope_{target_n}",
        expansion_decision_id=f"expansion_decision:sha256_factorization_scope_{target_n}",
        requested_child_count=4,
        max_children_per_unit=8,
        created_at=NOW,
    )


def _slot_inputs(plan, *, found_by_child_index: dict[int, str] | None = None):
    found_by_child_index = found_by_child_index or {}
    slots_by_child_index = {
        int(slot["source_child_logical_key"].rsplit(":", 1)[1]): slot
        for slot in plan.merge_plan.required_slots
    }
    inputs = []
    for range_input in plan.partition.ranges:
        factor = found_by_child_index.get(range_input.child_index)
        range_result = _range_result(range_input, factor=factor)
        slot = slots_by_child_index[range_input.child_index]
        inputs.append(
            RangeSlotMergeInput(
                slot_key=slot["slot_key"],
                range_result=range_result,
                canonical_output_digest=f"sha256:range_result_{range_input.child_index}",
            )
        )
    return inputs


def _range_result(range_input, *, factor: str | None) -> RangeResult:
    result_kind = RANGE_RESULT_FOUND_FACTOR if factor is not None else RANGE_RESULT_NO_FACTOR
    cofactor = str(int(range_input.target_n) // int(factor)) if factor is not None else None
    return RangeResult(
        range_result_id=(
            f"range_result:unit_{range_input.child_index}:attempt_1:"
            f"{range_input.coverage_id}:{range_input.child_index}"
        ),
        result_kind=result_kind,
        target_n=range_input.target_n,
        range_start=range_input.range_start,
        range_end=range_input.range_end,
        coverage_id=range_input.coverage_id,
        child_index=range_input.child_index,
        partition_params_digest=range_input.partition_params_digest,
        found_factor=factor,
        cofactor=cofactor,
        checked_divisor_count=int(range_input.range_end) - int(range_input.range_start) + 1,
        executor_summary={"checked": "bounded range"},
        created_at=NOW,
    )
