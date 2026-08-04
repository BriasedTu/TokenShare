# TokenShare 当前进度

更新时间：2026-08-04

本文件只保留当前权威状态、最近验收锚点和后续动作。逐轮命令、评审和旧测试细节由 git history、`Doc/archive/`、`session-handoff.md` 与仓库外 `TokenShareData` 保留完整记录。

## 当前权威状态（EPD-027）

- Active feature：`feat-011`（Paper Real AI Experiments），仍为 `in-progress`。
- EPD-027 共 35 个 Task；Task 0–33 已 accepted，计 `34/35`。active/current focus=`Task34`，为唯一剩余 `1/35`。
- Task 29 实现范围严格为 `21` 个 approved implementation 文件，另有 `10` 个已批准 `PLAN_OUT` 文件；无未批准文件。未扩大到协议核心、模型/指标 contract、Lean adapter 或 renderer。
- Task 29 已审定证据（验收时未复跑）：review=`0 Critical / 0 Important`；canonical=`49 passed`；pipeline affected=`9`；CLI affected=`3`；core=`204`；official closure=`3`（CNY complete ready，USD/missing usage blocked）；runner=`158`；observation=`17`；complete-CNY 正例=`1`；PowerShell parse=`9/9`。post-accept minimization=`0` 修改、`0` 删除、`0` 合并。
- provider/network calls=`0/0`，无经 Task 26 验证的 paid receipt；正式论文矩阵始终为 **NO-GO**，不得调用真实 API 或把离线/trace 结果描述为当次 `real_transport`。
- 正式矩阵 NO-GO 原因未变：没有 `Task 26 verified paid receipt`，且 provider/network=`0/0`。receipt、offline、mode、admission、budget、secret 等门禁仍须 fail closed。
- 恰有 5 项 deferred Minor：既有 Task28 facility predicate、`offline_gate_parser_only`、Task29 receipt digests、Task30 artifact-audit/tripwire，加 Task32 runtime 未 ignore 的 housekeeping；code-map milestone follow-up 保留。

## Task 30–33 accepted / Task 34 handoff

- prestart commits=`2d7240f4`/`012fcc32`，90-minute checkpoint=`23a21146`；Task30 implementation=`9ec806b4`。范围严格为原 `8` 文件加唯一批准的第 `9` 文件 `tests/experiments/test_paper_full_resource_trace.py`，无第 `10` 文件或 production 扩张。
- RED=`10/15`，canonical 最终=`25 passed`。profiles final：L1=`441`、L2=`8`、L3=`2`、L4=`2`，总计 `453 selectors / 892 items`；L1 首次=`32`，expanded=`855 passed`，final composite full-run=`877 passed` + `2` precise repaired passes。
- 新增 GREEN：Task19=`2`、Task23=`6`、Task24=`1`；L2 relative root=`2 passed`；L3 missing root blocked exit=`3`、outside root exit=`1`、PowerShell/Bash exit=`3`；overlap=`1 passed in 9.08s`；500-root=`1 passed in 1846.39s`。
- final review=`0 Critical / 0 Important`；minimizer `<3m` 且零修改。provider/network=`0/0`，无 receipt/secret；formal matrix 仍 **NO-GO**。
- Task31 初始 precondition 在 artifact directory 缺失时 fail closed、收集 `0 tests`；创建 planned exact directory 后，权威 profile 仅实际运行一次：L1 exit=`0`，`882 passed / 0 failed / 0 errors`，`441` exact selectors，`2155.58s`，digest=`sha256:d3dd21ed9fa0c15452019c70c8a90e1fa8a7d45564951043b54aed01f399018c`，status=`passed`。
- coverage 覆盖 Task0–29（含25–29）、Task24 pure=`2` / artifact audit=`0` 和 two-stage gate；Lean root=`1` / checker=`1<=2`。唯一 Fast exit=`0`：`525 passed, 1 skipped in 38.84s`。
- review=`0C/0I/0M`，证据链为真实 production、无 shadow；minimizer=`N/A`、零修改。provider/network=`0/0`，无 receipt/secret；L3/L4 unset，formal matrix **NO-GO**。既有4项 deferred Minor 与 code-map milestone follow-up 不变。
- Task32 runtime-only：无 source/test commit；保留 `local/verification/epd027-l2/{replay-a,replay-b,negative-exp34,l2-runtime-summary.json}`，工作树显示 `?? local/verification/`。profile exit=`0`：`8 passed in 8.41s`，status=`passed`，digest=`sha256:145059c...cef2a`。
- positive source=`heiyucode_gpt56_smoke_20260716`；number=`4733749`，predicate=`passed/true/true`，factors=`1013×4673`；case/batch/tree/raw/provenance digests 已在 runtime summary 记录。真实链 terminal=`SETTLEMENT_RECORDED`、无 shadow；classification=`regression_only`、`paper=false`、not formal。
- negative expected-fail hash before=after=`sha256:7f2a...1c60`。双 replay 使用独立 root；observations=`sha256:4b125...4192`、table=`sha256:c4f4...5ae9`、lineage=`sha256:9af8...a9a0` 与 ledger 相等，`113` files inventory/content 相同。
- review=`0C/0I`；minimizer=`N/A`、零修改。provider/network=`0/0`（历史 source attempt 独立，不等于本次调用）；无 receipt/secret，formal matrix **NO-GO**，L2 不替代 L4。
- Task33 owning fix=`a10e988ddfc923cca28423dfd83af60a96863a4f`，exact `12` 文件：sidecar/semantic-authority/environment/fixed_plan/catalog/audit/profile/Exp5 + `4` tests；PLAN_OUT 仅 `profile`、`Exp5`、`fixed_plan` 三个 production 文件，无未批准文件。
- RED module-missing exit=`2`；targeted=`140/140 in 30.10s`，contracts=`12/12`，native receipt=`1/1 in 6.07s`，L1 Lean=`1/1 in 3.12s`。bridge RED=`0/1 in 0.68s`→GREEN=`1/1 in 0.62s`，fixedplan=`1/1 in 0.72s`，diffcheck=`0`。
- 唯一 Fast exit=`1`：`524 passed / 1 failed / 1 skipped in 83.83s`，因 L1 function count `40!=39`；最小合并后失败 node=`1/1 in 0.25s`、既有 tests=`2/2 in 19.78s`，reviewer 明确无需重跑 Fast。final review=`0C/0I`，minimizer=`NO_CHANGE`。
- public CLI 仅一次 exit=`3 in 83.67s`，因 receipt absent 正确 BLOCKED；budget=`516/171708288/979.524864/CNY1000`，blocked digest=`sha256:47598f070d41715b99a58034b223c3c18664fa9a9a970af059f0750acaf5b8f5`；provider/network=`0/0`，3 roots/marker/ledger absent，launcher/audit skipped；runtime `local/verification/` 保留。
- rejected cascade：official 600 checker 曾 success=`516.23s`，但架构已弃用；Full 一次 exit=`1`：`2752p/86f/1s in 3826.97s`，未进入 LeanAudit、不重跑。formal matrix **NO-GO**，receipt absent。
- deferred：此前5项保留；新增 `8` 个 EOL/stat-only housekeeping，以及仅 broad Full 触发、未阻塞 Task33 正常路径的 ArtifactStore Windows long-marker `OSError 22`；code-map milestone follow-up 保留。
- Task34 handoff：仅 `local/verification/epd027-l4` runtime；因 L3 已 blocked，只能写 `l4_cell_traceability_blocked`，不得 L4 PASS；验证 execution/publication gate provider=`0`/no dispatch；tracked docs 按 Task34 计划，禁止 production/test；仅四个 focused profiles + Fast、no broad，并检查 `24` 项 review。Task34 是唯一剩余 `1/35`。

## 已接受历史摘要（Task 0–28）

- Task 0–3：固定配置、ledger binding、typed-hook/direct-results 等协议和 evidence 基础已 accepted；历史 Fast 曾为 `468 passed, 1 skipped`，均离线且 provider/network=`0/0`。
- Task 4–11：request identity、immutable response bank、inventory/preflight、SQLite WAL budget、acquisition/reconcile、deterministic scheduler、parent-owned commit ABI、trace-backed executor / dual provenance 已 accepted。所有验收保持本地 fake/trace 口径，不解锁真实 API。
- Task 12–20：Exp1–5 observations/projectors、registry/formal metric drafts、evidence eligibility、normal formal lifecycle、per-cell lineage 等已 accepted；历史 smoke/fixture 仅可作为 provenance 或回归，不能升级为论文在线证据。
- Task 21–24：canonical direct results、deterministic traceability replay、formal runner/persisted generation 和输出 lineage 已 accepted；derived artifact 只能从持久化 ledger/artifact/manifest 重投影，缺失/漂移必须 fail closed。
- Task 25：online-check plan/evidence contract 已 accepted；冻结 capability=`4 calls`、Exp2=`24 refs/480 upper`、Exp3=`2 roots/12 upper`、`max_concurrent_roots=1`。在线检查仍需当次真实 API，并受 receipt/admission 门禁。
- Task 26：paid-receipt validator 已 accepted（commit=`e9ab3d9c`）；真实 receipt persistence/provider/network=`0/0/0`，当前没有 user-provided valid receipt。
- Task 27：accepted（commit=`b5f340f5`），历史 canonical+network tripwire=`358 passed in 470.24s`；provider/network=`0/0`，无 paid receipt。
- Task 28：execution/publication gate accepted（commit=`7338f177`）；正式磁盘/receipt/L1–L4/bank/terminal 缺失时稳定 BLOCKED，不能由 capability/facility 推导 formal publication PASS。该 Task 留下上述前两项 deferred Minor。

## 2026-07-31 当前正式规模与资源门禁（EPD-026）

- active scale profile=`paper_suite_scale_300_50_54.v1`，Factorization 稳定 hash corpus=`100/100/100`；Exp1=300，Exp2 hard=50，Exp3/4 共享=`17/17/16`。旧 v1 profile 只供历史 replay。
- Exp5 active selection=`exp5_parent_quarter_selection.v4.json`，42 Factorization hard + Lean 三 topic 各 4，共 54 roots/model-repeat；8-root smoke suite=`paper_smoke_exp5_v4`，其 launcher 名仍为 `local/run_exp5_v3_smoke.ps1`。
- 精确正式计划：Exp1=`435/1,970/1,970`，Exp2=`600/12,000/12,000`，Exp3=`3,726/17,148/54,372`，Exp4=`975/4,410/7,938`，Exp5=`648/4,992/4,992`（roots/first-attempt units/provider-attempt upper）。Exp1–5 合计=`6,384/40,520/81,272`。
- 冻结全量 ceiling：tokens=`23,503,151,360`，cost=`7,346.259328`，required disk=`63,406,407,680` bytes（59.05 GiB）；2026-07-31 E: free snapshot=`471,755,141,120` bytes。正式启动前仍按实际 output 重算。
- 共享 reference 一次性索引与 500-root runner 压力证据尚非 formal GO；不得将离线规模探针宣称为正式全量内存风险已解决。

## 2026-08-01 两阶段真实回答库决定（EPD-027）

- `76,280` 保留为 Exp1–4 旧全在线 attempt 上界/trace-slot capacity；主矩阵在线 acquisition 预算须由稳定 outbound-body digest 枚举完整 bank inventory 后冻结。Experiment 5 SiliconFlow 预算单列。
- Exp2–4 主矩阵的完成率/机制指标由完整 TokenShare 状态机产生；wall-clock/token/cost/call 使用 trace-replay、trace-attributed、bank-slot 口径，不得冒充条件当次在线支出。
- Exp3 `wasted_actual_tokens` 仅用于小型在线恢复检查；主矩阵用 `discarded_trace_tokens`。故障后在线恢复必须有新 attempt、新 provider response/provenance 和独立 actual usage。
- DeepSeek acquisition 与在线检查目标 CNY hard stop=`1,000`；Task 7/8 已实现本地 authority，但没有 valid paid receipt 时不能真实 dispatch。

## 历史真实 smoke 与证据边界

- `exp34-smoke-20260731-041442` 为用户此前启动的历史真实运行：36 个 DeepSeek HTTP 200 provider attempts、839,054 tokens；估算 CNY=`4.6147626`，不是实付。负向结果保留为真实失败，不能改写为成功。
- 历史输出曾通过真实性审计，但 canonical 论文证据审计为 FAIL：no-return provenance、ledger hash/body、timing/fault refs、model inventory 与 replay closure 均存在问题。历史输出保持只读，不能升级为论文证据。
- 后续 canonical materialization、fault/recovery、Exp5 smoke evidence 与 renderer 已补 fail-closed 规则；但这不改变当前无 verified paid receipt、provider/network=`0/0` 和 formal matrix **NO-GO** 的结论。

## 未执行与边界

- 本轮不运行 Full、LeanAudit、force-all、真实 API smoke 或正式矩阵；不新增网络资料、provider 调用、stage、commit、push、merge 或 PR。
- V1 仍是本地可复现实验协议内核；不扩展到生产网络、安全对抗、动态 provider 平台或 Lean 服务。
- TTFT 无持久化来源时保持 missing，不伪造为 0；已知历史 P2（Exp5 bundle path portability、PowerShell EOF 极端等待）维持 deferred，不能表述为已修复。
