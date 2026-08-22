# Factorization Prompt V2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 Factorization 输出 schema、parser、verifier、任务切分或下游 trace 合同的前提下，减少 prompt 冗余，并降低目标数漂移、未经完备搜索即返回 `no_factor_in_range` 和长篇逐个试除的概率。

**Architecture:** 仅修改共享 Factorization plugin 已有的 `_prompt_text()` 文本生成逻辑，保留 `PromptPackage.output_schema`和`RangeResult`字段，并把新语义显式标识为`factorization.bounded_range_prompt.v2`。新文本使用一个紧凑 canonical JSON skeleton 承载不可变任务值，删除候选列表与双模板，加入 bounded Fermat、安全 wheel sieve、原始目标重载和最终精确验证规则。Slim V2 文档只记录本次实验 prompt 修订，不修改四份冻结权威。

**Tech Stack:** Python 3.12、pytest、TokenShare Factorization plugin、Markdown。

---

### Task 1: 冻结 prompt-only 合同

**Files:**
- Modify: `tests/plugins/factorization/test_factorization_prompt_package.py`

- [x] **Step 1: 先写失败测试**

  更新现有 prompt package 测试，继续逐项断言 `output_schema.required_fields` 与 `constraints` 不变，并新增以下行为断言：

  ```python
  assert "IMMUTABLE TASK AND RESPONSE SKELETON" in prompt_text
  assert "bounded Fermat" in prompt_text
  assert "may be skipped only when N % p != 0" in prompt_text
  assert "Reload N, L, and U" in prompt_text
  assert "Candidate divisors to test:" not in prompt_text
  assert "For no_factor_in_range, return exactly this JSON shape" not in prompt_text
  assert prompt_text.count('"target_n": "221"') == 1
  ```

- [x] **Step 2: 运行测试并确认 RED**

  ```powershell
  conda run -n tokenshare python -m pytest tests/plugins/factorization/test_factorization_prompt_package.py -q
  ```

  预期：因新 prompt 文本尚不存在而失败；schema/constraints 旧断言仍通过。

### Task 2: 实现紧凑且完备的算法路由 prompt

**Files:**
- Modify: `src/tokenshare/plugins/factorization/prompt_builder.py`

- [x] **Step 1: 删除 prompt 冗余**

  删除 `_candidate_divisors_text()` 调用及 helper；不再生成 `bound_fields`、`found_factor_template`、`no_factor_template` 三份重复 JSON。改为一个紧凑 skeleton，所有 protocol-bound 值只出现一次，`found_factor/cofactor/checked_divisor_count/created_at` 使用明确占位符和条件说明。

- [x] **Step 2: 加入搜索与最终验证规则**

  prompt 必须包含：

  ```text
  For odd N, map [L,U] to bounded Fermat bounds:
  a_start = ceil((U^2 + N) / (2U))
  a_end = floor((L^2 + N) / (2L))
  ```

  当 Fermat 区间显著更短时完整扫描该区间；否则使用 sound wheel sieve，且只允许用满足 `N % p != 0` 的 prime 跳过其倍数。`no_factor_in_range` 只能来自对 `[L,U]` 的完备覆盖。found factor 输出前必须从 canonical skeleton 重新读取 `N/L/U`，执行 range、modulo、integer quotient、product 和逐位目标比较。

- [x] **Step 3: 保持机器合同不变**

  不修改 `_RANGE_RESULT_REQUIRED_FIELDS`、`output_schema`、`constraints`、parser、verifier、runtime adapter 或 Slim execution bridge。prompt profile升级为`factorization.bounded_range_prompt.v2`，使新旧实验身份可区分。`checked_divisor_count`继续机械设置为no-factor时`U-L+1`、found-factor时`d-L+1`，但明确它是protocol coverage value，不要求逐项叙述。

- [x] **Step 4: 运行 prompt focused test 并确认 GREEN**

  ```powershell
  conda run -n tokenshare python -m pytest tests/plugins/factorization/test_factorization_prompt_package.py -q
  ```

  预期：全部通过。

### Task 3: 同步实验记录并验证兼容性

**Files:**
- Modify: `Doc/SlimV2/slim_v2_design_spec.md`
- Modify: `Doc/SlimV2/slim_v2_implementation_plan.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `progress.md`

- [x] **Step 1: 记录权威影响结论**

  明确四份冻结权威无需修改；prompt revision 不改变实验、指标、provider、任务切分、JSON schema、parser/verifier 或 trace lookup。旧 representative traces 不得与新 prompt 的 Full 混用；新 representative/Full 必须由同一当前代码生成 Exp1 traces，Exp2–4继续继承同一 prompt。

- [x] **Step 2: 运行 focused compatibility verification**

  ```powershell
  conda run -n tokenshare python -m pytest tests/plugins/factorization/test_factorization_prompt_package.py tests/plugins/factorization/test_factorization_parser.py tests/plugins/factorization/test_factorization_verifier.py tests/experiments/slim_v2/test_answer_paths.py -q
  ```

  预期：全部通过；不调用 provider、网络或 Lean。

- [x] **Step 3: 静态检查和最终 diff 审查**

  ```powershell
  conda run -n tokenshare python -m compileall -q src/tokenshare/plugins/factorization/prompt_builder.py
  git diff --check
  git diff -- src/tokenshare/plugins/factorization/prompt_builder.py tests/plugins/factorization/test_factorization_prompt_package.py Doc/SlimV2/slim_v2_design_spec.md Doc/SlimV2/slim_v2_implementation_plan.md Doc/TechnicalDocument/tokenshare_v1_code_map.md progress.md
  ```

  预期：compile exit 0、无新增 whitespace error，diff 只包含本计划允许的 prompt/test/docs/progress 变化，并保留用户已有未提交修改。

## 完成证据

- 两次行为变更均完成可解释RED→GREEN：B方案文本合同与profile `v2`身份。
- prompt/parser/verifier/Slim answer-path focused compatibility：`34 passed in 19.81s`。
- 共享Factorization plugin与Phase 6 flow回归：`61 passed in 17.03s`。
- 最终合并fresh focused regression：`80 passed in 34.58s`。
- 代表性大区间prompt：`3154 → 2336`字符（`-25.9%`），目标数字面出现`4 → 1`。
- `compileall`、相关Markdown严格UTF-8读取和`git diff --check`：PASS。
- provider/network=`0/0`；未运行representative、Full、Lean专项suite、LeanAudit、catalog、`lake`或`lean`。
