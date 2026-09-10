# Experiment guide

TokenShare runs all experiments through `tokenshare.experiments`, using the same protocol runtime, storage, plugins, and executor contracts as the rest of the system.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Design](design.md) | Experiment questions, conditions, and execution semantics |
| [Metrics](metrics.md) | Outcomes, denominators, resource accounting, and missing-value rules |
| [System integration](system-integration.md) | Protocol and experiment wiring |
| [Code map](code-map.md) | Implementation entry points |
| [Corpus manifest](corpus-manifest.md) | Frozen asset provenance and current identity expectations |
| [miniF2F supplement](minif2f-supplement.md) | Fixed mathematical DAGs, admission evidence, and the Experiment 1 supplement |
| [Factorization analysis](factorization-analysis.md) | Read-only root and attempt analysis from recorded runs |
| [Reproducibility](../../REPRODUCIBILITY.md) | Environment setup and verification commands |
| [Results](../../RESULTS.md) | Published tables and raw archive availability |

## Experiments and execution

1. **Baseline:** Experiment 1 runs factorization and Lean tasks against the configured provider.
2. **Concurrency:** Experiment 2 replays Experiment 1 unit traces with different worker counts.
3. **Recovery:** Experiment 3 replays those traces with fault and recovery scenarios.
4. **Mechanisms:** Experiment 4 runs mode-blind challenges across 11 mechanism modes.
5. **Providers:** Experiment 5 compares three configured SiliconFlow endpoints.

Experiments 2–4 make zero provider calls and require a source run containing Experiment 1 traces. Roots execute serially; `worker_count` controls concurrency inside each root. Experiment 1 coverage-tail work supplies traces needed by downstream experiments without changing the root's recorded runtime.

The separate `minif2f` profile selects only Experiment 1, with 81 admitted theorem roots and 223 proof units. Experiments 2–5 retain their existing inventories.

## Public assets

| Asset | Path |
| --- | --- |
| Factorization catalog | `benchmarks/experiments/factorization_catalog.v2.jsonl` |
| Lean direct catalog | `benchmarks/experiments/lean_catalog.v1.jsonl` |
| Lean lemma-DAG catalog | `benchmarks/experiments/lean_lemma_graph_catalog.v1.jsonl` |
| miniF2F catalog and admission evidence | `benchmarks/experiments/minif2f_catalog.v1.jsonl`, `benchmarks/experiments/minif2f/` |
| Lean environment locks and fixtures | `benchmarks/experiments/fixtures/` |
| Experiment 1 provider config | `configs/experiments/exp1_baseline_provider_config.v3.json` |
| Experiment 5 current provider config | `configs/experiments/exp5_siliconflow_provider_config.v4.json` |
| Experiment 5 original provider config | `configs/experiments/exp5_siliconflow_provider_config.v3.json` |
| Corpus integrity manifest | `benchmarks/experiments/manifest.v1.json` |
| Published metrics | `results/experiments/` |

The original 20 corpus/config/fixture files retain byte-identical provenance from the annotated tag `experiments-pre-cleanup-20260827`, peeled commit `bb5e637785afb6bd5743e4d89af4c02ab0736204`. Current selections and the miniF2F supplement have additional complete identity and file-hash checks. Historical source paths in manifests are provenance, not runtime dependencies.

## Verification

From the repository root:

```powershell
python verification/run_verification.py --focused system
python verification/run_verification.py --focused experiments
python verification/run_verification.py --focused extraction
```

Use the Python environment prepared in [Reproducibility](../../REPRODUCIBILITY.md). Default verification checks contracts, file hashes, environment locks, and recorded compiler evidence without starting Lean/Lake or real provider calls. Compiler tests require `--run-lean-integration` and a prepared dependency cache.

Full Lean environment checks are a separate explicit operation. The miniF2F checker uses its pinned Lean 4.24.0/Mathlib environment and reuses matching proof evidence. Raw experiment outputs, compiler installations, manuscript files, and authoring plans remain local under the ignored data boundary.
