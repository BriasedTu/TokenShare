from dataclasses import replace

from tokenshare.experiments.paper_models import (
    PaperAttemptResult,
    PaperAttemptStatus,
    evaluate_paper_eligibility,
)


def test_real_ai_gate_accepts_attempt_with_full_persisted_provider_evidence() -> None:
    attempt = _valid_attempt()
    report = evaluate_paper_eligibility(
        attempts=[attempt],
        run_evidence=_valid_run_evidence([attempt.to_dict()]),
    )

    body = report.to_dict()

    assert body["schema_version"] == "tokenshare.paper_eligibility_report.v1"
    assert body["paper_eligible"] is True
    assert body["checked_attempt_count"] == 1
    assert body["ineligibility_reasons"] == []
    assert body["real_transport"] is True
    assert body["transport_kind"] == "ai_api"
    assert body["secret_scan_passed"] is True


def test_real_ai_gate_rejects_fabricated_refs_without_artifact_inventory() -> None:
    attempt = _valid_attempt()
    run_evidence = _valid_run_evidence([attempt.to_dict()])
    run_evidence["artifact_manifests"] = []

    report = evaluate_paper_eligibility(
        attempts=[attempt],
        run_evidence=run_evidence,
    )

    assert report.paper_eligible is False
    assert "transport_evidence_ref_unverified" in report.ineligibility_reasons
    assert "secret_scan_report_ref_unverified" in report.ineligibility_reasons
    assert "attempt:attempt_1:unverified_request_ref" in report.ineligibility_reasons
    assert "attempt:attempt_1:unverified_raw_output_ref" in report.ineligibility_reasons


def test_real_ai_gate_rejects_scripted_fake_or_deterministic_transport() -> None:
    for transport_kind in ("scripted", "fake", "deterministic", "mock"):
        attempt = _valid_attempt()
        run_evidence = _valid_run_evidence([attempt.to_dict()])
        run_evidence["transport_evidence"] = {
            **run_evidence["transport_evidence"],
            "real_transport": False,
            "transport_kind": transport_kind,
        }
        report = evaluate_paper_eligibility(
            attempts=[attempt],
            run_evidence=run_evidence,
        )

        assert report.paper_eligible is False
        assert "real_transport_required" in report.ineligibility_reasons
        assert f"unsupported_transport:{transport_kind}" in report.ineligibility_reasons


def test_real_ai_gate_rejects_attempt_missing_required_provider_evidence() -> None:
    attempt = _valid_attempt().to_dict()
    attempt["raw_output_ref"] = None
    attempt["usage_ref"] = None
    attempt["parsed_output_ref"] = None
    attempt["parse_failure_ref"] = None
    attempt["provider_attempt_index"] = None
    attempt["prompt_tokens"] = None
    attempt["completion_tokens"] = None

    report = evaluate_paper_eligibility(
        attempts=[attempt],
        run_evidence=_valid_run_evidence([attempt]),
    )

    assert report.paper_eligible is False
    assert "attempt:attempt_1:missing_raw_output_ref" in report.ineligibility_reasons
    assert "attempt:attempt_1:missing_usage_ref" in report.ineligibility_reasons
    assert "attempt:attempt_1:missing_provider_attempt_index" in report.ineligibility_reasons
    assert "attempt:attempt_1:missing_prompt_tokens" in report.ineligibility_reasons
    assert "attempt:attempt_1:missing_completion_tokens" in report.ineligibility_reasons
    assert (
        "attempt:attempt_1:missing_parsed_output_or_parse_failure_ref"
        in report.ineligibility_reasons
    )


def test_real_ai_gate_rejects_secret_scan_failure_and_missing_attempts() -> None:
    run_evidence = _valid_run_evidence([])
    run_evidence["secret_scan_report"] = {
        **run_evidence["secret_scan_report"],
        "status": "failed",
        "leak_count": 1,
    }
    report = evaluate_paper_eligibility(
        attempts=[],
        run_evidence=run_evidence,
    )

    assert report.paper_eligible is False
    assert "missing_provider_attempt" in report.ineligibility_reasons
    assert "secret_scan_failed" in report.ineligibility_reasons


def test_real_ai_gate_marks_executor_error_ineligible_without_provider_artifact_requirements() -> None:
    request_ref = _artifact_ref(
        "executor-request",
        "ExecutionRequest",
        "phase3.execution_request",
        source_kind="protocol_engine",
    )
    attempt = PaperAttemptResult(
        condition_id="condition_1",
        repeat_id=0,
        run_id="run_1",
        task_id="task_1",
        unit_id="unit_root_1",
        attempt_id="attempt_root_1",
        worker_id="worker_1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.EXECUTOR_ERROR,
        provider=None,
        model=None,
        entry_id=None,
        request_ref=request_ref,
        raw_output_ref=None,
        parsed_output_ref=None,
        parse_failure_ref=None,
        provenance_ref=None,
        usage_ref=None,
        started_at="2026-07-28T00:00:00Z",
        ended_at="2026-07-28T00:00:01Z",
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_estimate=0.0,
        error_kind="retry_limit_reached",
        fault_injection_ref=None,
        paper_eligible=False,
        provider_attempt_count=0,
        executor_id="executor_factorization_runtime",
        executor_type="deterministic_local",
        schema_version="tokenshare.paper_attempt_result.v2",
    )

    report = evaluate_paper_eligibility(
        attempts=[attempt],
        run_evidence=_valid_run_evidence([attempt.to_dict()]),
    )

    reasons = set(report.ineligibility_reasons)
    assert report.paper_eligible is False
    assert "attempt:attempt_root_1:executor_error" in reasons
    for forbidden in (
        "missing_provider",
        "missing_model",
        "missing_entry_id",
        "missing_raw_output_ref",
        "missing_provenance_ref",
        "missing_usage_ref",
        "missing_parsed_output_or_parse_failure_ref",
        "missing_total_tokens",
    ):
        assert f"attempt:attempt_root_1:{forbidden}" not in reasons


def test_real_ai_gate_accepts_audited_provider_failure_with_missing_usage() -> None:
    model_record_ref = _artifact_ref(
        "model-record",
        "PaperModelExecutionRecord",
        "tokenshare.paper_model_execution_record",
    )
    attempt = replace(
        _valid_attempt(),
        attempt_status=PaperAttemptStatus.PROVIDER_ERROR,
        raw_output_ref=None,
        parsed_output_ref=None,
        model_execution_record_ref=model_record_ref,
        provider_attempt_count=1,
        latency_ms=1234,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        cost_estimate=None,
        cost_estimate_status="usage_missing",
        error_kind="timeout",
        paper_eligible=True,
        schema_version="tokenshare.paper_attempt_result.v3",
    )

    report = evaluate_paper_eligibility(
        attempts=[attempt],
        run_evidence=_valid_run_evidence([attempt.to_dict()]),
    )

    assert report.paper_eligible is True
    assert report.ineligibility_reasons == ()


def _valid_attempt() -> PaperAttemptResult:
    ref = _artifact_ref("request", "ExecutionRequest", "phase3.execution_request", source_kind="protocol_engine")
    raw_ref = _artifact_ref("raw", "RawModelOutput", "phase7.raw_model_output")
    parsed_ref = _artifact_ref("parsed", "ParsedModelOutput", "phase7.parsed_model_output")
    provenance_ref = _artifact_ref(
        "provenance",
        "AIProviderCallProvenance",
        "phase7.ai_provider_call_provenance",
    )
    usage_ref = _artifact_ref("usage", "AIUsageSummary", "tokenshare.paper_ai_usage")
    return PaperAttemptResult(
        condition_id="condition_1",
        repeat_id=0,
        run_id="run_1",
        task_id="task_1",
        unit_id="unit_1",
        attempt_id="attempt_1",
        worker_id="worker_1",
        provider_attempt_index=0,
        attempt_status=PaperAttemptStatus.SUCCEEDED,
        provider="siliconflow",
        model="GLM-5.2",
        entry_id="glm_5_2__sf_key_1",
        request_ref=ref,
        raw_output_ref=raw_ref,
        parsed_output_ref=parsed_ref,
        parse_failure_ref=None,
        provenance_ref=provenance_ref,
        usage_ref=usage_ref,
        started_at="2026-07-14T00:00:00Z",
        ended_at="2026-07-14T00:00:02Z",
        latency_ms=1234,
        prompt_tokens=40,
        completion_tokens=59,
        total_tokens=99,
        cost_estimate=0.001,
        error_kind=None,
        fault_injection_ref=None,
        paper_eligible=False,
    )


def _valid_run_evidence(attempts: list[dict]) -> dict:
    transport_ref = _artifact_ref(
        "transport_evidence",
        "AITransportEvidence",
        "tokenshare.paper_transport_evidence",
        source_kind="run_paper_experiments",
    )
    secret_scan_ref = _artifact_ref(
        "secret_scan_report",
        "SecretScanReport",
        "tokenshare.paper_secret_scan_report",
        source_kind="run_paper_experiments",
    )
    artifact_manifests = [transport_ref, secret_scan_ref]
    scanned_artifact_ids = [transport_ref["artifact_id"], secret_scan_ref["artifact_id"]]
    for attempt in attempts:
        for field_name in (
            "request_ref",
            "raw_output_ref",
            "parsed_output_ref",
            "parse_failure_ref",
            "provenance_ref",
            "usage_ref",
            "model_execution_record_ref",
        ):
            ref = attempt.get(field_name)
            if ref is not None:
                artifact_manifests.append(ref)
                scanned_artifact_ids.append(ref["artifact_id"])
    return {
        "schema_version": "tokenshare.paper_run_evidence.v1",
        "transport_evidence": {
            "schema_version": "tokenshare.paper_transport_evidence.v1",
            "real_transport": True,
            "transport_kind": "ai_api",
            "config_source": "local_gitignored_config",
            "api_key_policy": "env_only",
            "executor_kind": "ai_api_executor",
            "evidence_ref": transport_ref,
        },
        "secret_scan_report": {
            "schema_version": "tokenshare.paper_secret_scan_report.v1",
            "status": "passed",
            "leak_count": 0,
            "secret_checked_count": 1,
            "scanned_artifact_ids": scanned_artifact_ids,
            "report_ref": secret_scan_ref,
        },
        "artifact_manifests": artifact_manifests,
    }


def _artifact_ref(
    artifact_id: str,
    artifact_type: str,
    artifact_schema_id: str,
    *,
    source_kind: str = "ai_api_executor",
) -> dict:
    digest = "sha256:" + artifact_id.encode("utf-8").hex().ljust(64, "0")[:64]
    return {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "uri": artifact_id,
        "content_hash": digest,
        "size_bytes": 1,
        "media_type": "application/json",
        "artifact_schema_id": artifact_schema_id,
        "artifact_schema_version": "v1",
        "source": {"kind": source_kind},
        "metadata": {},
        "created_at": "2026-07-14T00:00:00Z",
    }
