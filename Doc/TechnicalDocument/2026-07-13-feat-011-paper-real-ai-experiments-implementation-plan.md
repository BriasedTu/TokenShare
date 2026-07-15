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
- 主要缺的是实验层代码：paper catalog、paper runner、real-AI gate、paper adapters、fault/worker harness、metrics/report。
- 系统内部最明确的缺口是 Lean 拆分能力偏窄。当前 Lean helper 稳定支持顶层 `P ∧ Q` 和 `P ↔ Q`；`P → Q`、`∀ n : Nat, n = n` 等形状当前会返回 unsupported。因此“困难 Lean 题能不能拆”不能笼统回答为能，只能回答：如果困难题仍落在已支持顶层结构里，可以拆；如果困难来自新的逻辑结构，就必须先补 Lean 插件内部 split/merge 规则。
- `budget / plan-only / approve-budget-digest` 不是线上系统能力，不应进入协议核心。它最多是实验层的成本安全阀，用来防止正式 P0-core 真实 API run 误花钱；实现顺序上不应阻塞最小 pilot。

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
| `plan-only` | 只算预计 API 次数、token、成本，不真正跑。 | 实验层可选/成本安全阀，不是线上系统能力。 |

## 当前缺陷归类

| ID | 现在的问题 | 属于哪一层 | 对应要做的事 |
|---|---|---|---|
| D01 | 没有 `run_paper_experiments` 论文实验入口。 | 实验层 | 新增 paper runner 和唯一 CLI。 |
| D02 | 没有 frozen paper catalog；旧 Lean 50 题太浅，旧 factorization 500 是 direct answer。 | 实验层 | 新建 `factorization_catalog.v1.jsonl` 和 `lean_catalog.v1.jsonl`，带难度和 oracle/preflight。 |
| D03 | 旧 Lean 题主要是 `P ∧ Q` / `P ↔ Q`，容易接近满分，没有区分度。 | 先实验层，必要时系统内部 | 先设计更难 child proof；若需要新顶层逻辑结构，再补 Lean split/merge。 |
| D04 | 旧 runner 可以 scripted/fake/deterministic，不能证明真实 AI。 | 实验层 | 实现 real-AI gate，scripted 一律 `paper_eligible=false`。 |
| D05 | direct factorization 500 不是协议 lifecycle，也不是同一 root 内 range worker 扩展性。 | 实验层 | 写 factorization paper adapter，根任务先 deterministic split，再每个 range child 真实 API。 |
| D06 | Lean split 机制还窄，当前不能拆任意困难定理。 | 系统内部，条件触发 | 如果 medium lemma-DAG 或 hard/frontier catalog 需要 implication/forall/nested/induction/rewrite/dependency-aware merge 支持，补 `lean_proof` 插件和 Lean helper。 |
| D07 | 旧故障模拟偏报告层，不保证先真实 API 后 mutation。 | 实验层 | 写 `paper_faults.py`，保存 raw output 后再变换 parsed/submission/lease。 |
| D08 | worker death 还不是独立 worker process death。 | 实验层，可能暴露系统 bug | 写 `paper_workers.py`；如 lease/reassignment 真实失败，再补协议内部。 |
| D09 | 旧 metrics 有些指标是硬写 0/1，不能支撑论文结论。 | 实验层 | 写 `paper_metrics.py`，从 evidence 复算。 |
| D10 | 没有论文所需目录、per-task/per-attempt JSONL、CSV、audit 和 secret scan report。 | 实验层 | 写 `paper_report.py`。 |
| D11 | strong/weak/mixed 模型策略没有统一 paper condition 支持。 | 实验层 | 在 runner/model policy 中实现，不进 executor。 |
| D12 | `budget / plan-only` 容易被误解为线上系统能力。 | 实验层可选 | 只作为正式大规模实验的成本安全阀；不进入协议核心、插件或 executor。 |
| D13 | 当前 Lean paper catalog 太浅，历史 easy/medium/hard 都只是 simple/shallow proof-chain 变化，不能支撑递归证明拆分主张。 | 实验层 + Lean 插件能力提升 | 新增 medium recursive lemma-DAG catalog；hard/frontier 作为复杂/不可解 stress；正式论文 run 前补 catalog schema、split/merge 能力和 checker preflight。 |

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

- [ ] 运行 `.\init.ps1`，确认当前基线仍通过。
- [ ] 确认 active feature 是 `feat-011`。
- [ ] 确认本计划只补 paper experiment path，不回到 `feat-010` replay/audit。
- [ ] 确认 `budget / plan-only` 只作为实验成本安全阀，不作为线上系统需求。

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
- 如果正式 medium lemma-DAG / hard-frontier Lean 题需要 `P → Q`、`∀`、nested conjunction/iff 等新顶层拆分，这一步必须输出 blocked/internal-gap，进入 Task 6B。

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
- [x] 每个 range child 都走真实 `AIAPIExecutor` 生成候选。
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
- [x] 对 child/direct proof 调真实 AI API。
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

### Task 6B: 条件触发的 Lean 系统内部 split/merge 扩展

**层级：** 系统内部，仅当 medium lemma-DAG / hard-frontier catalog 或 Task 5 证明当前 split/merge 机制挡住正式 Lean 复杂度主张时执行。

**Files:**
- Modify: `fixtures/lean_proof_project/TokenShare/SplitRules.lean`
- Modify: `src/tokenshare/plugins/lean_proof/split_strategy.py`
- Modify: `src/tokenshare/plugins/lean_proof/merge_policy.py`
- Modify: `src/tokenshare/plugins/lean_proof/models.py`
- Test: `tests/plugins/lean_proof/test_*.py`
- Then update: `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md`

- [ ] 先写 unsupported regression：证明当前 `P → Q` / `∀` / nested shape 不能生成可 merge 的 split plan。
- [ ] 为一个新结构补 Lean-side deterministic rule。
- [ ] 为同一个结构补 merge skeleton 和 root recheck。
- [ ] Python bridge 只接受 Lean helper 给出的 certificate，不让 AI 决定拆分。
- [ ] 更新 Lean code map，记录新增规则和验证证据。

**修复的缺陷：** D06。

**不做：**

- 不引入 LeanDojo、检索系统或动态 theorem-proving 平台。
- 不把 Lean 规则搬进 `tokenshare.core`。
- 不为了 medium lemma-DAG 或 hard/frontier catalog 让 AI 输出协议级拆分方案。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\plugins\lean_proof -q
```

### 单独模块：Lean 复杂题库与递归 lemma-DAG 要求

**状态：** 必须记录并后续实现；不阻塞 Task 7 / Task 8 / Task 9 的实验基础设施开发，但阻塞正式论文中关于复杂 Lean 递归证明拆分的主张。

**用户要求（2026-07-15）：**

- 当前 `benchmarks/paper/lean_catalog.v1.jsonl` 中已有题目，无论历史 `difficulty` 是 easy、medium 还是 hard，都应按正式论文口径归为 simple / shallow 题。
- 用户要求新增一类更难题：一个 root theorem 由多个 lemma 推导，每个 lemma 又由多个 sublemma 推导，形成递归 lemma dependency DAG；这类题应作为正式口径的 medium 难度。
- 用户还要求再定义更难一类：目前人类也不一定能做出、或非常复杂的混合题目。该类应作为 hard / frontier stress，但若没有可验证 oracle proof，不能伪造成 Lean checker success，只能输出 structured blocked / frontier stress evidence。
- 拆分插件能力必须随之提升，系统要支持这种复杂题，而不是只扩 catalog 文本。

**回应与边界：**

- 可以先继续 Task 7 / Task 8 / Task 9：post-AI fault injection、worker death harness 和 ablation support 不依赖复杂 Lean 题库才能实现；当前 shallow Lean + factorization adapter 足以验证基础机制。
- 但正式 Experiment 1-5 的论文结果不能只用当前 shallow Lean catalog 支撑 Lean 难度、worker scaling 或 protocol ablation 主张；正式 run 前必须回来补 complex Lean catalog 或把 Lean 复杂度主张降级。
- 后续实现应新增 `lean_lemma_graph_catalog.v1.jsonl` 或 `lean_catalog.v2.jsonl`，字段至少包含 `paper_difficulty`、`root_theorem_payload`、`lemma_graph`、`dependency_edges`、`expected_depth`、`expected_leaf_count`、`expected_ai_unit_count`、`merge_plan_shape`、`oracle_proof_package_ref`、`environment_digest`、`preflight_status`。
- Lean 插件/adapter 后续必须支持递归 lemma graph split、proof-file assembly、per-lemma checker evidence、multi-level merge/root recheck、dependency-aware slot integrity，以及必要的 deterministic split/merge 规则；仍禁止 AI 决定协议级拆分。
- Task 7/8/9 实现时不得把 Lean 写死为两个 child 或 `P ∧ Q` / `P ↔ Q`；fault、worker、ablation 数据结构应按 child list / slot list / dependency graph 扩展，避免后续 lemma-DAG 重做。

### Task 7: 实现 post-AI fault injection

**层级：** 实验层。

**Files:**
- Create: `src/tokenshare/experiments/paper_faults.py`
- Test: `tests/experiments/test_paper_faults.py`

- [x] 故障选择必须 deterministic seed。
- [x] fault 必须发生在真实 raw output 持久化之后。
- [x] `false_positive` 修改 parsed candidate/submission。
- [x] `false_negative` 抑制本来存在的 found candidate 或 proof。
- [x] `no_return` 保存 raw 后不提交，让 lease expire。
- [x] `late_submission` 超过 lease deadline 后提交，验证 late result 不污染 canonical。
- [x] `executor_error` 保存 raw 后生成受控 error record，恢复 attempt 再真实调用 API。
- [x] 每条 fault record 同时引用 original ref 和 mutated ref。

**完成记录（2026-07-15）：**

- 新增 `src/tokenshare/experiments/paper_faults.py` 和 `tests/experiments/test_paper_faults.py`；`src/tokenshare/experiments/__init__.py` 已导出 `PaperFaultType`、`FaultTargetDescriptor`、`FaultInjectionRecord`、`FaultInjectionOutcome`、`select_fault_targets()`、`select_fault_target_descriptors()` 和 `inject_post_ai_fault()`。
- `select_fault_targets()` 保留 AI unit id 兼容入口；`select_fault_target_descriptors()` 用 seed + target descriptor digest 做 deterministic 选点，可携带 `slot_key`、`child_logical_key`、`dependency_path`、`lemma_node_id`、`parent_node_id`、`merge_slot`、`artifact_ref` 和 domain-specific target metadata，避免后续 Lean lemma-DAG / factorization range fault 选择重做。
- `inject_post_ai_fault()` 要求 raw output artifact 已由 `ArtifactStore` 持久化后才写 mutation artifact 和 `FaultInjectionRecord`；每条 record 和 mutation artifact 都写入 `target_context` 与 `target_selection_digest`，并校验显式 target 的 `unit_id` / `attempt_id` / `artifact_ref` 不与当前 attempt 和持久化 evidence 脱钩。
- 五类非死亡 fault 均已覆盖：`false_positive` 写 intentionally invalid parsed candidate/submission mutation，`false_negative` 写 suppressed candidate/proof mutation，`no_return` 写 lease-expired/drop-submission record，`late_submission` 写 deadline 后 rejected / `canonical_pollution=false` record，`executor_error` 写 controlled error record 并标记 retry 必须由后续 runner 重新真实调用 provider。
- 严格自查修复：`false_negative` 现在只允许作用于确实存在 found factor 或非空 Lean proof 的 parsed payload；没有 found candidate / proof 时直接报受控 `ValueError`，避免把 no-result 伪造成有效故障注入。
- 边界：本 Task 只实现 post-AI mutation / record 模块，不等于 Experiment 3 正式故障矩阵已跑完；fault-aware runner integration、worker death、metrics/report 和正式 real API recovery attempts 仍属于后续任务。

**修复的缺陷：** D07。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_faults.py -q
```

### Task 8: 实现 worker death harness

**层级：** 实验层；如暴露 lease/reassignment bug，再补系统内部。

**Files:**
- Create: `src/tokenshare/experiments/paper_workers.py`
- Test: `tests/experiments/test_paper_workers.py`

- [x] 每个 worker 是独立 process 或等价的独立执行槽，有独立 worker id。
- [x] 在 25% / 50% / 75% progress kill point 终止 worker。
- [x] coordinator 不死，等待 lease expiry。
- [x] replacement worker 接手并产生新的 attempt。
- [x] 记录 worker pid、kill point、lease expiry、reassignment、replacement attempt。

**2026-07-15 实现记录：** `paper_workers.py` 的记录结构按 generic `PaperAIUnit`、attempt snapshot 和 dependency graph 设计；测试使用多层 dependency DAG，不写死 Lean 两个 child、`P ∧ Q` / `P ↔ Q` 或单层 child proof。当前 slice 只证明 worker death harness、lease expiry 和 replacement attempt 的实验层证据链；正式故障矩阵、runner integration 和真实 API replacement 调用仍在后续任务。

**修复的缺陷：** D08。

**系统内部决策点：**

- 如果 lease expiry/reassignment 事件链已经正确，这一步不改 core。
- 如果 late result 进入 canonical、replacement 没发生、attempt 状态乱了，才补协议内部。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_workers.py tests\core tests\storage -q
```

### Task 9: 实现 ablation support

**层级：** 实验层。

**Files:**
- Create: `src/tokenshare/experiments/paper_ablation.py`
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Test: `tests/experiments/test_paper_ablation.py`

- [x] 支持 FULL、NO_PARSER_POLICY、NO_VERIFICATION、NO_REQUEUE、NO_MERGE_GATE、NO_SLOT_INTEGRITY 等模式。
- [x] 每个 ablation run 仍先真实调用 AI API。
- [x] 关闭机制只发生在实验边界，不改变系统默认安全行为。
- [x] 输出 canonical pollution、premature merge、blocked root、settlement blocked 等 evidence-derived 指标。

**2026-07-15 实现记录：** 新增 `paper_ablation.py` 和 `tests/experiments/test_paper_ablation.py`，并在 `paper_runner.py` 展开 `exp4_real_ai_protocol_ablation` 的 6 个 ablation modes；`paper_budget.py` 对 Experiment 4 使用每档固定 5-task batch 估算，单 repeat 计划为 180 root-runs，3 repeats 对齐权威设计的 540 root-runs。`PaperAblationProfile` 记录并强制 `scope=experiment_boundary`、`requires_provider_attempt_before_ablation=true` 和 `system_default_behavior_changed=false`；`validate_ablation_attempt_coverage()` 要求 generic dependency graph 中每个 AI unit 都已有 provider attempt，并用 artifact inventory 校验 request / raw / provenance / usage / parsed-or-parse-failure refs 的 id/hash/type/schema/source 后才允许应用 ablation；`PaperAblationEvidenceRecord` / `summarize_ablation_evidence()` 从 event/artifact refs 支撑的 evidence records 复算 canonical pollution、premature merge、blocked root、settlement blocked 和 slot integrity violation，clean record 也不能缺少 refs。测试使用多层 generic dependency DAG，不写死 Lean 两个 child、`P ∧ Q` / `P ↔ Q` 或单层 child proof。由于当前 Lean v1 catalog 仍是 shallow，`paper_runner.py` 将 `lean_proof` medium / hard 的 exp4 plan 条件标记为 `paper_eligible_required=false`，直到补齐 medium recursive lemma-DAG catalog 和对应 Lean split/merge/adapter 能力；factorization 和 Lean easy 条件仍保持 paper eligibility 要求。当前 slice 只完成 ablation support 和 evidence summary；正式 Experiment 4 runner integration、真实 API ablation matrix、paper metrics/report/CSV 仍在后续任务。

**修复的缺陷：** D09 的一部分，也支撑 Experiment 4。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_ablation.py -q
```

### Task 10: 实现 strong/weak/mixed model policy

**层级：** 实验层。

**Files:**
- Extend: `src/tokenshare/experiments/paper_models.py`
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Test: `tests/experiments/test_paper_model_policy.py`

- [ ] 支持 `strong_only`、`weak_only`、`mixed`。
- [ ] 记录每个 AI unit 实际使用的 entry id、provider、model。
- [ ] 如果缺少 strong 或 weak entry，Experiment 5 写 structured blocked，不让 Experiment 1-4 失败。
- [ ] 模型策略不进入 executor；executor 只执行被选择的 entry。

**修复的缺陷：** D11。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_model_policy.py -q
```

### Task 11: 实现可选 experiment budget / plan-only

**层级：** 实验层可选成本安全阀；不是线上系统能力。

**Files:**
- Create: `src/tokenshare/experiments/paper_budget.py`
- Extend: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_paper_budget.py`

- [ ] `--plan-only` 只展开 catalog、conditions、repeats 和 split preflight，不调用真实 provider。
- [ ] 输出预计 root-runs、AI units、provider-attempt 上界、token/cost 粗估、digest。
- [ ] `--approve-budget-digest` 只用于正式大规模 run 防误操作。
- [ ] 允许先跳过 full budget，直接跑小 pilot；但正式 P0-core 前建议打开。

**修复的缺陷：** D12，并降低正式实验误花费风险。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_budget.py -q
```

### Task 12: 实现 paper metrics

**层级：** 实验层。

**Files:**
- Create: `src/tokenshare/experiments/paper_metrics.py`
- Test: `tests/experiments/test_paper_metrics.py`

- [ ] 从 per-task/per-attempt/event/fault records 复算 completion rate。
- [ ] 复算 accepted validity rate，和 completion rate 分开。
- [ ] 复算 parser/checker/verifier failure breakdown。
- [ ] 复算 detection/recovery/false accept/reassignment/cost overhead。
- [ ] 复算 worker speedup、throughput、parallel efficiency。
- [ ] 不能沿用旧 helper 里的硬写 0/1 指标作为论文统计。

**修复的缺陷：** D09。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_metrics.py -q
```

### Task 13: 实现 paper report 和 audit outputs

**层级：** 实验层。

**Files:**
- Create: `src/tokenshare/experiments/paper_report.py`
- Test: `tests/experiments/test_paper_report.py`

- [ ] 写 `suite_manifest.json`。
- [ ] 写 `input_catalog_manifest.json`。
- [ ] 写 `conditions.jsonl`。
- [ ] 每个 run 写 `run_manifest.json`、`per_task_results.jsonl`、`per_attempt_results.jsonl`、`fault_injections.jsonl`、event log 和 artifacts。
- [ ] 写 feasibility/scalability/robustness/ablation/model policy CSV。
- [ ] 写 `paper_eligibility_report.json`。
- [ ] 写 `secret_scan_report.json`。

**修复的缺陷：** D10。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_paper_report.py -q
```

### Task 14: 串起完整 paper CLI

**层级：** 实验层。

**Files:**
- Extend: `src/tokenshare/experiments/run_paper_experiments.py`
- Test: `tests/experiments/test_run_paper_experiments_cli.py`

- [ ] 支持 `--experiments exp1,exp2,exp3,exp4,exp5`。
- [ ] 支持 `--real-transport`，默认拒绝 scripted paper run。
- [ ] 支持 `--ai-api-config local/ai_api_smoke.local.json`。
- [ ] 支持 `--strong-entry-id`、`--weak-entry-id`。
- [ ] 支持 `--worker-levels`、`--optional-worker-levels`、`--repeats`、`--seed-family`。
- [ ] 支持 `--pilot` 或等价小规模 suite。
- [ ] 输出路径固定为 `outputs/experiments/paper_v1/` 下的 suite id 子目录。

**修复的缺陷：** D01、D04、D10、D11。

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments\test_run_paper_experiments_cli.py -q
```

### Task 15: 跑 pilot，而不是直接跑 P0-core

**层级：** 实验执行。

**Files:**
- Output only: `outputs/experiments/paper_v1/` 下的 pilot suite id 子目录。

- [ ] 每个 domain 每档至少 1 题。
- [ ] worker levels 先用 1 和 3。
- [ ] fault 先选 1-2 类低比例。
- [ ] Lean medium lemma-DAG / hard-frontier catalog 如果大量 unsupported，回到 Task 6B；不得把当前 shallow v1 的 legacy hard 标签当作正式 hard catalog。
- [ ] 检查 provider 429、timeout、parse failure、checker failure、成本、secret scan。

**修复的缺陷：** 在花大钱前发现 schema/prompt/quota/Lean split 真实问题。

**示例命令：**

```powershell
$env:PYTHONPATH='src'
$env:TOKENSHARE_STRONG_ENTRY_ID='glm_5_2__sf_key_1'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --output-root outputs/experiments/paper_v1 `
  --experiments exp1,exp2,exp3,exp4 `
  --pilot `
  --real-transport `
  --ai-api-config local/ai_api_smoke.local.json `
  --strong-entry-id $env:TOKENSHARE_STRONG_ENTRY_ID `
  --worker-levels 1,3 `
  --repeats 1 `
  --seed-family 1
```

`TOKENSHARE_STRONG_ENTRY_ID` 的值必须是本地 gitignored config 里的真实 entry id；不要把 key 或 secret 写进命令、日志或文档。

### Task 16: 根据 pilot 决定系统内部是否补强

**层级：** 决策点。

- [ ] 如果 factorization adapter 能完成 range child -> verifier -> merge，factorization 系统内部不改。
- [ ] 如果 Lean unsupported 主要来自新顶层结构，执行 Task 6B。
- [ ] 如果 late/no_return/worker_death 暴露 lease/canonical/reassignment bug，补 `tokenshare.core` / `tokenshare.storage`，并写 targeted core/storage tests。
- [ ] 如果 executor evidence 缺 provider-attempt correlation 或 cancellation-safe provenance，才补 `tokenshare.executors.ai_api` 字段；不要把 fault policy 放进 executor。

**修复的缺陷：** 避免提前乱改系统内部，也避免实验层硬绕系统 bug。

### Task 17: 正式运行 Experiment 1

**层级：** 实验执行。

- [ ] 2 domains × 3 difficulties × 10 tasks × 3 repeats。
- [ ] 固定 strong entry。
- [ ] 输出 feasibility table。
- [ ] 报告 completion rate 和 accepted validity rate，不能只报 accepted outputs 100% correct。

**验证对象：** 跨领域可行性与难度。

### Task 18: 正式运行 Experiment 2

**层级：** 实验执行。

- [ ] worker levels 1, 3, 10, 30。
- [ ] 每个 domain / paper difficulty 固定 5-task batch；Lean 当前 shallow v1 不能填 medium lemma-DAG / hard-frontier batch。
- [ ] repeats 5。
- [ ] factorization scaling 必须是同一 root 内 range children 并行，不能用 direct 500 代替。
- [ ] 输出 speedup、throughput、efficiency、429/retry。

**验证对象：** worker 扩展性。

### Task 19: 正式运行 Experiment 3

**层级：** 实验执行。

- [ ] rate faults：`false_positive`、`false_negative`、`no_return`、`late_submission`、`executor_error`。
- [ ] worker death 单独矩阵：25%、50%、75% kill point。
- [ ] 每个 fault record 必须引用 original/mutated refs。
- [ ] 输出 detection、false accept、recovery、completion、reassignment、cost overhead。

**验证对象：** 故障检测、隔离、恢复边界。

### Task 20: 正式运行 Experiment 4 和 Experiment 5

**层级：** 实验执行。

- [ ] Experiment 4 跑 6 个 ablation modes，输出 ablation table。
- [ ] Experiment 5 只在 strong/weak 模型 entry 都可用时跑。
- [ ] 如果缺 weak entry，Experiment 5 写 blocked，不影响 Experiment 1-4。
- [ ] mixed 策略必须记录每个 AI unit 的真实模型选择。

**验证对象：** 协议机制贡献与模型策略。

### Task 21: 收尾验证和状态同步

**层级：** 文档/验证。

**Files:**
- Update: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md` if experiment infrastructure code changes.
- Update: `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md` only if Lean plugin internals change.
- Update: `feature_list.json`
- Update: `progress.md`
- Update: `session-handoff.md`
- Update paper table/figure inputs after real runs.

- [ ] Targeted tests pass.
- [ ] `tests/experiments` pass.
- [ ] Plugin/executor impact suites pass.
- [ ] Full `.\init.ps1` pass.
- [ ] Secret scan pass.
- [ ] Evidence paths and run ids written to progress/handoff.

**验证：**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests\experiments -q
conda run -n tokenshare python -m pytest tests\executors tests\plugins\factorization tests\plugins\lean_proof -q
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

## 推荐实施批次

### 第一批：让实验能小规模真实跑

- Task 0
- Task 1
- Task 2
- Task 3
- Task 4
- Task 5
- Task 14 的最小 CLI
- Task 15 pilot

第一批完成后，应该能回答：当前题库是否太简单、Lean hard 是否真的被 split 机制挡住、真实 API evidence 是否完整。

### 第二批：让鲁棒性和 worker 实验可信

- Task 7
- Task 8
- Task 9
- Task 10
- Task 12
- Task 13
- Task 16

第二批完成后，应该能回答：post-AI fault、worker death、ablation 是否真的支撑论文 robustness 主张。

### 第三批：正式数据

- Task 11，如果需要正式成本安全阀。
- 回到“Lean 复杂题库与递归 lemma-DAG 要求”：若论文要保留 Lean 递归拆分、难度、worker scaling 或 ablation 主张，正式数据前必须补 medium recursive lemma-DAG catalog 和对应 split/merge/adapter 能力；否则 suite manifest 和论文文字必须降级 Lean claim，只把当前 v1 当 simple/shallow。
- Task 17
- Task 18
- Task 19
- Task 20
- Task 21

第三批完成后，才进入论文图表和文字更新。

## 对用户问题的直接答案

**这些实验现在能不能跑？**
不能直接跑成论文结果。当前 paper schema、catalog、budget/plan-only skeleton、real-AI eligibility gate、Lean catalog checker-backed preflight、factorization paper adapter 和 Lean paper adapter 已有基础；仍缺真实 API pilot 执行路径、post-AI fault / worker-death harness、worker scaling condition expansion、ablation/model policy、evidence-derived metrics/report、secret scan wiring 和正式 Experiment 1-5 run。2026-07-15 新增决策还要求正式论文 Lean 主张必须补 medium recursive lemma-DAG catalog；当前 `lean_catalog.v1.jsonl` 全部只算 simple / shallow。旧实验能跑，但只能算 regression/calibration。

**要跑还需要哪些东西？**
优先需要实验层代码，不是优先改系统核心：adapters、真实执行 runner、faults、workers、metrics、report、CLI 和正式运行编排。系统内部只在 pilot 证明挡路时补。

**准备的问题是不是太简单？**
是。旧 Lean 50 和当前 `lean_catalog.v1.jsonl` 都偏简单，因为主要是 `P ∧ Q` 和 `P ↔ Q`，child proof 常接近直接假设或短 implication chain。正式论文口径下，当前 v1 全部归为 simple / shallow；用户要求的“root theorem -> 多个 lemma -> 多个 sublemma”的递归 lemma-DAG 才算 medium；更难的 hard / frontier 可以是复杂混合题或人类也未必能完成的 stress case，但没有固定 oracle proof 的题只能作为 structured blocked / frontier stress，不能伪造成 checker success。

**Lean 拆分机制是不是还没做好？**
当前不是“完全没做好”，而是“范围窄”。它可以拆已支持的顶层结构；不能拆任意困难 theorem。困难如果只是 shallow proof steps 变多，可以先不改 split；如果困难来自递归 lemma-DAG、new top-level structure、proof-file assembly、induction/rewrite 或 dependency-aware merge，就要补 Lean 插件内部 split/merge 和 adapter schema。

**整个系统机制能不能支持现在的实验？**
factorization 和真实 Lean checker、AI executor、artifact/event 基础已经足够支持 pilot。正式论文实验缺的是把这些能力串起来的 paper experiment layer。协议核心只在真实 fault/worker pilot 暴露安全问题时再补。

**哪些是专门为实验写的代码？**
`tokenshare.experiments.paper_*`、`run_paper_experiments.py`、`benchmarks/paper/*.jsonl`、paper metrics/report/fault/worker/model policy/budget 都是实验层。

**哪些是系统内部缺的代码？**
当前明确候选只有 Lean split/merge 扩展；其次是 pilot 可能暴露的 protocol lease/reassignment/canonical bug；最后是 executor evidence 字段不足。没有证据前，不主动扩大系统内部改动。
