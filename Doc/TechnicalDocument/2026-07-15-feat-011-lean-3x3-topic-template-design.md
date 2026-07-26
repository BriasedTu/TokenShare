# Feat-011 Lean 3x3 题型模板设计记录

> 状态：设计记录 / case-selection guidance。本文不代表 runner、paper adapter 或 Lean plugin 已支持完整 3x3 执行矩阵。
> 日期：2026-07-15
> 权威边界：本文服从 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。如两者冲突，以唯一权威实验设计为准。
> 2026-07-18 Task 14 二次审核备注：本文中“只有一个 medium_lemma_dag golden case”“adapter 不能执行 v2 recursive lemma-DAG”“proof-file assembly / dependency-aware merge/root recheck 未完成”等描述是 2026-07-15 当时的设计背景，已被 Task 14 当前代码、catalog、tests、`benchmarks/paper/lean_task14_3x3_readiness.v1.json`、`progress.md` 和 Phase 8 code map 更新覆盖。保留旧描述只作为设计 provenance，不应作为当前实现事实。
> 2026-07-24 EPD-004 覆盖：本文若出现 Experiment 3 每 condition 3 repeats，只作历史口径；当前 rate-fault 与 worker-death 均为 2 repeats，Lean fault/rate/task slice 本身不变。

## 结论

基于已完成的 `topic_family` schema retrofit，Lean 正式实验的 3x3 目标可以设计，但必须分阶段落地。2026-07-15 当时实现事实是：

- `benchmarks/paper/lean_catalog.v1.jsonl` 全部只能算 `simple` / shallow；历史 `easy` / `medium` / `hard` 只是 shallow-v1 内部 proof-chain 变化，不能填正式 `medium_lemma_dag` 或 `hard_frontier`。
- `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 当前只有一个 checker-backed `pure_logic / medium_lemma_dag` golden case 和一个 no-oracle `pure_logic / hard_frontier` structured-blocked row。
- 当前 `SplitRules.lean` 只支持 `P ∧ Q` 和 `P ↔ Q`；`P → Q`、`∀ n : Nat, n = n` 等仍返回 unsupported。
- 当前 `lean_paper_adapter.py` 仍只接受 v1 `conjunction|iff` case，不能执行 v2 recursive lemma-DAG。
- `LeanLemmaGraphCertificate` v2 已能表达 lemma-DAG certificate / proposal，但 proof-file assembly、adapter DAG execution、dependency-aware multi-level merge/root recheck 仍未完成。

因此，3x3 题库可以先作为 catalog/template 设计和 fixed oracle package 规划，不能直接把 runner 改成完整 3x3 paper-eligible runnable。

## Recommended First 3 Golden Cases

最小实现切片建议先做 3 个 oracle-backed golden cases，并在正式 paper-runnable 前先补 v2 adapter DAG execution / proof-file assembly / dependency-aware root recheck。

| 优先级 | Golden case | 选择理由 | imports / Mathlib | expected_depth / expected_leaf_count / expected_ai_unit_count | 当前/所需能力 |
|---|---|---|---|---|---|
| 1 | `pure_logic.medium_lemma_dag.chain_implication.v1`：沿用当前 `P, P -> Q, Q -> R ⊢ R` | 已有 checker-backed fixture，是 schema、oracle package、budget selection 的控制样本 | `TokenShare.LemmaGraphOracle`；不依赖 Mathlib | `3 / 3 / 5` | 当前 catalog preflight 可 passed；paper adapter DAG execution 仍缺 |
| 2 | `function_set.medium_lemma_dag.dx_subset_chain.v1`：`(D E F : α -> Set β)`, `∀x, D x ⊆ E x`, `∀x, E x ⊆ F x ⊢ ∀x, D x ⊆ F x` | 回答 `D(x) subset` 分类，能用本地 fixed oracle，无需 Mathlib；比 pure logic 多函数/集合语义 | 优先 `Init` + 本地 `TokenShare.FunctionSetCases` / `TokenShare.FunctionSetOracle`；不依赖 Mathlib | `3 / 2-3 / 4-5` | 需要 forall intro、subset/implication intro、DAG proof assembly |
| 3 | `induction.medium_lemma_dag.nat_predicate_chain.v1`：从 `P 0`、`∀n, P n -> P (n+1)` 得 `∀n, P n`，再由 `P -> Q`、`Q -> R` 得 `∀n, R n` | 直接暴露 induction 所需 split/merge 能力，是 induction topic 的最小可信样本 | `Init` + 本地 `TokenShare.InductionCases` / `TokenShare.InductionOracle`；不依赖 Mathlib | `3 / 3-4 / 5-6` | 需要 deterministic induction skeleton、base/step slots、IH context、proof-file assembly |

第 3 个 case 可以先作为 oracle-preflight golden；在 induction split/merge 未完成前，runner condition 仍应 blocked，不应 paper-eligible runnable。

## Full 3x3 Candidate Matrix

| topic_family / paper_difficulty | Candidate theorem templates | imports / Mathlib | 推荐 metadata 与能力 | 风险 |
|---|---|---|---|---|
| `pure_logic / simple` | 1. `(P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q`；2. `(P Q : Prop) (hpq : P -> Q) (hqp : Q -> P) : P ↔ Q`；3. `(P Q : Prop) (hP : P) (hpq : P -> Q) : P ∧ Q` | `Init` 或当前 shallow catalog；不依赖 Mathlib | fixed oracle yes；D/L/U `1-2 / 2 / 2`；split `conjunction_goal` / `iff_goal`；shape 可继续视作 `shallow_split_merge.v1` 或 legacy v1 simple | 太浅，只能验证 adapter/checker/fault 基础，不能证明递归拆分 |
| `pure_logic / medium_lemma_dag` | 1. 当前 `P, P -> Q, Q -> R ⊢ R`；2. diamond：`P -> Q`, `P -> S`, `Q -> T`, `S -> T ⊢ T`；3. equivalence chain：`P ↔ Q`, `Q ↔ R ⊢ P ↔ R` | 本地 `TokenShare.LemmaGraphCases` / `TokenShare.LemmaGraphOracle`；不依赖 Mathlib | fixed oracle yes；D/L/U `3 / 3-4 / 5-7`；shape `recursive_lemma_dag_required_slots.v1`；需要 topological DAG execution and root recheck | 当前 adapter 不执行 v2 DAG；merge policy 只会 v1 conjunction/iff root assembly |
| `pure_logic / hard_frontier` | 1. oracle-backed deep quantified chain：`∀x, P x -> Q x`, `∀x, Q x -> R x`, `P a ⊢ R a`；2. nested iff/rewrite reuse：`(P ↔ Q)`, `(Q ↔ R)`, theorem reuse into conjunction root；3. no-oracle `False` / intentionally impossible stress | 1-2 可本地 oracle；3 用 `Init` no oracle | 1-2 可 fixed-oracle passed；D/L/U `4-5 / 4-6 / 7-10`；shape `recursive_lemma_dag_required_slots.v1` 或 future `recursive_lemma_dag_with_quantifier_rewrite.v1`；3 用 `structured_blocked_no_oracle_frontier_stress.v1` | 无 oracle 不能 checker success；量词/rewrite 若靠动态发现必须 blocked |
| `function_set / simple` | 1. pointwise function equality symmetry：`(h : ∀x, f x = g x) ⊢ ∀x, g x = f x`；2. direct subset assumption：`(h : ∀x, D x ⊆ E x) ⊢ ∀x, D x ⊆ E x`；3. local preimage monotonicity：`A ⊆ B ⊢ {x | A (f x)} ⊆ {x | B (f x)}` | 优先 `Init` + local definitions；avoid Mathlib | fixed oracle yes；D/L/U `1-2 / 1-2 / 1-2`；需要 forall / intro / subset-as-implication；shape `shallow_function_set.v1` | 如果使用 `Set.image`、extensionality notation、`simp` from Mathlib，环境复杂度上升 |
| `function_set / medium_lemma_dag` | 1. `D(x)` subset chain：`D ⊆ E`, `E ⊆ F ⊢ D ⊆ F`；2. image/preimage chain with local defs：`S ⊆ T`, `T ⊆ U ⊢ Preimage f S ⊆ Preimage f U`；3. odd function local algebra：`Odd f`, `Odd g ⊢ Odd (fun x => f x + g x)` with local algebra lemmas | 1-2 no Mathlib with local defs；3 no Mathlib only if algebra lemmas are local/oracle；Real version needs Mathlib | fixed oracle yes for 1-2；D/L/U `3 / 2-4 / 4-7`；shape `recursive_lemma_dag_required_slots.v1`；需要 forall intro、implication intro、local definitions、dependency-aware proof assembly | Odd functions are medium only with `Int`/local algebra lemmas；over `Real` with ring/order/theorem reuse becomes hard |
| `function_set / hard_frontier` | 1. Real/domain subset：`D x` defined by inequalities, prove `D x ⊆ E x`；2. odd/even real composition/product with domain restrictions；3. image/preimage extensional equality with injective/surjective hypotheses and existential witnesses | Usually Mathlib-dependent if using `Real`, order, image, extensionality；local finite/predicate versions can avoid Mathlib | Oracle-backed passed only after fixed proof package and environment digest；D/L/U `4-5 / 4-8 / 8-12`；shape future `recursive_lemma_dag_with_rewrite_cases.v1`；no oracle -> structured blocked | Highest overclaim risk：Mathlib not in current lakefile；dynamic theorem reuse/rewrite must not be AI-defined |
| `induction / simple` | 1. generic Nat induction：`(P : Nat -> Prop) h0 hs ⊢ ∀n, P n`；2. List induction identity over local predicate：`P []`, step -> `∀xs, P xs`；3. `∀n, Q n` where base/step are assumptions | `Init`；不依赖 Mathlib | fixed oracle yes；D/L/U `2 / 2 / 2-3`；shape `induction_base_step_slots.v1`；需要 deterministic induction variable from catalog/rule、base/step child payloads、IH context | 当前 `SplitRules.lean` returns unsupported for `∀`；不能运行到 paper-eligible，直到 induction split rule 存在 |
| `induction / medium_lemma_dag` | 1. Nat predicate chain `∀n, P n -> ∀n, Q n -> ∀n, R n`；2. List lemma-DAG：prove helper lemma by induction, use it in root；3. Nat two-helper theorem：one inductive lemma plus one rewrite lemma feed root | `Init` + local `TokenShare.InductionCases` / `TokenShare.InductionOracle`；不依赖 Mathlib | fixed oracle yes after plugin support；D/L/U `3 / 3-5 / 5-8`；shape `recursive_induction_lemma_dag_required_slots.v1`；需要 induction skeleton + lemma DAG + proof-file assembly | Requires carrying IH and local helper theorem names through artifact/proof assembly |
| `induction / hard_frontier` | 1. nested Nat induction over `(m,n)`；2. accumulator/list reverse-style theorem requiring helper lemmas and rewrite order；3. custom inductive tree theorem with multiple constructors | Local custom inductive possible without Mathlib；richer List/Nat rewrites may need more local lemmas, not necessarily Mathlib | Oracle-backed passed if fixed package contains all helper lemmas and preflight passes；D/L/U `4-6 / 5-10 / 9-15`；no oracle or missing skeleton -> structured blocked / frontier stress | Nested induction generalization is easy to get wrong；hard 只有 oracle proof exists 才能 passed，否则 blocked |

`D/L/U` 表示 `expected_depth / expected_leaf_count / expected_ai_unit_count`。

## Function_Set 分类标准

函数与集合题不要按“出现函数/集合”自动算 hard。分类标准应看证明结构和依赖：

- `simple`：root theorem 可以直接由一个假设、一次 pointwise intro、一次 subset intro 或一个 shallow split 完成。例如 `∀x, D x ⊆ E x` 直接来自 `hDE`。
- `medium_lemma_dag`：需要 2-3 层固定 lemma-DAG，但所有概念都可用本地定义和 fixed oracle package 表达。例如 `D(x)` subset transitive chain、preimage monotonicity chain、local predicate set proof。
- `hard_frontier`：需要外部库 theorem reuse、real arithmetic/order、domain-of-expression reasoning、case split、set extensionality/existential witness choreography，或证明需要较强 rewrite strategy。

具体判断：

- `D(x) subset` 如果只是 `D E F : α -> Set β` 的 subset chain，应归 medium。
- `D(x)` 如果由不等式、分段函数、实数表达式 domain 推导出来，应归 hard。
- 奇函数如果是 `Int` 或本地 algebra lemma 的 fixed proof，可归 medium。
- 奇函数如果是 `Real` 上带连续性、可导性、domain 或 ring/order automation，归 hard。

## Induction 所需 deterministic split/merge 能力

induction topic 至少需要这些能力，且必须由 plugin / catalog deterministic rule 给出，不能让 AI 选择拆分：

1. 识别或读取 catalog 指定的 induction variable：如 `n : Nat`、`xs : List α`、custom inductive value。
2. 生成 base / step child theorem payload：step payload 必须显式带 induction hypothesis。
3. 支持 base/step slot integrity：base proof、step proof、IH 使用范围、environment digest 都要绑定。
4. 支持 recursive lemma-DAG ordering：先证明 inductive helper lemma，再把 helper lemma 作为 root proof dependency。
5. proof-file assembly：按 topological order 写入 helper theorem，再写 root theorem；每个 accepted child proof 必须作为局部 theorem / `have` 或 named theorem 被 root checker 复验。
6. rewrite skeleton：允许 catalog / fixed oracle 指定可用 local rewrite lemmas；不要让 AI 返回“我建议用这个 lemma”来改变协议 DAG。
7. nested induction 只进入 hard，且必须有 fixed oracle proof package；否则 structured blocked。

## Hard_Frontier 可采信边界

可作为 `oracle-backed passed` 的 hard case 必须同时满足：

- fixed local oracle proof package 存在并被 hash 绑定。
- 每个 node proof、helper theorem、root assembly 都能在固定 Lean/lake/toolchain/library environment 下 preflight passed。
- `dependency_edges`、`expected_depth`、`expected_leaf_count`、`expected_ai_unit_count` 与实际 graph 一致。
- `proof_assembly_shape` 已被 loader / adapter 明确支持，且 root checker recheck 不依赖历史运行补造。

只能作为 `structured_blocked` / `frontier_stress` 的 hard case：

- 没有 oracle proof package，或 oracle hash / environment digest 不稳定。
- 题目可能不真、不可解、等价于研究级 conjecture，或当前本地 environment 没有依赖库。
- 需要动态 theorem search、检索、自动发现 split strategy。
- 当前类似 `False` no-oracle hard row 应继续 `structured_blocked`，不能 checker success。

## Required Plugin / Adapter Capabilities

最小能力顺序建议如下：

1. 实验层：v2 `lean_lemma_graph` adapter execution。按 `dependency_edges` 拓扑执行 proof units，不能只跑 v1 conjunction / iff。
2. Lean plugin：lemma-DAG proof-file assembly。把 accepted node proof artifacts 组装成 helper theorem / root theorem source，并由 Lean checker root recheck。
3. Lean plugin：dependency-aware slot integrity。merge 必须验证 node id、context digest、payload digest、environment digest、proof artifact digest。
4. Lean plugin：generalized merge beyond conjunction / iff。当前 `merge_policy.py` 只会 `And.intro` / `Iff.intro`，不足以 root assembly。
5. Function_set support：forall intro、subset-as-implication intro、local predicate set definitions、optional exists witness assembly。
6. Induction support：catalog-driven induction skeleton、base/step child payload、IH context、local theorem ordering。
7. Catalog / preflight：oracle package group 按 topic 分模块，例如 `TokenShare.FunctionSetOracle`、`TokenShare.InductionOracle`，每组独立 hash 和 environment digest。
8. Runner safety：缺 cell 或 unsupported shape 输出 blocked / insufficient catalog，不 fallback 到 shallow v1。

## Budget Impact Estimate

粗估先按 AI unit，不按 root task。最终仍必须由 `--plan-only` 根据 catalog 精确展开。

- Catalog pool 目标：`9 cells × 10-20 cases = 90-180 Lean cases`。
- 单 case AI unit 粗估：simple `1-2`，medium `4-8`，hard oracle-backed `8-15`，hard structured-blocked `0 provider calls` 但有 preflight / blocked evidence。
- 若每 cell 10 cases，单次全 Lean 3x3 feasibility pass 约：simple `3 × 10 × 2 = 60` units，medium `3 × 10 × 6 ≈ 180` units，hard 若全 oracle-backed `3 × 10 × 10 ≈ 300` units，总计约 `540 provider calls / repeat`，3 repeats 约 `1620` provider calls；20 cases 约翻倍。
- 如果 Experiment 1/2/4 都把 Lean `topic_family` 作为正式维度，root-run 数会明显增加：Experiment 1 Lean 从 `90` root-runs 变 `270`；Experiment 2 Lean 从 `300` 变 `900`；Experiment 4 Lean 从 `270` 变 `810`。仅这三项相对旧 P0-core 就增加约 `1320` root-runs，还没算 fault / worker-death 是否按 topic 扩展。
- Checker preflight 成本也会放大：90-180 cases × 平均 5-8 nodes，约 `450-1440` 次 node checker / preflight 级检查；hard oracle package build 还会增加 lake build 时间。
- First 3 golden cases 粗估：`5 + 4/5 + 5/6 ≈ 14-16 AI units / repeat`；3 repeats 约 `42-48 provider calls`，若 max provider attempts=2，上界约 `84-96` provider attempts。

## Risks And Non-Goals

风险：

- 最大技术风险不是 schema，而是 adapter DAG execution 和 proof-file assembly 尚未完成；没有这两项，v2 medium / hard 只能 catalog/preflight，不能 paper-runnable。
- Mathlib 风险：当前 `lakefile.lean` 没有 Mathlib；任何 `Real`、高级 `Set.image`、order/ring automation 都会改变 environment digest 和预检成本，不适合 first slice。
- Oracle proof 人工成本会成为瓶颈；每个 hard case 都要固定 proof package，不应按 AI 成败挑题。
- AI proof success 不稳定不能用于决定 case 是否入 catalog；catalog 只看 theorem 是否真、oracle/preflight 是否通过。
- hard/frontier overclaim 风险高：无 oracle 的 frontier case 只能用于 structured failure、budget、recovery、ablation 边界，不能写成 theorem proving success。

非目标：

- 不引入动态 theorem proving 平台。
- 不引入检索系统。
- 不让 AI 决定 decomposition。
- 不把 Lean 规则写进 `tokenshare.core`。
- 不把 shallow v1 legacy hard 当正式 hard。
- 不批量新增 90-180 道题，除非 adapter、oracle package、budget gate 和 preflight 先完成。

## 本轮资料

本设计记录只使用仓库内已有文档、catalog、fixture 和代码上下文；本轮未联网。
