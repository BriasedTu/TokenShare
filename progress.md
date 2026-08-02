# TokenShare 当前进度

更新时间：2026-08-02

本文件只保存当前事实与最近验证。历史过程由 git history、`Doc/archive/` 和仓库外 `TokenShareData` 保留。

## 当前状态

- Active feature：`feat-011`（Paper Real AI Experiments），状态仍为 `in-progress`。
- EPD-027 实施计划共 35 个 Task，Task 0–14 已 accepted（`15/35`）。Task 14 contract commit=`3dc92de5`、projector commit=`77d79a65`；Task 15 standalone Exp4 projector 为 `queued/next`。
- Task 14 contract 使用完整 backlink identity：started/reassignment 只要求实际启动链，successful 才要求 qualified/completed；retry-exhausted 为 started=`1`/success=`0`/reassignment=`1`。digest=`sha256:b72307e727e28f5233de934df28e1701cf46ffb711b99aa845ea6f8580694b0e`。
- 接管 Fast 仅运行一次：exit `0`，`497 passed, 1 skipped in 32.15s`。Task 14 review-fix targeted RED=`6 failed in 2.52s`，contract nodeid RED=`1 failed in 0.39s`；contract full=`197 passed in 15.74s`；第二轮 online missingness RED=`2 failed in 0.70s`，最终 canonical=`12 passed in 1.79s`；py_compile/diff-check 通过。
- Task 14 final reviewer=`PASS`，Critical/Important=`0/0`；无影子 TokenShare、无 fault-name 推断。唯一 audit-only ratio coercion Minor 不阻塞；post-accept test minimization=`NO_CHANGE`。
- 用户已授权每个后续 Task 持续执行实现/review/fix/follow-up 至 PASS 后直接推进。下一步只实施 Task 15 两个计划文件；不得进入 Task 16、formal runner/renderer 或真实 API。
- 本轮 provider/network calls=`0`，无 paid receipt；Fast 未重复，Full/LeanAudit 未运行，正式矩阵继续 **NO-GO**。
- Task 3 的两项通用前置均已 accepted：ledger-binding `860b7c48` 最终 focused `40 passed`、review PASS；typed-hook `4ea293b1` 最终 `69 + 286 passed`、review PASS。Task 3 四文件提交=`f9773944`，最终 focused `101 passed`、独立 review PASS，provider calls=`0`。
- Task 3 post-task 测试压缩已完成并提交为 `66643084`：仅修改 5 个测试文件，production 零修改；test defs / 估算 cases 从 `77/153` 降至 `66/118`。canonical scoped 结果 exit `0`，`118 passed in 5.55s`，provider calls=`0`；独立 review PASS，Critical/Important/Minor=`0/0/0`。本次提交与状态同步未重复运行 Fast、Full 或 LeanAudit。
- Task 3 反影子结论：正式 direct-results 路径消费 canonical `ProtocolRunResult`、verified ledger binding 与 official typed-hook parser；没有复制 `ProtocolEngine`/状态机，禁用 `ProtocolEngine` 不能铸造 paper-eligible success。手工 typed fixture 仅用于组件测试。
- 最新 Fast 证据（2026-08-01）：`.\init.ps1` exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 均通过，provider calls=`0`。本次按用户指令 intentionally not run Full；LeanAudit 与真实 API 也未运行。
- Task 4 exact outbound bytes/request identity/admission/Lean prompt v2 已提交为 `0cbda1df`。focused=`125 passed`；canonical 初次为 `323 passed / 39 failed`，随后 fixture 逐步收敛；最终正式 E2E nodeid exit `0`，`1 passed in 6.68s`（total `7.635s`），日志=`%TEMP%\tokenshare_task4_canonical_single_selection_nodeid.log`。全程 network tripwire/provider calls=`0`。
- Task 4 综合 review 最终 `PASS`，Critical/Important=`0/0`；post-accept test minimization 运行 3 分钟，结论 `NO_CHANGE`，保留 5 个 distinct tests。`py_compile`、`git diff --check` 与 staged name-only 边界检查均 exit `0`；Fast、Full、LeanAudit 未运行。
- 用户明确批准唯一计划外 production 文件 `src/tokenshare/experiments/paper_formal_evidence.py`：把 immutable `protocol_task_id` 修正为 case task-id closure mapping，并对映射冲突 fail closed；review 确认没有 shadow protocol path。
- Task 5 immutable response-bank objects/index/opaque locator 已提交为 `58cda71f`，严格五文件。canonical combined exit `0`，`17 passed / 0 failed / 0 skipped in 0.81s`；provider/network calls=`0`；3 个 production 文件 `py_compile` 与 diff-check 均 exit `0`。综合 review=`PASS`，Critical/Important/Minor=`0/0/0`；post-task test minimization `<5min`=`NO_CHANGE`，保留 17 个 distinct cases。本轮 Fast、Full、LeanAudit 未运行。
- Task 6 semantic slot inventory/zero-engine preflight 已提交为 `5f97034c`，严格五文件（计划四文件 + 用户授权 `benchmarks/paper/epd027_pipeline_profile.v1.json`）。canonical combined exit `0`，`44 passed in 25.14s`；network tripwire/provider calls=`0`；2 个 production 文件 `py_compile` 与 diff-check 均 exit `0`。comprehensive final review=`PASS`，C1 terminal conflict 与 C2 repeat-sample 跨 replacement closure 已闭合，Critical/Important=`0/0`；post-task test minimization `<5min`=`NO_CHANGE`。profile 仅同步 plan authority/profile 派生 digest，budget digest 保持不变且无模型、题量、repeat、fault、worker、evidence 参数漂移。本轮 Fast、Full、LeanAudit 未运行。
- Task 7 SQLite WAL atomic budget authority 已提交为 `aadbd483`，严格四文件。post-min final test exit `0`，`12 passed in 2.18s`，覆盖真实本地 two-process race；comprehensive review=`PASS`，Critical/Important/Minor=`0/0/0`。test compaction 仅删除 1 个冗余 assert；provider/network/secret=`0`；3 个 production 文件 `py_compile` exit `0`。本次接受状态持久化未运行任何测试、Fast、Full 或 LeanAudit，也未联网或调用 provider。
- Task 8 durable response-bank acquisition/publish/resume/reconcile 已提交为 `6802918d`，严格六个实际变更文件；计划中的三个 executor 测试文件无变更且未 stage。最终 exact 5-file command exit `0`，`77 passed in 15.20s`；manifest 专项 `3 passed`、cross-check `4 passed`；fake-only，真实 provider/network/secret=`0`。4 个 production 文件 `py_compile`、diff-check、allowlist、secret scan 均 exit `0`；final review=`PASS`，Critical/Important/Minor=`0/0/0`，manifest 修复已闭合；post-task test minimization 3 分钟=`NO_CHANGE`，collect-only=`19`（行为用例 `18`）。没有 paid receipt；本次接受状态持久化未运行测试、Fast、Full 或 LeanAudit，也未联网或调用 provider。
- Task 9 deterministic logical source-latency scheduler 已提交为 `1109e852`（`feat(runtime): add deterministic trace event scheduler`），严格 12 文件：计划 11 文件加用户授权的 `src/tokenshare/storage/artifacts.py`。final canonical exit `0`，`42 passed in 36.58s`；final review=`PASS`，Critical/Important/Minor=`0/0/0`；C1 typed in-process checkpoint/resume 与 C2 六类 queue 闭合均已确认；post-task test minimization=`NO_CHANGE`。7 个 production 文件 `py_compile` 与 diff-check exit `0`；provider/network calls=`0`。`artifacts.py` 仅把含冒号 logical artifact id 映射为 Windows 普通文件名，logical identity 不变且未扩展 security scope。本次接受状态持久化未运行测试、Fast、Full 或 LeanAudit，也未联网或调用 provider。
- Task 10 attempt ordinal / parent-owned trace delivery commit ABI 已提交为 `933ca71bd3aca9b62ad4f4b399f43a8f5d8092e1`（`feat(runtime): add parent-owned trace delivery commits`），严格 16 文件（11 production + 5 tests）。canonical exit `0`，`106 passed in 48.71s`；11 个 production 文件 `py_compile` exit `0`；final reviewer=`PASS`。验收后仅修改 2 个测试文件并删除 37 行 / 3 cases，最小验证 exit `0`，`35 passed in 10.61s`。provider_calls=`0`，无 paid receipt；本次状态持久化未运行测试、Fast、Full、LeanAudit、network/provider，Full 按用户指令 intentionally not run。
- Task 11 trace-backed response-bank executor / dual provenance 已提交为 `1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`（`feat(executors): add trace-backed response-bank execution`），严格 10 文件（6 production + 4 tests），其中 `ai_api.py`、`local_runtime/contracts.py`、`local_runtime/coordinator.py` 与 parent commit test 是用户批准的最小 plan-out 接点。post-min trace final exit `0`，`10 passed in 3.58s`；parent commit 32、logical scheduler 5、Factorization + Lean targeted 2、recovery 39、descriptor 3 均通过；6 production compile、diff-check、allowlist 与 secret/output 检查通过；final reviewer=`PASS`。canonical 首次 tool timeout，cache 随后暴露 3 个 v2 descriptor 失败，修复后对应 3 nodeids 通过；原 canonical 未取得完整成功退出，因此不得记录 canonical PASS。测试最小化删除 7 行 / 1 test；provider_calls=`0`，无 paid receipt。本次状态持久化未运行测试、Fast、Full、LeanAudit、network/provider，Full intentionally not run。
- Task 12 standalone Experiment 1 metric observations 已提交为 `a1183c6645d067409b2cfbce54c909aaf4b569c1`（`feat(experiments): add Experiment 1 metric observations`），严格 5 文件。Task 6 profile digest 更新导致 Task 2 tracked contract 的正式 loader binding 漂移，真实 loader RED=`1 failed`；用户批准的最小 plan-out 只同步 profile binding `sha256:e8e8c1f10b638607054c094f6fee409c62aade27ed01ca8645e9afdb79c14fb7` 与派生 contract digest `sha256:48882b8f97c54237654b6ebba9d22c290fa92b2b98b105ef567c57c674352929`，无指标语义变化。Task 2 suite=`196 passed`，Task 12 suite=`5 passed`，最小化后 combined=`201 passed in 18.17s`；final reviewer=`PASS`；provider/network calls=`0`，无 paid receipt。本次状态持久化未运行测试、Fast、Full、LeanAudit、network/provider，Full intentionally not run。
- Task 13 standalone Experiment 2 trace/online metric observations 已提交为 `3f8d4ec2284d472896b706a3d94112f81be7e7c2`（`feat(experiments): add Experiment 2 metric observations`），严格 2 文件。review 补充 RED=`3 failed`，targeted 修复后 `3 passed`；canonical=`16 passed in 13.85s`；测试最小化后仍为 `16 passed in 12.30s`；production compile、diff-check、forbidden import/alias scan 与 allowlist 均通过；final reviewer=`PASS`。provider/network calls=`0`，无 paid receipt；本次状态持久化未运行测试、Fast、Full、LeanAudit、network/provider，Full intentionally not run。
- EPD-027 已按用户确认写入权威设计：Experiment 1/5 继续真实在线；Experiment 2–4 改为不可变真实回答库驱动的完整协议运行。同一 repeat 的配对条件共享回答、不同 repeat 使用独立 sample slot，replacement 按冻结最大恢复深度准备。
- Experiment 2 另在缩小且预注册的题集上，用真实 API 覆盖 `1,3,7,10,30,50` 全六个 worker 档位；Experiment 3 另保留 verifier/checker 拒绝后 replacement 与 worker death 后重新分派的小型真实在线恢复检查。完整实施计划已形成，相关题集、重复、阈值、预算与 profile 仍须由后续 Task 实现、复审并在付费门禁前验证。
- EPD-027 是实验设施全面改造，涉及稳定 provider-body digest、response-bank schema/inventory、trace-backed executor、source/consumer 双 provenance、双 paper evidence class、预算分账与硬门、runner/metrics/report/renderer/replay/audit；不是现有 replay 的局部修补。Task 0–14 已 accepted，但其余 `20/35` 仍未实施，正式全量继续 **NO-GO**。
- Experiment 5 指标继续遵守 EPD-025：维持零 retry，正文使用“首次输出质量/最终结果”和“调用量/资源”两张 model 汇总表，删除六组 pairwise、recovery 与重复旧正确率名。Task 2 metric contract、Task 3 direct projector、Task 4 stable request identity、Task 5 immutable bank primitives、Task 6 semantic inventory/preflight、Task 7 atomic budget authority、Task 8 durable acquisition、Task 9 logical scheduler、Task 10 parent-owned commit ABI、Task 11 trace-backed executor / dual provenance、Task 12 Exp1、Task 13 Exp2 与 Task 14 Exp3 projector 已 accepted；Exp4–5 projector、formal runner/metrics/renderer/replay 正式接线属于后续 Task，不能把 `15/35` 写成整条 pipeline implemented。
- Experiment 3/4/5 的代码与离线 smoke/paper evidence 门禁已闭合；2026-07-31 用户启动的真实 Exp3/4 smoke 已完成运行，但其历史 canonical 论文证据审计不通过，修复后仍须使用全新 identity 重跑。正式 P0-full 仍为 **NO-GO**，还缺新的 Exp3/4 11-root 证据与 Exp5 8-root artifact-backed endpoint smoke bundle。
- Experiment 1–4 继续固定官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，thinking enabled、high、`timeout_seconds=600`、`max_tokens=300000`。
- Experiment 5 继续固定 SiliconFlow cohort v3 四模型与 `600/32768`；GLM/Qwen/MiniMax thinking budget 32768，DeepSeek-V3 nonthinking。
- 历史 smoke/diagnostic 全部只读，不 resume、不补写、不升级为论文证据。
- 2026-07-31 用户要求暂停当前工作；所有协作 Agent 已停止，工作树原样保留且未 stage/commit。正式全量仍为 **NO-GO**：root-delta/terminal snapshot 主路径已经落地，但共享 Exp1 root 仍缺 terminal-snapshot-only 的一次性校验索引，当前 resolver 会逐次扫描 source run；500-root 常驻对象压力测试和最新 runner 整体回归也尚未完成。

## 2026-07-31 当前正式规模与资源门禁（EPD-026）

- active scale profile=`paper_suite_scale_300_50_54.v1`，Factorization 稳定 hash corpus=`100/100/100`；Exp1=300，Exp2 hard=50，Exp3/4 共享=`17/17/16`。旧 v1 Factorization profile 仅供历史 replay。
- Exp5 active selection=`exp5_parent_quarter_selection.v4.json`，42 Factorization hard + Lean 三 topic 各 4，共 54 roots/model-repeat；v3 107-root selection 保持原字节作为 parent/provenance。active 8-root smoke suite=`paper_smoke_exp5_v4`，launcher 仍名为 `local/run_exp5_v3_smoke.ps1`，但其 profile/suite/selection fail-closed 常量均已迁移到 v4。
- 精确正式计划：Exp1=`435/1,970/1,970`，Exp2=`600/12,000/12,000`，Exp3=`3,726/17,148/54,372`，Exp4=`975/4,410/7,938`，Exp5=`648/4,992/4,992`；顺序为 roots/first-attempt units/provider-attempt upper。Exp1–4 总计=`5,736/35,528/76,280`，Exp1–5 总计=`6,384/40,520/81,272`。
- 全量 exact ceiling：tokens=`23,503,151,360`，冻结价格 cost=`7,346.259328`，disk forecast=`50,206,081,024` bytes，含 compaction 与安全余量 required=`63,406,407,680` bytes（59.05 GiB）。2026-07-31 E: free 只读快照=`471,755,141,120` bytes（439.36 GiB），当前足够；正式启动仍须按实际 output volume 重算。
- 最大单 condition=`100 roots/1,000 units/1,000 attempts`。generation v3 root-delta checkpoint/release、SQLite streaming compaction、metrics lazy bundle 与 JSONL/chunked report 已覆盖主要持久化/汇总路径，不会设计成把 81,272 attempts 全部常驻；5/20-run 合成 metrics 探针峰值=`1,675,937/1,744,169` bytes、完整 bundle live max=1。但共享 reference 一次性索引与 500-root runner 压力验证仍未闭合，因此当前不能把“正式全量内存风险已解决”作为 GO 结论；此外仍需为异常大的单次 provider response 保留 OS 余量。
- 当前规模/Exp3/4 reviewer 已确认 50 项通过；Exp3 26、Exp4 39、Exp5 model 90、Exp5 smoke/evidence/supervision 48、authoritative CLI exact plan-only 1 项通过。以上均离线，provider calls=0；没有运行 Full、LeanAudit 或正式全量。

## 2026-08-01 两阶段真实回答库决定（EPD-027）

- EPD-026 的 `76,280` 继续保留为 Exp1–4 旧全在线 provider-attempt 上界和最大 trace-slot capacity，不再代表新方案预计在线调用量。静态审计量级约为 `6,904` 次 DeepSeek acquisition 加在线检查；必须以稳定 outbound-body digest 枚举完整 bank inventory 后才能冻结，Experiment 5 SiliconFlow 预算单列。
- Experiment 2–4 主矩阵的正确率、完成率、fault/recovery/ablation 机制指标继续由完整 TokenShare 状态机产生；主矩阵 wall-clock/token/cost/call 统一改为 trace-replay/trace-attributed/bank-slot 口径，不能写成每个条件当次在线支出。
- Experiment 3 主矩阵把 `wasted_actual_tokens` 改为 `discarded_trace_tokens`；`wasted_actual_tokens` 只在小型在线恢复检查中使用，并要求 fault/death 之后的新 attempt、新 provider response/provenance 与独立 actual usage。
- Exp2 六档在线检查用于观察真实 API 排队、限流、超时和扩展趋势是否与 trace 主矩阵严重背离；Exp3 在线恢复检查用于证明故障以后系统确实会重新调用 AI。两者都是单独 evidence，不拼入回答库主矩阵冒充全量在线结果。
- 人民币 `1,000` 作为 DeepSeek bank acquisition 与在线检查的目标硬停止线。Task 7 已以 SQLite WAL authority 同时限制 calls/tokens/CNY，并把未 settled reservation upper 计入原子门禁；Task 8 已把 acquire/publish/resume/reconcile 接入该权威，但本次没有 paid receipt，未真实 dispatch。
- 历史冻结节点：EPD-027 决策落库当时只更新设计与 harness、没有实现或真实 API；该表述仅描述实施计划形成前的历史状态。当前已完成 Task 0–9，仍未运行真实 API。

## 2026-07-31 Exp3/4/5 修复

- 用户启动的 `exp34-smoke-20260731-041442` 真实运行已结束：11 conditions/runs/roots、36 个 DeepSeek HTTP 200 provider attempts、839,054 tokens；8 roots completed、3 roots 为 evidence-complete experimental failures、0 infrastructure blocked/not-started。配置口径 cost estimate 为 CNY 4.6147626，provider actual billing 不可用，不能把估算写成实付。
- 三个负向结果分别是 Exp3 `false_positive=100%` replacement 验证失败、Exp3 `worker_death` 真实死亡后 replacement 验证失败、Exp4 `NO_PARSER_POLICY` 全部 verification rejected；均保留真实失败，不改写成成功恢复或通过。
- 独立真实性审计确认 36 个唯一 `PaperModelExecutionRecord.v2`、36 个唯一 DeepSeek response ID、模型/请求控制与 v3 配置一致，artifact/raw/usage 可回溯且 secret scan 零命中；执行路径进入 `ProtocolRunCoordinator`/`ProtocolEngine`，没有绕过 TokenShare 伪造 provider 结果。
- 同一历史输出的 canonical 论文证据审计 **FAIL**：两个 `no_return` provenance 只在 compatibility runtime tree；906/906 canonical `LedgerEvent.v2` body 被投影层改写但保留旧 hash；11 条 timing/concurrency 被补成 `null/0`；部分 `fault_refs` 为字符串 `"None"`；root model inventory 为空且 replay 只加载旧 runner result。历史输出保持只读，不能升级为论文证据。
- 修复后 canonical materialization 会校验并复制 no-return raw/provenance/usage/model/fault 链；LedgerEvent 原文/hash/task identity 保真并以 sidecar 映射 case；timing、critical path、provider latency、Exp3 wasted/reassignment 和非法负测量使用 complete-or-null/fail-closed；正式 model inventory 强制 canonical v2 identity；replay 从 frozen dispatch 与 CURRENT generation 独立重算；smoke usage/validity/timing/fault refs 与基础设施退出码均 fail closed。
- Exp3/4：独立确认 Windows process worker-death 不再受 `mappingproxy` pickle 或 `DuplicateHandle` 阻断；Factorization/Lean 均经过 `ProtocolRunCoordinator`、`ProtocolEngine`、event ledger、artifact、projection 和 checkpoint/replay。
- Exp3 五类 rate-fault 与单独 worker-death、Exp4 五种正式 ablation 均从 runtime evidence 计算，不按 fault/mode 名称造指标。
- Exp5：新增 `paper_exp5_smoke_evidence.py`，8-root bundle 必须绑定 canonical v3 profile、四模型 × 两领域、完整 TokenShare lifecycle、event hash-chain、run/generation manifest、artifact index/bytes/hash、request controls、usage/thinking 和 configured/requested/resolved model identity。
- v3 formal preflight 不再接受 inline `sha256:` 形状引用；必须显式传入 `--exp5-smoke-evidence-bundle`。capturing/scripted、任意 completed event 或缺失 artifact bytes 均不能放行。
- Exp5 identity mismatch 改为 condition-local fail-stop；未执行 roots 物化为 `not_started`，后续 condition 继续，固定 648-root 分母不缩水。
- provider timeout/connection/rate/provider/auth/client taxonomy 不再被泛化成 `executor_error`；provider 前本地错误使用显式 `executor_ai_api/ai_api_pre_provider` v2 executor error。
- provider failure 无 raw output/usage 时仍可作为 evidence-complete failure，但 outcome 与 token/cost/latency 保持 `null/NA`；汇总使用 complete-or-null，并写 `sample_size/missing_count`，不补 0。
- execute/replay 现在从持久化 metrics rows 生成 Exp5 6 个 audit + 8 个 paper artifacts；suite、metrics digest、rows digest 与 inventory 必须一致，空 inventory 或调用方注入 rows 不能产论文结果。
- 正式 suite 若声称 `paper_eligible=true` 而 renderer 失败，CLI 非零退出。`local/run_exp5_v3_smoke.ps1` 冻结 v4 suite/profile 的 8 roots/60 units/60 attempts/四模型身份并要求成功后存在 `audit/exp5_endpoint_smoke_evidence.json`。

## 最近验证证据

- EPD-027 Task 13 accepted 证据：commit=`3f8d4ec2284d472896b706a3d94112f81be7e7c2`；严格 2 文件；review RED=`3 failed`→targeted=`3 passed`；canonical=`16 passed in 13.85s`；最小化后=`16 passed in 12.30s`；production compile、diff-check、forbidden import/alias scan、allowlist 均通过；final reviewer=`PASS`；provider/network calls=`0`，无 paid receipt；未运行 Fast、Full、LeanAudit，Full intentionally not run。
- EPD-027 Task 12 accepted 证据：commit=`a1183c6645d067409b2cfbce54c909aaf4b569c1`；真实 loader RED=`1 failed`；Task 2 contract suite=`196 passed`；Task 12 suite=`5 passed`；最小化后 combined=`201 passed in 18.17s`；final reviewer=`PASS`。严格 5 文件，Task 2 contract 最小 plan-out 只同步 Task 6 后的 profile binding 与派生 digest，无指标语义变化；provider/network calls=`0`，无 paid receipt；未运行 Fast、Full、LeanAudit，Full intentionally not run。
- EPD-027 Task 11 accepted 证据：commit=`1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`；严格 10 文件（6 production + 4 tests）；post-min trace final exit `0`，`10 passed in 3.58s`；parent commit 32、logical scheduler 5、Factorization + Lean targeted 2、recovery 39、descriptor 3 通过；6 production compile/diff-check/allowlist/secret-output 通过；final reviewer=`PASS`；测试最小化删除 7 行 / 1 test。canonical 首次 tool timeout，cache 暴露 3 个 v2 descriptor 失败，修复后 3 nodeids pass；不得据此记录 canonical PASS。provider/network calls=`0`，无 paid receipt；未运行 Fast、Full、LeanAudit，Full intentionally not run。
- EPD-027 Task 9 accepted 证据：commit=`1109e852`；final canonical exit `0`，`42 passed in 36.58s`；final review=`PASS`，Critical/Important/Minor=`0/0/0`；C1 typed in-process checkpoint/resume、C2 六类 queue 闭合；test minimization=`NO_CHANGE`；7 个 production 文件 `py_compile` 与 diff-check exit `0`；严格 12 文件包含用户授权的 `src/tokenshare/storage/artifacts.py` Windows normal-path 映射且 logical identity 不变；provider/network calls=`0`。未运行 Fast、Full、LeanAudit。
- EPD-027 Task 8 accepted 证据：commit=`6802918d`；最终 exact 5-file command exit `0`，`77 passed in 15.20s`；manifest 专项 `3 passed`、cross-check `4 passed`；fake-only，真实 provider/network/secret=`0`；4 个 production 文件 `py_compile`、diff-check、allowlist、secret scan exit `0`；final review=`PASS`，Critical/Important/Minor=`0/0/0`；test minimization 3 分钟=`NO_CHANGE`，collect-only=`19`（行为用例 `18`）。没有 paid receipt；未运行 Fast、Full、LeanAudit。
- EPD-027 Task 6 accepted 证据：commit=`5f97034c`；canonical combined exit `0`，`44 passed in 25.14s`；network tripwire/provider calls=`0`；2 个 production 文件 `py_compile` 与 diff-check exit `0`；comprehensive final review=`PASS`，C1 terminal conflict/C2 repeat-sample 跨 replacement closure 已闭合，Critical/Important=`0/0`；test minimization `<5min`=`NO_CHANGE`。profile 仅同步获授权的 plan authority/profile 派生 digest，budget digest 不变且无实验参数漂移；本轮未运行 Fast、Full 或 LeanAudit。
- EPD-027 Task 4 accepted 证据：focused `125 passed`；canonical 初次 `323 passed / 39 failed` 后 fixture 逐步收敛；最终正式 E2E nodeid exit `0`，`1 passed in 6.68s`（total `7.635s`），日志=`%TEMP%\tokenshare_task4_canonical_single_selection_nodeid.log`；provider calls=`0`，network tripwire loaded。
- Task 4 final review=`PASS`、Critical/Important=`0/0`；post-accept test minimization 3 分钟=`NO_CHANGE`（5 distinct tests）；`py_compile`、diff-check、name-only 均 exit `0`。本轮未运行 Fast、Full、LeanAudit，也未联网或调用真实 API。
- EPD-027 Task 3 post-task 测试压缩 `66643084`：test defs / 估算 cases=`77/153 → 66/118`；canonical scoped exit `0`，`118 passed in 5.55s`；独立 reviewer PASS，Critical/Important/Minor=`0/0/0`，provider calls=`0`。本次未重复运行 Fast、Full 或 LeanAudit。
- EPD-027 Task 3 accepted 后 Fast：`.\init.ps1` exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 通过，provider calls=`0`；Full intentionally not run per user instruction，LeanAudit/API 未运行。
- 状态同步时直接调用 `verification/run_verification.py`，误判其为无 pytest harness，实际重复执行 Fast：exit `0`，`468 passed, 1 skipped in 18.78s`。该次不算新增覆盖；后续不得用此命令规避 Fast 去重门禁。
- EPD-027 通用 ledger-binding `860b7c48`：focused `40 passed`、review PASS；typed-hook `4ea293b1`：`69 + 286 passed`、review PASS；Task 3 `f9773944`：focused `101 passed`、review PASS。三项均 provider calls=`0`。
- EPD-027 记录前基线 `.\init.ps1`：`458 passed, 1 skipped in 17.91s`；JSON/SQLite、harness 与 compileall 通过，未联网、未调用真实 API。
- EPD-027 权威设计、台账、AGENTS、feature/progress/handoff 与 code map 同步后 `.\init.ps1`：`458 passed, 1 skipped in 17.96s`；JSON/SQLite、harness 与 compileall 通过，未联网、未调用真实 API。
- EPD-025 权威设计、参数台账和 harness 状态同步后运行 `\.\init.ps1`：`457 passed, 1 skipped in 19.99s`；JSON/SQLite、harness 与 compileall 均通过，未联网、未调用真实 API。
- 本轮结果完整性独立复审最终为 Ready：无 Critical/Important/Minor；补充探针确认 `protocol_task_id` 模型记录可验证，URI-only artifact ref 被拒绝。
- 本轮统一核心回归：`356 passed in 154.73s`；Exp3/4 与通用 smoke launcher supervision：`9 passed in 3.27s`。
- 本轮最终 Fast：`457 passed, 1 skipped in 16.78s`；JSON/SQLite、harness 与 compileall 均通过。
- 根集成相关测试：`442 passed in 145.11s`。
- 根集成后的 CLI/launcher/DeepSeek transport 再验证：`93 passed in 98.77s`。
- 最终 Fast：`456 passed, 1 skipped in 17.07s`；此前唯一失败是旧 DeepSeek timeout 测试仍期待笼统 `executor_error`，更新为真实 `timeout` taxonomy 后通过。
- Exp3/4 独立审计：核心 87 passed；worker-death production 5 passed；fault/ablation lifecycle 11 passed；metrics 13 passed；report/replay CLI 2 passed。
- Exp5 evidence/model policy：41 passed；renderer/schema/projection/formal 组合：322 passed；PowerShell launcher supervision：5 passed。
- 离线 identity-only：Exp3/4=`11 roots / 22 first-attempt units / 54 attempt upper`；Exp5=`8 / 60 / 60`，均为固定模型/请求控制且不创建目标 output root。
- authoritative Exp1–5 plan-only：`6,384 roots / 40,520 units / 81,272 attempt upper`；精确 token/cost/disk/max-condition 全部匹配 EPD-026，测试 transport 明确禁止 provider 调用且 observed calls=0。
- Exp5 formal 在未提供 bundle 时 exit 3、provider calls=0，并持久化 canonical 四 member 的 `missing_smoke_evidence_bundle` 诊断。
- `git diff --check`、Python compile/compileall 与 Exp5 launcher PowerShell parse 通过。
- 暂停前最小回归：`test_paper_formal_checkpoint.py + test_paper_formal_evidence.py` 为 `142 passed in 23.43s`；shared-reference dispatch、missing-shared-evidence fail-close、compaction-guard resume 三项 runner 回归为 `3 passed in 2.29s`。均离线、provider calls=0。
- 暂停收尾 Fast：`.\init.ps1` 为 `458 passed, 1 skipped in 21.06s`，JSON/SQLite、harness 与 compileall 均通过；未运行 Full、LeanAudit 或真实 API。

## 尚未执行与残留

- EPD-025/027 的实施已不再是“只完成设计同步”：Task 2–14 的 metric contract、canonical direct results、stable request identity、immutable bank、inventory/preflight、budget、acquisition、logical scheduler、parent-owned commit ABI、trace-backed executor / dual provenance 与 standalone Exp1/Exp2/Exp3 projectors 已 accepted；Exp4–5 projector、formal metrics/report/renderer、execute/replay 与论文管线正式接线仍须按后续 Task 完成。本轮没有启动真实 API。
- 按用户要求，本次审计/修复没有新运行 Full、LeanAudit、force-all、真实 API smoke 或正式矩阵；未检查人为注入攻击。上面的真实调用来自用户此前启动的历史 smoke。
- 历史审计时当前进程的 `DEEPSEEK_API_KEY` 非空；这只证明当时环境曾配置，不构成当前付费授权，也不把 secret 写入仓库或输出。Task 0–14 已 accepted，Task 15 为 queued/next；旧 Exp3/4 与 Exp5 smoke 继续暂停。
- TTFT 当前没有持久化来源，保持缺失，不伪造为 0。
- Exp5 bundle digest 仍包含 source suite 持久化路径字符串，属于跨机器可移植性 P2，不影响当前本机 lifecycle/artifact 绑定。
- PowerShell 日志 helper 在“runner 已退出但其后代长期持有继承管道写端”的极端路径仍可能等待 EOF；正常 launcher 实时脱敏、受控退出与退出码传播测试通过。该 P2 未伪装成已修复，不影响本轮结果完整性结论。
- 全量资源 A 支线暂停点：`build_shared_root_reference()` 尚未替换为 terminal snapshot-only 的 `ValidatedSharedRootReferenceIndex`；还需补 stale/duplicate source rejection、同一 suite 共享索引复用、500 synthetic roots 的 `max_live_full_outcomes <= 1` 证据，并在完成后重跑 formal runner 与 Fast。完成这些之前不得启动正式全量。
- EPD-027 Task 0–14 已 accepted；standalone Exp1/Exp2/Exp3 projector 已实现，但 Exp4–5 projector 与 formal runner/metrics/renderer/replay 的正式 pipeline 接线仍未实现。旧 Exp3/4 11-root 与 Exp5 8-root smoke 仍暂停；未获经 Task 26 校验的 paid receipt 不得运行在线并发/恢复检查。
- Task 13 production/tests 已独立提交为 `3f8d4ec2284d472896b706a3d94112f81be7e7c2`；Task 14 为未提交的暂停交接状态。本轮不 push、不 merge、不创建 PR。
- 本轮暂停状态持久化未联网、未调用 provider，未引入外部资料；仅更新 minimal handoff、`progress.md`、`feature_list.json` 与 `session-handoff.md`，不改 code map；未运行测试/Fast/Full/LeanAudit，Full intentionally not run per user；无 paid receipt，provider/network calls=`0`，不 stage/commit。
