import json

from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.prompt_builder import (
    build_lean_proof_candidate_prompt_package,
    parse_lean_proof_candidate_ai_output,
)


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_prompt_package_requests_only_proof_candidate_not_split_plan() -> None:
    payload = _payload()

    prompt = build_lean_proof_candidate_prompt_package(
        request_id="request_lean_ai_1",
        task_id="task_lean",
        unit_id="unit_lean",
        theorem_payload=payload,
        created_at=CREATED_AT,
        seed=7,
    )

    assert prompt.prompt_package_id == "lean_proof_candidate_prompt:request_lean_ai_1"
    assert prompt.input_summary["theorem_payload_digest"] == payload.payload_digest
    assert prompt.output_schema == {
        "schema_version": "lean_proof.proof_candidate.v1",
        "media_type": "application/json",
        "required_fields": [
            "schema_version",
            "proof_candidate_id",
            "theorem_payload_digest",
            "proof_source",
            "created_at",
        ],
    }
    assert prompt.constraints["strict_json_only"] is True
    assert prompt.constraints["verification_authority"] == "lean_proof.checker.validator.v1"
    assert prompt.constraints["executor_must_not"] == [
        "create_or_modify_task_graph",
        "return_child_tasks",
        "return_split_plan",
        "return_merge_plan",
        "claim_checker_success",
        "claim_canonical_output",
        "claim_settlement",
    ]
    assert "Return only one JSON object" in prompt.prompt_text
    assert "proof_source" in prompt.prompt_text
    assert "Do not propose child tasks" in prompt.prompt_text
    assert "Do not return a split plan" in prompt.prompt_text


def test_lean_parse_policy_maps_valid_json_to_proof_candidate() -> None:
    payload = _payload()
    raw_output_ref_summary = {
        "artifact_id": "raw_model_output_lean_1",
        "content_hash": "sha256:raw_lean_1",
    }
    raw_output = {
        "schema_version": "lean_proof.proof_candidate.v1",
        "proof_candidate_id": "proof_candidate:one_eq_one:1",
        "theorem_payload_digest": payload.payload_digest,
        "proof_source": "rfl",
        "created_at": CREATED_AT,
    }

    result = parse_lean_proof_candidate_ai_output(
        json.dumps(raw_output),
        theorem_payload=payload,
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
    )

    assert result.succeeded is True
    assert result.result_kind == "parsed"
    assert result.parser_id == "lean_proof.proof_candidate.parser.v1"
    assert result.required_output_name == "proof_candidate"
    assert result.parsed_artifact_schema_id == "lean_proof.proof_candidate"
    assert result.parsed_artifact_schema_version == "v1"
    assert result.parsed_artifact_body == raw_output
    assert result.candidate_output_artifact_bodies == {"proof_candidate": raw_output}
    assert result.parse_failure_artifact_body is None


def test_lean_parse_policy_records_parse_failure_for_freeform_or_split_plan_output() -> None:
    payload = _payload()
    raw_output_ref_summary = {
        "artifact_id": "raw_model_output_lean_freeform",
        "content_hash": "sha256:raw_freeform",
    }

    freeform = parse_lean_proof_candidate_ai_output(
        "The proof is obvious by rfl.",
        theorem_payload=payload,
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
    )
    split_plan = parse_lean_proof_candidate_ai_output(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:bad:1",
            "theorem_payload_digest": payload.payload_digest,
            "proof_source": "rfl",
            "created_at": CREATED_AT,
            "child_goals": [{"statement_source": "1 = 1"}],
            "merge_plan": {"rule": "and_intro"},
        },
        theorem_payload=payload,
        raw_output_ref_summary=raw_output_ref_summary,
        created_at=CREATED_AT,
    )

    assert freeform.succeeded is False
    assert freeform.result_kind == "parse_failed"
    assert freeform.candidate_output_artifact_bodies == {}
    assert freeform.parse_failure_artifact_body["failure_kind"] == "invalid_json_object"
    assert freeform.parse_failure_artifact_body["candidate_outputs"] == {}

    assert split_plan.succeeded is False
    assert split_plan.result_kind == "parse_failed"
    assert split_plan.candidate_output_artifact_bodies == {}
    assert split_plan.parse_failure_artifact_body["failure_kind"] == (
        "decomposition_authority_forbidden"
    )
    assert split_plan.parse_failure_artifact_body["candidate_outputs"] == {}


def test_lean_ai_output_never_creates_decomposition_proposal() -> None:
    payload = _payload()

    result = parse_lean_proof_candidate_ai_output(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": "proof_candidate:bad_split:1",
            "theorem_payload_digest": payload.payload_digest,
            "proof_source": "rfl",
            "created_at": CREATED_AT,
            "decomposition_proposal": {
                "child_specs": [{"child_logical_key": "ai_child"}],
            },
        },
        theorem_payload=payload,
        raw_output_ref_summary={"artifact_id": "raw_model_output_ai_split"},
        created_at=CREATED_AT,
    )

    assert result.succeeded is False
    assert result.parse_failure_artifact_body["failure_kind"] == (
        "decomposition_authority_forbidden"
    )
    assert not hasattr(result, "decomposition_proposal")
    assert result.candidate_output_artifact_bodies == {}


def test_lean_parse_policy_rejects_sorry_and_admit_proof_sources() -> None:
    payload = _payload()

    for placeholder in ("sorry", "admit"):
        result = parse_lean_proof_candidate_ai_output(
            {
                "schema_version": "lean_proof.proof_candidate.v1",
                "proof_candidate_id": f"proof_candidate:{placeholder}:1",
                "theorem_payload_digest": payload.payload_digest,
                "proof_source": f"by\n  {placeholder}",
                "created_at": CREATED_AT,
            },
            theorem_payload=payload,
            raw_output_ref_summary={"artifact_id": f"raw_model_output_{placeholder}"},
            created_at=CREATED_AT,
        )

        assert result.succeeded is False
        assert result.parse_failure_artifact_body["failure_kind"] == (
            "invalid_structured_proof_candidate"
        )
        assert placeholder in result.parse_failure_artifact_body["message"]


def _payload() -> LeanTheoremPayload:
    return LeanTheoremPayload(
        theorem_id="lean_theorem:one_eq_one",
        theorem_name="one_eq_one",
        imports=["Init"],
        namespace="TokenShareFixtures",
        open_namespaces=[],
        options={},
        parameters_source="",
        statement_source="1 = 1",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={
            "project": "tokenshare_lean",
            "module": "TokenShare.Fixtures.Direct",
        },
        decomposition_policy={
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
