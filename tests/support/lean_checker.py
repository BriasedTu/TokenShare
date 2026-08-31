from __future__ import annotations

import json
from dataclasses import replace

from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerReport,
    LeanCheckerRequest,
    LeanCheckerStatus,
    render_lean_source,
)
from tokenshare.plugins.lean_proof.models import (
    LeanTheoremPayload,
    canonical_json_digest,
)


class RecordingLeanChecker:
    """Schema-compatible checker spy; acceptance is explicit test behavior."""

    def __init__(self, *, status: LeanCheckerStatus = LeanCheckerStatus.ACCEPTED):
        self.status = status
        self.requests: list[LeanCheckerRequest] = []

    @classmethod
    def reject_all(cls) -> "RecordingLeanChecker":
        return cls(status=LeanCheckerStatus.REJECTED)

    @property
    def modes(self) -> list[LeanCheckerMode]:
        return [request.checker_mode for request in self.requests]

    def __call__(
        self,
        request: LeanCheckerRequest,
        *,
        artifact_store,
        environment_manifest,
    ) -> LeanCheckerReport:
        del environment_manifest
        self.requests.append(request)
        payload = LeanTheoremPayload.from_dict(
            json.loads(
                artifact_store.read_bytes(request.theorem_payload_ref).decode("utf-8")
            )
        )
        proof_body = json.loads(
            artifact_store.read_bytes(request.proof_candidate_ref).decode("utf-8")
        )
        proof_source = str(proof_body["proof_source"])
        generated_source = render_lean_source(payload, proof_source)
        normalized_theorem_digest = canonical_json_digest(
            {
                "theorem_name": payload.theorem_name,
                "imports": payload.imports,
                "namespace": payload.namespace,
                "parameters_source": payload.parameters_source,
                "statement_source": payload.statement_source,
            }
        )
        proof_digest = canonical_json_digest(
            {
                "theorem_payload_digest": payload.payload_digest,
                "proof_source": proof_source,
            }
        )
        safe_id = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in request.request_id
        )
        source = {"kind": "lean_checker_test_fake", "request_id": request.request_id}
        generated_source_ref = artifact_store.save_bytes(
            generated_source.encode("utf-8"),
            artifact_id=f"{safe_id}_generated_source",
            artifact_type="LeanGeneratedSource",
            media_type="text/x-lean",
            artifact_schema_id="lean_proof.generated_source",
            artifact_schema_version="v1",
            source=source,
            metadata={"checker_mode": request.checker_mode.value},
            created_at=request.created_at,
        )
        stdout_ref = artifact_store.save_bytes(
            b"test fake checker\n",
            artifact_id=f"{safe_id}_stdout",
            artifact_type="LeanCheckerStdout",
            media_type="text/plain",
            artifact_schema_id="lean_proof.checker_stdout",
            artifact_schema_version="v1",
            source=source,
            metadata={"checker_mode": request.checker_mode.value},
            created_at=request.created_at,
        )
        stderr_ref = artifact_store.save_bytes(
            b"",
            artifact_id=f"{safe_id}_stderr",
            artifact_type="LeanCheckerStderr",
            media_type="text/plain",
            artifact_schema_id="lean_proof.checker_stderr",
            artifact_schema_version="v1",
            source=source,
            metadata={"checker_mode": request.checker_mode.value},
            created_at=request.created_at,
        )
        proof_artifact_ref = None
        if self.status == LeanCheckerStatus.ACCEPTED:
            proof_artifact_ref = artifact_store.save_bytes(
                proof_source.encode("utf-8"),
                artifact_id=f"{safe_id}_proof_artifact",
                artifact_type="LeanProofArtifact",
                media_type="text/x-lean",
                artifact_schema_id="lean_proof.proof_artifact",
                artifact_schema_version="v1",
                source=source,
                metadata={
                    "checker_mode": request.checker_mode.value,
                    "proof_digest": proof_digest,
                },
                created_at=request.created_at,
            )
        report = LeanCheckerReport(
            report_id=f"lean_checker_test_report:{safe_id}",
            request_id=request.request_id,
            status=self.status,
            exit_code=0 if self.status == LeanCheckerStatus.ACCEPTED else 1,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            generated_source_ref=generated_source_ref,
            proof_artifact_ref=proof_artifact_ref,
            report_ref=None,
            diagnostics={
                "failure_kind": (
                    None
                    if self.status == LeanCheckerStatus.ACCEPTED
                    else "test_fake_rejection"
                ),
                "combined_excerpt": "test fake checker",
            },
            normalized_theorem_digest=normalized_theorem_digest,
            proof_digest=(
                proof_digest if self.status == LeanCheckerStatus.ACCEPTED else None
            ),
            environment_ref=request.environment_ref,
            command_summary={"backend": "test_fake", "args": []},
            duration_ms=0,
        )
        report_ref = artifact_store.save_json(
            report.to_dict(include_report_ref=False),
            artifact_id=f"{safe_id}_checker_report",
            artifact_type="LeanCheckerReport",
            artifact_schema_id="lean_proof.checker_report",
            artifact_schema_version="v1",
            source=source,
            metadata={
                "checker_mode": request.checker_mode.value,
                "status": self.status.value,
                "backend": "test_fake",
            },
            created_at=request.created_at,
        )
        return replace(report, report_ref=report_ref)
