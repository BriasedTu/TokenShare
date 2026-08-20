"""从系统 runtime 事实派生论文 task/attempt 结果。

本模块只读取 ``ProtocolRunResult``、协议事件和已持久化 artifact。它不推进
协议状态，也不接受 paper runner 提供的成功/失败结论作为权威输入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from tokenshare.core.models import ArtifactRef, JsonObject
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskResult,
    PaperTaskStatus,
    digest_json,
)
from tokenshare.local_runtime import ProtocolRunResult
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True, kw_only=True)
class PaperProtocolProjection:
    task_result: PaperTaskResult
    attempt_results: tuple[PaperAttemptResult, ...]
    lifecycle_coverage: JsonObject
    runtime_generation_identity: JsonObject
    runtime_observation: JsonObject
    ineligibility_reasons: tuple[str, ...]
    schema_version: str = "tokenshare.paper_protocol_projection.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "task_result": self.task_result.to_dict(),
            "attempt_results": [item.to_dict() for item in self.attempt_results],
            "lifecycle_coverage": dict(self.lifecycle_coverage),
            "runtime_generation_identity": dict(self.runtime_generation_identity),
            "runtime_observation": dict(self.runtime_observation),
            "ineligibility_reasons": list(self.ineligibility_reasons),
        }


@dataclass(frozen=True, kw_only=True)
class CapturedModelExecutionRecordBinding:
    """执行器 capture 与协议 attempt 的严格 model-record 绑定。"""

    request_attempt_id: str
    record_attempt_id: str | None
    record_ref: ArtifactRef | None
    record_required: bool


def reconcile_captured_model_execution_records(
    projection: PaperProtocolProjection,
    bindings: Sequence[CapturedModelExecutionRecordBinding],
) -> PaperProtocolProjection:
    """用执行后 capture 权威回填 projection，不从 usage 或 artifact 顺序猜测。"""

    projected_by_id: dict[str, PaperAttemptResult] = {}
    for attempt in projection.attempt_results:
        attempt_id = attempt.attempt_id
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("projected attempt_id must be non-empty")
        if attempt_id in projected_by_id:
            raise ValueError(
                f"duplicate projected attempt_id: {attempt_id}"
            )
        projected_by_id[attempt_id] = attempt

    captured_by_id: dict[str, CapturedModelExecutionRecordBinding] = {}
    for binding in bindings:
        request_attempt_id = binding.request_attempt_id
        if (
            not isinstance(request_attempt_id, str)
            or not request_attempt_id.strip()
        ):
            raise ValueError("captured request attempt_id must be non-empty")
        if request_attempt_id in captured_by_id:
            raise ValueError(
                "duplicate captured request attempt_id: "
                f"{request_attempt_id}"
            )
        if type(binding.record_required) is not bool:
            raise ValueError("captured model execution record requirement is invalid")
        if (binding.record_attempt_id is None) != (binding.record_ref is None):
            raise ValueError(
                "captured model execution record identity and ref must be "
                f"observed together: {request_attempt_id}"
            )
        if binding.record_attempt_id is not None:
            if (
                not isinstance(binding.record_attempt_id, str)
                or not binding.record_attempt_id.strip()
            ):
                raise ValueError("captured record attempt_id must be non-empty")
            if binding.record_attempt_id != request_attempt_id:
                raise ValueError(
                    "captured record attempt_id does not match request attempt_id: "
                    f"{binding.record_attempt_id} != {request_attempt_id}"
                )
            if not isinstance(binding.record_ref, ArtifactRef):
                raise ValueError("captured model execution record ref is invalid")
        captured_by_id[request_attempt_id] = binding

    unknown_ids = sorted(set(captured_by_id) - set(projected_by_id))
    if unknown_ids:
        raise ValueError(
            "captured model execution record references unknown projected "
            f"attempts: {unknown_ids}"
        )

    reconciled: list[PaperAttemptResult] = []
    captured_ref_values: list[JsonObject] = []
    for attempt in projection.attempt_results:
        binding = captured_by_id.get(attempt.attempt_id)
        captured_ref = binding.record_ref if binding is not None else None
        if binding is None and attempt.provider_attempt_count > 0:
            raise ValueError(
                "actual provider attempt is missing captured model execution "
                "record: "
                f"{attempt.attempt_id}"
            )
        if (
            binding is not None
            and binding.record_required
            and captured_ref is None
        ):
            raise ValueError(
                "required captured model execution record is missing: "
                f"{attempt.attempt_id}"
            )
        if captured_ref is None:
            reconciled.append(attempt)
            continue
        captured_value = captured_ref.to_dict()
        if (
            attempt.model_execution_record_ref is not None
            and attempt.model_execution_record_ref != captured_value
        ):
            raise ValueError(
                "conflicting projected model execution record for attempt: "
                f"{attempt.attempt_id}"
            )
        reconciled.append(
            replace(attempt, model_execution_record_ref=captured_value)
        )
        captured_ref_values.append(captured_value)

    artifact_refs: list[JsonObject] = []
    artifact_keys: set[tuple[str, str]] = set()
    for value in (
        *projection.task_result.artifact_refs,
        *captured_ref_values,
    ):
        if not isinstance(value, Mapping):
            raise ValueError("paper task artifact ref must be an object")
        artifact_id = value.get("artifact_id")
        content_hash = value.get("content_hash")
        if not isinstance(artifact_id, str) or not isinstance(content_hash, str):
            raise ValueError("paper task artifact ref identity is incomplete")
        key = (artifact_id, content_hash)
        if key in artifact_keys:
            continue
        artifact_keys.add(key)
        artifact_refs.append(dict(value))

    return replace(
        projection,
        attempt_results=tuple(reconciled),
        task_result=replace(
            projection.task_result,
            artifact_refs=artifact_refs,
        ),
    )


def project_paper_protocol_run(
    *,
    condition: PaperExperimentCondition,
    case: Mapping[str, Any],
    runtime_result: ProtocolRunResult,
    protocol_events: Sequence[Any],
    artifact_store: ArtifactStore,
    accepted_validity: bool | None,
    attempt_metadata_by_unit: Mapping[str, Mapping[str, Any]] | None = None,
) -> PaperProtocolProjection:
    """从权威协议日志和 artifact 派生兼容的 paper result。"""

    if runtime_result.task_id is None or runtime_result.root_unit_id is None:
        raise ValueError("runtime result must identify task and root unit")
    events = tuple(_event_body(item) for item in protocol_events)
    if not events:
        raise ValueError("paper protocol projection requires protocol events")
    if any(event.get("task_id") != runtime_result.task_id for event in events):
        raise ValueError("paper projection received foreign-task protocol event")
    _validate_runtime_event_refs(runtime_result, events)

    inventory = _artifact_inventory(artifact_store)
    event_types = tuple(str(event["event_type"]) for event in events)
    requests = _request_records(events, artifact_store)
    submissions = _submission_records(events, artifact_store)
    attempt_snapshots = _attempt_snapshots(events)
    verification_by_attempt = _events_by_attempt(events, "VERIFICATION_RECORDED")
    canonical_attempt_ids = {
        str(event["payload"].get("selected_attempt_id"))
        for event in events
        if event["event_type"] == "CANONICAL_OUTPUTS_BOUND"
        and event["payload"].get("selected_attempt_id")
    }
    recovery_by_attempt = _recovery_events_by_attempt(events)
    metadata_by_unit = attempt_metadata_by_unit or {}
    runtime_observation = _runtime_observation(runtime_result)
    worker_fact_by_attempt = {
        str(fact["attempt_id"]): fact
        for fact in runtime_observation.get("worker_execution_facts", ())
        if isinstance(fact, Mapping)
        and isinstance(fact.get("attempt_id"), str)
        and fact["attempt_id"]
    }

    attempts: list[PaperAttemptResult] = []
    next_provider_attempt_index_by_unit: dict[str, int] = {}
    for request_event, request, request_ref in requests:
        executor = request.get("executor")
        executor_id = str(executor.get("executor_id") or "") if isinstance(executor, dict) else ""
        if "ai" not in executor_id.lower():
            continue
        attempt_id = str(request.get("attempt_id") or request_event["payload"].get("attempt_id") or "")
        unit_id = str(request.get("unit_id") or request_event["payload"].get("unit_id") or "")
        if not attempt_id or not unit_id:
            raise ValueError("AI request event is missing attempt/unit identity")
        submission_event, submission, _submission_ref = submissions.get(
            attempt_id,
            ({}, {}, None),
        )
        usage_ref, usage = _artifact_for_execution(
            inventory,
            artifact_store,
            artifact_types={
                "AIUsageSummary",
                "UsageSummary",
                "AIUsage",
                "TraceCurrentUsage",
            },
            attempt_id=attempt_id,
            request_id=str(request.get("request_id") or ""),
            submission_id=str(submission.get("submission_id") or ""),
        )
        model_record_ref, model_record = _artifact_for_execution(
            inventory,
            artifact_store,
            artifact_types={"PaperModelExecutionRecord"},
            attempt_id=attempt_id,
            request_id=str(request.get("request_id") or ""),
        )
        fault_injection_ref, fault_record = _artifact_for_execution(
            inventory,
            artifact_store,
            artifact_types={"FaultInjectionRecord"},
            attempt_id=attempt_id,
        )
        runtime_fault_ref, runtime_fault_record = _artifact_for_execution(
            inventory,
            artifact_store,
            artifact_types={"RuntimeFaultInjectionRecord"},
            attempt_id=attempt_id,
        )
        provenance_ref = _mapping_or_none(submission.get("provenance_ref"))
        raw_output_ref = _mapping_or_none(submission.get("raw_output_ref"))
        auditable_no_return = _auditable_no_return_evidence(
            condition=condition,
            submission_event=submission_event,
            submission=submission,
            request=request,
            request_ref=request_ref,
            model_record=model_record,
            fault_record=fault_record,
            fault_injection_ref=fault_injection_ref,
            runtime_fault_record=runtime_fault_record,
            runtime_fault_ref=runtime_fault_ref,
            store=artifact_store,
        )
        if auditable_no_return:
            raw_output_ref = auditable_no_return["raw_output_ref"]
            provenance_ref = auditable_no_return["provenance_ref"]
            usage_ref = auditable_no_return["usage_ref"]
            usage = _read_ref_body(artifact_store, usage_ref)
        provenance = _read_ref_body(artifact_store, provenance_ref)
        provider_attempts = provenance.get("attempts", ()) if isinstance(provenance, dict) else ()
        if not isinstance(provider_attempts, list):
            provider_attempts = []
        last_provider_attempt = (
            provider_attempts[-1]
            if provider_attempts and isinstance(provider_attempts[-1], dict)
            else {}
        )
        usage = usage if isinstance(usage, dict) else {}
        snapshot = attempt_snapshots.get(attempt_id, {})
        verification = verification_by_attempt.get(attempt_id, {})
        recovery = recovery_by_attempt.get(attempt_id, {})
        worker_fact = worker_fact_by_attempt.get(attempt_id, {})
        status, error_kind = _attempt_status(
            condition=condition,
            submission_event=submission_event,
            submission=submission,
            snapshot=snapshot,
            verification=verification,
            canonical=attempt_id in canonical_attempt_ids,
            recovery=recovery,
            model_record=model_record,
            auditable_no_return=bool(auditable_no_return),
        )
        provider_attempt_count = _submission_provider_attempt_count(
            submission=submission,
            usage=usage,
            provider_attempts=provider_attempts,
        )
        pre_provider_executor_error = status == PaperAttemptStatus.EXECUTOR_ERROR
        if pre_provider_executor_error:
            usage_ref = None
            usage = {}
            model_record_ref = None
            model_record = {}
        unit_metadata = dict(metadata_by_unit.get(unit_id, {}))
        task_unit_snapshot = request.get("task_unit_snapshot")
        if isinstance(task_unit_snapshot, dict) and isinstance(
            task_unit_snapshot.get("metadata"),
            dict,
        ):
            unit_metadata = {**task_unit_snapshot["metadata"], **unit_metadata}
        prompt_tokens = _non_negative_int(usage.get("prompt_tokens"))
        completion_tokens = _non_negative_int(usage.get("completion_tokens"))
        total_tokens = _non_negative_int(usage.get("total_tokens"))
        cost_estimate = _non_negative_number(usage.get("cost_estimate"))
        latency_ms = _non_negative_int(last_provider_attempt.get("latency_ms"))
        if pre_provider_executor_error:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            cost_estimate = 0.0
            latency_ms = 0
        provider_usage_missing = (
            not pre_provider_executor_error
            and any(
                value is None
                for value in (
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost_estimate,
                )
            )
        )
        nullable_provider_observation = (
            not pre_provider_executor_error
            and any(
                value is None
                for value in (
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost_estimate,
                )
            )
        )
        provider_attempt_index = next_provider_attempt_index_by_unit.get(unit_id, 0)
        next_provider_attempt_index_by_unit[unit_id] = provider_attempt_index + 1
        attempt_evidence_complete = all(
            (
                request_ref is not None,
                provenance_ref is not None,
                usage_ref is not None,
                model_record_ref is not None,
                bool(provider_attempts),
                (
                    _provider_failure_identity_is_auditable(
                        model_record=model_record,
                        provider_attempts=provider_attempts,
                    )
                    if status == PaperAttemptStatus.PROVIDER_ERROR
                    and raw_output_ref is None
                    else raw_output_ref is not None
                    and model_record.get("paper_eligible") is True
                ),
            )
        )
        attempts.append(
            PaperAttemptResult(
                condition_id=condition.condition_id,
                repeat_id=condition.repeat_id,
                run_id=runtime_result.run_id,
                task_id=runtime_result.task_id,
                unit_id=unit_id,
                attempt_id=attempt_id,
                worker_id=str(
                    worker_fact.get("worker_id")
                    or snapshot.get("client_id")
                    or "unknown"
                ),
                provider_attempt_index=provider_attempt_index,
                attempt_status=status,
                provider=(
                    None
                    if pre_provider_executor_error
                    else str(
                        usage.get("provider_family")
                        or last_provider_attempt.get("provider_family")
                        or condition.provider_family
                        or "unknown"
                    )
                ),
                model=(
                    None
                    if pre_provider_executor_error
                    else str(
                        usage.get("model")
                        or usage.get("configured_model")
                        or last_provider_attempt.get("configured_model")
                        or condition.provider_model_id
                        or "unknown"
                    )
                ),
                entry_id=(
                    None
                    if pre_provider_executor_error
                    else str(
                        usage.get("entry_id")
                        or last_provider_attempt.get("entry_id")
                        or condition.model_entry_id
                        or "unknown"
                    )
                ),
                request_ref=request_ref,
                raw_output_ref=raw_output_ref,
                parsed_output_ref=_mapping_or_none(submission.get("parsed_output_ref")),
                parse_failure_ref=_mapping_or_none(submission.get("parse_failure_ref")),
                provenance_ref=provenance_ref,
                usage_ref=usage_ref,
                model_execution_record_ref=model_record_ref,
                provider_attempt_count=provider_attempt_count,
                started_at=str(
                    worker_fact.get("started_at")
                    or request.get("created_at")
                    or events[0]["occurred_at"]
                ),
                ended_at=str(
                    worker_fact.get("ended_at")
                    or submission.get("submitted_at")
                    or snapshot.get("finished_at")
                    or events[-1]["occurred_at"]
                ),
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_estimate=cost_estimate,
                error_kind=error_kind,
                fault_injection_ref=fault_injection_ref,
                paper_eligible=attempt_evidence_complete,
                paper_difficulty=condition.paper_difficulty,
                topic_family=condition.topic_family,
                topic_family_version=condition.topic_family_version,
                construction_rule_id=condition.construction_rule_id,
                oracle_package_group=condition.oracle_package_group,
                proof_assembly_shape=condition.proof_assembly_shape,
                lemma_node_id=_string_or_none(unit_metadata.get("lemma_node_id")),
                slot_key=_string_or_none(unit_metadata.get("slot_key")),
                dependency_path=tuple(unit_metadata.get("dependency_path") or ()),
                planned_ai_unit_id=_string_or_none(
                    unit_metadata.get("planned_ai_unit_id")
                ),
                executor_id=(
                    str(executor_id) if pre_provider_executor_error else None
                ),
                executor_type=(
                    "ai_api_pre_provider" if pre_provider_executor_error else None
                ),
                cost_estimate_currency=_string_or_none(usage.get("currency")),
                cost_estimate_status=(
                    (
                        "usage_missing"
                        if provider_usage_missing
                        and _string_or_none(usage.get("cost_estimate_status"))
                        not in {"usage_missing", "usage_invalid"}
                        else _string_or_none(usage.get("cost_estimate_status"))
                    )
                    if nullable_provider_observation
                    else _string_or_none(usage.get("cost_estimate_status"))
                    if not pre_provider_executor_error
                    else None
                ),
                schema_version=(
                    "tokenshare.paper_attempt_result.v2"
                    if pre_provider_executor_error
                    else "tokenshare.paper_attempt_result.v3"
                    if nullable_provider_observation
                    else "tokenshare.paper_attempt_result.v1"
                ),
            )
        )

    root_status = _paper_task_status(runtime_result.status)
    lifecycle_coverage = _lifecycle_coverage(
        root_status=root_status,
        root_unit_id=runtime_result.root_unit_id,
        event_types=event_types,
        events=events,
    )
    reasons: list[str] = []
    if root_status == PaperTaskStatus.COMPLETED and not lifecycle_coverage["complete"]:
        reasons.append("incomplete_protocol_lifecycle")
    if attempts and not all(attempt.paper_eligible for attempt in attempts):
        reasons.append("incomplete_attempt_evidence")
    if root_status == PaperTaskStatus.COMPLETED and not attempts:
        reasons.append("missing_provider_attempt")
    if root_status == PaperTaskStatus.PARTIAL:
        reasons.append("partial_pilot_observation")
    paper_eligible = not reasons and bool(attempts)
    failure_stage, failure_kind = _task_failure(attempts, events, root_status)
    all_artifact_refs = _stable_artifact_refs(
        runtime_result.artifact_refs,
        attempts,
    )
    task_total_tokens = _complete_attempt_sum(attempts, "total_tokens")
    task_cost_estimate = _complete_attempt_sum(attempts, "cost_estimate")
    task_usage_missing = (
        task_total_tokens is None or task_cost_estimate is None
    )
    task_result = PaperTaskResult(
        condition_id=condition.condition_id,
        repeat_id=condition.repeat_id,
        task_id=runtime_result.task_id,
        domain=condition.domain,
        difficulty=str(case.get("difficulty") or condition.difficulty),
        paper_difficulty=condition.paper_difficulty,
        topic_family=condition.topic_family,
        topic_family_version=condition.topic_family_version,
        construction_rule_id=condition.construction_rule_id,
        oracle_package_group=condition.oracle_package_group,
        proof_assembly_shape=condition.proof_assembly_shape,
        root_status=root_status,
        accepted_validity=accepted_validity,
        failure_stage=failure_stage,
        failure_kind=failure_kind,
        attempt_count=len(attempts),
        provider_attempt_count=sum(
            attempt.provider_attempt_count for attempt in attempts
        ),
        wall_clock_ms=_runtime_wall_clock_ms(runtime_observation, events),
        total_tokens=task_total_tokens,
        cost_estimate=task_cost_estimate,
        event_refs=[_event_ref(event) for event in events],
        artifact_refs=all_artifact_refs,
        paper_eligible=paper_eligible,
        cost_estimate_status=(
            "usage_missing"
            if task_usage_missing
            else None
        ),
        schema_version=(
            "tokenshare.paper_task_result.v2"
            if task_usage_missing
            else "tokenshare.paper_task_result.v1"
        ),
    )
    generation_identity = {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": runtime_result.run_id,
        "task_id": runtime_result.task_id,
        "root_unit_id": runtime_result.root_unit_id,
        "event_count": len(events),
        "first_event_id": str(events[0]["event_id"]),
        "last_event_id": str(events[-1]["event_id"]),
        "last_event_hash": events[-1].get("event_hash"),
        "ledger_digest": digest_json(list(events)),
        "runtime_observation_digest": (
            digest_json(runtime_observation) if runtime_observation else None
        ),
    }
    return PaperProtocolProjection(
        task_result=task_result,
        attempt_results=tuple(attempts),
        lifecycle_coverage=lifecycle_coverage,
        runtime_generation_identity=generation_identity,
        runtime_observation=runtime_observation,
        ineligibility_reasons=tuple(reasons),
    )


def _event_body(value: Any) -> JsonObject:
    if isinstance(value, Mapping):
        body = dict(value)
    else:
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict) or not isinstance(to_dict(), dict):
            raise TypeError("protocol event must be a mapping or expose to_dict")
        body = dict(to_dict())
    event_type = body.get("event_type")
    if isinstance(event_type, Enum):
        body["event_type"] = event_type.value
    for field_name in ("event_id", "event_type", "task_id", "occurred_at"):
        if not isinstance(body.get(field_name), str) or not body[field_name]:
            raise ValueError(f"protocol event {field_name} is required")
    if not isinstance(body.get("payload"), dict):
        raise ValueError("protocol event payload is required")
    return body


def _validate_runtime_event_refs(
    runtime_result: ProtocolRunResult,
    events: Sequence[JsonObject],
) -> None:
    expected = tuple(
        (str(ref.get("event_id")), int(ref.get("event_seq", 0)), str(ref.get("event_type")))
        for ref in runtime_result.event_refs
    )
    actual = tuple(
        (str(event["event_id"]), int(event.get("event_seq", 0)), str(event["event_type"]))
        for event in events
    )
    if expected != actual:
        raise ValueError("runtime event refs do not match protocol event generation")


def _request_records(
    events: Sequence[JsonObject],
    store: ArtifactStore,
) -> tuple[tuple[JsonObject, JsonObject, JsonObject], ...]:
    records = []
    for event in events:
        if event["event_type"] != "EXECUTION_REQUEST_RECORDED":
            continue
        ref = _mapping_or_none(event["payload"].get("request_ref"))
        body = _read_ref_body(store, ref)
        if not body:
            raise ValueError("execution request artifact is missing")
        assert ref is not None
        records.append((event, body, ref))
    return tuple(records)


def _submission_records(
    events: Sequence[JsonObject],
    store: ArtifactStore,
) -> dict[str, tuple[JsonObject, JsonObject, JsonObject | None]]:
    records = {}
    for event in events:
        if event["event_type"] != "EXECUTION_SUBMISSION_RECORDED":
            continue
        attempt_id = str(event["payload"].get("attempt_id") or "")
        ref = _mapping_or_none(event["payload"].get("submission_ref"))
        body = _read_ref_body(store, ref)
        records[attempt_id] = (event, body, ref)
    return records


def _attempt_snapshots(events: Sequence[JsonObject]) -> dict[str, JsonObject]:
    result = {}
    for event in events:
        if event["event_type"] != "ATTEMPT_STATE_CHANGED":
            continue
        attempt = event["payload"].get("attempt")
        if isinstance(attempt, dict) and attempt.get("attempt_id"):
            result[str(attempt["attempt_id"])] = dict(attempt)
    return result


def _events_by_attempt(
    events: Sequence[JsonObject],
    event_type: str,
) -> dict[str, JsonObject]:
    result = {}
    for event in events:
        if event["event_type"] == event_type and event["payload"].get("attempt_id"):
            result[str(event["payload"]["attempt_id"])] = event
    return result


def _recovery_events_by_attempt(events: Sequence[JsonObject]) -> dict[str, JsonObject]:
    result = {}
    for event in events:
        if event["event_type"] != "RECOVERY_ACTION_RECORDED":
            continue
        action = event["payload"].get("recovery_action")
        if isinstance(action, dict) and action.get("attempt_id"):
            result[str(action["attempt_id"])] = event
    return result


def _artifact_inventory(store: ArtifactStore) -> tuple[ArtifactRef, ...]:
    refs = []
    for path in sorted(store.artifact_dir.glob("*.manifest.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        ref = ArtifactRef.from_dict(body)
        if not store.verify(ref):
            raise ValueError(f"artifact verification failed: {ref.artifact_id}")
        refs.append(ref)
    return tuple(refs)


def _artifact_for_execution(
    inventory: Sequence[ArtifactRef],
    store: ArtifactStore,
    *,
    artifact_types: set[str],
    attempt_id: str = "",
    request_id: str = "",
    submission_id: str = "",
) -> tuple[JsonObject | None, JsonObject]:
    for ref in inventory:
        if ref.artifact_type not in artifact_types:
            continue
        body = _read_ref_body(store, ref.to_dict())
        if attempt_id and str(body.get("attempt_id") or "") == attempt_id:
            return ref.to_dict(), body
        if request_id and str(body.get("request_id") or "") == request_id:
            return ref.to_dict(), body
        if submission_id and str(body.get("submission_id") or "") == submission_id:
            return ref.to_dict(), body
    return None, {}


def _auditable_no_return_evidence(
    *,
    condition: PaperExperimentCondition,
    submission_event: Mapping[str, Any],
    submission: Mapping[str, Any],
    request: Mapping[str, Any],
    request_ref: JsonObject,
    model_record: Mapping[str, Any],
    fault_record: Mapping[str, Any],
    fault_injection_ref: JsonObject | None,
    runtime_fault_record: Mapping[str, Any],
    runtime_fault_ref: JsonObject | None,
    store: ArtifactStore,
) -> JsonObject:
    """只接受 provider 响应已持久化后被确定性丢弃的 no_return。"""

    attempt_id = str(request.get("attempt_id") or "")
    request_id = str(request.get("request_id") or "")
    if (
        submission_event
        or submission
        or condition.fault_type != "no_return"
        or not attempt_id
        or not request_id
        or fault_injection_ref is None
        or runtime_fault_ref is None
        or runtime_fault_record.get("attempt_id") != attempt_id
        or runtime_fault_record.get("fault_type") != "no_return"
        or runtime_fault_record.get("applicability_status") != "injected"
        or runtime_fault_record.get("hook_stage")
        != "after_raw_provenance_usage_before_parser"
        or runtime_fault_record.get("injection_point")
        != "after_raw_output_before_submission"
        or runtime_fault_record.get("mutated_attempt_status") != "lease_expired"
        or runtime_fault_record.get("original_attempt_status") != "succeeded"
        or not _same_artifact_ref(
            runtime_fault_record.get("primitive_fault_record_ref"),
            fault_injection_ref,
        )
        or fault_record.get("attempt_id") != attempt_id
        or fault_record.get("fault_type") != "no_return"
        or fault_record.get("injection_point") != "after_raw_output_before_submission"
        or fault_record.get("mutated_attempt_status") != "lease_expired"
        or fault_record.get("original_attempt_status") != "succeeded"
        or not isinstance(fault_record.get("mutation_summary"), Mapping)
        or fault_record["mutation_summary"].get("mutation_kind")
        != "no_return_drop_submission"
        or model_record.get("attempt_id") != attempt_id
        or model_record.get("paper_eligible") is not True
        or model_record.get("identity_status") != "matched"
        or not _same_artifact_ref(model_record.get("request_ref"), request_ref)
    ):
        return {}

    raw_output_ref, raw_output = _verified_artifact_body(
        store,
        runtime_fault_record.get("original_raw_output_ref"),
    )
    provenance_ref, provenance = _verified_artifact_body(
        store,
        runtime_fault_record.get("original_provenance_ref"),
    )
    pre_fault_usage_ref, pre_fault_usage = _verified_artifact_body(
        store,
        runtime_fault_record.get("pre_fault_usage_ref"),
    )
    model_usage_ref, model_usage = _verified_artifact_body(
        store,
        model_record.get("usage_ref"),
    )
    if (
        raw_output_ref is None
        or provenance_ref is None
        or pre_fault_usage_ref is None
        or model_usage_ref is None
        or not raw_output
        or not provenance
        or not pre_fault_usage
        or not model_usage
        or not _same_artifact_ref(model_record.get("raw_output_ref"), raw_output_ref)
        or not _same_artifact_ref(
            model_record.get("provenance_ref"),
            provenance_ref,
        )
        or not _same_artifact_ref(
            fault_record.get("original_raw_output_ref"),
            raw_output_ref,
        )
        or provenance.get("request_id") != request_id
        or provenance.get("lifecycle_stage")
        != "provider_response_persisted_before_parser"
        or not _same_artifact_ref(provenance.get("raw_output_ref"), raw_output_ref)
        or not isinstance(provenance.get("attempts"), list)
        or not provenance["attempts"]
        or pre_fault_usage.get("request_id") != request_id
        or pre_fault_usage.get("lifecycle_stage")
        != "provider_response_persisted_before_parser"
        or not _same_artifact_ref(pre_fault_usage.get("raw_output_ref"), raw_output_ref)
        or (
            model_usage.get("request_id") is not None
            and model_usage.get("request_id") != request_id
        )
    ):
        return {}
    return {
        "raw_output_ref": raw_output_ref,
        "provenance_ref": provenance_ref,
        "usage_ref": model_usage_ref,
    }


def _verified_artifact_body(
    store: ArtifactStore,
    value: Any,
) -> tuple[JsonObject | None, JsonObject]:
    ref = _mapping_or_none(value)
    if ref is None:
        return None, {}
    try:
        return ref, _read_ref_body(store, ref)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None, {}


def _same_artifact_ref(left: Any, right: Any) -> bool:
    left_ref = _mapping_or_none(left)
    right_ref = _mapping_or_none(right)
    if left_ref is None or right_ref is None:
        return False
    left_identity = tuple(
        left_ref.get(field_name)
        for field_name in ("artifact_id", "content_hash", "uri")
    )
    right_identity = tuple(
        right_ref.get(field_name)
        for field_name in ("artifact_id", "content_hash", "uri")
    )
    if any(
        not isinstance(value, str) or not value
        for value in (*left_identity, *right_identity)
    ):
        return False
    return left_identity == right_identity


def _read_ref_body(store: ArtifactStore, value: JsonObject | None) -> JsonObject:
    if value is None:
        return {}
    ref = ArtifactRef.from_dict(value)
    if not store.verify(ref):
        raise ValueError(f"artifact verification failed: {ref.artifact_id}")
    body = json.loads(store.read_bytes(ref).decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("paper projection artifact body must be an object")
    return body


def _attempt_status(
    *,
    condition: PaperExperimentCondition,
    submission_event: JsonObject,
    submission: JsonObject,
    snapshot: JsonObject,
    verification: JsonObject,
    canonical: bool,
    recovery: JsonObject,
    model_record: JsonObject,
    auditable_no_return: bool = False,
) -> tuple[PaperAttemptStatus, str | None]:
    if model_record.get("identity_status") == "model_identity_mismatch":
        return PaperAttemptStatus.MODEL_IDENTITY_MISMATCH, "model_identity_mismatch"
    if not submission_event or not submission:
        if auditable_no_return:
            return PaperAttemptStatus.LEASE_EXPIRED, "lease_expired"
        return PaperAttemptStatus.PROVIDER_ERROR, "missing_submission_event"
    rejection_reason = submission_event.get("payload", {}).get("rejection_reason")
    if rejection_reason in {"lease_expired", "deadline_exceeded", "late_submission"}:
        return PaperAttemptStatus.LATE_REJECTED, "late_submission"
    result_kind = str(submission.get("result_kind") or "")
    if result_kind == "parse_failed":
        return PaperAttemptStatus.PARSE_FAILED, "parse_failure"
    if result_kind in {"executor_error", "fatal_executor_error"}:
        error_kind = _submission_error_kind(submission, default=result_kind)
        usage_summary = submission.get("usage_summary")
        provider_attempt_count = (
            usage_summary.get("provider_attempt_count")
            if isinstance(usage_summary, Mapping)
            else None
        )
        if provider_attempt_count == 0 and submission.get("raw_output_ref") is None:
            return PaperAttemptStatus.EXECUTOR_ERROR, error_kind
        return PaperAttemptStatus.PROVIDER_ERROR, error_kind
    if result_kind in {
        "provider_error",
        "rate_limited",
        "timeout",
        "connection_error",
        "auth_error",
        "client_error",
        "no_return",
    }:
        error = submission.get("error")
        error_kind = str(error.get("kind")) if isinstance(error, dict) and error.get("kind") else result_kind
        return PaperAttemptStatus.PROVIDER_ERROR, error_kind
    verification_payload = verification.get("payload", {})
    verification_report = verification_payload.get("verification_report", {})
    verification_status = str(
        verification_payload.get("status")
        or (
            verification_report.get("status")
            if isinstance(verification_report, dict)
            else ""
        )
        or ""
    )
    if verification_status in {"rejected", "failed"}:
        if condition.domain == "lean_proof":
            return PaperAttemptStatus.CHECKER_REJECTED, "checker_rejected"
        return PaperAttemptStatus.VERIFICATION_REJECTED, "verifier_rejected"
    action = recovery.get("payload", {}).get("recovery_action")
    if isinstance(action, dict) and action.get("trigger") == "lease_expired":
        return PaperAttemptStatus.LEASE_EXPIRED, "lease_expired"
    if snapshot.get("failure_kind") == "worker_terminated":
        return PaperAttemptStatus.WORKER_DIED, "worker_died"
    if canonical:
        return PaperAttemptStatus.SUCCEEDED, None
    if snapshot.get("state") in {"Failed", "Superseded", "Rejected"}:
        return PaperAttemptStatus.PROVIDER_ERROR, str(snapshot.get("failure_kind") or "provider_error")
    return PaperAttemptStatus.SUCCEEDED, None


def _provider_failure_identity_is_auditable(
    *,
    model_record: Mapping[str, Any],
    provider_attempts: Sequence[Mapping[str, Any]],
) -> bool:
    """确认无响应失败仍绑定到冻结 endpoint/request identity。"""

    if (
        model_record.get("schema_version")
        != "tokenshare.paper_model_execution_record.v2"
        or model_record.get("identity_status") != "not_observed"
        or model_record.get("response_model_status") != "unavailable"
        or model_record.get("raw_output_ref") is not None
        or tuple(model_record.get("mismatch_reasons") or ())
    ):
        return False
    expected = model_record.get("expected_identity")
    request_identities = model_record.get("actual_request_identities")
    recorded_attempts = model_record.get("actual_provider_attempts")
    if (
        not isinstance(expected, Mapping)
        or not isinstance(request_identities, list)
        or not isinstance(recorded_attempts, list)
        or len(request_identities) != len(provider_attempts)
        or len(recorded_attempts) != len(provider_attempts)
        or recorded_attempts != list(provider_attempts)
    ):
        return False
    expected_provider = expected.get("provider_family")
    expected_model = expected.get("provider_model_id")
    expected_entry = expected.get("selected_entry_id")
    return all(
        isinstance(identity, Mapping)
        and identity.get("schema_version")
        == "phase7.provider_request_identity.v2"
        and identity.get("provider_family") == expected_provider
        and identity.get("entry_id") == expected_entry
        and identity.get("configured_model") == expected_model
        and identity.get("requested_model") == expected_model
        and isinstance(identity.get("reasoning_controls"), Mapping)
        and isinstance(identity.get("effective_request_controls_digest"), str)
        and str(identity["effective_request_controls_digest"]).startswith("sha256:")
        for identity in request_identities
    )


def _submission_provider_attempt_count(
    *,
    submission: Mapping[str, Any],
    usage: Mapping[str, Any],
    provider_attempts: Sequence[Mapping[str, Any]],
) -> int:
    """优先使用 executor 持久化的真实 provider call 计数。"""

    usage_summary = submission.get("usage_summary")
    candidates = (
        usage_summary.get("provider_attempt_count")
        if isinstance(usage_summary, Mapping)
        else None,
        usage.get("provider_attempt_count"),
    )
    for value in candidates:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return len(provider_attempts)


def _submission_error_kind(
    submission: Mapping[str, Any],
    *,
    default: str,
) -> str:
    error = submission.get("error")
    if not isinstance(error, Mapping):
        return default
    attempts = error.get("attempts")
    if isinstance(attempts, Sequence) and not isinstance(
        attempts,
        (str, bytes, bytearray),
    ):
        for attempt in reversed(attempts):
            if isinstance(attempt, Mapping):
                result_kind = attempt.get("result_kind")
                if isinstance(result_kind, str) and result_kind:
                    return result_kind
    reason = error.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    kind = error.get("kind")
    return str(kind) if isinstance(kind, str) and kind else default


def _paper_task_status(status: str) -> PaperTaskStatus:
    normalized = str(status).lower()
    return {
        "completed": PaperTaskStatus.COMPLETED,
        "failed": PaperTaskStatus.FAILED,
        "blocked": PaperTaskStatus.BLOCKED,
        "timeout": PaperTaskStatus.TIMEOUT,
        "budget_exhausted": PaperTaskStatus.BUDGET_EXHAUSTED,
        "ineligible": PaperTaskStatus.INELIGIBLE,
        "partial": PaperTaskStatus.PARTIAL,
        "processing": PaperTaskStatus.BLOCKED,
        "ready": PaperTaskStatus.BLOCKED,
        "pending": PaperTaskStatus.BLOCKED,
    }.get(normalized, PaperTaskStatus.FAILED)


def _lifecycle_coverage(
    *,
    root_status: PaperTaskStatus,
    root_unit_id: str,
    event_types: Sequence[str],
    events: Sequence[JsonObject],
) -> JsonObject:
    required = {
        "TASK_REGISTERED",
        "EXECUTION_REQUEST_RECORDED",
        "EXECUTION_SUBMISSION_RECORDED",
        "VERIFICATION_RECORDED",
        "CANONICAL_OUTPUTS_BOUND",
        "TASK_UNIT_STATE_CHANGED",
        "SETTLEMENT_RECORDED",
    }
    if "TASK_EXPANDED" in event_types:
        required.add("MERGE_RECORDED")
    present = set(event_types)
    completed_root = any(
        event["event_type"] == "TASK_UNIT_STATE_CHANGED"
        and event["payload"].get("new_state") == "Completed"
        and (
            event["payload"].get("task_unit", {}).get("unit_id") == root_unit_id
            if isinstance(event["payload"].get("task_unit"), dict)
            else event.get("object_id") == root_unit_id
        )
        for event in events
    )
    missing = sorted(required - present)
    if root_status == PaperTaskStatus.COMPLETED and not completed_root:
        missing.append("ROOT_COMPLETION")
    return {
        "required_event_types": sorted(required),
        "present_event_types": sorted(present),
        "missing_event_types": list(dict.fromkeys(missing)),
        "complete": not missing,
    }


def _task_failure(
    attempts: Sequence[PaperAttemptResult],
    events: Sequence[JsonObject],
    root_status: PaperTaskStatus,
) -> tuple[PaperFailureStage | None, PaperFailureKind | None]:
    if root_status == PaperTaskStatus.COMPLETED:
        return None, None
    for attempt in reversed(attempts):
        mapping = {
            PaperAttemptStatus.MODEL_IDENTITY_MISMATCH: (
                PaperFailureStage.AUDIT,
                PaperFailureKind.MODEL_IDENTITY_MISMATCH,
            ),
            PaperAttemptStatus.PARSE_FAILED: (
                PaperFailureStage.PARSE,
                PaperFailureKind.PARSE_FAILURE,
            ),
            PaperAttemptStatus.VERIFICATION_REJECTED: (
                PaperFailureStage.VERIFICATION,
                PaperFailureKind.VERIFIER_REJECTED,
            ),
            PaperAttemptStatus.CHECKER_REJECTED: (
                PaperFailureStage.CHECKER,
                PaperFailureKind.CHECKER_REJECTED,
            ),
            PaperAttemptStatus.LEASE_EXPIRED: (
                PaperFailureStage.PROVIDER,
                PaperFailureKind.LEASE_EXPIRED,
            ),
            PaperAttemptStatus.LATE_REJECTED: (
                PaperFailureStage.PROVIDER,
                PaperFailureKind.LATE_SUBMISSION,
            ),
            PaperAttemptStatus.PROVIDER_ERROR: (
                PaperFailureStage.PROVIDER,
                PaperFailureKind.PROVIDER_ERROR,
            ),
            PaperAttemptStatus.EXECUTOR_ERROR: (
                PaperFailureStage.REQUEST,
                PaperFailureKind.EXECUTOR_ERROR,
            ),
        }
        if attempt.attempt_status in mapping:
            return mapping[attempt.attempt_status]
    if any(event["event_type"] == "MERGE_RECORDED" for event in events):
        return PaperFailureStage.SETTLEMENT, PaperFailureKind.INTERNAL_ERROR
    return PaperFailureStage.MERGE, PaperFailureKind.INTERNAL_ERROR


def _stable_artifact_refs(
    runtime_refs: Sequence[ArtifactRef],
    attempts: Sequence[PaperAttemptResult],
) -> list[JsonObject]:
    result: dict[tuple[str, str], JsonObject] = {
        (ref.artifact_id, ref.content_hash): ref.to_dict() for ref in runtime_refs
    }
    for attempt in attempts:
        for value in (
            attempt.request_ref,
            attempt.raw_output_ref,
            attempt.parsed_output_ref,
            attempt.parse_failure_ref,
            attempt.provenance_ref,
            attempt.usage_ref,
            attempt.model_execution_record_ref,
            attempt.fault_injection_ref,
        ):
            if isinstance(value, dict) and value.get("artifact_id") and value.get("content_hash"):
                result[(str(value["artifact_id"]), str(value["content_hash"]))] = dict(value)
    return [result[key] for key in sorted(result)]


def _event_ref(event: JsonObject) -> JsonObject:
    return {
        "event_id": event["event_id"],
        "event_seq": event.get("event_seq"),
        "event_type": event["event_type"],
    }


def _mapping_or_none(value: Any) -> JsonObject | None:
    return dict(value) if isinstance(value, Mapping) else None


def _string_or_none(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        raise ValueError("negative integer measurement is invalid")
    return int(value)


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        raise ValueError("negative numeric measurement is invalid")
    return float(value)


def _complete_attempt_sum(
    attempts: Sequence[PaperAttemptResult],
    field_name: str,
) -> int | float | None:
    values = [getattr(attempt, field_name) for attempt in attempts]
    if any(value is None for value in values):
        return None
    return sum(values)


def _wall_clock_ms(events: Sequence[JsonObject]) -> int:
    started = _timestamp(events[0]["occurred_at"])
    ended = _timestamp(events[-1]["occurred_at"])
    interval_ms = int((ended - started).total_seconds() * 1000)
    if interval_ms < 0:
        raise ValueError("negative event timing is invalid")
    return interval_ms


def _runtime_observation(runtime_result: ProtocolRunResult) -> JsonObject:
    value = runtime_result.summary.get("runtime_observation")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("runtime_observation must be a mapping")
    observation = dict(value)
    if observation.get("schema_version") != (
        "tokenshare.protocol_runtime_observation.v1"
    ):
        raise ValueError("runtime_observation schema is unsupported")
    if observation.get("run_id") != runtime_result.run_id:
        raise ValueError("runtime_observation run_id mismatch")
    _timestamp(str(observation.get("runtime_started_at")))
    _timestamp(str(observation.get("runtime_ended_at")))
    wall_clock_ms = observation.get("runtime_wall_clock_ms")
    if (
        isinstance(wall_clock_ms, bool)
        or not isinstance(wall_clock_ms, (int, float))
        or wall_clock_ms < 0
    ):
        raise ValueError("runtime_observation wall clock is invalid")
    facts = observation.get("worker_execution_facts")
    if not isinstance(facts, list):
        raise ValueError("runtime_observation worker facts are required")
    return observation


def _runtime_wall_clock_ms(
    runtime_observation: Mapping[str, Any],
    events: Sequence[JsonObject],
) -> int:
    value = runtime_observation.get("runtime_wall_clock_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 0:
            raise ValueError("negative runtime wall clock is invalid")
        return int(value)
    return _wall_clock_ms(events)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
