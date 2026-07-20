from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
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
    run_exp1_normal_strategy,
    run_exp3_post_ai_strategy,
    run_exp3_worker_death_strategy,
    run_exp4_ablation_strategy,
    run_exp5_identity_strategy,
    run_scheduled_cases,
)
from tokenshare.experiments.paper_workers import WorkerDeathKillPoint
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True)
class _Attempt:
    unit_id: str
    attempt_id: str
    provider: str = "siliconflow"
    model: str = "zai-org/GLM-5.2"
    entry_id: str = "glm_5_2_exp1_baseline"
    raw_output_ref: dict[str, Any] | None = None
    provenance_ref: dict[str, Any] | None = None


def test_exp1_strategy_consumes_complete_frozen_order() -> None:
    calls: list[str] = []

    result = run_exp1_normal_strategy(
        ordered_case_ids=("case-c", "case-a", "case-b"),
        execute_case=lambda case_id, worker_id: calls.append(case_id) or case_id,
    )

    assert calls == ["case-c", "case-a", "case-b"]
    assert result.ordered_case_ids == ("case-c", "case-a", "case-b")
    assert result.outcomes == ("case-c", "case-a", "case-b")
    assert result.metrics["observed_max_parallel_slots"] == 1


def test_exp2_scheduler_changes_actual_parallel_slots_and_preserves_order() -> None:
    lock = Lock()
    active = 0
    observed = 0

    def execute(case_id: str, worker_id: str) -> dict[str, Any]:
        nonlocal active, observed
        with lock:
            active += 1
            observed = max(observed, active)
        sleep(0.03)
        with lock:
            active -= 1
        return {
            "case_id": case_id,
            "worker_id": worker_id,
            "provider_latency_ms": 7,
            "provider_error_kind": "rate_limited" if case_id == "case-2" else None,
        }

    serial = run_scheduled_cases(
        ordered_case_ids=("case-1", "case-2", "case-3", "case-4"),
        worker_count=1,
        execute_case=execute,
    )
    parallel = run_scheduled_cases(
        ordered_case_ids=("case-1", "case-2", "case-3", "case-4"),
        worker_count=2,
        execute_case=execute,
    )

    assert serial.metrics["observed_max_parallel_slots"] == 1
    assert parallel.metrics["observed_max_parallel_slots"] == 2
    assert observed == 2
    assert parallel.ordered_case_ids == ("case-1", "case-2", "case-3", "case-4")
    assert tuple(item["case_id"] for item in parallel.outcomes) == parallel.ordered_case_ids
    unit_events = [event for event in parallel.events if event["event_type"] == "AI_UNIT_ENDED"]
    assert len(unit_events) == 4
    assert all(event["started_at"] < event["ended_at"] for event in unit_events)
    assert all(event["worker_id"] for event in unit_events)
    assert parallel.metrics["wall_clock_ms"] >= parallel.metrics["critical_path_ms"]
    assert parallel.metrics["provider_latency_sum_ms"] == 28
    assert parallel.metrics["provider_latency_sum_ms"] != parallel.metrics["wall_clock_ms"]
    assert parallel.metrics["provider_error_count"] == 1


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
    assert result.events[0]["event_type"] == "PROVIDER_RAW_PERSISTED"
    assert result.events[-1]["event_type"] == "REPLACEMENT_ACCEPTED"


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


def test_exp3_worker_death_strategy_uses_process_harness_and_orders_recovery(
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
    )

    record = result.fault_records[0]
    assert record["worker_process_exitcode"] not in (0, None)
    assert record["replacement_process_exitcode"] == 0
    assert record["coordinator"]["survived"] is True
    assert record["worker_pid"] != record["replacement_worker_pid"]
    assert [event["event_type"] for event in result.events] == [
        "WORKER_TERMINATED",
        "LEASE_EXPIRED",
        "UNIT_REASSIGNED",
        "REPLACEMENT_ACCEPTED",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_flag"),
    [
        ("FULL", "default_protocol_behavior"),
        ("NO_VERIFICATION", "wrong_canonical_exposed"),
        ("NO_PARSER_POLICY", "raw_only_exposed"),
        ("NO_REQUEUE", "stuck_after_rejection"),
        ("NO_MERGE_GATE", "premature_merge_attempted"),
        ("NO_SLOT_INTEGRITY", "slot_mismatch_exposed"),
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
        assert result.metrics["exposed_error_count"] == 1


def test_exp5_strategy_rejects_failover_and_emits_model_records() -> None:
    approved = {
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "cohort_member_id": "glm_5_2_siliconflow",
    }
    attempt = _Attempt(unit_id="unit-1", attempt_id="attempt-1")

    result = run_exp5_identity_strategy(
        attempts=(attempt,),
        approved_identity=approved,
        condition_id="condition-exp5",
        cohort_member_id="glm_5_2_siliconflow",
    )
    assert result.model_execution_records[0]["attempt_id"] == "attempt-1"
    assert result.model_execution_records[0]["cohort_member_id"] == (
        "glm_5_2_siliconflow"
    )

    drifted = _Attempt(
        unit_id="unit-1",
        attempt_id="attempt-failover",
        model="other-model",
    )
    with pytest.raises(ValueError, match="failover"):
        run_exp5_identity_strategy(
            attempts=(drifted,),
            approved_identity=approved,
            condition_id="condition-exp5",
            cohort_member_id="glm_5_2_siliconflow",
        )
