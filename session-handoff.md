# Session Handoff

更新时间：2026-08-20 +08:00

本文件是当前紧凑权威交接。逐轮 RED/GREEN、完整 review、旧快照与命令输出保留在 git history、`Doc/archive/`、`progress.md` 和仓库外 `TokenShareData`；不得由本摘要反推已删除的细节。

## 当前状态

- 唯一 workspace=`E:\TokenEcnomic\TokenShareWorktrees\exp-full-run`，branch=`repair/exp-full-run`；这是合法 dirty worktree。禁止 `reset/clean/checkout/stash`、删除或覆盖他人资产，禁止自行 stage/commit/push/merge/PR。`local/task6-*` 与 `local/task6*` 为只读证据。
- active feature=`feat-011`，状态=`in-progress`；本轮完成 current-price authority 接线、canonical external-bank binding 与 representative provider-zero plan-only；正式 Full bank acquisition/paid matrix/publication 仍未完成。
- R54 已完成 source/artifact 级根因审计：plan-only、receipt、paid 初次 reload/attestation 的预算 digest 都是 `sha256:551c5699...`，`Exp1 new_paid=false` 正确；真正分叉是 paid service 的第二次预算推导使用 current Exp5 pricing binding，产生 `sha256:8a004584...`。两份 authority 的 calls/tokens/CNY/new-paid 相同，只差 Exp5 pricing authority/member snapshot digests；失败发生在 external-bank resolver 与 Exp5 transport 之前，因此该次没有进入 provider dispatch。
- 直接缺陷是 fresh scope loader 只在 Full `approval_authority` 非空时向预算推导传 current Exp5 binding；系统性缺陷是预算在多个入口携带不同 optional context 重算。建议待批准后实施 single-authority repair，并把纯 authority 校验移到 readiness 消耗前；external-bank reuse 不再物化新的 Exp1 acquisition bundle。
- `R52`（run id=`r52-20260819-current-pricing`）已按用户要求启动后不监督：不得轮询/阅读其日志、推断终态、provider calls、花费、指标或通过状态。R51 仅保留旧的 provider=`0` blocked 记录。
- 当前轮只做 R54 离线审计与修复设计；没有启动新的 paid run、Full、LeanAudit、force-all 或 Full bank acquisition。

## Newfullrun 与 Full 边界

- `local/Newfullrun.ps1` + `local/newfullrun_audit.py` 只接受 selection=`full_exp1_exp3_exp5`（Exp4 excluded），从当前 Exp5 provider config 重建 pricing authority，计算 Full Exp1 acquisition + Exp5 online budget；Exp2/3 是 trace replay、provider=`0`。结果为 zero-call `awaiting_user_approval` authority，receipt=`not_issued`，不创建 bank 或 dispatch。
- `local/Newfullrun_execute.ps1` 现在按单一 A 自动执行 acquisition → immutable Full bank → scope preparation → plan-only → internal execution authorization → paid canonical `run-results-first --selection full_exp1_exp3_exp5`；本轮未调用它的 paid Full 分支。
- Full A、receipt、bank 和 canonical loader 已有同一 authority 校验；但因本轮未做 Full acquisition/paid execution，Full 仍不能写成已完成或已产生结果。
- Newfullrun 专项 `tests/experiments/test_newfullrun_scripts.py`=`5 passed in 181.33s`；Python `py_compile`、PowerShell parser、scoped `git diff --check` 通过；未调用 provider。

## Representative / Full 不可混淆

- representative 固定 `145` roots，Exp1/2/3/4/5=`12/6/81/30/16`；本次 selection `representative_exp1_exp3_exp5` 实际为 Exp1/2/3/5=`12/6/81/16`，Exp4 excluded。它不是 Full，不得用 Full、R38 pilot 或历史 smoke 补分母。
- Full authority 固定 `324 conditions / 6384 roots / 40520 first attempts`，不得重建 catalog/snapshot/inventory，不得复用 R13 representative bank。
- representative 的 Exp1–3 使用 R13 immutable response bank/source 语义及其旧定价；Exp5 由当前 provider config 重建 binding。Full 必须使用独立新 Exp1 bank 与当前全部价格，不能沿用 representative 预算/receipt/bank。
- Exp2–4 主矩阵只消费经批准真实 acquisition 的 immutable response bank，并完整重跑 `ProtocolEngine`、scheduler/lease/attempt、fault/recovery、worker death、ablation、verifier/checker、canonical、merge、settlement；证据类=`real_model_trace_protocol_run`，当前 provider calls=`0`。只有 Exp1、Exp5及另行批准的 Exp2 online checks/Exp3 recovery checks允许真实 API。

## Representative paid ledger 与 authority

- Exp1 acquisition inventory=`90`，`90/90 settled`，`76 success + 14 provider_failure/usage_missing`，provider calls=`90`，每 slot 至多一次、reacquisition=`0`。known actual=`1,733,487 tokens / CNY 10.021065`；usage-missing charged upper=`6,027,352 tokens / CNY 35.502660`。
- typed representative cap=`202 calls / 34,385,749 tokens / CNY 242.762815`，budget digest=`sha256:9f3e0ffbb7f27a5c970a27a1b9c5d62ceb5dd0634dd2dc1a308c114471540d8e`；Exp1 已占90 calls，后续 Exp5 上限112。用户外层 CNY1500 不得提高 typed cap。
- R13 ledger=`local/paid-representative-20260816-r13-paid-output/acquisition/acquisition_budget.v1.sqlite3`，SHA256=`ea778c0f5cadabc8cfa02f94d8b335eebb4d3a6c08d4130f45ed3b494fad53fe`，WAL/SHM absent。R15 plan-only source pin 因 bootstrap adapter 变化而 stale，不得用于 paid resume。

## 最近修复与阻断

- Exp5 pricing authority fix：R13 bank 只复用 Exp1–3 trace，Exp5 loader 从当前 provider config 重建 binding；定向 `2 passed`，未调用 provider。R50 response-artifact 与 partial-source finalizer 修复分别有 `32 passed, 35 deselected` 与 `28 passed` focused 证据，均未调用 provider。
- 历史 official provider-zero bootstrap session=`18419` 曾因 `staged_publication_preflight` / multi-entry ordered `entry_digests` 契约 fail-closed；进程已回收且 provider=`0`。该记录不是 R54 当前根因，不能替代本轮 focused authority 验证。
- L2=`8 passed`；L3/L4 exit=`3` blocked。Task34 post-fix L1=`882 passed`，Fast=`525 passed / 1 skipped`，review=`0 Critical / 0 Important`。这些是 boundary evidence，不是 formal publication PASS。
- execution/publication gates 仍 `BLOCKED`、DAG 无环、无 receipt/dispatch/provider attempt；execution digest=`sha256:b1dfa6edf77b3a0982dc72ce09fde74f1fe1300d7f055520c95cfe7cc721de3c`，publication digest=`sha256:7b49f79a7a22f63eb7bf9e3ba7056fd61c918b12deeed5070433a03113273d0c`。

## FullAudit 与正式规模

- `2026-08-18` FullAudit 是 zero-call preparation，不是 Full run：`234 conditions / 5409 roots / 36110 selected units`，source=`pending_new_full_exp1_acquisition`，`r13_bank_accepted=false`，projection=`6962 calls / 909477542 tokens / CNY 7113.566514`，状态=`ready_for_user_paid_receipt`，无 acquisition、receipt 或 provider。专项 focused `1 + 3 passed`。
- active profile=`paper_suite_scale_300_50_54.v1`；正式 Exp1–5 roots/first-attempt/provider-attempt upper=`435/1970/1970`、`600/12000/12000`、`3726/17148/54372`、`975/4410/7938`、`648/4992/4992`，合计=`6384/40520/81272`。ceiling=`23,503,151,360 tokens / CNY 7,346.259328`，启动前必须按批准 authority 重算。
- DeepSeek acquisition/online-check hard stop=`CNY 1000`；Experiment 5 SiliconFlow 预算单列。不得自动换模型、改变 thinking/request controls、删失败、补零或把 trace-attributed usage 记为当次在线支出。

## 历史接受摘要

- Task0–6：协议基础、ledger binding、typed hooks、direct-results、plugin/executor/storage/state-machine 基础已 accepted，离线 provider/network=`0/0`。
- Task7–18：request identity、immutable response bank、inventory/preflight、budget/WAL、acquisition/reconcile、scheduler、trace-backed executor、lineage、Factor/Lean、metrics、replacement、receipt/replay 门禁已 accepted。
- Task19–28：在严格 plan-out 必要性批准后接入 trace、lineage producers、direct merge、traceability、online checks、paid authorization、pipeline 与 execution/publication gate；专项回归通过，provider/network=`0/0`，无真实 paid receipt。
- Task29–34：readiness/profile、Full-resource、L1/L2/L3/L4 边界已验收；L3/L4 blocked，formal matrix 不得升级为 PASS。逐轮提交、审查和测试详情见 git history、`progress.md` 与归档。

## 接管动作、禁止事项和完成标准

1. 任何代码修改前先读 `AGENTS.md`、`feature_list.json`、`progress.md`、本文件、`Doc/agent-navigation.md` 与实验设计。默认 `init.ps1` 如运行只记录环境现状；由于其测试清单长期未同步，不作为 R54 根因、修复正确性或 readiness 判断。
2. R54 下一步先补真实三角回归：同一个 representative external bank 同时经过 fresh plan-only 与 paid-service budget path，禁止 mock 掉预算推导，并断言 current Exp5 pricing binding、`new_paid=false` 与最终 authority 完全一致；随后做最小 single-authority 修复。只允许 focused RED→GREEN、`py_compile`、scoped diff-check 与 provider-zero dry-run，不使用陈旧默认基线作判断，不重建 Full inventory。
3. 只有 representative 完整闭合、独立规格/质量审查 P0/P1=0、Fresh authority/budget/receipt/bank lineage 通过，才进入 Full 专属 authority 阶段；Full 必须另外审批、另外建 bank、使用最新全量定价。
4. 禁止 blind retry、并发 run、真实 API、Full、LeanAudit、force-all、stage/commit/push/merge/PR、联网资料与 destructive worktree 操作。secret 只报告 `SET/UNSET`，绝不输出值、片段、长度或 hash。
5. 正常结果必须经真实 `ProtocolEngine`/adapter/coordinator、持久化 provider artifacts、deterministic verifier/fixed Lean checker、固定 denominator、terminal truth 与 metrics lineage；capturing/scripted/历史 smoke 永远不能升级为论文在线证据。TTFT 无持久化来源时保持 missing。

## 残留与验证

- P2：Exp5 bundle digest 的 source path 跨机可移植性、PowerShell 后代持有 pipe 时的 EOF 等待、少量 runtime housekeeping；均不得写作已修复。
- P0：R54 budget authority 仍是多点重算；readiness 在第二次预算比较之前已被消耗，且 external-bank reuse 仍会物化 90-entry Exp1 acquisition bundle。修复前不得重试 paid run。
- 当前 formal matrix、receipt、publication、Full bank、Full current-price loader 消费链均未完成；这是本文件的 terminal conclusion。完整证据保留在归档和 git，后续应压缩重复历史而不删除上述边界。
