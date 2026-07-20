# Factorization 500 Full Matrix And Budget Bypass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic 500-root Factorization paper catalog, include all 500 roots in every applicable Experiment 1-5 formal matrix, and let the paper CLI proceed without manual budget approval while preserving budget evidence.

**Architecture:** Keep the historical 30-case v1 catalog for regression and add a generated v2 catalog as the paper CLI default. Experiment modules continue owning condition/selection semantics, but derive Factorization selection sizes from the frozen 500-case catalog and validate the new preregistered totals. Budget approval becomes an explicit policy input whose state is stored outside the budget digest, so bypass does not weaken plan identity.

**Tech Stack:** Python 3.11, dataclasses, JSONL, deterministic 64-bit primality checks, pytest, PowerShell, TokenShare paper experiment contracts.

---

### Task 1: Deterministic 500-Root Catalog

**Files:**
- Create: `src/tokenshare/experiments/paper_factorization_catalog.py`
- Create: `tests/experiments/test_paper_factorization_catalog.py`
- Create: `benchmarks/paper/factorization_catalog.v2.jsonl`
- Modify: `src/tokenshare/experiments/paper_catalog.py`
- Test: `tests/experiments/test_paper_catalog.py`

- [ ] **Step 1: Write the failing generator contract tests**

Add tests that import `generate_factorization_paper_cases` and assert exactly 500 cases, unique IDs/targets, `167/167/166` difficulty counts, target range `[1_000_000, 100_000_000_000)`, five magnitude buckets in every difficulty, valid oracle products, valid candidate ranges, balanced factor positions, and child counts `2/4/8`.

- [ ] **Step 2: Run the generator tests and confirm RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_factorization_catalog.py
```

Expected: collection fails because `paper_factorization_catalog` does not exist.

- [ ] **Step 3: Implement deterministic generation**

Implement:

```python
generate_factorization_paper_cases(*, count: int = 500, seed: int = 20260720) -> tuple[JsonObject, ...]
catalog_jsonl_text(cases: Sequence[Mapping[str, Any]]) -> str
write_factorization_paper_catalog(*, output_path: Path, count: int = 500, seed: int = 20260720) -> str
```

Use a deterministic Miller-Rabin predicate valid for unsigned 64-bit integers, deterministic next-prime search, difficulty-specific candidate counts, and canonical compact/sorted JSON lines. Never generate provider output or expected AI responses.

- [ ] **Step 4: Generate the tracked v2 JSONL**

Run the generator through its public module entry point or a short read-only import command that calls `write_factorization_paper_catalog`. Confirm exact file line count and deterministic digest.

- [ ] **Step 5: Add v2 catalog validation while preserving v1 regression**

Update `paper_catalog.py` to derive the Factorization profile from row metadata:

```python
v1: 30 cases, 10/10/10, catalog version v1
v2: 500 cases, 167/167/166, catalog version v2
```

Validate unique targets, v2 generator fields, magnitude coverage, factor-position semantics, and `partition_candidate_ranges` full coverage. Keep Lean v1 validation unchanged. Derive manifest `catalog_version` and `generator_version` from the loaded Factorization profile.

- [ ] **Step 6: Run catalog tests and confirm GREEN**

Run the new generator tests and existing paper catalog tests. Fix only contract failures caused by the new profile.

### Task 2: Experiment 1-5 Full Factorization Selections

**Files:**
- Modify: `src/tokenshare/experiments/paper_exp1.py`
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Modify: `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- Modify: `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- Modify: `src/tokenshare/experiments/paper_runner.py`
- Test: `tests/experiments/test_paper_exp1_formal.py`
- Test: `tests/experiments/test_paper_exp2_scalability.py`
- Test: `tests/experiments/test_paper_exp3_fault_recovery.py`
- Test: `tests/experiments/test_paper_exp4_ablation_runner.py`
- Test: `tests/experiments/test_paper_exp5_model_comparison.py`
- Test: `tests/experiments/test_paper_gate_c_dispatcher.py`

- [ ] **Step 1: Write failing full-matrix tests**

Load `factorization_catalog.v2.jsonl`, build the formal catalog/readiness context, and assert:

```text
Exp1 = 1,905
Exp2 = 10,300
Exp3 = 61,734
Exp4 = 9,270
Exp5 = 4,635
P0-core = 83,209
P0-full = 87,844
```

Assert every applicable Factorization comparison group uses all difficulty-appropriate roots and that the union across difficulty is all 500 roots. Assert worker/mode/endpoint/repeat comparisons keep ordered IDs stable.

- [ ] **Step 2: Run focused matrix tests and confirm RED**

Expected failures must show old 3/5/10 slices or old root-run constants, not fixture/setup mistakes.

- [ ] **Step 3: Update Exp1 and Exp2 selections**

Exp1 uses all cases returned for each Factorization difficulty. Exp2 builds the shared Factorization slice from all cases per difficulty while retaining the existing Lean 5-task 2/2/1 slice. Update completeness audits to derive Factorization roots from selection sizes and compare against the frozen 500-case inventory.

- [ ] **Step 4: Update Exp3 catalog view and matrix audits**

Replace `medium[:5]` and one-per-difficulty worker-death selections with all 500 Factorization case IDs in stable catalog order. Ensure rate-fault and worker-death conditions bind all 500 roots and AI-unit target IDs remain globally namespaced as `case_id:unit_id`.

- [ ] **Step 5: Update Exp4 and Exp5 selections**

Exp4 `factorization_slices` uses every case in each difficulty. Exp5 formal shared Factorization slices use every case in each difficulty for all cohort members. Preserve Lean slices unchanged.

- [ ] **Step 6: Replace stale arithmetic constants and summary audits**

Update public expected-root constants and any completeness audit that currently assumes 495/600/813/540/270. Do not weaken exact matrix checks; replace them with the approved 500-case numbers or actual-selection derivation plus a frozen expected comparison.

- [ ] **Step 7: Run all five module tests and Gate C tests**

Confirm full-plan tests are GREEN without invoking adapters or provider transports.

### Task 3: Default Budget Approval Bypass With Evidence

**Files:**
- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_budget.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] **Step 1: Add failing budget-policy tests**

Add tests for:

```python
plan_paper_suite(..., plan_only=False, budget_approval_required=False)
plan_exp1_pilot(..., plan_only=False, budget_approval_required=False)
```

Assert missing digest is accepted, explicit mismatched digest is rejected, and `budget_approval` records `user_bypassed`. Assert budget digest equals the equivalent plan-only digest.

- [ ] **Step 2: Add failing CLI tests**

Assert default CLI non-plan invocation passes the budget planner without `--approve-budget-digest`, while `--require-budget-approval` preserves the old exit-code-2 failure. Use monkeypatched execution boundaries; never call a provider.

- [ ] **Step 3: Implement budget policy**

Add `budget_approval_required: bool = True` to both library planners. Refactor `_validate_budget_approval` to accept the policy. Add a structured record to `quota_preflight["budget_approval"]` without adding that record to the budget digest body.

- [ ] **Step 4: Integrate CLI default bypass**

Add `--require-budget-approval`. Pass `budget_approval_required=args.require_budget_approval`; when execution functions still require an effective digest, pass `budget.budget_digest` internally under bypass mode. Preserve explicit-digest mismatch detection.

- [ ] **Step 5: Run budget and CLI tests**

Keep the pre-existing stable Lean matrix digest and formal token/cost upper-bound work already present in the dirty files.

### Task 4: Documentation, Public API, And Status

**Files:**
- Modify: `src/tokenshare/experiments/__init__.py`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-prompt-pack.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`
- Verify: `Doc/TechnicalDocument/2026-07-20-feat-011-formal-experiment-infrastructure-completion-prompt.md`

- [ ] **Step 1: Export catalog generation API**

Expose generator/write helpers from `tokenshare.experiments` only if they form a supported paper-catalog API; otherwise keep the module import path explicit and document it.

- [ ] **Step 2: Replace authoritative frozen counts**

Update the sole authoritative design and Prompt Pack so no active instruction claims Factorization uses 30 roots or old root-run totals. Preserve old counts only in clearly labeled historical provenance.

- [ ] **Step 3: Update status honestly**

Record that catalog/matrix/budget policy is complete offline, provider calls for this task are zero, and formal infrastructure remains delegated to the separate completion prompt. Do not claim 87,844 root-runs were executed.

- [ ] **Step 4: Validate JSON and stale wording**

Parse `feature_list.json`, scan active docs for stale 30/495/2718 wording, and review every match rather than blindly replacing historical evidence.

### Task 5: Full Verification And Commit

**Files:**
- Verify all changed files

- [ ] **Step 1: Run targeted suites**

Run generator/catalog, budget/CLI, C-G modules, dispatcher, structured-output and directly affected adapter tests.

- [ ] **Step 2: Run experiment and impact suites**

Run `tests/experiments`, executor/factorization/Lean plugin suites, and `compileall`.

- [ ] **Step 3: Run repository verification**

Run `git diff --check`, `./init.ps1`, and `./init.ps1 -Full` with real provider smoke disabled.

- [ ] **Step 4: Audit provider calls and diff ownership**

Confirm no command used real provider transport. Inspect final diff and distinguish the pre-existing night-supervision changes from this implementation while preserving both.

- [ ] **Step 5: Commit coherent implementation**

Stage only files belonging to the approved 500-case/matrix/budget-policy change, including overlapping pre-existing files only after confirming their full current contents belong to the integrated result. Do not push.
