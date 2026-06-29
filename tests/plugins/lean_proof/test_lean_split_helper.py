import json
from pathlib import Path

from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.plugins.lean_proof.split_strategy import (
    LeanSplitHelperRequest,
    LeanSplitHelperStatus,
    run_lean_split_helper,
)
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_split_helper_outputs_certificate_for_conjunction_goal(
    tmp_path: Path,
) -> None:
    report = _run_split_helper(tmp_path, _theorem_payload(statement_source="P ∧ Q"))

    assert report.status == LeanSplitHelperStatus.SUCCEEDED
    assert report.certificate_ref is not None
    assert report.certificate is not None
    assert report.certificate.split_kind == "all_required_children"
    assert report.certificate.rule_id == "lean_split.conjunction_goal.v1"
    assert [child["statement_source"] for child in report.certificate.child_goals] == [
        "P",
        "Q",
    ]
    assert report.certificate.merge_skeleton["merge_rule_id"] == (
        "lean_merge.conjunction_intro.v1"
    )


def test_lean_split_helper_outputs_certificate_for_iff_goal(tmp_path: Path) -> None:
    report = _run_split_helper(tmp_path, _theorem_payload(statement_source="P ↔ Q"))

    assert report.status == LeanSplitHelperStatus.SUCCEEDED
    assert report.certificate is not None
    assert report.certificate.split_kind == "all_required_children"
    assert report.certificate.rule_id == "lean_split.iff_goal.v1"
    assert [child["statement_source"] for child in report.certificate.child_goals] == [
        "P → Q",
        "Q → P",
    ]
    assert report.certificate.merge_skeleton["merge_rule_id"] == "lean_merge.iff_intro.v1"


def test_lean_split_helper_does_not_advertise_unmerged_intro_rules(
    tmp_path: Path,
) -> None:
    implication = _run_split_helper(
        tmp_path,
        _theorem_payload(statement_source="P → Q"),
        request_id="lean_split_request:implication",
    )
    forall = _run_split_helper(
        tmp_path,
        _theorem_payload(
            theorem_name="forall_refl",
            statement_source="∀ n : Nat, n = n",
            parameters_source="",
        ),
        request_id="lean_split_request:forall",
    )

    assert implication.certificate is not None
    assert implication.status == LeanSplitHelperStatus.UNSUPPORTED
    assert implication.certificate.split_kind == "unsupported"
    assert implication.certificate.rule_id == "lean_split.unsupported.v1"
    assert implication.certificate.child_goals == []
    assert implication.certificate.unsupported_reason == "unsupported_merge_rule"

    assert forall.certificate is not None
    assert forall.status == LeanSplitHelperStatus.UNSUPPORTED
    assert forall.certificate.split_kind == "unsupported"
    assert forall.certificate.rule_id == "lean_split.unsupported.v1"
    assert forall.certificate.child_goals == []
    assert forall.certificate.unsupported_reason == "unsupported_merge_rule"


def test_lean_split_helper_returns_unsupported_for_uncovered_goal_shape(
    tmp_path: Path,
) -> None:
    report = _run_split_helper(
        tmp_path,
        _theorem_payload(statement_source="Nat.succ n = 0", theorem_name="unsupported"),
    )

    assert report.status == LeanSplitHelperStatus.UNSUPPORTED
    assert report.certificate is not None
    assert report.certificate.split_kind == "unsupported"
    assert report.certificate.rule_id == "lean_split.unsupported.v1"
    assert report.certificate.child_goals == []
    assert report.certificate.unsupported_reason == "unsupported_goal_shape"
    assert report.helper_stdout_ref is not None
    body = json.loads(report.helper_stdout_excerpt)
    assert body["schema_version"] == "lean_proof.split_certificate.v1"


def test_lean_split_helper_enforces_parent_decomposition_policy(tmp_path: Path) -> None:
    report = _run_split_helper(
        tmp_path,
        _theorem_payload(
            statement_source="P ∧ Q",
            decomposition_policy={
                "policy_id": "lean_proof.deterministic_tactic_split.v1",
                "allowed_rules": [],
                "max_depth": 4,
                "max_children": 8,
                "unsupported_policy": "return_unsupported",
            },
        ),
        request_id="lean_split_request:policy_blocked",
    )

    assert report.status == LeanSplitHelperStatus.UNSUPPORTED
    assert report.certificate is not None
    assert report.certificate.split_kind == "unsupported"
    assert report.certificate.unsupported_reason == "rule_disallowed_by_policy"
    assert report.certificate.child_goals == []


def test_lean_split_helper_rejects_unelaborable_parent_goal(tmp_path: Path) -> None:
    report = _run_split_helper(
        tmp_path,
        _theorem_payload(
            statement_source="P ∧ Q",
            parameters_source="",
            options={"autoImplicit": False},
        ),
        request_id="lean_split_request:unelaborable",
    )

    assert report.status == LeanSplitHelperStatus.UNSUPPORTED
    assert report.certificate is not None
    assert report.certificate.split_kind == "unsupported"
    assert report.certificate.unsupported_reason == "parent_goal_elaboration_failed"
    assert report.certificate.child_goals == []


def _run_split_helper(
    tmp_path: Path,
    payload: LeanTheoremPayload,
    *,
    request_id: str = "lean_split_request:default",
):
    store = ArtifactStore(tmp_path)
    manifest = _environment_manifest()
    payload_ref = store.save_json(
        payload.to_dict(),
        artifact_id=f"{request_id.replace(':', '_')}_theorem_payload",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"theorem_name": payload.theorem_name},
        created_at=CREATED_AT,
    )
    return run_lean_split_helper(
        LeanSplitHelperRequest(
            request_id=request_id,
            theorem_payload_ref=payload_ref,
            environment_ref=build_lean_environment_ref(manifest),
            timeout_seconds=30,
            max_output_bytes=65536,
            created_at=CREATED_AT,
        ),
        artifact_store=store,
        environment_manifest=manifest,
    )


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
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["conjunction", "iff", "intro"],
            "max_depth": 4,
            "max_children": 8,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }
    values.update(overrides)
    return LeanTheoremPayload(**values)
