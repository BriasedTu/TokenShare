"""Deterministic local mock AI executor for Phase 3 contract tests."""

from __future__ import annotations

from dataclasses import dataclass

from tokenshare.core.models import JsonObject
from tokenshare.executors.contracts import ExecutionRequest, ExecutionSubmission
from tokenshare.storage.artifacts import ArtifactStore


@dataclass(frozen=True)
class MockAIExecutorProfile:
    """Deterministic output fixture used instead of a production AI API."""

    raw_text: str
    parsed_output: JsonObject | None = None
    result_kind: str = "succeeded"


class MockAIExecutor:
    """Executor that persists AI-style raw and parsed output artifacts."""

    def __init__(
        self,
        *,
        executor_id: str,
        executor_version: str,
        artifact_store: ArtifactStore,
        profile: MockAIExecutorProfile,
    ) -> None:
        self.executor_id = executor_id
        self.executor_version = executor_version
        self._artifact_store = artifact_store
        self._profile = profile

    def execute(
        self,
        request: ExecutionRequest,
        *,
        submission_id: str,
        submitted_at: str,
    ) -> ExecutionSubmission:
        raw_output_ref = self._artifact_store.save_bytes(
            self._profile.raw_text.encode("utf-8"),
            artifact_id=f"raw_model_output_{submission_id}",
            artifact_type="RawModelOutput",
            media_type="text/plain",
            artifact_schema_id="phase3.raw_model_output",
            artifact_schema_version="v1",
            source={"kind": "mock_ai_executor", "request_id": request.request_id},
            metadata={"executor_id": self.executor_id},
            created_at=submitted_at,
        )
        parsed_output_ref = None
        candidate_output_refs = {}
        parse_failure_ref = None
        result_kind = self._profile.result_kind
        if self._profile.parsed_output is not None:
            parsed_output_ref = self._artifact_store.save_json(
                self._profile.parsed_output,
                artifact_id=f"parsed_model_output_{submission_id}",
                artifact_type="ParsedModelOutput",
                artifact_schema_id="phase3.parsed_model_output",
                artifact_schema_version="v1",
                source={"kind": "mock_ai_executor", "raw_output_ref": raw_output_ref.to_dict()},
                metadata={"executor_id": self.executor_id},
                created_at=submitted_at,
            )
            candidate_output_refs = {
                name: parsed_output_ref for name in request.output_contract.required_outputs
            }
        elif result_kind == "succeeded":
            result_kind = "parse_failed"
            parse_failure_ref = self._artifact_store.save_json(
                {
                    "schema_version": "phase3.parse_failure_report.v1",
                    "submission_id": submission_id,
                    "raw_output_ref": raw_output_ref.to_dict(),
                    "reason": "parsed_output_missing",
                    "missing_outputs": list(request.output_contract.required_outputs),
                },
                artifact_id=f"parse_failure_{submission_id}",
                artifact_type="ParseFailureReport",
                artifact_schema_id="phase3.parse_failure_report",
                artifact_schema_version="v1",
                source={"kind": "mock_ai_executor", "request_id": request.request_id},
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
            result_kind=result_kind,
            raw_output_ref=raw_output_ref,
            parsed_output_ref=parsed_output_ref,
            candidate_output_refs=candidate_output_refs,
            parse_failure_ref=parse_failure_ref,
            log_ref=None,
            environment_ref=request.environment_ref,
            environment_summary={
                "runtime": request.environment_ref.runtime,
                "fixture_profile_digest": request.environment_ref.fixture_profile_digest,
            },
            provenance_ref=None,
            usage_summary={"mock": True},
            error=None if result_kind == "succeeded" else {"kind": result_kind},
            submitted_at=submitted_at,
        )
