from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenshare.experiments import paper_formal_metrics
from tokenshare.experiments import paper_formal_evidence
from tokenshare.experiments.paper_formal_metrics import (
    publish_paper_formal_metric_drafts,
)
from tokenshare.experiments.paper_formal_evidence import (
    CanonicalLineageInput,
    FormalEvidenceStore,
    LineageSourceRecord,
    build_canonical_lineage_inputs,
    export_lineage_source_index,
    lineage_input_identity_digest,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_metric_registry import (
    PaperMetricRegistry,
    load_paper_metric_registry,
)
from tokenshare.executors.response_bank import CurrentTraceWrapper
from tokenshare.executors.trace_backed import (
    TraceReplacementBinding,
    TraceSourceBinding,
)
from tokenshare.local_runtime.contracts import PreparedTraceDelivery
from tokenshare.experiments.paper_models import ArtifactIdentitySnapshot, digest_json
from tokenshare.core.models import ProtocolConfig
from tokenshare.executors.contracts import EnvironmentRef, ExecutionRequest
from tokenshare.experiments.paper_models import (
    DirectRootExecutionBinding,
    LedgerEventIdentitySnapshot,
)
from tokenshare.plugins.contracts import OutputContract
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger


def _real_canonical_rows(tmp_path: Path, *, exp1_root=None):
    from tests.experiments import test_paper_exp1_metrics as exp1
    from tests.experiments import test_paper_exp2_metrics as exp2
    from tests.experiments import test_paper_exp3_metrics as exp3
    from tests.experiments import test_paper_exp4_metrics as exp4
    from tests.experiments import test_paper_exp5_metrics as exp5
    from tests.experiments import test_paper_metric_registry as registry_fixtures
    from tokenshare.experiments.paper_exp3_metrics import Exp3OnlineRecoveryInput

    exp1_root = exp1_root or exp1._not_started_row()
    online_direct = exp2._direct_row(
        tmp_path,
        root_id="task20-online-root",
        worker=1,
        evidence_class="online_real_provider",
    )
    online_recovery = Exp3OnlineRecoveryInput(
        recovery_source="validation_replacement",
        case_id="case-1",
        repeat_id=0,
        observations=(
            exp3._observation(
                "recovery-anchor",
                member_kind="exp3_online_recovery_identity",
                case_id="case-1",
                recovery_source="validation_replacement",
                current_replacement_attempt_id="attempt-1",
                provider_object_links=(),
            ),
            exp3._root("task20-exp3-root"),
        ),
    )
    return {
        "exp1_feasibility": (exp1_root,),
        "exp2_trace_scalability": exp2._trace_pair(tmp_path),
        "exp2_online_concurrency": (exp2._online(online_direct),),
        "exp3_trace_robustness": registry_fixtures._exp3_trace_payload(),
        "exp3_online_recovery": (online_recovery,),
        "exp4_ablation": (exp4._mode("FULL", exp4._root("task20-exp4-root")),),
        "experiment_5": (exp5._repeat(0),),
    }


def _executed_formal_lineage_fixture(output_root: Path):
    from tests.experiments import test_paper_direct_results as direct_fixtures
    from tests.experiments import test_paper_formal_evidence as formal_fixtures
    from tokenshare.experiments.paper_direct_results import build_canonical_direct_evidence

    inventory_row, condition_manifest, catalog = direct_fixtures._inventory_row(
        root_id="task20-executed-root",
        condition_id=formal_fixtures.CONDITION_A,
        case_id="task20-case",
        evidence_class="online_real_provider",
    )
    execution_id = "task20-execution"
    task_id = "task20-root-task"
    unit_id = "task20-root-unit"
    artifact_store = direct_fixtures.ArtifactStore(
        output_root.parent / f"{output_root.name}-task3-source"
    )
    ledger = direct_fixtures.EventLedger(
        artifact_store.root_path / "events" / "ledger.jsonl"
    )
    final_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-final",
        role="final_result",
        execution_id=execution_id,
        task_id=task_id,
        body={
            "schema_version": "factorization.prime_factorization_result.v1",
            "target_n": "91",
            "prime_factors": [
                {"prime": "7", "exponent": 1},
                {"prime": "13", "exponent": 1},
            ],
        },
    )
    parser_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-parser",
        role="parser_result",
        execution_id=execution_id,
        task_id=task_id,
    )
    verdict_ref = direct_fixtures._factor_domain_verdict_ref(
        store=artifact_store,
        row=inventory_row,
        execution_id=execution_id,
        task_id=task_id,
        root_unit_id=unit_id,
        final_ref=final_ref,
        correct=True,
    )
    current_refs = tuple(
        direct_fixtures._save_role_artifact(
            artifact_store,
            label=f"task20-{role}",
            role=role,
            execution_id=execution_id,
            task_id=task_id,
        )
        for role in (
            "request_body",
            "raw_output_or_provider_failure",
            "provenance",
            "usage_status",
            "latency",
            "pricing",
            "provider_attempt",
            "model_record",
        )
    )
    resource_ref = direct_fixtures._save_role_artifact(
        artifact_store,
        label="task20-resource",
        role="actual_resource_book",
        execution_id=execution_id,
        task_id=task_id,
    )
    base_unit = {"unit_id": unit_id, "task_id": task_id, "state": "Ready"}
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.TASK_UNIT_CREATED,
        suffix="created",
        payload={"task_unit": base_unit},
    )
    for event_type, state, suffix in (
        (direct_fixtures.EventType.EXECUTION_REQUEST_RECORDED, "Running", "request"),
        (direct_fixtures.EventType.EXECUTION_SUBMISSION_RECORDED, "Submitted", "submission"),
        (direct_fixtures.EventType.VERIFICATION_RECORDED, "Verified", "verification"),
    ):
        direct_fixtures._append_event(
            ledger,
            task_id=task_id,
            event_type=event_type,
            suffix=suffix,
            payload={
                "schema_version": f"paper_direct_fixture.{suffix}.v1",
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": "task20-attempt",
                "state": state,
            },
        )
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.CANONICAL_OUTPUTS_BOUND,
        suffix="canonical",
        payload={
            "canonical_selection": {
                "unit_id": unit_id,
                "canonical_output_refs": {"answer": final_ref.to_dict()},
            }
        },
    )
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.MERGE_RECORDED,
        suffix="merge",
        payload={
            "schema_version": "phase5.merge_recorded.v1",
            "task_id": task_id,
            "parent_unit_id": unit_id,
            "merge_output_refs": {"answer": final_ref.to_dict()},
            "merge_record": {
                "task_id": task_id,
                "parent_unit_id": unit_id,
                "merge_output_refs": {"answer": final_ref.to_dict()},
            },
        },
    )
    direct_fixtures._append_event(
        ledger,
        task_id=task_id,
        event_type=direct_fixtures.EventType.TASK_UNIT_STATE_CHANGED,
        suffix="terminal",
        payload={"task_unit": {**base_unit, "state": "Completed"}},
    )
    runtime_result = direct_fixtures.project_protocol_run(
        run_id=execution_id,
        task_id=task_id,
        root_unit_id=unit_id,
        event_ledger=ledger,
        artifact_store=artifact_store,
    )
    kwargs = {
        "inventory_row": inventory_row,
        "execution_id": execution_id,
        "event_ledger": ledger,
        "artifact_store": artifact_store,
        "runtime_result": runtime_result,
        "final_result_ref": final_ref,
        "parser_refs": (parser_ref,),
        "verifier_checker_refs": (verdict_ref,),
        "current_provider_object_refs": current_refs,
        "actual_resource_book_ref": resource_ref,
    }
    evidence = build_canonical_direct_evidence(**kwargs)
    direct_result = direct_fixtures._project(
        direct_fixtures._inventory_manifest(inventory_row),
        (condition_manifest,),
        (catalog,),
        {inventory_row.preregistered_root_run_id: evidence},
    ).rows[0]
    binding = direct_result.execution_binding
    assert binding is not None
    def replace_experiment_identity(value):
        if isinstance(value, dict):
            return {
                key: replace_experiment_identity(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_experiment_identity(item) for item in value]
        return "experiment_1" if value == formal_fixtures.EXPERIMENT_A else value

    bodies = replace_experiment_identity(
        formal_fixtures._suite_bodies_with_root_ids((binding.task_id,))
    )
    store = FormalEvidenceStore.initialize(
        output_root=output_root,
        **bodies,
        capturing=False,
    )
    run_artifact_root = (
        output_root
        / "experiments"
        / direct_result.experiment_id
        / "runs"
        / direct_result.condition_id
        / str(direct_result.repeat_id)
        / "artifacts"
    )
    run_artifact_root.mkdir(parents=True, exist_ok=True)
    source_refs = {
        ref.artifact_id: ref
        for ref in (
            *runtime_result.artifact_refs,
            kwargs["final_result_ref"],
            *kwargs["parser_refs"],
            *kwargs["verifier_checker_refs"],
            *kwargs.get("current_provider_object_refs", ()),
            *(() if kwargs.get("actual_resource_book_ref") is None else (kwargs["actual_resource_book_ref"],)),
        )
    }
    artifact_records = []
    for index, snapshot in enumerate(direct_result.artifact_refs):
        source_ref = source_refs[snapshot.artifact_id]
        content = artifact_store.read_bytes(source_ref)
        artifact_path = run_artifact_root / f"{index:03d}-{snapshot.artifact_id}.bin"
        artifact_path.write_bytes(content)
        artifact_records.append(
            {
                "artifact_id": snapshot.artifact_id,
                "experiment_id": direct_result.experiment_id,
                "condition_id": direct_result.condition_id,
                "repeat_id": direct_result.repeat_id,
                "task_id": binding.task_id,
                "path": artifact_path.relative_to(output_root).as_posix(),
                "content_hash": snapshot.content_hash,
                "size_bytes": snapshot.size_bytes,
                "source_artifact_ref": source_ref.to_dict(),
            }
        )
    runtime_identity = {
        "schema_version": "tokenshare.paper_runtime_generation_identity.v1",
        "run_id": binding.execution_id,
        "task_id": binding.task_id,
        "root_unit_id": binding.root_unit_id,
        "ledger_digest": binding.ledger_digest,
    }
    execution_identity = formal_fixtures._execution_version_identity()
    execution_identity["runtime_generation_identity_digest"] = formal_fixtures._sha256(
        formal_fixtures._canonical_json(runtime_identity).encode("utf-8")
    )
    task = {
        **formal_fixtures._task(
            direct_result.experiment_id,
            direct_result.condition_id,
            task_id=binding.task_id,
        ),
        "case_id": direct_result.case_id,
        "preregistered_root_run_id": direct_result.preregistered_root_run_id,
        "runtime_generation_identity": runtime_identity,
        "execution_version_identity": execution_identity,
    }
    attempt = formal_fixtures._attempt(
        direct_result.experiment_id,
        direct_result.condition_id,
        task_id=binding.task_id,
    )
    attempt["attempt_id"] = f"attempt:{direct_result.preregistered_root_run_id}"
    store.checkpoint_root(
        experiment_id=direct_result.experiment_id,
        condition=formal_fixtures._condition(
            direct_result.experiment_id,
            direct_result.condition_id,
        ),
        repeat_id=direct_result.repeat_id,
        task=task,
        attempts=(attempt,),
        faults=(),
        events=tuple(event.to_dict() for event in ledger.read_all()),
        artifact_refs=tuple(artifact_records),
    )
    return evidence, direct_result


_MISSING_ORDINAL = object()


def _trace_request_lineage_fixture(
    root: Path,
    *,
    mutation: str | None = None,
    paper_attempt_ordinal: object = _MISSING_ORDINAL,
):
    artifact_store = ArtifactStore(root)
    event_ledger = EventLedger(root / "events" / "event_log.jsonl")
    protocol_engine = ProtocolEngine(
        event_ledger=event_ledger,
        artifact_store=artifact_store,
        protocol_config=ProtocolConfig.default(
            config_id="lineage-request-fixture",
            artifact_store_uri="artifacts",
            event_log_uri="events/event_log.jsonl",
        ),
    )
    task_id = "task-1"
    run_id = "run-1"
    unit_id = "unit-1"
    attempt_id = "attempt-1"
    lease_id = "lease-1"
    entry_id = "entry-1"
    manifest_digest = digest_json({"manifest": 1})
    inference_digest = digest_json({"inference": 1})
    parser_digest = digest_json({"parser": 1})
    locator_digest = digest_json({"request_body": 1})
    binding = TraceSourceBinding.create(
        planned_ai_unit_id="planned-1",
        sample_slot_index=0,
        bank_root_id="bank-1",
        manifest_digest=manifest_digest,
        replacements=(
            TraceReplacementBinding(
                replacement_slot=0,
                entry_id=entry_id,
                inference_request_digest=inference_digest,
            ),
        ),
        source_evidence_class="approved_real_api_acquisition",
    )
    locator = {
        "bank_root_id": "bank-1",
        "manifest_digest": manifest_digest,
        "entry_id": entry_id,
        "object_role": "request_body",
        "object_digest": locator_digest,
    }
    delivery = PreparedTraceDelivery.create(
        current_run_id=run_id,
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        attempt_ordinal=0,
        binding_digest=binding.binding_digest,
        inference_request_digest=inference_digest,
        bank_root_id="bank-1",
        manifest_digest=manifest_digest,
        entry_id=entry_id,
        source_terminal_kind="success",
        source_bank_object_locators=(locator,),
        logical_start_ms=1,
        source_latency_ms=1,
        parser_input_media_type="application/json",
        parser_input_digest=parser_digest,
        child_worker_id="worker-1",
        child_completion_sequence=0,
    )
    now = "2026-08-14T00:00:00Z"
    request = ExecutionRequest(
        request_id="request-1",
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        lease_id=lease_id,
        fencing_token="fence-1",
        plugin={"plugin_id": "formal-plugin", "plugin_version": "v1"},
        executor={"executor_id": "trace-executor", "executor_version": "v1"},
        registry_snapshot_id="registry-1",
        allocation_decision={"worker_id": "worker-1"},
        capability_snapshot={"formal": True},
        task_unit_snapshot={"unit_id": unit_id, "task_id": task_id},
        input_artifact_refs={},
        output_contract=OutputContract(
            output_contract_id="formal-output",
            required_outputs=["candidate"],
            output_schema_refs={"candidate": {"schema_id": "candidate.v1"}},
            raw_output_policy={"allowed": True},
        ),
        hard_requirements={"formal": True},
        soft_hints={
            "planned_ai_unit_id": binding.planned_ai_unit_id,
            "sample_slot_index": binding.sample_slot_index,
            "replacement_slot": 0,
            "trace_source_binding_digest": binding.binding_digest,
            "trace_inference_request_digest": inference_digest,
        },
        environment_ref=EnvironmentRef(
            environment_id="env-1",
            environment_digest=digest_json({"environment": 1}),
            runtime="python",
            tool_versions={"python": "3"},
            resource_limits={"memory_mb": 64},
            fixture_profile_digest=digest_json({"fixture": 1}),
            seed=1,
            clock_policy="fixed",
            created_at=now,
        ),
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={"timeout_seconds": 60},
        created_at=now,
        attempt_ordinal=0,
        source_binding_digest=binding.binding_digest,
    )
    request_flow = protocol_engine.record_execution_request(
        request=request,
        correlation_id="lineage-request-fixture",
    )
    request_body = request.to_dict()
    request_mutations = {
        "request_ordinal": ("attempt_ordinal", 1),
        "request_ordinal_bool": ("attempt_ordinal", False),
        "request_task": ("task_id", "other-task"),
        "request_unit": ("unit_id", "other-unit"),
        "request_attempt": ("attempt_id", "other-attempt"),
        "request_lease": ("lease_id", "other-lease"),
        "request_binding": ("source_binding_digest", digest_json({"other": 1})),
    }
    if mutation in request_mutations:
        field_name, value = request_mutations[mutation]
        request_body[field_name] = value
    hint_mutations = {
        "hint_planned": ("planned_ai_unit_id", "other-planned"),
        "hint_replacement": ("replacement_slot", 1),
        "hint_replacement_bool": ("replacement_slot", False),
        "hint_sample": ("sample_slot_index", 1),
        "hint_sample_bool": ("sample_slot_index", False),
        "hint_binding": ("trace_source_binding_digest", digest_json({"other": 2})),
        "hint_inference": ("trace_inference_request_digest", digest_json({"other": 3})),
    }
    if mutation in hint_mutations:
        field_name, value = hint_mutations[mutation]
        request_body["soft_hints"][field_name] = value
    if mutation == "request_missing_required":
        del request_body["fencing_token"]
    if mutation == "request_invalid_nested":
        del request_body["environment_ref"]["runtime"]

    def artifact_record(ref):
        body = ref.to_dict()
        return {
            "path": ref.uri,
            "content_hash": ref.content_hash,
            "size_bytes": ref.size_bytes,
            "source_artifact_ref": body,
        }

    body_mutations = set(request_mutations) | set(hint_mutations) | {
        "request_missing_required",
        "request_invalid_nested",
    }
    request_ref = request_flow.request_ref
    if mutation in body_mutations:
        mutated_store = ArtifactStore(root, artifact_dir_name="mutated-artifacts")
        request_ref = mutated_store.save_json(
            request_body,
            artifact_id=request.request_id,
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={"task_id": task_id, "attempt_id": attempt_id},
            created_at=now,
        )
    request_record = artifact_record(request_ref)
    event_request_ref = request_ref.to_dict()
    records = [request_record]
    if mutation == "request_ref":
        other_ref = artifact_store.save_json(
            request_body,
            artifact_id="request-other",
            artifact_type="ExecutionRequest",
            artifact_schema_id="phase3.execution_request",
            artifact_schema_version="v1",
            source={"kind": "protocol_engine"},
            metadata={},
            created_at=now,
        )
        event_request_ref = other_ref.to_dict()
        records.append(artifact_record(other_ref))

    native_wrapper_ref = artifact_store.save_external_trace_wrapper(
        delivery.to_dict(),
        artifact_id="wrapper-1",
        created_at=now,
    )
    canonical_wrapper_ref = native_wrapper_ref.to_dict()
    canonical_wrapper_ref["source"] = {
        "kind": "canonical_direct_runtime_projection",
        "source_artifact_ref": native_wrapper_ref.to_dict(),
    }
    records.insert(
        0,
        {
            "path": native_wrapper_ref.uri,
            "content_hash": native_wrapper_ref.content_hash,
            "size_bytes": native_wrapper_ref.size_bytes,
            "source_artifact_ref": canonical_wrapper_ref,
        },
    )

    request_event = request_flow.event.to_dict()
    request_event["payload"]["request_ref"] = event_request_ref
    request_event["payload"]["request_digest"] = event_request_ref["content_hash"]
    envelope_mutations = {
        "event_object_type": ("object_type", "OtherObject"),
        "event_object_id": ("object_id", "other-request"),
    }
    if mutation in envelope_mutations:
        field_name, value = envelope_mutations[mutation]
        request_event[field_name] = value
    payload_mutations = {
        "event_payload_schema": ("schema_version", "other.request_record.v1"),
        "event_request_id": ("request_id", "other-request"),
        "event_task": ("task_id", "other-task"),
        "event_unit": ("unit_id", "other-unit"),
        "event_attempt": ("attempt_id", "other-attempt"),
        "event_lease": ("lease_id", "other-lease"),
    }
    if mutation in payload_mutations:
        field_name, value = payload_mutations[mutation]
        request_event["payload"][field_name] = value

    if mutation == "duplicate_event":
        event_ledger.append(
            event_type="EXECUTION_REQUEST_RECORDED",
            object_type="ExecutionRequest",
            object_id=request.request_id,
            task_id=task_id,
            actor={"kind": "protocol_engine"},
            correlation_id="lineage-request-fixture",
            idempotency_key="execution_request:request-1:duplicate",
            payload=dict(request_flow.event.payload),
            occurred_at=now,
        )
    commit_event = event_ledger.append(
        event_type="TRACE_DELIVERY_COMMITTED.v1",
        object_type="CurrentTraceWrapper",
        object_id=native_wrapper_ref.artifact_id,
        task_id=task_id,
        actor={"kind": "trace_executor"},
        correlation_id="lineage-request-fixture",
        idempotency_key="trace_delivery:attempt-1",
        payload={
            "attempt_id": attempt_id,
            "current_wrapper_ref": native_wrapper_ref.to_dict(),
        },
        occurred_at=now,
    )
    wrapper = CurrentTraceWrapper(
        current_run_id=run_id,
        current_task_id=task_id,
        current_unit_id=unit_id,
        current_attempt_id=attempt_id,
        attempt_ordinal=0,
        bank_root_id="bank-1",
        manifest_digest=manifest_digest,
        root_binding_marker_digest=digest_json({"root": 1}),
        inference_request_digest=inference_digest,
        entry_id=entry_id,
        locator_digests={"request_body": locator_digest},
        logical_started_at="logical:1",
        logical_finished_at="logical:2",
        source_latency_ms=1,
        current_parse_ref=None,
        current_verifier_ref=None,
        current_checker_ref=None,
        current_canonical_ref=None,
        current_ledger_ref=commit_event.event_id,
    )

    resource_source = {
        "kind": "canonical_trace_wrapper_role_projection",
        "role": "trace_resource_book",
        "task_id": task_id,
        "execution_id": run_id,
        "source_artifact_ref": native_wrapper_ref.to_dict(),
    }
    resource_ref = artifact_store.save_json(
        paper_formal_evidence._current_trace_wrapper_body(wrapper),
        artifact_id="resource-book-1",
        artifact_type="CurrentTraceWrapper",
        artifact_schema_id="tokenshare.current_trace_wrapper",
        artifact_schema_version="v1",
        source=resource_source,
        metadata={},
        created_at=now,
    )
    records.append(artifact_record(resource_ref))
    resource_snapshot = ArtifactIdentitySnapshot(
        artifact_id=resource_ref.artifact_id,
        artifact_type=resource_ref.artifact_type,
        uri=resource_ref.uri,
        content_hash=resource_ref.content_hash,
        size_bytes=resource_ref.size_bytes,
        media_type=resource_ref.media_type,
        artifact_schema_id=resource_ref.artifact_schema_id,
        artifact_schema_version=resource_ref.artifact_schema_version,
        source_role="trace_resource_book",
        source_task_id=task_id,
        source_execution_id=run_id,
        created_at=resource_ref.created_at,
    )
    attempt = {
        "attempt_id": attempt_id,
        "task_id": task_id,
        "unit_id": unit_id,
        "planned_ai_unit_id": binding.planned_ai_unit_id,
        "trace_source_binding_digest": binding.binding_digest,
        "request_ref": request_ref.to_dict(),
    }
    if paper_attempt_ordinal is not _MISSING_ORDINAL:
        attempt["attempt_ordinal"] = paper_attempt_ordinal
    events = [
        event.to_dict()
        for event in event_ledger.read_all()
        if mutation != "missing_event"
        or event.event_type != "EXECUTION_REQUEST_RECORDED"
    ]
    for index, event in enumerate(events):
        if event["event_id"] == request_flow.event.event_id:
            events[index] = request_event
    if mutation == "missing_artifact":
        records.remove(request_record)
    if mutation == "duplicate_artifact":
        records.append(dict(request_record))
    direct = SimpleNamespace(
        trace_resource_book_ref=resource_snapshot,
        source_bank_object_locators=(SimpleNamespace(**locator),),
    )
    event_snapshots = tuple(
        LedgerEventIdentitySnapshot(
            event_seq=event.event_seq,
            event_id=event.event_id,
            event_type=str(event.event_type.value if hasattr(event.event_type, "value") else event.event_type),
            event_hash=event.event_hash,
            prev_event_hash=event.prev_event_hash,
            task_id=event.task_id or "",
            object_type=event.object_type,
            object_id=event.object_id,
        )
        for event in event_ledger.read_all()
    )
    ledger_digest = digest_json([value.to_dict() for value in event_snapshots])
    binding_body = {
        "schema_version": "tokenshare.direct_root_execution_binding.v1",
        "preregistered_root_run_id": "root-1",
        "execution_id": run_id,
        "task_id": task_id,
        "root_unit_id": unit_id,
        "ledger_digest": ledger_digest,
        "events": [value.to_dict() for value in event_snapshots],
    }
    execution_binding = DirectRootExecutionBinding(
        preregistered_root_run_id="root-1",
        task_id=task_id,
        execution_id=run_id,
        root_unit_id=unit_id,
        ledger_digest=ledger_digest,
        events=event_snapshots,
        binding_digest=digest_json(binding_body),
    )
    return {
        "store": FormalEvidenceStore(root),
        "direct": direct,
        "binding": execution_binding,
        "wrappers": (wrapper,),
        "trace_source_bindings": (binding,),
        "attempt_records": (attempt,),
        "events": tuple(events),
        "persisted_artifact_records": tuple(records),
    }


@pytest.mark.parametrize(
    "paper_attempt_ordinal",
    (_MISSING_ORDINAL, 0),
    ids=("historical-missing", "explicit-match"),
)
def test_trace_resource_book_recovers_historical_missing_attempt_ordinal_from_verified_request(
    tmp_path: Path,
    paper_attempt_ordinal: object,
) -> None:
    consumptions = paper_formal_evidence._validate_persisted_trace_resource_book(
        **_trace_request_lineage_fixture(
            tmp_path / "accepted",
            paper_attempt_ordinal=paper_attempt_ordinal,
        )
    )

    assert len(consumptions) == 1
    assert consumptions[0].attempt_ordinal == 0


@pytest.mark.parametrize(
    "mutation,paper_attempt_ordinal",
    (
        ("request_ref", _MISSING_ORDINAL),
        ("missing_event", _MISSING_ORDINAL),
        ("duplicate_event", _MISSING_ORDINAL),
        ("missing_artifact", _MISSING_ORDINAL),
        ("duplicate_artifact", _MISSING_ORDINAL),
        ("event_object_type", _MISSING_ORDINAL),
        ("event_object_id", _MISSING_ORDINAL),
        ("event_payload_schema", _MISSING_ORDINAL),
        ("event_request_id", _MISSING_ORDINAL),
        ("event_task", _MISSING_ORDINAL),
        ("event_unit", _MISSING_ORDINAL),
        ("event_attempt", _MISSING_ORDINAL),
        ("event_lease", _MISSING_ORDINAL),
        ("request_ordinal", _MISSING_ORDINAL),
        ("request_ordinal_bool", _MISSING_ORDINAL),
        ("request_task", _MISSING_ORDINAL),
        ("request_unit", _MISSING_ORDINAL),
        ("request_attempt", _MISSING_ORDINAL),
        ("request_lease", _MISSING_ORDINAL),
        ("request_binding", _MISSING_ORDINAL),
        ("request_missing_required", _MISSING_ORDINAL),
        ("request_invalid_nested", _MISSING_ORDINAL),
        ("hint_planned", _MISSING_ORDINAL),
        ("hint_replacement", _MISSING_ORDINAL),
        ("hint_replacement_bool", _MISSING_ORDINAL),
        ("hint_sample", _MISSING_ORDINAL),
        ("hint_sample_bool", _MISSING_ORDINAL),
        ("hint_binding", _MISSING_ORDINAL),
        ("hint_inference", _MISSING_ORDINAL),
        (None, 1),
    ),
)
def test_trace_resource_book_rejects_request_dispatch_identity_mismatch(
    tmp_path: Path,
    mutation: str | None,
    paper_attempt_ordinal: object,
) -> None:
    with pytest.raises(ValueError, match="request|ordinal|identity|artifact"):
        paper_formal_evidence._validate_persisted_trace_resource_book(
            **_trace_request_lineage_fixture(
                tmp_path / (mutation or "paper-ordinal"),
                mutation=mutation,
                paper_attempt_ordinal=paper_attempt_ordinal,
            )
        )


def test_formal_metrics_delegates_without_rederiving_formula(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    received = []
    original = PaperMetricRegistry.project_all

    def recording_project_all(self, inputs, *, global_infra_invalid=False):
        received.append((self, inputs, global_infra_invalid))
        return original(
            self,
            inputs,
            global_infra_invalid=global_infra_invalid,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", recording_project_all)

    publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        global_infrastructure_valid=False,
        registry=registry,
        contract=contract,
    )

    assert received == [(registry, canonical_rows, True)]
    with pytest.raises(TypeError, match="registry must be PaperMetricRegistry"):
        publish_paper_formal_metric_drafts(
            tmp_path / "fake",
            canonical_rows,
            registry=object(),
            contract=contract,
        )
    wrong_contracts = (
        replace(contract, contract_id="wrong-contract"),
        replace(contract, contract_digest="sha256:" + "f" * 64),
        replace(contract, pipeline_profile_id="wrong-profile"),
    )
    for wrong_contract in wrong_contracts:
        with pytest.raises(ValueError, match="registry contract identity"):
            publish_paper_formal_metric_drafts(
                tmp_path / "wrong-contract",
                canonical_rows,
                registry=registry,
                contract=wrong_contract,
            )
    source = inspect.getsource(paper_formal_metrics)
    for forbidden in (
        "recompute_metric",
        "_rate(",
        "_quantile(",
    ):
        assert forbidden not in source

    from tests.experiments.test_paper_exp1_metrics import _not_started_row

    direct = _not_started_row()
    with pytest.raises(TypeError):
        CanonicalLineageInput(
            direct_result=direct,
            input_identity_digest=lineage_input_identity_digest(canonical_rows),
        )
    with pytest.raises(ValueError, match="executed|canonical factory|provenance"):
        build_canonical_lineage_inputs(
            canonical_rows,
            canonical_runtime_evidence=(),
            requested_root_ids=(direct.preregistered_root_run_id,),
        )
    with pytest.raises(TypeError, match="CanonicalLineageInput"):
        export_lineage_source_index(
            FormalEvidenceStore(tmp_path / "mapping-rejected"),
            ({"direct_result": direct},),
            input_identity_digest=lineage_input_identity_digest(canonical_rows),
        )
    exporter_source = inspect.getsource(export_lineage_source_index)
    for forbidden in ("._conditions", "_logical_lineage_records", "_typed_payloads", "_collect_direct_results"):
        assert forbidden not in exporter_source

    evidence, executed_root = _executed_formal_lineage_fixture(tmp_path / "real-formal")
    real_rows = _real_canonical_rows(
        tmp_path / "real-fixtures",
        exp1_root=executed_root,
    )
    foreign_rows = _real_canonical_rows(tmp_path / "foreign-fixtures")
    with pytest.raises(ValueError, match="reachable|canonical inputs"):
        build_canonical_lineage_inputs(
            foreign_rows,
            canonical_runtime_evidence=(evidence,),
            requested_root_ids=(executed_root.preregistered_root_run_id,),
        )
    manual_evidence = type(evidence)._from_validated(
        **{
            name: getattr(evidence, name)
            for name in evidence.__dataclass_fields__
            if name != "_producer_validated"
        }
    )
    with pytest.raises(ValueError, match="canonical factory"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(manual_evidence,),
        )
    valid_lineage_inputs = build_canonical_lineage_inputs(
        real_rows,
        canonical_runtime_evidence=(evidence,),
    )
    closure_store = FormalEvidenceStore(tmp_path / "real-formal")
    closure = closure_store.load_logical_run_records(
        experiment_id=executed_root.experiment_id,
        condition_id=executed_root.condition_id,
        repeat_id=executed_root.repeat_id,
    )
    mutated_events = {
        **closure,
        "events": [
            {**closure["events"][0], "event_hash": "sha256:" + "f" * 64},
            *closure["events"][1:],
        ],
    }
    monkeypatch.setattr(
        closure_store,
        "load_logical_run_records",
        lambda **_: mutated_events,
    )
    with pytest.raises(ValueError, match="ledger event identity"):
        export_lineage_source_index(
            closure_store,
            valid_lineage_inputs,
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    artifact_store_view = FormalEvidenceStore(tmp_path / "real-formal")
    mutated_artifact = dict(closure["artifacts"][0])
    mutated_artifact["source_artifact_ref"] = {
        **mutated_artifact["source_artifact_ref"],
        "content_hash": "sha256:" + "e" * 64,
    }
    mutated_artifacts = {
        **closure,
        "artifacts": [mutated_artifact, *closure["artifacts"][1:]],
    }
    monkeypatch.setattr(
        artifact_store_view,
        "load_logical_run_records",
        lambda **_: mutated_artifacts,
    )
    with pytest.raises(ValueError, match="artifact identity"):
        export_lineage_source_index(
            artifact_store_view,
            valid_lineage_inputs,
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    forged_lineage = CanonicalLineageInput._from_validated(
        **{
            name: getattr(valid_lineage_inputs[0], name)
            for name in valid_lineage_inputs[0].__dataclass_fields__
            if name != "_producer_validated"
        }
    )
    with pytest.raises(ValueError, match="canonical lineage factory"):
        export_lineage_source_index(
            FormalEvidenceStore(tmp_path / "real-formal"),
            (forged_lineage,),
            input_identity_digest=lineage_input_identity_digest(real_rows),
        )
    from tests.experiments.test_paper_formal_evidence import _trace_classification_body
    from tokenshare.executors.response_bank import CurrentTraceWrapper
    from tokenshare.executors.trace_backed import TraceSourceBinding
    from tokenshare.experiments.paper_models import PaperEvidenceEligibilityFacts

    trace_facts = PaperEvidenceEligibilityFacts.from_mapping(_trace_classification_body())
    trace_wrapper = CurrentTraceWrapper.from_dict(trace_facts.current_lifecycle_refs[0])
    trace_binding = TraceSourceBinding.from_dict(trace_facts.trace_source_bindings[0])
    with pytest.raises(ValueError, match="crosses requested lineage root"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={"foreign-root": (trace_wrapper,)},
        )
    with pytest.raises(ValueError, match="wrappers do not match"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (
                    replace(trace_wrapper, current_task_id="cross-root-task"),
                )
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (trace_binding,)
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    with pytest.raises(ValueError, match="bindings do not match"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (trace_wrapper,)
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (
                    TraceSourceBinding.create(
                        planned_ai_unit_id="cross-root-unit",
                        sample_slot_index=trace_binding.sample_slot_index,
                        bank_root_id=trace_binding.bank_root_id,
                        manifest_digest=trace_binding.manifest_digest,
                        replacements=trace_binding.replacements,
                        source_evidence_class=trace_binding.source_evidence_class,
                    ),
                )
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    with pytest.raises(ValueError, match="evidence class mismatch"):
        build_canonical_lineage_inputs(
            real_rows,
            canonical_runtime_evidence=(evidence,),
            current_trace_wrappers_by_root={
                executed_root.preregistered_root_run_id: (trace_wrapper,)
            },
            trace_source_bindings_by_root={
                executed_root.preregistered_root_run_id: (trace_binding,)
            },
            eligibility_facts_by_root={
                executed_root.preregistered_root_run_id: trace_facts
            },
        )
    real = publish_paper_formal_metric_drafts(
        tmp_path / "real-formal",
        real_rows,
        registry=registry,
        contract=contract,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(executed_root.preregistered_root_run_id,),
    )
    replay = publish_paper_formal_metric_drafts(
        tmp_path / "real-formal",
        real_rows,
        registry=registry,
        contract=contract,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(executed_root.preregistered_root_run_id,),
    )
    assert len(real.table_drafts) == 8
    assert all(draft.rows for draft in real.table_drafts)
    assert any(
        observation.table_id == "exp3_online_recovery"
        for observation in real.metric_observations
    )
    assert len(real.metric_observations) == sum(
        len(row.cells) for draft in real.table_drafts for row in draft.rows
    )
    assert real.observations_digest == replay.observations_digest
    assert real.metrics_digest == replay.metrics_digest
    assert real.blocked_table_ids
    assert real.lineage_source_index.get(executed_root.preregistered_root_run_id) is not None


def test_formal_metric_jsonl_stream_preserves_canonical_bytes_and_digest(
    tmp_path: Path,
) -> None:
    class Payload:
        def __init__(self, value):
            self.value = value
            self.calls = 0

        def to_dict(self):
            self.calls += 1
            return self.value

    records = (
        Payload({"z": "中文", "a": 1}),
        Payload({"nested": {"b": 2, "a": [3, 4]}}),
    )
    payloads = [record.value for record in records]
    expected_bytes = "".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for payload in payloads
    ).encode("utf-8")
    expected_digest = paper_formal_metrics._digest({"records": payloads})
    output = tmp_path / "streamed.jsonl"

    actual_digest = paper_formal_metrics._atomic_write_jsonl_records(output, records)

    assert output.read_bytes() == expected_bytes
    assert actual_digest == expected_digest
    assert [record.calls for record in records] == [1, 1]


def test_formal_metric_lineage_digest_is_materialized_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.experiments import paper_formal_evidence
    from tokenshare.experiments.paper_direct_results import PaperDirectRootResult

    evidence, executed_root = _executed_formal_lineage_fixture(tmp_path / "formal")
    canonical_rows = _real_canonical_rows(
        tmp_path / "fixtures",
        exp1_root=executed_root,
    )
    digest_calls = 0
    original_digest = paper_formal_evidence.lineage_input_identity_digest

    def counting_digest(value):
        nonlocal digest_calls
        digest_calls += 1
        return original_digest(value)

    monkeypatch.setattr(
        paper_formal_evidence,
        "lineage_input_identity_digest",
        counting_digest,
    )
    monkeypatch.setattr(
        paper_formal_metrics,
        "lineage_input_identity_digest",
        counting_digest,
    )
    publish_paper_formal_metric_drafts(
        tmp_path / "formal",
        canonical_rows,
        canonical_runtime_evidence=(evidence,),
        requested_lineage_root_ids=(executed_root.preregistered_root_run_id,),
    )

    assert digest_calls == 1


def test_metric_lineage_export_partitions_by_bound_experiment_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from tokenshare.experiments import paper_formal_metrics

    inputs = (
        SimpleNamespace(
            direct_result=SimpleNamespace(
                experiment_id="exp1_real_ai_feasibility",
                preregistered_root_run_id="root-exp1",
            )
        ),
        SimpleNamespace(
            direct_result=SimpleNamespace(
                experiment_id="exp5_real_ai_model_endpoint_comparison",
                preregistered_root_run_id="root-exp5",
            )
        ),
    )
    roots = {
        "exp1_real_ai_feasibility": tmp_path / "trace",
        "exp5_real_ai_model_endpoint_comparison": tmp_path / "exp5",
    }
    calls = []

    def export(store, values, *, input_identity_digest):
        experiment_id = values[0].direct_result.experiment_id
        calls.append((store.output_root, experiment_id, input_identity_digest))
        record = LineageSourceRecord.create(
            member_id=experiment_id,
            evidence_class=(
                "online_real_provider"
                if experiment_id.startswith("exp5")
                else "real_model_trace_protocol_run"
            ),
        )
        return paper_formal_metrics.LineageSourceIndex.create(
            records=(record,),
            input_identity_digest=input_identity_digest,
        )

    monkeypatch.setattr(paper_formal_metrics, "export_lineage_source_index", export)

    index = paper_formal_metrics._export_metric_lineage_source_index(
        output_root=tmp_path / "unused",
        canonical_lineage_inputs=inputs,
        input_identity_digest="sha256:" + "1" * 64,
        evidence_roots_by_experiment=roots,
    )

    assert sorted(calls, key=lambda value: str(value[0])) == sorted([
        (
            roots["exp1_real_ai_feasibility"].resolve(strict=False),
            "exp1_real_ai_feasibility",
            "sha256:" + "1" * 64,
        ),
        (
            roots["exp5_real_ai_model_endpoint_comparison"].resolve(strict=False),
            "exp5_real_ai_model_endpoint_comparison",
            "sha256:" + "1" * 64,
        ),
    ], key=lambda value: str(value[0]))
    assert {record.member_id for record in index.records} == set(roots)
    with pytest.raises(ValueError, match="evidence root inventory mismatch"):
        paper_formal_metrics._export_metric_lineage_source_index(
            output_root=tmp_path / "unused",
            canonical_lineage_inputs=inputs,
            input_identity_digest="sha256:" + "1" * 64,
            evidence_roots_by_experiment={
                "exp1_real_ai_feasibility": roots["exp1_real_ai_feasibility"]
            },
        )


def test_metric_lineage_export_coalesces_same_resolved_store_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from tokenshare.experiments import paper_formal_metrics

    inputs = tuple(
        SimpleNamespace(
            direct_result=SimpleNamespace(
                experiment_id=experiment_id,
                preregistered_root_run_id=root_id,
            )
        )
        for experiment_id, root_id in (
            ("exp1_real_ai_feasibility", "root-exp1"),
            ("exp2_real_ai_scalability", "root-exp2"),
        )
    )
    calls = []

    def export(store, values, *, input_identity_digest):
        calls.append(
            (
                store.output_root,
                tuple(value.direct_result.experiment_id for value in values),
            )
        )
        return paper_formal_metrics.LineageSourceIndex.create(
            records=tuple(
                LineageSourceRecord.create(
                    member_id=value.direct_result.preregistered_root_run_id,
                    evidence_class="real_model_trace_protocol_run",
                )
                for value in values
            ),
            input_identity_digest=input_identity_digest,
        )

    monkeypatch.setattr(paper_formal_metrics, "export_lineage_source_index", export)
    shared = tmp_path / "trace"
    index = paper_formal_metrics._export_metric_lineage_source_index(
        output_root=tmp_path / "unused",
        canonical_lineage_inputs=inputs,
        input_identity_digest="sha256:" + "2" * 64,
        evidence_roots_by_experiment={
            "exp1_real_ai_feasibility": shared,
            "exp2_real_ai_scalability": shared / ".",
        },
    )

    assert calls == [
        (
            shared.resolve(strict=False),
            ("exp1_real_ai_feasibility", "exp2_real_ai_scalability"),
        )
    ]
    assert {record.member_id for record in index.records} == {
        "root-exp1",
        "root-exp2",
    }

    duplicate_inputs = (
        inputs[0],
        SimpleNamespace(
            direct_result=SimpleNamespace(
                experiment_id="exp2_real_ai_scalability",
                preregistered_root_run_id="root-exp1",
            )
        ),
    )
    with pytest.raises(ValueError, match="duplicate lineage root"):
        paper_formal_metrics._export_metric_lineage_source_index(
            output_root=tmp_path / "unused",
            canonical_lineage_inputs=duplicate_inputs,
            input_identity_digest="sha256:" + "2" * 64,
            evidence_roots_by_experiment={
                "exp1_real_ai_feasibility": shared,
                "exp2_real_ai_scalability": shared,
            },
        )


def test_lineage_export_reuses_direct_alias_body_and_singleton_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tokenshare.experiments.paper_direct_results import PaperDirectRootResult

    evidence, executed_root = _executed_formal_lineage_fixture(tmp_path / "formal")
    canonical_rows = _real_canonical_rows(
        tmp_path / "fixtures",
        exp1_root=executed_root,
    )
    input_digest = lineage_input_identity_digest(canonical_rows)
    lineage_inputs = build_canonical_lineage_inputs(
        canonical_rows,
        canonical_runtime_evidence=(evidence,),
        requested_root_ids=(executed_root.preregistered_root_run_id,),
    )
    direct_dict_calls = 0
    record_create_calls = 0
    original_to_dict = PaperDirectRootResult.to_dict
    original_create = LineageSourceRecord.create.__func__

    def counting_to_dict(self):
        nonlocal direct_dict_calls
        direct_dict_calls += 1
        return original_to_dict(self)

    def counting_create(cls, **kwargs):
        nonlocal record_create_calls
        record_create_calls += 1
        return original_create(cls, **kwargs)

    monkeypatch.setattr(PaperDirectRootResult, "to_dict", counting_to_dict)
    monkeypatch.setattr(
        LineageSourceRecord,
        "create",
        classmethod(counting_create),
    )

    source_index = export_lineage_source_index(
        FormalEvidenceStore(tmp_path / "formal"),
        lineage_inputs,
        input_identity_digest=input_digest,
    )

    assert direct_dict_calls == 1
    assert record_create_calls == len(source_index.records)
    assert source_index.get(executed_root.preregistered_root_run_id) is not None
    alias_id = executed_root.attempt_refs[0].object_id
    alias = source_index.get(alias_id)
    assert alias is not None
    assert alias.direct_result_refs == ()
    assert alias.parser_verifier_checker_canonical_refs == ()
    assert {
        value.object_id for value in alias.current_task_attempt_event_refs
    } == {alias_id}
    assert {
        value.object_id for value in alias.ledger_refs
    } == {alias_id}


def test_lineage_source_index_digest_is_streamed_without_schema_drift_and_get_is_indexed(
    tmp_path: Path,
) -> None:
    import pickle

    from tokenshare.experiments import paper_formal_evidence

    evidence, executed_root = _executed_formal_lineage_fixture(tmp_path / "formal")
    canonical_rows = _real_canonical_rows(
        tmp_path / "fixtures",
        exp1_root=executed_root,
    )
    input_digest = lineage_input_identity_digest(canonical_rows)
    lineage_inputs = build_canonical_lineage_inputs(
        canonical_rows,
        canonical_runtime_evidence=(evidence,),
        requested_root_ids=(executed_root.preregistered_root_run_id,),
    )
    source_index = export_lineage_source_index(
        FormalEvidenceStore(tmp_path / "formal"),
        lineage_inputs,
        input_identity_digest=input_digest,
    )
    expected_digest = paper_formal_evidence._digest_json(
        {
            "schema_version": source_index.schema_version,
            "index_id": source_index.index_id,
            "input_identity_digest": source_index.input_identity_digest,
            "records": [record.to_dict() for record in source_index.records],
        }
    )

    assert paper_formal_evidence._digest_lineage_source_index(
        schema_version=source_index.schema_version,
        index_id=source_index.index_id,
        input_identity_digest=source_index.input_identity_digest,
        records=source_index.records,
    ) == expected_digest
    expected_record = source_index.get(executed_root.preregistered_root_run_id)
    assert expected_record is not None
    restored = pickle.loads(pickle.dumps(source_index))
    assert restored == source_index
    assert restored.get(executed_root.preregistered_root_run_id) == expected_record
    object.__setattr__(source_index, "records", ())
    assert source_index.get(executed_root.preregistered_root_run_id) is expected_record


def test_singleton_lineage_merge_preserves_old_unique_canonical_order() -> None:
    from tokenshare.experiments import paper_formal_evidence
    from tests.experiments.test_paper_metric_observations import _attempt_event

    values = (_attempt_event("attempt-a"), _attempt_event("attempt-b"))
    canonical = paper_formal_evidence._unique_typed(values)
    assert len(canonical) == 2
    record = LineageSourceRecord.create(
        member_id="root-1",
        evidence_class="online_real_provider",
        current_task_attempt_event_refs=tuple(reversed(canonical)),
    )
    expected = LineageSourceRecord.create(
        member_id="root-1",
        evidence_class="online_real_provider",
        current_task_attempt_event_refs=canonical,
    )

    assert paper_formal_evidence._merge_lineage_source_records((record,)) == (
        expected,
    )




def test_retired_aliases_and_old_exp2_exp5_outputs_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract = load_paper_metric_contract()
    registry = load_paper_metric_registry(contract)
    canonical_rows = {key: () for key in registry.required_input_keys}
    drafts = registry.project_all(canonical_rows)
    original = PaperMetricRegistry.project_all

    def missing_table(self, inputs, *, global_infra_invalid=False):
        return drafts[:-1]

    monkeypatch.setattr(PaperMetricRegistry, "project_all", missing_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "missing",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def extra_table(self, inputs, *, global_infra_invalid=False):
        return (*drafts, drafts[0])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", extra_table)
    with pytest.raises(ValueError, match="draft table inventory"):
        publish_paper_formal_metric_drafts(
            tmp_path / "extra",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    def wrong_path(self, inputs, *, global_infra_invalid=False):
        return (replace(drafts[0], output_path="metrics/wrong.csv"), *drafts[1:])

    monkeypatch.setattr(PaperMetricRegistry, "project_all", wrong_path)
    with pytest.raises(ValueError, match="output path contract drift"):
        publish_paper_formal_metric_drafts(
            tmp_path / "wrong-path",
            canonical_rows,
            registry=registry,
            contract=contract,
        )

    monkeypatch.setattr(PaperMetricRegistry, "project_all", original)

    result = publish_paper_formal_metric_drafts(
        tmp_path,
        canonical_rows,
        registry=registry,
        contract=contract,
    )

    assert tuple(draft.table_id for draft in result.table_drafts) == tuple(
        table.table_id for table in contract.tables
    )
    published = "\n".join(ref["path"] for ref in result.output_refs)
    for retired in (
        "sensitivity",
        "all_runs",
        "views",
        "model_endpoint_comparison",
        "pairwise",
        "significance",
        "accepted_validity",
    ):
        assert retired not in published
    assert not (tmp_path / "metrics" / "exp2_sensitivity.csv").exists()
    assert not (tmp_path / "metrics" / "paper_table_model_endpoint_comparison.csv").exists()
    assert result.metric_observations == ()
    assert result.lineage_source_index.records == ()
    expected_lineage_paths = {
        "metrics/paper_lineage_source_index.v1.jsonl",
        "metrics/paper_metric_observations.v1.jsonl",
        "metrics/paper_metric_observations_manifest.v1.json",
    }
    assert expected_lineage_paths <= {ref["path"] for ref in result.output_refs}
    manifest = json.loads(
        (tmp_path / "metrics" / "paper_metric_observations_manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["lineage_source_index"]["index_digest"] == (
        result.lineage_source_index.index_digest
    )
    assert manifest["observations_digest"] == result.observations_digest
    assert manifest["provider_calls"] == 0
