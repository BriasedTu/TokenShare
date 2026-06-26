"""Append-only JSONL event ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


class EventType(str, Enum):
    """Event type constants used by the current protocol phases."""

    ARTIFACT_STORED = "ARTIFACT_STORED"
    TASK_REGISTERED = "TASK_REGISTERED"
    TASK_UNIT_CREATED = "TASK_UNIT_CREATED"
    TASK_RELATION_CREATED = "TASK_RELATION_CREATED"
    CLIENT_REGISTERED = "CLIENT_REGISTERED"
    TASK_UNIT_STATE_CHANGED = "TASK_UNIT_STATE_CHANGED"
    CLIENT_STATE_CHANGED = "CLIENT_STATE_CHANGED"
    LEASE_STATE_CHANGED = "LEASE_STATE_CHANGED"
    ATTEMPT_STATE_CHANGED = "ATTEMPT_STATE_CHANGED"
    RECOVERY_ACTION_RECORDED = "RECOVERY_ACTION_RECORDED"
    REGISTRY_SNAPSHOT_RECORDED = "REGISTRY_SNAPSHOT_RECORDED"
    EXECUTION_REQUEST_RECORDED = "EXECUTION_REQUEST_RECORDED"
    EXECUTION_SUBMISSION_RECORDED = "EXECUTION_SUBMISSION_RECORDED"
    VERIFICATION_RECORDED = "VERIFICATION_RECORDED"
    CANONICAL_OUTPUTS_BOUND = "CANONICAL_OUTPUTS_BOUND"
    SPLIT_STRATEGY_INVOCATION_RECORDED = "SPLIT_STRATEGY_INVOCATION_RECORDED"
    DECOMPOSITION_PROPOSAL_RECORDED = "DECOMPOSITION_PROPOSAL_RECORDED"
    EXPANSION_DECISION_RECORDED = "EXPANSION_DECISION_RECORDED"
    MERGE_PLAN_RECORDED = "MERGE_PLAN_RECORDED"
    TASK_EXPANDED = "TASK_EXPANDED"
    MERGE_TASK_LINK_RECORDED = "MERGE_TASK_LINK_RECORDED"
    MERGE_RECORDED = "MERGE_RECORDED"
    EXPECTED_OUTPUT_RESOLVED = "EXPECTED_OUTPUT_RESOLVED"
    CONTRIBUTION_STATE_CHANGED = "CONTRIBUTION_STATE_CHANGED"
    SETTLEMENT_RECORDED = "SETTLEMENT_RECORDED"
    SUBTREE_PRUNED = "SUBTREE_PRUNED"


@dataclass(frozen=True)
class LedgerEvent:
    """One JSONL event envelope.

    ``event_hash`` is derived from every field except itself. That gives replay
    a cheap integrity check and makes accidental event edits visible.
    """

    event_seq: int
    event_id: str
    event_type: EventType | str
    occurred_at: str
    task_id: str | None
    object_type: str
    object_id: str
    actor: JsonObject
    correlation_id: str | None
    causation_event_id: str | None
    idempotency_key: str
    payload: JsonObject
    prev_event_hash: str | None
    event_hash: str
    schema_version: str = "LedgerEvent.v2"
    batch_id: str | None = None
    batch_index: int | None = None
    batch_size: int | None = None

    def to_dict(self) -> JsonObject:
        event_dict: JsonObject = {
            "schema_version": self.schema_version,
            "event_seq": self.event_seq,
            "event_id": self.event_id,
            "event_type": _event_type_value(self.event_type),
            "occurred_at": self.occurred_at,
            "task_id": self.task_id,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "causation_event_id": self.causation_event_id,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "prev_event_hash": self.prev_event_hash,
            "event_hash": self.event_hash,
        }
        if self.schema_version == "LedgerEvent.v2":
            event_dict["batch_id"] = self.batch_id
            event_dict["batch_index"] = self.batch_index
            event_dict["batch_size"] = self.batch_size
        return event_dict

    @classmethod
    def from_dict(cls, data: JsonObject) -> "LedgerEvent":
        return cls(
            schema_version=data.get("schema_version", "LedgerEvent.v1"),
            event_seq=int(data["event_seq"]),
            event_id=data["event_id"],
            event_type=_parse_event_type(data["event_type"]),
            occurred_at=data["occurred_at"],
            task_id=data.get("task_id"),
            object_type=data["object_type"],
            object_id=data["object_id"],
            actor=dict(data.get("actor", {})),
            correlation_id=data.get("correlation_id"),
            causation_event_id=data.get("causation_event_id"),
            idempotency_key=data["idempotency_key"],
            payload=dict(data.get("payload", {})),
            prev_event_hash=data.get("prev_event_hash"),
            event_hash=data["event_hash"],
            batch_id=data.get("batch_id"),
            batch_index=data.get("batch_index"),
            batch_size=data.get("batch_size"),
        )


@dataclass(frozen=True)
class EventDraft:
    """In-memory event input used before ledger sequence and hash assignment."""

    event_type: EventType | str
    object_type: str
    object_id: str
    payload: JsonObject
    idempotency_key: str
    task_id: str | None = None
    actor: JsonObject | None = None
    correlation_id: str | None = None
    causation_event_id: str | None = None
    occurred_at: str | None = None


class EventLedger:
    """JSONL ledger with event sequence, idempotency, and hash chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events = self.read_all()
        self._by_idempotency_key = {event.idempotency_key: event for event in self._events}
        self._last_event_hash = self._events[-1].event_hash if self._events else None

    def append(
        self,
        *,
        event_type: EventType | str,
        object_type: str,
        object_id: str,
        payload: JsonObject,
        idempotency_key: str,
        task_id: str | None = None,
        actor: JsonObject | None = None,
        correlation_id: str | None = None,
        causation_event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> LedgerEvent:
        """Append a new event or return the existing idempotent event.

        Returning the existing event keeps duplicate registration attempts from
        extending the ledger while preserving append-only semantics.
        """

        if idempotency_key in self._by_idempotency_key:
            existing_event = self._by_idempotency_key[idempotency_key]
            if _idempotency_signature(
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
                task_id=task_id,
                payload=payload,
            ) != _idempotency_signature(
                event_type=existing_event.event_type,
                object_type=existing_event.object_type,
                object_id=existing_event.object_id,
                task_id=existing_event.task_id,
                payload=existing_event.payload,
            ):
                raise ValueError(f"idempotency key conflict: {idempotency_key}")
            return existing_event

        event_seq = len(self._events) + 1
        event_id = f"event_{event_seq:012d}"
        draft = LedgerEvent(
            event_seq=event_seq,
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at or utc_now(),
            task_id=task_id,
            object_type=object_type,
            object_id=object_id,
            actor=actor or {"kind": "protocol"},
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            idempotency_key=idempotency_key,
            payload=payload,
            prev_event_hash=self._last_event_hash,
            event_hash="",
            schema_version="LedgerEvent.v2",
            batch_id=None,
            batch_index=None,
            batch_size=None,
        )
        event = _finalize_event(draft)

        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(event.to_dict()))
            handle.write("\n")

        self._events.append(event)
        self._by_idempotency_key[idempotency_key] = event
        self._last_event_hash = event.event_hash
        return event

    def append_batch(
        self, events: list[EventDraft], batch_id: str
    ) -> tuple[LedgerEvent, ...]:
        """Append a batch of events as one local ledger operation.

        This method is the Phase 4 foundation for complete/expand commits. It
        assigns contiguous ledger sequence numbers and batch envelope fields,
        but leaves semantic batch validation to later replay/projection layers.
        """

        drafts = tuple(events)
        if not drafts:
            raise ValueError("event batch cannot be empty")
        if not batch_id:
            raise ValueError("batch_id is required")

        draft_keys = [draft.idempotency_key for draft in drafts]
        if len(set(draft_keys)) != len(draft_keys):
            raise ValueError("duplicate idempotency key in batch")

        existing_events = [
            self._by_idempotency_key.get(draft.idempotency_key) for draft in drafts
        ]
        existing_count = sum(event is not None for event in existing_events)
        if existing_count == len(drafts):
            existing_batch = tuple(event for event in existing_events if event is not None)
            for index, (draft, event) in enumerate(
                zip(drafts, existing_batch, strict=True), start=1
            ):
                if not _batch_event_matches(
                    event=event,
                    draft=draft,
                    batch_id=batch_id,
                    batch_index=index,
                    batch_size=len(drafts),
                ):
                    raise ValueError(
                        f"idempotency key conflict: {draft.idempotency_key}"
                    )
            return existing_batch
        if existing_count:
            raise ValueError("partial existing batch")
        if any(event.batch_id == batch_id for event in self._events):
            raise ValueError(f"batch_id conflict: {batch_id}")

        new_events = self._build_batch_events(drafts=drafts, batch_id=batch_id)

        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for event in new_events:
                handle.write(_canonical_json(event.to_dict()))
                handle.write("\n")
            handle.flush()

        for event in new_events:
            self._events.append(event)
            self._by_idempotency_key[event.idempotency_key] = event
        self._last_event_hash = new_events[-1].event_hash
        return new_events

    def _build_batch_events(
        self, *, drafts: tuple[EventDraft, ...], batch_id: str
    ) -> tuple[LedgerEvent, ...]:
        batch_size = len(drafts)
        previous_hash = self._last_event_hash
        next_event_seq = len(self._events) + 1
        batch_events: list[LedgerEvent] = []
        for index, draft in enumerate(drafts, start=1):
            event_seq = next_event_seq + index - 1
            ledger_event = LedgerEvent(
                event_seq=event_seq,
                event_id=f"event_{event_seq:012d}",
                event_type=draft.event_type,
                occurred_at=draft.occurred_at or utc_now(),
                task_id=draft.task_id,
                object_type=draft.object_type,
                object_id=draft.object_id,
                actor=draft.actor or {"kind": "protocol"},
                correlation_id=draft.correlation_id,
                causation_event_id=draft.causation_event_id,
                idempotency_key=draft.idempotency_key,
                payload=draft.payload,
                prev_event_hash=previous_hash,
                event_hash="",
                schema_version="LedgerEvent.v2",
                batch_id=batch_id,
                batch_index=index,
                batch_size=batch_size,
            )
            finalized_event = _finalize_event(ledger_event)
            batch_events.append(finalized_event)
            previous_hash = finalized_event.event_hash
        return tuple(batch_events)

    def read_all(self) -> list[LedgerEvent]:
        if not self.path.exists():
            return []
        events: list[LedgerEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    events.append(LedgerEvent.from_dict(json.loads(stripped)))
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSONL event at line {line_number}") from error
        return events

    def verify_hash_chain(self) -> bool:
        previous_hash: str | None = None
        for expected_seq, event in enumerate(self.read_all(), start=1):
            event_dict = event.to_dict()
            if event.event_seq != expected_seq:
                return False
            if event.prev_event_hash != previous_hash:
                return False
            if _event_hash(event_dict) != event.event_hash:
                return False
            previous_hash = event.event_hash
        return True


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_event_type(value: str) -> EventType | str:
    try:
        return EventType(value)
    except ValueError:
        return value


def _event_type_value(value: EventType | str) -> str:
    return value.value if isinstance(value, EventType) else value


def _idempotency_signature(
    *,
    event_type: EventType | str,
    object_type: str,
    object_id: str,
    task_id: str | None,
    payload: JsonObject,
) -> tuple[str, str, str, str | None, str]:
    return (
        _event_type_value(event_type),
        object_type,
        object_id,
        task_id,
        _canonical_json(payload),
    )


def _batch_event_matches(
    *,
    event: LedgerEvent,
    draft: EventDraft,
    batch_id: str,
    batch_index: int,
    batch_size: int,
) -> bool:
    return (
        event.schema_version == "LedgerEvent.v2"
        and event.batch_id == batch_id
        and event.batch_index == batch_index
        and event.batch_size == batch_size
        and _idempotency_signature(
            event_type=event.event_type,
            object_type=event.object_type,
            object_id=event.object_id,
            task_id=event.task_id,
            payload=event.payload,
        )
        == _idempotency_signature(
            event_type=draft.event_type,
            object_type=draft.object_type,
            object_id=draft.object_id,
            task_id=draft.task_id,
            payload=draft.payload,
        )
    )


def _finalize_event(event: LedgerEvent) -> LedgerEvent:
    return LedgerEvent(
        event_seq=event.event_seq,
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        task_id=event.task_id,
        object_type=event.object_type,
        object_id=event.object_id,
        actor=event.actor,
        correlation_id=event.correlation_id,
        causation_event_id=event.causation_event_id,
        idempotency_key=event.idempotency_key,
        payload=event.payload,
        prev_event_hash=event.prev_event_hash,
        event_hash=_event_hash(event.to_dict()),
        schema_version=event.schema_version,
        batch_id=event.batch_id,
        batch_index=event.batch_index,
        batch_size=event.batch_size,
    )


def _event_hash(event_dict: JsonObject) -> str:
    hash_input = {key: value for key, value in event_dict.items() if key != "event_hash"}
    return f"sha256:{sha256(_canonical_json(hash_input).encode('utf-8')).hexdigest()}"


def _canonical_json(data: JsonObject) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
