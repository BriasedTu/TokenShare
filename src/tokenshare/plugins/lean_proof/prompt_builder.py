"""Lean proof candidate prompt and AI output parse policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import PromptPackage
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    LEAN_FAILURE_REPORT_SCHEMA_VERSION,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    PROOF_CANDIDATE_PARSER_ID,
)


LEAN_PROOF_CANDIDATE_PROMPT_PROFILE = "lean_proof.proof_candidate_prompt.v1"
PROOF_CANDIDATE_OUTPUT_NAME = "proof_candidate"

_PROOF_CANDIDATE_REQUIRED_FIELDS = [
    "schema_version",
    "proof_candidate_id",
    "theorem_payload_digest",
    "proof_source",
    "created_at",
]

_FORBIDDEN_DECOMPOSITION_KEYS = {
    "child_goals",
    "children",
    "decomposition_proposal",
    "merge_plan",
    "split_plan",
    "task_graph",
}

_FORBIDDEN_PROOF_PLACEHOLDER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_'.])(?P<placeholder>sorry|admit)(?![A-Za-z0-9_'.])"
)


@dataclass(frozen=True, kw_only=True)
class LeanProofCandidateAIParseResult:
    """Pure result of the Lean plugin-owned AI output parse policy."""

    succeeded: bool
    result_kind: str
    parser_id: str
    required_output_name: str | None
    parsed_artifact_schema_id: str | None
    parsed_artifact_schema_version: str | None
    parsed_artifact_body: JsonObject | None
    candidate_output_artifact_bodies: dict[str, JsonObject]
    parse_failure_artifact_body: JsonObject | None


def build_lean_proof_candidate_prompt_package(
    *,
    request_id: str,
    task_id: str,
    unit_id: str,
    theorem_payload: LeanTheoremPayload,
    created_at: str,
    seed: int | None = None,
) -> PromptPackage:
    """Build the plugin-owned prompt package for proof candidate generation."""

    return PromptPackage(
        prompt_package_id=f"lean_proof_candidate_prompt:{request_id}",
        request_id=request_id,
        task_id=task_id,
        unit_id=unit_id,
        prompt_text=_prompt_text(theorem_payload),
        input_summary={
            "theorem_id": theorem_payload.theorem_id,
            "theorem_name": theorem_payload.theorem_name,
            "theorem_payload_digest": theorem_payload.payload_digest,
            "imports": list(theorem_payload.imports),
            "namespace": theorem_payload.namespace,
            "statement_source": theorem_payload.statement_source,
        },
        output_schema={
            "schema_version": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
            "media_type": "application/json",
            "required_fields": list(_PROOF_CANDIDATE_REQUIRED_FIELDS),
        },
        constraints={
            "prompt_owner": "lean_proof_plugin",
            "verification_authority": CHECKER_VALIDATOR_POLICY_ID,
            "strict_json_only": True,
            "requires_json_mode": True,
            "executor_must_not": [
                "create_or_modify_task_graph",
                "return_child_tasks",
                "return_split_plan",
                "return_merge_plan",
                "claim_checker_success",
                "claim_canonical_output",
                "claim_settlement",
            ],
        },
        seed=seed,
        fixture_profile=LEAN_PROOF_CANDIDATE_PROMPT_PROFILE,
        created_at=created_at,
    )


def parse_lean_proof_candidate_ai_output(
    raw_output: JsonObject | str | None,
    *,
    theorem_payload: LeanTheoremPayload,
    raw_output_ref_summary: JsonObject,
    created_at: str,
    raw_only: bool = False,
) -> LeanProofCandidateAIParseResult:
    """Parse only proof candidate JSON; never promote AI decomposition output."""

    if raw_only or raw_output is None:
        return _parse_failure_result(
            failure_kind="raw_only_not_allowed",
            message="Lean proof candidate generation requires parsed JSON output",
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )
    try:
        body = _structured_json_object(raw_output)
    except (TypeError, ValueError) as exc:
        return _parse_failure_result(
            failure_kind="invalid_json_object",
            message=str(exc),
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    forbidden_keys = sorted(_FORBIDDEN_DECOMPOSITION_KEYS.intersection(body))
    if forbidden_keys:
        return _parse_failure_result(
            failure_kind="decomposition_authority_forbidden",
            message=(
                "AI output cannot define Lean decomposition, child tasks, or merge plan: "
                + ", ".join(forbidden_keys)
            ),
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )
    validation_error = _validate_proof_candidate_body(body, theorem_payload=theorem_payload)
    if validation_error is not None:
        return _parse_failure_result(
            failure_kind="invalid_structured_proof_candidate",
            message=validation_error,
            raw_output=raw_output,
            raw_output_ref_summary=raw_output_ref_summary,
            created_at=created_at,
        )

    return LeanProofCandidateAIParseResult(
        succeeded=True,
        result_kind="parsed",
        parser_id=PROOF_CANDIDATE_PARSER_ID,
        required_output_name=PROOF_CANDIDATE_OUTPUT_NAME,
        parsed_artifact_schema_id="lean_proof.proof_candidate",
        parsed_artifact_schema_version="v1",
        parsed_artifact_body=body,
        candidate_output_artifact_bodies={PROOF_CANDIDATE_OUTPUT_NAME: body},
        parse_failure_artifact_body=None,
    )


def _prompt_text(theorem_payload: LeanTheoremPayload) -> str:
    required_fields = ", ".join(_PROOF_CANDIDATE_REQUIRED_FIELDS)
    return "\n".join(
        [
            "You are generating a Lean proof candidate for TokenShare.",
            f"Theorem name: {theorem_payload.theorem_name}",
            f"Imports: {', '.join(theorem_payload.imports)}",
            f"Namespace: {theorem_payload.namespace or '<root>'}",
            f"Parameters source: {theorem_payload.parameters_source or '<none>'}",
            f"Statement source: {theorem_payload.statement_source}",
            f"Theorem payload digest: {theorem_payload.payload_digest}",
            f"Return only one JSON object matching {LEAN_PROOF_CANDIDATE_SCHEMA_VERSION}.",
            "The JSON object must contain a proof_source string accepted by the fixed Lean checker.",
            "Do not propose child tasks.",
            "Do not return a split plan.",
            "Do not return a merge plan.",
            "Do not claim checker success; the local Lean checker is the only authority.",
            "Do not return prose, markdown, or reasoning outside the JSON object.",
            f"Required JSON fields: {required_fields}.",
        ]
    )


def _validate_proof_candidate_body(
    body: JsonObject,
    *,
    theorem_payload: LeanTheoremPayload,
) -> str | None:
    if body.get("schema_version") != LEAN_PROOF_CANDIDATE_SCHEMA_VERSION:
        return "proof candidate schema_version must be lean_proof.proof_candidate.v1"
    for field_name in _PROOF_CANDIDATE_REQUIRED_FIELDS:
        if field_name not in body:
            return f"proof candidate missing required field: {field_name}"
    for field_name in ("proof_candidate_id", "theorem_payload_digest", "proof_source", "created_at"):
        if not isinstance(body.get(field_name), str) or not body.get(field_name):
            return f"{field_name} must be a non-empty string"
    if body["theorem_payload_digest"] != theorem_payload.payload_digest:
        return "theorem_payload_digest does not match prompt theorem payload"
    if not str(body["proof_candidate_id"]).startswith("proof_candidate:"):
        return "proof_candidate_id must start with proof_candidate:"
    placeholder = _forbidden_proof_placeholder(body["proof_source"])
    if placeholder is not None:
        return f"proof_source contains forbidden Lean placeholder: {placeholder}"
    return None


def _forbidden_proof_placeholder(proof_source: str) -> str | None:
    match = _FORBIDDEN_PROOF_PLACEHOLDER_PATTERN.search(proof_source)
    if match is None:
        return None
    return match.group("placeholder")


def _structured_json_object(payload: JsonObject | str) -> JsonObject:
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("proof candidate parser requires a structured JSON object") from exc
        if not isinstance(decoded, dict):
            raise ValueError("proof candidate parser requires a structured JSON object")
        return decoded
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError("proof candidate parser requires a structured JSON object")


def _parse_failure_result(
    *,
    failure_kind: str,
    message: str,
    raw_output: JsonObject | str | None,
    raw_output_ref_summary: JsonObject,
    created_at: str,
) -> LeanProofCandidateAIParseResult:
    return LeanProofCandidateAIParseResult(
        succeeded=False,
        result_kind="parse_failed",
        parser_id=PROOF_CANDIDATE_PARSER_ID,
        required_output_name=None,
        parsed_artifact_schema_id=None,
        parsed_artifact_schema_version=None,
        parsed_artifact_body=None,
        candidate_output_artifact_bodies={},
        parse_failure_artifact_body={
            "schema_version": LEAN_FAILURE_REPORT_SCHEMA_VERSION,
            "parser_id": PROOF_CANDIDATE_PARSER_ID,
            "failure_kind": failure_kind,
            "message": message,
            "raw_excerpt": _raw_excerpt(raw_output),
            "raw_output_ref_summary": dict(raw_output_ref_summary),
            "candidate_outputs": {},
            "created_at": created_at,
        },
    )


def _raw_excerpt(raw_output: JsonObject | str | None, *, max_chars: int = 200) -> str | None:
    if raw_output is None:
        return None
    if isinstance(raw_output, str):
        text = raw_output
    else:
        text = json.dumps(raw_output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}..."


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
