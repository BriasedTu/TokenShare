# DeepSeek Exp1–4 600 秒 / 300K 重新预注册 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Experiment 1–4 官方 DeepSeek 正式请求控制版本化重新预注册为 `timeout_seconds=600`、`max_tokens=300000`，保持 Experiment 5 v2 为 `100/8192`，并重跑同一 hard×10 / concurrency=10 真实 API 诊断。

**Architecture:** 保留已用于历史 evidence 与 Exp5 的 v2 文件，新建 v3 baseline/profile，并用 Exp1–4 专用常量把新上限与 Exp5 公共控制隔离。所有 formal preflight 继续 fail closed；真实诊断先 prepare 持久化 digest/command，再 execute，且每题只有一次 provider attempt。

**Tech Stack:** Python 3.12、pytest、PowerShell、SQLite/JSON/JSONL、本地 `UrlLibDeepSeekTransport`、DeepSeek OpenAI-compatible Chat Completions API。

---

## File map

- Create `benchmarks/paper/exp1_baseline_provider_config.v3.json`: Exp1–4 DeepSeek v3 provider identity 与默认请求控制。
- Create `benchmarks/paper/exp1_minimal_pilot_profile.v3.json`: v3 pilot/profile request policy 与 provider config binding。
- Modify `src/tokenshare/experiments/paper_models.py`: 分离 Exp1–4 DeepSeek 与 Exp5 timeout/max-token 常量。
- Modify `src/tokenshare/experiments/paper_exp1.py`: Exp1 固定 request controls 切换到 v3 常量。
- Modify `src/tokenshare/experiments/paper_exp2_scalability.py`: Exp2 固定 request controls 切换到 v3 常量。
- Modify `src/tokenshare/experiments/paper_exp3_fault_recovery.py`: Exp3 固定 request controls 切换到 v3 常量；worker-death guard 自动变为 630 秒。
- Modify `src/tokenshare/experiments/paper_exp4_ablation_runner.py`: Exp4 固定 request controls 切换到 v3 常量。
- Modify `src/tokenshare/experiments/run_paper_experiments.py`: Exp1–4 默认 profile/config 指向 v3，预算 token upper bound 覆盖 300K。
- Modify `tests/experiments/test_deepseek_runner_migration.py`: v3 当前行为、v2/Exp5 不变性和 digest 敏感性测试。
- Modify affected Exp1–4/CLI tests: 将只属于 Exp1–4 的旧 `100/8192` fixture/assertion 更新为 `600/300000`；Exp5 fixture 保持旧值。
- Modify `local/_deepseek_hard10_diagnostic.py`: diagnostic 绑定 v3 与 `600/300000`，不改变题目/prompt/parser/checker/attempts/concurrency。
- Modify authoritative docs/status/code maps listed in Task 4.

由于当前 `main` 工作树已经包含用户的未提交 feat-011 前置实现，本计划不执行 git commit/stash/reset；每个任务以测试和 `git diff -- <scoped files>` 作为检查点。

### Task 1: RED — 冻结 v3 与 Exp5 不变性契约

**Files:**
- Modify: `tests/experiments/test_deepseek_runner_migration.py`
- Test: `tests/experiments/test_deepseek_runner_migration.py`

- [ ] **Step 1: 先写失败测试**

把 Exp1–4 预期控制改为：

```python
assert paper_exp1.EXP1_FORMAL_REQUEST_CONTROLS == {
    "max_tokens": 300_000,
    "timeout_seconds": 600,
    "max_provider_attempts": 1,
    "stream": False,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
}
```

新增 v3 文件常量和测试：

```python
BASELINE_V3 = ROOT / "benchmarks/paper/exp1_baseline_provider_config.v3.json"
PROFILE_V3 = ROOT / "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"

def test_baseline_v3_reregisters_exp1_to_exp4_without_mutating_exp5_v2() -> None:
    body = _read_json(BASELINE_V3)
    config = load_ai_api_config(body)
    assert config.defaults["timeout_seconds"] == 600
    assert config.defaults["max_tokens"] == 300_000
    assert _read_json(PROFILE_V3)["baseline_provider"]["provider_config_path"] == (
        "exp1_baseline_provider_config.v3.json"
    )
    assert all(
        member["max_tokens"] == 8192
        for member in _read_json(COHORT_V2)["members"]
    )
```

扩充历史保护测试，记录 v2 config digest 与 cohort v2 digest，并确认改变 v3 的 timeout/max_tokens 会改变 config digest。

- [ ] **Step 2: 运行 RED 并确认因 v3/新常量尚不存在而失败**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_deepseek_runner_migration.py -q
```

Expected: FAIL，原因是 `BASELINE_V3`/`PROFILE_V3` 文件不存在，且 Exp1–4 控制仍为 `100/8192`；不得接受语法错误或无关 import error 作为 RED。

### Task 2: GREEN — 创建版本化配置并隔离 Exp1–4/Exp5 常量

**Files:**
- Create: `benchmarks/paper/exp1_baseline_provider_config.v3.json`
- Create: `benchmarks/paper/exp1_minimal_pilot_profile.v3.json`
- Modify: `src/tokenshare/experiments/paper_models.py`
- Modify: `src/tokenshare/experiments/paper_exp1.py`
- Modify: `src/tokenshare/experiments/paper_exp2_scalability.py`
- Modify: `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- Modify: `src/tokenshare/experiments/paper_exp4_ablation_runner.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_deepseek_runner_migration.py`

- [ ] **Step 1: 新建 v3 provider/profile**

复制 v2 的模型、endpoint、thinking、pricing、secret policy 和并发控制，只改变并版本化以下字段：

```json
{
  "defaults": {
    "timeout_seconds": 600,
    "max_tokens": 300000,
    "stream": false,
    "max_provider_attempts": 1
  },
  "metadata": {
    "approval_snapshot": "feat_011_deepseek_v4_pro_300k_reregistration_2026_07_28"
  }
}
```

profile v3 的两个 domain limits 均使用：

```json
{"timeout_seconds": 600, "max_tokens": 300000}
```

- [ ] **Step 2: 建立专用常量**

在 `paper_models.py` 保留 Exp5 的旧 timeout，并新增：

```python
PAPER_FORMAL_AI_TIMEOUT_SECONDS = 100
EXP1_TO_EXP4_DEEPSEEK_TIMEOUT_SECONDS = 600
EXP1_TO_EXP4_DEEPSEEK_MAX_TOKENS = 300_000
```

Exp1–4 四个模块的固定 policy 均使用后两个常量，不改变 `stream/thinking/reasoning_effort/max_provider_attempts`。

- [ ] **Step 3: 切换 Exp1–4 CLI 默认文件**

在 `run_paper_experiments.py` 使用：

```python
DEFAULT_EXP1_PILOT_PROFILE = Path(
    "benchmarks/paper/exp1_minimal_pilot_profile.v3.json"
)
```

并把 `--ai-api-config` 默认值改为 `benchmarks/paper/exp1_baseline_provider_config.v3.json`。`model_comparison_cohort.v2.json` 及其 member map 不变。

- [ ] **Step 4: 运行 GREEN**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_deepseek_runner_migration.py -q
```

Expected: 全部 PASS。

### Task 3: RED/GREEN — 更新所有 Exp1–4 fail-closed 契约

**Files:**
- Modify only affected files under `tests/experiments/`
- Test: Exp1–4、formal runner、CLI、smoke tests

- [ ] **Step 1: 运行定向集合，收集旧控制造成的真实失败**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/experiments/test_paper_exp1_formal.py `
  tests/experiments/test_paper_exp2_scalability.py `
  tests/experiments/test_paper_exp3_fault_recovery.py `
  tests/experiments/test_paper_exp4_ablation_runner.py `
  tests/experiments/test_paper_execution_runner.py `
  tests/experiments/test_run_paper_experiments_cli.py `
  tests/experiments/test_paper_smoke.py -q
```

Expected: 仅旧 `100/8192` fixture/assertion 与 v2 默认路径相关的测试失败；先保存完整 RED summary。

- [ ] **Step 2: 最小更新 Exp1–4 fixture/assertion**

只将绑定 Exp1–4 baseline 的 literal 更新为：

```python
{"max_tokens": 300_000, "timeout_seconds": 600}
```

任何 Exp5 helper/member/comparable-controls fixture 继续为：

```python
{"max_tokens": 8192, "timeout_seconds": 100}
```

worker-death process guard assertion若存在，应从 130 更新为 630，因为 guard 仍按 provider timeout + 30 秒派生。

- [ ] **Step 3: 重跑定向集合**

Run: 与 Step 1 相同。

Expected: 全部 PASS，且 Exp5 不变性测试仍通过。

### Task 4: 同步权威设计、来源、状态和 code map

**Files:**
- Modify: `AGENTS.md`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- Modify: `Doc/agent-navigation.md`
- Modify: `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `README.md`
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`

- [ ] **Step 1: 更新权威口径**

写明 Exp1–4 v3=`600/300000`，Exp5 cohort v2=`100/8192`；禁止把“300K DeepSeek 重新预注册”扩展成 GLM/GPT 控制。

- [ ] **Step 2: 更新决策日志与在线来源索引**

记录 2026-07-28 决策、官方 1M context/384K max output、600 秒 keepalive/服务端边界，以及该资料只影响 Exp1–4 DeepSeek。

- [ ] **Step 3: 二次检索旧表述**

Run:

```powershell
Get-ChildItem AGENTS.md,README.md,Doc,src,tests,benchmarks -Recurse -File `
  -Include *.md,*.py,*.json | Where-Object { $_.FullName -notmatch '__pycache__' } | `
  Select-String -Pattern 'Exp1.{0,40}8192|Experiment 1.{0,40}8192|timeout_seconds=100|max_tokens=8192' -Encoding UTF8
```

Expected: 剩余 `100/8192` 只属于 v1/v2 历史、Exp5 或明确的 smoke history，不存在把它描述为当前 Exp1–4 正式控制的旧表述。

### Task 5: 验证门禁

**Files:**
- No production edits unless a test exposes an in-scope regression.

- [ ] **Step 1: 运行 DeepSeek/Exp1–4 定向测试**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_deepseek_transport.py tests/experiments/test_deepseek_runner_migration.py tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_exp4_ablation_runner.py tests/experiments/test_run_paper_experiments_cli.py tests/experiments/test_paper_smoke.py -q
```

Expected: 0 failed。

- [ ] **Step 2: 运行 Fast**

```powershell
.\init.ps1
```

Expected: exit 0，pytest summary 不含 `FAILED`。

- [ ] **Step 3: 运行 Full**

```powershell
.\init.ps1 -Full
```

Expected: exit 0，pytest summary 不含 `FAILED`。本次不修改 Lean checker/catalog/toolchain/helper，因此不运行 LeanAudit。

### Task 6: prepare 并执行 hard×10 / concurrency=10 真实诊断

**Files:**
- Modify: `local/_deepseek_hard10_diagnostic.py`
- Create: `outputs/diagnostics/deepseek_hard10_concurrency10_direct_300k_<UTC timestamp>/...`

- [ ] **Step 1: 绑定 v3 与新控制**

```python
CONFIG_PATH = ROOT / "benchmarks/paper/exp1_baseline_provider_config.v3.json"
TIMEOUT_SECONDS = 600
MAX_TOKENS = 300_000
EXPECTED_CALLS = 10
CONCURRENCY = 10
```

不得改变 hard case selection、prompt builder、JSON response format、parser、checker 或 attempt count。

- [ ] **Step 2: 运行 prepare，尚不加载 key、不调用 API**

```powershell
conda run -n tokenshare python local/_deepseek_hard10_diagnostic.py --prepare --output-directory <new-output-directory>
```

Expected: 新目录包含 launch manifest、十个 prompt/request artifact、config digest、selection digest、command、endpoint、`600/300000` 和 `expected_calls=10`。

- [ ] **Step 3: secret/proxy/manifest preflight**

只输出布尔值确认 User-scope `DEEPSEEK_API_KEY` 存在；确认 process/WinINet proxy 均为空；扫描 prepare artifacts 不含 key。若任一失败，写 structured blocked，不调用 provider。

- [ ] **Step 4: execute 恰好十次调用**

```powershell
$env:DEEPSEEK_API_KEY=[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
conda run -n tokenshare python local/_deepseek_hard10_diagnostic.py --execute --output-directory <same-output-directory>
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
```

Expected: 进程自然结束；不自动 retry，不覆盖旧输出。若运行超过 10 分钟，按监督规则报告实时状态，不伪造进度。

- [ ] **Step 5: 生成并核验最终汇总**

核对 expected/executed/completed/failed/blocked、每题 finish reason/阶段、provider calls、retries、429、provider-reported usage、usage-missing attempts、静态 cost estimate、wall-clock、artifact paths 和 secret scan。明确 `paper_eligible=false`，没有运行 pilot、正式 Experiment 1–5、Full matrix 或 LeanAudit。

## Self-review

- Spec coverage：Tasks 1–3 覆盖版本化配置、隔离常量和 fail-closed 行为；Task 4 覆盖权威文档/外部来源/status/code map；Task 5 覆盖门禁；Task 6 覆盖真实诊断与最终 evidence。
- Placeholder scan：无 TBD/TODO/“类似上一步”等占位表达；所有命令、路径和关键值均已列出。
- Type consistency：`timeout_seconds` 与 `max_tokens` 均保持正整数；request control key 集合不变；Exp1–4 v3 与 Exp5 v2 使用不同常量/文件，不复用会漂移的公共默认值。
- Scope check：不修改题库、prompt/parser/checker、repeat/worker/retry、GLM/GPT 或 Exp5 cohort digest；不删除/覆盖旧 run 与 v2 配置。
