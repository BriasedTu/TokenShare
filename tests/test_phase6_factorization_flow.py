from __future__ import annotations

from tokenshare.storage.events import EventType
from tokenshare.plugins.factorization.schemas import (
    ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
    MERGE_RESULT_PRIME_CERTIFICATE,
    MERGE_RESULT_PRIME_FACTORIZATION,
    RANGE_RESULT_FOUND_FACTOR,
    RANGE_RESULT_NO_FACTOR,
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)
from tokenshare.plugins.factorization.fixtures import run_factorization_fixture_flow


def test_factorization_prime_fixture_expands_ranges_merges_prime_certificate_and_settles(
    tmp_path,
) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=97,
        requested_child_count=3,
        root_budget=13,
    )

    assert result.root_canonical.canonical_selection.canonical_output_refs.keys() == {
        "factor_integer_subject"
    }
    assert result.subject_ref.metadata["output_name"] == "factor_integer_subject"
    assert result.subject.target_n == "97"
    assert [item.range_result.result_kind for item in result.range_results] == [
        RANGE_RESULT_NO_FACTOR,
        RANGE_RESULT_NO_FACTOR,
        RANGE_RESULT_NO_FACTOR,
    ]
    assert len(result.range_canonical_events) == len(result.range_results)
    assert all(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        for event in result.range_canonical_events
    )
    assert result.merge_policy_result.merge_result.result_kind == MERGE_RESULT_PRIME_CERTIFICATE
    assert result.merge_policy_result.expected_output_resolvable is True
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "97", "exponent": 1}
    ]
    assert result.resolution.expected_output_name == REQUESTED_OUTPUT_PRIME_FACTORIZATION
    assert result.parent_completion is not None
    assert result.parent_completion.task_unit.state.value == "Completed"
    assert result.settlement is not None
    assert result.settlement.settlement_record.entry_count == 2
    assert result.settlement.settlement_record.total_reward == 13

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.MERGE_RECORDED in event_types
    assert EventType.EXPECTED_OUTPUT_RESOLVED in event_types
    assert EventType.CONTRIBUTION_STATE_CHANGED in event_types
    assert EventType.SETTLEMENT_RECORDED in event_types


def test_factorization_root_subject_is_canonical_under_subject_output_name(tmp_path) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
    )

    root_outputs = result.root_canonical.canonical_selection.canonical_output_refs

    assert set(root_outputs) == {"factor_integer_subject"}
    assert REQUESTED_OUTPUT_PRIME_FACTORIZATION not in root_outputs
    assert root_outputs["factor_integer_subject"].content_hash == result.subject_ref.content_hash
    assert result.expand_result.expected_output_refs[0].output_name == (
        REQUESTED_OUTPUT_PRIME_FACTORIZATION
    )


def test_factorization_small_prime_fixture_completes_directly_without_ranges(tmp_path) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=2,
        requested_child_count=3,
        root_budget=11,
    )

    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "2", "exponent": 1}
    ]
    assert result.expand_result is None
    assert result.range_results == ()
    assert result.merge_task_creations == ()
    assert result.merge_policy_result is None
    assert result.merge_resolution is None
    assert result.parent_completion is not None
    assert result.parent_completion.task_unit.state.value == "Completed"
    completion_refs = result.parent_completion.events[0].payload["action_body"][
        "completion_evidence"
    ]["completed_output_refs"]
    assert completion_refs[REQUESTED_OUTPUT_PRIME_FACTORIZATION]["content_hash"] == (
        result.prime_factorization_ref.content_hash
    )
    assert result.settlement is not None
    assert result.settlement.settlement_record.total_reward == 11

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.SPLIT_STRATEGY_INVOCATION_RECORDED in event_types
    assert EventType.EXPANSION_DECISION_RECORDED in event_types
    assert EventType.TASK_UNIT_STATE_CHANGED in event_types
    assert EventType.DECOMPOSITION_PROPOSAL_RECORDED not in event_types
    assert EventType.TASK_EXPANDED not in event_types
    assert EventType.MERGE_TASK_LINK_RECORDED not in event_types
    assert EventType.MERGE_RECORDED not in event_types


def test_factorization_semiprime_fixture_finds_factor_merges_final_result_and_settles(
    tmp_path,
) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
        root_budget=17,
    )

    assert any(
        item.range_result.result_kind == RANGE_RESULT_FOUND_FACTOR
        and item.range_result.found_factor == "7"
        and item.range_result.cofactor == "13"
        for item in result.range_results
    )
    assert any(
        item.range_result.result_kind == RANGE_RESULT_NO_FACTOR
        for item in result.range_results
    )
    assert len(result.range_verifications) == len(result.range_results)
    assert all(item.report.status == "passed" for item in result.range_verifications)
    assert all(
        observation["range_start"] <= observation["checked_start"]
        and observation["checked_end"] <= observation["range_end"]
        for observation in result.executor_observations
    )
    assert result.merge_policy_result.merge_result.result_kind == MERGE_RESULT_PRIME_FACTORIZATION
    assert result.merge_policy_result.expected_output_resolvable is True
    assert result.prime_factorization_result is not None
    assert result.prime_factorization_result.to_dict()["prime_factors"] == [
        {"prime": "7", "exponent": 1},
        {"prime": "13", "exponent": 1},
    ]
    assert result.resolution.resolved_output_ref["content_hash"] == (
        result.prime_factorization_ref.content_hash
    )
    assert result.parent_completion.task_unit.state.value == "Completed"
    assert result.settlement.settlement_record.total_reward == 17


def test_factorization_range_requests_include_plugin_owned_prompt_packages(tmp_path) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
    )

    assert result.range_results
    for execution in result.range_results:
        request = execution.request.request
        assert request.execution_instruction_ref is not None
        assert request.prompt_package_ref is not None
        assert request.prompt_package_ref.artifact_type == "PromptPackage"
        assert request.prompt_package_ref.artifact_schema_id == "phase3.prompt_package"
        prompt_body = result.store.read_bytes(request.prompt_package_ref).decode("utf-8")
        assert "Target integer: 91" in prompt_body
        assert (
            f"Search divisor range: {execution.range_input.range_start} "
            f"to {execution.range_input.range_end} inclusive"
        ) in prompt_body
        assert "Do not search outside the assigned range" in prompt_body
        assert "Do not create child tasks" in prompt_body


def test_factorization_semiprime_flow_waits_for_all_required_ranges_before_merge(
    tmp_path,
) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=91,
        requested_child_count=3,
        stop_after_canonical_range_count=1,
    )

    assert len(result.range_canonical_events) == 1
    assert not result.merge_task_creations
    assert result.merge_policy_result is None
    assert result.merge_resolution is None
    assert result.parent_completion is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.MERGE_TASK_LINK_RECORDED not in event_types
    assert EventType.MERGE_RECORDED not in event_types
    assert EventType.EXPECTED_OUTPUT_RESOLVED not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types


def test_factorization_composite_cofactor_full_flow_keeps_parent_unresolved(tmp_path) -> None:
    result = run_factorization_fixture_flow(
        tmp_path,
        target_n=84,
        requested_child_count=4,
    )

    assert result.merge_policy_result is not None
    assert result.merge_policy_result.expected_output_resolvable is False
    assert result.merge_result_ref is not None
    assert result.prime_factorization_result is None
    assert result.prime_factorization_ref is None
    assert result.merge_submission is not None
    assert result.merge_record.merge_policy_id == ALL_REQUIRED_RANGE_MERGE_POLICY_ID
    assert set(result.merge_canonical.canonical_selection.canonical_output_refs) == {
        "factorization_result"
    }
    assert result.merge_record is not None
    assert result.resolution is None
    assert result.merge_resolution is None
    assert result.parent_completion is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.MERGE_TASK_LINK_RECORDED in event_types
    assert EventType.MERGE_RECORDED not in event_types
    assert EventType.EXPECTED_OUTPUT_RESOLVED not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types
