from pathlib import Path

import pytest

from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.preflight import LeanPreflightStatus, run_lean_preflight


CREATED_AT = "2026-06-29T00:00:00Z"


def test_lean_preflight_reports_blocked_when_toolchain_missing(tmp_path: Path) -> None:
    project_root = _minimal_project(tmp_path)
    missing_lean = tmp_path / "missing" / "lean.exe"
    missing_lake = tmp_path / "missing" / "lake.exe"

    result = run_lean_preflight(
        project_root=project_root,
        lean_executable=missing_lean,
        lake_executable=missing_lake,
        resource_limits={"timeout_seconds": 30},
        created_at=CREATED_AT,
    )

    assert result.status == LeanPreflightStatus.BLOCKED_MISSING_TOOLCHAIN
    assert result.ready is False
    assert result.blocked_reason["blocker_kind"] == "blocked_missing_toolchain"
    assert str(missing_lean) in result.blocked_reason["missing_paths"]
    assert str(missing_lake) in result.blocked_reason["missing_paths"]
    assert result.environment_ref is None


def test_lean_environment_ref_records_executables_toolchain_lake_project_and_digests(
    tmp_path: Path,
) -> None:
    project_root = _minimal_project(tmp_path)
    lean_exe = _write_executable(tmp_path / "bin" / "lean.exe")
    lake_exe = _write_executable(tmp_path / "bin" / "lake.exe")

    manifest = LeanEnvironmentManifest.from_project(
        project_root=project_root,
        lean_executable=lean_exe,
        lake_executable=lake_exe,
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu)",
        lake_version="Lake version 5.0.0",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=CREATED_AT,
    )
    environment_ref = build_lean_environment_ref(manifest)
    body = environment_ref.to_dict()

    assert body["runtime"] == "lean"
    assert body["environment_digest"].startswith("sha256:")
    assert body["tool_versions"]["lean_version"] == manifest.lean_version
    assert body["tool_versions"]["lake_version"] == manifest.lake_version
    assert body["tool_versions"]["lean_executable"] == str(lean_exe.resolve())
    assert body["tool_versions"]["lake_executable"] == str(lake_exe.resolve())
    assert body["tool_versions"]["toolchain_file_digest"].startswith("sha256:")
    assert body["tool_versions"]["lakefile_digest"].startswith("sha256:")
    assert body["tool_versions"]["helper_sources_digest"].startswith("sha256:")
    assert body["tool_versions"]["lake_project_root"] == str(project_root.resolve())
    assert body["fixture_profile_digest"].startswith("sha256:")
    assert body["resource_limits"] == {"timeout_seconds": 30, "max_output_bytes": 65536}


def test_lean_preflight_rejects_environment_digest_mismatch(tmp_path: Path) -> None:
    project_root = _minimal_project(tmp_path)
    lean_exe = _write_executable(tmp_path / "bin" / "lean.exe")
    lake_exe = _write_executable(tmp_path / "bin" / "lake.exe")
    manifest = LeanEnvironmentManifest.from_project(
        project_root=project_root,
        lean_executable=lean_exe,
        lake_executable=lake_exe,
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu)",
        lake_version="Lake version 5.0.0",
        resource_limits={"timeout_seconds": 30},
        created_at=CREATED_AT,
    )

    with pytest.raises(ValueError, match="environment_digest"):
        LeanEnvironmentManifest.from_dict(
            {
                **manifest.to_dict(),
                "environment_digest": "sha256:wrong",
            }
        )


def _minimal_project(root: Path) -> Path:
    project_root = root / "lean_project"
    (project_root / "TokenShare").mkdir(parents=True)
    (project_root / "lean-toolchain").write_text("leanprover/lean4:v4.8.0\n", encoding="utf-8")
    (project_root / "lakefile.lean").write_text(
        'import Lake\nopen Lake DSL\npackage "tokenshare_lean"\n',
        encoding="utf-8",
    )
    (project_root / "TokenShare" / "Helper.lean").write_text(
        "namespace TokenShare\n#eval \"helper\"\nend TokenShare\n",
        encoding="utf-8",
    )
    (project_root / "TokenShare" / "SplitRules.lean").write_text(
        "namespace TokenShare\n#eval \"split\"\nend TokenShare\n",
        encoding="utf-8",
    )
    (project_root / "TokenShare" / "Merge.lean").write_text(
        "namespace TokenShare\n#eval \"merge\"\nend TokenShare\n",
        encoding="utf-8",
    )
    return project_root


def _write_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@echo off\n", encoding="utf-8")
    return path
