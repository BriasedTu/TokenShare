# Experiment 1–5 多题真实 smoke 设计

状态：用户已于 2026-08-09 授权执行与真实 API 预算。

## 目标与验收

本轮只验收 Experiment 1–5 的 results-first 多题 smoke，不以 `init.ps1 -Full`、receipt、L1–L4、publication closure 或论文资格门禁作为完成标准。

每个实验固定运行 4 道 Factorization 与 4 道 checker-backed Lean，共 8 个预注册 roots；五个实验合计 40 roots。每个 root 必须进入原 adapter、`ProtocolRunCoordinator`、`ProtocolEngine`、领域 parser/verifier 或固定 Lean checker，并持久化真实 provider request/response、usage 与终态。验收不要求答案全部正确，但要求真实结果、正确性分母、完成率、provider latency、prompt/completion/total tokens 与 cost estimate 均能正确记录；Experiment 2–4 的条件参数和 runtime hook 必须确实执行。

## 明确边界

- 可以直接跳过或击穿非指标实验设施的 receipt、publication、lineage、L1–L4 与历史 evidence closure。
- 不得绕过 provider/model identity、AI executor、`ProtocolEngine`、Factorization verifier、Lean checker、真实 usage/latency 与正确率计算。
- provider/auth/timeout/parse/verifier/checker failure 是真实实验结果，保留在固定分母中；不得改写成成功。
- smoke 固定 `regression_only=true`、`paper_eligible=false`，不得冒充正式论文矩阵。
- 不重写 runner，不新增影子执行器，不改变正式 catalog、正式 Exp2 Factor-only 矩阵或 Exp5 cohort。
- 本轮不运行 Full；只运行与修复直接相关的 RED/GREEN、identity-only、capturing/condition 定向验证和最终真实 40-root smoke。

## 执行拓扑

使用五个独立的 fresh output 顺序执行，而不是一个 40-root 单体进程：

1. Exp1：DeepSeek，8 roots。
2. Exp2：DeepSeek，8 roots。
3. Exp3：DeepSeek，8 roots。
4. Exp4：DeepSeek，8 roots。
5. Exp5：SiliconFlow，复用 `paper_smoke_exp5_profile.v4.json`，8 roots。

Exp1–4 新增四个 v1 regression-only profile。每个输出独立冻结 profile、plan、budget 与 source identity；最终由批次审计清单汇总 5×8。独立输出能隔离网络故障和 resume 风险，也避免 Exp5 的四模型 schedule 与 DeepSeek plan 相互污染。

每个实验先运行 provider-free identity-only，再使用同一 plan/budget identity 启动真实 transport。相同 output root 不允许并发进程。网络故障形成 terminal root 时保留本次结果；需要重试则使用新的 `a02` output，绝不覆盖旧 evidence。

## 题目与条件矩阵

所有 case 必须来自当前 canonical execution selection，profile 创建时冻结 exact case ID 与 selector。每个实验恰好四个不同 Factor case 与四个不同 Lean case。

- Exp1：Factor 覆盖 easy/medium/hard，并允许第 4 题复用一个 difficulty condition；Lean 覆盖至少三个 paper difficulty/topic cells。
- Exp2：Factor 与 Lean 均覆盖四个 worker_count，优先 `1/10/30/50`。Lean 只通过显式 regression-only smoke seam 加入；正式 Exp2 600-root Factor-only matrix、论文指标与 validator 保持不变。
- Exp3：Factor 与 Lean 各四题覆盖四个预注册 fault/runtime 条件，至少包含一个 rate fault 与 worker death；`baseline_policy=omitted_for_smoke_regression`，不要求 publication paired trace。
- Exp4：Factor 与 Lean 各四题覆盖四个 ablation hook；smoke 不以正式 FULL-pair publication closure 为门禁。
- Exp5：保持 v4 profile 的四个 cohort members，每个 member 各一题 Factor 与一题 Lean，合计 4+4；model/provider/reasoning/schedule identity 继续严格校验。

## 最小设施改动

### 1. 同一 condition 下多 root 分组

现有 resolver 将 `condition_id` 当作唯一 smoke item，导致 Exp1 第四道 Factor 题无法加入。改为：同一 condition 只 dispatch 一次，`root_case_filter[condition_id]` 保存全部有序唯一 case IDs；完全重复的 `condition_id + case_id` 仍拒绝。execution items 仍逐 root 保留，正确率/完成率分母不折叠。

预算 commitment 必须按 condition 分组后的全部 case 求和，不能用 `condition_id → single item` 覆盖前面的 roots。planned roots、AI units、provider-attempt ceiling 与运行时 hard-limit 必须一致。

### 2. Exp2 regression-only Lean seam

只在 smoke profile 明确包含 Exp2 Lean item 且 classification 为 regression-only/paper-ineligible 时，从现有 checker-backed Lean selection 派生 Lean conditions/bindings。派生 condition 保留 Exp2 worker_count、DeepSeek identity 与原 Lean adapter/checker，仍走 coordinator/engine。

默认 `expand_exp2_conditions()`、正式 Factor-only plan、600-root分母、paper validator 与论文表均不改变；formal/paper-eligible 调用不得看到该 seam。

### 3. 指标投影补齐

per-attempt evidence 已保存 prompt/completion/total tokens、latency 与 cost。smoke summary/row/CSV additive 增加 prompt 与 completion token 汇总，并保持：

- `total_tokens = prompt_tokens + completion_tokens`，逐 attempt 和聚合都可核对；
- 任一应有 provider evidence 的值缺失时聚合为 `null` 并记录 missing count，不补零；
- correctness denominator 固定为预注册 roots；`False` 是观测错误，`None` 是缺失；
- completion、latency、tokens、cost 与 provider-attempt count 均从持久化 attempt/root facts 投影。

### 4. 非指标门禁旁路

results-first smoke 保持 publication closure 默认关闭；不得重新引入 receipt、L1–L4、lineage 或 secret-scan 作为执行门禁。既有 secret 不落盘行为继续保留，但其报告不是本轮 dispatch gate。

## 网络恢复与30分钟轮询

当前任务启用30分钟 heartbeat。轮询时：

1. 若同一 output 的 runner 存活，只读观察，不启动副本；单次 provider timeout 为600秒，不能以短期无 checkpoint 判断卡死。
2. 进程退出后先检查8个 canonical roots、`CURRENT.json`、attempt artifacts 与 runner exit，再决定 replay/audit。
3. 只有非终态 root 可证明尚未 dispatch（无 prepared/raw/provider artifact）时才允许相同 identity resume。
4. prepared request 已存在但无 terminal checkpoint 属于是否已付费的歧义窗口，不自动 resume；使用新 attempt 前先审计。
5. Exp5 还要求有效 `audit/exp5_v3_execution_schedule.json`；hard kill 且 schedule 缺失时禁止 resume。
6. 401/403、key 缺失、identity drift、checkpoint 损坏或相同 output 已有活跃进程时停止该实验，不影响其他实验继续收口。

## 凭据现状

DeepSeek 凭据已在进程环境中可用，Exp1–4 可以执行。SiliconFlow 凭据当前未设置，Exp5 可完成 identity-only 与设施验证，但真实 provider run 必须等 `SILICONFLOW_API_KEY` 可用；不得用 DeepSeek key 代替、不得换 provider/model。该外部缺口不阻止先完成 Exp1–4。

## 完成证据

最终交付必须给出五个 fresh output 路径和一个批次汇总，逐实验证明：

- exact roots=8、Factor=4、Lean=4；
- real provider attempts 与 configured/requested/resolved identity；
- prompt/completion/total tokens、provider latency、cost estimate 的逐 attempt 与汇总一致性；
- correctness/completion numerator、denominator、rate；
- Lean child/merge checker 与 Factor verifier 的真实结果；
- Exp2 worker、Exp3 fault/worker-death、Exp4 ablation、Exp5 cohort/schedule 条件实际进入 runtime；
- replay/audit 不新增 provider call。

任何真实错误结果可使 suite 为 `completed_with_failures`，但非指标设施 gate 不得再把已经得到的系统结果改写为 blocked。
