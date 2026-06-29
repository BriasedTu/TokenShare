"""Phase 6 real Lean proof plugin descriptor construction."""

from __future__ import annotations

from tokenshare.plugins.contracts import OutputContract, PluginDescriptor, SplitStrategyContract
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION,
    LEAN_CHECKER_REPORT_SCHEMA_VERSION,
    LEAN_FAILURE_REPORT_SCHEMA_VERSION,
    LEAN_MERGE_RESULT_SCHEMA_VERSION,
    LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
    LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
    LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION,
    LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
    MERGE_RESULT_CONTRACT_ID,
    MERGE_RESULT_OUTPUT_NAME,
    PLUGIN_ID,
    PLUGIN_VERSION,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
    PROOF_CANDIDATE_PARSER_ID,
    SUPPORTED_TASK_TYPES,
    THEOREM_PAYLOAD_CONTRACT_ID,
    THEOREM_PAYLOAD_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
    schema_ref,
)


def build_lean_proof_plugin_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        supported_task_types=list(SUPPORTED_TASK_TYPES),
        input_contract={
            "structured_theorem_payload": schema_ref(LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION),
            "proof_candidate": schema_ref(LEAN_PROOF_CANDIDATE_SCHEMA_VERSION),
        },
        output_contracts={
            THEOREM_PAYLOAD_OUTPUT_NAME: _theorem_payload_contract(),
            PROOF_ARTIFACT_OUTPUT_NAME: _proof_artifact_contract(),
            MERGE_RESULT_OUTPUT_NAME: _merge_result_contract(),
        },
        execution_contracts={
            "deterministic_lean_checker": {
                "hard_requirements": {"executor": "deterministic_local_lean_checker"},
                "output_contract_id": PROOF_ARTIFACT_CONTRACT_ID,
                "environment_policy": {
                    "runtime": "lean",
                    "network_access": False,
                    "environment_ref_required": True,
                    "checker_logs_required": True,
                },
            },
            "lean_helper_split": {
                "hard_requirements": {"executor": "deterministic_local_lean_helper"},
                "output_schema": schema_ref(LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION),
                "environment_policy": {
                    "runtime": "lean",
                    "network_access": False,
                    "environment_ref_required": True,
                },
            },
            "mock_ai_proof_candidate": {
                "hard_requirements": {"executor": "mock_ai"},
                "output_contract_id": PROOF_ARTIFACT_CONTRACT_ID,
                "prompt_package": {
                    "required": True,
                    "builder": "lean_proof.build_proof_candidate_prompt_package.v1",
                    "prompt_owner": "lean_proof_plugin",
                    "executor_may_define_prompt": False,
                    "executor_may_define_output_schema": False,
                    "executor_may_define_task_graph": False,
                    "prompt_package_schema": "phase3.prompt_package.v1",
                },
            },
            "ai_api_proof_candidate": {
                "hard_requirements": {"executor": "ai_api"},
                "output_contract_id": PROOF_ARTIFACT_CONTRACT_ID,
                "parser_id": PROOF_CANDIDATE_PARSER_ID,
                "executor_may_define_task_graph": False,
                "executor_may_define_canonical_output": False,
            },
            "environment_policy": {
                "runtime": "lean",
                "network_access": False,
                "seed_policy": "fixed_or_empty",
            },
        },
        split_strategies={
            DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID: SplitStrategyContract(
                split_strategy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
                params_schema_ref=schema_ref("lean_proof.deterministic_tactic_split_params.v1"),
                allowed_unit_types=["lean_proof_subgoal"],
                child_input_port_schema_refs={
                    "child_theorem_payload": schema_ref(
                        LEAN_CHILD_THEOREM_PAYLOAD_SCHEMA_VERSION
                    )
                },
                child_output_contract_refs={
                    "lean_proof_subgoal": {"output_contract_id": PROOF_ARTIFACT_CONTRACT_ID}
                },
                validator_policy_id=CHECKER_VALIDATOR_POLICY_ID,
                merge_policy_id=VERIFIED_MERGE_POLICY_ID,
                durable_subgoal_policy={
                    "only_promote_unit_types": ["lean_proof_subgoal"],
                    "only_promote_helper_certificate_children": True,
                    "requires_split_certificate": True,
                    "executor_may_define_task_graph": False,
                },
                candidate_artifact_policy={
                    "required_structured_output": PROOF_ARTIFACT_OUTPUT_NAME,
                    "required_schema_version": LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
                    "raw_text_authoritative": False,
                    "executor_may_submit_candidates": True,
                    "executor_may_define_task_graph": False,
                    "ai_may_decide_decomposition": False,
                },
                max_children_per_expansion=None,
            )
        },
        validator_policy_id=CHECKER_VALIDATOR_POLICY_ID,
        merge_policy_id=VERIFIED_MERGE_POLICY_ID,
        metadata={
            "plugin_identity": {
                "role": "real_lean_formal_proof_plugin",
                "is_real_checker_plugin": True,
            },
            "exclusive_task_types": [
                "lean_theorem",
                "lean_proof_subgoal",
                "lean_proof_merge",
            ],
            "schema_versions": {
                "theorem_payload": LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION,
                "proof_candidate": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
                "proof_artifact": LEAN_PROOF_ARTIFACT_SCHEMA_VERSION,
                "checker_report": LEAN_CHECKER_REPORT_SCHEMA_VERSION,
                "split_certificate": LEAN_SPLIT_CERTIFICATE_SCHEMA_VERSION,
                "merge_result": LEAN_MERGE_RESULT_SCHEMA_VERSION,
            },
            "real_checker_required": True,
            "lean_stub_allowed_as_success": False,
            "ai_may_decide_decomposition": False,
            "python_semantic_text_parse": False,
            "structured_theorem_payload_required": True,
            "environment_ref_required": True,
            "checker_logs_required": True,
            "ai_output_parse_policy": {
                "parser_id": PROOF_CANDIDATE_PARSER_ID,
                "parse_required": True,
                "raw_only_allowed": False,
                "raw_output_always_persisted": True,
                "parsed_schema_version": LEAN_PROOF_CANDIDATE_SCHEMA_VERSION,
                "parse_failure_schema": LEAN_FAILURE_REPORT_SCHEMA_VERSION,
                "verification_authority": CHECKER_VALIDATOR_POLICY_ID,
            },
            "phase8_ready_capabilities": [
                "lean_plugin_descriptor",
                "lean_fixture_manifest",
                "fixed_lean_environment_ref",
                "checker_log_artifacts",
                "proof_artifact_refs",
                "direct_proof_fixture",
                "decomposition_merge_fixture",
            ],
        },
    )


def _theorem_payload_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=THEOREM_PAYLOAD_CONTRACT_ID,
        required_outputs=[THEOREM_PAYLOAD_OUTPUT_NAME],
        output_schema_refs={
            THEOREM_PAYLOAD_OUTPUT_NAME: schema_ref(LEAN_THEOREM_PAYLOAD_SCHEMA_VERSION)
        },
        raw_output_policy={"allowed": False, "media_type": "application/json"},
    )


def _proof_artifact_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=PROOF_ARTIFACT_CONTRACT_ID,
        required_outputs=[PROOF_ARTIFACT_OUTPUT_NAME],
        output_schema_refs={
            PROOF_ARTIFACT_OUTPUT_NAME: schema_ref(LEAN_PROOF_ARTIFACT_SCHEMA_VERSION)
        },
        raw_output_policy={
            "allowed": True,
            "authoritative": False,
            "media_type": "text/plain",
            "max_size_bytes": 65536,
        },
        parsed_output_schema_ref=schema_ref(LEAN_PROOF_CANDIDATE_SCHEMA_VERSION),
        parse_failure_schema_ref=schema_ref(LEAN_FAILURE_REPORT_SCHEMA_VERSION),
    )


def _merge_result_contract() -> OutputContract:
    return OutputContract(
        output_contract_id=MERGE_RESULT_CONTRACT_ID,
        required_outputs=[MERGE_RESULT_OUTPUT_NAME],
        output_schema_refs={MERGE_RESULT_OUTPUT_NAME: schema_ref(LEAN_MERGE_RESULT_SCHEMA_VERSION)},
        raw_output_policy={"allowed": False, "media_type": "application/json"},
        parsed_output_schema_ref=schema_ref(LEAN_MERGE_RESULT_SCHEMA_VERSION),
    )
