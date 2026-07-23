from __future__ import annotations

from pathlib import Path


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

