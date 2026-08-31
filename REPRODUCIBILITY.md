# Reproducibility

## Environment

Use Python 3 and install the tracked dependencies in an isolated environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The public extraction currently contains the retained system implementation, the `tokenshare.experiments` package, `benchmarks/experiments/`, `configs/experiments/`, `Doc/Experiments/`, focused offline tests, and the authoritative corpus verifier. Later extraction stages add `results/experiments/` and final result/publication verification gates.

## Current focused checks

Generate the full experiment plan without contacting a provider:

```powershell
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile full --run-id reproducibility-plan
```

Verify the authoritative corpus and public path manifest:

```powershell
.\.venv\Scripts\python verification/verify_authoritative_corpus.py
```

Run the focused offline experiment tests:

```powershell
.\.venv\Scripts\python -m pytest tests/experiments -q
```

These checks use deterministic or fake executors. Do not supply real provider credentials unless a separate run is explicitly authorized.

The official result manifest, result verifier, extraction boundary gate, and raw archive instructions are added by the result/publication extraction stages.
