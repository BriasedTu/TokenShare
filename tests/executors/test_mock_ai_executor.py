from tokenshare.executors.contracts import ExecutionRequest, PromptPackage
from tokenshare.executors.mock_ai import MockAIExecutor, MockAIExecutorProfile
from tokenshare.storage.artifacts import ArtifactStore

from tests.phase2_fixtures import make_unit
from tests.phase3_fixtures import make_environment_ref, make_output_contract


def test_mock_ai_executor_accepts_unified_request_and_persists_ai_artifacts(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    prompt_ref = store.save_json(
        PromptPackage(
            prompt_package_id="prompt_1",
            request_id="request_1",
            task_id="task_demo",
            unit_id="unit_ready",
            prompt_text="Return a JSON answer.",
            input_summary={"unit": "unit_ready"},
            output_schema={"required": ["answer"]},
            constraints={"format": "json"},
            seed=7,
            fixture_profile="stable",
            created_at="2026-06-23T00:00:01Z",
        ).to_dict(),
        artifact_id="prompt_1",
        artifact_type="PromptPackage",
        artifact_schema_id="phase3.prompt_package",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at="2026-06-23T00:00:01Z",
    )
    request = ExecutionRequest(
        request_id="request_1",
        task_id="task_demo",
        unit_id="unit_ready",
        attempt_id="attempt_1",
        lease_id="lease_1",
        fencing_token="token_1",
        plugin={
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_ref": {"artifact_id": "plugin_descriptor_1"},
            "plugin_descriptor_digest": "sha256:plugin",
        },
        executor={
            "executor_id": "executor_mock_ai",
            "executor_version": "0.1.0",
            "executor_descriptor_ref": {"artifact_id": "executor_descriptor_1"},
            "executor_descriptor_digest": "sha256:executor",
        },
        registry_snapshot_id="registry_snapshot_1",
        allocation_decision={
            "decision_id": "allocation_1",
            "selected_executor_id": "executor_mock_ai",
            "eligible_executor_ids": ["executor_mock_ai"],
        },
        capability_snapshot={"executor": "mock_ai"},
        task_unit_snapshot=make_unit().to_dict(),
        input_artifact_refs={},
        output_contract=make_output_contract(),
        hard_requirements={"executor": "mock_ai"},
        soft_hints={"prefer": "deterministic-fixture"},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=prompt_ref,
        limits={"timeout_seconds": 30},
        created_at="2026-06-23T00:00:02Z",
    )
    executor = MockAIExecutor(
        executor_id="executor_mock_ai",
        executor_version="0.1.0",
        artifact_store=store,
        profile=MockAIExecutorProfile(
            raw_text='{"answer":"forty-two"}',
            parsed_output={"answer": "forty-two"},
        ),
    )

    submission = executor.execute(
        request,
        submission_id="submission_1",
        submitted_at="2026-06-23T00:00:03Z",
    )

    assert submission.schema_version == "phase3.execution_submission.v1"
    assert submission.request_id == "request_1"
    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is not None
    assert submission.parsed_output_ref is not None
    assert submission.candidate_output_refs["answer"] == submission.parsed_output_ref
    assert store.verify(prompt_ref)
    assert store.verify(submission.raw_output_ref)
    assert store.verify(submission.parsed_output_ref)
