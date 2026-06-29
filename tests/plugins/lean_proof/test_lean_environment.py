from pathlib import Path

from tokenshare.plugins.lean_proof.environment import LeanEnvironmentManifest
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path


def test_lean_environment_digest_ignores_manifest_creation_time() -> None:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"

    first = LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at="2026-06-29T00:00:00Z",
    )
    second = LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version="Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)",
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at="2026-06-29T01:00:00Z",
    )

    assert first.environment_digest == second.environment_digest
    assert first.to_dict()["created_at"] == "2026-06-29T00:00:00Z"
    assert second.to_dict()["created_at"] == "2026-06-29T01:00:00Z"
