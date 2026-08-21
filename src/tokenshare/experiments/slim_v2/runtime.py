"""Slim V2 对单个协议 root 的最小公共装配。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping

from tokenshare.core.models import ProtocolConfig
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ProtocolExecutionScope,
    ProtocolMechanismPolicy,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ProtocolRunResult,
    build_runtime_observation,
    project_protocol_run,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from .execution import ProviderSubmissionAdapter
from .schema import UnitTraceV1
from .storage import RunStore, scan_resume


@dataclass(frozen=True, slots=True)
class RootAssembly:
    """一个 root 独占的系统对象；不跨 root 共享可变 runtime。"""

    run_id: str
    root_input: object
    protocol_config: ProtocolConfig
    artifact_store: ArtifactStore
    event_ledger: EventLedger
    plugin_runtime: object
    worker_backend: object
    now: Callable[[], str]
    observation_clock: Callable[[], str]
    mechanism_policy: ProtocolMechanismPolicy | None = None
    submission_adapter: object | None = None
    hooks: object | None = None
    logical_scheduler: object | None = None
    trace_delay_policy: str = "online_real_time"
    continue_after_terminal_child_failure: bool = True
    scenario: object | None = None


@dataclass(frozen=True, slots=True)
class TailSummaryV1:
    trace_tail_started_at_ms: int | None
    trace_tail_terminal_at_ms: int | None
    trace_tail_wall_clock_ms: int
    trace_tail_status: str
    trace_tail_target_ai_unit_ids: list[str]
    trace_tail_recorded_ai_unit_ids: list[str]
    trace_tail_success_unit_count: int
    trace_tail_failure_unit_count: int
    trace_tail_provider_attempt_count: int
    trace_tail_total_tokens: int | None
    trace_tail_cost_estimate_cny: float | None


def run_root_slice(assembly: RootAssembly) -> ProtocolRunResult:
    """经现有 engine/coordinator 恰好运行一次完整 root 生命周期。"""

    engine = ProtocolEngine(
        event_ledger=assembly.event_ledger,
        protocol_config=assembly.protocol_config,
        artifact_store=assembly.artifact_store,
    )
    coordinator = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=assembly.artifact_store,
        event_ledger=assembly.event_ledger,
        now=assembly.now,
        observation_clock=assembly.observation_clock,
    )
    request = ProtocolRunRequest(
        run_id=assembly.run_id,
        root_input=assembly.root_input,
        plugin_runtime=assembly.plugin_runtime,
        worker_backend=assembly.worker_backend,
        mechanism_policy=assembly.mechanism_policy or ProtocolMechanismPolicy(),
        hooks=assembly.hooks or NoOpRuntimeHooks(),
        continue_after_terminal_child_failure=(
            assembly.continue_after_terminal_child_failure
        ),
        execution_scope=ProtocolExecutionScope(
            mode="whole_root",
            selected_ai_unit_ids=(),
        ),
        trace_delay_policy=assembly.trace_delay_policy,
        logical_scheduler=assembly.logical_scheduler,
    )
    try:
        result = coordinator.run_root(request)
    except Exception as exc:
        result = _project_started_runtime_failure(assembly=assembly, error=exc)
    condition_error = (
        getattr(assembly.submission_adapter, "condition_error", None)
        if assembly.submission_adapter is not None
        else None
    )
    if condition_error is None:
        return result
    if result.status != "failed":
        raise ValueError("provider condition failure requires a failed protocol root")
    return replace(
        result,
        summary={
            **result.summary,
            "slim_condition_failure": {
                "failure_stage": "provider_call",
                "failure_kind": condition_error.error_kind,
            },
        },
    )


def _project_started_runtime_failure(
    *, assembly: RootAssembly, error: Exception
) -> ProtocolRunResult:
    """已写入root lifecycle后，把coordinator异常保留为固定分母失败结果。"""

    events = assembly.event_ledger.read_all()
    registered = [event for event in events if event.event_type == EventType.TASK_REGISTERED]
    if not registered:
        raise error
    created_units = [
        event.payload.get("task_unit")
        for event in events
        if event.event_type == EventType.TASK_UNIT_CREATED
        and isinstance(event.payload.get("task_unit"), Mapping)
    ]
    root_units = [
        unit
        for unit in created_units
        if unit.get("unit_type") == "root" and isinstance(unit.get("unit_id"), str)
    ]
    if len(root_units) != 1:
        raise RuntimeError("started failure lacks a unique root unit") from error
    task_id = registered[0].task_id
    root_unit_id = str(root_units[0]["unit_id"])
    scenario = assembly.scenario
    inventory = getattr(scenario, "inventory", None)
    planned = tuple(getattr(inventory, "planned_ai_unit_ids", ()))
    if not planned:
        raise RuntimeError("started failure lacks structured planned AI units") from error
    adapter = getattr(scenario, "submission_adapter", assembly.submission_adapter)
    attempts = tuple(getattr(adapter, "attempts", ()))
    dispatched = tuple(
        dict.fromkeys(
            str(item.planned_ai_unit_id)
            for item in attempts
            if isinstance(getattr(item, "planned_ai_unit_id", None), str)
        )
    )
    canonical_attempt_ids = {
        event.payload.get("selected_attempt_id")
        for event in events
        if event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and isinstance(event.payload.get("selected_attempt_id"), str)
    }
    completed = tuple(
        dict.fromkeys(
            str(item.planned_ai_unit_id)
            for item in attempts
            if item.attempt_id in canonical_attempt_ids
        )
    )
    facts = tuple(
        fact.to_dict()
        for fact in getattr(assembly.worker_backend, "execution_facts", ())
    )
    scheduler = assembly.logical_scheduler
    ended_at = (
        scheduler.now_timestamp()
        if scheduler is not None and getattr(scheduler, "has_wall_clock_origin", False)
        else assembly.observation_clock()
    )
    observation = build_runtime_observation(
        run_id=assembly.run_id,
        runtime_started_at=events[0].occurred_at,
        runtime_ended_at=ended_at,
        planned_ai_unit_ids=planned,
        dispatched_ai_unit_ids=dispatched,
        completed_ai_unit_ids=completed,
        worker_execution_facts=facts,
    )
    projected = project_protocol_run(
        run_id=assembly.run_id,
        task_id=task_id,
        root_unit_id=root_unit_id,
        event_ledger=assembly.event_ledger,
        artifact_store=assembly.artifact_store,
        runtime_observation=observation,
    )
    return replace(
        projected,
        status="failed",
        summary={
            **projected.summary,
            "slim_runtime_failure": {
                "failure_stage": "protocol_runtime",
                "failure_kind": type(error).__name__,
                "engine_root_status": projected.status,
            },
        },
    )


def materialize_protocol_traces(
    *,
    store: RunStore,
    root_key: tuple[str, str, str, int],
    protocol_result: ProtocolRunResult,
    submission_adapter: ProviderSubmissionAdapter,
    event_ledger: EventLedger,
) -> tuple[UnitTraceV1, ...]:
    """终态后先冻结可重建 trace 的 protocol.json，再幂等物化 trace。"""

    if protocol_result.status not in {"completed", "failed"}:
        raise ValueError("protocol traces require a terminal root")
    events = [event for event in event_ledger.read_all() if event.task_id == protocol_result.task_id]
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
    submission_adapter.apply_protocol_facts(
        canonical_attempt_ids=canonical_attempt_ids,
        verification_status=verification_status,
    )
    planned = sorted(
        {
            attempt.planned_ai_unit_id
            for attempt in submission_adapter.attempts
            if attempt.planned_ai_unit_id is not None
        }
    )
    traces = tuple(
        submission_adapter.build_trace(unit_id, trace_origin="protocol")
        for unit_id in planned
    )
    store.write_root_protocol(
        *root_key,
        {
            "schema_version": "slim_v2.protocol_material.v1",
            "root_key": list(root_key),
            "protocol_result": asdict(protocol_result),
            "traces": [asdict(trace) for trace in traces],
        },
    )
    for trace in traces:
        store.write_trace(trace)
    return traces


def run_coverage_tail(
    *,
    store: RunStore,
    case_id: str,
    target_requests: Mapping[str, Any],
    submission_adapter: ProviderSubmissionAdapter,
    evaluate_submission: Callable[[Any, Any], bool],
    clock_ms: Callable[[], int],
    protocol_result: ProtocolRunResult,
) -> TailSummaryV1:
    """覆盖完整 unscheduled 集合，仅 acquisition 尚无 trace 的 units。"""

    resume = scan_resume(store.run_dir)
    if protocol_result.status not in {"completed", "failed"}:
        raise ValueError("coverage tail requires a terminal protocol result")
    observation = protocol_result.summary.get("runtime_observation")
    if not isinstance(observation, Mapping):
        raise ValueError("protocol result lacks runtime observation")
    unscheduled = observation.get("unscheduled_ai_unit_ids")
    if not isinstance(unscheduled, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in unscheduled
    ):
        raise ValueError("protocol result lacks typed unscheduled unit identities")
    targets = sorted(set(unscheduled))
    if not targets:
        return TailSummaryV1(None, None, 0, "not_needed", [], [], 0, 0, 0, 0, 0.0)
    traces: dict[str, UnitTraceV1] = {}
    for unit_id in targets:
        if (case_id, 0, unit_id) not in resume.trace_keys:
            continue
        trace = store.read_trace(case_id, 0, unit_id)
        if trace.trace_origin != "coverage_tail":
            raise ValueError(
                f"preexisting trace for unscheduled unit {unit_id} is not coverage_tail"
            )
        traces[unit_id] = trace
    pending = [unit_id for unit_id in targets if unit_id not in traces]
    missing_requests = [unit_id for unit_id in pending if unit_id not in target_requests]
    if missing_requests:
        raise ValueError(
            "coverage tail lacks requests for unscheduled units: "
            + ", ".join(missing_requests)
        )
    for unit_id in pending:
        base = target_requests[unit_id]
        accepted = False
        for ordinal in range(3):
            attempt_started_at_ms = clock_ms()
            request = replace(
                base,
                request_id=f"{base.request_id}:coverage-tail:{ordinal}",
                attempt_id=f"{base.attempt_id}:coverage-tail:{ordinal}",
                attempt_ordinal=ordinal,
            )
            submission = submission_adapter.execute(
                request,
                submission_id=f"tail:{case_id}:{unit_id}:{ordinal}",
                submitted_at=base.created_at,
            )
            evaluated = evaluate_submission(request, submission)
            accepted = bool(
                evaluated.get("accepted")
                if isinstance(evaluated, Mapping)
                else evaluated
            )
            reached_domain_check = bool(
                evaluated.get("reached_domain_check", True)
                if isinstance(evaluated, Mapping)
                else submission.result_kind == "succeeded"
            )
            attempt_ended_at_ms = clock_ms()
            submission_adapter.record_tail_evaluation(
                planned_ai_unit_id=unit_id,
                attempt_ordinal=ordinal,
                accepted=accepted,
                reached_domain_check=reached_domain_check,
                started_at_ms=attempt_started_at_ms,
                ended_at_ms=attempt_ended_at_ms,
            )
            if accepted:
                break
        trace = submission_adapter.build_trace(unit_id, trace_origin="coverage_tail")
        store.write_trace(trace)
        traces[unit_id] = trace
    recorded = sorted(traces)
    success = sum(
        any(
            (attempt.verifier_result if trace.domain == "factorization" else attempt.checker_result)
            == "passed"
            for attempt in trace.attempts
        )
        for trace in traces.values()
    )
    failure = len(recorded) - success
    tail_attempts = [attempt for trace in traces.values() for attempt in trace.attempts]
    if any(
        attempt.started_at_ms is None or attempt.ended_at_ms is None
        for attempt in tail_attempts
    ):
        raise ValueError("coverage-tail trace attempt lacks stable timing")
    started = min(int(attempt.started_at_ms) for attempt in tail_attempts)
    terminal = max(int(attempt.ended_at_ms) for attempt in tail_attempts)
    tokens = (
        sum(int(attempt.total_tokens) for attempt in tail_attempts)
        if all(attempt.total_tokens is not None for attempt in tail_attempts)
        else None
    )
    cost = (
        sum(float(attempt.cost_estimate_cny) for attempt in tail_attempts)
        if all(attempt.cost_estimate_cny is not None for attempt in tail_attempts)
        else None
    )
    return TailSummaryV1(
        started,
        terminal,
        terminal - started,
        "completed" if failure == 0 else "completed_with_failures",
        targets,
        recorded,
        success,
        failure,
        len(tail_attempts),
        tokens,
        cost,
    )


__all__ = [
    "RootAssembly", "TailSummaryV1", "materialize_protocol_traces",
    "run_coverage_tail", "run_root_slice",
]
