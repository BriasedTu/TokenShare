# TokenShare 真实 AI API 论文实验设计与实施规格

> 状态：唯一权威实验设计
> 生效日期：2026-07-12
> 适用范围：论文实验、实验 runner 改造、论文表格与图、实验执行和结果审计

> **强制口径：** 自 2026-07-12 起，旧 Phase 8 Experiment 1-4、scripted/fake transport、deterministic fixture 和 direct 500-number benchmark 只保留为回归、输入来源或成本校准，不得作为新论文主实验结果。所有可写入论文的新实验必须实际调用真实 AI API，并保留可审计的 provider、model、usage、latency、cost 和 raw-output 证据。

# 文档目的、来源与替代关系

本文档把导师研讨意见转化为可直接实现和执行的实验规格。它回答以下问题：为什么要做每个实验；输入、变量、控制条件和重复次数是什么；当前系统缺什么；agent 应修改哪些程序；每次运行必须输出哪些数据；哪些结果可以进入论文；需要多少 API、时间、token、成本和人工检查。

本文档不沿用旧实验问题结构。旧的 Phase 8 实验基础设施仍可用于回归，但旧的实验设计文稿 `2026-06-29-phase-8-experiment-infrastructure-tdd.md` 被删除。`2026-06-29-phase-8-experiment-infrastructure-code-map.md` 继续保留，因为它是当前实现事实和验证证据映射，不是第二份实验设计。

研讨录音转写存在以下高置信度纠正：`令` 指 Lean，`头疼` 指 token，`force positive/negative` 指 false positive/false negative，`obligation` 在消融上下文中指 ablation，`skin low` 指 scaling law。导师举出的 3/30/300 workers、1%–100% 和黎曼猜想是问题尺度示例，不应机械解释为逐整数故障率或要求运行著名未解问题。

# 设计一致性决议（Additive Consistency Decisions）

若本文不同段落对同一实验数量、条件或模式出现局部不一致，采用加法原则修正：在工程和预算上合理、且不违反真实 AI / deterministic verifier 边界的条件都进入计划；不得为了让数字变小而默认删减实验。删减只能作为显式预算决策写入 suite manifest，不能静默发生。

本版固定以下一致性决议：

1.  Experiment 2 P0-core scaling 使用两个 domain、三个 paper difficulty、每档固定 5-task batch、4 个强制 worker levels（1, 3, 10, 30）和 5 次 repeat，共 600 个 root-runs。factorization 的三档是 easy / medium / hard；Lean 的三档是 simple / medium lemma-DAG / hard-frontier，当前 shallow v1 只能填 simple。100 和 300 worker levels 是 preflight-gated extension：quota、AI unit 数量和机器资源都满足时运行；不满足时输出 `unsupported_worker_level`，不补造曲线。

2.  Experiment 3 的 rate-fault 矩阵只包含 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。`worker_death` 永远单独进入 worker-death 矩阵，避免被 rate-fault 统计重复计算。

3.  Experiment 4 P0-core ablation 使用每个 domain / paper difficulty 固定 5 个 tasks、6 个模式和 3 次 repeat，共 540 个 root-runs。旧的 3-task 数字不再作为正式 P0 口径；若 Lean medium lemma-DAG / hard-frontier catalog 未完成，Lean 对应难度的 ablation claim 必须 blocked 或降级。

4.  Experiment 5 改为预注册的三模型 model-provider endpoint comparison：SiliconFlow `zai-org/GLM-5.2`、SiliconFlow `Qwen/Qwen3.6-27B`、OpenAI `gpt-5.6-sol` with `reasoning_effort=high`。三个真实 entry、provider transport、reasoning profile 和 smoke evidence 都可用时纳入 P0-full；缺少任一 cohort member 时输出结构化 `blocked`，不算协议失败，也不影响 Experiment 1-4 的主张。

5.  P0-core 指 Experiment 1-4；P0-full 指 Experiment 1-5 且模型策略 preflight 通过。所有 summary、预算和论文表格必须标明自己属于 P0-core、P0-full 还是包含 100/300 worker extension 的扩展运行。

# 论文要回答的三个主问题

论文实验章节只围绕三个主张组织：

1.  **可行性（Feasibility）**：同一协议生命周期能否在 factorization 和真实 Lean proof 两个不同领域中，用真实 AI API 生成候选输出，并由各自确定性 verifier/checker 给出可审计结果？

2.  **扩展性（Scalability）**：固定任务和模型时，增加协议 worker/node 是否改变端到端时间、吞吐、token、成本和失败率？收益在哪个并发点开始饱和？

3.  **鲁棒性（Robustness）**：真实 AI 输出之后发生 false positive、false negative、不返回、延迟、executor error 或 worker death 时，协议能否检测、隔离、重试、重分配并完成？哪些错误只能检测而不能恢复？

协议消融用于解释第三个主张中各机制的贡献；三模型 model-provider endpoint comparison 只作为次要分析，不单独证明协议正确性，也不把跨 provider 的 latency/cost 差异解释为纯模型效应。

# 论文可采信硬门槛

## 真实 AI API 门槛

一条 run 只有同时满足以下条件，才可标记 `paper_eligible=true`：

1.  `real_transport=true`，且配置来自被 gitignore 的本地 config；标准配置只记录 `api_key_env`。

2.  每个需要 AI 生成的 `TaskUnit` 至少存在一次真实 provider attempt；不得用 scripted response、mock executor 或 deterministic answer 替代。

3.  保存 request、raw model output、parsed output 或 parse failure、provider provenance、usage、latency、cost estimate 和模型身份 artifact。

4.  输出中不存在 API key；event、artifact、SQLite、日志、config digest 和论文 CSV 均不得包含 secret。

5.  AI 不决定协议级拆分。factorization 和 Lean 的拆分仍由插件确定性规则生成。

6.  factorization 结果必须经插件 parser/verifier；Lean proof 必须经固定本地 Lean/lake/toolchain/project checker。

7.  replay/metrics/report 阶段不重新调用 AI API，也不重新调用 Lean 来补写历史成功事实。

任何缺少真实 provider attempt 的 run 都必须输出 `paper_eligible=false` 和具体 `ineligibility_reasons`。现有 `run_all` 默认 suite、scripted Lean 50、scripted AI profile 和 deterministic fixture 即使测试通过，也只能标记 `regression_only=true`。

## 受控故障仍必须经过真实 API

故障注入不能绕过真实 API。正确流程是：

1.  正常发送真实 API request，保存 provider provenance、raw output 和 usage。

2.  在预先声明的注入点对 parsed candidate、submission、lease 或 worker 进程做受控变换。

3.  保存独立 `FaultInjectionRecord`，同时引用原始真实输出和变换后输出。

4.  恢复 attempt 若需要 AI 候选，必须再次调用真实 API；不得复用未被协议接受的历史 candidate 假装恢复完成。

因此，论文必须把“自然模型错误”和“真实输出后的受控注入错误”分开统计。注入变换本身消耗 0 个 provider token；原始调用和恢复调用的实际 token 必须全部计入。

# 当前系统事实与关键缺口

| 组件 | 当前已有能力 | 新实验缺口 |
|:---|:---|:---|
| Phase 7 AI executor | 真实 SiliconFlow-compatible transport；raw/parsed/error/usage/latency/cost/provenance artifact；secret 和 replay guard。 | 新论文 runner 必须强制 real transport、预算门禁、固定模型策略和每个 AI unit 的 provider-attempt coverage。 |
| Phase 8 default suite | 通用 runner、adapter、simulation、metrics/report；默认 Experiment 1–4。 | 默认使用 deterministic/scripted 路径，旧 case 不再是论文实验；`SimulationProfile` 也没有 fault rate、worker count、difficulty、repeat、model policy。 |
| Factorization 500 benchmark | 500 个 deterministic semiprime、真实 API direct answer、并发、准确率、token/cost/latency。 | 它直接让模型给完整分解，`worker_count` 并发的是独立整数，不是协议内部 range workers；不能单独证明协议 lifecycle 或 worker scaling。 |
| Factorization adapter | 真实插件 fixture、deterministic split/verifier/merge。 | 缺少“协议 range child 全部由真实 API 执行”的批量论文路径、难度 catalog、factor position 控制和同一 root 内 worker 并发。 |
| Lean AI 50 | 50 个 `P ∧ Q` / `P ↔ Q` 任务；真实 API child proof、parser、checker、merge/root recheck。 | 仅两种浅层结构、没有难度标签、固定顺序、没有 worker/model/request-limit CLI、缺少故障和消融入口，容易出现接近全对而无区分度。 |
| Metrics | 可从 events/artifacts 复算 event coverage 和 AI usage/cost。 | 某些旧 helper 把 canonical pollution、requeue、premature merge 等固定写成 0/1；新论文指标必须从实际事件、attempt、verification 和 fault record 复算。 |
| Worker fault | 旧 wrapper 可记录 offline/slow/executor_error/invalid_output/late_submission 决策。 | 只是报告层决策；缺少真实 API 后注入、故障率选择、真实 worker process 终止、lease expiry 和 replacement attempt 证据。 |
| Report | JSON/CSV/run manifest。 | 缺少按条件聚合、重复统计、置信区间、论文图数据、预算报告、paper eligibility 和 secret scan report。 |

# 统一术语、实验单位和控制变量

## 术语

| 术语 | 定义 |
|:---|:---|
| root task | 一个 factorization 整数或一个 Lean theorem payload 的协议根任务。 |
| AI unit | 一次需要真实 AI API 生成候选输出的叶子执行单元。 |
| worker | 可同时执行一个 AI unit 的实验执行槽。论文和代码统一使用 `worker_count`；不要与 attempt/run/node 混用。 |
| attempt | 一个 worker 对一个 AI unit 的一次执行尝试；provider failover attempts 单独统计。 |
| run | 一个固定 condition、repeat id 和 seed 下的完整实验批次。 |
| condition | domain、difficulty、worker count、fault、ablation、model policy 等自变量的唯一组合。 |
| task completion | 根任务得到插件认可的 final output；未完成、超时和 blocked 均计失败。 |
| accepted result validity | 已被协议接受的 final output 是否通过 deterministic oracle/checker。它和 completion rate 必须分开。 |

## 必须固定或记录的控制变量

同一对比组必须固定 input catalog digest、provider/model entry、prompt/parser/plugin/executor version、Lean environment digest、timeout、max tokens、provider-attempt limit、fault seed、worker scheduling policy、机器与 Python/Lean 版本。每次 run 记录开始/结束时间、进程数、CPU logical count、内存摘要和网络/provider rate-limit 事件。

真实 API 温度等非确定性参数必须写入 request artifact。论文主对比不得在看到结果后更换模型或 prompt；若必须修复 prompt contract，修复前后的结果分开成不同 experiment version。

# 输入 catalog 与难度定义

## Factorization catalog v1

新建并冻结 `benchmarks/paper/factorization_catalog.v1.jsonl`。主实验使用 30 个 root tasks，每档 10 个；另保留现有 500 输入作为 appendix/model-quality sanity check，不作为协议主结果。

factorization 难度不用十进制位数单独定义，而用插件实际搜索工作量定义：

| 难度 | candidate divisor count | factor position | 目的 |
|:---|:---|:---|:---|
| easy | 1–32 | early/middle/late 均衡 | 验证基本真实 API + parser/verifier/merge 闭环。 |
| medium | 33–128 | early/middle/late 均衡 | 测试更多 range units 和适度并发。 |
| hard | 129–512 | early/middle/late 均衡，并含 no-factor/prime case | 观察成本、失败和饱和，不追求全对。 |

每行至少包含：`case_id,target_n,oracle_prime_factors,candidate_start,candidate_end,candidate_divisor_count,factor_position_quantile,difficulty,split_params,source_seed`。target 和 oracle 由 deterministic generator 生成并在运行前验证，但候选执行必须走真实 AI API。

## Lean catalog 分层要求

`benchmarks/paper/lean_catalog.v1.jsonl` 已经冻结并可由固定 toolchain 检查，但 2026-07-15 起它只作为 **simple / shallow Lean catalog**：其中历史 `easy` / `medium` / `hard` 标签只表示 shallow-v1 proof-chain 长度和上下文干扰，不再满足正式论文的 Lean 三档难度定义。当前 v1 的全部 30 个 `P ∧ Q` / `P ↔ Q` case 都归入正式论文口径的 `simple` 层，只能用于 adapter、checker、artifact、fault/worker/ablation 基础设施验证和成本校准；不得用它们单独支撑“复杂 Lean 递归拆分”或“hard theorem proving”主张。

正式论文 Lean catalog 必须扩展为分层 catalog，建议新增 `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 或 `lean_catalog.v2.jsonl`，并把 `paper_difficulty` 与旧 `difficulty` 字段区分开：

| 正式层级 | 客观定义 | 构造规则 | 论文用途 |
|:---|:---|:---|:---|
| simple | 当前 v1 shallow theorem：单个 root theorem，顶层 `P ∧ Q` / `P ↔ Q`，通常 2 个 child，child proof 是直接假设或短 implication chain。 | 保留当前 v1，必要时把旧 `difficulty` 重命名或解释为 `shallow_v1_difficulty`。 | 验证 Lean adapter、checker、merge/root recheck、artifact evidence 和 fault/worker/ablation 基础机制。 |
| medium | 用户要求的递归 lemma-DAG：一个 root theorem 由多个 lemma 推出，每个 lemma 又可由多个 sublemma 推出，形成 2-3 层 dependency DAG。 | catalog 显式给出 lemma graph、dependency edges、expected depth、leaf proof units、merge slots、root theorem payload、oracle proof package 和 environment digest；拆分仍由 Lean 插件/确定性 catalog 规则决定，AI 只证明被分派的 proof unit。 | 支撑 TokenShare 的递归任务拆分、分派、验证、合并和 replay 主张；正式 Experiment 1/2/4 的 Lean 主张必须至少包含这一层。 |
| hard / frontier | 更复杂的混合题：多层 lemma-DAG、induction/rewrite/theorem reuse、跨主题组合，或接近人类研究难度的 frontier stress case。 | 如果 theorem 已有固定 oracle proof，必须 preflight 通过后才能作为 `paper_eligible` proof case；如果题目本身可能未解或人类也不一定能完成，只能作为 `frontier_stress` / `structured_blocked` / negative case，不能伪造成 Lean checker success。 | 观察系统在复杂/不可解任务下的 structured failure、budget、worker recovery、ablation 和 evidence 边界；不能把未解 conjecture 的失败算作协议失败。 |

medium lemma-DAG / hard-frontier catalog 每行至少包含：`case_id,paper_difficulty,root_theorem_payload,lemma_graph,dependency_edges,expected_depth,expected_leaf_count,expected_ai_unit_count,merge_plan_shape,oracle_proof_package_ref,environment_digest,preflight_status`。catalog freeze 前必须运行本地 Lean preflight，确保所有纳入 `paper_eligible` 的 theorem / lemma / root assembly 都可由固定 Lean/lake/toolchain/library 环境验证；AI 是否能找到 proof 不能作为纳入/排除条件，避免按结果挑题。

为了支持 medium lemma-DAG / hard-frontier，Lean 插件能力也必须随 catalog 提升：支持递归 lemma graph split、proof-file assembly、per-lemma checker evidence、multi-level merge/root recheck、dependency-aware slot integrity，以及必要的 deterministic split/merge 规则，例如 implication introduction、forall introduction、nested conjunction/iff、induction/rewrite skeleton。仍然禁止让 AI 决定协议级拆分；AI 输出只能作为 proof candidate，经 parser/checker 后进入 evidence。

### Lean catalog 题型矩阵目标

2026-07-15 起，正式 Lean catalog 的长期目标不只是三档难度，还应覆盖三类经过设计的题型。目标 catalog pool 使用 3 × 3 矩阵：`paper_difficulty` 为 `simple`、`medium_lemma_dag`、`hard_frontier`；`topic_family` 为 `pure_logic`、`function_set`、`induction`。

| `topic_family` | simple 目标 | medium_lemma_dag 目标 | hard_frontier 目标 |
|:---|:---|:---|:---|
| `pure_logic` | 当前 shallow `P ∧ Q` / `P ↔ Q` 可作为过渡样本，但正式 simple pool 应记录题型 metadata。 | 多层命题逻辑 lemma-DAG，例如 root 依赖 intermediate lemma，再依赖多个 leaf/sublemma proof units。 | 更深的量词、等价、rewrite 或 theorem reuse 组合；无 oracle 时只能 blocked / stress。 |
| `function_set` | 函数、集合、子集、像/原像或简单单调性相关 theorem，证明结构浅但不是纯 `P ∧ Q`。 | 函数与集合 theorem 的 2-3 层 lemma-DAG，例如先证明函数分段性质、集合刻画，再证明 root subset。 | 混合实数函数、集合构造、分类讨论、rewrite 和外部库 theorem reuse；必须先做 oracle feasibility review。 |
| `induction` | Nat/List 上的简单归纳或递归定义 theorem，root proof 可以由一个短 induction skeleton 完成。 | 一个 root theorem 依赖多个归纳/辅助 lemma，形成 recursive lemma-DAG，AI 只证明分派 proof unit。 | 嵌套归纳、互相依赖 rewrite、theorem reuse 或较长 proof-file assembly；无固定 oracle proof 时不能标 checker success。 |

用户最新决策是：当前正式实验目标要扩大到覆盖上述 Lean 3 × 3 题型矩阵，而不是把它只当远期 catalog pool。每个 `(paper_difficulty, topic_family)` 单元的目标规模仍是 10-20 道可审计 case；扩大后的 suite version、抽样规则、预算审批和输出表格必须显式记录，不得静默沿用旧 P0 口径，也不得用当前 shallow v1 Lean case 补齐 medium / hard。

该决策目前是文档层要求，不表示 runner、paper adapter 或 Lean plugin 已具备执行能力。进入实现前必须先完成题库与拆分机制复核：每个题型/难度先做 1-2 个 checker-backed golden case；明确 `topic_family` schema、deterministic catalog rule / Lean plugin rule / fixed oracle package 的责任边界；确认 recursive lemma-DAG split、proof-file assembly、dependency-aware merge/root recheck、preflight 时间和真实 AI provider 预算。只有这些复核通过后，才能按 TDD 扩成正式批量题库并修改 runner / adapter；不能先写实验层代码假装这些题已经可拆、可合并或可 paper-eligible。

扩展 schema 时应考虑显式记录 `topic_family`、`topic_family_version`、`construction_rule_id` 或等价 metadata，并继续要求 `environment_digest`、`preflight_status`、oracle package hash 和 checker evidence。当前已存在的 `lean_catalog.v1.jsonl` 仍只算 simple/shallow 过渡输入；当前 `lean_lemma_graph_catalog.v1.jsonl` 只有最小 medium golden fixture，不满足上述完整矩阵。

后续 reviewer 应先使用 `Doc/TechnicalDocument/2026-07-15-feat-011-lean-tiered-topic-catalog-review-prompt.md` 复核矩阵可行性、Lean 规则成本、oracle package 组织方式、正式实验扩大后的预算影响和 runner / adapter 实现顺序，再进入实现。

# Experiment 1: 真实 AI 跨领域可行性与难度

## 为什么需要

论文首先必须证明 TokenShare 不是只在一个 toy task 上工作。factorization 和 Lean 分别代表可枚举算术搜索与形式化证明；两者共享协议 lifecycle，但 split、parser、verifier/checker 和 merge 都属于插件。难度分层用于避免所有输入都成功而没有信息量。

## 设计

| 项目 | 固定值 |
|:---|:---|
| domains | `factorization`, `lean_proof` |
| paper difficulty | factorization 使用 easy / medium / hard；Lean 使用 simple / medium lemma-DAG / hard-frontier 三档 |
| tasks | 每个 domain 每个 paper difficulty 目标 10 个，共 60 个 root tasks；若 Lean medium / hard-frontier catalog 未完成，正式 Lean 难度主张 blocked 或降级，不得用当前 shallow v1 补齐三档 |
| repeats | 论文 run 每个 task 3 次；pilot 只跑 1 次且不进入主表 |
| worker count | 固定 10；若 provider preflight 不允许 10，并发改为可用上限且整个实验保持一致 |
| model | 固定一个预注册的 baseline entry id；baseline 只表示控制变量，不表示 strong |
| fault/ablation | none / FULL |

## 程序必须输出

逐 task 输出：completion、accepted validity、parser/checker status、split/child/merge 数、attempt/provider-attempt 数、wall-clock、provider latency sum、prompt/completion/total tokens、cost、failure stage、event/artifact refs。逐难度输出 completion rate、accepted validity、median/P90 wall-clock、median/P90 tokens、cost per completed task 和 failure breakdown。

## 可写入论文的结果

可以写两个领域在不同难度下的完成率与成本；可以写 Lean checker 或 factorization verifier 拒绝了哪些候选；可以写能力边界随难度下降。不得仅写“accepted outputs 100% correct”而不报告未完成任务，也不得把模型求解失败解释为协议状态污染。

# Experiment 2: 真实 AI worker 扩展性

## 为什么需要

TokenShare 的关键价值主张之一是把可拆任务分派给多个 worker。必须用固定任务和真实 API 测量增加 worker 是否缩短端到端时间，以及收益何时被 provider rate limit、任务粒度、merge gate 或调度开销抵消。

## 设计

强制 worker levels 为 `1, 3, 10, 30`。`100, 300` 是扩展点：只有当输入至少产生相同数量的可运行 AI units、provider quota preflight 通过、没有把线程数冒充逻辑 worker 数时才运行。否则报告 `unsupported_worker_level`，不能补造曲线。

每个 worker level 在每个 domain 的每个 paper difficulty 各取固定 5-task batch：factorization 使用 easy / medium / hard，Lean 使用 simple / medium lemma-DAG / hard-frontier，且当前 shallow v1 不得冒充 Lean medium / hard-frontier。所有 batch 使用完全相同的 catalog digest、任务顺序集合、模型、prompt、timeout 和 seed family，重复 5 次；时间比较报告 median 和 IQR。factorization 主 scaling case 必须是同一 root 内的 range children 并行，不得用当前 direct 500 中“多个独立整数同时跑”替代。Lean scaling 同时报告 root throughput、child-proof throughput 和 lemma-DAG critical path。

## 输出与公式

``` math
S(w)=\frac{T(1)}{T(w)},\qquad
E(w)=\frac{S(w)}{w},\qquad
Q(w)=\frac{\text{completed roots}}{\text{wall-clock seconds}}
```

程序输出 `worker_count,difficulty,task_batch_id,wall_clock_ms,critical_path_ms,provider_latency_sum_ms,throughput,speedup,parallel_efficiency,total_tokens,cost,completion_rate,http_429_count,retry_count`。`critical_path_ms` 必须从同一 root / task batch 的 task-attempt 时间戳和 merge gate 依赖关系复算，不能用 provider latency sum 替代。若出现 provider 429/限流，必须同时给出包含限流的 end-to-end 曲线和去除限流 run 的敏感性分析，不能把外部 API 限流声称为协议本身不可扩展。

# Experiment 3: 真实 AI 故障注入与 worker death 恢复

## 为什么需要

只有 pass/fail 不能证明鲁棒性。该实验必须显示故障率增加时检测、恢复、完成、时间和 token 如何变化，并明确哪些错误可恢复、哪些只能检测、哪些会导致失败。

## 故障类型与注入点

| fault type | 注入点 | 受控动作 |
|:---|:---|:---|
| false_positive | real parsed candidate 之后、verification 之前 | 把未成立的 factor/proof claim 写入 mutated submission；保留原始 raw/parsed refs。 |
| false_negative | real parsed candidate 之后、verification 之前 | 把本来找到的结果改为 no-result/缺失 child；记录被抑制 candidate。 |
| no_return | real raw output 保存之后、submission 之前 | 丢弃 submission，使 lease 过期并触发 replacement attempt。 |
| late_submission | real raw output 保存之后 | 延迟到 lease deadline 之后提交，验证 late result 不污染 canonical。 |
| executor_error | real provider response 保存之后、parser bridge 之前 | 生成受控 executor error record，恢复 attempt 再次真实调用 API。 |
| worker_death | 独立 worker process 已保存 raw output、尚未提交时 | 终止该 worker process；协调器保持运行，等待 lease expiry 后交给 replacement worker。 |

Rate-fault 矩阵只覆盖 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。Factorization fault rates 使用 `0%, 1%, 5%, 10%, 25%, 50%, 100%`。Lean 因每个 root 有多个 proof calls，使用 `0%, 10%, 50%, 100%`。故障目标用固定 seed 从 AI units 中选择，实际 target ids 写入 manifest。每个 condition 重复 3 次。

worker death 另按任务进度 25%、50%、75% 三个位置注入，每个 domain 每个位置重复 3 次。这里终止的是 executor worker，不是 coordinator；因此可以在 Phase 9 完整 replay 之前测试 lease/reassignment。若要测试 coordinator crash/restart，必须等待 state replay 可重建后另立实验，不得混写。

## 必须输出

`fault_type,fault_rate,fault_target_count,injection_point,original_output_ref,mutated_output_ref,detection_rate,false_accept_rate,recovery_rate,completion_rate,recovery_latency_ms,retry_count,reassignment_count,wasted_actual_tokens,total_actual_tokens,cost_overhead`。

actual token 只来自 provider usage。注入变换的 synthetic work 另写 `simulated_mutation_count`，不能伪造 token。论文必须至少展示一个可恢复正例和一个不可恢复或代价过高的负例。

指标分母固定如下：

- `detection_rate = detected_fault_count / injected_fault_count`。

- `false_accept_rate = wrongly_canonicalized_fault_count / injected_fault_count`。

- `recovery_rate = recovered_faulted_unit_count / recoverable_fault_target_count`；不可恢复或只应检测的 fault 不进入分母。

- `completion_rate = completed_root_count / attempted_root_count`。

- `cost_overhead = (fault_condition_cost - matched_no_fault_baseline_cost) / matched_no_fault_baseline_cost`。

`false_positive` 和 `late_submission` 默认是 detect-and-isolate；`no_return`、`executor_error` 和 `worker_death` 默认是 recoverable；`false_negative` 必须按插件能否从后续 merge / checker 发现缺失事实记录为 `recoverable` 或 `detect_only`，不能硬写为成功恢复。

# Experiment 4: 真实 AI 协议消融

## 为什么需要

消融用于证明 verification、parser、requeue 和 merge gate 不是装饰。所有消融 run 仍先调用真实 AI API，只在协议边界关闭一个机制；每个 run 使用独立 output root，防止错误 canonical 数据影响其他实验。

| mode | 与 FULL 唯一差异 | 主要观察 |
|:---|:---|:---|
| FULL | 无 | 论文 baseline。 |
| NO_VERIFICATION | 不执行 domain verifier/checker gate | wrong canonical acceptance、final invalidity。 |
| NO_PARSER_POLICY | 允许 raw/free-form 直接进入候选边界 | parse isolation 破坏和错误逃逸。 |
| NO_REQUEUE | rejected/expired unit 不创建 replacement attempt | stuck task rate 和 completion 降低。 |
| NO_MERGE_GATE | required slots 未齐时允许 merge 尝试 | premature merge、root checker/merge failure。 |
| NO_SLOT_INTEGRITY | child output 可绑定到错误 slot | slot mismatch acceptance 和错误 merge 风险。 |

每个 domain 从 easy/medium/hard 各取固定 5 个 tasks，所有模式重复 3 次。报告 completion、accepted validity、wrong canonical acceptance、raw-only acceptance、stuck task、premature merge、slot mismatch、time、token 和 cost。消融实现必须在实验 wrapper/adapter 中，不修改协议 core 的默认 FULL 语义。

# Experiment 5: 三模型 model-provider endpoint comparison（次要）

## 为什么需要

三个具名模型端点可用于观察 TokenShare 在异构真实模型/提供商上的完成率、有效性、token、成本和延迟差异，但不再人为划分 strong/weak，也不实现 mixed routing。它主要测量 model-provider endpoint 对实验结果的影响，不能替代前四个协议实验；OpenAI 与 SiliconFlow 的 provider 差异会同时影响 latency、429、成本和可用性，因此论文必须显式保留该混杂因素。

## 预注册 cohort

正式 cohort 固定为 `tokenshare.paper.model_endpoint_cohort.v1`：

| `cohort_member_id` | provider endpoint | provider model / reasoning profile | Artificial Analysis v4.1 背景分数 | 解释边界 |
|:---|:---|:---|---:|:---|
| `glm_5_2_siliconflow` | SiliconFlow | `zai-org/GLM-5.2`；reasoning controls 在 pilot 前固定 | GLM-5.2 (max): 51 | 未证明 SiliconFlow reasoning mode 与榜单 max 完全一致时，`benchmark_match_status=family_only`。 |
| `qwen3_6_27b_siliconflow` | SiliconFlow | `Qwen/Qwen3.6-27B`；reasoning controls 在 pilot 前固定 | Qwen3.6 27B (Reasoning): 37 | 未证明 endpoint thinking configuration 与榜单一致时，`benchmark_match_status=family_only`。 |
| `gpt_5_6_sol_high_openai` | OpenAI | `gpt-5.6-sol`；`reasoning_effort=high` | GPT-5.6 Sol (high): 56 | provider request、response resolved model 和 reasoning profile 都匹配后才标 `exact`。 |

外部分数只进入 cohort snapshot 和论文模型背景表，不产生 strong/weak 标签，不决定 routing、paper eligibility 或 TokenShare 结论。正式 run 不联网刷新榜单；榜单变更只能生成新 cohort version，不能改写旧 suite。2026-07-16 本地摘要来源为 Artificial Analysis [v4.1 methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking)、[Data API](https://artificialanalysis.ai/data-api)、[GLM-5.2](https://artificialanalysis.ai/models/glm-5-2)、[Qwen3.6 27B](https://artificialanalysis.ai/models/qwen3-6-27b)、[GPT-5.6 Sol high](https://artificialanalysis.ai/models/gpt-5-6-sol-high)，以及 provider identity 来源 SiliconFlow [模型中心](https://www.siliconflow.cn/models) 和 OpenAI [Models](https://developers.openai.com/api/docs/models)。这些在线资料影响的范围仅为 cohort identity、外部分数 provenance、provider/model/reasoning preflight 和论文限定语。

## 设计与输出

使用 Experiment 1 catalog slice，每个 domain / paper difficulty 固定 5 个 tasks；三个 cohort member 各自作为 `model_policy="fixed_entry"` 的独立 condition，使用相同 task order、prompt/parser/plugin version、worker count、timeout、request-limit policy 和 repeat/seed family，每个 condition 重复 3 次。AI unit 在整个首次/恢复 attempt 链中保持同一 cohort member，不按 difficulty 换模型，也不在失败后升级到另一个模型。

输出 completion、accepted validity、tokens、cost、latency、provider errors、recovery attempts 和 `model_execution_records`。每条 execution record 至少包含 `condition_id,task_id,unit_id,attempt_id,model_policy,model_cohort_id,cohort_member_id,selected_entry_id,provider_family,provider_model_id,reasoning_profile_id,request_ref,provider_attempt_ref,usage_ref,latency_ms,total_tokens,cost_estimate`。论文以 completion / accepted validity 作为主要跨端点结果；latency、cost 和 rate-limit 结果必须按 provider 分层或标注 provider confounding。

正式 Experiment 5 preflight 必须验证三个 cohort member 的 provider config、key env、model id、reasoning profile、真实 smoke evidence、catalog compatibility 和预算。缺少任一 member 时，整个正式 Experiment 5 标记 `blocked`，`blocked_reason="incomplete_model_cohort"`，`paper_eligible=false`，`provider_attempt_count=0`；可用 member 的单模型试跑只能标 `pilot_only=true`，不能生成三模型论文主表。该 blocked 不影响 Experiment 1-4 的主张。

# 统一输出契约

## Python / CLI 返回值契约

新 paper runner 的 Python API 和 CLI 必须返回同一套结构化结果；CLI 可以把完整对象写入 `suite_manifest.json`，stdout 只打印摘要路径和 status。

最小返回对象如下：

| 对象 | 最小字段 |
|:---|:---|
| `PaperSuiteResult` | `schema_version,suite_id,status,output_root,started_at,ended_at,experiment_ids,condition_count,run_count,task_count,provider_attempt_count,total_tokens,total_cost_estimate,paper_eligible,eligibility_report_ref,budget_ref,metrics_refs,audit_refs,error_summary` |
| `PaperExperimentResult` | `experiment_id,status,condition_ids,run_count,task_count,completion_rate,accepted_validity_rate,total_tokens,total_cost_estimate,summary_ref` |
| `PaperConditionResult` | `condition_id,status,repeat_count,task_count,completed_root_count,failed_root_count,blocked_root_count,provider_attempt_count,metrics_ref` |
| `PaperRunResult` | `condition_id,repeat_id,run_id,status,run_manifest_ref,per_task_results_ref,per_attempt_results_ref,fault_injections_ref,event_log_ref,artifact_root,paper_eligible,ineligibility_reasons` |
| `PaperTaskResult` | `condition_id,repeat_id,task_id,domain,difficulty,root_status,accepted_validity,failure_stage,failure_kind,attempt_count,provider_attempt_count,wall_clock_ms,total_tokens,cost_estimate,event_refs,artifact_refs,paper_eligible` |
| `PaperAttemptResult` | `condition_id,repeat_id,task_id,unit_id,attempt_id,worker_id,attempt_status,provider,model,entry_id,request_ref,raw_output_ref,parsed_output_ref,parse_failure_ref,provenance_ref,usage_ref,latency_ms,total_tokens,cost_estimate,error_kind,fault_injection_ref,paper_eligible` |
| `PaperBudgetResult` | `budget_digest,planned_experiments,planned_conditions,planned_root_runs,planned_ai_units,max_provider_attempts,token_upper_bound,cost_upper_bound,wall_clock_estimate,quota_preflight,rate_limit_preflight,disk_estimate,status` |

## 状态与失败枚举

`status` 字段必须使用稳定枚举，不能临时写自然语言：

- suite / experiment / condition / run status：`planned`、`running`、`completed`、`completed_with_failures`、`blocked`、`budget_exhausted`、`failed`。

- root task status：`completed`、`failed`、`blocked`、`timeout`、`budget_exhausted`、`ineligible`。

- attempt status：`succeeded`、`provider_error`、`parse_failed`、`verification_rejected`、`checker_rejected`、`lease_expired`、`late_rejected`、`worker_died`、`cancelled_by_budget`。

- failure stage：`catalog`、`split`、`request`、`provider`、`parse`、`verification`、`checker`、`canonical`、`merge`、`settlement`、`metrics`、`audit`。

- failure kind：`provider_error`、`rate_limited`、`parse_failure`、`verifier_rejected`、`checker_rejected`、`lease_expired`、`late_submission`、`no_requeue`、`premature_merge`、`slot_mismatch`、`budget_limit`、`unsupported_worker_level`、`missing_model_entry`、`secret_leak`、`internal_error`。

CLI exit code 固定为：0 表示 runner 正常结束或按预算上限结构化停止；1 表示参数 / config / catalog / schema 错误；2 表示预算 digest 不匹配或缺少批准；3 表示 secret scan failure；4 表示 runner 内部错误。

## 目录结构

    outputs/experiments/paper_v1/<suite_id>/
      suite_manifest.json
      run_budget.json
      input_catalog_manifest.json
      conditions.jsonl
      runs/<condition_id>/<repeat_id>/
        run_manifest.json
        per_task_results.jsonl
        per_attempt_results.jsonl
        fault_injections.jsonl
        events/event_log.jsonl
        artifacts/...
      metrics/per_condition_summary.csv
      metrics/paper_table_feasibility.csv
      metrics/paper_table_ablation.csv
      metrics/paper_plot_scalability.csv
      metrics/paper_plot_robustness.csv
      metrics/failure_examples.json
      audit/paper_eligibility_report.json
      audit/secret_scan_report.json

## Condition manifest 最小字段

    {
      "schema_version": "tokenshare.paper_condition.v1",
      "experiment_id": "exp2_real_ai_scalability",
      "condition_id": "...",
      "domain": "factorization|lean_proof",
      "difficulty": "easy|medium|hard|all",
      "paper_difficulty": "easy|medium|hard|simple|medium_lemma_dag|hard_frontier|all",
      "worker_count": 10,
      "fault_type": "none",
      "fault_rate": 0.0,
      "ablation_mode": "FULL",
      "model_policy": "fixed_entry",
      "model_cohort_id": null,
      "cohort_member_id": null,
      "model_entry_id": "glm_5_2_siliconflow",
      "repeat_id": 0,
      "seed": 1,
      "catalog_digest": "sha256:...",
      "real_transport_required": true,
      "paper_eligible_required": true
    }

## Per-task result 最小字段

每行必须包含 condition/run/task id、domain/difficulty、root status、accepted validity、failure stage/kind、worker/attempt/provider/model、parser/verifier/checker/merge 状态、wall-clock/provider latency、tokens/cost、fault/ablation refs、event/artifact refs 和 `paper_eligible`。任何 summary 数字都必须能回到这些逐 task/attempt 记录和 event/artifact evidence。

## Per-attempt result 最小字段

每行必须包含 `condition_id,repeat_id,run_id,task_id,unit_id,attempt_id,worker_id,provider_attempt_index,provider,model,entry_id,request_ref,raw_output_ref,parsed_output_ref,parse_failure_ref,provenance_ref,usage_ref,started_at,ended_at,latency_ms,prompt_tokens,completion_tokens,total_tokens,cost_estimate,attempt_status,error_kind,fault_injection_ref,paper_eligible`。如果 provider failover 发生，必须为每次 provider attempt 写独立行或写入可展开的 `provider_attempts[]`，不能只保留最后一次。

## Catalog manifest 最小字段

`input_catalog_manifest.json` 必须包含 `schema_version,catalog_id,catalog_version,catalog_digest,generator_version,case_count,domain_counts,difficulty_counts,oracle_validation_status,lean_preflight_status,created_at,source_files`。Lean v2 / lemma-DAG catalog 还必须包含 `paper_difficulty_counts`，用于区分 current shallow-v1 legacy difficulty 和正式 Lean paper difficulty。任一 case 的 oracle 或 Lean preflight 失败时，catalog freeze 失败；不能在正式 run 中静默跳过该 case。

# 论文表格、图和可写结论

| 论文产物 | 数据文件 | 可以回答 |
|:---|:---|:---|
| Feasibility table | `paper_table_feasibility.csv` | 两个领域、三档 paper difficulty 的完成率、accepted validity、时间、token、成本；Lean 当前 shallow v1 只能进入 simple。 |
| Difficulty figure | feasibility CSV 派生 | 难度上升时成功率和成本如何变化，能力边界在哪里。 |
| Scalability figure | `paper_plot_scalability.csv` | worker 增加后的 wall-clock、throughput、speedup、efficiency、token 和限流。 |
| Robustness figure | `paper_plot_robustness.csv` | fault rate 对检测、恢复、完成、时间/token overhead 的影响。 |
| Ablation table | `paper_table_ablation.csv` | 关闭一个机制后哪种错误逃逸或任务卡住。 |
| Failure examples | `failure_examples.json` | 至少一个可恢复和一个不可恢复案例的完整 evidence chain。 |

只有数据支持时才可写“worker 增加缩短时间”“混合模型降低成本”或“某类错误可恢复”。负面结果可以直接写：例如速度在 10 workers 后饱和、false negative 在某种同批次条件下无法恢复、NO_VERIFICATION 导致错误 canonical。不得预写必然正向结论。

论文结构建议固定为：`Feasibility Across Two Domains`、`Scalability with Real AI Workers`、`Robustness and Failure Boundaries`。Ablation 放在第三部分，三模型 model-provider endpoint comparison 放 appendix 或次要 subsection。

# API、时间、token、成本和人工投入

正式 P0 的最小执行规模固定如下；agent 不得自行扩大，扩大前必须重新生成预算并由用户批准：

| 实验 | 最小正式规模 | root-run 数量 |
|---|---:|---:|
| Experiment 1 | 2 domains × 3 difficulties × 10 tasks × 3 repeats | 180 |
| Experiment 2 | 2 domains × 3 difficulties × 每档固定 5-task batch × 4 worker levels × 5 repeats | 600 |
| Experiment 3 rate faults | Factorization: 5 tasks × 5 fault types × 7 rates × 3 repeats；Lean: 3 tasks × 5 fault types × 4 rates × 3 repeats | 705 |
| Experiment 3 worker death | 2 domains × 3 tasks × 3 kill positions × 3 repeats | 54 |
| Experiment 4 | 2 domains × 3 difficulties × 5 tasks × 6 modes × 3 repeats | 540 |
| Experiment 5（三模型 cohort 完整时纳入 P0-full） | 2 domains × 3 difficulties × 5 tasks × 3 fixed model-provider endpoints × 3 repeats | 270 |

P0-core（Experiment 1-4）合计 2079 个 root-runs；P0-full（Experiment 1-5 且三模型 cohort / provider preflight 通过）合计 2349 个 root-runs。100 / 300 worker extension 若 preflight 通过，最多额外增加 300 个 root-runs，并必须在 suite manifest 中标记为 extension，不并入 P0-core 或 P0-full 主统计。root-run 数量不等于 provider calls。Factorization root 可能拆成多个 range AI units，Lean root 可能拆成多个 proof AI units；真实 provider-attempt 上界必须由 split preflight 精确展开。若预算上限无法覆盖计划，runner 写 `budget_exhausted` 并停止启动新 task；不得静默减少样本、删 mode 或删 difficulty。任何缩小矩阵都必须作为新的用户批准 suite version 记录。

## 运行前预算门禁

runner 必须先执行 `--plan-only`，根据 catalog、child counts、conditions、repeats 和 max provider attempts 生成 `run_budget.json`：

``` math
N_{calls}^{max}=\sum_{conditions}\sum_{tasks}
  N_{AI\ units}(task)\times repeats\times maxProviderAttempts
```

预算文件至少包含 planned root tasks、AI units、provider attempts 上界、token 上界、cost estimate 上界、预计 wall-clock、provider/model、并发、quota/rate-limit preflight 和磁盘空间估计。正式运行需要显式 `--approve-budget-digest`，避免配置变化后误花费。

CLI 必须支持 `--max-total-provider-attempts`、`--max-total-tokens`、`--max-cost-estimate` 和 `--stop-after-current-task`。超过任一上限时写结构化 `budget_exhausted`，不启动新 task；已完成 evidence 保留。

## 现有真实运行只用于资源校准

2026-07-02 的本地记录显示：direct factorization 500 tasks 使用 525777 total tokens、cost estimate 1.7462418；Lean 50 root tasks 使用 101 provider attempts、cost estimate 0.167799265。它们不是本文新实验结果，只用于初始预算量级。新 factorization 协议实验每个 root 会有多个 range AI units，必须由 `--plan-only` 按实际 split 重新估算，不能用 direct benchmark 的每题成本直接代替。

## 人工与机器投入

| 阶段 | 预计投入 | 完成物 |
|:---|:---|:---|
| 代码补齐 | 1–2 人日 | paper runner、catalog、real-AI gate、fault/process worker、metrics/report、tests。 |
| pilot | 0.5 人日 + API | 每个 condition 1 repeat，发现 schema/prompt/quota 问题，不进入主表。 |
| 正式 P0 run | 0.5–1 人日 + API | Experiment 1–3 三次重复和完整 evidence。 |
| ablation/model | 0.5 人日 + API | Experiment 4；预算允许时 Experiment 5。 |
| 论文与审计 | 1 人日 | 图表、failure analysis、secret scan、replay/evidence check、文字改写。 |

# 代码改造计划（agent 可直接实施）

## 文件结构

| 文件 | 职责 |
|:---|:---|
| `benchmarks/paper/factorization_catalog.v1.jsonl` | 冻结的 factorization 30-task catalog。 |
| `benchmarks/paper/lean_catalog.v1.jsonl` | 冻结的 Lean simple/shallow 30-task catalog；历史 easy/medium/hard 只保留为 shallow-v1 标签。 |
| `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 或 `benchmarks/paper/lean_catalog.v2.jsonl` | 后续必须新增的 medium recursive lemma-DAG / hard-frontier catalog，用于正式 Lean 复杂度主张。 |
| `src/tokenshare/experiments/paper_models.py` | `PaperExperimentCondition`、budget、fault record、paper eligibility schema 和 digest。 |
| `src/tokenshare/experiments/paper_catalog.py` | 加载、校验和 digest paper catalogs；本地 oracle/Lean preflight；后续必须区分 Lean `paper_difficulty` 与 shallow-v1 legacy difficulty。 |
| `src/tokenshare/experiments/factorization_paper_adapter.py` | 从 root split 到 range children，所有 range candidate 经真实 `AIAPIExecutor`、插件 parser/verifier、canonical/merge。 |
| `src/tokenshare/experiments/lean_paper_adapter.py` | 执行分难度 Lean catalog，真实 API child/direct proof、checker、merge/root recheck，并支持 model/worker config。 |
| `src/tokenshare/experiments/paper_faults.py` | 在真实 output 后执行 deterministic fault selection/mutation，写 `FaultInjectionRecord`。 |
| `src/tokenshare/experiments/paper_workers.py` | 独立 worker process、kill point、lease expiry、replacement attempt 和进程 evidence。 |
| `src/tokenshare/experiments/paper_model_policy.py` | 加载/校验三模型 cohort snapshot 与 local entry map，展开 fixed-entry conditions，写 model execution records；不在 executor 内解释模型强弱。 |
| `src/tokenshare/experiments/paper_budget.py` | plan-only provider/token/cost/time/space 预算及硬上限。 |
| `src/tokenshare/experiments/paper_runner.py` | 展开 Experiment 1–5 conditions、repeat/seed、resume、budget gate、paper eligibility。 |
| `src/tokenshare/experiments/paper_metrics.py` | 从 events/artifacts/attempts/fault records 复算逐条件统计、quantile、speedup、recovery、ablation。 |
| `src/tokenshare/experiments/paper_report.py` | 写统一目录、逐 task/attempt JSONL、论文 CSV、audit reports。 |
| `src/tokenshare/experiments/run_paper_experiments.py` | 唯一论文实验 CLI；默认拒绝 scripted transport。 |
| `tests/experiments/test_paper_*.py` | schema/catalog/gate/fault/worker/budget/metrics/report/CLI 回归。 |

## 现有文件的最小修改

- `src/tokenshare/experiments/__init__.py`：导出 paper suite public API。

- `src/tokenshare/experiments/lean_ai_benchmark.py`：抽出可复用单 case 执行函数；不得再限制新 catalog 只能按前 N 个固定顺序；暴露 request limits、entry ids、worker count。

- `src/tokenshare/experiments/factorization_500_ai.py`：仅复用 deterministic semiprime generator/oracle；不要把 direct answer runner 当作 protocol adapter。

- `src/tokenshare/experiments/metrics.py`：旧 hard-coded 0/1 指标保留给 regression only；paper runner 不得调用这些字段作为论文统计。

- `src/tokenshare/experiments/simulation.py`：旧 v1 决策保留回归；paper faults 使用新 v2 record，不用只写“selected fault”的报告层模拟。

- `src/tokenshare/executors/ai_api.py`、`ai_api_config.py`、`ai_api_transport.py`：为 Task 10 增加独立 OpenAI Chat Completions provider family 和动态 provider provenance，同时保持 SiliconFlow v1 兼容；不把 cohort、外部分数、fault、worker 或 experiment policy 放入 executor。

## 实施顺序与测试

1.  先写 paper model/catalog/budget 的失败测试，验证 digest、难度字段、30+30 catalog、plan-only 和 budget approval。

2.  实现真实 API paper eligibility gate；测试 scripted/fake/deterministic run 必须被拒绝为论文结果。

3.  实现 factorization paper adapter；用注入 fake transport 做测试，但正式 CLI 必须 real transport。验证每个 range AI unit 都有 provider attempt、raw/parsed/verifier evidence。

4.  实现 Lean paper adapter 和难度 catalog；本地 Lean checker 测试不依赖网络，正式候选生成走真实 API。

5.  实现 post-AI fault mutation 和 worker process death；测试 original/mutated refs、lease expiry、replacement attempt 和 no canonical pollution。

6.  实现 paper metrics/report；用手工构造 event/artifact fixture 验证统计，不硬写 pass。

7.  运行 targeted tests，再运行 `tests/experiments`、executor/plugin impact suite 和完整 `init.ps1`。

8.  执行 plan-only、pilot、正式 P0、ablation；Experiment 5 只有在三模型 cohort、OpenAI/SiliconFlow provider preflight 和预算都通过时进入 P0-full，不得阻塞 P0-core。

建议验证命令：

    $env:PYTHONPATH='src'
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_models.py -q
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_catalog.py -q
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_real_ai_gate.py -q
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_faults.py -q
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_workers.py -q
    conda run -n tokenshare python -m pytest tests/experiments/test_paper_metrics.py -q
    conda run -n tokenshare python -m pytest tests/experiments -q
    .\init.ps1

# 正式 CLI 规格

唯一论文入口：

    $env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --output-root outputs/experiments/paper_v1 `
  --experiments exp1,exp2,exp3,exp4,exp5 `
  --real-transport `
  --provider-config siliconflow=local/ai_api_smoke.local.json `
  --provider-config openai=local/openai_api_smoke.local.json `
  --baseline-entry-id <configured-baseline-entry-id> `
  --model-cohort-file benchmarks/paper/model_comparison_cohort.v1.json `
  --model-entry-map local/model_comparison_entries.local.json `
  --worker-levels 1,3,10,30 `
  --optional-worker-levels 100,300 `
  --repeats 3 `
  --seed-family 1,2,3 `
  --plan-only

plan-only 通过人工检查后：

conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --output-root outputs/experiments/paper_v1 `
  --experiments exp1,exp2,exp3,exp4,exp5 `
  --real-transport `
  --provider-config siliconflow=local/ai_api_smoke.local.json `
  --provider-config openai=local/openai_api_smoke.local.json `
  --baseline-entry-id <configured-baseline-entry-id> `
  --model-cohort-file benchmarks/paper/model_comparison_cohort.v1.json `
  --model-entry-map local/model_comparison_entries.local.json `
  --worker-levels 1,3,10,30 `
  --optional-worker-levels 100,300 `
  --repeats 3 `
  --seed-family 1,2,3 `
  --approve-budget-digest <digest> `
      --max-total-provider-attempts <approved-limit> `
      --max-total-tokens <approved-limit> `
      --max-cost-estimate <approved-limit>

CLI 若未给 `--real-transport`、Experiment 1-4 baseline entry 不可用、catalog digest 不匹配、预算 digest 不匹配或输出目录已有不同 suite manifest，应拒绝启动，不得自动退回 scripted transport。运行 Experiment 5 时还必须加载 frozen cohort、local entry map、SiliconFlow/OpenAI provider configs，并通过三个 member 的 key/model/reasoning/smoke preflight；任一 member 缺失时 runner 写 `blocked_reason="incomplete_model_cohort"`，而不是让 Experiment 1-4 失败，也不得自动替换模型。

# 四天执行安排

| 日期 | 工作和完成门槛 |
|:---|:---|
| Day 1 | 完成 catalog、paper schemas、real-AI gate、budget plan 和 adapter 最小路径；targeted tests 通过；跑每个 domain 1 个真实 API smoke。 |
| Day 2 | 完成 Experiment 1 和 Experiment 2；跑完整 pilot；当天生成 feasibility/scalability CSV 并检查是否有区分度和 429。 |
| Day 3 | 完成 post-AI fault、worker process death 和 Experiment 3；跑 factorization 完整故障率和 Lean 精简故障率。 |
| Day 4 | 完成 Experiment 4、论文表图和 failure analysis；三模型 cohort 与双 provider preflight 通过时跑 Experiment 5；做 secret scan、evidence check、Markdown/论文文字更新和完整 init。 |

如果时间或预算不足，runner 使用 `budget_exhausted` 结构化停止，不能静默删除 Experiment 1-4、difficulty、fault type 或 ablation mode。Experiment 5 只在三模型 cohort 任一 member、provider transport、reasoning profile 或真实 smoke 不满足时允许 `incomplete_model_cohort` blocked；其他删减必须经用户重新批准并写成新的 suite version。Ablation 默认运行全部 6 个模式，FULL、NO_VERIFICATION、NO_REQUEUE 只是后续人工分析时的最低必读对照，不是默认裁剪口径。

# 验收标准

新实验计划完成的必要条件：

1.  本 Markdown 是唯一权威设计；旧 `.tex/.pdf` 和 Phase 8 实验设计文稿已删除，导航和 README 不再把旧 Experiment 1-4 当论文主口径。

2.  新 paper runner 没有 scripted fallback；所有论文 run 的 `paper_eligible=true` 可由真实 provider attempts 和 raw artifacts 证明。

3.  factorization 主实验走协议 range children、parser/verifier/canonical/merge，不用 direct 500 准确率替代。

4.  Lean 主实验必须区分 simple shallow、medium recursive lemma-DAG 和 hard/frontier stress 层级：当前 `lean_catalog.v1.jsonl` 全部只能算 simple，正式递归证明拆分主张至少需要 medium lemma-DAG；所有可采信 proof case 都必须有真实 AI proof candidates、真实 checker、merge/root recheck，不能用 50 个近似同难度 shallow 题替代。

5.  worker scaling 测同一 root/task batch 的协议 worker，并记录 provider 限流混杂。

6.  故障注入引用原始真实输出，实际 token 与 synthetic mutation 分开；worker death 是真实独立 worker process 终止。

7.  metrics 从 events/artifacts/attempts/fault records 复算；逐 task/attempt 数据能支撑每个论文汇总值。

8.  输出包含预算、paper eligibility、secret scan、图表 CSV、正负 failure examples 和稳定 schema version。

9.  targeted tests、影响范围 tests、`compileall`、完整 `init.ps1` 通过，并把证据同步到 code map、feature list、progress 和 handoff。
