from tests.phase2_fixtures import make_artifact_ref
from tokenshare.executors.contracts import EnvironmentRef, ExecutorDescriptor, ExecutorStatus
from tokenshare.plugins.contracts import OutputContract, PluginDescriptor


def make_output_contract() -> OutputContract:
    return OutputContract(
        output_contract_id="contract_answer",
        required_outputs=["answer"],
        optional_outputs=["confidence"],
        output_schema_refs={"answer": {"schema_ref": "schema.answer.v1"}},
        raw_output_policy={"allowed": True, "max_size_bytes": 4096, "media_type": "text/plain"},
        parsed_output_schema_ref={"schema_ref": "schema.parsed.v1"},
        candidate_bundle_schema_ref={"schema_ref": "schema.candidate_bundle.v1"},
        parse_failure_schema_ref={"schema_ref": "schema.parse_failure.v1"},
    )


def make_plugin_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        supported_task_types=["work"],
        input_contract={"root_input": {"media_type": "application/json"}},
        output_contracts={"work": make_output_contract()},
        execution_contracts={
            "mock_ai": {
                "hard_requirements": {"executor": "mock_ai"},
                "soft_hints": {"prefer": "deterministic-fixture"},
                "environment_policy": {"runtime": "python"},
                "output_contract_id": "contract_answer",
            }
        },
        validator_policy_id="structured_report_stub_validator_v1",
        merge_policy_id="structured_report_stub_merge_v1",
        metadata={"purpose": "phase3-test"},
    )


def make_executor_descriptor(
    *,
    executor_id: str = "executor_mock_ai",
    status: ExecutorStatus = ExecutorStatus.AVAILABLE,
) -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_id=executor_id,
        executor_type="mock_ai",
        executor_version="0.1.0",
        supported_request_schema_versions=["phase3.execution_request.v1"],
        capabilities={"executor": "mock_ai", "output_modes": ["raw_text", "parsed_json"]},
        environment_policy={"runtime": "python", "fixture_profile": "stable"},
        status=status,
        metadata={"purpose": "phase3-test"},
    )


def make_environment_ref() -> EnvironmentRef:
    return EnvironmentRef(
        environment_id="env_mock_ai",
        environment_digest="sha256:env_mock_ai",
        runtime="python",
        tool_versions={"mock_ai": "0.1.0"},
        resource_limits={"timeout_seconds": 30},
        fixture_profile_digest="sha256:fixture_profile",
        seed=7,
        clock_policy="fixed",
        created_at="2026-06-23T00:00:00Z",
    )


def make_schema_ref_artifact(artifact_id: str = "artifact_schema_answer"):
    return make_artifact_ref(artifact_id=artifact_id)
