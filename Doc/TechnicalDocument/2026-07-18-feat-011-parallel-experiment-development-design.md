# feat-011 并行实验开发设计

> 日期：2026-07-18  
> 状态：方案已获用户认可；Task 14 语义审核未通过，允许并发代码开发但禁止正式实验  
> 适用范围：`feat-011` Task 15–20 的代码开发、集成、预算与正式运行顺序  
> 权威实验口径：`tokenshare_latest_real_plugin_experiment_design.md`

## 1. 决策

后续实验采用“公共契约串行冻结、Experiment 1–5 代码并行开发、共享入口串行集成、真实实验串行审批与执行”的组织方式。

Experiment 1–5 不是五套独立系统。它们共享 catalog selection、condition/run/task/attempt schema、真实 AI executor、Factorization/Lean adapters、artifact/event evidence、budget gate、metrics 和 report。各实验主要差异是 condition 展开、执行 wrapper、故障或消融注入、模型 endpoint 约束，以及专项指标。因此可以并行开发实验模块，但不能让多个 agent 同时改写共享 runner、schema、metrics/report 汇总文件或状态源。

## 2. 目标与非目标

目标：

- 缩短 Task 15–19 的代码开发等待时间，同时保持 Experiment 1–5 的冻结样本、模型、预算和证据口径不变。
- 让每个实验模块拥有清楚的输入、输出和文件所有权，可以独立 TDD、独立审查、独立合并。
- 保留一个 integration owner，统一修改 CLI、共享 runner、共享 metrics/report 和状态文档。
- 真实 API pilot、预算批准和正式运行仍按可审计顺序执行，不让并行开发变成并行花费或并行污染结果。

非目标：

- 不减少 Experiment 1–5、difficulty、topic family、worker level、fault type、ablation mode、repeat 或三模型 endpoint。
- 不改变协议 core、插件 verifier/checker 权威或 Phase 7 executor 的通用边界。
- 不在本设计中实现 Task 14、调用 provider、批准预算或生成论文正式结果。
- 不新增恶意攻击防护或生产安全范围；验收聚焦实验能运行、证据可审计、结果可产出。

## 3. 串行门禁与并行边界

### Gate A：Task 14 语义修复完成

2026-07-18 复核确认，当前 Task 14 已满足 9×15、135 个唯一 case IDs、checker preflight、exact selection/budget input 和 `provider_calls_made=0` 的机械契约，但尚未满足论文实验的语义契约：八个 v2 passed cells 每格只有一个 theorem/DAG 语义形状；三个 `hard_frontier` checker pool 是对应 `medium_lemma_dag` 模板的重命名副本，深度、节点、statement、edge、AI-unit 和 oracle theorem 均未形成 hard 难度；hard/function_set 与 hard/induction 也缺少逐格端到端 golden 证据。

因此 Gate A 重新打开为 Task 14 semantic repair：每格 15 个 case 必须是 15 个真实不同的 Lean roots，而不是只改 `case_id`、seed 或 theorem name；`hard_frontier` 必须使用独立且明显高于 medium 的 theorem/DAG/oracle shapes；九格均需 1–2 个实际执行过的 split → proof assembly → dependency-aware merge → root recheck golden cases。修复后重新生成所有 selection/catalog/matrix/oracle/AI-unit digests。

在 Gate A 修复期间，Exp1–5 独立模块可基于固定接口、synthetic fixtures 和 structured-blocked paths 并行开发；不得把当前 Lean catalog 当正式输入，不得冻结正式预算，不得调用 provider，不得生成论文正式结果。

### Gate B：公共开发契约冻结

Task 14 agent 停止写入并完成审核后，由 integration owner 串行冻结以下共享契约。Gate B 的类型和函数接口必须同时表达 executable selection 与 structured-blocked selection，因此不依赖“所有 case 已成功”才能开始；正式数据 binding 仍以 Task 14 的最终 15/135 manifest 为门禁。

- `PaperExperimentCondition` v2 的正式字段和 digest。
- exact `FrozenCaseSelection`：任务 ID、顺序、slice digest、catalog digest 和预计 AI units。
- condition/run/task/attempt result schema 与稳定 status/failure 枚举。
- `PaperExecutionContext`：catalog、approved endpoint identity、request limits、hard limits、output root 和 evidence stores。
- 实验模块的最小函数接口和返回值。

Gate B 只做接口收口和必要的薄层抽取，不重写已经工作的 adapters、executor、fault/worker/ablation support。

### Parallel Batch：Experiment 1–5 代码开发

Gate B 后，各实验模块可以在独立 worktree/branch 中并行开发。每个模块只拥有自己的新文件和对应 tests，不直接修改共享 dispatcher、CLI、状态文档或其他实验模块。

### Gate C：串行集成

各模块通过独立审查后，由 integration owner 依次合并，并统一接入 `paper_runner.py`、`run_paper_experiments.py`、`paper_metrics.py` 和 `paper_report.py`。Gate C 负责跨实验 schema、budget、resume/replay、paper eligibility 和输出目录的一致性。

### Gate D：串行预算、pilot 与正式运行

代码全部集成并通过完整验证后，按 Experiment 1、2、3、4、5 的顺序分别执行：

1. `--plan-only` 和 exact digest 审核。
2. 用户批准该实验预算。
3. 小 pilot 与输出审计。
4. 修复 pilot 暴露的问题并重新生成/批准受影响预算。
5. 正式运行和专项报告。

并行开发不授权并行真实 API 调用。这样可以隔离 provider quota、模型配置、成本、输出目录和失败归因。

## 4. 模块接口

避免建立过度抽象的框架。每个实验模块只需实现四类纯入口，命名按实验编号固定：

```python
expand_expN_conditions(context) -> tuple[PaperExperimentCondition, ...]
freeze_expN_case_selections(context, conditions) -> tuple[FrozenCaseSelection, ...]
run_expN_condition(context, condition, selection) -> PaperConditionResult
summarize_expN(evidence) -> ExperimentSummaryRows
```

公共 runner 负责生命周期、预算 hard limits、checkpoint、resume/replay、artifact/event 落盘和 suite 汇总；实验模块负责本实验的 condition 矩阵、合法性约束、已有 wrapper/support 的组合方式和专项指标输入。

正式模块不得自行创建第二套 provider transport、artifact store、Lean checker、factorization verifier、budget approval 或 report root。

## 5. 并行工作包与文件所有权

| 工作包 | 主要职责 | 独占新文件建议 | 不得直接修改 |
|---|---|---|---|
| Exp1 formal | 165 roots、3 repeats、feasibility rows、difficulty boundary | `paper_exp1.py`、`test_paper_exp1_formal.py` | shared runner/CLI/status docs |
| Exp2 scalability | worker 1/3/10/30、可选 100/300、critical path、speedup/efficiency | `paper_exp2_scalability.py`、`test_paper_exp2_scalability.py` | Exp3–5 modules、shared metrics/report |
| Exp3 robustness | rate faults、worker death、matched baseline、recovery/overhead | `paper_exp3_fault_recovery.py`、`test_paper_exp3_fault_recovery.py` | shared fault/worker primitives，除非先交 integration owner 审核 |
| Exp4 ablation | 六种 mode、独立 output root、error escape | `paper_exp4_ablation_runner.py`、`test_paper_exp4_ablation_runner.py` | protocol core、默认 FULL semantics |
| Exp5 endpoints | 三端点 fixed-entry cohort、identity records、provider confounding | `paper_exp5_model_comparison.py`、`test_paper_exp5_model_comparison.py` | executor cohort semantics、Exp1–4 baseline model |
| Integration | dispatcher、CLI、共享 schema/metrics/report、最终状态 | 现有 shared files | 各实验模块内部，除非修复明确接口问题 |

共享文件只有 integration owner 可以在并行批次内修改：

- `src/tokenshare/experiments/paper_models.py`
- `src/tokenshare/experiments/paper_runner.py`
- `src/tokenshare/experiments/run_paper_experiments.py`
- `src/tokenshare/experiments/paper_metrics.py`
- `src/tokenshare/experiments/paper_report.py`
- `src/tokenshare/experiments/__init__.py`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`
- 现有 feat-011 实施计划和 code map

## 6. 各实验不能漂移的约束

Exp1–4 的所有首次、恢复和消融 AI attempts 固定使用 SiliconFlow `zai-org/GLM-5.2` / `glm_5_2_exp1_baseline`，不得因模块并行开发而各自选择模型。

Exp2、Exp4、Exp5 共用同一组预注册 5-task Lean slices 和 exact task IDs；Exp3 使用固定的三个 topic-family 各一题 slice。slice selection 由 Gate B 冻结，实验模块只能消费，不得重新抽样。

Exp3 的 matched baseline、Exp4 的 FULL baseline 和 Exp5 的三 endpoint conditions 必须引用共享 condition identity 与 case-selection digest，不能在 report 层猜测或补写。

所有正式候选仍走真实 provider、parser、verifier/checker、canonical/merge 和 evidence chain。fake/scripted transport 只用于模块测试，并保持 `paper_eligible=false`。

## 7. 错误处理和停止规则

- 缺 catalog cell、exact selection、oracle/checker preflight 或 digest：在 provider call 前结构化 blocked。
- 公共 schema 或 module interface drift：停止集成，更新 Gate B version 后重跑所有模块 contract tests。
- 预算 digest、endpoint identity 或 request limits drift：旧批准失效，provider calls 保持 0。
- 模块专项 condition 失败：保留该 run evidence，不修改其他实验输出目录。
- pilot 暴露 shared bug：由 integration owner 修复公共层，各模块只更新适配测试。
- 任一 agent 需要修改非自有 shared file：停止该工作包并交给 integration owner，不直接跨边界写入。

## 8. Git、worktree 与并发纪律

当前工作树存在大量未提交改动，且另一个 agent 正在修 Task 14。并行批次不能从这个活动中的 dirty tree 直接复制或同时写共享文件。

安全顺序：

1. 等 Task 14 agent 停止写入，主 agent 审核并运行验证。
2. 经用户明确确认后，在当前 feature branch 创建一个标明 Task 14 `semantic_blocked` 实际状态的 reviewed checkpoint commit；不合并到 `main`。该 checkpoint 只用于并行代码开发，不授权预算或真实运行。
3. 从该 checkpoint 为各实验创建独立 worktree/branch，branch 使用 `codex/` 前缀。
4. 每个工作包只提交自己的模块和 tests。
5. integration owner 按模块逐个合并；每次合并运行该模块 tests 和 shared contract tests。
6. 全部集成后运行 `tests/experiments`、相关 executor/plugin impact suites 和完整 `init.ps1`。

在 Task 14 agent 活动期间，本设计只允许新增本文件；不修改它正在处理的 runner、catalog、adapter、tests、manifest、状态文档或现有实施计划，也不移动共享 branch HEAD。

## 9. 测试与审核

每个实验工作包都遵守 TDD：

- RED：condition 数量、固定 task IDs、模型 identity、预算计数和专项指标缺失时失败。
- GREEN：使用 fake/scripted transport 验证 orchestration，但断言 `paper_eligible=false`。
- 模块测试不能依赖其他并行模块尚未合并的实现。
- 每个工作包完成后独立代码审核；Critical/Important 修复后才能交给 integration owner。

Gate C 集成测试至少覆盖：

- 五个实验 condition expansion 能同时加载且 schema 一致。
- exact case selection 不因 worker/mode/model/repeat 重新抽样。
- Exp1–4 model identity 始终为 GLM-5.2 baseline，Exp5 始终为完整三端点 cohort。
- plan-only、budget approval、hard limits、resume/replay 和 output-root isolation。
- metrics/report 的每个 summary row 可回溯到 per-task/per-attempt/event/artifact evidence。
- scripted/fake、blocked、pilot、formal 和 budget-exhausted 数据严格分离。

## 10. 对现有实施计划的改写方式

本设计已获用户认可；实施计划按以下方式同步：

1. 保留 Task 14 semantic repair 为正式运行硬门禁，不把语义重复 catalog 转移给 Task 15。
2. 在 Task 14 与实验实现之间加入“公共开发契约冻结”任务。
3. 把原 Task 15–19 拆成一个可并行的代码开发 batch，不在这些开发任务中执行真实 API。
4. 增加串行 integration task，统一接入 runner/CLI/metrics/report。
5. 把预算、pilot、formal run 重新列为五个串行执行任务；它们消费已集成代码和冻结 digests。
6. Task 20 联合审计仍在所有正式运行之后执行。

该改写只改变工程调度，不改变唯一权威实验设计、样本规模、模型策略、预算审批或论文结果口径。

## 11. 完成标准

- Task 14 semantic repair 和 Gate B 已通过审核并形成 reviewed checkpoint。
- 五个实验模块可独立测试，且没有并行修改 shared files。
- integration owner 已接入五个模块并通过 shared contract tests。
- 完整 `init.ps1` 通过。
- 每个正式实验都单独完成 plan-only、用户预算批准、pilot、formal run 和审计。
- 最终联合报告保留完整 evidence chain、负面结果、blocked 结论和 provider confounding。
