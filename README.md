# TokenShare

TokenShare 是一个早期本地研究原型，用来验证一种协议内核：把大型任务递归拆分、分派、验证、合并、结算，并能从 append-only 事件日志重放全过程。

当前 V1 目标是用 Python、SQLite、JSON、JSONL 和本地文件系统做一个可复现实验实现，跑通 factorization 和真实 Lean 形式化证明插件两类 proof-of-concept 实验，并使用已完成的 Phase 7 实验级 AI API executor 验证真实模型输出效果。2026-06-29 起，Phase 6 最后一个 planned plugin（structured report stub）已从开发计划中剔除；历史文档、早期测试夹具或 schema 示例中的 `structured_report_stub` 名称只保留为 provenance / 通用插件夹具，不代表后续还要开发该插件。

## What It Is

TokenShare 第一阶段不是某个具体任务程序，而是一个本地协议框架。它要证明复杂任务可以通过统一协议被拆成 `TaskUnit`，交给不同执行器处理，再由插件验证、合并和记录贡献。

V1 重点验证这些闭环：

- 根任务注册。
- 递归任务图和依赖关系维护。
- lease、attempt、retry 和 late submission 隔离。
- artifact 持久化和内容哈希校验。
- 插件化验证、展开和合并。
- 唯一 canonical output bundle 选择。
- sandbox 贡献结算。
- 从 JSONL event ledger 重放最终状态。

## What It Is Not

V1 明确不是生产网络，也不尝试一次性实现最终愿景。

TokenShare V1 不做：

- 真实区块链、钱包、智能合约或真实代币支付。
- 真实分布式网络、HTTP worker pool 或 P2P runtime。
- 生产级身份、权限、反女巫或拜占庭容错系统。
- 完整 Web UI 或动态第三方插件市场。
- 生产级 AI API 平台、多租户 provider 管理或动态模型市场。
- 生产级 theorem-proving 平台、LeanDojo 训练/检索平台或动态 Lean 服务；但 Phase 6 必须实现本地真实 Lean checker 驱动的形式化证明插件。

## V1 Scope

V1 是本地可复现实验用的协议内核，范围包括：

- 协议基础对象：`TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`ArtifactRef`、`LedgerEvent`、`ProtocolConfig`。
- 状态机：`TaskUnit`、`Lease`、`Attempt`、`ContributionRecord`。
- 本地存储：SQLite、JSON、JSONL、本地 artifact 文件。
- append-only `EventLedger`，用于状态重放和审计。
- 固定版本的 `PluginRegistry` 和 `ExecutorRegistry`。
- `ArtifactStore` 写入、读取和内容哈希校验。
- 调度、租约、执行尝试、验证、正式输出选择、展开、合并、恢复和结算。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟；该能力作为独立实验基础设施部分，不再归入 Phase 6 插件实现范围。
- 指标报告、状态重放、审计重放和 sandbox 结算；指标报告作为独立实验基础设施部分实现。

具体字段、SQLite 表结构和插件 API 会在实现阶段逐步细化。README 只记录已经稳定的项目边界和启动方式。

## Proof-of-Concept Experiments

V1 当前计划包含两类实验插件，实验设计和论文实验口径以 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 为唯一权威。所有可写入论文的新实验必须实际调用真实 AI API；旧 deterministic/scripted、toy/stub 或 direct benchmark 只能用于回归、输入来源和成本校准。

- **factorization**：验证普通可拆分计算任务。当前规划的插件就是主 TDD 第 14.1 节的整数分解插件；第一版字段规格已收束为候选因子搜索空间分区、bounded range search、结果验证、all-required merge、prime / semiprime fixture 闭环。`one_success`、提前完成、sibling pruning 和 composite cofactor 的完整递归 resolution 已明确不属于第一切片。
- **Lean formal proof**：验证真实形式化证明工作流。simple case 可使用插件内确定性 helper；medium/hard case 使用 catalog/脚本预注册、版本化的固定 lemma-DAG，由 Lean 插件校验并生成 certificate。当前实现不从任意 theorem 通用自动发现 lemma-DAG，AI 只生成被分派 proof unit 的候选证明；候选 proof artifact 必须通过固定本地 Lean/lake/toolchain/library 环境真实检查，checker 日志和环境身份持久化，replay 不重新运行 Lean 补历史事实。
已剔除：

- **structured report stub**：曾用于规划大型自然语言任务的结构化拆分、弱验证、覆盖率检查和 `MergePlan` 合并流程；2026-06-29 起不再作为 Phase 6 待开发插件。

这些实验是协议扩展性的验证对象，不应被硬编码进协议核心。

最新实验设计把论文实验分成五组：

- **Experiment 1 - 真实 AI 跨领域可行性与难度**：factorization 使用 easy/medium/hard=`167/167/166`、合计 500 道，Lean 使用 3 个 paper difficulty × 3 个 topic family × 每格固定 15 道、合计 135 道，分别报告完成率、accepted result validity、时间、token、成本和失败边界。
- **Experiment 2 - 真实 AI worker 扩展性**：固定任务和模型，比较 1/3/7/10/30/50 workers；`worker_count` 与 provider inflight limit 分开记录。
- **Experiment 3 - 真实 AI 故障注入与 worker death 恢复**：真实 API 输出后注入 false positive、false negative、不返回、延迟、executor error 和独立 worker process death，报告检测、恢复和成本曲线。
- **Experiment 4 - 真实 AI 协议消融**：每次只关闭 verification、parser policy、requeue、merge gate 或 slot integrity 中的一个机制。
- **Experiment 5 - 三模型 model-provider endpoint comparison**：当前 cohort v2 比较 SiliconFlow GLM-5.2（thinking enabled）、官方 DeepSeek-V4-Pro high 和 OpenAI GPT-5.6 Sol high，三个端点 `max_tokens=8192`；不使用 strong/weak/mixed 标签，旧含 Qwen 的 v1 只供历史 replay，且跨 provider 的延迟/成本差异不能解释为纯模型效应。

除 Experiment 5 外，Experiment 1–4 的 pilot、正式 condition、故障恢复 attempt 和消融 mode 均固定使用官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`，thinking enabled、`reasoning_effort=high`、`timeout_seconds=600`、`max_tokens=300000`，默认使用版本化的 `exp1_baseline_provider_config.v3.json`；配置或 identity 证据不满足时结构化停止，不自动切换模型。Experiment 5 cohort v2 保持三个端点公共 `timeout_seconds=100`、`max_tokens=8192`。DeepSeek 本地 estimate 使用 provider-reported usage；CNY 与 USD 不直接合计。

2026-07-29 起，正式 Experiment 3 不再重复执行 0% 或 dedicated no-kill baseline，而是按同一 `case_id` 引用正式 Experiment 1 已持久化的 terminal evidence；Exp3 rates 为 Factorization `1/5/10/25/50/100%`、Lean `10/50/100%`，合计 36,126 roots。回归入口 `local/run_exp3_exp4_v3_smoke.ps1` 对应 `paper_smoke_exp3_exp4_v1` 的精确 11-root、无 baseline/supporting-root smoke；它永久 `paper_eligible=false`，必须使用全新的 output/supervision 路径，并且真实执行仍需用户另行授权。

## Architecture Principles

TokenShare 的核心边界是三层：

- **协议框架**：维护任务生命周期、不变量、状态机、调度、验证编排、正式输出选择、事件日志、恢复和结算。
- **任务插件**：声明任务域 schema、拆分策略、验证规则、合并规则和能力要求。
- **执行器**：实际处理已经确定的 `TaskUnit`，返回统一 `ExecutionSubmission`。

关键原则：

- 协议核心不理解 factorization、Lean 或历史 structured report fixture 的领域逻辑。
- 客户端和执行器不能直接修改任务图，也不能临时提出协议级子任务；图更新只能由协议框架根据版本化插件拆分策略写入。
- 候选输出必须先通过验证，再由协议绑定唯一 canonical output bundle。
- 非确定性输出必须持久化；状态恢复不能重新调用 AI 或 executor 来假装结果一致。
- event、plugin、artifact schema 都要显式版本化，以支持 replay。

## Quick Start

Windows PowerShell：

```powershell
.\init.ps1
# feature 完成、提交/合并或发布实验结果前
.\init.ps1 -Full
# Lean 相关完成门禁（增量）；共享 checker/toolchain/helper 变化或正式发布再追加 -ForceAllLeanAudit
.\init.ps1 -Full -LeanAudit
```

Bash、Git Bash 或 WSL：

```bash
./init.sh
# feature 完成、提交/合并或发布实验结果前
./init.sh --full
# Lean 相关完成门禁（增量）；共享 checker/toolchain/helper 变化或正式发布再追加 --force-all-lean-audit
./init.sh --full --lean-audit
```

两个 wrapper 都只启动一次目标 conda Python，并把验证交给统一入口：

```bash
conda run -n tokenshare python verification/run_verification.py --mode fast
```

统一入口运行 JSON/SQLite、harness、排除 `reference_repos/` 的 `compileall`，随后由 `verification/fast-tests.txt` 或完整 `pytest tests` 选择测试。Fast 不启动真实 Lean；Full 包含固定小常数的真实 Lean canary，但不默认重跑 600-entry catalog audit；`LeanAudit` 用内容寻址 manifest 只重检失效 entry。普通 catalog load 只验证 tracked evidence，stale 时 fail closed，不会为“省事”跳过错误或隐式触发几分钟的全量 Lean。通用规则见 `Doc/TechnicalDocument/2026-07-22-lean-checker-verification-profiles-design.md`。

`init.ps1` 和 `init.sh` 默认使用 conda 环境 `tokenshare`，可通过 `TOKENSHARE_CONDA_ENV` 临时覆盖环境名。Python 依赖可通过 `pip install -r requirements.txt` 安装。默认快速档用于启动和开发循环，不能替代 feature 完成、提交/合并或实验发布前的完整验证。

## Run Experiments

以下现有命令属于 Phase 8 回归与校准入口，不是 2026-07-12 新论文主实验入口。新论文 runner 已落地为 `tokenshare.experiments.run_paper_experiments`，正常执行通过 `paper_dispatcher`、`tokenshare.local_runtime` 和 `ProtocolEngine` 推进生命周期；正式运行仍必须满足唯一权威设计中的 real-transport、catalog、预算、provider evidence 和最终验证门禁。旧命令输出不得写成新实验结论。

### Experiment 1–4-only 真实 API smoke profile

当前 Exp1–4-only smoke 固定选择 21 个 direct roots，worker-death 另调度一个 distinct no-kill supporting baseline，因此固定分母为 22。实际执行必须通过 `local/run_exp1_exp4_v3_smoke.ps1`，launcher 会先运行不读取 secret、不创建 output root 的 `--smoke-identity-only` 预检，再把当前 output root 生成的 `execution_plan_digest` 和 `budget_digest` 动态冻结到实际 runner 命令。缺少显式 v3 config，或 provider/model/endpoint/thinking/reasoning/timeout/max-tokens/config digest 任一漂移时，runner 都会在 provider dispatch 前写 structured blocked，且 provider call 数为 0。

```powershell
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$runId = "paper_smoke_exp1_exp4_v3_${stamp}_run03"
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\local\run_exp1_exp4_v3_smoke.ps1 `
  -RunId $runId `
  -OutputRoot "outputs/experiments/$runId" `
  -SupervisorRoot "local/supervision/$runId"
```

该命令只运行 Exp1–4 smoke/regression，不运行 Experiment 5、pilot、正式实验或 Full 实验矩阵。跨 run 必须稳定的是 profile/catalog/selection/config、模型与请求控制、repeat/retry 以及 21 direct + 1 supporting 分母；`execution_plan_digest` 和 `budget_digest` 绑定当前绝对 `output_root`，因此新 root 应生成新值。launcher 只把本次 root 的两个运行实例 digest 冻结并回传给 runner；同一 root 内的 identity 漂移仍 fail closed，不得拿历史 root 的两个 digest 阻止合法新 run。v3 固定为官方 DeepSeek `deepseek-v4-pro` / `deepseek_v4_pro_exp1_baseline`、thinking enabled/high、`timeout_seconds=600`、`max_tokens=300000`、单 AI unit 一次 provider attempt；输出始终 `paper_eligible=false`。

### Experiment 1–5 独立 API smoke profile

`benchmarks/paper/paper_smoke_profile.v2.json` 从正式 frozen catalog 和 canonical plans 语义解析 27 个直接 root-runs：Exp1 六题、Exp2 三个 worker level、Exp3 no-fault/五类 rate-fault/worker-death、Exp4 FULL/四种正式消融、Exp5 两题×三个固定 endpoint。v2 将 Experiment 1–4 selector 固定到官方 DeepSeek baseline，并将 Experiment 5 selector 固定到 GLM/DeepSeek/GPT cohort v2；`paper_smoke_profile.v1.json` 原样保留，仅供旧 GLM/Qwen cohort 的历史 replay。worker-death 生产路径还会执行一个 distinct no-kill supporting baseline，因此预算中的实际调度 root 数为 28。该 profile 不改变正式 Experiment 1–5 的题库、condition、worker、repeat、fault、ablation、模型或 request controls。

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_paper_experiments `
  --smoke-profile benchmarks/paper/paper_smoke_profile.v2.json `
  --output-root outputs/experiments/paper_smoke_v2 `
  --real-transport `
  --ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json `
  --provider-config siliconflow=local/ai_api_smoke.local.json `
  --provider-config deepseek=benchmarks/paper/exp1_baseline_provider_config.v2.json `
  --provider-config openai=local/openai_api_smoke.local.json `
  --model-cohort-file benchmarks/paper/model_comparison_cohort.v2.json `
  --model-entry-map local/model_comparison_entries.local.json `
  --unlimited-budget
```

smoke 输出始终冻结为 `formal=false`、`pilot_only=true`、`regression_only=true`、`paper_eligible=false`，不可用 CLI 升级，也不会生成正式 `paper_table_*` / `paper_plot_*`。`--unlimited-budget` 只是显式记录“无总 provider-attempt/token/cost 硬上限”：仍生成 `run_budget.json`、预算估计和真实 usage，仍保留每 AI unit 一次 provider attempt、模型/reasoning controls 和 cohort/config preflight；Exp1–4 固定 `600/300000`，Exp5 cohort v2 固定 `100/8192`。它与人工 approval/digest 及三个 `--max-total-*` 参数互斥。不传该参数时保持原有默认兼容行为。

### Experiment 5 v3 独立 8-root smoke

`benchmarks/paper/paper_smoke_exp5_profile.v3.json` 只调度四个 SiliconFlow 模型各 1 个 Factorization hard root 和 1 个 Lean hard root，共 8 direct roots、60 planned first-attempt AI units。首次 smoke 的 bootstrap preflight 不要求预先已有 passed smoke evidence；正式 Experiment 5 eligibility preflight 仍要求该 evidence。先用同一全新 `output-root` 执行 `--smoke-identity-only`，取得 path-bound execution-plan/budget digest，再把两个 digest 原样传入 `--real-transport` 命令。`--local-ai-api-config local/ai_api_smoke.local.json` 只把匹配的 SiliconFlow secret 注入当前进程，不写入安全 config 或输出。

当前旧 Experiment 1-4 regression suite 可一条命令运行，输出会写入被忽略的 `outputs/experiments/`：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs/experiments --seed 1
```

如果要单独比较 deterministic baseline 与 AI API executor 的输出质量、parser 成功率、usage、cost、latency、provider/model 和 retry，可运行旧 AI profile suite。默认路径使用 scripted fake transport，因此只能作为 regression/calibration：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_ai_profile --output-root outputs/experiments/ai_profile --seed 1
```

也可以在运行默认 Experiment 1-4 后一并写出 AI profile 报告：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs/experiments --seed 1 --run-ai-profile
```

AI profile 当前覆盖 `deterministic_semiprime_range_flow`、`ai_api_semiprime_range_flow` 和 `ai_api_parse_failure_raw_only` 三个 profile，并写出 `ai_profile_suite_report.json` / `ai_profile_summary.csv`。真实 SiliconFlow transport 仍是显式 opt-in，需要本地 config 至少有一个已启用 key。

如果要专门校准 Lean proof 子任务，可运行旧 Lean AI 50 benchmark。当前 50 个任务严格限制在 `P ∧ Q` 与 `P ↔ Q` 浅层切片；默认 scripted transport 不联网，不能替代新设计要求的三档难度真实 API 实验：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_lean_ai_benchmark --output-root outputs/experiments/lean_ai_50 --count 50 --seed 1
```

真实 SiliconFlow transport 需要显式 opt-in，并复用 gitignored `local/ai_api_smoke.local.json` 的安全注入边界：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_lean_ai_benchmark --output-root outputs/experiments/lean_ai_50_real --count 50 --seed 1 --real-transport
```

本地已验证 scripted full 50 任务全部通过；真实 transport 先以 `--count 1` smoke 通过，避免默认消耗 100 次真实 proof-candidate API 调用。

如果要获得“输入为待分解大整数、输出为直接准确率”的模型校准，可运行 direct 500-number suite。它绕过 factorization 协议内部 range-child lifecycle，不能单独作为新论文的协议可行性或 worker 扩展性证据：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_factorization_500_ai --output-root outputs/experiments/factorization_500_ai_real_glm_seed1_count500_workers10_retry_20260702 --count 500 --seed 1 --real-transport --entry-id glm_5_2__sf_key_1 --max-provider-attempts 1 --max-tokens 256 --timeout-seconds 20 --worker-count 10
```

该 suite 会写出 `factorization_500_ai_settings.json`、`input_numbers.jsonl`、`oracle_answers.jsonl`、`per_number_results.jsonl`、`per_number_summary.csv`、`progress_report.json` 和 `batch_report.json`。2026-07-02 的真实 SiliconFlow / GLM-5.2 运行结果为 `attempted_count=500`、`correct_count=476`、`accuracy=0.952`、`failure_breakdown={"executor_error": 23, "product_mismatch": 1}`。

真实 SiliconFlow smoke 可用本地 gitignored JSON 配置，不需要手动设置 API key 环境变量。默认路径是 `local/ai_api_smoke.local.json`，该文件被 `.gitignore` 覆盖；loader 会把 `api_keys` 中已填写的 `api_key` 仅注入当前进程环境变量，并把 key 池和 `models` 模型池展开成标准 `entries`，再走原来的 `api_key_env` 安全边界。secret 不进入 event、artifact、SQLite、日志或 config digest。

```json
{
  "schema_version": "phase7.ai_api_executor_config.v1",
  "executor_id": "executor_ai_api",
  "provider_family": "siliconflow",
  "selection_policy": {"kind": "uniform_random_without_weights", "seed_source": "request_or_environment_seed"},
  "defaults": {
    "timeout_seconds": 60,
    "max_tokens": 64,
    "temperature": 0.2,
    "top_p": 0.9,
    "stream": false,
    "max_provider_attempts": 6
  },
  "api_keys": [
    {"key_id": "sf_key_1", "api_key": "PASTE_SILICONFLOW_API_KEY_1_HERE"},
    {"key_id": "sf_key_2", "api_key": "PASTE_SILICONFLOW_API_KEY_2_HERE"},
    {"key_id": "sf_key_3", "api_key": "PASTE_SILICONFLOW_API_KEY_3_HERE"}
  ],
  "models": [
    {"model_id": "qwen3_6_27b", "model": "Qwen/Qwen3.6-27B", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": true, "pricing": {"currency": "CNY", "input_per_million_tokens": 0.3, "output_per_million_tokens": 3.2}},
    {"model_id": "deepseek_v4_pro", "model": "deepseek-ai/DeepSeek-V4-Pro", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": true, "pricing": {"currency": "CNY", "input_per_million_tokens": 1.6, "output_per_million_tokens": 3.135}},
    {"model_id": "minimax_m2_5", "model": "MiniMaxAI/MiniMax-M2.5", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": true, "pricing": {"currency": "CNY", "input_per_million_tokens": 0.3, "output_per_million_tokens": 1.2}},
    {"model_id": "glm_5_2", "model": "zai-org/GLM-5.2", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": false, "pricing": {"currency": "CNY", "input_per_million_tokens": 1.4, "output_per_million_tokens": 4.4}},
    {"model_id": "step_3_5_flash", "model": "stepfun-ai/Step-3.5-Flash", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": false, "pricing": {"currency": "CNY", "input_per_million_tokens": 0.1, "output_per_million_tokens": 0.3}},
    {"model_id": "tencent_hy3_preview", "model": "tencent/Hy3-preview", "base_url": "https://api.siliconflow.cn/v1", "supports_json_mode": false, "pricing": {"currency": "CNY", "input_per_million_tokens": 0.066, "output_per_million_tokens": 0.26}}
  ],
  "local_concurrency": {"max_in_flight_global": 1},
  "metadata": {"purpose": "local smoke"}
}
```

本仓库已创建完整模板文件；通常只需要把 `api_keys[].api_key` 的占位符替换成实际 SiliconFlow key。未填写的 `PASTE_` / `REPLACE_` key 会自动禁用；填 1 个 key 会生成 6 个候选 entry，填 3 个 key 会生成 18 个候选 entry。当前真实 smoke 使用 raw text prompt，因此六个模型都会进入随机候选；JSON 任务仍会按 `supports_json_mode` 过滤。

配置好后可显式运行真实 smoke：

```powershell
$env:PYTHONPATH='src'
$env:TOKENSHARE_RUN_SILICONFLOW_SMOKE='1'
conda run -n tokenshare python -m pytest tests\executors\test_ai_api_siliconflow_smoke.py -q
```

## Repository Map

关键文件：

- `AGENTS.md`：agent 工作规则、项目边界、启动流程和完成标准。
- `feature_list.json`：feature 路线图和状态源。
- `progress.md`：当前进度、验证证据、风险和下一步。
- `session-handoff.md`：下轮会话恢复信息。
- `requirements.txt`：可由 `pip install -r requirements.txt` 安装的 Python 依赖清单。
- `init.ps1`：Windows PowerShell 启动验证。
- `init.sh`：Bash/Git Bash/WSL 启动验证。
- `src/tokenshare/`：TokenShare Python package，实现协议核心、存储、插件、执行器、重放和实验模块边界。
- `src/tokenshare/local_runtime/`：本地系统应用协调层，统一驱动 scheduler、lease、executor、plugin 和 `ProtocolEngine`；不承载论文矩阵或领域算法。
- `src/tokenshare/plugins/factorization/runtime_adapter.py`：Factorization runtime bridge，提供 range plan、request/parser/verifier 与 merge 领域规则。
- `src/tokenshare/plugins/lean_proof/{fixed_plan,runtime_adapter}.py`：Lean 预注册 fixed plan 校验/certificate 与 proof-unit/checker/merge runtime bridge。
- `src/tokenshare/experiments/paper_projection.py`：从系统 ledger/artifacts 只读派生 `PaperTaskResult` / `PaperAttemptResult`，不参与协议决策。
- `src/tokenshare/experiments/factorization_500_ai.py`：direct 500-number AI factorization benchmark，实现输入生成、真实/脚本 transport、逐题 artifact/event 记录、oracle 校验和准确率报告。
- `src/tokenshare/experiments/run_factorization_500_ai.py`：direct 500-number benchmark CLI，默认 scripted transport，`--real-transport` 才调用真实 SiliconFlow API，并支持 `--entry-id` / `--worker-count` 控制真实模型和并发。
- `src/tokenshare/experiments/lean_ai_benchmark.py`：Lean AI 50 benchmark 实现，生成当前 helper 支持的 50 个证明任务并经 AI executor、parser、checker、merge 写出报告。
- `src/tokenshare/experiments/run_lean_ai_benchmark.py`：Lean AI 50 benchmark CLI，默认 scripted transport，`--real-transport` 才调用真实 SiliconFlow API。
- `tests/`：与 package 边界镜像的 pytest 测试。
- `reference_repos/`：package layout 研究用的外部参考源码浅克隆，不属于 TokenShare runtime。
- `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md`：Phase 1-6 收敛后的默认完整说明，覆盖协议对象、状态机、event/artifact/SQLite 机制、执行链、factorization 插件和真实 Lean proof 插件。
- `Doc/TechnicalDocument/tokenshare_v1_code_map.md`：Phase 1-6 收敛后的默认 code map，以当前 `src/` 和 `tests/` 为准映射代码、测试、event 和 SQLite projection。
- `Doc/TechnicalDocument/phase-1-6-archive/`：旧 Phase 1-6 阶段文档普通归档目录；不作为默认阅读入口，也不维护单独索引。
- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`：唯一权威实验设计；固定真实 AI API 门槛、Experiment 1-5、输入 catalog、输出 schema、预算、代码改造与论文结果口径。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md`：Phase 7 实验级 AI API executor 字段规格。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`：Phase 7 实验级 AI API executor TDD 实施规划。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`：Phase 7 AI API executor 代码、测试、字段规格章节、验证证据和协议边界映射。
- `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`：已实现 Phase 8 regression infrastructure 的 source/tests/验证映射；不是实验设计权威。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：论文、技术报告、本地 TeX/OCR 与模块借鉴映射。
- `Doc/TechnicalDocument/tokenshare-paper-tex/`：已本地化的论文/技术报告 TeX 或 OCR 文本。
- `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 机制整合记录；只用于追溯取舍理由，不覆盖主 TDD。
- `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：协议内核讨论稿。
- `Doc/agent-navigation.md`：agent 导航、模块路由和外部参考资料落库规则。

## Development Workflow

开发时以 `feature_list.json` 为状态源。当前 active feature 是 `feat-011` Paper Real AI Experiments；`feat-007` Phase 6 real Lean formal proof plugin、`feat-008` Phase 7 AI API executor 和 `feat-009` Phase 8 实验基础设施已完成并标记 done。`feat-010` Phase 9 replay and audit 已从当前剩余必做开发路径中延后，不再作为开始论文实验前的下一步 phase。

开始写代码前：

1. 确认工作目录是仓库根目录。
2. 阅读 `AGENTS.md`。
3. 阅读 `Doc/agent-navigation.md`，再按当前 feature 阅读对应权威文档；Phase 1-6 默认只读 `tokenshare_v1_complete_spec.md` 和 `tokenshare_v1_code_map.md`。
4. 运行 `.\init.ps1` 或 `./init.sh`。
5. 阅读 `feature_list.json`、`progress.md` 和 `session-handoff.md`。
6. 常规文件读取和搜索使用 PowerShell，并显式使用 UTF-8；中文文档读取用 `Get-Content -Encoding UTF8`，文本检索用 `Select-String`，不要把 `rg` 作为默认检索工具。
7. 开始具体设计或编码前，确认模块归属、参考资料边界和当前 feature 范围。

如果开发中联网查找资料，并且资料影响项目设计、代码、测试或文档，必须先按 `Doc/agent-navigation.md` 完成本地落库和索引同步：论文/报告进入本地论文映射，开源项目进入 `reference_repos/`，普通在线文档至少记录来源、访问日期、本地摘要和影响范围。

完成一个 feature 前必须有验证证据。没有实际验证输出，不应把 feature 标记为完成。

## Current Status

当前日期状态：2026-07-23。

已完成：

- 启动 harness 已建立。
- `init.ps1` / `init.sh` 基线验证已建立。
- V1 路线图已写入 `feature_list.json`。
- 当前项目边界已写入 `AGENTS.md`。
- package layout 已确定并创建：`src/tokenshare/{core,storage,plugins,executors,local_runtime,replay,experiments}` 与镜像 `tests/`。
- Phase 1 协议基础对象与本地存储已实现：root task registration、artifact save/read/hash、JSONL event append/read/hash chain、SQLite 可重建索引。
- Phase 2 最小协议内核已实现：`TaskGraph`、`TaskUnit` / `Lease` / `Attempt` 状态机、FIFO `Scheduler`、`LeaseManager`、Phase 2 event type、SQLite `leases` / `attempts` / `recovery_actions` 投影，以及顶层 `ProtocolEngine` 调度、heartbeat 和 lease expiry 事件流。
- Phase 3 插件与执行器契约已实现：`PluginRegistry`、`PluginDescriptor` / `SplitStrategyContract`、`ExecutorRegistry`、`ExecutionRequest`、`ExecutionSubmission`、`MockAIExecutor`、`DeterministicLocalExecutor`、Phase 3 event type、`Attempt.Running -> Submitted` 状态推进，以及 SQLite `registry_snapshots` / `execution_requests` / `execution_submissions` / `executor_statuses` index-only 投影。
- Phase 1-6 旧阶段文档已收敛为 `Doc/TechnicalDocument/tokenshare_v1_complete_spec.md` 和 `Doc/TechnicalDocument/tokenshare_v1_code_map.md`；旧文档已移动到 `Doc/TechnicalDocument/phase-1-6-archive/`，不再作为默认阅读入口。
- P01-P22 候选机制已整合进主 TDD：requirements/hints、expected output、environment、allocation、verification/selection、merge、settlement 和 replay 边界现在以主 TDD 为实现口径。
- Phase 4 已完成：`LedgerEvent.v2` batch envelope、`EventLedger.append_batch()`、verification report、canonical output binding、split invocation audit、complete path、accepted expand path、atomic graph update、ExpectedOutputRef 和 SQLite Phase 4 index-only projection 均已实现。
- Phase 5 已完成：merge task creation、merge resolution、parent completion、root settlement 和 subtree pruning 均有 batch 边界、SQLite projection 和测试覆盖。
- Phase 5 merge / contribution / settlement 主闭环已实现：merge task creation、merge resolution、canonical contribution creation、parent completion、root-level sandbox settlement、subtree pruning、SQLite Phase 5 projection，以及完整 merge -> parent completion -> root settlement projection integration。
- 2026-06-27 Phase 5 hardening 已完成：SQLite rebuild 会拒绝错误 Phase 5 batch id；root settlement 要求 caller supplied eligible contribution set 精确等于 ledger 当前 eligible set。
- 最新完整启动验证证据以 `progress.md` 顶部和 `feature_list.json` 为准；2026-07-12 状态同步已切换到 `feat-011` Paper Real AI Experiments，项目当前只剩最新真实 AI 论文实验的实现、执行、指标和论文表图收尾。
- Phase 6 factorization 插件第一版已完成：插件主导候选因子搜索空间分区、bounded `factor_search_range`、deterministic `range_result` verifier、all-required merge、prime / semiprime fixture 闭环已实现；early success / sibling pruning / composite cofactor 完整递归 resolution 不属于第一切片。
- Phase 6 真实 Lean proof plugin 已完成：Lean 插件不再是 stub / synthetic-only proof；当前实现使用本地真实 Lean checker、固定 Lean/lake/elan fixture project、结构化 theorem payload、Lean-side split helper、checker artifact、child proof、merge proof 和 replay evidence guard。
- 2026-06-28 Phase 7 实验级 AI API executor 已完成并映射：SiliconFlow-only 第一版、request-scoped provider failover、artifact-backed raw/parsed/parse-failure/provenance/usage/cost、secret redaction、plugin parser bridge 和 replay no-call guard 已实现。
- 2026-07-12 新真实 AI API 论文实验设计已设为唯一权威：后续 paper runner、difficulty catalog、worker scaling、post-AI fault/worker death、ablation、预算、metrics 和论文表图都以 `tokenshare_latest_real_plugin_experiment_design.md` 为准。
- 2026-06-29 Phase 8 实验基础设施已完成并标记 done：code map 记录旧通用 runner、Experiment 1-4 regression suite、failure/ablation 报告、metrics/report、AI usage/cost 和 Lean adapter ready path；这些旧 suite 不再是新论文主实验。
- 2026-07-23 feat-011 system runtime 迁移 Task 1-9 已完成：Factorization/Lean 正常 FULL 路径共用 `ProtocolRunCoordinator` / `ProtocolEngine`，worker/fault/ablation 通过 runtime backend/hooks 注入，paper results 从 ledger/artifacts 投影；Task 10 负责文档、兼容壳与最终门禁。

当前进行中：

- `feat-011`：Paper Real AI Experiments（按 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 实现并运行真实 AI API 论文实验，包括 catalog、paper runner、budget gate、real transport eligibility、factorization / Lean adapters、post-AI faults、worker death、ablation、metrics、report 和论文表图）。

当前 Phase 6 / 实验路线：

- Phase 6 Lean track：已完成 direct proof、decomposition/child proof/merge、Phase 8 adapter ready path 和 replay/evidence guard；后续只在发现回归或新论文实验需要小范围补证时返回。
- Phase 8 track：已完成通用实验基础设施；后续只在发现回归或新论文实验 runner 需要复用 artifacts/events/metrics 边界时做小范围扩展。
- factorization 第一版只承诺 prime / semiprime fixture 端到端闭环；不宣称 early success、sibling pruning 或完整 composite cofactor recursive resolution。
- Lean 插件必须接入本地真实 Lean checker；simple helper 或预注册 fixed lemma-DAG 的拆分都不由 AI 决定，且后者必须由插件校验并生成 certificate。旧 `Lean stub proof` 路线已废弃。
- 正式实验 runner 通过 `paper_dispatcher` 构造 `ProtocolRunRequest`，由 system coordinator 调用 Factorization/Lean runtime bridge；两个 public paper adapter 只保留历史/selector regression 兼容入口，不能用 stub 或兼容直连分支替代正式系统证据。

`feat-008` / Phase 7 Experimental AI API Executor 已完成：

- 可通过统一 `ExecutionRequest` / `ExecutionSubmission` 调用真实模型 API。
- 持久化 provider、model、prompt package、raw output、parsed output 或 parse failure、usage、latency、cost 和 error provenance。
- 标准 executor config 只保存 `api_key_env`；真实 smoke 可从被 gitignore 的 `local/ai_api_smoke.local.json` 读取本地 key 并注入当前进程环境变量。API key 不写入 event、artifact、SQLite、日志或 config digest；baseline 测试不要求联网。
- replay 不重新调用 AI API，缺失历史输出 artifact 时必须失败。

- `SimulationProfile`、`SimulationWrapper`、`ExperimentRunner` 和 `MetricsCollector`。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
- work、critical path、retry/wasted work、shadow benefit 等指标报告。

当前已进入 `feat-011` / Paper Real AI Experiments。

当前 paper runner、system runtime 迁移、正式 evidence/metrics/report 门禁和独立 27-root smoke profile 均已落地并完成离线验证；尚未完成的是单独授权后的真实 API smoke/正式 Experiment 1–5、真实 evidence 审计、论文结果发布及届时要求的 Full/必要 Lean 审计。`feat-010` replay / audit 已从当前必做开发路径中延后。真实分布式 executor 网络、生产级 AI API 平台或真实链上结算仍属于 V1 范围外。`feat-007` 真实 Lean proof plugin、`feat-008` 实验级 AI API executor 和 `feat-009` 实验基础设施已完成。structured report stub 已从 Phase 6 开发计划剔除。

当前仍需注意：

- 自然语言任务的验证不是“证明文本绝对正确”，而是通过结构化 schema、证据引用、覆盖率和审计 replay 降低风险。
- Lean V1 已按 2026-06-28 范围变更调整为真实 checker 驱动的形式化证明插件；不得再按 stub/synthetic-only 路线实现。
- Experiment 4 必须使用真实 Lean proof plugin / Lean adapter；没有真实 checker logs 和 `EnvironmentRef` 时只能标记 blocked / pending，不能用 `lean_stub` 替代。当前默认 Lean adapter 已有真实 checker ready path。
- factorization 和 Lean 形式化证明插件是当前插件实验对象，不应硬编码进协议核心；历史 structured report fixture 名称不应重新扩大为待开发插件目标。
- 当前实现默认使用 `conda` 环境 `tokenshare`；如果运行时选择变化，需要同步更新 README、harness 和设计资料。
