# Lean 四节点 Lemma-DAG 代表性实例

更新时间：2026-08-27

## 1. 文档用途

本文为论文写作 AI 提供五个来自 TokenShare 正式 Lean 题库的四节点 lemma-DAG 实例。本文只呈现题库中的形式化事实，不为抽象谓词 `A`–`F` 附加业务场景，也不把解释性重命名写成题库原义。

五个实例均满足：

- `schema_version="tokenshare.paper_lean_lemma_graph_case.v1"`；
- `topic_family="function_set"`；
- `paper_difficulty="medium_lemma_dag"`；
- `preflight_status="passed"`；
- `expected_ai_unit_count=4`；
- `expected_depth=3`；
- `expected_leaf_count=2`；
- `expected_split_kind="recursive_lemma_dag"`。

它们来自 `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl`。当前题库中共有 15 个满足上述条件的正式可检查实例；本文选择其中五个，以覆盖不同的合取、析取和嵌套命题结构。

## 2. 四节点图的统一结构

五题具有相同的依赖图形状：

```text
leaf_ab ──┐
           ├──> intermediate_ac ───> root
leaf_bc ──┘
```

精确偏序为：

```text
leaf_ab < intermediate_ac < root
leaf_bc < intermediate_ac < root
```

因此应区分两种“顺序”：

1. **题库序列化的 `dependency_order`**：`leaf_ab, leaf_bc, intermediate_ac, root`。这是一个确定性的拓扑排序。
2. **按依赖得到的执行阶段**：
   - 阶段 1：`leaf_ab` 与 `leaf_bc` 均无前置节点，可以并行执行；
   - 阶段 2：两条叶节点证明都成为 accepted result 后，执行 `intermediate_ac`；
   - 阶段 3：`intermediate_ac` 成为 accepted result 后，执行 `root` proof unit；
   - 节点执行结束后：四个 required proof slots 齐备，插件执行 merge，并对组装后的顶层证明进行最终 Lean 检查。这个 merge/recheck 步骤不是第五个 lemma 节点。

`intermediate_ac` 与 `root` 在这五题中具有相同的 proposition，但它们是图中的不同节点：前者表示组合出的中间证明，后者是顶层 theorem 的 proof unit。最终 merge 与 root recheck 在四个 proof units 完成之后发生，不能与 `root` 节点本身混为同一步。

还有一个必须保留的实现边界：dependency edge 是协议级依赖。目标节点的请求会得到前置节点的 proof references，但题库中的 oracle proof source 可能直接使用共享假设 `hAB`、`hBC` 重新写出组合证明，而不一定在 Lean 源码中按前置 theorem name 调用它们。因此，不能把“图中有依赖边”误写成“oracle proof 文本必然显式调用前置 lemma 名称”。

## 3. 实例选择概览

| 实例 | `case_id` | 形式结构 | 选择理由 |
|---|---|---|---|
| 1 | `lean_v2_medium_function_set_dx_subset_chain_02` | 原子前件 → 原子中间式 → 二项合取 | 最简单的非平凡结论增强 |
| 2 | `lean_v2_medium_function_set_dx_subset_chain_04` | 原子前件 → 二项析取 → 二项合取 | 清楚展示析取中间层被下一条规则统一消费 |
| 3 | `lean_v2_medium_function_set_dx_subset_chain_06` | 二项合取 → 二项析取 → 二项合取 | 输入、中间式和输出均有复合结构 |
| 4 | `lean_v2_medium_function_set_dx_subset_chain_11` | 二项析取 → 三项合取 → 原子结论 | 展示从分支前件汇聚到较强中间不变量，再投影到结论 |
| 5 | `lean_v2_medium_function_set_dx_subset_chain_15` | 嵌套析取前件 → 原子中间式 → 原子结论 | 五题中前件语法最丰富，同时保持推理链简洁 |

---

## 4. 实例一：原子前件经中间关系增强为合取结论

### 4.1 题库身份

- `case_id`: `lean_v2_medium_function_set_dx_subset_chain_02`
- 题库位置：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:14`
- root theorem：`function_set_medium_root_02`

### 4.2 Root theorem statement

```lean
theorem function_set_medium_root_02
    (α β : Type)
    (A B C D E F : α -> β -> Prop)
    (hAB : ∀ x y, B x y -> E x y)
    (hBC : ∀ x y, E x y -> B x y ∧ C x y) :
    ∀ x y, B x y -> B x y ∧ C x y
```

形式推理链：

```text
B x y  ──hAB──>  E x y  ──hBC──>  B x y ∧ C x y
```

### 4.3 四个节点

所有节点共享 root theorem 中的完整参数和假设上下文。

| 执行阶段 | 节点名称 | `node_kind` | Statement | 直接依赖 |
|---|---|---|---|---|
| 1A | `function_set_medium_ab_02` | `leaf_sublemma` | `∀ x y, B x y -> E x y` | 无 |
| 1B | `function_set_medium_bc_02` | `leaf_sublemma` | `∀ x y, E x y -> B x y ∧ C x y` | 无 |
| 2 | `function_set_medium_ac_02` | `intermediate_lemma` | `∀ x y, B x y -> B x y ∧ C x y` | `ab_02`, `bc_02` |
| 3 | `function_set_medium_root_02` | `root_theorem` | `∀ x y, B x y -> B x y ∧ C x y` | `ac_02` |

### 4.4 依赖边与顺序

```text
function_set_medium_ab_02 ──┐
                             ├──> function_set_medium_ac_02
function_set_medium_bc_02 ──┘                  │
                                                ▼
                                  function_set_medium_root_02
```

精确 `dependency_edges`：

```text
function_set_medium_ab_02 -> function_set_medium_ac_02
function_set_medium_bc_02 -> function_set_medium_ac_02
function_set_medium_ac_02 -> function_set_medium_root_02
```

题库 `dependency_order`：

```text
function_set_medium_ab_02
-> function_set_medium_bc_02
-> function_set_medium_ac_02
-> function_set_medium_root_02
```

### 4.5 Oracle proof skeleton

```lean
-- function_set_medium_ab_02
by
  exact hAB

-- function_set_medium_bc_02
by
  exact hBC

-- function_set_medium_ac_02
by
  intro x y h
  exact hBC x y (hAB x y h)

-- function_set_medium_root_02
by
  intro x y h
  exact hBC x y (hAB x y h)
```

### 4.6 形式代表性

该题是四节点图中最容易理解的非平凡形式：两个叶节点分别证明两段全称蕴含，中间节点进行函数式复合，得到比输入 `B x y` 更强的合取结论 `B x y ∧ C x y`。它适合用来首次解释固定 lemma-DAG 的并行叶节点、依赖屏障和最终检查。

---

## 5. 实例二：通过析取中间式得到合取结论

### 5.1 题库身份

- `case_id`: `lean_v2_medium_function_set_dx_subset_chain_04`
- 题库位置：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:32`
- root theorem：`function_set_medium_root_04`

### 5.2 Root theorem statement

```lean
theorem function_set_medium_root_04
    (α β : Type)
    (A B C D E F : α -> β -> Prop)
    (hAB : ∀ x y, D x y -> A x y ∨ B x y)
    (hBC : ∀ x y, A x y ∨ B x y -> A x y ∧ C x y) :
    ∀ x y, D x y -> A x y ∧ C x y
```

形式推理链：

```text
D x y  ──hAB──>  A x y ∨ B x y  ──hBC──>  A x y ∧ C x y
```

### 5.3 四个节点

| 执行阶段 | 节点名称 | `node_kind` | Statement | 直接依赖 |
|---|---|---|---|---|
| 1A | `function_set_medium_ab_04` | `leaf_sublemma` | `∀ x y, D x y -> A x y ∨ B x y` | 无 |
| 1B | `function_set_medium_bc_04` | `leaf_sublemma` | `∀ x y, A x y ∨ B x y -> A x y ∧ C x y` | 无 |
| 2 | `function_set_medium_ac_04` | `intermediate_lemma` | `∀ x y, D x y -> A x y ∧ C x y` | `ab_04`, `bc_04` |
| 3 | `function_set_medium_root_04` | `root_theorem` | `∀ x y, D x y -> A x y ∧ C x y` | `ac_04` |

### 5.4 依赖边与顺序

```text
function_set_medium_ab_04 ──┐
                             ├──> function_set_medium_ac_04
function_set_medium_bc_04 ──┘                  │
                                                ▼
                                  function_set_medium_root_04
```

```text
dependency_edges:
  function_set_medium_ab_04 -> function_set_medium_ac_04
  function_set_medium_bc_04 -> function_set_medium_ac_04
  function_set_medium_ac_04 -> function_set_medium_root_04

dependency_order:
  function_set_medium_ab_04
  -> function_set_medium_bc_04
  -> function_set_medium_ac_04
  -> function_set_medium_root_04
```

### 5.5 Oracle proof skeleton

```lean
-- 两个叶节点
by exact hAB
by exact hBC

-- intermediate_ac 与 root
by
  intro x y h
  exact hBC x y (hAB x y h)
```

### 5.6 形式代表性

该题把析取 `A x y ∨ B x y` 用作真正的中间接口。第一个叶节点负责产生析取，第二个叶节点给出一个统一消费整个析取的规则；中间节点只有在两者均可用后才能构造合取结论。它展示了 dependency edge 对证明组合顺序的约束，而不要求两个叶节点之间存在先后关系。

---

## 6. 实例三：合取—析取—合取的复合变换

### 6.1 题库身份

- `case_id`: `lean_v2_medium_function_set_dx_subset_chain_06`
- 题库位置：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:50`
- root theorem：`function_set_medium_root_06`

### 6.2 Root theorem statement

```lean
theorem function_set_medium_root_06
    (α β : Type)
    (A B C D E F : α -> β -> Prop)
    (hAB : ∀ x y, A x y ∧ B x y -> B x y ∨ C x y)
    (hBC : ∀ x y, B x y ∨ C x y -> C x y ∧ D x y) :
    ∀ x y, A x y ∧ B x y -> C x y ∧ D x y
```

形式推理链：

```text
A x y ∧ B x y
    ──hAB──> B x y ∨ C x y
    ──hBC──> C x y ∧ D x y
```

### 6.3 四个节点

| 执行阶段 | 节点名称 | `node_kind` | Statement | 直接依赖 |
|---|---|---|---|---|
| 1A | `function_set_medium_ab_06` | `leaf_sublemma` | `∀ x y, A x y ∧ B x y -> B x y ∨ C x y` | 无 |
| 1B | `function_set_medium_bc_06` | `leaf_sublemma` | `∀ x y, B x y ∨ C x y -> C x y ∧ D x y` | 无 |
| 2 | `function_set_medium_ac_06` | `intermediate_lemma` | `∀ x y, A x y ∧ B x y -> C x y ∧ D x y` | `ab_06`, `bc_06` |
| 3 | `function_set_medium_root_06` | `root_theorem` | `∀ x y, A x y ∧ B x y -> C x y ∧ D x y` | `ac_06` |

### 6.4 依赖边与顺序

```text
function_set_medium_ab_06 ──┐
                             ├──> function_set_medium_ac_06
function_set_medium_bc_06 ──┘                  │
                                                ▼
                                  function_set_medium_root_06
```

```text
dependency_edges:
  function_set_medium_ab_06 -> function_set_medium_ac_06
  function_set_medium_bc_06 -> function_set_medium_ac_06
  function_set_medium_ac_06 -> function_set_medium_root_06

dependency_order:
  function_set_medium_ab_06
  -> function_set_medium_bc_06
  -> function_set_medium_ac_06
  -> function_set_medium_root_06
```

### 6.5 Oracle proof skeleton

```lean
-- function_set_medium_ab_06
by exact hAB

-- function_set_medium_bc_06
by exact hBC

-- function_set_medium_ac_06 / function_set_medium_root_06
by
  intro x y h
  exact hBC x y (hAB x y h)
```

### 6.6 形式代表性

该题的输入、中间式和输出均为复合命题，能够比实例一更清楚地展示 proof artifact 不能只凭类型名称或单个原子命题拼接：组合节点必须保持相同的 `x, y`，先应用 `hAB` 得到析取，再把该析取完整传给 `hBC`。

---

## 7. 实例四：从析取经三项合取到原子结论

### 7.1 题库身份

- `case_id`: `lean_v2_medium_function_set_dx_subset_chain_11`
- 题库位置：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:95`
- root theorem：`function_set_medium_root_11`

### 7.2 Root theorem statement

```lean
theorem function_set_medium_root_11
    (α β : Type)
    (A B C D E F : α -> β -> Prop)
    (hAB : ∀ x y, A x y ∨ C x y -> A x y ∧ B x y ∧ C x y)
    (hBC : ∀ x y, A x y ∧ B x y ∧ C x y -> F x y) :
    ∀ x y, A x y ∨ C x y -> F x y
```

形式推理链：

```text
A x y ∨ C x y
    ──hAB──> A x y ∧ B x y ∧ C x y
    ──hBC──> F x y
```

### 7.3 四个节点

| 执行阶段 | 节点名称 | `node_kind` | Statement | 直接依赖 |
|---|---|---|---|---|
| 1A | `function_set_medium_ab_11` | `leaf_sublemma` | `∀ x y, A x y ∨ C x y -> A x y ∧ B x y ∧ C x y` | 无 |
| 1B | `function_set_medium_bc_11` | `leaf_sublemma` | `∀ x y, A x y ∧ B x y ∧ C x y -> F x y` | 无 |
| 2 | `function_set_medium_ac_11` | `intermediate_lemma` | `∀ x y, A x y ∨ C x y -> F x y` | `ab_11`, `bc_11` |
| 3 | `function_set_medium_root_11` | `root_theorem` | `∀ x y, A x y ∨ C x y -> F x y` | `ac_11` |

### 7.4 依赖边与顺序

```text
function_set_medium_ab_11 ──┐
                             ├──> function_set_medium_ac_11
function_set_medium_bc_11 ──┘                  │
                                                ▼
                                  function_set_medium_root_11
```

```text
dependency_edges:
  function_set_medium_ab_11 -> function_set_medium_ac_11
  function_set_medium_bc_11 -> function_set_medium_ac_11
  function_set_medium_ac_11 -> function_set_medium_root_11

dependency_order:
  function_set_medium_ab_11
  -> function_set_medium_bc_11
  -> function_set_medium_ac_11
  -> function_set_medium_root_11
```

### 7.5 Oracle proof skeleton

```lean
-- 两个叶节点
by exact hAB
by exact hBC

-- intermediate_ac 与 root
by
  intro x y h
  exact hBC x y (hAB x y h)
```

### 7.6 形式代表性

该题展示了较强的中间不变量：第一段推理把析取前件提升为三项合取，第二段再以完整合取为输入得到原子结论。它适合说明中间 lemma 的价值不只在缩短最终证明，也在为下游节点提供一个精确、可检查的接口 proposition。

---

## 8. 实例五：嵌套前件经两段蕴含得到最终结论

### 8.1 题库身份

- `case_id`: `lean_v2_medium_function_set_dx_subset_chain_15`
- 题库位置：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:131`
- root theorem：`function_set_medium_root_15`

### 8.2 Root theorem statement

```lean
theorem function_set_medium_root_15
    (α β : Type)
    (A B C D E F : α -> β -> Prop)
    (hAB : ∀ x y, (A x y ∧ B x y) ∨ C x y -> A x y)
    (hBC : ∀ x y, A x y -> D x y) :
    ∀ x y, (A x y ∧ B x y) ∨ C x y -> D x y
```

形式推理链：

```text
(A x y ∧ B x y) ∨ C x y
    ──hAB──> A x y
    ──hBC──> D x y
```

### 8.3 四个节点

| 执行阶段 | 节点名称 | `node_kind` | Statement | 直接依赖 |
|---|---|---|---|---|
| 1A | `function_set_medium_ab_15` | `leaf_sublemma` | `∀ x y, (A x y ∧ B x y) ∨ C x y -> A x y` | 无 |
| 1B | `function_set_medium_bc_15` | `leaf_sublemma` | `∀ x y, A x y -> D x y` | 无 |
| 2 | `function_set_medium_ac_15` | `intermediate_lemma` | `∀ x y, (A x y ∧ B x y) ∨ C x y -> D x y` | `ab_15`, `bc_15` |
| 3 | `function_set_medium_root_15` | `root_theorem` | `∀ x y, (A x y ∧ B x y) ∨ C x y -> D x y` | `ac_15` |

### 8.4 依赖边与顺序

```text
function_set_medium_ab_15 ──┐
                             ├──> function_set_medium_ac_15
function_set_medium_bc_15 ──┘                  │
                                                ▼
                                  function_set_medium_root_15
```

```text
dependency_edges:
  function_set_medium_ab_15 -> function_set_medium_ac_15
  function_set_medium_bc_15 -> function_set_medium_ac_15
  function_set_medium_ac_15 -> function_set_medium_root_15

dependency_order:
  function_set_medium_ab_15
  -> function_set_medium_bc_15
  -> function_set_medium_ac_15
  -> function_set_medium_root_15
```

### 8.5 Oracle proof skeleton

```lean
-- function_set_medium_ab_15
by exact hAB

-- function_set_medium_bc_15
by exact hBC

-- function_set_medium_ac_15 / function_set_medium_root_15
by
  intro x y h
  exact hBC x y (hAB x y h)
```

### 8.6 形式代表性

该题拥有五题中语法最丰富的前件：析取的一侧自身是合取。尽管中间和最终结论都是原子 proposition，组合节点仍必须保持嵌套前件的完整结构，并按 `hAB` 后接 `hBC` 的顺序构造证明。

## 9. 五题的统一执行顺序

对每一题，协议级执行顺序都可以写成以下三个阶段：

```text
阶段 1（可并行）
  ├─ 证明 function_set_medium_ab_N
  └─ 证明 function_set_medium_bc_N

阶段 2（等待阶段 1 的两个 accepted proofs）
  └─ 证明 function_set_medium_ac_N

阶段 3（等待阶段 2）
  └─ 证明 function_set_medium_root_N

节点执行结束后（不属于第五个节点）
  └─ merge 四个 required proof slots，并对组装后的顶层证明进行最终 Lean 检查
```

其中 `N ∈ {02, 04, 06, 11, 15}`。

这一顺序不能误写成四个节点完全串行。题库的 `dependency_order` 是确定性拓扑顺序，不会取消两个叶节点之间的并行可能性。反过来，也不能把四个节点写成全部并行，因为 `ac_N` 有两个前置依赖，而 `root_N` 依赖 `ac_N`。

## 10. 给论文写作 AI 的使用约束

1. **不得写成 AI 自主发现了这些 lemma。** 图、节点名称、statement 和 dependency edges 都由题库预注册；AI 只为固定 proof unit 生成候选证明。
2. **不得为 `A`–`F` 编造题库没有给出的现实语义。** 它们是类型 `α -> β -> Prop` 的抽象二元谓词。
3. **不得把 oracle proof 当作模型实际输出。** Oracle package 证明题目在固定 Lean 环境中可检查；真实实验中的模型输出和结果必须以对应 run 的记录为准。
4. **必须区分拓扑序与执行阶段。** 两个叶节点无依赖，可并行；中间节点等待两个叶节点；root 等待中间节点。
5. **必须区分 intermediate、root proof unit 与最终检查。** intermediate 和 root statement 相同不表示节点重复无意义；root proof unit 完成后，插件还会在 merge 阶段对组装后的顶层证明执行最终 Lean 检查。
6. **不得概括为全部 Lean 题都只有四个节点。** 本文五题属于 `function_set × medium_lemma_dag`；正式题库还包含其他 topic、difficulty、节点数和图深度。
7. **论文术语优先使用普通学术表达。** 可使用 `task graph/DAG`、`subtask`、`accepted proof`、`merge` 和 `final verification`；不要把内部存储或实现名词扩写成新的理论概念。

## 11. 可用于论文的精简描述

下面这段可以作为论文写作的事实素材，但仍应根据正文上下文调整：

> For medium-difficulty Lean tasks, the benchmark includes fixed four-node lemma DAGs with two independent leaf lemmas, one intermediate composition lemma, and one root-theorem proof unit. The two leaves can be attempted concurrently. The intermediate node becomes executable only after both leaf proofs have been accepted, and the root node follows the intermediate proof. Once all required proof slots are available, the task-specific module merges them and performs final Lean verification on the assembled top-level proof. The decomposition is preregistered; the language model generates proof candidates for fixed nodes rather than discovering the task graph.

这段描述只概括图结构和执行边界，不声称五个实例代表所有 Lean 题，也不声称 oracle proof 是实验中的模型回答。

## 12. 事实来源

- 五个 case 的 theorem payload、node statements、`dependency_edges`、`dependency_order`、required slots 和 oracle proof sources：`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl:14,32,50,95,131`。
- Lean fixed lemma-DAG、依赖调度、真实 checker 与 root recheck 的系统接线：`Doc/SlimV2/slim_v2_system_integration_contract.md` 第 4.1–4.3 节。
- 论文侧关于 Lean 应用及术语边界的共同基线：`paper/PAPER_WRITING_BRIEF.md` 第 4.2 节与 `paper/PAPER_TERMINOLOGY_MAPPING.md`。
