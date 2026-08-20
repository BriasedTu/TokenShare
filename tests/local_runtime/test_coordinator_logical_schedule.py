from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import sleep

import pytest

from tokenshare.core.models import ProtocolConfig
from tokenshare.experiments.paper_runtime_clock import (
    FixedLifecycleClock,
    runtime_lifecycle_clock,
)
from tokenshare.local_runtime.contracts import (
    MergeReadinessDecision,
    ProtocolRunRequest,
)
from tokenshare.local_runtime.coordinator import ProtocolRunCoordinator
from tokenshare.local_runtime.workers import WorkerBatchOutcome, WorkerExecutionFact
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.local_runtime.test_coordinator_full_lifecycle import (
    _ArtifactExecutor,
    _Clock,
    _DirectCompletePluginRuntime,
    _ExpandedPluginRuntime,
)


def test_coordinator_commits_only_queue_popped_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
    from tokenshare.local_runtime.logical_scheduler import (
        LOGICAL_SOURCE_LATENCY_1X,
        LogicalSourceLatencyScheduler,
    )

    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="logical_schedule_test",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=0,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    executor = _ArtifactExecutor(store)
    clock = _Clock()

    class ReversedBackend:
        capacity = 2

        def __init__(self) -> None:
            self.execution_index = 0
            self.two_unit_input_order: tuple[str, ...] = ()

        def execute_batch(self, requests):
            request_batch = tuple(requests)
            if len(request_batch) == 2:
                self.two_unit_input_order = tuple(item.unit_id for item in request_batch)
            latencies = (100, 300) if len(request_batch) == 2 else (0,)
            outcomes = []
            for request, latency_ms in zip(request_batch, latencies, strict=True):
                self.execution_index += 1
                submission = executor.execute(
                    request,
                    submission_id=f"submission_{self.execution_index}",
                    submitted_at=clock(),
                )
                outcomes.append(
                    WorkerBatchOutcome(
                        request=request,
                        submission=submission,
                        fact=WorkerExecutionFact(
                            execution_index=self.execution_index,
                            request_id=request.request_id,
                            submission_id=submission.submission_id,
                            result_kind="succeeded",
                            unit_id=request.unit_id,
                            attempt_id=request.attempt_id,
                            lease_id=request.lease_id,
                            worker_id=f"reverse-{self.execution_index}",
                        ),
                        logical_completion=WorkerCompletionSchedule(
                            source_latency_ms=latency_ms,
                            attempt_ordinal=0,
                        ),
                    )
                )
            return tuple(reversed(outcomes))

    committed_unit_ids: list[str] = []
    original_record_submission = engine.record_execution_submission

    def record_submission(*args, **kwargs):
        committed_unit_ids.append(kwargs["submission"].unit_id)
        return original_record_submission(*args, **kwargs)

    monkeypatch.setattr(engine, "record_execution_submission", record_submission)
    backend = ReversedBackend()
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)

    result = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="run_logical_schedule",
            root_input={"prompt": "scheduler owns commit order"},
            plugin_runtime=_ExpandedPluginRuntime(config),
            worker_backend=backend,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    child_commits = [
        unit_id for unit_id in committed_unit_ids if unit_id in backend.two_unit_input_order
    ]
    assert result.status == "completed"
    assert child_commits == list(backend.two_unit_input_order)
    child_pop_order = [
        event.unit_id
        for event in scheduler.pop_history
        if event.unit_id in backend.two_unit_input_order
    ]
    assert child_pop_order == list(backend.two_unit_input_order)


def test_checkpoint_resume_keeps_pending_context_without_reexecuting_worker(
    tmp_path: Path,
) -> None:
    from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
    from tokenshare.local_runtime.logical_scheduler import (
        LOGICAL_SOURCE_LATENCY_1X,
        LogicalSourceLatencyScheduler,
    )
    from tokenshare.local_runtime.workers import SequentialWorkerBackend

    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="logical_resume_test",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=0,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=lambda: "2026-08-02T00:00:00Z",
        completion_schedule=lambda _request, _submission, _failure: (
            WorkerCompletionSchedule(
                source_latency_ms=125,
                attempt_ordinal=0,
            )
        ),
    )
    request = ProtocolRunRequest(
        run_id="run_logical_resume",
        root_input={"prompt": "resume queued completion"},
        plugin_runtime=_DirectCompletePluginRuntime(config),
        worker_backend=backend,
        trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
        logical_scheduler=scheduler,
    )
    first = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: "2026-08-02T00:00:00Z",
    )

    checkpoint = first.checkpoint_root(request)

    assert checkpoint.scheduler_checkpoint.clock_ms == 0
    assert checkpoint.scheduler_checkpoint.next_sequence == 1
    assert len(checkpoint.pending_executions) == 1
    assert len(backend.execution_facts) == 1
    request_count_before_resume = sum(
        event.event_type == EventType.EXECUTION_REQUEST_RECORDED
        for event in ledger.read_all()
    )

    resumed = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: "2099-01-01T00:00:00Z",
    )
    result = resumed.resume_root(request, checkpoint)

    assert result.status == "completed"
    assert len(backend.execution_facts) == 1
    assert sum(
        event.event_type == EventType.EXECUTION_REQUEST_RECORDED
        for event in ledger.read_all()
    ) == request_count_before_resume
    assert sum(
        event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        for event in ledger.read_all()
    ) == 1
    assert resumed.logical_scheduler is not scheduler
    assert resumed.logical_scheduler.clock_ms == 125
    assert resumed.logical_scheduler.next_sequence == 1
    assert [
        event.ordering_key for event in resumed.logical_scheduler.pop_history
    ] == [checkpoint.scheduler_checkpoint.pending_events[0].ordering_key]


def test_early_stop_prunes_unscheduled_sibling_only_after_queue_pop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
    from tokenshare.local_runtime.logical_scheduler import (
        LOGICAL_SOURCE_LATENCY_1X,
        LogicalSourceLatencyScheduler,
    )
    from tokenshare.local_runtime.workers import SequentialWorkerBackend

    class EarlyStopPlugin(_ExpandedPluginRuntime):
        def evaluate_merge_readiness(self, context):
            required = tuple(child.unit_id for child in context.children)
            completed = tuple(
                child.unit_id
                for child in context.children
                if child.state.value == "Completed"
            )
            if completed:
                return MergeReadinessDecision(
                    status="ready",
                    reason="first_verified_child",
                    policy_id="runtime_test.first_verified.v1",
                    policy_version="v1",
                    required_child_unit_ids=required,
                    selected_child_unit_ids=(completed[0],),
                )
            return MergeReadinessDecision(
                status="wait",
                reason="no_verified_child",
                policy_id="runtime_test.first_verified.v1",
                policy_version="v1",
                required_child_unit_ids=required,
            )

    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="logical_early_stop_test",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=0,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)

    def completion_schedule(request, _submission, _failure):
        unit_type = request.task_unit_snapshot["unit_type"]
        latency_ms = 1 if unit_type == "merge" else 0 if request.unit_id == "unit_ready" else 100
        return WorkerCompletionSchedule(
            source_latency_ms=latency_ms,
            attempt_ordinal=0,
        )

    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=scheduler.now_timestamp,
        completion_schedule=completion_schedule,
    )
    prune_pop_kinds: list[str] = []
    original_prune = engine.record_subtree_pruning

    def record_prune(*args, **kwargs):
        prune_pop_kinds.append(scheduler.pop_history[-1].event_kind)
        return original_prune(*args, **kwargs)

    monkeypatch.setattr(engine, "record_subtree_pruning", record_prune)
    result = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: "2026-08-02T00:00:00Z",
    ).run_root(
        ProtocolRunRequest(
            run_id="run_logical_early_stop",
            root_input={"prompt": "stop after first verified child"},
            plugin_runtime=EarlyStopPlugin(config),
            worker_backend=backend,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert len(backend.execution_facts) == 3
    assert prune_pop_kinds == ["early_stop"]
    assert scheduler.pop_history[-2].event_kind == "worker_completion"
    assert scheduler.pop_history[-1].event_kind == "early_stop"
    assert scheduler.pop_history[-2].logical_time_ms == 101
    assert scheduler.pop_history[-1].logical_time_ms == 101
    assert result.summary["unit_state_counts"]["Cancelled"] == 1
    assert any(
        event.event_type == EventType.SUBTREE_PRUNED
        for event in ledger.read_all()
    )


def test_plugin_ready_does_not_prune_repeated_verification_requeue(
    tmp_path: Path,
) -> None:
    from tokenshare.core.verification import build_verification_report
    from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
    from tokenshare.local_runtime.logical_scheduler import (
        LOGICAL_SOURCE_LATENCY_1X,
        LogicalSourceLatencyScheduler,
    )
    from tokenshare.local_runtime.workers import ThreadWorkerBackend

    target_unit_id: str | None = None

    class RetryBeforeEarlyStopPlugin(_ExpandedPluginRuntime):
        def __init__(self, config: ProtocolConfig) -> None:
            super().__init__(config)
            self.target_rejections = 0

        def evaluate_merge_readiness(self, context):
            required = tuple(child.unit_id for child in context.children)
            completed = tuple(
                child.unit_id
                for child in context.children
                if child.state.value == "Completed"
            )
            return MergeReadinessDecision(
                status="ready" if completed else "wait",
                reason="first_verified_child" if completed else "no_verified_child",
                policy_id="runtime_test.first_verified.v1",
                policy_version="v1",
                required_child_unit_ids=required,
                selected_child_unit_ids=(completed[:1] if completed else ()),
            )

        def verify_submission(self, submission, *, unit):
            report = super().verify_submission(submission, unit=unit)
            if unit.unit_id != target_unit_id or self.target_rejections >= 2:
                return report
            self.target_rejections += 1
            return build_verification_report(
                verification_report_id=report.verification_report_id,
                task_id=submission.task_id,
                unit_id=submission.unit_id,
                attempt_id=submission.attempt_id,
                submission_id=submission.submission_id,
                submission_event_seq=1,
                candidate_output_refs=submission.candidate_output_refs,
                required_output_names=["answer"],
                output_contract_id="contract_answer",
                validator_policy_id="structured_report_stub_validator_v1",
                plugin_id=self.descriptor.plugin_id,
                plugin_version=self.descriptor.plugin_version,
                plugin_descriptor_digest=self.descriptor.descriptor_digest,
                status="rejected",
                expected_artifact_hashes={
                    name: ref.content_hash
                    for name, ref in submission.candidate_output_refs.items()
                },
                required_evidence_ref_ids=[],
                available_evidence_ref_ids=[],
                plugin_domain_status="rejected",
                audit_status="passed",
                verification_environment={"runtime": "pytest"},
                verifier={"verifier_id": "runtime_spy", "verifier_version": "1"},
                started_at=submission.submitted_at,
                completed_at=submission.submitted_at,
            )

    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="logical_recovery_before_merge_test",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=2,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    scheduler = LogicalSourceLatencyScheduler(start_ms=0)

    def completion_schedule(request, _submission, _failure):
        nonlocal target_unit_id
        is_child = request.task_unit_snapshot.get("parent_unit_id") == "unit_ready"
        if not is_child:
            latency_ms = 1
        else:
            if target_unit_id is None:
                target_unit_id = request.unit_id
            latency_ms = 200 if request.unit_id == target_unit_id else 100
        return WorkerCompletionSchedule(
            source_latency_ms=latency_ms,
            attempt_ordinal=request.attempt_ordinal,
        )

    plugin = RetryBeforeEarlyStopPlugin(config)
    backend = ThreadWorkerBackend(
        executor=_ArtifactExecutor(store),
        capacity=2,
        submitted_at=scheduler.now_timestamp,
        completion_schedule=completion_schedule,
    )
    result = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=lambda: "2026-08-02T00:00:00Z",
    ).run_root(
        ProtocolRunRequest(
            run_id="run_logical_recovery_before_merge",
            root_input={"prompt": "finish accepted requeue before early stop"},
            plugin_runtime=plugin,
            worker_backend=backend,
            continue_after_terminal_child_failure=True,
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=scheduler,
        )
    )

    assert result.status == "completed"
    assert target_unit_id is not None
    assert plugin.target_rejections == 2
    events = ledger.read_all()
    target_recoveries = [
        event
        for event in events
        if event.event_type == EventType.RECOVERY_ACTION_RECORDED
        and event.payload["recovery_action"]["unit_id"] == target_unit_id
        and event.payload["recovery_action"]["retry_allowed"] is True
    ]
    assert len(target_recoveries) == 2
    target_completed_seq = next(
        event.event_seq
        for event in events
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.object_id == target_unit_id
        and event.payload["task_unit_state_change"]["new_state"] == "Completed"
    )
    merge_seq = next(
        event.event_seq
        for event in events
        if event.event_type == EventType.MERGE_TASK_LINK_RECORDED
    )
    assert target_completed_seq < merge_seq
    assert not any(
        event.event_type == EventType.SUBTREE_PRUNED
        and target_unit_id in event.payload.get("cancelled_unit_ids", ())
        for event in events
    )


def test_trace_policy_rejects_real_sleep_and_noop_sleeper() -> None:
    for sleeper in (sleep, lambda _seconds: None):
        with pytest.raises(ValueError, match="logical_source_latency_1x.*sleeper"):
            runtime_lifecycle_clock(
                real_transport=False,
                transport=object(),
                deterministic_now="2026-08-02T00:00:00Z",
                trace_delay_policy="logical_source_latency_1x",
                sleeper=sleeper,
            )


def test_online_policy_uses_real_clock_not_logical() -> None:
    wall_values = iter(("2026-08-02T00:00:01Z", "2026-08-02T00:00:02Z"))
    monotonic_values = iter((3.5, 4.0))
    clock = runtime_lifecycle_clock(
        real_transport=True,
        transport=object(),
        deterministic_now="2026-08-02T00:00:00Z",
        trace_delay_policy="online_real_time",
        wall_clock=lambda: next(wall_values),
        monotonic_clock=lambda: next(monotonic_values),
    )

    assert not isinstance(clock, FixedLifecycleClock)
    assert clock() == "2026-08-02T00:00:01Z"
    assert clock.monotonic_seconds() == 3.5
