"""Focused verification entry points for the public TokenShare extraction."""

from __future__ import annotations

import argparse
import compileall
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
VERIFICATION = ROOT / "verification"
FAST_TEST_MANIFEST = VERIFICATION / "fast-tests.txt"
FROZEN_SHA = "bb5e637785afb6bd5743e4d89af4c02ab0736204"

SYSTEM_SELECTORS = (
    "tests/core",
    "tests/storage",
    "tests/local_runtime",
    "tests/plugins/factorization",
    "tests/plugins/lean_proof",
    "tests/plugins/test_plugin_registry.py",
    "tests/executors",
    "tests/verification",
)
EXPERIMENT_SELECTORS = ("tests/experiments",)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--focused",
        choices=("fast", "system", "experiments", "corpus", "results", "extraction"),
        default="fast",
    )
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--require-raw", action="store_true")
    parser.add_argument(
        "--run-lean-integration", action="store_true",
        help="Explicitly enable real Lean compilation tests; requires a prepared local cache.",
    )
    args = parser.parse_args(argv)
    if args.run_lean_integration and args.focused not in {"fast", "system", "experiments"}:
        parser.error("--run-lean-integration requires a pytest-focused verification mode")

    _prepare_imports()
    _verify_python_runtime()
    _compile_python()
    if args.focused == "fast":
        _run_pytest(_manifest_entries(FAST_TEST_MANIFEST), run_lean_integration=args.run_lean_integration)
    elif args.focused == "system":
        _run_pytest(SYSTEM_SELECTORS, run_lean_integration=args.run_lean_integration)
    elif args.focused == "experiments":
        _run_corpus()
        _run_plan()
        _run_pytest(EXPERIMENT_SELECTORS, run_lean_integration=args.run_lean_integration)
    elif args.focused == "corpus":
        _run_corpus()
    elif args.focused == "results":
        _run_results(raw_root=args.raw_root, require_raw=args.require_raw)
    elif args.focused == "extraction":
        _run_extraction()
    print(
        json.dumps(
            {"status": "ok", "focused": args.focused, "run_lean_integration": args.run_lean_integration},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _prepare_imports() -> None:
    for path in (str(VERIFICATION), str(ROOT), str(SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)
    existing = os.environ.get("PYTHONPATH")
    values = [str(VERIFICATION), str(ROOT), str(SRC)]
    if existing:
        values.extend(item for item in existing.split(os.pathsep) if item)
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(values))


def _verify_python_runtime() -> None:
    json.dumps({"json": True})
    print("python-json-ok", flush=True)


def _compile_python() -> None:
    roots = (SRC, ROOT / "tests", VERIFICATION)
    ok = True
    for path in roots:
        if path.exists():
            ok = compileall.compile_dir(path, quiet=1) and ok
    if not ok:
        raise SystemExit("compileall failed")
    print("compileall-ok", flush=True)


def _manifest_entries(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise SystemExit(f"verification manifest missing: {path}")
    entries = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not entries:
        raise SystemExit(f"verification manifest is empty: {path}")
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    if duplicates:
        raise SystemExit(f"verification manifest has duplicates: {duplicates}")
    return entries


def _run_pytest(selectors: Sequence[str], *, run_lean_integration: bool = False) -> None:
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "verification.pytest_network_tripwire",
            *selectors,
            "-q",
            *(["--run-lean-integration"] if run_lean_integration else []),
        ],
        "pytest",
    )


def _run_corpus() -> None:
    _run([sys.executable, "verification/verify_authoritative_corpus.py"], "corpus")


def _run_plan() -> None:
    _run(
        [
            sys.executable,
            "-m",
            "tokenshare.experiments.cli",
            "plan",
            "--profile",
            "full",
            "--run-id",
            "verification-plan",
        ],
        "experiments-plan",
    )


def _run_results(*, raw_root: Path | None, require_raw: bool) -> None:
    command = [
        sys.executable,
        "verification/verify_official_results.py",
        "--verify-worktree-index",
    ]
    if raw_root is not None:
        command.extend(["--raw-root", str(raw_root)])
    if require_raw:
        command.append("--require-raw")
    _run(command, "official-results")


def _run_extraction() -> None:
    _run(
        [
            sys.executable,
            "verification/verify_extraction.py",
            "--frozen-sha",
            FROZEN_SHA,
        ],
        "extraction",
    )


def _run(command: Sequence[str], label: str) -> None:
    print(
        f"running-{label}: " + " ".join(_quote(part) for part in command),
        flush=True,
    )
    subprocess.run(command, cwd=ROOT, check=True)


def _quote(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        return repr(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
