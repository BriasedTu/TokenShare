"""论文实验结果与证据完整性的双轴终态契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PaperOutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED_EXPERIMENTAL = "failed_experimental"
    BLOCKED_DEPENDENCY = "blocked_dependency"


class PaperEvidenceIntegrity(str, Enum):
    COMPLETE = "complete"
    MISSING = "missing"
    INVALID = "invalid"
    CORRUPT = "corrupt"


class PaperSuiteTerminalStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, kw_only=True)
class PaperTerminalOutcome:
    outcome_status: PaperOutcomeStatus
    evidence_integrity: PaperEvidenceIntegrity
    failure_stage: str | None = None
    failure_kind: str | None = None

    def __post_init__(self) -> None:
        if self.outcome_status in {
            PaperOutcomeStatus.SUCCEEDED,
            PaperOutcomeStatus.FAILED_EXPERIMENTAL,
        } and self.evidence_integrity is not PaperEvidenceIntegrity.COMPLETE:
            raise ValueError("successful or experimental outcome requires complete evidence")
        if (
            self.outcome_status is PaperOutcomeStatus.BLOCKED_DEPENDENCY
            and self.evidence_integrity is PaperEvidenceIntegrity.COMPLETE
        ):
            raise ValueError("dependency block requires non-complete evidence integrity")
        for field_name in ("failure_stage", "failure_kind"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be a non-empty string")

    @property
    def should_continue_dispatch(self) -> bool:
        return self.outcome_status is not PaperOutcomeStatus.BLOCKED_DEPENDENCY

    def to_dict(self) -> dict[str, str]:
        body = {
            "outcome_status": self.outcome_status.value,
            "evidence_integrity": self.evidence_integrity.value,
        }
        if self.failure_stage is not None:
            body["failure_stage"] = self.failure_stage
        if self.failure_kind is not None:
            body["failure_kind"] = self.failure_kind
        return body


def derive_suite_terminal_status(
    *,
    succeeded_count: int,
    failed_experimental_count: int,
    blocked_dependency_count: int,
    interrupted: bool,
    persistence_complete: bool,
) -> PaperSuiteTerminalStatus:
    for field_name, value in (
        ("succeeded_count", succeeded_count),
        ("failed_experimental_count", failed_experimental_count),
        ("blocked_dependency_count", blocked_dependency_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} must be an integer >= 0")
    if not isinstance(interrupted, bool) or not isinstance(
        persistence_complete,
        bool,
    ):
        raise ValueError("suite persistence flags must be booleans")
    if interrupted or not persistence_complete:
        return PaperSuiteTerminalStatus.INCOMPLETE
    if blocked_dependency_count:
        return PaperSuiteTerminalStatus.BLOCKED
    if failed_experimental_count:
        return PaperSuiteTerminalStatus.COMPLETED_WITH_FAILURES
    return PaperSuiteTerminalStatus.COMPLETED
