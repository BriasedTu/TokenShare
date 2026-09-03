# Experiment 1 Factorization analysis extraction design

## Purpose and scope

This change adds one small, standalone, read-only extractor for the published
Experiment 1 Factorization run. It reorganizes already persisted facts into
root-level and range-attempt-level analysis tables. It does not run an
experiment, invoke a provider, modify the reducer, overwrite official metrics,
or add a publication gate.

The extractor is published at
`src/tokenshare/experiments/factorization_exp1_analysis.py`. A byte-identical
copy is retained in the source Slim V2 worktree. The local raw full-run
directory and output directory are required command-line arguments; the script
does not discover runs or credentials.

## Authoritative inputs

The extractor reads only these persisted inputs:

- `run.json`: global run identity.
- `inventory/roots.jsonl`: frozen Experiment 1 root membership, case identity,
  repeat, configured model, worker count, and planned range unit identities.
- `inventory/conditions.jsonl`: frozen `max_retries` and condition facts.
- `benchmarks/experiments/factorization_catalog.v2.jsonl` in the public tree,
  or its byte-identical source-worktree path: target integer, complete candidate
  domain, oracle factors, original stratum, native factor-position stratum, and
  requested partition count.
- `roots/exp1/*/result.json`: committed root outcome, protocol lifecycle,
  attempt projections, resource facts, failure classification, and missing
  reasons.
- `roots/exp1/*/protocol.json`: protocol run identity, executed unit traces, and
  worker execution timestamps. This file is legitimately absent when preflight
  failed before protocol start.
- `calls/*.terminal.json`: provider terminal error kind and cross-checks for
  usage and latency.
- per-root system artifacts referenced by `protocol.json`: parsed
  `factorization.range_result.v1` candidate kind. Raw response bodies are not
  read.
- `metrics/tables/exp1.jsonl`: read-only comparison target for published cell
  totals.

No facts are inferred from the paper draft or from legacy labels alone.

## Outputs

The extractor creates four new files in an explicitly supplied output
directory:

- `factorization_exp1_root_analysis.csv`
- `factorization_exp1_attempt_analysis.csv`
- `metadata.json`
- `factorization_exp1_validation_report.md`

The root table contains every frozen Experiment 1 Factorization root, including
infrastructure-invalid and no-final roots. The attempt table contains one row
per actual protocol range attempt. Planned-but-unscheduled ranges are represented
only by root plan/execution counts and never become synthetic attempt rows.

`metadata.json` records every output column's semantic meaning, source file,
source field path, fact/derivation status, exact derivation where applicable,
and null or missing meaning. It also records input file SHA-256 digests and the
validation results.

## Key derivations

- `root_id` uses the runtime's deterministic identity formula
  `run_id:experiment_id:condition_id:case_id:repeat_id`. It is reproduced from
  frozen inventory identity fields for the two roots that stopped before a
  `protocol.json` could be written, and it must equal `protocol_result.run_id`
  for every started root. The four-part inventory key and relative root result
  path are also retained for direct traceability.
- `sqrt_floor = floor(sqrt(target_n))` using integer square root.
- `candidate_domain_size = sqrt_floor - 1`, the size of
  `[2, floor(sqrt(target_n))]`.
- For composite cases, `smallest_factor` is the sole oracle prime factor inside
  the complete candidate domain. Prime/no-factor cases have no such value.
- `factor_position_group` copies the catalog's native
  `factor_position_quantile`; it is not re-binned.
- `factor_position_ratio = (smallest_factor - 2) / (sqrt_floor - 2)`, matching
  the frozen catalog generator and validator. It is null for no-factor cases.
- `factor_range_index` is the zero-based index from the frozen contiguous,
  balanced partition rule. Derived ranges must match every persisted executed
  trace range.
- `input_scale_rank` and `input_scale_group` apply only to 8-range composite
  roots. Roots are ordered by `(candidate_domain_size, target_n, case_id)`. For
  `N=94`, group index `floor(3 * (rank - 1) / N)` maps to
  `small_M`, `middle_M`, and `large_M`, producing groups of 32, 31, and 31.
  Prime and non-8-range roots have null values.
- `completion` follows the published metric definition and equals
  `final_result_present`. `root_status` and `protocol_task_completed` remain
  separate columns.
- `final_verified` copies `verified_correct`. `no_final`, `incorrect_final`,
  and `infra_invalid` are direct tests of the committed `failure_kind`.
  `scientifically_evaluable` excludes only infrastructure-invalid roots.
- Root tokens and provider latency are nullable sums over attempts with
  `trace_origin=protocol` and `provider_call_made=true`. If any called attempt
  lacks the relevant fact, the root total is null. An empty set of provider
  calls sums to zero under the published reducer and is distinguished by
  `provider_call_count=0`; missing values are never imputed as zero.
- Root end-to-end time copies `runtime_wall_clock_ms`, whose formal definition
  is `root_terminal_at_ms - root_start_at_ms` for the protocol lifecycle.
- Attempt elapsed time is the persisted worker `ended_at - started_at` in
  milliseconds. Provider latency remains a separate direct field.
- `retry_count` counts protocol attempts with `attempt_ordinal > 0`; every such
  attempt must carry a persisted `replacement_of_attempt_id`.
- `first_attempt_nonpass_count` counts executed ranges whose ordinal-zero
  attempt did not have `verifier_result=passed`. Parse and provider failures
  therefore count as non-passes without being mislabeled as verifier
  rejections.
- `verification_rejection_count`, `parse_failure_count`, and
  `provider_failure_count` count the direct states `verifier_result=rejected`,
  `parse_result=rejected`, and `result_kind=provider_failed`, respectively.
- Attempt `failure_type` is a documented diagnostic derivation with precedence
  `provider_failure`, `parse_failure`, `verification_rejection`, then null. The
  direct provider error kind, acquisition result kind, parse result, verifier
  result, and canonical-acceptance flag remain separate columns.

There is no authoritative attempt-level `is_required_for_final` or independent
`attempt_valid` fact. These requested columns remain null and are marked
`unavailable` in metadata rather than being guessed.

## Validation and failure behavior

Extraction fails without replacing or deleting outputs when identities,
schemas, catalog formulas, partition geometry, attempt joins, or published
cell totals disagree. This is ordinary input validation inside the extractor,
not a repository publication gate.

The validation report covers:

- exactly 300 unique Experiment 1 Factorization roots and 100 roots per original
  stratum;
- the 2/4/8 partition mapping and prime/composite plus native factor-position
  composition;
- verified, no-final, incorrect-final, and infrastructure-invalid counts;
- nullable resource and root-time totals against the published Experiment 1
  reducer rows;
- complete case/root traceability;
- the composition of the six 8-range no-final roots.

Focused verification consists of pure derivation tests, compilation of the
standalone script, one extraction from the existing formal run, and an
independent read-only inspection of produced row counts, null handling, joins,
and published totals. No provider or experiment command is permitted.
