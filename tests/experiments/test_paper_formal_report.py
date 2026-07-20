from __future__ import annotations

import json
from pathlib import Path

from tokenshare.experiments.paper_formal_metrics import (
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
    markdown = (tmp_path / "formal_regression_report.md").read_text(encoding="utf-8")
    assert "capturing" in markdown.lower()
    assert "paper-eligible: false" in markdown.lower()


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
