"""Slim V2 单 root 只读投影。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
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

from .runtime import RootAssembly, TailSummaryV1, coverage_tail_blocked
from .schema import (
    AblationObservationV1,
    AttemptResultV1,
    ChallengeObservationV1,
    FaultObservationV1,
    RecoveryObservationV1,
    RootInventoryV1,
    RootResultV2,
    WorkerDeathObservationV1,
    WorkerExecutionFactV1,
)


class RootProjectionError(RuntimeError):
    """当前 root 的公共事实不足或彼此矛盾。"""


def project_root_result(
    *,
    inventory: RootInventoryV1,
    assembly: RootAssembly,
    protocol_result: ProtocolRunResult,
    provider_family: str,
    requested_model: str,
    resolved_model: str | None,
    reasoning_mode: str,
    attempts: Sequence[AttemptResultV1] | None = None,
    tail_summary: TailSummaryV1 | None = None,
    scenario: object | None = None,
) -> RootResultV2:
    """只读 join 当前 result、ledger/store 与插件领域事实。"""

    inventory.validate()
    if inventory.experiment_id not in {"exp1", "exp2", "exp3", "exp4", "exp5"}:
        raise RootProjectionError("unsupported Slim V2 experiment")
    if inventory.experiment_id in {"exp2", "exp3", "exp4"} and scenario is None:
        raise RootProjectionError("system-scenario projection requires scenario evidence")
    if protocol_result.run_id != assembly.run_id:
        raise RootProjectionError("protocol result run_id does not match assembly")
    observation = protocol_result.summary.get("runtime_observation")
    if not isinstance(observation, Mapping):
        raise RootProjectionError("protocol result is missing runtime_observation")
    if observation.get("run_id") != assembly.run_id:
        raise RootProjectionError("runtime observation run_id does not match assembly")
    condition_failure = protocol_result.summary.get("slim_condition_failure")
    runtime_failure = protocol_result.summary.get("slim_runtime_failure")
    terminal_failure = protocol_result.summary.get("terminal_failure")
    if condition_failure is not None:
        if not isinstance(condition_failure, Mapping):
            raise RootProjectionError("Slim condition failure must be an object")
        if (
            condition_failure.get("failure_stage") != "provider_call"
            or condition_failure.get("failure_kind")
            not in {
                "provider_configuration_invalid",
                "provider_journal_conflict",
                "provider_model_mismatch",
            }
        ):
            raise RootProjectionError("Slim condition failure identity is invalid")
    if runtime_failure is not None:
        if (
            not isinstance(runtime_failure, Mapping)
            or runtime_failure.get("failure_stage") != "protocol_runtime"
            or not isinstance(runtime_failure.get("failure_kind"), str)
            or not isinstance(runtime_failure.get("engine_root_status"), str)
        ):
            raise RootProjectionError("Slim runtime failure identity is invalid")
    if terminal_failure is not None:
        if (
            not isinstance(terminal_failure, Mapping)
            or set(terminal_failure) != {
                "failure_stage",
                "failure_origin",
                "infrastructure_invalid",
            }
            or terminal_failure.get("failure_stage")
            not in {
                "candidate_acquisition",
                "candidate_verification",
                "child_execution",
                "preflight",
            }
            or terminal_failure.get("failure_origin")
            not in {
                "model_parse_exhausted",
                "model_verification_exhausted",
                "provider_transport_exhausted",
                "mixed_candidate_acquisition_failure",
                "worker_death_exhausted",
                "checker_environment_error",
                "unexpected_runtime_error",
            }
            or not isinstance(terminal_failure.get("infrastructure_invalid"), bool)
        ):
            raise RootProjectionError("protocol terminal failure identity is invalid")
        if (
            terminal_failure["failure_origin"]
            in {"checker_environment_error", "unexpected_runtime_error"}
        ) != terminal_failure["infrastructure_invalid"]:
            raise RootProjectionError("protocol terminal failure class is inconsistent")

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
        if terminal_failure is not None:
            raise RootProjectionError("terminal failure cannot carry a merged root")
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
            failure_origin = None
        else:
            failure_stage = "domain_root_recheck"
            failure_kind = "incorrect_final"
            failure_origin = None
    else:
        if condition_failure is not None:
            if protocol_result.status != "failed":
                raise RootProjectionError("condition failure requires a failed protocol root")
            failure_stage = str(condition_failure["failure_stage"])
            failure_kind = "infrastructure_invalid"
            failure_origin = str(condition_failure["failure_kind"])
        elif runtime_failure is not None:
            failure_stage = str(runtime_failure["failure_stage"])
            failure_kind = "infrastructure_invalid"
            failure_origin = "unexpected_runtime_error"
        elif terminal_failure is not None:
            if protocol_result.status != "failed":
                raise RootProjectionError("terminal failure requires a failed protocol root")
            if not terminal_failure["infrastructure_invalid"]:
                _natural_rejection_stage(
                    events=events,
                    protocol_result=protocol_result,
                    worker_facts=observation.get("worker_execution_facts"),
                )
            failure_stage = str(terminal_failure["failure_stage"])
            failure_kind = (
                "infrastructure_invalid"
                if terminal_failure["infrastructure_invalid"]
                else "no_final"
            )
            failure_origin = str(terminal_failure["failure_origin"])
        elif inventory.experiment_id == "exp4" and protocol_result.status == "processing":
            failure_stage, failure_kind = _exp4_processing_failure(scenario)
            if failure_kind not in {"no_final", "infrastructure_invalid"}:
                failure_origin = failure_kind
                failure_kind = "no_final"
            elif failure_kind == "no_final":
                failure_origin = "premature_merge_no_final"
            else:
                failure_origin = "exp4_processing_infrastructure_invalid"
        else:
            failure_stage = _natural_rejection_stage(
                events=events,
                protocol_result=protocol_result,
                worker_facts=observation.get("worker_execution_facts"),
            )
            failure_kind = "no_final"
            failure_origin = "ledger_confirmed_natural_rejection"
        required_slot_count, recovered_slot_count = _failed_slot_counts(
            assembly=assembly,
            events=events,
        )
        final_result_present = False
        verified_correct = False

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
    if inventory.experiment_id in {"exp2", "exp3", "exp4"}:
        scheduler = getattr(scenario, "logical_scheduler", None)
        logical_makespan = getattr(scheduler, "clock_ms", None)
        if (
            isinstance(logical_makespan, bool)
            or not isinstance(logical_makespan, int)
            or logical_makespan < 0
        ):
            raise RootProjectionError("system scenario lacks logical makespan evidence")
        runtime_wall_clock_ms = logical_makespan
        root_terminal_at_ms = root_start_at_ms + logical_makespan

    planned_ai_unit_ids = _string_list(
        observation.get("planned_ai_unit_ids"),
        "planned_ai_unit_ids",
    )
    dispatched_ai_unit_ids = _string_list(
        observation.get("dispatched_ai_unit_ids"),
        "dispatched_ai_unit_ids",
    )
    completed_ai_unit_ids = _string_list(
        observation.get("completed_ai_unit_ids"),
        "completed_ai_unit_ids",
    )
    unscheduled_ai_unit_ids = _string_list(
        observation.get("unscheduled_ai_unit_ids"),
        "unscheduled_ai_unit_ids",
    )
    if inventory.experiment_id == "exp1":
        acquisition_failure = coverage_tail_blocked(protocol_result.summary)
        if acquisition_failure and tail_summary is not None:
            raise RootProjectionError("acquisition-failed Exp1 root cannot carry a coverage tail")
        if not acquisition_failure and unscheduled_ai_unit_ids and tail_summary is None:
            raise RootProjectionError(
                "Exp1 unscheduled AI units require a completed coverage-tail summary"
            )
        tail = tail_summary or TailSummaryV1(
            None, None, 0, "not_needed", [], [], 0, 0, 0, 0, 0.0
        )
        if (
            not acquisition_failure
            and tail.trace_tail_status != "not_required_by_downstream"
            and tail.trace_tail_target_ai_unit_ids != unscheduled_ai_unit_ids
        ):
            raise RootProjectionError(
                "Exp1 tail targets must exactly equal protocol unscheduled AI units"
            )
        tail_values: dict[str, Any] = {
            item.name: getattr(tail, item.name) for item in fields(TailSummaryV1)
        }
    else:
        if tail_summary is not None:
            raise RootProjectionError("Exp5 cannot carry a coverage tail")
        tail_values = {
            "trace_tail_started_at_ms": None,
            "trace_tail_terminal_at_ms": None,
            "trace_tail_wall_clock_ms": None,
            "trace_tail_status": None,
            "trace_tail_target_ai_unit_ids": None,
            "trace_tail_recorded_ai_unit_ids": None,
            "trace_tail_success_unit_count": None,
            "trace_tail_failure_unit_count": None,
            "trace_tail_provider_attempt_count": None,
            "trace_tail_total_tokens": None,
            "trace_tail_cost_estimate_cny": None,
        }

    canonical_attempt_ids = {
        attempt_id
        for event in events
        if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and isinstance((attempt_id := event.payload.get("selected_attempt_id")), str)
    }
    verification_status = {
        attempt_id: status
        for event in events
        if event.event_type == EventType.VERIFICATION_RECORDED
        and isinstance((attempt_id := event.payload.get("attempt_id")), str)
        and isinstance((status := event.payload.get("status")), str)
    }
    lean_checker_status = (
        _lean_attempt_checker_status(
            events=events,
            adapter=assembly.plugin_runtime,
        )
        if inventory.domain == "lean"
        else {}
    )
    projected_attempts = []
    fact_field = "verifier_result" if inventory.domain == "factorization" else "checker_result"
    reason_key = f"attempts[].{fact_field}"
    for attempt in attempts or ():
        attempt_id = str(attempt.attempt_id)
        status = (
            verification_status.get(attempt_id)
            if inventory.domain == "factorization"
            else lean_checker_status.get(attempt_id, "not_reached")
        )
        projected = replace(
            attempt,
            canonical_accepted=attempt.attempt_id in canonical_attempt_ids,
            **({fact_field: status} if status is not None else {}),
        )
        if status is not None:
            projected = replace(
                projected,
                missing_reason={
                    key: value
                    for key, value in projected.missing_reason.items()
                    if key != reason_key
                },
            )
        projected_attempts.append(projected)
    for attempt in projected_attempts:
        attempt.validate(experiment_id=str(inventory.experiment_id))
    projected_attempts, recovery_observations = _recovery_projection(
        attempts=projected_attempts,
        events=events,
        canonical_attempt_ids=canonical_attempt_ids,
        verification_status=verification_status,
    )
    fault_targets = (
        list(getattr(scenario, "fault_target_planned_ai_unit_ids", ()))
        if inventory.experiment_id == "exp3" and inventory.fault_type is not None
        else None
    )
    fault_observations = _fault_projection(
        scenario=scenario,
        attempts=projected_attempts,
        verification_status=verification_status,
        canonical_attempt_ids=canonical_attempt_ids,
    )
    worker_death_observations = _worker_death_projection(
        worker_facts=observation.get("worker_execution_facts"),
        recoveries=recovery_observations,
    )
    challenge_observations = _challenge_projection(
        scenario=scenario,
        assembly=assembly,
        events=events,
        verification_status=verification_status,
        canonical_attempt_ids=canonical_attempt_ids,
        recoveries=recovery_observations,
        verified_correct=verified_correct,
    )
    ablation_observations, ablation_missing_reasons = _ablation_projection(
        inventory=inventory,
        scenario=scenario,
        challenges=challenge_observations,
        verified_correct=verified_correct,
        final_result_present=final_result_present,
        events=events,
        recoveries=recovery_observations,
        assembly=assembly,
    )
    challenge_plan = getattr(scenario, "challenge_plan", None)
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
        "challenge_family": getattr(challenge_plan, "challenge_family", None),
        "challenge_target_planned_ai_unit_ids": (
            list(challenge_plan.target_planned_ai_unit_ids)
            if challenge_plan is not None
            else None
        ),
        "challenge_attempt_ordinal_rule": getattr(
            challenge_plan, "attempt_rule", None
        ),
        "provider_family": provider_family,
        "provider_entry_id": inventory.provider_entry_id,
        "configured_model": inventory.configured_model,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "reasoning_mode": reasoning_mode,
        "root_start_at_ms": root_start_at_ms,
        "root_terminal_at_ms": root_terminal_at_ms,
        "runtime_wall_clock_ms": runtime_wall_clock_ms,
        **tail_values,
        "preflight_status": "passed",
        "protocol_started": True,
        "root_status": protocol_result.status,
        "final_result_present": final_result_present,
        "verified_correct": verified_correct,
        "failure_stage": failure_stage,
        "failure_kind": failure_kind,
        "failure_origin": failure_origin,
        "planned_ai_unit_ids": planned_ai_unit_ids,
        "dispatched_ai_unit_ids": dispatched_ai_unit_ids,
        "completed_ai_unit_ids": completed_ai_unit_ids,
        "unscheduled_ai_unit_ids": unscheduled_ai_unit_ids,
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
        "attempts": projected_attempts,
        "fault_target_planned_ai_unit_ids": fault_targets,
        "fault_target_count": len(fault_targets) if fault_targets is not None else None,
        "fault_observations": fault_observations,
        "recovery_observations": recovery_observations,
        "worker_death_observations": worker_death_observations,
        "challenge_observations": challenge_observations,
        "ablation_observations": ablation_observations,
        "missing_reason": {
            **(
                {"resolved_model": "model_resolution_not_reached"}
                if resolved_model is None
                else {}
            ),
            **ablation_missing_reasons,
        },
        "not_applicable_reason": {},
    }
    values["not_applicable_reason"] = _null_reasons(values)
    for field_name in values["missing_reason"]:
        values["not_applicable_reason"].pop(field_name, None)
    projected = RootResultV2(**values)
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
    worker_facts: Any,
) -> str:
    root_unit_id = protocol_result.root_unit_id
    if protocol_result.status != "failed" or not isinstance(root_unit_id, str):
        raise RootProjectionError(
            "missing merge is only projectable for a ledger-confirmed failed root "
            f"(status={protocol_result.status!r})"
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

    terminal_recoveries = []
    for event in events:
        if (
            event.event_type != EventType.RECOVERY_ACTION_RECORDED
            or event.event_id != child_failure_event.causation_event_id
        ):
            continue
        recovery = event.payload.get("recovery_action")
        if (
            isinstance(recovery, Mapping)
            and recovery.get("unit_id") == failed_child_unit_id
            and recovery.get("retry_allowed") is False
            and recovery.get("new_task_state") == "Failed"
        ):
            terminal_recoveries.append((event, recovery))
    if len(terminal_recoveries) != 1:
        raise RootProjectionError(
            "failed child has no unique causally bound terminal recovery fact"
        )
    recovery_event, recovery = terminal_recoveries[0]
    recovery_attempt_id = recovery.get("attempt_id")
    recovery_trigger = recovery.get("trigger")
    if not isinstance(recovery_attempt_id, str) or not isinstance(
        recovery_trigger, str
    ):
        raise RootProjectionError("terminal recovery identity is incomplete")

    if recovery_trigger != "verification_rejected":
        normalized_worker_facts = (
            worker_facts
            if isinstance(worker_facts, Sequence)
            and not isinstance(worker_facts, (str, bytes, bytearray))
            else ()
        )
        death_facts = [
            fact
            for fact in normalized_worker_facts
            if isinstance(fact, Mapping)
            and fact.get("unit_id") == failed_child_unit_id
            and fact.get("attempt_id") == recovery_attempt_id
            and fact.get("result_kind") == "worker_terminated"
            and isinstance(fact.get("worker_id"), str)
            and isinstance(fact.get("worker_pid"), int)
            and not isinstance(fact.get("worker_pid"), bool)
            and isinstance(fact.get("process_exitcode"), int)
            and not isinstance(fact.get("process_exitcode"), bool)
            and isinstance(fact.get("kill_progress_actual_ratio"), (int, float))
            and not isinstance(fact.get("kill_progress_actual_ratio"), bool)
        ]
        if len(death_facts) > 1:
            raise RootProjectionError(
                "execution recovery has duplicate matching worker death facts"
            )
        if death_facts:
            return "child_execution"

        late_rejected_submissions = [
            event
            for event in events
            if event.event_seq < recovery_event.event_seq
            and event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
            and event.payload.get("unit_id") == failed_child_unit_id
            and event.payload.get("attempt_id") == recovery_attempt_id
            and event.payload.get("acceptance_status") == "rejected"
            and event.payload.get("result_kind") == "succeeded"
            and event.payload.get("rejection_reason") == "lease_deadline_exceeded"
        ]
        if recovery_trigger == "lease_expired" and len(late_rejected_submissions) == 1:
            return "child_execution"

        failed_submissions = [
            event
            for event in events
            if event.event_seq < recovery_event.event_seq
            and event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
            and event.payload.get("unit_id") == failed_child_unit_id
            and event.payload.get("attempt_id") == recovery_attempt_id
            and event.payload.get("acceptance_status") == "accepted"
            and isinstance(event.payload.get("result_kind"), str)
            and event.payload.get("result_kind") != "succeeded"
        ]
        if len(failed_submissions) != 1:
            raise RootProjectionError(
                "execution recovery has no unique matching failed submission fact"
            )
        return "child_execution"

    rejected = []
    for event in events:
        if (
            event.event_seq >= child_failure_event.event_seq
            or event.event_type != EventType.VERIFICATION_RECORDED
            or event.payload.get("unit_id") != failed_child_unit_id
            or event.payload.get("attempt_id") != recovery_attempt_id
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


def _exp4_processing_failure(scenario: object | None) -> tuple[str, str]:
    """只从实际 recovery/premerge route 解释实验性 no-final。"""

    observer = getattr(scenario, "route_observer", None)
    premature = getattr(observer, "premature_merge_observations", ())
    if premature:
        latest = premature[-1]
        return str(latest.failure_stage or "no_final"), "no_final"
    records = getattr(observer, "recovery_records", ())
    if any(
        isinstance(item, Mapping)
        and item.get("route_status") == "applied"
        and item.get("stuck_due_to_no_requeue") is True
        for item in records
    ):
        return "requeue", "replacement_disabled_with_pending_root"
    raise RootProjectionError(
        "Exp4 processing root lacks an observed disabled-recovery boundary"
    )


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


def _recovery_projection(
    *,
    attempts: Sequence[AttemptResultV1],
    events: Sequence[Any],
    canonical_attempt_ids: set[str],
    verification_status: Mapping[str, str],
) -> tuple[list[AttemptResultV1], list[RecoveryObservationV1]]:
    """只把ledger已经产生且确有后继attempt的恢复动作连到结果。"""

    projected = list(attempts)
    by_id = {str(item.attempt_id): item for item in projected}
    by_unit: dict[str, list[AttemptResultV1]] = {}
    for item in projected:
        by_unit.setdefault(str(item.unit_id), []).append(item)
    for values in by_unit.values():
        values.sort(key=lambda item: (int(item.attempt_ordinal), str(item.attempt_id)))

    observations: list[RecoveryObservationV1] = []
    updates: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.event_type != EventType.RECOVERY_ACTION_RECORDED:
            continue
        action = event.payload.get("recovery_action")
        if not isinstance(action, Mapping) or action.get("retry_allowed") is not True:
            continue
        original_id = action.get("attempt_id")
        unit_id = action.get("unit_id")
        trigger = action.get("trigger")
        if not all(isinstance(item, str) and item for item in (original_id, unit_id, trigger)):
            raise RootProjectionError("recovery action identity is incomplete")
        original = by_id.get(original_id)
        retry_count = action.get("retry_count")
        replacements = [
            item
            for item in by_unit.get(unit_id, ())
            if (
                int(item.attempt_ordinal) > int(original.attempt_ordinal)
                if original is not None
                else isinstance(retry_count, int)
                and not isinstance(retry_count, bool)
                and int(item.attempt_ordinal) >= retry_count
            )
        ]
        if not replacements:
            continue
        replacement_attempt = replacements[0]
        replacement_id = str(replacement_attempt.attempt_id)
        updates[replacement_id] = (original_id, trigger)
        succeeded = (
            replacement_id in canonical_attempt_ids
            or verification_status.get(replacement_id) in {"accepted", "passed"}
        )
        observations.append(
            RecoveryObservationV1(
                original_attempt_id=original_id,
                replacement_attempt_id=replacement_id,
                replacement_started=True,
                replacement_succeeded=succeeded,
                reassigned=True,
                fault_at_ms=None,
                replacement_started_at_ms=replacement_attempt.started_at_ms,
                replacement_ended_at_ms=replacement_attempt.ended_at_ms,
            )
        )
    for index, item in enumerate(projected):
        linkage = updates.get(str(item.attempt_id))
        if linkage is None:
            continue
        original_id, trigger = linkage
        projected[index] = replace(
            item,
            replacement_of_attempt_id=original_id,
            recovery_trigger=trigger,
            missing_reason={
                key: reason
                for key, reason in item.missing_reason.items()
                if key
                not in {
                    "attempts[].replacement_of_attempt_id",
                    "attempts[].recovery_trigger",
                }
            },
        )
    return projected, observations


def _lean_attempt_checker_status(
    *,
    events: Sequence[Any],
    adapter: object,
) -> dict[str, str]:
    """按 request 关联公开 checker report，不从粗粒度验证事件推断。"""

    if not isinstance(adapter, LeanRuntimeAdapter):
        raise RootProjectionError("Lean inventory has the wrong plugin runtime")
    request_ids = {
        attempt_id: request_id
        for event in events
        if event.event_type == EventType.EXECUTION_REQUEST_RECORDED
        and isinstance((attempt_id := event.payload.get("attempt_id")), str)
        and isinstance((request_id := event.payload.get("request_id")), str)
    }
    projected: dict[str, str] = {}
    status_mapping = {
        LeanCheckerStatus.ACCEPTED: "accepted",
        LeanCheckerStatus.REJECTED: "proof_rejected",
        LeanCheckerStatus.ENVIRONMENT_ERROR: "environment_error",
        LeanCheckerStatus.TIMEOUT: "timeout",
        LeanCheckerStatus.HELPER_ERROR: "helper_error",
    }
    for attempt_id, request_id in request_ids.items():
        report = adapter.checker_report_for_request(request_id)
        if report is None:
            projected[attempt_id] = "not_reached"
            continue
        try:
            checker_status = LeanCheckerStatus(report.status)
            projected[attempt_id] = status_mapping[checker_status]
        except (KeyError, TypeError, ValueError) as exc:
            raise RootProjectionError(
                f"unsupported Lean checker status for request {request_id}"
            ) from exc
    return projected


def _fault_projection(
    *,
    scenario: object | None,
    attempts: Sequence[AttemptResultV1],
    verification_status: Mapping[str, str],
    canonical_attempt_ids: set[str],
) -> list[FaultObservationV1]:
    hooks = getattr(scenario, "hooks", None)
    records = getattr(hooks, "injection_records", ())
    if not isinstance(records, Sequence):
        raise RootProjectionError("scenario injection records are malformed")
    attempt_by_id = {str(item.attempt_id): item for item in attempts}
    projected: list[FaultObservationV1] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise RootProjectionError("scenario injection record must be an object")
        fault_type = record.get("kind")
        if fault_type not in {
            "false_positive",
            "false_negative",
            "no_return",
            "late_submission",
            "executor_error",
        }:
            continue
        attempt_id = record.get("attempt_id")
        planned = record.get("planned_ai_unit_id")
        if not isinstance(attempt_id, str) or not isinstance(planned, str):
            raise RootProjectionError("fault injection identity is incomplete")
        attempt = attempt_by_id.get(attempt_id)
        status = verification_status.get(attempt_id)
        canonical = attempt_id in canonical_attempt_ids
        projected.append(
            FaultObservationV1(
                attempt_id=attempt_id,
                fault_type=fault_type,
                target_planned_ai_unit_id=planned,
                injected=record.get("injected") is True,
                reached_verification=status is not None,
                independently_wrong=(
                    record.get("independently_wrong")
                    if isinstance(record.get("independently_wrong"), bool)
                    else None
                ),
                verifier_intercepted=status == "rejected" if status is not None else None,
                escaped_to_canonical_or_root=canonical,
                discarded_total_tokens=(
                    0
                    if canonical
                    else attempt.simulated_total_tokens
                    if attempt is not None
                    else None
                ),
            )
        )
    return projected


def _worker_death_projection(
    *,
    worker_facts: Any,
    recoveries: Sequence[RecoveryObservationV1],
) -> list[WorkerDeathObservationV1]:
    if not isinstance(worker_facts, Sequence) or isinstance(
        worker_facts, (str, bytes, bytearray)
    ):
        raise RootProjectionError("worker_execution_facts must be a sequence")
    replacement_by_original = {
        str(item.original_attempt_id): str(item.replacement_attempt_id)
        for item in recoveries
    }
    observations: list[WorkerDeathObservationV1] = []
    for fact in worker_facts:
        if not isinstance(fact, Mapping):
            raise RootProjectionError("worker execution fact must be an object")
        if fact.get("result_kind") != "worker_terminated":
            continue
        worker_id = fact.get("worker_id")
        pid = fact.get("worker_pid")
        exit_code = fact.get("process_exitcode")
        target_ratio = fact.get("kill_progress_target_ratio")
        actual_ratio = fact.get("kill_progress_actual_ratio")
        original_attempt_id = fact.get("attempt_id")
        if not isinstance(worker_id, str) or not worker_id:
            raise RootProjectionError("worker death fact lacks worker identity")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
        ):
            raise RootProjectionError("worker death fact lacks PID/exit evidence")
        if (
            isinstance(target_ratio, bool)
            or not isinstance(target_ratio, (int, float))
            or isinstance(actual_ratio, bool)
            or not isinstance(actual_ratio, (int, float))
        ):
            raise RootProjectionError("worker death fact lacks progress evidence")
        original = (
            original_attempt_id
            if isinstance(original_attempt_id, str) and original_attempt_id
            else None
        )
        observations.append(
            WorkerDeathObservationV1(
                worker_id=worker_id,
                pid=pid,
                exit_code=exit_code,
                target_progress_ratio=float(target_ratio),
                actual_progress_ratio=float(actual_ratio),
                original_attempt_id=original,
                replacement_attempt_id=(
                    replacement_by_original.get(original) if original is not None else None
                ),
            )
        )
    return observations


def _challenge_projection(
    *,
    scenario: object | None,
    assembly: RootAssembly,
    events: Sequence[Any],
    verification_status: Mapping[str, str],
    canonical_attempt_ids: set[str],
    recoveries: Sequence[RecoveryObservationV1],
    verified_correct: bool,
) -> list[ChallengeObservationV1]:
    plan = getattr(scenario, "challenge_plan", None)
    if plan is None:
        return []
    controller = getattr(scenario, "challenge_controller", None)
    records = list(getattr(controller, "injection_records", ()))
    for event in events:
        if event.event_type != EventType.EXECUTION_SUBMISSION_RECORDED:
            continue
        submission_ref = event.payload.get("submission_ref")
        if not isinstance(submission_ref, Mapping):
            continue
        try:
            submission = json.loads(
                assembly.artifact_store.read_bytes(
                    ArtifactRef.from_dict(dict(submission_ref))
                ).decode("utf-8")
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RootProjectionError(
                "challenge submission evidence is unreadable"
            ) from exc
        summary = submission.get("environment_summary")
        actual_records = (
            summary.get("slim_challenge_observations", ())
            if isinstance(summary, Mapping)
            else ()
        )
        if not isinstance(actual_records, Sequence) or isinstance(
            actual_records, (str, bytes, bytearray)
        ):
            raise RootProjectionError("challenge submission observations are malformed")
        for record in actual_records:
            if record not in records:
                records.append(record)
    if not records:
        raise RootProjectionError(
            "Exp4 challenge route lacks persisted actual injection evidence"
        )
    replacement_by_original = {
        str(item.original_attempt_id): item for item in recoveries
    }
    observations: list[ChallengeObservationV1] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise RootProjectionError("scenario injection record must be an object")
        family = record.get("kind")
        if family not in {
            "INVALID_PARSED_CANDIDATE",
            "PARSER_REQUIRED_CANONICAL_JSON",
            "RECOVERABLE_NO_RETURN",
            "REQUIRED_CHILD_DELAY",
        }:
            continue
        attempt_id = record.get("attempt_id")
        planned = record.get("planned_ai_unit_id")
        ordinal = record.get("attempt_ordinal")
        boundary = record.get("boundary")
        if (
            not isinstance(attempt_id, str)
            or not isinstance(planned, str)
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not isinstance(boundary, str)
        ):
            raise RootProjectionError("challenge injection identity is incomplete")
        status = verification_status.get(attempt_id)
        recovery = replacement_by_original.get(attempt_id)
        independently_wrong = record.get("independently_wrong")
        observations.append(
            ChallengeObservationV1(
                challenge_plan_id=str(plan.challenge_plan_id),
                challenge_family=family,
                target_planned_ai_unit_id=planned,
                attempt_ordinal=ordinal,
                injection_boundary=boundary,
                opportunity=record.get("opportunity") is True,
                injected=record.get("injected") is True,
                source_semantics_preserved=(
                    record.get("source_semantics_preserved")
                    if isinstance(record.get("source_semantics_preserved"), bool)
                    else None
                ),
                candidate_independent_label=(
                    "invalid"
                    if independently_wrong is True
                    else "valid"
                    if independently_wrong is False
                    else None
                ),
                reached_verification=status is not None,
                verifier_rejected=status == "rejected" if status is not None else None,
                escaped_to_canonical_or_root=attempt_id in canonical_attempt_ids,
                replacement_started=(
                    recovery.replacement_started if recovery is not None else False
                ),
                replacement_succeeded=(
                    recovery.replacement_succeeded if recovery is not None else False
                ),
                valid_final_after_challenge=verified_correct,
            )
        )
    return observations


def _ablation_projection(
    *,
    inventory: RootInventoryV1,
    scenario: object | None,
    challenges: Sequence[ChallengeObservationV1],
    verified_correct: bool,
    final_result_present: bool,
    events: Sequence[Any],
    recoveries: Sequence[RecoveryObservationV1],
    assembly: RootAssembly,
) -> tuple[list[AblationObservationV1], dict[str, str]]:
    if inventory.experiment_id != "exp4":
        return [], {}
    observer = getattr(scenario, "route_observer", None)
    parser_records = list(getattr(observer, "parser_records", ()))
    verification_records = list(getattr(observer, "verification_records", ()))
    recovery_records = getattr(observer, "recovery_records", ())
    merge_records = getattr(observer, "merge_records", ())
    premature = getattr(observer, "premature_merge_observations", ())
    for name, records in (
        ("parser", parser_records),
        ("verification", verification_records),
        ("recovery", recovery_records),
        ("merge", merge_records),
        ("premature merge", premature),
    ):
        if not isinstance(records, Sequence) or isinstance(
            records, (str, bytes, bytearray)
        ):
            raise RootProjectionError(f"{name} route records must be a sequence")
    target_ids = set(
        getattr(getattr(scenario, "challenge_plan", None), "target_planned_ai_unit_ids", ())
    )
    persisted_challenge_attempt_ids: set[str] = set()
    for event in events:
        if event.event_type != EventType.EXECUTION_SUBMISSION_RECORDED:
            continue
        submission_ref = event.payload.get("submission_ref")
        if not isinstance(submission_ref, Mapping):
            continue
        try:
            submission = json.loads(
                assembly.artifact_store.read_bytes(
                    ArtifactRef.from_dict(dict(submission_ref))
                ).decode("utf-8")
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RootProjectionError("structural route submission is unreadable") from exc
        summary = submission.get("environment_summary")
        if not isinstance(summary, Mapping):
            continue
        parser_record = summary.get("slim_parser_route_observation")
        if isinstance(parser_record, Mapping) and parser_record not in parser_records:
            parser_records.append(parser_record)
        verification_record = summary.get(
            "slim_lean_verification_route_observation"
        )
        if (
            isinstance(verification_record, Mapping)
            and verification_record not in verification_records
        ):
            verification_records.append(verification_record)
        actual_challenges = summary.get("slim_challenge_observations", ())
        if isinstance(actual_challenges, Sequence) and not isinstance(
            actual_challenges, (str, bytes, bytearray)
        ):
            persisted_challenge_attempt_ids.update(
                str(item.get("attempt_id"))
                for item in actual_challenges
                if isinstance(item, Mapping)
                and isinstance(item.get("attempt_id"), str)
            )
    challenge_attempt_ids = persisted_challenge_attempt_ids | {
        str(item.get("attempt_id"))
        for item in getattr(
            getattr(scenario, "challenge_controller", None),
            "injection_records",
            (),
        )
        if isinstance(item, Mapping)
        and isinstance(item.get("attempt_id"), str)
        and item.get("planned_ai_unit_id") in target_ids
    }
    actual_target_attempt_ids = challenge_attempt_ids | {
        str(item.get("attempt_id"))
        for item in (*parser_records, *verification_records)
        if isinstance(item, Mapping)
        and item.get("planned_ai_unit_id") in target_ids
        and isinstance(item.get("attempt_id"), str)
    }
    verification_reports = {
        str(event.payload.get("attempt_id")): event.payload.get("verification_report")
        for event in events
        if event.event_type == EventType.VERIFICATION_RECORDED
        and isinstance(event.payload.get("attempt_id"), str)
        and isinstance(event.payload.get("verification_report"), Mapping)
    }
    invalid = [
        item for item in challenges if item.candidate_independent_label == "invalid"
    ]
    wrong_canonical = any(item.escaped_to_canonical_or_root is True for item in invalid)
    try:
        root_checker_report = getattr(
            assembly.plugin_runtime.merge_result,
            "root_checker_report",
            None,
        )
    except (AttributeError, RuntimeError):
        root_checker_report = None
    replacement_by_original = {
        str(item.original_attempt_id): str(item.replacement_attempt_id)
        for item in recoveries
    }
    observations: list[AblationObservationV1] = []
    missing_reasons: dict[str, str] = {}
    for mechanism in inventory.disabled_mechanisms:
        values: dict[str, Any] = {
            "route_status": None,
            "domain_parser_call_count": None,
            "domain_child_checker_call_count": None,
            "plugin_verify_submission_call_count": None,
            "root_checker_call_count": None,
            "candidate_independent_label": (
                invalid[0].candidate_independent_label if invalid else None
            ),
            "wrong_canonical_accepted": None,
            "root_checker_reached": None,
            "root_check_passed": None,
            "root_checker_rejected_after_wrong_canonical": None,
            "raw_only_exposed": None,
            "raw_only_accepted": None,
            "parse_result": None,
            "recovery_attempt_id": None,
            "recovery_retry_allowed": None,
            "replacement_attempt_id": None,
            "stuck_due_to_no_requeue": None,
            "merge_gate_satisfied": None,
            "required_child_unit_ids": None,
            "canonical_child_unit_ids": None,
            "missing_required_slot_ids": None,
            "plugin_merge_attempted": None,
            "plugin_outcome": None,
            "plugin_error_kind": None,
            "premature_merge_attempted": None,
            "premature_merge_failed": None,
            "final_result_present": None,
            "failure_stage": None,
        }
        if mechanism == "verification":
            reports = [
                report
                for attempt_id, report in verification_reports.items()
                if attempt_id in actual_target_attempt_ids
            ]
            if reports:
                domain_invocations = sum(
                    not (
                        isinstance(report.get("metadata"), Mapping)
                        and report["metadata"].get("domain_verifier_invoked") is False
                    )
                    for report in reports
                )
                lean_checker_calls = sum(
                    int(item.get("domain_child_checker_call_count") or 0)
                    for item in verification_records
                    if isinstance(item, Mapping)
                    and item.get("planned_ai_unit_id") in target_ids
                )
                if domain_invocations == 0:
                    values["route_status"] = "applied"
                values["plugin_verify_submission_call_count"] = domain_invocations
                values["domain_child_checker_call_count"] = lean_checker_calls
            values["wrong_canonical_accepted"] = wrong_canonical
            if root_checker_report is not None:
                status = getattr(root_checker_report, "status", None)
                status_value = getattr(status, "value", status)
                values["root_checker_reached"] = True
                values["root_check_passed"] = status_value == "accepted"
                values["root_checker_call_count"] = 1
                values["root_checker_rejected_after_wrong_canonical"] = bool(
                    wrong_canonical and status_value != "accepted"
                )
            else:
                values["root_checker_reached"] = False
                values["root_check_passed"] = None
                values["root_checker_call_count"] = 0
        elif mechanism == "parser_policy":
            target_parser_records = [
                item
                for item in parser_records
                if isinstance(item, Mapping)
                and item.get("planned_ai_unit_id") in target_ids
            ]
            if target_parser_records:
                values["route_status"] = "applied"
                values["domain_parser_call_count"] = sum(
                    int(item.get("domain_parser_call_count") or 0)
                    for item in target_parser_records
                )
                values["parse_result"] = (
                    "bypassed"
                    if all(item.get("parse_result") == "bypassed" for item in target_parser_records)
                    else "mixed"
                )
                values["raw_only_exposed"] = any(
                    item.get("raw_only_exposed") is True
                    for item in target_parser_records
                )
                values["raw_only_accepted"] = any(
                    item.escaped_to_canonical_or_root is True
                    for item in challenges
                    if item.target_planned_ai_unit_id in target_ids
                )
            else:
                pass
        elif mechanism == "requeue":
            actual = next(
                (
                    item
                    for item in reversed(recovery_records)
                    if isinstance(item, Mapping)
                    and item.get("route_status")
                    in {"applied", "preempted_by_merge_first"}
                ),
                None,
            )
            if actual is None:
                pass
            else:
                values["route_status"] = actual.get("route_status")
                values["recovery_attempt_id"] = actual.get("recovery_attempt_id")
                values["recovery_retry_allowed"] = actual.get("retry_allowed")
                values["replacement_attempt_id"] = replacement_by_original.get(
                    str(actual.get("recovery_attempt_id"))
                )
                values["stuck_due_to_no_requeue"] = actual.get(
                    "stuck_due_to_no_requeue"
                )
        elif mechanism == "merge_gate":
            recovery_premerge = next(
                (
                    item
                    for item in reversed(merge_records)
                    if isinstance(item, Mapping)
                    and item.get("boundary") == "recovery_premerge"
                ),
                None,
            )
            premature_observation = premature[-1] if premature else None
            if recovery_premerge is None:
                pass
            else:
                values["merge_gate_satisfied"] = recovery_premerge.get(
                    "gate_satisfied"
                )
                values["required_child_unit_ids"] = list(
                    recovery_premerge.get("required_child_unit_ids", ())
                )
                values["canonical_child_unit_ids"] = list(
                    recovery_premerge.get("canonical_child_unit_ids", ())
                )
                values["missing_required_slot_ids"] = list(
                    recovery_premerge.get("missing_required_slot_ids", ())
                )
                values["route_status"] = recovery_premerge.get("route_status")
            if premature_observation is not None:
                values.update(
                    recovery_attempt_id=premature_observation.recovery_attempt_id,
                    plugin_merge_attempted=premature_observation.plugin_merge_attempted,
                    plugin_outcome=premature_observation.plugin_outcome,
                    plugin_error_kind=premature_observation.plugin_error_kind,
                    root_checker_reached=premature_observation.root_checker_reached,
                    root_check_passed=premature_observation.root_check_passed,
                    root_checker_call_count=int(
                        premature_observation.root_checker_reached
                    ),
                    premature_merge_attempted=(
                        premature_observation.plugin_merge_attempted
                    ),
                    premature_merge_failed=bool(
                        premature_observation.plugin_outcome
                        == "rejected_incomplete_input"
                        or premature_observation.root_check_passed is False
                        or not premature_observation.final_result_present
                    ),
                    final_result_present=(
                        premature_observation.final_result_present
                    ),
                    failure_stage=premature_observation.failure_stage,
                )
        observations.append(
            AblationObservationV1(disabled_mechanism=mechanism, **values)
        )
        if values["route_status"] is None:
            missing_reasons[
                f"ablation_observations[].{mechanism}_route"
            ] = "missing_actual_route_evidence"
    return observations, missing_reasons


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
    for item in fields(RootResultV2):
        if item.metadata.get("nullable") is not True or values.get(item.name) is not None:
            continue
        if item.name in {"trace_tail_started_at_ms", "trace_tail_terminal_at_ms"}:
            reasons[item.name] = "trace_tail_not_needed"
        elif item.name in {"failure_stage", "failure_kind", "failure_origin"}:
            reasons[item.name] = "root_verified_correct"
        elif item.name == "topic_family":
            reasons[item.name] = "not_applicable_to_factorization"
        else:
            reasons[item.name] = "not_applicable_to_answer_path_projection"
    for collection_name in (
        "fault_observations",
        "recovery_observations",
        "worker_death_observations",
        "challenge_observations",
        "ablation_observations",
    ):
        records = values.get(collection_name) or ()
        if not records:
            continue
        for item in fields(type(records[0])):
            if item.metadata.get("nullable") is not True:
                continue
            if any(getattr(record, item.name) is None for record in records):
                if (
                    collection_name == "challenge_observations"
                    and item.name == "source_semantics_preserved"
                ):
                    reasons[f"{collection_name}[].{item.name}"] = (
                        "candidate_semantics_not_observed_for_result_suppression"
                    )
                else:
                    reasons[f"{collection_name}[].{item.name}"] = (
                        "not_observed_at_scenario_boundary"
                    )
    return reasons


__all__ = ["RootProjectionError", "project_root_result"]
