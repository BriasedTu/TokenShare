"""Phase 6 factorization 插件 descriptor 构造。"""

from __future__ import annotations

from tokenshare.plugins.contracts import OutputContract, PluginDescriptor, SplitStrategyContract
from tokenshare.plugins.factorization.schemas import (
    ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
    CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION,
    CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
    FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
    FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
    FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION,
    FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
    FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_CONTRACT_ID,
    RANGE_RESULT_SCHEMA_VERSION,
    RANGE_RESULT_VALIDATOR_POLICY_ID,
    SUPPORTED_TASK_TYPES,
    schema_ref,
)


def build_factorization_plugin_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        supported_task_types=list(SUPPORTED_TASK_TYPES),
        input_contract={
            "root_input": schema_ref("factorization.root_input.v1"),
            "canonical_subject": schema_ref(FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION),
        },
        output_contracts={
            "factor_integer_subject": _factor_integer_subject_contract(),
            "range_result": _range_result_contract(),
            "factorization_result": _factorization_merge_result_contract(),
        },
        execution_contracts={
            "deterministic_local": {
                "hard_requirements": {"executor": "deterministic_local"},
                "output_contract_ids": [
                    FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
                    FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
                ],
                "environment_policy": {"runtime": "python", "network_access": False},
            },
            "mock_ai_bounded_search": {
                "hard_requirements": {"executor": "mock_ai"},
                "output_contract_id": RANGE_RESULT_CONTRACT_ID,
                "bounded_range_required": True,
                "verifier_rechecks_output": True,
            },
            "environment_policy": {
                "runtime": "python",
                "network_access": False,
                "seed_policy": "fixed_or_empty",
            },
        },
        split_strategies={
            CANDIDATE_RANGE_PARTITION_STRATEGY_ID: SplitStrategyContract(
                split_strategy_id=CANDIDATE_RANGE_PARTITION_STRATEGY_ID,
                params_schema_ref=schema_ref(CANDIDATE_RANGE_PARTITION_PARAMS_SCHEMA_VERSION),
                allowed_unit_types=["factor_search_range"],
                child_input_port_schema_refs={
                    "factor_search_range_input": schema_ref(FACTOR_SEARCH_RANGE_INPUT_SCHEMA_VERSION)
                },
                child_output_contract_refs={
                    "factor_search_range": {"output_contract_id": RANGE_RESULT_CONTRACT_ID}
                },
                validator_policy_id=RANGE_RESULT_VALIDATOR_POLICY_ID,
                merge_policy_id=ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
                durable_subgoal_policy={
                    "only_promote_unit_types": ["factor_search_range"],
                    "requires_bounded_candidate_range": True,
                    "executor_may_define_task_graph": False,
                },
                candidate_artifact_policy={
                    "required_structured_output": "range_result",
                    "required_schema_version": RANGE_RESULT_SCHEMA_VERSION,
                    "raw_text_authoritative": False,
                    "executor_may_submit_candidates": True,
                    "executor_may_define_task_graph": False,
                },
                max_children_per_expansion=None,
            )
        },
        validator_policy_id=RANGE_RESULT_VALIDATOR_POLICY_ID,
        merge_policy_id=ALL_REQUIRED_RANGE_MERGE_POLICY_ID,
        metadata={
            "schema_versions": {
                "factor_integer_subject": FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION,
                "range_result": RANGE_RESULT_SCHEMA_VERSION,
                "factorization_merge_result": FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION,
                "prime_factorization_result": PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION,
            },
            "first_slice_limitations": {
                "early_success": "deferred",
                "sibling_pruning": "deferred",
                "composite_cofactor_recursive_resolution": "deferred",
            },
            "recursive_policy": {
                "same_plugin_for_recursive_factor_integer": True,
                "continuation_plugin_allowed": False,
            },
        },
    )


def _factor_integer_subject_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=FACTOR_INTEGER_SUBJECT_CONTRACT_ID,
        required_outputs=["factor_integer_subject"],
        output_schema_refs={
            "factor_integer_subject": schema_ref(FACTOR_INTEGER_SUBJECT_SCHEMA_VERSION)
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
    )


def _range_result_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=RANGE_RESULT_CONTRACT_ID,
        required_outputs=["range_result"],
        output_schema_refs={"range_result": schema_ref(RANGE_RESULT_SCHEMA_VERSION)},
        raw_output_policy={
            "allowed": True,
            "authoritative": False,
            "media_type": "text/plain",
            "max_size_bytes": 4096,
        },
        parsed_output_schema_ref=schema_ref(RANGE_RESULT_SCHEMA_VERSION),
    )


def _factorization_merge_result_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=FACTORIZATION_MERGE_RESULT_CONTRACT_ID,
        required_outputs=["factorization_result"],
        output_schema_refs={
            "factorization_result": schema_ref(FACTORIZATION_MERGE_RESULT_SCHEMA_VERSION),
            "prime_factorization_result": schema_ref(PRIME_FACTORIZATION_RESULT_SCHEMA_VERSION),
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
    )
