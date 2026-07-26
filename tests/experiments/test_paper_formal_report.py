from __future__ import annotations

import json
from pathlib import Path

from tokenshare.experiments.paper_formal_metrics import (
    FormalMetricsResult,
    recompute_paper_formal_metrics,
)
from tokenshare.experiments.paper_formal_report import (
    generate_paper_formal_report,
)


def test_capturing_formal_report_is_audit_only_and_secret_scan_runs_first(
    tmp_path: Path,
) -> None:
    _write_minimal_formal_evidence(tmp_path)
    metrics = recompute_paper_formal_metrics(tmp_path)

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        secret_values=(),
    )

    assert report.paper_eligible is False
    assert report.regression_only is True
    assert report.formal_paper_table_generated is False
    assert (tmp_path / "audit" / "secret_scan_report.json").is_file()
    assert (tmp_path / "audit" / "paper_eligibility_report.json").is_file()
    assert (tmp_path / "formal_regression_report.md").is_file()
    assert not (tmp_path / "formal_paper_report.md").exists()
    assert (tmp_path / "metrics" / "paper_table_model_comparison.csv").is_file()
    assert (
        tmp_path / "metrics" / "paper_table_model_endpoint_comparison.csv"
    ).is_file()
    markdown = (tmp_path / "formal_regression_report.md").read_text(encoding="utf-8")
    assert "capturing" in markdown.lower()
    assert "paper-eligible: false" in markdown.lower()
    assert "provider-confounded" in markdown.lower()
    assert "exp4 rows pair each ablation with full by case_id × repeat_id" in (
        markdown.lower()
    )


def test_formal_report_records_secret_scan_failure_without_paper_output(
    tmp_path: Path,
) -> None:
    _write_minimal_formal_evidence(tmp_path)
    secret = "formal-report-test-secret"
    (tmp_path / "experiments" / "captured.txt").write_text(secret, encoding="utf-8")
    metrics = recompute_paper_formal_metrics(tmp_path)

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        secret_values=(secret,),
    )

    scan = json.loads(
        (tmp_path / "audit" / "secret_scan_report.json").read_text(encoding="utf-8")
    )
    assert scan["passed"] is False
    assert scan["finding_count"] == 1
    assert report.paper_eligible is False
    assert report.formal_paper_table_generated is False


def test_formal_report_rejects_false_condition_even_when_suite_claims_eligible(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(tmp_path, task_eligible=True, attempt_eligible=True)
    metrics = _claimed_metrics(condition_eligible=False)

    report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert report.paper_eligible is False
    assert report.formal_paper_table_generated is False
    assert not (tmp_path / "formal_paper_report.md").exists()


def test_formal_report_rejects_false_experiment_row_below_true_condition(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(tmp_path, task_eligible=True, attempt_eligible=True)
    metrics = _claimed_metrics(condition_eligible=True, experiment_eligible=False)

    report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert report.paper_eligible is False
    assert report.formal_paper_table_generated is False
    assert not (tmp_path / "formal_paper_report.md").exists()


def test_formal_report_rejects_false_task_or_attempt_below_true_condition(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(tmp_path, task_eligible=False, attempt_eligible=True)
    metrics = _claimed_metrics(condition_eligible=True)

    task_report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert task_report.paper_eligible is False
    assert task_report.formal_paper_table_generated is False

    other = tmp_path / "attempt"
    _write_claimed_formal_evidence(other, task_eligible=True, attempt_eligible=False)
    attempt_report = generate_paper_formal_report(
        output_root=other,
        metrics=metrics,
    )
    assert attempt_report.paper_eligible is False
    assert attempt_report.formal_paper_table_generated is False


def test_formal_report_rejects_capturing_even_when_all_claims_are_true(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        capturing=True,
    )

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=_claimed_metrics(condition_eligible=True),
    )

    assert report.paper_eligible is False
    assert report.formal_paper_table_generated is False


def test_formal_report_fails_closed_when_task_attempt_or_refs_are_missing(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        include_refs=False,
    )

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=_claimed_metrics(condition_eligible=True),
    )

    assert report.paper_eligible is False
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "persisted_evidence_incomplete" in eligibility["ineligibility_reasons"]


def test_formal_report_rejects_dangling_event_or_artifact_refs(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
    )
    generation = (
        tmp_path
        / "experiments"
        / "exp1_real_ai_feasibility"
        / "runs"
        / "condition-1"
        / "0"
        / ".generations"
        / "generation-1"
    )
    attempt_path = generation / "per_attempt_results.jsonl"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt["raw_output_ref"] = {
        "artifact_id": "artifact-not-in-index",
        "content_hash": "sha256:" + "9" * 64,
    }
    attempt_path.write_text(json.dumps(attempt) + "\n", encoding="utf-8")

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=_claimed_metrics(condition_eligible=True),
    )

    assert report.paper_eligible is False
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "persisted_evidence_ref_unresolved" in eligibility[
        "ineligibility_reasons"
    ]


def _write_minimal_formal_evidence(root: Path) -> None:
    (root / "suite_manifest.json").write_text(
        json.dumps(
            {
                "formal": True,
                "pilot_only": False,
                "execution_scope": "formal_matrix",
                "regression_only": True,
                "paper_eligible": False,
                "experiment_ids": ["exp1_real_ai_feasibility"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "conditions.jsonl").write_text(
        json.dumps(
            {
                "experiment_id": "exp1_real_ai_feasibility",
                "condition_id": "condition-1",
                "repeat_id": 0,
                "domain": "factorization",
                "difficulty": "easy",
                "paper_difficulty": "easy",
                "worker_count": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    run_root = (
        root
        / "experiments"
        / "exp1_real_ai_feasibility"
        / "runs"
        / "condition-1"
        / "0"
    )
    generation = run_root / ".generations" / "generation-1"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-1"}) + "\n",
        encoding="utf-8",
    )
    records = {
        "per_task_results.jsonl": [
            {
                "experiment_id": "exp1_real_ai_feasibility",
                "condition_id": "condition-1",
                "repeat_id": 0,
                "task_id": "case-1",
                "root_status": "completed",
                "accepted_validity": True,
                "paper_eligible": False,
            }
        ],
        "per_attempt_results.jsonl": [
            {
                "experiment_id": "exp1_real_ai_feasibility",
                "condition_id": "condition-1",
                "repeat_id": 0,
                "task_id": "case-1",
                "unit_id": "unit-1",
                "attempt_id": "attempt-1",
                "attempt_status": "succeeded",
                "provider": "siliconflow",
                "model": "zai-org/GLM-5.2",
                "entry_id": "glm_5_2_exp1_baseline",
                "total_tokens": 10,
                "cost_estimate": 0.1,
                "latency_ms": 20,
            }
        ],
        "fault_injections.jsonl": [],
        "events/event_log.jsonl": [
            {
                "experiment_id": "exp1_real_ai_feasibility",
                "condition_id": "condition-1",
                "repeat_id": 0,
                "task_id": "case-1",
                "event_type": "AI_UNIT_ENDED",
                "offset_ms": 20,
                "duration_ms": 20,
            }
        ],
    }
    for relative_path, rows in records.items():
        path = generation / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )


def _claimed_metrics(
    *,
    condition_eligible: bool,
    experiment_eligible: bool | None = None,
) -> FormalMetricsResult:
    condition_row = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "condition-1",
        "repeat_id": 0,
        "paper_eligible": condition_eligible,
        "paper_ineligibility_reasons": (
            [] if condition_eligible else ["condition_not_paper_eligible"]
        ),
        "failure_breakdown": {},
    }
    return FormalMetricsResult(
        condition_rows=(condition_row,),
        experiment_rows={
            "exp1_real_ai_feasibility": (
                {
                    **dict(condition_row),
                    "paper_eligible": (
                        condition_eligible
                        if experiment_eligible is None
                        else experiment_eligible
                    ),
                },
            ),
            "exp2_real_ai_scalability": (),
            "exp3_real_ai_fault_recovery": (),
            "exp4_real_ai_protocol_ablation": (),
            "exp5_real_ai_model_endpoint_comparison": (),
        },
        metrics_digest="sha256:" + "1" * 64,
        paper_eligible=True,
        capturing=False,
        output_refs=(),
    )


def _write_claimed_formal_evidence(
    root: Path,
    *,
    task_eligible: bool,
    attempt_eligible: bool,
    capturing: bool = False,
    include_refs: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "suite_manifest.json").write_text(
        json.dumps(
            {
                "formal": True,
                "pilot_only": False,
                "execution_scope": "formal_matrix",
                "capturing": capturing,
                "regression_only": capturing,
                "paper_eligible": True,
                "experiment_ids": ["exp1_real_ai_feasibility"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    condition = {
        "experiment_id": "exp1_real_ai_feasibility",
        "condition_id": "condition-1",
        "repeat_id": 0,
        "domain": "factorization",
    }
    (root / "conditions.jsonl").write_text(
        json.dumps(condition) + "\n",
        encoding="utf-8",
    )
    (root / "condition_results.jsonl").write_text(
        json.dumps({**condition, "paper_eligible": True}) + "\n",
        encoding="utf-8",
    )
    experiment_root = root / "experiments" / "exp1_real_ai_feasibility"
    experiment_root.mkdir(parents=True, exist_ok=True)
    (experiment_root / "experiment_manifest.json").write_text(
        json.dumps({**condition, "paper_eligible": True}) + "\n",
        encoding="utf-8",
    )
    run_root = experiment_root / "runs" / "condition-1" / "0"
    generation = run_root / ".generations" / "generation-1"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "CURRENT.json").write_text(
        json.dumps({"generation_id": "generation-1"}) + "\n",
        encoding="utf-8",
    )
    evidence_ref = {
        "artifact_id": "artifact-1",
        "path": "artifacts/case-1/evidence.json",
        "content_hash": "sha256:" + "2" * 64,
    }
    task = {
        **condition,
        "task_id": "case-1",
        "record_scope": "protocol",
        "root_status": "completed",
        "paper_eligible": task_eligible,
        "event_refs": ([{"event_id": "event-1"}] if include_refs else []),
        "artifact_refs": ([evidence_ref] if include_refs else []),
        "evidence_artifact_refs": ([evidence_ref] if include_refs else []),
    }
    attempt = {
        **condition,
        "task_id": "case-1",
        "run_id": "run-1",
        "unit_id": "unit-1",
        "attempt_id": "attempt-1",
        "attempt_status": "succeeded",
        "record_scope": "protocol",
        "paper_eligible": attempt_eligible,
        "request_ref": evidence_ref if include_refs else None,
        "raw_output_ref": evidence_ref if include_refs else None,
        "provenance_ref": evidence_ref if include_refs else None,
        "usage_ref": evidence_ref if include_refs else None,
        "model_execution_record_ref": evidence_ref if include_refs else None,
        "started_at": "2026-07-26T00:00:00Z",
        "ended_at": "2026-07-26T00:00:00.100000Z",
    }
    records = {
        "per_task_results.jsonl": [task],
        "per_attempt_results.jsonl": [attempt],
        "events/event_log.jsonl": [
            {
                **condition,
                "task_id": "case-1",
                "event_id": "event-1",
                "event_type": "TASK_UNIT_STATE_CHANGED",
                "record_scope": "protocol",
            }
        ],
        "artifacts/artifact_index.jsonl": ([{**condition, "task_id": "case-1", **evidence_ref}] if include_refs else []),
    }
    for relative_path, rows in records.items():
        path = generation / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
