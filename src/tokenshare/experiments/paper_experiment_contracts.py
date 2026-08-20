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
CONDITION_SELECTION_BINDING_SCHEMA_VERSION = (
    "tokenshare.paper_condition_selection_binding.v1"
)


def canonical_contract_digest(value: Any) -> str:
    return digest_json(value)


def formal_runtime_task_id(domain: str, case_id: str) -> str:
    """把 catalog case identity 映射为正式 runtime task identity。"""

    _require_non_empty("domain", domain)
    _require_non_empty("case_id", case_id)
    prefix = {
        "factorization": "paper_factorization_",
        "lean_proof": "paper_lean_",
    }.get(domain)
    if prefix is None:
        raise ValueError("formal runtime task domain is unsupported")
    return f"{prefix}{case_id}"


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

    @property
    def case_selection_digest(self) -> str:
        """跨实验比较用的 exact case slice digest，不含模块所有权字段。"""

        return canonical_contract_digest(
            {
                "schema_version": "tokenshare.paper_case_selection_digest.v1",
                "catalog_version": self.catalog_version,
                "catalog_digest": self.catalog_digest,
                "domain": self.domain,
                "paper_difficulty": self.paper_difficulty,
                "topic_family": self.topic_family,
                "ordered_case_ids": list(self.ordered_case_ids),
                "expected_ai_unit_count": self.expected_ai_unit_count,
                "blocked_reason": self.blocked_reason,
            }
        )

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> FrozenCaseSelection:
        body = _require_mapping(body)
        expected_digest = _required_digest_field(body, "selection_digest")
        selection = cls(
            schema_version=_required_string_field(body, "schema_version"),
            selection_id=_required_string_field(body, "selection_id"),
            experiment_id=_required_string_field(body, "experiment_id"),
            suite_version=_required_string_field(body, "suite_version"),
            catalog_version=_required_string_field(body, "catalog_version"),
            domain=_required_string_field(body, "domain"),
            paper_difficulty=_required_string_field(body, "paper_difficulty"),
            topic_family=_optional_string_field(body, "topic_family"),
            ordered_case_ids=_required_value(body, "ordered_case_ids"),
            catalog_digest=_required_digest_field(body, "catalog_digest"),
            expected_ai_unit_count=_required_non_negative_int_field(
                body,
                "expected_ai_unit_count",
            ),
            paper_eligible_required=_required_bool_field(
                body,
                "paper_eligible_required",
            ),
            blocked_reason=_optional_string_field(body, "blocked_reason"),
        )
        if expected_digest != selection.selection_digest:
            raise ValueError("selection_digest mismatch")
        expected_case_digest = body.get("case_selection_digest")
        if (
            expected_case_digest is not None
            and expected_case_digest != selection.case_selection_digest
        ):
            raise ValueError("case_selection_digest mismatch")
        return selection

    def to_dict(self) -> JsonObject:
        body = self._body(include_digest=True)
        body["case_selection_digest"] = self.case_selection_digest
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
class FrozenConditionSelectionBinding:
    """由模块在生成 selection 时建立的完整 condition identity 绑定。"""

    condition_id: str
    condition_digest: str
    selection: FrozenCaseSelection
    schema_version: str = CONDITION_SELECTION_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONDITION_SELECTION_BINDING_SCHEMA_VERSION:
            raise ValueError(
                "schema_version must be "
                f"{CONDITION_SELECTION_BINDING_SCHEMA_VERSION}"
            )
        _require_non_empty("condition_id", self.condition_id)
        _require_complete_digest("condition_digest", self.condition_digest)
        if not isinstance(self.selection, FrozenCaseSelection):
            raise ValueError("selection must be FrozenCaseSelection")

    @classmethod
    def from_condition(
        cls,
        condition: PaperExperimentCondition,
        selection: FrozenCaseSelection,
    ) -> FrozenConditionSelectionBinding:
        if (
            selection.experiment_id != condition.experiment_id
            or selection.domain != condition.domain
            or selection.paper_difficulty != condition.paper_difficulty
            or selection.topic_family != condition.topic_family
            or selection.catalog_digest != condition.catalog_digest
        ):
            raise ValueError("selection does not match bound condition")
        return cls(
            condition_id=condition.condition_id,
            condition_digest=condition.condition_digest,
            selection=selection,
        )

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "condition_digest": self.condition_digest,
            "selection": self.selection.to_dict(),
        }


class FrozenCaseSelectionBatch(tuple):
    """兼容旧 tuple API，并携带不依赖位置的 condition bindings。"""

    condition_selection_bindings: tuple[FrozenConditionSelectionBinding, ...]

    def __new__(
        cls,
        bindings: tuple[FrozenConditionSelectionBinding, ...]
        | list[FrozenConditionSelectionBinding],
    ) -> FrozenCaseSelectionBatch:
        normalized = tuple(bindings)
        if any(
            not isinstance(binding, FrozenConditionSelectionBinding)
            for binding in normalized
        ):
            raise ValueError(
                "FrozenCaseSelectionBatch requires condition selection bindings"
            )
        condition_ids = [binding.condition_id for binding in normalized]
        condition_digests = [binding.condition_digest for binding in normalized]
        if len(set(condition_ids)) != len(condition_ids):
            raise ValueError("duplicate condition selection binding condition_id")
        if len(set(condition_digests)) != len(condition_digests):
            raise ValueError("duplicate condition selection binding condition_digest")
        instance = super().__new__(
            cls,
            (binding.selection for binding in normalized),
        )
        instance.condition_selection_bindings = normalized
        return instance


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
        body = _require_mapping(body)
        expected_digest = _required_digest_field(body, "summary_digest")
        row_count = _required_non_negative_int_field(body, "row_count")
        summary = cls(
            schema_version=_required_string_field(body, "schema_version"),
            experiment_id=_required_string_field(body, "experiment_id"),
            rows=_required_value(body, "rows"),
        )
        if row_count != len(summary.rows):
            raise ValueError("row_count must equal len(rows)")
        if expected_digest != summary.summary_digest:
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
    ) -> FrozenCaseSelectionBatch:
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


def _require_mapping(value: Any, field_name: str = "body") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _required_value(body: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in body:
        raise ValueError(f"{field_name} is required")
    return body[field_name]


def _required_string_field(body: Mapping[str, Any], field_name: str) -> str:
    value = _required_value(body, field_name)
    _require_non_empty(field_name, value)
    return value


def _optional_string_field(body: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in body or body[field_name] is None:
        return None
    value = body[field_name]
    _require_non_empty(field_name, value)
    return value


def _required_bool_field(body: Mapping[str, Any], field_name: str) -> bool:
    value = _required_value(body, field_name)
    _require_bool(field_name, value)
    return value


def _required_non_negative_int_field(
    body: Mapping[str, Any],
    field_name: str,
) -> int:
    value = _required_value(body, field_name)
    _require_non_negative_int(field_name, value)
    return value


def _required_digest_field(body: Mapping[str, Any], field_name: str) -> str:
    value = _required_value(body, field_name)
    _require_complete_digest(field_name, value)
    return value


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
    "FrozenCaseSelectionBatch",
    "FrozenCaseSelection",
    "FrozenConditionSelectionBinding",
    "PaperConditionResult",
    "PaperExecutionContext",
    "PaperExperimentModule",
    "canonical_contract_digest",
]
