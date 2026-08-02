"""正式论文实验的独立、可恢复 evidence 文件存储。"""

from __future__ import annotations

from collections import deque
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.trace_backed import TraceSourceBinding
from tokenshare.experiments.paper_direct_results import PaperDirectRootResult
from tokenshare.experiments.paper_formal_checkpoint import (
    V3_GENERATION_SCHEMA,
    V3GenerationDescriptor,
    compact_v3_delta_chain_to_snapshot,
    validate_v3_delta_chain,
    validate_v3_generation_manifest,
)
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    CanonicalDirectRootEvidence,
    ExternalBankObjectLocator,
    LedgerEventIdentitySnapshot,
    PaperEvidenceEligibilityFacts,
    VersionedPaperEvidenceEligibilityReport,
    evaluate_versioned_paper_evidence as _evaluate_versioned_paper_evidence,
)


_CANONICAL_LINEAGE_INPUT_FACTORY_TOKEN = object()


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IDENTITY_NAMES = (
    "suite",
    "dispatch",
    "budget",
    "catalog",
    "identity",
    "request_limits",
    "hard_limits",
)
_RUN_FILES = (
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)
_SUCCESS_STATUSES = {"accepted", "completed", "success", "succeeded"}
_TERMINAL_CHECKPOINT_STATUSES = _SUCCESS_STATUSES | {
    "blocked",
    "budget_exhausted",
    "failed",
    "ineligible",
    "not_started",
    "partial",
    "timeout",
    "worker_died",
}
_SUITE_RUNTIME_FIELDS = frozenset(
    {
        "status",
        "formal",
        "pilot_only",
        "execution_scope",
        "capturing",
        "regression_only",
        "paper_eligible",
        "ineligibility_reasons",
        "baseline_policy",
        "suite_identity",
    }
)
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_LOCK_FILE_NAME = ".formal-evidence.lock"
_PENDING_FILE_NAME = "PENDING.json"
_PENDING_KEYS = {
    "schema_version",
    "target_generation_id",
    "target_generation_manifest_digest",
    "expected_prior_generation_id",
    "expected_prior_generation_manifest_digest",
    "expected_prior_current_digest",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "task_id",
}
_PENDING_V2_KEYS = {
    "schema_version",
    "publication_kind",
    "target_generation_id",
    "target_generation_manifest_digest",
    "expected_prior_generation_id",
    "expected_prior_generation_manifest_digest",
    "expected_prior_current_digest",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "task_id",
    "selection_ordinal",
    "anchor_task_id",
    "condition_events_digest",
    "compacted_prior_head_generation_id",
    "compacted_prior_head_generation_manifest_digest",
    "compacted_chain_digest",
}


class SharedEvidenceError(ValueError):
    """带类型的 shared evidence 失败，避免消费端解析异常文案。"""

    def __init__(self, message: str, *, evidence_integrity: str) -> None:
        super().__init__(message)
        if evidence_integrity not in {"missing", "invalid", "corrupt"}:
            raise ValueError("invalid shared evidence integrity classification")
        self.evidence_integrity = evidence_integrity
_PLAN_ONLY_ROOT_FILES = frozenset(
    {
        "lean_3x3_matrix.json",
        "model_endpoint_cohort_plan.json",
        "model_policy_plan.json",
        "paper_dispatch_plans.json",
        "run_budget.json",
        "suite_manifest.json",
    }
)
_CURRENT_KEYS = {
    "schema_version",
    "generation_id",
    "generation_manifest_digest",
}
_GENERATION_MANIFEST_V1_KEYS = {
    "schema_version",
    "generation_id",
    "files",
}
_GENERATION_MANIFEST_V2_KEYS = {
    "schema_version",
    "generation_id",
    "parent_generation_id",
    "parent_generation_manifest_digest",
    "files",
}
_RUN_MANIFEST_KEYS = {
    "schema_version",
    "generation_id",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "task_ids",
    "completed_task_ids",
    "status",
}
_CONDITION_MANIFEST_KEYS = {
    "schema_version",
    "experiment_id",
    "condition_id",
    "repeat_id",
    "condition_identity",
    "expected_root_count",
    "observed_root_count",
    "terminal_root_count",
    "status",
    "terminal",
    "current_ref",
    "generation_manifest_ref",
    "run_manifest_ref",
    "paper_eligible",
    "ineligibility_reasons",
}
_CONDITION_MANIFEST_V2_KEYS = _CONDITION_MANIFEST_KEYS | {
    "head_generation_kind",
    "chain_generation_count",
    "root_delta_count",
    "condition_event_delta_count",
    "logical_task_count",
    "reachable_size_bytes",
    "commit_chain_digest",
    "logical_records_digest",
}
_LEDGER_EVENT_V1_KEYS = {
    "schema_version",
    "event_seq",
    "event_id",
    "event_type",
    "occurred_at",
    "task_id",
    "object_type",
    "object_id",
    "actor",
    "correlation_id",
    "causation_event_id",
    "idempotency_key",
    "payload",
    "prev_event_hash",
    "event_hash",
}
_LEDGER_EVENT_V2_KEYS = _LEDGER_EVENT_V1_KEYS | {
    "batch_id",
    "batch_index",
    "batch_size",
}


def evaluate_versioned_paper_evidence(
    facts: PaperEvidenceEligibilityFacts | Mapping[str, Any],
) -> VersionedPaperEvidenceEligibilityReport:
    """公开的 formal evidence 只读 eligibility 边界。"""

    return _evaluate_versioned_paper_evidence(facts)


@dataclass(frozen=True, init=False)
class CanonicalLineageInput:
    """调用方显式绑定的 Task3/Task19 typed lineage；禁止扫描 raw mapping。"""

    direct_result: PaperDirectRootResult
    canonical_evidence: CanonicalDirectRootEvidence
    input_identity_digest: str
    current_trace_wrappers: tuple[CurrentTraceWrapper, ...] = ()
    trace_source_bindings: tuple[TraceSourceBinding, ...] = ()
    eligibility_facts: PaperEvidenceEligibilityFacts | None = None
    _producer_validated: bool = False

    @classmethod
    def _from_validated(
        cls,
        *,
        _factory_token: object | None = None,
        **values: Any,
    ) -> "CanonicalLineageInput":
        expected = set(cls.__dataclass_fields__) - {"_producer_validated"}
        if set(values) != expected:
            raise ValueError("canonical lineage factory field mismatch")
        instance = object.__new__(cls)
        for field_name, value in values.items():
            object.__setattr__(instance, field_name, value)
        object.__setattr__(
            instance,
            "_producer_validated",
            _factory_token is _CANONICAL_LINEAGE_INPUT_FACTORY_TOKEN,
        )
        return instance

    @property
    def producer_validated(self) -> bool:
        return self._producer_validated


def build_canonical_lineage_inputs(
    canonical_inputs: Mapping[str, object],
    *,
    canonical_runtime_evidence: Sequence[CanonicalDirectRootEvidence],
    requested_root_ids: Sequence[str] | None = None,
    current_trace_wrappers_by_root: Mapping[str, Sequence[CurrentTraceWrapper]] | None = None,
    trace_source_bindings_by_root: Mapping[str, Sequence[TraceSourceBinding]] | None = None,
    eligibility_facts_by_root: Mapping[str, PaperEvidenceEligibilityFacts] | None = None,
) -> tuple[CanonicalLineageInput, ...]:
    """从 Task3 protected evidence 与 publisher 的真实 typed rows 铸造 lineage input。"""

    if not isinstance(canonical_inputs, Mapping):
        raise TypeError("canonical inputs must be a mapping")
    evidence_values = tuple(canonical_runtime_evidence)
    if any(
        not isinstance(value, CanonicalDirectRootEvidence)
        or not value.producer_validated
        for value in evidence_values
    ):
        raise ValueError("lineage provenance requires Task3 canonical factory evidence")
    evidence_by_root = {
        value.preregistered_root_run_id: value for value in evidence_values
    }
    if len(evidence_by_root) != len(evidence_values):
        raise ValueError("duplicate canonical lineage evidence root")
    requested = tuple(
        evidence_by_root if requested_root_ids is None else requested_root_ids
    )
    if len(set(requested)) != len(requested):
        raise ValueError("duplicate requested lineage root")
    direct_by_root: dict[str, list[PaperDirectRootResult]] = {}
    for direct in _typed_direct_results_in_inputs(canonical_inputs):
        direct_by_root.setdefault(direct.preregistered_root_run_id, []).append(direct)
    input_digest = lineage_input_identity_digest(canonical_inputs)
    wrappers_by_root = current_trace_wrappers_by_root or {}
    bindings_by_root = trace_source_bindings_by_root or {}
    facts_by_root = eligibility_facts_by_root or {}
    requested_set = set(requested)
    for mapping in (wrappers_by_root, bindings_by_root, facts_by_root):
        if not set(mapping) <= requested_set:
            raise ValueError("Task19 typed input crosses requested lineage root")
    results: list[CanonicalLineageInput] = []
    for root_id in requested:
        evidence = evidence_by_root.get(root_id)
        if evidence is None:
            raise ValueError("requested root lacks canonical factory provenance")
        matches = direct_by_root.get(root_id, [])
        if len(matches) != 1:
            raise ValueError("executed direct result is not uniquely reachable from canonical inputs")
        direct = matches[0]
        if not direct._factory_validated or direct.execution_binding is None:
            raise ValueError("executed lineage requires canonical factory direct result")
        _validate_direct_result_provenance(direct, evidence)
        wrappers = tuple(wrappers_by_root.get(root_id, ()))
        bindings = tuple(bindings_by_root.get(root_id, ()))
        facts = facts_by_root.get(root_id)
        if any(not isinstance(value, CurrentTraceWrapper) for value in wrappers):
            raise TypeError("current trace wrappers must be typed")
        if any(not isinstance(value, TraceSourceBinding) for value in bindings):
            raise TypeError("trace source bindings must be typed")
        if facts is not None and not isinstance(facts, PaperEvidenceEligibilityFacts):
            raise TypeError("eligibility facts must be typed")
        if wrappers or bindings or facts is not None:
            if facts is None:
                raise ValueError("Task19 lineage requires typed eligibility facts")
            _validate_task19_typed_inputs(direct, wrappers, bindings, facts)
        results.append(
            CanonicalLineageInput._from_validated(
                _factory_token=_CANONICAL_LINEAGE_INPUT_FACTORY_TOKEN,
                direct_result=direct,
                canonical_evidence=evidence,
                input_identity_digest=input_digest,
                current_trace_wrappers=wrappers,
                trace_source_bindings=bindings,
                eligibility_facts=facts,
            )
        )
    return tuple(results)


def _typed_direct_results_in_inputs(value: Any):
    if isinstance(value, PaperDirectRootResult):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _typed_direct_results_in_inputs(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _typed_direct_results_in_inputs(item)
    elif is_dataclass(value):
        for field_name in value.__dataclass_fields__:
            if not field_name.startswith("_"):
                yield from _typed_direct_results_in_inputs(getattr(value, field_name))


def _validate_direct_result_provenance(
    direct: PaperDirectRootResult,
    evidence: CanonicalDirectRootEvidence,
) -> None:
    shared_fields = (
        "preregistered_root_run_id",
        "evidence_class",
        "execution_binding",
        "final_result_ref",
        "terminal_root_event_ref",
        "canonical_acceptance_ref",
        "merge_ref",
        "independently_verified_correct",
        "paper_evidence_complete",
        "identity_consistent",
        "infrastructure_valid",
        "attempt_refs",
        "event_refs",
        "parser_refs",
        "verifier_checker_refs",
        "artifact_refs",
        "current_provider_object_refs",
        "source_bank_object_locators",
        "actual_resource_book_ref",
        "trace_resource_book_ref",
        "ineligibility_reasons",
    )
    if any(getattr(direct, name) != getattr(evidence, name) for name in shared_fields):
        raise ValueError("direct result does not match Task3 canonical provenance")


def _validate_task19_typed_inputs(
    direct: PaperDirectRootResult,
    wrappers: Sequence[CurrentTraceWrapper],
    bindings: Sequence[TraceSourceBinding],
    facts: PaperEvidenceEligibilityFacts,
) -> None:
    if not _typed_mapping_sequence_matches(
        tuple(_current_trace_wrapper_body(value) for value in wrappers),
        facts.current_lifecycle_refs,
    ):
        raise ValueError("lineage wrappers do not match eligibility facts")
    if not _typed_mapping_sequence_matches(
        tuple(value.to_dict() for value in bindings),
        facts.trace_source_bindings,
    ):
        raise ValueError("lineage bindings do not match eligibility facts")
    report = evaluate_versioned_paper_evidence(facts)
    if report.evidence_class != direct.evidence_class:
        raise ValueError("lineage eligibility evidence class mismatch")


def _execution_classification(
    suite_body: Any,
    *,
    capturing: bool,
) -> dict[str, Any]:
    """从冻结 suite identity 派生 evidence flags；缺省保持正式行为。"""

    declared = (
        suite_body.get("execution_classification")
        if isinstance(suite_body, dict)
        else None
    )
    if declared is None:
        return {
            "formal": True,
            "pilot_only": False,
            "regression_only": bool(capturing),
            "paper_eligible": True,
            "execution_scope": "formal_matrix",
            "ineligibility_reasons": [],
        }
    if not isinstance(declared, dict):
        raise ValueError("execution_classification must be an object")
    required = {
        "formal",
        "pilot_only",
        "regression_only",
        "paper_eligible",
        "execution_scope",
        "ineligibility_reasons",
    }
    optional = {"baseline_policy"}
    if not required <= set(declared) or set(declared) - required > optional:
        raise ValueError("execution_classification fields are invalid")
    for field_name in ("formal", "pilot_only", "regression_only", "paper_eligible"):
        if not isinstance(declared[field_name], bool):
            raise ValueError(f"execution_classification {field_name} must be a bool")
    reasons = declared["ineligibility_reasons"]
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason for reason in reasons
    ):
        raise ValueError("execution_classification reasons are invalid")
    if (
        declared["formal"] is not False
        or declared["pilot_only"] is not True
        or declared["regression_only"] is not True
        or declared["paper_eligible"] is not False
        or declared["execution_scope"] != "smoke_suite"
        or not {"smoke_suite", "pilot_only"}.issubset(reasons)
    ):
        raise ValueError("unsupported non-formal execution classification")
    result = {
        **declared,
        "ineligibility_reasons": list(reasons),
    }
    baseline_policy = declared.get("baseline_policy")
    if baseline_policy is not None and baseline_policy not in {
        "required_by_formal_plan",
        "omitted_for_smoke_regression",
    }:
        raise ValueError("execution_classification baseline_policy is invalid")
    return result


@dataclass(frozen=True, kw_only=True)
class LineageSourceRecord:
    """一个 metric member 可引用的已验证、typed persisted evidence 集合。"""

    member_id: str
    evidence_class: str
    direct_result_refs: tuple[Mapping[str, Any], ...]
    current_task_attempt_event_refs: tuple[LedgerEventIdentitySnapshot, ...]
    parser_verifier_checker_canonical_refs: tuple[
        ArtifactIdentitySnapshot | LedgerEventIdentitySnapshot, ...
    ]
    ledger_refs: tuple[LedgerEventIdentitySnapshot, ...]
    current_provider_object_refs: tuple[ArtifactIdentitySnapshot, ...]
    source_bank_object_locators: tuple[ExternalBankObjectLocator, ...]
    current_trace_wrappers: tuple[CurrentTraceWrapper, ...]
    trace_source_bindings: tuple[TraceSourceBinding, ...]
    record_digest: str
    schema_version: str = "tokenshare.lineage_source_record.v1"

    @classmethod
    def create(
        cls,
        *,
        member_id: str,
        evidence_class: str,
        direct_result_refs: Sequence[Mapping[str, Any]] = (),
        current_task_attempt_event_refs: Sequence[LedgerEventIdentitySnapshot] = (),
        parser_verifier_checker_canonical_refs: Sequence[
            ArtifactIdentitySnapshot | LedgerEventIdentitySnapshot
        ] = (),
        ledger_refs: Sequence[LedgerEventIdentitySnapshot] = (),
        current_provider_object_refs: Sequence[ArtifactIdentitySnapshot] = (),
        source_bank_object_locators: Sequence[ExternalBankObjectLocator] = (),
        current_trace_wrappers: Sequence[CurrentTraceWrapper] = (),
        trace_source_bindings: Sequence[TraceSourceBinding] = (),
    ) -> "LineageSourceRecord":
        values = {
            "member_id": member_id,
            "evidence_class": evidence_class,
            "direct_result_refs": tuple(direct_result_refs),
            "current_task_attempt_event_refs": tuple(current_task_attempt_event_refs),
            "parser_verifier_checker_canonical_refs": tuple(
                parser_verifier_checker_canonical_refs
            ),
            "ledger_refs": tuple(ledger_refs),
            "current_provider_object_refs": tuple(current_provider_object_refs),
            "source_bank_object_locators": tuple(source_bank_object_locators),
            "current_trace_wrappers": tuple(current_trace_wrappers),
            "trace_source_bindings": tuple(trace_source_bindings),
        }
        return cls(
            **values,
            record_digest=_digest_json(_lineage_source_record_body(**values)),
        )

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.lineage_source_record.v1":
            raise ValueError("unsupported lineage source record schema")
        if not isinstance(self.member_id, str) or not self.member_id:
            raise ValueError("lineage member id must be a non-empty string")
        if self.evidence_class not in {
            "online_real_provider",
            "real_model_trace_protocol_run",
            "regression_only",
        }:
            raise ValueError("unsupported lineage evidence class")
        typed_fields = (
            ("current_task_attempt_event_refs", LedgerEventIdentitySnapshot),
            ("ledger_refs", LedgerEventIdentitySnapshot),
            ("current_provider_object_refs", ArtifactIdentitySnapshot),
            ("source_bank_object_locators", ExternalBankObjectLocator),
            ("current_trace_wrappers", CurrentTraceWrapper),
            ("trace_source_bindings", TraceSourceBinding),
        )
        for field_name, expected_type in typed_fields:
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, expected_type) for value in values):
                raise TypeError(f"{field_name} must contain typed persisted facts")
            object.__setattr__(self, field_name, values)
        mixed = tuple(self.parser_verifier_checker_canonical_refs)
        if any(
            not isinstance(value, (ArtifactIdentitySnapshot, LedgerEventIdentitySnapshot))
            for value in mixed
        ):
            raise TypeError(
                "parser_verifier_checker_canonical_refs must contain typed snapshots"
            )
        object.__setattr__(self, "parser_verifier_checker_canonical_refs", mixed)
        direct = tuple(self.direct_result_refs)
        if any(not isinstance(value, Mapping) or not value for value in direct):
            raise TypeError("direct_result_refs must contain persisted mappings")
        object.__setattr__(self, "direct_result_refs", direct)
        _require_digest({"record_digest": self.record_digest}, "record_digest", "lineage")
        expected = _digest_json(
            _lineage_source_record_body(
                member_id=self.member_id,
                evidence_class=self.evidence_class,
                direct_result_refs=self.direct_result_refs,
                current_task_attempt_event_refs=self.current_task_attempt_event_refs,
                parser_verifier_checker_canonical_refs=(
                    self.parser_verifier_checker_canonical_refs
                ),
                ledger_refs=self.ledger_refs,
                current_provider_object_refs=self.current_provider_object_refs,
                source_bank_object_locators=self.source_bank_object_locators,
                current_trace_wrappers=self.current_trace_wrappers,
                trace_source_bindings=self.trace_source_bindings,
            )
        )
        if self.record_digest != expected:
            raise ValueError("lineage source record digest mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            **_lineage_source_record_body(
                member_id=self.member_id,
                evidence_class=self.evidence_class,
                direct_result_refs=self.direct_result_refs,
                current_task_attempt_event_refs=self.current_task_attempt_event_refs,
                parser_verifier_checker_canonical_refs=(
                    self.parser_verifier_checker_canonical_refs
                ),
                ledger_refs=self.ledger_refs,
                current_provider_object_refs=self.current_provider_object_refs,
                source_bank_object_locators=self.source_bank_object_locators,
                current_trace_wrappers=self.current_trace_wrappers,
                trace_source_bindings=self.trace_source_bindings,
            ),
            "record_digest": self.record_digest,
        }


@dataclass(frozen=True, kw_only=True)
class LineageSourceIndex:
    """由 persisted facts 导出的 canonical member-to-source sidecar。"""

    index_id: str
    input_identity_digest: str
    records: tuple[LineageSourceRecord, ...]
    index_digest: str
    schema_version: str = "tokenshare.lineage_source_index.v1"

    @classmethod
    def create(
        cls,
        *,
        records: Sequence[LineageSourceRecord],
        input_identity_digest: str,
    ) -> "LineageSourceIndex":
        canonical = tuple(sorted(records, key=lambda value: value.member_id))
        member_ids = tuple(value.member_id for value in canonical)
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("lineage source index contains duplicate member ids")
        identity = _digest_json(
            {
                "schema_version": "tokenshare.lineage_source_index_identity.v1",
                "input_identity_digest": input_identity_digest,
                "record_digests": [value.record_digest for value in canonical],
            }
        )
        body = {
            "schema_version": "tokenshare.lineage_source_index.v1",
            "index_id": identity,
            "input_identity_digest": input_identity_digest,
            "records": [value.to_dict() for value in canonical],
        }
        return cls(
            index_id=identity,
            input_identity_digest=input_identity_digest,
            records=canonical,
            index_digest=_digest_json(body),
        )

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.lineage_source_index.v1":
            raise ValueError("unsupported lineage source index schema")
        for field_name in ("index_id", "input_identity_digest", "index_digest"):
            _require_digest({field_name: getattr(self, field_name)}, field_name, "lineage")
        canonical = tuple(sorted(self.records, key=lambda value: value.member_id))
        if canonical != self.records or any(
            not isinstance(value, LineageSourceRecord) for value in canonical
        ):
            raise ValueError("lineage source index records are not canonical typed facts")
        expected_id = _digest_json(
            {
                "schema_version": "tokenshare.lineage_source_index_identity.v1",
                "input_identity_digest": self.input_identity_digest,
                "record_digests": [value.record_digest for value in canonical],
            }
        )
        expected_digest = _digest_json(
            {
                "schema_version": self.schema_version,
                "index_id": expected_id,
                "input_identity_digest": self.input_identity_digest,
                "records": [value.to_dict() for value in canonical],
            }
        )
        if expected_id != self.index_id or expected_digest != self.index_digest:
            raise ValueError("lineage source index identity mismatch")

    def get(self, member_id: str) -> LineageSourceRecord | None:
        matches = tuple(value for value in self.records if value.member_id == member_id)
        return matches[0] if len(matches) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "index_id": self.index_id,
            "input_identity_digest": self.input_identity_digest,
            "record_count": len(self.records),
            "record_digests": [value.record_digest for value in self.records],
            "index_digest": self.index_digest,
        }


@dataclass(frozen=True)
class LoadedFormalEvidence:
    """通过完整性校验后可用于 resume/replay 的最小索引。"""

    completed_task_ids: tuple[str, ...]
    completed_task_ids_by_experiment: dict[str, tuple[str, ...]]
    completed_task_keys: tuple[tuple[str, str, str, str], ...]


class FormalEvidenceStore:
    """只负责正式实验 evidence 的持久化与确定性校验。"""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root).resolve(strict=False)
        self._lock = _lock_for_root(self.output_root)
        self._conditions = self._load_condition_index()
        self._expected_task_counts = self._load_expected_task_counts()
        self._selection_ordinals = self._load_selection_ordinals()
        self._v3_commit_cache: dict[
            tuple[str, str, str, str], tuple[str, dict[str, Any]]
        ] = {}
        self._v3_root_chain_cache: dict[
            str,
            tuple[
                tuple[str, str],
                dict[str, tuple[str, dict[str, Any]]],
            ],
        ] = {}

    @classmethod
    def initialize(
        cls,
        *,
        output_root: str | Path,
        suite: Any,
        dispatch: Any,
        budget: Any,
        catalog: Any,
        identity: Any,
        request_limits: Any,
        hard_limits: Any,
        capturing: bool,
    ) -> "FormalEvidenceStore":
        """冻结 suite identity，并原子写入正式执行所需的根 manifests。"""

        root = Path(output_root).resolve(strict=False)
        if not isinstance(capturing, bool):
            raise ValueError("capturing must be a boolean")
        bodies = {
            "suite": _json_value(suite),
            "dispatch": _json_value(dispatch),
            "budget": _json_value(budget),
            "catalog": _json_value(catalog),
            "identity": _json_value(identity),
            "request_limits": _json_value(request_limits),
            "hard_limits": _json_value(hard_limits),
        }
        if root.exists() and any(root.iterdir()):
            _validate_promotable_plan_only_root(root, bodies=bodies)
        root.mkdir(parents=True, exist_ok=True)
        if not isinstance(bodies["suite"], dict):
            raise ValueError("suite body must be a JSON object")
        conditions_by_experiment = _conditions_by_experiment(bodies["dispatch"])
        experiment_ids = tuple(conditions_by_experiment)
        declared_ids = bodies["suite"].get("experiment_ids")
        if declared_ids is not None and tuple(declared_ids) != experiment_ids:
            raise ValueError("suite and dispatch experiment identity mismatch")

        identity_components = {
            name: {"body": body, "digest": _digest_json(body)}
            for name, body in bodies.items()
        }
        classification = _execution_classification(
            bodies["suite"],
            capturing=capturing,
        )
        suite_manifest = dict(bodies["suite"])
        suite_manifest.update(
            {
                "status": "running",
                "formal": classification["formal"],
                "pilot_only": classification["pilot_only"],
                "execution_scope": classification["execution_scope"],
                "capturing": bool(capturing),
                "regression_only": classification["regression_only"],
                "paper_eligible": False,
                "ineligibility_reasons": classification[
                    "ineligibility_reasons"
                ],
                "baseline_policy": classification.get(
                    "baseline_policy",
                    "required_by_formal_plan",
                ),
                "suite_identity": identity_components,
            }
        )
        _atomic_write_json(root / "suite_manifest.json", suite_manifest)
        _atomic_write_json(root / "run_budget.json", bodies["budget"])
        _atomic_write_json(root / "input_catalog_manifest.json", bodies["catalog"])
        _atomic_write_json(root / "paper_dispatch_plans.json", bodies["dispatch"])
        all_conditions = [
            condition
            for conditions in conditions_by_experiment.values()
            for condition in conditions
        ]
        _atomic_write_jsonl(root / "conditions.jsonl", all_conditions)
        dispatch_plans = {
            plan["experiment_id"]: plan for plan in _dispatch_plans(bodies["dispatch"])
        }
        for experiment_id, conditions in conditions_by_experiment.items():
            experiment_root = root / "experiments" / experiment_id
            _atomic_write_json(
                experiment_root / "experiment_manifest.json",
                {
                    "schema_version": "tokenshare.paper_experiment_evidence.v1",
                    "suite_id": suite_manifest.get("suite_id"),
                    "experiment_id": experiment_id,
                    "condition_ids": [item["condition_id"] for item in conditions],
                    "status": "running",
                    "formal": classification["formal"],
                    "pilot_only": classification["pilot_only"],
                    "execution_scope": classification["execution_scope"],
                    "capturing": bool(capturing),
                    "regression_only": classification["regression_only"],
                    "paper_eligible": False,
                    "ineligibility_reasons": classification[
                        "ineligibility_reasons"
                    ],
                    "baseline_policy": classification.get(
                        "baseline_policy",
                        "required_by_formal_plan",
                    ),
                    "dispatch_plan_digest": _digest_json(
                        dispatch_plans[experiment_id]
                    ),
                },
            )
        store = cls(root)
        store._refresh_evidence_manifest()
        return store

    def checkpoint_root(
        self,
        *,
        experiment_id: str,
        condition: Any,
        repeat_id: int | str,
        selection_ordinal: int | None = None,
        task: Any,
        attempts: Sequence[Any],
        faults: Sequence[Any],
        events: Sequence[Any],
        artifact_refs: Sequence[Any],
    ) -> dict[str, Any]:
        """合并一个 root task checkpoint；已成功 task 永不被后写覆盖。"""

        experiment_id = _safe_id(experiment_id, "experiment_id")
        repeat_name = _safe_id(str(repeat_id), "repeat_id")
        condition_body = _require_object(condition, "condition")
        condition_id = _safe_id(condition_body.get("condition_id"), "condition_id")
        expected_condition = self._conditions.get((experiment_id, condition_id))
        if expected_condition is None:
            raise ValueError("condition does not belong to experiment")
        if _canonical_bytes(condition_body) != _canonical_bytes(expected_condition):
            raise ValueError("condition experiment identity mismatch")
        _validate_context(
            condition_body,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            label="condition",
        )
        task_body = _require_object(task, "task")
        task_id = _safe_id(task_body.get("task_id"), "task_id")
        frozen_ordinal = self._selection_ordinals.get(
            (experiment_id, condition_id, task_id)
        )
        if frozen_ordinal is not None:
            if selection_ordinal is not None and selection_ordinal != frozen_ordinal:
                raise ValueError("checkpoint selection ordinal conflicts with frozen selection")
            selection_ordinal = frozen_ordinal
        elif selection_ordinal is not None and (
            isinstance(selection_ordinal, bool)
            or not isinstance(selection_ordinal, int)
            or selection_ordinal < 0
        ):
            raise ValueError("checkpoint selection ordinal must be non-negative")
        _validate_context(
            task_body,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=task_id,
            label="task",
        )
        attempt_bodies = [_require_object(item, "attempt") for item in attempts]
        event_bodies = [_require_object(item, "event") for item in events]
        fault_bodies = [_require_object(item, "fault") for item in faults]
        artifact_bodies = [_require_object(item, "artifact ref") for item in artifact_refs]
        protocol_events = [
            item for item in event_bodies if _is_protocol_ledger_event(item)
        ]
        if protocol_events:
            protocol_event_ledger = _protocol_event_ledger_metadata(
                case_task_id=task_id,
                events=protocol_events,
            )
            existing_protocol_event_ledger = task_body.get("protocol_event_ledger")
            if (
                existing_protocol_event_ledger is not None
                and _canonical_bytes(existing_protocol_event_ledger)
                != _canonical_bytes(protocol_event_ledger)
            ):
                raise ValueError("protocol event ledger metadata conflicts with events")
            task_body["protocol_event_ledger"] = protocol_event_ledger
        if not attempt_bodies:
            raise ValueError("checkpoint requires attempt evidence")
        if not event_bodies:
            raise ValueError("checkpoint requires event evidence")
        for label, records in (
            ("attempt", attempt_bodies),
            ("event", event_bodies),
            ("fault", fault_bodies),
        ):
            for record in records:
                if label == "event" and _is_protocol_ledger_event(record):
                    continue
                _validate_context(
                    record,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    label=label,
                )

        run_root = (
            self.output_root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / repeat_name
        )
        if _is_success(task_body) and not artifact_bodies:
            raise ValueError("completed task requires artifact evidence")
        with self._lock:
            with _exclusive_output_root_lock(self.output_root):
                return self._publish_checkpoint_generation(
                    run_root=run_root,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    selection_ordinal=selection_ordinal,
                    task_body=task_body,
                    attempt_bodies=attempt_bodies,
                    fault_bodies=fault_bodies,
                    event_bodies=event_bodies,
                    artifact_bodies=artifact_bodies,
                )

    def checkpoint_condition_tail_events(
        self,
        *,
        experiment_id: str,
        condition: Any,
        repeat_id: int | str,
        anchor_task_id: str,
        events: Sequence[Any],
    ) -> dict[str, Any]:
        """提交 condition 唯一 closure；空 events 仍写 event-only delta。"""

        experiment_id = _safe_id(experiment_id, "experiment_id")
        repeat_name = _safe_id(str(repeat_id), "repeat_id")
        condition_body = _require_object(condition, "condition")
        condition_id = _safe_id(condition_body.get("condition_id"), "condition_id")
        expected_condition = self._conditions.get((experiment_id, condition_id))
        if expected_condition is None or _canonical_bytes(condition_body) != (
            _canonical_bytes(expected_condition)
        ):
            raise ValueError("condition experiment identity mismatch")
        anchor_task_id = _safe_id(anchor_task_id, "anchor_task_id")
        event_bodies = [_require_object(item, "condition event") for item in events]
        for event in event_bodies:
            event_type = event.get("event_type")
            if not isinstance(event_type, str) or not event_type.startswith(
                "MERGE_GATE_"
            ):
                raise ValueError("condition tail events must be MERGE_GATE events")
        run_root = (
            self.output_root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / repeat_name
        )
        with self._lock:
            with _exclusive_output_root_lock(self.output_root):
                return self._publish_condition_tail_generation(
                    run_root=run_root,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    anchor_task_id=anchor_task_id,
                    event_bodies=event_bodies,
                )

    def compact_condition_snapshot(
        self,
        *,
        experiment_id: str,
        condition: Any,
        repeat_id: int | str,
        temp_parent: str | Path | None = None,
    ) -> dict[str, Any]:
        """把已闭合的完整delta chain原子发布为terminal snapshot。"""

        experiment_id = _safe_id(experiment_id, "experiment_id")
        repeat_name = _safe_id(str(repeat_id), "repeat_id")
        condition_body = _require_object(condition, "condition")
        condition_id = _safe_id(condition_body.get("condition_id"), "condition_id")
        expected_condition = self._conditions.get((experiment_id, condition_id))
        if expected_condition is None or _canonical_bytes(condition_body) != (
            _canonical_bytes(expected_condition)
        ):
            raise ValueError("condition experiment identity mismatch")
        run_root = (
            self.output_root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / repeat_name
        )
        with self._lock:
            with _exclusive_output_root_lock(self.output_root):
                return self._publish_terminal_snapshot(
                    run_root=run_root,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    temp_parent=(
                        Path(temp_parent).resolve(strict=False)
                        if temp_parent is not None
                        else self.output_root.parent
                    ),
                )

    def build_shared_root_reference(
        self,
        *,
        source_experiment_id: str,
        case_id: str,
        source_repeat_id: int,
        expected_condition_identity: Mapping[str, Any],
        expected_source_versions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """校验并冻结一个已持久化 root，供后续实验零调用引用。"""

        source_experiment_id = _safe_id(
            source_experiment_id,
            "source_experiment_id",
        )
        case_id = _safe_id(case_id, "case_id")
        if isinstance(source_repeat_id, bool) or not isinstance(
            source_repeat_id,
            int,
        ):
            raise SharedEvidenceError(
                "source repeat identity is invalid",
                evidence_integrity="invalid",
            )
        if not isinstance(expected_condition_identity, Mapping):
            raise SharedEvidenceError(
                "expected source condition identity must be an object",
                evidence_integrity="invalid",
            )
        if expected_source_versions is not None and not isinstance(
            expected_source_versions,
            Mapping,
        ):
            raise SharedEvidenceError(
                "expected source version identity must be an object",
                evidence_integrity="invalid",
            )

        suite_manifest = _read_json(self.output_root / "suite_manifest.json")
        suite_identity = suite_manifest.get("suite_identity")
        if not isinstance(suite_identity, dict):
            raise SharedEvidenceError(
                "source suite identity evidence is missing",
                evidence_integrity="missing",
            )
        frozen_request_limits = _frozen_identity_body(
            suite_identity,
            "request_limits",
        )
        frozen_catalog = _frozen_identity_body(suite_identity, "catalog")
        runs_root = (
            self.output_root
            / "experiments"
            / source_experiment_id
            / "runs"
        )
        if not runs_root.is_dir():
            raise SharedEvidenceError(
                "shared source evidence is missing",
                evidence_integrity="missing",
            )

        matches: list[dict[str, Any]] = []
        for condition_root in sorted(
            path for path in runs_root.iterdir() if path.is_dir()
        ):
            run_root = condition_root / str(source_repeat_id)
            if not run_root.is_dir():
                continue
            try:
                logical = self.load_logical_run_records(
                    experiment_id=source_experiment_id,
                    condition_id=condition_root.name,
                    repeat_id=source_repeat_id,
                )
            except (ValueError, json.JSONDecodeError) as error:
                raise SharedEvidenceError(
                    "shared source checkpoint integrity validation failed",
                    evidence_integrity="corrupt",
                ) from error
            for task in logical["tasks"]:
                task_case_id = task.get("case_id", task.get("task_id"))
                if task_case_id != case_id:
                    continue
                matches.append(
                    {
                        "run_root": run_root,
                        "task": task,
                        "logical": logical,
                    }
                )
        if not matches:
            raise SharedEvidenceError(
                "shared source case evidence is missing",
                evidence_integrity="missing",
            )
        if len(matches) != 1:
            raise SharedEvidenceError(
                "shared source case identity is ambiguous",
                evidence_integrity="invalid",
            )

        match = matches[0]
        run_root = match["run_root"]
        task = match["task"]
        logical = match["logical"]
        condition_id = run_root.parent.name
        condition = self._conditions.get((source_experiment_id, condition_id))
        if condition is None:
            raise SharedEvidenceError(
                "shared source condition identity is missing",
                evidence_integrity="missing",
            )
        for field_name, expected_value in expected_condition_identity.items():
            if field_name == "request_limits":
                actual_value = frozen_request_limits
            elif field_name == "catalog_digest":
                actual_value = condition.get(
                    field_name,
                    frozen_catalog.get(field_name),
                )
            else:
                actual_value = condition.get(field_name)
            if _canonical_bytes(actual_value) != _canonical_bytes(expected_value):
                raise SharedEvidenceError(
                    f"shared source condition identity mismatch: {field_name}",
                    evidence_integrity="invalid",
                )

        if not _is_terminal_checkpoint(task):
            raise SharedEvidenceError(
                "shared source evidence is not terminal",
                evidence_integrity="invalid",
            )
        source_generation_roots = tuple(logical["source_generation_roots"])
        generation_root = next(
            (
                source_root
                for source_root in source_generation_roots
                if any(
                    item.get("task_id") == task.get("task_id")
                    for item in _read_jsonl(
                        source_root / "per_task_results.jsonl"
                    )
                )
            ),
            None,
        )
        if generation_root is None:
            raise SharedEvidenceError(
                "shared source task generation is missing",
                evidence_integrity="corrupt",
            )
        generation_manifest = _read_json(
            generation_root / "generation_manifest.json"
        )
        run_manifest = _read_json(generation_root / "run_manifest.json")
        attempts = [
            item
            for item in logical["attempts"]
            if item.get("task_id") == task.get("task_id")
        ]
        events = [
            item
            for item in logical["events"]
            if _event_belongs_to_task(item, task)
        ]
        faults = [
            item
            for item in logical["faults"]
            if item.get("task_id") == task.get("task_id")
        ]
        artifacts = [
            item
            for item in logical["artifacts"]
            if item.get("task_id") == task.get("task_id")
        ]
        if not attempts or not events:
            raise SharedEvidenceError(
                "shared source evidence is incomplete",
                evidence_integrity="missing",
            )

        source_usage = _shared_source_usage(task=task, attempts=attempts)
        source_root_status = str(task.get("root_status"))
        comparison_eligible = _is_success(task) and bool(
            source_usage["usage_complete"]
        )
        def record_generation(relative_path: str, record: Mapping[str, Any]) -> Path:
            matches = [
                source_root
                for source_root in source_generation_roots
                if any(
                    _canonical_bytes(item) == _canonical_bytes(record)
                    for item in _read_jsonl(source_root / relative_path)
                )
            ]
            if len(matches) != 1:
                raise SharedEvidenceError(
                    "shared source record generation is ambiguous",
                    evidence_integrity="corrupt",
                )
            return matches[0]

        source_record_refs = {
            "source_task_ref": _generation_record_ref(
                self.output_root,
                generation_root,
                "per_task_results.jsonl",
                task,
            ),
            "source_attempt_refs": [
                _generation_record_ref(
                    self.output_root,
                    record_generation("per_attempt_results.jsonl", item),
                    "per_attempt_results.jsonl",
                    item,
                )
                for item in attempts
            ],
            "source_event_refs": [
                _generation_record_ref(
                    self.output_root,
                    record_generation("events/event_log.jsonl", item),
                    "events/event_log.jsonl",
                    item,
                )
                for item in events
            ],
            "source_fault_refs": [
                _generation_record_ref(
                    self.output_root,
                    record_generation("fault_injections.jsonl", item),
                    "fault_injections.jsonl",
                    item,
                )
                for item in faults
            ],
            "source_artifact_refs": artifacts,
        }
        source_versions = task.get("execution_version_identity")
        _validate_execution_version_identity(source_versions)
        assert isinstance(source_versions, Mapping)
        source_versions = dict(source_versions)
        runtime_generation_identity = task.get("runtime_generation_identity")
        if (
            not isinstance(runtime_generation_identity, Mapping)
            or source_versions.get("runtime_generation_schema_version")
            != runtime_generation_identity.get("schema_version")
            or source_versions.get("runtime_generation_identity_digest")
            != _digest_json(runtime_generation_identity)
        ):
            raise SharedEvidenceError(
                "shared source runtime generation identity mismatch",
                evidence_integrity="invalid",
            )
        if expected_source_versions is not None:
            for field_name, expected_value in expected_source_versions.items():
                if _canonical_bytes(source_versions.get(field_name)) != _canonical_bytes(
                    expected_value
                ):
                    raise SharedEvidenceError(
                        f"shared source version identity mismatch: {field_name}",
                        evidence_integrity="invalid",
                    )
        reference_core: dict[str, Any] = {
            "schema_version": "tokenshare.paper_exp1_shared_reference.v1",
            "source_suite_id": suite_manifest.get("suite_id"),
            "source_run_id": f"{condition_id}/{source_repeat_id}",
            "source_generation_id": generation_root.name,
            "source_generation_manifest_digest": _digest_json(
                generation_manifest
            ),
            "source_experiment_id": source_experiment_id,
            "source_condition_id": condition_id,
            "source_condition_digest": _digest_json(condition),
            "source_condition": condition,
            "source_case_id": case_id,
            "source_task_id": str(task["task_id"]),
            "source_repeat_id": source_repeat_id,
            "source_seed": condition.get("seed"),
            "source_worker_count": condition.get("worker_count"),
            "source_root_status": source_root_status,
            "source_provider_config_id": condition.get("provider_config_id"),
            "source_model_entry_id": condition.get("model_entry_id"),
            "source_provider_family": condition.get("provider_family"),
            "source_provider_model_id": condition.get("provider_model_id"),
            "source_reasoning_profile_id": condition.get("reasoning_profile_id"),
            "source_request_limits": frozen_request_limits,
            "source_catalog_identity": frozen_catalog,
            "source_versions": source_versions,
            "source_run_manifest_hash": _digest_json(run_manifest),
            "source_task_record_hash": _digest_json(task),
            **source_record_refs,
            "source_usage": source_usage,
            "evidence_integrity": "complete",
            "baseline_comparison_eligible": comparison_eligible,
            "baseline_unavailable_reason": (
                None
                if comparison_eligible
                else (
                    "source_exp1_failed_experimental"
                    if not _is_success(task)
                    else "source_exp1_usage_incomplete"
                )
            ),
            "provider_calls_made": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_estimate": 0.0,
        }
        source_hash = _digest_json(reference_core)
        return {
            **reference_core,
            "source_hash": source_hash,
            "source_reference_id": (
                "shared_exp1_" + source_hash.removeprefix("sha256:")[:24]
            ),
        }

    def _publish_checkpoint_generation(
        self,
        *,
        run_root: Path,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        task_id: str,
        selection_ordinal: int | None,
        task_body: dict[str, Any],
        attempt_bodies: list[dict[str, Any]],
        fault_bodies: list[dict[str, Any]],
        event_bodies: list[dict[str, Any]],
        artifact_bodies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """发布只包含当前 root 的 v3 immutable delta。"""

        if (run_root / _PENDING_FILE_NAME).exists():
            raise ValueError(
                "checkpoint publication is pending; repair before a new checkpoint"
            )
        commit_key = (experiment_id, condition_id, str(repeat_id), task_id)
        checkpoint_digest = _digest_json(
            {
                "selection_ordinal": selection_ordinal,
                "task": task_body,
                "attempts": attempt_bodies,
                "faults": fault_bodies,
                "events": event_bodies,
                "artifacts": artifact_bodies,
            }
        )
        current_root = self._current_generation_root(run_root, required=False)
        prior_current = (
            _read_json(run_root / "CURRENT.json")
            if current_root is not None
            else None
        )
        prior_generation_manifest = (
            _read_json(current_root / "generation_manifest.json")
            if current_root is not None
            else None
        )
        if prior_generation_manifest is not None:
            if prior_generation_manifest.get("schema_version") != V3_GENERATION_SCHEMA:
                raise ValueError("historical checkpoint generation is read-only")
            prior_descriptor = validate_v3_generation_manifest(
                current_root,
                prior_generation_manifest,
                expected_experiment_id=experiment_id,
                expected_condition_id=condition_id,
                expected_repeat_id=repeat_id,
            )
            if prior_descriptor.generation_kind != "delta":
                raise ValueError("terminal checkpoint snapshot is immutable")
            assert prior_current is not None
            persisted_roots = self._index_v3_root_chain(
                run_root=run_root,
                head=prior_descriptor,
                head_manifest_digest=str(
                    prior_current["generation_manifest_digest"]
                ),
                expected_root_count=self._expected_task_counts.get(
                    (experiment_id, condition_id),
                    0,
                ),
            )
            persisted = persisted_roots.get(task_id)
            if persisted is not None:
                stored_digest, token = persisted
                if stored_digest != checkpoint_digest:
                    raise ValueError(
                        "checkpoint terminal task evidence drift is immutable"
                    )
                self._v3_commit_cache[commit_key] = (
                    checkpoint_digest,
                    dict(token),
                )
                return dict(token)

        self._validate_artifact_refs(
            artifact_bodies,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=task_id,
            run_root=run_root,
        )
        prior_condition = (
            _read_json(run_root / "condition_manifest.json")
            if (run_root / "condition_manifest.json").is_file()
            else None
        )
        if prior_condition is not None and prior_condition.get("schema_version") != (
            "tokenshare.paper_condition_evidence.v2"
        ):
            raise ValueError("historical condition checkpoint is read-only")
        if selection_ordinal is None:
            selection_ordinal = (
                int(prior_condition.get("logical_task_count", 0))
                if prior_condition is not None
                else 0
            )
        if isinstance(selection_ordinal, bool) or not isinstance(
            selection_ordinal, int
        ) or selection_ordinal < 0:
            raise ValueError("checkpoint selection ordinal must be non-negative")

        generation_id = uuid.uuid4().hex
        generation_root = run_root / ".generations" / generation_id
        run_manifest = {
            "schema_version": "tokenshare.paper_run_evidence.v1",
            "generation_id": generation_id,
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_ids": [task_id],
            "completed_task_ids": [task_id] if _is_success(task_body) else [],
            "status": _derived_run_status(
                [task_body],
                expected_task_count=1,
            ),
        }
        _atomic_write_json(generation_root / "run_manifest.json", run_manifest)
        _atomic_write_jsonl(
            generation_root / "per_task_results.jsonl", [task_body]
        )
        _atomic_write_jsonl(
            generation_root / "per_attempt_results.jsonl", attempt_bodies
        )
        _atomic_write_jsonl(
            generation_root / "fault_injections.jsonl", fault_bodies
        )
        _atomic_write_jsonl(
            generation_root / "events" / "event_log.jsonl", event_bodies
        )
        _atomic_write_jsonl(
            generation_root / "artifacts" / "artifact_index.jsonl",
            artifact_bodies,
        )
        generation_manifest = {
            "schema_version": V3_GENERATION_SCHEMA,
            "generation_id": generation_id,
            "generation_kind": "delta",
            "delta_role": "root_outcome",
            "selection_ordinal": selection_ordinal,
            "anchor_task_id": task_id,
            "parent_generation_id": (
                current_root.name if current_root is not None else None
            ),
            "parent_generation_manifest_digest": (
                prior_current.get("generation_manifest_digest")
                if prior_current is not None
                else None
            ),
            "compacted_from_head_generation_id": None,
            "compacted_from_head_generation_manifest_digest": None,
            "compacted_chain_digest": None,
            "compacted_generation_count": 0,
            "files": [
                _file_evidence(generation_root, generation_root / relative_path)
                for relative_path in _RUN_FILES
            ],
        }
        _atomic_write_json(
            generation_root / "generation_manifest.json", generation_manifest
        )
        validate_v3_generation_manifest(
            generation_root,
            generation_manifest,
            expected_experiment_id=experiment_id,
            expected_condition_id=condition_id,
            expected_repeat_id=repeat_id,
        )
        self._checkpoint_publication_hook(
            stage="target_written",
            run_root=run_root,
        )
        pending = {
            "schema_version": "tokenshare.paper_checkpoint_pending.v2",
            "publication_kind": "root_delta",
            "target_generation_id": generation_id,
            "target_generation_manifest_digest": _digest_json(
                generation_manifest
            ),
            "expected_prior_generation_id": (
                current_root.name if current_root is not None else None
            ),
            "expected_prior_generation_manifest_digest": (
                prior_current.get("generation_manifest_digest")
                if prior_current is not None
                else None
            ),
            "expected_prior_current_digest": (
                _digest_json(prior_current)
                if prior_current is not None
                else None
            ),
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_id": task_id,
            "selection_ordinal": selection_ordinal,
            "anchor_task_id": task_id,
            "condition_events_digest": None,
            "compacted_prior_head_generation_id": None,
            "compacted_prior_head_generation_manifest_digest": None,
            "compacted_chain_digest": None,
        }
        _atomic_write_json(run_root / _PENDING_FILE_NAME, pending)
        self._checkpoint_publication_hook(
            stage="intent_written",
            run_root=run_root,
        )
        self._checkpoint_publication_hook(
            stage="generation_manifest_written",
            run_root=run_root,
        )
        self._validate_pending_prior_current(
            run_root=run_root,
            pending=pending,
        )
        _atomic_write_json(
            run_root / "CURRENT.json",
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation_id,
                "generation_manifest_digest": _digest_json(generation_manifest),
            },
        )
        self._checkpoint_publication_hook(
            stage="current_written",
            run_root=run_root,
        )
        _atomic_write_json(
            run_root / "condition_manifest.json",
            self._condition_manifest_v2_body(
                run_root=run_root,
                generation_root=generation_root,
                task_body=task_body,
                artifact_bodies=artifact_bodies,
                prior_condition=prior_condition,
            ),
        )
        self._checkpoint_publication_hook(
            stage="condition_manifest_written",
            run_root=run_root,
        )
        self._refresh_evidence_manifest(run_root=run_root)
        self._checkpoint_publication_hook(
            stage="inventory_refreshed",
            run_root=run_root,
        )
        (run_root / _PENDING_FILE_NAME).unlink()
        current_body = _read_json(run_root / "CURRENT.json")
        token = {
            "schema_version": "tokenshare.paper_checkpoint_commit.v1",
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": str(repeat_id),
            "task_id": task_id,
            "run_root": run_root.relative_to(self.output_root).as_posix(),
            "generation_id": generation_id,
            "generation_manifest_digest": _digest_json(generation_manifest),
            "current_digest": _digest_json(current_body),
            "selection_ordinal": selection_ordinal,
        }
        self._v3_commit_cache[commit_key] = (checkpoint_digest, dict(token))
        run_cache_key = run_root.resolve(strict=False).as_posix()
        cached_chain = self._v3_root_chain_cache.get(run_cache_key)
        cached_entries = (
            dict(cached_chain[1])
            if cached_chain is not None
            and (
                prior_current is None
                or cached_chain[0]
                == (
                    str(prior_current["generation_id"]),
                    str(prior_current["generation_manifest_digest"]),
                )
            )
            else {}
        )
        cached_entries[task_id] = (checkpoint_digest, dict(token))
        self._v3_root_chain_cache[run_cache_key] = (
            (generation_id, _digest_json(generation_manifest)),
            cached_entries,
        )
        return token

    def _publish_condition_tail_generation(
        self,
        *,
        run_root: Path,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        anchor_task_id: str,
        event_bodies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if (run_root / _PENDING_FILE_NAME).exists():
            raise ValueError(
                "checkpoint publication is pending; repair before condition closure"
            )
        commit_key = (
            experiment_id,
            condition_id,
            str(repeat_id),
            "__condition_tail_events__",
        )
        checkpoint_digest = _digest_json(
            {"anchor_task_id": anchor_task_id, "events": event_bodies}
        )
        cached = self._v3_commit_cache.get(commit_key)
        if cached is not None:
            cached_digest, cached_token = cached
            if cached_digest != checkpoint_digest:
                raise ValueError("condition tail event evidence drift is immutable")
            return dict(cached_token)

        current_root = self._current_generation_root(run_root, required=True)
        assert current_root is not None
        prior_current = _read_json(run_root / "CURRENT.json")
        prior_generation_manifest = _read_json(
            current_root / "generation_manifest.json"
        )
        prior_descriptor = validate_v3_generation_manifest(
            current_root,
            prior_generation_manifest,
            expected_experiment_id=experiment_id,
            expected_condition_id=condition_id,
            expected_repeat_id=repeat_id,
        )
        if prior_descriptor.generation_kind != "delta":
            raise ValueError("terminal checkpoint snapshot is immutable")
        prior_condition = _read_json(run_root / "condition_manifest.json")
        _require_exact_keys(
            prior_condition,
            _CONDITION_MANIFEST_V2_KEYS,
            "condition manifest v2",
        )
        if prior_condition.get("condition_event_delta_count") == 1:
            if prior_descriptor.delta_role != "condition_tail_events":
                raise ValueError("condition closure count conflicts with CURRENT")
            stored_events = _read_jsonl(current_root / "events" / "event_log.jsonl")
            if (
                prior_descriptor.anchor_task_id != anchor_task_id
                or _digest_json(stored_events) != _digest_json(event_bodies)
            ):
                raise ValueError("condition tail event evidence drift is immutable")
            token = self._v3_commit_token(
                run_root=run_root,
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=repeat_id,
                task_id=None,
                selection_ordinal=None,
                generation_id=current_root.name,
                generation_manifest_digest=prior_current[
                    "generation_manifest_digest"
                ],
            )
            self._v3_commit_cache[commit_key] = (checkpoint_digest, dict(token))
            return token
        expected_count = int(prior_condition["expected_root_count"])
        if (
            prior_condition.get("root_delta_count") != expected_count
            or prior_condition.get("terminal_root_count") != expected_count
        ):
            raise ValueError("condition closure requires all frozen roots terminal")
        last_case = next(
            (
                case_id
                for (exp_id, cond_id, case_id), ordinal in self._selection_ordinals.items()
                if exp_id == experiment_id
                and cond_id == condition_id
                and ordinal == expected_count - 1
            ),
            None,
        )
        if last_case is not None and anchor_task_id != last_case:
            raise ValueError("condition closure anchor conflicts with frozen selection")

        generation_id = uuid.uuid4().hex
        generation_root = run_root / ".generations" / generation_id
        _atomic_write_json(
            generation_root / "run_manifest.json",
            {
                "schema_version": "tokenshare.paper_run_evidence.v1",
                "generation_id": generation_id,
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_ids": [],
                "completed_task_ids": [],
                "status": "running",
            },
        )
        for relative_path in (
            "per_task_results.jsonl",
            "per_attempt_results.jsonl",
            "fault_injections.jsonl",
            "artifacts/artifact_index.jsonl",
        ):
            _atomic_write_jsonl(generation_root / relative_path, [])
        _atomic_write_jsonl(
            generation_root / "events" / "event_log.jsonl",
            event_bodies,
        )
        generation_manifest = {
            "schema_version": V3_GENERATION_SCHEMA,
            "generation_id": generation_id,
            "generation_kind": "delta",
            "delta_role": "condition_tail_events",
            "selection_ordinal": None,
            "anchor_task_id": anchor_task_id,
            "parent_generation_id": current_root.name,
            "parent_generation_manifest_digest": prior_current[
                "generation_manifest_digest"
            ],
            "compacted_from_head_generation_id": None,
            "compacted_from_head_generation_manifest_digest": None,
            "compacted_chain_digest": None,
            "compacted_generation_count": 0,
            "files": [
                _file_evidence(generation_root, generation_root / relative_path)
                for relative_path in _RUN_FILES
            ],
        }
        _atomic_write_json(
            generation_root / "generation_manifest.json",
            generation_manifest,
        )
        validate_v3_generation_manifest(
            generation_root,
            generation_manifest,
            expected_experiment_id=experiment_id,
            expected_condition_id=condition_id,
            expected_repeat_id=repeat_id,
        )
        self._checkpoint_publication_hook(stage="target_written", run_root=run_root)
        pending = {
            "schema_version": "tokenshare.paper_checkpoint_pending.v2",
            "publication_kind": "condition_tail_events",
            "target_generation_id": generation_id,
            "target_generation_manifest_digest": _digest_json(generation_manifest),
            "expected_prior_generation_id": current_root.name,
            "expected_prior_generation_manifest_digest": prior_current[
                "generation_manifest_digest"
            ],
            "expected_prior_current_digest": _digest_json(prior_current),
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_id": None,
            "selection_ordinal": None,
            "anchor_task_id": anchor_task_id,
            "condition_events_digest": _digest_json(event_bodies),
            "compacted_prior_head_generation_id": None,
            "compacted_prior_head_generation_manifest_digest": None,
            "compacted_chain_digest": None,
        }
        _atomic_write_json(run_root / _PENDING_FILE_NAME, pending)
        self._checkpoint_publication_hook(stage="intent_written", run_root=run_root)
        self._validate_pending_prior_current(run_root=run_root, pending=pending)
        _atomic_write_json(
            run_root / "CURRENT.json",
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation_id,
                "generation_manifest_digest": _digest_json(generation_manifest),
            },
        )
        self._checkpoint_publication_hook(stage="current_written", run_root=run_root)
        _atomic_write_json(
            run_root / "condition_manifest.json",
            self._condition_manifest_v2_tail_body(
                run_root=run_root,
                generation_root=generation_root,
                prior_condition=prior_condition,
            ),
        )
        self._checkpoint_publication_hook(
            stage="condition_manifest_written",
            run_root=run_root,
        )
        self._refresh_evidence_manifest(run_root=run_root)
        self._checkpoint_publication_hook(
            stage="inventory_refreshed",
            run_root=run_root,
        )
        (run_root / _PENDING_FILE_NAME).unlink()
        token = self._v3_commit_token(
            run_root=run_root,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=None,
            selection_ordinal=None,
            generation_id=generation_id,
            generation_manifest_digest=_digest_json(generation_manifest),
        )
        self._v3_commit_cache[commit_key] = (checkpoint_digest, dict(token))
        return token

    def _publish_terminal_snapshot(
        self,
        *,
        run_root: Path,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        temp_parent: Path,
    ) -> dict[str, Any]:
        if (run_root / _PENDING_FILE_NAME).exists():
            raise ValueError(
                "checkpoint publication is pending; repair before compaction"
            )
        current_root = self._current_generation_root(run_root, required=True)
        assert current_root is not None
        prior_current = _read_json(run_root / "CURRENT.json")
        prior_manifest = _read_json(current_root / "generation_manifest.json")
        head = validate_v3_generation_manifest(
            current_root,
            prior_manifest,
            expected_experiment_id=experiment_id,
            expected_condition_id=condition_id,
            expected_repeat_id=repeat_id,
        )
        if head.generation_kind == "snapshot":
            condition = _read_json(run_root / "condition_manifest.json")
            if condition.get("terminal") is not True:
                raise ValueError("terminal snapshot condition manifest is incomplete")
            return self._v3_commit_token(
                run_root=run_root,
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=repeat_id,
                task_id=None,
                selection_ordinal=None,
                generation_id=head.generation_id,
                generation_manifest_digest=head.manifest_digest,
            )
        expected_count = self._expected_task_counts.get(
            (experiment_id, condition_id)
        )
        if expected_count is None:
            raise ValueError("terminal snapshot frozen denominator is missing")
        chain = validate_v3_delta_chain(
            run_root,
            head,
            expected_root_count=expected_count,
        )
        if head.delta_role != "condition_tail_events":
            raise ValueError("terminal snapshot requires condition closure")
        prior_condition = _read_json(run_root / "condition_manifest.json")
        _require_exact_keys(
            prior_condition,
            _CONDITION_MANIFEST_V2_KEYS,
            "condition manifest v2",
        )
        if (
            prior_condition.get("head_generation_kind") != "delta"
            or prior_condition.get("root_delta_count") != expected_count
            or prior_condition.get("terminal_root_count") != expected_count
            or prior_condition.get("condition_event_delta_count") != 1
            or prior_condition.get("terminal") is not False
        ):
            raise ValueError("terminal snapshot condition is not ready")

        generation_id = uuid.uuid4().hex
        generation_root = run_root / ".generations" / generation_id
        try:
            compacted = compact_v3_delta_chain_to_snapshot(
                run_root,
                head,
                target_root=generation_root,
                generation_id=generation_id,
                expected_root_count=expected_count,
                temp_parent=temp_parent,
            )
        except BaseException:
            if generation_root.exists():
                shutil.rmtree(generation_root)
            raise
        generation_manifest = compacted["manifest"]
        generation_digest = _digest_json(generation_manifest)
        pending = {
            "schema_version": "tokenshare.paper_checkpoint_pending.v2",
            "publication_kind": "terminal_snapshot",
            "target_generation_id": generation_id,
            "target_generation_manifest_digest": generation_digest,
            "expected_prior_generation_id": head.generation_id,
            "expected_prior_generation_manifest_digest": head.manifest_digest,
            "expected_prior_current_digest": _digest_json(prior_current),
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "task_id": None,
            "selection_ordinal": None,
            "anchor_task_id": None,
            "condition_events_digest": None,
            "compacted_prior_head_generation_id": head.generation_id,
            "compacted_prior_head_generation_manifest_digest": head.manifest_digest,
            "compacted_chain_digest": generation_manifest[
                "compacted_chain_digest"
            ],
        }
        _atomic_write_json(run_root / _PENDING_FILE_NAME, pending)
        self._checkpoint_publication_hook(
            stage="compaction_intent_written",
            run_root=run_root,
        )
        self._validate_pending_prior_current(run_root=run_root, pending=pending)
        _atomic_write_json(
            run_root / "CURRENT.json",
            {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": generation_id,
                "generation_manifest_digest": generation_digest,
            },
        )
        self._checkpoint_publication_hook(
            stage="current_snapshot_written",
            run_root=run_root,
        )
        _atomic_write_json(
            run_root / "condition_manifest.json",
            self._condition_manifest_v2_snapshot_body(
                run_root=run_root,
                generation_root=generation_root,
                prior_condition=prior_condition,
                logical_records_digest=str(
                    compacted["logical_records_digest"]
                ),
            ),
        )
        self._checkpoint_publication_hook(
            stage="condition_terminal_written",
            run_root=run_root,
        )
        self._refresh_evidence_manifest(run_root=run_root)
        self._validate_evidence_manifest()
        self._checkpoint_publication_hook(
            stage="inventory_terminal_written",
            run_root=run_root,
        )
        (run_root / _PENDING_FILE_NAME).unlink()
        for descriptor in chain:
            if descriptor.generation_root != generation_root:
                shutil.rmtree(descriptor.generation_root)
        self._v3_root_chain_cache.pop(
            run_root.resolve(strict=False).as_posix(),
            None,
        )
        return self._v3_commit_token(
            run_root=run_root,
            experiment_id=experiment_id,
            condition_id=condition_id,
            repeat_id=repeat_id,
            task_id=None,
            selection_ordinal=None,
            generation_id=generation_id,
            generation_manifest_digest=generation_digest,
        )

    def _v3_commit_token(
        self,
        *,
        run_root: Path,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        task_id: str | None,
        selection_ordinal: int | None,
        generation_id: str,
        generation_manifest_digest: str,
    ) -> dict[str, Any]:
        current = _read_json(run_root / "CURRENT.json")
        if (
            current.get("generation_id") != generation_id
            or current.get("generation_manifest_digest")
            != generation_manifest_digest
        ):
            raise ValueError("checkpoint commit token does not bind actual CURRENT")
        return {
            "schema_version": "tokenshare.paper_checkpoint_commit.v1",
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": str(repeat_id),
            "task_id": task_id,
            "run_root": run_root.relative_to(self.output_root).as_posix(),
            "generation_id": generation_id,
            "generation_manifest_digest": generation_manifest_digest,
            "current_digest": _digest_json(current),
            "selection_ordinal": selection_ordinal,
        }

    def _index_v3_root_chain(
        self,
        *,
        run_root: Path,
        head: V3GenerationDescriptor,
        head_manifest_digest: str,
        expected_root_count: int,
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        """每个CURRENT链只验证一次，并缓存root稳定identity与原始commit token。"""

        run_cache_key = run_root.resolve(strict=False).as_posix()
        marker = (head.generation_id, head_manifest_digest)
        cached = self._v3_root_chain_cache.get(run_cache_key)
        if cached is not None and cached[0] == marker:
            return cached[1]
        chain = validate_v3_delta_chain(
            run_root,
            head,
            expected_root_count=expected_root_count,
        )
        indexed: dict[str, tuple[str, dict[str, Any]]] = {}
        for descriptor in chain:
            if descriptor.delta_role != "root_outcome":
                continue
            assert descriptor.anchor_task_id is not None
            tasks = _read_jsonl(
                descriptor.generation_root / "per_task_results.jsonl"
            )
            stored_digest = _digest_json(
                {
                    "selection_ordinal": descriptor.selection_ordinal,
                    "task": tasks[0] if len(tasks) == 1 else None,
                    "attempts": _read_jsonl(
                        descriptor.generation_root
                        / "per_attempt_results.jsonl"
                    ),
                    "faults": _read_jsonl(
                        descriptor.generation_root / "fault_injections.jsonl"
                    ),
                    "events": _read_jsonl(
                        descriptor.generation_root
                        / "events"
                        / "event_log.jsonl"
                    ),
                    "artifacts": _read_jsonl(
                        descriptor.generation_root
                        / "artifacts"
                        / "artifact_index.jsonl"
                    ),
                }
            )
            generation_digest = descriptor.manifest_digest
            historical_current = {
                "schema_version": "tokenshare.paper_checkpoint_current.v1",
                "generation_id": descriptor.generation_id,
                "generation_manifest_digest": generation_digest,
            }
            token = {
                "schema_version": "tokenshare.paper_checkpoint_commit.v1",
                "experiment_id": descriptor.experiment_id,
                "condition_id": descriptor.condition_id,
                "repeat_id": str(descriptor.repeat_id),
                "task_id": descriptor.anchor_task_id,
                "run_root": run_root.relative_to(self.output_root).as_posix(),
                "generation_id": descriptor.generation_id,
                "generation_manifest_digest": generation_digest,
                "current_digest": _digest_json(historical_current),
                "selection_ordinal": descriptor.selection_ordinal,
            }
            indexed[descriptor.anchor_task_id] = (stored_digest, token)
        self._v3_root_chain_cache[run_cache_key] = (marker, indexed)
        return indexed

    def _validate_pending_prior_current(
        self,
        *,
        run_root: Path,
        pending: Mapping[str, Any],
    ) -> None:
        prior_generation_id = pending.get("expected_prior_generation_id")
        prior_generation_manifest_digest = pending.get(
            "expected_prior_generation_manifest_digest"
        )
        prior_current_digest = pending.get("expected_prior_current_digest")
        current_path = run_root / "CURRENT.json"
        if prior_generation_id is None:
            if (
                prior_generation_manifest_digest is not None
                or prior_current_digest is not None
                or current_path.exists()
            ):
                raise ValueError("checkpoint publication prior CURRENT conflict")
            return
        if not current_path.is_file():
            raise ValueError("checkpoint publication prior CURRENT is missing")
        current = _read_json(current_path)
        if (
            current.get("generation_id") != prior_generation_id
            or current.get("generation_manifest_digest")
            != prior_generation_manifest_digest
            or _digest_json(current) != prior_current_digest
        ):
            raise ValueError("checkpoint publication prior CURRENT conflict")

    def _checkpoint_publication_hook(self, *, stage: str, run_root: Path) -> None:
        """供断点恢复测试注入进程中断；生产路径默认无操作。"""

        del stage, run_root

    def _condition_manifest_body(
        self,
        *,
        run_root: Path,
        generation_root: Path,
    ) -> dict[str, Any]:
        run_manifest = _read_json(generation_root / "run_manifest.json")
        experiment_id = _safe_id(
            run_manifest.get("experiment_id"),
            "experiment_id",
        )
        condition_id = _safe_id(
            run_manifest.get("condition_id"),
            "condition_id",
        )
        condition = self._conditions.get((experiment_id, condition_id))
        if condition is None:
            raise ValueError("condition manifest identity is not frozen")
        tasks = _read_jsonl(generation_root / "per_task_results.jsonl")
        expected_count = self._expected_task_counts.get(
            (experiment_id, condition_id),
            len(tasks),
        )
        terminal_count = sum(_is_terminal_checkpoint(task) for task in tasks)
        status = str(run_manifest.get("status"))
        terminal = (
            len(tasks) == expected_count
            and terminal_count == expected_count
            and status != "running"
        )
        suite_manifest = _read_json(self.output_root / "suite_manifest.json")
        suite_identity = suite_manifest.get("suite_identity")
        frozen_suite = (
            suite_identity.get("suite", {}).get("body")
            if isinstance(suite_identity, Mapping)
            and isinstance(suite_identity.get("suite"), Mapping)
            else None
        )
        if not isinstance(frozen_suite, Mapping):
            raise ValueError("condition manifest suite identity is missing")
        capturing = suite_manifest.get("capturing")
        if not isinstance(capturing, bool):
            raise ValueError("condition manifest capturing identity is invalid")
        classification = _execution_classification(
            dict(frozen_suite),
            capturing=capturing,
        )
        reasons = set(classification["ineligibility_reasons"])
        if not terminal:
            reasons.add("condition_incomplete")
        outcome_status_eligible = status in {
            "completed",
            "completed_with_failures",
        }
        if terminal and not outcome_status_eligible:
            reasons.add(f"condition_status:{status}")
        tasks_eligible = bool(tasks) and all(
            task.get("paper_eligible") is True for task in tasks
        )
        if not tasks_eligible:
            reasons.add("task_ineligible")
        paper_eligible = (
            classification["paper_eligible"] is True
            and terminal
            and tasks_eligible
            and outcome_status_eligible
        )
        return {
            "schema_version": "tokenshare.paper_condition_evidence.v1",
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": run_manifest.get("repeat_id"),
            "condition_identity": {
                "body": condition,
                "digest": _digest_json(condition),
            },
            "expected_root_count": expected_count,
            "observed_root_count": len(tasks),
            "terminal_root_count": terminal_count,
            "status": status,
            "terminal": terminal,
            "current_ref": _file_evidence(
                self.output_root,
                run_root / "CURRENT.json",
            ),
            "generation_manifest_ref": _file_evidence(
                self.output_root,
                generation_root / "generation_manifest.json",
            ),
            "run_manifest_ref": _file_evidence(
                self.output_root,
                generation_root / "run_manifest.json",
            ),
            "paper_eligible": paper_eligible,
            "ineligibility_reasons": sorted(reasons),
        }

    def _condition_manifest_v2_body(
        self,
        *,
        run_root: Path,
        generation_root: Path,
        task_body: Mapping[str, Any],
        artifact_bodies: Sequence[Mapping[str, Any]],
        prior_condition: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """从 prior condition 摘要和单个新 delta 增量构造 running v2。"""

        run_manifest = _read_json(generation_root / "run_manifest.json")
        experiment_id = _safe_id(run_manifest.get("experiment_id"), "experiment_id")
        condition_id = _safe_id(run_manifest.get("condition_id"), "condition_id")
        condition = self._conditions.get((experiment_id, condition_id))
        if condition is None:
            raise ValueError("condition manifest identity is not frozen")
        prior_root_count = 0
        prior_terminal_count = 0
        prior_chain_count = 0
        prior_event_delta_count = 0
        prior_reachable_size = 0
        prior_commit_digest: str | None = None
        prior_expected_count: int | None = None
        if prior_condition is not None:
            _require_exact_keys(
                prior_condition,
                _CONDITION_MANIFEST_V2_KEYS,
                "condition manifest v2",
            )
            prior_root_count = int(prior_condition["root_delta_count"])
            prior_terminal_count = int(prior_condition["terminal_root_count"])
            prior_chain_count = int(prior_condition["chain_generation_count"])
            prior_event_delta_count = int(
                prior_condition["condition_event_delta_count"]
            )
            prior_reachable_size = int(prior_condition["reachable_size_bytes"])
            prior_commit_digest = str(prior_condition["commit_chain_digest"])
            prior_expected_count = int(prior_condition["expected_root_count"])
        expected_count = self._expected_task_counts.get(
            (experiment_id, condition_id),
            prior_expected_count
            if prior_expected_count is not None
            else prior_root_count + 1,
        )
        if prior_expected_count is not None and expected_count != prior_expected_count:
            raise ValueError("condition expected root count drift")
        new_root_count = prior_root_count + 1
        if new_root_count > expected_count:
            raise ValueError("condition root delta exceeds frozen denominator")
        generation_manifest = _read_json(
            generation_root / "generation_manifest.json"
        )
        generation_manifest_digest = _digest_json(generation_manifest)
        generation_bytes = sum(
            int(entry["size"])
            for entry in generation_manifest["files"]
            if isinstance(entry, Mapping)
        ) + (generation_root / "generation_manifest.json").stat().st_size
        payload_bytes = 0
        for artifact in artifact_bodies:
            relative_path = artifact.get("path")
            if isinstance(relative_path, str):
                payload_bytes += (self.output_root / relative_path).stat().st_size
        reachable_size = prior_reachable_size + generation_bytes + payload_bytes
        commit_chain_digest = _digest_json(
            {
                "prior_commit_chain_digest": prior_commit_digest,
                "generation_id": generation_root.name,
                "generation_manifest_digest": generation_manifest_digest,
            }
        )
        terminal_count = prior_terminal_count + int(_is_terminal_checkpoint(task_body))
        suite_manifest = _read_json(self.output_root / "suite_manifest.json")
        suite_identity = suite_manifest.get("suite_identity")
        frozen_suite = (
            suite_identity.get("suite", {}).get("body")
            if isinstance(suite_identity, Mapping)
            and isinstance(suite_identity.get("suite"), Mapping)
            else None
        )
        if not isinstance(frozen_suite, Mapping):
            raise ValueError("condition manifest suite identity is missing")
        capturing = suite_manifest.get("capturing")
        if not isinstance(capturing, bool):
            raise ValueError("condition manifest capturing identity is invalid")
        classification = _execution_classification(
            dict(frozen_suite),
            capturing=capturing,
        )
        reasons = set(classification["ineligibility_reasons"])
        reasons.add("condition_incomplete")
        if task_body.get("paper_eligible") is not True:
            reasons.add("task_ineligible")
        return {
            "schema_version": "tokenshare.paper_condition_evidence.v2",
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": run_manifest.get("repeat_id"),
            "condition_identity": {
                "body": condition,
                "digest": _digest_json(condition),
            },
            "expected_root_count": expected_count,
            "observed_root_count": new_root_count,
            "terminal_root_count": terminal_count,
            "status": "running",
            "terminal": False,
            "head_generation_kind": "delta",
            "chain_generation_count": prior_chain_count + 1,
            "root_delta_count": new_root_count,
            "condition_event_delta_count": prior_event_delta_count,
            "logical_task_count": new_root_count,
            "reachable_size_bytes": reachable_size,
            "commit_chain_digest": commit_chain_digest,
            "logical_records_digest": None,
            "current_ref": _file_evidence(
                self.output_root,
                run_root / "CURRENT.json",
            ),
            "generation_manifest_ref": _file_evidence(
                self.output_root,
                generation_root / "generation_manifest.json",
            ),
            "run_manifest_ref": _file_evidence(
                self.output_root,
                generation_root / "run_manifest.json",
            ),
            "paper_eligible": False,
            "ineligibility_reasons": sorted(reasons),
        }

    def _condition_manifest_v2_tail_body(
        self,
        *,
        run_root: Path,
        generation_root: Path,
        prior_condition: Mapping[str, Any],
    ) -> dict[str, Any]:
        """把唯一 closure delta 增量并入 running condition 摘要。"""

        _require_exact_keys(
            prior_condition,
            _CONDITION_MANIFEST_V2_KEYS,
            "condition manifest v2",
        )
        if prior_condition.get("condition_event_delta_count") != 0:
            raise ValueError("condition closure already exists")
        generation_manifest = _read_json(
            generation_root / "generation_manifest.json"
        )
        generation_manifest_digest = _digest_json(generation_manifest)
        generation_bytes = sum(
            int(entry["size"])
            for entry in generation_manifest["files"]
            if isinstance(entry, Mapping)
        ) + (generation_root / "generation_manifest.json").stat().st_size
        result = dict(prior_condition)
        result.update(
            {
                "head_generation_kind": "delta",
                "chain_generation_count": int(
                    prior_condition["chain_generation_count"]
                )
                + 1,
                "condition_event_delta_count": 1,
                "reachable_size_bytes": int(
                    prior_condition["reachable_size_bytes"]
                )
                + generation_bytes,
                "commit_chain_digest": _digest_json(
                    {
                        "prior_commit_chain_digest": prior_condition[
                            "commit_chain_digest"
                        ],
                        "generation_id": generation_root.name,
                        "generation_manifest_digest": generation_manifest_digest,
                    }
                ),
                "logical_records_digest": None,
                "current_ref": _file_evidence(
                    self.output_root,
                    run_root / "CURRENT.json",
                ),
                "generation_manifest_ref": _file_evidence(
                    self.output_root,
                    generation_root / "generation_manifest.json",
                ),
                "run_manifest_ref": _file_evidence(
                    self.output_root,
                    generation_root / "run_manifest.json",
                ),
            }
        )
        return result

    def _condition_manifest_v2_snapshot_body(
        self,
        *,
        run_root: Path,
        generation_root: Path,
        prior_condition: Mapping[str, Any],
        logical_records_digest: str,
    ) -> dict[str, Any]:
        """从已验证snapshot与running摘要构造terminal condition v2。"""

        _require_exact_keys(
            prior_condition,
            _CONDITION_MANIFEST_V2_KEYS,
            "condition manifest v2",
        )
        base = self._condition_manifest_body(
            run_root=run_root,
            generation_root=generation_root,
        )
        if base.get("terminal") is not True:
            raise ValueError("terminal snapshot task denominator is incomplete")
        manifest = _read_json(generation_root / "generation_manifest.json")
        descriptor = validate_v3_generation_manifest(
            generation_root,
            manifest,
            expected_experiment_id=str(base["experiment_id"]),
            expected_condition_id=str(base["condition_id"]),
            expected_repeat_id=base["repeat_id"],
        )
        if descriptor.generation_kind != "snapshot":
            raise ValueError("terminal condition must point to a snapshot")
        artifacts = _read_jsonl(
            generation_root / "artifacts" / "artifact_index.jsonl"
        )
        payload_paths: set[Path] = set()
        for artifact in artifacts:
            task_id = _safe_id(artifact.get("task_id"), "task_id")
            self._validate_artifact_refs(
                [artifact],
                experiment_id=str(base["experiment_id"]),
                condition_id=str(base["condition_id"]),
                repeat_id=base["repeat_id"],
                task_id=task_id,
                run_root=run_root,
            )
            payload_paths.add(
                (self.output_root / str(artifact["path"])).resolve(
                    strict=False
                )
            )
        reachable_size = (
            (generation_root / "generation_manifest.json").stat().st_size
            + sum(
                int(entry["size"])
                for entry in manifest["files"]
                if isinstance(entry, Mapping)
            )
            + sum(path.stat().st_size for path in payload_paths)
        )
        result = dict(base)
        result.update(
            {
                "schema_version": "tokenshare.paper_condition_evidence.v2",
                "head_generation_kind": "snapshot",
                "chain_generation_count": 1,
                "root_delta_count": int(prior_condition["root_delta_count"]),
                "condition_event_delta_count": int(
                    prior_condition["condition_event_delta_count"]
                ),
                "logical_task_count": int(base["observed_root_count"]),
                "reachable_size_bytes": reachable_size,
                "commit_chain_digest": descriptor.compacted_chain_digest,
                "logical_records_digest": logical_records_digest,
            }
        )
        return result

    @classmethod
    def load(
        cls,
        *,
        output_root: str | Path,
        expected_suite: Any,
        expected_dispatch: Any,
        expected_budget: Any,
        expected_catalog: Any,
        expected_identity: Any,
        expected_request_limits: Any,
        expected_hard_limits: Any,
    ) -> LoadedFormalEvidence:
        """验证 suite identity 和全部 checkpoint evidence 后返回完成索引。"""

        store = cls(output_root)
        suite_path = store.output_root / "suite_manifest.json"
        if not suite_path.is_file():
            raise ValueError("required evidence file is missing: suite_manifest.json")
        suite_manifest = _read_json(suite_path)
        frozen = suite_manifest.get("suite_identity")
        if not isinstance(frozen, dict):
            raise ValueError("suite identity evidence is missing")
        expected = {
            "suite": expected_suite,
            "dispatch": expected_dispatch,
            "budget": expected_budget,
            "catalog": expected_catalog,
            "identity": expected_identity,
            "request_limits": expected_request_limits,
            "hard_limits": expected_hard_limits,
        }
        for name in _IDENTITY_NAMES:
            body = _json_value(expected[name])
            component = frozen.get(name)
            if (
                not isinstance(component, dict)
                or component.get("digest") != _digest_json(body)
                or _canonical_bytes(component.get("body")) != _canonical_bytes(body)
            ):
                raise ValueError(f"suite identity drift: {name}")

        actual_root_bodies = {
            "budget": _read_json_value(store.output_root / "run_budget.json"),
            "catalog": _read_json_value(
                store.output_root / "input_catalog_manifest.json"
            ),
            "dispatch": _read_json_value(
                store.output_root / "paper_dispatch_plans.json"
            ),
        }
        for name, actual_body in actual_root_bodies.items():
            if _canonical_bytes(actual_body) != _canonical_bytes(frozen[name]["body"]):
                raise ValueError(f"suite identity file mismatch: {name}")
        frozen_conditions = [
            condition
            for conditions in _conditions_by_experiment(frozen["dispatch"]["body"]).values()
            for condition in conditions
        ]
        actual_conditions = _read_jsonl(store.output_root / "conditions.jsonl")
        if _canonical_bytes(actual_conditions) != _canonical_bytes(frozen_conditions):
            raise ValueError("suite identity file mismatch: conditions")

        store.repair_interrupted_checkpoint_publication()
        store._validate_evidence_manifest()
        store._validate_suite_and_experiment_manifests(
            suite_manifest=suite_manifest,
            frozen_suite=frozen["suite"]["body"],
            frozen_dispatch=frozen["dispatch"]["body"],
        )
        completed_by_experiment: dict[str, set[str]] = {}
        completed_task_keys: set[tuple[str, str, str, str]] = set()
        for experiment_id in _conditions_by_experiment(frozen["dispatch"]["body"]):
            experiment_root = store.output_root / "experiments" / experiment_id
            completed = completed_by_experiment.setdefault(experiment_id, set())
            runs_root = experiment_root / "runs"
            run_roots = (
                sorted(
                    repeat_root
                    for condition_root in runs_root.iterdir()
                    if condition_root.is_dir()
                    for repeat_root in condition_root.iterdir()
                    if repeat_root.is_dir()
                )
                if runs_root.is_dir()
                else []
            )
            for run_root in run_roots:
                records = store._validate_run(
                    run_root,
                    experiment_id=experiment_id,
                )
                completed.update(
                    str(item["task_id"]) for item in records if _is_success(item)
                )
                completed_task_keys.update(
                    (
                        experiment_id,
                        run_root.parent.name,
                        run_root.name,
                        str(item["task_id"]),
                    )
                    for item in records
                    # checkpoint 已冻结的负面结果同样是终态；resume 不得把它
                    # 当作新的 provider retry 再次执行。
                    if _is_terminal_checkpoint(item)
                )
        normalized = {
            experiment_id: tuple(sorted(task_ids))
            for experiment_id, task_ids in completed_by_experiment.items()
            if task_ids
        }
        all_completed = tuple(
            sorted({task_id for task_ids in normalized.values() for task_id in task_ids})
        )
        return LoadedFormalEvidence(
            completed_task_ids=all_completed,
            completed_task_ids_by_experiment=normalized,
            completed_task_keys=tuple(sorted(completed_task_keys)),
        )

    def _validate_suite_and_experiment_manifests(
        self,
        *,
        suite_manifest: dict[str, Any],
        frozen_suite: Any,
        frozen_dispatch: Any,
    ) -> None:
        if not isinstance(frozen_suite, dict):
            raise ValueError("frozen suite manifest identity must be an object")
        actual_frozen_fields = {
            field_name: value
            for field_name, value in suite_manifest.items()
            if field_name not in _SUITE_RUNTIME_FIELDS
        }
        if _canonical_bytes(actual_frozen_fields) != _canonical_bytes(frozen_suite):
            raise ValueError("suite manifest frozen identity mismatch")
        capturing = suite_manifest.get("capturing")
        if not isinstance(capturing, bool):
            raise ValueError("suite manifest capturing identity is invalid")
        classification = _execution_classification(
            frozen_suite,
            capturing=capturing,
        )
        required_suite_flags = {
            "formal": classification["formal"],
            "pilot_only": classification["pilot_only"],
            "execution_scope": classification["execution_scope"],
            "regression_only": classification["regression_only"],
            "ineligibility_reasons": classification["ineligibility_reasons"],
            "baseline_policy": classification.get(
                "baseline_policy",
                "required_by_formal_plan",
            ),
        }
        for field_name, expected_value in required_suite_flags.items():
            if suite_manifest.get(field_name) != expected_value:
                raise ValueError(f"suite manifest {field_name} identity mismatch")
        if not isinstance(suite_manifest.get("paper_eligible"), bool):
            raise ValueError("suite manifest paper eligibility is invalid")
        if (
            capturing
            or classification["paper_eligible"] is False
        ) and suite_manifest.get("paper_eligible") is True:
            raise ValueError("classified suite cannot be paper eligible")

        plans = _dispatch_plans(frozen_dispatch)
        plan_by_experiment = {plan["experiment_id"]: plan for plan in plans}
        conditions_by_experiment = _conditions_by_experiment(frozen_dispatch)
        declared_ids = set(conditions_by_experiment)
        experiments_root = self.output_root / "experiments"
        actual_ids = {
            path.name for path in experiments_root.iterdir() if path.is_dir()
        } if experiments_root.is_dir() else set()
        if actual_ids != declared_ids:
            extra = actual_ids - declared_ids
            if extra:
                raise ValueError("undeclared experiment root exists")
            raise ValueError("declared experiment manifest root is missing")
        for experiment_id, conditions in conditions_by_experiment.items():
            manifest_path = (
                experiments_root / experiment_id / "experiment_manifest.json"
            )
            if not manifest_path.is_file():
                raise ValueError("declared experiment manifest is missing")
            manifest = _read_json(manifest_path)
            expected = {
                "schema_version": "tokenshare.paper_experiment_evidence.v1",
                "suite_id": suite_manifest.get("suite_id"),
                "experiment_id": experiment_id,
                "condition_ids": [item["condition_id"] for item in conditions],
                "formal": classification["formal"],
                "pilot_only": classification["pilot_only"],
                "execution_scope": classification["execution_scope"],
                "capturing": capturing,
                "regression_only": classification["regression_only"],
                "ineligibility_reasons": classification[
                    "ineligibility_reasons"
                ],
                "baseline_policy": classification.get(
                    "baseline_policy",
                    "required_by_formal_plan",
                ),
                "dispatch_plan_digest": _digest_json(
                    plan_by_experiment[experiment_id]
                ),
            }
            for field_name, expected_value in expected.items():
                if manifest.get(field_name) != expected_value:
                    raise ValueError(
                        f"experiment manifest {field_name} identity mismatch"
                    )
            if manifest.get("status") not in {
                "planned",
                "running",
                "completed",
                "completed_with_failures",
                "blocked",
                "incomplete",
                "budget_exhausted",
                "failed",
            }:
                raise ValueError("experiment manifest status is invalid")
            if not isinstance(manifest.get("paper_eligible"), bool):
                raise ValueError("experiment manifest paper eligibility is invalid")
            if (
                capturing
                or classification["paper_eligible"] is False
            ) and manifest.get("paper_eligible") is True:
                raise ValueError("classified experiment cannot be paper eligible")

    def _current_generation_root(
        self,
        run_root: Path,
        *,
        required: bool,
    ) -> Path | None:
        pointer_path = run_root / "CURRENT.json"
        generations_root = run_root / ".generations"
        if not pointer_path.is_file():
            if required:
                raise ValueError("checkpoint generation has no commit marker")
            return None
        pointer = _read_json(pointer_path)
        _require_exact_keys(pointer, _CURRENT_KEYS, "checkpoint CURRENT")
        if (
            pointer.get("schema_version")
            != "tokenshare.paper_checkpoint_current.v1"
        ):
            raise ValueError("checkpoint CURRENT schema version mismatch")
        _require_string(pointer, "generation_id", "checkpoint CURRENT")
        _require_digest(
            pointer,
            "generation_manifest_digest",
            "checkpoint CURRENT",
        )
        generation_id = _safe_id(pointer.get("generation_id"), "generation_id")
        generation_root = generations_root / generation_id
        if not generation_root.is_dir():
            raise ValueError("checkpoint CURRENT generation is missing")
        generation_manifest = self._validate_generation_root(generation_root)
        if pointer.get("generation_manifest_digest") != _digest_json(
            generation_manifest
        ):
            raise ValueError("checkpoint commit marker digest mismatch")
        return generation_root

    def _validate_generation_root(
        self,
        generation_root: Path,
    ) -> dict[str, Any]:
        manifest_path = generation_root / "generation_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("checkpoint generation manifest is missing")
        generation_manifest = _read_json(manifest_path)
        generation_schema = generation_manifest.get("schema_version")
        if generation_schema == V3_GENERATION_SCHEMA:
            validate_v3_generation_manifest(
                generation_root,
                generation_manifest,
            )
            return generation_manifest
        if generation_schema not in {
            "tokenshare.paper_checkpoint_generation.v1",
            "tokenshare.paper_checkpoint_generation.v2",
        }:
            raise ValueError("checkpoint generation schema version mismatch")
        expected_keys = (
            _GENERATION_MANIFEST_V1_KEYS
            if generation_schema == "tokenshare.paper_checkpoint_generation.v1"
            else _GENERATION_MANIFEST_V2_KEYS
        )
        _require_exact_keys(
            generation_manifest,
            expected_keys,
            "checkpoint generation manifest",
        )
        _require_string(
            generation_manifest,
            "generation_id",
            "checkpoint generation manifest",
        )
        if generation_schema == "tokenshare.paper_checkpoint_generation.v2":
            parent_generation_id = generation_manifest.get("parent_generation_id")
            parent_generation_digest = generation_manifest.get(
                "parent_generation_manifest_digest"
            )
            if parent_generation_id is None:
                if parent_generation_digest is not None:
                    raise ValueError(
                        "checkpoint generation parent binding is invalid"
                    )
            else:
                _safe_id(parent_generation_id, "parent_generation_id")
                if (
                    not isinstance(parent_generation_digest, str)
                    or not parent_generation_digest.startswith("sha256:")
                ):
                    raise ValueError("checkpoint generation parent digest is invalid")
        if not isinstance(generation_manifest.get("files"), list):
            raise ValueError("checkpoint generation manifest files type is invalid")
        if generation_manifest.get("generation_id") != generation_root.name:
            raise ValueError("checkpoint generation identity mismatch")
        entries = generation_manifest.get("files")
        assert isinstance(entries, list)
        expected_paths = set(_RUN_FILES)
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid checkpoint generation evidence")
            relative_path = entry["path"]
            if relative_path in seen:
                raise ValueError("duplicate checkpoint generation evidence")
            seen.add(relative_path)
            path = (generation_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, generation_root) or not path.is_file():
                raise ValueError("checkpoint generation evidence is missing")
            if _file_evidence(generation_root, path) != entry:
                raise ValueError("checkpoint generation evidence integrity mismatch")
        if seen != expected_paths:
            raise ValueError("checkpoint generation required evidence mismatch")
        actual_paths = {
            path.relative_to(generation_root).as_posix()
            for path in generation_root.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        }
        if actual_paths != expected_paths | {"generation_manifest.json"}:
            raise ValueError("checkpoint generation contains incomplete evidence")
        return generation_manifest

    def _load_condition_index(self) -> dict[tuple[str, str], dict[str, Any]]:
        path = self.output_root / "conditions.jsonl"
        if not path.is_file():
            return {}
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for condition in _read_jsonl(path):
            experiment_id = _safe_id(condition.get("experiment_id"), "experiment_id")
            condition_id = _safe_id(condition.get("condition_id"), "condition_id")
            key = (experiment_id, condition_id)
            if key in result:
                raise ValueError("duplicate condition evidence")
            result[key] = condition
        return result

    def _load_expected_task_counts(self) -> dict[tuple[str, str], int]:
        suite_path = self.output_root / "suite_manifest.json"
        if not suite_path.is_file():
            return {}
        suite = _read_json(suite_path)
        identity = suite.get("suite_identity")
        if not isinstance(identity, dict):
            return {}
        dispatch_component = identity.get("dispatch")
        if not isinstance(dispatch_component, dict):
            return {}
        dispatch = dispatch_component.get("body")
        suite_component = identity.get("suite")
        suite_body = (
            suite_component.get("body")
            if isinstance(suite_component, dict)
            else None
        )
        root_case_filter = (
            suite_body.get("root_case_filter", {})
            if isinstance(suite_body, dict)
            else {}
        )
        if not isinstance(root_case_filter, dict):
            raise ValueError("suite root_case_filter must be an object")
        result: dict[tuple[str, str], int] = {}
        for plan in _dispatch_plans(dispatch):
            experiment_id = str(plan["experiment_id"])
            conditions = plan.get("conditions", [])
            selections = plan.get("selections", [])
            if not isinstance(conditions, list) or not isinstance(selections, list):
                continue
            if len(conditions) != len(selections):
                continue
            for condition, selection in zip(conditions, selections, strict=True):
                ordered = selection.get("ordered_case_ids", [])
                if isinstance(ordered, list):
                    condition_id = str(condition["condition_id"])
                    filtered = root_case_filter.get(condition_id, ordered)
                    if not isinstance(filtered, list):
                        raise ValueError("suite root_case_filter entry must be a list")
                    result[(experiment_id, condition_id)] = len(filtered)
        return result

    def _load_selection_ordinals(self) -> dict[tuple[str, str, str], int]:
        """从冻结 dispatch selection 构造 case→ordinal，只在 store 建立时读取一次。"""

        suite_path = self.output_root / "suite_manifest.json"
        if not suite_path.is_file():
            return {}
        suite = _read_json(suite_path)
        identity = suite.get("suite_identity")
        dispatch_component = (
            identity.get("dispatch") if isinstance(identity, Mapping) else None
        )
        dispatch = (
            dispatch_component.get("body")
            if isinstance(dispatch_component, Mapping)
            else None
        )
        suite_component = (
            identity.get("suite") if isinstance(identity, Mapping) else None
        )
        suite_body = (
            suite_component.get("body")
            if isinstance(suite_component, Mapping)
            else None
        )
        root_case_filter = (
            suite_body.get("root_case_filter", {})
            if isinstance(suite_body, Mapping)
            else {}
        )
        if not isinstance(root_case_filter, Mapping):
            raise ValueError("suite root_case_filter must be an object")
        result: dict[tuple[str, str, str], int] = {}
        for plan in _dispatch_plans(dispatch):
            experiment_id = str(plan["experiment_id"])
            conditions = plan.get("conditions", [])
            selections = plan.get("selections", [])
            if not isinstance(conditions, list) or not isinstance(selections, list):
                continue
            if len(conditions) != len(selections):
                continue
            for condition, selection in zip(conditions, selections, strict=True):
                condition_id = str(condition["condition_id"])
                ordered = selection.get("ordered_case_ids", [])
                if not isinstance(ordered, list):
                    continue
                filtered = root_case_filter.get(condition_id, ordered)
                if not isinstance(filtered, list):
                    raise ValueError("suite root_case_filter entry must be a list")
                for ordinal, case_id_value in enumerate(filtered):
                    case_id = _safe_id(case_id_value, "case_id")
                    key = (experiment_id, condition_id, case_id)
                    if key in result:
                        raise ValueError("duplicate case in frozen condition selection")
                    result[key] = ordinal
        return result

    def _validate_artifact_refs(
        self,
        refs: Sequence[dict[str, Any]],
        *,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
        task_id: str,
        run_root: Path,
    ) -> None:
        artifact_root = (run_root / "artifacts").resolve(strict=False)
        for ref in refs:
            expected_identity = {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": repeat_id,
                "task_id": task_id,
            }
            for field_name, expected_value in expected_identity.items():
                if field_name not in ref or ref[field_name] != expected_value:
                    raise ValueError(
                        f"artifact ref {field_name} does not match run identity"
                    )
            relative_path = ref.get("path")
            digest = ref.get("content_hash")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("artifact ref requires path evidence")
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise ValueError("artifact ref requires content_hash evidence")
            path = (self.output_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, artifact_root) or path == artifact_root:
                raise ValueError("artifact ref crosses run artifact path isolation")
            if not path.is_file():
                raise ValueError("artifact evidence is missing or hash mismatched")
            artifact_bytes = path.read_bytes()
            if _digest_bytes(artifact_bytes) != digest:
                raise ValueError("artifact evidence is missing or hash mismatched")
            expected_size = ref.get("size_bytes")
            if expected_size is not None and (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size != len(artifact_bytes)
            ):
                raise ValueError("artifact evidence size mismatched")
            if isinstance(ref.get("source_artifact_ref"), Mapping) and (
                expected_size is None
            ):
                raise ValueError("materialized artifact evidence requires size_bytes")

    def _validate_reachable_artifact_closure(
        self,
        *,
        records: Sequence[Mapping[str, Any]],
        artifacts: Sequence[Mapping[str, Any]],
    ) -> None:
        """从 run 记录的 ArtifactRef 种子重算并验证完整可达闭包。"""

        indexed: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
        task_ids_by_identity: dict[tuple[str, str, str], set[str]] = {}
        artifact_id_hashes: dict[tuple[str, str], str] = {}
        for artifact in artifacts:
            source_ref = artifact.get("source_artifact_ref")
            if not isinstance(source_ref, Mapping):
                continue
            _validate_complete_source_artifact_ref(source_ref)
            task_id = str(artifact.get("task_id") or "")
            if not task_id:
                raise ValueError("reachable artifact closure task identity is missing")
            artifact_id = str(source_ref["artifact_id"])
            content_hash = str(source_ref["content_hash"])
            prior_hash = artifact_id_hashes.setdefault(
                (task_id, artifact_id),
                content_hash,
            )
            if prior_hash != content_hash:
                raise ValueError("reachable artifact closure identity conflict")
            source_identity = _source_artifact_identity(source_ref)
            task_ids_by_identity.setdefault(source_identity, set()).add(task_id)
            key = (task_id, *source_identity)
            prior = indexed.setdefault(key, artifact)
            if prior is not artifact and prior.get("path") != artifact.get("path"):
                raise ValueError("reachable artifact closure index is ambiguous")

        protocol_task_ids: dict[str, str] = {}
        for record in records:
            task_id = str(record.get("task_id") or "")
            protocol_task_id = record.get("protocol_task_id")
            if (
                not task_id
                or not isinstance(protocol_task_id, str)
                or not protocol_task_id
            ):
                continue
            prior_task_id = protocol_task_ids.setdefault(protocol_task_id, task_id)
            if prior_task_id != task_id:
                raise ValueError(
                    "reachable artifact closure protocol task mapping conflict"
                )

        queue: deque[tuple[str, Mapping[str, Any]]] = deque()
        for record in records:
            task_id = str(record.get("task_id") or "")
            artifact_task_id = protocol_task_ids.get(task_id, task_id)
            for source_ref in _nested_source_artifact_refs(record):
                if artifact_task_id:
                    queue.append((artifact_task_id, source_ref))
                    continue
                source_identity = _source_artifact_identity(source_ref)
                matching_task_ids = task_ids_by_identity.get(source_identity, set())
                if not matching_task_ids:
                    queue.append(("__unbound__", source_ref))
                else:
                    queue.extend(
                        (matching_task_id, source_ref)
                        for matching_task_id in sorted(matching_task_ids)
                    )
        seen: set[tuple[str, str, str, str]] = set()
        while queue:
            task_id, source_ref = queue.popleft()
            _validate_complete_source_artifact_ref(source_ref)
            key = (task_id, *_source_artifact_identity(source_ref))
            if key in seen:
                continue
            seen.add(key)
            artifact = indexed.get(key)
            if artifact is None:
                raise ValueError(
                    "reachable artifact closure is missing an artifact index row"
                )
            if artifact.get("content_hash") != source_ref.get("content_hash"):
                raise ValueError("reachable artifact closure hash identity mismatch")
            if artifact.get("size_bytes") != source_ref.get("size_bytes"):
                raise ValueError("reachable artifact closure size identity mismatch")
            source_uri = artifact.get("source_uri")
            if source_uri is not None and source_uri != source_ref.get("uri"):
                raise ValueError("reachable artifact closure source URI mismatch")

            indexed_source_ref = artifact.get("source_artifact_ref")
            assert isinstance(indexed_source_ref, Mapping)
            queue.extend(
                (task_id, child)
                for child in _nested_source_artifact_refs(
                    indexed_source_ref.get("source")
                )
            )
            queue.extend(
                (task_id, child)
                for child in _nested_source_artifact_refs(
                    indexed_source_ref.get("metadata")
                )
            )
            relative_path = artifact.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("reachable artifact closure requires path evidence")
            path = self.output_root / relative_path
            payload_bytes = path.read_bytes()
            media_type = indexed_source_ref.get("media_type")
            declared_json = _declares_json_media_type(media_type)
            try:
                payload = json.loads(payload_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                if declared_json:
                    raise ValueError(
                        "reachable artifact closure contains invalid declared JSON"
                    ) from error
                payload = None
            if payload is not None:
                queue.extend(
                    (task_id, child)
                    for child in _nested_source_artifact_refs(payload)
                )

    def _refresh_evidence_manifest(self, *, run_root: Path | None = None) -> None:
        """新运行写v2；root热路径只替换一个condition entry。"""

        manifest_path = self.output_root / "evidence_manifest.json"
        existing = _read_json(manifest_path) if manifest_path.is_file() else None
        if existing is not None and existing.get("schema_version") == (
            "tokenshare.paper_evidence_manifest.v1"
        ):
            raise ValueError("historical evidence manifest v1 is read-only")
        if existing is not None:
            _require_exact_keys(
                existing,
                {"schema_version", "files", "conditions"},
                "evidence manifest v2",
            )
            if existing.get("schema_version") != (
                "tokenshare.paper_evidence_manifest.v2"
            ):
                raise ValueError("evidence manifest schema version mismatch")
        if run_root is None:
            files = [
                _file_evidence(self.output_root, path)
                for path in self._v2_static_paths()
            ]
            conditions = list(existing.get("conditions", [])) if existing else []
        else:
            if existing is None:
                raise ValueError("evidence manifest v2 must exist before checkpoint")
            files = list(existing["files"])
            conditions = list(existing["conditions"])
            condition_path = run_root / "condition_manifest.json"
            condition = _read_json(condition_path)
            _require_exact_keys(
                condition,
                _CONDITION_MANIFEST_V2_KEYS,
                "condition manifest v2",
            )
            entry = {
                "experiment_id": condition["experiment_id"],
                "condition_id": condition["condition_id"],
                "repeat_id": condition["repeat_id"],
                "condition_manifest_ref": _file_evidence(
                    self.output_root,
                    condition_path,
                ),
                "reachable_size_bytes": condition["reachable_size_bytes"],
            }
            key = (
                str(entry["experiment_id"]),
                str(entry["condition_id"]),
                str(entry["repeat_id"]),
            )
            retained = [
                item
                for item in conditions
                if (
                    str(item.get("experiment_id")),
                    str(item.get("condition_id")),
                    str(item.get("repeat_id")),
                )
                != key
            ]
            retained.append(entry)
            conditions = sorted(
                retained,
                key=lambda item: (
                    str(item["experiment_id"]),
                    str(item["condition_id"]),
                    str(item["repeat_id"]),
                ),
            )
        _atomic_write_json(
            manifest_path,
            {
                "schema_version": "tokenshare.paper_evidence_manifest.v2",
                "files": files,
                "conditions": conditions,
            },
        )

    def _v2_static_paths(self) -> list[Path]:
        result: list[Path] = []
        for path in sorted(self.output_root.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(self.output_root).as_posix()
            parts = Path(relative_path).parts
            if (
                path.name in {
                    "evidence_manifest.json",
                    _LOCK_FILE_NAME,
                    _PENDING_FILE_NAME,
                }
                or path.name.endswith(".tmp")
                or (
                    len(parts) >= 3
                    and parts[0] == "experiments"
                    and parts[2] == "runs"
                )
                or _is_adapter_compatibility_path(
                    self.output_root,
                    relative_path,
                )
            ):
                continue
            result.append(path)
        return result

    def repair_interrupted_checkpoint_publication(self) -> dict[str, Any] | None:
        """只按原子 PENDING intent 补完已完整落盘的 v2 checkpoint。"""

        pending_paths = sorted(
            self.output_root.glob(
                f"experiments/*/runs/*/*/{_PENDING_FILE_NAME}"
            )
        )
        if not pending_paths:
            return None
        repaired: list[dict[str, Any]] = []
        repaired_pending_paths: list[Path] = []
        cleanup_generations: list[tuple[Path, tuple[str, ...]]] = []
        with self._lock:
            with _exclusive_output_root_lock(self.output_root):
                for pending_path in pending_paths:
                    run_root = pending_path.parent
                    pending = _read_json(pending_path)
                    if pending.get("schema_version") == (
                        "tokenshare.paper_checkpoint_pending.v2"
                    ):
                        repair_result = self._repair_v3_pending_publication(
                            run_root=run_root,
                            pending=pending,
                        )
                        cleanup_ids = tuple(
                            repair_result.pop("cleanup_generation_ids", [])
                        )
                        repaired.append(repair_result)
                        if cleanup_ids:
                            cleanup_generations.append((run_root, cleanup_ids))
                        repaired_pending_paths.append(pending_path)
                        continue
                    _require_exact_keys(
                        pending,
                        _PENDING_KEYS,
                        "checkpoint PENDING",
                    )
                    if (
                        pending.get("schema_version")
                        != "tokenshare.paper_checkpoint_pending.v1"
                    ):
                        raise ValueError("checkpoint PENDING schema mismatch")
                    for field_name in (
                        "target_generation_id",
                        "target_generation_manifest_digest",
                        "experiment_id",
                        "condition_id",
                        "task_id",
                    ):
                        _require_string(pending, field_name, "checkpoint PENDING")
                    target_generation_id = _safe_id(
                        pending["target_generation_id"],
                        "target_generation_id",
                    )
                    target_root = (
                        run_root / ".generations" / target_generation_id
                    )
                    target_manifest = self._validate_generation_root(target_root)
                    if (
                        target_manifest.get("schema_version")
                        != "tokenshare.paper_checkpoint_generation.v2"
                    ):
                        raise ValueError(
                            "checkpoint PENDING cannot promote a v1 generation"
                        )
                    if pending.get("target_generation_manifest_digest") != (
                        _digest_json(target_manifest)
                    ):
                        raise ValueError("checkpoint PENDING target digest mismatch")
                    if (
                        target_manifest.get("parent_generation_id")
                        != pending.get("expected_prior_generation_id")
                        or target_manifest.get(
                            "parent_generation_manifest_digest"
                        )
                        != pending.get(
                            "expected_prior_generation_manifest_digest"
                        )
                    ):
                        raise ValueError(
                            "checkpoint PENDING parent generation conflict"
                        )
                    experiment_id = _safe_id(
                        pending["experiment_id"],
                        "experiment_id",
                    )
                    condition_id = _safe_id(
                        pending["condition_id"],
                        "condition_id",
                    )
                    repeat_id = pending.get("repeat_id")
                    if (
                        run_root.parents[2].name != experiment_id
                        or run_root.parent.name != condition_id
                        or run_root.name != str(repeat_id)
                    ):
                        raise ValueError("checkpoint PENDING run identity mismatch")
                    target_run_manifest = _read_json(
                        target_root / "run_manifest.json"
                    )
                    if (
                        target_run_manifest.get("experiment_id") != experiment_id
                        or target_run_manifest.get("condition_id") != condition_id
                        or target_run_manifest.get("repeat_id") != repeat_id
                    ):
                        raise ValueError(
                            "checkpoint PENDING target run identity mismatch"
                        )
                    target_tasks = _read_jsonl(
                        target_root / "per_task_results.jsonl"
                    )
                    if pending.get("task_id") not in {
                        task.get("task_id") for task in target_tasks
                    }:
                        raise ValueError(
                            "checkpoint PENDING task identity is missing"
                        )

                    current_path = run_root / "CURRENT.json"
                    current = (
                        _read_json(current_path)
                        if current_path.is_file()
                        else None
                    )
                    if (
                        current is not None
                        and current.get("generation_id") == target_generation_id
                    ):
                        if current.get("generation_manifest_digest") != _digest_json(
                            target_manifest
                        ):
                            raise ValueError(
                                "checkpoint PENDING committed target conflicts"
                            )
                    else:
                        self._validate_pending_prior_current(
                            run_root=run_root,
                            pending=pending,
                        )
                        _atomic_write_json(
                            current_path,
                            {
                                "schema_version": (
                                    "tokenshare.paper_checkpoint_current.v1"
                                ),
                                "generation_id": target_generation_id,
                                "generation_manifest_digest": _digest_json(
                                    target_manifest
                                ),
                            },
                        )

                    _atomic_write_json(
                        run_root / "condition_manifest.json",
                        self._condition_manifest_body(
                            run_root=run_root,
                            generation_root=target_root,
                        ),
                    )
                    for candidate in (run_root / ".generations").iterdir():
                        if candidate.is_dir() and candidate != target_root:
                            shutil.rmtree(candidate)
                    repaired_pending_paths.append(pending_path)
                    repaired.append(
                        {
                            "experiment_id": experiment_id,
                            "condition_id": condition_id,
                            "repeat_id": repeat_id,
                            "generation_id": target_generation_id,
                        }
                    )
                self._refresh_evidence_manifest()
                self._validate_evidence_manifest()
                for pending_path in repaired_pending_paths:
                    pending_path.unlink()
                for cleanup_root, generation_ids in cleanup_generations:
                    for generation_id in generation_ids:
                        candidate = cleanup_root / ".generations" / generation_id
                        if candidate.is_dir():
                            shutil.rmtree(candidate)
        self._validate_evidence_manifest()
        return {
            "schema_version": "tokenshare.paper_checkpoint_repair.v1",
            "repaired_publications": repaired,
        }

    def _repair_v3_pending_publication(
        self,
        *,
        run_root: Path,
        pending: Mapping[str, Any],
    ) -> dict[str, Any]:
        """按 exact prior/target 二态幂等补完 v3 root delta intent。"""

        _require_exact_keys(pending, _PENDING_V2_KEYS, "checkpoint PENDING v2")
        publication_kind = pending.get("publication_kind")
        if publication_kind not in {
            "root_delta",
            "condition_tail_events",
            "terminal_snapshot",
        }:
            raise ValueError("unsupported checkpoint PENDING v2 publication kind")
        experiment_id = _safe_id(pending.get("experiment_id"), "experiment_id")
        condition_id = _safe_id(pending.get("condition_id"), "condition_id")
        task_id = (
            _safe_id(pending.get("task_id"), "task_id")
            if publication_kind == "root_delta"
            else None
        )
        repeat_id = pending.get("repeat_id")
        selection_ordinal = pending.get("selection_ordinal")
        anchor_task_id = (
            None
            if publication_kind == "terminal_snapshot"
            else _safe_id(
                pending.get("anchor_task_id"),
                "anchor_task_id",
            )
        )
        if publication_kind == "root_delta":
            if (
                isinstance(selection_ordinal, bool)
                or not isinstance(selection_ordinal, int)
                or selection_ordinal < 0
            ):
                raise ValueError("checkpoint PENDING selection ordinal is invalid")
            if anchor_task_id != task_id:
                raise ValueError("checkpoint PENDING root anchor conflicts with task")
            if pending.get("condition_events_digest") is not None:
                raise ValueError("checkpoint root PENDING has condition events")
        elif publication_kind == "condition_tail_events" and (
            pending.get("task_id") is not None
            or selection_ordinal is not None
            or not isinstance(pending.get("condition_events_digest"), str)
        ):
            raise ValueError("checkpoint tail PENDING fields are invalid")
        if publication_kind == "terminal_snapshot":
            if (
                pending.get("task_id") is not None
                or selection_ordinal is not None
                or pending.get("anchor_task_id") is not None
                or pending.get("condition_events_digest") is not None
                or pending.get("compacted_prior_head_generation_id")
                != pending.get("expected_prior_generation_id")
                or pending.get(
                    "compacted_prior_head_generation_manifest_digest"
                )
                != pending.get("expected_prior_generation_manifest_digest")
                or not isinstance(pending.get("compacted_chain_digest"), str)
            ):
                raise ValueError("checkpoint snapshot PENDING fields are invalid")
        elif (
            pending.get("compacted_prior_head_generation_id") is not None
            or pending.get(
                "compacted_prior_head_generation_manifest_digest"
            )
            is not None
            or pending.get("compacted_chain_digest") is not None
        ):
            raise ValueError("checkpoint delta PENDING has compacted bindings")
        if (
            run_root.parents[2].name != experiment_id
            or run_root.parent.name != condition_id
            or run_root.name != str(repeat_id)
        ):
            raise ValueError("checkpoint PENDING run identity mismatch")
        target_generation_id = _safe_id(
            pending.get("target_generation_id"),
            "target_generation_id",
        )
        target_root = run_root / ".generations" / target_generation_id
        target_manifest = _read_json(target_root / "generation_manifest.json")
        target_descriptor = validate_v3_generation_manifest(
            target_root,
            target_manifest,
            expected_experiment_id=experiment_id,
            expected_condition_id=condition_id,
            expected_repeat_id=repeat_id,
        )
        if publication_kind == "terminal_snapshot":
            if (
                target_descriptor.generation_kind != "snapshot"
                or target_descriptor.compacted_from_head_generation_id
                != pending.get("compacted_prior_head_generation_id")
                or target_descriptor.compacted_from_head_generation_manifest_digest
                != pending.get(
                    "compacted_prior_head_generation_manifest_digest"
                )
                or target_descriptor.compacted_chain_digest
                != pending.get("compacted_chain_digest")
            ):
                raise ValueError(
                    "checkpoint PENDING target snapshot identity mismatch"
                )
        elif (
            target_descriptor.generation_kind != "delta"
            or target_descriptor.delta_role
            != (
                "root_outcome"
                if publication_kind == "root_delta"
                else "condition_tail_events"
            )
            or target_descriptor.anchor_task_id != anchor_task_id
            or target_descriptor.selection_ordinal != selection_ordinal
        ):
            raise ValueError("checkpoint PENDING target delta identity mismatch")
        target_digest = _digest_json(target_manifest)
        if pending.get("target_generation_manifest_digest") != target_digest:
            raise ValueError("checkpoint PENDING target digest mismatch")
        if publication_kind != "terminal_snapshot" and (
            target_descriptor.parent_generation_id
            != pending.get("expected_prior_generation_id")
            or target_descriptor.parent_generation_manifest_digest
            != pending.get("expected_prior_generation_manifest_digest")
        ):
            raise ValueError("checkpoint PENDING parent generation conflict")
        if publication_kind == "condition_tail_events":
            target_events = _read_jsonl(
                target_root / "events" / "event_log.jsonl"
            )
            if pending.get("condition_events_digest") != _digest_json(target_events):
                raise ValueError("checkpoint PENDING tail events digest mismatch")

        current_path = run_root / "CURRENT.json"
        current = _read_json(current_path) if current_path.is_file() else None
        if current is not None and current.get("generation_id") == target_generation_id:
            if current.get("generation_manifest_digest") != target_digest:
                raise ValueError("checkpoint PENDING committed target conflicts")
        else:
            self._validate_pending_prior_current(
                run_root=run_root,
                pending=pending,
            )
            _atomic_write_json(
                current_path,
                {
                    "schema_version": "tokenshare.paper_checkpoint_current.v1",
                    "generation_id": target_generation_id,
                    "generation_manifest_digest": target_digest,
                },
            )

        condition_path = run_root / "condition_manifest.json"
        existing_condition = (
            _read_json(condition_path) if condition_path.is_file() else None
        )
        target_manifest_path = (
            target_root / "generation_manifest.json"
        ).relative_to(self.output_root).as_posix()
        condition_is_target = (
            isinstance(existing_condition, Mapping)
            and isinstance(existing_condition.get("generation_manifest_ref"), Mapping)
            and existing_condition["generation_manifest_ref"].get("path")
            == target_manifest_path
        )
        if condition_is_target:
            _require_exact_keys(
                existing_condition,
                _CONDITION_MANIFEST_V2_KEYS,
                "condition manifest v2",
            )
        else:
            prior_condition = existing_condition
            if publication_kind == "root_delta":
                tasks = _read_jsonl(target_root / "per_task_results.jsonl")
                artifacts = _read_jsonl(
                    target_root / "artifacts" / "artifact_index.jsonl"
                )
                if len(tasks) != 1 or tasks[0].get("task_id") != task_id:
                    raise ValueError(
                        "checkpoint PENDING target task identity is missing"
                    )
                condition_body = self._condition_manifest_v2_body(
                    run_root=run_root,
                    generation_root=target_root,
                    task_body=tasks[0],
                    artifact_bodies=artifacts,
                    prior_condition=prior_condition,
                )
            elif publication_kind == "condition_tail_events":
                if not isinstance(prior_condition, Mapping):
                    raise ValueError("checkpoint PENDING prior condition is missing")
                condition_body = self._condition_manifest_v2_tail_body(
                    run_root=run_root,
                    generation_root=target_root,
                    prior_condition=prior_condition,
                )
            else:
                if not isinstance(prior_condition, Mapping):
                    raise ValueError("checkpoint PENDING prior condition is missing")
                condition_body = self._condition_manifest_v2_snapshot_body(
                    run_root=run_root,
                    generation_root=target_root,
                    prior_condition=prior_condition,
                    logical_records_digest=_logical_records_digest(
                        target_manifest
                    ),
                )
            _atomic_write_json(
                condition_path,
                condition_body,
            )
        effective_condition = (
            existing_condition if condition_is_target else condition_body
        )
        assert isinstance(effective_condition, Mapping)
        self._refresh_evidence_manifest(run_root=run_root)
        result = {
            "experiment_id": experiment_id,
            "condition_id": condition_id,
            "repeat_id": repeat_id,
            "generation_id": target_generation_id,
        }
        if publication_kind == "terminal_snapshot":
            result["cleanup_generation_ids"] = [
                descriptor.generation_id
                for descriptor in validate_v3_delta_chain(
                    run_root,
                    validate_v3_generation_manifest(
                        run_root
                        / ".generations"
                        / str(pending["expected_prior_generation_id"])
                    ),
                    expected_root_count=int(
                        effective_condition["expected_root_count"]
                    ),
                )
            ]
        return result

    def repair_stale_compatibility_manifest(self) -> dict[str, Any] | None:
        """仅修复与 canonical evidence 逐字节一致的未索引兼容镜像。"""

        try:
            self._validate_evidence_manifest()
            return None
        except ValueError as error:
            if str(error) != "evidence manifest does not exactly index stored files":
                raise

        manifest_path = self.output_root / "evidence_manifest.json"
        manifest = _read_json(manifest_path)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen = {
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and Path(str(entry["path"])).name != _LOCK_FILE_NAME
            and not _is_noncurrent_generation_path(
                self.output_root,
                str(entry["path"]),
            )
        }
        actual = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if seen.difference(actual):
            raise ValueError("stale evidence manifest references missing files")
        unindexed = sorted(actual.difference(seen))
        if not unindexed:
            raise ValueError("stale evidence manifest has no repairable files")

        mirror_pairs = []
        for relative_path in unindexed:
            parts = Path(relative_path).parts
            if not parts or parts[0] in {"experiments", "repairs"}:
                raise ValueError(
                    "unindexed evidence is not a compatibility projection"
                )
            compatibility_path = self.output_root / relative_path
            canonical_path = self.output_root / "experiments" / relative_path
            if (
                not canonical_path.is_file()
                or compatibility_path.read_bytes() != canonical_path.read_bytes()
            ):
                raise ValueError(
                    "unindexed compatibility evidence differs from canonical evidence"
                )
            mirror_pairs.append(
                {
                    "compatibility": _file_evidence(
                        self.output_root,
                        compatibility_path,
                    ),
                    "canonical": _file_evidence(
                        self.output_root,
                        canonical_path,
                    ),
                }
            )

        original_bytes = manifest_path.read_bytes()
        original_digest = _digest_bytes(original_bytes)
        repair_suffix = original_digest.removeprefix("sha256:")[:16]
        repair_root = self.output_root / "repairs"
        original_copy = (
            repair_root
            / f"pre_resume_evidence_manifest_{repair_suffix}.json"
        )
        if original_copy.is_file():
            if original_copy.read_bytes() != original_bytes:
                raise ValueError("preserved evidence manifest identity mismatch")
        else:
            original_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest_path, original_copy)
        repair_body = {
            "schema_version": "tokenshare.paper_compatibility_manifest_repair.v1",
            "repair_kind": "stale_compatibility_projection_index",
            "original_manifest_ref": _file_evidence(
                self.output_root,
                original_copy,
            ),
            "unindexed_compatibility_file_count": len(mirror_pairs),
            "verified_mirror_pairs": mirror_pairs,
            "paper_eligible": False,
        }
        repair_path = (
            repair_root
            / f"compatibility_manifest_repair_{repair_suffix}.json"
        )
        if repair_path.is_file():
            if _read_json(repair_path) != repair_body:
                raise ValueError("compatibility manifest repair identity mismatch")
        else:
            _atomic_write_json(repair_path, repair_body)
        self._refresh_evidence_manifest()
        self._validate_evidence_manifest()
        return repair_body

    def archive_uncheckpointed_adapter_runs(self) -> dict[str, Any] | None:
        """归档设施异常留下、尚未进入 canonical checkpoint 的 adapter 现场。"""

        suite = _read_json(self.output_root / "suite_manifest.json")
        identity = suite.get("suite_identity")
        dispatch_component = (
            identity.get("dispatch") if isinstance(identity, dict) else None
        )
        dispatch = (
            dispatch_component.get("body")
            if isinstance(dispatch_component, dict)
            else None
        )
        if not isinstance(dispatch, dict):
            raise ValueError("suite identity dispatch evidence is missing")

        orphan_roots: list[Path] = []
        for experiment_id, conditions in _conditions_by_experiment(dispatch).items():
            for condition in conditions:
                condition_id = str(condition["condition_id"])
                repeat_id = _safe_id(str(condition.get("repeat_id", 0)), "repeat_id")
                compatibility_root = (
                    self.output_root
                    / experiment_id
                    / "runs"
                    / condition_id
                )
                canonical_current = (
                    self.output_root
                    / "experiments"
                    / experiment_id
                    / "runs"
                    / condition_id
                    / repeat_id
                    / "CURRENT.json"
                )
                if (
                    compatibility_root.is_dir()
                    and not canonical_current.is_file()
                    and any(path.is_file() for path in compatibility_root.rglob("*"))
                ):
                    orphan_roots.append(compatibility_root)
        if not orphan_roots:
            return None

        manifest_path = self.output_root / "evidence_manifest.json"
        manifest = _read_json(manifest_path)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen = {
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and Path(str(entry["path"])).name != _LOCK_FILE_NAME
            and not _is_noncurrent_generation_path(
                self.output_root,
                str(entry["path"]),
            )
        }
        actual = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if seen.difference(actual):
            raise ValueError("stale evidence manifest references missing files")
        orphan_paths = {
            path.relative_to(self.output_root).as_posix()
            for root in orphan_roots
            for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        }
        if actual.difference(seen) != orphan_paths:
            raise ValueError(
                "unindexed evidence is not exactly uncheckpointed adapter evidence"
            )

        inventory = [
            {
                "source_path": relative_path,
                "content_sha256": _digest_bytes(
                    (self.output_root / relative_path).read_bytes()
                ),
                "size": (self.output_root / relative_path).stat().st_size,
            }
            for relative_path in sorted(orphan_paths)
        ]
        repair_suffix = _digest_json(inventory).removeprefix("sha256:")[:16]
        repair_id = f"facility_orphan_{repair_suffix}"
        repair_root = (
            self.output_root / "repairs" / "facility_orphans" / repair_id
        )
        if repair_root.exists():
            raise ValueError("facility orphan archive identity already exists")
        original_copy = repair_root / "pre_archive_evidence_manifest.json"
        original_copy.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(manifest_path, original_copy)

        archived_files = []
        for orphan_root in sorted(orphan_roots):
            source_relative = orphan_root.relative_to(self.output_root)
            destination = repair_root / "original" / source_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(orphan_root), str(destination))
            for archived_path in sorted(destination.rglob("*")):
                if not archived_path.is_file() or archived_path.name.endswith(".tmp"):
                    continue
                original_relative = (
                    source_relative / archived_path.relative_to(destination)
                ).as_posix()
                archived_files.append(
                    {
                        "source_path": original_relative,
                        "archived_ref": _file_evidence(
                            self.output_root,
                            archived_path,
                        ),
                    }
                )

        repair_body = {
            "schema_version": "tokenshare.paper_facility_orphan_archive.v1",
            "repair_id": repair_id,
            "repair_kind": "uncheckpointed_adapter_evidence_archive",
            "original_manifest_ref": _file_evidence(
                self.output_root,
                original_copy,
            ),
            "archived_condition_count": len(orphan_roots),
            "archived_files": archived_files,
            "paper_eligible": False,
        }
        repair_body["repair_digest"] = _digest_json(repair_body)
        _atomic_write_json(repair_root / "repair.json", repair_body)
        self._refresh_evidence_manifest()
        self._validate_evidence_manifest()
        return repair_body

    def _validate_evidence_manifest(self) -> None:
        required = (
            "suite_manifest.json",
            "run_budget.json",
            "input_catalog_manifest.json",
            "paper_dispatch_plans.json",
            "conditions.jsonl",
            "evidence_manifest.json",
        )
        for relative_path in required:
            if not (self.output_root / relative_path).is_file():
                raise ValueError(f"required evidence file is missing: {relative_path}")
        manifest = _read_json(self.output_root / "evidence_manifest.json")
        if manifest.get("schema_version") == (
            "tokenshare.paper_evidence_manifest.v2"
        ):
            self._validate_evidence_manifest_v2(manifest)
            return
        if manifest.get("schema_version") != (
            "tokenshare.paper_evidence_manifest.v1"
        ):
            raise ValueError("evidence manifest schema version mismatch")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("evidence manifest file index is missing")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError("invalid evidence manifest entry")
            relative_path = entry["path"]
            if (
                Path(relative_path).name in {
                    _LOCK_FILE_NAME,
                    _PENDING_FILE_NAME,
                }
                or _is_noncurrent_generation_path(
                    self.output_root,
                    relative_path,
                )
                or _is_adapter_compatibility_path(
                    self.output_root,
                    relative_path,
                )
            ):
                continue
            if relative_path in seen:
                raise ValueError("duplicate evidence manifest path")
            seen.add(relative_path)
            path = (self.output_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, self.output_root) or not path.is_file():
                raise ValueError(f"evidence file is missing: {relative_path}")
            if _file_evidence(self.output_root, path) != entry:
                raise ValueError(f"evidence file integrity mismatch: {relative_path}")
        if not set(required[:-1]) <= seen:
            raise ValueError("required evidence files are absent from manifest")
        actual_paths = {
            path.relative_to(self.output_root).as_posix()
            for path in self.output_root.rglob("*")
            if path.is_file()
            and path.name != "evidence_manifest.json"
            and path.name != _LOCK_FILE_NAME
            and path.name != _PENDING_FILE_NAME
            and not path.name.endswith(".tmp")
            and not _is_noncurrent_generation_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
            and not _is_adapter_compatibility_path(
                self.output_root,
                path.relative_to(self.output_root).as_posix(),
            )
        }
        if actual_paths != seen:
            raise ValueError("evidence manifest does not exactly index stored files")

    def _validate_evidence_manifest_v2(
        self,
        manifest: Mapping[str, Any],
    ) -> None:
        """冷路径递归验证static refs与每个v3 condition可达链。"""

        _require_exact_keys(
            manifest,
            {"schema_version", "files", "conditions"},
            "evidence manifest v2",
        )
        static_entries = manifest.get("files")
        condition_entries = manifest.get("conditions")
        if not isinstance(static_entries, list) or not isinstance(
            condition_entries, list
        ):
            raise ValueError("evidence manifest v2 inventories must be lists")
        static_seen: set[str] = set()
        for entry in static_entries:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("path"), str
            ):
                raise ValueError("invalid evidence manifest v2 static entry")
            relative_path = str(entry["path"])
            if relative_path in static_seen:
                raise ValueError("duplicate evidence manifest v2 static path")
            static_seen.add(relative_path)
            path = (self.output_root / relative_path).resolve(strict=False)
            if not _is_relative_to(path, self.output_root) or not path.is_file():
                raise ValueError("evidence manifest v2 static file is missing")
            if _file_evidence(self.output_root, path) != dict(entry):
                raise ValueError("evidence manifest v2 static integrity mismatch")
        actual_static = {
            path.relative_to(self.output_root).as_posix()
            for path in self._v2_static_paths()
        }
        if static_seen != actual_static:
            raise ValueError("evidence manifest v2 static inventory mismatch")

        condition_seen: set[tuple[str, str, str]] = set()
        for entry in condition_entries:
            if not isinstance(entry, Mapping):
                raise ValueError("invalid evidence manifest v2 condition entry")
            _require_exact_keys(
                entry,
                {
                    "experiment_id",
                    "condition_id",
                    "repeat_id",
                    "condition_manifest_ref",
                    "reachable_size_bytes",
                },
                "evidence manifest v2 condition entry",
            )
            experiment_id = _safe_id(entry.get("experiment_id"), "experiment_id")
            condition_id = _safe_id(entry.get("condition_id"), "condition_id")
            repeat_id = entry.get("repeat_id")
            repeat_name = _safe_id(str(repeat_id), "repeat_id")
            key = (experiment_id, condition_id, repeat_name)
            if key in condition_seen:
                raise ValueError("duplicate evidence manifest v2 condition entry")
            condition_seen.add(key)
            run_root = (
                self.output_root
                / "experiments"
                / experiment_id
                / "runs"
                / condition_id
                / repeat_name
            )
            condition_path = run_root / "condition_manifest.json"
            expected_condition_ref = _file_evidence(
                self.output_root,
                condition_path,
            )
            if entry.get("condition_manifest_ref") != expected_condition_ref:
                raise ValueError("condition inventory manifest ref mismatch")
            condition = _read_json(condition_path)
            _require_exact_keys(
                condition,
                _CONDITION_MANIFEST_V2_KEYS,
                "condition manifest v2",
            )
            if (
                condition.get("experiment_id") != experiment_id
                or condition.get("condition_id") != condition_id
                or str(condition.get("repeat_id")) != repeat_name
            ):
                raise ValueError("condition manifest v2 identity mismatch")
            current_path = run_root / "CURRENT.json"
            current = _read_json(current_path)
            _require_exact_keys(current, _CURRENT_KEYS, "checkpoint CURRENT")
            if condition.get("current_ref") != _file_evidence(
                self.output_root,
                current_path,
            ):
                raise ValueError("condition manifest v2 CURRENT ref mismatch")
            generation_id = _safe_id(current.get("generation_id"), "generation_id")
            generation_root = run_root / ".generations" / generation_id
            generation_manifest = _read_json(
                generation_root / "generation_manifest.json"
            )
            descriptor = validate_v3_generation_manifest(
                generation_root,
                generation_manifest,
                expected_experiment_id=experiment_id,
                expected_condition_id=condition_id,
                expected_repeat_id=repeat_id,
            )
            if current.get("generation_manifest_digest") != _digest_json(
                generation_manifest
            ):
                raise ValueError("condition CURRENT generation digest mismatch")
            if condition.get("generation_manifest_ref") != _file_evidence(
                self.output_root,
                generation_root / "generation_manifest.json",
            ):
                raise ValueError("condition generation manifest ref mismatch")
            if condition.get("run_manifest_ref") != _file_evidence(
                self.output_root,
                generation_root / "run_manifest.json",
            ):
                raise ValueError("condition run manifest ref mismatch")
            expected_count = int(condition["expected_root_count"])
            frozen_expected_count = self._expected_task_counts.get(
                (experiment_id, condition_id)
            )
            if (
                frozen_expected_count is not None
                and expected_count != frozen_expected_count
            ):
                raise ValueError("condition manifest evidence mismatch")
            if descriptor.generation_kind == "snapshot":
                if descriptor.compacted_generation_count != expected_count + 1:
                    raise ValueError(
                        "terminal snapshot compacted denominator mismatch"
                    )
                run_manifest = _read_json(
                    generation_root / "run_manifest.json"
                )
                tasks = _read_jsonl(
                    generation_root / "per_task_results.jsonl"
                )
                artifacts = _read_jsonl(
                    generation_root / "artifacts" / "artifact_index.jsonl"
                )
                payload_paths: set[Path] = set()
                for artifact in artifacts:
                    task_id = _safe_id(artifact.get("task_id"), "task_id")
                    self._validate_artifact_refs(
                        [artifact],
                        experiment_id=experiment_id,
                        condition_id=condition_id,
                        repeat_id=repeat_id,
                        task_id=task_id,
                        run_root=run_root,
                    )
                    payload_paths.add(
                        (self.output_root / str(artifact["path"])).resolve(
                            strict=False
                        )
                    )
                reachable_size = (
                    (generation_root / "generation_manifest.json").stat().st_size
                    + sum(
                        int(file_entry["size"])
                        for file_entry in generation_manifest["files"]
                        if isinstance(file_entry, Mapping)
                    )
                    + sum(path.stat().st_size for path in payload_paths)
                )
                logical_records_digest = _digest_json(
                    [
                        {
                            "path": file_entry["path"],
                            "record_count": file_entry["record_count"],
                            "records_digest": file_entry["records_digest"],
                        }
                        for file_entry in generation_manifest["files"]
                        if isinstance(file_entry, Mapping)
                    ]
                )
                terminal_count = sum(
                    _is_terminal_checkpoint(task) for task in tasks
                )
                expected_fields = {
                    "head_generation_kind": "snapshot",
                    "chain_generation_count": 1,
                    "root_delta_count": expected_count,
                    "condition_event_delta_count": 1,
                    "logical_task_count": expected_count,
                    "observed_root_count": expected_count,
                    "terminal_root_count": expected_count,
                    "reachable_size_bytes": reachable_size,
                    "commit_chain_digest": descriptor.compacted_chain_digest,
                    "logical_records_digest": logical_records_digest,
                    "terminal": True,
                    "status": run_manifest["status"],
                }
                if len(tasks) != expected_count or terminal_count != expected_count:
                    raise ValueError("terminal snapshot task denominator mismatch")
                for field_name, expected_value in expected_fields.items():
                    if condition.get(field_name) != expected_value:
                        raise ValueError(
                            f"condition manifest v2 {field_name} mismatch"
                        )
                base = self._condition_manifest_body(
                    run_root=run_root,
                    generation_root=generation_root,
                )
                for field_name in (
                    "experiment_id",
                    "condition_id",
                    "repeat_id",
                    "condition_identity",
                    "expected_root_count",
                    "observed_root_count",
                    "terminal_root_count",
                    "status",
                    "terminal",
                    "current_ref",
                    "generation_manifest_ref",
                    "run_manifest_ref",
                    "paper_eligible",
                    "ineligibility_reasons",
                ):
                    if condition.get(field_name) != base.get(field_name):
                        raise ValueError(
                            f"condition manifest v2 {field_name} mismatch"
                        )
                if entry.get("reachable_size_bytes") != reachable_size:
                    raise ValueError("condition inventory reachable size mismatch")
                continue
            chain = validate_v3_delta_chain(
                run_root,
                descriptor,
                expected_root_count=expected_count,
            )
            root_deltas = [
                item for item in chain if item.delta_role == "root_outcome"
            ]
            tail_deltas = [
                item
                for item in chain
                if item.delta_role == "condition_tail_events"
            ]
            terminal_count = 0
            reachable_size = 0
            payload_paths: set[Path] = set()
            commit_digest: str | None = None
            for generation in chain:
                body = _read_json(
                    generation.generation_root / "generation_manifest.json"
                )
                reachable_size += (
                    generation.generation_root / "generation_manifest.json"
                ).stat().st_size
                reachable_size += sum(
                    int(file_entry["size"])
                    for file_entry in body["files"]
                    if isinstance(file_entry, Mapping)
                )
                commit_digest = _digest_json(
                    {
                        "prior_commit_chain_digest": commit_digest,
                        "generation_id": generation.generation_id,
                        "generation_manifest_digest": generation.manifest_digest,
                    }
                )
                if generation.delta_role != "root_outcome":
                    continue
                tasks = _read_jsonl(
                    generation.generation_root / "per_task_results.jsonl"
                )
                terminal_count += sum(
                    _is_terminal_checkpoint(task) for task in tasks
                )
                artifacts = _read_jsonl(
                    generation.generation_root
                    / "artifacts"
                    / "artifact_index.jsonl"
                )
                for artifact in artifacts:
                    task_id = _safe_id(artifact.get("task_id"), "task_id")
                    self._validate_artifact_refs(
                        [artifact],
                        experiment_id=experiment_id,
                        condition_id=condition_id,
                        repeat_id=repeat_id,
                        task_id=task_id,
                        run_root=run_root,
                    )
                    payload_paths.add(
                        (self.output_root / str(artifact["path"])).resolve(
                            strict=False
                        )
                    )
            reachable_size += sum(path.stat().st_size for path in payload_paths)
            expected_fields = {
                "head_generation_kind": "delta",
                "chain_generation_count": len(chain),
                "root_delta_count": len(root_deltas),
                "condition_event_delta_count": len(tail_deltas),
                "logical_task_count": len(root_deltas),
                "observed_root_count": len(root_deltas),
                "terminal_root_count": terminal_count,
                "reachable_size_bytes": reachable_size,
                "commit_chain_digest": commit_digest,
                "logical_records_digest": None,
                "terminal": False,
                "status": "running",
            }
            for field_name, expected_value in expected_fields.items():
                if condition.get(field_name) != expected_value:
                    raise ValueError(
                        f"condition manifest v2 {field_name} mismatch"
                    )
            if entry.get("reachable_size_bytes") != reachable_size:
                raise ValueError("condition inventory reachable size mismatch")

    def _validate_run(
        self,
        run_root: Path,
        *,
        experiment_id: str,
    ) -> list[dict[str, Any]]:
        generation_root = self._current_generation_root(run_root, required=True)
        assert generation_root is not None
        generation_manifest = _read_json(
            generation_root / "generation_manifest.json"
        )
        is_v3 = generation_manifest.get("schema_version") == V3_GENERATION_SCHEMA
        if is_v3:
            condition_manifest = _read_json(run_root / "condition_manifest.json")
            _require_exact_keys(
                condition_manifest,
                _CONDITION_MANIFEST_V2_KEYS,
                "condition manifest v2",
            )
            expected_count = int(condition_manifest["expected_root_count"])
            head = validate_v3_generation_manifest(
                generation_root,
                generation_manifest,
                expected_experiment_id=experiment_id,
                expected_condition_id=run_root.parent.name,
            )
            if head.generation_kind == "snapshot":
                run_manifest = _read_json(
                    generation_root / "run_manifest.json"
                )
                tasks = _read_jsonl(
                    generation_root / "per_task_results.jsonl"
                )
                attempts = _read_jsonl(
                    generation_root / "per_attempt_results.jsonl"
                )
                faults = _read_jsonl(
                    generation_root / "fault_injections.jsonl"
                )
                events = _read_jsonl(
                    generation_root / "events" / "event_log.jsonl"
                )
                artifacts = _read_jsonl(
                    generation_root / "artifacts" / "artifact_index.jsonl"
                )
            else:
                chain = validate_v3_delta_chain(
                    run_root,
                    head,
                    expected_root_count=expected_count,
                )
                roots = sorted(
                    (
                        item for item in chain if item.delta_role == "root_outcome"
                    ),
                    key=lambda item: int(item.selection_ordinal),
                )
                tails = [
                    item
                    for item in chain
                    if item.delta_role == "condition_tail_events"
                ]
                tasks = [
                    record
                    for item in roots
                    for record in _read_jsonl(
                        item.generation_root / "per_task_results.jsonl"
                    )
                ]
                attempts = [
                    record
                    for item in roots
                    for record in _read_jsonl(
                        item.generation_root / "per_attempt_results.jsonl"
                    )
                ]
                faults = [
                    record
                    for item in roots
                    for record in _read_jsonl(
                        item.generation_root / "fault_injections.jsonl"
                    )
                ]
                artifacts = [
                    record
                    for item in roots
                    for record in _read_jsonl(
                        item.generation_root
                        / "artifacts"
                        / "artifact_index.jsonl"
                    )
                ]
                events = [
                    record
                    for item in [*roots, *tails]
                    for record in _read_jsonl(
                        item.generation_root / "events" / "event_log.jsonl"
                    )
                ]
                repeat_id = head.repeat_id
                run_manifest = {
                    "schema_version": "tokenshare.paper_run_evidence.v1",
                    "generation_id": generation_root.name,
                    "experiment_id": experiment_id,
                    "condition_id": run_root.parent.name,
                    "repeat_id": repeat_id,
                    "task_ids": [str(item["task_id"]) for item in tasks],
                    "completed_task_ids": [
                        str(item["task_id"])
                        for item in tasks
                        if _is_success(item)
                    ],
                    "status": _derived_run_status(
                        tasks,
                        expected_task_count=expected_count,
                    ),
                }
        else:
            run_manifest = _read_json(generation_root / "run_manifest.json")
            tasks = _read_jsonl(generation_root / "per_task_results.jsonl")
            attempts = _read_jsonl(generation_root / "per_attempt_results.jsonl")
            events = _read_jsonl(generation_root / "events" / "event_log.jsonl")
            faults = _read_jsonl(generation_root / "fault_injections.jsonl")
            artifacts = _read_jsonl(
                generation_root / "artifacts" / "artifact_index.jsonl"
            )
        _require_exact_keys(run_manifest, _RUN_MANIFEST_KEYS, "run manifest")
        if (
            run_manifest.get("schema_version")
            != "tokenshare.paper_run_evidence.v1"
        ):
            raise ValueError("run manifest schema version mismatch")
        for field_name in ("generation_id", "experiment_id", "condition_id", "status"):
            _require_string(run_manifest, field_name, "run manifest")
        repeat_value = run_manifest.get("repeat_id")
        if isinstance(repeat_value, bool) or not isinstance(repeat_value, (int, str)):
            raise ValueError("run manifest repeat_id type is invalid")
        for field_name in ("task_ids", "completed_task_ids"):
            value = run_manifest.get(field_name)
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"run manifest {field_name} type is invalid")
        condition_id = run_root.parent.name
        repeat_id = run_manifest.get("repeat_id")
        if run_manifest.get("experiment_id") != experiment_id:
            raise ValueError("run evidence crosses experiment identity")
        if run_manifest.get("condition_id") != condition_id:
            raise ValueError("run evidence crosses condition identity")
        if (experiment_id, condition_id) not in self._conditions:
            raise ValueError("run condition does not belong to experiment")
        if str(repeat_id) != run_root.name:
            raise ValueError("run manifest repeat identity mismatch")
        if run_manifest.get("generation_id") != generation_root.name:
            raise ValueError("run manifest generation identity mismatch")
        if tasks and not attempts:
            raise ValueError("attempt evidence is missing")
        if tasks and not events:
            raise ValueError("event evidence is missing")
        task_ids = {str(item.get("task_id")) for item in tasks}
        if len(task_ids) != len(tasks) or "None" in task_ids:
            raise ValueError("task evidence identity is invalid")
        for label, records in (
            ("task", tasks),
            ("attempt", attempts),
            ("event", events),
            ("fault", faults),
        ):
            for record in records:
                if label == "event" and _is_protocol_ledger_event(record):
                    continue
                task_id = _safe_id(record.get("task_id"), "task_id")
                _validate_context(
                    record,
                    experiment_id=experiment_id,
                    condition_id=condition_id,
                    repeat_id=repeat_id,
                    task_id=task_id,
                    label=label,
                )
                if label in {"attempt", "event"} and str(task_id) not in task_ids:
                    raise ValueError(f"{label} evidence references an unknown task")
                if label == "fault" and str(task_id) not in task_ids:
                    raise ValueError("fault evidence references an unknown task")
        _validate_stored_protocol_event_ledgers(tasks=tasks, events=events)
        expected_task_ids = [str(item["task_id"]) for item in tasks]
        expected_completed_ids = [
            str(item["task_id"]) for item in tasks if _is_success(item)
        ]
        expected_status = _derived_run_status(
            tasks,
            expected_task_count=self._expected_task_counts.get(
                (experiment_id, condition_id),
                len(tasks),
            ),
        )
        if run_manifest.get("task_ids") != expected_task_ids:
            raise ValueError("run manifest task_ids do not match task records")
        if run_manifest.get("completed_task_ids") != expected_completed_ids:
            raise ValueError(
                "run manifest completed_task_ids do not match task records"
            )
        if run_manifest.get("status") != expected_status:
            raise ValueError("run manifest status does not match task records")
        artifact_task_ids = {str(item.get("task_id")) for item in artifacts}
        for task in tasks:
            if _is_success(task) and str(task["task_id"]) not in artifact_task_ids:
                raise ValueError("completed task artifact evidence is missing")
        for artifact in artifacts:
            artifact_task_id = _safe_id(artifact.get("task_id"), "task_id")
            if artifact_task_id not in task_ids:
                raise ValueError("artifact evidence references an unknown task")
            self._validate_artifact_refs(
                [artifact],
                experiment_id=experiment_id,
                condition_id=condition_id,
                repeat_id=repeat_id,
                task_id=artifact_task_id,
                run_root=run_root,
            )
        self._validate_reachable_artifact_closure(
            records=[*tasks, *attempts, *faults, *events],
            artifacts=artifacts,
        )
        if (
            generation_manifest.get("schema_version")
            == "tokenshare.paper_checkpoint_generation.v2"
        ):
            condition_path = run_root / "condition_manifest.json"
            if not condition_path.is_file():
                raise ValueError("condition manifest is missing")
            condition_manifest = _read_json(condition_path)
            _require_exact_keys(
                condition_manifest,
                _CONDITION_MANIFEST_KEYS,
                "condition manifest",
            )
            expected_condition_manifest = self._condition_manifest_body(
                run_root=run_root,
                generation_root=generation_root,
            )
            if _canonical_bytes(condition_manifest) != _canonical_bytes(
                expected_condition_manifest
            ):
                raise ValueError("condition manifest evidence mismatch")
        return tasks

    def load_logical_run_records(
        self,
        *,
        experiment_id: str,
        condition_id: str,
        repeat_id: int | str,
    ) -> dict[str, Any]:
        """读取已验证 run 的逻辑全量记录，统一覆盖历史 full generation、v3 chain 与 snapshot。"""

        experiment_id = _safe_id(experiment_id, "experiment_id")
        condition_id = _safe_id(condition_id, "condition_id")
        if isinstance(repeat_id, bool) or not isinstance(repeat_id, (int, str)):
            raise ValueError("repeat_id type is invalid")
        run_root = (
            self.output_root
            / "experiments"
            / experiment_id
            / "runs"
            / condition_id
            / str(repeat_id)
        )
        tasks = self._validate_run(run_root, experiment_id=experiment_id)
        generation_root = self._current_generation_root(run_root, required=True)
        assert generation_root is not None
        generation_manifest = _read_json(
            generation_root / "generation_manifest.json"
        )
        is_v3 = generation_manifest.get("schema_version") == V3_GENERATION_SCHEMA
        source_generations: list[Path]
        if not is_v3:
            source_generations = [generation_root]
        else:
            condition_manifest = _read_json(run_root / "condition_manifest.json")
            expected_count = int(condition_manifest["expected_root_count"])
            head = validate_v3_generation_manifest(
                generation_root,
                generation_manifest,
                expected_experiment_id=experiment_id,
                expected_condition_id=condition_id,
                expected_repeat_id=repeat_id,
            )
            if head.generation_kind == "snapshot":
                source_generations = [generation_root]
            else:
                chain = validate_v3_delta_chain(
                    run_root,
                    head,
                    expected_root_count=expected_count,
                )
                roots = sorted(
                    (
                        descriptor
                        for descriptor in chain
                        if descriptor.delta_role == "root_outcome"
                    ),
                    key=lambda descriptor: int(descriptor.selection_ordinal),
                )
                tails = [
                    descriptor
                    for descriptor in chain
                    if descriptor.delta_role == "condition_tail_events"
                ]
                source_generations = [
                    descriptor.generation_root for descriptor in [*roots, *tails]
                ]

        def records(relative_path: str, *, include_tail: bool = False) -> list[dict[str, Any]]:
            sources = source_generations
            if is_v3 and len(source_generations) > 1 and not include_tail:
                sources = [
                    source
                    for source in source_generations
                    if _read_json(source / "generation_manifest.json").get(
                        "delta_role"
                    )
                    == "root_outcome"
                ]
            return [
                record
                for source in sources
                for record in _read_jsonl(source / relative_path)
            ]

        if len(source_generations) == 1:
            tasks = records("per_task_results.jsonl")
        return {
            "tasks": tasks,
            "attempts": records("per_attempt_results.jsonl"),
            "faults": records("fault_injections.jsonl"),
            "events": records("events/event_log.jsonl", include_tail=True),
            "artifacts": records("artifacts/artifact_index.jsonl"),
            "source_generation_roots": tuple(source_generations),
        }


def _frozen_identity_body(
    suite_identity: Mapping[str, Any],
    name: str,
) -> dict[str, Any]:
    component = suite_identity.get(name)
    if not isinstance(component, Mapping) or not isinstance(
        component.get("body"),
        Mapping,
    ):
        raise ValueError(f"source suite identity is missing: {name}")
    body = dict(component["body"])
    if component.get("digest") != _digest_json(body):
        raise ValueError(f"source suite identity digest mismatch: {name}")
    return body


def _generation_record_ref(
    suite_root: Path,
    generation_root: Path,
    relative_path: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "path": (generation_root / relative_path).relative_to(
            suite_root
        ).as_posix(),
        "record_hash": _digest_json(record),
    }


def _conditions_by_experiment(dispatch: Any) -> dict[str, list[dict[str, Any]]]:
    plans = _dispatch_plans(dispatch)
    result: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        experiment_id = plan["experiment_id"]
        if experiment_id in result:
            raise ValueError("duplicate experiment dispatch plan")
        raw_conditions = plan.get("conditions")
        if not isinstance(raw_conditions, list):
            raise ValueError("dispatch plan requires conditions")
        conditions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_condition in raw_conditions:
            condition = _require_object(raw_condition, "condition")
            condition_id = _safe_id(condition.get("condition_id"), "condition_id")
            if condition.get("experiment_id") != experiment_id:
                raise ValueError("condition experiment identity mismatch")
            if condition_id in seen:
                raise ValueError("duplicate condition_id in experiment")
            seen.add(condition_id)
            conditions.append(condition)
        result[experiment_id] = conditions
    return result


def _dispatch_plans(dispatch: Any) -> list[dict[str, Any]]:
    if isinstance(dispatch, list):
        plans = dispatch
    elif isinstance(dispatch, dict):
        plans = dispatch.get("plans", dispatch.get("dispatch_plans"))
    else:
        raise ValueError("dispatch body must contain JSON plans")
    if not isinstance(plans, list):
        raise ValueError("dispatch body requires plans")
    result: list[dict[str, Any]] = []
    for raw_plan in plans:
        plan = _require_object(raw_plan, "dispatch plan")
        experiment_id = _safe_id(plan.get("experiment_id"), "experiment_id")
        plan["experiment_id"] = experiment_id
        result.append(plan)
    return result


def _validate_promotable_plan_only_root(
    root: Path,
    *,
    bodies: Mapping[str, Any],
) -> None:
    entries = tuple(root.iterdir())
    if any(not entry.is_file() for entry in entries):
        raise ValueError("formal evidence output_root contains non-plan entries")
    names = {entry.name for entry in entries}
    required = {
        "lean_3x3_matrix.json",
        "paper_dispatch_plans.json",
        "run_budget.json",
        "suite_manifest.json",
    }
    if not required <= names or not names <= _PLAN_ONLY_ROOT_FILES:
        raise ValueError("formal evidence output_root contains non-plan entries")

    suite = _read_json(root / "suite_manifest.json")
    budget = _read_json(root / "run_budget.json")
    dispatch = _read_json(root / "paper_dispatch_plans.json")
    matrix = _read_json(root / "lean_3x3_matrix.json")
    expected_suite = _require_object(bodies.get("suite"), "suite")
    expected_budget = _require_object(bodies.get("budget"), "budget")
    expected_dispatch = _require_object(bodies.get("dispatch"), "dispatch")
    expected_catalog = _require_object(bodies.get("catalog"), "catalog")

    if suite.get("suite_id") != "paper_v1_plan" or suite.get("status") != "planned":
        raise ValueError("formal evidence output_root is not a plan-only suite")
    expected_experiment_ids = expected_suite.get("experiment_ids")
    if suite.get("experiment_ids") != expected_experiment_ids:
        raise ValueError("plan-only suite experiment identity drift")
    for field_name in (
        "provider_attempt_count",
        "total_tokens",
        "total_cost_estimate",
    ):
        if suite.get(field_name) != 0:
            raise ValueError("plan-only suite contains provider usage")

    expected_budget_digest = expected_budget.get("budget_digest")
    if (
        not isinstance(expected_budget_digest, str)
        or budget.get("budget_digest") != expected_budget_digest
    ):
        raise ValueError("plan-only budget identity drift")
    quota = budget.get("quota_preflight")
    if not isinstance(quota, Mapping) or quota.get("provider_calls_made") != 0:
        raise ValueError("plan-only budget contains provider calls")
    suite_budget = suite.get("budget_ref")
    if (
        not isinstance(suite_budget, Mapping)
        or suite_budget.get("budget_digest") != expected_budget_digest
    ):
        raise ValueError("plan-only suite budget identity drift")

    if dispatch.get("provider_calls_made") != 0:
        raise ValueError("plan-only dispatch contains provider calls")
    if _canonical_bytes(dispatch.get("plans")) != _canonical_bytes(
        expected_dispatch.get("plans")
    ):
        raise ValueError("plan-only dispatch identity drift")
    if matrix.get("provider_calls_made") != 0:
        raise ValueError("plan-only Lean matrix contains provider calls")
    if matrix.get("catalog_digest") != expected_catalog.get("catalog_digest"):
        raise ValueError("plan-only catalog identity drift")

    for optional_name in (
        "model_endpoint_cohort_plan.json",
        "model_policy_plan.json",
    ):
        if optional_name in names:
            optional_body = _read_json(root / optional_name)
            if optional_body.get("provider_calls_made") != 0:
                raise ValueError("plan-only model preflight contains provider calls")


def _validate_context(
    record: Mapping[str, Any],
    *,
    experiment_id: str,
    condition_id: str,
    repeat_id: int | str,
    label: str,
    task_id: str | None = None,
) -> None:
    expected = {
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "repeat_id": repeat_id,
    }
    if task_id is not None:
        expected["task_id"] = task_id
    for field_name, expected_value in expected.items():
        if field_name not in record:
            raise ValueError(f"{label} evidence requires {field_name} identity")
        if record[field_name] != expected_value:
            raise ValueError(f"{label} evidence crosses experiment/run identity")


def _is_protocol_ledger_event(record: Mapping[str, Any]) -> bool:
    return record.get("schema_version") in {"LedgerEvent.v1", "LedgerEvent.v2"}


def _protocol_event_ledger_metadata(
    *,
    case_task_id: str,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _validate_protocol_event_hash_chain(events)
    event_hashes = [str(event["event_hash"]) for event in events]
    protocol_task_ids = list(
        dict.fromkeys(
            str(event["task_id"])
            for event in events
            if isinstance(event.get("task_id"), str) and event.get("task_id")
        )
    )
    return {
        "schema_version": "tokenshare.protocol_event_ledger_mapping.v1",
        "case_task_id": case_task_id,
        "protocol_task_ids": protocol_task_ids,
        "event_count": len(events),
        "event_hashes": event_hashes,
        "first_event_hash": event_hashes[0],
        "last_event_hash": event_hashes[-1],
    }


def _validate_protocol_event_hash_chain(
    events: Sequence[Mapping[str, Any]],
) -> None:
    if not events:
        raise ValueError("protocol event hash chain is empty")
    previous_hash: str | None = None
    for expected_seq, event in enumerate(events, start=1):
        schema_version = event.get("schema_version")
        expected_keys = (
            _LEDGER_EVENT_V2_KEYS
            if schema_version == "LedgerEvent.v2"
            else _LEDGER_EVENT_V1_KEYS
            if schema_version == "LedgerEvent.v1"
            else None
        )
        if expected_keys is None or set(event) != expected_keys:
            raise ValueError("protocol event hash chain envelope is invalid")
        if event.get("event_seq") != expected_seq:
            raise ValueError("protocol event hash chain sequence is invalid")
        if event.get("prev_event_hash") != previous_hash:
            raise ValueError("protocol event hash chain predecessor is invalid")
        event_hash = event.get("event_hash")
        hash_input = {key: value for key, value in event.items() if key != "event_hash"}
        if (
            not isinstance(event_hash, str)
            or event_hash
            != "sha256:" + hashlib.sha256(_canonical_bytes(hash_input)).hexdigest()
        ):
            raise ValueError("protocol event hash chain content hash is invalid")
        previous_hash = event_hash


def _event_belongs_to_task(
    event: Mapping[str, Any],
    task: Mapping[str, Any],
) -> bool:
    mapping = task.get("protocol_event_ledger")
    if _is_protocol_ledger_event(event) and isinstance(mapping, Mapping):
        event_hashes = mapping.get("event_hashes")
        return (
            isinstance(event_hashes, list)
            and event.get("event_hash") in event_hashes
        )
    return event.get("task_id") == task.get("task_id")


def _validate_stored_protocol_event_ledgers(
    *,
    tasks: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> None:
    protocol_events = [
        event for event in events if _is_protocol_ledger_event(event)
    ]
    if not protocol_events:
        return
    events_by_hash: dict[str, Mapping[str, Any]] = {}
    for event in protocol_events:
        event_hash = event.get("event_hash")
        if not isinstance(event_hash, str) or event_hash in events_by_hash:
            raise ValueError("protocol event ledger event hash inventory is invalid")
        events_by_hash[event_hash] = event
    accounted_hashes: set[str] = set()
    for task in tasks:
        mapping = task.get("protocol_event_ledger")
        if not isinstance(mapping, Mapping):
            continue
        event_hashes = mapping.get("event_hashes")
        if (
            not isinstance(event_hashes, list)
            or not event_hashes
            or any(not isinstance(value, str) for value in event_hashes)
        ):
            raise ValueError("protocol event ledger mapping is invalid")
        if any(value in accounted_hashes for value in event_hashes):
            raise ValueError("protocol event ledger mapping overlaps another task")
        try:
            task_events = [events_by_hash[value] for value in event_hashes]
        except KeyError as error:
            raise ValueError("protocol event ledger mapping references missing event") from error
        expected_mapping = _protocol_event_ledger_metadata(
            case_task_id=str(task.get("task_id")),
            events=task_events,
        )
        if _canonical_bytes(mapping) != _canonical_bytes(expected_mapping):
            raise ValueError("protocol event ledger mapping does not match events")
        accounted_hashes.update(event_hashes)
    if accounted_hashes != set(events_by_hash):
        raise ValueError("protocol event ledger contains unmapped events")


def _merge_records(
    existing: Sequence[dict[str, Any]],
    incoming: Sequence[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    merged = list(existing)
    positions: dict[str, int] = {}
    for index, record in enumerate(merged):
        value = record.get(key)
        if value is not None:
            positions[str(value)] = index
    for record in incoming:
        value = record.get(key)
        if value is None:
            if record not in merged:
                merged.append(record)
            continue
        text = str(value)
        if text in positions:
            if _canonical_bytes(merged[positions[text]]) != _canonical_bytes(record):
                raise ValueError(f"conflicting checkpoint evidence for {key}={text}")
        else:
            positions[text] = len(merged)
            merged.append(record)
    return merged


def _merge_event_records(
    existing: Sequence[dict[str, Any]],
    incoming: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(existing)
    positions = {
        _event_merge_identity(record): index
        for index, record in enumerate(merged)
    }
    for record in incoming:
        identity = _event_merge_identity(record)
        if identity in positions:
            if _canonical_bytes(merged[positions[identity]]) != _canonical_bytes(
                record
            ):
                raise ValueError("conflicting checkpoint event evidence")
            continue
        positions[identity] = len(merged)
        merged.append(record)
    return merged


def _event_merge_identity(record: Mapping[str, Any]) -> tuple[str, ...]:
    if _is_protocol_ledger_event(record):
        event_hash = record.get("event_hash")
        if not isinstance(event_hash, str) or not event_hash:
            raise ValueError("protocol event hash chain event_hash is missing")
        return ("protocol", event_hash)
    event_id = record.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("experiment event_id is missing")
    return (
        "experiment",
        str(record.get("experiment_id")),
        str(record.get("condition_id")),
        str(record.get("repeat_id")),
        str(record.get("task_id")),
        event_id,
    )


def _is_success(task: Mapping[str, Any]) -> bool:
    status = task.get("root_status", task.get("status"))
    return isinstance(status, str) and status.lower() in _SUCCESS_STATUSES


def _shared_source_usage(
    *,
    task: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provider_attempts = [
        item
        for item in attempts
        if (
            isinstance(item.get("provider_attempt_count"), int)
            and not isinstance(item.get("provider_attempt_count"), bool)
            and int(item["provider_attempt_count"]) > 0
        )
        or isinstance(item.get("model_execution_record_ref"), Mapping)
    ]
    expected_count = task.get("provider_attempt_count")
    count_complete = (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count > 0
        and expected_count == len(provider_attempts)
    )
    complete_attempts: list[Mapping[str, Any]] = []
    currencies: set[str] = set()
    cost_statuses: set[str] = set()
    for attempt in provider_attempts:
        token_values = [
            attempt.get(field_name)
            for field_name in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        ]
        cost_value = attempt.get("cost_estimate")
        currency = attempt.get("cost_estimate_currency")
        cost_status = attempt.get("cost_estimate_status")
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in token_values
            )
            or isinstance(cost_value, bool)
            or not isinstance(cost_value, (int, float))
            or float(cost_value) < 0
            or not isinstance(currency, str)
            or not currency
            or cost_status != "estimated"
            or attempt.get("usage_missing") is True
        ):
            continue
        complete_attempts.append(attempt)
        currencies.add(currency)
        cost_statuses.add(cost_status)
    usage_complete = (
        count_complete
        and len(complete_attempts) == len(provider_attempts)
        and len(currencies) == 1
        and cost_statuses == {"estimated"}
    )
    missing_count = max(
        int(expected_count) if isinstance(expected_count, int) else 0,
        len(provider_attempts),
    ) - len(complete_attempts)
    return {
        "provider_attempt_count": len(provider_attempts),
        "expected_provider_attempt_count": (
            int(expected_count)
            if isinstance(expected_count, int) and not isinstance(expected_count, bool)
            else None
        ),
        "prompt_tokens": (
            sum(int(item["prompt_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "completion_tokens": (
            sum(int(item["completion_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "total_tokens": (
            sum(int(item["total_tokens"]) for item in complete_attempts)
            if usage_complete
            else None
        ),
        "cost_estimate": (
            round(
                sum(float(item["cost_estimate"]) for item in complete_attempts),
                12,
            )
            if usage_complete
            else None
        ),
        "usage_complete": usage_complete,
        "usage_missing_provider_attempt_count": missing_count,
        "cost_estimate_status": "estimated" if usage_complete else "usage_missing",
        "cost_estimate_currency": next(iter(currencies)) if usage_complete else None,
    }


def _validate_execution_version_identity(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise SharedEvidenceError(
            "shared source execution version identity is missing",
            evidence_integrity="missing",
        )
    required_strings = (
        "schema_version",
        "plugin_version",
        "parser_version",
        "verifier_version",
        "executor_version",
        "prompt_version",
        "runtime_generation_schema_version",
    )
    if value.get("schema_version") != "tokenshare.paper_execution_version_identity.v1":
        raise SharedEvidenceError(
            "shared source execution version identity schema is invalid",
            evidence_integrity="invalid",
        )
    for field_name in required_strings:
        field_value = value.get(field_name)
        if not isinstance(field_value, str) or not field_value:
            raise SharedEvidenceError(
                f"shared source execution version identity is missing: {field_name}",
                evidence_integrity="missing",
            )
    for field_name in (
        "split_profile_digest",
        "runtime_generation_identity_digest",
    ):
        field_value = value.get(field_name)
        if (
            not isinstance(field_value, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", field_value)
        ):
            raise SharedEvidenceError(
                f"shared source execution version digest is invalid: {field_name}",
                evidence_integrity="invalid",
            )


def _is_terminal_checkpoint(task: Mapping[str, Any]) -> bool:
    status = task.get("root_status", task.get("status"))
    return (
        isinstance(status, str)
        and status.lower() in _TERMINAL_CHECKPOINT_STATUSES
    )


def _derived_run_status(
    tasks: Sequence[Mapping[str, Any]],
    *,
    expected_task_count: int,
) -> str:
    if len(tasks) < expected_task_count:
        return "running"
    statuses = {
        str(task.get("root_status", task.get("status", "failed"))).lower()
        for task in tasks
    }
    if tasks and all(_is_success(task) for task in tasks):
        return "completed"
    if "budget_exhausted" in statuses:
        return "budget_exhausted"
    if statuses == {"blocked"}:
        return "blocked"
    return "completed_with_failures"


def _file_evidence(root: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    relative_path = path.relative_to(root).as_posix()
    records: list[Any] | None = None
    is_artifact_payload = "/artifacts/" in f"/{relative_path}" and not relative_path.endswith(
        "artifact_index.jsonl"
    )
    if not is_artifact_payload and path.suffix == ".json":
        records = [json.loads(content.decode("utf-8"))]
    elif path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    return {
        "path": relative_path,
        "size": len(content),
        "content_sha256": _digest_bytes(content),
        "record_count": None if records is None else len(records),
        "records_digest": None if records is None else _digest_json(records),
    }


def _atomic_write_json(path: Path, body: Any) -> None:
    _atomic_write(path, _canonical_bytes(body) + b"\n")


def _atomic_write_jsonl(path: Path, records: Sequence[Any]) -> None:
    content = b"".join(_canonical_bytes(record) + b"\n" for record in records)
    _atomic_write(path, content)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    body = _read_json_value(path)
    if not isinstance(body, dict):
        raise ValueError(f"JSON evidence must be an object: {path.name}")
    return body


def _read_json_value(path: Path) -> Any:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON evidence: {path.name}") from exc
    return body


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSONL evidence: {path.name}") from exc
    if any(not isinstance(item, dict) for item in records):
        raise ValueError(f"JSONL evidence requires object records: {path.name}")
    return records


def _read_jsonl_if_present(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl(path) if path.is_file() else []


def _generation_jsonl(
    generation_root: Path | None,
    relative_path: str,
) -> list[dict[str, Any]]:
    if generation_root is None:
        return []
    return _read_jsonl(generation_root / relative_path)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    body = _json_value(value)
    if not isinstance(body, dict):
        raise ValueError(f"{label} must be a JSON object")
    return body


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"value is not JSON serializable: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _logical_records_digest(generation_manifest: Mapping[str, Any]) -> str:
    files = generation_manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("snapshot generation files inventory is invalid")
    return _digest_json(
        [
            {
                "path": entry["path"],
                "record_count": entry["record_count"],
                "records_digest": entry["records_digest"],
            }
            for entry in files
            if isinstance(entry, Mapping)
        ]
    )


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _nested_source_artifact_refs(value: Any) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if value.get("schema_version") == "ArtifactRef.v1":
            result.append(value)
        for child in value.values():
            result.extend(_nested_source_artifact_refs(child))
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for child in value:
            result.extend(_nested_source_artifact_refs(child))
    return result


def _source_artifact_identity(
    ref: Mapping[str, Any],
) -> tuple[str, str, str]:
    artifact_id = ref.get("artifact_id")
    content_hash = ref.get("content_hash")
    if isinstance(artifact_id, str) and artifact_id:
        return ("artifact_id", artifact_id, str(content_hash))
    return ("uri", str(ref.get("uri")), str(content_hash))


def _validate_complete_source_artifact_ref(ref: Mapping[str, Any]) -> None:
    if ref.get("schema_version") != "ArtifactRef.v1":
        raise ValueError("reachable artifact closure requires ArtifactRef.v1")
    for field_name in (
        "artifact_id",
        "artifact_type",
        "uri",
        "content_hash",
        "media_type",
        "artifact_schema_id",
        "artifact_schema_version",
        "created_at",
    ):
        value = ref.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"reachable artifact closure requires source {field_name}"
            )
    size_bytes = ref.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
    ):
        raise ValueError("reachable artifact closure requires source size_bytes")
    for field_name in ("source", "metadata"):
        if not isinstance(ref.get(field_name), Mapping):
            raise ValueError(
                f"reachable artifact closure requires source {field_name} mapping"
            )


def _declares_json_media_type(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.split(";", maxsplit=1)[0].strip().lower()
    return normalized == "application/json" or normalized.endswith("+json")


def _safe_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a path-safe identifier")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_adapter_compatibility_path(root: Path, relative_path: str) -> bool:
    """plan-root working/compatibility tree 不属于 canonical evidence 闭包。"""

    parts = Path(relative_path).parts
    if not parts or parts[0] in {"experiments", "repairs"}:
        return False
    return (root / "experiments" / parts[0]).is_dir()


def _lock_for_root(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_output_root_lock(root: Path, *, timeout_seconds: float = 30.0):
    lock_path = root / _LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        acquired = False
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "timed out acquiring formal evidence output-root lock"
                    ) from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_exact_keys(
    body: Mapping[str, Any],
    expected_keys: set[str],
    label: str,
) -> None:
    if set(body) != expected_keys:
        raise ValueError(f"{label} keys mismatch")


def _require_string(
    body: Mapping[str, Any],
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} {field_name} type is invalid")


def _require_digest(
    body: Mapping[str, Any],
    field_name: str,
    label: str,
) -> None:
    value = body.get(field_name)
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} {field_name} type is invalid")


def lineage_input_identity_digest(canonical_inputs: Mapping[str, object]) -> str:
    """对 projector 的 typed canonical inputs 计算稳定 sidecar 输入身份。"""

    if not isinstance(canonical_inputs, Mapping):
        raise TypeError("lineage canonical inputs must be a mapping")
    return _digest_json(
        {
            "schema_version": "tokenshare.lineage_source_input_identity.v1",
            "canonical_inputs": _lineage_json_value(canonical_inputs),
        }
    )


def export_lineage_source_index(
    store: FormalEvidenceStore,
    canonical_inputs: Sequence[CanonicalLineageInput],
    *,
    input_identity_digest: str,
) -> LineageSourceIndex:
    """只从显式 Task3/Task19 typed inputs 与其指定 logical closure 导出。"""

    if not isinstance(store, FormalEvidenceStore):
        raise TypeError("lineage exporter requires FormalEvidenceStore")
    inputs = tuple(canonical_inputs)
    if any(not isinstance(value, CanonicalLineageInput) for value in inputs):
        raise TypeError("lineage exporter inputs must contain CanonicalLineageInput")
    if any(not value.producer_validated for value in inputs):
        raise ValueError("lineage inputs must come from canonical lineage factory")
    if any(value.input_identity_digest != input_identity_digest for value in inputs):
        raise ValueError("lineage source input identity mismatch")
    root_ids = tuple(
        value.direct_result.preregistered_root_run_id for value in inputs
    )
    if len(set(root_ids)) != len(root_ids):
        raise ValueError("lineage direct result identity is ambiguous")
    records: list[LineageSourceRecord] = []
    for lineage_input in inputs:
        direct = lineage_input.direct_result
        if direct.execution_binding is not None:
            logical = store.load_logical_run_records(
                experiment_id=direct.experiment_id,
                condition_id=direct.condition_id,
                repeat_id=direct.repeat_id,
            )
            _validate_persisted_lineage_closure(lineage_input, logical)
        wrappers = lineage_input.current_trace_wrappers
        bindings = lineage_input.trace_source_bindings
        if direct.execution_binding is not None and any(
            value.current_task_id != direct.execution_binding.task_id
            for value in wrappers
        ):
            raise ValueError("current trace wrapper task identity mismatch")
        source_locators = _unique_typed(direct.source_bank_object_locators)
        provider_refs = _unique_typed(direct.current_provider_object_refs)
        if direct.evidence_class == "online_real_provider" and source_locators:
            raise ValueError("online lineage cannot contain source bank locators")
        if direct.evidence_class == "real_model_trace_protocol_run" and provider_refs:
            raise ValueError("trace lineage cannot contain current provider refs")

        current_refs = _unique_typed((*direct.attempt_refs, *direct.event_refs))
        parser_refs = _unique_typed(
            (
                *direct.parser_refs,
                *direct.verifier_checker_refs,
                *(() if direct.canonical_acceptance_ref is None else (direct.canonical_acceptance_ref,)),
                *(() if direct.merge_ref is None else (direct.merge_ref,)),
            )
        )
        ledger_refs = _unique_typed(
            (
                *direct.event_refs,
                *(() if direct.terminal_root_event_ref is None else (direct.terminal_root_event_ref,)),
                *(() if direct.canonical_acceptance_ref is None else (direct.canonical_acceptance_ref,)),
                *(() if direct.merge_ref is None else (direct.merge_ref,)),
            )
        )
        root_record = LineageSourceRecord.create(
            member_id=direct.preregistered_root_run_id,
            evidence_class=direct.evidence_class,
            direct_result_refs=(direct.to_dict(),),
            current_task_attempt_event_refs=current_refs,
            parser_verifier_checker_canonical_refs=parser_refs,
            ledger_refs=ledger_refs,
            current_provider_object_refs=provider_refs,
            source_bank_object_locators=source_locators,
            current_trace_wrappers=wrappers,
            trace_source_bindings=bindings,
        )
        records.append(root_record)
        aliases = {
            direct.preregistered_root_run_id,
            *(value.object_id for value in current_refs),
            *(value.source_execution_id for value in provider_refs),
            *(value.entry_id for value in source_locators),
            *(value.current_attempt_id for value in wrappers),
            *(value.current_unit_id for value in wrappers),
            *(value.entry_id for value in wrappers),
            *(value.planned_ai_unit_id for value in bindings),
            *(
                replacement.entry_id
                for binding in bindings
                for replacement in binding.replacements
            ),
        }
        for alias in sorted(aliases - {direct.preregistered_root_run_id}):
            records.append(
                LineageSourceRecord.create(
                    member_id=alias,
                    evidence_class=direct.evidence_class,
                    direct_result_refs=(direct.to_dict(),),
                    current_task_attempt_event_refs=current_refs,
                    parser_verifier_checker_canonical_refs=parser_refs,
                    ledger_refs=ledger_refs,
                    current_provider_object_refs=tuple(
                        value
                        for value in provider_refs
                        if value.source_execution_id == alias
                    ),
                    source_bank_object_locators=tuple(
                        value for value in source_locators if value.entry_id == alias
                    ),
                    current_trace_wrappers=tuple(
                        value
                        for value in wrappers
                        if alias
                        in {
                            value.current_attempt_id,
                            value.current_unit_id,
                            value.entry_id,
                        }
                    ),
                    trace_source_bindings=tuple(
                        value
                        for value in bindings
                        if alias == value.planned_ai_unit_id
                        or alias in {item.entry_id for item in value.replacements}
                    ),
                )
            )
    merged = _merge_lineage_source_records(records)
    return LineageSourceIndex.create(
        records=merged,
        input_identity_digest=input_identity_digest,
    )


def _validate_persisted_lineage_closure(
    lineage_input: CanonicalLineageInput,
    logical: Mapping[str, Any],
) -> None:
    direct = lineage_input.direct_result
    binding = direct.execution_binding
    if binding is None:
        raise ValueError("executed lineage closure requires execution binding")
    tasks = tuple(logical["tasks"])
    task_matches = tuple(
        task
        for task in tasks
        if task.get("task_id") == binding.task_id
        and task.get("case_id") == direct.case_id
        and task.get("preregistered_root_run_id") == direct.preregistered_root_run_id
    )
    if len(task_matches) != 1:
        raise ValueError("persisted closure root/case identity mismatch")
    task = task_matches[0]
    runtime_identity = task.get("runtime_generation_identity")
    if not isinstance(runtime_identity, Mapping) or any(
        runtime_identity.get(name) != expected
        for name, expected in (
            ("run_id", binding.execution_id),
            ("task_id", binding.task_id),
            ("root_unit_id", binding.root_unit_id),
            ("ledger_digest", binding.ledger_digest),
        )
    ):
        raise ValueError("persisted closure execution binding mismatch")

    persisted_events = {
        (event.get("event_id"), event.get("event_hash")): event
        for event in logical["events"]
        if isinstance(event, Mapping) and event.get("task_id") == binding.task_id
    }
    for event_ref in binding.events:
        event = persisted_events.get((event_ref.event_id, event_ref.event_hash))
        if event is None or any(
            event.get(name) != expected
            for name, expected in (
                ("event_seq", event_ref.event_seq),
                ("event_type", event_ref.event_type),
                ("prev_event_hash", event_ref.prev_event_hash),
                ("task_id", event_ref.task_id),
                ("object_type", event_ref.object_type),
                ("object_id", event_ref.object_id),
            )
        ):
            raise ValueError("persisted closure ledger event identity mismatch")

    persisted_artifact_records = tuple(
        artifact
        for artifact in logical["artifacts"]
        if isinstance(artifact, Mapping) and artifact.get("task_id") == binding.task_id
    )
    persisted_artifacts = tuple(
        artifact.get("source_artifact_ref")
        for artifact in persisted_artifact_records
        if isinstance(artifact.get("source_artifact_ref"), Mapping)
    )
    for artifact_ref in direct.artifact_refs:
        if not any(
            _artifact_snapshot_matches_source_ref(artifact_ref, value)
            for value in persisted_artifacts
        ):
            raise ValueError("persisted closure artifact identity mismatch")

    attempt_records = tuple(
        value
        for value in logical["attempts"]
        if isinstance(value, Mapping) and value.get("task_id") == binding.task_id
    )
    attempts = {value.get("attempt_id") for value in attempt_records}
    event_ids = {value[0] for value in persisted_events}
    artifact_ids = {
        value.get("artifact_id")
        for value in persisted_artifacts
        if isinstance(value, Mapping)
    }
    wrappers = lineage_input.current_trace_wrappers
    source_locators = direct.source_bank_object_locators
    locators_by_entry: dict[str, dict[str, str]] = {}
    bank_identity_by_entry: dict[str, tuple[str, str]] = {}
    for locator in source_locators:
        locators_by_entry.setdefault(locator.entry_id, {})[
            locator.object_role
        ] = locator.object_digest
        bank_identity_by_entry[locator.entry_id] = (
            locator.bank_root_id,
            locator.manifest_digest,
        )
    for wrapper in wrappers:
        if (
            wrapper.current_task_id != binding.task_id
            or wrapper.current_attempt_id not in attempts
            or wrapper.entry_id not in locators_by_entry
            or dict(wrapper.locator_digests) != locators_by_entry[wrapper.entry_id]
            or (wrapper.bank_root_id, wrapper.manifest_digest)
            != bank_identity_by_entry[wrapper.entry_id]
        ):
            raise ValueError("persisted closure current trace wrapper identity mismatch")
        for reference in (
            wrapper.current_parse_ref,
            wrapper.current_verifier_ref,
            wrapper.current_checker_ref,
            wrapper.current_canonical_ref,
            wrapper.current_ledger_ref,
        ):
            if reference is not None and reference not in event_ids | artifact_ids:
                raise ValueError("persisted closure wrapper reference is unreachable")
        wrapper_digest = _digest_json(_current_trace_wrapper_body(wrapper))
        if not any(
            isinstance((source_ref := record.get("source_artifact_ref")), Mapping)
            and source_ref.get("artifact_type") == "CurrentTraceWrapper"
            and source_ref.get("content_hash") == wrapper_digest
            for record in persisted_artifact_records
        ):
            raise ValueError("persisted closure current trace wrapper is not persisted")
    wrapper_entries = {value.entry_id for value in wrappers}
    for source_binding in lineage_input.trace_source_bindings:
        replacement_entries = {value.entry_id for value in source_binding.replacements}
        if not replacement_entries or not replacement_entries <= wrapper_entries:
            raise ValueError("persisted closure trace binding is unreachable")
        for entry_id in replacement_entries:
            if (
                source_binding.bank_root_id,
                source_binding.manifest_digest,
            ) != bank_identity_by_entry.get(entry_id):
                raise ValueError("persisted closure trace source identity mismatch")
        if not any(
            attempt.get("source_binding_digest") == source_binding.binding_digest
            or attempt.get("trace_source_binding_digest") == source_binding.binding_digest
            for attempt in attempt_records
        ):
            raise ValueError("persisted closure trace binding is not persisted")
    facts = lineage_input.eligibility_facts
    if facts is not None:
        report = evaluate_versioned_paper_evidence(facts)
        if task.get("versioned_paper_evidence_report") != report.to_dict():
            raise ValueError("persisted closure eligibility report mismatch")


def _artifact_snapshot_matches_source_ref(
    snapshot: ArtifactIdentitySnapshot,
    source_ref: Mapping[str, Any],
) -> bool:
    source = source_ref.get("source")
    return isinstance(source, Mapping) and all(
        actual == expected
        for actual, expected in (
            (source_ref.get("artifact_id"), snapshot.artifact_id),
            (source_ref.get("artifact_type"), snapshot.artifact_type),
            (source_ref.get("uri"), snapshot.uri),
            (source_ref.get("content_hash"), snapshot.content_hash),
            (source_ref.get("size_bytes"), snapshot.size_bytes),
            (source_ref.get("media_type"), snapshot.media_type),
            (source_ref.get("artifact_schema_id"), snapshot.artifact_schema_id),
            (
                source_ref.get("artifact_schema_version"),
                snapshot.artifact_schema_version,
            ),
            (source.get("role"), snapshot.source_role),
            (source.get("task_id"), snapshot.source_task_id),
            (source.get("execution_id"), snapshot.source_execution_id),
            (source_ref.get("created_at"), snapshot.created_at),
        )
    )


def _current_trace_wrapper_body(value: CurrentTraceWrapper) -> dict[str, Any]:
    return {
        field_name: (
            dict(field_value)
            if isinstance(field_value, Mapping)
            else field_value
        )
        for field_name in value.__dataclass_fields__
        for field_value in (getattr(value, field_name),)
    }


def _typed_mapping_sequence_matches(
    actual: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
) -> bool:
    return len(actual) == len(expected) and all(
        _canonical_bytes(left) == _canonical_bytes(right)
        for left, right in zip(actual, expected, strict=True)
    )


def _lineage_source_record_body(
    *,
    member_id: str,
    evidence_class: str,
    direct_result_refs: Sequence[Mapping[str, Any]],
    current_task_attempt_event_refs: Sequence[LedgerEventIdentitySnapshot],
    parser_verifier_checker_canonical_refs: Sequence[
        ArtifactIdentitySnapshot | LedgerEventIdentitySnapshot
    ],
    ledger_refs: Sequence[LedgerEventIdentitySnapshot],
    current_provider_object_refs: Sequence[ArtifactIdentitySnapshot],
    source_bank_object_locators: Sequence[ExternalBankObjectLocator],
    current_trace_wrappers: Sequence[CurrentTraceWrapper],
    trace_source_bindings: Sequence[TraceSourceBinding],
) -> dict[str, Any]:
    return {
        "schema_version": "tokenshare.lineage_source_record.v1",
        "member_id": member_id,
        "evidence_class": evidence_class,
        "direct_result_refs": [_lineage_json_value(value) for value in direct_result_refs],
        "current_task_attempt_event_refs": [
            value.to_dict() for value in current_task_attempt_event_refs
        ],
        "parser_verifier_checker_canonical_refs": [
            value.to_dict() for value in parser_verifier_checker_canonical_refs
        ],
        "ledger_refs": [value.to_dict() for value in ledger_refs],
        "current_provider_object_refs": [
            value.to_dict() for value in current_provider_object_refs
        ],
        "source_bank_object_locators": [
            value.to_dict() for value in source_bank_object_locators
        ],
        "current_trace_wrappers": [
            _current_trace_wrapper_body(value) for value in current_trace_wrappers
        ],
        "trace_source_bindings": [value.to_dict() for value in trace_source_bindings],
    }


def _merge_lineage_source_records(
    records: Sequence[LineageSourceRecord],
) -> tuple[LineageSourceRecord, ...]:
    grouped: dict[str, list[LineageSourceRecord]] = {}
    for record in records:
        grouped.setdefault(record.member_id, []).append(record)
    merged: list[LineageSourceRecord] = []
    for member_id in sorted(grouped):
        values = grouped[member_id]
        evidence_classes = {value.evidence_class for value in values}
        if len(evidence_classes) != 1:
            raise ValueError("lineage member evidence class identity conflict")
        merged.append(
            LineageSourceRecord.create(
                member_id=member_id,
                evidence_class=next(iter(evidence_classes)),
                direct_result_refs=_unique_mappings(
                    item for value in values for item in value.direct_result_refs
                ),
                current_task_attempt_event_refs=_unique_typed(
                    item
                    for value in values
                    for item in value.current_task_attempt_event_refs
                ),
                parser_verifier_checker_canonical_refs=_unique_typed(
                    item
                    for value in values
                    for item in value.parser_verifier_checker_canonical_refs
                ),
                ledger_refs=_unique_typed(
                    item for value in values for item in value.ledger_refs
                ),
                current_provider_object_refs=_unique_typed(
                    item
                    for value in values
                    for item in value.current_provider_object_refs
                ),
                source_bank_object_locators=_unique_typed(
                    item
                    for value in values
                    for item in value.source_bank_object_locators
                ),
                current_trace_wrappers=_unique_typed(
                    item
                    for value in values
                    for item in value.current_trace_wrappers
                ),
                trace_source_bindings=_unique_typed(
                    item
                    for value in values
                    for item in value.trace_source_bindings
                ),
            )
        )
    return tuple(merged)


def _unique_typed(values: Sequence[Any] | Any) -> tuple[Any, ...]:
    by_digest: dict[str, Any] = {}
    for value in values:
        body = (
            _current_trace_wrapper_body(value)
            if isinstance(value, CurrentTraceWrapper)
            else value.to_dict()
        )
        key = _digest_json(_lineage_json_value(body))
        previous = by_digest.setdefault(key, value)
        if previous != value:
            raise ValueError("typed lineage identity conflict")
    return tuple(by_digest[key] for key in sorted(by_digest))


def _unique_mappings(values: Any) -> tuple[Mapping[str, Any], ...]:
    by_digest: dict[str, Mapping[str, Any]] = {}
    for value in values:
        key = _digest_json(_lineage_json_value(value))
        previous = by_digest.setdefault(key, value)
        if _canonical_bytes(previous) != _canonical_bytes(value):
            raise ValueError("direct lineage mapping identity conflict")
    return tuple(by_digest[key] for key in sorted(by_digest))


def _lineage_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _lineage_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_lineage_json_value(item) for item in value]
    if is_dataclass(value):
        if hasattr(value, "to_dict"):
            return _lineage_json_value(value.to_dict())
        return {
            field_name: _lineage_json_value(getattr(value, field_name))
            for field_name in value.__dataclass_fields__
            if not field_name.startswith("_")
        }
    raise TypeError(f"unsupported lineage identity value: {type(value).__name__}")


def _is_noncurrent_generation_path(root: Path, relative_path: str) -> bool:
    parts = Path(relative_path).parts
    try:
        generation_index = parts.index(".generations")
    except ValueError:
        return False
    if generation_index + 1 >= len(parts):
        return False
    pointer_path = root.joinpath(*parts[:generation_index], "CURRENT.json")
    if not pointer_path.is_file():
        return False
    pointer = _read_json(pointer_path)
    current_id = pointer.get("generation_id")
    if not isinstance(current_id, str):
        return False
    return parts[generation_index + 1] != current_id
