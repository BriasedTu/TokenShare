"""Shared contracts for independently developed paper experiment modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tokenshare.experiments.paper_models import (
    LEAN_PAPER_DIFFICULTIES,
    LEAN_TOPIC_FAMILIES,
    PAPER_DIFFICULTIES,
    PAPER_DOMAINS,
    JsonObject,
    PaperConditionResult,
    PaperExperimentCondition,
    UNSUPPORTED_PAPER_TRANSPORTS,
    digest_json,
)


FROZEN_CASE_SELECTION_SCHEMA_VERSION = "tokenshare.paper_frozen_case_selection.v1"
PAPER_EXECUTION_CONTEXT_SCHEMA_VERSION = "tokenshare.paper_execution_context.v1"
EXPERIMENT_SUMMARY_ROWS_SCHEMA_VERSION = (
    "tokenshare.paper_experiment_summary_rows.v1"
)


def canonical_contract_digest(value: Any) -> str:
    return digest_json(value)


@dataclass(frozen=True, kw_only=True)
class FrozenCaseSelection:
    selection_id: str
    experiment_id: str
    suite_version: str
    catalog_version: str
    domain: str
    paper_difficulty: str
    topic_family: str | None
    ordered_case_ids: tuple[str, ...] | list[str]
    catalog_digest: str
    expected_ai_unit_count: int
    paper_eligible_required: bool
    blocked_reason: str | None = None
    schema_version: str = FROZEN_CASE_SELECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FROZEN_CASE_SELECTION_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {FROZEN_CASE_SELECTION_SCHEMA_VERSION}"
            )
        for field_name in (
            "selection_id",
            "experiment_id",
            "suite_version",
            "catalog_version",
            "domain",
            "paper_difficulty",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if self.domain not in PAPER_DOMAINS:
            raise ValueError("domain must be factorization or lean_proof")
        _validate_paper_difficulty(
            domain=self.domain,
            paper_difficulty=self.paper_difficulty,
        )
        _validate_topic_family(domain=self.domain, topic_family=self.topic_family)
        _require_complete_digest("catalog_digest", self.catalog_digest)
        _require_bool("paper_eligible_required", self.paper_eligible_required)
        _require_non_negative_int(
            "expected_ai_unit_count",
            self.expected_ai_unit_count,
        )
        normalized_case_ids = _normalize_ordered_case_ids(self.ordered_case_ids)
        object.__setattr__(self, "ordered_case_ids", normalized_case_ids)
        normalized_blocked_reason = _normalize_optional_blocked_reason(
            self.blocked_reason
        )
        object.__setattr__(self, "blocked_reason", normalized_blocked_reason)
        if normalized_blocked_reason is None:
            if not normalized_case_ids or self.expected_ai_unit_count < 1:
                raise ValueError(
                    "executable selection must declare ordered case ids and AI units"
                )
            return
        if self.expected_ai_unit_count != 0:
            raise ValueError("blocked selection must not declare AI units")

    @property
    def is_blocked(self) -> bool:
        return self.blocked_reason is not None

    @property
    def is_executable(self) -> bool:
        return not self.is_blocked

    @property
    def selection_digest(self) -> str:
        return canonical_contract_digest(self._body(include_digest=False))

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> FrozenCaseSelection:
        expected_digest = body.get("selection_digest")
        if not isinstance(expected_digest, str) or not expected_digest:
            raise ValueError("selection_digest is required")
        selection = cls(
            schema_version=str(
                body.get("schema_version", FROZEN_CASE_SELECTION_SCHEMA_VERSION)
            ),
            selection_id=str(body.get("selection_id", "")),
            experiment_id=str(body.get("experiment_id", "")),
            suite_version=str(body.get("suite_version", "")),
            catalog_version=str(body.get("catalog_version", "")),
            domain=str(body.get("domain", "")),
            paper_difficulty=str(body.get("paper_difficulty", "")),
            topic_family=body.get("topic_family"),
            ordered_case_ids=body.get("ordered_case_ids", ()),
            catalog_digest=str(body.get("catalog_digest", "")),
            expected_ai_unit_count=body.get("expected_ai_unit_count"),
            paper_eligible_required=body.get("paper_eligible_required"),
            blocked_reason=body.get("blocked_reason"),
        )
        if expected_digest != selection.selection_digest:
            raise ValueError("selection_digest mismatch")
        return selection

    def to_dict(self) -> JsonObject:
        body = self._body(include_digest=True)
        body["execution_status"] = (
            "structured_blocked" if self.is_blocked else "executable"
        )
        body["paper_eligible_possible"] = self.is_executable
        body["provider_calls_made"] = 0
        return body

    def _body(self, *, include_digest: bool) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "selection_id": self.selection_id,
            "experiment_id": self.experiment_id,
            "suite_version": self.suite_version,
            "catalog_version": self.catalog_version,
            "domain": self.domain,
            "paper_difficulty": self.paper_difficulty,
            "topic_family": self.topic_family,
            "ordered_case_ids": list(self.ordered_case_ids),
            "catalog_digest": self.catalog_digest,
            "expected_ai_unit_count": self.expected_ai_unit_count,
            "paper_eligible_required": self.paper_eligible_required,
            "blocked_reason": self.blocked_reason,
        }
        if include_digest:
            body["selection_digest"] = self.selection_digest
        return body


@dataclass(frozen=True, kw_only=True)
class PaperExecutionContext:
    context_id: str
    catalog: Any
    approved_endpoint_binding: Any
    request_limits: Mapping[str, Any]
    hard_limits: Mapping[str, Any]
    output_root: str
    artifact_store: Any
    event_store: Any
    execution_callback: Callable[..., PaperConditionResult]
    schema_version: str = PAPER_EXECUTION_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PAPER_EXECUTION_CONTEXT_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {PAPER_EXECUTION_CONTEXT_SCHEMA_VERSION}"
            )
        _require_non_empty("context_id", self.context_id)
        _require_non_empty("output_root", self.output_root)
        if self.catalog is None:
            raise ValueError("catalog reference is required")
        if self.approved_endpoint_binding is None:
            raise ValueError("approved_endpoint_binding reference is required")
        if self.artifact_store is None:
            raise ValueError("artifact_store reference is required")
        if self.event_store is None:
            raise ValueError("event_store reference is required")
        if not isinstance(self.request_limits, Mapping):
            raise ValueError("request_limits must be a mapping")
        if not isinstance(self.hard_limits, Mapping):
            raise ValueError("hard_limits must be a mapping")
        if not callable(self.execution_callback):
            raise ValueError("execution_callback must be callable")


@dataclass(frozen=True, kw_only=True)
class ExperimentSummaryRows:
    experiment_id: str
    rows: tuple[JsonObject, ...] | list[Mapping[str, Any]]
    schema_version: str = EXPERIMENT_SUMMARY_ROWS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIMENT_SUMMARY_ROWS_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {EXPERIMENT_SUMMARY_ROWS_SCHEMA_VERSION}"
            )
        _require_non_empty("experiment_id", self.experiment_id)
        if not isinstance(self.rows, (list, tuple)):
            raise ValueError("rows must be a list or tuple")
        normalized_rows: list[JsonObject] = []
        for row in self.rows:
            if not isinstance(row, Mapping):
                raise ValueError("summary rows must be JSON objects")
            normalized = dict(row)
            _reject_scripted_eligible_row(normalized)
            normalized_rows.append(normalized)
        object.__setattr__(self, "rows", tuple(normalized_rows))

    @property
    def summary_digest(self) -> str:
        return canonical_contract_digest(self._body(include_digest=False))

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> ExperimentSummaryRows:
        expected_digest = body.get("summary_digest")
        summary = cls(
            schema_version=str(
                body.get("schema_version", EXPERIMENT_SUMMARY_ROWS_SCHEMA_VERSION)
            ),
            experiment_id=str(body.get("experiment_id", "")),
            rows=body.get("rows", ()),
        )
        if (
            isinstance(expected_digest, str)
            and expected_digest
            and expected_digest != summary.summary_digest
        ):
            raise ValueError("summary_digest mismatch")
        return summary

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "row_count": len(self.rows),
            "rows": [dict(row) for row in self.rows],
        }
        if include_digest:
            body["summary_digest"] = self.summary_digest
        return body


@runtime_checkable
class PaperExperimentModule(Protocol):
    def expand_conditions(
        self,
        context: PaperExecutionContext,
    ) -> tuple[PaperExperimentCondition, ...]:
        ...

    def freeze_case_selections(
        self,
        context: PaperExecutionContext,
        conditions: tuple[PaperExperimentCondition, ...],
    ) -> tuple[FrozenCaseSelection, ...]:
        ...

    def run_condition(
        self,
        context: PaperExecutionContext,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> PaperConditionResult:
        ...

    def summarize(self, evidence: Any) -> ExperimentSummaryRows:
        ...


def _normalize_ordered_case_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ordered_case_ids must be a list or tuple")
    normalized: list[str] = []
    for case_id in value:
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case_id values must be non-empty strings")
        normalized.append(case_id)
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate case_id in ordered_case_ids")
    return tuple(normalized)


def _normalize_optional_blocked_reason(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("blocked_reason must be a non-empty string")
    return value


def _validate_paper_difficulty(*, domain: str, paper_difficulty: str) -> None:
    if domain == "factorization":
        if paper_difficulty not in PAPER_DIFFICULTIES:
            raise ValueError("factorization paper_difficulty must be easy, medium, or hard")
        return
    if paper_difficulty not in LEAN_PAPER_DIFFICULTIES:
        raise ValueError(
            "Lean paper_difficulty must be simple, medium_lemma_dag, or hard_frontier"
        )


def _validate_topic_family(*, domain: str, topic_family: Any) -> None:
    if topic_family is None:
        if domain == "lean_proof":
            raise ValueError("Lean selection requires topic_family")
        return
    if not isinstance(topic_family, str) or not topic_family.strip():
        raise ValueError("topic_family must be a non-empty string")
    if domain == "factorization":
        raise ValueError("factorization selection must not declare topic_family")
    if topic_family not in LEAN_TOPIC_FAMILIES:
        raise ValueError("topic_family must be pure_logic, function_set, or induction")


def _reject_scripted_eligible_row(row: JsonObject) -> None:
    transport_kind = row.get("transport_kind")
    if (
        isinstance(transport_kind, str)
        and transport_kind in UNSUPPORTED_PAPER_TRANSPORTS
        and row.get("paper_eligible") is True
    ):
        raise ValueError(f"{transport_kind} transport cannot be paper eligible")


def _require_non_empty(field_name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_bool(field_name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")


def _require_non_negative_int(field_name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be an integer >= 0")


def _require_complete_digest(field_name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


__all__ = [
    "ExperimentSummaryRows",
    "FrozenCaseSelection",
    "PaperConditionResult",
    "PaperExecutionContext",
    "PaperExperimentModule",
    "canonical_contract_digest",
]
