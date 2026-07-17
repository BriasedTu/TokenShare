# Paper Real AI Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when implementing this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `feat-011` 最新真实 AI 论文实验从“设计文档”落实成可运行、可审计、可复现的实验代码和实验数据，同时严格区分实验层代码与 TokenShare 系统内部能力。

**Architecture:** 论文实验入口统一放在 `tokenshare.experiments`，负责选题、重复、worker 数、故障、模型策略、指标和报告；协议核心、factorization 插件、Lean 插件、AI executor 只补自己真实缺失的通用能力。实验层不能替代插件 verifier / Lean checker / 协议事件事实，系统内部也不能写入 paper-only 的实验变量。

**Tech Stack:** Python 3.12、SQLite、JSON/JSONL、本地 artifact store、真实 AI API executor、固定本地 Lean/lake toolchain、PowerShell UTF-8 验证流程。

---

## 文档定位

本文是实施计划，不是第二份实验设计。唯一权威实验设计仍是 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。

本文回答四个工程问题：

- 现在这些实验能不能直接跑。
- 如果要跑，还缺哪些东西。
- 每个缺口属于实验层代码，还是系统内部能力。
- 先做哪些、后做哪些，避免一边写实验脚本一边污染协议核心。

结论先写清楚：

- 现在不能直接跑成论文可采信实验。旧 Phase 8 runner、direct factorization 500 和 Lean AI 50 都只能作为回归或校准，不能满足最新 paper eligibility。
- 已完成的实验基础包括 paper schema、factorization 30 题 catalog、Lean shallow-v1 30 题 catalog、三个 checker-backed medium lemma-DAG golden case、real-AI eligibility gate、Factorization/Lean paper adapters、post-AI fault support、worker-death support、ablation support、plan-only/budget skeleton、双 provider transport、三模型 cohort preflight 和 fixed-entry identity 闭环。
- 当前最大缺口不是“模块不存在”，而是没有完整 execution orchestrator：`paper_runner.py` 仍以 plan-only condition expansion 为主，Experiment 2/3 尚未展开，正式路径不会调度 adapters、fault/recovery、worker death 或 ablation，也不会生成逐 run/task/attempt 论文产物。
- Lean v1 仍只代表 simple/shallow；v2 已能执行三个 medium lemma-DAG golden case，但 3×3 每格 10–20 题的正式 catalog、hard/frontier oracle-backed case 和可能需要的新 top-level/induction/rewrite split rules 仍未完成。因此不能把当前能力描述为“支持任意困难 theorem”。
- `budget / plan-only / approve-budget-digest` 不是线上系统能力，必须留在实验层；但按唯一权威实验设计和当前 CLI gate，它是 pilot/正式真实 API 执行前的成本与配置审批门禁，不再是可以在正式路径中跳过的可选功能。

## 英文术语速查

| 术语 | 大学生能听懂的解释 | 在本项目里的边界 |
|---|---|---|
| `catalog` | 实验题库。每行是一道要跑的题，并带难度和标准答案。 | 实验层文件，放 `benchmarks/paper/*.jsonl`。 |
| `runner` | 总调度脚本。它决定跑哪些题、跑几遍、用几个 worker、写到哪个输出目录。 | 实验层，放 `tokenshare.experiments`。 |
| `adapter` | 翻译层。把实验题库里的题变成现有插件/协议/executor 能执行的任务。 | 实验层；不能把验证规则写进 adapter。 |
| `paper_eligible` | 能不能作为论文正式结果。 | 实验层审计标记；必须由真实 API evidence 支撑。 |
| `real transport` | 真的调用 AI API，不是 fake/scripted/deterministic。 | executor 已有能力；实验层必须强制使用。 |
| `artifact` | 落盘证据文件，比如 prompt、raw output、parsed output、checker report。 | 系统已有基础设施；实验层必须引用它们。 |
| `raw output` | 模型原始返回文本。 | 必须先保存，故障注入不能绕过它。 |
| `parser` | 把模型文本解析成结构化 JSON 或 proof candidate。 | 插件拥有，不应由实验层重写规则。 |
| `verifier` | factorization 的确定性验算器。 | factorization 插件内部能力。 |
| `checker` | Lean 本地形式化证明检查器。 | Lean 插件内部能力。 |
| `canonical output` | 协议正式接受的输出。 | 协议核心/插件验证后才能绑定。 |
| `merge` | 子任务都完成后，把子结果合成父结果。 | 插件 merge policy + 协议事件流程。 |
| `fault injection` | 故意制造错误，看系统能不能发现或恢复。 | 实验层选择和变换；协议内部只负责正常的 lease/requeue/canonical 安全。 |
| `worker death` | 真的杀掉一个执行 worker 进程。 | 实验层 harness；如果暴露 lease/reassignment bug，再补协议内部。 |
| `lease expiry` | worker 超时没交活，租约过期。 | 协议核心已有基础能力；实验要制造并证明它发生。 |
| `requeue / reassignment` | 失败或过期后重新分配给别的 worker。 | 协议核心能力；实验层只触发和记录。 |
| `ablation` | 关闭某个机制看会坏在哪里。 | 实验层配置；不能把“关闭 verifier”变成系统默认行为。 |
| `metrics` | 从证据算出来的完成率、正确率、成本、延迟、恢复率。 | 实验层，但必须从 events/artifacts/attempts 复算。 |
| `report` | 输出论文表格、CSV、audit report。 | 实验层。 |
| `pilot` | 小规模试跑，检查题库、API、schema、成本和 bug。 | 必须先于正式 P0 run。 |
| `P0-core` | Experiment 1-4 的正式最小论文实验矩阵。 | 规模很大，不能一上来直接跑。 |
| `plan-only` | 只算预计 API 次数、token、成本，不真正跑。 | 实验层真实执行前置门禁，不是线上系统能力；pilot/正式 run 都先生成对应预算。 |

## 当前缺陷归类

本表记录的是 **2026-07-17 当前现实**，不再把已经完成的 support module 写成“尚不存在”。“已完成”只表示相应模块或 plan-only contract 已实现并通过测试；正式 runner integration、真实 API 执行和论文产物必须分别记录。

| ID | 当前已有 | 当前剩余缺口 | 层级 / 后续归属 |
|---|---|---|---|
| D01 | 已有 `run_paper_experiments` 和 plan-only CLI，可写 structured blocked/planned suite。 | 没有 execution orchestrator；Experiment 2/3 不展开，非 plan-only 路径仍只写 planned suite。 | Task 12 先打通 Exp1；Task 19 再扩到 Exp2–5。 |
| D02 | 已有 frozen factorization 30 题、Lean shallow-v1 30 题、三个 medium lemma-DAG golden case 和一个 structured-blocked hard/frontier row。 | Lean 3×3 每格 10–20 题尚未完成，hard/frontier 没有可进入正式 proof 主表的 oracle-backed case。 | Task 17；必要时触发 Task 18。 |
| D03 | 已明确 shallow-v1 全部只算 simple；三个 topic family 各有一个 medium golden case。 | 当前样本量仍不足以支撑复杂度、扩展性或 hard theorem 主张。 | Task 17、18、27。 |
| D04 | 已有 real-AI eligibility gate；scripted/fake/deterministic evidence 会被判 paper-ineligible。 | 正式 runner 尚未强制每个 AI unit 实际走 real transport 并汇总 suite/run eligibility 与 formal secret scan。 | Task 12、14、19、25。 |
| D05 | Factorization paper adapter 已能 deterministic split、逐 range AI execution、parser/verifier、canonical/merge。 | 尚未接入正式 runner；Experiment 2 同一 root 内多 worker scaling 和正式数据未执行。 | Task 12、19、31。 |
| D06 | Lean simple adapter、三个 medium lemma-DAG golden case、dependency-aware proof assembly/root recheck 已完成。 | 不支持任意 top-level/induction/rewrite/hard-frontier shape；只有 catalog/规则证明确实需要时才扩 Lean internals。 | Task 17 后由 Task 18 条件触发。 |
| D07 | `paper_faults.py` 已实现 deterministic post-raw mutation 和 original/mutated evidence records。 | fault-aware runner 尚未把 mutation 接入真实 lease/submission/canonical 流程；`executor_error` recovery 尚未由 runner 再次真实调用 provider。 | Task 20、27、32。 |
| D08 | `paper_workers.py` 已实现独立 subprocess、kill point、lease expiry 和 replacement-attempt harness。 | 尚未绑定真实 adapter raw-output evidence，也未让 replacement worker 重新调用同一批准模型端点。 | Task 21、27、32。 |
| D09 | 已有 ablation profile、provider-evidence coverage validator 和 evidence summary support。 | `paper_metrics.py` 不存在；正式 ablation run、scaling、recovery 和汇总指标尚未从 events/artifacts 复算。 | Task 13、22、24。 |
| D10 | plan-only CLI 可写 `suite_manifest.json`、`run_budget.json` 和 preflight artifacts。 | 正式 per-run/task/attempt JSONL、CSV、eligibility、secret scan 和 failure examples 尚不存在；`paper_report.py` 不存在。 | Task 14、25。 |
| D11 | Experiment 5 的三模型 cohort、跨 provider preflight、fixed-entry condition identity、pre/post-call validator、通用 request provenance 和逐 AI unit identity artifact 已完成。 | 仍缺 real-provider runner/pilot、`model_execution_records.jsonl` join、metrics/report/CSV、formal secret scan 和真实三端点运行。 | Task 23、25、27、34。 |
| D12 | `paper_budget.py` 和 plan-only/approval digest 基础已实现，且不进入协议核心。 | Experiment 2/3、fault/worker/ablation、真实 split/request profile、quota/rate-limit 和 pilot-sized approval 尚未完整展开。 | Task 11、26、29。 |
| D13 | medium lemma-DAG 第一版已实现三个 checker-backed case。 | 正式 3×3 catalog、hard/frontier oracle policy、批量 preflight 和可能的新 deterministic split/merge rules 仍未完成。 | Task 17、18。 |

## 边界规则

以下内容必须留在实验层：

- `experiment_id`、`condition_id`、`repeat_id`、`difficulty`、`fault_rate`、`ablation_mode`、`worker_count`、`model_policy`。
- `paper_eligible`、`ineligibility_reasons`、论文 CSV、图表数据、suite manifest。
- 实验用的 worker pool、fault target selection、pilot/full run 矩阵。
- 成本上限、`plan-only`、`budget_digest`、`approve-budget-digest`。

以下内容属于系统内部：

- `tokenshare.core`：任务状态、lease、attempt、canonical、merge、event ledger 安全。
- `tokenshare.plugins.factorization`：range split、parser、verifier、merge policy、oracle 规则。
- `tokenshare.plugins.lean_proof`：Lean theorem payload、split helper、proof parser、checker、merge/root recheck。
- `tokenshare.executors`：真实 API 调用、raw output 持久化、provider provenance、usage、latency、secret redaction、replay guard。

以下事情不能做：

- 不能把 Lean 或 factorization 的领域规则写进 `tokenshare.core`。
- 不能让 AI 决定协议级拆分。
- 不能让实验层绕过插件 verifier/checker。
- 不能把 fake/scripted run 标成论文正式结果。
- 不能把 `plan-only` 当作线上系统功能。

## 实施顺序总览

顺序原则：先让实验层能小规模真实跑，再看系统内部是否真的挡住；不要一开始就改协议核心。

### Task 0: 固定边界和基线

**层级：** 文档/实施准备，不改系统行为。

**Files:**
- Read: `AGENTS.md`
- Read: `Doc/agent-navigation.md`
- Read: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Read: `feature_list.json`
- Read: `progress.md`
- Read: `session-handoff.md`

- [x] 运行 `.\init.ps1`，确认当前基线仍通过。
- [x] 确认 active feature 是 `feat-011`。
- [x] 确认本计划只补 paper experiment path，不回到 `feat-010` replay/audit。
- [x] 确认 `budget / plan-only` 是实验层真实执行门禁，不是线上系统、协议核心、插件或 executor 功能。

**2026-07-17 完成记录：** 当前 active feature 为 `feat-011`；`feat-010` 仍延后。修订前 fresh `.\init.ps1` 收集 638 项，结果 `637 passed, 1 skipped in 265.90s`，skip 为默认关闭的真实 SiliconFlow smoke。budget/plan-only 继续只属于实验层，但按权威设计是 pilot/正式真实 API 执行前的审批门禁。

**修复的缺陷：** D12，防止把实验管理工具误写进协议核心。

**验证：**

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

### Task 1: 新增 paper model 和 runner skeleton

**层级：** 实验层。

**Files:**
- Create: `src/tokenshare/experiments/paper_models.py`
- Create: `src/tokenshare/experiments/paper_runner.py`
- Create: `src/tokenshare/experiments/run_paper_experiments.py`
- Modify: `src/tokenshare/experiments/__init__.py`
- Test: `tests/experiments/test_paper_models.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [x] 写 failing tests：要求 `PaperSuiteResult`、`PaperExperimentResult`、`PaperConditionResult`、`PaperRunResult`、`PaperTaskResult`、`PaperAttemptResult` 有稳定 schema/status 字段。
- [x] 写 failing CLI test：`python -m tokenshare.experiments.run_paper_experiments --help` 可用，但不传 `--real-transport` 不能启动正式 paper run。
- [x] 实现最小 dataclass / dict serialization / status enum。
- [x] 实现 CLI skeleton，只能输出 structured blocked / planned status，先不跑真实 API。

**修复的缺陷：** D01，先有统一入口和稳定返回对象。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_models.py tests\experiments\test_run_paper_experiments_cli.py -q
```

### Task 2: 新增 paper catalog 和 catalog loader

**层级：** 实验层。

**Files:**
- Create: `benchmarks/paper/factorization_catalog.v1.jsonl`
- Create: `benchmarks/paper/lean_catalog.v1.jsonl`
- Create: `src/tokenshare/experiments/paper_catalog.py`
- Test: `tests/experiments/test_paper_catalog.py`

- [x] 写 factorization catalog 30 题：easy/medium/hard 各 10 题，带 `target_n`、oracle、candidate range、difficulty、split params、source seed。
- [x] 写 Lean shallow-v1 catalog 30 题：历史 easy/medium/hard 标签各 10 题，带 theorem payload、expected split kind、expected child count、minimum proof steps、context size、oracle proof/preflight ref；2026-07-15 起这些标签只表示 shallow-v1 内部变化，正式论文口径全部归为 simple / shallow。
- [x] loader 校验字段完整、difficulty 分布正确、case id 不重复、catalog digest 稳定。
- [x] factorization preflight 用 deterministic oracle 验证目标数和标准答案。
- [x] Lean preflight 用本地 Lean checker 验证题目本身为真，不能按 AI 能不能答出来挑题。

**修复的缺陷：** D02、D03。

**重要决策：**

- 如果旧 v1 的 “hard” Lean 题仍使用 `P ∧ Q` / `P ↔ Q` 顶层，只是 child proof 更长、更有干扰 context，它仍只算 simple / shallow；正式 medium / hard-frontier 必须另建 recursive lemma-DAG / frontier catalog。
- 如果正式 medium lemma-DAG / hard-frontier Lean 题需要 `P → Q`、`∀`、nested conjunction/iff 等新顶层拆分，这一步必须输出 blocked/internal-gap，待 Task 17 建立 catalog 证据后进入 Task 18。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_catalog.py -q
```

### Task 3: 实现 real-AI paper eligibility gate

**层级：** 实验层，读取 executor evidence。

**Files:**
- Create or extend: `src/tokenshare/experiments/paper_models.py`
- Create: `tests/experiments/test_paper_real_ai_gate.py`

- [x] 定义 `paper_eligible` 计算规则。
- [x] scripted/fake/deterministic transport 一律 `paper_eligible=false`。
- [x] 缺 raw output、provider/model、usage、latency、request ref、parsed/parse-failure ref 的 attempt 一律不能 paper eligible。
- [x] secret scan failure 一律使 suite/run ineligible。

**修复的缺陷：** D04。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_real_ai_gate.py -q
```

### Task 4: 实现 factorization paper adapter

**层级：** 实验层；使用已有 factorization 插件能力。

**Files:**
- Create: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Test: `tests/experiments/test_factorization_paper_adapter.py`

- [x] 从 paper catalog case 生成 factorization root task。
- [x] 使用插件 deterministic `candidate_range_partition.v1` 拆成 range children。
- [x] 每个 range child 都走 `AIAPIExecutor` execution path；测试可注入 fake transport，正式 paper condition 必须通过 real-transport gate。本项不表示正式真实 API suite 已运行。
- [x] 每个候选必须经过 factorization parser/verifier。
- [x] 子结果通过 canonical/merge 形成 root final output。
- [x] per-attempt 记录 provider/model/raw/parsed/usage/latency/cost。

**修复的缺陷：** D05。

**系统内部是否要改：**

- 预计第一版不需要改 factorization 插件。
- 如果 catalog 设计成需要 composite cofactor 递归、early success、sibling pruning，才另立系统内部 factorization 扩展；当前计划先不做。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_factorization_paper_adapter.py tests\plugins\factorization -q
```

### Task 5: 实现 Lean paper adapter

**层级：** 实验层；使用已有 Lean 插件 checker/parser/merge。

**Files:**
- Create: `src/tokenshare/experiments/lean_paper_adapter.py`
- Modify only if needed: `src/tokenshare/experiments/lean_ai_benchmark.py`
- Test: `tests/experiments/test_lean_paper_adapter.py`

- [x] 从 Lean catalog case 生成 theorem payload。
- [x] 调用 Lean split helper，拿到 split certificate 或 structured unsupported。
- [x] child/direct proof 都走 `AIAPIExecutor` execution path；测试可注入 fake transport，正式 paper condition 必须使用真实 transport。本项不表示正式真实 API suite 已运行。
- [x] AI proof candidate 经过 Lean parser。
- [x] proof candidate 经过本地 Lean checker。
- [x] child proofs merge 后做 root recheck。
- [x] per-task/per-attempt 记录 checker report、proof artifact、environment digest。

**修复的缺陷：** D02、D03、D06 的实验层部分。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_lean_paper_adapter.py tests\plugins\lean_proof -q
```

### Task 6: Lean medium lemma-DAG 最小基础（已完成）

当前 v2 medium lemma-DAG 第一版来自 deterministic catalog certificate、dependency-aware proof assembly 和 root recheck；剩余 catalog 与系统内部扩展已经迁移到 Task 17 / 18。

- [x] pure_logic、function_set、induction 各有一个 checker-backed `medium_lemma_dag` golden case。
- [x] 支持 dependency-aware proof assembly 和 root recheck。
- [x] 无 oracle hard/frontier case 保持 structured blocked，不伪造成 checker success。
- [x] 明确当前完成状态不代表 `P → Q` / `∀` / nested / induction / rewrite 新规则已经实现。

### 单独模块：Lean 复杂题库与递归 lemma-DAG 要求

**状态：** 部分实现。当前已有 pure_logic、function_set、induction 各一个 checker-backed `medium_lemma_dag` golden case，以及一个无 oracle 的 structured-blocked hard/frontier row；仍缺正式 3×3 每格 10–20 题、hard/frontier oracle-backed case 和批量 runner/预算集成。该缺口不阻塞 support module 开发，但继续阻塞正式论文中关于复杂 Lean 递归拆分、三档难度和三题型覆盖的主张。

**用户要求（2026-07-15）：**

- 当前 `benchmarks/paper/lean_catalog.v1.jsonl` 中已有题目，无论历史 `difficulty` 是 easy、medium 还是 hard，都应按正式论文口径归为 simple / shallow 题。
- 用户要求新增一类更难题：一个 root theorem 由多个 lemma 推导，每个 lemma 又由多个 sublemma 推导，形成递归 lemma dependency DAG；这类题应作为正式口径的 medium 难度。
- 用户还要求再定义更难一类：目前人类也不一定能做出、或非常复杂的混合题目。该类应作为 hard / frontier stress，但若没有可验证 oracle proof，不能伪造成 Lean checker success，只能输出 structured blocked / frontier stress evidence。
- 拆分插件能力必须随之提升，系统要支持这种复杂题，而不是只扩 catalog 文本。
- 2026-07-15 用户最新决策：当前正式 Lean 实验目标要扩大到 3 个 `paper_difficulty` × 3 个 `topic_family`，难度为 `simple`、`medium_lemma_dag`、`hard_frontier`，题型为 `pure_logic`、`function_set`、`induction`；每个单元目标 10-20 道可审计 case。这个决定先记录为正式实验目标，不代表现在可以直接修改 runner 或 paper adapter 执行矩阵。

**回应与边界：**

- Task 7A / Task 8A / Task 9A support modules 已经完成；formal integration 已迁移到 Task 20 / 21 / 22，可以先用 factorization、Lean simple 和已有 medium golden case 实现，不需要等待完整 3×3 catalog。但正式 Lean medium/hard robustness 主张仍必须等待 Task 17 的对应 catalog 覆盖。
- 但正式 Experiment 1-5 的论文结果不能只用当前 shallow Lean catalog 支撑 Lean 难度、worker scaling 或 protocol ablation 主张；正式 run 前必须回来补 complex Lean catalog 或把 Lean 复杂度主张降级。
- 后续实现应新增 `lean_lemma_graph_catalog.v1.jsonl` 或 `lean_catalog.v2.jsonl`，字段至少包含 `paper_difficulty`、`root_theorem_payload`、`lemma_graph`、`dependency_edges`、`expected_depth`、`expected_leaf_count`、`expected_ai_unit_count`、`merge_plan_shape`、`oracle_proof_package_ref`、`environment_digest`、`preflight_status`。
- 扩展 schema 时应复核是否新增 `topic_family`、`topic_family_version`、`construction_rule_id`、`oracle_package_group` 或等价 metadata；当前 v1 simple catalog 只是过渡输入，当前 v2 medium fixture 只是最小 golden case，不满足完整 3 × 3 题型矩阵。
- Lean 插件/adapter 后续必须支持递归 lemma graph split、proof-file assembly、per-lemma checker evidence、multi-level merge/root recheck、dependency-aware slot integrity，以及必要的 deterministic split/merge 规则；仍禁止 AI 决定协议级拆分。
- 已完成的 Task 7A / 8A / 9A 不得把 Lean 写死为两个 child 或 `P ∧ Q` / `P ↔ Q`；后续 Task 20–22 继续按 child list / slot list / dependency graph 集成，避免 lemma-DAG 重做。
- 进入实现前，下一位 reviewer 应先使用 `Doc/TechnicalDocument/2026-07-15-feat-011-lean-tiered-topic-catalog-review-prompt.md` 复核正式实验扩大后的矩阵规模、题型模板、oracle proof 成本、Lean split/merge 缺口、paper adapter DAG execution 缺口和预算影响；不要在题库与 deterministic split/oracle 方案未确定前先写 runner 执行代码或直接批量添加 90-180 道题。

### Task 7A: post-AI fault support module（已完成）

**层级：** 实验层 support module；不代表正式 fault-aware run 已执行。

**Files:**
- Create: `src/tokenshare/experiments/paper_faults.py`
- Test: `tests/experiments/test_paper_faults.py`

- [x] 用 deterministic seed 和 target descriptor digest 选择 fault targets。
- [x] 只有 raw output artifact 已持久化后才允许生成 mutation artifact 和 fault record。
- [x] `false_positive` 生成 intentionally invalid parsed candidate/submission mutation。
- [x] `false_negative` 只抑制确实存在的 found factor 或非空 proof。
- [x] `no_return` 生成 drop-submission / expected lease-expiry record，但不伪称 runner 已实际等待 lease expiry。
- [x] `late_submission` 校验提交时间超过 deadline，并记录 expected late rejection / `canonical_pollution=false`，但不伪称正式协议流已执行。
- [x] `executor_error` 保存 controlled error record 并标记 `retry_requires_new_provider_attempt=true`，但 support module 自身不调用 provider。
- [x] 每条 fault record 同时引用 original raw/output ref 和 mutated/suppressed ref。

**完成记录（2026-07-15）：** `paper_faults.py`、测试和 public exports 已完成；记录结构按 generic AI unit / dependency metadata 设计，不写死 Lean 两 child。当前完成范围止于 mutation/record 和 evidence validation。

**修复的缺陷：** D07 的 support-module 部分。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_faults.py -q
```

### Fault formal integration 迁移说明

Fault formal integration 的全部未完成项已迁移到 Task 20；此处只保留已完成的 Task 7A support module 及其验证证据。

### Task 8A: worker-death support harness（已完成）

**层级：** 实验层 support harness；如正式 integration 暴露 lease/reassignment bug，再补系统内部。

**Files:**
- Create: `src/tokenshare/experiments/paper_workers.py`
- Test: `tests/experiments/test_paper_workers.py`

- [x] 使用独立 subprocess 和独立 worker id 模拟执行槽。
- [x] 在 25% / 50% / 75% progress kill point 终止 subprocess。
- [x] coordinator 保持运行并通过 `LeaseManager` 形成 lease-expiry evidence。
- [x] 创建新的 replacement lease/attempt 并运行 replacement subprocess 到完成。
- [x] 记录 worker pid、kill point、lease expiry、reassignment 和 replacement attempt snapshot。

**2026-07-15 实现记录：** harness 使用 generic `PaperAIUnit` 和 dependency graph；当前 subprocess 执行的是受控 progress worker，不是实际 provider adapter。它证明进程死亡、lease expiry 和 replacement-attempt 记录结构，但不证明真实 API replacement 已完成。

**修复的缺陷：** D08 的 support-harness 部分。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_workers.py tests\core tests\storage -q
```

### Worker-death formal integration 迁移说明

Worker-death formal integration 的全部未完成项已迁移到 Task 21；此处只保留已完成的 Task 8A support harness 及其验证证据。

### Task 9A: ablation support module（已完成）

**层级：** 实验层 support module；不代表六模式正式 run 已执行。

**Files:**
- Create: `src/tokenshare/experiments/paper_ablation.py`
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Test: `tests/experiments/test_paper_ablation.py`

- [x] 定义 FULL、NO_PARSER_POLICY、NO_VERIFICATION、NO_REQUEUE、NO_MERGE_GATE、NO_SLOT_INTEGRITY 六种 profile。
- [x] 在应用 ablation support 前验证 generic dependency graph 的每个 AI unit 已有完整 provider request/raw/provenance/usage/parsed-or-parse-failure evidence。
- [x] 强制 `scope=experiment_boundary`、`requires_provider_attempt_before_ablation=true`、`system_default_behavior_changed=false`。
- [x] 从带 event/artifact refs 的 evidence records 汇总 canonical pollution、premature merge、blocked root、settlement blocked 和 slot-integrity violation。
- [x] plan-only 为 Experiment 4 展开六模式并按每档 5-task batch 估算 540 root-runs；不把当前 shallow Lean 冒充 medium/hard eligibility。

**2026-07-15 实现记录：** support profile、coverage validator、evidence record/summary 和 plan-only condition expansion 已完成；没有正式调度 adapter、没有运行真实 API ablation matrix，也没有生成论文 metrics/report/CSV。

**修复的缺陷：** D09 的 ablation-support 部分。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_ablation.py -q
```

### Ablation formal integration 迁移说明

Ablation formal integration 的全部未完成项已迁移到 Task 22；此处只保留已完成的 Task 9A support module 及其验证证据。

### Task 10: 实现预注册三模型 model-provider endpoint comparison

**层级：** Experiment 5 的 cohort、condition、执行记录和比较逻辑属于实验层；为了接入 `gpt-5.6-sol` 而新增的 OpenAI Chat Completions transport/config 支持属于 executor 层。executor 只执行 runner 已选择的 entry，不解释外部分数、不贴 strong/weak 标签、不决定实验路由。

**2026-07-16 用户决策：** 论文正式放弃 `strong`、`weak`、`mixed` 和 `difficulty_aware_mixed` 口径。Experiment 5 改为三个预注册、具名的固定模型端点比较：SiliconFlow `zai-org/GLM-5.2`、SiliconFlow `Qwen/Qwen3.6-27B`、OpenAI `gpt-5.6-sol` 且固定 `reasoning_effort=high`。Artificial Analysis 分数只作为模型背景和外部排序 metadata，不产生类别、不参与 routing，也不能代替 TokenShare 实验中的 completion、accepted validity、token、cost 和 latency 实测。

**外部资料本地摘要（访问日期：2026-07-16）：**

- Artificial Analysis [Intelligence Benchmarking Methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking) 与 [Data API](https://artificialanalysis.ai/data-api)：Intelligence Index v4.1 是版本化综合指标，覆盖 agent、coding、scientific reasoning 和 general 能力，并提供稳定数据接口。影响范围：只用于冻结 cohort 的外部背景分数、榜单版本、模型变体和访问日期；正式 run 不联网刷新分数。
- Artificial Analysis 模型页：[GLM-5.2 (max)](https://artificialanalysis.ai/models/glm-5-2) 为 51 分，[Qwen3.6 27B (Reasoning)](https://artificialanalysis.ai/models/qwen3-6-27b) 为 37 分，[GPT-5.6 Sol (high)](https://artificialanalysis.ai/models/gpt-5-6-sol-high) 为 56 分。影响范围：分数进入 tracked cohort snapshot；GLM/Qwen 的 SiliconFlow endpoint 若未证明 reasoning mode 与榜单变体完全一致，必须标记 `benchmark_match_status=family_only`，不能声称是同一 benchmark configuration。
- SiliconFlow [模型中心](https://www.siliconflow.cn/models) 确认当前 provider model strings `zai-org/GLM-5.2` 与 `Qwen/Qwen3.6-27B`；OpenAI [Models](https://developers.openai.com/api/docs/models) 确认 `gpt-5.6-sol` 是 GPT-5.6 Sol API model id，并支持 `high` reasoning effort。影响范围：Task 10A/10B 的 provider/model/reasoning identity preflight 和 safe manifest 字段。

**Files:**
- Create: `benchmarks/paper/model_comparison_cohort.v1.json`
- Create: `src/tokenshare/experiments/paper_model_policy.py`
- Extend: `src/tokenshare/experiments/paper_models.py`
- Create: `src/tokenshare/experiments/paper_model_identity.py`
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Extend: `src/tokenshare/experiments/run_paper_experiments.py`
- Extend: `src/tokenshare/executors/ai_api_config.py`
- Extend: `src/tokenshare/executors/ai_api_transport.py`
- Extend: `src/tokenshare/executors/ai_api.py`
- Extend: `src/tokenshare/executors/ai_api_local_config.py`
- Test: `tests/experiments/test_paper_model_policy.py`
- Create: `tests/experiments/test_paper_model_identity.py`
- Test: `tests/executors/test_ai_api_config.py`
- Test: `tests/executors/test_ai_api_transport.py`
- Create: `tests/executors/test_ai_api_openai_transport.py`

#### Task 10A: 补 OpenAI provider transport，但不把实验策略放进 executor

- [x] 已用 failing config/transport tests 驱动 OpenAI provider support；OpenAI config 仍只保存 `api_key_env`，禁止明文 key，provider/model/reasoning request 字段进入 safe digest。
- [x] 一个 executor config 仍只对应一个 `provider_family`；runner 可同时加载独立 SiliconFlow/OpenAI configs，不交叉展开 key 或 endpoint。
- [x] OpenAI Chat Completions 已有独立 request builder/parser/transport，保存 request、raw response、parsed/parse-failure、usage、latency、provider response id、resolved model 和 provenance artifacts。
- [x] `AIAPIExecutor` 的 provider family 已来自已校验 config；SiliconFlow v1 行为和历史 replay evidence 保持兼容。
- [x] OpenAI provider error、429、usage、secret redaction 和 replay guard 复用稳定枚举/artifact 边界；executor 不含 experiment/cohort/member policy。
- [x] GPT entry/smoke 不可用时 Experiment 5 preflight structured blocked，不自动用 SiliconFlow 模型替代。

#### Task 10B: 冻结三模型 cohort 并实现 fixed-entry condition

- [x] `model_comparison_cohort.v1.json` 固定 `cohort_id=tokenshare.paper.model_endpoint_cohort.v1` 和三个 member：`glm_5_2_siliconflow`、`qwen3_6_27b_siliconflow`、`gpt_5_6_sol_high_openai`。每个 member 至少记录 `provider_family,provider_model_id,reasoning_profile_id,external_benchmark_source,index_version,index_score,benchmark_variant,benchmark_match_status,source_url,observed_at`。
- [x] cohort snapshot 是 tracked、无 secret 的输入；正式 run 只读 snapshot 和 digest，不在 plan-only、execution、metrics 或 replay 阶段联网刷新榜单。榜单更新只能生成新的 cohort version，不能改写已运行 suite 的模型身份。
- [x] local gitignored entry map 只负责把三个 `cohort_member_id` 映射到 `provider_config_id + entry_id`；suite/preflight evidence 保存 sanitized mapping、source config digest 和 smoke evidence refs，不保存 key value。
- [x] `PaperExperimentCondition` 对 Experiment 5 使用 `model_policy="fixed_entry"`，并携带 `model_cohort_id,model_cohort_digest,cohort_member_id,provider_config_id,model_entry_id,provider_family,provider_model_id,reasoning_profile_id,source_provider_config_digest,model_endpoint_identity_digest`；新 schema 不允许 `strength`、`selected_strength`、strong/weak tag 或旧 strong/weak/mixed model policy。
- 剩余执行项迁移到 Task 23：三个 member 分别跑相同的 Experiment 1 catalog slice、task order、prompt/parser/plugin version、worker count、timeout、request-limit policy、repeat/seed family；每个 AI unit 固定 cohort member，不做 difficulty-aware routing，也不在失败后换模型。
- [x] 每个正式 adapter AI unit 保存 `tokenshare.paper_model_execution_record.v2` identity artifact，并由 `PaperAttemptResult.model_execution_record_ref` 引用；record 包含 expected identity、actual request/attempt summaries、request/provenance/raw/usage refs、source/prepared config digests、requested model、nullable resolved model、response model status、identity status/reasons 和 record digest。历史 v1 artifact 不重写，v1 外层含义混杂的 raw `model` 不作为 observed identity。
- 剩余输出项迁移到 Task 23 / 25：正式 runner/report 把 identity artifact 与 attempt/usage join 成 `model_execution_records.jsonl`，补齐 `model_policy,latency_ms,total_tokens,cost_estimate`，并纳入 formal secret scan/audit。
- [x] plan-only preflight 已逐 member 验证 config、key env、model id、reasoning control 和 smoke evidence schema/status/provider/model/reasoning/artifact refs。任一 member 缺失或不匹配时，正式 Experiment 5 整体写 `status="blocked"`、`blocked_reason="incomplete_model_cohort"`、`paper_eligible_possible=false`、`provider_calls_made=0`，且 Experiment 1-4 不失败。后续真实执行仍需正式 runner/pilot integration、预算审批、report export 和 formal secret scan。
- 剩余表述项迁移到 Task 25 / 34：论文和 CSV 将该实验称为 `three-model model-provider endpoint comparison`。GPT 走 OpenAI、GLM/Qwen 走 SiliconFlow 时，completion/accepted validity 是主要比较；latency、429 和 cost 必须标注 provider confounding，不能归因于纯模型能力。
- 剩余预算项迁移到 Task 26 / 29：Experiment 5 仍为 2 domains × 3 difficulties × 5 tasks × 3 fixed members × 3 repeats = 270 root-runs，因此不增加原 P0-full root-run 数；provider calls、token 和成本上界必须按三个 endpoint 的真实 split/request profile 重新 plan-only 展开。

**未完成项归属：** 三 member 同 slice 的执行能力由 Task 23 实现、Task 34 运行；`model_execution_records.jsonl` 和 comparison CSV 由 Task 23 / 25 输出；论文限定语由 Task 25 / 35 同步；270 root-runs 及真实 split/request profile 预算由 Task 26 / 29 完成。不得为了让历史 Task 10B 看起来完整而另写平行 runner/report。

#### Task 10C: Fixed-entry endpoint identity 闭环（当前工作树已验证，尚未提交）

**状态边界：** 下列完成声明只对应当前 `codex/exp5-fixed-entry-identity` 工作树。相关 production/tests/docs 仍有未提交或未跟踪改动，因此它不是 `HEAD 1017e569` 的可恢复完成状态；在提交前不得把它描述为已进入稳定仓库基线。

- [x] 已证明 `entry_id` 只在单个 `AIAPIExecutorConfig` 内唯一；正式 entry namespace 使用 `provider_config_id + selected_entry_id`，并同时绑定 provider/model/reasoning。
- [x] identity 分层记录 `model_cohort_digest`、`model_endpoint_identity_digest`、`source_provider_config_digest`，运行时另记录 `prepared_execution_config_digest`；source drift 会改变 condition/budget digest 并使旧 approval 失效。
- [x] `validate_condition_fixed_entry_identity()` 在 provider call 前校验正式 condition/source config，Factorization 在创建 artifact store 前阻断，Lean 在 simple/lemma-DAG dispatch 前共享一次 validator；legacy Experiment 1–4 partial identity 保持兼容。
- [x] `_prepare_config()` 只保留 validated selected entry 并固定 `max_provider_attempts=1`；selected 503 不会 failover 到 sibling model，replacement/retry 复用原 endpoint identity，config/model/reasoning drift 均零调用失败。
- [x] 通用 executor 只校验 request/config provider requirement 并写安全 `provider_request_identity.v2`；分开保存 config entry 的 `configured_model` 与实际 request body 的 `requested_model`，不导入 experiment package，不保存 prompt/messages、Authorization 或 API key。
- [x] `RawModelOutput.v2` 分开保存 configured/requested/resolved model 与 response model status；resolved model 只从 provider response 非空字符串获得，response 缺失/null/空/非字符串时保持 `null`，不再从 selected entry 回填。
- [x] `validate_fixed_entry_submission_identity()` 比较 request provider、prepared digest、attempt provider/entry/model、实际 request model/reasoning controls 和 schema-aware raw resolved model。OpenAI resolved model exact match；reasoning 字符串规范化比较；SiliconFlow 隐式 `enable_thinking=false` 被记录，未批准的 `true` 被拒绝。
- [x] resolved-model mismatch 或 missing 在 Factorization、Lean simple、Lean lemma-DAG 首个异常 unit 后标记 audit-stage `model_identity_mismatch`、paper-ineligible，跳过 verifier/checker 并停止后续 AI units；missing 使用 `missing_resolved_model`，不同/未批准 alias 使用 `resolved_model_mismatch`；历史 raw/parsed evidence仍保留审计。
- [x] provider error 且没有成功 raw output 时不伪造 model，record 使用 `identity_status=not_observed` 并保留 provider failure kind；parse-failed 但 raw response 已存在时仍执行 resolved-model audit。
- [x] replay guard 只读取并验证历史 artifacts，provider calls 为 0；future runner/resume 必须消费 condition/record 中的批准 identity，不得从当前 config 仅按 entry 字符串重解析或补写 resolved model。

**验证证据：** 2026-07-16 三路径 resolved-model mismatch RED 为 Factorization 2 calls、Lean simple 2 calls、Lean lemma-DAG 5 calls；GREEN 后均只调用首个异常 unit，Task 7 最终相关回归为 `102 passed in 80.76s`。2026-07-17 response-fact v2 第一轮 transport/executor/raw-schema RED 为 `28 failed, 12 passed`，GREEN 为 `40 passed`；identity/record/provider-error RED/GREEN 为 `9 failed, 34 passed` / `43 passed`；missing-model adapter stop gate RED 为 `3 failed, 30 passed`，补齐正常 formal path 后 adapter suite 为 `35 passed in 63.47s`；replay reader RED/GREEN 为 `1 failed, 2 passed` / `3 passed`；schema hardening RED/GREEN 为 `2 failed, 4 passed` / `6 passed`；review hardening 又完成 `6 failed, 48 passed` → `54 passed`、`5 failed, 4 passed` → `9 passed`、`1 failed, 5 passed` → `6 passed`。最终 targeted 为 transport 19、executor/failover/replay 31、identity 21、adapters 35、model policy 10、Lean merge 14、额外 raw/record schema 37 项全部通过；完整 `.\init.ps1` collected 638 items，结果 `637 passed, 1 skipped in 247.63s`。对抗复现得到 `resolved_model=null`、`missing_resolved_model`、paper-ineligible；独立 review 无剩余 Critical/Important。均未调用真实 API。

**修复的缺陷：** D11，并补齐 GPT-5.6 所需的最小 provider transport 前置能力。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\executors\test_ai_api_config.py tests\executors\test_ai_api_transport.py tests\executors\test_ai_api_openai_transport.py -q
conda run -n tokenshare python -m pytest tests\experiments\test_paper_model_policy.py tests\experiments\test_paper_models.py tests\experiments\test_run_paper_experiments_cli.py -q
conda run -n tokenshare python -m pytest tests\executors tests\experiments -q
```

## 已完成的剩余链前置基础

以下能力已经存在，不再占用 Task 11 之后的编号：

- `paper_budget.py`、plan-only budget digest 和 approval mismatch gate 已完成基础实现。
- plan-only CLI、real-transport gate、三模型 sanitized preflight 和 incomplete-cohort structured block 已完成。
- Factorization/Lean adapters、real-AI eligibility gate、fault/worker/ablation support modules 和 fixed-entry identity artifacts 已完成。
- 当前 `codex/exp5-fixed-entry-identity` 工作树已验证但尚未提交；进入 Task 11 前必须先形成可恢复 checkpoint，不能把未提交状态误当稳定 HEAD。

## 剩余实施任务（唯一执行顺序）

以下 Task 11–35 是剩余工作的唯一权威顺序。除明确标注为条件任务外，前一 Task 未完成时不得开始后一 Task；条件任务若没有触发，必须用证据记录“无需修改”后才能完成。

### Task 11: 冻结并批准 Exp1 最小 pilot 矩阵与预算

**依赖：** 已完成 budget/plan-only skeleton 和当前工作树 checkpoint。

- [ ] 固定 Factorization、Lean 两个 domain 的最小 Exp1 catalog slice、task order、repeat、seed、baseline entry 和 timeout/request-limit policy。
- [ ] 从 catalog 与现有 adapters 复算实际 split/request profile、AI-unit 数、provider-attempt 上界、token/cost、wall-clock 和磁盘上界。
- [ ] hard/frontier 无 oracle case 只能 structured blocked，不得计作会调用 provider 的成功条件。
- [ ] 生成独立 pilot suite id、`run_budget.json` 和稳定 digest，确认 `provider_calls_made=0`。
- [ ] 记录并批准对应 `--approve-budget-digest`；任何 profile/config drift 都使批准失效。

### Task 12: 实现 Exp1 双领域 execution orchestrator

**依赖：** Task 11。

**Files:**
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Extend: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_execution_runner.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] 为 Exp1 展开非空、可验证的 conditions；请求展开为零 conditions 时必须失败。
- [ ] 从 condition/catalog/repeat 建立 run/task/AI-unit 计划，并按 domain 调用现有 Factorization/Lean adapters。
- [ ] 加载单 baseline provider config 与 `--baseline-entry-id`，并在 provider call 前验证预算和模型身份。
- [ ] 支持 `--pilot`、real transport、独立 suite id、provider/token/cost hard limits 和 stop-after-current-task。
- [ ] 持久化 run/task/attempt/event/artifact evidence；resume/replay 只能消费已有证据，不得重新调用 API。
- [ ] 输出固定到 `outputs/experiments/paper_v1/<suite_id>/`。

### Task 13: 实现 pilot 最小 evidence-derived metrics

**依赖：** Task 12 的 evidence contract。

**Files:**
- Create: `src/tokenshare/experiments/paper_metrics.py`
- Test: `tests/experiments/test_paper_metrics.py`

- [ ] 从 per-task/per-attempt/event records 复算 completion rate。
- [ ] 复算 accepted validity rate，并与 completion rate 分开。
- [ ] 复算 parser/checker/verifier failure breakdown。
- [ ] 汇总实际 provider attempts、tokens、latency 和 cost estimate。
- [ ] 禁止沿用旧 helper 中硬写的 0/1 指标作为 pilot 统计。

### Task 14: 实现 pilot 最小 report 和 audit outputs

**依赖：** Task 12、13。

**Files:**
- Create: `src/tokenshare/experiments/paper_report.py`
- Test: `tests/experiments/test_paper_report.py`

- [ ] 为真实执行 suite 写最终 `suite_manifest.json`、`input_catalog_manifest.json` 和 `conditions.jsonl`。
- [ ] 写 `run_manifest.json`、`per_task_results.jsonl`、`per_attempt_results.jsonl`、event log 和 artifacts 索引。
- [ ] 写 Exp1 feasibility pilot CSV，并消费 Task 13 复算指标。
- [ ] 写 `paper_eligibility_report.json`。
- [ ] 写 `secret_scan_report.json`；plan-only manifest 不能代替正式输出。

### Task 15: 运行 Exp1 真实 pilot

**依赖：** Task 11–14。

- [ ] 使用已批准预算、real transport、独立 suite id 和固定 baseline entry。
- [ ] Factorization 与 Lean 每个当前可执行 difficulty 至少 1 个 case；无 oracle hard/frontier 保持 structured blocked。
- [ ] 检查 429、timeout、parse failure、checker failure、identity mismatch、成本和 secret scan。
- [ ] 保存完整 metrics/report/evidence，不把 smoke 或 scripted transport 当论文数据。

### Task 16: 分析 Exp1 pilot 并修复基础链路问题

**依赖：** Task 15。

- [ ] 复核 prompt/parser/schema/provider quota 和 adapter split/merge 失败。
- [ ] Factorization 能完成 range child -> verifier -> merge 时不改系统内部。
- [ ] 只修复 Exp1 pilot 已证明的通用 adapter/executor evidence 缺口，不提前实现 fault policy 或系统核心改造。
- [ ] 任何 code/config/profile 变化都重新执行 Task 11 的 plan/approve，并重跑受影响 pilot slice。

### Task 17: 冻结正式 Lean 3×3 catalog 与 checker preflight

**依赖：** Task 16。

- [ ] 将 shallow-v1 统一归为 `simple`，不得沿用 legacy hard 标签。
- [ ] 按 `simple`、`medium_lemma_dag`、`hard_frontier` × `pure_logic`、`function_set`、`induction` 建立冻结矩阵，目标每格 10–20 个可审计 case。
- [ ] 为可计入 checker success 的 case 固定 oracle package；无 oracle hard/frontier 只能 structured blocked/frontier stress。
- [ ] 批量运行本地 Lean checker preflight，记录 catalog digest、environment digest 和 eligibility。
- [ ] 如果某格无法满足目标，冻结降级主张和 blocked condition，不得伪造覆盖。

### Task 18: 按 catalog 证据决定并执行 Lean split/merge 扩展

**依赖：** Task 17。

- [ ] 先用 golden case/regression 证明现有规则是否阻塞 `P → Q` / `∀` / nested / induction / rewrite shape。
- [ ] 若未阻塞，记录 evidence-backed no-change decision 并完成本 Task。
- [ ] 若阻塞，为一个已证明需要的结构补 Lean-side deterministic rule、merge skeleton 和 root recheck。
- [ ] Python bridge 只接受 Lean helper certificate，不让 AI 决定协议级拆分。
- [ ] 运行 `tests/plugins/lean_proof` 并更新 `tokenshare_v1_code_map.md`。

### Task 19: 将 execution runner 扩展到 Exp2–5

**依赖：** Task 18。

- [ ] 为 Exp2/Exp3 补非空 condition expansion，并保持 Exp4/Exp5 条件可验证。
- [ ] 真正执行全部 worker levels、optional worker levels、repeats 和 seed family，不再只取第一个 worker level。
- [ ] Exp1–4 复用固定 baseline entry；Exp5 只消费已批准的三 member fixed-entry bindings。
- [ ] 实现 resumable evidence、stop-after-current-task 和 provider/token/cost hard limits。
- [ ] 为 Task 20–23 暴露单一 integration hooks，不平行重写 support modules。

### Task 20: 接入 fault-aware real recovery

**依赖：** Task 19。

- [ ] 对已完成真实 provider attempt 并保存 raw evidence 的 AI unit 应用预注册 fault target。
- [ ] `no_return` 驱动 lease expiry、requeue 和 replacement attempt。
- [ ] `late_submission` 经过 fencing/canonical gate，证明 late result 未进入 canonical。
- [ ] `executor_error` recovery 使用同一批准模型身份再次真实调用 provider，并累计 token/cost。
- [ ] 保存 fault records、attempts、events、original/mutated refs 和 recovery refs。

### Task 21: 接入真实 worker death/replacement

**依赖：** Task 20。

- [ ] 把 kill target 绑定到真实 adapter attempt，并证明 raw output 已持久化而 submission 尚未发生。
- [ ] 终止真正承载 AI unit 的独立 worker process，而不是 progress simulator。
- [ ] replacement worker 复用原 condition 批准端点并产生新的真实 provider attempt。
- [ ] 从 events/attempts 证明 lease expiry、reassignment、late-result isolation 和 no canonical pollution。

### Task 22: 接入六模式 real-AI ablation

**依赖：** Task 21。

- [ ] 每个 ablation condition 先由真实 adapters 为全部 AI units 生成完整 provider evidence。
- [ ] 在实验边界只应用一个 ablation mode，不修改系统默认 FULL 语义。
- [ ] 从真实 event/attempt/artifact 链计算 wrong canonical、stuck task、premature merge 和 slot mismatch。
- [ ] 输出逐 task/attempt ablation evidence，供 Task 24/25 消费。

### Task 23: 完成三模型 fixed-entry execution 与 record export

**依赖：** Task 22。

- [ ] 三个 cohort member 执行相同 catalog slice、task order、prompt/parser/plugin version、worker count、timeout、request-limit、repeat 和 seed。
- [ ] 每个 AI unit 固定 cohort member，不做 difficulty-aware routing 或失败换模。
- [ ] 将 identity artifact 与 attempt/usage join 成 `model_execution_records.jsonl`。
- [ ] 补齐 `model_policy,latency_ms,total_tokens,cost_estimate`，保留 provider confounding metadata。
- [ ] 任一 member 缺失时 Experiment 5 structured blocked，provider calls 为 0，不用相邻模型替代。

### Task 24: 完成全部 evidence-derived metrics

**依赖：** Task 20–23。

- [ ] 保留 Task 13 的 completion、accepted validity、failure breakdown 和 usage/cost 复算。
- [ ] 复算 detection、recovery、false accept、reassignment 和 cost overhead。
- [ ] 复算 worker speedup、throughput 和 parallel efficiency。
- [ ] 复算 ablation wrong canonical、stuck task、premature merge 和 slot mismatch。
- [ ] 生成三模型 completion/accepted-validity 主比较，并将 latency/cost/429 标为 provider-confounded。

### Task 25: 完成全部 report、CSV 和 audit outputs

**依赖：** Task 24。

- [ ] 完成 feasibility、scalability、robustness、ablation 和 model-provider endpoint comparison CSV。
- [ ] 完成 `fault_injections.jsonl`、`model_execution_records.jsonl` 和 failure examples。
- [ ] suite/run/task/attempt eligibility 必须从实际 evidence 派生。
- [ ] formal secret scan 覆盖 config digest、event、artifact、SQLite、log、JSONL 和 CSV。
- [ ] 论文和 CSV 统一使用 `three-model model-provider endpoint comparison`，不出现 strong/weak/mixed。

### Task 26: 生成并批准 Exp1–5 综合 pilot 预算

**依赖：** Task 19–25。

- [ ] 使用实际 Exp2/3 worker/fault/death 条件和 recovery/ablation provider-call profile。
- [ ] 使用三个 endpoint 的实际 split/request profile、quota/rate-limit、wall-clock 和磁盘估计。
- [ ] 为每个实验选择最小但有代表性的 pilot slice，生成独立 suite id 和 digest。
- [ ] plan-only 保持 provider calls 为 0；批准 digest 后才能进入 Task 27。

### Task 27: 运行 Exp1–5 小规模综合 pilot

**依赖：** Task 26。

- [ ] Exp1 覆盖两 domain 与当前 eligible difficulties。
- [ ] Exp2 worker levels 先用 1 和 3；Exp3 先选 1–2 类低比例 fault 和最小 worker-death slice。
- [ ] Exp4 覆盖六模式最小 slice；Exp5 三个 member 各跑相同最小 slice。
- [ ] 检查真实 metrics/report/eligibility/secret scan、429、timeout、cost 和 resume。
- [ ] 所有输出写入独立 pilot suite，不与正式 suite 混用。

### Task 28: 根据综合 pilot 做条件式系统补强

**依赖：** Task 27。

- [ ] 仅当 fault/worker pilot 暴露 lease/canonical/reassignment bug 时修改 `tokenshare.core` / `tokenshare.storage` 并补 targeted tests。
- [ ] 仅当 evidence 缺 provider-attempt correlation 或 cancellation-safe provenance 时扩 `tokenshare.executors.ai_api`；fault policy 仍留在实验层。
- [ ] 仅修复实际发现的问题，不扩大到生产级网络、worker pool 或 theorem-proving 平台。
- [ ] 任何修复导致 code/config/profile drift 时，重新执行 Task 26 并重跑受影响 pilot slice。

### Task 29: 生成并批准正式 suite 预算

**依赖：** Task 28。

- [ ] 按冻结 catalog、完整 conditions、repeats、worker/fault/ablation profiles 和三个 endpoint 展开正式矩阵。
- [ ] 输出 root-runs、AI units、provider attempts、token/cost、wall-clock、quota/rate-limit 和磁盘上界。
- [ ] 为正式 Experiment 1–5 suite 生成新 digest；不得沿用任何 pilot approval。
- [ ] 未批准、digest mismatch 或 cohort/config drift 均必须零 provider call 阻断。

### Task 30: 正式运行 Experiment 1

**依赖：** Task 29。

- [ ] 2 domains × 3 difficulties × 10 tasks × 3 repeats；不可用 Lean cell 按 Task 17 的 blocked/降级口径处理。
- [ ] 固定一个预注册 baseline entry，不贴 strong 标签。
- [ ] 输出 feasibility table、completion rate 和 accepted validity rate。

### Task 31: 正式运行 Experiment 2

**依赖：** Task 30。

- [ ] worker levels 1、3、10、30，每个 domain/difficulty 固定 5-task batch，repeats 5。
- [ ] Factorization scaling 必须是同一 root 内 range children 并行，不能用 direct 500 代替。
- [ ] 输出 speedup、throughput、efficiency 和 429/retry。

### Task 32: 正式运行 Experiment 3

**依赖：** Task 31。

- [ ] 运行 `false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error` rate-fault matrix。
- [ ] 运行 25%、50%、75% kill point worker-death matrix。
- [ ] 每个 fault record 引用 original/mutated refs，并输出 detection、false accept、recovery、completion、reassignment、cost overhead。

### Task 33: 正式运行 Experiment 4

**依赖：** Task 32。

- [ ] 运行 FULL、NO_PARSER_POLICY、NO_VERIFICATION、NO_REQUEUE、NO_MERGE_GATE、NO_SLOT_INTEGRITY 六种模式。
- [ ] 输出 ablation table，并从真实 evidence 复算所有安全/活性结果。

### Task 34: 正式运行 Experiment 5

**依赖：** Task 33。

- [ ] 只运行 `glm_5_2_siliconflow`、`qwen3_6_27b_siliconflow`、`gpt_5_6_sol_high_openai` 三个固定 member。
- [ ] 任一 member 缺失时写 `incomplete_model_cohort` 并阻断 Experiment 5，不影响已完成的 Experiment 1–4。
- [ ] 每个 condition/AI unit 使用固定 member，写完整 `model_execution_records`，不失败换模。
- [ ] 输出 endpoint comparison，并保留跨 SiliconFlow/OpenAI 的 provider confounding 限定。

### Task 35: 最终验证、论文输入和状态同步

**依赖：** Task 30–34。

- [ ] Targeted tests、`tests/experiments`、plugin/executor impact suites 全部通过。
- [ ] 完整 `.\init.ps1` 和 formal secret scan 通过。
- [ ] 更新 Phase 8 experiment infrastructure code map；只有 Lean/plugin internals 改动时才更新 `tokenshare_v1_code_map.md`。
- [ ] 更新论文 table/figure inputs、`feature_list.json`、`progress.md` 和 `session-handoff.md`。
- [ ] 记录正式 evidence paths、suite ids、run ids、blocked cells 和降级主张。

## 执行阶段摘要

- Task 11–16：先打通 Exp1 最小真实 pilot 闭环。
- Task 17–18：冻结 Lean 正式题库，并仅按 checker 证据决定是否扩插件。
- Task 19–25：完成 Exp2–5 runner integration、metrics 和 report。
- Task 26–28：综合 pilot、诊断与条件式补强。
- Task 29–35：重新审批正式预算，依次运行 Experiment 1–5 并收尾。
## 对用户问题的直接答案

**这些实验现在能不能跑？**
不能直接跑成论文结果。当前 paper schema/catalog、budget/plan-only skeleton、real-AI eligibility gate、post-AI fault/worker-death/ablation **support modules**、OpenAI/SiliconFlow executor support、三模型 cohort preflight、当前工作树中的 fixed-entry identity closure、Factorization/Lean adapters，以及三个 checker-backed medium lemma-DAG golden case已经完成。下一步严格从 Task 11 开始，依次完成 Exp1 pilot 预算、Task 12 execution orchestrator、Task 13 metrics、Task 14 report/audit、Task 15 真实 pilot 和 Task 16 pilot 诊断；之后才进入 Task 17–29 的正式 catalog、Exp2–5 integration、综合 pilot 与正式预算，再按 Task 30–34 运行 Experiment 1–5。Lean 3×3 题库仍未达到每 cell 10–20 cases，hard/frontier 无 oracle 的行只能 structured blocked。旧实验和 scripted transport 仍只能算 regression/calibration。

**要跑还需要哪些东西？**
优先需要实验层集成，不是优先改系统核心：正式真实执行 runner/pilot、worker scaling/fault/worker/ablation matrix orchestration、metrics、report/CSV、secret scan、CLI resume 和正式运行编排。已有 adapters/faults/workers/identity artifacts 必须被复用，不能再写平行规则。系统内部只在 pilot 证明挡路时补。

**准备的问题是不是太简单？**
是。旧 Lean 50 和当前 `lean_catalog.v1.jsonl` 都偏简单，因为主要是 `P ∧ Q` 和 `P ↔ Q`，child proof 常接近直接假设或短 implication chain。正式论文口径下，当前 v1 全部归为 simple / shallow；用户要求的“root theorem -> 多个 lemma -> 多个 sublemma”的递归 lemma-DAG 才算 medium；更难的 hard / frontier 可以是复杂混合题或人类也未必能完成的 stress case，但没有固定 oracle proof 的题只能作为 structured blocked / frontier stress，不能伪造成 checker success。

**Lean 拆分机制是不是还没做好？**
当前不是“完全没做好”，而是 medium lemma-DAG 第一版已完成、hard/frontier 和题库覆盖仍窄。Lean v1 支持 simple/shallow；v2 已能从 deterministic catalog certificate 生成 multi-level lemma-DAG units、dependency-aware proof assembly 和 root recheck，但目前只有三个 medium golden cases，不能据此宣称支持任意困难 theorem。新增 top-level structure、induction/rewrite 规则或 hard/frontier oracle-backed cases 时，仍需按 checker-backed evidence 决定是否扩展 Lean 插件 split/merge。

**整个系统机制能不能支持现在的实验？**
factorization、真实 Lean checker/lemma-DAG merge、双 provider AI executor、fixed-entry identity 和 artifact/event 基础已经足够支持下一阶段 pilot。正式论文实验缺的是把这些能力串进同一 paper runner、resume、metrics/report 和 secret-scan 链路。协议核心只在真实 fault/worker pilot 暴露安全问题时再补。

**哪些是专门为实验写的代码？**
`tokenshare.experiments.paper_*`、`run_paper_experiments.py`、`benchmarks/paper/*.jsonl`、paper metrics/report/fault/worker/model cohort/budget 都是实验层。只有为 `gpt-5.6-sol` 新增的 OpenAI transport/config/provenance 支持属于 executor 层；cohort、外部分数和比较策略仍不得进入 executor。

**哪些是系统内部缺的代码？**
当前没有由 fixed-entry identity 修复证明出的新协议核心缺口。候选只包括未来 hard/frontier catalog 可能要求的 Lean split/merge 扩展、pilot 可能暴露的 protocol lease/reassignment/canonical bug，以及真实 provider pilot 可能暴露的通用 executor evidence 缺口；没有证据前不主动扩大系统内部改动。
