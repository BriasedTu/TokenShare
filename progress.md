# 会话进度日志（Session Progress Log）

## 当前状态（Current State）

**最后更新：** 2026-06-23
**当前 Feature：** feat-005 - Phase 4 - Verification, Canonical Output, and Expansion（下一轮待实现）
**仓库阶段：** startup / local research prototype

TokenShare 当前已有设计文档、仓库元数据、Python package layout、Phase 1 协议基础对象、本地存储实现、Phase 2 最小任务图/状态机/调度/租约/事件投影代码，以及 Phase 3 插件与执行器契约代码。启动期 harness 已经建立并通过 `conda` 环境 `tokenshare` 验证；V1 技术栈已收束为 Python 3.12+、SQLite、JSON、JSONL 和本地文件系统。`feat-003` 已完成并通过 2026-06-23 Phase 2 stabilization：重复 active lease、防提前过期、防超时 heartbeat 复活和 FIFO 排序均已有回归测试。P01-P22 候选机制已在 2026-06-23 整合进主 TDD，后续实现以主 TDD 为准。`feat-004` 已完成：registry freeze、统一 request/submission、mock AI executor、deterministic executor、Phase 3 event、`Attempt.Running -> Submitted` 和 SQLite index-only projection 均已有测试；2026-06-23 追加修复了 submission attempt/lease/fencing token 绑定校验和 scheduler Phase 3 `Available` 状态契约回归。当前 active feature 是 `feat-005`；Phase 4 验证、canonical output 和 expansion 代码尚未开始。

2026-06-23 已按当前代码对 Phase 1 / Phase 2 code map 做全面校准：以 `src/tokenshare/` 和 `tests/` 的 AST/路径扫描结果为准，补齐 `RootTaskRegistrationRequest` / `RootTaskRegistrationResult`、`ArtifactStore.save_json()`、`LeaseClaim`、`LeaseExpiryDecision`、`SchedulingFlowResult`、`LeaseHeartbeatFlowResult`、`LeaseExpiryFlowResult`、ProtocolEngine active lease ledger 投影 helper、当前源码/测试实物清单，并确认两个 code map 中引用的 `src/`、`tests/`、`Doc/` 路径全部真实存在。

2026-06-23 已新增 Phase 3 code map：`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`。该 map 记录 `src/tokenshare/plugins/contracts.py`、`src/tokenshare/plugins/registry.py`、`src/tokenshare/executors/contracts.py`、`src/tokenshare/executors/registry.py`、`src/tokenshare/executors/mock_ai.py`、`src/tokenshare/executors/deterministic.py`、`src/tokenshare/protocol_engine.py`、`src/tokenshare/storage/events.py` 和 `src/tokenshare/storage/sqlite_index.py` 与 Phase 3 字段草案、事件和测试的对应关系。

## 项目理解（Project Understanding）

TokenShare V1 不是整数分解应用、不是 Lean theorem prover，也不是区块链产品。它要先做一个本地协议内核，证明以下协议闭环可以工作：

1. 注册根任务。
2. 创建并维护递归任务图。
3. 通过 lease 和 attempt 调度 ready task unit。
4. 持久化 artifact 和 append-only event。
5. 通过插件规则验证 submission。
6. 绑定唯一 canonical output bundle。
7. 对节点执行 expand 或 complete。
8. 将子节点结果向父节点合并。
9. 记录贡献并执行 sandbox settlement。
10. 从 JSONL event replay 和 audit，不重新运行非确定性执行。

V1 的三类实验是 factorization、Lean stub proof 和 structured report stub。它们是用于验证协议可扩展性的插件，不是协议核心逻辑；其中 structured report stub 专门覆盖大型自然语言任务的结构化拆分、弱验证和合并。

## 状态（Status）

### 已完成（What's Done）

- [x] 阅读 `README.md`。
- [x] 阅读 TDD：`Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`。
- [x] 阅读协议讨论稿：`Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`。
- [x] 确认 V1 是 Python/SQLite/JSONL 本地可复现实验原型。
- [x] 创建启动期 harness 文件。
- [x] 成功运行启动验证。
- [x] 成功运行 harness 结构验证，得分 100/100。
- [x] 记录语言要求：`AGENTS.md` 和 `progress.md` 以后优先用中文维护，技术标识和命令可保留英文。
- [x] 按当前稳定边界重写中文 `README.md`，作为项目入口文档。
- [x] 对照 Airflow、Argo Workflows、Temporal、Ray、BOINC、SQLite 和 Python 官方资料，完成 V1 技术栈取舍。
- [x] 更新 TDD：新增“V1 技术栈决策”，并将“第一版实现语言”从开放问题收束为 Python 3.12+ / SQLite / JSONL 本地轻栈。
- [x] 创建 `reference_repos/`，浅克隆 / sparse checkout Temporal Python SDK、Luigi、cwltool、Prefect、Dagster，用于 package layout 参考。
- [x] 更新启动验证：`compileall` 排除 `reference_repos/`，避免外部参考源码影响 TokenShare 基线。
- [x] 确定并创建初始 package layout：`src/tokenshare/{core,storage,plugins,executors,replay,experiments}` 和镜像 `tests/` 骨架。
- [x] 更新启动验证：存在 `tests/` 时在 `PYTHONPATH=src` 下运行 `pytest tests`，并加入 package layout smoke test。
- [x] 新增 agent 导航文档：`Doc/agent-navigation.md`，记录事实源优先级、模块路由和外部参考资料使用规则。
- [x] 将 agent 导航从 `Doc/TechnicalDocument/` 移到 `Doc/` 根目录，保持技术设计目录只放设计资料。
- [x] 新增 Phase 1 最小对象字段规格：`Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`，用表格区分协议对象名、未来 Python 类名、对象字段名、JSON key、事件类型和 SQLite 索引表名。
- [x] 更新 `Doc/agent-navigation.md`，把对象字段规格文档加入事实源和“需要设计对象字段”的导航索引。
- [x] 使用 TDD 实现 Phase 1 代码：`ArtifactRef`、`ProtocolConfig`、`TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`RootTaskRegistrar`、`ArtifactStore`、`LedgerEvent`、JSONL `EventLedger` 和 `SQLiteMaterializedIndex`。
- [x] 新增 Phase 1 测试：协议对象 snapshot、artifact save/read/hash、event append/read/hash chain、SQLite rebuild、root task registration 三事件顺序。
- [x] 新增代码映射文档：`Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md`，记录规格章节、代码文件和测试文件之间的对应关系。
- [x] 创建 `conda` 环境 `tokenshare`，当前验证到 Python 3.12.13、SQLite 3.51.2、pytest 9.0.3。
- [x] 新增 `requirements.txt`，作为可由 `pip install -r requirements.txt` 安装的 Python 依赖清单。
- [x] 更新 `init.ps1` 和 `init.sh`，默认使用 `conda run -n tokenshare python`，并支持 `TOKENSHARE_CONDA_ENV` 覆盖。
- [x] 对照 LangGraph、AutoGen、CrewAI、LlamaIndex、DSPy Assertions、Decomposed Prompting、Tree of Thoughts 和 Graph of Verification，更新主 TDD 中的语义拆分、AI 文本验证、`MergePlan` 和结构化报告 stub 设计。
- [x] 同步更新 `AGENTS.md`、`README.md`、`Doc/agent-navigation.md`、`feature_list.json` 和历史讨论稿，避免“三类 PoC”和后续对象边界在文档间冲突。
- [x] 新增外部资料落库工作流：后续凡联网资料影响设计、代码、测试或项目文档，论文/报告必须本地化并更新论文映射，开源项目必须拉取到 `reference_repos/` 并更新索引，普通在线文档必须记录来源、访问日期、本地摘要和影响范围。
- [x] 新增工具与编码工作流：后续常规仓库读取和搜索默认使用 PowerShell，中文/JSON/Markdown 读取显式使用 UTF-8，常规检索不要使用 `rg`，避免编码乱码和重复误判浪费上下文。
- [x] 修正 TDD、agent 导航和历史讨论稿中的旧表述：论文归档路径改为 `Doc/TechnicalDocument/tokenshare-paper-tex/` 与论文映射索引，package layout 明确 `structured_report_stub/` 是 Phase 6 目标插件目录，Phase 1 目录/实现状态改为已创建基础实现，早期任务口径改为 structured report。
- [x] 将错误的依赖说明文件替换为标准 `requirements.txt`，当前可通过 `pip install -r requirements.txt` 安装 Python 依赖。
- [x] 审核并修正 `TaskState` 边界：确认 `Leased` 和 `Verifying` 不应属于 `TaskUnit` 节点生命周期，已从 `TaskState` 中移除，并用 `Processing` 表达“至少存在有效 attempt”的粗粒度节点状态；新增回归测试防止 `Lease` / `Attempt` 细节状态再次混入。
- [x] 修正 `EventLedger.append()` 幂等边界：相同 `idempotency_key` 只有在事件类型、对象、任务和 canonical payload 一致时返回旧事件；冲突重复写入会抛出 `ValueError`，避免 replay 审计时吞掉冲突。
- [x] 收窄 `tokenshare.core` 包入口：不再从 `tokenshare.core.__init__` 重新导出 Phase 1 临时协调器 `RootTaskRegistrar`，避免 protocol core 包入口和 storage orchestration 形成循环依赖。
- [x] 新增 Phase 2 协调边界备忘录，并在 `Doc/agent-navigation.md` 和 Phase 1 code map 中建立索引，提醒后续 agent 不要让 `RootTaskRegistrar` 继续长成 TaskGraph / Scheduler / LeaseManager 总入口。
- [x] 新增 Phase 2 专用规格文档：`Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`，记录 `TaskGraph`、`TaskUnitStateChange`、`Lease`、`Attempt`、`SchedulingDecision`、`RecoveryAction`、状态机、事件顺序、SQLite 投影和自然语言 artifact 边界。
- [x] 使用 TDD 实现 Phase 2 最小协议内核：`Lease` / `Attempt` 对象和状态枚举、`TaskGraph` ready 判断和图不变量、`TaskUnit` / `Lease` / `Attempt` 状态机、FIFO `Scheduler`、`LeaseManager` claim/heartbeat/expiry recovery、Phase 2 event type、SQLite `leases` / `attempts` / `recovery_actions` 投影，以及顶层 `ProtocolEngine` 调度、heartbeat 和 lease expiry 事件流。
- [x] 新增 Phase 2 代码映射文档：`Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`，记录 Phase 2 规格、实现文件和测试文件的对应关系。
- [x] 完成 Phase 2 stabilization：`Scheduler` 的 FIFO 按 `TaskUnit.created_at` / `unit_id` 排序；`LeaseManager.expire()` 拒绝未到 `expires_at` 的提前过期，`heartbeat()` 拒绝 `now >= expires_at` 的续命；`ProtocolEngine.schedule_ready_unit()` 调度前从 `EventLedger` 投影 active leases，避免调用者漏传 `active_leases_by_unit_id` 时重复 claim。
- [x] 完成 code map 同步审计：以当前代码为准更新 `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md` 和 `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`，补齐已存在但此前未映射的对象、flow result、helper 边界和测试文件清单；未修改协议代码。
- [x] 同步更新 `README.md` 当前状态、仓库地图和下一步，把 README 从旧 Phase 2 进行中口径改为当前 Phase 3 准备口径；同步更新 `Doc/agent-navigation.md` 日期。
- [x] 记录 Phase 3 开工前边界债务：`RootTaskRegistrar` 只能冻结为 Phase 1 legacy helper 或迁出到顶层 application service；scheduler 中硬编码的 client availability 字符串应在 Phase 3 `ExecutorRegistry` / client contract 中收束。
- [x] 新增并扩展 `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 已按机制主题融入同一结构，形成 109 个唯一规范定义、21 条跨模块不变量和 27 项设计决策；该文件现在保留为研究来源和整合记录，不覆盖主 TDD。
- [x] 完成 P08-P12 冲突审查：P08 进入可重复实验/故障 profile；P09 进入 executor actor 状态、动态 runtime 和 lineage 边界；P10/P11 仅作为未来控制面复制参考；P12 仅保留为未来离散弱验证扩展。同步修正论文映射中把 PBFT `3f+1`/`2f+1` 直接指向 AI verifier committee 的旧表述。
- [x] 完成 P13-P22 融合与冲突审查：P13-P15 仅支持未来弱验证 model family；P16-P18 只提供插件内求解/工具审计边界；P19-P21 固化验证环境、Lean stub 和 benchmark 可复现性边界；P22 提供可解释的确定性直接分派流程，不把完整 Contract Net 协商带入 V1。
- [x] 对 P01-P22 候选规范进行全文主题化重排：论文编号只用于溯源，正文按协议生命周期组织；不变量、反面设计、27 项取舍记录和主 TDD 修订清单均改为机制/阶段分组，稳定 requirement/invariant/decision ID 与技术判断保持不变。
- [x] 将 P01-P22 推荐取舍整合进主 TDD：明确 expected output/resolution、requirements/hints、capability snapshot、`EnvironmentRef`、action/observation provenance、verification/selection 分离、deterministic allocation、state replay 不重执行、merge 普通生命周期、最终结算和可复现实验边界。
- [x] 同步候选规范、agent 导航、论文映射和 README，使候选规范从“待裁决清单”降级为“整合记录”。
- [x] 记录 Phase 3 字段与机制讨论成果：新增 `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` 草案，确认以 `ExecutionRequest` / `ExecutionSubmission` 执行闭环为主骨架；request/submission 本体保存为 artifact，event payload 只保存 ref、digest 和索引摘要；收到 submission 后推进 `Attempt.Running -> Submitted`，但不进入验证、canonical、merge 或 settlement。
- [x] 使用 TDD 实现 Phase 3 插件与执行器契约：新增 `PluginDescriptor` / `OutputContract`、`PluginRegistry` / `RegistrySnapshot`、`ExecutorDescriptor` / `ExecutorRegistry`、`ExecutionRequest`、`ExecutionSubmission`、`EnvironmentRef`、`PromptPackage`、`MockAIExecutor` 和 `DeterministicLocalExecutor`；新增 `REGISTRY_SNAPSHOT_RECORDED`、`EXECUTION_REQUEST_RECORDED`、`EXECUTION_SUBMISSION_RECORDED`；`ProtocolEngine` 可记录 registry snapshot、request 和 submission artifact event；`Attempt` 允许 `Running -> Submitted`，但仍拒绝 `Submitted -> Verifying` 等 Phase 4 状态；SQLite 新增 `registry_snapshots`、`execution_requests`、`execution_submissions`、`executor_statuses` 四张 index-only projection。
- [x] 新增 Phase 3 代码映射文档：`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`，记录 Phase 3 规格、实现文件和测试文件的对应关系。
- [x] 修复 Phase 3 边界审查发现的问题：`record_execution_submission()` 现在要求 submission 与当前 running attempt、lease 和 fencing token 匹配才推进 `Attempt.Running -> Submitted`，否则只记录 audit submission event；`Scheduler` 只接受 Phase 3 序列化状态 `Available` 和 Phase 2 legacy `active`，不再接受旧 `ready`/`online`/`idle` 字符串。

### 进行中（What's In Progress）

- [ ] Phase 4 - Verification, Canonical Output, and Expansion。
  - 细节：实现通用数据检查、插件领域验证编排、`VerificationReport`、first_verified_bundle 选择、`DecompositionProposal`、`ExpansionDecision`、`MergePlan` 和原子图更新。
  - 当前状态：`feat-005` 已在 `feature_list.json` 中激活为唯一 in-progress feature；Phase 4 代码尚未开始。
  - Phase 4 必须从 Phase 3 artifact-backed submission 出发：读取 `ExecutionSubmission` artifact 和 output refs，写验证报告 artifact/event，再按 selection policy 绑定唯一 canonical output bundle。
  - Phase 4 不应实现 merge、contribution、settlement、真实网络 executor 或生产 AI API。

### 下一步（What's Next）

1. 下一轮先按 `AGENTS.md` 启动流程重新运行 `.\init.ps1`，确认当前 27 个测试仍通过。
2. 阅读主 TDD 第 4.3、8、9、10、12、21 节；同时阅读 `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`，确认 Phase 4 从 Phase 3 request/submission artifact 边界继续。
3. 使用 TDD 开始 `feat-005`；优先写验证报告、唯一 canonical binding 和无效 expansion 不改图的红灯测试。
4. 不要把 factorization、Lean stub 或 structured report stub 的领域规则硬编码进协议核心；插件规则只能通过插件 contract 和未来验证编排进入。

## 阻塞与风险（Blockers / Risks）

- [ ] Phase 2 必须继续保持 JSONL event ledger 为权威事实源；状态机推进不能只写内存或 SQLite。
- [ ] Phase 2 不实现验证/合并，但 `TaskGraph` 和状态机要为后续 `DecompositionProposal`、`ExpansionDecision`、`MergePlan`、`VerificationReport` 预留事件和状态边界。
- [ ] SQLite 只能作为可重建索引和查询视图，不能变成隐藏权威状态源；后续 replay 测试仍需覆盖“删除 SQLite 后可重建”。
- [ ] Lean V1 是 stub，不要不小心扩大到真实 theorem proving。
- [ ] 调度、lease 创建、attempt 创建和 unit 状态推进会产生多条 JSONL 事件；后续实现需要用 `correlation_id` 和恢复逻辑处理局部写入后的中间态。
- [ ] Phase 4 必须继续保持 JSONL event ledger + artifact 为权威事实源；SQLite 只能重建索引，不能成为验证或 canonical 的隐藏权威。
- [ ] Phase 4 只能从已记录的 `ExecutionSubmission` / output artifact refs 生成验证和 canonical 结果；不得重新调用 executor 或 mock AI 来补历史输出。
- [ ] Phase 4 需要保证同一 `TaskUnit` 只能绑定一个 canonical output bundle；重复绑定必须失败并留下可审计证据。
- [ ] Phase 4 的 expansion 必须先记录结构化 `DecompositionProposal` 和 `ExpansionDecision`，无效 expansion 不得部分写入 `TaskGraph`。
- [ ] Phase 3 已实现显式 `ExecutorRegistry` / `ExecutorStatus` contract，但 Phase 2 `Scheduler` 仍为兼容旧 `ClientRecord.status` 保留字符串匹配；如果 Phase 4 需要更深调度整合，必须作为单独边界变更记录。

## 已做决策（Decisions Made）

- **启动验证保持轻量：** 默认使用 `conda` 环境 `tokenshare` 运行 Python stdlib 检查和 `compileall`；存在 `tests/` 目录时运行 `pytest tests`。
- **feature list 映射 TDD 阶段：** feat-002 到 feat-008 对应 Phase 1 到 Phase 7。
- **协议边界写入 harness：** protocol core 不能硬编码 factorization、Lean 或 structured report 行为。
- **监督语言要求：** `AGENTS.md` 和 `progress.md` 优先用中文维护，方便用户随时监督；技术名词、对象名、命令和文件名可保留英文。
- **V1 技术栈决策：** 采用 Python 3.12+、SQLite、JSON、JSONL、本地文件系统和 pytest；Airflow、Argo Workflows、Temporal、Ray、BOINC 等只作为设计参照和后续迁移候选，不进入 V1 runtime。
- **Package layout 决策：** 采用 `src/` layout；协议核心、存储、插件、执行器、重放、实验分别位于 `tokenshare.core`、`tokenshare.storage`、`tokenshare.plugins`、`tokenshare.executors`、`tokenshare.replay`、`tokenshare.experiments`。
- **字段规格决策：** Phase 1 采用“稳定对象字段 + 版本化 JSON payload + SQLite 可重建索引”的最小规格；协议对象名、字段名、事件类型和 SQLite 表名在 `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` 中分层记录。
- **外部资料落库决策：** 不能再只把联网研究结果写成在线链接；凡进入项目决策的论文、技术报告、开源项目或工程文档，都必须有本地可复查材料和对应索引记录。
- **工具与编码决策：** 本仓库常规文件读取、枚举和检索使用 PowerShell；读取中文 Markdown、JSON、脚本和代码时显式指定 UTF-8；常规检索不使用 `rg`。
- **TaskState 边界决策：** `TaskUnit.state` 只表达节点生命周期；租约有效性属于 `Lease`，提交、验证和正式输出选择进度属于 `Attempt`，不能在 `TaskState` 中重复建模。
- **EventLedger 幂等决策：** `idempotency_key` 是“同一请求重试”的去重键，不是冲突覆盖键；同 key 写入必须校验事件类型、对象、任务和 canonical payload，一旦不同即失败。
- **Phase 2 协调边界决策：** `RootTaskRegistrar` 仍可作为 Phase 1 兼容入口，但后续 `TaskGraph`、`Scheduler`、`LeaseManager` 和 attempt 状态推进应进入独立编排层或 `ProtocolEngine`，不要继续塞进 protocol core。
- **Phase 2 对象规格决策：** `TaskGraph` 是可重建视图；`Lease` 过期会让关联 `Attempt` 进入 `Superseded` 而不是 `Failed`；`SchedulingDecision` 在 Phase 2 嵌入 lease/attempt 事件，不单独建 event type。
- **自然语言输出边界决策：** AI raw text、parsed output 和 candidate output bundle 都必须通过 `ArtifactRef` 进入系统；Phase 2 event payload 只保存结构化摘要和 refs，不嵌入长自然语言正文。
- **Phase 2 开工默认决策：** 当前阶段仍然只做协议内核，不做插件、executor/处理端、AI 调用、submission 验证、canonical binding、expansion、merge 或 settlement；暂不新增 `max_parallel_attempts_per_unit`，用 `allow_shadow_execution=false` 约束同一 unit 最多一个 active lease；heartbeat 每次成功都写 `LEASE_STATE_CHANGED Active -> Active` 事件。
- **Phase 2 实现边界决策：** `tokenshare.core` 新增纯协议对象、状态机、`TaskGraph`、`Scheduler` 和 `LeaseManager`；顶层 `tokenshare.protocol_engine.ProtocolEngine` 负责把调度和 lease expiry 决策写入 `EventLedger`；`RootTaskRegistrar` 未扩展，SQLite 仍只是从 JSONL events 重建的查询投影。
- **P01-P22 机制整合决策：** 主 TDD 已吸收候选机制的 V1 取舍；候选规范保留为研究来源。V1 接受 expected output/resolution、requirements/hints、capability snapshot、`EnvironmentRef`、action/observation provenance、verification/selection 分离、deterministic direct assignment、state replay 不重执行、merge 普通任务生命周期、最终一次性结算和可复现实验字段；完整 Contract Net、PBFT/Raft 控制面复制、Dawid-Skene/GLAD/MACE/CROWDLAB、真实 LeanDojo/mathlib/miniF2F 和通用 ToT/ReAct 搜索引擎留到后续版本。

## 本轮修改文件（Files Modified This Session）

- `AGENTS.md` - agent 启动流程、范围、规则、完成标准；已加入外部资料落库和 PowerShell/UTF-8 工具要求。
- `feature_list.json` - TokenShare 阶段路线图和 feature 状态。
- `progress.md` - 当前理解、状态、风险、下一步；已改为中文并记录语言要求。
- `session-handoff.md` - 下一轮 restart 摘要。
- `requirements.txt` - 可由 `pip install -r requirements.txt` 安装的 Python 依赖清单；当前包含测试依赖 `pytest==9.0.3`。
- `init.sh` - Bash 基线验证，默认使用 `conda` 环境 `tokenshare`。
- `init.ps1` - Windows PowerShell 基线验证，默认使用 `conda` 环境 `tokenshare`。
- `README.md` - 中文项目入口，记录项目定义、V1 范围、非目标、启动命令、仓库地图和当前状态。
- `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` - 新增 V1 技术栈决策、依赖更新和开放问题更新。
- `.gitignore` - 忽略 `reference_repos/` 下的第三方源码克隆，仅保留本地索引说明文件。
- `reference_repos/README.md` - 记录参考仓库来源、commit、拉取范围、观察重点和新增项目落库要求。
- `src/tokenshare/` - 初始 Python package 骨架。
- `tests/` - 镜像测试目录骨架和 package layout smoke test。
- `Doc/agent-navigation.md` - agent 导航、PowerShell/UTF-8 工具规则、模块路由和外部参考资料落库/使用规则。
- `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md` - 历史讨论稿；已修正 V1 非目标中的旧任务口径。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md` - 论文、技术报告、本地 TeX/OCR 和模块借鉴映射；已加入新增论文落库规则。
- `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md` - Phase 1 最小对象字段规格、事件 envelope 和 SQLite 可重建索引边界。
- `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md` - Phase 1 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` - Phase 2 协调边界备忘录，记录已修复的边界问题和后续编排层迁移触发条件。
- `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md` - Phase 2 最小字段、状态机、事件顺序、SQLite 投影和自然语言 artifact 边界规格。
- `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` - Phase 2 代码、规格章节和测试的对应关系。
- `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` - Phase 3 插件/执行器字段规格草案，记录 request/submission artifact 化、event 摘要、attempt submission 状态推进和 AI artifact 边界。
- `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` - Phase 3 代码、规格章节和测试的对应关系。
- `src/tokenshare/core/models.py` - Phase 1 协议对象和稳定 JSON snapshot。
- `src/tokenshare/core/__init__.py` - protocol core 包入口；不再重新导出 `RootTaskRegistrar` 等存储协调器。
- `src/tokenshare/core/registration.py` - root task registration 协调器。
- `src/tokenshare/core/task_graph.py` - Phase 2 `TaskGraph` 纯视图、ready 判断和图不变量。
- `src/tokenshare/core/state_machines.py` - Phase 2 `TaskUnit`、`Lease` 和 `Attempt` 状态机；已开放 Phase 3 `Attempt.Running -> Submitted`。
- `src/tokenshare/core/scheduling.py` - Phase 2 `Scheduler` 和 `SchedulingDecision`。
- `src/tokenshare/core/leases.py` - Phase 2 `LeaseManager` claim、heartbeat 和 expiry recovery 规则。
- `src/tokenshare/protocol_engine.py` - Phase 2 最小 application service，并新增 Phase 3 registry/request/submission artifact-backed event flow。
- `src/tokenshare/plugins/contracts.py` - Phase 3 `OutputContract`、`PluginDescriptor` 和 descriptor digest helper。
- `src/tokenshare/plugins/registry.py` - Phase 3 `PluginRegistry` 和 `RegistrySnapshot`。
- `src/tokenshare/executors/contracts.py` - Phase 3 `ExecutorStatus`、`EnvironmentRef`、`ExecutorDescriptor`、`PromptPackage`、`ExecutionRequest` 和 `ExecutionSubmission`。
- `src/tokenshare/executors/registry.py` - Phase 3 `ExecutorRegistry`、available matching、no-match reason 和 descriptor artifact freeze。
- `src/tokenshare/executors/mock_ai.py` - Phase 3 deterministic mock AI executor 和 raw/parsed/parse-failure artifact path。
- `src/tokenshare/executors/deterministic.py` - Phase 3 deterministic local executor boundary。
- `src/tokenshare/storage/artifacts.py` - 本地 artifact 保存、读取、hash 校验和 manifest。
- `src/tokenshare/storage/events.py` - JSONL `EventLedger`、`LedgerEvent`、事件类型、幂等键和 hash chain；已包含 Phase 3 event type。
- `src/tokenshare/storage/sqlite_index.py` - 从 JSONL events 重建 SQLite 查询索引；已包含 Phase 3 四张 index-only projection。
- `tests/__init__.py` - 让测试 helper 可稳定导入。
- `tests/phase2_fixtures.py` - Phase 2 测试夹具。
- `tests/phase3_fixtures.py` - Phase 3 测试夹具。
- `tests/core/test_phase1_models.py` - Phase 1 协议对象测试。
- `tests/core/test_task_graph.py` - Phase 2 `TaskGraph` 测试。
- `tests/core/test_state_machines.py` - Phase 2 状态机测试。
- `tests/core/test_scheduler.py` - Phase 2 scheduler 测试。
- `tests/core/test_lease_manager.py` - Phase 2 lease manager 测试。
- `tests/plugins/test_plugin_registry.py` - Phase 3 plugin registry freeze 测试。
- `tests/executors/test_executor_registry.py` - Phase 3 executor status contract 测试。
- `tests/executors/test_mock_ai_executor.py` - Phase 3 mock AI artifact path 测试。
- `tests/executors/test_deterministic_executor.py` - Phase 3 deterministic executor boundary 测试。
- `tests/storage/test_artifact_store.py` - artifact store 测试。
- `tests/storage/test_event_ledger.py` - event ledger 测试。
- `tests/storage/test_sqlite_index.py` - SQLite rebuild 测试。
- `tests/storage/test_phase2_event_projection.py` - Phase 2 SQLite event projection 测试。
- `tests/storage/test_phase3_event_projection.py` - Phase 3 SQLite index-only projection 测试。
- `tests/test_phase1_root_registration.py` - root task registration 集成测试。
- `tests/test_phase2_scheduling_flow.py` - Phase 2 调度和 lease expiry event-backed flow 集成测试。
- `tests/test_phase3_execution_flow.py` - Phase 3 registry/request/submission event-backed flow 集成测试。

## 完成证据（Evidence of Completion）

- [x] 启动验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed。
- [x] Harness 验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] README 更新后验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并因暂无 `tests/` 目录跳过 `pytest`。
- [x] 技术栈决策更新后验证：`.\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并因暂无 `tests/` 目录跳过 `pytest`。
- [x] 参考源码加入后验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`reference_repos/` 已从 `compileall` 排除，输出包含 `python-json-sqlite-ok`、`harness-files-ok`。
- [x] Package layout 骨架验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，并运行 `tests\test_package_layout.py`，结果 `1 passed`。
- [x] Agent 导航文档验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] Agent 导航迁移验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] Agent 导航迁移后 Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] Phase 1 字段规格文档验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；`tests\test_package_layout.py` 结果 `1 passed`。
- [x] 字段规格索引更新后 Harness 结构验证：`node C:\Users\32133\.codex\skills\harness-creator\scripts\validate-harness.mjs --target E:\TokenEcnomic\TokenShare` returned Overall 100/100。
- [x] Phase 1 TDD 红灯验证：`PYTHONPATH=src python -m pytest tests/core/test_phase1_models.py tests/storage/test_artifact_store.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/test_phase1_root_registration.py -q` 失败，原因是 `tokenshare.core.models`、`tokenshare.storage.artifacts`、`tokenshare.storage.events` 等 Phase 1 模块尚不存在。
- [x] Phase 1 定向绿灯验证：`PYTHONPATH=src python -m pytest tests/core/test_phase1_models.py tests/storage/test_artifact_store.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/test_phase1_root_registration.py -q` passed；结果 `6 passed in 0.41s`。
- [x] Phase 1 完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.21s`。
- [x] `conda` 环境验证：`conda run -n tokenshare python -c "import sys, sqlite3; print(sys.version); print(sqlite3.sqlite_version)"` passed；输出包含 Python `3.12.13` 和 SQLite `3.51.2`。
- [x] PowerShell conda 启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `Using conda environment: tokenshare`、`harness-files-ok`，pytest collected 7 items，结果 `7 passed in 0.17s`。
- [x] Bash conda 启动验证：`bash ./init.sh` passed；输出包含 `Using conda environment: tokenshare`、`harness-files-ok`，pytest collected 7 items，结果 `7 passed in 0.21s`。
- [x] 语义拆分/验证/合并文档更新后验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.17s`。
- [x] 文档一致性搜索：已检查旧的“双实验/双插件”、旧 Phase 1 状态、旧日期和仅列 factorization/Lean 的范围表述；除历史解释性上下文外无冲突命中。
- [x] Bash 入口复验：`bash ./init.sh` passed；pytest collected 7 items，结果 `7 passed in 0.18s`。
- [x] 外部资料落库工作流更新后 PowerShell 验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.17s`。
- [x] 外部资料落库工作流更新后 Bash 验证：`bash ./init.sh` passed；pytest collected 7 items，结果 `7 passed in 0.18s`。
- [x] PowerShell/UTF-8 工具工作流更新后验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.20s`。
- [x] 旧表述修正文档二次审核：已搜索旧论文归档路径、旧“双插件/双实验”表述、旧 Phase 1 layout 表述、旧任务口径和 Phase 2 规格关键词；未命中需要继续修正的旧表述，且未新增 Phase 2 规格文档。
- [x] 旧表述修正后 PowerShell 验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.17s`。
- [x] `requirements.txt` 验证：`conda run -n tokenshare python -m pip install -r requirements.txt` passed；输出显示 `pytest==9.0.3` 已满足。
- [x] `requirements.txt` 修正后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 7 items，结果 `7 passed in 0.22s`。
- [x] `TaskState` 边界回归测试：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py -q` passed；结果 `2 passed in 0.04s`。
- [x] `TaskState` 相关定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py tests\test_phase1_root_registration.py tests\storage\test_sqlite_index.py -q` passed；结果 `4 passed in 0.17s`。
- [x] `TaskState` 边界修正后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 8 items，结果 `8 passed in 0.19s`。
- [x] `EventLedger` 幂等冲突红灯验证（一）：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_event_ledger.py -q` failed；暴露 `tokenshare.core.__init__` 急切导出 `RootTaskRegistrar` 导致 `core` / `storage` 循环导入。
- [x] `EventLedger` 幂等冲突红灯验证（二）：修正包入口后再次运行同一命令 failed as expected；新增冲突测试失败信息为 `DID NOT RAISE <class 'ValueError'>`。
- [x] `EventLedger` 幂等冲突绿灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_event_ledger.py -q` passed；结果 `3 passed in 0.06s`。
- [x] `core` 模型和 package layout 定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase1_models.py tests\test_package_layout.py -q` passed；结果 `3 passed in 0.06s`。
- [x] 协调边界修正后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 9 items，结果 `9 passed in 0.20s`。
- [x] Phase 2 规格文档新增后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 9 items，结果 `9 passed`。
- [x] Phase 2 开工默认决策写入规格后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 9 items，结果 `9 passed`。
- [x] Phase 2 TDD 红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_task_graph.py tests\core\test_state_machines.py tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\storage\test_phase2_event_projection.py tests\test_phase2_scheduling_flow.py -q` failed as expected；失败原因是 `tokenshare.core.task_graph`、`tokenshare.core.scheduling`、`tokenshare.core.leases`、`Lease` / `Attempt` 对象和 `tokenshare.protocol_engine` 尚不存在。
- [x] Phase 2 heartbeat 红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase2_scheduling_flow.py -q` failed as expected；失败原因是 `ProtocolEngine.record_lease_heartbeat` 尚不存在。
- [x] Phase 2 定向绿灯验证：同一定向命令 passed；结果 `9 passed in 0.22s`。
- [x] Phase 2 完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；pytest collected 18 items，结果 `18 passed`。
- [x] Phase 2 stabilization 红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\core\test_lease_manager.py tests\test_phase2_scheduling_flow.py -q` failed as expected；新增测试暴露 FIFO 未按 `created_at`、未到期 lease 可提前 expire、超时 lease 可 heartbeat、同一 ready graph 可重复生成 active lease。
- [x] Phase 2 stabilization 定向绿灯验证：同一定向命令 passed；结果 `7 passed in 0.18s`。
- [x] Phase 2 stabilization 完整启动验证：`.\init.ps1` passed；pytest collected 21 items，结果 `21 passed in 0.38s`。
- [x] README 状态同步后二次审核：使用 `Select-String` 检查旧状态关键词；`README.md` 无旧状态命中，其他命中均为完成状态、历史 evidence 或当前真实的 Phase 3 尚未开始表述。
- [x] Phase 3 开工前边界债务记录验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 18 items，结果 `18 passed in 0.42s`。
- [x] P01-P07 候选规范结构审核：独立部分定义 85 条要求（73 条 MUST/SHOULD/MAY 和 12 条跨模块不变量），对照部分含 15 个唯一决策标题，无 `TODO`、`TBD`、`FIXME` 或待填写占位符；主 TDD 冲突原文已二次核对。
- [x] P01-P07 候选规范新增后完整启动验证：`.\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 18 items，结果 `18 passed in 0.43s`。
- [x] P01-P12 融合结构审核：磁盘文件 SHA-256 为 `CDC6E15B4E862021EEEFE65EF696672078DA5772FF08BEFA5B0122209F58C5BE`；P08-P12 必需标记全部存在；共 92 个唯一规范定义、16 条不变量、20 个唯一决策标题，无重复定义或 `TODO`/`TBD`/`FIXME`/待填写占位符。
- [x] P01-P12 融合后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 18 items，结果 `18 passed in 0.35s`。
- [x] P01-P22 融合结构审核：P13-P22 均已进入相关机制章节、覆盖矩阵、跨模块不变量或裁决链；共 109 个唯一规范定义（81 MUST、20 SHOULD、8 MAY）、21 条唯一不变量和 27 个唯一决策标题，无重复定义或 `TODO`/`TBD`/`FIXME`/待填写占位符。
- [x] P01-P22 融合后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 18 items，结果 `18 passed in 0.36s`。
- [x] P01-P22 全文主题化重排验证：结构审核确认 109 个唯一规范定义、21 条唯一不变量、27 个唯一裁决及 27 个取舍顺序引用全部闭合，修订批次痕迹为 0；`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed，pytest 结果 `18 passed in 0.57s`。
- [x] P01-P22 主 TDD 整合验证：`.\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 18 items，结果 `18 passed in 0.33s`。
- [x] Phase 3 字段规格草案验证：新增 `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` 并更新导航、进度、handoff 和 feature source documents；`feature_list.json` JSON 解析通过，新草案 `TODO|TBD|FIXME|待填写` 扫描无命中，`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed，pytest collected 21 items，结果 `21 passed in 0.39s`。
- [x] Phase 3 开工抉择收束验证：将 `AllocationDecision` 内联、descriptor artifact 化、executor status 最小枚举、SQLite index-only projection 四项决策写入 Phase 3 草案；索引检查确认 `Doc/agent-navigation.md`、`feature_list.json`、`progress.md`、`session-handoff.md` 均指向该草案；未决旧表述扫描无命中；边界扫描确认 Phase 4/5 词汇只出现在非目标或禁止项；`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed，pytest collected 21 items，结果 `21 passed in 0.41s`。
- [x] Code map 校准前基线验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 21 items，结果 `21 passed in 0.49s`。
- [x] Code map 路径/符号审计：使用 PowerShell + Python AST/Markdown 扫描当前 `src/tokenshare/` 与 `tests/`，并检查两个 code map 中引用的 `src/`、`tests/`、`Doc/` 路径；结果全部存在，缺失映射已补入两个 code map。
- [x] Code map 校准和状态记录更新后完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 21 items，结果 `21 passed in 0.35s`。
- [x] Phase 3 TDD 红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` failed as expected；失败原因是 `tokenshare.executors.contracts`、`tokenshare.executors.registry` 等 Phase 3 模块尚不存在。
- [x] Phase 3 deterministic executor 红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\executors\test_deterministic_executor.py -q` failed as expected；失败原因是 `tokenshare.executors.deterministic` 尚不存在。
- [x] Phase 3 定向绿灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_state_machines.py tests\plugins\test_plugin_registry.py tests\executors\test_executor_registry.py tests\executors\test_mock_ai_executor.py tests\executors\test_deterministic_executor.py tests\test_phase3_execution_flow.py tests\storage\test_phase3_event_projection.py -q` passed；结果 `9 passed in 0.53s`。
- [x] Phase 3 完整启动验证：`.\init.ps1` passed；输出包含 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 28 items，结果 `28 passed`。
- [x] Phase 3 边界修复红灯验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_scheduler.py tests\test_phase3_execution_flow.py -q` failed as expected；新增测试暴露 `ready` 旧状态仍可调度，以及 `record_execution_submission()` 尚未接收 lease/fencing token 绑定校验。
- [x] Phase 3 边界修复定向绿灯验证：同一定向命令 passed；结果 `6 passed in 0.18s`。
- [x] Phase 3 边界修复完整启动验证：`powershell -ExecutionPolicy Bypass -File E:\TokenEcnomic\TokenShare\init.ps1` passed；pytest collected 30 items，结果 `30 passed in 0.67s`。

## 下次会话提示（Notes for Next Session）

Feat-001、feat-002、feat-003 和 feat-004 已完成；2026-06-23 Phase 3 边界修复后完整启动验证通过 `.\init.ps1`，pytest collected 30 items，结果 `30 passed in 0.67s`。Phase 1 代码与规格对应关系见 `Doc/TechnicalDocument/2026-06-06-phase-1-code-map.md`；Phase 2 最小字段、状态和事件规格见 `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`；Phase 2 代码映射见 `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`；Phase 3 字段草案见 `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`；Phase 3 代码映射见 `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`。P01-P22 候选规范位于 `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`，现在只作为主 TDD 整合记录和论文取舍来源。下一步只实现 `feat-005`：Verification, Canonical Output, and Expansion；不要提前实现 merge、contribution、settlement、真实 executor 网络或生产 AI API。后续常规仓库读取/搜索按 `Doc/agent-navigation.md` 第 4 节使用 PowerShell 和 UTF-8；如需联网查资料，必须先按第 6 节完成本地落库和索引同步。
