"""Verify the clean extraction boundary and release identities."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


FORBIDDEN_TEXT_SCAN_EXEMPT_PATHS = {
    "verification/verify_extraction.py",
}

FORBIDDEN_TRACKED_PATHS = (
    "src/tokenshare/experiments/slim_v2",
    "tests/experiments/slim_v2",
    "run_slim_v2.cmd",
    "TokenShareData",
    "results",
    "paper",
    "RESULTS.md",
    "Doc/Experiments/factorization-analysis.md",
    "docs/superpowers",
)
PRIVATE_WORK_DOC_NAMES = {"agents.md", "claude.md", "codex.md"}
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
MACHINE_WORKTREE_PATH = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|/)[^\r\n\"']*worktrees?[\\/]"
)


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


def verify_all(repo_root: Path) -> JsonObject:
    repo_root = repo_root.resolve()
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "src"))
    boundary_summary = _verify_tracked_boundaries(repo_root)
    corpus_summary = _verify_corpus(repo_root)
    summary: JsonObject = {
        "status": "ok",
        "tracked_boundaries": boundary_summary,
        "corpus": corpus_summary,
    }
    return summary


def _verify_tracked_boundaries(repo_root: Path) -> JsonObject:
    tracked = _tracked_files(repo_root)
    private_docs = [path for path in tracked if Path(path).name.casefold() in PRIVATE_WORK_DOC_NAMES]
    if private_docs:
        raise ValueError(f"private work instructions are tracked: {private_docs[:5]}")
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
    for relative_path in tracked:
        if (
            relative_path in FORBIDDEN_TEXT_SCAN_EXEMPT_PATHS
        ):
            continue
        path = repo_root / relative_path
        if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Frozen miniF2F admission evidence records compiler paths and is
        # verified byte-for-byte by the authoritative corpus manifest.
        if (
            not relative_path.startswith("benchmarks/experiments/minif2f/")
            and MACHINE_WORKTREE_PATH.search(text)
        ):
            hits.append(f"{relative_path}: absolute source worktree path")
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                hits.append(f"{relative_path}: {pattern.pattern}")
    return hits


def _verify_corpus(repo_root: Path) -> JsonObject:
    from verification.verify_authoritative_corpus import verify_all as verify_corpus

    return verify_corpus(repo_root)


def _tracked_files(repo_root: Path) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
    )
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)
    summary = verify_all(Path(__file__).resolve().parents[1])
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
