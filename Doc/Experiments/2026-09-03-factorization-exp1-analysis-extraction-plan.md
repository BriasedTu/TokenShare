# Experiment 1 Factorization Analysis Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one standalone read-only Python extractor that converts the existing formal Experiment 1 Factorization facts into documented root and actual-attempt CSV tables and validates them against the published reducer totals.

**Architecture:** The extractor accepts explicit catalog, raw-run, and output paths. Pure helper functions implement only the frozen mathematical and nullable aggregation rules; one orchestration function joins persisted JSON/JSONL facts, writes two flat CSVs plus metadata and a validation report, and never imports or invokes the experiment runner or provider. The public script is copied byte-for-byte to the Slim V2 source worktree.

**Tech Stack:** Python 3 standard library (`argparse`, `csv`, `datetime`, `hashlib`, `json`, `math`, `pathlib`, `statistics`, `urllib.parse`), pytest for focused derivation tests, and the bundled spreadsheet runtime for a final CSV import/inspection check.

---

### Task 1: Specify the frozen derivations

**Files:**
- Create: `tests/experiments/test_factorization_exp1_analysis.py`
- Create: `src/tokenshare/experiments/factorization_exp1_analysis.py`

- [x] **Step 1: Add focused failing tests**

```python
from tokenshare.experiments.factorization_exp1_analysis import (
    assign_input_scale_groups,
    attempt_failure_type,
    nullable_sum,
    partition_ranges,
)


def test_partition_ranges_puts_remainder_in_earliest_ranges():
    assert partition_ranges(2, 11, 3) == ((2, 5), (6, 8), (9, 11))


def test_nullable_sum_preserves_missing_usage():
    assert nullable_sum([]) == 0
    assert nullable_sum([4, 5]) == 9
    assert nullable_sum([4, None]) is None


def test_attempt_failure_type_uses_documented_precedence():
    assert attempt_failure_type("provider_failed", None, None) == "provider_failure"
    assert attempt_failure_type("parse_rejected", "rejected", None) == "parse_failure"
    assert attempt_failure_type("parsed", "parsed", "rejected") == "verification_rejection"
    assert attempt_failure_type("parsed", "parsed", "passed") is None


def test_input_scale_groups_are_deterministic_ntiles():
    rows = [
        {"case_id": f"c{i:03d}", "partition_count": 8, "is_prime": False,
         "candidate_domain_size": i, "target_n": i * i}
        for i in range(1, 95)
    ]
    assign_input_scale_groups(rows)
    counts = {name: sum(r["input_scale_group"] == name for r in rows)
              for name in ("small_M", "middle_M", "large_M")}
    assert counts == {"small_M": 32, "middle_M": 31, "large_M": 31}
    assert [r["input_scale_rank"] for r in rows] == list(range(1, 95))
```

- [x] **Step 2: Run the test and confirm RED**

Run:

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
python -m pytest tests/experiments/test_factorization_exp1_analysis.py -q
```

Expected: collection fails because `factorization_exp1_analysis` does not exist.

- [x] **Step 3: Implement the four pure helpers and rerun GREEN**

The production module must expose exactly these tested signatures:

```python
def partition_ranges(start: int, end: int, count: int) -> tuple[tuple[int, int], ...]:
    domain_size = end - start + 1
    base_size, extra = divmod(domain_size, count)
    ranges = []
    next_start = start
    for index in range(count):
        width = base_size + int(index < extra)
        ranges.append((next_start, next_start + width - 1))
        next_start += width
    return tuple(ranges)


def nullable_sum(values: Iterable[int | float | None]) -> int | float | None:
    materialized = tuple(values)
    return None if any(value is None for value in materialized) else sum(materialized)


def attempt_failure_type(result_kind: str | None, parse_result: str | None,
                         verifier_result: str | None) -> str | None:
    if result_kind == "provider_failed":
        return "provider_failure"
    if parse_result == "rejected":
        return "parse_failure"
    if verifier_result == "rejected":
        return "verification_rejection"
    return None


def assign_input_scale_groups(rows: list[dict[str, object]]) -> None:
    selected = sorted(
        (row for row in rows if row["partition_count"] == 8 and not row["is_prime"]),
        key=lambda row: (row["candidate_domain_size"], row["target_n"], row["case_id"]),
    )
    labels = ("small_M", "middle_M", "large_M")
    for row in rows:
        row["input_scale_rank"] = None
        row["input_scale_group"] = None
    for rank, row in enumerate(selected, start=1):
        row["input_scale_rank"] = rank
        row["input_scale_group"] = labels[3 * (rank - 1) // len(selected)]
```

Run the same focused pytest command. Expected: `4 passed`.

### Task 2: Implement the read-only formal-run join and outputs

**Files:**
- Modify: `src/tokenshare/experiments/factorization_exp1_analysis.py`

- [x] **Step 1: Define the explicit output columns and metadata specs**

Define `ROOT_FIELDS` and `ATTEMPT_FIELDS` as ordered tuples. Define one metadata record for every field with `name`, `semantic`, `source_files`, `source_field_paths`, `kind`, `formula`, and `null_handling`. The required user columns and only the supporting identity, direct-state, missing-reason, and input-scale columns approved in the design must be present.

- [x] **Step 2: Implement read-only loaders and joins**

Implement these orchestration boundaries:

```python
def extract_analysis(*, run_dir: Path, catalog_path: Path) -> AnalysisResult:
    """Join frozen inventory members to committed catalog, root, trace, call, and artifact facts."""


def validate_analysis(result: AnalysisResult, *, run_dir: Path) -> dict[str, object]:
    """Compare identities, geometry, nullable totals, and outcome cells with exp1.jsonl."""


def write_analysis(result: AnalysisResult, validation: dict[str, object],
                   *, run_dir: Path, catalog_path: Path, output_dir: Path) -> tuple[Path, ...]:
    """Write only the two analysis CSVs, metadata JSON, and validation Markdown."""
```

The join must start from the 300 frozen Factorization inventory members, require one catalog and committed root result per identity, retain preflight infrastructure-invalid roots without synthetic attempts, join started roots to protocol traces and worker facts, join every attempt to one provider terminal, and read parsed candidate kind only through persisted submission artifact references.

- [x] **Step 3: Implement the CLI without runner/provider imports**

```powershell
python -m tokenshare.experiments.factorization_exp1_analysis `
  --run-dir TokenShareData/sources/official-full-run `
  --catalog benchmarks/experiments/factorization_catalog.v2.jsonl `
  --output-dir TokenShareData/outputs/experiments/slim-v2-full-flash-20260823-233000-b4c8e951-factorization-exp1-analysis
```

The CLI resolves all paths, rejects an output inside the resolved source run or tracked `results/experiments`, performs extraction and validation in memory, then writes only the four named analysis files.

- [x] **Step 4: Run focused tests and compilation**

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
python -m pytest tests/experiments/test_factorization_exp1_analysis.py -q
python -m compileall -q src/tokenshare/experiments/factorization_exp1_analysis.py
```

Expected: focused tests pass and compilation exits zero.

### Task 3: Generate, inspect, document, and synchronize

**Files:**
- Modify: `Doc/Experiments/code-map.md`
- Create: `E:/TokenEcnomic/TokenShareWorktrees/slim-v2-baseline/src/tokenshare/experiments/slim_v2/factorization_exp1_analysis.py`
- Create under ignored local output: `TokenShareData/outputs/experiments/slim-v2-full-flash-20260823-233000-b4c8e951-factorization-exp1-analysis/*`

- [x] **Step 1: Run the extractor once against the junction**

Run the Task 2 CLI command. Expected outputs are 300 root rows and 1,547 actual attempt rows; no experiment or provider command is run.

- [x] **Step 2: Independently inspect the CSV artifacts**

Use Python's `csv.DictReader` to check header identity, row counts, unique root IDs, blank/null serialization, the 32/31/31 input-scale grouping, and the six 8-range no-final roots. Import each CSV with the bundled spreadsheet runtime and inspect its first rows and column count; no XLSX is exported.

- [x] **Step 3: Check the generated metadata and validation report**

Confirm every CSV field has exactly one metadata entry, all formal reducer comparisons pass, the two preflight infrastructure-invalid roots retain blank root timing, and the one root with missing provider usage retains blank token totals rather than zero.

- [x] **Step 4: Synchronize the standalone script and update the code map**

Copy the public script byte-for-byte to the approved Slim V2 path and compare SHA-256. Add one `code-map.md` bullet describing the read-only extractor and its local output boundary. Do not modify the raw source, published `results/experiments`, root `AGENTS.md`, or existing untracked `benchmarks/paper/**`.

- [x] **Step 5: Run repository-focused verification and Git audits**

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
python verification/run_verification.py --focused experiments
git diff --check
git status --short
```

Expected: focused verification passes; tracked changes are limited to the extractor, its focused test, the design/plan and code-map documentation, while the migration Agent's existing `AGENTS.md` change and `benchmarks/paper/**` remain unstaged and untouched.

- [x] **Step 6: Commit only extraction-owned tracked files**

Stage explicit extraction-owned paths only, verify the cached diff excludes `AGENTS.md` and `benchmarks/paper/**`, then commit the implementation. The ignored local output and Slim V2 worktree copy are reported separately and are not included in the public commit.
