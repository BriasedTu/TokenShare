# TokenShare 真实 AI API 论文实验设计与实施规格

状态：唯一权威实验设计

当前版本：2026-07-30

适用范围：Experiment 1–5、paper runner、预算、真实 API 执行、指标、表图和结果审计。

本文只保存当前有效规格，不再夹带逐轮修复日志。参数决策 provenance 看 `tokenshare_experiment_parameter_decision_log.md`；旧设计、实施计划和审计记录位于 `Doc/archive/design-history/`，不得覆盖本文。

## 1. 论文主张与证据边界

论文只围绕三个主问题：

1. 可行性：同一协议生命周期能否在 Factorization 与真实 Lean proof 两个领域中，用真实 AI 产生候选，再由确定性 verifier/checker 给出可审计结论。
2. 扩展性：固定任务、模型和 provider 时，增加协议 worker 如何改变端到端时间、吞吐、token、成本、失败率与利用率。
3. 鲁棒性：真实 AI 输出后发生预注册 fault 或 worker death 时，协议能否检测、隔离、恢复或结构化失败。

Experiment 4 用于解释协议机制贡献。Experiment 5 是同一 SiliconFlow provider 下的四模型 endpoint comparison，只作次要分析；serving profile、路由、限流和计费仍是混杂因素，不得解释成模型本体的纯因果效应。

deterministic/scripted/mock、direct benchmark、selected-unit partial、diagnostic 与 smoke 只能用于回归、成本校准或接线验证，固定 `paper_eligible=false`，不能写入论文主结果。

## 2. 全局硬门槛

一条 run 只有同时满足以下条件才可 `paper_eligible=true`：

- `real_transport=true`，使用预注册 provider/model/reasoning/request identity。
- 每个需要 AI 的 unit 有真实 provider attempt；request、raw、parsed/parse-failure、provenance、usage、latency、cost estimate 和 model execution record 均已持久化。
- provider 非确定性输出在首次执行时落 artifact；resume/replay/metrics/report 不重新调用 provider。
- Factorization 候选经过插件 parser 与 deterministic verifier；Lean 候选经过固定本地 Lean/lake/toolchain/library checker。
- AI 不决定协议级拆分。Factorization range 由插件确定性生成；Lean 只使用 catalog/脚本预注册并由插件校验的 fixed lemma-DAG。
- 正常 FULL 生命周期由 `ProtocolRunCoordinator` 与 `ProtocolEngine` 驱动，scheduler、lease、attempt、verification、canonical、recovery、merge、completion、settlement 均能回到 event/artifact evidence。
- secret 不进入 tracked config、event、artifact、SQLite、log、digest 或论文输出。
- catalog、selection、plugin/executor/prompt、model endpoint、request limits、budget、output root 和 evidence identity 均与冻结计划一致。

缺少任一门槛时必须给出结构化 `ineligibility_reasons` 或在 provider 调用前 `blocked`，不得回退到其他模型、scripted transport 或缩小矩阵。

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

### 输入 catalog

- Factorization：`factorization_catalog.v2.jsonl`，500 roots，easy/medium/hard=`167/167/166`。v1 的 30 题只供回归。
- Lean：正式 selection 为 3 个 `paper_difficulty` × 3 个 `topic_family` × 每格恰好 15，合计 135 个 checker-backed roots。
- Lean 正式 case 来自 `lean_lemma_graph_catalog.v1.jsonl` 与 `lean_task14_3x3_readiness.v1.json` 的冻结 selection；`lean_catalog.v1.jsonl` 只是 shallow legacy 输入。
- 每个 Lean case 必须冻结 environment digest、oracle package、fixed lemma-DAG、preflight/checker evidence。frontier stress 若本身未解或不可证，只能作为 structured negative，不得伪造成 checker success。

### Experiment 1：跨领域可行性与难度

- Factorization 500 + Lean 135，共 635 unique roots。
- 每题 1 次，合计 635 root-runs。
- baseline repeat/seed 为 `repeat_id=0, seed=1`；Exp3 shared reference 使用同 `case_id` 的持久化 Exp1 terminal evidence。
- 输出按 domain、difficulty、Lean topic family 报告 completion、accepted validity、failure stage/kind、wall-clock、provider latency、tokens、cost 和 paper eligibility。

### Experiment 2：真实 AI worker 扩展性

- 只使用 Factorization hard 的全部 166 roots；Lean 不进入。
- 插件 split profile：`factorization.exp2_contiguous_20way.v1`，每 root 计划 20 个连续 range units。
- worker levels：`1,3,7,10,30,50`；每档对同序 roots 运行 2 repeats。
- 合计 1,992 root-runs、39,840 planned first-attempt AI units。
- verifier-accepted factor witness 允许自然早停：不取消已发出的同批请求，只停止尚未调度的 sibling。
- 必须记录 planned/executed/unscheduled units、in-flight-at-witness、observed peak concurrency、真实 wall-clock/critical path、throughput、paired speedup、efficiency、utilization、429/retry、tokens 和 cost。worker 30/50 的观测峰值不得超过图宽 20。

### Experiment 3：真实 AI 故障与 worker death

Rate-fault 仅包含五类：

| Fault | 实际注入边界 | 主要观察 |
|---|---|---|
| `false_positive` | parsed candidate 后、verification 前 | 错误候选是否被拒绝、是否污染 canonical |
| `false_negative` | parsed candidate 后、verification 前 | 可适用候选被抑制后的检测/恢复；不适用目标按冻结 reserve 处理 |
| `no_return` | raw/provenance/usage 已保存、submission 前 | lease expiry 与 replacement |
| `late_submission` | raw 已保存、deadline 后提交 | late rejection 与 canonical 隔离 |
| `executor_error` | provider response 已保存、parser bridge 前 | request-stage 失败与 replacement |

- Factorization rate-fault：500 roots × 5 faults × `1/5/10/25/50/100%` × 2 repeats。
- Lean rate-fault：3 roots（三个 topic family 各 1）× 5 faults × `10/50/100%` × 2 repeats。
- 0% 不形成 Exp3 condition/provider call；从正式 Exp1 同 `case_id` evidence 投影 shared reference。
- Worker death 是独立条件：`worker_count=10`，`dead_worker_count ∈ {1,3}`，kill progress=`25/50/75%`，2 repeats；终止真实 executor process，保留 PID/exit、lease expiry、recovery 与 replacement facts。
- Experiment 3 合计：rate-fault 30,090 + worker-death 6,036 = 36,126 root-runs，supporting baseline roots=0。

注入 record 只描述实际变换，`detected`、`wrongly_canonicalized`、`recoverable/recovered` 必须从 verifier/canonical/recovery/completion evidence 派生。真实死亡但 replacement 失败时写完整失败记录并保留分母；不得抛弃已有 evidence 或把它计为成功恢复。

核心指标：detection rate、false-accept rate、recovery rate、completion、recovery latency、reassignment、wasted actual tokens、相对 shared Exp1 的 wall-clock/token/cost overhead、kill-progress error、result completeness 与 accepted validity。零分母写 `null` 和明确 applicability reason。

### Experiment 4：真实 AI 协议消融

模式固定为：

- `FULL`
- `NO_VERIFICATION`
- `NO_PARSER_POLICY`
- `NO_REQUEUE`
- `NO_MERGE_GATE`

Factorization 每档使用全部 `167/167/166` roots；Lean 每档使用固定 5-task `2/2/1` topic slice。五个 mode 均运行 3 repeats，共 7,725 root-runs。

五个 mode 的 `ProtocolConfig.max_retries=1`；`NO_REQUEUE` 只关闭 replacement gate。消融必须通过 `ProtocolMechanismPolicy`/runtime hook 作用于真实生命周期：不得根据 mode 名称事后填充错误、stuck 或 premature-merge 指标。

输出 completion、accepted validity、wrong canonical acceptance、raw-only exposure/acceptance、stuck task、premature merge attempt/failure、error exposure/escape、真实时间、tokens 和 cost。FULL 与各 mode 按同 `case_id × repeat_id` 配对；不适用或零分母写 `null`。

### Experiment 5：四模型 endpoint comparison

- 独立、分层确定性 hard-only 半量 selection：Factorization 83；Lean 三个 topic family 各 8，共 107 roots/model-repeat。
- 四模型 × 3 repeats：48 conditions、1,284 root-runs、9,888 planned first-attempt AI units。
- Token ceiling：607,518,720。
- 所有 entry 固定 provider-config namespace、entry、configured/resolved model、reasoning、cohort/config/endpoint digests；调用后 `missing_resolved_model` 或 mismatch 必须使 attempt paper-ineligible，并停止该 condition 尚未执行的 units。
- 报告逐 model/domain/topic/repeat 的 completion、validity、tokens、cost、wall-clock、provider latency/error/429、recovery、identity status；生成六个无序 model pair 和 3-repeat aggregate，并明确同 provider 混杂。

首次 8-root smoke 是产生 endpoint smoke evidence 的 bootstrap，固定 regression-only；正式矩阵 preflight 必须要求四 member 的 artifact-backed capability/identity/smoke evidence。

## 5. 总预算口径

| 范围 | Root-runs | First-attempt AI units | Provider-attempt upper bound |
|---|---:|---:|---:|
| P0-core（Exp1–4） | 46,478 | 246,998 | 640,550 |
| P0-full（Exp1–5 v3） | 47,762 | 256,886 | 650,438 |

Root-run 不等于 provider call。Exp3 0% reference 不新增调用；同一个 Exp1 root 在全局唯一真实执行分母中只计一次。Experiment 2 没有 100/300 worker extension。

Runner 必须先生成 `run_budget.json`，至少包含 roots、AI units、provider attempts 上界、token/cost/time/space estimate、quota/rate-limit preflight、模型与并发。预算不足时写 `budget_exhausted` 并停止新 task，不得静默删实验、mode、difficulty、topic 或 roots。

`--unlimited-budget` 只取消总 attempts/tokens/cost 硬上限，仍保留每-unit attempt 限制、请求控制、preflight、真实 usage 记录和全部 identity。它与人工 approval digest/总量上限参数互斥。

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
metrics/paper_table_feasibility.csv
metrics/paper_plot_scalability.csv
metrics/paper_plot_robustness.csv
metrics/paper_table_ablation.csv
metrics/paper_table_model_endpoint_comparison.csv
audit/paper_eligibility_report.json
audit/replay_report.json
```

每个 summary 数字必须能回到 condition/run/task/attempt、event refs 和 artifact refs。缺少真实 timing、identity、fault、merge 或分母 evidence 时写 `null`/reason，并使相应行不合格；不得用 mode/fault 常量、固定时间或现有记录数量填补。

## 7. Smoke、路径与 replay

- `paper_smoke_exp3_exp4_profile.v1.json`：Exp3–4-only，11 roots，显式省略 baseline，只作 regression。
- `paper_smoke_exp5_profile.v3.json`：Exp5-only 8-root bootstrap，四模型各 1 Factorization + 1 Lean。
- `paper_smoke_profile.v3.json`：29-root 综合兼容 profile；它不是正式矩阵，任何 v2-derived 兼容字段不得覆盖本规格。
- Exp1–4 v3 launcher 与历史 smoke 只作回归/诊断；所有结果固定 paper-ineligible。

普通实验 CLI 的新运行默认写入仓库同级 `TokenShareData/outputs/experiments/`；paper runner 必须显式指定全新 `--output-root`。execution-plan 与 budget digest 绑定实际绝对 output root，新 root 必须重新生成 identity。历史 output 迁移后内容和 digest 不改写、不得 resume；读取旧仓库绝对、`outputs/...` 仓库相对或 `runs/...` suite 相对 artifact root 时，只允许通过 `runtime_paths.resolve_persisted_data_path()` 做只读解析。

Smoke/resume/replay 不得改写已闭合历史 evidence。任何 replay 测试先复制代表性输出到隔离临时目录。

## 8. 当前实现与启动门禁

当前已实现：

- Factorization/Lean FULL 路径进入 system coordinator/`ProtocolEngine`；插件拥有领域 split/parser/verifier/checker/merge。
- Exp2 20-way、真实 worker timing/concurrency/早停投影；Exp3 五类 fault、shared Exp1 reference、真实 process death；Exp4 五模式 runtime hooks；Exp5 v3 identity/selection/sequence/budget 和离线 renderer 基础。
- 正式 metrics 从持久化 condition/task/attempt/event/artifact/runtime facts 复算；capturing/scripted 路径保持 paper-ineligible。

当前 readiness：

- P0-core 尚未产生可发布的正式真实-provider 结果。
- P0-full 为 **NO-GO**：canonical v3 provider-config binding、四 member artifact-backed 8-root smoke evidence、部分/失败运行的 1,284 分母与 missingness、六类 Exp5 renderer 的 production CLI/replay 接线仍需逐项闭合。
- 原始 transport 异常被其他 taxonomy 掩盖、blocked 诊断使用旧 member id 等 P2 问题也应在正式 Exp5 前修复。
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
3. 所有 paper-eligible 结果来自批准的真实 API 与完整协议生命周期 evidence。
4. Replay 不调用 provider；历史 evidence 不改写；secret scan 无命中。
5. 每个表图可追溯到逐 task/attempt/event/artifact，失败和 missingness 保留在分母中。
6. 相关定向测试、`.\init.ps1` 和交付前 `.\init.ps1 -Full` 通过；Lean 共享输入变化时按 verification profile 增量审计。
7. `progress.md`、`feature_list.json`、`session-handoff.md` 与当前 code map 已同步，旧实施计划只在 archive 中作为 provenance。
