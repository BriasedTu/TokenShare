# Public Experiment Code Map

This map connects the protocol implementation, experiment entry points, research assets, and verification tools.

## Runtime

- `src/tokenshare/core/`: protocol objects and state types.
- `src/tokenshare/storage/`: JSONL, SQLite, artifact, and replay persistence.
- `src/tokenshare/local_runtime/`: local coordinator, workers, leases, completion scheduling, and projections.
- `src/tokenshare/protocol_engine.py`: retained protocol execution engine.

## Plugins And Executors

- `src/tokenshare/plugins/factorization/`: deterministic factorization task plugin.
- `src/tokenshare/plugins/lean_proof/`: Lean proof plugin, fixture environment helpers, semantic authority checks, and bounded checker integration.
- `src/tokenshare/executors/`: retained deterministic/mock executors, AI provider config, artifacts, descriptors, and urllib transports.

## Experiments

- `src/tokenshare/experiments/`: the only public experiment facility and CLI surface.
- `src/tokenshare/experiments/minif2f.py`: fixed mathematical DAG catalog loading, admission evidence validation, and pinned miniF2F environment support.
- `src/tokenshare/experiments/factorization_exp1_analysis.py`: standalone read-only extractor for root-level and actual range-attempt-level Experiment 1 Factorization analysis tables; it reads an explicitly supplied local raw run and writes only to a separate ignored analysis output directory.
- `tests/experiments/`: offline focused tests for case loading, planning, fake vertical runs, resume behavior, reducer output, terminal failure projection, and public launcher behavior.
- `run_experiments.cmd`: Windows launcher for `tokenshare.experiments.gui`.

## Assets And Results

- `benchmarks/experiments/`: official benchmark corpus, selection metadata, semantic authority sidecar, and Lean fixture project.
- `configs/experiments/`: official provider configurations. These files name secret environment variables but do not contain secrets.
- `results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/`: official frozen run metadata and metrics retained for publication.

## Verification

- `verification/verify_authoritative_corpus.py`: full corpus/config/Lean fixture byte and identity verifier.
- `verification/verify_official_results.py`: read-only verifier for the 12 tracked official result files and optional local raw comparison.
- `verification/verify_extraction.py`: release boundary verifier for freeze tag identity, public paths, corpus, results, and forbidden retained symbols.
- `verification/run_verification.py`: focused entry point for system, experiments, corpus, results, extraction, and fast offline checks.
- `verification/pytest_network_tripwire.py` and `verification/sitecustomize.py`: offline verification guard against accidental production provider calls.
- `verification/pytest_lean_integration.py` and `verification/lean_integration_guard.py`: opt-in Lean integration tests and process guards for default offline verification.
