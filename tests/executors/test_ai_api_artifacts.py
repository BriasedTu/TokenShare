import importlib
import importlib.util

import pytest


def _artifacts_module():
    module_name = "tokenshare.executors.ai_api_artifacts"
    assert importlib.util.find_spec(module_name) is not None
    return importlib.import_module(module_name)


def test_v2_raw_model_identity_keeps_configured_requested_and_resolved_separate() -> None:
    artifacts = _artifacts_module()

    evidence = artifacts.read_raw_model_identity_evidence(
        {
            "schema_version": "phase7.raw_model_output.v2",
            "configured_model": "gpt-5.6-sol",
            "requested_model": "gpt-5.6-sol",
            "resolved_model": "gpt-5.6-sol-2026-07-01",
            "response_model_status": "present",
            "raw_response_json": {"model": "gpt-5.6-sol-2026-07-01"},
        }
    )

    assert evidence.configured_model == "gpt-5.6-sol"
    assert evidence.requested_model == "gpt-5.6-sol"
    assert evidence.resolved_model == "gpt-5.6-sol-2026-07-01"
    assert evidence.response_model_status == "present"


def test_v1_raw_outer_model_is_never_promoted_to_resolved_response_fact() -> None:
    artifacts = _artifacts_module()

    evidence = artifacts.read_raw_model_identity_evidence(
        {
            "schema_version": "phase7.raw_model_output.v1",
            "model": "gpt-5.6-sol",
            "raw_response_json": {
                "id": "response-without-model",
                "choices": [{"message": {"content": "ok"}}],
            },
        }
    )

    assert evidence.configured_model is None
    assert evidence.requested_model is None
    assert evidence.resolved_model is None
    assert evidence.response_model_status == "missing"


def test_v1_raw_uses_only_nested_provider_response_model_when_present() -> None:
    artifacts = _artifacts_module()

    evidence = artifacts.read_raw_model_identity_evidence(
        {
            "schema_version": "phase7.raw_model_output.v1",
            "model": "configured-fallback-must-be-ignored",
            "raw_response_json": {"model": "provider-observed-model"},
        }
    )

    assert evidence.resolved_model == "provider-observed-model"
    assert evidence.response_model_status == "present"


@pytest.mark.parametrize("missing_field", ["configured_model", "requested_model"])
def test_v2_raw_rejects_missing_request_side_identity_field(missing_field: str) -> None:
    artifacts = _artifacts_module()
    body = {
        "schema_version": "phase7.raw_model_output.v2",
        "configured_model": "gpt-5.6-sol",
        "requested_model": "gpt-5.6-sol",
        "resolved_model": "gpt-5.6-sol",
        "response_model_status": "present",
        "raw_response_json": {"model": "gpt-5.6-sol"},
    }
    body.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        artifacts.read_raw_model_identity_evidence(body)


def test_v2_raw_rejects_resolved_model_inconsistent_with_raw_response() -> None:
    artifacts = _artifacts_module()

    with pytest.raises(ValueError, match="inconsistent"):
        artifacts.read_raw_model_identity_evidence(
            {
                "schema_version": "phase7.raw_model_output.v2",
                "configured_model": "gpt-5.6-sol",
                "requested_model": "gpt-5.6-sol",
                "resolved_model": "configured-fallback",
                "response_model_status": "present",
                "raw_response_json": {"model": "provider-observed-model"},
            }
        )


def test_v2_raw_requires_original_response_object() -> None:
    artifacts = _artifacts_module()

    with pytest.raises(ValueError, match="raw_response_json"):
        artifacts.read_raw_model_identity_evidence(
            {
                "schema_version": "phase7.raw_model_output.v2",
                "configured_model": "gpt-5.6-sol",
                "requested_model": "gpt-5.6-sol",
                "resolved_model": None,
                "response_model_status": "missing",
                "raw_response_json": None,
            }
        )
