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

1.  Experiment 1 正式输入固定为 500 个 Factorization roots（easy/medium/hard=`167/167/166`）和 135 个 Lean roots（3 个 `paper_difficulty` × 3 个 `topic_family` × 每格恰好 15 个），合计 635 个唯一 roots；每题 3 次 repeat，共 1,905 个 root-runs。500 个 Factorization roots 和每格 15 道 Lean 都是冻结的正式样本量，不得临时抽样冒充正式 Experiment 1。

2.  Experiment 2 P0-core scaling 的 Factorization 每个 difficulty condition 使用该档全部 roots（`167/167/166`），Lean 每档仍使用固定 5-task batch；4 个强制 worker levels（1, 3, 10, 30）和 5 次 repeat 合计 10,300 个 root-runs。Lean 每个 5-task batch 必须覆盖全部三个 `topic_family`，使用预注册的 2/2/1 分层分配：simple=`pure_logic:2,function_set:2,induction:1`，medium=`pure_logic:1,function_set:2,induction:2`，hard=`pure_logic:2,function_set:1,induction:2`；condition manifest 保存具体 task ids 和 slice digest，且同一对比组完全复用。100 和 300 worker levels 是 preflight-gated extension：quota、AI unit 数量和机器资源都满足时运行；不满足时输出 `unsupported_worker_level`，不补造曲线。

3.  Experiment 3 的 rate-fault 矩阵只包含 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。Factorization rate-fault condition 使用全部 500 roots；`worker_death` 永远单独进入 worker-death 矩阵，Factorization worker-death condition 按 difficulty 使用 `167/167/166`，每组 death-count/kill-position/repeat 的三档并集为全部 500 roots。worker-death P0 固定 `worker_count=10`、`dead_worker_count ∈ {1,3}` 和 25% / 50% / 75% 三个 kill positions。rate-fault 共 52,680 root-runs，worker-death 共 9,054，Experiment 3 合计 61,734。

4.  Experiment 4 P0-core ablation 的 Factorization 每个 difficulty 使用全部 `167/167/166` roots，Lean 每档使用固定 5-task 2/2/1 slice；6 个模式和 3 次 repeat 共 9,270 个 root-runs。旧的 3-task/5-task Factorization 子样本不再作为正式 P0 口径。

5.  Experiment 5 改为预注册的三模型 model-provider endpoint comparison：SiliconFlow `zai-org/GLM-5.2`、SiliconFlow `Qwen/Qwen3.6-27B`、OpenAI `gpt-5.6-sol` with `reasoning_effort=high`。三个真实 entry、provider transport、reasoning profile 和 smoke evidence 都可用时纳入 P0-full；缺少任一 cohort member 时输出结构化 `blocked`，不算协议失败，也不影响 Experiment 1-4 的主张。

6.  除 Experiment 5 三端点对比外，Experiment 1–4 的所有 pilot、正式 condition、故障恢复 attempt 和消融 mode 都固定使用 SiliconFlow `zai-org/GLM-5.2`，安全配置为 `benchmarks/paper/exp1_baseline_provider_config.v1.json`，entry id 为 `glm_5_2_exp1_baseline`，`temperature=0.0`、`enable_thinking=false`。缺少该 entry、API key、真实 smoke 或 identity evidence 时对应实验结构化 `blocked`；不得切换到 Qwen、OpenAI 或其他模型继续生成论文结果。

7.  P0-core 指 Experiment 1-4；P0-full 指 Experiment 1-5 且模型策略 preflight 通过。所有 summary、预算和论文表格必须标明自己属于 P0-core、P0-full 还是包含 100/300 worker extension 的扩展运行。

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
| Task 14 readiness 草稿 | 当前工作树已有 `benchmarks/paper/lean_task14_3x3_readiness.v1.json` 和 `tests/experiments/test_lean_task14_readiness.py`，能描述九格 readiness。 | 草稿仍把 `target_case_count` 固定为 10，现有测试也断言 10；它不满足本版每格 15 道的新冻结契约。Task 14 必须先把测试改成 15 的 RED，再扩 catalog、重生成 manifest 并通过 checker preflight；当前全量测试通过不能证明新样本契约已实现。 |
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

Experiment 1–4 的模型控制变量不是运行时任选值：必须解析到 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`，首次执行、provider retry、replacement 和 resume 都保持同一 identity。Experiment 5 才加载三模型 cohort；它的 GLM cohort entry `glm_5_2_siliconflow` 与 Experiment 1–4 baseline entry 是两个不同命名空间，不得混用 digest 或 approval。

# 输入 catalog 与难度定义

## Factorization catalog v2

冻结 `benchmarks/paper/factorization_catalog.v2.jsonl` 作为论文 CLI 默认 Factorization catalog，共 500 个 root tasks，easy/medium/hard=`167/167/166`，目标整数覆盖 `[1_000_000,100_000_000_000)` 且每档覆盖 10^6 至 10^10 数量级。`factorization_catalog.v1.jsonl` 的 30 题只保留为历史回归输入，不进入新的正式矩阵。

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

2026-07-15 起，正式 Lean catalog 不只区分三档难度，还必须覆盖三类经过设计的题型。正式 catalog 使用 3 × 3 矩阵：`paper_difficulty` 为 `simple`、`medium_lemma_dag`、`hard_frontier`；`topic_family` 为 `pure_logic`、`function_set`、`induction`。

| `topic_family` | simple 目标 | medium_lemma_dag 目标 | hard_frontier 目标 |
|:---|:---|:---|:---|
| `pure_logic` | 当前 shallow `P ∧ Q` / `P ↔ Q` 可作为过渡样本，但正式 simple pool 应记录题型 metadata。 | 多层命题逻辑 lemma-DAG，例如 root 依赖 intermediate lemma，再依赖多个 leaf/sublemma proof units。 | 更深的量词、等价、rewrite 或 theorem reuse 组合；无 oracle 时只能 blocked / stress。 |
| `function_set` | 函数、集合、子集、像/原像或简单单调性相关 theorem，证明结构浅但不是纯 `P ∧ Q`。 | 函数与集合 theorem 的 2-3 层 lemma-DAG，例如先证明函数分段性质、集合刻画，再证明 root subset。 | 混合实数函数、集合构造、分类讨论、rewrite 和外部库 theorem reuse；必须先做 oracle feasibility review。 |
| `induction` | Nat/List 上的简单归纳或递归定义 theorem，root proof 可以由一个短 induction skeleton 完成。 | 一个 root theorem 依赖多个归纳/辅助 lemma，形成 recursive lemma-DAG，AI 只证明分派 proof unit。 | 嵌套归纳、互相依赖 rewrite、theorem reuse 或较长 proof-file assembly；无固定 oracle proof 时不能标 checker success。 |

用户最新决策是：当前正式实验目标要覆盖上述 Lean 3 × 3 题型矩阵，而不是把它只当远期 catalog pool。每个 `(paper_difficulty, topic_family)` 单元固定为 **恰好 15 道** checker-backed、可审计 case，完整 Lean 正式 catalog 因而固定为 135 道。扩大后的 suite version、构造/抽样规则、预算审批和输出表格必须显式记录，不得静默沿用旧 P0 口径，也不得用当前 shallow v1 Lean case 补齐 medium / hard。

该决策目前是文档层要求，不表示 runner、paper adapter 或 Lean plugin 已具备执行能力。进入实现前必须先完成题库与拆分机制复核：每个题型/难度先做 1-2 个 checker-backed golden case；明确 `topic_family` schema、deterministic catalog rule / Lean plugin rule / fixed oracle package 的责任边界；确认 recursive lemma-DAG split、proof-file assembly、dependency-aware merge/root recheck、preflight 时间和真实 AI provider 预算。只有这些复核通过后，才能按 TDD 扩成正式批量题库并修改 runner / adapter；不能先写实验层代码假装这些题已经可拆、可合并或可 paper-eligible。

扩展 schema 必须显式记录 `topic_family`、`topic_family_version`、`construction_rule_id`、`matrix_cell_id`，并继续要求 `environment_digest`、`preflight_status`、oracle package hash 和 checker evidence。catalog manifest 必须证明九个 cell 的 `case_count` 均为 15；多一个、少一个、重复 case 或 preflight 不通过都使正式 catalog freeze 失败。当前已存在的 `lean_catalog.v1.jsonl` 仍只算 simple/shallow 过渡输入；当前 `lean_lemma_graph_catalog.v1.jsonl` 只有最小 medium golden fixture，不满足上述完整矩阵。

后续 reviewer 应先使用 `Doc/TechnicalDocument/2026-07-15-feat-011-lean-tiered-topic-catalog-review-prompt.md` 复核矩阵可行性、Lean 规则成本、oracle package 组织方式、正式实验扩大后的预算影响和 runner / adapter 实现顺序，再进入实现。

# Experiment 1: 真实 AI 跨领域可行性与难度

## 为什么需要

论文首先必须证明 TokenShare 不是只在一个 toy task 上工作。factorization 和 Lean 分别代表可枚举算术搜索与形式化证明；两者共享协议 lifecycle，但 split、parser、verifier/checker 和 merge 都属于插件。难度分层用于避免所有输入都成功而没有信息量。

## 设计

| 项目 | 固定值 |
|:---|:---|
| domains | `factorization`, `lean_proof` |
| paper difficulty | factorization 使用 easy / medium / hard；Lean 使用 simple / medium lemma-DAG / hard-frontier 三档 |
| tasks | Factorization easy/medium/hard=`167/167/166`，共 500 个；Lean 每个 `(paper_difficulty,topic_family)` 15 个，共 135 个；合计 635 个唯一 root tasks。若任一 Lean cell 未达到 15 个 checker-backed cases，正式 Experiment 1 不得启动，不得用其他 cell 或 shallow v1 补齐 |
| repeats | 论文 run 每个 task 3 次；pilot 只跑 1 次且不进入主表 |
| worker count | 固定 10；若 provider preflight 不允许 10，并发改为可用上限且整个实验保持一致 |
| model | 固定 SiliconFlow `zai-org/GLM-5.2`，entry id `glm_5_2_exp1_baseline`，`temperature=0.0`、`enable_thinking=false`；不得自动替换模型 |
| fault/ablation | none / FULL |

## 程序必须输出

逐 task 输出：completion、accepted validity、parser/checker status、split/child/merge 数、attempt/provider-attempt 数、wall-clock、provider latency sum、prompt/completion/total tokens、cost、failure stage、event/artifact refs。逐 `(domain,paper_difficulty,topic_family)` 输出 case count、completion rate、accepted validity、median/P90 wall-clock、median/P90 tokens、cost per completed task 和 failure breakdown；Factorization 的 `topic_family=not_applicable`。另外输出 `highest_observed_valid_completion_difficulty`：对每个 domain/topic family，取至少出现一个 `completion=true && accepted_validity=true` 的最高已测试难度；它只表示观测上界，不替代完整成功率和置信区间。

## 可写入论文的结果

可以写两个领域在不同难度下的完成率与成本；可以写 Lean checker 或 factorization verifier 拒绝了哪些候选；可以写能力边界随难度下降。不得仅写“accepted outputs 100% correct”而不报告未完成任务，也不得把模型求解失败解释为协议状态污染。

# Experiment 2: 真实 AI worker 扩展性

## 为什么需要

TokenShare 的关键价值主张之一是把可拆任务分派给多个 worker。必须用固定任务和真实 API 测量增加 worker 是否缩短端到端时间，以及收益何时被 provider rate limit、任务粒度、merge gate 或调度开销抵消。

## 设计

强制 worker levels 为 `1, 3, 10, 30`。`100, 300` 是扩展点：只有当输入至少产生相同数量的可运行 AI units、provider quota preflight 通过、没有把线程数冒充逻辑 worker 数时才运行。否则报告 `unsupported_worker_level`，不能补造曲线。

每个 worker level 的 Factorization easy / medium / hard condition 分别使用冻结的全部 `167/167/166` roots；Lean simple / medium lemma-DAG / hard-frontier 每档仍使用预注册 5-task 2/2/1 slice。所有 comparison group 使用完全相同的 catalog digest、任务顺序集合、SiliconFlow `zai-org/GLM-5.2` baseline、prompt、timeout 和 seed family，重复 5 次；时间比较报告 median 和 IQR。每个 Factorization root 都必须测试同一 root 内的 range children 并行，不得把多个独立整数当成一个 root 的并行。Lean scaling 同时报告 root throughput、child-proof throughput 和 lemma-DAG critical path。

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

Rate-fault 矩阵只覆盖 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。Factorization fault rates 使用 `0%, 1%, 5%, 10%, 25%, 50%, 100%`。Lean 因每个 root 有多个 proof calls，使用 `0%, 10%, 50%, 100%`，其 3-task slice 固定为 `pure_logic/function_set/induction` 各 1 道。故障目标用固定 seed 从 AI units 中选择，实际 target ids 写入 manifest。每个 condition 重复 3 次。所有原始和恢复 AI attempts 固定使用 `glm_5_2_exp1_baseline`，故障不得触发模型升级或 provider 切换。

worker death 固定 `worker_count=10`，分别终止 1 个和 3 个 executor workers，并按任务进度 25%、50%、75% 三个位置注入；每个 domain、每个死亡数量、每个位置重复 3 次。这里终止的是 executor worker，不是 coordinator；因此可以在 Phase 9 完整 replay 之前测试 lease/reassignment。若要测试 coordinator crash/restart，必须等待 state replay 可重建后另立实验，不得混写。

## 必须输出

Rate-fault 与通用恢复输出至少包含：`fault_type,fault_rate,fault_target_count,injection_point,original_output_ref,mutated_output_ref,matched_baseline_condition_id,detection_rate,false_accept_rate,recovery_rate,completion_rate,recovery_latency_ms,retry_count,reassignment_count,wasted_actual_tokens,total_actual_tokens,matched_baseline_wall_clock_ms,wall_clock_overhead_ms,wall_clock_overhead_ratio,matched_baseline_total_tokens,token_overhead,token_overhead_ratio,cost_overhead`。

worker-death 条件另必须输出：`worker_count,dead_worker_count,kill_progress,coordinator_continued,required_slot_count,recovered_slot_count,result_completeness_rate,root_output_complete,accepted_validity`。`coordinator_continued=true` 只表示 worker 被终止后 coordinator 仍存活并继续产生调度/租约事件；不能用最终完成状态倒推该字段。

`dead_worker_count` 记录操作系统层确认已终止的 executor worker process 数量，而不是计划请求值；实际未达到 condition 的 1 或 3 时该 run 标记 `failed`，不得并入对应 condition。`recovery_latency_ms` 从 lease expiry / 明确 fault detection 中较早的 recovery trigger 时间算到 replacement attempt 被接受的时间；没有恢复成功时为 `null`，并保留 terminal failure。`kill_progress` 同时保存预注册目标和依据已完成 AI units 计算的实际比例，实际注入误差必须进入审计。

actual token 只来自 provider usage。注入变换的 synthetic work 另写 `simulated_mutation_count`，不能伪造 token。论文必须至少展示一个可恢复正例和一个不可恢复或代价过高的负例。

指标分母固定如下：

- `detection_rate = detected_fault_count / injected_fault_count`。

- `false_accept_rate = wrongly_canonicalized_fault_count / injected_fault_count`。

- `recovery_rate = recovered_faulted_unit_count / recoverable_fault_target_count`；不可恢复或只应检测的 fault 不进入分母。

- `completion_rate = completed_root_count / attempted_root_count`。

- `wall_clock_overhead_ms = fault_condition_wall_clock_ms - matched_no_fault_baseline_wall_clock_ms`；`wall_clock_overhead_ratio = wall_clock_overhead_ms / matched_no_fault_baseline_wall_clock_ms`。

- `token_overhead = total_actual_tokens - matched_baseline_total_tokens`；`token_overhead_ratio = token_overhead / matched_baseline_total_tokens`。`wasted_actual_tokens` 只累计因注入、死亡、late/no-return 或 rejected recovery 而无法贡献到最终 accepted result 的真实 provider usage，不与 `token_overhead` 混用。

- `cost_overhead = (fault_condition_cost - matched_no_fault_baseline_cost) / matched_no_fault_baseline_cost`。

- `result_completeness_rate = recovered_slot_count / required_slot_count`；`root_output_complete = (recovered_slot_count == required_slot_count)`。它必须和 deterministic `accepted_validity` 同时报告：slot 齐全但 checker/verifier 不通过，仍不是完整有效结果。

所有 matched baseline 必须与 fault condition 使用相同 task ids、repeat/seed、worker count、GLM-5.2 entry、prompt/parser/plugin/executor version 和 request limits。任何比率的 baseline 分母为 0 时写 `null` 并记录 `zero_baseline_denominator`，不得输出 `Infinity`、`NaN` 或改用其他 condition。

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

Factorization 三档分别使用全部 `167/167/166` roots；Lean 每档按预注册的 2/2/1 分层固定 5-task slice 覆盖三个 topic families。所有模式重复 3 次，并固定使用 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`。报告 completion、accepted validity、wrong canonical acceptance、raw-only acceptance、stuck task、premature merge、slot mismatch、time、token 和 cost。消融实现必须在实验 wrapper/adapter 中，不修改协议 core 的默认 FULL 语义。

消融还必须输出 `exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability`。其中 `exposed_error_count` 是到达被关闭机制、且 FULL 模式本应拒绝或隔离的无效候选/不完整状态数量；`escaped_error_count` 是这些对象中继续进入 canonical、merge 或被错误标记为 terminal success 的数量；`error_escape_rate = escaped_error_count / exposed_error_count`。若某 mode 没有可适用的 gate（例如 NO_REQUEUE 主要观察 stuck/completion）或分母为 0，rate 写 `null`，并把 applicability 写为 `not_applicable` 或 `zero_denominator`，不得用 0 假装“没有逃逸”。

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

使用 Experiment 1 的完整 catalog：Factorization 三档分别使用全部 `167/167/166` roots，Lean 使用与 Experiment 2/4 相同的预注册 2/2/1 固定 5-task slice。三个 cohort member 各自作为 `model_policy="fixed_entry"` 的独立 condition，使用相同 task order、prompt/parser/plugin version、worker count、timeout、request-limit policy 和 repeat/seed family，每个 condition 重复 3 次。AI unit 在整个首次/恢复 attempt 链中保持同一 cohort member，不按 difficulty 换模型，也不在失败后升级到另一个模型。

输出 completion、accepted validity、tokens、cost、latency、provider errors、recovery attempts 和 `model_execution_records`。运行时首先为每个正式 AI unit 保存 `tokenshare.paper_model_execution_record.v2` identity artifact；正式 runner/report 再把该 artifact 与 `PaperAttemptResult` / usage evidence 连接成 `model_execution_records.jsonl`，补齐 `model_policy,latency_ms,total_tokens,cost_estimate` 等统计列。历史 v1 artifact 不重写，新 reader 必须按 schema version 保守读取。论文以 completion / accepted validity 作为主要跨端点结果；latency、cost 和 rate-limit 结果必须按 provider 分层或标注 provider confounding。

正式 Experiment 5 preflight 必须验证三个 cohort member 的 provider config、key env、model id、reasoning profile、真实 smoke evidence、catalog compatibility 和预算。缺少任一 member 时，整个正式 Experiment 5 标记 `blocked`，`blocked_reason="incomplete_model_cohort"`，`paper_eligible=false`，`provider_attempt_count=0`；可用 member 的单模型试跑只能标 `pilot_only=true`，不能生成三模型论文主表。该 blocked 不影响 Experiment 1-4 的主张。

## Fixed-entry 身份闭环

`entry_id` 只在一个 `AIAPIExecutorConfig.entries` 内唯一，不是跨 provider config 的全局模型身份。正式 Experiment 5 必须使用 `provider_config_id + selected_entry_id` 定位 entry，并同时冻结 provider/model/reasoning；仅比较同名 `entry_id` 不构成有效校验。

身份和 digest 分层如下：

- cohort 逻辑身份：`model_cohort_id,model_cohort_digest,cohort_member_id`；`model_cohort_digest` 锚定 tracked frozen cohort。
- endpoint 语义身份：`provider_config_id,selected_entry_id,provider_family,provider_model_id,reasoning_profile_id,effective_reasoning_controls`；`model_endpoint_identity_digest` 对这组语义字段做 canonical digest。
- 批准配置快照：`source_provider_config_digest` 锚定 preflight 加载的原始 safe config。即使 endpoint 语义未变，只要 safe config 内容改变，旧 condition/budget approval 也失效，必须重新 plan/approve。
- 运行时配置 provenance：`prepared_execution_config_digest` 锚定 adapter 过滤为单 entry、覆盖 request limits 并固定 `max_provider_attempts=1` 后的 config。它不能替代 source digest，也不能写回 endpoint 语义 digest。

模型字段的唯一语义和数据来源如下：

| 字段 | 唯一允许来源 | 正式含义与限制 |
|:---|:---|:---|
| `configured_model` | 调用前已校验的 selected config entry | 本地配置要求；只能证明配置了什么，不能证明 provider 实际执行了什么。 |
| `requested_model` | 实际发送的 provider request body `model` | 请求事实；只能证明请求了什么。必须与 configured model 分字段保存。 |
| `response_model` | provider response 原始 JSON 的 `model` 字段 | 原始响应事实；缺字段、`null`、空字符串、非字符串必须分别分类，禁止 fallback。 |
| `resolved_model` | `response_model` 为非空字符串时的原值 | 可用于正式 post-call audit 的 observed identity；其他情形必须为 `null`。alias 不做前缀猜测或本地规范化。 |
| `provider_request_identity.requested_model` | 实际 request body | actual request identity 中的 model；`configured_model` 另存，仅用于 config/request 一致性审计。 |
| `RawModelOutput.model` | 仅历史 `phase7.raw_model_output.v1` | v1 曾混合 response 与 selected entry，语义不可信。v2 删除该字段，改为 `configured_model,requested_model,resolved_model,response_model_status`；v1 reader 必须忽略外层 `model`，只从 `raw_response_json.model` 恢复 observed evidence。 |
| `PaperAttemptResult.model` | executor usage summary 中的 configured entry model | 兼容/展示字段，只表示本次 attempt 绑定的配置模型；不得用于正式 resolved-model audit。 |
| `PaperModelExecutionRecord.resolved_model` | schema-aware RawModelOutput observed evidence | 正式审计字段；nullable，缺失时保持 `null`，不得从 condition、config、request、attempt display 字段或当前 replay config 补写。 |

展示层如需要“模型标签”，必须使用独立 presentation 值（例如 `resolved_model ?? requested_model ?? configured_model`），并明确标注来源；该计算值不得写回 `RawModelOutput`、`PaperModelExecutionRecord.resolved_model`、identity status 或 replay evidence。

Provider call 前必须满足：condition 的 provider config namespace、entry、provider/model/reasoning、cohort digest、source config digest 和 endpoint identity digest都与批准 preflight binding 相等；prepared config 只含批准 entry；`ExecutionRequest.capability_snapshot.provider_family == hard_requirements.provider_family == condition.provider_family`。通用 `AIAPIExecutor` 只执行 request/config provider invariant，不解释 cohort/member/Experiment 5。

Provider call 后必须从持久化 evidence 审计：每个 provenance attempt 的 provider/entry/configured model、`provider_request_identity` 的 configured/requested model 与 reasoning controls、provenance 的 prepared config digest，以及 raw output 的 provider/entry/resolved model。成功或 parse-failed response 的 `model` 缺失、`null`、空字符串或非字符串统一产生稳定 reason `missing_resolved_model`；非空但与批准 identity 不同（包括未批准版本化 alias）产生 `resolved_model_mismatch`。OpenAI resolved model 默认 exact match；版本化 alias 只有进入新的 cohort/preflight approval 才可接受，禁止前缀猜测。上述 identity mismatch 必须把 attempt/failure kind 标为 `model_identity_mismatch`、`paper_eligible=false`，并停止该 condition 内尚未执行的 AI units；已经持久化的 raw/parsed evidence 保留用于审计，但不得进入 verifier/checker/canonical/merge。provider error 且没有成功 raw response 时不得伪造 resolved model，model execution record 写 `identity_status=not_observed`、`response_model_status=unavailable`，attempt 继续保留原 provider failure 分类。

Reasoning normalization 规则：OpenAI `reasoning_effort` 缺失或 `None` 为 `default`，空字符串非法，非空字符串规范化后比较，GPT member 必须为 `high`；SiliconFlow cohort v1 的逻辑 profile 保持 `default`，`enable_thinking` 作为独立 boolean effective control 持久化。JSON-mode builder 隐式产生的 `enable_thinking=false` 必须进入 request identity；未批准的 `true` 不能被 `default` 标签吞掉。

首次执行、provider failure、replacement/retry 和 resume 必须复用 condition 已批准的 endpoint identity；不得重新从当前 config 仅按 entry 字符串解析。source config drift 在 provider call 前失败；单-entry prepared config 禁止 failover 到 sibling entry/model。Replay 只验证和读取历史 request/provenance/raw/usage/model-execution artifacts，provider calls 必须为 0，也不得重新读取当前 API key 或当前 config 来补写历史事实。v1 artifact 的外层混合 `model` 不作为 resolved evidence；不能重写已有历史 artifact。未知 schema 或 v2 内部 requested/resolved/status 与原始 response 不一致时必须 fail closed。

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
| `PaperTaskResult` | `condition_id,repeat_id,task_id,domain,difficulty,paper_difficulty,topic_family,root_status,accepted_validity,failure_stage,failure_kind,attempt_count,provider_attempt_count,wall_clock_ms,total_tokens,cost_estimate,event_refs,artifact_refs,paper_eligible`；worker-death task 另含 completeness 字段。 |
| `PaperAttemptResult` | `condition_id,repeat_id,run_id,task_id,unit_id,attempt_id,worker_id,provider_attempt_index,attempt_status,provider,model,entry_id,request_ref,raw_output_ref,parsed_output_ref,parse_failure_ref,provenance_ref,usage_ref,model_execution_record_ref,started_at,ended_at,latency_ms,prompt_tokens,completion_tokens,total_tokens,cost_estimate,error_kind,fault_injection_ref,paper_eligible` |
| `PaperModelExecutionRecord` | `condition_id,repeat_id,run_id,task_id,unit_id,attempt_id,expected_identity,source_provider_config_digest,prepared_execution_config_digest,request_ref,provenance_ref,raw_output_ref,usage_ref,actual_request_identities,actual_provider_attempts,requested_model,resolved_model,response_model_status,identity_status,mismatch_reasons,paper_eligible,record_digest`；正式 report export 另从 attempt/usage join `model_policy,latency_ms,total_tokens,cost_estimate`。 |
| `PaperBudgetResult` | `budget_digest,planned_experiments,planned_conditions,planned_root_runs,planned_ai_units,max_provider_attempts,token_upper_bound,cost_upper_bound,wall_clock_estimate,quota_preflight,rate_limit_preflight,disk_estimate,status` |

## 状态与失败枚举

`status` 字段必须使用稳定枚举，不能临时写自然语言：

- suite / experiment / condition / run status：`planned`、`running`、`completed`、`completed_with_failures`、`blocked`、`budget_exhausted`、`failed`。

- root task status：`completed`、`failed`、`blocked`、`timeout`、`budget_exhausted`、`ineligible`。

- attempt status：`succeeded`、`model_identity_mismatch`、`provider_error`、`parse_failed`、`verification_rejected`、`checker_rejected`、`lease_expired`、`late_rejected`、`worker_died`、`cancelled_by_budget`。

- failure stage：`catalog`、`split`、`request`、`provider`、`parse`、`verification`、`checker`、`canonical`、`merge`、`settlement`、`metrics`、`audit`。

- failure kind：`model_identity_mismatch`、`provider_error`、`rate_limited`、`parse_failure`、`verifier_rejected`、`checker_rejected`、`lease_expired`、`late_submission`、`no_requeue`、`premature_merge`、`slot_mismatch`、`budget_limit`、`unsupported_worker_level`、`missing_model_entry`、`secret_leak`、`internal_error`。

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
      "schema_version": "tokenshare.paper_condition.v2",
      "experiment_id": "exp2_real_ai_scalability",
      "condition_id": "...",
      "domain": "factorization|lean_proof",
      "difficulty": "easy|medium|hard|all",
      "paper_difficulty": "easy|medium|hard|simple|medium_lemma_dag|hard_frontier|all",
      "topic_family": "pure_logic|function_set|induction|not_applicable|all",
      "worker_count": 10,
      "fault_type": "none",
      "fault_rate": 0.0,
      "dead_worker_count": 0,
      "kill_progress": null,
      "matched_baseline_condition_id": null,
      "ablation_mode": "FULL",
      "model_policy": "fixed_entry",
      "model_cohort_id": null,
      "cohort_member_id": null,
      "provider_config_id": "siliconflow",
      "model_entry_id": "glm_5_2_exp1_baseline",
      "provider_family": "siliconflow",
      "provider_model_id": "zai-org/GLM-5.2",
      "reasoning_profile_id": null,
      "model_cohort_digest": null,
      "source_provider_config_digest": null,
      "model_endpoint_identity_digest": null,
      "repeat_id": 0,
      "seed": 1,
      "catalog_digest": "sha256:...",
      "real_transport_required": true,
      "paper_eligible_required": true
    }

## Per-task result 最小字段

每行必须包含 condition/run/task id、domain/difficulty/topic family、root status、accepted validity、failure stage/kind、worker/attempt/provider/model、parser/verifier/checker/merge 状态、wall-clock/provider latency、tokens/cost、fault/ablation refs、event/artifact refs 和 `paper_eligible`。Experiment 3 worker-death 行还必须包含 `dead_worker_count,kill_progress,coordinator_continued,required_slot_count,recovered_slot_count,result_completeness_rate,root_output_complete`。任何 summary 数字都必须能回到这些逐 task/attempt 记录和 event/artifact evidence。

## Per-attempt result 最小字段

每行必须包含 `condition_id,repeat_id,run_id,task_id,unit_id,attempt_id,worker_id,provider_attempt_index,provider,model,entry_id,request_ref,raw_output_ref,parsed_output_ref,parse_failure_ref,provenance_ref,usage_ref,model_execution_record_ref,started_at,ended_at,latency_ms,prompt_tokens,completion_tokens,total_tokens,cost_estimate,attempt_status,error_kind,fault_injection_ref,paper_eligible`。如果 provider failover 发生，必须为每次 provider attempt 写独立行或写入可展开的 `provider_attempts[]`，不能只保留最后一次。正式 Experiment 5 的每个 AI unit 必须能从 `model_execution_record_ref` 回到 expected identity、实际 request/attempt identity、raw resolved model 和 source/prepared config digests。

## 实验专项汇总最小字段

- `paper_plot_robustness.csv` 必须包含通用 fault 字段、matched baseline id/value、`wall_clock_overhead_ms,wall_clock_overhead_ratio,token_overhead,token_overhead_ratio,cost_overhead`；worker-death 行另含 `dead_worker_count,kill_progress,coordinator_continued,result_completeness_rate,root_output_complete,accepted_validity`。
- `paper_table_ablation.csv` 必须包含 `ablation_mode,exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability,completion_rate,accepted_validity_rate,wrong_canonical_acceptance_rate,raw_only_acceptance_rate,stuck_task_rate,premature_merge_rate,slot_mismatch_rate,wall_clock_ms,total_tokens,cost`。
- `paper_table_feasibility.csv` 必须包含 `domain,paper_difficulty,topic_family,case_count,repeat_count,completion_rate,accepted_validity_rate,wall_clock_median_ms,wall_clock_p90_ms,total_tokens_median,total_tokens_p90,cost_per_completed_task,failure_kind,count`，并能生成每个 topic family 的 `highest_observed_valid_completion_difficulty`。

## Catalog manifest 最小字段

`input_catalog_manifest.json` 必须包含 `schema_version,catalog_id,catalog_version,catalog_digest,generator_version,case_count,domain_counts,difficulty_counts,oracle_validation_status,lean_preflight_status,created_at,source_files`。Lean 正式 catalog 还必须包含 `paper_difficulty_counts,topic_family_counts,matrix_cell_counts`，且九个 `matrix_cell_counts` 必须全部等于 15、总数等于 135，用于区分 current shallow-v1 legacy difficulty 和正式 Lean paper difficulty。任一 case 的 oracle 或 Lean preflight 失败时，catalog freeze 失败；不能在正式 run 中静默跳过该 case。

# 论文表格、图和可写结论

| 论文产物 | 数据文件 | 可以回答 |
|:---|:---|:---|
| Feasibility table | `paper_table_feasibility.csv` | 两个领域、三档 paper difficulty 的完成率、accepted validity、时间、token、成本；Lean 按 3×3 矩阵每格 15 道报告，当前 shallow v1 只能进入 simple。 |
| Difficulty figure | feasibility CSV 派生 | 难度上升时成功率和成本如何变化，能力边界在哪里。 |
| Scalability figure | `paper_plot_scalability.csv` | worker 增加后的 wall-clock、throughput、speedup、efficiency、token 和限流。 |
| Robustness figure | `paper_plot_robustness.csv` | fault rate 对检测、恢复、完成、时间/token overhead 的影响。 |
| Ablation table | `paper_table_ablation.csv` | 关闭一个机制后哪种错误逃逸或任务卡住。 |
| Failure examples | `failure_examples.json` | 至少一个可恢复和一个不可恢复案例的完整 evidence chain。 |

只有数据支持时才可写“worker 增加缩短时间”“某一具名 model-provider endpoint 成本较低”或“某类错误可恢复”。负面结果可以直接写：例如速度在 10 workers 后饱和、false negative 在某种同批次条件下无法恢复、NO_VERIFICATION 导致错误 canonical。不得预写必然正向结论。

论文结构建议固定为：`Feasibility Across Two Domains`、`Scalability with Real AI Workers`、`Robustness and Failure Boundaries`。Ablation 放在第三部分，三模型 model-provider endpoint comparison 放 appendix 或次要 subsection。

# API、时间、token、成本和人工投入

正式 P0 的执行规模固定如下；不得自行缩小。运行前必须重新生成并记录预算 digest 和硬上限，但当前本地研究原型默认不要求人工批准 digest：

| 实验 | 最小正式规模 | root-run 数量 |
|---|---:|---:|
| Experiment 1 | Factorization 500 + Lean 135 = 635 unique roots × 3 repeats | 1,905 |
| Experiment 2 | Factorization 500 × 4 worker levels × 5 repeats；Lean 15 × 4 × 5 | 10,300 |
| Experiment 3 rate faults | Factorization 500 × 5 fault types × 7 rates × 3 repeats；Lean 3 × 5 × 4 × 3 | 52,680 |
| Experiment 3 worker death | Factorization 三档并集 500 × 2 death counts × 3 kill positions × 3 repeats；Lean 3 × 2 × 3 × 3 | 9,054 |
| Experiment 4 | Factorization 500 × 6 modes × 3 repeats；Lean 15 × 6 × 3 | 9,270 |
| Experiment 5（三模型 cohort 完整时纳入 P0-full） | (Factorization 500 + Lean 15) × 3 endpoints × 3 repeats | 4,635 |

P0-core（Experiment 1-4）合计 83,209 个 root-runs；P0-full（Experiment 1-5 且三模型 cohort / provider preflight 通过）合计 87,844 个 root-runs。100 / 300 worker extension 必须在 suite manifest 中标记为 extension，不并入 P0-core 或 P0-full 主统计。root-run 数量不等于 provider calls。Factorization root 可能拆成多个 range AI units，Lean root 可能拆成多个 proof AI units；真实 provider-attempt 上界必须由 split preflight 精确展开。若预算上限无法覆盖计划，runner 写 `budget_exhausted` 并停止启动新 task；不得静默减少样本、删 mode、删 difficulty、删 topic family 或把 Lean 每格 15 道减为子样本。

## 运行前预算门禁

runner 必须先执行 `--plan-only`，根据 catalog、child counts、conditions、repeats 和 max provider attempts 生成 `run_budget.json`：

``` math
N_{calls}^{max}=\sum_{conditions}\sum_{tasks}
  N_{AI\ units}(task)\times repeats\times maxProviderAttempts
```

预算文件至少包含 planned root tasks、AI units、provider attempts 上界、token 上界、cost estimate 上界、预计 wall-clock、provider/model、并发、quota/rate-limit preflight 和磁盘空间估计。CLI 默认使用 `approval_mode=user_bypassed` 直接执行，同时记录 budget digest、`authorization_source=project_policy` 和实际 usage；如需恢复旧的人工门禁，显式传 `--require-budget-approval`，此时才要求匹配的 `--approve-budget-digest`。即使 bypass，任何显式提供但不匹配的 digest 仍必须在 transport 前拒绝。

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
| `benchmarks/paper/factorization_catalog.v2.jsonl` | 正式默认的 factorization 500-task catalog，easy/medium/hard=`167/167/166`。 |
| `benchmarks/paper/factorization_catalog.v1.jsonl` | 历史 30-task 回归 catalog，不进入新正式矩阵。 |
| `benchmarks/paper/lean_catalog.v1.jsonl` | 冻结的 Lean simple/shallow 30-task catalog；历史 easy/medium/hard 只保留为 shallow-v1 标签。 |
| `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 或 `benchmarks/paper/lean_catalog.v2.jsonl` | 后续必须新增的 medium recursive lemma-DAG / hard-frontier catalog，用于正式 Lean 复杂度主张。 |
| `src/tokenshare/experiments/paper_models.py` | `PaperExperimentCondition`、`PaperModelExecutionRecord`、budget、fault record、paper eligibility schema 和 digest。 |
| `src/tokenshare/experiments/paper_model_identity.py` | Experiment-layer endpoint identity、provider-specific reasoning normalization、condition-to-config pre-call binding 和 submission post-call audit；不得导入 runner/adapter 或硬编码 cohort member 列表。 |
| `src/tokenshare/experiments/paper_catalog.py` | 加载、校验和 digest paper catalogs；本地 oracle/Lean preflight；后续必须区分 Lean `paper_difficulty` 与 shallow-v1 legacy difficulty。 |
| `src/tokenshare/experiments/factorization_paper_adapter.py` | 从 root split 到 range children，所有 range candidate 经真实 `AIAPIExecutor`、插件 parser/verifier、canonical/merge。 |
| `src/tokenshare/experiments/lean_paper_adapter.py` | 执行分难度 Lean catalog，真实 API child/direct proof、checker、merge/root recheck，并支持 model/worker config。 |
| `src/tokenshare/experiments/paper_faults.py` | 在真实 output 后执行 deterministic fault selection/mutation，写 `FaultInjectionRecord`。 |
| `src/tokenshare/experiments/paper_workers.py` | 独立 worker process、kill point、lease expiry、replacement attempt 和进程 evidence。 |
| `src/tokenshare/experiments/paper_model_policy.py` | 加载/校验三模型 cohort snapshot 与 local entry map，构造批准的 fixed-entry member plans；不在 executor 内解释模型强弱。 |
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

1.  先写 paper model/catalog/budget 的失败测试，验证 digest、500 题 Factorization v2、v1 严格回归、plan-only、预算记录和可选人工 approval policy。

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
  --baseline-entry-id glm_5_2_exp1_baseline `
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
  --baseline-entry-id glm_5_2_exp1_baseline `
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

CLI 若未给 `--real-transport`、Experiment 1-4 baseline 不能从 `benchmarks/paper/exp1_baseline_provider_config.v1.json` 解析到 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`、catalog digest 不匹配、预算 digest 不匹配或输出目录已有不同 suite manifest，应拒绝启动，不得自动退回 scripted transport，也不得用 Experiment 5 cohort 的其他 member 替代。运行 Experiment 5 时还必须加载 frozen cohort、local entry map、SiliconFlow/OpenAI provider configs，并通过三个 member 的 key/model/reasoning/smoke preflight；任一 member 缺失时 runner 写 `blocked_reason="incomplete_model_cohort"`，而不是让 Experiment 1-4 失败，也不得自动替换模型。

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
