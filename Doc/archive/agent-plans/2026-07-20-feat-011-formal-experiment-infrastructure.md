# feat-011 Formal Experiment Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline-verifiable formal Experiment 1-5 execution path that consumes frozen dispatch bindings, persists immutable root/attempt evidence, resumes and replays without provider calls, and derives regression-only metrics/reports from capturing transport evidence.

**Architecture:** Keep protocol and domain algorithms in the existing Factorization/Lean adapters. Add an experiment-layer suite runner that invokes the registered module `run_condition()` entry, with explicit Exp1-5 callbacks that execute every selected root and persist condition/run/task/attempt/fault/worker/ablation evidence after each root. Add independent formal evidence validation, metrics recomputation, and report generation; capturing evidence is always `formal=true`, `pilot_only=false`, `execution_scope=formal_matrix`, `regression_only=true`, and `paper_eligible=false`.

**Tech Stack:** Python 3.11, dataclasses, `concurrent.futures`, pathlib, JSON/JSONL/CSV, SQLite-backed existing stores, pytest, existing TokenShare adapters and paper primitives.

---

### Task 1: Formal CLI and Generic Runner RED

**Files:**
- Create: `tests/experiments/test_paper_formal_runner.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`
- Create: `src/tokenshare/experiments/paper_formal_runner.py`

- [ ] Add a CLI test that patches `execute_paper_formal_suite`, invokes non-pilot/non-plan-only mode with capturing dependencies, and asserts the formal runner is called instead of writing `status=planned`.
- [ ] Run the two tests and confirm RED because `execute_paper_formal_suite` and the non-pilot branch do not exist.
- [ ] Add `execute_paper_formal_suite(...)`, validate catalog/budget/dispatch/identity/request-limit digests, allocate one output root per experiment, and call each registered module via `dispatch_paper_condition()`.
- [ ] Add CLI flags for formal execution, replay/resume, optional approval gate, and hard limits; preserve the parallel agent's budget upper-bound and bypass changes.
- [ ] Re-run the focused tests and confirm GREEN.

### Task 2: Immutable Evidence, Checkpoint, Resume, Replay

**Files:**
- Create: `tests/experiments/test_paper_formal_evidence.py`
- Create: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`

- [ ] Add RED tests for the required directory contract, artifact ref integrity, one checkpoint per completed root, completed-root resume with zero transport calls, replay-only with zero transport calls, digest drift rejection, cross-experiment isolation, and fail-closed missing evidence.
- [ ] Implement atomic JSON/JSONL writers, per-run evidence manifests, artifact index verification, suite identity manifests, completed-root checkpoints, and replay readers that never invoke provider/parser/checker.
- [ ] Re-run the focused evidence/runner tests and confirm GREEN.

### Task 3: Explicit Exp1-5 Formal Callbacks

**Files:**
- Create: `tests/experiments/test_paper_formal_callbacks.py`
- Create: `src/tokenshare/experiments/paper_formal_callbacks.py`
- Modify only if a failing integration test proves necessary: `src/tokenshare/experiments/factorization_paper_adapter.py`, `src/tokenshare/experiments/lean_paper_adapter.py`

- [ ] Add RED tests proving Exp1 consumes the complete selection; blocked selections call transport zero times; Exp2 worker count changes observed slots and emits timestamps/dependencies/merge gates; Exp3 persists raw/provenance before fault mutation, creates replacement attempts for recoverable faults, and uses the real process death harness; Exp4 six modes emit distinct boundary evidence; Exp5 remains fixed-entry and exports model execution records.
- [ ] Implement a callback registry keyed by experiment id. Reuse `dispatch_paper_case`, `inject_exp3_post_ai_fault`, `run_worker_death_harness`, and `ablation_profile_for_mode`; never generate adapter answers in the callback.
- [ ] Derive task/attempt status from adapter and wrapper evidence, preserve original and mutated refs, keep capturing transport ineligible, and checkpoint after every root.
- [ ] Re-run callback/runner/evidence tests and confirm GREEN.

### Task 4: Formal Metrics and Reports

**Files:**
- Create: `tests/experiments/test_paper_formal_metrics.py`
- Create: `tests/experiments/test_paper_formal_report.py`
- Create: `src/tokenshare/experiments/paper_formal_metrics.py`
- Create: `src/tokenshare/experiments/paper_formal_report.py`

- [ ] Add RED mutation tests showing changed task/attempt/fault evidence changes Exp1-5 summaries, and capturing/pilot/plan-only/blocked/budget-exhausted evidence never enters paper tables.
- [ ] Implement evidence-derived feasibility, scalability/critical-path, robustness/recovery, ablation escape, and endpoint identity rows.
- [ ] Run secret scan before report generation; write all required CSV/JSON/Markdown paths and an audit-only regression report for capturing runs.
- [ ] Re-run metrics/report tests and confirm GREEN.

### Task 5: Public API, Full-Plan Arithmetic Probe, and Status Sync

**Files:**
- Modify: `src/tokenshare/experiments/__init__.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`

- [ ] Add a pure dispatch-plan arithmetic probe that sums `len(selection.ordered_case_ids)` over `bound_items()` and expects the post-integration values 1,905 / 10,300 / 61,734 / 9,270 / 4,635, P0-core 83,209, and P0-full 87,844 without production constants.
- [ ] Export the formal API and make the CLI call it only for non-pilot/non-plan-only execution; single-experiment runs remain isolated and incomplete Exp5 blocks only Exp5.
- [ ] Update Chinese status docs without overwriting the catalog/budget agent's entries, recording capturing-only verification and zero real provider calls.
- [ ] Run focused formal tests, `tests/experiments`, executor/plugin impact tests, `compileall`, `git diff --check`, and fast `./init.ps1`; do not run the full Lean checker per the user's explicit instruction.
- [ ] Review only owned/integration diffs, stage only formal infrastructure files, and create one coherent commit without pushing.
