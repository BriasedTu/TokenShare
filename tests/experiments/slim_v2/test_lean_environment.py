import json
from pathlib import Path
import subprocess

import pytest

from tokenshare.experiments.slim_v2 import cli
from tokenshare.experiments.slim_v2 import lean_environment
from tokenshare.experiments.slim_v2.lean_environment import (
    LeanEnvironmentInvalid,
    collect_checker_backed_nodes,
    run_lean_environment_test,
    validate_lean_environment_pass,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_real_catalog_inventory_skips_only_preregistered_blocked_cases() -> None:
    inventory = collect_checker_backed_nodes(_REPO_ROOT)

    assert inventory.checker_backed_case_count == 135
    assert len(inventory.nodes) == 570
    assert inventory.structured_blocked_case_count == 30
    assert inventory.structured_blocked_node_count == 120


def test_environment_test_writes_pass_only_after_every_node_is_accepted(
    tmp_path: Path,
) -> None:
    repo = _minimal_repository(tmp_path)
    pass_path = repo / "local" / "lean_environment_pass.v1.json"
    checked: list[tuple[str, str]] = []

    def checker(node) -> str:
        checked.append((node.case_id, node.node_id))
        return "accepted"

    result = run_lean_environment_test(
        repository_root=repo,
        pass_path=pass_path,
        project_builder=_fake_project_builder,
        node_checker=checker,
        created_at="2026-08-22T00:00:00Z",
    )

    assert checked == [("case_passed", "node_a"), ("case_passed", "node_b")]
    assert result["status"] == "passed"
    assert result["checker_backed_case_count"] == 1
    assert result["checked_node_count"] == 2
    assert result["structured_blocked_case_count"] == 1
    assert result["structured_blocked_node_count"] == 1
    assert (
        "fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean"
        in result["critical_input_digests"]
    )
    assert json.loads(pass_path.read_text(encoding="utf-8")) == result


def test_failed_environment_test_invalidates_existing_pass(tmp_path: Path) -> None:
    repo = _minimal_repository(tmp_path)
    pass_path = repo / "local" / "lean_environment_pass.v1.json"
    pass_path.parent.mkdir(parents=True)
    pass_path.write_bytes(b"existing-pass\n")

    with pytest.raises(LeanEnvironmentInvalid, match="node_b"):
        run_lean_environment_test(
            repository_root=repo,
            pass_path=pass_path,
            project_builder=_fake_project_builder,
            node_checker=lambda node: (
                "proof_rejected" if node.node_id == "node_b" else "accepted"
            ),
            created_at="2026-08-22T00:00:00Z",
        )

    assert not pass_path.exists()


def test_startup_validation_is_lightweight_and_fails_closed_on_object_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _minimal_repository(tmp_path)
    pass_path = repo / "local" / "lean_environment_pass.v1.json"
    run_lean_environment_test(
        repository_root=repo,
        pass_path=pass_path,
        project_builder=_fake_project_builder,
        node_checker=lambda _node: "accepted",
        created_at="2026-08-22T00:00:00Z",
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("startup validation ran a process"),
    )
    assert validate_lean_environment_pass(repo, pass_path)["status"] == "passed"

    object_path = repo / "fixtures/lean_proof_project/.lake/build/lib/lean/TokenShare/LemmaGraphOracle.olean"
    object_path.write_bytes(b"drift")
    with pytest.raises(LeanEnvironmentInvalid, match="compiled object digest"):
        validate_lean_environment_pass(repo, pass_path)

    run_lean_environment_test(
        repository_root=repo,
        pass_path=pass_path,
        project_builder=_fake_project_builder,
        node_checker=lambda _node: "accepted",
        created_at="2026-08-22T00:00:00Z",
    )
    cases_object = repo / "fixtures/lean_proof_project/.lake/build/lib/lean/TokenShare/LemmaGraphCases.olean"
    cases_object.unlink()
    with pytest.raises(LeanEnvironmentInvalid, match="compiled object.*LemmaGraphCases"):
        validate_lean_environment_pass(repo, pass_path)


def test_cli_exposes_the_independent_environment_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Path, Path]] = []

    def run(*, repository_root, pass_path):
        calls.append((Path(repository_root), Path(pass_path)))
        return {"status": "passed", "checked_node_count": 570}

    monkeypatch.setattr(lean_environment, "run_lean_environment_test", run)
    pass_path = tmp_path / "lean-pass.json"

    assert cli.main(["lean-environment-test", "--pass-path", str(pass_path)]) == 0
    assert calls == [(_REPO_ROOT, pass_path)]
    assert json.loads(capsys.readouterr().out)["checked_node_count"] == 570


def test_default_lean_library_imports_the_oracle_module() -> None:
    source = (
        _REPO_ROOT / "fixtures/lean_proof_project/TokenShare.lean"
    ).read_text(encoding="utf-8")

    assert "import TokenShare.LemmaGraphOracle" in source.splitlines()
    assert "import TokenShare.LemmaGraphCases" in source.splitlines()


def _fake_project_builder(repository_root: Path) -> tuple[Path, Path]:
    object_dir = (
        repository_root / "fixtures/lean_proof_project/.lake/build/lib/lean/TokenShare"
    )
    object_dir.mkdir(parents=True, exist_ok=True)
    oracle = object_dir / "LemmaGraphOracle.olean"
    cases = object_dir / "LemmaGraphCases.olean"
    oracle.write_bytes(b"oracle-olean")
    cases.write_bytes(b"cases-olean")
    return oracle, cases


def _minimal_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    project = repo / "fixtures" / "lean_proof_project"
    oracle = project / "TokenShare" / "LemmaGraphOracle.lean"
    oracle.parent.mkdir(parents=True)
    oracle.write_text("namespace Fixture\nend Fixture\n", encoding="utf-8")
    (oracle.parent / "LemmaGraphCases.lean").write_text(
        "namespace FixtureCases\nend FixtureCases\n", encoding="utf-8"
    )
    (project / "TokenShare.lean").write_text(
        "import TokenShare.LemmaGraphCases\nimport TokenShare.LemmaGraphOracle\n",
        encoding="utf-8",
    )
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.8.0\n", encoding="utf-8")
    (project / "lakefile.lean").write_text("lean_lib TokenShare\n", encoding="utf-8")
    checker = repo / "src" / "tokenshare" / "plugins" / "lean_proof" / "checker.py"
    checker.parent.mkdir(parents=True)
    checker.write_text("# checker\n", encoding="utf-8")
    catalog = repo / "benchmarks" / "paper" / "lean_lemma_graph_catalog.v1.jsonl"
    catalog.parent.mkdir(parents=True)
    passed = {
        "case_id": "case_passed",
        "environment_digest": "sha256:" + "1" * 64,
        "preflight_status": "passed",
        "lemma_graph": {
            "nodes": [
                {"node_id": "node_a", "theorem_payload": _payload("node_a")},
                {"node_id": "node_b", "theorem_payload": _payload("node_b")},
            ]
        },
        "oracle_proof_package_ref": {
            "kind": "fixed_oracle_package",
            "source_path": "fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean",
            "content_hash": _sha256(oracle),
            "node_proof_sources": {"node_a": "by rfl", "node_b": "by rfl"},
        },
    }
    blocked = {
        "case_id": "case_blocked",
        "environment_digest": "sha256:" + "1" * 64,
        "preflight_status": "structured_blocked",
        "structured_blocked_reason": "missing_oracle_proof_package",
        "lemma_graph": {"nodes": [{"node_id": "blocked_node"}]},
        "oracle_proof_package_ref": None,
    }
    catalog.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in (passed, blocked)) + "\n",
        encoding="utf-8",
    )
    return repo


def _payload(theorem_name: str) -> dict[str, object]:
    return {
        "schema_version": "lean_proof.theorem_payload.v1",
        "theorem_name": theorem_name,
        "imports": ["TokenShare.LemmaGraphOracle"],
        "namespace": "Fixture",
        "open_namespaces": [],
        "options": {},
        "parameters_source": "",
        "statement_source": "True",
        "library_context": {"project": "tokenshare_lean"},
        "decomposition_policy": {
            "policy_id": "lean_proof.deterministic_tactic_split.v1",
            "allowed_rules": ["fixed_oracle_lemma_graph"],
            "max_depth": 0,
            "max_children": 0,
            "unsupported_policy": "return_unsupported",
        },
        "resource_limits": {"timeout_seconds": 30, "max_output_bytes": 65536},
    }


def _sha256(path: Path) -> str:
    from hashlib import sha256

    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return f"sha256:{sha256(normalized.encode('utf-8')).hexdigest()}"
