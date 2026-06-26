"""Minimal application service for event-backed protocol flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from tokenshare.core.expansion import (
    DecompositionProposal,
    ExpansionDecision,
    ExpectedOutputRef,
    MergePlan,
    SplitStrategyInvocation,
    derive_child_initial_state,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
    validate_decomposition_proposal_limits,
)
from tokenshare.core.leases import LeaseManager
from tokenshare.core.models import ArtifactRef, Attempt, AttemptState, ClientRecord, JsonObject, Lease, LeaseState, ProtocolConfig, TaskRelation, TaskState, TaskUnit
from tokenshare.core.scheduling import Scheduler, SchedulingDecision
from tokenshare.core.state_machines import transition_attempt, transition_task_unit
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import CanonicalSelection, VerificationReport, digest_json, select_first_verified_bundle
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry, RegistrySnapshot
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventDraft, EventLedger, EventType, LedgerEvent


@dataclass(frozen=True)
class SchedulingFlowResult:
    lease: Lease
    attempt: Attempt
    task_unit: TaskUnit
    scheduling_decision: SchedulingDecision
    events: tuple[LedgerEvent, LedgerEvent, LedgerEvent, LedgerEvent]


@dataclass(frozen=True)
class LeaseExpiryFlowResult:
    lease: Lease
    attempt: Attempt
    task_unit: TaskUnit
    recovery_action: JsonObject
    events: tuple[LedgerEvent, LedgerEvent, LedgerEvent, LedgerEvent]


@dataclass(frozen=True)
class LeaseHeartbeatFlowResult:
    lease: Lease
    event: LedgerEvent


@dataclass(frozen=True)
class RegistrySnapshotFlowResult:
    snapshot: RegistrySnapshot
    registry_snapshot_ref: object
    event: LedgerEvent


@dataclass(frozen=True)
class ExecutionRequestFlowResult:
    request: ExecutionRequest
    request_ref: object
    event: LedgerEvent


@dataclass(frozen=True)
class ExecutionSubmissionFlowResult:
    submission: ExecutionSubmission
    submission_ref: object
    event: LedgerEvent
    attempt: Attempt | None
    attempt_event: LedgerEvent | None


@dataclass(frozen=True)
class VerificationFlowResult:
    report: VerificationReport
    event: LedgerEvent
    attempt: Attempt | None
    attempt_event: LedgerEvent | None


@dataclass(frozen=True)
class CanonicalBindingFlowResult:
    canonical_selection: CanonicalSelection
    event: LedgerEvent
    attempt: Attempt | None
    attempt_event: LedgerEvent | None


@dataclass(frozen=True)
class SplitStrategyInvocationFlowResult:
    invocation: SplitStrategyInvocation
    event: LedgerEvent


@dataclass(frozen=True)
class CompleteDecisionFlowResult:
    decision: ExpansionDecision
    task_unit: TaskUnit
    events: tuple[LedgerEvent, LedgerEvent]


@dataclass(frozen=True)
class ExpandDecisionFlowResult:
    decision: ExpansionDecision
    proposal_ref: ArtifactRef
    merge_plan_ref: ArtifactRef
    child_units: tuple[TaskUnit, ...]
    relations: tuple[TaskRelation, ...]
    expected_output_refs: tuple[ExpectedOutputRef, ...]
    task_graph: TaskGraph
    events: tuple[LedgerEvent, ...]


class ProtocolEngine:
    """Write protocol decisions to the append-only event ledger."""

    def __init__(
        self,
        *,
        event_ledger: EventLedger,
        protocol_config: ProtocolConfig,
        artifact_store: ArtifactStore | None = None,
        scheduler: Scheduler | None = None,
        lease_manager: LeaseManager | None = None,
    ) -> None:
        self._event_ledger = event_ledger
        self._protocol_config = protocol_config
        self._artifact_store = artifact_store
        self._scheduler = scheduler or Scheduler()
        self._lease_manager = lease_manager or LeaseManager(protocol_config=protocol_config)

    def record_registry_snapshot(
        self,
        *,
        task_id: str,
        registry_snapshot_id: str,
        plugin_registry: PluginRegistry,
        executor_registry: ExecutorRegistry,
        now: str,
        correlation_id: str,
    ) -> RegistrySnapshotFlowResult:
        artifact_store = self._require_artifact_store()
        snapshot = plugin_registry.freeze(
            task_id=task_id,
            registry_snapshot_id=registry_snapshot_id,
            executor_registry=executor_registry,
            artifact_store=artifact_store,
            frozen_at=now,
        )
        snapshot_ref = artifact_store.save_json(
            snapshot.to_dict(),
            artifact_id=registry_snapshot_id,
            artifact_type="RegistrySnapshot",
            artifact_schema_id="phase3.registry_snapshot",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": task_id},
            created_at=now,
        )
        event = self._event_ledger.append(
            event_type=EventType.REGISTRY_SNAPSHOT_RECORDED,
            object_type="RegistrySnapshot",
            object_id=registry_snapshot_id,
            task_id=task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"registry_snapshot:{registry_snapshot_id}",
            payload={
                "schema_version": "phase3.registry_snapshot_record.v1",
                "registry_snapshot_id": registry_snapshot_id,
                "task_id": task_id,
                "registry_snapshot_ref": snapshot_ref.to_dict(),
                "registry_snapshot_digest": snapshot_ref.content_hash,
                "plugin_entries": [
                    _registry_plugin_entry_summary(entry) for entry in snapshot.plugin_entries
                ],
                "executor_entries": [
                    _registry_executor_entry_summary(entry) for entry in snapshot.executor_entries
                ],
                "frozen_at": now,
            },
            occurred_at=now,
        )
        return RegistrySnapshotFlowResult(
            snapshot=snapshot,
            registry_snapshot_ref=snapshot_ref,
            event=event,
        )

    def record_execution_request(
        self,
        *,
        request: ExecutionRequest,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ExecutionRequestFlowResult:
        artifact_store = self._require_artifact_store()
        request_ref = artifact_store.save_json(
            request.to_dict(),
            artifact_id=request.request_id,
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": request.task_id, "attempt_id": request.attempt_id},
            created_at=request.created_at,
        )
        event = self._event_ledger.append(
            event_type=EventType.EXECUTION_REQUEST_RECORDED,
            object_type="ExecutionRequest",
            object_id=request.request_id,
            task_id=request.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=f"execution_request:{request.request_id}",
            payload={
                "schema_version": "phase3.execution_request_record.v1",
                "request_id": request.request_id,
                "task_id": request.task_id,
                "unit_id": request.unit_id,
                "attempt_id": request.attempt_id,
                "lease_id": request.lease_id,
                "request_ref": request_ref.to_dict(),
                "request_digest": request_ref.content_hash,
                "plugin_id": request.plugin.get("plugin_id"),
                "executor_id": request.executor.get("executor_id"),
                "created_at": request.created_at,
            },
            occurred_at=request.created_at,
        )
        return ExecutionRequestFlowResult(request=request, request_ref=request_ref, event=event)

    def record_execution_submission(
        self,
        *,
        submission: ExecutionSubmission,
        attempt: Attempt,
        lease: Lease,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ExecutionSubmissionFlowResult:
        artifact_store = self._require_artifact_store()
        submission_ref = artifact_store.save_json(
            submission.to_dict(),
            artifact_id=submission.submission_id,
            artifact_type="ExecutionSubmission",
            artifact_schema_id="phase3.execution_submission",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": submission.task_id, "attempt_id": submission.attempt_id},
            created_at=submission.submitted_at,
        )
        event = self._event_ledger.append(
            event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
            object_type="ExecutionSubmission",
            object_id=submission.submission_id,
            task_id=submission.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=f"execution_submission:{submission.submission_id}",
            payload={
                "schema_version": "phase3.execution_submission_record.v1",
                "submission_id": submission.submission_id,
                "request_id": submission.request_id,
                "task_id": submission.task_id,
                "unit_id": submission.unit_id,
                "attempt_id": submission.attempt_id,
                "lease_id": submission.lease_id,
                "submission_ref": submission_ref.to_dict(),
                "submission_digest": submission_ref.content_hash,
                "result_kind": submission.result_kind,
                "submitted_at": submission.submitted_at,
            },
            occurred_at=submission.submitted_at,
        )
        if attempt.state != AttemptState.RUNNING or not _submission_matches_attempt_lease(
            submission=submission,
            attempt=attempt,
            lease=lease,
        ):
            return ExecutionSubmissionFlowResult(
                submission=submission,
                submission_ref=submission_ref,
                event=event,
                attempt=None,
                attempt_event=None,
            )
        submitted_attempt = transition_attempt(
            attempt,
            new_state=AttemptState.SUBMITTED,
            changed_at=submission.submitted_at,
            reason="execution_submission_recorded",
            environment_summary=submission.environment_summary,
            raw_output_ref=submission.raw_output_ref,
            parsed_output_ref=submission.parsed_output_ref,
            candidate_output_refs=submission.candidate_output_refs,
            log_ref=submission.log_ref,
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=submitted_attempt.attempt_id,
            task_id=submitted_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=event.event_id,
            idempotency_key=(
                f"attempt:state:{submitted_attempt.attempt_id}:Running:Submitted:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.RUNNING.value,
                "new_state": AttemptState.SUBMITTED.value,
                "attempt": submitted_attempt.to_dict(),
                "reason": "execution_submission_recorded",
                "correlation_id": correlation_id,
            },
            occurred_at=submission.submitted_at,
        )
        return ExecutionSubmissionFlowResult(
            submission=submission,
            submission_ref=submission_ref,
            event=event,
            attempt=submitted_attempt,
            attempt_event=attempt_event,
        )

    def record_verification(
        self,
        *,
        report: VerificationReport,
        attempt: Attempt,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> VerificationFlowResult:
        """Record a Phase 4 verification report and advance Submitted attempts.

        The report is re-materialized through ``VerificationReport`` before any
        ledger write so a caller cannot mutate ``eligible_for_canonical`` after
        construction and bypass the derived-layer invariant.
        """

        validated_report = _validate_verification_report_for_attempt(
            report=report,
            attempt=attempt,
        )
        report_dict = validated_report.to_dict()
        report_digest = digest_json(report_dict)
        event = self._event_ledger.append(
            event_type=EventType.VERIFICATION_RECORDED,
            object_type="VerificationReport",
            object_id=validated_report.verification_report_id,
            task_id=validated_report.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"verification:{validated_report.submission_id}:"
                f"{validated_report.validator_policy_id}"
            ),
            payload={
                "schema_version": "phase4.verification_record.v1",
                "verification_report": report_dict,
                "verification_report_digest": report_digest,
                "status": validated_report.status,
                "eligible_for_canonical": validated_report.eligible_for_canonical,
                "task_id": validated_report.task_id,
                "unit_id": validated_report.unit_id,
                "attempt_id": validated_report.attempt_id,
                "submission_id": validated_report.submission_id,
                "submission_event_seq": validated_report.submission_event_seq,
                "candidate_output_bundle_digest": (
                    validated_report.candidate_output_bundle_digest
                ),
                "validator_policy_id": validated_report.validator_policy_id,
                "plugin_id": validated_report.plugin_id,
                "plugin_version": validated_report.plugin_version,
                "completed_at": validated_report.completed_at,
            },
            occurred_at=validated_report.completed_at,
        )
        if validated_report.status == "error":
            return VerificationFlowResult(
                report=validated_report,
                event=event,
                attempt=None,
                attempt_event=None,
            )

        next_state = (
            AttemptState.VERIFIED
            if validated_report.status in {"passed", "accepted"}
            else AttemptState.REJECTED
        )
        reason = (
            "verification_passed"
            if next_state == AttemptState.VERIFIED
            else "verification_rejected"
        )
        verified_attempt = transition_attempt(
            attempt,
            new_state=next_state,
            changed_at=validated_report.completed_at,
            reason=reason,
            failure_kind="invalid_output" if next_state == AttemptState.REJECTED else None,
            failure_reason=(
                _verification_failure_message(validated_report)
                if next_state == AttemptState.REJECTED
                else None
            ),
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=verified_attempt.attempt_id,
            task_id=verified_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=event.event_id,
            idempotency_key=(
                f"attempt:state:{verified_attempt.attempt_id}:Submitted:"
                f"{next_state.value}:verification:{validated_report.verification_report_id}"
            ),
            payload={
                "old_state": AttemptState.SUBMITTED.value,
                "new_state": next_state.value,
                "attempt": verified_attempt.to_dict(),
                "reason": reason,
                "verification_report_id": validated_report.verification_report_id,
                "correlation_id": correlation_id,
            },
            occurred_at=validated_report.completed_at,
        )
        return VerificationFlowResult(
            report=validated_report,
            event=event,
            attempt=verified_attempt,
            attempt_event=attempt_event,
        )

    def bind_canonical_outputs(
        self,
        *,
        task_id: str,
        unit_id: str,
        verification_events: list[LedgerEvent],
        attempts_by_id: dict[str, Attempt],
        policy: str,
        now: str,
        correlation_id: str,
    ) -> CanonicalBindingFlowResult:
        """Bind the first eligible verified bundle as canonical for a unit."""

        if policy != "first_verified_bundle":
            raise ValueError(f"unsupported canonical output policy: {policy}")

        current_events = self._event_ledger.read_all()
        recorded_verification_events = _recorded_verification_events_for_binding(
            verification_events=verification_events,
            ledger_events=current_events,
        )
        selection = select_first_verified_bundle(
            task_id=task_id,
            unit_id=unit_id,
            verification_event_reports=_verification_reports_from_events(
                recorded_verification_events
            ),
            bound_at=now,
        )
        existing = _existing_canonical_event(
            events=current_events,
            task_id=task_id,
            unit_id=unit_id,
        )
        if existing is not None:
            existing_selection = _canonical_selection_from_payload(
                existing.payload["canonical_selection"]
            )
            if not _same_canonical_commitment(existing_selection, selection):
                raise ValueError(f"canonical outputs already bound for {task_id}/{unit_id}")
            return CanonicalBindingFlowResult(
                canonical_selection=existing_selection,
                event=existing,
                attempt=attempts_by_id.get(existing_selection.selected_attempt_id),
                attempt_event=None,
            )

        selected_attempt = attempts_by_id.get(selection.selected_attempt_id)
        if selected_attempt is None:
            raise ValueError(f"selected attempt not supplied: {selection.selected_attempt_id}")
        if selected_attempt.state != AttemptState.VERIFIED:
            raise ValueError("selected attempt must be Verified before canonical binding")

        selection_dict = selection.to_dict()
        selection_digest = digest_json(selection_dict)
        event = self._event_ledger.append(
            event_type=EventType.CANONICAL_OUTPUTS_BOUND,
            object_type="CanonicalSelection",
            object_id=selection.canonical_selection_id,
            task_id=selection.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"canonical_outputs:{task_id}:{unit_id}",
            payload={
                "schema_version": "phase4.canonical_outputs_bound.v1",
                "canonical_selection": selection_dict,
                "canonical_selection_digest": selection_digest,
                "task_id": selection.task_id,
                "unit_id": selection.unit_id,
                "selection_policy": selection.selection_policy,
                "selection_policy_version": selection.selection_policy_version,
                "selected_verification_report_id": (
                    selection.selected_verification_report_id
                ),
                "selected_verification_event_seq": (
                    selection.selected_verification_event_seq
                ),
                "selected_submission_id": selection.selected_submission_id,
                "selected_submission_event_seq": selection.selected_submission_event_seq,
                "selected_attempt_id": selection.selected_attempt_id,
                "canonical_output_bundle_digest": (
                    selection.canonical_output_bundle_digest
                ),
                "canonical_output_refs": _artifact_refs_to_dict(
                    selection.canonical_output_refs
                ),
                "bound_at": selection.bound_at,
            },
            occurred_at=now,
        )
        canonical_attempt = transition_attempt(
            selected_attempt,
            new_state=AttemptState.CANONICAL,
            changed_at=now,
            reason="canonical_outputs_bound",
            metadata={
                "canonical_selection_id": selection.canonical_selection_id,
                "canonical_output_bundle_digest": (
                    selection.canonical_output_bundle_digest
                ),
            },
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=canonical_attempt.attempt_id,
            task_id=canonical_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=event.event_id,
            idempotency_key=(
                f"attempt:state:{canonical_attempt.attempt_id}:Verified:Canonical:"
                f"{selection.canonical_selection_id}"
            ),
            payload={
                "old_state": AttemptState.VERIFIED.value,
                "new_state": AttemptState.CANONICAL.value,
                "attempt": canonical_attempt.to_dict(),
                "reason": "canonical_outputs_bound",
                "canonical_selection_id": selection.canonical_selection_id,
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        return CanonicalBindingFlowResult(
            canonical_selection=selection,
            event=event,
            attempt=canonical_attempt,
            attempt_event=attempt_event,
        )

    def record_split_strategy_invocation(
        self,
        *,
        invocation: SplitStrategyInvocation,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> SplitStrategyInvocationFlowResult:
        """Record the split strategy invocation audit event only."""

        payload = {
            "schema_version": "phase4.split_strategy_invocation_record.v1",
            "invocation": _split_invocation_summary(invocation),
            "task_id": invocation.task_id,
            "unit_id": invocation.unit_id,
            "canonical_selection_id": invocation.canonical_selection_id,
            "canonical_output_bundle_digest": (
                invocation.canonical_output_bundle_digest
            ),
            "plugin_id": invocation.plugin_id,
            "plugin_version": invocation.plugin_version,
            "plugin_descriptor_digest": invocation.plugin_descriptor_digest,
            "split_strategy_id": invocation.split_strategy_id,
            "split_strategy_params_digest": invocation.split_strategy_params_digest,
            "expansion_scope_hash": invocation.expansion_scope_hash,
            "status": invocation.status,
            "result_action": invocation.result_action,
            "result_digest": invocation.result_digest,
            "error_kind": invocation.error_kind,
            "error_summary": invocation.error_summary,
            "started_at": invocation.started_at,
            "completed_at": invocation.completed_at,
        }
        event = self._event_ledger.append(
            event_type=EventType.SPLIT_STRATEGY_INVOCATION_RECORDED,
            object_type="SplitStrategyInvocation",
            object_id=invocation.invocation_id,
            task_id=invocation.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"split_invocation:{invocation.expansion_scope_hash}:"
                f"attempt:{invocation.invocation_attempt_no}"
            ),
            payload=payload,
            occurred_at=invocation.completed_at,
        )
        return SplitStrategyInvocationFlowResult(invocation=invocation, event=event)

    def record_complete_decision(
        self,
        *,
        decision: ExpansionDecision,
        task_unit: TaskUnit,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> CompleteDecisionFlowResult:
        """Record an accepted complete decision and completed state atomically."""

        if decision.action != "complete":
            raise ValueError("record_complete_decision requires action=complete")
        _validate_task_unit_for_complete_decision(decision=decision, task_unit=task_unit)
        current_events = self._event_ledger.read_all()
        facts = _validate_accepted_decision_prerequisites(
            decision=decision,
            events=current_events,
            artifact_store=self._require_artifact_store(),
        )
        completed_unit = transition_task_unit(
            task_unit,
            new_state=TaskState.COMPLETED,
            reason="complete_decision_accepted",
            trigger="split_strategy",
            changed_at=decision.decided_at,
        )
        decision_dict = decision.to_dict()
        decision_digest = digest_json(decision_dict)
        decision_payload = {
            "schema_version": "phase4.expansion_decision_record.v1",
            "expansion_decision": decision_dict,
            "expansion_decision_digest": decision_digest,
            "task_id": decision.task_id,
            "unit_id": decision.unit_id,
            "canonical_selection_id": decision.canonical_selection_id,
            "canonical_output_bundle_digest": (
                decision.canonical_output_bundle_digest
            ),
            "expansion_scope_hash": decision.expansion_scope_hash,
            "action": decision.action,
            "source_invocation_id": decision.source_invocation_id,
            "proposal_id": decision.proposal_id,
            "proposal_digest": decision.proposal_digest,
            "merge_plan_id": decision.merge_plan_id,
            "merge_plan_digest": decision.merge_plan_digest,
            "action_body": decision.action_body,
            "decided_at": decision.decided_at,
        }
        batch_id = f"completion_batch:{decision.expansion_decision_id}"
        first_batch_event_id = _first_event_id_for_batch(
            events=current_events,
            batch_id=batch_id,
        ) or f"event_{len(current_events) + 1:012d}"
        task_payload = {
            "schema_version": "phase4.complete_task_unit_state_change_record.v1",
            "old_state": task_unit.state.value,
            "new_state": TaskState.COMPLETED.value,
            "task_unit_state_change": _task_unit_state_change(
                task_unit=completed_unit,
                old_state=task_unit.state,
                new_state=TaskState.COMPLETED,
                reason="complete_decision_accepted",
                trigger="split_strategy",
                correlation_id=correlation_id,
                causation_event_id=first_batch_event_id,
                changed_at=decision.decided_at,
                state_context={
                    "expansion_decision_id": decision.expansion_decision_id,
                    "source_invocation_id": decision.source_invocation_id,
                    "canonical_selection_id": decision.canonical_selection_id,
                    "canonical_output_bundle_digest": (
                        decision.canonical_output_bundle_digest
                    ),
                },
            ),
            "task_unit": completed_unit.to_dict(),
            "reason": "complete_decision_accepted",
            "expansion_decision_id": decision.expansion_decision_id,
            "source_invocation_id": facts["invocation_id"],
            "canonical_selection_id": facts["canonical_selection_id"],
            "correlation_id": correlation_id,
        }
        batch_events = self._event_ledger.append_batch(
            [
                EventDraft(
                    event_type=EventType.EXPANSION_DECISION_RECORDED,
                    object_type="ExpansionDecision",
                    object_id=decision.expansion_decision_id,
                    task_id=decision.task_id,
                    actor={"kind": "protocol_engine"},
                    correlation_id=correlation_id,
                    causation_event_id=causation_event_id,
                    idempotency_key=(
                        f"expansion_decision:{decision.expansion_scope_hash}"
                    ),
                    payload=decision_payload,
                    occurred_at=decision.decided_at,
                ),
                EventDraft(
                    event_type=EventType.TASK_UNIT_STATE_CHANGED,
                    object_type="TaskUnit",
                    object_id=completed_unit.unit_id,
                    task_id=completed_unit.task_id,
                    actor={"kind": "protocol_engine"},
                    correlation_id=correlation_id,
                    causation_event_id=first_batch_event_id,
                    idempotency_key=(
                        f"task_unit:state:{completed_unit.unit_id}:"
                        f"{task_unit.state.value}:Completed:"
                        f"{decision.expansion_decision_id}"
                    ),
                    payload=task_payload,
                    occurred_at=decision.decided_at,
                ),
            ],
            batch_id=batch_id,
        )
        return CompleteDecisionFlowResult(
            decision=decision,
            task_unit=completed_unit,
            events=(batch_events[0], batch_events[1]),
        )

    def record_expand_decision(
        self,
        *,
        decision: ExpansionDecision,
        proposal: DecompositionProposal,
        merge_plan: MergePlan,
        parent_unit: TaskUnit,
        graph: TaskGraph,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ExpandDecisionFlowResult:
        """Record an accepted expand decision and child graph batch atomically."""

        if decision.action != "expand":
            raise ValueError("record_expand_decision requires action=expand")
        _validate_task_unit_for_complete_decision(decision=decision, task_unit=parent_unit)
        artifact_store = self._require_artifact_store()

        proposal_ref = artifact_store.save_json(
            proposal.to_dict(),
            artifact_id=proposal.proposal_header["proposal_id"],
            artifact_type="DecompositionProposal",
            artifact_schema_id="phase4.decomposition_proposal",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine", "stage": "staged"},
            metadata={
                "task_id": decision.task_id,
                "unit_id": decision.unit_id,
                "expansion_decision_id": decision.expansion_decision_id,
            },
            created_at=proposal.proposal_header["created_at"],
        )
        merge_plan_ref = artifact_store.save_json(
            merge_plan.to_dict(),
            artifact_id=merge_plan.merge_plan_header["merge_plan_id"],
            artifact_type="MergePlan",
            artifact_schema_id="phase4.merge_plan",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine", "stage": "staged"},
            metadata={
                "task_id": decision.task_id,
                "unit_id": decision.unit_id,
                "expansion_decision_id": decision.expansion_decision_id,
            },
            created_at=merge_plan.merge_plan_header["created_at"],
        )

        current_events = self._event_ledger.read_all()
        facts = _validate_accepted_decision_prerequisites(
            decision=decision,
            events=current_events,
            artifact_store=artifact_store,
        )
        _validate_expand_documents(
            decision=decision,
            proposal=proposal,
            merge_plan=merge_plan,
            parent_unit=parent_unit,
            graph=graph,
            canonical_selection=facts["canonical_selection"],
            split_strategy=facts["split_strategy"],
            descriptor=facts["descriptor"],
        )

        child_units = _build_expansion_child_units(
            decision=decision,
            proposal=proposal,
            parent_unit=parent_unit,
            graph=graph,
            parent_canonical_output_refs=facts["canonical_selection"].canonical_output_refs,
        )
        child_unit_ids_by_key = {
            unit.metadata["child_logical_key"]: unit.unit_id for unit in child_units
        }
        relations = _build_expansion_relations(
            decision=decision,
            proposal=proposal,
            child_unit_ids_by_key=child_unit_ids_by_key,
        )
        _validate_merge_plan_child_ids(
            merge_plan=merge_plan,
            child_unit_ids_by_key=child_unit_ids_by_key,
        )

        batch_id = f"expansion_batch:{decision.expansion_decision_id}"
        batch_size = 4 + len(child_units) + len(relations)
        final_event_seq = _final_event_seq_for_batch(
            events=current_events,
            batch_id=batch_id,
            fallback_seq=len(current_events) + batch_size,
        )
        expected_output_refs = tuple(
            ExpectedOutputRef.from_expected_output(
                expected_output=expected_output,
                task_id=decision.task_id,
                owner_unit_id=decision.unit_id,
                canonical_selection_id=decision.canonical_selection_id,
                canonical_output_bundle_digest=decision.canonical_output_bundle_digest,
                source_proposal_id=decision.proposal_id or "",
                source_expansion_decision_id=decision.expansion_decision_id,
                created_event_seq=final_event_seq,
                logical_position=position,
                child_unit_ids_by_key=child_unit_ids_by_key,
                merge_plan_id=decision.merge_plan_id,
            )
            for position, expected_output in enumerate(proposal.expected_outputs)
        )

        decision_payload = _expansion_decision_payload(decision=decision)
        drafts: list[EventDraft] = [
            EventDraft(
                event_type=EventType.DECOMPOSITION_PROPOSAL_RECORDED,
                object_type="DecompositionProposal",
                object_id=proposal.proposal_header["proposal_id"],
                task_id=decision.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=(
                    f"decomposition_proposal:{decision.expansion_scope_hash}:"
                    f"{decision.proposal_digest}"
                ),
                payload={
                    "schema_version": "phase4.decomposition_proposal_record.v1",
                    "proposal_id": proposal.proposal_header["proposal_id"],
                    "task_id": decision.task_id,
                    "parent_unit_id": decision.unit_id,
                    "canonical_selection_id": decision.canonical_selection_id,
                    "proposal_ref": proposal_ref.to_dict(),
                    "proposal_digest": decision.proposal_digest,
                    "expansion_scope_hash": decision.expansion_scope_hash,
                    "plugin_id": decision.plugin_id,
                    "plugin_version": decision.plugin_version,
                    "split_strategy_id": decision.split_strategy_id,
                    "child_count": len(proposal.child_specs),
                    "dependency_edge_count": len(proposal.dependency_edges),
                    "expected_output_count": len(proposal.expected_outputs),
                    "merge_slot_count": len(proposal.merge_slots),
                    "created_at": proposal.proposal_header["created_at"],
                },
                occurred_at=proposal.proposal_header["created_at"],
            ),
            EventDraft(
                event_type=EventType.EXPANSION_DECISION_RECORDED,
                object_type="ExpansionDecision",
                object_id=decision.expansion_decision_id,
                task_id=decision.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=f"expansion_decision:{decision.expansion_scope_hash}",
                payload=decision_payload,
                occurred_at=decision.decided_at,
            ),
            EventDraft(
                event_type=EventType.MERGE_PLAN_RECORDED,
                object_type="MergePlan",
                object_id=merge_plan.merge_plan_header["merge_plan_id"],
                task_id=decision.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=decision.expansion_decision_id,
                idempotency_key=(
                    f"merge_plan:{decision.expansion_scope_hash}:"
                    f"{decision.proposal_digest}:{decision.merge_plan_digest}"
                ),
                payload={
                    "schema_version": "phase4.merge_plan_record.v1",
                    "merge_plan_id": merge_plan.merge_plan_header["merge_plan_id"],
                    "task_id": decision.task_id,
                    "parent_unit_id": decision.unit_id,
                    "canonical_selection_id": decision.canonical_selection_id,
                    "decomposition_proposal_id": decision.proposal_id,
                    "expansion_decision_id": decision.expansion_decision_id,
                    "merge_plan_ref": merge_plan_ref.to_dict(),
                    "merge_plan_digest": decision.merge_plan_digest,
                    "merge_policy_id": merge_plan.merge_policy_ref.get("merge_policy_id"),
                    "merge_policy_version": merge_plan.merge_policy_ref.get(
                        "merge_policy_version"
                    ),
                    "required_slot_count": len(merge_plan.required_slots),
                    "parent_output_mapping_count": len(
                        merge_plan.parent_output_mapping
                    ),
                    "created_at": merge_plan.merge_plan_header["created_at"],
                },
                occurred_at=merge_plan.merge_plan_header["created_at"],
            ),
        ]
        drafts.extend(
            EventDraft(
                event_type=EventType.TASK_UNIT_CREATED,
                object_type="TaskUnit",
                object_id=child_unit.unit_id,
                task_id=child_unit.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=decision.expansion_decision_id,
                idempotency_key=f"task_unit:create:{child_unit.unit_id}",
                payload={
                    "schema_version": "phase4.expansion_task_unit_created.v1",
                    "task_unit": child_unit.to_dict(),
                    "expansion_decision_id": decision.expansion_decision_id,
                    "proposal_id": decision.proposal_id,
                    "proposal_digest": decision.proposal_digest,
                    "parent_unit_id": decision.unit_id,
                    "child_logical_key": child_unit.metadata["child_logical_key"],
                    "initial_state_derivation": "phase4.child_initial_state.v1",
                },
                occurred_at=child_unit.created_at,
            )
            for child_unit in child_units
        )
        drafts.extend(
            EventDraft(
                event_type=EventType.TASK_RELATION_CREATED,
                object_type="TaskRelation",
                object_id=relation.relation_id,
                task_id=relation.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=decision.expansion_decision_id,
                idempotency_key=f"task_relation:create:{relation.relation_id}",
                payload={
                    "schema_version": "phase4.expansion_task_relation_created.v1",
                    "task_relation": relation.to_dict(),
                    "expansion_decision_id": decision.expansion_decision_id,
                    "proposal_id": decision.proposal_id,
                    "proposal_digest": decision.proposal_digest,
                    "edge_logical_key": relation.metadata["edge_logical_key"],
                },
                occurred_at=relation.created_at,
            )
            for relation in relations
        )
        drafts.append(
            EventDraft(
                event_type=EventType.TASK_EXPANDED,
                object_type="TaskExpansion",
                object_id=decision.expansion_decision_id,
                task_id=decision.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=decision.expansion_decision_id,
                idempotency_key=f"task_expanded:{decision.expansion_decision_id}",
                payload={
                    "schema_version": "phase4.task_expanded.v1",
                    "task_id": decision.task_id,
                    "parent_unit_id": decision.unit_id,
                    "expansion_decision_id": decision.expansion_decision_id,
                    "canonical_selection_id": decision.canonical_selection_id,
                    "proposal_id": decision.proposal_id,
                    "proposal_digest": decision.proposal_digest,
                    "merge_plan_id": decision.merge_plan_id,
                    "merge_plan_digest": decision.merge_plan_digest,
                    "child_unit_ids": [unit.unit_id for unit in child_units],
                    "relation_ids": [relation.relation_id for relation in relations],
                    "expected_output_ids": [
                        ref.expected_output_id for ref in expected_output_refs
                    ],
                    "expanded_at": decision.decided_at,
                },
                occurred_at=decision.decided_at,
            )
        )

        batch_events = self._event_ledger.append_batch(drafts, batch_id=batch_id)
        expanded_graph = TaskGraph(
            task_id=graph.task_id,
            units={**graph.units, **{unit.unit_id: unit for unit in child_units}},
            relations=tuple(graph.relations) + relations,
            canonical_outputs_by_unit_id=graph.canonical_outputs_by_unit_id,
            protocol_config=graph.protocol_config,
        )
        return ExpandDecisionFlowResult(
            decision=decision,
            proposal_ref=proposal_ref,
            merge_plan_ref=merge_plan_ref,
            child_units=child_units,
            relations=relations,
            expected_output_refs=expected_output_refs,
            task_graph=expanded_graph,
            events=tuple(batch_events),
        )

    def schedule_ready_unit(
        self,
        *,
        graph: TaskGraph,
        clients: Iterable[ClientRecord],
        now: str,
        correlation_id: str,
        decision_id: str,
        lease_id: str,
        attempt_id: str,
        fencing_token: str,
        active_leases_by_unit_id: dict[str, object] | None = None,
    ) -> SchedulingFlowResult:
        active_leases = _merge_active_lease_maps(
            _active_leases_by_unit_id_from_events(self._event_ledger.read_all()),
            active_leases_by_unit_id or {},
        )
        decision = self._scheduler.select_next(
            graph=graph,
            clients=clients,
            protocol_config=self._protocol_config,
            active_leases_by_unit_id=active_leases,
            now=now,
            decision_id=decision_id,
        )
        if decision is None:
            raise ValueError("no schedulable ready unit")

        claim = self._lease_manager.claim(
            decision=decision,
            lease_id=lease_id,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            now=now,
        )
        unit = graph.units[decision.unit_id]
        processing_unit = transition_task_unit(
            unit,
            new_state=TaskState.PROCESSING,
            reason="scheduled",
            trigger="scheduler",
            changed_at=now,
        )

        lease_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=claim.lease.lease_id,
            task_id=claim.lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"lease:create:{claim.lease.lease_id}",
            payload={
                "old_state": None,
                "new_state": LeaseState.ACTIVE.value,
                "lease": claim.lease.to_dict(),
                "scheduling_decision": decision.to_dict(),
                "reason": "scheduled",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_created_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=claim.created_attempt.attempt_id,
            task_id=claim.created_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=lease_event.event_id,
            idempotency_key=(
                f"attempt:state:{claim.created_attempt.attempt_id}:null:Created:{correlation_id}"
            ),
            payload={
                "old_state": None,
                "new_state": AttemptState.CREATED.value,
                "attempt": claim.created_attempt.to_dict(),
                "reason": "scheduled",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_running_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=claim.running_attempt.attempt_id,
            task_id=claim.running_attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_created_event.event_id,
            idempotency_key=(
                f"attempt:state:{claim.running_attempt.attempt_id}:Created:Running:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.CREATED.value,
                "new_state": AttemptState.RUNNING.value,
                "attempt": claim.running_attempt.to_dict(),
                "reason": "executor_started",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        task_unit_event = self._event_ledger.append(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id=processing_unit.unit_id,
            task_id=processing_unit.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_running_event.event_id,
            idempotency_key=(
                f"task_unit:state:{processing_unit.unit_id}:Ready:Processing:{correlation_id}"
            ),
            payload={
                "task_unit_state_change": _task_unit_state_change(
                    task_unit=processing_unit,
                    old_state=TaskState.READY,
                    new_state=TaskState.PROCESSING,
                    reason="scheduled",
                    trigger="scheduler",
                    correlation_id=correlation_id,
                    causation_event_id=attempt_running_event.event_id,
                    changed_at=now,
                ),
                "task_unit": processing_unit.to_dict(),
            },
            occurred_at=now,
        )

        return SchedulingFlowResult(
            lease=claim.lease,
            attempt=claim.running_attempt,
            task_unit=processing_unit,
            scheduling_decision=decision,
            events=(lease_event, attempt_created_event, attempt_running_event, task_unit_event),
        )

    def record_lease_heartbeat(
        self,
        *,
        lease: Lease,
        now: str,
        correlation_id: str,
    ) -> LeaseHeartbeatFlowResult:
        heartbeat_lease = self._lease_manager.heartbeat(lease, now=now)
        heartbeat_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=heartbeat_lease.lease_id,
            task_id=heartbeat_lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=(
                f"lease:heartbeat:{heartbeat_lease.lease_id}:{heartbeat_lease.heartbeat_count}"
            ),
            payload={
                "old_state": LeaseState.ACTIVE.value,
                "new_state": LeaseState.ACTIVE.value,
                "lease": heartbeat_lease.to_dict(),
                "reason": "heartbeat",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        return LeaseHeartbeatFlowResult(lease=heartbeat_lease, event=heartbeat_event)

    def record_lease_expiry(
        self,
        *,
        lease: Lease,
        attempt: Attempt,
        task_unit: TaskUnit,
        now: str,
        correlation_id: str,
        recovery_action_id: str,
        retry_count: int,
    ) -> LeaseExpiryFlowResult:
        expiry = self._lease_manager.expire(
            lease=lease,
            attempt=attempt,
            task_unit=task_unit,
            now=now,
            recovery_action_id=recovery_action_id,
            retry_count=retry_count,
        )
        reason = expiry.recovery_action["reason"]
        recovered_unit = transition_task_unit(
            task_unit,
            new_state=expiry.next_task_state,
            reason=reason,
            trigger="recovery",
            changed_at=now,
        )

        lease_event = self._event_ledger.append(
            event_type=EventType.LEASE_STATE_CHANGED,
            object_type="Lease",
            object_id=expiry.lease.lease_id,
            task_id=expiry.lease.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            idempotency_key=f"lease:terminal:{expiry.lease.lease_id}:Expired",
            payload={
                "old_state": LeaseState.ACTIVE.value,
                "new_state": LeaseState.EXPIRED.value,
                "lease": expiry.lease.to_dict(),
                "reason": "lease_expired",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        attempt_event = self._event_ledger.append(
            event_type=EventType.ATTEMPT_STATE_CHANGED,
            object_type="Attempt",
            object_id=expiry.attempt.attempt_id,
            task_id=expiry.attempt.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=lease_event.event_id,
            idempotency_key=(
                f"attempt:state:{expiry.attempt.attempt_id}:Running:Superseded:{correlation_id}"
            ),
            payload={
                "old_state": AttemptState.RUNNING.value,
                "new_state": AttemptState.SUPERSEDED.value,
                "attempt": expiry.attempt.to_dict(),
                "reason": "lease_expired",
                "correlation_id": correlation_id,
            },
            occurred_at=now,
        )
        recovery_event = self._event_ledger.append(
            event_type=EventType.RECOVERY_ACTION_RECORDED,
            object_type="RecoveryAction",
            object_id=expiry.recovery_action["recovery_action_id"],
            task_id=expiry.recovery_action["task_id"],
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=attempt_event.event_id,
            idempotency_key=(
                f"recovery:{expiry.recovery_action['unit_id']}:lease_expired:"
                f"{expiry.recovery_action['attempt_id']}:{expiry.recovery_action['retry_count']}"
            ),
            payload={"recovery_action": expiry.recovery_action},
            occurred_at=now,
        )
        task_event = self._event_ledger.append(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id=recovered_unit.unit_id,
            task_id=recovered_unit.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=recovery_event.event_id,
            idempotency_key=(
                f"task_unit:state:{recovered_unit.unit_id}:{task_unit.state.value}:"
                f"{recovered_unit.state.value}:{correlation_id}"
            ),
            payload={
                "task_unit_state_change": _task_unit_state_change(
                    task_unit=recovered_unit,
                    old_state=task_unit.state,
                    new_state=recovered_unit.state,
                    reason=reason,
                    trigger="recovery",
                    correlation_id=correlation_id,
                    causation_event_id=recovery_event.event_id,
                    changed_at=now,
                    state_context={"retry_count": retry_count},
                ),
                "task_unit": recovered_unit.to_dict(),
            },
            occurred_at=now,
        )
        return LeaseExpiryFlowResult(
            lease=expiry.lease,
            attempt=expiry.attempt,
            task_unit=recovered_unit,
            recovery_action=expiry.recovery_action,
            events=(lease_event, attempt_event, recovery_event, task_event),
        )

    def _require_artifact_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            raise ValueError("artifact_store is required for Phase 3 execution artifacts")
        return self._artifact_store


def _task_unit_state_change(
    *,
    task_unit: TaskUnit,
    old_state: TaskState,
    new_state: TaskState,
    reason: str,
    trigger: str,
    correlation_id: str,
    causation_event_id: str | None,
    changed_at: str,
    state_context: JsonObject | None = None,
) -> JsonObject:
    return {
        "schema_version": "phase2.task_unit_state_change.v1",
        "task_id": task_unit.task_id,
        "unit_id": task_unit.unit_id,
        "old_state": old_state.value,
        "new_state": new_state.value,
        "reason": reason,
        "trigger": trigger,
        "correlation_id": correlation_id,
        "causation_event_id": causation_event_id,
        "changed_at": changed_at,
        "state_context": dict(state_context or {}),
    }


def _active_leases_by_unit_id_from_events(events: Iterable[LedgerEvent]) -> dict[str, list[str]]:
    latest_leases: dict[str, JsonObject] = {}
    for event in events:
        if event.event_type != EventType.LEASE_STATE_CHANGED:
            continue
        lease = event.payload.get("lease")
        if not isinstance(lease, dict):
            continue
        lease_id = lease.get("lease_id")
        if isinstance(lease_id, str):
            latest_leases[lease_id] = lease

    active_by_unit_id: dict[str, list[str]] = {}
    for lease_id, lease in latest_leases.items():
        if lease.get("state") != LeaseState.ACTIVE.value:
            continue
        unit_id = lease.get("unit_id")
        if isinstance(unit_id, str):
            active_by_unit_id.setdefault(unit_id, []).append(lease_id)
    return active_by_unit_id


def _merge_active_lease_maps(
    ledger_active: dict[str, list[str]],
    supplied_active: dict[str, object],
) -> dict[str, object]:
    active: dict[str, object] = {unit_id: list(lease_ids) for unit_id, lease_ids in ledger_active.items()}
    for unit_id, value in supplied_active.items():
        active[unit_id] = value
    return active


def _submission_matches_attempt_lease(
    *,
    submission: ExecutionSubmission,
    attempt: Attempt,
    lease: Lease,
) -> bool:
    return (
        submission.task_id == attempt.task_id
        and submission.unit_id == attempt.unit_id
        and submission.attempt_id == attempt.attempt_id
        and submission.lease_id == attempt.lease_id
        and lease.task_id == attempt.task_id
        and lease.unit_id == attempt.unit_id
        and lease.attempt_id == attempt.attempt_id
        and submission.lease_id == lease.lease_id
        and submission.fencing_token == lease.fencing_token
    )


def _validate_verification_report_for_attempt(
    *, report: VerificationReport, attempt: Attempt
) -> VerificationReport:
    if attempt.state != AttemptState.SUBMITTED:
        raise ValueError("attempt must be Submitted before verification recording")
    if (
        report.task_id != attempt.task_id
        or report.unit_id != attempt.unit_id
        or report.attempt_id != attempt.attempt_id
    ):
        raise ValueError("verification report does not match attempt")

    validated = VerificationReport(
        verification_report_id=report.verification_report_id,
        task_id=report.task_id,
        unit_id=report.unit_id,
        attempt_id=report.attempt_id,
        submission_id=report.submission_id,
        submission_event_seq=report.submission_event_seq,
        candidate_output_bundle_digest=report.candidate_output_bundle_digest,
        candidate_output_refs=dict(report.candidate_output_refs),
        required_output_names=list(report.required_output_names),
        output_contract_id=report.output_contract_id,
        validator_policy_id=report.validator_policy_id,
        plugin_id=report.plugin_id,
        plugin_version=report.plugin_version,
        plugin_descriptor_digest=report.plugin_descriptor_digest,
        status=report.status,
        eligible_for_canonical=report.eligible_for_canonical,
        layer_results=dict(report.layer_results),
        failure_summary=dict(report.failure_summary) if report.failure_summary else None,
        verification_environment=dict(report.verification_environment),
        verifier=dict(report.verifier),
        started_at=report.started_at,
        completed_at=report.completed_at,
        metadata=dict(report.metadata or {}),
        schema_version=report.schema_version,
    )
    if validated.candidate_output_bundle_digest != digest_json(
        validated.candidate_output_refs
    ):
        raise ValueError("verification report candidate output bundle digest mismatch")
    if validated.status in {"passed", "accepted"} and not validated.eligible_for_canonical:
        raise ValueError("passed verification report must have all required layers passed")
    if validated.status in {"rejected", "error"} and validated.eligible_for_canonical:
        raise ValueError("non-passed verification report cannot be canonical eligible")
    missing_outputs = [
        name
        for name in validated.required_output_names
        if name not in validated.candidate_output_refs
    ]
    required_layer = validated.layer_results.get("required_output_coverage_check", {})
    if missing_outputs and required_layer.get("status") == "passed":
        raise ValueError("required output coverage layer contradicts candidate outputs")
    if attempt.candidate_output_refs is not None and dict(attempt.candidate_output_refs) != dict(
        validated.candidate_output_refs
    ):
        raise ValueError("verification report candidate outputs do not match attempt")
    return validated


def _verification_reports_from_events(
    events: Iterable[LedgerEvent],
) -> list[tuple[int, VerificationReport]]:
    reports: list[tuple[int, VerificationReport]] = []
    for event in events:
        if event.event_type != EventType.VERIFICATION_RECORDED:
            continue
        report_payload = event.payload.get("verification_report")
        if not isinstance(report_payload, dict):
            continue
        reports.append((event.event_seq, _verification_report_from_payload(report_payload)))
    return reports


def _recorded_verification_events_for_binding(
    *, verification_events: Iterable[LedgerEvent], ledger_events: Iterable[LedgerEvent]
) -> tuple[LedgerEvent, ...]:
    ledger_events_by_id = {event.event_id: event for event in ledger_events}
    recorded_events: list[LedgerEvent] = []
    for event in verification_events:
        recorded_event = ledger_events_by_id.get(event.event_id)
        if (
            recorded_event is None
            or recorded_event.event_type != EventType.VERIFICATION_RECORDED
            or event.event_type != EventType.VERIFICATION_RECORDED
            or recorded_event.event_seq != event.event_seq
            or recorded_event.event_hash != event.event_hash
        ):
            raise ValueError("recorded verification event is required for canonical binding")
        recorded_events.append(recorded_event)
    return tuple(recorded_events)


def _find_selected_verification_report(
    *, events: Iterable[LedgerEvent], canonical_selection: CanonicalSelection
) -> VerificationReport:
    for event in events:
        if event.event_seq != canonical_selection.selected_verification_event_seq:
            continue
        if event.event_type != EventType.VERIFICATION_RECORDED:
            raise ValueError("selected verification report mismatch")
        report_payload = event.payload.get("verification_report")
        if not isinstance(report_payload, dict):
            raise ValueError("selected verification report missing for completion evidence")
        report = _verification_report_from_payload(report_payload)
        _validate_selected_report_against_canonical_selection(
            report=report,
            canonical_selection=canonical_selection,
        )
        return report
    raise ValueError("selected verification report missing for completion evidence")


def _validate_selected_report_against_canonical_selection(
    *, report: VerificationReport, canonical_selection: CanonicalSelection
) -> None:
    if (
        report.verification_report_id
        != canonical_selection.selected_verification_report_id
        or report.task_id != canonical_selection.task_id
        or report.unit_id != canonical_selection.unit_id
        or report.submission_id != canonical_selection.selected_submission_id
        or report.submission_event_seq
        != canonical_selection.selected_submission_event_seq
        or report.attempt_id != canonical_selection.selected_attempt_id
        or report.candidate_output_bundle_digest
        != canonical_selection.canonical_output_bundle_digest
        or report.candidate_output_refs != canonical_selection.canonical_output_refs
    ):
        raise ValueError("selected verification report mismatch")


def _verification_report_from_payload(payload: JsonObject) -> VerificationReport:
    return VerificationReport(
        verification_report_id=payload["verification_report_id"],
        task_id=payload["task_id"],
        unit_id=payload["unit_id"],
        attempt_id=payload["attempt_id"],
        submission_id=payload["submission_id"],
        submission_event_seq=int(payload["submission_event_seq"]),
        candidate_output_bundle_digest=payload["candidate_output_bundle_digest"],
        candidate_output_refs=_artifact_refs_from_dict(payload["candidate_output_refs"]),
        required_output_names=list(payload["required_output_names"]),
        output_contract_id=payload["output_contract_id"],
        validator_policy_id=payload["validator_policy_id"],
        plugin_id=payload["plugin_id"],
        plugin_version=payload["plugin_version"],
        plugin_descriptor_digest=payload["plugin_descriptor_digest"],
        status=payload["status"],
        eligible_for_canonical=bool(payload["eligible_for_canonical"]),
        layer_results=dict(payload["layer_results"]),
        failure_summary=(
            dict(payload["failure_summary"]) if payload.get("failure_summary") else None
        ),
        verification_environment=dict(payload["verification_environment"]),
        verifier=dict(payload["verifier"]),
        started_at=payload["started_at"],
        completed_at=payload["completed_at"],
        metadata=dict(payload.get("metadata", {})),
        schema_version=payload.get("schema_version", "phase4.verification_report.v1"),
    )


def _existing_canonical_event(
    *, events: Iterable[LedgerEvent], task_id: str, unit_id: str
) -> LedgerEvent | None:
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        if event.payload.get("task_id") == task_id and event.payload.get("unit_id") == unit_id:
            return event
    return None


def _canonical_selection_from_payload(payload: JsonObject) -> CanonicalSelection:
    return CanonicalSelection(
        canonical_selection_id=payload["canonical_selection_id"],
        task_id=payload["task_id"],
        unit_id=payload["unit_id"],
        selection_policy=payload["selection_policy"],
        selection_policy_version=payload["selection_policy_version"],
        selected_verification_report_id=payload["selected_verification_report_id"],
        selected_verification_event_seq=int(payload["selected_verification_event_seq"]),
        selected_submission_id=payload["selected_submission_id"],
        selected_submission_event_seq=int(payload["selected_submission_event_seq"]),
        selected_attempt_id=payload["selected_attempt_id"],
        canonical_output_bundle_digest=payload["canonical_output_bundle_digest"],
        canonical_output_refs=_artifact_refs_from_dict(payload["canonical_output_refs"]),
        eligible_report_ids_considered=list(payload["eligible_report_ids_considered"]),
        selection_reason=payload["selection_reason"],
        bound_at=payload["bound_at"],
        metadata=dict(payload.get("metadata", {})),
        schema_version=payload.get("schema_version", "phase4.canonical_selection.v1"),
    )


def _same_canonical_commitment(
    existing: CanonicalSelection, candidate: CanonicalSelection
) -> bool:
    return (
        existing.task_id == candidate.task_id
        and existing.unit_id == candidate.unit_id
        and existing.selection_policy == candidate.selection_policy
        and existing.selection_policy_version == candidate.selection_policy_version
        and existing.selected_verification_report_id
        == candidate.selected_verification_report_id
        and existing.selected_verification_event_seq
        == candidate.selected_verification_event_seq
        and existing.selected_submission_id == candidate.selected_submission_id
        and existing.selected_submission_event_seq
        == candidate.selected_submission_event_seq
        and existing.selected_attempt_id == candidate.selected_attempt_id
        and existing.canonical_output_bundle_digest
        == candidate.canonical_output_bundle_digest
        and existing.canonical_output_refs == candidate.canonical_output_refs
    )


def _split_invocation_summary(invocation: SplitStrategyInvocation) -> JsonObject:
    return {
        "schema_version": invocation.schema_version,
        "invocation_id": invocation.invocation_id,
        "invocation_attempt_no": invocation.invocation_attempt_no,
        "expansion_scope_hash": invocation.expansion_scope_hash,
        "task_id": invocation.task_id,
        "unit_id": invocation.unit_id,
        "canonical_selection_id": invocation.canonical_selection_id,
        "canonical_output_bundle_digest": invocation.canonical_output_bundle_digest,
        "plugin_id": invocation.plugin_id,
        "plugin_version": invocation.plugin_version,
        "plugin_descriptor_digest": invocation.plugin_descriptor_digest,
        "split_strategy_id": invocation.split_strategy_id,
        "split_strategy_params_digest": invocation.split_strategy_params_digest,
        "status": invocation.status,
        "result_action": invocation.result_action,
        "result_digest": invocation.result_digest,
        "error_kind": invocation.error_kind,
        "error_summary": invocation.error_summary,
        "started_at": invocation.started_at,
        "completed_at": invocation.completed_at,
    }


def _validate_task_unit_for_complete_decision(
    *, decision: ExpansionDecision, task_unit: TaskUnit
) -> None:
    if decision.task_id != task_unit.task_id or decision.unit_id != task_unit.unit_id:
        raise ValueError("complete decision does not match task unit")
    if task_unit.state != TaskState.PROCESSING:
        raise ValueError("expansion decision requires Processing task unit")


def _validate_accepted_decision_prerequisites(
    *,
    decision: ExpansionDecision,
    events: Iterable[LedgerEvent],
    artifact_store: ArtifactStore,
) -> JsonObject:
    event_list = list(events)
    invocation_event = _find_split_invocation_event(
        events=event_list,
        invocation_id=decision.source_invocation_id,
    )
    if invocation_event is None:
        raise ValueError("missing source invocation for expansion decision")

    invocation = _split_invocation_payload(invocation_event)
    _validate_decision_against_invocation(decision=decision, invocation=invocation)

    canonical_event = _find_canonical_event(
        events=event_list,
        canonical_selection_id=decision.canonical_selection_id,
    )
    if canonical_event is None:
        raise ValueError("missing canonical selection for expansion decision")
    canonical_selection = _canonical_selection_from_payload(
        canonical_event.payload["canonical_selection"]
    )
    _validate_decision_against_canonical_selection(
        decision=decision,
        invocation=invocation,
        canonical_selection=canonical_selection,
    )

    descriptor = _load_frozen_plugin_descriptor(
        events=event_list,
        artifact_store=artifact_store,
        plugin_id=decision.plugin_id,
        plugin_version=decision.plugin_version,
        plugin_descriptor_digest=decision.plugin_descriptor_digest,
    )
    split_strategies = descriptor.get("split_strategies", {})
    if decision.split_strategy_id not in split_strategies:
        raise ValueError(
            f"split strategy id not declared in descriptor: {decision.split_strategy_id}"
        )
    if decision.action == "complete":
        selected_report = _find_selected_verification_report(
            events=event_list,
            canonical_selection=canonical_selection,
        )
        _validate_complete_action_body(
            decision=decision,
            canonical_selection=canonical_selection,
            selected_verification_report=selected_report,
        )
    return {
        "invocation_id": invocation["invocation_id"],
        "canonical_selection_id": canonical_selection.canonical_selection_id,
        "canonical_selection": canonical_selection,
        "descriptor": descriptor,
        "split_strategy": split_strategies[decision.split_strategy_id],
    }


def _find_split_invocation_event(
    *, events: Iterable[LedgerEvent], invocation_id: str
) -> LedgerEvent | None:
    for event in events:
        if event.event_type != EventType.SPLIT_STRATEGY_INVOCATION_RECORDED:
            continue
        if event.object_id == invocation_id:
            return event
    return None


def _split_invocation_payload(event: LedgerEvent) -> JsonObject:
    payload = event.payload
    invocation = payload.get("invocation")
    if isinstance(invocation, dict):
        merged = dict(invocation)
        for key in (
            "task_id",
            "unit_id",
            "canonical_selection_id",
            "canonical_output_bundle_digest",
            "plugin_id",
            "plugin_version",
            "plugin_descriptor_digest",
            "split_strategy_id",
            "split_strategy_params_digest",
            "expansion_scope_hash",
            "status",
            "result_action",
            "result_digest",
            "error_kind",
            "error_summary",
            "started_at",
            "completed_at",
        ):
            if key in payload:
                merged[key] = payload[key]
        return merged
    return dict(payload)


def _validate_decision_against_invocation(
    *, decision: ExpansionDecision, invocation: JsonObject
) -> None:
    if invocation.get("status") != "succeeded":
        raise ValueError("source invocation must be succeeded before accepted decision")
    if invocation.get("result_action") != decision.action:
        raise ValueError("source invocation result_action mismatch")
    _require_same(invocation, "task_id", decision.task_id, "task")
    _require_same(invocation, "unit_id", decision.unit_id, "unit")
    _require_same(
        invocation,
        "expansion_scope_hash",
        decision.expansion_scope_hash,
        "scope",
    )
    _require_same(
        invocation,
        "canonical_selection_id",
        decision.canonical_selection_id,
        "canonical selection",
    )
    _require_same(
        invocation,
        "canonical_output_bundle_digest",
        decision.canonical_output_bundle_digest,
        "canonical bundle digest",
    )
    _require_same(invocation, "plugin_id", decision.plugin_id, "plugin")
    _require_same(invocation, "plugin_version", decision.plugin_version, "plugin")
    _require_same(
        invocation,
        "plugin_descriptor_digest",
        decision.plugin_descriptor_digest,
        "descriptor",
    )
    _require_same(
        invocation,
        "split_strategy_id",
        decision.split_strategy_id,
        "strategy",
    )
    _require_same(
        invocation,
        "split_strategy_params_digest",
        decision.split_strategy_params_digest,
        "split strategy params digest",
    )


def _find_canonical_event(
    *, events: Iterable[LedgerEvent], canonical_selection_id: str
) -> LedgerEvent | None:
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        if event.object_id == canonical_selection_id:
            return event
        selection = event.payload.get("canonical_selection")
        if (
            isinstance(selection, dict)
            and selection.get("canonical_selection_id") == canonical_selection_id
        ):
            return event
    return None


def _validate_decision_against_canonical_selection(
    *,
    decision: ExpansionDecision,
    invocation: JsonObject,
    canonical_selection: CanonicalSelection,
) -> None:
    if canonical_selection.task_id != decision.task_id:
        raise ValueError("canonical selection task mismatch")
    if canonical_selection.unit_id != decision.unit_id:
        raise ValueError("canonical selection unit mismatch")
    if canonical_selection.canonical_selection_id != decision.canonical_selection_id:
        raise ValueError("canonical selection mismatch")
    if (
        canonical_selection.canonical_output_bundle_digest
        != decision.canonical_output_bundle_digest
    ):
        raise ValueError("canonical output bundle digest mismatch")
    if (
        invocation.get("canonical_output_bundle_digest")
        != canonical_selection.canonical_output_bundle_digest
    ):
        raise ValueError("canonical output bundle digest mismatch")


def _load_frozen_plugin_descriptor(
    *,
    events: Iterable[LedgerEvent],
    artifact_store: ArtifactStore,
    plugin_id: str,
    plugin_version: str,
    plugin_descriptor_digest: str,
) -> JsonObject:
    seen_same_plugin = False
    for event in events:
        if event.event_type != EventType.REGISTRY_SNAPSHOT_RECORDED:
            continue
        for entry in event.payload.get("plugin_entries", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("plugin_id") != plugin_id or entry.get("plugin_version") != plugin_version:
                continue
            seen_same_plugin = True
            if entry.get("descriptor_digest") != plugin_descriptor_digest:
                continue
            descriptor_ref_data = entry.get("descriptor_ref")
            if not isinstance(descriptor_ref_data, dict):
                raise ValueError("descriptor ref missing from registry snapshot")
            descriptor_ref = ArtifactRef.from_dict(descriptor_ref_data)
            if not artifact_store.verify(descriptor_ref):
                raise ValueError("descriptor artifact digest mismatch")
            descriptor = json.loads(artifact_store.read_bytes(descriptor_ref).decode("utf-8"))
            if descriptor.get("descriptor_digest") != plugin_descriptor_digest:
                raise ValueError("descriptor digest mismatch")
            if descriptor.get("plugin_id") != plugin_id or descriptor.get("plugin_version") != plugin_version:
                raise ValueError("descriptor plugin identity mismatch")
            return descriptor
    if seen_same_plugin:
        raise ValueError("descriptor digest mismatch")
    raise ValueError("descriptor not found for plugin/version")


def _validate_complete_action_body(
    *,
    decision: ExpansionDecision,
    canonical_selection: CanonicalSelection,
    selected_verification_report: VerificationReport,
) -> None:
    evidence = decision.action_body.get("completion_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("complete decision requires inline completion_evidence")
    required_fields = {
        "completion_kind",
        "validator_policy_id",
        "verification_report_id",
        "canonical_selection_id",
        "canonical_output_bundle_digest",
        "completed_output_refs",
        "plugin_completion_summary",
    }
    missing = sorted(required_fields.difference(evidence))
    if missing:
        raise ValueError("completion_evidence missing required fields: " + ", ".join(missing))
    if evidence["canonical_selection_id"] != decision.canonical_selection_id:
        raise ValueError("completion_evidence canonical selection mismatch")
    if evidence["canonical_output_bundle_digest"] != decision.canonical_output_bundle_digest:
        raise ValueError("completion_evidence canonical bundle mismatch")
    if (
        selected_verification_report.verification_report_id
        != canonical_selection.selected_verification_report_id
    ):
        raise ValueError("selected verification report mismatch")
    if evidence["verification_report_id"] != canonical_selection.selected_verification_report_id:
        raise ValueError("completion_evidence verification report mismatch")
    if evidence["validator_policy_id"] != selected_verification_report.validator_policy_id:
        raise ValueError("completion_evidence validator policy mismatch")
    if (
        _artifact_refs_from_dict(evidence["completed_output_refs"])
        != canonical_selection.canonical_output_refs
    ):
        raise ValueError("completion_evidence completed output refs mismatch")


def _expansion_decision_payload(*, decision: ExpansionDecision) -> JsonObject:
    decision_dict = decision.to_dict()
    return {
        "schema_version": "phase4.expansion_decision_record.v1",
        "expansion_decision": decision_dict,
        "expansion_decision_digest": digest_json(decision_dict),
        "task_id": decision.task_id,
        "unit_id": decision.unit_id,
        "canonical_selection_id": decision.canonical_selection_id,
        "canonical_output_bundle_digest": decision.canonical_output_bundle_digest,
        "expansion_scope_hash": decision.expansion_scope_hash,
        "action": decision.action,
        "source_invocation_id": decision.source_invocation_id,
        "proposal_id": decision.proposal_id,
        "proposal_digest": decision.proposal_digest,
        "merge_plan_id": decision.merge_plan_id,
        "merge_plan_digest": decision.merge_plan_digest,
        "action_body": decision.action_body,
        "decided_at": decision.decided_at,
    }


def _validate_expand_documents(
    *,
    decision: ExpansionDecision,
    proposal: DecompositionProposal,
    merge_plan: MergePlan,
    parent_unit: TaskUnit,
    graph: TaskGraph,
    canonical_selection: CanonicalSelection,
    split_strategy: JsonObject,
    descriptor: JsonObject,
) -> None:
    if parent_unit.unit_id not in graph.units:
        raise ValueError("parent unit missing from task graph")
    if graph.units[parent_unit.unit_id].task_id != parent_unit.task_id:
        raise ValueError("parent unit graph entry mismatch")

    header = proposal.proposal_header
    _require_same(header, "proposal_id", decision.proposal_id, "proposal")
    _require_same(header, "task_id", decision.task_id, "task")
    _require_same(header, "parent_unit_id", decision.unit_id, "unit")
    _require_same(
        header,
        "canonical_selection_id",
        decision.canonical_selection_id,
        "canonical selection",
    )
    _require_same(
        header,
        "canonical_output_bundle_digest",
        decision.canonical_output_bundle_digest,
        "canonical bundle digest",
    )
    _require_same(header, "plugin_id", decision.plugin_id, "plugin")
    _require_same(header, "plugin_version", decision.plugin_version, "plugin")
    _require_same(
        header,
        "plugin_descriptor_digest",
        decision.plugin_descriptor_digest,
        "descriptor",
    )
    _require_same(header, "split_strategy_id", decision.split_strategy_id, "strategy")
    _require_same(
        header,
        "split_strategy_params_digest",
        decision.split_strategy_params_digest,
        "split strategy params digest",
    )
    _require_same(header, "expansion_scope_hash", decision.expansion_scope_hash, "scope")
    _require_same(header, "proposal_digest", decision.proposal_digest, "proposal")
    if digest_decomposition_proposal_body(proposal) != decision.proposal_digest:
        raise ValueError("proposal body digest mismatch")
    _validate_expand_evidence_against_documents(
        decision=decision,
        proposal=proposal,
        merge_plan=merge_plan,
    )

    merge_header = merge_plan.merge_plan_header
    _require_same(merge_header, "merge_plan_id", decision.merge_plan_id, "merge plan")
    _require_same(merge_header, "task_id", decision.task_id, "task")
    _require_same(merge_header, "parent_unit_id", decision.unit_id, "unit")
    _require_same(
        merge_header,
        "canonical_selection_id",
        decision.canonical_selection_id,
        "canonical selection",
    )
    _require_same(
        merge_header,
        "decomposition_proposal_id",
        decision.proposal_id,
        "proposal",
    )
    _require_same(
        merge_header,
        "expansion_decision_id",
        decision.expansion_decision_id,
        "expansion decision",
    )
    _require_same(merge_header, "created_by_plugin_id", decision.plugin_id, "plugin")
    _require_same(
        merge_header,
        "created_by_plugin_version",
        decision.plugin_version,
        "plugin",
    )
    _require_same(merge_header, "merge_plan_digest", decision.merge_plan_digest, "merge plan")
    if digest_merge_plan_body(merge_plan) != decision.merge_plan_digest:
        raise ValueError("merge plan body digest mismatch")

    merge_policy_ref = merge_plan.merge_policy_ref
    _require_same(merge_policy_ref, "plugin_id", decision.plugin_id, "plugin")
    _require_same(merge_policy_ref, "plugin_version", decision.plugin_version, "plugin")
    _require_same(
        merge_policy_ref,
        "merge_policy_descriptor_digest",
        decision.plugin_descriptor_digest,
        "merge policy descriptor",
    )
    _require_same(
        merge_policy_ref,
        "merge_policy_id",
        split_strategy.get("merge_policy_id"),
        "merge policy",
    )

    allowed_unit_types = set(split_strategy.get("allowed_unit_types", []))
    for child_spec in proposal.child_specs:
        if child_spec.get("unit_type") not in allowed_unit_types:
            raise ValueError("child unit_type not allowed by split strategy")

    validate_decomposition_proposal_limits(
        proposal,
        protocol_config=graph.protocol_config or ProtocolConfig.default(
            config_id="config_from_engine",
            artifact_store_uri="",
            event_log_uri="",
        ),
        parent_depth=parent_unit.depth,
        existing_unit_count=len(graph.units),
        parent_required_output_names=list(canonical_selection.canonical_output_refs),
        max_children_per_strategy=split_strategy.get("max_children_per_expansion"),
    )
    if descriptor.get("descriptor_digest") != decision.plugin_descriptor_digest:
        raise ValueError("descriptor digest mismatch")


def _validate_expand_evidence_against_documents(
    *,
    decision: ExpansionDecision,
    proposal: DecompositionProposal,
    merge_plan: MergePlan,
) -> None:
    evidence = decision.action_body.get("expand_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("expand_evidence missing")
    expected_counts = {
        "child_count": len(proposal.child_specs),
        "relation_count": len(proposal.dependency_edges),
        "expected_output_count": len(proposal.expected_outputs),
        "required_merge_slot_count": len(merge_plan.required_slots),
    }
    for field_name, expected in expected_counts.items():
        if evidence.get(field_name) != expected:
            raise ValueError(f"expand_evidence {field_name} mismatch")


def _build_expansion_child_units(
    *,
    decision: ExpansionDecision,
    proposal: DecompositionProposal,
    parent_unit: TaskUnit,
    graph: TaskGraph,
    parent_canonical_output_refs: dict[str, ArtifactRef],
) -> tuple[TaskUnit, ...]:
    child_units: list[TaskUnit] = []
    for child_spec in proposal.child_specs:
        child_key = child_spec["child_logical_key"]
        input_refs = _child_input_refs(
            child_spec=child_spec,
            parent_canonical_output_refs=parent_canonical_output_refs,
        )
        initial_state = derive_child_initial_state(
            proposal=proposal,
            child_logical_key=child_key,
            graph=graph,
            parent_canonical_output_refs=parent_canonical_output_refs,
        )
        child_units.append(
            TaskUnit(
                unit_id=_derive_child_unit_id(
                    proposal_digest=decision.proposal_digest or "",
                    parent_unit_id=parent_unit.unit_id,
                    child_logical_key=child_key,
                ),
                task_id=decision.task_id,
                parent_unit_id=parent_unit.unit_id,
                depth=parent_unit.depth + 1,
                unit_type=child_spec["unit_type"],
                state=initial_state,
                input_refs=input_refs,
                canonical_output_refs={},
                required_capabilities=dict(child_spec["required_capabilities"]),
                weight=float(child_spec["weight"]),
                budget_limit=child_spec.get("budget_limit"),
                deadline=child_spec.get("deadline"),
                plugin_payload=dict(child_spec["plugin_payload"]),
                metadata={
                    "child_logical_key": child_key,
                    "source_proposal_id": decision.proposal_id,
                    "source_expansion_decision_id": decision.expansion_decision_id,
                    "canonical_selection_id": decision.canonical_selection_id,
                    "required_outputs": list(child_spec["required_outputs"]),
                    "output_contract_refs": dict(child_spec["output_contract_refs"]),
                    "validator_policy_id": child_spec["validator_policy_id"],
                },
                created_at=decision.decided_at,
                updated_at=decision.decided_at,
            )
        )
    return tuple(child_units)


def _child_input_refs(
    *,
    child_spec: JsonObject,
    parent_canonical_output_refs: dict[str, ArtifactRef],
) -> dict[str, ArtifactRef]:
    input_refs: dict[str, ArtifactRef] = {}
    for input_name, binding in child_spec.get("input_bindings", {}).items():
        binding_kind = binding.get("kind")
        if binding_kind == "parent_output":
            output_name = binding.get("output_name")
            if output_name not in parent_canonical_output_refs:
                raise ValueError("parent output binding missing canonical output")
            input_refs[input_name] = parent_canonical_output_refs[output_name]
        elif binding_kind == "artifact_ref":
            artifact_ref_data = binding.get("artifact_ref")
            if not isinstance(artifact_ref_data, dict):
                raise ValueError("artifact_ref binding requires artifact_ref")
            input_refs[input_name] = ArtifactRef.from_dict(artifact_ref_data)
        elif binding_kind in {"constant", "dependency_output", "child_output"}:
            continue
        else:
            raise ValueError(f"unsupported child input binding kind: {binding_kind}")
    return input_refs


def _build_expansion_relations(
    *,
    decision: ExpansionDecision,
    proposal: DecompositionProposal,
    child_unit_ids_by_key: dict[str, str],
) -> tuple[TaskRelation, ...]:
    relations: list[TaskRelation] = []
    for edge in proposal.dependency_edges:
        source_key = edge["source_child_key"]
        target_key = edge["target_child_key"]
        relations.append(
            TaskRelation(
                relation_id=_derive_relation_id(
                    proposal_digest=decision.proposal_digest or "",
                    source_child_key=source_key,
                    target_child_key=target_key,
                    source_output_name=edge["source_output_name"],
                    target_input_name=edge["target_input_name"],
                ),
                task_id=decision.task_id,
                relation_type=edge["relation_type"],
                source_unit_id=child_unit_ids_by_key[source_key],
                target_unit_id=child_unit_ids_by_key[target_key],
                source_output_name=edge["source_output_name"],
                target_input_name=edge["target_input_name"],
                created_reason="expansion_decision_accepted",
                metadata={
                    "edge_logical_key": edge["edge_logical_key"],
                    "source_proposal_id": decision.proposal_id,
                    "source_expansion_decision_id": decision.expansion_decision_id,
                },
                created_at=decision.decided_at,
            )
        )
    return tuple(relations)


def _validate_merge_plan_child_ids(
    *, merge_plan: MergePlan, child_unit_ids_by_key: dict[str, str]
) -> None:
    for slot in merge_plan.required_slots:
        child_key = slot.get("source_child_logical_key")
        if child_key not in child_unit_ids_by_key:
            raise ValueError("merge plan slot child logical key mismatch")
        if slot.get("source_child_unit_id") != child_unit_ids_by_key[child_key]:
            raise ValueError("merge plan slot child unit id mismatch")


def _final_event_seq_for_batch(
    *, events: Iterable[LedgerEvent], batch_id: str, fallback_seq: int
) -> int:
    for event in events:
        if event.batch_id == batch_id and event.event_type == EventType.TASK_EXPANDED:
            return event.event_seq
    return fallback_seq


def _derive_child_unit_id(
    *, proposal_digest: str, parent_unit_id: str, child_logical_key: str
) -> str:
    stable_suffix = _stable_id_component(proposal_digest.removeprefix("sha256:"))
    return f"unit_{_stable_id_component(parent_unit_id)}_{stable_suffix}_{_stable_id_component(child_logical_key)}"


def _derive_relation_id(
    *,
    proposal_digest: str,
    source_child_key: str,
    target_child_key: str,
    source_output_name: str,
    target_input_name: str,
) -> str:
    stable_suffix = _stable_id_component(proposal_digest.removeprefix("sha256:"))
    return (
        f"relation_{stable_suffix}_{_stable_id_component(source_child_key)}_"
        f"{_stable_id_component(target_child_key)}_"
        f"{_stable_id_component(source_output_name)}_"
        f"{_stable_id_component(target_input_name)}"
    )


def _stable_id_component(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value)


def _require_same(
    source: JsonObject, source_key: str, expected: object, label: str
) -> None:
    if source.get(source_key) != expected:
        raise ValueError(f"{label} mismatch")


def _first_event_id_for_batch(
    *, events: Iterable[LedgerEvent], batch_id: str
) -> str | None:
    for event in events:
        if event.batch_id == batch_id and event.batch_index == 1:
            return event.event_id
    return None


def _artifact_refs_from_dict(data: JsonObject) -> dict[str, ArtifactRef]:
    return {
        output_name: ArtifactRef.from_dict(ref_data)
        for output_name, ref_data in data.items()
    }


def _artifact_refs_to_dict(refs: dict[str, ArtifactRef]) -> JsonObject:
    return {output_name: ref.to_dict() for output_name, ref in refs.items()}


def _verification_failure_message(report: VerificationReport) -> str:
    if report.failure_summary:
        message = report.failure_summary.get("message")
        if isinstance(message, str) and message:
            return message
    return "verification rejected"


def _registry_plugin_entry_summary(entry: JsonObject) -> JsonObject:
    return {
        "plugin_id": entry.get("plugin_id"),
        "plugin_version": entry.get("plugin_version"),
        "descriptor_digest": entry.get("descriptor_digest"),
        "descriptor_ref": entry.get("descriptor_ref"),
        "supported_task_types": list(entry.get("supported_task_types", [])),
    }


def _registry_executor_entry_summary(entry: JsonObject) -> JsonObject:
    return {
        "executor_id": entry.get("executor_id"),
        "executor_type": entry.get("executor_type"),
        "executor_version": entry.get("executor_version"),
        "descriptor_digest": entry.get("descriptor_digest"),
        "descriptor_ref": entry.get("descriptor_ref"),
        "status": entry.get("status"),
    }
