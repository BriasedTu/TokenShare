from __future__ import annotations

from tokenshare.plugins.lean_proof.fixtures import (
    run_lean_decomposition_fixture_flow,
    run_lean_direct_proof_fixture_flow,
    run_lean_unsupported_decomposition_fixture_flow,
)
from tokenshare.plugins.lean_proof.schemas import PROOF_ARTIFACT_OUTPUT_NAME
from tokenshare.storage.events import EventType


def test_lean_direct_proof_fixture_verifies_canonicalizes_completes_and_settles(
    tmp_path,
) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path, root_budget=7)

    assert result.checker_report.status.value == "accepted"
    assert result.checker_report.proof_artifact_ref is not None
    assert result.canonical_proof_ref.artifact_type == "canonical_output"
    assert result.root_canonical.canonical_selection.canonical_output_refs == {
        PROOF_ARTIFACT_OUTPUT_NAME: result.canonical_proof_ref
    }
    assert result.complete_result.task_unit.state.value == "Completed"
    assert result.settlement.settlement_record.total_reward == 7
    assert result.settlement.settlement_record.entry_count == 1

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.REGISTRY_SNAPSHOT_RECORDED in event_types
    assert EventType.EXECUTION_REQUEST_RECORDED in event_types
    assert EventType.EXECUTION_SUBMISSION_RECORDED in event_types
    assert EventType.VERIFICATION_RECORDED in event_types
    assert EventType.CANONICAL_OUTPUTS_BOUND in event_types
    assert EventType.SPLIT_STRATEGY_INVOCATION_RECORDED in event_types
    assert EventType.EXPANSION_DECISION_RECORDED in event_types
    assert EventType.CONTRIBUTION_STATE_CHANGED in event_types
    assert EventType.SETTLEMENT_RECORDED in event_types


def test_lean_direct_proof_invalid_candidate_does_not_pollute_canonical(tmp_path) -> None:
    result = run_lean_direct_proof_fixture_flow(tmp_path, proof_kind="invalid")

    assert result.checker_report.status.value == "rejected"
    assert result.verification.report.status == "rejected"
    assert result.root_canonical is None
    assert result.canonical_proof_ref is None
    assert result.complete_result is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.VERIFICATION_RECORDED in event_types
    assert EventType.CANONICAL_OUTPUTS_BOUND not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types


def test_lean_decomposition_fixture_splits_child_goals_checks_children_merges_and_settles(
    tmp_path,
) -> None:
    result = run_lean_decomposition_fixture_flow(tmp_path, root_budget=19)

    assert result.split_report.certificate is not None
    assert result.split_report.certificate.rule_id == "lean_split.conjunction_goal.v1"
    assert len(result.child_results) == 2
    assert all(child.accepted and child.merge_ready for child in result.child_results)
    assert len(result.child_canonicals) == 2
    assert result.merge_task_creation is not None
    assert result.merge_policy_result is not None
    assert result.merge_policy_result.accepted is True
    assert result.merge_canonical is not None
    assert result.merge_record is not None
    assert result.merge_resolution is not None
    assert result.parent_completion is not None
    assert result.parent_completion.task_unit.state.value == "Completed"
    assert result.settlement is not None
    assert result.settlement.settlement_record.total_reward == 19
    assert result.settlement.settlement_record.entry_count == 2

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.SPLIT_STRATEGY_INVOCATION_RECORDED in event_types
    assert EventType.DECOMPOSITION_PROPOSAL_RECORDED in event_types
    assert EventType.MERGE_PLAN_RECORDED in event_types
    assert EventType.TASK_EXPANDED in event_types
    assert EventType.MERGE_TASK_LINK_RECORDED in event_types
    assert EventType.MERGE_RECORDED in event_types
    assert EventType.EXPECTED_OUTPUT_RESOLVED in event_types
    assert EventType.SETTLEMENT_RECORDED in event_types


def test_lean_decomposition_fixture_blocks_merge_until_all_required_child_proofs_canonical(
    tmp_path,
) -> None:
    result = run_lean_decomposition_fixture_flow(
        tmp_path,
        stop_after_canonical_child_count=1,
    )

    assert len(result.child_canonicals) == 1
    assert result.merge_task_creation is None
    assert result.merge_policy_result is None
    assert result.merge_record is None
    assert result.merge_resolution is None
    assert result.parent_completion is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.TASK_EXPANDED in event_types
    assert EventType.MERGE_TASK_LINK_RECORDED not in event_types
    assert EventType.MERGE_RECORDED not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types


def test_lean_unsupported_decomposition_returns_structured_unsupported_without_false_success(
    tmp_path,
) -> None:
    result = run_lean_unsupported_decomposition_fixture_flow(tmp_path)

    assert result.split_report.certificate is not None
    assert result.split_report.certificate.split_kind == "unsupported"
    assert result.unsupported_reason
    assert result.expand_result is None
    assert result.complete_result is None
    assert result.settlement is None

    event_types = [event.event_type for event in result.ledger.read_all()]
    assert EventType.SPLIT_STRATEGY_INVOCATION_RECORDED in event_types
    assert EventType.DECOMPOSITION_PROPOSAL_RECORDED not in event_types
    assert EventType.TASK_EXPANDED not in event_types
    assert EventType.SETTLEMENT_RECORDED not in event_types
