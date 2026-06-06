from hashlib import sha256

from tokenshare.storage.artifacts import ArtifactStore


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
