# Experiment 1–5 Matrix Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Every production change follows superpowers:test-driven-development; unexpected behavior follows superpowers:systematic-debugging. Do not run Full.

**Goal:** Execute five real regression smokes, each with four Factorization and four checker-backed Lean roots, while preserving the real protocol core and producing correct latency/token/cost/correctness metrics.

**Architecture:** Extend only the existing `run_paper_experiments` results-first path. Group multiple cases under one canonical condition, add a narrowly classified Exp2 Lean smoke seam without changing its formal Factor-only matrix, add four exact 8-root DeepSeek profiles, reuse the Exp5 v4 profile, and project prompt/completion token metrics from persisted attempts. Execute five isolated outputs sequentially and audit them as one 40-root batch.

**Acceptance boundary:** Relevant RED/GREEN and identity/condition tests plus the real Exp1–5 smoke. No Full, receipt, L1–L4, publication closure, or paper-eligibility gate. ProtocolEngine, adapters, verifier/checker, provider identity and metrics cannot be bypassed.

---

### Task 1: Group multiple smoke roots under one canonical condition

**Files:**
- Modify: `src/tokenshare/experiments/paper_smoke.py`
- Modify: `tests/experiments/test_paper_smoke.py`

- [ ] Add RED tests proving two distinct cases with one selector currently fail at `paper smoke conditions must be unique`, and duplicate condition+case remains invalid.
- [ ] Change resolution to dispatch each condition/binding once while appending ordered unique case IDs to `root_case_filter`.
- [ ] Keep one `ResolvedPaperSmokeItem` per root so direct root count and metric denominator remain unchanged.
- [ ] Change budget commitments to aggregate every selected case under the condition; assert planned root/AI-unit totals cannot be overwritten by a dictionary keyed only by condition.
- [ ] Run only the new nodes and complete `test_paper_smoke.py`; commit the focused change.

### Task 2: Add Exp2 regression-only checker-backed Lean planning

**Files (exact final set determined after RED call-chain proof):**
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py` and/or the narrow smoke planning seam in `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `tests/experiments/test_paper_exp2_scalability.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] Add a RED identity test with an Exp2 Lean smoke item; current result must be `matched 0`.
- [ ] Add a regression-only/paper-ineligible path that reuses the existing canonical Lean selection and creates typed Exp2 Lean conditions for the selected worker counts.
- [ ] Prove normal formal Exp2 expansion remains Factor-only with the same counts and validators.
- [ ] Add a capturing execution test proving the Lean roots call the original Lean adapter, ProtocolEngine and fixed checker, while worker_count is preserved.
- [ ] Run the new nodes plus focused Exp2 condition tests; commit. Do not touch checker or protocol core.

### Task 3: Freeze the five 4×4 smoke matrices

**Files:**
- Create: `benchmarks/paper/paper_smoke_exp1_matrix8_profile.v1.json`
- Create: `benchmarks/paper/paper_smoke_exp2_matrix8_profile.v1.json`
- Create: `benchmarks/paper/paper_smoke_exp3_matrix8_profile.v1.json`
- Create: `benchmarks/paper/paper_smoke_exp4_matrix8_profile.v1.json`
- Reuse unchanged: `benchmarks/paper/paper_smoke_exp5_profile.v4.json`
- Modify: `tests/experiments/test_paper_smoke.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] From current canonical selections freeze four distinct Factor and four distinct Lean case IDs per experiment; never use an ID excluded by the active stable-hash/Lean selection.
- [ ] Freeze selectors according to the design: Exp1 difficulty/topic coverage; Exp2 four worker counts; Exp3 four fault/runtime hooks with omitted regression baseline; Exp4 four ablation hooks; Exp5 official four-member v4 mapping.
- [ ] Add RED profile/identity tests before creating the profiles.
- [ ] Provider-free identity-only must resolve exactly eight items per profile, four per domain, with no duplicate condition+case and no provider calls.
- [ ] Add a 40-root batch inventory assertion across all five profiles. Commit profiles and tests.

### Task 4: Add prompt/completion token metrics without new gates

**Files:**
- Modify: `src/tokenshare/experiments/paper_smoke_report.py`
- Modify: `tests/experiments/test_paper_smoke.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] Add RED tests for row/summary/CSV `prompt_tokens` and `completion_tokens`, complete sums, missing-not-zero behavior, and `total=prompt+completion`.
- [ ] Project only from the same persisted attempts already validated for total tokens/latency/cost; keep schema additive and do not introduce evidence eligibility gates.
- [ ] Extend offline 4×4 capturing smoke assertions for provider attempts, latency, prompt/completion/total, cost, correctness denominator and condition identity.
- [ ] Run smoke report and focused Exp2–4 condition tests; commit.

### Task 5: Provider-free integrated readiness

**Files:**
- Modify only if a discovered facility blocker has a new RED test.

- [ ] Run identity-only for each of the five profiles against a fresh non-created output path; assert eight roots and zero provider calls.
- [ ] Run focused capturing E2E for Exp1–4 and Exp5 preflight; prove system adapters/checkers and Exp2–4 hooks are used.
- [ ] If a non-metric evidence gate blocks, write the smallest RED and bypass only that facility gate. If provider identity, verifier/checker or metrics fail, fix the real path rather than bypass it.
- [ ] Do not run Full. Fast is optional and is not acceptance; run it only if a harness manifest changed and a direct node cannot verify that change.

### Task 6: Execute real Exp1–4 smokes sequentially

**Runtime only; no source edit unless a new RED reproduces a blocker.**

- [ ] Create a batch ID and five fresh `a01` output names under `E:\TokenShareData\outputs\experiments`.
- [ ] For each Exp1–4 profile, first run `--smoke-identity-only --real-transport --unlimited-budget` and freeze execution-plan/budget digests without provider calls.
- [ ] Run the matching real transport command once. Keep the DeepSeek v3 config/model/reasoning/timeout unchanged.
- [ ] After each experiment exits, audit exact eight roots, four domains each, provider artifacts, latency/tokens/cost, correctness/completion, and experiment-specific conditions before starting the next.
- [ ] On network failure apply the 30-minute recovery state machine; never start a duplicate runner for the same output and never blindly resume an ambiguous prepared request.

### Task 7: Execute Exp5 when SiliconFlow credentials are available

**Runtime only; official v4 profile remains unchanged.**

- [ ] Recheck only whether `SILICONFLOW_API_KEY` is set; never print it.
- [ ] Run provider-free identity/preflight with the official cohort, entry map and SiliconFlow provider config.
- [ ] If the key is still absent, record the precise external blocker while keeping Exp1–4 evidence complete; do not substitute provider/model.
- [ ] When available, run the fresh Exp5 output once and validate all four member identities, model order schedule, 4 Factor + 4 Lean roots and metrics.
- [ ] Hard-kill with missing Exp5 schedule is non-resumable; create a new attempt only after auditing dispatch ambiguity.

### Task 8: Final 40-root audit and state update

**Files:**
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`

- [ ] Generate/record one batch inventory referencing all five immutable output roots; do not rewrite runtime evidence.
- [ ] Verify total roots=40, each experiment=8, each domain=4, and all metric sums/rates against per-attempt/per-root artifacts.
- [ ] Verify Exp2 worker counts, Exp3 fault/worker-death hooks, Exp4 ablation hooks and Exp5 model schedule from runtime evidence.
- [ ] Run replay/audit only and prove it makes zero provider calls.
- [ ] Request final read-only code/evidence review. Update Chinese state documents and code map with exact commits, commands, outputs, counts, costs, failures and any credential/network limitation.
- [ ] Do not claim completion until all five real smokes, including Exp5, have completed and the 40-root metrics audit passes.
