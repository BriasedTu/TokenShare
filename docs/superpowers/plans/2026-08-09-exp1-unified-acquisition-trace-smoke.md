# Exp1 Unified Acquisition → Exp2–4 Trace Smoke Implementation Plan

> **Execution rule:** Use subagent-driven TDD. Non-metric facility gates may be bypassed; protocol core, provider identity, Factor verifier, Lean checker, fault semantics, and metrics may not be bypassed.

**Goal:** Make Exp1 the single approved acquisition source for the Exp2–4 4F+4L smoke matrices, including Exp3 replacement slots, while preserving incorrect answers and reporting real source metrics with zero current provider calls in Exp2–4.

**Architecture:** Reuse the existing response-bank inventory/acquisition/resolver and `PaperTraceRuntimeContext`. Add only a smoke-scoped Exp1 acquisition/profile seam and a results-first trace runner seam; do not create a second replay engine. Fix the Lean parsed-candidate hook before trace integration so mutated candidates are checked by the real Lean checker.

---

### Task 1: Repair Lean parsed-candidate fault ordering

**Files:**
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Test: `tests/experiments/test_lean_paper_adapter.py`

1. Add a failing test proving the current fault mutation happens after the checker or reuses an old checker report.
2. Invoke the typed parsed-candidate hook after AI parsing and before `LeanExecutionBridge`/fixed checker.
3. Assert the replacement candidate is the checker input and receives a fresh report.
4. Assert the coordinator's later hook is idempotent and does not mutate twice.
5. Run only Lean adapter/Exp3 offline targeted tests and commit.

### Task 2: Freeze the Exp1 unified acquisition contract

**Files:**
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Add/modify: `benchmarks/paper/*exp1*acquisition*.json`
- Add/modify: the four matrix8 smoke profiles as required
- Test: response-bank/profile contract tests

1. Add RED tests for one 4F+4L source inventory shared by Exp2–4 and explicit Exp3 replacement slots.
2. Freeze stable request, case, AI-unit, sample-slot and replacement-slot identities.
3. Require identical base source slot identities across Exp2–4 where the same model answer is reused.
4. Freeze the rule that incorrect terminal answers remain terminal bank entries and remain in all applicable denominators.
5. Keep Exp4 FULL as its own protocol execution while sharing only model-response slots.

### Task 3: Add results-first acquisition and trace CLI seam

**Files:**
- Modify the minimum of `paper_response_bank.py`, `run_paper_pipeline.py`, `run_paper_experiments.py`, `paper_smoke.py`, and focused tests.

1. RED: plan an 8-root Exp1 acquisition plus replacement slots and prove existing facility gates block before provider.
2. Add an explicit smoke/results-first facility bypass limited to receipt/publication/evidence gates; keep inventory, provider identity and budget accounting.
3. Import an existing Exp1 attempt only when the full stable inference identity and all object digests match; otherwise leave the slot missing.
4. Acquire only missing slots, once, under the Exp1 acquisition output identity.
5. RED/GREEN: Exp2–4 trace commands must have current provider calls 0 and must reject missing bank slots before protocol dispatch.

### Task 4: Preserve real failures and metrics

**Files:**
- Modify focused smoke/report code only if tests prove a gap.
- Test: `tests/experiments/test_paper_smoke.py` and trace metrics tests.

1. Add RED fixtures with an incorrect Exp1 Factor answer and rejected Lean answer.
2. Prove both are consumed without reacquisition and count as `False`, not `None`.
3. Assert per-root and summary correctness/completion denominators remain 8.
4. Assert source prompt/completion/total tokens, latency and cost are projected from validated bank entries; missing source usage remains `None` with reason, never 0.
5. Assert actual acquisition spend is counted once and Exp2–4 current provider calls stay 0.

### Task 5: Offline 4F+4L integration smoke

1. Use a capturing acquisition transport to create the unified bank with correct, incorrect and replacement samples.
2. Run Exp2, Exp3 and Exp4 against the bank.
3. Assert all 24 roots enter the original adapters/coordinator/`ProtocolEngine`; Factor verifier and Lean checker execute; Exp2–4 conditions/hook effects occur.
4. Assert metrics and fixed denominators, with no provider transport call during trace runs.
5. Request independent spec and code-quality review, then commit.

### Task 6: Real smoke execution

1. Audit the existing Exp1 real output against the new exact inventory; import only exact matches.
2. Acquire missing base/replacement slots once in a fresh Exp1 acquisition root.
3. Run Exp2–4 trace-backed 8-root smoke outputs without provider dispatch.
4. Run Exp5 8-root endpoint smoke only when its configured credential exists.
5. Audit every experiment's roots, correctness/completion, source/current call counts, latency, token and cost; do not run Full.

### Task 7: Handoff

Update `progress.md`, `feature_list.json`, `session-handoff.md`, and `tokenshare_v1_code_map.md` with the exact commands, commits, outputs, blockers and metric totals.
