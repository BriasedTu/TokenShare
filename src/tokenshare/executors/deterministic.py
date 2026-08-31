"""Deterministic local executor boundary for Phase 3."""

from __future__ import annotations

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.storage.artifacts import ArtifactStore


class DeterministicLocalExecutor:
    """Local executor that returns a fixed structured output.

    This is a contract boundary, not a plugin-domain solver. It proves that
    non-AI executors use the same request/submission envelope as mock AI.
    """

    def __init__(
        self,
        *,
        executor_id: str,
        executor_version: str,
        artifact_store: ArtifactStore,
        output: JsonObject,
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._output = dict(output)

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        parsed_output_ref = self._artifact_store.save_json(
            self._output,
            artifact_id=f"deterministic_output_{submission_id}",
            artifact_type="ParsedModelOutput",
            artifact_schema_id="phase3.parsed_model_output",
            artifact_schema_version="v1",
            source={"kind": "deterministic_local_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )
        return ExecutionSubmission(
            submission_id=submission_id,
            request_id=request.request_id,
            task_id=request.task_id,
            unit_id=request.unit_id,
            attempt_id=request.attempt_id,
            lease_id=request.lease_id,
            fencing_token=request.fencing_token,
            executor_id=self.executor_id,
            executor_version=self.executor_version,
            result_kind="succeeded",
            raw_output_ref=None,
            parsed_output_ref=parsed_output_ref,
            candidate_output_refs={
                name: parsed_output_ref for name in request.output_contract.required_outputs
            },
            parse_failure_ref=None,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={
                "runtime": request.environment_ref.runtime,
                "deterministic": True,
            },
            provenance_ref=None,
            usage_summary={"deterministic": True},
            error=None,
            submitted_at=submitted_at,
        )
