"""SQLite materialized indexes rebuilt from ledger events."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenshare.core.expansion import ExpectedOutputRef
from tokenshare.core.models import ArtifactRef
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventType, LedgerEvent


class SQLiteMaterializedIndex:
    """Rebuildable query tables for Phase 1.

    SQLite is deliberately not an authority. ``rebuild_from_events`` clears and
    reconstructs every table from JSONL events, matching the replay boundary in
    the Phase 1 field spec.
    """

    def __init__(
        self, path: str | Path, *, artifact_store: ArtifactStore | None = None
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._artifact_store = artifact_store

    def rebuild_from_events(self, events: list[LedgerEvent]) -> None:
        event_list = list(events)
        phase4_context = _build_phase4_projection_context(
            event_list,
            artifact_store=self._artifact_store,
        )
        with sqlite3.connect(self.path) as connection:
            self._reset_schema(connection)
            for event in event_list:
                self._insert_event(connection, event)
                self._insert_materialized_payload(
                    connection,
                    event,
                    phase4_context=phase4_context,
                )
            for expected_output in phase4_context.expected_output_refs:
                self._insert_expected_output_ref(connection, expected_output)
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
            drop table if exists registry_snapshots;
            drop table if exists execution_requests;
            drop table if exists execution_submissions;
            drop table if exists executor_statuses;
            drop table if exists verification_reports;
            drop table if exists canonical_outputs;
            drop table if exists split_strategy_invocations;
            drop table if exists decomposition_proposals;
            drop table if exists expansion_decisions;
            drop table if exists merge_plans;
            drop table if exists expected_output_refs;

            create table ledger_events (
                event_seq integer primary key,
                event_id text not null unique,
                event_type text not null,
                task_id text,
                object_type text not null,
                object_id text not null,
                occurred_at text not null,
                event_hash text not null,
                batch_id text,
                batch_index integer,
                batch_size integer,
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

            create table registry_snapshots (
                registry_snapshot_id text primary key,
                task_id text,
                registry_snapshot_artifact_id text,
                registry_snapshot_digest text,
                plugin_count integer,
                executor_count integer,
                frozen_at text,
                payload_json text not null
            );

            create table execution_requests (
                request_id text primary key,
                task_id text,
                unit_id text,
                attempt_id text,
                lease_id text,
                request_artifact_id text,
                request_digest text,
                plugin_id text,
                executor_id text,
                created_at text,
                payload_json text not null
            );

            create table execution_submissions (
                submission_id text primary key,
                request_id text,
                task_id text,
                unit_id text,
                attempt_id text,
                lease_id text,
                submission_artifact_id text,
                submission_digest text,
                result_kind text,
                submitted_at text,
                payload_json text not null
            );

            create table executor_statuses (
                executor_id text primary key,
                executor_version text,
                status text,
                descriptor_digest text,
                last_updated_at text,
                payload_json text not null
            );

            create table verification_reports (
                verification_report_id text primary key,
                task_id text,
                unit_id text,
                attempt_id text,
                submission_id text,
                submission_event_seq integer,
                status text,
                eligible_for_canonical integer,
                candidate_output_bundle_digest text,
                validator_policy_id text,
                plugin_id text,
                plugin_version text,
                verification_report_digest text,
                completed_at text,
                source_event_seq integer not null,
                payload_json text not null
            );

            create table canonical_outputs (
                canonical_selection_id text primary key,
                task_id text not null,
                unit_id text not null,
                selection_policy text,
                selection_policy_version text,
                selected_verification_report_id text,
                selected_verification_event_seq integer,
                selected_submission_id text,
                selected_submission_event_seq integer,
                selected_attempt_id text,
                canonical_output_bundle_digest text,
                bound_at text,
                source_event_seq integer not null,
                payload_json text not null
            );
            create unique index canonical_outputs_task_unit_unique
                on canonical_outputs(task_id, unit_id);

            create table split_strategy_invocations (
                invocation_id text primary key,
                invocation_attempt_no integer,
                task_id text,
                unit_id text,
                canonical_selection_id text,
                canonical_output_bundle_digest text,
                plugin_id text,
                plugin_version text,
                plugin_descriptor_digest text,
                split_strategy_id text,
                split_strategy_params_digest text,
                expansion_scope_hash text,
                status text,
                result_action text,
                result_digest text,
                error_kind text,
                error_summary text,
                started_at text,
                completed_at text,
                source_event_seq integer not null,
                payload_json text not null
            );

            create table decomposition_proposals (
                proposal_id text primary key,
                task_id text,
                parent_unit_id text,
                canonical_selection_id text,
                proposal_artifact_id text,
                proposal_digest text,
                expansion_scope_hash text,
                plugin_id text,
                plugin_version text,
                split_strategy_id text,
                child_count integer,
                dependency_edge_count integer,
                expected_output_count integer,
                merge_slot_count integer,
                created_at text,
                expansion_decision_id text,
                batch_id text,
                source_event_seq integer not null,
                visible integer not null,
                payload_json text not null
            );

            create table expansion_decisions (
                expansion_decision_id text primary key,
                task_id text,
                unit_id text,
                canonical_selection_id text,
                canonical_output_bundle_digest text,
                expansion_scope_hash text,
                action text,
                plugin_id text,
                plugin_version text,
                plugin_descriptor_digest text,
                split_strategy_id text,
                split_strategy_params_digest text,
                source_invocation_id text,
                proposal_id text,
                proposal_digest text,
                merge_plan_id text,
                merge_plan_digest text,
                decided_at text,
                batch_id text,
                source_event_seq integer not null,
                payload_json text not null
            );
            create unique index expansion_decisions_scope_unique
                on expansion_decisions(expansion_scope_hash);

            create table merge_plans (
                merge_plan_id text primary key,
                task_id text,
                parent_unit_id text,
                canonical_selection_id text,
                decomposition_proposal_id text,
                expansion_decision_id text,
                merge_plan_artifact_id text,
                merge_plan_digest text,
                merge_policy_id text,
                merge_policy_version text,
                required_slot_count integer,
                parent_output_mapping_count integer,
                created_at text,
                batch_id text,
                source_event_seq integer not null,
                visible integer not null,
                payload_json text not null
            );

            create table expected_output_refs (
                expected_output_id text primary key,
                task_id text,
                owner_unit_id text,
                output_name text,
                schema_ref_json text,
                resolution_kind text,
                resolution_status text,
                child_unit_id text,
                child_output_name text,
                merge_plan_id text,
                canonical_selection_id text,
                canonical_output_bundle_digest text,
                source_proposal_id text,
                source_expansion_decision_id text,
                created_event_seq integer,
                resolved_event_seq integer,
                payload_json text not null
            );
            """
        )

    def _insert_event(self, connection: sqlite3.Connection, event: LedgerEvent) -> None:
        connection.execute(
            """
            insert into ledger_events (
                event_seq, event_id, event_type, task_id, object_type, object_id,
                occurred_at, event_hash, batch_id, batch_index, batch_size, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                event.batch_id,
                event.batch_index,
                event.batch_size,
                _payload_json(event.payload),
            ),
        )

    def _insert_materialized_payload(
        self,
        connection: sqlite3.Connection,
        event: LedgerEvent,
        *,
        phase4_context: "_Phase4ProjectionContext",
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
            if _is_phase4_expansion_child_event(event) and not (
                event.batch_id in phase4_context.visible_expansion_batch_ids
            ):
                return
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
            if _is_phase4_expansion_child_event(event) and not (
                event.batch_id in phase4_context.visible_expansion_batch_ids
            ):
                return
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
        elif event_type == EventType.REGISTRY_SNAPSHOT_RECORDED.value:
            registry_snapshot = event.payload
            registry_ref = registry_snapshot.get("registry_snapshot_ref", {})
            plugin_entries = registry_snapshot.get("plugin_entries", [])
            executor_entries = registry_snapshot.get("executor_entries", [])
            connection.execute(
                """
                insert or replace into registry_snapshots (
                    registry_snapshot_id, task_id, registry_snapshot_artifact_id,
                    registry_snapshot_digest, plugin_count, executor_count, frozen_at,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    registry_snapshot["registry_snapshot_id"],
                    registry_snapshot.get("task_id"),
                    registry_ref.get("artifact_id"),
                    registry_snapshot.get("registry_snapshot_digest"),
                    len(plugin_entries),
                    len(executor_entries),
                    registry_snapshot.get("frozen_at"),
                    _payload_json(registry_snapshot),
                ),
            )
            for executor_entry in executor_entries:
                connection.execute(
                    """
                    insert or replace into executor_statuses (
                        executor_id, executor_version, status, descriptor_digest,
                        last_updated_at, payload_json
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        executor_entry["executor_id"],
                        executor_entry.get("executor_version"),
                        executor_entry.get("status"),
                        executor_entry.get("descriptor_digest"),
                        registry_snapshot.get("frozen_at"),
                        _payload_json(executor_entry),
                    ),
                )
        elif event_type == EventType.EXECUTION_REQUEST_RECORDED.value:
            request_record = event.payload
            request_ref = request_record.get("request_ref", {})
            connection.execute(
                """
                insert or replace into execution_requests (
                    request_id, task_id, unit_id, attempt_id, lease_id,
                    request_artifact_id, request_digest, plugin_id, executor_id,
                    created_at, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_record["request_id"],
                    request_record.get("task_id"),
                    request_record.get("unit_id"),
                    request_record.get("attempt_id"),
                    request_record.get("lease_id"),
                    request_ref.get("artifact_id"),
                    request_record.get("request_digest"),
                    request_record.get("plugin_id"),
                    request_record.get("executor_id"),
                    request_record.get("created_at"),
                    _payload_json(request_record),
                ),
            )
        elif event_type == EventType.EXECUTION_SUBMISSION_RECORDED.value:
            submission_record = event.payload
            submission_ref = submission_record.get("submission_ref", {})
            connection.execute(
                """
                insert or replace into execution_submissions (
                    submission_id, request_id, task_id, unit_id, attempt_id, lease_id,
                    submission_artifact_id, submission_digest, result_kind,
                    submitted_at, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_record["submission_id"],
                    submission_record.get("request_id"),
                    submission_record.get("task_id"),
                    submission_record.get("unit_id"),
                    submission_record.get("attempt_id"),
                    submission_record.get("lease_id"),
                    submission_ref.get("artifact_id"),
                    submission_record.get("submission_digest"),
                    submission_record.get("result_kind"),
                    submission_record.get("submitted_at"),
                    _payload_json(submission_record),
                ),
            )
        elif event_type == EventType.VERIFICATION_RECORDED.value:
            report = event.payload.get("verification_report", {})
            connection.execute(
                """
                insert or replace into verification_reports (
                    verification_report_id, task_id, unit_id, attempt_id,
                    submission_id, submission_event_seq, status,
                    eligible_for_canonical, candidate_output_bundle_digest,
                    validator_policy_id, plugin_id, plugin_version,
                    verification_report_digest, completed_at, source_event_seq,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.get("verification_report_id", event.object_id),
                    event.payload.get("task_id", report.get("task_id")),
                    event.payload.get("unit_id", report.get("unit_id")),
                    event.payload.get("attempt_id", report.get("attempt_id")),
                    event.payload.get("submission_id", report.get("submission_id")),
                    event.payload.get(
                        "submission_event_seq",
                        report.get("submission_event_seq"),
                    ),
                    event.payload.get("status", report.get("status")),
                    int(
                        bool(
                            event.payload.get(
                                "eligible_for_canonical",
                                report.get("eligible_for_canonical"),
                            )
                        )
                    ),
                    event.payload.get(
                        "candidate_output_bundle_digest",
                        report.get("candidate_output_bundle_digest"),
                    ),
                    event.payload.get(
                        "validator_policy_id", report.get("validator_policy_id")
                    ),
                    event.payload.get("plugin_id", report.get("plugin_id")),
                    event.payload.get("plugin_version", report.get("plugin_version")),
                    event.payload.get("verification_report_digest"),
                    event.payload.get("completed_at", report.get("completed_at")),
                    event.event_seq,
                    _payload_json(event.payload),
                ),
            )
        elif event_type == EventType.CANONICAL_OUTPUTS_BOUND.value:
            self._insert_canonical_outputs(connection, event)
        elif event_type == EventType.SPLIT_STRATEGY_INVOCATION_RECORDED.value:
            invocation = event.payload.get("invocation", {})
            connection.execute(
                """
                insert or replace into split_strategy_invocations (
                    invocation_id, invocation_attempt_no, task_id, unit_id,
                    canonical_selection_id, canonical_output_bundle_digest,
                    plugin_id, plugin_version, plugin_descriptor_digest,
                    split_strategy_id, split_strategy_params_digest,
                    expansion_scope_hash, status, result_action, result_digest,
                    error_kind, error_summary, started_at, completed_at,
                    source_event_seq, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation.get("invocation_id", event.object_id),
                    invocation.get("invocation_attempt_no"),
                    event.payload.get("task_id", invocation.get("task_id")),
                    event.payload.get("unit_id", invocation.get("unit_id")),
                    event.payload.get(
                        "canonical_selection_id",
                        invocation.get("canonical_selection_id"),
                    ),
                    event.payload.get(
                        "canonical_output_bundle_digest",
                        invocation.get("canonical_output_bundle_digest"),
                    ),
                    event.payload.get("plugin_id", invocation.get("plugin_id")),
                    event.payload.get("plugin_version", invocation.get("plugin_version")),
                    event.payload.get(
                        "plugin_descriptor_digest",
                        invocation.get("plugin_descriptor_digest"),
                    ),
                    event.payload.get(
                        "split_strategy_id", invocation.get("split_strategy_id")
                    ),
                    event.payload.get(
                        "split_strategy_params_digest",
                        invocation.get("split_strategy_params_digest"),
                    ),
                    event.payload.get(
                        "expansion_scope_hash",
                        invocation.get("expansion_scope_hash"),
                    ),
                    event.payload.get("status", invocation.get("status")),
                    event.payload.get("result_action", invocation.get("result_action")),
                    event.payload.get("result_digest", invocation.get("result_digest")),
                    event.payload.get("error_kind", invocation.get("error_kind")),
                    event.payload.get("error_summary", invocation.get("error_summary")),
                    event.payload.get("started_at", invocation.get("started_at")),
                    event.payload.get("completed_at", invocation.get("completed_at")),
                    event.event_seq,
                    _payload_json(event.payload),
                ),
            )
        elif event_type == EventType.DECOMPOSITION_PROPOSAL_RECORDED.value:
            expansion_batch = phase4_context.expansion_batches_by_id.get(event.batch_id)
            if expansion_batch is None or expansion_batch.proposal_event is not event:
                return
            proposal_ref = event.payload.get("proposal_ref", {})
            connection.execute(
                """
                insert or replace into decomposition_proposals (
                    proposal_id, task_id, parent_unit_id, canonical_selection_id,
                    proposal_artifact_id, proposal_digest, expansion_scope_hash,
                    plugin_id, plugin_version, split_strategy_id, child_count,
                    dependency_edge_count, expected_output_count, merge_slot_count,
                    created_at, expansion_decision_id, batch_id, source_event_seq,
                    visible, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.payload.get("proposal_id", event.object_id),
                    event.payload.get("task_id"),
                    event.payload.get("parent_unit_id"),
                    event.payload.get("canonical_selection_id"),
                    proposal_ref.get("artifact_id"),
                    event.payload.get("proposal_digest"),
                    event.payload.get("expansion_scope_hash"),
                    event.payload.get("plugin_id"),
                    event.payload.get("plugin_version"),
                    event.payload.get("split_strategy_id"),
                    event.payload.get("child_count"),
                    event.payload.get("dependency_edge_count"),
                    event.payload.get("expected_output_count"),
                    event.payload.get("merge_slot_count"),
                    event.payload.get("created_at"),
                    expansion_batch.expansion_decision_id,
                    event.batch_id,
                    event.event_seq,
                    1,
                    _payload_json(event.payload),
                ),
            )
        elif event_type == EventType.EXPANSION_DECISION_RECORDED.value:
            if not phase4_context.is_consumable_decision_event(event):
                return
            self._insert_expansion_decision(connection, event)
        elif event_type == EventType.MERGE_PLAN_RECORDED.value:
            expansion_batch = phase4_context.expansion_batches_by_id.get(event.batch_id)
            if expansion_batch is None or expansion_batch.merge_plan_event is not event:
                return
            merge_plan_ref = event.payload.get("merge_plan_ref", {})
            connection.execute(
                """
                insert or replace into merge_plans (
                    merge_plan_id, task_id, parent_unit_id, canonical_selection_id,
                    decomposition_proposal_id, expansion_decision_id,
                    merge_plan_artifact_id, merge_plan_digest, merge_policy_id,
                    merge_policy_version, required_slot_count,
                    parent_output_mapping_count, created_at, batch_id,
                    source_event_seq, visible, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.payload.get("merge_plan_id", event.object_id),
                    event.payload.get("task_id"),
                    event.payload.get("parent_unit_id"),
                    event.payload.get("canonical_selection_id"),
                    event.payload.get("decomposition_proposal_id"),
                    event.payload.get("expansion_decision_id"),
                    merge_plan_ref.get("artifact_id"),
                    event.payload.get("merge_plan_digest"),
                    event.payload.get("merge_policy_id"),
                    event.payload.get("merge_policy_version"),
                    event.payload.get("required_slot_count"),
                    event.payload.get("parent_output_mapping_count"),
                    event.payload.get("created_at"),
                    event.batch_id,
                    event.event_seq,
                    1,
                    _payload_json(event.payload),
                ),
            )

    def _insert_canonical_outputs(
        self, connection: sqlite3.Connection, event: LedgerEvent
    ) -> None:
        selection = event.payload.get("canonical_selection", {})
        task_id = event.payload.get("task_id", selection.get("task_id"))
        unit_id = event.payload.get("unit_id", selection.get("unit_id"))
        canonical_selection_id = selection.get("canonical_selection_id", event.object_id)
        bundle_digest = event.payload.get(
            "canonical_output_bundle_digest",
            selection.get("canonical_output_bundle_digest"),
        )
        payload_json = _payload_json(selection)
        existing = connection.execute(
            """
            select canonical_selection_id, canonical_output_bundle_digest, payload_json
            from canonical_outputs
            where task_id = ? and unit_id = ?
            """,
            (task_id, unit_id),
        ).fetchone()
        if existing is not None:
            if existing != (canonical_selection_id, bundle_digest, payload_json):
                raise ValueError(f"canonical outputs conflict for {task_id}/{unit_id}")
            return

        connection.execute(
            """
            insert into canonical_outputs (
                canonical_selection_id, task_id, unit_id, selection_policy,
                selection_policy_version, selected_verification_report_id,
                selected_verification_event_seq, selected_submission_id,
                selected_submission_event_seq, selected_attempt_id,
                canonical_output_bundle_digest, bound_at, source_event_seq,
                payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_selection_id,
                task_id,
                unit_id,
                event.payload.get("selection_policy", selection.get("selection_policy")),
                event.payload.get(
                    "selection_policy_version",
                    selection.get("selection_policy_version"),
                ),
                event.payload.get(
                    "selected_verification_report_id",
                    selection.get("selected_verification_report_id"),
                ),
                event.payload.get(
                    "selected_verification_event_seq",
                    selection.get("selected_verification_event_seq"),
                ),
                event.payload.get(
                    "selected_submission_id", selection.get("selected_submission_id")
                ),
                event.payload.get(
                    "selected_submission_event_seq",
                    selection.get("selected_submission_event_seq"),
                ),
                event.payload.get(
                    "selected_attempt_id", selection.get("selected_attempt_id")
                ),
                bundle_digest,
                event.payload.get("bound_at", selection.get("bound_at")),
                event.event_seq,
                payload_json,
            ),
        )

    def _insert_expansion_decision(
        self, connection: sqlite3.Connection, event: LedgerEvent
    ) -> None:
        decision = event.payload.get("expansion_decision", {})
        expansion_decision_id = decision.get("expansion_decision_id", event.object_id)
        expansion_scope_hash = event.payload.get(
            "expansion_scope_hash", decision.get("expansion_scope_hash")
        )
        payload_json = _payload_json(decision)
        existing = connection.execute(
            """
            select expansion_decision_id, payload_json
            from expansion_decisions
            where expansion_scope_hash = ?
            """,
            (expansion_scope_hash,),
        ).fetchone()
        if existing is not None:
            if existing != (expansion_decision_id, payload_json):
                raise ValueError(f"expansion decision conflict for {expansion_scope_hash}")
            return
        connection.execute(
            """
            insert into expansion_decisions (
                expansion_decision_id, task_id, unit_id, canonical_selection_id,
                canonical_output_bundle_digest, expansion_scope_hash, action,
                plugin_id, plugin_version, plugin_descriptor_digest,
                split_strategy_id, split_strategy_params_digest,
                source_invocation_id, proposal_id, proposal_digest,
                merge_plan_id, merge_plan_digest, decided_at, batch_id,
                source_event_seq, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expansion_decision_id,
                event.payload.get("task_id", decision.get("task_id")),
                event.payload.get("unit_id", decision.get("unit_id")),
                event.payload.get(
                    "canonical_selection_id", decision.get("canonical_selection_id")
                ),
                event.payload.get(
                    "canonical_output_bundle_digest",
                    decision.get("canonical_output_bundle_digest"),
                ),
                expansion_scope_hash,
                event.payload.get("action", decision.get("action")),
                event.payload.get("plugin_id", decision.get("plugin_id")),
                event.payload.get("plugin_version", decision.get("plugin_version")),
                event.payload.get(
                    "plugin_descriptor_digest",
                    decision.get("plugin_descriptor_digest"),
                ),
                event.payload.get(
                    "split_strategy_id", decision.get("split_strategy_id")
                ),
                event.payload.get(
                    "split_strategy_params_digest",
                    decision.get("split_strategy_params_digest"),
                ),
                event.payload.get(
                    "source_invocation_id", decision.get("source_invocation_id")
                ),
                event.payload.get("proposal_id", decision.get("proposal_id")),
                event.payload.get("proposal_digest", decision.get("proposal_digest")),
                event.payload.get("merge_plan_id", decision.get("merge_plan_id")),
                event.payload.get("merge_plan_digest", decision.get("merge_plan_digest")),
                event.payload.get("decided_at", decision.get("decided_at")),
                event.batch_id,
                event.event_seq,
                payload_json,
            ),
        )

    def _insert_expected_output_ref(
        self,
        connection: sqlite3.Connection,
        expected_output: "_ExpectedOutputProjection",
    ) -> None:
        expected_output_ref = expected_output.expected_output_ref
        payload = {
            **expected_output_ref.to_dict(),
            "source_expected_output": expected_output.source_expected_output,
        }
        connection.execute(
            """
            insert or replace into expected_output_refs (
                expected_output_id, task_id, owner_unit_id, output_name,
                schema_ref_json, resolution_kind, resolution_status,
                child_unit_id, child_output_name, merge_plan_id,
                canonical_selection_id, canonical_output_bundle_digest,
                source_proposal_id, source_expansion_decision_id,
                created_event_seq, resolved_event_seq, payload_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expected_output_ref.expected_output_id,
                expected_output_ref.task_id,
                expected_output_ref.owner_unit_id,
                expected_output_ref.output_name,
                _payload_json(expected_output_ref.schema_ref),
                expected_output_ref.resolution_kind,
                expected_output_ref.resolution_status,
                expected_output_ref.child_unit_id,
                expected_output_ref.child_output_name,
                expected_output_ref.merge_plan_id,
                expected_output_ref.canonical_selection_id,
                expected_output_ref.canonical_output_bundle_digest,
                expected_output_ref.source_proposal_id,
                expected_output_ref.source_expansion_decision_id,
                expected_output_ref.created_event_seq,
                expected_output_ref.resolved_event_seq,
                _payload_json(payload),
            ),
        )


@dataclass(frozen=True)
class _ExpansionBatch:
    batch_id: str
    expansion_decision_id: str
    proposal_event: LedgerEvent
    decision_event: LedgerEvent
    merge_plan_event: LedgerEvent
    child_unit_events: tuple[LedgerEvent, ...]
    relation_events: tuple[LedgerEvent, ...]
    task_expanded_event: LedgerEvent


@dataclass(frozen=True)
class _ExpectedOutputProjection:
    expected_output_ref: ExpectedOutputRef
    source_expected_output: dict[str, Any]


@dataclass(frozen=True)
class _Phase4ProjectionContext:
    expansion_batches_by_id: dict[str, _ExpansionBatch]
    completion_decision_event_seqs: frozenset[int]
    expected_output_refs: tuple[_ExpectedOutputProjection, ...]

    @property
    def visible_expansion_batch_ids(self) -> frozenset[str]:
        return frozenset(self.expansion_batches_by_id)

    def is_consumable_decision_event(self, event: LedgerEvent) -> bool:
        if event.event_seq in self.completion_decision_event_seqs:
            return True
        expansion_batch = self.expansion_batches_by_id.get(event.batch_id)
        return (
            expansion_batch is not None
            and expansion_batch.decision_event.event_seq == event.event_seq
        )


def _build_phase4_projection_context(
    events: list[LedgerEvent], *, artifact_store: ArtifactStore | None
) -> _Phase4ProjectionContext:
    grouped_events: dict[str, list[LedgerEvent]] = defaultdict(list)
    for event in events:
        if event.batch_id is not None:
            grouped_events[event.batch_id].append(event)
        if (
            event.event_type == EventType.EXPANSION_DECISION_RECORDED
            and _payload_field(event.payload, "action") == "complete"
            and not (event.batch_id or "").startswith("completion_batch:")
        ):
            raise ValueError(f"incomplete completion_batch: {event.object_id}")

    expansion_batches: dict[str, _ExpansionBatch] = {}
    completion_decision_event_seqs: set[int] = set()
    for batch_id, batch_events in grouped_events.items():
        ordered_events = _ordered_batch_events(batch_id, batch_events)
        if batch_id.startswith("completion_batch:"):
            completion_decision_event_seqs.add(
                _validate_completion_batch(batch_id, ordered_events).event_seq
            )
        elif batch_id.startswith("expansion_batch:"):
            expansion_batch = _validate_expansion_batch(batch_id, ordered_events)
            expansion_batches[batch_id] = expansion_batch

    expected_output_refs = tuple(
        expected_output_ref
        for expansion_batch in expansion_batches.values()
        for expected_output_ref in _expected_output_refs_from_batch(
            expansion_batch,
            artifact_store=artifact_store,
        )
    )
    return _Phase4ProjectionContext(
        expansion_batches_by_id=expansion_batches,
        completion_decision_event_seqs=frozenset(completion_decision_event_seqs),
        expected_output_refs=expected_output_refs,
    )


def _ordered_batch_events(batch_id: str, events: list[LedgerEvent]) -> tuple[LedgerEvent, ...]:
    if not events:
        raise ValueError(f"batch envelope inconsistent: {batch_id}")
    batch_sizes = {event.batch_size for event in events}
    if len(batch_sizes) != 1 or None in batch_sizes:
        _raise_batch_error(batch_id)
    batch_size = batch_sizes.pop()
    if batch_size != len(events):
        _raise_batch_error(batch_id)
    indexes = [event.batch_index for event in events]
    if sorted(indexes) != list(range(1, batch_size + 1)):
        _raise_batch_error(batch_id)
    if len(set(indexes)) != len(indexes):
        _raise_batch_error(batch_id)
    ordered_by_index = tuple(sorted(events, key=lambda event: event.batch_index or 0))
    ordered_by_seq = tuple(sorted(events, key=lambda event: event.event_seq))
    first_seq = ordered_by_seq[0].event_seq
    if [event.event_seq for event in ordered_by_seq] != list(
        range(first_seq, first_seq + batch_size)
    ):
        _raise_batch_error(batch_id)
    if [event.batch_index for event in ordered_by_seq] != list(range(1, batch_size + 1)):
        _raise_batch_error(batch_id)
    return ordered_by_index


def _validate_completion_batch(
    batch_id: str, events: tuple[LedgerEvent, ...]
) -> LedgerEvent:
    event_types = [_event_type_value(event.event_type) for event in events]
    if event_types != [
        EventType.EXPANSION_DECISION_RECORDED.value,
        EventType.TASK_UNIT_STATE_CHANGED.value,
    ]:
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    decision_event, state_event = events
    decision_id = _payload_field(decision_event.payload, "expansion_decision_id")
    if batch_id != f"completion_batch:{decision_id}":
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if _payload_field(decision_event.payload, "action") != "complete":
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if _state_change_new_state(state_event.payload) != "Completed":
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if state_event.task_id != _payload_field(decision_event.payload, "task_id"):
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if _state_change_task_id(state_event.payload) not in {
        None,
        _payload_field(decision_event.payload, "task_id"),
    }:
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if _state_change_unit_id(state_event.payload) != _payload_field(
        decision_event.payload,
        "unit_id",
    ):
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    return decision_event


def _validate_expansion_batch(
    batch_id: str, events: tuple[LedgerEvent, ...]
) -> _ExpansionBatch:
    event_types = [_event_type_value(event.event_type) for event in events]
    if len(event_types) < 4:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    if event_types[:3] != [
        EventType.DECOMPOSITION_PROPOSAL_RECORDED.value,
        EventType.EXPANSION_DECISION_RECORDED.value,
        EventType.MERGE_PLAN_RECORDED.value,
    ]:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    if event_types[-1] != EventType.TASK_EXPANDED.value:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")

    child_unit_events: list[LedgerEvent] = []
    relation_events: list[LedgerEvent] = []
    relation_phase_started = False
    for event in events[3:-1]:
        event_type = _event_type_value(event.event_type)
        if event_type == EventType.TASK_UNIT_CREATED.value and not relation_phase_started:
            child_unit_events.append(event)
            continue
        if event_type == EventType.TASK_RELATION_CREATED.value:
            relation_phase_started = True
            relation_events.append(event)
            continue
        raise ValueError(f"incomplete expansion_batch: {batch_id}")

    proposal_event, decision_event, merge_plan_event = events[:3]
    task_expanded_event = events[-1]
    decision_id = _payload_field(decision_event.payload, "expansion_decision_id")
    if batch_id != f"expansion_batch:{decision_id}":
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    if _payload_field(decision_event.payload, "action") != "expand":
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    if merge_plan_event.payload.get("expansion_decision_id") != decision_id:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    if task_expanded_event.payload.get("expansion_decision_id") != decision_id:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")

    _require_same_batch_field(
        batch_id,
        proposal_event.payload.get("proposal_id"),
        _payload_field(decision_event.payload, "proposal_id"),
        task_expanded_event.payload.get("proposal_id"),
    )
    _require_same_batch_field(
        batch_id,
        proposal_event.payload.get("proposal_digest"),
        _payload_field(decision_event.payload, "proposal_digest"),
        task_expanded_event.payload.get("proposal_digest"),
    )
    _require_same_batch_field(
        batch_id,
        merge_plan_event.payload.get("merge_plan_id"),
        _payload_field(decision_event.payload, "merge_plan_id"),
        task_expanded_event.payload.get("merge_plan_id"),
    )
    _require_same_batch_field(
        batch_id,
        merge_plan_event.payload.get("merge_plan_digest"),
        _payload_field(decision_event.payload, "merge_plan_digest"),
        task_expanded_event.payload.get("merge_plan_digest"),
    )
    _require_same_batch_field(
        batch_id,
        proposal_event.payload.get("parent_unit_id"),
        _payload_field(decision_event.payload, "unit_id"),
        task_expanded_event.payload.get("parent_unit_id"),
    )
    _require_same_batch_field(
        batch_id,
        proposal_event.payload.get("task_id"),
        _payload_field(decision_event.payload, "task_id"),
        task_expanded_event.payload.get("task_id"),
    )
    _require_same_batch_field(
        batch_id,
        proposal_event.payload.get("canonical_selection_id"),
        _payload_field(decision_event.payload, "canonical_selection_id"),
        task_expanded_event.payload.get("canonical_selection_id"),
    )
    marker_child_ids = task_expanded_event.payload.get("child_unit_ids")
    if marker_child_ids != _child_unit_ids(child_unit_events):
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    marker_relation_ids = task_expanded_event.payload.get("relation_ids")
    if marker_relation_ids != _relation_ids(relation_events):
        raise ValueError(f"incomplete expansion_batch: {batch_id}")

    return _ExpansionBatch(
        batch_id=batch_id,
        expansion_decision_id=decision_id,
        proposal_event=proposal_event,
        decision_event=decision_event,
        merge_plan_event=merge_plan_event,
        child_unit_events=tuple(child_unit_events),
        relation_events=tuple(relation_events),
        task_expanded_event=task_expanded_event,
    )


def _expected_output_refs_from_batch(
    expansion_batch: _ExpansionBatch, *, artifact_store: ArtifactStore | None
) -> tuple[_ExpectedOutputProjection, ...]:
    if artifact_store is None:
        raise ValueError(
            f"artifact_store is required to project expected_output_refs for {expansion_batch.batch_id}"
        )
    proposal_ref_data = expansion_batch.proposal_event.payload.get("proposal_ref")
    if proposal_ref_data is None:
        raise ValueError(f"incomplete expansion_batch: {expansion_batch.batch_id}")
    proposal = _read_json_artifact(artifact_store, proposal_ref_data)
    proposal_header = proposal.get("proposal_header", {})
    decision_payload = expansion_batch.decision_event.payload
    child_unit_ids_by_key = _child_unit_ids_by_key(expansion_batch.child_unit_events)
    refs = tuple(
        _ExpectedOutputProjection(
            expected_output_ref=ExpectedOutputRef.from_expected_output(
                expected_output=expected_output,
                task_id=_payload_field(decision_payload, "task_id"),
                owner_unit_id=_payload_field(decision_payload, "unit_id"),
                canonical_selection_id=_payload_field(
                    decision_payload, "canonical_selection_id"
                ),
                canonical_output_bundle_digest=_payload_field(
                    decision_payload, "canonical_output_bundle_digest"
                ),
                source_proposal_id=proposal_header.get(
                    "proposal_id",
                    expansion_batch.proposal_event.payload.get("proposal_id"),
                ),
                source_expansion_decision_id=expansion_batch.expansion_decision_id,
                created_event_seq=expansion_batch.task_expanded_event.event_seq,
                logical_position=position,
                child_unit_ids_by_key=child_unit_ids_by_key,
                merge_plan_id=_payload_field(decision_payload, "merge_plan_id"),
            ),
            source_expected_output=dict(expected_output),
        )
        for position, expected_output in enumerate(proposal.get("expected_outputs", []))
    )
    marker_expected_ids = expansion_batch.task_expanded_event.payload.get(
        "expected_output_ids",
        [],
    )
    if marker_expected_ids and marker_expected_ids != [
        projection.expected_output_ref.expected_output_id for projection in refs
    ]:
        raise ValueError(f"incomplete expansion_batch: {expansion_batch.batch_id}")
    return refs


def _read_json_artifact(
    artifact_store: ArtifactStore, artifact_ref_data: dict[str, Any]
) -> dict[str, Any]:
    artifact_ref = ArtifactRef.from_dict(artifact_ref_data)
    if not artifact_store.verify(artifact_ref):
        raise ValueError(f"artifact verification failed: {artifact_ref.artifact_id}")
    return json.loads(artifact_store.read_bytes(artifact_ref).decode("utf-8"))


def _child_unit_ids_by_key(events: tuple[LedgerEvent, ...]) -> dict[str, str]:
    child_unit_ids: dict[str, str] = {}
    for event in events:
        task_unit = event.payload.get("task_unit", {})
        child_key = event.payload.get("child_logical_key") or task_unit.get(
            "metadata", {}
        ).get("child_logical_key")
        if child_key:
            child_unit_ids[child_key] = task_unit["unit_id"]
    return child_unit_ids


def _child_unit_ids(events: list[LedgerEvent]) -> list[str]:
    ids: list[str] = []
    for event in events:
        task_unit = event.payload.get("task_unit", {})
        if isinstance(task_unit, dict) and task_unit.get("unit_id"):
            ids.append(task_unit["unit_id"])
        else:
            ids.append(event.object_id)
    return ids


def _relation_ids(events: list[LedgerEvent]) -> list[str]:
    ids: list[str] = []
    for event in events:
        relation = event.payload.get("task_relation", {})
        if isinstance(relation, dict) and relation.get("relation_id"):
            ids.append(relation["relation_id"])
        else:
            ids.append(event.object_id)
    return ids


def _payload_field(payload: dict[str, Any], field_name: str) -> Any:
    if field_name in payload:
        return payload[field_name]
    nested_decision = payload.get("expansion_decision", {})
    if isinstance(nested_decision, dict):
        return nested_decision.get(field_name)
    return None


def _state_change_new_state(payload: dict[str, Any]) -> str | None:
    if "new_state" in payload:
        return payload["new_state"]
    state_change = payload.get("task_unit_state_change", {})
    if isinstance(state_change, dict):
        return state_change.get("new_state")
    return None


def _state_change_task_id(payload: dict[str, Any]) -> str | None:
    state_change = payload.get("task_unit_state_change", {})
    if isinstance(state_change, dict) and state_change.get("task_id"):
        return state_change["task_id"]
    task_unit = payload.get("task_unit", {})
    if isinstance(task_unit, dict):
        return task_unit.get("task_id")
    return None


def _state_change_unit_id(payload: dict[str, Any]) -> str | None:
    state_change = payload.get("task_unit_state_change", {})
    if isinstance(state_change, dict) and state_change.get("unit_id"):
        return state_change["unit_id"]
    task_unit = payload.get("task_unit", {})
    if isinstance(task_unit, dict) and task_unit.get("unit_id"):
        return task_unit["unit_id"]
    return payload.get("unit_id")


def _is_phase4_expansion_child_event(event: LedgerEvent) -> bool:
    return (
        event.payload.get("schema_version")
        in {
            "phase4.expansion_task_unit_created.v1",
            "phase4.expansion_task_relation_created.v1",
        }
        or "expansion_decision_id" in event.payload
    )


def _require_same_batch_field(batch_id: str, *values: Any) -> None:
    normalized_values = [value for value in values if value is not None]
    if not normalized_values or len(set(normalized_values)) != 1:
        raise ValueError(f"incomplete expansion_batch: {batch_id}")


def _raise_batch_error(batch_id: str) -> None:
    if batch_id.startswith("completion_batch:"):
        raise ValueError(f"incomplete completion_batch: {batch_id}")
    if batch_id.startswith("expansion_batch:"):
        raise ValueError(f"incomplete expansion_batch: {batch_id}")
    raise ValueError(f"batch envelope inconsistent: {batch_id}")


def _payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_type_value(value: EventType | str) -> str:
    return value.value if isinstance(value, EventType) else value
