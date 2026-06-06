import sqlite3

from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex


def test_sqlite_index_rebuilds_query_tables_from_ledger_events(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    ledger.append(
        event_type=EventType.ARTIFACT_STORED,
        object_type="ArtifactRef",
        object_id="artifact_root_input",
        payload={
            "artifact_ref": {
                "artifact_id": "artifact_root_input",
                "artifact_type": "root_input",
                "uri": "artifacts/artifact_root_input",
                "content_hash": "sha256:abc123",
                "artifact_schema_id": "factorization.root_input",
                "created_at": "2026-06-06T00:00:00Z",
            }
        },
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="artifact:sha256:abc123",
        occurred_at="2026-06-06T00:00:00Z",
    )
    ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={
            "task_spec": {
                "task_id": "task_demo",
                "plugin_id": "factorization",
                "plugin_version": "0.1.0",
                "created_at": "2026-06-06T00:00:01Z",
            }
        },
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-06T00:00:01Z",
    )

    index = SQLiteMaterializedIndex(tmp_path / "tokenshare.sqlite")
    index.rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "tokenshare.sqlite") as connection:
        task_row = connection.execute(
            "select task_id, plugin_id, plugin_version from task_specs"
        ).fetchone()
        artifact_row = connection.execute(
            "select artifact_id, artifact_type, content_hash from artifact_refs"
        ).fetchone()
        event_count = connection.execute("select count(*) from ledger_events").fetchone()[0]

    assert task_row == ("task_demo", "factorization", "0.1.0")
    assert artifact_row == ("artifact_root_input", "root_input", "sha256:abc123")
    assert event_count == 2
