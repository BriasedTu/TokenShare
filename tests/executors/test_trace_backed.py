from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

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
    TraceBackedParentStager,
    TraceSourceBinding,
    bind_trace_execution_request,
    freeze_projected_trace_source_binding,
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
    source_api_latencies_ms: tuple[int | None, ...] | None = None,
    planned_ai_unit_id: str = "planned-unit",
    case_record_digest: str = "sha256:" + "c" * 64,
    request_bodies: tuple[bytes, ...] | None = None,
    role_body_overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> ResponseBankResolver:
    contents = contents or tuple(f'{{"slot":{index}}}' for index in range(len(terminal_kinds)))
    rows: list[ResponseBankInventoryRow] = []
    entry_specs: list[tuple[str, str, dict[str, bytes]]] = []
    for replacement_slot, (terminal_kind, content) in enumerate(zip(terminal_kinds, contents)):
        entry_id = f"entry-{replacement_slot}"
        body = (
            request_bodies[replacement_slot]
            if request_bodies is not None
            else json.dumps({"slot": replacement_slot}, sort_keys=True).encode()
        )
        inference_digest = _digest(body + b":inference")
        row_values = {
            "inventory_entry_id": "",
            "semantic_slot_key": semantic_slot_key(
                case_record_digest=case_record_digest,
                planned_ai_unit_id=planned_ai_unit_id,
                sample_slot_index=0,
                replacement_slot=replacement_slot,
                provider_config_digest="sha256:provider",
                prompt_profile_digest="sha256:prompt",
                prompt_admission_profile_digest="sha256:admission",
                plugin_version="2.0.0",
            ),
            "case_record_digest": case_record_digest,
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
            "usage": json.dumps(
                {
                    "usage_status": "reported",
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                    },
                }
            ).encode(),
            "latency": json.dumps(
                {
                    "schema_version": "tokenshare.response_bank_latency.v1",
                    "latency_ms": (
                        10 + replacement_slot
                        if source_api_latencies_ms is None
                        else source_api_latencies_ms[replacement_slot]
                    ),
                    "timing_source": "provider",
                }
            ).encode(),
            "pricing": json.dumps(
                {
                    "currency": "CNY",
                    "input_per_million_tokens": "0.5",
                    "output_per_million_tokens": "1.5",
                }
            ).encode(),
            "acquisition_attempt": json.dumps(
                {
                    "attempt_id": f"source-attempt-{replacement_slot}",
                    "attempt_index": replacement_slot,
                }
            ).encode(),
            "model_record": json.dumps({"model": "frozen-model"}).encode(),
        }
        if role_body_overrides is not None:
            objects.update(
                {
                    role: json.dumps(body, sort_keys=True).encode()
                    for role, body in role_body_overrides.items()
                }
            )
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


def test_fault_redelivery_projects_three_current_attempts_to_one_exp1_artifact(
    tmp_path,
) -> None:
    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("success",),
        contents=('{"exp1_wrong_answer":true}',),
        planned_ai_unit_id="exp1-range-0",
    )
    binding = freeze_projected_trace_source_binding(
        resolver,
        current_planned_ai_unit_id="exp3-range-0",
        current_sample_slot_index=2,
        source_entry_ids_by_current_attempt=("entry-0", "entry-0", "entry-0"),
        delivery_kinds_by_current_attempt=(
            "ordinary_attempt",
            "fault_redelivery",
            "fault_redelivery",
        ),
        redelivery_reasons_by_current_attempt=(
            None,
            "false_positive_requeue",
            "false_positive_requeue",
        ),
    )
    executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="exp3-replay",
    )

    deliveries = tuple(
        executor.execute(
            _bound_request(binding, ordinal=ordinal),
            submission_id=f"delivery-{ordinal}",
            submitted_at="ignored",
        )
        for ordinal in range(3)
    )

    assert executor.provider_call_count == 0
    assert [delivery.attempt_ordinal for delivery in deliveries] == [0, 1, 2]
    assert {delivery.entry_id for delivery in deliveries} == {"entry-0"}
    assert {delivery.inference_request_digest for delivery in deliveries} == {
        resolver.entry("entry-0").inference_request_digest
    }
    assert len({delivery.parser_input_digest for delivery in deliveries}) == 1
    assert binding.terminal_ordinary_ordinal == 0
    assert [item.to_dict() for item in binding.attempt_deliveries] == [
        {
            "current_attempt_ordinal": 0,
            "source_entry_id": "entry-0",
            "source_attempt_index": 0,
            "source_replacement_slot": 0,
            "delivery_kind": "ordinary_attempt",
            "redelivery_reason": None,
        },
        {
            "current_attempt_ordinal": 1,
            "source_entry_id": "entry-0",
            "source_attempt_index": 0,
            "source_replacement_slot": 0,
            "delivery_kind": "fault_redelivery",
            "redelivery_reason": "false_positive_requeue",
        },
        {
            "current_attempt_ordinal": 2,
            "source_entry_id": "entry-0",
            "source_attempt_index": 0,
            "source_replacement_slot": 0,
            "delivery_kind": "fault_redelivery",
            "redelivery_reason": "false_positive_requeue",
        },
    ]
    roundtrip = type(binding).from_dict(binding.to_dict())
    assert roundtrip == binding
    drifted = binding.to_dict()
    drifted["attempt_deliveries"][1]["source_attempt_index"] = 1
    with pytest.raises(ValueError, match="binding|source attempt identity"):
        type(binding).from_dict(drifted)


def test_missing_source_api_latency_stays_nullable_with_separate_protocol_delay(
    tmp_path,
) -> None:
    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("provider_failure",),
        contents=("provider failed before observed latency",),
        source_api_latencies_ms=(None,),
    )
    binding = _binding(resolver)
    executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="exp1-missing-latency",
    )

    delivery = executor.execute(
        _bound_request(binding, logical_start_ms=50),
        submission_id="delivery-0",
        submitted_at="ignored",
    )

    assert executor.provider_call_count == 0
    assert delivery.source_api_latency_ms is None
    assert delivery.source_api_latency_missing is True
    assert delivery.source_api_latency_missing_count == 1
    assert delivery.source_api_latency_ref.endswith(":entry-0:latency")
    assert delivery.protocol_operational_delay_ms == 1
    assert delivery.logical_finish_ms == 51
    body = delivery.to_dict()
    assert body["source_api_latency_ms"] is None
    assert body["protocol_operational_delay_ms"] == 1
    assert body.get("source_latency_ms") != 0
    assert type(delivery).from_dict(body) == delivery
    drifted = dict(body)
    drifted["source_api_latency_ref"] = "response-bank:drifted:entry-0:latency"
    with pytest.raises(ValueError, match="delivery_digest mismatch"):
        type(delivery).from_dict(drifted)


def _supervised_stopped_attempt_v2_roles() -> dict[str, dict[str, object]]:
    authority_digest = "sha256:" + "a" * 64
    stop_digest = "sha256:" + "b" * 64
    return {
        "provider_failure": {
            "schema_version": "tokenshare.response_bank_provider_failure.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "failure_kind": "no_response",
            "http_status": None,
            "message": "provider returned no terminal response before supervised stop",
            "raw_response_json": None,
            "transport_call_count": 1,
            "closure_provider_call_count": 0,
            "closure_authority_digest": authority_digest,
            "stop_evidence_digest": stop_digest,
        },
        "provenance": {
            "schema_version": "tokenshare.response_bank_provenance.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "provider_family": "deepseek",
            "entry_id": "deepseek_v4_pro_exp1_baseline",
            "provider_config_digest": "sha256:" + "c" * 64,
            "inference_request_digest": "sha256:" + "d" * 64,
            "normalized_absolute_endpoint": "https://api.deepseek.com/chat/completions",
            "transport_call_count": 1,
            "closure_provider_call_count": 0,
            "closure_authority_digest": authority_digest,
            "stop_evidence_digest": stop_digest,
            "secret_persisted": False,
            "receipt_digest": "sha256:" + "e" * 64,
        },
        "latency": {
            "schema_version": "tokenshare.response_bank_latency.v2",
            "evidence_kind": "supervised_stopped_attempt",
            "latency_ms": None,
            "latency_missing": True,
            "timing_source": "supervised_no_terminal_response",
            "observed_inflight_lower_bound_ms": 720_000,
            "unchanged_observation_window_ms": 120_000,
            "stop_evidence_digest": stop_digest,
        },
    }


def _hard_deadline_v2_roles() -> dict[str, dict[str, object]]:
    proof = {
        "schema_version": "tokenshare.ai_api_hard_deadline_quiescence.v1",
        "deadline_enforced": True,
        "hard_total_seconds": 600.0,
        "observed_wall_clock_ms": 600_123,
        "child_pid": 4242,
        "child_exit_code": -15,
        "terminate_attempted": True,
        "kill_attempted": False,
        "child_reaped": True,
        "network_start_acknowledged": True,
        "result_observed_before_deadline": False,
        "child_completed_before_parent_deadline": False,
        "result_commit_count": 0,
        "accepted_result_commit_count": 0,
        "late_result_rejected": False,
        "post_reap_late_result_absent": True,
        "parent_only_evidence_consumer": True,
        "ephemeral_files_secret_free": True,
        "request_file_sha256": "sha256:" + "1" * 64,
    }
    proof_digest = canonical_digest(proof)
    return {
        "provider_failure": {
            "schema_version": "tokenshare.response_bank_provider_failure.v2",
            "evidence_kind": "hard_deadline_child",
            "failure_kind": "no_response",
            "http_status": None,
            "message": "provider returned no response before hard deadline",
            "raw_response_json": None,
            "transport_call_count": 1,
            "hard_deadline_evidence": proof,
        },
        "provenance": {
            "schema_version": "tokenshare.response_bank_provenance.v2",
            "evidence_kind": "hard_deadline_child",
            "provider_family": "deepseek",
            "entry_id": "deepseek_v4_pro_exp1_baseline",
            "provider_config_digest": "sha256:" + "2" * 64,
            "inference_request_digest": "sha256:" + "3" * 64,
            "normalized_absolute_endpoint": "https://api.deepseek.com/chat/completions",
            "transport_call_count": 1,
            "secret_persisted": False,
            "receipt_digest": "sha256:" + "4" * 64,
            "hard_deadline_evidence_digest": proof_digest,
            "network_start_acknowledged": True,
            "result_observed_before_deadline": False,
            "late_result_rejected": False,
            "post_reap_late_result_absent": True,
            "child_reaped": True,
            "parent_only_evidence_consumer": True,
            "ephemeral_files_secret_free": True,
        },
        "latency": {
            "schema_version": "tokenshare.response_bank_latency.v2",
            "evidence_kind": "hard_deadline_child",
            "latency_ms": None,
            "latency_missing": True,
            "timing_source": "unknown_no_response",
            "hard_deadline_evidence_digest": proof_digest,
            "observed_wall_clock_ms": 600_123,
        },
    }


@pytest.mark.parametrize("drift", ("proof_not_quiescent", "proof_digest"))
def test_trace_reader_accepts_strict_v2_hard_deadline_and_rejects_proof_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    valid_roles = _hard_deadline_v2_roles()
    valid = _bank(
        tmp_path / "valid-hard",
        terminal_kinds=("provider_failure",),
        source_api_latencies_ms=(None,),
        role_body_overrides=valid_roles,
    )
    valid_binding = _binding(valid)
    TraceBackedExecutor(
        resolver=valid,
        bindings=(valid_binding,),
        current_run_id="hard-valid",
    ).execute(
        _bound_request(valid_binding),
        submission_id="delivery-valid",
        submitted_at="ignored",
    )

    drifted_roles = _hard_deadline_v2_roles()
    if drift == "proof_not_quiescent":
        proof = drifted_roles["provider_failure"]["hard_deadline_evidence"]
        assert isinstance(proof, dict)
        proof["post_reap_late_result_absent"] = False
        digest = canonical_digest(proof)
        drifted_roles["provenance"]["hard_deadline_evidence_digest"] = digest
        drifted_roles["latency"]["hard_deadline_evidence_digest"] = digest
    else:
        drifted_roles["latency"]["hard_deadline_evidence_digest"] = (
            "sha256:" + "f" * 64
        )
    drifted = _bank(
        tmp_path / "drifted-hard",
        terminal_kinds=("provider_failure",),
        source_api_latencies_ms=(None,),
        role_body_overrides=drifted_roles,
    )
    drifted_binding = _binding(drifted)
    with pytest.raises(ValueError, match="response bank .*v2|hard-deadline"):
        TraceBackedExecutor(
            resolver=drifted,
            bindings=(drifted_binding,),
            current_run_id="hard-drifted",
        ).execute(
            _bound_request(drifted_binding),
            submission_id="delivery-drifted",
            submitted_at="ignored",
        )


@pytest.mark.parametrize(
    ("role", "key", "drifted_value"),
    (
        ("provider_failure", "closure_provider_call_count", 1),
        ("provenance", "secret_persisted", True),
        ("provenance", "stop_evidence_digest", "sha256:" + "f" * 64),
        ("latency", "observed_inflight_lower_bound_ms", 719_999),
        ("latency", "unchanged_observation_window_ms", 119_999),
    ),
)
def test_trace_reader_accepts_strict_v2_closure_and_rejects_role_drift(
    tmp_path: Path,
    role: str,
    key: str,
    drifted_value: object,
) -> None:
    valid_roles = _supervised_stopped_attempt_v2_roles()
    valid = _bank(
        tmp_path / "valid",
        terminal_kinds=("provider_failure",),
        source_api_latencies_ms=(None,),
        role_body_overrides=valid_roles,
    )
    valid_binding = _binding(valid)
    delivery = TraceBackedExecutor(
        resolver=valid,
        bindings=(valid_binding,),
        current_run_id="v2-valid",
    ).execute(
        _bound_request(valid_binding),
        submission_id="delivery-valid",
        submitted_at="ignored",
    )
    assert delivery.source_api_latency_ms is None

    drifted_roles = _supervised_stopped_attempt_v2_roles()
    drifted_roles[role][key] = drifted_value
    drifted = _bank(
        tmp_path / "drifted",
        terminal_kinds=("provider_failure",),
        source_api_latencies_ms=(None,),
        role_body_overrides=drifted_roles,
    )
    drifted_binding = _binding(drifted)
    with pytest.raises(ValueError, match="response bank .*v2|supervised"):
        TraceBackedExecutor(
            resolver=drifted,
            bindings=(drifted_binding,),
            current_run_id="v2-drifted",
        ).execute(
            _bound_request(drifted_binding),
            submission_id="delivery-drifted",
            submitted_at="ignored",
        )


def test_fault_redelivery_does_not_duplicate_missing_source_latency_accounting(
    tmp_path,
) -> None:
    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("provider_failure",),
        contents=("provider failed before observed latency",),
        source_api_latencies_ms=(None,),
    )
    binding = freeze_projected_trace_source_binding(
        resolver,
        current_planned_ai_unit_id="exp3-range-0",
        current_sample_slot_index=0,
        source_entry_ids_by_current_attempt=("entry-0", "entry-0", "entry-0"),
        delivery_kinds_by_current_attempt=(
            "ordinary_attempt",
            "fault_redelivery",
            "fault_redelivery",
        ),
        redelivery_reasons_by_current_attempt=(
            None,
            "worker_death_saved_artifact_redelivery",
            "worker_death_saved_artifact_redelivery",
        ),
    )
    executor = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="exp3-missing-latency-redelivery",
    )

    deliveries = tuple(
        executor.execute(
            _bound_request(binding, ordinal=ordinal),
            submission_id=f"delivery-{ordinal}",
            submitted_at="ignored",
        )
        for ordinal in range(3)
    )

    assert executor.provider_call_count == 0
    assert [item.source_api_latency_ms for item in deliveries] == [None, None, None]
    assert [item.source_api_latency_missing_count for item in deliveries] == [1, 0, 0]
    assert len({item.source_api_latency_ref for item in deliveries}) == 1
    assert [item.protocol_operational_delay_ms for item in deliveries] == [1, 1, 1]


def test_parent_stage_persists_nullable_source_latency_without_zero_fallback(
    tmp_path,
) -> None:
    resolver = _bank(
        tmp_path / "bank",
        terminal_kinds=("provider_failure",),
        contents=("provider failed before observed latency",),
        source_api_latencies_ms=(None,),
    )
    binding = _binding(resolver)
    request = _bound_request(binding, logical_start_ms=12)
    delivery = TraceBackedExecutor(
        resolver=resolver,
        bindings=(binding,),
        current_run_id="exp1-missing-latency-parent-stage",
    ).execute(request, submission_id="delivery-0", submitted_at="ignored")
    delivery = delivery.with_child_completion(
        child_worker_id="worker-1", child_completion_sequence=1
    )
    stores = _stores(tmp_path / "current")

    record = commit_prepared_delivery(
        delivery=delivery,
        worker_completion=SimpleNamespace(
            fact=_completion(), request=request, failure_kind=None
        ),
        killed_worker_ids=(),
        active_lease=replace(
            _lease(),
            metadata={
                "binding_digest": binding.binding_digest,
                "attempt_ordinal": 0,
            },
        ),
        current_fencing_token="fence_trace",
        current_stores=replace(
            stores,
            trace_delivery_stager=TraceBackedParentStager(
                resolver=resolver,
                bindings=(binding,),
                domain_stage=object(),
            ),
        ),
    )

    provenance = json.loads(
        stores.artifact_store.read_bytes(
            record.core.current_provenance_ref
        ).decode("utf-8")
    )
    attribution = json.loads(
        stores.artifact_store.read_bytes(
            record.core.trace_attribution_refs[0]
        ).decode("utf-8")
    )
    for body in (provenance, attribution):
        assert body["source_api_latency_ms"] is None
        assert body["source_api_latency_missing"] is True
        assert body["source_api_latency_missing_count"] == 1
        assert body["source_api_latency_ref"].endswith(":entry-0:latency")
        assert body["protocol_operational_delay_ms"] == 1
        assert body.get("source_latency_ms") != 0
        assert body["current_provider_call_count"] == 0


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
