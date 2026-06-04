# AGENTS.md

TokenShare is an early-stage local research prototype for a protocol that recursively decomposes, dispatches, verifies, merges, settles, and replays large tasks. The current target is a Python/SQLite/JSONL implementation that can run two proof-of-concept experiments: factorization and Lean stub proof.

## Startup Workflow

Before writing code:

1. Confirm the working directory is the repository root.
2. Read this file completely.
3. Read the current design sources:
   - `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`
   - `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`
4. Run the baseline verifier:
   - PowerShell: `.\init.ps1`
   - Bash/Git Bash/WSL: `./init.sh`
5. Read `feature_list.json` and pick exactly one unfinished feature.
6. Review `progress.md` and `session-handoff.md` for current state and unresolved decisions.

If baseline verification is failing, repair that before adding new scope.

## Project Boundaries

V1 is a local reproducible protocol kernel, not a production network. Keep the protocol framework separate from task plugins and executors.

In scope for V1:

- Protocol objects, state machines, task graph, leases, attempts, artifact refs, and append-only event ledger.
- Local artifact storage using the filesystem, SQLite, JSON, and JSONL as appropriate.
- Plugin and executor registries with fixed versions.
- Factorization plugin and Lean stub plugin as protocol experiments.
- Simulation profiles for offline, slow, executor_error, invalid_output, and late_submission.
- Metrics report, replay, audit replay, and sandbox settlement.

Out of scope for V1:

- Real blockchain, wallets, smart contracts, or token payments.
- Real distributed networking, HTTP worker pool, or P2P runtime.
- Production identity, permission, anti-Sybil, or Byzantine-fault systems.
- Complete Web UI, dynamic third-party plugin marketplace, production AI API integration, or full Lean theorem proving.

## Working Rules

- One feature at a time: use `feature_list.json` as the source of truth.
- Protocol first: do not hard-code factorization or Lean behavior into the protocol core.
- Evidence required: do not mark a feature done without verification output in `progress.md` or `feature_list.json`.
- Persist nondeterministic outputs: never rely on replaying AI or executor calls during recovery.
- Keep schema/version decisions explicit: event, plugin, and artifact formats must support replay.
- Keep docs and harness current when commands, architecture, or scope changes.

## Definition of Done

A feature is done only when all of the following are true:

- Target behavior is implemented or the design artifact is completed.
- The relevant verification command ran successfully.
- Evidence is recorded in `feature_list.json` or `progress.md`.
- Any changed protocol/event/artifact schema is documented.
- The repository remains restartable from `.\init.ps1` or `./init.sh`.

## Verification Commands

Current startup verification:

```bash
python -c "import json, sqlite3; print('python-json-sqlite-ok')"
python -m compileall .
python -m pytest
```

`init.ps1` and `init.sh` run the first two checks unconditionally. They run `pytest` only when a `tests/` directory exists.

## End of Session

Before ending a session:

1. Update `progress.md` with current state, verification evidence, and next step.
2. Update `feature_list.json` feature status and evidence.
3. Record unresolved risks or decisions in `session-handoff.md`.
4. Leave the repo clean enough for the next session to run the verifier immediately.
