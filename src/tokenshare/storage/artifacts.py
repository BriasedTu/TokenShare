"""Local filesystem ArtifactStore for Phase 1."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

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
        durability_hook: Callable[[str], None] | None = None,
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

        self._commit_bytes(
            target,
            data,
            marker_path=self.artifact_dir / f"{artifact_id}.commit.json",
            durability_hook=durability_hook,
        )
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

    def save_content_addressed_bytes(
        self,
        data: bytes,
        *,
        artifact_type: str,
        media_type: str,
        artifact_schema_id: str,
        artifact_schema_version: str,
        source: JsonObject,
        metadata: JsonObject,
        created_at: str,
        durability_hook: Callable[[str], None] | None = None,
    ) -> ArtifactRef:
        """以内容摘要命名并耐久提交一个不可变对象。"""

        content_hash = _sha256_hash(data)
        return self.save_bytes(
            data,
            artifact_id=content_hash.removeprefix("sha256:"),
            artifact_type=artifact_type,
            media_type=media_type,
            artifact_schema_id=artifact_schema_id,
            artifact_schema_version=artifact_schema_version,
            source=source,
            metadata=metadata,
            created_at=created_at,
            durability_hook=durability_hook,
        )

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

    def save_external_trace_wrapper(
        self,
        data: JsonObject,
        *,
        artifact_id: str,
        created_at: str,
    ) -> ArtifactRef:
        """只保存 current wrapper；拒绝把 source-bank bytes/位置带入当前 store。"""

        forbidden = {
            "path",
            "root_path",
            "uri",
            "artifact_ref",
            "bank_owned_artifact_ref",
            "source_bytes",
        }
        present = forbidden.intersection(data)
        if present or any(isinstance(value, (bytes, bytearray)) for value in data.values()):
            raise ValueError("current store cannot materialize source bank object")
        return self.save_json(
            data,
            artifact_id=artifact_id,
            artifact_type="CurrentTraceWrapper",
            artifact_schema_id="tokenshare.current_trace_wrapper",
            artifact_schema_version="v1",
            source={"kind": "external_response_bank_locator"},
            metadata={},
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
        manifest_bytes = json.dumps(
            artifact_ref.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._commit_bytes(
            manifest_path,
            manifest_bytes,
            marker_path=manifest_path.with_name(f"{manifest_path.name}.commit.json"),
            durability_hook=None,
        )

    def load_artifact_ref(self, artifact_id: str) -> ArtifactRef:
        manifest_path = self.artifact_dir / f"{artifact_id}.manifest.json"
        manifest_bytes = self._validate_commit_marker(
            manifest_path,
            manifest_path.with_name(f"{manifest_path.name}.commit.json"),
        )
        body = json.loads(manifest_bytes.decode("utf-8"))
        ref = ArtifactRef.from_dict(body)
        if ref.artifact_id != artifact_id or not self.verify(ref):
            raise ValueError(f"artifact commit is not valid: {artifact_id}")
        self._validate_commit_marker(
            self.artifact_dir / artifact_id,
            self.artifact_dir / f"{artifact_id}.commit.json",
        )
        return ref

    def _commit_bytes(
        self,
        target: Path,
        data: bytes,
        *,
        marker_path: Path,
        durability_hook: Callable[[str], None] | None,
    ) -> None:
        """temp/flush/fsync/rename/marker 顺序提交；已 rename 对象可补 marker。"""

        target.parent.mkdir(parents=True, exist_ok=True)
        expected_hash = _sha256_hash(data)
        if target.exists():
            if _sha256_hash(target.read_bytes()) != expected_hash:
                raise ValueError(
                    f"immutable target already exists with different content: {target.name}"
                )
            self._ensure_commit_marker(target, marker_path, expected_hash, len(data))
            self._cleanup_stale_temps(target)
            return

        temp_path = target.parent / (
            f".{target.name}.tmp.{os.getpid()}.{uuid4().hex}"
        )
        with temp_path.open("xb") as stream:
            stream.write(data)
            _notify_durability(durability_hook, "artifact_temp_written")
            stream.flush()
            _notify_durability(durability_hook, "artifact_flushed")
            os.fsync(stream.fileno())
            _notify_durability(durability_hook, "artifact_fsynced")
        os.replace(temp_path, target)
        _notify_durability(durability_hook, "artifact_renamed")
        self._ensure_commit_marker(target, marker_path, expected_hash, len(data))
        _notify_durability(durability_hook, "artifact_commit_marker_written")
        self._cleanup_stale_temps(target)

    def _ensure_commit_marker(
        self,
        target: Path,
        marker_path: Path,
        content_hash: str,
        size_bytes: int,
    ) -> None:
        marker = {
            "schema_version": "tokenshare.durable_file_commit.v1",
            "target_name": target.name,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
        }
        encoded = json.dumps(
            marker, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if marker_path.exists():
            if marker_path.read_bytes() != encoded:
                raise ValueError(f"durable commit marker mismatch: {marker_path.name}")
            return
        temp_marker = marker_path.parent / (
            f".{marker_path.name}.tmp.{os.getpid()}.{uuid4().hex}"
        )
        with temp_marker.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_marker, marker_path)

    def _validate_commit_marker(self, target: Path, marker_path: Path) -> bytes:
        if not target.exists():
            raise FileNotFoundError(target)
        if not marker_path.exists():
            raise ValueError(f"durable artifact commit is incomplete: {target.name}")
        data = target.read_bytes()
        self._ensure_commit_marker(
            target,
            marker_path,
            _sha256_hash(data),
            len(data),
        )
        return data

    @staticmethod
    def _cleanup_stale_temps(target: Path) -> None:
        for candidate in target.parent.glob(f".{target.name}.tmp.*"):
            try:
                candidate.unlink()
            except OSError:
                # 并发 writer 仍持有的 temp 不能作为已提交对象读取。
                continue

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


def _notify_durability(
    hook: Callable[[str], None] | None,
    stage: str,
) -> None:
    if hook is not None:
        hook(stage)
