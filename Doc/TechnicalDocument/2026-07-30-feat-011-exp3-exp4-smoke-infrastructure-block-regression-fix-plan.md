# Exp3–4 smoke 基础设施阻断回归修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` inline；本仓库是共享 dirty worktree，本计划不创建 worktree、不提交，也不派生 sub-agent。

**Goal:** 修复真实 Exp3–4 smoke 暴露出的错误终态分类：worker 子进程 bootstrap/IPC 异常不得被记为可信实验失败并继续付费调度。

**Architecture:** 保留 provider/parser/checker/故障注入通过正常 result envelope 表达实验失败的既有边界。任何逃出 shared-reference 或 adapter dispatch 边界的异常都转换为带 condition/task 身份的 `PaperInfrastructureBlockedError` 并立即重新抛到 suite closure；suite 负责把当前 root 记为 blocked、后续 roots 记为 not_started。失败前的 adapter 文件保持只读留存，不改写历史 smoke evidence。

**Tech Stack:** Python 3.12、pytest、PowerShell、JSON/JSONL evidence。

---

### Task 1：用真实终态期望替换错误回归测试

**Files:**

- Modify: `tests/experiments/test_paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [x] 将旧的 runtime-error-as-experimental 回归改为 `test_unexpected_runner_error_records_remaining_roots_not_started`。
- [x] 断言第一个 `RuntimeError` 使 suite 为 `blocked`，adapter 只调用 `case-1`，`case-1` 为 `blocked_dependency + invalid`，`case-2` 为 `not_started`，provider attempt 为 0。
- [x] 运行该节点并确认 RED：旧实现仍返回 `completed_with_failures` 且执行 `case-2`。

### Task 2：在 dispatch 边界 fail closed

**Files:**

- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [x] 将 shared Exp1 reference 边界逃出的 `RuntimeError`/`OSError`/`TimeoutError` 包装为 `PaperInfrastructureBlockedError`，写入 `condition_id`、`task_id`、`failure_stage=shared_exp1_reference` 后重新抛出。
- [x] 将 adapter dispatch 边界逃出的 `RuntimeError`/`OSError`/`TimeoutError` 在结清预算 reservation 后包装为 `PaperInfrastructureBlockedError`，写入 `condition_id`、`task_id`、`failure_stage=adapter_runtime` 后重新抛出；不得创建 `failed_experimental` runner-error checkpoint。
- [x] 运行 Task 1 节点并确认 GREEN。
- [x] 运行 checker rejection、provider failure、worker-death incomplete continuation 的既有节点，确认结构化实验失败仍继续。

### Task 3：牵连检查与验证

**Files:**

- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [x] 运行 `test_paper_formal_runner.py`、`test_factorization_paper_adapter.py`、smoke report/CLI/launcher 定向集合。
- [x] 运行 `powershell -ExecutionPolicy Bypass -File .\init.ps1`。
- [x] 二次检查本次历史 run 的 output/supervision mtime 未变化，且没有真实 API 进程。
- [x] 同步 code map、feature evidence、progress 与 handoff；说明历史 run 不可作为“设施全部跑通”证据，需要新 run 才能验证 live worker-death。
