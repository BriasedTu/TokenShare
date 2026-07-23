from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import tokenshare.plugins.lean_proof.checker as checker_module
from tokenshare.plugins.lean_proof.checker import (
    LeanCheckerMode,
    LeanCheckerRequest,
    LeanCheckerStatus,
    check_lean_proof,
    prepared_lean_environment,
)
from tokenshare.plugins.lean_proof.environment import (
    LeanEnvironmentManifest,
    build_lean_environment_ref,
)
from tokenshare.plugins.lean_proof.fixtures import default_lean_fixture_project_path
from tokenshare.plugins.lean_proof.models import LeanTheoremPayload
from tokenshare.storage.artifacts import ArtifactStore


CREATED_AT = "2026-07-22T00:00:00Z"


@pytest.fixture(autouse=True)
def _isolate_prepared_environment_cache():
    checker_module._clear_prepared_lean_environment_cache()
    yield
    checker_module._clear_prepared_lean_environment_cache()


def test_direct_checker_bootstraps_lake_environment_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _environment_manifest()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if command[0] == manifest.lake_executable:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "LEAN_PATH": "C:/lean/path",
                        "PATH": "C:/lean/bin",
                        "TOKENSHARE_TEST_SECRET": "must-not-be-cached",
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(checker_module.subprocess, "run", fake_run)
    checker_module._clear_prepared_lean_environment_cache()

    for index in range(2):
        store = ArtifactStore(tmp_path / f"run_{index}")
        request = _request(store, manifest=manifest, index=index)
        report = check_lean_proof(
            request,
            artifact_store=store,
            environment_manifest=manifest,
        )
        assert report.status == LeanCheckerStatus.ACCEPTED

    lake_calls = [call for call in calls if call[0] == manifest.lake_executable]
    direct_calls = [call for call in calls if call[0] == manifest.lean_executable]
    assert len(lake_calls) == 1
    assert len(direct_calls) == 2
    assert all(call[1].endswith(".lean") for call in direct_calls)


def test_bootstrap_cache_retains_only_allowlisted_environment(
    monkeypatch,
) -> None:
    manifest = _environment_manifest()

    def fake_run(command, **kwargs):
        del command, kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "LEAN_PATH": "C:/lean/path",
                    "PATH": "C:/lean/bin",
                    "TOKENSHARE_TEST_SECRET": "must-not-be-cached",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(checker_module.subprocess, "run", fake_run)
    checker_module._clear_prepared_lean_environment_cache()

    environment = prepared_lean_environment(manifest)

    assert environment["LEAN_PATH"] == "C:/lean/path"
    assert "TOKENSHARE_TEST_SECRET" not in environment


def test_direct_checker_timeout_is_persisted_without_proof_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _environment_manifest()

    def fake_run(command, **kwargs):
        del kwargs
        if command[0] == manifest.lake_executable:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"LEAN_PATH": "C:/lean/path", "PATH": "C:/lean/bin"}),
                stderr="",
            )
        raise subprocess.TimeoutExpired(
            command,
            timeout=1,
            output="partial stdout",
            stderr="partial stderr",
        )

    monkeypatch.setattr(checker_module.subprocess, "run", fake_run)
    store = ArtifactStore(tmp_path / "timeout")
    request = _request(store, manifest=manifest, index=99)

    report = check_lean_proof(
        request,
        artifact_store=store,
        environment_manifest=manifest,
    )

    assert report.status == LeanCheckerStatus.TIMEOUT
    assert report.exit_code is None
    assert report.proof_artifact_ref is None
    assert report.stdout_ref is not None
    assert report.stderr_ref is not None
    assert report.report_ref is not None


def _request(
    store: ArtifactStore,
    *,
    manifest: LeanEnvironmentManifest,
    index: int,
) -> LeanCheckerRequest:
    payload = LeanTheoremPayload(
        theorem_id=f"lean_theorem:cache_{index}",
        theorem_name=f"cache_test_{index}",
        imports=["Init"],
        namespace="TokenShareGenerated",
        open_namespaces=[],
        options={},
        parameters_source="",
        statement_source="True",
        theorem_source=None,
        proof_candidate_ref=None,
        library_context={},
        decomposition_policy={
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["leaf_close"],
            "max_depth": 0,
            "max_children": 0,
            "unsupported_policy": "return_unsupported",
        },
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
    )
    theorem_ref = store.save_json(
        payload.to_dict(),
        artifact_id=f"cache_theorem_{index}",
        artifact_type="LeanTheoremPayload",
        artifact_schema_id="lean_proof.theorem_payload",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    proof_ref = store.save_json(
        {
            "schema_version": "lean_proof.proof_candidate.v1",
            "proof_candidate_id": f"proof_candidate:cache_{index}",
            "theorem_payload_digest": payload.payload_digest,
            "proof_source": "by\n  trivial",
            "created_at": CREATED_AT,
        },
        artifact_id=f"cache_proof_{index}",
        artifact_type="LeanProofCandidate",
        artifact_schema_id="lean_proof.proof_candidate",
        artifact_schema_version="v1",
        source={"kind": "test"},
        metadata={},
        created_at=CREATED_AT,
    )
    return LeanCheckerRequest(
        request_id=f"lean_checker_cache_{index}",
        theorem_payload_ref=theorem_ref,
        proof_candidate_ref=proof_ref,
        environment_ref=build_lean_environment_ref(manifest),
        checker_mode=LeanCheckerMode.DIRECT_PROOF,
        timeout_seconds=30,
        max_output_bytes=65536,
        created_at=CREATED_AT,
    )


def _environment_manifest() -> LeanEnvironmentManifest:
    tools_root = Path.home() / "AppData" / "Local" / "TokenShare" / "LeanToolchain"
    elan_home = tools_root / "elan-home"
    return LeanEnvironmentManifest.from_project(
        project_root=default_lean_fixture_project_path(),
        lean_executable=elan_home / "bin" / "lean.exe",
        lake_executable=elan_home / "bin" / "lake.exe",
        lean_version=(
            "Lean (version 4.8.0, x86_64-w64-windows-gnu, "
            "commit df668f00e6c0, Release)"
        ),
        lake_version="Lake version 5.0.0-df668f0 (Lean version 4.8.0)",
        resource_limits={"timeout_seconds": 30, "max_output_bytes": 65536},
        created_at=CREATED_AT,
    )
