"""从系统 runtime 事实派生论文 task/attempt 结果。

本模块只读取 ``ProtocolRunResult``、协议事件和已持久化 artifact。它不推进
协议状态，也不接受 paper runner 提供的成功/失败结论作为权威输入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
            artifact_types={"AIUsageSummary", "UsageSummary", "AIUsage"},
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
        provenance_ref = _mapping_or_none(submission.get("provenance_ref"))
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
        )
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
        provider_attempt_index = next_provider_attempt_index_by_unit.get(unit_id, 0)
        next_provider_attempt_index_by_unit[unit_id] = provider_attempt_index + 1
        attempt_evidence_complete = all(
            (
                request_ref is not None,
                _mapping_or_none(submission.get("raw_output_ref")) is not None,
                provenance_ref is not None,
                usage_ref is not None,
                model_record_ref is not None,
                model_record.get("paper_eligible") is True,
                bool(provider_attempts),
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
                provider=str(
                    usage.get("provider_family")
                    or last_provider_attempt.get("provider_family")
                    or condition.provider_family
                    or "unknown"
                ),
                model=str(
                    usage.get("model")
                    or usage.get("configured_model")
                    or last_provider_attempt.get("configured_model")
                    or condition.provider_model_id
                    or "unknown"
                ),
                entry_id=str(
                    usage.get("entry_id")
                    or last_provider_attempt.get("entry_id")
                    or condition.model_entry_id
                    or "unknown"
                ),
                request_ref=request_ref,
                raw_output_ref=_mapping_or_none(submission.get("raw_output_ref")),
                parsed_output_ref=_mapping_or_none(submission.get("parsed_output_ref")),
                parse_failure_ref=_mapping_or_none(submission.get("parse_failure_ref")),
                provenance_ref=provenance_ref,
                usage_ref=usage_ref,
                model_execution_record_ref=model_record_ref,
                provider_attempt_count=len(provider_attempts),
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
                latency_ms=_non_negative_int(last_provider_attempt.get("latency_ms")),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_estimate=_non_negative_number(usage.get("cost_estimate")),
                error_kind=error_kind,
                fault_injection_ref=_fault_ref_for_attempt(
                    inventory,
                    artifact_store,
                    attempt_id,
                ),
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
        provider_attempt_count=len(attempts),
        wall_clock_ms=_runtime_wall_clock_ms(runtime_observation, events),
        total_tokens=sum(attempt.total_tokens for attempt in attempts),
        cost_estimate=sum(attempt.cost_estimate for attempt in attempts),
        event_refs=[_event_ref(event) for event in events],
        artifact_refs=all_artifact_refs,
        paper_eligible=paper_eligible,
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


def _fault_ref_for_attempt(
    inventory: Sequence[ArtifactRef],
    store: ArtifactStore,
    attempt_id: str,
) -> JsonObject | None:
    ref, _body = _artifact_for_execution(
        inventory,
        store,
        artifact_types={"FaultInjectionRecord"},
        attempt_id=attempt_id,
    )
    return ref


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
) -> tuple[PaperAttemptStatus, str | None]:
    if model_record.get("identity_status") == "model_identity_mismatch":
        return PaperAttemptStatus.MODEL_IDENTITY_MISMATCH, "model_identity_mismatch"
    if not submission_event or not submission:
        return PaperAttemptStatus.PROVIDER_ERROR, "missing_submission_event"
    rejection_reason = submission_event.get("payload", {}).get("rejection_reason")
    if rejection_reason in {"lease_expired", "deadline_exceeded", "late_submission"}:
        return PaperAttemptStatus.LATE_REJECTED, "late_submission"
    result_kind = str(submission.get("result_kind") or "")
    if result_kind == "parse_failed":
        return PaperAttemptStatus.PARSE_FAILED, "parse_failure"
    if result_kind in {
        "provider_error",
        "rate_limited",
        "executor_error",
        "fatal_executor_error",
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


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _non_negative_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def _wall_clock_ms(events: Sequence[JsonObject]) -> int:
    try:
        started = _timestamp(events[0]["occurred_at"])
        ended = _timestamp(events[-1]["occurred_at"])
    except ValueError:
        return 0
    return max(0, int((ended - started).total_seconds() * 1000))


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
        return max(0, int(value))
    return _wall_clock_ms(events)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
