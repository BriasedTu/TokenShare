"""从 canonical runtime 本体投影固定分母 direct results。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_models import (
    _CANONICAL_DIRECT_EVIDENCE_FACTORY_TOKEN,
    ArtifactIdentitySnapshot,
    CanonicalDirectRootEvidence,
    DirectRootExecutionBinding,
    ExternalBankObjectLocator,
    JsonObject,
    LedgerEventIdentitySnapshot,
    PaperDirectRootInventoryRow,
    PaperDirectRootStatus,
    PreregisteredRootInventoryManifest,
    digest_json,
)
from tokenshare.local_runtime.contracts import (
    ProtocolRunResult,
    RuntimeHookObservationV1,
)
from tokenshare.local_runtime.projection import (
    build_runtime_observation,
    project_protocol_run,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


PAPER_DIRECT_ROOT_RESULT_SCHEMA_VERSION = "tokenshare.paper_direct_root_result.v2"
PAPER_DIRECT_PROJECTION_SCHEMA_VERSION = "tokenshare.paper_direct_projection.v2"
PAPER_DIRECT_AGGREGATE_SCHEMA_VERSION = "tokenshare.paper_direct_boolean_aggregate.v1"
CANONICAL_DIRECT_EVIDENCE_SCHEMA_VERSION = (
    "tokenshare.canonical_direct_root_evidence.v1"
)
DIRECT_RESULT_PRODUCER_BOUNDARY = "canonical_runtime_typed_evidence_factory"
_DIRECT_RESULT_FACTORY_TOKEN = object()
CONDITION_AXIS_KEYS = (
    "domain",
    "difficulty",
    "topic_family",
    "worker_count",
    "sample_slot_index",
    "fault_condition",
    "death_condition",
    "ablation_mode",
    "model_endpoint_id",
)
LEAN_MIXED_TOPIC_FAMILY_AXIS = "mixed_topic_family"

_CONDITION_MANIFEST_SCHEMA = "tokenshare.preregistered_condition_manifest.v1"
_CONDITION_RECORD_SCHEMA = "tokenshare.preregistered_condition_record.v1"
_CONDITION_REF_SCHEMA = "tokenshare.preregistered_condition_ref.v1"
_CATALOG_MANIFEST_SCHEMA = "tokenshare.preregistered_case_catalog_manifest.v1"
_CASE_RECORD_SCHEMA = "tokenshare.preregistered_case_record.v1"
_CASE_REF_SCHEMA = "tokenshare.preregistered_case_ref.v1"
_LEAN_CASE_RECORD_SCHEMA = "tokenshare.preregistered_lean_case_record.v1"
_LEAN_CASE_REF_SCHEMA = "tokenshare.preregistered_lean_case_ref.v1"
_ACTUAL_RESOURCE_BOOK_SCHEMA = "tokenshare.paper_actual_resource_book.v1"
_PARSER_ROLES = frozenset({"parser_result", "parse_failure"})

_ATTEMPT_EVENT_TYPES = frozenset(
    {
        EventType.EXECUTION_REQUEST_RECORDED.value,
        EventType.EXECUTION_SUBMISSION_RECORDED.value,
        EventType.VERIFICATION_RECORDED.value,
    }
)
_COMMON_PROVIDER_ROLES = frozenset(
    {
        "request_body",
        "raw_output_or_provider_failure",
        "provenance",
        "usage_status",
        "latency",
        "pricing",
        "model_record",
    }
)
_ONLINE_PROVIDER_ROLES = _COMMON_PROVIDER_ROLES | {"provider_attempt"}
_TRACE_SOURCE_ROLES = _COMMON_PROVIDER_ROLES | {"acquisition_attempt"}
_REGRESSION_SOURCE_ROLES = frozenset(
    {"raw_output", "provenance", "model_record"}
)
_VERIFIER_ROLES = frozenset(
    {
        "independent_verdict",
        "lean_checker_verdict",
        "root_checker_report",
        "verification_report",
    }
)
from tokenshare.plugins.lean_proof.checker import render_lean_source
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
_DIRECT_RUNTIME_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "blocked",
        "timeout",
        "budget_exhausted",
    }
)
_TERMINAL_TASK_UNIT_STATES = {
    "Completed": "completed",
    "Failed": "failed",
}
_COORDINATOR_SUMMARY_KEYS = frozenset(
    {
        "runtime_observation",
        "runtime_hook_observations",
        "execution_scope",
        "selected_ai_unit_ids",
        "partial_observation",
    }
)
_PARTIAL_OBSERVATION_KEYS = frozenset(
    {"execution_scope", "selected_ai_unit_ids", "partial_observation"}
)
_RUNTIME_OBSERVATION_KEYS = {
    "schema_version",
    "run_id",
    "runtime_started_at",
    "runtime_ended_at",
    "runtime_wall_clock_ms",
    "planned_ai_unit_ids",
    "dispatched_ai_unit_ids",
    "completed_ai_unit_ids",
    "unscheduled_ai_unit_ids",
    "in_flight_ai_unit_ids_at_witness",
    "witness_observed_at",
    "worker_execution_facts",
    "observed_peak_concurrency",
}


@dataclass(frozen=True, kw_only=True)
class PaperDirectRootResult:
    preregistered_root_run_id: str
    experiment_id: str
    condition_id: str
    preregistered_condition_ref: Mapping[str, Any]
    condition_axes: Mapping[str, Any]
    case_id: str
    preregistered_case_ref: Mapping[str, Any]
    repeat_id: int
    evidence_class: str
    root_status: str
    execution_binding: DirectRootExecutionBinding | None
    final_result_ref: ArtifactIdentitySnapshot | None
    terminal_root_event_ref: LedgerEventIdentitySnapshot | None
    canonical_acceptance_ref: LedgerEventIdentitySnapshot | None
    merge_ref: LedgerEventIdentitySnapshot | None
    final_result_reference_complete: bool
    independently_verified_correct: bool
    paper_evidence_complete: bool
    identity_consistent: bool
    end_to_end_verified_success: bool
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
    producer_boundary: str = DIRECT_RESULT_PRODUCER_BOUNDARY
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = PAPER_DIRECT_ROOT_RESULT_SCHEMA_VERSION
    _factory_token: InitVar[object | None] = None
    _factory_validated: bool = field(
        init=False,
        default=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self, _factory_token: object | None) -> None:
        factory_validated = _factory_token is _DIRECT_RESULT_FACTORY_TOKEN
        object.__setattr__(self, "_factory_validated", factory_validated)
        if self.schema_version != PAPER_DIRECT_ROOT_RESULT_SCHEMA_VERSION:
            raise ValueError("unsupported paper direct root result schema")
        if self.producer_boundary != DIRECT_RESULT_PRODUCER_BOUNDARY:
            raise ValueError("paper direct result producer boundary mismatch")
        if self.recompute_only is not True or self.paper_eligible is not False:
            raise ValueError("direct result cannot grant paper eligibility")
        for field_name in (
            "preregistered_root_run_id",
            "experiment_id",
            "condition_id",
            "case_id",
            "evidence_class",
        ):
            _non_empty(field_name, getattr(self, field_name))
        _strict_int("repeat_id", self.repeat_id, minimum=0)
        if self.evidence_class not in {
            "online_real_provider",
            "real_model_trace_protocol_run",
            "regression_only",
        }:
            raise ValueError("unsupported evidence class")
        for field_name in (
            "preregistered_condition_ref",
            "condition_axes",
            "preregistered_case_ref",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping) or not value:
                raise ValueError(f"{field_name} must be a non-empty mapping")
            object.__setattr__(self, field_name, _freeze(value))
        if self.execution_binding is not None and not isinstance(
            self.execution_binding,
            DirectRootExecutionBinding,
        ):
            raise TypeError("execution_binding must be typed")
        for field_name, expected_type in (
            ("final_result_ref", ArtifactIdentitySnapshot),
            ("terminal_root_event_ref", LedgerEventIdentitySnapshot),
            ("canonical_acceptance_ref", LedgerEventIdentitySnapshot),
            ("merge_ref", LedgerEventIdentitySnapshot),
            ("actual_resource_book_ref", ArtifactIdentitySnapshot),
            ("trace_resource_book_ref", ArtifactIdentitySnapshot),
        ):
            value = getattr(self, field_name)
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
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, expected_type) for value in values):
                raise TypeError(f"{field_name} must contain typed values")
            object.__setattr__(self, field_name, values)
        reasons = tuple(self.ineligibility_reasons)
        if any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("ineligibility reasons must be non-empty strings")
        object.__setattr__(self, "ineligibility_reasons", reasons)
        PaperDirectRootStatus(self.root_status)
        for field_name in (
            "final_result_reference_complete",
            "independently_verified_correct",
            "paper_evidence_complete",
            "identity_consistent",
            "end_to_end_verified_success",
            "infrastructure_valid",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a bool")
        expected = all(
            (
                self.root_status == PaperDirectRootStatus.COMPLETED.value,
                self.final_result_reference_complete,
                self.independently_verified_correct,
                self.paper_evidence_complete,
                self.identity_consistent,
                self.infrastructure_valid,
                not reasons,
            )
        )
        if factory_validated and self.end_to_end_verified_success is not expected:
            raise ValueError("end-to-end success violates four-gate invariant")
        if not factory_validated and (
            self.end_to_end_verified_success or expected
        ):
            raise ValueError("successful direct rows require canonical factory")
        if self.root_status == PaperDirectRootStatus.NOT_STARTED.value:
            if any(
                (
                    self.execution_binding is not None,
                    self.final_result_ref is not None,
                    self.terminal_root_event_ref is not None,
                    self.canonical_acceptance_ref is not None,
                    self.merge_ref is not None,
                    bool(self.attempt_refs),
                    bool(self.event_refs),
                    bool(self.parser_refs),
                    bool(self.verifier_checker_refs),
                    bool(self.artifact_refs),
                    bool(self.current_provider_object_refs),
                    bool(self.source_bank_object_locators),
                    self.actual_resource_book_ref is not None,
                    self.trace_resource_book_ref is not None,
                )
            ):
                raise ValueError("not_started row cannot contain execution evidence")
            if any(
                (
                    self.final_result_reference_complete,
                    self.independently_verified_correct,
                    self.paper_evidence_complete,
                    self.end_to_end_verified_success,
                    not self.identity_consistent,
                    not self.infrastructure_valid,
                )
            ):
                raise ValueError("not_started row has inconsistent gates")
        elif self.execution_binding is None:
            raise ValueError("observed direct row requires execution binding")
        if reasons and self.root_status != "ineligible":
            raise ValueError("ineligibility reasons require ineligible root status")
        if self.root_status == "ineligible" and not reasons:
            raise ValueError("ineligible root status requires reasons")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "producer_boundary": self.producer_boundary,
            "recompute_only": self.recompute_only,
            "paper_eligible": self.paper_eligible,
            "preregistered_root_run_id": self.preregistered_root_run_id,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "preregistered_condition_ref": _thaw(self.preregistered_condition_ref),
            "condition_axes": _thaw(self.condition_axes),
            "case_id": self.case_id,
            "preregistered_case_ref": _thaw(self.preregistered_case_ref),
            "repeat_id": self.repeat_id,
            "evidence_class": self.evidence_class,
            "root_status": self.root_status,
            "execution_binding": _optional_dict(self.execution_binding),
            "final_result_ref": _optional_dict(self.final_result_ref),
            "terminal_root_event_ref": _optional_dict(
                self.terminal_root_event_ref
            ),
            "canonical_acceptance_ref": _optional_dict(
                self.canonical_acceptance_ref
            ),
            "merge_ref": _optional_dict(self.merge_ref),
            "final_result_reference_complete": self.final_result_reference_complete,
            "independently_verified_correct": self.independently_verified_correct,
            "paper_evidence_complete": self.paper_evidence_complete,
            "identity_consistent": self.identity_consistent,
            "end_to_end_verified_success": self.end_to_end_verified_success,
            "infrastructure_valid": self.infrastructure_valid,
            "attempt_refs": [value.to_dict() for value in self.attempt_refs],
            "event_refs": [value.to_dict() for value in self.event_refs],
            "parser_refs": [value.to_dict() for value in self.parser_refs],
            "verifier_checker_refs": [
                value.to_dict() for value in self.verifier_checker_refs
            ],
            "artifact_refs": [value.to_dict() for value in self.artifact_refs],
            "current_provider_object_refs": [
                value.to_dict() for value in self.current_provider_object_refs
            ],
            "source_bank_object_locators": [
                value.to_dict() for value in self.source_bank_object_locators
            ],
            "actual_resource_book_ref": _optional_dict(
                self.actual_resource_book_ref
            ),
            "trace_resource_book_ref": _optional_dict(
                self.trace_resource_book_ref
            ),
            "ineligibility_reasons": list(self.ineligibility_reasons),
        }


@dataclass(frozen=True, kw_only=True)
class PaperDirectProjection:
    inventory_id: str
    inventory_digest: str
    rows: tuple[PaperDirectRootResult, ...]
    denominator_inventory_ids: tuple[str, ...]
    provider_calls: int = 0
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = PAPER_DIRECT_PROJECTION_SCHEMA_VERSION
    _factory_token: InitVar[object | None] = None
    _factory_validated: bool = field(
        init=False,
        default=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self, _factory_token: object | None) -> None:
        object.__setattr__(
            self,
            "_factory_validated",
            _factory_token is _DIRECT_RESULT_FACTORY_TOKEN,
        )
        if self.schema_version != PAPER_DIRECT_PROJECTION_SCHEMA_VERSION:
            raise ValueError("unsupported paper direct projection schema")
        rows = tuple(self.rows)
        denominator_ids = tuple(self.denominator_inventory_ids)
        if any(not isinstance(row, PaperDirectRootResult) for row in rows):
            raise TypeError("direct projection rows must be typed")
        if any(not isinstance(value, str) or not value for value in denominator_ids):
            raise ValueError("denominator inventory ids must be non-empty strings")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "denominator_inventory_ids", denominator_ids)
        row_ids = tuple(row.preregistered_root_run_id for row in self.rows)
        if row_ids != self.denominator_inventory_ids:
            raise ValueError("direct rows must exactly match fixed inventory")
        if len(set(row_ids)) != len(row_ids):
            raise ValueError("duplicate direct root inventory id")
        if self.provider_calls != 0 or self.paper_eligible is not False:
            raise ValueError("projection cannot call provider or grant eligibility")
        if self.recompute_only is not True:
            raise ValueError("projection must be recompute-only")

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, kw_only=True)
class PaperDirectBooleanAggregate:
    value: float | None
    status: str
    denominator_inventory_ids: tuple[str, ...]
    numerator_inventory_ids: tuple[str, ...]
    audit_denominator_count: int
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = PAPER_DIRECT_AGGREGATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        denominator_ids = tuple(self.denominator_inventory_ids)
        numerator_ids = tuple(self.numerator_inventory_ids)
        object.__setattr__(self, "denominator_inventory_ids", denominator_ids)
        object.__setattr__(self, "numerator_inventory_ids", numerator_ids)
        if self.status not in {"computed", "blocked"}:
            raise ValueError("unsupported paper direct aggregate status")
        if self.status == "blocked" and self.value is not None:
            raise ValueError("blocked aggregate value must be null")
        if self.audit_denominator_count != len(self.denominator_inventory_ids):
            raise ValueError("audit denominator count mismatch")
        if len(set(denominator_ids)) != len(denominator_ids):
            raise ValueError("duplicate aggregate denominator id")
        if len(set(numerator_ids)) != len(numerator_ids):
            raise ValueError("duplicate aggregate numerator id")
        if not set(numerator_ids).issubset(denominator_ids):
            raise ValueError("aggregate numerator is outside denominator")
        if self.recompute_only is not True or self.paper_eligible is not False:
            raise ValueError("aggregate cannot grant paper eligibility")

    def to_dict(self) -> JsonObject:
        return {
            "value": self.value,
            "status": self.status,
            "denominator_inventory_ids": list(self.denominator_inventory_ids),
        }


@dataclass(frozen=True)
class NativeDirectArtifactBundle:
    """adapter 原生 direct ABI；兼容旧的二元解包调用。"""

    actual_resource_book_ref: ArtifactRef | None
    independent_verdict_ref: ArtifactRef | None
    parser_refs: tuple[ArtifactRef, ...]
    domain_report_refs: tuple[ArtifactRef, ...] = ()

    def __iter__(self):
        yield self.actual_resource_book_ref
        yield self.independent_verdict_ref

    def to_dict(self) -> JsonObject:
        body: JsonObject = {
            "schema_version": "tokenshare.paper_direct_native_artifacts.v2",
            "parser_refs": [ref.to_dict() for ref in self.parser_refs],
            "domain_report_refs": [
                ref.to_dict() for ref in self.domain_report_refs
            ],
        }
        if self.actual_resource_book_ref is not None:
            body["actual_resource_book_ref"] = (
                self.actual_resource_book_ref.to_dict()
            )
        if self.independent_verdict_ref is not None:
            body["independent_verdict_ref"] = self.independent_verdict_ref.to_dict()
        return body


def persist_native_online_direct_artifacts(
    *,
    artifact_store: ArtifactStore,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactRef | None,
    oracle_verdict_ref: ArtifactRef | None,
    oracle_kind: str | None,
    oracle_correct: bool | None,
    oracle_fact: Mapping[str, Any] | None = None,
    current_provider_object_refs: (
        Mapping[str, Sequence[ArtifactRef]] | Sequence[ArtifactRef] | None
    ) = None,
    parser_object_refs: Mapping[str, Sequence[ArtifactRef]] | None = None,
    domain_report_refs: Sequence[ArtifactRef] = (),
) -> NativeDirectArtifactBundle:
    """在 adapter 原生 store 内投影 direct ABI，不重新判断 correctness。"""

    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be ArtifactStore")
    for name, value in (
        ("execution_id", execution_id),
        ("task_id", task_id),
        ("root_unit_id", root_unit_id),
    ):
        _non_empty(name, value)
    if oracle_fact is not None and not isinstance(oracle_fact, Mapping):
        raise TypeError("oracle_fact must be a mapping")
    if final_result_ref is None:
        if any(
            value is not None
            for value in (oracle_verdict_ref, oracle_kind, oracle_correct, oracle_fact)
        ):
            raise ValueError("missing final result cannot carry native oracle verdict")
    else:
        if not isinstance(final_result_ref, ArtifactRef) or not isinstance(
            oracle_verdict_ref,
            ArtifactRef,
        ):
            raise TypeError("native final and oracle refs must be ArtifactRef")
        if oracle_kind not in {"independent_verifier", "lean_checker"}:
            raise ValueError("unsupported native oracle kind")
        if type(oracle_correct) is not bool:
            raise ValueError("oracle_correct must be a bool")
    source_refs: tuple[ArtifactRef, ...]
    if current_provider_object_refs is None:
        source_refs = ()
        provider_refs = ()
    elif isinstance(current_provider_object_refs, Mapping):
        if set(current_provider_object_refs) != set(_ONLINE_PROVIDER_ROLES):
            raise ValueError("native online provider role inventory is incomplete")
        normalized_by_role: dict[str, tuple[ArtifactRef, ...]] = {}
        for role in sorted(current_provider_object_refs):
            values = tuple(current_provider_object_refs[role])
            if not values or any(not isinstance(ref, ArtifactRef) for ref in values):
                raise TypeError("native provider role sources must contain ArtifactRef")
            normalized_by_role[role] = values
        source_refs = tuple(
            ref for role in sorted(normalized_by_role) for ref in normalized_by_role[role]
        )
        provider_refs = ()
    else:
        provider_refs = tuple(current_provider_object_refs)
        if any(not isinstance(ref, ArtifactRef) for ref in provider_refs):
            raise TypeError("current provider refs must contain ArtifactRef")
        source_refs = provider_refs
    normalized_parser_by_role: dict[str, tuple[ArtifactRef, ...]] = {}
    if parser_object_refs is not None:
        if not set(parser_object_refs).issubset(_PARSER_ROLES):
            raise ValueError("unsupported native parser role")
        for role in sorted(parser_object_refs):
            values = tuple(parser_object_refs[role])
            if not values or any(not isinstance(ref, ArtifactRef) for ref in values):
                raise TypeError("native parser role sources must contain ArtifactRef")
            normalized_parser_by_role[role] = values
    parser_source_refs = tuple(
        ref
        for role in sorted(normalized_parser_by_role)
        for ref in normalized_parser_by_role[role]
    )
    normalized_domain_report_refs = tuple(domain_report_refs)
    if any(not isinstance(ref, ArtifactRef) for ref in normalized_domain_report_refs):
        raise TypeError("native domain report refs must contain ArtifactRef")
    identity_refs = tuple(
        ref
        for ref in (final_result_ref, oracle_verdict_ref)
        if isinstance(ref, ArtifactRef)
    )
    authoritative = tuple(
        artifact_store.load_artifact_ref(ref.artifact_id)
        for ref in (
            *identity_refs,
            *source_refs,
            *parser_source_refs,
            *normalized_domain_report_refs,
        )
    )
    supplied = (
        *identity_refs,
        *source_refs,
        *parser_source_refs,
        *normalized_domain_report_refs,
    )
    if any(left.to_dict() != right.to_dict() for left, right in zip(authoritative, supplied)):
        raise ValueError("native direct source artifact identity mismatch")
    if isinstance(current_provider_object_refs, Mapping):
        projected_refs: list[ArtifactRef] = []
        for role in sorted(normalized_by_role):
            values = normalized_by_role[role]
            role_suffix = digest_json(
                {
                    "execution_id": execution_id,
                    "task_id": task_id,
                    "root_unit_id": root_unit_id,
                    "role": role,
                    "source_artifact_refs": [ref.to_dict() for ref in values],
                }
            ).removeprefix("sha256:")[:24]
            projected_refs.append(
                artifact_store.save_json(
                    {
                        "schema_version": "tokenshare.paper_current_provider_role_book.v1",
                        "role": role,
                        "source_artifact_refs": [ref.to_dict() for ref in values],
                    },
                    artifact_id=f"paper_current_provider_{role}_{role_suffix}",
                    artifact_type="PaperCurrentProviderRoleBook",
                    artifact_schema_id="tokenshare.paper_current_provider_role_book.v1",
                    artifact_schema_version="v1",
                    source={
                        "kind": "native_online_provider_role_projection",
                        "role": role,
                        "execution_id": execution_id,
                        "task_id": task_id,
                        "source_artifact_refs": [ref.to_dict() for ref in values],
                    },
                    metadata={"source_artifact_count": len(values)},
                    created_at=values[0].created_at,
                )
            )
        provider_refs = tuple(projected_refs)
    parser_refs: tuple[ArtifactRef, ...] = ()
    if normalized_parser_by_role:
        projected_parser_refs: list[ArtifactRef] = []
        for role in sorted(normalized_parser_by_role):
            values = normalized_parser_by_role[role]
            role_suffix = digest_json(
                {
                    "execution_id": execution_id,
                    "task_id": task_id,
                    "root_unit_id": root_unit_id,
                    "role": role,
                    "source_artifact_refs": [ref.to_dict() for ref in values],
                }
            ).removeprefix("sha256:")[:24]
            projected_parser_refs.append(
                artifact_store.save_json(
                    {
                        "schema_version": "tokenshare.paper_parser_role_book.v1",
                        "role": role,
                        "source_artifact_refs": [ref.to_dict() for ref in values],
                    },
                    artifact_id=f"paper_parser_{role}_{role_suffix}",
                    artifact_type="PaperParserRoleBook",
                    artifact_schema_id="tokenshare.paper_parser_role_book.v1",
                    artifact_schema_version="v1",
                    source={
                        "kind": "native_parser_role_projection",
                        "role": role,
                        "execution_id": execution_id,
                        "task_id": task_id,
                        "source_artifact_refs": [ref.to_dict() for ref in values],
                    },
                    metadata={"source_artifact_count": len(values)},
                    created_at=values[0].created_at,
                )
            )
        parser_refs = tuple(projected_parser_refs)
    if current_provider_object_refs is not None:
        provider_roles = tuple(
            str(ref.source.get("role", "")) for ref in provider_refs
        )
        if frozenset(provider_roles) != _ONLINE_PROVIDER_ROLES or len(
            provider_roles
        ) != len(_ONLINE_PROVIDER_ROLES):
            raise ValueError("native online provider role inventory is incomplete")

    identity_suffix = digest_json(
        {
            "execution_id": execution_id,
            "task_id": task_id,
            "root_unit_id": root_unit_id,
            "final": (
                final_result_ref.to_dict()
                if isinstance(final_result_ref, ArtifactRef)
                else None
            ),
        }
    ).removeprefix("sha256:")[:24]
    resource_ref: ArtifactRef | None = None
    if current_provider_object_refs is not None:
        resource_body: JsonObject = {
            "schema_version": _ACTUAL_RESOURCE_BOOK_SCHEMA,
            "execution_id": execution_id,
            "task_id": task_id,
            "root_unit_id": root_unit_id,
            "current_provider_object_refs": [ref.to_dict() for ref in provider_refs],
        }
        if parser_refs:
            resource_body["parser_refs"] = [ref.to_dict() for ref in parser_refs]
        created_at_ref = final_result_ref or source_refs[0]
        resource_ref = artifact_store.save_json(
            resource_body,
            artifact_id=f"paper_actual_resource_book_{identity_suffix}",
            artifact_type="PaperActualResourceBook",
            artifact_schema_id=_ACTUAL_RESOURCE_BOOK_SCHEMA,
            artifact_schema_version="v1",
            source={
                "kind": "native_online_direct_projection",
                "role": "actual_resource_book",
                "execution_id": execution_id,
                "task_id": task_id,
            },
            metadata={
                "provider_object_count": len(provider_refs),
                "parser_object_count": len(parser_refs),
            },
            created_at=created_at_ref.created_at,
        )
    # correctness 不能由 adapter 传入的 bool 再包装成自报 verdict。这里仅携带
    # domain verifier/checker 的原生 artifact；canonical closure 会实际读取它。
    verdict_ref = (
        oracle_verdict_ref if isinstance(oracle_verdict_ref, ArtifactRef) else None
    )
    return NativeDirectArtifactBundle(
        actual_resource_book_ref=resource_ref,
        independent_verdict_ref=verdict_ref,
        parser_refs=parser_refs,
        domain_report_refs=normalized_domain_report_refs,
    )


def build_canonical_direct_evidence(
    *,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    event_ledger: EventLedger,
    artifact_store: ArtifactStore,
    runtime_result: ProtocolRunResult,
    final_result_ref: ArtifactRef | None,
    parser_refs: Sequence[ArtifactRef],
    verifier_checker_refs: Sequence[ArtifactRef],
    current_provider_object_refs: Sequence[ArtifactRef] = (),
    source_bank_object_locators: Sequence[ExternalBankObjectLocator] = (),
    actual_resource_book_ref: ArtifactRef | None = None,
    trace_resource_book_ref: ArtifactRef | None = None,
) -> CanonicalDirectRootEvidence:
    """校验 typed canonical runtime 并形成调用方不可写 gate 的冻结 evidence。"""

    if not isinstance(inventory_row, PaperDirectRootInventoryRow):
        raise TypeError("inventory_row must be PaperDirectRootInventoryRow")
    preregistered_root_run_id = inventory_row.preregistered_root_run_id
    evidence_class = inventory_row.evidence_class
    _non_empty("execution_id", execution_id)
    if evidence_class not in {
        "online_real_provider",
        "real_model_trace_protocol_run",
        "regression_only",
    }:
        raise ValueError("unsupported evidence class")
    if not isinstance(event_ledger, EventLedger):
        raise TypeError("event_ledger must be EventLedger")
    if not isinstance(artifact_store, ArtifactStore):
        raise TypeError("artifact_store must be ArtifactStore")
    if not isinstance(runtime_result, ProtocolRunResult):
        raise TypeError("runtime_result must be ProtocolRunResult")
    if runtime_result.run_id != execution_id:
        raise ValueError("runtime result execution binding mismatch")
    if runtime_result.ledger_binding is None:
        raise ValueError("runtime result producer ledger binding is required")
    task_id = runtime_result.task_id
    root_unit_id = runtime_result.root_unit_id
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("runtime result task binding is missing")
    if not isinstance(root_unit_id, str) or not root_unit_id:
        raise ValueError("runtime result root unit binding is missing")

    events = tuple(event_ledger.read_all())
    if not events or not event_ledger.verify_hash_chain():
        raise ValueError("ledger hash chain verification failed")
    if len({event.event_seq for event in events}) != len(events):
        raise ValueError("duplicate ledger event sequence")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("duplicate ledger event id")
    if len({event.event_hash for event in events}) != len(events):
        raise ValueError("duplicate ledger event hash")
    if any(event.schema_version != "LedgerEvent.v2" for event in events):
        raise ValueError("unsupported ledger event schema")
    if any(event.task_id != task_id for event in events):
        raise ValueError("ledger task binding mismatch")

    canonical_result = project_protocol_run(
        run_id=execution_id,
        task_id=task_id,
        root_unit_id=root_unit_id,
        event_ledger=event_ledger,
        artifact_store=artifact_store,
    )
    if canonical_result.ledger_binding != runtime_result.ledger_binding:
        raise ValueError("runtime result producer ledger binding mismatch")
    if (
        canonical_result.status != runtime_result.status
        or canonical_result.event_refs != runtime_result.event_refs
        or _artifact_triples(canonical_result.artifact_refs)
        != _artifact_triples(runtime_result.artifact_refs)
    ):
        raise ValueError("runtime result does not match canonical ledger projection")
    _validate_runtime_summary(runtime_result, canonical_result.summary)

    completed_runtime = canonical_result.status == "completed"
    if completed_runtime and final_result_ref is None:
        raise ValueError("completed runtime requires final result artifact")
    final_snapshot = _optional_snapshot_artifact(
        final_result_ref,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles={"final_result"},
    )
    if final_snapshot is not None and _snapshot_triple(final_snapshot) not in set(
        _artifact_triples(canonical_result.artifact_refs)
    ):
        raise ValueError("final artifact is not bound by canonical runtime result")
    parser_snapshots = _snapshot_artifacts(
        parser_refs,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles={"parser_result", "parse_failure"},
        field_name="parser_refs",
    )
    verifier_snapshots = _snapshot_artifacts(
        verifier_checker_refs,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles=_VERIFIER_ROLES,
        field_name="verifier_checker_refs",
    )
    current_snapshots = _snapshot_artifacts(
        current_provider_object_refs,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles=_ONLINE_PROVIDER_ROLES,
        field_name="current_provider_object_refs",
    )
    actual_snapshot = _optional_snapshot_artifact(
        actual_resource_book_ref,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles={"actual_resource_book"},
    )
    trace_snapshot = _optional_snapshot_artifact(
        trace_resource_book_ref,
        artifact_store=artifact_store,
        task_id=task_id,
        execution_id=execution_id,
        allowed_roles={"trace_resource_book"},
    )

    locators = _canonical_locators(source_bank_object_locators)
    if current_snapshots and locators:
        raise ValueError("current provider refs and source locators are mutually exclusive")
    if actual_snapshot is not None and trace_snapshot is not None:
        raise ValueError("online and trace resource books are mutually exclusive")

    reasons: list[str] = []
    if evidence_class == "online_real_provider":
        if locators:
            raise ValueError("online evidence cannot contain source locators")
        _append_role_completeness_reasons(
            _roles(current_snapshots),
            allowed=_ONLINE_PROVIDER_ROLES,
            reasons=reasons,
            prefix="current_provider",
        )
        if actual_snapshot is None or trace_snapshot is not None:
            reasons.append("invalid_online_resource_book")
    elif evidence_class == "real_model_trace_protocol_run":
        if current_snapshots:
            raise ValueError("trace evidence cannot contain current provider refs")
        _append_locator_role_completeness_reasons(
            locators,
            allowed=_TRACE_SOURCE_ROLES,
            reasons=reasons,
            prefix="source_bank",
        )
        if trace_snapshot is None or actual_snapshot is not None:
            reasons.append("invalid_trace_resource_book")
    else:
        if current_snapshots:
            raise ValueError("regression_only evidence cannot contain current refs")
        _append_locator_role_completeness_reasons(
            locators,
            allowed=_REGRESSION_SOURCE_ROLES,
            reasons=reasons,
            prefix="regression_source",
        )
        if trace_snapshot is None or actual_snapshot is not None:
            reasons.append("invalid_regression_resource_book")
        reasons.append("regression_only")

    event_snapshots = tuple(_snapshot_event(event) for event in events)
    attempt_refs = tuple(
        snapshot
        for snapshot in event_snapshots
        if snapshot.event_type in _ATTEMPT_EVENT_TYPES
    )
    attempt_types = frozenset(ref.event_type for ref in attempt_refs)
    missing_attempt_types = sorted(_ATTEMPT_EVENT_TYPES - attempt_types)
    canonical_unit_ids = _canonical_task_unit_ids(canonical_result.summary)
    attempt_identity_consistent = False
    if missing_attempt_types:
        reasons.append("missing_attempt_events:" + ",".join(missing_attempt_types))
    else:
        attempt_identity_consistent = _has_bound_attempt_chain(
            events,
            task_id,
            canonical_unit_ids,
        )
        if not attempt_identity_consistent:
            reasons.append("missing_bound_attempt_chain")
    if "parser_result" not in _roles(parser_snapshots):
        reasons.append("missing_parser_evidence")

    verdict_ref = _select_verdict_ref(verifier_snapshots)
    independently_correct = False
    if completed_runtime and verdict_ref is None:
        reasons.append("missing_independent_verdict")
    elif completed_runtime and verdict_ref is not None and final_snapshot is not None:
        independently_correct = _read_bound_verdict(
            verdict_ref,
            verifier_checker_refs,
            artifact_store=artifact_store,
            inventory_row=inventory_row,
            execution_id=execution_id,
            task_id=task_id,
            root_unit_id=root_unit_id,
            final_result_ref=final_snapshot,
            events=tuple(event.to_dict() for event in events),
        )

    canonical_event = (
        _select_canonical_event(events, root_unit_id, final_snapshot)
        if final_snapshot is not None
        else None
    )
    merge_event = (
        _select_merge_event(events, root_unit_id, final_snapshot)
        if final_snapshot is not None
        else None
    )
    if final_snapshot is not None and canonical_event is None and merge_event is not None:
        canonical_event, merge_event = _select_merge_unit_final_provenance(
            events,
            root_unit_id=root_unit_id,
            final_ref=final_snapshot,
        )
    terminal_event = _select_terminal_event(
        events,
        root_unit_id,
        canonical_result.status,
    )
    canonical_snapshot = (
        _snapshot_event(canonical_event) if canonical_event is not None else None
    )
    merge_snapshot = _snapshot_event(merge_event) if merge_event is not None else None
    terminal_snapshot = (
        _snapshot_event(terminal_event) if terminal_event is not None else None
    )
    if completed_runtime and canonical_event is None:
        reasons.append("missing_canonical_provenance")
    if completed_runtime and merge_event is None:
        reasons.append("missing_merge_provenance")
    if terminal_event is None:
        reasons.append("missing_terminal_root_provenance")
    if (
        canonical_event is not None
        and merge_event is not None
        and terminal_event is not None
        and not (
            canonical_event.event_seq
            < merge_event.event_seq
            < terminal_event.event_seq
        )
    ):
        canonical_snapshot = None
        merge_snapshot = None
        terminal_snapshot = None
        reasons.append("invalid_terminal_provenance_order")

    all_snapshots = _unique_artifact_snapshots(
        (
            *((final_snapshot,) if final_snapshot is not None else ()),
            *parser_snapshots,
            *verifier_snapshots,
            *current_snapshots,
            *(
                (actual_snapshot,) if actual_snapshot is not None else ()
            ),
            *((trace_snapshot,) if trace_snapshot is not None else ()),
            *(
                _snapshot_artifact(
                    ref,
                    artifact_store=artifact_store,
                    task_id=task_id,
                    execution_id=execution_id,
                    allowed_roles=None,
                )
                for ref in runtime_result.artifact_refs
            ),
        )
    )
    ledger_digest = digest_json([event.to_dict() for event in event_snapshots])
    binding_body = {
        "schema_version": "tokenshare.direct_root_execution_binding.v1",
        "preregistered_root_run_id": preregistered_root_run_id,
        "execution_id": execution_id,
        "task_id": task_id,
        "root_unit_id": root_unit_id,
        "ledger_digest": ledger_digest,
        "events": [event.to_dict() for event in event_snapshots],
    }
    binding = DirectRootExecutionBinding(
        preregistered_root_run_id=preregistered_root_run_id,
        execution_id=execution_id,
        task_id=task_id,
        root_unit_id=root_unit_id,
        ledger_digest=ledger_digest,
        events=event_snapshots,
        binding_digest=digest_json(binding_body),
    )
    reasons_tuple = tuple(dict.fromkeys(reasons))
    evidence_complete = not any(
        reason != "regression_only" for reason in reasons_tuple
    )
    return CanonicalDirectRootEvidence._from_validated(
        _factory_token=_CANONICAL_DIRECT_EVIDENCE_FACTORY_TOKEN,
        preregistered_root_run_id=preregistered_root_run_id,
        evidence_class=evidence_class,
        execution_binding=binding,
        canonical_runtime_status=canonical_result.status,
        final_result_ref=final_snapshot,
        terminal_root_event_ref=terminal_snapshot,
        canonical_acceptance_ref=canonical_snapshot,
        merge_ref=merge_snapshot,
        independently_verified_correct=independently_correct,
        paper_evidence_complete=evidence_complete,
        identity_consistent=attempt_identity_consistent,
        infrastructure_valid=evidence_complete,
        attempt_refs=attempt_refs,
        event_refs=event_snapshots,
        parser_refs=parser_snapshots,
        verifier_checker_refs=verifier_snapshots,
        artifact_refs=all_snapshots,
        current_provider_object_refs=current_snapshots,
        source_bank_object_locators=locators,
        actual_resource_book_ref=actual_snapshot,
        trace_resource_book_ref=trace_snapshot,
        ineligibility_reasons=reasons_tuple,
        schema_version=CANONICAL_DIRECT_EVIDENCE_SCHEMA_VERSION,
    )


def project_paper_direct_results(
    *,
    root_inventory_manifest: PreregisteredRootInventoryManifest,
    condition_manifests: Sequence[Mapping[str, Any]],
    catalog_manifests: Sequence[Mapping[str, Any]],
    canonical_runtime_evidence: Sequence[CanonicalDirectRootEvidence],
) -> PaperDirectProjection:
    if not isinstance(
        root_inventory_manifest, PreregisteredRootInventoryManifest
    ):
        raise TypeError("complete PreregisteredRootInventoryManifest is required")
    rows = root_inventory_manifest.rows
    root_ids = tuple(row.preregistered_root_run_id for row in rows)
    evidence_values = tuple(canonical_runtime_evidence)
    if any(
        not isinstance(value, CanonicalDirectRootEvidence)
        for value in evidence_values
    ):
        raise TypeError("observed values must come from canonical evidence factory")
    if any(not value.producer_validated for value in evidence_values):
        raise ValueError("observed values must come from canonical evidence factory")
    observed_root_ids = tuple(
        value.preregistered_root_run_id for value in evidence_values
    )
    if len(set(observed_root_ids)) != len(observed_root_ids):
        raise ValueError("duplicate observed root evidence")
    observed_by_root_id = {
        value.preregistered_root_run_id: value for value in evidence_values
    }
    observed_ids = set(observed_root_ids)
    foreign = sorted(observed_ids - set(root_ids))
    if foreign:
        raise ValueError("foreign observed root evidence: " + ", ".join(foreign))
    condition_index = _condition_index(condition_manifests)
    catalog_index = _catalog_index(catalog_manifests)
    direct_rows: list[PaperDirectRootResult] = []
    for row in rows:
        _validate_condition_binding(row, condition_index)
        _validate_case_binding(row, catalog_index)
        evidence = observed_by_root_id.get(row.preregistered_root_run_id)
        direct_rows.append(
            _not_started_row(row)
            if evidence is None
            else _observed_row(row, evidence)
        )
    return PaperDirectProjection(
        inventory_id=root_inventory_manifest.inventory_id,
        inventory_digest=root_inventory_manifest.inventory_digest,
        rows=tuple(direct_rows),
        denominator_inventory_ids=root_ids,
        _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
    )


def build_direct_boolean_aggregate(
    projection: PaperDirectProjection,
    *,
    outcome_field: str,
) -> PaperDirectBooleanAggregate:
    if not isinstance(projection, PaperDirectProjection):
        raise TypeError("projection must be PaperDirectProjection")
    if not projection._factory_validated:
        raise ValueError("aggregate requires canonical factory projection")
    if outcome_field not in {
        "final_result_reference_complete",
        "end_to_end_verified_success",
    }:
        raise ValueError("unsupported direct boolean outcome")
    blocked = any(
        row.ineligibility_reasons
        or not row.identity_consistent
        or not row.infrastructure_valid
        or (
            row.root_status != PaperDirectRootStatus.NOT_STARTED.value
            and not row.paper_evidence_complete
        )
        for row in projection.rows
    )
    numerator = tuple(
        row.preregistered_root_run_id
        for row in projection.rows
        if getattr(row, outcome_field)
    )
    denominator = projection.denominator_inventory_ids
    return PaperDirectBooleanAggregate(
        value=None if blocked else len(numerator) / len(denominator),
        status="blocked" if blocked else "computed",
        denominator_inventory_ids=denominator,
        numerator_inventory_ids=numerator,
        audit_denominator_count=len(denominator),
    )


def _observed_row(
    inventory: PaperDirectRootInventoryRow,
    evidence: CanonicalDirectRootEvidence,
) -> PaperDirectRootResult:
    if evidence.preregistered_root_run_id != inventory.preregistered_root_run_id:
        raise ValueError("root evidence inventory identity mismatch")
    if evidence.evidence_class != inventory.evidence_class:
        raise ValueError("root evidence class does not match frozen inventory")
    reasons = evidence.ineligibility_reasons
    status = evidence.canonical_runtime_status
    if status not in _DIRECT_RUNTIME_STATUSES:
        reasons = (*reasons, "unsupported_canonical_runtime_status")
    root_status = "ineligible" if reasons else status
    complete = all(
        (
            evidence.canonical_runtime_status == "completed",
            evidence.final_result_ref is not None,
            evidence.terminal_root_event_ref is not None,
            evidence.canonical_acceptance_ref is not None,
            evidence.merge_ref is not None,
            evidence.identity_consistent,
        )
    )
    success = all(
        (
            root_status == PaperDirectRootStatus.COMPLETED.value,
            complete,
            evidence.independently_verified_correct,
            evidence.paper_evidence_complete,
            evidence.identity_consistent,
            evidence.infrastructure_valid,
            not reasons,
        )
    )
    return PaperDirectRootResult(
        preregistered_root_run_id=inventory.preregistered_root_run_id,
        experiment_id=inventory.experiment_id,
        condition_id=inventory.condition_id,
        preregistered_condition_ref=inventory.preregistered_condition_ref,
        condition_axes=inventory.condition_axes,
        case_id=inventory.case_id,
        preregistered_case_ref=inventory.preregistered_case_ref,
        repeat_id=inventory.repeat_id,
        evidence_class=inventory.evidence_class,
        root_status=root_status,
        execution_binding=evidence.execution_binding,
        final_result_ref=evidence.final_result_ref,
        terminal_root_event_ref=evidence.terminal_root_event_ref,
        canonical_acceptance_ref=evidence.canonical_acceptance_ref,
        merge_ref=evidence.merge_ref,
        final_result_reference_complete=complete,
        independently_verified_correct=evidence.independently_verified_correct,
        paper_evidence_complete=evidence.paper_evidence_complete,
        identity_consistent=evidence.identity_consistent,
        end_to_end_verified_success=success,
        infrastructure_valid=evidence.infrastructure_valid,
        attempt_refs=evidence.attempt_refs,
        event_refs=evidence.event_refs,
        parser_refs=evidence.parser_refs,
        verifier_checker_refs=evidence.verifier_checker_refs,
        artifact_refs=evidence.artifact_refs,
        current_provider_object_refs=evidence.current_provider_object_refs,
        source_bank_object_locators=evidence.source_bank_object_locators,
        actual_resource_book_ref=evidence.actual_resource_book_ref,
        trace_resource_book_ref=evidence.trace_resource_book_ref,
        ineligibility_reasons=reasons,
        _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
    )


def _not_started_row(inventory: PaperDirectRootInventoryRow) -> PaperDirectRootResult:
    return PaperDirectRootResult(
        preregistered_root_run_id=inventory.preregistered_root_run_id,
        experiment_id=inventory.experiment_id,
        condition_id=inventory.condition_id,
        preregistered_condition_ref=inventory.preregistered_condition_ref,
        condition_axes=inventory.condition_axes,
        case_id=inventory.case_id,
        preregistered_case_ref=inventory.preregistered_case_ref,
        repeat_id=inventory.repeat_id,
        evidence_class=inventory.evidence_class,
        root_status=PaperDirectRootStatus.NOT_STARTED.value,
        execution_binding=None,
        final_result_ref=None,
        terminal_root_event_ref=None,
        canonical_acceptance_ref=None,
        merge_ref=None,
        final_result_reference_complete=False,
        independently_verified_correct=False,
        paper_evidence_complete=False,
        identity_consistent=True,
        end_to_end_verified_success=False,
        infrastructure_valid=True,
        attempt_refs=(),
        event_refs=(),
        parser_refs=(),
        verifier_checker_refs=(),
        artifact_refs=(),
        current_provider_object_refs=(),
        source_bank_object_locators=(),
        actual_resource_book_ref=None,
        trace_resource_book_ref=None,
        ineligibility_reasons=(),
        _factory_token=_DIRECT_RESULT_FACTORY_TOKEN,
    )


def _condition_index(
    manifests: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    index: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for manifest in manifests:
        _exact_keys(
            manifest,
            {
                "schema_version",
                "records",
                "condition_manifest_digest",
            },
            "condition manifest",
        )
        if manifest["schema_version"] != _CONDITION_MANIFEST_SCHEMA:
            raise ValueError("unsupported condition manifest schema")
        body = {
            "schema_version": manifest["schema_version"],
            "records": manifest["records"],
        }
        if digest_json(body) != manifest["condition_manifest_digest"]:
            raise ValueError("condition manifest digest mismatch")
        records = manifest["records"]
        if not isinstance(records, (list, tuple)) or not records:
            raise ValueError("condition manifest records must be non-empty")
        for record in records:
            _exact_keys(
                record,
                {
                    "schema_version",
                    "condition_id",
                    "condition_axes",
                    "condition_axes_digest",
                    "condition_record_digest",
                },
                "condition record",
            )
            if record["schema_version"] != _CONDITION_RECORD_SCHEMA:
                raise ValueError("unsupported condition record schema")
            record_body = {
                key: value
                for key, value in record.items()
                if key != "condition_record_digest"
            }
            if digest_json(record_body) != record["condition_record_digest"]:
                raise ValueError("condition record digest mismatch")
            _validate_condition_axes(record["condition_axes"])
            if digest_json(record["condition_axes"]) != record["condition_axes_digest"]:
                raise ValueError("condition axes digest mismatch")
            condition_id = record["condition_id"]
            if condition_id in index:
                raise ValueError("duplicate condition id")
            index[condition_id] = (manifest, record)
    return index


def _catalog_index(
    manifests: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    index: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for manifest in manifests:
        _exact_keys(
            manifest,
            {"schema_version", "records", "catalog_digest"},
            "catalog manifest",
        )
        if manifest["schema_version"] != _CATALOG_MANIFEST_SCHEMA:
            raise ValueError("unsupported catalog manifest schema")
        body = {"schema_version": manifest["schema_version"], "records": manifest["records"]}
        if digest_json(body) != manifest["catalog_digest"]:
            raise ValueError("catalog digest mismatch")
        records = manifest["records"]
        if not isinstance(records, (list, tuple)) or not records:
            raise ValueError("catalog records must be non-empty")
        for record in records:
            expected_record_keys = {
                "schema_version",
                "case_id",
                "domain",
                "difficulty",
                "case_axes_digest",
                "factor_position_quantile",
                "position_stratum",
                "case_record_digest",
            }
            if isinstance(record, Mapping) and record.get("domain") == "lean_proof":
                expected_record_keys.update(
                    {
                        "official_case_digest",
                        "official_root_theorem_id",
                        "official_root_theorem_payload_digest",
                    }
                )
            _exact_keys(record, expected_record_keys, "case record")
            expected_record_schema = (
                _LEAN_CASE_RECORD_SCHEMA
                if record["domain"] == "lean_proof"
                else _CASE_RECORD_SCHEMA
            )
            if record["schema_version"] != expected_record_schema:
                raise ValueError("unsupported case record schema")
            record_body = {
                key: value
                for key, value in record.items()
                if key != "case_record_digest"
            }
            if digest_json(record_body) != record["case_record_digest"]:
                raise ValueError("case record digest mismatch")
            _validate_case_identity_axes(record)
            if record["domain"] == "lean_proof":
                for field_name in (
                    "official_case_digest",
                    "official_root_theorem_payload_digest",
                ):
                    value = record[field_name]
                    if (
                        not isinstance(value, str)
                        or not value.startswith("sha256:")
                        or len(value) != 71
                    ):
                        raise ValueError(
                            f"Lean case record {field_name} is invalid"
                        )
                if (
                    not isinstance(record["official_root_theorem_id"], str)
                    or not record["official_root_theorem_id"]
                ):
                    raise ValueError("Lean case record theorem identity is missing")
            case_axes = {
                "factor_position_quantile": record["factor_position_quantile"],
                "position_stratum": record["position_stratum"],
            }
            if digest_json(case_axes) != record["case_axes_digest"]:
                raise ValueError("case axes digest mismatch")
            quantile = record["factor_position_quantile"]
            if type(quantile) in {int, float}:
                if not math.isfinite(float(quantile)) or not 0 <= quantile <= 1:
                    raise ValueError("factor_position_quantile must be finite in [0, 1]")
            elif not isinstance(quantile, str) or not quantile:
                raise ValueError("factor_position_quantile must be finite or non-empty")
            if (
                not isinstance(record["position_stratum"], str)
                or not record["position_stratum"]
            ):
                raise ValueError("position_stratum must be non-empty")
            case_id = record["case_id"]
            if case_id in index:
                raise ValueError("duplicate case id")
            index[case_id] = (manifest, record)
    return index


def _validate_condition_binding(
    row: PaperDirectRootInventoryRow,
    index: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> None:
    found = index.get(row.condition_id)
    if found is None:
        raise ValueError("condition manifest is missing inventory row")
    manifest, record = found
    ref = row.preregistered_condition_ref
    _exact_keys(
        ref,
        {
            "schema_version",
            "condition_manifest_digest",
            "condition_record_digest",
            "condition_axes_digest",
        },
        "condition ref",
    )
    if ref["schema_version"] != _CONDITION_REF_SCHEMA:
        raise ValueError("unsupported condition ref schema")
    expected = {
        "schema_version": _CONDITION_REF_SCHEMA,
        "condition_manifest_digest": manifest["condition_manifest_digest"],
        "condition_record_digest": record["condition_record_digest"],
        "condition_axes_digest": record["condition_axes_digest"],
    }
    if _thaw(ref) != expected or _thaw(row.condition_axes) != record["condition_axes"]:
        raise ValueError("condition ref and axes are not digest-bound to manifest")


def _validate_case_binding(
    row: PaperDirectRootInventoryRow,
    index: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> None:
    found = index.get(row.case_id)
    if found is None:
        raise ValueError("catalog is missing inventory case")
    manifest, record = found
    ref = row.preregistered_case_ref
    expected_ref_keys = {
        "schema_version",
        "catalog_digest",
        "case_record_digest",
        "case_axes_digest",
        "factor_position_quantile",
        "position_stratum",
    }
    if record["domain"] == "lean_proof":
        expected_ref_keys.update(
            {
                "official_case_digest",
                "official_root_theorem_id",
                "official_root_theorem_payload_digest",
            }
        )
    _exact_keys(ref, expected_ref_keys, "case ref")
    expected_ref_schema = (
        _LEAN_CASE_REF_SCHEMA
        if record["domain"] == "lean_proof"
        else _CASE_REF_SCHEMA
    )
    expected = {
        "schema_version": expected_ref_schema,
        "catalog_digest": manifest["catalog_digest"],
        "case_record_digest": record["case_record_digest"],
        "case_axes_digest": record["case_axes_digest"],
        "factor_position_quantile": record["factor_position_quantile"],
        "position_stratum": record["position_stratum"],
        **(
            {
                "official_case_digest": record["official_case_digest"],
                "official_root_theorem_id": record[
                    "official_root_theorem_id"
                ],
                "official_root_theorem_payload_digest": record[
                    "official_root_theorem_payload_digest"
                ],
            }
            if record["domain"] == "lean_proof"
            else {}
        ),
    }
    if _thaw(ref) != expected:
        raise ValueError("case ref is not digest-bound to catalog record")


def _validate_case_identity_axes(record: Mapping[str, Any]) -> None:
    domain = record["domain"]
    difficulty = record["difficulty"]
    if domain == "factorization":
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("invalid factorization case axes")
        return
    if domain == "lean_proof":
        if difficulty not in {"simple", "medium_lemma_dag", "hard_frontier"}:
            raise ValueError("invalid Lean case axes")
        return
    raise ValueError("unsupported case domain")


def _validate_condition_axes(value: Any) -> None:
    if not isinstance(value, Mapping) or tuple(value) != CONDITION_AXIS_KEYS:
        raise ValueError("condition axes must have exact canonical keys and order")
    domain = value["domain"]
    difficulty = value["difficulty"]
    topic = value["topic_family"]
    if domain not in {"factorization", "lean_proof"}:
        raise ValueError("unsupported condition domain")
    if domain == "factorization":
        if difficulty not in {"easy", "medium", "hard"} or topic is not None:
            raise ValueError("invalid factorization axes")
    elif difficulty not in {"simple", "medium_lemma_dag", "hard_frontier"} or topic not in {
        "pure_logic",
        "function_set",
        "induction",
        LEAN_MIXED_TOPIC_FAMILY_AXIS,
    }:
        raise ValueError("invalid Lean axes")
    _strict_int("worker_count", value["worker_count"], minimum=1)
    if value["sample_slot_index"] is not None:
        _strict_int("sample_slot_index", value["sample_slot_index"], minimum=0)
    _nullable_member(
        "fault_condition",
        value["fault_condition"],
        {"false_positive", "false_negative", "no_return", "late_submission", "executor_error"},
    )
    _nullable_member("death_condition", value["death_condition"], {"worker_death"})
    _nullable_member(
        "ablation_mode",
        value["ablation_mode"],
        {"FULL", "NO_VERIFICATION", "NO_PARSER_POLICY", "NO_REQUEUE", "NO_MERGE_GATE"},
    )
    endpoint = value["model_endpoint_id"]
    if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
        raise ValueError("model_endpoint_id must be non-empty or null")


def _validate_runtime_summary(
    runtime_result: ProtocolRunResult,
    canonical_summary: Mapping[str, Any],
) -> None:
    summary = runtime_result.summary
    if not isinstance(summary, Mapping):
        raise TypeError("runtime result summary must be a mapping")
    extra_keys = set(summary) - set(canonical_summary)
    unknown_extra_keys = sorted(extra_keys - _COORDINATOR_SUMMARY_KEYS)
    if unknown_extra_keys:
        raise ValueError(
            "runtime result summary contains unknown fields: "
            + ",".join(unknown_extra_keys)
        )
    normalized = {
        key: value
        for key, value in summary.items()
        if key not in extra_keys
    }
    if normalized != dict(canonical_summary):
        raise ValueError("runtime result summary does not match canonical ledger projection")

    runtime_observation = summary.get("runtime_observation")
    if runtime_observation is not None:
        _exact_keys(
            runtime_observation,
            _RUNTIME_OBSERVATION_KEYS,
            "runtime observation",
        )
        rebuilt = build_runtime_observation(
            run_id=runtime_observation["run_id"],
            runtime_started_at=runtime_observation["runtime_started_at"],
            runtime_ended_at=runtime_observation["runtime_ended_at"],
            planned_ai_unit_ids=runtime_observation["planned_ai_unit_ids"],
            dispatched_ai_unit_ids=runtime_observation["dispatched_ai_unit_ids"],
            completed_ai_unit_ids=runtime_observation["completed_ai_unit_ids"],
            worker_execution_facts=runtime_observation["worker_execution_facts"],
            in_flight_ai_unit_ids_at_witness=runtime_observation[
                "in_flight_ai_unit_ids_at_witness"
            ],
            witness_observed_at=runtime_observation["witness_observed_at"],
        )
        if (
            rebuilt != runtime_observation
            or runtime_observation["run_id"] != runtime_result.run_id
        ):
            raise ValueError("runtime result summary has invalid runtime observation")

    hook_observations = summary.get("runtime_hook_observations")
    if hook_observations is not None:
        if not isinstance(hook_observations, list) or not hook_observations:
            raise ValueError("runtime result summary has invalid hook observations")
        try:
            rebuilt_hook_observations = [
                RuntimeHookObservationV1.from_dict(value).to_dict()
                for value in hook_observations
            ]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "runtime result summary has invalid hook observations"
            ) from exc
        if rebuilt_hook_observations != hook_observations:
            raise ValueError(
                "runtime result summary has noncanonical hook observations"
            )

    present_partial_keys = set(summary) & _PARTIAL_OBSERVATION_KEYS
    if present_partial_keys:
        if present_partial_keys != _PARTIAL_OBSERVATION_KEYS:
            raise ValueError("runtime result summary has incomplete partial observation")
        selected_ids = summary["selected_ai_unit_ids"]
        if (
            runtime_result.status != "partial"
            or summary["execution_scope"] != "selected_ai_units"
            or summary["partial_observation"] is not True
            or not isinstance(selected_ids, list)
            or not selected_ids
            or any(not isinstance(value, str) or not value for value in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)
        ):
            raise ValueError("runtime result summary has invalid partial observation")


def _snapshot_artifact(
    ref: ArtifactRef,
    *,
    artifact_store: ArtifactStore,
    task_id: str,
    execution_id: str,
    allowed_roles: set[str] | frozenset[str] | None,
) -> ArtifactIdentitySnapshot:
    if not isinstance(ref, ArtifactRef):
        raise TypeError("artifact evidence must use ArtifactRef")
    if not artifact_store.verify(ref):
        raise ValueError("artifact reference verification failed")
    source = ref.source
    if not isinstance(source, Mapping):
        raise ValueError("artifact source binding is missing")
    role = source.get("role")
    if source.get("task_id") != task_id or source.get("execution_id") != execution_id:
        raise ValueError("artifact task binding or execution binding mismatch")
    if not _artifact_manifest_matches(ref, artifact_store):
        raise ValueError("artifact reference verification failed")
    if not isinstance(role, str) or not role:
        raise ValueError("artifact source role is missing")
    if allowed_roles is not None and role not in allowed_roles:
        raise ValueError("artifact source role is not allowed")
    return ArtifactIdentitySnapshot(
        artifact_id=ref.artifact_id,
        artifact_type=ref.artifact_type,
        uri=ref.uri,
        content_hash=ref.content_hash,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
        artifact_schema_id=ref.artifact_schema_id,
        artifact_schema_version=ref.artifact_schema_version,
        source_role=role,
        source_task_id=task_id,
        source_execution_id=execution_id,
        created_at=ref.created_at,
    )


def _snapshot_artifacts(
    refs: Sequence[ArtifactRef],
    *,
    artifact_store: ArtifactStore,
    task_id: str,
    execution_id: str,
    allowed_roles: set[str] | frozenset[str],
    field_name: str,
) -> tuple[ArtifactIdentitySnapshot, ...]:
    snapshots = tuple(
        _snapshot_artifact(
            ref,
            artifact_store=artifact_store,
            task_id=task_id,
            execution_id=execution_id,
            allowed_roles=allowed_roles,
        )
        for ref in refs
    )
    roles = [snapshot.source_role for snapshot in snapshots]
    if len(set(roles)) != len(roles):
        raise ValueError(f"duplicate {field_name} role")
    identities = [
        (snapshot.artifact_id, snapshot.content_hash, snapshot.size_bytes)
        for snapshot in snapshots
    ]
    if len(set(identities)) != len(identities):
        raise ValueError(f"duplicate {field_name} artifact")
    return tuple(sorted(snapshots, key=lambda value: (value.source_role, value.artifact_id)))


def _artifact_manifest_matches(
    ref: ArtifactRef,
    artifact_store: ArtifactStore,
) -> bool:
    try:
        manifest_ref = artifact_store.load_artifact_ref(ref.artifact_id)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return manifest_ref.to_dict() == ref.to_dict()


def _optional_snapshot_artifact(
    ref: ArtifactRef | None,
    **kwargs: Any,
) -> ArtifactIdentitySnapshot | None:
    return None if ref is None else _snapshot_artifact(ref, **kwargs)


def _snapshot_event(event: LedgerEvent) -> LedgerEventIdentitySnapshot:
    event_type = (
        event.event_type.value
        if isinstance(event.event_type, EventType)
        else str(event.event_type)
    )
    if event.task_id is None:
        raise ValueError("ledger event task binding is missing")
    return LedgerEventIdentitySnapshot(
        event_seq=event.event_seq,
        event_id=event.event_id,
        event_type=event_type,
        event_hash=event.event_hash,
        prev_event_hash=event.prev_event_hash,
        task_id=event.task_id,
        object_type=event.object_type,
        object_id=event.object_id,
    )


def _select_canonical_event(
    events: Sequence[LedgerEvent],
    root_unit_id: str,
    final_ref: ArtifactIdentitySnapshot,
) -> LedgerEvent | None:
    matches = []
    for event in events:
        if _event_type(event) != EventType.CANONICAL_OUTPUTS_BOUND.value:
            continue
        selection = event.payload.get("canonical_selection")
        if not isinstance(selection, Mapping) or selection.get("unit_id") != root_unit_id:
            continue
        output_refs = selection.get("canonical_output_refs")
        if not isinstance(output_refs, Mapping):
            continue
        if any(
            _ref_triple(value) == _snapshot_triple(final_ref)
            for value in output_refs.values()
        ):
            matches.append(event)
    return matches[-1] if matches else None


def _has_bound_attempt_chain(
    events: Sequence[LedgerEvent],
    task_id: str,
    canonical_unit_ids: frozenset[str],
) -> bool:
    by_attempt_unit: dict[tuple[str, str], set[str]] = {}
    all_events_identity_consistent = True
    for event in events:
        event_type = _event_type(event)
        if event_type not in _ATTEMPT_EVENT_TYPES:
            continue
        payload = event.payload
        attempt_id = payload.get("attempt_id")
        unit_id = payload.get("unit_id")
        if (
            payload.get("task_id") != task_id
            or not isinstance(attempt_id, str)
            or not attempt_id
            or not isinstance(unit_id, str)
            or not unit_id
        ):
            all_events_identity_consistent = False
            continue
        if unit_id not in canonical_unit_ids:
            all_events_identity_consistent = False
        by_attempt_unit.setdefault((attempt_id, unit_id), set()).add(event_type)
    return all_events_identity_consistent and any(
        types == _ATTEMPT_EVENT_TYPES for types in by_attempt_unit.values()
    )


def _canonical_task_unit_ids(summary: Mapping[str, Any]) -> frozenset[str]:
    units = summary.get("units")
    if not isinstance(units, list) or not units:
        raise ValueError("canonical task graph is missing")
    unit_ids = []
    for unit in units:
        if not isinstance(unit, Mapping):
            raise ValueError("canonical task graph unit is invalid")
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("canonical task graph unit identity is invalid")
        unit_ids.append(unit_id)
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("canonical task graph contains duplicate units")
    return frozenset(unit_ids)


def _select_merge_event(
    events: Sequence[LedgerEvent],
    root_unit_id: str,
    final_ref: ArtifactIdentitySnapshot,
) -> LedgerEvent | None:
    matches = []
    for event in events:
        if _event_type(event) != EventType.MERGE_RECORDED.value:
            continue
        if event.payload.get("parent_unit_id") != root_unit_id:
            continue
        output_refs = event.payload.get("merge_output_refs")
        if not isinstance(output_refs, Mapping):
            continue
        if any(
            _ref_triple(value) == _snapshot_triple(final_ref)
            for value in output_refs.values()
        ):
            matches.append(event)
    return matches[-1] if matches else None


def _select_terminal_event(
    events: Sequence[LedgerEvent],
    root_unit_id: str,
    canonical_runtime_status: str,
) -> LedgerEvent | None:
    matches = []
    for event in events:
        if _event_type(event) != EventType.TASK_UNIT_STATE_CHANGED.value:
            continue
        unit = event.payload.get("task_unit")
        if not isinstance(unit, Mapping) or unit.get("unit_id") != root_unit_id:
            continue
        if _TERMINAL_TASK_UNIT_STATES.get(unit.get("state")) != canonical_runtime_status:
            continue
        matches.append(event)
    return matches[-1] if matches else None


def _select_merge_unit_final_provenance(
    events: Sequence[LedgerEvent],
    *,
    root_unit_id: str,
    final_ref: ArtifactIdentitySnapshot,
) -> tuple[LedgerEvent, LedgerEvent]:
    """严格接受 expanded-root 的唯一 merge-unit canonical final。"""

    merge_matches = [
        event
        for event in events
        if _event_type(event) == EventType.MERGE_RECORDED.value
        and event.payload.get("parent_unit_id") == root_unit_id
        and isinstance(event.payload.get("merge_output_refs"), Mapping)
        and any(
            _ref_triple(value) == _snapshot_triple(final_ref)
            for value in event.payload["merge_output_refs"].values()
        )
    ]
    if len(merge_matches) != 1:
        raise ValueError("merge-unit final requires one unique MERGE_RECORDED event")
    merge_event = merge_matches[0]
    record = merge_event.payload.get("merge_record")
    if not isinstance(record, Mapping):
        raise ValueError("merge-unit final merge record is missing")
    if (
        record.get("task_id") != merge_event.task_id
        or record.get("parent_unit_id") != root_unit_id
        or merge_event.payload.get("task_id") != merge_event.task_id
    ):
        raise ValueError("merge-unit final parent root binding mismatch")
    merge_unit_id = record.get("merge_unit_id")
    selection_id = record.get("canonical_selection_id")
    if not isinstance(merge_unit_id, str) or not merge_unit_id:
        raise ValueError("merge-unit final merge unit binding is missing")
    if not isinstance(selection_id, str) or not selection_id:
        raise ValueError("merge-unit final canonical selection binding is missing")
    if merge_event.payload.get("merge_unit_id") != merge_unit_id:
        raise ValueError("merge-unit final top-level merge unit binding mismatch")
    if merge_event.payload.get("canonical_selection_id") != selection_id:
        raise ValueError("merge-unit final top-level canonical selection mismatch")
    canonical_event_seq = record.get("canonical_event_seq")
    if type(canonical_event_seq) is not int:
        raise ValueError("merge-unit final canonical event sequence is missing")
    if merge_event.payload.get("canonical_event_seq") != canonical_event_seq:
        raise ValueError("merge-unit final top-level canonical event sequence mismatch")
    record_refs = record.get("merge_output_refs")
    if (
        not isinstance(record_refs, Mapping)
        or dict(record_refs) != dict(merge_event.payload["merge_output_refs"])
    ):
        raise ValueError("merge-unit final output binding mismatch")

    canonical_matches: list[LedgerEvent] = []
    for event in events:
        if _event_type(event) != EventType.CANONICAL_OUTPUTS_BOUND.value:
            continue
        selection = event.payload.get("canonical_selection")
        if not isinstance(selection, Mapping):
            continue
        output_refs = selection.get("canonical_output_refs")
        if (
            selection.get("unit_id") == merge_unit_id
            and isinstance(output_refs, Mapping)
            and any(
                _ref_triple(value) == _snapshot_triple(final_ref)
                for value in output_refs.values()
            )
        ):
            canonical_matches.append(event)
    if len(canonical_matches) != 1:
        raise ValueError("merge-unit final canonical event is ambiguous or missing")
    canonical_event = canonical_matches[0]
    selection = canonical_event.payload["canonical_selection"]
    if selection.get("canonical_selection_id") != selection_id:
        raise ValueError("merge-unit final canonical selection identity mismatch")
    if canonical_event.event_seq != canonical_event_seq:
        raise ValueError("merge-unit final canonical event sequence mismatch")
    selection_refs = selection.get("canonical_output_refs")
    if (
        not isinstance(selection_refs, Mapping)
        or dict(selection_refs) != dict(record_refs)
    ):
        raise ValueError("merge-unit final canonical output mapping mismatch")
    if canonical_event.event_seq >= merge_event.event_seq:
        raise ValueError("merge-unit final canonical event must precede merge event")
    return canonical_event, merge_event


def _select_verdict_ref(
    refs: Sequence[ArtifactIdentitySnapshot],
) -> ArtifactIdentitySnapshot | None:
    matches = [
        ref
        for ref in refs
        if ref.source_role in {"independent_verdict", "lean_checker_verdict"}
    ]
    if len(matches) > 1:
        raise ValueError("multiple independent verdict artifacts")
    return matches[0] if matches else None


def _read_bound_verdict(
    snapshot: ArtifactIdentitySnapshot,
    refs: Sequence[ArtifactRef],
    *,
    artifact_store: ArtifactStore,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactIdentitySnapshot,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    original = next(
        ref
        for ref in refs
        if (ref.artifact_id, ref.content_hash, ref.size_bytes)
        == _snapshot_triple(snapshot)
    )
    try:
        body = json.loads(artifact_store.read_bytes(original).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("independent verdict artifact is invalid JSON") from exc
    schema_version = body.get("schema_version") if isinstance(body, Mapping) else None
    if schema_version == "tokenshare.paper_factorization_domain_verifier_report.v1":
        return _read_factor_domain_verdict(
            body,
            original=original,
            artifact_store=artifact_store,
            inventory_row=inventory_row,
            execution_id=execution_id,
            task_id=task_id,
            root_unit_id=root_unit_id,
            final_result_ref=final_result_ref,
        )
    if schema_version == "tokenshare.paper_lean_domain_verdict_binding.v2":
        return _read_lean_domain_verdict(
            body,
            original=original,
            refs=refs,
            artifact_store=artifact_store,
            inventory_row=inventory_row,
            execution_id=execution_id,
            task_id=task_id,
            root_unit_id=root_unit_id,
            final_result_ref=final_result_ref,
            events=events,
        )
    if schema_version == "tokenshare.paper_persisted_verification_event_verdict.v1":
        return _read_persisted_verification_event_verdict(
            body,
            inventory_row=inventory_row,
            execution_id=execution_id,
            task_id=task_id,
            root_unit_id=root_unit_id,
            final_result_ref=final_result_ref,
            events=events,
        )
    raise ValueError("unsupported artifact-backed domain verdict schema")


def build_persisted_verification_event_verdict_body(
    *,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactIdentitySnapshot,
    verification_event: Mapping[str, Any],
) -> JsonObject:
    """把已落盘的最终 verifier/checker event 绑定为只读 direct verdict。"""

    if not isinstance(inventory_row, PaperDirectRootInventoryRow):
        raise TypeError("inventory_row must be PaperDirectRootInventoryRow")
    if not isinstance(final_result_ref, ArtifactIdentitySnapshot):
        raise TypeError("final_result_ref must be ArtifactIdentitySnapshot")
    if not isinstance(verification_event, Mapping):
        raise TypeError("verification_event must be a mapping")
    domain = inventory_row.condition_axes.get("domain")
    if domain not in {"factorization", "lean_proof"}:
        raise ValueError("persisted verification event domain is unsupported")
    body: JsonObject = {
        "schema_version": (
            "tokenshare.paper_persisted_verification_event_verdict.v1"
        ),
        "preregistered_root_run_id": inventory_row.preregistered_root_run_id,
        "experiment_id": inventory_row.experiment_id,
        "condition_id": inventory_row.condition_id,
        "case_id": inventory_row.case_id,
        "execution_id": execution_id,
        "task_id": task_id,
        "root_unit_id": root_unit_id,
        "domain": domain,
        "final_result_ref": final_result_ref.to_dict(),
        "verification_event": dict(verification_event),
    }
    return {**body, "projection_digest": digest_json(body)}


def _read_persisted_verification_event_verdict(
    body: Mapping[str, Any],
    *,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactIdentitySnapshot,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    """验证 persisted final verification event；不重新调用 domain verifier。"""

    _exact_keys(
        body,
        {
            "schema_version",
            "preregistered_root_run_id",
            "experiment_id",
            "condition_id",
            "case_id",
            "execution_id",
            "task_id",
            "root_unit_id",
            "domain",
            "final_result_ref",
            "verification_event",
            "projection_digest",
        },
        "persisted verification event verdict",
    )
    projection_body = dict(body)
    projection_digest = projection_body.pop("projection_digest")
    if projection_digest != digest_json(projection_body):
        raise ValueError("persisted verification event projection digest mismatch")
    domain = inventory_row.condition_axes.get("domain")
    expected_identity = (
        inventory_row.preregistered_root_run_id,
        inventory_row.experiment_id,
        inventory_row.condition_id,
        inventory_row.case_id,
        execution_id,
        task_id,
        root_unit_id,
        domain,
    )
    actual_identity = tuple(
        body.get(name)
        for name in (
            "preregistered_root_run_id",
            "experiment_id",
            "condition_id",
            "case_id",
            "execution_id",
            "task_id",
            "root_unit_id",
            "domain",
        )
    )
    if actual_identity != expected_identity:
        raise ValueError("persisted verification event root identity mismatch")
    if _ref_triple(body.get("final_result_ref")) != _snapshot_triple(
        final_result_ref
    ):
        raise ValueError("persisted verification event final identity mismatch")

    verification_event = body.get("verification_event")
    if not isinstance(verification_event, Mapping):
        raise ValueError("persisted verification event is invalid")
    matches = tuple(
        event
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_seq") == verification_event.get("event_seq")
        and event.get("event_id") == verification_event.get("event_id")
        and event.get("event_hash") == verification_event.get("event_hash")
    )
    if len(matches) != 1 or dict(matches[0]) != dict(verification_event):
        raise ValueError("persisted verification event identity mismatch")
    if (
        verification_event.get("event_type")
        != EventType.VERIFICATION_RECORDED.value
        or verification_event.get("task_id") != task_id
    ):
        raise ValueError("persisted verification event binding mismatch")
    payload = verification_event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("persisted verification record is missing")
    report = payload.get("verification_report")
    if not isinstance(report, Mapping):
        raise ValueError("persisted verification report is missing")
    if payload.get("verification_report_digest") != digest_json(report):
        raise ValueError("persisted verification report digest mismatch")
    expected_validator = {
        "factorization": "factorization.merge_result.validator.v1",
        "lean_proof": "lean_proof.checker.validator.v1",
    }.get(domain)
    if any(
        value is not True
        for value in (
            payload.get("eligible_for_canonical"),
            report.get("eligible_for_canonical"),
        )
    ) or any(
        value != "passed"
        for value in (payload.get("status"), report.get("status"))
    ):
        raise ValueError("persisted verification event did not pass")
    if any(
        value != expected
        for value, expected in (
            (payload.get("plugin_id"), domain),
            (report.get("plugin_id"), domain),
            (payload.get("validator_policy_id"), expected_validator),
            (report.get("validator_policy_id"), expected_validator),
            (payload.get("task_id"), task_id),
            (report.get("task_id"), task_id),
            (payload.get("unit_id"), report.get("unit_id")),
        )
    ):
        raise ValueError("persisted verification event validator identity mismatch")
    candidate_refs = report.get("candidate_output_refs")
    if not isinstance(candidate_refs, Mapping):
        raise ValueError("persisted verification candidate refs are missing")
    final_matches = tuple(
        value
        for value in candidate_refs.values()
        if _ref_triple(value) == _snapshot_triple(final_result_ref)
    )
    if len(final_matches) != 1:
        raise ValueError("persisted verification final candidate is ambiguous or missing")
    return True


def _read_factor_domain_verdict(
    body: Mapping[str, Any],
    *,
    original: ArtifactRef,
    artifact_store: ArtifactStore,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactIdentitySnapshot,
) -> bool:
    _exact_keys(
        body,
        {
            "schema_version",
            "case_id",
            "execution_id",
            "task_id",
            "root_unit_id",
            "final_result_ref",
            "environment_ref",
            "verifier",
            "target_n",
            "checks",
            "status",
            "correct",
            "report_digest",
        },
        "Factor domain verdict",
    )
    report_body = dict(body)
    report_digest = report_body.pop("report_digest")
    if report_digest != digest_json(report_body):
        raise ValueError("Factor domain verdict report digest mismatch")
    final_binding = ArtifactRef.from_dict(body["final_result_ref"])
    if _ref_triple(final_binding.to_dict()) != _snapshot_triple(final_result_ref):
        raise ValueError("Factor domain verdict final identity mismatch")
    expected_identity = (
        inventory_row.case_id,
        execution_id,
        task_id,
        root_unit_id,
    )
    actual_identity = (
        body["case_id"],
        body["execution_id"],
        body["task_id"],
        body["root_unit_id"],
    )
    if actual_identity != expected_identity:
        raise ValueError("Factor domain verdict runtime identity mismatch")
    _validate_domain_environment_ref(body["environment_ref"])
    source_environment = original.source.get("environment_digest")
    if source_environment is not None and source_environment != body[
        "environment_ref"
    ]["environment_digest"]:
        raise ValueError("Factor domain verdict environment binding mismatch")
    if body["verifier"] != {
        "verifier_id": "factorization.prime_factorization_result.verifier",
        "verifier_version": "v1",
    }:
        raise ValueError("Factor domain verdict verifier identity mismatch")
    if not isinstance(body["target_n"], str) or not body["target_n"].isdigit():
        raise ValueError("Factor domain verdict target is invalid")
    try:
        final_body = json.loads(
            artifact_store.read_bytes(
                artifact_store.load_artifact_ref(final_result_ref.artifact_id)
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Factor final result is invalid JSON") from exc
    checks = _factor_domain_checks(final_body, target_n=body["target_n"])
    if body["checks"] != checks:
        raise ValueError("Factor domain verdict checks mismatch")
    correct = all(checks.values())
    if type(body["correct"]) is not bool or body["correct"] is not correct:
        raise ValueError("Factor domain verdict self-report mismatch")
    if body["status"] != ("accepted" if correct else "rejected"):
        raise ValueError("Factor domain verdict status mismatch")
    return correct


def _read_lean_domain_verdict(
    body: Mapping[str, Any],
    *,
    original: ArtifactRef,
    refs: Sequence[ArtifactRef],
    artifact_store: ArtifactStore,
    inventory_row: PaperDirectRootInventoryRow,
    execution_id: str,
    task_id: str,
    root_unit_id: str,
    final_result_ref: ArtifactIdentitySnapshot,
    events: Sequence[Mapping[str, Any]],
) -> bool:
    """从真实 root checker report 判定 Lean correctness，binding 仅承载身份。"""

    if inventory_row.condition_axes.get("domain") != "lean_proof":
        raise ValueError("Lean domain verdict inventory domain mismatch")
    _exact_keys(
        body,
        {
            "schema_version",
            "case_id",
            "execution_id",
            "task_id",
            "root_unit_id",
            "final_result_ref",
            "root_checker_report_ref",
            "root_theorem_payload_ref",
            "official_case_digest",
            "official_root_theorem_id",
            "official_root_theorem_payload_digest",
            "normalized_theorem_digest",
            "environment_digest",
            "report_status",
            "verifier",
            "binding_digest",
        },
        "Lean domain verdict binding",
    )
    binding_body = dict(body)
    binding_digest = binding_body.pop("binding_digest")
    if binding_digest != digest_json(binding_body):
        raise ValueError("Lean domain verdict binding digest mismatch")
    native_binding_ref = original.source.get("source_artifact_ref")
    native_binding_source = (
        native_binding_ref.get("source")
        if isinstance(native_binding_ref, Mapping)
        and isinstance(native_binding_ref.get("source"), Mapping)
        else original.source
    )
    source_environment_digest = native_binding_source.get("environment_digest")
    if source_environment_digest is not None and source_environment_digest != body[
        "environment_digest"
    ]:
        raise ValueError("Lean domain verdict source environment mismatch")
    for field_name in (
        "final_result_ref",
        "root_checker_report_ref",
        "root_theorem_payload_ref",
    ):
        source_value = native_binding_source.get(field_name)
        if source_value is not None and _ref_triple(source_value) != _ref_triple(
            body[field_name]
        ):
            raise ValueError(f"Lean domain verdict source {field_name} mismatch")
    source_theorem_digest = native_binding_source.get("normalized_theorem_digest")
    if (
        source_theorem_digest is not None
        and source_theorem_digest != body["normalized_theorem_digest"]
    ):
        raise ValueError("Lean domain verdict source normalized theorem mismatch")
    for field_name in (
        "official_case_digest",
        "official_root_theorem_id",
        "official_root_theorem_payload_digest",
    ):
        source_value = native_binding_source.get(field_name)
        if source_value is not None and source_value != body[field_name]:
            raise ValueError(
                f"Lean domain verdict source {field_name} mismatch"
            )
    expected_identity = (
        inventory_row.case_id,
        execution_id,
        task_id,
        root_unit_id,
    )
    actual_identity = (
        body["case_id"],
        body["execution_id"],
        body["task_id"],
        body["root_unit_id"],
    )
    if actual_identity != expected_identity:
        raise ValueError("Lean domain verdict runtime identity mismatch")
    if body["verifier"] != {
        "verifier_id": "lean_proof.checker.validator.v1",
        "verifier_version": "0.1.0",
    }:
        raise ValueError("Lean domain verdict verifier identity mismatch")
    final_binding = ArtifactRef.from_dict(body["final_result_ref"])
    if _ref_triple(final_binding.to_dict()) != _snapshot_triple(final_result_ref):
        raise ValueError("Lean domain verdict final identity mismatch")
    final_source_triples = _final_domain_source_triples(
        final_result_ref,
        artifact_store=artifact_store,
    )
    bound_verification_events: list[
        tuple[
            Mapping[str, Any],
            Mapping[str, Any],
            Mapping[str, Any],
            tuple[Any, ...],
        ]
    ] = []
    for event in events:
        if (
            not isinstance(event, Mapping)
            or event.get("event_type") != EventType.VERIFICATION_RECORDED.value
            or event.get("task_id") != task_id
        ):
            continue
        payload = event.get("payload")
        report = payload.get("verification_report") if isinstance(payload, Mapping) else None
        candidate_refs = (
            report.get("candidate_output_refs") if isinstance(report, Mapping) else None
        )
        if not isinstance(candidate_refs, Mapping):
            continue
        final_matches = tuple(
            value
            for value in candidate_refs.values()
            if _ref_triple(value) in final_source_triples
        )
        if final_matches:
            bound_verification_events.append((event, payload, report, final_matches))
    if len(bound_verification_events) != 1:
        raise ValueError(
            "Lean domain verdict requires exactly one authoritative verification event"
        )
    verification_event, verification_payload, verification_report, final_matches = (
        bound_verification_events[0]
    )
    if verification_payload.get("verification_report_digest") != digest_json(
        verification_report
    ):
        raise ValueError("Lean verification report digest mismatch")
    if len(final_matches) != 1:
        raise ValueError("Lean verification final candidate is ambiguous")
    bound_canonical_events: list[
        tuple[Mapping[str, Any], Mapping[str, Any], tuple[Any, ...]]
    ] = []
    for event in events:
        if (
            not isinstance(event, Mapping)
            or event.get("event_type") != EventType.CANONICAL_OUTPUTS_BOUND.value
            or event.get("task_id") != task_id
        ):
            continue
        payload = event.get("payload")
        selection = payload.get("canonical_selection") if isinstance(payload, Mapping) else None
        output_refs = (
            selection.get("canonical_output_refs")
            if isinstance(selection, Mapping)
            else None
        )
        if not isinstance(output_refs, Mapping):
            continue
        selection_matches = tuple(
            value
            for value in output_refs.values()
            if _ref_triple(value) in final_source_triples
        )
        if selection_matches:
            bound_canonical_events.append((event, selection, selection_matches))
    if not bound_canonical_events:
        raise ValueError("Lean canonical selection is missing")
    _, canonical_selection, canonical_final_matches = bound_canonical_events[-1]
    if len(canonical_final_matches) != 1:
        raise ValueError("Lean canonical selection final candidate is ambiguous")
    if any(
        value != expected
        for value, expected in (
            (
                canonical_selection.get("selected_verification_event_seq"),
                verification_event.get("event_seq"),
            ),
            (
                canonical_selection.get("selected_verification_report_id"),
                verification_report.get("verification_report_id"),
            ),
            (
                canonical_selection.get("selected_attempt_id"),
                verification_payload.get("attempt_id"),
            ),
            (
                canonical_selection.get("selected_attempt_id"),
                verification_report.get("attempt_id"),
            ),
            (
                canonical_selection.get("selected_submission_id"),
                verification_payload.get("submission_id"),
            ),
            (
                canonical_selection.get("selected_submission_id"),
                verification_report.get("submission_id"),
            ),
            (canonical_selection.get("task_id"), task_id),
            (
                canonical_selection.get("unit_id"),
                verification_payload.get("unit_id"),
            ),
            (
                canonical_selection.get("unit_id"),
                verification_report.get("unit_id"),
            ),
        )
    ):
        raise ValueError("Lean canonical selection verification binding mismatch")
    if any(
        value is not True
        for value in (
            verification_payload.get("eligible_for_canonical"),
            verification_report.get("eligible_for_canonical"),
        )
    ) or any(
        value != "passed"
        for value in (
            verification_payload.get("status"),
            verification_report.get("status"),
        )
    ):
        raise ValueError("Lean verification event did not pass")
    expected_validator = "lean_proof.checker.validator.v1"
    if any(
        value != expected
        for value, expected in (
            (verification_payload.get("plugin_id"), "lean_proof"),
            (verification_report.get("plugin_id"), "lean_proof"),
            (
                verification_payload.get("validator_policy_id"),
                expected_validator,
            ),
            (
                verification_report.get("validator_policy_id"),
                expected_validator,
            ),
            (verification_payload.get("task_id"), task_id),
            (verification_report.get("task_id"), task_id),
            (
                verification_payload.get("unit_id"),
                verification_report.get("unit_id"),
            ),
        )
    ) or not isinstance(verification_payload.get("unit_id"), str):
        raise ValueError("Lean verification event validator identity mismatch")
    environment_digest = body["environment_digest"]
    if not isinstance(environment_digest, str) or not environment_digest:
        raise ValueError("Lean domain verdict environment digest is missing")
    allowed_statuses = {
        "accepted",
        "rejected",
        "timeout",
        "environment_error",
        "helper_error",
    }
    if body["report_status"] not in allowed_statuses:
        raise ValueError("Lean domain verdict report status is invalid")

    root_theorem_ref, root_theorem_bytes = _read_internal_lean_artifact(
        body["root_theorem_payload_ref"],
        artifact_store=artifact_store,
        field_name="root_theorem_payload_ref",
    )
    try:
        root_theorem_body = json.loads(root_theorem_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Lean root theorem payload is invalid JSON") from exc
    _validate_lean_root_theorem_payload(
        root_theorem_body,
        authoritative_ref=root_theorem_ref,
        case_id=inventory_row.case_id,
    )
    official_case_ref = inventory_row.preregistered_case_ref
    official_fields = (
        "official_case_digest",
        "official_root_theorem_id",
        "official_root_theorem_payload_digest",
    )
    if any(field_name not in official_case_ref for field_name in official_fields):
        raise ValueError("Lean official case authority is missing")
    if any(
        body[field_name] != official_case_ref[field_name]
        for field_name in official_fields
    ):
        raise ValueError("Lean official root theorem authority mismatch")
    if (
        root_theorem_body["theorem_id"]
        != official_case_ref["official_root_theorem_id"]
        or root_theorem_body["payload_digest"]
        != official_case_ref["official_root_theorem_payload_digest"]
    ):
        raise ValueError("Lean official root theorem payload mismatch")
    normalized_theorem_digest = digest_json(
        {
            "theorem_name": root_theorem_body["theorem_name"],
            "imports": root_theorem_body["imports"],
            "namespace": root_theorem_body["namespace"],
            "parameters_source": root_theorem_body["parameters_source"],
            "statement_source": root_theorem_body["statement_source"],
        }
    )
    if body["normalized_theorem_digest"] != normalized_theorem_digest:
        raise ValueError("Lean domain verdict normalized theorem mismatch")

    report_refs = tuple(
        ref for ref in refs if ref.source.get("role") == "root_checker_report"
    )
    if len(report_refs) != 1:
        raise ValueError("Lean domain verdict requires exactly one root checker report")
    report_ref = report_refs[0]
    binding_report_triple = _ref_triple(body["root_checker_report_ref"])
    projected_source_triple = _ref_triple(report_ref.source.get("source_artifact_ref"))
    if binding_report_triple not in {
        _ref_triple(report_ref.to_dict()),
        projected_source_triple,
    }:
        raise ValueError("Lean root checker report identity mismatch")
    try:
        report_body = json.loads(artifact_store.read_bytes(report_ref).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Lean root checker report is invalid JSON") from exc
    _exact_keys(
        report_body,
        {
            "schema_version",
            "report_id",
            "request_id",
            "status",
            "exit_code",
            "stdout_ref",
            "stderr_ref",
            "generated_source_ref",
            "proof_artifact_ref",
            "diagnostics",
            "normalized_theorem_digest",
            "proof_digest",
            "environment_ref",
            "command_summary",
            "duration_ms",
        },
        "Lean root checker report",
    )
    if report_body["schema_version"] != "lean_proof.checker_report.v1":
        raise ValueError("unsupported Lean root checker report schema")
    for field_name in ("report_id", "request_id", "normalized_theorem_digest"):
        if not isinstance(report_body[field_name], str) or not report_body[field_name]:
            raise ValueError("Lean root checker report identity is incomplete")
    if report_body["status"] != body["report_status"]:
        raise ValueError("Lean root checker report status mismatch")
    if report_body["status"] not in allowed_statuses:
        raise ValueError("Lean root checker report status is invalid")
    _require_lean_request_identity(
        report_ref,
        report_ref,
        request_id=report_body["request_id"],
        field_name="root_checker_report_ref",
    )
    _validate_domain_environment_ref(report_body["environment_ref"])
    if report_body["environment_ref"]["environment_digest"] != environment_digest:
        raise ValueError("Lean root checker report environment mismatch")
    if not isinstance(report_body["diagnostics"], Mapping) or not isinstance(
        report_body["command_summary"], Mapping
    ):
        raise ValueError("Lean root checker report diagnostics are invalid")
    if type(report_body["duration_ms"]) is not int or report_body["duration_ms"] < 0:
        raise ValueError("Lean root checker report duration is invalid")
    if report_body["normalized_theorem_digest"] != normalized_theorem_digest:
        raise ValueError("Lean root checker normalized theorem mismatch")
    verification_metadata = verification_report.get("metadata")
    plugin_domain_layer = (
        verification_metadata.get("plugin_domain_layer")
        if isinstance(verification_metadata, Mapping)
        else None
    )
    plugin_domain_details = (
        plugin_domain_layer.get("details")
        if isinstance(plugin_domain_layer, Mapping)
        else None
    )
    if (
        not isinstance(verification_metadata, Mapping)
        or not isinstance(plugin_domain_details, Mapping)
        or _ref_triple(verification_metadata.get("checker_report_ref"))
        != binding_report_triple
        or plugin_domain_details.get("environment_digest") != environment_digest
        or plugin_domain_details.get("proof_digest") != report_body["proof_digest"]
        or plugin_domain_details.get("checker_status") != report_body["status"]
    ):
        raise ValueError("Lean checker event metadata binding mismatch")

    proof_triple = _ref_triple(report_body["proof_artifact_ref"])
    if report_body["status"] == "accepted":
        checker_artifacts: dict[str, tuple[ArtifactRef, bytes]] = {}
        for field_name in ("stdout_ref", "stderr_ref", "generated_source_ref"):
            if report_body[field_name] is None:
                raise ValueError(
                    f"accepted Lean checker report {field_name} is missing"
                )
            supplied_ref = ArtifactRef.from_dict(report_body[field_name])
            authoritative_ref, artifact_bytes = _read_internal_lean_artifact(
                report_body[field_name],
                artifact_store=artifact_store,
                field_name=field_name,
            )
            _require_lean_request_identity(
                supplied_ref,
                authoritative_ref,
                request_id=report_body["request_id"],
                field_name=field_name,
            )
            _validate_lean_checker_artifact_contract(
                supplied_ref,
                authoritative_ref,
                field_name=field_name,
            )
            try:
                artifact_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"Lean checker {field_name} is not UTF-8"
                ) from exc
            checker_artifacts[field_name] = (authoritative_ref, artifact_bytes)
        if not checker_artifacts["generated_source_ref"][1]:
            raise ValueError("accepted Lean checker generated source is empty")
        if proof_triple is None:
            raise ValueError(
                "accepted Lean checker report proof_artifact_ref is missing"
            )
        supplied_proof_ref = ArtifactRef.from_dict(report_body["proof_artifact_ref"])
        authoritative_proof_ref, proof_bytes = _read_internal_lean_artifact(
            report_body["proof_artifact_ref"],
            artifact_store=artifact_store,
            field_name="proof_artifact_ref",
        )
        _validate_lean_checker_artifact_contract(
            supplied_proof_ref,
            authoritative_proof_ref,
            field_name="proof_artifact_ref",
        )
        _require_lean_request_identity(
            supplied_proof_ref,
            authoritative_proof_ref,
            request_id=report_body["request_id"],
            field_name="proof_artifact_ref",
        )
        if proof_triple not in _final_domain_source_triples(
            final_result_ref,
            artifact_store=artifact_store,
        ):
            raise ValueError("Lean checker proof does not bind the canonical final")
        if report_body["exit_code"] != 0:
            raise ValueError("accepted Lean checker report exit status mismatch")
        if not isinstance(report_body["proof_digest"], str) or not report_body[
            "proof_digest"
        ]:
            raise ValueError("accepted Lean checker report proof digest is missing")
        try:
            proof_source = proof_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Lean checker proof artifact is not UTF-8") from exc
        recomputed_proof_digest = digest_json(
            {
                "theorem_payload_digest": root_theorem_body["payload_digest"],
                "proof_source": proof_source,
            }
        )
        if (
            supplied_proof_ref.metadata.get("proof_digest")
            != report_body["proof_digest"]
            or authoritative_proof_ref.metadata.get("proof_digest")
            != report_body["proof_digest"]
            or recomputed_proof_digest != report_body["proof_digest"]
        ):
            raise ValueError("Lean checker proof digest mismatch")
        generated_source = checker_artifacts["generated_source_ref"][1]
        expected_generated_source = render_lean_source(
            LeanTheoremPayload.from_dict(dict(root_theorem_body)),
            proof_source,
        ).encode("utf-8")
        if generated_source != expected_generated_source:
            raise ValueError("Lean checker generated source mismatch")
        return True
    if report_body["proof_artifact_ref"] is not None:
        raise ValueError("non-accepted Lean checker report cannot carry proof artifact")
    return False


def _read_internal_lean_artifact(
    value: Any,
    *,
    artifact_store: ArtifactStore,
    field_name: str,
) -> tuple[ArtifactRef, bytes]:
    """按 canonical manifest 解析 checker 内部 ref，并真实读取其内容。"""

    if not isinstance(value, Mapping):
        raise ValueError(f"Lean checker {field_name} is missing")
    supplied = ArtifactRef.from_dict(value)
    try:
        authoritative = artifact_store.load_artifact_ref(supplied.artifact_id)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"Lean checker {field_name} artifact is unavailable") from exc
    if _ref_triple(authoritative.to_dict()) != _ref_triple(supplied.to_dict()):
        raise ValueError(f"Lean checker {field_name} identity mismatch")
    try:
        content = artifact_store.read_bytes(authoritative)
    except OSError as exc:
        raise ValueError(f"Lean checker {field_name} artifact is unreadable") from exc
    return authoritative, content


def _native_artifact_source(ref: ArtifactRef) -> Mapping[str, Any]:
    source_artifact_ref = ref.source.get("source_artifact_ref")
    if isinstance(source_artifact_ref, Mapping) and isinstance(
        source_artifact_ref.get("source"), Mapping
    ):
        return source_artifact_ref["source"]
    return ref.source


def _require_lean_request_identity(
    supplied: ArtifactRef,
    authoritative: ArtifactRef,
    *,
    request_id: str,
    field_name: str,
) -> None:
    supplied_request_id = _native_artifact_source(supplied).get("request_id")
    authoritative_request_id = _native_artifact_source(authoritative).get(
        "request_id"
    )
    if (
        supplied_request_id != request_id
        or authoritative_request_id != request_id
    ):
        raise ValueError(f"Lean checker {field_name} request identity mismatch")


def _validate_lean_checker_artifact_contract(
    supplied: ArtifactRef,
    authoritative: ArtifactRef,
    *,
    field_name: str,
) -> None:
    expected = {
        "stdout_ref": (
            "LeanCheckerStdout",
            "text/plain",
            "lean_proof.checker_log",
            "v1",
        ),
        "stderr_ref": (
            "LeanCheckerStderr",
            "text/plain",
            "lean_proof.checker_log",
            "v1",
        ),
        "generated_source_ref": (
            "LeanGeneratedSource",
            "text/x-lean",
            "lean_proof.generated_source",
            "v1",
        ),
        "proof_artifact_ref": (
            "LeanProofArtifact",
            "text/x-lean",
            "lean_proof.proof_artifact",
            "v1",
        ),
    }[field_name]
    for ref in (supplied, authoritative):
        actual = (
            ref.artifact_type,
            ref.media_type,
            ref.artifact_schema_id,
            ref.artifact_schema_version,
        )
        if actual != expected:
            raise ValueError(f"Lean checker {field_name} type mismatch")


def _validate_lean_root_theorem_payload(
    value: Any,
    *,
    authoritative_ref: ArtifactRef,
    case_id: str,
) -> None:
    expected_keys = {
        "schema_version",
        "theorem_id",
        "theorem_name",
        "imports",
        "namespace",
        "open_namespaces",
        "options",
        "parameters_source",
        "statement_source",
        "theorem_source",
        "proof_candidate_ref",
        "library_context",
        "decomposition_policy",
        "resource_limits",
        "payload_digest",
    }
    _exact_keys(value, expected_keys, "Lean root theorem payload")
    if value["schema_version"] != "lean_proof.theorem_payload.v1":
        raise ValueError("unsupported Lean root theorem payload schema")
    payload_body = dict(value)
    payload_digest = payload_body.pop("payload_digest")
    if payload_digest != digest_json(payload_body):
        raise ValueError("Lean root theorem payload digest mismatch")
    theorem_id = value["theorem_id"]
    if theorem_id != f"lean_theorem:{case_id}" and not (
        isinstance(theorem_id, str)
        and theorem_id.startswith(f"lean_lemma_graph:{case_id}:")
    ):
        raise ValueError("Lean root theorem payload case identity mismatch")
    if authoritative_ref.metadata.get("case_id") != case_id:
        raise ValueError("Lean root theorem payload case metadata mismatch")
    if authoritative_ref.metadata.get("output_name") != "lean_theorem_payload":
        raise ValueError("Lean root theorem payload is not the runtime root output")
    library_context = value["library_context"]
    if not isinstance(library_context, Mapping) or library_context.get(
        "case_id"
    ) not in {None, case_id}:
        raise ValueError("Lean root theorem payload library case mismatch")
    if (
        not isinstance(value["theorem_name"], str)
        or not value["theorem_name"]
        or not isinstance(value["imports"], list)
        or not value["imports"]
        or not all(isinstance(item, str) and item for item in value["imports"])
        or value["namespace"] is not None
        and not isinstance(value["namespace"], str)
        or not isinstance(value["parameters_source"], str)
        or not isinstance(value["statement_source"], str)
        or not value["statement_source"]
    ):
        raise ValueError("Lean root theorem payload identity is incomplete")


def _final_domain_source_triples(
    final_result_ref: ArtifactIdentitySnapshot,
    *,
    artifact_store: ArtifactStore,
) -> set[tuple[str, str, int]]:
    """收集 canonical final 与其只读 projection/source lineage 的身份。"""

    values = {_snapshot_triple(final_result_ref)}
    authoritative = artifact_store.load_artifact_ref(final_result_ref.artifact_id)
    frontier: list[Any] = [authoritative.to_dict()]
    visited: set[str] = set()
    while frontier:
        current = frontier.pop()
        triple = _ref_triple(current)
        if triple is None or not isinstance(current, Mapping):
            continue
        projection_identity = digest_json(current)
        if projection_identity in visited:
            continue
        visited.add(projection_identity)
        values.add(triple)
        source = current.get("source")
        if isinstance(source, Mapping):
            for field_name in ("source_artifact_ref", "source_ref"):
                nested = source.get(field_name)
                if isinstance(nested, Mapping):
                    frontier.append(nested)
    return values


def _factor_domain_checks(value: Any, *, target_n: str) -> JsonObject:
    body = value if isinstance(value, Mapping) else {}
    factors = body.get("prime_factors")
    factor_values = (
        tuple(factors)
        if isinstance(factors, list)
        and factors
        and all(isinstance(item, Mapping) for item in factors)
        else ()
    )
    pairs: list[tuple[int, int]] = []
    try:
        pairs = [
            (int(str(item["prime"])), int(item["exponent"]))
            for item in factor_values
        ]
    except (KeyError, TypeError, ValueError):
        pairs = []
    primes = [prime for prime, _ in pairs]
    encoding_valid = (
        bool(pairs)
        and all(prime >= 2 and exponent >= 1 for prime, exponent in pairs)
        and primes == sorted(set(primes))
    )
    product = 1
    if encoding_valid:
        for prime, exponent in pairs:
            product *= prime**exponent
    return {
        "target_matches": str(body.get("target_n", "")) == target_n,
        "canonical_factor_encoding": encoding_valid,
        "factor_product_matches": encoding_valid and product == int(target_n),
        "all_factors_prime": encoding_valid
        and all(_deterministic_prime_check(prime) for prime in primes),
    }


def _deterministic_prime_check(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor <= math.isqrt(value):
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _validate_domain_environment_ref(value: Any) -> None:
    _exact_keys(
        value,
        {
            "schema_version",
            "environment_id",
            "environment_digest",
            "runtime",
            "tool_versions",
            "resource_limits",
            "fixture_profile_digest",
            "seed",
            "clock_policy",
            "created_at",
        },
        "domain verdict environment",
    )
    if value["schema_version"] != "phase3.environment_ref.v1":
        raise ValueError("unsupported domain verdict environment schema")
    for field_name in (
        "environment_id",
        "environment_digest",
        "runtime",
        "fixture_profile_digest",
        "clock_policy",
        "created_at",
    ):
        if not isinstance(value[field_name], str) or not value[field_name]:
            raise ValueError("domain verdict environment identity is incomplete")
    if not isinstance(value["tool_versions"], Mapping) or not isinstance(
        value["resource_limits"], Mapping
    ):
        raise ValueError("domain verdict environment facts are invalid")


def _canonical_locators(
    values: Sequence[ExternalBankObjectLocator],
) -> tuple[ExternalBankObjectLocator, ...]:
    locators = tuple(values)
    if any(not isinstance(value, ExternalBankObjectLocator) for value in locators):
        raise TypeError("source locators must be ExternalBankObjectLocator")
    roles = [(value.entry_id, value.object_role) for value in locators]
    if len(set(roles)) != len(roles):
        raise ValueError("duplicate source locator role within entry")
    identities = [
        (
            value.bank_root_id,
            value.manifest_digest,
            value.entry_id,
            value.object_role,
            value.object_digest,
        )
        for value in locators
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate source locator")
    if locators:
        bank_bindings = {
            (value.bank_root_id, value.manifest_digest) for value in locators
        }
        if len(bank_bindings) != 1:
            raise ValueError("source locator bank binding mismatch")
    return tuple(
        sorted(
            locators,
            key=lambda value: (value.entry_id, value.object_role, value.object_digest),
        )
    )


def _append_locator_role_completeness_reasons(
    locators: Sequence[ExternalBankObjectLocator],
    *,
    allowed: frozenset[str],
    reasons: list[str],
    prefix: str,
) -> None:
    by_entry: dict[str, set[str]] = {}
    for locator in locators:
        by_entry.setdefault(locator.entry_id, set()).add(locator.object_role)
    if not by_entry:
        _append_role_completeness_reasons(
            frozenset(),
            allowed=allowed,
            reasons=reasons,
            prefix=prefix,
        )
        return
    for entry_id in sorted(by_entry):
        _append_role_completeness_reasons(
            frozenset(by_entry[entry_id]),
            allowed=allowed,
            reasons=reasons,
            prefix=f"{prefix}[{entry_id}]",
        )


def _append_role_completeness_reasons(
    roles: frozenset[str],
    *,
    allowed: frozenset[str],
    reasons: list[str],
    prefix: str,
) -> None:
    missing = sorted(allowed - roles)
    if missing:
        reasons.append(f"{prefix}_roles_missing:" + ",".join(missing))
    unknown = sorted(roles - allowed)
    if unknown:
        raise ValueError(f"{prefix} contains unknown roles")


def _unique_artifact_snapshots(
    values: Sequence[ArtifactIdentitySnapshot],
) -> tuple[ArtifactIdentitySnapshot, ...]:
    by_identity: dict[tuple[str, str, int], ArtifactIdentitySnapshot] = {}
    for value in values:
        key = _snapshot_triple(value)
        existing = by_identity.get(key)
        if existing is not None and existing != value:
            raise ValueError("artifact identity snapshot collision")
        by_identity[key] = value
    return tuple(by_identity[key] for key in sorted(by_identity))


def _artifact_triples(refs: Sequence[ArtifactRef]) -> tuple[tuple[str, str, int], ...]:
    if any(not isinstance(ref, ArtifactRef) for ref in refs):
        raise TypeError("runtime artifact refs must be typed ArtifactRef")
    triples = tuple(sorted((ref.artifact_id, ref.content_hash, ref.size_bytes) for ref in refs))
    if len(set(triples)) != len(triples):
        raise ValueError("duplicate runtime artifact ref")
    return triples


def _snapshot_triple(value: ArtifactIdentitySnapshot) -> tuple[str, str, int]:
    return value.artifact_id, value.content_hash, value.size_bytes


def _ref_triple(value: Any) -> tuple[str, str, int] | None:
    if not isinstance(value, Mapping):
        return None
    artifact_id = value.get("artifact_id")
    content_hash = value.get("content_hash")
    size_bytes = value.get("size_bytes")
    if not isinstance(artifact_id, str) or not isinstance(content_hash, str):
        return None
    if type(size_bytes) is not int:
        return None
    return artifact_id, content_hash, size_bytes


def _roles(values: Sequence[ArtifactIdentitySnapshot]) -> frozenset[str]:
    return frozenset(value.source_role for value in values)


def _event_type(event: LedgerEvent) -> str:
    return (
        event.event_type.value
        if isinstance(event.event_type, EventType)
        else str(event.event_type)
    )


def _exact_keys(value: Any, expected: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{name} must have exact keys")


def _strict_int(name: str, value: Any, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be integer >= {minimum}")


def _nullable_member(name: str, value: Any, allowed: set[str]) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"unsupported {name}")


def _non_empty(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")


def _optional_dict(value: Any) -> JsonObject | None:
    return None if value is None else value.to_dict()


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if type(value) is float and not math.isfinite(value):
        raise ValueError("floating-point values must be finite")
    if value is None or type(value) in {bool, int, float, str}:
        return value
    raise ValueError("value must be JSON-compatible")
