from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

import tokenshare.experiments.paper_formal_report as formal_report
from tokenshare.experiments.paper_formal_metrics import (
    FormalMetricsResult,
    recompute_paper_formal_metrics,
)
from tokenshare.experiments.paper_exp5_artifacts import (
    EXP5_AUDIT_FILES,
    EXP5_PAPER_FILES,
    Exp5PaperArtifactResult,
)
from tokenshare.experiments.paper_formal_report import (
    generate_paper_formal_report,
)


def test_secret_scan_streams_paths_and_detects_cross_chunk_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_size = 1024 * 1024
    configured_secret = "configured-secret-value-123456"
    exact_path = tmp_path / "a-exact.bin"
    exact_path.write_bytes(
        b"x" * (chunk_size - 5) + configured_secret.encode("utf-8")
    )
    pattern_path = tmp_path / "nested" / "b-pattern.bin"
    pattern_path.parent.mkdir(parents=True, exist_ok=True)
    pattern_path.write_bytes(
        b"y" * (chunk_size - 2) + b"sk-" + b"A" * 20
    )
    (tmp_path / "z-clean.txt").write_text("clean", encoding="utf-8")

    def forbidden_rglob(self: Path, pattern: str) -> object:
        raise AssertionError("secret scan must not materialize the full path tree")

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("secret scan must read files in chunks")

    monkeypatch.setattr(Path, "rglob", forbidden_rglob)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    scan = formal_report._scan_formal_output(
        tmp_path,
        secret_values=(configured_secret,),
    )

    assert scan["passed"] is False
    assert scan["files_scanned"] == 3
    assert scan["findings"] == [
        {
            "path": "a-exact.bin",
            "finding_kind": "configured_secret_value",
        },
        {
            "path": "nested/b-pattern.bin",
            "finding_kind": "api_key_pattern",
        },
    ]


def test_secret_scan_preserves_windows_case_folded_path_order(
    tmp_path: Path,
) -> None:
    secret = "configured-order-secret"
    nested = tmp_path / "a"
    nested.mkdir()
    for relative in ("a/x.txt", "A-upper.txt", "a.txt", "b.txt"):
        (tmp_path / relative).write_text(secret, encoding="utf-8")

    scan = formal_report._scan_formal_output(
        tmp_path,
        secret_values=(secret,),
    )

    assert [finding["path"] for finding in scan["findings"]] == [
        "a/x.txt",
        "A-upper.txt",
        "a.txt",
        "b.txt",
    ]


def test_report_jsonl_reader_streams_without_path_read_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('{"row": 1}\n{"row": 2}\n', encoding="utf-8")
    original_read_text = Path.read_text

    def forbidden_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise AssertionError("report JSONL reader must stream lines")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    assert formal_report._read_jsonl(path) == [{"row": 1}, {"row": 2}]


def test_report_ref_hashes_file_in_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "report.bin"
    content = b"report" * 400_000
    path.write_bytes(content)
    expected = "sha256:" + sha256(content).hexdigest()

    def forbidden_read_bytes(self: Path) -> bytes:
        raise AssertionError("report refs must hash files in chunks")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)

    assert formal_report._ref(tmp_path, path) == {
        "path": "report.bin",
        "content_hash": expected,
    }


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


def test_formal_report_does_not_render_unbound_rows_carried_by_metrics(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        capturing=True,
    )
    metrics = _claimed_metrics(
        condition_eligible=True,
        capturing=True,
        exp5_artifact_rows={
            "suite_status": "completed",
            "identity_complete": False,
            "overall_rows": (),
            "domain_topic_rows": (),
            "paired_comparison_rows": (),
            "model_execution_rows": (),
            "order_concurrency_rows": (),
            "failure_taxonomy_rows": (),
        },
    )

    report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert report.paper_eligible is False
    assert report.exp5_artifact_refs == ()
    assert all(
        not (tmp_path / path).exists()
        for path in EXP5_AUDIT_FILES + EXP5_PAPER_FILES
    )


def test_formal_report_rejects_exp5_rows_when_suite_has_no_exp5_evidence(
    tmp_path: Path,
) -> None:
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
    )
    metrics = _claimed_metrics(
        condition_eligible=True,
        exp5_artifact_rows=_minimal_exp5_artifact_rows(
            suite_status="completed_with_failures"
        ),
    )

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        exp5_pdf_backend=lambda path, _spec: path.write_bytes(b"%PDF-1.4\n%%EOF\n"),
    )

    assert report.paper_eligible is False
    assert report.exp5_artifact_refs == ()
    assert all(
        not (tmp_path / path).exists()
        for path in EXP5_AUDIT_FILES + EXP5_PAPER_FILES
    )
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "exp5_renderer_rows_without_exp5_suite" in eligibility[
        "ineligibility_reasons"
    ]


def test_formal_report_rejects_exp5_rows_whose_digest_is_not_bound(
    tmp_path: Path,
) -> None:
    experiment_id = "exp5_real_ai_model_endpoint_comparison"
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        experiment_id=experiment_id,
    )
    rows = _minimal_exp5_artifact_rows(suite_status="completed_with_failures")
    metrics = _claimed_metrics(
        condition_eligible=True,
        experiment_id=experiment_id,
        exp5_artifact_rows=rows,
        exp5_artifact_rows_digest="sha256:" + "9" * 64,
    )
    _write_persisted_metrics_binding(tmp_path, metrics)

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        exp5_pdf_backend=lambda path, _spec: path.write_bytes(b"%PDF-1.4\n%%EOF\n"),
    )

    assert report.paper_eligible is False
    assert report.exp5_artifact_refs == ()
    assert all(
        not (tmp_path / path).exists()
        for path in EXP5_AUDIT_FILES + EXP5_PAPER_FILES
    )
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "exp5_renderer_rows_digest_mismatch" in eligibility[
        "ineligibility_reasons"
    ]


def test_formal_report_rejects_exp5_rows_when_metrics_digest_is_not_persisted(
    tmp_path: Path,
) -> None:
    experiment_id = "exp5_real_ai_model_endpoint_comparison"
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        experiment_id=experiment_id,
    )
    rows = _minimal_exp5_artifact_rows(suite_status="completed_with_failures")
    metrics = _claimed_metrics(
        condition_eligible=True,
        experiment_id=experiment_id,
        exp5_artifact_rows=rows,
        exp5_artifact_rows_digest=_digest_json(rows),
    )
    _write_persisted_metrics_binding(
        tmp_path,
        metrics,
        metrics_digest="sha256:" + "8" * 64,
    )

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        exp5_pdf_backend=lambda path, _spec: path.write_bytes(b"%PDF-1.4\n%%EOF\n"),
    )

    assert report.paper_eligible is False
    assert report.exp5_artifact_refs == ()
    assert all(
        not (tmp_path / path).exists()
        for path in EXP5_AUDIT_FILES + EXP5_PAPER_FILES
    )
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "exp5_metrics_digest_not_persisted" in eligibility[
        "ineligibility_reasons"
    ]


def test_formal_report_rejects_exp5_rows_missing_persisted_renderer_bundle(
    tmp_path: Path,
) -> None:
    experiment_id = "exp5_real_ai_model_endpoint_comparison"
    _write_claimed_formal_evidence(
        tmp_path,
        task_eligible=True,
        attempt_eligible=True,
        experiment_id=experiment_id,
    )
    rows = _minimal_exp5_artifact_rows(suite_status="completed_with_failures")
    metrics = _claimed_metrics(
        condition_eligible=True,
        experiment_id=experiment_id,
        exp5_artifact_rows=rows,
        exp5_artifact_rows_digest=_digest_json(rows),
    )
    _write_persisted_metrics_binding(tmp_path, metrics, persist_rows=False)

    report = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        exp5_pdf_backend=lambda path, _spec: path.write_bytes(b"%PDF-1.4\n%%EOF\n"),
    )

    assert report.paper_eligible is False
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "exp5_renderer_rows_not_persisted" in eligibility[
        "ineligibility_reasons"
    ]


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


def test_formal_report_fails_closed_when_exp5_paper_renderer_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_renderer(**kwargs):
        calls.append(kwargs)
        return Exp5PaperArtifactResult(
            status="paper_artifact_render_failed",
            paper_eligible=False,
            artifact_refs=(),
            failure_reason="deterministic backend failure",
        )

    monkeypatch.setattr(
        "tokenshare.experiments.paper_formal_report.render_exp5_paper_artifacts",
        fake_renderer,
    )
    parsed_rows = {
        "suite_status": "completed",
        "identity_complete": True,
        "overall_rows": (),
        "domain_topic_rows": (),
        "paired_comparison_rows": (),
        "model_execution_rows": (),
        "order_concurrency_rows": (),
        "failure_taxonomy_rows": (),
    }

    metrics = _write_bound_exp5_metrics(tmp_path, parsed_rows)
    report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert calls and calls[0]["paper_eligible"] is True
    assert report.paper_eligible is False
    assert report.formal_paper_table_generated is False
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "paper_artifact_render_failed" in eligibility[
        "ineligibility_reasons"
    ]
    assert eligibility["exp5_artifact_result"]["status"] == (
        "paper_artifact_render_failed"
    )
    assert not (tmp_path / "formal_paper_report.md").exists()


def test_formal_report_catches_exp5_renderer_validation_error_and_retains_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def invalid_renderer(**_kwargs):
        raise ValueError("overall_rows contains an unsafe nested field")

    monkeypatch.setattr(
        "tokenshare.experiments.paper_formal_report.render_exp5_paper_artifacts",
        invalid_renderer,
    )
    parsed_rows = {
        "suite_status": "completed",
        "identity_complete": True,
        "overall_rows": (),
        "domain_topic_rows": (),
        "paired_comparison_rows": (),
        "model_execution_rows": (),
        "order_concurrency_rows": (),
        "failure_taxonomy_rows": (),
    }

    metrics = _write_bound_exp5_metrics(tmp_path, parsed_rows)
    audit_path = tmp_path / "metrics" / "exp5_model_overall.csv"
    audit_path.write_text("cohort_member_id\n", encoding="utf-8")
    report = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert report.paper_eligible is False
    assert audit_path.read_text(encoding="utf-8") == "cohort_member_id\n"
    eligibility = json.loads(
        (tmp_path / "audit" / "paper_eligibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "paper_artifact_render_failed" in eligibility["ineligibility_reasons"]
    assert eligibility["exp5_artifact_result"]["failure_reason"] == (
        "overall_rows contains an unsafe nested field"
    )
    assert not (tmp_path / "formal_paper_report.md").exists()


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
    capturing: bool = False,
    exp5_artifact_rows: dict | None = None,
    exp5_artifact_rows_digest: str | None = None,
    experiment_id: str = "exp1_real_ai_feasibility",
) -> FormalMetricsResult:
    condition_row = {
        "experiment_id": experiment_id,
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
            candidate_id: (
                (
                    {
                        **dict(condition_row),
                        "paper_eligible": (
                            condition_eligible
                            if experiment_eligible is None
                            else experiment_eligible
                        ),
                    },
                )
                if candidate_id == experiment_id
                else ()
            )
            for candidate_id in (
                "exp1_real_ai_feasibility",
                "exp2_real_ai_scalability",
                "exp3_real_ai_fault_recovery",
                "exp4_real_ai_protocol_ablation",
                "exp5_real_ai_model_endpoint_comparison",
            )
        },
        metrics_digest="sha256:" + "1" * 64,
        paper_eligible=True,
        capturing=capturing,
        output_refs=(),
        exp5_artifact_rows=exp5_artifact_rows,
        exp5_artifact_rows_digest=exp5_artifact_rows_digest,
    )


def _minimal_exp5_artifact_rows(*, suite_status: str) -> dict:
    member_ids = (
        "glm_5_2_siliconflow",
        "qwen3_14b_siliconflow",
        "minimax_m2_5_siliconflow",
        "deepseek_v3_pro_siliconflow",
    )
    overall_rows = tuple(
        {
            "cohort_member_id": member_id,
            "model_label": member_id,
            "root_count": 1,
            "completion_count": 0,
            "completion_rate": 0.0,
            "completion_ci_low": 0.0,
            "completion_ci_high": 0.0,
            "accepted_validity_count": 0,
            "accepted_validity_rate": 0.0,
            "accepted_validity_ci_low": 0.0,
            "accepted_validity_ci_high": 0.0,
            "provider_attempt_count": 1,
            "prompt_tokens": 1,
            "reasoning_tokens": None,
            "visible_output_tokens": None,
            "total_tokens": 1,
            "total_tokens_median": 1.0,
            "total_tokens_ci_low": 1.0,
            "total_tokens_ci_high": 1.0,
            "cost_estimate": None,
            "cost_currency": None,
            "cost_estimate_status": "unavailable",
            "pricing_snapshot_digest": None,
            "wall_clock_ms": 1.0,
            "provider_latency_ms": 1.0,
            "provider_latency_ms_median": 1.0,
            "provider_latency_ms_ci_low": 1.0,
            "provider_latency_ms_ci_high": 1.0,
            "rate_limit_429_count": 0,
            "timeout_count": 0,
            "retry_count": 0,
            "identity_coverage": 1.0,
            "reasoning_tokens_missing_count": 1,
            "visible_output_tokens_missing_count": 1,
            "cost_estimate_missing_count": 1,
            "paper_eligible": True,
            "identity_complete": True,
        }
        for member_id in member_ids
    )
    return {
        "suite_status": suite_status,
        "identity_complete": True,
        "overall_rows": overall_rows,
        "domain_topic_rows": (),
        "paired_comparison_rows": (),
        "model_execution_rows": (),
        "order_concurrency_rows": (),
        "failure_taxonomy_rows": (),
    }


def _write_persisted_metrics_binding(
    root: Path,
    metrics: FormalMetricsResult,
    *,
    metrics_digest: str | None = None,
    persist_rows: bool = True,
) -> None:
    path = root / "metrics" / "formal_metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "tokenshare.paper_formal_metrics_body.v1",
                "metrics_digest": metrics_digest or metrics.metrics_digest,
                "exp5_artifact_rows_digest": metrics.exp5_artifact_rows_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if persist_rows:
        (root / "metrics" / "exp5_renderer_rows.json").write_text(
            json.dumps(
                metrics.exp5_artifact_rows,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _write_bound_exp5_metrics(
    root: Path,
    rows: dict,
) -> FormalMetricsResult:
    experiment_id = "exp5_real_ai_model_endpoint_comparison"
    _write_claimed_formal_evidence(
        root,
        task_eligible=True,
        attempt_eligible=True,
        experiment_id=experiment_id,
    )
    metrics = _claimed_metrics(
        condition_eligible=True,
        experiment_id=experiment_id,
        exp5_artifact_rows=rows,
        exp5_artifact_rows_digest=_digest_json(rows),
    )
    _write_persisted_metrics_binding(root, metrics)
    return metrics


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _write_claimed_formal_evidence(
    root: Path,
    *,
    task_eligible: bool,
    attempt_eligible: bool,
    capturing: bool = False,
    include_refs: bool = True,
    experiment_id: str = "exp1_real_ai_feasibility",
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
                "experiment_ids": [experiment_id],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    condition = {
        "experiment_id": experiment_id,
        "condition_id": "condition-1",
        "repeat_id": 0,
        "domain": "factorization",
    }
    if experiment_id == "exp5_real_ai_model_endpoint_comparison":
        condition["schema_version"] = "tokenshare.paper_condition.v3"
    (root / "conditions.jsonl").write_text(
        json.dumps(condition) + "\n",
        encoding="utf-8",
    )
    (root / "condition_results.jsonl").write_text(
        json.dumps({**condition, "paper_eligible": True}) + "\n",
        encoding="utf-8",
    )
    experiment_root = root / "experiments" / experiment_id
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
