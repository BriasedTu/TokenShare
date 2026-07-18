import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tokenshare.experiments.lean_paper_adapter as lean_paper_adapter
from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.experiments.lean_paper_adapter import (
    ScriptedLeanPaperProofTransport,
    run_lean_paper_case,
)
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_model_identity import (
    PaperModelIdentityMismatch,
    build_model_endpoint_identity,
)
from tokenshare.experiments.paper_models import (
    PaperAttemptStatus,
    PaperExperimentCondition,
    PaperFailureKind,
    PaperFailureStage,
    PaperTaskStatus,
)


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"
LEAN_LEMMA_GRAPH_CATALOG = "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"


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
    assert result.task_result.paper_difficulty == "simple"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.topic_family_version == "shallow_v1"
    assert result.task_result.construction_rule_id is None
    assert result.task_result.oracle_package_group is None
    assert result.task_result.proof_assembly_shape is None
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
    assert all(
        attempt.paper_difficulty == "simple"
        and attempt.topic_family == "pure_logic"
        and attempt.topic_family_version == "shallow_v1"
        and attempt.construction_rule_id is None
        and attempt.oracle_package_group is None
        and attempt.proof_assembly_shape is None
        for attempt in result.attempt_results
    )
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    provider_prompt = json.dumps(transport.calls[0]["body"], ensure_ascii=False)
    assert "lean_proof.proof_candidate.v1" in provider_prompt
    assert "Do not return a split plan" in provider_prompt
    assert "claim_checker_success" in provider_prompt


def test_lean_paper_adapter_runs_v2_medium_pure_logic_lemma_dag_nodes_through_ai_api_checker_and_merge(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert case["expected_split_kind"] == "recursive_lemma_dag"
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.provider_attempt_count == case["expected_ai_unit_count"]
    assert len(transport.calls) == case["expected_ai_unit_count"]
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_difficulty == "medium_lemma_dag"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.construction_rule_id == case["construction_rule_id"]
    assert result.task_result.oracle_package_group == case["oracle_package_group"]
    assert result.task_result.proof_assembly_shape == case["proof_assembly_shape"]
    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]
    assert result.merge_summary["merge_result_ref"]

    node_ids = {node["node_id"] for node in case["lemma_graph"]["nodes"]}
    assert {item["lemma_node_id"] for item in result.child_results} == node_ids
    assert all(item["unit_type"] == "lean_proof_lemma_node" for item in result.child_results)
    assert all(item["slot_key"].endswith(":lean_proof_artifact") for item in result.child_results)
    assert all(item["dependency_path"] for item in result.child_results)
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert all(item["candidate_output_ref"] for item in result.child_results)
    assert all(item["raw_output_ref"] for item in result.child_results)
    assert all(item["parsed_output_ref"] for item in result.child_results)
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(
        attempt.to_dict()["paper_difficulty"] == "medium_lemma_dag"
        for attempt in result.attempt_results
    )
    assert all(
        attempt.to_dict()["topic_family"] == "pure_logic"
        for attempt in result.attempt_results
    )
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    prompt = json.dumps(transport.calls[0]["body"], ensure_ascii=False)
    assert "Do not return a split plan" in prompt
    assert "Do not propose child tasks" in prompt


@pytest.mark.parametrize(
    "case_id, topic_family, expected_ai_units",
    [
        ("lean_v2_medium_function_set_dx_subset_chain_01", "function_set", 4),
        ("lean_v2_medium_induction_nat_predicate_chain_01", "induction", 5),
    ],
)
def test_lean_paper_adapter_runs_v2_medium_topic_family_regressions(
    tmp_path,
    case_id: str,
    topic_family: str,
    expected_ai_units: int,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, case_id)
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.provider_attempt_count == expected_ai_units
    assert len(transport.calls) == expected_ai_units
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.task_result.topic_family == topic_family
    assert result.task_result.paper_difficulty == "medium_lemma_dag"
    assert result.task_result.construction_rule_id == case["construction_rule_id"]
    assert result.run_evidence["lean_lemma_graph"]["topic_family"] == topic_family
    assert result.run_evidence["lean_lemma_graph"]["proof_assembly_shape"] == case["proof_assembly_shape"]


def test_lean_paper_adapter_blocks_v2_merge_when_node_checker_rejects(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    proof_sources = _oracle_sources_by_statement(case)
    proof_sources["P"] = "by\n  exact hpq"
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=proof_sources
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
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert not result.merge_summary.get("root_proof_artifact_ref")
    assert any(item["checker"]["accepted"] is False for item in result.child_results)
    assert any(
        attempt.attempt_status == PaperAttemptStatus.CHECKER_REJECTED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_maps_v2_parse_failure_without_fake_merge_success(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)

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
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_maps_v2_provider_failure_without_fake_merge_success(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)

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
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )


def test_lean_paper_adapter_returns_structured_blocked_for_v2_hard_frontier_no_oracle(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_hard_frontier_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.BLOCKED
    assert result.task_result.accepted_validity is False
    assert result.task_result.provider_attempt_count == 0
    assert result.task_result.paper_difficulty == "hard_frontier"
    assert result.task_result.topic_family == case["topic_family"]
    assert result.merge_summary["status"] == "blocked"
    assert result.merge_summary["reason"] == "structured_blocked_no_oracle_frontier_stress"
    assert result.merge_summary.get("root_checker_accepted") is not True
    assert len(transport.calls) == 0
    assert result.attempt_results == ()
    assert result.run_evidence["lean_lemma_graph"]["preflight_status"] == "structured_blocked"


def test_lean_paper_adapter_executes_checker_backed_hard_frontier_golden(
    tmp_path,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_hard_frontier_pure_logic_checker_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert case["paper_difficulty"] == "hard_frontier"
    assert case["preflight_status"] == "passed"
    assert isinstance(case["oracle_proof_package_ref"], dict)
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.paper_difficulty == "hard_frontier"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.provider_attempt_count == case["expected_ai_unit_count"]
    assert len(transport.calls) == case["expected_ai_unit_count"]
    assert result.split_summary["split_status"] == "succeeded"
    assert result.split_summary["split_kind"] == "recursive_lemma_dag"
    assert all(item["checker"]["accepted"] is True for item in result.child_results)
    assert result.merge_summary["status"] == "completed"
    assert result.merge_summary["root_checker_accepted"] is True
    assert result.merge_summary["root_checker_report_ref"]
    assert result.merge_summary["root_proof_artifact_ref"]


def test_lean_paper_adapter_simple_blocked_split_preserves_frozen_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition_for_case(catalog.catalog_digest, case)
    transport = ScriptedLeanPaperProofTransport()
    monkeypatch.setattr(
        lean_paper_adapter,
        "run_lean_split_helper",
        lambda *args, **kwargs: SimpleNamespace(
            status=SimpleNamespace(value="failed"),
            certificate=None,
            certificate_ref=None,
            report_ref=None,
        ),
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="lean_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.BLOCKED
    assert result.task_result.paper_difficulty == "simple"
    assert result.task_result.topic_family == "pure_logic"
    assert result.task_result.topic_family_version == "shallow_v1"
    assert result.task_result.construction_rule_id is None
    assert result.task_result.oracle_package_group is None
    assert result.task_result.proof_assembly_shape is None
    assert result.attempt_results == ()
    assert transport.calls == []


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


def test_lean_paper_adapter_accepts_openai_real_transport_through_executor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    monkeypatch.setenv("TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY", "test-key")
    transport = UrlLibOpenAITransport()
    calls = []

    def fake_openai_call(*, entry, api_key: str, body, timeout_seconds: int):
        calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "api_key_seen": bool(api_key),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return _TransportResponse(
            status_code=200,
            body={
                "id": "fake-openai-lean",
                "model": entry.model,
                "choices": [
                    {
                        "message": {"content": "not-json"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                },
            },
        )

    monkeypatch.setattr(transport, "post_chat_completion", fake_openai_call)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=True,
        ai_api_config=_openai_real_transport_config(),
        entry_id="openai_real_transport_guard",
    )

    assert calls
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert all(attempt.provider == "openai" for attempt in result.attempt_results)
    assert all(attempt.model == "gpt-5.6-sol" for attempt in result.attempt_results)
    assert all(
        attempt.entry_id == "openai_real_transport_guard"
        for attempt in result.attempt_results
    )
    assert result.run_evidence["transport_evidence"]["transport_kind"] == "ai_api"
    secret_scan = result.run_evidence["secret_scan_report"]
    assert secret_scan["status"] == "passed"
    assert secret_scan["secret_checked_count"] == 1
    assert secret_scan["leak_count"] == 0
    assert "secret_scan_failed" not in result.eligibility_report.ineligibility_reasons
    for attempt in result.attempt_results:
        request = _read_request_artifact(result.output_root, attempt.request_ref)
        assert request["capability_snapshot"]["provider_family"] == "openai"
        assert request["hard_requirements"]["provider_family"] == "openai"
        assert _registry_provider_matches(request) == ["openai"]


def test_lean_paper_adapter_v2_openai_request_artifacts_record_openai(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    condition = _condition_for_case(catalog.catalog_digest, case)
    monkeypatch.setenv("TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY", "test-key")
    transport = UrlLibOpenAITransport()
    calls = []

    def fake_openai_call(*, entry, api_key: str, body, timeout_seconds: int):
        calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "api_key_seen": bool(api_key),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return _TransportResponse(
            status_code=200,
            body={
                "id": "fake-openai-lean-lemma-graph",
                "model": entry.model,
                "choices": [
                    {
                        "message": {"content": "not-json"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    monkeypatch.setattr(transport, "post_chat_completion", fake_openai_call)

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=True,
        ai_api_config=_openai_real_transport_config(),
        entry_id="openai_real_transport_guard",
    )

    assert calls
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert all(attempt.provider == "openai" for attempt in result.attempt_results)
    assert all(
        attempt.to_dict()["paper_difficulty"] == "medium_lemma_dag"
        for attempt in result.attempt_results
    )
    for attempt in result.attempt_results:
        request = _read_request_artifact(result.output_root, attempt.request_ref)
        assert request["capability_snapshot"]["provider_family"] == "openai"
        assert request["hard_requirements"]["provider_family"] == "openai"
        assert _registry_provider_matches(request) == ["openai"]


def test_lean_paper_adapter_rejects_openai_url_transport_without_real_flag(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibOpenAITransport"):
        run_lean_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=UrlLibOpenAITransport(),
            real_transport=False,
        )


def test_lean_paper_adapter_rejects_same_entry_id_with_wrong_model_before_simple_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        model="gpt-5.6-sol-wrong",
        reasoning_effort="high",
    )
    transport = ScriptedLeanPaperProofTransport()
    output_root = tmp_path / "wrong-model"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
                case=case,
                condition=condition,
                output_root=output_root,
                transport=transport,
                real_transport=False,
                ai_api_config=wrong_config,
                entry_id="gpt-entry",
            )
        finally:
            assert transport.calls == []
            assert not output_root.exists()


def test_lean_paper_adapter_rejects_reasoning_profile_drift_before_lemma_dag_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case)
    )
    output_root = tmp_path / "wrong-reasoning"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
                case=case,
                condition=condition,
                output_root=output_root,
                transport=transport,
                real_transport=False,
                ai_api_config=wrong_config,
                entry_id="gpt-entry",
            )
        finally:
            assert transport.calls == []
            assert not output_root.exists()


def test_lean_retry_keeps_original_identity_and_rejects_changed_config(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )

    initial_transport = ScriptedLeanPaperProofTransport(model="gpt-5.6-sol")
    initial = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "initial-attempt",
        transport=initial_transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )
    replacement_transport = ScriptedLeanPaperProofTransport(model="gpt-5.6-sol")
    replacement = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "replacement-attempt",
        transport=replacement_transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert initial.task_result.root_status == PaperTaskStatus.COMPLETED
    assert replacement.task_result.root_status == PaperTaskStatus.COMPLETED
    assert initial.condition is condition
    assert replacement.condition is condition
    assert {call["entry_id"] for call in replacement_transport.calls} == {"gpt-entry"}
    assert {call["model"] for call in replacement_transport.calls} == {"gpt-5.6-sol"}
    for attempt in replacement.attempt_results:
        assert attempt.model_execution_record_ref is not None
        execution_record = _read_artifact(
            replacement.output_root,
            attempt.model_execution_record_ref,
        )
        assert execution_record["identity_status"] == "matched"
        assert execution_record["mismatch_reasons"] == []
        assert execution_record["requested_model"] == "gpt-5.6-sol"
        assert execution_record["resolved_model"] == "gpt-5.6-sol"
        assert execution_record["response_model_status"] == "present"
        assert execution_record["source_provider_config_digest"] == (
            approved_config.config_digest
        )
        assert execution_record["prepared_execution_config_digest"] != (
            execution_record["source_provider_config_digest"]
        )

    changed_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    rejected_transport = ScriptedLeanPaperProofTransport()
    rejected_output = tmp_path / "changed-config-attempt"
    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_lean_paper_case(
                case=case,
                condition=condition,
                output_root=rejected_output,
                transport=rejected_transport,
                real_transport=False,
                ai_api_config=changed_config,
                entry_id="gpt-entry",
            )
        finally:
            assert rejected_transport.calls == []
            assert not rejected_output.exists()


def test_lean_simple_resolved_model_mismatch_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        model="gpt-5.6-sol-versioned",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "simple-resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_resolved_model_mismatch_stops_condition(result, transport)
    assert len(result.child_results) == 1


def test_lean_lemma_dag_fixed_entry_matching_response_writes_matched_v2_records(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
        model="gpt-5.6-sol",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-matching-response",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(transport.calls) == case["expected_ai_unit_count"]
    for attempt in result.attempt_results:
        assert attempt.model_execution_record_ref is not None
        assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
        record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
        assert record["identity_status"] == "matched"
        assert record["requested_model"] == "gpt-5.6-sol"
        assert record["resolved_model"] == "gpt-5.6-sol"
        assert record["response_model_status"] == "present"
        assert record["mismatch_reasons"] == []


def test_lean_lemma_dag_resolved_model_mismatch_records_audit_and_stops_later_nodes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = ScriptedLeanPaperProofTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
        model="gpt-5.6-sol-versioned",
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_resolved_model_mismatch_stops_condition(result, transport)
    assert len(result.child_results) == 1
    assert case["expected_ai_unit_count"] > 1


def test_lean_simple_missing_resolved_model_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="lean_proof", difficulty="easy")[0]
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelLeanTransport()

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "simple-missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_missing_resolved_model_stops_condition(result, transport)
    assert len(result.child_results) == 1


def test_lean_lemma_dag_missing_resolved_model_records_audit_and_stops_later_nodes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = _catalog_with_lemma_graph()
    case = _v2_case(catalog, "lean_v2_medium_lemma_dag_01")
    approved_config = _identity_config(model="gpt-5.6-sol", reasoning_effort="high")
    condition = _formal_exp5_condition_for_case(
        catalog_digest=catalog.catalog_digest,
        case=case,
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelLeanTransport(
        proof_sources_by_statement=_oracle_sources_by_statement(case),
    )

    result = run_lean_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "lemma-dag-missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    _assert_missing_resolved_model_stops_condition(result, transport)
    assert len(result.child_results) == 1
    assert case["expected_ai_unit_count"] > 1


def _assert_resolved_model_mismatch_stops_condition(result, transport) -> None:
    assert len(transport.calls) == 1
    assert result.task_result.attempt_count == 1
    assert result.task_result.provider_attempt_count == 1
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.task_result.paper_eligible is False
    assert result.merge_summary["status"] == "blocked"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.paper_eligible is False
    assert attempt.model_execution_record_ref is not None
    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    provenance = _read_artifact(result.output_root, attempt.provenance_ref)
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["paper_eligible"] is False
    assert "resolved_model_mismatch" in record["mismatch_reasons"]
    assert record["actual_request_identities"][0]["configured_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["requested_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert record["resolved_model"] == "gpt-5.6-sol-versioned"
    assert record["source_provider_config_digest"] == (
        result.condition.source_provider_config_digest
    )
    assert record["prepared_execution_config_digest"] == provenance["config_digest"]


def _assert_missing_resolved_model_stops_condition(result, transport) -> None:
    assert len(transport.calls) == 1
    assert result.task_result.attempt_count == 1
    assert result.task_result.provider_attempt_count == 1
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.task_result.paper_eligible is False
    assert result.merge_summary["status"] == "blocked"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.model_execution_record_ref is not None
    assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
    assert attempt.raw_output_ref is not None
    assert attempt.provenance_ref is not None
    assert attempt.usage_ref is not None
    raw = _read_artifact(result.output_root, attempt.raw_output_ref)
    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    assert raw["resolved_model"] is None
    assert raw["response_model_status"] == "missing"
    assert "model" not in raw["raw_response_json"]
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["resolved_model"] is None
    assert record["response_model_status"] == "missing"
    assert record["mismatch_reasons"] == ["missing_resolved_model"]
    assert record["paper_eligible"] is False


class _MissingResolvedModelLeanTransport(ScriptedLeanPaperProofTransport):
    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        response = super().post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body.pop("model", None)
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response


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
        paper_difficulty="simple",
        topic_family="pure_logic",
        topic_family_version="shallow_v1",
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _condition_for_case(catalog_digest: str, case: dict) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"exp1_lean_{case['paper_difficulty']}_{case['case_id']}_r0",
        domain="lean_proof",
        difficulty=case["difficulty"],
        paper_difficulty=case.get("paper_difficulty"),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _formal_exp5_condition_for_case(
    *,
    catalog_digest: str,
    case: dict,
    approved_config,
) -> PaperExperimentCondition:
    identity = build_model_endpoint_identity(
        model_cohort_id="experiment_5_fixed_endpoint_cohort_v1",
        model_cohort_digest=f"sha256:{'1' * 64}",
        cohort_member_id="gpt_5_6_sol_high_openai",
        provider_config_id="openai",
        selected_entry_id="gpt-entry",
        expected_provider_family="openai",
        expected_provider_model_id="gpt-5.6-sol",
        expected_reasoning_profile_id="high",
        source_config=approved_config,
    )
    return PaperExperimentCondition(
        experiment_id="exp5_real_ai_model_endpoint_comparison",
        condition_id=f"exp5_lean_{case['case_id']}_gpt_r0",
        domain="lean_proof",
        difficulty=case["difficulty"],
        paper_difficulty=case.get("paper_difficulty"),
        topic_family=case.get("topic_family"),
        topic_family_version=case.get("topic_family_version"),
        construction_rule_id=case.get("construction_rule_id"),
        oracle_package_group=case.get("oracle_package_group"),
        proof_assembly_shape=case.get("proof_assembly_shape"),
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        model_cohort_id=identity.model_cohort_id,
        cohort_member_id=identity.cohort_member_id,
        provider_config_id=identity.provider_config_id,
        model_entry_id=identity.selected_entry_id,
        provider_family=identity.provider_family,
        provider_model_id=identity.provider_model_id,
        reasoning_profile_id=identity.reasoning_profile_id,
        model_cohort_digest=identity.model_cohort_digest,
        source_provider_config_digest=identity.source_provider_config_digest,
        model_endpoint_identity_digest=identity.model_endpoint_identity_digest,
        repeat_id=0,
        seed=1,
        catalog_digest=catalog_digest,
    )


def _catalog_with_lemma_graph():
    return load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
        lean_lemma_graph_path=LEAN_LEMMA_GRAPH_CATALOG,
    )


def _v2_case(catalog, case_id: str) -> dict:
    matches = [
        case
        for case in catalog.cases_for(domain="lean_proof")
        if case["case_id"] == case_id
    ]
    assert len(matches) == 1
    return matches[0]


def _oracle_sources_by_statement(case: dict) -> dict[str, str]:
    proof_sources_by_node = case["oracle_proof_package_ref"]["node_proof_sources"]
    result: dict[str, str] = {}
    for node in case["lemma_graph"]["nodes"]:
        result[node["theorem_payload"]["statement_source"]] = proof_sources_by_node[node["node_id"]]
    return result


def _identity_config(*, model: str, reasoning_effort: str):
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": "openai",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 1024,
                "temperature": 0.0,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "gpt-entry",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
                    "model": model,
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {
                        "temperature": 0.0,
                        "reasoning_effort": reasoning_effort,
                    },
                    "pricing": {
                        "currency": "USD",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["identity_test"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "fixed-entry-identity-test"},
        }
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


def _openai_real_transport_config():
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": "openai",
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 1024,
                "temperature": 0.0,
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "openai_real_transport_guard",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "TOKENSHARE_OPENAI_REAL_TRANSPORT_GUARD_KEY",
                    "model": "gpt-5.6-sol",
                    "endpoint": "/chat/completions",
                    "supports_json_mode": True,
                    "supports_streaming": False,
                    "request_overrides": {
                        "temperature": 0.0,
                        "reasoning_effort": "high",
                    },
                    "pricing": {
                        "currency": "USD",
                        "input_per_million_tokens": 1.0,
                        "output_per_million_tokens": 2.0,
                    },
                    "tags": ["lean_paper", "openai", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "lean-openai-real-transport-guard-test"},
        }
    )


def _read_request_artifact(output_root: str, request_ref: dict) -> dict:
    return _read_artifact(output_root, request_ref)


def _read_artifact(output_root: str, artifact_ref: dict) -> dict:
    return json.loads((Path(output_root) / artifact_ref["uri"]).read_text(encoding="utf-8"))


def _request_provider_family(output_root: str, request_ref: dict) -> str:
    request = _read_request_artifact(output_root, request_ref)
    assert request["capability_snapshot"]["provider_family"] == request["hard_requirements"][
        "provider_family"
    ]
    return str(request["hard_requirements"]["provider_family"])


def _registry_provider_matches(request: dict) -> list[str]:
    registry = ExecutorRegistry()
    registry.register(
        build_ai_api_executor_descriptor(
            executor_id="executor_ai_api_siliconflow",
            provider_family="siliconflow",
        )
    )
    registry.register(
        build_ai_api_executor_descriptor(
            executor_id="executor_ai_api_openai",
            provider_family="openai",
        )
    )
    return [
        str(descriptor.capabilities["provider_family"])
        for descriptor in registry.match_available(
            executor_type="ai_api",
            hard_requirements=request["hard_requirements"],
            request_schema_version=request["schema_version"],
        )
    ]
