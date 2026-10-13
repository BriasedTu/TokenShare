import pytest

from tokenshare.core.contribution import (
    ContributionRecord,
    ContributionState,
    SettlementEntry,
    SettlementRecord,
    SubtreePruneRecord,
    build_sandbox_equal_weight_settlement_entries,
    digest_contribution,
    digest_settlement_entries,
    transition_contribution,
)
from tokenshare.core.merge import (
    ExpectedOutputResolution,
    MergeRecord,
    MergeTaskLink,
    RequiredSlotBinding,
    digest_merge_task_link,
)
from tokenshare.storage.events import EventType


NOW = "2026-06-25T00:00:00Z"


def test_phase5_event_type_constants_are_declared() -> None:
    assert EventType.MERGE_TASK_LINK_RECORDED.value == "MERGE_TASK_LINK_RECORDED"
    assert EventType.MERGE_RECORDED.value == "MERGE_RECORDED"
    assert EventType.EXPECTED_OUTPUT_RESOLVED.value == "EXPECTED_OUTPUT_RESOLVED"
    assert EventType.CONTRIBUTION_STATE_CHANGED.value == "CONTRIBUTION_STATE_CHANGED"
    assert EventType.SETTLEMENT_RECORDED.value == "SETTLEMENT_RECORDED"
    assert EventType.SUBTREE_PRUNED.value == "SUBTREE_PRUNED"


def test_required_slot_binding_requires_child_canonical_output() -> None:
    binding = RequiredSlotBinding(
        slot_key="slot_a",
        slot_id="slot_id_a",
        source_child_logical_key="child_a",
        source_child_unit_id="unit_child_a",
        source_output_name="answer",
        source_output_schema_digest="sha256:schema_answer",
        canonical_selection_id="canonical_selection:child_a",
        canonical_event_seq=42,
        canonical_output_ref={
            "schema_version": "ArtifactRef.v1",
            "artifact_id": "artifact_answer",
            "artifact_type": "canonical_output",
            "uri": "artifacts/artifact_answer",
            "content_hash": "sha256:artifact_answer",
            "size_bytes": 17,
            "media_type": "application/json",
            "artifact_schema_id": "tokenshare.test_output",
            "artifact_schema_version": "1",
            "source": {"kind": "test"},
            "metadata": {},
            "created_at": NOW,
        },
        canonical_output_digest="sha256:artifact_answer",
        canonical_output_bundle_digest="sha256:bundle_child_a",
        selected_verification_report_id="verification_report_child_a",
        selected_attempt_id="attempt_child_a",
        binding_source="canonical_output",
        schema_version="phase5.required_slot_binding.v1",
    )

    assert binding.to_dict()["schema_version"] == "phase5.required_slot_binding.v1"
    assert binding.binding_source == "canonical_output"

    with pytest.raises(ValueError, match="canonical_output"):
        RequiredSlotBinding(
            slot_key="slot_a",
            slot_id="slot_id_a",
            source_child_logical_key="child_a",
            source_child_unit_id="unit_child_a",
            source_output_name="answer",
            source_output_schema_digest="sha256:schema_answer",
            canonical_selection_id="canonical_selection:child_a",
            canonical_event_seq=42,
            canonical_output_ref={
                "schema_version": "ArtifactRef.v1",
                "artifact_id": "artifact_answer",
                "artifact_type": "submission_output",
                "uri": "artifacts/artifact_answer",
                "content_hash": "sha256:artifact_answer",
                "size_bytes": 17,
                "media_type": "application/json",
                "artifact_schema_id": "tokenshare.test_output",
                "artifact_schema_version": "1",
                "source": {"kind": "test"},
                "metadata": {},
                "created_at": NOW,
            },
            canonical_output_digest="sha256:artifact_answer",
            canonical_output_bundle_digest="sha256:bundle_child_a",
            selected_verification_report_id="verification_report_child_a",
            selected_attempt_id="attempt_child_a",
            binding_source="submission_output",
        )

    with pytest.raises(ValueError, match="canonical_output_ref"):
        RequiredSlotBinding(
            slot_key="slot_a",
            slot_id="slot_id_a",
            source_child_logical_key="child_a",
            source_child_unit_id="unit_child_a",
            source_output_name="answer",
            source_output_schema_digest="sha256:schema_answer",
            canonical_selection_id="canonical_selection:child_a",
            canonical_event_seq=42,
            canonical_output_ref={
                "schema_version": "ArtifactRef.v1",
                "artifact_id": "artifact_answer",
                "artifact_type": "submission_output",
                "uri": "artifacts/artifact_answer",
                "content_hash": "sha256:artifact_answer",
                "size_bytes": 17,
                "media_type": "application/json",
                "artifact_schema_id": "tokenshare.test_output",
                "artifact_schema_version": "1",
                "source": {"kind": "test"},
                "metadata": {},
                "created_at": NOW,
            },
            canonical_output_digest="sha256:artifact_answer",
            canonical_output_bundle_digest="sha256:bundle_child_a",
            selected_verification_report_id="verification_report_child_a",
            selected_attempt_id="attempt_child_a",
            binding_source="canonical_output",
        )

    with pytest.raises(ValueError, match="canonical_output"):
        RequiredSlotBinding(
            slot_key="slot_a",
            slot_id="slot_id_a",
            source_child_logical_key="child_a",
            source_child_unit_id="unit_child_a",
            source_output_name="answer",
            source_output_schema_digest="sha256:schema_answer",
            canonical_selection_id="canonical_selection:child_a",
            canonical_event_seq=42,
            canonical_output_ref={
                "schema_version": "ArtifactRef.v1",
                "artifact_id": "artifact_answer",
                "artifact_type": "candidate_output",
                "uri": "artifacts/artifact_answer",
                "content_hash": "sha256:artifact_answer",
                "size_bytes": 17,
                "media_type": "application/json",
                "artifact_schema_id": "tokenshare.test_output",
                "artifact_schema_version": "1",
                "source": {"kind": "test"},
                "metadata": {},
                "created_at": NOW,
            },
            canonical_output_digest="sha256:artifact_answer",
            canonical_output_bundle_digest="sha256:bundle_child_a",
            selected_verification_report_id="verification_report_child_a",
            selected_attempt_id="attempt_child_a",
            binding_source="canonical_output",
        )


def test_merge_task_link_digest_is_stable_and_rejects_duplicate_slots() -> None:
    bindings = [
        _make_required_slot_binding(slot_key="slot_a", child_key="child_a"),
        _make_required_slot_binding(slot_key="slot_b", child_key="child_b"),
    ]
    link = MergeTaskLink(
        merge_task_link_id="merge_task_link:merge_plan_1",
        task_id="task_demo",
        parent_unit_id="unit_parent",
        merge_plan_id="merge_plan_1",
        expansion_decision_id="expansion_decision_1",
        merge_unit_id="unit_merge_1",
        merge_input_bundle_ref=_artifact_ref("artifact_merge_input_bundle"),
        merge_input_bundle_digest="sha256:merge_input_bundle",
        required_slot_bindings=bindings,
        required_slot_bindings_digest=digest_merge_task_link(bindings),
        merge_policy_id="merge_sections_v1",
        merge_policy_version="v1",
        merge_policy_descriptor_digest="sha256:plugin_descriptor",
        source_merge_plan_event_seq=77,
        source_task_expanded_event_seq=88,
        optional_task_relation_id="relation_merge_of_1",
        readiness_reason="all_required_slots_canonical",
        created_at=NOW,
        coordinator={"coordinator_id": "coordinator_local", "coordinator_version": "1"},
        schema_version="phase5.merge_task_link.v1",
    )

    assert link.to_dict()["schema_version"] == "phase5.merge_task_link.v1"
    assert link.required_slot_bindings_digest == digest_merge_task_link(list(reversed(bindings)))

    with pytest.raises(ValueError, match="duplicate required slot"):
        MergeTaskLink(
            merge_task_link_id="merge_task_link:merge_plan_1",
            task_id="task_demo",
            parent_unit_id="unit_parent",
            merge_plan_id="merge_plan_1",
            expansion_decision_id="expansion_decision_1",
            merge_unit_id="unit_merge_1",
            merge_input_bundle_ref=_artifact_ref("artifact_merge_input_bundle"),
            merge_input_bundle_digest="sha256:merge_input_bundle",
            required_slot_bindings=[
                _make_required_slot_binding(slot_key="slot_a", child_key="child_a"),
                _make_required_slot_binding(slot_key="slot_a", child_key="child_b"),
            ],
            required_slot_bindings_digest="sha256:duplicate",
            merge_policy_id="merge_sections_v1",
            merge_policy_version="v1",
            merge_policy_descriptor_digest="sha256:plugin_descriptor",
            source_merge_plan_event_seq=77,
            source_task_expanded_event_seq=88,
            optional_task_relation_id=None,
            readiness_reason="all_required_slots_canonical",
            created_at=NOW,
            coordinator={"coordinator_id": "coordinator_local", "coordinator_version": "1"},
        )


def test_merge_record_rejects_missing_canonical_commitment_fields() -> None:
    record = MergeRecord(
        merge_record_id="merge_record:merge_plan_1:unit_merge_1:canonical_selection_merge",
        task_id="task_demo",
        parent_unit_id="unit_parent",
        merge_plan_id="merge_plan_1",
        merge_unit_id="unit_merge_1",
        merge_task_link_id="merge_task_link:merge_plan_1",
        merge_input_bundle_ref=_artifact_ref("artifact_merge_input_bundle"),
        merge_input_bundle_digest="sha256:merge_input_bundle",
        required_slot_bindings_digest="sha256:bindings",
        merge_policy_id="merge_sections_v1",
        merge_policy_version="v1",
        merge_policy_descriptor_digest="sha256:plugin_descriptor",
        merge_policy_params_digest="sha256:merge_params",
        canonical_selection_id="canonical_selection_merge",
        canonical_event_seq=101,
        selected_verification_report_id="verification_report_merge",
        selected_verification_event_seq=100,
        selected_submission_id="submission_merge",
        selected_submission_event_seq=99,
        selected_attempt_id="attempt_merge",
        merge_output_bundle_digest="sha256:merge_output_bundle",
        merge_output_refs={"answer": _artifact_ref("artifact_merge_answer")},
        parent_output_mapping_digest="sha256:parent_output_mapping",
        created_at=NOW,
        schema_version="phase5.merge_record.v1",
    )

    assert record.to_dict()["schema_version"] == "phase5.merge_record.v1"

    with pytest.raises(ValueError, match="canonical_selection_id"):
        MergeRecord(
            merge_record_id="merge_record:merge_plan_1:unit_merge_1:canonical_selection_merge",
            task_id="task_demo",
            parent_unit_id="unit_parent",
            merge_plan_id="merge_plan_1",
            merge_unit_id="unit_merge_1",
            merge_task_link_id="merge_task_link:merge_plan_1",
            merge_input_bundle_ref=_artifact_ref("artifact_merge_input_bundle"),
            merge_input_bundle_digest="sha256:merge_input_bundle",
            required_slot_bindings_digest="sha256:bindings",
            merge_policy_id="merge_sections_v1",
            merge_policy_version="v1",
            merge_policy_descriptor_digest="sha256:plugin_descriptor",
            merge_policy_params_digest="sha256:merge_params",
            canonical_selection_id="",
            canonical_event_seq=101,
            selected_verification_report_id="verification_report_merge",
            selected_verification_event_seq=100,
            selected_submission_id="submission_merge",
            selected_submission_event_seq=99,
            selected_attempt_id="attempt_merge",
            merge_output_bundle_digest="sha256:merge_output_bundle",
            merge_output_refs={"answer": _artifact_ref("artifact_merge_answer")},
            parent_output_mapping_digest="sha256:parent_output_mapping",
            created_at=NOW,
        )


def test_expected_output_resolution_is_merge_record_sourced_in_v1() -> None:
    resolution = ExpectedOutputResolution(
        expected_output_resolution_id="expected_output_resolved:expected_1:merge_record_1",
        task_id="task_demo",
        owner_unit_id="unit_parent",
        expected_output_id="expected_1",
        expected_output_name="answer",
        resolution_source_type="merge_record",
        merge_record_id="merge_record_1",
        merge_plan_id="merge_plan_1",
        merge_unit_id="unit_merge_1",
        merge_canonical_selection_id="canonical_selection_merge",
        resolved_output_ref=_artifact_ref("artifact_merge_answer"),
        resolved_output_digest="sha256:artifact_merge_answer",
        resolved_at=NOW,
        schema_version="phase5.expected_output_resolution.v1",
    )

    assert resolution.to_dict()["schema_version"] == "phase5.expected_output_resolution.v1"
    assert resolution.resolution_source_type == "merge_record"

    with pytest.raises(ValueError, match="merge_record"):
        ExpectedOutputResolution(
            expected_output_resolution_id="expected_output_resolved:expected_1:merge_record_1",
            task_id="task_demo",
            owner_unit_id="unit_parent",
            expected_output_id="expected_1",
            expected_output_name="answer",
            resolution_source_type="submission",
            merge_record_id="merge_record_1",
            merge_plan_id="merge_plan_1",
            merge_unit_id="unit_merge_1",
            merge_canonical_selection_id="canonical_selection_merge",
            resolved_output_ref=_artifact_ref("artifact_merge_answer"),
            resolved_output_digest="sha256:artifact_merge_answer",
            resolved_at=NOW,
        )


def test_contribution_state_machine_allows_only_phase5_transitions() -> None:
    contribution = ContributionRecord(
        contribution_id="contribution:complete_canonical:task_demo:unit_parent:canonical_selection_parent",
        task_id="task_demo",
        unit_id="unit_parent",
        kind="complete_canonical",
        state=ContributionState.ELIGIBLE,
        source_attempt_id="attempt_parent",
        source_client_id="client_parent",
        canonical_selection_id="canonical_selection_parent",
        canonical_event_seq=41,
        verification_report_id="verification_report_parent",
        verification_event_seq=40,
        source_decision_id="decision_parent",
        merge_record_id=None,
        source_batch_id="completion_batch:decision_parent",
        source_terminal_event_seq=42,
        reward_weight=1,
        created_at=NOW,
        updated_at=NOW,
        schema_version="phase5.contribution_record.v1",
    )

    assert contribution.to_dict()["schema_version"] == "phase5.contribution_record.v1"
    assert transition_contribution(contribution, new_state="Settled", changed_at=NOW, reason="settlement_batch") == contribution.__class__(**{**contribution.to_dict(), "state": "Settled"})

    with pytest.raises(ValueError, match="Eligible -> Settled"):
        transition_contribution(
            contribution,
            new_state="Settled",
            changed_at=NOW,
            reason="normal_flow",
            source_batch_kind="completion_batch",
        )

    with pytest.raises(ValueError, match="Settled"):
        transition_contribution(
            transition_contribution(
                contribution,
                new_state=ContributionState.SETTLED,
                changed_at=NOW,
                reason="settlement_batch",
                source_batch_kind="settlement_batch",
            ),
            new_state="Eligible",
            changed_at=NOW,
            reason="invalid",
            source_batch_kind="settlement_batch",
        )


def test_sandbox_equal_weight_formula_distributes_remainder_deterministically() -> None:
    contributions = [
        _make_contribution("contribution_b", reward_weight=1),
        _make_contribution("contribution_a", reward_weight=1),
        _make_contribution("contribution_c", reward_weight=1),
    ]

    entries = build_sandbox_equal_weight_settlement_entries(
        task_id="task_demo",
        root_unit_id="unit_root",
        root_completion_event_seq=99,
        eligible_contributions=contributions,
        root_budget=10,
        settlement_policy_id="sandbox_equal_weight_v1",
        settlement_policy_version="v1",
        scale="1",
        created_at=NOW,
    )

    assert [entry.contribution_id for entry in entries] == ["contribution_a", "contribution_b", "contribution_c"]
    assert [entry.reward_units for entry in entries] == [4, 3, 3]
    assert sum(entry.reward_units for entry in entries) == 10
    assert digest_settlement_entries(entries) == digest_settlement_entries(list(reversed(entries)))


def _artifact_ref(artifact_id: str):
    return {
        "schema_version": "ArtifactRef.v1",
        "artifact_id": artifact_id,
        "artifact_type": "canonical_output",
        "uri": f"artifacts/{artifact_id}",
        "content_hash": f"sha256:{artifact_id}",
        "size_bytes": 17,
        "media_type": "application/json",
        "artifact_schema_id": "tokenshare.test_output",
        "artifact_schema_version": "1",
        "source": {"kind": "test"},
        "metadata": {},
        "created_at": NOW,
    }


def _make_required_slot_binding(*, slot_key: str, child_key: str) -> RequiredSlotBinding:
    return RequiredSlotBinding(
        slot_key=slot_key,
        slot_id=f"slot_id_{slot_key}",
        source_child_logical_key=child_key,
        source_child_unit_id=f"unit_{child_key}",
        source_output_name="answer",
        source_output_schema_digest="sha256:schema_answer",
        canonical_selection_id=f"canonical_selection:{child_key}",
        canonical_event_seq=42,
        canonical_output_ref=_artifact_ref(f"artifact_{child_key}_answer"),
        canonical_output_digest=f"sha256:artifact_{child_key}_answer",
        canonical_output_bundle_digest=f"sha256:bundle_{child_key}",
        selected_verification_report_id=f"verification_report_{child_key}",
        selected_attempt_id=f"attempt_{child_key}",
        binding_source="canonical_output",
    )


def _make_contribution(contribution_id: str, *, reward_weight: int) -> ContributionRecord:
    return ContributionRecord(
        contribution_id=contribution_id,
        task_id="task_demo",
        unit_id="unit_demo",
        kind="merge_canonical",
        state=ContributionState.ELIGIBLE,
        source_attempt_id="attempt_demo",
        source_client_id="client_demo",
        canonical_selection_id="canonical_selection_demo",
        canonical_event_seq=42,
        verification_report_id="verification_report_demo",
        verification_event_seq=41,
        source_decision_id=None,
        merge_record_id="merge_record_demo",
        source_batch_id="merge_resolution_batch:merge_record_demo",
        source_terminal_event_seq=43,
        reward_weight=reward_weight,
        created_at=NOW,
        updated_at=NOW,
    )
