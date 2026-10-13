import sqlite3
from dataclasses import replace

import pytest

from tokenshare.storage.artifacts import ArtifactStore
from tokenshare.storage.events import EventDraft, EventLedger, EventType
from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex

from tests.phase2_fixtures import make_artifact_ref


NOW = "2026-06-25T00:00:00Z"
TASK_ID = "task_demo"
UNIT_ID = "unit_ready"
CANONICAL_SELECTION_ID = "canonical_selection:task_demo:unit_ready"
CANONICAL_DIGEST = "sha256:canonical_bundle"
SCOPE_HASH = "sha256:scope_expand"
DECISION_ID = f"expansion_decision:{SCOPE_HASH}"
PROPOSAL_ID = "decomposition_proposal_expand_1"
PROPOSAL_DIGEST = "sha256:proposal_expand_1"
MERGE_PLAN_ID = "merge_plan_expand_1"
MERGE_PLAN_DIGEST = "sha256:merge_plan_expand_1"


def test_sqlite_index_rebuilds_verification_and_canonical_outputs(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    verification = _append_verification(ledger)
    _append_canonical(ledger, selected_verification_event_seq=verification.event_seq)

    SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
        ledger.read_all()
    )

    with sqlite3.connect(tmp_path / "index.sqlite") as connection:
        verification_rows = connection.execute(
            """
            select verification_report_id, task_id, unit_id, attempt_id,
                submission_id, status, eligible_for_canonical,
                candidate_output_bundle_digest, completed_at, source_event_seq
            from verification_reports
            """
        ).fetchall()
        canonical_rows = connection.execute(
            """
            select canonical_selection_id, task_id, unit_id, selected_attempt_id,
                selected_verification_event_seq, canonical_output_bundle_digest,
                bound_at, source_event_seq
            from canonical_outputs
            """
        ).fetchall()

    assert verification_rows == [
        (
            "verification_report_1",
            TASK_ID,
            UNIT_ID,
            "attempt_verified",
            "submission_verified",
            "passed",
            1,
            "sha256:candidate_bundle",
            NOW,
            verification.event_seq,
        )
    ]
    assert canonical_rows == [
        (
            CANONICAL_SELECTION_ID,
            TASK_ID,
            UNIT_ID,
            "attempt_verified",
            verification.event_seq,
            CANONICAL_DIGEST,
            NOW,
            verification.event_seq + 1,
        )
    ]


def test_sqlite_index_rebuilds_split_invocation_audit(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    invocation = _append_split_invocation(ledger)

    SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
        ledger.read_all()
    )

    with sqlite3.connect(tmp_path / "index.sqlite") as connection:
        rows = connection.execute(
            """
            select invocation_id, invocation_attempt_no, task_id, unit_id,
                canonical_selection_id, canonical_output_bundle_digest,
                plugin_id, plugin_version, plugin_descriptor_digest,
                split_strategy_id, split_strategy_params_digest,
                expansion_scope_hash, status, result_action, result_digest,
                error_kind, error_summary, started_at, completed_at, source_event_seq
            from split_strategy_invocations
            """
        ).fetchall()

    assert rows == [
        (
            "split_invocation:scope_expand:attempt:1",
            1,
            TASK_ID,
            UNIT_ID,
            CANONICAL_SELECTION_ID,
            CANONICAL_DIGEST,
            "structured_report_stub",
            "0.1.0",
            "sha256:plugin_descriptor",
            "structured_report_sections_v1",
            "sha256:params",
            SCOPE_HASH,
            "succeeded",
            "expand",
            "sha256:split_result",
            None,
            None,
            NOW,
            NOW,
            invocation.event_seq,
        )
    ]


def test_sqlite_index_exposes_expansion_rows_only_after_task_expanded(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)

    _append_unbatched_expansion_records(ledger, proposal_ref, merge_plan_ref)
    SQLiteMaterializedIndex(
        tmp_path / "index_before.sqlite", artifact_store=store
    ).rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "index_before.sqlite") as connection:
        assert connection.execute("select count(*) from decomposition_proposals").fetchone() == (
            0,
        )
        assert connection.execute("select count(*) from merge_plans").fetchone() == (0,)
        assert connection.execute("select count(*) from expected_output_refs").fetchone() == (
            0,
        )

    _append_complete_expansion_batch(ledger, proposal_ref, merge_plan_ref)
    SQLiteMaterializedIndex(
        tmp_path / "index_after.sqlite", artifact_store=store
    ).rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "index_after.sqlite") as connection:
        proposal_rows = connection.execute(
            """
            select proposal_id, expansion_decision_id, expansion_scope_hash, visible
            from decomposition_proposals
            """
        ).fetchall()
        decision_rows = connection.execute(
            """
            select expansion_decision_id, action, proposal_id, merge_plan_id,
                batch_id, source_event_seq
            from expansion_decisions
            """
        ).fetchall()
        merge_plan_rows = connection.execute(
            """
            select merge_plan_id, expansion_decision_id, decomposition_proposal_id,
                visible
            from merge_plans
            """
        ).fetchall()

    assert proposal_rows == [(PROPOSAL_ID, DECISION_ID, SCOPE_HASH, 1)]
    assert decision_rows == [
        (
            DECISION_ID,
            "expand",
            PROPOSAL_ID,
            MERGE_PLAN_ID,
            f"expansion_batch:{DECISION_ID}",
            ledger.read_all()[-4].event_seq,
        )
    ]
    assert merge_plan_rows == [(MERGE_PLAN_ID, DECISION_ID, PROPOSAL_ID, 1)]


def test_sqlite_index_rejects_duplicate_canonical_outputs_for_same_unit(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    verification = _append_verification(ledger)
    _append_canonical(
        ledger,
        selected_verification_event_seq=verification.event_seq,
        canonical_selection_id=CANONICAL_SELECTION_ID,
        canonical_digest=CANONICAL_DIGEST,
        idempotency_key="canonical_outputs:task_demo:unit_ready:first",
    )
    _append_canonical(
        ledger,
        selected_verification_event_seq=verification.event_seq,
        canonical_selection_id="canonical_selection:task_demo:unit_ready:second",
        canonical_digest="sha256:different_bundle",
        idempotency_key="canonical_outputs:task_demo:unit_ready:second",
    )

    with pytest.raises(ValueError, match="canonical outputs conflict"):
        SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
            ledger.read_all()
        )


def test_sqlite_index_rejects_incomplete_completion_batch(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.EXPANSION_DECISION_RECORDED,
                object_type="ExpansionDecision",
                object_id="expansion_decision:complete",
                task_id=TASK_ID,
                idempotency_key="expansion_decision:complete",
                payload=_decision_payload(
                    action="complete",
                    decision_id="expansion_decision:complete",
                    scope_hash="sha256:scope_complete",
                    proposal_id=None,
                    proposal_digest=None,
                    merge_plan_id=None,
                    merge_plan_digest=None,
                ),
                occurred_at=NOW,
            )
        ],
        batch_id="completion_batch:expansion_decision:complete",
    )

    with pytest.raises(ValueError, match="incomplete completion_batch"):
        SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
            ledger.read_all()
        )


def test_sqlite_index_rejects_completion_batch_state_for_wrong_unit(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append_batch(
        [
            EventDraft(
                event_type=EventType.EXPANSION_DECISION_RECORDED,
                object_type="ExpansionDecision",
                object_id="expansion_decision:complete",
                task_id=TASK_ID,
                idempotency_key="expansion_decision:complete",
                payload=_decision_payload(
                    action="complete",
                    decision_id="expansion_decision:complete",
                    scope_hash="sha256:scope_complete",
                    proposal_id=None,
                    proposal_digest=None,
                    merge_plan_id=None,
                    merge_plan_digest=None,
                ),
                occurred_at=NOW,
            ),
            EventDraft(
                event_type=EventType.TASK_UNIT_STATE_CHANGED,
                object_type="TaskUnit",
                object_id="unit_other",
                task_id=TASK_ID,
                idempotency_key="task_state:unit_other:completed",
                payload={
                    "schema_version": "phase4.complete_task_unit_state_change_record.v1",
                    "task_unit_state_change": {
                        "task_id": TASK_ID,
                        "unit_id": "unit_other",
                        "old_state": "Processing",
                        "new_state": "Completed",
                        "changed_at": NOW,
                    },
                    "task_unit": {
                        "schema_version": "TaskUnit.v1",
                        "unit_id": "unit_other",
                        "task_id": TASK_ID,
                        "state": "Completed",
                    },
                    "expansion_decision_id": "expansion_decision:complete",
                },
                occurred_at=NOW,
            ),
        ],
        batch_id="completion_batch:expansion_decision:complete",
    )

    with pytest.raises(ValueError, match="incomplete completion_batch"):
        SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
            ledger.read_all()
        )


def test_sqlite_index_rejects_incomplete_expansion_batch(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    ledger.append_batch(
        _expansion_record_drafts(proposal_ref, merge_plan_ref),
        batch_id=f"expansion_batch:{DECISION_ID}",
    )

    with pytest.raises(ValueError, match="incomplete expansion_batch"):
        SQLiteMaterializedIndex(
            tmp_path / "index.sqlite", artifact_store=store
        ).rebuild_from_events(ledger.read_all())


def test_sqlite_index_rejects_expansion_batch_without_artifact_store(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    _append_complete_expansion_batch(ledger, proposal_ref, merge_plan_ref)

    with pytest.raises(ValueError, match="artifact_store"):
        SQLiteMaterializedIndex(tmp_path / "index.sqlite").rebuild_from_events(
            ledger.read_all()
        )


def test_sqlite_index_rejects_non_contiguous_batch_event_seq(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    _append_complete_expansion_batch(ledger, proposal_ref, merge_plan_ref)
    events = list(ledger.read_all())
    events[1] = replace(events[1], event_seq=events[1].event_seq + 10)

    with pytest.raises(ValueError, match="incomplete expansion_batch|batch envelope"):
        SQLiteMaterializedIndex(
            tmp_path / "index.sqlite", artifact_store=store
        ).rebuild_from_events(events)


def test_sqlite_index_rejects_task_expanded_child_id_mismatch(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    marker = _task_expanded_draft()
    marker_payload = {**marker.payload, "child_unit_ids": ["unit_other"]}
    ledger.append_batch(
        [
            *_expansion_record_drafts(proposal_ref, merge_plan_ref),
            _child_unit_draft(),
            replace(marker, payload=marker_payload),
        ],
        batch_id=f"expansion_batch:{DECISION_ID}",
    )

    with pytest.raises(ValueError, match="incomplete expansion_batch"):
        SQLiteMaterializedIndex(
            tmp_path / "index.sqlite", artifact_store=store
        ).rebuild_from_events(ledger.read_all())


def test_sqlite_index_rejects_merge_plan_decision_id_mismatch(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    drafts = _expansion_record_drafts(proposal_ref, merge_plan_ref)
    bad_merge_payload = {
        **drafts[2].payload,
        "expansion_decision_id": "expansion_decision:other",
    }
    ledger.append_batch(
        [
            drafts[0],
            drafts[1],
            replace(drafts[2], payload=bad_merge_payload),
            _child_unit_draft(),
            _task_expanded_draft(),
        ],
        batch_id=f"expansion_batch:{DECISION_ID}",
    )

    with pytest.raises(ValueError, match="incomplete expansion_batch"):
        SQLiteMaterializedIndex(
            tmp_path / "index.sqlite", artifact_store=store
        ).rebuild_from_events(ledger.read_all())


def test_expected_output_refs_projection_from_accepted_proposal_and_task_expanded(
    tmp_path,
) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    store = ArtifactStore(tmp_path)
    proposal_ref, merge_plan_ref = _save_expansion_artifacts(store)
    _append_complete_expansion_batch(ledger, proposal_ref, merge_plan_ref)

    SQLiteMaterializedIndex(
        tmp_path / "index.sqlite", artifact_store=store
    ).rebuild_from_events(ledger.read_all())

    with sqlite3.connect(tmp_path / "index.sqlite") as connection:
        rows = connection.execute(
            """
            select expected_output_id, task_id, owner_unit_id, output_name,
                resolution_kind, resolution_status, child_unit_id,
                child_output_name, merge_plan_id, canonical_selection_id,
                canonical_output_bundle_digest, source_proposal_id,
                source_expansion_decision_id, created_event_seq, resolved_event_seq,
                payload_json
            from expected_output_refs
            """
        ).fetchall()

    assert len(rows) == 1
    (
        expected_output_id,
        task_id,
        owner_unit_id,
        output_name,
        resolution_kind,
        resolution_status,
        child_unit_id,
        child_output_name,
        merge_plan_id,
        canonical_selection_id,
        canonical_output_bundle_digest,
        source_proposal_id,
        source_expansion_decision_id,
        created_event_seq,
        resolved_event_seq,
        payload_json,
    ) = rows[0]
    assert expected_output_id == (
        f"expected_output:{PROPOSAL_ID}:{UNIT_ID}:answer:0"
    )
    assert task_id == TASK_ID
    assert owner_unit_id == UNIT_ID
    assert output_name == "answer"
    assert resolution_kind == "merge_plan_output"
    assert resolution_status == "expected"
    assert child_unit_id is None
    assert child_output_name is None
    assert merge_plan_id == MERGE_PLAN_ID
    assert canonical_selection_id == CANONICAL_SELECTION_ID
    assert canonical_output_bundle_digest == CANONICAL_DIGEST
    assert source_proposal_id == PROPOSAL_ID
    assert source_expansion_decision_id == DECISION_ID
    assert created_event_seq == ledger.read_all()[-1].event_seq
    assert resolved_event_seq is None
    assert '"merge_slot_id":"slot_intro"' in payload_json


def _append_verification(ledger: EventLedger):
    answer_ref = make_artifact_ref("artifact_answer")
    return ledger.append(
        event_type=EventType.VERIFICATION_RECORDED,
        object_type="VerificationReport",
        object_id="verification_report_1",
        task_id=TASK_ID,
        idempotency_key="verification:submission_verified:validator",
        payload={
            "schema_version": "phase4.verification_record.v1",
            "verification_report": {
                "verification_report_id": "verification_report_1",
                "task_id": TASK_ID,
                "unit_id": UNIT_ID,
                "attempt_id": "attempt_verified",
                "submission_id": "submission_verified",
                "submission_event_seq": 7,
                "candidate_output_refs": {"answer": answer_ref.to_dict()},
                "candidate_output_bundle_digest": "sha256:candidate_bundle",
                "validator_policy_id": "structured_report_stub_validator_v1",
                "plugin_id": "structured_report_stub",
                "plugin_version": "0.1.0",
                "status": "passed",
                "eligible_for_canonical": True,
                "completed_at": NOW,
            },
            "verification_report_digest": "sha256:verification_report",
            "status": "passed",
            "eligible_for_canonical": True,
            "task_id": TASK_ID,
            "unit_id": UNIT_ID,
            "attempt_id": "attempt_verified",
            "submission_id": "submission_verified",
            "submission_event_seq": 7,
            "candidate_output_bundle_digest": "sha256:candidate_bundle",
            "validator_policy_id": "structured_report_stub_validator_v1",
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "completed_at": NOW,
        },
        occurred_at=NOW,
    )


def _append_canonical(
    ledger: EventLedger,
    *,
    selected_verification_event_seq: int,
    canonical_selection_id: str = CANONICAL_SELECTION_ID,
    canonical_digest: str = CANONICAL_DIGEST,
    idempotency_key: str = "canonical_outputs:task_demo:unit_ready",
):
    answer_ref = make_artifact_ref("artifact_answer")
    return ledger.append(
        event_type=EventType.CANONICAL_OUTPUTS_BOUND,
        object_type="CanonicalSelection",
        object_id=canonical_selection_id,
        task_id=TASK_ID,
        idempotency_key=idempotency_key,
        payload={
            "schema_version": "phase4.canonical_outputs_bound.v1",
            "canonical_selection": {
                "schema_version": "phase4.canonical_selection.v1",
                "canonical_selection_id": canonical_selection_id,
                "task_id": TASK_ID,
                "unit_id": UNIT_ID,
                "selection_policy": "first_verified_bundle",
                "selection_policy_version": "v1",
                "selected_verification_report_id": "verification_report_1",
                "selected_verification_event_seq": selected_verification_event_seq,
                "selected_submission_id": "submission_verified",
                "selected_submission_event_seq": 7,
                "selected_attempt_id": "attempt_verified",
                "canonical_output_bundle_digest": canonical_digest,
                "canonical_output_refs": {"answer": answer_ref.to_dict()},
                "bound_at": NOW,
            },
            "canonical_selection_digest": "sha256:canonical_selection",
            "task_id": TASK_ID,
            "unit_id": UNIT_ID,
            "selection_policy": "first_verified_bundle",
            "selection_policy_version": "v1",
            "selected_verification_report_id": "verification_report_1",
            "selected_verification_event_seq": selected_verification_event_seq,
            "selected_submission_id": "submission_verified",
            "selected_submission_event_seq": 7,
            "selected_attempt_id": "attempt_verified",
            "canonical_output_bundle_digest": canonical_digest,
            "canonical_output_refs": {"answer": answer_ref.to_dict()},
            "bound_at": NOW,
        },
        occurred_at=NOW,
    )


def _append_split_invocation(ledger: EventLedger):
    return ledger.append(
        event_type=EventType.SPLIT_STRATEGY_INVOCATION_RECORDED,
        object_type="SplitStrategyInvocation",
        object_id="split_invocation:scope_expand:attempt:1",
        task_id=TASK_ID,
        idempotency_key="split_invocation:scope_expand:attempt:1",
        payload={
            "schema_version": "phase4.split_strategy_invocation_record.v1",
            "invocation": {
                "schema_version": "phase4.split_strategy_invocation.v1",
                "invocation_id": "split_invocation:scope_expand:attempt:1",
                "invocation_attempt_no": 1,
                "expansion_scope_hash": SCOPE_HASH,
                "task_id": TASK_ID,
                "unit_id": UNIT_ID,
                "canonical_selection_id": CANONICAL_SELECTION_ID,
                "canonical_output_bundle_digest": CANONICAL_DIGEST,
                "plugin_id": "structured_report_stub",
                "plugin_version": "0.1.0",
                "plugin_descriptor_digest": "sha256:plugin_descriptor",
                "split_strategy_id": "structured_report_sections_v1",
                "split_strategy_params_digest": "sha256:params",
                "status": "succeeded",
                "result_action": "expand",
                "result_digest": "sha256:split_result",
                "error_kind": None,
                "error_summary": None,
                "started_at": NOW,
                "completed_at": NOW,
            },
            "task_id": TASK_ID,
            "unit_id": UNIT_ID,
            "canonical_selection_id": CANONICAL_SELECTION_ID,
            "canonical_output_bundle_digest": CANONICAL_DIGEST,
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_digest": "sha256:plugin_descriptor",
            "split_strategy_id": "structured_report_sections_v1",
            "split_strategy_params_digest": "sha256:params",
            "expansion_scope_hash": SCOPE_HASH,
            "status": "succeeded",
            "result_action": "expand",
            "result_digest": "sha256:split_result",
            "error_kind": None,
            "error_summary": None,
            "started_at": NOW,
            "completed_at": NOW,
        },
        occurred_at=NOW,
    )


def _save_expansion_artifacts(store: ArtifactStore):
    proposal_ref = store.save_json(
        _proposal_document(),
        artifact_id=PROPOSAL_ID,
        artifact_type="DecompositionProposal",
        artifact_schema_id="phase4.decomposition_proposal",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"task_id": TASK_ID, "unit_id": UNIT_ID},
        created_at=NOW,
    )
    merge_plan_ref = store.save_json(
        _merge_plan_document(),
        artifact_id=MERGE_PLAN_ID,
        artifact_type="MergePlan",
        artifact_schema_id="phase4.merge_plan",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"task_id": TASK_ID, "unit_id": UNIT_ID},
        created_at=NOW,
    )
    return proposal_ref, merge_plan_ref


def _append_unbatched_expansion_records(ledger, proposal_ref, merge_plan_ref) -> None:
    for draft in [
        *_expansion_record_drafts(proposal_ref, merge_plan_ref),
        _task_expanded_draft(),
    ]:
        ledger.append(
            event_type=draft.event_type,
            object_type=draft.object_type,
            object_id=draft.object_id,
            task_id=draft.task_id,
            idempotency_key=f"unbatched:{draft.idempotency_key}",
            payload=draft.payload,
            occurred_at=draft.occurred_at,
        )


def _append_complete_expansion_batch(ledger, proposal_ref, merge_plan_ref) -> None:
    ledger.append_batch(
        [
            *_expansion_record_drafts(proposal_ref, merge_plan_ref),
            _child_unit_draft(),
            _task_expanded_draft(),
        ],
        batch_id=f"expansion_batch:{DECISION_ID}",
    )


def _expansion_record_drafts(proposal_ref, merge_plan_ref) -> list[EventDraft]:
    return [
        EventDraft(
            event_type=EventType.DECOMPOSITION_PROPOSAL_RECORDED,
            object_type="DecompositionProposal",
            object_id=PROPOSAL_ID,
            task_id=TASK_ID,
            idempotency_key=f"decomposition_proposal:{SCOPE_HASH}:{PROPOSAL_DIGEST}",
            payload={
                "schema_version": "phase4.decomposition_proposal_record.v1",
                "proposal_id": PROPOSAL_ID,
                "task_id": TASK_ID,
                "parent_unit_id": UNIT_ID,
                "canonical_selection_id": CANONICAL_SELECTION_ID,
                "proposal_ref": proposal_ref.to_dict(),
                "proposal_digest": PROPOSAL_DIGEST,
                "expansion_scope_hash": SCOPE_HASH,
                "plugin_id": "structured_report_stub",
                "plugin_version": "0.1.0",
                "split_strategy_id": "structured_report_sections_v1",
                "child_count": 1,
                "dependency_edge_count": 0,
                "expected_output_count": 1,
                "merge_slot_count": 1,
                "created_at": NOW,
            },
            occurred_at=NOW,
        ),
        EventDraft(
            event_type=EventType.EXPANSION_DECISION_RECORDED,
            object_type="ExpansionDecision",
            object_id=DECISION_ID,
            task_id=TASK_ID,
            idempotency_key=f"expansion_decision:{SCOPE_HASH}",
            payload=_decision_payload(),
            occurred_at=NOW,
        ),
        EventDraft(
            event_type=EventType.MERGE_PLAN_RECORDED,
            object_type="MergePlan",
            object_id=MERGE_PLAN_ID,
            task_id=TASK_ID,
            idempotency_key=(
                f"merge_plan:{SCOPE_HASH}:{PROPOSAL_DIGEST}:{MERGE_PLAN_DIGEST}"
            ),
            payload={
                "schema_version": "phase4.merge_plan_record.v1",
                "merge_plan_id": MERGE_PLAN_ID,
                "task_id": TASK_ID,
                "parent_unit_id": UNIT_ID,
                "canonical_selection_id": CANONICAL_SELECTION_ID,
                "decomposition_proposal_id": PROPOSAL_ID,
                "expansion_decision_id": DECISION_ID,
                "merge_plan_ref": merge_plan_ref.to_dict(),
                "merge_plan_digest": MERGE_PLAN_DIGEST,
                "merge_policy_id": "structured_report_stub_merge_v1",
                "merge_policy_version": "v1",
                "required_slot_count": 1,
                "parent_output_mapping_count": 1,
                "created_at": NOW,
            },
            occurred_at=NOW,
        ),
    ]


def _child_unit_draft() -> EventDraft:
    return EventDraft(
        event_type=EventType.TASK_UNIT_CREATED,
        object_type="TaskUnit",
        object_id="unit_child_intro",
        task_id=TASK_ID,
        idempotency_key="task_unit:create:unit_child_intro",
        payload={
            "schema_version": "phase4.expansion_task_unit_created.v1",
            "task_unit": {
                "schema_version": "TaskUnit.v1",
                "unit_id": "unit_child_intro",
                "task_id": TASK_ID,
                "parent_unit_id": UNIT_ID,
                "depth": 1,
                "unit_type": "section",
                "state": "Ready",
                "input_refs": {},
                "canonical_output_refs": {},
                "required_capabilities": {"executor": "mock_ai"},
                "weight": 1.0,
                "budget_limit": None,
                "deadline": None,
                "plugin_payload": {},
                "metadata": {"child_logical_key": "intro"},
                "created_at": NOW,
                "updated_at": NOW,
            },
            "expansion_decision_id": DECISION_ID,
            "proposal_id": PROPOSAL_ID,
            "proposal_digest": PROPOSAL_DIGEST,
            "parent_unit_id": UNIT_ID,
            "child_logical_key": "intro",
            "initial_state_derivation": "phase4.child_initial_state.v1",
        },
        occurred_at=NOW,
    )


def _task_expanded_draft() -> EventDraft:
    return EventDraft(
        event_type=EventType.TASK_EXPANDED,
        object_type="TaskExpansion",
        object_id=DECISION_ID,
        task_id=TASK_ID,
        idempotency_key=f"task_expanded:{DECISION_ID}",
        payload={
            "schema_version": "phase4.task_expanded.v1",
            "task_id": TASK_ID,
            "parent_unit_id": UNIT_ID,
            "expansion_decision_id": DECISION_ID,
            "canonical_selection_id": CANONICAL_SELECTION_ID,
            "proposal_id": PROPOSAL_ID,
            "proposal_digest": PROPOSAL_DIGEST,
            "merge_plan_id": MERGE_PLAN_ID,
            "merge_plan_digest": MERGE_PLAN_DIGEST,
            "child_unit_ids": ["unit_child_intro"],
            "relation_ids": [],
            "expected_output_ids": [
                f"expected_output:{PROPOSAL_ID}:{UNIT_ID}:answer:0"
            ],
            "expanded_at": NOW,
        },
        occurred_at=NOW,
    )


def _decision_payload(
    *,
    action: str = "expand",
    decision_id: str = DECISION_ID,
    scope_hash: str = SCOPE_HASH,
    proposal_id: str | None = PROPOSAL_ID,
    proposal_digest: str | None = PROPOSAL_DIGEST,
    merge_plan_id: str | None = MERGE_PLAN_ID,
    merge_plan_digest: str | None = MERGE_PLAN_DIGEST,
) -> dict:
    return {
        "schema_version": "phase4.expansion_decision_record.v1",
        "expansion_decision": {
            "schema_version": "phase4.expansion_decision.v1",
            "expansion_decision_id": decision_id,
            "task_id": TASK_ID,
            "unit_id": UNIT_ID,
            "canonical_selection_id": CANONICAL_SELECTION_ID,
            "canonical_output_bundle_digest": CANONICAL_DIGEST,
            "expansion_scope_hash": scope_hash,
            "action": action,
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_digest": "sha256:plugin_descriptor",
            "split_strategy_id": "structured_report_sections_v1",
            "split_strategy_params_digest": "sha256:params",
            "source_invocation_id": "split_invocation:scope_expand:attempt:1",
            "proposal_id": proposal_id,
            "proposal_digest": proposal_digest,
            "merge_plan_id": merge_plan_id,
            "merge_plan_digest": merge_plan_digest,
            "action_body": (
                {
                    "expand_evidence": {
                        "proposal_id": proposal_id,
                        "proposal_digest": proposal_digest,
                        "merge_plan_id": merge_plan_id,
                        "merge_plan_digest": merge_plan_digest,
                        "child_count": 1,
                        "relation_count": 0,
                        "expected_output_count": 1,
                        "required_merge_slot_count": 1,
                    }
                }
                if action == "expand"
                else {"completion_evidence": {"kind": "verified"}}
            ),
            "decided_at": NOW,
        },
        "task_id": TASK_ID,
        "unit_id": UNIT_ID,
        "canonical_selection_id": CANONICAL_SELECTION_ID,
        "canonical_output_bundle_digest": CANONICAL_DIGEST,
        "expansion_scope_hash": scope_hash,
        "action": action,
        "plugin_id": "structured_report_stub",
        "plugin_version": "0.1.0",
        "plugin_descriptor_digest": "sha256:plugin_descriptor",
        "split_strategy_id": "structured_report_sections_v1",
        "split_strategy_params_digest": "sha256:params",
        "source_invocation_id": "split_invocation:scope_expand:attempt:1",
        "proposal_id": proposal_id,
        "proposal_digest": proposal_digest,
        "merge_plan_id": merge_plan_id,
        "merge_plan_digest": merge_plan_digest,
        "decided_at": NOW,
    }


def _proposal_document() -> dict:
    return {
        "schema_version": "phase4.decomposition_proposal.v1",
        "proposal_header": {
            "proposal_id": PROPOSAL_ID,
            "proposal_schema_version": "phase4.decomposition_proposal.v1",
            "task_id": TASK_ID,
            "parent_unit_id": UNIT_ID,
            "canonical_selection_id": CANONICAL_SELECTION_ID,
            "canonical_output_bundle_digest": CANONICAL_DIGEST,
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "plugin_descriptor_digest": "sha256:plugin_descriptor",
            "split_strategy_id": "structured_report_sections_v1",
            "split_strategy_params_digest": "sha256:params",
            "expansion_scope_hash": SCOPE_HASH,
            "proposal_digest": PROPOSAL_DIGEST,
            "created_at": NOW,
        },
        "child_specs": [
            {
                "child_logical_key": "intro",
                "unit_type": "section",
                "input_bindings": {},
                "required_outputs": ["answer"],
                "output_contract_refs": {"answer": {"schema": "text"}},
                "validator_policy_id": "structured_report_stub_validator_v1",
                "budget_limit": None,
                "deadline": None,
                "weight": 1.0,
                "required_capabilities": {"executor": "mock_ai"},
                "plugin_payload": {},
                "promotion_guard_ref": None,
            }
        ],
        "dependency_edges": [],
        "expected_outputs": [
            {
                "output_name": "answer",
                "schema_ref": {"schema": "text"},
                "resolution_kind": "merge_plan_output",
                "child_key": None,
                "child_output_name": None,
                "merge_slot_id": "slot_intro",
                "required": True,
            }
        ],
        "merge_slots": [
            {
                "slot_id": "slot_intro",
                "child_key": "intro",
                "child_output_name": "answer",
                "schema_ref": {"schema": "text"},
                "required": True,
                "missing_policy": "block_merge",
            }
        ],
        "promotion_guard_evidence": {
            "typed_io_checked": True,
            "independently_schedulable_checked": True,
            "validator_policy_checked": True,
            "output_contract_checked": True,
            "no_freeform_thought_checked": True,
            "max_depth_checked": True,
            "max_children_checked": True,
            "evidence_ref": None,
        },
    }


def _merge_plan_document() -> dict:
    return {
        "schema_version": "phase4.merge_plan.v1",
        "merge_plan_header": {
            "merge_plan_id": MERGE_PLAN_ID,
            "merge_plan_schema_version": "phase4.merge_plan.v1",
            "task_id": TASK_ID,
            "parent_unit_id": UNIT_ID,
            "canonical_selection_id": CANONICAL_SELECTION_ID,
            "decomposition_proposal_id": PROPOSAL_ID,
            "expansion_decision_id": DECISION_ID,
            "created_by_plugin_id": "structured_report_stub",
            "created_by_plugin_version": "0.1.0",
            "merge_plan_digest": MERGE_PLAN_DIGEST,
            "created_at": NOW,
        },
        "merge_policy_ref": {
            "plugin_id": "structured_report_stub",
            "plugin_version": "0.1.0",
            "merge_policy_id": "structured_report_stub_merge_v1",
            "merge_policy_version": "v1",
            "merge_policy_descriptor_digest": "sha256:plugin_descriptor",
            "merge_policy_params_digest": "sha256:merge_params",
        },
        "required_slots": [
            {
                "slot_key": "slot_intro",
                "source_child_logical_key": "intro",
                "source_child_unit_id": "unit_child_intro",
                "source_output_name": "answer",
                "output_schema_ref": {"schema": "text"},
                "output_schema_digest": "sha256:schema",
                "required": True,
                "missing_policy": "block_merge",
            }
        ],
        "parent_output_mapping": [
            {
                "parent_output_name": "answer",
                "resolution_kind": "merge_plan_output",
                "merge_slot_keys": ["slot_intro"],
                "result_schema_ref": {"schema": "text"},
                "result_schema_digest": "sha256:schema",
            }
        ],
        "hash_recording_requirements": {
            "record_child_canonical_output_digest": True,
            "record_slot_source_artifact_digest": True,
            "record_merge_input_bundle_digest": True,
        },
        "merge_validation_requirements": {
            "all_required_slots_canonical": True,
            "slot_schema_check_required": True,
            "merged_output_schema_check_required": True,
            "plugin_merge_validator_policy_id": "structured_report_stub_merge_validator_v1",
        },
        "plugin_payload": {
            "plugin_defined_schema_ref": {"schema": "structured_report_merge_payload.v1"},
            "plugin_defined_body_digest": "sha256:merge_payload",
            "plugin_defined_body": {},
        },
    }
