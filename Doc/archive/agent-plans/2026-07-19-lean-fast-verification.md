# Lean Fast Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prominent agent policy and a real-but-bounded Lean verification entry that validates the exact Task 14 selection without launching one Lean process for every catalog node.

**Architecture:** A verification-only Python module reads the frozen readiness manifest plus the base/v2 Lean catalogs without calling `load_paper_catalogs()`, recomputes catalog/selection/fingerprint contracts, builds one temporary Lean source for all selected proof candidates, and invokes Lean once after a single oracle-module build. It then runs one deterministic hard-induction golden case end to end; PowerShell and Bash wrappers share this implementation and one exact pytest manifest. Production checker and paper evidence paths remain unchanged.

**Tech Stack:** Python 3.11, pytest, Lean 4.8/Lake 5, PowerShell, Bash, JSON/JSONL.

---

## File map

**Create**

- `verification/lean_fast_verify.py`: pure snapshot validation, batch source construction, Lean subprocess orchestration, representative golden execution, JSON summary.
- `verification/lean-fast-tests.txt`: exact fast pytest paths/node IDs.
- `verify-lean-fast.ps1`: Windows/PowerShell entry.
- `verify-lean-fast.sh`: Bash/Git Bash/WSL entry.
- `tests/test_lean_fast_verification_profile.py`: unit and wrapper/manifest contract tests.

**Modify**

- `AGENTS.md`: mandatory Lean slow-path policy immediately before Startup Workflow.
- `Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-prompt-pack.md`: C–G verification ownership and Prompt H full-suite checkpoint.
- `README.md`, `Doc/agent-navigation.md`: command discovery.
- `Doc/TechnicalDocument/tokenshare_v1_code_map.md`: component boundary.
- `feature_list.json`, `progress.md`, `session-handoff.md`: evidence and handoff.
- `docs/superpowers/specs/2026-07-19-lean-fast-verification-design.md`: already refined to cover both v1 and v2 selected cases.

Do not modify `src/tokenshare/plugins/lean_proof/checker.py`, `paper_catalog.py`, `lean_paper_adapter.py`, merge policy, paper evidence schemas, provider code, or experiment conditions.

### Task 1: Freeze the pure snapshot and batch-source contract

**Files:**

- Create: `tests/test_lean_fast_verification_profile.py`
- Create: `verification/lean_fast_verify.py`

- [ ] **Step 1: Write RED tests for catalog and selection validation**

Create tests that import the planned public verification API and use the real frozen files without invoking Lean:

```python
from copy import deepcopy
from pathlib import Path

import pytest

from verification.lean_fast_verify import (
    LeanFastVerificationError,
    build_batch_source,
    load_snapshot,
    validate_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_covers_exact_task14_selection_without_catalog_preflight() -> None:
    snapshot = load_snapshot(ROOT)
    summary = validate_snapshot(snapshot)

    assert summary["selected_case_count"] == 135
    assert summary["cell_count"] == 9
    assert summary["v1_root_proof_count"] > 0
    assert summary["v2_node_proof_count"] > 0
    assert summary["catalog_digest"] == snapshot.readiness["catalog_digest"]
    assert summary["selection_digest"] == snapshot.readiness["task15_budget_input"]["selection_digest"]
    assert summary["provider_calls_made"] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_id", "lean_fast_duplicate_selected_case"),
        ("missing_id", "lean_fast_selected_case_missing_from_catalog"),
        ("catalog_drift", "lean_fast_catalog_digest_mismatch"),
        ("selection_drift", "lean_fast_selection_digest_mismatch"),
        ("fingerprint_drift", "lean_fast_semantic_fingerprint_mismatch"),
    ],
)
def test_snapshot_drift_fails_closed(mutation: str, message: str) -> None:
    snapshot = deepcopy(load_snapshot(ROOT))
    snapshot = snapshot.mutated_for_test(mutation)

    with pytest.raises(LeanFastVerificationError, match=message):
        validate_snapshot(snapshot)


def test_batch_source_contains_every_selected_proof_with_stable_markers() -> None:
    snapshot = load_snapshot(ROOT)
    batch = build_batch_source(snapshot)

    assert batch.case_ids == snapshot.selected_case_ids
    assert len(batch.markers) == batch.v1_root_proof_count + batch.v2_node_proof_count
    assert "-- tokenshare-case:" in batch.source
    assert "set_option autoImplicit false" in batch.source
    assert batch.source.count("theorem ") == len(batch.markers)
```

The test-only mutation helper must return a new frozen snapshot and must never alter repository files.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
$env:PYTHONPATH='src;.'
conda run --no-capture-output -n tokenshare python -m pytest tests/test_lean_fast_verification_profile.py -q
```

Expected: collection fails because `verification.lean_fast_verify` does not exist.

- [ ] **Step 3: Implement JSON/JSONL loading and digest validation**

Create these types and stable errors in `verification/lean_fast_verify.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable

from tokenshare.experiments.paper_catalog import lean_case_semantic_fingerprint
from tokenshare.experiments.paper_models import digest_json


JsonObject = dict[str, Any]


class LeanFastVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class LeanFastSnapshot:
    root: Path
    readiness: JsonObject
    factorization_cases: tuple[JsonObject, ...]
    lean_v1_cases: tuple[JsonObject, ...]
    lean_v2_cases: tuple[JsonObject, ...]
    selected_case_ids: tuple[str, ...]

    def mutated_for_test(self, mutation: str) -> "LeanFastSnapshot":
        readiness = json.loads(json.dumps(self.readiness))
        v1 = list(self.lean_v1_cases)
        v2 = list(self.lean_v2_cases)
        if mutation == "duplicate_id":
            readiness["task15_budget_input"]["selected_case_ids_by_cell"]["simple/pure_logic"][1] = readiness["task15_budget_input"]["selected_case_ids_by_cell"]["simple/pure_logic"][0]
        elif mutation == "missing_id":
            readiness["task15_budget_input"]["selected_case_ids_by_cell"]["simple/pure_logic"][0] = "missing_case"
        elif mutation == "catalog_drift":
            v2[0] = {**v2[0], "construction_seed": "drift"}
        elif mutation == "selection_drift":
            readiness["task15_budget_input"]["selection_digest"] = "sha256:drift"
        elif mutation == "fingerprint_drift":
            key = "hard_frontier/induction"
            readiness["task15_budget_input"]["semantic_fingerprint_digests_by_cell"][key][0] = "sha256:drift"
        else:
            raise AssertionError(mutation)
        selected = _selected_ids(readiness)
        return replace(self, readiness=readiness, lean_v1_cases=tuple(v1), lean_v2_cases=tuple(v2), selected_case_ids=selected)


@dataclass(frozen=True)
class LeanBatchSource:
    source: str
    markers: tuple[tuple[int, str, str], ...]
    case_ids: tuple[str, ...]
    v1_root_proof_count: int
    v2_node_proof_count: int


def _read_jsonl(path: Path) -> tuple[JsonObject, ...]:
    rows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise LeanFastVerificationError(f"lean_fast_jsonl_row_not_object:{path}:{line_number}")
        rows.append(value)
    return tuple(rows)


def load_snapshot(root: Path) -> LeanFastSnapshot:
    readiness = json.loads((root / "benchmarks/paper/lean_task14_3x3_readiness.v1.json").read_text(encoding="utf-8"))
    factor = tuple({**row, "paper_difficulty": row.get("paper_difficulty", row.get("difficulty"))} for row in _read_jsonl(root / "benchmarks/paper/factorization_catalog.v1.jsonl"))
    lean_v1 = tuple({**row, "paper_difficulty": "simple", "topic_family": "pure_logic", "topic_family_version": "shallow_v1"} for row in _read_jsonl(root / "benchmarks/paper/lean_catalog.v1.jsonl"))
    lean_v2 = _read_jsonl(root / "benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl")
    return LeanFastSnapshot(root, readiness, factor, lean_v1, lean_v2, _selected_ids(readiness))
```

Implement `_selected_ids()`, `_catalog_digest()`, `_selection_digest()` and `validate_snapshot()` with these exact invariants:

```python
def _catalog_digest(snapshot: LeanFastSnapshot) -> str:
    return digest_json({
        "catalog_id": "tokenshare.paper.catalog",
        "catalog_version": "v1",
        "factorization_cases": snapshot.factorization_cases,
        "lean_cases": snapshot.lean_v1_cases,
        "lean_lemma_graph_cases": snapshot.lean_v2_cases,
    })


def _selected_ids(readiness: JsonObject) -> tuple[str, ...]:
    selected_by_cell = readiness["task15_budget_input"]["selected_case_ids_by_cell"]
    return tuple(str(case_id) for case_ids in selected_by_cell.values() for case_id in case_ids)


def _selection_digest(readiness: JsonObject) -> str:
    budget = readiness["task15_budget_input"]
    return digest_json({
        "schema_version": "tokenshare.lean_task14_selected_cases.v1",
        "catalog_digest": readiness["catalog_digest"],
        "environment_digest": readiness["environment_digest"],
        "oracle_package_digests": readiness["oracle_package_digests"],
        "target_case_count": budget["target_case_count"],
        "selected_case_ids_by_cell": budget["selected_case_ids_by_cell"],
        "semantic_fingerprint_digests_by_cell": budget["semantic_fingerprint_digests_by_cell"],
        "golden_case_ids_by_cell": budget["golden_case_ids_by_cell"],
    })
```

Implement the validation body without calling `load_paper_catalogs()`:

```python
def validate_snapshot(snapshot: LeanFastSnapshot) -> JsonObject:
    readiness = snapshot.readiness
    budget = readiness["task15_budget_input"]
    selected_by_cell = budget["selected_case_ids_by_cell"]
    if len(selected_by_cell) != 9:
        raise LeanFastVerificationError("lean_fast_cell_count_mismatch")
    if any(len(case_ids) != 15 for case_ids in selected_by_cell.values()):
        raise LeanFastVerificationError("lean_fast_case_count_per_cell_mismatch")
    selected = _selected_ids(readiness)
    if len(selected) != len(set(selected)):
        raise LeanFastVerificationError("lean_fast_duplicate_selected_case")
    if len(selected) != 135:
        raise LeanFastVerificationError("lean_fast_selected_case_count_mismatch")
    all_cases = snapshot.lean_v1_cases + snapshot.lean_v2_cases
    cases_by_id = {str(case["case_id"]): case for case in all_cases}
    missing = [case_id for case_id in selected if case_id not in cases_by_id]
    if missing:
        raise LeanFastVerificationError("lean_fast_selected_case_missing_from_catalog:" + missing[0])
    actual_catalog_digest = _catalog_digest(snapshot)
    if actual_catalog_digest != readiness["catalog_digest"]:
        raise LeanFastVerificationError("lean_fast_catalog_digest_mismatch")
    if _selection_digest(readiness) != budget["selection_digest"]:
        raise LeanFastVerificationError("lean_fast_selection_digest_mismatch")
    if readiness.get("blocked_cell_count") != 0 or budget.get("blocked_cell_count") != 0:
        raise LeanFastVerificationError("lean_fast_blocked_cell")
    if readiness.get("provider_calls_made") != 0 or budget.get("provider_calls_made") != 0:
        raise LeanFastVerificationError("lean_fast_provider_call_contract")

    fingerprints_by_cell: dict[str, list[str]] = {}
    v1_count = 0
    v2_node_count = 0
    for cell_key, case_ids in selected_by_cell.items():
        fingerprints = [lean_case_semantic_fingerprint(cases_by_id[case_id]) for case_id in case_ids]
        expected = budget["semantic_fingerprint_digests_by_cell"][cell_key]
        if fingerprints != expected:
            raise LeanFastVerificationError("lean_fast_semantic_fingerprint_mismatch:" + cell_key)
        if len(set(fingerprints)) != 15:
            raise LeanFastVerificationError("lean_fast_semantic_duplicate:" + cell_key)
        fingerprints_by_cell[cell_key] = fingerprints
        for case_id in case_ids:
            case = cases_by_id[case_id]
            if case.get("environment_digest") != readiness["environment_digest"]:
                raise LeanFastVerificationError("lean_fast_case_environment_mismatch:" + case_id)
            if case["schema_version"] == "tokenshare.paper_lean_case.v1":
                if case.get("oracle_proof_ref", {}).get("preflight_status") != "passed":
                    raise LeanFastVerificationError("lean_fast_v1_not_checker_backed:" + case_id)
                v1_count += 1
            else:
                oracle = case.get("oracle_proof_package_ref")
                node_ids = {str(node["node_id"]) for node in case["lemma_graph"]["nodes"]}
                if case.get("preflight_status") != "passed" or not isinstance(oracle, dict) or set(oracle.get("node_proof_sources", {})) != node_ids:
                    raise LeanFastVerificationError("lean_fast_v2_not_checker_backed:" + case_id)
                v2_node_count += len(node_ids)
    for topic in ("pure_logic", "function_set", "induction"):
        if set(fingerprints_by_cell[f"medium_lemma_dag/{topic}"]) & set(fingerprints_by_cell[f"hard_frontier/{topic}"]):
            raise LeanFastVerificationError("lean_fast_hard_is_medium:" + topic)
    return {
        "catalog_digest": actual_catalog_digest,
        "selection_digest": budget["selection_digest"],
        "selected_case_count": len(selected),
        "cell_count": len(selected_by_cell),
        "v1_root_proof_count": v1_count,
        "v2_node_proof_count": v2_node_count,
        "provider_calls_made": 0,
    }
```

This returns counts only; it must not create artifacts or invoke Lean.

- [ ] **Step 4: Implement deterministic batch rendering**

Use one case-local namespace so reused theorem names cannot collide. Preserve declared dependency order for v2 and original payload/proof text:

```python
def _safe_identifier(value: str) -> str:
    body = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)
    return f"Case_{body}"


def _lean_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _render_theorem(payload: JsonObject, proof_source: str) -> tuple[list[str], int]:
    lines: list[str] = []
    namespace = str(payload.get("namespace", ""))
    if namespace:
        lines.append(f"namespace {namespace}")
    for opened in payload.get("open_namespaces", []):
        lines.append(f"open {opened}")
    options = {"autoImplicit": False, **dict(payload.get("options", {}))}
    for key, value in sorted(options.items()):
        lines.append(f"set_option {key} {_lean_value(value)} in")
    parameters = str(payload.get("parameters_source", ""))
    parameters = f" {parameters}" if parameters else ""
    theorem_line = len(lines)
    lines.append(
        f"theorem {payload['theorem_name']}{parameters} : "
        f"{payload['statement_source']} := {proof_source}"
    )
    if namespace:
        lines.append(f"end {namespace}")
    return lines, theorem_line


def build_batch_source(snapshot: LeanFastSnapshot) -> LeanBatchSource:
    validate_snapshot(snapshot)
    cases = {str(case["case_id"]): case for case in snapshot.lean_v1_cases + snapshot.lean_v2_cases}
    imports = {"Init"}
    declarations: list[tuple[str, str, JsonObject, str]] = []
    v1_count = 0
    v2_count = 0
    for case_id in snapshot.selected_case_ids:
        case = cases[case_id]
        if case["schema_version"] == "tokenshare.paper_lean_case.v1":
            payload = case["theorem_payload"]
            imports.update(payload.get("imports", ["Init"]))
            declarations.append((case_id, "root", payload, case["oracle_proof_ref"]["proof_source"]))
            v1_count += 1
            continue
        nodes = {str(node["node_id"]): node for node in case["lemma_graph"]["nodes"]}
        order = [str(node_id) for node_id in case["merge_plan_shape"]["dependency_order"]]
        proof_sources = case["oracle_proof_package_ref"]["node_proof_sources"]
        for node_id in order:
            payload = nodes[node_id]["theorem_payload"]
            imports.update(payload.get("imports", ["Init"]))
            declarations.append((case_id, node_id, payload, proof_sources[node_id]))
            v2_count += 1

    lines = [*(f"import {name}" for name in sorted(imports)), "", "set_option autoImplicit false", ""]
    markers: list[tuple[int, str, str]] = []
    for case_id, node_id, payload, proof_source in declarations:
        case_namespace = "TokenShareLeanFast." + _safe_identifier(case_id)
        lines.extend([f"namespace {case_namespace}", f"-- tokenshare-case: {case_id} node: {node_id}"])
        rendered, theorem_offset = _render_theorem(payload, proof_source)
        markers.append((len(lines) + theorem_offset + 1, case_id, node_id))
        lines.extend([*rendered, "end " + case_namespace, ""])
    return LeanBatchSource("\n".join(lines), tuple(markers), snapshot.selected_case_ids, v1_count, v2_count)
```

Before GREEN, add a test with a dependency-order mutation that expects `lean_fast_dependency_order_mismatch`; do not silently topologically reorder a malformed frozen manifest.

- [ ] **Step 5: Run pure tests and commit**

Run the RED command again. Expected: all pure tests pass without a `lake`, `lean`, or provider subprocess.

Commit only:

```powershell
git add -- verification/lean_fast_verify.py tests/test_lean_fast_verification_profile.py
git commit -m "test: freeze fast Lean verification contracts"
```

### Task 2: Execute one build, one batch check, and one golden path

**Files:**

- Modify: `verification/lean_fast_verify.py`
- Test: `tests/test_lean_fast_verification_profile.py`

- [ ] **Step 1: Write RED orchestration tests with injected runners**

Add an injectable `command_runner` and `golden_runner`; assert exact call count and non-paper output:

```python
from subprocess import CompletedProcess
from types import SimpleNamespace


def test_run_fast_verification_uses_one_build_one_batch_and_one_golden(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    snapshot = load_snapshot(ROOT)

    def command_runner(args, **kwargs):
        commands.append(tuple(args))
        return CompletedProcess(args, 0, "", "")

    golden_calls: list[str] = []
    result = run_fast_verification(
        load_snapshot(ROOT),
        temp_root=tmp_path,
        command_runner=command_runner,
        golden_runner=lambda case, output_root: golden_calls.append(case["case_id"]) or {
            "case_id": case["case_id"],
            "deterministic_split": "passed",
            "child_proof_file_construction": "passed",
            "checker_preflight": "passed",
            "dependency_aware_merge": "passed",
            "root_recheck": "passed",
            "provider_calls_made": 0,
        },
        environment_manifest=SimpleNamespace(
            environment_digest=snapshot.readiness["environment_digest"],
            project_root=str(tmp_path),
            lake_executable="lake",
            lean_executable="lean",
        ),
    )

    assert [command[1:3] for command in commands] == [("build", "TokenShare.LemmaGraphOracle"), ("env", "lean")]
    assert len(golden_calls) == 1
    assert result["verification_profile"] == "lean_fast"
    assert result["paper_eligible"] is False
    assert result["provider_calls_made"] == 0
```

Also test build failure, batch failure with line-to-case mapping, environment digest drift, and a failed/missing golden stage. Stable error prefixes must be `lean_fast_oracle_build_failed`, `lean_fast_batch_failed`, `lean_fast_environment_digest_mismatch`, and `lean_fast_golden_failed`.

- [ ] **Step 2: Run RED orchestration tests**

Run the Task 1 command. Expected: failure because `run_fast_verification()` and CLI are missing.

- [ ] **Step 3: Implement subprocess and golden orchestration**

Add:

```python
from contextlib import nullcontext
import os
import re
import subprocess
import tempfile
from time import monotonic

from tokenshare.experiments.lean_paper_adapter import build_lean_lemma_graph_oracle_evidence
from tokenshare.experiments.paper_catalog import default_lean_paper_environment_manifest


def _subprocess_env(lean_executable: str) -> dict[str, str]:
    env = dict(os.environ)
    lean_path = Path(lean_executable)
    env["ELAN_HOME"] = str(lean_path.parent.parent)
    env["PATH"] = f"{lean_path.parent}{os.pathsep}{env.get('PATH', '')}"
    return env


def _representative_case(snapshot: LeanFastSnapshot) -> JsonObject:
    golden_id = snapshot.readiness["task15_budget_input"]["golden_case_ids_by_cell"]["hard_frontier/induction"][0]
    return next(case for case in snapshot.lean_v2_cases if case["case_id"] == golden_id)


def run_fast_verification(snapshot: LeanFastSnapshot, *, temp_root: Path | None = None, command_runner: Callable[..., Any] = subprocess.run, golden_runner: Callable[..., JsonObject] = build_lean_lemma_graph_oracle_evidence, environment_manifest=None) -> JsonObject:
    started = monotonic()
    summary = validate_snapshot(snapshot)
    manifest = environment_manifest or default_lean_paper_environment_manifest()
    if manifest.environment_digest != snapshot.readiness["environment_digest"]:
        raise LeanFastVerificationError("lean_fast_environment_digest_mismatch")
    batch = build_batch_source(snapshot)
    common = dict(cwd=manifest.project_root, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120, env=_subprocess_env(manifest.lean_executable), check=False)
    built = command_runner([manifest.lake_executable, "build", "TokenShare.LemmaGraphOracle"], **common)
    if built.returncode != 0:
        raise LeanFastVerificationError("lean_fast_oracle_build_failed:" + (built.stdout + built.stderr)[:1200])
    context = tempfile.TemporaryDirectory(prefix="tokenshare_lean_fast_") if temp_root is None else nullcontext(str(temp_root))
    with context as directory:
        batch_path = Path(directory) / "TokenShareLeanFastBatch.lean"
        batch_path.write_text(batch.source, encoding="utf-8")
        checked = command_runner([manifest.lake_executable, "env", "lean", str(batch_path)], **common)
        if checked.returncode != 0:
            raise LeanFastVerificationError(_mapped_batch_error(checked.stdout + checked.stderr, batch.markers))
        evidence = golden_runner(case=_representative_case(snapshot), output_root=Path(directory) / "golden")
    required = ("deterministic_split", "child_proof_file_construction", "checker_preflight", "dependency_aware_merge", "root_recheck")
    if evidence.get("provider_calls_made") != 0 or any(evidence.get(stage) != "passed" for stage in required):
        raise LeanFastVerificationError("lean_fast_golden_failed")
    return {**summary, "verification_profile": "lean_fast", "paper_eligible": False, "provider_calls_made": 0, "batch_lean_invocation_count": 1, "golden_case_id": evidence["case_id"], "duration_seconds": round(monotonic() - started, 3)}
```

Implement `_mapped_batch_error()` by extracting the generated source line number from Lean diagnostics and selecting the nearest preceding marker:

```python
_BATCH_LINE_RE = re.compile(r"TokenShareLeanFastBatch\.lean:(\d+):\d+")


def _mapped_batch_error(output: str, markers: tuple[tuple[int, str, str], ...]) -> str:
    match = _BATCH_LINE_RE.search(output)
    if match is None:
        return "lean_fast_batch_failed:" + output[:1200]
    line_number = int(match.group(1))
    candidates = [marker for marker in markers if marker[0] <= line_number]
    if not candidates:
        return "lean_fast_batch_failed:" + output[:1200]
    _, case_id, node_id = candidates[-1]
    return f"lean_fast_batch_failed:case_id={case_id}:node_id={node_id}:" + output[:1200]
```

- [ ] **Step 4: Implement CLI output**

Add `main()` with `--root` defaulting to the repository root and a single JSON object on success. It must never accept API keys/provider configs. On `LeanFastVerificationError`, print the stable message to stderr and exit 1.

- [ ] **Step 5: Run unit tests and real verifier once**

Run pure unit tests first, then:

```powershell
$env:PYTHONPATH='src;.'
Measure-Command { conda run --no-capture-output -n tokenshare python verification/lean_fast_verify.py }
```

Expected: exit 0; JSON contains `selected_case_count=135`, `verification_profile=lean_fast`, `paper_eligible=false`, `provider_calls_made=0`, `batch_lean_invocation_count=1`; record cold/warm durations.

- [ ] **Step 6: Commit**

```powershell
git add -- verification/lean_fast_verify.py tests/test_lean_fast_verification_profile.py
git commit -m "feat: batch fast Lean verification"
```

### Task 3: Add shared PowerShell/Bash entry and exact fast tests

**Files:**

- Create: `verification/lean-fast-tests.txt`
- Create: `verify-lean-fast.ps1`
- Create: `verify-lean-fast.sh`
- Modify: `tests/test_lean_fast_verification_profile.py`

- [ ] **Step 1: Write RED wrapper/manifest tests**

Require both wrappers to reference `verification/lean_fast_verify.py` and `verification/lean-fast-tests.txt`; parse pytest node paths before `::`; reject entries that select complete slow files/directories:

```python
def test_lean_fast_manifest_uses_exact_non_slow_nodes() -> None:
    entries = _lean_fast_entries()
    assert "tests" not in entries
    assert "tests/plugins/lean_proof" not in entries
    assert "tests/experiments/test_lean_task14_readiness.py" not in entries
    assert any("::test_task14_readiness_blocks_when_golden_evidence_missing" in item for item in entries)
    assert all((ROOT / item.split("::", 1)[0]).exists() for item in entries)


def test_wrappers_share_python_entry_and_manifest() -> None:
    powershell = (ROOT / "verify-lean-fast.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "verify-lean-fast.sh").read_text(encoding="utf-8")
    for body in (powershell, bash):
        assert "verification/lean_fast_verify.py" in body
        assert "verification/lean-fast-tests.txt" in body
```

- [ ] **Step 2: Run RED tests**

Expected: missing wrapper/manifest failures.

- [ ] **Step 3: Create exact test manifest**

Use:

```text
# Lean fast profile: no complete catalog/readiness/plugin suites.
tests/test_lean_fast_verification_profile.py
tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py
tests/plugins/lean_proof/test_lean_environment.py
tests/experiments/test_lean_task14_readiness.py::test_task14_readiness_blocks_when_golden_evidence_missing
tests/experiments/test_lean_task14_readiness.py::test_task14_readiness_blocks_when_golden_case_is_not_selected
tests/experiments/test_lean_task14_readiness.py::test_task14_readiness_blocks_when_golden_case_is_not_checker_backed
tests/experiments/test_lean_task14_readiness.py::test_task14_readiness_blocks_when_too_many_golden_cases_are_frozen
tests/experiments/test_lean_task14_readiness.py::test_task14_readiness_blocks_when_any_golden_stage_fails
```

- [ ] **Step 4: Implement thin wrappers**

PowerShell must set UTF-8/PYTHONPATH, run the verifier, read the manifest, then make one pytest call. Bash must do the same and use the existing conda discovery pattern from `init.sh`. Both fail on missing/empty/duplicate entries and unknown arguments. Neither wrapper starts parallel Lean jobs.

PowerShell public behavior:

```powershell
param()
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "src;$env:PYTHONPATH"
$CondaEnv = if ($env:TOKENSHARE_CONDA_ENV) { $env:TOKENSHARE_CONDA_ENV } else { "tokenshare" }
conda run --no-capture-output -n $CondaEnv python verification/lean_fast_verify.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$Tests = Get-Content -LiteralPath "verification/lean-fast-tests.txt" -Encoding UTF8 | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") }
conda run --no-capture-output -n $CondaEnv python -m pytest -q @Tests
exit $LASTEXITCODE
```

Add explicit manifest validation before execution; the snippet is the minimum public flow, not permission to omit validation.

- [ ] **Step 5: Verify wrappers and commit**

Run static tests, `powershell -ExecutionPolicy Bypass -File .\verify-lean-fast.ps1`, `bash -n ./verify-lean-fast.sh`, and when Bash can reach the Windows conda environment run `./verify-lean-fast.sh`.

Commit only the four owned files.

### Task 4: Put the slow-path rule in every agent's visible path

**Files:**

- Modify: `AGENTS.md`
- Modify: `Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-prompt-pack.md`
- Modify: `README.md`
- Modify: `Doc/agent-navigation.md`
- Modify: `tests/test_lean_fast_verification_profile.py`

- [ ] **Step 1: Write RED documentation assertions**

Assert that `AGENTS.md` contains a `## ⚠ Lean 慢路径规则（所有 Agent 必读）` heading before `## 启动流程`, names both fast wrappers, forbids routine whole-suite Lean runs, and defines the Prompt H/full checkpoint. Assert the Prompt Pack common rules mention C–G fast verification and Prompt H full ownership.

- [ ] **Step 2: Run RED documentation tests**

Expected: missing heading/policy failures.

- [ ] **Step 3: Update `AGENTS.md` prominently**

Insert immediately after the project introduction:

```markdown
## ⚠ Lean 慢路径规则（所有 Agent 必读）

Lean catalog/readiness/plugin 的完整测试会为每个 case/node/root 重复启动 `lake env lean`，可能耗时数分钟。除非改动直接涉及 checker、oracle、proof assembly、split/merge/root recheck，或正在执行 Prompt H/合并/发布 checkpoint，否则禁止把整个 `tests/plugins/lean_proof`、`test_lean_task14_readiness.py`、`test_lean_lemma_graph_catalog.py` 或 `pytest tests` 当作普通验证。

- 非 Lean 和 Exp1–5 独立模块：运行默认 `.\init.ps1` / `./init.sh` 与 owned tests。
- Lean metadata/catalog/readiness：先运行 `.\verify-lean-fast.ps1` / `./verify-lean-fast.sh` 和精确 pytest node ID。
- Lean checker/oracle/merge 边界：快速入口通过后，只扩大到受影响的真实 Lean tests。
- 完整 `.\init.ps1 -Full` / `./init.sh --full` 由 Lean feature checkpoint 或 Prompt H integration owner 集中运行一次；并发 C–G agent 不得重复运行。

需要真实 Lean 时，必须在同一个 pytest/验证进程内集中执行，先一次 build，再批量检查；禁止按 case 启动多个并行 Lean 进程。快速验证不是论文 evidence，不能替代正式 checker/golden/full checkpoint。
```

- [ ] **Step 4: Reconcile Prompt Pack contradictions**

Update the common rule and C–G completion sentences so they require owned tests + contracts/impact tests + default fast init + Lean fast only when relevant. Keep Prompt A and Prompt H full verification requirements because they own Lean semantics/integration. Do not change experiment matrices or provider prohibitions.

- [ ] **Step 5: Update command indexes and verify**

Add one concise section/link to README and agent navigation. Run the documentation/profile tests and scan for old statements that still require every C–G agent to run full Lean/full init.

- [ ] **Step 6: Commit**

Stage only the listed documentation and contract test file; commit `docs: make Lean slow-path policy explicit`.

### Task 5: Final verification, state, and code map

**Files:**

- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [ ] **Step 1: Run targeted static/unit verification**

Run:

```powershell
$env:PYTHONPATH='src;.'
conda run --no-capture-output -n tokenshare python -m pytest tests/test_lean_fast_verification_profile.py -q
conda run -n tokenshare python -m compileall -q verification tests/test_lean_fast_verification_profile.py
git diff --check
```

Expected: all pass; only pre-existing line-ending warnings are acceptable.

- [ ] **Step 2: Measure cold and warm fast Lean runs**

Run `.\verify-lean-fast.ps1` twice in sequence. Record total duration, oracle build duration, batch Lean duration, golden duration, selected/root/node counts and subprocess counts. Do not delete `.lake/build` to manufacture a cold run; label the first observed run cold-ish and the second warm.

Acceptance: both pass; each run uses one oracle build command and one batch command; warm run is materially faster than the old per-node suite. If batch time exceeds 60 seconds, stop and inspect diagnostics before changing architecture.

- [ ] **Step 3: Run default fast startup verification**

Run `.\init.ps1`. Expected: fast mode passes and does not invoke full Lean catalog/readiness suites.

- [ ] **Step 4: Run one full checkpoint only if no other agent owns it**

Before running, inspect active processes/agent notes. If Prompt H or another integration agent is already running `-Full`, reuse its evidence instead of starting a duplicate. Otherwise run `.\init.ps1 -Full` once because this change adds executable verification code and wrappers. Record exact pass/fail and duration; do not claim complete if it is interrupted.

- [ ] **Step 5: Update state and code map**

Record:

- root cause and measured 33.7s/0.49s/0.6–0.8s baseline;
- new commands and decision policy;
- exact targeted/default/full verification evidence;
- `provider_calls_made=0`, `paper_eligible=false` for fast verification;
- any remaining performance or Bash-environment caveat.

Keep Chinese-first wording. Add the new verification files to the code map. Do not mark Task 15 or any formal experiment complete.

- [ ] **Step 6: Review diff and commit**

Confirm only intended files are staged, then commit `feat: add fast Lean verification workflow`. Do not push unless the user separately requests it.

## Completion criteria

- All agents encounter the Lean slow-path rule before Startup Workflow.
- C–G prompts no longer require duplicate full Lean/full-suite runs.
- Fast verifier recomputes current catalog/selection/fingerprint contracts for exact 135 IDs without `load_paper_catalogs()`.
- All selected v1 root and v2 node proof candidates compile in one batch Lean invocation.
- One fixed hard-induction golden case passes split/check/merge/root-recheck with zero provider calls.
- Fast wrapper tests are exact and exclude complete slow suites.
- Production checker/evidence semantics are unchanged.
- Targeted, fast-startup, and one coordinated full-checkpoint evidence are recorded.
