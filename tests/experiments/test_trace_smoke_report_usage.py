from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tokenshare.experiments.paper_smoke_report import _actual_usage


def _consumption(
    *,
    consumption_id: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    latency_ms: int | None,
    cost_estimate_cny: str | None,
) -> dict[str, object]:
    return {
        "consumption_id": consumption_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "cost_estimate_cny": cost_estimate_cny,
    }


def _task(*consumptions: dict[str, object]) -> dict[str, object]:
    return {
        "trace_source_usage": {
            "schema_version": "tokenshare.paper_trace_source_usage.v1",
            "attribution_kind": "immutable_response_bank",
            "current_provider_call_count": 0,
            "current_provider_spend_cny": "0",
            "committed_consumption_count": len(consumptions),
            "consumptions": list(consumptions),
        }
    }


def test_trace_usage_reports_zero_current_calls_and_committed_source_metrics(
    tmp_path: Path,
) -> None:
    task = _task(
        _consumption(
            consumption_id="commit-0",
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            latency_ms=100,
            cost_estimate_cny="0.000032",
        ),
        _consumption(
            consumption_id="commit-1",
            prompt_tokens=13,
            completion_tokens=5,
            total_tokens=18,
            latency_ms=101,
            cost_estimate_cny="0.000028",
        ),
    )

    usage = _actual_usage(
        ({"provider_attempt_count": 99},),
        root=tmp_path,
        artifacts=(),
        capturing=False,
        task=task,
    )

    assert usage["provider_attempt_count"] == 0
    assert usage["provider_attempt_count_unavailable_reason"] is None
    assert usage["provider_latency_ms"] == 201.0
    assert usage["provider_latency_sample_size"] == 2
    assert usage["provider_latency_missing_count"] == 0
    assert usage["prompt_tokens"] == 24
    assert usage["completion_tokens"] == 12
    assert usage["total_tokens"] == 36
    assert Decimal(str(usage["cost_estimate"])) == Decimal("0.000060")
    assert usage["cost_estimate_sample_size"] == 2
    assert usage["cost_estimate_missing_count"] == 0
    assert usage["evidence_issue"] is None


def test_trace_usage_keeps_missing_source_metrics_null_with_counts_and_reasons(
    tmp_path: Path,
) -> None:
    task = _task(
        _consumption(
            consumption_id="commit-0",
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            latency_ms=100,
            cost_estimate_cny="0.000032",
        ),
        _consumption(
            consumption_id="commit-1",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            latency_ms=101,
            cost_estimate_cny=None,
        ),
    )

    usage = _actual_usage(
        (),
        root=tmp_path,
        artifacts=(),
        capturing=False,
        task=task,
    )

    assert usage["provider_attempt_count"] == 0
    assert usage["provider_latency_ms"] == 201.0
    assert usage["provider_latency_missing_count"] == 0
    assert usage["prompt_tokens"] is None
    assert usage["prompt_tokens_sample_size"] == 1
    assert usage["prompt_tokens_missing_count"] == 1
    assert usage["prompt_tokens_unavailable_reason"] == (
        "missing_trace_source_prompt_tokens_evidence"
    )
    assert usage["completion_tokens"] is None
    assert usage["completion_tokens_missing_count"] == 1
    assert usage["total_tokens"] is None
    assert usage["total_tokens_missing_count"] == 1
    assert usage["cost_estimate"] is None
    assert usage["cost_estimate_sample_size"] == 1
    assert usage["cost_estimate_missing_count"] == 1
    assert usage["cost_estimate_unavailable_reason"] == (
        "missing_trace_source_cost_estimate_evidence"
    )
    assert usage["evidence_issue"] == "missing_trace_source_usage_evidence"


def test_capturing_and_explicit_zero_call_semantics_remain_unchanged(
    tmp_path: Path,
) -> None:
    capturing = _actual_usage(
        (),
        root=tmp_path,
        artifacts=(),
        capturing=True,
    )
    scripted = _actual_usage(
        (
            {
                "schema_version": "tokenshare.paper_attempt_result.v2",
                "attempt_status": "executor_error",
                "provider_attempt_count": 0,
                "model_execution_record_ref": None,
            },
        ),
        root=tmp_path,
        artifacts=(),
        capturing=False,
    )

    for usage in (capturing, scripted):
        assert usage["provider_attempt_count"] == 0
        assert usage["provider_latency_ms"] == 0.0
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert usage["cost_estimate"] == 0.0
        assert usage["evidence_issue"] is None
