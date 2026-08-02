from __future__ import annotations

import json
from pathlib import Path

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
