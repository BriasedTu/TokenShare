# TokenShare

TokenShare is a local research prototype for a protocol system that recursively decomposes, assigns, verifies, merges, settles, and replays large tasks.

This repository has one experiment facility: `tokenshare.experiments`. Its public research assets are organized around four parts:

- the protocol system and its runtime, storage, plugin, and executor boundaries;
- the single `experiments` package and launcher;
- the official benchmark corpus and provider configurations;
- the official metrics retained from the full experiment run.

The current skeleton establishes the public package, documentation, and launcher contract. Later extraction commits will add the retained system, experiment implementation, official corpus, official metrics, and verification harness before publication.

## Public layout by extraction stage

The current skeleton contains:

- `src/tokenshare/`: the public package root and minimal `tokenshare.experiments` package.
- `run_experiments.cmd`: the Windows launcher contract.
- the root documentation and repository policy files.

Later extraction commits, before publication, will add:

- the protocol runtime, storage, plugins, executors, and complete experiment implementation under `src/tokenshare/`;
- `benchmarks/experiments/`: official benchmark corpus and proof fixtures.
- `configs/experiments/`: official provider configurations without secrets.
- `results/experiments/`: official result files and their manifest.
- `verification/`: focused system, experiment, corpus, and result checks.
- `Doc/Experiments/`: experiment design, metrics, integration, corpus, and code-map documentation.

## Start here

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The Windows launcher sets the same repository-local `src` import path automatically. After the experiment implementation is added by its later extraction commit, launch the interface with:

```powershell
.\run_experiments.cmd
```

Routine verification must use fake or deterministic executors. Real provider calls require explicit authorization.
