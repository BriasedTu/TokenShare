"""本地协议 FULL 生命周期协调器。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Callable

from tokenshare.core.contribution import ContributionCoordinator
from tokenshare.core.merge_coordinator import BatchView, MergeCoordinator
from tokenshare.core.models import Lease, LeaseState, TaskState
from tokenshare.core.recovery import ACCEPTED, evaluate_retry
from tokenshare.core.registration import RootTaskRegistrationResult, RootTaskRegistrar
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import build_verification_report
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.local_runtime.contracts import (
    CanonicalUnitContext,
    CompleteAction,
    ExpandAction,
    GateDirective,
    MergeContext,
    MergeExecutionContext,
    MergeReadinessContext,
    MergeReadinessDecision,
    ParsedCandidateContext,
    ParentCommitStores,
    ParentStagedTraceDelivery,
    ParentTraceDeliveryStageContext,
    ParentTraceDeliveryStager,
    ParserContext,
    PreparedTraceDelivery,
    ProtocolMechanismPolicy,
    ProtocolRunRequest,
    ProtocolRunResult,
    RecoveryContext,
    RecoveryMergeContext,
    RootProtocolPlan,
    RuntimeHookObservationV1,
    UnitProgressContext,
    TraceConsumptionCore,
    TraceConsumptionRecord,
    TraceDeliveryAttempt,
    VerificationContext,
    LOGICAL_DISPATCH_START_MS_HINT,
    build_experiment_premature_merge_attempted_observation,
)
from tokenshare.local_runtime.projection import (
    build_runtime_observation,
    project_protocol_run,
)
from tokenshare.local_runtime.logical_scheduler import (
    EVENT_PRIORITY_BY_KIND,
    LogicalScheduledEvent,
    LogicalSchedulerCheckpoint,
    LogicalSourceLatencyScheduler,
)
from tokenshare.local_runtime.workers import WorkerBatchOutcome, execute_worker_batch
from tokenshare.protocol_engine import (
    ExecutionRequestFlowResult,
    ProtocolEngine,
    SchedulingFlowResult,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


@dataclass(frozen=True, kw_only=True)
class LogicalPendingExecution:
    """把 queue event 绑定回 parent 已准备但尚未提交的 worker 结果。"""

    event: LogicalScheduledEvent | None
    execution_request: object
    scheduled: SchedulingFlowResult
    request_flow: ExecutionRequestFlowResult
    worker_outcome: WorkerBatchOutcome

    def __post_init__(self) -> None:
        if self.request_flow.request != self.execution_request:
            raise ValueError("pending execution request flow identity mismatch")
        if self.worker_outcome.request != self.execution_request:
            raise ValueError("pending worker outcome request identity mismatch")
        if self.event is not None and (
            self.event.task_id != self.scheduled.task_unit.task_id
            or self.event.unit_id != self.scheduled.task_unit.unit_id
        ):
            raise ValueError("pending logical event identity mismatch")


def commit_prepared_delivery(
    *,
    delivery: PreparedTraceDelivery,
    worker_completion: object,
    killed_worker_ids,
    active_lease: Lease,
    current_fencing_token: str,
    current_stores: ParentCommitStores,
) -> TraceConsumptionRecord:
    """在parent验证worker/lease后，以单event公开durable staged artifacts。"""

    if not isinstance(delivery, PreparedTraceDelivery):
        raise TypeError("delivery must be PreparedTraceDelivery")
    if not isinstance(current_stores, ParentCommitStores):
        raise TypeError("current_stores must be ParentCommitStores")
    fact = getattr(worker_completion, "fact", worker_completion)
    sequence = getattr(fact, "execution_index", None)
    worker_id = getattr(fact, "worker_id", None)
    if sequence != delivery.child_completion_sequence:
        raise ValueError("worker completion sequence mismatch")
    if worker_id != delivery.child_worker_id:
        raise ValueError("worker completion identity mismatch")
    if delivery.child_worker_id in frozenset(killed_worker_ids):
        raise ValueError("killed worker cannot commit a delivery")
    if getattr(fact, "result_kind", None) in {
        "worker_terminated",
        "fenced",
        "stale_fencing",
    } or getattr(worker_completion, "failure_kind", None) in {
        "worker_terminated",
        "fenced",
        "stale_fencing",
    }:
        raise ValueError("killed or fenced worker cannot commit a delivery")
    if not isinstance(active_lease, Lease) or active_lease.state != LeaseState.ACTIVE:
        raise ValueError("active lease is required")
    if type(current_fencing_token) is not str or not current_fencing_token:
        raise ValueError("current fencing token must be a non-empty string")
    if active_lease.fencing_token != current_fencing_token:
        raise ValueError("current fencing token does not match active lease")
    if (
        active_lease.task_id != delivery.task_id
        or active_lease.unit_id != delivery.unit_id
        or active_lease.attempt_id != delivery.attempt_id
    ):
        raise ValueError("active lease attempt identity mismatch")
    if active_lease.attempt_ordinal != delivery.attempt_ordinal:
        raise ValueError("active lease attempt ordinal mismatch")
    lease_binding = active_lease.binding_digest or active_lease.metadata.get(
        "binding_digest"
    )
    request = getattr(worker_completion, "request", None)
    request_binding = getattr(request, "source_binding_digest", None)
    if (
        (request_binding is None and lease_binding is None)
        or (
            request_binding is not None
            and request_binding != delivery.binding_digest
        )
        or (lease_binding is not None and lease_binding != delivery.binding_digest)
    ):
        raise ValueError("active lease binding digest mismatch")
    if request is not None and (
        getattr(request, "task_id", None) != delivery.task_id
        or getattr(request, "unit_id", None) != delivery.unit_id
        or getattr(request, "lease_id", None) != active_lease.lease_id
        or getattr(request, "attempt_ordinal", None) != delivery.attempt_ordinal
        or getattr(request, "attempt_id", None) != delivery.attempt_id
        or getattr(request, "fencing_token", None) != current_fencing_token
    ):
        raise ValueError("dispatch request does not match prepared delivery")

    # 在任何stage前先验证既有ledger，损坏或partial tail必须fail closed。
    current_stores.event_ledger.read_verified_snapshot()
    digest_key = delivery.delivery_digest.removeprefix("sha256:")
    created_at = "1970-01-01T00:00:00Z"
    staged_source = {
        "kind": "prepared_trace_delivery",
        "delivery_digest": delivery.delivery_digest,
    }

    wrapper_ref = current_stores.artifact_store.save_external_trace_wrapper(
        delivery.to_dict(),
        artifact_id=f"trace_wrapper_{digest_key}",
        created_at=created_at,
    )
    _commit_hook(current_stores, "wrapper_staged")
    parser_input_ref = current_stores.artifact_store.save_json(
        {
            "schema_version": "tokenshare.trace_parser_input.v1",
            "media_type": delivery.parser_input_media_type,
            "content_digest": delivery.parser_input_digest,
            "source_bank_object_locators": [
                dict(item) for item in delivery.source_bank_object_locators
            ],
        },
        artifact_id=f"trace_parser_input_{digest_key}",
        artifact_type="TraceParserInput",
        artifact_schema_id="tokenshare.trace_parser_input",
        artifact_schema_version="v1",
        source=staged_source,
        metadata={"attempt_id": delivery.attempt_id},
        created_at=created_at,
    )
    _commit_hook(current_stores, "parser_input_staged")
    if current_stores.trace_delivery_stager is None:
        parser_result_ref = current_stores.artifact_store.save_json(
            {
                "schema_version": "tokenshare.trace_parser_result.v1",
                "parser_input_digest": delivery.parser_input_digest,
                "source_terminal_kind": delivery.source_terminal_kind,
                "status": "prepared",
            },
            artifact_id=f"trace_parser_result_{digest_key}",
            artifact_type="TraceParserResult",
            artifact_schema_id="tokenshare.trace_parser_result",
            artifact_schema_version="v1",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=created_at,
        )
        _commit_hook(current_stores, "parser_result_staged")
        provenance_ref = current_stores.artifact_store.save_json(
            {
                "schema_version": "tokenshare.current_trace_provenance.v1",
                "bank_root_id": delivery.bank_root_id,
                "manifest_digest": delivery.manifest_digest,
                "entry_id": delivery.entry_id,
                "inference_request_digest": delivery.inference_request_digest,
                "logical_start_ms": delivery.logical_start_ms,
                "source_latency_ms": delivery.source_latency_ms,
                "logical_finish_ms": delivery.logical_finish_ms,
            },
            artifact_id=f"trace_provenance_{digest_key}",
            artifact_type="CurrentTraceProvenance",
            artifact_schema_id="tokenshare.current_trace_provenance",
            artifact_schema_version="v1",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=created_at,
        )
        _commit_hook(current_stores, "provenance_staged")
        attribution_ref = current_stores.artifact_store.save_json(
            {
                "schema_version": "tokenshare.trace_attribution.v1",
                "delivery_digest": delivery.delivery_digest,
                "source_bank_object_locators": [
                    dict(item) for item in delivery.source_bank_object_locators
                ],
            },
            artifact_id=f"trace_attribution_{digest_key}",
            artifact_type="TraceAttribution",
            artifact_schema_id="tokenshare.trace_attribution",
            artifact_schema_version="v1",
            source=staged_source,
            metadata={"attempt_id": delivery.attempt_id},
            created_at=created_at,
        )
        staged = ParentStagedTraceDelivery(
            parser_result_ref=parser_result_ref,
            current_provenance_ref=provenance_ref,
            verifier_checker_refs=(),
            canonical_ref=None,
            trace_attribution_refs=(attribution_ref,),
        )
        _commit_hook(current_stores, "artifacts_staged")
    else:
        if not isinstance(request, ExecutionRequest):
            raise ValueError("typed trace delivery stager requires dispatch request")
        staged = current_stores.trace_delivery_stager.stage(
            ParentTraceDeliveryStageContext(
                delivery=delivery,
                request=request,
                artifact_store=current_stores.artifact_store,
                current_wrapper_ref=wrapper_ref,
                parser_input_ref=parser_input_ref,
                created_at=created_at,
            )
        )
        if not isinstance(staged, ParentStagedTraceDelivery):
            raise TypeError("trace delivery stager must return ParentStagedTraceDelivery")
        parser_result_ref = staged.parser_result_ref
        provenance_ref = staged.current_provenance_ref
        _commit_hook(current_stores, "parser_result_staged")
        _commit_hook(current_stores, "provenance_staged")
        _commit_hook(current_stores, "artifacts_staged")

    staged_refs = (
        parser_result_ref,
        provenance_ref,
        *staged.verifier_checker_refs,
        *staged.trace_attribution_refs,
        *((staged.canonical_ref,) if staged.canonical_ref is not None else ()),
    )
    if any(not current_stores.artifact_store.verify(ref) for ref in staged_refs):
        raise ValueError("trace delivery stager returned a non-current artifact ref")

    core = TraceConsumptionCore(
        attempt_id=delivery.attempt_id,
        binding_digest=delivery.binding_digest,
        current_fencing_token=current_fencing_token,
        status="delivered",
        current_wrapper_ref=wrapper_ref,
        parser_input_ref=parser_input_ref,
        parser_result_ref=parser_result_ref,
        current_provenance_ref=provenance_ref,
        verifier_checker_refs=staged.verifier_checker_refs,
        canonical_ref=staged.canonical_ref,
        trace_attribution_refs=staged.trace_attribution_refs,
    )
    event = current_stores.event_ledger.append(
        event_type=EventType.TRACE_DELIVERY_COMMITTED,
        object_type="TraceConsumption",
        object_id=delivery.attempt_id,
        task_id=delivery.task_id,
        actor={"kind": "protocol_parent"},
        correlation_id=delivery.current_run_id,
        idempotency_key=f"trace_delivery_committed:{delivery.attempt_id}",
        payload=core.to_dict(),
    )
    _commit_hook(current_stores, "commit_event_appended")
    if current_stores.sqlite_index is not None:
        snapshot = current_stores.event_ledger.read_verified_snapshot()
        current_stores.sqlite_index.rebuild_from_events(list(snapshot.events))
    _commit_hook(current_stores, "projection_applied")
    return TraceConsumptionRecord(
        core=core,
        committed_event_ref={
            "event_id": event.event_id,
            "event_seq": event.event_seq,
            "event_type": EventType.TRACE_DELIVERY_COMMITTED.value,
            "event_hash": event.event_hash,
        },
    )


def commit_worker_outcome(
    *,
    outcome: WorkerBatchOutcome,
    killed_worker_ids,
    active_lease: Lease,
    current_fencing_token: str,
    current_stores: ParentCommitStores,
) -> TraceConsumptionRecord:
    """让所有worker backend统一经过parent-owned delivery commit边界。"""

    if not isinstance(outcome, WorkerBatchOutcome):
        raise TypeError("outcome must be WorkerBatchOutcome")
    if outcome.prepared_delivery is None:
        raise ValueError("worker outcome has no prepared trace delivery")
    if outcome.submission is not None:
        raise ValueError("prepared trace delivery cannot include a submission")
    return commit_prepared_delivery(
        delivery=outcome.prepared_delivery,
        worker_completion=outcome,
        killed_worker_ids=killed_worker_ids,
        active_lease=active_lease,
        current_fencing_token=current_fencing_token,
        current_stores=current_stores,
    )


def build_trace_delivery_attempt(
    *,
    outcome: WorkerBatchOutcome,
    status: str,
    event: LedgerEvent,
) -> TraceDeliveryAttempt:
    """从既有attempt状态event派生killed/fenced delivery审计记录。"""

    if not isinstance(outcome, WorkerBatchOutcome):
        raise TypeError("outcome must be WorkerBatchOutcome")
    delivery = outcome.prepared_delivery
    if delivery is None:
        raise ValueError("worker outcome has no prepared trace delivery")
    if status not in {"killed", "fenced"}:
        raise ValueError("terminal trace delivery status must be killed or fenced")
    fact_kind = outcome.fact.result_kind
    if status == "killed" and fact_kind != "worker_terminated":
        raise ValueError("killed delivery requires worker termination fact")
    if status == "fenced" and fact_kind not in {"fenced", "stale_fencing"}:
        raise ValueError("fenced delivery requires fencing fact")
    if not isinstance(event, LedgerEvent):
        raise TypeError("event must be a LedgerEvent")
    if (
        event.event_type != EventType.ATTEMPT_STATE_CHANGED
        or event.object_id != delivery.attempt_id
    ):
        raise ValueError("trace delivery attempt requires matching attempt state event")
    if event.payload.get("new_state") not in {"Failed", "Rejected", "Superseded"}:
        raise ValueError("terminal trace delivery requires a terminal attempt event")
    return TraceDeliveryAttempt(
        attempt_id=delivery.attempt_id,
        binding_digest=delivery.binding_digest,
        status=status,
        event_ref=_event_ref(event),
    )


def _commit_hook(stores: ParentCommitStores, stage: str) -> None:
    if stores.commit_hook is not None:
        stores.commit_hook(stage)


def _bind_execution_request(
    request: ExecutionRequest,
    *,
    attempt_ordinal: int,
    logical_dispatch_start_ms: int | None = None,
) -> ExecutionRequest:
    """在dispatch前固定core-neutral ordinal/source binding。"""

    if not isinstance(request, ExecutionRequest):
        raise TypeError("plugin runtime must build an ExecutionRequest")
    if type(attempt_ordinal) is not int or attempt_ordinal < 0:
        raise ValueError("persisted attempt ordinal must be a non-negative integer")
    binding_digest = request.source_binding_digest
    if binding_digest is None:
        body = {
            "schema_version": "tokenshare.source_binding.v1",
            "request_id": request.request_id,
            "task_id": request.task_id,
            "unit_id": request.unit_id,
            "attempt_id": request.attempt_id,
            "attempt_ordinal": attempt_ordinal,
            "lease_id": request.lease_id,
            "fencing_token": request.fencing_token,
        }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        binding_digest = f"sha256:{sha256(encoded).hexdigest()}"
    elif not _is_sha256_digest(binding_digest):
        raise ValueError("source binding digest must be a sha256 digest")
    soft_hints = dict(request.soft_hints or {})
    # replacement identity 只能来自协议已持久化 ordinal；插件 hint 不拥有该权威。
    soft_hints["replacement_slot"] = attempt_ordinal
    if logical_dispatch_start_ms is None:
        soft_hints.pop(LOGICAL_DISPATCH_START_MS_HINT, None)
    else:
        if (
            isinstance(logical_dispatch_start_ms, bool)
            or not isinstance(logical_dispatch_start_ms, int)
            or logical_dispatch_start_ms < 0
        ):
            raise ValueError(
                "logical dispatch start must be a non-negative integer"
            )
        # 当前 scheduler clock 由协议 parent 冻结，插件 hint 不拥有 timing 权威。
        soft_hints[LOGICAL_DISPATCH_START_MS_HINT] = logical_dispatch_start_ms
    return replace(
        request,
        attempt_ordinal=attempt_ordinal,
        source_binding_digest=binding_digest,
        soft_hints=soft_hints,
    )


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _trace_delivery_terminal_status(
    outcome: WorkerBatchOutcome,
) -> str | None:
    """只把带prepared delivery的明确worker终止/fence事实归为终态。"""

    if outcome.prepared_delivery is None:
        return None
    kinds = {outcome.failure_kind, outcome.fact.result_kind}
    if "worker_terminated" in kinds:
        return "killed"
    if kinds & {"fenced", "stale_fencing"}:
        return "fenced"
    return None


def _trace_consumption_submission(
    *,
    request: ExecutionRequest,
    delivery: PreparedTraceDelivery,
    consumption: TraceConsumptionRecord,
    submitted_at: str,
    artifact_store: ArtifactStore,
) -> ExecutionSubmission:
    """把parent已公开的trace artifacts规范化为普通engine submission输入。"""

    if consumption.core.attempt_id != request.attempt_id:
        raise ValueError("trace consumption does not match execution request")
    executor_id = request.executor.get("executor_id")
    executor_version = request.executor.get("executor_version")
    if not isinstance(executor_id, str) or not executor_id:
        raise ValueError("trace execution request has no executor_id")
    if not isinstance(executor_version, str) or not executor_version:
        raise ValueError("trace execution request has no executor_version")
    succeeded = delivery.source_terminal_kind == "success"
    parser_result_ref = consumption.core.parser_result_ref
    candidate_ref = consumption.core.canonical_ref or parser_result_ref
    failure_error = None
    if not succeeded:
        failure_body = json.loads(
            artifact_store.read_bytes(parser_result_ref).decode("utf-8")
        )
        if not isinstance(failure_body, dict):
            raise ValueError("trace provider failure artifact must be an object")
        failure_error = {
            "kind": str(failure_body.get("failure_kind", "provider_failure")),
            "message": str(
                failure_body.get(
                    "message", "prepared source trace ended in provider failure"
                )
            ),
            "http_status": failure_body.get("http_status"),
        }
    return ExecutionSubmission(
        submission_id=(
            "trace_submission_"
            f"{delivery.delivery_digest.removeprefix('sha256:')}"
        ),
        request_id=request.request_id,
        task_id=request.task_id,
        unit_id=request.unit_id,
        attempt_id=request.attempt_id,
        lease_id=request.lease_id,
        fencing_token=request.fencing_token,
        executor_id=executor_id,
        executor_version=executor_version,
        result_kind="succeeded" if succeeded else "failed",
        raw_output_ref=consumption.core.current_wrapper_ref,
        parsed_output_ref=parser_result_ref,
        candidate_output_refs=(
            {
                output_name: candidate_ref
                for output_name in request.output_contract.required_outputs
            }
            if succeeded
            else {}
        ),
        parse_failure_ref=None,
        log_ref=None,
        environment_ref=request.environment_ref,
        environment_summary={
            "runtime": "prepared_trace_delivery",
            "current_transport": "none",
        },
        provenance_ref=consumption.core.current_provenance_ref,
        usage_summary={
            "provider_attempt_count": 0,
            "current_provider_call_count": 0,
            "current_provider_spend": 0,
            "source_usage_class": "trace_attribution",
        },
        error=failure_error,
        submitted_at=submitted_at,
    )


@dataclass(frozen=True, kw_only=True)
class LogicalRunCheckpoint:
    """Task9 的进程内 coordinator/scheduler 一致快照。"""

    run_id: str
    request_context: ProtocolRunRequest
    scheduler_checkpoint: LogicalSchedulerCheckpoint
    runtime_started_at: str
    run_key: str
    plan: RootProtocolPlan
    registration: RootTaskRegistrationResult
    graph: TaskGraph
    retry_counts: tuple[tuple[str, int], ...]
    completion_batches: tuple[BatchView, ...]
    expansion_batch: BatchView | None
    expand_result: object | None
    merge_plan: object | None
    merge_action: object | None
    merge_children: tuple[object, ...]
    merge_creation: object | None
    merge_resolution_batch: BatchView | None
    schedule_ordinal: int
    terminal_child_failure: object | None
    pending_executions: tuple[LogicalPendingExecution, ...]
    runtime_observations: tuple[RuntimeHookObservationV1, ...]
    trace_delivery_attempts: tuple[TraceDeliveryAttempt, ...]
    worker_execution_facts: tuple[dict[str, object], ...]
    partial_observation: bool
    witness_observed_at: str | None
    in_flight_ai_unit_ids_at_witness: tuple[str, ...]
    schema_version: str = "tokenshare.logical_run_checkpoint.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.logical_run_checkpoint.v1":
            raise ValueError("unsupported logical run checkpoint schema")
        if self.run_id != self.request_context.run_id:
            raise ValueError("logical run checkpoint request identity mismatch")
        queued = set(self.scheduler_checkpoint.pending_events)
        pending = {
            item.event for item in self.pending_executions if item.event is not None
        }
        if any(item.event is None for item in self.pending_executions):
            raise ValueError("logical run checkpoint contains a non-logical pending entry")
        if queued != pending:
            raise ValueError("logical run checkpoint queue/context mismatch")


@dataclass(frozen=True, kw_only=True)
class _DeferredRecovery:
    scheduled: SchedulingFlowResult
    trigger: str
    causation_event_id: str
    attempt_for_recovery: object | None
    recovery_now: str | None
    halt_run: bool
    trace_delivery_outcome: WorkerBatchOutcome | None = None
    trace_delivery_status: str | None = None


@dataclass(frozen=True, kw_only=True)
class _AppliedRecovery:
    scheduled: SchedulingFlowResult
    graph: TaskGraph
    trigger: str
    decision: object
    recovery: object
    recovery_event_refs: tuple[dict[str, object], ...]
    halt_run: bool
    trace_delivery_attempt: TraceDeliveryAttempt | None = None


@dataclass(frozen=True, kw_only=True)
class _LogicalPendingRetry:
    event: LogicalScheduledEvent
    recovery: _DeferredRecovery


@dataclass(frozen=True, kw_only=True)
class _LogicalPendingRequeue:
    event: LogicalScheduledEvent
    recovery: _AppliedRecovery


@dataclass(frozen=True, kw_only=True)
class _EarlyStopFinalization:
    parent_unit_id: str
    parent_completed_event_seq: int
    candidate_descendant_units: tuple[object, ...]
    pruning_policy_ref: dict[str, object]
    causation_event_id: str
    settlement_completion_event_seq: int
    settlement_contributions: tuple[object, ...]


@dataclass(frozen=True, kw_only=True)
class _LogicalPendingEarlyStop:
    event: LogicalScheduledEvent
    finalization: _EarlyStopFinalization


class ProtocolRunCoordinator:
    """一次只驱动一个 root；权威状态写入全部委托给既有 API。"""

    def __init__(
        self,
        *,
        engine: ProtocolEngine,
        artifact_store: ArtifactStore,
        event_ledger: EventLedger,
        now: Callable[[], str] | None = None,
        observation_clock: Callable[[], str] | None = None,
        trace_delivery_stager: ParentTraceDeliveryStager | None = None,
    ) -> None:
        self._engine = engine
        self._artifact_store = artifact_store
        self._event_ledger = event_ledger
        self._now = now or _utc_now
        self._observation_clock = observation_clock or _utc_now
        self._trace_delivery_stager = trace_delivery_stager
        self._logical_scheduler: LogicalSourceLatencyScheduler | None = None

    @property
    def logical_scheduler(self) -> LogicalSourceLatencyScheduler | None:
        return self._logical_scheduler

    def run_root(self, request: ProtocolRunRequest) -> ProtocolRunResult:
        result = self._run_root(request)
        if not isinstance(result, ProtocolRunResult):
            raise RuntimeError("run_root stopped at an unexpected checkpoint")
        return result

    def checkpoint_root(self, request: ProtocolRunRequest) -> LogicalRunCheckpoint:
        if request.logical_scheduler is None:
            raise ValueError("checkpoint_root requires a logical scheduler")
        result = self._run_root(request, checkpoint_after_dispatch=True)
        if not isinstance(result, LogicalRunCheckpoint):
            raise RuntimeError("logical run completed before a checkpoint was available")
        return result

    def resume_root(
        self,
        request: ProtocolRunRequest,
        checkpoint: LogicalRunCheckpoint,
    ) -> ProtocolRunResult:
        if not isinstance(checkpoint, LogicalRunCheckpoint):
            raise TypeError("checkpoint must be LogicalRunCheckpoint")
        _validate_resume_request(request, checkpoint.request_context)
        restored_scheduler = LogicalSourceLatencyScheduler.from_checkpoint(
            checkpoint.scheduler_checkpoint
        )
        resumed_request = replace(request, logical_scheduler=restored_scheduler)
        result = self._run_root(
            resumed_request,
            resume_checkpoint=checkpoint,
        )
        if not isinstance(result, ProtocolRunResult):
            raise RuntimeError("resumed run stopped at an unexpected checkpoint")
        return result

    def _run_root(
        self,
        request: ProtocolRunRequest,
        *,
        checkpoint_after_dispatch: bool = False,
        resume_checkpoint: LogicalRunCheckpoint | None = None,
    ) -> ProtocolRunResult | LogicalRunCheckpoint:
        if request.worker_backend.capacity < 1:
            raise ValueError("runtime worker capacity must be positive")
        if request.worker_backend.capacity != 1 and not callable(
            getattr(request.worker_backend, "execute_batch", None)
        ):
            raise ValueError(
                "multi-worker backend requires execute_batch; use a single-worker-compatible backend"
            )
        logical_scheduler = request.logical_scheduler
        if logical_scheduler is not None and not isinstance(
            logical_scheduler, LogicalSourceLatencyScheduler
        ):
            raise TypeError("logical_scheduler must be LogicalSourceLatencyScheduler")
        self._logical_scheduler = logical_scheduler
        if (
            logical_scheduler is not None
            and not logical_scheduler.has_wall_clock_origin
        ):
            logical_scheduler.bind_wall_clock_origin(self._now())
        if resume_checkpoint is None:
            runtime_started_at = self._observation_now(request)
            run_key = _safe_id(request.run_id)
            plan = request.plugin_runtime.plan_root(
                request.root_input,
                artifact_store=self._artifact_store,
            )
            if not isinstance(plan, RootProtocolPlan):
                raise TypeError("plan_root must return RootProtocolPlan")
            registration = RootTaskRegistrar(
                artifact_store=self._artifact_store,
                event_ledger=self._event_ledger,
            ).register_root_task(plan.registration_request)
            self._engine.record_registry_snapshot(
                task_id=registration.task_spec.task_id,
                registry_snapshot_id=plan.registry_snapshot_id,
                plugin_registry=plan.plugin_registry,
                executor_registry=plan.executor_registry,
                now=self._protocol_now(request),
                correlation_id=f"{request.run_id}:registry",
            )
            graph = TaskGraph(
                task_id=registration.task_spec.task_id,
                units={registration.root_unit.unit_id: registration.root_unit},
                relations=(),
                protocol_config=plan.registration_request.protocol_config,
            )
            retry_counts: dict[str, int] = {}
            completion_batches: list[BatchView] = []
            expansion_batch: BatchView | None = None
            expand_result = None
            merge_plan = None
            merge_action = None
            merge_children: tuple[object, ...] = ()
            merge_creation = None
            merge_resolution_batch: BatchView | None = None
            schedule_ordinal = 0
            terminal_child_failure = None
            terminal_infrastructure_failure = None
            pending_executions: list[
                LogicalPendingExecution
                | _LogicalPendingRetry
                | _LogicalPendingRequeue
                | _LogicalPendingEarlyStop
            ] = []
            runtime_observations: list[RuntimeHookObservationV1] = []
            trace_delivery_attempts: list[TraceDeliveryAttempt] = []
            worker_execution_facts: list[dict[str, object]] = []
            partial_observation = False
            witness_observed_at: str | None = None
            in_flight_ai_unit_ids_at_witness: tuple[str, ...] = ()
            recovery_merge_once_keys: set[tuple[str, str]] = set()
        else:
            runtime_started_at = resume_checkpoint.runtime_started_at
            run_key = resume_checkpoint.run_key
            plan = resume_checkpoint.plan
            registration = resume_checkpoint.registration
            graph = resume_checkpoint.graph
            retry_counts = dict(resume_checkpoint.retry_counts)
            completion_batches = list(resume_checkpoint.completion_batches)
            expansion_batch = resume_checkpoint.expansion_batch
            expand_result = resume_checkpoint.expand_result
            merge_plan = resume_checkpoint.merge_plan
            merge_action = resume_checkpoint.merge_action
            merge_children = resume_checkpoint.merge_children
            merge_creation = resume_checkpoint.merge_creation
            merge_resolution_batch = resume_checkpoint.merge_resolution_batch
            schedule_ordinal = resume_checkpoint.schedule_ordinal
            terminal_child_failure = resume_checkpoint.terminal_child_failure
            terminal_infrastructure_failure = None
            pending_executions = list(resume_checkpoint.pending_executions)
            runtime_observations = list(resume_checkpoint.runtime_observations)
            trace_delivery_attempts = list(
                resume_checkpoint.trace_delivery_attempts
            )
            worker_execution_facts = [
                dict(item) for item in resume_checkpoint.worker_execution_facts
            ]
            partial_observation = resume_checkpoint.partial_observation
            witness_observed_at = resume_checkpoint.witness_observed_at
            in_flight_ai_unit_ids_at_witness = (
                resume_checkpoint.in_flight_ai_unit_ids_at_witness
            )
            recovery_merge_once_keys = set()

        while True:
            pending_control = any(
                not isinstance(item, LogicalPendingExecution)
                for item in pending_executions
            )
            activatable = (
                graph.activatable_unit_ids()
                if (
                    logical_scheduler is not None
                    and not pending_control
                )
                or not pending_executions
                else ()
            )
            activatable = _scoped_unit_ids(
                request=request,
                graph=graph,
                unit_ids=activatable,
                expansion_started=expand_result is not None,
            )
            if merge_creation is not None:
                activatable = tuple(
                    unit_id
                    for unit_id in activatable
                    if unit_id == merge_creation.merge_task_unit.unit_id
                )
            if activatable:
                unit_id = activatable[0]
                activated = self._engine.record_dependency_ready(
                    unit=graph.units[unit_id],
                    graph=graph,
                    now=self._protocol_now(request),
                    correlation_id=f"{run_key}_dependency_ready_{unit_id}",
                )
                graph = _replace_graph_unit(graph, activated.task_unit)
                continue
            ready = _scoped_unit_ids(
                request=request,
                graph=graph,
                unit_ids=graph.ready_unit_ids(),
                expansion_started=expand_result is not None,
            )
            if merge_creation is not None:
                ready = tuple(
                    unit_id
                    for unit_id in ready
                    if unit_id == merge_creation.merge_task_unit.unit_id
                )
            readiness_before_dispatch = None
            if (
                expand_result is not None
                and merge_creation is None
                and (logical_scheduler is not None or not pending_executions)
                and not pending_control
                and _plugin_owns_merge_readiness(request)
            ):
                readiness_before_dispatch, _, _, _ = (
                    _current_merge_readiness(
                        request=request,
                        graph=graph,
                        event_ledger=self._event_ledger,
                        root_unit_id=registration.root_unit.unit_id,
                        child_units=tuple(expand_result.child_units),
                        merge_plan=merge_plan,
                    )
                )
                if (
                    readiness_before_dispatch.status == "ready"
                    and witness_observed_at is None
                ):
                    witness_observed_at = self._observation_now(request)
                    in_flight_ai_unit_ids_at_witness = (
                        _observed_in_flight_ai_unit_ids(
                            request=request,
                            graph=graph,
                            worker_execution_facts=worker_execution_facts,
                            observed_at=witness_observed_at,
                        )
                    )
            merge_ready_before_dispatch = (
                readiness_before_dispatch is not None
                and readiness_before_dispatch.status == "ready"
            )
            dispatch_slots = (
                request.worker_backend.capacity
                - sum(
                    isinstance(item, LogicalPendingExecution)
                    for item in pending_executions
                )
                if logical_scheduler is not None
                else request.worker_backend.capacity
                if not pending_executions
                else 0
            )
            should_dispatch = bool(
                dispatch_slots > 0
                and ready
                and not merge_ready_before_dispatch
                and not pending_control
            )
            if pending_executions or should_dispatch:
                if should_dispatch:
                    prepared: list[tuple[object, object, object]] = []
                    for _slot in range(dispatch_slots):
                        scoped_ready = _scoped_unit_ids(
                            request=request,
                            graph=graph,
                            unit_ids=graph.ready_unit_ids(),
                            expansion_started=expand_result is not None,
                        )
                        if merge_creation is not None:
                            scoped_ready = tuple(
                                unit_id
                                for unit_id in scoped_ready
                                if unit_id
                                == merge_creation.merge_task_unit.unit_id
                            )
                        if not scoped_ready:
                            break
                        schedule_ordinal += 1
                        ordinal = schedule_ordinal
                        scheduled = self._engine.schedule_ready_unit(
                            graph=graph,
                            clients=plan.clients,
                            now=self._protocol_now(request),
                            correlation_id=f"{run_key}_schedule_{ordinal}",
                            decision_id=f"{run_key}_decision_{ordinal}",
                            lease_id=f"{run_key}_lease_{ordinal}",
                            attempt_id=f"{run_key}_attempt_{ordinal}",
                            fencing_token=f"{run_key}_fence_{ordinal}",
                            allowed_unit_ids=scoped_ready,
                        )
                        graph = _replace_graph_unit(graph, scheduled.task_unit)
                        progress_directive = _observe(
                            request.hooks.on_unit_progress,
                            UnitProgressContext(
                                stage="scheduled",
                                unit=scheduled.task_unit,
                                attempt=scheduled.attempt,
                                lease=scheduled.lease,
                            ),
                        )
                        _collect_observations(
                            runtime_observations, progress_directive
                        )
                        execution_request, request_flow = self._prepare_unit_execution(
                                request=request,
                                scheduled=scheduled,
                            )
                        prepared.append(
                            (execution_request, request_flow, scheduled)
                        )
                    worker_outcomes = execute_worker_batch(
                        request.worker_backend,
                        tuple(item[0] for item in prepared),
                    )
                    matched_outcomes = _match_worker_outcomes(
                        prepared=prepared,
                        worker_outcomes=worker_outcomes,
                    )
                    if logical_scheduler is None:
                        worker_execution_facts.extend(
                            outcome.fact.to_dict()
                            for outcome in sorted(
                                worker_outcomes,
                                key=lambda item: item.fact.execution_index,
                            )
                        )
                    for (
                        execution_request,
                        request_flow,
                        scheduled,
                    ), worker_outcome in matched_outcomes:
                        scheduled_event = None
                        if logical_scheduler is not None:
                            completion = worker_outcome.logical_completion
                            if completion is None:
                                raise ValueError(
                                    "logical_source_latency_1x requires worker completion timing"
                                )
                            if (
                                completion.attempt_ordinal
                                != execution_request.attempt_ordinal
                            ):
                                raise ValueError(
                                    "worker completion attempt ordinal does not match persisted request"
                                )
                            event_kind = _completion_event_kind(
                                worker_outcome, completion.event_kind
                            )
                            if completion.event_priority != EVENT_PRIORITY_BY_KIND[
                                "worker_completion"
                            ]:
                                raise ValueError(
                                    "worker completion priority must match the frozen profile"
                                )
                            if event_kind == "worker_completion":
                                scheduled_event = (
                                    logical_scheduler.schedule_delivery(
                                        task_id=execution_request.task_id,
                                        unit_id=execution_request.unit_id,
                                        attempt_ordinal=(
                                            execution_request.attempt_ordinal
                                        ),
                                        source_latency_ms=(
                                            completion.source_latency_ms
                                        ),
                                        event_priority=EVENT_PRIORITY_BY_KIND[
                                            event_kind
                                        ],
                                    )
                                )
                            else:
                                delay_ms = _lifecycle_delay_ms(
                                    scheduled=scheduled,
                                    worker_outcome=worker_outcome,
                                    source_latency_ms=(
                                        completion.source_latency_ms
                                    ),
                                )
                                scheduled_event = (
                                    logical_scheduler.schedule_event(
                                        event_kind=event_kind,
                                        logical_time_ms=(
                                            logical_scheduler.clock_ms + delay_ms
                                        ),
                                        event_priority=EVENT_PRIORITY_BY_KIND[
                                            event_kind
                                        ],
                                        task_id=execution_request.task_id,
                                        unit_id=execution_request.unit_id,
                                        attempt_ordinal=(
                                            execution_request.attempt_ordinal
                                        ),
                                    )
                                )
                        pending_executions.append(
                            LogicalPendingExecution(
                                event=scheduled_event,
                                execution_request=execution_request,
                                scheduled=scheduled,
                                request_flow=request_flow,
                                worker_outcome=worker_outcome,
                            )
                        )
                    if checkpoint_after_dispatch:
                        return LogicalRunCheckpoint(
                            run_id=request.run_id,
                            request_context=request,
                            scheduler_checkpoint=logical_scheduler.checkpoint(),
                            runtime_started_at=runtime_started_at,
                            run_key=run_key,
                            plan=plan,
                            registration=registration,
                            graph=graph,
                            retry_counts=tuple(sorted(retry_counts.items())),
                            completion_batches=tuple(completion_batches),
                            expansion_batch=expansion_batch,
                            expand_result=expand_result,
                            merge_plan=merge_plan,
                            merge_action=merge_action,
                            merge_children=merge_children,
                            merge_creation=merge_creation,
                            merge_resolution_batch=merge_resolution_batch,
                            schedule_ordinal=schedule_ordinal,
                            terminal_child_failure=terminal_child_failure,
                            pending_executions=tuple(pending_executions),
                            runtime_observations=tuple(runtime_observations),
                            trace_delivery_attempts=tuple(trace_delivery_attempts),
                            worker_execution_facts=tuple(
                                dict(item) for item in worker_execution_facts
                            ),
                            partial_observation=partial_observation,
                            witness_observed_at=witness_observed_at,
                            in_flight_ai_unit_ids_at_witness=(
                                in_flight_ai_unit_ids_at_witness
                            ),
                        )
                if not pending_executions:
                    raise RuntimeError("worker dispatch produced no pending completion")
                if logical_scheduler is None:
                    pending_execution = pending_executions.pop(0)
                    if not isinstance(pending_execution, LogicalPendingExecution):
                        raise RuntimeError("non-logical run received a control event")
                    scheduled = pending_execution.scheduled
                    request_flow = pending_execution.request_flow
                    worker_outcome = pending_execution.worker_outcome
                    execution = self._execute_unit(
                        request=request,
                        plan=plan,
                        graph=graph,
                        scheduled=scheduled,
                        request_flow=request_flow,
                        worker_outcome=worker_outcome,
                        retry_counts=retry_counts,
                        runtime_observations=runtime_observations,
                        recovery_merge_snapshot=(
                            registration.root_unit.unit_id,
                            tuple(expand_result.child_units) if expand_result is not None else (),
                            merge_plan,
                            recovery_merge_once_keys,
                        ),
                    )
                else:
                    popped_event = logical_scheduler.pop_next()
                    matching_indexes = [
                        index
                        for index, item in enumerate(pending_executions)
                        if item.event == popped_event
                    ]
                    if len(matching_indexes) != 1:
                        raise RuntimeError(
                            "logical scheduler popped an unknown or duplicate completion"
                        )
                    pending_execution = pending_executions.pop(
                        matching_indexes[0]
                    )
                    if isinstance(pending_execution, LogicalPendingExecution):
                        scheduled = pending_execution.scheduled
                        request_flow = pending_execution.request_flow
                        worker_outcome = pending_execution.worker_outcome
                        logical_now = self._protocol_now(request)
                        normalized_fact = replace(
                            worker_outcome.fact,
                            started_at=(
                                scheduled.attempt.started_at
                                or scheduled.attempt.created_at
                            ),
                            ended_at=logical_now,
                            kill_progress_observed_at=(
                                logical_now
                                if worker_outcome.fact.kill_progress_observed_at
                                is not None
                                else None
                            ),
                        )
                        worker_outcome = replace(
                            worker_outcome,
                            fact=normalized_fact,
                        )
                        worker_execution_facts.append(normalized_fact.to_dict())
                        execution = self._execute_unit(
                            request=request,
                            plan=plan,
                            graph=graph,
                            scheduled=scheduled,
                            request_flow=request_flow,
                            worker_outcome=worker_outcome,
                            retry_counts=retry_counts,
                            runtime_observations=runtime_observations,
                            recovery_merge_snapshot=(
                                registration.root_unit.unit_id,
                                tuple(expand_result.child_units) if expand_result is not None else (),
                                merge_plan,
                                recovery_merge_once_keys,
                            ),
                        )
                        deferred = execution.get("deferred_recovery")
                        if deferred is not None:
                            if not isinstance(deferred, _DeferredRecovery):
                                raise TypeError("invalid deferred recovery context")
                            retry_event = logical_scheduler.schedule_event(
                                event_kind="retry",
                                logical_time_ms=logical_scheduler.clock_ms,
                                event_priority=EVENT_PRIORITY_BY_KIND["retry"],
                                task_id=scheduled.task_unit.task_id,
                                unit_id=scheduled.task_unit.unit_id,
                                attempt_ordinal=popped_event.attempt_ordinal,
                            )
                            pending_executions.append(
                                _LogicalPendingRetry(
                                    event=retry_event,
                                    recovery=deferred,
                                )
                            )
                            continue
                    elif isinstance(pending_execution, _LogicalPendingRetry):
                        scheduled = pending_execution.recovery.scheduled
                        applied = self._record_recovery(
                            request=request,
                            plan=plan,
                            graph=graph,
                            retry_counts=retry_counts,
                            deferred=pending_execution.recovery,
                        )
                        graph = applied.graph
                        if applied.decision.retry_allowed:
                            recovery_merge_directive = self._before_recovery_merge(
                                request=request,
                                applied=applied,
                                root_unit_id=registration.root_unit.unit_id,
                                child_units=(
                                    tuple(expand_result.child_units)
                                    if expand_result is not None
                                    else ()
                                ),
                                merge_plan=merge_plan,
                                once_keys=recovery_merge_once_keys,
                                runtime_observations=runtime_observations,
                            )
                            if (
                                isinstance(recovery_merge_directive, GateDirective)
                                and recovery_merge_directive.stop
                            ):
                                execution = _recovery_result(
                                    applied,
                                    requeue_blocked=True,
                                )
                            else:
                                requeue_event = logical_scheduler.schedule_event(
                                    event_kind="requeue",
                                    logical_time_ms=logical_scheduler.clock_ms,
                                    event_priority=EVENT_PRIORITY_BY_KIND["requeue"],
                                    task_id=scheduled.task_unit.task_id,
                                    unit_id=scheduled.task_unit.unit_id,
                                    attempt_ordinal=popped_event.attempt_ordinal,
                                )
                                pending_executions.append(
                                    _LogicalPendingRequeue(
                                        event=requeue_event,
                                        recovery=applied,
                                    )
                                )
                                continue
                        else:
                            execution = _recovery_result(
                                applied,
                                requeue_blocked=False,
                            )
                    elif isinstance(pending_execution, _LogicalPendingRequeue):
                        scheduled = pending_execution.recovery.scheduled
                        execution = self._finish_requeue(
                            request=request,
                            applied=pending_execution.recovery,
                            runtime_observations=runtime_observations,
                        )
                    elif isinstance(pending_execution, _LogicalPendingEarlyStop):
                        finalization = pending_execution.finalization
                        pruning = self._engine.record_subtree_pruning(
                            parent_unit_id=finalization.parent_unit_id,
                            parent_completed_event_seq=(
                                finalization.parent_completed_event_seq
                            ),
                            candidate_descendant_units=list(
                                finalization.candidate_descendant_units
                            ),
                            pruning_policy_ref=finalization.pruning_policy_ref,
                            now=self._protocol_now(request),
                            correlation_id=(
                                f"{request.run_id}:early_stop:"
                                f"{finalization.parent_unit_id}"
                            ),
                            causation_event_id=finalization.causation_event_id,
                        )
                        for cancelled_unit_id in pruning.cancelled_units:
                            cancelled_unit = replace(
                                graph.units[cancelled_unit_id],
                                state=TaskState.CANCELLED,
                                updated_at=self._protocol_now(request),
                            )
                            graph = _replace_graph_unit(graph, cancelled_unit)
                        self._settle(
                            request=request,
                            plan=plan,
                            root_unit_id=finalization.parent_unit_id,
                            completion_event_seq=(
                                finalization.settlement_completion_event_seq
                            ),
                            contributions=list(
                                finalization.settlement_contributions
                            ),
                        )
                        break
                    else:
                        raise TypeError("unsupported logical pending event")
                unit_id = scheduled.task_unit.unit_id
                trace_delivery_attempt = execution.get("trace_delivery_attempt")
                if trace_delivery_attempt is not None:
                    if not isinstance(trace_delivery_attempt, TraceDeliveryAttempt):
                        raise TypeError("invalid trace delivery attempt record")
                    trace_delivery_attempts.append(trace_delivery_attempt)
                graph = execution["graph"]
                infrastructure_failure = execution.get("infrastructure_failure")
                if infrastructure_failure is not None:
                    terminal_infrastructure_failure = infrastructure_failure
                    break
                if execution["canonical"] is None:
                    if execution.get("requeue_blocked"):
                        break
                    if execution["failed"]:
                        if unit_id != registration.root_unit.unit_id:
                            terminal_child_failure = (
                                graph.units[unit_id],
                                execution["failure_event"],
                            )
                            if not request.continue_after_terminal_child_failure:
                                graph = self._record_parent_failure(
                                    request=request,
                                    graph=graph,
                                    terminal_child_failure=terminal_child_failure,
                                )
                                break
                            # 失败事实已由 engine 原子落账；可继续观测独立 sibling。
                            if execution.get("halt_run"):
                                graph = self._record_parent_failure(
                                    request=request,
                                    graph=graph,
                                    terminal_child_failure=terminal_child_failure,
                                )
                                break
                            continue
                        break
                    continue
                canonical = execution["canonical"]
                canonical_unit = replace(
                    graph.units[unit_id],
                    canonical_output_refs=dict(
                        canonical.canonical_selection.canonical_output_refs
                    ),
                )
                graph = _replace_graph_unit(
                    graph,
                    canonical_unit,
                    canonical_refs=canonical.canonical_selection.canonical_output_refs,
                )
                if (
                    expand_result is not None
                    and merge_creation is None
                    and witness_observed_at is None
                    and canonical_unit.unit_type != "merge"
                    and _plugin_owns_merge_readiness(request)
                ):
                    readiness_after_canonical, _, _, _ = (
                        _current_merge_readiness(
                            request=request,
                            graph=graph,
                            event_ledger=self._event_ledger,
                            root_unit_id=registration.root_unit.unit_id,
                            child_units=tuple(expand_result.child_units),
                            merge_plan=merge_plan,
                        )
                    )
                    if readiness_after_canonical.status == "ready":
                        witness_observed_at = self._observation_now(request)
                        in_flight_ai_unit_ids_at_witness = (
                            _observed_in_flight_ai_unit_ids(
                                request=request,
                                graph=graph,
                                worker_execution_facts=worker_execution_facts,
                                observed_at=witness_observed_at,
                            )
                        )
                if canonical_unit.unit_type == "merge":
                    if merge_action is None or merge_creation is None or expand_result is None:
                        raise RuntimeError("merge runtime context is incomplete")
                    resolved = merge_action.resolution_builder(
                        MergeExecutionContext(
                            parent=graph.units[registration.root_unit.unit_id],
                            canonical_children=merge_children,
                            merge_unit=canonical_unit,
                            merge_task_link=merge_creation.merge_task_link,
                            merge_plan=merge_plan,
                            expected_output_refs=tuple(expand_result.expected_output_refs),
                            canonical_selection=canonical.canonical_selection,
                            canonical_event=canonical.event,
                        )
                    )
                    merge_resolution = self._engine.record_merge_resolution(
                        merge_record=resolved.merge_record,
                        expected_output_resolutions=list(
                            resolved.expected_output_resolutions
                        ),
                        correlation_id=f"{request.run_id}:merge_resolution",
                        causation_event_id=canonical.event.event_id,
                    )
                    merge_resolution_batch = _batch(merge_resolution.events)
                    contributions = ContributionCoordinator(
                        event_ledger=self._event_ledger
                    ).record_canonical_contributions(
                        task_id=graph.task_id,
                        completion_batches=completion_batches,
                        expansion_batches=[expansion_batch],
                        merge_resolution_batches=[merge_resolution_batch],
                        now=self._protocol_now(request),
                        correlation_id=f"{request.run_id}:contributions",
                    )
                    expand_contributions = [
                        item.contribution
                        for item in contributions
                        if item.contribution.kind == "expand_canonical"
                    ]
                    parent_completion = self._engine.record_parent_completion(
                        owner_unit=graph.units[registration.root_unit.unit_id],
                        expected_output_refs=list(expand_result.expected_output_refs),
                        expected_output_resolutions=list(
                            resolved.expected_output_resolutions
                        ),
                        expand_contributions=expand_contributions,
                        now=self._protocol_now(request),
                        correlation_id=f"{request.run_id}:parent_completion",
                        causation_event_id=merge_resolution.events[-1].event_id,
                    )
                    graph = _replace_graph_unit(graph, parent_completion.task_unit)
                    settlement_contributions = [
                        item.contribution
                        for item in contributions
                        if item.contribution.kind != "expand_canonical"
                    ] + list(parent_completion.expand_contributions)
                    parent_completion_event_seq = _completion_event_seq(
                        parent_completion.events,
                        registration.root_unit.unit_id,
                    )
                    early_stop_candidates = _unscheduled_descendants(
                        graph=graph,
                        parent_unit_id=registration.root_unit.unit_id,
                        pending_events=pending_executions,
                    )
                    if logical_scheduler is not None and early_stop_candidates:
                        early_stop_event = logical_scheduler.schedule_event(
                            event_kind="early_stop",
                            logical_time_ms=logical_scheduler.clock_ms,
                            event_priority=EVENT_PRIORITY_BY_KIND["early_stop"],
                            task_id=graph.task_id,
                            unit_id=registration.root_unit.unit_id,
                            attempt_ordinal=0,
                        )
                        pending_executions.append(
                            _LogicalPendingEarlyStop(
                                event=early_stop_event,
                                finalization=_EarlyStopFinalization(
                                    parent_unit_id=(
                                        registration.root_unit.unit_id
                                    ),
                                    parent_completed_event_seq=(
                                        parent_completion_event_seq
                                    ),
                                    candidate_descendant_units=(
                                        early_stop_candidates
                                    ),
                                    pruning_policy_ref=_pruning_policy_ref(
                                        merge_plan=merge_plan,
                                        expansion_events=expand_result.events,
                                    ),
                                    causation_event_id=(
                                        parent_completion.events[-1].event_id
                                    ),
                                    settlement_completion_event_seq=(
                                        parent_completion_event_seq
                                    ),
                                    settlement_contributions=tuple(
                                        settlement_contributions
                                    ),
                                ),
                            )
                        )
                        continue
                    self._settle(
                        request=request,
                        plan=plan,
                        root_unit_id=registration.root_unit.unit_id,
                        completion_event_seq=parent_completion_event_seq,
                        contributions=settlement_contributions,
                    )
                    break

                action = plan.canonical_action_builder(
                    CanonicalUnitContext(
                        unit=canonical_unit,
                        canonical_selection=canonical.canonical_selection,
                    )
                )
                if (
                    isinstance(action, CompleteAction)
                    and not request.mechanism_policy.verification_enabled
                ):
                    action = _bind_no_verification_completion(action)
                split = self._engine.record_split_strategy_invocation(
                    invocation=action.invocation,
                    correlation_id=f"{request.run_id}:split:{unit_id}",
                    causation_event_id=canonical.event.event_id,
                )
                if isinstance(action, CompleteAction):
                    complete = self._engine.record_complete_decision(
                        decision=action.decision,
                        task_unit=canonical_unit,
                        correlation_id=f"{request.run_id}:complete:{unit_id}",
                        causation_event_id=split.event.event_id,
                    )
                    graph = _replace_graph_unit(graph, complete.task_unit)
                    completion_batches.append(_batch(complete.events))
                    if unit_id == registration.root_unit.unit_id:
                        contributions = ContributionCoordinator(
                            event_ledger=self._event_ledger
                        ).record_canonical_contributions(
                            task_id=graph.task_id,
                            completion_batches=[completion_batches[-1]],
                            expansion_batches=[],
                            merge_resolution_batches=[],
                            now=self._protocol_now(request),
                            correlation_id=f"{request.run_id}:contributions",
                        )
                        self._settle(
                            request=request,
                            plan=plan,
                            root_unit_id=unit_id,
                            completion_event_seq=_completion_event_seq(
                                complete.events,
                                unit_id,
                            ),
                            contributions=[item.contribution for item in contributions],
                        )
                        break
                elif isinstance(action, ExpandAction):
                    expand_result = self._engine.record_expand_decision(
                        decision=action.decision,
                        proposal=action.proposal,
                        merge_plan=action.merge_plan,
                        parent_unit=canonical_unit,
                        graph=graph,
                        correlation_id=f"{request.run_id}:expand:{unit_id}",
                        causation_event_id=split.event.event_id,
                    )
                    graph = expand_result.task_graph
                    merge_plan = action.merge_plan
                    expansion_batch = _batch(expand_result.events)
                    _validate_selected_scope_units(
                        request=request,
                        child_units=tuple(expand_result.child_units),
                    )
                else:
                    raise TypeError("canonical action must be CompleteAction or ExpandAction")
                continue

            if (
                expand_result is not None
                and request.execution_scope.mode == "selected_ai_units"
                and _selected_scope_finished(
                    request=request,
                    graph=graph,
                    child_units=tuple(expand_result.child_units),
                )
            ):
                partial_observation = True
                break
            if expand_result is not None and merge_creation is None:
                readiness, children, canonical_events, verification_events = (
                    _current_merge_readiness(
                        request=request,
                        graph=graph,
                        event_ledger=self._event_ledger,
                        root_unit_id=registration.root_unit.unit_id,
                        child_units=tuple(expand_result.child_units),
                        merge_plan=merge_plan,
                    )
                )
                if readiness.status == "ready" and witness_observed_at is None:
                    witness_observed_at = self._observation_now(request)
                    in_flight_ai_unit_ids_at_witness = (
                        _observed_in_flight_ai_unit_ids(
                            request=request,
                            graph=graph,
                            worker_execution_facts=worker_execution_facts,
                            observed_at=witness_observed_at,
                        )
                    )
                gate_satisfied = readiness.status == "ready"
                merge_directive = _observe(
                    request.hooks.before_merge,
                    MergeContext(
                        parent=graph.units[registration.root_unit.unit_id],
                        canonical_children=tuple(
                            child
                            for child in children
                            if child.state.value == "Completed"
                        ),
                        required_child_unit_ids=tuple(
                            child.unit_id for child in children
                        ),
                        gate_satisfied=gate_satisfied,
                        protocol_event_refs=tuple(
                            _event_ref(event) for event in canonical_events
                        ),
                    ),
                )
                _collect_observations(runtime_observations, merge_directive)
                merge_gate_open = gate_satisfied or (
                    not request.mechanism_policy.merge_gate_enabled
                    and merge_directive is not None
                    and merge_directive.bypass
                )
                if merge_gate_open:
                    if not gate_satisfied:
                        # NO_MERGE_GATE 必须真实调用插件 merge，并把失败结果持久化；
                        # 但不伪造 required slot，也不进入 core merge task 创建路径。
                        completed_children = tuple(
                            child
                            for child in children
                            if child.state.value == "Completed"
                        )
                        plugin_result_type = None
                        plugin_error = None
                        try:
                            plugin_result = request.plugin_runtime.build_merge(
                                parent=graph.units[registration.root_unit.unit_id],
                                canonical_children=completed_children,
                                slot_integrity_enabled=True,
                            )
                            plugin_result_type = type(plugin_result).__name__
                        except Exception as exc:  # 实验结果必须记录插件的实际拒绝。
                            plugin_error = f"{type(exc).__name__}: {exc}"
                        completed_ids = {
                            child.unit_id for child in completed_children
                        }
                        attempt_body = {
                            "schema_version": (
                                "tokenshare.premature_merge_attempt.v1"
                            ),
                            "run_id": request.run_id,
                            "task_id": graph.task_id,
                            "parent_unit_id": registration.root_unit.unit_id,
                            "required_child_unit_ids": list(
                                readiness.required_child_unit_ids
                            ),
                            "canonical_child_unit_ids": [
                                child.unit_id for child in completed_children
                            ],
                            "missing_child_unit_ids": [
                                unit_id
                                for unit_id in readiness.required_child_unit_ids
                                if unit_id not in completed_ids
                            ],
                            "attempt_status": "executed",
                            "plugin_result_type": plugin_result_type,
                            "plugin_error": plugin_error,
                            "root_check_passed": False,
                            "failure_kind": "merge_readiness_unsatisfied",
                            "protocol_event_refs": [
                                _event_ref(event) for event in canonical_events
                            ],
                        }
                        result_ref = self._artifact_store.save_json(
                            attempt_body,
                            artifact_id=(
                                f"premature_merge_{run_key}_"
                                f"{len(runtime_observations)}"
                            ),
                            artifact_type="experiment_evidence",
                            artifact_schema_id=(
                                "tokenshare.premature_merge_attempt"
                            ),
                            artifact_schema_version="v1",
                            source={
                                "kind": "experiment_ablation",
                                "run_id": request.run_id,
                            },
                            metadata={
                                "ablation_mode": "NO_MERGE_GATE",
                                "root_check_passed": False,
                            },
                            created_at=self._protocol_now(request),
                        )
                        runtime_observations.append(
                            build_experiment_premature_merge_attempted_observation(
                                attempt_schema_version=attempt_body["schema_version"],
                                run_id=request.run_id,
                                task_id=graph.task_id,
                                parent_unit_id=registration.root_unit.unit_id,
                                required_child_unit_ids=tuple(
                                    attempt_body["required_child_unit_ids"]
                                ),
                                canonical_child_unit_ids=tuple(
                                    attempt_body["canonical_child_unit_ids"]
                                ),
                                missing_child_unit_ids=tuple(
                                    attempt_body["missing_child_unit_ids"]
                                ),
                                attempt_status=attempt_body["attempt_status"],
                                plugin_result_type=plugin_result_type,
                                plugin_error=plugin_error,
                                root_check_passed=False,
                                failure_kind=attempt_body["failure_kind"],
                                protocol_event_refs=tuple(
                                    attempt_body["protocol_event_refs"]
                                ),
                                result_artifact_ref=result_ref,
                            )
                        )
                        break
                    selected_child_unit_ids = set(
                        readiness.selected_child_unit_ids
                    )
                    merge_children = tuple(
                        child
                        for child in children
                        if child.unit_id in selected_child_unit_ids
                    )
                    merge_action = request.plugin_runtime.build_merge(
                        parent=graph.units[registration.root_unit.unit_id],
                        canonical_children=merge_children,
                        slot_integrity_enabled=(
                            request.mechanism_policy.slot_integrity_enabled
                        ),
                    )
                    merge_results = MergeCoordinator(
                        event_ledger=self._event_ledger,
                        artifact_store=self._artifact_store,
                        protocol_config=plan.registration_request.protocol_config,
                    ).create_ready_merge_tasks(
                        task_id=graph.task_id,
                        graph=graph,
                        merge_plan_events=[
                            event
                            for event in expand_result.events
                            if event.event_type == EventType.MERGE_PLAN_RECORDED
                        ],
                        expansion_batches=[expansion_batch],
                        canonical_events=canonical_events,
                        now=self._protocol_now(request),
                        coordinator_id="protocol_run_coordinator",
                        correlation_id=f"{request.run_id}:merge_create",
                        readiness_decision=readiness.to_dict(),
                    )
                    if len(merge_results) != 1:
                        raise RuntimeError("expected exactly one merge task")
                    merge_creation = merge_results[0]
                    graph = _replace_graph_unit(
                        graph,
                        merge_creation.merge_task_unit,
                        add=True,
                    )
                    continue
                if readiness.status == "failed":
                    if terminal_child_failure is None:
                        raise RuntimeError(
                            "failed merge readiness requires terminal child evidence"
                        )
                    graph = self._record_parent_failure(
                        request=request,
                        graph=graph,
                        terminal_child_failure=terminal_child_failure,
                    )
                    break
            if terminal_child_failure is not None and merge_creation is not None:
                graph = self._record_parent_failure(
                    request=request,
                    graph=graph,
                    terminal_child_failure=terminal_child_failure,
                )
                break
            root_state = graph.units[registration.root_unit.unit_id].state
            if root_state not in {TaskState.COMPLETED, TaskState.FAILED}:
                raise RuntimeError(
                    "protocol run stalled before the root reached a terminal state"
                )
            break

        runtime_ended_at = self._observation_now(request)
        planned_by_unit_id = _planned_ai_units_by_protocol_unit(
            request=request,
            graph=graph,
        )
        dispatched_ai_unit_ids = _ordered_unique(
            planned_by_unit_id[unit_id]
            for fact in worker_execution_facts
            if isinstance((unit_id := fact.get("unit_id")), str)
            and unit_id in planned_by_unit_id
        )
        completed_ai_unit_ids = tuple(
            planned_ai_unit_id
            for unit_id, planned_ai_unit_id in planned_by_unit_id.items()
            if graph.units[unit_id].state == TaskState.COMPLETED
            and planned_ai_unit_id in set(dispatched_ai_unit_ids)
        )
        runtime_observation = build_runtime_observation(
            run_id=request.run_id,
            runtime_started_at=runtime_started_at,
            runtime_ended_at=runtime_ended_at,
            planned_ai_unit_ids=tuple(planned_by_unit_id.values()),
            dispatched_ai_unit_ids=dispatched_ai_unit_ids,
            completed_ai_unit_ids=completed_ai_unit_ids,
            worker_execution_facts=worker_execution_facts,
            in_flight_ai_unit_ids_at_witness=in_flight_ai_unit_ids_at_witness,
            witness_observed_at=witness_observed_at,
        )
        projected = project_protocol_run(
            run_id=request.run_id,
            task_id=registration.task_spec.task_id,
            root_unit_id=registration.root_unit.unit_id,
            event_ledger=self._event_ledger,
            artifact_store=self._artifact_store,
            runtime_observation=runtime_observation,
        )
        if trace_delivery_attempts:
            projected = replace(
                projected,
                summary={
                    **projected.summary,
                    "trace_delivery_attempts": [
                        record.to_dict() for record in trace_delivery_attempts
                    ],
                },
            )
        if partial_observation:
            projected = replace(
                projected,
                status="partial",
                summary={
                    **projected.summary,
                    "execution_scope": request.execution_scope.mode,
                    "selected_ai_unit_ids": list(
                        request.execution_scope.selected_ai_unit_ids
                    ),
                    "partial_observation": True,
                },
            )
        if terminal_infrastructure_failure is not None:
            projected = replace(
                projected,
                status="failed",
                summary={
                    **projected.summary,
                    "terminal_failure": terminal_infrastructure_failure,
                },
            )
        if not runtime_observations:
            return projected
        return replace(
            projected,
            summary={
                **projected.summary,
                "runtime_hook_observations": [
                    record.to_dict() for record in runtime_observations
                ],
            },
        )
    def _record_parent_failure(
        self,
        *,
        request,
        graph,
        terminal_child_failure,
    ):
        failed_child, child_failure_event = terminal_child_failure
        parent_unit_id = failed_child.parent_unit_id
        if parent_unit_id is None:
            raise ValueError("terminal child failure requires a parent unit")
        failure = self._engine.record_parent_failure(
            parent_unit=graph.units[parent_unit_id],
            failed_child=failed_child,
            graph=graph,
            child_failure_event=child_failure_event,
            now=self._protocol_now(request),
            correlation_id=f"{request.run_id}:parent_failure:{parent_unit_id}",
        )
        return _replace_graph_unit(graph, failure.task_unit)

    def _prepare_unit_execution(self, *, request, scheduled) -> tuple[object, object]:
        execution_request = request.plugin_runtime.build_execution_request(
            scheduled.task_unit,
            attempt=scheduled.attempt,
            lease=scheduled.lease,
        )
        execution_request = _bind_execution_request(
            execution_request,
            attempt_ordinal=scheduled.attempt.attempt_ordinal,
            logical_dispatch_start_ms=(
                self._logical_scheduler.clock_ms
                if self._logical_scheduler is not None
                else None
            ),
        )
        request_flow = self._engine.record_execution_request(
            request=execution_request,
            correlation_id=f"{request.run_id}:request:{scheduled.attempt.attempt_id}",
            causation_event_id=scheduled.events[-1].event_id,
        )
        return execution_request, request_flow

    def _execute_unit(
        self,
        *,
        request,
        plan,
        graph,
        scheduled,
        request_flow,
        worker_outcome: WorkerBatchOutcome,
        retry_counts,
        runtime_observations,
        recovery_merge_snapshot,
    ) -> dict[str, object]:
        unit_id = scheduled.task_unit.unit_id
        prepared_delivery = worker_outcome.prepared_delivery
        trace_terminal_status = _trace_delivery_terminal_status(worker_outcome)
        if prepared_delivery is not None and trace_terminal_status is None:
            consumption = commit_worker_outcome(
                outcome=worker_outcome,
                killed_worker_ids=(),
                active_lease=scheduled.lease,
                current_fencing_token=scheduled.lease.fencing_token,
                current_stores=ParentCommitStores(
                    artifact_store=self._artifact_store,
                    event_ledger=self._event_ledger,
                    trace_delivery_stager=self._trace_delivery_stager,
                ),
            )
            worker_outcome = replace(
                worker_outcome,
                submission=_trace_consumption_submission(
                    request=request_flow.request,
                    delivery=prepared_delivery,
                    consumption=consumption,
                    submitted_at=self._protocol_now(request),
                    artifact_store=self._artifact_store,
                ),
            )
        if worker_outcome.submission is None:
            worker_terminated = worker_outcome.failure_kind == "worker_terminated"
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="lease_expired" if worker_terminated else "executor_error",
                causation_event_id=request_flow.event.event_id,
                recovery_now=(
                    self._protocol_now(request)
                    if worker_terminated and request.logical_scheduler is not None
                    else scheduled.lease.expires_at
                    if worker_terminated
                    else None
                ),
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
                trace_delivery_outcome=(
                    worker_outcome if trace_terminal_status is not None else None
                ),
                trace_delivery_status=trace_terminal_status,
            )
        submission = worker_outcome.submission
        if request.logical_scheduler is not None:
            submission = replace(
                submission,
                submitted_at=self._protocol_now(request),
            )
        if submission.result_kind == "no_return":
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="lease_expired",
                causation_event_id=request_flow.event.event_id,
                recovery_now=(
                    self._protocol_now(request)
                    if request.logical_scheduler is not None
                    else scheduled.lease.expires_at
                ),
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
            )
        if submission.result_kind == "late_submission":
            submission = replace(
                submission,
                submitted_at=_after_deadline(scheduled.lease.expires_at),
            )
        parser_directive = _observe(
            request.hooks.before_parser,
            ParserContext(
                execution_request=request_flow.request,
                submission=submission,
                unit=scheduled.task_unit,
                attempt=scheduled.attempt,
                lease=scheduled.lease,
            ),
        )
        _collect_observations(runtime_observations, parser_directive)
        submission = _replacement_submission(submission, parser_directive)
        if (
            not request.mechanism_policy.parser_policy_enabled
            and submission.raw_output_ref is not None
        ):
            submission = replace(
                submission,
                result_kind="succeeded",
                parsed_output_ref=submission.raw_output_ref,
                candidate_output_refs={
                    output_name: submission.raw_output_ref
                    for output_name in request_flow.request.output_contract.required_outputs
                },
                parse_failure_ref=None,
                error=None,
            )
        if parser_directive is not None and parser_directive.stop:
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="parser_failure",
                causation_event_id=request_flow.event.event_id,
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
            )
        if (
            submission.parsed_output_ref is not None
            and submission.candidate_output_refs
        ):
            parsed_candidate_directive = _observe(
                request.hooks.after_parsed_candidate_persisted,
                ParsedCandidateContext(
                    run_id=request.run_id,
                    task_id=submission.task_id,
                    unit_id=submission.unit_id,
                    attempt_id=submission.attempt_id,
                    lease_id=submission.lease_id,
                    worker_id=scheduled.attempt.client_id,
                    raw_output_ref=submission.raw_output_ref,
                    original_parsed_output_ref=submission.parsed_output_ref,
                    candidate_output_refs=dict(submission.candidate_output_refs),
                    submitted_at=submission.submitted_at,
                    experiment_unit_id=_optional_planned_ai_unit_id(
                        request.plugin_runtime,
                        scheduled.task_unit,
                    ),
                ),
            )
            _collect_observations(
                runtime_observations,
                parsed_candidate_directive,
            )
            submission = _replace_candidate_output_refs(
                submission,
                parsed_candidate_directive,
            )
        submission_flow = self._engine.record_execution_submission(
            submission=submission,
            attempt=scheduled.attempt,
            lease=scheduled.lease,
            correlation_id=f"{request.run_id}:submission:{scheduled.attempt.attempt_id}",
            causation_event_id=request_flow.event.event_id,
        )
        if submission_flow.acceptance_decision.acceptance_status != ACCEPTED:
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="lease_expired"
                if submission_flow.acceptance_decision.rejection_reason
                == "lease_deadline_exceeded"
                else "executor_error",
                causation_event_id=submission_flow.event.event_id,
                recovery_now=(
                    submission.submitted_at
                    if submission_flow.acceptance_decision.rejection_reason
                    == "lease_deadline_exceeded"
                    else None
                ),
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
            )
        if submission.result_kind != "succeeded":
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="executor_error",
                causation_event_id=submission_flow.event.event_id,
                attempt_for_recovery=submission_flow.attempt,
                halt_run=submission.result_kind == "fatal_executor_error",
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
            )
        verification_directive = _observe(
            request.hooks.before_verification,
            VerificationContext(
                execution_request=request_flow.request,
                submission=submission,
                unit=scheduled.task_unit,
                attempt=submission_flow.attempt,
                lease=scheduled.lease,
            ),
        )
        _collect_observations(runtime_observations, verification_directive)
        submission = _replacement_submission(submission, verification_directive)
        if request.mechanism_policy.verification_enabled:
            try:
                report = request.plugin_runtime.verify_submission(
                    submission,
                    unit=scheduled.task_unit,
                )
            except (KeyError, TypeError, ValueError):
                if request.mechanism_policy.parser_policy_enabled:
                    raise
                return self._recover(
                    request=request,
                    plan=plan,
                    graph=graph,
                    scheduled=scheduled,
                    retry_counts=retry_counts,
                    trigger="parser_failure",
                    causation_event_id=submission_flow.event.event_id,
                    attempt_for_recovery=submission_flow.attempt,
                    runtime_observations=runtime_observations,
                    recovery_merge_snapshot=recovery_merge_snapshot,
                )
        else:
            report = _build_no_verification_report(
                execution_request=request_flow.request,
                submission=submission,
                submitted_attempt=submission_flow.attempt,
                now=self._protocol_now(request),
            )
        report = replace(report, submission_event_seq=submission_flow.event.event_seq)
        verification = self._engine.record_verification(
            report=report,
            attempt=submission_flow.attempt,
            correlation_id=f"{request.run_id}:verification:{scheduled.attempt.attempt_id}",
            causation_event_id=submission_flow.event.event_id,
        )
        infrastructure_failure = _checker_infrastructure_failure(report)
        if infrastructure_failure is not None:
            return {
                "graph": graph,
                "canonical": None,
                "failed": False,
                "infrastructure_failure": infrastructure_failure,
            }
        if report.status not in {"passed", "accepted"}:
            recovery_attempt = verification.attempt or submission_flow.attempt
            recovery_causation_event_id = (
                verification.attempt_event.event_id
                if verification.attempt_event is not None
                else verification.event.event_id
            )
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="verification_rejected",
                causation_event_id=recovery_causation_event_id,
                attempt_for_recovery=recovery_attempt,
                runtime_observations=runtime_observations,
                recovery_merge_snapshot=recovery_merge_snapshot,
            )
        canonical = self._engine.bind_canonical_outputs(
            task_id=scheduled.task_unit.task_id,
            unit_id=unit_id,
            verification_events=[verification.event],
            attempts_by_id={verification.attempt.attempt_id: verification.attempt},
            policy=plan.registration_request.protocol_config.canonical_output_policy,
            now=self._protocol_now(request),
            correlation_id=f"{request.run_id}:canonical:{scheduled.attempt.attempt_id}",
        )
        return {"graph": graph, "canonical": canonical, "failed": False}

    def _recover(
        self,
        *,
        request,
        plan,
        graph,
        scheduled,
        retry_counts,
        trigger,
        causation_event_id,
        attempt_for_recovery=None,
        recovery_now=None,
        halt_run=False,
        runtime_observations=None,
        trace_delivery_outcome=None,
        trace_delivery_status=None,
        recovery_merge_snapshot=None,
    ) -> dict[str, object]:
        deferred = _DeferredRecovery(
            scheduled=scheduled,
            trigger=trigger,
            causation_event_id=causation_event_id,
            attempt_for_recovery=attempt_for_recovery,
            recovery_now=recovery_now,
            halt_run=bool(halt_run),
            trace_delivery_outcome=trace_delivery_outcome,
            trace_delivery_status=trace_delivery_status,
        )
        if request.logical_scheduler is not None:
            return {
                "graph": graph,
                "canonical": None,
                "failed": False,
                "requeue_blocked": False,
                "deferred_recovery": deferred,
            }
        applied = self._record_recovery(
            request=request,
            plan=plan,
            graph=graph,
            retry_counts=retry_counts,
            deferred=deferred,
        )
        recovery_merge_directive = None
        if applied.decision.retry_allowed and recovery_merge_snapshot is not None:
            (
                root_unit_id,
                child_units,
                merge_plan,
                once_keys,
            ) = recovery_merge_snapshot
            recovery_merge_directive = self._before_recovery_merge(
                request=request,
                applied=applied,
                root_unit_id=root_unit_id,
                child_units=child_units,
                merge_plan=merge_plan,
                once_keys=once_keys,
                runtime_observations=runtime_observations,
            )
        if (
            isinstance(recovery_merge_directive, GateDirective)
            and recovery_merge_directive.stop
        ):
            return _recovery_result(applied, requeue_blocked=True)
        return self._finish_requeue(
            request=request,
            applied=applied,
            runtime_observations=runtime_observations,
        )

    def _record_recovery(
        self,
        *,
        request,
        plan,
        graph,
        retry_counts,
        deferred: _DeferredRecovery,
    ) -> _AppliedRecovery:
        scheduled = deferred.scheduled
        trigger = deferred.trigger
        unit_id = scheduled.task_unit.unit_id
        retry_count = retry_counts.get(unit_id, 0) + 1
        retry_counts[unit_id] = retry_count
        decision = evaluate_retry(
            trigger=trigger,
            retry_count=retry_count,
            max_retries=plan.registration_request.protocol_config.max_retries,
        )
        recovery_action_id = f"{request.run_id}:recovery:{unit_id}:{retry_count}"
        correlation_id = f"{request.run_id}:recovery:{unit_id}:{retry_count}"
        if trigger == "lease_expired":
            recovery = self._engine.record_lease_expiry(
                lease=scheduled.lease,
                attempt=deferred.attempt_for_recovery or scheduled.attempt,
                task_unit=scheduled.task_unit,
                now=deferred.recovery_now or self._protocol_now(request),
                correlation_id=correlation_id,
                recovery_action_id=recovery_action_id,
                retry_count=retry_count,
                causation_event_id=deferred.causation_event_id,
            )
        else:
            recovery = self._engine.record_recovery_decision(
                decision=decision,
                attempt=deferred.attempt_for_recovery or scheduled.attempt,
                lease=scheduled.lease,
                task_unit=scheduled.task_unit,
                recovery_action_id=recovery_action_id,
                now=deferred.recovery_now or self._protocol_now(request),
                correlation_id=correlation_id,
                causation_event_id=deferred.causation_event_id,
            )
        graph = _replace_graph_unit(graph, recovery.task_unit)
        recovery_event_refs = tuple(
            {
                "event_id": event.event_id,
                "event_seq": event.event_seq,
                "event_type": (
                    event.event_type.value
                    if isinstance(event.event_type, EventType)
                    else str(event.event_type)
                ),
            }
            for event in recovery.events
        )
        trace_delivery_attempt = None
        if deferred.trace_delivery_outcome is not None:
            attempt_event = next(
                (
                    event
                    for event in recovery.events
                    if event.event_type == EventType.ATTEMPT_STATE_CHANGED
                    and event.object_id == scheduled.attempt.attempt_id
                ),
                None,
            )
            if attempt_event is None:
                raise RuntimeError(
                    "terminal trace delivery recovery produced no attempt state event"
                )
            trace_delivery_attempt = build_trace_delivery_attempt(
                outcome=deferred.trace_delivery_outcome,
                status=deferred.trace_delivery_status,
                event=attempt_event,
            )
        return _AppliedRecovery(
            scheduled=scheduled,
            graph=graph,
            trigger=trigger,
            decision=decision,
            recovery=recovery,
            recovery_event_refs=recovery_event_refs,
            halt_run=deferred.halt_run,
            trace_delivery_attempt=trace_delivery_attempt,
        )

    def _before_recovery_merge(
        self,
        *,
        request,
        applied: _AppliedRecovery,
        root_unit_id: str,
        child_units,
        merge_plan,
        once_keys: set[tuple[str, str]],
        runtime_observations,
    ) -> GateDirective | None:
        """仅对显式 capability 暴露 recovery 后、replacement 前的真实快照。"""

        hook = getattr(request.hooks, "before_recovery_merge", None)
        if not callable(hook) or not child_units or merge_plan is None:
            return None
        recovered_attempt_id = str(applied.recovery.attempt.attempt_id)
        once_key = (root_unit_id, recovered_attempt_id)
        if once_key in once_keys:
            raise RuntimeError(
                "duplicate recovery-premerge observation for recovered attempt"
            )
        once_keys.add(once_key)
        readiness, children, canonical_events, verification_events = (
            _current_merge_readiness(
                request=request,
                graph=applied.graph,
                event_ledger=self._event_ledger,
                root_unit_id=root_unit_id,
                child_units=tuple(child_units),
                merge_plan=merge_plan,
            )
        )
        completed_children = tuple(
            child for child in children if child.state == TaskState.COMPLETED
        )
        decision = applied.decision
        directive = _observe(
            hook,
            RecoveryMergeContext(
                parent=applied.graph.units[root_unit_id],
                canonical_children=completed_children,
                required_child_unit_ids=tuple(
                    readiness.required_child_unit_ids
                ),
                gate_satisfied=readiness.status == "ready",
                recovered_attempt_id=recovered_attempt_id,
                recovery_trigger=applied.trigger,
                recovery_decision={
                    "trigger": decision.trigger,
                    "retry_allowed": decision.retry_allowed,
                    "next_task_state": decision.next_task_state.value,
                    "superseded_attempt_state": (
                        decision.superseded_attempt_state.value
                    ),
                    "retry_count": decision.retry_count,
                    "reason": decision.reason,
                },
                protocol_event_refs=tuple(
                    _event_ref(event)
                    for event in (*canonical_events, *verification_events)
                ),
                recovery_event_refs=applied.recovery_event_refs,
            ),
        )
        if runtime_observations is not None:
            _collect_observations(runtime_observations, directive)
        return directive

    def _finish_requeue(
        self,
        *,
        request,
        applied: _AppliedRecovery,
        runtime_observations,
    ) -> dict[str, object]:
        decision = applied.decision
        recovery = applied.recovery
        requeue_directive = _observe(
            request.hooks.before_requeue,
            RecoveryContext(
                unit=recovery.task_unit,
                attempt=recovery.attempt,
                lease=recovery.lease,
                trigger=applied.trigger,
                decision={
                    "trigger": decision.trigger,
                    "retry_allowed": decision.retry_allowed,
                    "next_task_state": decision.next_task_state.value,
                    "superseded_attempt_state": (
                        decision.superseded_attempt_state.value
                    ),
                    "retry_count": decision.retry_count,
                    "reason": decision.reason,
                },
                recovery_event_refs=applied.recovery_event_refs,
            ),
        )
        if runtime_observations is not None:
            _collect_observations(runtime_observations, requeue_directive)
        requeue_blocked = bool(
            decision.retry_allowed
            and (
                not request.mechanism_policy.replacement_attempts_allowed
                or (
                    isinstance(requeue_directive, GateDirective)
                    and requeue_directive.stop
                )
            )
        )
        return _recovery_result(applied, requeue_blocked=requeue_blocked)

    def _settle(
        self,
        *,
        request,
        plan,
        root_unit_id,
        completion_event_seq,
        contributions,
    ) -> None:
        root_budget = plan.registration_request.root_budget
        if root_budget is None:
            root_budget = 0
        self._engine.record_root_settlement(
            task_id=plan.registration_request.task_id,
            root_unit_id=root_unit_id,
            root_completion_event_seq=completion_event_seq,
            eligible_contributions=contributions,
            root_budget=int(root_budget),
            settlement_policy_id=plan.settlement_policy_id,
            now=self._protocol_now(request),
            correlation_id=f"{request.run_id}:settlement",
        )

    def _protocol_now(self, request: ProtocolRunRequest) -> str:
        scheduler = request.logical_scheduler
        return scheduler.now_timestamp() if scheduler is not None else self._now()

    def _observation_now(self, request: ProtocolRunRequest) -> str:
        scheduler = request.logical_scheduler
        return (
            scheduler.now_timestamp()
            if scheduler is not None
            else self._observation_clock()
        )


def _scoped_unit_ids(*, request, graph, unit_ids, expansion_started):
    candidates = tuple(unit_ids)
    if request.execution_scope.mode == "whole_root" or not expansion_started:
        return candidates
    selected = set(request.execution_scope.selected_ai_unit_ids)
    return tuple(
        unit_id
        for unit_id in candidates
        if request.plugin_runtime.planned_ai_unit_id(graph.units[unit_id])
        in selected
    )


def _validate_resume_request(
    request: ProtocolRunRequest,
    checkpoint_request: ProtocolRunRequest,
) -> None:
    if (
        request.run_id != checkpoint_request.run_id
        or request.root_input != checkpoint_request.root_input
        or request.trace_delay_policy != checkpoint_request.trace_delay_policy
        or request.mechanism_policy != checkpoint_request.mechanism_policy
        or request.continue_after_terminal_child_failure
        != checkpoint_request.continue_after_terminal_child_failure
        or request.execution_scope != checkpoint_request.execution_scope
        or request.plugin_runtime is not checkpoint_request.plugin_runtime
        or request.worker_backend is not checkpoint_request.worker_backend
        or request.hooks is not checkpoint_request.hooks
    ):
        raise ValueError("resume request does not match logical run checkpoint")


def _recovery_result(
    applied: _AppliedRecovery,
    *,
    requeue_blocked: bool,
) -> dict[str, object]:
    decision = applied.decision
    recovery = applied.recovery
    return {
        "graph": applied.graph,
        "canonical": None,
        "failed": not decision.retry_allowed,
        "halt_run": applied.halt_run,
        "requeue_blocked": requeue_blocked,
        "trace_delivery_attempt": applied.trace_delivery_attempt,
        "failure_event": (
            recovery.events[-1] if not decision.retry_allowed else None
        ),
    }


def _unscheduled_descendants(*, graph, parent_unit_id, pending_events):
    in_flight_unit_ids = {
        item.scheduled.task_unit.unit_id
        for item in pending_events
        if isinstance(item, LogicalPendingExecution)
    }
    return tuple(
        unit
        for unit in graph.units.values()
        if unit.unit_id != parent_unit_id
        and unit.unit_id not in in_flight_unit_ids
        and unit.state in {TaskState.READY, TaskState.BLOCKED}
    )


def _pruning_policy_ref(*, merge_plan, expansion_events) -> dict[str, object]:
    if merge_plan is None:
        raise RuntimeError("early stop requires a merge plan")
    merge_policy = merge_plan.merge_policy_ref
    merge_plan_event = next(
        (
            event
            for event in expansion_events
            if event.event_type == EventType.MERGE_PLAN_RECORDED
        ),
        None,
    )
    if merge_plan_event is None:
        raise RuntimeError("early stop requires merge plan event evidence")
    return {
        "pruning_policy_id": merge_policy["merge_policy_id"],
        "pruning_policy_version": merge_policy["merge_policy_version"],
        "pruning_policy_plugin_id": merge_policy["plugin_id"],
        "pruning_policy_plugin_version": merge_policy["plugin_version"],
        "pruning_policy_descriptor_digest": merge_policy[
            "merge_policy_descriptor_digest"
        ],
        "policy_source_type": "merge_plan",
        "policy_source_id": merge_plan.merge_plan_header["merge_plan_id"],
        "policy_source_event_seq": merge_plan_event.event_seq,
    }


def _match_worker_outcomes(*, prepared, worker_outcomes):
    """按 request identity 关联结果，禁止 backend 返回顺序影响 parent。"""

    prepared_by_key = {}
    for item in prepared:
        key = _execution_request_identity(item[0])
        if key in prepared_by_key:
            raise ValueError("prepared worker batch contains duplicate request identity")
        prepared_by_key[key] = item
    outcomes_by_key = {}
    for outcome in worker_outcomes:
        key = _execution_request_identity(outcome.request)
        if key in outcomes_by_key:
            raise ValueError("worker batch contains duplicate request identity")
        if key not in prepared_by_key:
            raise ValueError("worker batch returned an unknown request identity")
        outcomes_by_key[key] = outcome
    if set(outcomes_by_key) != set(prepared_by_key):
        raise ValueError("worker backend returned an incomplete request batch")
    return tuple(
        (item, outcomes_by_key[_execution_request_identity(item[0])])
        for item in prepared
    )


def _completion_event_kind(
    outcome: WorkerBatchOutcome,
    declared_kind: str,
) -> str:
    if outcome.failure_kind == "worker_terminated":
        return "worker_death"
    result_kind = getattr(outcome.submission, "result_kind", None)
    if result_kind in {"no_return", "late_submission"}:
        return "lease_deadline"
    return declared_kind


def _lifecycle_delay_ms(
    *,
    scheduled,
    worker_outcome: WorkerBatchOutcome,
    source_latency_ms: int,
) -> int:
    lease_ms = _elapsed_utc_ms(
        scheduled.lease.issued_at,
        scheduled.lease.expires_at,
    )
    result_kind = getattr(worker_outcome.submission, "result_kind", None)
    if result_kind == "no_return":
        return lease_ms
    if result_kind == "late_submission":
        return max(source_latency_ms, lease_ms + 1000)
    if worker_outcome.failure_kind == "worker_terminated":
        return max(source_latency_ms, lease_ms)
    return source_latency_ms


def _elapsed_utc_ms(start: str, end: str) -> int:
    start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
    elapsed_ms = int((end_value - start_value).total_seconds() * 1000)
    if elapsed_ms < 0:
        raise ValueError("lease deadline precedes lease issue time")
    return elapsed_ms


def _execution_request_identity(request) -> tuple[object, ...]:
    return (
        getattr(request, "request_id", None),
        getattr(request, "task_id", None),
        getattr(request, "unit_id", None),
        getattr(request, "attempt_id", None),
        getattr(request, "lease_id", None),
        getattr(request, "fencing_token", None),
    )


def _validate_selected_scope_units(*, request, child_units) -> None:
    if request.execution_scope.mode != "selected_ai_units":
        return
    available = {
        planned
        for unit in child_units
        if (
            planned := request.plugin_runtime.planned_ai_unit_id(unit)
        )
        is not None
    }
    missing = sorted(set(request.execution_scope.selected_ai_unit_ids) - available)
    if missing:
        raise ValueError(
            "selected_ai_unit_id is not present in plugin split plan: "
            + ", ".join(missing)
        )


def _selected_scope_finished(*, request, graph, child_units) -> bool:
    selected = set(request.execution_scope.selected_ai_unit_ids)
    selected_units = tuple(
        graph.units[unit.unit_id]
        for unit in child_units
        if request.plugin_runtime.planned_ai_unit_id(unit) in selected
    )
    return bool(selected_units) and all(
        unit.state in {TaskState.COMPLETED, TaskState.FAILED}
        for unit in selected_units
    )


def _current_merge_readiness(
    *,
    request,
    graph,
    event_ledger,
    root_unit_id,
    child_units,
    merge_plan,
):
    """从当前 graph/ledger 快照构造一次通用 plugin readiness 观察。"""

    children = tuple(graph.units[unit.unit_id] for unit in child_units)
    task_events = [
        event
        for event in event_ledger.read_all()
        if event.task_id == graph.task_id
    ]
    canonical_events = [
        event
        for event in task_events
        if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
    ]
    verification_events = [
        event
        for event in task_events
        if event.event_type == EventType.VERIFICATION_RECORDED
    ]
    readiness = _evaluate_merge_readiness(
        request=request,
        parent=graph.units[root_unit_id],
        children=children,
        merge_plan=merge_plan,
        canonical_events=canonical_events,
        verification_events=verification_events,
    )
    return readiness, children, canonical_events, verification_events


def _plugin_owns_merge_readiness(request: ProtocolRunRequest) -> bool:
    return callable(
        getattr(request.plugin_runtime, "evaluate_merge_readiness", None)
    )


def _evaluate_merge_readiness(
    *,
    request,
    parent,
    children,
    merge_plan,
    canonical_events,
    verification_events,
) -> MergeReadinessDecision:
    context = MergeReadinessContext(
        parent=parent,
        children=tuple(children),
        merge_plan=merge_plan,
        canonical_events=tuple(canonical_events),
        verification_events=tuple(verification_events),
    )
    evaluator = getattr(
        request.plugin_runtime,
        "evaluate_merge_readiness",
        None,
    )
    if callable(evaluator):
        decision = evaluator(context)
        if not isinstance(decision, MergeReadinessDecision):
            raise TypeError(
                "evaluate_merge_readiness must return MergeReadinessDecision"
            )
        return decision

    required = tuple(
        dict.fromkeys(
            str(slot["source_child_unit_id"])
            for slot in merge_plan.required_slots
        )
    )
    required_children = tuple(
        child for child in children if child.unit_id in set(required)
    )
    if all(child.state == TaskState.COMPLETED for child in required_children):
        return MergeReadinessDecision(
            status="ready",
            reason="all_required_slots_canonical",
            policy_id="tokenshare.runtime.all_required.v1",
            policy_version="v1",
            required_child_unit_ids=required,
            selected_child_unit_ids=required,
        )
    if any(child.state == TaskState.FAILED for child in required_children):
        return MergeReadinessDecision(
            status="failed",
            reason="terminal_child_failure",
            policy_id="tokenshare.runtime.all_required.v1",
            policy_version="v1",
            required_child_unit_ids=required,
        )
    return MergeReadinessDecision(
        status="wait",
        reason="required_children_not_terminal",
        policy_id="tokenshare.runtime.all_required.v1",
        policy_version="v1",
        required_child_unit_ids=required,
    )


def _replace_graph_unit(
    graph: TaskGraph,
    unit,
    *,
    canonical_refs=None,
    add: bool = False,
) -> TaskGraph:
    if not add and unit.unit_id not in graph.units:
        raise ValueError(f"unknown graph unit: {unit.unit_id}")
    canonical = dict(graph.canonical_outputs_by_unit_id)
    if canonical_refs:
        canonical[unit.unit_id] = dict(canonical_refs)
    return TaskGraph(
        task_id=graph.task_id,
        units={**graph.units, unit.unit_id: unit},
        relations=graph.relations,
        canonical_outputs_by_unit_id=canonical,
        protocol_config=graph.protocol_config,
    )


def _batch(events) -> BatchView:
    events = tuple(events)
    return BatchView(batch_id=events[0].batch_id or "", events=events)


def _completion_event_seq(events, unit_id: str) -> int:
    for event in events:
        if (
            event.event_type == EventType.TASK_UNIT_STATE_CHANGED
            and event.object_id == unit_id
            and event.payload.get("new_state") == "Completed"
        ):
            return event.event_seq
    raise ValueError(f"completion event missing for {unit_id}")


def _observe(hook, context):
    return hook(context)


def _collect_observations(
    observations: list[RuntimeHookObservationV1],
    directive,
) -> None:
    if directive is None:
        return
    for record in getattr(directive, "experiment_records", ()):
        if not isinstance(record, RuntimeHookObservationV1):
            raise TypeError(
                "runtime hook experiment records must be RuntimeHookObservationV1"
            )
        observations.append(record)


def _replacement_submission(submission, directive):
    if directive is None or getattr(directive, "replacement", None) is None:
        return submission
    replacement = directive.replacement
    if not isinstance(replacement, type(submission)):
        raise TypeError("runtime hook replacement must be an ExecutionSubmission")
    return replacement


def _replace_candidate_output_refs(submission, directive):
    if directive is None:
        return submission
    replacement_refs = directive.replacement_candidate_output_refs
    if not isinstance(replacement_refs, dict):
        raise TypeError("parsed-candidate replacement refs must be an object")
    if set(replacement_refs) != set(submission.candidate_output_refs):
        raise ValueError(
            "parsed-candidate replacement refs must preserve output contract names"
        )
    if any(
        not isinstance(name, str) or not isinstance(ref, type(submission.parsed_output_ref))
        for name, ref in replacement_refs.items()
    ):
        raise TypeError(
            "parsed-candidate replacement refs must contain ArtifactRef values"
        )
    return replace(
        submission,
        candidate_output_refs=dict(replacement_refs),
    )


def _optional_planned_ai_unit_id(plugin_runtime, unit) -> str | None:
    resolver = getattr(plugin_runtime, "planned_ai_unit_id", None)
    if not callable(resolver):
        return None
    planned_ai_unit_id = resolver(unit)
    if planned_ai_unit_id is None:
        return None
    if not isinstance(planned_ai_unit_id, str) or not planned_ai_unit_id:
        raise ValueError("planned_ai_unit_id must be a non-empty string")
    return planned_ai_unit_id


def _checker_infrastructure_failure(report) -> dict[str, object] | None:
    """只把 Lean checker 明确基础设施状态升级为 root infrastructure 终止。"""

    if report.status != "error":
        return None
    checker_status = None
    failure_summary = report.failure_summary
    if isinstance(failure_summary, dict):
        checker_status = failure_summary.get("checker_status")
    metadata = report.metadata
    plugin_layer = (
        metadata.get("plugin_domain_layer")
        if isinstance(metadata, dict)
        else None
    )
    details = (
        plugin_layer.get("details")
        if isinstance(plugin_layer, dict)
        else None
    )
    if isinstance(details, dict) and isinstance(details.get("checker_status"), str):
        checker_status = details["checker_status"]
    if checker_status not in {"environment_error", "timeout", "helper_error"}:
        return None
    return {
        "failure_stage": "candidate_verification",
        "failure_origin": "checker_environment_error",
        "infrastructure_invalid": True,
    }


def _build_no_verification_report(
    *,
    execution_request,
    submission,
    submitted_attempt,
    now: str,
):
    if submitted_attempt is None:
        raise ValueError("NO_VERIFICATION requires an accepted submission")
    plugin = execution_request.plugin
    return build_verification_report(
        verification_report_id=f"runtime_no_verification_{submission.attempt_id}",
        task_id=submission.task_id,
        unit_id=submission.unit_id,
        attempt_id=submission.attempt_id,
        submission_id=submission.submission_id,
        submission_event_seq=1,
        candidate_output_refs=dict(submission.candidate_output_refs),
        required_output_names=list(
            execution_request.output_contract.required_outputs
        ),
        output_contract_id=execution_request.output_contract.output_contract_id,
        validator_policy_id="runtime_ablation_no_verification.v1",
        plugin_id=str(plugin.get("plugin_id") or "unknown_plugin"),
        plugin_version=str(plugin.get("plugin_version") or "unknown_version"),
        plugin_descriptor_digest=str(
            plugin.get("plugin_descriptor_digest")
            or plugin.get("descriptor_digest")
            or "sha256:runtime_no_verification"
        ),
        status="passed",
        expected_artifact_hashes={
            name: ref.content_hash
            for name, ref in submission.candidate_output_refs.items()
        },
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={
            "runtime": "tokenshare.local_runtime",
            "ablation_mode": "NO_VERIFICATION",
            "domain_verification_layer": "bypassed_before_domain_verifier",
            "synthetic_verification": True,
        },
        verifier={
            "verifier_id": "runtime_ablation_no_verification",
            "verifier_version": "v1",
        },
        started_at=now,
        completed_at=now,
        metadata={
            "ablation_mode": "NO_VERIFICATION",
            "domain_verifier_invoked": False,
            "synthetic_verification": True,
            "bypass_reason": "verification_mechanism_disabled",
        },
    )


def _bind_no_verification_completion(action: CompleteAction) -> CompleteAction:
    """让complete evidence引用实际选中的synthetic verification policy。"""

    action_body = dict(action.decision.action_body)
    evidence = action_body.get("completion_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("complete decision requires structured completion_evidence")
    action_body["completion_evidence"] = {
        **evidence,
        "validator_policy_id": "runtime_ablation_no_verification.v1",
    }
    return replace(
        action,
        decision=replace(action.decision, action_body=action_body),
    )


def _after_deadline(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")


def _event_ref(event) -> dict[str, object]:
    event_type = event.event_type
    return {
        "event_id": event.event_id,
        "event_seq": event.event_seq,
        "event_type": getattr(event_type, "value", event_type),
        "event_hash": event.event_hash,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _planned_ai_units_by_protocol_unit(
    *,
    request: ProtocolRunRequest,
    graph: TaskGraph,
) -> dict[str, str]:
    resolver = getattr(request.plugin_runtime, "planned_ai_unit_id", None)
    if not callable(resolver):
        return {}
    planned_by_unit_id: dict[str, str] = {}
    for unit_id, unit in graph.units.items():
        planned_ai_unit_id = resolver(unit)
        if planned_ai_unit_id is None:
            continue
        if not isinstance(planned_ai_unit_id, str) or not planned_ai_unit_id:
            raise ValueError("planned_ai_unit_id must be a non-empty string")
        if planned_ai_unit_id in planned_by_unit_id.values():
            raise ValueError("planned_ai_unit_id inventory must be unique")
        planned_by_unit_id[unit_id] = planned_ai_unit_id
    return planned_by_unit_id


def _observed_in_flight_ai_unit_ids(
    *,
    request: ProtocolRunRequest,
    graph: TaskGraph,
    worker_execution_facts,
    observed_at: str,
) -> tuple[str, ...]:
    """按真实 worker interval 投影 readiness witness 时仍在执行的 AI units。"""

    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    planned_by_unit_id = _planned_ai_units_by_protocol_unit(
        request=request,
        graph=graph,
    )
    return _ordered_unique(
        planned_by_unit_id[unit_id]
        for fact in worker_execution_facts
        if isinstance((unit_id := fact.get("unit_id")), str)
        and unit_id in planned_by_unit_id
        and isinstance(fact.get("started_at"), str)
        and isinstance(fact.get("ended_at"), str)
        and datetime.fromisoformat(
            str(fact["started_at"]).replace("Z", "+00:00")
        )
        <= observed
        < datetime.fromisoformat(
            str(fact["ended_at"]).replace("Z", "+00:00")
        )
    )


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _safe_id(value: str) -> str:
    if not value:
        raise ValueError("run_id must not be empty")
    readable = "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in value
    )
    if readable == value and len(readable) <= 48:
        return readable
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{readable[:48]}_{digest}"
