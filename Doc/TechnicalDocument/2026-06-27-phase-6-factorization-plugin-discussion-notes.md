# Phase 6 Factorization 插件讨论记录

日期：2026-06-27

状态：Phase 6 factorization 插件设计讨论记录。本文记录已经确认的 factorization 第一版拆分算法和协议边界；本文不是最终字段规格，也不是实现计划。后续需要把确认后的字段、artifact schema、event payload、SQLite projection、故障模拟和 TDD 任务收束到 Phase 6 专用字段规格。2026-06-27 重读主 TDD 后补充：本文规划的 factorization 插件就是主 TDD 第 14.1 节的“整数分解插件”，不是另一个插件；所谓递归 continuation 也不是新增独立机制，而是主 TDD 已经要求的 canonical output 驱动、插件版本化规则驱动的渐进式扩图闭环。

## 0. 主 TDD 对齐修正

本轮重读 `2026-06-03-tokenshare-protocol-technical-design.md` 后，需要先修正两处容易误解的口径：

1. **同一个插件**：当前讨论的 `factorization` 插件，与主 TDD 第 14.1 节“整数分解插件”是同一个 Phase 6 实验插件。后续不应新建 `continuation` 插件，也不应把 factor search、range merge、`d/q` 递归展开拆成多个协议外插件。
2. **不是注册时一次性建完整图**：主 TDD 要求注册根任务时固定 plugin、plugin version、split strategy 和根输入；任务图随后根据 canonical output 逐步展开。对 factorization 来说，根 `factor_integer(N)` 先展开为 range search 子图；当 merge canonical 输出 `nontrivial_factor_found(d, q)` 后，再由同一个 factorization 插件的版本化规则生成下一层 `factor_integer(d)` / `factor_integer(q)` 子任务或 prime leaf 完成事实。

重读主 TDD 后确认的硬要求如下：

1. **插件主导扩图**：AI、executor、客户端不能提供 expansion 候选拆分，也不能临时发明协议级子任务；`DecompositionProposal` 必须由插件版本化 split strategy 基于 canonical output 直接生成。
2. **canonical gate 是扩图入口**：`VERIFICATION_RECORDED` 只给候选资格；真正驱动下一步 `complete` / `expand` 的是 `CANONICAL_OUTPUTS_BOUND` 绑定后的正式输出束。
3. **任务图和输出解析分离**：`TaskRelation` 表示拆分和依赖 DAG；父输出满足关系必须通过 `ExpectedOutputRef` / merge resolution 表示，不能把权威 resolution state 藏进 `TaskUnit.plugin_payload` 或 metadata。
4. **merge 是普通 TaskUnit**：产生父输出的合并必须走 request / submission / verification / canonical 生命周期；插件 merge rule 不能绕过协议生命周期直接写父输出。
5. **重放不执行历史工作**：state replay 只能读取 event 和 artifact；不能重新调用 executor、AI、插件拆分、插件合并或验证器。因此 split proposal、merge plan、range result、verification report、merge record 和实验随机决定都必须 artifact/event 化。
6. **插件职责不止拆分算法**：Phase 6 字段规格必须覆盖 descriptor、typed ports、root/child schema、execution contract、执行说明、raw output parser、verification rule、split strategy、completion/expansion decision、merge rule、output contract、metrics/fault assertions。
7. **实验基础设施也是 Phase 6 范围**：主 TDD Phase 6 交付不只是三个插件，还包括 `SimulationProfile`、`SimulationWrapper`、`ExperimentRunner`、`MetricsCollector`，以及 offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
8. **早完成和剪枝是主 TDD 原始目标**：第 14.1 节写到“任一子节点找到有效因数时，父节点可以完成并取消不再需要的兄弟子树”。当前 Phase 5 all-required merge 会阻止这一点；如果 Phase 6 第一版继续采用 all-required merge，就必须在字段规格中把它标成有意的阶段切片，而不是假装已经满足完整 TDD 14.1。

对“continuation”的修正口径：

- `continuation` 不是新增协议机制，也不是另一个插件；它是主 TDD 已有的“结果驱动递归展开”在 factorization 插件中的具体映射。
- `nontrivial_factor_found(d, q)` 一旦通过 merge task 成为 canonical fact，后续递归只能由 factorization 插件生成协议可见的 `DecompositionProposal`，把 `d` 和 `q` 转成新的 `factor_integer` 子任务、prime leaf 或受控的 complete decision。
- AI / executor 可以执行 bounded `factor_search_range`，也可以在将来执行其他插件已定义的 bounded leaf work，但不能把完整质因数递归藏在一次 range 子任务的自由文本或私有代码路径里。
- `d` / `q` 是否由插件先做确定性小规模 primality check、还是统一生成 `factor_integer` 子任务再通过 `prime_certificate` 完成，是 Phase 6 字段规格需要显式确认的算法决策；不能默认为 AI 私下判断。

## 1. 已确认主轴

Factorization 插件第一版拆分算法采用 **候选因子搜索空间分区**。

核心口径：

1. 插件负责把输入整数 `N` 的候选因子搜索域拆成协议可理解的子任务图。
2. AI / executor 只处理插件已经确定边界的 `factor_search_range` 子任务。
3. AI / executor 不负责提出 factor pair，不负责决定如何拆分任务，也不能直接修改任务图。
4. 插件负责验证每个 range result，并把 range results 通过 `MergePlan` 合并成父任务可理解的结果。
5. 协议核心不理解 factorization 数学规则，只记录插件版本、split strategy、proposal、child specs、merge slots、verification / canonical / merge 事件和 artifact hash。

这意味着第一版不能采用“AI 先找一个因子，插件再验证并递归拆分”的方案。那种方案会把任务拆分权交给 AI，插件退化为验证器和合并器，违反 TokenShare 的插件主导目标。

## 2. 第一版拆分算法

给定目标整数 `N`，插件 split strategy 计算候选因子搜索域：

```text
candidate_domain = [2, floor_sqrt(N)]
```

随后按确定性参数把该区间拆成多个连续范围：

```text
range_i = [range_start_i, range_end_i]
```

第一版推荐使用 `fixed_child_count contiguous ranges`：

1. 输入参数包含 `target_n`、`child_count`、`min_divisor`、`max_divisor`、`range_policy=contiguous` 和可选小素数预检结果。
2. 插件生成的 ranges 必须覆盖完整搜索域。
3. ranges 之间不能重叠，不能有 gap。
4. range 顺序、边界和 `coverage_id` 必须可由相同输入确定性重算。
5. 每个子任务类型是 `factor_search_range`，子任务只声明“在 `[range_start, range_end]` 内寻找 `N` 的非平凡因子”。

第一版先选择连续区间分区，而不是 Pollard Rho seed-space、wheel residue class 或自适应搜索树。原因是连续区间最容易验证 coverage、no gap、no overlap 和 merge completeness，适合 Phase 6 首个插件实验证明协议边界。

## 3. 子任务语义

每个 `factor_search_range` 子任务输入至少包含：

| 字段 | 说明 |
|---|---|
| `target_n` | 十进制字符串形式的大整数，避免 JSON number 精度语义不清。 |
| `range_start` | 当前候选因子范围起点。 |
| `range_end` | 当前候选因子范围终点。 |
| `coverage_id` | 本次分区的稳定覆盖标识。 |
| `child_index` | 当前 range 在分区中的稳定序号。 |
| `partition_params_digest` | split strategy 参数摘要。 |

子任务输出 `range_result` 至少区分两类结果：

1. `found_factor`：声明在当前范围内找到因子 `d`。
2. `no_factor_in_range`：声明当前范围内不存在 `N` 的因子。

AI / executor 可以使用自然语言推理或程序辅助搜索，但协议只接受结构化 `range_result` artifact。自由文本推理、hidden reasoning 或临时建议不能成为协议事实。

## 4. 插件验证边界

插件 verifier 对 `found_factor` 至少检查：

1. `d` 是整数。
2. `1 < d < N`。
3. `range_start <= d <= range_end`。
4. `N % d == 0`。
5. 输出 artifact 的 `target_n`、range、`coverage_id` 和 params digest 与子任务输入一致。

插件 verifier 对 `no_factor_in_range` 不能只相信自然语言声明。第一版必须采用可重放验证策略：

1. 对实验规模可控的 range，插件用确定性本地检查重新验证该范围无因子。
2. 如果后续引入更大 range 或证明型证书，必须把证书 artifact 化，并让 verifier 可重放检查证书。

Phase 6 第一版优先采用确定性 range recheck，牺牲性能换取审计清晰度。否则“无因子”会变成不可验证的 AI 断言。

## 5. 协议表示方式

Factorization 插件应声明一个版本化 split strategy，例如：

```text
factorization.candidate_range_partition.v1
```

该 strategy 生成 `DecompositionProposal` 时，应把拆分方式显式放入协议可理解的结构：

| `DecompositionProposal` 区块 | Factorization 映射 |
|---|---|
| `proposal_header` | 记录 plugin id/version、split strategy id、params digest、目标 unit 和 canonical input/output 来源。 |
| `child_specs` | 每个 range 一个 `factor_search_range` child，输入绑定为 `target_n`、`range_start`、`range_end`、`coverage_id` 等结构化字段。 |
| `dependency_edges` | 第一版 range search 之间通常无依赖，可并行执行。 |
| `expected_outputs` | 父任务期望输出由后续 merge 解析，例如 `factorization_search_result`。 |
| `merge_slots` | 每个 range child 的 `range_result` 是一个 required slot。 |
| `promotion_guard_evidence` | 内联 coverage proof：domain start/end、range count、no gap、no overlap、sqrt bound、params digest。 |

对应 `MergePlan` 使用 all-required slots：

1. `required_slots` 覆盖所有 range children 的 `range_result`。
2. `merge_validation_requirements` 要求检查 range coverage、slot count、child canonical output digest、`coverage_id` 一致性。
3. `parent_output_mapping` 把 merge output 映射为父任务 expected output。
4. `plugin_payload` 只能携带 merge policy 所需的非权威配置摘要，不能让 child payload 或 metadata 成为任务图、slot coverage 或 completion 的权威来源。

## 6. Merge 语义

第一版 merge policy：

1. 如果任意 required range slot 给出已验证 `found_factor(d)`，merge 输出 `nontrivial_factor_found`，包含 `d` 和 `N / d`。
2. 如果所有 range slots 都给出已验证 `no_factor_in_range`，且 ranges 完整覆盖 `[2, floor_sqrt(N)]`，merge 输出 `prime_certificate`。
3. 如果存在 slot 缺失、range coverage 不完整、重复 coverage、digest 不一致、`coverage_id` 不一致或输出类型非法，merge 必须拒绝。

受 Phase 5 当前 all-required merge 边界限制，第一版即使某个 range 已经找到因子，也仍需要等待所有 required range canonical outputs 后才能 merge。`one_success`、optional slots、partial merge、early terminal resolution 和 factorization early pruning 仍是后续扩展，不进入第一版字段规格。

主 TDD 第 14.1 节把“任一子节点找到有效因数时父节点可以完成并取消兄弟子树”列为整数分解插件目标。所以上述 all-required 口径只能作为 Phase 6 第一切片的保守实现选择；最终 Phase 6 字段规格必须要求用户确认：是第一版就扩展 `one_success` / early terminal resolution / pruning 以完整覆盖 TDD 14.1，还是先用 all-required 跑通协议闭环并把 early success 明确列为未覆盖项。

## 7. 大数与实验边界

“任何大数输入”在 Phase 6 第一版中应解释为：插件可以为任意合法正整数 `N` 生成确定性、可审计、可验证 coverage 的候选因子搜索计划。

这不等于保证任意巨大合数都能在合理时间内完成。穷举到 `floor_sqrt(N)` 是完整但可能不可行的算法。Phase 6 实验应选择可在本地测试预算内完成的样例，同时保留 schema 能表达更大搜索空间的能力。

## 8. 后续字段规格必须解决的问题

本轮确认的“递归 continuation”不是独立机制，而是同一个 factorization 插件在 canonical output 后继续生成协议可见子图。Phase 6 字段规格必须继续明确：

1. 单一插件包边界：`tokenshare.plugins.factorization` 内部包含 descriptor、schema、split strategies、validators、merge policies、fixtures 和测试；不拆出另一个 continuation 插件。
2. `factor_integer`、`factor_search_range`、merge task 和最终 `prime_factorization_result` 的 unit type / output contract。
3. 当 `nontrivial_factor_found(d, q)` 成为 canonical merge output 后，插件如何生成下一层 `DecompositionProposal`：对 `d` / `q` 创建新的 `factor_integer` 子任务、prime leaf complete decision，或其他明确版本化策略。
4. `d` / `q` primality 判断放在哪里：插件确定性预检、递归 `factor_integer` 子任务、还是两者组合；无论哪种，AI 不能私自决定递归终止。
5. 当前 Phase 5 all-required merge 对 factorization early success 的效率影响，以及是否在 Phase 6 第一版就引入 `one_success` / early terminal resolution / pruning 来满足主 TDD 第 14.1 节。
6. `range_result`、`factorization_search_result`、`prime_certificate`、`nontrivial_factor_found` 和 `prime_factorization_result` 的 artifact schema version。
7. plugin descriptor 中如何声明 `candidate_range_partition.v1`、递归展开策略、merge policy、pruning policy、execution contract、output contract、environment policy 和 metrics labels。
8. 对 invalid output、late submission、executor_error、slow、offline 五类故障模拟的 factorization 专用断言。
9. replay 边界：哪些 split / merge / verification / experiment artifacts 必须持久化，确保 replay 不重新调用插件逻辑来补历史事实。

## 9. 第一批 TDD 方向

后续 Phase 6 字段规格可以把本决策收束为以下红绿测试任务：

1. `candidate_range_partition_v1` 对给定 `N` 和 `child_count` 生成稳定、无 gap、无 overlap、全覆盖的 ranges。
2. split strategy 生成的 `DecompositionProposal.child_specs` 全部是 `factor_search_range`，且 `merge_slots` 与 child outputs 一一对应。
3. `found_factor` verifier 拒绝范围外因子、非因子、`target_n` 不一致和 digest 不一致。
4. `no_factor_in_range` verifier 对实验范围做确定性 recheck，拒绝实际存在因子的 no-factor 输出。
5. merge policy 在完整 coverage 且存在 verified factor 时输出 `nontrivial_factor_found`。
6. merge policy 在完整 coverage 且所有 range 无因子时输出 `prime_certificate`。
7. 端到端实验用小型 semiprime 和 prime 样例证明插件主导拆分、执行器 bounded search、插件验证、all-required merge 和 Phase 5 contribution / settlement 能串起来。
8. 专门记录当前 all-required merge 导致无法 early success 的限制，避免后续误以为 factorization 插件已经实现剪枝优化。
9. `nontrivial_factor_found(d, q)` 成为 canonical merge output 后，插件生成下一层 `factor_integer(d)` / `factor_integer(q)` 的 `DecompositionProposal`，且该 proposal 不包含 AI 生成的拆分建议。
10. `factorization` 插件 descriptor 同时声明 range partition、递归展开、验证和 merge/pruning policy；测试禁止注册第二个 continuation 插件来完成同一职责。
11. replay 测试删除 SQLite 后从 event + artifact 重建 factorization 关键状态，不重新调用 split strategy、merge rule 或 verifier。
12. 如果第一版仍采用 all-required merge，应有显式测试证明 early-success/pruning 未被宣称为已实现；如果选择覆盖主 TDD 14.1，应增加 `one_success` / pruning 的红绿测试。
