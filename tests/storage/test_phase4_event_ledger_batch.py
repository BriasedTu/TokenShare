import json
from hashlib import sha256
from pathlib import Path

import pytest

from tokenshare.storage import events
from tokenshare.storage.events import EventLedger, EventType, LedgerEvent


def test_phase4_event_type_constants_are_available() -> None:
    assert EventType.VERIFICATION_RECORDED.value == "VERIFICATION_RECORDED"
    assert EventType.CANONICAL_OUTPUTS_BOUND.value == "CANONICAL_OUTPUTS_BOUND"
    assert (
        EventType.SPLIT_STRATEGY_INVOCATION_RECORDED.value
        == "SPLIT_STRATEGY_INVOCATION_RECORDED"
    )
    assert EventType.DECOMPOSITION_PROPOSAL_RECORDED.value == "DECOMPOSITION_PROPOSAL_RECORDED"
    assert EventType.EXPANSION_DECISION_RECORDED.value == "EXPANSION_DECISION_RECORDED"
    assert EventType.MERGE_PLAN_RECORDED.value == "MERGE_PLAN_RECORDED"
    assert EventType.TASK_EXPANDED.value == "TASK_EXPANDED"


def test_existing_v1_hash_chain_still_verifies_after_v2_reader(tmp_path) -> None:
    ledger_path = tmp_path / "events" / "legacy.jsonl"
    _write_legacy_v1_event(
        ledger_path,
        event_seq=1,
        event_id="event_000000000001",
        event_type=EventType.TASK_REGISTERED.value,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        idempotency_key="register_task:task_demo",
    )

    ledger = EventLedger(ledger_path)
    [event] = ledger.read_all()

    assert event.schema_version == "LedgerEvent.v1"
    assert event.batch_id is None
    assert event.batch_index is None
    assert event.batch_size is None
    assert "batch_id" not in event.to_dict()
    assert "batch_index" not in event.to_dict()
    assert "batch_size" not in event.to_dict()
    assert ledger.verify_hash_chain()


def test_v2_non_batch_event_hash_includes_null_batch_fields(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")

    event = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        actor={"kind": "protocol"},
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-24T00:00:00Z",
    )

    event_dict = event.to_dict()
    assert event.schema_version == "LedgerEvent.v2"
    assert event.batch_id is None
    assert event.batch_index is None
    assert event.batch_size is None
    assert event_dict["batch_id"] is None
    assert event_dict["batch_index"] is None
    assert event_dict["batch_size"] is None

    without_batch_fields = {
        key: value
        for key, value in event_dict.items()
        if key not in {"batch_id", "batch_index", "batch_size"}
    }
    assert _event_hash(event_dict) == event.event_hash
    assert _event_hash(without_batch_fields) != event.event_hash
    assert ledger.verify_hash_chain()


def test_append_batch_assigns_batch_fields_and_contiguous_seq(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    first = ledger.append(
        event_type=EventType.TASK_REGISTERED,
        object_type="TaskSpec",
        object_id="task_demo",
        payload={"task_spec": {"task_id": "task_demo"}},
        task_id="task_demo",
        idempotency_key="register_task:task_demo",
        occurred_at="2026-06-24T00:00:00Z",
    )

    batch = ledger.append_batch(
        [
            _draft(
                event_type="DECOMPOSITION_PROPOSAL_RECORDED",
                object_type="DecompositionProposal",
                object_id="proposal_1",
                idempotency_key="decomposition_proposal:scope:proposal",
                payload={"proposal_id": "proposal_1"},
                task_id="task_demo",
                occurred_at="2026-06-24T00:00:01Z",
            ),
            _draft(
                event_type="EXPANSION_DECISION_RECORDED",
                object_type="ExpansionDecision",
                object_id="decision_1",
                idempotency_key="expansion_decision:scope",
                payload={"decision_id": "decision_1", "action": "expand"},
                task_id="task_demo",
                occurred_at="2026-06-24T00:00:02Z",
            ),
            _draft(
                event_type="TASK_EXPANDED",
                object_type="TaskUnit",
                object_id="unit_parent",
                idempotency_key="task_expanded:decision_1",
                payload={"expansion_decision_id": "decision_1"},
                task_id="task_demo",
                occurred_at="2026-06-24T00:00:03Z",
            ),
        ],
        batch_id="expansion_batch:decision_1",
    )

    assert [event.event_seq for event in batch] == [2, 3, 4]
    assert [event.prev_event_hash for event in batch] == [
        first.event_hash,
        batch[0].event_hash,
        batch[1].event_hash,
    ]
    assert [event.batch_id for event in batch] == ["expansion_batch:decision_1"] * 3
    assert [event.batch_index for event in batch] == [1, 2, 3]
    assert [event.batch_size for event in batch] == [3, 3, 3]
    assert all(event.schema_version == "LedgerEvent.v2" for event in batch)
    assert ledger.verify_hash_chain()


def test_append_batch_is_idempotent_when_existing_batch_matches(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    drafts = [
        _draft(
            event_type="EXPANSION_DECISION_RECORDED",
            object_type="ExpansionDecision",
            object_id="decision_1",
            idempotency_key="expansion_decision:scope",
            payload={"decision_id": "decision_1", "action": "complete"},
            task_id="task_demo",
            occurred_at="2026-06-24T00:00:00Z",
        ),
        _draft(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id="unit_1",
            idempotency_key="task_state:unit_1:completed:decision_1",
            payload={"from_state": "Processing", "to_state": "Completed"},
            task_id="task_demo",
            occurred_at="2026-06-24T00:00:01Z",
        ),
    ]

    first = ledger.append_batch(drafts, batch_id="completion_batch:decision_1")
    retry = ledger.append_batch(drafts, batch_id="completion_batch:decision_1")

    assert retry == first
    assert len(ledger.read_all()) == 2


def test_append_batch_rejects_conflicting_retry_without_appending(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    drafts = [
        _draft(
            event_type="EXPANSION_DECISION_RECORDED",
            object_type="ExpansionDecision",
            object_id="decision_1",
            idempotency_key="expansion_decision:scope",
            payload={"decision_id": "decision_1", "action": "complete"},
            task_id="task_demo",
            occurred_at="2026-06-24T00:00:00Z",
        ),
        _draft(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id="unit_1",
            idempotency_key="task_state:unit_1:completed:decision_1",
            payload={"from_state": "Processing", "to_state": "Completed"},
            task_id="task_demo",
            occurred_at="2026-06-24T00:00:01Z",
        ),
    ]
    ledger.append_batch(drafts, batch_id="completion_batch:decision_1")

    conflicting = [
        drafts[0],
        _draft(
            event_type=EventType.TASK_UNIT_STATE_CHANGED,
            object_type="TaskUnit",
            object_id="unit_1",
            idempotency_key="task_state:unit_1:completed:decision_1",
            payload={"from_state": "Processing", "to_state": "Failed"},
            task_id="task_demo",
            occurred_at="2026-06-24T00:00:01Z",
        ),
    ]
    with pytest.raises(ValueError, match="conflict"):
        ledger.append_batch(conflicting, batch_id="completion_batch:decision_1")

    assert len(ledger.read_all()) == 2


def test_append_batch_rejects_partial_existing_batch(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    ledger.append(
        event_type="EXPANSION_DECISION_RECORDED",
        object_type="ExpansionDecision",
        object_id="decision_1",
        payload={"decision_id": "decision_1", "action": "complete"},
        task_id="task_demo",
        idempotency_key="expansion_decision:scope",
        occurred_at="2026-06-24T00:00:00Z",
    )

    with pytest.raises(ValueError, match="partial existing batch"):
        ledger.append_batch(
            [
                _draft(
                    event_type="EXPANSION_DECISION_RECORDED",
                    object_type="ExpansionDecision",
                    object_id="decision_1",
                    idempotency_key="expansion_decision:scope",
                    payload={"decision_id": "decision_1", "action": "complete"},
                    task_id="task_demo",
                    occurred_at="2026-06-24T00:00:00Z",
                ),
                _draft(
                    event_type=EventType.TASK_UNIT_STATE_CHANGED,
                    object_type="TaskUnit",
                    object_id="unit_1",
                    idempotency_key="task_state:unit_1:completed:decision_1",
                    payload={"from_state": "Processing", "to_state": "Completed"},
                    task_id="task_demo",
                    occurred_at="2026-06-24T00:00:01Z",
                ),
            ],
            batch_id="completion_batch:decision_1",
        )

    assert len(ledger.read_all()) == 1


def test_completion_batch_records_decision_and_completed_state_atomically(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")

    batch = ledger.append_batch(
        [
            _draft(
                event_type="EXPANSION_DECISION_RECORDED",
                object_type="ExpansionDecision",
                object_id="decision_complete_1",
                idempotency_key="expansion_decision:scope_complete_1",
                payload={
                    "schema_version": "phase4.expansion_decision_record.v1",
                    "decision_id": "decision_complete_1",
                    "action": "complete",
                    "completion_evidence": {
                        "completion_kind": "canonical_output_complete",
                        "canonical_selection_id": "canonical_1",
                    },
                },
                task_id="task_demo",
                occurred_at="2026-06-24T00:00:00Z",
            ),
            _draft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id="unit_1",
                idempotency_key="task_state:unit_1:completed:decision_complete_1",
                    payload={
                        "unit_id": "unit_1",
                        "from_state": "Processing",
                        "to_state": "Completed",
                        "reason": "phase4_complete_decision",
                        "expansion_decision_id": "decision_complete_1",
                },
                task_id="task_demo",
                occurred_at="2026-06-24T00:00:01Z",
            ),
        ],
        batch_id="completion_batch:decision_complete_1",
    )

    assert [event.event_type for event in batch] == [
        "EXPANSION_DECISION_RECORDED",
        EventType.TASK_UNIT_STATE_CHANGED,
    ]
    assert [event.batch_index for event in batch] == [1, 2]
    assert [event.batch_size for event in batch] == [2, 2]
    assert batch[0].batch_id == "completion_batch:decision_complete_1"
    assert batch[1].batch_id == "completion_batch:decision_complete_1"
    assert batch[0].payload["action"] == "complete"
    assert batch[1].payload["from_state"] == "Processing"
    assert batch[1].payload["to_state"] == "Completed"
    assert ledger.read_all() == list(batch)
    assert ledger.verify_hash_chain()


def _draft(
    *,
    event_type: EventType | str,
    object_type: str,
    object_id: str,
    idempotency_key: str,
    payload: dict,
    task_id: str,
    occurred_at: str,
):
    return events.EventDraft(
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        payload=payload,
        idempotency_key=idempotency_key,
        task_id=task_id,
        actor={"kind": "protocol"},
        correlation_id=None,
        causation_event_id=None,
        occurred_at=occurred_at,
    )


def _write_legacy_v1_event(
    path: Path,
    *,
    event_seq: int,
    event_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    payload: dict,
    idempotency_key: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event_dict = {
        "schema_version": "LedgerEvent.v1",
        "event_seq": event_seq,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": "2026-06-23T00:00:00Z",
        "task_id": "task_demo",
        "object_type": object_type,
        "object_id": object_id,
        "actor": {"kind": "protocol"},
        "correlation_id": None,
        "causation_event_id": None,
        "idempotency_key": idempotency_key,
        "payload": payload,
        "prev_event_hash": None,
        "event_hash": "",
    }
    event_dict["event_hash"] = _event_hash(event_dict)
    path.write_text(_canonical_json(event_dict) + "\n", encoding="utf-8")


def _event_hash(event_dict: dict) -> str:
    hash_input = {key: value for key, value in event_dict.items() if key != "event_hash"}
    return f"sha256:{sha256(_canonical_json(hash_input).encode('utf-8')).hexdigest()}"


def _canonical_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
