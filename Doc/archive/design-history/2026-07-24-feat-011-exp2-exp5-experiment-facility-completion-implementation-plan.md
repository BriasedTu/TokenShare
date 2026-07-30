# Feat-011 Experiment 1 参数同步与 Experiment 2–5 实验设施补全实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按 Task 执行；每个行为修复先用 `superpowers:test-driven-development` 写 RED，完成声明前使用 `superpowers:verification-before-completion`。步骤使用 checkbox（`- [ ]`）跟踪。

**Goal:** 先把 Experiment 1 已批准的单次运行参数同步到代码，再补齐 Experiment 2–5 已审计确认的实验行为与指标接线缺口，使实验由系统 runtime 执行真实生命周期，实验代码只设定条件、注入已批准的实验条件并观察结果，最终能生成有解释力、可回溯的论文数据。

**Architecture:** 继续使用现有 `ProtocolRunCoordinator`、`ProtocolEngine`、Factorization/Lean plugin runtime bridge 和 ledger/artifact projection，不重做已经完成的系统生命周期迁移。先统一真实 execution timing、worker facts、paper eligibility 和正式 metrics 生产路径，再让 Exp2、Exp3、Exp4、Exp5 分别消费同一套事实；禁止各实验用 mode/fault 标签、固定时间或 self-baseline 生成“看起来合理”的数字。

**Tech Stack:** Python 3、pytest、SQLite/JSON/JSONL、TokenShare `local_runtime` / `ProtocolEngine`、Factorization/Lean plugins、Phase 7 AI API executor、SiliconFlow/OpenAI transports。

---

## 0. 权威、范围和执行硬约束

### 0.1 文档优先级

1. 实验参数、矩阵和论文口径唯一权威：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
2. 用户参数决定 provenance：`Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`。
3. 本文：可直接开工的参数快照，以及 Exp1 参数同步、Exp2–Exp5 剩余行为/指标缺口的当前实施顺序、文件归属和验收方法。
4. `2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md`：已完成生命周期迁移的历史执行证据；不得重开已完成任务。
5. 2026-07-13/18/20 的旧实施计划、并发设计和 prompt pack：只保留历史 provenance；与 EPD-001～EPD-006 或本文冲突时，不得按旧数字实现。

### 0.2 必须遵守的三个用户要求

- **以跑通实验并获得有价值的数据为目标。** 只实现会影响正式运行、事实真实性、指标解释或预算/恢复的必要能力；不做无关重构，不建设生产平台。
- **不处理人为输入攻击。** 不新增 tamper/fabrication、path/SQL/JSON/command injection、schema/security fuzzing、auth/signature/ACL、恶意 plugin/executor/worker、Byzantine 或其他外部攻击防护。故障范围只包含五类 rate-fault、预注册 worker death 和 Exp4 ablation。
- **尽量不跑全量 Lean。** 日常 Task 只跑直接相关 pytest。只有修改经过 Lean parser/checker/canonical/merge 的共享调用链时，追加固定 Lean canary；只有 tracked catalog/checker 输入实际改变时才运行内容寻址增量 audit。不得默认运行 600-entry force-all、全量 Lean 实验或正式真实-provider矩阵。

### 0.3 本计划不做

- 不改变 Lean catalog/脚本预写固定 lemma-DAG 的策略，也不实现通用自动 theorem decomposition。
- 不把 `local_runtime` 变成生产分布式网络，不增加 HTTP worker pool。
- 不把实验指标、`PaperTaskResult` 或 `PaperAttemptResult` 重新变成协议状态权威。
- 不运行真实 AI API、pilot 或正式实验；本文完成后先重新生成 plan-only，真实调用由用户另行启动。
- 不清理、reset 或覆盖当前 dirty worktree；执行 agent 只修改自己 Task 列出的文件。

## 1. 单文件开工参数快照

### 1.1 本节的使用规则

执行 agent 应先按本节写测试和修改常量，不需要再从旧计划拼参数。只有以下情况才回到唯一权威设计：

1. 本节没有覆盖某个 schema/event 字段的精确定义；
2. 源码实际接口与本文列出的文件路径不一致；
3. 本文与 `tokenshare_latest_real_plugin_experiment_design.md` 出现冲突。

出现冲突时以唯一权威设计为准，并先修本文，不得从旧代码、旧测试或 2026-07-13/18/20 的计划中选择“看起来合适”的数字。

术语固定如下：

| 术语 | 含义 |
|:---|:---|
| root | 题库中的一道完整题，例如一个待分解整数或一个 Lean theorem。 |
| root-run | 一个 root 在一个正式 condition/repeat 下运行一次。 |
| planned AI unit | 插件为该 root 预注册、需要 AI 产出候选的工作单元。Factorization 通常是候选因子区间；Lean 是固定 lemma-DAG 中的 proof 节点。它不是“整个协议任务图的所有节点”的同义词。 |
| first-attempt provider call upper base | 假定每个 planned AI unit 首次都发出一次 provider 请求时的基数；早停可使实际值更少。 |
| replacement/provider retry | 首次请求之外，由协议恢复或 provider transport retry 产生的额外调用。 |
| budget | 运行前资源准备和硬限额；不能回填为实验结果。 |
| actual usage | 真实 response usage/attempt evidence 中的调用次数、token 和 cost；论文只报告这个值。 |

### 1.2 所有实验共用的模型和请求约束

Experiment 1–4 必须固定为同一个 endpoint identity：

```text
provider_config_id = exp1_baseline_siliconflow
entry_id           = glm_5_2_exp1_baseline
provider_family     = siliconflow
provider_model_id   = zai-org/GLM-5.2
reasoning_profile   = default
config              = benchmarks/paper/exp1_baseline_provider_config.v1.json
```

逻辑请求控制固定为：

```text
max_tokens            = 1024
timeout_seconds        = 100
max_provider_attempts  = 1
temperature            = 0.0
top_p                  = 1.0
stream                 = false
enable_thinking        = false
```

约束：

- Experiment 1–4 的首次 attempt、replacement、resume 都复用同一 identity；不能切换到 Qwen/OpenAI。
- `max_provider_attempts=1` 是一次 executor 请求内部的 provider transport attempt 上限，不等于协议 `max_retries`。
- Experiment 4 的 `FULL` 和五模式中的其他 mode 统一冻结 `ProtocolConfig.max_retries=1`；`NO_REQUEUE` 只关闭 `replacement_attempts_allowed`，不能把 FULL 也配成零重试。该值给一次协议 replacement 机会，因此单 unit 最多 2 个协议 attempts；每个 executor attempt 内仍只有 1 个 provider attempt。
- 真实 API key 只从环境变量或被 gitignore 的本地配置注入；本计划不新增任何攻击防护工作。

Experiment 5 的 cohort 固定来自 `benchmarks/paper/model_comparison_cohort.v1.json`：

| cohort member | provider | model | reasoning |
|:---|:---|:---|:---|
| `glm_5_2_siliconflow` | SiliconFlow | `zai-org/GLM-5.2` | `default` |
| `qwen3_6_27b_siliconflow` | SiliconFlow | `Qwen/Qwen3.6-27B` | `default` |
| `gpt_5_6_sol_high_openai` | OpenAI | `gpt-5.6-sol` | `high`，`reasoning_effort=high` |

三个 member 的公共逻辑控制必须一致：`max_tokens=1024`、`timeout_seconds=100`、`max_provider_attempts=1`、`temperature=0.0`、`top_p=1.0`、`stream=false`。按 2026-07-26 EPD-007，三者即使共同漂移到另一个 timeout 也必须整体阻塞。provider adapter 可以采用不同 API 字段名；provider 专有 reasoning 字段按上表保留，不要求把 OpenAI `reasoning_effort` 伪装成 SiliconFlow `enable_thinking`。任一 member 缺 config/entry/key/smoke/identity evidence，整个 Exp5 写结构化 `incomplete_model_cohort` 并且不发请求；不得只运行两个 endpoint。

### 1.3 已确认的正式矩阵

| 实验 | 当前权威参数 | root-runs |
|:---|:---|---:|
| Exp1 | Factorization 500 + Lean 135；worker=10；1 repeat | 635 |
| Exp2 | Factorization hard 全部 166 题；20-way；worker `1/3/7/10/30/50`；2 repeats | 1,992 |
| Exp3 rate-fault | 五类 fault；Factorization/Lean 既定题库和 rates；2 repeats | 35,120 |
| Exp3 worker death | worker=10；dead=`1/3`；kill=`25/50/75%`；2 repeats | 6,036 |
| Exp4 | `FULL + NO_VERIFICATION + NO_PARSER_POLICY + NO_REQUEUE + NO_MERGE_GATE`；3 repeats | 7,725 |
| Exp5 | Exp1 正式题库的全部 hard：Factorization 166 + Lean 45；3 endpoints；3 repeats | 1,899 |

### 1.4 Experiment 1：可行性与难度

| 参数 | 冻结值 |
|:---|:---|
| experiment id | `exp1_real_ai_feasibility` |
| repeat | 只运行 1 遍，`repeat_id=(0,)`，`seed_family=(1,)` |
| worker | `10` |
| Factorization | 全部 500 roots：easy=167、medium=167、hard=166 |
| Factorization split | 保留 Exp1 插件 profile：easy/medium/hard 每题分别为 2/4/8 AI units |
| Lean | 正式 135 roots：3 个 paper difficulty × 3 个 topic family × 每格 15 |
| Lean split | 保留 catalog/脚本预注册并由 Lean 插件校验的固定 lemma-DAG；不实现运行时自动拆 theorem |
| conditions | `3 Factorization difficulty + 9 Lean cells = 12` |
| root-runs | `500 + 135 = 635` |
| planned first-attempt AI units | Factorization `2,330` + Lean `570` = `2,900` |

实现目标是把 `paper_exp1.py` 当前的 `EXP1_FORMAL_REPEAT_COUNT=3`、`SEED_FAMILY=(1,2,3)`、`ROOT_RUNS=1,905` 改成上表，不改变题库、拆分或模型。

### 1.5 Experiment 2：Factorization worker 扩展性

| 参数 | 冻结值 |
|:---|:---|
| experiment id | `exp2_real_ai_scalability` |
| domain | 只运行 Factorization；Lean condition 数必须为 0 |
| roots | Exp1/正式 Factorization catalog 的全部 166 道 hard |
| factor-position 分布 | `early=53`、`middle=53`、`late=53`、`no_factor=7`；不得择优删题 |
| split | 插件 profile `factorization.exp2_contiguous_20way.v1`，每题恰好 20 个连续、无重叠、无空洞区间 |
| workers | `(1,3,7,10,30,50)` |
| repeats/seeds | 2 遍；`repeat_id=(0,1)`，`seed=(2000,2001)` |
| conditions | `6 workers × 2 repeats = 12` |
| root-runs | `166 × 6 × 2 = 1,992` |
| planned first-attempt AI units | `1,992 × 20 = 39,840` |

自然早停固定为：verifier 接受 factor witness 后，系统停止调度尚未发出的 sibling；已组成并发批次、已经发给 provider 的请求不追溯取消。worker=30/50 的单 root 实际并发上限都为 20，用于观察平台期。不能把 39,840 写成实际 provider calls。

### 1.6 Experiment 3：五类 rate-fault 与 worker death

共同参数：

```text
experiment_id = exp3_real_ai_fault_recovery
worker_count  = 10
repeat_ids    = (0, 1)
fault target selection seed = 300300
```

condition comparison seed 不使用一张手写数字表，必须保留现有稳定公式：

```text
330000 + first_8_hex(
  sha256(canonical_json(
    schema_version,
    matrix_kind,
    domain,
    task_slice_key,
    repeat_id
  ))
) mod 100000
```

该公式故意不包含 `fault_type/rate/dead_count/kill_position`，使同一 domain/slice/repeat 的比较条件共享 seed。

五类 rate-fault：

| fault | injection point | 目标行为 |
|:---|:---|:---|
| `false_positive` | `after_parsed_candidate_before_verification` | 用错误候选进入真实 verification；观察检测/错误 canonical，不预填结果 |
| `false_negative` | `after_parsed_candidate_before_verification` | 只在原候选真实有效时拒绝；无有效候选则 `not_applicable` 并从 reserve 补目标 |
| `no_return` | `after_raw_output_before_submission` | 保存真实 raw/usage 后不提交，等待 lease expiry/replacement |
| `late_submission` | `after_raw_output_late_submission` | 超过 lease 后真实提交，观察 late rejection/隔离 |
| `executor_error` | `after_raw_output_before_parser_bridge` | 保存真实 raw/usage 后产生 executor error，由协议恢复 |

矩阵：

| 子矩阵 | roots/condition | rates | conditions | root-runs |
|:---|---:|:---|---:|---:|
| Factorization rate-fault | 全部 500 | `0/1/5/10/25/50/100%` | `5×7×2=70` | `500×70=35,000` |
| Lean rate-fault | medium lemma-DAG 3 roots，三个 topic 各 1 | `0/10/50/100%` | `5×4×2=40` | `3×40=120` |
| Factorization worker death | 每个 difficulty 分别 167/167/166 | dead=`1/3`，kill=`25/50/75%` | `3×2×3×2=36` | `6,000` |
| Lean worker death | medium lemma-DAG 三个 topic 各 1 | dead=`1/3`，kill=`25/50/75%` | `3×2×3×2=36` | `36` |
| headline 合计 | — | — | `182` | `41,156` |

worker death 的 25/50/75% 指真实 `completed_ai_units/planned_ai_units` 进度门，不是 planned unit 序号。必须实际终止 1 或 3 个不同 OS executor processes，保留 coordinator，等待 lease expiry/reassignment/replacement。

matched baseline 口径：

- rate-fault 复用同 `fault_type × slice × repeat` 的 0% condition，不产生额外 root-run；
- worker-death 必须真实执行相同 case/repeat/seed/worker/request controls、但不 kill 的 dedicated baseline；
- dedicated baseline id 按 `domain × task_slice_key × repeat` 去重，不能按 dead-count/kill-position 重跑，也不能把 fault run 自己复制成 baseline；
- Factorization dedicated baseline：`(167+167+166) × 2 = 1,000` root-runs、`2,330×2=4,660` planned AI units；
- Lean dedicated baseline：`3 × 2 = 6` root-runs、`14×2=28` planned AI units；
- Exp3 论文 headline 仍为 41,156 root-runs；实际 plan-only 必须另外预算 1,006 supporting baseline root-runs，总调度 42,162；
- Exp3 headline planned first-attempt AI units 为 `191,788`；加 supporting baseline 后为 `196,476`，均不含 recovery replacement。

### 1.7 Experiment 4：五种协议模式消融

| 参数 | 冻结值 |
|:---|:---|
| experiment id | `exp4_real_ai_protocol_ablation` |
| modes | `FULL`、`NO_VERIFICATION`、`NO_PARSER_POLICY`、`NO_REQUEUE`、`NO_MERGE_GATE` |
| repeats/seeds | 3 遍；`repeat_id=(0,1,2)`，`seed=(4000,4001,4002)` |
| worker | `10` |
| Factorization | 全部 500 roots，easy/medium/hard=167/167/166 |
| Lean | 每档 5 roots：simple=`pure2/function2/induction1`；medium=`pure1/function2/induction2`；hard=`pure2/function1/induction2` |
| conditions | `(3 Factor difficulty + 3 Lean difficulty) × 5 modes × 3 repeats = 90` |
| root-runs | `(500+15) × 5 × 3 = 7,725` |
| planned first-attempt AI units | Factorization `2,330×5×3=34,950`；Lean `(7+23+34)×5×3=960`；合计 `35,910` |
| protocol retry | 所有 mode `max_retries=1`；只有 `NO_REQUEUE` 把 replacement gate 关闭 |

五种行为只能在真实 runtime gate 注入：

| mode | 唯一关闭项 | 主要观察 |
|:---|:---|:---|
| `FULL` | 无 | 配对 baseline |
| `NO_VERIFICATION` | verification gate | invalid candidate 是否进入 canonical |
| `NO_PARSER_POLICY` | parser policy gate | raw-only exposure/acceptance |
| `NO_REQUEUE` | replacement gate | rejected/expired 后是否 stuck、completion 是否下降 |
| `NO_MERGE_GATE` | merge readiness gate | 不完整依赖下的 premature merge 是否被 root recheck 拒绝 |

`NO_SLOT_INTEGRITY` 已删除，不得出现在 enum 的 formal allow-list、condition、budget、CSV 或论文表。`error_escape_rate` 对 `FULL/NO_REQUEUE` 为 `not_applicable`；有 gate 但暴露分母为 0 时为 `zero_denominator`，两者都写 `null` 而不是 0。

### 1.8 Experiment 5：三 endpoint hard-only 对比

Exp5 按 EPD-006 生成 36 个 conditions：

```text
3 endpoints
× 3 repeats
× (1 个 Factorization hard condition + 3 个 Lean hard topic-family conditions)
= 36 conditions
```

Exp5 首轮 AI-unit 预算基数为：

```text
Factorization hard: 166 × 8 = 1,328
Lean hard: 15 × 7 + 15 × 6 + 15 × 7 = 300
每 endpoint/repeat: 1,628
全部 endpoint/repeat: 1,628 × 3 × 3 = 14,652 planned AI units
```

该数不包含 provider retry 或协议 replacement，实际调用数仍只从 provider attempt/usage evidence 报告。

完整参数：

| 参数 | 冻结值 |
|:---|:---|
| experiment id | `exp5_real_ai_model_endpoint_comparison` |
| roots | Exp1 formal catalog 的 Factorization hard 166 + Lean hard_frontier 45 |
| Factorization split | 保留 Exp1 hard 的 8-way；绝不继承 Exp2 20-way |
| Lean split | `pure_logic/function_set/induction` 每格 15；AI units 分别 7/6/7 |
| workers | `10` |
| repeats/seeds | 3 遍；`repeat_id=(0,1,2)`，`seed=(5001,5002,5003)` |
| condition 组织 | 每 endpoint/repeat：1 个 Factorization hard + 3 个 Lean topic |
| conditions | `3 endpoints × 3 repeats × 4 = 36` |
| root-runs | `(166+45) × 3 × 3 = 1,899` |
| planned first-attempt AI units | `(166×8 + 15×7 + 15×6 + 15×7) ×3×3 = 14,652` |

selection 必须直接绑定 Exp1 formal catalog execution view 的 case IDs/order/digest，不得调用 Exp2 shared slice。三个 endpoint 使用相同 root、repeat、prompt、parser、plugin、worker 和公共请求控制。失败或恢复不得 model/provider failover。

### 1.9 总预算口径

| 范围 | 论文 headline root-runs | supporting baseline root-runs | plan-only 实际调度 root-runs | planned first-attempt AI units（含 supporting baseline） |
|:---|---:|---:|---:|---:|
| Exp1 | 635 | 0 | 635 | 2,900 |
| Exp2 | 1,992 | 0 | 1,992 | 39,840 |
| Exp3 | 41,156 | 1,006 | 42,162 | 196,476 |
| Exp4 | 7,725 | 0 | 7,725 | 35,910 |
| P0-core（Exp1–4） | 51,508 | 1,006 | 52,514 | 275,126 |
| Exp5 | 1,899 | 0 | 1,899 | 14,652 |
| P0-full（Exp1–5） | 53,407 | 1,006 | 54,413 | 289,778 |

解释：

- 论文表中的正式矩阵 headline 继续使用 51,508 / 53,407；1,006 个 worker-death no-kill baselines 是支持配对分析的额外真实执行，不混入 fault condition 分母。
- planned first-attempt AI units 是完整展开、尚未考虑自然早停的基数。Exp2 以及其他 Factorization run 可能因 witness 早停而少发请求。
- `run_budget.json` 还必须在上述基数上加入：Exp3 恢复 replacement reserve、Exp4 每 unit 一次 protocol replacement reserve，以及任何明确批准的其他协议 recovery reserve。
- provider `max_provider_attempts=1`，所以 provider transport 自身不再额外放大每个 executor attempt。
- 实际 provider calls、token 和 cost 只能从持久化 attempt/usage evidence 汇总；不得用本表估计值替代。

### 1.10 当前代码到目标值的修改清单

| 模块 | 当前代码/旧值 | 本计划目标 |
|:---|:---|:---|
| `paper_exp1.py` | 3 repeats；seeds 1/2/3；1,905 roots | 1 repeat；seed 1；635 roots；2,900 units |
| `paper_exp2_scalability.py` | Factorization+Lean；worker 1/3/10/30；5 repeats；10,300 roots；普通 2/4/8 split | Factorization hard-only 166；worker 1/3/7/10/30/50；2 repeats；1,992 roots；插件 20-way |
| `paper_exp3_fault_recovery.py` | 3 repeats；61,734 headline roots；部分 outcome 由标签预填 | 2 repeats；41,156 headline roots；另执行 1,006 baseline roots；outcome 从真实事实派生 |
| `paper_exp4_ablation_runner.py` | 6 modes；9,270 roots；FULL `max_retries=0` | 删除 slot mode；5 modes；7,725 roots；全部 mode `max_retries=1`，NO_REQUEUE 只关 gate |
| `paper_exp5_model_comparison.py` | 54 conditions；4,635 roots；复用 Exp2 mixed slice | 36 conditions；1,899 roots；直接使用 Exp1 hard-only selection |
| `paper_formal_metrics.py` | Exp2–5 formal CLI 仍走简化 rows | 每个实验只保留一条消费真实 evidence 的生产 summarizer |
| `paper_formal_report.py` | 缺独立 Exp5 comparison 表，部分正式 JSONL join 简化 | 接 strict v2 join 和全部正式 CSV/JSONL |
| `paper_budget.py` | legacy condition/root 常量，replacement reserve 不完整 | 使用本节 headline/support/AI-unit 参数和各实验明确 retry policy |

### 1.11 正式输出文件与最小字段

统一 output root 至少包含：

```text
suite_manifest.json
run_budget.json
conditions.jsonl
per_task_results.jsonl
per_attempt_results.jsonl
fault_injections.jsonl
events/event_log.jsonl
metrics/per_condition_summary.csv
metrics/paper_table_feasibility.csv
metrics/paper_plot_scalability.csv
metrics/paper_plot_robustness.csv
metrics/paper_table_ablation.csv
metrics/paper_table_model_endpoint_comparison.csv
metrics/model_execution_records.jsonl
metrics/failure_examples.json
audit/paper_eligibility_report.json
audit/secret_scan_report.json
```

专项最小要求：

- Exp1 feasibility：domain/difficulty/topic、case/repeat count、completion、accepted validity、wall-clock、actual tokens/cost、failure kind。
- Exp2 scalability：逐 root + 逐 repeat condition + 2-repeat aggregate；planned/dispatched/completed/unscheduled/in-flight units、actual peak concurrency、utilization、throughput、worker=1 paired speedup/efficiency、min/max/relative difference、factor position、429 sensitivity。
- Exp3 robustness：fault target/applicability/injection stage、original/mutated refs、detected/false accept/recovery/completion、canonical evidence、retry/reassignment、real kill progress/error、wasted actual tokens、matched baseline latency/token/cost overhead、2-repeat min/max/relative difference。
- Exp4 ablation：exposed/escaped counts、error escape applicability/rate、wrong canonical、raw-only exposure/acceptance、stuck、premature merge、completion/validity、time/token/cost，以及 FULL 配对。
- Exp5 comparison：endpoint/provider/model/reasoning、identity status/mismatch、domain/topic/repeat、completion/validity、真实 runtime/provider latency、429/error、actual tokens/cost、recovery，以及 3-repeat aggregate 和 `provider_confounding=true/false`。

每个正式数字必须能回到 task/attempt/event/artifact refs；缺证据时 `paper_eligible=false` 并给稳定 reason，不能以 0、固定时间或 mode/fault 标签补值。

### 1.12 禁止执行 agent 自行推断或改动

- 不得恢复 Exp1 三遍、Exp2 五遍、Exp3 三遍、Exp4 第六 mode，或旧 Exp5 easy/medium 题。
- 不得把 Exp2 改成只跑 7 道 `no_factor`，也不得删除会自然早停的 159 道 hard 题。
- 不得把 Exp2 的 20-way split 传播到 Exp1/3/4/5。
- 不得让 Lean 进入 Exp2；Lean 固定 DAG 不为扩展性曲线临时加宽。
- 不得让 AI 决定 Lean 协议级拆分；保留脚本/catalog 预注册的拆分。
- 不得将失败 task 自动换模型；重做仍使用该 condition 的 fixed endpoint。
- 不得把 configured worker 数、fault rate、ablation mode 或 planned timestamp 当作观察数据。
- 不得用预算 token/call 数生成实际结果。
- 不得新增五类 rate-fault 以外的攻击/故障类型，也不得做外部输入攻击防护。
- 不得为了文档或定向实现运行真实 provider、全量实验、全量 Lean 或 force-all Lean audit。

## 2. 文件归属与共享文件锁

| 层 | 文件 | 本计划中的唯一职责 |
|:---|:---|:---|
| runtime contract | `src/tokenshare/local_runtime/contracts.py` | parsed-candidate hook、真实 runtime observation contract；不导入 experiments |
| runtime coordinator | `src/tokenshare/local_runtime/coordinator.py` | 早停调度点、真实运行时间、progress gate、premature merge ablation attempt |
| worker backend | `src/tokenshare/local_runtime/workers.py` | 真实 execution facts、progress-triggered process termination |
| runtime projection | `src/tokenshare/local_runtime/projection.py` | 从 ledger/artifacts/backend facts 生成统一 runtime evidence |
| Factorization plugin | `src/tokenshare/plugins/factorization/{descriptor,split_strategy,runtime_adapter}.py` | Exp2 20-way profile、coverage 和 readiness；runner 不复制算法 |
| experiment condition | `paper_exp2_scalability.py`、`paper_exp3_fault_recovery.py`、`paper_exp4_ablation_runner.py`、`paper_exp5_model_comparison.py` | 各实验 condition、selection、矩阵和严格 summarizer |
| experiment hooks | `paper_faults.py`、`paper_workers.py`、`paper_ablation.py` | 只注入/观察已批准条件，不写协议状态 |
| formal runner | `paper_formal_runner.py`、`paper_formal_callbacks.py` | 执行 baseline/condition，保存实验观察；不伪造协议事件 |
| metrics/report | `paper_formal_metrics.py`、`paper_formal_report.py` | 唯一生产汇总路径和 CSV/JSONL |
| preflight/budget/CLI | `paper_model_policy.py`、`paper_budget.py`、`run_paper_experiments.py` | 控制变量、预算、plan-only 与启动门禁 |

`paper_formal_runner.py`、`paper_formal_metrics.py`、`paper_budget.py` 和 `run_paper_experiments.py` 是共享热点文件。多个 agent 可以并行准备 RED 测试和实验模块内部变更，但这些共享文件必须由一个 integration owner 串行合并。

## 3. 统一数据契约

实现时应使用以下版本化观察结构；字段可以落在现有 runtime result `summary`，不要求为了 schema 单独创建新 package：

```python
runtime_observation = {
    "schema_version": "tokenshare.protocol_runtime_observation.v1",
    "run_id": "...",
    "runtime_started_at": "...",
    "runtime_ended_at": "...",
    "runtime_wall_clock_ms": 0.0,
    "planned_ai_unit_ids": [],
    "dispatched_ai_unit_ids": [],
    "completed_ai_unit_ids": [],
    "unscheduled_ai_unit_ids": [],
    "in_flight_ai_unit_ids_at_witness": [],
    "witness_observed_at": None,
    "worker_execution_facts": [],
    "observed_peak_concurrency": 0,
}
```

约束：

- 正式时间来自真实 UTC/monotonic execution clock；协议 event 的 deterministic `now` 只用于测试和协议顺序，不再承担 wall-clock。
- `worker_execution_facts` 直接消费已有 `WorkerExecutionFact`，不得由 configured `worker_count` 反推。
- `paper_eligible` 由 suite/condition/task/attempt evidence coverage 计算；不能在 `_condition_metrics()` 固定为 `False`，也不能为缺证据的行固定为 `True`。
- Exp2–Exp5 的完整 summarizer 必须只有一条生产入口。保留旧 helper 可以作为私有适配层，但 formal CLI 不得同时维护“完整未接线”和“简化已接线”两套算法。

## 3A. Task A：同步 Experiment 1 单次运行参数

该 Task 是其他实验共享预算和 P0 总数的前置门禁，但不改 Exp1 的实验行为、题库或指标定义。

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp1.py`
- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `tests/experiments/test_paper_exp1_formal.py`
- Modify: `tests/experiments/test_paper_budget.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`

- [x] **Step 1: 写参数 RED**

断言正式 condition 只有 12 个，`repeat_id=(0,)`、`seed=(1,)`，每个 condition 的 frozen selection 与旧 repeat 0 完全一致，roots=635、planned first-attempt AI units=2,900。断言旧 repeat 1/2 condition id、1,905 roots 和 8,700 units 被拒绝。

- [x] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_budget.py tests/experiments/test_run_paper_experiments_cli.py -q
```

Expected: 当前 `EXP1_FORMAL_REPEAT_COUNT=3`、`SEED_FAMILY=(1,2,3)` 和旧 budget 常量导致失败。

- [x] **Step 3: 最小实现**

只修改 Exp1 repeat/seed/root-run/condition expansion 及其 budget/CLI identity；保留 worker=10、500+135 case IDs/order、Factorization 2/4/8 split、Lean 固定 DAG、GLM baseline 和现有真实 evidence contract。

- [x] **Step 4: 运行 GREEN**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_budget.py tests/experiments/test_run_paper_experiments_cli.py -q
```

Expected: PASS；无 provider call、无 Lean checker 执行。

## 4. Task 1：共享真实 timing、worker facts 与 eligibility

**Files:**

- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/local_runtime/workers.py`
- Modify: `src/tokenshare/local_runtime/projection.py`
- Modify: `src/tokenshare/experiments/paper_projection.py`
- Test: `tests/local_runtime/test_coordinator_full_lifecycle.py`
- Test: `tests/local_runtime/test_worker_death_recovery.py`
- Test: `tests/experiments/test_paper_projection.py`

- [x] **Step 1: 写真实计时和 worker-fact RED**

新增测试，使用可注入 observation clock 产生不同起止时间，同时保留 deterministic protocol `now`；断言 projection 的 `runtime_wall_clock_ms` 来自 observation clock，且每个 `WorkerExecutionFact` 的 `unit_id/attempt_id/worker_id/started_at/ended_at` 原样进入 runtime result。

- [x] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/local_runtime/test_coordinator_full_lifecycle.py tests/local_runtime/test_worker_death_recovery.py tests/experiments/test_paper_projection.py -q
```

Expected: 新测试因 runtime result 缺少统一 observation/timing/worker facts 而失败。

- [x] **Step 3: 实现 observation clock 和通用 facts projection**

`ProtocolRunCoordinator` 保留现有 `now` 作为协议事件 clock，新增独立的真实 observation clock；在 `run_root()` 入口/出口记录时间。每次 `WorkerBatchOutcome` 收束时收集其 `fact.to_dict()`；run 结束后统一投影 planned/dispatched/completed/unscheduled/in-flight facts。测试可注入 clock，生产默认真实时间。

- [x] **Step 4: 修正 paper eligibility 传播**

`paper_projection.py` 只从真实 transport、完整生命周期、必需 artifact/event refs 和实验专项 evidence 计算 eligibility；缺 timing 只让依赖 timing 的专项行 ineligible，不得把已经完整的普通 task 全部固定为 false。

- [x] **Step 5: 运行 GREEN**

执行 Step 2 的同一命令。Expected: PASS。

## 5. Task 2：收敛正式 metrics 为一条生产路径

**Files:**

- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Modify: `src/tokenshare/experiments/paper_formal_callbacks.py`
- Modify: `tests/experiments/test_paper_formal_metrics.py`
- Modify: `tests/experiments/test_paper_formal_report.py`

- [x] **Step 1: 写 evidence mutation RED**

对 Exp2–Exp5 各准备一份最小 formal evidence。只改 worker interval、fault outcome、canonical ref、ablation observation 或 model execution v2 record，断言对应 CSV 值同步变化；删除必需 evidence 后断言该行 `paper_eligible=false` 并带稳定 reason。

- [x] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py -q
```

Expected: 至少 Exp2–Exp5 专项行因现有简化 `_exp*_rows()` 或缺少输出文件而失败。

- [x] **Step 3: 建立单一 dispatch**

`recompute_paper_formal_metrics()` 对每个实验调用其严格 summarizer，或把严格逻辑迁入共享纯函数后由实验模块与 formal CLI 共用。删除用 condition/mode/fault 标签直接生成 outcome 的分支。

- [x] **Step 4: 修正 quantile 语义**

逐 task、逐 repeat condition 和跨 repeat aggregate 分层输出。不得对单个 condition wall-clock 值构造 `[wall_clock_ms]` 后声称 P50/P95；两遍只报告原始值/min/max/relative difference，三遍才能输出预注册的 repeat aggregate。

- [x] **Step 5: 运行 GREEN**

执行 Step 2 的同一命令。Expected: PASS。

## 6. Task 3：Experiment 2 矩阵与插件 20-way profile

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/plugins/factorization/descriptor.py`
- Modify: `src/tokenshare/plugins/factorization/split_strategy.py`
- Modify: `src/tokenshare/plugins/factorization/runtime_adapter.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `tests/experiments/test_paper_exp2_scalability.py`
- Modify: `tests/plugins/factorization/test_factorization_split_strategy.py`
- Modify: `tests/experiments/test_factorization_paper_adapter.py`

- [x] **Step 1: 写 EPD-003 矩阵 RED**

断言只生成 12 conditions（6 workers × 2 repeats）、每个 selection 固定全部 166 hard case IDs、root-runs=1,992、Lean condition=0、worker 30/50 保留。

- [x] **Step 2: 写 20-way plugin RED**

断言 `factorization.exp2_contiguous_20way.v1` 由插件解析，完整覆盖 `[2,floor_sqrt(n)]`，恰好生成 20 个连续、不重叠、无空洞 range children，proposal/merge plan/coverage digest 稳定；普通 Exp1/3/4/5 profile 仍为原 2/4/8。

- [x] **Step 3: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/plugins/factorization/test_factorization_split_strategy.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_factorization_paper_adapter.py -q
```

Expected: 旧 10,300-root 双域矩阵和缺少 20-way profile 导致失败。

- [x] **Step 4: 最小实现**

Exp2 selection 只传 `split_profile_id`；Factorization plugin 负责把 profile 解析为 `requested_child_count=20` 并生成 coverage。不得在 `paper_exp2_scalability.py` 复制 `_partition_contiguous_ranges()`。

- [x] **Step 5: 运行 GREEN**

执行 Step 3 的同一命令。Expected: PASS。

## 7. Task 4：Experiment 2 factor-witness 早停与扩展性指标

**Files:**

- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `tests/local_runtime/test_coordinator_full_lifecycle.py`
- Modify: `tests/integration/test_paper_protocol_runtime_integration.py`
- Modify: `tests/experiments/test_paper_exp2_scalability.py`
- Modify: `tests/experiments/test_paper_formal_metrics.py`

- [x] **Step 1: 写早停 RED**

构造 20 children、worker=3 的 Factorization root：第一批得到 verifier-accepted witness 后，断言下一批 sibling 不再 schedule；同批已经发出的另外两个 request 正常收束；no-factor root 仍执行全部 20 个 children。

- [x] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/local_runtime/test_coordinator_full_lifecycle.py tests/integration/test_paper_protocol_runtime_integration.py tests/experiments/test_paper_exp2_scalability.py -q
```

Expected: 当前 coordinator 在 readiness 检查前继续发完 Ready siblings，新测试失败。

- [x] **Step 3: 调整调度顺序**

当 expansion 已存在、没有 pending execution、准备创建下一批 lease 前，先调用 plugin `evaluate_merge_readiness()`。若 status=`ready`，冻结 witness time/in-flight/unscheduled facts 并进入 merge；不得取消已发 batch，不得把 Factorization 的 `found_factor` 词汇写进通用 coordinator。

- [x] **Step 4: 接通 Exp2 正式 CSV**

`paper_plot_scalability.csv` 至少包含：

```text
worker_count,repeat_id,case_id,factor_position_quantile,
planned_ai_unit_count,executed_ai_unit_count,
early_stop_unscheduled_count,in_flight_after_witness_count,
observed_peak_concurrency,wall_clock_ms,critical_path_ms,
provider_latency_sum_ms,throughput,speedup,parallel_efficiency,
worker_utilization,provider_attempt_count,total_tokens,cost,
completion_rate,http_429_count,retry_count
```

逐 root 以相同 `case_id × repeat_id` 与 worker=1 配对；两遍输出原始值/min/max/relative difference。worker 30/50 的 observed peak 不得超过 20。

- [x] **Step 5: 运行 GREEN**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/local_runtime/test_coordinator_full_lifecycle.py tests/integration/test_paper_protocol_runtime_integration.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_paper_formal_metrics.py -q
```

Expected: PASS。

## 8. Task 5：Experiment 3 矩阵、真实 parsed-candidate hook 与 applicability

**Files:**

- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/experiments/paper_faults.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Modify: `tests/experiments/test_paper_faults.py`
- Modify: `tests/experiments/test_paper_exp3_fault_recovery.py`
- Modify: `tests/integration/test_paper_protocol_runtime_integration.py`

- [x] **Step 1: 写两遍矩阵 RED**

断言 repeats=`(0,1)`，rate-fault=35,120、worker-death=6,036、合计=41,156；旧三遍 condition/digest 不再被接受。

- [x] **Step 2: 写 injection-stage RED**

断言 false-positive/false-negative 只在真实 parser 已产出 parsed candidate artifact 后、`record_execution_submission()` / verifier 之前触发；record 的 injection point 来自实际 hook。no-return/late/executor-error 保留现有 raw-output 后路径。

- [x] **Step 3: 写 false-negative applicability RED**

原输出无 factor/proof candidate 时，记录 `not_applicable` 并按冻结 reserve order 补位；若 condition 最终没有足够 eligible candidates，保留 candidate/eligible/injected 数并让该 denominator 明确，不得抛 `ValueError` 或伪装 executor error。

- [x] **Step 4: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_faults.py tests/experiments/test_paper_exp3_fault_recovery.py tests/integration/test_paper_protocol_runtime_integration.py -q
```

Expected: 旧 3 repeats、raw-hook stage 和无 applicability 逻辑导致失败。

- [x] **Step 5: 最小实现**

新增窄 `ParsedCandidateContext` / directive，携带 original parsed ref、candidate refs、unit/attempt/lease；hook 只能返回替换后的 candidate refs 和实验 observation，协议 submission/verification/canonical 仍由 coordinator/engine 写入。注入 record 不预填 `canonical_pollution`、`detected`、`recovered` 或统一的 `requires_replacement`。

- [x] **Step 6: 运行 GREEN**

执行 Step 4 的同一命令。Expected: PASS。

- [x] **Step 7: Lean 调用链最小验证**

由于本 Task 触及 Lean parsed candidate → checker 边界，只追加固定 canary：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python verification/run_verification.py --mode full --only-lean-canary
```

Expected: 固定 canary PASS；不运行 600-entry force-all。

## 9. Task 6：Experiment 3 真实 kill progress、dedicated baseline 与恢复指标

**Files:**

- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/workers.py`
- Modify: `src/tokenshare/experiments/paper_workers.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Modify: `src/tokenshare/experiments/paper_formal_callbacks.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `tests/local_runtime/test_worker_death_recovery.py`
- Modify: `tests/experiments/test_paper_workers.py`
- Modify: `tests/experiments/test_paper_exp3_fault_recovery.py`
- Modify: `tests/experiments/test_paper_formal_runner.py`
- Modify: `tests/experiments/test_paper_formal_metrics.py`

- [x] **Step 1: 写真实 progress gate RED**

分别在 completed/total 首次达到 25%、50%、75% 后才武装 termination；断言 observation 同时保存 target ratio、actual completed count、actual ratio、timestamp 和 error。不得用 execution index 或目标百分比回填 observed progress。

- [x] **Step 2: 写 dedicated baseline RED**

每个 worker-death condition 必须先/另行执行相同 case/repeat/seed/worker/request limits、但无 kill 的 baseline condition；故障 condition 只能引用其 condition id/evidence ref。删除 baseline 或把自身作为 baseline 时应 fail closed。

- [x] **Step 3: 写 outcome projection RED**

`detected/wrongly_canonicalized/recoverable/recovered` 从 verification、late rejection、canonical、recovery attempt 和 completion event refs 派生。真实 replacement 不依赖虚构 `REPLACEMENT_ACCEPTED` 事件名。

- [x] **Step 4: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/local_runtime/test_worker_death_recovery.py tests/experiments/test_paper_workers.py tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_metrics.py -q
```

Expected: 旧 planned-index kill、自身 baseline 和硬编码 fault outcome 导致失败。

- [x] **Step 5: 实现并接通正式输出**

`paper_plot_robustness.csv` 输出设计冻结的 detection/false-accept/recovery/completion、latency、reassignment、wasted actual tokens、matched overhead、completeness 和 kill-progress fields。两遍保留原始值/min/max/relative difference；缺真实 baseline/timing/canonical/recovery ref 时为 ineligible/null，不能填 0。

- [x] **Step 6: 运行 GREEN**

执行 Step 4 的同一命令。Expected: PASS。

## 10. Task 7：Experiment 4 五模式真实行为

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- Modify: `src/tokenshare/experiments/paper_ablation.py`
- Modify: `src/tokenshare/local_runtime/contracts.py`
- Modify: `src/tokenshare/local_runtime/coordinator.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Modify: `tests/experiments/test_paper_ablation.py`
- Modify: `tests/experiments/test_paper_exp4_ablation_runner.py`
- Modify: `tests/integration/test_paper_protocol_runtime_integration.py`

- [x] **Step 1: 写五模式矩阵 RED**

断言只含 `FULL/NO_VERIFICATION/NO_PARSER_POLICY/NO_REQUEUE/NO_MERGE_GATE`，90 conditions、7,725 roots；`NO_SLOT_INTEGRITY` 不得出现在 formal condition、budget 或报告。

- [x] **Step 2: 写四种行为 RED**

- `NO_VERIFICATION`：candidate 真实进入 canonical，独立 deterministic audit 可观察 valid/invalid。
- `NO_PARSER_POLICY`：逐 attempt 记录 raw exposure → candidate → canonical → final validity。
- `NO_REQUEUE`：FULL 与 ablation 的 `max_retries` 都严格等于 1，唯一差异是 replacement gate；真实 rejected/expired 后无 replacement 才算 stuck。
- `NO_MERGE_GATE`：readiness 未满足时实际执行插件级 incomplete/premature merge attempt，保存 attempt/result/root-check failure；不得伪造 required slots 或修改 FULL core gate。

- [x] **Step 3: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_ablation.py tests/experiments/test_paper_exp4_ablation_runner.py tests/integration/test_paper_protocol_runtime_integration.py -q
```

Expected: 旧六模式、FULL max_retries=0、mode-count 和 premature observation-only 路径导致失败。

- [x] **Step 4: 最小实现**

四种差异通过 `ProtocolMechanismPolicy` / runtime hook 生效。premature merge 只在 ablation 路径调用 plugin-level attempt 并记录实验 observation；协议 core 默认 gate、canonical slots 和 FULL 行为不变。

- [x] **Step 5: 运行 GREEN**

执行 Step 3 的同一命令。Expected: PASS。

- [x] **Step 6: Lean 调用链最小验证**

本 Task 触及 Lean verification/merge ablation path，运行一次固定 canary；不跑全量：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python verification/run_verification.py --mode full --only-lean-canary
```

## 11. Task 8：Experiment 4 专项指标

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Modify: `tests/experiments/test_paper_exp4_ablation_runner.py`
- Modify: `tests/experiments/test_paper_formal_metrics.py`
- Modify: `tests/experiments/test_paper_formal_report.py`

- [x] **Step 1: 写真实 evidence-derived RED**

每个专项 count/rate 只改变对应 canonical/raw/recovery/merge evidence 时才变化；只改 mode label 不得改变数值。

- [x] **Step 2: 接通字段**

`paper_table_ablation.csv` 至少输出：

```text
wrong_canonical_count,wrong_canonical_acceptance_rate,
raw_only_exposure_count,raw_only_acceptance_rate,
stuck_task_count,stuck_task_rate,
premature_merge_attempt_count,premature_merge_failure_rate,
exposed_error_count,escaped_error_count,
error_escape_rate,error_escape_applicability
```

FULL 与 mode 按相同 `case_id × repeat_id` 配对，并输出逐 task、逐 repeat condition、三遍 aggregate。

- [x] **Step 3: 运行验证**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp4_ablation_runner.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py -q
```

Expected: PASS。

## 12. Task 9：Experiment 5 hard-only selection 与公平控制变量

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- Modify: `src/tokenshare/experiments/paper_model_policy.py`
- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `tests/experiments/test_paper_exp5_model_comparison.py`
- Modify: `tests/experiments/test_paper_model_policy.py`
- Modify: `tests/experiments/test_paper_budget.py`

- [x] **Step 1: 写 EPD-006 selection RED**

断言：

```text
Factorization: Exp1 formal catalog 的全部 166 hard case IDs
Lean: Exp1 readiness/formal catalog 的 hard_frontier 三 topic × 每格 15，共 45
conditions: 36
root-runs: 1,899
planned first-attempt AI units: 14,652
```

selection digest 必须直接绑定 Exp1 formal catalog execution view，不得调用 `_shared_exp2_slice()`，也不得依赖 Exp2 的 20-way profile。

- [x] **Step 2: 写跨 endpoint request-control RED**

三个 member 的 `temperature/top_p/stream/timeout/max_tokens/request limits/max_provider_attempts` 和同 domain prompt/parser/plugin version 必须一致；provider 专有 reasoning 字段保留各自批准值。任一可比较控制变量漂移时整个 Exp5 preflight blocked。

- [x] **Step 3: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_model_comparison.py tests/experiments/test_paper_model_policy.py tests/experiments/test_paper_budget.py -q
```

Expected: 旧 54 conditions / 4,635 roots / Exp2 slice coupling 和缺少 cross-member controls 导致失败。

- [x] **Step 4: 实现 hard-only selection 和 controls snapshot**

member plan 显式持久化 normalized `request_controls`；cohort preflight 比较三个 member 的公共控制变量，并让 condition/budget digest 包含 controls snapshot。不同 provider 的 API 字段名先规范化为同一逻辑语义再比较，不要求把 OpenAI reasoning 参数伪装成 SiliconFlow `enable_thinking`。

- [x] **Step 5: 运行 GREEN**

执行 Step 3 的同一命令。Expected: PASS。

## 13. Task 10：Experiment 5 身份审计、v2 join 和正式比较表

**Files:**

- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Modify: `src/tokenshare/experiments/paper_formal_callbacks.py`
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Modify: `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- Modify: `tests/experiments/test_paper_formal_runner.py`
- Modify: `tests/experiments/test_paper_formal_metrics.py`
- Modify: `tests/experiments/test_paper_formal_report.py`
- Modify: `tests/experiments/test_paper_exp5_model_comparison.py`

- [x] **Step 1: 写 identity truth RED**

持久化一个 `resolved_model_mismatch` v2 record；断言 attempt 已被 adapter 隔离且正式 `model_identity_match_rate` 降低，不能因为 display provider/model/entry 相同或 `_apply_exp5_identity()` 添加 `fixed_entry_match` 而报 1。

- [x] **Step 2: 写 strict join RED**

formal `model_execution_records.jsonl` 必须调用 `build_exp5_model_execution_rows()` 消费真实 `tokenshare.paper_model_execution_record.v2`、raw identity、request/provenance/usage 和最终 task eligibility。断言使用 `model_execution_record_ref`，不再读取不存在的 `model_execution_ref`。

- [x] **Step 3: 写正式表 RED**

要求生成 `metrics/paper_table_model_endpoint_comparison.csv`，包含每 endpoint/domain/topic/repeat 的 completion、validity、tokens、cost、runtime wall-clock、provider latency、provider errors、429、recovery attempts、identity status；另有 3-repeat aggregate 和明确 `provider_confounding` 字段。

- [x] **Step 4: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_model_comparison.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py -q
```

Expected: 硬编码 `fixed_entry_match`、简化 JSONL、错误 ref 字段和缺少正式 Exp5 CSV 导致失败。

- [x] **Step 5: 实现五个已批准缺口**

1. identity match 只从 persisted v2 observed identity 计算。
2. strict v2 join 接入 formal production；修正 record ref。
3. 生成三端点正式表、3-repeat aggregate 和 provider confounding。
4. 使用 Task 1 的真实 runtime timing，并传播真实 paper eligibility。
5. 消费 Task 9 的公平 request-controls preflight。

- [x] **Step 6: 运行 GREEN**

执行 Step 4 的同一命令。Expected: PASS。

## 14. Task 11：预算、CLI、plan-only 与端到端 capturing 验收

**Files:**

- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `tests/experiments/test_paper_budget.py`
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`
- Modify: `tests/experiments/test_paper_gate_c_dispatcher.py`
- Modify: `tests/integration/test_paper_protocol_runtime_integration.py`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [x] **Step 1: 更新预算 reserve**

plan-only 从冻结 selection/AI-unit commitments 和每实验 retry/replacement policy 计算上界；必须同时验证 Exp1=635、Exp2=1,992、Exp3 headline/support=`41,156/1,006`、Exp4=7,725、Exp5=1,899，以及 P0-core/P0-full 实际调度 roots=`52,514/54,413`。Exp2 early-stop 的 39,840 仍作为无早停首轮上限，实际 usage 不得从预算回填。Exp3 dedicated baseline、Exp4 `max_retries=1` reserve 和 Exp5 14,652 首轮 units 都进入预算 identity。

- [x] **Step 2: 写 plan-only / CLI RED**

断言新矩阵 root counts、condition counts、selection digests、预算 digest、provider calls=0；旧 plan/budget digest 拒绝 resume/execute。

- [x] **Step 3: capturing 端到端验证**

使用 capturing/in-memory transport 证明完整路径能跑通和产出所有表，但全部结果保持 `paper_eligible=false`，provider calls/tokens/cost=0。不得把 capturing 结果写成论文结果。

- [x] **Step 4: 运行定向组合**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/local_runtime/test_coordinator_full_lifecycle.py tests/local_runtime/test_worker_death_recovery.py tests/plugins/factorization/test_factorization_split_strategy.py tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_exp4_ablation_runner.py tests/experiments/test_paper_exp5_model_comparison.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_budget.py tests/experiments/test_run_paper_experiments_cli.py tests/integration/test_paper_protocol_runtime_integration.py -q
```

Expected: PASS。

- [x] **Step 5: 运行 Fast**

```powershell
.\init.ps1
```

Expected: JSON/SQLite、harness、compileall 和 fast tests PASS。

- [x] **Step 6: 按需 Lean canary**

如果 Task 5/7 之后的集成修改再次触及 Lean parser/checker/canonical/merge 调用链，只运行：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python verification/run_verification.py --mode full --only-lean-canary
```

不运行 force-all。只有 tracked Lean source/toolchain/helper 输入实际改变，才追加：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.lean_catalog_audit --verify
```

Task 11 只修改预算 identity、formal artifact discovery/validation、CLI/capturing 回归与文档，没有再次触及 Lean parser/checker/canonical/merge 调用链，因此按本计划不重复 canary；Task 5/7 的固定 canary 证据继续有效。

- [x] **Step 7: 静态收尾**

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
git diff --check
```

Expected: JSON 可解析，`git diff --check` 无 whitespace error。

## 15. 推荐 agent 分工和串行门禁

可以并行准备但不能同时写共享热点文件：

| 工作包 | 可并行 owned files | 必须等待 integration owner 的部分 |
|:---|:---|:---|
| P：Exp1 参数同步 | `paper_exp1.py`、`test_paper_exp1_formal.py` | budget/CLI |
| A：Exp2 | `paper_exp2_scalability.py`、Factorization plugin、owned tests | coordinator、formal metrics、budget |
| B：Exp3 | `paper_faults.py`、`paper_workers.py`、`paper_exp3_fault_recovery.py`、owned tests | contracts/coordinator/workers、formal runner/metrics |
| C：Exp4 | `paper_ablation.py`、`paper_exp4_ablation_runner.py`、owned tests | coordinator、formal metrics/report |
| D：Exp5 | `paper_exp5_model_comparison.py`、`paper_model_policy.py`、owned tests | formal runner/metrics/report、budget |
| Integration owner | shared runtime、projection、formal runner/metrics/report、budget/CLI、docs | 串行合并 A–D |

门禁顺序：

```text
Task A Exp1 parameter gate
→ Task 1 shared facts
→ Task 2 metrics spine
→ Exp2 Task 3–4
→ Exp3 Task 5–6
→ Exp4 Task 7–8
→ Exp5 Task 9–10
→ Task 11 plan-only/capturing/Fast
→ 用户另行批准后才运行真实 pilot/formal experiments
```

## 16. 最终验收标准

- [x] Exp1 只生成 12 conditions、635 root-runs、2,900 planned first-attempt AI units，旧三遍参数不再进入 formal/budget/CLI。
- [x] Exp2 的 20-way、早停、实际并发、paired speedup 和平台期数据来自系统执行事实。
- [x] Exp3 的五类 fault stage、applicability、canonical/recovery outcome、真实 kill progress 和 dedicated baseline 均真实实现。
- [x] Exp4 只有五模式；四种专项行为真实发生，rate/applicability 从 evidence 派生。
- [x] Exp5 只使用 Exp1 全部 hard 题，独立于 Exp2；三端点控制变量可比较。
- [x] Exp5 identity mismatch 不再被硬编码 match 掩盖；v2 strict join 和正式 comparison CSV 已接通。
- [x] 正式 timing、worker facts、paper eligibility 和专项 metrics 只有一条生产路径。
- [x] capturing 端到端和 plan-only 通过，provider calls=0；没有运行正式实验。
- [x] 没有新增人为输入攻击防护或第六类 rate-fault。
- [x] 没有默认运行 600-entry force-all 或全量 Lean 实验；只保留必要 canary/增量检查证据。
- [x] 定向测试和 Fast 通过，状态文档、parameter log、code map 和唯一权威设计同步。

### 16.1 Task 11 收口证据（2026-07-25）

- P0-core/P0-full actual scheduled roots 为 `52,514/54,413`；planned first-attempt AI units 为 `275,126/289,778`。Exp3/Exp4 replacement reserve 分别为 `411,704/28,728`，因此 P0-core/P0-full provider-attempt 上界为 `715,558/730,210`。
- P0-core plan-only digest 为 `sha256:5e40ecfc3a4c59fba9b6a5e1e2fc06f88ed43c98d855f15fde49af1f7d4344a1`；旧 digest 在 formal dispatch 前 fail closed。
- capturing 端到端实际经过 formal runner、Factorization adapter、capturing transport、artifact/evidence join 和全部表生成，结果保持 `paper_eligible=false`，provider calls/tokens/cost=`0/0/0`。
- Task 11 规定的 13 文件组合在最终集成修复后为 `286 passed in 162.67s`；文档同步后最终 Fast 为 `331 passed, 1 skipped in 15.27s`，JSON/SQLite、harness、compileall 均通过。
- 本次没有运行 Full、LeanAudit、force-all、真实 API、pilot 或正式 Experiment 1–5；设施验收完成不等于论文实验结果完成。

### 16.2 证据真实性与指标完整性复核（2026-07-26）

Task 11 后的反伪造审计发现并修复了正式资格与逐层 evidence 不一致、Exp2 critical path 退化为 wall clock/单 attempt、Exp4 空 observation、Exp5 以现存 identity record 缩小分母等问题。当前实现门槛为：

- 正式资格只能由持久化的 `attempt → task → condition → experiment → suite → report` 自下而上聚合；report 独立复核逐层资格和 event/artifact refs。任一下层不合格、ref 缺失/无法解析、capturing/scripted、selected-unit partial 或 synthetic fallback 都只能生成 `formal_regression_report.md`。
- Exp2 `critical_path_ms` 由 task/AI-unit dependency、attempt interval、canonical、merge gate/record 和 root completion 的事件图计算；缺边或时间戳时为 `null` 且 Exp2 行不合格。该专项门禁不扩散到 Exp1/3/4/5：证据完整的 checker rejection、未恢复故障、消融失败或 provider/model failure 即使没有成功 merge/root-completion，也必须作为合格负面样本留在正式分母。429 主视图保留全部 run，sensitivity 视图按持久化 run id 明列排除项，不能替换主结果。
- Exp3 worker-death 输出从真实 process death、death 后 coordinator 调度/lease、replacement/canonical slots、merge/root checker 和 terminal evidence 派生；matched baseline 禁止 self-reference，并继续由 runner 验证同 case/repeat/seed/worker/model/request limits。
- Exp4 非 FULL 行必须有 schema/condition/case/repeat/mode 一致的真实 hook observation、输入/结果和 refs；空 observation、错 mode、缺 FULL 配对或不合格 FULL 均 fail closed，零分母写 `null` 与 applicability reason。
- Exp5 identity denominator 是全部应调用模型的 provider-attempt inventory；missing、duplicate、orphan、join-key mismatch、resolved-model/config/ref mismatch 任一存在即不合格，三端点 preflight 不完整时 Exp5 整体 blocked。

TDD 包含每项缺陷的反假阳性案例；本轮补充的负面终局门禁从 Exp1/3/4/5 四个预期失败加一个 Exp2 严格对照，得到 RED=`4 failed, 1 passed`、GREEN=`5 passed`，完整 formal metrics=`28 passed in 3.15s`。用户规定的九文件最低验收为 `251 passed in 120.27s`，最终 Fast 为 `331 passed, 1 skipped in 15.77s`。本轮没有运行 Full、LeanAudit、force-all、真实 API、pilot 或正式 Experiment 1–5；`feat-011` 仍须等待单独授权的正式 provider 实验与完整发布门禁，不能标记 done。
