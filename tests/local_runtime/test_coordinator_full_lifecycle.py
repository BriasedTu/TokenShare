from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tokenshare.local_runtime import (
    CanonicalUnitContext,
    CompleteAction,
    ExpandAction,
    MergeAction,
    MergeExecutionContext,
    MergeResolutionAction,
    ProtocolMechanismPolicy,
    ProtocolRunLedgerBinding,
    ProtocolRunCoordinator,
    ProtocolRunRequest,
    RootProtocolPlan,
    SequentialWorkerBackend,
    project_protocol_run,
)
from tokenshare.local_runtime.contracts import WorkerCompletionSchedule
from tokenshare.local_runtime.coordinator import _elapsed_utc_ms
from tokenshare.local_runtime.logical_scheduler import (
    LOGICAL_SOURCE_LATENCY_1X,
    LogicalSourceLatencyScheduler,
)
from tokenshare.core.expansion import ExpansionDecision, SplitStrategyInvocation
from tokenshare.core.merge import ExpectedOutputResolution, MergeRecord
from tokenshare.core.merge_coordinator import MergeCoordinator
from tokenshare.core.models import ClientRecord, ProtocolConfig, TaskUnit
from tokenshare.core.registration import RootTaskRegistrationRequest
from tokenshare.core.verification import build_verification_report, digest_json
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.plugins.registry import PluginRegistry
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tests.phase3_fixtures import (
    make_environment_ref,
    make_executor_descriptor,
    make_output_contract,
    make_plugin_descriptor,
)
from tests.retained_fixtures import (
    PARAMS_DIGEST,
    SCOPE_HASH,
    STRATEGY_ID,
    _expand_decision,
    _expected_child_unit_ids,
    _merge_plan,
    _merge_plan_body_digest,
    _proposal,
    _proposal_body_digest,
    _split_invocation,
)


@pytest.mark.parametrize(
    ("end", "expected_ms"),
    [
        ("2026-08-23T09:00:31Z", 299500),
        ("2026-08-23T09:00:31.500126Z", 300000),
    ],
)
def test_elapsed_utc_ms_rounds_lease_deadline_up(
    end: str,
    expected_ms: int,
) -> None:
    assert _elapsed_utc_ms("2026-08-23T08:55:31.500126Z", end) == expected_ms


@dataclass(frozen=True)
class _RequestIdentity:
    request_id: str
    attempt_id: str


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, str]] = []

    def execute(self, request, *, submission_id: str, submitted_at: str):
        self.calls.append((request, submission_id, submitted_at))
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=getattr(request, "request_id", "request_test"),
            task_id=getattr(request, "task_id", "task_test"),
            unit_id=getattr(request, "unit_id", "unit_test"),
            attempt_id=getattr(request, "attempt_id", "attempt_test"),
            lease_id=getattr(request, "lease_id", "lease_test"),
            fencing_token=getattr(request, "fencing_token", "fencing_test"),
            executor_id="executor_test",
            executor_version="0.1.0",
            result_kind="succeeded",
            raw_output_ref=None,
            parsed_output_ref=None,
            candidate_output_refs={},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=make_environment_ref(),
            environment_summary={"runtime": "pytest"},
            provenance_ref=None,
            usage_summary={"calls": 0},
            error=None,
            submitted_at=submitted_at,
        )


def test_sequential_backend_has_one_worker_and_only_reports_execution_fact() -> None:
    executor = _Executor()
    backend = SequentialWorkerBackend(
        executor=executor,
        submitted_at=lambda: "2026-07-22T00:00:01Z",
    )
    request = object()

    submission = backend.execute(request)

    assert backend.capacity == 1
    assert submission.submission_id == "submission_000001"
    assert executor.calls == [
        (request, "submission_000001", "2026-07-22T00:00:01Z")
    ]
    assert backend.execution_facts[0].submission_id == submission.submission_id
    assert not hasattr(backend, "requeue")


def test_sequential_backend_derives_windows_safe_submission_id_from_attempt() -> None:
    first = SequentialWorkerBackend(
        executor=_Executor(),
        submitted_at=lambda: "2026-07-22T00:00:01Z",
    ).execute(
        _RequestIdentity(
            request_id="request_run:a",
            attempt_id="run:a/attempt:1",
        )
    )
    second = SequentialWorkerBackend(
        executor=_Executor(),
        submitted_at=lambda: "2026-07-22T00:00:01Z",
    ).execute(
        _RequestIdentity(
            request_id="request_run?a",
            attempt_id="run?a/attempt:1",
        )
    )

    assert first.submission_id != second.submission_id
    assert all(
        character not in first.submission_id + second.submission_id
        for character in '<>:"/\\|?*'
    )


def test_runtime_exports_coordinator_and_reference_projection() -> None:
    assert ProtocolRunCoordinator.__name__ == "ProtocolRunCoordinator"
    assert callable(project_protocol_run)


class _Clock:
    def __init__(self) -> None:
        self._value = datetime(2026, 7, 22, tzinfo=UTC)

    def __call__(self) -> str:
        value = self._value
        self._value += timedelta(seconds=1)
        return value.isoformat().replace("+00:00", "Z")


class _ObservationClock:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class _ArtifactExecutor:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        output_ref = self._store.save_json(
            {"unit_id": request.unit_id, "answer": "accepted"},
            artifact_id=f"answer_{request.attempt_id}",
            artifact_type="canonical_output",
            artifact_schema_id="tokenshare.runtime_test.answer",
            artifact_schema_version="v1",
            source={"kind": "runtime_test_executor"},
            metadata={"output_name": "answer"},
            created_at=submitted_at,
        )
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id="executor_mock_ai",
            executor_version="0.1.0",
            result_kind="succeeded",
            raw_output_ref=output_ref,
            parsed_output_ref=output_ref,
            candidate_output_refs={"answer": output_ref},
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={"runtime": "pytest"},
            provenance_ref=None,
            usage_summary={"calls": 1},
            error=None,
            submitted_at=submitted_at,
        )


def _fixed_completion_schedule(
    _request,
    _submission,
    _failure_kind,
) -> WorkerCompletionSchedule:
    return WorkerCompletionSchedule(source_latency_ms=10, attempt_ordinal=0)


class _ExpandedPluginRuntime:
    def __init__(self, config: ProtocolConfig) -> None:
        self.config = config
        self.descriptor = make_plugin_descriptor()
        self.executor_descriptor = make_executor_descriptor()
        self.verify_calls: list[str] = []
        self.merge_calls = 0
        self.merge_slot_integrity_policies: list[bool] = []
        self.clear_dependency_edges = True

    def plan_root(self, root_input: object, *, artifact_store: ArtifactStore):
        del artifact_store
        plugin_registry = PluginRegistry()
        plugin_registry.register(self.descriptor)
        executor_registry = ExecutorRegistry()
        executor_registry.register(self.executor_descriptor)
        return RootProtocolPlan(
            registration_request=RootTaskRegistrationRequest(
                task_id="task_demo",
                root_unit_id="unit_ready",
                root_artifact_id="artifact_root_input",
                description="runtime expanded lifecycle",
                plugin_id=self.descriptor.plugin_id,
                plugin_version=self.descriptor.plugin_version,
                split_strategy_id=STRATEGY_ID,
                split_strategy_params={"mode": "two_sections"},
                root_input_bytes=json.dumps(root_input, sort_keys=True).encode("utf-8"),
                root_input_media_type="application/json",
                root_input_schema_id="tokenshare.runtime_test.root",
                root_input_schema_version="v1",
                protocol_config=self.config,
                required_capabilities={"executor": "mock_ai"},
                plugin_payload={},
                metadata={"run": "expanded"},
                created_at="2026-07-22T00:00:00Z",
                root_budget=10,
            ),
            plugin_registry=plugin_registry,
            executor_registry=executor_registry,
            registry_snapshot_id="registry_snapshot_runtime",
            clients=(
                ClientRecord(
                    client_id="client_local",
                    executor_type="mock_ai",
                    executor_id="executor_mock_ai",
                    executor_version="0.1.0",
                    capabilities={"executor": ["mock_ai", "local"]},
                    status="active",
                    stats={},
                    metadata={},
                    registered_at="2026-07-22T00:00:00Z",
                ),
            ),
            canonical_action_builder=self._canonical_action,
            settlement_policy_id="sandbox_equal_weight_v1",
        )

    def build_execution_request(self, unit, *, attempt, lease):
        return ExecutionRequest(
            request_id=f"request_{attempt.attempt_id}",
            task_id=unit.task_id,
            unit_id=unit.unit_id,
            attempt_id=attempt.attempt_id,
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
            plugin=self.descriptor.to_dict(),
            executor=self.executor_descriptor.to_dict(),
            registry_snapshot_id="registry_snapshot_runtime",
            allocation_decision={"client_id": attempt.client_id},
            capability_snapshot=dict(unit.required_capabilities),
            task_unit_snapshot=unit.to_dict(),
            input_artifact_refs=dict(unit.input_refs),
            output_contract=make_output_contract(),
            hard_requirements={"executor": "mock_ai"},
            soft_hints={},
            environment_ref=make_environment_ref(),
            execution_instruction_ref=None,
            prompt_package_ref=None,
            limits={},
            created_at=attempt.started_at or attempt.created_at,
        )

    def verify_submission(self, submission, *, unit):
        self.verify_calls.append(unit.unit_id)
        return build_verification_report(
            verification_report_id=f"verification_{submission.attempt_id}",
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
            status="passed",
            expected_artifact_hashes={
                name: ref.content_hash
                for name, ref in submission.candidate_output_refs.items()
            },
            required_evidence_ref_ids=[],
            available_evidence_ref_ids=[],
            plugin_domain_status="passed",
            audit_status="passed",
            verification_environment={"runtime": "pytest"},
            verifier={"verifier_id": "runtime_spy", "verifier_version": "1"},
            started_at=submission.submitted_at,
            completed_at=submission.submitted_at,
        )

    def build_merge(
        self,
        *,
        parent,
        canonical_children,
        slot_integrity_enabled: bool = True,
    ):
        self.merge_calls += 1
        self.merge_slot_integrity_policies.append(slot_integrity_enabled)
        assert parent.unit_id == "unit_ready"
        # build_merge 只接收 readiness decision 选中的 merge input children。
        assert len(canonical_children) == 1
        return MergeAction(resolution_builder=self._merge_resolution)

    def _canonical_action(self, context: CanonicalUnitContext):
        if context.unit.unit_id == "unit_ready":
            return self._expand_action(context)
        return self._complete_action(context)

    def _expand_action(self, context: CanonicalUnitContext) -> ExpandAction:
        canonical = context.canonical_selection
        proposal = _proposal(
            proposal_id="decomposition_proposal_pending",
            proposal_digest="sha256:pending_proposal_digest",
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=STRATEGY_ID,
            split_strategy_params_digest=PARAMS_DIGEST,
        )
        if self.clear_dependency_edges:
            # Task 3 只验证 sequential lifecycle；依赖解阻 API 不在本 Task 范围。
            proposal.dependency_edges.clear()
        proposal_digest = _proposal_body_digest(proposal)
        proposal_id = f"decomposition_proposal_{proposal_digest.removeprefix('sha256:')}"
        proposal.proposal_header["proposal_id"] = proposal_id
        proposal.proposal_header["proposal_digest"] = proposal_digest
        child_ids = _expected_child_unit_ids(
            proposal_digest=proposal_digest,
            parent_unit_id=context.unit.unit_id,
        )
        merge_plan = _merge_plan(
            merge_plan_id="merge_plan_pending",
            merge_plan_digest="sha256:pending_merge_plan_digest",
            proposal_id=proposal_id,
            decision_id=f"expansion_decision:{SCOPE_HASH}",
            canonical_selection_id=canonical.canonical_selection_id,
            child_unit_ids_by_key=child_ids,
        )
        merge_digest = _merge_plan_body_digest(merge_plan)
        merge_plan_id = f"merge_plan_{merge_digest.removeprefix('sha256:')}"
        merge_plan.merge_plan_header["merge_plan_id"] = merge_plan_id
        merge_plan.merge_plan_header["merge_plan_digest"] = merge_digest
        invocation = _split_invocation(
            canonical_selection=canonical,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            status="succeeded",
        )
        decision = _expand_decision(
            canonical_selection=canonical,
            canonical_selection_id=canonical.canonical_selection_id,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            source_invocation_id=invocation.invocation_id,
            proposal_id=proposal_id,
            proposal_digest=proposal_digest,
            merge_plan_id=merge_plan_id,
            merge_plan_digest=merge_digest,
            split_strategy_id=STRATEGY_ID,
            split_strategy_params_digest=PARAMS_DIGEST,
        )
        decision = replace(
            decision,
            action_body={
                "expand_evidence": {
                    **decision.action_body["expand_evidence"],
                    "relation_count": len(proposal.dependency_edges),
                }
            },
        )
        return ExpandAction(
            invocation=invocation,
            decision=decision,
            proposal=proposal,
            merge_plan=merge_plan,
        )

    def _complete_action(self, context: CanonicalUnitContext) -> CompleteAction:
        canonical = context.canonical_selection
        suffix = context.unit.metadata.get("child_logical_key", "root")
        scope = f"sha256:scope_{suffix}"
        invocation = SplitStrategyInvocation(
            invocation_id=f"split_invocation:{scope}:attempt:1",
            invocation_attempt_no=1,
            expansion_scope_hash=scope,
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            plugin_id=self.descriptor.plugin_id,
            plugin_version=self.descriptor.plugin_version,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=STRATEGY_ID,
            split_strategy_params_digest=PARAMS_DIGEST,
            status="succeeded",
            result_action="complete",
            result_digest=f"sha256:complete_{suffix}",
            started_at=canonical.bound_at,
            completed_at=canonical.bound_at,
        )
        decision = ExpansionDecision(
            expansion_decision_id=f"expansion_decision:{scope}",
            task_id=context.unit.task_id,
            unit_id=context.unit.unit_id,
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_output_bundle_digest=canonical.canonical_output_bundle_digest,
            expansion_scope_hash=scope,
            action="complete",
            plugin_id=self.descriptor.plugin_id,
            plugin_version=self.descriptor.plugin_version,
            plugin_descriptor_digest=self.descriptor.descriptor_digest,
            split_strategy_id=STRATEGY_ID,
            split_strategy_params_digest=PARAMS_DIGEST,
            source_invocation_id=invocation.invocation_id,
            action_body={
                "completion_evidence": {
                    "completion_kind": "runtime_test_child",
                    "validator_policy_id": "structured_report_stub_validator_v1",
                    "verification_report_id": canonical.selected_verification_report_id,
                    "canonical_selection_id": canonical.canonical_selection_id,
                    "canonical_output_bundle_digest": canonical.canonical_output_bundle_digest,
                    "completed_output_refs": {
                        name: ref.to_dict()
                        for name, ref in canonical.canonical_output_refs.items()
                    },
                    "plugin_completion_summary": "child accepted",
                }
            },
            decided_at=canonical.bound_at,
        )
        return CompleteAction(invocation=invocation, decision=decision)

    def _merge_resolution(
        self,
        context: MergeExecutionContext,
    ) -> MergeResolutionAction:
        canonical = context.canonical_selection
        link = context.merge_task_link
        merge_record = MergeRecord(
            merge_record_id=(
                f"merge_record:{link.merge_plan_id}:"
                f"{link.merge_unit_id}:{canonical.canonical_selection_id}"
            ),
            task_id=link.task_id,
            parent_unit_id=link.parent_unit_id,
            merge_plan_id=link.merge_plan_id,
            merge_unit_id=link.merge_unit_id,
            merge_task_link_id=link.merge_task_link_id,
            merge_input_bundle_ref=link.merge_input_bundle_ref,
            merge_input_bundle_digest=link.merge_input_bundle_digest,
            required_slot_bindings_digest=link.required_slot_bindings_digest,
            merge_policy_id=link.merge_policy_id,
            merge_policy_version=link.merge_policy_version,
            merge_policy_descriptor_digest=link.merge_policy_descriptor_digest,
            merge_policy_params_digest=context.merge_plan.merge_policy_ref[
                "merge_policy_params_digest"
            ],
            canonical_selection_id=canonical.canonical_selection_id,
            canonical_event_seq=context.canonical_event.event_seq,
            selected_verification_report_id=canonical.selected_verification_report_id,
            selected_verification_event_seq=canonical.selected_verification_event_seq,
            selected_submission_id=canonical.selected_submission_id,
            selected_submission_event_seq=canonical.selected_submission_event_seq,
            selected_attempt_id=canonical.selected_attempt_id,
            merge_output_bundle_digest=canonical.canonical_output_bundle_digest,
            merge_output_refs={
                name: ref.to_dict()
                for name, ref in canonical.canonical_output_refs.items()
            },
            parent_output_mapping_digest=digest_json(
                context.merge_plan.parent_output_mapping
            ),
            created_at=canonical.bound_at,
        )
        expected = context.expected_output_refs[0]
        output_ref = canonical.canonical_output_refs["answer"]
        resolution = ExpectedOutputResolution(
            expected_output_resolution_id=(
                f"expected_output_resolved:{expected.expected_output_id}:"
                f"{merge_record.merge_record_id}"
            ),
            task_id=merge_record.task_id,
            owner_unit_id=merge_record.parent_unit_id,
            expected_output_id=expected.expected_output_id,
            expected_output_name=expected.output_name,
            resolution_source_type="merge_record",
            merge_record_id=merge_record.merge_record_id,
            merge_plan_id=merge_record.merge_plan_id,
            merge_unit_id=merge_record.merge_unit_id,
            merge_canonical_selection_id=merge_record.canonical_selection_id,
            resolved_output_ref=output_ref.to_dict(),
            resolved_output_digest=output_ref.content_hash,
            resolved_at=canonical.bound_at,
        )
        return MergeResolutionAction(
            merge_record=merge_record,
            expected_output_resolutions=(resolution,),
        )


class _DirectCompletePluginRuntime(_ExpandedPluginRuntime):
    def _canonical_action(self, context: CanonicalUnitContext):
        return self._complete_action(context)


class _DependencyBlockedPluginRuntime(_ExpandedPluginRuntime):
    def __init__(self, config: ProtocolConfig) -> None:
        super().__init__(config)
        self.clear_dependency_edges = False


@pytest.mark.parametrize("slot_integrity_enabled", (True, False))
def test_coordinator_runs_expand_children_merge_completion_and_settlement_through_engine(
    tmp_path: Path,
    monkeypatch,
    slot_integrity_enabled: bool,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_test_config",
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
    merge_canonical_task_ids: list[str | None] = []
    original_merge_creation = MergeCoordinator.create_ready_merge_tasks

    def record_merge_inputs(self, *args, **kwargs):
        merge_canonical_task_ids.extend(
            event.task_id for event in kwargs["canonical_events"]
        )
        return original_merge_creation(self, *args, **kwargs)

    monkeypatch.setattr(
        MergeCoordinator,
        "create_ready_merge_tasks",
        record_merge_inputs,
    )
    calls: list[str] = []
    watched = (
        "schedule_ready_unit",
        "record_execution_request",
        "record_execution_submission",
        "record_verification",
        "bind_canonical_outputs",
        "record_split_strategy_invocation",
        "record_expand_decision",
        "record_complete_decision",
        "record_merge_resolution",
        "record_parent_completion",
        "record_root_settlement",
    )
    for name in watched:
        original = getattr(engine, name)

        def recording(*args, __name=name, __original=original, **kwargs):
            calls.append(__name)
            recorded = __original(*args, **kwargs)
            if (
                __name == "bind_canonical_outputs"
                and calls.count("bind_canonical_outputs") == 3
            ):
                ledger.append(
                    event_type=EventType.CANONICAL_OUTPUTS_BOUND,
                    object_type="CanonicalSelection",
                    object_id=recorded.canonical_selection.unit_id,
                    task_id="task_foreign",
                    actor={"kind": "runtime_test_foreign_task"},
                    correlation_id="corr_foreign_canonical",
                    idempotency_key="foreign_canonical_same_unit_id",
                    payload={
                        "canonical_selection": {
                            "unit_id": recorded.canonical_selection.unit_id,
                        }
                    },
                    occurred_at="2026-07-22T00:00:00Z",
                )
            return recorded

        monkeypatch.setattr(engine, name, recording)

    plugin = _ExpandedPluginRuntime(config)
    clock = _Clock()
    logical_scheduler = LogicalSourceLatencyScheduler(start_ms=0)
    backend = SequentialWorkerBackend(
        executor=_ArtifactExecutor(store),
        submitted_at=logical_scheduler.now_timestamp,
        completion_schedule=lambda _request, _submission, _failure: (
            WorkerCompletionSchedule(
                source_latency_ms=10,
                attempt_ordinal=0,
            )
        ),
    )
    coordinator = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    )

    result = coordinator.run_root(
        ProtocolRunRequest(
            run_id="run_expanded",
            root_input={"prompt": "split then merge"},
            plugin_runtime=plugin,
            worker_backend=backend,
            mechanism_policy=ProtocolMechanismPolicy(
                slot_integrity_enabled=slot_integrity_enabled
            ),
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=logical_scheduler,
        )
    )

    assert result.status == "completed"
    assert calls.count("schedule_ready_unit") == 4
    assert calls.count("record_execution_request") == 4
    assert calls.count("record_execution_submission") == 4
    assert calls.count("record_verification") == 4
    assert calls.count("bind_canonical_outputs") == 4
    assert calls.count("record_split_strategy_invocation") == 3
    assert calls.count("record_expand_decision") == 1
    assert calls.count("record_complete_decision") == 2
    assert calls.count("record_merge_resolution") == 1
    assert calls.count("record_parent_completion") == 1
    assert calls.count("record_root_settlement") == 1
    assert set(merge_canonical_task_ids) == {"task_demo"}
    assert plugin.merge_calls == 1
    assert plugin.merge_slot_integrity_policies == [slot_integrity_enabled]
    assert len(plugin.verify_calls) == 4
    assert result.summary["runtime_observation"]["runtime_wall_clock_ms"] == 40.0
    assert [event.logical_time_ms for event in logical_scheduler.pop_history] == [
        10,
        20,
        30,
        40,
    ]

    event_types = [event.event_type for event in ledger.read_all()]
    assert EventType.TASK_EXPANDED in event_types
    assert EventType.MERGE_RECORDED in event_types
    assert EventType.SETTLEMENT_RECORDED in event_types
    assert all(set(ref) <= {"event_id", "event_seq", "event_type"} for ref in result.event_refs)
    assert result.artifact_refs
    assert result.ledger_binding == ProtocolRunLedgerBinding.from_verified_snapshot(
        run_id=result.run_id,
        task_id=result.task_id,
        root_unit_id=result.root_unit_id,
        verified_snapshot=ledger.read_verified_snapshot(),
    )
    assert result.summary["unit_state_counts"]["Completed"] == 3
    assert result.summary["unit_state_counts"]["Processing"] == 1
    projected = project_protocol_run(
        run_id="run_expanded",
        task_id="task_demo",
        root_unit_id="unit_ready",
        event_ledger=ledger,
        artifact_store=store,
        runtime_observation=result.summary["runtime_observation"],
    )
    assert projected == result

    corrupted_ref = result.artifact_refs[0]
    (store.root_path / corrupted_ref.uri).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="artifact reference verification failed"):
        project_protocol_run(
            run_id="run_expanded",
            task_id="task_demo",
            root_unit_id="unit_ready",
            event_ledger=ledger,
            artifact_store=store,
        )


def test_coordinator_direct_root_complete_records_settlement_without_expansion(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_direct.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_direct_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_direct.jsonl",
        ),
        max_retries=2,
    )
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=config,
        artifact_store=store,
    )
    plugin = _DirectCompletePluginRuntime(config)
    clock = _Clock()

    result = ProtocolRunCoordinator(
        engine=engine,
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
        observation_clock=_ObservationClock(
            "2026-07-24T00:00:00Z",
            "2026-07-24T00:00:01.250000Z",
        ),
    ).run_root(
        ProtocolRunRequest(
            run_id="run_direct_complete",
            root_input={"prompt": "complete directly"},
            plugin_runtime=plugin,
            worker_backend=SequentialWorkerBackend(
                executor=_ArtifactExecutor(store),
                submitted_at=clock,
            ),
        )
    )

    assert result.status == "completed"
    assert result.summary["unit_state_counts"] == {"Completed": 1}
    observation = result.summary["runtime_observation"]
    assert observation["schema_version"] == "tokenshare.protocol_runtime_observation.v1"
    assert observation["runtime_started_at"] == "2026-07-24T00:00:00Z"
    assert observation["runtime_ended_at"] == "2026-07-24T00:00:01.250000Z"
    assert observation["runtime_wall_clock_ms"] == 1250.0
    assert len(observation["worker_execution_facts"]) == 1
    assert observation["worker_execution_facts"][0]["unit_id"] == "unit_ready"
    assert observation["worker_execution_facts"][0]["attempt_id"]
    assert observation["worker_execution_facts"][0]["worker_id"] == (
        "sequential-worker-1"
    )
    event_types = [event.event_type for event in ledger.read_all()]
    assert EventType.SETTLEMENT_RECORDED in event_types
    assert EventType.TASK_EXPANDED not in event_types
    assert plugin.merge_calls == 0


def test_checkpoint_round_trip_resumes_plain_submission_to_equivalent_terminal_state(
    tmp_path: Path,
) -> None:
    def build_runtime(root: Path):
        store = ArtifactStore(root)
        ledger = EventLedger(root / "events" / "task_checkpoint.jsonl")
        config = replace(
            ProtocolConfig.default(
                config_id="runtime_checkpoint_config",
                artifact_store_uri="file://artifacts",
                event_log_uri="file://events/task_checkpoint.jsonl",
            ),
            max_retries=2,
        )
        engine = ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        )
        logical_scheduler = LogicalSourceLatencyScheduler(start_ms=0)
        clock = _Clock()
        request = ProtocolRunRequest(
            run_id="run_checkpoint_plain_submission",
            root_input={"prompt": "complete directly"},
            plugin_runtime=_DirectCompletePluginRuntime(config),
            worker_backend=SequentialWorkerBackend(
                executor=_ArtifactExecutor(store),
                submitted_at=logical_scheduler.now_timestamp,
                completion_schedule=_fixed_completion_schedule,
            ),
            trace_delay_policy=LOGICAL_SOURCE_LATENCY_1X,
            logical_scheduler=logical_scheduler,
        )
        coordinator = ProtocolRunCoordinator(
            engine=engine,
            artifact_store=store,
            event_ledger=ledger,
            now=clock,
            observation_clock=_ObservationClock(
                "2026-07-24T00:00:00Z",
                "2026-07-24T00:00:00.010000Z",
            ),
        )
        return coordinator, request, ledger

    baseline_coordinator, baseline_request, baseline_ledger = build_runtime(
        tmp_path / "baseline"
    )
    baseline = baseline_coordinator.run_root(baseline_request)

    resumed_coordinator, resumed_request, resumed_ledger = build_runtime(
        tmp_path / "resumed"
    )
    checkpoint = resumed_coordinator.checkpoint_root(resumed_request)
    checkpoint_bytes = pickle.dumps(checkpoint)
    restored_checkpoint = pickle.loads(checkpoint_bytes)

    assert "trace_delivery_attempts" not in checkpoint.__dataclass_fields__
    assert b"trace_delivery_attempts" not in checkpoint_bytes
    assert b"trace_consumption" not in checkpoint_bytes

    resumed = resumed_coordinator.resume_root(
        restored_checkpoint.request_context,
        restored_checkpoint,
    )

    assert resumed.status == baseline.status == "completed"
    assert resumed.summary["unit_state_counts"] == baseline.summary[
        "unit_state_counts"
    ]
    assert resumed.summary["attempt_state_counts"] == baseline.summary[
        "attempt_state_counts"
    ]
    assert [event.event_type for event in resumed_ledger.read_all()] == [
        event.event_type for event in baseline_ledger.read_all()
    ]
    summary_json = json.dumps(resumed.summary, sort_keys=True)
    assert "trace_delivery" not in summary_json
    assert "trace_consumption" not in summary_json


def test_task5_runtime_activates_blocked_unit_after_dependency_is_canonical(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_blocked.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_blocked_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_blocked.jsonl",
        ),
        max_retries=2,
    )
    clock = _Clock()
    plugin = _DependencyBlockedPluginRuntime(config)

    result = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="run_dependency_blocked",
            root_input={"prompt": "dependency becomes ready"},
            plugin_runtime=plugin,
            worker_backend=SequentialWorkerBackend(
                executor=_ArtifactExecutor(store),
                submitted_at=clock,
            ),
        )
    )

    assert result.status == "completed"
    assert plugin.merge_calls == 1
    events = ledger.read_all()
    activation_events = [
        event
        for event in events
        if event.event_type == EventType.TASK_UNIT_STATE_CHANGED
        and event.payload["task_unit_state_change"]["trigger"]
        == "dependency_resolution"
    ]
    assert len(activation_events) == 1
    activation = activation_events[0].payload["task_unit_state_change"]
    assert activation["old_state"] == "Blocked"
    assert activation["new_state"] == "Ready"
    assert activation["state_context"]["dependency_source_unit_ids"]
    assert activation["state_context"]["dependency_output_refs"]
    assert any(
        event.event_type == EventType.SETTLEMENT_RECORDED for event in events
    )


def test_long_ascii_run_id_remains_windows_safe_end_to_end(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_long_run.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="runtime_long_run_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_long_run.jsonl",
        ),
        max_retries=2,
    )
    clock = _Clock()

    result = ProtocolRunCoordinator(
        engine=ProtocolEngine(
            event_ledger=ledger,
            protocol_config=config,
            artifact_store=store,
        ),
        artifact_store=store,
        event_ledger=ledger,
        now=clock,
    ).run_root(
        ProtocolRunRequest(
            run_id="r" * 300,
            root_input={"prompt": "long run id"},
            plugin_runtime=_DirectCompletePluginRuntime(config),
            worker_backend=SequentialWorkerBackend(
                executor=_ArtifactExecutor(store),
                submitted_at=clock,
            ),
        )
    )

    assert result.status == "completed"
    assert all(len(ref.artifact_id) < 128 for ref in result.artifact_refs)


def test_projection_rejects_partial_artifact_reference(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "partial_ref.jsonl")
    ledger.append(
        event_type="RUNTIME_TEST_PARTIAL_ARTIFACT",
        object_type="RuntimeTest",
        object_id="partial_ref",
        task_id="task_partial",
        actor={"kind": "runtime_test"},
        correlation_id="corr_partial_ref",
        idempotency_key="runtime_test_partial_ref",
        payload={
            "artifact_ref": {
                "artifact_id": "artifact_partial",
                "artifact_type": "PartialArtifact",
                "content_hash": "sha256:missing_uri",
            }
        },
        occurred_at="2026-07-22T00:00:00Z",
    )

    with pytest.raises(ValueError, match="malformed artifact reference"):
        project_protocol_run(
            run_id="run_partial_ref",
            task_id="task_partial",
            root_unit_id="unit_partial",
            event_ledger=ledger,
            artifact_store=store,
        )


def test_projection_reads_one_verified_snapshot_without_legacy_second_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path / "store")
    ledger = EventLedger(tmp_path / "events" / "single_snapshot.jsonl")
    ledger.append(
        event_type=EventType.TASK_UNIT_CREATED,
        object_type="TaskUnit",
        object_id="root_snapshot",
        task_id="task_snapshot",
        actor={"kind": "runtime_test"},
        correlation_id="corr_snapshot",
        idempotency_key="root_snapshot:created",
        payload={
            "task_unit": {
                "unit_id": "root_snapshot",
                "state": "Completed",
            }
        },
        occurred_at="2026-08-01T00:00:00Z",
    )
    original_snapshot_reader = ledger.read_verified_snapshot
    snapshot_reads = 0

    def counted_snapshot_reader():
        nonlocal snapshot_reads
        snapshot_reads += 1
        return original_snapshot_reader()

    def forbidden_legacy_read(*_args, **_kwargs):
        raise AssertionError("projection performed an independent legacy ledger read")

    monkeypatch.setattr(ledger, "read_verified_snapshot", counted_snapshot_reader)
    monkeypatch.setattr(ledger, "read_all", forbidden_legacy_read)
    monkeypatch.setattr(ledger, "verify_hash_chain", forbidden_legacy_read)

    result = project_protocol_run(
        run_id="run_snapshot",
        task_id="task_snapshot",
        root_unit_id="root_snapshot",
        event_ledger=ledger,
        artifact_store=store,
    )

    assert snapshot_reads == 1
    assert result.status == "completed"
    assert result.ledger_binding is not None
    assert result.ledger_binding.run_id == "run_snapshot"


def test_projection_binding_distinguishes_legal_producer_ledgers_with_same_refs(
    tmp_path: Path,
) -> None:
    results = []
    for marker in ("producer-a", "producer-b"):
        root = tmp_path / marker
        ledger = EventLedger(root / "events.jsonl")
        ledger.append(
            event_type=EventType.TASK_UNIT_CREATED,
            object_type="TaskUnit",
            object_id="root_bound",
            task_id="task_bound",
            actor={"kind": "runtime_test"},
            correlation_id="corr_bound",
            idempotency_key="root_bound:created",
            payload={
                "task_unit": {
                    "unit_id": "root_bound",
                    "state": "Completed",
                },
                "producer_marker": marker,
            },
            occurred_at="2026-08-01T00:00:00Z",
        )
        results.append(
            project_protocol_run(
                run_id="run_bound",
                task_id="task_bound",
                root_unit_id="root_bound",
                event_ledger=ledger,
                artifact_store=ArtifactStore(root / "store"),
            )
        )

    first, second = results
    assert first.status == second.status == "completed"
    assert first.event_refs == second.event_refs
    assert first.artifact_refs == second.artifact_refs == ()
    assert first.ledger_binding is not None
    assert second.ledger_binding is not None
    assert first.ledger_binding.ledger_bytes_digest != (
        second.ledger_binding.ledger_bytes_digest
    )
    assert first.ledger_binding.ledger_events_digest != (
        second.ledger_binding.ledger_events_digest
    )
    assert first.ledger_binding.binding_digest != second.ledger_binding.binding_digest
