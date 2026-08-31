# TokenShare

TokenShare is a local research prototype for a protocol system that recursively decomposes, assigns, verifies, merges, settles, and replays large tasks.

This repository has one experiment facility: `tokenshare.experiments`. Its public research assets are organized around four parts:

- the protocol system and its runtime, storage, plugin, and executor boundaries;
- the single `experiments` package and launcher;
- the official benchmark corpus and provider configurations;
- the official metrics retained from the full experiment run after the result extraction stage.

The current public package contains the retained protocol runtime, plugin core, minimal executor closure, official corpus/config files, current experiment implementation, focused tests, and the authoritative corpus verifier. Later extraction stages add the retained official metrics and final publication verification gates.

## Public layout by extraction stage

The current public extraction contains:

- `src/tokenshare/`: the public system package root, including protocol runtime, storage, plugins, executor descriptors/transports, and `tokenshare.experiments`.
- `tests/`: focused offline tests for the retained system, plugins, executors, corpus, and experiments.
- `benchmarks/experiments/`: official benchmark corpus and proof fixtures.
- `configs/experiments/`: official provider configurations without secrets.
- `Doc/Experiments/`: experiment design, metrics, integration, corpus, and public package documentation.
- `verification/`: the authoritative corpus verifier.
- `run_experiments.cmd`: the Windows GUI launcher for `tokenshare.experiments.gui`.
- the root documentation and repository policy files.

Later extraction commits, before publication, will add:

- `results/experiments/`: official result files and their manifest.
- `verification/`: result and extraction gates beyond the current corpus verifier.

## Start here

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The Windows launcher sets the same repository-local `src` import path automatically. Launch the interface with:

```powershell
.\run_experiments.cmd
```

Routine verification must use fake or deterministic executors. Real provider calls require explicit authorization. For repeatable local checks, start with `REPRODUCIBILITY.md`.
