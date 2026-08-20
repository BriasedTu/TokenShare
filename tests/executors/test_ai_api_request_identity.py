from __future__ import annotations

from dataclasses import replace
import base64
from hashlib import sha256
import json

import pytest

from tests.phase7_fixtures import FakeSiliconFlowTransport, make_ai_request, make_config_dict
from tokenshare.executors.ai_api import AIAPIExecutor
from tokenshare.executors.ai_api_config import AIAPIProviderEntry, load_ai_api_config

from tokenshare.executors.ai_api_request_identity import (
    PROMPT_ADMISSION_PROFILE_DIGEST,
    PROMPT_ADMISSION_PROFILE_ID,
    PreparedOutboundRequest,
    PreparedOutboundRequestFactory,
    validate_prepared_request,
)
from tokenshare.storage.artifacts import ArtifactStore


def _prepare(**overrides):
    values = {
        "body_obj": {
            "model": "模型-甲",
            "messages": [
                {"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": "证明 1 = 1"},
            ],
            "temperature": 0.0,
        },
        "base_url": "https://api.example.test/v1/",
        "endpoint": "/chat/completions",
        "provider_config_digest": "sha256:config",
        "entry_id": "entry_a",
        "configured_model": "模型-甲",
        "effective_controls_digest": "sha256:controls",
        "plugin_id": "lean_proof",
        "plugin_version": "2.0.0",
        "prompt_profile_id": "lean_proof.proof_candidate_prompt.v2",
        "prompt_serialization_schema": "phase3.prompt_package.v1",
        "body_serialization_schema": "openai.chat_completions.v1",
        "case_id": "case_001",
        "planned_ai_unit_id": "lemma_A",
        "sample_slot_index": 0,
        "replacement_slot": 0,
    }
    values.update(overrides)
    return PreparedOutboundRequestFactory.prepare(**values)


def test_artifact_digest_and_wire_share_same_prepared_bytes_object() -> None:
    prepared = _prepare()

    assert prepared.body_bytes == (
        b'{"messages":[{"content":"\xe5\x8f\xaa\xe8\xbe\x93\xe5\x87\xba JSON\xe3\x80\x82","role":"system"},'
        b'{"content":"\xe8\xaf\x81\xe6\x98\x8e 1 = 1","role":"user"}],"model":"\xe6\xa8\xa1\xe5\x9e\x8b-\xe7\x94\xb2","temperature":0.0}'
    )
    assert prepared.body_digest == f"sha256:{sha256(prepared.body_bytes).hexdigest()}"
    assert validate_prepared_request(prepared) is prepared
    assert prepared.normalized_absolute_endpoint == "https://api.example.test/v1/chat/completions"


def test_prepared_outbound_request_persistence_is_strict_and_canonical() -> None:
    prepared = _prepare()

    persisted = prepared.to_dict()

    assert PreparedOutboundRequest.from_dict(persisted) == prepared
    assert persisted == prepared.to_dict()
    with pytest.raises(ValueError, match="fields"):
        PreparedOutboundRequest.from_dict({**persisted, "unexpected": True})
    with pytest.raises(ValueError, match="sample_slot_index"):
        PreparedOutboundRequest.from_dict(
            {**persisted, "sample_slot_index": True}
        )
    with pytest.raises(ValueError, match="body bytes"):
        PreparedOutboundRequest.from_dict(
            {**persisted, "body_bytes_base64": persisted["body_bytes_base64"] + "="}
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("body_obj", {"model": "drift", "messages": []}, "body bytes"),
        (
            "body_bytes_base64",
            base64.b64encode(b"{}").decode("ascii"),
            "body bytes",
        ),
        ("inference_request_digest", "sha256:" + "0" * 64, "inference"),
    ),
)
def test_prepared_outbound_request_persistence_rejects_identity_tamper(
    field: str,
    value: object,
    message: str,
) -> None:
    persisted = _prepare().to_dict()

    with pytest.raises(ValueError, match=message):
        PreparedOutboundRequest.from_dict({**persisted, field: value})


def test_body_obj_bytes_digest_recompute_mismatch_fails_closed() -> None:
    prepared = _prepare()

    with pytest.raises(ValueError, match="body bytes"):
        validate_prepared_request(replace(prepared, body_obj={**prepared.body_obj, "model": "drift"}))
    with pytest.raises(ValueError, match="body digest"):
        validate_prepared_request(replace(prepared, body_digest="sha256:drift"))


def test_prompt_admission_profile_fields_unicode_and_limit_are_frozen() -> None:
    prepared = _prepare()

    assert prepared.prompt_admission_profile_id == PROMPT_ADMISSION_PROFILE_ID
    assert prepared.prompt_admission_profile_version == 1
    assert prepared.prompt_admission_profile_digest == PROMPT_ADMISSION_PROFILE_DIGEST
    assert prepared.estimated_prompt_tokens == len(prepared.body_bytes) + 8 * 2 + 16

    with pytest.raises(ValueError, match="prompt admission"):
        _prepare(
            body_obj={
                "model": "模型-甲",
                "messages": [{"role": "user", "content": "界" * 11_000}],
            }
        )


def test_volatile_execution_fields_do_not_change_stable_inference_identity() -> None:
    first = _prepare()
    second = _prepare()

    assert first.inference_request_digest == second.inference_request_digest
    assert "request" not in first.inference_request_digest


def test_prompt_over_limit_blocks_before_secret_resolution_or_transport(
    tmp_path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path)
    request = make_ai_request(store, request_id="request_oversized_prompt")
    prompt = json.loads(store.read_bytes(request.prompt_package_ref).decode("utf-8"))
    prompt["prompt_text"] = "界" * 11_000
    prompt_ref = store.save_json(
        prompt,
        artifact_id="prompt_oversized",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "task4_test"},
        metadata={},
        created_at=prompt["created_at"],
    )
    request = replace(request, prompt_package_ref=prompt_ref)
    transport = FakeSiliconFlowTransport([])
    secret_reads = 0

    def reject_secret_read(_entry) -> str:
        nonlocal secret_reads
        secret_reads += 1
        raise AssertionError("secret must not be read before prompt admission")

    monkeypatch.setattr(AIAPIProviderEntry, "resolve_api_key", reject_secret_read)
    submission = AIAPIExecutor(
        executor_id="executor_ai_api",
        executor_version="0.1.0",
        artifact_store=store,
        config=load_ai_api_config(make_config_dict()),
        transport=transport,
    ).execute(
        request,
        submission_id="submission_oversized_prompt",
        submitted_at="2026-08-02T00:00:00Z",
    )

    assert submission.result_kind == "executor_error"
    assert secret_reads == 0
    assert transport.calls == []
