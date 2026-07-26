"""本地协议 FULL 生命周期协调器。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Callable

from tokenshare.core.contribution import ContributionCoordinator
from tokenshare.core.merge_coordinator import BatchView, MergeCoordinator
from tokenshare.core.models import TaskState
from tokenshare.core.recovery import ACCEPTED, evaluate_retry
from tokenshare.core.registration import RootTaskRegistrar
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import build_verification_report
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
    ParserContext,
    ProtocolMechanismPolicy,
    ProtocolRunRequest,
    ProtocolRunResult,
    RecoveryContext,
    RootProtocolPlan,
    UnitProgressContext,
    VerificationContext,
)
from tokenshare.local_runtime.projection import (
    build_runtime_observation,
    project_protocol_run,
)
from tokenshare.local_runtime.workers import WorkerBatchOutcome, execute_worker_batch
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType


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
    ) -> None:
        self._engine = engine
        self._artifact_store = artifact_store
        self._event_ledger = event_ledger
        self._now = now or _utc_now
        self._observation_clock = observation_clock or _utc_now

    def run_root(self, request: ProtocolRunRequest) -> ProtocolRunResult:
        if request.worker_backend.capacity < 1:
            raise ValueError("runtime worker capacity must be positive")
        if request.worker_backend.capacity != 1 and not callable(
            getattr(request.worker_backend, "execute_batch", None)
        ):
            raise ValueError(
                "multi-worker backend requires execute_batch; use a single-worker-compatible backend"
            )
        runtime_started_at = self._observation_clock()
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
            now=self._now(),
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
        pending_executions: list[tuple[object, object, WorkerBatchOutcome]] = []
        runtime_observations: list[dict[str, object]] = []
        worker_execution_facts: list[dict[str, object]] = []
        partial_observation = False
        witness_observed_at: str | None = None
        in_flight_ai_unit_ids_at_witness: tuple[str, ...] = ()

        while True:
            activatable = () if pending_executions else graph.activatable_unit_ids()
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
                    now=self._now(),
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
                and not pending_executions
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
                    witness_observed_at = self._observation_clock()
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
            if pending_executions or (ready and not merge_ready_before_dispatch):
                if not pending_executions:
                    prepared: list[tuple[object, object, object]] = []
                    for _slot in range(request.worker_backend.capacity):
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
                            now=self._now(),
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
                    worker_execution_facts.extend(
                        outcome.fact.to_dict() for outcome in worker_outcomes
                    )
                    pending_executions.extend(
                        (scheduled, request_flow, worker_outcome)
                        for (
                            _execution_request,
                            request_flow,
                            scheduled,
                        ), worker_outcome in zip(
                            prepared,
                            worker_outcomes,
                            strict=True,
                        )
                    )
                scheduled, request_flow, worker_outcome = pending_executions.pop(0)
                unit_id = scheduled.task_unit.unit_id
                execution = self._execute_unit(
                    request=request,
                    plan=plan,
                    graph=graph,
                    scheduled=scheduled,
                    request_flow=request_flow,
                    worker_outcome=worker_outcome,
                    retry_counts=retry_counts,
                    runtime_observations=runtime_observations,
                )
                graph = execution["graph"]
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
                                raise RuntimeError(
                                    f"child unit failed after retry limit: {unit_id}"
                                )
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
                        witness_observed_at = self._observation_clock()
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
                        now=self._now(),
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
                        now=self._now(),
                        correlation_id=f"{request.run_id}:parent_completion",
                        causation_event_id=merge_resolution.events[-1].event_id,
                    )
                    graph = _replace_graph_unit(graph, parent_completion.task_unit)
                    settlement_contributions = [
                        item.contribution
                        for item in contributions
                        if item.contribution.kind != "expand_canonical"
                    ] + list(parent_completion.expand_contributions)
                    self._settle(
                        request=request,
                        plan=plan,
                        root_unit_id=registration.root_unit.unit_id,
                        completion_event_seq=_completion_event_seq(
                            parent_completion.events,
                            registration.root_unit.unit_id,
                        ),
                        contributions=settlement_contributions,
                    )
                    break

                action = plan.canonical_action_builder(
                    CanonicalUnitContext(
                        unit=canonical_unit,
                        canonical_selection=canonical.canonical_selection,
                    )
                )
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
                            now=self._now(),
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
                    witness_observed_at = self._observation_clock()
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
                            created_at=self._now(),
                        )
                        runtime_observations.append(
                            {
                                "event_type": (
                                    "EXPERIMENT_PREMATURE_MERGE_ATTEMPTED"
                                ),
                                **attempt_body,
                                "result_artifact_ref": result_ref.to_dict(),
                            }
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
                        now=self._now(),
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

        runtime_ended_at = self._observation_clock()
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
        if not runtime_observations:
            return projected
        return replace(
            projected,
            summary={
                **projected.summary,
                "runtime_hook_observations": runtime_observations,
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
            now=self._now(),
            correlation_id=f"{request.run_id}:parent_failure:{parent_unit_id}",
        )
        return _replace_graph_unit(graph, failure.task_unit)

    def _prepare_unit_execution(self, *, request, scheduled) -> tuple[object, object]:
        execution_request = request.plugin_runtime.build_execution_request(
            scheduled.task_unit,
            attempt=scheduled.attempt,
            lease=scheduled.lease,
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
    ) -> dict[str, object]:
        unit_id = scheduled.task_unit.unit_id
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
                recovery_now=(scheduled.lease.expires_at if worker_terminated else None),
                runtime_observations=runtime_observations,
            )
        submission = worker_outcome.submission
        if submission.result_kind == "no_return":
            return self._recover(
                request=request,
                plan=plan,
                graph=graph,
                scheduled=scheduled,
                retry_counts=retry_counts,
                trigger="lease_expired",
                causation_event_id=request_flow.event.event_id,
                recovery_now=scheduled.lease.expires_at,
                runtime_observations=runtime_observations,
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
                )
        else:
            report = _build_no_verification_report(
                execution_request=request_flow.request,
                submission=submission,
                submitted_attempt=submission_flow.attempt,
                now=self._now(),
            )
        report = replace(report, submission_event_seq=submission_flow.event.event_seq)
        verification = self._engine.record_verification(
            report=report,
            attempt=submission_flow.attempt,
            correlation_id=f"{request.run_id}:verification:{scheduled.attempt.attempt_id}",
            causation_event_id=submission_flow.event.event_id,
        )
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
            )
        canonical = self._engine.bind_canonical_outputs(
            task_id=scheduled.task_unit.task_id,
            unit_id=unit_id,
            verification_events=[verification.event],
            attempts_by_id={verification.attempt.attempt_id: verification.attempt},
            policy=plan.registration_request.protocol_config.canonical_output_policy,
            now=self._now(),
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
    ) -> dict[str, object]:
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
                attempt=attempt_for_recovery or scheduled.attempt,
                task_unit=scheduled.task_unit,
                now=recovery_now or self._now(),
                correlation_id=correlation_id,
                recovery_action_id=recovery_action_id,
                retry_count=retry_count,
                causation_event_id=causation_event_id,
            )
        else:
            recovery = self._engine.record_recovery_decision(
                decision=decision,
                attempt=attempt_for_recovery or scheduled.attempt,
                lease=scheduled.lease,
                task_unit=scheduled.task_unit,
                recovery_action_id=recovery_action_id,
                now=recovery_now or self._now(),
                correlation_id=correlation_id,
                causation_event_id=causation_event_id,
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
        requeue_directive = _observe(
            request.hooks.before_requeue,
            RecoveryContext(
                unit=recovery.task_unit,
                attempt=recovery.attempt,
                lease=recovery.lease,
                trigger=trigger,
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
                recovery_event_refs=recovery_event_refs,
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
        return {
            "graph": graph,
            "canonical": None,
            "failed": not decision.retry_allowed,
            "halt_run": bool(halt_run),
            "requeue_blocked": requeue_blocked,
            "failure_event": (
                recovery.events[-1] if not decision.retry_allowed else None
            ),
        }

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
            now=self._now(),
            correlation_id=f"{request.run_id}:settlement",
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


def _collect_observations(observations: list[dict], directive) -> None:
    if directive is None:
        return
    for record in getattr(directive, "experiment_records", ()):
        if not isinstance(record, dict):
            raise TypeError("runtime hook experiment records must be objects")
        observations.append(dict(record))


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
        validator_policy_id=str(
            plugin.get("validator_policy_id")
            or "runtime_ablation_no_verification_v1"
        ),
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
        },
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
