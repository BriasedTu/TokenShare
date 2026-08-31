from tokenshare.executors.contracts import ExecutionRequest
from tokenshare.executors.deterministic import DeterministicLocalExecutor
from tokenshare.storage.artifacts import ArtifactStore

from tests.phase2_fixtures import make_unit
from tests.phase3_fixtures import make_environment_ref, make_output_contract


def test_deterministic_executor_uses_unified_request_without_ai_raw_output(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    request = ExecutionRequest(
        request_id="request_1",
        task_id="task_demo",
        unit_id="unit_ready",
        attempt_id="attempt_1",
        lease_id="lease_1",
        fencing_token="token_1",
        plugin={
            "plugin_id": "factorization_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_ref": {"artifact_id": "plugin_descriptor_1"},
            "plugin_descriptor_digest": "sha256:plugin",
        },
        executor={
            "executor_id": "executor_local",
            "executor_version": "0.1.0",
            "executor_descriptor_ref": {"artifact_id": "executor_descriptor_1"},
            "executor_descriptor_digest": "sha256:executor",
        },
        registry_snapshot_id="registry_snapshot_1",
        allocation_decision={
            "decision_id": "allocation_1",
            "selected_executor_id": "executor_local",
            "eligible_executor_ids": ["executor_local"],
        },
        capability_snapshot={"executor": "deterministic_local"},
        task_unit_snapshot=make_unit().to_dict(),
        input_artifact_refs={},
        output_contract=make_output_contract(),
        hard_requirements={"executor": "deterministic_local"},
        soft_hints={},
        environment_ref=make_environment_ref(),
        execution_instruction_ref=None,
        prompt_package_ref=None,
        limits={"timeout_seconds": 30},
        created_at="2026-06-23T00:00:02Z",
    )

    executor = DeterministicLocalExecutor(
        executor_id="executor_local",
        executor_version="0.1.0",
        artifact_store=store,
        output={"answer": "42"},
    )
    submission = executor.execute(
        request,
        submission_id="submission_1",
        submitted_at="2026-06-23T00:00:03Z",
    )

    assert submission.result_kind == "succeeded"
    assert submission.raw_output_ref is None
    assert submission.parsed_output_ref is not None
    assert submission.candidate_output_refs["answer"] == submission.parsed_output_ref
    assert store.verify(submission.parsed_output_ref)
