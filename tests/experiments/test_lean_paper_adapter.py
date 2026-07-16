import json
from pathlib import Path

import pytest

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
    return json.loads((Path(output_root) / request_ref["uri"]).read_text(encoding="utf-8"))


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
