from __future__ import annotations

from tokenshare.storage.events import EventType
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.fixtures import run_factorization_fixture_flow
from tokenshare.plugins.factorization.merge_policy import (
    RangeSlotMergeInput,
    merge_required_range_results,
)
from tokenshare.plugins.factorization.models import FactorIntegerSubject, RangeResult
from tokenshare.plugins.factorization.schemas import (
    MERGE_RESULT_NONTRIVIAL_FACTOR,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)
from tokenshare.plugins.factorization.split_strategy import build_factorization_split_plan


NOW = "2026-06-28T00:00:00Z"
COMPOSITE_COFACTOR_LIMITATION = "composite_cofactor_requires_future_recursive_resolution"


def test_factorization_does_not_claim_early_success_before_all_required_ranges(
    tmp_path,
) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
        stop_after_canonical_range_count=1,
    )
    metadata = build_factorization_plugin_descriptor().to_dict()["metadata"]

    assert metadata["first_slice_limitations_detail"]["merge_readiness_policy"] == (
        "all_required_ranges_canonical"
    )
    assert metadata["first_slice_limitations_detail"][
        "factor_found_before_all_required_ranges"
    ] == "not_early_success"
    assert len(result.range_canonical_events) == 1
    assert result.merge_task_creations == ()
    assert result.merge_policy_result is None
    assert result.merge_resolution is None
    assert result.parent_completion is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.MERGE_TASK_LINK_RECORDED not in event_types
    assert EventType.MERGE_RECORDED not in event_types
    assert EventType.EXPECTED_OUTPUT_RESOLVED not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types


def test_factorization_does_not_prune_sibling_ranges_in_first_slice(tmp_path) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
    )
    metadata = build_factorization_plugin_descriptor().to_dict()["metadata"]

    assert metadata["first_slice_limitations_detail"]["phase5_subtree_pruning_usage"] == {
        "uses_subtree_pruning_for_factorization_early_success": False,
        "sibling_range_pruning": "not_in_first_slice",
    }
    assert len(result.range_canonical_events) == len(result.expand_result.child_units)
    assert any(
        execution.range_result.result_kind == RANGE_RESULT_FOUND_FACTOR
        for execution in result.range_executions
    )
    found_index = next(
        index
        for index, execution in enumerate(result.range_executions)
        if execution.range_result.result_kind == RANGE_RESULT_FOUND_FACTOR
    )
    assert any(
        execution.range_result.result_kind == RANGE_RESULT_NO_FACTOR
        for execution in result.range_executions[found_index + 1 :]
    )

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.SUBTREE_PRUNED not in event_types


def test_factorization_composite_cofactor_requires_future_recursive_resolution() -> None:
    plan = _build_split_plan(target_n="84")
    slot_inputs = _slot_inputs(
        plan,
        found_by_child_index={
            0: "2",
            1: "4",
            2: "6",
        },
    )
    metadata = build_factorization_plugin_descriptor().to_dict()["metadata"]

    merge_result = merge_required_range_results(
        merge_plan=plan.merge_plan,
        slot_results=slot_inputs,
        merge_unit_id="merge_unit_composite_84",
        created_at=NOW,
    )

    assert metadata["first_slice_limitations_detail"][
        "composite_cofactor_limitation_reason"
    ] == COMPOSITE_COFACTOR_LIMITATION
    assert merge_result.expected_output_resolvable is False
    assert merge_result.resolved_output_name is None
    assert merge_result.prime_factorization_result is None
    assert merge_result.merge_result.result_kind == MERGE_RESULT_NONTRIVIAL_FACTOR
    assert merge_result.merge_result.found_factor == "2"
    assert merge_result.merge_result.cofactor == "42"
    assert merge_result.merge_result.prime_factorization_ref is None
    assert merge_result.merge_result.limitation_reason == COMPOSITE_COFACTOR_LIMITATION


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
        slot = slots_by_child_index[range_input.child_index]
        inputs.append(
            RangeSlotMergeInput(
                slot_key=slot["slot_key"],
                range_result=_range_result(range_input, factor=factor),
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
