# Session Handoff

更新时间：2026-08-09

本文件是下一任的紧凑权威交接。Task 的逐轮 RED/GREEN、完整 review、命令输出与旧状态快照已保留在 git history、`Doc/archive/design-history/2026-08-01-epd027-minimal-handoff.md`、归档 handoff、`progress.md` 和仓库外 `TokenShareData`，不得由本摘要反推或覆盖。

## 开工顺序

1. 完整阅读 `AGENTS.md`、`feature_list.json`、`progress.md` 与本文件；实验/runner/论文工作另读 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
2. 运行 `git status --short --branch`、`git log -3 --oneline`，只读确认唯一工作树与当前分支；不得 reset/clean/checkout 或覆盖用户改动。
3. 新的代码修改前先运行默认 `powershell -NoProfile -ExecutionPolicy Bypass -File .\init.ps1`。当前 readiness 已按真实双题 smoke 验收，不得仅因交接重跑 Full、LeanAudit 或 API；全量真实矩阵须等待用户另行预算授权。

## 当前权威交接：readiness repair accepted / full matrix pending

- 唯一 active feature=`feat-011`，状态保持 `in-progress`；readiness repair focus 已 `accepted`，全量真实矩阵未运行。
- 修复链=`4f2527df/67f8ab38/e9c0364d/c50cea20/8a85d160/c67657a5/a26f919c/937288a2/3e102836/cc0878d7/209722b0/bb04b4f4`。Factorization catalog=`500`、Lean lemma catalog=`165`，均完整；Fast 最终=`526 passed / 1 skipped`。
- 系统本体未绕过：真实任务使用原 `ProtocolEngine`、adapter/coordinator、parser/verifier/checker 与 artifact/event/metrics。results-first 默认仅绕过设施 publication closure；需要历史严格 publication closure 时显式 opt-in。
- 已验收真实 smoke：`E:\TokenShareData\outputs\experiments\dual-domain-smoke-real-fixed-20260808-184048`，exit=`0`、status=`completed_with_failures`、roots=`2`、blocked=`0`；4 次 attempt 全部 HTTP `200`、DeepSeek identity matched；tokens=`93859`、cost=`0.527757 CNY`、provider latency=`920236ms`、correctness=`1/2=0.5`。
- Factorization `false` 为真实失败；Lean `true`，2 child + 1 merge 均经 checker accepted exit=`0`。不得因验收而把 Factor false 改为 true，也不得增加 both-accepted gate。
- smoke summary 已增加 correctness/completion/provider latency，且 missing latency 不补零；历史 JSON fixture 仅做文本 CRLF→LF digest 规范化，不放宽内容差异。
- Full 历史观测=`2847p/10f/1s`；10 项设施失败已定向修复。最后一轮 Full 按用户要求终止、不采信，不宣称 Full PASS，也不要再运行 Full 作为本轮标准。
- 下一动作：等待用户另行授权全量 API 预算后，用 results-first 路径运行全量真实矩阵；不要求 receipt、L1–L4 或 publication evidence，但 ProtocolEngine/checker/accepted/metrics 仍是硬边界。未经授权不得自动调用 provider。

## 前序交接：Task 34 accepted / EPD-027 implementation complete

- Active feature=`feat-011` 的 EPD-027 实施状态=`accepted`；Task 0–34 已全部 accepted，计 `35/35`。无 active/current Task、无下一实施 Task；active writer=`0`。
- Feature-level 状态保持 `in-progress`；上述 `accepted` 仅指 EPD-027 实施 focus，正式采集与论文矩阵仍等待经 Task 26 校验的 paid receipt。
- Task 29 implementation commit=`555d9f3d0cb808b419ecbecfc59e37b2d1ace62d`（`feat(experiments): replace legacy smoke launchers`）；验收状态 commit=`ae865dc0`。
- Task 29 严格范围为 `21` 个 approved implementation 文件与 `10` 个已批准 `PLAN_OUT` 文件；无未批准文件。不得扩大到协议核心、模型/指标 contract、Lean adapter 或 renderer。
- 已审定、未在本交接复跑的 GREEN：canonical=`49 passed`；pipeline affected=`9`；CLI affected=`3`；core runner/observation/gate=`204`；official closure=`3`（complete CNY ready，USD/missing usage blocked）；runner=`158`；observation=`17`；complete-CNY 正例=`1`；PowerShell parse=`9/9`。review=`0 Critical / 0 Important`；历史 `3C/2I`、false-switch、Exp5 zero-role 已关闭；post-accept minimization=`0` 修改、`0` 删除、`0` 合并。
- provider/network=`0/0`，没有 Task26 verified paid receipt；正式 paper matrix 为 **NO-GO**，不得发起真实 API 或把 offline/trace 结果描述为当次 `real_transport`。
- Task30 prestart=`2d7240f4`/`012fcc32`，90-minute checkpoint=`23a21146`，implementation=`9ec806b4`；严格原 `8` 文件加唯一批准第 `9` 文件 `tests/experiments/test_paper_full_resource_trace.py`，无第 `10` 文件/production 扩张。
- GREEN：RED=`10/15`、canonical=`25 passed`；profiles L1/L2/L3/L4=`441/8/2/2`，total=`453 selectors/892 items`；L1 首次32、expanded855、final composite877+2 precise repaired；Task19/23/24新增=`2/6/1`；L2 relative-root2；L3 missing-root exit3、outside-root exit1、PS/Bash exit3；overlap1/9.08s；500-root1/1846.39s。final review=`0C/0I`，minimizer零修改 `<3m`。
- provider/network=`0/0`，无 receipt/secret，formal matrix **NO-GO**。4项 deferred Minor：既有3项（Task28 facility predicate、`offline_gate_parser_only`、Task29 receipt digests）加 Task30 artifact audit 早于 pytest tripwire、但当前 stored replay 无 provider；code-map milestone follow-up 保留。
- Task31 无 source/test/runtime evidence commit（planned empty directory 被 git 忽略），仅本次 state acceptance commit。初始 artifact directory 缺失时 fail closed/`0 tests`；创建 planned exact directory 后 profile 仅真实运行一次：L1 exit0，`882/0/0`，441 exact selectors，2155.58s，digest=`sha256:d3dd21ed9fa0c15452019c70c8a90e1fa8a7d45564951043b54aed01f399018c`，status passed。
- coverage=Tasks0–29（含25–29）、Task24 pure2/artifact-audit0、two-stage gate；Lean root1/checker1<=2。唯一 Fast exit0=`525 passed, 1 skipped in 38.84s`；review=`0C/0I/0M` 且 production chain/no shadow；minimizer N/A、零修改。
- provider/network=`0/0`，无 receipt/secret；L3/L4 unset，formal matrix **NO-GO**。既有4项 deferred Minor 与 code-map milestone follow-up 不变。
- Task32 runtime-only、无 source/test commit；授权 runtime 保留 `local/verification/epd027-l2/{replay-a,replay-b,negative-exp34,l2-runtime-summary.json}`，状态为 `?? local/verification/`。profile exit0=`8 passed in 8.41s`，status passed，digest=`sha256:145059c...cef2a`。
- positive=`heiyucode_gpt56_smoke_20260716` / number4733749 / predicate passed,true,true / factors1013×4673；case/batch/tree/raw/provenance digests 在 summary 可查。terminal=`SETTLEMENT_RECORDED`、无 shadow；`regression_only`/paper=false/not formal。negative hash before=after=`sha256:7f2a...1c60`。
- 双 replay 独立 root，obs/table/lineage=`sha256:4b125...4192`/`sha256:c4f4...5ae9`/`sha256:9af8...a9a0` 与 ledger 相等；113 files inventory/content 相同。review=`0C/0I`；minimizer N/A零修改；provider/network=`0/0`（历史 source attempt 不计本次调用）。
- 无 receipt/secret，formal matrix **NO-GO**，L2 不替代 L4。deferred Minor 共5项：既有4项加 runtime 未 ignore housekeeping；code-map milestone follow-up 保留。
- Task33 owning fix=`a10e988ddfc923cca28423dfd83af60a96863a4f`，follow-up fix=`7c9dff7c`；主实现 exact12：sidecar/semantic-authority/environment/fixed_plan/catalog/audit/profile/Exp5+4 tests；PLAN_OUT 仅 profile/Exp5/fixed_plan 三个 production，无未批准文件。review=`0C/0I`，minimizer=`NO_CHANGE`。
- GREEN/验收：module-missing RED exit2；targeted140/140 30.10s、contracts12/12、native receipt1/1 6.07s、L1 Lean1/1 3.12s；bridge0/1→1/1、fixedplan1/1、diffcheck0。唯一 Fast=`524p/1f/1s in 83.83s`，计数40!=39；最小修复 node1/1 0.25s + existing2/2 19.78s，reviewer确认无需重跑。
- 唯一 public CLI exit3/83.67s：receipt absent BLOCKED；budget516/171708288/979.524864/CNY1000，digest=`sha256:47598f070d41715b99a58034b223c3c18664fa9a9a970af059f0750acaf5b8f5`；provider/network0/0，3 roots/marker/ledger absent，launcher/audit skipped；runtime保留。
- rejected cascade：600 checker success516.23s但方案弃用；Full exit1=`2752p/86f/1s in 3826.97s`，未进入LeanAudit且不重跑。formal matrix **NO-GO**、receipt absent。
- Task34 因 L3 receipt-absent BLOCKED，仅写 `l4_cell_traceability_blocked`；diagnostic root 不是 formal/canonical L3 terminal output，不得建立 L4 PASS。blocked record digest=`sha256:17ccfc2fc72184a9ee6151b910d462b5ba8014c8d55e9f9597931b3d6bbfa268`。
- execution/publication gate 均为 `BLOCKED`，DAG 无环、无 dispatch/receipt；execution/publication digests=`sha256:b1dfa6edf77b3a0982dc72ce09fde74f1fe1300d7f055520c95cfe7cc721de3c` / `sha256:7b49f79a7a22f63eb7bf9e3ba7056fd61c918b12deeed5070433a03113273d0c`。provider/network=`0/0`，provider attempts 为空。
- profiles：L2=`8 passed`，L3/L4 均 exit=`3`/blocked；首次 L1=`880 passed / 2 failed in 2834.24s` 暴露 Task33 bug，`7c9dff7c` 修复后 L1=`882 passed in 2807.21s`；Fast=`525 passed / 1 skipped in 75.85s`。Task34 review=`0C/0I`，Minor 仅既有 `offline_gate_parser_only` deferred；minimizer=`N/A`。未运行 Full/LeanAudit/600。
- Task34 仅修改计划内三份 tracked docs，本收尾提交再同步四份 state/handoff；无 production/test 修改。deferred 保留 7 类：Task28 facility predicate detail；Task29 offline label 与 top-level receipt digests；Task30 artifact-audit/tripwire order；Task32 runtime gitignore；Task33 8个 EOL/stat-only housekeeping；ArtifactStore Windows long-marker `OSError22`。
- EPD-027 实施已 accepted，但 formal matrix 仍 **NO-GO**，receipt absent。无下一 Task；未来只能在用户提供并通过 Task26 校验的 paid receipt 后进入 formal acquisition，不得自动调用 provider。

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
