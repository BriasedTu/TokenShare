import pytest

from tests.phase2_fixtures import make_artifact_ref
from tokenshare.core import expansion as expansion_models
from tokenshare.core.models import ProtocolConfig, TaskState
from tokenshare.core.task_graph import TaskGraph
from tokenshare.core.verification import (
    REQUIRED_VERIFICATION_LAYERS,
    VerificationReport,
    build_verification_report,
    select_first_verified_bundle,
)
from tokenshare.core.expansion import (
    DecompositionProposal,
    ExpectedOutputRef,
    ExpansionDecision,
    MergePlan,
    SplitStrategyInvocation,
    SplitStrategyResult,
    derive_child_initial_state,
)


NOW = "2026-06-24T00:00:00Z"


def test_verification_report_passed_layers_are_eligible_and_digest_is_canonical() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    first = build_verification_report(
        verification_report_id="verification_report_1",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=["artifact_answer"],
        available_evidence_ref_ids=["artifact_answer"],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={"runtime": "pytest"},
        verifier={"verifier_id": "verifier_local", "verifier_version": "1", "verifier_kind": "stub"},
        started_at=NOW,
        completed_at=NOW,
    )
    second = build_verification_report(
        verification_report_id="verification_report_2",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=["artifact_answer"],
        available_evidence_ref_ids=["artifact_answer"],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={"runtime": "pytest"},
        verifier={"verifier_id": "verifier_local", "verifier_version": "1", "verifier_kind": "stub"},
        started_at=NOW,
        completed_at=NOW,
    )

    assert first.eligible_for_canonical is True
    assert first.candidate_output_bundle_digest == second.candidate_output_bundle_digest
    assert first.to_dict()["schema_version"] == "phase4.verification_report.v1"


def test_verification_report_missing_required_output_is_not_eligible() -> None:
    report = build_verification_report(
        verification_report_id="verification_report_missing_output",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    assert report.eligible_for_canonical is False
    assert report.layer_results["required_output_coverage_check"]["status"] == "rejected"
    assert report.failure_summary["failed_layer"] == "required_output_coverage_check"


def test_verification_report_artifact_digest_mismatch_is_not_eligible() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    report = build_verification_report(
        verification_report_id="verification_report_bad_digest",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": "sha256:different"},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    assert report.eligible_for_canonical is False
    assert report.layer_results["artifact_integrity_check"]["status"] == "rejected"


def test_verification_report_missing_evidence_ref_is_not_eligible() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    report = build_verification_report(
        verification_report_id="verification_report_missing_evidence",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=["evidence_required"],
        available_evidence_ref_ids=["artifact_answer"],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    assert report.eligible_for_canonical is False
    assert report.layer_results["evidence_reference_check"]["status"] == "rejected"


def test_verification_report_plugin_domain_rejected_is_not_eligible() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    report = build_verification_report(
        verification_report_id="verification_report_domain_rejected",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="rejected",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=["artifact_answer"],
        available_evidence_ref_ids=["artifact_answer"],
        plugin_domain_status="rejected",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    assert report.eligible_for_canonical is False
    assert report.layer_results["plugin_domain_check"]["status"] == "rejected"


def test_verification_report_error_status_is_never_eligible() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    report = build_verification_report(
        verification_report_id="verification_report_error",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_1",
        submission_id="submission_1",
        submission_event_seq=4,
        candidate_output_refs={"answer": answer_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="error",
        expected_artifact_hashes={"answer": answer_ref.content_hash},
        required_evidence_ref_ids=["artifact_answer"],
        available_evidence_ref_ids=["artifact_answer"],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    assert report.eligible_for_canonical is False


def test_verification_report_rejects_eligible_true_when_layer_not_passed() -> None:
    answer_ref = make_artifact_ref("artifact_answer")

    with pytest.raises(ValueError, match="eligible_for_canonical"):
        VerificationReport(
            verification_report_id="verification_report_invalid",
            task_id="task_demo",
            unit_id="unit_parent",
            attempt_id="attempt_1",
            submission_id="submission_1",
            submission_event_seq=4,
            candidate_output_bundle_digest="sha256:bundle",
            candidate_output_refs={"answer": answer_ref},
            required_output_names=["answer"],
            output_contract_id="contract_structured_report",
            validator_policy_id="validator_structured_report",
            plugin_id="structured_report_stub",
            plugin_version="0.1.0",
            plugin_descriptor_digest="sha256:plugin_descriptor",
            status="passed",
            eligible_for_canonical=True,
            layer_results={
                "schema_check": {"status": "passed"},
                "artifact_integrity_check": {"status": "passed"},
                "required_output_coverage_check": {"status": "passed"},
                "evidence_reference_check": {"status": "skipped"},
                "plugin_domain_check": {"status": "passed"},
                "audit_check": {"status": "passed"},
            },
            failure_summary=None,
            verification_environment={},
            verifier={},
            started_at=NOW,
            completed_at=NOW,
        )


def test_verification_report_rejects_invalid_report_status() -> None:
    kwargs = _verification_report_kwargs("verification_report_bad_status")
    kwargs["status"] = "nonsense"

    with pytest.raises(ValueError, match="invalid verification report status"):
        VerificationReport(**kwargs)


def test_verification_report_rejects_invalid_layer_status() -> None:
    kwargs = _verification_report_kwargs("verification_report_bad_layer")
    kwargs["layer_results"]["schema_check"]["status"] = "nonsense"

    with pytest.raises(ValueError, match="invalid verification layer status"):
        VerificationReport(**kwargs)


def test_first_verified_bundle_selects_lowest_verification_event_seq() -> None:
    first_ref = make_artifact_ref("artifact_first")
    second_ref = make_artifact_ref("artifact_second")
    later_report = build_verification_report(
        verification_report_id="verification_report_later",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_later",
        submission_id="submission_later",
        submission_event_seq=8,
        candidate_output_refs={"answer": second_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="passed",
        expected_artifact_hashes={"answer": second_ref.content_hash},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )
    earlier_report = build_verification_report(
        verification_report_id="verification_report_earlier",
        task_id="task_demo",
        unit_id="unit_parent",
        attempt_id="attempt_earlier",
        submission_id="submission_earlier",
        submission_event_seq=7,
        candidate_output_refs={"answer": first_ref},
        required_output_names=["answer"],
        output_contract_id="contract_structured_report",
        validator_policy_id="validator_structured_report",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        status="accepted",
        expected_artifact_hashes={"answer": first_ref.content_hash},
        required_evidence_ref_ids=[],
        available_evidence_ref_ids=[],
        plugin_domain_status="passed",
        audit_status="passed",
        verification_environment={},
        verifier={},
        started_at=NOW,
        completed_at=NOW,
    )

    selection = select_first_verified_bundle(
        task_id="task_demo",
        unit_id="unit_parent",
        verification_event_reports=[(12, later_report), (10, earlier_report)],
        bound_at=NOW,
    )

    assert selection.selected_verification_report_id == "verification_report_earlier"
    assert selection.selected_verification_event_seq == 10
    assert selection.canonical_output_refs == {"answer": first_ref}


def test_phase4_expansion_objects_have_stable_schema_versions() -> None:
    invocation = SplitStrategyInvocation(
        invocation_id="split_invocation:scope_1:attempt:1",
        invocation_attempt_no=1,
        expansion_scope_hash="sha256:scope_1",
        task_id="task_demo",
        unit_id="unit_parent",
        canonical_selection_id="canonical_selection:task_demo:unit_parent",
        canonical_output_bundle_digest="sha256:bundle",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        split_strategy_id="section_split_v1",
        split_strategy_params_digest="sha256:params",
        status="succeeded",
        result_action="complete",
        result_digest="sha256:result",
        started_at=NOW,
        completed_at=NOW,
    )
    decision = ExpansionDecision(
        expansion_decision_id="expansion_decision:scope_1",
        task_id="task_demo",
        unit_id="unit_parent",
        canonical_selection_id="canonical_selection:task_demo:unit_parent",
        canonical_output_bundle_digest="sha256:bundle",
        expansion_scope_hash="sha256:scope_1",
        action="complete",
        plugin_id="structured_report_stub",
        plugin_version="0.1.0",
        plugin_descriptor_digest="sha256:plugin_descriptor",
        split_strategy_id="section_split_v1",
        split_strategy_params_digest="sha256:params",
        source_invocation_id=invocation.invocation_id,
        action_body={"completion_evidence": {"completion_kind": "already_complete"}},
        decided_at=NOW,
    )

    assert invocation.to_dict()["schema_version"] == "phase4.split_strategy_invocation.v1"
    assert decision.to_dict()["schema_version"] == "phase4.expansion_decision.v1"


def test_split_strategy_result_requires_exactly_one_action_body() -> None:
    with pytest.raises(ValueError, match="complete"):
        SplitStrategyResult(
            action="complete",
            expansion_scope_hash="sha256:scope_1",
            split_strategy_identity={"split_strategy_id": "section_split_v1"},
            complete=None,
            expand=None,
            generation_evidence={},
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="exclusive"):
        SplitStrategyResult(
            action="expand",
            expansion_scope_hash="sha256:scope_1",
            split_strategy_identity={"split_strategy_id": "section_split_v1"},
            complete={"completion_kind": "done"},
            expand={"proposal_digest": "sha256:proposal", "merge_plan_digest": "sha256:merge"},
            generation_evidence={},
            created_at=NOW,
        )


def test_expansion_decision_requires_fixed_expand_evidence_shape() -> None:
    with pytest.raises(ValueError, match="expand_evidence"):
        ExpansionDecision(
            expansion_decision_id="expansion_decision:scope_1",
            task_id="task_demo",
            unit_id="unit_parent",
            canonical_selection_id="canonical_selection:task_demo:unit_parent",
            canonical_output_bundle_digest="sha256:bundle",
            expansion_scope_hash="sha256:scope_1",
            action="expand",
            plugin_id="structured_report_stub",
            plugin_version="0.1.0",
            plugin_descriptor_digest="sha256:plugin_descriptor",
            split_strategy_id="section_split_v1",
            split_strategy_params_digest="sha256:params",
            source_invocation_id="split_invocation:scope_1:attempt:1",
            action_body={"expansion_kind": "decomposition", "child_count": 2},
            proposal_id="proposal_1",
            proposal_digest="sha256:proposal",
            merge_plan_id="merge_plan_1",
            merge_plan_digest="sha256:merge",
            decided_at=NOW,
        )


def test_expansion_decision_rejects_expand_action_body_extra_fields() -> None:
    with pytest.raises(ValueError, match="action_body"):
        ExpansionDecision(
            expansion_decision_id="expansion_decision:scope_1",
            task_id="task_demo",
            unit_id="unit_parent",
            canonical_selection_id="canonical_selection:task_demo:unit_parent",
            canonical_output_bundle_digest="sha256:bundle",
            expansion_scope_hash="sha256:scope_1",
            action="expand",
            plugin_id="structured_report_stub",
            plugin_version="0.1.0",
            plugin_descriptor_digest="sha256:plugin_descriptor",
            split_strategy_id="section_split_v1",
            split_strategy_params_digest="sha256:params",
            source_invocation_id="split_invocation:scope_1:attempt:1",
            action_body={
                "expand_evidence": {
                    "proposal_id": "proposal_1",
                    "proposal_digest": "sha256:proposal",
                    "merge_plan_id": "merge_plan_1",
                    "merge_plan_digest": "sha256:merge",
                    "child_count": 2,
                    "relation_count": 1,
                    "expected_output_count": 1,
                    "required_merge_slot_count": 2,
                },
                "settlement_preview": {"eligible": True},
            },
            proposal_id="proposal_1",
            proposal_digest="sha256:proposal",
            merge_plan_id="merge_plan_1",
            merge_plan_digest="sha256:merge",
            decided_at=NOW,
        )


def test_decomposition_proposal_rejects_freeform_child_and_duplicate_target_input() -> None:
    with pytest.raises(ValueError, match="freeform"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_freeform"),
            child_specs=[
                _child_spec("child_a", plugin_payload={"thought": "try outline first"})
            ],
            dependency_edges=[],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(no_freeform_thought_checked=False),
        )

    with pytest.raises(ValueError, match="duplicate target input"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_duplicate_input"),
            child_specs=[_child_spec("child_a"), _child_spec("child_b"), _child_spec("child_c")],
            dependency_edges=[
                _dependency_edge("edge_a_c", "child_a", "child_c", target_input_name="source"),
                _dependency_edge("edge_b_c", "child_b", "child_c", target_input_name="source"),
            ],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )

    with pytest.raises(ValueError, match="duplicate target input"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_duplicate_binding_and_edge"),
            child_specs=[
                _child_spec("child_a"),
                _child_spec(
                    "child_b",
                    input_bindings={
                        "source": {"kind": "parent_output", "output_name": "answer"}
                    },
                ),
            ],
            dependency_edges=[
                _dependency_edge("edge_a_b", "child_a", "child_b", target_input_name="source")
            ],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )


def test_decomposition_proposal_rejects_missing_required_header_fields() -> None:
    with pytest.raises(ValueError, match="proposal_header missing required field"):
        DecompositionProposal(
            proposal_header={"proposal_schema_version": "phase4.decomposition_proposal.v1"},
            child_specs=[_child_spec("child_a")],
            dependency_edges=[],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )


def test_decomposition_proposal_rejects_missing_child_required_fields() -> None:
    child = _child_spec("child_a")
    del child["validator_policy_id"]

    with pytest.raises(ValueError, match="child_spec missing required field"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_missing_child_field"),
            child_specs=[child],
            dependency_edges=[],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )


def test_decomposition_proposal_rejects_missing_dependency_edge_required_fields() -> None:
    edge = _dependency_edge("edge_a_b", "child_a", "child_b")
    del edge["target_input_name"]

    with pytest.raises(ValueError, match="dependency_edge missing required field"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_missing_edge_field"),
            child_specs=[_child_spec("child_a"), _child_spec("child_b")],
            dependency_edges=[edge],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )


def test_decomposition_proposal_rejects_invalid_expected_output_references() -> None:
    with pytest.raises(ValueError, match="expected output child_key must exist"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_bad_expected_child"),
            child_specs=[_child_spec("child_a")],
            dependency_edges=[],
            expected_outputs=[
                {
                    "output_name": "answer",
                    "schema_ref": {"schema": "text"},
                    "resolution_kind": "child_output",
                    "child_key": "missing_child",
                    "child_output_name": "answer",
                    "merge_slot_id": None,
                    "required": True,
                }
            ],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )

    bad_merge_output = _expected_output("answer")
    del bad_merge_output["merge_slot_id"]
    with pytest.raises(ValueError, match="expected_output missing required field"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_bad_expected_merge"),
            child_specs=[_child_spec("child_a")],
            dependency_edges=[],
            expected_outputs=[bad_merge_output],
            merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )


def test_validate_decomposition_proposal_limits_enforces_protocol_config_and_parent_outputs() -> None:
    validator = getattr(expansion_models, "validate_decomposition_proposal_limits")
    proposal = DecompositionProposal(
        proposal_header=_proposal_header("proposal_limits"),
        child_specs=[_child_spec("child_a"), _child_spec("child_b")],
        dependency_edges=[],
        expected_outputs=[_expected_output("answer")],
        merge_slots=[_merge_slot("slot_a", "child_a", "answer")],
        promotion_guard_evidence=_promotion_guard(),
    )
    protocol_config = ProtocolConfig.default(
        config_id="config_limits",
        artifact_store_uri="file://artifacts",
        event_log_uri="file://events.jsonl",
    )

    with pytest.raises(ValueError, match="child count"):
        validator(
            proposal,
            protocol_config=protocol_config,
            parent_depth=0,
            existing_unit_count=1,
            parent_required_output_names=["answer"],
            max_children_per_strategy=1,
        )

    with pytest.raises(ValueError, match="max_depth"):
        validator(
            proposal,
            protocol_config=protocol_config,
            parent_depth=protocol_config.max_depth,
            existing_unit_count=1,
            parent_required_output_names=["answer"],
        )

    with pytest.raises(ValueError, match="max_total_units"):
        validator(
            proposal,
            protocol_config=protocol_config,
            parent_depth=0,
            existing_unit_count=protocol_config.max_total_units,
            parent_required_output_names=["answer"],
        )

    with pytest.raises(ValueError, match="parent required output"):
        validator(
            proposal,
            protocol_config=protocol_config,
            parent_depth=0,
            existing_unit_count=1,
            parent_required_output_names=["answer", "summary"],
        )


def test_child_initial_state_is_derived_from_dependency_and_input_bindings_not_plugin_payload() -> None:
    with pytest.raises(ValueError, match="plugin_payload.*state"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_payload_state"),
            child_specs=[_child_spec("child_invalid", plugin_payload={"state": "Ready"})],
            dependency_edges=[],
            expected_outputs=[_expected_output("answer")],
            merge_slots=[_merge_slot("slot_invalid", "child_invalid", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )

    proposal = DecompositionProposal(
        proposal_header=_proposal_header("proposal_child_state"),
        child_specs=[
            _child_spec(
                "child_ready",
                input_bindings={"parent_answer": {"kind": "parent_output", "output_name": "answer"}},
            ),
            _child_spec(
                "child_blocked",
            ),
        ],
        dependency_edges=[
            _dependency_edge("edge_ready_blocked", "child_ready", "child_blocked", target_input_name="ready_answer")
        ],
        expected_outputs=[_expected_output("answer", merge_slot_id="slot_ready")],
        merge_slots=[_merge_slot("slot_ready", "child_ready", "answer")],
        promotion_guard_evidence=_promotion_guard(),
    )
    graph = TaskGraph(task_id="task_demo", units={}, relations=[])

    ready_state = derive_child_initial_state(
        proposal=proposal,
        child_logical_key="child_ready",
        graph=graph,
        parent_canonical_output_refs={"answer": make_artifact_ref("artifact_parent_answer")},
    )
    blocked_state = derive_child_initial_state(
        proposal=proposal,
        child_logical_key="child_blocked",
        graph=graph,
        parent_canonical_output_refs={"answer": make_artifact_ref("artifact_parent_answer")},
    )

    assert ready_state == TaskState.READY
    assert blocked_state == TaskState.BLOCKED

    with pytest.raises(ValueError, match="parent output"):
        derive_child_initial_state(
            proposal=proposal,
            child_logical_key="child_ready",
            graph=graph,
            parent_canonical_output_refs={},
        )


def test_merge_plan_requires_only_required_slots() -> None:
    with pytest.raises(ValueError, match="optional_slots"):
        MergePlan(
            merge_plan_header=_merge_plan_header(),
            merge_policy_ref=_merge_policy_ref(),
            required_slots=[_required_slot("slot_a")],
            parent_output_mapping=[_parent_output_mapping()],
            hash_recording_requirements=_hash_recording_requirements(),
            merge_validation_requirements=_merge_validation_requirements(),
            plugin_payload={**_merge_plugin_payload(), "optional_slots": ["slot_b"]},
        )


def test_merge_plan_rejects_missing_required_schema_fields() -> None:
    header = _merge_plan_header()
    del header["merge_plan_schema_version"]
    with pytest.raises(ValueError, match="merge_plan_header missing required field"):
        MergePlan(
            merge_plan_header=header,
            merge_policy_ref=_merge_policy_ref(),
            required_slots=[_required_slot("slot_a")],
            parent_output_mapping=[_parent_output_mapping()],
            hash_recording_requirements=_hash_recording_requirements(),
            merge_validation_requirements=_merge_validation_requirements(),
            plugin_payload=_merge_plugin_payload(),
        )

    slot = _required_slot("slot_a")
    del slot["output_schema_digest"]
    with pytest.raises(ValueError, match="required_slot missing required field"):
        MergePlan(
            merge_plan_header=_merge_plan_header(),
            merge_policy_ref=_merge_policy_ref(),
            required_slots=[slot],
            parent_output_mapping=[_parent_output_mapping()],
            hash_recording_requirements=_hash_recording_requirements(),
            merge_validation_requirements=_merge_validation_requirements(),
            plugin_payload=_merge_plugin_payload(),
        )


def test_plugin_payload_rejects_authoritative_state_fields() -> None:
    with pytest.raises(ValueError, match="plugin_payload.*state"):
        DecompositionProposal(
            proposal_header=_proposal_header("proposal_payload_resolution"),
            child_specs=[
                _child_spec("child_invalid", plugin_payload={"resolution_status": "expected"})
            ],
            dependency_edges=[],
            expected_outputs=[_expected_output("answer", merge_slot_id="slot_invalid")],
            merge_slots=[_merge_slot("slot_invalid", "child_invalid", "answer")],
            promotion_guard_evidence=_promotion_guard(),
        )

    with pytest.raises(ValueError, match="plugin_payload.*state"):
        MergePlan(
            merge_plan_header=_merge_plan_header(),
            merge_policy_ref=_merge_policy_ref(),
            required_slots=[_required_slot("slot_a")],
            parent_output_mapping=[_parent_output_mapping()],
            hash_recording_requirements=_hash_recording_requirements(),
            merge_validation_requirements=_merge_validation_requirements(),
            plugin_payload={
                **_merge_plugin_payload(),
                "canonical_output_refs": {"answer": "artifact"},
            },
        )


def test_phase4_objects_reject_non_spec_schema_versions() -> None:
    kwargs = _verification_report_kwargs("verification_report_bad_schema")
    kwargs["schema_version"] = "phase4.verification_report.v2"
    with pytest.raises(ValueError, match="schema_version"):
        VerificationReport(**kwargs)

    with pytest.raises(ValueError, match="schema_version"):
        ExpansionDecision(
            expansion_decision_id="expansion_decision:scope_1",
            task_id="task_demo",
            unit_id="unit_parent",
            canonical_selection_id="canonical_selection:task_demo:unit_parent",
            canonical_output_bundle_digest="sha256:bundle",
            expansion_scope_hash="sha256:scope_1",
            action="complete",
            plugin_id="structured_report_stub",
            plugin_version="0.1.0",
            plugin_descriptor_digest="sha256:plugin_descriptor",
            split_strategy_id="section_split_v1",
            split_strategy_params_digest="sha256:params",
            source_invocation_id="split_invocation:scope_1:attempt:1",
            action_body={"completion_evidence": {"completion_kind": "already_complete"}},
            decided_at=NOW,
            schema_version="phase4.expansion_decision.v2",
        )


def test_expected_output_ref_is_derived_from_task_expanded_visibility() -> None:
    ref = ExpectedOutputRef.from_expected_output(
        expected_output={"output_name": "answer", "schema_ref": {"schema": "text"}, "resolution_kind": "merge_plan_output", "merge_slot_id": "slot_a", "required": True},
        task_id="task_demo",
        owner_unit_id="unit_parent",
        canonical_selection_id="canonical_selection:task_demo:unit_parent",
        canonical_output_bundle_digest="sha256:bundle",
        source_proposal_id="proposal_1",
        source_expansion_decision_id="expansion_decision:scope_1",
        created_event_seq=99,
        logical_position=0,
        child_unit_ids_by_key={"child_a": "unit_child_a"},
        merge_plan_id="merge_plan_1",
    )

    assert ref.resolution_status == "expected"
    assert ref.created_event_seq == 99
    assert ref.to_dict()["schema_version"] == "phase4.expected_output_ref.v1"


def test_expected_output_ref_rejects_unresolvable_child_output() -> None:
    with pytest.raises(ValueError, match="child_key must resolve to child_unit_id"):
        ExpectedOutputRef.from_expected_output(
            expected_output={
                "output_name": "answer",
                "schema_ref": {"schema": "text"},
                "resolution_kind": "child_output",
                "child_key": "missing_child",
                "child_output_name": "answer",
                "required": True,
            },
            task_id="task_demo",
            owner_unit_id="unit_parent",
            canonical_selection_id="canonical_selection:task_demo:unit_parent",
            canonical_output_bundle_digest="sha256:bundle",
            source_proposal_id="proposal_1",
            source_expansion_decision_id="expansion_decision:scope_1",
            created_event_seq=99,
            logical_position=1,
            child_unit_ids_by_key={"child_a": "unit_child_a"},
            merge_plan_id=None,
        )


def test_expected_output_ref_rejects_merge_output_without_merge_plan() -> None:
    with pytest.raises(ValueError, match="merge_plan_id is required"):
        ExpectedOutputRef.from_expected_output(
            expected_output={
                "output_name": "answer",
                "schema_ref": {"schema": "text"},
                "resolution_kind": "merge_plan_output",
                "merge_slot_id": "slot_a",
                "required": True,
            },
            task_id="task_demo",
            owner_unit_id="unit_parent",
            canonical_selection_id="canonical_selection:task_demo:unit_parent",
            canonical_output_bundle_digest="sha256:bundle",
            source_proposal_id="proposal_1",
            source_expansion_decision_id="expansion_decision:scope_1",
            created_event_seq=99,
            logical_position=2,
            child_unit_ids_by_key={"child_a": "unit_child_a"},
            merge_plan_id=None,
        )


def test_expected_output_ref_id_uses_logical_position_not_created_event_seq() -> None:
    common = {
        "expected_output": {
            "output_name": "answer",
            "schema_ref": {"schema": "text"},
            "resolution_kind": "merge_plan_output",
            "merge_slot_id": "slot_a",
            "required": True,
        },
        "task_id": "task_demo",
        "owner_unit_id": "unit_parent",
        "canonical_selection_id": "canonical_selection:task_demo:unit_parent",
        "canonical_output_bundle_digest": "sha256:bundle",
        "source_proposal_id": "proposal_1",
        "source_expansion_decision_id": "expansion_decision:scope_1",
        "logical_position": 3,
        "child_unit_ids_by_key": {"child_a": "unit_child_a"},
        "merge_plan_id": "merge_plan_1",
    }

    first = ExpectedOutputRef.from_expected_output(created_event_seq=99, **common)
    second = ExpectedOutputRef.from_expected_output(created_event_seq=100, **common)

    assert first.expected_output_id == second.expected_output_id
    assert first.expected_output_id == "expected_output:proposal_1:unit_parent:answer:3"
    assert first.created_event_seq == 99
    assert second.created_event_seq == 100


def _verification_report_kwargs(report_id: str) -> dict:
    answer_ref = make_artifact_ref("artifact_answer")
    return {
        "verification_report_id": report_id,
        "task_id": "task_demo",
        "unit_id": "unit_parent",
        "attempt_id": "attempt_1",
        "submission_id": "submission_1",
        "submission_event_seq": 4,
        "candidate_output_bundle_digest": "sha256:bundle",
        "candidate_output_refs": {"answer": answer_ref},
        "required_output_names": ["answer"],
        "output_contract_id": "contract_structured_report",
        "validator_policy_id": "validator_structured_report",
        "plugin_id": "structured_report_stub",
        "plugin_version": "0.1.0",
        "plugin_descriptor_digest": "sha256:plugin_descriptor",
        "status": "passed",
        "eligible_for_canonical": False,
        "layer_results": {
            layer_name: {
                "status": "passed",
                "reason_code": "ok",
                "summary": "ok",
                "evidence_refs": [],
                "checked_at": NOW,
            }
            for layer_name in REQUIRED_VERIFICATION_LAYERS
        },
        "failure_summary": None,
        "verification_environment": {},
        "verifier": {},
        "started_at": NOW,
        "completed_at": NOW,
    }


def _proposal_header(proposal_id: str) -> dict:
    return {
        "proposal_id": proposal_id,
        "proposal_schema_version": "phase4.decomposition_proposal.v1",
        "task_id": "task_demo",
        "parent_unit_id": "unit_parent",
        "canonical_selection_id": "canonical_selection:task_demo:unit_parent",
        "canonical_output_bundle_digest": "sha256:bundle",
        "plugin_id": "structured_report_stub",
        "plugin_version": "0.1.0",
        "plugin_descriptor_digest": "sha256:plugin_descriptor",
        "split_strategy_id": "section_split_v1",
        "split_strategy_params_digest": "sha256:params",
        "expansion_scope_hash": "sha256:scope_1",
        "proposal_digest": "sha256:proposal",
        "created_at": NOW,
    }


def _child_spec(
    child_logical_key: str,
    *,
    input_bindings: dict | None = None,
    plugin_payload: dict | None = None,
) -> dict:
    return {
        "child_logical_key": child_logical_key,
        "unit_type": "section",
        "input_bindings": input_bindings or {},
        "required_outputs": ["answer"],
        "output_contract_refs": {"answer": {"schema": "text"}},
        "validator_policy_id": "validator_structured_report",
        "budget_limit": None,
        "deadline": None,
        "weight": 1.0,
        "required_capabilities": {"executor": "local"},
        "plugin_payload": plugin_payload or {},
        "promotion_guard_ref": None,
    }


def _dependency_edge(
    edge_logical_key: str,
    source_child_key: str,
    target_child_key: str,
    *,
    target_input_name: str = "source_answer",
) -> dict:
    return {
        "edge_logical_key": edge_logical_key,
        "source_child_key": source_child_key,
        "target_child_key": target_child_key,
        "source_output_name": "answer",
        "target_input_name": target_input_name,
        "relation_type": "depends_on_output",
    }


def _expected_output(output_name: str, *, merge_slot_id: str = "slot_a") -> dict:
    return {
        "output_name": output_name,
        "schema_ref": {"schema": "text"},
        "resolution_kind": "merge_plan_output",
        "child_key": None,
        "child_output_name": None,
        "merge_slot_id": merge_slot_id,
        "required": True,
    }


def _merge_slot(slot_id: str, child_key: str, output_name: str) -> dict:
    return {
        "slot_id": slot_id,
        "child_key": child_key,
        "child_output_name": output_name,
        "schema_ref": {"schema": "text"},
        "required": True,
        "missing_policy": "block_merge",
    }


def _promotion_guard(**overrides: bool) -> dict:
    guard = {
        "typed_io_checked": True,
        "independently_schedulable_checked": True,
        "validator_policy_checked": True,
        "output_contract_checked": True,
        "no_freeform_thought_checked": True,
        "max_depth_checked": True,
        "max_children_checked": True,
        "evidence_ref": None,
    }
    guard.update(overrides)
    return guard


def _merge_plan_header() -> dict:
    return {
        "merge_plan_id": "merge_plan_1",
        "merge_plan_schema_version": "phase4.merge_plan.v1",
        "task_id": "task_demo",
        "parent_unit_id": "unit_parent",
        "canonical_selection_id": "canonical_selection:task_demo:unit_parent",
        "decomposition_proposal_id": "proposal_1",
        "expansion_decision_id": "expansion_decision:scope_1",
        "created_by_plugin_id": "structured_report_stub",
        "created_by_plugin_version": "0.1.0",
        "merge_plan_digest": "sha256:merge",
        "created_at": NOW,
    }


def _required_slot(slot_key: str) -> dict:
    return {
        "slot_key": slot_key,
        "source_child_logical_key": "child_a",
        "source_child_unit_id": "unit_child_a",
        "source_output_name": "answer",
        "output_schema_ref": {"schema": "text"},
        "output_schema_digest": "sha256:schema",
        "required": True,
        "missing_policy": "block_merge",
    }


def _merge_policy_ref() -> dict:
    return {
        "plugin_id": "structured_report_stub",
        "plugin_version": "0.1.0",
        "merge_policy_id": "merge_sections_v1",
        "merge_policy_version": "v1",
        "merge_policy_descriptor_digest": "sha256:plugin_descriptor",
        "merge_policy_params_digest": "sha256:merge_params",
    }


def _parent_output_mapping() -> dict:
    return {
        "parent_output_name": "answer",
        "resolution_kind": "merge_plan_output",
        "merge_slot_keys": ["slot_a"],
        "result_schema_ref": {"schema": "text"},
        "result_schema_digest": "sha256:schema",
    }


def _hash_recording_requirements() -> dict:
    return {
        "record_child_canonical_output_digest": True,
        "record_slot_source_artifact_digest": True,
        "record_merge_input_bundle_digest": True,
    }


def _merge_validation_requirements() -> dict:
    return {
        "all_required_slots_canonical": True,
        "slot_schema_check_required": True,
        "merged_output_schema_check_required": True,
        "plugin_merge_validator_policy_id": "merge_validator_v1",
    }


def _merge_plugin_payload() -> dict:
    return {
        "plugin_defined_schema_ref": {"schema": "merge_payload.v1"},
        "plugin_defined_body_digest": "sha256:merge_payload",
        "plugin_defined_body": {"merge_style": "ordered_sections"},
    }
