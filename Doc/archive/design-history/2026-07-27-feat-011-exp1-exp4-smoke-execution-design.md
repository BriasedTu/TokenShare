# Feat-011 Exp1–4-only 真实 API Smoke 执行设计

日期：2026-07-27  
状态：用户已批准，仅用于本轮 smoke/regression 执行  
权威上位文档：`tokenshare_latest_real_plugin_experiment_design.md`

## 1. 目标与授权

新增一个独立、版本化、不可升级为论文证据的 Exp1–4-only smoke profile，并在所有启动门禁通过后使用真实 SiliconFlow `zai-org/GLM-5.2` 执行。预算总量无上限，不要求单独 `--plan-only` 或 budget digest；CLI 仍须在首次 provider 调用前完成内联 planning/preflight 并持久化全部 identity。

本授权不包含 pilot、正式 Experiment 1–5、Experiment 5、Full、LeanAudit、force-all、全量 Lean 或其他付费实验。任何 catalog、selection、model、endpoint、timeout、repeat、分母或 request-limit 漂移都必须在 transport 前 structured blocked。

## 2. 冻结矩阵

- Profile：`benchmarks/paper/paper_smoke_exp1_exp4_profile.v1.json`。
- Suite id：`paper_smoke_exp1_exp4_v1`。
- 直接 roots：21，按 Exp1/2/3/4=`6/3/7/5`。
- 实际调度 roots：22；Exp3 worker-death 生产 callback 额外执行 1 个 distinct no-kill supporting baseline。
- Items：完整 27-root profile 中原有 Exp1–4 items 原样保留，删除全部 6 个 Exp5 items；不得改 case、selector、repeat 或顺序。
- Repeat：每个 direct smoke item 固定 `repeat_id=0`。
- Output：`outputs/experiments/paper_smoke_exp1_exp4_20260727_run01`，不得复用以前 blocked/full-smoke root。

## 3. 模型与请求控制

- Provider：SiliconFlow。
- Model：`zai-org/GLM-5.2`。
- Entry：`glm_5_2_exp1_baseline`。
- Provider config authority：`benchmarks/paper/exp1_baseline_provider_config.v1.json`；本地 `local/ai_api_smoke.local.json` 只把匹配 secret 注入当前进程，不成为持久化配置权威。
- `timeout_seconds=100`。
- `max_tokens=1024`。
- `max_provider_attempts=1`。
- `temperature=0.0`、`top_p=1.0`、`stream=false`、`enable_thinking=false`。
- Budget mode：`unlimited`，总 provider attempts/tokens/cost 无 hard limit；不得改变上述单请求限制或预注册 recovery 行为。

## 4. 启动 identity

首次 provider 调用前必须写入 `smoke_launch_manifest.json`，至少冻结：UTC 启动时间、精确 Python argv、suite/profile/execution-plan/catalog/budget/config digest、21/22 分母、所有 condition/selection digest、model endpoint、request limits、output root 和 smoke classification。manifest 不得包含 secret 或 secret 值。

同一 output root 的 identity 不一致必须 fail closed。设施修复后的恢复优先使用验证过的 checkpoint；若必须新启动，则使用新的 run/generation identity并保留旧 root、日志、ledger、artifact 与对应关系。

`execution_plan_digest` 的 canonical body 包含实际 `output_root`，`budget_digest` 也通过 `output_identity.output_root` 绑定该路径。因此两个 digest 属于每个 output root 的运行实例 identity：换用全新 root 时应重新生成并冻结，不能把旧 run 的值当成跨 run 固定预注册值。跨 run 必须完全一致的是 profile/catalog/selection/provider-config digest、模型与请求控制、repeat/retry、worker/inflight 和 21 direct + 1 supporting 分母；同一 root 内上述语义或运行实例 identity 的任何漂移仍 fail closed。

2026-07-28 的 run02 因旧监督 prompt 错把 run01 的两个运行实例 digest 当成跨 run 固定值而在调用 provider 前 structured blocked；该结果永久保留且不复用。此修订只修正授权/监督门禁与启动说明，不修改 digest 算法、实验参数或任何历史 evidence。

## 5. 方案取舍

采用方案 A：独立 profile + 按 experiment scope 条件化 cohort preflight。它能把分母和 selector 固定在版本化 JSON 中，且不影响原 27-root profile。

不采用方案 B（新增 `--smoke-item`/动态过滤）：命令行选择容易形成未版本化分母和 selection 漂移。

不采用方案 C（运行时临时删除 Exp5 plan）：无法在 transport 前由 profile digest 证明授权范围。

## 6. 故障与终态

设施错误按 RED→GREEN 最小修复处理，保留原失败 run 并最多对同一根因自动修复/恢复两次。provider error、429、parser/verifier/checker rejection、未恢复故障、root failure 或零 completion 属于真实负面结果，只按预注册规则记录，不改模型、题目、分母或 retry。

最终终态允许 `succeeded`、`failed`、`blocked` 或 `incomplete`。所有层级固定 `formal=false`、`pilot_only=true`、`regression_only=true`、`paper_eligible=false`，不得生成 `formal_paper_report.md`。

## 7. 验证与监督

启动前运行 smoke/profile/CLI/formal evidence 相关定向测试和 `powershell -ExecutionPolicy Bypass -File .\init.ps1`，pytest summary 不得含 `FAILED`。启动后以不超过 10 分钟的间隔检查主进程/worker、run/generation/checkpoint、样本计数、provider usage/retries/429/tokens/cost、最新 event/attempt/artifact 和最后活动时间。轮询只读，不修改实验 evidence。
