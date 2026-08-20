"""不可变 response bank 驱动的零 provider trace executor 与 parent stager。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.executors.response_bank import (
    ResponseBankEntry,
    ResponseBankResolver,
    canonical_digest,
)
from tokenshare.local_runtime.contracts import (
    LOGICAL_DISPATCH_START_MS_HINT,
    ParentStagedTraceDelivery,
    ParentTraceDeliveryStageContext,
    PreparedTraceDelivery,
    TRACE_TERMINAL_EXECUTION_RESULT_KINDS,
)
from tokenshare.storage.artifacts import ArtifactStore


APPROVED_REAL_SOURCE_EVIDENCE_CLASS = "approved_real_api_acquisition"
SYNTHETIC_SOURCE_EVIDENCE_CLASSES = frozenset(
    {"synthetic_regression", "scripted_regression", "deterministic_regression"}
)
TRACE_ATTEMPT_DELIVERY_KINDS = frozenset(
    {"ordinary_attempt", "fault_redelivery"}
)
MISSING_SOURCE_PROTOCOL_OPERATIONAL_DELAY_MS = 1


@dataclass(frozen=True, kw_only=True)
class TraceReplacementBinding:
    replacement_slot: int
    entry_id: str
    inference_request_digest: str

    def __post_init__(self) -> None:
        if isinstance(self.replacement_slot, bool) or not isinstance(
            self.replacement_slot, int
        ):
            raise TypeError("replacement_slot must be an integer")
        if self.replacement_slot < 0:
            raise ValueError("replacement_slot must be non-negative")
        _require_non_empty("entry_id", self.entry_id)
        _require_sha256("inference_request_digest", self.inference_request_digest)

    def to_dict(self) -> JsonObject:
        return {
            "replacement_slot": self.replacement_slot,
            "entry_id": self.entry_id,
            "inference_request_digest": self.inference_request_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TraceReplacementBinding":
        if set(value) != {
            "replacement_slot",
            "entry_id",
            "inference_request_digest",
        }:
            raise ValueError("trace replacement binding fields mismatch")
        return cls(
            replacement_slot=value["replacement_slot"],
            entry_id=value["entry_id"],
            inference_request_digest=value["inference_request_digest"],
        )


@dataclass(frozen=True, kw_only=True)
class TraceAttemptDeliveryBinding:
    """current protocol delivery 到 immutable Exp1 attempt 的严格映射。"""

    current_attempt_ordinal: int
    source_entry_id: str
    source_attempt_index: int
    source_replacement_slot: int
    delivery_kind: str
    redelivery_reason: str | None

    def __post_init__(self) -> None:
        for name, value in (
            ("current_attempt_ordinal", self.current_attempt_ordinal),
            ("source_attempt_index", self.source_attempt_index),
            ("source_replacement_slot", self.source_replacement_slot),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        _require_non_empty("source_entry_id", self.source_entry_id)
        if self.source_attempt_index != self.source_replacement_slot:
            raise ValueError("trace delivery source attempt identity drifted")
        if self.delivery_kind not in TRACE_ATTEMPT_DELIVERY_KINDS:
            raise ValueError("unsupported trace attempt delivery kind")
        if self.delivery_kind == "ordinary_attempt":
            if self.redelivery_reason is not None:
                raise ValueError("ordinary trace attempt cannot have redelivery reason")
        else:
            _require_non_empty("redelivery_reason", self.redelivery_reason)

    def to_dict(self) -> JsonObject:
        return {
            "current_attempt_ordinal": self.current_attempt_ordinal,
            "source_entry_id": self.source_entry_id,
            "source_attempt_index": self.source_attempt_index,
            "source_replacement_slot": self.source_replacement_slot,
            "delivery_kind": self.delivery_kind,
            "redelivery_reason": self.redelivery_reason,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "TraceAttemptDeliveryBinding":
        expected = {
            "current_attempt_ordinal",
            "source_entry_id",
            "source_attempt_index",
            "source_replacement_slot",
            "delivery_kind",
            "redelivery_reason",
        }
        if set(value) != expected:
            raise ValueError("trace attempt delivery binding fields mismatch")
        return cls(
            current_attempt_ordinal=value["current_attempt_ordinal"],
            source_entry_id=value["source_entry_id"],
            source_attempt_index=value["source_attempt_index"],
            source_replacement_slot=value["source_replacement_slot"],
            delivery_kind=value["delivery_kind"],
            redelivery_reason=value["redelivery_reason"],
        )


@dataclass(frozen=True, kw_only=True)
class TraceSourceBinding:
    planned_ai_unit_id: str
    sample_slot_index: int
    bank_root_id: str
    manifest_digest: str
    replacements: tuple[TraceReplacementBinding, ...]
    attempt_deliveries: tuple[TraceAttemptDeliveryBinding, ...]
    source_evidence_class: str
    paper_eligibility_disposition: str
    binding_digest: str
    schema_version: str = "tokenshare.trace_source_binding.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.trace_source_binding.v2":
            raise ValueError("unsupported trace source binding schema")
        _require_non_empty("planned_ai_unit_id", self.planned_ai_unit_id)
        _require_non_empty("bank_root_id", self.bank_root_id)
        _require_sha256("manifest_digest", self.manifest_digest)
        _require_non_empty("source_evidence_class", self.source_evidence_class)
        if isinstance(self.sample_slot_index, bool) or not isinstance(
            self.sample_slot_index, int
        ):
            raise TypeError("sample_slot_index must be an integer")
        if self.sample_slot_index < 0:
            raise ValueError("sample_slot_index must be non-negative")
        canonical = tuple(sorted(self.replacements, key=lambda item: item.replacement_slot))
        if not canonical or canonical != self.replacements:
            raise ValueError("trace replacements must be non-empty and canonically sorted")
        ordinals = tuple(item.replacement_slot for item in canonical)
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("trace replacement slots must be unique")
        deliveries = tuple(
            sorted(
                self.attempt_deliveries,
                key=lambda item: item.current_attempt_ordinal,
            )
        )
        if not deliveries or deliveries != self.attempt_deliveries:
            raise ValueError("trace attempt deliveries must be canonical")
        if tuple(item.current_attempt_ordinal for item in deliveries) != tuple(
            range(len(deliveries))
        ):
            raise ValueError("trace attempt delivery ordinals must be contiguous")
        if len(deliveries) != len(canonical):
            raise ValueError("trace replacements and attempt deliveries diverged")
        if any(
            delivery.current_attempt_ordinal != replacement.replacement_slot
            or delivery.source_entry_id != replacement.entry_id
            for delivery, replacement in zip(deliveries, canonical)
        ):
            raise ValueError("trace replacement/delivery identity drifted")
        first_redelivery = next(
            (
                index
                for index, item in enumerate(deliveries)
                if item.delivery_kind == "fault_redelivery"
            ),
            len(deliveries),
        )
        if first_redelivery == 0 or any(
            item.delivery_kind != "fault_redelivery"
            for item in deliveries[first_redelivery:]
        ):
            raise ValueError(
                "trace deliveries require an ordinary prefix and redelivery suffix"
            )
        terminal_ordinary = deliveries[first_redelivery - 1]
        if any(
            item.source_entry_id != terminal_ordinary.source_entry_id
            or item.source_attempt_index != terminal_ordinary.source_attempt_index
            or item.source_replacement_slot
            != terminal_ordinary.source_replacement_slot
            for item in deliveries[first_redelivery:]
        ):
            raise ValueError(
                "fault redelivery must reuse the terminal ordinary source attempt"
            )
        expected_disposition = _paper_disposition(self.source_evidence_class)
        if self.paper_eligibility_disposition != expected_disposition:
            raise ValueError("trace source paper eligibility disposition mismatch")
        expected_digest = canonical_digest(self._digest_body())
        if self.binding_digest != expected_digest:
            raise ValueError("trace source binding digest mismatch")

    @classmethod
    def create(
        cls,
        *,
        planned_ai_unit_id: str,
        sample_slot_index: int,
        bank_root_id: str,
        manifest_digest: str,
        replacements: Sequence[TraceReplacementBinding],
        attempt_deliveries: Sequence[TraceAttemptDeliveryBinding] | None = None,
        source_evidence_class: str,
    ) -> "TraceSourceBinding":
        canonical = tuple(sorted(replacements, key=lambda item: item.replacement_slot))
        canonical_deliveries = tuple(
            TraceAttemptDeliveryBinding(
                current_attempt_ordinal=item.replacement_slot,
                source_entry_id=item.entry_id,
                source_attempt_index=item.replacement_slot,
                source_replacement_slot=item.replacement_slot,
                delivery_kind="ordinary_attempt",
                redelivery_reason=None,
            )
            for item in canonical
        ) if attempt_deliveries is None else tuple(
            sorted(
                attempt_deliveries,
                key=lambda item: item.current_attempt_ordinal,
            )
        )
        body = {
            "schema_version": "tokenshare.trace_source_binding.v2",
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "bank_root_id": bank_root_id,
            "manifest_digest": manifest_digest,
            "replacements": [item.to_dict() for item in canonical],
            "attempt_deliveries": [
                item.to_dict() for item in canonical_deliveries
            ],
            "source_evidence_class": source_evidence_class,
            "paper_eligibility_disposition": _paper_disposition(source_evidence_class),
        }
        return cls(
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=sample_slot_index,
            bank_root_id=bank_root_id,
            manifest_digest=manifest_digest,
            replacements=canonical,
            attempt_deliveries=canonical_deliveries,
            source_evidence_class=source_evidence_class,
            paper_eligibility_disposition=body["paper_eligibility_disposition"],
            binding_digest=canonical_digest(body),
        )

    @property
    def paper_eligible(self) -> bool:
        # Task 18 才能把完整 traceability 事实提升为 eligible；本层只做 hard false。
        return self.paper_eligibility_disposition == "eligible"

    def replacement(self, attempt_ordinal: int) -> TraceReplacementBinding:
        matches = [
            item for item in self.replacements if item.replacement_slot == attempt_ordinal
        ]
        if len(matches) != 1:
            raise KeyError(
                f"no preregistered trace replacement for persisted ordinal {attempt_ordinal}"
            )
        return matches[0]

    def delivery(self, attempt_ordinal: int) -> TraceAttemptDeliveryBinding:
        matches = [
            item
            for item in self.attempt_deliveries
            if item.current_attempt_ordinal == attempt_ordinal
        ]
        if len(matches) != 1:
            raise KeyError(
                f"no preregistered trace delivery for persisted ordinal {attempt_ordinal}"
            )
        return matches[0]

    @property
    def terminal_ordinary_ordinal(self) -> int:
        return max(
            item.current_attempt_ordinal
            for item in self.attempt_deliveries
            if item.delivery_kind == "ordinary_attempt"
        )

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "sample_slot_index": self.sample_slot_index,
            "bank_root_id": self.bank_root_id,
            "manifest_digest": self.manifest_digest,
            "replacements": [item.to_dict() for item in self.replacements],
            "attempt_deliveries": [
                item.to_dict() for item in self.attempt_deliveries
            ],
            "source_evidence_class": self.source_evidence_class,
            "paper_eligibility_disposition": self.paper_eligibility_disposition,
        }

    def to_dict(self) -> JsonObject:
        return {**self._digest_body(), "binding_digest": self.binding_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TraceSourceBinding":
        expected = {
            "schema_version",
            "planned_ai_unit_id",
            "sample_slot_index",
            "bank_root_id",
            "manifest_digest",
            "replacements",
            "attempt_deliveries",
            "source_evidence_class",
            "paper_eligibility_disposition",
            "binding_digest",
        }
        if set(value) != expected:
            raise ValueError("trace source binding fields mismatch")
        return cls(
            schema_version=value["schema_version"],
            planned_ai_unit_id=value["planned_ai_unit_id"],
            sample_slot_index=value["sample_slot_index"],
            bank_root_id=value["bank_root_id"],
            manifest_digest=value["manifest_digest"],
            replacements=tuple(
                TraceReplacementBinding.from_dict(item)
                for item in value["replacements"]
            ),
            attempt_deliveries=tuple(
                TraceAttemptDeliveryBinding.from_dict(item)
                for item in value["attempt_deliveries"]
            ),
            source_evidence_class=value["source_evidence_class"],
            paper_eligibility_disposition=value["paper_eligibility_disposition"],
            binding_digest=value["binding_digest"],
        )


def freeze_trace_source_binding(
    resolver: ResponseBankResolver,
    *,
    planned_ai_unit_id: str,
    sample_slot_index: int,
    entry_ids: Sequence[str],
    source_evidence_class: str = APPROVED_REAL_SOURCE_EVIDENCE_CLASS,
) -> TraceSourceBinding:
    """从已验证 bank index 冻结 opaque replacement mapping。"""

    if not isinstance(resolver, ResponseBankResolver):
        raise TypeError("resolver must be a ResponseBankResolver")
    if not entry_ids:
        raise ValueError("entry_ids must be non-empty")
    row_by_entry = {row.entry_id: row for row in resolver.index.inventory_rows}
    replacements: list[TraceReplacementBinding] = []
    for entry_id in entry_ids:
        entry = resolver.entry(entry_id)
        row = row_by_entry.get(entry.entry_id)
        if row is None:
            raise ValueError("response bank entry has no inventory row")
        if (
            row.planned_ai_unit_id != planned_ai_unit_id
            or row.sample_slot_index != sample_slot_index
        ):
            raise ValueError("response bank entry does not match frozen AI-unit slot")
        replacements.append(
            TraceReplacementBinding(
                replacement_slot=entry.replacement_slot,
                entry_id=entry.entry_id,
                inference_request_digest=entry.inference_request_digest,
            )
        )
    manifest = resolver.index.manifest
    return TraceSourceBinding.create(
        planned_ai_unit_id=planned_ai_unit_id,
        sample_slot_index=sample_slot_index,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        replacements=replacements,
        source_evidence_class=source_evidence_class,
    )


def freeze_projected_trace_source_binding(
    resolver: ResponseBankResolver,
    *,
    current_planned_ai_unit_id: str,
    current_sample_slot_index: int,
    source_entry_ids_by_current_attempt: Sequence[str],
    delivery_kinds_by_current_attempt: Sequence[str] | None = None,
    redelivery_reasons_by_current_attempt: Sequence[str | None] | None = None,
    source_evidence_class: str = APPROVED_REAL_SOURCE_EVIDENCE_CLASS,
) -> TraceSourceBinding:
    """把 current attempt ordinal 显式绑定到既有 Exp1 source entry。

    多个 current ordinals 可以引用同一个 source entry；这代表协议重投，
    不是新的 provider attempt。source entry 仍由 resolver 做 digest/locator 校验。
    """

    if not isinstance(resolver, ResponseBankResolver):
        raise TypeError("resolver must be a ResponseBankResolver")
    _require_non_empty("current_planned_ai_unit_id", current_planned_ai_unit_id)
    if (
        isinstance(current_sample_slot_index, bool)
        or not isinstance(current_sample_slot_index, int)
        or current_sample_slot_index < 0
    ):
        raise ValueError("current_sample_slot_index must be non-negative")
    if not source_entry_ids_by_current_attempt:
        raise ValueError("projected trace source entries must be non-empty")
    delivery_kinds = (
        tuple("ordinary_attempt" for _ in source_entry_ids_by_current_attempt)
        if delivery_kinds_by_current_attempt is None
        else tuple(delivery_kinds_by_current_attempt)
    )
    redelivery_reasons = (
        tuple(None for _ in source_entry_ids_by_current_attempt)
        if redelivery_reasons_by_current_attempt is None
        else tuple(redelivery_reasons_by_current_attempt)
    )
    if (
        len(delivery_kinds) != len(source_entry_ids_by_current_attempt)
        or len(redelivery_reasons) != len(source_entry_ids_by_current_attempt)
    ):
        raise ValueError("projected trace delivery plan length mismatch")
    entries = tuple(
        resolver.entry(source_entry_id)
        for source_entry_id in source_entry_ids_by_current_attempt
    )
    replacements = tuple(
        TraceReplacementBinding(
            replacement_slot=current_ordinal,
            entry_id=entry.entry_id,
            inference_request_digest=entry.inference_request_digest,
        )
        for current_ordinal, entry in enumerate(entries)
    )
    attempt_deliveries = tuple(
        TraceAttemptDeliveryBinding(
            current_attempt_ordinal=current_ordinal,
            source_entry_id=entry.entry_id,
            source_attempt_index=entry.replacement_slot,
            source_replacement_slot=entry.replacement_slot,
            delivery_kind=delivery_kinds[current_ordinal],
            redelivery_reason=redelivery_reasons[current_ordinal],
        )
        for current_ordinal, entry in enumerate(entries)
    )
    manifest = resolver.index.manifest
    return TraceSourceBinding.create(
        planned_ai_unit_id=current_planned_ai_unit_id,
        sample_slot_index=current_sample_slot_index,
        bank_root_id=manifest.bank_root_id,
        manifest_digest=manifest.manifest_digest,
        replacements=replacements,
        attempt_deliveries=attempt_deliveries,
        source_evidence_class=source_evidence_class,
    )


def bind_trace_execution_request(
    request: ExecutionRequest,
    binding: TraceSourceBinding,
) -> ExecutionRequest:
    """dispatch 前把已持久化 attempt ordinal 绑定到冻结 replacement。"""

    if not isinstance(request, ExecutionRequest):
        raise TypeError("request must be an ExecutionRequest")
    if not isinstance(binding, TraceSourceBinding):
        raise TypeError("binding must be a TraceSourceBinding")
    replacement = binding.replacement(request.attempt_ordinal)
    delivery = binding.delivery(request.attempt_ordinal)
    hints = dict(request.soft_hints or {})
    for key, expected in {
        "planned_ai_unit_id": binding.planned_ai_unit_id,
        "sample_slot_index": binding.sample_slot_index,
    }.items():
        if key in hints and hints[key] != expected:
            raise ValueError(f"execution request {key} conflicts with trace binding")
        hints[key] = expected
    hints.update(
        {
            "replacement_slot": request.attempt_ordinal,
            "trace_source_binding_digest": binding.binding_digest,
            "trace_inference_request_digest": replacement.inference_request_digest,
            "trace_delivery_kind": delivery.delivery_kind,
            "trace_source_entry_id": delivery.source_entry_id,
            "trace_source_attempt_index": delivery.source_attempt_index,
            "trace_source_replacement_slot": delivery.source_replacement_slot,
            "trace_redelivery_reason": delivery.redelivery_reason,
        }
    )
    return replace(
        request,
        source_binding_digest=binding.binding_digest,
        soft_hints=hints,
    )


class TraceBackedExecutor:
    """只流式读取/校验外部对象并产生 PreparedTraceDelivery。"""

    def __init__(
        self,
        *,
        resolver: ResponseBankResolver,
        bindings: Sequence[TraceSourceBinding],
        current_run_id: str,
        stream_chunk_size: int = 64 * 1024,
    ) -> None:
        if not isinstance(resolver, ResponseBankResolver):
            raise TypeError("resolver must be a ResponseBankResolver")
        _require_non_empty("current_run_id", current_run_id)
        if stream_chunk_size <= 0:
            raise ValueError("stream_chunk_size must be positive")
        self._resolver = resolver
        self._bindings = _binding_index(bindings, resolver)
        self._current_run_id = current_run_id
        self._stream_chunk_size = stream_chunk_size

    @property
    def provider_call_count(self) -> int:
        return 0

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> PreparedTraceDelivery:
        del submission_id, submitted_at
        binding, entry = _resolve_request_entry(
            request=request,
            bindings=self._bindings,
            resolver=self._resolver,
        )
        logical_start_ms = _logical_dispatch_start_ms(request)
        objects = _read_entry_objects(
            self._resolver,
            entry,
            chunk_size=self._stream_chunk_size,
        )
        evidence_roles = {
            role: _json_object(objects[role], role=role)
            for role in ("provider_failure", "provenance", "latency")
            if role in objects
        }
        _validate_response_bank_evidence_role_bundle(
            terminal_kind=entry.terminal_kind,
            roles=evidence_roles,
        )
        if entry.terminal_kind == "success":
            terminal = _json_object(objects["raw_output"], role="raw_output")
            content_text = terminal.get("content_text")
            if not isinstance(content_text, str):
                raise ValueError("response bank raw_output requires content_text")
            parser_input = content_text.encode("utf-8")
        else:
            parser_input = objects["provider_failure"]
        latency = _json_object(objects["latency"], role="latency")
        source_api_latency_ms, source_api_latency_missing = (
            _parse_source_api_latency(latency)
        )
        attempt_delivery = binding.delivery(request.attempt_ordinal)
        source_api_latency_missing_count = int(
            source_api_latency_missing
            and attempt_delivery.delivery_kind == "ordinary_attempt"
        )
        protocol_operational_delay_ms = (
            source_api_latency_ms
            if source_api_latency_ms is not None
            else MISSING_SOURCE_PROTOCOL_OPERATIONAL_DELAY_MS
        )
        return PreparedTraceDelivery.create(
            current_run_id=self._current_run_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            attempt_ordinal=request.attempt_ordinal,
            binding_digest=binding.binding_digest,
            inference_request_digest=entry.inference_request_digest,
            bank_root_id=binding.bank_root_id,
            manifest_digest=binding.manifest_digest,
            entry_id=entry.entry_id,
            source_terminal_kind=entry.terminal_kind,
            source_bank_object_locators=tuple(
                locator.to_dict() for locator in entry.object_locators
            ),
            logical_start_ms=logical_start_ms,
            source_api_latency_ms=source_api_latency_ms,
            source_api_latency_missing=source_api_latency_missing,
            source_api_latency_missing_count=source_api_latency_missing_count,
            source_api_latency_ref=_source_api_latency_ref(binding, entry),
            protocol_operational_delay_ms=protocol_operational_delay_ms,
            parser_input_media_type="application/json",
            parser_input_digest=_digest_bytes(parser_input),
            child_worker_id="trace-child-unassigned",
            child_completion_sequence=0,
        )


@dataclass(frozen=True, kw_only=True)
class TraceDomainStageContext:
    delivery: PreparedTraceDelivery
    request: ExecutionRequest
    artifact_store: ArtifactStore
    current_wrapper_ref: ArtifactRef
    current_provenance_ref: ArtifactRef
    trace_attribution_ref: ArtifactRef
    parser_input_ref: ArtifactRef
    parser_input_text: str
    source_objects: Mapping[str, JsonObject]
    created_at: str


@dataclass(frozen=True, kw_only=True)
class TraceDomainStageResult:
    parser_result_ref: ArtifactRef
    verifier_checker_refs: tuple[ArtifactRef, ...] = ()
    canonical_ref: ArtifactRef | None = None
    execution_result_kind: str | None = None

    def __post_init__(self) -> None:
        if (
            self.execution_result_kind is not None
            and self.execution_result_kind not in TRACE_TERMINAL_EXECUTION_RESULT_KINDS
        ):
            raise ValueError("unsupported trace execution result kind")
        if self.execution_result_kind is not None and (
            self.verifier_checker_refs or self.canonical_ref is not None
        ):
            raise ValueError(
                "terminal trace execution result cannot include checker or canonical refs"
            )


class TraceDomainStage(Protocol):
    def stage(self, context: TraceDomainStageContext) -> TraceDomainStageResult: ...


class TraceBackedParentStager:
    """parent-only dual-provenance staging；adapter 拥有领域 parser/checker。"""

    def __init__(
        self,
        *,
        resolver: ResponseBankResolver,
        bindings: Sequence[TraceSourceBinding],
        domain_stage: TraceDomainStage,
        stream_chunk_size: int = 64 * 1024,
    ) -> None:
        self._resolver = resolver
        self._bindings = _binding_index(bindings, resolver)
        self._domain_stage = domain_stage
        self._stream_chunk_size = stream_chunk_size

    def stage(
        self,
        context: ParentTraceDeliveryStageContext,
    ) -> ParentStagedTraceDelivery:
        delivery = context.delivery
        binding, entry = _resolve_request_entry(
            request=context.request,
            bindings=self._bindings,
            resolver=self._resolver,
        )
        if (
            delivery.binding_digest != binding.binding_digest
            or delivery.entry_id != entry.entry_id
            or delivery.inference_request_digest != entry.inference_request_digest
            or delivery.source_terminal_kind != entry.terminal_kind
        ):
            raise ValueError("prepared delivery does not match frozen trace entry")
        expected_locators = tuple(
            locator.to_dict()
            for locator in sorted(entry.object_locators, key=lambda item: item.object_role)
        )
        delivered_locators = tuple(
            sorted(
                (dict(item) for item in delivery.source_bank_object_locators),
                key=lambda item: item["object_role"],
            )
        )
        if delivered_locators != expected_locators:
            raise ValueError("prepared delivery locator set does not match bank entry")
        raw_objects = _read_entry_objects(
            self._resolver, entry, chunk_size=self._stream_chunk_size
        )
        parsed_objects = MappingProxyType(
            {role: _json_object(data, role=role) for role, data in raw_objects.items()}
        )
        _validate_response_bank_evidence_role_bundle(
            terminal_kind=entry.terminal_kind,
            roles=parsed_objects,
        )
        latency_body = parsed_objects["latency"]
        staged_source_api_latency_ms, staged_source_api_latency_missing = (
            _parse_source_api_latency(latency_body)
        )
        attempt_delivery = binding.delivery(delivery.attempt_ordinal)
        expected_missing_count = int(
            staged_source_api_latency_missing
            and attempt_delivery.delivery_kind == "ordinary_attempt"
        )
        expected_operational_delay_ms = (
            staged_source_api_latency_ms
            if staged_source_api_latency_ms is not None
            else MISSING_SOURCE_PROTOCOL_OPERATIONAL_DELAY_MS
        )
        if (
            staged_source_api_latency_ms != delivery.source_api_latency_ms
            or staged_source_api_latency_missing
            != delivery.source_api_latency_missing
            or expected_missing_count
            != delivery.source_api_latency_missing_count
            or _source_api_latency_ref(binding, entry)
            != delivery.source_api_latency_ref
            or expected_operational_delay_ms
            != delivery.protocol_operational_delay_ms
        ):
            raise ValueError("prepared delivery latency does not match source object")
        digest_key = delivery.delivery_digest.removeprefix("sha256:")
        staged_source = {
            "kind": "trace_backed_parent_stage",
            "delivery_digest": delivery.delivery_digest,
        }

        provenance_ref = context.artifact_store.save_json(
            {
                "schema_version": "tokenshare.current_trace_provenance.v2",
                "current_run_id": delivery.current_run_id,
                "current_task_id": delivery.task_id,
                "current_unit_id": delivery.unit_id,
                "current_attempt_id": delivery.attempt_id,
                "attempt_ordinal": delivery.attempt_ordinal,
                "source_sample_slot_index": entry.sample_slot_index,
                "source_replacement_slot": entry.replacement_slot,
                "binding_digest": binding.binding_digest,
                "bank_root_id": binding.bank_root_id,
                "manifest_digest": binding.manifest_digest,
                "entry_id": entry.entry_id,
                "inference_request_digest": entry.inference_request_digest,
                "source_evidence_class": binding.source_evidence_class,
                "paper_eligibility_disposition": binding.paper_eligibility_disposition,
                "logical_start_ms": delivery.logical_start_ms,
                "source_api_latency_ms": delivery.source_api_latency_ms,
                "source_api_latency_missing": delivery.source_api_latency_missing,
                "source_api_latency_missing_count": (
                    delivery.source_api_latency_missing_count
                ),
                "source_api_latency_ref": delivery.source_api_latency_ref,
                "protocol_operational_delay_ms": (
                    delivery.protocol_operational_delay_ms
                ),
                "logical_finish_ms": delivery.logical_finish_ms,
                "current_provider_call_count": 0,
                "current_provider_spend": 0,
            },
            artifact_id=f"trace_current_provenance_{digest_key}",
            artifact_type="CurrentTraceProvenance",
            artifact_schema_id="tokenshare.current_trace_provenance",
            artifact_schema_version="v2",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=context.created_at,
        )
        attribution_ref = context.artifact_store.save_json(
            {
                "schema_version": "tokenshare.trace_attribution.v2",
                "delivery_digest": delivery.delivery_digest,
                "source_usage_class": "trace_attribution",
                "source_terminal_kind": entry.terminal_kind,
                "source_evidence_class": binding.source_evidence_class,
                "source_entry_id": entry.entry_id,
                "source_sample_slot_index": entry.sample_slot_index,
                "source_replacement_slot": entry.replacement_slot,
                "current_attempt_ordinal": delivery.attempt_ordinal,
                "source_usage": parsed_objects["usage"],
                "source_latency": parsed_objects["latency"],
                "source_api_latency_ms": delivery.source_api_latency_ms,
                "source_api_latency_missing": delivery.source_api_latency_missing,
                "source_api_latency_missing_count": (
                    delivery.source_api_latency_missing_count
                ),
                "source_api_latency_ref": delivery.source_api_latency_ref,
                "protocol_operational_delay_ms": (
                    delivery.protocol_operational_delay_ms
                ),
                "source_pricing": parsed_objects["pricing"],
                "source_acquisition_attempt": parsed_objects["acquisition_attempt"],
                "source_model_record": parsed_objects["model_record"],
                "source_object_digests": {
                    locator.object_role: locator.object_digest
                    for locator in entry.object_locators
                },
                "current_provider_call_count": 0,
                "current_provider_spend": 0,
            },
            artifact_id=f"trace_attribution_{digest_key}",
            artifact_type="TraceAttribution",
            artifact_schema_id="tokenshare.trace_attribution",
            artifact_schema_version="v2",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=context.created_at,
        )
        if entry.terminal_kind == "provider_failure":
            if (
                _digest_bytes(raw_objects["provider_failure"])
                != delivery.parser_input_digest
            ):
                raise ValueError("prepared provider failure digest mismatch")
            failure = parsed_objects["provider_failure"]
            parser_result_ref = context.artifact_store.save_json(
                {
                    "schema_version": "tokenshare.trace_provider_failure.v1",
                    "failure_kind": failure.get("failure_kind", "provider_failure"),
                    "http_status": failure.get("http_status"),
                    "message": failure.get("message", "source provider failure"),
                    "source_terminal_kind": "provider_failure",
                    "current_provider_call_count": 0,
                    "current_provider_spend": 0,
                },
                artifact_id=f"trace_provider_failure_{digest_key}",
                artifact_type="TraceProviderFailure",
                artifact_schema_id="tokenshare.trace_provider_failure",
                artifact_schema_version="v1",
                source=staged_source,
                metadata={"attempt_id": delivery.attempt_id},
                created_at=context.created_at,
            )
            domain_result = TraceDomainStageResult(
                parser_result_ref=parser_result_ref
            )
        else:
            raw_output = parsed_objects["raw_output"]
            content_text = raw_output.get("content_text")
            if not isinstance(content_text, str):
                raise ValueError("response bank raw_output requires content_text")
            if _digest_bytes(content_text.encode("utf-8")) != delivery.parser_input_digest:
                raise ValueError("prepared parser input digest mismatch")
            domain_result = self._domain_stage.stage(
                TraceDomainStageContext(
                    delivery=delivery,
                    request=context.request,
                    artifact_store=context.artifact_store,
                    current_wrapper_ref=context.current_wrapper_ref,
                    current_provenance_ref=provenance_ref,
                    trace_attribution_ref=attribution_ref,
                    parser_input_ref=context.parser_input_ref,
                    parser_input_text=content_text,
                    source_objects=parsed_objects,
                    created_at=context.created_at,
                )
            )
            if not isinstance(domain_result, TraceDomainStageResult):
                raise TypeError("domain trace stage must return TraceDomainStageResult")
        return ParentStagedTraceDelivery(
            parser_result_ref=domain_result.parser_result_ref,
            current_provenance_ref=provenance_ref,
            verifier_checker_refs=domain_result.verifier_checker_refs,
            canonical_ref=domain_result.canonical_ref,
            trace_attribution_refs=(attribution_ref,),
            execution_result_kind=domain_result.execution_result_kind,
        )


def _binding_index(
    bindings: Sequence[TraceSourceBinding],
    resolver: ResponseBankResolver,
) -> dict[str, TraceSourceBinding]:
    manifest = resolver.index.manifest
    result: dict[str, TraceSourceBinding] = {}
    for binding in bindings:
        if (
            binding.bank_root_id != manifest.bank_root_id
            or binding.manifest_digest != manifest.manifest_digest
        ):
            raise ValueError("trace binding root does not match resolver")
        if binding.binding_digest in result:
            raise ValueError("trace binding digests must be unique")
        result[binding.binding_digest] = binding
    if not result:
        raise ValueError("bindings must be non-empty")
    return result


def _resolve_request_entry(
    *,
    request: ExecutionRequest,
    bindings: Mapping[str, TraceSourceBinding],
    resolver: ResponseBankResolver,
) -> tuple[TraceSourceBinding, ResponseBankEntry]:
    if not isinstance(request, ExecutionRequest):
        raise TypeError("request must be an ExecutionRequest")
    try:
        binding = bindings[request.source_binding_digest]
    except (KeyError, TypeError) as exc:
        raise ValueError("execution request has no registered source binding") from exc
    replacement = binding.replacement(request.attempt_ordinal)
    delivery = binding.delivery(request.attempt_ordinal)
    hints = dict(request.soft_hints or {})
    expected_hints = {
        "planned_ai_unit_id": binding.planned_ai_unit_id,
        "sample_slot_index": binding.sample_slot_index,
        "replacement_slot": request.attempt_ordinal,
        "trace_source_binding_digest": binding.binding_digest,
        "trace_inference_request_digest": replacement.inference_request_digest,
        "trace_delivery_kind": delivery.delivery_kind,
        "trace_source_entry_id": delivery.source_entry_id,
        "trace_source_attempt_index": delivery.source_attempt_index,
        "trace_source_replacement_slot": delivery.source_replacement_slot,
        "trace_redelivery_reason": delivery.redelivery_reason,
    }
    if any(hints.get(key) != value for key, value in expected_hints.items()):
        raise ValueError("execution request trace hints do not match frozen binding")
    entry = resolver.entry(replacement.entry_id)
    if (
        entry.inference_request_digest != replacement.inference_request_digest
        or entry.entry_id != delivery.source_entry_id
        or entry.replacement_slot != delivery.source_attempt_index
        or entry.replacement_slot != delivery.source_replacement_slot
    ):
        raise ValueError("response bank entry does not match persisted attempt ordinal")
    return binding, entry


def _read_entry_objects(
    resolver: ResponseBankResolver,
    entry: ResponseBankEntry,
    *,
    chunk_size: int,
) -> dict[str, bytes]:
    return {
        locator.object_role: resolver.read_verified(locator, chunk_size=chunk_size)
        for locator in entry.object_locators
    }


def _json_object(data: bytes, *, role: str) -> JsonObject:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"response bank {role} is not canonical JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"response bank {role} must be a JSON object")
    return value


_V2_PROVIDER_FAILURE_SCHEMA = "tokenshare.response_bank_provider_failure.v2"
_V2_PROVENANCE_SCHEMA = "tokenshare.response_bank_provenance.v2"
_V2_LATENCY_SCHEMA = "tokenshare.response_bank_latency.v2"
_V2_EVIDENCE_KINDS = frozenset(
    {"hard_deadline_child", "supervised_stopped_attempt"}
)


def _validate_response_bank_evidence_role_bundle(
    *,
    terminal_kind: str,
    roles: Mapping[str, Mapping[str, object]],
) -> None:
    """严格校验新增 v2 终态证据；旧 v1 回放继续走原字段语义。"""

    provenance = roles.get("provenance")
    latency = roles.get("latency")
    failure = roles.get("provider_failure")
    if provenance is None or latency is None:
        raise ValueError("response bank terminal evidence roles are incomplete")
    schemas = {
        provenance.get("schema_version"),
        latency.get("schema_version"),
        None if failure is None else failure.get("schema_version"),
    }
    if not schemas.intersection(
        {_V2_PROVIDER_FAILURE_SCHEMA, _V2_PROVENANCE_SCHEMA, _V2_LATENCY_SCHEMA}
    ):
        return
    if (
        provenance.get("schema_version") != _V2_PROVENANCE_SCHEMA
        or latency.get("schema_version") != _V2_LATENCY_SCHEMA
        or terminal_kind == "provider_failure"
        and (
            failure is None
            or failure.get("schema_version") != _V2_PROVIDER_FAILURE_SCHEMA
        )
    ):
        raise ValueError("response bank v2 role schemas must advance atomically")

    evidence_kind = provenance.get("evidence_kind")
    if evidence_kind not in _V2_EVIDENCE_KINDS:
        raise ValueError("response bank v2 evidence kind is unsupported")
    if latency.get("evidence_kind") != evidence_kind or (
        failure is not None and failure.get("evidence_kind") != evidence_kind
    ):
        raise ValueError("response bank v2 evidence kinds drifted")
    _validate_v2_provenance(provenance, evidence_kind=str(evidence_kind))
    _validate_v2_latency(latency, evidence_kind=str(evidence_kind))
    if failure is not None:
        _validate_v2_provider_failure(failure, evidence_kind=str(evidence_kind))

    if evidence_kind == "supervised_stopped_attempt":
        if terminal_kind != "provider_failure" or failure is None:
            raise ValueError("supervised v2 evidence requires provider failure")
        _validate_supervised_v2_cross_role(
            failure=failure,
            provenance=provenance,
            latency=latency,
        )
    else:
        _validate_hard_deadline_v2_cross_role(
            terminal_kind=terminal_kind,
            failure=failure,
            provenance=provenance,
            latency=latency,
        )


def _validate_v2_provenance(
    provenance: Mapping[str, object], *, evidence_kind: str
) -> None:
    common = {
        "schema_version",
        "evidence_kind",
        "provider_family",
        "entry_id",
        "provider_config_digest",
        "inference_request_digest",
        "normalized_absolute_endpoint",
        "transport_call_count",
        "secret_persisted",
    }
    variant = (
        {
            "closure_provider_call_count",
            "closure_authority_digest",
            "stop_evidence_digest",
        }
        if evidence_kind == "supervised_stopped_attempt"
        else {
            "hard_deadline_evidence_digest",
            "network_start_acknowledged",
            "result_observed_before_deadline",
            "late_result_rejected",
            "post_reap_late_result_absent",
            "child_reaped",
            "parent_only_evidence_consumer",
            "ephemeral_files_secret_free",
        }
    )
    paid = {"receipt_digest"}
    internal = {"authorization_kind", "authorization_digest"}
    if set(provenance) == common | variant | paid:
        _require_sha256("receipt_digest", provenance["receipt_digest"])
    elif set(provenance) == common | variant | internal:
        _require_non_empty("authorization_kind", provenance["authorization_kind"])
        _require_sha256("authorization_digest", provenance["authorization_digest"])
    else:
        raise ValueError("response bank provenance v2 fields drifted")
    for name in ("provider_family", "entry_id", "normalized_absolute_endpoint"):
        _require_non_empty(name, provenance[name])
    for name in ("provider_config_digest", "inference_request_digest"):
        _require_sha256(name, provenance[name])
    if type(provenance["transport_call_count"]) is not int or (
        provenance["transport_call_count"] != 1
    ):
        raise ValueError("response bank provenance v2 transport count drifted")
    if provenance["secret_persisted"] is not False:
        raise ValueError("response bank provenance v2 cannot persist a secret")
    if evidence_kind == "supervised_stopped_attempt":
        if type(provenance["closure_provider_call_count"]) is not int or (
            provenance["closure_provider_call_count"] != 0
        ):
            raise ValueError("supervised v2 closure provider count drifted")
        _require_sha256(
            "closure_authority_digest", provenance["closure_authority_digest"]
        )
        _require_sha256("stop_evidence_digest", provenance["stop_evidence_digest"])
    else:
        _require_sha256(
            "hard_deadline_evidence_digest",
            provenance["hard_deadline_evidence_digest"],
        )
        for name in (
            "network_start_acknowledged",
            "result_observed_before_deadline",
            "late_result_rejected",
            "post_reap_late_result_absent",
            "child_reaped",
            "parent_only_evidence_consumer",
            "ephemeral_files_secret_free",
        ):
            if type(provenance[name]) is not bool:
                raise ValueError("hard-deadline provenance v2 boolean drifted")


def _validate_v2_provider_failure(
    failure: Mapping[str, object], *, evidence_kind: str
) -> None:
    common = {
        "schema_version",
        "evidence_kind",
        "failure_kind",
        "http_status",
        "message",
        "raw_response_json",
        "transport_call_count",
    }
    variant = (
        {
            "closure_provider_call_count",
            "closure_authority_digest",
            "stop_evidence_digest",
        }
        if evidence_kind == "supervised_stopped_attempt"
        else {"hard_deadline_evidence"}
    )
    if set(failure) != common | variant:
        raise ValueError("response bank provider failure v2 fields drifted")
    _require_non_empty("failure_kind", failure["failure_kind"])
    _require_non_empty("message", failure["message"])
    status = failure["http_status"]
    if status is not None and (type(status) is not int or status < 100):
        raise ValueError("response bank provider failure v2 status drifted")
    if failure["raw_response_json"] is not None:
        raise ValueError("response bank provider failure v2 raw body drifted")
    if type(failure["transport_call_count"]) is not int or (
        failure["transport_call_count"] != 1
    ):
        raise ValueError("response bank provider failure v2 call count drifted")
    if evidence_kind == "supervised_stopped_attempt":
        if type(failure["closure_provider_call_count"]) is not int or (
            failure["closure_provider_call_count"] != 0
        ):
            raise ValueError("supervised v2 closure provider count drifted")
        _require_sha256(
            "closure_authority_digest", failure["closure_authority_digest"]
        )
        _require_sha256("stop_evidence_digest", failure["stop_evidence_digest"])
    elif not isinstance(failure["hard_deadline_evidence"], Mapping):
        raise ValueError("hard-deadline v2 proof must be an object")


def _validate_v2_latency(
    latency: Mapping[str, object], *, evidence_kind: str
) -> None:
    common = {
        "schema_version",
        "evidence_kind",
        "latency_ms",
        "latency_missing",
        "timing_source",
    }
    variant = (
        {
            "observed_inflight_lower_bound_ms",
            "unchanged_observation_window_ms",
            "stop_evidence_digest",
        }
        if evidence_kind == "supervised_stopped_attempt"
        else {"hard_deadline_evidence_digest", "observed_wall_clock_ms"}
    )
    if set(latency) != common | variant:
        raise ValueError("response bank latency v2 fields drifted")
    value = latency["latency_ms"]
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("response bank latency v2 value drifted")
    if type(latency["latency_missing"]) is not bool or (
        latency["latency_missing"] is not (value is None)
    ):
        raise ValueError("response bank latency v2 missingness drifted")
    _require_non_empty("timing_source", latency["timing_source"])
    if evidence_kind == "supervised_stopped_attempt":
        for name, minimum in (
            ("observed_inflight_lower_bound_ms", 720_000),
            ("unchanged_observation_window_ms", 120_000),
        ):
            if type(latency[name]) is not int or latency[name] < minimum:
                raise ValueError("supervised v2 observation threshold drifted")
        _require_sha256("stop_evidence_digest", latency["stop_evidence_digest"])
    else:
        _require_sha256(
            "hard_deadline_evidence_digest",
            latency["hard_deadline_evidence_digest"],
        )
        if (
            type(latency["observed_wall_clock_ms"]) is not int
            or latency["observed_wall_clock_ms"] < 0
        ):
            raise ValueError("hard-deadline v2 wall clock drifted")


def _validate_supervised_v2_cross_role(
    *,
    failure: Mapping[str, object],
    provenance: Mapping[str, object],
    latency: Mapping[str, object],
) -> None:
    if (
        failure["failure_kind"] != "no_response"
        or failure["http_status"] is not None
        or latency["latency_ms"] is not None
        or latency["latency_missing"] is not True
        or latency["timing_source"] != "supervised_no_terminal_response"
    ):
        raise ValueError("supervised v2 terminal semantics drifted")
    for name in ("closure_authority_digest", "stop_evidence_digest"):
        values = {failure[name], provenance[name]}
        if name == "stop_evidence_digest":
            values.add(latency[name])
        if len(values) != 1:
            raise ValueError("supervised v2 cross-role digest drifted")


def _validate_hard_deadline_v2_cross_role(
    *,
    terminal_kind: str,
    failure: Mapping[str, object] | None,
    provenance: Mapping[str, object],
    latency: Mapping[str, object],
) -> None:
    if provenance["network_start_acknowledged"] is not True:
        raise ValueError("hard-deadline v2 lacks network start acknowledgement")
    for name in (
        "post_reap_late_result_absent",
        "child_reaped",
        "parent_only_evidence_consumer",
        "ephemeral_files_secret_free",
    ):
        if provenance[name] is not True:
            raise ValueError("hard-deadline v2 is not quiescent")
    digest = provenance["hard_deadline_evidence_digest"]
    if latency["hard_deadline_evidence_digest"] != digest:
        raise ValueError("hard-deadline v2 cross-role digest drifted")
    if failure is not None:
        proof = failure["hard_deadline_evidence"]
        assert isinstance(proof, Mapping)
        _validate_hard_deadline_v1_proof(proof)
        if canonical_digest(dict(proof)) != digest:
            raise ValueError("hard-deadline v2 proof digest drifted")
        if latency["observed_wall_clock_ms"] != proof["observed_wall_clock_ms"]:
            raise ValueError("hard-deadline v2 wall clock drifted")
        for name in (
            "network_start_acknowledged",
            "result_observed_before_deadline",
            "late_result_rejected",
            "post_reap_late_result_absent",
            "child_reaped",
            "parent_only_evidence_consumer",
            "ephemeral_files_secret_free",
        ):
            if provenance[name] != proof[name]:
                raise ValueError("hard-deadline v2 proof projection drifted")
    observed = provenance["result_observed_before_deadline"]
    if terminal_kind == "success" and observed is not True:
        raise ValueError("hard-deadline v2 success was not observed on time")
    if observed is False and (
        failure is None
        or failure["failure_kind"] != "no_response"
        or latency["latency_ms"] is not None
        or latency["latency_missing"] is not True
        or latency["timing_source"] != "unknown_no_response"
    ):
        raise ValueError("hard-deadline v2 no-response semantics drifted")


def _validate_hard_deadline_v1_proof(proof: Mapping[str, object]) -> None:
    expected = {
        "schema_version",
        "deadline_enforced",
        "hard_total_seconds",
        "observed_wall_clock_ms",
        "child_pid",
        "child_exit_code",
        "terminate_attempted",
        "kill_attempted",
        "child_reaped",
        "network_start_acknowledged",
        "result_observed_before_deadline",
        "child_completed_before_parent_deadline",
        "result_commit_count",
        "accepted_result_commit_count",
        "late_result_rejected",
        "post_reap_late_result_absent",
        "parent_only_evidence_consumer",
        "ephemeral_files_secret_free",
        "request_file_sha256",
    }
    if set(proof) != expected or proof["schema_version"] != (
        "tokenshare.ai_api_hard_deadline_quiescence.v1"
    ):
        raise ValueError("hard-deadline v2 proof schema drifted")
    bool_names = (
        "deadline_enforced",
        "terminate_attempted",
        "kill_attempted",
        "child_reaped",
        "network_start_acknowledged",
        "result_observed_before_deadline",
        "child_completed_before_parent_deadline",
        "late_result_rejected",
        "post_reap_late_result_absent",
        "parent_only_evidence_consumer",
        "ephemeral_files_secret_free",
    )
    if any(type(proof[name]) is not bool for name in bool_names):
        raise ValueError("hard-deadline v2 proof boolean drifted")
    for name in (
        "observed_wall_clock_ms",
        "child_pid",
        "result_commit_count",
        "accepted_result_commit_count",
    ):
        if type(proof[name]) is not int or proof[name] < 0:
            raise ValueError("hard-deadline v2 proof integer drifted")
    if type(proof["child_exit_code"]) is not int:
        raise ValueError("hard-deadline v2 child exit drifted")
    hard_total = proof["hard_total_seconds"]
    if type(hard_total) not in {int, float} or not (0 < hard_total < float("inf")):
        raise ValueError("hard-deadline v2 total deadline drifted")
    if (
        proof["deadline_enforced"] is not True
        or proof["child_reaped"] is not True
        or proof["post_reap_late_result_absent"] is not True
        or proof["parent_only_evidence_consumer"] is not True
        or proof["ephemeral_files_secret_free"] is not True
    ):
        raise ValueError("hard-deadline v2 proof is not quiescent")
    _require_sha256("request_file_sha256", proof["request_file_sha256"])


def _parse_source_api_latency(
    latency: Mapping[str, object],
) -> tuple[int | None, bool]:
    source_api_latency_ms = latency.get(
        "latency_ms", latency.get("milliseconds")
    )
    missing_marker = latency.get("latency_missing")
    if source_api_latency_ms is None:
        if missing_marker is not None and missing_marker is not True:
            raise ValueError(
                "response bank missing latency requires latency_missing=true"
            )
        if (
            missing_marker is None
            and latency.get("schema_version")
            != "tokenshare.response_bank_latency.v1"
        ):
            raise ValueError(
                "response bank missing latency requires canonical latency schema"
            )
        return None, True
    if (
        isinstance(source_api_latency_ms, bool)
        or not isinstance(source_api_latency_ms, int)
        or source_api_latency_ms < 0
    ):
        raise ValueError(
            "response bank latency requires non-negative integer latency_ms"
        )
    if missing_marker not in {None, False}:
        raise ValueError("observed response bank latency cannot be marked missing")
    return source_api_latency_ms, False


def _source_api_latency_ref(
    binding: TraceSourceBinding,
    entry: ResponseBankEntry,
) -> str:
    return f"response-bank:{binding.bank_root_id}:{entry.entry_id}:latency"


def _paper_disposition(source_evidence_class: str) -> str:
    _require_non_empty("source_evidence_class", source_evidence_class)
    if source_evidence_class in SYNTHETIC_SOURCE_EVIDENCE_CLASSES:
        return "hard_false"
    if source_evidence_class == APPROVED_REAL_SOURCE_EVIDENCE_CLASS:
        return "deferred"
    return "hard_false"


def _logical_dispatch_start_ms(request: ExecutionRequest) -> int:
    value = dict(request.soft_hints or {}).get(LOGICAL_DISPATCH_START_MS_HINT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "execution request requires a non-negative logical dispatch start"
        )
    return value


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_sha256(name: str, value: object) -> None:
    if not (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a sha256 digest")
