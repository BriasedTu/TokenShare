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
)
from tokenshare.storage.artifacts import ArtifactStore


APPROVED_REAL_SOURCE_EVIDENCE_CLASS = "approved_real_api_acquisition"
SYNTHETIC_SOURCE_EVIDENCE_CLASSES = frozenset(
    {"synthetic_regression", "scripted_regression", "deterministic_regression"}
)


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
class TraceSourceBinding:
    planned_ai_unit_id: str
    sample_slot_index: int
    bank_root_id: str
    manifest_digest: str
    replacements: tuple[TraceReplacementBinding, ...]
    source_evidence_class: str
    paper_eligibility_disposition: str
    binding_digest: str
    schema_version: str = "tokenshare.trace_source_binding.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.trace_source_binding.v1":
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
        source_evidence_class: str,
    ) -> "TraceSourceBinding":
        canonical = tuple(sorted(replacements, key=lambda item: item.replacement_slot))
        body = {
            "schema_version": "tokenshare.trace_source_binding.v1",
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": sample_slot_index,
            "bank_root_id": bank_root_id,
            "manifest_digest": manifest_digest,
            "replacements": [item.to_dict() for item in canonical],
            "source_evidence_class": source_evidence_class,
            "paper_eligibility_disposition": _paper_disposition(source_evidence_class),
        }
        return cls(
            planned_ai_unit_id=planned_ai_unit_id,
            sample_slot_index=sample_slot_index,
            bank_root_id=bank_root_id,
            manifest_digest=manifest_digest,
            replacements=canonical,
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

    def _digest_body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "planned_ai_unit_id": self.planned_ai_unit_id,
            "sample_slot_index": self.sample_slot_index,
            "bank_root_id": self.bank_root_id,
            "manifest_digest": self.manifest_digest,
            "replacements": [item.to_dict() for item in self.replacements],
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
        if entry.terminal_kind == "success":
            terminal = _json_object(objects["raw_output"], role="raw_output")
            content_text = terminal.get("content_text")
            if not isinstance(content_text, str):
                raise ValueError("response bank raw_output requires content_text")
            parser_input = content_text.encode("utf-8")
        else:
            parser_input = objects["provider_failure"]
        latency = _json_object(objects["latency"], role="latency")
        source_latency_ms = latency.get("latency_ms", latency.get("milliseconds"))
        if isinstance(source_latency_ms, bool) or not isinstance(source_latency_ms, int):
            raise ValueError("response bank latency requires integer latency_ms")
        if source_latency_ms < 0:
            raise ValueError("response bank latency must be non-negative")
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
            source_latency_ms=source_latency_ms,
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
    parser_input_ref: ArtifactRef
    parser_input_text: str
    source_objects: Mapping[str, JsonObject]
    created_at: str


@dataclass(frozen=True, kw_only=True)
class TraceDomainStageResult:
    parser_result_ref: ArtifactRef
    verifier_checker_refs: tuple[ArtifactRef, ...] = ()
    canonical_ref: ArtifactRef | None = None


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
            or delivery.attempt_ordinal != entry.replacement_slot
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
        latency_body = parsed_objects["latency"]
        staged_latency_ms = latency_body.get(
            "latency_ms", latency_body.get("milliseconds")
        )
        if staged_latency_ms != delivery.source_latency_ms:
            raise ValueError("prepared delivery latency does not match source object")
        digest_key = delivery.delivery_digest.removeprefix("sha256:")
        staged_source = {
            "kind": "trace_backed_parent_stage",
            "delivery_digest": delivery.delivery_digest,
        }

        provenance_ref = context.artifact_store.save_json(
            {
                "schema_version": "tokenshare.current_trace_provenance.v1",
                "current_run_id": delivery.current_run_id,
                "current_task_id": delivery.task_id,
                "current_unit_id": delivery.unit_id,
                "current_attempt_id": delivery.attempt_id,
                "attempt_ordinal": delivery.attempt_ordinal,
                "binding_digest": binding.binding_digest,
                "bank_root_id": binding.bank_root_id,
                "manifest_digest": binding.manifest_digest,
                "entry_id": entry.entry_id,
                "inference_request_digest": entry.inference_request_digest,
                "source_evidence_class": binding.source_evidence_class,
                "paper_eligibility_disposition": binding.paper_eligibility_disposition,
                "logical_start_ms": delivery.logical_start_ms,
                "source_latency_ms": delivery.source_latency_ms,
                "logical_finish_ms": delivery.logical_finish_ms,
                "current_provider_call_count": 0,
                "current_provider_spend": 0,
            },
            artifact_id=f"trace_current_provenance_{digest_key}",
            artifact_type="CurrentTraceProvenance",
            artifact_schema_id="tokenshare.current_trace_provenance",
            artifact_schema_version="v1",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=context.created_at,
        )
        attribution_ref = context.artifact_store.save_json(
            {
                "schema_version": "tokenshare.trace_attribution.v1",
                "delivery_digest": delivery.delivery_digest,
                "source_usage_class": "trace_attribution",
                "source_terminal_kind": entry.terminal_kind,
                "source_evidence_class": binding.source_evidence_class,
                "source_usage": parsed_objects["usage"],
                "source_latency": parsed_objects["latency"],
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
            artifact_schema_version="v1",
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
    hints = dict(request.soft_hints or {})
    expected_hints = {
        "planned_ai_unit_id": binding.planned_ai_unit_id,
        "sample_slot_index": binding.sample_slot_index,
        "replacement_slot": request.attempt_ordinal,
        "trace_source_binding_digest": binding.binding_digest,
        "trace_inference_request_digest": replacement.inference_request_digest,
    }
    if any(hints.get(key) != value for key, value in expected_hints.items()):
        raise ValueError("execution request trace hints do not match frozen binding")
    entry = resolver.entry(replacement.entry_id)
    if (
        entry.replacement_slot != request.attempt_ordinal
        or entry.inference_request_digest != replacement.inference_request_digest
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
