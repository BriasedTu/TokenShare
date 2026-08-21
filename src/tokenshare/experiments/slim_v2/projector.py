"""Slim V2 单 root 只读投影。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from datetime import datetime
from math import isfinite
from typing import Any

from tokenshare.core.models import ArtifactRef
from tokenshare.local_runtime import ProtocolRunResult
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.factorization.schemas import (
    REQUESTED_OUTPUT_PRIME_FACTORIZATION,
)
from tokenshare.plugins.lean_proof.checker import LeanCheckerStatus
from tokenshare.plugins.lean_proof.runtime_adapter import LeanRuntimeAdapter
from tokenshare.plugins.lean_proof.schemas import PROOF_ARTIFACT_OUTPUT_NAME
from tokenshare.storage.events import EventType

from .runtime import RootAssembly
from .schema import RootInventoryV1, RootResultV1, WorkerExecutionFactV1


class RootProjectionError(RuntimeError):
    """当前 root 的公共事实不足或彼此矛盾。"""


def project_root_result(
    *,
    inventory: RootInventoryV1,
    assembly: RootAssembly,
    protocol_result: ProtocolRunResult,
    provider_family: str,
    requested_model: str,
    resolved_model: str,
    reasoning_mode: str,
) -> RootResultV1:
    """只读 join 当前 result、ledger/store 与插件领域事实。"""

    inventory.validate()
    if inventory.experiment_id != "exp1":
        raise RootProjectionError("Task 1 projector only supports Exp1 roots")
    if protocol_result.run_id != assembly.run_id:
        raise RootProjectionError("protocol result run_id does not match assembly")
    observation = protocol_result.summary.get("runtime_observation")
    if not isinstance(observation, Mapping):
        raise RootProjectionError("protocol result is missing runtime_observation")
    if observation.get("run_id") != assembly.run_id:
        raise RootProjectionError("runtime observation run_id does not match assembly")

    events = tuple(
        event
        for event in assembly.event_ledger.read_all()
        if event.task_id == protocol_result.task_id
    )
    if not any(event.event_type == EventType.TASK_REGISTERED for event in events):
        raise RootProjectionError("protocol task lifecycle event is missing")
    merge_events = tuple(
        event for event in events if event.event_type == EventType.MERGE_RECORDED
    )
    if merge_events:
        canonical_events, merge_event, final_canonical_event = _merge_ledger_facts(
            events
        )
        required_slot_count, recovered_slot_count = _slot_counts(
            assembly=assembly,
            canonical_events=canonical_events,
            merge_event=merge_event,
        )
        merge_output_refs = merge_event.payload.get("merge_output_refs")
        merge_output_ids = _artifact_ids(
            merge_output_refs,
            "merge output refs",
        )
        canonical_output_ids = _artifact_ids(
            final_canonical_event.payload.get("canonical_output_refs"),
            "final canonical output refs",
        )
        if merge_output_ids != canonical_output_ids:
            raise RootProjectionError(
                "merge outputs do not match the corresponding canonical event"
            )
        final_result_present, verified_correct = _domain_result(
            inventory=inventory,
            assembly=assembly,
            final_output_ids=merge_output_ids,
            final_output_refs=merge_output_refs,
        )
        if verified_correct:
            failure_stage = None
            failure_kind = None
        else:
            failure_stage = "domain_root_recheck"
            failure_kind = "incorrect_final"
    else:
        failure_stage = _natural_rejection_stage(
            events=events,
            protocol_result=protocol_result,
        )
        required_slot_count, recovered_slot_count = _failed_slot_counts(
            assembly=assembly,
            events=events,
        )
        final_result_present = False
        verified_correct = False
        failure_kind = "no_final"

    root_start_at_ms = _timestamp_ms(
        observation.get("runtime_started_at"),
        "runtime_started_at",
    )
    root_terminal_at_ms = _timestamp_ms(
        observation.get("runtime_ended_at"),
        "runtime_ended_at",
    )
    runtime_wall_clock_ms = root_terminal_at_ms - root_start_at_ms
    observed_wall = observation.get("runtime_wall_clock_ms")
    if (
        isinstance(observed_wall, bool)
        or not isinstance(observed_wall, (int, float))
        or not isfinite(float(observed_wall))
        or abs(float(observed_wall) - runtime_wall_clock_ms) > 1.0
    ):
        raise RootProjectionError("runtime wall clock contradicts lifecycle boundaries")

    values: dict[str, Any] = {
        "experiment_id": inventory.experiment_id,
        "condition_id": inventory.condition_id,
        "case_id": inventory.case_id,
        "repeat_id": inventory.repeat_id,
        "domain": inventory.domain,
        "difficulty": inventory.difficulty,
        "topic_family": inventory.topic_family,
        "position_stratum": inventory.position_stratum,
        "mode": inventory.mode,
        "disabled_mechanisms": list(inventory.disabled_mechanisms),
        "worker_count": inventory.worker_count,
        "fault_type": inventory.fault_type,
        "fault_rate": inventory.fault_rate,
        "dead_worker_count": inventory.dead_worker_count,
        "kill_progress_target_ratio": inventory.kill_progress_target_ratio,
        "challenge_plan_id": inventory.challenge_plan_id,
        "challenge_family": None,
        "challenge_target_planned_ai_unit_ids": None,
        "challenge_attempt_ordinal_rule": None,
        "provider_family": provider_family,
        "provider_entry_id": inventory.provider_entry_id,
        "configured_model": inventory.configured_model,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "reasoning_mode": reasoning_mode,
        "root_start_at_ms": root_start_at_ms,
        "root_terminal_at_ms": root_terminal_at_ms,
        "runtime_wall_clock_ms": runtime_wall_clock_ms,
        # coverage tail 在 Task 3 才执行；Task 1 明确投影为无目标。
        "trace_tail_started_at_ms": None,
        "trace_tail_terminal_at_ms": None,
        "trace_tail_wall_clock_ms": 0,
        "trace_tail_status": "not_needed",
        "trace_tail_target_ai_unit_ids": [],
        "trace_tail_recorded_ai_unit_ids": [],
        "trace_tail_success_unit_count": 0,
        "trace_tail_failure_unit_count": 0,
        "trace_tail_provider_attempt_count": 0,
        "trace_tail_total_tokens": 0,
        "trace_tail_cost_estimate_cny": 0,
        "preflight_status": "passed",
        "protocol_started": True,
        "root_status": protocol_result.status,
        "final_result_present": final_result_present,
        "verified_correct": verified_correct,
        "failure_stage": failure_stage,
        "failure_kind": failure_kind,
        "planned_ai_unit_ids": _string_list(
            observation.get("planned_ai_unit_ids"),
            "planned_ai_unit_ids",
        ),
        "dispatched_ai_unit_ids": _string_list(
            observation.get("dispatched_ai_unit_ids"),
            "dispatched_ai_unit_ids",
        ),
        "completed_ai_unit_ids": _string_list(
            observation.get("completed_ai_unit_ids"),
            "completed_ai_unit_ids",
        ),
        "unscheduled_ai_unit_ids": _string_list(
            observation.get("unscheduled_ai_unit_ids"),
            "unscheduled_ai_unit_ids",
        ),
        "in_flight_ai_unit_ids_at_witness": _string_list(
            observation.get("in_flight_ai_unit_ids_at_witness"),
            "in_flight_ai_unit_ids_at_witness",
        ),
        "observed_peak_concurrency": _nonnegative_int(
            observation.get("observed_peak_concurrency"),
            "observed_peak_concurrency",
        ),
        "worker_execution_facts": _worker_facts(
            observation.get("worker_execution_facts")
        ),
        "required_slot_count": required_slot_count,
        "recovered_valid_canonical_slot_count": recovered_slot_count,
        # attempt/provider materialization 属于 Task 2/3。
        "attempts": [],
        "fault_target_planned_ai_unit_ids": None,
        "fault_target_count": None,
        "fault_observations": [],
        "recovery_observations": [],
        "worker_death_observations": [],
        "challenge_observations": [],
        "ablation_observations": [],
        "missing_reason": {},
        "not_applicable_reason": {},
    }
    values["not_applicable_reason"] = _null_reasons(values)
    projected = RootResultV1(**values)
    projected.validate()
    return projected


def _merge_ledger_facts(events: Sequence[Any]) -> tuple[tuple[Any, ...], Any, Any]:
    canonical_events = tuple(
        event for event in events if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
    )
    merge_events = tuple(
        event for event in events if event.event_type == EventType.MERGE_RECORDED
    )
    if not canonical_events:
        raise RootProjectionError("canonical ledger fact is missing")
    if len(merge_events) != 1:
        raise RootProjectionError("root projection requires exactly one merge ledger fact")
    merge_event = merge_events[0]
    canonical_event_seq = merge_event.payload.get("canonical_event_seq")
    matches = tuple(
        event
        for event in canonical_events
        if event.event_seq == canonical_event_seq
        and event.payload.get("unit_id") == merge_event.payload.get("merge_unit_id")
    )
    if len(matches) != 1:
        raise RootProjectionError(
            "merge ledger fact has no corresponding canonical ledger fact"
        )
    return canonical_events, merge_event, matches[0]


def _slot_counts(
    *,
    assembly: RootAssembly,
    canonical_events: Sequence[Any],
    merge_event: Any,
) -> tuple[int, int]:
    required_slot_count = _required_slot_count(assembly)
    bundle_ref_body = merge_event.payload.get("merge_input_bundle_ref")
    if not isinstance(bundle_ref_body, Mapping):
        raise RootProjectionError("merge input bundle ref is missing")
    try:
        bundle_ref = ArtifactRef.from_dict(dict(bundle_ref_body))
        bundle = json.loads(assembly.artifact_store.read_bytes(bundle_ref))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RootProjectionError("merge input bundle is unreadable") from exc
    readiness = bundle.get("readiness_decision")
    if not isinstance(readiness, Mapping):
        raise RootProjectionError("merge readiness decision is missing")
    required_children = _string_list(
        readiness.get("required_child_unit_ids"),
        "required_child_unit_ids",
    )
    if len(required_children) != required_slot_count:
        raise RootProjectionError("required child inventory contradicts merge plan slots")
    canonical_units = {
        unit_id
        for event in canonical_events
        if isinstance((unit_id := event.payload.get("unit_id")), str)
    }
    recovered_slot_count = sum(
        unit_id in canonical_units for unit_id in required_children
    )
    return required_slot_count, recovered_slot_count


def _failed_slot_counts(
    *,
    assembly: RootAssembly,
    events: Sequence[Any],
) -> tuple[int, int]:
    required_slots = _required_slots(assembly)
    required_child_unit_ids = []
    for slot in required_slots:
        unit_id = slot.get("source_child_unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise RootProjectionError(
                "plugin required slot has no source child unit identity"
            )
        required_child_unit_ids.append(unit_id)
    if len(required_child_unit_ids) != len(set(required_child_unit_ids)):
        raise RootProjectionError("plugin required slots repeat a child unit")
    canonical_units = {
        unit_id
        for event in events
        if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and isinstance((unit_id := event.payload.get("unit_id")), str)
    }
    recovered_slot_count = sum(
        unit_id in canonical_units for unit_id in required_child_unit_ids
    )
    return len(required_slots), recovered_slot_count


def _required_slots(assembly: RootAssembly) -> tuple[Mapping[str, Any], ...]:
    try:
        required_slots = assembly.plugin_runtime.planned_split_plan.merge_plan.required_slots
    except (AttributeError, RuntimeError) as exc:
        raise RootProjectionError("plugin required-slot plan is unavailable") from exc
    if not isinstance(required_slots, Sequence) or isinstance(
        required_slots,
        (str, bytes, bytearray),
    ):
        raise RootProjectionError("plugin required-slot plan is malformed")
    normalized = tuple(required_slots)
    if any(not isinstance(slot, Mapping) for slot in normalized):
        raise RootProjectionError("plugin required-slot plan is malformed")
    return normalized


def _required_slot_count(assembly: RootAssembly) -> int:
    return len(_required_slots(assembly))


def _natural_rejection_stage(
    *,
    events: Sequence[Any],
    protocol_result: ProtocolRunResult,
) -> str:
    root_unit_id = protocol_result.root_unit_id
    if protocol_result.status != "failed" or not isinstance(root_unit_id, str):
        raise RootProjectionError(
            "missing merge is only projectable for a ledger-confirmed failed root"
        )
    root_failures = []
    for event in events:
        if event.event_type != EventType.TASK_UNIT_STATE_CHANGED:
            continue
        task_unit = event.payload.get("task_unit")
        state_change = event.payload.get("task_unit_state_change")
        if (
            isinstance(task_unit, Mapping)
            and task_unit.get("unit_id") == root_unit_id
            and task_unit.get("state") == "Failed"
            and isinstance(state_change, Mapping)
            and state_change.get("unit_id") == root_unit_id
            and state_change.get("new_state") == "Failed"
            and state_change.get("trigger") == "terminal_child_failure"
        ):
            root_failures.append((event, state_change))
    if len(root_failures) != 1:
        raise RootProjectionError(
            "failed result has no unique root terminal-failure ledger fact"
        )
    root_failure_event, state_change = root_failures[0]
    state_context = state_change.get("state_context")
    failed_child_unit_id = (
        state_context.get("failed_child_unit_id")
        if isinstance(state_context, Mapping)
        else None
    )
    if not isinstance(failed_child_unit_id, str):
        raise RootProjectionError("root failure does not identify its failed child")

    child_failures = []
    for event in events:
        if (
            event.event_type != EventType.TASK_UNIT_STATE_CHANGED
            or event.event_id != root_failure_event.causation_event_id
            or event.object_id != failed_child_unit_id
        ):
            continue
        task_unit = event.payload.get("task_unit")
        state_change = event.payload.get("task_unit_state_change")
        if (
            isinstance(task_unit, Mapping)
            and task_unit.get("unit_id") == failed_child_unit_id
            and task_unit.get("state") == "Failed"
            and isinstance(state_change, Mapping)
            and state_change.get("unit_id") == failed_child_unit_id
            and state_change.get("new_state") == "Failed"
        ):
            child_failures.append(event)
    if len(child_failures) != 1:
        raise RootProjectionError(
            "root terminal failure has no causally bound failed-child fact"
        )
    child_failure_event = child_failures[0]

    rejected = []
    for event in events:
        if (
            event.event_seq >= child_failure_event.event_seq
            or event.event_type != EventType.VERIFICATION_RECORDED
            or event.payload.get("unit_id") != failed_child_unit_id
            or event.payload.get("status") != "rejected"
            or event.payload.get("eligible_for_canonical") is not False
        ):
            continue
        report = event.payload.get("verification_report")
        if isinstance(report, Mapping) and report.get("status") == "rejected":
            rejected.append(event)
    if not rejected:
        raise RootProjectionError(
            "failed root has no matching verification/checker rejection fact"
        )

    unit_type = None
    for event in events:
        if event.event_type != EventType.TASK_UNIT_CREATED:
            continue
        task_unit = event.payload.get("task_unit")
        if (
            isinstance(task_unit, Mapping)
            and task_unit.get("unit_id") == failed_child_unit_id
        ):
            unit_type = task_unit.get("unit_type")
            break
    return "domain_root_recheck" if unit_type == "merge" else "child_verification"


def _domain_result(
    *,
    inventory: RootInventoryV1,
    assembly: RootAssembly,
    final_output_ids: set[str],
    final_output_refs: Any,
) -> tuple[bool, bool]:
    if inventory.domain == "factorization":
        return _factorization_result(
            assembly=assembly,
            final_output_ids=final_output_ids,
        )
    if inventory.domain == "lean":
        return _lean_result(
            assembly=assembly,
            final_output_ids=final_output_ids,
            final_output_refs=final_output_refs,
        )
    raise RootProjectionError(f"unsupported Task 1 domain {inventory.domain!r}")


def _factorization_result(
    *,
    assembly: RootAssembly,
    final_output_ids: set[str],
) -> tuple[bool, bool]:
    adapter = assembly.plugin_runtime
    if not isinstance(adapter, FactorizationRuntimeAdapter):
        raise RootProjectionError("Factorization inventory has the wrong plugin runtime")
    prime_ref = adapter.merge_candidate_refs.get(
        REQUESTED_OUTPUT_PRIME_FACTORIZATION
    )
    if prime_ref is None or prime_ref.artifact_id not in final_output_ids:
        raise RootProjectionError("Factorization final prime artifact is not canonical")
    try:
        body = json.loads(assembly.artifact_store.read_bytes(prime_ref))
        factors = body["prime_factors"]
        target_n = int(assembly.root_input["target_n"])
        product = 1
        for factor in factors:
            product *= int(factor["prime"]) ** int(factor["exponent"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RootProjectionError("Factorization final prime artifact is malformed") from exc
    final_present = True
    verified = (
        final_present
        and body.get("product_check_passed") is True
        and product == target_n
    )
    return final_present, verified


def _lean_result(
    *,
    assembly: RootAssembly,
    final_output_ids: set[str],
    final_output_refs: Any,
) -> tuple[bool, bool]:
    adapter = assembly.plugin_runtime
    if not isinstance(adapter, LeanRuntimeAdapter):
        raise RootProjectionError("Lean inventory has the wrong plugin runtime")
    try:
        merge_result = adapter.merge_result
    except RuntimeError as exc:
        raise RootProjectionError("Lean public merge result is unavailable") from exc
    root_ref = merge_result.root_proof_artifact_ref
    if root_ref is None:
        raise RootProjectionError("Lean merge has no root proof artifact")
    if not isinstance(final_output_refs, Mapping):
        raise RootProjectionError("Lean merge output refs are unavailable")
    promoted_body = final_output_refs.get(PROOF_ARTIFACT_OUTPUT_NAME)
    if not isinstance(promoted_body, Mapping):
        raise RootProjectionError("Lean canonical root proof output is missing")
    try:
        promoted_ref = ArtifactRef.from_dict(dict(promoted_body))
        proof_source = assembly.artifact_store.read_bytes(root_ref)
        canonical_source = assembly.artifact_store.read_bytes(promoted_ref)
    except (KeyError, TypeError, ValueError) as exc:
        raise RootProjectionError("Lean root proof artifact is unreadable") from exc
    if (
        not proof_source
        or promoted_ref.artifact_id not in final_output_ids
        or promoted_ref.content_hash != root_ref.content_hash
        or canonical_source != proof_source
    ):
        raise RootProjectionError(
            "Lean root checker proof does not match the canonical root proof"
        )
    final_present = True
    verified = (
        final_present
        and merge_result.accepted is True
        and merge_result.root_checker_report.status == LeanCheckerStatus.ACCEPTED
    )
    return final_present, verified


def _worker_facts(value: Any) -> list[WorkerExecutionFactV1]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RootProjectionError("worker_execution_facts must be a sequence")
    projected: list[WorkerExecutionFactV1] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RootProjectionError("worker execution fact must be an object")
        worker_id = item.get("worker_id")
        result_kind = item.get("result_kind")
        if not isinstance(worker_id, str) or not isinstance(result_kind, str):
            raise RootProjectionError("worker execution fact identity is incomplete")
        projected.append(
            WorkerExecutionFactV1(
                worker_id=worker_id,
                started_at_ms=_timestamp_ms(item.get("started_at"), "started_at"),
                ended_at_ms=_timestamp_ms(item.get("ended_at"), "ended_at"),
                result_kind=result_kind,
            )
        )
    return projected


def _artifact_ids(value: Any, field_name: str) -> set[str]:
    if not isinstance(value, Mapping) or not value:
        raise RootProjectionError(f"{field_name} must be a non-empty object")
    result: set[str] = set()
    for ref in value.values():
        if not isinstance(ref, Mapping) or not isinstance(ref.get("artifact_id"), str):
            raise RootProjectionError(f"{field_name} contains an invalid artifact ref")
        result.add(ref["artifact_id"])
    if len(result) != len(value):
        raise RootProjectionError(f"{field_name} contains duplicate artifact IDs")
    return result


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RootProjectionError(f"{field_name} must be a sequence")
    normalized = list(value)
    if any(not isinstance(item, str) or not item for item in normalized):
        raise RootProjectionError(f"{field_name} must contain non-empty strings")
    if len(normalized) != len(set(normalized)):
        raise RootProjectionError(f"{field_name} must contain unique values")
    return normalized


def _timestamp_ms(value: Any, field_name: str) -> int:
    if not isinstance(value, str):
        raise RootProjectionError(f"{field_name} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RootProjectionError(f"{field_name} is not an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise RootProjectionError(f"{field_name} must include a timezone")
    return int(round(parsed.timestamp() * 1000))


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RootProjectionError(f"{field_name} must be a non-negative integer")
    return value


def _null_reasons(values: Mapping[str, Any]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for item in fields(RootResultV1):
        if item.metadata.get("nullable") is not True or values.get(item.name) is not None:
            continue
        if item.name in {"trace_tail_started_at_ms", "trace_tail_terminal_at_ms"}:
            reasons[item.name] = "trace_tail_not_needed"
        elif item.name in {"failure_stage", "failure_kind"}:
            reasons[item.name] = "root_verified_correct"
        elif item.name == "topic_family":
            reasons[item.name] = "not_applicable_to_factorization"
        else:
            reasons[item.name] = "not_applicable_to_exp1_task1_projection"
    return reasons


__all__ = ["RootProjectionError", "project_root_result"]
