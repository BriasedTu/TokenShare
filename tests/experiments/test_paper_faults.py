import json
from dataclasses import replace

import pytest

from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_faults import (
    FaultTargetDescriptor,
    PaperFaultRuntimeHooks,
    PaperFaultType,
    inject_post_ai_fault,
    select_fault_target_descriptors,
    select_fault_targets,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.local_runtime import RawOutputContext


NOW = "2026-07-15T00:00:00Z"
DEADLINE = "2026-07-15T00:00:10Z"
LATE_SUBMITTED_AT = "2026-07-15T00:00:11Z"


def test_runtime_fault_hook_waits_for_raw_provenance_and_usage_artifacts(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "runtime_raw")
    provenance_ref = store.save_json(
        {"schema_version": "test.provenance.v1", "provider": "siliconflow"},
        artifact_id="runtime_provenance",
        artifact_type="ProviderProvenance",
        artifact_schema_id="test.provenance",
        artifact_schema_version="v1",
        source={"kind": "pytest"},
        metadata={},
        created_at=NOW,
    )
    usage_ref = store.save_json(
        {"schema_version": "test.usage.v1", "total_tokens": 17},
        artifact_id="runtime_usage",
        artifact_type="ProviderUsage",
        artifact_schema_id="test.usage",
        artifact_schema_version="v1",
        source={"kind": "pytest"},
        metadata={},
        created_at=NOW,
    )
    context = RawOutputContext(
        run_id="run-runtime-fault",
        task_id="task-runtime-fault",
        unit_id="unit_1",
        attempt_id="attempt_1",
        worker_id="worker_1",
        raw_output_ref=raw_ref,
        provenance_ref=provenance_ref,
        usage_ref=usage_ref,
        content_text=json.dumps({"candidate": 7}),
        provider="siliconflow",
        model="zai-org/GLM-5.2",
        entry_id="glm_5_2_exp1_baseline",
        usage_summary={"total_tokens": 17},
        submitted_at=NOW,
        lease_deadline_at=DEADLINE,
    )
    hooks = PaperFaultRuntimeHooks(
        artifact_store=store,
        condition_id="condition-runtime-fault",
        repeat_id=0,
        fault_type=PaperFaultType.NO_RETURN,
        seed=19,
        selected_unit_ids=("unit_1",),
    )

    directive = hooks.after_raw_output_persisted(context)

    assert directive is not None
    assert directive.result_kind == "no_return"
    assert len(hooks.records) == 1
    assert hooks.records[0]["original_raw_output_ref"] == raw_ref.to_dict()
    assert hooks.records[0]["original_provenance_ref"] == provenance_ref.to_dict()
    assert hooks.records[0]["pre_fault_usage_ref"] == usage_ref.to_dict()
    assert hooks.events[0]["event_type"] == "EXPERIMENT_FAULT_INJECTED"
    assert hooks.events[0]["artifact_refs"]

    replacement_context = replace(context, attempt_id="attempt_2")
    assert hooks.after_raw_output_persisted(replacement_context) is None
    assert len(hooks.records) == 1

    missing_usage_hooks = PaperFaultRuntimeHooks(
        artifact_store=store,
        condition_id="condition-missing-usage",
        repeat_id=0,
        fault_type=PaperFaultType.NO_RETURN,
        seed=19,
        selected_unit_ids=("unit_1",),
    )
    with pytest.raises(ValueError, match="persisted usage"):
        missing_usage_hooks.after_raw_output_persisted(
            replace(context, usage_ref=None)
        )
    assert missing_usage_hooks.records == ()


def test_select_fault_targets_is_deterministic_for_seed_and_rate() -> None:
    unit_ids = [f"unit_{index}" for index in range(10)]

    first = select_fault_targets(unit_ids, fault_rate=0.3, seed=17)
    second = select_fault_targets(reversed(unit_ids), fault_rate=0.3, seed=17)
    different_seed = select_fault_targets(unit_ids, fault_rate=0.3, seed=18)

    assert first == second
    assert first != different_seed
    assert len(first) == 3
    assert set(first).issubset(set(unit_ids))
    assert select_fault_targets(unit_ids, fault_rate=0.0, seed=17) == ()
    assert len(select_fault_targets(unit_ids, fault_rate=0.01, seed=17)) == 1
    with pytest.raises(ValueError, match="fault_rate"):
        select_fault_targets(unit_ids, fault_rate=1.1, seed=17)


def test_fault_targets_preserve_graph_slot_and_domain_metadata(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_lemma_dag_false_positive")
    parsed_ref = _save_parsed(
        store,
        "parsed_lemma_dag_false_positive",
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_sublemma_A2",
            "proof_source": "by\n  exact h",
        },
    )
    lemma_target = FaultTargetDescriptor(
        target_kind="lean_proof_unit",
        unit_id="unit_sublemma_A2",
        attempt_id="attempt_1",
        artifact_ref=parsed_ref.to_dict(),
        slot_key="lemma_A.sublemma_slots[1]",
        child_logical_key="sublemma_A2",
        dependency_path=("root", "lemma_A", "sublemma_A2"),
        lemma_node_id="sublemma_A2",
        parent_node_id="lemma_A",
        merge_slot="lemma_A.prereq[1]",
        domain_target={
            "paper_difficulty": "medium_lemma_dag",
            "dependency_edges": [["sublemma_A2", "lemma_A"]],
        },
    )
    factorization_target = FaultTargetDescriptor(
        target_kind="factorization_range_unit",
        unit_id="unit_range_17_31",
        attempt_id="attempt_range_17_31",
        slot_key="range:17:31",
        domain_target={
            "target_n": "899",
            "range_start": "17",
            "range_end": "31",
        },
    )

    first = select_fault_target_descriptors(
        [lemma_target, factorization_target],
        fault_rate=1.0,
        seed=212,
    )
    second = select_fault_target_descriptors(
        [factorization_target.to_dict(), lemma_target.to_dict()],
        fault_rate=1.0,
        seed=212,
    )

    assert [target.to_dict() for target in first] == [
        target.to_dict() for target in second
    ]
    assert {target.unit_id for target in first} == {
        "unit_sublemma_A2",
        "unit_range_17_31",
    }

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=_attempt(
            raw_ref=raw_ref,
            parsed_ref=parsed_ref,
            unit_id="unit_sublemma_A2",
        ),
        fault_type=PaperFaultType.FALSE_POSITIVE,
        seed=212,
        created_at=NOW,
        fault_target=lemma_target,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["target_context"]["unit_id"] == "unit_sublemma_A2"
    assert record["target_context"]["slot_key"] == "lemma_A.sublemma_slots[1]"
    assert record["target_context"]["child_logical_key"] == "sublemma_A2"
    assert record["target_context"]["dependency_path"] == [
        "root",
        "lemma_A",
        "sublemma_A2",
    ]
    assert record["target_context"]["lemma_node_id"] == "sublemma_A2"
    assert record["target_context"]["parent_node_id"] == "lemma_A"
    assert record["target_context"]["merge_slot"] == "lemma_A.prereq[1]"
    assert record["target_context"]["domain_target"]["paper_difficulty"] == (
        "medium_lemma_dag"
    )
    assert record["target_selection_digest"].startswith("sha256:")
    assert mutated["target_context"] == record["target_context"]
    assert mutated["target_selection_digest"] == record["target_selection_digest"]


def test_false_positive_mutates_parsed_candidate_after_raw_output_is_persisted(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_false_positive")
    parsed_ref = _save_parsed(
        store,
        "parsed_false_positive",
        {
            "schema_version": "factorization.range_result.v1",
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )
    attempt = _attempt(raw_ref=raw_ref, parsed_ref=parsed_ref)

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type=PaperFaultType.FALSE_POSITIVE,
        seed=101,
        created_at=NOW,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["schema_version"] == "tokenshare.paper_fault_injection.v1"
    assert record["fault_type"] == "false_positive"
    assert record["injection_point"] == "after_parsed_candidate_before_verification"
    assert record["original_raw_output_ref"] == raw_ref.to_dict()
    assert record["original_output_ref"] == parsed_ref.to_dict()
    assert record["mutated_output_ref"] == outcome.mutated_output_ref.to_dict()
    assert record["simulated_mutation_count"] == 1
    assert record["provider_tokens_attributed"] == 0
    assert outcome.mutated_attempt.fault_injection_ref == outcome.record_ref.to_dict()
    assert outcome.mutated_attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
    assert mutated["fault_type"] == "false_positive"
    assert mutated["original_output_ref"] == parsed_ref.to_dict()
    assert mutated["mutated_payload"]["result_kind"] == "found_factor"
    assert mutated["mutated_payload"]["found_factor"] == "1"
    assert mutated["mutated_payload"]["cofactor"] == "91"


def test_false_negative_suppresses_found_candidate_and_records_original_ref(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_false_negative")
    parsed_ref = _save_parsed(
        store,
        "parsed_false_negative",
        {
            "schema_version": "factorization.range_result.v1",
            "result_kind": "found_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": "7",
            "cofactor": "13",
        },
    )
    attempt = _attempt(raw_ref=raw_ref, parsed_ref=parsed_ref)

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type="false_negative",
        seed=102,
        created_at=NOW,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["fault_type"] == "false_negative"
    assert record["suppressed_output_ref"] == parsed_ref.to_dict()
    assert record["recovery_class"] == "domain_dependent"
    assert mutated["mutated_payload"]["result_kind"] == "no_factor"
    assert mutated["mutated_payload"]["found_factor"] is None
    assert mutated["mutated_payload"]["cofactor"] is None
    assert mutated["mutation_summary"]["suppressed_candidate"] is True


def test_false_negative_requires_an_existing_found_candidate_or_proof(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_false_negative_without_candidate")
    parsed_ref = _save_parsed(
        store,
        "parsed_false_negative_without_candidate",
        {
            "schema_version": "factorization.range_result.v1",
            "result_kind": "no_factor",
            "target_n": "91",
            "range_start": "2",
            "range_end": "10",
            "found_factor": None,
            "cofactor": None,
        },
    )

    with pytest.raises(ValueError, match="found candidate or proof"):
        inject_post_ai_fault(
            artifact_store=store,
            attempt=_attempt(raw_ref=raw_ref, parsed_ref=parsed_ref),
            fault_type="false_negative",
            seed=107,
            created_at=NOW,
        )


def test_no_return_requires_raw_output_and_marks_lease_expired_recovery(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_no_return")
    attempt = _attempt(raw_ref=raw_ref, parsed_ref=None)

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type="no_return",
        seed=103,
        created_at=NOW,
        lease_deadline_at=DEADLINE,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["fault_type"] == "no_return"
    assert record["original_output_ref"] == raw_ref.to_dict()
    assert record["injection_point"] == "after_raw_output_before_submission"
    assert record["mutated_attempt_status"] == "lease_expired"
    assert record["recovery_required"] is True
    assert record["retry_required"] is True
    assert outcome.mutated_attempt.attempt_status == PaperAttemptStatus.LEASE_EXPIRED
    assert mutated["submission_action"] == "dropped_after_raw_output"
    assert mutated["lease_deadline_at"] == DEADLINE


def test_late_submission_is_rejected_after_deadline_without_canonical_pollution(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_late")
    parsed_ref = _save_parsed(store, "parsed_late", {"proof_source": "by\n  exact hP"})
    attempt = _attempt(raw_ref=raw_ref, parsed_ref=parsed_ref)

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type="late_submission",
        seed=104,
        created_at=NOW,
        lease_deadline_at=DEADLINE,
        submitted_at=LATE_SUBMITTED_AT,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["fault_type"] == "late_submission"
    assert record["mutated_attempt_status"] == "late_rejected"
    assert record["canonical_pollution"] is False
    assert outcome.mutated_attempt.attempt_status == PaperAttemptStatus.LATE_REJECTED
    assert mutated["submitted_at"] == LATE_SUBMITTED_AT
    assert mutated["lease_deadline_at"] == DEADLINE
    assert mutated["accepted_by_protocol"] is False

    with pytest.raises(ValueError, match="after lease_deadline_at"):
        inject_post_ai_fault(
            artifact_store=store,
            attempt=attempt,
            fault_type="late_submission",
            seed=104,
            created_at=NOW,
            lease_deadline_at=DEADLINE,
            submitted_at=DEADLINE,
        )


def test_executor_error_records_controlled_error_after_raw_output_and_requires_retry(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _save_raw(store, "raw_executor_error")
    attempt = _attempt(raw_ref=raw_ref, parsed_ref=None)

    outcome = inject_post_ai_fault(
        artifact_store=store,
        attempt=attempt,
        fault_type="executor_error",
        seed=105,
        created_at=NOW,
    )

    record = outcome.record.to_dict()
    mutated = _read_json(store, record["mutated_output_ref"])

    assert record["fault_type"] == "executor_error"
    assert record["injection_point"] == "after_raw_output_before_parser_bridge"
    assert record["mutated_attempt_status"] == "provider_error"
    assert record["error_kind"] == "executor_error"
    assert record["recovery_required"] is True
    assert record["retry_required"] is True
    assert outcome.mutated_attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
    assert outcome.mutated_attempt.error_kind == "executor_error"
    assert mutated["controlled_error"]["kind"] == "executor_error"
    assert mutated["controlled_error"]["retry_requires_new_provider_attempt"] is True


def test_fault_injection_rejects_attempt_without_persisted_raw_output(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    raw_ref = _artifact_ref("missing_raw", "RawModelOutput", "phase7.raw_model_output")
    attempt = _attempt(raw_ref=ArtifactRef.from_dict(raw_ref), parsed_ref=None)

    with pytest.raises(ValueError, match="persisted raw output"):
        inject_post_ai_fault(
            artifact_store=store,
            attempt=attempt,
            fault_type="no_return",
            seed=106,
            created_at=NOW,
            lease_deadline_at=DEADLINE,
        )


def _attempt(
    *,
    raw_ref: ArtifactRef,
    parsed_ref: ArtifactRef | None,
    unit_id: str = "unit_1",
) -> PaperAttemptResult:
    return PaperAttemptResult(
        condition_id="condition_1",
        repeat_id=0,
        run_id="run_1",
        task_id="task_1",
        unit_id=unit_id,
        attempt_id="attempt_1",
        worker_id="worker_1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="GLM-5.2",
        entry_id="glm_5_2__sf_key_1",
        request_ref=_artifact_ref("request_1", "ExecutionRequest", "phase3.execution_request"),
        raw_output_ref=raw_ref.to_dict(),
        parsed_output_ref=parsed_ref.to_dict() if parsed_ref is not None else None,
        parse_failure_ref=None,
        provenance_ref=_artifact_ref(
            "provenance_1",
            "AIProviderCallProvenance",
            "phase7.ai_provider_call_provenance",
        ),
        usage_ref=_artifact_ref("usage_1", "AIUsageSummary", "tokenshare.paper_ai_usage"),
        started_at="2026-07-15T00:00:00Z",
        ended_at="2026-07-15T00:00:02Z",
        latency_ms=1234,
        prompt_tokens=40,
        completion_tokens=59,
        total_tokens=99,
        cost_estimate=0.001,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )


def _save_raw(store: ArtifactStore, artifact_id: str) -> ArtifactRef:
    return store.save_json(
        {
            "schema_version": "phase7.raw_model_output.v1",
            "provider": "siliconflow",
            "model": "GLM-5.2",
            "text": "{\"result\": \"ok\"}",
        },
        artifact_id=artifact_id,
        artifact_type="RawModelOutput",
        artifact_schema_id="phase7.raw_model_output",
        artifact_schema_version="v1",
        source={"kind": "ai_api_executor"},
        metadata={},
        created_at=NOW,
    )


def _save_parsed(store: ArtifactStore, artifact_id: str, payload: dict) -> ArtifactRef:
    return store.save_json(
        payload,
        artifact_id=artifact_id,
        artifact_type="ParsedModelOutput",
        artifact_schema_id="phase7.parsed_model_output",
        artifact_schema_version="v1",
        source={"kind": "ai_api_executor"},
        metadata={},
        created_at=NOW,
    )


def _read_json(store: ArtifactStore, ref: dict) -> dict:
    return json.loads(store.read_bytes(ArtifactRef.from_dict(ref)).decode("utf-8"))


def _artifact_ref(
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
) -> dict:
    digest = "sha256:" + artifact_id.encode("utf-8").hex().ljust(64, "0")[:64]
    return {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "uri": f"artifacts/{artifact_id}",
        "content_hash": digest,
        "size_bytes": 1,
        "media_type": "application/json",
        "artifact_schema_id": artifact_schema_id,
        "artifact_schema_version": "v1",
        "source": {"kind": "ai_api_executor"},
        "metadata": {},
        "created_at": NOW,
    }
