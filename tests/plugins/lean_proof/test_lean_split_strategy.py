from pathlib import Path

import pytest

from tokenshare.core.expansion import (
    DecompositionProposal,
    MergePlan,
    digest_decomposition_proposal_body,
    digest_merge_plan_body,
)
from tokenshare.plugins.lean_proof.descriptor import build_lean_proof_plugin_descriptor
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanSplitCertificate, LeanTheoremPayload
from tokenshare.plugins.lean_proof.models import canonical_json_digest
from tokenshare.plugins.lean_proof.schemas import (
    CHECKER_VALIDATOR_POLICY_ID,
    DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
    PROOF_ARTIFACT_CONTRACT_ID,
    PROOF_ARTIFACT_OUTPUT_NAME,
    VERIFIED_MERGE_POLICY_ID,
)
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperRequest,
    LeanSplitHelperReport,
    LeanSplitHelperStatus,
    build_lean_split_plan,
    run_lean_split_helper,
    _child_payload,
)
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"

FORBIDDEN_PLUGIN_PAYLOAD_KEYS = {
    "state",
    "initial_state",
    "desired_state",
    "task_state",
    "attempt_state",
    "resolution_status",
    "canonical_output_refs",
    "canonical_outputs_by_unit_id",
    "canonical_selection_id",
    "canonical_output_bundle_digest",
    "expected_output_refs",
    "merge_readiness",
}


def test_lean_split_strategy_maps_certificate_children_to_decomposition_proposal(
    tmp_path: Path,
) -> None:
    store, split_report = _split_report(tmp_path, statement_source="P ∧ Q")
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id="task_lean",
        parent_unit_id="unit_lean",
        canonical_selection_id="canonical_selection:task_lean:unit_lean",
        canonical_output_bundle_digest="sha256:lean_canonical_bundle",
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:lean_scope",
        expansion_decision_id="expansion_decision:lean_scope",
        created_at=CREATED_AT,
    )

    assert isinstance(split_plan.proposal, DecompositionProposal)
    assert split_plan.proposal.proposal_header["plugin_id"] == "lean_proof"
    assert split_plan.proposal.proposal_header["split_strategy_id"] == (
        DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID
    )
    assert split_plan.proposal.proposal_header["proposal_digest"] == (
        digest_decomposition_proposal_body(split_plan.proposal)
    )
    assert len(split_plan.proposal.child_specs) == 2
    assert split_plan.proposal.dependency_edges == []
    assert split_plan.proposal.promotion_guard_evidence[
        "lean_split_certificate_ref"
    ] == split_report.certificate_ref.to_dict()

    for child_spec, child_ref in zip(
        split_plan.proposal.child_specs,
        split_plan.child_payload_refs_by_logical_key.values(),
    ):
        assert child_spec["unit_type"] == "lean_proof_subgoal"
        assert child_spec["required_outputs"] == [PROOF_ARTIFACT_OUTPUT_NAME]
        assert child_spec["validator_policy_id"] == CHECKER_VALIDATOR_POLICY_ID
        assert child_spec["output_contract_refs"][PROOF_ARTIFACT_OUTPUT_NAME] == {
            "output_contract_id": PROOF_ARTIFACT_CONTRACT_ID,
            "schema_ref": {
                "schema_version": "lean_proof.proof_artifact.v1",
                "artifact_schema_id": "lean_proof.proof_artifact",
                "artifact_schema_version": "v1",
            },
        }
        binding = child_spec["input_bindings"]["child_theorem_payload"]
        assert binding["kind"] == "artifact_ref"
        assert binding["artifact_ref"] == child_ref.to_dict()
        assert store.verify(child_ref)
        _assert_no_forbidden_plugin_payload_keys(child_spec["plugin_payload"])


def test_lean_split_strategy_maps_merge_skeleton_to_merge_plan(tmp_path: Path) -> None:
    store, split_report = _split_report(tmp_path, statement_source="P ↔ Q")
    split_plan = build_lean_split_plan(
        split_report=split_report,
        artifact_store=store,
        task_id="task_lean",
        parent_unit_id="unit_lean",
        canonical_selection_id="canonical_selection:task_lean:unit_lean",
        canonical_output_bundle_digest="sha256:lean_canonical_bundle",
        plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
        expansion_scope_hash="sha256:lean_scope",
        expansion_decision_id="expansion_decision:lean_scope",
        created_at=CREATED_AT,
    )

    assert isinstance(split_plan.merge_plan, MergePlan)
    assert split_plan.merge_plan.merge_policy_ref["merge_policy_id"] == VERIFIED_MERGE_POLICY_ID
    assert split_plan.merge_plan.merge_plan_header["merge_plan_digest"] == (
        digest_merge_plan_body(split_plan.merge_plan)
    )
    assert len(split_plan.merge_plan.required_slots) == 2
    assert split_plan.merge_plan.required_slots[0]["source_child_logical_key"] == (
        split_plan.proposal.child_specs[0]["child_logical_key"]
    )
    assert split_plan.merge_plan.parent_output_mapping == [
        {
            "parent_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            "resolution_kind": "merge_plan_output",
            "merge_slot_keys": [
                slot["slot_key"] for slot in split_plan.merge_plan.required_slots
            ],
            "result_schema_ref": {
                "schema_version": "lean_proof.proof_artifact.v1",
                "artifact_schema_id": "lean_proof.proof_artifact",
                "artifact_schema_version": "v1",
            },
            "result_schema_digest": split_plan.merge_plan.parent_output_mapping[0][
                "result_schema_digest"
            ],
        }
    ]
    plugin_body = split_plan.merge_plan.plugin_payload["plugin_defined_body"]
    assert plugin_body["summary"]["merge_rule_id"] == "lean_merge.iff_intro.v1"
    assert plugin_body["summary"]["split_certificate_id"] == (
        split_report.certificate.split_certificate_id
    )
    _assert_no_forbidden_plugin_payload_keys(split_plan.merge_plan.plugin_payload)


def test_lean_split_strategy_never_uses_ai_output_as_decomposition_authority(
    tmp_path: Path,
) -> None:
    store, split_report = _split_report(tmp_path, statement_source="P ∧ Q")
    ai_output_ref = store.save_json(
        {
            "schema_version": "phase3.raw_model_output.v1",
            "text": '{"children":[{"claim":"AI invented split"}]}',
        },
        artifact_id="ai_suggested_split_plan",
        artifact_type="RawModelOutput",
        artifact_schema_id="phase3.raw_model_output",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"must_not_be_used_for_decomposition": True},
        created_at=CREATED_AT,
    )

    with pytest.raises(ValueError, match="AI output cannot define Lean decomposition"):
        build_lean_split_plan(
            split_report=split_report,
            artifact_store=store,
            task_id="task_lean",
            parent_unit_id="unit_lean",
            canonical_selection_id="canonical_selection:task_lean:unit_lean",
            canonical_output_bundle_digest="sha256:lean_canonical_bundle",
            plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
            expansion_scope_hash="sha256:lean_scope",
            expansion_decision_id="expansion_decision:lean_scope",
            created_at=CREATED_AT,
            executor_decomposition_authority_ref=ai_output_ref,
        )


def test_lean_split_strategy_rejects_certificate_with_missing_child_payload_digest() -> None:
    body = {
        "schema_version": "lean_proof.split_certificate.v1",
        "split_certificate_id": "lean_split_certificate:bad",
        "parent_theorem_payload_ref": None,
        "normalized_parent_goal_digest": "sha256:parent",
        "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        "rule_id": "lean_split.conjunction_goal.v1",
        "rule_trace": [{"rule_id": "lean_split.conjunction_goal.v1"}],
        "split_kind": "all_required_children",
        "child_goals": [
            {
                "child_logical_key": "child:missing_digest",
                "theorem_name": "missing_digest",
                "parameters_source": "(P Q : Prop)",
                "statement_source": "P",
                "context_digest": "sha256:ctx",
                "required_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            }
        ],
        "merge_skeleton": {"merge_rule_id": "lean_merge.conjunction_intro.v1"},
        "unsupported_reason": None,
        "helper_stdout_ref": None,
        "helper_stderr_ref": None,
        "diagnostics": {},
    }

    with pytest.raises(ValueError, match="child_payload_digest"):
        LeanSplitCertificate.from_dict(body)


def test_lean_split_strategy_rejects_certificate_exceeding_parent_policy(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path)
    parent_payload = _theorem_payload(
        decomposition_policy={
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["conjunction"],
            "max_depth": 4,
            "max_children": 1,
            "unsupported_policy": "return_unsupported",
        }
    )
    parent_ref = store.save_json(
        parent_payload.to_dict(),
        artifact_id="lean_parent_payload_policy_max_one",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": parent_payload.theorem_name},
        created_at=CREATED_AT,
    )
    child_goals = [
        {
            "child_logical_key": "child:left",
            "theorem_name": "split_fixture_left",
            "parameters_source": "(P Q : Prop)",
            "statement_source": "P",
            "context_digest": "sha256:lean_context_left",
            "required_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            "source_rule_id": "lean_split.conjunction_goal.v1",
        },
        {
            "child_logical_key": "child:right",
            "theorem_name": "split_fixture_right",
            "parameters_source": "(P Q : Prop)",
            "statement_source": "Q",
            "context_digest": "sha256:lean_context_right",
            "required_output_name": PROOF_ARTIFACT_OUTPUT_NAME,
            "source_rule_id": "lean_split.conjunction_goal.v1",
        },
    ]
    child_goals = [
        {
            **child,
            "child_payload_digest": _child_payload(parent_payload, child).payload_digest,
        }
        for child in child_goals
    ]
    certificate = LeanSplitCertificate(
        split_certificate_id="lean_split_certificate:policy_max_one",
        parent_theorem_payload_ref=parent_ref,
        normalized_parent_goal_digest=canonical_json_digest(
            {
                "theorem_payload_digest": parent_payload.payload_digest,
                "statement_source": parent_payload.statement_source,
                "parameters_source": parent_payload.parameters_source,
            }
        ),
        policy_id=DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
        rule_id="lean_split.conjunction_goal.v1",
        rule_trace=[{"rule_id": "lean_split.conjunction_goal.v1"}],
        split_kind="all_required_children",
        child_goals=child_goals,
        merge_skeleton={
            "merge_rule_id": "lean_merge.conjunction_intro.v1",
            "merge_policy_id": VERIFIED_MERGE_POLICY_ID,
        },
        unsupported_reason=None,
        helper_stdout_ref=None,
        helper_stderr_ref=None,
        diagnostics={"source": "test"},
    )
    certificate_ref = store.save_json(
        certificate.to_dict(),
        artifact_id="lean_split_certificate_policy_max_one",
        artifact_type="LeanSplitCertificate",
        artifact_schema_id="lean_proof.split_certificate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"rule_id": certificate.rule_id},
        created_at=CREATED_AT,
    )
    split_report = LeanSplitHelperReport(
        report_id="lean_split_helper_report:policy_max_one",
        request_id="lean_split_request:policy_max_one",
        status=LeanSplitHelperStatus.SUCCEEDED,
        exit_code=0,
        generated_source_ref=None,
        helper_stdout_ref=None,
        helper_stderr_ref=None,
        certificate_ref=certificate_ref,
        report_ref=None,
        certificate=certificate,
        diagnostics={},
        environment_ref=build_lean_environment_ref(_environment_manifest()),
        command_summary={},
        duration_ms=0,
        helper_stdout_excerpt="",
        helper_stderr_excerpt="",
    )

    with pytest.raises(ValueError, match="max_children"):
        build_lean_split_plan(
            split_report=split_report,
            artifact_store=store,
            task_id="task_lean",
            parent_unit_id="unit_lean",
            canonical_selection_id="canonical_selection:task_lean:unit_lean",
            canonical_output_bundle_digest="sha256:lean_canonical_bundle",
            plugin_descriptor_digest=build_lean_proof_plugin_descriptor().descriptor_digest,
            expansion_scope_hash="sha256:lean_scope",
            expansion_decision_id="expansion_decision:lean_scope",
            created_at=CREATED_AT,
        )


def _split_report(tmp_path: Path, *, statement_source: str):
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    payload = _theorem_payload(statement_source=statement_source)
    payload_ref = store.save_json(
        payload.to_dict(),
        artifact_id=f"payload_{statement_source.encode('utf-8').hex()}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": payload.theorem_name},
        created_at=CREATED_AT,
    )
    split_report = run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id=f"lean_split_request:{statement_source.encode('utf-8').hex()}",
            theorem_payload_ref=payload_ref,
            environment_ref=build_lean_environment_ref(manifest),
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )
    return store, split_report


def _environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    return LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=CREATED_AT,
    )


def _theorem_payload(**overrides) -> LeanTheoremPayload:
    values = {
        "theorem_id": "lean_theorem:split_fixture",
        "theorem_name": "split_fixture",
        "imports": ["Init"],
        "namespace": "TokenShareGenerated",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "(P Q : Prop)",
        "statement_source": "P ∧ Q",
        "theorem_source": None,
        "proof_candidate_ref": None,
        "library_context": {
            "project": "tokenshare_lean",
            "module": "TokenShareGenerated.Split",
        },
        "decomposition_policy": {
            "policy_id": DETERMINISTIC_TACTIC_SPLIT_STRATEGY_ID,
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    values.update(overrides)
    return LeanTheoremPayload(**values)


def _assert_no_forbidden_plugin_payload_keys(value) -> None:
    if isinstance(value, dict):
        blocked = FORBIDDEN_PLUGIN_PAYLOAD_KEYS.intersection(value)
        assert not blocked
        for item in value.values():
            _assert_no_forbidden_plugin_payload_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_plugin_payload_keys(item)
