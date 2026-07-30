# Exp1–4-only Real API Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结、验证并运行 21-direct-root/22-actual-root 的 Exp1–4-only 真实 API smoke，同时保留完整 pre-call identity、真实 evidence 和 fail-closed 边界。

**Architecture:** 新 profile 只保存原 27-root profile 的 Exp1–4 canonical selectors。CLI 根据 profile experiment ids 决定是否需要 Exp5 cohort，并把启动 identity 作为 formal evidence store 的 pre-execution document 在首次 provider 调用前持久化；实际执行继续复用 `paper_formal_runner`、system runtime 和 `ProtocolEngine`。

**Tech Stack:** Python 3.12、pytest、JSON/JSONL、SQLite/event ledger、PowerShell、SiliconFlow-compatible AI API transport。

---

### Task 1: Profile 与 scope gate

**Files:**
- Create: `benchmarks/paper/paper_smoke_exp1_exp4_profile.v1.json`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_smoke.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] 先新增失败测试，要求新 profile 恰含 Exp1/2/3/4=`6/3/7/5`、总计 21 items、无 Exp5，且和原 profile 对应 items 完全相同。
- [ ] 运行精确测试，确认因 profile 缺失而 RED。
- [ ] 新增 profile；只复制原 profile 的 Exp1–4 items并更换 suite id/expected count。
- [ ] 新增失败测试，要求 no-Exp5 smoke 不加载 cohort files也能进入 execution callback，Exp5 profile 仍 fail closed。
- [ ] 修改 `_run_smoke_cli()`：只在 experiment ids 含 Exp5 时要求 `model_endpoint_cohort_preflight.status=planned`，且只在存在 preflight 时注册 Exp5 binding。
- [ ] 运行精确测试，确认 GREEN。

### Task 2: Pre-call launch manifest

**Files:**
- Modify: `src/tokenshare/experiments/paper_smoke.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_smoke.py`
- Test: `tests/experiments/test_paper_formal_runner.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] 先新增失败测试，要求 CLI 传递 schema v1 launch manifest，包含 UTC start、argv、profile/plan/catalog/budget/config digests、21/22 counts、selection digests、endpoint、request limits 和 output root，且 JSON 中不含 secret。
- [ ] 运行精确测试，确认 RED。
- [ ] 让 `execute_paper_smoke_suite()` 接收 launch manifest，并把 `smoke_launch_manifest.json` 加入 formal runner 允许的 pre-execution documents。
- [ ] 在 CLI 完成 config/key preflight 后构造 manifest；只写安全 identity，不写 key 值。
- [ ] 运行精确测试，确认 GREEN，并验证 identity mismatch fail closed。

### Task 3: 启动前门禁

**Files:**
- Modify after evidence only: `progress.md`
- Modify after evidence only: `feature_list.json`
- Modify after evidence only: `session-handoff.md`
- Modify if code changed: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`

- [ ] 运行 smoke/profile/CLI/formal runner/evidence/report 定向测试；summary 不得含 FAILED。
- [ ] 运行 `powershell -ExecutionPolicy Bypass -File .\init.ps1`；summary 不得含 FAILED。
- [ ] 用只读 preflight 计算并核对 21 direct / 22 actual、所有 digests、endpoint、100/1024/1 request limits和独立 output root。
- [ ] 记录精确启动命令与 UTC 时间；不得运行独立 `--plan-only`、Full 或 Lean audit。

### Task 4: 真实运行与监督

**Files:**
- Runtime output: `outputs/experiments/paper_smoke_exp1_exp4_20260727_run01/`

- [ ] 使用 `--real-transport --unlimited-budget` 启动，不传 Exp5 provider/cohort 参数。
- [ ] 每次间隔不超过 10 分钟读取进程、worker、CURRENT/generation、task/attempt/event/artifact、provider usage/retry/429/cost 和最后活动时间。
- [ ] 若为真实负面结果，原样保留；若为设施错误，保留旧 evidence、写最小 RED、修复设施、跑 GREEN+Fast，再按 checkpoint/new generation 规则恢复。
- [ ] 同一根因连续两次修复/恢复仍失败时停止并 structured blocked。

### Task 5: 报告、重放与状态写回

**Files:**
- Runtime output: `outputs/experiments/paper_smoke_exp1_exp4_20260727_run01/metrics/`
- Runtime output: `outputs/experiments/paper_smoke_exp1_exp4_20260727_run01/audit/`
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`

- [ ] 运行 `--replay-only`，确认 provider 调用为 0 且报告只从持久化 evidence 复算。
- [ ] 核对 secret scan、evidence manifest、21/22 分母、每个失败样本、usage/retries/429/tokens/cost/wall-clock和所有路径。
- [ ] 明确记录 smoke/regression、`paper_eligible=false`，且未运行 pilot、正式实验、Full、LeanAudit、force-all 或全量 Lean。
- [ ] 更新状态文档与 code map，运行 JSON/diff 检查和最终 Fast。

## 自审结论

- 授权覆盖：只含 Exp1–4 smoke；Exp5/pilot/formal/Full/Lean audit 均排除。
- 分母覆盖：21 direct + 1 supporting baseline = 22 actual。
- Identity 覆盖：command、start、profile/plan/catalog/budget/config/selection digest、endpoint、request limits、output root均在 provider 前持久化。
- 真值覆盖：只读持久化 evidence，不使用 capturing/synthetic/budget estimate补 actual；负面结果保留。
- 无占位符、无动态 selector、无模型或分母降级路径。

