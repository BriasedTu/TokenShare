import json
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from tokenshare.storage.events import EventLedger, EventType


def test_event_ledger_appends_reads_and_verifies_hash_chain(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")

    artifact_event = ledger.append(
        event_type=EventType.ARTIFACT_STORED,
        object_type="ArtifactRef",
        object_id="artifact_root_input",
        payload={"artifact_ref": {"artifact_id": "artifact_root_input"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="artifact:sha256:abc123",
        occurred_at="2026-06-06T00:00:00Z",
    )
    task_event = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-06T00:00:01Z",
    )

    events = ledger.read_all()

    assert artifact_event.event_seq == 1
    assert task_event.event_seq == 2
    assert task_event.prev_event_hash == artifact_event.event_hash
    assert [event.event_type for event in events] == [
        EventType.ARTIFACT_STORED,
        EventType.TASK_REGISTERED,
    ]
    assert ledger.verify_hash_chain()


def test_read_verified_snapshot_captures_exact_identity(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_snapshot.jsonl")
    first = ledger.append(
        event_type=EventType.ARTIFACT_STORED,
        object_type="ArtifactRef",
        object_id="artifact_root_input",
        payload={"artifact_ref": {"artifact_id": "artifact_root_input"}},
        task_id="task_snapshot",
        actor={"kind": "protocol"},
        idempotency_key="artifact:sha256:snapshot",
        occurred_at="2026-06-06T00:00:00Z",
    )
    second = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_snapshot",
        payload={"task_spec": {"task_id": "task_snapshot"}},
        task_id="task_snapshot",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_snapshot",
        occurred_at="2026-06-06T00:00:01Z",
    )
    persisted_bytes = ledger.path.read_bytes()

    snapshot = ledger.read_verified_snapshot()

    canonical_events = json.dumps(
        [first.to_dict(), second.to_dict()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert snapshot.schema_version == "tokenshare.verified_ledger_snapshot.v1"
    assert snapshot.events == (first, second)
    assert snapshot.ledger_bytes_digest == (
        f"sha256:{sha256(persisted_bytes).hexdigest()}"
    )
    assert snapshot.ledger_events_digest == (
        f"sha256:{sha256(canonical_events).hexdigest()}"
    )
    assert snapshot.event_count == 2
    assert snapshot.tip_event_seq == second.event_seq
    assert snapshot.tip_event_id == second.event_id
    assert snapshot.tip_event_hash == second.event_hash
    with pytest.raises(FrozenInstanceError):
        snapshot.event_count = 3


def test_read_verified_snapshot_rejects_tampered_or_duplicate_ledger(
    tmp_path,
) -> None:
    cases = (
        ("event_seq", "continuous event_seq"),
        ("event_id", "duplicate event_id"),
        ("event_hash", "duplicate event_hash"),
        ("prev_event_hash", "prev_event_hash mismatch"),
        ("payload", "event_hash mismatch"),
    )
    for mutation, error_match in cases:
        ledger = EventLedger(
            tmp_path / "events" / f"task_tamper_{mutation}.jsonl"
        )
        ledger.append(
            event_type=EventType.ARTIFACT_STORED,
            object_type="ArtifactRef",
            object_id="artifact_root_input",
            payload={"artifact_ref": {"artifact_id": "artifact_root_input"}},
            task_id="task_tamper",
            actor={"kind": "protocol"},
            idempotency_key="artifact:sha256:tamper",
            occurred_at="2026-06-06T00:00:00Z",
        )
        ledger.append(
            event_type=EventType.TASK_REGISTERED,
            object_type="TaskSpec",
            object_id="task_tamper",
            payload={"task_spec": {"task_id": "task_tamper"}},
            task_id="task_tamper",
            actor={"kind": "protocol"},
            idempotency_key="register_task:task_tamper",
            occurred_at="2026-06-06T00:00:01Z",
        )
        rows = [
            json.loads(line)
            for line in ledger.path.read_text(encoding="utf-8").splitlines()
        ]
        if mutation == "event_seq":
            rows[1]["event_seq"] = 3
        elif mutation == "event_id":
            rows[1]["event_id"] = rows[0]["event_id"]
        elif mutation == "event_hash":
            rows[1]["event_hash"] = rows[0]["event_hash"]
        elif mutation == "prev_event_hash":
            rows[1]["prev_event_hash"] = f"sha256:{'0' * 64}"
        else:
            rows[1]["payload"]["task_spec"]["task_id"] = "task_changed"
        ledger.path.write_text(
            "\n".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for row in rows
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        with pytest.raises(ValueError, match=error_match):
            ledger.read_verified_snapshot()


def test_event_ledger_returns_existing_event_for_duplicate_idempotency_key(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")

    first = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-06T00:00:00Z",
    )
    duplicate = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-06T00:00:01Z",
    )

    assert duplicate == first
    assert len(ledger.read_all()) == 1


def test_event_ledger_rejects_conflicting_duplicate_idempotency_key(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")

    ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-06T00:00:00Z",
    )

    with pytest.raises(ValueError, match="idempotency key conflict"):
        ledger.append(
            event_type=EventType.TASK_REGISTERED,
            object_type="TaskSpec",
            object_id="task_demo",
            payload={"task_spec": {"task_id": "different_task"}},
            task_id="task_demo",
            actor={"kind": "protocol"},
            idempotency_key="register_task:task_demo",
            occurred_at="2026-06-06T00:00:01Z",
        )

    assert len(ledger.read_all()) == 1
