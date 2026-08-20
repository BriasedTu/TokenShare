from __future__ import annotations

import sqlite3
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from tokenshare.core.models import Lease, LeaseState, ProtocolConfig
from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.local_runtime.contracts import (
    ParentCommitStores,
    ParentStagedTraceDelivery,
    PreparedTraceDelivery,
    TraceDeliveryAttempt,
    TraceConsumptionCore,
    TraceConsumptionRecord,
    ProtocolRunRequest,
)
from tokenshare.local_runtime.coordinator import (
    ProtocolRunCoordinator,
    build_trace_delivery_attempt,
)
from tokenshare.local_runtime.coordinator import commit_prepared_delivery
from tokenshare.local_runtime.coordinator import commit_worker_outcome
from tokenshare.local_runtime.projection import project_trace_consumptions
from tokenshare.local_runtime.workers import (
    ProcessWorkerBackend,
    SequentialWorkerBackend,
    ThreadWorkerBackend,
    WorkerExecutionFact,
    execute_worker_batch,
)
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex
from tests.phase3_fixtures import (
    make_environment_ref,
    make_executor_descriptor,
    make_output_contract,
    make_plugin_descriptor,
)
from tests.local_runtime.test_coordinator_full_lifecycle import (
    _Clock,
    _DirectCompletePluginRuntime,
)


CANONICAL_FIELDS = (
    "schema_version",
    "current_run_id",
    "task_id",
    "unit_id",
    "attempt_id",
    "attempt_ordinal",
    "binding_digest",
    "inference_request_digest",
    "bank_root_id",
    "manifest_digest",
    "entry_id",
    "source_terminal_kind",
    "source_bank_object_locators",
    "logical_start_ms",
    "source_latency_ms",
    "logical_finish_ms",
    "parser_input_media_type",
    "parser_input_digest",
    "child_worker_id",
    "child_completion_sequence",
    "delivery_digest",
)

TYPED_FIELDS = (
    "schema_version",
    "current_run_id",
    "task_id",
    "unit_id",
    "attempt_id",
    "attempt_ordinal",
    "binding_digest",
    "inference_request_digest",
    "bank_root_id",
    "manifest_digest",
    "entry_id",
    "source_terminal_kind",
    "source_bank_object_locators",
    "logical_start_ms",
    "source_api_latency_ms",
    "source_api_latency_missing",
    "source_api_latency_missing_count",
    "source_api_latency_ref",
    "protocol_operational_delay_ms",
    "logical_finish_ms",
    "parser_input_media_type",
    "parser_input_digest",
    "child_worker_id",
    "child_completion_sequence",
    "delivery_digest",
)


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _delivery(*, worker_id: str = "worker-1", sequence: int = 1):
    return PreparedTraceDelivery.create(
        current_run_id="run_trace",
        task_id="task_trace",
        unit_id="unit_trace",
        attempt_id="attempt_trace",
        attempt_ordinal=0,
        binding_digest=_digest("1"),
        inference_request_digest=_digest("2"),
        bank_root_id="bank_root",
        manifest_digest=_digest("3"),
        entry_id="entry_1",
        source_terminal_kind="success",
        source_bank_object_locators=(
            {
                "bank_root_id": "bank_root",
                "manifest_digest": _digest("3"),
                "entry_id": "entry_1",
                "object_role": "provenance",
                "object_digest": _digest("5"),
            },
            {
                "bank_root_id": "bank_root",
                "manifest_digest": _digest("3"),
                "entry_id": "entry_1",
                "object_role": "raw_output",
                "object_digest": _digest("4"),
            },
        ),
        logical_start_ms=100,
        source_latency_ms=25,
        parser_input_media_type="application/json",
        parser_input_digest=_digest("6"),
        child_worker_id=worker_id,
        child_completion_sequence=sequence,
    )


def _lease() -> Lease:
    return Lease(
        lease_id="lease_trace",
        task_id="task_trace",
        unit_id="unit_trace",
        attempt_id="attempt_trace",
        client_id="worker-1",
        state=LeaseState.ACTIVE,
        fencing_token="fence_trace",
        issued_at="2026-08-02T00:00:00Z",
        expires_at="2026-08-02T00:05:00Z",
        last_heartbeat_at=None,
        heartbeat_count=0,
        lease_kind="primary",
        terminated_at=None,
        terminated_reason=None,
        metadata={"binding_digest": _digest("1"), "attempt_ordinal": 0},
    )


def _completion(*, worker_id: str = "worker-1", sequence: int = 1):
    return WorkerExecutionFact(
        execution_index=sequence,
        request_id="request_trace",
        submission_id=None,
        result_kind="prepared_trace_delivery",
        unit_id="unit_trace",
        attempt_id="attempt_trace",
        lease_id="lease_trace",
        worker_id=worker_id,
    )


def _stores(tmp_path) -> ParentCommitStores:
    artifacts = ArtifactStore(tmp_path / "artifacts")
    ledger = EventLedger(tmp_path / "events" / "trace.jsonl")
    index = SQLiteMaterializedIndex(tmp_path / "index.sqlite", artifact_store=artifacts)
    return ParentCommitStores(
        artifact_store=artifacts,
        event_ledger=ledger,
        sqlite_index=index,
    )


def test_parent_commit_accepts_core_neutral_typed_trace_delivery_stager(tmp_path) -> None:
    stores = _stores(tmp_path)

    class DomainStager:
        def stage(self, context):
            staged = context.artifact_store.save_json(
                {"kind": "official-domain-candidate"},
                artifact_id="typed_domain_candidate",
                artifact_type="canonical_output",
                artifact_schema_id="test.domain_candidate",
                artifact_schema_version="v1",
                source={"kind": "test_domain_stager"},
                metadata={},
                created_at=context.created_at,
            )
            evidence = context.artifact_store.save_json(
                {"kind": "official-verifier"},
                artifact_id="typed_domain_verifier",
                artifact_type="VerificationEvidence",
                artifact_schema_id="test.domain_verifier",
                artifact_schema_version="v1",
                source={"kind": "test_domain_stager"},
                metadata={},
                created_at=context.created_at,
            )
            return ParentStagedTraceDelivery(
                parser_result_ref=staged,
                current_provenance_ref=staged,
                verifier_checker_refs=(evidence,),
                canonical_ref=staged,
                trace_attribution_refs=(evidence,),
            )

    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=SimpleNamespace(
            fact=_completion(), request=_request(), failure_kind=None
        ),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=replace(stores, trace_delivery_stager=DomainStager()),
    )

    assert record.core.canonical_ref is not None
    assert record.core.canonical_ref.artifact_type == "canonical_output"
    assert len(record.core.verifier_checker_refs) == 1
    assert len(stores.event_ledger.read_all()) == 1


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        request_id="request_trace",
        task_id="task_trace",
        unit_id="unit_trace",
        attempt_id="attempt_trace",
        lease_id="lease_trace",
        fencing_token="fence_trace",
        plugin=make_plugin_descriptor().to_dict(),
        executor=make_executor_descriptor().to_dict(),
        registry_snapshot_id="registry_trace",
        allocation_decision={"client_id": "worker-1"},
        capability_snapshot={"executor": "mock_ai"},
        task_unit_snapshot={"unit_id": "unit_trace"},
        input_artifact_refs={},
        output_contract=make_output_contract(),
        hard_requirements={"executor": "mock_ai"},
        soft_hints={},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={},
        created_at="2026-08-02T00:00:00Z",
        attempt_ordinal=0,
        source_binding_digest=_digest("1"),
    )


class _PreparedExecutor:
    def execute(self, request, *, submission_id: str, submitted_at: str):
        del request, submission_id, submitted_at
        return _delivery(worker_id="child-placeholder", sequence=999)


class _RunRootPreparedExecutor:
    """只返回与真实 dispatch identity 绑定的 child delivery。"""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id

    def execute(self, request, *, submission_id: str, submitted_at: str):
        del submission_id, submitted_at
        return PreparedTraceDelivery.create(
            current_run_id=self._run_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            attempt_ordinal=request.attempt_ordinal,
            binding_digest=request.source_binding_digest,
            inference_request_digest=_digest("2"),
            bank_root_id="bank_root",
            manifest_digest=_digest("3"),
            entry_id="entry_1",
            source_terminal_kind="success",
            source_bank_object_locators=(
                {
                    "bank_root_id": "bank_root",
                    "manifest_digest": _digest("3"),
                    "entry_id": "entry_1",
                    "object_role": "raw_output",
                    "object_digest": _digest("4"),
                },
            ),
            logical_start_ms=0,
            source_latency_ms=25,
            parser_input_media_type="application/json",
            parser_input_digest=_digest("6"),
            child_worker_id="child-placeholder",
            child_completion_sequence=999,
        )


class _FencedPreparedBackend:
    def __init__(self, delegate) -> None:
        self._delegate = delegate

    @property
    def capacity(self) -> int:
        return self._delegate.capacity

    def execute_batch(self, requests):
        return tuple(
            replace(
                outcome,
                fact=replace(outcome.fact, result_kind="fenced"),
                failure_kind="fenced",
            )
            for outcome in self._delegate.execute_batch(requests)
        )


def _run_root_runtime(tmp_path, *, backend, run_id: str, max_retries: int = 0):
    store = ArtifactStore(tmp_path / "artifacts")
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="trace_parent_commit_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/task_demo.jsonl",
        ),
        max_retries=max_retries,
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
            run_id=run_id,
            root_input={"prompt": "consume prepared trace"},
            plugin_runtime=_DirectCompletePluginRuntime(config),
            worker_backend=backend(store, clock),
        )
    )
    return result, store, ledger


def test_prepared_trace_delivery_v1_exact_fields_and_digest() -> None:
    delivery = _delivery()

    assert tuple(item.name for item in fields(delivery)) == TYPED_FIELDS
    assert tuple(delivery.to_dict()) == CANONICAL_FIELDS
    assert delivery.source_api_latency_ms == 25
    assert delivery.source_api_latency_missing is False
    assert delivery.source_api_latency_missing_count == 0
    assert delivery.protocol_operational_delay_ms == 25
    assert [
        item["object_role"] for item in delivery.to_dict()["source_bank_object_locators"]
    ] == ["provenance", "raw_output"]
    assert delivery.logical_finish_ms == 125
    with pytest.raises(ValueError, match="delivery_digest mismatch"):
        replace(delivery, delivery_digest=_digest("0"))


def test_binding_is_opaque_and_present_before_dispatch() -> None:
    request = _request()
    serialized = request.to_dict()

    assert serialized["attempt_ordinal"] == 0
    assert serialized["source_binding_digest"] == _digest("1")
    assert "bank" not in " ".join(serialized).lower()


def test_parent_stages_artifacts_then_appends_exactly_one_trace_delivery_committed_event(tmp_path) -> None:
    stores = _stores(tmp_path)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=frozenset(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=stores,
    )

    events = stores.event_ledger.read_all()
    assert isinstance(record, TraceConsumptionRecord)
    assert len(events) == 1
    assert events[0].event_type == EventType.TRACE_DELIVERY_COMMITTED
    assert stores.artifact_store.verify(record.core.current_wrapper_ref)
    with sqlite3.connect(stores.sqlite_index.path) as connection:
        assert connection.execute("select count(*) from trace_consumptions").fetchone() == (1,)


def test_commit_event_payload_is_exact_trace_consumption_core_without_event_ref_seq_or_hash(tmp_path) -> None:
    stores = _stores(tmp_path)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=stores,
    )
    event = stores.event_ledger.read_all()[0]

    assert event.payload == record.core.to_dict()
    assert record.core.execution_result_kind is None
    assert "execution_result_kind" not in event.payload
    assert set(event.payload) == {
        item.name
        for item in fields(TraceConsumptionCore)
        if item.name != "execution_result_kind"
    }
    assert not {"committed_event_ref", "event_seq", "event_hash"} & set(event.payload)
    assert record.committed_event_ref == {
        "event_id": event.event_id,
        "event_seq": event.event_seq,
        "event_type": EventType.TRACE_DELIVERY_COMMITTED.value,
        "event_hash": event.event_hash,
    }


def test_projection_and_sqlite_derive_visibility_only_from_commit_event(tmp_path) -> None:
    stores = _stores(tmp_path)
    delivery = _delivery()
    stores.artifact_store.save_json(
        delivery.to_dict(),
        artifact_id="orphan_prepared_delivery",
        artifact_type="PreparedTraceDelivery",
        artifact_schema_id="tokenshare.prepared_trace_delivery",
        artifact_schema_version="v1",
        source={"kind": "parent_stage"},
        metadata={},
        created_at="2026-08-02T00:00:00Z",
    )

    assert project_trace_consumptions(stores.event_ledger.read_verified_snapshot()) == ()
    stores.sqlite_index.rebuild_from_events(stores.event_ledger.read_all())
    with sqlite3.connect(stores.sqlite_index.path) as connection:
        assert connection.execute("select count(*) from trace_consumptions").fetchone() == (0,)


def test_projection_derives_committed_event_ref_from_finalized_outer_header(tmp_path) -> None:
    stores = _stores(tmp_path)
    committed = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=stores,
    )

    projected = project_trace_consumptions(
        stores.event_ledger.read_verified_snapshot()
    )

    assert projected == (committed,)
    assert projected[0].committed_event_ref["event_hash"] == (
        stores.event_ledger.read_all()[0].event_hash
    )


@pytest.mark.parametrize(
    ("crash_stage", "commit_visible"),
    [
        ("wrapper_staged", False),
        ("parser_input_staged", False),
        ("parser_result_staged", False),
        ("provenance_staged", False),
        ("artifacts_staged", False),
        ("commit_event_appended", True),
        ("projection_applied", True),
    ],
)
def test_crash_at_each_stage_artifact_event_projection_boundary_never_exposes_false_consumption(
    tmp_path,
    crash_stage,
    commit_visible,
) -> None:
    def crash(stage: str) -> None:
        if stage == crash_stage:
            raise RuntimeError(f"crash:{stage}")

    stores = replace(_stores(tmp_path), commit_hook=crash)
    with pytest.raises(RuntimeError, match=f"crash:{crash_stage}"):
        commit_prepared_delivery(
            delivery=_delivery(),
            worker_completion=_completion(),
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=stores,
        )

    projected = project_trace_consumptions(
        stores.event_ledger.read_verified_snapshot()
    )
    assert len(projected) == int(commit_visible)
    with sqlite3.connect(stores.sqlite_index.path) as connection:
        has_table = connection.execute(
            "select count(*) from sqlite_master where type='table' and name='trace_consumptions'"
        ).fetchone()[0]
        sqlite_count = (
            connection.execute("select count(*) from trace_consumptions").fetchone()[0]
            if has_table
            else 0
        )
    assert sqlite_count <= len(projected)

    reconciled_stores = _stores(tmp_path)
    reconciled = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=reconciled_stores,
    )
    assert len(reconciled_stores.event_ledger.read_all()) == 1
    assert project_trace_consumptions(
        reconciled_stores.event_ledger.read_verified_snapshot()
    ) == (reconciled,)
    with sqlite3.connect(reconciled_stores.sqlite_index.path) as connection:
        assert connection.execute(
            "select count(*) from trace_consumptions"
        ).fetchone() == (1,)


def test_orphan_staged_artifacts_are_invisible_and_reconcilable(tmp_path) -> None:
    def crash_after_staging(stage: str) -> None:
        if stage == "artifacts_staged":
            raise RuntimeError("parent crash before visibility event")

    stores = replace(_stores(tmp_path), commit_hook=crash_after_staging)
    with pytest.raises(RuntimeError, match="before visibility event"):
        commit_prepared_delivery(
            delivery=_delivery(),
            worker_completion=_completion(),
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=stores,
        )

    assert list((tmp_path / "artifacts").rglob("*.json"))
    assert project_trace_consumptions(
        stores.event_ledger.read_verified_snapshot()
    ) == ()

    reconciled_stores = _stores(tmp_path)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=reconciled_stores,
    )
    assert reconciled_stores.artifact_store.verify(record.core.current_wrapper_ref)
    assert len(reconciled_stores.event_ledger.read_all()) == 1


def test_partial_or_corrupt_event_tail_fails_closed_under_existing_ledger_recovery(
    tmp_path,
) -> None:
    stores = _stores(tmp_path)
    commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=stores,
    )
    with stores.event_ledger.path.open("ab") as handle:
        handle.write(b'{"partial":')

    with pytest.raises(ValueError, match="invalid JSONL event"):
        stores.event_ledger.read_verified_snapshot()
    with pytest.raises(ValueError, match="invalid JSONL event"):
        commit_prepared_delivery(
            delivery=_delivery(),
            worker_completion=_completion(),
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=stores,
        )


def test_commit_event_replay_is_idempotent_without_ordinal_or_consumption_duplication(tmp_path) -> None:
    stores = _stores(tmp_path)
    arguments = {
        "delivery": _delivery(),
        "worker_completion": _completion(),
        "killed_worker_ids": (),
        "active_lease": _lease(),
        "current_fencing_token": "fence_trace",
        "current_stores": stores,
    }

    first = commit_prepared_delivery(**arguments)
    second = commit_prepared_delivery(**arguments)

    assert first == second
    assert len(stores.event_ledger.read_all()) == 1
    with sqlite3.connect(stores.sqlite_index.path) as connection:
        assert connection.execute("select count(*) from trace_consumptions").fetchone() == (1,)


def test_event_hash_preimage_has_no_self_reference(tmp_path) -> None:
    stores = _stores(tmp_path)
    commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(),
        killed_worker_ids=(),
        active_lease=_lease(),
        current_fencing_token="fence_trace",
        current_stores=stores,
    )

    event = stores.event_ledger.read_all()[0]
    assert "event_hash" not in event.payload
    assert stores.event_ledger.verify_hash_chain() is True


@pytest.mark.parametrize("backend_kind", ["sequential", "thread", "process"])
def test_run_root_commits_prepared_delivery_once_then_uses_protocol_engine_lifecycle(
    tmp_path,
    backend_kind,
) -> None:
    run_id = f"run_trace_{backend_kind}"

    def backend(_store, clock):
        executor = _RunRootPreparedExecutor(run_id)
        if backend_kind == "sequential":
            return SequentialWorkerBackend(executor=executor, submitted_at=clock)
        if backend_kind == "thread":
            return ThreadWorkerBackend(
                executor=executor,
                capacity=1,
                submitted_at=clock,
            )
        return ProcessWorkerBackend(
            executor=executor,
            capacity=1,
            submitted_at=clock,
        )

    result, _store, ledger = _run_root_runtime(
        tmp_path,
        backend=backend,
        run_id=run_id,
    )
    events = ledger.read_all()

    assert result.status == "completed"
    assert result.summary["trace_consumption_count"] == 1
    assert sum(
        event.event_type == EventType.TRACE_DELIVERY_COMMITTED for event in events
    ) == 1
    assert sum(
        event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED for event in events
    ) == 1
    assert sum(event.event_type == EventType.VERIFICATION_RECORDED for event in events) == 1


@pytest.mark.parametrize("terminal_kind", ["killed", "fenced"])
def test_run_root_observes_terminal_trace_delivery_attempt_without_consumption(
    tmp_path,
    terminal_kind,
) -> None:
    run_id = f"run_trace_{terminal_kind}"

    def backend(_store, clock):
        executor = _RunRootPreparedExecutor(run_id)
        if terminal_kind == "killed":
            return ProcessWorkerBackend(
                executor=executor,
                capacity=1,
                submitted_at=clock,
                terminate_once=lambda _request: True,
                kill_point="after_child_prepare",
            )
        return _FencedPreparedBackend(
            SequentialWorkerBackend(executor=executor, submitted_at=clock)
        )

    result, _store, ledger = _run_root_runtime(
        tmp_path,
        backend=backend,
        run_id=run_id,
    )

    assert result.status == "failed"
    assert result.summary["trace_consumption_count"] == 0
    assert len(result.summary["trace_delivery_attempts"]) == 1
    attempt_record = TraceDeliveryAttempt(
        **result.summary["trace_delivery_attempts"][0]
    )
    assert attempt_record.attempt_id == f"{run_id}_attempt_1"
    assert attempt_record.status == terminal_kind
    assert attempt_record.event_ref["event_type"] == (
        EventType.ATTEMPT_STATE_CHANGED.value
    )
    assert all(
        event.event_type != EventType.TRACE_DELIVERY_COMMITTED
        for event in ledger.read_all()
    )


def test_child_delivery_has_zero_artifact_event_sqlite_side_effects(tmp_path) -> None:
    stores = _stores(tmp_path)
    before = tuple(tmp_path.rglob("*"))
    backend = SequentialWorkerBackend(
        executor=_PreparedExecutor(),
        submitted_at=lambda: "2026-08-02T00:00:00Z",
    )

    outcome = execute_worker_batch(backend, (_request(),))[0]

    assert outcome.prepared_delivery is not None
    assert tuple(tmp_path.rglob("*")) == before
    assert stores.event_ledger.read_all() == []
    assert not stores.sqlite_index.path.exists()


def test_parent_requires_request_and_lease_binding_to_agree_before_staging(
    tmp_path,
) -> None:
    backend = SequentialWorkerBackend(
        executor=_PreparedExecutor(),
        submitted_at=lambda: "2026-08-02T00:00:00Z",
    )
    outcome = execute_worker_batch(backend, (_request(),))[0]
    stale_lease = replace(
        _lease(),
        metadata={"binding_digest": _digest("0"), "attempt_ordinal": 0},
    )
    stores = _stores(tmp_path)

    with pytest.raises(ValueError, match="binding digest"):
        commit_worker_outcome(
            outcome=outcome,
            killed_worker_ids=(),
            active_lease=stale_lease,
            current_fencing_token="fence_trace",
            current_stores=stores,
        )

    assert stores.event_ledger.read_all() == []
    assert list((tmp_path / "artifacts").rglob("*.json")) == []


def test_parent_rejects_cross_task_dispatch_request_before_staging(tmp_path) -> None:
    backend = SequentialWorkerBackend(
        executor=_PreparedExecutor(),
        submitted_at=lambda: "2026-08-02T00:00:00Z",
    )
    outcome = execute_worker_batch(backend, (_request(),))[0]
    outcome = replace(outcome, request=replace(outcome.request, task_id="task_other"))
    stores = _stores(tmp_path)

    with pytest.raises(ValueError, match="dispatch request"):
        commit_worker_outcome(
            outcome=outcome,
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=stores,
        )

    assert stores.event_ledger.read_all() == []
    assert list((tmp_path / "artifacts").rglob("*.json")) == []


def test_process_death_after_child_prepare_yields_killed_record_and_zero_consumption(
    tmp_path,
) -> None:
    stores = _stores(tmp_path)
    backend = ProcessWorkerBackend(
        executor=_PreparedExecutor(),
        capacity=1,
        submitted_at=lambda: "2026-08-02T00:00:00Z",
        terminate_once=lambda _request: True,
        kill_point="after_child_prepare",
    )

    outcome = execute_worker_batch(backend, (_request(),))[0]
    assert outcome.failure_kind == "worker_terminated"
    assert outcome.prepared_delivery is not None
    attempt_event = stores.event_ledger.append(
        event_type=EventType.ATTEMPT_STATE_CHANGED,
        object_type="Attempt",
        object_id="attempt_trace",
        task_id="task_trace",
        actor={"kind": "protocol_parent"},
        correlation_id="run_trace",
        idempotency_key="attempt_trace:killed",
        payload={"old_state": "Running", "new_state": "Failed"},
    )
    attempt_record = build_trace_delivery_attempt(
        outcome=outcome,
        status="killed",
        event=attempt_event,
    )

    assert isinstance(attempt_record, TraceDeliveryAttempt)
    assert attempt_record.status == "killed"
    assert all(
        event.event_type != EventType.TRACE_DELIVERY_COMMITTED
        for event in stores.event_ledger.read_all()
    )
    assert project_trace_consumptions(
        stores.event_ledger.read_verified_snapshot()
    ) == ()
    assert list((tmp_path / "artifacts").rglob("*.json")) == []


def test_fenced_prepared_delivery_yields_fenced_attempt_and_zero_consumption(
    tmp_path,
) -> None:
    stores = _stores(tmp_path)
    backend = SequentialWorkerBackend(
        executor=_PreparedExecutor(),
        submitted_at=lambda: "2026-08-02T00:00:00Z",
    )
    outcome = execute_worker_batch(backend, (_request(),))[0]
    outcome = replace(
        outcome,
        fact=replace(outcome.fact, result_kind="fenced"),
        failure_kind="fenced",
    )
    attempt_event = stores.event_ledger.append(
        event_type=EventType.ATTEMPT_STATE_CHANGED,
        object_type="Attempt",
        object_id="attempt_trace",
        task_id="task_trace",
        actor={"kind": "protocol_parent"},
        correlation_id="run_trace",
        idempotency_key="attempt_trace:fenced",
        payload={"old_state": "Running", "new_state": "Failed"},
    )

    attempt_record = build_trace_delivery_attempt(
        outcome=outcome,
        status="fenced",
        event=attempt_event,
    )
    assert attempt_record.status == "fenced"
    with pytest.raises(ValueError, match="killed or fenced"):
        commit_worker_outcome(
            outcome=outcome,
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=stores,
        )
    assert project_trace_consumptions(
        stores.event_ledger.read_verified_snapshot()
    ) == ()
    assert list((tmp_path / "artifacts").rglob("*.json")) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"worker_completion": _completion(sequence=2)}, "completion sequence"),
        ({"killed_worker_ids": {"worker-1"}}, "killed"),
        ({"current_fencing_token": "stale"}, "fencing"),
        ({"active_lease": replace(_lease(), state=LeaseState.EXPIRED)}, "active lease"),
    ],
)
def test_parent_commits_only_after_worker_status_sequence_lease_and_string_fence_validation(
    tmp_path,
    change,
    message,
) -> None:
    stores = _stores(tmp_path)
    arguments = {
        "delivery": _delivery(),
        "worker_completion": _completion(),
        "killed_worker_ids": frozenset(),
        "active_lease": _lease(),
        "current_fencing_token": "fence_trace",
        "current_stores": stores,
    }
    arguments.update(change)

    with pytest.raises(ValueError, match=message):
        commit_prepared_delivery(**arguments)

    assert stores.event_ledger.read_all() == []
    assert list((tmp_path / "artifacts").rglob("*.json")) == []
