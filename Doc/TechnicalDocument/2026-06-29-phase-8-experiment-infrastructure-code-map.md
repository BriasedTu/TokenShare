# Phase 8 实验基础设施代码映射

日期：2026-06-29

状态：Phase 8 第一版实验基础设施已实现并完成定向验证。本文映射 `feat-009` 的 source、tests、设计章节、验证证据和边界说明。2026-06-29 后续 Lean adapter 已接入真实 Lean proof plugin ready path；Experiment 4 的 Lean direct proof 与 decomposition/merge 默认先运行真实 Lean preflight，再使用真实 checker evidence，通过可注入 preflight 仍能回归 blocked / pending gate。2026-06-30 追加 AI profile suite，作为 Phase 8/Phase 7 的小范围实验补强，不改变当前 active feature `feat-010`。

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
| `src/tokenshare/experiments/runner.py` | TDD 第 4、8、11 节 | `ExperimentRunner`、`default_experiment_cases()` 和 `run_phase8_default_suite()`；默认 suite 覆盖 Experiment 1-4，汇总 `phase8_suite_summary.csv`、`phase8_suite_report.json` 和 suite-level `phase8_experiment_settings.json`，后者记录 seed、case settings、simulation profiles、profile digests 和 per-run manifest/case/metrics 路径。 |
| `src/tokenshare/experiments/ai_profile.py` | TDD 第 10、11、13 节 / 2026-06-30 AI profile 补强 | 显式 AI profile suite：运行 deterministic semiprime baseline、AI API semiprime profile 和 AI API raw-only parse failure profile；AI 路径默认用 scripted fake transport，但经过真实 `AIAPIExecutor`、Factorization 插件 parser、raw / parsed / parse-failure artifact 和 execution submission event；输出 provider/model、usage、cost、latency、retry、parser success 和 deterministic vs ai_api 对比。2026-06-30 strict AI 修复后，semiprime success 必须来自 found_factor parsed output，不能把 no-factor-only partial output 当作 `["91"]`；real strict arithmetic profile 会在存在替代模型时排除已实测不适配的 Qwen 条目；raw-only failure injection 即使在 real-transport suite 中也保持 scripted invalid raw stimulus。2026-07-02 补充 `ai_profile_settings.json` 记录 real_transport、request limits、JSON parser policy、range inputs、config digest 和模型过滤信息，并让 deterministic baseline artifacts 持久化到 profile 输出目录而非临时目录。 |
| `src/tokenshare/experiments/run_ai_profile.py` | TDD 第 11、13 节 / 2026-06-30 AI profile CLI | `python -m tokenshare.experiments.run_ai_profile` 命令入口，默认写 `outputs/experiments/ai_profile/`；真实 transport 仍需显式 `--real-transport` 且本地 config 至少有一个可用 API key。 |
| `src/tokenshare/experiments/run_all.py` | TDD 第 11 节 / 2026-06-29 CLI 补丁 / 2026-06-30 AI profile opt-in | `python -m tokenshare.experiments.run_all` 命令入口，默认运行 Experiment 1-4 suite，输出到 `outputs/experiments/`，可读取 gitignored local AI API config 并只把 secret 注入当前进程环境；新增 `--run-ai-profile` 显式 opt-in 后附带运行 AI profile suite。 |
| `src/tokenshare/experiments/__init__.py` | TDD 第 5 节 | Phase 8 public exports，包含 `run_ai_profile_suite`。 |

## 2. Test Map

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/experiments/test_phase8_models.py` | `SimulationProfile` digest、`ExperimentRun` run_id/status、blocked reason 必填。 |
| `tests/experiments/test_phase8_runner_reports.py` | 单 case runner：factorization semiprime report/metrics/CSV、Lean direct proof ready path、Lean decomposition/merge ready path、可注入 Lean blocked gate、默认真实 Lean preflight 缺 toolchain blocked、failure injection canonical pollution=0、ablation expected degradation、AI API usage/cost 从 submission artifact 复算。 |
| `tests/experiments/test_phase8_default_suite.py` | 默认 Experiment 1-4 case 矩阵和 suite 汇总；Experiment 4 包含 factorization semiprime passed、Lean direct proof passed、Lean decomposition/merge passed，均带真实 checker evidence；2026-07-02 覆盖 suite-level `phase8_experiment_settings.json`。 |
| `tests/experiments/test_phase8_simulation.py` | `SimulationWrapper` 五类 fault 和五个 ablation mode 决策记录。 |
| `tests/experiments/test_run_all_cli.py` | CLI 入口运行默认 suite、写 `phase8_suite_report.json`、包含 Lean direct proof / decomposition merge run manifest。 |
| `tests/experiments/test_ai_profile_suite.py` | AI profile suite 输出 schema、raw / parsed / parse failure refs、provider/model、usage、cost、latency、parser success、deterministic vs ai_api 对比、独立 CLI 和 `run_all --run-ai-profile` opt-in 接线；2026-06-30 新增 token budget、no-factor-only false success、strict arithmetic model filter 和 raw-only real-transport injection 回归；2026-07-02 覆盖 `ai_profile_settings.json` 和 deterministic baseline artifact root 持久化。 |

## 3. Boundary Notes

- Phase 8 只新增 `tokenshare.experiments` 层；未修改 `tokenshare.core`、`ProtocolEngine`、factorization verifier / merge policy、Phase 7 executor 或 storage authority。
- Factorization 实验 adapter 复用插件公开 fixture helper，不复制 divisibility verifier 或 merge policy 领域逻辑。
- Failure injection / ablation 当前是实验 wrapper 报告层语义，用于论文退化/隔离指标；不把错误路径改成 protocol core 默认行为。
- Metrics 从 JSONL events 和 artifact manifest / content digest 复算 evidence；不信任脚本内联成功布尔值作为唯一证据。
- Lean adapter 默认使用真实 preflight、checker logs、proof artifacts 和 `EnvironmentRef` 声明 Experiment 4 Lean 侧通过；如果这些 evidence 缺失，只能通过 blocked / pending path 报告，不能使用 `lean_stub` 或 synthetic evidence。
- 人类可通过 `conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs/experiments --seed 1` 一次性运行默认 Experiment 1-4；该入口不扩大 protocol core，也不改变 adapter contract。
- 人类可通过 `conda run -n tokenshare python -m tokenshare.experiments.run_ai_profile --output-root outputs/experiments/ai_profile --seed 1` 单独运行 AI profile suite；默认 fake transport 不联网、不读取 secret，但仍持久化真实 `AIAPIExecutor` 输出 artifacts 和 submission event。
- 人类可通过 `--real-transport` 运行真实 AI profile；strict arithmetic profile 会基于模型适配诊断过滤已知不适配的 Qwen 条目，raw-only failure injection 仍保持受控 scripted stimulus，因此该 suite 同时可比较真实模型正常 JSON 输出和 parser failure 隔离。
- `run_all --run-ai-profile` 只是显式附带报告，不改变默认 Experiment 1-4 suite 语义；本地 config 存在但没有可用 API key 时，默认 scripted profile 仍使用内置 fake config，避免占位 local 文件破坏 baseline。
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
- 2026-06-30 AI profile suite 补强：RED 1 `tests\experiments\test_ai_profile_suite.py -q` first failed with `ModuleNotFoundError: No module named 'tokenshare.experiments.ai_profile'`; GREEN after adding `ai_profile.py` and `run_ai_profile.py` passed with `2 passed in 2.40s`。RED 2 `run_all --run-ai-profile` targeted test failed with unrecognized argument `--run-ai-profile`; GREEN after opt-in wiring passed with `tests\experiments\test_ai_profile_suite.py -q` = `3 passed in 20.15s`。最新验证待本轮最终完整验证记录为准。
- 2026-06-30 real AI strict parser 修复：RED 增量覆盖 token budget 仍为 512、no-factor-only partial output 被误判为 semiprime success、缺少 strict arithmetic model filter、raw-only failure injection 在 real-transport suite 中误走正常 real prompt。GREEN `tests\experiments\test_ai_profile_suite.py -q` 通过，结果 `7 passed in 20.94s`；影响面 `tests\executors tests\plugins\factorization tests\experiments tests\test_phase7_ai_api_execution_flow.py -q` 通过，结果 `111 passed, 1 skipped in 65.25s`；真实 transport `run_ai_profile --output-root outputs\experiments\ai_profile_real_fixed4 --seed 1 --real-transport` 退出码 0，`ai_api_semiprime_range_flow` passed，`parser_success_rate=1.0`，`final_correctness=true`，`prime_factors=["7","13"]`，`parse_failure_count=0`，`provider_attempt_count=4`，model `MiniMaxAI/MiniMax-M2.5`；`ai_api_parse_failure_raw_only` passed with `parse_failure_count=1`。
- 2026-07-02 论文实验 setting / result 重跑补强：RED `tests\experiments\test_phase8_default_suite.py tests\experiments\test_ai_profile_suite.py -q` 先失败于缺少 `settings_path`；GREEN after adding `phase8_experiment_settings.json` / `ai_profile_settings.json` passed with `8 passed in 42.25s`。随后发现 AI profile deterministic baseline artifact root 指向已删除临时目录；新增回归先失败，修复为持久化到 `deterministic_semiprime_range_flow/fixture_flow/artifacts` 后 targeted passed with `1 passed in 1.09s`。实验测试 `tests\experiments -q` 通过，结果 `21 passed in 72.89s`。清空旧 `outputs\experiments` 后重跑默认 suite：`run_all --output-root outputs\experiments --seed 1` 退出码 0，`total_runs=17`、`passed_runs=12`、`inconclusive_runs=5`、`failed_runs=0`、`blocked_runs=0`，写出 `phase8_experiment_settings.json`、`phase8_suite_report.json`、`phase8_suite_summary.csv`。真实 AI profile `run_ai_profile --output-root outputs\experiments\ai_profile_real --seed 1 --real-transport` 退出码 0，AI semiprime profile passed，`parser_success_rate=1.0`、`final_correctness=true`、`raw_output_count=4`、`parsed_output_count=4`、`parse_failure_count=0`、model `MiniMaxAI/MiniMax-M2.5`、`usage.total_tokens=7469`、`cost_estimate_total=0.0041459999999999995`、`latency_ms_total=45858`；raw-only profile preserved `parse_failure_count=1`。Secret scan over 390 output files checked one local secret value and passed with no leak。
