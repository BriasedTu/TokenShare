# Session Handoff

更新时间：2026-08-02

## 开工顺序

1. 先完整阅读 `AGENTS.md` 与 `Doc/archive/design-history/2026-08-01-epd027-minimal-handoff.md`。
2. 执行 `git status --short --branch` 与 `git log -3 --oneline`，确认 Task 18 代码提交和状态提交。
3. Task 0–18 已 accepted；Task 19 的四个计划外 production 接线已获用户明确授权，按 8 文件 allowlist 实施，不得进入 Task 20。

## 2026-08-02 EPD-027 当前实施交接（Task 18 accepted）

- Active feature=`feat-011`，仍为 `in-progress`；active focus id 保持 `paper-metric-contract-trace-bank-and-traceability-plan`，status=`in_progress`。Task 0–18 已 accepted（`19/35`）；Task 19 为 `in_progress`；正式矩阵仍为 **NO-GO**。
- 当前分支=`codex/feat-011-epd027-pipeline`；Task 18 evidence eligibility commit=`18d2c12a`，状态提交在其后。
- Task 0–2 accepted 原提交不变：Task 0=`ed0b056a`，Task 1=`78fbddaa`，Task 2=`d2b16f89`。
- Task 3–9 已 accepted；提交依次包括 `f9773944`/`66643084`、`0cbda1df`、`58cda71f`、`5f97034c`、`aadbd483`、`6802918d`、`1109e852`，均有 scoped exit `0` 与最终 review PASS，provider/network calls=`0`。完整文件、测试和复审证据见 minimal handoff/progress。
- Task 3 direct projector 不复制协议状态机；Task 4 用户授权的 `paper_formal_evidence.py` mapping conflict fail closed；Task 5–8 已提供 immutable bank、semantic inventory/preflight、SQLite budget authority 与 durable acquisition。当前没有 paid receipt/付费授权，不得调用 provider。
- Task 10 attempt ordinal / parent-owned trace delivery commit ABI 已 accepted，代码 commit=`933ca71bd3aca9b62ad4f4b399f43a8f5d8092e1`，严格 16 文件（11 production + 5 tests）。canonical exit `0`，`106 passed in 48.71s`；11 production `py_compile` exit `0`；final reviewer=`PASS`。
- Task 10 验收后测试最小化仅改 2 个测试文件，删除 37 行 / 3 cases；最小验证 exit `0`，`35 passed in 10.61s`。provider_calls=`0`，无 paid receipt；该时点状态持久化未运行测试、Fast、Full、LeanAudit、network/provider，Full intentionally not run per user。
- Task 11 trace-backed response-bank executor / dual provenance 已 accepted，代码 commit=`1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`，严格 10 文件（6 production + 4 tests）。post-min trace final exit `0`，`10 passed in 3.58s`；parent commit 32、logical scheduler 5、Factorization + Lean targeted 2、recovery 39、descriptor 3 通过；6 production compile/diff-check/allowlist/secret-output 通过；final reviewer=`PASS`。
- Task 11 canonical 首次 tool timeout，cache 暴露 3 个 v2 descriptor 失败，修复后对应 3 nodeids pass；原 canonical 未完整成功，禁止记录 canonical PASS。测试最小化删除 7 行 / 1 test；provider_calls=`0`，无 paid receipt。四个用户批准的最小 plan-out 接点为 `ai_api.py`、`local_runtime/contracts.py`、`local_runtime/coordinator.py` 与 parent commit test。
- Task 12 standalone Experiment 1 metric observations 已 accepted，代码 commit=`a1183c6645d067409b2cfbce54c909aaf4b569c1`，严格 5 文件。真实 loader RED=`1 failed`；Task 2 contract suite=`196 passed`；Task 12 suite=`5 passed`；最小化后 combined=`201 passed in 18.17s`；final reviewer=`PASS`。
- Task 6 profile digest 更新导致 Task 2 tracked contract 正式 loader drift；用户批准的最小 plan-out 只同步 profile binding=`sha256:e8e8c1f10b638607054c094f6fee409c62aade27ed01ca8645e9afdb79c14fb7` 与派生 contract digest=`sha256:48882b8f97c54237654b6ebba9d22c290fa92b2b98b105ef567c57c674352929`，无指标语义变化。Task 12 accepted 时 provider/network calls=`0`，无 paid receipt，且未运行测试、Fast、Full、LeanAudit、network/provider，Full intentionally not run。
- Task 13 standalone Experiment 2 trace/online metric observations 已 accepted，代码 commit=`3f8d4ec2284d472896b706a3d94112f81be7e7c2`，严格 2 文件。review 补充 RED=`3 failed`，targeted 修复后 `3 passed`；canonical=`16 passed in 13.85s`；测试最小化后仍为 `16 passed in 12.30s`；production compile、diff-check、forbidden import/alias scan 与 allowlist 均通过；final reviewer=`PASS`。provider/network calls=`0`，无 paid receipt。
- Task 14 目标是纯 Exp3 trace 24 / online recovery 13 observations，保持 fixed denominator、有序 recovery、worker death、五类 rate-fault 与 no-shadow 边界。metrics 只能投影 persisted event/artifact/attempt/provider facts，不得按 condition/fault 名称模拟 state/retry/death/terminal。
- Task 14 contract 使用完整 backlink identity；started/reassignment 只要求实际启动链，successful 才要求 qualified/completed，retry-exhausted 为 started=`1`/success=`0`/reassignment=`1`。digest=`sha256:b72307e727e28f5233de934df28e1701cf46ffb711b99aa845ea6f8580694b0e`。
- Task 14 review-fix targeted RED=`6 failed in 2.52s`，contract nodeid RED=`1 failed in 0.39s`；contract full=`197 passed in 15.74s`。第二轮 online missingness targeted RED=`2 failed in 0.70s`；最终 canonical=`12 passed in 1.79s`，py_compile/diff-check 通过。
- Task 14 final reviewer=`PASS`，Critical/Important=`0/0`；post-accept test minimization=`NO_CHANGE`。接管 Fast 仅运行一次：`497 passed, 1 skipped in 32.15s`；Full/LeanAudit 未运行，provider/network calls=`0`，无 paid receipt。
- Task 18 初始 RED=`183 passed/9 failed in 27.64s`、GREEN=`193 passed in 26.28s`；两轮 reviewer BLOCK=`3C/1I` 与 `2C/1I`，逐 unit/attempt、全部 replacement/entry/manifest/receipt creator 与 terminal-aware lifecycle 修复后最终 canonical=`193 passed in 24.78s`。final reviewer PASS=`0/0/0`；子智能体 test minimization=`CHANGED` 后 canonical=`193 passed in 26.83s`；provider/network=`0/0`。
- 用户已授权后续每个 Task 持续实施/review/fix/follow-up 直至 PASS 后直接推进，且 post-accept 测试最小化必须由子智能体执行。下一动作是实施 Task 19；Task 20 仍保持 queued。
- 用户已明确批准 Task 19 计划外 `paper_response_bank.py`（纯 completeness seam）、`paper_dispatcher.py`（typed trace context）、`factorization_paper_adapter.py` 与 `lean_paper_adapter.py`（正式 trace runtime/bridge/stager/scheduler 接线）。加原计划 runner/callbacks 与两个 tests，共 8 文件；禁止其他计划外 production、runner 旁路与 coordinator 私有 ABI。

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
- 在该历史节点只完成 `design_synced`，尚未实现或调用真实 API；此后完整实施计划已形成，当前 Task 0–18 已 accepted。不要再把本条历史快照当作当前状态。
- 旧 P0-core/P0-full 和“下一次 smoke”顺序暂停；EPD-027 设施与 profile 未实现前不得启动。EPD-026 的题量/fault/mode/repeat 不变，`76,280` 仅保留为旧全在线上界/trace-slot capacity；约 `6,904 + canary` 只是待 bank inventory 验证的 DeepSeek 量级。
- Experiment 2–4 主矩阵资源指标改为 trace-replay/trace-attributed；Experiment 3 主矩阵使用 `discarded_trace_tokens`。`wasted_actual_tokens` 只用于 Exp3 在线恢复检查，不能把预取回答重复计成 actual spend。
- 人民币 `1,000` 是 DeepSeek acquisition/在线检查目标硬停止线；当前每 attempt `0.05` 预留不可靠，后续必须同时约束 calls/tokens/CNY 和 in-flight 悲观预留。Experiment 5 SiliconFlow 预算单列。

## 当前业务状态

- Active feature：`feat-011`。EPD-027 accepted 进度为 `19/35`：Task 0–18 已 accepted；Task 19 为 in_progress，Task 20 及以后保持 queued。immutable bank、inventory/preflight、budget、acquisition、logical scheduler、parent-owned commit ABI、trace-backed executor / dual provenance、standalone Exp1–5 projectors、registry/formal metrics 与 evidence eligibility 已 accepted；formal runner/renderer/replay 接线仍未闭合。
- Active focus 已从“只写计划”进入严格串行实施。EPD-025 的 Experiment 5 `max_retries=0`、两张 model 汇总表、无六组 pairwise 继续冻结；Task 2–16 已提供 contract/direct facts/stable request identity/immutable bank/inventory/budget/acquisition/scheduler/parent commit ABI/trace-backed executor/Exp1–5 projectors，正式 consumer/renderer/replay 接线仍待后续 Task。
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

- Task 16 accepted：commit=`440c1ef9`；严格 2 文件；reviewer BLOCK=`1 Critical/2 Important`；targeted RED=`5 failed in 0.36s` + identity-shadow RED=`1 failed in 0.48s`，最终 review canonical=`9 passed in 1.20s`；py_compile/diff/scan通过；final reviewer PASS=`0/0/0`；子智能体 test minimization=`CHANGED` 后 canonical=`9 passed in 1.17s`；provider/network=`0/0`，未运行 Fast/Full/LeanAudit。
- Task 13 accepted：commit=`3f8d4ec2284d472896b706a3d94112f81be7e7c2`；严格 2 文件；review RED=`3 failed`→targeted=`3 passed`；canonical=`16 passed in 13.85s`；最小化后=`16 passed in 12.30s`；production compile、diff-check、forbidden import/alias scan、allowlist 均通过；final reviewer PASS。provider/network calls=`0`，无 paid receipt；未运行 Fast/Full/LeanAudit，Full intentionally not run。
- Task 12 accepted：commit=`a1183c6645d067409b2cfbce54c909aaf4b569c1`；真实 loader RED=`1 failed`；Task2 suite=`196 passed`；Task12 suite=`5 passed`；post-min combined=`201 passed in 18.17s`；final reviewer PASS；严格5文件，Task2 contract最小plan-out只同步Task6后的profile binding与派生digest，无指标语义变化。provider/network calls=`0`，无paid receipt；未运行Fast/Full/LeanAudit，Full intentionally not run。
- Task 11 accepted：commit=`1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`；final reviewer PASS；post-min trace final `10 passed in 3.58s`；parent32/logical5/Factor+Lean2/recovery39/descriptor3通过；6 production compile/diff/allowlist/secret-output通过；测试最小化删7行/1 test。canonical 首次 tool timeout，cache 暴露3个v2失败，修后3 nodeids pass，不能记录canonical PASS。provider/network calls=`0`，无paid receipt；未运行Fast/Full/LeanAudit，Full intentionally not run。
- Task 9 accepted：commit=`1109e852`；final canonical exit `0`，`42 passed in 36.58s`；final review PASS，Critical/Important/Minor=`0/0/0`；C1 typed in-process checkpoint/resume、C2 六类 queue 闭合；test minimization=`NO_CHANGE`；7 个 production 文件 `py_compile` 与 diff-check exit `0`；严格 12 文件包含用户授权的 `src/tokenshare/storage/artifacts.py` Windows normal-path 映射且 logical identity 不变；provider/network calls=`0`；未运行 Fast/Full/LeanAudit。
- Task 8 accepted：commit=`6802918d`；最终 exact 5-file command exit `0`，`77 passed in 15.20s`；manifest 专项 `3 passed`、cross-check `4 passed`；fake-only，真实 provider/network/secret=`0`；4 个 production 文件 `py_compile`、diff-check、allowlist、secret scan exit `0`；final review PASS，Critical/Important/Minor=`0/0/0`；test minimization 3 分钟=`NO_CHANGE`，collect-only=`19`（行为 `18`）。没有 paid receipt；未运行 Fast/Full/LeanAudit。
- Task 7 accepted：commit=`aadbd483`；post-min final test exit `0`，`12 passed in 2.18s`；真实本地 two-process race；provider/network/secret=`0`；3 个 production 文件 `py_compile` exit `0`；comprehensive review PASS，Critical/Important/Minor=`0/0/0`。
- Task 7 test compaction 仅删除 1 个冗余 assert。本次状态提交未运行任何测试、Fast、Full、LeanAudit 或真实 API。
- Task 6 accepted：commit=`5f97034c`；canonical combined exit `0`，`44 passed in 25.14s`；network tripwire/provider calls=`0`；2 个 production 文件 `py_compile` 与 diff-check exit `0`；comprehensive final review PASS，C1 terminal conflict/C2 repeat-sample 跨 replacement closure 已闭合，Critical/Important=`0/0`。
- Task 6 post-task test minimization `<5min`=`NO_CHANGE`；用户授权的 profile 仅同步 plan authority/profile 派生 digest，budget digest 不变且无模型、题量、repeat、fault、worker、evidence 参数漂移。本轮未运行 Fast/Full/LeanAudit。
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
- 本次暂停交接只更新 minimal handoff、`feature_list.json`、`progress.md` 与本文件；不改 code map/Task 14 代码，不运行测试/Fast/Full/LeanAudit/force-all/provider/network，不 stage/commit。provider/network calls=`0`，无 paid receipt；正式矩阵 **NO-GO**；不 push、不 merge、不创建 PR。
- TTFT 无持久化来源，保持缺失；bundle source path 参与 digest 是本机可移植性 P2。
- Exp5 identity fail-stop 的 raw checkpoint placeholder 使用 `attempt_status=not_started`；当前 metrics/replay 直接消费并有回归覆盖，未来若引入严格 `PaperAttemptStatus` 反序列化需先版本化该状态。
- PowerShell helper 在 runner 已退出但后代长期持有继承管道写端时仍可能等待 EOF；正常实时日志脱敏、受控退出和精确退出码路径已通过。该项为 P2，不得写成已修复。
- 审计输出保存在仓库外 `E:\TokenEcnomic\TokenShareData` 的 `_identity_probe_*`、`_exp5_formal_preflight_no_bundle_20260731*` 与 `_audit_exp34_p0core_plan_20260731_a`；均不是论文 evidence。
