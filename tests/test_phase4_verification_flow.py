import pytest

from tokenshare.core.models import Attempt, AttemptState
from tokenshare.core.verification import REQUIRED_VERIFICATION_LAYERS, VerificationReport, build_verification_report
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.phase2_fixtures import make_artifact_ref, make_config


NOW = "2026-06-24T00:00:00Z"


def test_record_passed_verification_writes_report_event_and_advances_attempt_to_verified(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    answer_ref = make_artifact_ref("artifact_answer")
    attempt = _submitted_attempt(candidate_output_refs={"answer": answer_ref})
    report = _verification_report("verification_report_passed", attempt)

    result = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id="corr_verify_passed",
    )

    events = ledger.read_all()
    assert result.event.event_type == EventType.VERIFICATION_RECORDED
    assert result.event.payload["status"] == "passed"
    assert result.event.payload["eligible_for_canonical"] is True
    assert result.event.payload["verification_report"]["verification_report_id"] == (
        "verification_report_passed"
    )
    assert result.event.payload["verification_report_digest"].startswith("sha256:")
    assert result.attempt is not None
    assert result.attempt.state == AttemptState.VERIFIED
    assert result.attempt_event is not None
    assert result.attempt_event.payload["old_state"] == "Submitted"
    assert result.attempt_event.payload["new_state"] == "Verified"
    assert [event.event_type for event in events] == [
        EventType.VERIFICATION_RECORDED,
        EventType.ATTEMPT_STATE_CHANGED,
    ]


def test_record_rejected_verification_advances_attempt_to_rejected(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    answer_ref = make_artifact_ref("artifact_answer")
    attempt = _submitted_attempt(candidate_output_refs={"answer": answer_ref})
    report = _verification_report(
        "verification_report_rejected",
        attempt,
        status="rejected",
        plugin_domain_status="rejected",
    )

    result = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id="corr_verify_rejected",
    )

    assert result.event.payload["status"] == "rejected"
    assert result.event.payload["eligible_for_canonical"] is False
    assert result.attempt is not None
    assert result.attempt.state == AttemptState.REJECTED
    assert result.attempt.failure_kind == "invalid_output"
    assert result.attempt_event is not None
    assert result.attempt_event.payload["new_state"] == "Rejected"
    assert [event.event_type for event in ledger.read_all()] == [
        EventType.VERIFICATION_RECORDED,
        EventType.ATTEMPT_STATE_CHANGED,
    ]


def test_verification_error_records_event_without_attempt_state_change(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    answer_ref = make_artifact_ref("artifact_answer")
    attempt = _submitted_attempt(candidate_output_refs={"answer": answer_ref})
    report = _verification_report(
        "verification_report_error",
        attempt,
        status="error",
    )

    result = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id="corr_verify_error",
    )

    assert result.event.event_type == EventType.VERIFICATION_RECORDED
    assert result.event.payload["status"] == "error"
    assert result.event.payload["eligible_for_canonical"] is False
    assert result.attempt is None
    assert result.attempt_event is None
    events = ledger.read_all()
    assert len(events) == 1
    assert events[0].event_type == EventType.VERIFICATION_RECORDED


def test_record_verification_rejects_report_marked_eligible_when_required_layer_failed(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    answer_ref = make_artifact_ref("artifact_answer")
    attempt = _submitted_attempt(candidate_output_refs={"answer": answer_ref})
    report = _verification_report(
        "verification_report_forged",
        attempt,
        status="passed",
        required_evidence_ref_ids=["evidence_required"],
        available_evidence_ref_ids=[],
    )
    object.__setattr__(report, "eligible_for_canonical", True)

    with pytest.raises(ValueError, match="eligible_for_canonical"):
        engine.record_verification(
            report=report,
            attempt=attempt,
            correlation_id="corr_verify_forged",
        )

    assert ledger.read_all() == []


def test_record_verification_rejects_passed_status_when_required_layer_failed(
    tmp_path,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    attempt = _submitted_attempt(candidate_output_refs={})
    report = _verification_report(
        "verification_report_passed_but_missing_output",
        attempt,
        status="passed",
        candidate_output_refs={},
    )

    with pytest.raises(ValueError, match="passed verification report"):
        engine.record_verification(
            report=report,
            attempt=attempt,
            correlation_id="corr_verify_passed_invalid",
        )

    assert ledger.read_all() == []


@pytest.mark.parametrize(
    ("report_id", "candidate_output_refs", "expected_hashes", "required_evidence", "available_evidence", "plugin_domain_status", "failed_layer"),
    [
        (
            "verification_report_missing_output",
            {},
            {},
            [],
            [],
            "passed",
            "required_output_coverage_check",
        ),
        (
            "verification_report_bad_digest",
            {"answer": make_artifact_ref("artifact_answer")},
            {"answer": "sha256:different"},
            [],
            [],
            "passed",
            "artifact_integrity_check",
        ),
        (
            "verification_report_missing_evidence",
            {"answer": make_artifact_ref("artifact_answer")},
            {"answer": make_artifact_ref("artifact_answer").content_hash},
            ["evidence_required"],
            [],
            "passed",
            "evidence_reference_check",
        ),
        (
            "verification_report_domain_rejected",
            {"answer": make_artifact_ref("artifact_answer")},
            {"answer": make_artifact_ref("artifact_answer").content_hash},
            [],
            [],
            "rejected",
            "plugin_domain_check",
        ),
    ],
)
def test_record_verification_keeps_invalid_candidate_outputs_ineligible(
    tmp_path,
    report_id,
    candidate_output_refs,
    expected_hashes,
    required_evidence,
    available_evidence,
    plugin_domain_status,
    failed_layer,
) -> None:
    engine, ledger = _make_engine(tmp_path)
    attempt = _submitted_attempt(candidate_output_refs=candidate_output_refs)
    report = _verification_report(
        report_id,
        attempt,
        status="rejected",
        candidate_output_refs=candidate_output_refs,
        expected_hashes=expected_hashes,
        required_evidence_ref_ids=required_evidence,
        available_evidence_ref_ids=available_evidence,
        plugin_domain_status=plugin_domain_status,
    )

    result = engine.record_verification(
        report=report,
        attempt=attempt,
        correlation_id=f"corr_{report_id}",
    )

    assert result.event.payload["eligible_for_canonical"] is False
    assert result.event.payload["verification_report"]["layer_results"][failed_layer][
        "status"
    ] == "rejected"
    assert result.attempt is not None
    assert result.attempt.state == AttemptState.REJECTED
    assert [event.event_type for event in ledger.read_all()] == [
        EventType.VERIFICATION_RECORDED,
        EventType.ATTEMPT_STATE_CHANGED,
    ]


def _make_engine(tmp_path):
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=ArtifactStore(tmp_path),
    )
    return engine, ledger


def _submitted_attempt(
    *,
    attempt_id: str = "attempt_1",
    unit_id: str = "unit_ready",
    candidate_output_refs: dict | None = None,
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
        candidate_output_refs=(
            {"answer": make_artifact_ref("artifact_answer")}
            if candidate_output_refs is None
            else candidate_output_refs
        ),
        metadata={},
    )


def _verification_report(
    report_id: str,
    attempt: Attempt,
    *,
    status: str = "passed",
    candidate_output_refs: dict | None = None,
    expected_hashes: dict | None = None,
    required_evidence_ref_ids: list[str] | None = None,
    available_evidence_ref_ids: list[str] | None = None,
    plugin_domain_status: str = "passed",
    audit_status: str = "passed",
    completed_at: str = NOW,
) -> VerificationReport:
    output_refs = candidate_output_refs if candidate_output_refs is not None else dict(attempt.candidate_output_refs or {})
    expected = expected_hashes
    if expected is None:
        expected = {name: ref.content_hash for name, ref in output_refs.items()}
    available_evidence = available_evidence_ref_ids
    if available_evidence is None:
        available_evidence = list(output_refs)
    return build_verification_report(
        verification_report_id=report_id,
        task_id=attempt.task_id,
        unit_id=attempt.unit_id,
        attempt_id=attempt.attempt_id,
        submission_id=f"submission_{attempt.attempt_id}",
        submission_event_seq=7,
        candidate_output_refs=output_refs,
        required_output_names=["answer"],
        output_contract_id="contract_answer",
        validator_policy_id="validator_policy_v1",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status=status,
        expected_artifact_hashes=expected,
        required_evidence_ref_ids=required_evidence_ref_ids or [],
        available_evidence_ref_ids=available_evidence,
        plugin_domain_status=plugin_domain_status,
        audit_status=audit_status,
        verification_environment={"runtime": "pytest"},
        verifier={"verifier_id": "verifier_local", "verifier_version": "1"},
        started_at=NOW,
        completed_at=completed_at,
    )


def _all_passed_layers() -> dict:
    return {
        layer_name: {
            "status": "passed",
            "reason_code": "ok",
            "summary": "ok",
            "evidence_refs": [],
            "checked_at": NOW,
        }
        for layer_name in REQUIRED_VERIFICATION_LAYERS
    }
