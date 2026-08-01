from hashlib import sha256

import pytest

from tokenshare.storage.artifacts import ArtifactStore


def _save_test_artifact(store: ArtifactStore, *, artifact_id: str) -> None:
    store.save_bytes(
        b'{"n": 91}',
        artifact_id=artifact_id,
        artifact_type="root_input",
        media_type="application/json",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="1",
        source={"kind": "client_input"},
        metadata={"case": "phase1"},
        created_at="2026-06-06T00:00:00Z",
    )


def test_artifact_store_saves_reads_and_verifies_content_hash(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    payload = b'{"n": 91}'
    expected_hash = f"sha256:{sha256(payload).hexdigest()}"

    artifact_ref = store.save_bytes(
        payload,
        artifact_id="artifact_root_input",
        artifact_type="root_input",
        media_type="application/json",
        artifact_schema_id="factorization.root_input",
        artifact_schema_version="1",
        source={"kind": "client_input"},
        metadata={"case": "phase1"},
        created_at="2026-06-06T00:00:00Z",
    )

    assert artifact_ref.uri == "artifacts/artifact_root_input"
    assert artifact_ref.content_hash == expected_hash
    assert artifact_ref.size_bytes == len(payload)
    assert store.read_bytes(artifact_ref) == payload
    assert store.verify(artifact_ref)

    (tmp_path / artifact_ref.uri).write_bytes(b'{"n": 92}')

    assert not store.verify(artifact_ref)


def test_load_artifact_ref_rejects_missing_manifest_commit_marker(tmp_path) -> None:
    artifact_id = "artifact_missing_manifest_marker"
    store = ArtifactStore(tmp_path)
    _save_test_artifact(store, artifact_id=artifact_id)
    manifest_path = store.artifact_dir / f"{artifact_id}.manifest.json"
    manifest_path.with_name(f"{manifest_path.name}.commit.json").unlink()

    with pytest.raises(ValueError, match="durable artifact commit is incomplete"):
        ArtifactStore(tmp_path).load_artifact_ref(artifact_id)


def test_load_artifact_ref_rejects_corrupt_manifest_commit_marker(tmp_path) -> None:
    artifact_id = "artifact_corrupt_manifest_marker"
    store = ArtifactStore(tmp_path)
    _save_test_artifact(store, artifact_id=artifact_id)
    manifest_path = store.artifact_dir / f"{artifact_id}.manifest.json"
    manifest_path.with_name(f"{manifest_path.name}.commit.json").write_text(
        '{"corrupt":true}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="durable commit marker mismatch"):
        ArtifactStore(tmp_path).load_artifact_ref(artifact_id)


def test_manifest_rename_before_marker_crash_is_rejected_on_reopen(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_id = "artifact_manifest_rename_crash"
    store = ArtifactStore(tmp_path)
    ensure_commit_marker = store._ensure_commit_marker

    def crash_before_manifest_marker(target, marker_path, content_hash, size_bytes) -> None:
        if target.name.endswith(".manifest.json"):
            raise RuntimeError("simulated manifest marker crash")
        ensure_commit_marker(target, marker_path, content_hash, size_bytes)

    monkeypatch.setattr(store, "_ensure_commit_marker", crash_before_manifest_marker)

    with pytest.raises(RuntimeError, match="simulated manifest marker crash"):
        _save_test_artifact(store, artifact_id=artifact_id)

    manifest_path = store.artifact_dir / f"{artifact_id}.manifest.json"
    assert manifest_path.exists()
    assert not manifest_path.with_name(f"{manifest_path.name}.commit.json").exists()
    with pytest.raises(ValueError, match="durable artifact commit is incomplete"):
        ArtifactStore(tmp_path).load_artifact_ref(artifact_id)
