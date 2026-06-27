"""Phase 6 factorization 插件 schema 常量。"""

from __future__ import annotations

from tokenshare.core.models import JsonObject


PLUGIN_ID = "factorization"
PLUGIN_VERSION = "0.1.0"

ROOT_TASK_TYPE = "root"
FACTOR_INTEGER_TASK_TYPE = "factor_integer"
FACTOR_SEARCH_RANGE_TASK_TYPE = "factor_search_range"
FACTORIZATION_MERGE_TASK_TYPE = "factorization_merge"

SUPPORTED_TASK_TYPES = [
    ROOT_TASK_TYPE,
    FACTOR_INTEGER_TASK_TYPE,
    FACTOR_SEARCH_RANGE_TASK_TYPE,
    FACTORIZATION_MERGE_TASK_TYPE,
]

ROOT_INPUT_SCHEMA_VERSION = "factorization.root_input.v1"
FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION = "factorization.factor_integer_subject.v1"
CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION = (
    "factorization.candidate_range_partition_params.v1"
)
CANDIDATE_RANGE_COVERAGE_PROOF_SCHEMA_VERSION = (
    "factorization.candidate_range_coverage_proof.v1"
)
FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION = "factorization.factor_search_range_input.v1"
FACTOR_SEARCH_INSTRUCTION_SCHEMA_VERSION = "factorization.factor_search_instruction.v1"
RANGE_RESULT_SCHEMA_VERSION = "factorization.range_result.v1"
FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION = "factorization.merge_result.v1"
PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION = "factorization.prime_factorization_result.v1"
FIXTURE_CASE_SCHEMA_VERSION = "factorization.fixture_case.v1"

CANDIDATE_RANGE_PARTITION_STRATEGY_ID = "factorization.candidate_range_partition.v1"
RANGE_RESULT_VALIDATOR_POLICY_ID = "factorization.range_result.validator.v1"
ALL_REQUIRED_RANGE_MERGE_POLICY_ID = "factorization.all_required_range_merge.v1"
TRIAL_DIVISION_PRIMALITY_POLICY_ID = "factorization.trial_division_primality.v1"

FACTOR_INTEGER_SUBJECT_CONTRACT_ID = "factorization.factor_integer_subject.contract.v1"
RANGE_RESULT_CONTRACT_ID = "factorization.range_result.contract.v1"
FACTORIZATION_MERGE_RESULT_CONTRACT_ID = "factorization.merge_result.contract.v1"

REQUESTED_OUTPUT_PRIME_FACTORIZATION = "prime_factorization_result"
RANGE_RESULT_FOUND_FACTOR = "found_factor"
RANGE_RESULT_NO_FACTOR = "no_factor_in_range"
RANGE_RESULT_KINDS = [RANGE_RESULT_FOUND_FACTOR, RANGE_RESULT_NO_FACTOR]

MERGE_RESULT_PRIME_CERTIFICATE = "prime_certificate"
MERGE_RESULT_PRIME_FACTORIZATION = "prime_factorization_result"
MERGE_RESULT_NONTRIVIAL_FACTOR = "nontrivial_factor_found"
MERGE_RESULT_KINDS = [
    MERGE_RESULT_PRIME_CERTIFICATE,
    MERGE_RESULT_PRIME_FACTORIZATION,
    MERGE_RESULT_NONTRIVIAL_FACTOR,
]


def schema_ref(schema_version: str) -> JsonObject:
    """根据完整 schema_version 生成稳定 artifact schema ref。"""

    schema_id, version = schema_version.rsplit(".", 1)
    return {
        "schema_version": schema_version,
        "artifact_schema_id": schema_id,
        "artifact_schema_version": version,
    }
