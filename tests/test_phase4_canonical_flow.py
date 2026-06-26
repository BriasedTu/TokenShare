from dataclasses import replace

import pytest

from tokenshare.core.models import Attempt, AttemptState, TaskState
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import build_verification_report
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.phase2_fixtures import make_artifact_ref, make_config, make_relation, make_unit


NOW = "2026-06-24T00:00:00Z"


def test_bind_canonical_outputs_selects_earliest_eligible_verification_event_seq(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    first_ref = make_artifact_ref("artifact_recorded_first")
    second_ref = make_artifact_ref("artifact_recorded_second")
    first = _record_verified_attempt(
        engine,
        attempt_id="attempt_recorded_first",
        output_ref=first_ref,
        completed_at="2026-06-24T00:00:20Z",
    )
    second = _record_verified_attempt(
        engine,
        attempt_id="attempt_recorded_second",
        output_ref=second_ref,
        completed_at="2026-06-24T00:00:10Z",
    )

    result = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[first.event, second.event],
        attempts_by_id={
            first.attempt.attempt_id: first.attempt,
            second.attempt.attempt_id: second.attempt,
        },
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_first",
    )

    assert result.event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
    assert result.canonical_selection.selected_attempt_id == "attempt_recorded_first"
    assert result.canonical_selection.selected_verification_event_seq == first.event.event_seq
    assert result.canonical_selection.canonical_output_refs == {"answer": first_ref}
    assert result.attempt.state == AttemptState.CANONICAL
    assert result.attempt_event.payload["old_state"] == "Verified"
    assert result.attempt_event.payload["new_state"] == "Canonical"
    assert ledger.verify_hash_chain()


def test_bind_canonical_outputs_rejects_unrecorded_verification_event(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    other_engine, _ = _make_engine(tmp_path / "other")
    unrecorded = _record_verified_attempt(
        other_engine,
        attempt_id="attempt_unrecorded",
        output_ref=make_artifact_ref("artifact_unrecorded"),
    )

    with pytest.raises(ValueError, match="recorded verification"):
        engine.bind_canonical_outputs(
            task_id="task_demo",
            unit_id="unit_ready",
            verification_events=[unrecorded.event],
            attempts_by_id={unrecorded.attempt.attempt_id: unrecorded.attempt},
            policy="first_verified_bundle",
            now="2026-06-24T00:00:30Z",
            correlation_id="corr_bind_unrecorded",
        )

    assert not any(
        event.event_type == EventType.CANONICAL_OUTPUTS_BOUND
        for event in ledger.read_all()
    )


def test_bind_canonical_outputs_uses_recorded_payload_not_caller_mutation(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    recorded = _record_verified_attempt(
        engine,
        attempt_id="attempt_recorded",
        output_ref=make_artifact_ref("artifact_recorded"),
    )
    tampered_ref = make_artifact_ref("artifact_tampered")
    tampered_report = dict(recorded.event.payload["verification_report"])
    tampered_report["attempt_id"] = "attempt_tampered"
    tampered_report["submission_id"] = "submission_tampered"
    tampered_report["candidate_output_refs"] = {"answer": tampered_ref.to_dict()}
    tampered_payload = {
        **recorded.event.payload,
        "verification_report": tampered_report,
        "attempt_id": "attempt_tampered",
        "submission_id": "submission_tampered",
        "candidate_output_refs": {"answer": tampered_ref.to_dict()},
    }
    tampered_event = replace(recorded.event, payload=tampered_payload)
    tampered_attempt = Attempt(
        attempt_id="attempt_tampered",
        task_id="task_demo",
        unit_id="unit_ready",
        lease_id="lease_tampered",
        client_id="client_local",
        state=AttemptState.VERIFIED,
        attempt_kind="primary",
        created_at=NOW,
        started_at=NOW,
        submitted_at=NOW,
        finished_at=NOW,
        candidate_output_refs={"answer": tampered_ref},
        metadata={},
    )

    result = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[tampered_event],
        attempts_by_id={
            recorded.attempt.attempt_id: recorded.attempt,
            tampered_attempt.attempt_id: tampered_attempt,
        },
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_tampered",
    )

    assert result.canonical_selection.selected_attempt_id == recorded.attempt.attempt_id
    assert result.canonical_selection.canonical_output_refs == {
        "answer": make_artifact_ref("artifact_recorded")
    }


def test_bind_canonical_outputs_is_unique_per_task_unit(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    first = _record_verified_attempt(
        engine,
        attempt_id="attempt_first",
        output_ref=make_artifact_ref("artifact_first"),
    )
    second = _record_verified_attempt(
        engine,
        attempt_id="attempt_second",
        output_ref=make_artifact_ref("artifact_second"),
    )
    first_binding = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[first.event],
        attempts_by_id={first.attempt.attempt_id: first.attempt},
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_first",
    )
    event_count = len(ledger.read_all())

    retry = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[first.event],
        attempts_by_id={first.attempt.attempt_id: first.attempt},
        policy="first_verified_bundle",
        now="2026-06-24T00:00:31Z",
        correlation_id="corr_bind_first_retry",
    )
    with pytest.raises(ValueError, match="canonical"):
        engine.bind_canonical_outputs(
            task_id="task_demo",
            unit_id="unit_ready",
            verification_events=[second.event],
            attempts_by_id={second.attempt.attempt_id: second.attempt},
            policy="first_verified_bundle",
            now="2026-06-24T00:00:32Z",
            correlation_id="corr_bind_conflict",
        )

    assert retry.event.event_id == first_binding.event.event_id
    assert len(ledger.read_all()) == event_count


def test_losing_verified_attempt_remains_verified(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    winner = _record_verified_attempt(
        engine,
        attempt_id="attempt_winner",
        output_ref=make_artifact_ref("artifact_winner"),
    )
    loser = _record_verified_attempt(
        engine,
        attempt_id="attempt_loser",
        output_ref=make_artifact_ref("artifact_loser"),
    )
    before_bind_count = len(ledger.read_all())

    engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[winner.event, loser.event],
        attempts_by_id={
            winner.attempt.attempt_id: winner.attempt,
            loser.attempt.attempt_id: loser.attempt,
        },
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_loser",
    )

    bind_events = ledger.read_all()[before_bind_count:]
    assert loser.attempt.state == AttemptState.VERIFIED
    assert not any(
        event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.object_id == loser.attempt.attempt_id
        for event in bind_events
    )


def test_late_verified_bundle_does_not_replace_existing_canonical(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    first = _record_verified_attempt(
        engine,
        attempt_id="attempt_first",
        output_ref=make_artifact_ref("artifact_first"),
    )
    first_binding = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[first.event],
        attempts_by_id={first.attempt.attempt_id: first.attempt},
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_first",
    )
    later = _record_verified_attempt(
        engine,
        attempt_id="attempt_late",
        output_ref=make_artifact_ref("artifact_late"),
    )
    event_count = len(ledger.read_all())

    retry = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_ready",
        verification_events=[first.event, later.event],
        attempts_by_id={
            first.attempt.attempt_id: first.attempt,
            later.attempt.attempt_id: later.attempt,
        },
        policy="first_verified_bundle",
        now="2026-06-24T00:00:40Z",
        correlation_id="corr_bind_after_late",
    )

    assert retry.event.event_id == first_binding.event.event_id
    assert retry.canonical_selection.selected_attempt_id == "attempt_first"
    assert len(ledger.read_all()) == event_count


def test_task_graph_replay_uses_canonical_outputs_by_unit_id_from_canonical_event(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    answer_ref = make_artifact_ref("artifact_parent_answer")
    parent = make_unit("unit_parent", state=TaskState.PROCESSING)
    child = make_unit("unit_child", state=TaskState.READY)
    relation = make_relation(
        source_unit_id="unit_parent",
        target_unit_id="unit_child",
        source_output_name="answer",
        target_input_name="parent_answer",
    )
    verified = _record_verified_attempt(
        engine,
        attempt_id="attempt_parent",
        unit_id="unit_parent",
        output_ref=answer_ref,
    )
    binding = engine.bind_canonical_outputs(
        task_id="task_demo",
        unit_id="unit_parent",
        verification_events=[verified.event],
        attempts_by_id={verified.attempt.attempt_id: verified.attempt},
        policy="first_verified_bundle",
        now="2026-06-24T00:00:30Z",
        correlation_id="corr_bind_parent",
    )

    stale_graph = TaskGraph(
        task_id="task_demo",
        units={"unit_parent": parent, "unit_child": child},
        relations=[relation],
    )
    replay_graph = TaskGraph(
        task_id="task_demo",
        units={"unit_parent": parent, "unit_child": child},
        relations=[relation],
        canonical_outputs_by_unit_id=_canonical_outputs_projection(ledger.read_all()),
    )

    assert parent.canonical_output_refs == {}
    assert "unit_child" not in stale_graph.ready_unit_ids()
    assert replay_graph.canonical_outputs_by_unit_id["unit_parent"] == (
        binding.canonical_selection.canonical_output_refs
    )
    assert replay_graph.ready_unit_ids() == ["unit_child"]


def _make_engine(tmp_path):
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=ArtifactStore(tmp_path),
    )
    return engine, ledger


def _record_verified_attempt(
    engine: ProtocolEngine,
    *,
    attempt_id: str,
    output_ref,
    unit_id: str = "unit_ready",
    completed_at: str = NOW,
):
    attempt = _submitted_attempt(
        attempt_id=attempt_id,
        unit_id=unit_id,
        candidate_output_refs={"answer": output_ref},
    )
    report = build_verification_report(
        verification_report_id=f"verification_report_{attempt_id}",
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=f"submission_{attempt_id}",
        submission_event_seq=10,
        candidate_output_refs={"answer": output_ref},
        required_output_names=["answer"],
        output_contract_id="contract_answer",
        validator_policy_id="validator_policy_v1",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": output_ref.content_hash},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={"runtime": "pytest"},
        verifier={"verifier_id": "verifier_local", "verifier_version": "1"},
        started_at=NOW,
        completed_at=completed_at,
    )
    result = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id=f"corr_verify_{attempt_id}",
    )
    assert result.attempt is not None
    return result


def _submitted_attempt(
    *,
    attempt_id: str,
    unit_id: str,
    candidate_output_refs: dict,
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        task_id="task_demo",
        unit_id=unit_id,
        lease_id=f"lease_{attempt_id}",
        client_id="client_local",
        state=AttemptState.SUBMITTED,
        attempt_kind="primary",
        created_at="2026-06-24T00:00:00Z",
        started_at="2026-06-24T00:00:01Z",
        submitted_at="2026-06-24T00:00:02Z",
        candidate_output_refs=candidate_output_refs,
        metadata={},
    )


def _canonical_outputs_projection(events):
    projection = {}
    for event in events:
        if event.event_type != EventType.CANONICAL_OUTPUTS_BOUND:
            continue
        selection = event.payload["canonical_selection"]
        projection[selection["unit_id"]] = binding_refs = {}
        for output_name, ref_data in selection["canonical_output_refs"].items():
            binding_refs[output_name] = make_artifact_ref(ref_data["artifact_id"])
    return projection
