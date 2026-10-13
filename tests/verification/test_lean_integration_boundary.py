"""The default verification suite must never need a Lean installation."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from verification import run_verification


def test_real_lean_tests_require_explicit_opt_in(tmp_path: Path) -> None:
    sample = tmp_path / "test_sample.py"
    sample.write_text(
        "import os\nimport pytest\n"
        "def test_contract():\n    assert 1 + 1 == 2\n"
        "@pytest.mark.lean_integration\n"
        "def test_explicit_integration():\n"
        "    assert os.environ['TOKENSHARE_PYTEST_LEAN_GUARD_ACTIVE'] == '0'\n",
        encoding="utf-8",
    )
    command = [
        sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
        "-p", "verification.pytest_lean_integration",
        "--confcutdir", str(tmp_path), str(sample), "-q",
    ]
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    default = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    assert default.returncode == 0, default.stdout + default.stderr
    assert "1 passed, 1 skipped" in default.stdout
    explicit = subprocess.run(
        [*command, "--run-lean-integration"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert "2 passed" in explicit.stdout


@pytest.mark.parametrize("name", ["lean.exe", "lake", "elan.exe"])
def test_unmarked_compiler_process_is_blocked_before_launch(tmp_path: Path, name: str, monkeypatch) -> None:
    monkeypatch.setenv("TOKENSHARE_PYTEST_LEAN_GUARD_ACTIVE", "1")
    # These paths do not exist, even on a machine with Lean installed.
    with pytest.raises(RuntimeError, match="Lean integration requires --run-lean-integration"):
        subprocess.run([str(tmp_path / name), "--version"], check=True)


def test_python_child_inherits_the_compiler_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOKENSHARE_PYTEST_LEAN_GUARD_ACTIVE", "1")
    source = (
        "import subprocess\n"
        "try:\n"
        f"    subprocess.run([{str(tmp_path / 'lean.exe')!r}, '--version'])\n"
        "except RuntimeError as exc:\n"
        "    assert 'Lean integration requires --run-lean-integration' in str(exc)\n"
        "    print('child-lean-blocked')\n"
        "else:\n"
        "    raise AssertionError('compiler guard was not inherited')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "child-lean-blocked"


def test_focused_verification_forwards_only_explicit_opt_in(monkeypatch) -> None:
    commands = []
    monkeypatch.setattr(run_verification, "_run", lambda command, label: commands.append(command))
    run_verification._run_pytest(["tests/plugins/lean_proof"])
    run_verification._run_pytest(["tests/plugins/lean_proof"], run_lean_integration=True)
    assert "--run-lean-integration" not in commands[0]
    assert "--run-lean-integration" in commands[1]
