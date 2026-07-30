# Exp3/Exp4 实验设施全面排查与硬阻断清零实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` inline。仓库为共享 dirty worktree，不创建 worktree、不派生 sub-agent、不 stage/commit/push。

**Goal:** 对 Exp3–4-only smoke 的进程、执行、终态、证据消费全链路做一次离线全面排查，修复所有可复现的设施硬阻断，并建立防止同类回归的矩阵测试。

**Architecture:** 以两个真实失败 run 为只读证据，按 launcher→Windows child process→protocol worker→formal runner closure→smoke metrics/report/audit/replay 逆向追踪。预期实验失败继续用正常 envelope；任何设施异常都停止新 dispatch、完整持久化 blocked/not-started denominator，并且所有下游消费者必须能无例外地读取该终态。

**Tech Stack:** Python 3.12、Windows subprocess、PowerShell、SQLite/JSON/JSONL、pytest。

---

### Task 1：冻结两个真实失败现场与故障矩阵

**Files:**

- Read-only: `outputs/experiments/paper_smoke_exp3_exp4_v1_20260729T211516Z`
- Read-only: `outputs/experiments/paper_smoke_exp3_exp4_v1_20260730T073347Z_run01`
- Modify: `progress.md`

- [x] 记录进程终态、退出码、异常栈、已开始/完成/blocked/not-started denominator、provider usage 与目录 mtime。
- [x] 区分原始 Windows spawn 故障、runner terminal classification、report/audit 消费失败三个边界。
- [x] 保持历史 evidence 只读，不 resume、不补写、不重分类。

### Task 2：blocked/not-started execution classification 闭环

**Files:**

- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Modify: `tests/experiments/test_paper_formal_runner.py`
- Test: `src/tokenshare/experiments/paper_smoke_report.py`

- [x] RED：真实 runner 生成的 smoke blocked/not-started task 缺少 `formal/pilot_only/regression_only`，报告抛 `persisted smoke task eligibility flags are invalid`。
- [x] GREEN：dependency closure 从 suite `execution_classification` 写入 task/attempt/event；formal 与 smoke 分类均保持准确。
- [x] 端到端生成 smoke summary/failures/eligibility/secret/evidence manifests，并确认 blocked/not-started usage 为零且 denominator 不缩减。

### Task 3：Windows worker spawn 根因与最小复现

**Files:**

- Inspect/Modify: `src/tokenshare/local_runtime/workers.py`
- Inspect/Modify: `local/invoke_native_process_with_logs.ps1`
- Modify: `tests/local_runtime/test_workers.py` 或实际 owner 测试文件
- Modify: `tests/experiments/test_factorization_paper_adapter.py`

- [x] 完整读取 worker backend、native wrapper 与 Python `multiprocessing.popen_spawn_win32` handle 传递路径。
- [x] 对比直接 pytest、native wrapper、长时父进程三种启动环境的标准句柄与 process sentinel 行为。
- [x] 建立可重复的最小测试：强制旧 `get_context("spawn")` 抛同型错误、首轮 child payload-load 失败、首轮 process create 失败，并分别验证绕开旧 pipe、bootstrap retry 与 create retry。
- [x] 用 `subprocess` + 原子临时文件 ready/start/result/release handshake 替换内部 multiprocessing pipe；子进程 ready 前不放行 executor/provider，三次 bootstrap 失败后抛 typed infrastructure error，保留真实 PID/kill/nonzero exit/replacement/lease-recovery 事实。

### Task 4：Exp3/Exp4 执行边界矩阵

**Files:**

- Test: `tests/experiments/test_paper_faults.py`
- Test: `tests/experiments/test_paper_workers.py`
- Test: `tests/experiments/test_factorization_paper_adapter.py`
- Test: `tests/experiments/test_paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_smoke.py`

- [x] Exp3 五类 rate-fault 分别覆盖 accepted/rejected/retry/replacement/terminal envelope；worker-death 覆盖成功恢复、replacement 失败、bootstrap 失败。
- [x] Exp4 FULL/NO_VERIFICATION/NO_PARSER_POLICY/NO_REQUEUE/NO_MERGE_GATE 分别覆盖正常负面结果与上游 infrastructure block 后 not_started。
- [x] 对每个矩阵项验证 provider-attempt inventory、token/cost、fault refs、event/artifact refs、outcome/evidence 双轴和 paper-ineligible 语义。
- [x] 验证任何 infrastructure block 后不会启动新的 adapter/provider dispatch；已完成 evidence 保留且预算 reservation 归零。

### Task 5：下游消费者与 launcher 收口

**Files:**

- Test: `tests/experiments/test_paper_smoke.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`
- Test: `tests/experiments/test_exp3_exp4_v3_launcher_supervision.py`
- Test: `tests/experiments/test_paper_terminal_outcomes.py`

- [x] completed、completed_with_failures、blocked、incomplete 四类 suite terminal 均能生成或明确拒绝对应报告，不抛未分类内部异常。
- [x] replay-only 对 blocked/not-started evidence 零 provider 调用且稳定复算。
- [x] launcher 对 runner exit 0/3、stdout/stderr、wrapper terminal、secret redaction 和全新 identity fail-closed 均有测试。

### Task 6：综合验证与状态同步

**Files:**

- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [x] 运行所有 Exp3/Exp4 owner 测试、formal runner/metrics/report/smoke/CLI/launcher/worker backend 组合。
- [x] 运行 `powershell -ExecutionPolicy Bypass -File .\init.ps1`。
- [x] 检查两个历史 run mtime 未变化、无实验进程、无真实 provider 调用。
- [x] 记录已修根因、排除项、验证证据和剩余只能由新真实 run 验证的风险；不运行 Full、LeanAudit、force-all 或真实 API。

## 现场事实与修复结论

1. 首次 run `paper_smoke_exp3_exp4_v1_20260729T211516Z` 的旧 runner 把 worker bootstrap 设施错误降级为可信实验失败并继续 Exp4。该误分类已由 `PaperInfrastructureBlockedError` 修复。
2. 第二次 run `paper_smoke_exp3_exp4_v1_20260730T073347Z_run01` 证明 hard-stop 已生效：5 个 Exp3 rate-fault roots 为 1 completed/4 failed，worker-death root 为 blocked，5 个 Exp4 roots 全部 not_started；runner exit code=3，23 provider attempts、430,977 tokens、配置口径 cost estimate 2.3151。随后报告器因 closure records 缺 `formal/pilot_only/regression_only` 失败；未来 closure 现已从 suite classification 同步这些字段并完成报告闭环。
3. 两个真实 run 的相同 `multiprocessing.spawn -> reduction.duplicate -> DuplicateHandle -> WinError 6` 均发生在项目 child target 入口之前。新 `ProcessWorkerBackend` 不再调用 `multiprocessing.get_context`、Pipe、Event 或 spawn bootstrap handle；独立解释器仅通过文件 handshake 传递 ready/start/result/release，并把父解释器实际 `sys.path` 传给 child。
4. 全量 Gate C 交叉测试还残留 EPD-012 之前的 `182 conditions / 41,156 roots / 1,006 supporting roots / fault_rate=0` 断言。现已修正为正式 Exp3 `162 / 36,126 / supporting=0`，P0-core/full roots=`46,478/48,377`；旧 single-case Gate C pilot 明确拒绝伪造 Exp3 zero-rate baseline，Exp3 fault/death 只走 formal/smoke production callback。

## 外部官方资料本地摘要

- 来源：CPython 官方 GitHub issue [python/cpython#82444](https://github.com/python/cpython/issues/82444)，标题为 Windows multiprocessing `DupHandle.detach()` / `DuplicateHandle(DUPLICATE_CLOSE_SOURCE)` race；访问日期 `2026-07-30`。
- 本地摘要：该 issue 记录了 Windows `multiprocessing` 标准库在 handle duplicate/detach 生命周期中的竞态类别，迁移元数据显示曾处于 `needs patch`。它支持“Windows multiprocessing handle bootstrap 并非项目协议层可证明可靠边界”的工程判断，但不能单独证明本次两个真实 run 的精确竞态机制；精确故障事实仍来自本机 Python 3.12.13 的三次 `WinError 6` 栈和安装态 `popen_spawn_win32.py/spawn.py/reduction.py` 调用链。
- 影响范围：只替换 `ProcessWorkerBackend` 的本地 child bootstrap/IPC；不修改协议 lease/retry/requeue 权威、Exp3 故障类型、provider 请求、模型、prompt、实验矩阵或论文 eligibility，也不引入第三方依赖。

## 最终验证

- worker owner=`8 passed`；local runtime=`48 passed`；Factorization/Lean/integration=`96 passed`；native wrapper=`10 passed` 且 stderr 为空。
- Exp3/Exp4 owner/callback/worker=`83 passed`；fault/ablation/terminal=`41 passed`；formal/smoke/evidence/metrics/report/CLI/launcher=`250 passed`；Gate C 全文件=`33 passed`。
- 当前 tracked Exp3–4-only identity-only：experiment IDs 仅 Exp3/Exp4，direct/actual/supporting roots=`11/11/0`，first-attempt AI units=`22`，provider-attempt upper=`54`，且 output root 未创建。
- `powershell -ExecutionPolicy Bypass -File .\init.ps1`：证据同步前=`412 passed, 1 skipped in 21.71s`，post-document 最终复验=`412 passed, 1 skipped in 15.06s`；两次均为 `python-json-sqlite-ok`、`harness-files-ok`、`compileall-ok`。既有 gitignored pytest 临时目录 listing warning 不影响任何门禁。
- 本轮 provider calls=`0`；未运行 Full、LeanAudit、force-all 或真实 API。两个历史 output/supervision mtime 保持不变，无遗留 experiment/worker child process。

## 第三次真实 smoke 跟进：endpoint identity pickle

1. `paper_smoke_exp3_exp4_v1_20260730T115251Z_run01` 从 `11:53:45Z` 运行到 `12:42:53Z`。五个 rate-fault 条件完成，worker-death 因 `TypeError: cannot pickle 'mappingproxy' object` blocked，五个 Exp4 条件由 dependency closure 停止；总计 22 provider attempts、472,160 tokens、配置口径 cost 2.573934。无遗留进程，历史 evidence 不改写。
2. 新 subprocess bootstrap 会先 pickle executor bridge；v3 frozen endpoint binding 中两处 `MappingProxyType` 是此前离线无完整 endpoint identity fixture 时遗漏的组合边界。两个 identity 已以显式 reduce/restore 闭合，恢复后仍不可变且 digest 一致；真实 v3 binding 经 `ProcessWorkerBackend` 的 worker-death/replacement 测试已通过。
3. 同时修复 smoke/formal/pilot 实际 execution 的固定 exit 0：blocked/incomplete/invalid 现在返回 3，正常 completed/completed_with_failures 和可 resume budget_exhausted 返回 0。launcher 无需额外修改即可透传。
4. 新增三项 TDD 从精确 RED 到 `3 passed`；扩展影响集分别 `219 passed`、`228 passed`；最终 Full 为 `1698 passed, 1 skipped in 799.10s`，证据同步后 Fast=`412 passed, 1 skipped in 15.82s`；JSON/SQLite、harness、compileall 通过。本跟进未调用 provider、LeanAudit 或 force-all。
