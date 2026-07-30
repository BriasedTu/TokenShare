from __future__ import annotations

import pytest

from tokenshare.experiments.paper_terminal_outcomes import (
    PaperEvidenceIntegrity,
    PaperOutcomeStatus,
    PaperSuiteTerminalStatus,
    PaperTerminalOutcome,
    derive_suite_terminal_status,
)


def test_terminal_outcome_axes_have_stable_serialized_values() -> None:
    assert [item.value for item in PaperOutcomeStatus] == [
        "succeeded",
        "failed_experimental",
        "blocked_dependency",
    ]
    assert [item.value for item in PaperEvidenceIntegrity] == [
        "complete",
        "missing",
        "invalid",
        "corrupt",
    ]
    assert [item.value for item in PaperSuiteTerminalStatus] == [
        "completed",
        "completed_with_failures",
        "blocked",
        "incomplete",
    ]


def test_complete_experimental_failure_is_terminal_but_does_not_block_dispatch() -> None:
    outcome = PaperTerminalOutcome(
        outcome_status=PaperOutcomeStatus.FAILED_EXPERIMENTAL,
        evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
        failure_stage="verifier",
        failure_kind="verification_rejected",
    )

    assert outcome.should_continue_dispatch is True
    assert outcome.to_dict() == {
        "outcome_status": "failed_experimental",
        "evidence_integrity": "complete",
        "failure_stage": "verifier",
        "failure_kind": "verification_rejected",
    }


@pytest.mark.parametrize(
    "integrity",
    (
        PaperEvidenceIntegrity.MISSING,
        PaperEvidenceIntegrity.INVALID,
        PaperEvidenceIntegrity.CORRUPT,
    ),
)
def test_dependency_integrity_failure_stops_new_dispatch(
    integrity: PaperEvidenceIntegrity,
) -> None:
    outcome = PaperTerminalOutcome(
        outcome_status=PaperOutcomeStatus.BLOCKED_DEPENDENCY,
        evidence_integrity=integrity,
        failure_stage="shared_exp1_reference",
        failure_kind="source_evidence_unavailable",
    )

    assert outcome.should_continue_dispatch is False


def test_terminal_outcome_rejects_conflated_axis_pairs() -> None:
    with pytest.raises(ValueError, match="complete evidence"):
        PaperTerminalOutcome(
            outcome_status=PaperOutcomeStatus.SUCCEEDED,
            evidence_integrity=PaperEvidenceIntegrity.MISSING,
        )
    with pytest.raises(ValueError, match="dependency block"):
        PaperTerminalOutcome(
            outcome_status=PaperOutcomeStatus.BLOCKED_DEPENDENCY,
            evidence_integrity=PaperEvidenceIntegrity.COMPLETE,
        )


def test_suite_terminal_status_reserves_incomplete_for_interruption_or_persistence() -> None:
    assert derive_suite_terminal_status(
        succeeded_count=3,
        failed_experimental_count=0,
        blocked_dependency_count=0,
        interrupted=False,
        persistence_complete=True,
    ) is PaperSuiteTerminalStatus.COMPLETED
    assert derive_suite_terminal_status(
        succeeded_count=3,
        failed_experimental_count=1,
        blocked_dependency_count=0,
        interrupted=False,
        persistence_complete=True,
    ) is PaperSuiteTerminalStatus.COMPLETED_WITH_FAILURES
    assert derive_suite_terminal_status(
        succeeded_count=1,
        failed_experimental_count=0,
        blocked_dependency_count=1,
        interrupted=False,
        persistence_complete=True,
    ) is PaperSuiteTerminalStatus.BLOCKED
    assert derive_suite_terminal_status(
        succeeded_count=1,
        failed_experimental_count=0,
        blocked_dependency_count=0,
        interrupted=True,
        persistence_complete=True,
    ) is PaperSuiteTerminalStatus.INCOMPLETE
    assert derive_suite_terminal_status(
        succeeded_count=1,
        failed_experimental_count=0,
        blocked_dependency_count=0,
        interrupted=False,
        persistence_complete=False,
    ) is PaperSuiteTerminalStatus.INCOMPLETE
