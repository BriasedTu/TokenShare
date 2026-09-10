# Results

## Published experiment tables

The retained five-experiment result set is in [`results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/`](results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/). The directory preserves the original run identifier.

| File | Contents |
| --- | --- |
| `run.json` | Recorded run metadata |
| `metrics/summary.json` | Experiment summary |
| `metrics/tables/exp1.csv` and `.jsonl` | 12 baseline rows |
| `metrics/tables/exp2.csv` and `.jsonl` | 88 concurrency rows |
| `metrics/tables/exp3.csv` and `.jsonl` | 684 recovery rows |
| `metrics/tables/exp4.csv` and `.jsonl` | 648 mechanism rows |
| `metrics/tables/exp5.csv` and `.jsonl` | 3 provider-comparison rows |

The run-local [`manifest.json`](results/experiments/slim-v2-full-flash-20260823-233000-b4c8e951/manifest.json) records the exact file list, byte sizes, SHA-256 digests, and raw archive facts. [Metric definitions](Doc/Experiments/metrics.md) explain how to interpret the tables.

Verify the files without modifying them:

```powershell
python verification/verify_official_results.py --verify-worktree-index
```

## miniF2F supplement

The separate Experiment 1 miniF2F run completed on September 8, 2026, over 81 roots and 223 planned first-attempt proof units. The recorded outcomes were 27 passed roots, 48 model-verification failures, five checker timeouts, and one mixed candidate-acquisition failure, with 354 provider calls.

The public repository includes the admitted corpus and its proof-validation evidence. The supplement's raw run and result audit are local artifacts; they are not part of the published five-experiment tables above. See the [miniF2F supplement](Doc/Experiments/minif2f-supplement.md) for provenance and local report locations.

## Raw data availability

The original five-experiment raw archive contains 1,088,134 files totaling 7,143,234,452 bytes. It is retained locally under the ignored data area. An external archive has not been published; its recorded status is `pending_advisor_archive_decision`.

Public metrics support inspection and integrity checks. Exact trace replay and independent recomputation from raw provider responses additionally require that separate archive. Current configurations, including the Experiment 5 v4 cohort, do not retroactively change the identity of a recorded result set.
