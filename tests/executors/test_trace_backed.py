from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.executors.response_bank import (
    OBJECT_ROLES,
    ExternalBankObjectLocator,
    ResponseBankEntry,
    ResponseBankInventoryRow,
    ResponseBankManifest,
    ResponseBankResolver,
    canonical_digest,
    initialize_response_bank,
    inventory_entry_id,
    semantic_slot_key,
)
from tokenshare.executors.trace_backed import (
    TraceBackedExecutor,
    TraceSourceBinding,
    bind_trace_execution_request,
    freeze_trace_source_binding,
)
from tokenshare.local_runtime.contracts import (
    LOGICAL_DISPATCH_START_MS_HINT,
    TraceDeliveryAttempt,
)
from tokenshare.local_runtime.coordinator import commit_prepared_delivery
from tokenshare.local_runtime.projection import project_trace_consumptions
from tokenshare.storage.events import EventType
from tests.local_runtime.test_trace_delivery_parent_commit import (
    _completion,
    _delivery,
    _lease,
    _request,
    _stores,
)


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _bank(
    root: Path,
    *,
    terminal_kinds: tuple[str, ...] = ("success",),
    contents: tuple[str, ...] | None = None,
    planned_ai_unit_id: str = "planned-unit",
) -> ResponseBankResolver:
    contents = contents or tuple(f'{{"slot":{index}}}' for index in range(len(terminal_kinds)))
    rows: list[ResponseBankInventoryRow] = []
    entry_specs: list[tuple[str, str, dict[str, bytes]]] = []
    for replacement_slot, (terminal_kind, content) in enumerate(zip(terminal_kinds, contents)):
        entry_id = f"entry-{replacement_slot}"
        body = json.dumps({"slot": replacement_slot}, sort_keys=True).encode()
        inference_digest = _digest(body + b":inference")
        row_values = {
            "inventory_entry_id": "",
            "semantic_slot_key": semantic_slot_key(
                case_record_digest="sha256:case",
                planned_ai_unit_id=planned_ai_unit_id,
                sample_slot_index=0,
                replacement_slot=replacement_slot,
                provider_config_digest="sha256:provider",
                prompt_profile_digest="sha256:prompt",
                prompt_admission_profile_digest="sha256:admission",
                plugin_version="2.0.0",
            ),
            "case_record_digest": "sha256:case",
            "planned_ai_unit_id": planned_ai_unit_id,
            "sample_slot_index": 0,
            "replacement_slot": replacement_slot,
            "provider_config_digest": "sha256:provider",
            "prompt_profile_digest": "sha256:prompt",
            "prompt_admission_profile_digest": "sha256:admission",
            "plugin_version": "2.0.0",
            "entry_id": entry_id,
            "body_digest": _digest(body),
            "inference_request_digest": inference_digest,
        }
        row_values["inventory_entry_id"] = inventory_entry_id(row_values)
        rows.append(ResponseBankInventoryRow(**row_values))
        terminal_role = "raw_output" if terminal_kind == "success" else "provider_failure"
        terminal_body = (
            {
                "schema_version": "tokenshare.response_bank_raw_output.v1",
                "raw_response_json": {"id": entry_id},
                "content_text": content,
                "reasoning_content": None,
                "provider_response_id": entry_id,
                "finish_reason": "stop",
            }
            if terminal_kind == "success"
            else {
                "schema_version": "tokenshare.response_bank_provider_failure.v1",
                "failure_kind": "http_error",
                "http_status": 503,
                "message": content,
                "raw_response_json": None,
            }
        )
        objects = {
            "request_body": body,
            terminal_role: json.dumps(terminal_body, sort_keys=True).encode(),
            "provenance": json.dumps({"source_transport": "real_api"}).encode(),
            "usage": json.dumps({"usage_status": "reported", "usage": {"total_tokens": 7}}).encode(),
            "latency": json.dumps({"latency_ms": 10 + replacement_slot, "timing_source": "provider"}).encode(),
            "pricing": json.dumps({"cost_usd": "0.01"}).encode(),
            "acquisition_attempt": json.dumps({"attempt": replacement_slot}).encode(),
            "model_record": json.dumps({"model": "frozen-model"}).encode(),
        }
        entry_specs.append((terminal_kind, inference_digest, objects))

    inventory_digest = canonical_digest(
        [item.to_dict() for item in sorted(rows, key=lambda item: item.inventory_entry_id)]
    )
    manifest = ResponseBankManifest.create(
        bank_root_id="bank-root",
        profile_digest="sha256:profile",
        budget_digest="sha256:budget",
        inventory_digest=inventory_digest,
        provider_config_digest="sha256:provider",
        entry_ids=tuple(row.entry_id for row in rows),
        object_role_schema=OBJECT_ROLES,
        terminal_entry_count=len(rows),
        created_by_paid_receipt_digest="sha256:receipt",
    )
    entries: list[ResponseBankEntry] = []
    object_bytes: dict[str, bytes] = {}
    for row, (terminal_kind, inference_digest, objects) in zip(rows, entry_specs):
        locators = tuple(
            ExternalBankObjectLocator(
                bank_root_id=manifest.bank_root_id,
                manifest_digest=manifest.manifest_digest,
                entry_id=row.entry_id,
                object_role=role,
                object_digest=_digest(data),
            )
            for role, data in objects.items()
        )
        object_bytes.update({locator.object_digest: objects[locator.object_role] for locator in locators})
        entries.append(
            ResponseBankEntry(
                inventory_digest=inventory_digest,
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                inference_request_digest=inference_digest,
                entry_id=row.entry_id,
                sample_slot_index=0,
                replacement_slot=row.replacement_slot,
                terminal_kind=terminal_kind,
                object_locators=locators,
                acquisition_state_ref=f"state-{row.replacement_slot}",
            )
        )
    initialize_response_bank(
        root,
        manifest=manifest,
        inventory_rows=rows,
        entries=entries,
        objects=object_bytes,
    )
    return ResponseBankResolver.open(root)


def _binding(resolver: ResponseBankResolver, *, evidence: str = "approved_real_api_acquisition") -> TraceSourceBinding:
    planned_ai_unit_id = resolver.index.inventory_rows[0].planned_ai_unit_id
    return freeze_trace_source_binding(
        resolver,
        planned_ai_unit_id=planned_ai_unit_id,
        sample_slot_index=0,
        entry_ids=tuple(item.entry_id for item in resolver.index.entries),
        source_evidence_class=evidence,
    )


def _bound_request(
    binding: TraceSourceBinding,
    *,
    ordinal: int = 0,
    logical_start_ms: int = 0,
):
    request = replace(
        _request(),
        attempt_ordinal=ordinal,
        source_binding_digest=None,
        soft_hints={
            "planned_ai_unit_id": binding.planned_ai_unit_id,
            "sample_slot_index": binding.sample_slot_index,
            LOGICAL_DISPATCH_START_MS_HINT: logical_start_ms,
        },
    )
    return bind_trace_execution_request(request, binding)


def test_trace_executor_calls_provider_zero_and_streams_external_source(tmp_path, monkeypatch) -> None:
    resolver = _bank(tmp_path / "bank")
    binding = _binding(resolver)
    reads: list[tuple[str, int]] = []
    original = resolver.read_verified

    def recording_read(locator, *, chunk_size=64 * 1024):
        reads.append((locator.object_role, chunk_size))
        return original(locator, chunk_size=chunk_size)

    monkeypatch.setattr(resolver, "read_verified", recording_read)
    executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="run-trace",
        stream_chunk_size=7,
    )

    delivery = executor.execute(
        _bound_request(binding, logical_start_ms=50),
        submission_id="ignored",
        submitted_at="ignored",
    )

    assert executor.provider_call_count == 0
    assert {role for role, _ in reads} == {
        "request_body", "raw_output", "provenance", "usage", "latency",
        "pricing", "acquisition_attempt", "model_record",
    }
    assert {chunk_size for _, chunk_size in reads} == {7}
    assert delivery.parser_input_digest == _digest(b'{"slot":0}')
    assert delivery.logical_finish_ms == 60
    serialized = json.dumps(delivery.to_dict(), sort_keys=True)
    assert '{"slot":0}' not in serialized
    assert str(resolver.root_path) not in serialized
    assert "file://" not in serialized


def test_delivery_attempt_started_killed_fenced_never_looks_delivered(tmp_path) -> None:
    resolver = _bank(tmp_path / "bank")
    binding = _binding(resolver)
    delivery = TraceBackedExecutor(
        resolver=resolver, bindings=(binding,), current_run_id="run_trace"
    ).execute(_bound_request(binding), submission_id="ignored", submitted_at="ignored")
    delivery = delivery.with_child_completion(
        child_worker_id="worker-1", child_completion_sequence=1
    )

    started = TraceDeliveryAttempt(
        attempt_id=delivery.attempt_id,
        binding_digest=delivery.binding_digest,
        status="started",
        event_ref={
            "event_id": "attempt-started",
            "event_seq": 1,
            "event_type": "ATTEMPT_STATE_CHANGED",
            "event_hash": _digest(b"attempt-started"),
        },
    )
    assert started.status == "started"

    with pytest.raises(ValueError, match="killed worker"):
        commit_prepared_delivery(
            delivery=delivery,
            worker_completion=_completion(),
            killed_worker_ids={"worker-1"},
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=_stores(tmp_path / "killed"),
        )
    with pytest.raises(ValueError, match="killed or fenced worker"):
        commit_prepared_delivery(
            delivery=delivery,
            worker_completion=replace(_completion(), result_kind="fenced"),
            killed_worker_ids=(),
            active_lease=_lease(),
            current_fencing_token="fence_trace",
            current_stores=_stores(tmp_path / "fenced"),
        )
    assert project_trace_consumptions(_stores(tmp_path / "empty").event_ledger.read_verified_snapshot()) == ()


def test_only_single_trace_delivery_committed_event_makes_staged_refs_and_consumption_visible(tmp_path) -> None:
    stores = _stores(tmp_path)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(), killed_worker_ids=(), active_lease=_lease(),
        current_fencing_token="fence_trace", current_stores=stores,
    )
    events = stores.event_ledger.read_all()
    assert [event.event_type for event in events] == [EventType.TRACE_DELIVERY_COMMITTED]
    assert project_trace_consumptions(stores.event_ledger.read_verified_snapshot()) == (record,)


def test_trace_executor_commit_payload_never_contains_committed_event_ref_event_seq_or_event_hash(tmp_path) -> None:
    stores = _stores(tmp_path)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(), killed_worker_ids=(), active_lease=_lease(),
        current_fencing_token="fence_trace", current_stores=stores,
    )
    payload = stores.event_ledger.read_all()[0].payload
    assert payload == record.core.to_dict()
    assert not {"committed_event_ref", "event_seq", "event_hash"} & set(payload)


def test_trace_consumption_record_gets_committed_ref_only_after_event_finalize(tmp_path) -> None:
    observed: dict[str, object] = {}
    stores = _stores(tmp_path)

    def hook(stage: str) -> None:
        observed[stage] = tuple(project_trace_consumptions(stores.event_ledger.read_verified_snapshot()))

    stores = replace(stores, commit_hook=hook)
    record = commit_prepared_delivery(
        delivery=_delivery(),
        worker_completion=_completion(), killed_worker_ids=(), active_lease=_lease(),
        current_fencing_token="fence_trace", current_stores=stores,
    )
    assert all(observed[stage] == () for stage in (
        "wrapper_staged", "parser_input_staged", "parser_result_staged",
        "provenance_staged", "artifacts_staged",
    ))
    assert observed["commit_event_appended"][0].committed_event_ref == record.committed_event_ref


def test_crash_before_each_staged_artifact_and_commit_event_boundary_replays_without_false_consumption(tmp_path) -> None:
    for index, boundary in enumerate((
        "wrapper_staged", "parser_input_staged", "parser_result_staged",
        "provenance_staged", "artifacts_staged",
    )):
        stores = _stores(tmp_path / str(index))

        def crash(stage: str, target=boundary) -> None:
            if stage == target:
                raise RuntimeError(target)

        with pytest.raises(RuntimeError, match=boundary):
            commit_prepared_delivery(
                delivery=_delivery(),
                worker_completion=_completion(), killed_worker_ids=(), active_lease=_lease(),
                current_fencing_token="fence_trace", current_stores=replace(stores, commit_hook=crash),
            )
        assert project_trace_consumptions(stores.event_ledger.read_verified_snapshot()) == ()


def test_crash_after_commit_before_projection_replays_idempotently(tmp_path) -> None:
    stores = _stores(tmp_path)

    def crash(stage: str) -> None:
        if stage == "commit_event_appended":
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match="commit_event_appended"):
        commit_prepared_delivery(
            delivery=_delivery(),
            worker_completion=_completion(), killed_worker_ids=(), active_lease=_lease(),
            current_fencing_token="fence_trace", current_stores=replace(stores, commit_hook=crash),
        )
    first = project_trace_consumptions(stores.event_ledger.read_verified_snapshot())
    assert len(first) == 1
    stores.sqlite_index.rebuild_from_events(stores.event_ledger.read_all())
    stores.sqlite_index.rebuild_from_events(stores.event_ledger.read_all())
    assert project_trace_consumptions(stores.event_ledger.read_verified_snapshot()) == first


def test_retry_uses_persisted_attempt_ordinal_as_replacement_slot(tmp_path) -> None:
    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("success", "success"),
        contents=('{"replacement":0}', '{"replacement":1}'),
    )
    binding = _binding(resolver)
    request = _bound_request(binding, ordinal=1)
    delivery = TraceBackedExecutor(
        resolver=resolver, bindings=(binding,), current_run_id="run-trace"
    ).execute(request, submission_id="condition-lie-0", submitted_at="ignored")
    assert delivery.attempt_ordinal == 1
    assert delivery.entry_id == "entry-1"
    assert delivery.parser_input_digest == _digest(b'{"replacement":1}')
    assert request.soft_hints["replacement_slot"] == 1


def test_coordinator_freezes_each_replacement_dispatch_clock_into_delivery(
    tmp_path,
) -> None:
    from tests.local_runtime.test_coordinator_full_lifecycle import (
        _Clock,
        _DirectCompletePluginRuntime,
    )
    from tokenshare.core.models import ProtocolConfig
    from tokenshare.local_runtime.contracts import ProtocolRunRequest
    from tokenshare.local_runtime.coordinator import ProtocolRunCoordinator
    from tokenshare.local_runtime.logical_scheduler import (
        LogicalSourceLatencyScheduler,
    )
    from tokenshare.local_runtime.workers import SequentialWorkerBackend
    from tokenshare.protocol_engine import ProtocolEngine
    from tokenshare.storage.artifacts import ArtifactStore
    from tokenshare.storage.events import EventLedger

    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("provider_failure", "success"),
        contents=("retry this slot", '{"answer":"accepted"}'),
    )
    binding = _binding(resolver)
    store = ArtifactStore(tmp_path / "artifacts")
    ledger = EventLedger(tmp_path / "events" / "trace.jsonl")
    config = replace(
        ProtocolConfig.default(
            config_id="trace_dispatch_clock_config",
            artifact_store_uri="file://artifacts",
            event_log_uri="file://events/trace.jsonl",
        ),
        max_retries=1,
    )

    class TraceBoundRuntime(_DirectCompletePluginRuntime):
        def build_execution_request(self, unit, *, attempt, lease):
            base = super().build_execution_request(
                unit,
                attempt=attempt,
                lease=lease,
            )
            return bind_trace_execution_request(
                replace(
                    base,
                    attempt_ordinal=attempt.attempt_ordinal,
                    source_binding_digest=None,
                    soft_hints={
                        "planned_ai_unit_id": binding.planned_ai_unit_id,
                        "sample_slot_index": binding.sample_slot_index,
                    },
                ),
                binding,
            )

    deliveries = []
    trace_executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="run_trace_dispatch_clock",
    )

    class RecordingExecutor:
        def execute(self, request, *, submission_id: str, submitted_at: str):
            delivery = trace_executor.execute(
                request,
                submission_id=submission_id,
                submitted_at=submitted_at,
            )
            deliveries.append(delivery)
            return delivery

    clock = _Clock()
    scheduler = LogicalSourceLatencyScheduler(start_ms=37)
    ProtocolRunCoordinator(
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
            run_id="run_trace_dispatch_clock",
            root_input={"prompt": "retry a frozen trace"},
            plugin_runtime=TraceBoundRuntime(config),
            worker_backend=SequentialWorkerBackend(
                executor=RecordingExecutor(),
                submitted_at=clock,
            ),
            trace_delay_policy="logical_source_latency_1x",
            logical_scheduler=scheduler,
        )
    )

    assert [delivery.attempt_ordinal for delivery in deliveries] == [0, 1]
    assert [delivery.logical_start_ms for delivery in deliveries] == [37, 47]
    assert [delivery.logical_finish_ms for delivery in deliveries] == [47, 58]


def test_regression_synthetic_source_cannot_be_paper_eligible(tmp_path) -> None:
    resolver = _bank(tmp_path / "bank")
    approved = _binding(resolver)
    binding = _binding(resolver, evidence="synthetic_regression")
    assert approved.paper_eligibility_disposition == "deferred"
    assert not approved.paper_eligible
    assert binding.paper_eligibility_disposition == "hard_false"
    assert not binding.paper_eligible
    serialized = json.dumps(binding.to_dict(), sort_keys=True)
    assert str(resolver.root_path) not in serialized
    assert "file://" not in serialized
