# Phase 8 实验基础设施代码映射

日期：2026-06-29

状态：Phase 8 第一版实验基础设施已实现并完成定向验证。本文映射 `feat-009` 的 source、tests、设计章节、验证证据和边界说明。2026-06-29 后续 Lean adapter 已接入真实 Lean proof plugin ready path；Experiment 4 的 Lean direct proof 与 decomposition/merge 默认先运行真实 Lean preflight，再使用真实 checker evidence，通过可注入 preflight 仍能回归 blocked / pending gate。

## 1. Source Map

| 文件 | 规格章节 | 当前内容 |
|---|---|---|
| `src/tokenshare/experiments/models.py` | TDD 第 6、7、9 节 | `SimulationProfile`、`ExperimentCase`、`ExperimentRun`、`ExperimentStatus`、adapter preflight / result / runner result 纯模型和稳定 digest helper。 |
| `src/tokenshare/experiments/adapters.py` | TDD 第 6 节 | `PluginExperimentAdapter` 协议和 `AdapterRegistry`，通用 runner 只通过插件 ID / version 查找 adapter。 |
| `src/tokenshare/experiments/factorization_adapter.py` | TDD 第 8.1、8.2、8.3、10 节 | 复用 `run_factorization_fixture_flow()` 接入 factorization 真实插件；生成 Experiment 1 fixture report、Experiment 2 failure injection report、Experiment 3 ablation report，以及 Experiment 4 factorization semiprime lifecycle report。 |
| `src/tokenshare/experiments/lean_adapter.py` | TDD 第 6.1、8.4、10 节 | Lean adapter ready path：默认运行真实 Lean preflight，ready 后运行真实 Lean direct proof 与 decomposition/merge fixtures，复制 event logs / artifacts，报告 `real_checker_evidence`、`environment_ref_complete`、decomposition lifecycle coverage 和 merge recheck evidence；同时保留 injectable blocked preflight 回归路径。 |
| `src/tokenshare/experiments/simulation.py` | TDD 第 9 节 | `SimulationWrapper` 记录五类故障和五个消融模式的机器可读决策；不修改 protocol core 默认语义。 |
| `src/tokenshare/experiments/metrics.py` | TDD 第 10、12 节 | 从 copied JSONL event log 和 artifact manifests 复算 event coverage、artifact link、settlement、factorization correctness、failure / ablation、work、critical path、retry wasted work、shadow benefit，并从 Phase 7 `ExecutionSubmission` artifact 复算 AI API executor provider attempts / cost 指标；不解析 pytest stdout，不重新调用 executor / Lean / AI。 |
| `src/tokenshare/experiments/report.py` | TDD 第 7、11 节 | 写 `run_manifest.json`、`case_report.json`、`metrics/metrics.json`、`metrics/paper_summary.csv`、`metrics/lifecycle_coverage.csv`，并固定论文宽表列。 |
| `src/tokenshare/experiments/runner.py` | TDD 第 4、8、11 节 | `ExperimentRunner`、`default_experiment_cases()` 和 `run_phase8_default_suite()`；默认 suite 覆盖 Experiment 1-4，汇总 `phase8_suite_summary.csv` 和 `phase8_suite_report.json`。 |
| `src/tokenshare/experiments/run_all.py` | TDD 第 11 节 / 2026-06-29 CLI 补丁 | `python -m tokenshare.experiments.run_all` 命令入口，默认运行 Experiment 1-4 suite，输出到 `outputs/experiments/`，可读取 gitignored local AI API config 并只把 secret 注入当前进程环境。 |
| `src/tokenshare/experiments/__init__.py` | TDD 第 5 节 | Phase 8 public exports。 |

## 2. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/experiments/test_phase8_models.py` | `SimulationProfile` digest、`ExperimentRun` run_id/status、blocked reason 必填。 |
| `tests/experiments/test_phase8_runner_reports.py` | 单 case runner：factorization semiprime report/metrics/CSV、Lean direct proof ready path、Lean decomposition/merge ready path、可注入 Lean blocked gate、默认真实 Lean preflight 缺 toolchain blocked、failure injection canonical pollution=0、ablation expected degradation、AI API usage/cost 从 submission artifact 复算。 |
| `tests/experiments/test_phase8_default_suite.py` | 默认 Experiment 1-4 case 矩阵和 suite 汇总；Experiment 4 包含 factorization semiprime passed、Lean direct proof passed、Lean decomposition/merge passed，均带真实 checker evidence。 |
| `tests/experiments/test_phase8_simulation.py` | `SimulationWrapper` 五类 fault 和五个 ablation mode 决策记录。 |
| `tests/experiments/test_run_all_cli.py` | CLI 入口运行默认 suite、写 `phase8_suite_report.json`、包含 Lean direct proof / decomposition merge run manifest。 |

## 3. Boundary Notes

- Phase 8 只新增 `tokenshare.experiments` 层；未修改 `tokenshare.core`、`ProtocolEngine`、factorization verifier / merge policy、Phase 7 executor 或 storage authority。
- Factorization 实验 adapter 复用插件公开 fixture helper，不复制 divisibility verifier 或 merge policy 领域逻辑。
- Failure injection / ablation 当前是实验 wrapper 报告层语义，用于论文退化/隔离指标；不把错误路径改成 protocol core 默认行为。
- Metrics 从 JSONL events 和 artifact manifest / content digest 复算 evidence；不信任脚本内联成功布尔值作为唯一证据。
- Lean adapter 默认使用真实 preflight、checker logs、proof artifacts 和 `EnvironmentRef` 声明 Experiment 4 Lean 侧通过；如果这些 evidence 缺失，只能通过 blocked / pending path 报告，不能使用 `lean_stub` 或 synthetic evidence。
- 人类可通过 `conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs/experiments --seed 1` 一次性运行默认 Experiment 1-4；该入口不扩大 protocol core，也不改变 adapter contract。
- Phase 8 evidence check 不是完整 replay / audit engine；完整 state replay、audit replay 和 no-double-settlement 属于 `feat-010` / Phase 9。

## 4. Verification Evidence

- 启动基线：`.\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 301 items，结果 `300 passed, 1 skipped in 30.43s`。
- RED 1：`tests\experiments -q` 失败，原因是缺少 `tokenshare.experiments.models` 和 `tokenshare.experiments.adapters`。
- GREEN 1：实现 models / adapters / report / metrics / factorization adapter / Lean blocked adapter / runner 后，`tests\experiments -q` 通过，结果 `7 passed in 2.19s`。
- RED 2：新增 default suite 测试后失败，原因是缺少 `default_experiment_cases()` / `run_phase8_default_suite()`；修复后 `tests\experiments\test_phase8_default_suite.py -q` 通过。
- RED 3：suite summary 将 `inconclusive` 消融误计入 `passed_runs`；修复为单列 `inconclusive_runs` 后默认 suite 测试通过。
- RED 4：Experiment 4 默认矩阵缺 factorization semiprime lifecycle 和 Lean decomposition / merge gate；补齐后默认 suite 测试通过。
- RED 5：新增 `SimulationWrapper` 测试失败于缺少 `tokenshare.experiments.simulation`；实现后 `tests\experiments\test_phase8_simulation.py -q` 通过。
- RED 6：新增 AI API usage/cost metrics 测试后失败，原因是 `build_metrics()` 仍把 `ai_api_usage_cost` / `ai_api_executor_effect` 固定为 0，而不是从 `EXECUTION_SUBMISSION_RECORDED` 指向的 `ExecutionSubmission` artifact 复算。
- 第一版 targeted verification：`tests\experiments\test_phase8_runner_reports.py -q` 通过，结果 `5 passed in 2.11s`；`tests\experiments -q` 通过，结果 `10 passed in 13.89s`。
- Lean ready-path update targeted verification：Lean Phase 8 ready targeted tests 通过，结果 `3 passed in 6.83s`；`tests\experiments -q` 通过，结果 `12 passed in 25.10s`；combined Lean plugin/E2E/experiments suite 通过，结果 `55 passed in 65.42s`。
- 冲击面验证：`tests\test_phase6_factorization_flow.py tests\test_phase6_factorization_limitations.py tests\executors tests\test_phase7_ai_api_execution_flow.py -q` 通过，结果 `45 passed, 1 skipped in 5.38s`。
- Compile verification：`conda run -n tokenshare python -m compileall -x "reference_repos" .` 退出码 0。
- Whitespace check：`git diff --check` 退出码 0，仅有 LF/CRLF warning。
- 2026-06-29 `run_all` CLI 补丁：RED targeted test first failed with missing `tokenshare.experiments.run_all`; GREEN `tests\experiments\test_run_all_cli.py -q` passed with `1 passed in 16.03s`; wider `tests\experiments -q` passed with `14 passed in 40.51s`; actual CLI `$env:PYTHONPATH='src'; conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs\experiments --seed 1` exited 0 and reported `total_runs=17`, `passed_runs=12`, `inconclusive_runs=5`, `blocked_runs=0`; final `.\init.ps1` passed with pytest collected 368 items, result `367 passed, 1 skipped in 115.85s`.
