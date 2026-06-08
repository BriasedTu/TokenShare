# Session Handoff

## Current Objective

- Goal: Continue feat-003 by preparing Task Graph, State Machines, and Scheduling.
- Current status: `feat-002` is complete. Phase 1 now has concrete protocol base objects, local artifact storage, JSONL event ledger, SQLite rebuildable index, root task registration, tests, and a code-to-spec mapping document. `feature_list.json` now marks `feat-003` as the active in-progress feature. Startup verification now uses the `conda` environment `tokenshare`.
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
- `src/tokenshare/core/models.py`
- `src/tokenshare/core/registration.py`
- `src/tokenshare/core/__init__.py`
- `src/tokenshare/storage/artifacts.py`
- `src/tokenshare/storage/events.py`
- `src/tokenshare/storage/sqlite_index.py`
- `src/tokenshare/storage/__init__.py`
- `tests/core/test_phase1_models.py`
- `tests/storage/test_artifact_store.py`
- `tests/storage/test_event_ledger.py`
- `tests/storage/test_sqlite_index.py`
- `tests/test_phase1_root_registration.py`

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

## Blockers / Risks

- Phase 2 must preserve the same event-ledger-first boundary: state transitions, scheduling decisions, lease changes, and attempt changes should be written to JSONL events.
- Phase 2 agents should read `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` before expanding registration/orchestration code; the memo records what was fixed now and what is intentionally deferred.
- Phase 2 should not implement plugin verification/merge yet, but `TaskGraph`, states, and events must not block later `DecompositionProposal`, `VerificationReport`, `ExpansionDecision`, `MergePlan`, and `MergeRecord`.
- Factorization split strategy, Lean stub fixtures, and structured report fixtures are open design points.
- Real distributed runtime, real chain settlement, production AI APIs, and full Lean proving are explicitly out of scope for V1.

## Next Session Startup

1. Read `AGENTS.md`.
2. Read `feature_list.json` and `progress.md`.
3. Run `.\init.ps1` on Windows or `./init.sh` in Bash. Both default to `conda` env `tokenshare`.
4. For routine repository reads/searches, follow `Doc/agent-navigation.md` section 4: PowerShell plus explicit UTF-8, no default `rg`.
5. If using online research, first follow `Doc/agent-navigation.md` section 6 to download/pull and index the source material.
6. Read `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` before changing registration or orchestration boundaries.
7. Continue feat-003.

## Recommended Next Step

- Start Phase 2 from the TDD sections on `TaskUnit`, `Lease`, and `Attempt` state machines. Add tests first for ready-node scheduling, lease expiry returning tasks to `Ready`, event-ledger recording of all state transitions, and graph APIs that can later accept expansion/merge metadata without embedding plugin domain logic.
