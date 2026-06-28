import pytest

from tokenshare.core.expansion import (
    DecompositionProposal,
    MergePlan,
    SplitStrategyResult,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
)
from tokenshare.plugins.factorization import split_strategy as factor_split
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor
from tokenshare.plugins.factorization.models import FactorIntegerSubject
from tokenshare.plugins.factorization.schemas import (
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_SCHEMA_VERSION,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)


NOW = "2026-06-27T00:00:00Z"

FORBIDDEN_PLUGIN_PAYLOAD_KEYS = {
    "state",
    "initial_state",
    "desired_state",
    "task_state",
    "attempt_state",
    "resolution_status",
    "canonical_output_refs",
    "canonical_outputs_by_unit_id",
    "canonical_selection_id",
    "canonical_output_bundle_digest",
    "expected_output_refs",
    "merge_readiness",
    "resolved_output_ref",
    "output_resolution",
}


def test_factorization_split_generates_only_factor_search_range_children() -> None:
    result = _build_split_plan()

    assert isinstance(result.proposal, DecompositionProposal)
    assert isinstance(result.merge_plan, MergePlan)
    assert len(result.proposal.child_specs) == len(result.partition.ranges)
    assert len(result.proposal.child_specs) > 1
    assert result.proposal.dependency_edges == []

    for child_spec, range_input in zip(result.proposal.child_specs, result.partition.ranges):
        assert child_spec["unit_type"] == FACTOR_SEARCH_RANGE_TASK_TYPE
        assert child_spec["required_outputs"] == ["range_result"]
        assert child_spec["validator_policy_id"] == "factorization.range_result.validator.v1"
        assert child_spec["input_bindings"]["range_input"] == {
            "kind": "constant",
            "schema_version": FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
            "body": range_input.to_dict(),
            "body_digest": range_input.range_digest,
        }
        assert child_spec["output_contract_refs"]["range_result"]["schema_ref"][
            "schema_version"
        ] == RANGE_RESULT_SCHEMA_VERSION


def test_factorization_proposal_records_coverage_proof() -> None:
    result = _build_split_plan()
    coverage = result.proposal.promotion_guard_evidence["factorization_coverage"]

    assert coverage["schema_version"] == "factorization.candidate_range_coverage_proof.v1"
    assert coverage["coverage_id"] == result.partition.coverage_proof.coverage_id
    assert coverage["domain_start"] == "2"
    assert coverage["domain_end"] == result.partition.params.max_divisor
    assert coverage["range_count"] == len(result.partition.ranges)
    assert coverage["ranges_digest"] == result.partition.coverage_proof.ranges_digest
    assert coverage["no_gap"] is True
    assert coverage["no_overlap"] is True
    assert coverage["full_domain_covered"] is True
    assert coverage["sqrt_bound_checked"] is True
    assert result.proposal.proposal_header[
        "proposal_digest"
    ] == digest_decomposition_proposal_body(result.proposal)


def test_factorization_merge_slots_match_children_one_to_one() -> None:
    result = _build_split_plan()
    child_keys = [spec["child_logical_key"] for spec in result.proposal.child_specs]
    proposal_slots = result.proposal.merge_slots
    required_slots = result.merge_plan.required_slots

    assert len(proposal_slots) == len(child_keys)
    assert len(required_slots) == len(child_keys)
    assert [slot["child_key"] for slot in proposal_slots] == child_keys
    assert [slot["source_child_logical_key"] for slot in required_slots] == child_keys
    assert all(slot["required"] is True for slot in proposal_slots)
    assert all(slot["required"] is True for slot in required_slots)
    assert all(slot["missing_policy"] == "block_merge" for slot in proposal_slots)
    assert all(slot["missing_policy"] == "block_merge" for slot in required_slots)
    assert all(slot["child_output_name"] == "range_result" for slot in proposal_slots)
    assert all(slot["source_output_name"] == "range_result" for slot in required_slots)
    assert [
        slot["source_child_unit_id"] for slot in required_slots
    ] == [result.child_unit_ids_by_logical_key[key] for key in child_keys]

    parent_mapping = result.merge_plan.parent_output_mapping[0]
    assert parent_mapping["parent_output_name"] == REQUESTED_OUTPUT_PRIME_FACTORIZATION
    assert parent_mapping["result_schema_ref"]["schema_version"] == (
        PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION
    )
    assert parent_mapping["merge_slot_keys"] == [
        slot["slot_key"] for slot in required_slots
    ]
    expected_output = result.proposal.expected_outputs[0]
    assert expected_output["merge_slot_id"] == proposal_slots[0]["slot_id"]
    assert expected_output["merge_slot_policy"] == "all_required_slots"
    assert expected_output["merge_slot_count"] == len(proposal_slots)
    assert expected_output["merge_slot_keys"] == [
        slot["slot_id"] for slot in proposal_slots
    ]
    assert result.merge_plan.merge_plan_header[
        "merge_plan_digest"
    ] == digest_merge_plan_body(result.merge_plan)


def test_factorization_proposal_contains_no_authoritative_resolution_in_plugin_payload() -> None:
    result = _build_split_plan()

    for child_spec in result.proposal.child_specs:
        payload = child_spec["plugin_payload"]
        assert set(payload) == {"schema_version", "summary", "validation_requirements"}
        _assert_no_forbidden_plugin_payload_keys(payload)

    merge_payload = result.merge_plan.plugin_payload
    assert set(merge_payload["plugin_defined_body"]) == {
        "schema_version",
        "summary",
        "validation_requirements",
    }
    _assert_no_forbidden_plugin_payload_keys(merge_payload)


def test_factorization_split_strategy_returns_complete_for_two_or_three() -> None:
    result = factor_split.build_factorization_split_strategy_result(
        subject=_subject("3"),
        canonical_selection_id="canonical_selection:task_factor:unit_factor:3",
        canonical_output_bundle_digest="sha256:factor_subject_bundle_3",
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:factorization_scope_3",
        expansion_decision_id="expansion_decision:sha256_factorization_scope_3",
        requested_child_count=4,
        max_children_per_unit=8,
        created_at=NOW,
    )

    assert isinstance(result.split_strategy_result, SplitStrategyResult)
    assert result.split_strategy_result.action == "complete"
    assert result.split_strategy_result.expand is None
    assert result.split_strategy_result.complete["completed_output_name"] == (
        REQUESTED_OUTPUT_PRIME_FACTORIZATION
    )
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "3", "exponent": 1}
    ]
    assert result.split_plan is None


def _build_split_plan():
    return factor_split.build_factorization_split_plan(
        subject=_subject("221"),
        canonical_selection_id="canonical_selection:task_factor:unit_factor",
        canonical_output_bundle_digest="sha256:factor_subject_bundle",
        plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:factorization_scope_221",
        expansion_decision_id="expansion_decision:sha256_factorization_scope_221",
        requested_child_count=4,
        max_children_per_unit=8,
        created_at=NOW,
    )


def _subject(target_n: str) -> FactorIntegerSubject:
    return FactorIntegerSubject(
        subject_id=f"factor_subject:task_factor:unit_factor:{target_n}",
        task_id="task_factor",
        unit_id="unit_factor",
        target_n=target_n,
        source_kind="root_input",
        source_ref={"artifact_id": f"root_input_{target_n}", "content_hash": "sha256:root"},
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )


def _assert_no_forbidden_plugin_payload_keys(value) -> None:
    if isinstance(value, dict):
        blocked = FORBIDDEN_PLUGIN_PAYLOAD_KEYS.intersection(value)
        assert not blocked
        for item in value.values():
            _assert_no_forbidden_plugin_payload_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_plugin_payload_keys(item)


def test_factorization_split_plan_rejects_partial_candidate_domain_for_parent_output() -> None:
    subject = FactorIntegerSubject(
        subject_id="factor_subject:task_factor:unit_factor:221",
        task_id="task_factor",
        unit_id="unit_factor",
        target_n="221",
        source_kind="root_input",
        source_ref={"artifact_id": "root_input_221", "content_hash": "sha256:root"},
        requested_output=REQUESTED_OUTPUT_PRIME_FACTORIZATION,
        created_at=NOW,
    )

    with pytest.raises(ValueError, match="complete candidate domain"):
        factor_split.build_factorization_split_plan(
            subject=subject,
            canonical_selection_id="canonical_selection:task_factor:unit_factor",
            canonical_output_bundle_digest="sha256:factor_subject_bundle",
            plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
            expansion_scope_hash="sha256:factorization_scope_221",
            expansion_decision_id="expansion_decision:sha256_factorization_scope_221",
            requested_child_count=4,
            max_children_per_unit=8,
            min_divisor="3",
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="complete candidate domain"):
        factor_split.build_factorization_split_plan(
            subject=subject,
            canonical_selection_id="canonical_selection:task_factor:unit_factor",
            canonical_output_bundle_digest="sha256:factor_subject_bundle",
            plugin_descriptor_digest=build_factorization_plugin_descriptor().descriptor_digest,
            expansion_scope_hash="sha256:factorization_scope_221",
            expansion_decision_id="expansion_decision:sha256_factorization_scope_221",
            requested_child_count=4,
            max_children_per_unit=8,
            max_divisor="10",
            created_at=NOW,
        )
