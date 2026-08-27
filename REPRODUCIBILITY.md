# Reproducibility

## Environment

Use Python 3 and install the tracked dependencies in an isolated environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The current skeleton contains the package roots, documentation, and launcher only. Later extraction commits will add the system and experiment implementation, `benchmarks/experiments/`, `configs/experiments/`, `results/experiments/`, `verification/`, and `Doc/Experiments/` before publication.

## Publication-stage checks

The commands below become available after those later extraction commits. They are publication gates, not commands that the current skeleton claims can already run.

Generate the full experiment plan without contacting a provider:

```powershell
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile full --run-id reproducibility-plan
```

Run the focused offline verification entry points:

```powershell
.\.venv\Scripts\python verification/run_verification.py --focused system
.\.venv\Scripts\python verification/run_verification.py --focused experiments
```

These checks use deterministic or fake executors. Do not supply real provider credentials unless a separate run is explicitly authorized.

## Official metrics after result extraction

The official metrics and their verifier will be added by a later extraction commit before publication. Once present, verify them read-only against their manifest and Git index bytes:

```powershell
.\.venv\Scripts\python verification/verify_official_results.py --verify-worktree-index
```

The raw archive status is `pending_advisor_archive_decision`. If a local raw directory is available, pass it explicitly to the verifier with `--raw-root` and `--require-raw`; the verifier must not rewrite either the raw directory or tracked results.
