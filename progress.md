# TokenShare 当前进度

更新时间：2026-08-04

本文件只保留当前权威状态、最近验收锚点和后续动作。逐轮命令、评审和旧测试细节由 git history、`Doc/archive/`、`session-handoff.md` 与仓库外 `TokenShareData` 保留完整记录。

## 当前权威状态（EPD-027）

- Active feature：`feat-011`（Paper Real AI Experiments），仍为 `in-progress`。
- EPD-027 共 35 个 Task；Task 0–29 已 accepted，计 `30/35`。Task 29 实现 commit=`555d9f3d0cb808b419ecbecfc59e37b2d1ace62d`（`feat(experiments): replace legacy smoke launchers`）；验收状态 commit=`ae865dc0`。
- Task 29 实现范围严格为 `21` 个 approved implementation 文件，另有 `10` 个已批准 `PLAN_OUT` 文件；无未批准文件。未扩大到协议核心、模型/指标 contract、Lean adapter 或 renderer。
- Task 29 已审定证据（验收时未复跑）：review=`0 Critical / 0 Important`；canonical=`49 passed`；pipeline affected=`9`；CLI affected=`3`；core=`204`；official closure=`3`（CNY complete ready，USD/missing usage blocked）；runner=`158`；observation=`17`；complete-CNY 正例=`1`；PowerShell parse=`9/9`。post-accept minimization=`0` 修改、`0` 删除、`0` 合并。
- provider/network calls=`0/0`，无经 Task 26 验证的 paid receipt；正式论文矩阵始终为 **NO-GO**，不得调用真实 API 或把离线/trace 结果描述为当次 `real_transport`。
- 正式矩阵 NO-GO 原因未变：没有 `Task 26 verified paid receipt`，且 provider/network=`0/0`。receipt、offline、mode、admission、budget、secret 等门禁仍须 fail closed。
- 恰有 3 项 deferred Minor：Task28 facility execution predicate 细节；`run_paper_pipeline` 的 `offline_gate_parser_only` stage；Task29 trace 顶层 result/wrapper 不暴露两份 receipt digest。

## Task 30 冻结交接

- 90 分钟 checkpoint：Task30 初始 `8` 文件实现已完成，canonical=`25 passed`；I2 path Important 已关闭，L1 扩至 `438` selectors / 四档 `883` cases。
- 最新 L1 实测 exit=`1`：`877 passed / 2 failed in 3479.84s`。blocker 是 Task22 两个有效 resource 节点仍使用旧的非法 Exp1+trace fixture，authority 已正确拒绝。
- scope expansion：implementer 已正式提出 PLAN_OUT_REQUEST、同一 reviewer 已确认；监督仅批准第 `9` 文件 `tests/experiments/test_paper_full_resource_trace.py`，production 零扩张；planned_out_files=该 `1` 文件。
- Task30 尚未 accepted，现保留未提交 `9` 文件 implementation diff；预计 `90–150` 分钟（500-root 定向可能约 `58` 分钟）。provider/network=`0/0`，formal matrix 仍 **NO-GO**、无 verified paid receipt。
- 最新 handoff anchor：2026-08-04 Task30 90-min checkpoint；本文件是当前短摘要，完整命令输出、review 轮次与交接风险以 handoff/history 为准。

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
