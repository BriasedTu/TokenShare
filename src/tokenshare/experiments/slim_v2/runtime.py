"""Slim V2 对单个协议 root 的最小公共装配。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import BoundedSemaphore
from time import time_ns
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from tokenshare.core.models import ArtifactRef, ProtocolConfig, TaskState, TaskUnit
from tokenshare.executors.contracts import (
    EnvironmentRef,
    ExecutionRequest,
)
from tokenshare.local_runtime import (
    NoOpRuntimeHooks,
    ProtocolExecutionScope,
    ProtocolMechanismPolicy,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    ProtocolRunResult,
    SequentialWorkerBackend,
    ThreadWorkerBackend,
    build_runtime_observation,
    project_protocol_run,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationExecutionBridge,
    FactorizationRuntimeAdapter,
)
from tokenshare.plugins.factorization.models import FactorSearchRangeInput
from tokenshare.plugins.factorization.validator import verify_range_result
from tokenshare.plugins.contracts import OutputContract
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
)
from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.runtime_adapter import (
    LeanCanonicalDependencyUnavailableError,
    LeanExecutionBridge,
    LeanRuntimeAdapter,
)
from tokenshare.plugins.lean_proof.prompt_builder import (
    PROOF_CANDIDATE_OUTPUT_NAME,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from .execution import (
    ProviderSubmissionAdapter,
    reconstruct_fixed_trace_attempt,
)
from .provider import (
    ProviderCallContextV1,
    call_provider_once,
    recover_interrupted_call,
)
from .schema import (
    AttemptResultV1,
    ProviderCallResultV1,
    ProviderEntryViewV1,
    ProviderRequestControlV1,
    RootInventoryV1,
    RootResultV2,
    PRE_FLASH_CONFIGURED_MODEL,
    PRE_FLASH_EXP2_ROOT_KEY,
    PRE_FLASH_PROVIDER_ENTRY_ID,
    PRE_FLASH_REPRESENTATIVE_RUN_ID,
    UnitTraceV1,
)
from .storage import (
    RunStore,
    StorageConflictError,
    coverage_tail_blocked,
    scan_resume,
)


_REPO_ROOT = Path(__file__).parents[4]
_LEAN_VERSION = "Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)"
_LAKE_VERSION = "Lake version 5.0.0-df668f0 (Lean version 4.8.0)"


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
    inventory: RootInventoryV1 | None = None
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
    inventory = assembly.inventory or getattr(scenario, "inventory", None)
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
                "failure_kind": "infrastructure_invalid",
                "error_kind": type(error).__name__,
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
    tail_requests: Mapping[str, Mapping[str, Any]] | None = None,
    protocol_projection: RootResultV2 | None = None,
) -> tuple[UnitTraceV1, ...]:
    """终态后先冻结可重建 trace 的 protocol.json，再幂等物化 trace。"""

    traces = _protocol_traces(
        protocol_result=protocol_result,
        submission_adapter=submission_adapter,
        event_ledger=event_ledger,
    )
    store.write_root_protocol_snapshot(
        *root_key,
        protocol_result=asdict(protocol_result),
        traces=traces,
        tail_requests=tail_requests,
        protocol_projection=protocol_projection,
    )
    for trace in traces:
        store.write_trace(trace)
    return traces


def _protocol_traces(
    *,
    protocol_result: ProtocolRunResult,
    submission_adapter: ProviderSubmissionAdapter,
    event_ledger: EventLedger,
) -> tuple[UnitTraceV1, ...]:
    """从当前活跃 root 提取普通 protocol traces，但不决定写入顺序。"""

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
    targets = list(unscheduled)
    if len(targets) != len(set(targets)):
        raise ValueError("protocol result has duplicate unscheduled identities")
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
    recorded = [unit_id for unit_id in targets if unit_id in traces]
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
    provider_attempts = [
        attempt for attempt in tail_attempts if attempt.provider_call_made is True
    ]
    if any(
        attempt.started_at_ms is None or attempt.ended_at_ms is None
        for attempt in tail_attempts
    ):
        raise ValueError("coverage-tail trace attempt lacks stable timing")
    started = min(int(attempt.started_at_ms) for attempt in tail_attempts)
    terminal = max(int(attempt.ended_at_ms) for attempt in tail_attempts)
    tokens = (
        sum(int(attempt.total_tokens) for attempt in provider_attempts)
        if all(attempt.total_tokens is not None for attempt in provider_attempts)
        else None
    )
    cost = (
        sum(float(attempt.cost_estimate_cny) for attempt in provider_attempts)
        if all(
            attempt.cost_estimate_cny is not None for attempt in provider_attempts
        )
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
        len(provider_attempts),
        tokens,
        cost,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clock_ms() -> int:
    return time_ns() // 1_000_000


def _root_key(context: Any) -> tuple[str, str, str, int]:
    inventory = context.inventory
    return (
        str(inventory.experiment_id),
        str(inventory.condition_id),
        str(inventory.case_id),
        int(inventory.repeat_id),
    )


def _root_run_id(context: Any) -> str:
    experiment, condition, case_id, repeat_id = _root_key(context)
    return (
        f"{context.run_id}:{experiment}:{condition}:{case_id}:{repeat_id}"
    )


def _call_provider_with_resume(
    entry: ProviderEntryViewV1,
    prompt: str,
    control: ProviderRequestControlV1,
    context: ProviderCallContextV1,
    store: RunStore,
    *,
    provider_call: Callable[..., ProviderCallResultV1] = call_provider_once,
) -> ProviderCallResultV1:
    """复用同 ordinal 的 typed terminal outcome；未知 intent 绝不重调。"""

    if store.call_terminal_path(context.call_key).is_file():
        try:
            return store.read_provider_terminal_result(
                context.call_key,
                context=context,
            )
        except (TypeError, ValueError) as exc:
            raise StorageConflictError(
                "provider terminal is not reusable without a second call: "
                f"{context.call_key}"
            ) from exc
    if store.call_intent_path(context.call_key).is_file():
        return recover_interrupted_call(entry, context, store)
    result = provider_call(entry, prompt, control, context, store)
    if not isinstance(result, ProviderCallResultV1):
        raise TypeError("provider caller must return ProviderCallResultV1")
    result.validate()
    try:
        persisted = store.read_provider_terminal_result(
            context.call_key,
            context=context,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise StorageConflictError(
            "provider caller returned without a reusable typed terminal: "
            f"{context.call_key}"
        ) from exc
    if persisted != result:
        raise StorageConflictError(
            f"provider terminal differs from caller result: {context.call_key}"
        )
    return persisted


def _provider_caller_for_experiment(
    experiment_id: str,
) -> Callable[..., ProviderCallResultV1]:
    """为单个 root 创建共享 provider permit，不改变 worker backend capacity。"""

    try:
        permit = BoundedSemaphore({"exp1": 10, "exp5": 3}[experiment_id])
    except KeyError as exc:
        raise ValueError(f"experiment has no online provider: {experiment_id}") from exc

    def provider_call_with_permit(
        entry: ProviderEntryViewV1,
        prompt: str,
        control: ProviderRequestControlV1,
        context: ProviderCallContextV1,
        store: RunStore,
    ) -> ProviderCallResultV1:
        with permit:
            return call_provider_once(entry, prompt, control, context, store)

    def caller(
        entry: ProviderEntryViewV1,
        prompt: str,
        control: ProviderRequestControlV1,
        context: ProviderCallContextV1,
        store: RunStore,
    ) -> ProviderCallResultV1:
        return _call_provider_with_resume(
            entry,
            prompt,
            control,
            context,
            store,
            provider_call=provider_call_with_permit,
        )

    return caller


def _protocol_config(context: Any, system_dir: Path) -> ProtocolConfig:
    artifact_dir = system_dir / "artifacts"
    event_path = system_dir / "events.jsonl"
    config = ProtocolConfig.default(
        config_id="slim-v2-" + "-".join(str(item) for item in _root_key(context)),
        artifact_store_uri=artifact_dir.resolve().as_uri(),
        event_log_uri=event_path.resolve().as_uri(),
        metadata={"slim_v2_profile_id": str(context.profile_id)},
    )
    return replace(
        config,
        max_retries=int(context.max_retries),
        max_children_per_unit=max(
            config.max_children_per_unit,
            len(context.inventory.planned_ai_unit_ids),
        ),
        max_total_units=max(
            config.max_total_units,
            len(context.inventory.planned_ai_unit_ids) + 2,
        ),
    )


def _lean_environment() -> LeanEnvironmentManifest:
    local_app_data = os.environ.get("LOCALAPPDATA")
    tools_root = (
        Path(local_app_data) / "TokenShare" / "LeanToolchain"
        if local_app_data
        else Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    )
    elan_bin = tools_root / "elan-home" / "bin"
    lean = elan_bin / ("lean.exe" if os.name == "nt" else "lean")
    lake = elan_bin / ("lake.exe" if os.name == "nt" else "lake")
    project = _REPO_ROOT / "fixtures" / "lean_proof_project"
    missing = [str(path) for path in (lean, lake) if not path.is_file()]
    if missing:
        raise RuntimeError("Lean runtime toolchain is unavailable: " + ", ".join(missing))
    return LeanEnvironmentManifest.from_project(
        project_root=project,
        lean_executable=lean,
        lake_executable=lake,
        lean_version=_LEAN_VERSION,
        lake_version=_LAKE_VERSION,
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=_utc_now(),
    )


def _plugin_runtime(
    *,
    context: Any,
    protocol_config: ProtocolConfig,
    provider_family: str,
) -> object:
    common = {
        "provider_family": provider_family,
        "seed": 20260820,
        "protocol_config": protocol_config,
        "created_at": _utc_now(),
    }
    if context.inventory.domain == "factorization":
        return FactorizationRuntimeAdapter(**common)
    if context.inventory.domain == "lean":
        return LeanRuntimeAdapter(
            **common,
            environment_manifest=_lean_environment(),
        )
    raise ValueError(f"unsupported Slim V2 domain: {context.inventory.domain}")


def _provider_entry(context: Any) -> ProviderEntryViewV1:
    entry_id = str(context.inventory.provider_entry_id)
    entry = context.provider_entries.get(entry_id)
    if not isinstance(entry, ProviderEntryViewV1):
        raise RuntimeError(f"provider entry is unavailable for root: {entry_id}")
    entry.validate()
    return entry


def _reasoning_mode(entry: ProviderEntryViewV1) -> str:
    overrides = dict(entry.request_overrides)
    if entry.provider_family == "deepseek":
        thinking = overrides.get("thinking")
        return (
            "thinking"
            if isinstance(thinking, Mapping) and thinking.get("type") == "enabled"
            else "nonthinking"
        )
    return "thinking" if overrides.get("enable_thinking") is True else "nonthinking"


def _build_root_assembly(context: Any) -> RootAssembly:
    """只为当前 root 创建一套 plugin/backend/store/ledger 对象。"""

    key = _root_key(context)
    system_dir = context.run_store.system_root_directory(*key)
    artifact_dir = system_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_store = ArtifactStore(system_dir)
    event_ledger = EventLedger(system_dir / "events.jsonl")
    protocol_config = _protocol_config(context, system_dir)
    experiment_id = str(context.inventory.experiment_id)
    provider_family = "siliconflow" if experiment_id == "exp5" else "deepseek"
    plugin_runtime = _plugin_runtime(
        context=context,
        protocol_config=protocol_config,
        provider_family=provider_family,
    )

    if experiment_id in {"exp1", "exp5"}:
        entry = _provider_entry(context)
        submission_adapter = ProviderSubmissionAdapter(
            entry=entry,
            artifact_store=artifact_store,
            run_store=context.run_store,
            root_key=key,
            domain=str(context.inventory.domain),
            caller=_provider_caller_for_experiment(experiment_id),
        )
        if isinstance(plugin_runtime, FactorizationRuntimeAdapter):
            executor = FactorizationExecutionBridge(
                plugin_runtime=plugin_runtime,
                range_executor=submission_adapter,
            )
        elif isinstance(plugin_runtime, LeanRuntimeAdapter):
            executor = LeanExecutionBridge(
                plugin_runtime=plugin_runtime,
                proof_candidate_executor=submission_adapter,
            )
        else:  # pragma: no cover - guarded by _plugin_runtime
            raise TypeError("unsupported online plugin runtime")
        worker_count = int(context.inventory.worker_count)
        backend = (
            SequentialWorkerBackend(executor=executor, submitted_at=_utc_now)
            if worker_count == 1
            else ThreadWorkerBackend(
                executor=executor,
                capacity=worker_count,
                submitted_at=_utc_now,
            )
        )
        return RootAssembly(
            run_id=_root_run_id(context),
            root_input=context.root_input,
            protocol_config=protocol_config,
            artifact_store=artifact_store,
            event_ledger=event_ledger,
            plugin_runtime=plugin_runtime,
            worker_backend=backend,
            now=_utc_now,
            observation_clock=_utc_now,
            inventory=context.inventory,
            mechanism_policy=ProtocolMechanismPolicy(
                replacement_attempts_allowed=int(context.max_retries) > 0
            ),
            submission_adapter=submission_adapter,
            continue_after_terminal_child_failure=bool(
                context.continue_after_terminal_child_failure
            ),
        )

    if experiment_id not in {"exp2", "exp3", "exp4"}:
        raise ValueError(f"unsupported Slim V2 experiment: {experiment_id}")
    if context.source_run_dir is None:
        raise RuntimeError(f"{experiment_id} requires an explicit source run dir")
    from .scenarios import build_scenario

    scenario = build_scenario(
        inventory=context.inventory,
        root_input=context.root_input,
        source_store=RunStore(context.source_run_dir),
        artifact_store=artifact_store,
        plugin_runtime=plugin_runtime,
        protocol_config=protocol_config,
        submitted_at=_utc_now,
        challenge_plan=context.challenge_plan,
    )
    return RootAssembly(
        run_id=_root_run_id(context),
        root_input=context.root_input,
        protocol_config=protocol_config,
        artifact_store=artifact_store,
        event_ledger=event_ledger,
        plugin_runtime=plugin_runtime,
        worker_backend=scenario.worker_backend,
        now=_utc_now,
        observation_clock=_utc_now,
        inventory=context.inventory,
        mechanism_policy=scenario.mechanism_policy,
        submission_adapter=scenario.submission_adapter,
        hooks=scenario.hooks,
        logical_scheduler=scenario.logical_scheduler,
        trace_delay_policy="logical_source_latency_1x",
        continue_after_terminal_child_failure=bool(
            context.continue_after_terminal_child_failure
        ),
        scenario=scenario,
    )


def _online_resolved_model(adapter: ProviderSubmissionAdapter) -> str | None:
    for outcome in reversed(adapter.outcomes):
        if outcome.resolved_model is not None:
            return outcome.resolved_model
    return None


def _task_unit_from_document(document: Mapping[str, Any]) -> TaskUnit:
    expected = set(TaskUnit.__dataclass_fields__)
    if set(document) != expected:
        raise ValueError("invalid TaskUnit snapshot fields")
    values = dict(document)
    values["state"] = TaskState(values["state"])
    for name in ("input_refs", "canonical_output_refs"):
        refs = values[name]
        if not isinstance(refs, Mapping):
            raise ValueError(f"TaskUnit {name} must be an object")
        values[name] = {
            str(key): ArtifactRef.from_dict(value)
            for key, value in refs.items()
            if isinstance(value, Mapping)
        }
        if len(values[name]) != len(refs):
            raise ValueError(f"TaskUnit {name} entries must be objects")
    return TaskUnit(**values)


def _planned_from_unit_snapshot(
    snapshot: Mapping[str, Any],
    *,
    domain: str,
) -> str | None:
    if domain == "factorization":
        payload = snapshot.get("plugin_payload")
        summary = payload.get("summary") if isinstance(payload, Mapping) else None
        index = summary.get("child_index") if isinstance(summary, Mapping) else None
        return f"range_{index}" if isinstance(index, int) and index >= 0 else None
    metadata = snapshot.get("metadata")
    logical_key = (
        metadata.get("child_logical_key")
        if isinstance(metadata, Mapping)
        else None
    )
    return logical_key if isinstance(logical_key, str) and logical_key else None


def _tail_requests_from_assembly(
    *,
    assembly: RootAssembly,
    protocol_result: ProtocolRunResult,
    domain: str,
    case_id: str,
) -> tuple[dict[str, ExecutionRequest], dict[str, UnitTraceV1]]:
    observation = protocol_result.summary.get("runtime_observation")
    unscheduled = (
        observation.get("unscheduled_ai_unit_ids")
        if isinstance(observation, Mapping)
        else None
    )
    if not isinstance(unscheduled, (list, tuple)):
        raise ValueError("protocol result lacks unscheduled identities")
    targets = list(unscheduled)
    if any(not isinstance(item, str) or not item for item in targets):
        raise ValueError("protocol result has invalid unscheduled identities")
    if len(targets) != len(set(targets)):
        raise ValueError("protocol result has duplicate unscheduled identities")
    if not targets:
        return {}, {}
    target_set = set(targets)
    snapshots: dict[str, Mapping[str, Any]] = {}
    for event in assembly.event_ledger.read_all():
        if event.event_type != EventType.TASK_UNIT_CREATED:
            continue
        snapshot = event.payload.get("task_unit")
        if not isinstance(snapshot, Mapping):
            continue
        planned = _planned_from_unit_snapshot(snapshot, domain=domain)
        if planned in target_set:
            if planned in snapshots:
                raise ValueError("duplicate task unit for coverage-tail target")
            snapshots[planned] = snapshot
    requests: dict[str, ExecutionRequest] = {}
    pre_dispatch_traces: dict[str, UnitTraceV1] = {}
    for planned in targets:
        snapshot = snapshots.get(planned)
        if snapshot is None:
            raise ValueError(f"coverage-tail target lacks a TaskUnit: {planned}")
        unit = _task_unit_from_document(snapshot)
        identity = "".join(
            character if character.isalnum() else "_" for character in planned
        )
        created_at = _utc_now()
        attempt = SimpleNamespace(
            attempt_id=f"slim_tail_plan:{identity}:0",
            client_id="slim-v2-coverage-tail",
            created_at=created_at,
            started_at=created_at,
            attempt_ordinal=0,
        )
        lease = SimpleNamespace(
            lease_id=f"slim_tail_lease:{identity}:0",
            fencing_token=f"slim-tail:{identity}:0",
            expires_at=created_at,
        )
        try:
            request = assembly.plugin_runtime.build_execution_request(
                unit,
                attempt=attempt,
                lease=lease,
            )
        except LeanCanonicalDependencyUnavailableError:
            pre_dispatch_traces[planned] = _lean_pre_dispatch_failure_trace(
                assembly=assembly,
                case_id=case_id,
                planned=planned,
                unit=unit,
                identity=identity,
            )
            continue
        if not isinstance(request, ExecutionRequest):
            raise TypeError("plugin runtime must build an ExecutionRequest")
        hints = dict(request.soft_hints or {})
        if hints.get("planned_ai_unit_id") != planned:
            raise ValueError("coverage-tail request planned identity differs")
        hints["replacement_slot"] = 0
        requests[planned] = replace(
            request,
            attempt_ordinal=0,
            soft_hints=hints,
        )
    return requests, pre_dispatch_traces


def _lean_pre_dispatch_failure_trace(
    *,
    assembly: RootAssembly,
    case_id: str,
    planned: str,
    unit: TaskUnit,
    identity: str,
) -> UnitTraceV1:
    adapter = assembly.submission_adapter
    if not isinstance(adapter, ProviderSubmissionAdapter):
        raise TypeError("Lean coverage-tail pre-dispatch trace requires provider adapter")
    if not isinstance(assembly.plugin_runtime, LeanRuntimeAdapter):
        raise TypeError("Lean runtime cannot expose canonical dependency semantics")
    dependency_path = assembly.plugin_runtime.dependency_path_for_planned_unit(
        planned
    )
    if not isinstance(dependency_path, list) or any(
        not isinstance(item, str) or not item for item in dependency_path
    ):
        raise TypeError("Lean runtime returned invalid dependency semantics")
    timestamp = _clock_ms()
    present_nullable = {
        "trace_origin",
        "started_at_ms",
        "ended_at_ms",
        "parse_result",
        "checker_result",
    }
    attempt = AttemptResultV1(
        attempt_id=f"slim_tail_pre_dispatch:{identity}:0",
        unit_id=unit.unit_id,
        planned_ai_unit_id=planned,
        attempt_ordinal=0,
        trace_origin="coverage_tail",
        started_at_ms=timestamp,
        ended_at_ms=timestamp,
        result_kind="pre_dispatch_failure",
        provider_call_made=False,
        raw_response_present=False,
        parse_result="not_reached",
        checker_result="not_reached",
        canonical_accepted=False,
        usage_status="not_available",
        call_state="not_started",
        missing_reason={
            f"attempts[].{path}": "coverage_tail_pre_dispatch_failure"
            for path in AttemptResultV1.nullable_leaf_paths()
            if path not in present_nullable
        },
    )
    attempt.validate(experiment_id="exp1")
    entry = adapter.entry
    trace = UnitTraceV1(
        case_id=case_id,
        source_repeat_id=0,
        planned_ai_unit_id=planned,
        domain="lean",
        trace_origin="coverage_tail",
        lemma_node_id=planned,
        dependency_path=list(dependency_path),
        provider_family=entry.provider_family,
        provider_entry_id=entry.entry_id,
        configured_model=entry.configured_model,
        requested_model=entry.configured_model,
        resolved_model=entry.configured_model,
        attempts=[attempt],
    )
    trace.validate()
    return trace


def _execution_request_from_document(
    document: Mapping[str, Any],
) -> ExecutionRequest:
    expected = set(ExecutionRequest.__dataclass_fields__)
    if set(document) != expected:
        raise ValueError("invalid ExecutionRequest ordinary fields")
    values = dict(document)
    refs = values["input_artifact_refs"]
    if not isinstance(refs, Mapping):
        raise ValueError("ExecutionRequest input refs must be an object")
    values["input_artifact_refs"] = {
        str(key): ArtifactRef.from_dict(value)
        for key, value in refs.items()
        if isinstance(value, Mapping)
    }
    if len(values["input_artifact_refs"]) != len(refs):
        raise ValueError("ExecutionRequest input refs must be ArtifactRef objects")
    for name in (
        "execution_instruction_ref",
        "prompt_package_ref",
    ):
        value = values[name]
        if value is not None:
            if not isinstance(value, Mapping):
                raise ValueError(f"ExecutionRequest {name} must be an ArtifactRef")
            values[name] = ArtifactRef.from_dict(value)
    environment = values["environment_ref"]
    contract = values["output_contract"]
    if not isinstance(environment, Mapping) or not isinstance(contract, Mapping):
        raise ValueError("ExecutionRequest nested contracts must be objects")
    values["environment_ref"] = EnvironmentRef(**dict(environment))
    values["output_contract"] = OutputContract(**dict(contract))
    return ExecutionRequest(**values)


def _protocol_result_from_document(
    document: Mapping[str, Any],
) -> ProtocolRunResult:
    run_id = document.get("run_id")
    status = document.get("status")
    summary = document.get("summary")
    if (
        not isinstance(run_id, str)
        or not run_id
        or status not in {"completed", "failed"}
        or not isinstance(summary, Mapping)
    ):
        raise ValueError("protocol snapshot result is not terminal/typed")
    return ProtocolRunResult(
        run_id=run_id,
        task_id=(
            document.get("task_id")
            if isinstance(document.get("task_id"), str)
            else None
        ),
        root_unit_id=(
            document.get("root_unit_id")
            if isinstance(document.get("root_unit_id"), str)
            else None
        ),
        status=status,
        summary=dict(summary),
    )


def _placeholder_tail(protocol_result: ProtocolRunResult) -> TailSummaryV1 | None:
    observation = protocol_result.summary.get("runtime_observation")
    targets = (
        observation.get("unscheduled_ai_unit_ids")
        if isinstance(observation, Mapping)
        else None
    )
    if not isinstance(targets, list) or any(
        not isinstance(item, str) or not item for item in targets
    ):
        raise ValueError("protocol result lacks typed tail targets")
    if not targets:
        return None
    return TailSummaryV1(
        0,
        0,
        0,
        "pending_coverage_tail",
        list(targets),
        list(targets),
        0,
        len(targets),
        len(targets),
        None,
        None,
    )


def _evaluate_tail_submission(
    *,
    domain: str,
    artifact_store: ArtifactStore,
    request: ExecutionRequest,
    submission: Any,
) -> dict[str, bool]:
    if submission.result_kind != "succeeded":
        return {"accepted": False, "reached_domain_check": False}
    if domain == "factorization":
        range_ref = request.input_artifact_refs.get("range_input")
        candidate_ref = submission.candidate_output_refs.get("range_result")
        if range_ref is None or candidate_ref is None:
            return {"accepted": False, "reached_domain_check": False}
        child = FactorSearchRangeInput(
            **json.loads(artifact_store.read_bytes(range_ref).decode("utf-8"))
        )
        candidate = json.loads(
            artifact_store.read_bytes(candidate_ref).decode("utf-8")
        )
        report = verify_range_result(candidate, child_input=child)
        return {"accepted": bool(report.accepted), "reached_domain_check": True}
    proof_ref = submission.candidate_output_refs.get(PROOF_CANDIDATE_OUTPUT_NAME)
    theorem_ref = request.input_artifact_refs.get("lemma_theorem_payload")
    if theorem_ref is None:
        theorem_ref = request.input_artifact_refs.get("child_theorem_payload")
    if proof_ref is None or theorem_ref is None:
        return {"accepted": False, "reached_domain_check": False}
    manifest = _lean_environment()
    report = check_lean_proof(
        LeanCheckerRequest(
            request_id=f"tail-checker:{request.request_id}",
            theorem_payload_ref=theorem_ref,
            proof_candidate_ref=proof_ref,
            environment_ref=request.environment_ref,
            checker_mode=LeanCheckerMode.CHILD_PROOF,
            timeout_seconds=int(request.limits.get("timeout_seconds", 30)),
            max_output_bytes=65536,
            created_at=_utc_now(),
        ),
        artifact_store=artifact_store,
        environment_manifest=manifest,
    )
    return {
        "accepted": report.status == LeanCheckerStatus.ACCEPTED,
        "reached_domain_check": True,
    }


def _with_persisted_tail_attempts(
    *,
    result: RootResultV2,
    tail: TailSummaryV1,
    store: RunStore,
    case_id: str,
) -> RootResultV2:
    """按冻结顺序把持久化 coverage-tail attempts 接到 protocol 投影之后。"""

    targets = tail.trace_tail_target_ai_unit_ids
    if tail.trace_tail_recorded_ai_unit_ids != targets:
        raise ValueError("coverage-tail recorded identities differ from target order")
    if any(attempt.trace_origin != "protocol" for attempt in result.attempts):
        raise ValueError("Exp1 protocol projection contains a non-protocol attempt")
    protocol_units = {
        attempt.planned_ai_unit_id
        for attempt in result.attempts
        if attempt.planned_ai_unit_id is not None
    }
    if protocol_units.intersection(targets):
        raise ValueError("coverage-tail target already appears in protocol attempts")

    attempts = list(result.attempts)
    for target in targets:
        trace = store.read_trace(case_id, 0, target)
        if (
            trace.case_id != case_id
            or trace.planned_ai_unit_id != target
            or trace.trace_origin != "coverage_tail"
        ):
            raise ValueError(
                f"persisted trace for coverage-tail target {target} has wrong identity"
            )
        trace.validate()
        if any(attempt.planned_ai_unit_id != target for attempt in trace.attempts):
            raise ValueError(
                f"persisted trace attempts for coverage-tail target {target} have wrong identity"
            )
        attempts.extend(sorted(trace.attempts, key=lambda item: item.attempt_ordinal))

    values = {
        name: getattr(tail, name) for name in TailSummaryV1.__dataclass_fields__
    }
    projected = replace(result, attempts=attempts, **values)
    projected.validate()
    return projected


def execute_root_context(context: Any) -> RootResultV2:
    """CLI 生产 seam：唯一装配并运行一个 root，再冻结可恢复投影。"""

    assembly = _build_root_assembly(context)
    protocol_result = run_root_slice(assembly)
    inventory = context.inventory
    experiment_id = str(inventory.experiment_id)
    scenario = assembly.scenario
    tail_summary: TailSummaryV1 | None = None
    protocol_projection: RootResultV2 | None = None
    from .projector import project_root_result

    if isinstance(assembly.submission_adapter, ProviderSubmissionAdapter):
        adapter = assembly.submission_adapter
        if len(adapter.attempts) > int(context.protocol_execution_attempt_upper):
            raise RuntimeError("protocol execution attempt cap exceeded")
        if len(adapter.outcomes) > int(context.provider_call_upper):
            raise RuntimeError("provider call cap exceeded")
        entry = adapter.entry
        requested_model = str(entry.configured_model)
        resolved_model = _online_resolved_model(adapter)
        provider_family = str(entry.provider_family)
        reasoning_mode = _reasoning_mode(entry)
        if experiment_id == "exp1":
            traces = _protocol_traces(
                protocol_result=protocol_result,
                submission_adapter=adapter,
                event_ledger=assembly.event_ledger,
            )
            acquisition_failure = coverage_tail_blocked(protocol_result.summary)
            observation = protocol_result.summary.get("runtime_observation")
            unscheduled = (
                observation.get("unscheduled_ai_unit_ids", [])
                if isinstance(observation, Mapping)
                else []
            )
            tail_preparation = (
                _tail_requests_from_assembly(
                    assembly=assembly,
                    protocol_result=protocol_result,
                    domain=str(inventory.domain),
                    case_id=str(inventory.case_id),
                )
                if not acquisition_failure and unscheduled
                else ({}, {})
            )
            target_requests, pre_dispatch_traces = tail_preparation
            placeholder = (
                _placeholder_tail(protocol_result)
                if not acquisition_failure
                else None
            )
            protocol_projection = project_root_result(
                inventory=inventory,
                assembly=assembly,
                protocol_result=protocol_result,
                provider_family=provider_family,
                requested_model=requested_model,
                resolved_model=resolved_model,
                reasoning_mode=reasoning_mode,
                attempts=adapter.attempts,
                tail_summary=placeholder,
            )
            snapshot_traces = (*traces, *pre_dispatch_traces.values())
            context.run_store.write_root_protocol_snapshot(
                *_root_key(context),
                protocol_result=asdict(protocol_result),
                traces=snapshot_traces,
                tail_requests={
                    name: request.to_dict()
                    for name, request in target_requests.items()
                },
                protocol_projection=protocol_projection,
            )
            for trace in snapshot_traces:
                context.run_store.write_trace(trace)
            if not acquisition_failure and unscheduled:
                tail_summary = run_coverage_tail(
                    store=context.run_store,
                    case_id=str(inventory.case_id),
                    target_requests=target_requests,
                    submission_adapter=adapter,
                    evaluate_submission=lambda request, submission: (
                        _evaluate_tail_submission(
                            domain=str(inventory.domain),
                            artifact_store=assembly.artifact_store,
                            request=request,
                            submission=submission,
                        )
                    ),
                    clock_ms=_clock_ms,
                    protocol_result=protocol_result,
                )
            if len(adapter.outcomes) > int(context.provider_call_upper):
                raise RuntimeError("provider call cap exceeded after coverage tail")
        attempts = adapter.attempts
    else:
        adapter = assembly.submission_adapter
        provider_family = "deepseek"
        requested_model = str(inventory.configured_model)
        resolved_model = str(inventory.configured_model)
        reasoning_mode = "thinking"
        attempts = list(getattr(adapter, "attempts", ()))

    if experiment_id == "exp1":
        if protocol_projection is None:
            raise RuntimeError("Exp1 execution lacks a typed protocol projection")
        result = (
            _with_persisted_tail_attempts(
                result=protocol_projection,
                tail=tail_summary,
                store=context.run_store,
                case_id=str(inventory.case_id),
            )
            if tail_summary is not None
            else protocol_projection
        )
    else:
        result = project_root_result(
            inventory=inventory,
            assembly=assembly,
            protocol_result=protocol_result,
            provider_family=provider_family,
            requested_model=requested_model,
            resolved_model=resolved_model,
            reasoning_mode=reasoning_mode,
            attempts=attempts,
            tail_summary=tail_summary,
            scenario=scenario,
        )
    if experiment_id != "exp1":
        context.run_store.write_root_protocol_snapshot(
            *_root_key(context),
            protocol_result=asdict(protocol_result),
            traces=(),
            tail_requests={},
            protocol_projection=result,
        )
    return result


def resume_exp1_root_context(
    context: Any,
    protocol: Mapping[str, Any],
) -> RootResultV2:
    """仅凭 typed ordinary facts 恢复 protocol→result 的最后提交窗口。"""

    key = _root_key(context)
    if key[0] != "exp1":
        raise ValueError("protocol resume is only valid for Exp1")
    snapshot = context.run_store.read_root_protocol_snapshot(*key)
    if dict(protocol) != context.run_store.read_root_protocol(*key):
        raise ValueError("protocol resume input differs from the ordinary snapshot")
    for trace in snapshot.traces:
        context.run_store.write_trace(trace)
    base = snapshot.protocol_projection
    if base is None:
        raise RuntimeError("protocol snapshot lacks a typed base projection")
    summary = snapshot.protocol_result.get("summary")
    if isinstance(summary, Mapping) and coverage_tail_blocked(summary):
        base.validate()
        return base
    protocol_result = _protocol_result_from_document(snapshot.protocol_result)
    observation = protocol_result.summary.get("runtime_observation")
    targets = (
        observation.get("unscheduled_ai_unit_ids")
        if isinstance(observation, Mapping)
        else None
    )
    if not isinstance(targets, list):
        raise ValueError("protocol snapshot lacks typed tail targets")
    if not targets:
        result = base
    else:
        requests = {
            name: _execution_request_from_document(document)
            for name, document in snapshot.tail_requests.items()
        }
        resume = scan_resume(context.run_store.run_dir)
        pending = [
            name
            for name in targets
            if (str(context.inventory.case_id), 0, name)
            not in resume.trace_keys
        ]
        if pending:
            entry = _provider_entry(context)
        else:
            entry = ProviderEntryViewV1(
                provider_family="deepseek",
                entry_id=str(base.provider_entry_id),
                base_url="https://resume.invalid",
                endpoint="/not-used",
                api_key_env="SLIM_V2_RESUME_NOT_USED",
                configured_model=str(base.configured_model),
                request_overrides={},
                supports_json_mode=True,
            )
        artifact_store = ArtifactStore(
            context.run_store.system_root_directory(*key)
        )
        adapter = ProviderSubmissionAdapter(
            entry=entry,
            artifact_store=artifact_store,
            run_store=context.run_store,
            root_key=key,
            domain=str(context.inventory.domain),
            caller=_provider_caller_for_experiment("exp1"),
        )
        tail = run_coverage_tail(
            store=context.run_store,
            case_id=str(context.inventory.case_id),
            target_requests=requests,
            submission_adapter=adapter,
            evaluate_submission=lambda request, submission: (
                _evaluate_tail_submission(
                    domain=str(context.inventory.domain),
                    artifact_store=artifact_store,
                    request=request,
                    submission=submission,
                )
            ),
            clock_ms=_clock_ms,
            protocol_result=protocol_result,
        )
        result = _with_persisted_tail_attempts(
            result=base,
            tail=tail,
            store=context.run_store,
            case_id=str(context.inventory.case_id),
        )
    if (
        result.experiment_id,
        result.condition_id,
        result.case_id,
        result.repeat_id,
    ) != key:
        raise ValueError("resumed RootResultV2 identity differs from CLI context")
    result.validate()
    return result


def resume_root_context(
    context: Any,
    protocol: Mapping[str, Any],
) -> RootResultV2:
    """从普通 typed snapshot 恢复最后的 root-result 提交窗口。"""

    key = _root_key(context)
    if key[0] == "exp1":
        return resume_exp1_root_context(context, protocol)
    snapshot = context.run_store.read_root_protocol_snapshot(*key)
    if dict(protocol) != context.run_store.read_root_protocol(*key):
        raise ValueError("protocol resume input differs from the ordinary snapshot")
    projection = snapshot.protocol_projection
    if projection is None:  # pragma: no cover - storage contract rejects this first
        raise RuntimeError("protocol snapshot lacks a typed projection")
    if (
        projection.experiment_id,
        projection.condition_id,
        projection.case_id,
        projection.repeat_id,
    ) != key:
        raise ValueError("resumed RootResultV2 identity differs from CLI context")
    projection.validate()
    return projection


def _recovery_artifact_document(
    artifact_store: ArtifactStore,
    reference: object,
    *,
    name: str,
) -> Mapping[str, Any]:
    """读取已验证的普通 artifact；恢复不得依赖未绑定的内存对象。"""

    if not isinstance(reference, Mapping):
        raise ValueError(f"recovery {name} reference is malformed")
    artifact = ArtifactRef.from_dict(dict(reference))
    if not artifact_store.verify(artifact):
        raise ValueError(f"recovery {name} artifact verification failed")
    try:
        document = json.loads(artifact_store.read_bytes(artifact).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"recovery {name} artifact is unreadable") from exc
    if not isinstance(document, Mapping):
        raise ValueError(f"recovery {name} artifact must be an object")
    return document


def _pre_flash_terminal_root_facts(
    context: Any,
    assembly: RootAssembly,
) -> tuple[ProtocolRunResult, list[AttemptResultV1], object]:
    """只为唯一冻结 Exp2 terminal ledger 重新建立投影所需 typed facts。"""

    inventory = context.inventory
    key = _root_key(context)
    if (
        context.run_id != PRE_FLASH_REPRESENTATIVE_RUN_ID
        or key != PRE_FLASH_EXP2_ROOT_KEY
        or inventory.provider_entry_id != PRE_FLASH_PROVIDER_ENTRY_ID
        or inventory.configured_model != PRE_FLASH_CONFIGURED_MODEL
        or context.source_run_dir is None
        or Path(context.source_run_dir).resolve() != context.run_store.run_dir.resolve()
    ):
        raise ValueError("pre-Flash recovery context is not the exact frozen Exp2 root")
    if (
        context.run_store.root_protocol_path(*key).exists()
        or context.run_store.root_result_path(*key).exists()
    ):
        raise ValueError("pre-Flash recovery requires a root without snapshot or result")

    events = assembly.event_ledger.read_all()
    registered = [
        event for event in events if event.event_type == EventType.TASK_REGISTERED
    ]
    if len(registered) != 1:
        raise ValueError("pre-Flash recovery requires one registered task")
    task_id = registered[0].task_id
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("pre-Flash recovery task identity is malformed")

    root_snapshots = []
    child_units: dict[str, str] = {}
    for event in events:
        if event.event_type != EventType.TASK_UNIT_CREATED:
            continue
        snapshot = event.payload.get("task_unit")
        if not isinstance(snapshot, Mapping):
            raise ValueError("pre-Flash recovery unit snapshot is malformed")
        unit_id = snapshot.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("pre-Flash recovery unit identity is malformed")
        if snapshot.get("unit_type") == "root":
            root_snapshots.append(unit_id)
        planned = _planned_from_unit_snapshot(
            snapshot,
            domain=str(inventory.domain),
        )
        if planned is not None:
            if planned in child_units:
                raise ValueError("pre-Flash recovery has duplicate planned child unit")
            child_units[planned] = unit_id
    if len(root_snapshots) != 1:
        raise ValueError("pre-Flash recovery requires one root unit")
    if set(child_units) != set(inventory.planned_ai_unit_ids):
        raise ValueError("pre-Flash recovery child inventory differs from terminal ledger")

    request_events: dict[str, tuple[Any, ExecutionRequest]] = {}
    for event in events:
        if event.event_type != EventType.EXECUTION_REQUEST_RECORDED:
            continue
        document = _recovery_artifact_document(
            assembly.artifact_store,
            event.payload.get("request_ref"),
            name="request",
        )
        request = _execution_request_from_document(document)
        planned = (request.soft_hints or {}).get("planned_ai_unit_id")
        if not isinstance(planned, str) or not planned:
            continue
        if (
            request.task_id != task_id
            or request.unit_id != child_units.get(planned)
            or request.attempt_id in request_events
        ):
            raise ValueError("pre-Flash recovery request identity is malformed")
        request_events[request.attempt_id] = (event, request)
    if not request_events:
        raise ValueError("pre-Flash recovery has no fixed-source requests")

    submissions: dict[str, tuple[Any, Mapping[str, Any]]] = {}
    for event in events:
        if event.event_type != EventType.EXECUTION_SUBMISSION_RECORDED:
            continue
        attempt_id = event.payload.get("attempt_id")
        if attempt_id not in request_events:
            continue
        if attempt_id in submissions:
            raise ValueError(
                "pre-Flash recovery has duplicate terminal submission identity"
            )
        document = _recovery_artifact_document(
            assembly.artifact_store,
            event.payload.get("submission_ref"),
            name="submission",
        )
        request = request_events[attempt_id][1]
        if (
            document.get("task_id") != task_id
            or document.get("unit_id") != request.unit_id
            or document.get("attempt_id") != attempt_id
            or document.get("request_id") != request.request_id
            or document.get("result_kind") != event.payload.get("result_kind")
            or event.payload.get("unit_id") != request.unit_id
        ):
            raise ValueError("pre-Flash recovery submission identity is malformed")
        submissions[attempt_id] = (event, document)
    if set(submissions) != set(request_events):
        raise ValueError("pre-Flash recovery requests lack unique terminal submissions")

    root_started = []
    root_terminal = []
    for event in events:
        if event.event_type != EventType.TASK_UNIT_STATE_CHANGED:
            continue
        transition = event.payload.get("task_unit_state_change")
        if not isinstance(transition, Mapping) or transition.get("unit_id") != root_snapshots[0]:
            continue
        if transition.get("new_state") == "Processing":
            root_started.append(event)
        if transition.get("new_state") == "Failed":
            root_terminal.append(event)
    if len(root_started) != 1 or len(root_terminal) != 1:
        raise ValueError("pre-Flash recovery requires one terminal failed root lifecycle")

    attempts: list[AttemptResultV1] = []
    worker_facts: list[dict[str, Any]] = []
    for execution_index, (attempt_id, (request_event, request)) in enumerate(
        sorted(request_events.items(), key=lambda item: item[1][0].event_seq)
    ):
        submission_event, _submission = submissions[attempt_id]
        attempts.append(
            reconstruct_fixed_trace_attempt(
                request=request,
                source_store=RunStore(context.source_run_dir),
                artifact_store=assembly.artifact_store,
                case_id=str(inventory.case_id),
                domain=str(inventory.domain),
                provider_entry_id=str(inventory.provider_entry_id),
                configured_model=str(inventory.configured_model),
            )
        )
        worker_facts.append(
            {
                "execution_index": execution_index,
                "request_id": request.request_id,
                "submission_id": _submission.get("submission_id"),
                "result_kind": submission_event.payload.get("result_kind"),
                "unit_id": request.unit_id,
                "attempt_id": attempt_id,
                "lease_id": request.lease_id,
                "worker_id": "logical-worker-0",
                "started_at": request_event.occurred_at,
                "ended_at": submission_event.occurred_at,
            }
        )
    if any(
        not isinstance(item["submission_id"], str)
        or not item["submission_id"]
        or not isinstance(item["result_kind"], str)
        or not item["result_kind"]
        for item in worker_facts
    ):
        raise ValueError("pre-Flash recovery worker facts are malformed")

    observation = build_runtime_observation(
        run_id=assembly.run_id,
        runtime_started_at=root_started[0].occurred_at,
        runtime_ended_at=root_terminal[0].occurred_at,
        planned_ai_unit_ids=list(inventory.planned_ai_unit_ids),
        dispatched_ai_unit_ids=list(dict.fromkeys(
            str(item.planned_ai_unit_id) for item in attempts
        )),
        completed_ai_unit_ids=[],
        worker_execution_facts=worker_facts,
    )
    protocol = project_protocol_run(
        run_id=assembly.run_id,
        task_id=task_id,
        root_unit_id=root_snapshots[0],
        event_ledger=assembly.event_ledger,
        artifact_store=assembly.artifact_store,
        runtime_observation=observation,
    )
    if protocol.status != "failed":
        raise ValueError("pre-Flash recovery ledger is not terminal failed")
    logical_makespan = int(round(float(observation["runtime_wall_clock_ms"])))
    scenario = replace(
        assembly.scenario,
        logical_scheduler=SimpleNamespace(clock_ms=logical_makespan),
    )
    slots = tuple(
        {"source_child_unit_id": child_units[planned]}
        for planned in inventory.planned_ai_unit_ids
    )
    projection_assembly = replace(
        assembly,
        plugin_runtime=SimpleNamespace(
            planned_split_plan=SimpleNamespace(
                merge_plan=SimpleNamespace(required_slots=slots)
            )
        ),
        scenario=scenario,
    )
    return protocol, attempts, projection_assembly


def reproject_pre_flash_exp2_terminal_root_context(context: Any) -> RootResultV2:
    """窄恢复：仅投影唯一旧 Exp2 terminal ledger，不重跑协议或 provider。"""

    key = _root_key(context)
    if (
        context.run_id != PRE_FLASH_REPRESENTATIVE_RUN_ID
        or key != PRE_FLASH_EXP2_ROOT_KEY
        or context.source_run_dir is None
        or Path(context.source_run_dir).resolve() != context.run_store.run_dir.resolve()
    ):
        raise ValueError("pre-Flash recovery context is not the exact frozen Exp2 root")
    if (
        context.run_store.root_protocol_path(*key).exists()
        or context.run_store.root_result_path(*key).exists()
    ):
        raise ValueError("pre-Flash recovery requires a root without snapshot or result")
    events_path = context.run_store.system_root_directory(*key) / "events.jsonl"
    if not events_path.is_file() or events_path.stat().st_size == 0:
        raise ValueError("pre-Flash recovery requires a terminal ledger")
    assembly = _build_root_assembly(context)
    protocol, attempts, projection_assembly = _pre_flash_terminal_root_facts(
        context,
        assembly,
    )
    from .projector import project_root_result

    result = project_root_result(
        inventory=context.inventory,
        assembly=projection_assembly,
        protocol_result=protocol,
        provider_family="deepseek",
        requested_model=str(context.inventory.configured_model),
        resolved_model=str(context.inventory.configured_model),
        reasoning_mode="thinking",
        attempts=attempts,
        scenario=projection_assembly.scenario,
    )
    context.run_store.write_root_protocol_snapshot(
        *_root_key(context),
        protocol_result=asdict(protocol),
        traces=(),
        tail_requests={},
        protocol_projection=result,
    )
    return result


__all__ = [
    "RootAssembly", "TailSummaryV1", "coverage_tail_blocked",
    "execute_root_context",
    "materialize_protocol_traces", "resume_exp1_root_context",
    "reproject_pre_flash_exp2_terminal_root_context",
    "resume_root_context",
    "run_coverage_tail", "run_root_slice",
]
