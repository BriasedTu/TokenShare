import json
from dataclasses import replace
from hashlib import sha256

import pytest

import tokenshare.executors.ai_api as ai_api_module
from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.storage.artifacts import ArtifactStore


def parse_answer(raw_text: str):
    return {"answer": json.loads(raw_text)["answer"]}


def _request_for_provider(request, provider_family: str):
    return replace(
        request,
        capability_snapshot={"executor": "ai_api", "provider_family": provider_family},
        hard_requirements={"executor": "ai_api", "provider_family": provider_family},
    )


def _effective_controls_digest(body: dict) -> str:
    controls = {
        key: value
        for key, value in body.items()
        if key not in {"messages", "model"}
    }
    encoded = json.dumps(
        controls,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _openai_config_dict():
    body = make_config_dict()
    body["provider_family"] = "openai"
    body["entries"] = [
        {
            "entry_id": "openai_gpt_5_6_sol_high",
            "enabled": True,
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-5.6-sol",
            "endpoint": "/chat/completions",
            "supports_json_mode": True,
            "supports_streaming": False,
            "request_overrides": {"reasoning_effort": "high", "temperature": 0.0},
            "pricing": {
                "currency": "USD",
                "input_per_million_tokens": 0.0,
                "output_per_million_tokens": 0.0,
            },
            "tags": ["paper", "openai", "reasoning:high"],
        }
    ]
    return body


def test_ai_api_executor_persists_raw_parsed_provenance_usage_and_cost(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store)
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-response-1",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [
                        {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id="submission_ai_1",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is not None
    assert submission.provenance_ref is not None
    assert submission.candidate_output_refs["answer"] == submission.parsed_output_ref
    assert submission.usage_summary["total_tokens"] == 15
    assert submission.usage_summary["provider_attempt_count"] == 1
    assert submission.usage_summary["cost_estimate_status"] == "estimated"
    assert store.verify(submission.raw_output_ref)
    assert store.verify(submission.parsed_output_ref)
    assert store.verify(submission.provenance_ref)
    assert b"secret-a" not in store.read_bytes(submission.provenance_ref)
    assert b"secret-b" not in store.read_bytes(submission.provenance_ref)


def test_ai_api_executor_uses_openai_provider_family_in_request_and_provenance(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    store = ArtifactStore(tmp_path)
    request = _request_for_provider(
        make_ai_request(store, request_id="request_openai_success"),
        "openai",
    )
    config = load_ai_api_config(_openai_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "chatcmpl-openai-1",
                    "model": "gpt-5.6-sol-2026-07-01",
                    "choices": [
                        {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id="submission_openai_success",
        submitted_at="2026-07-16T00:00:02Z",
    )

    raw = json.loads(store.read_bytes(submission.raw_output_ref).decode("utf-8"))
    provenance = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))

    assert submission.result_kind == "succeeded"
    assert transport.calls[0]["body"]["reasoning_effort"] == "high"
    assert "enable_thinking" not in transport.calls[0]["body"]
    assert submission.environment_summary["provider_family"] == "openai"
    assert submission.usage_summary["provider_family"] == "openai"
    assert raw["provider_family"] == "openai"
    assert raw["entry_id"] == "openai_gpt_5_6_sol_high"
    assert raw["schema_version"] == "phase7.raw_model_output.v2"
    assert raw["configured_model"] == "gpt-5.6-sol"
    assert raw["requested_model"] == "gpt-5.6-sol"
    assert raw["resolved_model"] == "gpt-5.6-sol-2026-07-01"
    assert raw["response_model_status"] == "present"
    assert "model" not in raw
    assert provenance["provider_family"] == "openai"
    assert provenance["schema_version"] == "phase7.ai_provider_call_provenance.v2"
    assert submission.provenance_ref.artifact_schema_version == "v2"
    assert provenance["attempts"][0]["provider_family"] == "openai"
    provider_request_identity = provenance["attempts"][0]["provider_request_identity"]
    assert provider_request_identity == {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": "openai",
        "entry_id": "openai_gpt_5_6_sol_high",
        "configured_model": "gpt-5.6-sol",
        "requested_model": "gpt-5.6-sol",
        "reasoning_controls": {"reasoning_effort": "high"},
        "effective_request_controls_digest": _effective_controls_digest(
            transport.calls[0]["body"]
        ),
    }
    assert "messages" not in provider_request_identity
    assert "Authorization" not in provider_request_identity
    assert b"openai-secret" not in store.read_bytes(submission.provenance_ref)


@pytest.mark.parametrize("provider_family", ["siliconflow", "openai"])
@pytest.mark.parametrize(
    ("response_model", "include_model", "expected_resolved", "expected_status"),
    [
        ("approved-model", True, "approved-model", "present"),
        ("different-model", True, "different-model", "present"),
        (None, False, None, "missing"),
        (None, True, None, "null"),
        ("", True, None, "empty"),
        (["not", "a", "model"], True, None, "invalid_type"),
    ],
)
def test_ai_api_executor_v2_raw_output_never_backfills_response_model(
    tmp_path,
    monkeypatch,
    provider_family: str,
    response_model,
    include_model: bool,
    expected_resolved: str | None,
    expected_status: str,
) -> None:
    api_key_env = "OPENAI_API_KEY" if provider_family == "openai" else "SILICONFLOW_API_KEY_A"
    monkeypatch.setenv(api_key_env, "executor-v2-test-secret")
    config_body = _openai_config_dict() if provider_family == "openai" else make_config_dict()
    config_body["defaults"]["max_provider_attempts"] = 1
    config_body["entries"] = [
        {
            **config_body["entries"][0],
            "api_key_env": api_key_env,
            "model": "approved-model",
        }
    ]
    config = load_ai_api_config(config_body)
    store = ArtifactStore(tmp_path)
    request = _request_for_provider(
        make_ai_request(store, request_id=f"request_{provider_family}_v2_model"),
        provider_family,
    )
    response_body = {
        "id": f"{provider_family}-response-model-shape",
        "choices": [
            {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    if include_model:
        response_body["model"] = response_model
    transport = FakeSiliconFlowTransport(
        [FakeProviderResponse(status_code=200, body=response_body)]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id=f"submission_{provider_family}_v2_model",
        submitted_at="2026-07-17T00:00:00Z",
    )
    raw = json.loads(store.read_bytes(submission.raw_output_ref).decode("utf-8"))
    provenance = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))

    assert raw["configured_model"] == "approved-model"
    assert raw["requested_model"] == "approved-model"
    assert raw["resolved_model"] == expected_resolved
    assert raw["response_model_status"] == expected_status
    assert raw["raw_response_json"] == response_body
    assert "model" not in raw
    request_identity = provenance["attempts"][0]["provider_request_identity"]
    assert request_identity["configured_model"] == "approved-model"
    assert request_identity["requested_model"] == "approved-model"
    assert "model" not in request_identity
    assert b"executor-v2-test-secret" not in store.read_bytes(submission.raw_output_ref)
    assert b"executor-v2-test-secret" not in store.read_bytes(submission.provenance_ref)


def test_ai_api_executor_requested_model_fields_come_from_built_request_body(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "requested-model-source-secret")
    config_body = make_config_dict()
    config_body["defaults"]["max_provider_attempts"] = 1
    config_body["entries"] = [
        {
            **config_body["entries"][0],
            "model": "configured-model",
        }
    ]
    config = load_ai_api_config(config_body)
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_model_source_boundary")
    original_builder, parser = ai_api_module._provider_adapter("siliconflow")

    def build_body_with_distinct_request_model(**kwargs):
        body = original_builder(**kwargs)
        body["model"] = "actual-request-model"
        return body

    monkeypatch.setattr(
        ai_api_module,
        "_provider_adapter",
        lambda _provider_family: (build_body_with_distinct_request_model, parser),
    )
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "response-model-source-boundary",
                    "model": "actual-request-model",
                    "choices": [
                        {
                            "message": {"content": '{"answer":"ok"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        ]
    )
    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    ).execute(
        request,
        submission_id="submission_model_source_boundary",
        submitted_at="2026-07-17T00:00:00Z",
    )
    raw = json.loads(store.read_bytes(submission.raw_output_ref).decode("utf-8"))
    provenance = json.loads(
        store.read_bytes(submission.provenance_ref).decode("utf-8")
    )

    assert raw["configured_model"] == "configured-model"
    assert raw["requested_model"] == "actual-request-model"
    assert submission.usage_summary["configured_model"] == "configured-model"
    assert submission.usage_summary["requested_model"] == "actual-request-model"
    request_identity = provenance["attempts"][0]["provider_request_identity"]
    assert request_identity["configured_model"] == "configured-model"
    assert request_identity["requested_model"] == "actual-request-model"


def test_ai_api_executor_provenance_records_effective_siliconflow_reasoning_control(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_siliconflow_reasoning_identity")
    config_body = make_config_dict()
    config_body["entries"] = [
        {
            **config_body["entries"][0],
            "model": "Qwen/Qwen3.6-27B",
            "request_overrides": {"temperature": 0.0},
            "tags": ["json_mode", "reasoning"],
        }
    ]
    config_body["defaults"]["max_provider_attempts"] = 1
    config = load_ai_api_config(config_body)
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-reasoning-identity",
                    "model": "Qwen/Qwen3.6-27B",
                    "choices": [
                        {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    submission = executor.execute(
        request,
        submission_id="submission_siliconflow_reasoning_identity",
        submitted_at="2026-07-16T00:00:02Z",
    )

    provenance = json.loads(store.read_bytes(submission.provenance_ref).decode("utf-8"))
    provider_request_identity = provenance["attempts"][0]["provider_request_identity"]
    assert provider_request_identity["provider_family"] == "siliconflow"
    assert provider_request_identity["entry_id"] == config.entries[0].entry_id
    assert provider_request_identity["configured_model"] == "Qwen/Qwen3.6-27B"
    assert provider_request_identity["requested_model"] == "Qwen/Qwen3.6-27B"
    assert provider_request_identity["reasoning_controls"] == {"enable_thinking": False}
    assert provider_request_identity["effective_request_controls_digest"] == (
        _effective_controls_digest(transport.calls[0]["body"])
    )
    assert "messages" not in provider_request_identity
    assert b"secret-a" not in store.read_bytes(submission.provenance_ref)


def test_ai_api_executor_sends_full_prompt_package_context_to_provider(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_prompt_context")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-prompt-context",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [
                        {"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=parse_answer,
    )

    executor.execute(
        request,
        submission_id="submission_prompt_context",
        submitted_at="2026-06-28T00:00:02Z",
    )

    sent_prompt = transport.calls[0]["body"]["messages"][-1]["content"]
    assert "Authoritative PromptPackage input_summary" in sent_prompt
    assert '"question": "demo"' in sent_prompt
    assert "Authoritative PromptPackage output_schema" in sent_prompt
    assert '"required": [' in sent_prompt
    assert "Authoritative PromptPackage constraints" in sent_prompt
    assert '"requires_json_mode": true' in sent_prompt


def test_ai_api_executor_never_persists_api_key_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "super-secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "super-secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_secret_scan")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-secret-scan",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"safe"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "safe"},
    )

    executor.execute(
        request,
        submission_id="submission_secret_scan",
        submitted_at="2026-06-28T00:00:02Z",
    )

    for path in store.artifact_dir.glob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"super-secret-a" not in data
            assert b"super-secret-b" not in data


def test_ai_api_executor_marks_successful_response_missing_usage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_missing_usage")
    config = load_ai_api_config(make_config_dict())
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-missing-usage",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"safe"}'}}],
                },
            )
        ]
    )
    executor = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=config,
        transport=transport,
        parser=lambda raw: {"answer": "safe"},
    )

    submission = executor.execute(
        request,
        submission_id="submission_missing_usage",
        submitted_at="2026-06-28T00:00:02Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.usage_summary["cost_estimate_status"] == "usage_missing"
    assert submission.usage_summary["prompt_tokens"] is None
    assert submission.usage_summary["completion_tokens"] is None
    assert submission.usage_summary["total_tokens"] is None
    assert submission.usage_summary["cost_estimate"] is None
