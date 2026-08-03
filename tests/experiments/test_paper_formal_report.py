from __future__ import annotations

import json
from pathlib import Path

import pytest

import tokenshare.experiments.paper_formal_report as formal_report
import tokenshare.experiments.paper_exp5_artifacts as metric_artifacts
from tokenshare.experiments.paper_formal_metrics import (
    FormalMetricsResult,
    publish_paper_formal_metric_drafts,
)
from tokenshare.experiments.paper_formal_report import generate_paper_formal_report
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract

from test_paper_metric_renderer_contract import (
    _DIGEST,
    _collection_digest,
    _complete_publication,
)


def _render_report(tmp_path: Path, table_id: str, column_id: str):
    del table_id, column_id
    metrics = _persisted_task20_metrics(tmp_path)
    return generate_paper_formal_report(output_root=tmp_path, metrics=metrics)


def _persisted_task20_metrics(tmp_path: Path) -> FormalMetricsResult:
    from tests.experiments.test_paper_formal_metrics import _real_canonical_rows

    return publish_paper_formal_metric_drafts(
        tmp_path,
        _real_canonical_rows(tmp_path / "canonical-inputs"),
    )


def test_exp3_caption_says_paired_trace_reference_not_actual_online(
    tmp_path: Path,
) -> None:
    result = _render_report(
        tmp_path,
        "exp3_trace_robustness",
        "trace_replay_wall_clock_overhead_ms",
    )

    caption = result.captions["exp3_trace_robustness"]
    assert "comparison_kind=paired_trace_reference" in caption
    assert "not per-condition actual online timing" in caption
    assert "not per-condition paid timing" in caption


def test_exp5_caption_discloses_endpoint_and_serving_confounding(
    tmp_path: Path,
) -> None:
    result = _render_report(tmp_path, "exp5_quality", "completion_rate")

    for table_id in ("exp5_quality", "exp5_resources"):
        caption = result.captions[table_id]
        assert "endpoint" in caption
        assert "serving" in caption
        assert "confounding" in caption
        assert "pure model effect" in caption


def test_cost_claim_says_usage_pricing_estimate_not_actual_bill(tmp_path: Path) -> None:
    result = _render_report(
        tmp_path,
        "exp5_resources",
        "actual_cost_estimate_cny",
    )

    captions = "\n".join(result.captions.values()).lower()
    assert "usage × frozen-pricing estimate" in captions
    assert "cost estimate" in captions
    assert "actual bill" not in captions
    assert "paid amount" not in captions
    assert "invoice" not in captions
    persisted = tmp_path / "formal_report_result.json"
    assert persisted.is_file()
    assert not list(tmp_path.glob("*.md"))


def test_formal_report_refs_name_their_actual_audits(tmp_path: Path) -> None:
    metrics = _persisted_task20_metrics(tmp_path)

    result = generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
        secret_values=("not-present",),
    )

    assert result.report_ref["path"] == "audit/paper_formal_report_manifest.v2.json"
    assert result.eligibility_report_ref["path"] == "audit/paper_eligibility_report.json"
    assert result.secret_scan_report_ref["path"] == "audit/secret_scan_report.json"
    assert result.cell_lineage_ref["path"] == "audit/paper_cell_lineage.jsonl"
    assert result.tables_digest.startswith("sha256:")
    assert result.cell_lineage_digest.startswith("sha256:")
    for ref, schema in (
        (result.eligibility_report_ref, "tokenshare.paper_formal_eligibility_report.v2"),
        (result.secret_scan_report_ref, "tokenshare.paper_output_secret_scan.v1"),
    ):
        body = json.loads((tmp_path / ref["path"]).read_text(encoding="utf-8"))
        assert body["schema_version"] == schema


def test_constructed_metrics_without_persisted_task20_closure_fail_closed(
    tmp_path: Path,
) -> None:
    observations = _complete_publication()
    constructed = FormalMetricsResult(
        table_drafts=(),
        metric_observations=observations,
        lineage_source_index=None,
        metrics_digest=_DIGEST,
        observations_digest=_collection_digest(observations),
        output_refs=(),
    )

    result = generate_paper_formal_report(output_root=tmp_path, metrics=constructed)

    assert result.paper_eligible is False
    assert result.formal_paper_table_generated is False
    assert not (tmp_path / "metrics").exists()
    eligibility = json.loads(
        (tmp_path / result.eligibility_report_ref["path"]).read_text(encoding="utf-8")
    )
    assert "task20_publication_invalid" in eligibility["ineligibility_reasons"]


def test_incomplete_formal_metrics_are_audited_but_never_publishable(
    tmp_path: Path,
) -> None:
    empty = ()
    metrics = FormalMetricsResult(
        table_drafts=(),
        metric_observations=empty,
        lineage_source_index=None,
        metrics_digest=_DIGEST,
        observations_digest=_collection_digest(empty),
        output_refs=(),
    )

    result = generate_paper_formal_report(output_root=tmp_path, metrics=metrics)

    assert result.paper_eligible is False
    assert result.formal_paper_table_generated is False
    assert not (tmp_path / "metrics").exists()
    eligibility = json.loads(
        (tmp_path / result.eligibility_report_ref["path"]).read_text(encoding="utf-8")
    )
    assert "task20_publication_invalid" in eligibility["ineligibility_reasons"]


def test_lineage_failure_after_renderer_success_never_promotes_current_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _persisted_task20_metrics(tmp_path)
    calls = []

    def fail_after_renderer(**kwargs):
        calls.append(Path(kwargs["output_root"]))
        raise ValueError("injected lineage completion failure")

    monkeypatch.setattr(formal_report, "write_cell_lineage_audit", fail_after_renderer)
    result = formal_report.generate_paper_formal_report(
        output_root=tmp_path,
        metrics=metrics,
    )

    assert calls and calls[0] != tmp_path
    assert result.paper_eligible is False
    assert result.formal_paper_table_generated is False
    assert result.cell_lineage_ref == {}
    assert not (tmp_path / "audit" / "paper_metric_renderer_manifest.v1.json").exists()
    assert not (tmp_path / "audit" / "paper_cell_lineage.jsonl").exists()
    blocked = json.loads(
        (tmp_path / result.report_ref["path"]).read_text(encoding="utf-8")
    )
    assert "renderer_input_invalid" in blocked["ineligibility_reasons"]


def test_partial_complete_report_promotion_restores_every_owned_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _persisted_task20_metrics(tmp_path)
    owned = tuple(
        sorted(
            {
                *metric_artifacts.PAPER_TABLE_CSV_FILES,
                *metric_artifacts.PAPER_TABLE_TEX_FILES,
                metric_artifacts.PAPER_RENDERER_AUDIT_FILE,
                metric_artifacts.PAPER_RENDERER_MANIFEST_FILE,
                *metric_artifacts._RETIRED_OUTPUTS,
                "audit/paper_cell_lineage.jsonl",
                formal_report.PAPER_SECRET_SCAN_REPORT,
                formal_report.PAPER_ELIGIBILITY_REPORT,
                formal_report.PAPER_FORMAL_REPORT_MANIFEST,
                "formal_report_result.json",
            }
        )
    )
    for index, relative_path in enumerate(owned):
        if index % 2 == 0 and relative_path != formal_report.PAPER_FORMAL_REPORT_MANIFEST:
            path = tmp_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"prior:{relative_path}".encode("utf-8"))
    pointer = tmp_path / "unrelated" / "CURRENT.json"
    pointer.parent.mkdir()
    pointer.write_bytes(b"prior-current")
    before = {
        relative_path: (
            None
            if not (tmp_path / relative_path).is_file()
            else (tmp_path / relative_path).read_bytes()
        )
        for relative_path in owned
    }
    before_temps = tuple(
        sorted(path.name for path in tmp_path.iterdir() if path.name.startswith(".paper"))
    )
    original_replace = Path.replace
    promoted: list[str] = []
    final_targets = {
        (tmp_path / relative_path).resolve(strict=False): relative_path
        for relative_path in owned
    }

    def fail_after_one_promotion(source: Path, target):
        resolved_target = Path(target).resolve(strict=False)
        relative_path = final_targets.get(resolved_target)
        if relative_path is not None and "backup" not in source.as_posix():
            promoted.append(relative_path)
            if len(promoted) == 2:
                raise OSError("injected complete-report promotion failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_after_one_promotion)

    with pytest.raises(OSError, match="complete-report promotion failure"):
        formal_report.generate_paper_formal_report(
            output_root=tmp_path,
            metrics=metrics,
        )

    assert len(promoted) >= 2
    assert {
        relative_path: (
            None
            if not (tmp_path / relative_path).is_file()
            else (tmp_path / relative_path).read_bytes()
        )
        for relative_path in owned
    } == before
    assert pointer.read_bytes() == b"prior-current"
    assert tuple(
        sorted(path.name for path in tmp_path.iterdir() if path.name.startswith(".paper"))
    ) == before_temps
    assert not (tmp_path / formal_report.PAPER_FORMAL_REPORT_MANIFEST).exists()
