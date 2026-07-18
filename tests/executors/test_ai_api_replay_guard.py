from dataclasses import replace

import pytest

from tokenshare.executors import ai_api_replay
from tests.phase7_fixtures import (
    FakeProviderResponse,
    FakeSiliconFlowTransport,
    make_ai_request,
    make_config_dict,
)
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import load_ai_api_config
from tokenshare.executors.ai_api_replay import verify_ai_api_submission_artifacts
from tokenshare.storage.artifacts import ArtifactStore


def test_replay_guard_verifies_artifacts_without_calling_transport(tmp_path, monkeypatch) -> None:
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
                    "id": "sf-replay",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"ok"}'}}],
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
        parser=lambda raw: {"answer": "ok"},
    )
    submission = executor.execute(
        request,
        submission_id="submission_replay",
        submitted_at="2026-06-28T00:00:02Z",
    )
    transport.calls.clear()

    assert verify_ai_api_submission_artifacts(store, submission) is True
    assert transport.calls == []


def test_replay_guard_fails_on_missing_raw_artifact(tmp_path, monkeypatch) -> None:
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
                    "id": "sf-replay-missing",
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "choices": [{"message": {"content": '{"answer":"ok"}'}}],
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
        parser=lambda raw: {"answer": "ok"},
    )
    submission = executor.execute(
        request,
        submission_id="submission_replay_missing",
        submitted_at="2026-06-28T00:00:02Z",
    )
    assert submission.raw_output_ref is not None
    (store.root_path / submission.raw_output_ref.uri).unlink()

    with pytest.raises(FileNotFoundError, match="missing AI API artifact"):
        verify_ai_api_submission_artifacts(store, submission)


def test_replay_reads_persisted_v2_response_identity_without_config_or_transport_call(
    tmp_path,
    monkeypatch,
) -> None:
    reader = getattr(ai_api_replay, "read_ai_api_submission_model_identity", None)
    assert reader is not None
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_replay_missing_model")
    config_body = make_config_dict()
    config_body["defaults"]["max_provider_attempts"] = 1
    config_body["entries"] = [config_body["entries"][0]]
    config = load_ai_api_config(config_body)
    transport = FakeSiliconFlowTransport(
        [
            FakeProviderResponse(
                status_code=200,
                body={
                    "id": "sf-replay-without-model",
                    "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
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
        parser=lambda raw: {"answer": "ok"},
    ).execute(
        request,
        submission_id="submission_replay_missing_model",
        submitted_at="2026-07-17T00:00:00Z",
    )
    transport.calls.clear()

    evidence = reader(store, submission)

    assert evidence.configured_model == config.entries[0].model
    assert evidence.requested_model == config.entries[0].model
    assert evidence.resolved_model is None
    assert evidence.response_model_status == "missing"
    assert transport.calls == []


def test_replay_model_identity_rejects_raw_ref_schema_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY_A", "secret-a")
    monkeypatch.setenv("SILICONFLOW_API_KEY_B", "secret-b")
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_replay_bad_raw_ref")
    config_body = make_config_dict()
    config_body["defaults"]["max_provider_attempts"] = 1
    config_body["entries"] = [config_body["entries"][0]]
    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(config_body),
        transport=FakeSiliconFlowTransport(
            [
                FakeProviderResponse(
                    status_code=200,
                    body={
                        "id": "sf-replay-bad-raw-ref",
                        "model": config_body["entries"][0]["model"],
                        "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                    },
                )
            ]
        ),
        parser=lambda raw: {"answer": "ok"},
    ).execute(
        request,
        submission_id="submission_replay_bad_raw_ref",
        submitted_at="2026-07-17T00:00:00Z",
    )
    tampered_submission = replace(
        submission,
        raw_output_ref=replace(
            submission.raw_output_ref,
            artifact_schema_version="v1",
        ),
    )

    with pytest.raises(ValueError, match="RawModelOutput artifact ref"):
        ai_api_replay.read_ai_api_submission_model_identity(
            store,
            tampered_submission,
        )
