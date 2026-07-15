import json

import pytest

from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import UrlLibSiliconFlowTransport
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskStatus,
)


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"


def test_lean_paper_adapter_runs_split_children_through_ai_api_checker_and_merge(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedLeanPaperProofTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.schema_version == "tokenshare.lean_paper_run.v1"
    assert result.split_summary["split_status"] == "succeeded"
    assert result.split_summary["child_count"] == case["expected_child_count"]
    assert len(transport.calls) == result.task_result.provider_attempt_count
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_eligible is False
    assert result.eligibility_report.paper_eligible is False
    assert "real_transport_required" in result.eligibility_report.ineligibility_reasons
    assert "unsupported_transport:scripted" in result.eligibility_report.ineligibility_reasons
    assert "secret_scan_failed" in result.eligibility_report.ineligibility_reasons
    assert result.run_evidence["secret_scan_report"]["status"] == "pending"

    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["environment_digest"] == case["environment_digest"]
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]
    assert len(result.child_results) == case["expected_child_count"]
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert all(
        item["checker"]["environment_digest"] == case["environment_digest"]
        for item in result.child_results
    )
    assert all(item["checker"]["report_ref"] for item in result.child_results)
    assert all(item["checker"]["proof_artifact_ref"] for item in result.child_results)
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.usage_ref is not None for attempt in result.attempt_results)
    assert all(attempt.provider == "siliconflow" for attempt in result.attempt_results)
    assert all(attempt.entry_id == "lean_paper_scripted" for attempt in result.attempt_results)

    provider_prompt = json.dumps(transport.calls[0]["body"], ensure_ascii=False)
    assert "lean_proof.proof_candidate.v1" in provider_prompt
    assert "Do not return a split plan" in provider_prompt
    assert "claim_checker_success" in provider_prompt


def test_lean_paper_adapter_blocks_merge_when_checker_rejects_child_proof(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement={"P": "by\n  exact hQ"}
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.task_result.failure_stage == PaperFailureStage.CHECKER
    assert result.task_result.failure_kind == PaperFailureKind.CHECKER_REJECTED
    assert result.merge_summary["status"] == "blocked"
    assert any(item["checker"]["accepted"] is False for item in result.child_results)
    assert any(
        attempt.attempt_status == PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_reports_parse_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_InvalidJsonTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert result.merge_summary["status"] == "blocked"
    assert any(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_reports_provider_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ProviderErrorTransport(),
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.failure_kind == PaperFailureKind.PROVIDER_ERROR
    assert result.merge_summary["status"] == "blocked"
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_rejects_custom_transport_marked_real(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibSiliconFlowTransport"):
        run_lean_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=_CustomRealTransportSubclass(),
            real_transport=True,
            ai_api_config=_real_transport_config(),
        )


class _CustomRealTransportSubclass(UrlLibSiliconFlowTransport):
    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        return _TransportResponse(
            status_code=200,
            body={
                "id": "fake-real-subclass",
                "model": entry.model,
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )


class _TransportResponse:
    def __init__(self, *, status_code: int, body) -> None:
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body, ensure_ascii=False)


class _InvalidJsonTransport:
    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        return _TransportResponse(
            status_code=200,
            body={
                "id": "invalid-json",
                "model": entry.model,
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )


class _ProviderErrorTransport:
    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        return _TransportResponse(
            status_code=503,
            body={"message": "provider overloaded"},
        )


def _condition(catalog_digest: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_lean_easy_w10_r0",
        domain="lean_proof",
        difficulty="easy",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="strong_only",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _real_transport_config():
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": "siliconflow",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 512,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "real_transport_guard",
                    "enabled": True,
                    "base_url": "https://api.siliconflow.cn/v1",
                    "api_key_env": "TOKENSHARE_REAL_TRANSPORT_GUARD_KEY",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {"temperature": 0.0},
                    "pricing": {
                        "currency": "CNY",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["lean_paper", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-real-transport-guard-test"},
        }
    )
