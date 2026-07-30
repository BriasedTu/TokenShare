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

1.  Experiment 1 正式输入固定为 500 个 Factorization roots（easy/medium/hard=`167/167/166`）和 135 个 Lean roots（3 个 `paper_difficulty` × 3 个 `topic_family` × 每格恰好 15 个），合计 635 个唯一 roots；按 2026-07-24 用户参数决定，两个领域每题只运行 1 次，共 635 个 root-runs。500 个 Factorization roots 和每格 15 道 Lean 都是冻结的正式样本量，不得临时抽样冒充正式 Experiment 1。EPD-001 只取消 Experiment 1 同题重复；后续 EPD-003 另行修改 Experiment 2 的 domain、题库、worker levels 和 repeats。决策 provenance 和同步状态见 `tokenshare_experiment_parameter_decision_log.md`。

2.  Experiment 2 P0-core scaling 只使用 Factorization hard 的全部 166 roots，Lean 不进入扩展性实验。Factorization 插件按 Exp2 冻结的 `factorization.exp2_contiguous_20way.v1` profile，把每个 root 的完整候选因子域确定性切成 20 个连续 range children；强制 worker levels 为 `1, 3, 7, 10, 30, 50`，每档对相同 166 roots 运行 2 遍，共 1,992 个 root-runs 和 39,840 个 planned AI units。worker 30/50 都受单 root 20 个 range children 限制，用于观测扩展性上限。保留 verifier-accepted factor witness 的自然早停，不取消已发出的同批请求；报告必须同时给出延迟、实际 provider calls、已执行/未调度 AI units、token/cost 和利用率。决策 provenance 见 EPD-003。

3.  Experiment 3 的 rate-fault 矩阵只包含 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。Factorization rate-fault condition 使用全部 500 roots；`worker_death` 永远单独进入 worker-death 矩阵，Factorization worker-death condition 按 difficulty 使用 `167/167/166`，每组 death-count/kill-position/repeat 的三档并集为全部 500 roots。worker-death P0 固定 `worker_count=10`、`dead_worker_count ∈ {1,3}` 和 25% / 50% / 75% 三个 kill positions。每个实际 fault condition 均重复 2 次；Factorization rate-fault 只调度 `1/5/10/25/50/100%`，Lean 只调度 `10/50/100%`，0% 行由正式 Exp1 同 `case_id` 的持久化 evidence 投影且不形成 Exp3 root/provider call。因此 rate-fault 共 30,090 root-runs，worker-death 共 6,036，Experiment 3 合计 36,126。

4.  Experiment 4 P0-core ablation 的 Factorization 每个 difficulty 使用全部 `167/167/166` roots，Lean 每档使用固定 5-task 2/2/1 slice；按 EPD-005 删除 `NO_SLOT_INTEGRITY` 后，固定为 `FULL + 4` 个消融模式和 3 次 repeat，共 7,725 个 root-runs。旧的 3-task/5-task Factorization 子样本不再作为正式 P0 口径。

5.  Experiment 5 当前预注册口径为 cohort v3：SiliconFlow `zai-org/GLM-5.2`、`Qwen/Qwen3-14B`、`MiniMaxAI/MiniMax-M2.5`、`Pro/deepseek-ai/DeepSeek-V3` 四个固定 entry，共用 `timeout_seconds=600`、`max_tokens=32768`、`max_provider_attempts_per_ai_unit=1`；GLM/Qwen/MiniMax 启用 thinking 且 `thinking_budget=32768`，DeepSeek 使用 nonthinking。题库使用 Exp5 专属、分层确定性半量 hard-only selection：Factorization 83 道，Lean 三个 topic family 各 8 道，共 107 道/model-repeat；四模型 × 3 repeats 共 48 conditions、1,284 root-runs、9,888 个 planned first-attempt AI units，token ceiling=`607,518,720`。离线设施和独立 8-root bootstrap 入口已实现；五次最小 endpoint/key 诊断不是 artifact-backed evidence，8-root smoke 尚未运行，因此正式 preflight 仍必须在 provider 调用前 blocked。cohort v1/v2、1,899-root 三端点矩阵和 `100/8192` 只供历史 replay/provenance。

6.  除 Experiment 5 当前四端点对比外，Experiment 1–4 的所有 pilot、正式 condition、故障恢复 attempt 和消融 mode 固定使用官方 DeepSeek `deepseek-v4-pro`，当前安全配置为 `benchmarks/paper/exp1_baseline_provider_config.v3.json`，entry id 为 `deepseek_v4_pro_exp1_baseline`，官方 base URL/endpoint 为 `https://api.deepseek.com` + `/chat/completions`，`thinking={"type":"enabled"}`、`reasoning_effort=high`、`timeout_seconds=600`、`max_tokens=300000`。Thinking 模式请求不得发送 `temperature` 或 `top_p`。缺少该 entry、API key、真实 smoke 或 identity evidence 时对应实验结构化 `blocked`；不得切换到 GLM、OpenAI 或其他模型继续生成论文结果。`max_in_flight_global=50` 是 provider 并发上限，与 Experiment 2 的 `worker_count=1/3/7/10/30/50` 保持独立。v2 的 `100/8192` 配置只保留为历史 replay 与 Experiment 5 cohort v2 成员输入，不得冒充当前 Exp1–4 或 Exp5 v3 配置。

7.  P0-core 指 Experiment 1-4；P0-full 指 Experiment 1-5 且 Exp5 v3 四 entry 的 capability、identity、selection、budget、output-contract 与 smoke preflight 全部通过。当前只有 Exp5 v3 离线设施与非正式诊断证据，artifact-backed 8-root smoke 未运行，所以 P0-full 不得启动。所有 summary、预算和论文表格必须标明自己属于 P0-core、P0-full 还是包含 100/300 worker extension 的扩展运行。

# 论文要回答的三个主问题

论文实验章节只围绕三个主张组织：

1.  **可行性（Feasibility）**：同一协议生命周期能否在 factorization 和真实 Lean proof 两个不同领域中，用真实 AI API 生成候选输出，并由各自确定性 verifier/checker 给出可审计结果？

2.  **扩展性（Scalability）**：固定任务和模型时，增加协议 worker/node 是否改变端到端时间、吞吐、token、成本和失败率？收益在哪个并发点开始饱和？

3.  **鲁棒性（Robustness）**：真实 AI 输出之后发生 false positive、false negative、不返回、延迟、executor error 或 worker death 时，协议能否检测、隔离、重试、重分配并完成？哪些错误只能检测而不能恢复？

协议消融用于解释第三个主张中各机制的贡献；Experiment 5 的四模型、同一 SiliconFlow provider endpoint comparison 只作为次要分析，不单独证明协议正确性。它减少了跨 provider 混杂，但仍受同一 provider 的 serving profile、路由、限流和计费口径约束，不能把观察差异解释为模型本体的纯因果效应。

# 论文可采信硬门槛

## 真实 AI API 门槛

一条 run 只有同时满足以下条件，才可标记 `paper_eligible=true`：

1.  `real_transport=true`，且配置来自被 gitignore 的本地 config；标准配置只记录 `api_key_env`。

2.  每个需要 AI 生成的 `TaskUnit` 至少存在一次真实 provider attempt；不得用 scripted response、mock executor 或 deterministic answer 替代。

3.  保存 request、raw model output、parsed output 或 parse failure、provider provenance、usage、latency、cost estimate 和模型身份 artifact。

4.  输出中不存在 API key；event、artifact、SQLite、日志、config digest 和论文 CSV 均不得包含 secret。

5.  AI 不决定协议级拆分。factorization 的 range split 由插件确定性规则生成；Lean 允许保留 catalog/脚本预先写好的、版本化的固定 lemma-DAG，由 Lean 插件在运行时校验、规范化并生成拆分 certificate。这里不要求运行时自动发现任意 theorem 的全部中间引理。

6.  factorization 结果必须经插件 parser/verifier；Lean proof 必须经固定本地 Lean/lake/toolchain/project checker。

7.  replay/metrics/report 阶段不重新调用 AI API，也不重新调用 Lean 来补写历史成功事实。

任何缺少真实 provider attempt 的 run 都必须输出 `paper_eligible=false` 和具体 `ineligibility_reasons`。现有 `run_all` 默认 suite、scripted Lean 50、scripted AI profile 和 deterministic fixture 即使测试通过，也只能标记 `regression_only=true`。

## 系统执行权威与实验边界

正常 `FULL` 论文 run 必须由 TokenShare 系统应用层驱动协议生命周期。目标边界如下：

1.  `tokenshare.core` 只保存协议对象、不变量和 submission/retry/merge 等纯决策，不负责线程、进程、provider、文件或实验矩阵。
2.  `ProtocolEngine` 是协议事实写入权威，负责把调度、lease、submission、verification、canonical、split/expand、recovery、merge、completion 和 settlement 写入 ledger/artifacts。
3.  新增 `tokenshare.local_runtime` 作为本地应用协调层，循环调用 scheduler、lease、executor、插件和 `ProtocolEngine`；它不是生产网络 runtime。
4.  Factorization/Lean 插件继续拥有领域拆分、parser/verifier/checker 和 merge/readiness 规则；Lean 固定 lemma-DAG 的 plan body 可以来自预注册 catalog，但 certificate/proposal/merge plan 必须由 Lean 插件校验后产生。Factorization 当前使用版本化的非对称完成策略：任一 parser 解析且 deterministic verifier 接受的有效 factor witness 足以进入 merge；没有有效 witness 时，只有全部 required ranges 都 accepted、canonical 且 coverage/slot 完整，才能给出 no-factor/prime 结论。通用 runtime 只消费领域无关的 readiness decision，不识别 `found_factor`。
5.  `tokenshare.experiments` 只选择 catalog/condition/repeat/model，注入 fault/ablation/worker-kill 条件，并从权威 ledger/artifacts 派生 `PaperTaskResult`、metrics 和 report。实验结果对象不得反过来决定 canonical、requeue、merge 或 root completion。

2026-07-23 system runtime 迁移 Task 1-9 已完成，2026-07-24 又补齐迁移复核发现的三个阻塞点：正常 FULL 与当前可达的 selected-unit pilot 都统一进入 `paper_dispatcher`、`ProtocolRunCoordinator` 和 `ProtocolEngine`；selected-unit 只是版本化 `ProtocolExecutionScope`，仍由插件 plan/split、engine 创建事实和 scheduler/lease 领取指定 unit，只投影 `partial` 观察，不伪造未执行 sibling 或 root merge/completion/settlement，且固定 `paper_eligible=false`。formal plan 把规划时使用的版本化 catalog execution view 冻结进 dispatch plan，执行、resume 和 replay 复用同一 body/digest，不能从当前可变 manifest 重新推导。迁移前由 `factorization_paper_adapter.py`、`lean_paper_adapter.py` 或 `paper_formal_runner.py` 直接推进生命周期的结果只作为 **historical** 模型效果或回归资料，不能单独证明“协议系统本体执行了对应生命周期”。正式迁移与最终门禁仍以 `2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md` 为准。

同日完成的 callback 小补丁进一步固定当前系统事实：`ProtocolEngine` 在处理 submission/heartbeat 时以 ledger 最新 attempt/lease snapshot 为权威；stale submission 可保留审计记录但不能推进 terminal attempt，terminal stale heartbeat 不产生新 lease event。该行为只解决受信本地正常时序一致性，不增加攻击防护、故障类型或论文实验条件。Task 10 的既有 `330 passed, 1 skipped` Fast 和 150-entry Lean 抽样仍只代表当时收口证据；小补丁后续通过 Fast `330 passed, 1 skipped` 与 Full `1302 passed, 1 skipped`，但仍不能替代 600-entry force-all 或正式真实-provider Experiment 1–5。

## 受信本地原型与安全非目标

TokenShare V1 假设实验人员、catalog、配置、插件、executor registry、本地文件和启动命令均属于受信研究环境。本项目不面向外部不可信输入或主动攻击者，feat-011 和 system runtime 迁移不得新增攻击防护或安全加固工作流。

故障处理范围严格限于 Experiment 3 的五类 rate-fault：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。`worker_death` 是同一实验中单独预注册的进程终止条件，只测试既定 lease expiry/reassignment，不扩展为任意 crash、恶意 worker 或拜占庭容错；Experiment 4 ablation 是机制关闭实验，不增加故障类型。

正常执行中自然出现的 `provider_error`、`rate_limited`、`parse_failure`、`verifier_rejected`、`checker_rejected`、`budget_limit` 或 `internal_error` 仍按事实进入 failure taxonomy 和报告；它们不是新增的 fault-injection 类型，也不授权单独建设安全/恢复子系统。

明确不做：恶意手工篡改/伪造 event、artifact、manifest 或 checkpoint 的对抗防护；path traversal、symlink、SQL/JSON/command injection、反序列化和资源耗尽攻击加固；schema/security fuzzing；不可信 plugin/executor/provider envelope；身份、权限、签名、ACL、生产 sandbox 或攻击者模型。不得为这些范围新增代码、测试、报告或论文主张。

现有正常路径 schema/type 校验、parser/verifier/checker、lease/fencing/deadline 不变量、artifact content hash、secret 不落盘和 checkpoint/replay 身份一致性继续保留，因为它们保证受信实验的正确性与可复现性；不得把它们继续扩展或表述为 adversarial security guarantee。

## 受控故障仍必须经过真实 API

故障注入不能绕过真实 API。正确流程是：

1.  正常发送真实 API request，保存 provider provenance、raw output 和 usage。

2.  在预先声明的注入点对 parsed candidate、submission、lease 或 worker 进程做受控变换。

3.  保存独立 `FaultInjectionRecord`，同时引用原始真实输出和变换后输出。

4.  恢复 attempt 若需要 AI 候选，必须再次调用真实 API；不得复用未被协议接受的历史 candidate 假装恢复完成。

因此，论文必须把“自然模型错误”和“真实输出后的受控注入错误”分开统计。注入变换本身消耗 0 个 provider token；原始调用和恢复调用的实际 token 必须全部计入。

# 当前系统事实与剩余实验门槛

| 组件 | 当前已有能力 | 新实验缺口 |
|:---|:---|:---|
| Phase 7 AI executor | 真实 SiliconFlow-compatible 与 OpenAI Chat Completions transport；raw/parsed/error/usage/latency/cost/provenance artifact；secret 和 replay guard。 | 新论文 runner 必须强制 real transport、预算门禁、固定模型策略和每个 AI unit 的 provider-attempt coverage。 |
| Phase 8 default suite | 通用 runner、adapter、simulation、metrics/report；默认 Experiment 1–4。 | 默认使用 deterministic/scripted 路径，旧 case 不再是论文实验；`SimulationProfile` 也没有 fault rate、worker count、difficulty、repeat、model policy。 |
| Factorization 500 benchmark | 500 个 deterministic semiprime、真实 API direct answer、并发、准确率、token/cost/latency。 | 它直接让模型给完整分解，`worker_count` 并发的是独立整数，不是协议内部 range workers；不能单独证明协议 lifecycle 或 worker scaling。 |
| Factorization paper adapter | 500-root catalog、真实 AI range child、插件 parser/verifier/非对称 readiness/merge 已接入 Factorization runtime bridge；正常 FULL/fault/ablation 与 selected-unit diagnostic 路径都由 system coordinator/`ProtocolEngine` 推进实际协议事实，并从 ledger/artifacts 投影 result shape。 | selected-unit 只执行指定协议 unit 并返回 paper-ineligible partial observation；不会由 adapter 直推生命周期，也不会伪造 root completion。 |
| Lean paper adapter | simple helper 与预注册 fixed lemma-DAG 都已接入 Lean runtime bridge；插件校验 fixed plan/certificate，真实 checker、dependency-aware merge/root recheck 和 system coordinator FULL 生命周期已完成；selected-unit diagnostic 同样经 coordinator/engine/checker。 | partial observation 不执行 sibling，也不产生 root merge/completion/settlement。catalog/脚本预写的固定拆分图继续保留，不要求任意 theorem 的通用自动 lemma discovery。 |
| Paper formal runner/adapters | 已通过 shared dispatcher/system runtime 执行 FULL、五类 rate-fault、真实 process worker death、消融 hook 和 Exp5 fixed-entry endpoint binding，并从权威 ledger/artifacts 派生 paper projection；runner 不再决定 canonical、replacement、merge/completion。五类 rate-fault 以 `case_id:planned_ai_unit_id` 映射到实际协议 unit，worker death 由 process backend 终止真实 executor process。 | 2026-07-26 已修复正式资格分裂及 Exp2–5 的证据真实性 blocker；捕获式、脚本式、selected-unit partial 和任何缺失下层 evidence 的结果仍一律 regression-only / paper-ineligible。正式真实-provider 数据尚未运行，不能发布论文结论。 |
| Task 14 readiness | `benchmarks/paper/lean_task14_3x3_readiness.v1.json` 已冻结九格×每格 15、合计 135 个 selected checker-backed cases；Task 5 已由 Lean 插件校验 fixed plan、生成 certificate，并由 system runtime/`ProtocolEngine` 记录 FULL 生命周期。 | readiness 和本地 checker 回归仍不等于正式真实-provider实验结果；正式矩阵还必须通过后续 runtime hooks、预算和 provider evidence gate。 |
| Metrics | 正式 CLI 只使用 `paper_formal_metrics.py` 从持久化 condition/task/attempt/event/artifact/runtime observation 复算；Exp2 关键路径、Exp3 recovery/worker death、Exp4 hook/配对、Exp5 v2 identity inventory 和 429 sensitivity 均已接入生产 CSV。 | 任何依赖边、时间戳、hook ref、FULL 配对、attempt identity record 或分母证据缺失时写 `null`/applicability reason 并使行不合格；不回退到 wall clock、provider latency、mode/fault 名称或现存记录分母。 |
| Worker fault | 实验层冻结 kill plan/selection；runtime process backend 终止真实 worker，coordinator/engine 记录 lease expiry、recovery 和 replacement；25%/50%/75% 使用真实 completed/planned progress。无故障 reference 统一引用正式 Exp1 同 `case_id` evidence，不新增 dedicated baseline 调度。 | 正式数据仍须由真实 provider 条件运行；实际死亡进程数、death 后 coordinator event 或 replacement canonical slots 不足时形成 `failed_experimental + complete`；Exp1 reference 缺失、hash/schema/identity 不符才作为设施失败 fail closed。 |
| Report | formal evidence、metrics/report、预算、paper eligibility、secret scan 和论文 CSV 生成路径已实现；report 独立复核 suite→experiment→condition→task→attempt 与 event/artifact refs。 | 只有所有必需下层 row/evidence 均合格且 transport 为真实 API 时才生成 `formal_paper_report.md`；否则只生成 `formal_regression_report.md`。尚无本轮正式真实-provider 数据，不得预写论文结论。 |

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

EPD-009 覆盖 EPD-007 对 Experiment 1–4 timeout 的旧决定：正式 Experiment 1–4 的官方 DeepSeek 单次请求固定 `timeout_seconds=600`、`max_tokens=300000`。EPD-011 进一步冻结 Experiment 5 cohort v3 的四个 SiliconFlow entry：共同 `timeout_seconds=600`、`max_tokens=32768`、每 AI unit 仅 1 次 provider attempt；GLM/Qwen/MiniMax 必须匹配 thinking 与 `thinking_budget=32768`，DeepSeek 必须匹配 nonthinking。任何 entry 即使共同漂移也必须在 preflight fail closed。cohort v2 的 `100/8192` 仅供历史 replay。worker-death process backend 继续使用既有 `max(30, request_timeout + 30)` 进程保护，Exp1–4 当前派生值为 `630` 秒；该保护时间不是额外的 provider 响应配额。参数变更会改变 provider/profile/condition/budget identity 和 digest，旧批准 digest 均不得复用。

本决定不修改通用 `AIAPIExecutor` 和 paper adapter 的 30 秒缺省回退、不修改协议 lease 的 300 秒、不修改 Lean payload/checker 的 30 秒资源限制，也不修改历史 direct Factorization 500 benchmark 的 60 秒 timeout。

真实 API 温度等非确定性参数必须写入 request artifact。论文主对比不得在看到结果后更换模型或 prompt；若必须修复 prompt contract，修复前后的结果分开成不同 experiment version。

Experiment 1–4 的模型控制变量不是运行时任选值：必须解析到官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，首次执行、provider retry、replacement 和 resume 都保持同一 identity。Experiment 5 单独加载四模型 cohort v3，所有 entry 都绑定 SiliconFlow provider config；它们与 Experiment 1–4 baseline entry 属于不同命名空间，不得混用 digest、approval 或 smoke evidence。

# 输入 catalog 与难度定义

## Factorization catalog v2

冻结 `benchmarks/paper/factorization_catalog.v2.jsonl` 作为论文 CLI 默认 Factorization catalog，共 500 个 root tasks，easy/medium/hard=`167/167/166`，目标整数覆盖 `[1_000_000,100_000_000_000)` 且每档覆盖 10^6 至 10^10 数量级。文件名和 `catalog_version=v2` 保持不变；当前 generator version 固定为 `tokenshare.paper_factorization_catalog.v2.full_domain.v1`。`factorization_catalog.v1.jsonl` 的 30 题只保留为历史回归输入，不进入新的正式矩阵。

Factorization v2 的 root-validity 依赖完整试除域。每个 case 必须满足 `candidate_start=2`、`candidate_end=floor_sqrt(target_n)`、`candidate_divisor_count=candidate_end-candidate_start+1`；不得再用按 difficulty 截断的局部 candidate window。difficulty 表示固定的确定性 range partition 粒度和并发形状，不改变完整域：

| 难度 | 完整 candidate domain | requested children | factor position | 目的 |
|:---|:---|:---|:---|:---|
| easy | `[2,floor_sqrt(target_n)]` | 2 | early/middle/late 均衡 | 验证基本真实 API + parser/verifier/merge 闭环。 |
| medium | `[2,floor_sqrt(target_n)]` | 4 | early/middle/late 均衡 | 测试更多 range units 和适度并发。 |
| hard | `[2,floor_sqrt(target_n)]` | 8 | early/middle/late 均衡；另有恰好 7 个 no-factor prime controls | 观察成本、失败和饱和，不追求全对。 |

每行至少包含：`case_id,target_n,oracle_prime_factors,candidate_start,candidate_end,candidate_divisor_count,factor_position_quantile,difficulty,split_params,source_seed,generator_version`，且 500 行的 `generator_version` 必须全部精确等于当前冻结版本，缺失、`null` 或漂移均使 catalog freeze 失败。semiprime 的较小质因子必须相对完整 `[2,floor_sqrt(target_n)]` 域计算 early/middle/late，三类在每个 difficulty 内均衡；7 个 hard controls 的 target 本身为质数，完整域内确实无因子。case ID 和 ordinal 固定为 `factor_v2_easy_001...`、`factor_v2_medium_001...`、`factor_v2_hard_001...` 的连续顺序。target 和 oracle 由 deterministic generator 生成并在运行前验证，但候选执行必须走真实 AI API，catalog oracle 不得替代 AI candidate 或 verifier evidence。

2026-07-20 完整域重生成使旧 catalog、selection、condition 和 budget identity 全部失效；旧 Exp1 budget digest `sha256:732196d576ba1a7245cda06576533695e6cbf627383f5f0570fd195ea49adddc` 明确不得复用。下一次真实 pilot 必须使用新的 output root 重新执行 plan-only，再以新 digest 和冻结 identity 进入最小 Factorization pilot。完整域修复不改变 2/4/8 AI-unit 算术。2026-07-24 EPD-001 又把正式 Exp1 改为两个领域每题只运行 1 次，因此当前 Exp1=`635` root-runs / `2,900` planned AI units，任何按旧 `1,905` / `8,700` 规模生成的 condition、selection 和 budget identity 同样不得复用。

## Lean catalog 分层要求

`benchmarks/paper/lean_catalog.v1.jsonl` 已经冻结并可由固定 toolchain 检查，但 2026-07-15 起它只作为 **simple / shallow Lean catalog**：其中历史 `easy` / `medium` / `hard` 标签只表示 shallow-v1 proof-chain 长度和上下文干扰，不再满足正式论文的 Lean 三档难度定义。当前 v1 的全部 30 个 `P ∧ Q` / `P ↔ Q` case 都归入正式论文口径的 `simple` 层，只能用于 adapter、checker、artifact、fault/worker/ablation 基础设施验证和成本校准；不得用它们单独支撑“复杂 Lean 递归拆分”或“hard theorem proving”主张。

正式论文 Lean catalog 必须扩展为分层 catalog，建议新增 `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 或 `lean_catalog.v2.jsonl`，并把 `paper_difficulty` 与旧 `difficulty` 字段区分开：

| 正式层级 | 客观定义 | 构造规则 | 论文用途 |
|:---|:---|:---|:---|
| simple | 当前 v1 shallow theorem：单个 root theorem，顶层 `P ∧ Q` / `P ↔ Q`，通常 2 个 child，child proof 是直接假设或短 implication chain。 | 保留当前 v1，必要时把旧 `difficulty` 重命名或解释为 `shallow_v1_difficulty`。 | 验证 Lean adapter、checker、merge/root recheck、artifact evidence 和 fault/worker/ablation 基础机制。 |
| medium | 用户要求的递归 lemma-DAG：一个 root theorem 由多个 lemma 推出，每个 lemma 又可由多个 sublemma 推出，形成 2-3 层 dependency DAG。 | catalog 显式给出 lemma graph、dependency edges、expected depth、leaf proof units、merge slots、root theorem payload、oracle proof package 和 environment digest；拆分仍由 Lean 插件/确定性 catalog 规则决定，AI 只证明被分派的 proof unit。 | 支撑 TokenShare 的递归任务拆分、分派、验证、合并和 replay 主张；正式 Experiment 1/2/4 的 Lean 主张必须至少包含这一层。 |
| hard / frontier | 更复杂的混合题：多层 lemma-DAG、induction/rewrite/theorem reuse、跨主题组合，或接近人类研究难度的 frontier stress case。 | 如果 theorem 已有固定 oracle proof，必须 preflight 通过后才能作为 `paper_eligible` proof case；如果题目本身可能未解或人类也不一定能完成，只能作为 `frontier_stress` / `structured_blocked` / negative case，不能伪造成 Lean checker success。 | 观察系统在复杂/不可解任务下的 structured failure、budget、worker recovery、ablation 和 evidence 边界；不能把未解 conjecture 的失败算作协议失败。 |

medium lemma-DAG / hard-frontier catalog 每行至少包含：`case_id,paper_difficulty,root_theorem_payload,lemma_graph,dependency_edges,expected_depth,expected_leaf_count,expected_ai_unit_count,merge_plan_shape,oracle_proof_package_ref,environment_digest,preflight_status`。catalog freeze 前必须运行本地 Lean preflight，确保所有纳入 `paper_eligible` 的 theorem / lemma / root assembly 都可由固定 Lean/lake/toolchain/library 环境验证；AI 是否能找到 proof 不能作为纳入/排除条件，避免按结果挑题。

为了支持 medium lemma-DAG / hard-frontier，Lean 插件能力也必须随 catalog 提升：读取并校验预注册递归 lemma graph、生成 split certificate、执行 proof-file assembly、per-lemma checker evidence、multi-level merge/root recheck 和 dependency-aware slot integrity。固定 plan 可以由 catalog/生成脚本预先给出 implication introduction、forall introduction、nested conjunction/iff、induction/rewrite skeleton；本阶段不要求运行时从任意 theorem 自动发现这些 lemma。仍然禁止让 AI 决定协议级拆分；AI 输出只能作为 proof candidate，经 parser/checker 后进入 evidence。

### Lean catalog 题型矩阵目标

2026-07-15 起，正式 Lean catalog 不只区分三档难度，还必须覆盖三类经过设计的题型。正式 catalog 使用 3 × 3 矩阵：`paper_difficulty` 为 `simple`、`medium_lemma_dag`、`hard_frontier`；`topic_family` 为 `pure_logic`、`function_set`、`induction`。

| `topic_family` | simple 目标 | medium_lemma_dag 目标 | hard_frontier 目标 |
|:---|:---|:---|:---|
| `pure_logic` | 当前 shallow `P ∧ Q` / `P ↔ Q` 可作为过渡样本，但正式 simple pool 应记录题型 metadata。 | 多层命题逻辑 lemma-DAG，例如 root 依赖 intermediate lemma，再依赖多个 leaf/sublemma proof units。 | 更深的量词、等价、rewrite 或 theorem reuse 组合；无 oracle 时只能 blocked / stress。 |
| `function_set` | 函数、集合、子集、像/原像或简单单调性相关 theorem，证明结构浅但不是纯 `P ∧ Q`。 | 函数与集合 theorem 的 2-3 层 lemma-DAG，例如先证明函数分段性质、集合刻画，再证明 root subset。 | 混合实数函数、集合构造、分类讨论、rewrite 和外部库 theorem reuse；必须先做 oracle feasibility review。 |
| `induction` | Nat/List 上的简单归纳或递归定义 theorem，root proof 可以由一个短 induction skeleton 完成。 | 一个 root theorem 依赖多个归纳/辅助 lemma，形成 recursive lemma-DAG，AI 只证明分派 proof unit。 | 嵌套归纳、互相依赖 rewrite、theorem reuse 或较长 proof-file assembly；无固定 oracle proof 时不能标 checker success。 |

用户最新决策是：当前正式实验目标要覆盖上述 Lean 3 × 3 题型矩阵，而不是把它只当远期 catalog pool。每个 `(paper_difficulty, topic_family)` 单元固定为 **恰好 15 道** checker-backed、可审计 case，完整 Lean 正式 catalog 因而固定为 135 道。扩大后的 suite version、构造/抽样规则、预算审批和输出表格必须显式记录，不得静默沿用旧 P0 口径，也不得用当前 shallow v1 Lean case 补齐 medium / hard。

该决策要求题库和执行系统分别给出证据：每个题型/难度有 checker-backed golden case；`topic_family` schema、预注册 fixed plan、Lean plugin validation 和 fixed oracle package 的责任边界明确；recursive lemma-DAG certificate、proof-file assembly、dependency-aware merge/root recheck、preflight 时间和真实 AI provider 预算可审计。2026-07-22 Task 5 已完成 fixed-plan/runtime FULL 主路径，2026-07-23 Task 6-9 又完成 worker/fault/ablation hooks、paper projection 与双领域系统验收：catalog 只提供预注册数据，Lean 插件校验 fixed plan 并生成 certificate，system coordinator/`ProtocolEngine` 推进 FULL 生命周期，paper adapter 从 ledger/artifacts 投影兼容结果。该实现不是通用自动 lemma discovery。

扩展 schema 必须显式记录 `topic_family`、`topic_family_version`、`construction_rule_id`、`matrix_cell_id`，并继续要求 `environment_digest`、`preflight_status`、oracle package hash 和 checker evidence。catalog manifest 必须证明九个 cell 的 `case_count` 均为 15；多一个、少一个、重复 case 或 preflight 不通过都使正式 catalog freeze 失败。`lean_catalog.v1.jsonl` 仍只算 simple/shallow 输入；当前正式 135 道 selection 由 `lean_lemma_graph_catalog.v1.jsonl` 的 pool 和 `lean_task14_3x3_readiness.v1.json` 冻结，后续不得在 runtime 迁移时改写这些 case 的预注册拆分图。

`Doc/TechnicalDocument/2026-07-15-feat-011-lean-tiered-topic-catalog-review-prompt.md` 保留为题库构造和语义复核的历史 provenance；当前 runtime 迁移的实现顺序以 2026-07-22 迁移计划为准。

# Experiment 1: 真实 AI 跨领域可行性与难度

## 为什么需要

论文首先必须证明 TokenShare 不是只在一个 toy task 上工作。factorization 和 Lean 分别代表可枚举算术搜索与形式化证明；两者共享协议 lifecycle，但 split、parser、verifier/checker 和 merge 都属于插件。难度分层用于避免所有输入都成功而没有信息量。

## 设计

| 项目 | 固定值 |
|:---|:---|
| domains | `factorization`, `lean_proof` |
| paper difficulty | factorization 使用 easy / medium / hard；Lean 使用 simple / medium lemma-DAG / hard-frontier 三档 |
| tasks | Factorization easy/medium/hard=`167/167/166`，共 500 个；Lean 每个 `(paper_difficulty,topic_family)` 15 个，共 135 个；合计 635 个唯一 root tasks。若任一 Lean cell 未达到 15 个 checker-backed cases，正式 Experiment 1 不得启动，不得用其他 cell 或 shallow v1 补齐 |
| repeats | 论文 run 的 Factorization 500 题和 Lean 135 题均每题 1 次；pilot 仍只跑 1 次且不进入主表。后续 EPD-003 把 Experiment 2 设为每个 worker level 2 次，EPD-004 把 Experiment 3 所有 condition 设为 2 次；Experiment 4–5 不受 EPD-001 影响。 |
| worker count | 固定 10；若 provider preflight 不允许 10，并发改为可用上限且整个实验保持一致 |
| model | 固定官方 DeepSeek `deepseek-v4-pro`，entry id `deepseek_v4_pro_exp1_baseline`，thinking enabled、`reasoning_effort=high`、`timeout_seconds=600`、`max_tokens=300000`，并省略 `temperature/top_p`；不得自动替换模型 |
| fault/ablation | none / FULL |

## 程序必须输出

逐 task 输出：completion、accepted validity、parser/checker status、split/child/merge 数、attempt/provider-attempt 数、wall-clock、provider latency sum、prompt/completion/total tokens、cost、failure stage、event/artifact refs。逐 `(domain,paper_difficulty,topic_family)` 输出 case count、completion rate、accepted validity、median/P90 wall-clock、median/P90 tokens、cost per completed task 和 failure breakdown；Factorization 的 `topic_family=not_applicable`。另外输出 `highest_observed_valid_completion_difficulty`：对每个 domain/topic family，取至少出现一个 `completion=true && accepted_validity=true` 的最高已测试难度；它只表示观测上界，不替代完整成功率和置信区间。

## 可写入论文的结果

可以写两个领域在不同难度下的完成率与成本；可以写 Lean checker 或 factorization verifier 拒绝了哪些候选；可以写能力边界随难度下降。不得仅写“accepted outputs 100% correct”而不报告未完成任务，也不得把模型求解失败解释为协议状态污染。

# Experiment 2: 真实 AI worker 扩展性

## 为什么需要

TokenShare 的关键价值主张之一是把可拆任务分派给多个 worker。必须用固定任务和真实 API 测量增加 worker 是否缩短端到端时间，以及收益何时被 provider rate limit、任务粒度、merge gate 或调度开销抵消。

## 设计

Experiment 2 仅保留 Factorization，Lean 固定 lemma-DAG 不进入 worker scaling。冻结题库是正式 Factorization catalog 的全部 166 个 hard roots，原始类型分布为 `early=53`、`middle=53`、`late=53`、`no_factor=7`；不得只挑必须执行全部区间的 no-factor roots，也不得按运行结果事后删题。

每个 Exp2 root 使用插件拥有的 `factorization.exp2_contiguous_20way.v1` split profile：候选域仍严格覆盖 `[2, floor_sqrt(target_n)]`，但 `requested_child_count=20`。实验只提交该 profile 和 catalog case；连续区间边界、coverage proof、task graph child、merge slot 和 completion 仍由 Factorization 插件与系统 runtime 生成，不得由 runner 复制拆分算法。该 profile 只适用于 Experiment 2，不修改 Experiment 1/3/4/5 对同一 catalog case 的冻结 split 参数。协议 run config 必须允许至少 20 个直接 child，并把 profile identity、20-way partition digest 和 case selection digest 写入 plan/evidence。

强制 worker levels 为 `1, 3, 7, 10, 30, 50`，不再保留 `100/300` 扩展点。每个 worker level 对完全相同、顺序一致的 166 roots 运行 2 遍；comparison group 固定 catalog digest、case IDs/order、split profile、官方 DeepSeek `deepseek-v4-pro` baseline、prompt、timeout 和两成员 seed family。正式矩阵共 `166 × 6 × 2 = 1,992` root-runs；每个 root 计划 20 个 AI units，因此首次 attempt 预算基数为 `1,992 × 20 = 39,840` planned AI units。该数是无早停、无 replacement 时的计划上限，不是实际 provider-call 报告。`worker_count` 控制协议 worker 并行度，`provider_inflight_limit=50` 控制 provider 请求并发，runner 不得用更小的本地 semaphore 静默截断 30/50 档。

Factor witness 早停必须保留：verifier 接受一个 canonical factor witness 后，插件可以打开 merge gate，系统停止调度尚未发出的 sibling；已经组成 worker batch 并发给 provider 的请求不追溯取消。159 个 semiprime roots 可能自然早停，7 个 no-factor roots 必须覆盖全部 20 个 range children。更高 worker 既可能降低 witness latency，也可能在 witness 返回前增加 speculative provider calls；这是被测系统的 latency/cost trade-off，不是需要消除的混杂。

worker 30 和 50 面对单 root 最多都只有 20 个可运行 range children；不得把线程配置值 30/50 报成实际并行执行了 30/50 个 AI units。二者用于检验超过任务图宽度后 wall-clock、吞吐和利用率是否进入平台期。所有 root 仍由 formal runner 顺序观察，worker count 只控制同一 root 内的 range-child 并行，不得用多个独立整数并行冒充单 root 扩展性。

两遍的 condition-level 原始值必须全部保留，并报告两值的 min/max 和相对差；不得把 `n=2` 的 IQR 当作稳定性证据。逐 root 可在相同 `case_id × repeat_id` 上与 worker=1 做 paired speedup，再跨 166 roots 汇总 median/P90 和 factor-position 分层结果。

## 输出与公式

``` math
S(w)=\frac{T(1)}{T(w)},\qquad
E(w)=\frac{S(w)}{w},\qquad
Q(w)=\frac{\text{completed roots}}{\text{wall-clock seconds}}
```

程序输出至少包含 `worker_count,repeat_id,case_id,factor_position_quantile,task_batch_id,planned_ai_unit_count,executed_ai_unit_count,early_stop_unscheduled_count,in_flight_after_witness_count,observed_peak_concurrency,wall_clock_ms,critical_path_ms,provider_latency_sum_ms,throughput,speedup,parallel_efficiency,worker_utilization,provider_attempt_count,total_tokens,cost,completion_rate,http_429_count,retry_count`。`critical_path_ms` 必须从同一 root / task batch 的 task-attempt 时间戳和 merge gate 依赖关系复算，不能用 provider latency sum 替代。`executed_ai_unit_count + early_stop_unscheduled_count` 在无 selected-unit/terminal failure 的正常 root 中必须等于 20；worker 30/50 的 `observed_peak_concurrency` 不得超过 20。若出现 provider 429/限流，必须同时给出包含限流的 end-to-end 曲线和去除限流 run 的敏感性分析，不能把外部 API 限流声称为协议本身不可扩展。

## 2026-07-24 Exp2 实现与指标接线审计

本次沿正式 CLI 的实际代码路径检查了 `paper_exp2_scalability.py`、`paper_formal_runner.py`、`factorization_paper_adapter.py`、`local_runtime/coordinator.py`、`local_runtime/workers.py`、`paper_projection.py` 和 `paper_formal_metrics.py`。结论不是“Exp2 完全没实现”，而是“真实并发执行底座已经存在，但 EPD-003 行为和可用于论文的扩展性计时/利用率证据尚未实现完整”。

### 已真实实现的行为

- `condition.worker_count > 1` 时，Factorization adapter 确实创建 `ThreadWorkerBackend(capacity=worker_count)`；coordinator 确实通过系统 scheduler、lease、attempt、executor、verification 和 canonical 路径成批执行同一 root 的 range children。worker 数不是由 runner 伪造的标签。
- `ThreadWorkerBackend` 确实使用线程池并记录真实 `started_at/ended_at/worker_id/execution_index`。真实 provider usage、provider latency、raw/provenance 和 attempt evidence 也会持久化。
- worker 只控制单 root 内并发；formal callback 外层顺序运行 roots，没有用并行跑多个整数冒充单 root 扩展性。

### 尚未真实实现或当前实现不符合 EPD-003 的行为

| 项目 | 当前代码事实 | 必须修复为 |
|:---|:---|:---|
| 正式矩阵 | `paper_exp2_scalability.py` 仍展开 Factorization+Lean、三档难度、worker=`1/3/10/30`、5 repeats，常量仍为 10,300 roots。 | 只展开 hard Factorization 166 roots、worker=`1/3/7/10/30/50`、2 repeats，共 1,992 roots；Lean 和 100/300 extension 不进入正式 Exp2。 |
| 20-way split | selection 直接复用 catalog case 原有 split params 和 AI-unit count；没有注入或校验 `factorization.exp2_contiguous_20way.v1`。 | profile 定义和 20 个连续区间的实际生成放在 Factorization 插件；Exp2 只选择 profile id。plan/evidence 必须证明每个正常 root 有 20 个 planned range children 和完整 coverage。 |
| factor witness 早停 | 插件能判定一个 verified canonical witness 已足以满足 merge readiness，但 coordinator 在检查 merge readiness 之前会继续调度所有仍为 Ready 的 sibling；`descriptor.py` 也仍把 sibling pruning 标为 deferred。因此当前通常会把所有区间都发完，并不存在 EPD-003 所写的“停止发送尚未调度 sibling”。 | coordinator 在当前已发 batch 全部收束后、创建下一批 lease/request 之前先检查 plugin merge readiness。若 witness 已满足，停止调度后续 Ready sibling；不取消已经发出的同批请求。记录 witness 时刻、仍在途数和未调度 sibling 数。 |
| worker 30/50 上限 | 真实 backend 会受 ready unit 数约束，但当前每题仍主要是 2/4/8 children，因此不能用它证明 20-way 的 30/50 平台期。 | 先实现 20-way，再以实际 `observed_peak_concurrency <= 20` 证明任务图宽度上限；配置 worker_count 不能代替观测并发。 |

### 当前正式指标为什么还不可用

- adapters 给 coordinator 和 submission 注入固定 `NOW`；paper projection 的 `wall_clock_ms` 又从这些协议 event 时间戳计算。formal CSV 因此不能把该值当成真实并发墙钟。
- thread/process backend 已有真实 execution facts，但 normal Exp2 不把这些 facts 写进通用 runtime result/projection；当前只有 worker-death 特殊路径消费它们。正式证据中无法复算实际并发区间、峰值并发和 worker utilization。
- `paper_formal_metrics._exp2_rows()` 只用 condition wall-clock 算 throughput/speedup/efficiency，没有输出 planned/executed/early-stop-unscheduled/in-flight/observed-peak/utilization，也没有按 `case_id × repeat_id` 做 worker=1 paired speedup，或汇总两遍 min/max/relative difference。
- `_critical_path_ms()` 只查找当前 ledger 中并不存在的 `AI_UNIT_ENDED` / `MERGE_GATE_COMPLETED` duration 事件，找不到就退回上述 wall-clock；不能证明依赖关键路径。
- `paper_exp2_scalability.summarize_exp2_scalability()` 虽然声明并校验了更完整的 worker/timing/critical-path 结构，但生产 CLI 的 `recompute_paper_formal_metrics()` 没有调用它，而且 formal evidence producer 也没有生成它要求的 `batch_started_at_ms/batch_ended_at_ms` 与逐 unit/gate 时间结构。函数存在不等于指标已接线。

### Exp2 必须完成的修复清单

1.  在 `paper_exp2_scalability.py` 同步 EPD-003 condition、selection、常量和 root-run 校验；在 Factorization 插件的 split strategy/runtime adapter 内新增并校验 20-way profile，paper adapter 只传 profile id。
2.  在 `local_runtime/coordinator.py` 把“witness readiness 检查”放到下一批 sibling 调度之前，实现“不取消已发 batch、停止未发 sibling”的真实早停；在通用 runtime result/projection 中持久化 planned、dispatched、completed、in-flight-at-witness 和 unscheduled unit facts。
3.  formal run 使用真实 monotonic/UTC execution clock；测试可继续注入 deterministic clock。把 worker backend 的真实 execution facts 纳入通用 system runtime evidence，不做 Exp2-only synthetic 时间。
4.  收敛正式指标路径：`recompute_paper_formal_metrics()` 必须消费上述真实 facts，并复用或取代 `summarize_exp2_scalability()`；不得继续维护一个“完整但未接线”的 summarizer 和一个“已接线但字段不足”的 `_exp2_rows()`。
5.  `paper_plot_scalability.csv` 同时输出逐 root 原始行、逐 repeat condition 行和两遍汇总；至少实现本节冻结字段、paired speedup、min/max/relative difference、factor-position 分层及 429 敏感性字段。缺少真实 timing/concurrency evidence 时 condition 必须 `paper_eligible=false`，不能回退到固定时间或 provider latency sum。

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

Rate-fault 矩阵只覆盖 5 类非死亡故障：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。Factorization 实际 fault rates 使用 `1%, 5%, 10%, 25%, 50%, 100%`；Lean 使用 `10%, 50%, 100%`，其 3-task slice 固定为 `pure_logic/function_set/induction` 各 1 道。0% 不再是 Exp3 condition，而是从正式 Exp1 的同 `case_id` root 投影 `shared_exp1_reference/shared_reference` 行。故障目标用固定 seed 从 AI units 中选择，实际 target ids 写入 manifest。每个 condition 重复 2 次。所有原始和恢复 AI attempts 固定使用 `deepseek_v4_pro_exp1_baseline`，故障不得触发模型升级或 provider 切换。

worker death 固定 `worker_count=10`，分别终止 1 个和 3 个 executor worker processes，并按任务进度 25%、50%、75% 三个位置注入；每个 domain、每个死亡数量、每个位置重复 2 次。`dead_worker_count` 统计实际被终止的不同操作系统进程，不要求这些进程对应互不相同的逻辑 AI unit：若一个 root 的可选逻辑 unit 少于 3 个，冻结 manifest 仍只列唯一逻辑 target，后续死亡可落在这些 unit 的 replacement process 上。每次死亡都必须有独立 process/attempt fact、lease expiry/recovery 事件和最终成功 replacement；不得把重复逻辑 unit 或重复 PID 伪装成多次死亡。这里终止的是 executor worker，不是 coordinator；因此可以在 Phase 9 完整 replay 之前测试 lease/reassignment。若要测试 coordinator crash/restart，必须等待 state replay 可重建后另立实验，不得混写。

2026-07-28 负向终态补充：上句“最终成功 replacement”是 `tokenshare.paper_worker_death.v1` 成功恢复记录和 paper-eligible worker-death 结果的必要条件，不允许 adapter 因 replacement 未完成而抛出异常并中断整个 suite。若真实 executor process 已由 runtime 终止，ledger 随后以 `retry_allowed=false` 的 recovery action（例如 `retry_limit_reached`）进入终态，但没有成功 replacement，实验层必须把已有 process fact、dead attempt、replacement attempts、terminal recovery event、artifact refs 与实际 kill progress 原样投影为 `tokenshare.paper_worker_death_incomplete.v1`，并显式写 `recovery_completed=false`、`evidence_complete=false`、`replacement_fact=null`。该记录计入真实死亡与失败分母，但其 root/condition 为失败或 `completed_with_failures`，始终 paper-ineligible，不得进入“成功恢复”分子；runner 仍须闭合 run/condition/experiment/suite manifest、生成 metrics/audit/smoke reports 并继续后续预注册条件（包括 Exp4）。`tokenshare.paper_attempt_result.v2` 不是通用 failed-runtime fallback：它只允许 `condition.experiment_id=exp3_real_ai_fault_recovery`，且每个已持久化 `ExecutionRequest` 必须精确标识 `executor_id=executor_factorization_runtime`、`executor_type=deterministic_local`；production 入口和投影 helper 必须分别审计这两个条件。满足 allowlist 的 provider 前失败写 `attempt_status=executor_error`、真实 `executor_id/executor_type/request_ref`、`provider/model/entry_id=null`、`provider_attempt_count/tokens/cost=0`、空 raw/usage/provenance/model-record refs并固定 paper-ineligible；它属于 request-stage/internal executor failure，不计 provider call 或 provider failure。Experiment 1/2/4/5 或任何非预期 executor 必须在创建 v2 row/checkpoint 前 fail closed，不得退回 v1/provider_error；所有真实 provider-dispatched attempts（尤其 Experiment 5）继续使用 `tokenshare.paper_attempt_result.v1`，其 schema 与 endpoint 指标语义不变。缺少真实 config identity 或协议证据仍在调用前 fail closed，配置/identity `ValueError` 不得转换为 condition failure 或继续后续 condition。此补充不改变 event/ledger schema，也不允许 catch 任意配置、身份或 invariant 错误。

Experiment 3 每个 condition 的两遍原始结果必须全部保留，并报告 min/max 和相对差；不得把 `n=2` 的 IQR 当作稳定性证据。两遍都引用同一个正式 Exp1 `repeat_id=0,seed=1,worker_count=10` 的同 `case_id` evidence；这是共享 reference，不得描述为 same-repeat/paired baseline。兼容字段可保留 `matched_baseline_*`，但必须同时冻结 `comparison_kind=shared_reference`、source suite/run/generation/condition/task/repeat/seed、content hash 和 source usage。

## 双轴终态与 suite continuation

每个 root 同时冻结 `outcome_status ∈ {succeeded,failed_experimental,blocked_dependency}` 与 `evidence_integrity ∈ {complete,missing,invalid,corrupt}`。parser/checker/provider/injected-fault/worker-death 的可信失败是 `failed_experimental + complete`，必须闭合 root/condition 并继续剩余预注册条件；所有 roots 有可信终态而存在失败时 suite 为 `completed_with_failures`。配置、catalog、selection、budget、output、模型/request identity 或 evidence hash/schema 漂移是 `blocked_dependency`，停止新 API 调用，但尽最大可能把未开始 roots 明写为 `not_started` 并闭合 experiment/suite manifest。`incomplete` 只用于进程/机器中断或终态持久化失败。

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

- `wall_clock_overhead_ms = fault_condition_wall_clock_ms - shared_exp1_reference_wall_clock_ms`；`wall_clock_overhead_ratio = wall_clock_overhead_ms / shared_exp1_reference_wall_clock_ms`。

- `token_overhead = total_actual_tokens - shared_exp1_reference_total_tokens`；`token_overhead_ratio = token_overhead / shared_exp1_reference_total_tokens`。`wasted_actual_tokens` 只累计因注入、死亡、late/no-return 或 rejected recovery 而无法贡献到最终 accepted result 的真实 provider usage，不与 `token_overhead` 混用。

- `cost_overhead = (fault_condition_cost - shared_exp1_reference_cost) / shared_exp1_reference_cost`。

- `result_completeness_rate = recovered_slot_count / required_slot_count`；`root_output_complete = (recovered_slot_count == required_slot_count)`。它必须和 deterministic `accepted_validity` 同时报告：slot 齐全但 checker/verifier 不通过，仍不是完整有效结果。

所有 shared reference 必须与 fault condition 使用相同 `case_id`、catalog/split/plugin/parser/verifier/executor identity、DeepSeek-V4-Pro entry 和 request limits；reference 固定来自 Exp1 的 `repeat_id=0,seed=1,worker_count=10`，不要求等于 Exp3 fault repeat/seed。Exp1 root 有完整失败终态时 Exp3 仍执行，但 `baseline_comparison_eligible=false`，wall-clock/token/cost delta 均写 `null` 并记录 `source_exp1_failed_experimental`。任何比率分母为 0 时也写 `null` 并记录 `zero_baseline_denominator`，不得输出 `Infinity`、`NaN` 或改用其他 condition。

`false_positive` 和 `late_submission` 默认是 detect-and-isolate；`no_return`、`executor_error` 和 `worker_death` 默认是 recoverable；`false_negative` 必须按插件能否从后续 merge / checker 发现缺失事实记录为 `recoverable` 或 `detect_only`，不能硬写为成功恢复。

## 2026-07-24 Exp3 实现与指标接线审计

> 本节保留 2026-07-24 修复前的代码审计 provenance；其中“当前”“尚未”、same-repeat matched baseline、dedicated no-kill baseline 和 blocker 清单都不是当前要求。2026-07-29 起统一由上文 `shared_exp1_reference`、双轴终态以及 EPD-012 覆盖，不能作为当前实现状态读取。

本次沿 `paper_faults.py`、`paper_exp3_fault_recovery.py`、`paper_formal_runner.py`、两类 paper adapter、`local_runtime/workers.py`、`paper_workers.py` 和 `paper_formal_metrics.py` 检查真实执行链。结论是：五类 rate-fault 的注入点和协议恢复大体是真实的，worker process death 与 lease/reassignment 也是真实的；但重复矩阵、false-negative applicability、kill progress、matched baseline 和正式汇总存在 blocker。

### 已真实实现的行为

- 五类 rate-fault 都由 `PaperFaultRuntimeHooks.after_raw_output_persisted()` 在真实 raw/provenance/usage artifact 已保存之后触发；no-return/late/executor-error 的实际 hook stage 与“raw 后、submission/parser bridge 前”的设计大体一致。false positive/negative 也确实改写了随后进入 parser/verifier 的真实内容，但当前 stage 仍有下表所述偏差。它们都不是 runner 在任务结束后伪造失败行。
- rate-fault run 的协议 config 允许 replacement；rejected/expired/error attempt 后由 coordinator/engine 建立新的 lease/attempt，formal runner 只投影同一 protocol unit 上实际存在的 replacement attempts，没有再次私自调用 adapter。
- worker-death 使用独立 OS process backend；被终止 attempt、非零 exit code、lease expiry、superseded attempt、Ready recovery、不同 PID 的 replacement 和 coordinator 存活事实均有真实系统证据。

### 尚未真实实现或语义不完整的行为

| 项目 | 当前代码事实 | 必须修复为 |
|:---|:---|:---|
| repeats | `REPEAT_IDS=(0,1,2)`，root-run 常量仍为 61,734。 | EPD-004 的两个 repeat，root-runs=41,156，并重新生成 condition/budget identity。 |
| false-positive/negative 注入点 | 两者实际都在 `after_raw_output_persisted()`、正式 domain parser 之前运行；hook 仅自行 `json.loads` raw 内容，却把 record 的 `injection_point` 写成 `after_parsed_candidate_before_verification`。 | 在 system runtime/plugin execution bridge 增加真实的 parsed-candidate-after-parser/before-verifier hook，传入 domain parser 产物和 artifact ref；false-positive/negative 只能在此处变换。record stage 必须来自实际 hook，不得写预期名称冒充。 |
| false-negative applicability | target manifest 在 provider 输出前从全部 planned AI units 选 target；若目标原输出没有 factor/proof candidate，mutation 直接抛 `ValueError`。Factorization 的大量 no-factor range 天然不可适用。 | 为 false-negative 冻结 deterministic reserve order；hook 只对实际含候选的原输出注入，不适用 target 记录 `not_applicable` 后按冻结顺序补位，或在预检可证明的 eligible pool 内选取。分别记录 candidate/eligible/injected 数，禁止抛错后把它混成 executor failure。 |
| fault 结果分类 | primitive record 在注入时把 `canonical_pollution=False` 预填，runtime record 又给所有 fault 写 `requires_replacement=True`，与各 fault 的真实检测/恢复语义不一致。 | 注入 record 只记录“注入了什么”；run 结束后从 verification、late rejection、canonical selection、recovery attempt 和 root completion events 投影 `detected/wrongly_canonicalized/recoverable/recovered`。 |
| worker kill progress | manifest 按 planned unit 序号选择 25/50/75% 附近的 target；process backend 对 target 在子进程已产出 submission 后立即 terminate，三个百分比没有控制“已完成工作达到该比例才 kill”。`progress_before_kill` 直接复制目标百分比，另一个 observed 值又按 execution index 而非真实完成时间计算。 | 用真实 completed-unit event/fact 驱动 kill gate；达到目标阈值后才武装并终止下一个符合条件、已持久化 raw 但未提交的 worker。分别保存 target、kill 时真实 completed/total、时间戳和误差；25/50/75 必须形成可验证的三个注入时点。 |
| worker-death matched baseline | manifest 声明 `additional_execution_required=true` 的 dedicated baseline，但没有生产代码执行它。formal runner 反而把当前 worker-death outcome 自己的 attempts/tokens/cost/wall-clock 写成 `matched_baseline_*` fallback。 | 对每个 worker-death condition 实际执行同 case/repeat/seed/worker/request limits、仅不 kill 的 baseline，持久化独立 condition evidence；故障 run 只引用该 baseline，禁止 self-baseline。 |

### 当前正式指标为什么还不可用

- `paper_formal_metrics._exp3_rows()` 把 `detected` 定义为 `fault.detected is True OR canonical_pollution is False`；由于 fault record 预填 `canonical_pollution=False`，当前 `detection_rate` 会被结构性推向 1，`false_accept_rate` 会被结构性推向 0，而不是从 canonical evidence 观察得到。
- recovery 只统计 event type 精确等于 `REPLACEMENT_ACCEPTED`，但真实 formal 路径写的是协议 attempt/canonical 事件和 `EXPERIMENT_FAULT_OBSERVED`，并不生产该事件名；因此真实 replacement 存在时也可能被报成未恢复。
- 所有 fault 又被统一写 `requires_replacement=True`，导致 recovery-rate 分母混入 detect-only/not-applicable 项。
- dedicated worker-death baseline 没执行，而 self-baseline fallback 使 wall-clock/token/cost overhead 天然为 0 或接近 0。
- `canonical_pollution`、`wasted_actual_tokens`、`recovery_latency_ms`、reassignment、result completeness、kill-progress error 和两遍 min/max/relative difference 没有全部接入正式 `paper_table_fault_recovery.csv`。
- `paper_exp3_fault_recovery.summarize_exp3()` 有更严格的字段和校验，但生产 formal CSV 没有调用它；它甚至要求所有 fault record 的 `canonical_pollution` 必须预先为 false，因此也不能直接作为“观察消融/故障逃逸”的最终实现。
- Exp2 所述固定协议时间同样会污染 Exp3 的 recovery latency 和 matched wall-clock overhead；修复必须共用真实 system runtime timing evidence。

### Exp3 必须完成的修复清单

1.  在 `paper_exp3_fault_recovery.py` 同步 EPD-004 的 repeats、condition/root-run 常量、selection、预算和 baseline manifest。
2.  在 system runtime/plugin execution bridge 增加正式 parser 后、verifier 前的 candidate hook，把 false-positive/negative 从 raw hook 移到该边界；在 `paper_faults.py` 增加 fault applicability/reserve-target 契约。注入 record 不再伪报 stage，也不预判 detection、canonical pollution 或 recovery outcome。false-negative 无候选时结构化跳过/补位，不得异常中断 condition。
3.  在 system runtime projection 与 `paper_formal_runner.py` 的 Exp3 投影中，从真实 verification/canonical/recovery/attempt/completion 事实生成逐 target outcome；recovery success 必须绑定 replacement attempt 被协议接受的 event refs。
4.  在 `local_runtime/workers.py` / `paper_workers.py` 把 kill progress 改成真实进度触发，并用完成时间而非 execution index 复算 observed progress；保留真实 process death 与 lease expiry 语义。
5.  formal runner 执行并持久化 dedicated worker-death no-fault baselines；metrics 只接受真实匹配 condition evidence，不接受 self-baseline 或 synthetic snapshot。
6.  收敛 `paper_formal_metrics._exp3_rows()` 与 `summarize_exp3()`；正式 CSV 必须输出本节全部 rate/overhead/completeness 字段、逐 target 分母/applicability、两遍原始值及 min/max/relative difference。缺 baseline、真实 timing 或 canonical/recovery refs 时标记 ineligible，不得填 0。

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

Factorization 三档分别使用全部 `167/167/166` roots；Lean 每档按预注册的 2/2/1 分层固定 5-task slice 覆盖三个 topic families。`FULL + 4` 个消融模式均重复 3 次，并固定使用官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`。为让 `NO_REQUEUE` 与 FULL 形成可解释的唯一变量对照，Exp4 五个 mode 的 `ProtocolConfig.max_retries` 统一冻结为 `1`；`NO_REQUEUE` 只把 `replacement_attempts_allowed` 关闭。报告 completion、accepted validity、wrong canonical acceptance、raw-only exposure/acceptance、stuck task、premature merge、time、token 和 cost。消融条件由实验层选择，但必须通过 `tokenshare.local_runtime` 的稳定 hook / `ProtocolMechanismPolicy` 在真实生命周期 gate 注入；不得让 paper runner 事后改写结果，也不得修改协议 core 的默认 FULL 语义。

消融还必须输出 `exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability`。其中 `exposed_error_count` 是到达被关闭机制、且 FULL 模式本应拒绝或隔离的无效候选/不完整状态数量；`escaped_error_count` 是这些对象中继续进入 canonical、merge 或被错误标记为 terminal success 的数量；`error_escape_rate = escaped_error_count / exposed_error_count`。若某 mode 没有可适用的 gate（例如 NO_REQUEUE 主要观察 stuck/completion）或分母为 0，rate 写 `null`，并把 applicability 写为 `not_applicable` 或 `zero_denominator`，不得用 0 假装“没有逃逸”。

## 2026-07-24 实现与输出接线审计

当前真实 formal 输出路径是 `paper_formal_runner.py` 持久化逐 task/attempt 证据，再由 `paper_formal_metrics.py` 生成 `metrics/paper_table_ablation.csv`。另一个 `paper_exp4_ablation_runner.summarize_exp4_ablation()` 虽然声明了较完整的 rate/applicability/median/IQR 字段，但当前没有接入这条 formal CSV 路径，不能把“该函数存在”当作正式实验已经输出对应数据。

已真实接通并可从 evidence 复算的通用字段是：`completion_rate`、`accepted_validity_rate`、provider attempt 数、`wall_clock_ms`、`total_tokens`、`total_cost_estimate`、provider latency/error，以及逐 task/attempt/event/artifact 引用。以下 Exp4 专项字段在修复前仍不满足正式实验标准：

| mode | 当前已有运行事实 | 当前正式输出缺口 |
|:---|:---|:---|
| FULL | 通用 baseline 指标可输出。 | 需要与每个消融使用同一 case/repeat 做成对汇总。 |
| NO_VERIFICATION | runtime 确实能跳过 verifier，并保留最终 deterministic validity audit。 | `canonical_accepted_by_ablation` / `wrong_canonical_acceptance` 没有由真实 canonical event 生产；现有 applicability 反而查找被关闭后不再出现的 rejection status，可能把真实暴露记成 0。 |
| NO_PARSER_POLICY | runtime 能把 raw artifact 暴露到候选边界，逐 task 的 `ablation_runtime.raw_only_exposed` 可保留。 | 正式 CSV 统计的是“该 mode 被选中”的布尔标记，不是实际 raw exposure/canonical acceptance；缺少真实 `raw_only_acceptance_rate`。 |
| NO_REQUEUE | `ProtocolMechanismPolicy.replacement_attempts_allowed` 和 requeue hook 已存在。 | Exp4 当前 normal run 的 `ProtocolConfig.max_retries=0`，导致 FULL 本身也没有 replacement 机会；现有 `stuck_count` 只按 mode 标记计数，不能证明关闭 requeue 导致任务卡住。 |
| NO_MERGE_GATE | merge gate bypass hook 和未满足 readiness 的观察记录已存在。 | coordinator 在 gate 未满足时仍在调用 plugin merge 前停止；因此目前没有“真实 premature merge 被执行后”的 merge/root-check 结果。正式 CSV 的 `premature_merge_count` 也按 mode 标记计数，而非按实际尝试计数。 |

此外，当前 `paper_table_ablation.csv` 只有 `wrong_canonical_count/raw_only_count/stuck_count/premature_merge_count/exposed_error_count/escaped_error_count` 等 condition-level count，没有本规格要求的四种专项 rate、`error_escape_rate` 和 `error_escape_applicability`，也没有把 3 次 repeat 汇总为正式比较行。修复必须让每个数字从真实 runtime/canonical/recovery/merge evidence 派生；不得以 mode 常量、预期退化值或 synthetic task flag 代替观察结果。

### Exp4 必须完成的行为修复

1.  从 `paper_exp4_ablation_runner.py`、`paper_ablation.py`、formal condition validation、budget 和 tests 中删除 `NO_SLOT_INTEGRITY`，正式矩阵固定 5 modes / 90 conditions / 7,725 roots。
2.  `NO_VERIFICATION`：在 verifier bypass 后仍让真实 candidate 进入系统 canonical 路径，并在独立 deterministic audit 中复检；逐 task 保存 candidate、canonical event、audit validity 和 wrong-canonical binding。不能用“mode=NO_VERIFICATION”推定一定错误。
3.  `NO_PARSER_POLICY`：逐 attempt 保存 raw-only 是否实际暴露、是否进入 candidate/canonical、最终是否有效；没有 raw exposure 时 applicability 为 `zero_denominator`。
4.  `NO_REQUEUE`：FULL 与该 mode 的 `ProtocolConfig.max_retries` 都严格等于 `1`；唯一差异是 `replacement_attempts_allowed=false`。stuck 必须由真实 rejected/expired attempt 后没有 replacement、root 未终止完成的状态事实判定；自然没有 recovery need 时为不适用。
5.  `NO_MERGE_GATE`：在 `local_runtime` 的 ablation hook 路径真正调用插件级 incomplete/premature merge attempt，并持久化 attempt/result/root-check failure；不得伪造 required-slot canonical binding，也不得改变 FULL 默认 gate。当前“观察到 bypass 后立即 break”不算执行过 premature merge。

### Exp4 必须完成的指标修复

1.  每个专项 count/rate 都必须来自逐 task/attempt/canonical/recovery/merge evidence：`wrong_canonical_acceptance`、`raw_only_exposure/acceptance`、`stuck_task`、`premature_merge_attempt/failure`、`exposed_error`、`escaped_error`。
2.  `error_escape_applicability` 明确区分 `applicable`、`not_applicable`、`zero_denominator`；不得用 0 同时表示“没有暴露”和“暴露后无逃逸”。
3.  FULL 与 mode 按相同 `case_id × repeat_id` 配对；输出逐 task、逐 repeat condition 和 3-repeat 汇总，并保留 completion、validity、wall-clock、provider attempts、tokens 和 cost。
4.  收敛 `paper_formal_metrics._exp4_rows()` 与 `summarize_exp4_ablation()` 为一条生产路径；`paper_table_ablation.csv` 缺真实 event/artifact refs 或专项分母时必须拒绝 paper eligibility。

# Experiment 5: 四模型、同一 SiliconFlow endpoint comparison（次要）

## 为什么需要

四个具名 SiliconFlow 模型 entry 用于观察 TokenShare 在固定 provider 家族下的完成率、有效性、token、成本和延迟差异；不划分 strong/weak，也不实现 mixed routing。它不能替代前四个协议实验。单 provider 设计减少了跨 provider 混杂，但 SiliconFlow 的 serving profile、路由、限流、模型版本映射和计费仍是共同上游条件，所以论文只能报告“同 provider 下的模型端点比较”，不能把差异解释成模型本体的纯因果效应。

## 预注册 cohort

当前预注册 cohort 固定为 `tokenshare.paper.model_endpoint_cohort.v3`，tracked artifact 为 `benchmarks/paper/model_comparison_cohort.v3.json`：

| `cohort_member_id` | provider endpoint | provider model / reasoning profile | 公共请求上限 | 解释边界 |
|:---|:---|:---|---:|:---|
| `glm_5_2_siliconflow` | SiliconFlow | `zai-org/GLM-5.2`；thinking；`thinking_budget=32768` | 600 秒 / 32768 / 1 attempt | request、response identity 和 thinking control 必须匹配。 |
| `qwen3_14b_siliconflow` | SiliconFlow | `Qwen/Qwen3-14B`；thinking；`thinking_budget=32768` | 600 秒 / 32768 / 1 attempt | 与 GLM 共用 thinking contract，不允许静默降为 nonthinking。 |
| `minimax_m2_5_siliconflow` | SiliconFlow | `MiniMaxAI/MiniMax-M2.5`；thinking；`thinking_budget=32768` | 600 秒 / 32768 / 1 attempt | 实测即使发送 `enable_thinking=false` 仍产生 reasoning tokens，因此按实际端点行为冻结为 thinking。 |
| `deepseek_v3_pro_siliconflow` | SiliconFlow | `Pro/deepseek-ai/DeepSeek-V3`；nonthinking | 600 秒 / 32768 / 1 attempt | 这是 SiliconFlow entry，不得与 Experiment 1–4 官方 DeepSeek baseline 混用。 |

外部分数和价格只进入冻结的 cohort/provider snapshot 与论文背景表，不产生 strong/weak 标签，不决定 routing、paper eligibility 或 TokenShare 结论。正式 run 不联网刷新；模型、价格或端点变更只能生成新 version。v3 cohort digest 为 `sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942`，safe provider config digest 为 `sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1`。

## 设计与输出

v3 只使用 EPD-010 冻结的 Exp5 专属分层确定性半量 hard-only selection：在 Factorization hard 与 Lean `hard_frontier` 的 `pure_logic/function_set/induction` 四个 stratum 内分别按 `sha256(case_id)` 升序取前 `ceil(n/2)`，得到 `83 + 8 + 8 + 8 = 107` roots/model-repeat。semantic selection digest 为 `sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490`。所有模型和 repeats 使用同一 case IDs/order；不得按模型输出、失败或成本换题。该 selection 不修改 Experiment 1–4 或共享 catalog。

四个 cohort member 各自作为 `model_policy="fixed_entry"` 的独立 condition，Factorization 与三个 Lean topic slices 各 1 个 condition/repeat，因此共有 `4 models × 3 repeats × 4 slices = 48` conditions、`107 × 4 × 3 = 1,284` root-runs。Factorization 83 roots 保持 8-way split，Lean 三个 stratum 的 8 roots 分别保持 catalog 固定 lemma-DAG，合计 9,888 个 planned first-attempt AI units；每 AI unit 仅 1 次 provider attempt。调度顺序固定为 repeat 0/1/2 的 `ABCD / BDAC / CADB`，sequence digest 为 `sha256:de9271732513d7d2ab20624d2949af3c9230b9adc2cd8db4b1f1ddb0d57b58cb`；四个 model arm 顺序执行，arm 内全局 `max_in_flight_global=3`。最坏 token ceiling 为 607,518,720。

cohort v1/v2 以及 v2 的三端点、36 conditions、1,899 roots、14,652 first-attempt units、`100/8192` 口径仅供历史 replay/provenance；不得再称为当前正式 cohort，也不得把 v2 approval/digest 复用于 v3。

输出契约同时覆盖机器可审计与论文消费：raw/normalized evidence 使用 JSON/JSONL；统计表使用 CSV；论文表使用 TeX；可复核摘要使用 Markdown；正式报告通过 TeX 生成 PDF；图表使用可追溯 SVG。核心字段包括 completion、accepted validity、tokens、cost estimate、latency、provider errors、recovery attempts、identity 和 `model_execution_records`。六个无序模型对都输出成对差异/置信区间所需行；任何缺失 evidence 都保留空值与 eligibility reason，不从显示字段补写。历史 schema 只读、不重写。

DeepSeek-V4-Pro 的 pricing snapshot 固定为 CNY：cached input ¥0.025 / 1M、uncached input ¥3 / 1M、output ¥6 / 1M。usage 有 cache hit/miss 明细时分项估算，缺少明细时按 uncached input 保守估算；未收到 usage 时写 `usage_missing`，不得按零 token 解释。本地值一律称 `cost_estimate`，不是实际账单。Experiment 5 的 CNY 与 USD estimate 必须分别汇总到 `cost_estimate_by_currency`；未预注册汇率前，混合币种的顶层 scalar 合计固定为不可用并标记 `mixed_currency_not_aggregated`。

正式 Experiment 5 preflight 必须验证四个 cohort member 的 provider config、key env、model id、reasoning profile、真实 capability evidence、8-root Exp5 smoke evidence、catalog/selection compatibility、请求限制、调度顺序、预算和输出契约。首次 8-root smoke 是产生该 smoke evidence 的 bootstrap 路径，启动前只校验 config/key/model/reasoning/catalog/selection/request/budget/输出身份，不要求事先已有 passed smoke evidence；正式矩阵 preflight 仍必须 fail closed 地要求该 evidence，二者不得再复用同一个门禁模式。`paper_smoke_profile.v3.json` 是 29-root 综合 profile；`paper_smoke_exp5_profile.v3.json` 是只含 Exp5 的独立 8-root profile，digest=`sha256:b44646d8b298200165abb73155304085c6800fab9ab363aa52483bd03556e61b`。两者均固定 `pilot_only/regression_only/paper_eligible=false`。2026-07-30 的五次最小 endpoint/key 诊断均返回 HTTP 200，其中 MiniMax 在显式 `enable_thinking=false` 时仍报告 87 个 reasoning tokens；这些诊断没有形成 artifact-backed capability/smoke evidence，8-root smoke 也尚未运行，所以正式 preflight 仍保持 `blocked`。可用单模型试跑也只能是 pilot，不能生成四模型论文表；该 blocked 不影响 Experiment 1–4 的主张。

2026-07-30 首次真实 Exp5-only bootstrap 的 `paper_smoke_exp5_v3_20260730_run01` 在 API 前因实现漂移失败：preflight 已冻结 `thinking_budget=32768`，formal runner 的重复字段投影却漏掉该字段；同时 CLI 未持久化启动异常，因而只留下空目录。修复后 preflight/runner 共用同一 reasoning-controls projector，且 pre-execution exception 会在未初始化 suite 时持久化 blocked/0-attempt evidence。无 API 的真实参数哨兵已到达 evidence initialize；随后两次顺序 Qwen diagnostic attempts 中第一次为远端 connection error，第二次成功并报告 397 total/303 reasoning tokens，secret scan 无命中。上述单模型 diagnostic 仍不是 passed 8-root smoke evidence；空 run01 不得 resume，下一次完整 smoke 必须使用全新 output root 和重新生成的 path-bound digests。

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

Reasoning normalization 规则：OpenAI `reasoning_effort` 缺失或 `None` 为 `default`，空字符串非法，非空字符串规范化后比较，GPT member 必须为 `high`；SiliconFlow cohort v2 的逻辑 profile 为 `thinking`，必须持久化 `enable_thinking=true`；DeepSeek 必须同时持久化 `thinking={"type":"enabled"}` 与 `reasoning_effort=high`。v1 cohort 的 SiliconFlow `default` / `enable_thinking=false` 只用于历史读取，不得套用到 v2。

首次执行、provider failure、replacement/retry 和 resume 必须复用 condition 已批准的 endpoint identity；不得重新从当前 config 仅按 entry 字符串解析。source config drift 在 provider call 前失败；单-entry prepared config 禁止 failover 到 sibling entry/model。Replay 只验证和读取历史 request/provenance/raw/usage/model-execution artifacts，provider calls 必须为 0，也不得重新读取当前 API key 或当前 config 来补写历史事实。v1 artifact 的外层混合 `model` 不作为 resolved evidence；不能重写已有历史 artifact。未知 schema 或 v2 内部 requested/resolved/status 与原始 response 不一致时必须 fail closed。

## 2026-07-24 Exp5 实现与指标接线审计

沿 `paper_exp5_model_comparison.py`、`paper_model_policy.py`、`paper_formal_runner.py`、两类 adapter 和 `paper_formal_metrics.py` 的正式代码路径检查后，结论是：三个 endpoint 的 provider config、fixed entry 和调用后 resolved-model 隔离已经真实实现；主要 blocker 在正式身份汇总、v2 evidence join、比较表、真实 timing/eligibility、公平控制变量和 selection 所有权。

### 已真实实现的行为

- formal runner 会按 condition 的 `provider_config_id + selected_entry_id` 加载并过滤单 entry config，并校验 provider/model/reasoning、cohort/source/endpoint digests。
- 两类 adapter 都会从真实 request/provenance/raw/usage 生成 `tokenshare.paper_model_execution_record.v2`。
- `missing_resolved_model` / `resolved_model_mismatch` 会让 adapter 把 attempt 变成 fatal identity failure、清除 candidate，并阻止该输出进入 verifier/checker/canonical。

### 五个必须直接修复的缺口

1.  `_apply_exp5_identity()` 当前只检查 attempt 展示字段后写入 `model_identity_audit="fixed_entry_match"`；`_exp5_rows()` 又接受该标签或 display provider/model/entry 作为 match。真实 v2 record 已记录 mismatch 时，正式 match rate 仍可能被报成 1。identity rate 必须只从持久化 v2 observed identity 计算。
2.  `build_exp5_model_execution_rows()` 已有严格 v2 join，但 formal CLI 没有调用；`_model_record_text()` 只复制简化 attempt 字段，并读取不存在的 `model_execution_ref`，而正式字段是 `model_execution_record_ref`。生产 JSONL 必须接入严格 join。
3.  当前没有 `paper_table_model_endpoint_comparison.csv`，也没有三次 repeat 的跨 endpoint paired/aggregate 表或 provider confounding 列。正式报告必须按 endpoint/domain/topic/repeat 输出 completion、validity、tokens、cost、真实 runtime wall-clock、provider latency/error/429、recovery 和 identity status，再生成 3-repeat aggregate。
4.  通用 `_condition_metrics()` 使用固定协议时间推导 wall-clock，并把单值当 P50/P95，且硬写 `paper_eligible=false`。Exp5 必须使用通用真实 runtime timing evidence，并从 task/attempt/identity coverage 传播 eligibility；缺证据时 fail closed，不能硬编码成功或失败。
5.  cohort preflight 逐 member 检查 config，但没有比较三个 endpoint 的公共 request controls。member plan 必须冻结并比较 `temperature/top_p/stream/timeout/max_tokens/request limits/max_provider_attempts` 及同 domain prompt/parser/plugin version；provider 专有 reasoning controls 单独保留和批准。

### EPD-006 对第六个 selection 缺口的处理

旧 Exp5 通过 `_shared_exp2_slice()` 复用旧 Exp2 题目选择，导致 Exp2 改成 hard Factorization-only/20-way 后会污染或破坏 Exp5。该耦合不再修补为“继续共享”，而是删除：Exp5 独立消费 Experiment 1 formal catalog execution view 的全部 hard roots，按本节 166+45 口径生成 36 conditions。Exp2 的 domain、20-way profile、worker levels 或 selection 以后变化，不得改变 Exp5 digest。

### 实施入口

完整文件归属、TDD 顺序、定向验证和少跑 Lean/不做攻击防护的硬约束见 `2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`。修复完成前不得启动正式 Exp5 1,899-root 三端点矩阵。

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
      "domain": "factorization",
      "difficulty": "hard",
      "paper_difficulty": "hard",
      "topic_family": "not_applicable",
      "worker_count": 10,
      "split_profile_id": "factorization.exp2_contiguous_20way.v1",
      "requested_child_count": 20,
      "fault_type": "none",
      "fault_rate": 0.0,
      "dead_worker_count": 0,
      "kill_progress": null,
      "matched_baseline_condition_id": null,
      "ablation_mode": "FULL",
      "model_policy": "fixed_entry",
      "model_cohort_id": null,
      "cohort_member_id": null,
      "provider_config_id": "exp1_baseline_deepseek",
      "model_entry_id": "deepseek_v4_pro_exp1_baseline",
      "provider_family": "deepseek",
      "provider_model_id": "deepseek-v4-pro",
      "reasoning_profile_id": "high",
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
- `paper_table_ablation.csv` 必须包含 `ablation_mode,exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability,completion_rate,accepted_validity_rate,wrong_canonical_acceptance_rate,raw_only_exposure_rate,raw_only_acceptance_rate,stuck_task_rate,premature_merge_rate,wall_clock_ms,total_tokens,cost`。每个专项 rate 必须来自对应 runtime/event/canonical/recovery/merge evidence，不得直接由 `ablation_mode` 推导。
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
| Experiment 1 | Factorization 500 + Lean 135 = 635 unique roots × 1 次 | 635 |
| Experiment 2 | Factorization hard 166 × 6 worker levels × 2 repeats；每 root 使用 Exp2 专用 20-way split；Lean 不进入 | 1,992 |
| Experiment 3 rate faults | Factorization 500 × 5 fault types × 6 positive rates × 2 repeats；Lean 3 × 5 × 3 positive rates × 2 | 30,090 |
| Experiment 3 worker death | Factorization 三档并集 500 × 2 death counts × 3 kill positions × 2 repeats；Lean 3 × 2 × 3 × 2 | 6,036 |
| Experiment 4 | Factorization 500 × 5 modes × 3 repeats；Lean 15 × 5 × 3 | 7,725 |
| Experiment 5 v3（四模型 capability/smoke preflight 完整时纳入 P0-full） | (Factorization 83 + Lean 8/8/8) × 4 models × 3 repeats | 1,284 |

P0-core（Experiment 1-4）唯一实际调度合计 46,478 个 root-runs；叠加当前 Exp5 v3 后，P0-full 为 47,762 个 root-runs、256,886 个 first-attempt AI units，provider-attempt 上界 650,438。历史三模型 Exp5 v2 的 48,377-root P0-full 只供 replay/provenance。Exp3 的 0% reference 行统一引用 Exp1 已持久化 evidence，reference 自身 provider calls/tokens/cost 为 0；不再产生 1,006 个 dedicated supporting roots，因此 headline 与实际调度分母一致。同一个 Exp1 root 在全局唯一真实执行分母中只计一次。Experiment 2 不再有 100/300 worker extension。root-run 数量不等于 provider calls。若预算上限无法覆盖计划，runner 写 `budget_exhausted` 并停止启动新 task；不得静默减少样本、删 mode、删 difficulty、删 topic family 或把 Lean 每格 15 道减为子样本。

## 运行前预算门禁

runner 必须先执行 `--plan-only`，根据 catalog、child counts、conditions、repeats 和 max provider attempts 生成 `run_budget.json`：

``` math
N_{calls}^{max}=\sum_{conditions}\sum_{tasks}
  N_{AI\ units}(task)\times repeats\times maxProviderAttempts
```

预算文件至少包含 planned root tasks、AI units、provider attempts 上界、token 上界、cost estimate 上界、预计 wall-clock、provider/model、并发、quota/rate-limit preflight 和磁盘空间估计。CLI 默认使用 `approval_mode=user_bypassed` 直接执行，同时记录 budget digest、`authorization_source=project_policy` 和实际 usage；如需恢复旧的人工门禁，显式传 `--require-budget-approval`，此时才要求匹配的 `--approve-budget-digest`。即使 bypass，任何显式提供但不匹配的 digest 仍必须在 transport 前拒绝。

CLI 必须支持 `--max-total-provider-attempts`、`--max-total-tokens`、`--max-cost-estimate` 和 `--stop-after-current-task`。超过任一上限时写结构化 `budget_exhausted`，不启动新 task；已完成 evidence 保留。

`--unlimited-budget` 是对既有“未提供三个总量上限时不设总量硬上限”行为的显式授权与审计身份，不是取消请求控制。该模式仍生成预算估计和 `run_budget.json`，仍记录真实 attempts/tokens/cost，并持久化 `budget_mode="unlimited"`、`approval_required=false`、`approval_mode="explicit_unlimited"`、`authorization_source="cli"`、`hard_limits={}`；但不要求 approval digest，也不设置总 provider attempts、tokens 或 cost 上限。单 AI unit 最多一次 provider attempt；Exp1–4 保留 `timeout_seconds=600`、`max_tokens=300000`，Exp5 cohort v2 保留公共 `timeout_seconds=100`、`max_tokens=8192`；temperature/model/reasoning、provider/cohort/config preflight、secret 不落盘和 evidence identity 检查全部保留。该参数与 `--require-budget-approval`、`--approve-budget-digest` 及三个 `--max-total-*` 参数互斥，冲突必须在 transport 前拒绝；不传时保持现有 `user_bypassed` 兼容行为。

## 独立 smoke profiles

`benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json` 是当前独立 Exp3–4-only 真实 API smoke selection，`suite_id=paper_smoke_exp3_exp4_v1`。它只对 `factor_v2_easy_001`、`repeat_id=0`、`worker_count=10` 调度五类 r100 rate-fault、一个 `dead1/p50` worker-death 和 Exp4 的 `FULL + 4` modes，正好 11 个直接/实际 roots。profile 强类型冻结 `baseline_policy=omitted_for_smoke_regression`：不运行/读取/导入 Exp1、Exp2、Exp3 0% 或历史 run01–run04，supporting roots/AI units/provider attempts 均为 0；Exp3 row 写 `baseline=null`、`baseline_comparison_eligible=false`、`baseline_unavailable_reason=smoke_baseline_not_requested`。该例外仅限 smoke，正式 scope 若尝试 omission policy 必须在 provider dispatch 前 fail closed。

当前综合 smoke 由 `paper_smoke_profile.v3.json` 冻结为 29 个 direct roots：Exp1–4 既有部分 21 个，Exp5 v3 部分 8 个（四模型各 1 Factorization + 1 Lean）。另有 `paper_smoke_exp5_profile.v3.json` 作为物理独立的 Exp5-only 8-root bootstrap；首次 smoke 不要求先验 smoke evidence，正式 preflight 仍要求。五次最小 endpoint/key 诊断已完成但不构成正式 capability/smoke evidence，8-root smoke 尚未运行。`paper_smoke_profile.v1/v2`、旧 Exp1–4 profile/launcher 和历史输出只供 replay/provenance，不能冒充当前预注册设计。

smoke 只负责检查真实配置、协议生命周期、fault/death/ablation/endpoint 路由和持久化/report 接线。所有层级必须冻结 `formal=false`、`pilot_only=true`、`regression_only=true`、`paper_eligible=false`，且 `ineligibility_reasons` 至少含 `smoke_suite,pilot_only`；不得提供升级参数，不得生成正式 `paper_table_*` / `paper_plot_*`，正式 metrics/report 必须拒绝其 evidence。condition selector 只保存语义字段，运行前解析为恰好一个 canonical condition/selection 并冻结 condition/selection/profile digests；零匹配、多匹配、catalog/output identity 漂移均在 transport 前阻塞。Exp3/Exp4 必须复用真实 production hooks，Exp5 必须经过 whole-cohort preflight；cohort 不完整时整个 smoke suite 在 transport 前阻塞，同时逐 endpoint 写 `status=blocked` 的审计记录，禁止自动换模型。离线 capturing 测试的 provider calls/tokens/cost 必须为 `0/0/0`，replay 只读持久化 evidence。

## 现有真实运行只用于资源校准

2026-07-02 的本地记录显示：direct factorization 500 tasks 使用 525777 total tokens、cost estimate 1.7462418；Lean 50 root tasks 使用 101 provider attempts、cost estimate 0.167799265。它们不是本文新实验结果，只用于初始预算量级。新 factorization 协议实验每个 root 会有多个 range AI units，必须由 `--plan-only` 按实际 split 重新估算，不能用 direct benchmark 的每题成本直接代替。

## 人工与机器投入

| 阶段 | 预计投入 | 完成物 |
|:---|:---|:---|
| 代码补齐 | 1–2 人日 | paper runner、catalog、real-AI gate、fault/process worker、metrics/report、tests。 |
| pilot | 0.5 人日 + API | 每个 condition 1 repeat，发现 schema/prompt/quota 问题，不进入主表。 |
| 正式 P0 run | 0.5–1 人日 + API | Experiment 1 单次正式矩阵、Experiment 2–3 各自预注册的重复和完整 evidence。 |
| ablation/model | 0.5 人日 + API | Experiment 4；预算允许时 Experiment 5。 |
| 论文与审计 | 1 人日 | 图表、failure analysis、secret scan、replay/evidence check、文字改写。 |

# 代码改造计划（agent 可直接实施）

## 文件结构

| 文件 | 职责 |
|:---|:---|
| `benchmarks/paper/factorization_catalog.v2.jsonl` | 正式默认的 factorization 500-task catalog，easy/medium/hard=`167/167/166`。 |
| `benchmarks/paper/factorization_catalog.v1.jsonl` | 历史 30-task 回归 catalog，不进入新正式矩阵。 |
| `benchmarks/paper/lean_catalog.v1.jsonl` | 冻结的 Lean simple/shallow 30-task catalog；历史 easy/medium/hard 只保留为 shallow-v1 标签。 |
| `benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` | 已冻结的 medium recursive lemma-DAG / hard-frontier catalog；正式 135 道 selection 另由 readiness manifest 固定。 |
| `src/tokenshare/experiments/paper_models.py` | `PaperExperimentCondition`、`PaperModelExecutionRecord`、budget、fault record、paper eligibility schema 和 digest。 |
| `src/tokenshare/experiments/paper_model_identity.py` | Experiment-layer endpoint identity、provider-specific reasoning normalization、condition-to-config pre-call binding 和 submission post-call audit；不得导入 runner/adapter 或硬编码 cohort member 列表。 |
| `src/tokenshare/experiments/paper_catalog.py` | 加载、校验和 digest paper catalogs；本地 oracle/Lean preflight；显式区分 Lean `paper_difficulty` 与 shallow-v1 legacy difficulty。 |
| `src/tokenshare/experiments/paper_catalog_execution_view.py` | 冻结规划使用的版本化 catalog execution view 及 body/digest；formal execution、resume/replay 只从 dispatch plan 恢复同一 view。 |
| `src/tokenshare/local_runtime/contracts.py` | 系统 runtime 的 run/plugin/hook/worker 稳定接口；不得导入 experiment schema。 |
| `src/tokenshare/local_runtime/coordinator.py` | 本地完整协议生命周期协调器；通过 scheduler/lease、executor、plugin 和 `ProtocolEngine` 推进状态。 |
| `src/tokenshare/local_runtime/workers.py` | sequential/thread/process worker capacity、liveness、heartbeat/death 事实；不自行决定 requeue。 |
| `src/tokenshare/local_runtime/projection.py` | 从 ledger/artifacts 派生通用 runtime run/unit/attempt 视图。 |
| `src/tokenshare/plugins/factorization/runtime_adapter.py` | Factorization runtime bridge：range split、request/domain payload、parser/verifier 和 merge 规则。 |
| `src/tokenshare/plugins/lean_proof/fixed_plan.py` | 校验预注册固定 lemma-DAG plan 并生成 Lean certificate；不执行 AI 拆分，也不要求通用自动 lemma discovery。 |
| `src/tokenshare/plugins/lean_proof/runtime_adapter.py` | Lean runtime bridge：proof-unit request、checker、proof assembly、dependency-aware merge/root recheck。 |
| `src/tokenshare/experiments/factorization_paper_adapter.py` | 兼容薄壳：把 paper case/config 转成 `ProtocolRunRequest`，调用系统 coordinator，再投影旧 result shape。不得保留第二套生命周期。 |
| `src/tokenshare/experiments/lean_paper_adapter.py` | 兼容薄壳：选择 Lean case/fixed plan/config，调用系统 coordinator，再投影旧 result shape。不得自行生成权威 certificate/canonical/merge。 |
| `src/tokenshare/experiments/paper_faults.py` | 在真实 output 后执行 deterministic fault selection/mutation，写 `FaultInjectionRecord`。 |
| `src/tokenshare/experiments/paper_workers.py` | worker-death 条件、kill target/progress 和实验 record；真实 worker/process/lease/replacement 由 `local_runtime`/`ProtocolEngine` 执行。 |
| `src/tokenshare/experiments/paper_model_policy.py` | 加载/校验三模型 cohort snapshot 与 local entry map，构造批准的 fixed-entry member plans；不在 executor 内解释模型强弱。 |
| `src/tokenshare/experiments/paper_budget.py` | plan-only provider/token/cost/time/space 预算及硬上限。 |
| `src/tokenshare/experiments/paper_runner.py` | 展开 Experiment 1–5 conditions、repeat/seed、resume、budget gate、paper eligibility。 |
| `src/tokenshare/experiments/paper_projection.py` | 从系统 projection、events 和 artifacts 派生 `PaperTaskResult` / `PaperAttemptResult`；这些对象不参与协议决策。 |
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

- `src/tokenshare/executors/ai_api.py`、`ai_api_config.py`、`ai_api_transport.py`：2026-07-16 旧 feat-011 实施计划 Task 10A 已完成独立 OpenAI Chat Completions provider family 和动态 provider provenance，同时保持 SiliconFlow v1 兼容；该编号不是 2026-07-22 system runtime 迁移 Task 10。executor 不承载 cohort、外部分数、fault、worker 或 experiment policy。

## 实施顺序与测试

1.  先写 paper model/catalog/budget 的失败测试，验证 digest、500 题 Factorization v2、v1 严格回归、plan-only、预算记录和可选人工 approval policy。

2.  实现真实 API paper eligibility gate；测试 scripted/fake/deterministic run 必须被拒绝为论文结果。

3.  补齐 core/`ProtocolEngine` 的 late-submission 接受性和通用 retry/recovery 原子事实，再实现 `tokenshare.local_runtime` 的单 worker FULL 生命周期；不得先让 paper runner 继续承担 requeue/canonical/merge。

4.  （已完成）迁移 Factorization runtime bridge，paper adapter 变成 coordinator 兼容薄壳；注入 fake transport 只作回归，正式 CLI 必须 real transport。每个 range AI unit 都有协议 scheduler/lease/request/submission/verification/canonical 事实。

5.  （已完成）保留 Lean catalog 中预写固定 lemma-DAG，把 fixed plan 校验/certificate 生成和 proof-unit/merge 领域逻辑迁入 Lean 插件；paper adapter 作为 coordinator 兼容薄壳从 ledger/artifacts 投影结果。本地 Lean checker 测试不依赖网络，正式候选生成仍必须走真实 API。

6.  （已完成）把 worker capacity/death、post-AI fault 和 ablation 接到 runtime hooks；测试 original/mutated refs、真实 worker termination、协议 lease expiry/replacement 和 no canonical pollution。

7.  （已完成）实现 paper projection/metrics/report；`Paper*Result` 从权威 events/artifacts 派生，用 event/artifact fixture 验证统计，不硬写 pass。

8.  （Task 10 迁移及后续正常时序补缝已完成定向与 Fast 验证）运行 runtime、targeted、`tests/experiments`、executor/plugin impact suite 和完整 `init.ps1`；正式发布时再按验证分层运行所需 Full/LeanAudit。本轮按用户要求不重复全量门禁或正式实验。

9.  只有系统生命周期覆盖审计通过后，才执行新的 plan-only、pilot、正式 P0、ablation；Experiment 5 只有在 v3 四模型 SiliconFlow capability、identity、8-root smoke 和预算 preflight 都通过时进入 P0-full，不得阻塞 P0-core。当前 8-root smoke 未运行，P0-full 保持 blocked。

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
  --ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json `
  --provider-config siliconflow=local/ai_api_smoke.local.json `
  --provider-config deepseek=benchmarks/paper/exp1_baseline_provider_config.v2.json `
  --provider-config openai=local/openai_api_smoke.local.json `
  --baseline-entry-id deepseek_v4_pro_exp1_baseline `
  --model-cohort-file benchmarks/paper/model_comparison_cohort.v2.json `
  --model-entry-map local/model_comparison_entries.local.json `
  --exp2-worker-levels 1,3,7,10,30,50 `
  --exp2-repeats 2 `
  --exp2-seed-family 2000,2001 `
  --plan-only

plan-only 通过人工检查后：

conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --output-root outputs/experiments/paper_v1 `
  --experiments exp1,exp2,exp3,exp4,exp5 `
  --real-transport `
  --ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json `
  --provider-config siliconflow=local/ai_api_smoke.local.json `
  --provider-config deepseek=benchmarks/paper/exp1_baseline_provider_config.v2.json `
  --provider-config openai=local/openai_api_smoke.local.json `
  --baseline-entry-id deepseek_v4_pro_exp1_baseline `
  --model-cohort-file benchmarks/paper/model_comparison_cohort.v2.json `
  --model-entry-map local/model_comparison_entries.local.json `
  --exp2-worker-levels 1,3,7,10,30,50 `
  --exp2-repeats 2 `
  --exp2-seed-family 2000,2001 `
  --approve-budget-digest <digest> `
      --max-total-provider-attempts <approved-limit> `
      --max-total-tokens <approved-limit> `
      --max-cost-estimate <approved-limit>

CLI 若未给 `--real-transport`、Exp1–4 实际执行命令未显式给 `--ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json`，或 v3 digest、provider/model/endpoint/thinking/reasoning/`600/300000`、catalog/selection/profile/分母等预注册语义任一不匹配，应在 provider dispatch 前 structured blocked，且 provider calls 为 0。`execution_plan_digest` 与 `budget_digest` 都绑定实际绝对 `output_root`；全新 root 必须重新生成并冻结这两个运行实例 digest，不能与历史 root 的值比较。只有同一 output root 的冻结 identity 不一致才必须 fail closed。不得自动退回 scripted transport，也不得用 Experiment 5 cohort 的其他 member 替代。上面两条包含 Exp5 的命令中，`--provider-config deepseek=benchmarks/paper/exp1_baseline_provider_config.v2.json` 只绑定 Exp5 cohort v2 的 DeepSeek member，不是 Exp1–4 baseline。运行 Experiment 5 时还必须加载 frozen cohort v2、local entry map、SiliconFlow/DeepSeek/OpenAI provider configs，并通过三个 member 的 key/model/reasoning/smoke preflight；任一 member 缺失时 runner 写 `blocked_reason="incomplete_model_cohort"`，而不是让 Experiment 1–4 失败，也不得自动替换模型。

当前 Exp1–4-only smoke 的唯一启动形态如下；run id、output root 和 supervision root 都必须此前不存在，输出固定 `paper_eligible=false`：

    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $runId = "paper_smoke_exp1_exp4_v3_${stamp}_run03"
    powershell -NoProfile -ExecutionPolicy Bypass `
      -File .\local\run_exp1_exp4_v3_smoke.ps1 `
      -RunId $runId `
      -OutputRoot "outputs/experiments/$runId" `
      -SupervisorRoot "local/supervision/$runId"

launcher 必须先执行 `--smoke-identity-only`：跨 run 核对 profile/catalog/selection/provider-config digest、模型与请求控制、repeat/retry、21 direct roots + 1 worker-death supporting baseline（22 个实际调度 roots）、114 个首轮 AI units 和 150 provider-attempt upper bound；然后仅冻结当前 output root 生成的 execution-plan/budget digest，并在实际 runner argv 中通过 expected-digest 参数复核。不运行 Experiment 5、pilot、正式 Experiment 1–5 或 Full 实验矩阵。

# 四天执行安排

| 日期 | 工作和完成门槛 |
|:---|:---|
| Day 1 | 完成 catalog、paper schemas、real-AI gate、budget plan 和 adapter 最小路径；targeted tests 通过；跑每个 domain 1 个真实 API smoke。 |
| Day 2 | 完成 Experiment 1 和 Experiment 2；跑完整 pilot；当天生成 feasibility/scalability CSV 并检查是否有区分度和 429。 |
| Day 3 | 完成 post-AI fault、worker process death 和 Experiment 3；跑 factorization 完整故障率和 Lean 精简故障率。 |
| Day 4 | 完成 Experiment 4、论文表图和 failure analysis；三模型 cohort 与双 provider preflight 通过时跑 Experiment 5；做 secret scan、evidence check、Markdown/论文文字更新和完整 init。 |

如果时间或预算不足，runner 使用 `budget_exhausted` 结构化停止，不能静默删除 Experiment 1-4、difficulty、fault type 或 ablation mode。Experiment 5 只在三模型 cohort 任一 member、provider transport、reasoning profile 或真实 smoke 不满足时允许 `incomplete_model_cohort` blocked；其他删减必须经用户重新批准并写成新的 suite version。按 EPD-005，Ablation 默认运行 `FULL、NO_VERIFICATION、NO_PARSER_POLICY、NO_REQUEUE、NO_MERGE_GATE` 全部 5 个模式；这不是运行时预算裁剪，而是用户批准的新正式矩阵。

# 验收标准

新实验计划完成的必要条件：

1.  本 Markdown 是唯一权威设计；旧 `.tex/.pdf` 和 Phase 8 实验设计文稿已删除，导航和 README 不再把旧 Experiment 1-4 当论文主口径。

2.  新 paper runner 没有 scripted fallback；所有论文 run 的 `paper_eligible=true` 可由真实 provider attempts、raw artifacts 和系统 ledger 中完整的协议 lifecycle coverage 证明。

3.  factorization 主实验走协议 range children、parser/verifier/canonical/merge，不用 direct 500 准确率替代；有效 factor witness 使用 verifier-gated OR-join，没有 witness 的 no-factor/prime 结论仍要求全部 required ranges 的 accepted canonical coverage。

4.  Lean 主实验必须区分 simple shallow、medium recursive lemma-DAG 和 hard/frontier stress 层级：当前 `lean_catalog.v1.jsonl` 全部只能算 simple，正式递归证明拆分主张至少需要 medium lemma-DAG；允许使用 catalog/脚本预注册的固定拆分图，但必须由 Lean 插件校验并生成 certificate，再由协议系统 ledger 记录 split/expand、依赖解阻、checker/canonical、merge/root recheck 和 completion；所有可采信 proof case 都必须有真实 AI proof candidates，不能用 50 个近似同难度 shallow 题替代，也不能把当前 fixed-plan 能力表述为尚未实现的通用自动 lemma discovery。

5.  worker scaling 测同一 root/task batch 的协议 worker，并记录 provider 限流混杂。

6.  故障注入引用原始真实输出，实际 token 与 synthetic mutation 分开；worker death 是真实独立 worker process 终止。

7.  metrics 从权威 protocol events/artifacts/attempts/fault records 复算；`PaperTaskResult`/`PaperAttemptResult` 只作派生报表，逐 task/attempt 数据能支撑每个论文汇总值。

8.  输出包含预算、paper eligibility、secret scan、图表 CSV、正负 failure examples 和稳定 schema version。

9.  `tokenshare.experiments` 的 FULL 路径不直接推进 canonical/requeue/merge/completion，worker pool 只提供容量，所有 unit 都通过系统 scheduler/lease；targeted tests、影响范围 tests、`compileall`、完整 `init.ps1` 通过，并把证据同步到 code map、feature list、progress 和 handoff。

## 2026-07-25 实验设施 Task A–11 实现状态（历史，已由 EPD-012 覆盖 Exp3/P0 算术）

2026-07-24 的 Exp2–4 行为审计和 Exp5 blocker 列表保留为实现前 provenance；当前实现已经按 EPD-001～EPD-006 完成设施补全：

- Exp1=`12 conditions / 635 roots / 2,900 first-attempt AI units`；Exp2=`12 / 1,992 / 39,840 no-early-stop upper bound`，使用插件拥有的 20-way profile 和 system-native early stop。
- Exp3 当时的 headline/supporting/actual roots=`41,156/1,006/42,162`；该 dedicated baseline 版本只保留历史 replay，当前 runner 已由 EPD-012 改为 shared Exp1 reference，正式 roots=`36,126` 且 supporting=`0`。
- Exp4 只含五模式，`90 conditions / 7,725 roots`；五模式统一 `max_retries=1`，`NO_REQUEUE` 只关闭 replacement；四类专项指标来自 runtime observations。
- Exp5 当时为 `36 conditions / 1,899 roots / 14,652 first-attempt AI units`；该 v2 三端点口径现仅供历史 replay。当前 v3 为四模型同一 SiliconFlow provider、`48 conditions / 1,284 roots / 9,888 first-attempt AI units`，离线设施与独立 bootstrap 入口已定向验证，artifact-backed 8-root smoke 未运行。
- 当时 P0-core/P0-full actual scheduled roots=`52,514/54,413`，first-attempt AI units=`275,126/289,778`，provider-attempt 上界=`715,558/730,210`；这些数已失效。当前 EPD-012 P0-core roots=`46,478`、first-attempt AI units=`246,998`、provider-attempt 上界=`640,550`；叠加当前 Exp5 v3 后，P0-full roots=`47,762`、first-attempt AI units=`256,886`、provider-attempt 上界=`650,438`。历史 v2 的 `48,377/261,650/655,202` 只供 provenance；这些都只是计划预算，不是实际 usage。

2026-07-26 的反伪造复核进一步关闭了 Task 11 后发现的资格链、critical path、空消融 observation、Exp5 identity 分母、worker-death 输出、429 sensitivity 和 Gate C stale fixture 问题。正式资格现在只有 `attempt → task → condition → experiment → suite → report` 一条聚合链；Exp2 的 `wall_clock_ms`、`provider_latency_sum_ms`、`critical_path_ms` 保持独立，且只有 Exp2 把完整成功依赖路径作为专项资格门禁。Exp1/3/4/5 中证据完整的 checker rejection、未恢复故障、消融失败和 provider/model failure 必须保留在正式分母中，不得因没有成功 merge/root-completion 而失格；其资格仍由真实 terminal attempt/task evidence 和各实验专项 evidence 决定。Exp3 禁止 self-baseline，Exp4 要求真实 hook refs 与同 case/repeat 的合格 FULL 配对，Exp5 以完整预期 provider-attempt inventory 作为身份分母。负面终局专项 TDD 从 `4 failed, 1 passed` 到 `5 passed`，最低验收九文件套件为 `251 passed in 120.27s`，最终 Fast 为 `331 passed, 1 skipped in 15.77s`；Capturing 仍只能生成 regression report。

此前设施验收证据为 Task 11 规定组合 `286 passed` 和当时 Fast `331 passed, 1 skipped`。本次反伪造复核也没有运行真实 API、pilot、formal Experiment 1–5、Full、LeanAudit 或 force-all。因此本节只声明实验设施和证据门禁可运行，不声明任何新的论文实验结果，`feat-011` 继续保持 `in-progress`。
