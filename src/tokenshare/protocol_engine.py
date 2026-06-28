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
from tokenshare.core.contribution import (
    ContributionRecord,
    ContributionState,
    SettlementEntry,
    SettlementRecord,
    SubtreePruneRecord,
    build_sandbox_equal_weight_settlement_entries,
    digest_settlement_entries,
    transition_contribution,
)
from tokenshare.core.leases import LeaseManager
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord
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


@dataclass(frozen=True)
class MergeResolutionFlowResult:
    merge_record: MergeRecord
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...]
    events: tuple[LedgerEvent, ...]


@dataclass(frozen=True)
class ParentCompletionFlowResult:
    task_unit: TaskUnit
    resolved_output_set_digest: str
    expand_contributions: tuple[ContributionRecord, ...]
    events: tuple[LedgerEvent, ...]


@dataclass(frozen=True)
class SettlementFlowResult:
    settlement_record: SettlementRecord
    settlement_entries: tuple[SettlementEntry, ...]
    settled_contributions: tuple[ContributionRecord, ...]
    events: tuple[LedgerEvent, ...]


@dataclass(frozen=True)
class SubtreePruningFlowResult:
    subtree_prune_record: SubtreePruneRecord | None
    cancelled_units: tuple[str, ...]
    preserved_completed_unit_count: int
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

    def record_merge_resolution(
        self,
        *,
        merge_record: MergeRecord,
        expected_output_resolutions: list[ExpectedOutputResolution],
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> MergeResolutionFlowResult:
        """Record merge commitment and parent expected-output resolutions atomically."""

        resolutions = tuple(expected_output_resolutions)
        current_events = self._event_ledger.read_all()
        artifact_store = self._require_artifact_store()
        _reject_incomplete_merge_resolution_batch(
            events=current_events,
            merge_record=merge_record,
        )
        _reject_existing_merge_record_conflict(
            events=current_events,
            merge_record=merge_record,
        )
        canonical_event = _validate_merge_resolution_prerequisites(
            events=current_events,
            artifact_store=artifact_store,
            merge_record=merge_record,
            expected_output_resolutions=resolutions,
        )

        drafts = _merge_resolution_drafts(
            merge_record=merge_record,
            expected_output_resolutions=resolutions,
            correlation_id=correlation_id,
            causation_event_id=causation_event_id or canonical_event.event_id,
        )
        batch_events = self._event_ledger.append_batch(
            list(drafts),
            batch_id=f"merge_resolution_batch:{merge_record.merge_record_id}",
        )
        return MergeResolutionFlowResult(
            merge_record=merge_record,
            expected_output_resolutions=resolutions,
            events=tuple(batch_events),
        )

    def record_parent_completion(
        self,
        *,
        owner_unit: TaskUnit,
        expected_output_refs: list[ExpectedOutputRef],
        expected_output_resolutions: list[ExpectedOutputResolution],
        expand_contributions: list[ContributionRecord],
        now: str,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> ParentCompletionFlowResult:
        """Complete an expanded owner after every required expected output resolves."""

        refs = tuple(expected_output_refs)
        resolutions = tuple(expected_output_resolutions)
        contributions = tuple(expand_contributions)
        current_events = self._event_ledger.read_all()
        resolved_output_set_digest = _resolved_output_set_digest(resolutions)
        _reject_parent_completion_conflict(
            events=current_events,
            owner_unit_id=owner_unit.unit_id,
            resolved_output_set_digest=resolved_output_set_digest,
        )
        _validate_parent_completion_resolutions(
            owner_unit=owner_unit,
            expected_output_refs=refs,
            expected_output_resolutions=resolutions,
            events=current_events,
        )
        completed_unit = transition_task_unit(
            owner_unit,
            new_state=TaskState.COMPLETED,
            reason="required_expected_outputs_resolved",
            trigger="parent_completion_batch",
            changed_at=now,
        )
        eligible_contributions = _eligible_parent_completion_contributions(
            owner_unit=owner_unit,
            expected_output_refs=refs,
            expand_contributions=contributions,
            now=now,
        )
        batch_id = (
            f"parent_completion_batch:{owner_unit.unit_id}:"
            f"{resolved_output_set_digest}"
        )
        first_batch_event_id = _first_event_id_for_batch(
            events=current_events,
            batch_id=batch_id,
        ) or f"event_{len(current_events) + 1:012d}"
        drafts = _parent_completion_drafts(
            owner_unit=owner_unit,
            completed_unit=completed_unit,
            eligible_contributions=eligible_contributions,
            resolved_output_set_digest=resolved_output_set_digest,
            now=now,
            correlation_id=correlation_id,
            causation_event_id=causation_event_id or first_batch_event_id,
            batch_id=batch_id,
        )
        batch_events = self._event_ledger.append_batch(list(drafts), batch_id=batch_id)
        return ParentCompletionFlowResult(
            task_unit=completed_unit,
            resolved_output_set_digest=resolved_output_set_digest,
            expand_contributions=eligible_contributions,
            events=tuple(batch_events),
        )

    def record_root_settlement(
        self,
        *,
        task_id: str,
        root_unit_id: str,
        root_completion_event_seq: int,
        eligible_contributions: list[ContributionRecord],
        root_budget: int,
        settlement_policy_id: str,
        now: str,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> SettlementFlowResult:
        """Settle every eligible contribution for one completed root in one batch."""

        settlement_policy_version = "v1"
        scale = "1"
        artifact_store = self._require_artifact_store()
        current_events = self._event_ledger.read_all()
        batch_id = (
            f"settlement_batch:{task_id}:{root_unit_id}:"
            f"{root_completion_event_seq}"
        )
        supplied_eligible = _eligible_settlement_contributions(
            contributions=tuple(eligible_contributions),
            task_id=task_id,
            root_completion_event_seq=root_completion_event_seq,
        )

        existing = _existing_root_settlement(
            events=current_events,
            artifact_store=artifact_store,
            batch_id=batch_id,
            task_id=task_id,
            root_unit_id=root_unit_id,
            root_completion_event_seq=root_completion_event_seq,
        )
        if existing is not None:
            expected_entries = tuple(
                build_sandbox_equal_weight_settlement_entries(
                    task_id=task_id,
                    root_unit_id=root_unit_id,
                    root_completion_event_seq=root_completion_event_seq,
                    eligible_contributions=list(supplied_eligible),
                    root_budget=root_budget,
                    settlement_policy_id=settlement_policy_id,
                    settlement_policy_version=settlement_policy_version,
                    scale=scale,
                    created_at=now,
                )
            )
            _validate_existing_settlement_matches_request(
                existing=existing,
                expected_entries=expected_entries,
                settlement_policy_id=settlement_policy_id,
                settlement_policy_version=settlement_policy_version,
                root_budget=root_budget,
                scale=scale,
            )
            return existing

        _validate_root_completion_event(
            events=current_events,
            task_id=task_id,
            root_unit_id=root_unit_id,
            root_completion_event_seq=root_completion_event_seq,
        )
        recorded_eligible = _eligible_contributions_from_ledger(
            events=current_events,
            task_id=task_id,
            root_completion_event_seq=root_completion_event_seq,
        )
        supplied_eligible_ids = {
            contribution.contribution_id for contribution in supplied_eligible
        }
        recorded_eligible_ids = {
            contribution.contribution_id for contribution in recorded_eligible
        }
        if not recorded_eligible_ids.issubset(supplied_eligible_ids):
            raise ValueError("partial settlement: must settle all eligible contributions")
        if supplied_eligible_ids != recorded_eligible_ids:
            raise ValueError("eligible contribution set mismatch")

        settlement_entries = tuple(
            build_sandbox_equal_weight_settlement_entries(
                task_id=task_id,
                root_unit_id=root_unit_id,
                root_completion_event_seq=root_completion_event_seq,
                eligible_contributions=list(recorded_eligible),
                root_budget=root_budget,
                settlement_policy_id=settlement_policy_id,
                settlement_policy_version=settlement_policy_version,
                scale=scale,
                created_at=now,
            )
        )
        settlement_entries_ref = artifact_store.save_json(
            [entry.to_dict() for entry in settlement_entries],
            artifact_id=(
                f"settlement_entries_{_stable_id_component(task_id)}_"
                f"{_stable_id_component(root_unit_id)}_"
                f"{root_completion_event_seq}"
            ),
            artifact_type="SettlementEntrySet",
            artifact_schema_id="phase5.settlement_entries",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={
                "task_id": task_id,
                "root_unit_id": root_unit_id,
                "root_completion_event_seq": root_completion_event_seq,
            },
            created_at=now,
        )
        settlement_record = SettlementRecord(
            settlement_record_id=(
                f"settlement:{task_id}:{root_unit_id}:{root_completion_event_seq}"
            ),
            task_id=task_id,
            root_unit_id=root_unit_id,
            root_completion_event_seq=root_completion_event_seq,
            settlement_policy_id=settlement_policy_id,
            settlement_policy_version=settlement_policy_version,
            root_budget=root_budget,
            scale=scale,
            total_reward=sum(entry.reward_units for entry in settlement_entries),
            entry_count=len(settlement_entries),
            settlement_entries_digest=digest_settlement_entries(
                list(settlement_entries)
            ),
            settlement_entries_ref=settlement_entries_ref.to_dict(),
            settlement_summary=_settlement_summary(settlement_entries),
            created_at=now,
        )
        contributions_by_id = {
            contribution.contribution_id: contribution
            for contribution in recorded_eligible
        }
        drafts = _settlement_drafts(
            settlement_record=settlement_record,
            settlement_entries=settlement_entries,
            contributions_by_id=contributions_by_id,
            now=now,
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
        )
        batch_events = self._event_ledger.append_batch(list(drafts), batch_id=batch_id)
        settled_contributions = _settled_contributions_from_events(batch_events)
        return SettlementFlowResult(
            settlement_record=settlement_record,
            settlement_entries=settlement_entries,
            settled_contributions=settled_contributions,
            events=tuple(batch_events),
        )

    def record_subtree_pruning(
        self,
        *,
        parent_unit_id: str,
        parent_completed_event_seq: int,
        candidate_descendant_units: list[TaskUnit],
        pruning_policy_ref: JsonObject,
        now: str,
        correlation_id: str,
        causation_event_id: str | None = None,
    ) -> SubtreePruningFlowResult:
        """Cancel unfinished descendant work after a parent has completed."""

        artifact_store = self._require_artifact_store()
        current_events = self._event_ledger.read_all()
        parent_completed_event = _validate_parent_completed_event(
            events=current_events,
            parent_unit_id=parent_unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
        )
        policy = _validated_pruning_policy_ref(
            pruning_policy_ref=pruning_policy_ref,
            events=current_events,
            artifact_store=artifact_store,
            task_id=parent_completed_event.task_id or "",
            parent_unit_id=parent_unit_id,
        )
        cancellable_units, preserved_count = _subtree_pruning_candidates(
            parent_unit_id=parent_unit_id,
            task_id=parent_completed_event.task_id or "",
            candidate_descendant_units=tuple(candidate_descendant_units),
            events=current_events,
        )
        batch_id = (
            f"subtree_pruning_batch:{parent_unit_id}:"
            f"{parent_completed_event_seq}"
        )
        expected_record = _subtree_prune_record(
            parent_completed_event=parent_completed_event,
            parent_unit_id=parent_unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
            policy=policy,
            cancellable_units=cancellable_units,
            preserved_completed_unit_count=preserved_count,
            now=now,
        )
        existing = _existing_subtree_pruning(
            events=current_events,
            batch_id=batch_id,
            parent_unit_id=parent_unit_id,
            parent_completed_event_seq=parent_completed_event_seq,
        )
        if existing is not None:
            _validate_existing_subtree_pruning_matches_request(
                existing=existing,
                expected_record=expected_record,
                expected_cancelled_unit_ids=tuple(
                    unit.unit_id for unit in cancellable_units
                ),
            )
            return existing
        if not cancellable_units:
            return SubtreePruningFlowResult(
                subtree_prune_record=None,
                cancelled_units=(),
                preserved_completed_unit_count=preserved_count,
                events=(),
            )

        drafts = _subtree_pruning_drafts(
            subtree_prune_record=expected_record,
            cancellable_units=cancellable_units,
            now=now,
            correlation_id=correlation_id,
            causation_event_id=causation_event_id or parent_completed_event.event_id,
        )
        batch_events = self._event_ledger.append_batch(list(drafts), batch_id=batch_id)
        return SubtreePruningFlowResult(
            subtree_prune_record=expected_record,
            cancelled_units=tuple(unit.unit_id for unit in cancellable_units),
            preserved_completed_unit_count=preserved_count,
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
    completed_refs = _artifact_refs_from_dict(evidence["completed_output_refs"])
    for output_name, canonical_ref in canonical_selection.canonical_output_refs.items():
        if completed_refs.get(output_name) != canonical_ref:
            raise ValueError(
                "completion_evidence must include canonical output refs"
            )


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
        parent_required_output_names=_parent_required_output_names(
            parent_unit=parent_unit,
            canonical_selection=canonical_selection,
        ),
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


def _parent_required_output_names(
    *,
    parent_unit: TaskUnit,
    canonical_selection: CanonicalSelection,
) -> list[str]:
    required_outputs = parent_unit.plugin_payload.get("required_outputs")
    if isinstance(required_outputs, list) and all(
        isinstance(item, str) and item for item in required_outputs
    ):
        return list(required_outputs)
    requested_outputs = parent_unit.plugin_payload.get("requested_outputs")
    if isinstance(requested_outputs, list) and all(
        isinstance(item, str) and item for item in requested_outputs
    ):
        return list(requested_outputs)
    requested_output = parent_unit.plugin_payload.get("requested_output")
    if isinstance(requested_output, str) and requested_output:
        return [requested_output]
    return list(canonical_selection.canonical_output_refs)


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


def _reject_incomplete_merge_resolution_batch(
    *, events: Iterable[LedgerEvent], merge_record: MergeRecord
) -> None:
    batch_id = f"merge_resolution_batch:{merge_record.merge_record_id}"
    relevant_events = [
        event
        for event in events
        if event.batch_id == batch_id
        or (
            event.event_type == EventType.MERGE_RECORDED
            and event.object_id == merge_record.merge_record_id
        )
    ]
    if not relevant_events:
        return
    if not _is_complete_merge_resolution_batch(
        events=events,
        batch_id=batch_id,
        merge_record_id=merge_record.merge_record_id,
    ):
        raise ValueError("projection inconsistent: incomplete merge_resolution_batch")


def _reject_existing_merge_record_conflict(
    *, events: Iterable[LedgerEvent], merge_record: MergeRecord
) -> None:
    for event in events:
        if event.event_type != EventType.MERGE_RECORDED:
            continue
        existing = event.payload.get("merge_record")
        if not isinstance(existing, dict):
            continue
        if existing.get("merge_plan_id") != merge_record.merge_plan_id:
            continue
        if existing != merge_record.to_dict():
            raise ValueError("merge record conflict for merge_plan_id")


def _validate_merge_resolution_prerequisites(
    *,
    events: Iterable[LedgerEvent],
    artifact_store: ArtifactStore,
    merge_record: MergeRecord,
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...],
) -> LedgerEvent:
    ledger_events = tuple(events)
    canonical_event = _merge_unit_canonical_event(
        events=ledger_events,
        merge_record=merge_record,
    )
    merge_task_link = _merge_task_link_payload(
        events=ledger_events,
        merge_record=merge_record,
    )
    parent_output_mapping = _validate_merge_input_bundle(
        artifact_store=artifact_store,
        merge_record=merge_record,
        merge_task_link=merge_task_link,
    )
    _validate_merge_record_against_link(
        merge_record=merge_record,
        merge_task_link=merge_task_link,
    )
    _validate_merge_record_against_canonical_event(
        merge_record=merge_record,
        canonical_event=canonical_event,
    )
    _validate_expected_output_resolutions(
        merge_record=merge_record,
        expected_output_resolutions=expected_output_resolutions,
        parent_output_mapping=parent_output_mapping,
    )
    return canonical_event


def _merge_unit_canonical_event(
    *, events: Iterable[LedgerEvent], merge_record: MergeRecord
) -> LedgerEvent:
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        if event.payload.get("task_id") != merge_record.task_id:
            continue
        if event.payload.get("unit_id") != merge_record.merge_unit_id:
            continue
        selection = event.payload.get("canonical_selection")
        if not isinstance(selection, dict):
            continue
        if selection.get("canonical_selection_id") != merge_record.canonical_selection_id:
            continue
        if event.event_seq != merge_record.canonical_event_seq:
            raise ValueError("canonical selection event seq mismatch")
        return event
    raise ValueError("merge unit CANONICAL_OUTPUTS_BOUND is required before MERGE_RECORDED")


def _merge_task_link_payload(
    *, events: Iterable[LedgerEvent], merge_record: MergeRecord
) -> JsonObject:
    for event in events:
        if event.event_type != EventType.MERGE_TASK_LINK_RECORDED:
            continue
        payload = event.payload
        if payload.get("merge_plan_id") != merge_record.merge_plan_id:
            continue
        link = payload.get("merge_task_link")
        if not isinstance(link, dict):
            raise ValueError("merge task link payload missing")
        if link.get("merge_task_link_id") != merge_record.merge_task_link_id:
            raise ValueError("merge task link mismatch")
        return link
    raise ValueError("merge task link not found for merge resolution")


def _validate_merge_input_bundle(
    *,
    artifact_store: ArtifactStore,
    merge_record: MergeRecord,
    merge_task_link: JsonObject,
) -> list[JsonObject]:
    if merge_task_link.get("merge_input_bundle_ref") != merge_record.merge_input_bundle_ref:
        raise ValueError("merge input bundle ref mismatch")
    if merge_task_link.get("merge_input_bundle_digest") != merge_record.merge_input_bundle_digest:
        raise ValueError("merge input bundle digest mismatch")
    bundle_ref = ArtifactRef.from_dict(merge_record.merge_input_bundle_ref)
    if not artifact_store.verify(bundle_ref):
        raise ValueError("merge input bundle artifact digest mismatch")
    bundle = json.loads(artifact_store.read_bytes(bundle_ref).decode("utf-8"))
    if bundle.get("merge_plan_id") != merge_record.merge_plan_id:
        raise ValueError("merge input bundle merge_plan_id mismatch")
    if bundle.get("parent_unit_id") != merge_record.parent_unit_id:
        raise ValueError("merge input bundle parent_unit_id mismatch")
    parent_output_mapping = bundle.get("parent_output_mapping")
    if not isinstance(parent_output_mapping, list) or not parent_output_mapping:
        raise ValueError("merge input bundle parent_output_mapping missing")
    if digest_json(parent_output_mapping) != merge_record.parent_output_mapping_digest:
        raise ValueError("parent output mapping digest mismatch")
    return [dict(mapping) for mapping in parent_output_mapping]


def _validate_merge_record_against_link(
    *, merge_record: MergeRecord, merge_task_link: JsonObject
) -> None:
    expected_fields = {
        "task_id": merge_record.task_id,
        "parent_unit_id": merge_record.parent_unit_id,
        "merge_plan_id": merge_record.merge_plan_id,
        "merge_unit_id": merge_record.merge_unit_id,
        "merge_input_bundle_digest": merge_record.merge_input_bundle_digest,
        "required_slot_bindings_digest": merge_record.required_slot_bindings_digest,
        "merge_policy_id": merge_record.merge_policy_id,
        "merge_policy_version": merge_record.merge_policy_version,
        "merge_policy_descriptor_digest": merge_record.merge_policy_descriptor_digest,
    }
    for field_name, expected in expected_fields.items():
        if merge_task_link.get(field_name) != expected:
            raise ValueError(f"merge task link {field_name} mismatch")


def _validate_merge_record_against_canonical_event(
    *, merge_record: MergeRecord, canonical_event: LedgerEvent
) -> None:
    selection = canonical_event.payload.get("canonical_selection")
    if not isinstance(selection, dict):
        raise ValueError("canonical selection payload missing")
    expected_fields = {
        "canonical_selection_id": merge_record.canonical_selection_id,
        "selected_verification_report_id": merge_record.selected_verification_report_id,
        "selected_verification_event_seq": merge_record.selected_verification_event_seq,
        "selected_submission_id": merge_record.selected_submission_id,
        "selected_submission_event_seq": merge_record.selected_submission_event_seq,
        "selected_attempt_id": merge_record.selected_attempt_id,
        "canonical_output_bundle_digest": merge_record.merge_output_bundle_digest,
    }
    for field_name, expected in expected_fields.items():
        if selection.get(field_name) != expected:
            raise ValueError(f"canonical selection {field_name} mismatch")
    if selection.get("canonical_output_refs") != merge_record.merge_output_refs:
        raise ValueError("canonical selection merge output refs mismatch")
    for output_ref in merge_record.merge_output_refs.values():
        if not isinstance(output_ref, dict):
            raise ValueError("merge output ref must be an object")
        if output_ref.get("artifact_type") != "canonical_output":
            raise ValueError("merge output ref must be canonical_output")


def _validate_expected_output_resolutions(
    *,
    merge_record: MergeRecord,
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...],
    parent_output_mapping: list[JsonObject],
) -> None:
    if not expected_output_resolutions:
        raise ValueError("required parent outputs are not fully resolved")
    expected_ids: set[str] = set()
    for resolution in expected_output_resolutions:
        if resolution.expected_output_id in expected_ids:
            raise ValueError("duplicate expected output resolution")
        expected_ids.add(resolution.expected_output_id)

    required_parent_output_names = {
        mapping["parent_output_name"]
        for mapping in parent_output_mapping
        if mapping.get("resolution_kind") == "merge_plan_output"
    }
    resolution_names = {
        resolution.expected_output_name for resolution in expected_output_resolutions
    }
    if resolution_names != required_parent_output_names:
        raise ValueError("required parent outputs are not fully resolved")

    for resolution in expected_output_resolutions:
        if resolution.task_id != merge_record.task_id:
            raise ValueError("expected output resolution task mismatch")
        if resolution.owner_unit_id != merge_record.parent_unit_id:
            raise ValueError("expected output resolution owner mismatch")
        if resolution.merge_record_id != merge_record.merge_record_id:
            raise ValueError("expected output resolution merge_record mismatch")
        if resolution.merge_plan_id != merge_record.merge_plan_id:
            raise ValueError("expected output resolution merge_plan mismatch")
        if resolution.merge_unit_id != merge_record.merge_unit_id:
            raise ValueError("expected output resolution merge_unit mismatch")
        if resolution.merge_canonical_selection_id != merge_record.canonical_selection_id:
            raise ValueError("expected output resolution canonical selection mismatch")
        expected_ref = merge_record.merge_output_refs.get(resolution.expected_output_name)
        if expected_ref is None:
            raise ValueError("expected output resolution missing merge output ref")
        if resolution.resolved_output_ref != expected_ref:
            raise ValueError("expected output resolution output ref mismatch")
        if resolution.resolved_output_digest != expected_ref.get("content_hash"):
            raise ValueError("expected output resolution output digest mismatch")


def _merge_resolution_drafts(
    *,
    merge_record: MergeRecord,
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...],
    correlation_id: str,
    causation_event_id: str | None,
) -> tuple[EventDraft, ...]:
    merge_record_payload = {
        "schema_version": "phase5.merge_recorded.v1",
        "merge_record": merge_record.to_dict(),
        "task_id": merge_record.task_id,
        "parent_unit_id": merge_record.parent_unit_id,
        "merge_plan_id": merge_record.merge_plan_id,
        "merge_unit_id": merge_record.merge_unit_id,
        "merge_task_link_id": merge_record.merge_task_link_id,
        "merge_input_bundle_ref": merge_record.merge_input_bundle_ref,
        "merge_input_bundle_digest": merge_record.merge_input_bundle_digest,
        "required_slot_bindings_digest": merge_record.required_slot_bindings_digest,
        "merge_policy_id": merge_record.merge_policy_id,
        "merge_policy_version": merge_record.merge_policy_version,
        "merge_policy_descriptor_digest": merge_record.merge_policy_descriptor_digest,
        "merge_policy_params_digest": merge_record.merge_policy_params_digest,
        "canonical_selection_id": merge_record.canonical_selection_id,
        "canonical_event_seq": merge_record.canonical_event_seq,
        "selected_verification_report_id": merge_record.selected_verification_report_id,
        "selected_verification_event_seq": merge_record.selected_verification_event_seq,
        "selected_submission_id": merge_record.selected_submission_id,
        "selected_submission_event_seq": merge_record.selected_submission_event_seq,
        "selected_attempt_id": merge_record.selected_attempt_id,
        "merge_output_bundle_digest": merge_record.merge_output_bundle_digest,
        "merge_output_refs": merge_record.merge_output_refs,
        "parent_output_mapping_digest": merge_record.parent_output_mapping_digest,
        "created_at": merge_record.created_at,
    }
    drafts: list[EventDraft] = [
        EventDraft(
            event_type=EventType.MERGE_RECORDED,
            object_type="MergeRecord",
            object_id=merge_record.merge_record_id,
            task_id=merge_record.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"merge_record:{merge_record.merge_plan_id}:"
                f"{merge_record.merge_unit_id}:{merge_record.canonical_selection_id}"
            ),
            payload=merge_record_payload,
            occurred_at=merge_record.created_at,
        )
    ]
    for resolution in expected_output_resolutions:
        drafts.append(
            EventDraft(
                event_type=EventType.EXPECTED_OUTPUT_RESOLVED,
                object_type="ExpectedOutputRef",
                object_id=resolution.expected_output_id,
                task_id=resolution.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=(
                    f"expected_output_resolved:{resolution.expected_output_id}:"
                    f"{merge_record.merge_record_id}"
                ),
                payload={
                    "schema_version": "phase5.expected_output_resolved.v1",
                    "expected_output_resolution": resolution.to_dict(),
                    "task_id": resolution.task_id,
                    "owner_unit_id": resolution.owner_unit_id,
                    "expected_output_id": resolution.expected_output_id,
                    "expected_output_name": resolution.expected_output_name,
                    "resolution_source_type": resolution.resolution_source_type,
                    "merge_record_id": resolution.merge_record_id,
                    "merge_plan_id": resolution.merge_plan_id,
                    "merge_unit_id": resolution.merge_unit_id,
                    "merge_canonical_selection_id": (
                        resolution.merge_canonical_selection_id
                    ),
                    "resolved_output_ref": resolution.resolved_output_ref,
                    "resolved_output_digest": resolution.resolved_output_digest,
                    "resolved_at": resolution.resolved_at,
                },
                occurred_at=resolution.resolved_at,
            )
        )
    return tuple(drafts)


def _is_complete_merge_resolution_batch(
    *, events: Iterable[LedgerEvent], batch_id: str, merge_record_id: str
) -> bool:
    batch = sorted(
        (event for event in events if event.batch_id == batch_id),
        key=lambda event: event.batch_index or 0,
    )
    if len(batch) < 2:
        return False
    batch_sizes = {event.batch_size for event in batch}
    if len(batch_sizes) != 1 or None in batch_sizes:
        return False
    if next(iter(batch_sizes)) != len(batch):
        return False
    if batch[0].batch_index != 1 or batch[0].event_type != EventType.MERGE_RECORDED:
        return False
    if batch[0].object_id != merge_record_id:
        return False
    expected_indexes = list(range(1, len(batch) + 1))
    actual_indexes = [event.batch_index for event in batch]
    if actual_indexes != expected_indexes:
        return False
    return all(
        event.event_type == EventType.EXPECTED_OUTPUT_RESOLVED for event in batch[1:]
    )


def _reject_parent_completion_conflict(
    *,
    events: Iterable[LedgerEvent],
    owner_unit_id: str,
    resolved_output_set_digest: str,
) -> None:
    expected_batch_id = (
        f"parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}"
    )
    prefix = f"parent_completion_batch:{owner_unit_id}:"
    batch_ids = {
        event.batch_id
        for event in events
        if isinstance(event.batch_id, str) and event.batch_id.startswith(prefix)
    }
    for batch_id in batch_ids:
        if not _is_complete_parent_completion_batch(
            events=events,
            batch_id=batch_id,
            owner_unit_id=owner_unit_id,
        ):
            raise ValueError("projection inconsistent: incomplete parent_completion_batch")
        if batch_id != expected_batch_id:
            raise ValueError("parent completion conflict for owner unit")


def _validate_parent_completion_resolutions(
    *,
    owner_unit: TaskUnit,
    expected_output_refs: tuple[ExpectedOutputRef, ...],
    expected_output_resolutions: tuple[ExpectedOutputResolution, ...],
    events: Iterable[LedgerEvent],
) -> None:
    if not expected_output_refs:
        raise ValueError("required expected outputs are not fully resolved")
    refs_by_id: dict[str, ExpectedOutputRef] = {}
    for ref in expected_output_refs:
        if ref.task_id != owner_unit.task_id or ref.owner_unit_id != owner_unit.unit_id:
            raise ValueError("required expected outputs do not belong to owner unit")
        if ref.expected_output_id in refs_by_id:
            raise ValueError("duplicate required expected output")
        refs_by_id[ref.expected_output_id] = ref

    resolutions_by_id: dict[str, ExpectedOutputResolution] = {}
    for resolution in expected_output_resolutions:
        if resolution.expected_output_id in resolutions_by_id:
            raise ValueError("duplicate expected output resolution")
        resolutions_by_id[resolution.expected_output_id] = resolution
    if set(resolutions_by_id) != set(refs_by_id):
        raise ValueError("required expected outputs are not fully resolved")

    recorded_by_id = _recorded_expected_output_resolutions(events=events)
    for expected_output_id, ref in refs_by_id.items():
        resolution = resolutions_by_id[expected_output_id]
        if resolution.task_id != owner_unit.task_id:
            raise ValueError("expected output resolution task mismatch")
        if resolution.owner_unit_id != owner_unit.unit_id:
            raise ValueError("expected output resolution owner mismatch")
        if resolution.expected_output_name != ref.output_name:
            raise ValueError("expected output resolution name mismatch")
        recorded = recorded_by_id.get(expected_output_id)
        if recorded is None:
            raise ValueError("required expected outputs are not fully resolved")
        if recorded != resolution.to_dict():
            raise ValueError("expected output resolution conflict")


def _recorded_expected_output_resolutions(
    *, events: Iterable[LedgerEvent]
) -> dict[str, JsonObject]:
    ledger_events = tuple(events)
    recorded: dict[str, JsonObject] = {}
    for event in ledger_events:
        if event.event_type != EventType.EXPECTED_OUTPUT_RESOLVED:
            continue
        resolution = event.payload.get("expected_output_resolution")
        if not isinstance(resolution, dict):
            continue
        merge_record_id = resolution.get("merge_record_id")
        if (
            not isinstance(event.batch_id, str)
            or not isinstance(merge_record_id, str)
            or not _is_complete_merge_resolution_batch(
                events=ledger_events,
                batch_id=event.batch_id,
                merge_record_id=merge_record_id,
            )
        ):
            continue
        expected_output_id = resolution.get("expected_output_id")
        if not isinstance(expected_output_id, str) or not expected_output_id:
            continue
        existing = recorded.get(expected_output_id)
        if existing is not None and existing != resolution:
            raise ValueError("expected output resolution conflict")
        recorded[expected_output_id] = dict(resolution)
    return recorded


def _eligible_parent_completion_contributions(
    *,
    owner_unit: TaskUnit,
    expected_output_refs: tuple[ExpectedOutputRef, ...],
    expand_contributions: tuple[ContributionRecord, ...],
    now: str,
) -> tuple[ContributionRecord, ...]:
    if not expand_contributions:
        raise ValueError("expand contribution is required for parent completion")
    source_decision_ids = {
        ref.source_expansion_decision_id for ref in expected_output_refs
    }
    eligible: list[ContributionRecord] = []
    seen_ids: set[str] = set()
    for contribution in sorted(
        expand_contributions,
        key=lambda item: item.contribution_id,
    ):
        if contribution.contribution_id in seen_ids:
            raise ValueError("duplicate expand contribution")
        seen_ids.add(contribution.contribution_id)
        if contribution.kind != "expand_canonical":
            raise ValueError("parent completion requires expand_canonical contribution")
        if contribution.task_id != owner_unit.task_id or contribution.unit_id != owner_unit.unit_id:
            raise ValueError("expand contribution owner mismatch")
        if contribution.state != ContributionState.PENDING:
            raise ValueError("expand contribution must be Pending")
        if contribution.source_decision_id not in source_decision_ids:
            raise ValueError("expand contribution source decision mismatch")
        eligible.append(
            transition_contribution(
                contribution,
                new_state=ContributionState.ELIGIBLE,
                changed_at=now,
                reason="parent_completed",
            )
        )
    return tuple(eligible)


def _parent_completion_drafts(
    *,
    owner_unit: TaskUnit,
    completed_unit: TaskUnit,
    eligible_contributions: tuple[ContributionRecord, ...],
    resolved_output_set_digest: str,
    now: str,
    correlation_id: str,
    causation_event_id: str | None,
    batch_id: str,
) -> tuple[EventDraft, ...]:
    task_payload = {
        "schema_version": "phase5.parent_completion_task_unit_state_changed.v1",
        "old_state": owner_unit.state.value,
        "new_state": TaskState.COMPLETED.value,
        "task_unit_state_change": _task_unit_state_change(
            task_unit=completed_unit,
            old_state=owner_unit.state,
            new_state=TaskState.COMPLETED,
            reason="required_expected_outputs_resolved",
            trigger="parent_completion_batch",
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            changed_at=now,
            state_context={
                "resolved_output_set_digest": resolved_output_set_digest,
                "parent_completion_batch_id": batch_id,
            },
        ),
        "task_unit": completed_unit.to_dict(),
        "reason": "required_expected_outputs_resolved",
        "resolved_output_set_digest": resolved_output_set_digest,
        "parent_completion_batch_id": batch_id,
        "correlation_id": correlation_id,
    }
    drafts: list[EventDraft] = [
        EventDraft(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id=completed_unit.unit_id,
            task_id=completed_unit.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"task_unit:state:{completed_unit.unit_id}:Processing:"
                f"Completed:parent_completion:{resolved_output_set_digest}"
            ),
            payload=task_payload,
            occurred_at=now,
        )
    ]
    for contribution in eligible_contributions:
        drafts.append(
            EventDraft(
                event_type=EventType.CONTRIBUTION_STATE_CHANGED,
                object_type="ContributionRecord",
                object_id=contribution.contribution_id,
                task_id=contribution.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=(
                    f"contribution:state:{contribution.contribution_id}:"
                    f"Pending:Eligible:parent_completion:{resolved_output_set_digest}"
                ),
                payload={
                    "schema_version": "phase5.contribution_state_changed.v1",
                    "contribution": contribution.to_dict(),
                    "old_state": ContributionState.PENDING.value,
                    "new_state": ContributionState.ELIGIBLE.value,
                    "reason": "parent_completed",
                    "task_id": contribution.task_id,
                    "unit_id": contribution.unit_id,
                    "kind": contribution.kind,
                    "canonical_selection_id": contribution.canonical_selection_id,
                    "canonical_event_seq": contribution.canonical_event_seq,
                    "source_batch_id": contribution.source_batch_id,
                    "source_terminal_event_seq": contribution.source_terminal_event_seq,
                    "resolved_output_set_digest": resolved_output_set_digest,
                    "changed_at": contribution.updated_at,
                },
                occurred_at=now,
            )
        )
    return tuple(drafts)


def _resolved_output_set_digest(
    resolutions: tuple[ExpectedOutputResolution, ...]
) -> str:
    return digest_json(
        sorted(
            [
                {
                    "expected_output_id": resolution.expected_output_id,
                    "output_name": resolution.expected_output_name,
                    "resolved_output_digest": resolution.resolved_output_digest,
                }
                for resolution in resolutions
            ],
            key=lambda item: item["expected_output_id"],
        )
    )


def _is_complete_parent_completion_batch(
    *, events: Iterable[LedgerEvent], batch_id: str, owner_unit_id: str
) -> bool:
    batch = sorted(
        (event for event in events if event.batch_id == batch_id),
        key=lambda event: event.batch_index or 0,
    )
    if len(batch) < 2:
        return False
    batch_sizes = {event.batch_size for event in batch}
    if len(batch_sizes) != 1 or None in batch_sizes:
        return False
    if next(iter(batch_sizes)) != len(batch):
        return False
    if (
        batch[0].batch_index != 1
        or batch[0].event_type != EventType.TASK_UNIT_STATE_CHANGED
        or batch[0].object_id != owner_unit_id
        or batch[0].payload.get("old_state") != TaskState.PROCESSING.value
        or batch[0].payload.get("new_state") != TaskState.COMPLETED.value
    ):
        return False
    expected_indexes = list(range(1, len(batch) + 1))
    if [event.batch_index for event in batch] != expected_indexes:
        return False
    return all(
        event.event_type == EventType.CONTRIBUTION_STATE_CHANGED
        and event.payload.get("old_state") == ContributionState.PENDING.value
        and event.payload.get("new_state") == ContributionState.ELIGIBLE.value
        for event in batch[1:]
    )


def _eligible_settlement_contributions(
    *,
    contributions: tuple[ContributionRecord, ...],
    task_id: str,
    root_completion_event_seq: int,
) -> tuple[ContributionRecord, ...]:
    by_id: dict[str, ContributionRecord] = {}
    for contribution in contributions:
        if contribution.task_id != task_id:
            continue
        if contribution.state != ContributionState.ELIGIBLE:
            continue
        if contribution.source_terminal_event_seq > root_completion_event_seq:
            continue
        existing = by_id.get(contribution.contribution_id)
        if existing is not None and existing != contribution:
            raise ValueError("duplicate eligible contribution")
        by_id[contribution.contribution_id] = contribution
    return tuple(
        by_id[contribution_id]
        for contribution_id in sorted(by_id)
    )


def _validate_root_completion_event(
    *,
    events: Iterable[LedgerEvent],
    task_id: str,
    root_unit_id: str,
    root_completion_event_seq: int,
) -> LedgerEvent:
    for event in events:
        if event.event_seq != root_completion_event_seq:
            continue
        if (
            event.event_type == EventType.TASK_UNIT_STATE_CHANGED
            and event.task_id == task_id
            and event.object_id == root_unit_id
            and event.payload.get("new_state") == TaskState.COMPLETED.value
        ):
            return event
        break
    raise ValueError("root completion event missing")


def _eligible_contributions_from_ledger(
    *,
    events: Iterable[LedgerEvent],
    task_id: str,
    root_completion_event_seq: int,
) -> tuple[ContributionRecord, ...]:
    states = _contribution_states_from_events(events=events)
    eligible = [
        contribution
        for contribution in states.values()
        if contribution.task_id == task_id
        and contribution.state == ContributionState.ELIGIBLE
        and contribution.source_terminal_event_seq <= root_completion_event_seq
    ]
    if not eligible:
        raise ValueError("settlement requires at least one eligible contribution")
    return tuple(sorted(eligible, key=lambda contribution: contribution.contribution_id))


def _contribution_states_from_events(
    *, events: Iterable[LedgerEvent]
) -> dict[str, ContributionRecord]:
    states: dict[str, ContributionRecord] = {}
    for event in events:
        if event.event_type != EventType.CONTRIBUTION_STATE_CHANGED:
            continue
        contribution_data = event.payload.get("contribution")
        if not isinstance(contribution_data, dict):
            raise ValueError("contribution state event missing contribution")
        contribution = ContributionRecord(**contribution_data)
        states[contribution.contribution_id] = contribution
    return states


def _existing_root_settlement(
    *,
    events: Iterable[LedgerEvent],
    artifact_store: ArtifactStore,
    batch_id: str,
    task_id: str,
    root_unit_id: str,
    root_completion_event_seq: int,
) -> SettlementFlowResult | None:
    ledger_events = tuple(events)
    matching_markers = [
        event
        for event in ledger_events
        if event.event_type == EventType.SETTLEMENT_RECORDED
        and event.payload.get("task_id") == task_id
        and event.payload.get("root_unit_id") == root_unit_id
        and event.payload.get("root_completion_event_seq") == root_completion_event_seq
    ]
    if not matching_markers and not any(event.batch_id == batch_id for event in ledger_events):
        return None
    if len(matching_markers) > 1:
        raise ValueError("settlement conflict: multiple SETTLEMENT_RECORDED markers")
    if matching_markers and matching_markers[0].batch_id != batch_id:
        raise ValueError("settlement conflict: unexpected settlement batch id")
    batch = tuple(
        sorted(
            (event for event in ledger_events if event.batch_id == batch_id),
            key=lambda event: event.batch_index or 0,
        )
    )
    if not batch:
        return None
    return _validate_existing_settlement_batch(
        batch=batch,
        artifact_store=artifact_store,
        task_id=task_id,
        root_unit_id=root_unit_id,
        root_completion_event_seq=root_completion_event_seq,
    )


def _validate_existing_settlement_batch(
    *,
    batch: tuple[LedgerEvent, ...],
    artifact_store: ArtifactStore,
    task_id: str,
    root_unit_id: str,
    root_completion_event_seq: int,
) -> SettlementFlowResult:
    batch_sizes = {event.batch_size for event in batch}
    if len(batch) < 2 or len(batch_sizes) != 1 or None in batch_sizes:
        raise ValueError("projection inconsistent: incomplete settlement_batch")
    if next(iter(batch_sizes)) != len(batch):
        raise ValueError("projection inconsistent: incomplete settlement_batch")
    if [event.batch_index for event in batch] != list(range(1, len(batch) + 1)):
        raise ValueError("projection inconsistent: incomplete settlement_batch")
    if batch[-1].event_type != EventType.SETTLEMENT_RECORDED:
        raise ValueError("projection inconsistent: settlement_batch missing final marker")
    settled_events = batch[:-1]
    if not all(
        event.event_type == EventType.CONTRIBUTION_STATE_CHANGED
        and event.payload.get("old_state") == ContributionState.ELIGIBLE.value
        and event.payload.get("new_state") == ContributionState.SETTLED.value
        for event in settled_events
    ):
        raise ValueError("projection inconsistent: settlement_batch settled events")
    marker_payload = batch[-1].payload
    record_payload = marker_payload.get("settlement_record")
    if not isinstance(record_payload, dict):
        raise ValueError("settlement_record payload missing")
    settlement_record = SettlementRecord(**record_payload)
    if (
        settlement_record.task_id != task_id
        or settlement_record.root_unit_id != root_unit_id
        or settlement_record.root_completion_event_seq != root_completion_event_seq
    ):
        raise ValueError("settlement conflict: root completion mismatch")
    if marker_payload.get("settlement_entries_ref") != settlement_record.settlement_entries_ref:
        raise ValueError("settlement_entries_ref mismatch")

    settlement_entries = _settlement_entries_from_artifact(
        artifact_store=artifact_store,
        settlement_record=settlement_record,
    )
    if len(settlement_entries) != len(settled_events):
        raise ValueError("settlement entries mismatch settled contribution events")
    _validate_settlement_entries_against_events(
        settlement_record=settlement_record,
        settlement_entries=settlement_entries,
        settled_events=settled_events,
    )
    return SettlementFlowResult(
        settlement_record=settlement_record,
        settlement_entries=settlement_entries,
        settled_contributions=_settled_contributions_from_events(settled_events),
        events=batch,
    )


def _settlement_entries_from_artifact(
    *, artifact_store: ArtifactStore, settlement_record: SettlementRecord
) -> tuple[SettlementEntry, ...]:
    ref_data = settlement_record.settlement_entries_ref
    if not isinstance(ref_data, dict):
        raise ValueError("settlement_entries_ref must be an object")
    artifact_ref = ArtifactRef.from_dict(ref_data)
    if not artifact_store.verify(artifact_ref):
        raise ValueError("settlement entries artifact missing or corrupt")
    raw_entries = json.loads(artifact_store.read_bytes(artifact_ref).decode("utf-8"))
    if not isinstance(raw_entries, list):
        raise ValueError("settlement entries artifact must contain a list")
    settlement_entries = tuple(SettlementEntry(**entry) for entry in raw_entries)
    if digest_settlement_entries(list(settlement_entries)) != (
        settlement_record.settlement_entries_digest
    ):
        raise ValueError("settlement entries artifact digest mismatch")
    if len(settlement_entries) != settlement_record.entry_count:
        raise ValueError("settlement entries artifact entry_count mismatch")
    if sum(entry.reward_units for entry in settlement_entries) != (
        settlement_record.total_reward
    ):
        raise ValueError("settlement entries artifact reward total mismatch")
    if settlement_record.total_reward != settlement_record.root_budget:
        raise ValueError("settlement entries artifact reward total mismatch")
    return settlement_entries


def _validate_settlement_entries_against_events(
    *,
    settlement_record: SettlementRecord,
    settlement_entries: tuple[SettlementEntry, ...],
    settled_events: tuple[LedgerEvent, ...],
) -> None:
    entries_by_id = {entry.contribution_id: entry for entry in settlement_entries}
    if len(entries_by_id) != len(settlement_entries):
        raise ValueError("settlement entries mismatch: duplicate contribution")
    event_entries: dict[str, JsonObject] = {}
    for event in settled_events:
        contribution_data = event.payload.get("contribution")
        entry_data = event.payload.get("settlement_entry")
        if not isinstance(contribution_data, dict) or not isinstance(entry_data, dict):
            raise ValueError("settlement entries mismatch settled contribution events")
        contribution = ContributionRecord(**contribution_data)
        if contribution.state != ContributionState.SETTLED:
            raise ValueError("settlement entries mismatch settled contribution events")
        entry = entries_by_id.get(contribution.contribution_id)
        if entry is None:
            raise ValueError("settlement entries mismatch settled contribution events")
        if entry_data != entry.to_dict():
            raise ValueError("settlement entries mismatch settled contribution events")
        if (
            entry.reward_weight != contribution.reward_weight
            or entry.source_client_id != contribution.source_client_id
            or entry.task_id != contribution.task_id
            or entry.unit_id != contribution.unit_id
            or entry.kind != contribution.kind
        ):
            raise ValueError("settlement entries mismatch settled contribution events")
        event_entries[contribution.contribution_id] = dict(entry_data)
    if set(event_entries) != set(entries_by_id):
        raise ValueError("settlement entries mismatch settled contribution events")
    if sum(entry.reward_units for entry in settlement_entries) != (
        settlement_record.total_reward
    ):
        raise ValueError("settlement entries mismatch reward total")


def _validate_existing_settlement_matches_request(
    *,
    existing: SettlementFlowResult,
    expected_entries: tuple[SettlementEntry, ...],
    settlement_policy_id: str,
    settlement_policy_version: str,
    root_budget: int,
    scale: str,
) -> None:
    record = existing.settlement_record
    if (
        record.settlement_policy_id != settlement_policy_id
        or record.settlement_policy_version != settlement_policy_version
        or record.root_budget != root_budget
        or record.scale != scale
        or record.entry_count != len(expected_entries)
        or record.total_reward != sum(entry.reward_units for entry in expected_entries)
        or record.settlement_entries_digest
        != digest_settlement_entries(list(expected_entries))
    ):
        raise ValueError("settlement conflict")
    if [entry.to_dict() for entry in existing.settlement_entries] != [
        entry.to_dict() for entry in expected_entries
    ]:
        raise ValueError("settlement conflict")


def _settlement_drafts(
    *,
    settlement_record: SettlementRecord,
    settlement_entries: tuple[SettlementEntry, ...],
    contributions_by_id: dict[str, ContributionRecord],
    now: str,
    correlation_id: str,
    causation_event_id: str | None,
) -> tuple[EventDraft, ...]:
    drafts: list[EventDraft] = []
    for entry in settlement_entries:
        contribution = contributions_by_id.get(entry.contribution_id)
        if contribution is None:
            raise ValueError("settlement entries mismatch eligible contributions")
        settled = transition_contribution(
            contribution,
            new_state=ContributionState.SETTLED,
            changed_at=now,
            reason="settlement_batch",
            source_batch_kind="settlement_batch",
        )
        drafts.append(
            EventDraft(
                event_type=EventType.CONTRIBUTION_STATE_CHANGED,
                object_type="ContributionRecord",
                object_id=contribution.contribution_id,
                task_id=contribution.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=(
                    f"contribution:state:{contribution.contribution_id}:"
                    f"Eligible:Settled:{settlement_record.settlement_record_id}"
                ),
                payload={
                    "schema_version": "phase5.contribution_state_changed.v1",
                    "contribution": settled.to_dict(),
                    "old_state": ContributionState.ELIGIBLE.value,
                    "new_state": ContributionState.SETTLED.value,
                    "reason": "settlement_batch",
                    "task_id": contribution.task_id,
                    "unit_id": contribution.unit_id,
                    "kind": contribution.kind,
                    "canonical_selection_id": contribution.canonical_selection_id,
                    "canonical_event_seq": contribution.canonical_event_seq,
                    "source_batch_id": contribution.source_batch_id,
                    "source_terminal_event_seq": contribution.source_terminal_event_seq,
                    "settlement_record_id": settlement_record.settlement_record_id,
                    "settlement_entry": entry.to_dict(),
                    "changed_at": settled.updated_at,
                },
                occurred_at=now,
            )
        )
    drafts.append(
        EventDraft(
            event_type=EventType.SETTLEMENT_RECORDED,
            object_type="SettlementRecord",
            object_id=settlement_record.settlement_record_id,
            task_id=settlement_record.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"settlement:{settlement_record.task_id}:"
                f"{settlement_record.root_unit_id}:"
                f"{settlement_record.root_completion_event_seq}"
            ),
            payload={
                "schema_version": "phase5.settlement_recorded.v1",
                "settlement_record": settlement_record.to_dict(),
                "task_id": settlement_record.task_id,
                "root_unit_id": settlement_record.root_unit_id,
                "root_completion_event_seq": (
                    settlement_record.root_completion_event_seq
                ),
                "settlement_policy_id": settlement_record.settlement_policy_id,
                "settlement_policy_version": (
                    settlement_record.settlement_policy_version
                ),
                "root_budget": settlement_record.root_budget,
                "scale": settlement_record.scale,
                "total_reward": settlement_record.total_reward,
                "entry_count": settlement_record.entry_count,
                "settlement_entries_digest": (
                    settlement_record.settlement_entries_digest
                ),
                "settlement_entries_ref": (
                    settlement_record.settlement_entries_ref
                ),
                "settlement_summary": settlement_record.settlement_summary,
                "created_at": settlement_record.created_at,
            },
            occurred_at=now,
        )
    )
    return tuple(drafts)


def _settled_contributions_from_events(
    events: Iterable[LedgerEvent],
) -> tuple[ContributionRecord, ...]:
    settled: list[ContributionRecord] = []
    for event in events:
        if event.event_type != EventType.CONTRIBUTION_STATE_CHANGED:
            continue
        contribution_data = event.payload.get("contribution")
        if isinstance(contribution_data, dict):
            settled.append(ContributionRecord(**contribution_data))
    return tuple(settled)


def _settlement_summary(entries: tuple[SettlementEntry, ...]) -> JsonObject:
    kind_counts = {
        kind: sum(1 for entry in entries if entry.kind == kind)
        for kind in sorted({entry.kind for entry in entries})
    }
    client_reward_totals = {
        client_id: sum(
            entry.reward_units for entry in entries if entry.source_client_id == client_id
        )
        for client_id in sorted({entry.source_client_id for entry in entries})
    }
    return {
        "entry_count": len(entries),
        "kind_counts": kind_counts,
        "client_count": len(client_reward_totals),
        "client_reward_totals": client_reward_totals,
        "total_reward": sum(entry.reward_units for entry in entries),
    }


def _validate_parent_completed_event(
    *,
    events: Iterable[LedgerEvent],
    parent_unit_id: str,
    parent_completed_event_seq: int,
) -> LedgerEvent:
    for event in events:
        if event.event_seq != parent_completed_event_seq:
            continue
        if (
            event.event_type == EventType.TASK_UNIT_STATE_CHANGED
            and event.object_id == parent_unit_id
            and event.payload.get("new_state") == TaskState.COMPLETED.value
        ):
            return event
        break
    raise ValueError("parent completion event missing")


def _validated_pruning_policy_ref(
    *,
    pruning_policy_ref: JsonObject,
    events: Iterable[LedgerEvent],
    artifact_store: ArtifactStore,
    task_id: str,
    parent_unit_id: str,
) -> JsonObject:
    if not isinstance(pruning_policy_ref, dict):
        raise ValueError("pruning policy ref must be an object")
    required_fields = (
        "pruning_policy_id",
        "pruning_policy_version",
        "pruning_policy_plugin_id",
        "pruning_policy_plugin_version",
        "pruning_policy_descriptor_digest",
        "policy_source_type",
        "policy_source_id",
        "policy_source_event_seq",
    )
    missing = [field for field in required_fields if not pruning_policy_ref.get(field)]
    if missing:
        if "pruning_policy_descriptor_digest" in missing:
            raise ValueError("pruning policy descriptor provenance missing")
        raise ValueError("pruning policy descriptor provenance missing")
    policy_source_event_seq = pruning_policy_ref["policy_source_event_seq"]
    if not isinstance(policy_source_event_seq, int) or policy_source_event_seq <= 0:
        raise ValueError("policy source event mismatch")

    try:
        descriptor = _load_frozen_plugin_descriptor(
            events=events,
            artifact_store=artifact_store,
            plugin_id=str(pruning_policy_ref["pruning_policy_plugin_id"]),
            plugin_version=str(pruning_policy_ref["pruning_policy_plugin_version"]),
            plugin_descriptor_digest=str(
                pruning_policy_ref["pruning_policy_descriptor_digest"]
            ),
        )
    except ValueError as error:
        raise ValueError("pruning policy descriptor provenance invalid") from error

    pruning_policy_id = str(pruning_policy_ref["pruning_policy_id"])
    if not _descriptor_declares_merge_policy(
        descriptor=descriptor,
        merge_policy_id=pruning_policy_id,
    ):
        raise ValueError("pruning policy is not declared by plugin descriptor")

    source_event = _event_at_seq(events=events, event_seq=policy_source_event_seq)
    if source_event is None:
        raise ValueError("policy source event mismatch")
    if pruning_policy_ref["policy_source_type"] != "merge_plan":
        raise ValueError("policy source event mismatch")
    _validate_merge_plan_policy_source(
        source_event=source_event,
        artifact_store=artifact_store,
        pruning_policy_ref=pruning_policy_ref,
        task_id=task_id,
        parent_unit_id=parent_unit_id,
    )
    return dict(pruning_policy_ref)


def _descriptor_declares_merge_policy(
    *, descriptor: JsonObject, merge_policy_id: str
) -> bool:
    if descriptor.get("merge_policy_id") == merge_policy_id:
        return True
    split_strategies = descriptor.get("split_strategies", {})
    if not isinstance(split_strategies, dict):
        return False
    return any(
        isinstance(strategy, dict)
        and strategy.get("merge_policy_id") == merge_policy_id
        for strategy in split_strategies.values()
    )


def _event_at_seq(
    *, events: Iterable[LedgerEvent], event_seq: int
) -> LedgerEvent | None:
    for event in events:
        if event.event_seq == event_seq:
            return event
    return None


def _validate_merge_plan_policy_source(
    *,
    source_event: LedgerEvent,
    artifact_store: ArtifactStore,
    pruning_policy_ref: JsonObject,
    task_id: str,
    parent_unit_id: str,
) -> None:
    if (
        source_event.event_type != EventType.MERGE_PLAN_RECORDED
        or source_event.task_id != task_id
        or source_event.object_id != pruning_policy_ref["policy_source_id"]
        or source_event.payload.get("merge_plan_id")
        != pruning_policy_ref["policy_source_id"]
        or source_event.payload.get("parent_unit_id") != parent_unit_id
        or source_event.payload.get("merge_policy_id")
        != pruning_policy_ref["pruning_policy_id"]
        or source_event.payload.get("merge_policy_version")
        != pruning_policy_ref["pruning_policy_version"]
    ):
        raise ValueError("policy source event mismatch")

    ref_data = source_event.payload.get("merge_plan_ref")
    if not isinstance(ref_data, dict):
        raise ValueError("policy source event mismatch")
    merge_plan_ref = ArtifactRef.from_dict(ref_data)
    if not artifact_store.verify(merge_plan_ref):
        raise ValueError("policy source event mismatch")
    merge_plan_data = json.loads(artifact_store.read_bytes(merge_plan_ref).decode("utf-8"))
    merge_plan = MergePlan(**merge_plan_data)
    if digest_merge_plan_body(merge_plan) != source_event.payload.get("merge_plan_digest"):
        raise ValueError("policy source event mismatch")
    merge_policy_ref = merge_plan.merge_policy_ref
    if (
        merge_policy_ref.get("plugin_id")
        != pruning_policy_ref["pruning_policy_plugin_id"]
        or merge_policy_ref.get("plugin_version")
        != pruning_policy_ref["pruning_policy_plugin_version"]
        or merge_policy_ref.get("merge_policy_id")
        != pruning_policy_ref["pruning_policy_id"]
        or merge_policy_ref.get("merge_policy_version")
        != pruning_policy_ref["pruning_policy_version"]
        or merge_policy_ref.get("merge_policy_descriptor_digest")
        != pruning_policy_ref["pruning_policy_descriptor_digest"]
    ):
        raise ValueError("policy source event mismatch")


def _subtree_pruning_candidates(
    *,
    parent_unit_id: str,
    task_id: str,
    candidate_descendant_units: tuple[TaskUnit, ...],
    events: Iterable[LedgerEvent],
) -> tuple[tuple[TaskUnit, ...], int]:
    units_by_id = _unique_units_by_id(candidate_descendant_units)
    canonical_unit_ids = _canonical_unit_ids_from_events(events=events)
    settlement_unit_ids = _settlement_evidence_unit_ids(events=events)
    cancellable_states = {
        TaskState.READY,
        TaskState.PROCESSING,
        TaskState.BLOCKED,
    }
    cancellable: list[TaskUnit] = []
    preserved_count = 0
    for unit in sorted(units_by_id.values(), key=lambda item: item.unit_id):
        if unit.task_id != task_id:
            continue
        if not _is_descendant_unit(
            unit=unit,
            parent_unit_id=parent_unit_id,
            units_by_id=units_by_id,
        ):
            continue
        protected = (
            unit.state == TaskState.COMPLETED
            or bool(unit.canonical_output_refs)
            or unit.unit_id in canonical_unit_ids
            or unit.unit_id in settlement_unit_ids
        )
        if unit.state in cancellable_states and not protected:
            cancellable.append(unit)
        else:
            preserved_count += 1
    return tuple(cancellable), preserved_count


def _unique_units_by_id(units: tuple[TaskUnit, ...]) -> dict[str, TaskUnit]:
    units_by_id: dict[str, TaskUnit] = {}
    for unit in units:
        existing = units_by_id.get(unit.unit_id)
        if existing is not None and existing != unit:
            raise ValueError(f"conflicting candidate descendant unit: {unit.unit_id}")
        units_by_id[unit.unit_id] = unit
    return units_by_id


def _is_descendant_unit(
    *, unit: TaskUnit, parent_unit_id: str, units_by_id: dict[str, TaskUnit]
) -> bool:
    current_parent_id = unit.parent_unit_id
    seen: set[str] = set()
    while current_parent_id:
        if current_parent_id == parent_unit_id:
            return True
        if current_parent_id in seen:
            return False
        seen.add(current_parent_id)
        parent = units_by_id.get(current_parent_id)
        if parent is None:
            return False
        current_parent_id = parent.parent_unit_id
    return False


def _canonical_unit_ids_from_events(*, events: Iterable[LedgerEvent]) -> set[str]:
    unit_ids: set[str] = set()
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        unit_id = event.payload.get("unit_id")
        if isinstance(unit_id, str) and unit_id:
            unit_ids.add(unit_id)
    return unit_ids


def _settlement_evidence_unit_ids(*, events: Iterable[LedgerEvent]) -> set[str]:
    unit_ids: set[str] = set()
    for event in events:
        if event.event_type != EventType.CONTRIBUTION_STATE_CHANGED:
            continue
        contribution_data = event.payload.get("contribution")
        if not isinstance(contribution_data, dict):
            continue
        try:
            contribution = ContributionRecord(**contribution_data)
        except ValueError:
            continue
        if contribution.state == ContributionState.SETTLED:
            unit_ids.add(contribution.unit_id)
    return unit_ids


def _subtree_prune_record(
    *,
    parent_completed_event: LedgerEvent,
    parent_unit_id: str,
    parent_completed_event_seq: int,
    policy: JsonObject,
    cancellable_units: tuple[TaskUnit, ...],
    preserved_completed_unit_count: int,
    now: str,
) -> SubtreePruneRecord:
    return SubtreePruneRecord(
        subtree_prune_id=f"subtree_pruned:{parent_unit_id}:{parent_completed_event_seq}",
        task_id=parent_completed_event.task_id or "",
        parent_unit_id=parent_unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
        pruning_policy_id=str(policy["pruning_policy_id"]),
        pruning_policy_version=str(policy["pruning_policy_version"]),
        pruning_policy_plugin_id=str(policy["pruning_policy_plugin_id"]),
        pruning_policy_descriptor_digest=str(
            policy["pruning_policy_descriptor_digest"]
        ),
        policy_source_type=str(policy["policy_source_type"]),
        policy_source_id=str(policy["policy_source_id"]),
        policy_source_event_seq=int(policy["policy_source_event_seq"]),
        cancelled_unit_count=len(cancellable_units),
        cancelled_unit_ids_digest=_cancelled_unit_ids_digest(
            tuple(unit.unit_id for unit in cancellable_units)
        ),
        preserved_completed_unit_count=preserved_completed_unit_count,
        reason="parent_completed_post_completion_pruning",
        created_at=now,
    )


def _cancelled_unit_ids_digest(cancelled_unit_ids: tuple[str, ...]) -> str:
    return digest_json(sorted(cancelled_unit_ids))


def _existing_subtree_pruning(
    *,
    events: Iterable[LedgerEvent],
    batch_id: str,
    parent_unit_id: str,
    parent_completed_event_seq: int,
) -> SubtreePruningFlowResult | None:
    ledger_events = tuple(events)
    matching_markers = [
        event
        for event in ledger_events
        if event.event_type == EventType.SUBTREE_PRUNED
        and event.payload.get("parent_unit_id") == parent_unit_id
        and event.payload.get("parent_completed_event_seq")
        == parent_completed_event_seq
    ]
    if not matching_markers and not any(event.batch_id == batch_id for event in ledger_events):
        return None
    if len(matching_markers) > 1:
        raise ValueError("subtree pruning conflict: multiple SUBTREE_PRUNED markers")
    if matching_markers and matching_markers[0].batch_id != batch_id:
        raise ValueError("subtree pruning conflict: unexpected pruning batch id")
    batch = tuple(
        sorted(
            (event for event in ledger_events if event.batch_id == batch_id),
            key=lambda event: event.batch_index or 0,
        )
    )
    if not batch:
        return None
    return _validate_existing_subtree_pruning_batch(
        batch=batch,
        parent_unit_id=parent_unit_id,
        parent_completed_event_seq=parent_completed_event_seq,
    )


def _validate_existing_subtree_pruning_batch(
    *,
    batch: tuple[LedgerEvent, ...],
    parent_unit_id: str,
    parent_completed_event_seq: int,
) -> SubtreePruningFlowResult:
    batch_sizes = {event.batch_size for event in batch}
    if len(batch) < 2 or len(batch_sizes) != 1 or None in batch_sizes:
        raise ValueError("projection inconsistent: incomplete subtree_pruning_batch")
    if next(iter(batch_sizes)) != len(batch):
        raise ValueError("projection inconsistent: incomplete subtree_pruning_batch")
    if [event.batch_index for event in batch] != list(range(1, len(batch) + 1)):
        raise ValueError("projection inconsistent: incomplete subtree_pruning_batch")
    if batch[-1].event_type != EventType.SUBTREE_PRUNED:
        raise ValueError("projection inconsistent: subtree_pruning_batch missing final marker")
    cancellation_events = batch[:-1]
    if not all(
        event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.payload.get("old_state")
        in {
            TaskState.READY.value,
            TaskState.PROCESSING.value,
            TaskState.BLOCKED.value,
        }
        and event.payload.get("new_state") == TaskState.CANCELLED.value
        for event in cancellation_events
    ):
        raise ValueError("projection inconsistent: subtree_pruning_batch cancellations")
    marker_payload = batch[-1].payload
    record_payload = marker_payload.get("subtree_prune_record")
    if not isinstance(record_payload, dict):
        raise ValueError("subtree_prune_record payload missing")
    record = SubtreePruneRecord(**record_payload)
    cancelled_unit_ids = tuple(event.object_id for event in cancellation_events)
    if (
        record.parent_unit_id != parent_unit_id
        or record.parent_completed_event_seq != parent_completed_event_seq
        or record.cancelled_unit_count != len(cancelled_unit_ids)
        or record.cancelled_unit_ids_digest
        != _cancelled_unit_ids_digest(cancelled_unit_ids)
        or marker_payload.get("cancelled_unit_ids_digest")
        != record.cancelled_unit_ids_digest
    ):
        raise ValueError("projection inconsistent: subtree_pruning_batch marker mismatch")
    return SubtreePruningFlowResult(
        subtree_prune_record=record,
        cancelled_units=cancelled_unit_ids,
        preserved_completed_unit_count=record.preserved_completed_unit_count,
        events=batch,
    )


def _validate_existing_subtree_pruning_matches_request(
    *,
    existing: SubtreePruningFlowResult,
    expected_record: SubtreePruneRecord,
    expected_cancelled_unit_ids: tuple[str, ...],
) -> None:
    if existing.subtree_prune_record != expected_record:
        raise ValueError("subtree pruning conflict")
    if existing.cancelled_units != expected_cancelled_unit_ids:
        raise ValueError("subtree pruning conflict")


def _subtree_pruning_drafts(
    *,
    subtree_prune_record: SubtreePruneRecord,
    cancellable_units: tuple[TaskUnit, ...],
    now: str,
    correlation_id: str,
    causation_event_id: str | None,
) -> tuple[EventDraft, ...]:
    drafts: list[EventDraft] = []
    for unit in cancellable_units:
        cancelled = transition_task_unit(
            unit,
            new_state=TaskState.CANCELLED,
            reason="parent_completed_post_completion_pruning",
            trigger="subtree_pruning_batch",
            changed_at=now,
        )
        drafts.append(
            EventDraft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id=unit.unit_id,
                task_id=unit.task_id,
                actor={"kind": "protocol_engine"},
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
                idempotency_key=(
                    f"task_unit:state:{unit.unit_id}:{unit.state.value}:"
                    f"Cancelled:subtree_pruning:"
                    f"{subtree_prune_record.parent_completed_event_seq}"
                ),
                payload={
                    "schema_version": "phase5.subtree_pruning_task_unit_state_changed.v1",
                    "old_state": unit.state.value,
                    "new_state": TaskState.CANCELLED.value,
                    "task_unit_state_change": _task_unit_state_change(
                        task_unit=cancelled,
                        old_state=unit.state,
                        new_state=TaskState.CANCELLED,
                        reason="parent_completed_post_completion_pruning",
                        trigger="subtree_pruning_batch",
                        correlation_id=correlation_id,
                        causation_event_id=causation_event_id,
                        changed_at=now,
                        state_context={
                            "subtree_prune_id": subtree_prune_record.subtree_prune_id,
                            "parent_unit_id": subtree_prune_record.parent_unit_id,
                            "parent_completed_event_seq": (
                                subtree_prune_record.parent_completed_event_seq
                            ),
                        },
                    ),
                    "task_unit": cancelled.to_dict(),
                    "subtree_prune_id": subtree_prune_record.subtree_prune_id,
                    "parent_unit_id": subtree_prune_record.parent_unit_id,
                    "parent_completed_event_seq": (
                        subtree_prune_record.parent_completed_event_seq
                    ),
                    "reason": "parent_completed_post_completion_pruning",
                    "correlation_id": correlation_id,
                },
                occurred_at=now,
            )
        )
    drafts.append(
        EventDraft(
            event_type=EventType.SUBTREE_PRUNED,
            object_type="SubtreePruneRecord",
            object_id=subtree_prune_record.subtree_prune_id,
            task_id=subtree_prune_record.task_id,
            actor={"kind": "protocol_engine"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=(
                f"subtree_pruned:{subtree_prune_record.parent_unit_id}:"
                f"{subtree_prune_record.parent_completed_event_seq}"
            ),
            payload={
                "schema_version": "phase5.subtree_pruned.v1",
                "subtree_prune_record": subtree_prune_record.to_dict(),
                "task_id": subtree_prune_record.task_id,
                "parent_unit_id": subtree_prune_record.parent_unit_id,
                "parent_completed_event_seq": (
                    subtree_prune_record.parent_completed_event_seq
                ),
                "pruning_policy_id": subtree_prune_record.pruning_policy_id,
                "pruning_policy_version": (
                    subtree_prune_record.pruning_policy_version
                ),
                "pruning_policy_plugin_id": (
                    subtree_prune_record.pruning_policy_plugin_id
                ),
                "pruning_policy_descriptor_digest": (
                    subtree_prune_record.pruning_policy_descriptor_digest
                ),
                "policy_source_type": subtree_prune_record.policy_source_type,
                "policy_source_id": subtree_prune_record.policy_source_id,
                "policy_source_event_seq": (
                    subtree_prune_record.policy_source_event_seq
                ),
                "cancelled_unit_count": subtree_prune_record.cancelled_unit_count,
                "cancelled_unit_ids": [unit.unit_id for unit in cancellable_units],
                "cancelled_unit_ids_digest": (
                    subtree_prune_record.cancelled_unit_ids_digest
                ),
                "preserved_completed_unit_count": (
                    subtree_prune_record.preserved_completed_unit_count
                ),
                "reason": subtree_prune_record.reason,
                "created_at": subtree_prune_record.created_at,
            },
            occurred_at=now,
        )
    )
    return tuple(drafts)


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
