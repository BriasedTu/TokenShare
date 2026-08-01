# Session Handoff

更新时间：2026-08-02

## 开工顺序

1. 完整阅读 `AGENTS.md`、`feature_list.json`、`progress.md` 和本文件。
2. 阅读 `Doc/agent-navigation.md` 与唯一实验权威 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
3. 运行 `.\init.ps1`；除非用户改变要求，不要运行 Full。

## 2026-08-02 EPD-027 当前实施交接（Task 5 accepted）

- Active feature=`feat-011`，仍为 `in-progress`；active focus id 保持 `paper-metric-contract-trace-bank-and-traceability-plan`，active focus status=`queued`。Task 0–5 已 accepted（`6/35`）；Task 6 queued/next 且尚未开始。
- Task 0–2 accepted 原提交不变：Task 0=`ed0b056a`，Task 1=`78fbddaa`，Task 2=`d2b16f89`。
- Task 3 的通用 ledger-binding prerequisite=`860b7c48`（7 files changed），最终 focused `40 passed`、独立 review PASS、provider calls=`0`。
- Task 3 的 typed-hook prerequisite=`4ea293b1`（18 files changed），最终 `69 + 286 passed`、独立 review PASS、provider calls=`0`。
- Task 3 commit=`f9773944`，严格改动 `paper_direct_results.py`、`paper_models.py` 及对应两个测试；最终 focused `101 passed`、独立 review PASS、provider calls=`0`。
- Task 3 post-task 测试压缩 commit=`66643084`，仅修改 5 个已复审测试文件，production 零修改；test defs / 估算 cases=`77/153 → 66/118`。canonical scoped exit `0`，`118 passed in 5.55s`，provider calls=`0`；独立 reviewer PASS，Critical/Important/Minor=`0/0/0`。
- 反影子结论：direct projector 只消费 canonical runtime facts、`ProtocolRunLedgerBinding` 与 `RuntimeHookObservationV1.from_dict()`；不复制 `ProtocolEngine` 或协议状态机，禁用 `ProtocolEngine` 不能形成 paper-eligible success。手工 typed fixture 只用于组件测试。
- Task 3 accepted 后 Fast：`.\init.ps1` exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 通过，provider calls=`0`。
- 状态同步时 `verification/run_verification.py` 被误判为无 pytest harness，实际重复执行 Fast：exit `0`，`468 passed, 1 skipped in 18.78s`；不算新增覆盖证据，后续不得重复。
- Task 3 测试压缩提交与本次状态同步未重复运行 Fast、Full 或 LeanAudit，也未调用真实 API。当前没有 paid receipt/付费授权，不得调用 provider。
- Task 4 exact outbound bytes/request identity/admission/Lean prompt v2 已 accepted，代码 commit=`0cbda1df`。focused=`125 passed`；canonical 初次 `323 passed / 39 failed` 后 fixture 逐步收敛；最终正式 E2E nodeid exit `0`，`1 passed in 6.68s`（total `7.635s`），日志=`%TEMP%\tokenshare_task4_canonical_single_selection_nodeid.log`；provider calls=`0`，network tripwire loaded。
- 综合 review 最终 `PASS`，Critical/Important=`0/0`；post-accept test minimization 3 分钟=`NO_CHANGE`，保留 5 个 distinct tests。`py_compile`、diff-check、name-only 均 exit `0`。
- 用户批准唯一计划外 production `src/tokenshare/experiments/paper_formal_evidence.py`：immutable `protocol_task_id` 改由 case task-id closure mapping 解析，并在映射冲突时 fail closed；review 确认没有 shadow protocol path。
- Task 5 immutable response-bank objects/index/opaque locator 已 accepted，代码 commit=`58cda71f`，严格五文件。canonical combined exit `0`，`17 passed / 0 failed / 0 skipped in 0.81s`；provider/network calls=`0`；3 个 production 文件 `py_compile` 与 diff-check 均 exit `0`。
- Task 5 综合 review=`PASS`，Critical/Important/Minor=`0/0/0`；post-task test minimization `<5min`=`NO_CHANGE`，保留 17 个 distinct cases。本轮 Fast、Full、LeanAudit 均未运行；未联网、未调用 provider。下一动作是 Task 6，当前仅 queued、尚未开始。

## 2026-07-31 用户暂停点（历史）

- 用户要求暂停并收尾；所有协作 Agent 已停止。不要自动恢复实现、真实 smoke 或正式全量。
- 当前工作树未 stage/commit，保留本轮代码、测试、设计与规模 profile。不要 reset/clean/checkout，也不要删除 ACL 拒绝访问的 pytest 临时目录。
- EPD-026 的 300/50/50/50/Exp5-54 规模与资源门禁已独立复审通过；这部分可以继续作为权威输入。
- 全量内存/增量 checkpoint 支线只完成到中间安全点：root-delta、terminal snapshot、PENDING repair、runner per-root checkpoint/release 与 compaction guard 已实现；暂停前 checkpoint/evidence `142 passed`，shared dispatch/fail-close/resume `3 passed`。
- 暂停收尾 Fast 已通过：`458 passed, 1 skipped in 21.06s`；未运行 Full。
- 正式全量仍为 **NO-GO**。恢复时首先完成 terminal-snapshot-only 的 `ValidatedSharedRootReferenceIndex`（一次构建、同 suite 复用、拒绝 stale/duplicate source），再补 500 synthetic roots 的 `max_live_full_outcomes <= 1` 压力证据、formal runner 整体回归和 Fast。当前 `build_shared_root_reference()` 仍按调用扫描 source run，不能把这一支写成已完成。
- 本次暂停未运行 Full、LeanAudit、真实 API 或正式全量。

## 2026-08-01 用户确认的 EPD-027（历史设计冻结节点）

- 用户接受两阶段真实回答库的修正版：Experiment 1/5 保持逐 unit 在线；Experiment 2–4 从不可变真实 API 回答库取输入，但仍完整运行 TokenShare 状态机和正式 fault/verification/lease/worker-death/requeue/merge/settlement/event-ledger 路径。
- 同一 repeat 内比较条件共享回答，不同 repeat 使用独立 sample slot；replacement 必须覆盖当前冻结最大深度。缺 bank entry 必须 blocked，不能临时在线补洞、scripted 回退或缩短 retry。
- Experiment 2 在线检查：缩小且预注册的题集，真实 API 覆盖 worker=`1,3,7,10,30,50` 全六档。Experiment 3 在线检查：小型真实恢复链，至少覆盖验证拒绝后 replacement 与 worker death 后重新分派；必须证明 fault/death 后才发生新 provider call。
- 用户明确要求把本方案记录为实验设施全面修改，而非简单修复。影响 request identity、executor/transport、artifact schema、paper eligibility、budget、runner、metrics/report/renderer、smoke/canary 和 replay/audit。
- 在该历史节点只完成 `design_synced`，尚未实现或调用真实 API；此后完整实施计划已形成，Task 0–5 已 accepted。不要再把本条历史快照当作当前状态。
- 旧 P0-core/P0-full 和“下一次 smoke”顺序暂停；EPD-027 设施与 profile 未实现前不得启动。EPD-026 的题量/fault/mode/repeat 不变，`76,280` 仅保留为旧全在线上界/trace-slot capacity；约 `6,904 + canary` 只是待 bank inventory 验证的 DeepSeek 量级。
- Experiment 2–4 主矩阵资源指标改为 trace-replay/trace-attributed；Experiment 3 主矩阵使用 `discarded_trace_tokens`。`wasted_actual_tokens` 只用于 Exp3 在线恢复检查，不能把预取回答重复计成 actual spend。
- 人民币 `1,000` 是 DeepSeek acquisition/在线检查目标硬停止线；当前每 attempt `0.05` 预留不可靠，后续必须同时约束 calls/tokens/CNY 和 in-flight 悲观预留。Experiment 5 SiliconFlow 预算单列。

## 当前业务状态

- Active feature：`feat-011`。EPD-027 accepted 进度为 `6/35`：Task 5 已 accepted；Task 6 及以后保持 queued。immutable response-bank primitives 已存在，semantic inventory/acquisition、trace-backed executor 与 formal runner/metrics/renderer/replay 接线仍属后续 Task，因此整体 paper readiness 未闭合。
- Active focus 已从“只写计划”进入严格串行实施。EPD-025 的 Experiment 5 `max_retries=0`、两张 model 汇总表、无六组 pairwise 继续冻结；Task 2/3/4/5 已提供 contract/direct facts/stable request identity/immutable bank primitives，正式 consumer/renderer/replay 接线仍待后续 Task。
- 真实 `exp34-smoke-20260731-041442` 已结束：11 roots、36 provider attempts、839,054 tokens、8 completed、3 evidence-complete experimental failures；配置口径 cost estimate CNY 4.6147626，actual billing unavailable。
- 真实性审计 PASS：36 个 DeepSeek HTTP 200 response ID 与 36 个 canonical v2 model records，v3 模型/请求身份一致，TokenShare coordinator/engine/ledger/artifact 路径完整，secret scan 零命中。
- 历史 canonical 论文证据审计 FAIL：两个 no-return provenance 未进入 canonical index、906/906 canonical ledger bodies 被改写但旧 hash 保留、timing 被补零/缺失、fault refs/model inventory/replay 不满足权威。历史 output 只读且不得用于论文。
- P0-full 仍是 **NO-GO**：修复后尚未产生新的 Exp3/4 11-root canonical 证据；Exp5 8-root smoke 也尚未运行，`audit/exp5_endpoint_smoke_evidence.json` 尚未产生。
- Exp3/4 修复后离线独立审计 Ready：无 Critical/Important/Minor；canonical materialization/ledger、nullable metrics、model inventory、independent replay、smoke evidence/exit semantics 均 fail closed。
- Exp5 离线独立审计 PASS：artifact-backed bundle、formal binding、condition-local fail-stop、fixed denominator/missingness、6 audit + 8 paper renderer、execute/replay 均已接线。

## 当前正式规模与全量资源门禁（EPD-026）

- 当前机器可读规模权威是 `benchmarks/paper/paper_suite_scale_profile.v1.json`（`paper_suite_scale_300_50_54.v1`）。Factorization active corpus=`100/100/100`；Exp1=300，Exp2 hard=50，Exp3/4 共享=`17/17/16`。Exp5 active selection v4=42 Factorization hard + Lean 三 topic 各 4，共 54 roots/model-repeat。
- 精确 roots/units/attempt-upper：Exp1=`435/1,970/1,970`，Exp2=`600/12,000/12,000`，Exp3=`3,726/17,148/54,372`，Exp4=`975/4,410/7,938`，Exp5=`648/4,992/4,992`；P0-core=`5,736/35,528/76,280`，P0-full=`6,384/40,520/81,272`。
- P0-full exact ceiling：tokens=`23,503,151,360`，cost=`7,346.259328`，disk forecast=`50,206,081,024` bytes，含最大 condition compaction 与安全余量 required=`63,406,407,680` bytes（59.05 GiB）。2026-07-31 E: free 快照=`471,755,141,120` bytes（439.36 GiB）；正式启动必须针对实际 output volume 重算，不能把快照当永久保证。
- 最大单 condition=`100 roots/1,000 units/1,000 attempts`。generation v3 每 root delta checkpoint/release、SQLite streaming compaction、metrics lazy bundle、JSONL/chunked report 已避免主要路径把全 suite 常驻；5/20-run 合成 metrics 峰值=`1,675,937/1,744,169` bytes 且完整 bundle live max=1。共享 reference 索引与 500-root runner 压力证据仍未闭合，所以这只是已验证的局部上界，不是正式全量 GO；仍需防范单个 provider 响应或单 bundle 过大并保留 OS 余量。
- 旧 `paper_factorization_sampling_profile.v1.json`、Exp5 v3 107-root selection 与 Exp5 smoke v3 保持不可变只供 replay/provenance。EPD-025 的 zero-retry/two-table 指标口径仍有效，EPD-026 只替换题量、selection、总量和预算 identity。

## 历史 smoke 入口与当前暂停状态

- 必须使用全新仓库外绝对 output/supervisor roots，不能复用任何历史输出。
- Exp3/4 固定入口：`local/run_exp3_exp4_v3_smoke.ps1`，身份为 11 roots / 22 units / 54 attempt upper、DeepSeek v4-pro high `600/300000`。
- Exp5 固定入口：`local/run_exp5_v3_smoke.ps1`，文件名保留 v3 cohort provenance，但 active suite/profile/selection 已是 v4；身份为 8 roots / 60 units / 60 attempts、四个 SiliconFlow cohort-v3 endpoints、global inflight 3。
- Exp5 launcher 从 gitignored `local/ai_api_smoke.local.json` 读取本地 secret，只传入子进程并对 stdout/stderr 做脱敏；成功退出必须存在 `audit/exp5_endpoint_smoke_evidence.json`。
- 正式 Exp5 preflight 必须显式传 `--exp5-smoke-evidence-bundle <新 smoke root>/audit/exp5_endpoint_smoke_evidence.json`；缺失时 exit 3、provider calls=0，并持久化 canonical 四 member 诊断。
- 未获得明确付费授权前不要再次调用真实 API；本轮只审计用户已启动的历史调用并修代码，没有新发起付费调用。
- EPD-025 不改变 Exp5 8-root smoke 的 cohort、请求身份和 capability 目的；smoke 可以继续作为门禁证据，但在正式矩阵和论文结果发布前，必须先完成新的两表 metric contract 与 renderer 迁移验证。
- EPD-027 生效后，上述入口只记录旧设施身份，不是当前可以立即执行的下一步。完整实施计划已把 bank acquisition、Exp2 六档在线并发检查、Exp3 在线恢复检查与 Exp5 smoke 排入后续 Task；相关 profile/门禁尚未实施 accepted，未获用户明确付费授权前不得调用 API。

## 不可破坏边界

- **正式规模冻结（EPD-026）**：此前 EPD-022 的 Exp3 规模未决状态已关闭；当前 Exp3 固定 3,726 roots、17,148 first-attempt units、54,372 attempt upper。不得退回旧 36,126 总量，也不得改变 fault rates、repeat 或 worker-death matrix。
- **Exp5 指标冻结（EPD-025）**：不启用 replacement/retry，不报告 recovery/retry 指标，不做 model pairwise significance；正式正文只保留首次输出质量/最终 root 结果和调用量/资源两张汇总表。图表美化属于后续论文呈现，不得改变冻结分母或新增综合评分。
- **两阶段 evidence 冻结（EPD-027）**：Exp2–4 主矩阵必须标为 `real_model_trace_protocol_run`，不能伪装 `real_transport=true`；Exp1/5 与两类在线检查才是 `online_real_provider`。source acquisition actual spend 与各条件 trace attribution 必须分账。
- 正常 paper 结果必须来自 `ProtocolRunCoordinator`/`ProtocolEngine`、真实 provider artifacts、deterministic verifier/固定 Lean checker；capturing/scripted 永远 paper-ineligible。
- provider failure 保留真实 taxonomy、固定分母和 `null/NA`；不得补 0、删失败或伪造成 success。
- replay 只读持久化 provider output，不重新调用 API；历史 evidence 不补写、不改 digest、不升级。
- 不修改正式矩阵、模型、请求参数或 tracked v1/v2 replay 配置；不扩展人为攻击/安全工程。
- 不要移动/删除仓库根 ACL 拒绝的 `pytest*` 环境遗留目录；不要 reset/clean/checkout 用户工作树。

## 当前验证与残留

- Task 5 accepted：commit=`58cda71f`；canonical combined exit `0`，`17 passed / 0 failed / 0 skipped in 0.81s`；final review PASS，Critical/Important/Minor=`0/0/0`；provider/network calls=`0`。
- Task 5 post-task test minimization `<5min`=`NO_CHANGE`（17 distinct cases）；3 个 production 文件 `py_compile`、diff-check exit `0`。本轮未运行 Fast/Full/LeanAudit。
- Task 3 post-task 测试压缩 `66643084`：test defs / 估算 cases=`77/153 → 66/118`；canonical scoped exit `0`，`118 passed in 5.55s`；独立 reviewer PASS，Critical/Important/Minor=`0/0/0`，provider calls=`0`。本次未重复 Fast、Full 或 LeanAudit。
- 最新 Fast：`.\init.ps1` exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 通过，provider calls=`0`。Full intentionally not run per user instruction；LeanAudit/API 未运行。
- Task 3 证据：`860b7c48` focused 40 + review PASS；`4ea293b1` 69+286 + review PASS；`f9773944` focused 101 + review PASS；provider calls 均为 0。
- EPD-025 文档与状态同步后的最新 Fast：`457 passed, 1 skipped in 19.99s`；未运行 Full、LeanAudit 或真实 API。
- 本轮统一核心：356 passed；launcher：9 passed；最终 Fast：457 passed/1 skipped；结果完整性独立复审 Ready。
- 既有根集成：442 passed；此前 Fast：456 passed、1 skipped。
- 当前 scale/Exp3/4 reviewer：50 项通过；Exp3 26、Exp4 39；Exp5 model 90；Exp5 smoke/evidence/supervision 48；authoritative Exp1–5 exact plan-only 1 项通过，provider calls=0。
- 当前工作树暂停收尾 Fast：`458 passed, 1 skipped in 21.06s`；JSON/SQLite、harness、compileall 通过。
- EPD-027 文档/harness 同步后 Fast：`458 passed, 1 skipped in 17.96s`；未联网、未调用真实 API、未运行 Full。
- Exp5 identity-only v4：8/60/60，suite=`paper_smoke_exp5_v4`；launcher fail-closed profile/selection identity 已同步 v4。
- 本次 state/docs 同步未运行 pytest/Fast/Full/LeanAudit/force-all/真实 API/攻击测试；只持久化 Task 5 accepted、Task 6 queued 与已有验证事实，不 push、不 merge、不创建 PR。
- TTFT 无持久化来源，保持缺失；bundle source path 参与 digest 是本机可移植性 P2。
- Exp5 identity fail-stop 的 raw checkpoint placeholder 使用 `attempt_status=not_started`；当前 metrics/replay 直接消费并有回归覆盖，未来若引入严格 `PaperAttemptStatus` 反序列化需先版本化该状态。
- PowerShell helper 在 runner 已退出但后代长期持有继承管道写端时仍可能等待 EOF；正常实时日志脱敏、受控退出和精确退出码路径已通过。该项为 P2，不得写成已修复。
- 审计输出保存在仓库外 `E:\TokenEcnomic\TokenShareData` 的 `_identity_probe_*`、`_exp5_formal_preflight_no_bundle_20260731*` 与 `_audit_exp34_p0core_plan_20260731_a`；均不是论文 evidence。
