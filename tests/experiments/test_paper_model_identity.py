import importlib
from types import ModuleType

import pytest

from tokenshare.executors.ai_api_config import (
    AIAPIExecutorConfig,
    load_ai_api_config,
)


COHORT_ID = "tokenshare.paper.model_endpoint_cohort.v1"
COHORT_DIGEST = "sha256:" + "1" * 64
COHORT_MEMBER_ID = "gpt_5_6_sol_high_openai"


def test_approved_config_builds_validated_binding_and_separates_source_digest() -> None:
    identity = _identity_module()
    approved_config = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        metadata={"approval": "first"},
    )
    same_endpoint_changed_source = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        metadata={"approval": "second"},
    )

    expected = _approved_openai_identity(identity, source_config=approved_config)
    same_endpoint = _approved_openai_identity(
        identity,
        source_config=same_endpoint_changed_source,
    )
    binding = identity.validate_fixed_entry_config_identity(
        expected_identity=expected,
        provider_config_id="openai",
        source_config=approved_config,
    )

    assert isinstance(expected, identity.PaperModelEndpointIdentity)
    assert isinstance(binding, identity.ValidatedModelEndpointBinding)
    assert binding.identity == expected
    assert binding.selected_entry.entry_id == "gpt-entry"
    assert expected.source_provider_config_digest == approved_config.config_digest
    assert expected.model_endpoint_identity_digest.startswith("sha256:")
    assert (
        expected.model_endpoint_identity_digest
        == same_endpoint.model_endpoint_identity_digest
    )
    assert (
        expected.source_provider_config_digest
        != same_endpoint.source_provider_config_digest
    )


def test_same_entry_id_in_different_provider_configs_is_not_the_same_identity() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)
    same_entry_id_in_wrong_namespace = _config(
        provider_family="siliconflow",
        model="Qwen/Qwen3.6-27B",
    )

    with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
        identity.validate_fixed_entry_config_identity(
            expected_identity=expected,
            provider_config_id="siliconflow",
            source_config=same_entry_id_in_wrong_namespace,
        )

    assert "provider_config_id_mismatch" in exc.value.reasons
    assert "provider_family_mismatch" in exc.value.reasons


def test_fixed_entry_identity_rejects_same_entry_id_with_different_model() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)
    wrong_model = _config(
        provider_family="openai",
        model="gpt-5.6-sol-wrong",
        reasoning_effort="high",
    )

    with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
        identity.validate_fixed_entry_config_identity(
            expected_identity=expected,
            provider_config_id="openai",
            source_config=wrong_model,
        )

    assert "provider_model_id_mismatch" in exc.value.reasons


def test_reasoning_identity_treats_missing_as_default_and_rejects_empty_string() -> None:
    identity = _identity_module()

    missing = identity.normalize_reasoning_identity(
        provider_family="openai",
        request_overrides={},
    )
    explicit_none = identity.normalize_reasoning_identity(
        provider_family="openai",
        request_overrides={"reasoning_effort": None},
    )
    high = identity.normalize_reasoning_identity(
        provider_family="openai",
        request_overrides={"reasoning_effort": "high"},
    )

    assert missing.reasoning_profile_id == "default"
    assert explicit_none.reasoning_profile_id == "default"
    assert high.reasoning_profile_id == "high"
    for invalid_effort in ("", "   "):
        with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
            identity.normalize_reasoning_identity(
                provider_family="openai",
                request_overrides={"reasoning_effort": invalid_effort},
            )
        assert "invalid_reasoning_profile" in exc.value.reasons


def test_siliconflow_reasoning_identity_keeps_default_profile_and_thinking_control() -> None:
    identity = _identity_module()

    normalized = identity.normalize_reasoning_identity(
        provider_family="siliconflow",
        request_overrides={"enable_thinking": False},
    )

    assert normalized.reasoning_profile_id == "default"
    assert normalized.effective_controls == {"enable_thinking": False}
    with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
        identity.normalize_reasoning_identity(
            provider_family="siliconflow",
            request_overrides={"enable_thinking": "false"},
        )
    assert "invalid_reasoning_control" in exc.value.reasons


def test_fixed_entry_identity_rejects_reasoning_profile_drift() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)
    wrong_reasoning = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )

    with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
        identity.validate_fixed_entry_config_identity(
            expected_identity=expected,
            provider_config_id="openai",
            source_config=wrong_reasoning,
        )

    assert "reasoning_profile_id_mismatch" in exc.value.reasons


def test_retry_rejects_changed_source_config_digest_before_rebinding_entry() -> None:
    identity = _identity_module()
    approved_config = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        metadata={"approval": "original"},
    )
    expected = _approved_openai_identity(identity, source_config=approved_config)
    changed_config = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
        metadata={"approval": "changed-after-planning"},
    )

    assert changed_config.config_digest != approved_config.config_digest
    with pytest.raises(identity.PaperModelIdentityMismatch) as exc:
        identity.validate_fixed_entry_config_identity(
            expected_identity=expected,
            provider_config_id="openai",
            source_config=changed_config,
        )

    assert "source_provider_config_digest_mismatch" in exc.value.reasons


def test_replacement_attempt_reuses_original_endpoint_identity_object() -> None:
    identity = _identity_module()
    approved_config = _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    expected = _approved_openai_identity(
        identity,
        source_config=approved_config,
    )

    initial_binding = identity.validate_fixed_entry_config_identity(
        expected_identity=expected,
        provider_config_id="openai",
        source_config=approved_config,
    )
    replacement_binding = identity.validate_fixed_entry_config_identity(
        expected_identity=initial_binding.identity,
        provider_config_id="openai",
        source_config=approved_config,
    )

    assert initial_binding.identity is expected
    assert replacement_binding.identity is expected
    assert replacement_binding.selected_entry is initial_binding.selected_entry
    assert replacement_binding.selected_entry.entry_id == "gpt-entry"


def test_submission_identity_rejects_actual_openai_reasoning_drift() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record(
        identity,
        expected=expected,
        reasoning_controls={"reasoning_effort": "low"},
        resolved_model="gpt-5.6-sol",
    )

    assert record.identity_status == "model_identity_mismatch"
    assert "request_reasoning_profile_id_mismatch" in record.mismatch_reasons
    assert "request_reasoning_controls_mismatch" in record.mismatch_reasons
    assert record.paper_eligible is False


def test_submission_identity_normalizes_actual_openai_reasoning_case() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record(
        identity,
        expected=expected,
        reasoning_controls={"reasoning_effort": "HIGH"},
        resolved_model="gpt-5.6-sol",
    )

    assert record.identity_status == "matched"
    assert record.mismatch_reasons == ()
    assert record.actual_request_identities[0]["reasoning_controls"] == {
        "reasoning_effort": "HIGH"
    }


def test_submission_identity_records_implicit_siliconflow_thinking_disable_but_rejects_enable() -> None:
    identity = _identity_module()
    source_config = _config(
        provider_family="siliconflow",
        model="Qwen/Qwen3.6-27B",
    )
    expected = identity.build_model_endpoint_identity(
        model_cohort_id=COHORT_ID,
        model_cohort_digest=COHORT_DIGEST,
        cohort_member_id="qwen3_6_27b_siliconflow",
        provider_config_id="siliconflow",
        selected_entry_id="gpt-entry",
        expected_provider_family="siliconflow",
        expected_provider_model_id="Qwen/Qwen3.6-27B",
        expected_reasoning_profile_id="default",
        source_config=source_config,
    )

    implicit_disable = _submission_record(
        identity,
        expected=expected,
        reasoning_controls={"enable_thinking": False},
        resolved_model="Qwen/Qwen3.6-27B",
    )
    unexpected_enable = _submission_record(
        identity,
        expected=expected,
        reasoning_controls={"enable_thinking": True},
        resolved_model="Qwen/Qwen3.6-27B",
    )

    assert implicit_disable.identity_status == "matched"
    assert implicit_disable.actual_request_identities[0]["reasoning_controls"] == {
        "enable_thinking": False
    }
    assert unexpected_enable.identity_status == "model_identity_mismatch"
    assert "request_reasoning_controls_mismatch" in unexpected_enable.mismatch_reasons


def test_submission_identity_v2_matches_only_observed_response_model() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        response_model="gpt-5.6-sol",
    )

    assert record.schema_version == "tokenshare.paper_model_execution_record.v2"
    assert record.identity_status == "matched"
    assert record.requested_model == "gpt-5.6-sol"
    assert record.resolved_model == "gpt-5.6-sol"
    assert record.response_model_status == "present"
    assert record.mismatch_reasons == ()
    assert record.paper_eligible is True


def test_submission_identity_v2_preserves_mismatching_actual_requested_model() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        requested_model="different-request-model",
        response_model="gpt-5.6-sol",
    )

    assert record.identity_status == "model_identity_mismatch"
    assert record.requested_model == "different-request-model"
    assert "request_identity_model_mismatch" in record.mismatch_reasons
    assert "raw_requested_model_mismatch" in record.mismatch_reasons


def test_submission_identity_rejects_unknown_provenance_wrapper_schema() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        response_model="gpt-5.6-sol",
        provenance_schema="phase7.ai_provider_call_provenance.v999",
    )

    assert record.identity_status == "model_identity_mismatch"
    assert "provider_provenance_schema_mismatch" in record.mismatch_reasons


@pytest.mark.parametrize(
    ("response_model", "include_model", "expected_status"),
    [
        (None, False, "missing"),
        (None, True, "null"),
        ("", True, "empty"),
        (123, True, "invalid_type"),
    ],
)
def test_submission_identity_v2_rejects_missing_or_invalid_resolved_model(
    response_model,
    include_model: bool,
    expected_status: str,
) -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        response_model=response_model,
        include_model=include_model,
    )

    assert record.identity_status == "model_identity_mismatch"
    assert record.resolved_model is None
    assert record.response_model_status == expected_status
    assert record.mismatch_reasons == ("missing_resolved_model",)
    assert record.paper_eligible is False


def test_submission_identity_parse_failed_response_without_model_is_identity_mismatch() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        response_model=None,
        include_model=False,
        final_result_kind="parse_failed",
    )

    assert record.identity_status == "model_identity_mismatch"
    assert record.mismatch_reasons == ("missing_resolved_model",)
    assert record.response_model_status == "missing"


def test_submission_identity_provider_error_without_raw_response_is_not_observed() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record_v2(
        identity,
        expected=expected,
        response_model=None,
        include_raw_output=False,
        final_result_kind="provider_error",
    )

    assert record.identity_status == "not_observed"
    assert record.resolved_model is None
    assert record.response_model_status == "unavailable"
    assert record.mismatch_reasons == ()
    assert record.paper_eligible is False


def test_submission_identity_v1_ignores_outer_config_fallback_when_response_model_missing() -> None:
    identity = _identity_module()
    expected = _approved_openai_identity(identity)

    record = _submission_record(
        identity,
        expected=expected,
        reasoning_controls={"reasoning_effort": "high"},
        resolved_model="gpt-5.6-sol",
        raw_response_json={"id": "legacy-response-without-model"},
    )

    assert record.identity_status == "model_identity_mismatch"
    assert record.resolved_model is None
    assert "missing_resolved_model" in record.mismatch_reasons
    assert record.paper_eligible is False


def _identity_module() -> ModuleType:
    return importlib.import_module("tokenshare.experiments.paper_model_identity")


def _approved_openai_identity(
    identity: ModuleType,
    *,
    source_config: AIAPIExecutorConfig | None = None,
):
    config = source_config or _config(
        provider_family="openai",
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    return identity.build_model_endpoint_identity(
        model_cohort_id=COHORT_ID,
        model_cohort_digest=COHORT_DIGEST,
        cohort_member_id=COHORT_MEMBER_ID,
        provider_config_id="openai",
        selected_entry_id="gpt-entry",
        expected_provider_family="openai",
        expected_provider_model_id="gpt-5.6-sol",
        expected_reasoning_profile_id="high",
        source_config=config,
    )


def _submission_record(
    identity: ModuleType,
    *,
    expected,
    reasoning_controls: dict,
    resolved_model: str,
    raw_response_json: dict | None = None,
):
    request_identity = {
        "schema_version": "phase7.provider_request_identity.v1",
        "provider_family": expected.provider_family,
        "entry_id": expected.selected_entry_id,
        "model": expected.provider_model_id,
        "reasoning_controls": reasoning_controls,
        "effective_request_controls_digest": "sha256:" + "2" * 64,
    }
    prepared_digest = "sha256:" + "3" * 64
    return identity.validate_fixed_entry_submission_identity(
        expected_identity=expected,
        prepared_execution_config_digest=prepared_digest,
        condition_id="exp5_test_condition",
        repeat_id=0,
        run_id="exp5_test_run",
        task_id="exp5_test_task",
        unit_id="exp5_test_unit",
        attempt_id="exp5_test_attempt",
        request={
            "hard_requirements": {
                "provider_family": expected.provider_family,
            },
            "capability_snapshot": {
                "provider_family": expected.provider_family,
            },
        },
        request_ref={"artifact_id": "request"},
        provenance={
            "schema_version": "phase7.ai_provider_call_provenance.v1",
            "provider_family": expected.provider_family,
            "config_digest": prepared_digest,
            "selection_record": {
                "eligible_entry_ids": [expected.selected_entry_id],
                "selected_entry_id": expected.selected_entry_id,
                "attempt_entry_ids": [expected.selected_entry_id],
            },
            "attempts": [
                {
                    "provider_family": expected.provider_family,
                    "entry_id": expected.selected_entry_id,
                    "model": expected.provider_model_id,
                    "result_kind": "succeeded",
                    "provider_request_identity": request_identity,
                }
            ],
            "final_entry_id": expected.selected_entry_id,
            "final_result_kind": "succeeded",
        },
        provenance_ref={"artifact_id": "provenance"},
        raw_output={
            "schema_version": "phase7.raw_model_output.v1",
            "provider_family": expected.provider_family,
            "entry_id": expected.selected_entry_id,
            "model": resolved_model,
            "raw_response_json": (
                raw_response_json
                if raw_response_json is not None
                else {"model": resolved_model}
            ),
        },
        raw_output_ref={"artifact_id": "raw"},
        usage_ref={"artifact_id": "usage"},
        created_at="2026-07-16T00:00:00Z",
    )


def _submission_record_v2(
    identity: ModuleType,
    *,
    expected,
    response_model,
    requested_model: str | None = None,
    include_model: bool = True,
    include_raw_output: bool = True,
    final_result_kind: str = "succeeded",
    provenance_schema: str = "phase7.ai_provider_call_provenance.v2",
):
    actual_requested_model = requested_model or expected.provider_model_id
    response_body = {"id": "provider-response-v2"}
    if include_model:
        response_body["model"] = response_model
    if "model" not in response_body:
        resolved_model = None
        response_model_status = "missing"
    elif response_model is None:
        resolved_model = None
        response_model_status = "null"
    elif not isinstance(response_model, str):
        resolved_model = None
        response_model_status = "invalid_type"
    elif not response_model.strip():
        resolved_model = None
        response_model_status = "empty"
    else:
        resolved_model = response_model
        response_model_status = "present"
    request_identity = {
        "schema_version": "phase7.provider_request_identity.v2",
        "provider_family": expected.provider_family,
        "entry_id": expected.selected_entry_id,
        "configured_model": expected.provider_model_id,
        "requested_model": actual_requested_model,
        "reasoning_controls": {"reasoning_effort": "high"},
        "effective_request_controls_digest": "sha256:" + "2" * 64,
    }
    prepared_digest = "sha256:" + "3" * 64
    raw_output = None
    raw_output_ref = None
    if include_raw_output:
        raw_output = {
            "schema_version": "phase7.raw_model_output.v2",
            "provider_family": expected.provider_family,
            "entry_id": expected.selected_entry_id,
            "configured_model": expected.provider_model_id,
            "requested_model": actual_requested_model,
            "resolved_model": resolved_model,
            "response_model_status": response_model_status,
            "raw_response_json": response_body,
        }
        raw_output_ref = {"artifact_id": "raw"}
    return identity.validate_fixed_entry_submission_identity(
        expected_identity=expected,
        prepared_execution_config_digest=prepared_digest,
        condition_id="exp5_test_condition",
        repeat_id=0,
        run_id="exp5_test_run",
        task_id="exp5_test_task",
        unit_id="exp5_test_unit",
        attempt_id="exp5_test_attempt",
        request={
            "hard_requirements": {"provider_family": expected.provider_family},
            "capability_snapshot": {"provider_family": expected.provider_family},
        },
        request_ref={"artifact_id": "request"},
        provenance={
            "schema_version": provenance_schema,
            "provider_family": expected.provider_family,
            "config_digest": prepared_digest,
            "selection_record": {
                "eligible_entry_ids": [expected.selected_entry_id],
                "selected_entry_id": expected.selected_entry_id,
                "attempt_entry_ids": [expected.selected_entry_id],
            },
            "attempts": [
                {
                    "provider_family": expected.provider_family,
                    "entry_id": expected.selected_entry_id,
                    "configured_model": expected.provider_model_id,
                    "result_kind": (
                        "succeeded"
                        if final_result_kind in {"succeeded", "parse_failed"}
                        else final_result_kind
                    ),
                    "provider_request_identity": request_identity,
                }
            ],
            "final_entry_id": (
                expected.selected_entry_id
                if final_result_kind in {"succeeded", "parse_failed"}
                else None
            ),
            "final_result_kind": final_result_kind,
        },
        provenance_ref={"artifact_id": "provenance"},
        raw_output=raw_output,
        raw_output_ref=raw_output_ref,
        usage_ref={"artifact_id": "usage"},
        created_at="2026-07-17T00:00:00Z",
    )


_MISSING = object()


def _config(
    *,
    provider_family: str,
    model: str,
    reasoning_effort: object = _MISSING,
    metadata: dict | None = None,
) -> AIAPIExecutorConfig:
    request_overrides: dict[str, object] = {"temperature": 0.0}
    if reasoning_effort is not _MISSING:
        request_overrides["reasoning_effort"] = reasoning_effort
    base_url = (
        "https://api.openai.com/v1"
        if provider_family == "openai"
        else "https://api.siliconflow.cn/v1"
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
                "top_p": 0.9,
                "stream": False,
                "max_provider_attempts": 1,
            },
            "entries": [
                {
                    "entry_id": "gpt-entry",
                    "enabled": True,
                    "base_url": base_url,
                    "api_key_env": "TEST_ONLY_API_KEY",
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
                    "tags": ["paper", provider_family],
                }
            ],
            "local_concurrency": {"max_in_flight_global": 1},
            "metadata": metadata or {"purpose": "paper-model-identity-test"},
        }
    )
