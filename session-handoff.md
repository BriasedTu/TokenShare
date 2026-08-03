# Session Handoff

更新时间：2026-08-04

本文件是下一任的紧凑权威交接。Task 的逐轮 RED/GREEN、完整 review、命令输出与旧状态快照已保留在 git history、`Doc/archive/design-history/2026-08-01-epd027-minimal-handoff.md`、归档 handoff、`progress.md` 和仓库外 `TokenShareData`，不得由本摘要反推或覆盖。

## 开工顺序

1. 完整阅读 `AGENTS.md`、`feature_list.json`、`progress.md` 与本文件；实验/runner/论文工作另读 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
2. 运行 `git status --short --branch`、`git log -3 --oneline`，只读确认唯一工作树与当前分支；不得 reset/clean/checkout 或覆盖用户改动。
3. 先运行默认 `powershell -NoProfile -ExecutionPolicy Bypass -File .\init.ps1`。feature 完成/合并前按 AGENTS 运行 Full；Lean 相关变更按分层配置追加 LeanAudit。

## 当前权威交接：Task 29 accepted / Task 30 frozen

- Active feature=`feat-011`，状态=`in-progress`；EPD-027 Task 0–29 已 accepted，计 `30/35`。active writer=`0`。
- Task 29 implementation commit=`555d9f3d0cb808b419ecbecfc59e37b2d1ace62d`（`feat(experiments): replace legacy smoke launchers`）；验收状态 commit=`ae865dc0`。
- Task 29 严格范围为 `21` 个 approved implementation 文件与 `10` 个已批准 `PLAN_OUT` 文件；无未批准文件。不得扩大到协议核心、模型/指标 contract、Lean adapter 或 renderer。
- 已审定、未在本交接复跑的 GREEN：canonical=`49 passed`；pipeline affected=`9`；CLI affected=`3`；core runner/observation/gate=`204`；official closure=`3`（complete CNY ready，USD/missing usage blocked）；runner=`158`；observation=`17`；complete-CNY 正例=`1`；PowerShell parse=`9/9`。review=`0 Critical / 0 Important`；历史 `3C/2I`、false-switch、Exp5 zero-role 已关闭；post-accept minimization=`0` 修改、`0` 删除、`0` 合并。
- provider/network=`0/0`，没有 Task26 verified paid receipt；正式 paper matrix 为 **NO-GO**，不得发起真实 API 或把 offline/trace 结果描述为当次 `real_transport`。
- 恰有 3 项 deferred Minor：Task28 facility execution predicate 细节；`run_paper_pipeline` 的 `offline_gate_parser_only`；Task29 trace 顶层 result/wrapper 不暴露两份 receipt digest。code map 未改，作为 milestone follow-up。
- Task30 仅为刚冻结、未实现。Tier 1 的 `progress.md`/本文件已压缩至约 `7.6KB/8.2KB`；Task29 遗留旧 launcher supervision 已由 commit=`2d7240f4` 同步。默认 baseline=`515 passed, 1 skipped`，provider/network=`0/0`，Task30 八文件 writer 可恢复。
- 随后仅实施既定 `8` 文件 RED → GREEN；baseline/状态压缩不构成 Task30 实现、验收或 formal matrix GO。最新 handoff anchor：2026-08-04 Task29 accepted / Task30 prestart；完整旧交接见上方归档路径及 git history。

## 已接受实现摘要（Task 0–28）

- Task 0–3：固定配置、ledger binding、typed-hook/direct-results 等 protocol/evidence 基础 accepted。Task 3 系列提交包括 `f9773944`、`66643084`；direct projector 不复制 `ProtocolEngine`。
- Task 4–11：request identity、immutable response bank、semantic inventory/preflight、SQLite WAL budget、durable acquisition/reconcile、deterministic scheduler、parent-owned commit ABI、trace-backed executor/dual provenance accepted。核心 commits：`0cbda1df`、`58cda71f`、`5f97034c`、`aadbd483`、`6802918d`、`1109e852`、`933ca71b`、`1b5d098e`。
- Task 12–20：Exp1–5 observations/projectors、registry/formal metrics、evidence eligibility、normal formal lifecycle、cell lineage observations accepted。重要 commits：`a1183c66`（Task12）、`3f8d4ec2`（Task13）、`440c1ef9`（Task16）；metrics 只能重投影持久化 event/artifact/attempt/provider facts，不得按 condition/fault 名称模拟。
- Task 19–24：正常 runner、canonical direct results、renderer、traceability replay 与 protected persisted generation accepted。核心 commits：`dd5f5eac`、`a161ea0c`、`8c1d7ccc`、`8863e8b3`。derived result/table/report 必须从持久化 ledger/artifact/manifest/digest closure 重投影；缺失或 drift 必须 fail closed。
- Task 25 online-check plan/evidence contract accepted（`c7db1948`）：capability=`4 calls`、Exp2=`24 refs/480 upper`、Exp3=`2 roots/12 upper`、`max_concurrent_roots=1`。在线检查必须当次真实 API，受 receipt/admission 门禁。
- Task 26 paid-receipt validator accepted（`e9ab3d9c`）：receipt/marker/scope/expiry/no-mint/allow-flag fail-close；synthetic fixtures non-authorizing。真实 receipt/provider/network=`0/0/0`，API key/env 不构成授权。
- Task 27 accepted（`b5f340f5`）：历史 canonical+network tripwire=`358 passed in 470.24s`；provider/network=`0/0`。Task 28 execution/publication gate accepted（`7338f177`）：正式 receipt/L1–L4/bank/terminal 缺失时稳定 BLOCKED，capability/facility 不得推导 formal publication PASS。

## EPD-027 关键实验与规模约束

- 两阶段回答库：Exp1/5 与 Exp2/3 小型在线检查是 `online_real_provider`；Exp2–4 主矩阵只消费经批准真实 API acquisition 的 immutable response bank，并完整重跑状态机，标为 `real_model_trace_protocol_run`。replay 不得重新调用 API。
- Exp3 主矩阵用 `discarded_trace_tokens`；`wasted_actual_tokens` 仅用于小型在线恢复检查，且故障/worker death 后必须有新 attempt、新 provider response/provenance 与独立 usage。
- DeepSeek acquisition/online check CNY hard stop=`1,000`，Task7/8 authority 已实现但无 receipt 不 dispatch；Experiment 5 SiliconFlow 预算单列。不得自动换模型或改变冻结请求控制。
- active profile=`paper_suite_scale_300_50_54.v1`。Exp1=`435/1,970/1,970`，Exp2=`600/12,000/12,000`，Exp3=`3,726/17,148/54,372`，Exp4=`975/4,410/7,938`，Exp5=`648/4,992/4,992`（roots/first-attempt units/provider-attempt upper）；总计=`6,384/40,520/81,272`。
- 冻结全量 ceiling：tokens=`23,503,151,360`，cost=`7,346.259328`，required disk=`63,406,407,680` bytes（59.05 GiB）。2026-07-31 E: free snapshot=`471,755,141,120` bytes；正式启动前按实际 output 重算。
- 共享 reference 索引与 500-root pressure 的正式证据未构成全量 GO；不得将离线 scale probe 描述为正式矩阵完成。

## 历史 smoke 的证据边界

- `exp34-smoke-20260731-041442` 是用户此前启动的历史真实运行：36 个 DeepSeek HTTP 200 attempts、839,054 tokens、估算 CNY=`4.6147626`（不是实付）。真实负向结果保留，不能改写为成功。
- 历史输出真实性审计曾通过，但 canonical paper-evidence audit 为 FAIL（no-return provenance、ledger body/hash、timing/fault refs、model inventory、replay closure）。历史 output 只读，不能升级为论文证据。
- 后续 canonical materialization、fault/recovery、Exp5 smoke evidence 与 renderer 已补 fail-closed 规则；这不改变当前 no verified receipt、provider/network=`0/0`、formal matrix **NO-GO**。

## 不可破坏边界

- V1 是本地可复现实验协议内核；不扩展为生产网络、攻击防护、动态 provider 平台或 Lean 服务。
- 正常 paper result 必须经 `ProtocolRunCoordinator`/`ProtocolEngine`、持久化 provider artifacts、deterministic verifier/fixed Lean checker；capturing/scripted 永远 paper-ineligible。
- provider failure 使用真实 taxonomy、固定 denominator、`null/NA`；不得补零、删失败或伪造成 success。TTFT 无持久化来源时保持 missing。
- 不改变正式矩阵、模型、请求参数、fault/repeat/worker-death matrix 或 tracked v1/v2 replay 配置；不新增攻击/安全工程。
- 不移动/删除 ACL 拒绝的 pytest 遗留目录；不 stage/commit/push/merge/PR，除非用户后续明确授权。

## 已知残留与验证规则

- 既有 P2：Exp5 bundle digest 内 source path 的跨机可移植性，以及 PowerShell 后代持有 pipe 时的 EOF 等待；均不得写作已修复。
- 旧 Fast 证据仅作历史参考；当前任何 feature 开始前以本轮实际 baseline 为准。Full、LeanAudit、真实 API、正式矩阵均非本次状态压缩的授权范围。
- 本文件若再次膨胀，应压缩重复逐轮日志而非删除上述 Task29/Task30、receipt/NO-GO 和实验口径锚点；完整证据保留在归档与 git。
