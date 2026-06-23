import sqlite3

from tests.phase2_fixtures import make_artifact_ref
from tokenshare.storage.events import EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex


def test_sqlite_index_rebuilds_phase3_indexes_without_storing_full_bodies(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    registry_ref = make_artifact_ref("registry_snapshot_1")
    request_ref = make_artifact_ref("request_1")
    submission_ref = make_artifact_ref("submission_1")

    ledger.append(
        event_type=EventType.REGISTRY_SNAPSHOT_RECORDED,
        object_type="RegistrySnapshot",
        object_id="registry_snapshot_1",
        task_id="task_demo",
        payload={
            "schema_version": "phase3.registry_snapshot_record.v1",
            "registry_snapshot_id": "registry_snapshot_1",
            "task_id": "task_demo",
            "registry_snapshot_ref": registry_ref.to_dict(),
            "registry_snapshot_digest": registry_ref.content_hash,
            "plugin_entries": [
                {
                    "plugin_id": "structured_report_stub",
                    "plugin_version": "0.1.0",
                    "descriptor_digest": "sha256:plugin",
                }
            ],
            "executor_entries": [
                {
                    "executor_id": "executor_mock_ai",
                    "executor_version": "0.1.0",
                    "status": "Available",
                    "descriptor_digest": "sha256:executor",
                }
            ],
            "frozen_at": "2026-06-23T00:00:00Z",
        },
        idempotency_key="registry_snapshot:registry_snapshot_1",
        occurred_at="2026-06-23T00:00:00Z",
    )
    ledger.append(
        event_type=EventType.EXECUTION_REQUEST_RECORDED,
        object_type="ExecutionRequest",
        object_id="request_1",
        task_id="task_demo",
        payload={
            "schema_version": "phase3.execution_request_record.v1",
            "request_id": "request_1",
            "task_id": "task_demo",
            "unit_id": "unit_ready",
            "attempt_id": "attempt_1",
            "lease_id": "lease_1",
            "request_ref": request_ref.to_dict(),
            "request_digest": request_ref.content_hash,
            "plugin_id": "structured_report_stub",
            "executor_id": "executor_mock_ai",
            "created_at": "2026-06-23T00:00:02Z",
        },
        idempotency_key="execution_request:request_1",
        occurred_at="2026-06-23T00:00:02Z",
    )
    ledger.append(
        event_type=EventType.EXECUTION_SUBMISSION_RECORDED,
        object_type="ExecutionSubmission",
        object_id="submission_1",
        task_id="task_demo",
        payload={
            "schema_version": "phase3.execution_submission_record.v1",
            "submission_id": "submission_1",
            "request_id": "request_1",
            "task_id": "task_demo",
            "unit_id": "unit_ready",
            "attempt_id": "attempt_1",
            "lease_id": "lease_1",
            "submission_ref": submission_ref.to_dict(),
            "submission_digest": submission_ref.content_hash,
            "result_kind": "succeeded",
            "submitted_at": "2026-06-23T00:00:03Z",
        },
        idempotency_key="execution_submission:submission_1",
        occurred_at="2026-06-23T00:00:03Z",
    )

    index = SQLiteMaterializedIndex(tmp_path / "tokenshare.sqlite")
    index.rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "tokenshare.sqlite") as connection:
        registry_row = connection.execute(
            """
            select task_id, registry_snapshot_digest, frozen_at
            from registry_snapshots
            where registry_snapshot_id = ?
            """,
            ("registry_snapshot_1",),
        ).fetchone()
        request_row = connection.execute(
            """
            select unit_id, attempt_id, request_artifact_id, plugin_id, executor_id
            from execution_requests
            where request_id = ?
            """,
            ("request_1",),
        ).fetchone()
        submission_row = connection.execute(
            """
            select request_id, attempt_id, submission_artifact_id, result_kind
            from execution_submissions
            where submission_id = ?
            """,
            ("submission_1",),
        ).fetchone()
        status_row = connection.execute(
            """
            select executor_version, status, descriptor_digest
            from executor_statuses
            where executor_id = ?
            """,
            ("executor_mock_ai",),
        ).fetchone()

    assert registry_row == ("task_demo", registry_ref.content_hash, "2026-06-23T00:00:00Z")
    assert request_row == (
        "unit_ready",
        "attempt_1",
        request_ref.artifact_id,
        "structured_report_stub",
        "executor_mock_ai",
    )
    assert submission_row == ("request_1", "attempt_1", submission_ref.artifact_id, "succeeded")
    assert status_row == ("0.1.0", "Available", "sha256:executor")
