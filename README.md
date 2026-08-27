# TokenShare

TokenShare is a local research prototype for a protocol system that recursively decomposes, assigns, verifies, merges, settles, and replays large tasks.

This repository has one experiment facility: `tokenshare.experiments`. Its public research assets are organized around four parts:

- the protocol system and its runtime, storage, plugin, and executor boundaries;
- the single `experiments` package and launcher;
- the official benchmark corpus and provider configurations;
- the official metrics retained from the full experiment run.

## Public layout

- `src/tokenshare/`: protocol system and the `tokenshare.experiments` package.
- `benchmarks/experiments/`: official benchmark corpus and proof fixtures.
- `configs/experiments/`: official provider configurations without secrets.
- `results/experiments/`: tracked official result files and their manifest.
- `verification/`: focused system, experiment, corpus, and result checks.
- `Doc/Experiments/`: experiment design, metrics, integration, corpus, and code-map documentation.

## Start here

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Launch the experiment interface on Windows:

```powershell
.\run_experiments.cmd
```

Routine verification must use fake or deterministic executors. Real provider calls require explicit authorization.
