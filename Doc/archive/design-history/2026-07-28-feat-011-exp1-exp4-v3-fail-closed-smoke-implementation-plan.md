# Exp1–4 v3 Fail-Closed 与真实 Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任何 Exp1–4-only smoke 在真实 provider dispatch 前同时锁定显式 v3 路径、冻结 config digest 和完整 DeepSeek 请求语义；验证通过后启动既有 21-direct/22-actual-root smoke，并监督到明确终态。

**Architecture:** 保留 v2 与 Exp5 cohort v2 原样。CLI 在解析 Exp1–4-only smoke profile 后先验证命令显式提供 tracked v3 path；在构造实际 executor config 后再验证 pinned digest、provider/entry/model/endpoint、thinking/high、`600/300000`、单 attempt、stream false 和 inflight 50。任一不一致写 smoke structured blocked manifest，且不进入 `execute_paper_smoke_suite`。

**Tech Stack:** Python 3.12、pytest、PowerShell、JSON/JSONL、SQLite/event ledger、DeepSeek Chat Completions。

---

## File map

- Modify `src/tokenshare/experiments/run_paper_experiments.py`: Exp1–4-only smoke 的显式 v3 path 与实际 execution-config pre-dispatch gate。
- Modify `tests/experiments/test_run_paper_experiments_cli.py`: v3 GREEN、显式 v2 RED、字段漂移 RED、structured blocked 与零 dispatch/call 证明。
- Modify `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`: 当前 Exp1–4 v3 CLI 和 fail-closed 命令。
- Modify `README.md`: 当前 Exp1–4-only smoke 命令。
- Modify affected code maps plus `progress.md`, `feature_list.json`, `session-handoff.md`: 实现、验证和运行终态证据。
- Runtime only `outputs/experiments/<new-smoke-root>/`: 新 launch/run/generation/checkpoint、ledger、attempt、artifact、usage 和报告；不得覆盖旧 evidence。

当前 `main` 工作树已有用户前置实现与历史 evidence。本计划按既有仓库决策直接在该工作树增量执行，不 stash/reset/clean/checkout，不 stage/commit/push。

### Task 1: RED — 冻结 Exp1–4-only smoke v3 CLI 契约

**Files:**
- Modify: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] **Step 1: 更新成功路径，要求显式 v3**

在 `test_exp1_exp4_smoke_does_not_require_or_register_exp5_cohort` 的 argv 中加入：

```python
"--ai-api-config",
"benchmarks/paper/exp1_baseline_provider_config.v3.json",
```

并继续断言 launch manifest 为 DeepSeek official、`deepseek-v4-pro`、thinking enabled/high、`timeout_seconds=600`、`max_tokens=300000`、单 attempt、21 direct / 22 actual、`paper_eligible=false`。

- [ ] **Step 2: 新增显式 v2 拒绝测试**

调用同一 Exp1–4-only profile，但传：

```python
"--ai-api-config",
"benchmarks/paper/exp1_baseline_provider_config.v2.json",
```

断言 CLI exit code 为 3，`suite_manifest.json.status == "blocked"`，failure kind 为稳定的 v3 config gate 类型，`provider_attempt_count == 0`，capturing transport 与 `execute_paper_smoke_suite` 调用计数均为 0。

- [ ] **Step 3: 新增逐字段漂移参数化测试**

从 tracked v3 JSON 深复制后，逐项只改一个字段：

```python
("timeout_seconds", 599)
("max_tokens", 299999)
("model", "deepseek-v4-pro-drift")
("endpoint", "/v1/chat/completions")
("thinking", {"type": "disabled"})
("reasoning_effort", "medium")
```

另覆盖 `provider_family` 漂移。把每个变体加载为 supplied execution config；命令仍显式指向 tracked v3。每个 case 都必须在 `execute_paper_smoke_suite` 前 structured blocked，并断言 provider/transport call 数为 0。

- [ ] **Step 4: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_run_paper_experiments_cli.py -k "exp1_exp4_smoke" -q
```

Expected: 新增拒绝测试因当前 CLI 忽略显式 v2 或未把 execution-config 漂移持久化为 structured blocked 而失败；成功路径继续通过。语法、import、fixture 错误不算有效 RED。

### Task 2: GREEN — 实现双层 pre-dispatch gate

**Files:**
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`

- [ ] **Step 1: 冻结 v3 authority**

新增 Exp1–4-only experiment id tuple、tracked v3 path、当前 config digest 和精确语义常量。语义至少包含：

```python
{
    "provider_family": "deepseek",
    "entry_id": "deepseek_v4_pro_exp1_baseline",
    "model": "deepseek-v4-pro",
    "base_url": "https://api.deepseek.com",
    "endpoint": "/chat/completions",
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
    "timeout_seconds": 600,
    "max_tokens": 300_000,
    "max_provider_attempts": 1,
    "stream": False,
    "max_in_flight_global": 50,
}
```

- [ ] **Step 2: 校验显式路径**

仅当 profile experiment ids 精确等于 Exp1–4 时，要求原始 argv 明确出现 `--ai-api-config`（含 argparse 支持的等号形式）且解析路径与 tracked v3 相同。缺失、v2 或其他路径均调用 `_write_smoke_blocked_suite(... failure_kind="exp1_exp4_v3_config_blocked")` 后 return 3。

- [ ] **Step 3: 校验实际 execution config**

在 `execute_paper_smoke_suite` 之前，对 `execution_configs["exp1_baseline_deepseek"]` 验证 pinned digest 和上述精确语义；不接受额外 enabled entry、temperature/top_p、endpoint alias 或不同 inflight。任一异常同样写 structured blocked，provider attempts/tokens/cost 为 0。

- [ ] **Step 4: 运行 GREEN**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_run_paper_experiments_cli.py -k "exp1_exp4_smoke" -q
```

Expected: 全部 PASS。

### Task 3: 回归与文档同步

**Files:**
- Modify: authority/README/code maps/status files from the file map.

- [ ] **Step 1: 运行 CLI/config/selection/budget 定向集合**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_deepseek_runner_migration.py tests/experiments/test_paper_smoke.py tests/experiments/test_run_paper_experiments_cli.py tests/experiments/test_paper_budget.py tests/experiments/test_paper_execution_runner.py tests/experiments/test_paper_gate_c_dispatcher.py -q
```

- [ ] **Step 2: 同步文档并二次扫描**

把权威设计和 README 的当前 Exp1–4 命令改成显式 v3；保留的 v2/8192 只能明确属于历史 replay 或 Exp5。同步 code map、`progress.md`、`feature_list.json`、`session-handoff.md`。

- [ ] **Step 3: 仅运行 Fast**

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

exit code 必须为 0，pytest summary 不含 `FAILED`。本轮依用户明确授权只运行轻量定向测试和 Fast，不运行 Full、全量 pytest、LeanAudit、force-all 或全量 Lean。

### Task 4: 启动、监督与最终报告

**Files:**
- Create runtime evidence only under a new `outputs/experiments/` root.

- [ ] **Step 1: pre-call identity**

在 provider 前持久化 UTC/本地时间、完整命令、Git commit 与 commit-less diff、suite/run/generation、v3 path/config digest、profile/selection/catalog/budget digests、DeepSeek endpoint/controls、workers/inflight、request/retry、21 direct/22 actual denominator、output root 和全部未运行范围。

- [ ] **Step 2: 显式 v3 启动**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --output-root <new-output-root> `
  --smoke-profile benchmarks/paper/paper_smoke_exp1_exp4_profile.v2.json `
  --ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json `
  --real-transport `
  --unlimited-budget
```

API key 只从已有本地 secret / 进程环境取得，不出现在命令、日志、event、artifact、报告、tracked 文件或回复。

- [ ] **Step 3: 每 20 分钟只读监督**

启动后立即写初始快照；以后每 20 分钟记录进程/worker、suite/run/generation/checkpoint、expected/started/completed/failed/blocked、calls/retries/429、provider usage、usage missing、估算/实际费用、latest ledger event、attempt/artifact/checkpoint/last activity 和 limit 状态，直到 succeeded/failed/blocked/incomplete。

- [ ] **Step 4: 最终交付**

从 actual evidence 生成最终 smoke/regression 报告；失败样本、缺失 usage/计费和未解析 evidence 保持真实。明确 `paper_eligible=false`，未运行 Exp5、pilot、正式实验、Full 实验矩阵、LeanAudit、force-all 或全量 Lean。

## Self-review

- Spec coverage：路径、digest、全部字段、zero dispatch、structured blocked、验证、启动、20 分钟监督和最终报告均有对应任务。
- Placeholder scan：命令中 `<new-output-root>` 只在运行时替换为启动前生成并持久化的唯一 UTC identity，不代表未决设计；生产字段和测试断言无 TODO/TBD。
- Type consistency：v3 `600/300000/1/50` 与 Exp5 `100/8192` 分离；21 direct / 22 actual 分母与既有 profile/worker-death supporting baseline 一致。
- Scope：不改 v3 实验参数、Exp5、catalog、selection、repeat、retry、题目、parser/checker、代理/网络/transport。

## 2026-07-28 run-instance identity 门禁修复补充

根因不是 runner digest 计算错误：`execution_plan_digest` 包含实际 `output_root`，`budget_digest` 也绑定 `output_identity.output_root`。旧监督 prompt 错把 run01 的这两个运行实例 digest 当成了跨 run 固定值，run02 因而正确 fail closed；run02 永久保留为 blocked，不得恢复或复用。

修复按以下顺序执行：

1. RED：新增跨 root identity 回归，证明新 root 只允许 execution-plan/budget digest 改变；新增 launcher 静态门禁；复用既有 same-root resume 漂移断言证明仍 fail closed。
2. GREEN：runner 增加不创建 output root、不读取 secret 的 `--smoke-identity-only`；输出跨 run `preregistered_semantics` 与当前 root 的 `run_instance_identity`。launcher 先核对稳定语义，再把动态生成的两个 digest 作为 expected 值传给真实 runner。
3. 验证：只运行相关轻量 pytest 和 `init.ps1` Fast；不运行 Full、全量 pytest、LeanAudit、force-all 或全量 Lean。
4. 启动：修复验证通过后仅使用全新的 `run03` identity；绝不复用 run02，也不修改历史 evidence。
