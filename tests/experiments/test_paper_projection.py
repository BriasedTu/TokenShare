from __future__ import annotations

from dataclasses import replace

import pytest

import tokenshare.experiments.paper_projection as paper_projection
from tokenshare.core.models import ArtifactRef
from tokenshare.experiments.paper_models import PaperExperimentCondition
from tokenshare.experiments.paper_projection import (
    _auditable_no_return_evidence,
    _attempt_status,
    project_paper_protocol_run,
)
from tokenshare.local_runtime import ProtocolRunResult
from tokenshare.storage.artifacts import ArtifactStore


NOW = "2026-07-23T00:00:00Z"


@pytest.mark.parametrize(
    ("parser", "negative_value", "zero_value"),
    (
        (paper_projection._non_negative_int, -1, 0),
        (paper_projection._non_negative_number, -0.5, 0.0),
    ),
)
def test_projection_rejects_negative_measurements_but_preserves_zero(
    parser,
    negative_value,
    zero_value,
) -> None:
    with pytest.raises(ValueError, match="negative"):
        parser(negative_value)
    assert parser(zero_value) == zero_value


def test_projection_rejects_negative_event_intervals_but_preserves_zero() -> None:
    with pytest.raises(ValueError, match="negative"):
        paper_projection._wall_clock_ms(
            (
                {"occurred_at": "2026-07-31T00:00:00.200+00:00"},
                {"occurred_at": "2026-07-31T00:00:00.100+00:00"},
            )
        )
    with pytest.raises(ValueError, match="negative"):
        paper_projection._runtime_wall_clock_ms(
            {"runtime_wall_clock_ms": -1},
            (),
        )
    assert paper_projection._wall_clock_ms(
        (
            {"occurred_at": "2026-07-31T00:00:00.100+00:00"},
            {"occurred_at": "2026-07-31T00:00:00.100+00:00"},
        )
    ) == 0
    assert paper_projection._runtime_wall_clock_ms(
        {"runtime_wall_clock_ms": 0},
        (),
    ) == 0


def test_artifact_reference_comparison_rejects_uri_only_and_uri_mismatch() -> None:
    assert paper_projection._same_artifact_ref(
        {"uri": "artifacts/left"},
        {"uri": "artifacts/right"},
    ) is False
    assert paper_projection._same_artifact_ref(
        {
            "artifact_id": "artifact-1",
            "content_hash": "sha256:" + "1" * 64,
            "uri": "artifacts/left",
        },
        {
            "artifact_id": "artifact-1",
            "content_hash": "sha256:" + "1" * 64,
            "uri": "artifacts/right",
        },
    ) is False
    assert paper_projection._same_artifact_ref(
        {
            "artifact_id": "artifact-1",
            "content_hash": "sha256:" + "1" * 64,
            "uri": "artifacts/same",
        },
        {
            "artifact_id": "artifact-1",
            "content_hash": "sha256:" + "1" * 64,
            "uri": "artifacts/same",
        },
    ) is True


@pytest.mark.parametrize(
    "failure_kind",
    (
        "timeout",
        "connection_error",
        "rate_limited",
        "provider_error",
        "auth_error",
        "client_error",
    ),
)
def test_projection_preserves_provider_transport_failure_kind(failure_kind) -> None:
    status, error_kind = _attempt_status(
        condition=_condition(),
        submission_event={"payload": {"acceptance_status": "accepted"}},
        submission={
            "result_kind": failure_kind,
            "error": {"kind": failure_kind},
        },
        snapshot={"state": "Failed", "failure_kind": "execution_failed"},
        verification={},
        canonical=False,
        recovery={},
        model_record={},
    )

    assert status.value == "provider_error"
    assert error_kind == failure_kind


def test_projection_keeps_pre_provider_executor_error_out_of_provider_taxonomy() -> None:
    status, error_kind = _attempt_status(
        condition=_condition(),
        submission_event={"payload": {"acceptance_status": "accepted"}},
        submission={
            "result_kind": "executor_error",
            "raw_output_ref": None,
            "usage_summary": {"provider_attempt_count": 0},
            "error": {
                "kind": "executor_error",
                "attempts": [{"result_kind": "config_error"}],
            },
        },
        snapshot={"state": "Failed", "failure_kind": "execution_failed"},
        verification={},
        canonical=False,
        recovery={},
        model_record={},
    )

    assert status.value == "executor_error"
    assert error_kind == "config_error"


@pytest.mark.parametrize(
    (
        "provider_latency",
        "expected_attempt_schema",
        "usage_artifact_type",
        "expected_provider_attempt_count",
        "expected_total_tokens",
        "expected_task_schema",
        "expected_cost_status",
    ),
    (
        (
            17,
            "tokenshare.paper_attempt_result.v1",
            "AIUsageSummary",
            1,
            11,
            "tokenshare.paper_task_result.v1",
            "estimated",
        ),
        (
            None,
            "tokenshare.paper_attempt_result.v3",
            "AIUsageSummary",
            1,
            11,
            "tokenshare.paper_task_result.v1",
            "estimated",
        ),
        (
            None,
            "tokenshare.paper_attempt_result.v3",
            "TraceCurrentUsage",
            0,
            None,
            "tokenshare.paper_task_result.v2",
            "usage_missing",
        ),
    ),
)
def test_projection_derives_paper_results_from_protocol_events_and_artifacts(
    tmp_path,
    provider_latency,
    expected_attempt_schema,
    usage_artifact_type,
    expected_provider_attempt_count,
    expected_total_tokens,
    expected_task_schema,
    expected_cost_status,
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
                    "latency_ms": provider_latency,
                }
            ]
        },
    )
    usage_ref = _save(
        store,
        "usage",
        usage_artifact_type,
        (
            {
                "submission_id": "submission_case_1_0",
                "request_id": "request_case_1_0",
                "provider_attempt_count": 0,
                "current_provider_call_count": 0,
                "source_usage_class": "trace_attribution",
            }
            if usage_artifact_type == "TraceCurrentUsage"
            else {
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
                "cost_estimate_status": "estimated",
            }
        ),
    )
    model_record_ref = _save(
        store,
        "model-record",
        "PaperModelExecutionRecord",
        {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "attempt_id": attempt_id,
            "expected_identity": {
                "provider_family": "siliconflow",
                "provider_model_id": "zai-org/GLM-5.2",
                "selected_entry_id": "glm_5_2_exp1_baseline",
            },
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
            "usage_summary": {
                "provider_attempt_count": expected_provider_attempt_count,
                "current_provider_call_count": (
                    0 if usage_artifact_type == "TraceCurrentUsage" else 1
                ),
            },
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
    assert (
        projection.task_result.provider_attempt_count
        == expected_provider_attempt_count
    )
    assert projection.task_result.wall_clock_ms == 1234
    assert projection.task_result.total_tokens == expected_total_tokens
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
    assert projection.attempt_results[0].latency_ms == provider_latency
    assert projection.attempt_results[0].schema_version == expected_attempt_schema
    assert (
        projection.attempt_results[0].cost_estimate_status
        == expected_cost_status
    )
    assert projection.task_result.schema_version == expected_task_schema
    assert projection.attempt_results[0].usage_ref == usage_ref.to_dict()
    if usage_artifact_type == "TraceCurrentUsage":
        assert projection.attempt_results[0].provider_attempt_count == 0
        assert projection.attempt_results[0].prompt_tokens is None
        assert projection.attempt_results[0].completion_tokens is None
        assert projection.attempt_results[0].total_tokens is None
        assert projection.attempt_results[0].cost_estimate is None
    assert (
        projection.attempt_results[0].model_execution_record_ref
        == model_record_ref.to_dict()
    )
    assert projection.lifecycle_coverage["complete"] is True
    assert projection.runtime_observation["runtime_wall_clock_ms"] == 1234.0
    assert projection.runtime_generation_identity["last_event_id"] == "event-9"
    assert projection.runtime_generation_identity["ledger_digest"].startswith("sha256:")


def test_projection_keeps_artifact_backed_provider_failure_eligible_and_counts_calls(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_provider_failure"
    unit_id = "paper_factorization_case_provider_failure_range_0"
    attempt_id = "attempt_provider_failure_0"
    request_ref = _save(
        store,
        "request-provider-failure",
        "ExecutionRequest",
        {
            "request_id": "request_provider_failure_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "created_at": NOW,
        },
    )
    request_identity = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": "siliconflow",
        "entry_id": "glm_5_2_exp1_baseline",
        "configured_model": "zai-org/GLM-5.2",
        "requested_model": "zai-org/GLM-5.2",
        "reasoning_controls": {"enable_thinking": True},
        "effective_request_controls_digest": "sha256:" + "7" * 64,
    }
    provider_attempts = [
        {
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "configured_model": "zai-org/GLM-5.2",
            "result_kind": "rate_limited",
            "provider_request_identity": request_identity,
        },
        {
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "configured_model": "zai-org/GLM-5.2",
            "result_kind": "provider_error",
            "provider_request_identity": request_identity,
        },
    ]
    provenance_ref = _save(
        store,
        "provenance-provider-failure",
        "AIProviderCallProvenance",
        {"final_result_kind": "provider_error", "attempts": provider_attempts},
    )
    usage_ref = _save(
        store,
        "usage-provider-failure",
        "AIUsageSummary",
        {
            "submission_id": "submission_provider_failure_0",
            "request_id": "request_provider_failure_0",
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "model": "zai-org/GLM-5.2",
            "provider_attempt_count": 2,
            "cost_estimate": None,
            "cost_estimate_status": "usage_missing",
        },
    )
    model_record_ref = _save(
        store,
        "model-record-provider-failure",
        "PaperModelExecutionRecord",
        {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "attempt_id": attempt_id,
            "expected_identity": {
                "provider_family": "siliconflow",
                "provider_model_id": "zai-org/GLM-5.2",
                "selected_entry_id": "glm_5_2_exp1_baseline",
            },
            "request_ref": request_ref.to_dict(),
            "raw_output_ref": None,
            "provenance_ref": provenance_ref.to_dict(),
            "usage_ref": usage_ref.to_dict(),
            "actual_request_identities": [request_identity, request_identity],
            "actual_provider_attempts": provider_attempts,
            "paper_eligible": False,
            "identity_status": "not_observed",
            "response_model_status": "unavailable",
            "mismatch_reasons": [],
        },
    )
    submission_ref = _save(
        store,
        "submission-provider-failure",
        "ExecutionSubmission",
        {
            "submission_id": "submission_provider_failure_0",
            "request_id": "request_provider_failure_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "result_kind": "provider_error",
            "raw_output_ref": None,
            "parsed_output_ref": None,
            "parse_failure_ref": None,
            "provenance_ref": provenance_ref.to_dict(),
            "usage_summary": {"provider_attempt_count": 2},
            "error": {"kind": "provider_error"},
            "submitted_at": "2026-07-23T00:00:00.017000Z",
        },
    )
    events = _provider_failure_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_ref=request_ref.to_dict(),
        submission_ref=submission_ref.to_dict(),
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_1_case_provider_failure",
        task_id=task_id,
        root_unit_id=unit_id,
        status="failed",
        event_refs=tuple(_event_ref(event) for event in events),
        artifact_refs=(
            request_ref,
            submission_ref,
            provenance_ref,
            usage_ref,
            model_record_ref,
        ),
    )

    projection = project_paper_protocol_run(
        condition=_condition(),
        case={"case_id": "case_provider_failure", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=events,
        artifact_store=store,
        accepted_validity=None,
    )

    attempt = projection.attempt_results[0]
    assert attempt.attempt_status.value == "provider_error"
    assert attempt.error_kind == "provider_error"
    assert attempt.raw_output_ref is None
    assert attempt.paper_eligible is True
    assert attempt.provider_attempt_count == 2
    assert attempt.schema_version == "tokenshare.paper_attempt_result.v3"
    assert attempt.prompt_tokens is None
    assert attempt.completion_tokens is None
    assert attempt.total_tokens is None
    assert attempt.cost_estimate is None
    assert attempt.cost_estimate_status == "usage_missing"
    assert projection.task_result.root_status.value == "failed"
    assert projection.task_result.accepted_validity is None
    assert projection.task_result.paper_eligible is True
    assert projection.task_result.provider_attempt_count == 2
    assert projection.task_result.schema_version == "tokenshare.paper_task_result.v2"
    assert projection.task_result.total_tokens is None
    assert projection.task_result.cost_estimate is None
    body = projection.to_dict()
    assert body["attempt_results"][0]["total_tokens"] is None
    assert body["task_result"]["cost_estimate"] is None


def test_projection_recovers_auditable_no_return_evidence_without_submission(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_no_return"
    unit_id = "paper_factorization_case_no_return_range_0"
    attempt_id = "attempt_no_return_0"
    request_id = "request_no_return_0"
    request_ref = _save(
        store,
        "request-no-return",
        "ExecutionRequest",
        {
            "request_id": request_id,
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "created_at": NOW,
        },
    )
    raw_ref = _save(store, "raw-no-return", "RawModelOutput", {"content_text": "{}"})
    provenance_ref = _save(
        store,
        "response-provenance-no-return",
        "AIProviderResponseProvenance",
        {
            "schema_version": "phase7.ai_provider_response_provenance.v1",
            "request_id": request_id,
            "lifecycle_stage": "provider_response_persisted_before_parser",
            "raw_output_ref": raw_ref.to_dict(),
            "attempts": [
                {
                    "provider_family": "siliconflow",
                    "entry_id": "glm_5_2_exp1_baseline",
                    "configured_model": "zai-org/GLM-5.2",
                    "result_kind": "succeeded",
                    "latency_ms": 17,
                }
            ],
        },
    )
    usage_ref = _save(
        store,
        "usage-no-return",
        "AIUsageSummary",
        {
            "request_id": request_id,
            "provider_family": "siliconflow",
            "entry_id": "glm_5_2_exp1_baseline",
            "model": "zai-org/GLM-5.2",
            "provider_attempt_count": 1,
            "prompt_tokens": 5,
            "completion_tokens": 6,
            "total_tokens": 11,
            "cost_estimate": 0.125,
            "cost_estimate_status": "estimated",
        },
    )
    response_usage_ref = _save(
        store,
        "response-usage-no-return",
        "AIProviderResponseUsage",
        {
            "schema_version": "phase7.ai_provider_response_usage.v1",
            "request_id": request_id,
            "lifecycle_stage": "provider_response_persisted_before_parser",
            "raw_output_ref": raw_ref.to_dict(),
            "usage_summary": {"provider_attempt_count": 1, "total_tokens": 11},
        },
    )
    model_record_ref = _save(
        store,
        "model-record-no-return",
        "PaperModelExecutionRecord",
        {
            "schema_version": "tokenshare.paper_model_execution_record.v2",
            "attempt_id": attempt_id,
            "expected_identity": {
                "provider_family": "siliconflow",
                "provider_model_id": "zai-org/GLM-5.2",
                "selected_entry_id": "glm_5_2_exp1_baseline",
            },
            "request_ref": request_ref.to_dict(),
            "raw_output_ref": raw_ref.to_dict(),
            "provenance_ref": provenance_ref.to_dict(),
            "usage_ref": usage_ref.to_dict(),
            "paper_eligible": True,
            "identity_status": "matched",
        },
    )
    primitive_fault_ref = _save(
        store,
        "fault-no-return",
        "FaultInjectionRecord",
        {
            "schema_version": "tokenshare.paper_fault_injection.v1",
            "attempt_id": attempt_id,
            "fault_id": "fault-no-return",
            "fault_type": "no_return",
            "injection_point": "after_raw_output_before_submission",
            "mutated_attempt_status": "lease_expired",
            "original_attempt_status": "succeeded",
            "original_raw_output_ref": raw_ref.to_dict(),
            "mutation_summary": {"mutation_kind": "no_return_drop_submission"},
        },
    )
    runtime_fault_ref = _save(
        store,
        "fault-no-return-runtime",
        "RuntimeFaultInjectionRecord",
        {
            "schema_version": "tokenshare.paper_fault_injection.v1",
            "attempt_id": attempt_id,
            "fault_id": "fault-no-return",
            "fault_type": "no_return",
            "applicability_status": "injected",
            "hook_stage": "after_raw_provenance_usage_before_parser",
            "injection_point": "after_raw_output_before_submission",
            "mutated_attempt_status": "lease_expired",
            "original_attempt_status": "succeeded",
            "original_raw_output_ref": raw_ref.to_dict(),
            "original_provenance_ref": provenance_ref.to_dict(),
            "pre_fault_usage_ref": response_usage_ref.to_dict(),
            "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
            "mutation_summary": {"mutation_kind": "no_return_drop_submission"},
        },
    )
    events = _missing_submission_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_id=request_id,
        request_ref=request_ref.to_dict(),
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_no_return_case",
        task_id=task_id,
        root_unit_id=unit_id,
        status="completed",
        event_refs=tuple(_event_ref(event) for event in events),
        artifact_refs=(request_ref, runtime_fault_ref),
    )

    projection = project_paper_protocol_run(
        condition=replace(
            _condition(),
            experiment_id="exp3_real_ai_fault_recovery",
            fault_type="no_return",
            fault_rate=1.0,
        ),
        case={"case_id": "case_no_return", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=events,
        artifact_store=store,
        accepted_validity=None,
    )

    attempt = projection.attempt_results[0]
    assert attempt.attempt_status.value == "lease_expired"
    assert attempt.error_kind == "lease_expired"
    assert attempt.raw_output_ref == raw_ref.to_dict()
    assert attempt.provenance_ref == provenance_ref.to_dict()
    assert attempt.usage_ref == usage_ref.to_dict()
    assert attempt.model_execution_record_ref == model_record_ref.to_dict()
    assert attempt.fault_injection_ref == primitive_fault_ref.to_dict()
    assert attempt.paper_eligible is True
    stable_refs = {
        ref["artifact_id"]: ref for ref in projection.task_result.artifact_refs
    }
    for expected_ref in (
        raw_ref,
        provenance_ref,
        usage_ref,
        model_record_ref,
        primitive_fault_ref,
    ):
        persisted = ArtifactRef.from_dict(stable_refs[expected_ref.artifact_id])
        assert store.verify(persisted) is True


def test_auditable_no_return_rejects_model_runtime_provenance_mismatch(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    request_id = "request-no-return-provenance-mismatch"
    attempt_id = "attempt-no-return-provenance-mismatch"
    request_ref = _save(
        store,
        "request-no-return-provenance-mismatch",
        "ExecutionRequest",
        {"request_id": request_id, "attempt_id": attempt_id},
    )
    raw_ref = _save(
        store,
        "raw-no-return-provenance-mismatch",
        "RawModelOutput",
        {"content_text": "{}"},
    )
    runtime_provenance_ref = _save(
        store,
        "runtime-provenance-no-return",
        "AIProviderResponseProvenance",
        {
            "request_id": request_id,
            "lifecycle_stage": "provider_response_persisted_before_parser",
            "raw_output_ref": raw_ref.to_dict(),
            "attempts": [{"result_kind": "succeeded"}],
        },
    )
    model_provenance_ref = _save(
        store,
        "model-provenance-no-return",
        "AIProviderResponseProvenance",
        {
            "request_id": request_id,
            "lifecycle_stage": "provider_response_persisted_before_parser",
            "raw_output_ref": raw_ref.to_dict(),
            "attempts": [{"result_kind": "succeeded"}],
        },
    )
    response_usage_ref = _save(
        store,
        "response-usage-no-return-provenance-mismatch",
        "AIProviderResponseUsage",
        {
            "request_id": request_id,
            "lifecycle_stage": "provider_response_persisted_before_parser",
            "raw_output_ref": raw_ref.to_dict(),
        },
    )
    model_usage_ref = _save(
        store,
        "model-usage-no-return-provenance-mismatch",
        "AIUsageSummary",
        {"request_id": request_id, "total_tokens": 1},
    )
    primitive_fault_ref = _save(
        store,
        "primitive-fault-no-return-provenance-mismatch",
        "FaultInjectionRecord",
        {"attempt_id": attempt_id},
    )
    runtime_fault_ref = _save(
        store,
        "runtime-fault-no-return-provenance-mismatch",
        "RuntimeFaultInjectionRecord",
        {"attempt_id": attempt_id},
    )

    evidence = _auditable_no_return_evidence(
        condition=replace(
            _condition(),
            experiment_id="exp3_real_ai_fault_recovery",
            fault_type="no_return",
            fault_rate=1.0,
        ),
        submission_event={},
        submission={},
        request={"request_id": request_id, "attempt_id": attempt_id},
        request_ref=request_ref.to_dict(),
        model_record={
            "attempt_id": attempt_id,
            "paper_eligible": True,
            "identity_status": "matched",
            "request_ref": request_ref.to_dict(),
            "raw_output_ref": raw_ref.to_dict(),
            "provenance_ref": model_provenance_ref.to_dict(),
            "usage_ref": model_usage_ref.to_dict(),
        },
        fault_record={
            "attempt_id": attempt_id,
            "fault_type": "no_return",
            "injection_point": "after_raw_output_before_submission",
            "mutated_attempt_status": "lease_expired",
            "original_attempt_status": "succeeded",
            "original_raw_output_ref": raw_ref.to_dict(),
            "mutation_summary": {"mutation_kind": "no_return_drop_submission"},
        },
        fault_injection_ref=primitive_fault_ref.to_dict(),
        runtime_fault_record={
            "attempt_id": attempt_id,
            "fault_type": "no_return",
            "applicability_status": "injected",
            "hook_stage": "after_raw_provenance_usage_before_parser",
            "injection_point": "after_raw_output_before_submission",
            "mutated_attempt_status": "lease_expired",
            "original_attempt_status": "succeeded",
            "original_raw_output_ref": raw_ref.to_dict(),
            "original_provenance_ref": runtime_provenance_ref.to_dict(),
            "pre_fault_usage_ref": response_usage_ref.to_dict(),
            "primitive_fault_record_ref": primitive_fault_ref.to_dict(),
        },
        runtime_fault_ref=runtime_fault_ref.to_dict(),
        store=store,
    )

    assert evidence == {}


def test_projection_keeps_ordinary_missing_submission_fail_closed(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_missing_submission"
    unit_id = "paper_factorization_case_missing_submission_range_0"
    attempt_id = "attempt_missing_submission_0"
    request_id = "request_missing_submission_0"
    request_ref = _save(
        store,
        "request-missing-submission",
        "ExecutionRequest",
        {
            "request_id": request_id,
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "created_at": NOW,
        },
    )
    events = _missing_submission_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_id=request_id,
        request_ref=request_ref.to_dict(),
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_missing_submission_case",
        task_id=task_id,
        root_unit_id=unit_id,
        status="failed",
        event_refs=tuple(_event_ref(event) for event in events),
        artifact_refs=(request_ref,),
    )

    projection = project_paper_protocol_run(
        condition=_condition(),
        case={"case_id": "case_missing_submission", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=events,
        artifact_store=store,
        accepted_validity=None,
    )

    attempt = projection.attempt_results[0]
    assert attempt.attempt_status.value == "provider_error"
    assert attempt.error_kind == "missing_submission_event"
    assert attempt.raw_output_ref is None
    assert attempt.provenance_ref is None
    assert attempt.paper_eligible is False


def test_projection_materializes_ai_pre_provider_error_without_provider_evidence(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    task_id = "paper_factorization_case_executor_error"
    unit_id = "paper_factorization_case_executor_error_range_0"
    attempt_id = "attempt_executor_error_0"
    request_ref = _save(
        store,
        "request-executor-error",
        "ExecutionRequest",
        {
            "request_id": "request_executor_error_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "executor": {"executor_id": "executor_ai_api"},
            "created_at": NOW,
        },
    )
    provenance_ref = _save(
        store,
        "provenance-executor-error",
        "AIProviderCallProvenance",
        {
            "final_result_kind": "executor_error",
            "attempts": [
                {
                    "entry_id": "glm_5_2_exp1_baseline",
                    "result_kind": "config_error",
                }
            ],
        },
    )
    submission_ref = _save(
        store,
        "submission-executor-error",
        "ExecutionSubmission",
        {
            "submission_id": "submission_executor_error_0",
            "request_id": "request_executor_error_0",
            "task_id": task_id,
            "unit_id": unit_id,
            "attempt_id": attempt_id,
            "result_kind": "executor_error",
            "raw_output_ref": None,
            "parsed_output_ref": None,
            "parse_failure_ref": None,
            "provenance_ref": provenance_ref.to_dict(),
            "usage_summary": {"provider_attempt_count": 0},
            "error": {
                "kind": "executor_error",
                "attempts": [{"result_kind": "config_error"}],
            },
            "submitted_at": "2026-07-23T00:00:00.017000Z",
        },
    )
    events = _executor_error_events(
        task_id=task_id,
        unit_id=unit_id,
        attempt_id=attempt_id,
        request_ref=request_ref.to_dict(),
        submission_ref=submission_ref.to_dict(),
    )
    runtime_result = ProtocolRunResult(
        run_id="condition_1_case_executor_error",
        task_id=task_id,
        root_unit_id=unit_id,
        status="failed",
        event_refs=tuple(_event_ref(event) for event in events),
        artifact_refs=(request_ref, submission_ref, provenance_ref),
    )

    projection = project_paper_protocol_run(
        condition=_condition(),
        case={"case_id": "case_executor_error", "difficulty": "easy"},
        runtime_result=runtime_result,
        protocol_events=events,
        artifact_store=store,
        accepted_validity=None,
    )

    attempt = projection.attempt_results[0]
    assert attempt.schema_version == "tokenshare.paper_attempt_result.v2"
    assert attempt.attempt_status.value == "executor_error"
    assert attempt.error_kind == "config_error"
    assert attempt.provider_attempt_count == 0
    assert attempt.provider is None
    assert attempt.model is None
    assert attempt.entry_id is None
    assert attempt.raw_output_ref is None
    assert attempt.usage_ref is None
    assert attempt.model_execution_record_ref is None
    assert attempt.provenance_ref == provenance_ref.to_dict()
    assert attempt.executor_id == "executor_ai_api"
    assert attempt.executor_type == "ai_api_pre_provider"
    assert attempt.paper_eligible is False
    assert projection.task_result.provider_attempt_count == 0
    assert projection.task_result.failure_stage.value == "request"
    assert projection.task_result.failure_kind.value == "executor_error"
    assert projection.task_result.paper_eligible is False


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


def _provider_failure_events(
    *,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    request_ref: dict,
    submission_ref: dict,
) -> tuple[dict, ...]:
    rows = (
        ("TASK_REGISTERED", {"task_id": task_id}),
        (
            "EXECUTION_REQUEST_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "request_id": "request_provider_failure_0",
                "request_ref": request_ref,
            },
        ),
        (
            "EXECUTION_SUBMISSION_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "submission_id": "submission_provider_failure_0",
                "submission_ref": submission_ref,
                "result_kind": "provider_error",
                "acceptance_status": "accepted",
            },
        ),
        (
            "TASK_UNIT_STATE_CHANGED",
            {
                "task_id": task_id,
                "old_state": "Processing",
                "new_state": "Failed",
                "task_unit": {
                    "task_id": task_id,
                    "unit_id": unit_id,
                    "state": "Failed",
                },
            },
        ),
    )
    return tuple(
        {
            "event_id": f"failure-event-{index}",
            "event_seq": index,
            "event_type": event_type,
            "task_id": task_id,
            "object_id": f"failure-object-{index}",
            "occurred_at": f"2026-07-23T00:00:0{index}Z",
            "event_hash": f"sha256:failure-event-{index}",
            "payload": payload,
        }
        for index, (event_type, payload) in enumerate(rows, start=1)
    )


def _executor_error_events(
    *,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    request_ref: dict,
    submission_ref: dict,
) -> tuple[dict, ...]:
    rows = (
        ("TASK_REGISTERED", {"task_id": task_id}),
        (
            "EXECUTION_REQUEST_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "request_id": "request_executor_error_0",
                "request_ref": request_ref,
            },
        ),
        (
            "EXECUTION_SUBMISSION_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "submission_id": "submission_executor_error_0",
                "submission_ref": submission_ref,
                "result_kind": "executor_error",
                "acceptance_status": "accepted",
            },
        ),
        (
            "TASK_UNIT_STATE_CHANGED",
            {
                "task_id": task_id,
                "old_state": "Processing",
                "new_state": "Failed",
                "task_unit": {
                    "task_id": task_id,
                    "unit_id": unit_id,
                    "state": "Failed",
                },
            },
        ),
    )
    return tuple(
        {
            "event_id": f"executor-error-event-{index}",
            "event_seq": index,
            "event_type": event_type,
            "task_id": task_id,
            "object_id": f"executor-error-object-{index}",
            "occurred_at": f"2026-07-23T00:00:0{index}Z",
            "event_hash": f"sha256:executor-error-event-{index}",
            "payload": payload,
        }
        for index, (event_type, payload) in enumerate(rows, start=1)
    )


def _missing_submission_events(
    *,
    task_id: str,
    unit_id: str,
    attempt_id: str,
    request_id: str,
    request_ref: dict,
) -> tuple[dict, ...]:
    rows = (
        ("TASK_REGISTERED", {"task_id": task_id}),
        (
            "EXECUTION_REQUEST_RECORDED",
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "request_id": request_id,
                "request_ref": request_ref,
            },
        ),
        (
            "ATTEMPT_STATE_CHANGED",
            {
                "task_id": task_id,
                "attempt": {
                    "attempt_id": attempt_id,
                    "unit_id": unit_id,
                    "state": "Failed",
                    "failure_kind": "lease_expired",
                },
            },
        ),
    )
    return tuple(
        {
            "event_id": f"missing-submission-event-{index}",
            "event_seq": index,
            "event_type": event_type,
            "task_id": task_id,
            "object_id": f"missing-submission-object-{index}",
            "occurred_at": f"2026-07-23T00:00:0{index}Z",
            "event_hash": f"sha256:missing-submission-event-{index}",
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
