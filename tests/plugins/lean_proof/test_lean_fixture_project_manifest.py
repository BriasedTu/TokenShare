from pathlib import Path
import json
import shutil

import pytest

from tokenshare.plugins.lean_proof.fixtures import (
    build_lean_fixture_manifest,
    default_lean_fixture_project_path,
)
from tokenshare.plugins.lean_proof.fixed_plan import (
    LeanFixedDecompositionPlan,
    build_fixed_plan_certificate,
)
from tokenshare.plugins.lean_proof.models import LeanFixtureManifest
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.semantic_authority import (
    LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION,
    load_lean_semantic_authority,
)
from tokenshare.storage.artifacts import ArtifactStore


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_AUTHORITY_RELATIVE_PATHS = (
    "benchmarks/paper/lean_catalog.v1.jsonl",
    "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl",
    "benchmarks/paper/lean_checker_preflight.v1.json",
    "benchmarks/paper/lean_environment_semantic_authority.v1.json",
    "src/tokenshare/plugins/lean_proof/checker.py",
)


def test_lean_fixture_project_contains_toolchain_lakefile_and_helper_sources() -> None:
    project_root = default_lean_fixture_project_path()

    assert (project_root / "lean-toolchain").is_file()
    assert (project_root / "lakefile.lean").is_file()
    assert (project_root / "TokenShare" / "Helper.lean").is_file()
    assert (project_root / "TokenShare" / "SplitRules.lean").is_file()
    assert (project_root / "TokenShare" / "Merge.lean").is_file()

    manifest = build_lean_fixture_manifest()

    assert manifest.project_root == str(project_root.resolve())
    assert manifest.toolchain_file_digest.startswith("sha256:")
    assert manifest.lakefile_digest.startswith("sha256:")
    assert manifest.helper_sources_digest.startswith("sha256:")
    assert "TokenShare/Helper.lean" in manifest.helper_sources
    assert "TokenShare/SplitRules.lean" in manifest.helper_sources
    assert "TokenShare/Merge.lean" in manifest.helper_sources


def test_lean_fixture_manifest_lists_direct_decomposition_merge_and_unsupported_cases() -> None:
    manifest = build_lean_fixture_manifest()
    body = manifest.to_dict()

    assert body["schema_version"] == "lean_proof.fixture_manifest.v1"
    assert body["fixture_cases"]["lean_direct_proof"]["capabilities"] == ["direct_proof"]
    assert body["fixture_cases"]["lean_decomposition_merge"]["capabilities"] == [
        "decomposition",
        "child_proof",
        "merge_proof",
    ]
    assert body["fixture_cases"]["lean_unsupported_decomposition"]["expected_status"] == (
        "unsupported_decomposition"
    )
    assert body["fixture_cases"]["lean_invalid_proof"]["expected_status"] == "rejected"


def test_lean_helper_source_digest_changes_when_helper_changes(tmp_path: Path) -> None:
    source_project = default_lean_fixture_project_path()
    copied_project = tmp_path / "lean_project"
    _copy_project(source_project, copied_project)

    before = build_lean_fixture_manifest(project_root=copied_project)
    helper = copied_project / "TokenShare" / "Helper.lean"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n#eval \"digest changed\"\n",
        encoding="utf-8",
    )
    after = build_lean_fixture_manifest(project_root=copied_project)

    assert before.helper_sources_digest != after.helper_sources_digest
    assert before.manifest_digest != after.manifest_digest

    with_manifest_digest = after.to_dict()
    with_manifest_digest["manifest_digest"] = "sha256:wrong"
    with pytest.raises(ValueError, match="manifest_digest"):
        LeanFixtureManifest.from_dict(with_manifest_digest)


def test_lean_semantic_authority_requires_versioned_sidecar(tmp_path: Path) -> None:
    copied_root = tmp_path / "without_sidecar"
    _copy_semantic_authority_repository(copied_root, include_sidecar=False)

    with pytest.raises(ValueError, match="semantic authority sidecar"):
        load_lean_semantic_authority(repository_root=copied_root)


def test_lean_semantic_authority_is_checkout_and_line_ending_independent(
    tmp_path: Path,
) -> None:
    lf_root = tmp_path / "lf_checkout"
    crlf_root = tmp_path / "crlf_checkout"
    _copy_semantic_authority_repository(lf_root)
    _copy_semantic_authority_repository(crlf_root)
    _rewrite_semantic_sources(lf_root, newline="\n")
    _rewrite_semantic_sources(crlf_root, newline="\r\n")

    lf_authority = load_lean_semantic_authority(repository_root=lf_root)
    crlf_authority = load_lean_semantic_authority(repository_root=crlf_root)

    assert lf_authority.schema_version == LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION
    assert lf_authority.authority_environment_digest == (
        crlf_authority.authority_environment_digest
    )
    assert lf_authority.semantic_environment_digest == (
        crlf_authority.semantic_environment_digest
    )
    assert lf_authority.semantic_checker_digest == crlf_authority.semantic_checker_digest
    assert lf_authority.sidecar_digest == crlf_authority.sidecar_digest


@pytest.mark.parametrize(
    "relative_path",
    (
        "fixtures/lean_proof_project/TokenShare/Helper.lean",
        "fixtures/lean_proof_project/lean-toolchain",
        "fixtures/lean_proof_project/lakefile.lean",
        "src/tokenshare/plugins/lean_proof/checker.py",
    ),
)
def test_lean_semantic_authority_rejects_real_content_drift(
    tmp_path: Path,
    relative_path: str,
) -> None:
    copied_root = tmp_path / "changed_checkout"
    _copy_semantic_authority_repository(copied_root)
    changed = copied_root / relative_path
    changed.write_text(
        changed.read_text(encoding="utf-8") + "\n-- semantic drift\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="semantic content drift"):
        load_lean_semantic_authority(repository_root=copied_root)


def test_environment_ref_records_legacy_and_semantic_authority_digests(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "runtime_checkout"
    _copy_semantic_authority_repository(copied_root)
    project_root = copied_root / "fixtures/lean_proof_project"
    manifest = LeanEnvironmentManifest.from_project(
        project_root=project_root,
        lean_executable=copied_root / "tools/lean.exe",
        lake_executable=copied_root / "tools/lake.exe",
        lean_version="Lean test",
        lake_version="Lake test",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at="2026-08-04T00:00:00Z",
    )

    environment_ref = build_lean_environment_ref(manifest)
    authority = load_lean_semantic_authority(repository_root=copied_root)

    assert environment_ref.tool_versions["authority_environment_digest"] == (
        authority.authority_environment_digest
    )
    assert environment_ref.tool_versions["semantic_environment_digest"] == (
        authority.semantic_environment_digest
    )
    assert environment_ref.tool_versions["sidecar_digest"] == authority.sidecar_digest
    assert environment_ref.tool_versions["semantic_authority_schema_version"] == (
        LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION
    )

    case = json.loads(
        next(
            line
            for line in (
                copied_root / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    )
    plan = LeanFixedDecompositionPlan.from_catalog_case(case)
    store = ArtifactStore(tmp_path / "authority_bridge_artifacts")
    parent_ref = store.save_json(
        plan.parent_theorem_payload().to_dict(),
        artifact_id="semantic_authority_bridge_parent",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={"case_id": plan.case_id},
        created_at="2026-08-04T00:00:00Z",
    )
    certificate = build_fixed_plan_certificate(
        plan=plan,
        parent_theorem_payload_ref=parent_ref,
        environment_manifest=manifest,
    )
    assert certificate.environment_digest == manifest.environment_digest
    assert certificate.diagnostics["environment_authority_bridge"] == {
        "schema_version": "tokenshare.lean_environment_authority_bridge.v1",
        "semantic_authority_schema_version": LEAN_SEMANTIC_AUTHORITY_SCHEMA_VERSION,
        "authority_environment_digest": authority.authority_environment_digest,
        "runtime_environment_digest": manifest.environment_digest,
        "semantic_environment_digest": authority.semantic_environment_digest,
        "sidecar_digest": authority.sidecar_digest,
    }

    forged_manifest = LeanEnvironmentManifest(
        project_root=manifest.project_root,
        lean_executable=manifest.lean_executable,
        lake_executable=manifest.lake_executable,
        lean_version=manifest.lean_version,
        lake_version=manifest.lake_version,
        toolchain_file_digest="sha256:" + "0" * 64,
        lakefile_digest=manifest.lakefile_digest,
        import_set_digest=manifest.import_set_digest,
        helper_sources_digest=manifest.helper_sources_digest,
        fixture_profile_digest=manifest.fixture_profile_digest,
        resource_limits=dict(manifest.resource_limits),
        created_at=manifest.created_at,
    )
    ordinary_forged_ref = build_lean_environment_ref(forged_manifest)
    assert ordinary_forged_ref.environment_digest == forged_manifest.environment_digest
    assert {
        "semantic_authority_schema_version",
        "authority_environment_digest",
        "runtime_environment_digest",
        "semantic_environment_digest",
        "semantic_checker_digest",
        "sidecar_digest",
    }.isdisjoint(ordinary_forged_ref.tool_versions)
    with pytest.raises(ValueError, match="current project"):
        build_fixed_plan_certificate(
            plan=plan,
            parent_theorem_payload_ref=parent_ref,
            environment_manifest=forged_manifest,
        )


def _copy_project(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _copy_semantic_authority_repository(
    destination: Path,
    *,
    include_sidecar: bool = True,
) -> None:
    for relative_path in _AUTHORITY_RELATIVE_PATHS:
        if not include_sidecar and relative_path.endswith(
            "lean_environment_semantic_authority.v1.json"
        ):
            continue
        source = _REPOSITORY_ROOT / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    _copy_project(
        _REPOSITORY_ROOT / "fixtures/lean_proof_project",
        destination / "fixtures/lean_proof_project",
    )


def _rewrite_semantic_sources(repository_root: Path, *, newline: str) -> None:
    project_root = repository_root / "fixtures/lean_proof_project"
    paths = [
        project_root / "lean-toolchain",
        project_root / "lakefile.lean",
        repository_root / "src/tokenshare/plugins/lean_proof/checker.py",
        *sorted((project_root / "TokenShare").rglob("*.lean")),
    ]
    for path in paths:
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
            "\r", "\n"
        )
        path.write_bytes(normalized.replace("\n", newline).encode("utf-8"))
