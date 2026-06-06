"""Local filesystem ArtifactStore for Phase 1."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from tokenshare.core.models import ArtifactRef, JsonObject


class ArtifactStore:
    """Persist artifacts under a local ``artifacts/`` directory.

    The event ledger stores only ``ArtifactRef`` snapshots. The bytes remain in
    this store so replay can verify content hashes without embedding large
    payloads in JSONL events.
    """

    def __init__(self, root_path: str | Path, *, artifact_dir_name: str = "artifacts") -> None:
        self.root_path = Path(root_path)
        self.artifact_dir_name = artifact_dir_name
        self.artifact_dir = self.root_path / artifact_dir_name
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str,
        artifact_type: str,
        media_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
    ) -> ArtifactRef:
        """Write artifact bytes and return the protocol reference.

        Artifact ids are caller-provided in Phase 1 so tests and replay fixtures
        can be deterministic. If an id already exists with different content,
        we fail instead of silently overwriting historical evidence.
        """

        target = self.artifact_dir / artifact_id
        content_hash = _sha256_hash(data)
        if target.exists() and _sha256_hash(target.read_bytes()) != content_hash:
            raise ValueError(f"artifact_id already exists with different content: {artifact_id}")

        target.write_bytes(data)
        artifact_ref = ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=f"{self.artifact_dir_name}/{artifact_id}",
            content_hash=content_hash,
            size_bytes=len(data),
            media_type=media_type,
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
        )
        self._write_manifest(artifact_ref)
        return artifact_ref

    def save_json(
        self,
        data: JsonObject,
        *,
        artifact_id: str,
        artifact_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
    ) -> ArtifactRef:
        """Persist canonical JSON bytes for small structured artifacts."""

        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.save_bytes(
            encoded,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            media_type="application/json",
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
        )

    def read_bytes(self, artifact_ref: ArtifactRef) -> bytes:
        return self._resolve_uri(artifact_ref.uri).read_bytes()

    def verify(self, artifact_ref: ArtifactRef) -> bool:
        """Check both size and hash so truncated files are detected."""

        try:
            data = self.read_bytes(artifact_ref)
        except FileNotFoundError:
            return False
        return len(data) == artifact_ref.size_bytes and _sha256_hash(data) == artifact_ref.content_hash

    def _write_manifest(self, artifact_ref: ArtifactRef) -> None:
        manifest_path = self.artifact_dir / f"{artifact_ref.artifact_id}.manifest.json"
        manifest_path.write_text(
            json.dumps(artifact_ref.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _resolve_uri(self, uri: str) -> Path:
        """Resolve a stored URI without allowing path traversal.

        Phase 1 writes relative URIs such as ``artifacts/artifact_root_input``.
        ``file://`` is accepted for future compatibility, but paths still must
        stay under the configured store root.
        """

        parsed = urlparse(uri)
        if parsed.scheme == "file":
            candidate = Path(parsed.path)
        elif parsed.scheme == "":
            candidate = self.root_path / uri
        else:
            raise ValueError(f"unsupported artifact uri scheme: {parsed.scheme}")

        root = self.root_path.resolve()
        resolved = candidate.resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError(f"artifact uri escapes store root: {uri}")
        return resolved


def _sha256_hash(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"
