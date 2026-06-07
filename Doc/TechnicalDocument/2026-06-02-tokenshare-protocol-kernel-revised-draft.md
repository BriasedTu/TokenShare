# TokenShare 协议框架设计稿（第五次讨论修订版）

日期：2026-06-02

状态：讨论中。本文档合并了原始设计稿与后续讨论中已经确认的修改意见，用于继续
梳理协议结构。本文档不是最终实现规格。

## 1. 文档目的

TokenShare 的第一阶段目标不是先实现整数分解程序，也不是先实现 Lean 证明系统。
第一阶段要实现的是一个完整、可扩展、可运行的协议框架：

```text
大型任务
  -> 按照用户预先选定的规则递归拆分
  -> 将可执行任务分派给客户端
  -> 接收和验证执行结果
  -> 根据经过验证的中间结果继续拆分或向上合并
  -> 记录贡献并结算
  -> 从事件日志恢复和审计全过程
```

整数分解和 Lean 证明是最早接入同一框架的两类初始实验插件。后续实现导向 TDD 又补充了
structured report stub，用于覆盖大型自然语言任务的结构化拆分、弱验证和合并。它们
用于验证协议框架是否能够承载不同类型的任务，而不是协议内核本身。

## 2. 总体结构

TokenShare 分为三个部分：

```text
TokenShare 协议框架
  ├─ 固定机制
  └─ 可配置策略

任务插件

执行器
```

### 2.1 协议框架

协议框架负责所有任务共同遵守的生命周期和不变量，包括：

- 任务注册。
- 任务图维护。
- 渐进式递归拆分。
- 任务就绪判断。
- 客户端认领、租约、超时、释放和重试。
- 执行尝试记录。
- 结果提交。
- 验证编排。
- 正式结果选择。
- 自底向上的递归合并。
- 贡献记录和幂等结算。
- append-only 事件日志。
- 状态恢复、审计重放和基础实验指标。

协议框架内部可以包含可配置策略，例如租约有效期、最大重试次数、调度顺序、
是否启动影子执行以及奖励参数。策略属于协议框架内部，不与协议框架并列。

### 2.2 任务插件

任务插件负责领域相关知识，包括：

- 根任务输入格式。
- 子任务输入和输出格式。
- 用户可以选择的拆分逻辑。
- 叶节点判定。
- 根据经过验证的中间结果生成下一层任务。
- 任务域验证规则。
- 任务域合并规则。
- 客户端能力要求。
- 任务特定实验指标。

插件不能直接修改协议状态机，不能绕过验证和正式结果选择，也不能直接发放奖励。

### 2.3 执行器

执行器负责实际处理客户端收到的任务单元。执行方式可以是：

- AI。
- 本地模型。
- 确定性程序。
- 未来的远程服务。
- 未来的人类 worker。

同一个任务插件不绑定唯一执行器。例如，整数分解任务既可以交给普通程序，也
可以交给 AI。

## 3. 核心角色

第一版至少包含以下角色：

- **任务发起者**：提交根任务，选择任务插件、拆分逻辑和相关参数。
- **协议协调器**：自动推进协议状态，维护任务图，调用插件，调度任务并记录事件。
- **客户端**：认领任务，通过执行器自动处理任务，并提交结果。
- **验证器**：按照插件提供的规则验证提交结果。第一版可以由协调器本地调用。
- **账本**：记录协议事件、贡献和结算状态。第一版使用本地 append-only 日志。

任务发起者在注册任务后不需要持续参与调度。协议运行过程应当自动完成。

## 4. 协议框架主循环

当前已经确认的主循环如下：

```text
任务发起者注册根任务，并选择插件和拆分逻辑
  -> 协议协调器创建根 TaskUnit
  -> 协议协调器逐步选择可处理的 TaskUnit
  -> 客户端获得租约并通过执行器处理 TaskUnit
  -> 客户端提交结果
  -> 协议协调器调用插件验证结果
  -> 协议协调器按照正式输出选择策略原子绑定输出束
  -> 插件返回 complete 或 expand
      -> complete：结果进入向上合并流程
      -> expand：插件根据正式输出生成下一层任务描述
  -> 协议协调器原子更新任务图
  -> 新的就绪任务进入调度
  -> 所需子结果齐备后，协议协调器调用插件合并
  -> 递归向上产生根任务结果
  -> 协议记录贡献并结算
```

## 5. 任务图

### 5.1 递归拆分树与执行依赖 DAG

“任务树”和“任务 DAG”不是互斥方案。它们描述任务结构的两个不同维度：

- **递归拆分关系**回答“这个节点由谁拆分产生”。一个节点由一个上级节点展开
  而来，因此这部分形成拆分树。
- **执行依赖关系**回答“这个节点开始之前必须等待哪些正式结果”。一个节点可以
  依赖多个前置节点，因此这部分形成 DAG。

例如：

```text
Root
├─ TaskUnit A ----\
└─ TaskUnit B -----+--> TaskUnit C
```

`A` 和 `B` 由 `Root` 拆分产生，属于拆分树。`C` 需要同时读取 `A` 和 `B` 的正式
结果，因此存在两条执行依赖边。

协议框架使用一张运行时任务图统一保存这些节点，但明确区分两类边：

```text
decomposition edge：记录递归来源、向上合并路径和中间贡献归因
dependency edge：记录执行前置条件，用于推导节点何时 Ready
```

合并不需要额外引入第三张图。父节点的合并由拆分关系、子节点的正式结果和插件
提供的合并规则共同决定。每次实际合并使用了哪些输入结果，仍然必须写入事件日志。

协议协调器负责维护运行时任务图，并拒绝任何会使执行依赖形成环的更新。

### 5.2 任务图渐进式展开

用户注册任务时确定的是**拆分规则**，而不是完整的任务图实例。具体节点不一定
能够在开始时全部生成，因为后续拆分可能依赖中间结果。

因此，任务图不在注册时一次性完整生成，而是在运行过程中渐进式展开。

渐进式展开仍然是全自动的。它不表示客户端可以自行决定拆分方案。只有协议
协调器可以修改任务图。

### 5.3 结果驱动拆分

下一层任务可能依赖当前节点的处理结果，因此第一版需要支持结果驱动拆分：

```text
客户端处理当前 TaskUnit
  -> 提交中间结果
  -> 插件验证结果
  -> 协议协调器按照正式输出选择策略原子绑定输出束
  -> 协议协调器将正式输出交给插件中的既定拆分逻辑
  -> 插件生成下一层任务描述和依赖关系
  -> 协议协调器原子写入任务图
```

客户端只能提交结果，不能直接创建或分派新的任务。

### 5.4 任务就绪判断

任务图既表示控制关系，也表示数据依赖。下游任务不能仅仅因为上游任务“执行过”
或“提交过”就进入调度。

第一版采用以下原则：

```text
一个 TaskUnit 可以进入 Ready
  当且仅当
  它需要的全部前置结果
  都已经通过验证并被协议接受为正式结果
```

一个下游节点可以依赖多个前置节点的正式结果。具体数据结构仍待讨论。

### 5.5 命名输出与正式版本

一个 `TaskUnit` 不必只有一个无名称的结果。插件可以声明该节点需要提供多个用途
不同的**命名输出**，下游节点则依赖某个具体名称对应的正式结果。

例如：

```text
TaskUnit A
  -> output "proof_patch"
  -> output "error_log"

TaskUnit B depends on A."proof_patch"
```

同一个命名输出也可能由多个 attempt 提交候选版本。候选版本通过验证后，仍需由协议按照正式结果选择策略绑定为正式版本。对每个命名输出而言，同一时刻最多只有一个正式版本。一个集合或文件列表也可以作为某个命名输出的内容，不需要拆成多个协议对象。

这一规则不要求现在引入新的独立对象。第一版先明确以下语义：

- 插件声明节点需要提供哪些命名输出，以及哪些输出属于节点完成条件。
- `dependency edge` 指向前置节点的具体命名输出，而不是笼统地指向整个节点。
- 只有已经通过验证并被协议接受的正式输出，才能解锁下游依赖或参与向上合并。
- 第一版将同一 attempt 的多个命名输出视为一个输出束，整组接受或整组拒绝。

### 5.6 正式输出选择

结果验证与正式输出选择是两个不同步骤：

- **任务插件**判断候选输出是否符合任务域规则。
- **协议框架**决定经过验证的候选输出是否成为正式版本。

第一版使用 `first_verified_bundle` 策略：

```text
attempt 提交候选输出束
  -> 插件验证
  -> 协议框架以原子 compare-and-set 方式尝试绑定正式输出束
      -> 绑定成功：attempt 进入 Canonical
      -> 已有正式输出束：attempt 进入 Superseded
```

正式输出束必须来自同一个 attempt。第一版不允许从多个 attempt 中分别挑选不同
命名输出后重新拼接，因为这些输出之间可能存在插件才能理解的一致性约束。缺少
必需命名输出的 attempt 不能成为 `Canonical`。正式输出束一旦绑定，迟到提交只能
保留为审计证据，不能覆盖它。

未来可以增加 `quorum_consensus` 策略：协议框架收集多个经过验证的 attempt，再
根据插件提供的等价性或比较规则选择正式输出束。这类似 BOINC 的 canonical result
机制。但第一版的整数分解和 Lean 实验具有确定性验证器，不需要先引入共识选择。

正式输出选择属于协议框架内部的可配置策略。插件提供验证规则，但不能直接绑定或
覆盖正式输出。如果未来某些输出确实需要独立选择，优先将它们建模为不同
`TaskUnit`；是否增加显式输出分组留待后续扩展。

### 5.7 与现有系统的关系

这一结构综合了几类已有系统的经验：

- Dryad 一类数据流系统强调预先组织计算步骤和数据依赖 DAG。
- Airflow Dynamic Task Mapping 说明固定工作流可以根据上游结果在运行时实例化
  具体任务。
- CIEL 进一步支持运行时动态任务图，适合表达结果驱动的递归与迭代。
- Cilk 区分递归展开形成的 `spawn tree` 与实际执行约束形成的 precedence DAG。
- Apache Beam 的 tagged outputs、Nextflow 的 named outputs 和 Argo Workflows 的
  named artifacts 说明下游依赖具体命名输出是成熟的工作流表达方式。
- MapReduce 的原子 rename、Dryad 的 execution version 选择和 BOINC 的 canonical
  result 机制说明：存在重复执行时，系统应当明确选择正式版本，而不是让迟到结果
  覆盖已经接受的结果。

TokenShare 采用相近的分层理解，但增加一条更严格的协议约束：客户端不能直接
修改任务图。客户端只提交结果，协议协调器在验证并正式接受输出后调用用户预先
选择的插件拆分逻辑，并将图更新写入事件日志。

来源：

- Dryad：<https://www.microsoft.com/en-us/research/wp-content/uploads/2007/03/eurosys07.pdf>
- Airflow Dynamic Task Mapping：
  <https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html>
- CIEL：
  <https://www.usenix.org/conference/nsdi11/ciel-universal-execution-engine-distributed-data-flow-computing>
- Cilk：<https://publications.csail.mit.edu/lcs/pubs/pdf/MIT-LCS-TM-548.pdf>
- Apache Beam：
  <https://beam.apache.org/documentation/transforms/java/elementwise/pardo/>
- Nextflow：
  <https://docs.seqera.io/nextflow/process>
- Argo Workflows：
  <https://argo-workflows.readthedocs.io/en/stable/walk-through/artifacts>
- MapReduce：
  <https://research.google/pubs/mapreduce-simplified-data-processing-on-large-clusters/>
- BOINC：
  <https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf>

## 6. 任务、执行尝试和结果

本节先明确不同层次中需要区分的对象。对象名称和字段只表示当前设计范畴，
不代表最终代码结构、数据库表或 API 已经定稿。

### 6.1 协议框架对象

协议框架对象用于表达所有任务共同遵守的生命周期、DAG 关系、执行记录、验证、
贡献归因、结算和事件重放。

| 对象 | 作用 | 字段范畴 |
|---|---|---|
| `TaskSpec` | 根任务注册信息。固定用户选择的插件、拆分策略和运行参数。 | 任务标识、任务描述、插件标识与版本、拆分策略与参数、根输入引用、预算、截止时间、协议配置、元数据 |
| `TaskUnit` | DAG 中等待处理、拆分或合并的工作节点。一个节点可以声明多个用途不同的命名输出。 | 节点标识、根任务标识、任务类型、生命周期状态、输入引用、命名输出引用、结算权重、预算、截止时间、能力要求、元数据 |
| `TaskRelation` | 表达节点之间的结构关系。区分递归拆分关系与执行依赖关系。依赖边指向具体命名输出；合并输入由拆分关系、正式结果和插件合并规则共同确定。 | 关系标识、来源节点、目标节点、`decomposition` 或 `dependency`、所需命名输出引用、创建原因、元数据 |
| `ClientRecord` | 协议识别和调度客户端所需的基础信息。 | 客户端标识、能力声明、可用状态、执行器类型、协议相关统计、元数据 |
| `Lease` | 某个客户端对某个任务单元的临时执行权。用于超时、释放、迟到提交隔离和重试。 | 租约标识、任务单元标识、客户端标识、attempt 标识、租约状态、签发时间、过期时间、心跳、fencing 信息、幂等键 |
| `Attempt` | 客户端对某个任务单元的一次具体执行。保留成功、失败、迟到和被替代的执行历史。 | attempt 标识、任务单元标识、租约标识、客户端标识、状态、开始与提交时间、执行环境摘要、候选命名输出引用、日志引用、失败原因 |
| `ArtifactRef` | 对任务输入、任务结果、中间结果、文件或日志的统一引用。它不是任务，而是任务处理过程中产生或使用的数据。 | 结果标识、类型、存储位置、内容哈希、schema 标识、来源 attempt、来源追踪、创建时间、元数据 |
| `VerificationResult` | 记录插件验证器对某次提交结果的判断。 | 验证记录标识、任务单元标识、attempt 标识、artifact 引用、判定、理由、证据、验证器信息、时间 |
| `ExpansionDecision` | 在结果验证通过后，记录插件判断该节点已经完成，还是应根据该结果继续展开。 | 判定标识、任务单元标识、输入 artifact 引用集合、`complete` 或 `expand`、生成的子任务描述、依赖描述、合并要求、插件版本、时间 |
| `ContributionRecord` | 记录某次有效贡献与后续子树、合并和结算之间的关系。支持中间贡献延迟奖励。 | 贡献标识、贡献类型、任务单元标识、attempt 标识、artifact 引用、贡献者、依赖的后续成功条件、结算状态、元数据 |
| `SettlementRecord` | 记录奖励或惩罚，并保证重复请求和日志重放不会重复结算。 | 结算标识、贡献标识、任务单元标识、attempt 标识、客户端标识、奖励、惩罚、理由、结算状态、幂等键 |
| `LedgerEvent` | append-only 事件日志中的一条记录。用于状态恢复、审计和实验指标计算。 | 事件标识、事件类型、时间、关联对象标识、关联链路标识、幂等键、事件载荷、前序哈希、事件哈希 |

协议框架内部还需要一个可配置对象，但它不必成为独立业务实体：

| 对象 | 作用 | 字段范畴 |
|---|---|---|
| `ProtocolConfig` | 保存第一版运行策略。策略属于协议框架内部，不与协议框架并列。 | 心跳间隔、租约有效期、重试上限与退避参数、是否允许影子执行、调度模式、正式输出选择策略、冗余规则、基础奖励参数、事件存储配置 |

### 6.2 任务插件对象

任务插件对象用于表达特定任务领域的规则。协议框架只读取这些对象并调用插件，
不理解整数分解、Lean 证明或未来任务的内部知识。

| 对象 | 作用 | 字段范畴 |
|---|---|---|
| `PluginDescriptor` | 标识任务插件及其版本，并声明插件提供哪些能力。 | 插件标识、版本、任务类型、支持的拆分策略、schema 标识、支持的执行器类别、能力要求、兼容信息 |
| `DecompositionStrategy` | 表达用户注册任务时选择的既定拆分逻辑。协议运行期间不能由客户端临时修改。 | 策略标识、版本、参数 schema、策略参数、适用任务类型、是否支持结果驱动拆分 |
| `TaskSchema` | 描述插件接受的根输入、子任务输入、命名输出和结果格式。 | schema 标识、版本、输入格式、命名输出及其格式、完成条件、中间结果格式、兼容规则 |
| `VerificationRule` | 描述插件如何判断任务结果有效。由协议框架统一编排调用。 | 规则标识、版本、适用任务类型、输入 schema、结果 schema、验证模式、环境要求 |
| `MergeRule` | 描述插件如何将多个已验证正式输出合并为父节点的命名输出。 | 规则标识、版本、适用任务类型、输入结果要求、合并条件、输出 schema、验证要求 |

这些对象不一定都需要独立类。第一版可以将其中一部分表示为插件代码、配置或
描述信息。此处先明确它们属于任务插件，而不是协议框架。

### 6.3 执行器对象

执行器对象用于表达“客户端如何处理已经确定的 TaskUnit”。执行器不决定任务图，
也不绕过协议验证。

| 对象 | 作用 | 字段范畴 |
|---|---|---|
| `ExecutionRequest` | 协议框架交给执行器的统一输入。 | attempt 标识、租约与 fencing 上下文、任务单元摘要、输入引用、命名输出要求、能力要求、资源与时间限制、执行环境要求、插件提供的执行说明 |
| `ExecutionSubmission` | 执行器向协议框架返回的统一提交。 | attempt 标识、租约与 fencing 上下文、执行结果类别、候选命名输出引用或原始输出引用、日志引用、环境摘要、成本与用量摘要、结构化错误信息 |
| `PromptPackage` | AI 执行器使用的 prompt 封装，不是所有插件和执行器的公共协议对象。 | 系统指令、任务指令、上下文、输出格式、约束、工具提示、模型参数 |
| `RawModelOutput` | AI 执行器收到的原始模型响应。解析后转换为通用 `ExecutionSubmission`。 | 原始文本、模型标识、token 使用、延迟、结束原因、元数据 |
| `SimulationProfile` | 第一版本地实验使用的客户端行为模拟参数，不属于正式协议语义。 | profile 标识与版本、速度、可靠性、离线概率、无效结果概率、迟到概率、成本倍率、随机种子 |

### 6.4 暂不进入第一版核心的对象

原设计中的以下对象有合理用途，但不应阻塞第一版协议框架：

| 对象 | 暂缓原因 | 后续可能归属 |
|---|---|---|
| `PolicyDecision` | 风险等级、人工审批和外部资源授权不是第一版闭环的必要条件。 | 后续治理与安全扩展 |
| `NotificationRecord` | 本地第一版不需要模拟完整消息投递层。 | 后续分布式运行时 |
| `ReputationVector` | 向量化信誉适合后续调度、验证和激励实验，但第一版可以只保留基础统计。 | 后续协议策略扩展 |

对象内部结构将在层次确认后单独开章讨论。本节不进一步展开个别对象。

## 7. 状态机

协议框架使用四个相互关联但职责不同的状态机：

| 对象 | 状态机负责的问题 |
|---|---|
| `TaskUnit` | 一个任务节点当前处于依赖等待、可执行、处理、等待子树、合并还是完成阶段 |
| `Lease` | 某个客户端对任务节点的临时执行权当前是否仍然有效 |
| `Attempt` | 客户端的一次具体执行当前进行到哪一步，以及该结果是否成为正式结果 |
| `ContributionRecord` | 某项有效贡献是否仍在等待后续成功条件，是否具备结算资格 |

其他核心对象通常作为不可变事实记录，不单独维护状态机。例如，`VerificationResult`
记录一次验证结论，`ExpansionDecision` 记录一次拆分判定，`LedgerEvent` 记录一次
已经发生的事件。

### 7.1 TaskUnit 状态机

`TaskUnit` 状态机负责渐进式拆分和自底向上合并。它不记录某次具体执行的细节。
同一个 `TaskUnit` 可以同时关联多个 attempt。

```text
Created
  -> Blocked
  -> Ready
  -> Processing
      -> Completed                 [完成所需正式输出通过验证，插件返回 complete]
      -> WaitingForChildren        [驱动拆分的正式输出通过验证，插件返回 expand]

WaitingForChildren
  -> MergeReady                    [协议根据正式子输出调用固定插件规则，推导合并条件已经满足]
  -> Merging
      -> Completed                 [合并结果通过验证]
      -> MergeFailed               [合并失败，保留证据并进入恢复流程]
```

补充转移：

```text
Created  -> Ready                  [创建时前置条件已经满足]
Blocked  -> Ready                  [所需前置结果已经齐备]
Ready    -> Cancelled              [根任务或当前节点被取消]
Processing -> Ready                [现有 attempt 未产生正式结果，需要重试]
Processing -> Failed               [拆分计划无效，或重试已经达到终止条件]
MergeFailed -> Merging             [瞬时故障，使用相同输入进行有界重试]
MergeFailed -> Failed              [不可重试错误，或重试已经达到终止条件]
```

各状态语义：

| 状态 | 含义 |
|---|---|
| `Created` | 节点已经写入运行时任务图，但尚未完成首次就绪判断 |
| `Blocked` | 节点仍在等待前置正式结果或其他必要条件 |
| `Ready` | 节点可以进入调度并由客户端认领 |
| `Processing` | 至少有一次有效 attempt 正在执行、提交或验证；具体过程由 attempt 状态机记录 |
| `WaitingForChildren` | 当前节点已经根据正式中间结果展开，正在等待子树结果 |
| `MergeReady` | 协议根据正式子输出调用固定插件规则，推导合并条件已经满足，可以调用合并规则 |
| `Merging` | 协议协调器正在调用插件合并，并验证合并结果 |
| `Completed` | 当前节点已经拥有完成所需的正式输出，可以解锁下游依赖或参与上级合并 |
| `MergeFailed` | 合并失败，失败记录必须保留，恢复策略尚未完成 |
| `Failed` | 节点无法继续恢复 |
| `Cancelled` | 节点因根任务或协议操作而取消 |

当客户端提交的结果通过验证后，插件必须返回结构化判定：

```python
ExpansionDecision(
    action="complete" | "expand",
    result_refs=[...],
    child_specs=[...],
)
```

- `complete` 使当前节点进入 `Completed`。
- `expand` 使协议协调器原子创建下一层节点和关系，当前节点进入
  `WaitingForChildren`。
- 第一版不引入 `complete_and_expand`。

合并规则：

```text
多个经过验证的正式子输出
  -> 插件合并
  -> 父节点命名输出
  -> 插件验证
  -> 父节点 Completed
```

协议框架必须保证：

- 未验证结果不能触发 `Completed`，也不能进入父节点合并。
- `WaitingForChildren -> MergeReady` 只能由协议协调器根据正式子结果和固定插件规则
  推导。
- 合并过程、输入结果和输出结果必须写入事件日志。
- 合并失败不能静默丢弃。
- 合并失败不能自动推翻已经接受的子节点正式输出。
- 拆分逻辑在根任务注册时确定，客户端不能临时修改拆分规则。

### 7.2 Lease 状态机

`Lease` 表示客户端对某个 `TaskUnit` 的临时执行权。租约状态与任务节点状态分开，
因为同一个节点可能由于重试或影子执行产生多个租约。

```text
Active
  -> Active       [heartbeat 或续期，更新过期时间]
  -> Released     [客户端主动释放]
  -> Expired      [超过有效期]
  -> Revoked      [协议主动撤销]
```

`Released`、`Expired` 和 `Revoked` 是终止状态。租约终止后，原 fencing 信息失效。
携带旧租约的迟到提交可以保留为审计证据，但不能覆盖正式结果。

### 7.3 Attempt 状态机

`Attempt` 表示客户端依据某个租约执行某个 `TaskUnit` 的一次具体尝试。

```text
Created
  -> Running
      -> Submitted
          -> Verifying
              -> Verified
                  -> Canonical
                  -> Superseded
              -> Rejected
      -> Failed
      -> Superseded
```

各状态语义：

| 状态 | 含义 |
|---|---|
| `Created` | attempt 已创建但尚未开始执行 |
| `Running` | 执行器正在处理任务 |
| `Submitted` | 客户端已经提交结果和必要日志 |
| `Verifying` | 协议正在编排解析、本地校验和任务域验证 |
| `Verified` | 结果已经通过验证，但尚未完成正式结果选择 |
| `Canonical` | 该 attempt 提交的候选输出束被协议原子选为正式版本 |
| `Rejected` | 结果未通过校验、验证或租约有效性检查 |
| `Failed` | 执行器未能产生可提交结果 |
| `Superseded` | 其他 attempt 已经胜出，当前 attempt 不再有机会成为正式结果 |

解析、本地校验和任务域验证应记录事件，但第一版不必都建成长期状态。影子执行
与普通执行共享同一套 attempt 状态机，只是创建原因不同。

### 7.4 ContributionRecord 状态机

`ContributionRecord` 记录某次有效工作是否已经满足结算条件。结算凭证
`SettlementRecord` 是不可变记录，不单独维护状态机。

```text
Pending
  -> Eligible       [贡献已经满足后续成功条件]
  -> Invalidated    [贡献对应路径失败、取消或被判定无效]

Eligible
  -> Settled        [生成不可变 SettlementRecord]
  -> Invalidated    [根任务在最终结算前失败或取消]
```

规则：

- `complete` 节点的有效贡献可以在节点正式完成后进入 `Eligible`。
- `expand` 节点的中间贡献保持 `Pending`。只有其子树成功完成并向上合并后，才
  进入 `Eligible`。
- 根任务在最终结算前失败或取消时，所有尚未结算的贡献进入 `Invalidated`。
- 日志重放不得重复生成相同的 `SettlementRecord`。

### 7.5 四个状态机的联动

主流程可以概括为：

```text
TaskUnit Ready
  -> Lease Active
  -> Attempt Running
  -> Attempt Submitted
  -> Attempt Verifying
  -> Attempt Canonical
  -> ExpansionDecision
      -> TaskUnit Completed
      -> TaskUnit WaitingForChildren
          -> 子节点递归执行
          -> TaskUnit MergeReady
          -> TaskUnit Merging
          -> TaskUnit Completed
  -> ContributionRecord Pending | Eligible

Root TaskUnit Completed
  -> 满足条件的 ContributionRecord Eligible
  -> ContributionRecord Settled
```

故障不会删除历史记录：

```text
Lease Expired
  -> 当前 attempt 不能成为正式输出来源
  -> TaskUnit 根据恢复策略重新进入 Ready
  -> 创建新的 Lease 和 Attempt
```

状态转移必须写入 append-only 事件日志，从而支持状态恢复、审计和指标计算。

## 8. 故障恢复和幂等性

### 8.1 分层原则

故障恢复同时涉及协议固定机制和协议内部策略，但不应吞并插件职责：

- **协议固定机制**负责记录失败事实、隔离旧租约、保持正式输出唯一性、执行幂等
  状态转移，并在达到终止条件后停止恢复。
- **协议内部策略**决定哪些错误可重试、重试次数、退避间隔、租约时长、是否启动
  影子执行以及何时认定任务失败。
- **任务插件**负责判断任务域结果是否有效，以及插件生成的拆分计划和合并结果
  是否符合领域约束。
- **执行器**负责报告本次执行成功、失败、日志和环境摘要，不能自行决定重试。

### 8.2 第一版恢复触发条件

| 触发条件 | 协议动作 |
|---|---|
| 租约超过有效期，或客户端主动释放租约 | 终止租约；当前 attempt 不得再绑定正式输出；节点在仍有恢复预算时重新进入 `Ready` |
| 执行器失败或客户端报告不可完成 | 将当前 attempt 标记为 `Failed`；仅对策略允许重试的错误重新调度 |
| 提交结果未通过解析、本地校验或任务域验证 | 将 attempt 标记为 `Rejected`；保留证据；在无效尝试上限内重新调度 |
| attempt 运行时间超过影子执行阈值 | 在策略允许时创建独立租约和影子 attempt；原 attempt 不立即取消 |
| 输入 artifact 不可读取或内容哈希不匹配 | 优先从持久副本恢复；仅对可重建的确定性结果递归重执行来源任务 |
| 节点达到最大尝试次数、截止时间或预算上限 | 节点进入 `Failed`，失败沿依赖和拆分关系传播 |
| 根任务被取消 | 未完成节点进入 `Cancelled`；尚未结算的贡献失效 |

第一版需要在 `ProtocolConfig` 中提供以下参数范畴：

```text
heartbeat_interval
lease_ttl
max_attempts_per_unit
max_invalid_attempts_per_unit
retry_initial_delay
retry_backoff_coefficient
retry_max_delay
default_unit_deadline
shadow_after
max_parallel_attempts_per_unit
```

根任务的 `root_deadline` 和 `root_budget` 由任务发起者注册任务时写入 `TaskSpec`，
不是全局协议参数。子节点可以继承根任务限制，并由协议策略收紧局部限制。

`lease_ttl` 必须大于 `heartbeat_interval`。具体数值属于实验运行配置，不属于协议
语义定稿；第一版可以为本地模拟设置不同 profile 比较恢复行为。与无限重试相比，
TokenShare 默认采用有界重试，因为 AI 执行和外部资源调用具有不可忽略的成本。

### 8.3 Artifact 恢复边界

Ray 和 CIEL 可以通过 lineage reconstruction 或递归重执行恢复丢失对象，但它们
依赖任务具有确定性和幂等性。TokenShare 不能默认重新执行 AI 后得到完全相同的
结果，因此采用更严格的边界：

- 正式输出必须持久化，并使用内容哈希校验。
- 确定性执行器产生的可重建 artifact 可以根据来源追踪递归重执行。
- AI 或其他非确定性执行器产生的正式输出丢失时，不得在状态重放中静默重新生成；
  协议应报告不可恢复错误，或者由上层明确启动新的任务流程。

### 8.4 拆分和合并失败

结果驱动拆分和向上合并是协调器调用插件的编排操作，不是客户端可以自由修改的
步骤。第一版按以下方式恢复：

```text
固定输入 artifact 引用 + 固定插件版本 + 幂等操作标识
  -> 调用插件拆分或合并
  -> 校验插件返回结构
  -> 原子写入图变更或合并结果
```

- 插件调用超时、进程崩溃或其他瞬时故障：使用相同输入、相同插件版本和相同幂等
  操作标识进行有界重试。
- 拆分计划违反 schema、引用不存在的输出或产生依赖环：视为不可重试的插件错误，
  当前节点进入 `Failed`。
- 合并调用发生瞬时故障：进入 `MergeFailed`，然后使用相同正式输入重试。
- 合并结果无法通过验证：保留证据，并在重试预算耗尽后进入 `Failed`。
- 合并失败不能自动废除已经接受的子节点正式输出，也不能临时改写用户选择的拆分
  规则。需要领域修复时，插件应将修复表达为普通 `TaskUnit`；接口边界见第 11 章。

图更新必须是原子的。重放日志时，已经存在的图变更直接恢复，不重新调用 AI，也
不假设插件再次返回完全相同的拆分计划。

### 8.5 幂等性不变量

- 同一任务可以有多个 attempt，但正式输出束只能原子绑定一次。
- 影子执行使用独立租约和 attempt，不改变正式输出唯一性。
- 过期、释放或撤销租约的迟到提交不能覆盖当前正式输出。
- 图更新、重复认领请求、重复提交、恢复动作和日志重放必须保持幂等。
- 所有恢复动作必须写入事件日志，不能只修改内存状态。

## 9. 贡献记录和结算

第一版不实现真实区块链、钱包或代币支付，而是实现可审计的 sandbox 结算。

### 9.1 分层原则

- **协议固定机制**负责创建贡献记录、推导结算资格并保证恰好一次结算。
- **协议内部策略**根据根任务预算、贡献类型和结算权重计算 sandbox 奖励。
- **任务插件**可以在创建 `TaskUnit` 时提供领域相关的相对工作量建议，但不能直接
  发放奖励；协议必须限制权重范围并保证不超过根任务预算。
- **执行器**报告的 token、时间或资源消耗仅用于审计，不能直接决定奖励金额。

### 9.2 第一版贡献类型

| 贡献类型 | 第一版处理 |
|---|---|
| 完成贡献 | attempt 的输出束成为 `Canonical` 且插件返回 `complete` 后创建，可以进入 `Eligible` |
| 中间拆分贡献 | attempt 的输出束成为 `Canonical` 且插件返回 `expand` 后创建，但保持 `Pending`；只有后续子树成功合并后才进入 `Eligible` |
| 协议请求的冗余验证贡献 | 影子或冗余 attempt 虽未成为 `Canonical`，但其输出束经验证并与正式输出一致时，可以按配置创建较低权重的验证贡献 |
| 协调器本地验证 | 第一版不单独奖励，因为它不是客户端贡献 |
| 协调器本地合并 | 第一版不单独奖励；未来若合并本身成为可分派工作，应建模为普通 `TaskUnit` |
| 未被协议请求的重复或迟到提交 | 保留审计记录，但默认不奖励 |

### 9.3 Sandbox 奖励公式

第一版使用预算受限的基线公式，而不是根据客户端自报成本直接支付：

```text
provisional_reward(c) = base_rate[kind(c)] * weight(c)

scale = min(
  1,
  root_budget / sum(provisional_reward(c) for eligible contribution c)
)

reward(c) = provisional_reward(c) * scale
```

其中：

- `base_rate` 由 `ProtocolConfig` 按贡献类型配置。
- `root_budget` 由任务发起者注册任务时写入 `TaskSpec`。
- `weight(c)` 在创建 `TaskUnit` 或协议请求冗余验证时固定，不能由提交结果的客户端
  临时修改。
- 结算只针对 `Eligible` 贡献生成不可变 `SettlementRecord`。
- 如果根任务完成时没有 `Eligible` 贡献，则不生成结算记录。
- 为了在渐进式任务图完全展开后仍然保证预算上限，第一版在根任务完成后统一执行
  最终结算。

### 9.4 惩罚边界

第一版不引入质押、扣除既有余额或链上 slashing。结果无效、租约过期或提交迟到时：

- 不发放奖励。
- 保留失败和验证证据。
- 更新 `ClientRecord` 中的基础统计，供后续调度实验使用。

更复杂的信誉向量、质押和恶意行为治理属于后续协议策略扩展。它们不应阻塞第一版
协议闭环。

## 10. 事件日志和重放

第一版使用 JSONL append-only 事件日志。协议状态可以缓存，但日志是状态恢复和
审计的事实来源。

需要支持两种重放：

- **状态重放**：根据历史事件重建任务图、租约、attempt、正式结果和结算状态。
  状态恢复时不重新调用 AI。
- **审计重放**：使用已经保存的结果、插件版本和环境信息重新执行验证器，检查
  结果是否仍然成立。

结果驱动拆分必须记录足够信息，使状态恢复时可以直接恢复当时生成的子节点，
而不是重新调用插件并假设它再次生成完全相同的图。

例如：

```text
TASK_EXPANDED {
  unit_id,
  plugin_id,
  plugin_version,
  strategy_id,
  input_result_refs,
  generated_child_specs,
  generated_dependencies
}
```

### 10.1 最小事件集合

第一版事件日志记录可重放的领域事实，而不是把每次内部函数调用都写入日志：

| 事件类型 | 用途 |
|---|---|
| `TASK_REGISTERED` | 保存根任务、插件版本、拆分策略、预算和根输入 |
| `CLIENT_STATE_CHANGED` | 保存客户端注册、能力变化和可用状态变化 |
| `TASK_UNIT_STATE_CHANGED` | 保存节点生命周期变化，包括失败和取消 |
| `LEASE_STATE_CHANGED` | 保存租约签发、续期、释放、过期和撤销 |
| `ATTEMPT_STATE_CHANGED` | 保存 attempt 创建、运行、失败、拒绝、正式接受和被替代 |
| `SUBMISSION_RECORDED` | 保存候选命名输出、日志、环境摘要和来源 attempt |
| `VERIFICATION_RECORDED` | 保存验证结论、证据和验证器信息 |
| `CANONICAL_OUTPUTS_BOUND` | 原子记录某个节点正式输出束的唯一绑定 |
| `TASK_EXPANDED` | 保存拆分输入、插件版本、生成的子节点和依赖关系 |
| `MERGE_RECORDED` | 保存合并输入、插件版本、合并输出和验证结果 |
| `RECOVERY_ACTION_RECORDED` | 保存重试、重新调度、影子执行和终止恢复的原因 |
| `CONTRIBUTION_STATE_CHANGED` | 保存贡献创建、具备资格和失效 |
| `SETTLEMENT_RECORDED` | 保存恰好一次 sandbox 结算 |

每条事件至少需要事件标识、时间、关联对象、因果链路、幂等键和事件载荷。原始
heartbeat、调度器扫描过程和性能采样可以作为可选审计事件记录，但不属于恢复协议
状态所需的最小集合。

### 10.2 重放约束

- 日志是事实来源，内存状态和快照只是缓存。
- 状态重放只应用已经记录的事实，不重新调用 AI。
- `CANONICAL_OUTPUTS_BOUND`、`TASK_EXPANDED` 和 `SETTLEMENT_RECORDED` 必须使用幂等键
  防止重复应用。
- 如果日志尾部存在未完成的拆分或合并操作，协调器根据最后一个已提交事件恢复，
  再按照第 8.4 节执行有界重试。

## 11. 任务插件契约

任务插件负责领域语义，不负责推进协议状态。第一版需要先稳定插件与协议框架之间
的边界，而不是先设计第三方动态上传机制。

### 11.1 最小能力

每个任务插件至少需要提供以下能力：

| 能力 | 作用 | 不属于插件的部分 |
|---|---|---|
| 描述插件 | 声明插件标识、版本、支持的任务类型、拆分策略和 schema。 | 插件注册、版本固定和审计记录由协议框架负责。 |
| 校验任务输入 | 检查根输入和子任务输入是否符合领域约束。 | artifact 持久化、哈希校验和引用解析由协议框架负责。 |
| 声明命名输入与输出 | 为根输入、子任务输入、候选输出和中间结果提供版本化 schema。 | 正式输出束的唯一绑定由协议框架负责。 |
| 判断继续展开或完成 | 接收已经验证并正式绑定的输出束，返回 `complete` 或 `expand`。 | 插件不能直接创建节点或写入任务图。 |
| 生成展开计划 | 在返回 `expand` 时生成子任务描述、依赖关系和合并要求。 | 协议框架检查 schema、引用和 DAG 无环性，然后原子更新任务图。 |
| 验证候选输出 | 根据任务域规则判断一次候选提交是否有效。 | 协议框架负责编排验证过程，并按正式输出选择策略绑定结果。 |
| 判断何时可以合并并执行合并 | 根据已经通过验证的正式子输出判断父节点是否具备合并条件，并产生父节点输出。 | 协议框架负责触发调用、记录输入输出和处理有界重试。 |
| 声明客户端能力要求 | 声明执行某类任务必须具备的执行器类别、环境或资源。 | 客户端匹配、租约和调度排序由协议框架负责。 |

插件可以为某类执行器提供领域相关的执行说明和原始输出解析逻辑。例如，Lean
插件可以为 AI 执行器构造 proof-state prompt，并将模型文本解析为 proof patch；
它也可以为确定性程序提供结构化输入。插件不负责实际调用模型、启动进程或上报
租约心跳。

### 11.2 拆分逻辑的表示

第一版采用**插件内置策略标识、版本和参数**，不接受用户在注册任务时上传任意
脚本或表达式：

```text
任务发起者选择 strategy_id + strategy_version + parameters
  -> TaskSpec 固定这次运行使用的拆分逻辑
  -> 插件根据正式输出束执行该策略
  -> 返回 complete
     或 expand(child_specs, dependencies, merge_requirements)
  -> 协议协调器校验并原子写入图变更
```

这样仍然满足“任务发起者明确决定拆分逻辑”：用户选择的不是某次临时拆分结果，
而是一个版本固定、参数明确的既定策略。运行过程中，客户端只能提交结果，不能
替换策略或提出新方案。

CWL 支持表达式和 `scatter` 展开，但其规范也建议谨慎使用表达式。TokenShare 第一版
采取更保守的接口：复杂逻辑写在版本化插件代码中，协议日志记录策略标识、参数、
插件版本和实际生成的展开计划。未来如果确实需要用户自定义策略语言，应单独设计
受限 DSL，而不是直接执行任意代码。

### 11.3 输入、输出、依赖和合并

插件使用版本化 `TaskSchema` 声明 typed named I/O。`ArtifactRef` 保存具体数据引用；
它不替代 schema，也不需要额外引入新的 slot 对象。

```text
TaskSchema
  -> 声明任务输入
  -> 声明候选命名输出及其格式
  -> 声明哪些命名输出属于节点完成条件

ExpansionDecision(action="expand")
  -> 返回子任务描述
  -> 返回指向正式命名输出的 dependency edge
  -> 返回父节点后续合并所需的输入要求
```

执行依赖和合并条件需要区分：

- `dependency edge` 表示某个下游节点开始执行前必须具备哪些正式命名输出。
- `MergeRule` 表示父节点在收集哪些正式子输出后可以合并，以及如何产生父节点输出。
- 协议框架只把已经通过验证并正式绑定的输出交给插件。
- 插件可以根据领域逻辑返回 `wait` 或 `merge_ready`。例如整数分解可以在任一子节点
  找到因数时提前合并，也可以在所有搜索区间均无结果后合并为“未找到因数”。
- 第一版不为合并条件设计通用表达式语言。具体条件由版本化插件实现，实际合并
  使用的正式输入必须写入事件日志。

### 11.4 验证分工

验证是协议中的正式步骤，但任务域正确性仍由插件判断：

| 阶段 | 负责方 | 第一版职责 |
|---|---|---|
| 提交接入检查 | 协议框架 | 检查 attempt、租约、fencing、幂等键和提交完整性。 |
| 通用数据检查 | 协议框架 | 检查 artifact 是否可读、内容哈希和 schema 标识是否匹配。 |
| 领域验证 | 任务插件 | 检查候选输出是否满足整数分解、Lean 或其他任务域规则。 |
| 正式输出选择 | 协议框架 | 对通过验证的候选输出束执行 `first_verified_bundle` 等策略。 |
| 合并验证 | 协议框架编排，任务插件判断 | 使用正式子输出执行合并，并验证父节点输出。 |

因此，插件验证器不能自行绑定正式输出、修改图、触发奖励或决定重试。协议框架也
不能把“通过 JSON schema”误当作“领域结果正确”。

### 11.5 客户端能力要求

第一版将客户端能力声明分成两类：

- **硬要求**：不满足就不能认领任务。例如执行器类别、固定 Lean/mathlib 环境、
  CPU、GPU、内存、磁盘、网络或工具可用性。
- **可选偏好**：不影响正确性，只用于调度排序。例如更低延迟、更低成本或本地缓存。

插件声明能力要求，协议协调器将硬要求固化到具体 `TaskUnit`，再与 `ClientRecord`
中的能力声明匹配。调度器只能把任务分派给满足硬要求的客户端。第一版只需要支持
有限的资源键和值、环境标识和标签，不需要通用布尔表达式语言。

CWL 的 `requirements` 与 `hints`、Kubernetes 的资源 request/limit，以及 Ray 的
预定义资源、自定义资源和标签都说明：能力声明应当可被调度器读取，但不需要把
任务域知识写入调度器。

## 12. 执行器契约

执行器负责实际处理已经确定的 `TaskUnit`。它不知道如何修改任务图，不决定结果
是否正式有效，也不自行重试。

### 12.1 统一请求与提交信封

第一版使用 `ExecutionRequest` 和 `ExecutionSubmission` 作为执行器公共边界：

```text
ExecutionRequest
  -> attempt、租约和 fencing 上下文
  -> typed input artifact 引用
  -> 必需命名输出及其 schema
  -> 资源、时间和环境限制
  -> 插件提供的执行说明

ExecutionSubmission
  -> attempt、租约和 fencing 上下文
  -> 执行结果类别
  -> 候选命名输出或原始输出引用
  -> 日志、环境、用量和成本摘要
  -> 结构化错误信息
```

Mesos 的执行器接口很小：执行器接收任务描述，并向平台发送状态。CWL 也将具体
命令行工具的异构输入输出封装在统一描述之后。TokenShare 沿用这种思路：公共信封
保持稳定，执行器内部实现可以不同。

### 12.2 AI 与确定性程序的共享边界

AI 执行器和确定性程序执行器共享任务生命周期、租约、请求信封和提交信封，但不
强行共享内部调用格式：

| 执行器类别 | 执行器特有输入 | 执行器特有原始输出 |
|---|---|---|
| AI、本地模型或未来的人类 worker | `PromptPackage`、模型参数、工具提示和上下文引用 | `RawModelOutput`、模型标识、token 用量、结束原因和工具调用记录 |
| 确定性程序 | 程序入口、结构化参数、输入文件和环境要求 | 程序生成文件、退出状态、标准输出、标准错误和资源用量 |

插件可以提供面向某类执行器的执行说明生成器和原始输出解析器。执行器只负责调用
目标系统并捕获原始结果；解析后的候选命名输出统一进入 `ExecutionSubmission`。
协议框架只读取公共提交信封。

这保留了原始设计中的：

```text
PromptPackage -> RawModelOutput -> 结构化候选输出
```

但它只属于 AI 执行路径，不再成为所有任务插件必须经过的公共链路。

### 12.3 日志、环境和成本

执行器返回的信息分成两层：

- **协议推进所需信息**：attempt、租约上下文、执行结果类别、候选输出和结构化错误。
- **审计与实验信息**：日志引用、执行器版本、环境哈希、开始与结束时间、资源用量、
  token 用量、模型标识和成本摘要。

正式 artifact、重要日志和需要保留的原始 AI 输出应持久化为带内容哈希的引用，
事件日志保存引用和摘要，不默认内联完整 prompt、模型输出或工具调用参数。
OpenTelemetry 的 GenAI 语义约定也提示，这些内容可能包含敏感信息。其字段仍处于
持续演化状态，因此 TokenShare 第一版只借鉴“记录用量、错误和可选内容引用”的
原则，不绑定某套遥测字段名称。

执行器报告的成本仅用于审计和实验指标。奖励仍由第 9 章中的协议策略计算，不能
由客户端自报成本直接决定。

### 12.4 模拟执行器与真实执行器分开

客户端行为模拟和真实执行器应当分开。第一版使用 `SimulationProfile` 和模拟包装
层，在不改写协议状态机的前提下注入故障：

```text
SimulationProfile + 固定随机种子
  -> 模拟包装层决定是否离线、延迟、返回错误、篡改候选输出或延迟提交
  -> 被包装的 fixture、AI 或确定性执行器产生正常或受扰动的提交
  -> 协议框架只观察普通租约、attempt、验证和恢复事件
```

这样，`MockAIExecutor` 和故障模拟不再混为一谈：

- `MockAIExecutor` 使用 fixture 或确定性规则生成 AI 风格原始输出，用于测试解析链路。
- 模拟包装层控制 offline、slow、invalid output 和 late submission 等实验行为，
  可以包裹任意执行器。
- 未来替换为真实 AI 或真实程序执行器时，协议状态机不需要改写。

SimGrid 强调模拟实验可以探索任意场景，并具备可重复、可观察的优势。Chaos Mesh
也将 delay、loss、corrupt、partition 等故障作为显式实验配置，而不是业务逻辑。
TokenShare 第一版不实现通用分布式模拟器，但采用同样的隔离原则。

### 12.5 第一版故障模拟

第一版至少支持以下 profile 行为：

| 模拟行为 | 协议应观察到的结果 |
|---|---|
| `offline` | 客户端不开始执行或不再发送心跳，租约最终过期。 |
| `slow` | attempt 延迟完成，可能触发影子执行。 |
| `executor_error` | 执行器返回结构化失败，协议按恢复策略决定是否重试。 |
| `invalid_output` | 执行器提交格式错误或领域错误结果，验证流程拒绝。 |
| `late_submission` | 客户端在租约过期或正式输出已经绑定后提交，结果只保留审计记录。 |

为了复现实验，每次注入应记录 profile 标识、版本、随机种子、目标 attempt 和实际
动作。它们属于实验审计记录，不属于决定正式协议状态的最小事件集合。协议恢复仍然
只依赖第 10.1 节列出的领域事实。

## 13. 实验插件

### 13.1 整数分解插件

整数分解用于验证协议框架的递归拆分、调度、验证、合并、故障恢复和结算。它不是
协议中心，也不绑定唯一执行方式。

候选能力：

- 将搜索空间拆分为多个任务。
- 使用程序或 AI 搜索候选因数。
- 验证候选因数。
- 汇总正式结果。
- 模拟离线、慢执行、错误结果和重复执行。

具体拆分方法需要在插件讨论阶段确定。

### 13.2 Lean 证明插件

Lean 用于验证协议框架能否承载需要AI自主处理、需要递归处理且需要确定性检查的
智能任务。

候选能力：

- 将定理或 proof state 表示为任务。
- 使用 AI 或其他执行器生成 proof patch。
- 使用固定 Lean 环境检查结果。
- 根据经过验证的中间结果继续产生任务。
- 合并已经验证的子证明。
- 保存环境哈希、错误日志和验证记录。

具体拆分和合并方式需要在插件讨论阶段确定。

## 14. 第一版明确不做

- 不实现真实区块链、钱包、智能合约和真实代币支付。
- 不实现真实多机器通信。
- 不实现各类安全防护和系统冗余。
- 不实现完整 Web UI。
- 不要求第三方现在就能动态上传新插件。
- 不将 factorization、Lean stub 或 structured report 领域规则写死在协议框架中。

## 15. 参考系统的可借鉴部分

### 15.1 CIEL

CIEL 的动态任务图、对象依赖和按需展开与 TokenShare 的结果驱动递归接近。
TokenShare 可以借鉴“依赖结果是否具体可用来判断任务就绪”的思想。CIEL 还会
记录根任务和后续任务描述，并在 master 重启后重放日志、重建动态任务图。这说明
TokenShare 的拆分结果必须持久化，而不是在恢复时重新调用非确定性执行过程。

TokenShare 与 CIEL 的区别是：客户端不能直接修改任务图。图变更由协议协调器
根据预先固定的插件规则完成。

来源：

- <https://www.usenix.org/conference/nsdi11/ciel-universal-execution-engine-distributed-data-flow-computing>

### 15.2 Airflow 与 Argo Workflows

Airflow 和 Argo Workflows 可以作为 DAG 依赖和节点就绪判断的工程参照。第一版
TokenShare 不必实现它们的完整条件表达能力。Argo Workflows 将重试上限、失败
类别、条件表达式和 backoff 分开配置，适合作为协议内部恢复策略的工程参照。

来源：

- <https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html>
- <https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html>
- <https://argo-workflows.readthedocs.io/en/release-3.7/enhanced-depends-logic/>
- <https://argo-workflows.readthedocs.io/en/release-3.5/retries/>

### 15.3 Temporal

Temporal 的 append-only Event History 和确定性重放原则适合用于指导 TokenShare
的状态恢复设计。非确定性的 AI 调用不应在状态恢复过程中重新执行。Temporal 将
瞬时错误、间歇性错误和永久错误区分处理，并允许配置重试间隔、上限和不可重试
错误类型。

来源：

- <https://docs.temporal.io/workflow-execution>
- <https://docs.temporal.io/workflow-execution/event>
- <https://docs.temporal.io/workflow-definition>
- <https://docs.temporal.io/encyclopedia/retry-policies>

### 15.4 Ray

Ray 的 lineage reconstruction 可以作为结果来源追踪和确定性任务重执行的参照。
TokenShare 对 AI 结果不能只依赖重执行恢复，因此正式结果仍需持久化。

来源：

- <https://docs.ray.io/en/latest/ray-core/fault_tolerance/objects.html>
- <https://docs.ray.io/en/latest/ray-core/fault_tolerance/tasks.html>

### 15.5 BOINC

BOINC 面向不稳定且不完全可信的志愿计算节点。它使用 deadline、重复实例、
`min_quorum`、错误实例上限、成功实例上限和 canonical instance 处理迟到、失败、
不一致结果和 credit 发放。TokenShare 第一版不复制 BOINC 的全部冗余计算机制，
但借鉴以下原则：

- 失败和迟到会生成新的执行尝试，而不是覆盖历史。
- 达到上限后任务必须停止，避免无限消耗资源。
- 正式结果选择与验证分开。
- credit 只在验证后发放；迟到结果是否获得 credit 必须有明确策略。

来源：

- <https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf>
- <https://github.com/BOINC/boinc/wiki/JobReplication>

### 15.6 MapReduce 与 Dryad

MapReduce 使用重执行作为主要容错机制，并使用原子 rename 保证重复 reduce 执行
最终只留下一个正式输出。Dryad 为 vertex execution 维护版本，并选择成功版本的
输出。TokenShare 借鉴“重复执行可以存在，但正式版本必须唯一绑定”的原则；由于
AI 执行可能非确定，TokenShare 还需要比这两者更严格地持久化正式输出。

来源：

- <https://research.google/pubs/mapreduce-simplified-data-processing-on-large-clusters/>
- <https://www.microsoft.com/en-us/research/wp-content/uploads/2007/03/eurosys07.pdf>

### 15.7 CWL

Common Workflow Language 将命令行工具和工作流表达为 typed input/output、数据
连接、运行要求和可选 hints。步骤在输入可用后才能执行，而具体调度时机由工作流
引擎决定。TokenShare 不实现完整 CWL 标准，但借鉴其分层方式：

- 插件声明输入、命名输出、依赖和执行要求。
- 协议协调器根据正式结果判断就绪，并决定实际调度。
- 硬要求与可选偏好分开。
- 第一版避免把任意表达式直接作为协议级拆分逻辑。

来源：

- CWL 论文：<https://arxiv.org/abs/2105.07028>
- Workflow Description：<https://www.commonwl.org/v1.2/Workflow.html>
- Command Line Tool Description：<https://www.commonwl.org/v1.2/CommandLineTool.html>

### 15.8 Mesos、Kubernetes 与 Ray

Mesos 的 executor API 将执行器边界压缩为“接收任务描述、发送状态”；Kubernetes
区分资源 request 和 limit；Ray 支持 CPU、GPU、内存、自定义资源和标签，并只在
资源满足时调度任务。TokenShare 借鉴以下原则：

- 执行器共享稳定的请求和提交信封，不要求内部调用格式相同。
- 插件声明硬能力要求和可选偏好，调度器读取这些声明但不理解任务域知识。
- 第一版只实现有限资源键、环境标识和标签，不设计复杂能力语言。

来源：

- Mesos 论文：<https://mesos.apache.org/assets/papers/nsdi_mesos.pdf>
- Kubernetes 资源管理：
  <https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/>
- Ray Resources：<https://docs.ray.io/en/latest/ray-core/scheduling/resources.html>

### 15.9 SimGrid 与 Chaos Mesh

SimGrid 强调分布式系统模拟的可重复性和可观察性。Chaos Mesh 将 delay、loss、
corrupt 和 partition 等故障作为显式实验配置。TokenShare 第一版只做本地协议实验，
不复制完整模拟平台，但借鉴“故障注入与业务逻辑隔离、实验参数可复现”的原则。

来源：

- SimGrid 论文：<https://hal.science/hal-04909441v1>
- Chaos Mesh NetworkChaos：
  <https://chaos-mesh.org/docs/simulate-network-chaos-on-kubernetes/>

### 15.10 OpenTelemetry GenAI 语义约定

OpenTelemetry 的 GenAI 语义约定为模型标识、token 用量、错误和可选内容记录提供
工程参照，同时明确提醒 prompt、输出和工具调用参数可能包含敏感信息。该约定仍在
演化，因此 TokenShare 第一版只借鉴审计分类和敏感内容隔离原则。

来源：

- <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/>

这些来源只作为设计启发，不表示 TokenShare 完全复刻其中任何系统。

1. **MapReduce**  
   Dean 和 Ghemawat 的 MapReduce 将大规模计算表达为可调度、可重试的任务，并讨论了 commodity cluster 上的失败处理。TokenShare 借鉴其“任务单元可重试、失败后重新执行、straggler 可缓解”的工程思路。  
   来源：USENIX OSDI 2004，<https://www.usenix.org/conference/osdi-04/mapreduce-simplified-data-processing-large-clusters>

2. **BOINC**  
   BOINC 是志愿计算平台，面向异构、不稳定、分布式参与者。TokenShare 的 client profile、异构能力、离线与可靠性建模借鉴这个方向。  
   来源：Journal of Grid Computing 2020，<https://link.springer.com/article/10.1007/s10723-019-09497-9>

3. **Ray**  
   Ray 面向 AI 应用提供分布式任务和 actor 框架。TokenShare 的 task graph 和 future distributed runtime 可以参考 Ray 的动态任务图思想，但第一阶段不依赖 Ray。  
   来源：USENIX OSDI 2018，<https://www.usenix.org/conference/osdi18/presentation/nishihara>

4. **Raft**  
   Raft 强调日志复制、leader election 和状态恢复。TokenShare 第一阶段不实现 Raft，但采用 append-only event log 和 replay/snapshot 作为控制平面恢复基础。  
   来源：USENIX ATC 2014，<https://www.usenix.org/conference/atc14/technical-sessions/presentation/ongaro>

5. **Practical Byzantine Fault Tolerance (PBFT)**  
   PBFT 是拜占庭容错复制的经典系统。TokenShare 第一阶段不实现 PBFT，但 verifier committee 的高风险任务阈值可参考 `3f+1` 成员、`2f+1` quorum 的思想。  
   来源：USENIX OSDI 1999，<https://www.usenix.org/conference/osdi-99/presentation/practical-byzantine-fault-tolerance>

6. **Dawid-Skene**  
   Dawid-Skene 用 EM 估计真实标签和观察者错误率。TokenShare 未来的 semantic task 可以用它作为 contributor reliability 和 latent quality 的基础模型。  
   来源：Applied Statistics 1979，DOI <https://doi.org/10.2307/2346806>

7. **GLAD**  
   GLAD 同时建模 annotator ability 和 item difficulty。TokenShare 的 semantic verification 可以借鉴其“任务难度 + worker 能力”的建模方式。  
   来源：NeurIPS 2009，<https://papers.nips.cc/paper/2009/hash/f899139df5e1059396431415e770c6dd-Abstract.html>

8. **MACE**  
   MACE 估计多标注者 competence，用于 noisy annotation 场景。TokenShare 未来处理低质量、恶意或不稳定 semantic contributors 时可以参考。  
   来源：ACL Anthology NAACL 2013，<https://aclanthology.org/N13-1132/>

9. **CrowdLab**  
   CrowdLab 将多标注者标签和模型预测结合，估计 consensus label、label quality score、annotator quality。TokenShare 未来的 semantic acceptance score 可以参考这种三类输出。  
   来源：arXiv 2210.06812，<https://arxiv.org/abs/2210.06812>

10. **Decomposed Prompting**  
    Decomposed Prompting 强调把复杂任务拆成模块化子任务，并允许子任务进一步替换为 prompt、模型或 symbolic function。TokenShare 的 adapter/decompose 接口与此方向一致，但 TokenShare 额外加入 lease、verification、settlement 和 ledger。  
    来源：ICLR 2023 OpenReview，<https://openreview.net/forum?id=_nGgzQjzaRy>

11. **Tree of Thoughts**  
    Tree of Thoughts 将中间 reasoning state 显式化为可搜索单元。TokenShare 可将 proof state、subgoal、repair attempt 等看作可分配、可验证的 task unit。  
    来源：NeurIPS 2023，<https://papers.neurips.cc/paper_files/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract-Conference.html>

12. **ReAct**  
    ReAct 把 reasoning trace 和 action 交错起来，说明智能任务常常需要工具交互和状态更新。TokenShare 的 execution log、tool metadata、environment hash 可以为这类任务保留 provenance。  
    来源：ICLR 2023 OpenReview/arXiv，<https://arxiv.org/abs/2210.03629>

13. **LeanDojo**  
    LeanDojo 提供 Lean theorem proving 与 retrieval-augmented language models 的数据和交互基础。TokenShare 的 Lean adapter 可以未来接入 LeanDojo 风格的 proof state、premise retrieval 和 verifier。  
    来源：NeurIPS 2023，<https://proceedings.neurips.cc/paper_files/paper/2023/hash/4441469427094f8873d0fecb0c4e1cee-Abstract-Datasets_and_Benchmarks.html>

14. **miniF2F**  
    miniF2F 是 formal olympiad-level mathematics benchmark。TokenShare 的 Lean 实验可以用 miniF2F 子集作为 benchmark。  
    来源：arXiv 2109.00110，<https://arxiv.org/abs/2109.00110>

15. **The Lean Mathematical Library (mathlib)**  
    mathlib 展示了 Lean 中大规模 formalized mathematics 的库生态。TokenShare 的 Lean verifier 必须固定 Lean/mathlib/lake 环境 hash，确保 proof replay。  
    来源：ACM CPP 2020，<https://doi.org/10.1145/3372885.3373824>

16. **Contract Net Protocol**  
    Contract Net Protocol 提出任务公告、投标和授予的 distributed problem solving 模式。TokenShare 的 routing/assignment 可以看作更强约束版本：加入 task DAG、verification、failure recovery 和 delayed settlement。  
    来源：IEEE Transactions on Computers 1980，<https://doi.org/10.1109/TC.1980.1675516>

17. **IMClaw / OpenClaw Agent collaboration tooling**  
    IMClaw 的公开 Skill/SDK 暴露了任务创建、认领、释放、完成、取消、指派、子任务、依赖、`task_updated` 事件、授权审批、群聊上下文隔离和 Agent wake 机制。TokenShare 借鉴这些协作控制面原语：共享任务板、原子 claim、DAG dependency guard、风险授权和事件唤醒。但公开资料没有显示 IMClaw 具备 typed artifact schema、verification contract、merge contract、append-only replay ledger、贡献归因、reward/penalty settlement 或 Lean/formal verifier；TokenShare 必须把这些作为协议核心，而不是复刻聊天协作平台。  
    来源：官方站点 <https://imclaw.mosi.cn/>；Skill latest API <https://imclaw-server.app.mosi.cn/api/v1/skill/latest>；公开仓库 <https://github.com/OpenMOSS/imclaw-skill>；npm channel extension <https://www.npmjs.com/package/imclaw>。截至本设计稿，未找到 IMClaw 后端源码，因此只比较公开 Skill/SDK/API surface。

