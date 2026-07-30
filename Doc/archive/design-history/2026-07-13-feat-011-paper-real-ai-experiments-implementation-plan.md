# Paper Real AI Experiments Rapid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every feature or bug fix must follow `superpowers:test-driven-development`; every completion claim must follow `superpowers:verification-before-completion`.

> **2026-07-22 架构覆盖条款：** Experiment 1–5 的矩阵、样本、模型、预算和报告要求继续使用本文；但本文中让 paper adapter/runner 直接执行 canonical、requeue、worker recovery、merge/completion 的落地方式已被 `2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md` 覆盖。后续 agent 必须先完成系统 runtime 迁移，让实验只设条件和观察结果，再运行新的正式实验。Lean 保留 catalog/脚本预写的固定 lemma-DAG，由 Lean 插件校验并生成 certificate，不要求改成通用自动 theorem 拆分。

> **2026-07-23 当前状态覆盖条款：** system runtime 迁移 Task 1-9 已落地；正常 FULL/fault/ablation 由 `paper_dispatcher` 进入 `local_runtime` / `ProtocolEngine`。公开 adapter direct API 仅作 deprecated 历史/selector regression 兼容。本文下方仍用“当前”“尚未”描述 adapter、formal runner、worker harness 或旧 catalog 的段落，均是迁移前的历史实施快照，不代表 2026-07-23 当前生命周期所有权或完成状态。

> **2026-07-26 证据门禁覆盖条款：** Task A–11 的设施实现和本次反伪造 blocker 修复已通过用户规定的九文件最低验收 `246 passed`。正式资格只走持久化 `attempt → task → condition → experiment → suite → report` 聚合；Exp2 critical path、Exp3 matched baseline/worker death、Exp4 hook/FULL 配对、Exp5 provider-attempt identity inventory 与 429 sensitivity 均按权威证据 fail closed。下方关于 Gate C“等待接入”、旧简化 metrics/report 或可由 capturing 证明正式资格的表述只作历史计划，不代表当前实现。真实 provider Experiment 1–5、Full 和发布门禁仍未执行，`feat-011` 保持 `in-progress`。

> **2026-07-22 安全范围覆盖条款：** 本地研究原型不做 external-input attack、tamper/fabrication、path/injection、security fuzzing、auth/signature/ACL、恶意 plugin/executor/worker 或 Byzantine hardening。错误处理只实现 `false_positive,false_negative,no_return,late_submission,executor_error` 五类 rate-fault；worker death 和 ablation 仅按已冻结实验矩阵执行。本文历史 Task 中已有的 evidence consistency/tamper regression 只保留为既有正常流程回归，不授权新增攻击防护。

> **2026-07-24 Exp2 参数覆盖条款：** EPD-003 已把 Experiment 2 改为仅使用全部 166 道 hard Factorization、每 root 20-way split、worker `1/3/7/10/30/50`、每档 2 遍；Lean 不进入 Exp2，正式规模为 `1,992` root-runs / `39,840` planned AI units。本文下方 Factorization 三档 + Lean 5-task、worker `1/3/10/30`、5 repeats、`10,300` root-runs 和 optional `100/300` 均为旧计划，不得再实现。

> **2026-07-24 Exp3 参数覆盖条款：** EPD-004 只把五类 rate-fault 和 worker-death 的每个 condition 从 3 repeats 改为 2 repeats；其他矩阵不变。新规模为 rate-fault=`35,120`、worker-death=`6,036`、Exp3=`41,156` root-runs。本文下方 3 repeats 和旧规模只作历史计划。

> **2026-07-24 Exp4/Exp5 与剩余设施覆盖条款：** EPD-005 把 Exp4 固定为 `FULL + 4`、7,725 root-runs；EPD-006 把 Exp5 改为独立使用 Exp1 全部 hard roots（Factorization 166 + Lean 45）、36 conditions、1,899 root-runs，不再复用 Exp2 slice。Exp2–Exp5 当前行为/指标 blocker、文件归属和实施顺序统一以 `2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md` 为准；本文后续旧六模式、4,635-root Exp5、共享 Exp2 slice 和未接线 summarizer 表述不得继续实施。

**Goal:** 在不删减 Experiment 1–5、也不跳过 Lean 3×3 正式实验前置能力的前提下，用最少的工程 Task 完成真实 AI API 实验、审计输出和论文表格。

**Architecture:** 复用已完成的 catalog/adapters、fault/worker/ablation support、fixed-entry identity、budget gate 和 Exp1 orchestrator。2026-07-18 审核后采用“Task 14 语义修复 + 公共契约薄层冻结、Exp1–5 独立模块并行开发、共享入口串行集成、预算/pilot/formal run 串行执行”的调度。当前 9×15 manifest 只满足机械计数，不满足 135 个语义不同 roots 与 hard/medium 难度分层，因此正式预算和 provider 调用继续 blocked。Experiment 1–4 固定使用 SiliconFlow GLM-5.2，Experiment 5 才执行三端点对比。`tokenshare_latest_real_plugin_experiment_design.md` 是实验口径唯一权威，本文把关键冻结字段、依赖和执行顺序落实为 agent 可直接执行的清单。

**Tech Stack:** Python 3、pytest、SQLite/JSON/JSONL、本地 artifact store、Factorization/Lean plugins、Lean/lake checker、Phase 7 AI API executor、SiliconFlow/OpenAI transports。

---

## 1. 快速版边界

“快速”只减少工程 Task 数量，不减少实验要求：

- Experiment 1–5 全部必做；不得静默删除 domain、difficulty、worker level、fault type、ablation mode 或三模型 endpoint。
- Exp1 pilot 可以使用当前 8-root 冻结 slice；正式 Exp1 不能把 shallow Lean case 冒充 `medium_lemma_dag` 或 `hard_frontier`。
- 正式 Exp1 前必须完成 Lean `simple / medium_lemma_dag / hard_frontier × pure_logic / function_set / induction` 的 3×3 catalog 与拆分机制复核。
- 每个 Lean cell 先有 1–2 个 checker-backed golden cases；只有复核通过后才能扩成每格恰好 15 个可审计 case，九格合计 135 个 Lean roots。
- 无固定 oracle、checker 或 deterministic split/merge 能力的 cell，必须在实际 feasibility review 后形成 evidence-backed blocked/degraded 结论；不能因为实现尚未开始就直接跳过。
- pilot 不进入论文主表；所有论文结果必须实际调用真实 AI API。
- 除 Experiment 5 外，Experiment 1–4 的 pilot、正式 condition、故障恢复 attempt 和消融 mode 全部固定为 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`；缺少配置、key、smoke 或 identity evidence 时结构化 blocked，不得自动换模型。
- 每次真实调用前重新生成预算、核对 digest、取得对应批准，并启用 provider/token/cost/time hard limits。
- raw output、request/response provenance、attempt/event/artifact refs 必须先持久化；resume/replay 不得重新调用 provider。
- `scripted`、`fake`、旧 deterministic suite、direct 500、Lean 50 和 `lean_stub` 只能用于回归或校准。
- 一次只推进 `feat-011`。允许按本计划的文件所有权并行开发 Exp1–5 独立模块，但公共契约、共享 runner/CLI/metrics/report、状态源、预算批准和真实运行仍保持单一 owner 与串行 gate。
- 当前工作树已把 readiness 机械契约迁移到 9×15、135 个唯一 case IDs、0 blocked 和 480 AI units；但 2026-07-18 语义审核发现八个 v2 passed cells 每格只有一个 theorem/DAG 形状，三个 hard cells 是 medium 模板副本。Task 14 因实验有效性缺陷重新打开。不得 `reset`、`clean` 或覆盖现有改动。

## 2. 执行 agent 必须冻结的实验口径

| 范围 | 不可自行改变的要求 |
|:---|:---|
| Lean 正式 catalog | `simple / medium_lemma_dag / hard_frontier × pure_logic / function_set / induction` 九格；每格恰好 15 道，合计 135 道；manifest 必须逐格证明 count、oracle/checker preflight 和 digest。 |
| 正式 Experiment 1 | Factorization 30 roots + Lean 135 roots = 165 unique roots；3 repeats = 495 root-runs。必须使用完整 Lean 3×3 catalog，不得抽成每档 10 道。 |
| 正式预算计数 | Exp1=495、Exp2=600、Exp3 rate-fault=705、Exp3 worker-death=108、Exp4=540，所以 P0-core=2448 root-runs；Exp5=270，三端点 cohort 完整时 P0-full=2718。root-runs 不等于 provider calls，仍须由 split preflight 展开 provider-attempt 上界。 |
| Experiment 1–4 模型 | `provider_family=siliconflow`、`provider_model_id=zai-org/GLM-5.2`、`model_entry_id=glm_5_2_exp1_baseline`、`temperature=0.0`、`enable_thinking=false`；安全配置来自 `benchmarks/paper/exp1_baseline_provider_config.v1.json`。 |
| Experiment 3 overhead | 必须用 matched no-fault baseline 输出 `matched_baseline_condition_id,wall_clock_overhead_ms,wall_clock_overhead_ratio,token_overhead,token_overhead_ratio,cost_overhead`；分母为 0 时输出 `null + zero_baseline_denominator`。 |
| Experiment 3 worker death | P0 固定 `worker_count=10`、`dead_worker_count=1,3`、kill progress 25%/50%/75%；输出 coordinator 是否继续、恢复时间、重试/重分配、slot 完整度、root completeness 和 accepted validity。 |
| Experiment 4 error escape | 输出 `exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability`；NO_REQUEUE 或零分母场景不得用 0 伪造可适用比率。 |
| Experiment 5 | 仅比较冻结的三个具名 model-provider endpoints；不恢复 strong/weak/mixed routing，也不受 Experiment 1–4 baseline 单模型约束。 |

Lean 的 5-task 分层 slice 在 Experiment 2、4、5 间统一：simple=`pure_logic:2,function_set:2,induction:1`，medium=`pure_logic:1,function_set:2,induction:2`，hard=`pure_logic:2,function_set:1,induction:2`。runner 必须冻结具体 task ids 和 slice digest；不同 worker、mode、model endpoint 或 repeat 不得重新抽样。Experiment 3 的 Lean 3-task slice 固定为每个 topic family 各 1 道。

新增 condition schema 使用 `tokenshare.paper_condition.v2`，至少补入 `topic_family,dead_worker_count,kill_progress,matched_baseline_condition_id`。任何 runner、metrics 或 report 实现如果仍只接受 v1 字段，必须先写 RED 测试并完成显式 schema/version 迁移，不能在 report 层猜值。

## 3. 当前状态与编号映射

这里的 Task 11/12 就是原 35-Task 计划中的 Task 11/12，不是重新定义的同名任务：

| Task | 状态 | 与旧计划的关系 |
|:---|:---:|:---|
| 11 | 完成并获批准 | 原 Task 11：冻结并批准 Exp1 最小 pilot 矩阵与预算。 |
| 12 | 完成 | 原 Task 12：实现 Exp1 双领域 execution orchestrator。 |
| 13 | 完成 | 合并原 Task 13–16：共享 metrics/report/audit、真实 Exp1 pilot 与 pilot 修复；未运行正式 Exp1。 |
| 14 | 重新打开 / semantic blocked | 机械 15/135 契约已实现；必须修复语义重复、hard 冒充 medium 和逐格 golden 证据缺口。 |
| Gate B | 可立即串行冻结 | 新文件中的公共开发接口；不接 runner、不批准预算、不调用 provider。 |
| 15D–19D | Gate B 后可并行 | Experiment 1–5 独立模块与 tests；只用 synthetic/scripted fixtures，不能生成正式结果。 |
| Gate C | 等待并行模块 | 串行接入 shared runner/CLI/metrics/report，并执行跨实验回归。 |
| 15R–19R | Gate A + Gate C 后串行 | 分别 plan-only、预算批准、pilot、正式运行 Exp1–5。 |
| 20 | 未开始 | 五实验联合审计与论文输入。 |

Task 0–10 的已完成基础不再逐项展开：paper models/catalog、real-AI eligibility、Factorization/Lean adapters、最小 medium lemma-DAG、fault/worker/ablation support、三模型 cohort、OpenAI transport、fixed-entry identity 和 plan-only CLI 已存在并有回归测试。

## 4. 唯一执行顺序

### Task 11: 冻结并批准 Exp1 最小 pilot

**状态：已完成。**

- [x] 冻结 `benchmarks/paper/exp1_minimal_pilot_profile.v1.json` 与安全 provider config。
- [x] 固定 8 个 roots（7 executable、1 structured blocked）和 22 个 AI units。
- [x] 复算并批准 provider/token/cost/time/disk 上界；批准产物 `provider_calls_made=0`。
- [x] 批准 digest：`sha256:74cf22c52a2aca22891e3271823ab262879da82d027682cfa8935f3652c72414`。

### Task 12: 实现 Exp1 双领域 execution orchestrator

**状态：已完成，但当前工作树尚未提交。**

**Files:**

- `src/tokenshare/experiments/paper_runner.py`
- `src/tokenshare/experiments/run_paper_experiments.py`
- `tests/experiments/test_paper_execution_runner.py`
- `tests/experiments/test_run_paper_experiments_cli.py`

- [x] 展开 8 conditions/runs/tasks 与 22 AI units，并按 domain 调用现有 adapters。
- [x] 在调用前校验 budget、approval、fixed-entry identity、real transport 和 hard limits。
- [x] 原子持久化 plan、run/task/attempt、events、artifact index 和 evidence manifest。
- [x] complete-only resume/replay 校验 artifact size/hash 且当前调用为零。
- [x] fake/scripted、budget-exhausted、不完整 suite 均不能 paper-eligible。
- [x] CLI 支持 `--pilot`、baseline entry、hard limits、resume/replay 和规范输出路径。

**验证基线：** Task 11/12 关键回归 37 passed；完整 `.\init.ps1` 为 663 passed、1 skipped。尚未运行真实 pilot。

### Task 13: 完成共享 metrics/report 并运行 Exp1 最小真实 pilot

**状态：已完成。** 依赖 Task 12；本 Task 未运行正式 Exp1。

**Files:**

- Create: `src/tokenshare/experiments/paper_metrics.py`
- Create: `src/tokenshare/experiments/paper_report.py`
- Extend: `src/tokenshare/experiments/paper_runner.py`
- Extend: `src/tokenshare/experiments/run_paper_experiments.py`
- Create: `tests/experiments/test_paper_metrics.py`
- Create: `tests/experiments/test_paper_report.py`
- Extend: `tests/experiments/test_paper_execution_runner.py`
- Extend: `tests/experiments/test_run_paper_experiments_cli.py`

- [x] 运行 `git status --short` 和 `.\init.ps1`，核对 Task 11/12 基线；不清理工作树。
- [x] RED：tampered/incomplete/fake evidence 生成论文 metrics/report 必须失败或 `paper_eligible=false`。
- [x] 从 run/task/attempt/event/artifact evidence 复算 completion、accepted validity、parser/checker/verifier failure、attempts、tokens、latency、cost 和 failure stage。
- [x] 生成 `metrics.json`、`task_metrics.csv`、`summary.csv`、`audit_summary.json`、`report.md` 和 pilot-only Exp1 feasibility CSV。
- [x] report 前验证 evidence manifest、artifact refs、suite lifecycle、budget、real transport、secret scan 和 paper eligibility。
- [x] fake/scripted evidence 只用于回归并明确不可 paper-eligible。
- [x] 重新生成 Exp1 pilot plan/budget；只有 frozen digest、批准状态和 hard limits 全部匹配时才运行真实 API。
- [x] 运行 8-root Exp1 最小真实 pilot；检查两个 domain、structured-blocked row 零调用、usage、artifacts、resume/replay 零调用和 metrics/report。
- [x] pilot 输出必须 `pilot_only=true`，不得生成正式论文主表。
- [x] 只修复 pilot 证明存在的共享 executor/adapter/evidence/report 问题；任何 profile/config 变化都重新 plan/approve 并重跑受影响 slice。
- [x] 更新 code map、`progress.md`、`feature_list.json`、`session-handoff.md`，运行 targeted tests 与 `.\init.ps1`，然后停在 Task 14 前。

**完成门槛：** 共享 metrics/report 能从 evidence 重算，真实 8-root pilot 产生完整审计证据，pilot 问题完成闭环。API key、批准或 provider 可用性缺失时必须停在结构化 gate，不能把 Task 13 标为完成。

### Task 14: 完成 Lean 3×3 catalog 与 split/merge readiness

**依赖：** Task 13 的真实 pilot 结果。**当前机械计数已通过但语义审核未通过；这是所有正式运行的硬依赖。**

**Files:**

- Extend/Create: `benchmarks/paper/lean_*.jsonl`
- Modify: `benchmarks/paper/lean_task14_3x3_readiness.v1.json`
- Extend: `src/tokenshare/experiments/paper_catalog.py`
- Extend: `src/tokenshare/experiments/lean_paper_adapter.py`
- Conditional Extend: `src/tokenshare/plugins/lean_proof/`
- Extend/Create: `tests/experiments/test_lean_*catalog*.py`
- Modify: `tests/experiments/test_lean_task14_readiness.py`
- Extend: `tests/experiments/test_lean_paper_adapter.py`
- Conditional Extend: `tests/plugins/lean_proof/`
- Update: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`

- [x] 把 readiness 机械测试迁移为每格 15、合计 135、exact selected IDs、预算只计 selected passed rows 和零 provider call。
- [ ] 新增 canonical semantic fingerprint：忽略 `case_id`、construction seed、theorem/node 名称、library-context case ID 和 oracle package ID，只对 root parameters/statement、node statements/kinds/depths、dependency edges、merge shape、expected depth/leaf/AI units 和必要 oracle theorem identity 做规范化；每格 15 个 selected cases 必须得到 15 个不同 root fingerprints。
- [ ] 新增跨难度分层 RED：三个 `hard_frontier` cell 不得与对应 `medium_lemma_dag` cell 共享 root/DAG fingerprint；hard 题型必须符合权威设计中的深 DAG、nested induction、rewrite/theorem reuse、case split、extensionality/quantifier 或跨主题组合之一，并拥有独立 fixed oracle proof package。
- [ ] 为九格各执行 1–2 个 golden split → proof-file assembly → dependency-aware merge → root recheck；不得因“有 passed row”自动把能力标为 ready。
- [ ] 统一 schema：`paper_difficulty`、`topic_family`、`topic_family_version`、`construction_rule_id`、environment/oracle/checker metadata。
- [ ] 建立 `simple / medium_lemma_dag / hard_frontier × pure_logic / function_set / induction` 九格 manifest，shallow-v1 只能进入 simple。
- [ ] 每格先实现并本地验证 1–2 个 checker-backed golden cases；记录 oracle package hash、environment digest、preflight time 和失败原因。
- [ ] 用 golden tests 判断现有 deterministic split、proof-file assembly、dependency-aware merge/root recheck 是否支持目标 theorem shapes。
- [ ] 若现有规则已支持，记录 evidence-backed no-change；若被证明阻塞，只为已暴露结构逐个补 Lean-side deterministic rule、merge skeleton 和 root recheck。
- [ ] Python bridge 只接受 Lean helper certificate；AI 不得决定协议级拆分。
- [ ] golden readiness 通过后，按固定构造规则扩成每格恰好 15 个语义不同、可审计 cases，九格合计 135 个，并批量 checker preflight；任何缺格、语义重复、ID 重复、难度标签伪升级或 preflight failure 都使 catalog freeze 失败。
- [ ] 对仍不可行的 cell 记录已经执行过的 feasibility evidence、具体 blocker 和降级主张；不得用“尚未实现”直接替代复核。
- [ ] 冻结 catalog/suite version、digest、抽样规则和正式预算输入，运行全部 Lean plugin/adapter/catalog tests 与 `.\init.ps1`。
- [ ] 更新状态后停在 Task 15 前。

**完成门槛：** 九格均有 golden feasibility 证据且各自冻结 15 个 checker-backed cases；需要的 split/merge 能力已实现并回归。任一 cell 无法形成 15 个可采信 cases 时 Task 14 和正式 Experiment 1 保持 blocked，并记录经过实际复核的明确 blocker；仅保留旧 shallow/最小 medium fixture 不算完成。

### 2026-07-18 并行调度覆盖条款

以下条款覆盖下方 Task 15–19 中“开发、pilot、formal run 同一 Task 串行完成”的旧调度，但不改变实验口径：

1. Lane A 先修 Task 14 semantic catalog；修复前当前 manifest/digest 不得用于正式预算。
2. Gate B 可从 reviewed `semantic_blocked` checkpoint 新增 `paper_experiment_contracts.py` 及 contract tests，冻结 `FrozenCaseSelection`、`PaperExecutionContext` 和模块 Protocol；不得修改 shared runner/CLI。
3. Gate B 合并后，Task 15D–19D 在独立 worktree/branch 中并行创建各自模块和 tests；并行 agents 不修改 shared files、状态文档或其他实验模块。
4. Gate C 由 integration owner 在 Task 14 semantic repair 与五个模块都通过审核后串行接入 shared runner、CLI、metrics/report、budget 和 exports。
5. Task 15R–19R 仍严格按 Exp1 → Exp2 → Exp3 → Exp4 → Exp5 顺序执行 plan-only、预算审核/批准、pilot、正式运行和专项报告。并发开发不授权并发 provider 调用。
6. 具体文件所有权、验收命令和全部可复制 prompts 见 `2026-07-18-feat-011-parallel-experiment-prompt-pack.md`。

### Task 15: 预算批准并正式运行 Experiment 1

**依赖：** Task 14 readiness 完成。

**Files:**

- Extend: `src/tokenshare/experiments/paper_runner.py`
- Extend: `src/tokenshare/experiments/paper_budget.py`
- Extend: `src/tokenshare/experiments/paper_metrics.py`
- Extend: `src/tokenshare/experiments/paper_report.py`
- Extend: `tests/experiments/test_paper_execution_runner.py`
- Create/Extend: `tests/experiments/test_paper_exp1_formal.py`

- [ ] 从冻结 Factorization catalog 取每档 10 roots（30 个），并使用 Task 14 Lean 3×3 完整 catalog 的每格 15 roots（135 个），合计 165 unique roots；不得对 Lean 正式矩阵再抽样。
- [ ] 固定 3 repeats（495 root-runs）、worker count、seed/order、timeout/request limits、suite version，以及 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline` identity 和 request controls。
- [ ] 生成正式预算与 digest；在用户批准前保持 `provider_calls_made=0`。
- [ ] 获批后在 hard limits 内运行正式 Exp1，禁止 pilot 输出混入主表。
- [ ] 输出逐 task evidence 和按 `domain/paper_difficulty/topic_family` 汇总的 case count、completion、accepted validity、时间、tokens、cost、failure breakdown 及 `highest_observed_valid_completion_difficulty`。
- [ ] 复核 Task 14 blocked/degraded cells 的论文限定语，不用 shallow case 补齐。
- [ ] 更新状态、运行完整验证，停在 Task 16 前。

### Task 16: 扩展并运行 Experiment 2（worker 扩展性）

**依赖：** Task 15。

**Files:** `paper_runner.py`、`paper_workers.py`、`paper_metrics.py`、`paper_report.py`、CLI 及对应 experiment tests。

- [ ] 展开强制 worker levels `1,3,10,30`；`100,300` 不满足 AI-unit/quota 条件时写 `unsupported_worker_level`。
- [ ] 每 domain/difficulty 使用相同 5-task batch、catalog digest、prompt/timeout/seed，重复 5 次；Lean 每档用预注册 2/2/1 slice 覆盖三个 topic families。
- [ ] 所有 conditions 固定使用 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`，并在结果中保留 configured/requested/resolved model identity；不得随 worker level 换模型。
- [ ] Factorization 测同一 root 内 range children；Lean 记录 root/child throughput 与 lemma-DAG critical path。
- [ ] 从 timestamps/dependencies 复算 speedup、efficiency、throughput，区分 provider 429。
- [ ] 在同一 Task 内完成 TDD、预算批准、小 pilot、正式 Exp2、`paper_table_exp2.csv`、状态和完整验证。

### Task 17: 扩展并运行 Experiment 3（fault 与 worker death）

**依赖：** Task 16。

**Files:** `paper_runner.py`、`paper_faults.py`、`paper_workers.py`、metrics/report 及对应 fault/death execution tests。

- [ ] rate-fault 只展开 `false_positive,false_negative,no_return,late_submission,executor_error` 和权威 domain-specific rates/repeats。
- [ ] Lean rate-fault 的 3-task slice 固定为 `pure_logic/function_set/induction` 各 1 道，并冻结 task ids/slice digest；不同 fault/rate/repeat 不得重新抽样。
- [ ] 在真实 raw output 持久化后按指定 injection point 注入，保留 original/mutated refs 与固定 target ids。
- [ ] 所有原始/恢复 attempts 固定使用 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`，fault 不得触发 provider/model failover。
- [ ] worker death 固定 `worker_count=10`，分别终止 1 个和 3 个独立 executor worker processes；coordinator 保持运行并测试 25%/50%/75% lease replacement。
- [ ] 正确复算 detection、false-accept、recovery、completion、`wall_clock_overhead_ms,wall_clock_overhead_ratio,token_overhead,token_overhead_ratio,cost_overhead`；synthetic mutation 不伪造 token，matched baseline 必须同 task/model/seed/worker/request profile，零 baseline 分母写 `null + zero_baseline_denominator`。
- [ ] worker-death report 输出 `dead_worker_count,kill_progress,coordinator_continued,recovery_latency_ms,retry_count,reassignment_count,required_slot_count,recovered_slot_count,result_completeness_rate,root_output_complete,accepted_validity`。
- [ ] 在同一 Task 内完成 TDD、预算批准、小 pilot、正式 Exp3、fault/recovery reports、状态和完整验证。

### Task 18: 扩展并运行 Experiment 4（五模式消融）

**依赖：** Task 17。

**Files:** `paper_runner.py`、`paper_ablation.py`、metrics/report 及对应 ablation execution tests。

- [ ] 按 EPD-005 展开 `FULL,NO_VERIFICATION,NO_PARSER_POLICY,NO_REQUEUE,NO_MERGE_GATE`；`NO_SLOT_INTEGRITY` 已从正式矩阵删除。
- [ ] 消融只存在于 experiment wrapper/adapter，不修改协议 core 的 FULL 默认语义。
- [ ] 每 mode 使用独立 output root；每 domain/difficulty 固定 5 tasks，Lean 使用预注册 2/2/1 topic-family slice，重复 3 次，仍先调用真实 AI API。
- [ ] 所有 modes 固定使用 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`；不得让不同 mode 使用不同模型。
- [ ] 记录 completion、accepted validity、wrong canonical/raw-only exposure/acceptance、stuck、premature merge、time/token/cost，以及 `exposed_error_count,escaped_error_count,error_escape_rate,error_escape_applicability`；专项字段必须来自运行事实，不能由 mode 标记直接生成。
- [ ] `error_escape_rate=escaped_error_count/exposed_error_count`；NO_REQUEUE 不适用或分母为 0 时写 `null` 和稳定 applicability reason，不得硬写 0。
- [ ] 在同一 Task 内完成 TDD、预算批准、小 pilot、正式 Exp4、`paper_table_exp4.csv`、状态和完整验证。

### Task 19: 扩展并运行 Experiment 5（三模型 endpoint comparison）

**依赖：** Task 18。

**Files:** `paper_runner.py`、`paper_model_identity.py`、metrics/report、CLI 及对应 model execution tests。

- [ ] 只比较冻结的 SiliconFlow GLM-5.2、SiliconFlow Qwen3.6-27B、OpenAI GPT-5.6 Sol high；不恢复 strong/weak/mixed routing。
- [ ] 明确这是 Experiment 1–4 固定 GLM-5.2 规则的唯一模型例外；三个 cohort conditions 各自 fixed-entry，不允许在单个 AI unit 内 mixed routing。
- [ ] 三 endpoint 使用相同 Exp1 slice、order、prompt/parser/plugin、worker、timeout 和 repeat/seed family。
- [ ] 每个 AI unit 的恢复链固定同一 endpoint，校验 configured/requested/resolved model、reasoning 和各层 digests。
- [ ] 导出 `model_execution_records.jsonl`，连接 v2 identity artifact 与 attempt/usage evidence，保守读取历史 v1。
- [ ] 缺任一 member 时正式 Exp5 必须 incomplete-cohort blocked、零调用；单 endpoint 只能是 pilot。
- [ ] 在同一 Task 内完成 TDD、三 endpoint 预算批准、小 pilot、正式 Exp5、provider-confounding report、状态和完整验证。

### Task 20: 五实验联合审计与论文输入

**依赖：** Task 15–19。

**Files:** `paper_metrics.py`、`paper_report.py`、CLI、final audit tests、Phase 8 code map、`progress.md`、`feature_list.json`、`session-handoff.md`。

- [ ] 联合验证五个 suite 的 version/catalog/model/budget digests、paper eligibility、evidence manifests、artifact refs 和 secret scan；Exp1–4 必须全部解析到 GLM-5.2 baseline，Exp5 必须解析到完整三端点 cohort。
- [ ] 生成统一 task/condition/failure CSV、`model_execution_records.jsonl`、`audit_summary.json`、五张论文表和 `report.md`。
- [ ] formal、pilot、blocked、budget-exhausted、unsupported worker level 必须分开；非 formal 数据不得进入主表。
- [ ] 所有论文单元格可回溯到 run/task/attempt/event/artifact evidence，replay 为零 provider calls。
- [ ] 保留负面结果、能力边界、provider confounding 和经过复核的 blocked cells。
- [ ] 更新 code map、状态与 handoff，运行 targeted tests、`.\init.ps1`、JSON/Markdown/diff/secret audits。
- [ ] 只有五个实验均有正式结果或经过实际前置复核的可审计 blocked 结论，才将 `feat-011` 标记完成。

## 5. 压缩原则

不再为每个实验单独拆出 budget、pilot、metrics、report 和 formal-run Task；这些活动留在对应实验 Task 内。唯一不能压缩掉的额外依赖是 Task 14：Lean 3×3 题库与 split/merge readiness 是正式 Exp1 以及后续 Lean 条件的真实前置能力，不能被普通 report 或 structured blocked gate 代替。
