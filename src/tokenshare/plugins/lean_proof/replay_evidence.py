"""Replay-time evidence checks for persisted Lean checker artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tokenshare.core.models import ArtifactRef, JsonObject


class LeanReplayEvidenceError(ValueError):
    """Raised when persisted Lean evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class LeanReplayEvidenceResult:
    accepted: bool
    replay_no_checker_call: bool
    environment_digest: str
    checker_report_ref: ArtifactRef
    required_artifact_ids: tuple[str, ...]
    checker_report_status: str

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": "lean_proof.replay_evidence.v1",
            "accepted": self.accepted,
            "replay_no_checker_call": self.replay_no_checker_call,
            "environment_digest": self.environment_digest,
            "checker_report_ref": self.checker_report_ref.to_dict(),
            "required_artifact_ids": list(self.required_artifact_ids),
            "checker_report_status": self.checker_report_status,
        }


def verify_lean_replay_evidence(
    *,
    artifact_root: str | Path,
    checker_report_ref: ArtifactRef,
    expected_environment_digest: str,
) -> LeanReplayEvidenceResult:
    """Verify persisted Lean checker evidence without re-running Lean.

    Replay is allowed to inspect immutable artifacts and hashes only. It must
    not call the checker, helper, executor, lake, lean, or AI path.
    """

    root = Path(artifact_root)
    report_body = _read_json_artifact(root, checker_report_ref, label="checker report")
    environment_ref = report_body.get("environment_ref")
    if not isinstance(environment_ref, dict):
        raise LeanReplayEvidenceError("missing environment ref in checker report")
    environment_digest = str(environment_ref.get("environment_digest", ""))
    if environment_digest != expected_environment_digest:
        raise LeanReplayEvidenceError("environment digest mismatch")

    stdout_ref = _artifact_ref_from_report(report_body, "stdout_ref", "missing checker log ref")
    stderr_ref = _artifact_ref_from_report(report_body, "stderr_ref", "missing checker log ref")
    generated_source_ref = _artifact_ref_from_report(
        report_body,
        "generated_source_ref",
        "missing generated source ref",
    )
    required_refs = [stdout_ref, stderr_ref, generated_source_ref, checker_report_ref]
    proof_ref = _optional_artifact_ref(report_body.get("proof_artifact_ref"))
    if proof_ref is not None:
        required_refs.append(proof_ref)

    for ref in required_refs:
        if not _verify_ref(root, ref):
            if ref.artifact_id in {stdout_ref.artifact_id, stderr_ref.artifact_id}:
                raise LeanReplayEvidenceError("missing checker log artifact")
            raise LeanReplayEvidenceError(f"missing or corrupt artifact: {ref.artifact_id}")

    status = str(report_body.get("status", ""))
    return LeanReplayEvidenceResult(
        accepted=status == "accepted",
        replay_no_checker_call=True,
        environment_digest=environment_digest,
        checker_report_ref=checker_report_ref,
        required_artifact_ids=tuple(ref.artifact_id for ref in required_refs),
        checker_report_status=status,
    )


def _read_json_artifact(root: Path, ref: ArtifactRef, *, label: str) -> JsonObject:
    if not _verify_ref(root, ref):
        raise LeanReplayEvidenceError(f"missing or corrupt {label} artifact")
    try:
        data = json.loads((root / ref.artifact_id).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LeanReplayEvidenceError(f"invalid {label} JSON") from exc
    if not isinstance(data, dict):
        raise LeanReplayEvidenceError(f"{label} artifact must contain a JSON object")
    return data


def _artifact_ref_from_report(
    report_body: JsonObject,
    field_name: str,
    missing_message: str,
) -> ArtifactRef:
    return _required_artifact_ref(report_body.get(field_name), missing_message)


def _required_artifact_ref(value: object, missing_message: str) -> ArtifactRef:
    ref = _optional_artifact_ref(value)
    if ref is None:
        raise LeanReplayEvidenceError(missing_message)
    return ref


def _optional_artifact_ref(value: object) -> ArtifactRef | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LeanReplayEvidenceError("artifact ref must be an object")
    return ArtifactRef.from_dict(value)


def _verify_ref(root: Path, ref: ArtifactRef) -> bool:
    path = root / ref.artifact_id
    if not path.is_file():
        return False
    data = path.read_bytes()
    if len(data) != ref.size_bytes:
        return False
    return _sha256(data) == ref.content_hash


def _sha256(data: bytes) -> str:
    from hashlib import sha256

    return f"sha256:{sha256(data).hexdigest()}"
