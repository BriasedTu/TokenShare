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

V1 当前计划包含两类实验插件，实验设计和论文实验口径以 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.tex` / `.pdf` 为准。旧 toy / stub 实验不能作为论文主实验结论，`lean_stub` 结果不能替代真实 Lean plugin 结果。

- **factorization**：验证普通可拆分计算任务。当前规划的插件就是主 TDD 第 14.1 节的整数分解插件；第一版字段规格已收束为候选因子搜索空间分区、bounded range search、结果验证、all-required merge、prime / semiprime fixture 闭环。`one_success`、提前完成、sibling pruning 和 composite cofactor 的完整递归 resolution 已明确不属于第一切片。
- **Lean formal proof**：验证真实形式化证明工作流。它接收 Lean theorem / proof-state 代码 artifact，由插件内确定性拆分算法自动识别目标结构并生成子任务；候选 proof artifact 必须通过固定本地 Lean/lake/toolchain/library 环境真实检查，checker 日志和环境身份持久化，replay 不重新运行 Lean 补历史事实。
已剔除：

- **structured report stub**：曾用于规划大型自然语言任务的结构化拆分、弱验证、覆盖率检查和 `MergePlan` 合并流程；2026-06-29 起不再作为 Phase 6 待开发插件。

这些实验是协议扩展性的验证对象，不应被硬编码进协议核心。

最新实验设计把论文实验分成四组：

- **Experiment 1 - Factorization End-to-End Execution**：用 `factorization@0.1.0` 跑通 prime / semiprime fixture 的 descriptor freeze、split、execution、parser/verifier、canonical、all-required merge、settlement 和 replay。
- **Experiment 2 - Failure Injection and Recovery**：分别注入 invalid factor、false no-factor、parse failure/raw-only、worker crash/expired lease 和 no-factor recheck budget exceeded，验证错误不会污染 canonical state。
- **Experiment 3 - Protocol Ablation Study**：关闭 verification、parser policy、requeue、all-required merge gate 或 slot integrity，验证关键机制缺失时出现预期退化。
- **Experiment 4 - Cross-Plugin Generality with Real Plugins**：用 factorization plugin 和真实 Lean proof plugin 共享同一套 protocol lifecycle，验证协议核心不硬编码分解或 Lean 语义；真实 Lean checker logs 和 `EnvironmentRef` 缺失时不得声称通过。

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
```

Bash、Git Bash 或 WSL：

```bash
./init.sh
```

当前启动验证会运行：

```bash
conda run -n tokenshare python -c "import json, sqlite3; print('python-json-sqlite-ok')"
conda run -n tokenshare python -m compileall -x "reference_repos" .
PYTHONPATH=src conda run -n tokenshare python -m pytest tests
```

`init.ps1` 和 `init.sh` 默认使用 `conda` 环境 `tokenshare`，可通过 `TOKENSHARE_CONDA_ENV` 临时覆盖环境名。Python 依赖可通过 `pip install -r requirements.txt` 安装。脚本会无条件运行 Python JSON/SQLite 检查和 `compileall`。`reference_repos/` 保存外部参考源码，不参与 `compileall`。只有存在 `tests/` 目录时才在 `PYTHONPATH=src` 下运行 `pytest tests`。

## Run Experiments

当前 Experiment 1-4 默认 suite 可一条命令运行，输出会写入被忽略的 `outputs/experiments/`：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m tokenshare.experiments.run_all --output-root outputs/experiments --seed 1
```

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
- `tests/`：与 package 边界镜像的 pytest 测试。
- `reference_repos/`：package layout 研究用的外部参考源码浅克隆，不属于 TokenShare runtime。
- `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`：当前实现导向技术设计文档。
- `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`：Phase 1 最小对象字段、event envelope 和 SQLite 可重建索引规格。
- `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md`：Phase 1 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`：Phase 2 最小对象、状态机、事件顺序和 SQLite 投影规格。
- `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`：Phase 2 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`：Phase 3 插件、执行器、request/submission、artifact 和 event 字段规格。
- `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`：Phase 3 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-24-phase-4-discussion-notes.md`：Phase 4 验证、canonical output、split strategy、`MergePlan` 和原子扩图讨论记录。
- `Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md`：Phase 4 字段规格与 TDD 计划。
- `Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`：Phase 4 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md`：Phase 5 merge、expected output resolution、contribution、settlement 和 pruning 字段规格 / TDD 计划，直接指导 `feat-006` 实现。
- `Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md`：Phase 5 Task 1 / Task 2 / Task 3 / Task 4 / Task 5 / Task 6 / Task 7 / Task 8 的代码、规格章节、projection 和测试对应关系。
- `Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md`：Phase 5 merge 讨论记录和已确认取舍。
- `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md`：Phase 5 merge 主闭环外部系统调研备忘。
- `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`：Phase 6 factorization 插件第一版字段规格 / TDD 计划，直接指导 factorization 插件实现。
- `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`：Phase 6 factorization 插件第一版拆分算法和主 TDD 对齐讨论记录；用于追溯取舍，不覆盖字段规格。
- `Doc/TechnicalDocument/2026-06-28-phase-6-lean-real-plugin-scope-change.md`：Phase 6 Lean 插件范围变更记录；覆盖旧 `Lean stub` / synthetic-only 口径，要求实现本地真实 Lean checker 驱动的形式化证明插件。
- `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-tdd.md`：Phase 6 真实 Lean proof 插件 TDD 设计稿，直接指导 `lean_proof` 插件本体、结构化 theorem payload、Lean-side deterministic tactics、固定 toolchain / fixture project、真实 checker artifact、split / merge 和 Phase 8 ready path。
- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.tex` / `.pdf`：最新真实插件实验设计；Experiment 1-4 的主口径，覆盖旧 toy / stub 实验设计。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md`：Phase 7 实验级 AI API executor 字段规格。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`：Phase 7 实验级 AI API executor TDD 实施规划。
- `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`：Phase 7 AI API executor 代码、测试、字段规格章节、验证证据和协议边界映射。
- `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-tdd.md`：Phase 8 实验基础设施 TDD 设计文稿，覆盖通用实验 runner、插件适配契约、故障注入、消融、metrics/report 和 Lean pending / ready 门禁。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：论文、技术报告、本地 TeX/OCR 与模块借鉴映射。
- `Doc/TechnicalDocument/tokenshare-paper-tex/`：已本地化的论文/技术报告 TeX 或 OCR 文本。
- `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 机制整合记录；只用于追溯取舍理由，不覆盖主 TDD。
- `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：协议内核讨论稿。
- `Doc/agent-navigation.md`：agent 导航、模块路由和外部参考资料落库规则。

## Development Workflow

开发时以 `feature_list.json` 为状态源。当前 active feature 是 `feat-010` Phase 9 replay and audit；`feat-007` Phase 6 real Lean formal proof plugin、`feat-008` Phase 7 AI API executor 和 `feat-009` Phase 8 实验基础设施已完成并标记 done。

开始写代码前：

1. 确认工作目录是仓库根目录。
2. 阅读 `AGENTS.md`。
3. 阅读 `Doc/agent-navigation.md`，再按当前 feature 阅读对应字段规格和 code map。
4. 运行 `.\init.ps1` 或 `./init.sh`。
5. 阅读 `feature_list.json`、`progress.md` 和 `session-handoff.md`。
6. 常规文件读取和搜索使用 PowerShell，并显式使用 UTF-8；中文文档读取用 `Get-Content -Encoding UTF8`，文本检索用 `Select-String`，不要把 `rg` 作为默认检索工具。
7. 开始具体设计或编码前，确认模块归属、参考资料边界和当前 feature 范围。

如果开发中联网查找资料，并且资料影响项目设计、代码、测试或文档，必须先按 `Doc/agent-navigation.md` 完成本地落库和索引同步：论文/报告进入本地论文映射，开源项目进入 `reference_repos/`，普通在线文档至少记录来源、访问日期、本地摘要和影响范围。

完成一个 feature 前必须有验证证据。没有实际验证输出，不应把 feature 标记为完成。

## Current Status

当前日期状态：2026-06-29。

已完成：

- 启动 harness 已建立。
- `init.ps1` / `init.sh` 基线验证已建立。
- V1 路线图已写入 `feature_list.json`。
- 当前项目边界已写入 `AGENTS.md`。
- package layout 已确定并创建：`src/tokenshare/{core,storage,plugins,executors,replay,experiments}` 与镜像 `tests/`。
- Phase 1 协议基础对象与本地存储已实现：root task registration、artifact save/read/hash、JSONL event append/read/hash chain、SQLite 可重建索引。
- Phase 2 最小协议内核已实现：`TaskGraph`、`TaskUnit` / `Lease` / `Attempt` 状态机、FIFO `Scheduler`、`LeaseManager`、Phase 2 event type、SQLite `leases` / `attempts` / `recovery_actions` 投影，以及顶层 `ProtocolEngine` 调度、heartbeat 和 lease expiry 事件流。
- Phase 2 code map 已新增：`Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`。
- Phase 3 插件与执行器契约已实现：`PluginRegistry`、`PluginDescriptor` / `SplitStrategyContract`、`ExecutorRegistry`、`ExecutionRequest`、`ExecutionSubmission`、`MockAIExecutor`、`DeterministicLocalExecutor`、Phase 3 event type、`Attempt.Running -> Submitted` 状态推进，以及 SQLite `registry_snapshots` / `execution_requests` / `execution_submissions` / `executor_statuses` index-only 投影。
- Phase 3 code map 已新增：`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`。
- 主 TDD 已补充大型自然语言任务相关边界：`DecompositionProposal`、`VerificationReport`、`MergePlan`、`MergeRecord` 和 structured report stub。
- P01-P22 候选机制已整合进主 TDD：requirements/hints、expected output、environment、allocation、verification/selection、merge、settlement 和 replay 边界现在以主 TDD 为实现口径。
- Phase 4 已完成：`LedgerEvent.v2` batch envelope、`EventLedger.append_batch()`、verification report、canonical output binding、split invocation audit、complete path、accepted expand path、atomic graph update、ExpectedOutputRef 和 SQLite Phase 4 index-only projection 均已实现并映射到 `Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`。
- Phase 5 已完成：`Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md` 是 `feat-006` 实现口径，`Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md` 记录 Task 1-8 代码和测试映射。
- Phase 5 merge / contribution / settlement 主闭环已实现：merge task creation、merge resolution、canonical contribution creation、parent completion、root-level sandbox settlement、subtree pruning、SQLite Phase 5 projection，以及完整 merge -> parent completion -> root settlement projection integration。
- 2026-06-27 Phase 5 hardening 已完成：SQLite rebuild 会拒绝错误 Phase 5 batch id；root settlement 要求 caller supplied eligible contribution set 精确等于 ledger 当前 eligible set。
- 最新完整启动验证证据以 `progress.md` 顶部和 `feature_list.json` 为准；2026-06-29 Lean completion 状态同步中已切换到 `feat-010` Phase 9 replay / audit。
- Phase 6 factorization 插件第一版字段规格 / TDD 计划已完成：`Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md` 直接指导实现。它固定插件主导候选因子搜索空间分区、bounded `factor_search_range`、deterministic `range_result` verifier、all-required merge、prime / semiprime fixture 闭环，并明确 early success / sibling pruning / composite cofactor 完整递归 resolution 不属于第一切片。
- 2026-06-28 范围更新：Lean 插件不再是 stub / synthetic-only proof；Phase 6 第二插件必须实现本地真实 Lean checker 驱动的形式化证明能力，且拆分算法必须由插件内确定性规则自动识别 Lean theorem / proof-state 结构并生成子任务。2026-06-29 已配置固定 Lean/lake/elan 工具链和 fixture project，工具链记录见 `Doc/TechnicalDocument/2026-06-29-phase-6-lean-toolchain-setup-notes.md`。
- 2026-06-28 Phase 7 实验级 AI API executor 已完成并映射：SiliconFlow-only 第一版、request-scoped provider failover、artifact-backed raw/parsed/parse-failure/provenance/usage/cost、secret redaction、plugin parser bridge 和 replay no-call guard 已实现。
- 2026-06-29 最新真实插件实验设计已拉取并设为实验主口径：后续实验 runner、failure injection、ablation、metrics 和论文实验表格应以 `tokenshare_latest_real_plugin_experiment_design` 为准。
- 2026-06-29 Phase 8 实验基础设施已按 TDD 完成并标记 done：`Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md` 记录通用实验内核、Experiment 1-4 默认 suite、failure / ablation 报告、metrics/report、AI API usage/cost 复算和 Lean adapter ready path。
- 2026-06-29 Phase 6 真实 Lean proof plugin 已按 TDD Task 1-15 完成并标记 done：新增 `src/tokenshare/plugins/lean_proof/`、`fixtures/lean_proof_project/` 和 `tests/plugins/lean_proof/`，完成固定工具链 manifest/preflight、schema/descriptor、fixture project、真实 direct checker、validator、Lean-side split helper JSON certificate、Python split bridge、proof prompt/parser、child proof checker flow、merge policy/root proof recheck、direct/decomposition 协议 E2E fixture、Phase 8 ready path 和 replay evidence guard。当前实现映射见 `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md`。

当前进行中：

- `feat-010`：Phase 9 - Replay and Audit（state replay、audit replay、replay consistency checks、no-double-settlement verification）。

当前 Phase 6 / 实验路线：

- Phase 6 Lean track：已完成 direct proof、decomposition/child proof/merge、Phase 8 adapter ready path 和 replay/evidence guard；后续只在发现回归或 Phase 9 replay/audit 需要小范围补证时返回。
- Phase 8 track：已完成通用实验基础设施；后续只在发现回归或 Phase 9 replay/audit 需要读取实验 artifacts/events 时做小范围扩展。
- factorization 第一版只承诺 prime / semiprime fixture 端到端闭环；不宣称 early success、sibling pruning 或完整 composite cofactor recursive resolution。
- Lean 插件必须接入本地真实 Lean checker，并用无 AI 介入的确定性拆分算法生成 proof subtask；旧 `Lean stub proof` 路线已废弃。
- 实验 runner 必须通过 `PluginExperimentAdapter` 兼容 factorization 和真实 Lean proof plugin；Lean adapter 默认使用真实 checker evidence，且仍保留结构化 blocked / pending regression path，不能用 stub 替代。

`feat-008` / Phase 7 Experimental AI API Executor 已完成：

- 可通过统一 `ExecutionRequest` / `ExecutionSubmission` 调用真实模型 API。
- 持久化 provider、model、prompt package、raw output、parsed output 或 parse failure、usage、latency、cost 和 error provenance。
- 标准 executor config 只保存 `api_key_env`；真实 smoke 可从被 gitignore 的 `local/ai_api_smoke.local.json` 读取本地 key 并注入当前进程环境变量。API key 不写入 event、artifact、SQLite、日志或 config digest；baseline 测试不要求联网。
- replay 不重新调用 AI API，缺失历史输出 artifact 时必须失败。

- `SimulationProfile`、`SimulationWrapper`、`ExperimentRunner` 和 `MetricsCollector`。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
- work、critical path、retry/wasted work、shadow benefit 等指标报告。

当前已进入 `feat-010` / Phase 9 replay and audit。

当前尚未完成 feat-010 replay / audit、真实 executor 网络、生产级 AI API 平台或真实链上结算。`feat-007` 真实 Lean proof plugin、`feat-008` 实验级 AI API executor 和 `feat-009` 实验基础设施已完成。structured report stub 已从 Phase 6 开发计划剔除。

当前仍需注意：

- 自然语言任务的验证不是“证明文本绝对正确”，而是通过结构化 schema、证据引用、覆盖率和审计 replay 降低风险。
- Lean V1 已按 2026-06-28 范围变更调整为真实 checker 驱动的形式化证明插件；不得再按 stub/synthetic-only 路线实现。
- Experiment 4 必须使用真实 Lean proof plugin / Lean adapter；没有真实 checker logs 和 `EnvironmentRef` 时只能标记 blocked / pending，不能用 `lean_stub` 替代。当前默认 Lean adapter 已有真实 checker ready path。
- factorization 和 Lean 形式化证明插件是当前插件实验对象，不应硬编码进协议核心；历史 structured report fixture 名称不应重新扩大为待开发插件目标。
- 当前实现默认使用 `conda` 环境 `tokenshare`；如果运行时选择变化，需要同步更新 README、harness 和设计资料。
