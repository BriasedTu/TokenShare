"""Verify the published official result files without rewriting them."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence


RUN_ID = "slim-v2-full-flash-20260823-233000-b4c8e951"
MANIFEST_PATH = f"results/experiments/{RUN_ID}/manifest.json"
RESULT_ROOT = f"results/experiments/{RUN_ID}"
METRIC_RELATIVE_PATHS = (
    "metrics/summary.json",
    "metrics/tables/exp1.csv",
    "metrics/tables/exp1.jsonl",
    "metrics/tables/exp2.csv",
    "metrics/tables/exp2.jsonl",
    "metrics/tables/exp3.csv",
    "metrics/tables/exp3.jsonl",
    "metrics/tables/exp4.csv",
    "metrics/tables/exp4.jsonl",
    "metrics/tables/exp5.csv",
    "metrics/tables/exp5.jsonl",
)


JsonObject = dict[str, Any]


def verify_all(
    repo_root: Path,
    *,
    raw_root: Path | None = None,
    require_raw: bool = False,
    verify_worktree_index: bool = True,
) -> JsonObject:
    repo_root = repo_root.resolve()
    manifest = _read_json(repo_root / MANIFEST_PATH)
    expected = _expected_files(manifest)
    result_root = repo_root / RESULT_ROOT
    published_summary = _verify_published_files(result_root, expected)
    index_summary = (
        _verify_worktree_index(repo_root, result_root, expected)
        if verify_worktree_index
        else None
    )
    raw_summary: JsonObject | None = None
    if raw_root is not None or require_raw:
        if raw_root is None:
            raise ValueError("--require-raw needs --raw-root")
        raw_root = raw_root.resolve()
        if not raw_root.is_dir():
            if require_raw:
                raise ValueError(f"raw root is unavailable: {raw_root}")
            raw_summary = {"status": "skipped", "reason": "raw_root_unavailable"}
        else:
            raw_summary = _verify_raw_and_reducer(raw_root, result_root, expected)

    summary: JsonObject = {
        "status": "ok",
        "run_id": RUN_ID,
        "published_files": published_summary,
    }
    if index_summary is not None:
        summary["worktree_index"] = index_summary
    if raw_summary is not None:
        summary["raw"] = raw_summary
    return summary


def _read_json(path: Path) -> JsonObject:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _expected_files(manifest: Mapping[str, Any]) -> dict[str, JsonObject]:
    if manifest.get("schema_version") != (
        "tokenshare.experiments.official_results_manifest.v1"
    ):
        raise ValueError("unsupported official results manifest schema")
    if manifest.get("run_id") != RUN_ID:
        raise ValueError("official results run_id mismatch")
    published = manifest.get("published_files")
    if not isinstance(published, list) or len(published) != 12:
        raise ValueError("official results manifest must list exactly 12 files")
    expected: dict[str, JsonObject] = {}
    for item in published:
        if not isinstance(item, dict):
            raise ValueError("published file entry must be an object")
        relative_path = item.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("published file entry has invalid relative_path")
        if relative_path in expected:
            raise ValueError(f"duplicate official result path: {relative_path}")
        if "\\" in relative_path or relative_path.startswith("../"):
            raise ValueError(f"unsafe official result path: {relative_path}")
        expected[relative_path] = dict(item)
    required = {"run.json", *METRIC_RELATIVE_PATHS}
    if set(expected) != required:
        raise ValueError("official result file set mismatch")
    return expected


def _verify_published_files(
    result_root: Path,
    expected: Mapping[str, Mapping[str, Any]],
) -> JsonObject:
    checked = 0
    rows: dict[str, int | None] = {}
    for relative_path, facts in expected.items():
        data = _read_bytes(result_root / relative_path)
        _verify_bytes(relative_path, data, facts)
        actual_rows = _count_rows(result_root / relative_path)
        if actual_rows != facts.get("data_rows"):
            raise ValueError(f"row count mismatch: {relative_path}")
        rows[relative_path] = actual_rows
        checked += 1
    return {"file_count": checked, "rows": rows}


def _verify_worktree_index(
    repo_root: Path,
    result_root: Path,
    expected: Mapping[str, Mapping[str, Any]],
) -> JsonObject:
    checked = 0
    for relative_path, facts in expected.items():
        target_path = f"{RESULT_ROOT}/{relative_path}"
        data = _read_bytes(result_root / relative_path)
        _verify_bytes(relative_path, data, facts)
        blob = _git_index_blob(repo_root, target_path)
        _verify_bytes(relative_path, blob, facts)
        _verify_attributes(repo_root, target_path, cached=False)
        _verify_attributes(repo_root, target_path, cached=True)
        checked += 1
    return {"file_count": checked, "working_matches": checked, "index_matches": checked}


def _verify_raw_and_reducer(
    raw_root: Path,
    result_root: Path,
    expected: Mapping[str, Mapping[str, Any]],
) -> JsonObject:
    for relative_path, facts in expected.items():
        data = _read_bytes(raw_root / relative_path)
        _verify_bytes(relative_path, data, facts)
        if data != _read_bytes(result_root / relative_path):
            raise ValueError(f"published result differs from raw: {relative_path}")
    captured = _capture_reducer_payloads(raw_root)
    expected_metrics = set(METRIC_RELATIVE_PATHS)
    if set(captured) != expected_metrics:
        raise ValueError(
            "reducer payload set mismatch: "
            + json.dumps(sorted(captured), ensure_ascii=False)
        )
    for relative_path in sorted(expected_metrics):
        data = captured[relative_path]
        _verify_bytes(relative_path, data, expected[relative_path])
        if data != _read_bytes(result_root / relative_path):
            raise ValueError(f"reducer payload differs from published file: {relative_path}")
    return {
        "status": "ok",
        "raw_files_checked": len(expected),
        "reducer_payloads_checked": len(captured),
    }


def _capture_reducer_payloads(raw_root: Path) -> dict[str, bytes]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from tokenshare.experiments import reducer

    captured: dict[str, bytes] = {}
    original_stage_payloads = reducer._stage_payloads

    def capture(payloads: Mapping[Path, str], staged: dict[Path, Path]) -> None:
        for target, content in payloads.items():
            relative_path = target.relative_to(raw_root).as_posix()
            if relative_path in captured:
                raise ValueError(f"duplicate reducer payload: {relative_path}")
            captured[relative_path] = content.encode("utf-8")

    try:
        reducer._stage_payloads = capture
        reducer._reduce_run_staged(raw_root, {})
    finally:
        reducer._stage_payloads = original_stage_payloads
    return captured


def _read_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"missing official result file: {path}")
    return path.read_bytes()


def _verify_bytes(
    relative_path: str,
    data: bytes,
    facts: Mapping[str, Any],
) -> None:
    expected_sha = str(facts.get("sha256"))
    expected_size = int(facts.get("bytes"))
    if len(data) != expected_size:
        raise ValueError(f"byte size mismatch: {relative_path}")
    if sha256(data).hexdigest() != expected_sha:
        raise ValueError(f"sha256 mismatch: {relative_path}")


def _count_rows(path: Path) -> int | None:
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = sum(1 for _row in csv.reader(stream))
        return max(rows - 1, 0)
    if path.suffix == ".jsonl":
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
    return None


def _git_index_blob(repo_root: Path, target_path: str) -> bytes:
    listing = _git(repo_root, "ls-files", "-s", "--", target_path).strip()
    if not listing:
        raise ValueError(f"official result is not in the Git index: {target_path}")
    if "\n" in listing:
        raise ValueError(f"ambiguous index entry for official result: {target_path}")
    blob_sha = listing.split()[1]
    return subprocess.check_output(["git", "cat-file", "blob", blob_sha], cwd=repo_root)


def _verify_attributes(repo_root: Path, target_path: str, *, cached: bool) -> None:
    command = ["check-attr"]
    if cached:
        command.append("--cached")
    command.extend(["text", "eol", "--", target_path])
    output = _git(repo_root, *command)
    observed: dict[str, str] = {}
    for line in output.splitlines():
        _path, attribute, value = line.rsplit(": ", 2)
        observed[attribute] = value
    if observed != {"text": "unset", "eol": "unspecified"}:
        scope = "cached" if cached else "working"
        raise ValueError(f"{scope} attributes mismatch for {target_path}: {observed}")


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo_root,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--require-raw", action="store_true")
    parser.add_argument(
        "--verify-worktree-index",
        action="store_true",
        help=(
            "Compatibility no-op: working/index result verification is "
            "the default public gate."
        ),
    )
    parser.add_argument(
        "--skip-worktree-index",
        action="store_true",
        help="Only verify working result files; intended for pre-staging diagnostics.",
    )
    args = parser.parse_args(argv)
    summary = verify_all(
        Path(__file__).resolve().parents[1],
        raw_root=args.raw_root,
        require_raw=args.require_raw,
        verify_worktree_index=not args.skip_worktree_index,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
