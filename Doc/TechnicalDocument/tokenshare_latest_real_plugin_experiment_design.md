# TokenShare 真实 AI API 论文实验设计与实施规格

状态：唯一权威实验设计

当前版本：2026-08-01

适用范围：Experiment 1–5、paper runner、预算、真实 API 执行、指标、表图和结果审计。

本文只保存当前有效规格，不再夹带逐轮修复日志。参数决策 provenance 看 `tokenshare_experiment_parameter_decision_log.md`；旧设计、实施计划和审计记录位于 `Doc/archive/design-history/`，不得覆盖本文。

### 2026-07-31 当前规模覆盖声明（EPD-026）

本节与第 4、5、7、8 节中的同口径条目是当前运行权威。早期文档、历史 selection/profile 和历史 evidence 中出现的 Factorization 500 全量、Exp2 hard 166、Exp3/4 大矩阵、Exp5 v3 107 roots/model-repeat、P0-core 46,478 或 P0-full 47,762 只作 replay/provenance，不得用于新 plan、smoke 或正式运行。

- 当前机器可读规模权威是 `benchmarks/paper/paper_suite_scale_profile.v1.json`，profile=`paper_suite_scale_300_50_54.v1`，解析 digest=`sha256:9cab1fb5077265807e1f64c1b63265d0dd3e2f1e87a3371f1c811132e5da7dad`。
- Factorization 从不可变 v2 500 题 catalog 按 difficulty 内稳定 hash 选择 `100/100/100`；Exp1 使用全部 300，Exp2 使用其中 hard 50，Exp3/4 共享 `17/17/16`。不得退回 catalog prefix 或运行时随机抽样。
- Exp5 当前 selection 是 `exp5_parent_quarter_selection.v4.json`：Factorization hard 42，Lean 三个 hard topic family 各 4，共 54 roots/model-repeat；canonical JSON 语义 `selection_digest=sha256:452f25dcc53a1eb0387665c6f451f1320095efb0c4bf154af62bf5e388afb6b2`，tracked 文件原始字节 `content_digest=sha256:e6c5c05e4310b385495ca3dccfcc2d7f38b71841d451726c89a1fe45200beb67`。它按 stratum 取不可变 v3 parent selection 的有序前缀，v3 文件保持原字节只供历史 replay。
- EPD-026 冻结的逻辑计划总量为 Exp1–4 `5,736 / 35,528 / 76,280`，Exp1–5 `6,384 / 40,520 / 81,272`，顺序分别是 root-runs / planned first-attempt AI units / 原全在线 provider-attempt upper bound（EPD-027 下兼作最大 trace-slot capacity）。
- EPD-026 的 Exp1–5 `23,503,151,360` token 和 `7,346.259328` cost 是旧全在线方案的 ceiling，不是 EPD-027 两阶段方案的预计 acquisition 或实付；新预算必须由 response-bank inventory 与在线检查 profile 重算。
- 正式磁盘 forecast=`50,206,081,024` bytes（46.76 GiB），最大 condition compaction reserve=`648,806,400` bytes（0.60 GiB），连同 25%/2 GiB 中较大的安全余量后 required=`63,406,407,680` bytes（59.05 GiB）。runner 必须在 provider dispatch 前以实际目标卷 free bytes 重算并 fail closed。
- 全量运行的峰值内存不随 81,272 attempts 一次性常驻：generation v3 每 root 只保留 delta 并立即 checkpoint/release，terminal compaction 流式写临时 SQLite；metrics 用 lazy bundle mapping，JSONL/报告采用流式或分块扫描。当前证明的最大单 condition 为 `100 roots / 1,000 AI units / 1,000 provider attempts`，因此内存量级为最大单 outcome/当前 bundle 加固定 SQLite/分块开销，而不是全 suite 结果总和。它不是任意 provider 单响应大小的绝对 RSS 保证；正式启动仍须保留 OS 余量并监控单响应与当前 bundle 峰值。

### 2026-08-01 两阶段真实回答库覆盖声明（EPD-027）

Experiment 1 与 Experiment 5 继续逐 unit 在线调用真实 API。Experiment 2–4 的正式主矩阵改为“两阶段配对设计”：先按稳定的 provider 实际请求正文取得并封存不可变真实回答，再由新的 trace-backed executor 把这些回答作为输入，重新经过 TokenShare 正常状态机、fault hook、verifier/checker、lease、worker death、requeue、merge、settlement 与 event ledger。这里的 trace 是真实 API 回答的封存副本，不是 scripted/mock 输出。

- 回答库键不得使用包含 `attempt_id`、lease、fencing、时间或 condition 的完整 `ExecutionRequest` digest；必须对 provider 实际收到的稳定正文、endpoint/model、有效请求控制、prompt/plugin version、独立 sample/repeat slot 与 replacement slot 建立 `inference_request_digest`。同一 repeat 内的配对条件共享回答；不同 repeat 使用独立 sample slot。
- 每个 bank entry 至少保存不可变 request body/digest、raw output 或真实 provider failure、provider/model/config provenance、usage、实测 latency、acquisition attempt identity、sample slot 与 replacement slot。回答库 acquisition 的真实消费只记一次；消费运行创建当前 task/unit/attempt/lease 所属的新 submission，并显式引用 source bank entry，不得复制 source 身份冒充新的 provider call。
- Experiment 3 的 replacement slot 按冻结最大恢复深度准备；只准备一个 replacement 不足以覆盖当前 rate-fault 与 worker-death 规则。缺少任一必需 entry 时必须在该正式运行前 `blocked`，不得临时退回 scripted 结果或偷偷缩短 retry。
- Experiment 2 另在缩小且预注册的题集上，使用真实 API 覆盖全部六个 worker 档位 `1,3,7,10,30,50` 做在线并发检查。它用于检查真实 API 的排队、限流、超时和变化趋势是否与回答库主实验严重背离；它不把主矩阵改写成全量在线结果。缩小题集、重复数、通过阈值和预算须在实施计划中冻结后再实现。
- Experiment 3 另保留小型真实 API 在线恢复检查，至少覆盖 verifier/checker 拒绝后 replacement 与真实 worker death 后重新分派两条恢复来源。证据链必须证明 fault/death 之后才建立新 attempt 并发起新的 provider call，随后保存独立 raw/provenance/usage；它只为小型检查提供 `actual` 重跑证据，不把回答库主矩阵的 trace token 冒充为逐条件实际在线消费。
- Experiment 2–4 的主结果统一声明为“基于不可变真实模型 trace 的协议扩展性/恢复/消融”。正确率、完成率和机制指标继续由完整状态机 evidence 产生；主矩阵的时间、token、cost 和调用量使用 `trace_replay_*`、`trace_attributed_*` 或 bank-slot consumption 口径。Experiment 3 主矩阵使用 `discarded_trace_tokens`；`wasted_actual_tokens` 仅适用于在线恢复检查中具备故障后新 provider call 证据的样本。
- EPD-026 的 `76,280` 仍保留为 Exp1–4 原全在线方案的 provider-attempt 上界和当前协议 attempt/trace-slot 容量参考，不再代表两阶段方案预计会在线发出的请求数。当前静态审计得到的 DeepSeek acquisition 量级约为 `6,904` 次加在线检查，但这不是冻结预算；必须先生成完整 outbound-body bank inventory 才能确定。Experiment 5 的 SiliconFlow 调用和预算单列。
- 人民币 `1,000` 作为 DeepSeek bank acquisition 与在线检查的目标硬停止线；实现时必须按冻结价格、per-request token ceiling 和 in-flight reservation 同时约束 calls/tokens/CNY。当前每 attempt 固定 `0.05` 的预留不能作为可靠硬门。预算耗尽时停止新 provider dispatch、正常收口已有 evidence；bank 不完整则所有依赖它的正式结果保持 blocked。
- 该决定是一次跨 request identity、executor/transport、artifact schema、paper eligibility、budget、runner、metrics/report/renderer、smoke/canary 与 replay/audit 的实验设施全面改造，不是对现有 replay 或 runner 的局部修复。当前状态仅为 `design_synced`，尚未实现；在完整实施计划获确认并通过新设施验证前，不得启动旧 P0-core/P0-full 或把旧 paper eligibility 规则套到回答库结果上。

## 1. 论文主张与证据边界

论文只围绕三个主问题：

1. 可行性：同一协议生命周期能否在 Factorization 与真实 Lean proof 两个领域中，用真实 AI 产生候选，再由确定性 verifier/checker 给出可审计结论。
2. 扩展性：在固定的真实模型回答 trace 上增加协议 worker，如何改变协议侧端到端时间、配对加速、trace 资源需求、正确性与利用率；并由缩小题集的六档真实 API 在线检查审计 provider 并发偏差。
3. 鲁棒性：真实 AI 回答 trace 遭遇预注册 fault 或 worker death 时，协议能否检测、隔离、恢复或结构化失败；并由小型在线恢复检查证明故障后真实重新调用 API 的能力。

Experiment 4 用于解释协议机制贡献。Experiment 5 是同一 SiliconFlow provider 下的四模型 endpoint comparison，只作次要分析；serving profile、路由、限流和计费仍是混杂因素，不得解释成模型本体的纯因果效应。

deterministic/scripted/mock、direct benchmark、未预注册 selected-unit partial、diagnostic 与普通 smoke 只能用于回归、成本校准或接线验证，固定 `paper_eligible=false`，不能写入论文主结果。EPD-027 的 trace-backed 主矩阵和预注册在线检查是新的显式证据类别，不能归入旧 `stored_evidence_only` replay，也不能冒充当次 `real_transport=true`。

## 2. 全局硬门槛

paper evidence 分为两类，不得混写：

- `online_real_provider`：用于 Experiment 1、Experiment 5、Experiment 2 在线并发检查和 Experiment 3 在线恢复检查；要求本次 run 的每个实际执行 AI unit 均有真实 provider attempt，且 `real_transport=true`。
- `real_model_trace_protocol_run`：用于 Experiment 2–4 主矩阵；要求每个实际消费 slot 都绑定一个不可变、身份一致且可完整追溯到真实 provider acquisition 的 bank entry，同时本次 run 的 `current_provider_call_count=0`。它必须明确记录 source real transport 与 current trace-backed execution，不能把 source call 重复计成当前调用。

两类 evidence 都必须同时满足：

- 使用预注册 provider/model/reasoning/request identity；request、raw 或真实 provider failure、parsed/parse-failure、provenance、usage、latency、cost estimate 和 model execution record 均按各自 evidence class 持久化。
- provider 非确定性输出在 acquisition/首次在线执行时落 artifact；resume、stored-evidence replay、metrics 与 report 不重新调用 provider。trace-backed 主矩阵只消费已冻结 bank entry，不在缺口处临时发请求。
- Factorization 候选经过插件 parser 与 deterministic verifier；Lean 候选经过固定本地 Lean/lake/toolchain/library checker。
- AI 不决定协议级拆分。Factorization range 由插件确定性生成；Lean 只使用 catalog/脚本预注册并由插件校验的 fixed lemma-DAG。
- 正常 FULL 生命周期由 `ProtocolRunCoordinator` 与 `ProtocolEngine` 驱动，scheduler、lease、attempt、verification、canonical、recovery、merge、completion、settlement 均能回到 event/artifact evidence。
- secret 不进入 tracked config、event、artifact、SQLite、log、digest 或论文输出。
- catalog、selection、plugin/executor/prompt、model endpoint、request limits、budget、output root、evidence class、bank inventory（适用时）和 evidence identity 均与冻结计划一致。

缺少任一门槛时必须给出结构化 `ineligibility_reasons` 或在 provider 调用前 `blocked`，不得回退到其他模型、scripted transport 或缩小矩阵。

### 全局端到端正确性主指标

论文中所有 Experiment 1–5 的正确性主指标统一为：

```text
end_to_end_verified_success_rate
  = verified_correct_root_count / preregistered_root_count
```

- 分母是对应实验、condition、repeat 与报告分层中冻结的全部预注册 root-run；实验性失败、规定重试用尽、无返回、超时、未恢复 worker death 与其他未得到可接受结果的 root-run 都保留在分母中，不得删除。
- 分子只包含同时满足以下条件的 root-run：存在最终结果；该最终结果由冻结的领域正确性判断确认正确（Factorization deterministic verifier/oracle，或固定 Lean checker）；相应 task/attempt/event/artifact 证据完整且身份一致。Experiment 4 即使关闭某项协议机制，论文评估仍使用同一冻结领域正确性判断评价最终结果，不能把“协议未检查”等同于“结果正确”。
- 没有最终结果、只有被拒绝候选或重试用尽的 root-run 不算“错误最终结果”，而是“未得到可接受结果”；它仍使本指标下降，并必须在 failure stage/kind 中保留具体原因。
- 产生了最终结果但冻结领域正确性判断为错误的 root-run 记为“错误最终结果”；这类结果尤其用于 Experiment 4 的错误逃逸与错误 canonical 分析。
- 论文主表不使用 `正确结果数 / 实际产生最终结果的题数` 这种条件正确率，避免排除未解决 root-run 后造成比例虚高。正确、错误最终结果、未得到可接受结果的计数及失败原因可以作为解释性列或审计输出，但不得另换分母覆盖本主指标。
- 配置、catalog、identity 或基础设施阻断导致整个 suite/condition 不具备论文资格时，仍须在审计输出中保存预注册分母和未开始记录，但不得把该无效运行伪装成一条可发表的低成功率论文结果。

### 全局完成率

论文中所有 Experiment 1–5 的完成率统一为：

```text
completion_rate
  = final_result_root_count / preregistered_root_count
```

- 分母与 `end_to_end_verified_success_rate` 完全相同，是对应报告单元冻结的全部预注册 root-run。
- 分子只包含实际产生了最终 canonical/root result 且具备完整结果引用的 root-run，不要求该最终结果正确；仅仅到达 `failed`、`blocked`、`timeout`、`budget_exhausted`、`ineligible` 或 `not_started` 等终态不算完成。
- 完成且正确的 root-run 同时进入两个指标的分子；完成但错误的最终结果只进入 `completion_rate` 分子；规定重试用尽、无返回、超时、未恢复 worker death 或其他没有最终结果的 root-run 不进入任一分子，但始终保留在共同分母中。
- 在分母和证据完整时，`completion_rate - end_to_end_verified_success_rate` 等于错误最终结果率；论文仍须保留错误最终结果计数与 evidence，不能只给差值。
- paper-ineligible 的配置或基础设施阻断运行只在审计中保留 planned denominator，不形成可发表的 completion row。

## 3. 当前模型与请求控制

### Experiment 1–4

- Provider：官方 DeepSeek。
- Model：`deepseek-v4-pro`。
- Entry：`deepseek_v4_pro_exp1_baseline`。
- Tracked config：`benchmarks/paper/exp1_baseline_provider_config.v3.json`。
- Endpoint：`https://api.deepseek.com/chat/completions`。
- Thinking：enabled；`reasoning_effort=high`。
- `timeout_seconds=600`，`max_tokens=300000`。
- `max_provider_attempts_per_ai_unit=1`；thinking 请求不发送 `temperature` 或 `top_p`。
- `max_in_flight_global=50`，与协议 `worker_count` 分开控制。

首次 attempt、恢复/replacement、resume 都保持相同 identity。缺少 entry、key、identity 或必要 smoke evidence 时对应条件 blocked；不得切换 GLM、OpenAI 或 Exp5 member。

### Experiment 5 cohort v3

四个 entry 全部通过 SiliconFlow：

| 顺序代号 | Model | Reasoning |
|---|---|---|
| A | `zai-org/GLM-5.2` | thinking，budget 32768 |
| B | `Qwen/Qwen3-14B` | thinking，budget 32768 |
| C | `MiniMaxAI/MiniMax-M2.5` | thinking，budget 32768 |
| D | `Pro/deepseek-ai/DeepSeek-V3` | nonthinking |

公共控制：`timeout_seconds=600`、`max_tokens=32768`、每 AI unit 1 次 provider attempt、SiliconFlow 全局 in-flight=3。三次 repeat 的 model 顺序固定为 `ABCD / BDAC / CADB`。

Tracked identity：

- `benchmarks/paper/model_comparison_cohort.v3.json`
- `benchmarks/paper/model_comparison_entry_map.v3.json`
- `benchmarks/paper/exp5_siliconflow_provider_config.v3.json`
- `benchmarks/paper/exp5_hard_half_selection.v3.json`

cohort v1/v2、三端点、`100/8192`、1,899 roots 等值只供历史 replay/provenance。

## 4. Catalog 与实验矩阵

### 输入 catalog 与 active selection

- Factorization 不可变 source：`factorization_catalog.v2.jsonl`，500 roots，easy/medium/hard=`167/167/166`。当前正式 corpus 由 `paper_suite_scale_profile.v1.json` 按 difficulty 内稳定 hash 固定选择 `100/100/100`，合计 300；未选中的 v2 roots 与 v1 的 30 题都不进入新正式矩阵。
- Lean：正式 selection 为 3 个 `paper_difficulty` × 3 个 `topic_family` × 每格恰好 15，合计 135 个 checker-backed roots。
- Lean 正式 case 来自 `lean_lemma_graph_catalog.v1.jsonl` 与 `lean_task14_3x3_readiness.v1.json` 的冻结 selection；`lean_catalog.v1.jsonl` 只是 shallow legacy 输入。
- 每个 Lean case 必须冻结 environment digest、oracle package、fixed lemma-DAG、preflight/checker evidence。frontier stress 若本身未解或不可证，只能作为 structured negative，不得伪造成 checker success。

### Experiment 1：跨领域可行性与难度

- Factorization 300 + Lean 135，共 435 unique roots。
- 每题 1 次，合计 435 root-runs、1,970 planned first-attempt AI units/provider-attempt upper bound。
- baseline repeat/seed 为 `repeat_id=0, seed=1`；Exp3 shared reference 使用同 `case_id` 的持久化 Exp1 terminal evidence。
- 输出按 domain、difficulty、Lean topic family 报告 completion、`end_to_end_verified_success_rate`、failure stage/kind、wall-clock、provider latency、tokens、cost 和 paper eligibility。

### Experiment 2：真实回答 trace 驱动的 worker 扩展性与六档在线检查

- 只使用当前稳定 hash corpus 中的 Factorization hard 50 roots；Lean 不进入。
- 插件 split profile：`factorization.exp2_contiguous_20way.v1`，每 root 计划 20 个连续 range units。
- worker levels：`1,3,7,10,30,50`；每档对同序 roots 运行 2 repeats。
- 主矩阵合计 600 root-runs、12,000 planned first-attempt AI units/trace-slot consumptions；同一 `case_id × repeat_id` 的六个 worker level 共享同一份真实回答，两个 repeat 使用独立 sample slot。
- verifier-accepted factor witness 允许自然早停：不取消已发出的同批请求，只停止尚未调度的 sibling。
- 各 worker level 的正确性门禁继续报告 `end_to_end_verified_success_rate` 与 completion；快速失败不能被解释为扩展性收益。
- `trace_replay_wall_clock_ms` 保存每个预注册 root-run 从协议开始到终态的 trace-backed 总时间，无论最终正确、错误或未完成都不得删除。核心扩展性指标改为 `trace_replay_paired_speedup`，只在同 `case_id × repeat_id` 的 worker=1 与 worker=k 两端均 `end_to_end_verified_success=true`、时间 evidence 完整且时间大于 0 时计算：`worker=1 trace_replay_wall_clock_ms / worker=k trace_replay_wall_clock_ms`；否则写 `null`，并报告 eligible/ineligible pair count 与原因。失败 root-run 仍完整进入全局正确率、完成率、时间和资源汇总。即使按 bank 中实测 latency 延迟交付，该时间也不得称为当次真实 provider wall-clock。
- 每个 repeat 内按 worker level 报告 50 个逐 root 配对结果及 eligible pairs 的 paired-speedup 中位数，并按 `early/middle/late/no_factor` 分层；两个 repeats 的原始汇总值均保留，只报告 min/max 与相对差，不把 `n=2` 当作稳定分布。
- 资源代价改为 bank-slot consumption、`trace_attributed_tokens` 与 `trace_attributed_cost`，并在同 `case_id × repeat_id` 上相对 worker=1 计算 paired trace token/cost multiplier；不得把同一个 bank source 被多条件消费写成多笔 actual provider spend。回答库 acquisition 的真实 provider calls/tokens/cost 在单独账本只记一次。
- 真实并行与自然早停 evidence 保留 planned/executed/unscheduled units、in-flight-at-witness、observed peak concurrency 与 `worker_utilization`。`observed_peak_concurrency` 不得超过配置 worker 数、实际 dispatched unit 数或图宽 20；worker 30/50 的低利用率用于呈现超过图宽后的闲置容量。
- 只保留解释性 `trace_replay_parallel_efficiency = trace_replay_paired_speedup / configured_worker_count`，使用与 speedup 相同的 eligibility；重复别名 `efficiency` 删除。主矩阵不声称测得真实 provider 429/rate-limit；这些外部行为只由在线并发检查记录。
- 当前 `throughput = executed_ai_unit_count / wall_clock` 会把更多无用执行误当成更高性能，`throughput_roots_per_second` 在逐 root 行又只是 wall-clock 的倒数；两者均从论文指标删除。critical path 与 source provider latency 继续持久化为诊断/审计 evidence，但不进入 Experiment 2 主矩阵论文指标集合，也不得替代 `trace_replay_wall_clock_ms`；在线检查的真实 wall-clock 单独报告。

在线并发检查使用缩小且预注册的同构题集，固定覆盖 `1,3,7,10,30,50` 六档并真实调用 API。它单独报告 actual calls/tokens/cost、真实 wall-clock、provider latency、429/timeout 与完成/正确性，用于判断主矩阵趋势是否存在严重外部偏差；不得把小题集检查直接拼入 600 root-runs 主表，也不得用它补齐主矩阵的逐 root 在线数字。

### Experiment 3：真实回答 trace 故障/worker death 与在线恢复检查

Rate-fault 仅包含五类：

| Fault | 实际注入边界 | 主要观察 |
|---|---|---|
| `false_positive` | parsed candidate 后、verification 前 | 错误候选是否被拒绝、是否污染 canonical |
| `false_negative` | parsed candidate 后、verification 前 | 可适用候选被抑制后的检测/恢复；不适用目标按冻结 reserve 处理 |
| `no_return` | raw/provenance/usage 已保存、submission 前 | lease expiry 与 replacement |
| `late_submission` | raw 已保存、deadline 后提交 | late rejection 与 canonical 隔离 |
| `executor_error` | provider response 已保存、parser bridge 前 | request-stage 失败与 replacement |

- Factorization rate-fault：共享稳定 hash slice 50 roots（`17/17/16`）× 5 faults × `1/5/10/25/50/100%` × 2 repeats。
- Lean rate-fault：3 roots（三个 topic family 各 1）× 5 faults × `10/50/100%` × 2 repeats。
- 0% 不形成 Exp3 condition/provider call；从正式 Exp1 同 `case_id` evidence 投影 shared reference。
- Worker death 是独立条件：`worker_count=10`，`dead_worker_count ∈ {1,3}`，kill progress=`25/50/75%`，2 repeats；终止真实 executor process，保留 PID/exit、lease expiry、recovery 与 replacement facts。
- Worker-death 对相同 50 Factorization + 3 Lean roots 运行，因此 Experiment 3 合计：rate-fault 3,090 + worker-death 636 = 3,726 root-runs，17,148 planned first-attempt AI units，54,372 provider-attempt upper bound，supporting baseline roots=0。

注入 record 只描述实际变换，`detected`、`wrongly_canonicalized`、`recoverable/recovered` 必须从 verifier/canonical/recovery/completion evidence 派生。真实死亡但 replacement 失败时写完整失败记录并保留分母；不得抛弃已有 evidence 或把它计为成功恢复。

验证机制的检出能力只在 Experiment 3 的受控故障边界内计算：`false_positive` 的受控错误拦截率以“预注册、实际完成注入且确实到达 verification 边界的已知错误候选”为分母，以其中被 verifier 明确拒绝的候选为分子；受控错误逃逸率使用同一分母，以进入 canonical/root result 的注入候选为分子。二者必须按同一 fault/attempt identity 从 evidence 派生，不得混入自然产生但没有独立错误标签的候选、无返回、超时、parser/executor error 或其他 fault。自然运行中的 verifier 拒绝次数/占比只能作为过程统计，不得命名为自然错误检出率。Experiment 4 的 `FULL` 与 `NO_VERIFICATION` 则通过相同 `case_id × repeat_id` 上的 completion 与 `end_to_end_verified_success_rate` 差异衡量启用验证对最终系统结果的影响，不重复计算检出率。

“检出后是否启动恢复”不作为论文比例：凡协议规定应恢复且已检出的 fault，都必须产生匹配 fault/attempt identity 的 replacement/requeue evidence；否则该 root-run 的恢复链证据不完整并 fail closed。含义模糊的 `recovery_rate` 删除。Experiment 3 只保留辅助指标 `replacement_attempt_success_rate`：分母为实际启动的 replacement attempt，分子为其中产生合格替代结果并完成原 task unit 的 attempt；按 fault type 分别报告，不跨 fault 汇总。该指标解释重试次数限制及替代回答再次失败，不替代 root 级 `end_to_end_verified_success_rate`；零分母写 `null` 和明确 applicability reason。

`recovery_latency` 不作为论文指标。fault 发生、确认、replacement/requeue 启动与结束的原始时间仍必须持久化，用于审计和诊断；Experiment 3 主矩阵的时间与资源结果改为 trace-replay wall-clock 以及 trace-attributed token/cost。比较必须绑定同一 sample slot 的无故障 trace reference，并在表头、caption 或方法中明确 `comparison_kind=paired_trace_reference`，不得声称为每个 fault condition 的实际在线时间或付费。

主矩阵核心指标：受控错误拦截率、受控错误逃逸率、completion、`replacement_attempt_success_rate`（辅助）、reassignment、`discarded_trace_tokens`、相对 paired trace reference 的 trace-replay wall-clock/trace-attributed token/cost overhead、kill-progress error、result completeness 与 `end_to_end_verified_success_rate`。在线恢复检查另报告实际 provider calls/usage 与 `wasted_actual_tokens`。

`reassignment_count` 保留为 Experiment 3 的过程计数：只统计因 fault/worker death 实际启动、具有新 `attempt_id` 且能反向关联原 fault 与 task unit 的 replacement/requeue attempt；只有恢复计划或 recovery event、但没有真实新 attempt 的记录不得计数。按 fault type/worker-death condition 分别报告，原始 identity 链同时作为实验完整性 evidence。

主矩阵的 `discarded_trace_tokens` 只汇总因实际 fault/worker death 而没有进入最终有效 canonical 路径的 bank entries 所携带的真实 source usage；同一 source 被不同条件消费时是 trace 归因，不是重复发生的 actual spend。它必须逐项关联 source bank entry、当前 attempt、fault identity 与 rejection/abandonment/canonical exclusion evidence；必要 usage 缺失时写 `null` 并 fail closed。

`wasted_actual_tokens` 只保留在小型在线恢复检查中：被 fault/death 作废的 attempt 必须是当次真实 provider call，且在 fault/death 之后存在不同 `attempt_id` 的后续真实 provider call 与独立 raw/provenance/usage。在线检查至少覆盖 verifier/checker 拒绝触发 replacement 与 worker death 触发重新分派两条链；只有 recovery event、预取 bank slot 或计划值不能证明真实重跑。

论文正文把主矩阵的 `trace_attributed_token_overhead` 解释为“在同一组真实回答下，故障使系统额外消费的回答 Token”，并明确它不是逐条件新增付费。在线恢复检查的 actual token overhead 与 `wasted_actual_tokens` 分开报告；后者本身仍不能单独证明 AI 已重跑，必须与 fault 后的新 `attempt_id`、新 provider response/provenance 和 actual usage 联合证明。

### Experiment 4：真实回答 trace 驱动的协议消融

模式固定为：

- `FULL`
- `NO_VERIFICATION`
- `NO_PARSER_POLICY`
- `NO_REQUEUE`
- `NO_MERGE_GATE`

Factorization 每档使用共享稳定 hash slice `17/17/16` roots；Lean 每个 paper difficulty 使用固定 5-task `2/2/1` topic slice，共 15 Lean roots。五个 mode 均运行 3 repeats，共 975 root-runs、4,410 planned first-attempt AI units、7,938 provider-attempt upper bound。

五个 mode 的 `ProtocolConfig.max_retries=1`；`NO_REQUEUE` 只关闭 replacement gate。消融必须通过 `ProtocolMechanismPolicy`/runtime hook 作用于真实生命周期：不得根据 mode 名称事后填充错误、stuck 或 premature-merge 指标。

Experiment 4 的正式 baseline 必须是本实验单独执行的 `FULL`：每个 `case_id × repeat_id` 只运行一个 FULL，并由四个消融 mode 共同复用；不得使用 Experiment 1 替代。Experiment 1 只有单 repeat，且正式 replacement policy 与 Experiment 4 `max_retries=1` 不同，不能形成只改变一个协议机制的正式配对。

主结果按同 `case_id × repeat_id` 对 FULL 与各消融 mode 做逐 root 配对。保留四类转移计数（FULL/消融分别正确或未正确），并计算 `end_to_end_success_loss_vs_full = FULL end-to-end success - ablation end-to-end success`、`completion_loss_vs_full = FULL completion - ablation completion`；正值表示删除机制使结果退化。实验性失败保留为 false，不得从预注册配对分母删除；identity/evidence/infrastructure 不完整的 pair 写 `null`、记录原因且不得进入论文汇总。

FULL 与四个消融 mode 在相同 `case_id × repeat_id` 内消费相同 sample/replacement slots，使模型回答差异不再混入机制对比。资源代价按相同 pair 计算 `trace_replay_wall_clock_delta_vs_full`、`trace_attributed_token_delta_vs_full`、`trace_attributed_cost_delta_vs_full`，统一使用“消融值减 FULL 值”，正值表示消融更慢或需要消费更多回答资源。source usage 或时间 evidence 不完整时对应 delta 写 `null`；不得因为消融失败而删除已经发生的 trace 消费，也不得将其称为各 mode 当次实际在线支出。

四个 mode 各自只保留直接专项指标：`NO_VERIFICATION` 报告 `wrong_canonical_acceptance_count/rate`，分母为有独立正确性标签的 invalid candidates；`NO_PARSER_POLICY` 报告 `raw_only_exposure_count` 与 `raw_only_acceptance_count/rate`；`NO_REQUEUE` 报告 `stuck_task_count/rate`，分母为该 mode 全部预注册 root-runs；`NO_MERGE_GATE` 报告 `premature_merge_attempt_count` 与 `premature_merge_failure_count/rate`。不适用或零分母写 `null` 和 applicability reason。

删除把不同 mode、不同分母混在一起的 `exposed_error_count`、`escaped_error_count`、`error_escape_rate`。删除重复别名 `wrong_canonical_count`、`raw_only_count`、`stuck_count`、`premature_merge_count`，只保留上一段的明确名称。FULL 中不得出现 mechanism disabled、wrong canonical、raw-only acceptance、stuck-by-disabled-requeue 或 premature merge 的真实 hook evidence；配置只关闭一个机制、相同题目/模型/request/retry identity、runtime hook 与完整事件链均作为论文资格门禁，不包装成额外指标。

### Experiment 5：四模型 endpoint comparison

- 独立、分层确定性 parent-quarter v4 selection：Factorization hard 42；Lean 三个 hard topic family 各 4，共 54 roots/model-repeat。
- 四模型 × 3 repeats：48 conditions、648 root-runs、4,992 planned first-attempt AI units/provider-attempt upper bound。
- Token ceiling：306,708,480；其中 thinking endpoint 逐 attempt 包含 4,096 prompt + 32,768 visible completion + 32,768 thinking 上界，nonthinking endpoint 不含 thinking 上界。
- Experiment 5 固定不做协议 replacement/retry：`ProtocolConfig.max_retries=0`、`replacement_attempts_allowed=false`，每个 AI unit 最多只有一次 provider attempt。论文不得报告 recovery、重试次数、重试成功率或“重试用尽后的正确率”；最终结果只表示首次节点输出经过相同 parser/verifier/checker、canonical 与 merge 流程后的 root 结果。
- 所有 entry 固定 provider-config namespace、entry、configured/resolved model、reasoning、cohort/config/endpoint digests；调用后 `missing_resolved_model` 或 mismatch 必须使 attempt paper-ineligible，并停止该 condition 尚未执行的 units。
- Experiment 5 正文不做六个无序 model pair、pairwise significance、胜负排名或合成总分。主结果按 model 汇总到两张表；domain/topic/repeat 明细和 failure taxonomy 保留为附录或可追溯数据，不另造主指标。
- 质量与最终结果表固定报告：
  - `first_attempt_nonpass_rate = first_attempt_without_verifier_accepted_candidate_count / actual_first_provider_attempt_count`。分子是首次真实 provider attempt 没有直接产生 verifier/checker 接受候选的次数；必须按互斥优先级保留 provider/transport failure、parse/schema unusable、verification/checker rejection 三类原因，不能把基础设施失败伪装成模型自然错误。
  - `first_attempt_verification_rejection_rate = first_attempt_explicitly_rejected_by_verifier_count / first_attempt_checkable_candidate_count`。分母只包含首次调用中已正常返回、可解析并实际到达 verifier/checker 的候选；该指标只能表述为“首次输出验证拒绝率”，不得命名为自然错误检出率或声称覆盖逃逸错误。
  - 全局 `completion_rate` 与 `end_to_end_verified_success_rate`，分母均为该 model/report slice 的全部预注册 root-runs。错误最终结果、没有最终结果和提前停止均按第 2 节口径保留，不能从分母删除。
- 调用量与资源表固定报告：planned first-attempt AI units、actual first provider attempts、`first_attempt_call_coverage = actual_first_provider_attempt_count / planned_first_attempt_ai_unit_count`、actual total tokens、冻结价格快照下的 cost estimate 与真实 end-to-end wall-clock。由于不重试，actual provider-attempt 总数与 actual first provider attempts 是同一数量，不得重复包装成两项指标。
- 计划 AI unit 数在四个 model arm 中相同，但实际 provider attempts 可能因 Factorization 自然早停或 condition-local fail-stop 而不同；必须同时显示 planned 与 actual，不能把提前失败导致的少调用解释成更省资源。provider error/timeout/429 与 parse failure 保留为首次未通过原因或附录明细；provider latency 保留为审计/解释性数据，不进入正文最小指标表。
- 三个 repeat 的原始结果必须保留；正文 model 汇总显示三次 repeat 的汇总值并附 repeat 间范围，不再生成模型两两检验。`accepted_validity_rate` 及其他与全局端到端正确率重复的旧名退出论文指标集合。
- model identity、usage 完整性、固定价格快照、实际零 retry、预注册顺序/并发和完整 event/artifact 链只作为论文资格门禁，不包装成模型性能指标。后续可以把这两张表制作成论文比较图，但图形只能呈现上述冻结指标，不得改变分母、隐藏失败或派生新的综合评分。

首次 8-root smoke 是产生 endpoint smoke evidence 的 bootstrap，固定 regression-only；正式矩阵 preflight 必须要求四 member 的 artifact-backed capability/identity/smoke evidence。

## 5. 总预算口径

| 范围 | Root-runs | First-attempt AI units | 原全在线 provider-attempt upper bound / trace-slot capacity |
|---|---:|---:|---:|
| P0-core（Exp1–4） | 5,736 | 35,528 | 76,280 |
| P0-full（Exp1–5，Exp5 selection v4） | 6,384 | 40,520 | 81,272 |

逐实验精确值：

| 实验 | Root-runs | First-attempt AI units | 原全在线 provider-attempt upper bound / trace-slot capacity |
|---|---:|---:|---:|
| Experiment 1 | 435 | 1,970 | 1,970 |
| Experiment 2 | 600 | 12,000 | 12,000 |
| Experiment 3 | 3,726 | 17,148 | 54,372 |
| Experiment 4 | 975 | 4,410 | 7,938 |
| Experiment 5 | 648 | 4,992 | 4,992 |

Root-run、trace-slot consumption 和 provider call 是三种不同数量。上表第三列继续保存 EPD-026 原全在线方案的最坏 provider-attempt 上界，并作为两阶段方案的最大 trace-slot 容量参考；采用 EPD-027 后不得把 `76,280` 解释为预计在线调用量。当前按语义请求去重和最大 replacement 深度审计得到约 `6,904` 次 DeepSeek acquisition 加在线检查的量级，但稳定 outbound-body digest、完整 bank inventory、Exp2 缩小题集和 Exp3 在线检查规模尚未冻结，因此该数不能写入正式预算或 approval digest。Exp5 的 4,992 次 SiliconFlow 上界继续单列。

Runner 必须先生成 `run_budget.json`；两阶段设计还必须生成独立的 `response_bank_inventory.json` 与 acquisition budget，至少包含 roots、AI units、trace slots、unique outbound-body digests、sample/replacement slots、online canary calls、tokens/cost/time/space estimate、quota/rate-limit preflight、模型与并发。人民币 `1,000` DeepSeek 目标硬停止线必须把已发生与 in-flight 悲观预留同时计入 calls/tokens/CNY；usage 缺失或预算不足时停止新 provider dispatch、正常收口已有 evidence。bank 不完整时依赖条件 `blocked`，不得静默删实验、mode、difficulty、topic、roots、repeat 或 retry slot。

`--unlimited-budget` 不得用于 response-bank acquisition 或在线检查绕过人民币 `1,000` 用户硬停止线；若未来仍保留该参数，它只适用于不产生 provider 调用的 trace-backed protocol run，并仍保留 trace-slot、请求控制、preflight 与全部 identity。

## 6. 状态、输出与可追溯性

稳定终态：

- suite/experiment/condition/run：`planned`、`running`、`completed`、`completed_with_failures`、`blocked`、`budget_exhausted`、`failed`、`incomplete`。
- root：`completed`、`failed`、`blocked`、`timeout`、`budget_exhausted`、`ineligible`、`not_started`。
- attempt：`succeeded`、`model_identity_mismatch`、`provider_error`、`parse_failed`、`verification_rejected`、`checker_rejected`、`lease_expired`、`late_rejected`、`worker_died`、`cancelled_by_budget`、`executor_error`。

实验失败且 evidence 完整时用 `completed_with_failures`，继续剩余预注册条件。配置/catalog/selection/budget/model/evidence identity 漂移是 `blocked`，停止新 API 调用并尽可能闭合未开始记录。`incomplete` 只用于进程/机器中断或终态持久化失败。

正式输出至少包含：

```text
suite_manifest.json
experiment_manifest.json
condition_manifest.json
run_budget.json
input_catalog_manifest.json
runs/<condition>/<case>/run_manifest.json
runs/<condition>/<case>/events/event_log.jsonl
runs/<condition>/<case>/artifacts/artifact_index.jsonl
per_task_results.jsonl
per_attempt_results.jsonl
model_execution_records.jsonl
fault_injections.jsonl
response_bank_inventory.json
response_bank_acquisition_records.jsonl
trace_consumption_records.jsonl
metrics/paper_table_feasibility.csv
metrics/paper_plot_scalability.csv
metrics/paper_plot_robustness.csv
metrics/paper_table_ablation.csv
metrics/paper_table_model_endpoint_comparison.csv
audit/paper_eligibility_report.json
audit/replay_report.json
```

每个 summary 数字必须能回到 condition/run/task/attempt、event refs 和 artifact refs；trace-backed 结果还必须继续回到唯一 source bank entry 及其真实 acquisition request/raw/provenance/usage。acquisition actual spend 与各条件 trace attribution 必须分账，不能重复记账。缺少 timing、identity、fault、merge、bank source 或分母 evidence 时写 `null`/reason，并使相应行不合格；不得用 mode/fault 常量、固定时间或现有记录数量填补。

## 7. Smoke、路径与 replay

- `paper_smoke_exp3_exp4_profile.v1.json`：Exp3–4-only，11 roots，显式省略 baseline，只作 regression。
- `paper_smoke_exp5_profile.v4.json`：Exp5-only 8-root bootstrap，suite=`paper_smoke_exp5_v4`，canonical profile digest=`sha256:ef3b46948dee69ff640d4e21a25c3d84979045e2a8be93d0429fd9474e6b0dd6`，tracked 文件原始字节 digest=`sha256:8378ce5c5a3c76339344088a36f995eda5c862059b9c9eecf5e34347985de5df`；四模型各 1 Factorization + 1 Lean。identity-only 另输出 8-item `selection_inventory` 的 bundle digest=`sha256:eb1a7c33103fe4ef514627c8bf905de5d179e5547d98328fd17b00b62591e9b1`，它不是 v4 selection digest。历史 v3 profile 只供 replay。
- `paper_smoke_profile.v3.json`：29-root 综合兼容 profile；它不是正式矩阵，任何 v2-derived 兼容字段不得覆盖本规格。
- Exp1–4 v3 launcher 与历史 smoke 只作回归/诊断；所有结果固定 paper-ineligible。

EPD-027 生效后，现有 smoke/launcher 不能充当 response-bank、六档 Exp2 在线并发检查或 Exp3 在线恢复检查的正式身份。新的 bank acquisition、trace-backed 主矩阵和两类在线检查必须在实施计划中各自冻结 profile、预算、output identity 与 paper eligibility；在此之前不得启动旧的“下一次 smoke”流程。

普通实验 CLI 的新运行默认写入仓库同级 `TokenShareData/outputs/experiments/`；paper runner 必须显式指定全新 `--output-root`。execution-plan 与 budget digest 绑定实际绝对 output root，新 root 必须重新生成 identity。历史 output 迁移后内容和 digest 不改写、不得 resume；读取旧仓库绝对、`outputs/...` 仓库相对或 `runs/...` suite 相对 artifact root 时，只允许通过 `runtime_paths.resolve_persisted_data_path()` 做只读解析。

Smoke/resume/replay 不得改写已闭合历史 evidence。任何 replay 测试先复制代表性输出到隔离临时目录。

## 8. 当前实现与启动门禁

当前已实现：

- Factorization/Lean FULL 路径进入 system coordinator/`ProtocolEngine`；插件拥有领域 split/parser/verifier/checker/merge。
- Exp2 20-way、真实 worker timing/concurrency/早停投影；Exp3 五类 fault、shared Exp1 reference、真实 process death；Exp4 五模式 runtime hooks；Exp5 cohort v3 + selection/smoke v4 identity/sequence/budget、artifact-backed 8-root smoke evidence validator 和 production execute/replay renderer。
- 正式 metrics 从持久化 condition/task/attempt/event/artifact/runtime facts 复算；capturing/scripted 路径保持 paper-ineligible。
- 2026-07-31 用户启动的真实 Exp3/4 smoke 已完成 11 roots/36 DeepSeek HTTP 200 attempts/839,054 tokens，得到 8 completed 与 3 evidence-complete experimental failures；真实性与 TokenShare lifecycle 审计通过，但该历史输出的 canonical ledger/provenance/timing/model inventory/replay 审计失败，保持只读且不得用于论文。
- 同日修复已使 no-return artifact 链、LedgerEvent 原文/hash、真实 timing/critical-path/provider latency、model inventory、独立 replay、smoke missingness 与基础设施退出码 fail closed；修复本身只运行离线/定向验证，没有再次调用 provider。Exp5 condition-local identity fail-stop、固定 648-root 分母/missingness、真实 transport taxonomy、6 audit + 8 paper artifacts 和固定参数 smoke launcher 继续有效。

尚未实现 EPD-027：当前 `ai_api_replay`/formal replay 只恢复或复算既有 evidence，不会以回答库输入重新驱动完整状态机；当前 `ExecutionRequest`/prompt identity 也没有可跨 condition 稳定复用的 outbound-body digest。trace-backed executor、bank schema/inventory、双重 provenance、两类 paper eligibility、acquisition/attribution 分账、六档 Exp2 在线检查和 Exp3 在线恢复检查均是待实施范围。因此此前“代码门禁已闭合”的表述只适用于 EPD-026 的旧全在线设施，不表示新的两阶段设施已完成。

当前 readiness：

- P0-core 尚未产生可发布结果；上述 Exp3/4 smoke 虽是真实调用，但 canonical 论文证据无效，而且不能作为 EPD-027 回答库 acquisition 或在线恢复检查复用。
- P0-full 仍为 **NO-GO**：EPD-027 是尚未实现的全面实验设施改造，旧 P0-core/P0-full plan 与 smoke 顺序暂停；新的完整实施计划确认、trace 设施验证和两类在线检查身份冻结之前不得启动 provider dispatch。Experiment 5 的四 member real-transport v4 8-root smoke 要求继续有效，但执行顺序须由新计划统一安排。
- transport timeout/connection/rate/provider/auth/client taxonomy、canonical v3 blocked 诊断、失败分母与 nullable usage 已修复；TTFT 因没有持久化来源继续保持 unavailable，不得填 0。
- 五次最小 endpoint/key 诊断不是 artifact-backed capability/smoke evidence，不能解除门禁。

因此当前文档不提供可直接复制执行的正式 Exp1–5 命令。先使用：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments --help
```

实现者必须先完成相应 RED→GREEN、plan-only、identity/budget/output-contract preflight；真实 API 会产生费用，未获用户明确授权不得启动 smoke、pilot 或 formal matrix。

## 9. 验收标准

实验设施或正式运行只有在以下条件满足时才能交付：

1. 本文与参数台账、tracked config、catalog/selection、代码和测试一致；不存在另一个活跃实验设计。
2. Exp1–5 的 frozen 数量、模型、请求控制、repeats、fault/ablation 和预算精确匹配。
3. `online_real_provider` 结果来自当次批准的真实 API；`real_model_trace_protocol_run` 的每个 slot 来自批准且不可变的真实 API acquisition，并完整经过当次协议生命周期。两类 evidence 不混写、不重复计算 actual spend。
4. Stored-evidence replay 不调用 provider；trace-backed protocol run 只消费冻结 bank；历史 evidence 不改写；secret scan 无命中。
5. 每个表图可追溯到逐 task/attempt/event/artifact，失败和 missingness 保留在分母中。
6. 相关定向测试、`.\init.ps1` 和交付前 `.\init.ps1 -Full` 通过；Lean 共享输入变化时按 verification profile 增量审计。
7. EPD-027 的 response-bank inventory、trace-backed executor、在线检查、预算硬门与 paper eligibility 已按获批实施计划实现并验证；在此之前正式矩阵保持 NO-GO。
8. `progress.md`、`feature_list.json`、`session-handoff.md` 与当前 code map 已同步，旧实施计划只在 archive 中作为 provenance。
