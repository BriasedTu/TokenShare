# TokenShare

TokenShare 是一个早期本地研究原型，用来验证一种协议内核：把大型任务递归拆分、分派、验证、合并、结算，并能从 append-only 事件日志重放全过程。

当前 V1 目标是用 Python、SQLite、JSON、JSONL 和本地文件系统做一个可复现实验实现，跑通 factorization、Lean stub proof 和 structured report stub 三类 proof-of-concept 实验。

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
- 生产 AI API 集成。
- 完整 Lean theorem proving。

## V1 Scope

V1 是本地可复现实验用的协议内核，范围包括：

- 协议基础对象：`TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`ArtifactRef`、`LedgerEvent`、`ProtocolConfig`。
- 状态机：`TaskUnit`、`Lease`、`Attempt`、`ContributionRecord`。
- 本地存储：SQLite、JSON、JSONL、本地 artifact 文件。
- append-only `EventLedger`，用于状态重放和审计。
- 固定版本的 `PluginRegistry` 和 `ExecutorRegistry`。
- `ArtifactStore` 写入、读取和内容哈希校验。
- 调度、租约、执行尝试、验证、正式输出选择、展开、合并、恢复和结算。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
- 指标报告、状态重放、审计重放和 sandbox 结算。

具体字段、SQLite 表结构和插件 API 会在实现阶段逐步细化。README 只记录已经稳定的项目边界和启动方式。

## Proof-of-Concept Experiments

V1 计划包含三类实验插件：

- **factorization**：验证普通可拆分计算任务。当前规划的插件就是主 TDD 第 14.1 节的整数分解插件；第一版采用候选因子搜索空间分区，先覆盖插件主导拆分、bounded range search、结果验证、all-required merge、失败恢复和结算。`nontrivial_factor_found(d, q)` 后的递归展开必须继续由同一个插件生成协议可见子图；提前完成、`one_success` 和子树剪枝是否第一版实现，需要在 Phase 6 字段规格中显式确认。
- **Lean stub proof**：验证 proof-like 工作流。它只使用 fixture 或 stub 模拟 Lean 检查，用来覆盖 proof patch、error log、子目标展开和合并流程。
- **structured report stub**：验证大型自然语言任务。它使用 fixture 模拟 AI section 输出、证据引用、缺失 section、伪造引用和合并报告，用来覆盖结构化拆分、弱验证、覆盖率检查和 `MergePlan` 合并流程。

这些实验是协议扩展性的验证对象，不应被硬编码进协议核心。

## Architecture Principles

TokenShare 的核心边界是三层：

- **协议框架**：维护任务生命周期、不变量、状态机、调度、验证编排、正式输出选择、事件日志、恢复和结算。
- **任务插件**：声明任务域 schema、拆分策略、验证规则、合并规则和能力要求。
- **执行器**：实际处理已经确定的 `TaskUnit`，返回统一 `ExecutionSubmission`。

关键原则：

- 协议核心不理解 factorization、Lean 或 structured report 的领域逻辑。
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
- `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`：Phase 6 factorization 插件第一版拆分算法和主 TDD 对齐讨论记录；已确认采用候选因子搜索空间分区，AI / executor 只处理 bounded range search，递归展开由同一插件基于 canonical output 继续生成协议可见子图。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：论文、技术报告、本地 TeX/OCR 与模块借鉴映射。
- `Doc/TechnicalDocument/tokenshare-paper-tex/`：已本地化的论文/技术报告 TeX 或 OCR 文本。
- `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 机制整合记录；只用于追溯取舍理由，不覆盖主 TDD。
- `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：协议内核讨论稿。
- `Doc/agent-navigation.md`：agent 导航、模块路由和外部参考资料落库规则。

## Development Workflow

开发时以 `feature_list.json` 为状态源，一次只处理一个未完成 feature。

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

当前日期状态：2026-06-27。

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
- 当前完整启动验证通过：`.\init.ps1` 在 `tokenshare` conda 环境中收集 211 个测试并全部通过。
- Phase 6 factorization 插件第一版拆分主轴已确认并记录：由插件把候选因子搜索空间拆成 `factor_search_range` 子任务，AI / executor 只在给定范围内搜索，插件验证 `range_result` 并通过 all-required `MergePlan` 合并。重读主 TDD 后已补充纠偏：这是主 TDD 第 14.1 节同一个整数分解插件，递归 continuation 不是新机制或新插件，而是 canonical output 驱动的插件递归展开闭环。

当前进行中：

- `feat-007`：Phase 6 - Experiments, Fault Simulation, and Metrics。

Phase 6 的下一步是先收束实验、故障模拟和指标字段规格 / TDD 计划，然后按单一 feature 范围实现：

- factorization、Lean stub proof 和 structured report stub 三类 proof-of-concept 实验。
- `SimulationProfile`、`SimulationWrapper`、`ExperimentRunner` 和 `MetricsCollector`。
- offline、slow、executor_error、invalid_output、late_submission 五类故障模拟。
- factorization 字段规格需要显式决定：第一版是否实现主 TDD 14.1 的 early success / pruning，还是先把 all-required merge 作为有意的阶段切片记录。

当前尚未实现 Phase 6 实验、Phase 7 replay / audit、真实 executor 网络、生产 AI API 或真实链上结算。

当前仍需注意：

- 自然语言任务的验证不是“证明文本绝对正确”，而是通过结构化 schema、证据引用、覆盖率和审计 replay 降低风险。
- Lean V1 只是 stub，不应扩大到真实 theorem proving。
- factorization、Lean stub 和 structured report stub 是插件实验对象，不应硬编码进协议核心。
- 当前实现默认使用 `conda` 环境 `tokenshare`；如果运行时选择变化，需要同步更新 README、harness 和设计资料。
