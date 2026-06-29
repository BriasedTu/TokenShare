import json

from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.plugins.factorization.validator import parse_factorization_ai_output
from tokenshare.storage.artifacts import ArtifactStore


def _success_response(content: str):
    return FakeProviderResponse(
        status_code=200,
        body={
            "id": "sf-response-parser",
            "model": "deepseek-ai/DeepSeek-V3",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
        },
    )


def test_parse_failure_does_not_failover_after_provider_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("not-json")])

    def parser(_raw: str):
        raise ValueError("plugin parser rejected output")

    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
    )

    submission = executor.execute(
        request,
        submission_id="submission_parse_fail",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "parse_failed"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.parse_failure_ref is not None
    assert len(transport.calls) == 1
    assert b"plugin parser rejected output" in store.read_bytes(submission.parse_failure_ref)


def test_raw_only_mode_returns_succeeded_without_candidate_refs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("plain text answer")])
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=None,
    )

    submission = executor.execute(
        request,
        submission_id="submission_raw_only",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is None
    assert submission.candidate_output_refs == {}
    assert submission.parse_failure_ref is None


def test_parser_bridge_persists_plugin_owned_parse_result_candidate_outputs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_factor_parser")
    config = load_ai_api_config(make_config_dict())
    raw_body = {
        "schema_version": "factorization.range_result.v1",
        "range_result_id": "range_result:unit_2:attempt_1:coverage_1:0",
        "result_kind": "found_factor",
        "target_n": "91",
        "range_start": "7",
        "range_end": "9",
        "coverage_id": "coverage_1",
        "child_index": 0,
        "partition_params_digest": "sha256:params",
        "found_factor": "7",
        "cofactor": "13",
        "checked_divisor_count": 3,
        "executor_summary": {"checked": "bounded range"},
        "created_at": "2026-06-28T00:00:02Z",
    }
    transport = FakeSiliconFlowTransport([_success_response(json.dumps(raw_body))])

    def parser(raw: str, *, raw_output_ref_summary, created_at: str):
        return parse_factorization_ai_output(
            raw,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
    )

    submission = executor.execute(
        request,
        submission_id="submission_factor_parser",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.parsed_output_ref is not None
    assert set(submission.candidate_output_refs) == {"range_result"}
    assert submission.candidate_output_refs["range_result"] != submission.parsed_output_ref
    parsed_body = json.loads(store.read_bytes(submission.parsed_output_ref).decode("utf-8"))
    candidate_body = json.loads(
        store.read_bytes(submission.candidate_output_refs["range_result"]).decode("utf-8")
    )
    assert parsed_body == raw_body
    assert candidate_body == raw_body


def test_parser_bridge_maps_plugin_owned_parse_failure_to_parse_failed(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_factor_parse_failure")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport([_success_response("I found factor 13")])

    def parser(raw: str, *, raw_output_ref_summary, created_at: str):
        return parse_factorization_ai_output(
            raw,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parser,
    )

    submission = executor.execute(
        request,
        submission_id="submission_factor_parse_failure",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "parse_failed"
    assert submission.parsed_output_ref is None
    assert submission.candidate_output_refs == {}
    assert submission.parse_failure_ref is not None
    failure_body = json.loads(store.read_bytes(submission.parse_failure_ref).decode("utf-8"))
    assert failure_body["schema_version"] == "phase3.parse_failure_report.v1"
    assert failure_body["failure_kind"] == "invalid_json_object"
