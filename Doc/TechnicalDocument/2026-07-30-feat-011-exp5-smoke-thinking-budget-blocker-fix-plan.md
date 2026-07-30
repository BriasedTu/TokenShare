# Experiment 5 smoke `thinking_budget` 启动阻断修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` and execute the steps in order. This shared dirty worktree must not be committed, reset, cleaned, or staged by this plan.

**Goal:** 修复 Exp5 v3 bootstrap 在 provider dispatch 前因 `thinking_budget` identity 漂移而退出的问题，并确保同类 pre-execution failure 不再只留下空目录。

**Architecture:** `paper_model_policy.py` 提供 Exp5 provider-specific reasoning controls 的唯一 canonical projector；preflight 与 formal runner 都调用它，不再维护两份字段白名单。CLI 只在尚未形成 `suite_manifest.json` 时为 pre-execution exception 写入 blocked smoke evidence，绝不覆盖已经初始化的 formal evidence。

**Tech Stack:** Python 3.12、pytest、JSON evidence、现有 SiliconFlow `AIAPIExecutor`/artifact store。

---

### Task 1：锁定真实 v3 reasoning-controls 回归

**Files:**

- Modify: `tests/experiments/test_paper_formal_runner.py`
- Modify: `src/tokenshare/experiments/paper_model_policy.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`

- [x] 新增测试，用 thinking SiliconFlow config 构造 `enable_thinking=true, thinking_budget=32768`，将同一 controls 放入 approved member plan，调用真实 `_condition_endpoint_contract()` 并断言返回 request limits 保留 `thinking_budget=32768`。
- [x] 运行该测试，确认 RED 为 `Exp5 approved member reasoning controls mismatch`。
- [x] 在 `paper_model_policy.py` 新增唯一字段常量与 projector：只按 `enable_thinking, thinking_budget, thinking, reasoning_effort` 的固定顺序投影存在的字段。
- [x] 让 `_exp5_request_controls()` 与 formal runner `_validate_exp5_binding()` 共用 projector；不放宽 digest、cohort、entry 或 model identity 校验。
- [x] 重跑单测，确认 GREEN；既有 strict mapping/digest 校验继续拒绝字段删除、改值或多余字段。

### Task 2：pre-execution failure 必须持久化

**Files:**

- Modify: `tests/experiments/test_run_paper_experiments_cli.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`

- [x] 新增 CLI 测试：让 `execute_paper_smoke_suite()` 在 evidence 初始化前抛出 `ValueError`，断言 exit=3、`suite_manifest.json` 为 blocked、`provider_attempt_count=0`、error summary 保留原始 failure kind/message。
- [x] 运行该测试，确认 RED 表现为 output root 为空。
- [x] 在 smoke execution exception handler 中，仅当 `suite_manifest.json` 不存在时调用 `_write_smoke_blocked_suite()`；若 evidence 已初始化则保留现有文件，不覆盖真实终态。
- [x] 重跑 CLI 测试与现有 fail-closed/resume tests，确认 GREEN。

### Task 3：离线验证与一次真实 provider probe

**Files:**

- Verify: `tests/experiments/test_paper_formal_runner.py`
- Verify: `tests/experiments/test_run_paper_experiments_cli.py`
- Verify: `tests/experiments/test_paper_model_policy.py`
- Verify: `tests/experiments/test_paper_smoke.py`

- [x] 重跑原始“到 evidence initialize 即停止”的临时诊断，不再出现 reasoning-controls mismatch，而是到达显式 diagnostic sentinel；provider calls=0，blocked evidence 已落盘。
- [x] 运行上述四文件定向 pytest：拆批为 66 + 89 = 155 passed。
- [x] 运行 `./init.ps1` Fast：412 passed、1 skipped；不运行 Full、全量 pytest或 LeanAudit。
- [x] 使用 tracked Exp5 Qwen entry、单 entry config、`max_provider_attempts=1` 发出短文本请求；首个顺序 probe 为远端 connection error，第二个成功并持久化 raw/provenance/usage，397 total/303 reasoning tokens，secret scan=0 hits。两次 probe 都不是 8-root smoke evidence。

### Task 4：状态与代码映射

**Files:**

- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`

- [x] 记录空 run01 的永久失败事实、根因、RED/GREEN、provider-call 数和真实 probe artifact 路径。
- [x] 明确 run01 不可恢复；完整 8-root smoke 仍未运行，必须使用全新 output root 与重新生成的 path-bound digests。
- [x] 二次扫描旧的“设施已能启动”表述，避免把单模型 provider probe 写成 passed 8-root smoke evidence。
