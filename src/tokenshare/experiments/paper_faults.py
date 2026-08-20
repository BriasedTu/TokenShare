"""Post-AI paper fault injection records.

该模块只处理实验层故障变换：真实 raw output 已经落盘后，按固定 seed
选择 AI unit，并写入可审计的 mutation artifact 和 FaultInjectionRecord。
它不调用 provider，不修改协议 core 默认语义，也不把 synthetic mutation
记作 provider token。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from math import ceil
from typing import Any, Iterable

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    digest_json,
)
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ParsedCandidateContext,
    ParsedCandidateDirective,
    RawOutputContext,
    RawOutputDirective,
    RuntimeHookObservationV1,
    build_experiment_fault_injected_observation,
)
from tokenshare.storage.artifacts import ArtifactStore


MUTATED_OUTPUT_SCHEMA_VERSION = "tokenshare.paper_fault_mutated_output.v1"
FAULT_RECORD_SCHEMA_VERSION = "tokenshare.paper_fault_injection.v1"
FAULT_TARGET_SCHEMA_VERSION = "tokenshare.paper_fault_target.v1"
_LEAN_FALSE_NEGATIVE_SUPPRESSED_PROOF_SOURCE = (
    "by\n  exact tokenshare_false_negative_suppressed_candidate"
)


class PaperFaultType(str, Enum):
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    NO_RETURN = "no_return"
    LATE_SUBMISSION = "late_submission"
    EXECUTOR_ERROR = "executor_error"


@dataclass(frozen=True, kw_only=True)
class FaultTargetDescriptor:
    unit_id: str
    target_kind: str = "ai_unit"
    attempt_id: str | None = None
    artifact_ref: JsonObject | None = None
    slot_key: str | None = None
    child_logical_key: str | None = None
    dependency_path: tuple[str, ...] = ()
    lemma_node_id: str | None = None
    parent_node_id: str | None = None
    merge_slot: str | None = None
    domain_target: JsonObject | None = None
    schema_version: str = FAULT_TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_non_empty_str(self.unit_id, "target.unit_id")
        _required_non_empty_str(self.target_kind, "target.target_kind")
        if self.attempt_id is not None:
            _required_non_empty_str(self.attempt_id, "target.attempt_id")
        for field_name in (
            "slot_key",
            "child_logical_key",
            "lemma_node_id",
            "parent_node_id",
            "merge_slot",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _required_non_empty_str(value, f"target.{field_name}")
        if not isinstance(self.dependency_path, tuple):
            object.__setattr__(self, "dependency_path", tuple(self.dependency_path))
        for index, value in enumerate(self.dependency_path):
            _required_non_empty_str(value, f"target.dependency_path[{index}]")
        if self.artifact_ref is not None and not isinstance(self.artifact_ref, dict):
            raise ValueError("target.artifact_ref must be an artifact ref object")
        if self.domain_target is not None and not isinstance(self.domain_target, dict):
            raise ValueError("target.domain_target must be an object")

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "target_kind": self.target_kind,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "artifact_ref": (
                _deepcopy_json(self.artifact_ref)
                if self.artifact_ref is not None
                else None
            ),
            "slot_key": self.slot_key,
            "child_logical_key": self.child_logical_key,
            "dependency_path": list(self.dependency_path),
            "lemma_node_id": self.lemma_node_id,
            "parent_node_id": self.parent_node_id,
            "merge_slot": self.merge_slot,
            "domain_target": (
                _deepcopy_json(self.domain_target)
                if self.domain_target is not None
                else None
            ),
        }


@dataclass(frozen=True, kw_only=True)
class FaultInjectionRecord:
    fault_id: str
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    worker_id: str
    fault_type: PaperFaultType | str
    seed: int
    target_context: JsonObject
    target_selection_digest: str
    injection_point: str
    original_raw_output_ref: JsonObject
    original_output_ref: JsonObject
    mutated_output_ref: JsonObject
    suppressed_output_ref: JsonObject | None
    original_attempt_status: PaperAttemptStatus | str
    mutated_attempt_status: PaperAttemptStatus | str
    error_kind: str | None
    lease_deadline_at: str | None
    submitted_at: str | None
    canonical_pollution: bool
    recovery_required: bool
    retry_required: bool
    recovery_class: str
    simulated_mutation_count: int
    provider_tokens_attributed: int
    mutation_summary: JsonObject
    created_at: str
    schema_version: str = FAULT_RECORD_SCHEMA_VERSION

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "fault_id": self.fault_id,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "worker_id": self.worker_id,
            "fault_type": _enum_value(self.fault_type),
            "seed": self.seed,
            "target_context": dict(self.target_context),
            "target_selection_digest": self.target_selection_digest,
            "injection_point": self.injection_point,
            "original_raw_output_ref": dict(self.original_raw_output_ref),
            "original_output_ref": dict(self.original_output_ref),
            "mutated_output_ref": dict(self.mutated_output_ref),
            "suppressed_output_ref": (
                dict(self.suppressed_output_ref)
                if self.suppressed_output_ref is not None
                else None
            ),
            "original_attempt_status": _enum_value(self.original_attempt_status),
            "mutated_attempt_status": _enum_value(self.mutated_attempt_status),
            "error_kind": self.error_kind,
            "lease_deadline_at": self.lease_deadline_at,
            "submitted_at": self.submitted_at,
            "canonical_pollution": self.canonical_pollution,
            "recovery_required": self.recovery_required,
            "retry_required": self.retry_required,
            "recovery_class": self.recovery_class,
            "simulated_mutation_count": self.simulated_mutation_count,
            "provider_tokens_attributed": self.provider_tokens_attributed,
            "mutation_summary": dict(self.mutation_summary),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, kw_only=True)
class FaultInjectionOutcome:
    record: FaultInjectionRecord
    record_ref: ArtifactRef
    mutated_output_ref: ArtifactRef
    mutated_attempt: PaperAttemptResult
    schema_version: str = "tokenshare.paper_fault_injection_outcome.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "record": self.record.to_dict(),
            "record_ref": self.record_ref.to_dict(),
            "mutated_output_ref": self.mutated_output_ref.to_dict(),
            "mutated_attempt": self.mutated_attempt.to_dict(),
        }


class PaperFaultRuntimeHooks(NoOpRuntimeHooks):
    """把冻结的 Exp3 fault target 接到真实 provider-output gate。"""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        condition_id: str,
        repeat_id: int,
        fault_type: PaperFaultType | str,
        seed: int,
        selected_unit_ids: Iterable[str],
        reserve_unit_ids: Iterable[str] = (),
    ) -> None:
        self._artifact_store = artifact_store
        self._condition_id = _required_non_empty_str(condition_id, "condition_id")
        if not isinstance(repeat_id, int) or isinstance(repeat_id, bool) or repeat_id < 0:
            raise ValueError("repeat_id must be a non-negative integer")
        self._repeat_id = repeat_id
        self._fault_type = PaperFaultType(fault_type)
        self._seed = seed
        self._selected_unit_ids = frozenset(
            _required_non_empty_str(str(unit_id), "selected_unit_ids")
            for unit_id in selected_unit_ids
        )
        self._reserve_unit_ids = tuple(
            dict.fromkeys(
                _required_non_empty_str(str(unit_id), "reserve_unit_ids")
                for unit_id in reserve_unit_ids
                if str(unit_id) not in self._selected_unit_ids
            )
        )
        self._active_target_ids = set(self._selected_unit_ids)
        self._remaining_reserve_unit_ids = list(self._reserve_unit_ids)
        self._injected_unit_ids: set[str] = set()
        self._observed_unit_ids: set[str] = set()
        self._eligible_target_count = 0
        self._not_applicable_target_count = 0
        self._raw_context_by_attempt_id: dict[str, RawOutputContext] = {}
        self._records: list[JsonObject] = []
        self._events: list[RuntimeHookObservationV1] = []

    @property
    def records(self) -> tuple[JsonObject, ...]:
        return tuple(dict(record) for record in self._records)

    @property
    def events(self) -> tuple[RuntimeHookObservationV1, ...]:
        return tuple(self._events)

    @property
    def applicability_counts(self) -> JsonObject:
        return {
            "candidate_target_count": (
                len(self._selected_unit_ids) + len(self._reserve_unit_ids)
            ),
            "selected_target_count": len(self._selected_unit_ids),
            "eligible_target_count": self._eligible_target_count,
            "injected_target_count": len(self._injected_unit_ids),
            "not_applicable_target_count": self._not_applicable_target_count,
            "injection_denominator": self._eligible_target_count,
        }

    def after_raw_output_persisted(
        self,
        context: RawOutputContext,
    ) -> RawOutputDirective | None:
        selected_target_id = context.experiment_unit_id or context.unit_id
        if selected_target_id not in self._selected_unit_ids:
            return None
        if selected_target_id in self._injected_unit_ids:
            return None
        if self._fault_type in {
            PaperFaultType.FALSE_POSITIVE,
            PaperFaultType.FALSE_NEGATIVE,
        }:
            self._raw_context_by_attempt_id[context.attempt_id] = context
            return None
        raw_ref = _required_runtime_ref(
            self._artifact_store, context.raw_output_ref, "persisted raw output"
        )
        provenance_ref = _required_runtime_ref(
            self._artifact_store, context.provenance_ref, "persisted provenance"
        )
        usage_ref = _required_runtime_ref(
            self._artifact_store, context.usage_ref, "persisted usage"
        )
        parsed_ref = None
        late_submitted_at = (
            _timestamp_after(context.lease_deadline_at)
            if self._fault_type == PaperFaultType.LATE_SUBMISSION
            else context.submitted_at
        )
        attempt = PaperAttemptResult(
            condition_id=self._condition_id,
            repeat_id=self._repeat_id,
            run_id=context.run_id,
            task_id=context.task_id,
            unit_id=context.unit_id,
            attempt_id=context.attempt_id,
            worker_id=context.worker_id,
            provider_attempt_index=1,
            attempt_status=PaperAttemptStatus.SUCCEEDED,
            provider=context.provider,
            model=context.model,
            entry_id=context.entry_id,
            request_ref=None,
            raw_output_ref=raw_ref.to_dict(),
            parsed_output_ref=parsed_ref.to_dict() if parsed_ref is not None else None,
            parse_failure_ref=None,
            provenance_ref=provenance_ref.to_dict(),
            usage_ref=usage_ref.to_dict(),
            started_at=context.submitted_at,
            ended_at=context.submitted_at,
            latency_ms=0,
            prompt_tokens=int(context.usage_summary.get("prompt_tokens", 0)),
            completion_tokens=int(context.usage_summary.get("completion_tokens", 0)),
            total_tokens=int(context.usage_summary.get("total_tokens", 0)),
            cost_estimate=float(context.usage_summary.get("cost_estimate", 0.0)),
            error_kind=None,
            fault_injection_ref=None,
            paper_eligible=False,
        )
        outcome = inject_post_ai_fault(
            artifact_store=self._artifact_store,
            attempt=attempt,
            fault_type=self._fault_type,
            seed=self._seed,
            created_at=context.submitted_at,
            lease_deadline_at=context.lease_deadline_at,
            submitted_at=late_submitted_at,
            fault_target={
                "unit_id": context.unit_id,
                "attempt_id": context.attempt_id,
                "artifact_ref": (
                    parsed_ref.to_dict() if parsed_ref is not None else raw_ref.to_dict()
                ),
            },
        )
        record = {
            **outcome.record.to_dict(),
            "fault_injection_id": outcome.record.fault_id,
            "selected_target_ai_unit_id": selected_target_id,
            "original_provenance_ref": provenance_ref.to_dict(),
            "pre_fault_usage_ref": usage_ref.to_dict(),
            "primitive_fault_record_ref": outcome.record_ref.to_dict(),
            "hook_stage": "after_raw_provenance_usage_before_parser",
            "applicability_status": "injected",
        }
        record.pop("canonical_pollution", None)
        runtime_record_ref = self._artifact_store.save_json(
            record,
            artifact_id=f"{outcome.record.fault_id}_runtime_hook_record",
            artifact_type="RuntimeFaultInjectionRecord",
            artifact_schema_id="tokenshare.paper_runtime_fault_injection",
            artifact_schema_version="v1",
            source={"kind": "paper_fault_runtime_hook"},
            metadata={
                "condition_id": self._condition_id,
                "unit_id": context.unit_id,
                "selected_target_ai_unit_id": selected_target_id,
            },
            created_at=context.submitted_at,
        )
        record["record_ref"] = runtime_record_ref.to_dict()
        event = build_experiment_fault_injected_observation(
            condition_id=self._condition_id,
            run_id=context.run_id,
            task_id=context.task_id,
            unit_id=context.unit_id,
            selected_target_ai_unit_id=selected_target_id,
            attempt_id=context.attempt_id,
            fault_type=self._fault_type.value,
            protocol_event_refs=(),
            artifact_refs=(
                raw_ref,
                provenance_ref,
                usage_ref,
                outcome.mutated_output_ref,
                runtime_record_ref,
            ),
            occurred_at=context.submitted_at,
        )
        self._injected_unit_ids.add(selected_target_id)
        self._records.append(record)
        self._events.append(event)
        replacement_text = None
        if self._fault_type in {
            PaperFaultType.FALSE_POSITIVE,
            PaperFaultType.FALSE_NEGATIVE,
        }:
            mutation_body = _read_json_ref(
                self._artifact_store, outcome.mutated_output_ref
            )
            replacement_text = json.dumps(
                mutation_body["mutated_payload"],
                ensure_ascii=False,
                sort_keys=True,
            )
        result_kind = (
            self._fault_type.value
            if self._fault_type
            in {
                PaperFaultType.NO_RETURN,
                PaperFaultType.LATE_SUBMISSION,
                PaperFaultType.EXECUTOR_ERROR,
            }
            else None
        )
        return RawOutputDirective(
            content_text=replacement_text,
            result_kind=result_kind,
            experiment_records=(event,),
        )

    def after_parsed_candidate_persisted(
        self,
        context: ParsedCandidateContext,
    ) -> ParsedCandidateDirective | None:
        if self._fault_type not in {
            PaperFaultType.FALSE_POSITIVE,
            PaperFaultType.FALSE_NEGATIVE,
        }:
            return None
        selected_target_id = context.experiment_unit_id or context.unit_id
        if (
            selected_target_id not in self._active_target_ids
            or selected_target_id in self._observed_unit_ids
        ):
            return None
        parsed_ref = _required_runtime_ref(
            self._artifact_store,
            context.original_parsed_output_ref,
            "persisted parsed candidate",
        )
        candidate_refs = {
            name: _required_runtime_ref(
                self._artifact_store,
                ref,
                f"persisted candidate {name}",
            )
            for name, ref in context.candidate_output_refs.items()
        }
        if not candidate_refs:
            raise ValueError("parsed-candidate hook requires candidate refs")
        parsed_body = _read_json_ref(self._artifact_store, parsed_ref)
        self._observed_unit_ids.add(selected_target_id)
        if (
            self._fault_type == PaperFaultType.FALSE_NEGATIVE
            and not _false_negative_is_applicable(parsed_body)
        ):
            self._not_applicable_target_count += 1
            record = self._record_not_applicable(
                context=context,
                selected_target_id=selected_target_id,
                parsed_ref=parsed_ref,
            )
            self._records.append(record)
            self._active_target_ids.discard(selected_target_id)
            if self._remaining_reserve_unit_ids:
                self._active_target_ids.add(
                    self._remaining_reserve_unit_ids.pop(0)
                )
            return None
        self._eligible_target_count += 1
        raw_context = self._raw_context_by_attempt_id.get(context.attempt_id)
        raw_ref = _required_runtime_ref(
            self._artifact_store,
            (
                raw_context.raw_output_ref
                if raw_context is not None
                else context.raw_output_ref
            ),
            "persisted raw output",
        )
        attempt = _paper_attempt_from_parsed_context(
            condition_id=self._condition_id,
            repeat_id=self._repeat_id,
            context=context,
            raw_context=raw_context,
            raw_ref=raw_ref,
            parsed_ref=parsed_ref,
        )
        outcome = inject_post_ai_fault(
            artifact_store=self._artifact_store,
            attempt=attempt,
            fault_type=self._fault_type,
            seed=self._seed,
            created_at=context.submitted_at,
            fault_target={
                "unit_id": context.unit_id,
                "attempt_id": context.attempt_id,
                "artifact_ref": parsed_ref.to_dict(),
            },
        )
        mutation_body = _read_json_ref(
            self._artifact_store,
            outcome.mutated_output_ref,
        )
        mutated_candidate_ref = self._artifact_store.save_json(
            mutation_body["mutated_payload"],
            artifact_id=(
                f"{outcome.record.fault_id}_parsed_candidate_replacement"
            ),
            artifact_type="FaultMutatedCandidate",
            artifact_schema_id="tokenshare.paper_fault_mutated_candidate",
            artifact_schema_version="v1",
            source={
                "kind": "paper_fault_runtime_hook",
                "primitive_fault_record_ref": outcome.record_ref.to_dict(),
            },
            metadata={
                "condition_id": self._condition_id,
                "unit_id": context.unit_id,
                "selected_target_ai_unit_id": selected_target_id,
            },
            created_at=context.submitted_at,
        )
        # 插件可把 parser 产物按输出契约另存为 content-identical artifact；
        # fault 必须沿实际 candidate ref 注入，不能依赖 ArtifactRef 对象全等。
        matching_output_names = {
            name
            for name, ref in candidate_refs.items()
            if ref.content_hash == parsed_ref.content_hash
        }
        if not matching_output_names:
            raise ValueError(
                "parsed-candidate fault requires a candidate ref matching "
                "the original parsed content"
            )
        replacement_refs = {
            name: (
                mutated_candidate_ref
                if name in matching_output_names
                else ref
            )
            for name, ref in candidate_refs.items()
        }
        record = {
            **outcome.record.to_dict(),
            "fault_injection_id": outcome.record.fault_id,
            "selected_target_ai_unit_id": selected_target_id,
            "original_output_ref": parsed_ref.to_dict(),
            "mutated_output_ref": mutated_candidate_ref.to_dict(),
            "primitive_mutated_output_ref": outcome.mutated_output_ref.to_dict(),
            "primitive_fault_record_ref": outcome.record_ref.to_dict(),
            "hook_stage": (
                "after_parsed_candidate_before_submission_and_verification"
            ),
            "applicability_status": "injected",
        }
        record.pop("canonical_pollution", None)
        if raw_context is not None:
            record["original_provenance_ref"] = (
                raw_context.provenance_ref.to_dict()
            )
            if raw_context.usage_ref is not None:
                record["pre_fault_usage_ref"] = raw_context.usage_ref.to_dict()
        runtime_record_ref = self._artifact_store.save_json(
            record,
            artifact_id=f"{outcome.record.fault_id}_runtime_hook_record",
            artifact_type="RuntimeFaultInjectionRecord",
            artifact_schema_id="tokenshare.paper_runtime_fault_injection",
            artifact_schema_version="v1",
            source={"kind": "paper_fault_runtime_hook"},
            metadata={
                "condition_id": self._condition_id,
                "unit_id": context.unit_id,
                "selected_target_ai_unit_id": selected_target_id,
            },
            created_at=context.submitted_at,
        )
        record["record_ref"] = runtime_record_ref.to_dict()
        event = build_experiment_fault_injected_observation(
            condition_id=self._condition_id,
            run_id=context.run_id,
            task_id=context.task_id,
            unit_id=context.unit_id,
            selected_target_ai_unit_id=selected_target_id,
            attempt_id=context.attempt_id,
            fault_type=self._fault_type.value,
            protocol_event_refs=(),
            artifact_refs=(
                raw_ref,
                parsed_ref,
                outcome.mutated_output_ref,
                mutated_candidate_ref,
                runtime_record_ref,
            ),
            occurred_at=context.submitted_at,
        )
        self._injected_unit_ids.add(selected_target_id)
        self._active_target_ids.discard(selected_target_id)
        self._records.append(record)
        self._events.append(event)
        return ParsedCandidateDirective(
            replacement_candidate_output_refs=replacement_refs,
            experiment_records=(event,),
        )

    def _record_not_applicable(
        self,
        *,
        context: ParsedCandidateContext,
        selected_target_id: str,
        parsed_ref: ArtifactRef,
    ) -> JsonObject:
        record = {
            "schema_version": "tokenshare.paper_fault_applicability.v1",
            "condition_id": self._condition_id,
            "repeat_id": self._repeat_id,
            "run_id": context.run_id,
            "task_id": context.task_id,
            "unit_id": context.unit_id,
            "attempt_id": context.attempt_id,
            "lease_id": context.lease_id,
            "fault_type": self._fault_type.value,
            "selected_target_ai_unit_id": selected_target_id,
            "original_output_ref": parsed_ref.to_dict(),
            "applicability_status": "not_applicable",
            "applicability_reason": "original_output_has_no_factor_or_proof_candidate",
            "hook_stage": (
                "after_parsed_candidate_before_submission_and_verification"
            ),
            "created_at": context.submitted_at,
        }
        record_ref = self._artifact_store.save_json(
            record,
            artifact_id=(
                "fault_applicability_"
                f"{_safe_fault_id(context.attempt_id)}"
            ),
            artifact_type="FaultApplicabilityRecord",
            artifact_schema_id="tokenshare.paper_fault_applicability",
            artifact_schema_version="v1",
            source={"kind": "paper_fault_runtime_hook"},
            metadata={
                "condition_id": self._condition_id,
                "unit_id": context.unit_id,
                "selected_target_ai_unit_id": selected_target_id,
            },
            created_at=context.submitted_at,
        )
        return {**record, "record_ref": record_ref.to_dict()}


def _paper_attempt_from_parsed_context(
    *,
    condition_id: str,
    repeat_id: int,
    context: ParsedCandidateContext,
    raw_context: RawOutputContext | None,
    raw_ref: ArtifactRef,
    parsed_ref: ArtifactRef,
) -> PaperAttemptResult:
    usage = dict(raw_context.usage_summary) if raw_context is not None else {}
    return PaperAttemptResult(
        condition_id=condition_id,
        repeat_id=repeat_id,
        run_id=context.run_id,
        task_id=context.task_id,
        unit_id=context.unit_id,
        attempt_id=context.attempt_id,
        worker_id=context.worker_id,
        provider_attempt_index=1,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider=(
            raw_context.provider if raw_context is not None else "runtime_unknown"
        ),
        model=raw_context.model if raw_context is not None else "runtime_unknown",
        entry_id=(
            raw_context.entry_id if raw_context is not None else "runtime_unknown"
        ),
        request_ref=None,
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict(),
        parse_failure_ref=None,
        provenance_ref=(
            raw_context.provenance_ref.to_dict()
            if raw_context is not None
            else None
        ),
        usage_ref=(
            raw_context.usage_ref.to_dict()
            if raw_context is not None and raw_context.usage_ref is not None
            else None
        ),
        started_at=context.submitted_at,
        ended_at=context.submitted_at,
        latency_ms=0,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        total_tokens=int(usage.get("total_tokens", 0)),
        cost_estimate=float(usage.get("cost_estimate", 0.0)),
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )


def _false_negative_is_applicable(payload: JsonObject) -> bool:
    if _is_factorization_range_result(payload):
        return (
            payload.get("result_kind") == "found_factor"
            and bool(payload.get("found_factor"))
        )
    if _is_lean_proof_candidate(payload):
        return bool(str(payload.get("proof_source") or "").strip())
    return _has_generic_candidate(payload)


def select_fault_targets(
    unit_ids: Iterable[str],
    *,
    fault_rate: float,
    seed: int,
) -> tuple[str, ...]:
    """Select a deterministic set of AI unit ids for a condition."""

    selected = select_fault_target_descriptors(
        (FaultTargetDescriptor(unit_id=str(unit_id)) for unit_id in unit_ids),
        fault_rate=fault_rate,
        seed=seed,
    )
    return tuple(target.unit_id for target in selected)


def select_fault_target_descriptors(
    targets: Iterable[FaultTargetDescriptor | JsonObject | str],
    *,
    fault_rate: float,
    seed: int,
) -> tuple[FaultTargetDescriptor, ...]:
    """Select deterministic fault targets while preserving slot/graph metadata."""

    _validate_fault_rate(fault_rate)
    normalized = tuple(
        sorted(
            (_coerce_fault_target(target) for target in targets),
            key=lambda target: _fault_target_digest(target),
        )
    )
    target_digests = tuple(_fault_target_digest(target) for target in normalized)
    if len(set(target_digests)) != len(target_digests):
        raise ValueError("fault targets must be unique")
    if not normalized or fault_rate == 0:
        return ()
    target_count = min(len(normalized), max(1, ceil(len(normalized) * float(fault_rate))))
    ranked = sorted(
        normalized,
        key=lambda target: digest_json(
            {
                "schema_version": "tokenshare.paper_fault_target_score.v1",
                "seed": seed,
                "target": target.to_dict(),
            }
        ),
    )
    return tuple(ranked[:target_count])


def _validate_fault_rate(fault_rate: float) -> None:
    if not isinstance(fault_rate, (float, int)) or isinstance(fault_rate, bool):
        raise ValueError("fault_rate must be between 0.0 and 1.0")
    if fault_rate < 0.0 or fault_rate > 1.0:
        raise ValueError("fault_rate must be between 0.0 and 1.0")


def inject_post_ai_fault(
    *,
    artifact_store: ArtifactStore,
    attempt: PaperAttemptResult,
    fault_type: PaperFaultType | str,
    seed: int,
    created_at: str,
    lease_deadline_at: str | None = None,
    submitted_at: str | None = None,
    fault_target: FaultTargetDescriptor | JsonObject | None = None,
) -> FaultInjectionOutcome:
    """Persist a post-AI mutation and return a fault-linked attempt snapshot."""

    normalized_fault = PaperFaultType(fault_type)
    raw_ref = _required_persisted_ref(
        artifact_store,
        attempt.raw_output_ref,
        message="fault injection requires persisted raw output",
    )
    original_ref = _original_output_ref_for_fault(
        artifact_store=artifact_store,
        attempt=attempt,
        fault_type=normalized_fault,
        raw_ref=raw_ref,
    )
    original_payload = _read_json_ref(artifact_store, original_ref)
    target_descriptor = _fault_target_for_attempt(
        artifact_store=artifact_store,
        attempt=attempt,
        original_ref=original_ref,
        fault_target=fault_target,
    )
    target_context = target_descriptor.to_dict()
    target_selection_digest = _fault_target_digest(target_descriptor)
    fault_id = _fault_id(
        attempt=attempt,
        fault_type=normalized_fault,
        seed=seed,
        target_selection_digest=target_selection_digest,
    )

    mutation = _build_mutation_body(
        artifact_store=artifact_store,
        attempt=attempt,
        fault_id=fault_id,
        fault_type=normalized_fault,
        seed=seed,
        raw_ref=raw_ref,
        original_ref=original_ref,
        original_payload=original_payload,
        target_context=target_context,
        target_selection_digest=target_selection_digest,
        lease_deadline_at=lease_deadline_at,
        submitted_at=submitted_at,
        created_at=created_at,
    )
    mutated_ref = artifact_store.save_json(
        mutation["artifact_body"],
        artifact_id=f"{fault_id}_mutated_output",
        artifact_type="FaultMutatedOutput",
        artifact_schema_id="tokenshare.paper_fault_mutated_output",
        artifact_schema_version="v1",
        source={
            "kind": "paper_fault_injection",
            "fault_type": normalized_fault.value,
            "attempt_id": attempt.attempt_id,
        },
        metadata={
            "condition_id": attempt.condition_id,
            "run_id": attempt.run_id,
            "unit_id": attempt.unit_id,
            "fault_id": fault_id,
        },
        created_at=created_at,
    )
    record = FaultInjectionRecord(
        fault_id=fault_id,
        condition_id=attempt.condition_id,
        repeat_id=attempt.repeat_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        worker_id=attempt.worker_id,
        fault_type=normalized_fault,
        seed=seed,
        target_context=target_context,
        target_selection_digest=target_selection_digest,
        injection_point=str(mutation["injection_point"]),
        original_raw_output_ref=raw_ref.to_dict(),
        original_output_ref=original_ref.to_dict(),
        mutated_output_ref=mutated_ref.to_dict(),
        suppressed_output_ref=(
            original_ref.to_dict() if mutation["suppressed_output"] else None
        ),
        original_attempt_status=attempt.attempt_status,
        mutated_attempt_status=mutation["mutated_attempt_status"],
        error_kind=mutation["error_kind"],
        lease_deadline_at=lease_deadline_at,
        submitted_at=submitted_at,
        canonical_pollution=False,
        recovery_required=bool(mutation["recovery_required"]),
        retry_required=bool(mutation["retry_required"]),
        recovery_class=str(mutation["recovery_class"]),
        simulated_mutation_count=1,
        provider_tokens_attributed=0,
        mutation_summary=dict(mutation["mutation_summary"]),
        created_at=created_at,
    )
    record_ref = artifact_store.save_json(
        record.to_dict(),
        artifact_id=f"{fault_id}_record",
        artifact_type="FaultInjectionRecord",
        artifact_schema_id="tokenshare.paper_fault_injection",
        artifact_schema_version="v1",
        source={
            "kind": "paper_fault_injection",
            "fault_type": normalized_fault.value,
            "attempt_id": attempt.attempt_id,
        },
        metadata={
            "condition_id": attempt.condition_id,
            "run_id": attempt.run_id,
            "unit_id": attempt.unit_id,
            "fault_id": fault_id,
        },
        created_at=created_at,
    )
    mutated_attempt = replace(
        attempt,
        attempt_status=mutation["mutated_attempt_status"],
        error_kind=mutation["error_kind"],
        fault_injection_ref=record_ref.to_dict(),
    )
    return FaultInjectionOutcome(
        record=record,
        record_ref=record_ref,
        mutated_output_ref=mutated_ref,
        mutated_attempt=mutated_attempt,
    )


def _original_output_ref_for_fault(
    *,
    artifact_store: ArtifactStore,
    attempt: PaperAttemptResult,
    fault_type: PaperFaultType,
    raw_ref: ArtifactRef,
) -> ArtifactRef:
    if fault_type in {PaperFaultType.FALSE_POSITIVE, PaperFaultType.FALSE_NEGATIVE}:
        return _required_persisted_ref(
            artifact_store,
            attempt.parsed_output_ref,
            message=f"{fault_type.value} requires persisted parsed output",
        )
    if fault_type == PaperFaultType.LATE_SUBMISSION and attempt.parsed_output_ref is not None:
        return _required_persisted_ref(
            artifact_store,
            attempt.parsed_output_ref,
            message="late_submission parsed output ref is not persisted",
        )
    return raw_ref


def _build_mutation_body(
    *,
    artifact_store: ArtifactStore,
    attempt: PaperAttemptResult,
    fault_id: str,
    fault_type: PaperFaultType,
    seed: int,
    raw_ref: ArtifactRef,
    original_ref: ArtifactRef,
    original_payload: JsonObject,
    target_context: JsonObject,
    target_selection_digest: str,
    lease_deadline_at: str | None,
    submitted_at: str | None,
    created_at: str,
) -> JsonObject:
    del artifact_store
    if fault_type == PaperFaultType.FALSE_POSITIVE:
        mutated_payload = _false_positive_payload(original_payload)
        return _mutation_result(
            fault_id=fault_id,
            fault_type=fault_type,
            seed=seed,
            raw_ref=raw_ref,
            original_ref=original_ref,
            original_payload=original_payload,
            target_context=target_context,
            target_selection_digest=target_selection_digest,
            mutated_payload=mutated_payload,
            injection_point="after_parsed_candidate_before_verification",
            mutated_attempt_status=PaperAttemptStatus.SUCCEEDED,
            error_kind=None,
            recovery_required=False,
            retry_required=False,
            recovery_class="detect_and_isolate",
            suppressed_output=False,
            mutation_summary={
                "mutation_kind": "false_positive_invalid_claim",
                "expected_detection": "verifier_or_checker_reject",
            },
            created_at=created_at,
        )
    if fault_type == PaperFaultType.FALSE_NEGATIVE:
        mutated_payload = _false_negative_payload(original_payload)
        return _mutation_result(
            fault_id=fault_id,
            fault_type=fault_type,
            seed=seed,
            raw_ref=raw_ref,
            original_ref=original_ref,
            original_payload=original_payload,
            target_context=target_context,
            target_selection_digest=target_selection_digest,
            mutated_payload=mutated_payload,
            injection_point="after_parsed_candidate_before_verification",
            mutated_attempt_status=PaperAttemptStatus.SUCCEEDED,
            error_kind=None,
            recovery_required=False,
            retry_required=False,
            recovery_class="domain_dependent",
            suppressed_output=True,
            mutation_summary={
                "mutation_kind": "false_negative_suppressed_candidate",
                "suppressed_candidate": True,
            },
            created_at=created_at,
        )
    if fault_type == PaperFaultType.NO_RETURN:
        _require_lease_deadline(fault_type, lease_deadline_at)
        body = _base_mutation_body(
            fault_id=fault_id,
            fault_type=fault_type,
            seed=seed,
            raw_ref=raw_ref,
            original_ref=original_ref,
            original_payload=original_payload,
            target_context=target_context,
            target_selection_digest=target_selection_digest,
            created_at=created_at,
        )
        body.update(
            {
                "submission_action": "dropped_after_raw_output",
                "lease_deadline_at": lease_deadline_at,
                "mutated_payload": None,
                "mutation_summary": {
                    "mutation_kind": "no_return_drop_submission",
                    "expected_protocol_effect": "lease_expiry_then_requeue",
                },
            }
        )
        return {
            "artifact_body": body,
            "injection_point": "after_raw_output_before_submission",
            "mutated_attempt_status": PaperAttemptStatus.LEASE_EXPIRED,
            "error_kind": None,
            "recovery_required": True,
            "retry_required": True,
            "recovery_class": "recoverable",
            "suppressed_output": False,
            "mutation_summary": body["mutation_summary"],
        }
    if fault_type == PaperFaultType.LATE_SUBMISSION:
        _require_lease_deadline(fault_type, lease_deadline_at)
        if submitted_at is None:
            raise ValueError("late_submission requires submitted_at")
        if _parse_timestamp(submitted_at) <= _parse_timestamp(
            str(lease_deadline_at)
        ):
            raise ValueError("late_submission submitted_at must be after lease_deadline_at")
        body = _base_mutation_body(
            fault_id=fault_id,
            fault_type=fault_type,
            seed=seed,
            raw_ref=raw_ref,
            original_ref=original_ref,
            original_payload=original_payload,
            target_context=target_context,
            target_selection_digest=target_selection_digest,
            created_at=created_at,
        )
        body.update(
            {
                "submitted_at": submitted_at,
                "lease_deadline_at": lease_deadline_at,
                "accepted_by_protocol": False,
                "canonical_pollution": False,
                "mutated_payload": original_payload,
                "mutation_summary": {
                    "mutation_kind": "late_submission_after_deadline",
                    "expected_protocol_effect": "late_result_rejected",
                },
            }
        )
        return {
            "artifact_body": body,
            "injection_point": "after_raw_output_late_submission",
            "mutated_attempt_status": PaperAttemptStatus.LATE_REJECTED,
            "error_kind": "late_submission",
            "recovery_required": False,
            "retry_required": False,
            "recovery_class": "detect_and_isolate",
            "suppressed_output": False,
            "mutation_summary": body["mutation_summary"],
        }
    if fault_type == PaperFaultType.EXECUTOR_ERROR:
        body = _base_mutation_body(
            fault_id=fault_id,
            fault_type=fault_type,
            seed=seed,
            raw_ref=raw_ref,
            original_ref=original_ref,
            original_payload=original_payload,
            target_context=target_context,
            target_selection_digest=target_selection_digest,
            created_at=created_at,
        )
        body.update(
            {
                "controlled_error": {
                    "kind": "executor_error",
                    "stage": "parser_bridge",
                    "message": "controlled paper fault after provider response",
                    "retry_requires_new_provider_attempt": True,
                },
                "mutated_payload": None,
                "mutation_summary": {
                    "mutation_kind": "controlled_executor_error",
                    "expected_protocol_effect": "retry_with_new_provider_attempt",
                },
            }
        )
        return {
            "artifact_body": body,
            "injection_point": "after_raw_output_before_parser_bridge",
            "mutated_attempt_status": PaperAttemptStatus.PROVIDER_ERROR,
            "error_kind": "executor_error",
            "recovery_required": True,
            "retry_required": True,
            "recovery_class": "recoverable",
            "suppressed_output": False,
            "mutation_summary": body["mutation_summary"],
        }
    raise ValueError(f"unsupported paper fault type: {fault_type.value}")


def _mutation_result(
    *,
    fault_id: str,
    fault_type: PaperFaultType,
    seed: int,
    raw_ref: ArtifactRef,
    original_ref: ArtifactRef,
    original_payload: JsonObject,
    target_context: JsonObject,
    target_selection_digest: str,
    mutated_payload: JsonObject,
    injection_point: str,
    mutated_attempt_status: PaperAttemptStatus,
    error_kind: str | None,
    recovery_required: bool,
    retry_required: bool,
    recovery_class: str,
    suppressed_output: bool,
    mutation_summary: JsonObject,
    created_at: str,
) -> JsonObject:
    body = _base_mutation_body(
        fault_id=fault_id,
        fault_type=fault_type,
        seed=seed,
        raw_ref=raw_ref,
        original_ref=original_ref,
        original_payload=original_payload,
        target_context=target_context,
        target_selection_digest=target_selection_digest,
        created_at=created_at,
    )
    body.update(
        {
            "mutated_payload": mutated_payload,
            "mutation_summary": mutation_summary,
        }
    )
    return {
        "artifact_body": body,
        "injection_point": injection_point,
        "mutated_attempt_status": mutated_attempt_status,
        "error_kind": error_kind,
        "recovery_required": recovery_required,
        "retry_required": retry_required,
        "recovery_class": recovery_class,
        "suppressed_output": suppressed_output,
        "mutation_summary": mutation_summary,
    }


def _base_mutation_body(
    *,
    fault_id: str,
    fault_type: PaperFaultType,
    seed: int,
    raw_ref: ArtifactRef,
    original_ref: ArtifactRef,
    original_payload: JsonObject,
    target_context: JsonObject,
    target_selection_digest: str,
    created_at: str,
) -> JsonObject:
    return {
        "schema_version": MUTATED_OUTPUT_SCHEMA_VERSION,
        "fault_id": fault_id,
        "fault_type": fault_type.value,
        "seed": seed,
        "original_raw_output_ref": raw_ref.to_dict(),
        "original_output_ref": original_ref.to_dict(),
        "original_payload": original_payload,
        "target_context": dict(target_context),
        "target_selection_digest": target_selection_digest,
        "simulated_mutation_count": 1,
        "provider_tokens_attributed": 0,
        "created_at": created_at,
    }


def _false_positive_payload(original_payload: JsonObject) -> JsonObject:
    payload = json.loads(json.dumps(original_payload, ensure_ascii=False, sort_keys=True))
    if _is_factorization_range_result(payload):
        payload["result_kind"] = "found_factor"
        payload["found_factor"] = "1"
        payload["cofactor"] = str(payload.get("target_n") or "1")
        payload["checked_divisor_count"] = 0
    elif _is_lean_proof_candidate(payload):
        payload["proof_source"] = "by\n  exact False.elim (by contradiction)"
        payload["proof_candidate_id"] = f"{payload.get('proof_candidate_id', 'proof')}:false_positive"
    else:
        payload["fault_forced_claim"] = True
    payload["paper_fault_mutation"] = {
        "fault_type": PaperFaultType.FALSE_POSITIVE.value,
        "validity": "intentionally_invalid",
    }
    return payload


def _false_negative_payload(original_payload: JsonObject) -> JsonObject:
    payload = json.loads(json.dumps(original_payload, ensure_ascii=False, sort_keys=True))
    if _is_factorization_range_result(payload):
        if payload.get("result_kind") != "found_factor" or not payload.get("found_factor"):
            raise ValueError("false_negative requires an existing found candidate or proof")
        payload["result_kind"] = "no_factor"
        payload["found_factor"] = None
        payload["cofactor"] = None
    elif _is_lean_proof_candidate(payload):
        if not str(payload.get("proof_source") or "").strip():
            raise ValueError("false_negative requires an existing found candidate or proof")
        payload["proof_source"] = _LEAN_FALSE_NEGATIVE_SUPPRESSED_PROOF_SOURCE
        payload["proof_suppressed"] = True
    else:
        if not _has_generic_candidate(payload):
            raise ValueError("false_negative requires an existing found candidate or proof")
        payload["candidate_suppressed"] = True
    payload["paper_fault_mutation"] = {
        "fault_type": PaperFaultType.FALSE_NEGATIVE.value,
        "validity": "candidate_suppressed",
    }
    return payload


def _is_factorization_range_result(payload: JsonObject) -> bool:
    return (
        payload.get("schema_version") == "factorization.range_result.v1"
        or {"target_n", "range_start", "range_end"}.issubset(payload)
    )


def _is_lean_proof_candidate(payload: JsonObject) -> bool:
    return "proof_source" in payload or payload.get("schema_version") == "lean_proof.proof_candidate.v1"


def _has_generic_candidate(payload: JsonObject) -> bool:
    if payload.get("found_candidate") is True or payload.get("candidate_found") is True:
        return True
    for field_name in ("candidate", "submission", "output", "answer", "result"):
        value = payload.get(field_name)
        if value not in (None, "", [], {}):
            return True
    return False


def _required_persisted_ref(
    artifact_store: ArtifactStore,
    value: JsonObject | None,
    *,
    message: str,
) -> ArtifactRef:
    if value is None:
        raise ValueError(message)
    ref = ArtifactRef.from_dict(value)
    if not artifact_store.verify(ref):
        raise ValueError(message)
    return ref


def _read_json_ref(artifact_store: ArtifactStore, ref: ArtifactRef) -> JsonObject:
    return json.loads(artifact_store.read_bytes(ref).decode("utf-8"))


def _require_lease_deadline(
    fault_type: PaperFaultType,
    lease_deadline_at: str | None,
) -> None:
    if lease_deadline_at is None:
        raise ValueError(f"{fault_type.value} requires lease_deadline_at")


def _fault_id(
    *,
    attempt: PaperAttemptResult,
    fault_type: PaperFaultType,
    seed: int,
    target_selection_digest: str,
) -> str:
    digest = digest_json(
        {
            "schema_version": "tokenshare.paper_fault_id.v1",
            "condition_id": attempt.condition_id,
            "repeat_id": attempt.repeat_id,
            "run_id": attempt.run_id,
            "task_id": attempt.task_id,
            "unit_id": attempt.unit_id,
            "attempt_id": attempt.attempt_id,
            "fault_type": fault_type.value,
            "seed": seed,
            "target_selection_digest": target_selection_digest,
        }
    )
    return f"paper_fault_{digest.removeprefix('sha256:')[:16]}"


def _fault_target_for_attempt(
    *,
    artifact_store: ArtifactStore,
    attempt: PaperAttemptResult,
    original_ref: ArtifactRef,
    fault_target: FaultTargetDescriptor | JsonObject | None,
) -> FaultTargetDescriptor:
    descriptor = (
        _coerce_fault_target(fault_target)
        if fault_target is not None
        else FaultTargetDescriptor(
            unit_id=attempt.unit_id,
            attempt_id=attempt.attempt_id,
            artifact_ref=original_ref.to_dict(),
        )
    )
    if descriptor.unit_id != attempt.unit_id:
        raise ValueError("fault target unit_id must match attempt unit_id")
    if descriptor.attempt_id is not None and descriptor.attempt_id != attempt.attempt_id:
        raise ValueError("fault target attempt_id must match attempt attempt_id")
    if descriptor.artifact_ref is not None:
        _required_persisted_ref(
            artifact_store,
            descriptor.artifact_ref,
            message="fault target artifact_ref is not persisted",
        )
    return replace(
        descriptor,
        attempt_id=descriptor.attempt_id or attempt.attempt_id,
        artifact_ref=descriptor.artifact_ref or original_ref.to_dict(),
    )


def _coerce_fault_target(
    value: FaultTargetDescriptor | JsonObject | str,
) -> FaultTargetDescriptor:
    if isinstance(value, FaultTargetDescriptor):
        return value
    if isinstance(value, str):
        return FaultTargetDescriptor(unit_id=value)
    if not isinstance(value, dict):
        raise ValueError("fault target must be a unit id or descriptor object")
    dependency_path = value.get("dependency_path") or ()
    if isinstance(dependency_path, str):
        dependency_path = (dependency_path,)
    if not isinstance(dependency_path, (list, tuple)):
        raise ValueError("target.dependency_path must be a list")
    return FaultTargetDescriptor(
        unit_id=_required_str_from_mapping(value, "unit_id"),
        target_kind=str(value.get("target_kind") or "ai_unit"),
        attempt_id=_optional_str_from_mapping(value, "attempt_id"),
        artifact_ref=_optional_object_from_mapping(value, "artifact_ref"),
        slot_key=_optional_str_from_mapping(value, "slot_key"),
        child_logical_key=_optional_str_from_mapping(value, "child_logical_key"),
        dependency_path=tuple(str(part) for part in dependency_path),
        lemma_node_id=_optional_str_from_mapping(value, "lemma_node_id"),
        parent_node_id=_optional_str_from_mapping(value, "parent_node_id"),
        merge_slot=_optional_str_from_mapping(value, "merge_slot"),
        domain_target=_optional_object_from_mapping(value, "domain_target"),
    )


def _fault_target_digest(target: FaultTargetDescriptor) -> str:
    return digest_json(target.to_dict())


def _required_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _required_str_from_mapping(value: JsonObject, field_name: str) -> str:
    return _required_non_empty_str(value.get(field_name), f"target.{field_name}")


def _optional_str_from_mapping(value: JsonObject, field_name: str) -> str | None:
    if value.get(field_name) is None:
        return None
    return _required_non_empty_str(value.get(field_name), f"target.{field_name}")


def _optional_object_from_mapping(value: JsonObject, field_name: str) -> JsonObject | None:
    item = value.get(field_name)
    if item is None:
        return None
    if not isinstance(item, dict):
        raise ValueError(f"target.{field_name} must be an object")
    return _deepcopy_json(item)


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _required_runtime_ref(
    artifact_store: ArtifactStore,
    value: ArtifactRef | None,
    label: str,
) -> ArtifactRef:
    if not isinstance(value, ArtifactRef) or not artifact_store.verify(value):
        raise ValueError(f"fault injection requires {label}")
    return value


def _timestamp_after(value: str | None) -> str:
    if value is None:
        raise ValueError("late_submission requires lease_deadline_at")
    parsed = _parse_timestamp(value)
    return (parsed + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_fault_id(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)
