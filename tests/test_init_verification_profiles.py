from __future__ import annotations

import json
from pathlib import Path

import pytest

from verification import run_verification


ROOT = Path(__file__).resolve().parents[1]
FAST_MANIFEST = ROOT / "verification" / "fast-tests.txt"


def _fast_test_entries() -> list[str]:
    assert FAST_MANIFEST.is_file(), "默认快速验证必须使用共享测试清单"
    return [
        line.strip()
        for line in FAST_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_fast_manifest_is_nonempty_unique_and_resolvable() -> None:
    entries = _fast_test_entries()

    assert entries, "快速测试清单不能为空"
    assert len(entries) == len(set(entries)), "快速测试清单不能包含重复路径"
    assert all((ROOT / entry).exists() for entry in entries)


def test_fast_manifest_keeps_required_smoke_coverage() -> None:
    entries = set(_fast_test_entries())

    assert {
        "tests/core",
        "tests/storage",
        "tests/executors",
        "tests/plugins/factorization",
        "tests/test_package_layout.py",
        "tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py",
        "tests/plugins/lean_proof/test_lean_environment.py",
        "tests/experiments/test_paper_experiment_contracts.py",
        "tests/experiments/test_paper_models.py",
    }.issubset(entries)


def test_fast_manifest_excludes_known_long_running_suites() -> None:
    entries = set(_fast_test_entries())

    assert "tests" not in entries
    assert "tests/experiments/test_paper_budget.py" not in entries
    assert "tests/experiments/test_paper_catalog.py" not in entries
    assert "tests/experiments/test_lean_paper_adapter.py" not in entries
    assert "tests/experiments/test_lean_task14_readiness.py" not in entries


def test_both_startup_scripts_expose_full_mode_and_share_manifest() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )

    assert "[switch] $Full" in powershell
    assert "--full" in bash
    assert "verification/fast-tests.txt" in runner


def test_startup_harness_routes_to_current_governance_not_historical_draft() -> None:
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )

    assert '"Doc/repository-governance.md"' in runner
    assert (
        '"Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md"'
        not in runner
    )


def test_startup_scripts_use_one_conda_python_verification_entry() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")

    assert powershell.count("conda run") == 1
    assert bash.count('run -n "$CONDA_ENV" python') == 1
    assert "verification/run_verification.py" in powershell
    assert "verification/run_verification.py" in bash


def test_profiles_expose_lean_audit_and_real_canary_manifest() -> None:
    powershell = (ROOT / "init.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "init.sh").read_text(encoding="utf-8")
    runner = (ROOT / "verification" / "run_verification.py").read_text(
        encoding="utf-8"
    )
    canaries = ROOT / "verification" / "lean-canary-tests.txt"

    assert "[switch] $LeanAudit" in powershell
    assert "[switch] $ForceAllLeanAudit" in powershell
    assert "--lean-audit" in bash
    assert "--force-all-lean-audit" in bash
    assert "verification/lean-canary-tests.txt" in runner
    assert '"--only-lean-canary"' in runner
    assert canaries.is_file()
    assert any(
        line.strip() and not line.lstrip().startswith("#")
        for line in canaries.read_text(encoding="utf-8").splitlines()
    )


def test_context_budget_rejects_oversized_tier1_file(tmp_path: Path) -> None:
    for relative_path in run_verification.TIER1_CONTEXT_BUDGET_BYTES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    oversized_path = tmp_path / "feature_list.json"
    oversized_path.write_bytes(
        b"x" * (run_verification.TIER1_CONTEXT_BUDGET_BYTES["feature_list.json"] + 1)
    )

    with pytest.raises(SystemExit, match="context budget"):
        run_verification._verify_context_budget(tmp_path)


def test_context_budget_rejects_oversized_tier1_total(tmp_path: Path) -> None:
    sizes = {
        "AGENTS.md": 15 * 1024,
        "feature_list.json": 25 * 1024,
        "progress.md": 25 * 1024,
        "session-handoff.md": 20 * 1024,
        "Doc/agent-navigation.md": 15 * 1024,
    }
    assert all(
        size <= run_verification.TIER1_CONTEXT_BUDGET_BYTES[relative_path]
        for relative_path, size in sizes.items()
    )
    assert sum(sizes.values()) > run_verification.TIER1_TOTAL_CONTEXT_BUDGET_BYTES
    for relative_path, size in sizes.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    with pytest.raises(SystemExit, match="total context budget"):
        run_verification._verify_context_budget(tmp_path)


def test_feature_state_rejects_task_named_top_level_history(tmp_path: Path) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": [
            {
                "id": "feat-011",
                "name": "Paper Real AI Experiments",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Current feature.",
                "acceptance": [],
                "evidence": [],
                "next_action": "Continue.",
            }
        ],
        "feat011_task_history_2026_07_30": {"status": "done"},
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match="unsupported top-level fields"):
        run_verification._verify_feature_state(path)


def test_feature_state_requires_exactly_one_matching_active_feature(
    tmp_path: Path,
) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": [
            {
                "id": "feat-010",
                "name": "Replay and Audit",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Deferred work.",
                "acceptance": [],
                "evidence": [],
                "next_action": "None.",
            },
            {
                "id": "feat-011",
                "name": "Paper Real AI Experiments",
                "status": "in-progress",
                "dependencies": ["feat-009"],
                "summary": "Current feature.",
                "acceptance": [],
                "evidence": [],
                "next_action": "Continue.",
            },
        ],
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match="exactly one in-progress"):
        run_verification._verify_feature_state(path)


@pytest.mark.parametrize(
    ("features", "message"),
    [
        (["not-a-record"], "feature records must be objects"),
        (
            [
                {
                    "id": "feat-011",
                    "name": "Paper Real AI Experiments",
                    "status": "in-progress",
                    "dependencies": [],
                    "summary": "Current feature.",
                    "acceptance": [],
                    "evidence": [],
                    "next_action": "Continue.",
                },
                {
                    "id": "feat-011",
                    "name": "Duplicate",
                    "status": "done",
                    "dependencies": [],
                    "summary": "Duplicate feature.",
                    "acceptance": [],
                    "evidence": [],
                    "next_action": "None.",
                },
            ],
            "duplicate feature ids",
        ),
        (
            [
                {
                    "id": "feat-011",
                    "status": "in-progress",
                }
            ],
            "missing required fields",
        ),
    ],
)
def test_feature_state_rejects_malformed_or_duplicate_records(
    tmp_path: Path,
    features: list[object],
    message: str,
) -> None:
    body = {
        "schema_version": "tokenshare.feature_list.v2",
        "project": "TokenShare",
        "stage": "paper-experiments",
        "active_feature": "feat-011",
        "active_focus": {"summary": "context consolidation"},
        "authorities": {},
        "features": features,
    }
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        run_verification._verify_feature_state(path)


def test_compile_repository_only_scans_code_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for relative_path in ("src", "tests", "verification"):
        (tmp_path / relative_path).mkdir()
    observed: list[Path] = []

    def capture(path: Path, **_kwargs: object) -> bool:
        observed.append(Path(path))
        return True

    monkeypatch.setattr(run_verification, "ROOT", tmp_path)
    monkeypatch.setattr(run_verification.compileall, "compile_dir", capture)

    run_verification._compile_repository()

    assert observed == [
        tmp_path / "src",
        tmp_path / "tests",
        tmp_path / "verification",
    ]

