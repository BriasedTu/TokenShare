import json

from tokenshare.plugins.factorization import parse_factorization_ai_output
from tokenshare.plugins.factorization.descriptor import build_factorization_plugin_descriptor


CREATED_AT = "2026-06-28T00:00:00Z"


def test_factorization_descriptor_declares_parse_required_and_raw_only_forbidden() -> None:
    descriptor = build_factorization_plugin_descriptor()
    body = descriptor.to_dict()

    policy = body["metadata"]["ai_output_parse_policy"]
    assert policy == {
        "parser_id": "factorization.range_result.parser.v1",
        "parse_required": True,
        "raw_only_allowed": False,
        "raw_output_always_persisted": True,
        "parsed_schema_version": "factorization.range_result.v1",
        "required_output_mapping": {
            "range_result": {
                "artifact_schema_id": "factorization.range_result",
                "artifact_schema_version": "v1",
            }
        },
        "parse_failure_schema": "phase3.parse_failure_report.v1",
        "verification_authority": "factorization.range_result.validator.v1",
    }


def test_factorization_parser_maps_valid_model_json_to_range_result_required_output() -> None:
    raw_output_ref_summary = {
        "artifact_id": "raw_model_output_1",
        "content_hash": "sha256:raw",
        "artifact_schema_id": "phase7.raw_model_output",
        "artifact_schema_version": "v1",
    }
    payload = _valid_range_result_payload()

    result = parse_factorization_ai_output(
        json.dumps(payload),
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
    )

    assert result.succeeded is True
    assert result.result_kind == "parsed"
    assert result.parser_id == "factorization.range_result.parser.v1"
    assert result.required_output_name == "range_result"
    assert result.parsed_artifact_schema_id == "factorization.range_result"
    assert result.parsed_artifact_schema_version == "v1"
    assert result.parsed_artifact_body == payload
    assert result.candidate_output_artifact_bodies == {"range_result": payload}
    assert result.parse_failure_artifact_body is None


def test_factorization_parser_records_parse_failure_for_freeform_output() -> None:
    raw_output_ref_summary = {
        "artifact_id": "raw_model_output_freeform",
        "content_hash": "sha256:raw_freeform",
    }

    result = parse_factorization_ai_output(
        "I found factor 13",
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
    )

    assert result.succeeded is False
    assert result.result_kind == "parse_failed"
    assert result.required_output_name is None
    assert result.parsed_artifact_body is None
    assert result.candidate_output_artifact_bodies == {}

    failure = result.parse_failure_artifact_body
    assert failure == {
        "schema_version": "phase3.parse_failure_report.v1",
        "parser_id": "factorization.range_result.parser.v1",
        "failure_kind": "invalid_json_object",
        "message": "range_result parser requires a structured JSON object",
        "raw_excerpt": "I found factor 13",
        "raw_output_ref_summary": raw_output_ref_summary,
        "candidate_outputs": {},
        "created_at": CREATED_AT,
    }


def test_factorization_ai_executor_path_never_treats_raw_only_as_successful_range_result() -> None:
    raw_output_ref_summary = {
        "artifact_id": "raw_model_output_raw_only",
        "content_hash": "sha256:raw_only",
    }

    result = parse_factorization_ai_output(
        None,
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
        raw_only=True,
    )

    assert result.succeeded is False
    assert result.result_kind == "parse_failed"
    assert result.required_output_name is None
    assert result.parsed_artifact_body is None
    assert result.candidate_output_artifact_bodies == {}
    assert result.parse_failure_artifact_body["failure_kind"] == "raw_only_not_allowed"
    assert result.parse_failure_artifact_body["raw_output_ref_summary"] == raw_output_ref_summary


def _valid_range_result_payload() -> dict:
    return {
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
        "created_at": CREATED_AT,
    }
