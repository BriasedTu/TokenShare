---
status: user_approved
document: slim_v2_experiment_metrics_authority
scope: TokenShare Slim V2 Experiment 1-5 experiments and metrics only
last_updated: 2026-08-23
---

# Slim V2 实验与指标权威

本文只回答一个问题：论文最终需要执行哪些实验，以及每个实验必须计算哪些指标？本文不定义系统接线、执行程序或实现计划。

本草案从以下当前材料提取参数，不以久未更新的基线测试结果作为判断依据：

- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- `benchmarks/paper/paper_metric_contract.v1.json`
- `benchmarks/paper/paper_suite_scale_profile.v1.json`

并纳入用户于 2026-08-20 明确冻结的 Slim V2 覆盖规则：Experiment 2–4 复用 Experiment 1 正式运行自然产生的 per-unit attempt traces、`relative_range=(max-min)/mean`、除 Experiment 2 外 `worker_count=10`、取消 Experiment 2/3 在线检查。Experiment 2 不再使用独立 20-way split，而是完整继承 Experiment 1 的任务计划；下游实验自己的 `repeat_id` 与固定为 0 的 `source_repeat_id` 分离。Experiment 4 另按用户同日批准的挑战驱动两两完备消融设计执行：FULL、四个单机制消融和六个双机制消融共享 mode-blind 确定性挑战，不研究不同错误率。同日另冻结 DeepSeek/SiliconFlow 官方价格、reasoning token 归属和 roots 串行生命周期口径。若旧材料与这些覆盖规则冲突，以本节列出的用户决定为准。

## 1. 统一统计口径

### 1.1 指标等级

- **必须指标**：必须出现在 reducer 生成的 CSV/JSON 中。包括保留 table scope 内机器指标合同标为 `primary`、`secondary` 或 `auxiliary` 的字段；用户已取消的两个在线检查 table scope 按第 7.2 节退出。计数型 `secondary` 字段同时给出比例的 numerator 和 denominator，不能省略。
- **仅诊断字段**：必须保留在原始 JSONL 或诊断明细中，但不进入最小论文指标表。机器合同标为 `audit_only` 的逐观察值也归入此类。
- **已退出指标**：不得重新包装、改名后进入论文结果。

### 1.2 固定分母、缺失与失败

令一个报告 slice 中的全部预注册 root-run 集合为 `R`：

- `preregistered_root_count = |R|`，预注册后固定；单个 root 失败、未返回、超时、worker death 未恢复或提前停止都不得从 `R` 删除。
- `final_result_root_count = |{r∈R : r 有最终 root 结果}|`。
- `verified_correct_root_count = |{r∈R : r 的最终结果经独立领域检查为正确}|`。
- 每个报告 cell 必须同时给出三个库存计数：`preregistered_root_count`、`scientifically_valid_root_count` 与 `infrastructure_invalid_root_count`，并满足 `scientifically_valid_root_count + infrastructure_invalid_root_count = preregistered_root_count`。这三个计数都必须报告，不能通过删除无效 root 缩小预注册库存；预注册但缺少 committed root result 的成员在 reducer inventory 层计入 `infrastructure_invalid_root_count`，诊断为 `missing_committed_root_result`。
- 仅当 `infrastructure_invalid_root_count=0` 时，`completion_rate = final_result_root_count / preregistered_root_count`，`end_to_end_verified_success_rate = verified_correct_root_count / preregistered_root_count`。cell 中只要存在一个 infrastructure-invalid root，两项 rate 及依赖该 cell 的相关科学 rate/effect 都写 `null`，`missing_reason=infrastructure_invalid_root_present`；不得改用 `scientifically_valid_root_count` 作为新分母。已经持久化且输入完整的精确事实仍必须报告，包括 inventory、attempt/fault/replacement 数量以及 token、cost、wall-clock 等资源事实；不得用整段 early return 把这些事实一并清空。只有依赖科学有效性的 rate/effect 和其区间传播该 reason，单个精确事实若自身必需输入缺失则仍按本节普通缺失规则写 `null`。
- root 顶层失败分类保持且只允许 `no_final`、`incorrect_final`、`infrastructure_invalid`，`failure_root_count` 是三类之和。三个 breakdown count 必须直接按已提交 `failure_kind` 分组，禁止再从 `final_result_present`、`verified_correct` 或 `failure_origin` 反推分类；三类互斥且其和等于 `failure_root_count`。细分诊断写入独立 `failure_origin`，不得扩展顶层 taxonomy。Factorization 与 Lean 中的 parser 重试耗尽、环境正常时 verifier/checker 拒绝耗尽、provider/transport 耗尽或这些候选取得失败的混合耗尽都是有效实验 `no_final`，对应 `failure_origin` 分别为 `model_parse_exhausted`、`model_verification_exhausted`、`provider_transport_exhausted`、`mixed_candidate_acquisition_failure`，进入固定分母且成功值为 0。
- Lean checker 的 `environment_error`、`timeout`、`helper_error` 是基础设施终态：首次出现即停止当前 root，不继续消耗模型 retry；root 投影为 `infrastructure_invalid`、`failure_origin=checker_environment_error`。独立 Lean environment pass 缺失/失效必须在 provider 前只把全部 pending Lean ordinary/reference roots 形成 `failure_stage=preflight` 的 infrastructure-invalid；同 run 的 Factorization roots 继续，fixed-source closure 排除这些 Lean-invalid keys。Lean import/toolchain/project/checker 环境、store/ledger、冻结接线或未知程序异常同属 infrastructure-invalid，不得伪装成模型失败。
- ratio 的 denominator 为 0 时写 `null` 且同一 metric 写 `missing_reason=zero_denominator`，不得写 0；sum/elapsed/delta 任一必需输入缺失时写 `null`，不得把缺失 token、cost 或时间当成 0。metric 后续从 `null` 变为计算值时必须同步移除该 metric 的旧 `missing_reason/not_applicable_reason`，不能同时保留值和过期原因。
- ordinary root result schema 从本次新实验 run 起固定为 `tokenshare.slim_v2.root_result.v2`：`failure_origin` 是必需出现、可为 `null` 的字段。v1 不做兼容读取、迁移或双版本 reducer；后续正式数据必须使用新的 run ID/目录从头执行。v2 仍必须支持同一 run 内的 crash/resume，这一运行恢复不等于旧 v1 数据迁移。
- 配对指标只接纳同一 `case_id × repeat_id`、两端均非 infrastructure-invalid 且所需字段完整的 pair；任一端为 infrastructure-invalid 时必须记为 ineligible。未接纳 pair 保留在 `planned_pair_count`、`eligible_pair_count` 和 `ineligible_pair_count` 三个数量及原因分布中，不得删除或当成失败值 0。
- Experiment 2–4 的来源键固定为 `case_id × source_repeat_id × planned_ai_unit_id`，其中 `source_repeat_id=0`。下游 root 的 `repeat_id` 是实验重复，不进入来源键；该键也不包含 `condition_id`、worker、fault、mode 或 attempt ordinal。同一键指向 Experiment 1 的唯一一条 per-unit trace；该 trace 可以来自正常协议阶段（`trace_origin=protocol`）或紧随该 root 的补齐阶段（`trace_origin=coverage_tail`）。
- **选择性Exp1 coverage policy**：同一 profile 的 Exp2、Exp3、Exp4（包含它们实际调度的 reference）将消费 trace 的 `case_id` 并集是唯一的tail-required集合；它由冻结inventory实际派生，Full当前验证为99，禁止硬编码该数。只有该集合中的Exp1来源root须为全部planned unit形成strict trace closure；非来源Exp1 root在协议自然terminal后不得调用tail，必须以`trace_tail_status=not_required_by_downstream`、空target/recorded IDs和零tail资源提交，同时保留protocol attempts及`unscheduled_ai_unit_ids`。Exp5 V4 supplemental只读committed `RootResultV2`，不读`UnitTrace`，绝不加入集合。
- 下游请求的 `attempt_ordinal` 若在 per-unit trace 中存在，读取同 ordinal 的自然 attempt；若不存在，确定性读取该 trace 中最大、也就是最后一个已有自然 ordinal。`source_attempt_ordinal` 保存实际选中的自然 ordinal，`source_attempt_fallback_used` 标明是否回退；回答内容、source usage、source latency、source cost 和 source result kind 均保持选中 trace attempt 的原值。Experiment 3 的 token/latency 扰动身份仍使用下游当前 `attempt_ordinal`，而不是回退后的 `source_attempt_ordinal`。
- 来源命中后必须比较普通语义字段：Factorization 比较 `candidate_start/candidate_end`，Lean 比较 `lemma_node_id/dependency_path`。字段不一致不得消费，也不得退回实时 API；这里不使用 hash、digest 或证据链。
- Experiment 4 的挑战计划键固定为 `case_id × repeat_id`，其值包含唯一 `challenge_family`、目标 `planned_ai_unit_ids` 和 attempt ordinal 规则。挑战计划先于 mode 展开，同一键在 FULL、四个单机制和六个双机制 mode 中必须完全相同；注入器不得接收或读取 mode、disabled mechanisms 或 `ProtocolMechanismPolicy`。
- Experiment 4 若在协议 root 生命周期开始前因 mode 配置或 preflight 被拒绝，该 root 仍保留在预注册库存并记为 `infrastructure_invalid`，但不能把它当成 ablation failure。受影响的 mode × challenge × domain 报告 cell 的性能差值和机制贡献指标必须为 `null`。只有协议已经开始后形成的 `no_final`、`stuck`、`incorrect_final` 或 root checker rejection 才是消融结果。
- 所有 roots 在 runner 层串行执行；任一时刻最多只有一个 root 处于协议生命周期内。`worker_count` 只控制当前 root 内部 AI units 的并发，不允许把多个 roots 并发运行。
- 所有时间以毫秒记录。`root_start_at_ms` 是 root 协议生命周期开始且任何 AI 子任务调度之前的时刻；`root_terminal_at_ms` 是 root 完成或失败终止之后的时刻；`runtime_wall_clock_ms=root_terminal_at_ms-root_start_at_ms`。Experiment 1/5 使用真实 wall clock，Experiment 2–4 使用逻辑调度器推进后的同一逻辑时钟；不得把 worker 首次开始时间冒充 root start。
- Experiment 2–5 跨 roots 的批次 elapsed 仍为 `max(root_terminal_at_ms)-min(root_start_at_ms)`。Experiment 1 是唯一例外：coverage tail 位于上一个 `root_terminal_at_ms` 与下一个 `root_start_at_ms` 之间，正文协议 wall-clock 必须使用 `Σ runtime_wall_clock_ms`，不能使用会混入 tail 的 `max-min`；tail 自己的真实耗时另存 `trace_tail_wall_clock_ms`。

### 1.3 方差、离散程度与 95% 置信区间

本节只增加 reducer 汇总，不改变任何实验运行。不得为统计报告新增 experiment、condition、数据集、模型、repeat、fault、ablation mode、root、provider call 或 source trace attempt；也不增加第 8 节的原始 JSONL 字段。全部统计量必须从本文件已经要求保存的 root、attempt、pair、quadruple 和时间字段离线派生，因此新增预算为 0。

#### 1.3.1 统一估计单位与 bootstrap

1. 置信区间统一采用 **95% 分层 case-cluster percentile bootstrap**。抽样簇固定为 `case_id`，不是单条 attempt、candidate、slot 或同一题的 repeat。这样 Full 中大量不同题目都参与不确定性估计，同时不会把同一题的重复运行伪装成相互独立样本。
2. 若报告 slice 已固定 `repeat_id`，簇内只携带该 repeat 的全部相关行；若跨 repeat 汇总，则抽中一个 `case_id` 时必须携带该题的全部 repeats。配对或交互指标还必须同时携带该题的全部必要 arms：Experiment 2 的 worker=1/worker=k、Experiment 3 的 fault/reference、Experiment 4 的 FULL/ablation pair 或四端 matched quadruple、Experiment 5 同一 model 下的三个 repeats。不得拆散后独立重采样。
3. 当一个汇总跨越 `domain`、`difficulty`、Lean `topic_family` 或 Factorization `position_stratum` 时，在这些冻结 strata 内分别有放回抽取与原 stratum 相同数量的 `case_id`，再合并计算。condition、worker、fault、mode、challenge 或 model 已作为报告 slice 固定时不另作跨层混抽。
4. 固定 `bootstrap_replicates=10000`、`bootstrap_seed=20260820`。每个 bootstrap replicate 必须从重采样后的原始观察重新计算 numerator、denominator、median、ratio、paired delta 或 interaction；禁止对已经聚合的 cell 均值、中位数或三个 repeat 汇总值再次 bootstrap。
5. 点估计沿用各指标原公式和固定分母。95% 区间取有效 bootstrap 估计的第 2.5 与 97.5 百分位；`bootstrap_variance=Σ_b(θ_b-mean(θ_b))²/(B_valid-1)`，`bootstrap_standard_error=sqrt(bootstrap_variance)`。rate 的 replicate 必须重算 numerator/denominator，不能把单个 candidate、attempt 或 slot 当独立样本。
6. 每个有区间的指标必须同时输出 `<metric>_ci95_low`、`<metric>_ci95_high`、`<metric>_bootstrap_variance`、`<metric>_bootstrap_standard_error`、`case_cluster_count`、`valid_bootstrap_replicate_count`，并在表级元数据记录 `ci_method=stratified_case_cluster_percentile_bootstrap`、`ci_level=0.95`、上述 replicate 数和 seed。
7. `case_cluster_count<5` 时区间、bootstrap variance 和 standard error 均为 `null`，并写 `missing_reason=insufficient_case_clusters_for_interval`。若 10,000 次中有效估计少于 9,500 次，区间及 bootstrap variance/standard error 为 `null`，写 `missing_reason=insufficient_valid_bootstrap_replicates`。原指标点估计仍按原缺失规则报告。
8. 这里的区间表示“冻结 benchmark 中不同题目/case 带来的不确定性”，不宣称估计同一道题重复调用模型时的随机性，也不代替外部总体推断。

#### 1.3.2 连续值与逐 root / pair 离散程度

对本节及各实验下文指定的逐 root、逐 matched pair 或逐 matched quadruple 标量，必须报告 `median`、`q25`、`q75`、`iqr=q75-q25`、`sample_variance` 和 `sample_stddev`。分位数固定使用 Hyndman–Fan type 7（等价于常用 `linear` quantile）。样本方差与标准差为：

- `sample_variance=Σ_i(x_i-mean(x))²/(n-1)`；
- `sample_stddev=sqrt(sample_variance)`；
- `n<2` 时二者为 `null`，并写 `missing_reason=insufficient_observations_for_sample_variance`；
- 中位数的 95% 区间仍按第 1.3.1 节重采样 `case_id` 后重算中位数，不使用正态近似。

`preregistered_root_count`、各种 planned/executed/unscheduled/call/slot/reassignment/transition 数、planned/eligible/ineligible pair 或 quadruple 数、批次 wall-clock、三个 repeat 的 min/max/range、`kill_progress_error_signed_max_pp`、`actual_total_tokens` 和 `actual_cost_estimate_cny` 等固定库存或精确总量不加置信区间。若需反映其波动，使用下文规定的逐 root 或逐 pair 派生值，而不是给精确总量套区间。

以下表中，“原始字段”均指第 8 节定义的 Slim V2 JSONL 叶字段。

### 1.4 冻结价格与 token 计费口径

前向成本换算按 experiment/provider 使用两个不可变版本：Experiment 1 使用 `pricing_version="slim_v2.pricing.2026-08-23"` 的 Flash 表；Experiment 5 保留 `pricing_version="slim_v2.pricing.2026-08-20"` 的 SiliconFlow 表。它们只是 reducer/projector 使用的普通静态价格表，不是预算、审批、receipt 或执行门禁；价格变化不得阻止实验运行。Flash 价格的直接用户来源见 `Doc/SlimV2/slim_v2_flash_pricing_source_20260823.md`；2026-08-20 的官方页面记录仅保留为 Experiment 5 与既有 Pro 事实的历史来源。

#### 1.4.1 DeepSeek Experiment 1（前向 Flash）

前向本地 entry `deepseek_v4_flash_exp1_baseline` 对应 API model `deepseek-v4-flash`。用户于 2026-08-23 直接冻结人民币单价：

| 计费项（CNY / 1M tokens） | `pricing_tier` |
|---|---:|
| input，cache hit | 0.05 |
| input，cache miss | 1.50 |
| output | 4.50 |

该 Flash 价格版本固定为 `slim_v2.pricing.2026-08-23`，仅有 `pricing_tier=flat`；不得沿用或按比例换算此前 Pro 的 peak/off_peak 表。真实 attempt 仍保存 `provider_request_started_at_utc` 作为调用事实，但它不参与 Flash 的价格档选择。DeepSeek Flash 实际成本为：

```text
cost_estimate_cny = (
    prompt_cache_hit_tokens  × cache_hit_rate
  + prompt_cache_miss_tokens × cache_miss_rate
  + completion_tokens        × output_rate
) / 1_000_000
```

`prompt_tokens` 必须等于 cache hit 与 miss 两项之和；任一实际调用缺少 cache split、`completion_tokens` 或价格版本时，attempt cost 为 `null` 并记录 `usage_status/missing_reason`，不得猜测。

#### 1.4.2 SiliconFlow Experiment 5

SiliconFlow 以官方专用价格页 `https://siliconflow.cn/pricing` 为价格权威，并用用户登录后的官方模型详情页逐项交叉核对；固定人民币单价为：

| API model（CNY / 1M tokens） | input/cache miss | output | cache hit |
|---|---:|---:|---:|
| `zai-org/GLM-5.2` | 8.00 | 28.00 | 2.00 |
| `Qwen/Qwen3-14B` | 0.50 | 2.00 | 无独立价 |
| `MiniMaxAI/MiniMax-M2.5` | 2.10 | 8.40 | 0.21 |

有独立 cache-hit 价的 endpoint 按 provider 返回的 `prompt_cache_hit_tokens/prompt_cache_miss_tokens` 分项计算；缺任一分项则 cost 为 `null`。Qwen3-14B 的官网 cache 列未列独立价格，不能解释为免费：其全部 `prompt_tokens` 均按 input 0.50 计算。三个 Exp5 endpoint 的 output 均按 `completion_tokens` 计算。

#### 1.4.3 reasoning tokens 不重复计数

DeepSeek 与 SiliconFlow 的官方 response schema 都把 `reasoning_tokens` 放在 `completion_tokens_details` 中；它是 `completion_tokens` 的子集，不是额外加在 completion 之外的 token。所有实际与模拟成本都按完整 `completion_tokens` 使用 output 单价，禁止再加一次 `reasoning_tokens`。若需要拆分诊断，定义：

```text
nonreasoning_completion_tokens = completion_tokens - reasoning_tokens
generated_tokens = nonreasoning_completion_tokens + reasoning_tokens
                 = completion_tokens
```

`reasoning_tokens` 缺失时只表示无法做该诊断拆分；只要 provider 的 `completion_tokens` 完整，仍可计算 output token 和成本。

#### 1.4.4 2026-08-23 前已启动 Representative 的事实边界

`slim-v2-representative-real-20260822-144900-ef9128fe` 在本次前向切换前已经持久化的 Experiment 1 traces 事实为 `deepseek-v4-pro` 与 `slim_v2.pricing.2026-08-20`。这些已发生事实不得重写、重价、重标为 Flash，也不得向同一 Experiment 1 事实集混入或重复新的 Flash provider 调用。

经 Stage 7.1 独立法定人数 `3/3`，该 exact run 只能在窄的 frozen-v2 上下文中为已知 projector/recovery 修复执行同一 run 的 `--resume`；它不是前向 Flash Experiment 1 结果，不能作为 post-change Flash 结果的可比或可发表证据。此项只是结果解释的真值标签，不创建价格、发布或运行 gate。

## 2. Experiment 1：跨领域可行性与难度

### 2.1 研究问题与条件

- **研究问题**：真实 AI 在 Factorization 与真实 Lean checker 两个领域中，随难度与 Lean topic family 变化，能否完成并得到正确的最终 root 结果；时间与资源代价是多少。
- **condition 维度**：`domain × difficulty × topic_family × repeat_id`。Factorization 的 `topic_family=null`。
- **case/root**：Factorization 当前稳定 hash corpus 的 easy/medium/hard 各 100，共 300；Lean 为 3 个 `paper_difficulty` × 3 个 `topic_family` × 每格 15，共 135；总计 435 roots。
- **repeat**：每题 1 次，`repeat_id=0`，`seed=1`；总计 435 root-runs、1,970 planned first-attempt AI units。
- **worker**：`worker_count=10`，不是新的实验维度。
- **provider/model**：官方 DeepSeek；前向 entry=`deepseek_v4_flash_exp1_baseline`，model=`deepseek-v4-flash`，thinking enabled，`reasoning_effort=high`，`timeout_seconds=600`，`max_tokens=300000`，每个实际执行的 AI unit 最多 3 次自然 provider attempts；provider 全局 in-flight 上限 50；成本按第 1.4 节冻结的 `slim_v2.pricing.2026-08-23` Flash flat 表计算。第 1.4.4 节的 exact historical run 只保留其已持久化 Pro 事实，不构成前向例外。
- **阶段一：正常协议阶段**：调用一次现有 `run_root()`。Factorization 找到第一个正确因数后按现有语义完成 root；Lean 按现有 proof/root 终态语义完成或失败。`root_terminal_at_ms` 在 `run_root()` 返回并形成协议终态时记录；正常协议阶段实际执行的 unit 写唯一 per-unit trace，`trace_origin=protocol`。正文正确性、完成率、协议时间、provider latency、token 与 cost 只消费这一阶段。
- **阶段二：选择性逐 root coverage tail**：仅tail-required来源root在`run_root()`返回后读取其`unscheduled_ai_unit_ids`，保持该冻结observation原始顺序（不再按文本或数字重排），只对尚无protocol trace的planned units执行既有Slim-local trace acquisition。来源root的有效`no_final/incorrect_final`或其他`terminal_failure.infrastructure_invalid=false`协议终态仍必须执行tail；只有`slim_condition_failure`、`slim_runtime_failure`或`terminal_failure.infrastructure_invalid=true`阻断。每个target继续使用相同unit input、prompt、provider/model与请求控制，写唯一`trace_origin=coverage_tail` trace，不得重复正常协议unit或创建独立回答库。非来源root不生成未调度unit trace。
- **tail attempt 规则**：每个 coverage-tail unit 从自然 `attempt_ordinal=0` 开始，按 Experiment 1 相同的 parse/verifier/checker 接受规则，在失败或拒绝时继续取得下一自然 attempt，首次 accepted 后停止，最多三个 attempts。tail 只为决定是否继续 acquisition 而运行 parser/verifier/checker，不创建 protocol attempt、canonical、merge 或新的 root 终态，也不修改已经完成的 `ProtocolRunResult`。Lean 依赖 canonical 不可用而无法构造合法 request 时，保存带真实 `lemma_node_id/dependency_path`、`provider_call_made=false` 的 typed `pre_dispatch_failure` trace；其他 provider/transport 连续失败同样保存真实失败 attempt/result kind，不伪造回答。
- **阶段与串行边界**：来源root的coverage tail在其`root_terminal_at_ms`之后开始，完成或按上限终止后才允许下一root记录`root_start_at_ms`；非来源root在协议terminal后直接提交。roots仍串行，tail不延长该root的`runtime_wall_clock_ms`。所有Exp1结果仍保存完整tail字段：非来源为`not_required_by_downstream`、空IDs与零资源；来源root单独保存真实tail wall/provider attempt/token/cost，且只聚合`provider_call_made=true`的attempts。它们均不进入Experiment 1正文资源指标。
- **覆盖完成与下游closure**：每个tail-required来源root在阶段二结束后，每个planned unit必须有且只有一条per-unit trace，且至少有一个自然attempt记录；记录可以是明确provider/pre-dispatch failure，不保证可用回答。Experiment 2–4对其所需来源root保持strict source closure：任何planned trace缺失都fail-closed，并原样消费选中`protocol/coverage_tail` trace的success或failure result kind。非来源root不宣称已覆盖。
- **最终投影与恢复等价**：来源root最终v2 `attempts[]`按冻结顺序由protocol projection attempts加当前root已持久化coverage-tail attempts重建；非来源root只保留protocol projection attempts。fresh与同一v2 run的resume仅从已冻结protocol snapshot/base projection恢复相同数组、tail summary和资源；`not_required_by_downstream`不得构造provider、请求secret或补tail，任何已有trace/terminal provider call均不得重复。

### 2.2 必须指标

| 指标 | 公式；numerator / denominator | grouping/slice | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `preregistered_root_count` | `|R|`；num=`R`，无 denominator | domain、difficulty、Lean topic、repeat | 固定库存，任何 root 失败仍计数 | 身份/分层字段 |
| `scientifically_valid_root_count` | `|{r∈R: failure_kind≠infrastructure_invalid}|` | 同上 | 实验性 `no_final/incorrect_final` 仍计入；不得作为成功率替代分母 | `failure_kind` |
| `infrastructure_invalid_root_count` | `|{r∈R: failure_kind=infrastructure_invalid}|` | 同上 | 非 0 时本 cell 科学 rate 为 `null` | `failure_stage`,`failure_kind`,`failure_origin` |
| `final_result_root_count` | `|F|`；num=有最终结果的 roots | 同上 | 无最终结果不进分子但留在 `R` | `final_result_present` |
| `verified_correct_root_count` | `|C|`；num=最终结果独立检查正确的 roots | 同上 | 错误或无结果不进分子 | `verified_correct` |
| `completion_rate` | `|F|/|R|`；num=`final_result_root_count`，den=`preregistered_root_count` | 同上 | 固定分母；本 cell 有 infrastructure-invalid 时为 `null` | 上述两个 count、`infrastructure_invalid_root_count` |
| `end_to_end_verified_success_rate` | `|C|/|R|`；num=`verified_correct_root_count`，den=`preregistered_root_count` | 同上 | 固定分母；本 cell 有 infrastructure-invalid 时为 `null` | 上述两个 count、`infrastructure_invalid_root_count` |
| `no_final_failure_count` | `|{r: failure_kind=no_final}|` | 同上 | 失败必须分类 | `failure_stage`,`failure_kind`,`failure_origin`,`final_result_present` |
| `incorrect_final_failure_count` | `|{r: failure_kind=incorrect_final}|` | 同上 | 完成但错误计入 | `final_result_present`,`verified_correct`,`failure_kind` |
| `infra_invalid_failure_count` | `|{r: failure_kind=infrastructure_invalid}|`；数值必须等于本 cell 的 `infrastructure_invalid_root_count` | 同上 | 仅保留既有失败 breakdown 名称，不替代三项库存计数 | `failure_stage`,`failure_kind`,`failure_origin` |
| `failure_root_count` | 上述三类之和 | 同上 | 三类必须互斥且覆盖所有非成功 root | 同上 |
| `actual_end_to_end_wall_clock_ms` | `Σ runtime_wall_clock_ms`，只累计正常协议阶段 | 同上 | 任一 root 协议边界缺失则 `null`；不得用跨-root `max-min` 混入 coverage tail | `runtime_wall_clock_ms` |
| `actual_provider_latency_ms` | 正常协议 `trace_origin=protocol` attempts 的 `Σ provider_latency_ms` | 同上 | 已调用但 latency 缺失则 `null`；tail 不进入 | `attempts[].trace_origin`,`attempts[].provider_call_made`,`attempts[].provider_latency_ms` |
| `actual_total_tokens` | 正常协议 `trace_origin=protocol` attempts 的 `Σ total_tokens` | 同上 | 实际协议调用 usage 缺失则 `null`；tail 不进入 | `attempts[].trace_origin`,`attempts[].provider_call_made`,`attempts[].total_tokens`,`attempts[].usage_status` |
| `actual_cost_estimate_cny` | 正常协议 `trace_origin=protocol` attempts 的 `Σ cost_estimate_cny` | 同上 | 协议 usage/价格输入缺失则 `null`；tail 不进入 | `attempts[].trace_origin`,`attempts[].cost_estimate_cny`,`attempts[].usage_status` |

`failure_stage`、`failure_kind` 与 `failure_origin` 的分布是必须保存的诊断明细，不新增一个与上述失败计数重复的综合比例。`trace_tail_wall_clock_ms`、`trace_tail_provider_attempt_count`、`trace_tail_total_tokens`、`trace_tail_cost_estimate_cny` 与 tail success/failure counts 也必须单独保存为 acquisition 诊断；不得加进上表四项正文协议资源，也不得用 tail 的成功修正正常协议 root 的完成或正确性。

上述三个 root 库存计数是冻结 153 个 formal metric occurrences 之外的 mandatory inventory diagnostics；它们不重编号或扩展 153 集合，但必须随每个 cell 输出并满足库存恒等式。

### 2.3 方差与置信区间

Experiment 1 使用真实调用已经产生的全部题目作为 `case_id` clusters，不新增模型调用或 repeat。必须追加：

| 对象 | 离线派生/分母 | 必须报告 | 不报告区间的项 |
|---|---|---|---|
| `completion_rate`、`end_to_end_verified_success_rate` | 沿用第 1.2 节固定 root 分母；每个 replicate 重算成功 root 数/预注册 root 数 | 点估计、numerator、denominator、bootstrap variance/standard error、95% CI、cluster count | — |
| `root_end_to_end_elapsed_ms` | 每 root：`root_terminal_at_ms-root_start_at_ms` | median/q25/q75/IQR、sample variance/stddev、median 的 95% CI | 批次 `actual_end_to_end_wall_clock_ms` 不加 CI |
| `root_provider_latency_ms` | 每 root 只对正常协议 `trace_origin=protocol` provider attempts 求 `Σ provider_latency_ms` | 同上 | 汇总总和 `actual_provider_latency_ms` 不加 CI；tail 不进入 |
| `root_total_tokens` | 每 root 只对正常协议 provider attempts 求 `Σ total_tokens` | 同上 | 精确总量 `actual_total_tokens` 不加 CI；tail 不进入 |
| `root_cost_estimate_cny` | 每 root 只对正常协议 provider attempts 求 `Σ cost_estimate_cny` | 同上 | 精确总量 `actual_cost_estimate_cny` 不加 CI；tail 不进入 |

逐 root 派生值沿用原指标的缺失规则：该 root 任一实际调用的必要 usage/latency/cost 输入缺失，则该逐 root 值为 `null`；不得当成 0。Factorization 和 Lean 的 domain/difficulty/topic 表按现有 slice 分开估计；单题细格因 `case_cluster_count<5` 只保留点估计并明确区间不可计算。

## 3. Experiment 2：worker 扩展性

### 3.1 主矩阵研究问题与条件

- **研究问题**：在相同真实模型回答输入下，提高 TokenShare 协议 worker 数量如何改变配对端到端时间、正确性、回答资源消耗、并发和利用率。
- **condition 维度**：`worker_count ∈ {1,3,7,10,30,50}`；报告按 `worker_count × repeat_id × position_stratum`，其中 `position_stratum ∈ {early,middle,late,no_factor}`。
- **case/root**：Factorization hard 50；Lean 不进入。每道题必须原样继承其在 Experiment 1 中的 split plan、子任务数量与范围、`planned_ai_unit_id`、prompt 和依赖关系；不再使用 `factorization.exp2_contiguous_20way.v1` 或任何 Experiment 2 专属 split profile。
- **repeat**：每档 2 repeats；root-run 的 `repeat_id` 是实验调度重复，来源固定为 `source_repeat_id=0`。六个 worker 档及两个 repeats 都消费同一批 Experiment 1 per-unit traces；共 600 root-runs。planned first-attempt AI unit 总数按 `6 × 2 × Σ_case |Experiment 1 planned_ai_unit_ids(case)|` 从预注册 inventory 精确计算，不再固定写成 12,000。
- **provider/model**：回答来自第 2.1 节 Experiment 1 正常协议或 coverage-tail traces，按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 精确匹配；优先读取当前协议 ordinal，缺失时读取最后一个已有自然 ordinal，并显式记录 fallback。Experiment 2 当前运行的真实 provider calls 固定为 0；不得把来源回答的 usage、latency 或 cost 称为 Experiment 2 的当次 provider 消费。
- **调度语义**：Experiment 2 不是用 Experiment 1 总时间除以 worker 数，而是把同一任务计划、同一 trace attempts 和同一 source latency 放入容量分别为 1/3/7/10/30/50 的逻辑调度器，由调度器产生 logical makespan、并发、利用率与提前停止结果。

### 3.2 主矩阵必须指标

除第 1.2 节的五个 root 库存/完成/正确性指标外，必须输出：

| 指标 | 公式；numerator / denominator | grouping/slice | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `trace_replay_wall_clock_ms` | 每 root `runtime_wall_clock_ms` | worker、repeat、position、case | 所有 roots 保留；时间缺失为 `null` | `runtime_wall_clock_ms` |
| `bank_slot_consumption` | 实际消费的固定回答 slot 数 | worker、repeat、position | 已消费后失败仍计数；slot 身份缺失为 `null` | `attempts[].source_response_slot_id`,`attempts[].source_response_consumed` |
| `trace_attributed_tokens` | `Σ source_total_tokens` | 同上 | 任一已消费 slot usage 缺失为 `null` | `attempts[].source_total_tokens` |
| `trace_attributed_cost` | `Σ source_cost_estimate_cny` | 同上 | 任一已消费 slot cost 缺失为 `null` | `attempts[].source_cost_estimate_cny` |
| `trace_replay_paired_speedup` | `T(worker=1)/T(worker=k)`；num=同 case/repeat 的 worker=1 时间，den=worker=k 时间 | case、repeat、position、k | 两端均正确且时间完整、正数才计算，否则 `null` | `case_id`,`repeat_id`,`worker_count`,`verified_correct`,`runtime_wall_clock_ms` |
| `trace_replay_parallel_efficiency` | `trace_replay_paired_speedup / k`；num=speedup，den=`worker_count` | 同上 | 与 speedup 使用同一 pair | 上述字段、`worker_count` |
| `paired_trace_token_multiplier` | `tokens(k)/tokens(1)` | 同上 | 两端资源完整且 baseline>0；否则 `null` | 配对身份、`source_total_tokens` |
| `paired_trace_cost_multiplier` | `cost(k)/cost(1)` | 同上 | 两端资源完整且 baseline>0；否则 `null` | 配对身份、`source_cost_estimate_cny` |
| `paired_speedup_planned_pair_count` | 预注册的 worker=1 与 worker=k pair 数 | repeat、position、k | 固定，不因失败删除 | 配对身份字段 |
| `paired_speedup_eligible_pair_count` | 满足 speedup 条件的 pair 数 | 同上 | 不完整 pair 不计入此分子 | 配对身份、正确性、时间 |
| `paired_speedup_ineligible_pair_count` | `planned-eligible` | 同上 | 必须带原因明细 | 同上、`failure_kind` |
| `trace_replay_paired_speedup_median` | eligible pair 的 speedup 中位数 | repeat、position、k | 无 eligible pair 为 `null` | per-pair speedup |
| `trace_replay_paired_speedup_repeat_min` | `min(S_repeat0,S_repeat1)` | position、k | 任一 repeat 汇总缺失为 `null` | 两个 repeat 汇总值 |
| `trace_replay_paired_speedup_repeat_max` | `max(S_repeat0,S_repeat1)` | position、k | 同上 | 两个 repeat 汇总值 |
| `trace_replay_paired_speedup_relative_difference` | `relative_range(S0,S1)=(max(S0,S1)-min(S0,S1))/mean(S0,S1)`；num=`max-min`，den=`(S0+S1)/2` | position、k | 任一 repeat 缺失或 mean=0 时为 `null` | 两个 repeat 汇总值 |
| `planned_ai_unit_count` | `|planned_ai_unit_ids|` | worker、repeat、position | 固定计划库存 | `planned_ai_unit_ids` |
| `executed_ai_unit_count` | `|dispatched_ai_unit_ids|` | 同上 | 执行失败但已 dispatch 仍计数 | `dispatched_ai_unit_ids` |
| `unscheduled_ai_unit_count` | `|unscheduled_ai_unit_ids|` | 同上 | 早停或失败未调度均计数 | `unscheduled_ai_unit_ids` |
| `in_flight_at_witness` | `|in_flight_ai_unit_ids_at_witness|` | 同上 | witness 缺失为 `null` | `in_flight_ai_unit_ids_at_witness` |
| `observed_peak_concurrency` | worker 执行区间的最大重叠数 | 同上 | 区间不完整为 `null` | `worker_execution_facts[].started_at_ms`,`worker_execution_facts[].ended_at_ms` |
| `worker_utilization` | `Σ busy_worker_time_ms /(worker_count × trace_replay_wall_clock_ms)` | 同上 | wall-clock≤0 或区间缺失为 `null` | `worker_execution_facts[]`,`worker_count`,`runtime_wall_clock_ms` |

Experiment 2 的六档真实 API 在线并发检查已退出 Slim V2，不选择 case、不产生额外 condition/root/provider-call 分母，也不生成原在线检查表。

### 3.3 方差与置信区间

Experiment 2 只对既有 50 个 Factorization hard cases 和两次 trace replay 做离线汇总。必须追加：

| 对象 | bootstrap 单位与重算方式 | 必须报告 |
|---|---|---|
| `completion_rate`、`end_to_end_verified_success_rate` | 在现有 worker × repeat × position slice 内按 case 抽样，重算固定分母 rate | bootstrap variance/standard error、95% CI、cluster count |
| `trace_replay_wall_clock_ms` | 逐 root 标量 | median/q25/q75/IQR、sample variance/stddev、median CI |
| `trace_replay_paired_speedup`、`trace_replay_parallel_efficiency` | 同一 case/repeat 的 worker=1 与 worker=k 作为不可拆 pair；每次重算 speedup 及 speedup/k | median/q25/q75/IQR、sample variance/stddev、median CI、planned/eligible/ineligible pair counts |
| `paired_trace_token_multiplier`、`paired_trace_cost_multiplier` | 沿用同一 matched pair 和原 eligibility | 同上 |
| `observed_peak_concurrency`、`worker_utilization` | 逐 root 标量；按 worker × repeat × position 汇总 | median/q25/q75/IQR、sample variance/stddev、median CI |

现有 `trace_replay_paired_speedup_median` 的区间字段名为 `trace_replay_paired_speedup_median_ci95_low/high`；parallel efficiency 和两项 multiplier 同理。两个 repeat 的 `repeat_min`、`repeat_max`、`relative_difference` 只作精确描述，不对两个汇总值计算 CI。`planned_ai_unit_count`、`executed_ai_unit_count`、`unscheduled_ai_unit_count`、`bank_slot_consumption` 和 `in_flight_at_witness` 保持精确计数，不加 CI。

## 4. Experiment 3：故障与 worker death 恢复

### 4.1 主矩阵条件

- **研究问题**：预注册 fault 或真实 worker process death 发生后，系统是否拦截受控错误、隔离无效结果、启动 replacement/requeue 并恢复最终结果；恢复带来多少时间与回答资源开销。
- **rate-fault**：仅 `false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error` 五类。
- **Factorization roots**：共享稳定 slice easy/medium/hard=`17/17/16`，共 50；fault rate=`1/5/10/25/50/100%`；2 repeats。
- **Lean roots**：三个 topic family 各 1，共 3；fault rate=`10/50/100%`；2 repeats。
- **0%**：不形成 Experiment 3 condition；使用相同 `case_id` 的无故障配对 reference。
- **worker**：rate-fault、无故障 reference 与 worker-death conditions 均为 `worker_count=10`；worker 数不是 Experiment 3 的变量。
- **worker death**：同一 50 Factorization + 3 Lean roots；`worker_count=10`，`dead_worker_count∈{1,3}`，kill progress=`25/50/75%`，2 repeats。
- **规模**：rate-fault 3,090 + worker-death 636 = 3,726 root-runs。当前冻结inventory中rate-fault为13,920、worker-death为2,808个planned first-attempt AI units，合计16,728。protocol execution-attempt upper bound保留worker-death被终止execution的额外headroom，固定按`3×rate_planned + 4×death_planned = 3×13,920 + 4×2,808 = 52,992`逐root派生；不得用陈旧总量硬覆盖或退化为`3×total_planned`。Experiment 3 当前真实 provider calls 固定为 0。
- **provider/model**：回答来自 Experiment 1 正常协议或 coverage-tail traces；reference、fault、worker-death 与 replacement attempts 均按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 精确匹配。当前 ordinal 存在则精确读取，不存在则读取最后一个已有自然 ordinal；Experiment 3 的 `repeat_id` 不改变来源 trace，扰动身份仍使用当前 ordinal。
- **grouping**：`fault_type × fault_rate × domain × difficulty/topic × repeat`，或 `dead_worker_count × kill_progress × domain × repeat`；不同 fault 不合并计算 replacement 成功率。

### 4.2 fault rate 与目标选择

`fault_rate` 的分母固定为每个 root 的 `planned first-attempt AI units`。令 `N=planned_first_attempt_ai_unit_count`：

```text
target_count = ceil(fault_rate × N)
```

只要 `fault_rate>0` 且 `N>0`，至少选择一个目标。先按 `planned_ai_unit_id` 稳定排序，再从完整有序列表中均匀选取 `target_count` 个目标；不得按已 dispatch、已 completed 或自然成功的 units 改变分母。故障只注入 `attempt_ordinal=0`；同一 unit 的 replacement attempts 不再注入相同故障。该实验测量一次故障后的恢复能力，不模拟永久不可用。

### 4.3 五种 fault 的固定动作

| Fault | 固定注入动作 | 系统预期行为 |
|---|---|---|
| `false_positive` | 将 ordinal 0 回答替换为格式正确但领域结论错误的 candidate | verifier/checker 实际拒绝，随后创建 replacement |
| `false_negative` | 将原本正确的 ordinal 0 candidate 强制送入 rejected 路径 | 系统重新分派；replacement 不再被误判 |
| `no_return` | ordinal 0 模拟调用已经消费 token/latency，但不形成 submission，等待 lease expiry | 系统检测超时并重新分派 |
| `late_submission` | ordinal 0 回答在 `lease_deadline+1ms` 提交 | 系统拒绝迟到结果，且迟到结果不能覆盖 replacement |
| `executor_error` | ordinal 0 模拟调用已经消费 token/latency，随后返回明确 `executor_error` | 系统记录错误并创建 replacement |

五类故障都表示“来源回答已经消费、模拟资源已经发生，但本次结果不能正常进入 canonical”。Experiment 3 当次仍没有真实 provider call；这里的“调用”是对 Experiment 1 trace attempt 的一次模拟消费。replacement 使用对应 Experiment 1 source trace 中未施加 fault 变换的回答内容。

### 4.4 token 与 latency 扰动

Experiment 3 对 reference 与 fault runs 的每个模拟 attempt 都生成确定性 token/latency 扰动；复用 Experiment 1 内容只避免重复付费，不假设真实重复调用的 usage 与 latency 完全相同。

Token 扰动：

```text
δ_token ~ Uniform(-0.10, +0.10)
simulated_total_tokens = max(1, round(source_total_tokens × (1 + δ_token)))
```

若 source usage 提供 prompt/completion/reasoning 分项，则 `prompt_tokens` 保持不变。这里用户冻结的“completion + reasoning”必须解释为两个不重叠的生成分量：`nonreasoning_completion_tokens=(completion_tokens-reasoning_tokens)` 与 `reasoning_tokens`；两者之和等于 provider 的完整 `completion_tokens`。因此只对完整 `completion_tokens` 应用 `δ_token`，不得把 provider 的 `completion_tokens` 与其子集 `reasoning_tokens` 直接相加。最终：

```text
simulated_generated_tokens = max(0, round(completion_tokens × (1 + δ_token)))
simulated_total_tokens = max(1, prompt_tokens + simulated_generated_tokens)
```

不得单独扰动 prompt。Exp3 `simulated_trace_attributed_cost` 继承 source attempt 的冻结 `pricing_version/pricing_tier` 和未扰动 prompt/cache 分项，只用 `simulated_generated_tokens` 代替 output token；不得根据逻辑时钟重新选择峰/谷档。

Latency 扰动：

```text
δ_network ~ Uniform(-0.10, +0.10)
δ_latency = 0.8 × δ_token + 0.2 × δ_network
simulated_latency_ms = max(1, round(source_latency_ms × (1 + δ_latency)))
```

因此最终 latency 扰动仍在 ±10% 内，并以 token 变化为主、保留较小独立网络波动。

扰动固定 `perturbation_seed=20260820`、`perturbation_version="slim_v2.exp3_perturbation.v1"`。伪随机身份只由以下普通字段确定：

```text
case_id
planned_ai_unit_id
experiment_repeat_id
attempt_ordinal
```

`fault_type`、`fault_rate`、condition 和 worker count 不得进入扰动身份。于是 reference 与 fault run 的相同 ordinal 0 attempt 使用相同扰动；replacement ordinal 1 使用另一组扰动；不同 experiment repeats 使用不同扰动；不同 fault conditions 不因随机数不同而破坏配对公平。

至少保存 `source_total_tokens`、`simulated_total_tokens`、`source_latency_ms`、`simulated_latency_ms`、`token_perturbation_factor`、`network_perturbation_factor`、`perturbation_seed`、`perturbation_version`。两个 factor 分别保存 `δ_token` 与 `δ_network` 本身，而不是 `1+δ`。这些是普通模拟字段，不需要 hash、digest、receipt 或证据链；`simulated_*` 不得表述为真实 provider usage。

每个 Experiment 3 root 的逻辑调度器必须使用扰动后的每次 attempt latency 重新推进并计算 logical makespan。不能把 replacement latency直接加到 reference 总时间，因为其他 units 可能并行执行。

### 4.5 主矩阵必须指标

除第 1.2 节统一 root 库存、完成、正确性与失败指标外，必须输出：

| 指标 | 公式；numerator / denominator | 适用范围 | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `injected_fault_target_count` | 实际按预注册目标计划在 ordinal 0 注入 fault 的 AI units 数 | 各 rate-fault condition | 目标未 dispatch 不计实际注入，但保留 planned target | target plan、fault observation fields |
| `controlled_wrong_candidate_count` | 实际注入、独立标记错误且到达 verification 边界的 `false_positive` candidates 数 | 仅 false_positive | 其他 fault 不进分母 | fault observation fields |
| `controlled_wrong_candidate_interception_count` | 上述集合中 verifier/checker 明确拒绝数 | 仅 false_positive | 未到 verification 不计 | `reached_verification`,`verifier_intercepted` |
| `controlled_wrong_candidate_interception_rate` | interception/count | 仅 false_positive | denominator=0 为 `null` | 上述字段 |
| `controlled_wrong_candidate_escape_count` | 同一分母中进入 canonical 或 root result 的数 | 仅 false_positive | 必须按同一 fault/attempt 关联 | `escaped_to_canonical_or_root` |
| `controlled_wrong_candidate_escape_rate` | escape/count | 仅 false_positive | denominator=0 为 `null` | 上述字段 |
| `started_replacement_attempt_count` | fault/death 后实际创建的新 attempt 数 | 各 fault/death condition | 只有 recovery 计划而无新 attempt 不计 | recovery observation fields |
| `successful_replacement_attempt_count` | 新 attempt 产生可接受替代结果并完成原 unit 的数量 | 同上 | replacement 再失败不进分子 | `replacement_started`,`replacement_succeeded` |
| `replacement_attempt_success_rate` | successful/started | 按 fault type 分开；辅助指标 | denominator=0 为 `null` | 上述字段 |
| `reassignment_count` | 有新 `attempt_id` 且反向关联原 attempt 的 replacement/requeue 数 | 各 fault/death condition | 仅计划不计 | `original_attempt_id`,`replacement_attempt_id`,`reassigned` |
| `discarded_simulated_trace_tokens` | `Σ` 因 fault/death 未进入最终有效 canonical 路径的 `simulated_total_tokens` | 主矩阵 | 任一相关模拟 usage 缺失为 `null` | fault linkage、`simulated_total_tokens`,`canonical_accepted` |
| `simulated_wall_clock_overhead_ms` | `fault_run_logical_makespan_ms-reference_run_logical_makespan_ms` | 同 case/repeat 配对 | 任一端时间缺失为 `null`；两端均由扰动后 attempts 重新调度 | 配对身份、`runtime_wall_clock_ms` |
| `simulated_token_overhead` | `fault_run_simulated_tokens-reference_run_simulated_tokens` | 同上 | 任一端模拟 token 缺失为 `null` | 配对身份、`simulated_total_tokens` |
| `simulated_trace_attributed_cost_overhead` | `fault_run_simulated_cost-reference_run_simulated_cost` | 同上 | 任一端价格或模拟 token 输入缺失为 `null` | 配对身份、模拟 token 与冻结价格字段 |
| `kill_progress_error_signed_mean_pp` | `mean_i[100×(actual_i-target_i)]` | worker death summary | 无实际 death observation 为 `null` | worker death progress fields |
| `kill_progress_error_signed_max_pp` | `max_i[100×(actual_i-target_i)]` | worker death summary | 同上 | worker death progress fields |
| `recovered_valid_canonical_required_slots` | 最终得到有效 canonical 的预注册 required slots 数 | 各 condition | 未恢复 slot 不进分子 | `required_slot_count`,`recovered_valid_canonical_slot_count` |
| `preregistered_required_slots` | 预注册 required slots 总数 | 各 condition | 固定分母 | `required_slot_count` |
| `result_completeness_rate` | recovered/preregistered slots | 各 condition | denominator=0 为 `null` | 上述字段 |
| `unrecovered_root_count` | `|{r: verified_correct=false}|`，包括 no-final 与 incorrect-final | 各 fault/death condition | 固定 root 分母，不删除失败 root | `verified_correct`,`failure_kind` |

`kill_progress_error_pp=100×(actual-target)` 的逐 death 值只保留为诊断字段；正式汇总使用 signed mean/max。fault 发生、确认、replacement 开始/结束的时间也仅作诊断，不创建 `recovery_latency`。

Experiment 3 的小型真实 API 在线恢复检查已退出 Slim V2，不选择 case、不产生额外 condition/root/provider-call 分母，也不生成原在线恢复表。replacement/requeue 仍在主矩阵中执行，但只复用同一三元键对应的 Experiment 1 回答。

上述 `injected_fault_target_count`、`started_replacement_attempt_count`、`successful_replacement_attempt_count`、`verified_correct_root_count` 和 `unrecovered_root_count` 是 Experiment 3 必须直接给出的五个核心计数。正文中的 token、cost 与 wall-clock 资源结果必须标为 `simulated trace-attributed`，不得写成 Experiment 3 当次真实 API 消耗。

### 4.6 方差与置信区间

Experiment 3 不新增 fault rate、fault type、death condition 或在线调用。必须按现有 fault/death slice 追加：

| 对象 | bootstrap 单位与重算方式 | 必须报告 |
|---|---|---|
| `completion_rate`、`end_to_end_verified_success_rate` | case cluster；重算固定 root 分母 | bootstrap variance/standard error、95% CI、cluster count |
| `controlled_wrong_candidate_interception_rate`、`controlled_wrong_candidate_escape_rate` | 抽 case 后汇总该 case 内全部受控 candidates，再重算 candidate numerator/denominator；禁止独立抽 candidate | 同上 |
| `replacement_attempt_success_rate` | 抽 case 后汇总其中全部 started/successful replacement attempts；fault type/death condition 保持分开 | 同上 |
| `result_completeness_rate` | 抽 case 后重算 `Σ recovered slots / Σ required slots` | 同上 |
| `simulated_wall_clock_overhead_ms`、`simulated_token_overhead`、`simulated_trace_attributed_cost_overhead` | 同一 case/repeat 的 fault/reference 作为不可拆 pair；两端相同 ordinal attempts 使用同一扰动身份 | median/q25/q75/IQR、sample variance/stddev、median CI、planned/eligible/ineligible pair counts |
| `discarded_simulated_trace_tokens_per_root` | 每 root 汇总其未进入最终有效 canonical 路径的 simulated tokens | median/q25/q75/IQR、sample variance/stddev、median CI |
| `kill_progress_error_signed_mean_pp` | 抽 case 时携带该 case 全部 death observations，并在 replicate 中重算 signed mean | 现有 signed mean、逐 observation sample variance/stddev、mean 的 bootstrap variance/standard error 与 95% CI |

`discarded_simulated_trace_tokens` 总量、fault target/reassignment/replacement 各计数、required-slot 两个计数和 `kill_progress_error_signed_max_pp` 都是观察到的精确值，不加 CI。Lean 某个 topic/fault/rate 细格若只有一个 case，按第 1.3.1 节输出 `null` 区间；可以对文档既有的更高层 domain 汇总计算区间，但不得为凑 cluster 数创建新 condition 或扩大数据集。

## 5. Experiment 4：挑战驱动的协议消融

### 5.1 研究问题与改动范围

- **研究问题**：在相同 roots、repeat、真实回答和确定性挑战下，FULL 能否完成协议并得到正确 root 结果；删除一个机制或两个机制后，任务完成、正确性、required-slot 完整度、时间、回答资源与执行 attempt 会下降多少；两个机制同时删除是否产生超过单项损失相加的交互惩罚。
- **相对旧设计的唯一实验改动**：由五个“自然暴露、单项消融”mode 改为十一 mode 两两完备设计；新增四类固定挑战和 challenge-stratified 指标。数据集总 root 数、模型回答来源、`worker_count=10`、3 repeats 和 `max_retries=1` 不变。
- **不研究**：不同 fault/error rate、三机制或四机制同时关闭、`ALL_OFF`、新的 provider/model 比较、统计显著性排名或综合分数。
- **有效性原则**：FULL 与所有 NO_X/NO_X__NO_Y 都必须能够进入正常 root 协议生命周期。mode-specific 配置/preflight BLOCK 只证明消融路径未接通，不能证明机制贡献。
- **结构性旁路原则**：V/P/R/M 必须分别在真实 verifier/checker、domain parser、replacement creation、unsatisfied merge decision 之前换路；“先调用真实机制、再覆盖其结果”不算删除该机制。挑战 controller 必须继续 mode-blind，structural route observer可以读取冻结 mechanism policy，但二者不得共享 mode-dependent challenge逻辑。
- **事实原则**：inventory/disabled set只标识配置与适用性，不证明 route已经到达或执行。wrong canonical、raw exposure/acceptance、R-stuck、merge readiness、premature attempt与checker rejection都必须由同一次 run 的实际 event/artifact/plugin/checker/typed route observation派生。

本次权威改动逐项如下：

| 项目 | 旧 Experiment 4 | 本次冻结值 |
|---|---|---|
| experiment | 自然回答下观察单项机制关闭 | challenge-driven 单项与双项机制贡献 |
| condition | 5 modes × 3 repeats | 11 modes × 3 repeats；challenge 是 root-level paired stratum |
| 数据集 | 50 Factorization + 15 Lean | case identity、数量和分层不变；新增 195 个 case-repeat challenge plans |
| 模型/回答 | Exp1 DeepSeek 固定回答，0 provider calls | 不变；challenge 在 lookup 后作用，不改写 source |
| repeat | 3 | 不变；challenge 在 repeat 间确定性轮转 |
| fault | 无 Exp4 fault rate | 仍无 fault condition/rate；改为四个固定 challenge families |
| ablation | FULL + 4 singles | FULL + 4 singles + 6 pairs |
| 分母 | mode 的 195 roots 与 FULL pairs | 保留固定 roots，新增 protocol-start validity、challenge opportunity、pair 和 quadruple 分母 |
| 指标公式 | success/completion loss、四类 mode label | 增加 conditional failure、slot/attempt/resource delta 和双机制 interaction penalty |

### 5.2 数据集、模型、repeat、worker 与 fault

- **case/root**：仍为每个 mode 每个 repeat 65 roots。Factorization easy/medium/hard=`17/17/16` 共 50；Lean 每个 difficulty 固定 5 题，topic=`pure_logic/function_set/induction=2/2/1`，共 15。
- **数据集改动**：不替换、不增删原 65 个 case，不改变 difficulty/topic 分层；只给 195 个 `case_id × repeat_id` cells 增加预注册 challenge plan。challenge target 可包含一个或多个 planned AI units，因此单 unit root 也不需要从数据集中排除。
- **repeat**：每题 3 次，`repeat_id∈{0,1,2}`。每个 `case_id × repeat_id` 只分配一个 challenge family；同一 case 可以在三个 repeat 中轮换到不同 challenge，以减少 case 与 challenge 的混淆。
- **worker**：全部 mode 固定 `worker_count=10`，不是自变量。
- **重试**：全部 mode 的 `ProtocolConfig.max_retries=1`。含 `NO_REQUEUE` 的 mode 只把 `replacement_attempts_allowed=false`，不把 `max_retries` 改成 0；这样 engine 仍可形成“本应 replacement”的 recovery decision。
- **provider/model**：前向所有 attempt 仍按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 精确读取 Experiment 1 的官方 DeepSeek `deepseek_v4_flash_exp1_baseline` / `deepseek-v4-flash` 正常协议或 coverage-tail trace。当前 ordinal 存在则精确读取，不存在则读取最后一个已有自然 ordinal并记录 fallback。同一 source key 在所有 mode 和三个实验 repeats 中不变；Experiment 4 当次 provider calls 固定为 0。挑战只在读取固定回答后变换本次协议输入，不改写 Experiment 1 trace 文件；第 1.4.4 节的 exact historical run 仍只消费其已经持久化的 Pro trace。
- **fault**：Experiment 4 没有 `fault_type`、`fault_rate` 或 rate sweep，相关 condition 字段固定为 `null`。四类 challenge 可以复用 `no_return` 等协议原语，但不并入 Experiment 3 fault 条件或 fault-rate 指标。

### 5.3 十一项 ablation mode

四个机制缩写为 verification=`V`、parser policy=`P`、requeue=`R`、merge gate=`M`。`slot_integrity_enabled` 在全部 mode 中保持 true。

| mode | verification | parser policy | requeue | merge gate | disabled set |
|---|---:|---:|---:|---:|---|
| `FULL` | 1 | 1 | 1 | 1 | `∅` |
| `NO_VERIFICATION` | 0 | 1 | 1 | 1 | `{V}` |
| `NO_PARSER_POLICY` | 1 | 0 | 1 | 1 | `{P}` |
| `NO_REQUEUE` | 1 | 1 | 0 | 1 | `{R}` |
| `NO_MERGE_GATE` | 1 | 1 | 1 | 0 | `{M}` |
| `NO_VERIFICATION__NO_PARSER_POLICY` | 0 | 0 | 1 | 1 | `{V,P}` |
| `NO_VERIFICATION__NO_REQUEUE` | 0 | 1 | 0 | 1 | `{V,R}` |
| `NO_VERIFICATION__NO_MERGE_GATE` | 0 | 1 | 1 | 0 | `{V,M}` |
| `NO_PARSER_POLICY__NO_REQUEUE` | 1 | 0 | 0 | 1 | `{P,R}` |
| `NO_PARSER_POLICY__NO_MERGE_GATE` | 1 | 0 | 1 | 0 | `{P,M}` |
| `NO_REQUEUE__NO_MERGE_GATE` | 1 | 1 | 0 | 0 | `{R,M}` |

每个组合 mode 必须通过一次完整的独立配置展开；不得以“先运行单项消融、再离线拼接两个结果”代替真实组合运行。组合 mode 可因挑战在协议开始后形成 no-final、stuck、错误 final 或 root checker rejection，但不能被设施以“不允许同时关闭”直接 BLOCK。

四个机制的冻结结构语义如下：

- V：Factorization 在 `verify_submission` 前旁路；Lean 在 `normalize_proof_submission`/child checker 前旁路。V disabled 的 target child真实 verifier/checker调用数必须为0；独立 root checker仍启用。synthetic verification必须带独立 bypass provenance，不能冒充 plugin checker。
- P：fixed trace raw artifact持久化后、`_parse_domain` 前旁路；P disabled 的 target domain parser调用数必须为0。
- R：recovery decision已经记录且 `retry_allowed=true`，但 replacement attempt尚未创建时旁路；不改变 recovery policy原始决定。
- M：recovery decision已经记录、任何 requeue/replacement前计算真实 readiness；M disabled且 false时，以当前真实 canonical children调用一次真实 plugin merge，不能合成 missing child。

六个 pair均在同一个真实 root/run内组合。`{R,M}` 冻结为 `RECOVERY_MERGE_FIRST`：先执行 recovery-premerge decision；M disabled且 readiness=false时 premature attempt抢先终止，R route记为`preempted_by_merge_first`，`stuck_due_to_no_requeue=false`。同一 run禁止同时报告 premature-M 与 stuck-R。

### 5.4 mode-blind 确定性 challenge

challenge plan 在展开 mode 之前，按 `(case_id, repeat_id)` 生成；`challenge_plan_id` 是普通文本 ID，不需要 digest。注入器构造函数和运行接口只接收 challenge plan、当前 unit/attempt 与固定回答，不接收 mode、disabled set 或 mechanism policy。

注入边界按 challenge 语义固定：parser-required challenge 在 domain parser 之前变换 raw content；invalid-candidate challenge 在 fixed adapter parser成功后、Factorization verifier或 Lean execution bridge normalization/child checker实际读取 candidate之前变换 candidate；两类 `no_return` challenge 在 raw/source usage 已记录后改变本次 submission result。不得在 Lean checker 已接受原 candidate 后才替换 ref 来伪装 checker interception。P disabled使 parsed boundary未到达时按实际 opportunity报告，不能伪造 injection。

| `challenge_family` | 确定性目标与注入 | FULL 的预期协议行为 | 对应机制删除后要观察的实际结果 |
|---|---|---|---|
| `INVALID_PARSED_CANDIDATE` | 目标为稳定排序第一个 planned AI unit，只改 attempt ordinal 0；在 parser 成功后、Factorization verifier 或 Lean checker 前替换成 schema-valid 但领域错误 candidate。Factorization 把 candidate 的 `target_n` 改成 `target_n+1`；Lean 保留 candidate schema/ID，但把 `proof_source` 换成引用不存在标识符的固定 proof。独立领域检查必须标记 invalid。 | verifier/checker 拒绝 ordinal 0，requeue 后 ordinal 1 使用未变换的同一 Exp1 回答并完成。 | 含 `{V}` 的 mode 可能把 invalid candidate 绑定为 canonical，随后形成 incorrect final、no-final 或 root checker rejection；不能由 mode 名直接填 count。 |
| `PARSER_REQUIRED_CANONICAL_JSON` | 目标为稳定排序第一个 planned AI unit，对该 unit 的每个 attempt 都把原本 parser-valid candidate 规范序列化为语义相同的 JSON object text；不得改变候选字段值。 | parser 把文本恢复为 typed candidate，正常进入 verification 和 merge。 | 含 `{P}` 的 mode 实际把 raw-only artifact 暴露给后续边界；记录 verifier/parser failure、raw acceptance、no-final 或额外 attempt。 |
| `RECOVERABLE_NO_RETURN` | 目标为稳定排序第一个 planned AI unit，只把 ordinal 0 变为 `no_return`；ordinal 1 返回同一 source key 的未变换回答。 | engine 记录 recovery，启动一个 replacement，最终得到有效 canonical/root。 | 含 `{R}` 的 mode 在 recovery decision 后不得创建 replacement，形成可观察 stuck/ready/no-final；不是 preflight BLOCK。 |
| `REQUIRED_CHILD_DELAY` | 对预注册 merge-blocker targets，仅把各 target 的 ordinal 0 变为 `no_return`。Factorization 选择所有包含真实 divisor 的 planned ranges；若 target 为 prime，则选择稳定排序最后一个 required range。这样剩余 children 不能形成 factor witness 或完整 no-factor coverage。Lean 选择稳定排序最后一个 required terminal slot；只有一个 slot 时就选择该 slot。 | recovery记录后、replacement前得到真实 gate=false并继续 replacement；replacement成功后正常 merge得到true。 | 含 `{M}` 的 mode在 recovery-premerge gate不满足时实际调用一次 plugin merge，记录 typed plugin outcome、missing slots、root-check reached/pass nullable 与 no-final。`{R,M}` 固定 M-first：premature attempt抢先时R不再形成stuck，不能双阳性或预填结果。 |

挑战注入是否成功必须由 hook/executor observation 与随后 protocol events 联合派生。challenge plan 存在但目标未 dispatch，不能记为 injected；mode 字符串存在，也不能推出 challenge 已应用。若 Exp1 source answer 自身未到相应 parser/checker 边界，该 root 仍留在固定任务分母并记录 `challenge_target_unreached`；只有 target 已到注入边界但 injector 未按 plan 动作，才是 challenge 接线无效。

### 5.5 challenge 分配、condition 与规模

- **condition 自变量**：`mode`。每个 mode × repeat 是一个顶层 condition，共 `11×3=33` 个 condition；`challenge_family` 是预注册 root-level paired stratum，不是 mode-specific injector 配置。
- **确定性分配**：在 Factorization 和 Lean 内分别按 difficulty、topic、case_id、repeat_id 稳定排序。Factorization 从循环 `[INVALID_PARSED_CANDIDATE, PARSER_REQUIRED_CANONICAL_JSON, RECOVERABLE_NO_RETURN, REQUIRED_CHILD_DELAY]` 的第一个元素开始逐 cell 轮转；Lean 从同一循环的 `REQUIRED_CHILD_DELAY` 开始轮转。达到下列 family quota 后跳过已满 family，直到所有 cells 分配完毕：
  - Factorization 150 个 root-repeat cells：`INVALID/PARSER/NO_RETURN/CHILD_DELAY=38/38/37/37`；
  - Lean 45 个 root-repeat cells：`11/11/11/12`；
  - 总计 195 个 paired challenge plans：`49/49/48/49`。
- **跨 mode 等同**：一个 paired challenge plan 在 11 个 mode 中复用；因此每个 mode 每 3 repeats 仍是 195 root-runs，每个 challenge family 在每个 mode 中分别有 `49/49/48/49` 个固定分母 roots。
- **总规模**：`11 modes × 3 repeats × 65 roots = 2,145 root-runs`；当前冻结inventory每个mode/repeat为`293`个planned units，合计`11×3×293=9,669` planned first-attempt AI units。7 个不含 `{R}` 的 mode 允许每 unit 最多 2 attempts，4 个含 `{R}` 的 mode 最多 1 attempt，所以 protocol execution-attempt upper bound 为 `7×3×293×2 + 4×3×293 = 15,822`。Experiment 4 provider calls 仍为 0。
- **grouping**：最小正文报告按 `mode × challenge_family × domain`；difficulty/topic 与 repeat 保留为分层表。FULL 配对和交互分析按完全相同的 `case_id × repeat_id × challenge_plan_id`。

### 5.6 两种 BLOCK 与科学有效性

令一个报告 cell 的预注册 roots 为 `R`，其中协议 root 生命周期已开始的集合为 `S`：

- `preflight_blocked_root_count=|R-S|`。
- `protocol_start_coverage=|S|/|R|`。
- 只有 `preflight_blocked_root_count=0`、`protocol_start_coverage=1`、`challenge_plan_mismatch_count=0` 且 `missed_challenge_opportunity_count=0` 时，cell 才满足 `scientifically_valid_ablation_cell=true`。target 因协议更早终止而从未到达 injection boundary，不是 missed opportunity；已经到达边界却未注入才是。
- 若 mode 配置、policy 组合或 preflight 在协议开始前拒绝任一 root，受影响 mode × challenge × domain cell 的 completion/success loss、FULL transition、interaction 和资源 delta 全部写 `null`，`missing_reason=preflight_blocked_invalid_ablation_path`。不得把这些 roots 填成 ablation failure。
- 协议开始后，challenge 已实际注入并沿正常状态机形成 `no_final`、`stuck`、`incorrect_final` 或 root checker rejection，全部计入固定 root 分母，是有效实验结果。
- 未处理的 Python 异常、数据缺键、mode-dependent challenge plan、真实 provider call 或无法写结果仍是 `infrastructure_invalid`；不能仅因异常发生在 protocol start 之后就改称 no-final。

### 5.7 必须指标：运行与挑战有效性

| 指标 | 公式；numerator / denominator | grouping | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `protocol_started_root_count` | `|S|` | mode、challenge、domain、repeat | preflight blocked 不进分子 | `protocol_started` |
| `preflight_blocked_root_count` | `|R-S|` | 同上 | 固定库存，不因停止删除 | `preflight_status`,`protocol_started` |
| `protocol_start_coverage` | `|S|/|R|` | 同上 | denominator=0 为 `null`；小于 1 时 cell 无效 | 上述字段 |
| `challenge_planned_root_count` | 有预注册 challenge plan 的 roots 数 | challenge、domain、repeat | 应等于固定库存 | `challenge_plan_id`,`challenge_family` |
| `challenge_target_opportunity_count` | 预注册 target attempt 实际到达对应 injection boundary 的次数 | challenge、domain、repeat、mode | target 未 dispatch/未到边界不进分子，但 root 留在任务分母 | challenge/attempt boundary fields |
| `challenge_injection_count` | 上述 opportunities 中实际按 plan injected 的次数 | 同上 | mode 不能改变 injector decision | `challenge_observations[].injected` |
| `missed_challenge_opportunity_count` | `opportunity-injection` | 同上 | 必须为 0；大于 0 时 cell 无效 | 上述字段 |
| `challenge_applied_root_count` | 至少一个目标 observation 显示实际 injected 的 roots 数 | 同上 | target 未到边界不进分子 | `challenge_observations[].injected` |
| `challenge_application_coverage` | injection/opportunity；num=`challenge_injection_count`，den=`challenge_target_opportunity_count` | 同上 | denominator=0 为 `null`；小于 1 属于接线无效 | 上述字段 |
| `challenge_plan_mismatch_count` | 同一 `case_id × repeat_id` 的 11 个 mode 中 challenge family、target set 或 ordinal rule 不一致的 roots 数 | challenge、domain | 必须为 0；非 0 时相关配对指标为 `null` | challenge identity fields |

### 5.8 必须指标：真实数值任务结果与资源下降

本节指标只有在对应 cell 的 `scientifically_valid_ablation_cell=true` 时计算。除第 1.2 节统一 root 库存、完成、正确性与失败指标外，必须输出：

| 指标 | 公式；numerator / denominator | grouping | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `no_final_rate` | `no_final_failure_count/preregistered_root_count` | mode、challenge、domain、repeat | protocol-start 后 stuck/no-final 进入分子 | root/failure fields |
| `incorrect_final_rate` | `incorrect_final_failure_count/preregistered_root_count` | 同上 | 完成但独立检查错误进入分子 | root/failure fields |
| `required_slot_completion_rate` | `Σ recovered_valid_canonical_required_slots / Σ preregistered_required_slots` | 同上 | 任一 root 的 required-slot 字段缺失则 cell 为 `null` | required-slot fields |
| `actual_execution_attempt_count` | `Σ len(attempts)` | 同上 | 已开始后失败 attempts 仍计数 | `attempts[].attempt_id` |
| `source_response_slot_consumption` | `Σ source_response_consumed=true` | 同上 | 消费后失败仍计数 | source consumption fields |
| `planned_pair_count` | FULL 与指定 mode 的预注册 `case × repeat × challenge_plan` pair 数 | 每个非 FULL mode、challenge、domain | 固定，不因失败删除 | 配对身份字段 |
| `runtime_valid_pair_count` | 两端 protocol-start、plan 相同且非 infrastructure-invalid 的 pairs 数 | 同上 | preflight/接线无效 pair 不进分子 | start/plan/root fields |
| `runtime_invalid_pair_count` | `planned_pair_count-runtime_valid_pair_count` | 同上 | 必须带原因明细 | 同上 |
| `full_success_ablation_success` | `count(Y_FULL=1,Y_mode=1)` | 同上 | `Y=verified_correct`；实验性失败为 0 | pair 两端 `verified_correct` |
| `full_success_ablation_failure` | `count(1,0)` | 同上 | 同上 | 同上 |
| `full_failure_ablation_success` | `count(0,1)` | 同上 | 同上 | 同上 |
| `full_failure_ablation_failure` | `count(0,0)` | 同上 | 同上 | 同上 |
| `ablation_failure_given_full_success_rate` | `count(Y_FULL=1,Y_mode=0)/count(Y_FULL=1)` | 同上 | FULL-success denominator=0 为 `null` | transition counts |
| `paired_end_to_end_success_loss_vs_full` | `mean_r(Y_FULL(r)-Y_mode(r))` over runtime-valid pairs | 同上 | range `[-1,1]`；invalid pair 不接纳但保留 count | pair 两端正确性 |
| `paired_completion_loss_vs_full` | `mean_r(C_FULL(r)-C_mode(r))`，`C=final_result_present` | 同上 | 同上 | pair 两端 completion |
| `paired_required_slot_completion_loss_vs_full` | `mean_r(Q_FULL(r)-Q_mode(r))`，`Q=valid_required_slots/required_slots` | 同上 | 任一端 required slots 缺失为 ineligible | required-slot fields |
| `trace_replay_wall_clock_delta_vs_full` | `median_r(T_mode-T_FULL)` | 同上 | 两端时间完整才 eligible；stuck 有终止观察时间时可计算 | `runtime_wall_clock_ms` |
| `execution_attempt_delta_vs_full` | `median_r(A_mode-A_FULL)` | 同上 | 两端 attempts 完整才 eligible | attempts |
| `source_slot_consumption_delta_vs_full` | `median_r(B_mode-B_FULL)` | 同上 | 两端 consumption 完整才 eligible | source consumption fields |
| `trace_attributed_token_delta_vs_full` | `median_r(tokens_mode-tokens_FULL)` | 同上 | 任一已消费 slot usage 缺失则 pair ineligible | source token fields |
| `trace_attributed_cost_delta_vs_full` | `median_r(cost_mode-cost_FULL)` | 同上 | 任一已消费 slot cost 缺失则 pair ineligible | source cost fields |

每个 paired delta 必须同时输出自己的 `planned_pair_count`、`eligible_pair_count`、`ineligible_pair_count` 和 ineligible reason 分布，不能只报告 median。上述 effect 先按 repeat 分别计算，再输出三个 repeat 值及 `repeat_median/min/max`；不对 signed loss/interaction 计算 `relative_range`。

### 5.9 必须指标：challenge/机制专项结果

| 指标 | 公式；numerator / denominator | 适用 challenge/mode | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `independently_labeled_invalid_candidate_count` | 实际 injected 且独立标记 invalid 的 candidates 数 | `INVALID_PARSED_CANDIDATE`，全部 mode | 未注入不进分母 | challenge candidate label |
| `invalid_candidate_verifier_rejection_count` | 上述 candidates 中 verifier/checker 明确拒绝数 | verification enabled modes | 未到 verification 不进分子 | reached/rejected fields |
| `wrong_canonical_acceptance_count` | 上述 candidates 中进入 canonical 的数量 | 全部 mode | 从 canonical event 派生 | `wrong_canonical_accepted` |
| `wrong_canonical_acceptance_rate` | wrong canonical / independently invalid | 全部 mode | denominator=0 为 `null` | 上述字段 |
| `root_checker_rejection_after_wrong_canonical_count` | wrong canonical 后 root checker 明确拒绝的 roots 数 | 全部 mode | 无 root checker 的 no-final 单列 failure kind | root checker/challenge linkage |
| `parser_required_input_count` | 实际注入 canonical JSON text 的 attempts 数 | `PARSER_REQUIRED_CANONICAL_JSON` | 未注入不计 | challenge observations |
| `raw_only_exposure_count` | parser policy bypass 后实际暴露 raw-only artifact 的次数 | 含 `{P}` 的 mode | 不按 mode 常量填值 | `raw_only_exposed` |
| `raw_only_acceptance_count` | raw-only exposure 后进入 canonical 的次数 | 含 `{P}` 的 mode | 必须从 canonical event 派生 | `raw_only_accepted` |
| `raw_only_acceptance_rate` | accepted/exposure | 含 `{P}` 的 mode | denominator=0 为 `null` | 上述字段 |
| `recoverable_no_return_count` | ordinal 0 实际注入 `no_return` 的 roots 数 | `RECOVERABLE_NO_RETURN` | 未注入不计 | challenge/attempt fields |
| `replacement_started_after_challenge_count` | 上述 roots 中实际创建 ordinal 1 attempt 的数量 | requeue enabled modes | 只有 decision 不计 started | replacement linkage |
| `valid_final_after_challenge_count` | 上述 roots 中最终 `verified_correct=true` 数 | 全部 mode | 固定 challenge root 分母 | challenge/root fields |
| `valid_final_after_challenge_rate` | valid final / 实际 injected challenge roots | 全部 challenge/mode | denominator=0 为 `null` | 上述字段 |
| `stuck_task_count` | 同一 linkage 上 recovery `retry_allowed=true`、R route实际应用、未创建 replacement且无 final 的 roots 数 | 含 `{R}` 的 mode | `{R,M}` 被M抢先时R=`preempted_by_merge_first`且不计stuck；不得从mode/processing/retry disallowed推导 | `stuck_due_to_no_requeue` |
| `stuck_task_rate` | stuck / 实际 injected `RECOVERABLE_NO_RETURN` roots | 含 `{R}` 的 mode | denominator=0 为 `null` | 上述字段 |
| `merge_gate_unsatisfied_observation_count` | gate_satisfied=false 且有 missing required slots 的观察数 | `REQUIRED_CHILD_DELAY` | 只看真实 hook input | merge observations |
| `premature_merge_attempt_count` | gate 未满足时实际执行的 merge attempt 数 | 含 `{M}` 的 mode | 只有 bypass 配置而无 attempt 不计 | `premature_merge_attempted` |
| `premature_merge_failure_count` | 上述 attempts 中 typed plugin rejection、真实 root checker rejection或未形成 final 的数量 | 含 `{M}` 的 mode | 按实际 attempt结果；checker未到达为`reached=false/pass=null`，不是checker false | `premature_merge_failed` |
| `premature_merge_failure_rate` | failure/attempt | 含 `{M}` 的 mode | denominator=0 为 `null` | 上述字段 |

不适用 challenge/mode 的专项指标写 `null` 并记录 `not_applicable`，不写 0。FULL 不得产生 disabled-mechanism observation，但必须接收与 NO_X 完全相同的 challenge。

`root_checker_rejection_after_wrong_canonical_count` 只计 linked root-check report明确显示 `reached=true/pass=false` 的 roots；`not verified_correct`、plugin rejection、checker未到达或no-final均不能代替明确checker rejection。route本应到达却缺实际证据时，相应 raw field为`null`并记录`missing_actual_route_evidence`，不能用`not_applicable`或mode常量掩盖wiring error。

### 5.10 必须指标：双机制交互

令 `Y_S(r)` 是 matched root `r` 在 disabled set `S` 下的 `verified_correct∈{0,1}`，`C_S(r)` 是 `final_result_present∈{0,1}`，`Q_S(r)` 是 required-slot completion ratio。对每个 `{i,j}⊆{V,P,R,M}`，只使用 `FULL`、`NO_i`、`NO_j`、`NO_i__NO_j` 四端均 runtime-valid、challenge plan 相同且字段完整的 matched quadruple：

- `single_removal_success_loss_i = mean_r(Y_∅(r)-Y_{i}(r))`。
- `pair_removal_success_loss_ij = mean_r(Y_∅(r)-Y_{i,j}(r))`。
- `pair_interaction_success_penalty_ij = mean_r(Y_i(r)+Y_j(r)-Y_∅(r)-Y_{i,j}(r))`。
- `pair_interaction_completion_penalty_ij = mean_r(C_i(r)+C_j(r)-C_∅(r)-C_{i,j}(r))`。
- `pair_interaction_required_slot_penalty_ij = mean_r(Q_i(r)+Q_j(r)-Q_∅(r)-Q_{i,j}(r))`。

interaction penalty 为正表示双机制同时删除造成的下降大于两个单项下降之和，为 0 表示加性，为负表示小于加性。每个交互指标必须输出 `planned_quadruple_count`、`eligible_quadruple_count`、`ineligible_quadruple_count`；任一四端 preflight blocked 时相关 cell 指标为 `null`，不能把 blocked 端当成 0。交互指标按 challenge family、domain 和 repeat 分层，并报告三个 repeat 的 median/min/max。

### 5.11 方差与置信区间

Experiment 4 沿用已冻结的 11 个 modes、四类 mode-blind challenge、现有 roots 和 3 repeats；本节不新增 ablation、challenge、错误率或调用。只有 `scientifically_valid_ablation_cell=true` 的 cell 才计算以下统计：

| 对象 | bootstrap 单位与重算方式 | 必须报告 |
|---|---|---|
| `completion_rate`、`end_to_end_verified_success_rate`、`no_final_rate`、`incorrect_final_rate` | 现有 mode × challenge × domain × repeat slice 内按 case 抽样，重算固定 root 分母 | bootstrap variance/standard error、95% CI、cluster count |
| `required_slot_completion_rate` | 抽 case 后重算 recovered/required slot 总数，不独立抽 slot | 同上 |
| `ablation_failure_given_full_success_rate`、`paired_end_to_end_success_loss_vs_full`、`paired_completion_loss_vs_full`、`paired_required_slot_completion_loss_vs_full` | FULL 与指定 mode 作为不可拆 matched pair；每次重算 transition 分母、paired mean/loss | bootstrap variance/standard error、95% CI、planned/eligible/ineligible pair counts |
| `trace_replay_wall_clock_delta_vs_full`、`execution_attempt_delta_vs_full`、`source_slot_consumption_delta_vs_full`、`trace_attributed_token_delta_vs_full`、`trace_attributed_cost_delta_vs_full` | 保持 FULL/mode pair 后计算逐 pair signed delta | median/q25/q75/IQR、sample variance/stddev、median CI、pair counts |
| `wrong_canonical_acceptance_rate`、`raw_only_acceptance_rate`、`valid_final_after_challenge_rate`、`stuck_task_rate`、`premature_merge_failure_rate` | 抽 case 后携带该 case 全部 candidate/attempt/challenge observations，并按第 5.9 节原分母重算；不独立抽 observation | bootstrap variance/standard error、95% CI、cluster count |
| `single_removal_success_loss_i`、`pair_removal_success_loss_ij`、三项 `pair_interaction_*_penalty_ij` | 以 case 为簇，FULL、NO_i、NO_j、NO_i__NO_j 四端和同一 challenge plan/repeat 不可拆；每次重算第 5.10 节公式 | bootstrap variance/standard error、95% CI、planned/eligible/ineligible quadruple counts |

第 5.8 节五项不带后缀的 delta 指标以逐 pair delta 的 median 作为点估计；其 q25/q75/IQR/variance/stddev/CI 也必须从逐 pair delta 计算，不能从三个 repeat median 计算。第 5.10 节的 CI 在 challenge family × domain × repeat cell 的 matched quadruples 上计算；三个 repeat 的 `repeat_median/min/max` 仍只作描述，不给这三个汇总值套 CI。四端任一 blocked 或不完整时继续执行原 `null`/ineligible 规则。

`protocol_started_root_count`、`preflight_blocked_root_count`、challenge planned/applied/mismatch 数、四格 transition counts、各种 candidate/attempt/root 原始计数、pair/quadruple 库存数以及三个 repeat 的 min/max 都保持精确值，不加 CI。`protocol_start_coverage` 和 `challenge_application_coverage` 是接线/挑战实施完整性检查，也只报告精确比例；它们小于 1 时按第 5.6–5.7 节判定 cell 有效性，不能用置信区间淡化接线缺失。

## 6. Experiment 5：三模型 endpoint comparison 与 Flash V4 补充参考

### 6.1 研究问题与条件

- **研究问题**：在同一 hard roots、相同 TokenShare/parser/verifier/checker 流程和零重试条件下，三个固定 SiliconFlow thinking endpoint 的首次输出质量、最终完成/正确性、实际资源和真实 wall-clock 如何比较。
- **case/root**：Factorization hard 固定有序清单的前 28；Lean hard 的 `pure_logic/function_set/induction` 各取既有有序清单的前 3；共 37 roots/model。此缩减只改变 Experiment 5，既有排序、协议、题目语义和 Experiment 1–4 均不变。
- **repeat/规模**：只运行 `repeat_id=0`；3 models × 1 repeat，12 conditions、111 root-runs、852 planned first-attempt AI units/provider-attempt upper bound。
- **worker**：所有 model/repeat condition 均为 `worker_count=10`，不是新的比较维度。
- **重试**：`ProtocolConfig.max_retries=0`，`replacement_attempts_allowed=false`，每 AI unit 最多 1 次 provider attempt。
- **provider/model**：SiliconFlow cohort：
  - A `zai-org/GLM-5.2`，thinking，`thinking_budget=100000`；
  - B `Qwen/Qwen3-14B`，thinking，`thinking_budget=100000`；
  - C `MiniMaxAI/MiniMax-M2.5`，thinking，`thinking_budget=100000`。
- **公共控制**：`timeout_seconds=600`，`max_tokens=100000`，SiliconFlow 全局 in-flight=3；三个 entry 的`thinking_budget=100000`不是 response token 上限；唯一运行顺序固定为 `ABC`。
- **成本口径**：三个 endpoint 全部使用第 1.4 节的 `pricing_version="slim_v2.pricing.2026-08-20"`；reasoning 是 completion/output 子集，不重复计价。
- **grouping**：正文按 model endpoint 汇总；domain/topic/repeat 和 failure taxonomy 留在明细。不得创建 pairwise significance、排名或综合分数。

### 6.2 质量与最终结果表（必须）

除第 1.2 节统一 root 库存、完成、正确性与失败指标外，必须输出：

| 指标 | 公式；numerator / denominator | 缺失与失败 | 最小原始字段 |
|---|---|---|---|
| `actual_first_provider_attempt_count` | 实际发生的 ordinal=0 provider attempts 数 | pre-dispatch 失败不计 call | attempt ordinal/call fields |
| `first_attempt_without_verifier_accepted_candidate_count` | 首次 provider attempt 未直接产生 verifier/checker 接受 candidate 的数量 | 只有全部实际首次调用都已到达可评估的 verifier/checker 边界时才写数值；出现第 6.2.1 节的已解析未提交事实时为 `null + not_applicable_or_unavailable` | first-attempt result fields |
| `first_attempt_nonpass_rate` | 上一 count / actual first attempts | denominator=0 为 `null + zero_denominator`；第 6.2.1 节事实使本指标及其 interval 派生为 `null + not_applicable_or_unavailable` | 上述字段 |
| `first_attempt_provider_transport_failure_count` | 非通过项中 provider/transport failure 数 | 互斥优先级第 1 | `result_kind`,`http_status` |
| `first_attempt_parse_schema_unusable_count` | 非通过项中 parse/schema unusable 数 | 仅未归入 transport failure | `parse_result` |
| `first_attempt_verification_checker_rejection_count` | 非通过项中 verifier/checker rejection 数 | 仅正常返回且可解析 | `verifier_result`,`checker_result` |
| `first_attempt_checkable_candidate_count` | 首次调用中正常返回、可解析且实际到达 verifier/checker 的数量 | 未到检查边界不计 | raw/parse/checker fields |
| `first_attempt_explicitly_rejected_by_verifier_count` | checkable candidates 中明确 rejected 数 | 只有实际到达 checker/verifier 的 candidate 可计；未到边界者不计入分母或本 count | verifier/checker fields |
| `first_attempt_verification_rejection_rate` | rejected/checkable | denominator=0 为 `null` | 上述字段 |

#### 6.2.1 已解析但未到 verification 边界

当 ordinal-0 的真实 provider call 已持久化 `2xx`、raw response、可用 `parse_result` 与 `result_kind="parsed"`，同时 `verifier_result/checker_result` 均为 `null`，且同一 attempt 已有 `not_applicable_or_unavailable` 的持久化缺失原因时，它是已终止 protocol 中**不可评估的 verification 边界事实**。它不是 transport、parse 或 verification rejection，也不创建第四类 failure taxonomy、metric 或 raw 字段。

Reducer 必须保留它的实际调用、调用覆盖、usage、token、cost 与 wall-clock 事实；它不进入三项 failure breakdown、`checkable`、explicitly rejected 或 verification-rejection 分母。由于不能从未到达 verification 的事实推导“未通过”，`first_attempt_without_verifier_accepted_candidate_count`、`first_attempt_nonpass_rate` 及其 bootstrap/CI/variance/standard-error 派生统一写 `null + not_applicable_or_unavailable`。没有这项既存缺失原因的同形 null 仍是投影损坏，reducer 必须 fail closed，不能静默吞掉。

### 6.3 调用量与资源表（必须）

| 指标 | 公式；numerator / denominator | grouping/slice | 缺失与失败 | 最小原始字段 |
|---|---|---|---|---|
| `planned_first_attempt_ai_unit_count` | `|planned_ai_unit_ids|` | model | 固定计划库存 | `planned_ai_unit_ids` |
| `actual_first_provider_attempt_count` | 实际 ordinal=0 provider calls 数 | model | 早停导致少调用必须如实保留 | attempt ordinal/call fields |
| `first_attempt_call_coverage` | actual/planned | model | denominator=0 为 `null` | 上述字段 |
| `actual_total_tokens` | `Σ actual total_tokens` | model | usage 缺失为 `null` | attempt usage fields |
| `actual_cost_estimate_cny` | `Σ actual cost` | model | usage/价格缺失为 `null` | attempt cost fields |
| `repeat0_wall_clock_ms` | `max(root_terminal_at_ms)-min(root_start_at_ms)`；roots 串行 | model × repeat0 | 任一 root 生命周期边界缺失为 `null` | `repeat_id`,`root_start_at_ms`,`root_terminal_at_ms` |
| `repeat1_wall_clock_ms` / `repeat2_wall_clock_ms` | 不运行 | model | 固定 `null + not_applicable_or_unavailable`，不是缺失或失败 | profile repeat set |
| `model_wall_clock_median_ms` / `model_wall_clock_min_ms` / `model_wall_clock_max_ms` | 唯一的 repeat0 wall-clock | model | repeat0 缺失时为 `null` | repeat0 值 |
| `model_wall_clock_range_ms` | 单观察值 `max-min=0` | model | repeat0 缺失时为 `null` | repeat0 值 |

provider latency、429/timeout 和更细 failure message 仅作 Experiment 5 诊断，不进入这两张最小正文表。

### 6.4 方差与置信区间

Experiment 5 对每个 model endpoint 单独估计，不新增模型、题目或 repeat，也不进行 pairwise significance、排名或综合分数。唯一 `repeat_id=0` 下的 model 汇总以 `case_id` 为 cluster；抽中一题时保留该 model 下该题的唯一 observation。

| 对象 | bootstrap 单位与重算方式 | 必须报告 |
|---|---|---|
| `completion_rate`、`end_to_end_verified_success_rate` | 每 model 按 case cluster 抽样并重算固定 root 分母 | bootstrap variance/standard error、95% CI、cluster count |
| `first_attempt_nonpass_rate` | 仅当第 6.2.1 节不可评估边界不存在时，抽 case 后重算 nonpass/actual；否则保持 `null + not_applicable_or_unavailable` | 同上 |
| `first_attempt_verification_rejection_rate` | 抽 case 后重算 rejected/checkable；不得独立抽 candidate | 同上 |
| `first_attempt_call_coverage` | 抽 case 后重算 actual/planned first attempts | 同上 |
| `root_actual_total_tokens` | 每 root 对实际 ordinal=0 provider attempts 求 `Σ total_tokens` | median/q25/q75/IQR、sample variance/stddev、median CI |
| `root_actual_cost_estimate_cny` | 每 root 对实际 ordinal=0 provider attempts 求 `Σ cost_estimate_cny` | 同上 |
| `repeat_wall_clock_ms` | 每 model 的唯一 repeat0 wall-clock 值 | `model_wall_clock_sample_stddev_ms=null + insufficient_observations_for_sample_variance`；不把单批次伪装为方差 |

`actual_total_tokens`、`actual_cost_estimate_cny` 和唯一 repeat0 的 wall-clock/min/max/range 保持精确值。单批次的 `model_wall_clock_median_ms` 不计算 CI，`range=0` 只描述该单观察值而不是可推广稳定性；不得把 37 roots 当作整批 wall-clock 的 37 次独立重复。若 root usage 缺失，逐 root token/cost 沿用第 6.3 节规则为 `null`，不当成 0。

### 6.5 Exp1 Flash V4 历史事实补充比较表（非正式 Exp5 表）

在三个 Exp5 实测模型的正式 `metrics/tables/exp5.*` 和 `summary.json` 已发布后，允许显式运行 `compare-exp5-v4-reference --run-dir <exp5-run> --source-run-dir <exp1-flash-run>`。它只读取两个 run 的冻结 inventory 与 committed `RootResultV2`，不调用 provider、不重跑 root，也不改写正式五表或 summary；输出仅为 target run 的 `metrics/supplemental/exp5_with_exp1_v4_reference.{jsonl,csv}`。

该表固定四行：三个 `observation_origin="exp5_live"` 行保留正式 Exp5 的质量、ordinal-0 实际 token/成本和各自真实 `repeat0_wall_clock_ms`；第四行固定为`configured_model="deepseek-v4-flash"`、`observation_origin="exp1_reused_actual"`，保留 Exp1 已发生的质量、ordinal-0 实际 token/原始成本和其原有 Flash 价格版本。V4 行的所有 wall-clock 字段必须为 `null + not_applicable_or_unavailable`，不得展示、换算或模拟其 Exp1 latency。

调用前 reducer 必须确认 target 恰为上述三个 Exp5 repeat-0 模型且三者 case 集相同；source 对应 case 必须都是 committed `experiment_id="exp1"`、`configured_model="deepseek-v4-flash"`、`repeat_id=0`，并且没有任何 `attempt_ordinal>0` 的 provider call。source run 中不属于 target case 的 Exp1 roots 不进入该表。该表是明确 provenance 的跨实验参考，不是正式四模型 Exp5 endpoint comparison，不进入 153 个 formal metric occurrences、正文 Exp5 latency 比较或 summary。

## 7. 仅诊断字段与已退出指标

### 7.1 仅诊断

- 每个 provider attempt 的原始响应是否存在、HTTP status、provider latency、error kind/message。
- parser、verifier、Lean checker 的逐 attempt 状态与拒绝原因。
- fault 发生/确认/replacement 开始/结束时间；逐 death `kill_progress_error_pp`。
- worker ID、PID、exit code、每个 worker busy interval。
- Exp4 每个 challenge 的 target 选择过程、attempt ordinal、injection boundary、mode 间 plan mismatch、preflight/block 原因、stuck 时最后协议状态和 paired/quadruple 不合格原因。
- Exp5 provider latency、429/timeout 明细。
- 配对不合格原因、字段缺失原因和不适用原因。

这些字段用于解释必须指标，不得衍生新的主结论比例。

### 7.2 已退出或不再需要

以下 metric IDs 不得恢复：

`accepted_validity_rate`、`throughput`、`throughput_roots_per_second`、`efficiency`、`recovery_rate`、`recovery_latency`、`recovery_latency_ms`、`exposed_error_count`、`escaped_error_count`、`error_escape_rate`、`wrong_canonical_count`、`raw_only_count`、`stuck_count`、`premature_merge_count`、`paired_sample_size`、`paired_case_count`、`paired_difference`、`actual_provider_attempt_count`、`retry_count`、`retry_success_rate`、`model_pairwise_significance`、`model_pairwise_rank`、`composite_model_score`。

此外，Slim V2 已退出旧 `exp2_online_concurrency` 与 `exp3_online_recovery` 两个完整 table scope。只在这些 scope 中存在的 `provider_429_or_timeout_union_count`、`provider_429_or_timeout_union_fraction`、`replacement_chain_count`、`worker_death_reassignment_chain_count`、`actual_provider_calls`、`actual_prompt_tokens`、`actual_completion_tokens`、`wasted_actual_tokens` 不再是 Slim V2 必须指标。与其他实验表同名的 completion、correctness、token、cost、wall-clock 字段仍按其保留 scope 计算，不能因退出在线表而一并删除。

## 8. 最小原始数据字段总表

建议每个预注册 root-run 写一行 JSONL；`attempts`、`fault_observations`、`recovery_observations`、`worker_death_observations`、`challenge_observations`、`ablation_observations` 为嵌套数组。tail-required来源Exp1 root另为每个planned unit写唯一per-unit trace；非来源Exp1只保留协议实际trace/attempt和明确非tail状态。trace attempt复用下表provider/usage/result字段并增加`trace_origin`。即使root在构造或执行早期失败，也必须写出身份字段、固定分母成员和失败分类。

| 原始字段 | 必填/可空 | 被哪些指标消费 |
|---|---|---|
| `experiment_id`,`condition_id`,`case_id`,`repeat_id` | 必填 | 所有分组、固定 root 分母、所有配对指标 |
| `domain`,`difficulty`,`topic_family`,`position_stratum` | 按实验；不适用为 null | Exp1 分层；Exp2 position；Exp3/4 domain/topic；Exp5 明细 |
| `mode`,`disabled_mechanisms`,`worker_count`,`fault_type`,`fault_rate`,`dead_worker_count`,`kill_progress_target_ratio` | 按 condition；不适用为 null | Exp2 worker/speedup/efficiency；Exp3 fault/death；Exp4 单项/组合 mode；Exp4 fault 字段必须为 null |
| `challenge_plan_id`,`challenge_family`,`challenge_target_planned_ai_unit_ids`,`challenge_attempt_ordinal_rule` | Exp4 必填 | mode-blind plan 等同、challenge 分组、paired/quadruple identity |
| `provider_family`,`provider_entry_id`,`configured_model`,`requested_model`,`resolved_model`,`reasoning_mode` | Exp1/5 实际调用必填；Exp2–4 来源字段必填 | Exp1/5 model slice 与 failure 分类；Exp2–4 验证来源回答来自冻结 DeepSeek entry/model |
| `root_start_at_ms`,`root_terminal_at_ms`,`runtime_wall_clock_ms` | 必填；三者必须满足 `runtime=root_terminal-root_start` | Exp1/5 真实 root wall-clock；Exp2/3/4 逻辑 root timing 与 overhead/delta；Exp5 repeat/model wall-clock |
| `trace_tail_started_at_ms`,`trace_tail_terminal_at_ms`,`trace_tail_wall_clock_ms`,`trace_tail_status` | Exp1全部必填；来源root无target时为`not_needed`，非来源root为`not_required_by_downstream`；两者start/terminal为null、wall=0 | coverage tail 与 root 协议时间隔离；tail wall只作acquisition诊断，非来源的零值是明确政策事实 |
| `trace_tail_target_ai_unit_ids`,`trace_tail_recorded_ai_unit_ids`,`trace_tail_provider_attempt_count`,`trace_tail_total_tokens`,`trace_tail_cost_estimate_cny` | Exp1全部必填/usage可空且带reason；非来源固定空IDs和零资源 | 来源root证明每个unscheduled planned unit形成唯一trace；单独统计真实tail调用资源，不进入Exp1正文资源 |
| `preflight_status`,`protocol_started`,`root_status`,`final_result_present`,`verified_correct`,`failure_stage`,`failure_kind`,`failure_origin` | `failure_origin` 在存在结构化细因时必填，无独立细因或成功 root 为 null；其余字段按生命周期必填/可空 | 所有 completion/success/failure counts；三项 cell root 计数；Exp2 speedup pair 接纳条件；Exp4 BLOCK 有效性、transition 与 interaction；细分正常耗尽与基础设施来源 |
| `planned_ai_unit_ids`,`dispatched_ai_unit_ids`,`completed_ai_unit_ids`,`unscheduled_ai_unit_ids` | 必填数组 | Exp2 planned/executed/unscheduled；Exp5 planned units/call coverage；其余运行完整性诊断 |
| `in_flight_ai_unit_ids_at_witness` | Exp2 主矩阵必填 | `in_flight_at_witness` |
| `observed_peak_concurrency` | Exp2 主矩阵必填 | `observed_peak_concurrency` |
| `worker_execution_facts[].worker_id` | worker 执行后必填 | worker utilization；worker death/reassignment 诊断 |
| `worker_execution_facts[].started_at_ms`,`worker_execution_facts[].ended_at_ms` | worker 执行后必填 | `observed_peak_concurrency`,`worker_utilization` |
| `worker_execution_facts[].result_kind` | worker 执行后必填 | worker death/failure 分类、executed-unit 诊断 |
| `required_slot_count`,`recovered_valid_canonical_slot_count` | Exp3/4 主矩阵必填 | Exp3 `preregistered_required_slots`,`recovered_valid_canonical_required_slots`,`result_completeness_rate`；Exp4 required-slot loss/interaction |
| `attempts[].attempt_id`,`attempts[].unit_id`,`attempts[].planned_ai_unit_id`,`attempts[].attempt_ordinal`,`attempts[].trace_origin` | attempt 创建后必填；`trace_origin` 在 Exp1 per-unit trace 必填，取 `protocol/coverage_tail` | actual first attempts/calls；fault/recovery linkage；planned/executed units；Exp1 正文/tail 资源隔离 |
| `attempts[].replacement_of_attempt_id`,`attempts[].recovery_trigger` | replacement 时必填 | Exp3 replacement counts/success rate、reassignment 与 recovery chain |
| `attempts[].started_at_ms`,`attempts[].ended_at_ms` | attempt 启动后必填 | provider/worker timing 诊断、recovery 时序诊断 |
| `attempts[].result_kind`,`attempts[].provider_call_made`,`attempts[].http_status` | attempt 终止后必填 | Exp1/5 actual call 与 failure taxonomy；Exp2–4 必须验证 `provider_call_made=false` |
| `attempts[].provider_latency_ms` | Exp1/5 provider call 后可空；缺失需 reason | Exp1 必须 provider latency；Exp5 仅诊断 |
| `attempts[].raw_response_present`,`attempts[].parse_result`,`attempts[].verifier_result`,`attempts[].checker_result`,`attempts[].canonical_accepted` | 按执行边界必填/可空 | completion/correctness；Exp3 interception/escape；Exp4 wrong/raw-only；Exp5 nonpass/checkable/rejection |
| `attempts[].prompt_tokens`,`attempts[].prompt_cache_hit_tokens`,`attempts[].prompt_cache_miss_tokens`,`attempts[].completion_tokens`,`attempts[].reasoning_tokens`,`attempts[].total_tokens` | provider usage 可空；缺失需 reason | 所有 actual token/cost 指标；reasoning token 仅作 completion 子集诊断，不重复加入 total/cost |
| `attempts[].provider_request_started_at_utc`,`attempts[].pricing_version`,`attempts[].pricing_tier` | Exp1/5 实际调用后必填；前向 Flash 与 Exp5 的 tier 均固定为 `flat` | 调用时间事实、模型专属静态表选择与成本来源冻结；不得用 root 时间代替 provider call start |
| `attempts[].cost_estimate_cny`,`attempts[].usage_status` | provider call 后必填/可空 | 所有 actual cost 指标；决定 token/cost 是否为 `null` |
| `attempts[].source_response_slot_id`,`attempts[].source_response_consumed`,`attempts[].source_attempt_ordinal`,`attempts[].source_attempt_fallback_used`,`attempts[].source_trace_origin`,`attempts[].source_result_kind` | Exp2–4 固定回答主矩阵必填 | 标识实际选中的自然 attempt、是否 last-attempt fallback、来自正常协议还是 coverage tail，并原样重放 source result kind；名称中的 slot 不代表 response-bank authority |
| `attempts[].source_case_id`,`attempts[].source_repeat_id`,`attempts[].source_planned_ai_unit_id` | Exp2–4 每次回答 lookup 必填 | 验证来源键严格等于 `case_id × source_repeat_id=0 × planned_ai_unit_id`；root `repeat_id` 不参与 lookup |
| `attempts[].source_unit_candidate_start`,`attempts[].source_unit_candidate_end`,`attempts[].source_lemma_node_id`,`attempts[].source_dependency_path` | 按 domain 必填/不适用为 null | 与当前 unit 的 Factorization range 或 Lean node/dependency 普通字段逐项核对；不使用 hash/digest |
| `attempts[].source_latency_ms`,`attempts[].source_total_tokens`,`attempts[].source_cost_estimate_cny` | Exp2–4 主矩阵必填/可空且带 reason | trace timing 输入、trace attributed token/cost 与 Exp4 delta；Exp3 保留 source 值但正文使用 simulated 值 |
| `attempts[].source_prompt_tokens`,`attempts[].source_prompt_cache_hit_tokens`,`attempts[].source_prompt_cache_miss_tokens`,`attempts[].source_completion_tokens`,`attempts[].source_reasoning_tokens`,`attempts[].source_pricing_version`,`attempts[].source_pricing_tier` | Exp3 每个模拟 attempt 必填/可空且带 reason | 只扰动完整 completion、保持 prompt/cache 不变，并按 source 冻结价格计算 simulated trace-attributed cost；reasoning 是 source completion 子集 |
| `attempts[].simulated_total_tokens`,`attempts[].simulated_latency_ms`,`attempts[].token_perturbation_factor`,`attempts[].network_perturbation_factor`,`attempts[].perturbation_seed`,`attempts[].perturbation_version` | Exp3 每个模拟 attempt 必填；source 缺失时为 null 且带 reason | Exp3 simulated token/wall-clock/cost、discarded resource 与配对 overhead |
| `fault_target_planned_ai_unit_ids`,`fault_target_count` | Exp3 rate-fault root 必填 | 固定 target plan、`injected_fault_target_count` 分母与未到达目标诊断 |
| `fault_observations[].attempt_id`,`fault_observations[].fault_type`,`fault_observations[].target_planned_ai_unit_id`,`fault_observations[].injected` | 实际 fault 时必填 | fault condition 计数与 fault/attempt identity |
| `fault_observations[].reached_verification`,`fault_observations[].independently_wrong`,`fault_observations[].verifier_intercepted`,`fault_observations[].escaped_to_canonical_or_root` | false_positive 时必填 | controlled wrong candidate count/interception/escape 及两种 rate |
| `fault_observations[].discarded_total_tokens` | 有丢弃模拟回答时必填/可空且带 reason | `discarded_simulated_trace_tokens`；值来自本次 `simulated_total_tokens` |
| `recovery_observations[].original_attempt_id`,`recovery_observations[].replacement_attempt_id` | recovery 时必填 | replacement/reassignment 与 discarded trace identity |
| `recovery_observations[].replacement_started`,`recovery_observations[].replacement_succeeded`,`recovery_observations[].reassigned` | recovery 时必填 | replacement counts/success rate 与 reassignment |
| `recovery_observations[].fault_at_ms`,`recovery_observations[].replacement_started_at_ms`,`recovery_observations[].replacement_ended_at_ms` | recovery 时可空且带 reason | 仅 recovery 时序诊断；不生成 recovery latency 指标 |
| `worker_death_observations[].worker_id`,`worker_death_observations[].pid`,`worker_death_observations[].exit_code` | worker death 时必填/可空且带 reason | worker death 真实性、failure 分类与 chain 诊断 |
| `worker_death_observations[].target_progress_ratio`,`worker_death_observations[].actual_progress_ratio` | worker death 时必填 | kill-progress signed mean/max；逐观察 pp 诊断 |
| `worker_death_observations[].original_attempt_id`,`worker_death_observations[].replacement_attempt_id` | worker death recovery 时必填 | worker-death reassignment 与 discarded trace identity |
| `challenge_observations[].challenge_plan_id`,`challenge_observations[].challenge_family`,`challenge_observations[].target_planned_ai_unit_id`,`challenge_observations[].attempt_ordinal`,`challenge_observations[].injection_boundary`,`challenge_observations[].opportunity`,`challenge_observations[].injected` | Exp4 challenge target dispatch/边界观察后必填 | challenge opportunity/coverage、mode 间 plan 等同与实际注入事实 |
| `challenge_observations[].source_semantics_preserved`,`challenge_observations[].candidate_independent_label`,`challenge_observations[].reached_verification`,`challenge_observations[].verifier_rejected`,`challenge_observations[].escaped_to_canonical_or_root` | 按 Exp4 challenge 适用 | parser challenge 语义等同；invalid candidate reject/escape/root linkage |
| `challenge_observations[].replacement_started`,`challenge_observations[].replacement_succeeded`,`challenge_observations[].valid_final_after_challenge` | challenge 触发 recovery 时必填 | replacement、valid-final-after-challenge 与 no-requeue 结果 |
| `ablation_observations[].disabled_mechanism`,`ablation_observations[].route_status` | Exp4 非 FULL 每个关闭机制各一条 | `disabled_mechanism`只标配置身份；`route_status=applied/not_reached/preempted_by_merge_first/missing_evidence`来自实际route。数量应等于disabled set大小，但行存在不证明gate执行 |
| `ablation_observations[].domain_parser_call_count`,`ablation_observations[].domain_child_checker_call_count`,`ablation_observations[].plugin_verify_submission_call_count`,`ablation_observations[].root_checker_call_count` | P/V challenge及兼容性观察时必填 | 证明真实parser/child checker/plugin verification是否调用，并把root checker独立计数 |
| `ablation_observations[].candidate_independent_label`,`ablation_observations[].wrong_canonical_accepted`,`ablation_observations[].root_checker_reached`,`ablation_observations[].root_check_passed`,`ablation_observations[].root_checker_rejected_after_wrong_canonical` | invalid或merge challenge适用时必填/nullable | independently invalid、真实canonical、checker reached与nullable pass；只有reached=true/pass=false才是明确root rejection |
| `ablation_observations[].raw_only_exposed`,`ablation_observations[].raw_only_accepted`,`ablation_observations[].parse_result` | 含 NO_PARSER_POLICY 且parser boundary适用时必填 | `parse_result=bypassed`存在Slim route observation而非公共submission字段；raw-only exposure/acceptance从实际refs/events派生 |
| `ablation_observations[].recovery_attempt_id`,`ablation_observations[].recovery_retry_allowed`,`ablation_observations[].replacement_attempt_id`,`ablation_observations[].stuck_due_to_no_requeue` | 含 NO_REQUEUE 且recovery challenge适用时必填 | linked retry decision、replacement absence与stuck count/rate；RM preempted时stuck=false |
| `ablation_observations[].merge_gate_satisfied`,`ablation_observations[].required_child_unit_ids`,`ablation_observations[].canonical_child_unit_ids`,`ablation_observations[].missing_required_slot_ids`,`ablation_observations[].plugin_merge_attempted`,`ablation_observations[].plugin_outcome`,`ablation_observations[].plugin_error_kind`,`ablation_observations[].premature_merge_attempted`,`ablation_observations[].premature_merge_failed`,`ablation_observations[].final_result_present`,`ablation_observations[].failure_stage` | merge challenge时按actual route必填/nullable | recovery-premerge readiness、typed plugin outcome、`not_attempted/rejected_incomplete_input/candidate_produced`、nullable checker与premature count/failure/rate |
| `missing_reason`,`not_applicable_reason` | 任一 nullable 指标输入缺失/不适用时必填 | reducer 决定输出 `null`，并解释 ineligible pair 与不适用专项指标 |

## 9. 用户已冻结的补充规则

以下规则已由用户明确决定，不再作为设计规格的开放问题：

1. Experiment 2–4 主矩阵只复用 Experiment 1 两个连续阶段产生的唯一 per-unit traces；lookup key 固定为 `case_id × source_repeat_id × planned_ai_unit_id` 且 `source_repeat_id=0`。下游实验 `repeat_id` 不进入来源键，并核对 Factorization range 或 Lean node/dependency 普通字段。
2. `relative_range(S0,S1)` 固定为 `(max(S0,S1)-min(S0,S1))/mean(S0,S1)`。
3. 除 Experiment 2 的六个 worker 档外，Experiment 1、3、4、5 全部固定 `worker_count=10`。
4. Experiment 2 六档在线并发检查和 Experiment 3 小型在线恢复检查全部退出 Slim V2；不再选择 case，不保留调用上限或输出表。
5. Experiment 4 固定使用 FULL、四个单机制和六个双机制的十一 mode 两两完备设计；不运行三重消融或 `ALL_OFF`。
6. Experiment 4 每个 `case_id × repeat_id` 固定一个先于 mode 生成的 challenge plan；注入器不知道 mode，11 个 mode 接收完全相同的 target set、attempt rule 和变换，不做 fault-rate sweep。
7. Experiment 4 的 mode-specific 配置/preflight BLOCK 使对应报告 cell 无效，不能当成任务失败或机制贡献；协议启动后的 no-final、stuck、incorrect-final 和 root checker rejection 才进入消融结果。
8. Experiment 2 取消独立 `factorization.exp2_contiguous_20way.v1`，每道题完整继承 Experiment 1 的 split、unit、prompt 与依赖；worker count 是唯一改变。
9. Experiment 1 每个root先取得结构化`run_root()`协议终态。仅同profile Exp2–4 trace-consumer集合中的来源root才立即对其`unscheduled_ai_unit_ids`执行Slim-local coverage tail；来源有效`no_final`同样执行，只有统一设施blocker阻断。tail只调用尚无protocol trace且具备合法request的planned units；Lean pre-dispatch failure不调用provider但仍形成`trace_origin=coverage_tail` typed trace。非来源root直接以`not_required_by_downstream`、空tail IDs和零资源提交。来源tail完成或按上限终止后才开始下一root；tail wall/token/cost单独记录，不进入Experiment 1正文协议资源。
10. Experiment 3 的 fault target 分母、`ceil` 数量、稳定均匀选择、ordinal 0 单次注入、五类固定动作及 token/latency 扰动公式按第 4.2–4.4 节执行，不再留给设计 Agent选择。
11. roots 在 runner 层串行；root timing 严格使用协议生命周期开始、终止与两者差值。Experiment 4 的五个 delta metric IDs 使用不带 `_median`/`_ms` 后缀的名称。
12. 前向 Experiment 1 成本使用第 1.4 节的 Flash `slim_v2.pricing.2026-08-23` flat 表；Experiment 5 保留 `slim_v2.pricing.2026-08-20` SiliconFlow 表。价格都是普通 reducer/projector 常量，不是预算或门禁。DeepSeek/SiliconFlow 的 `reasoning_tokens` 都是 `completion_tokens` 子集，不得重复计入 token 或成本；第 1.4.4 节 exact historical run 只保留已写入的 Pro 价格事实。
13. Experiment 2–4 请求的自然 ordinal 存在则精确读取；不存在则确定性读取该 per-unit trace 最后一个已有自然 attempt，不增加 provider call。回答/source result/usage/latency/cost 保持不变；Experiment 3 扰动仍按下游当前 ordinal 生成。
14. Experiment 4的V/P/R/M采用`slim_v2_exp4_structural_bypass_design.md`批准的实际调用前旁路；P和Lean V不能在真实parser/checker运行后才覆盖结果。
15. `{R,M}`固定为`RECOVERY_MERGE_FIRST`；M premature attempt抢先时R=`preempted_by_merge_first`、`stuck_due_to_no_requeue=false`，同一run禁止双阳性。
16. checker未到达固定表示为`root_checker_reached=false/root_check_passed=null`；inventory只给配置身份，所有ablation outcome必须从actual evidence派生。
