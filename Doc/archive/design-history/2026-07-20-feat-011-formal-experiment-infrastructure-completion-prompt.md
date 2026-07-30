# TokenShare feat-011 正式实验设施补全 Prompt

> 2026-07-24 参数覆盖：本文是历史设施补全 prompt，其中 Exp1=`1,905` 和 P0-core/P0-full 旧算术已被 EPD-001 替代。当前目标是 Experiment 1 的 Factorization 500 题与 Lean 135 题均只运行 1 次，即 Exp1=`635` root-runs / `2,900` planned AI units；以 `tokenshare_latest_real_plugin_experiment_design.md` 和 `tokenshare_experiment_parameter_decision_log.md` 为准。

> 2026-07-24 Exp2 参数覆盖：本文中的 Exp2=`10,300`、Factorization 三档 + Lean、worker `1/3/10/30` 和 5 repeats 均为历史设施口径。EPD-003 当前目标为全部 166 道 hard Factorization、20-way split、worker `1/3/7/10/30/50`、每档 2 遍，Lean 不进入，共 `1,992` root-runs / `39,840` planned AI units。

> 2026-07-24 Exp3 参数覆盖：本文中的 Experiment 3 3 repeats、rate-fault=`52,680`、worker-death=`9,054` 和 Exp3=`61,734` 均为历史设施口径。EPD-004 当前统一为每 condition 2 repeats，对应 `35,120/6,036/41,156` root-runs；其他参数不变。

> 2026-07-24 Exp4/Exp5 与当前设施覆盖：Exp4 当前为五模式/7,725 roots；Exp5 当前为 Exp1 全部 hard Factorization 166 + Lean 45、36 conditions、1,899 roots，并与 Exp2 selection 解耦。正式行为/指标 blocker 和实施顺序已统一写入 `2026-07-24-feat-011-exp2-exp5-experiment-facility-completion-implementation-plan.md`；本文下方旧六模式、4,635-root Exp5 和旧完整设施声明均为历史输入，不得作为“已经可跑”的证据。

把本文件从下一行开始完整交给负责设施补全的 agent。

---

你是 TokenShare feat-011 的 formal experiment infrastructure owner。你的任务是补全当前缺失的正式实验执行设施，使 Experiment 1-5 在不调用真实 provider 的前提下，通过 capturing/in-memory transport 证明完整 formal suite 可以真实执行、持久化、恢复、复算和报告。

仓库：

```text
E:\TokenEcnomic\TokenShare
```

## 一、并行开发事实和所有权

当前另一个 agent 正在并行完成以下工作：

- 新增 500 道正式 Factorization roots，范围约 100 万到 1000 亿。
- 500 道 Factorization 全部进入 Exp1-5，不再使用 3/5/10 题抽样。
- 更新 C-G 五实验 frozen selections 和新 root-run 算术。
- 让 paper CLI 默认免人工预算审批，但继续记录 budget digest 和预算上界。

你必须完整阅读：

```text
docs/superpowers/specs/2026-07-20-factorization-500-full-matrix-budget-bypass-design.md
```

该设计已经由用户批准，是你实现 formal infrastructure 时必须兼容的前向契约。不得把旧 30 题、Exp1=495、P0-full=2718 等数字重新硬编码进设施。

并行开发期间：

- 不要 reset、checkout、stash、clean 或覆盖当前工作树。
- 不要删除任何用户或其他 agent 的未提交修改。
- 开始时记录 `git status --short`、`git log -10 --oneline --decorate`、`git diff --stat`、`git diff --check`。
- 如果与 500 题 agent 共用工作树，优先创建新的设施文件，不要修改其 owned catalog/matrix 文件。
- 如果使用独立 worktree/branch，最终必须以当前主工作树的新 catalog/matrix 契约重新集成和验证。
- 只提交你确认属于 formal infrastructure 的文件；不要提交另一个 agent 的 500 题/catalog/budget改动。

500 题 agent 主要拥有：

```text
benchmarks/paper/factorization_catalog.v2.jsonl
src/tokenshare/experiments/paper_factorization_catalog.py
src/tokenshare/experiments/paper_catalog.py
src/tokenshare/experiments/paper_exp1.py
src/tokenshare/experiments/paper_exp2_scalability.py
src/tokenshare/experiments/paper_exp3_fault_recovery.py
src/tokenshare/experiments/paper_exp4_ablation_runner.py
src/tokenshare/experiments/paper_exp5_model_comparison.py
src/tokenshare/experiments/paper_budget.py
相关 catalog/matrix/budget tests 和设计状态文档
```

你的主要 owned files 建议为：

```text
src/tokenshare/experiments/paper_formal_runner.py
src/tokenshare/experiments/paper_formal_evidence.py
src/tokenshare/experiments/paper_formal_callbacks.py
src/tokenshare/experiments/paper_formal_metrics.py
src/tokenshare/experiments/paper_formal_report.py
tests/experiments/test_paper_formal_runner.py
tests/experiments/test_paper_formal_evidence.py
tests/experiments/test_paper_formal_callbacks.py
tests/experiments/test_paper_formal_metrics.py
tests/experiments/test_paper_formal_report.py
```

集成阶段允许最小修改：

```text
src/tokenshare/experiments/__init__.py
src/tokenshare/experiments/paper_runner.py
src/tokenshare/experiments/run_paper_experiments.py
src/tokenshare/experiments/paper_metrics.py
src/tokenshare/experiments/paper_report.py
tests/experiments/test_run_paper_experiments_cli.py
```

只有真实 runtime blocker 被失败测试证明时，才允许最小修改：

```text
src/tokenshare/experiments/factorization_paper_adapter.py
src/tokenshare/experiments/lean_paper_adapter.py
src/tokenshare/experiments/paper_faults.py
src/tokenshare/experiments/paper_workers.py
src/tokenshare/experiments/paper_ablation.py
src/tokenshare/executors/ai_api*.py
```

不要大范围重写协议 core、插件 parser/verifier/checker 或 storage authority。

## 二、权威文档和启动流程

必须使用 PowerShell，并对中文文件显式指定 UTF-8。完整阅读：

1. `AGENTS.md`
2. `Doc/agent-navigation.md`
3. `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
4. `Doc/TechnicalDocument/2026-07-18-feat-011-parallel-experiment-prompt-pack.md`
5. `docs/superpowers/specs/2026-07-20-factorization-500-full-matrix-budget-bypass-design.md`
6. `src/tokenshare/experiments/paper_experiment_contracts.py`
7. `src/tokenshare/experiments/paper_models.py`
8. `paper_dispatcher.py`、`paper_runner.py`、`run_paper_experiments.py`
9. C-G 五实验模块及测试
10. Factorization/Lean paper adapters
11. `paper_faults.py`、`paper_workers.py`、`paper_ablation.py`
12. `paper_budget.py`、`paper_metrics.py`、`paper_report.py`
13. AI API config/transport/identity/model-policy
14. `feature_list.json`、`progress.md`、`session-handoff.md` 和当前 code map

开始实现前运行：

```powershell
.\init.ps1
```

如果失败，先判断是否是当前工作树已有问题，不得覆盖其他 agent 的改动。

## 三、当前已确认的缺口

不要重新花一轮只做诊断。以下事实已经源码确认：

- `run_paper_experiments.py` 非 `--pilot` 分支只写 plan artifacts 和 `status=planned` suite，然后退出。
- `paper_runner.py` 只有 Exp1 pilot loop 和单 condition/case Gate C pilot。
- `_GateCCaseExecutionCallback` 只调用基础 adapter，忽略 Exp3 `execution_manifest` 和 Exp4 `mode_config/ablation_profile`。
- `paper_metrics.py` 只有 Gate C pilot 和 Exp1 pilot evidence metrics。
- `paper_report.py` 只有 Exp1 pilot report，并写 `formal_paper_table_generated=false`。
- Exp3 post-AI fault 和 worker-death primitives 已有，但没有接入真实 formal execution callback。
- 本段是当时的实现基线；当前 runtime wrapper/hook 已存在。EPD-005 后正式 Exp4 只保留五种 mode，且仍须修复权威设计中 2026-07-24 输出接线审计列出的专项指标问题。
- 当前绿色测试主要覆盖 plan、callback 参数转发、单 case capturing path 和独立 primitives，没有覆盖完整 formal suite。

你的任务是实现缺失设施，不是把现有 pilot loop 简单改名为 formal。

## 四、新冻结矩阵

设施必须从实际 dispatch plan 和 frozen selections 复算，不得依赖旧常量。集成 500 题改动后，预期为：

```text
Unique roots:
500 Factorization + 135 Lean = 635

Exp1 = 1,905 root-runs
Exp2 = 10,300 root-runs
Exp3 = 61,734 root-runs
  rate-fault   = 52,680
  worker-death =  9,054
Exp4 = 7,725 root-runs（EPD-005：5 modes）
Exp5 = 4,635 root-runs

P0-core = 83,209 root-runs
P0-full = 87,844 root-runs
```

这些数字只用于验收比较。生产实现必须对 `PaperExperimentDispatchPlan.bound_items()` 中实际 condition/selection bindings 求和。

设施单元测试应使用小型内存 catalog/dispatch plan，不能在每次 pytest 中真实执行 87,844 root-runs。另设纯 JSON/full-plan probe 验证完整新算术。

## 五、预算策略

用户已经明确：实验不需要人工预算批准，直接运行。

设施必须遵守：

- 仍生成和保存 `run_budget.json`。
- 仍记录 budget digest、planned roots、AI units、provider-attempt/token/cost upper bounds。
- 默认不要求 `--approve-budget-digest`。
- 接受结构化 `budget_approval.approval_mode="user_bypassed"`。
- budget digest 仍是计划身份，不是人工审批门禁。
- 不得因为缺少 approval digest 把 formal suite 标记 blocked。
- 如果调用者显式启用 `--require-budget-approval`，才恢复 digest approval gate。
- 可选 hard limits 仍可停止启动新 task；未配置 hard limits 时不得静默缩小矩阵。
- `budget_exhausted` 必须保留已有 evidence，不启动新的 task。

不要自行重新引入人工确认步骤。

## 六、目标架构

### 6.1 Formal suite runner

实现一个与 pilot 明确分离的正式入口，例如：

```python
execute_paper_formal_suite(
    *,
    dispatch_plans: tuple[PaperExperimentDispatchPlan, ...],
    catalog_manifest: PaperInputCatalogManifest,
    budget: PaperBudgetResult,
    budget_approval: Mapping[str, Any],
    output_root: Path,
    ai_api_configs: Mapping[str, AIAPIExecutorConfig],
    transport: Any | None,
    real_transport: bool,
    hard_limits: Mapping[str, Any],
    resume: bool = False,
    replay_only: bool = False,
) -> PaperSuiteResult
```

正式 runner 必须：

1. 验证 suite、catalog、dispatch plan、condition、selection、identity 和 request-limit digests。
2. 为每个 experiment 使用独立 output root。
3. 对每个 plan 调用同一个注册模块生成的 `run_condition()`。
4. callback 消费完整 frozen selection，不得只选一个 case。
5. 每个 case 由正式 Factorization/Lean adapter 处理。
6. 每完成一个 root 就 checkpoint。
7. 达到可选 hard limit 后停止启动新 root，写 `budget_exhausted`。
8. blocked condition 零 transport 调用。
9. suite/experiment/condition/run/task/attempt 状态从 evidence 派生。
10. capturing transport 永远 `paper_eligible=false`、`regression_only=true`。

### 6.2 运行循环职责

外层循环属于 experiment layer：

```text
suite
  -> experiment
    -> condition + exact FrozenCaseSelection
      -> root case
        -> deterministic AI units
          -> provider attempts
```

单 root 内 split、parser、verifier/checker、canonical、merge/root recheck 继续由 adapter/plugin 负责。不要在 formal runner 重写领域算法。

### 6.3 Experiment-specific callbacks

不要用一个忽略 kwargs 的万能 callback。实现明确策略：

- Exp1：完整 selection 的正常 adapter lifecycle。
- Exp2：使用 condition `worker_count` 的真实本地调度；保存 worker/unit start/end、dependency、merge gate 和 critical-path evidence。
- Exp3 rate-fault：真实/capturing provider raw output 先持久化，再按 `execution_manifest.fault_target_manifest` 注入；保存 original/mutated refs；需要恢复时创建新的 provider attempt。
- Exp3 worker-death：独立 executor worker process，按预注册 kill position 终止；coordinator 继续；lease expiry；replacement worker/reassignment；保存 OS PID/exitcode 和 replacement attempt。
- Exp4：消费 `mode_config` 和 `ablation_profile`，每个 mode 只关闭指定实验边界；FULL 复用默认行为，不修改协议 core 默认语义。
- Exp5：根据 condition 的 `provider_config_id + model_entry_id + cohort_member_id` 选择固定 entry；首次和恢复 attempts 不 failover；生成 `model_execution_records.jsonl`。

## 七、Exp2 要求

Exp2 不能把 `worker_count` 只写进 manifest。capturing tests 至少证明：

- worker level 改变实际最大并行 slot 数。
- 相同 frozen ordered IDs 在 worker/repeat 间不漂移。
- 每个 AI unit 有 started/ended timestamps 和 worker ID。
- wall-clock 从 condition/task 生命周期计算。
- critical path 从 dependency 和 merge gate 复算。
- provider latency sum 不冒充 wall-clock。
- 429/provider errors 被记录，不伪造成协议耗时。
- optional worker levels 不支持时写 `unsupported_worker_level`，不补造曲线。

## 八、Exp3 要求

Rate-fault 顺序必须是：

```text
request
-> provider/capturing response
-> raw/provenance/usage persistence
-> deterministic target membership
-> fault mutation/drop/delay/error
-> parser/verifier/checker/canonical gate
-> optional replacement attempt
-> evidence + summary
```

必须覆盖：

- false_positive
- false_negative
- no_return
- late_submission
- executor_error

worker death 必须实际使用 `paper_workers.run_worker_death_harness` 或等价真实 process harness，不能只写 `worker_died=true` fixture。

capturing integration tests 至少证明：

- fault injection 晚于 raw artifact persistence。
- original/mutated refs 都可读取。
- late/no-return 不污染 canonical。
- recoverable fault 创建新的 replacement attempt。
- replacement attempt 仍使用同一批准 model identity。
- worker process 确实被终止，coordinator 未终止。
- lease expiry、reassignment、replacement accept 顺序可从 events 复算。
- matched baseline 使用相同 roots/repeat/seed/worker/model/request limits。

## 九、Exp4 要求

按 EPD-005 固定五种 mode：

```text
FULL
NO_VERIFICATION
NO_PARSER_POLICY
NO_REQUEUE
NO_MERGE_GATE
```

要求：

- FULL 与现有默认 adapter/plugin 语义一致。
- mode wrapper 只改变一个实验边界。
- 每个 mode 独立 output root。
- NO_VERIFICATION 可以让错误 candidate 到达 canonical，但必须保留最终 deterministic validity audit。
- NO_PARSER_POLICY 只能在实验 wrapper 暴露 raw/free-form 风险，不能修改正式 parser 默认策略。
- NO_REQUEUE 在拒绝/过期后不创建 replacement，stuck/completion 从 evidence 计算。
- NO_MERGE_GATE 允许 premature merge attempt，但 root verifier/checker 仍记录失败。
- `exposed_error_count`、`escaped_error_count` 和 applicability 从实际事件复算，不硬编码。

## 十、Evidence 和 output contract

至少写出：

```text
suite_manifest.json
run_budget.json
input_catalog_manifest.json
paper_dispatch_plans.json
conditions.jsonl
experiments/<experiment_id>/experiment_manifest.json
runs/<condition_id>/<repeat_id>/run_manifest.json
runs/<condition_id>/<repeat_id>/per_task_results.jsonl
runs/<condition_id>/<repeat_id>/per_attempt_results.jsonl
runs/<condition_id>/<repeat_id>/fault_injections.jsonl
runs/<condition_id>/<repeat_id>/events/event_log.jsonl
runs/<condition_id>/<repeat_id>/artifacts/...
metrics/per_condition_summary.csv
metrics/paper_table_feasibility.csv
metrics/paper_plot_scalability.csv
metrics/paper_plot_robustness.csv
metrics/paper_table_ablation.csv
metrics/model_execution_records.jsonl
metrics/failure_examples.json
audit/paper_eligibility_report.json
audit/secret_scan_report.json
evidence_manifest.json
```

所有 request/raw/parsed-or-parse-failure/provenance/usage/model-execution refs 必须指向真实持久化 artifact。不得只写 artifact ID 占位符。

正式 evidence schema 必须与 pilot 分开：

- `formal=true`
- `pilot_only=false`
- `execution_scope="formal_matrix"`
- capturing transport 时仍 `paper_eligible=false`
- 只有未来真实 provider attempts 完整覆盖后才可能 `paper_eligible=true`

## 十一、Resume 和 replay

必须实现：

- `resume=True` 校验当前 suite/dispatch/budget/catalog/identity 与历史 manifest 一致。
- 完整已完成 root 不重新调用 transport。
- 不完整 root 明确失败或按已定义 checkpoint 继续，不能重写历史成功。
- `replay_only=True` 全程 transport calls=0。
- replay 不重新运行 Lean checker来补写历史结果。
- replay 不重新解析当前 provider config来回填 resolved model。
- 一个 experiment output root 的 evidence 不得用于另一个 experiment。
- 缺失 artifact、event 或 attempt evidence 时 fail closed。

至少用 capturing transport 做一次：执行 -> 完整 resume -> replay-only，后两者 transport call count 都必须为 0。

## 十二、Formal metrics 和 report

不要把 `recompute_exp1_pilot_metrics()` 改名后复用。新增正式 metrics 入口，从不可变 evidence 复算：

- Exp1：completion、accepted validity、wall-clock/tokens quantiles、failure breakdown。
- Exp2：throughput、speedup、efficiency、critical path、429/retry sensitivity。
- Exp3：detection/false accept/recovery/completion、matched overhead、worker completeness。
- Exp4：completion/validity、wrong canonical/raw-only exposure/acceptance/stuck/premature merge、error escape；专项字段必须来自真实运行事实。
- Exp5：按 endpoint/provider 的 completion/validity/tokens/cost/latency/errors 和 model execution identity audit。

必须有“修改输入 evidence 后汇总随之改变”的回归，证明没有硬编码结果。

report 必须：

- 从 formal metrics 写 CSV/JSON/Markdown。
- 不混入 pilot、plan-only、blocked、budget-exhausted 或 capturing regression evidence。
- capturing 运行只生成 audit/regression report，不生成可写入论文的正式表。
- secret scan 在报告前执行。

## 十三、CLI

统一 CLI 最终支持：

```text
--experiments exp1|exp2|exp3|exp4|exp5|组合
--plan-only
--real-transport
--resume
--replay-only
--require-budget-approval       # 可选，默认关闭
--approve-budget-digest         # 仅 require 模式使用
--max-total-provider-attempts
--max-total-tokens
--max-cost-estimate
--stop-after-current-task
```

非 `--pilot` 且非 `--plan-only` 时必须调用 formal suite runner，不能再只写 `status=planned` 后退出。

单独运行某个实验时不得要求其他实验已有 evidence。Exp5 cohort 不完整只阻塞 Exp5。

## 十四、TDD 顺序

必须使用 test-first：

1. 先为正式 CLI 非 pilot 路径写 RED，证明当前只返回 planned。
2. 为 generic formal runner 的单 experiment/condition/case mini-plan 写 RED。
3. 实现最小 Exp1 normal callback 和 evidence checkpoint。
4. 写 resume/replay RED，再实现零 transport replay。
5. 写 Exp2 actual scheduling/critical-path RED，再实现。
6. 写 Exp3 post-AI fault 和 worker-death end-to-end RED，再实现。
7. 写 Exp4 六模式 runtime RED，再实现。
8. 写 Exp5 fixed-entry/model-record RED，再实现。
9. 写 formal metrics/report evidence mutation RED，再实现。
10. 最后接 CLI/public API，并与 500 题 agent 的新 matrix 合并。

每个 RED 必须实际运行并确认因为缺少目标行为而失败；不能先写实现再补测试。

## 十五、离线 capturing 验收探针

不得调用真实 provider。至少证明：

1. CLI 非 pilot 正式路径调用 formal runner。
2. 五模块均通过同一 Protocol 和 dispatcher。
3. 每个实验可独立执行 mini formal plan。
4. Factorization 和 Lean 各有成功链、parse failure、verifier/checker rejection。
5. Exp1-4 request identity 只允许 SiliconFlow GLM-5.2 baseline。
6. Exp5 三 fixed entries 不 failover。
7. budget approval 默认 bypass 且有结构化记录。
8. plan-only callback/transport count=0。
9. blocked condition transport count=0。
10. Exp2 worker level改变实际调度，并能复算 critical path。
11. Exp3 fault/death/replacement 真实串联。
12. Exp4 六 mode 产生各自预期 evidence 差异。
13. resume/replay transport count=0。
14. metrics/report 随 evidence 改变。
15. capturing evidence 始终 paper-ineligible。
16. 完整新 plan 复算为 P0-core=83,209、P0-full=87,844。

## 十六、验证命令

先运行新增设施测试：

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest -q `
  tests/experiments/test_paper_formal_runner.py `
  tests/experiments/test_paper_formal_evidence.py `
  tests/experiments/test_paper_formal_callbacks.py `
  tests/experiments/test_paper_formal_metrics.py `
  tests/experiments/test_paper_formal_report.py `
  tests/experiments/test_run_paper_experiments_cli.py
```

然后运行：

```powershell
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments
conda run --no-capture-output -n tokenshare python -m pytest -q tests/executors tests/plugins/factorization tests/plugins/lean_proof
conda run --no-capture-output -n tokenshare python -m compileall -q src tests
git diff --check
.\init.ps1
.\init.ps1 -Full
```

完整验证可以调用本地 Lean checker，但不得调用真实 AI provider。

## 十七、状态和提交

完成后更新：

- `feature_list.json`
- `progress.md`
- `session-handoff.md`
- 当前实验 code map

必须如实写明：

- formal infrastructure 已实现。
- capturing/offline formal probes 已通过。
- 本任务真实 provider calls=0。
- 尚未产生新的真实 formal 论文结果。
- 未来真实运行直接执行，不需要人工预算批准，但仍记录预算和实际 usage。

提交前确认没有未解释的 Critical/Important blocker。形成一个 coherent formal infrastructure commit，不要 push。

## 十八、最终报告

最终报告按以下格式：

1. 结论：PASS / FAIL / BLOCKED
2. 补全的 formal execution 架构
3. Exp1-5 runtime 状态表
4. checkpoint/resume/replay 结果
5. Exp2/3/4/5 专项 callback 结果
6. formal evidence/metrics/report 结果
7. 500 题新矩阵复算结果
8. 所有验证命令、exit code、passed/skipped/failed 数
9. 真实 provider 调用数，必须为 0
10. 修改文件和提交 SHA
11. 剩余外部前提，只能是未来真实 provider formal run，不得把缺失代码设施留给下一轮

不要声称 capturing transport 产生了论文结果，不要用 synthetic callback 代替正式 adapter/executor，不要把 pilot evidence 重新标记为 formal。
