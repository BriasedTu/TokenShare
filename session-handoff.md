# Session Handoff

## Current Objective

- Goal: Continue feat-005 by implementing Verification, Canonical Output, and Expansion.
- Current status: `feat-004` is complete. Phase 2 has concrete protocol-kernel code for `TaskGraph`, `TaskUnit`/`Lease`/`Attempt` state machines, `Scheduler`, `LeaseManager`, Phase 2 event types, SQLite projections, and a minimal top-level `ProtocolEngine` scheduling/heartbeat/lease-expiry flow. Phase 3 now has `PluginDescriptor` / `OutputContract`, `PluginRegistry` / `RegistrySnapshot`, `ExecutorDescriptor` / `ExecutorRegistry`, `ExecutionRequest`, `ExecutionSubmission`, `EnvironmentRef`, `PromptPackage`, `MockAIExecutor`, `DeterministicLocalExecutor`, Phase 3 event types, `Attempt.Running -> Submitted`, and SQLite `registry_snapshots` / `execution_requests` / `execution_submissions` / `executor_statuses` index-only projections. A 2026-06-23 Phase 3 boundary fix now requires submission task/unit/attempt/lease/fencing-token binding before attempt advancement, and narrows scheduler availability to Phase 3 serialized `Available` plus Phase 2 legacy `active`. P01-P22 candidate mechanisms were integrated into the main TDD on 2026-06-23, so the main TDD is the implementation authority for Phase 4 through Phase 7. The active feature is `feat-005`; Phase 4 implementation code has not started. Startup verification uses the `conda` environment `tokenshare`.
- Branch / commit: Check with `git status` and `git log --oneline -5`.

## Completed This Session

- [x] Located project documents under `Doc/TechnicalDocument`.
- [x] Extracted current stage: local reproducible Python/SQLite/JSONL research prototype.
- [x] Identified V1 boundaries: protocol kernel first; factorization, Lean stub, and structured report stub as plugins.
- [x] Created startup harness artifacts.
- [x] Passed startup verification.
- [x] Passed structural harness validation with 100/100 score.
- [x] Rewrote `README.md` in Chinese with project definition, V1 scope, non-goals, quick start, repository map, workflow, and current status.
- [x] Researched comparable systems and updated the main TDD with a V1 technology-stack decision.
- [x] Kept Airflow, Argo Workflows, Temporal, Ray, BOINC, SQLite, and Python as design references rather than V1 runtime dependencies.
- [x] Created `reference_repos/` with shallow / sparse source snapshots for Temporal Python SDK, Luigi, cwltool, Prefect, and Dagster package-layout research.
- [x] Updated startup verification so `compileall` excludes `reference_repos/`.
- [x] Created initial `src/tokenshare` package skeleton and mirrored `tests` skeleton.
- [x] Updated startup verification so pytest runs as `PYTHONPATH=src python -m pytest tests`, now that `tests/` exists.
- [x] Added `Doc/agent-navigation.md` to route future agents to the right docs, modules, and reference repos.
- [x] Moved agent navigation out of `Doc/TechnicalDocument/` so the technical design directory stays focused on design materials.
- [x] Added `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` to distinguish protocol object names, future Python class names, object fields, JSON keys, event types, and SQLite materialized-index table names.
- [x] Updated `Doc/agent-navigation.md` so future agents use the new field spec before implementing Phase 1 objects.
- [x] Implemented `ArtifactRef`, `ProtocolConfig`, `TaskSpec`, `TaskUnit`, `TaskRelation`, and `ClientRecord` in `src/tokenshare/core/models.py`.
- [x] Implemented Phase 1 root task registration in `src/tokenshare/core/registration.py`.
- [x] Implemented local `ArtifactStore` in `src/tokenshare/storage/artifacts.py`.
- [x] Implemented JSONL `LedgerEvent` / `EventLedger` in `src/tokenshare/storage/events.py`.
- [x] Implemented rebuildable SQLite materialized indexes in `src/tokenshare/storage/sqlite_index.py`.
- [x] Added tests for Phase 1 protocol objects, artifact storage, event ledger, SQLite rebuild, and root task registration.
- [x] Added `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md` to map Phase 1 code back to the field spec.
- [x] Created the `conda` environment `tokenshare` with Python 3.12.13 and pytest 9.0.3.
- [x] Added `requirements.txt` as the pip-installable Python dependency list.
- [x] Updated `init.ps1` and `init.sh` to run through `conda run -n tokenshare python` by default.
- [x] Researched LangGraph, AutoGen, CrewAI, LlamaIndex, DSPy Assertions, Decomposed Prompting, Tree of Thoughts, and Graph of Verification, then updated the main TDD to include structured decomposition proposals, layered AI-text validation, `MergePlan`, `MergeRecord`, and a structured report stub experiment.
- [x] Synced AGENTS, README, agent navigation, feature roadmap, progress, and historical draft wording so the three-experiment V1 scope is consistent.
- [x] Added a mandatory external-research materialization workflow: project-impacting online research must be saved locally and indexed before it is treated as design evidence.
- [x] Added a mandatory PowerShell/UTF-8 workflow: routine repository reads and searches should use PowerShell with explicit UTF-8, and `rg` should not be the default search tool for this Windows workspace.
- [x] Corrected stale documentation wording in the TDD, agent navigation, and historical draft without adding a Phase 2 spec.
- [x] Replaced the incorrect dependency note file with pip-installable `requirements.txt`.
- [x] Audited and fixed the `TaskState` boundary: removed `Leased` and `Verifying` from the TaskUnit lifecycle enum because lease validity belongs to `Lease` and verification progress belongs to `Attempt`.
- [x] Fixed `EventLedger.append()` idempotency conflict handling: duplicate `idempotency_key` now returns the old event only when event type, object, task, and canonical payload match.
- [x] Narrowed `tokenshare.core.__init__` so the protocol-core package entry no longer re-exports the Phase 1 storage coordinator `RootTaskRegistrar`.
- [x] Added and indexed `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` so future agents see the Phase 2 orchestration-boundary debt before extending registration code.
- [x] Added and indexed `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md` to define Phase 2 `TaskGraph`, `TaskUnitStateChange`, `Lease`, `Attempt`, `SchedulingDecision`, `RecoveryAction`, event ordering, SQLite projections, module boundaries, and natural-language artifact handling.
- [x] Implemented Phase 2 minimal protocol-kernel code: `Lease` / `Attempt` objects and enums, `TaskGraph` ready/dependency/cycle checks, Phase 2 state machines, FIFO `Scheduler`, `LeaseManager` claim/heartbeat/expiry recovery, Phase 2 event types, SQLite `leases` / `attempts` / `recovery_actions` projections, and top-level `ProtocolEngine` event-backed scheduling, heartbeat, and lease expiry flows.
- [x] Added `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` to map Phase 2 code, tests, and specification sections.
- [x] Stabilized Phase 2 scheduling and lease invariants before Phase 3: added regression tests for created-at FIFO ordering, early lease expiry rejection, late heartbeat rejection, and duplicate active lease prevention via event-ledger projection.
- [x] Updated `README.md` current status and repository map after Phase 2 completion; updated `Doc/agent-navigation.md` date and rechecked stale Phase 2 status wording.
- [x] Recorded Phase 3 pre-start boundary debts in `progress.md`, `session-handoff.md`, and `feature_list.json`: keep `RootTaskRegistrar` frozen or migrate it before Phase 3 orchestration, and move scheduler client availability strings into an explicit `ExecutorRegistry` / client contract.
- [x] Added and expanded `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`: P01-P22 are integrated by mechanism into the existing structure, with 109 unique normative definitions, 21 invariants, and 27 decision records. P13-P15 remain future weak-verification models; P16-P18 define plugin-local solving and auditable tool-use boundaries; P19-P21 constrain environment-bound Lean/benchmark reproducibility; P22 informs deterministic explainable assignment without requiring full Contract Net negotiation.
- [x] Reorganized the full P01-P22 candidate specification by protocol lifecycle rather than paper intake order. Stable requirement, invariant, and decision IDs are unchanged; invariants, rejected designs, decision records, adjudication order, TDD guidance, and conclusions now use thematic/phase grouping.
- [x] Corrected the paper map's prior PBFT-to-verifier-committee wording: `3f+1`/`2f+1` thresholds require the deterministic Byzantine state-machine-replication model and must not be treated as AI/semantic-answer truth thresholds.
- [x] Integrated the P01-P22 recommendations into the main TDD and synced indexes: main TDD now covers expected output/resolution, requirements/hints, capability snapshot, `EnvironmentRef`, action/observation provenance, verification/selection separation, deterministic allocation, replay without historical re-execution, merge-as-task lifecycle, final settlement, and reproducible experiment fields.
- [x] Recorded the current Phase 3 field discussion in `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`: use `ExecutionRequest` / `ExecutionSubmission` as the execution-loop backbone, persist both request and submission bodies as artifacts, keep events to refs/digests/index summaries, and advance `Attempt.Running -> Submitted` after a recorded submission without entering verification or canonical selection.
- [x] Closed the remaining Phase 3 field decisions in that draft: inline `AllocationDecision` in `ExecutionRequest`, store `PluginDescriptor` / `ExecutorDescriptor` bodies as artifacts, use `Available` / `Busy` / `Offline` / `Disabled` executor status, and keep SQLite Phase 3 projections index-only.
- [x] Audited code maps against the current codebase as source of truth. Updated `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md` and `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` to cover existing but previously unmapped registration envelopes, `ArtifactStore.save_json()`, `LeaseClaim`, `LeaseExpiryDecision`, `SchedulingFlowResult`, `LeaseHeartbeatFlowResult`, `LeaseExpiryFlowResult`, active lease ledger projection helpers, and current source/test inventories.
- [x] Implemented Phase 3 Plugin and Executor Contracts with TDD: artifact-backed registry freeze, explicit executor status contract, unified request/submission dataclasses, mock AI and deterministic executor boundaries, Phase 3 events, `Attempt.Running -> Submitted`, and SQLite index-only projections.
- [x] Added `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` to map Phase 3 code, tests, and specification sections.
- [x] Fixed Phase 3 boundary review findings: mismatched submission artifacts/events remain audit-only and do not advance unrelated attempts; scheduler availability now accepts serialized `Available` and legacy `active` only. Updated the Phase 3 field spec and code map with the binding and status-contract behavior.

## Verification Evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Startup verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Python json/sqlite check, harness file check, compileall, pytest skipped because no tests directory yet. |
| Harness validation | `node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` | Passed | Overall 100/100. Structural check only. |
| README update verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Re-run after README rewrite; Python json/sqlite and harness checks passed, pytest skipped because no tests directory yet. |
| Tech stack TDD update verification | `.\init.ps1` | Passed | Re-run after TDD/progress/handoff/feature evidence update; Python json/sqlite and harness checks passed, pytest skipped because no tests directory yet. |
| Reference source verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Re-run after adding `reference_repos/`; compileall excludes external reference source, Python json/sqlite and harness checks passed. |
| Package layout verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Python json/sqlite and harness checks passed; pytest ran `tests\test_package_layout.py` with `1 passed`. |
| Agent navigation verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Re-run after adding agent navigation document; pytest ran `tests\test_package_layout.py` with `1 passed`. |
| Harness validation | `node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` | Passed | Overall 100/100. |
| Agent navigation relocation verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Re-run after moving navigation to `Doc/agent-navigation.md`; pytest ran `tests\test_package_layout.py` with `1 passed`. |
| Harness validation after relocation | `node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` | Passed | Overall 100/100. |
| Phase 1 field spec verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Re-run after adding field spec and navigation index; pytest ran `tests\test_package_layout.py` with `1 passed`. |
| Harness validation after field spec | `node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` | Passed | Overall 100/100. |
| Phase 1 TDD red check | `PYTHONPATH=src python -m pytest tests/core/test_phase1_models.py tests/storage/test_artifact_store.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/test_phase1_root_registration.py -q` | Failed as expected | Missing Phase 1 modules: `tokenshare.core.models`, `tokenshare.storage.artifacts`, `tokenshare.storage.events`. |
| Phase 1 targeted verification | `PYTHONPATH=src python -m pytest tests/core/test_phase1_models.py tests/storage/test_artifact_store.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/test_phase1_root_registration.py -q` | Passed | `6 passed in 0.41s`. |
| Phase 1 full startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.21s`. |
| Conda environment check | `conda run -n tokenshare python -c "import sys, sqlite3; print(sys.version); print(sqlite3.sqlite_version)"` | Passed | Python 3.12.13; SQLite 3.51.2. |
| PowerShell conda startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | Output included `Using conda environment: tokenshare`, `harness-files-ok`, and `7 passed in 0.17s`. |
| Bash conda startup verification | `bash ./init.sh` | Passed | Output included `Using conda environment: tokenshare`, `harness-files-ok`, and `7 passed in 0.21s`. |
| Semantic decomposition doc verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.17s`. |
| Documentation consistency search | stale two-experiment/two-plugin wording, old Phase 1 status, old date, and factorization/Lean-only scope wording | Passed | No conflicting matches outside intentional historical context. |
| Bash verification after doc update | `bash ./init.sh` | Passed | pytest collected 7 items; `7 passed in 0.18s`. |
| External research workflow verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.17s`. |
| External research workflow Bash verification | `bash ./init.sh` | Passed | pytest collected 7 items; `7 passed in 0.18s`. |
| PowerShell/UTF-8 workflow verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.20s`. |
| Stale wording correction review | searched old paper archive path, two-plugin wording, old Phase 1 layout wording, stale task wording, and Phase 2 spec terms | Passed | No remaining targeted stale wording; no Phase 2 spec was added. |
| Stale wording correction verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.17s`. |
| Requirements install check | `conda run -n tokenshare python -m pip install -r requirements.txt` | Passed | `pytest==9.0.3` already satisfied. |
| TaskState boundary regression | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py -q` | Passed | `2 passed in 0.04s`. |
| TaskState related targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py tests\test_phase1_root_registration.py tests\storage\test_sqlite_index.py -q` | Passed | `4 passed in 0.17s`. |
| TaskState boundary full startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 8 items; `8 passed in 0.19s`. |
| EventLedger idempotency red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_event_ledger.py -q` | Failed as expected after import-cycle fix | New conflict test initially failed with `DID NOT RAISE <class 'ValueError'>`. First run also exposed a `tokenshare.core.__init__` / storage circular import from eager registrar export. |
| EventLedger idempotency targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_event_ledger.py -q` | Passed | `3 passed in 0.06s`. |
| Core model and package targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py tests\test_package_layout.py -q` | Passed | `3 passed in 0.06s`. |
| Coordination-boundary full startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 9 items; `9 passed in 0.20s`. |
| Requirements correction startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 7 items; `7 passed in 0.22s`. |
| Phase 2 spec startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 9 items; `9 passed`. |
| Phase 2 implementation-start decision verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 9 items; `9 passed`. |
| Phase 2 TDD red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_task_graph.py tests\core\test_state_machines.py tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\storage\test_phase2_event_projection.py tests\test_phase2_scheduling_flow.py -q` | Failed as expected | Missing Phase 2 modules/objects: `tokenshare.core.task_graph`, `tokenshare.core.scheduling`, `tokenshare.core.leases`, `Lease` / `Attempt`, and `tokenshare.protocol_engine`. |
| Phase 2 heartbeat red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase2_scheduling_flow.py -q` | Failed as expected | `ProtocolEngine.record_lease_heartbeat` was missing. |
| Phase 2 targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_task_graph.py tests\core\test_state_machines.py tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\storage\test_phase2_event_projection.py tests\test_phase2_scheduling_flow.py -q` | Passed | `9 passed in 0.22s`. |
| Phase 2 full startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | pytest collected 18 items; `18 passed`. |
| Phase 2 stabilization red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\test_phase2_scheduling_flow.py -q` | Failed as expected | New regressions exposed FIFO insertion-order behavior, early expiry, late heartbeat revival, and duplicate active lease creation. |
| Phase 2 stabilization targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\test_phase2_scheduling_flow.py -q` | Passed | `7 passed in 0.18s`. |
| Phase 2 stabilization full startup verification | `.\init.ps1` | Passed | pytest collected 21 items; `21 passed in 0.38s`. |
| Phase 3 pre-start boundary note verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | Recorded the RootTaskRegistrar legacy/migration constraint and scheduler availability-state contract constraint; pytest collected 18 items; `18 passed in 0.42s`. |
| P01-P07 candidate specification verification | `.\init.ps1` | Passed | Candidate document structure audit passed; startup checks passed and pytest collected 18 items, `18 passed in 0.43s`. |
| P01-P12 integrated candidate specification audit | PowerShell marker/ID/placeholder audit | Passed | All P08-P12 markers are present; 92 unique normative definitions, 16 invariants, 20 unique decisions, no duplicate definitions/decisions or placeholders. |
| P01-P12 integrated candidate specification verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | Startup checks passed and pytest collected 18 items, `18 passed in 0.35s`. |
| P01-P22 integrated candidate specification audit | PowerShell marker/ID/placeholder audit | Passed | All P13-P22 markers are integrated; 109 unique normative definitions, 21 invariants, 27 unique decisions, no duplicate definitions/decisions or placeholders. |
| P01-P22 integrated candidate specification verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | Startup checks passed and pytest collected 18 items, `18 passed in 0.36s`. |
| P01-P22 thematic reorganization audit and verification | PowerShell structure audit + `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | 109 unique requirements, 21 unique invariants, 27 unique decisions, all 27 adjudication-order references present, no paper-batch revision traces; pytest `18 passed in 0.57s`. |
| P01-P22 main TDD integration verification | `.\init.ps1` | Passed | Main TDD and indexes synced; startup checks passed and pytest collected 18 items, `18 passed in 0.33s`. |
| Phase 3 field draft verification | JSON parse + placeholder scan + `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | `feature_list.json` parsed; new Phase 3 draft had no `TODO`/`TBD`/`FIXME`/`待填写`; pytest collected 21 items, `21 passed in 0.39s`. |
| Phase 3 implementation-start readiness check | JSON parse + stale-decision scan + index check + boundary scan + `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | `feature-list-json-ok`, `phase3-spec-index-ok`, stale decision scans clean; pytest collected 21 items, `21 passed in 0.41s`. |
| Code map source-of-truth audit | PowerShell + Python AST/Markdown scan | Passed | Scanned current `src/tokenshare/` and `tests/`; all code-map `src/` / `tests/` / `Doc/` path references exist, and missing mappings were added. |
| Code map audit startup verification | `powershell -ExecutionPolicy Bypass -File .\init.ps1` | Passed | Startup checks passed and pytest collected 21 items, `21 passed in 0.35s`. |
| Phase 3 TDD red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` | Failed as expected | Missing Phase 3 modules including `tokenshare.executors.contracts` and `tokenshare.executors.registry`. |
| Phase 3 deterministic executor red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_deterministic_executor.py -q` | Failed as expected | Missing `tokenshare.executors.deterministic`. |
| Phase 3 targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_state_machines.py tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\executors\test_deterministic_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` | Passed | `9 passed in 0.53s`. |
| Phase 3 full startup verification | `.\init.ps1` | Passed | Startup checks passed and pytest collected 28 items, `28 passed`. |
| Phase 3 boundary-fix red check | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\test_phase3_execution_flow.py -q` | Failed as expected | New tests exposed legacy `ready` status being scheduled and missing lease/fencing-token binding in `record_execution_submission()`. |
| Phase 3 boundary-fix targeted verification | `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\test_phase3_execution_flow.py -q` | Passed | `6 passed in 0.18s`. |
| Phase 3 boundary-fix full startup verification | `powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` | Passed | Startup checks passed and pytest collected 30 items, `30 passed in 0.67s`. |

## Files Changed

- `AGENTS.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`
- `requirements.txt`
- `init.sh`
- `init.ps1`
- `README.md`
- `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`
- `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`
- `.gitignore`
- `reference_repos/README.md`
- `src/tokenshare/`
- `tests/`
- `Doc/agent-navigation.md`
- `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`
- `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md`
- `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md`
- `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`
- `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`
- `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`
- `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`
- `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`
- `src/tokenshare/core/models.py`
- `src/tokenshare/core/registration.py`
- `src/tokenshare/core/__init__.py`
- `src/tokenshare/core/task_graph.py`
- `src/tokenshare/core/state_machines.py`
- `src/tokenshare/core/scheduling.py`
- `src/tokenshare/core/leases.py`
- `src/tokenshare/protocol_engine.py`
- `src/tokenshare/plugins/contracts.py`
- `src/tokenshare/plugins/registry.py`
- `src/tokenshare/executors/contracts.py`
- `src/tokenshare/executors/registry.py`
- `src/tokenshare/executors/mock_ai.py`
- `src/tokenshare/executors/deterministic.py`
- `src/tokenshare/storage/artifacts.py`
- `src/tokenshare/storage/events.py`
- `src/tokenshare/storage/sqlite_index.py`
- `src/tokenshare/storage/__init__.py`
- `tests/__init__.py`
- `tests/phase2_fixtures.py`
- `tests/phase3_fixtures.py`
- `tests/core/test_phase1_models.py`
- `tests/core/test_task_graph.py`
- `tests/core/test_state_machines.py`
- `tests/core/test_scheduler.py`
- `tests/core/test_lease_manager.py`
- `tests/plugins/test_plugin_registry.py`
- `tests/executors/test_executor_registry.py`
- `tests/executors/test_mock_ai_executor.py`
- `tests/executors/test_deterministic_executor.py`
- `tests/storage/test_artifact_store.py`
- `tests/storage/test_event_ledger.py`
- `tests/storage/test_phase2_event_projection.py`
- `tests/storage/test_phase3_event_projection.py`
- `tests/storage/test_sqlite_index.py`
- `tests/test_phase1_root_registration.py`
- `tests/test_phase2_scheduling_flow.py`
- `tests/test_phase3_execution_flow.py`

## Decisions Made

- Treat Python 3.12+ as the V1 implementation language because README states Python/SQLite/JSONL and the local baseline is Python 3.12.4.
- Use SQLite only for rebuildable indexes, registry snapshots, run metadata, and query views; JSONL event ledger plus artifact files remain authoritative for replay.
- Do not adopt Airflow, Argo Workflows, Temporal, Ray, Celery, Prefect, PostgreSQL, Redis, Kubernetes, or queues as V1 runtime dependencies.
- Run pytest only against TokenShare's `tests/` directory, with `PYTHONPATH=src`; external reference repos are excluded from discovery.
- Keep `reference_repos/` as ignored external reference material; do not treat it as TokenShare runtime code.
- Use `src/` package layout with `tokenshare.core`, `tokenshare.storage`, `tokenshare.plugins`, `tokenshare.executors`, `tokenshare.replay`, and `tokenshare.experiments`.
- Use `Doc/agent-navigation.md` before deciding module ownership or using `reference_repos/`.
- Use the TDD implementation phases as the initial feature roadmap.
- Use `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` as the Phase 1 field contract before writing `TaskSpec`, `TaskUnit`, `ArtifactRef`, `LedgerEvent`, `EventLedger`, or SQLite index code.
- Keep `ArtifactRef` in `tokenshare.core` as a protocol reference object; filesystem behavior stays in `tokenshare.storage.artifacts`.
- Use `RootTaskRegistrar` as a narrow Phase 1 coordinator until the later `ProtocolEngine` / `TaskGraph` work exists.
- SQLite index tables are rebuilt from `LedgerEvent` payloads and are not authoritative state.
- Use `conda run -n tokenshare python` as the default verification runtime. The env can be overridden with `TOKENSHARE_CONDA_ENV`.
- V1 now has three experiment families: factorization, Lean stub, and structured report stub. The third exists to test natural-language decomposition, weak verification, evidence coverage, and `MergePlan` merging.
- Any online research that affects TokenShare design, code, tests, or docs must be materialized locally before being relied on: papers/reports are indexed in the paper map, open-source projects are pinned in `reference_repos/README.md`, and ordinary online docs record source URL, access date, local summary, and impact scope.
- Routine file reading/searching in this workspace should use PowerShell with explicit UTF-8, for example `Get-Content -Encoding UTF8`, `Get-ChildItem`, and `Select-String`; avoid using `rg` as the default repository search path.
- `TaskUnit.state` is only a coarse node lifecycle state. Do not put lease validity or attempt verification progress into `TaskState`; use `Lease` and `Attempt` state machines for those details.
- `EventLedger` treats `idempotency_key` as retry dedupe, not conflict overwrite. Same key with different event type, object, task, or canonical payload is an audit conflict and must fail.
- `RootTaskRegistrar` remains a narrow Phase 1 compatibility coordinator. Phase 2 should put TaskGraph / Scheduler / LeaseManager / attempt orchestration into a separate orchestration layer or `ProtocolEngine`, not into `tokenshare.core.registration`.
- Phase 2 field/state/event choices are now explicit: `TaskGraph` is a rebuildable view, lease expiry supersedes the running attempt instead of marking it failed, and `SchedulingDecision` is embedded in lease/attempt events for the minimal version.
- AI or natural-language raw output must enter the protocol as artifacts referenced by `ArtifactRef`; event payloads should carry structured summaries and refs, not long raw text.
- Phase 2 is still protocol-kernel development only: no plugin implementation, executor/processing endpoint, AI call, submission verification, canonical binding, expansion, merge, or settlement. Do not add `max_parallel_attempts_per_unit` yet; use `allow_shadow_execution=false` to mean one active lease per unit in the minimal version. Every successful heartbeat should append a `LEASE_STATE_CHANGED Active -> Active` event.
- Phase 2 implementation boundary is now concrete: `tokenshare.core` contains pure objects/rules/decisions, `tokenshare.storage` contains append-only events and rebuildable projections, and top-level `tokenshare.protocol_engine.ProtocolEngine` is the minimal storage-writing application service. `RootTaskRegistrar` was not expanded.
- Phase 2 stabilization decision: `Scheduler` FIFO means oldest `TaskUnit.created_at` first with `unit_id` tie-break; `LeaseManager.expire()` requires `now >= expires_at`; `LeaseManager.heartbeat()` requires `now < expires_at`; `ProtocolEngine.schedule_ready_unit()` must derive active lease state from `EventLedger` before accepting a new claim, with caller-supplied active lease maps only as additional context.
- P01-P22 candidate mechanisms are now resolved at TDD level: the candidate specification is a provenance/integration record, while `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` is the implementation authority.
- Phase 3 implementation boundary is now concrete: `ExecutionRequest` and `ExecutionSubmission` are artifact-backed protocol records, event payloads carry only refs/digests/index summaries, and a valid recorded submission advances the attempt lifecycle from `Running` to `Submitted` only. Verification, canonical binding, merge, and settlement stay in later phases.
- Phase 3 submission advancement requires the recorded submission to match the current running attempt's task, unit, attempt, lease, and fencing token; mismatches are audit-only `EXECUTION_SUBMISSION_RECORDED` events.
- Scheduler status matching now preserves the layer boundary by accepting the serialized Phase 3 status value `Available` without importing executor modules into `tokenshare.core`, while keeping only `active` as the Phase 2 legacy compatibility status.

## Blockers / Risks

- Phase 4 must keep the protocol-core boundary: plugin verification rules may be orchestrated through plugin contracts, but factorization, Lean stub, and structured report rules should not be hard-coded into `tokenshare.core`.
- Phase 4 should read `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` before using request/submission objects, so verification and canonical binding align with the Phase 3 artifact-backed flow.
- Phase 4 should not implement merge, contribution, settlement, real distributed executor networks, or production AI APIs; those remain later features or out of V1 scope.
- Phase 2/3 event-ledger-first behavior must be preserved: verification and canonical binding should read persisted artifact refs and write new artifact/event evidence, not recalculate historical executor output.
- Phase 4 must enforce one canonical output bundle per `TaskUnit`; duplicate binding should fail and remain auditable.
- Expansion must record a structured `DecompositionProposal` and `ExpansionDecision` before mutating the task graph; invalid expansion must not partially write child nodes or edges.
- Phase 3 has an explicit `ExecutorRegistry` / `ExecutorStatus` contract, and Phase 2 `Scheduler` now accepts only serialized `Available` plus legacy `active`; do not do a broader scheduler/runtime migration inside Phase 4 unless it is explicitly scoped and tested.
- Factorization split strategy, Lean stub fixtures, and structured report fixtures are open design points.
- Real distributed runtime, real chain settlement, production AI APIs, and full Lean proving are explicitly out of scope for V1.

## Next Session Startup

1. Read `AGENTS.md`.
2. Read `feature_list.json` and `progress.md`.
3. Run `.\init.ps1` on Windows or `./init.sh` in Bash. Both default to `conda` env `tokenshare`.
4. For routine repository reads/searches, follow `Doc/agent-navigation.md` section 4: PowerShell plus explicit UTF-8, no default `rg`.
5. If using online research, first follow `Doc/agent-navigation.md` section 6 to download/pull and index the source material.
6. Read `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` before using Phase 2 scheduling, lease, attempt, or projection code; it now includes the 2026-06-23 stabilization invariants and verification evidence.
7. The Phase 1 and Phase 2 code maps were re-audited against current code on 2026-06-23; if code changes again, repeat the same code-as-source audit before relying on them.
8. Read `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` and `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` before writing Phase 4 tests or code.
9. Keep the Phase 4 risks from `Blockers / Risks` in scope while implementing verification, canonical binding, and expansion.
10. Read the main TDD sections 4.3, 8, 9, 10, 12, and 21 before writing Phase 4 tests or code.
11. Use `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md` only when tracing the source of a decision, not as a second implementation spec.
12. Continue feat-005.

## Recommended Next Step

- Start Phase 4 TDD from persisted `ExecutionSubmission` artifacts: add verification report tests, single canonical output binding tests, and invalid expansion no-mutation tests before writing implementation code.
