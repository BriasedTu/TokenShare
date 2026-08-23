# Exp5 三模型实测与 Exp1 Flash V4 参考比较实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Experiment 5 缩为三个 100k thinking 模型的真实运行，并在不改写 Experiment 5 原始事实的前提下，发布一张带 Exp1 Flash V4 历史参考行的补充比较表。

**Architecture:** `run-all`、`representative` 和主 reducer 保持五张正式实验表的现有接线不变。新增的显式比较命令只在三模型 Exp5 已完成并发布后读取其 `exp5` 表和一个传入的 Exp1 Flash source run；它要求 case 集精确相同，生成独立的 supplemental 表。三行 Exp5 保留真实 wall-clock；V4 行的全部 wall-clock 字段固定为 `null + not_applicable_or_unavailable`，绝不把 Exp1 延迟伪装为 Exp5 事实。

**Tech Stack:** Python 3、现有 `RunStore`/`RootInventoryV1`/`RootResultV2`、JSONL/CSV、pytest focused regression。

---

### Task 1: 冻结三模型 Exp5 inventory 与 provider 控制

**Files:**
- Modify: `benchmarks/paper/exp5_siliconflow_provider_config.v3.json`
- Modify: `src/tokenshare/experiments/slim_v2/profiles.py:750-755,1423-1428`
- Test: `tests/experiments/slim_v2/test_profiles.py`

- [x] **Step 1: 写失败的 profile 断言**

将 Exp5 的模型顺序断言为 GLM、Qwen、MiniMax，Representative 根数为 3，Full 条件/根/provider upper 为 `12/111/852`，并断言 V3 不在 inventory。

- [x] **Step 2: 运行失败断言**

运行：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_profiles.py -q`

预期：针对仍为四模型、148 roots、1,136 provider upper 的断言失败。

- [x] **Step 3: 最小配置与 inventory 修改**

将三个 SiliconFlow thinking entry 的 `thinking_budget` 设为 `100000`，将其默认 `max_tokens` 对齐为实际 caller 的 `100000`，删除 DeepSeek V3 entry；在 `_EXP5_PROVIDER_ENTRIES` 和 `model_ids` 中删除 V3。不得修改 Exp1–4 或 `run-all` 的执行路径。

- [x] **Step 4: 运行 profile 断言**

运行：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_profiles.py -q`

预期：全部通过，且 `build_plan("representative")` 的 Exp5 provider upper 为 24。

### Task 2: 发布隔离的 V4 历史参考表

**Files:**
- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Modify: `src/tokenshare/experiments/slim_v2/cli.py:105-133,1422-1489`
- Test: `tests/experiments/slim_v2/test_reducer_golden.py`
- Test: `tests/experiments/slim_v2/test_cli_e2e.py`

- [x] **Step 1: 写失败的 supplemental-table 测试**

构造一个三模型 Exp5 target run 和一个相同 case 的 Exp1 Flash source run。断言命令输出四行：三个 `observation_origin="exp5_live"` 行保留各自的 `repeat0_wall_clock_ms`，V4 行为 `observation_origin="exp1_reused_actual"`、保留质量/first-attempt token/cost，且只有 V4 行所有 wall-clock 字段均为 `null + not_applicable_or_unavailable`。

- [x] **Step 2: 运行失败断言**

运行：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q -k v4_reference`

预期：导入的 supplementary reducer 函数或 CLI 子命令不存在。

- [x] **Step 3: 最小实现**

新增一个 reducer 公开函数和 `compare-exp5-v4-reference --run-dir <three-model-exp5-run> --source-run-dir <flash-exp1-run>` 子命令。函数必须：(a) 要求 target 仅含三个冻结 Exp5 模型；(b) source 仅接受 `experiment_id="exp1"`、`configured_model="deepseek-v4-flash"`、`repeat_id=0` 的已提交结果；(c) 要求 source/target case 集精确相同；(d) 要求 source 没有 ordinal>0 的 provider call；(e) 将输出原子发布到 `metrics/supplemental/exp5_with_exp1_v4_reference.{jsonl,csv}`；(f) 不调用 provider、不修改正式 `metrics/tables/exp5.*` 或 `summary.json`。

- [ ] **Step 4: 运行 focused tests**

运行：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py tests/experiments/slim_v2/test_cli_e2e.py -q`

预期：所有 focused reducer/CLI 测试通过。

### Task 3: 同步权威记录与静态合同

**Files:**
- Modify: `Doc/SlimV2/slim_v2_experiment_metrics_authority.md`
- Modify: `Doc/SlimV2/slim_v2_design_spec.md`
- Modify: `Doc/SlimV2/slim_v2_exp5_scale_change_20260823.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `progress.md`

- [x] **Step 1: 记录新口径**

将 Exp5 正式 cohort 写为 GLM、Qwen、MiniMax、`thinking_budget=100000`、single repeat；记录 V4 仅进入 supplemental comparison，保留其 Exp1 provenance/价格/quality/token/cost，延迟字段不发布；相应更新 Full/Representative 的根数与调用上限。

- [x] **Step 2: 静态验证文档与代码合同**

运行：`conda run -n tokenshare python -m compileall -q src/tokenshare/experiments/slim_v2`

预期：退出码 0。

### Task 4: 保护刚通过的 Representative 接线

**Files:**
- Test: `tests/experiments/slim_v2/test_answer_paths.py`
- Test: `tests/experiments/slim_v2/test_cli_e2e.py`

- [ ] **Step 1: 运行 Representative focused regression**

运行：`conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_answer_paths.py tests/experiments/slim_v2/test_cli_e2e.py -q`

预期：全部通过；测试只使用 fake transport，不触发真实 provider。

- [x] **Step 2: 检查差异范围**

运行：`git diff --check` 与 `git diff --name-only`。

预期：无空白错误；仅出现本计划、既有 Exp5 配置/inventory、supplemental reducer/CLI、focused tests、Slim 文档、code map 与 progress 记录。

### 实施验证与已知限制（2026-08-23）

- 已观察 fail-first：profile 新断言在旧 inventory 为 `4 failed, 13 passed`；supplemental reducer import 与 CLI 子命令各在实现前缺失；100k Answer-paths 请求断言在保留旧 `32768` 期望时为 `3 failed, 17 deselected`。
- 已完成 focused 验证：`test_profiles.py -q`=`17 passed in 0.90s`；`test_reducer_golden.py -q`=`37 passed in 11.91s`；supplemental CLI test=`1 passed, 26 deselected`；Exp5 Answer-paths test=`3 passed, 17 deselected`；Slim `compileall` 与 `git diff --check`均通过。新增两条 reducer 边界测试实际构造 case-set 不同和 ordinal>0 provider-call source，均由 `ValueError` fail closed。
- 唯一未运行项是 `test_cli_e2e.py::test_cli_representative_fake_transport`：其 153 metrics × 10,000 bootstrap 在 60 秒内无输出，按运行约束主动终止。故 Task 2 Step 4 与 Task 4 Step 1 保持未勾选；其余已完成步骤均已勾选。本轮没有真实 provider 调用，也没有启动 Representative/Full。
