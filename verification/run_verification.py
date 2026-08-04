"""Single-process TokenShare startup verification profiles."""

from __future__ import annotations

import argparse
import compileall
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from typing import Sequence

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FAST_TEST_MANIFEST = ROOT / "verification/fast-tests.txt"
LEAN_CANARY_MANIFEST = ROOT / "verification/lean-canary-tests.txt"
PROFILE_DIR = ROOT / "verification/profiles"
PROFILE_NAMES = (
    "paper-l1-components",
    "paper-l2-historical-real",
    "paper-l3-new-real-smoke-audit",
    "paper-l4-cell-lineage",
)
PROFILE_MANIFESTS = {
    name: PROFILE_DIR / f"{name}.txt" for name in PROFILE_NAMES
}
ARTIFACT_PROFILES = frozenset(PROFILE_NAMES[2:])
_EXACT_NODEID = re.compile(
    r"^tests/[A-Za-z0-9_./-]+\.py::test_[^\s:]+(?:\[[^\]\r\n]+\])?$"
)
TIER1_CONTEXT_BUDGET_BYTES = {
    "AGENTS.md": 16 * 1024,
    "feature_list.json": 32 * 1024,
    "progress.md": 32 * 1024,
    "session-handoff.md": 32 * 1024,
    "Doc/agent-navigation.md": 24 * 1024,
}
TIER1_TOTAL_CONTEXT_BUDGET_BYTES = 96 * 1024
FEATURE_LIST_SCHEMA_VERSION = "tokenshare.feature_list.v2"
FEATURE_LIST_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "project",
        "stage",
        "active_feature",
        "active_focus",
        "authorities",
        "features",
    }
)
FEATURE_RECORD_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "name",
        "status",
        "dependencies",
        "summary",
        "acceptance",
        "evidence",
        "next_action",
    }
)
COMPILE_ROOTS = ("src", "tests", "verification")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument("--lean-audit", action="store_true")
    parser.add_argument("--force-all-lean-audit", action="store_true")
    parser.add_argument("--only-lean-canary", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args(argv)
    if args.force_all_lean_audit:
        args.lean_audit = True

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(SRC))
    print("=== TokenShare Startup Verification ===", flush=True)
    if args.profile is not None:
        if args.mode != "fast" or args.lean_audit or args.only_lean_canary:
            raise SystemExit("Focused profiles cannot be combined with Full/LeanAudit")
        entries, manifest_digest = _load_profile_manifest(args.profile)
        artifact_root = (
            None
            if args.artifact_root is None
            else _validate_artifact_root(args.artifact_root)
        )
        audit = _audit_profile_artifacts(args.profile, artifact_root)
        summary = {
            "profile": args.profile,
            "manifest_digest": manifest_digest,
            "nodeid_count": len(entries),
            **audit,
        }
        if audit["status"] != "passed":
            print(
                "verification-profile="
                + json.dumps(summary, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            return 3
        _verify_python_runtime()
        _verify_harness_files()
        _compile_repository()
        _run_profile_tests(entries)
        print(
            "verification-profile="
            + json.dumps(summary, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        print("=== Verification Complete ===", flush=True)
        return 0
    if args.artifact_root is not None:
        raise SystemExit("--artifact-root requires --profile")
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
        "Doc/repository-governance.md",
        "Doc/TechnicalDocument/tokenshare_v1_complete_spec.md",
        "Doc/TechnicalDocument/tokenshare_v1_code_map.md",
        "Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md",
    )
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"Missing required startup files: {missing}")
    _verify_context_budget(ROOT)
    _verify_feature_state(ROOT / "feature_list.json")
    print("harness-files-ok", flush=True)


def _verify_context_budget(root: Path) -> None:
    sizes: dict[str, int] = {}
    for relative_path, limit in TIER1_CONTEXT_BUDGET_BYTES.items():
        path = root / relative_path
        if not path.is_file():
            raise SystemExit(f"Missing Tier 1 context file: {relative_path}")
        size = path.stat().st_size
        sizes[relative_path] = size
        if size > limit:
            raise SystemExit(
                "Tier 1 context budget exceeded: "
                f"{relative_path} uses {size} bytes, limit is {limit}"
            )
    total = sum(sizes.values())
    if total > TIER1_TOTAL_CONTEXT_BUDGET_BYTES:
        raise SystemExit(
            "Tier 1 total context budget exceeded: "
            f"{total} bytes, limit is {TIER1_TOTAL_CONTEXT_BUDGET_BYTES}"
        )


def _verify_feature_state(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != FEATURE_LIST_SCHEMA_VERSION:
        raise SystemExit(
            "feature_list.json must use schema " + FEATURE_LIST_SCHEMA_VERSION
        )
    unsupported = sorted(set(data) - FEATURE_LIST_TOP_LEVEL_FIELDS)
    if unsupported:
        raise SystemExit(
            "feature_list.json has unsupported top-level fields: " + str(unsupported)
        )
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise SystemExit("feature_list.json has no features")
    if any(not isinstance(feature, dict) for feature in features):
        raise SystemExit("feature_list.json feature records must be objects")
    for feature in features:
        missing = sorted(FEATURE_RECORD_REQUIRED_FIELDS - set(feature))
        if missing:
            raise SystemExit(
                "feature_list.json feature record is missing required fields: "
                + str(missing)
            )
        if not isinstance(feature.get("id"), str) or not feature["id"]:
            raise SystemExit("feature_list.json feature ids must be non-empty strings")
    feature_ids = [feature["id"] for feature in features]
    duplicates = sorted(
        {feature_id for feature_id in feature_ids if feature_ids.count(feature_id) > 1}
    )
    if duplicates:
        raise SystemExit(
            "feature_list.json has duplicate feature ids: " + str(duplicates)
        )
    in_progress = [
        feature
        for feature in features
        if feature.get("status") == "in-progress"
    ]
    if len(in_progress) != 1:
        raise SystemExit("feature_list.json must have exactly one in-progress feature")
    active_feature = data.get("active_feature")
    if in_progress[0].get("id") != active_feature:
        raise SystemExit(
            "feature_list.json active_feature must match the in-progress feature"
        )


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


def _profile_manifest_path(name: str) -> Path:
    path = PROFILE_MANIFESTS.get(name)
    if path is None:
        raise SystemExit(f"Unknown verification profile: {name}")
    resolved = path.resolve(strict=False)
    if PROFILE_DIR.resolve(strict=False) not in resolved.parents:
        raise SystemExit("Verification profile resolves outside profile directory")
    return resolved


def _load_profile_manifest(name: str) -> tuple[list[str], str]:
    path = _profile_manifest_path(name)
    if not path.is_file():
        raise SystemExit(f"Verification manifest not found: {path.relative_to(ROOT)}")
    raw = path.read_bytes()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SystemExit("Verification profile manifest must be UTF-8") from error
    entries = [line.strip() for line in lines if line.strip()]
    if not entries:
        raise SystemExit(f"Verification manifest is empty: {path.relative_to(ROOT)}")
    if any(line != line.strip() or not _EXACT_NODEID.fullmatch(line) for line in lines if line):
        raise SystemExit("Verification profile must contain exact pytest nodeids only")
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    if duplicates:
        raise SystemExit(f"Verification manifest contains duplicates: {duplicates}")
    tracked = _tracked_test_paths()
    for entry in entries:
        test_path = entry.split("::", 1)[0]
        if test_path not in tracked or not (ROOT / test_path).is_file():
            raise SystemExit(
                f"Verification profile references an untracked test: {test_path}"
            )
    canonical = ("\n".join(entries) + "\n").encode("utf-8")
    return entries, "sha256:" + sha256(canonical).hexdigest()


def _tracked_test_paths() -> frozenset[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "tests"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit("Unable to verify tracked profile tests")
    return frozenset(line.strip().replace("\\", "/") for line in result.stdout.splitlines())


def _validate_artifact_root(value: Path) -> Path:
    repository = ROOT.resolve(strict=False)
    resolved = (
        value if value.is_absolute() else repository / value
    ).resolve(strict=False)
    if repository not in resolved.parents:
        raise SystemExit("Artifact root is outside the repository")
    allowed_roots = (
        repository / "local" / "verification",
        repository / "outputs",
    )
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise SystemExit("Artifact root is outside allowed repository runtime roots")
    if not resolved.is_dir():
        raise SystemExit(f"Artifact root is not an existing directory: {resolved}")
    return resolved


def _audit_profile_artifacts(name: str, artifact_root: Path | None) -> dict[str, object]:
    if name not in ARTIFACT_PROFILES:
        return {
            "status": "passed",
            "audit_mode": "offline_components",
            "artifact_root_supplied": artifact_root is not None,
        }
    if artifact_root is None:
        return {"status": "blocked", "reason": "artifact_root_required"}
    l3 = _audit_terminal_real_output(artifact_root)
    if l3.get("status") != "passed" or name == "paper-l3-new-real-smoke-audit":
        return l3
    try:
        first, second = _recompute_l4_digests(artifact_root)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return {
            "status": "blocked",
            "reason": "l4_recomputation_failed",
            "detail": str(error),
        }
    if first != second:
        return {
            "status": "blocked",
            "reason": "l4_recomputations_differ",
            "recomputation_digests": [first, second],
        }
    return {
        "status": "passed",
        "terminal_status": l3["terminal_status"],
        "recomputation_digests": [first, second],
    }


def _audit_terminal_real_output(artifact_root: Path) -> dict[str, object]:
    from tokenshare.experiments.paper_formal_runner import replay_paper_formal_suite

    try:
        result = replay_paper_formal_suite(output_root=artifact_root)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        return {
            "status": "blocked",
            "reason": "terminal_real_output_invalid",
            "detail": str(error),
        }
    try:
        suite_manifest = json.loads(
            (artifact_root / "suite_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            "status": "blocked",
            "reason": "terminal_real_output_invalid",
            "detail": str(error),
        }
    terminal_status = str(getattr(result.status, "value", result.status))
    if (
        not isinstance(suite_manifest, dict)
        or suite_manifest.get("capturing") is not False
        or terminal_status in {"planned", "running", "incomplete"}
        or not result.ended_at
        or result.provider_attempt_count <= 0
    ):
        return {
            "status": "blocked",
            "reason": "terminal_real_output_required",
            "terminal_status": terminal_status,
        }
    return {
        "status": "passed",
        "terminal_status": terminal_status,
        "provider_attempt_count": result.provider_attempt_count,
        "audit_mode": "stored_evidence_only",
    }


def _recompute_l4_digests(artifact_root: Path) -> tuple[str, str]:
    from tokenshare.experiments.paper_formal_runner import (
        load_paper_traceability_replay_input_root,
        recompute_paper_traceability_replay,
    )

    digests: list[str] = []
    for label in ("first", "second"):
        protected = load_paper_traceability_replay_input_root(artifact_root)
        with tempfile.TemporaryDirectory(prefix=f"tokenshare-l4-{label}-") as directory:
            result = recompute_paper_traceability_replay(
                output_root=Path(directory) / "audit",
                replay_input_root=protected,
            )
            if result.provider_calls != 0 or result.source_write_count != 0:
                raise RuntimeError("L4 replay violated read-only provider/source boundary")
            body = [
                result.observations_digest,
                result.tables_digest,
                result.cell_lineage_digest,
            ]
            digests.append(
                "sha256:"
                + sha256(
                    json.dumps(body, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
            )
    return digests[0], digests[1]


def _run_profile_tests(entries: Sequence[str]) -> None:
    args = ["-q", "-p", "verification.pytest_network_tripwire", *entries]
    exit_code = pytest.main(args)
    if exit_code != pytest.ExitCode.OK:
        raise SystemExit(int(exit_code))


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
    excluded = re.compile(r"(?:^|[\\/])(?:__pycache__|\.pytest_cache)(?:[\\/]|$)")
    for relative_path in COMPILE_ROOTS:
        path = ROOT / relative_path
        if path.is_dir() and not compileall.compile_dir(path, quiet=1, rx=excluded):
            raise SystemExit(f"compileall failed: {relative_path}")
    print("compileall-ok", flush=True)


def _run_pytest(mode: str, *, only_lean_canary: bool) -> None:
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
