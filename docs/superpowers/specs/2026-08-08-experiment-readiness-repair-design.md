# 全量实验可运行性快速修复设计

状态：用户已于 2026-08-08 批准执行。

## 目标

以最小改动恢复旧仓库的真实实验执行能力。题库必须完整；`ProtocolRunCoordinator`、`ProtocolEngine`、Factorization verifier 与 Lean checker 等系统本体不得绕过；真实 API 的 raw response、usage、latency、token、cost、completion 与正确率必须继续落盘并可汇总；Experiment 2–4 的条件和 runtime hook 必须可执行。

完成标准是一道 Factorization 与一道 Lean 题通过真实 DeepSeek API 完成端到端 smoke，并产出可核对的指标。用户已授权该小实验的 API 预算。

## 已确认现状

- Factorization v2 catalog 为 500 题，正式选择为 300 题；Lean lemma-graph pool 为 165 题，正式选择为 135 题，catalog 与 selection 摘要一致。
- 快速基线通过：`526 passed, 1 skipped`。
- Experiment 2–4 的条件构造和 capturing execution 定向验证通过：`6 passed`。
- 当前阻断集中在实验设施和本地 Lean 环境，而非协议核心：历史 implementation-plan raw digest 被当作执行门禁；Lake 的 compiled configuration 陈旧；blocked Lean golden evidence 无法稳定摘要；EPD-027 receipt/L1–L4 publication gate 阻止真实运行。

## 方案比较

### 方案 A：修复现有直接实验 runner（采用）

继续使用 `tokenshare.experiments.run_paper_experiments`。它已经连接真实 transport、正式 coordinator/engine、两个领域插件、artifact/event/attempt 与指标投影，并且没有 EPD-027 receipt/L1–L4 publication gate。仅修复当前三个确定性阻断，增加一个 regression-only 双题 profile，随后用现有 runner 验收。

优点是改动最少、最快，不复制执行器，也不需要伪造 receipt。代价是该 smoke 与后续快速全量运行不宣称满足旧 EPD-027 的 formal publication evidence；这正符合本轮“结果优先、允许放宽设施证据门禁”的授权。

### 方案 B：给统一 pipeline 增加 facility bypass 开关

为 `run_paper_pipeline` 增加显式开关，跳过 receipt、L1–L4 和 publication gate，但仍调用 official service。它能保留统一 CLI，却需要穿透多层 receipt/marker/factory 类型和 response-bank admission，改动面更大。

仅当方案 A 不能执行 Experiment 2–4 的现有正式条件时再采用。

### 方案 C：新写轻量 runner

新建只负责两题和指标的 runner。实现看似直接，但会复制调度、生命周期和指标逻辑，容易绕过系统本体，也违反“不重写实验设施”。不采用。

## 设计

### 1. 历史文档摘要降级为 provenance

`paper_pipeline_profile._load_authorities()` 仍解析并保留 implementation-plan 路径及声明摘要，但不再用当前 raw bytes 阻断 facility 启动。Factorization catalog、Lean catalog、provider config、scale profile、selection/readiness 等影响实验输入或真实请求的身份检查保持原状并继续 fail closed。

### 2. Lean 环境只针对陈旧 compiled configuration 自愈

`prepared_lean_environment()` 第一次仍执行原始 `lake env`。只有返回文本精确包含 `compiled configuration is invalid` 时，才使用同一固定 `lake_executable`、同一 project root、同一环境与 timeout 重试一次 `lake -R env`。其他错误保持失败；重试成功后仍由真实 Lean checker 校验证明，不接受未检查结果。

### 3. blocked Lean golden evidence 可摘要

passed evidence 继续强制 `split_certificate_ref`、environment/certificate digest 与 authority metadata。若 golden checker 已返回结构化 blocked evidence，则摘要只包含 schema、case、status/error、执行阶段与 `provider_calls_made=0` 等稳定字段，使 planner 返回 blocked 而非异常。它不得伪造 certificate 或把 blocked 视为 success。

### 4. 双题真实 smoke profile

新增 regression-only profile，只选择一个 Factorization case 与一个 checker-backed Lean case，固定使用 Experiment 1/FULL 路径和 DeepSeek v3 provider config。CLI 仍调用现有 `run_paper_experiments --real-transport`，输出写入全新仓库外目录。

数据流保持：catalog → dispatch plan → adapter → coordinator → `ProtocolEngine` → real API executor → raw/provenance/usage artifact → parser/verifier 或 Lean checker → canonical/merge/settlement → metrics/audit。

### 5. 指标与实验条件

smoke 至少验证：planned roots=2、两个 domain 均实际调用 provider、usage/token 与 provider latency 可读取、共同分母为 2、completion 与端到端正确率由真实结果计算。Experiment 2 worker levels、Experiment 3 fault/worker-death、Experiment 4 ablation 继续使用原 runtime hooks；以 capturing/离线回归证明条件可执行，不因本轮放宽证据设施而修改协议语义。

## 错误处理

- provider/auth/timeout/rate-limit/parse/checker failure 必须形成原有结构化失败和指标，不得补零或改写为成功。
- `DEEPSEEK_API_KEY` 只从环境读取，不写入 config、artifact、日志或摘要。
- 双题 smoke 的任一 provider 调用或 Lean checker 失败都使验收失败；保留输出用于诊断，不覆盖历史 evidence。
- 若方案 A 在真实 smoke 前仍被 EPD-027-only gate 阻断，才实施方案 B 的显式 facility-only bypass；不得修改协议核心。

## 验证

1. 每个修复先运行新增/现有失败测试确认 RED，再做最小实现并确认 GREEN。
2. 运行 catalog、Lean checker、readiness、smoke CLI 与 Experiment 2–4 定向回归。
3. 运行 `init.ps1`；交付前运行 `init.ps1 -Full`。
4. 最后在全新 output root 运行双题真实 API smoke，检查事件、artifact、attempt 与指标文件。

## 非目标

- 不重写实验 runner、metric projector 或 response-bank 系统。
- 不新增安全加固、攻击模型或生产级授权系统。
- 不改变正式 catalog、模型、请求参数、fault、worker、repeat 或 ablation 语义。
- 本轮 smoke 只证明设施可运行，不冒充完整论文矩阵已经执行或可发表。
