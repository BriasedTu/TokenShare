"""Single-process TokenShare startup verification profiles."""

from __future__ import annotations

import argparse
import compileall
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FAST_TEST_MANIFEST = ROOT / "verification/fast-tests.txt"
LEAN_CANARY_MANIFEST = ROOT / "verification/lean-canary-tests.txt"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument("--lean-audit", action="store_true")
    parser.add_argument("--force-all-lean-audit", action="store_true")
    parser.add_argument("--only-lean-canary", action="store_true")
    args = parser.parse_args(argv)
    if args.force_all_lean_audit:
        args.lean_audit = True

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(SRC))
    print("=== TokenShare Startup Verification ===", flush=True)
    print(f"Verification mode: {args.mode}", flush=True)
    _verify_python_runtime()
    _verify_harness_files()
    _verify_test_manifest(FAST_TEST_MANIFEST)
    _verify_test_manifest(LEAN_CANARY_MANIFEST, nodeids=True)
    _compile_repository()
    _run_pytest(args.mode, only_lean_canary=args.only_lean_canary)
    if args.lean_audit:
        _run_lean_audit(force_all=args.force_all_lean_audit)
    print("=== Verification Complete ===", flush=True)
    return 0


def _verify_python_runtime() -> None:
    json.dumps({"json": True})
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("SELECT 1").fetchone()
    finally:
        connection.close()
    print("python-json-sqlite-ok", flush=True)


def _verify_harness_files() -> None:
    required = (
        "AGENTS.md",
        "feature_list.json",
        "progress.md",
        "session-handoff.md",
        "Doc/agent-navigation.md",
        "Doc/TechnicalDocument/tokenshare_v1_complete_spec.md",
        "Doc/TechnicalDocument/tokenshare_v1_code_map.md",
        "Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md",
        "Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md",
    )
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"Missing required startup files: {missing}")
    data = json.loads((ROOT / "feature_list.json").read_text(encoding="utf-8"))
    features = data.get("features", [])
    if not features:
        raise SystemExit("feature_list.json has no features")
    if not any(feature.get("status") == "in-progress" for feature in features):
        raise SystemExit("feature_list.json should have one active in-progress feature")
    print("harness-files-ok", flush=True)


def _manifest_entries(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"Verification manifest not found: {path.relative_to(ROOT)}")
    entries = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not entries:
        raise SystemExit(f"Verification manifest is empty: {path.relative_to(ROOT)}")
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    if duplicates:
        raise SystemExit(f"Verification manifest contains duplicates: {duplicates}")
    return entries


def _verify_test_manifest(path: Path, *, nodeids: bool = False) -> None:
    entries = _manifest_entries(path)
    missing: list[str] = []
    for entry in entries:
        file_part = entry.split("::", 1)[0] if nodeids else entry
        if not (ROOT / file_part).exists():
            missing.append(file_part)
    if missing:
        raise SystemExit(f"Verification manifest contains missing paths: {missing}")


def _compile_repository() -> None:
    excluded = re.compile(r"(?:^|[\\/])reference_repos(?:[\\/]|$)")
    if not compileall.compile_dir(ROOT, quiet=1, rx=excluded):
        raise SystemExit("compileall failed")
    print("compileall-ok", flush=True)


def _run_pytest(mode: str, *, only_lean_canary: bool) -> None:
    import pytest

    if not (ROOT / "tests").is_dir():
        print("No tests/ directory; pytest skipped", flush=True)
        return
    if only_lean_canary:
        args = ["-q", *_manifest_entries(LEAN_CANARY_MANIFEST)]
    elif mode == "full":
        args = ["tests"]
    else:
        args = ["-q", *_manifest_entries(FAST_TEST_MANIFEST)]
    exit_code = pytest.main(args)
    if exit_code != pytest.ExitCode.OK:
        raise SystemExit(int(exit_code))


def _run_lean_audit(*, force_all: bool) -> None:
    from tokenshare.experiments.lean_catalog_audit import _run_default_catalog_audit

    result = _run_default_catalog_audit(refresh=False, force_all=force_all)
    print(
        "lean-audit="
        + json.dumps(result.summary(), ensure_ascii=False, sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
