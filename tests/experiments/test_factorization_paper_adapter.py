import json
from pathlib import Path

import pytest

import tokenshare.experiments.factorization_paper_adapter as factorization_paper_adapter_module
from tokenshare.executors.ai_api import build_ai_api_executor_descriptor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_transport import (
    UrlLibOpenAITransport,
    UrlLibSiliconFlowTransport,
)
from tokenshare.executors.registry import ExecutorRegistry
from tokenshare.experiments.factorization_paper_adapter import (
    ScriptedFactorizationRangeTransport,
    _paper_task_status_from_runtime,
    _prepare_config,
    _validate_factorization_case_for_adapter,
    run_factorization_paper_case,
)
from tokenshare.experiments.paper_unit_commitments import (
    build_case_ai_unit_bindings,
    task_unit_snapshot_commitment,
)
from tokenshare.plugins.factorization.runtime_adapter import (
    FactorizationRuntimeAdapter,
)
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.experiments.paper_catalog import load_paper_catalogs
from tokenshare.experiments.paper_factorization_catalog import (
    generate_factorization_paper_cases,
)
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
from tokenshare.plugins.factorization.split_strategy import partition_candidate_ranges
from tokenshare.local_runtime import ProtocolRunCoordinator
from tokenshare.storage.events import EventLedger, EventType


FACTOR_CATALOG = "benchmarks/paper/factorization_catalog.v1.jsonl"
LEAN_CATALOG = "benchmarks/paper/lean_catalog.v1.jsonl"


def test_factorization_v2_all_500_cases_pass_adapter_complete_domain_preflight() -> None:
    cases = generate_factorization_paper_cases()

    for case in cases:
        _validate_factorization_case_for_adapter(case)

    assert len(cases) == 500


def test_factorization_runtime_status_projection_is_terminal_and_fail_closed() -> None:
    assert (
        _paper_task_status_from_runtime("completed", accepted_validity=True)
        == PaperTaskStatus.COMPLETED
    )
    assert (
        _paper_task_status_from_runtime("completed", accepted_validity=False)
        == PaperTaskStatus.FAILED
    )
    assert (
        _paper_task_status_from_runtime("failed", accepted_validity=True)
        == PaperTaskStatus.FAILED
    )
    for invalid_status in ("processing", "unknown"):
        with pytest.raises(ValueError, match="terminal runtime status"):
            _paper_task_status_from_runtime(
                invalid_status,
                accepted_validity=False,
            )


def test_factorization_v2_easy_semiprime_completes_parser_verifier_canonical_and_merge(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "easy"
        and case["factor_position_quantile"] != "no_factor"
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert len(transport.calls) == 2
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.final_prime_factors == case["oracle_prime_factors"]
    assert result.merge_summary["result_kind"] == "prime_factorization_result"
    assert all(item["verification"]["accepted"] for item in result.range_results)
    assert all(item["canonical_output_ref"] for item in result.range_results)


def test_factorization_v2_hard_prime_control_merges_complete_no_factor_ranges(
    tmp_path,
) -> None:
    case = next(
        case
        for case in generate_factorization_paper_cases()
        if case["difficulty"] == "hard"
        and case["factor_position_quantile"] == "no_factor"
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert len(transport.calls) == 8
    assert all(
        call["range_result"]["result_kind"] == "no_factor_in_range"
        for call in transport.calls
    )
    assert all(item["verification"]["accepted"] for item in result.range_results)
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.final_prime_factors == [
        {"prime": case["target_n"], "exponent": 1}
    ]


def test_factorization_v2_large_no_factor_range_uses_canonical_child_length_as_budget(
    tmp_path,
) -> None:
    case, child_index, child_length = _v2_case_with_large_no_factor_range()
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id=f"range_{child_index}",
    )

    assert child_length > 100_000
    assert transport.calls[0]["range_result"]["result_kind"] == "no_factor_in_range"
    assert result.range_results[0]["verification"]["accepted"] is True
    assert (
        result.range_results[0]["verification"]["layer_summary"]["details"]
        ["checked_divisor_count"]
        == child_length
    )


def test_factorization_v2_large_range_false_no_factor_is_rejected_after_full_recheck(
    tmp_path,
) -> None:
    case, child_index, child_length = _v2_case_with_large_factor_range()
    transport = ScriptedFactorizationRangeTransport(
        force_false_negative_child_indices={child_index}
    )

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id=f"range_{child_index}",
    )

    assert child_length > 100_000
    assert result.range_results[0]["verification"]["accepted"] is False
    assert (
        result.range_results[0]["verification"]["layer_summary"]["reason_code"]
        == "divisor_exists_in_range"
    )
    assert result.task_result.failure_stage == PaperFailureStage.VERIFICATION
    assert result.task_result.failure_kind == PaperFailureKind.VERIFIER_REJECTED


def test_factorization_paper_adapter_runs_range_children_through_ai_api_executor_and_merges(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.schema_version == "tokenshare.factorization_paper_run.v1"
    assert result.split_summary["split_strategy_id"] == "factorization.candidate_range_partition.v1"
    assert result.split_summary["range_child_count"] == case["split_params"]["requested_child_count"]
    assert len(transport.calls) == result.task_result.provider_attempt_count
    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert result.task_result.accepted_validity is True
    assert result.task_result.paper_eligible is False
    assert result.eligibility_report.paper_eligible is False
    assert "real_transport_required" in result.eligibility_report.ineligibility_reasons
    assert "unsupported_transport:scripted" in result.eligibility_report.ineligibility_reasons
    assert "secret_scan_failed" in result.eligibility_report.ineligibility_reasons
    assert result.run_evidence["secret_scan_report"]["status"] == "pending"

    assert result.final_prime_factors == case["oracle_prime_factors"]
    assert result.merge_summary["result_kind"] == "prime_factorization_result"
    assert len(result.attempt_results) == result.split_summary["range_child_count"]
    assert len(result.range_results) == result.split_summary["range_child_count"]
    assert all(item["verification"]["accepted"] is True for item in result.range_results)
    assert all(item["canonical_output_ref"] for item in result.range_results)
    assert all(attempt.raw_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.parsed_output_ref is not None for attempt in result.attempt_results)
    assert all(attempt.usage_ref is not None for attempt in result.attempt_results)
    assert all(attempt.provider == "siliconflow" for attempt in result.attempt_results)
    assert all(attempt.entry_id == "factorization_paper_scripted" for attempt in result.attempt_results)
    assert all(
        _request_provider_family(result.output_root, attempt.request_ref) == "siliconflow"
        for attempt in result.attempt_results
    )

    provider_prompt = json.dumps(transport.calls[0]["body"], ensure_ascii=False)
    assert "factorization.range_result.v1" in provider_prompt
    assert "Search divisor range" in provider_prompt
    assert "direct_factorization_answer" not in provider_prompt


def test_factorization_paper_adapter_can_execute_exactly_one_selected_ai_unit(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        selected_ai_unit_id="range_0",
    )

    assert len(transport.calls) == 1
    assert len(result.attempt_results) == 1
    assert result.attempt_results[0].planned_ai_unit_id == "range_0"
    assert result.merge_summary["status"] == "blocked"
    assert result.task_result.root_status == PaperTaskStatus.FAILED

    untouched_transport = ScriptedFactorizationRangeTransport()
    with pytest.raises(ValueError, match="selected_ai_unit_id"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path / "invalid-selection",
            transport=untouched_transport,
            real_transport=False,
            entry_id="factorization_paper_scripted",
            selected_ai_unit_id="range_99",
        )
    assert untouched_transport.calls == []


def test_factorization_paper_adapter_blocks_merge_when_verifier_rejects_range_result(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = ScriptedFactorizationRangeTransport(force_false_negative_child_indices={0})

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.accepted_validity is False
    assert result.task_result.failure_stage == PaperFailureStage.VERIFICATION
    assert result.task_result.failure_kind == PaperFailureKind.VERIFIER_REJECTED
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert any(item["verification"]["accepted"] is False for item in result.range_results)


def test_factorization_paper_adapter_reports_parse_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_InvalidJsonTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PARSE
    assert result.task_result.failure_kind == PaperFailureKind.PARSE_FAILURE
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert any(
        attempt.attempt_status == PaperAttemptStatus.PARSE_FAILED
        for attempt in result.attempt_results
    )


@pytest.mark.parametrize(
    "mode",
    (
        "FULL",
        "NO_VERIFICATION",
        "NO_PARSER_POLICY",
        "NO_REQUEUE",
        "NO_MERGE_GATE",
        "NO_SLOT_INTEGRITY",
    ),
)
def test_factorization_exp4_ablation_modes_execute_inside_adapter_lifecycle(
    tmp_path: Path,
    mode: str,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)
    transport = (
        _InvalidJsonTransport()
        if mode == "NO_PARSER_POLICY"
        else ScriptedFactorizationRangeTransport(
            force_false_negative_child_indices=(
                {0}
                if mode in {"NO_VERIFICATION", "NO_MERGE_GATE"}
                else set()
            )
        )
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / mode,
        transport=transport,
        real_transport=False,
        entry_id="factorization_paper_scripted",
        ablation_mode=mode,
    )

    runtime = result.run_evidence["ablation_runtime"]
    assert runtime["mode"] == mode
    assert runtime["applied_before_adapter_completion"] is True
    if mode == "FULL":
        assert result.task_result.root_status == PaperTaskStatus.COMPLETED
        assert runtime["disabled_mechanism"] is None
    elif mode == "NO_VERIFICATION":
        assert any(
            item["verification"]["status"] == "passed"
            and item["verification"]["metadata"] == {
                "ablation_mode": "NO_VERIFICATION",
                "domain_verifier_invoked": False,
            }
            and item["canonical_output_ref"] is not None
            for item in result.range_results
        )
        assert any(
            observation["disabled_mechanism"] == "verification"
            for observation in runtime["hook_observations"]
        )
        assert result.task_result.accepted_validity is False
    elif mode == "NO_PARSER_POLICY":
        assert all(
            item.raw_output_ref is not None
            and item.parsed_output_ref is not None
            and item.parsed_output_ref["content_hash"]
            == item.raw_output_ref["content_hash"]
            for item in result.attempt_results
        )
        assert runtime["raw_only_exposed"] is True
        assert any(
            observation["disabled_mechanism"] == "parser_policy"
            for observation in runtime["hook_observations"]
        )
    elif mode == "NO_REQUEUE":
        assert runtime["replacement_attempts_allowed"] is False
        assert result.task_result.attempt_count == result.split_summary[
            "range_child_count"
        ]
    elif mode == "NO_MERGE_GATE":
        assert runtime["premature_merge_attempted"] is True
        assert runtime["root_validity_audit_passed"] is False
        assert any(
            observation["disabled_mechanism"] == "merge_gate"
            for observation in runtime["hook_observations"]
        )
    else:
        assert runtime["slot_integrity_violation"] is True
        assert runtime["root_validity_audit_passed"] is True
        assert any(
            observation["disabled_mechanism"] == "slot_integrity"
            for observation in runtime["hook_observations"]
        )


def test_factorization_paper_adapter_reports_provider_failure_stage(tmp_path) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ProviderErrorTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.failure_kind == PaperFailureKind.PROVIDER_ERROR
    assert result.final_prime_factors == []
    assert result.merge_summary["status"] == "blocked"
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        for attempt in result.attempt_results
    )


def test_factorization_paper_adapter_rejects_custom_transport_marked_real(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibSiliconFlowTransport"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=_CustomRealTransportSubclass(),
            real_transport=True,
            ai_api_config=_real_transport_config(),
        )


def test_factorization_paper_adapter_accepts_openai_real_transport_through_executor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
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
                "id": "fake-openai-factorization",
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

    result = run_factorization_paper_case(
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


def test_factorization_paper_adapter_rejects_openai_url_transport_without_real_flag(
    tmp_path,
) -> None:
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    condition = _condition(catalog.catalog_digest)

    with pytest.raises(ValueError, match="UrlLibOpenAITransport"):
        run_factorization_paper_case(
            case=case,
            condition=condition,
            output_root=tmp_path,
            transport=UrlLibOpenAITransport(),
            real_transport=False,
        )


def test_factorization_paper_adapter_rejects_same_entry_id_from_wrong_provider_before_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    wrong_config = _identity_config(
        provider_family="siliconflow",
        model="Qwen/Qwen3.6-27B",
        reasoning_effort=None,
    )
    transport = ScriptedFactorizationRangeTransport()
    output_root = tmp_path / "wrong-provider"

    with pytest.raises(PaperModelIdentityMismatch):
        try:
            run_factorization_paper_case(
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


def test_factorization_fixed_entry_503_does_not_failover_to_sibling_model(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    source_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        sibling_model="gpt-5.6-sol-sibling",
        max_provider_attempts=2,
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=source_config,
    )
    transport = _RecordingProviderErrorTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "selected-entry-503",
        transport=transport,
        real_transport=False,
        ai_api_config=source_config,
        entry_id="gpt-entry",
    )

    expected_ai_units = case["split_params"]["requested_child_count"]
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.task_result.failure_stage == PaperFailureStage.PROVIDER
    assert result.task_result.provider_attempt_count == expected_ai_units
    assert len(transport.calls) == expected_ai_units
    assert {call["entry_id"] for call in transport.calls} == {"gpt-entry"}
    assert {call["model"] for call in transport.calls} == {"gpt-5.6-sol"}
    for attempt in result.attempt_results:
        assert attempt.provenance_ref is not None
        provenance = json.loads(
            (Path(result.output_root) / attempt.provenance_ref["uri"]).read_text(
                encoding="utf-8"
            )
        )
        assert provenance["selection_record"]["eligible_entry_ids"] == ["gpt-entry"]
        assert provenance["selection_record"]["attempt_entry_ids"] == ["gpt-entry"]


def test_factorization_resolved_model_mismatch_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = _ResolvedModelMismatchFactorizationTransport(
        resolved_model="gpt-5.6-sol-versioned",
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "resolved-model-mismatch",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert len(transport.calls) == result.split_summary["range_child_count"]
    assert result.task_result.attempt_count == len(transport.calls)
    assert result.task_result.provider_attempt_count == len(transport.calls)
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
    assert all(
        item.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        and item.paper_eligible is False
        for item in result.attempt_results
    )

    record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
    provenance = _read_artifact(result.output_root, attempt.provenance_ref)
    assert record["schema_version"] == "tokenshare.paper_model_execution_record.v2"
    assert record["identity_status"] == "model_identity_mismatch"
    assert record["paper_eligible"] is False
    assert "resolved_model_mismatch" in record["mismatch_reasons"]
    assert record["expected_identity"]["model_endpoint_identity_digest"] == (
        condition.model_endpoint_identity_digest
    )
    assert record["actual_request_identities"][0]["configured_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["requested_model"] == "gpt-5.6-sol"
    assert record["actual_request_identities"][0]["reasoning_controls"] == {
        "reasoning_effort": "high"
    }
    assert record["resolved_model"] == "gpt-5.6-sol-versioned"
    assert record["source_provider_config_digest"] == approved_config.config_digest
    assert record["prepared_execution_config_digest"] == provenance["config_digest"]
    assert record["request_ref"] == attempt.request_ref
    assert record["provenance_ref"] == attempt.provenance_ref
    assert record["raw_output_ref"] == attempt.raw_output_ref
    assert record["usage_ref"] == attempt.usage_ref


def test_factorization_fixed_entry_matching_response_writes_matched_v2_records(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = ScriptedFactorizationRangeTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "matching-response",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert result.task_result.root_status == PaperTaskStatus.COMPLETED
    assert len(transport.calls) == len(result.attempt_results)
    assert len(transport.calls) > 1
    for attempt in result.attempt_results:
        assert attempt.model_execution_record_ref is not None
        assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
        record = _read_artifact(result.output_root, attempt.model_execution_record_ref)
        assert record["identity_status"] == "matched"
        assert record["requested_model"] == "gpt-5.6-sol"
        assert record["resolved_model"] == "gpt-5.6-sol"
        assert record["response_model_status"] == "present"
        assert record["mismatch_reasons"] == []


def test_factorization_missing_resolved_model_records_audit_and_stops_later_units(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    catalog = load_paper_catalogs(
        factorization_path=FACTOR_CATALOG,
        lean_path=LEAN_CATALOG,
    )
    case = catalog.cases_for(domain="factorization", difficulty="easy")[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=catalog.catalog_digest,
        domain="factorization",
        difficulty="easy",
        approved_config=approved_config,
    )
    transport = _MissingResolvedModelFactorizationTransport()

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path / "missing-resolved-model",
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert len(transport.calls) == result.split_summary["range_child_count"]
    assert result.task_result.failure_stage == PaperFailureStage.AUDIT
    assert result.task_result.failure_kind == PaperFailureKind.MODEL_IDENTITY_MISMATCH
    assert result.merge_summary["status"] == "blocked"
    assert result.range_results[0]["range_result"] is None
    assert result.range_results[0]["verification"]["status"] == "model_identity_mismatch"
    attempt = result.attempt_results[0]
    assert attempt.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
    assert attempt.error_kind == "model_identity_mismatch"
    assert attempt.raw_output_ref is not None
    assert attempt.provenance_ref is not None
    assert attempt.usage_ref is not None
    assert attempt.model_execution_record_ref is not None
    assert attempt.model_execution_record_ref["artifact_schema_version"] == "v2"
    assert all(
        item.attempt_status == PaperAttemptStatus.MODEL_IDENTITY_MISMATCH
        and item.paper_eligible is False
        for item in result.attempt_results
    )
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


class _RecordingProviderErrorTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        self.calls.append(
            {
                "entry_id": entry.entry_id,
                "model": entry.model,
                "api_key_seen": bool(api_key),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return _TransportResponse(
            status_code=503,
            body={"message": "provider overloaded"},
        )


class _ResolvedModelMismatchFactorizationTransport(
    ScriptedFactorizationRangeTransport
):
    def __init__(self, *, resolved_model: str) -> None:
        super().__init__()
        self.resolved_model = resolved_model

    def post_chat_completion(self, *, entry, api_key: str, body, timeout_seconds: int):
        response = super().post_chat_completion(
            entry=entry,
            api_key=api_key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        response.body["model"] = self.resolved_model
        response.text = json.dumps(response.body, ensure_ascii=False)
        return response


class _MissingResolvedModelFactorizationTransport(
    ScriptedFactorizationRangeTransport
):
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


def _v2_condition(case: dict) -> PaperExperimentCondition:
    difficulty = str(case["difficulty"])
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id=f"exp1_factorization_{difficulty}_v2_r0",
        domain="factorization",
        difficulty=difficulty,
        paper_difficulty=difficulty,
        worker_count=10,
        fault_type="none",
        fault_rate=0.0,
        ablation_mode="FULL",
        model_policy="fixed_entry",
        repeat_id=0,
        seed=1,
        catalog_digest="sha256:" + "0" * 64,
    )


def _v2_case_with_large_no_factor_range() -> tuple[dict, int, int]:
    for case in generate_factorization_paper_cases():
        partition = _v2_partition(case)
        oracle_primes = {int(item["prime"]) for item in case["oracle_prime_factors"]}
        for range_input in partition.ranges:
            start = int(range_input.range_start)
            end = int(range_input.range_end)
            child_length = end - start + 1
            if child_length > 100_000 and not any(
                start <= prime <= end for prime in oracle_primes
            ):
                return case, range_input.child_index, child_length
    raise AssertionError("generated v2 catalog must contain a >100,000 no-factor child range")


def _v2_case_with_large_factor_range() -> tuple[dict, int, int]:
    for case in generate_factorization_paper_cases():
        partition = _v2_partition(case)
        oracle_primes = {int(item["prime"]) for item in case["oracle_prime_factors"]}
        for range_input in partition.ranges:
            start = int(range_input.range_start)
            end = int(range_input.range_end)
            child_length = end - start + 1
            if child_length > 100_000 and any(
                start <= prime <= end for prime in oracle_primes
            ):
                return case, range_input.child_index, child_length
    raise AssertionError("generated v2 catalog must contain a >100,000 factor child range")


def _v2_partition(case: dict):
    requested_children = int(case["split_params"]["requested_child_count"])
    return partition_candidate_ranges(
        target_n=case["target_n"],
        requested_child_count=requested_children,
        max_children_per_unit=requested_children,
        min_divisor=case["candidate_start"],
        max_divisor=case["candidate_end"],
    )


def _condition(catalog_digest: str) -> PaperExperimentCondition:
    return PaperExperimentCondition(
        experiment_id="exp1_real_ai_feasibility",
        condition_id="exp1_factorization_easy_w10_r0",
        domain="factorization",
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


def _formal_exp5_condition(
    *,
    catalog_digest: str,
    domain: str,
    difficulty: str,
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
        condition_id=f"exp5_{domain}_{difficulty}_gpt_r0",
        domain=domain,
        difficulty=difficulty,
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


def _identity_config(
    *,
    provider_family: str,
    model: str,
    reasoning_effort: str | None,
    sibling_model: str | None = None,
    max_provider_attempts: int = 1,
):
    request_overrides = {"temperature": 0.0}
    if reasoning_effort is not None:
        request_overrides["reasoning_effort"] = reasoning_effort
    base_url = (
        "https://api.openai.com/v1"
        if provider_family == "openai"
        else "https://api.siliconflow.cn/v1"
    )
    entries = [
        {
            "entry_id": "gpt-entry",
            "enabled": True,
            "base_url": base_url,
            "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
            "model": model,
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": request_overrides,
            "pricing": {
                "currency": "USD",
                "input_per_million_tokens": 1.0,
                "output_per_million_tokens": 2.0,
            },
            "tags": ["identity_test"],
        }
    ]
    if sibling_model is not None:
        entries.append(
            {
                "entry_id": "sibling-entry",
                "enabled": True,
                "base_url": base_url,
                "api_key_env": "TOKENSHARE_IDENTITY_TEST_KEY",
                "model": sibling_model,
                "endpoint": "/chat/completions",
                "supports_json_mode": True,
                "supports_streaming": False,
                "request_overrides": request_overrides,
                "pricing": {
                    "currency": "USD",
                    "input_per_million_tokens": 1.0,
                    "output_per_million_tokens": 2.0,
                },
                "tags": ["identity_test", "sibling"],
            }
        )
    return load_ai_api_config(
        {
            "schema_version": "phase7.ai_api_executor_config.v1",
            "executor_id": "executor_ai_api",
            "provider_family": provider_family,
            "selection_policy": {
                "kind": "uniform_random_without_weights",
                "seed_source": "request_or_environment_seed",
            },
            "defaults": {
                "timeout_seconds": 30,
                "max_tokens": 512,
                "temperature": 0.0,
                "max_provider_attempts": max_provider_attempts,
            },
            "entries": entries,
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
                    "tags": ["factorization_paper", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "real-transport-guard-test"},
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
                "max_tokens": 512,
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
                    "tags": ["factorization_paper", "openai", "real_guard"],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": {"purpose": "openai-real-transport-guard-test"},
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
def test_factorization_budget_commitments_use_runtime_plan_unit_snapshots(
    tmp_path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    adapter = FactorizationRuntimeAdapter(provider_family="siliconflow", seed=7)
    planned_units = adapter.plan_units(case, artifact_store=ArtifactStore(tmp_path))

    bindings = build_case_ai_unit_bindings(case, seed=7)

    assert {
        binding["unit_id"]: binding["task_unit_snapshot_commitment"]
        for binding in bindings
    } == {
        unit.unit_id: task_unit_snapshot_commitment(unit.to_dict())
        for unit in planned_units
    }


def test_factorization_full_adapter_is_coordinator_thin_shell_with_event_refs(
    tmp_path,
    monkeypatch,
) -> None:
    case = generate_factorization_paper_cases()[0]
    calls = 0
    original = ProtocolRunCoordinator.run_root

    def recording(self, request):
        nonlocal calls
        calls += 1
        return original(self, request)

    monkeypatch.setattr(ProtocolRunCoordinator, "run_root", recording)

    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    assert calls == 1
    assert result.task_result.event_refs
    assert result.eligibility_report.paper_eligible is False


def test_factorization_full_preserves_ai_parsed_provenance_before_canonicalization(
    tmp_path,
) -> None:
    case = generate_factorization_paper_cases()[0]
    result = run_factorization_paper_case(
        case=case,
        condition=_v2_condition(case),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        entry_id="factorization_paper_scripted",
    )

    ranges_by_unit = {item["unit_id"]: item for item in result.range_results}
    for attempt in result.attempt_results:
        assert attempt.parsed_output_ref is not None
        assert attempt.parsed_output_ref["artifact_type"] == "ParsedModelOutput"
        canonical_ref = ranges_by_unit[attempt.unit_id]["canonical_output_ref"]
        assert canonical_ref is not None
        assert canonical_ref["artifact_type"] == "canonical_output"
        assert canonical_ref["artifact_id"] != attempt.parsed_output_ref["artifact_id"]
        canonical_body_ref = next(
            ref
            for ref in result.task_result.artifact_refs
            if ref["artifact_id"] == canonical_ref["artifact_id"]
        )
        source = canonical_body_ref["source"]
        assert source["parsed_output_ref"] == attempt.parsed_output_ref
        assert source["raw_output_ref"] == attempt.raw_output_ref
        assert source["candidate_output_ref"]["artifact_type"] == "CandidateOutput"


def test_factorization_full_task_artifacts_include_stable_attempt_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    result = run_factorization_paper_case(
        case=case,
        condition=_formal_exp5_condition(
            catalog_digest=f"sha256:{'3' * 64}",
            domain="factorization",
            difficulty=str(case["difficulty"]),
            approved_config=approved_config,
        ),
        output_root=tmp_path,
        transport=ScriptedFactorizationRangeTransport(),
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    task_keys = [
        (ref["artifact_id"], ref["content_hash"])
        for ref in result.task_result.artifact_refs
    ]
    assert len(task_keys) == len(set(task_keys))
    task_key_set = set(task_keys)
    for attempt in result.attempt_results:
        required_refs = (
            attempt.request_ref,
            attempt.raw_output_ref,
            attempt.parsed_output_ref,
            attempt.provenance_ref,
            attempt.usage_ref,
            attempt.model_execution_record_ref,
        )
        assert all(ref is not None for ref in required_refs)
        assert {
            (ref["artifact_id"], ref["content_hash"])
            for ref in required_refs
            if ref is not None
        }.issubset(task_key_set)


def test_fixed_identity_is_request_policy_and_mismatch_is_fatal_submission(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=f"sha256:{'2' * 64}",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        approved_config=approved_config,
    )
    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=_ResolvedModelMismatchFactorizationTransport(
            resolved_model="gpt-5.6-sol-versioned"
        ),
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    attempt = result.attempt_results[0]
    request = _read_artifact(result.output_root, attempt.request_ref)
    assert request["hard_requirements"] == {
        "executor": "ai_api",
        "provider_family": "openai",
        "provider_config_id": "openai",
        "source_provider_config_digest": approved_config.config_digest,
        "prepared_execution_config_digest": _prepare_config(
            approved_config,
            entry_id="gpt-entry",
            max_tokens=512,
            timeout_seconds=30,
        ).config_digest,
        "selected_entry_id": "gpt-entry",
        "provider_model_id": "gpt-5.6-sol",
        "reasoning_profile_id": "high",
        "model_endpoint_identity_digest": condition.model_endpoint_identity_digest,
    }
    serialized_requirements = json.dumps(request["hard_requirements"], sort_keys=True)
    assert "api_key" not in serialized_requirements
    assert "prompt" not in serialized_requirements
    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    events = ledger.read_all()
    submission_event = next(
        event
        for event in events
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["request_id"] == request["request_id"]
    )
    submission = _read_artifact(
        result.output_root,
        submission_event.payload["submission_ref"],
    )
    assert submission["result_kind"] == "fatal_executor_error"
    assert submission["candidate_output_refs"] == {}
    assert not any(
        event.event_type == EventType.VERIFICATION_RECORDED
        and event.payload["unit_id"] == attempt.unit_id
        for event in events
    )
    assert not any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        and event.payload["unit_id"] == attempt.unit_id
        for event in events
    )
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"


def test_fixed_identity_request_mismatch_is_fatal_without_provider_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENSHARE_IDENTITY_TEST_KEY", "fake-identity-test-key")
    case = generate_factorization_paper_cases()[0]
    approved_config = _identity_config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    condition = _formal_exp5_condition(
        catalog_digest=f"sha256:{'4' * 64}",
        domain="factorization",
        difficulty=str(case["difficulty"]),
        approved_config=approved_config,
    )
    transport = ScriptedFactorizationRangeTransport()
    executor_type = factorization_paper_adapter_module._FixedIdentityRangeExecutor
    original_init = executor_type.__init__

    def initialize_with_drifted_requirement(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.executor_requirements = {
            **self.executor_requirements,
            "provider_model_id": "wrong-model",
        }

    monkeypatch.setattr(
        executor_type,
        "__init__",
        initialize_with_drifted_requirement,
    )

    result = run_factorization_paper_case(
        case=case,
        condition=condition,
        output_root=tmp_path,
        transport=transport,
        real_transport=False,
        ai_api_config=approved_config,
        entry_id="gpt-entry",
    )

    assert transport.calls == []
    assert result.run_evidence["protocol_runtime"]["status"] == "failed"
    assert result.task_result.root_status == PaperTaskStatus.FAILED
    assert result.attempt_results
    assert all(
        attempt.attempt_status == PaperAttemptStatus.PROVIDER_ERROR
        and attempt.error_kind
        in {"executor_requirement_mismatch", "missing_submission_event"}
        and attempt.provenance_ref is None
        and attempt.model_execution_record_ref is None
        and attempt.paper_eligible is False
        for attempt in result.attempt_results
    )
    assert {
        attempt.error_kind for attempt in result.attempt_results
    } == {"executor_requirement_mismatch", "missing_submission_event"}
    ledger = EventLedger(
        Path(result.output_root)
        / "events"
        / f"paper_factorization_{case['case_id']}.jsonl"
    )
    submission_events = [
        event
        for event in ledger.read_all()
        if event.event_type == EventType.EXECUTION_SUBMISSION_RECORDED
        and event.payload["unit_id"] in {
            attempt.unit_id for attempt in result.attempt_results
        }
    ]
    assert len(submission_events) == 1
    for event in submission_events:
        submission = _read_artifact(
            result.output_root,
            event.payload["submission_ref"],
        )
        assert submission["result_kind"] == "fatal_executor_error"
        assert submission["candidate_output_refs"] == {}
        assert submission["provenance_ref"] is None
