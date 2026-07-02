# Factorization 500 AI Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible 500-number real-AI factorization benchmark that reports a direct accuracy value.

**Architecture:** Implement this as a Phase 8 experiment extension under `tokenshare.experiments`, not in protocol core. Each input is a deterministic semiprime between 1,000,000 and 1,000,000,000; each run uses `AIAPIExecutor` to persist prompt, raw output, parsed output or parse failure, provenance, usage, and event evidence, then a local evaluator computes correctness.

**Tech Stack:** Python 3.12, existing `AIAPIExecutor`, `ProtocolEngine`, `ArtifactStore`, JSON/JSONL/CSV outputs, pytest.

---

### Task 1: Failing Tests

**Files:**
- Create: `tests/experiments/test_factorization_500_ai.py`

- [x] **Step 1: Write tests for semiprime generation, direct parser/evaluator, suite outputs, and CLI**

Use tests that import the intended API:

```python
from tokenshare.experiments.factorization_500_ai import (
    ScriptedDirectFactorizationTransport,
    evaluate_direct_factorization_answer,
    generate_semiprime_inputs,
    run_factorization_500_ai_suite,
)
from tokenshare.experiments.run_factorization_500_ai import main
```

Check that 500 generated inputs are unique, in range, and have oracle prime factors. Check that wrong products fail. Check that a scripted suite writes `batch_report.json`, `per_number_results.jsonl`, `per_number_summary.csv`, `input_numbers.jsonl`, and `oracle_answers.jsonl`, with `accuracy == 1.0`. Check CLI writes the same report for a small count.

- [x] **Step 2: Run red tests**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\experiments\test_factorization_500_ai.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tokenshare.experiments.factorization_500_ai'`.

### Task 2: Implementation

**Files:**
- Create: `src/tokenshare/experiments/factorization_500_ai.py`
- Create: `src/tokenshare/experiments/run_factorization_500_ai.py`
- Modify: `src/tokenshare/experiments/__init__.py`

- [x] **Step 1: Implement deterministic semiprime generation**

Generate primes with a small sieve, choose deterministic unique `(p, q)` pairs using the seed, require `p > 1000`, `q > 1000`, `1_000_000 < p*q < 1_000_000_000`, and write decimal-string inputs plus oracle factor multisets.

- [x] **Step 2: Implement direct AI request and parser**

Create one `ExecutionRequest` per number. Save `RootInput`/prompt artifacts, require JSON mode, and parse only structured JSON containing `target_n` plus `prime_factors`. Persist parsed candidate output under `factorization.direct_factorization_answer.v1`; parse failures remain parse-failure artifacts through `AIAPIExecutor`.

- [x] **Step 3: Implement evaluator and report writer**

Use deterministic local primality/product/oracle checks to compute `final_correctness`. Write `per_number_results.jsonl`, `per_number_summary.csv`, `batch_report.json`, `input_numbers.jsonl`, `oracle_answers.jsonl`, and `factorization_500_ai_settings.json`. Include direct `accuracy = correct_count / attempted_count`.

- [x] **Step 4: Implement CLI**

Add:

```powershell
conda run -n tokenshare python -m tokenshare.experiments.run_factorization_500_ai --output-root outputs\experiments\factorization_500_ai --count 500 --seed 1 --real-transport
```

Default mode uses scripted transport for tests and local dry runs; `--real-transport` requires a usable gitignored local AI config.

### Task 3: Verification And Docs

**Files:**
- Modify: `README.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `progress.md`
- Modify: `session-handoff.md`
- Modify: `feature_list.json`

- [x] **Step 1: Run targeted tests**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\experiments\test_factorization_500_ai.py -q
```

Expected: pass.

- [x] **Step 2: Run experiment impact tests**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\experiments tests\executors tests\plugins\factorization tests\test_phase7_ai_api_execution_flow.py -q
```

Expected: pass.

- [x] **Step 3: Run real 500-task benchmark**

Run:

```powershell
$env:PYTHONPATH='src'; conda run -n tokenshare python -m tokenshare.experiments.run_factorization_500_ai --output-root outputs\experiments\factorization_500_ai_real --count 500 --seed 1 --real-transport
```

Expected: exit 0 and print a `batch_report.json` body with `attempted_count=500` and `accuracy`.

- [x] **Step 4: Update docs and status**

Record the exact command, output root, `accuracy`, `correct_count`, `parse_failure_count`, `executor_error_count`, total tokens, cost, and latency in `progress.md`, `session-handoff.md`, and `feature_list.json`. Add the new source/test/CLI rows to the Phase 8 code map and README.

- [x] **Step 5: Final verification**

Run:

```powershell
conda run -n tokenshare python -m compileall -q -x "reference_repos" .
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\experiments\test_factorization_500_ai.py tests\experiments\test_ai_profile_suite.py -q
git diff --check
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

Expected: all pass, with only known LF/CRLF warnings from `git diff --check` if present.
