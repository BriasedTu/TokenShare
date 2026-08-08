# Experiment Readiness Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Restore the existing real-experiment runner so it can execute the registered matrix and prove the path with one Factorization plus one Lean real-API smoke while preserving protocol-core verification and all experiment metrics.

**Architecture:** Keep run_paper_experiments as the fast authorized execution path and retain the existing adapters, coordinator, ProtocolEngine, real transport, artifacts, events, and metric projection. Remove only evidence-only facility blockers, make the fixed Lean environment recover from its known stale Lake configuration without bypassing the checker, and add a two-root regression-only smoke profile. The EPD-027 receipt/L1–L4 pipeline remains available for formal publication but is not required by this user-authorized results-first run.

**Tech Stack:** Python 3.12, pytest, PowerShell, JSON/JSONL/SQLite, Lean 4.8/Lake 5, DeepSeek AI API executor.

---

### Task 1: Treat archived implementation-plan bytes as non-blocking provenance

**Files:**
- Modify: src/tokenshare/experiments/paper_pipeline_profile.py
- Modify: tests/experiments/test_paper_pipeline_profile.py

- [ ] **Step 1: Add a failing provenance test**

    def test_archived_implementation_plan_digest_is_nonblocking_provenance() -> None:
        profile = load_paper_pipeline_profile()
        authority = profile.authorities
        assert authority.implementation_plan_path.is_file()
        assert authority.implementation_plan_content_digest.startswith("sha256:")
        assert _file_digest(authority.implementation_plan_path) != (
            authority.implementation_plan_content_digest
        )

- [ ] **Step 2: Run RED**

    $env:PYTHONPATH='src'
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_pipeline_profile.py::test_archived_implementation_plan_digest_is_nonblocking_provenance -q

Expected: FAIL with implementation_plan_content_digest drift.

- [ ] **Step 3: Implement the minimal change**

Remove ("implementation_plan_path", "implementation_plan_content_digest") from exact_raw_pairs; continue resolving and reading that file, validate the declared digest shape, and preserve it in PipelineAuthorities. Do not relax Factorization catalog, Lean catalog, provider config, scale, readiness, model, selection, or request controls.

- [ ] **Step 4: Run GREEN**

Run the RED node and the complete test_paper_pipeline_profile.py. Expected: both exit 0.

- [ ] **Step 5: Commit**

    git add src/tokenshare/experiments/paper_pipeline_profile.py tests/experiments/test_paper_pipeline_profile.py
    git commit -m "fix(experiments): demote archived plan digest gate"

### Task 2: Recover the fixed Lean environment from stale Lake configuration

**Files:**
- Modify: src/tokenshare/plugins/lean_proof/checker.py
- Modify: tests/plugins/lean_proof/test_lean_checker_environment_cache.py

- [ ] **Step 1: Add RED retry tests**

Use the existing _environment_manifest() and monkeypatch checker_module.subprocess.run. Return compiled configuration is invalid for the first Lake call, then valid allowlisted JSON for the second. Assert:

    assert lake_calls[0][1] == "env"
    assert lake_calls[1][1:3] == ["-R", "env"]
    assert environment["LEAN_PATH"] == "C:/lean/path"

Add a negative test returning an unrelated Lake error and assert LeanEnvironmentBootstrapError after exactly one Lake call.

- [ ] **Step 2: Run RED**

Run the two new nodes. Expected: stale-config node fails because only one lake env call occurs; unrelated errors stay fail-closed.

- [ ] **Step 3: Implement one exact retry**

In prepared_lean_environment(), execute the original command first. Only when combined stdout/stderr contains compiled configuration is invalid, rerun with [lake_executable, "-R", "env", ...] using the same cwd, timeout, environment, encoding, capture, and executable identities. Cache only a successful parsed allowlisted environment.

- [ ] **Step 4: Run GREEN and local canary**

    $env:PYTHONPATH='src'
    conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_checker_environment_cache.py -q
    $env:ELAN_HOME="$env:LOCALAPPDATA\TokenShare\LeanToolchain\elan-home"
    & "$env:ELAN_HOME\bin\lake.exe" -R env "$env:ELAN_HOME\bin\lean.exe" --version

Expected: tests pass; canary prints Lean 4.8.0 and exits 0.

- [ ] **Step 5: Commit**

    git add src/tokenshare/plugins/lean_proof/checker.py tests/plugins/lean_proof/test_lean_checker_environment_cache.py
    git commit -m "fix(lean): recover stale Lake environment"

### Task 3: Preserve structured blocked Lean golden evidence

**Files:**
- Modify: src/tokenshare/experiments/paper_runner.py
- Modify: tests/experiments/test_lean_task14_readiness.py

- [ ] **Step 1: Add a RED digest test**

Construct existing blocked evidence produced for environment_error, without split_certificate_ref, and assert:

    body = paper_runner._lean_golden_evidence_digest_body(blocked_evidence)
    assert body["status"] == "blocked"
    assert body["failure_kind"] == "environment_error"
    assert body["provider_calls_made"] == 0
    assert "split_certificate_digest" not in body

- [ ] **Step 2: Run RED**

Expected: FAIL with Lean golden evidence split_certificate_ref is missing.

- [ ] **Step 3: Implement a strict blocked projection**

At the start of _lean_golden_evidence_digest_body, branch only for the existing structured blocked status. Return schema, evidence source, case ID, status, failure kind/message, checker/preflight stages, and provider call count. Keep all existing certificate and authority checks unchanged for passed evidence.

- [ ] **Step 4: Run GREEN and readiness nodes**

Run the new test, test_task14_lean_3x3_matrix_freezes_readiness_and_digests, and test_task14_local_oracle_golden_evidence_runs_without_provider. Expected: all pass after Task 2 repairs Lake bootstrap.

- [ ] **Step 5: Commit**

    git add src/tokenshare/experiments/paper_runner.py tests/experiments/test_lean_task14_readiness.py
    git commit -m "fix(experiments): digest blocked Lean golden evidence"

### Task 4: Add the two-domain real-API smoke profile

**Files:**
- Create: benchmarks/paper/paper_smoke_dual_domain_profile.v1.json
- Modify: tests/experiments/test_paper_smoke.py
- Modify: tests/experiments/test_run_paper_experiments_cli.py

- [ ] **Step 1: Add a RED profile test**

Load the new path and assert exactly two ordered items: factor_v2_easy_001 and checker-backed lean_easy_01, both under exp1_real_ai_feasibility, repeat 0, FULL, DeepSeek baseline, regression_only=true, paper_eligible=false. Run it before creating the profile and confirm file-not-found RED.

- [ ] **Step 2: Create the minimal profile**

Derive catalog identity fields from paper_smoke_exp1_exp4_profile.v2.json, set suite_id=paper_smoke_dual_domain_v1 and expected_root_runs=2, and include:

    {"item_id":"factorization_easy","experiment_id":"exp1_real_ai_feasibility","case_id":"factor_v2_easy_001","repeat_id":0,"condition_selector":{"domain":"factorization","difficulty":"easy","worker_count":10,"fault_type":"none","fault_rate":0.0,"ablation_mode":"FULL","model_entry_id":"deepseek_v4_pro_exp1_baseline"}}

    {"item_id":"lean_simple","experiment_id":"exp1_real_ai_feasibility","case_id":"lean_easy_01","repeat_id":0,"condition_selector":{"domain":"lean_proof","paper_difficulty":"simple","topic_family":"pure_logic","worker_count":10,"fault_type":"none","fault_rate":0.0,"ablation_mode":"FULL","model_entry_id":"deepseek_v4_pro_exp1_baseline"}}

- [ ] **Step 3: Verify identity-only and capturing execution**

Run the profile test and CLI with --smoke-identity-only. Then run a capturing transport test and assert both domains reach the normal coordinator/engine adapters, two roots remain in the metric denominator, and per-attempt usage/latency fields are present.

- [ ] **Step 4: Run Experiment 2–4 regressions**

Run focused tests covering Exp2 worker levels, Exp3 rate faults/worker death, Exp4 mechanism hooks, and real-mode capturing. Expected: 6 passed and no protocol-core change.

- [ ] **Step 5: Commit**

    git add benchmarks/paper/paper_smoke_dual_domain_profile.v1.json tests/experiments/test_paper_smoke.py tests/experiments/test_run_paper_experiments_cli.py
    git commit -m "test(experiments): add dual-domain real smoke profile"

### Task 5: Integrated verification and real smoke

**Files:**
- Modify after evidence exists: progress.md
- Modify after evidence exists: feature_list.json
- Modify after evidence exists: session-handoff.md
- Modify if routing changed: Doc/TechnicalDocument/tokenshare_v1_code_map.md

- [ ] **Step 1: Run focused integration**

Run catalog, pipeline-profile, Lean environment/readiness, smoke CLI, metric projection, and Exp2–4 condition tests. Expected: no failures.

- [ ] **Step 2: Run Fast and Full**

    powershell -NoProfile -ExecutionPolicy Bypass -File .\init.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\init.ps1 -Full

Expected: both exit 0. Any new Full failure cluster requires a fresh failing test and root-cause analysis before further production changes.

- [ ] **Step 3: Run the authorized real API smoke**

    $env:PYTHONPATH='src'
    conda run --no-capture-output -n tokenshare python -m tokenshare.experiments.run_paper_experiments --output-root E:\TokenShareData\outputs\experiments\dual-domain-smoke-20260808 --smoke-profile benchmarks/paper/paper_smoke_dual_domain_profile.v1.json --real-transport --ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json

Expected: exit 0; two planned roots; real provider usage for both domains; Factorization verifier and Lean checker invoked; output contains event/artifact/attempt data plus token, latency, completion, and correctness metrics.

- [ ] **Step 4: Independently audit smoke outputs**

Verify a fresh output root, no secret text, configured DeepSeek provider/model identity, usage totals equal per-attempt sums, metric denominator equals 2, and every successful root has protocol terminal evidence. Do not require L1–L4 or publication lineage for this regression-only smoke.

- [ ] **Step 5: Update state and commit**

Record exact commands, counts, output root, non-secret digests, limitations, and that no external research was used. Commit only reviewed repair/state files.
