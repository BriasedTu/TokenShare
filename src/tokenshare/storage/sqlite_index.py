"""SQLite materialized indexes rebuilt from ledger events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tokenshare.storage.events import EventType, LedgerEvent


class SQLiteMaterializedIndex:
    """Rebuildable query tables for Phase 1.

    SQLite is deliberately not an authority. ``rebuild_from_events`` clears and
    reconstructs every table from JSONL events, matching the replay boundary in
    the Phase 1 field spec.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def rebuild_from_events(self, events: list[LedgerEvent]) -> None:
        with sqlite3.connect(self.path) as connection:
            self._reset_schema(connection)
            for event in events:
                self._insert_event(connection, event)
                self._insert_materialized_payload(connection, event)
            connection.commit()

    def _reset_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            drop table if exists ledger_events;
            drop table if exists task_specs;
            drop table if exists task_units;
            drop table if exists task_relations;
            drop table if exists artifact_refs;
            drop table if exists client_records;
            drop table if exists leases;
            drop table if exists attempts;
            drop table if exists recovery_actions;

            create table ledger_events (
                event_seq integer primary key,
                event_id text not null unique,
                event_type text not null,
                task_id text,
                object_type text not null,
                object_id text not null,
                occurred_at text not null,
                event_hash text not null,
                payload_json text not null
            );

            create table task_specs (
                task_id text primary key,
                plugin_id text,
                plugin_version text,
                created_at text,
                payload_json text not null
            );

            create table task_units (
                unit_id text primary key,
                task_id text,
                parent_unit_id text,
                state text,
                depth integer,
                created_at text,
                updated_at text,
                last_state_reason text,
                payload_json text not null
            );

            create table task_relations (
                relation_id text primary key,
                task_id text,
                relation_type text,
                source_unit_id text,
                target_unit_id text,
                payload_json text not null
            );

            create table artifact_refs (
                artifact_id text primary key,
                artifact_type text,
                uri text,
                content_hash text,
                artifact_schema_id text,
                created_at text,
                payload_json text not null
            );

            create table client_records (
                client_id text primary key,
                executor_type text,
                status text,
                last_seen_at text,
                payload_json text not null
            );

            create table leases (
                lease_id text primary key,
                task_id text,
                unit_id text,
                attempt_id text,
                client_id text,
                state text,
                fencing_token text,
                issued_at text,
                expires_at text,
                last_heartbeat_at text,
                heartbeat_count integer,
                lease_kind text,
                terminated_at text,
                terminated_reason text,
                payload_json text not null
            );

            create table attempts (
                attempt_id text primary key,
                task_id text,
                unit_id text,
                lease_id text,
                client_id text,
                state text,
                attempt_kind text,
                created_at text,
                started_at text,
                submitted_at text,
                finished_at text,
                failure_kind text,
                failure_reason text,
                superseded_by_attempt_id text,
                payload_json text not null
            );

            create table recovery_actions (
                recovery_action_id text primary key,
                task_id text,
                unit_id text,
                trigger text,
                lease_id text,
                attempt_id text,
                retry_count integer,
                retry_allowed integer,
                reason text,
                created_at text,
                payload_json text not null
            );
            """
        )

    def _insert_event(self, connection: sqlite3.Connection, event: LedgerEvent) -> None:
        connection.execute(
            """
            insert into ledger_events (
                event_seq, event_id, event_type, task_id, object_type, object_id,
                occurred_at, event_hash, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_seq,
                event.event_id,
                _event_type_value(event.event_type),
                event.task_id,
                event.object_type,
                event.object_id,
                event.occurred_at,
                event.event_hash,
                _payload_json(event.payload),
            ),
        )

    def _insert_materialized_payload(
        self, connection: sqlite3.Connection, event: LedgerEvent
    ) -> None:
        event_type = _event_type_value(event.event_type)
        if event_type == EventType.TASK_REGISTERED.value:
            task_spec = event.payload["task_spec"]
            connection.execute(
                """
                insert or replace into task_specs (
                    task_id, plugin_id, plugin_version, created_at, payload_json
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    task_spec["task_id"],
                    task_spec.get("plugin_id"),
                    task_spec.get("plugin_version"),
                    task_spec.get("created_at"),
                    _payload_json(task_spec),
                ),
            )
        elif event_type == EventType.TASK_UNIT_CREATED.value:
            task_unit = event.payload["task_unit"]
            connection.execute(
                """
                insert or replace into task_units (
                    unit_id, task_id, parent_unit_id, state, depth, created_at,
                    updated_at, last_state_reason, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_unit["unit_id"],
                    task_unit.get("task_id"),
                    task_unit.get("parent_unit_id"),
                    task_unit.get("state"),
                    task_unit.get("depth"),
                    task_unit.get("created_at"),
                    task_unit.get("updated_at"),
                    None,
                    _payload_json(task_unit),
                ),
            )
        elif event_type == EventType.TASK_UNIT_STATE_CHANGED.value:
            state_change = event.payload["task_unit_state_change"]
            task_unit = event.payload.get("task_unit")
            if task_unit is not None:
                connection.execute(
                    """
                    insert or replace into task_units (
                        unit_id, task_id, parent_unit_id, state, depth, created_at,
                        updated_at, last_state_reason, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_unit["unit_id"],
                        task_unit.get("task_id"),
                        task_unit.get("parent_unit_id"),
                        task_unit.get("state"),
                        task_unit.get("depth"),
                        task_unit.get("created_at"),
                        state_change.get("changed_at", task_unit.get("updated_at")),
                        state_change.get("reason"),
                        _payload_json(task_unit),
                    ),
                )
            else:
                connection.execute(
                    """
                    insert into task_units (
                        unit_id, task_id, parent_unit_id, state, depth, created_at,
                        updated_at, last_state_reason, payload_json
                    ) values (?, ?, null, ?, null, null, ?, ?, ?)
                    on conflict(unit_id) do update set
                        state = excluded.state,
                        updated_at = excluded.updated_at,
                        last_state_reason = excluded.last_state_reason,
                        payload_json = excluded.payload_json
                    """,
                    (
                        state_change["unit_id"],
                        state_change.get("task_id"),
                        state_change.get("new_state"),
                        state_change.get("changed_at"),
                        state_change.get("reason"),
                        _payload_json(state_change),
                    ),
                )
        elif event_type == EventType.TASK_RELATION_CREATED.value:
            relation = event.payload["task_relation"]
            connection.execute(
                """
                insert or replace into task_relations (
                    relation_id, task_id, relation_type, source_unit_id,
                    target_unit_id, payload_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    relation["relation_id"],
                    relation.get("task_id"),
                    relation.get("relation_type"),
                    relation.get("source_unit_id"),
                    relation.get("target_unit_id"),
                    _payload_json(relation),
                ),
            )
        elif event_type == EventType.ARTIFACT_STORED.value:
            artifact_ref = event.payload["artifact_ref"]
            connection.execute(
                """
                insert or replace into artifact_refs (
                    artifact_id, artifact_type, uri, content_hash,
                    artifact_schema_id, created_at, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_ref["artifact_id"],
                    artifact_ref.get("artifact_type"),
                    artifact_ref.get("uri"),
                    artifact_ref.get("content_hash"),
                    artifact_ref.get("artifact_schema_id"),
                    artifact_ref.get("created_at"),
                    _payload_json(artifact_ref),
                ),
            )
        elif event_type == EventType.CLIENT_REGISTERED.value:
            client_record = event.payload["client_record"]
            connection.execute(
                """
                insert or replace into client_records (
                    client_id, executor_type, status, last_seen_at, payload_json
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    client_record["client_id"],
                    client_record.get("executor_type"),
                    client_record.get("status"),
                    client_record.get("last_seen_at"),
                    _payload_json(client_record),
                ),
            )
        elif event_type == EventType.CLIENT_STATE_CHANGED.value:
            client_record = event.payload.get("client_record")
            state_change = event.payload.get("client_state_change", {})
            if client_record is not None:
                connection.execute(
                    """
                    insert or replace into client_records (
                        client_id, executor_type, status, last_seen_at, payload_json
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (
                        client_record["client_id"],
                        client_record.get("executor_type"),
                        client_record.get("status"),
                        client_record.get("last_seen_at"),
                        _payload_json(client_record),
                    ),
                )
            else:
                connection.execute(
                    """
                    update client_records
                    set status = ?, last_seen_at = ?, payload_json = ?
                    where client_id = ?
                    """,
                    (
                        state_change.get("new_status"),
                        state_change.get("changed_at"),
                        _payload_json(state_change),
                        state_change.get("client_id"),
                    ),
                )
        elif event_type == EventType.LEASE_STATE_CHANGED.value:
            lease = event.payload["lease"]
            connection.execute(
                """
                insert or replace into leases (
                    lease_id, task_id, unit_id, attempt_id, client_id, state,
                    fencing_token, issued_at, expires_at, last_heartbeat_at,
                    heartbeat_count, lease_kind, terminated_at, terminated_reason,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease["lease_id"],
                    lease.get("task_id"),
                    lease.get("unit_id"),
                    lease.get("attempt_id"),
                    lease.get("client_id"),
                    lease.get("state"),
                    lease.get("fencing_token"),
                    lease.get("issued_at"),
                    lease.get("expires_at"),
                    lease.get("last_heartbeat_at"),
                    lease.get("heartbeat_count"),
                    lease.get("lease_kind"),
                    lease.get("terminated_at"),
                    lease.get("terminated_reason"),
                    _payload_json(lease),
                ),
            )
        elif event_type == EventType.ATTEMPT_STATE_CHANGED.value:
            attempt = event.payload["attempt"]
            connection.execute(
                """
                insert or replace into attempts (
                    attempt_id, task_id, unit_id, lease_id, client_id, state,
                    attempt_kind, created_at, started_at, submitted_at, finished_at,
                    failure_kind, failure_reason, superseded_by_attempt_id,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["attempt_id"],
                    attempt.get("task_id"),
                    attempt.get("unit_id"),
                    attempt.get("lease_id"),
                    attempt.get("client_id"),
                    attempt.get("state"),
                    attempt.get("attempt_kind"),
                    attempt.get("created_at"),
                    attempt.get("started_at"),
                    attempt.get("submitted_at"),
                    attempt.get("finished_at"),
                    attempt.get("failure_kind"),
                    attempt.get("failure_reason"),
                    attempt.get("superseded_by_attempt_id"),
                    _payload_json(attempt),
                ),
            )
        elif event_type == EventType.RECOVERY_ACTION_RECORDED.value:
            recovery_action = event.payload["recovery_action"]
            connection.execute(
                """
                insert or replace into recovery_actions (
                    recovery_action_id, task_id, unit_id, trigger, lease_id,
                    attempt_id, retry_count, retry_allowed, reason, created_at,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recovery_action["recovery_action_id"],
                    recovery_action.get("task_id"),
                    recovery_action.get("unit_id"),
                    recovery_action.get("trigger"),
                    recovery_action.get("lease_id"),
                    recovery_action.get("attempt_id"),
                    recovery_action.get("retry_count"),
                    int(bool(recovery_action.get("retry_allowed"))),
                    recovery_action.get("reason"),
                    recovery_action.get("created_at"),
                    _payload_json(recovery_action),
                ),
            )


def _payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_type_value(value: EventType | str) -> str:
    return value.value if isinstance(value, EventType) else value
