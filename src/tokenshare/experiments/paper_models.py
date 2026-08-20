"""Paper real-AI experiment schemas and stable JSON helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from tokenshare.executors.response_bank import (
    CurrentTraceWrapper,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ValidatedResponseBankIndex,
)
from tokenshare.executors.trace_backed import (
    APPROVED_REAL_SOURCE_EVIDENCE_CLASS,
    TraceSourceBinding,
)


JsonObject = dict[str, Any]
PAPER_DOMAINS = ("factorization", "lean_proof")
PAPER_DIFFICULTIES = ("easy", "medium", "hard")
LEAN_PAPER_DIFFICULTIES = ("simple", "medium_lemma_dag", "hard_frontier")
PAPER_DIFFICULTY_VALUES = PAPER_DIFFICULTIES + LEAN_PAPER_DIFFICULTIES
LEAN_TOPIC_FAMILIES = ("pure_logic", "function_set", "induction")
PAPER_MODEL_POLICIES = ("fixed_entry",)
FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID = "exp5_real_ai_model_endpoint_comparison"
PAPER_FORMAL_AI_TIMEOUT_SECONDS = 100
EXP1_TO_EXP4_DEEPSEEK_TIMEOUT_SECONDS = 600
EXP1_TO_EXP4_DEEPSEEK_MAX_TOKENS = 300_000
UNSUPPORTED_PAPER_TRANSPORTS = frozenset({"scripted", "fake", "deterministic", "mock"})
PAPER_DIRECT_EVIDENCE_CLASSES = frozenset(
    {
        "online_real_provider",
        "real_model_trace_protocol_run",
        "regression_only",
    }
)
VERSIONED_PAPER_EVIDENCE_CLASSES = frozenset(
    {
        "online_real_provider",
        "real_model_trace_protocol_run",
        "capability_only",
        "historical",
        "synthetic",
        "regression_only",
    }
)
VERSIONED_PAPER_SOURCE_CLASSIFICATIONS = frozenset(
    {
        "current_real_provider",
        "approved_real_full_acquisition",
        "capability_only",
        "historical",
        "synthetic",
        "regression_only",
    }
)
PAPER_EVIDENCE_ELIGIBILITY_FACTS_SCHEMA = (
    "tokenshare.paper_evidence_eligibility_facts.v2"
)
PAPER_EVIDENCE_ELIGIBILITY_REPORT_SCHEMA = (
    "tokenshare.paper_evidence_eligibility_report.v2"
)
PAID_EXECUTION_RECEIPT_CLAIM_SCHEMA = (
    "tokenshare.paid_execution_receipt_claim.v1"
)
EPD027_FULL_BANK_ACQUISITION_SCOPE = "epd027_full_bank_acquisition"
EXTERNAL_BANK_OBJECT_ROLES = frozenset(
    {
        "request_body",
        "raw_output_or_provider_failure",
        "raw_output",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "acquisition_attempt",
        "model_record",
    }
)
_CANONICAL_DIRECT_EVIDENCE_FACTORY_TOKEN = object()


class PaperStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class PaperTaskStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INELIGIBLE = "ineligible"
    PARTIAL = "partial"


class PaperAttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    PROVIDER_ERROR = "provider_error"
    EXECUTOR_ERROR = "executor_error"
    PARSE_FAILED = "parse_failed"
    VERIFICATION_REJECTED = "verification_rejected"
    CHECKER_REJECTED = "checker_rejected"
    LEASE_EXPIRED = "lease_expired"
    LATE_REJECTED = "late_rejected"
    WORKER_DIED = "worker_died"
    CANCELLED_BY_BUDGET = "cancelled_by_budget"


class PaperDirectRootStatus(str, Enum):
    """新 direct-result schema 的 root 状态；不修改历史 PaperTaskStatus。"""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INELIGIBLE = "ineligible"
    NOT_STARTED = "not_started"


class PaperFailureStage(str, Enum):
    CATALOG = "catalog"
    SPLIT = "split"
    REQUEST = "request"
    PROVIDER = "provider"
    PARSE = "parse"
    VERIFICATION = "verification"
    CHECKER = "checker"
    CANONICAL = "canonical"
    MERGE = "merge"
    SETTLEMENT = "settlement"
    METRICS = "metrics"
    AUDIT = "audit"


class PaperFailureKind(str, Enum):
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    PROVIDER_ERROR = "provider_error"
    EXECUTOR_ERROR = "executor_error"
    RATE_LIMITED = "rate_limited"
    PARSE_FAILURE = "parse_failure"
    VERIFIER_REJECTED = "verifier_rejected"
    CHECKER_REJECTED = "checker_rejected"
    LEASE_EXPIRED = "lease_expired"
    LATE_SUBMISSION = "late_submission"
    NO_REQUEUE = "no_requeue"
    PREMATURE_MERGE = "premature_merge"
    SLOT_MISMATCH = "slot_mismatch"
    BUDGET_LIMIT = "budget_limit"
    UNSUPPORTED_WORKER_LEVEL = "unsupported_worker_level"
    MISSING_MODEL_ENTRY = "missing_model_entry"
    SECRET_LEAK = "secret_leak"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, kw_only=True)
class PaperEvidenceEligibilityFacts:
    """只读 eligibility 输入；只保存上游已持久化的事实与引用。"""

    evidence_class: str
    source_classification: str
    executed_ai_unit_count: int
    executed_unit_bindings: tuple[JsonObject, ...]
    current_provider_call_count: int
    source_provider_call_count: int
    current_real_provider_attempt_refs: tuple[JsonObject, ...]
    current_lifecycle_refs: tuple[JsonObject, ...]
    trace_source_bindings: tuple[JsonObject, ...]
    source_manifest: JsonObject | None
    source_inventory_rows: tuple[JsonObject, ...]
    source_entries: tuple[JsonObject, ...]
    paid_receipt_claim: JsonObject | None
    direct_evidence_complete: bool
    identity_consistent: bool
    regression_only: bool
    schema_version: str = PAPER_EVIDENCE_ELIGIBILITY_FACTS_SCHEMA

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PaperEvidenceEligibilityFacts":
        if not isinstance(value, Mapping):
            raise TypeError("paper evidence eligibility facts must be a mapping")
        if value.get("schema_version") != PAPER_EVIDENCE_ELIGIBILITY_FACTS_SCHEMA:
            raise ValueError("unsupported paper evidence eligibility facts schema")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError("paper evidence eligibility facts keys are invalid")
        return cls(**dict(value))

    def __post_init__(self) -> None:
        if self.schema_version != PAPER_EVIDENCE_ELIGIBILITY_FACTS_SCHEMA:
            raise ValueError("unsupported paper evidence eligibility facts schema")
        if self.evidence_class not in VERSIONED_PAPER_EVIDENCE_CLASSES:
            raise ValueError("unsupported versioned paper evidence class")
        if self.source_classification not in VERSIONED_PAPER_SOURCE_CLASSIFICATIONS:
            raise ValueError("unsupported versioned paper source classification")
        _require_integer("executed_ai_unit_count", self.executed_ai_unit_count, min_value=1)
        for field_name in (
            "current_provider_call_count",
            "source_provider_call_count",
        ):
            _require_integer(field_name, getattr(self, field_name), min_value=0)
        for field_name in (
            "direct_evidence_complete",
            "identity_consistent",
            "regression_only",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a bool")
        for field_name in (
            "executed_unit_bindings",
            "current_real_provider_attempt_refs",
            "current_lifecycle_refs",
            "trace_source_bindings",
            "source_inventory_rows",
            "source_entries",
        ):
            refs = getattr(self, field_name)
            if not isinstance(refs, (list, tuple)) or any(
                not isinstance(ref, Mapping) or not ref for ref in refs
            ):
                raise ValueError(f"{field_name} must contain persisted mappings")
            object.__setattr__(self, field_name, tuple(_freeze_json(ref) for ref in refs))
        for field_name in ("paid_receipt_claim", "source_manifest"):
            claim = getattr(self, field_name)
            if claim is not None and not isinstance(claim, Mapping):
                raise TypeError(f"{field_name} must be a mapping or null")
            if claim is not None:
                object.__setattr__(self, field_name, _freeze_json(claim))

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "evidence_class": self.evidence_class,
            "source_classification": self.source_classification,
            "executed_ai_unit_count": self.executed_ai_unit_count,
            "executed_unit_bindings": _thaw_json(self.executed_unit_bindings),
            "current_provider_call_count": self.current_provider_call_count,
            "source_provider_call_count": self.source_provider_call_count,
            "current_real_provider_attempt_refs": _thaw_json(
                self.current_real_provider_attempt_refs
            ),
            "current_lifecycle_refs": _thaw_json(self.current_lifecycle_refs),
            "trace_source_bindings": _thaw_json(self.trace_source_bindings),
            "source_manifest": _thaw_json(self.source_manifest),
            "source_inventory_rows": _thaw_json(self.source_inventory_rows),
            "source_entries": _thaw_json(self.source_entries),
            "paid_receipt_claim": _thaw_json(self.paid_receipt_claim),
            "direct_evidence_complete": self.direct_evidence_complete,
            "identity_consistent": self.identity_consistent,
            "regression_only": self.regression_only,
        }


@dataclass(frozen=True, kw_only=True)
class VersionedPaperEvidenceEligibilityReport:
    paper_eligible: bool
    ineligibility_reasons: tuple[str, ...]
    evidence_class: str
    source_classification: str
    executed_ai_unit_count: int
    current_provider_call_count: int
    source_provider_call_count: int
    direct_evidence_complete: bool
    identity_consistent: bool
    source_manifest_complete: bool
    schema_version: str = PAPER_EVIDENCE_ELIGIBILITY_REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PAPER_EVIDENCE_ELIGIBILITY_REPORT_SCHEMA:
            raise ValueError("unsupported paper evidence eligibility report schema")
        for field_name in (
            "paper_eligible",
            "direct_evidence_complete",
            "identity_consistent",
            "source_manifest_complete",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a bool")
        for field_name in (
            "executed_ai_unit_count",
            "current_provider_call_count",
            "source_provider_call_count",
        ):
            _require_integer(field_name, getattr(self, field_name), min_value=0)
        if self.evidence_class not in VERSIONED_PAPER_EVIDENCE_CLASSES:
            raise ValueError("unsupported versioned paper evidence class")
        if self.source_classification not in VERSIONED_PAPER_SOURCE_CLASSIFICATIONS:
            raise ValueError("unsupported versioned paper source classification")
        reasons = tuple(self.ineligibility_reasons)
        if any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("ineligibility reasons must be non-empty strings")
        object.__setattr__(self, "ineligibility_reasons", reasons)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "evidence_class": self.evidence_class,
            "source_classification": self.source_classification,
            "executed_ai_unit_count": self.executed_ai_unit_count,
            "current_provider_call_count": self.current_provider_call_count,
            "source_provider_call_count": self.source_provider_call_count,
            "direct_evidence_complete": self.direct_evidence_complete,
            "identity_consistent": self.identity_consistent,
            "source_manifest_complete": self.source_manifest_complete,
        }


@dataclass(frozen=True, kw_only=True)
class ExternalBankObjectLocator:
    """只保存 bank object 身份；filesystem capability 由进程外 resolver 持有。"""

    bank_root_id: str
    manifest_digest: str
    entry_id: str
    object_role: str
    object_digest: str
    schema_version: str = "tokenshare.external_bank_object_locator.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.external_bank_object_locator.v1":
            raise ValueError("unsupported external bank object locator schema")
        _require_non_empty("bank_root_id", self.bank_root_id)
        _require_non_empty("entry_id", self.entry_id)
        _require_identity_digest("manifest_digest", self.manifest_digest)
        _require_identity_digest("object_digest", self.object_digest)
        if self.object_role not in EXTERNAL_BANK_OBJECT_ROLES:
            raise ValueError("unsupported external bank object role")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "bank_root_id": self.bank_root_id,
            "manifest_digest": self.manifest_digest,
            "entry_id": self.entry_id,
            "object_role": self.object_role,
            "object_digest": self.object_digest,
        }


@dataclass(frozen=True, kw_only=True)
class PaperDirectRootInventoryRow:
    """预注册 root；不包含 execution、ledger 或任何运行时事实。"""

    inventory_id: str
    preregistered_root_run_id: str
    experiment_id: str
    condition_id: str
    preregistered_condition_ref: JsonObject
    condition_axes: JsonObject
    case_id: str
    preregistered_case_ref: JsonObject
    repeat_id: int
    evidence_class: str
    inventory_row_digest: str
    schema_version: str = "tokenshare.paper_direct_root_inventory_row.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.paper_direct_root_inventory_row.v2":
            raise ValueError("unsupported paper direct root inventory schema")
        for field_name in (
            "inventory_id",
            "preregistered_root_run_id",
            "experiment_id",
            "condition_id",
            "case_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_integer("repeat_id", self.repeat_id, min_value=0)
        if self.evidence_class not in PAPER_DIRECT_EVIDENCE_CLASSES:
            raise ValueError("unsupported paper direct evidence class")
        for field_name in (
            "preregistered_condition_ref",
            "condition_axes",
            "preregistered_case_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{field_name} must be a non-empty persisted mapping")
            object.__setattr__(self, field_name, _freeze_json(value))
        _require_identity_digest("inventory_row_digest", self.inventory_row_digest)
        if self.inventory_row_digest != digest_json(self._digest_body()):
            raise ValueError("inventory row digest mismatch")

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "inventory_id": self.inventory_id,
            "preregistered_root_run_id": self.preregistered_root_run_id,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "preregistered_condition_ref": _thaw_json(
                self.preregistered_condition_ref
            ),
            "condition_axes": _thaw_json(self.condition_axes),
            "case_id": self.case_id,
            "preregistered_case_ref": _thaw_json(self.preregistered_case_ref),
            "repeat_id": self.repeat_id,
            "evidence_class": self.evidence_class,
        }

    def to_dict(self) -> JsonObject:
        return {
            **self._digest_body(),
            "inventory_row_digest": self.inventory_row_digest,
        }


@dataclass(frozen=True, kw_only=True)
class PreregisteredRootInventoryManifest:
    """固定 direct denominator 的完整、digest-bound manifest。"""

    inventory_id: str
    rows: tuple[PaperDirectRootInventoryRow, ...]
    root_count: int
    inventory_digest: str
    schema_version: str = "tokenshare.preregistered_root_inventory_manifest.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.preregistered_root_inventory_manifest.v1":
            raise ValueError("unsupported preregistered root inventory schema")
        _require_non_empty("inventory_id", self.inventory_id)
        _require_integer("root_count", self.root_count, min_value=1)
        rows = tuple(self.rows)
        if any(not isinstance(row, PaperDirectRootInventoryRow) for row in rows):
            raise ValueError("inventory rows must be typed root rows")
        root_ids = tuple(row.preregistered_root_run_id for row in rows)
        if any(row.inventory_id != self.inventory_id for row in rows):
            raise ValueError("inventory row inventory_id mismatch")
        if len(rows) != self.root_count:
            raise ValueError("inventory root_count mismatch")
        if len(set(root_ids)) != len(root_ids):
            raise ValueError("duplicate root in inventory manifest")
        if len({row.inventory_row_digest for row in rows}) != len(rows):
            raise ValueError("duplicate inventory row digest")
        object.__setattr__(self, "rows", rows)
        _require_identity_digest("inventory_digest", self.inventory_digest)
        if self.inventory_digest != digest_json(self._digest_body()):
            raise ValueError("inventory digest mismatch")

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "inventory_id": self.inventory_id,
            "root_count": self.root_count,
            "rows": [row.to_dict() for row in self.rows],
        }

    def to_dict(self) -> JsonObject:
        return {**self._digest_body(), "inventory_digest": self.inventory_digest}


@dataclass(frozen=True, kw_only=True)
class ArtifactIdentitySnapshot:
    """ArtifactRef 的深冻结 identity；不保留可变 source/metadata。"""

    artifact_id: str
    artifact_type: str
    uri: str
    content_hash: str
    size_bytes: int
    media_type: str
    artifact_schema_id: str
    artifact_schema_version: str
    source_role: str
    source_task_id: str
    source_execution_id: str
    created_at: str
    schema_version: str = "tokenshare.artifact_identity_snapshot.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.artifact_identity_snapshot.v1":
            raise ValueError("unsupported artifact identity snapshot schema")
        for field_name in (
            "artifact_id",
            "artifact_type",
            "uri",
            "content_hash",
            "media_type",
            "artifact_schema_id",
            "artifact_schema_version",
            "source_role",
            "source_task_id",
            "source_execution_id",
            "created_at",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_identity_digest("content_hash", self.content_hash)
        _require_integer("size_bytes", self.size_bytes, min_value=0)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "artifact_schema_id": self.artifact_schema_id,
            "artifact_schema_version": self.artifact_schema_version,
            "source_role": self.source_role,
            "source_task_id": self.source_task_id,
            "source_execution_id": self.source_execution_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class LedgerEventIdentitySnapshot:
    event_seq: int
    event_id: str
    event_type: str
    event_hash: str
    prev_event_hash: str | None
    task_id: str
    object_type: str
    object_id: str
    schema_version: str = "tokenshare.ledger_event_identity_snapshot.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.ledger_event_identity_snapshot.v1":
            raise ValueError("unsupported ledger event identity snapshot schema")
        _require_integer("event_seq", self.event_seq, min_value=1)
        for field_name in (
            "event_id",
            "event_type",
            "event_hash",
            "task_id",
            "object_type",
            "object_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_identity_digest("event_hash", self.event_hash)
        if self.prev_event_hash is not None:
            _require_identity_digest("prev_event_hash", self.prev_event_hash)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "event_seq": self.event_seq,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_hash": self.event_hash,
            "prev_event_hash": self.prev_event_hash,
            "task_id": self.task_id,
            "object_type": self.object_type,
            "object_id": self.object_id,
        }


@dataclass(frozen=True, kw_only=True)
class DirectRootExecutionBinding:
    """direct 专用的 canonical execution/ledger snapshot。"""

    preregistered_root_run_id: str
    execution_id: str
    task_id: str
    root_unit_id: str
    ledger_digest: str
    events: tuple[LedgerEventIdentitySnapshot, ...]
    binding_digest: str
    schema_version: str = "tokenshare.direct_root_execution_binding.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.direct_root_execution_binding.v1":
            raise ValueError("unsupported direct root execution binding schema")
        for field_name in (
            "preregistered_root_run_id",
            "execution_id",
            "task_id",
            "root_unit_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_identity_digest("ledger_digest", self.ledger_digest)
        events = tuple(self.events)
        if not events or any(
            not isinstance(event, LedgerEventIdentitySnapshot) for event in events
        ):
            raise ValueError("execution binding requires typed ledger events")
        object.__setattr__(self, "events", events)
        _require_identity_digest("binding_digest", self.binding_digest)
        if self.binding_digest != digest_json(self._digest_body()):
            raise ValueError("direct root execution binding digest mismatch")

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "preregistered_root_run_id": self.preregistered_root_run_id,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "root_unit_id": self.root_unit_id,
            "ledger_digest": self.ledger_digest,
            "events": [event.to_dict() for event in self.events],
        }

    def to_dict(self) -> JsonObject:
        return {
            **self._digest_body(),
            "binding_digest": self.binding_digest,
        }


@dataclass(frozen=True, init=False)
class CanonicalDirectRootEvidence:
    """只能由 canonical evidence factory 形成的冻结结果。"""

    preregistered_root_run_id: str
    evidence_class: str
    execution_binding: DirectRootExecutionBinding
    canonical_runtime_status: str
    final_result_ref: ArtifactIdentitySnapshot | None
    terminal_root_event_ref: LedgerEventIdentitySnapshot | None
    canonical_acceptance_ref: LedgerEventIdentitySnapshot | None
    merge_ref: LedgerEventIdentitySnapshot | None
    independently_verified_correct: bool
    paper_evidence_complete: bool
    identity_consistent: bool
    infrastructure_valid: bool
    attempt_refs: tuple[LedgerEventIdentitySnapshot, ...]
    event_refs: tuple[LedgerEventIdentitySnapshot, ...]
    parser_refs: tuple[ArtifactIdentitySnapshot, ...]
    verifier_checker_refs: tuple[ArtifactIdentitySnapshot, ...]
    artifact_refs: tuple[ArtifactIdentitySnapshot, ...]
    current_provider_object_refs: tuple[ArtifactIdentitySnapshot, ...]
    source_bank_object_locators: tuple[ExternalBankObjectLocator, ...]
    actual_resource_book_ref: ArtifactIdentitySnapshot | None
    trace_resource_book_ref: ArtifactIdentitySnapshot | None
    ineligibility_reasons: tuple[str, ...]
    schema_version: str
    _producer_validated: bool = field(
        init=False,
        default=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def _from_validated(
        cls,
        *,
        _factory_token: object | None = None,
        **values: Any,
    ) -> "CanonicalDirectRootEvidence":
        expected = set(cls.__dataclass_fields__) - {"_producer_validated"}
        if set(values) != expected:
            raise ValueError("canonical direct evidence factory field mismatch")
        if values["schema_version"] != "tokenshare.canonical_direct_root_evidence.v1":
            raise ValueError("unsupported canonical direct evidence schema")
        _require_non_empty(
            "preregistered_root_run_id",
            values["preregistered_root_run_id"],
        )
        if values["evidence_class"] not in PAPER_DIRECT_EVIDENCE_CLASSES:
            raise ValueError("unsupported paper direct evidence class")
        binding = values["execution_binding"]
        if not isinstance(binding, DirectRootExecutionBinding):
            raise TypeError("execution_binding must be DirectRootExecutionBinding")
        if (
            binding.preregistered_root_run_id
            != values["preregistered_root_run_id"]
        ):
            raise ValueError("canonical evidence root binding mismatch")
        _require_non_empty(
            "canonical_runtime_status",
            values["canonical_runtime_status"],
        )
        for field_name in (
            "independently_verified_correct",
            "paper_evidence_complete",
            "identity_consistent",
            "infrastructure_valid",
        ):
            if type(values[field_name]) is not bool:
                raise ValueError(f"{field_name} must be a bool")
        for field_name, expected_type in (
            ("final_result_ref", ArtifactIdentitySnapshot),
            ("terminal_root_event_ref", LedgerEventIdentitySnapshot),
            ("canonical_acceptance_ref", LedgerEventIdentitySnapshot),
            ("merge_ref", LedgerEventIdentitySnapshot),
            ("actual_resource_book_ref", ArtifactIdentitySnapshot),
            ("trace_resource_book_ref", ArtifactIdentitySnapshot),
        ):
            value = values[field_name]
            if value is not None and not isinstance(value, expected_type):
                raise TypeError(f"{field_name} must be typed")
        for field_name, expected_type in (
            ("attempt_refs", LedgerEventIdentitySnapshot),
            ("event_refs", LedgerEventIdentitySnapshot),
            ("parser_refs", ArtifactIdentitySnapshot),
            ("verifier_checker_refs", ArtifactIdentitySnapshot),
            ("artifact_refs", ArtifactIdentitySnapshot),
            ("current_provider_object_refs", ArtifactIdentitySnapshot),
            ("source_bank_object_locators", ExternalBankObjectLocator),
        ):
            items = tuple(values[field_name])
            if any(not isinstance(item, expected_type) for item in items):
                raise TypeError(f"{field_name} must contain typed values")
            values[field_name] = items
        reasons = tuple(values["ineligibility_reasons"])
        if any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("ineligibility reasons must be non-empty strings")
        values["ineligibility_reasons"] = reasons
        if (
            values["current_provider_object_refs"]
            and values["source_bank_object_locators"]
        ):
            raise ValueError("current and source provider evidence are exclusive")
        if (
            values["actual_resource_book_ref"] is not None
            and values["trace_resource_book_ref"] is not None
        ):
            raise ValueError("actual and trace resource books are exclusive")
        instance = object.__new__(cls)
        for field_name, value in values.items():
            object.__setattr__(instance, field_name, value)
        object.__setattr__(
            instance,
            "_producer_validated",
            _factory_token is _CANONICAL_DIRECT_EVIDENCE_FACTORY_TOKEN,
        )
        return instance

    @property
    def producer_validated(self) -> bool:
        return self._producer_validated


@dataclass(frozen=True, kw_only=True)
class PaperExperimentCondition:
    experiment_id: str
    condition_id: str
    domain: str
    difficulty: str
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    worker_count: int
    fault_type: str
    fault_rate: float
    ablation_mode: str
    model_policy: str
    model_cohort_id: str | None = None
    cohort_member_id: str | None = None
    provider_config_id: str | None = None
    model_entry_id: str | None = None
    provider_family: str | None = None
    provider_model_id: str | None = None
    reasoning_profile_id: str | None = None
    model_cohort_digest: str | None = None
    source_provider_config_digest: str | None = None
    model_endpoint_identity_digest: str | None = None
    repeat_id: int
    seed: int
    catalog_digest: str
    real_transport_required: bool = True
    paper_eligible_required: bool = True
    schema_version: str = "tokenshare.paper_condition.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "condition_id",
            "domain",
            "difficulty",
            "fault_type",
            "ablation_mode",
            "model_policy",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_integer("worker_count", self.worker_count, min_value=1)
        _require_integer("repeat_id", self.repeat_id, min_value=0)
        _require_integer("seed", self.seed, min_value=0)
        if self.domain not in PAPER_DOMAINS:
            raise ValueError("domain must be factorization or lean_proof")
        if self.difficulty not in PAPER_DIFFICULTIES:
            raise ValueError("difficulty must be easy, medium, or hard")
        _resolve_paper_difficulty(self.domain, self.difficulty, self.paper_difficulty)
        _validate_optional_topic_family(self.domain, self.topic_family)
        _validate_model_policy(self.model_policy)
        for field_name in (
            "topic_family_version",
            "construction_rule_id",
            "oracle_package_group",
            "proof_assembly_shape",
            "model_cohort_id",
            "cohort_member_id",
            "provider_config_id",
            "model_entry_id",
            "provider_family",
            "provider_model_id",
            "reasoning_profile_id",
        ):
            _validate_optional_non_empty(field_name, getattr(self, field_name))
        if self.model_cohort_digest is not None:
            _require_identity_digest("model_cohort_digest", self.model_cohort_digest)
        if self.source_provider_config_digest is not None:
            _require_identity_digest(
                "source_provider_config_digest",
                self.source_provider_config_digest,
            )
        if self.model_endpoint_identity_digest is not None:
            _require_identity_digest(
                "model_endpoint_identity_digest",
                self.model_endpoint_identity_digest,
            )
        if self.experiment_id == FORMAL_MODEL_ENDPOINT_EXPERIMENT_ID:
            _require_complete_model_endpoint_identity(self)
        if not isinstance(self.fault_rate, (float, int)) or self.fault_rate < 0:
            raise ValueError("fault_rate must be a non-negative number")
        _require_digest("catalog_digest", self.catalog_digest)

    @property
    def condition_digest(self) -> str:
        return digest_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "paper_difficulty": _resolve_paper_difficulty(
                self.domain,
                self.difficulty,
                self.paper_difficulty,
            ),
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "worker_count": self.worker_count,
            "fault_type": self.fault_type,
            "fault_rate": float(self.fault_rate),
            "ablation_mode": self.ablation_mode,
            "model_policy": self.model_policy,
            "model_cohort_id": self.model_cohort_id,
            "cohort_member_id": self.cohort_member_id,
            "provider_config_id": self.provider_config_id,
            "model_entry_id": self.model_entry_id,
            "provider_family": self.provider_family,
            "provider_model_id": self.provider_model_id,
            "reasoning_profile_id": self.reasoning_profile_id,
            "model_cohort_digest": self.model_cohort_digest,
            "source_provider_config_digest": self.source_provider_config_digest,
            "model_endpoint_identity_digest": self.model_endpoint_identity_digest,
            "repeat_id": self.repeat_id,
            "seed": self.seed,
            "catalog_digest": self.catalog_digest,
            "real_transport_required": self.real_transport_required,
            "paper_eligible_required": self.paper_eligible_required,
        }
        if include_digest:
            body["condition_digest"] = self.condition_digest
        return body


@dataclass(frozen=True, kw_only=True)
class PaperSuiteResult:
    suite_id: str
    status: PaperStatus | str
    output_root: str
    started_at: str
    ended_at: str | None
    experiment_ids: list[str] | tuple[str, ...]
    condition_count: int
    run_count: int
    task_count: int
    provider_attempt_count: int
    total_tokens: int
    total_cost_estimate: float
    paper_eligible: bool
    eligibility_report_ref: JsonObject | None
    budget_ref: JsonObject | None
    metrics_refs: list[JsonObject] | tuple[JsonObject, ...]
    audit_refs: list[JsonObject] | tuple[JsonObject, ...]
    error_summary: list[JsonObject] | tuple[JsonObject, ...]
    model_policy_preflight: JsonObject | None = None
    model_endpoint_cohort_preflight: JsonObject | None = None
    cost_estimate_by_currency: JsonObject | None = None
    total_cost_estimate_status: str = "single_currency_or_legacy"
    condition_results: list[JsonObject] | tuple[JsonObject, ...] = ()
    schema_version: str = "tokenshare.paper_suite_result.v1"

    def to_dict(self) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "status": _status_value("status", PaperStatus, self.status),
            "output_root": self.output_root,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "experiment_ids": list(self.experiment_ids),
            "condition_count": self.condition_count,
            "run_count": self.run_count,
            "task_count": self.task_count,
            "provider_attempt_count": self.provider_attempt_count,
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "paper_eligible": self.paper_eligible,
            "eligibility_report_ref": _json_value(self.eligibility_report_ref),
            "budget_ref": _json_value(self.budget_ref),
            "metrics_refs": _json_value(list(self.metrics_refs)),
            "audit_refs": _json_value(list(self.audit_refs)),
            "error_summary": _json_value(list(self.error_summary)),
            "model_policy_preflight": _json_value(self.model_policy_preflight),
            "model_endpoint_cohort_preflight": _json_value(
                self.model_endpoint_cohort_preflight
            ),
        }
        if self.cost_estimate_by_currency is not None:
            body["cost_estimate_by_currency"] = _json_value(
                self.cost_estimate_by_currency
            )
            body["total_cost_estimate_status"] = self.total_cost_estimate_status
        if self.condition_results:
            body["condition_results"] = _json_value(list(self.condition_results))
        return body


@dataclass(frozen=True, kw_only=True)
class PaperExperimentResult:
    experiment_id: str
    status: PaperStatus | str
    condition_ids: list[str] | tuple[str, ...]
    run_count: int
    task_count: int
    completion_rate: float
    accepted_validity_rate: float
    total_tokens: int
    total_cost_estimate: float
    summary_ref: JsonObject | None
    schema_version: str = "tokenshare.paper_experiment_result.v1"

    def __post_init__(self) -> None:
        _status_value("status", PaperStatus, self.status)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "status": _status_value("status", PaperStatus, self.status),
            "condition_ids": list(self.condition_ids),
            "run_count": self.run_count,
            "task_count": self.task_count,
            "completion_rate": float(self.completion_rate),
            "accepted_validity_rate": float(self.accepted_validity_rate),
            "total_tokens": self.total_tokens,
            "total_cost_estimate": float(self.total_cost_estimate),
            "summary_ref": _json_value(self.summary_ref),
        }


@dataclass(frozen=True, kw_only=True)
class PaperConditionResult:
    condition_id: str
    status: PaperStatus | str
    repeat_count: int
    task_count: int
    completed_root_count: int
    failed_root_count: int
    blocked_root_count: int
    provider_attempt_count: int
    metrics_ref: JsonObject | None
    schema_version: str = "tokenshare.paper_condition_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "status": _status_value("status", PaperStatus, self.status),
            "repeat_count": self.repeat_count,
            "task_count": self.task_count,
            "completed_root_count": self.completed_root_count,
            "failed_root_count": self.failed_root_count,
            "blocked_root_count": self.blocked_root_count,
            "provider_attempt_count": self.provider_attempt_count,
            "metrics_ref": _json_value(self.metrics_ref),
        }


@dataclass(frozen=True, kw_only=True)
class PaperRunResult:
    condition_id: str
    repeat_id: int
    run_id: str
    status: PaperStatus | str
    run_manifest_ref: JsonObject | None
    per_task_results_ref: JsonObject | None
    per_attempt_results_ref: JsonObject | None
    fault_injections_ref: JsonObject | None
    event_log_ref: JsonObject | None
    artifact_root: str
    paper_eligible: bool
    ineligibility_reasons: list[str] | tuple[str, ...]
    schema_version: str = "tokenshare.paper_run_result.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "status": _status_value("status", PaperStatus, self.status),
            "run_manifest_ref": _json_value(self.run_manifest_ref),
            "per_task_results_ref": _json_value(self.per_task_results_ref),
            "per_attempt_results_ref": _json_value(self.per_attempt_results_ref),
            "fault_injections_ref": _json_value(self.fault_injections_ref),
            "event_log_ref": _json_value(self.event_log_ref),
            "artifact_root": self.artifact_root,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
        }


@dataclass(frozen=True, kw_only=True)
class PaperTaskResult:
    condition_id: str
    repeat_id: int
    task_id: str
    domain: str
    difficulty: str
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    root_status: PaperTaskStatus | str
    accepted_validity: bool | None
    failure_stage: PaperFailureStage | str | None
    failure_kind: PaperFailureKind | str | None
    attempt_count: int
    provider_attempt_count: int
    wall_clock_ms: int
    total_tokens: int | None
    cost_estimate: float | None
    event_refs: list[JsonObject] | tuple[JsonObject, ...]
    artifact_refs: list[JsonObject] | tuple[JsonObject, ...]
    paper_eligible: bool
    cost_estimate_currency: str | None = None
    cost_estimate_status: str | None = None
    schema_version: str = "tokenshare.paper_task_result.v1"

    def __post_init__(self) -> None:
        if self.schema_version == "tokenshare.paper_task_result.v1":
            if type(self.total_tokens) is not int or self.total_tokens < 0:
                raise ValueError("paper task result v1 requires numeric total_tokens")
            if (
                isinstance(self.cost_estimate, bool)
                or not isinstance(self.cost_estimate, (int, float))
                or float(self.cost_estimate) < 0.0
            ):
                raise ValueError("paper task result v1 requires numeric cost_estimate")
            return
        if self.schema_version != "tokenshare.paper_task_result.v2":
            raise ValueError("unsupported paper task result schema")
        if self.cost_estimate_status not in {"usage_missing", "usage_invalid"}:
            raise ValueError("paper task result v2 requires missing usage status")
        if self.total_tokens is not None and (
            type(self.total_tokens) is not int or self.total_tokens < 0
        ):
            raise ValueError("paper task result v2 has invalid total_tokens")
        if self.cost_estimate is not None and (
            isinstance(self.cost_estimate, bool)
            or not isinstance(self.cost_estimate, (int, float))
            or float(self.cost_estimate) < 0.0
        ):
            raise ValueError("paper task result v2 has invalid cost_estimate")
        if self.total_tokens is not None and self.cost_estimate is not None:
            raise ValueError("paper task result v2 requires a nullable usage field")

    def to_dict(self) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "task_id": self.task_id,
            "domain": self.domain,
            "difficulty": self.difficulty,
            "paper_difficulty": _resolve_paper_difficulty(
                self.domain,
                self.difficulty,
                self.paper_difficulty,
            ),
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "root_status": _status_value("root_status", PaperTaskStatus, self.root_status),
            "accepted_validity": self.accepted_validity,
            "failure_stage": _optional_status_value(
                "failure_stage",
                PaperFailureStage,
                self.failure_stage,
            ),
            "failure_kind": _optional_status_value(
                "failure_kind",
                PaperFailureKind,
                self.failure_kind,
            ),
            "attempt_count": self.attempt_count,
            "provider_attempt_count": self.provider_attempt_count,
            "wall_clock_ms": self.wall_clock_ms,
            "total_tokens": self.total_tokens,
            "cost_estimate": (
                float(self.cost_estimate)
                if self.cost_estimate is not None
                else None
            ),
            "event_refs": _json_value(list(self.event_refs)),
            "artifact_refs": _json_value(list(self.artifact_refs)),
            "paper_eligible": self.paper_eligible,
        }
        if self.cost_estimate_currency is not None:
            body["cost_estimate_currency"] = self.cost_estimate_currency
        if self.cost_estimate_status is not None:
            body["cost_estimate_status"] = self.cost_estimate_status
        return body


@dataclass(frozen=True, kw_only=True)
class PaperAttemptResult:
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    worker_id: str
    provider_attempt_index: int
    attempt_status: PaperAttemptStatus | str
    provider: str | None
    model: str | None
    entry_id: str | None
    request_ref: JsonObject | None
    raw_output_ref: JsonObject | None
    parsed_output_ref: JsonObject | None
    parse_failure_ref: JsonObject | None
    provenance_ref: JsonObject | None
    usage_ref: JsonObject | None
    started_at: str
    ended_at: str
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_estimate: float | None
    error_kind: str | None
    fault_injection_ref: JsonObject | None
    paper_eligible: bool
    model_execution_record_ref: JsonObject | None = None
    provider_attempt_count: int = 0
    paper_difficulty: str | None = None
    topic_family: str | None = None
    topic_family_version: str | None = None
    construction_rule_id: str | None = None
    oracle_package_group: str | None = None
    proof_assembly_shape: str | None = None
    lemma_node_id: str | None = None
    slot_key: str | None = None
    dependency_path: list[str] | tuple[str, ...] | None = None
    planned_ai_unit_id: str | None = None
    cost_estimate_currency: str | None = None
    cost_estimate_status: str | None = None
    executor_id: str | None = None
    executor_type: str | None = None
    schema_version: str = "tokenshare.paper_attempt_result.v1"

    def __post_init__(self) -> None:
        status = PaperAttemptStatus(self.attempt_status)
        if status == PaperAttemptStatus.EXECUTOR_ERROR:
            if self.schema_version != "tokenshare.paper_attempt_result.v2":
                raise ValueError("executor_error attempt requires v2 schema")
            for field_name in (
                "provider_attempt_count",
                "provider_attempt_index",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                if type(getattr(self, field_name)) is not int or getattr(
                    self, field_name
                ) != 0:
                    raise ValueError(
                        f"executor_error attempt requires integer zero {field_name}"
                    )
            if (
                isinstance(self.cost_estimate, bool)
                or not isinstance(self.cost_estimate, (int, float))
                or float(self.cost_estimate) != 0.0
            ):
                raise ValueError("executor_error attempt requires zero provider cost")
            if type(self.latency_ms) is not int or self.latency_ms < 0:
                raise ValueError(
                    "executor_error attempt requires non-negative integer latency_ms"
                )
            if any(value is not None for value in (self.provider, self.model, self.entry_id)):
                raise ValueError("executor_error attempt cannot claim provider identity")
            executor_source = (self.executor_id, self.executor_type)
            if executor_source not in {
                ("executor_factorization_runtime", "deterministic_local"),
                ("executor_ai_api", "ai_api_pre_provider"),
            }:
                raise ValueError("executor_error attempt source is unsupported")
            _require_non_empty("error_kind", self.error_kind)
            if not isinstance(self.request_ref, Mapping) or not self.request_ref:
                raise ValueError("executor_error attempt requires request_ref")
            if any(
                value is not None
                for value in (
                    self.raw_output_ref,
                    self.parsed_output_ref,
                    self.parse_failure_ref,
                    self.usage_ref,
                    self.fault_injection_ref,
                    self.model_execution_record_ref,
                )
            ):
                raise ValueError("executor_error attempt cannot claim provider artifacts")
            if executor_source == (
                "executor_factorization_runtime",
                "deterministic_local",
            ):
                if self.provenance_ref is not None:
                    raise ValueError(
                        "deterministic executor_error cannot claim AI provenance"
                    )
            elif self.provenance_ref is not None and (
                not isinstance(self.provenance_ref, Mapping)
                or not self.provenance_ref
            ):
                raise ValueError(
                    "AI pre-provider executor_error provenance_ref must be a "
                    "non-empty object when present"
                )
            if self.paper_eligible is not False:
                raise ValueError("executor_error attempt must be paper-ineligible")
            return
        if self.schema_version == "tokenshare.paper_attempt_result.v3":
            for field_name in (
                "latency_ms",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                value = getattr(self, field_name)
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError(
                        f"nullable provider attempt has invalid {field_name}"
                    )
            if self.cost_estimate is not None and (
                isinstance(self.cost_estimate, bool)
                or not isinstance(self.cost_estimate, (int, float))
                or float(self.cost_estimate) < 0.0
            ):
                raise ValueError(
                    "nullable provider attempt has invalid cost_estimate"
                )
            usage_missing = any(
                value is None
                for value in (
                    self.prompt_tokens,
                    self.completion_tokens,
                    self.total_tokens,
                    self.cost_estimate,
                )
            )
            latency_missing = self.latency_ms is None
            if not usage_missing and not latency_missing:
                raise ValueError(
                    "nullable provider attempt requires a missing observation"
                )
            if usage_missing and self.cost_estimate_status not in {
                "usage_missing",
                "usage_invalid",
            }:
                raise ValueError(
                    "nullable provider attempt requires explicit missing usage status"
                )
            if (
                not usage_missing
                and (
                    not isinstance(self.cost_estimate_status, str)
                    or not self.cost_estimate_status
                    or self.cost_estimate_status in {"usage_missing", "usage_invalid"}
                )
            ):
                raise ValueError(
                    "latency-only missing attempt requires observed usage status"
                )
            return
        if self.schema_version != "tokenshare.paper_attempt_result.v1":
            raise ValueError("provider-dispatched attempt requires v1 or v3 schema")

    def to_dict(self) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "provider_attempt_index": self.provider_attempt_index,
            "attempt_status": _status_value(
                "attempt_status",
                PaperAttemptStatus,
                self.attempt_status,
            ),
            "provider": self.provider,
            "model": self.model,
            "entry_id": self.entry_id,
            "request_ref": _json_value(self.request_ref),
            "raw_output_ref": _json_value(self.raw_output_ref),
            "parsed_output_ref": _json_value(self.parsed_output_ref),
            "parse_failure_ref": _json_value(self.parse_failure_ref),
            "provenance_ref": _json_value(self.provenance_ref),
            "usage_ref": _json_value(self.usage_ref),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_estimate": (
                float(self.cost_estimate)
                if self.cost_estimate is not None
                else None
            ),
            "error_kind": self.error_kind,
            "fault_injection_ref": _json_value(self.fault_injection_ref),
            "paper_eligible": self.paper_eligible,
            "model_execution_record_ref": _json_value(
                self.model_execution_record_ref
            ),
            "provider_attempt_count": self.provider_attempt_count,
            "paper_difficulty": self.paper_difficulty,
            "topic_family": self.topic_family,
            "topic_family_version": self.topic_family_version,
            "construction_rule_id": self.construction_rule_id,
            "oracle_package_group": self.oracle_package_group,
            "proof_assembly_shape": self.proof_assembly_shape,
            "lemma_node_id": self.lemma_node_id,
            "slot_key": self.slot_key,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "dependency_path": (
                list(self.dependency_path)
                if self.dependency_path is not None
                else None
            ),
        }
        if self.cost_estimate_currency is not None:
            body["cost_estimate_currency"] = self.cost_estimate_currency
        if self.cost_estimate_status is not None:
            body["cost_estimate_status"] = self.cost_estimate_status
        if self.executor_id is not None:
            body["executor_id"] = self.executor_id
        if self.executor_type is not None:
            body["executor_type"] = self.executor_type
        return body


@dataclass(frozen=True, kw_only=True)
class PaperModelExecutionRecord:
    """正式 fixed-entry AI unit 的预期身份与实际执行事实。"""

    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    expected_identity: JsonObject
    source_provider_config_digest: str
    prepared_execution_config_digest: str
    request_ref: JsonObject
    provenance_ref: JsonObject
    raw_output_ref: JsonObject | None
    usage_ref: JsonObject
    actual_request_identities: list[JsonObject] | tuple[JsonObject, ...]
    actual_provider_attempts: list[JsonObject] | tuple[JsonObject, ...]
    requested_model: str | None
    resolved_model: str | None
    response_model_status: str
    identity_status: str
    mismatch_reasons: list[str] | tuple[str, ...]
    paper_eligible: bool
    created_at: str
    schema_version: str = "tokenshare.paper_model_execution_record.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.paper_model_execution_record.v2":
            raise ValueError(
                "schema_version must be tokenshare.paper_model_execution_record.v2"
            )
        for field_name in (
            "condition_id",
            "run_id",
            "task_id",
            "unit_id",
            "attempt_id",
            "created_at",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        _require_integer("repeat_id", self.repeat_id, min_value=0)
        _require_identity_digest(
            "source_provider_config_digest",
            self.source_provider_config_digest,
        )
        _require_identity_digest(
            "prepared_execution_config_digest",
            self.prepared_execution_config_digest,
        )
        if self.identity_status not in {
            "matched",
            "model_identity_mismatch",
            "not_observed",
        }:
            raise ValueError(
                "identity_status must be matched, model_identity_mismatch, or not_observed"
            )
        if self.response_model_status not in {
            "present",
            "missing",
            "null",
            "empty",
            "invalid_type",
            "unavailable",
        }:
            raise ValueError("invalid response_model_status")
        if self.requested_model is not None:
            _require_non_empty("requested_model", self.requested_model)
        if self.resolved_model is not None:
            _require_non_empty("resolved_model", self.resolved_model)
        if self.response_model_status == "present" and self.resolved_model is None:
            raise ValueError("present response model requires resolved_model")
        if self.response_model_status != "present" and self.resolved_model is not None:
            raise ValueError(
                "resolved_model must be null unless response_model_status is present"
            )
        stable_reasons = tuple(dict.fromkeys(str(item) for item in self.mismatch_reasons))
        if self.identity_status == "matched" and stable_reasons:
            raise ValueError("matched model execution record cannot have mismatch_reasons")
        if self.identity_status == "model_identity_mismatch" and not stable_reasons:
            raise ValueError("model identity mismatch requires mismatch_reasons")
        if self.identity_status == "model_identity_mismatch" and self.paper_eligible:
            raise ValueError("model identity mismatch cannot be paper eligible")
        if self.identity_status == "not_observed" and stable_reasons:
            raise ValueError("not_observed record cannot have mismatch_reasons")
        if self.identity_status == "not_observed" and self.paper_eligible:
            raise ValueError("not_observed record cannot be paper eligible")
        if (
            self.identity_status == "not_observed"
            and self.response_model_status != "unavailable"
        ):
            raise ValueError("not_observed record requires unavailable response model")
        if self.identity_status == "not_observed" and self.raw_output_ref is not None:
            raise ValueError("not_observed record cannot reference a raw output")
        if self.identity_status == "matched" and self.response_model_status != "present":
            raise ValueError("matched record requires a present response model")
        if self.identity_status == "matched" and self.requested_model is None:
            raise ValueError("matched record requires requested_model")
        if self.identity_status == "matched" and self.raw_output_ref is None:
            raise ValueError("matched record requires raw_output_ref")
        if self.identity_status == "matched":
            expected_model = (
                self.expected_identity.get("provider_model_id")
                if isinstance(self.expected_identity, Mapping)
                else None
            )
            if (
                not isinstance(expected_model, str)
                or not expected_model
                or self.resolved_model != expected_model
            ):
                raise ValueError(
                    "matched resolved_model must equal expected_identity provider_model_id"
                )
            if self.requested_model != expected_model:
                raise ValueError(
                    "matched requested_model must equal expected_identity provider_model_id"
                )
        object.__setattr__(self, "expected_identity", _json_value(self.expected_identity))
        object.__setattr__(self, "request_ref", _json_value(self.request_ref))
        object.__setattr__(self, "provenance_ref", _json_value(self.provenance_ref))
        object.__setattr__(self, "raw_output_ref", _json_value(self.raw_output_ref))
        object.__setattr__(self, "usage_ref", _json_value(self.usage_ref))
        object.__setattr__(
            self,
            "actual_request_identities",
            tuple(_json_value(item) for item in self.actual_request_identities),
        )
        object.__setattr__(
            self,
            "actual_provider_attempts",
            tuple(_json_value(item) for item in self.actual_provider_attempts),
        )
        object.__setattr__(self, "mismatch_reasons", stable_reasons)

    @property
    def record_digest(self) -> str:
        return digest_json(self._body(include_digest=False))

    def to_dict(self) -> JsonObject:
        return self._body(include_digest=True)

    def _body(self, *, include_digest: bool) -> JsonObject:
        body: JsonObject = {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "expected_identity": _json_value(self.expected_identity),
            "source_provider_config_digest": self.source_provider_config_digest,
            "prepared_execution_config_digest": self.prepared_execution_config_digest,
            "request_ref": _json_value(self.request_ref),
            "provenance_ref": _json_value(self.provenance_ref),
            "raw_output_ref": _json_value(self.raw_output_ref),
            "usage_ref": _json_value(self.usage_ref),
            "actual_request_identities": _json_value(
                list(self.actual_request_identities)
            ),
            "actual_provider_attempts": _json_value(
                list(self.actual_provider_attempts)
            ),
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "response_model_status": self.response_model_status,
            "identity_status": self.identity_status,
            "mismatch_reasons": list(self.mismatch_reasons),
            "paper_eligible": self.paper_eligible,
            "created_at": self.created_at,
        }
        if include_digest:
            body["record_digest"] = self.record_digest
        return body


@dataclass(frozen=True, kw_only=True)
class PaperEligibilityReport:
    paper_eligible: bool
    ineligibility_reasons: list[str] | tuple[str, ...]
    checked_attempt_count: int
    real_transport: bool
    transport_kind: str
    secret_scan_passed: bool
    schema_version: str = "tokenshare.paper_eligibility_report.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "paper_eligible": self.paper_eligible,
            "ineligibility_reasons": list(self.ineligibility_reasons),
            "checked_attempt_count": self.checked_attempt_count,
            "real_transport": self.real_transport,
            "transport_kind": self.transport_kind,
            "secret_scan_passed": self.secret_scan_passed,
        }


@dataclass(frozen=True, kw_only=True)
class PaperBudgetResult:
    budget_digest: str
    planned_experiments: list[str] | tuple[str, ...]
    planned_conditions: int
    planned_root_runs: int
    planned_ai_units: int
    max_provider_attempts: int
    token_upper_bound: int
    cost_upper_bound: float
    wall_clock_estimate: float
    quota_preflight: JsonObject
    rate_limit_preflight: JsonObject
    disk_estimate: JsonObject
    status: PaperStatus | str
    budget_mode: str | None = None
    approval_required: bool | None = None
    approval_mode: str | None = None
    authorization_source: str | None = None
    hard_limits: JsonObject | None = None
    schema_version: str = "tokenshare.paper_budget_result.v1"

    def to_dict(self) -> JsonObject:
        body = {
            "schema_version": self.schema_version,
            "budget_digest": self.budget_digest,
            "planned_experiments": list(self.planned_experiments),
            "planned_conditions": self.planned_conditions,
            "planned_root_runs": self.planned_root_runs,
            "planned_ai_units": self.planned_ai_units,
            "max_provider_attempts": self.max_provider_attempts,
            "token_upper_bound": self.token_upper_bound,
            "cost_upper_bound": float(self.cost_upper_bound),
            "wall_clock_estimate": float(self.wall_clock_estimate),
            "quota_preflight": _json_value(self.quota_preflight),
            "rate_limit_preflight": _json_value(self.rate_limit_preflight),
            "disk_estimate": _json_value(self.disk_estimate),
            "status": _status_value("status", PaperStatus, self.status),
        }
        if self.budget_mode is not None:
            body.update(
                {
                    "budget_mode": self.budget_mode,
                    "approval_required": self.approval_required,
                    "approval_mode": self.approval_mode,
                    "authorization_source": self.authorization_source,
                    "hard_limits": _json_value(self.hard_limits or {}),
                }
            )
        return body


def digest_json(data: Any) -> str:
    encoded = json.dumps(
        _json_value(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def evaluate_versioned_paper_evidence(
    facts: PaperEvidenceEligibilityFacts | Mapping[str, Any],
) -> VersionedPaperEvidenceEligibilityReport:
    """按版本化事实判定 eligibility；不推导或构造协议运行时状态。"""

    decoded = (
        facts
        if isinstance(facts, PaperEvidenceEligibilityFacts)
        else PaperEvidenceEligibilityFacts.from_mapping(facts)
    )
    reasons: list[str] = []
    always_ineligible = {"capability_only", "historical", "synthetic"}
    if decoded.evidence_class in always_ineligible:
        reasons.append(
            f"evidence_class_always_ineligible:{decoded.evidence_class}"
        )
    if decoded.evidence_class == "regression_only" or decoded.regression_only:
        reasons.append("regression_only")

    unit_identities = _validated_executed_unit_identities(decoded, reasons)
    source_manifest_complete = False
    if decoded.evidence_class in {
        "online_real_provider",
        "real_model_trace_protocol_run",
    }:
        if not decoded.direct_evidence_complete:
            reasons.append("direct_evidence_incomplete")
        if not decoded.identity_consistent:
            reasons.append("current_identity_inconsistent")

    if decoded.evidence_class == "online_real_provider":
        if decoded.source_classification != "current_real_provider":
            reasons.append("online_source_classification_invalid")
        _extend_online_identity_reasons(decoded, unit_identities, reasons)

    if decoded.evidence_class == "real_model_trace_protocol_run":
        if decoded.current_provider_call_count != 0:
            reasons.append("trace_current_provider_calls_must_be_zero")
        if decoded.current_real_provider_attempt_refs:
            reasons.append("trace_current_provider_attempt_refs_must_be_empty")
        if decoded.source_classification != "approved_real_full_acquisition":
            reasons.append("trace_source_not_approved_real_full_acquisition")
        source_manifest_complete = _extend_trace_acquisition_reasons(
            decoded,
            unit_identities,
            reasons,
        )

    unique_reasons = tuple(dict.fromkeys(reasons))
    return VersionedPaperEvidenceEligibilityReport(
        paper_eligible=not unique_reasons,
        ineligibility_reasons=unique_reasons,
        evidence_class=decoded.evidence_class,
        source_classification=decoded.source_classification,
        executed_ai_unit_count=decoded.executed_ai_unit_count,
        current_provider_call_count=decoded.current_provider_call_count,
        source_provider_call_count=decoded.source_provider_call_count,
        direct_evidence_complete=decoded.direct_evidence_complete,
        identity_consistent=decoded.identity_consistent,
        source_manifest_complete=source_manifest_complete,
    )


def _validated_executed_unit_identities(
    facts: PaperEvidenceEligibilityFacts,
    reasons: list[str],
) -> dict[str, tuple[str, str]]:
    from tokenshare.experiments.paper_unit_commitments import (
        validate_ai_unit_binding,
    )

    identities: dict[str, tuple[str, str]] = {}
    planned_ids: set[str] = set()
    valid = len(facts.executed_unit_bindings) == facts.executed_ai_unit_count
    for frozen in facts.executed_unit_bindings:
        binding = _thaw_json(frozen)
        try:
            validate_ai_unit_binding(binding)
        except (TypeError, ValueError):
            valid = False
            continue
        unit_id = binding["unit_id"]
        planned_id = binding["planned_ai_unit_id"]
        binding_digest = binding["binding_digest"]
        if unit_id in identities or planned_id in planned_ids:
            valid = False
            continue
        identities[unit_id] = (planned_id, binding_digest)
        planned_ids.add(planned_id)
    if not valid or len(identities) != facts.executed_ai_unit_count:
        reasons.append("executed_unit_identity_invalid")
    return identities


def _extend_online_identity_reasons(
    facts: PaperEvidenceEligibilityFacts,
    unit_identities: Mapping[str, tuple[str, str]],
    reasons: list[str],
) -> None:
    attempt_keys = {
        "schema_version",
        "planned_ai_unit_id",
        "unit_id",
        "unit_binding_digest",
        "attempt_id",
        "request_identity_digest",
        "real_transport",
        "identity_consistent",
    }
    lifecycle_keys = {
        "schema_version",
        "planned_ai_unit_id",
        "unit_id",
        "unit_binding_digest",
        "attempt_ref",
        "event_ref",
        "artifact_ref",
        "terminal_ref",
    }
    attempt_links: set[tuple[str, str, str, str]] = set()
    lifecycle_links: set[tuple[str, str, str, str]] = set()
    valid = (
        facts.current_provider_call_count == facts.executed_ai_unit_count
        and facts.source_provider_call_count == 0
        and len(facts.current_real_provider_attempt_refs)
        == facts.executed_ai_unit_count
        and len(facts.current_lifecycle_refs) == facts.executed_ai_unit_count
    )
    for frozen in facts.current_real_provider_attempt_refs:
        ref = _thaw_json(frozen)
        identity = unit_identities.get(ref.get("unit_id"))
        if (
            set(ref) != attempt_keys
            or ref.get("schema_version")
            != "tokenshare.paper_current_online_attempt_ref.v1"
            or identity
            != (ref.get("planned_ai_unit_id"), ref.get("unit_binding_digest"))
            or not _non_empty_strings(ref, ("attempt_id",))
            or not _is_identity_digest(ref.get("request_identity_digest"))
            or ref.get("real_transport") is not True
            or ref.get("identity_consistent") is not True
        ):
            valid = False
            continue
        attempt_links.add(
            (
                ref["planned_ai_unit_id"],
                ref["unit_id"],
                ref["unit_binding_digest"],
                ref["attempt_id"],
            )
        )
    for frozen in facts.current_lifecycle_refs:
        ref = _thaw_json(frozen)
        identity = unit_identities.get(ref.get("unit_id"))
        if (
            set(ref) != lifecycle_keys
            or ref.get("schema_version")
            != "tokenshare.paper_current_online_lifecycle_ref.v1"
            or identity
            != (ref.get("planned_ai_unit_id"), ref.get("unit_binding_digest"))
            or not _non_empty_strings(
                ref,
                ("attempt_ref", "event_ref", "artifact_ref", "terminal_ref"),
            )
        ):
            valid = False
            continue
        lifecycle_links.add(
            (
                ref["planned_ai_unit_id"],
                ref["unit_id"],
                ref["unit_binding_digest"],
                ref["attempt_ref"],
            )
        )
    if (
        len(attempt_links) != facts.executed_ai_unit_count
        or len(lifecycle_links) != facts.executed_ai_unit_count
        or attempt_links != lifecycle_links
    ):
        valid = False
    if not valid:
        reasons.extend(
            (
                "current_real_provider_attempt_per_executed_unit_required",
                "online_unit_attempt_identity_mismatch",
            )
        )
    if (
        facts.trace_source_bindings
        or facts.source_manifest is not None
        or facts.source_inventory_rows
        or facts.source_entries
        or facts.paid_receipt_claim is not None
    ):
        reasons.append("online_trace_source_evidence_forbidden")


def _extend_trace_acquisition_reasons(
    facts: PaperEvidenceEligibilityFacts,
    unit_identities: Mapping[str, tuple[str, str]],
    reasons: list[str],
) -> bool:
    manifest: ResponseBankManifest | None = None
    rows: tuple[ResponseBankInventoryRow, ...] = ()
    entries: tuple[ResponseBankEntry, ...] = ()
    source_valid = True
    try:
        if facts.source_manifest is None:
            raise ValueError("source manifest missing")
        manifest = ResponseBankManifest.from_dict(_thaw_json(facts.source_manifest))
        rows = tuple(
            ResponseBankInventoryRow.from_dict(_thaw_json(row))
            for row in facts.source_inventory_rows
        )
        entries = tuple(
            ResponseBankEntry.from_dict(_thaw_json(entry))
            for entry in facts.source_entries
        )
        ValidatedResponseBankIndex.build(manifest, rows, entries)
        _require_identity_digest(
            "root_binding_marker_digest",
            manifest.root_binding_marker_digest,
        )
        acquisition_attempt_ids = tuple(entry.acquisition_state_ref for entry in entries)
        if (
            any(not item for item in acquisition_attempt_ids)
            or len(set(acquisition_attempt_ids)) != len(acquisition_attempt_ids)
        ):
            raise ValueError("acquisition attempt identity coverage invalid")
    except (KeyError, TypeError, ValueError):
        source_valid = False
        reasons.append("canonical_source_manifest_invalid")

    _extend_receipt_manifest_reasons(facts.paid_receipt_claim, manifest, reasons)
    if manifest is not None and facts.source_provider_call_count != len(
        manifest.entry_ids
    ):
        source_valid = False
        reasons.append("source_provider_call_count_incomplete")
    if source_valid and manifest is not None:
        source_valid = _trace_identity_chain_complete(
            facts,
            unit_identities,
            manifest,
            rows,
            entries,
            reasons,
        )
    return source_valid


def _extend_receipt_manifest_reasons(
    receipt: Mapping[str, Any] | None,
    manifest: ResponseBankManifest | None,
    reasons: list[str],
) -> None:
    expected_keys = {
        "schema_version",
        "receipt_scope",
        "receipt_digest",
        "manifest_digest",
    }
    if receipt is None:
        reasons.append("paid_full_acquisition_receipt_required")
        return
    if set(receipt) != expected_keys:
        reasons.append("paid_receipt_claim_keys_invalid")
    if receipt.get("schema_version") != PAID_EXECUTION_RECEIPT_CLAIM_SCHEMA:
        reasons.append("paid_receipt_schema_invalid")
    if receipt.get("receipt_scope") != EPD027_FULL_BANK_ACQUISITION_SCOPE:
        reasons.append("paid_receipt_scope_invalid")
    for field_name in ("receipt_digest", "manifest_digest"):
        if not _is_identity_digest(receipt.get(field_name)):
            reasons.append(f"paid_receipt_{field_name}_invalid")
    if manifest is not None:
        if receipt.get("manifest_digest") != manifest.manifest_digest:
            reasons.append("paid_receipt_manifest_digest_mismatch")
        if receipt.get("receipt_digest") != manifest.created_by_paid_receipt_digest:
            reasons.append("paid_receipt_manifest_creator_mismatch")


def _trace_identity_chain_complete(
    facts: PaperEvidenceEligibilityFacts,
    unit_identities: Mapping[str, tuple[str, str]],
    manifest: ResponseBankManifest,
    rows: tuple[ResponseBankInventoryRow, ...],
    entries: tuple[ResponseBankEntry, ...],
    reasons: list[str],
) -> bool:
    try:
        bindings = tuple(
            TraceSourceBinding.from_dict(_thaw_json(binding))
            for binding in facts.trace_source_bindings
        )
        wrappers = tuple(
            CurrentTraceWrapper.from_dict(_thaw_json(wrapper))
            for wrapper in facts.current_lifecycle_refs
        )
    except (KeyError, TypeError, ValueError):
        reasons.append("current_trace_identity_chain_invalid")
        return False
    valid = (
        len(bindings) == facts.executed_ai_unit_count
        and len(wrappers) >= facts.executed_ai_unit_count
    )
    bindings_by_planned = {binding.planned_ai_unit_id: binding for binding in bindings}
    wrappers_by_unit: dict[str, list[CurrentTraceWrapper]] = {}
    for wrapper in wrappers:
        wrappers_by_unit.setdefault(wrapper.current_unit_id, []).append(wrapper)
    if (
        len(bindings_by_planned) != len(bindings)
        or set(bindings_by_planned)
        != {identity[0] for identity in unit_identities.values()}
        or set(wrappers_by_unit) != set(unit_identities)
    ):
        valid = False
    entries_by_id = {entry.entry_id: entry for entry in entries}
    rows_by_entry_id = {row.entry_id: row for row in rows}
    domains_by_unit = _trace_domain_kinds_by_unit(facts)
    if set(domains_by_unit) != set(unit_identities):
        valid = False
    covered_entries: list[str] = []
    for binding in bindings:
        if (
            binding.source_evidence_class
            != APPROVED_REAL_SOURCE_EVIDENCE_CLASS
            or binding.bank_root_id != manifest.bank_root_id
            or binding.manifest_digest != manifest.manifest_digest
        ):
            valid = False
        for replacement in binding.replacements:
            covered_entries.append(replacement.entry_id)
            row = rows_by_entry_id.get(replacement.entry_id)
            entry = entries_by_id.get(replacement.entry_id)
            if (
                row is None
                or entry is None
                or row.entry_id != entry.entry_id
                or row.inventory_entry_id != entry.inventory_entry_id
                or row.semantic_slot_key != entry.semantic_slot_key
                or row.inference_request_digest
                != replacement.inference_request_digest
                or row.inference_request_digest != entry.inference_request_digest
                or row.sample_slot_index != entry.sample_slot_index
                or row.replacement_slot != entry.replacement_slot
            ):
                valid = False
    attempt_ids: set[str] = set()
    for unit_id, (planned_id, _) in unit_identities.items():
        binding = bindings_by_planned.get(planned_id)
        unit_wrappers = wrappers_by_unit.get(unit_id)
        if binding is None or not unit_wrappers:
            valid = False
            continue
        for wrapper in unit_wrappers:
            try:
                replacement = binding.replacement(wrapper.attempt_ordinal)
                entry = entries_by_id[wrapper.entry_id]
            except (KeyError, TypeError, ValueError):
                valid = False
                continue
            locator_digests = {
                locator.object_role: locator.object_digest
                for locator in entry.object_locators
            }
            if (
                wrapper.bank_root_id != manifest.bank_root_id
                or wrapper.manifest_digest != manifest.manifest_digest
                or wrapper.root_binding_marker_digest
                != manifest.root_binding_marker_digest
                or replacement.entry_id != entry.entry_id
                or replacement.inference_request_digest
                != entry.inference_request_digest
                or wrapper.inference_request_digest != entry.inference_request_digest
                or wrapper.locator_digests != locator_digests
                or type(wrapper.attempt_ordinal) is not int
                or wrapper.attempt_ordinal < 0
                or type(wrapper.source_latency_ms) is not int
                or wrapper.source_latency_ms < 0
                or not _trace_terminal_chain_valid(
                    wrapper,
                    entry,
                    domains_by_unit.get(unit_id),
                )
                or not _non_empty_strings(
                    wrapper.__dict__,
                    (
                        "current_run_id",
                        "current_task_id",
                        "current_unit_id",
                        "current_attempt_id",
                        "logical_started_at",
                        "logical_finished_at",
                        "current_ledger_ref",
                    ),
                )
            ):
                valid = False
            attempt_ids.add(wrapper.current_attempt_id)
    if (
        len(attempt_ids) != len(wrappers)
        or not covered_entries
        or not set(covered_entries) <= set(manifest.entry_ids)
    ):
        valid = False
    if not valid:
        reasons.append("current_trace_identity_chain_invalid")
    return valid


def _trace_domain_kinds_by_unit(
    facts: PaperEvidenceEligibilityFacts,
) -> dict[str, str]:
    kinds: dict[str, str] = {}
    allowed_commitment_kinds = {
        "factorization": {"factorization_range.v1"},
        "lean_proof": {"lean_simple_child.v1", "lean_lemma_dag_node.v1"},
    }
    for frozen in facts.executed_unit_bindings:
        binding = _thaw_json(frozen)
        commitment = binding.get("domain_unit_commitment")
        if not isinstance(commitment, Mapping):
            continue
        unit_id = binding.get("unit_id")
        planned_id = binding.get("planned_ai_unit_id")
        domain = commitment.get("domain")
        if (
            not isinstance(unit_id, str)
            or commitment.get("schema_version")
            != "tokenshare.paper_domain_unit_commitment.v1"
            or commitment.get("planned_ai_unit_id") != planned_id
            or commitment.get("unit_id") != unit_id
            or domain not in allowed_commitment_kinds
            or commitment.get("commitment_kind")
            not in allowed_commitment_kinds[domain]
        ):
            continue
        kinds[unit_id] = domain
    return kinds


def _trace_terminal_chain_valid(
    wrapper: CurrentTraceWrapper,
    entry: ResponseBankEntry,
    domain: str | None,
) -> bool:
    stage_refs = (
        wrapper.current_parse_ref,
        wrapper.current_verifier_ref,
        wrapper.current_checker_ref,
        wrapper.current_canonical_ref,
    )
    if entry.terminal_kind == "provider_failure":
        return all(ref is None for ref in stage_refs)
    if entry.terminal_kind != "success":
        return False
    if domain == "factorization":
        return (
            _non_empty_strings(
                wrapper.__dict__,
                ("current_parse_ref", "current_verifier_ref", "current_canonical_ref"),
            )
            and wrapper.current_checker_ref is None
        )
    if domain == "lean_proof":
        return (
            _non_empty_strings(
                wrapper.__dict__,
                ("current_parse_ref", "current_checker_ref", "current_canonical_ref"),
            )
            and wrapper.current_verifier_ref is None
        )
    return False


def _non_empty_strings(value: Mapping[str, Any], names: tuple[str, ...]) -> bool:
    return all(isinstance(value.get(name), str) and bool(value.get(name)) for name in names)


def evaluate_paper_eligibility(
    *,
    attempts: list[PaperAttemptResult | JsonObject] | tuple[PaperAttemptResult | JsonObject, ...],
    run_evidence: JsonObject,
) -> PaperEligibilityReport:
    attempt_bodies = [_attempt_body(attempt) for attempt in attempts]
    evidence = dict(run_evidence)
    artifact_inventory = _artifact_inventory(evidence)
    transport_evidence = _object_or_empty(evidence.get("transport_evidence"))
    secret_scan_report = _object_or_empty(evidence.get("secret_scan_report"))
    real_transport = transport_evidence.get("real_transport") is True
    transport_kind = str(transport_evidence.get("transport_kind") or "")
    secret_scan_passed = (
        secret_scan_report.get("status") == "passed"
        and int(secret_scan_report.get("leak_count", 1)) == 0
        and _positive_int(secret_scan_report.get("secret_checked_count"))
    )
    scanned_artifact_ids = {
        str(artifact_id)
        for artifact_id in secret_scan_report.get("scanned_artifact_ids", [])
    }
    reasons: list[str] = []

    if not transport_evidence:
        reasons.append("transport_evidence_missing")
    elif not _ref_verified(transport_evidence.get("evidence_ref"), artifact_inventory):
        reasons.append("transport_evidence_ref_unverified")
    if not real_transport:
        reasons.append("real_transport_required")
    if transport_kind in UNSUPPORTED_PAPER_TRANSPORTS:
        reasons.append(f"unsupported_transport:{transport_kind}")
    if transport_kind and transport_kind != "ai_api":
        reasons.append(f"unsupported_transport:{transport_kind}")
    if transport_evidence.get("config_source") != "local_gitignored_config":
        reasons.append("transport_config_source_not_local_gitignored")
    if transport_evidence.get("api_key_policy") != "env_only":
        reasons.append("transport_api_key_policy_not_env_only")
    if transport_evidence.get("executor_kind") != "ai_api_executor":
        reasons.append("transport_executor_not_ai_api")
    if not secret_scan_report:
        reasons.append("secret_scan_report_missing")
    elif not _ref_verified(secret_scan_report.get("report_ref"), artifact_inventory):
        reasons.append("secret_scan_report_ref_unverified")
    if not secret_scan_passed:
        reasons.append("secret_scan_failed")
    if not attempt_bodies:
        reasons.append("missing_provider_attempt")

    for attempt in attempt_bodies:
        reasons.extend(
            _attempt_ineligibility_reasons(
                attempt,
                artifact_inventory=artifact_inventory,
                scanned_artifact_ids=scanned_artifact_ids,
            )
        )

    unique_reasons = tuple(dict.fromkeys(reasons))
    return PaperEligibilityReport(
        paper_eligible=not unique_reasons,
        ineligibility_reasons=unique_reasons,
        checked_attempt_count=len(attempt_bodies),
        real_transport=real_transport,
        transport_kind=transport_kind,
        secret_scan_passed=secret_scan_passed,
    )


def _status_value(field_name: str, enum_type: type[Enum], value: Any) -> str:
    try:
        return enum_type(value).value
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid status") from exc


def _optional_status_value(field_name: str, enum_type: type[Enum], value: Any) -> str | None:
    if value is None:
        return None
    return _status_value(field_name, enum_type, value)


def _attempt_body(attempt: PaperAttemptResult | JsonObject) -> JsonObject:
    if isinstance(attempt, PaperAttemptResult):
        return attempt.to_dict()
    return dict(attempt)


def _attempt_ineligibility_reasons(
    attempt: JsonObject,
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
) -> list[str]:
    attempt_id = str(attempt.get("attempt_id") or "unknown")
    reasons: list[str] = []
    if attempt.get("attempt_status") == PaperAttemptStatus.EXECUTOR_ERROR.value:
        reasons.append(f"attempt:{attempt_id}:executor_error")
        if attempt.get("schema_version") != "tokenshare.paper_attempt_result.v2":
            reasons.append(f"attempt:{attempt_id}:invalid_executor_error_schema")
        for field_name in ("executor_id", "executor_type"):
            if not isinstance(attempt.get(field_name), str) or not attempt.get(
                field_name
            ):
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        if any(
            attempt.get(field_name) is not None
            for field_name in ("provider", "model", "entry_id")
        ):
            reasons.append(
                f"attempt:{attempt_id}:executor_error_claims_provider_identity"
            )
        if any(
            attempt.get(field_name) is not None
            for field_name in (
                "raw_output_ref",
                "provenance_ref",
                "usage_ref",
                "model_execution_record_ref",
            )
        ):
            reasons.append(
                f"attempt:{attempt_id}:executor_error_claims_provider_artifacts"
            )
        for field_name in (
            "provider_attempt_index",
            "provider_attempt_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            if (
                type(attempt.get(field_name)) is not int
                or attempt.get(field_name) != 0
            ):
                reasons.append(f"attempt:{attempt_id}:invalid_{field_name}")
        if (
            not isinstance(attempt.get("cost_estimate"), (int, float))
            or isinstance(attempt.get("cost_estimate"), bool)
            or float(attempt["cost_estimate"]) != 0.0
        ):
            reasons.append(f"attempt:{attempt_id}:invalid_cost_estimate")
        if attempt.get("paper_eligible") is not False:
            reasons.append(f"attempt:{attempt_id}:executor_error_marked_eligible")
        for field_name in ("started_at", "ended_at"):
            if not isinstance(attempt.get(field_name), str) or not attempt.get(
                field_name
            ):
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        _check_required_artifact_ref(
            attempt,
            "request_ref",
            attempt_id,
            reasons,
            artifact_inventory=artifact_inventory,
            scanned_artifact_ids=scanned_artifact_ids,
            allowed_types={"ExecutionRequest"},
            allowed_source_kinds=None,
        )
        return reasons
    if (
        attempt.get("attempt_status") == PaperAttemptStatus.PROVIDER_ERROR.value
        and attempt.get("schema_version")
        in {
            "tokenshare.paper_attempt_result.v1",
            "tokenshare.paper_attempt_result.v3",
        }
        and attempt.get("cost_estimate_status")
        in {"usage_missing", "usage_invalid"}
    ):
        for field_name in ("provider", "model", "entry_id"):
            if not isinstance(attempt.get(field_name), str) or not attempt.get(
                field_name
            ):
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        if not _non_negative_int(attempt.get("provider_attempt_index")):
            reasons.append(f"attempt:{attempt_id}:missing_provider_attempt_index")
        if not _positive_int(attempt.get("provider_attempt_count")):
            reasons.append(f"attempt:{attempt_id}:missing_provider_attempt_count")
        if attempt.get("latency_ms") is not None and not _non_negative_int(
            attempt.get("latency_ms")
        ):
            reasons.append(f"attempt:{attempt_id}:invalid_latency_ms")
        for field_name in ("started_at", "ended_at"):
            if not isinstance(attempt.get(field_name), str) or not attempt.get(
                field_name
            ):
                reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        for field_name, allowed_types, allowed_source_kinds in (
            ("request_ref", {"ExecutionRequest", "PromptPackage"}, None),
            (
                "provenance_ref",
                {"AIProviderCallProvenance"},
                {"ai_api_executor"},
            ),
            ("usage_ref", {"AIUsageSummary", "UsageSummary"}, None),
            (
                "model_execution_record_ref",
                {"PaperModelExecutionRecord"},
                {"ai_api_executor"},
            ),
        ):
            _check_required_artifact_ref(
                attempt,
                field_name,
                attempt_id,
                reasons,
                artifact_inventory=artifact_inventory,
                scanned_artifact_ids=scanned_artifact_ids,
                allowed_types=allowed_types,
                allowed_source_kinds=allowed_source_kinds,
            )
        if attempt.get("raw_output_ref") is not None:
            reasons.append(f"attempt:{attempt_id}:provider_error_claims_raw_output")
        if attempt.get("parsed_output_ref") is not None:
            reasons.append(f"attempt:{attempt_id}:provider_error_claims_parsed_output")
        return reasons
    if attempt.get("attempt_status") == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH.value:
        reasons.append(f"attempt:{attempt_id}:model_identity_mismatch")
    for field_name in ("provider", "model", "entry_id"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("provider_attempt_index", "latency_ms"):
        if not _non_negative_int(attempt.get(field_name)):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("prompt_tokens", "completion_tokens"):
        if not _non_negative_int(attempt.get(field_name)):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
    for field_name in ("started_at", "ended_at"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"attempt:{attempt_id}:missing_{field_name}")

    _check_required_artifact_ref(
        attempt,
        "request_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ExecutionRequest", "PromptPackage"},
        allowed_source_kinds=None,
    )
    _check_required_artifact_ref(
        attempt,
        "raw_output_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"RawModelOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "provenance_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"AIProviderCallProvenance"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "usage_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"AIUsageSummary", "UsageSummary"},
        allowed_source_kinds=None,
    )
    has_parsed = _check_optional_artifact_ref(
        attempt,
        "parsed_output_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ParsedModelOutput", "CandidateOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    has_parse_failure = _check_optional_artifact_ref(
        attempt,
        "parse_failure_ref",
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types={"ParseFailureReport"},
        allowed_source_kinds={"ai_api_executor"},
    )
    if not (has_parsed or has_parse_failure):
        reasons.append(f"attempt:{attempt_id}:missing_parsed_output_or_parse_failure_ref")
    if not _positive_int(attempt.get("total_tokens")):
        reasons.append(f"attempt:{attempt_id}:missing_total_tokens")
    if (
        _non_negative_int(attempt.get("prompt_tokens"))
        and _non_negative_int(attempt.get("completion_tokens"))
        and _positive_int(attempt.get("total_tokens"))
        and attempt["prompt_tokens"] + attempt["completion_tokens"] != attempt["total_tokens"]
    ):
        reasons.append(f"attempt:{attempt_id}:token_breakdown_mismatch")
    if not _non_negative_number(attempt.get("cost_estimate")):
        reasons.append(f"attempt:{attempt_id}:missing_cost_estimate")
    return reasons


def _check_required_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_optional_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if ref is None:
        return False
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"attempt:{attempt_id}:missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        attempt_id,
        reasons,
        artifact_inventory=artifact_inventory,
        scanned_artifact_ids=scanned_artifact_ids,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_artifact_evidence(
    ref: JsonObject,
    field_name: str,
    attempt_id: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    scanned_artifact_ids: set[str],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    artifact_id = str(ref["artifact_id"])
    manifest = artifact_inventory.get(artifact_id)
    verified = _ref_verified(ref, artifact_inventory)
    if not verified:
        reasons.append(f"attempt:{attempt_id}:unverified_{field_name}")
        return False
    if manifest is not None and manifest.get("artifact_type") not in allowed_types:
        reasons.append(f"attempt:{attempt_id}:invalid_{field_name}_type")
        verified = False
    if allowed_source_kinds is not None:
        source = _object_or_empty(manifest.get("source") if manifest is not None else None)
        if source.get("kind") not in allowed_source_kinds:
            reasons.append(f"attempt:{attempt_id}:invalid_{field_name}_source")
            verified = False
    if artifact_id not in scanned_artifact_ids:
        reasons.append(f"attempt:{attempt_id}:unscanned_{field_name}")
        verified = False
    return verified


def _artifact_inventory(evidence: JsonObject) -> dict[str, JsonObject]:
    manifests = evidence.get("artifact_manifests", [])
    if isinstance(manifests, dict):
        candidates = manifests.values()
    elif isinstance(manifests, list):
        candidates = manifests
    else:
        candidates = []
    inventory: dict[str, JsonObject] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and _has_complete_artifact_ref(candidate):
            inventory[str(candidate["artifact_id"])] = dict(candidate)
    return inventory


def _ref_verified(value: Any, inventory: dict[str, JsonObject]) -> bool:
    if not _has_complete_artifact_ref(value):
        return False
    manifest = inventory.get(str(value["artifact_id"]))
    if manifest is None:
        return False
    return (
        manifest.get("content_hash") == value.get("content_hash")
        and manifest.get("artifact_type") == value.get("artifact_type")
        and manifest.get("artifact_schema_id") == value.get("artifact_schema_id")
    )


def _has_complete_artifact_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = (
        "artifact_id",
        "artifact_type",
        "content_hash",
        "artifact_schema_id",
        "source",
    )
    if not all(value.get(field_name) for field_name in required):
        return False
    return (
        isinstance(value.get("source"), dict)
        and isinstance(value["content_hash"], str)
        and value["content_hash"].startswith("sha256:")
    )


def _object_or_empty(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _non_negative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and value >= 0
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _resolve_paper_difficulty(
    domain: str,
    difficulty: str,
    paper_difficulty: str | None,
) -> str:
    resolved = difficulty if paper_difficulty is None else paper_difficulty
    if not isinstance(resolved, str) or not resolved:
        raise ValueError("paper_difficulty must be a non-empty string")
    if resolved not in PAPER_DIFFICULTY_VALUES:
        raise ValueError(
            "paper_difficulty must be easy, medium, hard, simple, "
            "medium_lemma_dag, or hard_frontier"
        )
    if domain == "factorization" and resolved not in PAPER_DIFFICULTIES:
        raise ValueError("factorization paper_difficulty must be easy, medium, or hard")
    return resolved


def _validate_optional_topic_family(domain: str, topic_family: str | None) -> None:
    if topic_family is None:
        return
    if not isinstance(topic_family, str) or not topic_family:
        raise ValueError("topic_family must be a non-empty string")
    if domain == "lean_proof" and topic_family not in LEAN_TOPIC_FAMILIES:
        raise ValueError("topic_family must be pure_logic, function_set, or induction")


def _validate_optional_non_empty(field_name: str, value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _validate_model_policy(value: str) -> None:
    if value not in PAPER_MODEL_POLICIES:
        raise ValueError("model_policy must be fixed_entry")


def _require_complete_model_endpoint_identity(
    condition: PaperExperimentCondition,
) -> None:
    field_names = (
        "model_cohort_id",
        "model_cohort_digest",
        "cohort_member_id",
        "provider_config_id",
        "model_entry_id",
        "provider_family",
        "provider_model_id",
        "reasoning_profile_id",
        "source_provider_config_digest",
        "model_endpoint_identity_digest",
    )
    missing = [
        field_name
        for field_name in field_names
        if getattr(condition, field_name) is None
    ]
    if missing:
        raise ValueError(
            "formal Experiment 5 requires complete fixed-entry identity: "
            + ", ".join(missing)
        )


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_integer(field_name: str, value: int, *, min_value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < min_value:
        raise ValueError(f"{field_name} must be an integer >= {min_value}")


def _require_digest(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _require_identity_digest(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a complete sha256 digest")


def _is_identity_digest(value: Any) -> bool:
    try:
        _require_identity_digest("identity_digest", value)
    except ValueError:
        return False
    return True


def _freeze_json(value: Any) -> Any:
    """递归复制并冻结 JSON-like 输入，避免 nested mutation。"""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if type(value) is float and not math.isfinite(value):
        raise ValueError("floating-point values must be finite")
    if value is None or type(value) in {bool, int, float, str}:
        return value
    raise ValueError("value must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
