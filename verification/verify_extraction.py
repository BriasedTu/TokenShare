"""Verify the clean extraction boundary and release identities."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_FROZEN_SHA = "bb5e637785afb6bd5743e4d89af4c02ab0736204"
MANIFEST_PATH = "verification/extraction_manifest.json"
FORBIDDEN_TEXT_SCAN_EXEMPT_PATHS = {
    MANIFEST_PATH,
    "verification/verify_extraction.py",
}

FORBIDDEN_TRACKED_PATHS = (
    "src/tokenshare/experiments/slim_v2",
    "tests/experiments/slim_v2",
    "run_slim_v2.cmd",
    "TokenShareData",
)
FORBIDDEN_TEXT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"PreparedTraceDelivery",
        r"TraceConsumption",
        r"ParentTrace",
        r"prepared_delivery",
        r"trace_delivery_stager",
        r"TRACE_DELIVERY_COMMITTED",
        r"external_response_bank_locator",
        r"save_external_trace_wrapper",
        r"ai_api_request_identity",
        r"PreparedOutboundRequestFactory",
        r"tests\.phase7_fixtures",
        r"executors\.ai_api import",
        r"trace_backed",
        r"paper_formal",
        r"tokenshare\.experiments\.slim_v2",
        r"tests\.experiments\.slim_v2",
    )
)
def _absolute_source_patterns() -> tuple[str, ...]:
    frozen_source_checkout = (
        Path("E:/")
        / "TokenEcnomic"
        / "TokenShareWorktrees"
        / "slim-v2-baseline"
    )
    return (str(frozen_source_checkout), frozen_source_checkout.as_posix())


TEXT_SUFFIXES = {
    "",
    ".cmd",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
}


JsonObject = dict[str, Any]


def verify_all(repo_root: Path, *, frozen_sha: str = DEFAULT_FROZEN_SHA) -> JsonObject:
    repo_root = repo_root.resolve()
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "src"))
    manifest = _read_manifest(repo_root)
    tag_summary = _verify_freeze_tag(repo_root, manifest, frozen_sha)
    boundary_summary = _verify_tracked_boundaries(repo_root)
    corpus_summary = _verify_corpus(repo_root, frozen_sha)
    result_summary = _verify_results(repo_root)
    summary: JsonObject = {
        "status": "ok",
        "freeze_tag": tag_summary,
        "tracked_boundaries": boundary_summary,
        "corpus": corpus_summary,
        "official_results": result_summary,
        "scope_corrections": len(manifest["approved_scope_corrections"]),
        "historical_string_allowlist": len(manifest["historical_string_allowlist"]),
    }
    return summary


def _read_manifest(repo_root: Path) -> JsonObject:
    manifest = json.loads((repo_root / MANIFEST_PATH).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("extraction manifest must be a JSON object")
    if manifest.get("schema_version") != (
        "tokenshare.experiments.clean_extraction_manifest.v1"
    ):
        raise ValueError("unsupported extraction manifest schema")
    if not isinstance(manifest.get("approved_scope_corrections"), list):
        raise ValueError("extraction manifest lacks approved scope corrections")
    if not isinstance(manifest.get("historical_string_allowlist"), list):
        raise ValueError("extraction manifest lacks historical string allowlist")
    return manifest


def _verify_freeze_tag(
    repo_root: Path,
    manifest: Mapping[str, Any],
    frozen_sha: str,
) -> JsonObject:
    tag_name = str(manifest["source_tag"])
    tag_ref = f"refs/tags/{tag_name}"
    tag_object = _git(repo_root, "rev-parse", tag_ref).strip()
    tag_type = _git(repo_root, "cat-file", "-t", tag_object).strip()
    peeled = _git(repo_root, "rev-parse", f"{tag_ref}^{{}}").strip()
    if tag_type != "tag":
        raise ValueError(f"freeze ref is not an annotated tag: {tag_name}")
    if tag_object != manifest["tag_object_sha"]:
        raise ValueError("freeze tag object SHA mismatch")
    if peeled != manifest["peeled_commit_sha"] or peeled != frozen_sha:
        raise ValueError("freeze tag peeled commit mismatch")
    parents = _git(repo_root, "rev-list", "--parents", "-n", "1", peeled).split()
    if len(parents) != 2 or parents[1] != manifest["pre_baseline_source_sha"]:
        raise ValueError("freeze baseline parent mismatch")
    _git(repo_root, "merge-base", "--is-ancestor", peeled, "HEAD")
    generated_head = str(manifest["generated_from_target_head"])
    _git(repo_root, "merge-base", "--is-ancestor", generated_head, "HEAD")
    return {
        "tag_object_sha": tag_object,
        "peeled_commit_sha": peeled,
        "pre_baseline_source_sha": parents[1],
    }


def _verify_tracked_boundaries(repo_root: Path) -> JsonObject:
    tracked = _tracked_files(repo_root)
    paper = [path for path in tracked if path == "paper" or path.startswith("paper/")]
    if paper:
        raise ValueError(f"top-level paper workspace is tracked: {paper[:5]}")
    for forbidden in FORBIDDEN_TRACKED_PATHS:
        matches = [
            path
            for path in tracked
            if path == forbidden or path.startswith(forbidden.rstrip("/") + "/")
        ]
        if matches:
            raise ValueError(f"forbidden tracked path present: {forbidden}")
    text_hits = _forbidden_text_hits(repo_root, tracked)
    if text_hits:
        raise ValueError("forbidden text present: " + "; ".join(text_hits[:10]))
    return {"tracked_files": len(tracked), "paper_files": 0, "forbidden_text_hits": 0}


def _forbidden_text_hits(repo_root: Path, tracked: Sequence[str]) -> list[str]:
    hits: list[str] = []
    absolute_source_patterns = _absolute_source_patterns()
    for relative_path in tracked:
        if (
            relative_path in FORBIDDEN_TEXT_SCAN_EXEMPT_PATHS
            or relative_path.startswith("results/experiments/")
        ):
            continue
        path = repo_root / relative_path
        if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for absolute_source in absolute_source_patterns:
            if absolute_source in text:
                hits.append(f"{relative_path}: absolute source worktree path")
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                hits.append(f"{relative_path}: {pattern.pattern}")
    return hits


def _verify_corpus(repo_root: Path, frozen_sha: str) -> JsonObject:
    from verification.verify_authoritative_corpus import verify_all as verify_corpus

    return verify_corpus(repo_root, frozen_sha=frozen_sha)


def _verify_results(repo_root: Path) -> JsonObject:
    from verification.verify_official_results import verify_all as verify_results

    return verify_results(repo_root, verify_worktree_index=True)


def _tracked_files(repo_root: Path) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
    )
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo_root,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-sha", default=DEFAULT_FROZEN_SHA)
    args = parser.parse_args(argv)
    summary = verify_all(Path(__file__).resolve().parents[1], frozen_sha=args.frozen_sha)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
