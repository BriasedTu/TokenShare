from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config

from tokenshare.experiments.paper_formal_callbacks import (
    PaperOnlineProviderEvidenceCallback,
    produce_exp3_online_recovery_evidence,
    run_exp1_normal_strategy,
    run_exp3_post_ai_strategy,
    run_exp3_worker_death_strategy,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_online_checks import (
    EXP3_RECOVERY_CHAIN_ROLES,
    freeze_paper_online_checks_plan,
)
from tokenshare.experiments.paper_workers import WorkerDeathKillPoint
from tokenshare.storage.artifacts import ArtifactStore
from tests.experiments.test_paper_workers import _protocol_events


@dataclass(frozen=True)
class _Attempt:
    unit_id: str
    attempt_id: str
    provider: str = "siliconflow"
    model: str = "zai-org/GLM-5.2"
    entry_id: str = "glm_5_2_exp1_baseline"
    raw_output_ref: dict[str, Any] | None = None
    provenance_ref: dict[str, Any] | None = None


def _replace_unit_id(value: Any, unit_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_unit_id(item, unit_id)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_replace_unit_id(item, unit_id) for item in value)
    if isinstance(value, list):
        return [_replace_unit_id(item, unit_id) for item in value]
    return unit_id if value == "unit_lemma_join" else value


def _replace_protocol_identity(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_protocol_identity(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_replace_protocol_identity(item, replacements) for item in value)
    if isinstance(value, list):
        return [_replace_protocol_identity(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def _single_entry_config() -> dict:
    body = make_config_dict()
    body["entries"] = [body["entries"][0]]
    return body


def _run_persisted_online_attempt(
    store: ArtifactStore,
    *,
    callback: PaperOnlineProviderEvidenceCallback,
    submission_id: str,
    submitted_at: str,
) -> object:
    request = dataclass_replace(
        make_ai_request(store, request_id=f"request-{submission_id}"),
        attempt_id=submission_id,
        unit_id="unit-1",
    )
    executor = AIAPIExecutor(
        executor_id="task25-official-exp3-callback",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(_single_entry_config()),
        transport=FakeSiliconFlowTransport(
            [
                FakeProviderResponse(
                    status_code=200,
                    body={
                        "id": f"response-{submission_id}",
                        "model": "Qwen/Qwen2.5-7B-Instruct",
                        "choices": [{"message": {"content": "candidate"}}],
                        "usage": {
                            "prompt_tokens": 11,
                            "completion_tokens": 13,
                            "total_tokens": 24,
                        },
                    },
                )
            ]
        ),
        post_raw_output_hook=callback,
    )
    submission = executor.execute(
        request,
        submission_id=submission_id,
        submitted_at=submitted_at,
    )
    assert submission.raw_output_ref is not None
    return callback.require_capture(submission_id)


def test_exp1_strategy_consumes_complete_frozen_order() -> None:
    calls: list[str] = []

    result = run_exp1_normal_strategy(
        ordered_case_ids=("case-c", "case-a", "case-b"),
        execute_case=lambda case_id, worker_id: calls.append(case_id) or case_id,
    )

    assert calls == ["case-c", "case-a", "case-b"]
    assert result.ordered_case_ids == ("case-c", "case-a", "case-b")
    assert result.outcomes == ("case-c", "case-a", "case-b")
    assert result.metrics["observed_max_parallel_slots"] is None
    assert result.metrics["runtime_evidence_status"] == "unavailable"
    assert (
        result.metrics["runtime_evidence_unavailable_reason"]
        == "missing_worker_execution_facts"
    )


def test_scheduler_uses_persisted_runtime_observation_and_retry_attempt_identity() -> None:
    runtime_observation = {
        "schema_version": "tokenshare.protocol_runtime_observation.v1",
        "run_id": "run-case-1",
        "runtime_started_at": "2026-07-20T00:00:00.000Z",
        "runtime_ended_at": "2026-07-20T00:00:00.250Z",
        "runtime_wall_clock_ms": 250.0,
        "worker_execution_facts": [
            {
                "attempt_id": "attempt-root",
                "execution_index": 1,
                "unit_id": "root",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.010Z",
                "dependencies": [],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-a-failed",
                "execution_index": 2,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.010Z",
                "ended_at": "2026-07-20T00:00:00.110Z",
                "dependencies": ["root"],
                "result_kind": "executor_error",
            },
            {
                "attempt_id": "attempt-b",
                "execution_index": 3,
                "unit_id": "child-b",
                "started_at": "2026-07-20T00:00:00.010Z",
                "ended_at": "2026-07-20T00:00:00.160Z",
                "dependencies": ["root"],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-a-retry",
                "execution_index": 4,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.110Z",
                "ended_at": "2026-07-20T00:00:00.210Z",
                "dependencies": ["root"],
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-merge",
                "execution_index": 5,
                "unit_id": "merge",
                "started_at": "2026-07-20T00:00:00.210Z",
                "ended_at": "2026-07-20T00:00:00.230Z",
                "dependencies": ["child-a", "child-b"],
                "result_kind": "succeeded",
            },
        ],
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=lambda case_id, worker_count: {
            "case_id": case_id,
            "adapter_result": {
                "run_evidence": {
                    "protocol_runtime": {
                        "runtime_observation": runtime_observation,
                    }
                }
            },
            "provider_latency_ms": 350,
            "provider_error_kind": "executor_error",
        },
    )

    assert result.metrics["runtime_evidence_status"] == "complete"
    assert result.metrics["runtime_evidence_unavailable_reason"] is None
    assert result.metrics["condition_started_at"] == "2026-07-20T00:00:00.000Z"
    assert result.metrics["condition_ended_at"] == "2026-07-20T00:00:00.250Z"
    assert result.metrics["wall_clock_ms"] == 250
    assert result.metrics["observed_max_parallel_slots"] == 2
    assert result.metrics["critical_path_ms"] == 230
    assert result.metrics["throughput_completed_units_per_second"] == 16
    assert result.metrics["critical_path_evidence_status"] == "complete"
    assert result.metrics["critical_path_unavailable_reason"] is None


def test_scheduler_does_not_invent_critical_path_without_dependency_evidence() -> None:
    runtime_observation = {
        "schema_version": "tokenshare.protocol_runtime_observation.v1",
        "run_id": "run-case-1",
        "runtime_started_at": "2026-07-20T00:00:00.000Z",
        "runtime_ended_at": "2026-07-20T00:00:00.250Z",
        "runtime_wall_clock_ms": 250.0,
        "worker_execution_facts": [
            {
                "attempt_id": "attempt-a",
                "execution_index": 1,
                "unit_id": "child-a",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.200Z",
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-b",
                "execution_index": 2,
                "unit_id": "child-b",
                "started_at": "2026-07-20T00:00:00.000Z",
                "ended_at": "2026-07-20T00:00:00.150Z",
                "result_kind": "succeeded",
            },
            {
                "attempt_id": "attempt-merge",
                "execution_index": 3,
                "unit_id": "merge",
                "started_at": "2026-07-20T00:00:00.200Z",
                "ended_at": "2026-07-20T00:00:00.250Z",
                "result_kind": "succeeded",
            },
        ],
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=lambda case_id, worker_count: {
            "case_id": case_id,
            "adapter_result": {
                "run_evidence": {
                    "protocol_runtime": {
                        "runtime_observation": runtime_observation,
                    }
                }
            },
        },
    )

    assert result.metrics["runtime_evidence_status"] == "complete"
    assert result.metrics["wall_clock_ms"] == 250
    assert result.metrics["observed_max_parallel_slots"] == 2
    assert result.metrics["throughput_completed_units_per_second"] == 12
    assert result.metrics["critical_path_ms"] is None
    assert result.metrics["critical_path_evidence_status"] == "unavailable"
    assert (
        result.metrics["critical_path_unavailable_reason"]
        == "missing_protocol_dependency_evidence"
    )


def test_scheduler_marks_executed_case_without_runtime_facts_as_missing() -> None:
    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=10,
        execute_case=lambda case_id, worker_count: {"case_id": case_id},
    )

    assert result.metrics["runtime_evidence_status"] == "unavailable"
    assert (
        result.metrics["runtime_evidence_unavailable_reason"]
        == "missing_worker_execution_facts"
    )
    for field_name in (
        "observed_max_parallel_slots",
        "condition_started_at",
        "condition_ended_at",
        "wall_clock_ms",
        "critical_path_ms",
        "throughput_completed_units_per_second",
    ):
        assert result.metrics[field_name] is None


def test_exp2_scheduler_delegates_capacity_to_one_root_runtime() -> None:
    capacities: list[int] = []

    def execute(case_id: str, worker_capacity: int) -> dict[str, Any]:
        capacities.append(worker_capacity)
        return {
            "case_id": case_id,
            "provider_latency_ms": 28,
            "provider_error_kind": "rate_limited",
            "runtime_records": (
                {
                    "unit_id": "child-left",
                    "started_at": "2026-07-20T00:00:00.000Z",
                    "ended_at": "2026-07-20T00:00:00.200Z",
                    "dependencies": [],
                    "result_kind": "succeeded",
                },
                {
                    "unit_id": "child-right",
                    "started_at": "2026-07-20T00:00:00.000Z",
                    "ended_at": "2026-07-20T00:00:00.150Z",
                    "dependencies": [],
                    "result_kind": "succeeded",
                },
                {
                    "unit_id": "merge",
                    "started_at": "2026-07-20T00:00:00.200Z",
                    "ended_at": "2026-07-20T00:00:00.250Z",
                    "dependencies": ["child-left", "child-right"],
                    "result_kind": "succeeded",
                },
            ),
        }

    parallel = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=2,
        execute_case=execute,
    )

    assert capacities == [2]
    assert parallel.metrics["observed_max_parallel_slots"] == 2
    assert parallel.ordered_case_ids == ("case-1",)
    assert tuple(item["case_id"] for item in parallel.outcomes) == parallel.ordered_case_ids
    assert parallel.events == ()
    assert parallel.metrics["wall_clock_ms"] == 250
    assert parallel.metrics["critical_path_ms"] == 250
    assert parallel.metrics["provider_latency_sum_ms"] == 28
    assert parallel.metrics["provider_latency_sum_ms"] != parallel.metrics["wall_clock_ms"]
    assert parallel.metrics["provider_error_count"] == 1


def test_online_scheduler_releases_each_full_outcome_after_callback() -> None:
    class TrackedOutcome:
        live = 0
        max_live = 0

        def __init__(self, case_id: str) -> None:
            self.case_id = case_id
            self.provider_attempt_count = 0
            self.provider_latency_ms = None
            self.provider_error_kind = None
            self.runtime_records = ()
            type(self).live += 1
            type(self).max_live = max(type(self).max_live, type(self).live)

        def __del__(self) -> None:
            type(self).live -= 1

    checkpointed: list[str] = []
    result = run_scheduled_cases(
        ordered_case_ids=tuple(f"case-{index}" for index in range(20)),
        worker_count=1,
        execute_case=lambda case_id, worker_count: TrackedOutcome(case_id),
        on_case_complete=lambda case_id, outcome: checkpointed.append(case_id),
        retain_outcomes=False,
    )

    assert checkpointed == list(result.ordered_case_ids)
    assert result.outcomes == ()
    assert TrackedOutcome.max_live == 1
    assert TrackedOutcome.live == 0


def test_scheduler_provider_latency_is_null_when_required_latency_is_missing() -> None:
    outcomes = {
        "case-complete": {
            "case_id": "case-complete",
            "provider_attempt_count": 1,
            "provider_latency_ms": 28,
        },
        "case-missing": {
            "case_id": "case-missing",
            "provider_attempt_count": 1,
            "provider_latency_ms": None,
        },
    }

    result = run_scheduled_cases(
        ordered_case_ids=("case-complete", "case-missing"),
        worker_count=1,
        execute_case=lambda case_id, _worker_count: outcomes[case_id],
    )

    assert result.metrics["provider_latency_sum_ms"] is None
    assert result.metrics["provider_latency_evidence_status"] == "incomplete"
    assert (
        result.metrics["provider_latency_unavailable_reason"]
        == "missing_provider_latency_evidence"
    )


def test_scheduler_zero_call_latency_is_explicitly_not_applicable() -> None:
    result = run_scheduled_cases(
        ordered_case_ids=("case-pre-provider-error",),
        worker_count=1,
        execute_case=lambda case_id, _worker_count: {
            "case_id": case_id,
            "provider_attempt_count": 0,
            "provider_latency_ms": 0,
            "provider_error_kind": "executor_error",
        },
    )

    assert result.metrics["provider_latency_sum_ms"] == 0
    assert result.metrics["provider_latency_evidence_status"] == "not_applicable"
    assert (
        result.metrics["provider_latency_unavailable_reason"]
        == "no_provider_attempts"
    )


def test_exp2_scheduler_reports_unsupported_level_without_executing() -> None:
    calls: list[str] = []

    result = run_scheduled_cases(
        ordered_case_ids=("case-1",),
        worker_count=64,
        supported_worker_counts=(1, 2, 4),
        execute_case=lambda case_id, worker_id: calls.append(case_id),
    )

    assert result.status == "unsupported_worker_level"
    assert result.outcomes == ()
    assert calls == []


def test_exp3_post_ai_fault_is_injected_after_raw_and_uses_fixed_identity() -> None:
    order: list[str] = []
    original = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-original",
        raw_output_ref={"artifact_id": "raw-original"},
        provenance_ref={"artifact_id": "provenance-original"},
    )
    replacement = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-replacement",
        raw_output_ref={"artifact_id": "raw-replacement"},
        provenance_ref={"artifact_id": "provenance-replacement"},
    )

    def inject(attempt: _Attempt, fault_type: str) -> dict[str, Any]:
        assert attempt.raw_output_ref is not None
        assert attempt.provenance_ref is not None
        order.append("inject")
        return {
            "fault_type": fault_type,
            "original_output_ref": attempt.raw_output_ref,
            "mutated_output_ref": {"artifact_id": "mutated-output"},
            "detected": True,
            "requires_replacement": True,
        }

    def replace(attempt: _Attempt) -> _Attempt:
        order.append("replace")
        return replacement

    result = run_exp3_post_ai_strategy(
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="false_negative",
        inject_fault=inject,
        execute_replacement=replace,
        approved_identity={
            "provider": "siliconflow",
            "model": "zai-org/GLM-5.2",
            "entry_id": "glm_5_2_exp1_baseline",
        },
    )

    assert order == ["inject", "replace"]
    assert result.fault_records[0]["original_output_ref"]["artifact_id"] == "raw-original"
    assert result.fault_records[0]["mutated_output_ref"]["artifact_id"] == "mutated-output"
    assert result.replacement_attempts == (replacement,)
    assert result.events[0]["event_type"] == "EXPERIMENT_PROVIDER_RAW_OBSERVED"
    assert result.events[-1]["event_type"] == "EXPERIMENT_REPLACEMENT_ACCEPTED"


def test_ai_executor_post_raw_hook_runs_after_persistence_and_before_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="formal-post-raw-hook")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "formal-hook-response",
                    "model": "deepseek-ai/DeepSeek-V3",
                    "choices": [{"message": {"content": "original"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                },
            )
        ]
    )
    order: list[str] = []

    def post_raw_output_hook(**context):
        assert store.verify(context["raw_output_ref"])
        assert store.verify(context["provenance_ref"])
        assert store.verify(context["usage_ref"])
        order.append("fault_hook")
        return {"content_text": "mutated-before-parser"}

    def parser(raw_text: str):
        order.append("parser")
        assert raw_text == "mutated-before-parser"
        raise ValueError("expected parser stop")

    executor = AIAPIExecutor(
        executor_id="formal-hook-test",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
        post_raw_output_hook=post_raw_output_hook,
    )

    submission = executor.execute(
        request,
        submission_id="formal-hook-submission",
        submitted_at="2026-07-20T00:00:02Z",
    )

    assert submission.result_kind == "parse_failed"
    assert order == ["fault_hook", "parser"]


def test_exp3_worker_death_strategy_projects_runtime_evidence_without_fake_protocol(
    tmp_path: Path,
) -> None:
    result = run_exp3_worker_death_strategy(
        artifact_root=tmp_path,
        condition_id="condition-worker-death",
        repeat_id=0,
        task_id="case-1",
        ai_unit_ids=("unit-1",),
        dead_worker_count=1,
        kill_point=WorkerDeathKillPoint.PROGRESS_25,
        started_at="2026-07-20T00:00:00Z",
        process_tick_seconds=0.005,
        runtime_observations=(
            {
                "worker_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": "attempt_initial",
                    "lease_id": "lease_initial",
                    "worker_id": "process-worker-1",
                        "worker_pid": 1001,
                        "process_exitcode": -15,
                        "result_kind": "worker_terminated",
                        "kill_point": "progress_25",
                        "kill_progress_target_ratio": 0.25,
                        "kill_progress_completed_ai_unit_count": 1,
                        "kill_progress_total_ai_unit_count": 1,
                        "kill_progress_actual_ratio": 1.0,
                        "kill_progress_observed_at": "2026-07-20T00:00:01Z",
                        "kill_progress_error": None,
                    "started_at": "2026-07-20T00:00:00Z",
                    "ended_at": "2026-07-20T00:00:01Z",
                },
                "replacement_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": "attempt_replacement",
                    "lease_id": "lease_replacement",
                    "worker_id": "process-worker-2",
                    "worker_pid": 1002,
                    "process_exitcode": 0,
                    "result_kind": "succeeded",
                    "started_at": "2026-07-20T00:00:10Z",
                    "ended_at": "2026-07-20T00:00:11Z",
                },
                "protocol_events": _replace_unit_id(_protocol_events(), "unit-1"),
                "coordinator_pid": 999,
                "created_at": "2026-07-20T00:00:11Z",
            },
        ),
    )

    record = result.fault_records[0]
    assert record["worker_process_exitcode"] not in (0, None)
    assert record["replacement_process_exitcode"] == 0
    assert record["coordinator"]["survived"] is True
    assert record["worker_pid"] != record["replacement_worker_pid"]
    assert [event["event_type"] for event in result.events] == [
        "EXPERIMENT_WORKER_DEATH_PLAN_FROZEN",
        "EXPERIMENT_WORKER_DEATH_OBSERVED",
    ]
    assert result.events[-1]["protocol_event_refs"] == record["protocol_event_refs"]


@pytest.mark.parametrize(
    ("mode", "expected_flag"),
    [
        ("FULL", "default_protocol_behavior"),
        ("NO_VERIFICATION", "wrong_canonical_exposed"),
        ("NO_PARSER_POLICY", "raw_only_exposed"),
        ("NO_REQUEUE", "stuck_after_rejection"),
        ("NO_MERGE_GATE", "premature_merge_attempted"),
    ],
)
def test_exp4_strategy_changes_only_the_selected_boundary(
    mode: str,
    expected_flag: str,
) -> None:
    result = run_exp4_ablation_strategy(
        mode=mode,
        adapter_observation={
            "candidate_rejected": True,
            "parse_failed": True,
            "replacement_created": True,
            "merge_gate_blocked": True,
            "slot_binding_valid": True,
            "deterministic_validity": False,
        },
    )

    assert result.mode == mode
    assert result.runtime_flags[expected_flag] is True
    assert result.runtime_flags["deterministic_validity_audit_retained"] is True
    if mode != "FULL":
        assert result.metrics["applicable"] is True


def test_exp4_formal_strategy_rejects_retired_slot_integrity_mode() -> None:
    with pytest.raises(ValueError, match="unsupported Experiment 4 mode"):
        run_exp4_ablation_strategy(
            mode="NO_SLOT_INTEGRITY",
            adapter_observation={},
        )
        assert result.metrics["exposed_error_count"] == 1


def test_exp5_strategy_rejects_failover_and_requires_persisted_model_records(
    tmp_path: Path,
) -> None:
    approved = {
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "cohort_member_id": "glm_5_2_siliconflow",
    }
    attempt = _Attempt(unit_id="unit-1", attempt_id="attempt-1")

    shared = {
        "approved_identity": approved,
        "condition_id": "condition-exp5",
        "cohort_member_id": "glm_5_2_siliconflow",
        "adapter_root": tmp_path,
        "task": {},
        "transport_kind": "offline_capture",
        "model_policy": "fixed_entry",
        "pilot_only": True,
    }
    with pytest.raises(ValueError, match="model_execution_record_ref"):
        run_exp5_identity_strategy(attempts=(attempt,), **shared)

    drifted = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-failover",
        model="other-model",
    )
    with pytest.raises(ValueError, match="failover"):
        run_exp5_identity_strategy(
            attempts=(drifted,),
            **shared,
        )


def test_runner_uses_logical_scheduler_for_trace_and_real_clock_for_online(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_formal_callbacks import runtime_timing_policy
    from tokenshare.core.models import ArtifactRef
    from tokenshare.local_runtime.contracts import PreparedTraceDelivery
    from tokenshare.storage.artifacts import ArtifactStore

    trace = runtime_timing_policy(evidence_class="real_model_trace_protocol_run")
    online = runtime_timing_policy(evidence_class="online_real_provider")

    assert trace.trace_delay_policy == "logical_source_latency_1x"
    assert trace.logical_scheduler is not None
    assert online.trace_delay_policy == "online_real_time"
    assert online.logical_scheduler is None

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    runtime = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
    )
    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        trace_context=runtime,
    )
    assert result.task_result.wall_clock_ms > 0
    trace_store = ArtifactStore(Path(result.output_root))
    deliveries = [
        PreparedTraceDelivery.from_dict(
            json.loads(
                trace_store.read_bytes(
                    ArtifactRef.from_dict(event["payload"]["current_wrapper_ref"])
                ).decode("utf-8")
            )
        )
        for event in result.event_records
        if event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
    ]
    assert {delivery.source_latency_ms for delivery in deliveries} == {10}


def test_worker_death_parent_commit_and_ordinal_replacement_flow(
    tmp_path: Path,
) -> None:
    from tests.experiments.test_factorization_paper_adapter import _v2_condition
    from tests.experiments.test_paper_formal_runner import (
        _trace_context_from_adapter_result,
    )
    from tokenshare.experiments.factorization_paper_adapter import (
        ScriptedFactorizationRangeTransport,
        run_factorization_paper_case,
    )
    from tokenshare.experiments.paper_dispatcher import dispatch_paper_case
    from tokenshare.experiments.paper_factorization_catalog import (
        generate_factorization_paper_cases,
    )
    from tokenshare.experiments.paper_models import PaperAttemptStatus
    from tokenshare.local_runtime import WorkerTerminationPolicy

    case = generate_factorization_paper_cases()[0]
    condition = _v2_condition(case)
    source = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "source",
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
    )
    trace_context = _trace_context_from_adapter_result(
        bank_root=tmp_path / "bank",
        adapter_result=source,
        replacement_count=2,
    )
    result = dispatch_paper_case(
        case=case,
        condition=condition,
        output_root=(tmp_path / "trace").as_posix(),
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=None,
        entry_id=None,
        max_tokens=512,
        timeout_seconds=30,
        worker_termination_policy=WorkerTerminationPolicy(
            target_planned_ai_unit_ids=("range_0",),
            kill_point="progress_25",
            total_planned_ai_unit_count=2,
        ),
        trace_context=trace_context,
    )

    dead = [
        attempt
        for attempt in result.attempt_results
        if attempt.attempt_status == PaperAttemptStatus.WORKER_DIED
    ]
    replacement = [
        attempt
        for attempt in result.attempt_results
        if attempt.planned_ai_unit_id == "range_0"
        and attempt.attempt_status == PaperAttemptStatus.SUCCEEDED
    ]
    committed_attempt_ids = {
        event["payload"]["attempt_id"]
        for event in result.event_records
        if event["event_type"] == "TRACE_DELIVERY_COMMITTED.v1"
    }
    assert len(dead) == 1
    assert len(replacement) == 1
    assert dead[0].attempt_id not in committed_attempt_ids
    assert replacement[0].attempt_id in committed_attempt_ids
    assert replacement[0].entry_id == "entry-range_0-1"


def test_exp3_producer_emits_fault_death_before_distinct_new_attempt_provider_raw_provenance_usage_model_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    condition_ref = plan.exp3_root_refs[0]
    store = ArtifactStore(tmp_path)
    original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=0
        ),
        submission_id="attempt-initial",
        submitted_at="2026-08-03T00:00:00Z",
    )
    replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=1
        ),
        submission_id="attempt-replacement",
        submitted_at="2026-08-03T00:00:03Z",
    )
    strategy = run_exp3_post_ai_strategy(
        artifact_root=tmp_path,
        condition_ref=condition_ref,
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="false_positive",
        inject_fault=lambda attempt, fault_type: {
            "fault_type": fault_type,
            "original_output_ref": attempt.raw_output_ref,
            "mutated_output_ref": attempt.raw_output_ref,
            "requires_replacement": True,
        },
        execute_replacement=lambda attempt: replacement,
        approved_identity={
            "provider": "siliconflow",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "entry_id": "sf_qwen",
        },
    )

    evidence = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=replacement,
    )

    assert evidence.recovery_source == "persisted_fault_event"
    assert evidence.initial_source_raw_ref == original.raw_or_failure_ref
    assert evidence.original_attempt_id != evidence.replacement_attempt_id
    assert evidence.replacement_attempt_ordinal == evidence.original_attempt_ordinal + 1
    assert tuple(item.role for item in evidence.ordered_replacement_evidence) == (
        EXP3_RECOVERY_CHAIN_ROLES
    )
    refs = [item.ref.artifact_ref for item in evidence.ordered_replacement_evidence]
    assert all(store.verify(ref) for ref in refs)
    assert len({ref.artifact_id for ref in refs}) == len(refs)
    assert evidence.eligibility_input.value is True

    wrong_kind = run_exp3_post_ai_strategy(
        artifact_root=tmp_path / "wrong-kind",
        condition_ref=condition_ref,
        attempts=(original,),
        selected_target_ai_unit_ids=("unit-1",),
        fault_type="worker_death",
        inject_fault=lambda attempt, fault_type: {
            "fault_type": fault_type,
            "requires_replacement": True,
        },
        execute_replacement=lambda attempt: replacement,
        approved_identity={
            "provider": "siliconflow",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "entry_id": "sf_qwen",
        },
    )
    crossed = produce_exp3_online_recovery_evidence(
        artifact_store=ArtifactStore(tmp_path / "wrong-kind"),
        condition_ref=condition_ref,
        strategy_result=wrong_kind,
        original_attempt=original,
        replacement_attempt=replacement,
    )
    assert crossed.eligibility_input.blocked is True


def test_exp3_producer_records_actual_usage_cost_and_wasted_inputs_without_computing_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "synthetic-key")
    plan = freeze_paper_online_checks_plan()
    condition_ref = plan.exp3_root_refs[1]
    store = ArtifactStore(tmp_path)
    original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=0
        ),
        submission_id="attempt-dead",
        submitted_at="2026-08-03T00:00:00Z",
    )
    replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            condition_ref, attempt_ordinal=1
        ),
        submission_id="attempt-reassigned",
        submitted_at="2026-08-03T00:00:03Z",
    )
    strategy = run_exp3_worker_death_strategy(
        artifact_root=tmp_path,
        condition_id=condition_ref.condition_id,
        repeat_id=0,
        task_id=condition_ref.case_id,
        ai_unit_ids=("unit-1", "unit-2"),
        dead_worker_count=1,
        kill_point=WorkerDeathKillPoint.PROGRESS_50,
        started_at="2026-08-03T00:00:01Z",
        runtime_observations=(
            {
                "worker_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": original.attempt_id,
                    "lease_id": "lease-dead",
                    "worker_id": "process-worker-1",
                    "worker_pid": 1001,
                    "process_exitcode": -15,
                    "result_kind": "worker_terminated",
                    "kill_point": "progress_50",
                    "kill_progress_target_ratio": 0.5,
                    "kill_progress_completed_ai_unit_count": 1,
                    "kill_progress_total_ai_unit_count": 2,
                    "kill_progress_actual_ratio": 0.5,
                    "kill_progress_observed_at": "2026-08-03T00:00:01Z",
                    "kill_progress_error": None,
                    "started_at": "2026-08-03T00:00:00Z",
                    "ended_at": "2026-08-03T00:00:01Z",
                },
                "replacement_fact": {
                    "unit_id": "unit-1",
                    "attempt_id": replacement.attempt_id,
                    "attempt_ordinal": 1,
                    "lease_id": "lease-replacement",
                    "worker_id": "process-worker-2",
                    "worker_pid": 1002,
                    "process_exitcode": 0,
                    "result_kind": "succeeded",
                    "started_at": "2026-08-03T00:00:02Z",
                    "ended_at": "2026-08-03T00:00:04Z",
                },
                "protocol_events": _replace_protocol_identity(
                    _replace_unit_id(_protocol_events(), "unit-1"),
                    {
                        "attempt_initial": original.attempt_id,
                        "lease_initial": "lease-dead",
                        "attempt_replacement": replacement.attempt_id,
                        "lease_replacement": "lease-replacement",
                    },
                ),
                "coordinator_pid": 999,
                "created_at": "2026-08-03T00:00:04Z",
            },
        ),
    )

    evidence = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=replacement,
    )

    assert [item.actual_provider_call.value for item in evidence.provider_inputs] == [
        1,
        1,
    ]
    assert [item.total_tokens.value for item in evidence.provider_inputs] == [24, 24]
    assert [item.cost_estimate_cny.value for item in evidence.provider_inputs] == [
        Decimal("0.0"),
        Decimal("0.0"),
    ]
    assert len(evidence.discarded_current_inputs) == 1
    assert evidence.discarded_current_inputs[0].attempt_id == original.attempt_id
    assert evidence.discarded_current_inputs[0].total_tokens.value == 24

    incomplete = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=dataclass_replace(
            replacement, pricing_ref=original.pricing_ref
        ),
    )
    assert incomplete.eligibility_input.value is None
    assert incomplete.eligibility_input.blocked is True
    assert incomplete.provider_inputs[-1].cost_estimate_cny.value is None
    assert incomplete.provider_inputs[-1].cost_estimate_cny.blocked is True

    wrong_ordinal = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=condition_ref,
        strategy_result=strategy,
        original_attempt=original,
        replacement_attempt=dataclass_replace(
            replacement, attempt_ordinal=2
        ),
    )
    assert wrong_ordinal.eligibility_input.blocked is True

    false_positive_condition = plan.exp3_root_refs[0]
    false_original = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            false_positive_condition, attempt_ordinal=0
        ),
        submission_id="attempt-false-cross-initial",
        submitted_at="2026-08-03T00:00:05Z",
    )
    false_replacement = _run_persisted_online_attempt(
        store,
        callback=PaperOnlineProviderEvidenceCallback.for_exp3_attempt(
            false_positive_condition, attempt_ordinal=1
        ),
        submission_id="attempt-false-cross-replacement",
        submitted_at="2026-08-03T00:00:08Z",
    )
    crossed_death = produce_exp3_online_recovery_evidence(
        artifact_store=store,
        condition_ref=false_positive_condition,
        strategy_result=strategy,
        original_attempt=false_original,
        replacement_attempt=false_replacement,
    )
    assert crossed_death.eligibility_input.blocked is True
