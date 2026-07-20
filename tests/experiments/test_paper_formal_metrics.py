from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_formal_metrics import (
    recompute_paper_formal_metrics,
)


EXP1 = "exp1_real_ai_feasibility"
EXP2 = "exp2_real_ai_scalability"
EXP3 = "exp3_real_ai_fault_recovery"
EXP4 = "exp4_real_ai_protocol_ablation"
EXP5 = "exp5_real_ai_model_endpoint_comparison"


def test_formal_metrics_are_recomputed_from_mutable_input_evidence(
    tmp_path: Path,
) -> None:
    generation = _write_run(
        tmp_path,
        experiment_id=EXP1,
        condition_id="exp1-condition",
        condition={"domain": "factorization", "worker_count": 1},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "accepted_validity": True,
            "paper_eligible": False,
        },
        attempts=[_attempt(total_tokens=12, latency_ms=40)],
        events=_timing_events(100),
    )
    _write_suite_manifest(tmp_path, (EXP1,))

    first = recompute_paper_formal_metrics(tmp_path)
    first_row = first.experiment_rows[EXP1][0]
    assert first_row["completion_rate"] == 1.0
    assert first_row["accepted_validity_rate"] == 1.0
    assert first_row["token_p50"] == 12
    assert first.paper_eligible is False

    task_path = generation / "per_task_results.jsonl"
    changed = {
        **json.loads(task_path.read_text(encoding="utf-8")),
        "root_status": "failed",
        "accepted_validity": False,
    }
    task_path.write_text(json.dumps(changed) + "\n", encoding="utf-8")

    second = recompute_paper_formal_metrics(tmp_path)
    second_row = second.experiment_rows[EXP1][0]
    assert second_row["completion_rate"] == 0.0
    assert second_row["accepted_validity_rate"] == 0.0
    assert second.metrics_digest != first.metrics_digest


def test_formal_metrics_recompute_exp2_critical_path_and_all_experiment_views(
    tmp_path: Path,
) -> None:
    _write_run(
        tmp_path,
        experiment_id=EXP2,
        condition_id="exp2-w1",
        condition={"domain": "factorization", "difficulty": "easy", "worker_count": 1},
        task={"task_id": "case-1", "root_status": "completed"},
        attempts=[_attempt(total_tokens=10, latency_ms=80)],
        events=_scheduler_events(unit_ms=90, merge_ms=10, wall_ms=100),
    )
    _write_run(
        tmp_path,
        experiment_id=EXP2,
        condition_id="exp2-w2",
        condition={"domain": "factorization", "difficulty": "easy", "worker_count": 2},
        task={"task_id": "case-1", "root_status": "completed"},
        attempts=[
            {
                **_attempt(total_tokens=10, latency_ms=80),
                "attempt_status": "provider_error",
                "error_kind": "rate_limited",
            }
        ],
        events=_scheduler_events(unit_ms=40, merge_ms=10, wall_ms=50),
    )
    _write_run(
        tmp_path,
        experiment_id=EXP3,
        condition_id="exp3-fault",
        condition={"domain": "factorization", "worker_count": 10},
        task={"task_id": "case-1", "root_status": "completed"},
        attempts=[_attempt(total_tokens=10, latency_ms=30)],
        events=[
            {"event_type": "FAULT_INJECTED"},
            {"event_type": "REPLACEMENT_ACCEPTED"},
        ],
        faults=[
            {
                "fault_injection_id": "fault-1",
                "fault_type": "false_negative",
                "detected": True,
                "canonical_pollution": False,
                "recovery_required": True,
            }
        ],
    )
    _write_run(
        tmp_path,
        experiment_id=EXP4,
        condition_id="exp4-mode",
        condition={"domain": "factorization", "worker_count": 10, "ablation_mode": "NO_VERIFICATION"},
        task={
            "task_id": "case-1",
            "root_status": "completed",
            "final_deterministic_validity": False,
            "exposed_error_count": 1,
            "escaped_error_count": 1,
        },
        attempts=[_attempt(total_tokens=10, latency_ms=30)],
        events=[{"event_type": "ABLATION_BOUNDARY_APPLIED"}],
    )
    _write_run(
        tmp_path,
        experiment_id=EXP5,
        condition_id="exp5-member",
        condition={
            "domain": "factorization",
            "worker_count": 10,
            "provider_family": "siliconflow",
            "provider_model_id": "zai-org/GLM-5.2",
            "model_entry_id": "glm_5_2_exp1_baseline",
            "cohort_member_id": "glm_5_2_siliconflow",
        },
        task={"task_id": "case-1", "root_status": "completed"},
        attempts=[
            {
                **_attempt(total_tokens=13, latency_ms=25),
                "model_identity_audit": "fixed_entry_match",
                "cohort_member_id": "glm_5_2_siliconflow",
            }
        ],
        events=_timing_events(30),
    )
    _write_suite_manifest(tmp_path, (EXP2, EXP3, EXP4, EXP5))

    result = recompute_paper_formal_metrics(tmp_path)

    exp2_rows = {row["worker_count"]: row for row in result.experiment_rows[EXP2]}
    assert exp2_rows[1]["critical_path_ms"] == pytest.approx(100)
    assert exp2_rows[2]["critical_path_ms"] == pytest.approx(50)
    assert exp2_rows[2]["speedup"] == pytest.approx(2.0)
    assert exp2_rows[2]["efficiency"] == pytest.approx(1.0)
    assert exp2_rows[2]["rate_limited_attempt_count"] == 1
    assert result.experiment_rows[EXP3][0]["recovery_rate"] == 1.0
    assert result.experiment_rows[EXP4][0]["escaped_error_count"] == 1
    assert result.experiment_rows[EXP5][0]["model_identity_match_rate"] == 1.0

    for relative_path in (
        "metrics/per_condition_summary.csv",
        "metrics/paper_table_feasibility.csv",
        "metrics/paper_plot_scalability.csv",
        "metrics/paper_plot_robustness.csv",
        "metrics/paper_table_ablation.csv",
        "metrics/model_execution_records.jsonl",
        "metrics/failure_examples.json",
        "metrics/formal_metrics.json",
    ):
        assert (tmp_path / relative_path).is_file(), relative_path


def _write_suite_manifest(root: Path, experiment_ids: tuple[str, ...]) -> None:
    _write_json(
        root / "suite_manifest.json",
        {
            "formal": True,
            "pilot_only": False,
            "execution_scope": "formal_matrix",
            "regression_only": True,
            "paper_eligible": False,
            "experiment_ids": list(experiment_ids),
        },
    )


def _write_run(
    root: Path,
    *,
    experiment_id: str,
    condition_id: str,
    condition: dict[str, object],
    task: dict[str, object],
    attempts: list[dict[str, object]],
    events: list[dict[str, object]],
    faults: list[dict[str, object]] | None = None,
) -> Path:
    conditions_path = root / "conditions.jsonl"
    conditions_path.parent.mkdir(parents=True, exist_ok=True)
    with conditions_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "experiment_id": experiment_id,
                    "condition_id": condition_id,
                    "repeat_id": 0,
                    "difficulty": "easy",
                    "paper_difficulty": "easy",
                    **condition,
                }
            )
            + "\n"
        )
    run_root = root / "experiments" / experiment_id / "runs" / condition_id / "0"
    generation_id = "generation-1"
    generation = run_root / ".generations" / generation_id
    _write_json(run_root / "CURRENT.json", {"generation_id": generation_id})
    _write_jsonl(
        generation / "per_task_results.jsonl",
        [{"experiment_id": experiment_id, "condition_id": condition_id, "repeat_id": 0, **task}],
    )
    _write_jsonl(
        generation / "per_attempt_results.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": 0,
                "task_id": task["task_id"],
                **attempt,
            }
            for attempt in attempts
        ],
    )
    _write_jsonl(
        generation / "fault_injections.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": 0,
                "task_id": task["task_id"],
                **fault,
            }
            for fault in (faults or [])
        ],
    )
    _write_jsonl(
        generation / "events" / "event_log.jsonl",
        [
            {
                "experiment_id": experiment_id,
                "condition_id": condition_id,
                "repeat_id": 0,
                "task_id": task["task_id"],
                **event,
            }
            for event in events
        ],
    )
    return generation


def _attempt(*, total_tokens: int, latency_ms: int) -> dict[str, object]:
    return {
        "attempt_id": f"attempt-{total_tokens}-{latency_ms}",
        "unit_id": "unit-1",
        "attempt_status": "succeeded",
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp1_baseline",
        "total_tokens": total_tokens,
        "cost_estimate": 0.1,
        "latency_ms": latency_ms,
    }


def _timing_events(wall_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "AI_UNIT_STARTED", "offset_ms": 0},
        {"event_type": "AI_UNIT_ENDED", "offset_ms": wall_ms, "duration_ms": wall_ms},
    ]


def _scheduler_events(*, unit_ms: int, merge_ms: int, wall_ms: int) -> list[dict[str, object]]:
    return [
        {"event_type": "AI_UNIT_STARTED", "offset_ms": 0},
        {"event_type": "AI_UNIT_ENDED", "offset_ms": unit_ms, "duration_ms": unit_ms},
        {"event_type": "MERGE_GATE_COMPLETED", "offset_ms": wall_ms, "duration_ms": merge_ms},
    ]


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
