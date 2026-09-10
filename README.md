# TokenShare

**A research prototype for decomposing, verifying, and replaying collaborative AI tasks.**

TokenShare turns a large task into verifiable units, assigns them to workers, checks candidate results, and merges accepted outputs. Its local protocol runtime records task state, leases, artifacts, and settlement decisions so that execution can be inspected and replayed.

The repository contains the implementation and research assets for integer factorization and Lean theorem proving: one shared runtime, domain plugins, five experiments, a miniF2F supplement, and published experiment metrics.

[Quick start](#quick-start) · [Reproducibility](REPRODUCIBILITY.md) · [Results](RESULTS.md) · [Experiment guide](Doc/Experiments/README.md)

## How it works

```text
Task → Decomposition → Worker execution → Verification → Merge → Settlement
          └──────────── persisted events and artifacts ─────────────┘
                                  ↓
                           Inspection & replay
```

- **Shared protocol:** task decomposition, scheduling, leases, retries, acceptance, and settlement use the same protocol objects across experiments.
- **Domain verification:** factorization candidates are checked deterministically; Lean proof candidates and assembled proofs are checked by a pinned Lean environment.
- **Trace replay:** persisted execution traces support concurrency, recovery, and mechanism comparisons without new provider calls.
- **Auditable research assets:** manifests bind benchmark identities, proof evidence, configurations, and published metrics to file hashes.

TokenShare is a local research system. Settlement is modeled within the protocol; blockchain deployment, real token payments, and production Byzantine fault tolerance are outside its scope.

## Quick start

Use **Python 3.12** (the validated version). Runtime dependencies are from the Python standard library; `requirements.txt` pins pytest for verification.

From the repository root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path

# Inspect the full experiment plan without contacting a provider.
.\.venv\Scripts\python -m tokenshare.experiments.cli plan --profile full --run-id quickstart

# Run the fast offline verification suite.
.\.venv\Scripts\python verification/run_verification.py --focused fast
```

On macOS/Linux, use `.venv/bin/python` and `export PYTHONPATH="$PWD/src"` for the corresponding commands. The commands above inspect plans and run offline checks; they do not execute paid experiments or compile Lean proofs.

For the optional desktop interface, run `python -m tokenshare.experiments.gui` with `src` on `PYTHONPATH` and Tkinter installed. The Windows shortcut `run_experiments.cmd` uses an existing Conda environment named `tokenshare`.

## Experiments

| Experiment | Research question | Execution |
| --- | --- | --- |
| 1 · Baseline | How do factorization and Lean tasks perform under the protocol? | Provider-backed execution |
| 2 · Concurrency | How does worker count affect execution? | Replay of Experiment 1 traces |
| 3 · Recovery | How does the protocol respond to injected faults? | Replay with fault scenarios |
| 4 · Mechanisms | What changes when individual mechanisms are disabled? | Replay across 11 mechanism modes |
| 5 · Providers | How do three configured provider endpoints compare? | Provider-backed execution |

The [miniF2F supplement](Doc/Experiments/minif2f-supplement.md) adds an explicit Experiment 1 profile with **81 admitted theorem roots and 223 proof units**. It preserves original theorem statements, fixed dependency graphs, proof packages, and admission evidence.

[Published results](RESULTS.md) describe the retained tables and the limits of the public archive. Replaying Experiments 2–4 requires the original Experiment 1 traces, which are maintained separately from the public metrics. Provider configurations contain environment-variable names for credentials, never API keys.

## Repository map

| Path | Contents |
| --- | --- |
| [`src/tokenshare/`](src/tokenshare/) | Protocol, local runtime, storage, plugins, executors, and the `experiments` package |
| [`benchmarks/experiments/`](benchmarks/experiments/) | Benchmark catalogs, Lean fixtures, miniF2F proofs, and corpus manifest |
| [`configs/experiments/`](configs/experiments/) | Experiment and provider configurations |
| [`results/experiments/`](results/experiments/) | Published run metadata, metrics, and integrity manifest |
| [`Doc/Experiments/`](Doc/Experiments/) | Experiment design, metric definitions, and implementation references |
| [`tests/`](tests/) | Offline regression tests and explicitly enabled Lean integration tests |
| [`verification/`](verification/) | Focused checks for behavior, corpus identities, results, and release boundaries |

Generated runs, credentials, caches, and manuscript workspaces are excluded from Git. Local raw data belongs under `TokenShareData/`; new analyses must use a separate output directory and preserve their source runs.

## License

TokenShare is released under the [MIT License](LICENSE). The miniF2F fixture retains its [upstream license](benchmarks/experiments/fixtures/minif2f_project/upstream/LICENSE).
