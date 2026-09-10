# miniF2F Experiment 1 supplement

This supplement admits only roots whose fixed mathematical DAG, every node proof,
and original theorem assembly have passed the pinned local Lean checker. Candidate
pool size is not an admission target. It is selected explicitly for Experiment 1;
Experiments 2–5 retain their existing inventories.

The completed 102-candidate review admits **81 roots / 223 AI units** and
excludes **21 roots** whose original proof was not closed in this round. Every
candidate has a concrete attempt and disposition in
[`curation.v1.json`](../../benchmarks/experiments/minif2f/curation.v1.json).
The admitted catalog is
[`minif2f_catalog.v1.jsonl`](../../benchmarks/experiments/minif2f_catalog.v1.jsonl).
All 102 original statement files, candidate copies, and available reference
proofs retain their input SHA256. Exclusions include checked partial lemmas,
incomplete combined checks, and an informal analytic route; none claims the
upstream theorem is false. These partial results never count as admitted roots.

The admitted graphs contain 24 two-node, 53 three-node, and 4 four-node cases.
The corpus manifest binds complete ordered root and node identity arrays and
all statement, oracle, independent-review and compiler-evidence file hashes.
The original 6,912 root and 104 reference identities remain unchanged.

## Fixed DAG and proof package contract

- Preserve the upstream split, theorem name, imports, open namespaces, parameters,
  assumptions, and conclusion. Root identity is `minif2f_<split>_<theorem_name>`.
  Record upstream repository commit, relative file, exact file SHA256, and the
  original declaration. Reference scripts are evidence inputs, never trusted proofs.
- Nodes use descriptive ASCII snake_case identifiers, unique within a root. The
  terminal node is `root`; intermediate nodes express substantive mathematical
  progress. Do not count renaming, equivalent restatements, or the root conclusion
  wrapped as a helper. All nodes must contribute to the root.
- Edges point from prerequisite to consumer. Record only real direct dependencies;
  preserve independent branches. Topological order, depth, leaf count, and AI unit
  count are derived from the graph. One node is one proof-candidate AI unit.
- All nodes share the original root parameter context. Any additional bound
  variables must be quantified in the node statement. A dependency is available
  under `node_<source_node_id>` with its exact statement, only after that source
  proof is canonical. A node sees direct prerequisite declarations, never its own
  proof, downstream proofs, or the oracle package. AI output cannot change the DAG.
- Store proof bodies beginning with `by` separately from declarations. They may
  refer to the declared direct prerequisites. No `sorry`, `admit`, new `axiom`,
  unsafe evaluation, or imports containing problem answers. Use Mathlib and
  upstream public mathematical imports only. Bound local wall-clock resources.
- Merge uses checked candidate bodies in topological order, preserving Lean
  indentation and binding each as `have node_<id> : <statement> := <proof>`.
  The final proof is checked against the unchanged upstream root declaration.
- Authoring interchange is one JSON per candidate: `case_id` (upstream split/name),
  `imports`, `open_namespaces`, `parameters_source`, `statement_source`,
  `theorem_name`, `nodes` (each `node_id`, `statement_source`, `dependencies`,
  `proof_source`, `mathematical_role`), plus source provenance and validation
  evidence. This is curation data, converted to the existing
  `LeanFixedDecompositionPlan` catalog and oracle package, not another runner.

## Admission and independent review

For each candidate, persist a machine-readable disposition and concrete reasons.
Authors work in small batches and compile before advancing. Admission requires:
semantic comparison to the original declaration; substantive-node and edge review;
isolated node checking with only declared dependencies; topological assembly and
root recheck; independent reviewer approval; source, payload, proof-package, graph,
catalog and environment hashes; and successful inventory/environment preflight.
Failed candidates may be repaired on evidence and otherwise are excluded. Never
represent unattempted or unverified candidates as checked failures or admitted roots.

Offline admission compiles one file per case: anonymous `example` blocks for
the individual nodes, followed by the named original theorem assembled with the
production merge function. Lean 4.24's `elabMutualDef` checks examples under
`withoutModifyingEnv`, so earlier examples cannot supply proofs to later blocks.
Each block has payload/proof/source hashes and exact line spans. A nonzero exit,
timeout, or nonstandard root axiom prevents the entire file from supplying
accepted evidence. The allowed kernel axioms are `propext`, `Classical.choice`
and `Quot.sound`; neither `sorryAx` nor native-evaluation axioms are accepted.
The public review binds the compiler result and its source and output files.
Original upstream files are retained byte-for-byte as `statements/*.lean.txt`
for provenance, including upstream placeholders. They are never imported or
compiled. Only the separately reviewed proof packages and generated check sources
participate in admission. Upstream licensing is retained with the pinned fixture.

Default pytest and focused baseline verification never start Lean or Lake.
They check contracts, complete identity arrays, hashes, and persisted compiler
evidence. Real integration tests require `--run-lean-integration`. Environment
setup installs the locked tools, all package sources, and their compiled cache
before checking cases; normal operation never downloads or rebuilds them.
`lean-environment-test --profile minif2f` reuses matching verified case evidence
and compiles only missing or changed cases. Add `--recheck` only when a complete
fresh Lean run is necessary. Production AI units still receive real per-unit
Lean checking and a final assembled-root check through the existing plugin.

## Pinned environment

The public fixture pins Lean `leanprover/lean4:v4.24.0`, Mathlib commit
`f897ebcf72cd16f89ab4577d0c826cd14afaafc7`, and its transitive package lock.
The fixture helper imports Mathlib and contains no problem answers.

Compiler installations, package checkouts, and compiled caches remain local.
`configs/experiments/exp1_minif2f.v1.json` selects the ignored environment root;
its `runtime_path.json` can select a prepared installation, and
`TOKENSHARE_MINIF2F_ENVIRONMENT` provides an explicit override. Prepare the locked
toolchain and dependencies before real compiler tests. The original Lean fixture
has a separate environment and is not interchangeable with this one.

The corpus verifier checks all 81 root identities, 223 node identities, and 672
file hashes, including the persisted compiler evidence. This offline check does
not require the prepared installation. See [Reproducibility](../../REPRODUCIBILITY.md)
for the ordinary verification commands and explicit compiler-test boundary.

## Recorded Experiment 1 run

The Experiment 1 plan contains 81 roots, 223 first-attempt AI units, and at most
669 provider calls including retries. Experiments 2–5 have no selected units.
Inspect the plan without contacting a provider:

```powershell
$env:PYTHONPATH = 'src'
python -m tokenshare.experiments.cli plan --profile minif2f --run-id minif2f-plan
```

The run completed on September 8, 2026: 27 roots passed, 48 ended in
model-verification failures, five in checker timeouts, and one in a mixed
candidate-acquisition failure. All 81 roots are terminal. There were 354 provider
calls; Experiments 2–5 were not started.

The original raw run is retained locally at
`TokenShareData/outputs/experiments/minif2f-exp1-prepared-20260908/`.
The read-only report is at
`TokenShareData/outputs/experiments/minif2f-exp1-results-20260908/results.md`,
with complete root identities and a 3,147-file SHA evidence manifest. These run
artifacts are ignored and are not included in a public clone. Diagnostic rechecks
do not replace the original failure classifications.

The admitted corpus and proof-validation records are included in the public
repository. [RESULTS.md](../../RESULTS.md) distinguishes this supplement from the
separately published five-experiment tables.
