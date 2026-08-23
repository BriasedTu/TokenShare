---
status: user_approved_frozen
document: slim_v2_final_summary_lightweight_repair_plan
scope: post-representative final reducer wiring and same-run resume repair
created: 2026-08-22
last_updated: 2026-08-23
run_scope: representative_only
---

# Slim V2 最终汇总轻量修复计划

## 1. 决策与目标

本计划由用户于 2026-08-22 批准并冻结。目标是以最小 Slim-local 改动消除最终 reducer 发布阻断、恢复冻结失败分类与精确事实统计，并保证新 `RootResultV2` 的同一 run crash/resume 能稳定形成实验指标和结果文件。

唯一成功终点是：离线 Representative 规模（72 个论文 roots + 2 个 references，共 74 executions）使用 fake execution services 但真实生产 reducer，成功生成 `summary.json` 以及 Experiment 1–5 的 JSONL/CSV；随后由新 Owner 使用全新 v2 run ID/目录启动真实 AI Representative。

### 1.1 2026-08-23 前向 Flash 修订与 exact-run 边界

用户直接将前向/new-run Experiment 1 baseline 改为 `deepseek_v4_flash_exp1_baseline` / `deepseek-v4-flash`，并冻结独立价格版本 `slim_v2.pricing.2026-08-23`：cache hit/cache miss/output 分别为 `0.05/1.50/4.50 CNY / 1M tokens`，`pricing_tier=flat`。它是普通成本投影，不形成价格门禁，也绝不沿用 Pro 的 peak/off_peak 成本公式。Experiment 5 的 SiliconFlow 价格事实仍使用其 `slim_v2.pricing.2026-08-20` 快照。

此修订不能改写已经启动的 `slim-v2-representative-real-20260822-144900-ef9128fe`。Stage 7.1 三名独立只读 reviewer 对同一证据包给出 `3/3` 一致裁决：该 exact run 已持久化的 Experiment 1 Pro + `slim_v2.pricing.2026-08-20` facts 必须原样保留；不得重写、重价、混入 Flash，或为本次模型切换增加/重复 Experiment 1 provider 调用。它只能在 narrow frozen-v2 exact-run context 中为 projector/recovery 缺陷使用同一 run 的 `--resume`，且不得作为 post-change Flash Experiment 1 结果的可比或可发表依据。这是事实标签与报告边界，不是新的 runtime/publication/price gate。

lease-expiry natural-rejection 修复已由独立实施者完成：`projector._natural_rejection_stage()` 只接受唯一 `lease_expired + acceptance_status=rejected + result_kind=succeeded + rejection_reason=lease_deadline_exceeded` 的既存终端事实，并投影到既有 `child_execution` 阶段；非该窄条件仍保留原有严格失败-submission 要求。实现先通过 `625,812 ms` lease 边界的 fail-first regression，再经 fresh spec/quality review 与 Owner 级 13-test focused verification。该修复同时覆盖 Exp2–4 共用的 logical-source-latency 投影路径，但不改变 fault、taxonomy、指标或 Exp4 route evidence。

前向 Flash identity/config、按 experiment 选择 `pricing_versions={"exp1": "slim_v2.pricing.2026-08-23", "exp5": "slim_v2.pricing.2026-08-20"}` 的 schema/CLI wiring、Flash 独立价格公式与 reducer 的 source-price 映射也已完成并复核。旧 Pro 价格函数只用于 exact historical cohort，不能成为任何前向 Flash 公式的回退。

为上述 exact run 的唯一未投影 Exp2 terminal root，已完成经复核的 narrow frozen-v2 ledger reproject：它只读同一 run 的终端 ledger、request/submission artifacts 与同一 run trace，重建 typed fixed-response attempts 后写入 root projection；不得运行协议、provider 或 transport。该路径只匹配 exact run/config/root，且对重复 terminal submission identity、缺 terminal ledger、已有 snapshot/result 或非终态上下文 fail closed。focused test 显式确保无 transport/provider 调用。同一 run 的 `--resume` 已完成 root processing；其未发布的 reducer 阻断与精确终态记录见第 7.1 节。

## 2. 冻结范围

本轮只包含六项：

1. reducer 的 value/reason 一致写入、Exp2 repeat 旧 reason 清理、所有直接正式 ratio 的零分母 reason；
2. 三类失败 count 直接按 `failure_kind` 分组；
3. infrastructure-invalid cell 保留已发生的精确计数与资源事实，只使相关科学 rate/effect 为 null；
4. ordinary root result 升级为严格 `tokenshare.slim_v2.root_result.v2`；
5. 新 v2 run 的 Exp1 resume 从已持久化 protocol snapshot 与 tail traces 重建完整 attempts；
6. Representative 规模 fake execution + 真实 reducer 的端到端发布验证。

明确排除：

- 不修改 Exp4 `route_status`/route evidence 的生成、schema 或 cell validity；现有问题不阻断本轮输出时延期处理；
- 不迁移、不兼容、不读取旧 `root_result.v1`，不复用旧 Representative run；
- 不新增指标、表、字段、failure taxonomy、实验条件或 provider 调用；
- 不修改 shared core/local_runtime/plugin/executor；
- 不做安全、防注入、签名、门禁、publication/evidence closure；
- 不运行 Full、LeanAudit、Lean 专项 suite、全量 catalog、`lake`/`lean` 回归；
- Tasks 1–6 验证期不调用真实 provider。

## 3. 唯一实现语义

### 3.1 值与原因

reducer 使用一个小型内部写入路径保证同一 metric 只有一种状态：

- 写入计算值时，同时删除该 metric 的 `missing_reason` 和 `not_applicable_reason`；
- ratio denominator 为 0 时，写 `value=null` 与 `missing_reason=zero_denominator`；
- 其他缺失/不适用继续使用现有冻结 reason，不改变任何公式或 bootstrap；
- 不增加新的 reason taxonomy。

该路径必须覆盖 Exp2 repeat min/max/relative difference 的后写值，并覆盖 Exp3/4/5 等 reducer 中所有直接构造的正式 point ratio；不要求机械改写与 ratio 无关的字段。

### 3.2 失败分类与 infrastructure-invalid

- `no_final_failure_count`、`incorrect_final_failure_count`、`infra_invalid_failure_count` 只按 committed root 的 `failure_kind` 直接分组，禁止用 `final_result_present/verified_correct` 重推断；三类互斥，其和为 `failure_root_count`。
- infrastructure-invalid 存在时，completion/success 及相关科学 rate/effect/interval 仍按权威写 `null + infrastructure_invalid_root_present`。
- inventory、实际 attempts、fault/replacement counts，以及输入完整的 token/cost/wall-clock 等精确事实继续报告。删除 Exp3/Exp5 使整个后缀提前返回的传播方式；单个资源字段自身输入缺失时仍按原规则为 null。

### 3.3 v2 与同一 run 恢复

- schema literal 固定为 `tokenshare.slim_v2.root_result.v2`，`failure_origin` 必须出现但可为 null；reader、protocol projection、result writer 与 reducer 只接受 v2。
- 不提供 v1 reader、v1→v2 migration、双版本 reducer 或旧 run 修补。真实 Representative 使用全新 run ID/目录从头开始。
- 同一 v2 run 中允许 crash/resume。Exp1 最终 `attempts[]` 统一由 protocol snapshot 的 attempts 加当前 root 全部已持久化 coverage-tail trace attempts 构造，顺序为 snapshot protocol 顺序，然后按冻结 tail target 顺序、每条 trace 的自然 attempt ordinal。
- fresh/resume 对相同持久化事实必须得到相同 result attempts、tail summary、provider call/token/cost 计数；不得重复已有 trace 或 terminal provider call。

## 4. Task 与 Owner 接力

三个 Owner 是三个独立、侧边栏可见的 Codex 对话，不是同一对话内的内部 agent；它们直接顺序续接同一个保存项目 dirty 工作树。

| Owner 对话 | Tasks | 职责 |
|---|---|---|
| Owner 1 (`gpt-5.6-sol/high`，thread `01a028ea-9c35-7391-ad6a-3fdccc20467e`) | 1–3 | 统筹 reducer 修复；每项由独立子智能体实现，并由 fresh 子智能体做规格与代码质量复核 |
| Owner 2 (`gpt-5.6-sol/high`，thread `01a028ea-e22d-7a21-bbb6-62283ea8394f`) | 4–6 | 等待 Owner 1 完成后，统筹 v2、resume 与真实 reducer E2E；每项由独立子智能体实现并复核 |
| Owner 3 (`gpt-5.6-sol/high`，thread `01a028eb-386f-7fe2-b1ff-867c40209b36`) | 真实运行 | 等待 Owner 2 明确准许后，以全新 run ID/目录完成 preflight 并启动真实 AI Representative；运行中断只按 v2 resume 恢复 |

各独立对话的 Owner 不直接承担具体代码修改或代码审核；其职责是限定范围、分派对话内子智能体、合并同一工作树中的结果、运行 owner-level focused verification、监督 Critical/Important 清零并更新本计划与 `progress.md` 顶部。风险驱动验证优先，不为 helper 或覆盖率堆测试。

## 5. 六个实施 Task

### Task 1：reducer value/reason 原子一致

主要文件：`src/tokenshare/experiments/slim_v2/reducer.py`、`tests/experiments/slim_v2/test_reducer_golden.py`。

最小验收：

- 可计算 Exp2 pair 的 repeat min/max/relative difference 为数值且没有同名旧 reason；
- Exp3、Exp4、Exp5 各至少一个合法零分母正式 point ratio 为 `null + zero_denominator`；
- 不改变公式、metric ID、bootstrap 或表 schema；
- formal occurrence validator 不再因“数值与旧 missing reason 并存”或“合法零分母无 reason”阻断。

### Task 2：失败 count 直接读取 taxonomy

主要文件：`reducer.py` 与 focused reducer tests。

最小验收：构造 schema-valid 且可暴露重推断差异的 roots，证明三个 breakdown 只由 `failure_kind` 决定，互斥且求和闭合；`failure_origin` 只作诊断。

### Task 3：infra-invalid 保留精确事实

主要文件：`reducer.py` 与 Exp3/Exp5 focused tests。

最小验收：含 infrastructure-invalid root 的 cell 中，科学 rate/effect/interval 为 null 且 reason 正确；同时已记录的 attempt/fault/replacement counts 以及输入完整的 token/cost/wall-clock 仍输出精确值。不得扩大到 Exp4 route 修复。

#### Owner 1 Tasks 1–3 完成证据（2026-08-22）

- **修改文件**：生产代码仅修改 `src/tokenshare/experiments/slim_v2/reducer.py`；focused tests 仅修改 `tests/experiments/slim_v2/test_reducer_golden.py`。Owner 交棒证据同步到本计划、`progress.md` 顶部和 `Doc/TechnicalDocument/tokenshare_v1_code_map.md`。未修改 shared code、Exp4 route evidence、RootResultV2 或 Tasks 4–6。
- **Task 1**：实现子智能体先得到 `4 failed, 20 deselected`，分别暴露 Exp2 repeat 数值残留 placeholder reason，以及 Exp3/4/5 合法零分母缺 `zero_denominator`；新增小型 value/reason 与 ratio 写入路径后整份 reducer golden 为 `24 passed`。fresh spec reviewer=`SPEC_COMPLIANT`，fresh quality reviewer=`APPROVED`，最终 Critical/Important/Minor/out_of_scope=`0/0/0/0`。
- **Task 2**：fail-first 为 `1 failed`，证明旧 `_common()` 从 `final_result_present/verified_correct` 重推断并错分 taxonomy；修复后三类 breakdown 只按 committed `failure_kind` 直接分组，互斥求和，缺 committed result 只影响 inventory infra。整份 reducer golden 为 `25 passed`；fresh spec=`SPEC_COMPLIANT`、quality=`APPROVED`，最终 `0/0/0/0`。
- **Task 3**：首轮 fail-first 为 Exp3/Exp5 精确事实 `2 failed`，另有 missing usage reason `1 failed`；删除 Exp3/Exp5 过宽 early return 后，review 又发现并关闭三项 Important：reference-only infra、missing committed reference，以及 infra 与 required-slot missing metadata 不一致。最终 Exp3/Exp5 在 infra cell 保留输入完整的 counts/token/cost/wall-clock 与 pair inventory，科学 rate/effect/interval 统一为 `null + infrastructure_invalid_root_present`，字段自身缺输入仍保留原 reason。最终 fresh spec=`SPEC_COMPLIANT`、quality=`APPROVED`，范围内 Critical/Important/Minor/out_of_scope=`0/0/0/0`。
- **Owner fresh verification**：`test_reducer_golden.py -q` 为 `31 passed in 10.74s`；完整 `tests/experiments/slim_v2 -q` 为 `168 passed in 328.03s`；Slim 源码与测试 `compileall` exit 0；`git diff --check` exit 0（仅既有 LF→CRLF 提示）。全部命令显式设置 `PYTHONPATH=src` 并使用 `tokenshare` conda 环境。
- **边界与交棒**：provider/network=`0/0`；未运行真实 provider、representative/full、LeanAudit、Lean suite/catalog、`lake` 或 `lean`；未提交、暂存或创建工作树。Owner 1 到此停止，不启动 Tasks 4–6；Owner 2 thread `01a028ea-e22d-7a21-bbb6-62283ea8394f` 可直接从当前 dirty 工作树执行 Task 4。

### Task 4：严格 RootResultV2

主要文件：`schema.py`、`storage.py`、`projector.py`、相关 schema/storage tests。

最小验收：所有生产 writer/reader/embedded protocol projection 使用 v2；`failure_origin` 必须出现且可 null；v1 fixture 被明确拒绝。删除或更新仍声称 v1 的 Slim-local literal/断言，不增加迁移代码。

#### Task 4 完成证据（2026-08-22）

- ordinary root result 已收口为唯一生产 literal `tokenshare.slim_v2.root_result.v2`；`failure_origin` 必须出现且允许为 `null`。writer、reader、embedded protocol projection、projector、runtime、CLI 与 reducer 均只接受 v2，v1 fixture 和 v1 source run 被明确拒绝。
- Exp2–4 的 source run preflight 只接受具有 strict v2 committed Experiment 1 roots 的全新来源；没有 v1 reader、migration、compatibility shim、dual reader 或 old-run reuse。
- schema 跨字段不变量补齐 `verified_correct` 与 `failure_kind` 的双向互斥：已验证正确的 root 不得携带 failure taxonomy，携带 failure taxonomy 的 root 不得标为 verified correct。
- fresh spec reviewer=`SPEC_COMPLIANT`，fresh implementation-quality reviewer=`APPROVED`；最终 `Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`。Task 4 相关组合验证为 `94 passed`，并单独确认 Tasks 1–3 reducer 回归 `24 passed`。

### Task 5：Exp1 v2 resume attempts 闭合

主要文件：`runtime.py`、必要的 `storage.py/projector.py` 小范围调用点、resume focused tests。

最小验收：在 protocol snapshot 后、部分或全部 tail trace 持久化后模拟中断；resume 不第二次调用已有 unit/terminal ordinal，最终 attempts 包含 protocol 与全部 tail attempts，顺序稳定；fresh/resume 的 attempts、tail summary 和 provider call/token/cost 计数相同。

#### Task 5 完成证据（2026-08-22）

- fresh 与 resume 共用同一个 attempts 重建路径：读取 protocol snapshot attempts，并合并当前 root 已持久化的全部 coverage-tail `UnitTraceV1.attempts`；顺序固定为 protocol snapshot 原顺序，然后按冻结 tail target 顺序与每条 trace 的自然 ordinal 排列。
- 对相同持久化事实，fresh/resume 的最终 `RootResultV2` 经 `asdict()` 比较完全相等，tail summary 及 provider call/token/cost 计数相同；完整 tail 场景调用数保持 `3→3`，部分 tail 场景为 `2→3→3`，证明首次 resume 只补一个缺失 target、再次 resume 不重复调用。
- fresh spec reviewer=`SPEC_COMPLIANT`，fresh implementation-quality reviewer=`APPROVED`；最终 `Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`。focused 证据包括 `test_runtime_resume.py=20 passed`、`test_answer_paths.py=19 passed`、`test_schema.py=14 passed`。

### Task 6：Representative 规模真实 reducer E2E

主要文件：`tests/experiments/slim_v2/test_cli_e2e.py`，只在确有接线缺口时改 Slim-local生产代码。

最小验收：沿 `representative` 或等价 `run-all --profile representative` 提交 72 paper roots + 2 references，execution 可 fake，但 reducer 必须使用生产 `reduce_run`。命令成功并发布 `summary.json` 与 exp1–exp5 各自 JSONL/CSV；断言不是 mock reducer，且没有真实 provider 调用。

#### Task 6 完成证据（2026-08-22）

- production CLI Representative fake E2E 使用冻结真实 profile：Exp1/2/3/4/5 paper roots=`4/12/8/44/4`，合计 `72`；Factorization 与 Lean auxiliary references 各1，共 `2`，总 executions=`74`。原始用户硬约束始终是 `72+2`；不采用与冻结 profile 冲突的旧测试摘要 `6/18/30/12/6`。
- E2E 默认走 production `execute_root`、runtime/coordinator、storage/publication 与真实 `reduce_run()`；唯一 fake 边界是 provider 的 `_open_response` 网络入口。真实 provider/network=`0/0`，fake HTTP calls=`86`；未运行 Lean、`lake` 或任何 Lean 二进制。
- reducer 恰好发布 `11` 个文件：`summary.json`，以及 Experiment 1–5 各一份 JSONL 和 CSV。五实验表行数依次为 `3/20/16/108/4`；测试断言使用生产 reducer，不以 mock 或手写 summary 代替。
- E2E 暴露的 storage closure 修复只允许 Experiment 1 读取其 coverage-tail traces；非 Experiment 1 snapshot/result 仍必须遵守禁止 tail 的既有规则，没有放宽跨实验 schema。
- representative-scale E2E=`1 passed in 548.10s`。fresh spec reviewer=`SPEC_COMPLIANT`，fresh implementation-quality reviewer=`APPROVED`；最终 `Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`。

#### Owner 2 最终验收与交棒（2026-08-22）

- Owner 2 thread=`01a028ea-e22d-7a21-bbb6-62283ea8394f` 已完成并验收 Tasks 4–6；连同 Owner 1 thread=`01a028ea-9c35-7391-ad6a-3fdccc20467e` 的 Tasks 1–3，本计划六项全部完成，范围内 review finding 清零。
- Owner fresh verification：`test_reducer_golden.py -q`=`31 passed in 10.91s`；`test_schema.py + test_runtime_resume.py -q`=`35 passed in 17.17s`；`test_cli_e2e.py -q`=`26 passed in 637.23s`；完整 `tests/experiments/slim_v2 -q`=`177 passed in 917.87s`；Slim V2 源码/测试 `compileall` exit 0；`git diff --check` exit 0，仅有既有 LF/CRLF warning。
- provider/network真实调用=`0/0`。本 Owner 未运行真实 provider、真实 Representative/Full、LeanAudit、Lean suite/catalog、`lake`或`lean`；未 stage、commit 或创建 worktree。
- Owner 3 thread=`01a028eb-386f-7fe2-b1ff-867c40209b36` 可直接从同一 dirty working tree 开始新的真实 Representative，但必须先完成其自身 credentials、Lean environment 与零调用 preflight，并使用全新 v2 run ID/目录；不得迁移/读取 v1 或复用旧 run。Owner 2 不启动任何真实调用。

## 6. Owner 级 focused verification

各 Owner 按实际改动选最小集合；最终至少运行：

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2/test_schema.py tests/experiments/slim_v2/test_storage.py tests/experiments/slim_v2/test_runtime_resume.py -q
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2/test_cli_e2e.py -q
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2 -q
conda run --no-capture-output -n tokenshare python -m compileall -q src/tokenshare/experiments/slim_v2 tests/experiments/slim_v2
git diff --check
```

若实际 test 文件名不同，使用现有对应文件并在证据中精确记录；不得为满足命令名称创建空壳测试。

## 7. 真实 Representative 交棒

Owner 3 只能在以下事实全部成立后开始真实调用：Tasks 1–6 的实现、规格复核、质量复核和 owner-level focused tests 通过；生产 reducer 的 74-execution 离线 E2E 已真正发布五实验表；当前进程没有遗留 Slim run；两类 provider credential 与既有 Lean environment pass 可用；磁盘计划仍满足已冻结上界。

运行必须使用新的唯一 v2 run ID/目录，不复制或读取旧 v1 root results。启动前先执行零调用 `plan --profile representative`，随后执行同一生产 `representative` 入口。若同一 v2 run 中断，先核对 process、result/trace/call journal 和 unknown terminal，再使用 `--resume`；不得创建第二个 run 冒充恢复。运行完成后使用同一生产 reducer 发布结果并记录论文 roots、references、provider calls、summary 与五实验表证据。

### 7.1 当前 exact-run 恢复状态（2026-08-23）

上一段的“新的唯一 v2 run”要求已由该 run 的首次启动满足；当前不是新 run。完成 process、results/traces/calls/responses journal 与 unknown-terminal 预检后，Owner 对 `slim-v2-representative-real-20260822-144900-ef9128fe` 执行同一 run 的 `representative --resume`，其 narrow frozen-v2 compatibility 只重投影既有 Exp2 terminal ledger，不会重放该终端 protocol 或重复任一已终端 provider 调用。该 resume 已修复 Exp2 投影并完成 root processing；终态核对为 process=`0`、冻结 inventory roots=`72`、result files=`72`、calls=`52 intent/52 terminal`、responses=`47`。每个带 response reference 的 terminal 均指向存在文件；5 条 `MiniMaxAI/MiniMax-M2.5` `provider_transport_error` terminal 没有 response，unknown terminal=`0`。

该 exact run 始终是变更前 Pro cohort，绝不是 Flash evidence。`reduce --run-dir` 曾在 publication 前停止，精确异常为 `ValueError: missing first-attempt verification evidence: factor_v2_hard_145:range_3`；当时metrics、`summary.json` 与 temporary publication 文件均为零。因此本 Representative 未通过、未发布，且不得因 root processing 已完成而宣称全实验成功。

#### 7.1.1 Exp5 parsed-unsubmitted reducer 法定人数裁决（2026-08-23）

- owner 先暂停 reducer 修改，保留同一只读证据包，并并行分派三个全新、互不通信的只读审阅者：`/root/reducer_semantics_a`、`/root/reducer_semantics_b`、`/root/reducer_semantics_c`。三票分别为继续阻断、保留nonpass、quality指标置null，实质上全部不同；依接力协议第7.1节，再由全新只读仲裁者`/root/reducer_semantics_audit`读取同一证据和三份原始意见，作出绑定裁决 `C — EXP5_PARSED_UNSUBMITTED_METRIC_NULL_V1`、`authorize=yes`。
- 证据事实是同一MiniMax root的`range_1/range_3/range_7`都已持久化真实`2xx`、raw response、`parse_result=result_kind=parsed`、usage/token/cost，却均无 submission/verification，所以`verifier_result/checker_result=null`且已有`not_applicable_or_unavailable`。其余5个child为无response的真实transport failure。该事实不改写root的`no_final/provider_transport_exhausted`、Pro identity/pricing、calls、responses、roots或system journal。
- 授权的最小修复只写Slim-local `reducer.py`和`test_reducer_golden.py`：窄匹配该既有标记事实为内部不可评估状态，保留actual call、coverage、token、cost、wall-clock和三类已具证据的breakdown；不补造verification、不新增taxonomy/metric/raw字段、不把它伪称transport/parse/rejection。`checkable/rejected`不计该attempt；`first_attempt_without_verifier_accepted_candidate_count`、`first_attempt_nonpass_rate`及其bootstrap/CI/variance/standard-error派生写`null + not_applicable_or_unavailable`。没有既有N/A原因的同形null继续fail closed。
- 必须先完成fail-first与golden/reducer focused验证，随后才可对这个exact run仅执行一次本地reducer publication；禁止`resume` protocol、provider/Lean调用或改写原始运行文件。发布后需核对72 inventory/results、52 intent/terminal、47 response、5 transport、unknown=0仍不变。新Flash Representative另用新run ID，不复用或标注旧Pro cohort为Flash。

#### 7.1.2 本轮实施、发布与前向运行（2026-08-23）

- 规模变更的文档先行：Full Exp5 冻结为28个Factorization hard加纯逻辑、函数集合、归纳各3个Lean case，四模型仅`repeat0`、零retry；由inventory派生16 conditions、148 roots、1,136 provider upper，Full同步为7,054 paper roots、7,160 executions、7,046 online upper、84,618 fixed和91,664 total protocol attempts。
- 实现与回归仅改Slim-local `profiles.py`、`reducer.py`、`test_profiles.py`与`test_reducer_golden.py`。三个初始语义意见、第四位绑定仲裁、fresh文档复核、fresh规格复核及fresh代码质量复核均已完成；审查中发现并修复C root不能污染验证拒绝率bootstrap的边界。owner fresh focused验证为`test_profiles.py test_reducer_golden.py -q`=`51 passed in 12.72s`、Slim源码/测试`compileall` exit 0；零调用Full plan确认当前全量数字与strict-P95 estimate=`9.761 GiB`。
- 已按绑定范围对exact Pro run**仅执行一次**本地`reduce --run-dir`，成功发布`metrics/summary.json`和Exp1–5各JSONL/CSV共11文件。发布前后对calls、inventory、references、responses、roots、system、traces七个输入目录逐文件SHA-256 manifest核对均一致；未调用provider/Lean、未resume协议，旧Pro cohort仍不构成Flash证据。
- 新的前向Flash Representative已用`slim-v2-representative-flash-20260823-210500`从零启动并完成root processing与同一生产reducer发布：72个paper roots、74个executions（含2个Exp3 references）、72个root results，发布`metrics/summary.json`与Exp1–5各JSONL/CSV共11文件。真实provider为Exp1=`19`、Exp5=`32`，共51个attempt，全部为terminal/HTTP 200、带raw response；Exp1的4个Flash roots均`verified_correct`。Exp2–4中预注册fixed replay、fault和ablation条件的非完成root按各自实验语义保留，不能误作provider故障；Exp5的3个非完成root则是模型比较本身的真实`no_final`结果，须按其模型/parse/verification原因解读。不得创建第二个run或将旧Pro结果混入。

## 8. 完成标准

- Tasks 1–6 均由实现子智能体完成，fresh 规格与代码质量复核的范围内 Critical/Important 为 0；
- 所有 focused verification 和 74-execution fake + real reducer E2E 通过；
- 代码与文档没有引入本计划排除项；
- `progress.md` 顶部和 Slim V2 code map 按实际变更同步；
- Owner 3 已启动并持续推进新的真实 AI Representative；若外部 credential/服务客观阻断，必须留下精确零/已发生调用与可恢复状态证据，不能改代码绕过。
