from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from tokenshare.experiments.paper_exp5_artifacts import (
    EXP5_AUDIT_FILES,
    EXP5_PAPER_FILES,
    PdfBackendUnavailable,
    _plot_tex_document,
    render_exp5_paper_artifacts,
)


MODEL_IDS = (
    "glm_5_2_siliconflow",
    "qwen3_14b_siliconflow",
    "minimax_m2_5_siliconflow",
    "deepseek_v3_pro_siliconflow",
)
MODEL_LABELS = (
    "GLM_5.2 % & # {A}",
    "Qwen3-14B",
    "MiniMax-M2.5",
    "DeepSeek-V3 Pro",
)


def test_exp5_renderer_writes_exact_audit_and_paper_contract_deterministically(
    tmp_path: Path,
) -> None:
    backend = _DeterministicPdfBackend()
    fixture = _eligible_fixture()

    first = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=backend,
        **fixture,
    )

    assert first.status == "rendered"
    assert first.paper_eligible is True
    assert {path.as_posix() for path in _relative_files(tmp_path / "metrics", tmp_path)} == set(
        EXP5_AUDIT_FILES
    )
    assert {path.as_posix() for path in _relative_files(tmp_path / "paper", tmp_path)} == set(
        EXP5_PAPER_FILES
    )

    tex = (tmp_path / "paper" / "exp5_model_overall.tex").read_text(
        encoding="utf-8"
    )
    assert "\\toprule" in tex and "\\midrule" in tex and "\\bottomrule" in tex
    for escaped in (
        r"GLM\_5.2",
        r"\%",
        r"\&",
        r"\#",
        r"\{A\}",
    ):
        assert escaped in tex

    completion_svg = (
        tmp_path / "paper" / "exp5_completion_validity.svg"
    ).read_text(encoding="utf-8")
    tokens_svg = (tmp_path / "paper" / "exp5_tokens_latency.svg").read_text(
        encoding="utf-8"
    )
    for svg in (completion_svg, tokens_svg):
        assert 'viewBox="0 0 900 520"' in svg
        assert "95% CI" in svg
        assert all(label.split(" ")[0] in svg for label in MODEL_LABELS)
        assert svg.count("n=321") == 4
        assert 'data-tick-axis=' in svg
    assert "Completion / accepted validity rate" in completion_svg
    assert "Rate" in completion_svg
    assert '>0.000</text>' in completion_svg
    assert '>1.000</text>' in completion_svg
    assert "Median total tokens" in tokens_svg
    assert "Provider latency (ms)" in tokens_svg
    assert "<line" in completion_svg

    summary = (tmp_path / "paper" / "exp5_results_summary.md").read_text(
        encoding="utf-8"
    )
    overall_csv = (tmp_path / "metrics" / "exp5_model_overall.csv").read_text(
        encoding="utf-8"
    )
    assert "## English" in summary and "## 中文" in summary
    assert "0.910" in summary and "0.91" in overall_csv
    assert "Holm-adjusted p=0.030" in summary
    assert "same-provider serving-profile limitation" in summary
    assert "同一 provider serving-profile 局限" in summary
    assert "order sensitivity" in summary.lower()
    assert "missingness" in summary.lower()
    assert "winner" not in summary.lower() and "best model" not in summary.lower()

    appendix = (tmp_path / "paper" / "exp5_failure_appendix.md").read_text(
        encoding="utf-8"
    )
    for failure_kind in (
        "parse_failure",
        "checker_rejection",
        "timeout",
        "rate_limited_429",
        "identity_mismatch",
        "length_truncation",
    ):
        assert failure_kind in appendix
    assert "raw chain of thought" not in appendix

    stable_paths = tuple(EXP5_AUDIT_FILES) + tuple(
        path for path in EXP5_PAPER_FILES if not path.endswith(".pdf")
    )
    first_hashes = {
        path: sha256((tmp_path / path).read_bytes()).hexdigest()
        for path in stable_paths
    }
    second = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=backend,
        **fixture,
    )
    second_hashes = {
        path: sha256((tmp_path / path).read_bytes()).hexdigest()
        for path in stable_paths
    }
    assert second.status == "rendered"
    assert second_hashes == first_hashes

    assert [spec.artifact_id for _, spec in backend.calls[-2:]] == [
        "exp5_completion_validity",
        "exp5_tokens_latency",
    ]
    assert all(
        point.sample_size == 321
        for _, spec in backend.calls[-2:]
        for series in spec.series
        for point in series.points
    )
    for _, spec in backend.calls[-2:]:
        plot_tex = _plot_tex_document(spec)
        tick_lines = [
            line
            for line in plot_tex.splitlines()
            if "\\makebox(0,0)[r]{\\scriptsize" in line
            or "\\makebox(0,0)[l]{\\scriptsize" in line
        ]
        assert tick_lines
        assert all(line.endswith("}}") and not line.endswith("}}}") for line in tick_lines)
        assert plot_tex.count("n=321") == 4
    for pdf_name in (
        "exp5_completion_validity.pdf",
        "exp5_tokens_latency.pdf",
    ):
        pdf_path = tmp_path / "paper" / pdf_name
        assert pdf_path.stat().st_size > 100
        info = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "Pages:" in info.stdout and "1" in info.stdout


def test_exp5_renderer_keeps_paper_outputs_for_evidence_complete_failures(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["suite_status"] = "completed_with_failures"

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    assert result.paper_eligible is True
    assert all((tmp_path / path).is_file() for path in EXP5_AUDIT_FILES)
    assert all((tmp_path / path).is_file() for path in EXP5_PAPER_FILES)


@pytest.mark.parametrize(
    "row_group",
    (
        "domain_topic_rows",
        "paired_comparison_rows",
        "model_execution_rows",
        "order_concurrency_rows",
    ),
)
def test_exp5_renderer_rejects_empty_required_paper_inventory(
    tmp_path: Path,
    row_group: str,
) -> None:
    fixture = _eligible_fixture()
    fixture[row_group] = ()

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_render_failed"
    assert result.paper_eligible is False
    assert all((tmp_path / path).is_file() for path in EXP5_AUDIT_FILES)
    assert all(not (tmp_path / path).exists() for path in EXP5_PAPER_FILES)


def test_exp5_renderer_marks_missing_secondary_metrics_without_zero_filling(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["suite_status"] = "completed_with_failures"
    fixture["overall_rows"] = tuple(
        {
            **row,
            "total_tokens": None,
            "total_tokens_sample_size": 0,
            "total_tokens_median": None,
            "total_tokens_ci_low": None,
            "total_tokens_ci_high": None,
            "provider_latency_ms": None,
            "provider_latency_ms_sample_size": 0,
            "provider_latency_ms_median": None,
            "provider_latency_ms_ci_low": None,
            "provider_latency_ms_ci_high": None,
        }
        for row in fixture["overall_rows"]
    )

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    svg = (tmp_path / "paper" / "exp5_tokens_latency.svg").read_text(
        encoding="utf-8"
    )
    summary = (tmp_path / "paper" / "exp5_results_summary.md").read_text(
        encoding="utf-8"
    )
    assert "unavailable" in svg
    assert "null" in summary
    assert "<circle" not in svg


def test_exp5_renderer_does_not_persist_unapproved_parsed_fields(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["overall_rows"][0]["unreviewed_payload"] = "DO_NOT_RENDER"

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    for relative_path in EXP5_AUDIT_FILES + EXP5_PAPER_FILES:
        if relative_path.endswith(".pdf"):
            continue
        assert "DO_NOT_RENDER" not in (tmp_path / relative_path).read_text(
            encoding="utf-8"
        )


@pytest.mark.parametrize(
    ("suite_status", "paper_eligible", "identity_complete"),
    (
        ("blocked", True, True),
        ("budget_exhausted", True, True),
        ("incomplete", True, True),
        ("completed", False, True),
        ("completed", True, False),
    ),
)
def test_exp5_renderer_writes_empty_audit_contract_and_clears_stale_paper(
    tmp_path: Path,
    suite_status: str,
    paper_eligible: bool,
    identity_complete: bool,
) -> None:
    fixture = _eligible_fixture()
    fixture.update(
        suite_status=suite_status,
        paper_eligible=paper_eligible,
        identity_complete=identity_complete,
    )
    for field_name in (
        "overall_rows",
        "domain_topic_rows",
        "paired_comparison_rows",
        "model_execution_rows",
        "order_concurrency_rows",
        "failure_taxonomy_rows",
    ):
        fixture[field_name] = ()
    for relative_path in EXP5_PAPER_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("STALE", encoding="utf-8")

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_ineligible"
    assert {path.as_posix() for path in _relative_files(tmp_path / "metrics", tmp_path)} == set(
        EXP5_AUDIT_FILES
    )
    for relative_path in EXP5_AUDIT_FILES:
        content = (tmp_path / relative_path).read_text(encoding="utf-8")
        if relative_path.endswith(".jsonl"):
            assert content == ""
        else:
            assert content.count("\n") == 1
            assert content.strip()
    assert not (tmp_path / "paper").exists()


def test_exp5_renderer_preserves_real_execution_join_and_provenance_fields(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["model_execution_rows"] = (_real_model_execution_row(),)

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    persisted = json.loads(
        (tmp_path / "metrics" / "exp5_model_execution_records.jsonl").read_text(
            encoding="utf-8"
        )
    )
    for field_name in (
        "run_id",
        "task_id",
        "unit_id",
        "attempt_id",
        "attempt_status",
        "started_at",
        "ended_at",
        "identity_status",
        "selected_entry_id",
        "source_provider_config_digest",
        "prepared_execution_config_digest",
        "model_execution_record_digest",
        "request_ref",
        "provenance_ref",
        "raw_output_ref",
        "usage_ref",
        "model_execution_record_ref",
    ):
        assert persisted[field_name] == _real_model_execution_row()[field_name]
    assert persisted["latency_ms"] == 850
    assert persisted["cost_estimate_currency"] == "CNY"
    assert "provider_latency_ms" not in persisted
    assert "cost_currency" not in persisted


def test_exp5_renderer_accepts_auditable_provider_failure_without_response(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    provider_failure = {
        **_real_model_execution_row(),
        "resolved_model": None,
        "response_model_status": "unavailable",
        "raw_output_ref": None,
        "identity_status": "not_observed",
        "attempt_status": "provider_error",
        "provider_errors": ["timeout"],
        "error_kind": "timeout",
        "paper_eligible": True,
    }
    fixture["model_execution_rows"] = (provider_failure,)

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    persisted = json.loads(
        (tmp_path / "metrics" / "exp5_model_execution_records.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["identity_status"] == "not_observed"
    assert persisted["resolved_model"] is None
    assert persisted["provider_errors"] == ["timeout"]


def test_exp5_renderer_rejects_unbound_not_observed_execution_row(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["model_execution_rows"] = (
        {
            **_real_model_execution_row(),
            "resolved_model": None,
            "response_model_status": "unavailable",
            "raw_output_ref": None,
            "identity_status": "not_observed",
            "attempt_status": "provider_error",
            "provider_errors": [],
            "error_kind": "timeout",
            "paper_eligible": True,
        },
    )

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_ineligible"
    assert result.paper_eligible is False
    assert not (tmp_path / "paper").exists()


def test_exp5_renderer_requires_explicit_execution_row_eligibility(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    row = _real_model_execution_row()
    row.pop("paper_eligible")
    fixture["model_execution_rows"] = (row,)

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_ineligible"
    assert result.paper_eligible is False
    assert not (tmp_path / "paper").exists()


def test_exp5_renderer_consumes_exact_task7_paired_and_order_schemas(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["paired_comparison_rows"] = _task7_paired_rows(fixture)
    fixture["order_concurrency_rows"] = _task7_order_rows(fixture)
    fixture["model_execution_rows"] = (_real_model_execution_row(),)

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    summary = (tmp_path / "paper" / "exp5_results_summary.md").read_text(
        encoding="utf-8"
    )
    assert "Holm-adjusted p=0.030" in summary
    assert "direction=model_a_higher" in summary
    assert "No primary paired comparison provided sufficient evidence" not in summary
    assert "主要配对比较未观察到足够证据" not in summary
    assert "Order sensitivity" in summary
    paired_csv = (tmp_path / "metrics" / "exp5_paired_comparisons.csv").read_text(
        encoding="utf-8"
    )
    order_csv = (
        tmp_path / "metrics" / "exp5_order_and_concurrency.csv"
    ).read_text(encoding="utf-8")
    assert "tokenshare.paper_exp5_paired_comparison.v1" in paired_csv
    assert "root_completion" in paired_csv
    assert "tokenshare.paper_exp5_order_sensitivity.v1" in order_csv
    assert "root_completion_rate" in order_csv


@pytest.mark.parametrize(
    ("row_group", "field_name"),
    (
        ("paired_comparison_rows", "paper_eligible"),
        ("paired_comparison_rows", "identity_complete"),
        ("order_concurrency_rows", "paper_eligible"),
        ("order_concurrency_rows", "identity_complete"),
    ),
)
def test_exp5_renderer_rejects_explicit_false_on_exact_task7_schema(
    tmp_path: Path,
    row_group: str,
    field_name: str,
) -> None:
    fixture = _eligible_fixture()
    fixture["paired_comparison_rows"] = _task7_paired_rows(fixture)
    fixture["order_concurrency_rows"] = _task7_order_rows(fixture)
    rows = list(fixture[row_group])
    rows[0] = {**rows[0], field_name: False}
    fixture[row_group] = tuple(rows)

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_ineligible"
    assert result.paper_eligible is False
    assert not (tmp_path / "paper").exists()


@pytest.mark.parametrize(
    "unsafe_value",
    (
        {"artifact_id": "failure-1", "metadata": {"raw_output_text": "SECRET"}},
        {"artifact_id": "failure-1", "reasoning_content": "SECRET"},
        {"artifact_id": "failure-1", "token_payload": {"text": "SECRET"}},
        {"artifact_id": "failure-1", "metadata": {"note": "unbounded"}},
        {"artifact_id": "SECRET\nraw output"},
        {"artifact_id": "failure-1", "content_hash": "not-a-digest"},
    ),
)
def test_exp5_renderer_rejects_nested_payload_without_overwriting_audit(
    tmp_path: Path,
    unsafe_value: dict,
) -> None:
    fixture = _eligible_fixture()
    first = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )
    assert first.status == "rendered"
    audit_hashes = {
        path: sha256((tmp_path / path).read_bytes()).hexdigest()
        for path in EXP5_AUDIT_FILES
    }
    fixture["failure_taxonomy_rows"] = (
        {
            **fixture["failure_taxonomy_rows"][0],
            "evidence_ref": unsafe_value,
        },
    )

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "paper_artifact_render_failed"
    assert result.paper_eligible is False
    assert "SECRET" not in "".join(
        (tmp_path / path).read_text(encoding="utf-8")
        for path in EXP5_AUDIT_FILES
    )
    assert {
        path: sha256((tmp_path / path).read_bytes()).hexdigest()
        for path in EXP5_AUDIT_FILES
    } == audit_hashes
    assert not (tmp_path / "paper").exists()


@pytest.mark.parametrize(
    ("suite_status", "paper_eligible", "identity_complete"),
    (
        ("blocked", True, True),
        ("budget_exhausted", True, True),
        ("incomplete", True, True),
        ("completed", False, True),
        ("completed", True, False),
    ),
)
def test_exp5_renderer_never_creates_paper_for_ineligible_input(
    tmp_path: Path,
    suite_status: str,
    paper_eligible: bool,
    identity_complete: bool,
) -> None:
    backend = _DeterministicPdfBackend()
    fixture = _eligible_fixture()
    fixture.update(
        suite_status=suite_status,
        paper_eligible=paper_eligible,
        identity_complete=identity_complete,
    )

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=backend,
        **fixture,
    )

    assert result.status == "paper_artifact_ineligible"
    assert result.paper_eligible is False
    assert (tmp_path / "metrics" / "exp5_model_overall.csv").is_file()
    assert not (tmp_path / "paper").exists()
    assert backend.calls == []


def test_exp5_renderer_preserves_audit_and_fails_closed_without_pdf_backend(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()

    def unavailable_backend(_path, _plot_spec) -> None:
        raise PdfBackendUnavailable("pdflatex unavailable")

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=unavailable_backend,
        **fixture,
    )

    assert result.status == "paper_artifact_render_failed"
    assert result.paper_eligible is False
    assert result.failure_reason == "pdflatex unavailable"
    assert (tmp_path / "metrics" / "exp5_model_overall.csv").is_file()
    assert (tmp_path / "metrics" / "exp5_paired_comparisons.csv").is_file()
    assert not (tmp_path / "paper").exists()


def test_exp5_summary_states_insufficient_evidence_when_no_pair_is_significant(
    tmp_path: Path,
) -> None:
    fixture = _eligible_fixture()
    fixture["paired_comparison_rows"] = tuple(
        {**row, "holm_adjusted_p_value": 0.5}
        for row in fixture["paired_comparison_rows"]
    )

    result = render_exp5_paper_artifacts(
        output_root=tmp_path,
        pdf_backend=_DeterministicPdfBackend(),
        **fixture,
    )

    assert result.status == "rendered"
    summary = (tmp_path / "paper" / "exp5_results_summary.md").read_text(
        encoding="utf-8"
    )
    assert "No primary paired comparison provided sufficient evidence" in summary
    assert "主要配对比较未观察到足够证据" in summary


class _DeterministicPdfBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, object]] = []

    def __call__(self, path: Path, plot_spec) -> None:
        self.calls.append((path, plot_spec))
        _write_minimal_pdf(path, plot_spec.title)


def _eligible_fixture() -> dict:
    overall_rows = []
    for index, (member_id, label) in enumerate(
        zip(MODEL_IDS, MODEL_LABELS, strict=True)
    ):
        completion_rate = 0.91 - index * 0.03
        validity_rate = 0.86 - index * 0.025
        overall_rows.append(
            {
                "cohort_member_id": member_id,
                "model_label": label,
                "root_count": 321,
                "completion_count": round(321 * completion_rate),
                "completion_rate": completion_rate,
                "completion_ci_low": completion_rate - 0.03,
                "completion_ci_high": completion_rate + 0.03,
                "accepted_validity_count": round(321 * validity_rate),
                "accepted_validity_rate": validity_rate,
                "accepted_validity_ci_low": validity_rate - 0.03,
                "accepted_validity_ci_high": validity_rate + 0.03,
                "provider_attempt_count": 2472,
                "prompt_tokens": 100_000 + index * 1000,
                "reasoning_tokens": 50_000 if index < 2 else None,
                "visible_output_tokens": 75_000 + index * 1000,
                "total_tokens": 225_000 + index * 10_000,
                "total_tokens_median": 700 + index * 50,
                "total_tokens_ci_low": 650 + index * 50,
                "total_tokens_ci_high": 750 + index * 50,
                "cost_estimate": 12.5 + index,
                "cost_currency": "CNY",
                "cost_estimate_status": "available",
                "pricing_snapshot_digest": "sha256:" + str(index + 1) * 64,
                "wall_clock_ms": 200_000 + index * 10_000,
                "provider_latency_ms": 180_000 + index * 10_000,
                "provider_latency_ms_median": 850 + index * 60,
                "provider_latency_ms_ci_low": 800 + index * 60,
                "provider_latency_ms_ci_high": 900 + index * 60,
                "rate_limit_429_count": index,
                "timeout_count": index,
                "retry_count": 0,
                "identity_coverage": 1.0,
                "reasoning_tokens_missing_count": 0 if index < 2 else 5,
                "visible_output_tokens_missing_count": 0,
                "cost_estimate_missing_count": 0,
                "paper_eligible": True,
                "identity_complete": True,
            }
        )

    domain_topic_rows = []
    for row in overall_rows:
        for domain, topic_family in (
            ("factorization", None),
            ("lean_proof", "pure_logic"),
            ("lean_proof", "function_set"),
            ("lean_proof", "induction"),
        ):
            domain_topic_rows.append(
                {
                    "cohort_member_id": row["cohort_member_id"],
                    "model_label": row["model_label"],
                    "domain": domain,
                    "topic_family": topic_family,
                    "root_count": 83 if domain == "factorization" else 8,
                    "completion_count": 75 if domain == "factorization" else 7,
                    "completion_rate": row["completion_rate"],
                    "accepted_validity_count": 70 if domain == "factorization" else 6,
                    "accepted_validity_rate": row["accepted_validity_rate"],
                    "paper_eligible": True,
                    "identity_complete": True,
                }
            )

    pairs = []
    pair_index = 0
    for left_index in range(len(MODEL_IDS)):
        for right_index in range(left_index + 1, len(MODEL_IDS)):
            pairs.append(
                {
                    "metric": "root_completion_rate",
                    "domain": "overall",
                    "topic_family": None,
                    "model_a": MODEL_IDS[left_index],
                    "model_b": MODEL_IDS[right_index],
                    "paired_sample_size": 321,
                    "model_a_estimate": overall_rows[left_index]["completion_rate"],
                    "model_b_estimate": overall_rows[right_index]["completion_rate"],
                    "paired_difference": overall_rows[left_index]["completion_rate"]
                    - overall_rows[right_index]["completion_rate"],
                    "ci_95_low": -0.01,
                    "ci_95_high": 0.08,
                    "method": "cluster_bootstrap_exact_sign_flip",
                    "raw_p_value": 0.01 if pair_index == 0 else 0.2,
                    "holm_adjusted_p_value": 0.03 if pair_index == 0 else 0.6,
                    "effect_direction": "model_a_higher",
                    "paper_eligible": True,
                    "identity_complete": True,
                }
            )
            pair_index += 1

    model_execution_rows = tuple(
        {
            "cohort_member_id": member_id,
            "provider_family": "siliconflow",
            "provider_model_id": label,
            "condition_id": f"condition-{index}",
            "case_id": f"case-{index}",
            "repeat_id": 0,
            "provider_attempt_count": 1,
            "prompt_tokens": 100,
            "completion_tokens": 80,
            "reasoning_tokens": 20 if index < 2 else None,
            "visible_output_tokens": 60 if index < 2 else 80,
            "total_tokens": 180,
            "provider_latency_ms": 850,
            "cost_estimate": 0.1,
            "cost_currency": "CNY",
            "identity_complete": True,
            "paper_eligible": True,
        }
        for index, (member_id, label) in enumerate(
            zip(MODEL_IDS, MODEL_LABELS, strict=True)
        )
    )
    order_rows = tuple(
        {
            "row_type": "order_sensitivity",
            "repeat_id": 0,
            "cohort_member_id": member_id,
            "order_slot": index + 1,
            "predecessor_member_id": MODEL_IDS[index - 1] if index else None,
            "completion_rate": overall_rows[index]["completion_rate"],
            "condition_windows_overlap": False,
            "observed_peak_concurrency": 3,
            "paper_eligible": True,
            "identity_complete": True,
        }
        for index, member_id in enumerate(MODEL_IDS)
    )
    failure_kinds = (
        "parse_failure",
        "checker_rejection",
        "timeout",
        "rate_limited_429",
        "identity_mismatch",
        "length_truncation",
    )
    failure_rows = tuple(
        {
            "cohort_member_id": MODEL_IDS[index % 4],
            "domain": "lean_proof" if index else "factorization",
            "topic_family": "pure_logic" if index else None,
            "failure_stage": "checker" if index == 1 else "provider",
            "failure_kind": failure_kind,
            "count": index + 1,
            "evidence_ref": f"artifacts/failure-{index}.json",
            "short_summary": f"Observed {failure_kind}; no raw reasoning retained.",
        }
        for index, failure_kind in enumerate(failure_kinds)
    )
    return {
        "suite_status": "completed",
        "paper_eligible": True,
        "identity_complete": True,
        "overall_rows": tuple(overall_rows),
        "domain_topic_rows": tuple(domain_topic_rows),
        "paired_comparison_rows": tuple(pairs),
        "model_execution_rows": model_execution_rows,
        "order_concurrency_rows": order_rows,
        "failure_taxonomy_rows": failure_rows,
    }


def _real_model_execution_row() -> dict:
    digest = "sha256:" + "7" * 64
    artifact_ref = {
        "artifact_id": "artifact-1",
        "path": "artifacts/case-1/evidence.json",
        "content_hash": digest,
    }
    return {
        "schema_version": "tokenshare.paper_exp5_model_execution_row.v1",
        "experiment_id": "exp5_real_ai_model_endpoint_comparison",
        "condition_id": "condition-0",
        "repeat_id": 0,
        "run_id": "run-0",
        "task_id": "case-0",
        "unit_id": "unit-0",
        "attempt_id": "attempt-0",
        "model_policy": "fixed_entry",
        "model_cohort_id": "exp5_siliconflow_four_model_v3",
        "model_cohort_digest": digest,
        "cohort_member_id": MODEL_IDS[0],
        "provider_config_id": "exp5_siliconflow_provider_config_v3",
        "selected_entry_id": "glm_5_2_exp5_v3",
        "provider_family": "siliconflow",
        "configured_model": "zai-org/GLM-5.2",
        "requested_model": "zai-org/GLM-5.2",
        "resolved_model": "zai-org/GLM-5.2",
        "response_model_status": "present",
        "reasoning_profile_id": "thinking_enabled",
        "source_provider_config_digest": digest,
        "prepared_execution_config_digest": digest,
        "request_ref": artifact_ref,
        "provenance_ref": artifact_ref,
        "raw_output_ref": artifact_ref,
        "usage_ref": artifact_ref,
        "model_execution_record_digest": digest,
        "identity_status": "matched",
        "worker_id": "worker-0",
        "provider_attempt_index": 0,
        "attempt_status": "succeeded",
        "provider": "siliconflow",
        "model": "zai-org/GLM-5.2",
        "entry_id": "glm_5_2_exp5_v3",
        "parsed_output_ref": artifact_ref,
        "parse_failure_ref": None,
        "fault_injection_ref": None,
        "model_execution_record_ref": {"artifact_id": "model-record-1"},
        "request_evidence_joined": True,
        "provenance_evidence_joined": True,
        "usage_evidence_joined": True,
        "started_at": "2026-07-29T00:00:00Z",
        "ended_at": "2026-07-29T00:00:00.850000Z",
        "latency_ms": 850,
        "prompt_tokens": 100,
        "completion_tokens": 80,
        "reasoning_tokens": 20,
        "visible_output_tokens": 60,
        "visible_output_basis": "provider_reasoning_breakdown",
        "total_tokens": 180,
        "cost_estimate": 0.1,
        "cost_estimate_currency": "CNY",
        "cost_estimate_status": "available",
        "provider_errors": [],
        "error_kind": None,
        "failure_reasons": [],
        "transport_kind": "real",
        "pilot_only": False,
        "attempt_paper_eligible": True,
        "task_paper_eligible": True,
        "paper_eligible": True,
    }


def _task7_paired_rows(fixture: dict) -> tuple[dict, ...]:
    return tuple(
        {
            "schema_version": "tokenshare.paper_exp5_paired_comparison.v1",
            "metric": "root_completion",
            "metric_kind": "binary",
            "stratum_id": "overall",
            "domain": "overall",
            "topic_family": None,
            "model_a": row["model_a"],
            "model_b": row["model_b"],
            "pairing_key": "case_id×repeat_id",
            "pairing_denominator": 321,
            "paired_sample_size": row["paired_sample_size"],
            "paired_case_count": 107,
            "model_a_estimate": row["model_a_estimate"],
            "model_b_estimate": row["model_b_estimate"],
            "paired_difference": row["paired_difference"],
            "ci_95_low": row["ci_95_low"],
            "ci_95_high": row["ci_95_high"],
            "confidence_level": 0.95,
            "method": "paired_mean_difference_exact_sign_flip_case_cluster_bootstrap_ci",
            "raw_p_value": row["raw_p_value"],
            "holm_adjusted_p_value": row["holm_adjusted_p_value"],
            "p_value_applicability": "applicable",
            "effect_direction": row["effect_direction"],
            "missing_model_a_count": 0,
            "missing_model_b_count": 0,
            "missing_both_count": 0,
            "missingness_reasons": [],
            "bootstrap_cluster": "case_id",
            "bootstrap_seed": 5005,
            "bootstrap_resamples": 10_000,
        }
        for row in fixture["paired_comparison_rows"]
    )


def _task7_order_rows(fixture: dict) -> tuple[dict, ...]:
    overall_by_member = {
        row["cohort_member_id"]: row for row in fixture["overall_rows"]
    }
    return tuple(
        {
            "schema_version": "tokenshare.paper_exp5_order_sensitivity.v1",
            "row_scope": "order_sensitivity",
            "analysis_role": "secondary",
            "cohort_member_id": member_id,
            "order_slot": index + 1,
            "repeat_ids": [0, 1, 2],
            "case_repeat_denominator": 321,
            "root_completion_sample_size": 321,
            "root_completion_missing_count": 0,
            "root_completion_rate": overall_by_member[member_id]["completion_rate"],
            "accepted_validity_sample_size": 321,
            "accepted_validity_missing_count": 0,
            "accepted_validity_rate": overall_by_member[member_id][
                "accepted_validity_rate"
            ],
            "total_tokens_sample_size": 321,
            "total_tokens_missing_count": 0,
            "total_tokens_median": overall_by_member[member_id][
                "total_tokens_median"
            ],
            "provider_latency_ms_sample_size": 321,
            "provider_latency_ms_missing_count": 0,
            "provider_latency_ms_median": overall_by_member[member_id][
                "provider_latency_ms_median"
            ],
        }
        for index, member_id in enumerate(MODEL_IDS)
    )


def _relative_files(root: Path, base: Path) -> list[Path]:
    return [path.relative_to(base) for path in root.rglob("*") if path.is_file()]


def _write_minimal_pdf(path: Path, title: str) -> None:
    safe_title = title.replace("\\", " ").replace("(", "[").replace(")", "]")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe_title}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    )
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(payload))
