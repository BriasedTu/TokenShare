from pathlib import Path

import pytest

from tokenshare.plugins.lean_proof.fixtures import (
    build_lean_fixture_manifest,
    default_lean_fixture_project_path,
)
from tokenshare.plugins.lean_proof.models import LeanFixtureManifest


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


def _copy_project(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
