from __future__ import annotations

from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.experiments.paper_projection import project_paper_protocol_run
from tokenshare.local_runtime import ProtocolRunResult
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-23T00:00:00Z"


def test_projection_derives_paper_results_from_protocol_events_and_artifacts(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_1"
    unit_id = "paper_factorization_case_1_range_0"
    attempt_id = "attempt_case_1_0"
    request_ref = _save(
        store,
        "request",
        "ExecutionRequest",
        {
            "request_id": "request_case_1_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "task_unit_snapshot": {"metadata": {"planned_ai_unit_id": "range_0"}},
            "created_at": NOW,
        },
    )
    raw_ref = _save(store, "raw", "RawModelOutput", {"content_text": "{}"})
    parsed_ref = _save(store, "parsed", "ParsedModelOutput", {"result_kind": "no_factor"})
    provenance_ref = _save(
        store,
        "provenance",
        "AIProviderCallProvenance",
        {
            "attempts": [
                {
                    "provider_family": "siliconflow",
                    "entry_id": "glm_5_2_exp1_baseline",
                    "configured_model": "zai-org/GLM-5.2",
                    "latency_ms": 17,
                }
            ]
        },
    )
    usage_ref = _save(
        store,
        "usage",
        "AIUsageSummary",
        {
            "submission_id": "submission_case_1_0",
            "request_id": "request_case_1_0",
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "model": "zai-org/GLM-5.2",
            "provider_attempt_count": 1,
            "prompt_tokens": 5,
            "completion_tokens": 6,
            "total_tokens": 11,
            "cost_estimate": 0.125,
        },
    )
    model_record_ref = _save(
        store,
        "model-record",
        "PaperModelExecutionRecord",
        {
            "attempt_id": attempt_id,
            "request_ref": request_ref.to_dict(),
            "raw_output_ref": raw_ref.to_dict(),
            "provenance_ref": provenance_ref.to_dict(),
            "usage_ref": usage_ref.to_dict(),
            "paper_eligible": True,
            "identity_status": "matched",
        },
    )
    submission_ref = _save(
        store,
        "submission",
        "ExecutionSubmission",
        {
            "submission_id": "submission_case_1_0",
            "request_id": "request_case_1_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "result_kind": "succeeded",
            "raw_output_ref": raw_ref.to_dict(),
            "parsed_output_ref": parsed_ref.to_dict(),
            "parse_failure_ref": None,
            "provenance_ref": provenance_ref.to_dict(),
            "usage_summary": {"provider_attempt_count": 1},
            "error": None,
            "submitted_at": "2026-07-23T00:00:00.017000Z",
        },
    )
    events = _completed_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_ref=request_ref.to_dict(),
        submission_ref=submission_ref.to_dict(),
        parsed_ref=parsed_ref.to_dict(),
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_1_case_1",
        task_id=task_id,
        root_unit_id="paper_factorization_case_1_root",
        status="completed",
        event_refs=tuple(_event_ref(event) for event in events),
        artifact_refs=(
            request_ref,
            submission_ref,
            raw_ref,
            parsed_ref,
            provenance_ref,
        ),
        summary={
            "runtime_observation": {
                "schema_version": "tokenshare.protocol_runtime_observation.v1",
                "run_id": "condition_1_case_1",
                "runtime_started_at": "2026-07-23T00:00:00Z",
                "runtime_ended_at": "2026-07-23T00:00:01.234000Z",
                "runtime_wall_clock_ms": 1234.0,
                "planned_ai_unit_ids": ["range_0"],
                "dispatched_ai_unit_ids": ["range_0"],
                "completed_ai_unit_ids": ["range_0"],
                "unscheduled_ai_unit_ids": [],
                "in_flight_ai_unit_ids_at_witness": [],
                "witness_observed_at": None,
                "worker_execution_facts": [
                    {
                        "execution_index": 1,
                        "request_id": "request_case_1_0",
                        "submission_id": "submission_case_1_0",
                        "result_kind": "succeeded",
                        "unit_id": unit_id,
                        "attempt_id": attempt_id,
                        "lease_id": "lease_case_1_0",
                        "worker_id": "runtime-worker-7",
                        "worker_pid": None,
                        "process_exitcode": None,
                        "started_at": "2026-07-23T00:00:00.100000Z",
                        "ended_at": "2026-07-23T00:00:00.400000Z",
                        "kill_point": None,
                    }
                ],
                "observed_peak_concurrency": 1,
            }
        },
    )

    projection = project_paper_protocol_run(
        condition=_condition(),
        case={"case_id": "case_1", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=events,
        artifact_store=store,
        accepted_validity=True,
    )

    assert projection.task_result.root_status.value == "completed"
    assert projection.task_result.accepted_validity is True
    assert projection.task_result.paper_eligible is True
    assert projection.task_result.attempt_count == 1
    assert projection.task_result.provider_attempt_count == 1
    assert projection.task_result.wall_clock_ms == 1234
    assert projection.task_result.total_tokens == 11
    assert projection.attempt_results[0].attempt_id == attempt_id
    assert projection.attempt_results[0].worker_id == "runtime-worker-7"
    assert projection.attempt_results[0].started_at == (
        "2026-07-23T00:00:00.100000Z"
    )
    assert projection.attempt_results[0].ended_at == (
        "2026-07-23T00:00:00.400000Z"
    )
    assert projection.attempt_results[0].provider_attempt_index == 0
    assert projection.attempt_results[0].attempt_status.value == "succeeded"
    assert projection.attempt_results[0].usage_ref == usage_ref.to_dict()
    assert (
        projection.attempt_results[0].model_execution_record_ref
        == model_record_ref.to_dict()
    )
    assert projection.lifecycle_coverage["complete"] is True
    assert projection.runtime_observation["runtime_wall_clock_ms"] == 1234.0
    assert projection.runtime_generation_identity["last_event_id"] == "event-9"
    assert projection.runtime_generation_identity["ledger_digest"].startswith("sha256:")


def test_completed_projection_with_missing_merge_or_settlement_is_ineligible(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_1"
    unit_id = "paper_factorization_case_1_range_0"
    attempt_id = "attempt_case_1_0"
    request_ref = _save(
        store,
        "request",
        "ExecutionRequest",
        {
            "request_id": "request_case_1_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "created_at": NOW,
        },
    )
    parsed_ref = _save(store, "parsed", "ParsedModelOutput", {"result_kind": "no_factor"})
    submission_ref = _save(
        store,
        "submission",
        "ExecutionSubmission",
        {
            "submission_id": "submission_case_1_0",
            "request_id": "request_case_1_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "result_kind": "succeeded",
            "parsed_output_ref": parsed_ref.to_dict(),
            "submitted_at": NOW,
        },
    )
    events = _completed_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_ref=request_ref.to_dict(),
        submission_ref=submission_ref.to_dict(),
        parsed_ref=parsed_ref.to_dict(),
    )
    incomplete_events = tuple(
        event
        for event in events
        if event["event_type"] not in {"MERGE_RECORDED", "SETTLEMENT_RECORDED"}
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_1_case_1",
        task_id=task_id,
        root_unit_id="paper_factorization_case_1_root",
        status="completed",
        event_refs=tuple(_event_ref(event) for event in incomplete_events),
        artifact_refs=(request_ref, submission_ref, parsed_ref),
    )

    projection = project_paper_protocol_run(
        condition=_condition(),
        case={"case_id": "case_1", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=incomplete_events,
        artifact_store=store,
        accepted_validity=True,
    )

    assert projection.task_result.root_status.value == "completed"
    assert projection.task_result.paper_eligible is False
    assert projection.lifecycle_coverage["complete"] is False
    assert set(projection.lifecycle_coverage["missing_event_types"]) == {
        "MERGE_RECORDED",
        "SETTLEMENT_RECORDED",
    }
    assert "incomplete_protocol_lifecycle" in projection.ineligibility_reasons


def _condition() -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="condition_1",
        domain="factorization",
        difficulty="easy",
        worker_count=1,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_entry_id="glm_5_2_exp1_baseline",
        provider_family="siliconflow",
        provider_model_id="zai-org/GLM-5.2",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:catalog",
    )


def _save(store: ArtifactStore, artifact_id: str, artifact_type: str, body: dict):
    return store.save_json(
        body,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        artifact_schema_id=f"test.{artifact_type.lower()}",
        artifact_schema_version="v1",
        source={"kind": "task8_projection_test"},
        metadata={},
        created_at=NOW,
    )


def _completed_events(
    *,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    request_ref: dict,
    submission_ref: dict,
    parsed_ref: dict,
) -> tuple[dict, ...]:
    root_id = "paper_factorization_case_1_root"
    rows = (
        ("TASK_REGISTERED", {"task_id": task_id}),
        ("TASK_EXPANDED", {"task_id": task_id, "root_unit_id": root_id}),
        (
            "EXECUTION_REQUEST_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "request_id": "request_case_1_0",
                "request_ref": request_ref,
            },
        ),
        (
            "EXECUTION_SUBMISSION_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "submission_id": "submission_case_1_0",
                "submission_ref": submission_ref,
                "result_kind": "succeeded",
                "acceptance_status": "accepted",
            },
        ),
        (
            "VERIFICATION_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "status": "accepted",
                "eligible_for_canonical": True,
            },
        ),
        (
            "CANONICAL_OUTPUTS_BOUND",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "selected_attempt_id": attempt_id,
                "canonical_output_refs": {"range_result": parsed_ref},
            },
        ),
        ("MERGE_RECORDED", {"task_id": task_id, "merge_unit_id": root_id}),
        (
            "TASK_UNIT_STATE_CHANGED",
            {
                "task_id": task_id,
                "old_state": "Processing",
                "new_state": "Completed",
                "task_unit": {"task_id": task_id, "unit_id": root_id, "state": "Completed"},
            },
        ),
        (
            "SETTLEMENT_RECORDED",
            {"task_id": task_id, "root_unit_id": root_id},
        ),
    )
    return tuple(
        {
            "event_id": f"event-{index}",
            "event_seq": index,
            "event_type": event_type,
            "task_id": task_id,
            "object_id": f"object-{index}",
            "occurred_at": f"2026-07-23T00:00:0{index}Z",
            "event_hash": f"sha256:event-{index}",
            "payload": payload,
        }
        for index, (event_type, payload) in enumerate(rows, start=1)
    )


def _event_ref(event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "event_seq": event["event_seq"],
        "event_type": event["event_type"],
    }
