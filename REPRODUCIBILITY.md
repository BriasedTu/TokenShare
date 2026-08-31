# Reproducibility

## Environment

Use Python 3 and install the tracked dependencies in an isolated environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The public extraction contains the retained system implementation, the `tokenshare.experiments` package, `benchmarks/experiments/`, `configs/experiments/`, `Doc/Experiments/`, `results/experiments/`, focused offline tests, and final result/publication verification gates.

## Current focused checks

Generate the full experiment plan without contacting a provider:

```powershell
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile full --run-id reproducibility-plan
```

Verify the authoritative corpus and public path manifest:

```powershell
.\.venv\Scripts\python verification/verify_authoritative_corpus.py
```

Verify the retained official result files against their byte sizes, SHA-256 digests, row counts, and Git index blobs:

```powershell
.\.venv\Scripts\python verification/verify_official_results.py --verify-worktree-index
```

Verify the clean extraction boundary, frozen tag identity, corpus, and result gates together:

```powershell
.\.venv\Scripts\python verification/verify_extraction.py
```

Run the focused offline experiment tests:

```powershell
.\.venv\Scripts\python -m pytest tests/experiments -q
```

These checks use deterministic or fake executors. Do not supply real provider credentials unless a separate run is explicitly authorized.
