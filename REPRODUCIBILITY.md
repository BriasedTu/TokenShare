# Reproducibility

This guide separates offline repository verification from experiment execution. The public checkout includes benchmark inputs, proof admission evidence, and configurations. Experiment outputs, result tables, reports, original provider traces, and prepared Lean installations are maintained locally and are not included in a clone.

## Set up

Use Python 3.12. Corpus integrity checks read the public file hashes and complete identity arrays; they do not require historical Git objects or tags.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The examples use PowerShell. On macOS/Linux, substitute `.venv/bin/python` and set `PYTHONPATH` with `export PYTHONPATH="$PWD/src"`.

## Offline verification

Run the checks relevant to the change:

```powershell
# Fast regression selection.
.\.venv\Scripts\python verification/run_verification.py --focused fast

# Protocol, storage, runtime, plugins, executors, and verification guards.
.\.venv\Scripts\python verification/run_verification.py --focused system

# Corpus integrity, full experiment planning, and experiment regression tests.
.\.venv\Scripts\python verification/run_verification.py --focused experiments

# Read-only hashes, sizes, and row counts for an explicitly supplied local result directory.
$resultDir = 'C:\path\to\local\result-directory'
.\.venv\Scripts\python verification/run_verification.py --focused results --results-root $resultDir

# Public repository boundaries and corpus identities.
.\.venv\Scripts\python verification/run_verification.py --focused extraction
```

The corpus verifier compares complete ordered identity arrays and asset SHA-256 values, including the miniF2F supplement. It can also be run directly:

```powershell
.\.venv\Scripts\python verification/verify_authoritative_corpus.py
```

Focused tests use deterministic or fake executors and a provider-network guard. Default pytest and focused verification block Lean/Lake execution; tests requiring a real compiler are skipped unless explicitly enabled.

## Inspect experiment plans

Planning requires neither credentials nor a prepared Lean compiler:

```powershell
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile full --run-id reproducibility-plan
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile minif2f --run-id minif2f-plan
```

The miniF2F profile selects only Experiment 1. See the [experiment guide](Doc/Experiments/README.md) for experiment semantics and the [supplement](Doc/Experiments/minif2f-supplement.md) for its pinned environment and admission contract.

## Lean integration

Prepare the fixture's locked toolchain, package sources, and compiled dependency cache before running real compiler checks. The original Lean fixture and miniF2F fixture use separate environment locks. Reuse valid persisted validation evidence when neither proofs nor the checking environment changed.

To explicitly include real Lean integration tests in system verification:

```powershell
.\.venv\Scripts\python verification/run_verification.py --focused system --run-lean-integration
```

This flag enables compiler tests only. Full-corpus compiler checks use the separate `tokenshare.experiments.cli lean-environment-test` command and are not part of routine verification.

## Experiment data and execution

Experiments 1 and 5 require configured provider credentials and make paid requests. Run them only as a deliberate, separately authorized experiment. The checked-in provider settings and pricing describe recorded configurations, not a claim about current endpoint availability or prices.

Experiments 2–4 consume original Experiment 1 traces through `--source-run-dir` and do not fall back to a provider. A public checkout cannot reproduce those replays without the separate local run archive.

The current full and representative profiles require the Experiment 5 v4 configuration. Resume validation rejects older saved configurations that still name v3, including Experiment 1-only runs. Preserve those historical runs as recorded artifacts; do not rewrite their metadata to bypass validation.

Keep local raw runs and result reports under the ignored `TokenShareData/` directory. The existing `TokenShareData/sources/official-full-run/` entry is a read-only source and may be a directory link. Write new runs and analyses under `TokenShareData/outputs/experiments/<new-run-id>/`, preserving all source data and archived results. Never run the reducer over a preserved result archive.
