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
