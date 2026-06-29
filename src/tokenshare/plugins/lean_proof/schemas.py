"""Phase 6 real Lean proof plugin schema constants."""

from __future__ import annotations

from tokenshare.core.models import JsonObject


PLUGIN_ID = "lean_proof"
PLUGIN_VERSION = "0.1.0"

ROOT_TASK_TYPE = "root"
LEAN_THEOREM_TASK_TYPE = "lean_theorem"
LEAN_PROOF_SUBGOAL_TASK_TYPE = "lean_proof_subgoal"
LEAN_PROOF_MERGE_TASK_TYPE = "lean_proof_merge"

SUPPORTED_TASK_TYPES = [
    ROOT_TASK_TYPE,
    LEAN_THEOREM_TASK_TYPE,
    LEAN_PROOF_SUBGOAL_TASK_TYPE,
    LEAN_PROOF_MERGE_TASK_TYPE,
]

LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION = "lean_proof.theorem_payload.v1"
LEAN_PROOF_CANDIDATE_SCHEMA_VERSION = "lean_proof.proof_candidate.v1"
LEAN_PROOF_ARTIFACT_SCHEMA_VERSION = "lean_proof.proof_artifact.v1"
LEAN_CHECKER_REQUEST_SCHEMA_VERSION = "lean_proof.checker_request.v1"
LEAN_CHECKER_REPORT_SCHEMA_VERSION = "lean_proof.checker_report.v1"
LEAN_SPLIT_REQUEST_SCHEMA_VERSION = "lean_proof.split_request.v1"
LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION = "lean_proof.split_certificate.v1"
LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION = "lean_proof.child_theorem_payload.v1"
LEAN_MERGE_REQUEST_SCHEMA_VERSION = "lean_proof.merge_request.v1"
LEAN_MERGE_RESULT_SCHEMA_VERSION = "lean_proof.merge_result.v1"
LEAN_FIXTURE_MANIFEST_SCHEMA_VERSION = "lean_proof.fixture_manifest.v1"
LEAN_FAILURE_REPORT_SCHEMA_VERSION = "lean_proof.failure_report.v1"
LEAN_ENVIRONMENT_MANIFEST_SCHEMA_VERSION = "lean_proof.environment_manifest.v1"

DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID = "lean_proof.deterministic_tactic_split.v1"
CHECKER_VALIDATOR_POLICY_ID = "lean_proof.checker.validator.v1"
VERIFIED_MERGE_POLICY_ID = "lean_proof.verified_merge.v1"
PROOF_CANDIDATE_PARSER_ID = "lean_proof.proof_candidate.parser.v1"

THEOREM_PAYLOAD_CONTRACT_ID = "lean_proof.root_theorem.contract.v1"
PROOF_ARTIFACT_CONTRACT_ID = "lean_proof.proof_artifact.contract.v1"
MERGE_RESULT_CONTRACT_ID = "lean_proof.merge_result.contract.v1"

THEOREM_PAYLOAD_OUTPUT_NAME = "lean_theorem_payload"
PROOF_ARTIFACT_OUTPUT_NAME = "lean_proof_artifact"
MERGE_RESULT_OUTPUT_NAME = "lean_merge_result"


def schema_ref(schema_version: str) -> JsonObject:
    """Build a stable artifact schema ref from a full schema version."""

    schema_id, version = schema_version.rsplit(".", 1)
    return {
        "schema_version": schema_version,
        "artifact_schema_id": schema_id,
        "artifact_schema_version": version,
    }
