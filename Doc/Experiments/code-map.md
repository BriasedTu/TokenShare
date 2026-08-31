# Public Experiment Code Map

This map is the public reader's guide to the retained TokenShare experiment extraction. It intentionally names only the clean public paths.

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
