# Full-Plan Representative Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan one task at a time, with an implementer, spec reviewer, and code-quality reviewer for every task.

**Goal:** Replace matrix8 with a representative Exp1–5 smoke that is mechanically derived from a completely constructed and validated formal full plan, changes only the executed repeat/root subset, and reaches terminal real-provider results with truthful protocol and metrics evidence.

**Architecture:** Add a formal-plan snapshot/preflight layer that reuses the existing official plan builders, frozen selections, runtime adapters, request preparation, budgets, and dispatch validators. Derive representative coverage as references to original condition/binding objects plus a separate `root_case_filter`. Generalize the existing immutable response-bank acquisition path to consume exact prepared requests selected from that snapshot; Exp1–4 execute trace-backed through their original runners with zero current provider calls, while Exp5 executes every official endpoint online. Remove matrix8 authority and fixed-count assumptions instead of adding compatibility clones.

**Tech Stack:** Python 3.11, pytest, dataclasses/JSON canonical digests, SQLite/JSONL artifact stores, existing `ProtocolEngine`, Factorization/Lean runtime adapters, AI API executors, immutable response bank, PowerShell on Windows.

**Scope constraints:** Do not repair receipt, L1–L4, publication closure, paper evidence chain, or unrelated facility gates. Do not run old matrix8 scripts or use Fast/Full as acceptance. Preserve the three user-owned untracked files unless the old E2E harness is explicitly replaced in Task 6.

---

### Task 1: Freeze the formal full-plan snapshot contract

**Files:**
- Create: `src/tokenshare/experiments/paper_formal_plan.py`
- Modify only if required for a public non-executing planning API: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_plan.py`

1. Write RED tests that construct Exp1–5 official plans and assert their current authority-derived totals: 324 conditions, 6,384 root-runs, and 40,520 first-attempt AI units. The implementation must derive these values; production code must not compare against `145`, `8`, `32`, or `166`.
2. Add immutable snapshot records for condition reference, original `FrozenConditionSelectionBinding`, root identity, planned AI-unit identity, condition/selection digest, seed, repeat, split, plugin, and endpoint controls.
3. Enumerate every selected catalog case and fail on missing, duplicated, or non-canonical case binding.
4. Reuse `plan_paper_suite(..., plan_only=True)` and existing budget/request-ceiling calculators to prove every full condition is schedulable without provider calls.
5. Add an Exp4 plan-time completeness validator symmetric with Exp3: all formal domain × difficulty × ablation × repeat cells must be present before dispatch.
6. Run:
   `C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests/experiments/test_paper_formal_plan.py -q`
7. Commit only Task 1 files.

### Task 2: Freeze every formal prepared request without API access

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_plan.py`
- Modify: `src/tokenshare/experiments/paper_response_bank.py`
- Test: `tests/experiments/test_paper_formal_plan.py`
- Test: `tests/experiments/test_paper_response_bank.py`

1. Write RED tests for a formal prepared-request inventory that uses each original adapter's `plan_units()` and request preparation path, including all 4,992 Exp5 first-attempt requests.
2. Generalize the reusable parts of `build_matrix8_unified_acquisition_plan()` into exact prepared-request inventory helpers. Do not carry matrix8 selection, seed rewriting, condition suffixes, or DeepSeek-only assumptions into the generic helper.
3. Freeze body/inference/provider/model/reasoning digests, case/AI-unit/sample/replacement slot identity, request ceilings, and inventory digest for every full-plan root.
4. Verify a request can be dispatched by its original runner/executor while using a non-network planner/capturing seam; secret absence must not prevent structural planning.
5. Assert planning makes zero provider calls and creates no protocol task or real-transport capability evidence.
6. Run:
   `C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests/experiments/test_paper_formal_plan.py tests/experiments/test_paper_response_bank.py -q`
7. Commit only Task 2 files.

### Task 3: Derive representative coverage from official objects

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_plan.py`
- Modify: `src/tokenshare/experiments/paper_smoke.py`
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Test: `tests/experiments/test_paper_formal_plan.py`
- Test: `tests/experiments/test_paper_smoke.py`
- Test: focused Exp2/Exp3 tests discovered from `tests/experiments/`

1. Write RED tests proving the coverage result holds references/equality to original conditions and frozen bindings, preserves every digest/seed/split, and returns only a separate repeat subset and `root_case_filter`.
2. Derive repeat 0 coverage mechanically for all formal cells: Exp1 12, Exp2 six Factor-only worker cells, Exp3 81 fault/rate/death cells, Exp4 30 FULL/ablation cells, Exp5 16 endpoint/domain-slice cells. Counts are test observations of current authority, not planner constants.
3. Select roots in original selection order, adding roots only when required to actually schedule a formal fault/death branch. Validate through the existing formal-runner root filter membership checks.
4. Delete the Exp2 mixed-Lean smoke selection/binding/validator seam and Exp3 smoke-only condition/selection/seed validator code. Do not replace them with new smoke identities.
5. Delete the four tracked `benchmarks/paper/paper_smoke_exp{1,2,3,4}_matrix8_profile.v1.json` files and update focused tests to treat matrix8 as rejected legacy input.
6. Run all changed focused tests, including a zero-provider-call coverage construction test.
7. Commit only Task 3 files.

### Task 4: Generalize unified acquisition and hard-ledger accounting

**Files:**
- Modify: `src/tokenshare/experiments/paper_response_bank.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_response_bank.py`
- Test: `tests/experiments/test_paper_response_bank_acquisition.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

1. Write RED tests showing the acquisition plan is selected from the validated formal prepared-request snapshot, has dynamic request/root counts, and never changes formal condition identity to deduplicate.
2. Preserve exact sharing only when complete body/inference/provider identity matches. Add exact supplemental entries for non-sharing identities rather than changing seed, split, condition, or digest.
3. Generalize sparse replacement-slot calculation for selected formal Exp3/Exp4 roots and retain base incorrect/provider-error terminal entries in the immutable bank and fixed denominator.
4. Preserve maximum acquisition concurrency 10, atomic budget reservation, hard ledger, Windows artifact write lock, child-bank immutability, and ambiguous-dispatch reconciliation.
5. Remove fixed `166`, `8`, and `32` guards and old `_with_*matrix8*` condition/seed rewrite helpers from the active results-first path.
6. Assert source latency/tokens/cost/provider identity come from actual bank entries, missing usage is `None` plus count/reason, and acquisition spend is counted exactly once.
7. Run the three focused test modules and commit only Task 4 files.

### Task 5: Route representative Exp1–5 execution through original runners

**Files:**
- Modify: `src/tokenshare/experiments/run_paper_pipeline.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify if a dynamic report field is missing: `src/tokenshare/experiments/paper_smoke_report.py`
- Modify only when tests prove a gap: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_run_paper_pipeline.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`
- Test: `tests/experiments/test_trace_source_usage_projection.py`
- Test: `tests/experiments/test_trace_smoke_report_usage.py`

1. Write RED CLI/pipeline tests that require full-plan validation before acquisition or execution and reject missing coverage cells before provider dispatch.
2. Replace the fixed four-batch/eight-task trace adapter with dynamic representative plan/filter execution. Exp1–4 must use the original formal runners and bank-backed adapters with current provider calls/spend equal to zero.
3. Route Exp5's 16 current authority-derived representative condition cells through the original online endpoint runner; do not consume the DeepSeek bank and do not drop endpoints when a credential is absent.
4. Ensure each selected root is in correctness/completion denominators even on wrong answer, parse/checker/verifier rejection, terminal provider error, or missing metric.
5. Accept terminal `completed` or `completed_with_failures`; reject `blocked`, incomplete task accounting, fabricated completion, or a condition lacking a real runner branch.
6. Keep the explicit results-first bypass limited to nonmetric facilities already authorized by the user.
7. Run the five focused test modules and commit only Task 5 files.

### Task 6: Build the capturing-bank representative E2E

**Files:**
- Replace, do not submit verbatim: `tests/experiments/test_results_first_matrix8_e2e.py`
- Create preferred final name: `tests/experiments/test_results_first_full_plan_smoke_e2e.py`
- Modify only if the E2E exposes a production defect: the minimum production file from Tasks 1–5

1. Port `_OfflineMatrixTransport` into a neutral capturing transport and remove all 4F+4L, eight-root, 32-root, and 166-entry assumptions.
2. Construct and completely validate the official full plan before deriving the representative root filters.
3. Capture every dynamically selected acquisition entry, including deliberately wrong answers and required formal replacement slots; assert maximum concurrent calls is within `1..10`.
4. Consume the immutable bank through Exp1–4 original runners and assert current provider calls/spend are zero.
5. Install probes proving both system domains enter `ProtocolEngine`, Factor verifier, and Lean checker. Probe every Exp3 formal fault/rate/death cell and every Exp4 ablation including FULL.
6. Use an offline endpoint transport only for E2E to prove all four Exp5 runner branches and exact identities; this is not real-smoke evidence.
7. Assert dynamic root denominators, terminal accounting, source latency/token/cost/provider identity, correctness/completion, and missing counts/reasons.
8. Run this E2E alone, then the focused regression set from Tasks 1–5. Commit only E2E and necessary fixes.

### Task 7: Independent review and offline acceptance

**Files:** none unless reviewers identify in-scope defects.

1. Dispatch an independent specification reviewer against the user requirements and Tasks 1–6. Fix and re-review every Critical or Important finding.
2. Dispatch an independent code-quality reviewer against the complete implementation range. Fix and re-review every Critical or Important finding.
3. Run fresh offline commands:
   - formal full-plan preflight for all 6,384 roots and all prepared requests;
   - capturing-bank representative E2E;
   - focused regression modules only.
4. Confirm git status contains only intentional tracked changes plus the two untouched legacy local scripts.
5. Do not start provider acquisition until both reviews report `0 Critical / 0 Important` and all offline acceptance commands pass.

### Task 8: Run and audit the real representative smoke

**Files/artifacts:** fresh gitignored output root only; repository state files updated in Task 9 after terminal audit.

1. Inspect only whether `DEEPSEEK_API_KEY` and `SILICONFLOW_API_KEY` or approved gitignored local config are present; never print secret values.
2. Allocate a fresh output root, rebuild and validate the official full-plan snapshot/acquisition bundle, set an explicit hard budget, and verify no provider/smoke process is already running.
3. Create a new 30-minute recovery heartbeat/monitor before the first provider dispatch.
4. Run exactly one DeepSeek acquisition. On interruption, inspect ledger/bank/dispatch intent and reconcile ambiguous slots before any resume; never launch a second concurrent run.
5. Run Exp1–4 from the immutable bank and prove current provider calls/spend are zero while original protocol/verifier/checker/fault/recovery/settlement/metrics execute.
6. Run every formal Exp5 endpoint branch online. If the SiliconFlow credential remains absent, report the external blocker without deleting endpoints or marking the goal complete.
7. Audit terminal artifacts for all selected roots and aggregate actual latency, prompt/completion/total tokens, cost, correctness, completion, provider identity, and missing counts/reasons. Terminal must not be blocked.

### Task 9: Record evidence and hand off

**Files:**
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`

1. Record exact implementation commits, targeted/offline commands, full-plan digests/counts, review results, fresh output paths, provider call/spend counts, terminal status, and metric totals.
2. Update the authoritative experiment design to remove remaining matrix8 or Exp5 v3-selection-as-current wording; perform a second stale-wording review as required by `AGENTS.md`.
3. Update the code map for every production/test entry point changed.
4. Preserve the known unrelated `paper-l1-components` startup baseline failure as a scoped note; do not claim Fast/Full success.
5. Commit the handoff/evidence files only after the real smoke terminal audit is complete.
