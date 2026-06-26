import json

from tokenshare.core.expansion import SplitStrategyInvocation
from tokenshare.protocol_engine import ProtocolEngine
from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventLedger, EventType

from tests.phase2_fixtures import make_config


NOW = "2026-06-25T00:00:00Z"


def test_failed_split_invocation_records_audit_event_only(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    invocation = _split_invocation(status="failed", error_kind="timeout")

    result = engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id="corr_split_failed",
    )

    events = ledger.read_all()
    assert result.event.event_type == EventType.SPLIT_STRATEGY_INVOCATION_RECORDED
    assert result.event.payload["status"] == "failed"
    assert result.event.payload["error_kind"] == "timeout"
    assert result.event.payload["error_summary"] == "split strategy timed out"
    assert _event_types(events) == [EventType.SPLIT_STRATEGY_INVOCATION_RECORDED]
    assert _has_no_authoritative_expansion_or_state_events(events)
    assert ledger.verify_hash_chain()


def test_invalid_split_result_records_audit_event_only(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    invocation = _split_invocation(
        invocation_id="split_invocation:scope_complete:attempt:2",
        invocation_attempt_no=2,
        status="invalid_result",
        error_kind="missing_complete_body",
        error_summary="result action complete did not include complete body",
    )

    result = engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id="corr_split_invalid",
    )

    events = ledger.read_all()
    assert result.event.event_type == EventType.SPLIT_STRATEGY_INVOCATION_RECORDED
    assert result.event.payload["status"] == "invalid_result"
    assert result.event.payload["result_action"] is None
    assert result.event.payload["result_digest"] is None
    assert _event_types(events) == [EventType.SPLIT_STRATEGY_INVOCATION_RECORDED]
    assert _has_no_authoritative_expansion_or_state_events(events)


def test_succeeded_invocation_does_not_mutate_graph_or_state(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    invocation = _split_invocation(
        status="succeeded",
        result_action="complete",
        result_digest="sha256:split_result_digest",
    )

    result = engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id="corr_split_succeeded",
    )

    events = ledger.read_all()
    assert result.invocation == invocation
    assert result.event.payload["status"] == "succeeded"
    assert result.event.payload["result_action"] == "complete"
    assert result.event.payload["result_digest"] == "sha256:split_result_digest"
    assert _event_types(events) == [EventType.SPLIT_STRATEGY_INVOCATION_RECORDED]
    assert _has_no_authoritative_expansion_or_state_events(events)


def test_succeeded_invocation_records_only_digest_and_not_full_result_body(tmp_path) -> None:
    engine, ledger = _make_engine(tmp_path)
    invocation = _split_invocation(
        status="succeeded",
        result_action="complete",
        result_digest="sha256:split_result_digest",
        metadata={
            "result_body": {
                "complete": {
                    "completion_evidence": {
                        "proof_material": "do-not-inline-full-result-body"
                    }
                }
            }
        },
    )

    result = engine.record_split_strategy_invocation(
        invocation=invocation,
        correlation_id="corr_split_summary_only",
    )

    serialized_payload = json.dumps(result.event.payload, sort_keys=True)
    assert result.event.payload["result_action"] == "complete"
    assert result.event.payload["result_digest"] == "sha256:split_result_digest"
    assert "result_body" not in serialized_payload
    assert "do-not-inline-full-result-body" not in serialized_payload
    assert "proposal" not in result.event.payload
    assert "merge_plan" not in result.event.payload
    assert ledger.verify_hash_chain()


def _make_engine(tmp_path):
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=ArtifactStore(tmp_path),
    )
    return engine, ledger


def _split_invocation(
    *,
    invocation_id: str = "split_invocation:scope_complete:attempt:1",
    invocation_attempt_no: int = 1,
    status: str,
    result_action: str | None = None,
    result_digest: str | None = None,
    error_kind: str | None = None,
    error_summary: str | None = None,
    metadata: dict | None = None,
) -> SplitStrategyInvocation:
    return SplitStrategyInvocation(
        invocation_id=invocation_id,
        invocation_attempt_no=invocation_attempt_no,
        expansion_scope_hash="sha256:scope_complete",
        task_id="task_demo",
        unit_id="unit_ready",
        canonical_selection_id="canonical_selection:task_demo:unit_ready",
        canonical_output_bundle_digest="sha256:canonical_bundle",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        split_strategy_id="structured_report_sections_v1",
        split_strategy_params_digest="sha256:params",
        status=status,
        result_action=result_action,
        result_digest=result_digest,
        error_kind=error_kind,
        error_summary=error_summary or (
            "split strategy timed out" if status == "failed" else None
        ),
        started_at=NOW,
        completed_at=NOW,
        metadata=metadata,
    )


def _event_types(events):
    return [event.event_type for event in events]


def _has_no_authoritative_expansion_or_state_events(events) -> bool:
    forbidden = {
        EventType.EXPANSION_DECISION_RECORDED,
        EventType.DECOMPOSITION_PROPOSAL_RECORDED,
        EventType.MERGE_PLAN_RECORDED,
        EventType.TASK_EXPANDED,
        EventType.TASK_UNIT_CREATED,
        EventType.TASK_RELATION_CREATED,
        EventType.TASK_UNIT_STATE_CHANGED,
        EventType.ATTEMPT_STATE_CHANGED,
    }
    return not any(event.event_type in forbidden for event in events)
