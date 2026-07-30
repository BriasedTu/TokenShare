# Experiment 5 SiliconFlow 四模型 v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改写 Exp1–4 与 Exp5 v1/v2 evidence 的前提下，实现四个 SiliconFlow model-endpoint、Exp5 专属半量 hard-only selection、总并发 3、三次顺序轮换、逐 entry 预算，以及可直接进入论文的统计表图与文字产物。

**Architecture:** cohort/config/selection/smoke profile 全部新增 v3 immutable artifacts；policy 根据 cohort version 选择参数门禁，runner 根据 repeat-major 预注册顺序逐 arm 执行。底层 transport/identity 只增加通用 `thinking_budget` 与 usage provenance，Exp5 专属选择、预算、统计和论文 renderer 留在 experiment layer；历史 v1/v2 继续走原 reader 和 digest。

**Tech Stack:** Python 3.12、标准库、SQLite/JSON/JSONL/CSV、pytest；不新增运行时第三方依赖。SVG 由标准库模板生成；PDF 通过仓库可用的 LaTeX 工具链从同一冻结数据生成，工具不可用时 report fail closed，不用栅格截图代替。

---

日期：2026-07-29  
状态：Task 0–10 离线设施与独立启动入口已完成；Task 11 只有最小 endpoint/key 诊断，artifact-backed capability/8-root smoke 尚未运行，正式 preflight blocked  
对应设计：`Doc/TechnicalDocument/2026-07-29-feat-011-exp5-siliconflow-four-model-v3-design.md`  
决策：`Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md` 的 EPD-010 / EPD-011

## 当前完成状态（2026-07-30）

- [x] Task 0–6：运行现场保护、v3 artifacts、transport/identity、selection/sequence/concurrency、预算与 formal runner 接线完成。
- [x] Task 7：统计模块完成；定向验证 `55 passed`，独立 review PASS。
- [x] Task 8：CSV/JSONL/TeX/PDF/SVG/Markdown artifact renderer 与集成完成；`52 passed in 3.54s`，独立 review PASS。
- [x] Task 9：v3 综合与 Exp5-only smoke profile、drift/secret-safe config 门禁完成；`17 passed`，独立 review PASS。
- [x] Task 10：原 Exp5 定向离线集合 `464 passed in 231.56s`；MiniMax/bootstrap/key/profile/identity 最终影响集 `227 passed in 135.52s`，受影响 compileall 通过。Exp5-only identity 已冻结四 endpoint，而不是错误复用 Exp1 baseline。
- [ ] Task 11：四模型与 key 的五次最小诊断均 HTTP 200，MiniMax 的 nonthinking 请求仍返回 87 reasoning tokens；这些调用不产生正式 artifact-backed evidence。8-root Exp5 smoke 与正式矩阵未运行，正式 preflight 必须保持 blocked。

冻结计划事实：四模型 × 3 repeats × 4 slices=`48 conditions`，`1,284 roots`，`9,888 planned first-attempt AI units`，token ceiling=`607,518,720`；模型顺序为 `ABCD/BDAC/CADB`，全局并发 3、model arms 顺序执行。cohort/provider/semantic-selection/sequence digest 依次为 `sha256:1b317c9827d87c43b974b77c469b115906da463a79064d3ffb3b949dc5257942`、`sha256:6b1d6ffe977340ebbf59d69368d8c977fef664007575de72593c08c038d5ffe1`、`sha256:fbec153a02befa4071b4ad4d639e51e910a3bc4c433cc9eea4b19ac6489a3490`、`sha256:de9271732513d7d2ab20624d2949af3c9230b9adc2cd8db4b1f1ddb0d57b58cb`。Exp5-only smoke profile digest=`sha256:b44646d8b298200165abb73155304085c6800fab9ab363aa52483bd03556e61b`。

下方细粒度 checklist 保留原 TDD 执行脚本与历史 RED/GREEN 顺序；本节是当前完成状态源，不得据下方未回填的过程 checkbox 误判设施仍未实现。

## 文件结构

新建：

- `benchmarks/paper/model_comparison_cohort.v3.json`：四成员 immutable cohort。
- `benchmarks/paper/model_comparison_entry_map.v3.json`：四成员到同一 SiliconFlow config namespace 中四个 entry 的固定映射。
- `benchmarks/paper/exp5_siliconflow_provider_config.v3.json`：只含安全字段、四 entry controls 与官方 pricing snapshot；不含 key。
- `benchmarks/paper/exp5_hard_half_selection.v3.json`：107 个 case IDs、parent catalog digest 与 selection digest。
- `benchmarks/paper/paper_smoke_profile.v3.json`：保留其他实验 v2 selector，只把 Exp5 扩为四模型八 roots。
- `benchmarks/paper/paper_smoke_exp5_profile.v3.json`：只含 Experiment 5 的物理独立 8-root smoke 入口。
- `src/tokenshare/experiments/paper_exp5_statistics.py`：六模型对的配对统计、cluster bootstrap 与 Holm 校正。
- `src/tokenshare/experiments/paper_exp5_artifacts.py`：eligible metrics 到 LaTeX、SVG/PDF、结果摘要和失败附录的确定性 renderer。
- `tests/experiments/test_paper_exp5_statistics.py`、`tests/experiments/test_paper_exp5_artifacts.py`：新模块定向测试。

修改：

- `src/tokenshare/executors/ai_api_transport.py`、`src/tokenshare/executors/ai_api.py`：`thinking_budget` request identity 与 visible-output usage provenance。
- `src/tokenshare/experiments/paper_model_identity.py`、`paper_model_policy.py`：v3 reasoning identity、版本化 timeout/request controls、pricing gate。
- `src/tokenshare/experiments/paper_exp5_model_comparison.py`：v3 condition、顺序、半量 selection、并发和数量门禁。
- `src/tokenshare/experiments/paper_budget.py`、`run_paper_experiments.py`：逐 endpoint token ceiling 与 v3 plan identity。
- `src/tokenshare/experiments/paper_formal_metrics.py`、`paper_formal_report.py`：v3 分析输出和论文 renderer 接入；v2 文案保持原语义。
- `tests/executors/test_ai_api_transport.py`、相关 experiment tests：RED/GREEN 回归。
- `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`、权威设计、decision log、导航、`feature_list.json`、`progress.md`、`session-handoff.md`：实现证据与 code map。

## Task 0：保护运行现场与批准门禁

**Files:**

- Read: `progress.md`
- Read: `session-handoff.md`
- Read: `local/supervision/` 中当前 Exp1–4 smoke 监督记录
- Modify: none

- [ ] **Step 1：只读确认 Exp1–4 smoke 已退出**

运行：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'run_paper_experiments|run_exp1_exp4_v3_smoke|paper_smoke_exp1_exp4' } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

期望：没有仍可能从工作树导入 Python 的 runner/worker；若有结果，停止本计划，不改代码、不跑测试。

- [ ] **Step 2：确认 smoke evidence 已闭合或明确标为 incomplete**

用 `Get-Content -Encoding UTF8` 读取该 run 的 suite/experiment manifest、wrapper exit 与 final report。期望：状态、分母、最后写入时间和 runner 退出互相一致；本计划不修复或续跑该 evidence。

- [ ] **Step 3：确认用户批准范围**

批准文本必须覆盖：v3 `600/32768/0.0/1.0`、GLM/Qwen/MiniMax thinking 32K、DeepSeek non-thinking，以及 TDD 第 8 节强制输出。没有批准则只允许继续改设计/计划文档。

- [ ] **Step 4：保护 dirty worktree**

运行：

```powershell
git status --short
git diff --name-only
```

期望：记录所有用户变更；不 stash、不 reset、不从 HEAD 创建会遗漏未提交 Exp1–4 修改的 worktree。只有用户另行授权且当前变更已有安全基线时才创建 `codex/exp5-siliconflow-v3` 分支或提交。

## Task 1：冻结 cohort、entry map、provider config 与半量 selection

**Files:**

- Create: `benchmarks/paper/model_comparison_cohort.v3.json`
- Create: `benchmarks/paper/model_comparison_entry_map.v3.json`
- Create: `benchmarks/paper/exp5_siliconflow_provider_config.v3.json`
- Create: `benchmarks/paper/exp5_hard_half_selection.v3.json`
- Test: `tests/experiments/test_paper_exp5_model_comparison.py`
- Test: `tests/experiments/test_paper_model_policy.py`

- [ ] **Step 1：写 cohort/selection RED tests**

新增断言固定以下值：

```python
assert cohort["cohort_id"] == "tokenshare.paper.model_endpoint_cohort.v3"
assert [member["provider_model_id"] for member in cohort["members"]] == [
    "zai-org/GLM-5.2",
    "Qwen/Qwen3-14B",
    "MiniMaxAI/MiniMax-M2.5",
    "Pro/deepseek-ai/DeepSeek-V3",
]
assert selection["counts_by_stratum"] == {
    "factorization:hard": 83,
    "lean_proof:hard:pure_logic": 8,
    "lean_proof:hard:function_set": 8,
    "lean_proof:hard:induction": 8,
}
assert len(selection["ordered_case_ids"]) == 107
```

同时断言 v1/v2 文件字节与 digest 不变，Exp1–4 catalog case 数不变。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_model_comparison.py tests/experiments/test_paper_model_policy.py -q
```

期望：只因 v3 artifacts/reader 尚不存在或仍返回 v2 三成员而失败；不能有 import/fixture 错误。

- [ ] **Step 3：生成确定性 selection**

实现规则必须等价于：

```python
def select_case_ids(case_ids: list[str], retain_count: int) -> list[str]:
    ranked = sorted(
        case_ids,
        key=lambda case_id: (sha256(case_id.encode("utf-8")).hexdigest(), case_id),
    )
    return ranked[:retain_count]
```

对四个 stratum 分别执行，按固定 `factorization,pure_logic,function_set,induction` 顺序连接；artifact 保存 parent catalog digest、每层 source/retained count、case list 和 canonical SHA-256。不得删除或重排共享 catalog。

- [ ] **Step 4：写安全 config**

四 entry 公共字段固定 `provider=siliconflow`、`api_key_env=SILICONFLOW_API_KEY`、`stream=false`、`timeout_seconds=600`、`max_tokens=32768`、`temperature=0.0`、`top_p=1.0`、`max_provider_attempts=1`。GLM/Qwen/MiniMax overrides 为 `enable_thinking=true,thinking_budget=32768`；DeepSeek 为 `enable_thinking=false` 且没有 `thinking_budget`。

正式 config 的每个 pricing object 必须有以下完整结构：

```json
{
  "currency": "CNY",
  "input_per_million_tokens": 0.0,
  "output_per_million_tokens": 0.0,
  "source_url": "official SiliconFlow billing URL",
  "accessed_at": "ISO-8601 timestamp"
}
```

上面的 `0.0` 只表示 schema 示例，不得写入正式 artifact。实施时从官方计费面抄录真实非负值；若官方明确免费才允许 0。任一字段或精确 model id 对应关系无法审计时，不创建“已批准” config，preflight 返回 `pricing_snapshot_missing`。

- [ ] **Step 5：运行 GREEN 与 artifact digest 检查**

重复 Task 1 Step 2。期望：相关 v3 tests PASS；同一输入重复生成 selection byte-for-byte 相同；v1/v2 fixtures PASS。

## Task 2：为 SiliconFlow transport 与 identity 增加 `thinking_budget`

**Files:**

- Modify: `src/tokenshare/executors/ai_api_transport.py`
- Modify: `src/tokenshare/executors/ai_api.py`
- Modify: `src/tokenshare/experiments/paper_model_identity.py`
- Test: `tests/executors/test_ai_api_transport.py`
- Test: `tests/experiments/test_paper_model_identity.py`

- [ ] **Step 1：写 transport RED tests**

加入两个精确 body 测试：thinking entry 应发送 `enable_thinking=True,thinking_budget=32768,temperature=0.0,top_p=1.0,max_tokens=32768`；non-thinking entry 应发送 `enable_thinking=False` 且不存在 `thinking_budget`。加入无效值 `True/0/-1/1.5/"32768"` 拒绝测试。

- [ ] **Step 2：写 identity RED tests**

```python
identity = module.normalize_reasoning_identity(
    provider_family="siliconflow",
    request_overrides={"enable_thinking": True, "thinking_budget": 32768},
)
assert identity.effective_controls == {
    "enable_thinking": True,
    "thinking_budget": 32768,
}
```

断言 32768→16384 会改变 endpoint/prepared-config digest；`enable_thinking=False` 携带 budget 必须拒绝；v1/v2 无 budget identity 仍可加载。

- [ ] **Step 3：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_transport.py tests/experiments/test_paper_model_identity.py -q
```

期望：失败集中于 body 缺字段、allowlist/digest 不含字段或 validation 缺失。

- [ ] **Step 4：最小实现**

在 `build_siliconflow_chat_body()` 中加入：

```python
thinking_budget = entry.request_overrides.get("thinking_budget")
if thinking_budget is not None:
    if isinstance(thinking_budget, bool) or not isinstance(thinking_budget, int):
        raise ValueError("request_overrides.thinking_budget must be a positive integer")
    if thinking_budget < 1:
        raise ValueError("request_overrides.thinking_budget must be a positive integer")
    if thinking_override is not True:
        raise ValueError("thinking_budget requires enable_thinking=true")
    body["thinking_budget"] = thinking_budget
```

把 `thinking_budget` 加入 `_provider_request_identity()` 的 control/reasoning allowlist，并在 `normalize_reasoning_identity()` 中执行相同约束。不要把 Exp5 cohort 常量导入 executor。

- [ ] **Step 5：运行 GREEN**

重复 Task 2 Step 3。期望：全部 PASS，既有 JSON-mode 隐式关闭 Qwen 的历史测试仍 PASS。

## Task 3：实现 cohort-version-aware policy 与 whole-cohort preflight

**Files:**

- Modify: `src/tokenshare/experiments/paper_model_policy.py`
- Modify: `src/tokenshare/experiments/paper_model_identity.py`
- Test: `tests/experiments/test_paper_model_policy.py`

- [ ] **Step 1：写版本门禁 RED**

断言：v1/v2 仍接受并要求历史 `timeout=100,max_tokens=8192`；v3 精确要求 `timeout=600,max_tokens=32768,temperature=0.0,top_p=1.0,stream=false,max_provider_attempts=1`。v3 四成员共享公共字段，但 reasoning controls 按 entry 比较；任何成员缺 key/config/smoke/pricing 或漂移均在 provider dispatch 前整体 blocked。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_model_policy.py -q
```

期望：v3 schema unsupported 或仍按全局 100 秒门禁失败。

- [ ] **Step 3：加入不可变 definition**

使用 definition 数据而非覆盖共享常量：

```python
_COHORT_DEFINITIONS[V3_COHORT_ID] = {
    "schema_version": "tokenshare.paper_model_endpoint_cohort.v3",
    "member_ids": V3_MEMBER_IDS,
    "members": V3_MEMBERS,
    "comparable_fields": (
        "temperature", "top_p", "stream", "timeout_seconds",
        "max_tokens", "max_provider_attempts",
    ),
    "required_common_controls": {
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "timeout_seconds": 600,
        "max_tokens": 32768,
        "max_provider_attempts": 1,
    },
}
```

v1/v2 definition 保存各自 required controls；preflight 从选中 definition 读取，不再引用一个全局 Exp5 timeout 作为所有版本真值。

- [ ] **Step 4：运行 GREEN 与 zero-call 断言**

重复 Step 2；再运行 CLI plan-only 定向测试。期望：blocked fixtures 的 fake transport calls 仍为 0；v1/v2/v3 digest 分离。

## Task 4：实现 v3 repeat-major 条件、半量选择与并发证据

**Files:**

- Modify: `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- Modify: `src/tokenshare/experiments/paper_models.py`（仅在 order metadata 无法放入既有 condition identity 时）
- Test: `tests/experiments/test_paper_exp5_model_comparison.py`

- [ ] **Step 1：写 48-condition 顺序 RED**

固定顺序：

```python
expected_order = {
    0: ("glm_5_2_siliconflow", "qwen3_14b_siliconflow",
        "minimax_m2_5_siliconflow", "deepseek_v3_pro_siliconflow"),
    1: ("qwen3_14b_siliconflow", "deepseek_v3_pro_siliconflow",
        "glm_5_2_siliconflow", "minimax_m2_5_siliconflow"),
    2: ("minimax_m2_5_siliconflow", "glm_5_2_siliconflow",
        "deepseek_v3_pro_siliconflow", "qwen3_14b_siliconflow"),
}
```

每个 model-repeat 必须连续产生 Factorization、Lean pure_logic/function_set/induction 四 conditions，`worker_count==3`；总计 48。

- [ ] **Step 2：写 selection/数量 RED**

断言每个 model-repeat 都绑定相同 83/8/8/8 case IDs，root-runs=1,284、planned first-attempt units=9,888；换 selection digest、case order、parent catalog digest 或 condition order 都 fail closed。

- [ ] **Step 3：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_model_comparison.py -q
```

期望：旧 36/1,899/14,652 与 member-major 断言失败，新 v3 断言尚未满足。

- [ ] **Step 4：版本化实现**

保留 v2 reader/record validator；新增 v3 常量和按 `repeat -> member order -> slice` 展开。不要简单覆盖全部模块常量后让历史 fixture 失效。condition identity 写入 `order_slot`、`predecessor_member_id`、`sequence_plan_digest`；若扩 schema，则新建 `tokenshare.paper_condition.v3` 并保留 v2 reader。

- [ ] **Step 5：运行 GREEN**

重复 Step 3。期望：v3 数量/顺序 PASS，v2 历史 tests 继续 PASS。

## Task 5：逐 endpoint token/cost budget 与 CLI plan identity

**Files:**

- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_budget.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] **Step 1：写预算 RED**

构造四 endpoint 各 2,472 units，断言：

```python
assert plan["token_upper_bound_by_member"] == {
    "glm_5_2_siliconflow": 172_130_304,
    "qwen3_14b_siliconflow": 172_130_304,
    "minimax_m2_5_siliconflow": 172_130_304,
    "deepseek_v3_pro_siliconflow": 91_127_808,
}
assert plan["token_upper_bound"] == 607_518_720
```

断言旧 suite 不传 mapping 时仍使用 scalar `token_upper_bound_per_provider_attempt`；mapping 缺 member、额外 member、非正值或 digest 漂移均拒绝。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_budget.py tests/experiments/test_run_paper_experiments_cli.py -q
```

期望：旧 scalar 计算得到错误总量或新 mapping 参数尚不存在。

- [ ] **Step 3：扩展 plan API**

为 `plan_paper_suite()` 添加可选参数：

```python
token_upper_bound_by_endpoint_identity_digest: Mapping[str, int] | None = None
```

每个 condition 的 AI units 乘其 endpoint 上界；Exp5 v3 必须覆盖全部四 endpoint，其他 experiment 继续使用 scalar。budget identity 同时保存 mapping、pricing snapshot digests 和各 member token/cost subtotal。

- [ ] **Step 4：运行 GREEN**

重复 Step 2。期望：新旧预算 tests 全部 PASS；plan-only provider calls=0。

## Task 6：visible-output usage 与 provenance

**Files:**

- Modify: `src/tokenshare/executors/ai_api.py`
- Modify: `src/tokenshare/experiments/paper_exp5_model_comparison.py`
- Test: `tests/executors/test_ai_api_executor_success.py`
- Test: `tests/experiments/test_paper_exp5_model_comparison.py`

- [ ] **Step 1：写三分支 RED**

测试：reasoning 明细存在时 `visible=completion-reasoning`；显式 non-thinking 且无明细时 `visible=completion,basis=explicit_non_thinking`；thinking 且无明细时 `visible=None,basis=reasoning_breakdown_unavailable`。reasoning>completion 必须标记 `invalid_usage_breakdown`，不能返回负数。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/executors/test_ai_api_executor_success.py tests/experiments/test_paper_exp5_model_comparison.py -q
```

期望：usage summary 缺 `visible_output_tokens`/basis 或错误地把 unknown 当 0。

- [ ] **Step 3：实现纯函数**

```python
def _visible_output_usage(*, completion_tokens, reasoning_tokens, enable_thinking):
    if completion_tokens is None:
        return None, "completion_usage_unavailable"
    if reasoning_tokens is not None:
        if reasoning_tokens > completion_tokens:
            return None, "invalid_usage_breakdown"
        return completion_tokens - reasoning_tokens, "provider_reasoning_breakdown"
    if enable_thinking is False:
        return completion_tokens, "explicit_non_thinking"
    return None, "reasoning_breakdown_unavailable"
```

executor artifact、model execution record 和 CSV 都保存 value+basis；不保存 chain-of-thought。

- [ ] **Step 4：运行 GREEN**

重复 Step 2。期望：全部 PASS；usage missing/cost tests 不退化。

## Task 7：配对统计、Holm 校正与 order sensitivity

**Files:**

- Create: `src/tokenshare/experiments/paper_exp5_statistics.py`
- Create: `tests/experiments/test_paper_exp5_statistics.py`
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`

- [ ] **Step 1：写固定小样本 RED**

fixture 包含四模型、两个 case、三个 repeats，手算 completion/validity paired difference、median token difference、六个 pair 和 Holm adjusted p-value。加入 missingness、重复 join key、orphan row、非 eligible row 与 model order overlap 的 fail-closed tests。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_statistics.py tests/experiments/test_paper_formal_metrics.py -q
```

期望：新模块 import 失败或现有 metrics 不生成 v3 outputs。

- [ ] **Step 3：实现标准库统计**

固定 `case_id×repeat_id` 配对；cluster bootstrap 以 `case_id` 为抽样单位、seed 固定 5005、10,000 resamples；二元指标使用配对差与 exact sign-flip/permutation p-value，连续指标使用 paired median difference。Holm 实现必须等价于：

```python
ordered = sorted(enumerate(raw_p_values), key=lambda item: item[1])
running = 0.0
adjusted = [0.0] * len(raw_p_values)
for rank, (index, p_value) in enumerate(ordered):
    candidate = min(1.0, (len(ordered) - rank) * p_value)
    running = max(running, candidate)
    adjusted[index] = running
```

输出六个模型对、overall+四 domain/topic strata、分母/CI/method/raw/adjusted/effect direction；order sensitivity 只作 secondary summary。

- [ ] **Step 4：运行 GREEN**

重复 Step 2 两次。期望：全部 PASS，CSV bytes/digests 两次一致。

## Task 8：论文成品 renderer

**Files:**

- Create: `src/tokenshare/experiments/paper_exp5_artifacts.py`
- Create: `tests/experiments/test_paper_exp5_artifacts.py`
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Test: `tests/experiments/test_paper_formal_report.py`

- [ ] **Step 1：写输出契约 RED**

用固定 eligible metrics fixture 断言生成 TDD 第 8 节全部文件；LaTeX 对 `_ % & # { }` 转义；SVG 含四模型 label、轴标题和 CI；Markdown 中的数值能逐项回溯 CSV。blocked/ineligible/identity-incomplete 输入不得生成 paper 目录。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_exp5_artifacts.py tests/experiments/test_paper_formal_report.py -q
```

期望：renderer 尚不存在或现有 report 只列基础 CSV。

- [ ] **Step 3：实现 renderer**

renderer 只接收解析后的 eligible rows，不重新读取 raw reasoning。LaTeX 使用 `booktabs` 表；SVG 用固定 900×520 viewBox 和 stable model colors；PDF 从相同 plot data 生成。若 `pdflatex`/转换工具不可用，返回 `paper_artifact_render_failed` 并保留审计 CSV，不把缺 PDF 的 report 标为完成。

结果摘要模板只陈述实际数据：每个模型 completion/validity 与 CI、显著 pair、token/latency/cost、missingness、order sensitivity、same-provider serving-profile limitation；没有显著差异时明确写“未观察到足够证据”，不预写胜者。

- [ ] **Step 4：运行 GREEN 和确定性检查**

重复 Step 2 两次。期望：全部 PASS；CSV/TEX/SVG/MD hash 稳定，PDF 内容页数与标题可解析且不为空。

## Task 9：v3 smoke profile、CLI 顺序执行与全局并发 3

**Files:**

- Create: `benchmarks/paper/paper_smoke_profile.v3.json`
- Modify: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Test: `tests/experiments/test_paper_smoke_profile.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] **Step 1：写 8-root/并发 RED**

断言每模型恰好 1 Factorization hard + 1 Lean hard root，四模型共 8；`formal=false,pilot_only=true,regression_only=true,paper_eligible=false`。fake transport 记录 active calls，断言 peak≤3；不同 model arm 的 condition time window 不重叠；repeat 顺序与 Task 4 一致。

- [ ] **Step 2：运行 RED**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_smoke_profile.py tests/experiments/test_run_paper_experiments_cli.py -q
```

期望：v2 profile 仍只有 3 模型/6 roots 或 worker capacity 仍为 10。

- [ ] **Step 3：最小实现**

formal runner 继续按 canonical conditions 顺序执行；adapter capacity 取 v3 condition 的 `worker_count=3`；provider 共享 semaphore key 必须按 SiliconFlow config namespace 聚合，而不是按 model entry 分成四个各 3。运行证据写 observed peak、arm start/end、overlap violation 和 sequence digest。

- [ ] **Step 4：运行 GREEN**

重复 Step 2。期望：全部 PASS；fake transport provider calls 只来自测试 double，不调用真实 API。

## Task 10：文档、code map 与定向离线验证

**Files:**

- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `Doc/agent-navigation.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [ ] **Step 1：运行所有 Exp5 v3 定向 tests**

```powershell
conda run -n tokenshare python -m pytest `
  tests/executors/test_ai_api_transport.py `
  tests/executors/test_ai_api_executor_success.py `
  tests/experiments/test_paper_model_identity.py `
  tests/experiments/test_paper_model_policy.py `
  tests/experiments/test_paper_exp5_model_comparison.py `
  tests/experiments/test_paper_budget.py `
  tests/experiments/test_paper_exp5_statistics.py `
  tests/experiments/test_paper_exp5_artifacts.py `
  tests/experiments/test_paper_formal_metrics.py `
  tests/experiments/test_paper_formal_report.py `
  tests/experiments/test_paper_smoke_profile.py `
  tests/experiments/test_run_paper_experiments_cli.py -q
```

期望：全部 PASS；不要求本轮运行全量 suite。

- [ ] **Step 2：只编译受影响模块**

```powershell
conda run -n tokenshare python -m compileall -q `
  src/tokenshare/executors/ai_api_transport.py `
  src/tokenshare/executors/ai_api.py `
  src/tokenshare/experiments/paper_model_identity.py `
  src/tokenshare/experiments/paper_model_policy.py `
  src/tokenshare/experiments/paper_exp5_model_comparison.py `
  src/tokenshare/experiments/paper_budget.py `
  src/tokenshare/experiments/paper_exp5_statistics.py `
  src/tokenshare/experiments/paper_exp5_artifacts.py
```

期望：exit 0，无输出或只有正常 compile 路径。

- [ ] **Step 3：plan-only 与 secret scan**

运行 v3 plan-only，期望 `48/1284/9888/526516224`、provider calls=0、四 member identity 完整。扫描 tracked/new config/output 的 `Authorization`、`api_key` 明文和本地真实 key exact/fragment；期望 0 命中。只报告命中计数，不回显 secret。

- [ ] **Step 4：文档二次审核**

用 `Select-String -Encoding UTF8` 搜索旧“下一版 Exp5=三模型/100/8192/10 workers/1,899 roots/14,652 units/跨 provider”表述。历史段必须明确标 v1/v2；当前 v3 段必须唯一指向四模型、半量题库、600/32K、并发 3 和论文输出。

- [ ] **Step 5：边界声明**

本轮按用户要求不运行 `pytest tests`、`init.ps1 -Full`、LeanAudit、force-all Lean 或正式 Exp5 matrix。若 Fast `init.ps1` 也可能触碰活动 smoke，则不运行；只有 Task 0 确认完全退出后，用户没有禁止 Fast 且风险可接受时才运行默认 Fast。完成状态必须写明未做全量验证。

## Task 11：真实 capability smoke 与 8-root smoke（另需付费授权）

**Files:**

- Write only under a new gitignored `outputs/experiments/<new-exp5-v3-smoke-id>/`
- Modify tracked files only after审计发现实现 bug，且回到 RED/GREEN 流程

- [ ] **Step 1：获取独立授权**

向用户展示 model ids、4 capability calls、8 roots、并发 3、token/cost hard limits、output root 和计划/budget digests。用户没有明确批准真实付费调用时，停止在此处。

- [ ] **Step 2：四 entry capability smoke**

每 entry 使用无 benchmark 内容的固定 JSON prompt，验证 resolved model、请求 controls、finish reason、usage schema、thinking breakdown 和 600 秒 timeout path。任一 entry 不接受字段或 identity mismatch，则 whole cohort blocked，不降 token、不换 alias。

- [ ] **Step 3：运行 8-root pilot-only smoke**

只使用 v3 smoke profile；模型 arm 顺序执行；全局 peak≤3。不得把 smoke row 写入 formal paper table。

- [ ] **Step 4：审计输出**

确认 8 个 roots 分母未缩减、provider attempts/usage/cost/latency/identity 完整、paper eligibility 全 false、secret scan 0 命中。生成审计 CSV/JSONL 和预览 paper artifacts，但正式报告标 `pilot_only`。

- [ ] **Step 5：停在正式矩阵之前**

重新生成 plan-only 与 hard limits，向用户报告 smoke 结果和预计正式消耗。启动 1,284-root 正式 Exp5 必须再次获得明确授权；本计划不自动继续。

## 自审结果

- Spec coverage：四模型、EPD-010 selection、3 repeats 顺序、并发 3、参数、thinking semantics、版本兼容、逐 entry 预算、usage、统计、论文输出、smoke 与正式授权均有对应 Task。
- Placeholder scan：没有未定义的实施占位；唯一动态数据是官方 pricing snapshot，计划规定了精确来源、schema 和缺失时 fail-closed 行为，不允许猜值。
- Type consistency：`cohort_member_id`、`model_endpoint_identity_digest`、`thinking_budget`、`visible_output_tokens` 与既有模型/attempt identity 命名保持一致；逐 endpoint budget mapping 以 endpoint identity digest 为键，避免 entry id 跨 config namespace 冲突。
- Safety：Task 0 是硬门；当前 Exp1–4 smoke 未闭合时不得执行 Task 1 之后任何代码或测试。真实 API 只在 Task 11 单独授权后发生，正式矩阵不在本实施计划的自动执行范围。
