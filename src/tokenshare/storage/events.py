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
    """Phase 1 event type constants from the object-field spec."""

    ARTIFACT_STORED = "ARTIFACT_STORED"
    TASK_REGISTERED = "TASK_REGISTERED"
    TASK_UNIT_CREATED = "TASK_UNIT_CREATED"
    TASK_RELATION_CREATED = "TASK_RELATION_CREATED"
    CLIENT_REGISTERED = "CLIENT_REGISTERED"


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
    schema_version: str = "LedgerEvent.v1"

    def to_dict(self) -> JsonObject:
        return {
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
        )


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
            return self._by_idempotency_key[idempotency_key]

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
        )
        event = LedgerEvent(
            **{
                **draft.to_dict(),
                "event_type": draft.event_type,
                "event_hash": _event_hash(draft.to_dict()),
            }
        )

        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(event.to_dict()))
            handle.write("\n")

        self._events.append(event)
        self._by_idempotency_key[idempotency_key] = event
        self._last_event_hash = event.event_hash
        return event

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


def _event_hash(event_dict: JsonObject) -> str:
    hash_input = {key: value for key, value in event_dict.items() if key != "event_hash"}
    return f"sha256:{sha256(_canonical_json(hash_input).encode('utf-8')).hexdigest()}"


def _canonical_json(data: JsonObject) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
