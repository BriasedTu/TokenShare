# Feat-011 Formal AI Timeout 100s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将正式 Experiment 1–5 的单次 AI API 请求超时从 30 秒统一改为 100 秒，并保持其他执行器、协议租约和 Lean checker 超时不变。

**Architecture:** 在共享 paper model 常量中冻结正式 AI timeout，Exp1–4、正式 CLI、Gate C context 和 Exp5 preflight 共同引用该常量。tracked baseline/pilot JSON 同步为 100；Exp5 任一端点不是 100 时 fail closed，防止 plan-only 预算与真实执行配置漂移。现有 worker-death 进程保护公式继续使用 `request timeout + 30s`，因此正式配置下派生为 130 秒，不另设第二套常量。

**Tech Stack:** Python 3、pytest、JSON、PowerShell、TokenShare paper experiment modules。

---

### Task 1: 建立 100 秒正式契约的 RED 证据

**Files:**
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`
- Modify: `tests/experiments/test_paper_model_policy.py`

- [x] **Step 1: 让 plan-only 测试要求 100 秒**

在 `test_paper_cli_plan_only_writes_budget_and_suite_manifest()` 中增加：

```python
assert budget["quota_preflight"]["budget_commitments"]["request_limits"][
    "timeout_seconds"
] == 100
```

- [x] **Step 2: 让 Exp5 preflight 测试要求公共 timeout 为 100 秒**

把正式 Exp5 provider fixture 的默认 timeout 和公共 snapshot 断言改为 100，并增加三端点均漂移回 30 秒时的 fail-closed 断言：

```python
assert common["timeout_seconds"] == 100
assert all(
    "formal_ai_timeout_seconds_mismatch" in member["blocked_reasons"]
    for member in blocked["member_plans"].values()
)
```

- [x] **Step 3: 运行测试并确认按预期失败**

Run:

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_run_paper_experiments_cli.py::test_paper_cli_plan_only_writes_budget_and_suite_manifest tests/experiments/test_paper_model_policy.py::test_exp5_preflight_normalizes_and_compares_public_request_controls -q
```

Expected: FAIL；旧实现仍输出 30 秒或不拒绝三端点共同漂移到 30 秒。

### Task 2: 实现正式 100 秒 timeout 并保持边界

**Files:**
- Modify: `src/tokenshare/experiments/paper_models.py`
- Modify: `src/tokenshare/experiments/paper_exp1.py`
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Modify: `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- Modify: `src/tokenshare/experiments/paper_model_policy.py`
- Modify: `src/tokenshare/experiments/paper_runner.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `benchmarks/paper/exp1_baseline_provider_config.v1.json`
- Modify: `benchmarks/paper/exp1_minimal_pilot_profile.v1.json`

- [x] **Step 1: 冻结共享正式值**

在 `paper_models.py` 增加：

```python
PAPER_FORMAL_AI_TIMEOUT_SECONDS = 100
```

- [x] **Step 2: 替换所有正式 Experiment 1–4、CLI 和 Gate C 默认值**

这些路径的 `request_limits/request_controls["timeout_seconds"]` 必须引用 `PAPER_FORMAL_AI_TIMEOUT_SECONDS`；不修改 `AIAPIExecutor` 的通用 30 秒 fallback、adapter 通用默认、`ProtocolConfig` 的 300 秒 lease、Lean payload/checker 的 30 秒资源限制或旧 Factorization 500 benchmark 的 60 秒 timeout。

- [x] **Step 3: Exp5 对 100 秒 fail closed**

在公共 controls 归一化后加入：

```python
if request_controls["comparable"]["timeout_seconds"] != PAPER_FORMAL_AI_TIMEOUT_SECONDS:
    blocked_reasons.append("formal_ai_timeout_seconds_mismatch")
```

- [x] **Step 4: 同步 tracked baseline/pilot JSON**

把 `exp1_baseline_provider_config.v1.json` 与 `exp1_minimal_pilot_profile.v1.json` 中属于 provider request controls 的 timeout 改为 100；不修改 `lean_checker_preflight.v1.json`。

- [x] **Step 5: 运行 RED 节点并确认转绿**

Run: Task 1 Step 3 的同一命令。

Expected: `2 passed`。

### Task 3: 同步正式测试夹具和参数文档

**Files:**
- Modify: `tests/experiments/test_paper_exp1_formal.py`
- Modify: `tests/experiments/test_paper_exp2_scalability.py`
- Modify: `tests/experiments/test_paper_exp3_fault_recovery.py`
- Modify: `tests/experiments/test_paper_exp4_ablation_runner.py`
- Modify: `tests/experiments/test_paper_exp5_model_comparison.py`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- Modify: `Doc/TechnicalDocument/2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`

- [x] **Step 1: 只更新正式 baseline/cohort 测试夹具**

把代表当前正式 Exp1–5 request controls 的测试值改为 100；保留用于验证任意 config 透传、漂移拒绝、Lean checker 或旧 suite 的 30 秒测试值。

- [x] **Step 2: 追加 EPD-007 并同步唯一权威**

记录用户确认的 `30 -> 100`、进程保护派生 130 秒、digest/预算失效边界及不受影响范围。旧预算审批和旧 provider/profile digest 不得复用。

- [x] **Step 3: 运行 timeout 影响测试集**

Run:

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_exp4_ablation_runner.py tests/experiments/test_paper_exp5_model_comparison.py tests/experiments/test_paper_model_policy.py tests/experiments/test_run_paper_experiments_cli.py -q
```

Expected: 全部 PASS；provider calls 为 0。

### Task 4: 状态、code map 与最终验证

**Files:**
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [x] **Step 1: 更新 code map 和 feat-011 状态证据**

记录正式 timeout 100 秒、Exp5 fail-closed、130 秒派生保护、未修改的边界和验证命令；`feat-011` 仍保持 `in-progress`，因为真实论文实验尚未执行。

- [x] **Step 2: 运行静态审计**

Run:

```powershell
Get-Content -LiteralPath feature_list.json -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
git diff --check
```

Expected: JSON 可解析，`git diff --check` 无 whitespace error。

- [x] **Step 3: 记录 Full 边界与 Gate C 诊断**

本次是 feat-011 内部参数变更，不是 feature 完成；原 autonomous prompt 也未授权 Full。诊断性加入整个旧 Gate C 文件后，timeout 校验已通过，后续暴露 7 条 Task A–11 旧矩阵/catalog 断言漂移。该旧漂移记录到 progress/handoff，留待 feat-011 完成前修复并运行 Full；不在 timeout 变更中扩张范围。没有 Lean 调用链或 tracked Lean input 变化，因此不运行 LeanAudit。

- [x] **Step 4: 回填最终证据并复验 Fast**

Run:

```powershell
.\init.ps1
```

Expected: Fast PASS；把 fresh 输出写入 `feature_list.json`、`progress.md` 和 `session-handoff.md`。

本计划不包含 commit、stage、push、PR、真实 provider 调用或正式实验执行。
