# TokenShare P01-P22 候选机制规范与主 TDD 整合记录

> 状态：已整合为主 TDD 输入（Integration Record），不再作为待裁决清单
> 独立基线版本：`candidate-baseline-v3`
> 日期：2026-06-22；整合日期：2026-06-23
> 适用范围：TokenShare V1 本地研究原型
> 论文范围：P01 Dryad、P02 CIEL、P03 Cilk、P04 MapReduce、P05 BOINC、P06 CWL、P07 Mesos、P08 SimGrid、P09 Ray、P10 Raft、P11 PBFT、P12 Dawid-Skene、P13 GLAD、P14 MACE、P15 CROWDLAB、P16 Decomposed Prompting、P17 Tree of Thoughts、P18 ReAct、P19 LeanDojo、P20 miniF2F、P21 mathlib、P22 Contract Net Protocol
> 文档标识：文件名作为稳定引用保留；标题与正文中的论文范围以 P01-P22 为准

2026-06-23 整合说明：本文原先用于把 P01-P22 机制与主 TDD 对照，并列出需要负责人
裁决的候选项。当前推荐取舍已经被整合进
`2026-06-03-tokenshare-protocol-technical-design.md`，后续实现应以主 TDD 和阶段规格为
权威。本文保留为研究来源、裁决依据和历史对照；若本文旧段落与主 TDD 冲突，以主 TDD
为准。

## 1. 文档目的与方法边界

本文回答两个不同问题：

1. 如果不沿用 TokenShare 现有设计文档的结论，仅从 P01-P22 的原始论文机制、TokenShare 的研究目标和 V1 边界出发，TokenShare 应当具备什么机制？
2. 这套独立推导出的候选机制，与现有主设计文档相比有哪些一致、偏差、遗漏或冲突？

为避免“先看到现有设计，再倒推论文支持它”的确认偏差，本文采用冻结式结构：

- **第一部分：独立候选规范**。只使用 P01-P22 本地论文原文，以及 TokenShare 已确定的研究目标与 V1 边界。论文按所支持的协议问题参与论证，不按收录批次形成附录或追加层；该部分完成后冻结为 `candidate-baseline-v3`。
- **第二部分：现有设计对照**。冻结第一部分后，才读取并比对现有主 TDD、阶段规范、feature 状态与代码事实。

第二部分不得为了提高表面一致性而改写第一部分。若发现第一部分存在论文事实错误，只能以“勘误”形式显式记录。

## 2. 规范用语

- **MUST（必须）**：缺少该能力会破坏 TokenShare 的核心协议语义、重放能力或实验结论。
- **SHOULD（应当）**：V1 原则上应实现；如果延期，必须记录理由、替代措施和影响。
- **MAY（可以）**：有明确价值，但不阻塞 V1 核心实验。
- **不得**：明确禁止的实现方式，因为它会破坏某项不变量。

每项要求使用稳定编号。编号表达机制归属，不代表代码模块名称。

## 第一部分：独立候选规范（冻结基线）

## 3. TokenShare 的问题模型

TokenShare 不是普通队列，也不是只负责调用多个 agent 的编排脚本。它要验证的是一套可审计协议：一个大任务如何被递归拆分为任务图，如何分派给不同执行者，如何接收多个候选结果，如何验证并选择唯一正式结果，如何把正式结果合并为上层结果，以及如何依据可追溯证据结算贡献。

P01-P22 共同揭示了十四个不能混在一起的问题：

1. **计算结构**：任务之间究竟是树、DAG，还是运行时动态增长的图？
2. **数据结构**：任务消费和产生的具体对象是什么，如何命名、定型和版本化？
3. **执行身份**：逻辑任务、一次租约和一次实际执行是否被区分？
4. **提交语义**：多个执行结果并存时，哪个结果可以进入后继任务？
5. **正确性判断**：验证是确定性检查、语义比较，还是多副本共识？
6. **调度与恢复**：能力、资源、截止期、慢任务和执行者失联如何影响分派？
7. **审计与结算**：系统能否在不重新执行 agent 的情况下重建历史，并解释奖励为什么产生？
8. **实验有效性**：故障、时间、随机性和模型参数是否被固定，使不同运行之间可以复查和比较？
9. **控制面一致性**：多个协调器如何对事件顺序达成一致，与“任务答案是否正确”是否被错误混为一谈？
10. **弱验证真值推断**：没有完备 checker 时，如何表达概率性判断、观察者可靠性及其假设，而不把一致性误称为证明？
11. **求解策略与协议任务**：decomposition、search、reasoning step 中哪些只是执行器内部过程，哪些足够稳定、可验证，能够晋升为协议 `TaskUnit`？
12. **工具交互与审计事实**：tool action、observation、错误和环境结果如何留痕，与不可验证的自由文本 reasoning trace 如何区分？
13. **验证环境身份**：proof/checker 的结论依赖哪个固定工具链、fixture、library 和 namespace/context，如何避免环境漂移让正确结果被误判？
14. **分派与正确性**：能力过滤、任务排序、指派和拒绝原因如何记录，且不把“被选中执行”误当成“结果已经可信”？

候选规范的总原则是：

> **任务可以有多次执行和多个候选结果，但依赖关系只能消费经过验证并被正式选定的 immutable artifact；所有影响结果的动态决定都必须事件化并可重放。**

## 4. 从二十二篇论文选择什么

| 论文 | 选择借鉴的核心机制 | TokenShare 中的用途 | 不直接照搬的部分 |
|---|---|---|---|
| P01 Dryad | 任意 DAG、带类型的数据通道、逻辑图到物理执行的分离、执行版本与 lineage 恢复 | 建立任务图、artifact 边、attempt 版本和失败重放 | 分布式网络通道、集群级 vertex manager、完整数据平面 |
| P02 CIEL | 动态任务图、future/expected output、结果驱动的 spawn、不可变对象、continuation | 支持递归拆分和运行中扩图，同时保持依赖可判定 | 通用分布式脚本语言、任意运行时代码生成和完全惰性求值 |
| P03 Cilk | spawn tree 与 precedence DAG 分离、work/span、关键路径、work stealing 原理 | 区分任务血缘与真正执行依赖；定义并行度和瓶颈指标 | 线程级 continuation runtime、V1 中的底层 work-stealing deque |
| P04 MapReduce | 私有临时输出、重复执行、首个成功提交、原子 canonical 化、慢任务备份 | 隔离候选结果，支持重试和 shadow attempt，避免重复副作用 | 固定 Map/Shuffle/Reduce 编程模型和大规模集群数据交换 |
| P05 BOINC | job/instance 分离、异构能力匹配、截止期、多副本验证、quorum、canonical result、验证后 credit | 支持弱验证任务、多执行者候选、结果仲裁和结算门槛 | 公共志愿计算网络、生产级信誉、反作弊和真实货币系统 |
| P06 CWL | 版本化工具描述、具名强类型输入输出、requirements/hints、运行环境声明 | 定义插件和执行器的稳定契约，让协议核心不理解领域任务 | 完整 CWL 标准、容器编排和所有 workflow 表达能力 |
| P07 Mesos | 薄核心、框架与执行器分工、资源与能力声明、租约/状态回报、两级调度思想 | 保持协议核心、任务插件、执行器边界；分离硬约束和偏好 | V1 中的多租户资源 offer 市场、集群公平性和抢占系统 |
| P08 SimGrid | 可控、可观测、可重复的模拟实验；模型、场景和扩展机制分离；准确性/规模/可用性的取舍 | 固化故障注入、模拟时间、随机种子、实验参数和运行报告，使协议实验可复查 | 完整离散事件模拟器、网络/计算性能模型和对真实分布式性能的外推 |
| P09 Ray | 动态任务图、future/immutable object、任务与 actor、资源异构、控制状态与调度/对象存储解耦 | 补强 AI 风格动态任务、执行器能力、控制状态可观测性和未来远端 runtime 边界 | V1 直接依赖 Ray、把 actor 内存当协议事实、对 AI 输出做 lineage 自动重执行 |
| P10 Raft | 复制日志、提交后按序应用、term/leader fencing、快照与状态机恢复 | 澄清 event ledger 的提交/投影边界，并为未来多协调器提供控制面参考 | V1 leader election、日志复制；用节点多数判断任务答案正确性 |
| P11 PBFT | 在独立 Byzantine replica 假设下对确定性状态机请求排序；view、checkpoint、认证和 quorum 证据 | 明确未来高威胁控制面的故障模型和安全假设，约束“委员会”一词的使用 | V1 完整 PBFT；把 `3f+1`/`2f+1` 直接套到 AI verifier 或语义答案投票 |
| P12 Dawid-Skene | 无 gold label 时联合估计潜在类别和观察者混淆矩阵；显式独立性、可识别性和局部最优限制 | 作为未来离散弱验证任务的可选统计插件，输出概率与不确定性 | factorization/Lean 的验证路径、自由文本整体质量判定、把模型估计当确定性证明 |
| P13 GLAD | 用 item difficulty 与 annotator ability 的交互联合估计潜在二元标签、任务难度和贡献者能力 | 作为未来弱验证插件的 difficulty-aware 备选模型，要求记录二元标签、先验、初始化与 EM 收敛证据 | V1 通用验证器、自由文本质量分、把 ability 当永久信誉或直接结算权重 |
| P14 MACE | 显式 spam latent variable、annotator strategy、EM/VB 与准确率/覆盖率取舍 | 作为未来离散标注任务识别低质或策略性观察的备选模型 | V1 反作弊系统、生产身份信誉、把未满足 spam 模型假设的错误统称为恶意 |
| P15 CROWDLAB | 将交叠标注与 out-of-sample classifier probability 加权，输出 consensus、label quality 和 annotator quality | 作为未来存在可校准 classifier 时的语义质量评分备选算法 | 没有训练/校准数据时直接使用；用模型置信度替代领域 checker；把 quality score 当证明 |
| P16 Decomposed Prompting | decomposer、controller、版本化 sub-task handler、具名中间结果和递归分解 | 补强 `ExpansionProposal` 的 handler/typed I/O/merge 语义，并保持领域拆分策略可替换 | 把 prompt program 当协议核心语言、执行器边生成边直接改 authoritative graph |
| P17 Tree of Thoughts | thought generation、state evaluation、BFS/DFS、branch/prune 和成本—性能取舍 | 作为未来插件内部搜索策略；只把持久、结构化、可验证的 subgoal/proof state 晋升为 `TaskUnit` | 把每个 thought 都变成任务、用 LM value/vote 直接做 canonical validation、V1 实现通用搜索引擎 |
| P18 ReAct | action/observation 交错、工具调用轨迹、异常后的计划调整 | 定义 tool provenance、action/observation artifact 和执行日志边界 | 强制保存隐藏 chain-of-thought、把 free-form reasoning 当审计证明、V1 接入生产工具 agent |
| P19 LeanDojo | proof state、tactic、next/error/done 交互；精确 proof environment；premise retrieval 与 best-first search | 约束 Lean stub 的 checker-authoritative 与 environment-bound 验证；为未来真实 Lean adapter 留接口 | V1 实现 DPR、模型训练、真实 premise retrieval 或完整 proof search |
| P20 miniF2F | 固定 benchmark 版本、问题 ID、验证/测试划分、题型与难度覆盖 | 指导 Lean 实验 fixture 的版本化、分层和可比较性 | 强制 V1 下载完整 benchmark、把 benchmark 写进协议核心、以 benchmark 分数替代协议验证 |
| P21 mathlib | Lean/mathlib 的 library、automation、结构层级与版本演进背景 | 说明 proof checker 依赖具体 library/toolchain 语境，辅助定义环境身份 | 将其误写为 environment hash/replay 算法来源；V1 引入完整 mathlib |
| P22 Contract Net Protocol | eligibility、task abstraction、bid specification、award、report/refusal、deadline 与 no-bid 原因 | 补强调度决策信封、能力过滤、确定性直接指派和不可分派原因 | V1 实现广播、竞价市场和分布式协商；把 task-specific ranking 伪装成论文给出的通用评分算法 |

这些论文不是二十二套并列功能，而是从不同层面约束同一协议闭环：Dryad、CIEL、Cilk、MapReduce、BOINC、CWL、Mesos、Ray 和 Contract Net 共同限定任务图、执行身份、能力匹配、候选提交与正式选择；Dawid-Skene、GLAD、MACE 和 CROWDLAB 只为没有强 checker 的离散弱验证提供带假设的统计选项；Decomposed Prompting、Tree of Thoughts 与 ReAct 约束求解器内部状态如何跨越协议边界；LeanDojo、miniF2F 与 mathlib 约束 proof-like 实验的 checker、环境和 fixture 身份；SimGrid、Raft 与 PBFT 则分别界定可复现实验和控制面一致性。它们在 TokenShare 中形成以下统一流程：

```text
任务描述
  -> 任务图/动态扩图
  -> 就绪判定
  -> 能力匹配与租约
  -> 私有 attempt 执行
  -> 候选 artifact
  -> 验证/仲裁
  -> canonical bundle
  -> 后继任务解锁
  -> 合并与上层提交
  -> 结算与审计重放
```

协议闭环外侧还有七条横切边界，不能把它们压缩成一个含糊的“结果验证”步骤：

```text
SimulationProfile/ExperimentRun -> 控制故障、时间、随机性与报告（P08）
Control-plane ordering          -> 决定事件以什么顺序成为事实（P10/P11）
Latent-label estimation         -> 仅在无强 checker 且满足具体模型前提的离散弱验证任务中估计真值（P12-P15）
Solver strategy boundary        -> decomposition/search/reasoning 属于版本化插件或执行器（P16-P18）
VerifierEnvironment             -> 固定 checker、fixture、library 和上下文身份（P19/P21）
BenchmarkProfile                -> 固定 Lean fixture/benchmark 版本和问题集合（P20）
AllocationDecision              -> 记录 eligibility、排序、指派或不可分派原因（P22）
```

## 5. 核心对象模型

### 5.1 必须分离的对象

`TS-OBJ-001` **MUST**：协议至少区分以下逻辑对象，不能用一个“task 状态”对象同时代替它们。

| 对象 | 责任 | 是否可有多个 |
|---|---|---|
| `TaskUnit` | 一项逻辑工作及其稳定语义 | 每个逻辑任务一个 |
| `TaskEdge` | 任务之间的控制、数据或派生关系 | 多个 |
| `Artifact` | 不可变的输入、候选输出或正式输出 | 每任务可多个 |
| `Lease` | 协调器在一段时间内授予执行者的执行许可 | 每任务可多次授予 |
| `Attempt` | 执行者根据某个 lease 进行的一次实际执行 | 每任务可多次执行 |
| `Validation` | 对某个 attempt/artifact bundle 的验证记录 | 每候选可多次验证 |
| `CanonicalSelection` | 对任务正式输出的唯一选择决定 | 每任务至多一个有效决定 |
| `EnvironmentRef` | 固定执行或验证所依赖的 checker、fixture、toolchain、library 与配置身份 | 可被多个 task/attempt/validation 引用 |
| `SettlementEntry` | 基于已验证贡献产生的结算记录 | 每贡献主体可多个 |
| `Event` | 状态变化和决定的 append-only 事实 | 持续追加 |

`TS-OBJ-002` **MUST**：稳定身份与执行身份分离。

- `task_id` 表示逻辑工作，不因重试改变。
- `lease_id` 表示一次授权，不得在续租或重新分派时复用。
- `attempt_id` 表示一次执行，不得用 `task_id` 冒充。
- `artifact_id` 表示内容不可变的对象引用；内容改变必须产生新 ID。
- `validation_id` 和 `selection_id` 必须可独立审计。

`TS-OBJ-003` **MUST**：所有协议对象包含显式 `schema_version`。插件、执行器和 artifact 还必须有各自的类型/实现版本，不能只依赖当前代码版本解释旧事件。

`TS-OBJ-004` **SHOULD**：实验基础设施另行区分 `ExperimentRun` 与 `SimulationProfile`。前者标识一次完整实验，后者固定故障场景、模拟时钟、随机种子和模型参数；二者可以引用协议 run，但不得进入 `TaskUnit` 或 executor 领域逻辑充当隐藏状态。

`TS-OBJ-005` **MUST**：凡验证结论依赖具体 checker/toolchain/fixture/library 的 task，必须绑定不可变 `EnvironmentRef` 或等价 digest。自由文本 `environment_summary` 可以用于展示，但不能作为唯一验证身份；同一引用内容改变时必须产生新版本或新 digest。

### 5.2 建议的最小关系

```text
TaskUnit 1 ---- * Lease 1 ---- * Attempt
TaskUnit 1 ---- * TaskEdge
Attempt  1 ---- * Artifact(candidate)
Attempt  * ---- 1 EnvironmentRef(execution)
Artifact 1 ---- * Validation
Validation * -- 1 EnvironmentRef(checker)
TaskUnit 1 ---- 0..1 CanonicalSelection ---- 1..* Artifact(canonical bundle)
Canonical Artifact ---- * downstream TaskEdge
CanonicalSelection ---- * SettlementEntry
```

关系图表达语义，不强制采用对应 SQL 表数量。实现可以合并物理表，但不得丢失逻辑身份和约束。

## 6. 任务图：同时保留派生树与执行 DAG

### 6.1 为什么需要两种视图

递归拆分天然产生“父任务生成子任务”的树形血缘，但真实执行顺序通常不是树：一个子任务可能需要多个输入，一个 artifact 可能被多个后继共享，合并任务也会依赖多个兄弟任务。因此只保存 `parent_id` 无法准确表达可执行条件；只保存 DAG 又会丢失“谁提出了这次拆分”的解释链。

### 6.2 规范要求

`TS-GRAPH-001` **MUST**：同时维护两类关系：

- `decomposition_parent`：记录递归拆分、委派或 continuation 的生成血缘。
- `dependency_edge`：记录实际就绪判定所需的数据/控制依赖。

`TS-GRAPH-002` **MUST**：就绪判定只依据执行 DAG 的输入绑定，不得因为父任务“已经运行过”就认为子任务可执行。

`TS-GRAPH-003` **MUST**：每条数据依赖边至少声明：

```json
{
  "edge_id": "edge-...",
  "producer_task_id": "task-A",
  "producer_output_name": "factors",
  "consumer_task_id": "task-B",
  "consumer_input_name": "candidate_factors",
  "artifact_type": "tokenshare.factor-list/v1",
  "binding_policy": "canonical_only"
}
```

`TS-GRAPH-004` **MUST**：后继输入只能绑定生产任务的 canonical artifact。候选、验证失败、已过期 lease 或 losing attempt 的 artifact 不得解锁后继任务。

`TS-GRAPH-005` **MUST**：任务图必须拒绝已知环。动态扩图时必须在接受变更前检查新增依赖不会形成从任务到自身的可达路径。

`TS-GRAPH-006` **SHOULD**：记录以下图指标，用于实验比较而不是直接决定正确性：

- `work_estimate`：所有任务工作量估计之和，对应 Cilk 的 work 思想。
- `critical_path_estimate`：最长依赖路径估计，对应 span/critical path。
- `ready_width`：某时刻可并行执行的任务数。
- `retry_work` 与 `wasted_work`：重试、失效和未胜出的 shadow attempt 消耗。

### 6.3 最小验收条件

1. 一个父任务生成两个子任务，再由 merge 任务依赖两个子任务的 canonical 输出；系统可同时展示派生树和执行 DAG。
2. 两个子任务中只有一个完成时，merge 不得进入 ready。
3. 候选结果存在但未 canonical 化时，merge 不得进入 ready。
4. 动态新增反向依赖造成环时，扩图决定被拒绝并留下事件。

来源：P01 的任意 DAG/数据通道与 lineage；P03 的 spawn tree/precedence DAG 和 work/span。

## 7. 动态扩图：以结构化提案驱动递归拆分

### 7.1 核心思想

TokenShare 的“递归拆分”不能等同于执行器直接向数据库插入子任务。动态任务图需要稳定的预期输出引用、结构化扩图提案和协调器的原子接受；decomposer 与 sub-task handler 必须可独立版本化和替换；搜索中的 partial state 只有具备稳定契约、可独立调度且可验证时才能晋升为协议任务。P02、P16 和 P17 分别从动态图、可替换分解器和搜索状态控制三个角度支持这一边界。

### 7.2 规范要求

`TS-EXPAND-001` **MUST**：执行器不得直接修改 authoritative task graph。执行结果只能包含 `ExpansionProposal`，由协议核心作出 `accepted` 或 `rejected` 决定。

`TS-EXPAND-002` **MUST**：`ExpansionProposal` 至少包含：

```json
{
  "proposal_id": "proposal-...",
  "origin_task_id": "task-parent",
  "origin_attempt_id": "attempt-...",
  "plugin_id": "factorization",
  "plugin_version": "1.0.0",
  "tasks": [],
  "edges": [],
  "expected_outputs": [],
  "proposal_digest": "sha256:..."
}
```

`TS-EXPAND-003` **MUST**：每个动态子任务和 expected output 使用确定性派生 ID，至少由 origin task、被接受的 origin attempt、插件版本、逻辑位置和规范化内容共同决定。相同已持久化提案在重放时必须产生相同 ID。

`TS-EXPAND-004` **MUST**：扩图接受过程是原子的：校验插件权限、schema、ID 冲突、输入引用、类型兼容和无环性后，要么全部写入并追加 `GraphExpansionAccepted`，要么一个也不写入并追加 `GraphExpansionRejected`。

`TS-EXPAND-005` **MUST**：任务若声明 expected output，最终必须满足以下二选一：

- 自己发布并 canonical 化该输出；
- 通过被接受的扩图把该输出委派给子图，并保存从 expected output 到子图最终输出的解析关系。

不得让 expected output 永久悬空而把父任务标记为成功。

`TS-EXPAND-006` **MUST**：动态扩图是非确定性或外部决定时，完整提案和接受决定必须持久化。状态重放不得重新调用 agent 生成“看起来相同”的拆分。

`TS-EXPAND-007` **SHOULD**：插件声明其扩图能力与限制，例如 `can_expand=true`、最大深度、单次最大子任务数、允许生成的 task kind 和 artifact type。

`TS-EXPAND-008` **SHOULD**：对 logically equivalent 的重复提案提供幂等检测，避免协调器重启或执行者重报导致子图重复生成。

`TS-EXPAND-009` **MUST**：每个拟生成子任务必须声明 `task_kind`、目标 plugin/handler ID 与版本、具名输入输出端口和父级 expected output/merge slot 的解析关系。decomposer 可以由 AI、确定性程序或人工策略实现，但协议核心不得执行 prompt program 或依赖某个特定 decomposer 的自由文本约定。

`TS-EXPAND-010` **MUST**：只有满足以下条件的中间状态才可晋升为 authoritative `TaskUnit`：有稳定 schema、明确输入输出、可独立调度、可验证完成条件和受控图关系。临时 thought、未解析计划、LM 自评 value/vote 和 executor 工作记忆只能保存在 attempt artifact/log 中，不得自动获得任务身份、解锁下游或产生结算资格。

`TS-EXPAND-011` **MAY**：插件未来可声明 `search_policy_id`、breadth/depth/branch budget、state evaluator version 和 pruning policy，用 BFS、DFS 或其他搜索产生候选扩图；V1 不需要通用 ToT runtime，任何搜索决定若影响 authoritative graph 都必须先持久化并通过 `ExpansionProposal` 边界。

### 7.3 示例流程

```text
AttemptCompleted
  -> ExpansionProposalRecorded
  -> proposal schema/type/cycle validation
  -> GraphExpansionAccepted
  -> child TaskUnits + Edges + ExpectedOutputs materialized
  -> ready calculation
```

如果父 attempt 后续未通过验证，则其扩图默认不得成为 authoritative graph 的组成部分。V1 最稳妥的策略是：先验证产生扩图提案的 attempt，再接受扩图；若实验需要推测性扩图，则新子图必须保持 speculative，直到来源 attempt canonical 化。

### 7.4 最小验收条件

1. 同一持久化提案重复处理两次，只产生一组子任务。
2. 重启后重放事件，不调用执行器也能恢复完全相同的子图和 ID。
3. 插件提出未声明类型或形成环的边时，整项提案被拒绝。
4. 父任务通过子图委派 expected output 后，只有子图正式输出完成，父输出才可解析。
5. decomposer 输出一个只有自由文本 reasoning、没有 handler/typed I/O 的“子任务”时，提案被拒绝。
6. executor 产生多个搜索 thought 时，只有被插件转换为合规 subgoal 的状态可进入图，其余只保留为 attempt artifact。

来源：P02 的 dynamic task graph、future reference、expected output、spawn 与 continuation；P01 的 DAG 约束；P16 的 decomposer/controller/sub-task handler 与递归分解；P17 的 state generation/evaluation/search 分离及成本边界。

## 8. 插件与执行器：版本化契约和薄协议核心

### 8.1 三层职责

P06 和 P07 共同支持一种适合 TokenShare 的边界：协议核心只管理通用生命周期、租约、artifact、验证入口、事件和结算；插件解释领域任务；执行器负责在具体环境中运行。

| 层 | 必须知道什么 | 不应知道什么 |
|---|---|---|
| 协议核心 | 通用对象、状态、不变量、事件、租约、引用和策略接口 | factorization、Lean、报告内容的领域语义 |
| 任务插件 | task kind、typed I/O、拆分、领域验证、合并规则 | SQLite 事务细节、全局调度器内部状态 |
| 执行器 | 如何满足某种执行请求、能力与资源、如何返回 submission | 如何选 canonical、如何结算、如何修改任务图 |

### 8.2 插件描述符

`TS-PLUGIN-001` **MUST**：每个插件提供不可含糊的版本化描述符，至少声明：

```json
{
  "plugin_id": "factorization",
  "plugin_version": "1.0.0",
  "descriptor_schema_version": "1",
  "task_kinds": ["factorize", "verify_factorization", "merge_factorization"],
  "input_ports": {},
  "output_ports": {},
  "requirements": [],
  "hints": [],
  "supports_expansion": true,
  "validator_id": "factorization.exact/v1",
  "merge_policy_id": "factorization.product/v1"
}
```

`TS-PLUGIN-002` **MUST**：输入输出是具名且带类型的端口。类型至少包含稳定的 type identifier 和 schema version；不能只用文件名或自然语言说明约定。

`TS-PLUGIN-003` **MUST**：区分 `requirements` 与 `hints`：

- requirement 不满足时不得分派或执行。
- hint 不满足时仍可执行，但调度器应记录为何没有采用偏好。

`TS-PLUGIN-004` **MUST**：插件版本和 validator/merge policy 版本进入 task、attempt、validation 和事件记录。重放旧任务时不得静默使用新版本解释旧输出。

`TS-PLUGIN-005` **SHOULD**：插件仅通过稳定协议接口创建扩图提案、验证候选、合并 canonical inputs，不直接写协议核心表。

### 8.3 执行器契约

`TS-EXEC-001` **MUST**：统一执行请求至少包含：

- `task_id`、`lease_id`、`attempt_id` 与 fencing token；
- plugin/task kind/version；
- 已绑定的 canonical input artifact refs；
- required output ports；
- requirements、hints、deadline 和故障模拟参数；
- 运行参数和其 canonical digest；
- 本次执行和后续验证必须使用的 `EnvironmentRef`/environment contract digest。

`TS-EXEC-002` **MUST**：执行 submission 至少包含：

- 对应身份和 fencing token；
- 终态 `succeeded`、`executor_error`、`invalid_output` 等；
- 候选 artifact refs 与 output-port binding；
- 可选 expansion proposal；
- 可审计的时间、错误和资源使用摘要；
- 若使用工具或交互式环境，action/observation log artifact ref 与 tool provenance。

`TS-EXEC-003` **MUST**：执行器的 submission 只是候选事实，不得自行把 task 标记完成、选择 canonical 或触发结算。

`TS-EXEC-004` **SHOULD**：执行器注册能力声明，包括支持的 plugin/task kind、平台、资源、依赖和隔离能力。能力声明必须有 freshness/epoch，不能无限期信任旧信息。

`TS-EXEC-005` **MAY**：未来执行器可在本地进程、容器或远端 worker 中实现，但三者使用同一协议请求/响应语义。

`TS-EXEC-006` **MUST**：若执行器内部采用 Ray 式 actor、长驻 agent session 或其他可变内存对象，其状态只属于执行器实现。凡是会影响协议恢复、验证、扩图、合并或结算的状态，必须显式提交为 artifact/event；协议不得依靠“该 actor 仍活着”解释历史。

`TS-EXEC-007` **SHOULD**：控制状态存储、调度决策和 artifact 数据访问保持接口分离。V1 可以全部位于本机，但 scheduler 不应成为 task lineage、artifact location 和历史决定的唯一保存者。

`TS-EXEC-008` **MUST**：request 绑定的 environment contract 至少标识 executor/checker ID 与版本、fixture/profile digest、依赖或 library 版本以及影响解析/验证的配置。submission 必须回显实际 environment digest；协议公共验证发现请求、实际执行和 checker 环境不一致时，不得把结果送入正常 canonical 路径。

`TS-EXEC-009` **MUST**：工具型 executor 的审计日志以显式 action/observation 为核心，至少记录 action kind、tool ID/version、规范化输入 digest、输出/observation artifact ref、状态、错误和顺序。自由文本 reasoning trace 可以作为受政策控制的 raw artifact 保存，但不是必需协议字段，也不得单独作为正确性、扩图或结算证据。

`TS-EXEC-010` **MAY**：未来 executor 可采用 ReAct 式迭代 controller 或长驻 agent session，但必须受声明的 tool allowlist、step/budget/deadline 限制；每次会改变外部状态或影响正式结果的动作都必须产生可审计 observation/result，而不能只留在模型上下文中。

### 8.4 最小验收条件

1. factorization、Lean stub 和 structured report stub 都通过同一协议接口执行，核心中不存在按插件名称分支的领域逻辑。
2. 不满足 hard requirement 的执行器不会获得 lease；只不满足 hint 时仍可被选择并留下原因。
3. 改变插件或 validator 版本后，旧事件仍引用旧版本，不被静默升级。
4. 执行器尝试直接报告 canonical/completed 时，协议层忽略或拒绝越权字段。
5. 终止并重建一个有内部状态的 AI executor 后，协议仍能仅凭已保存 artifact/event 解释此前 submission；未提交的 actor 内存不被伪装为历史事实。
6. request 与 submission 的 environment digest 不一致时，候选不能进入正常领域验证或 canonical selection。
7. 工具型执行路径无需公开隐藏 chain-of-thought，但必须能从 action/observation artifacts 解释调用了什么工具、收到什么结果和为何形成该 submission。

来源：P06 的 typed ports、tool/workflow descriptor、requirements/hints 和版本语义；P07 的薄核心、framework/executor 分工与状态回报；P09 的 task/actor 区分、GCS 控制状态分离和 immutable object 边界；P18 的 action/observation 交错轨迹；P19 的可靠 proof-environment 交互；P21 的 Lean/mathlib 版本化工具链背景。

## 9. Task、Lease 与 Attempt：授权和执行必须可区分

### 9.1 状态职责

`TaskUnit` 表示逻辑目标，`Lease` 表示限时授权，`Attempt` 表示实际执行。三者分开后，系统才能正确表达失联重派、慢任务备份、迟到提交和多个候选结果。

`TS-ATTEMPT-001` **MUST**：一个 task 可有零到多个 lease，每个 lease 可对应一个或多个 attempt 记录，但一次实际启动必须有唯一 attempt ID。

`TS-ATTEMPT-002` **MUST**：lease 包含：

- `issued_at`、`expires_at`；
- executor identity 与 capability snapshot/digest；
- 单调递增的 fencing token 或 task assignment epoch；
- 允许执行的 task/plugin version；
- deadline 和接受迟到 submission 的审计政策。

`TS-ATTEMPT-003` **MUST**：过期、撤销或旧 fencing token 的 attempt 可以提交审计材料，但不得改变 task 的 authoritative 状态，不得成为 canonical，也不得获得正常完成结算。

`TS-ATTEMPT-004` **MUST**：attempt 的状态机与 task 的状态机分离。attempt 成功只表示产生了候选结果；task 只有在验证和 canonical selection 后才能成为协议意义上的成功。

`TS-ATTEMPT-005` **MUST**：重试产生新 lease/attempt，不能覆盖旧 attempt。旧错误、日志、候选 artifact 和验证结论保留。

`TS-ATTEMPT-006` **SHOULD**：区分下列结束原因：

- `offline`：执行者未能开始或失联；
- `slow`：超过调度阈值但不一定已失败；
- `executor_error`：执行器运行错误；
- `invalid_output`：产生输出但 schema/领域验证失败；
- `late_submission`：在授权失效后提交；
- `cancelled`：因已有 canonical 或策略撤销；
- `superseded`：合法完成但未被选为 canonical。

### 9.2 私有候选输出

`TS-ATTEMPT-007` **MUST**：attempt 输出先进入私有候选命名空间，例如：

```text
artifact://candidate/{task_id}/{attempt_id}/{output_name}/{content_digest}
```

候选命名空间不得被后继任务直接消费。

`TS-ATTEMPT-008` **MUST**：artifact 内容写入完成、digest 校验通过并被事件记录后，submission 才能进入验证。部分写入文件不得成为候选 artifact。

### 9.3 最小验收条件

1. 同一 task 连续重派两次，历史中保留两个 lease 和 attempt。
2. 旧 lease 的迟到成功结果不会覆盖新 attempt 的 canonical 输出。
3. executor 成功返回后、验证前，task 仍未处于协议成功状态。
4. attempt 输出写入中断时，不产生可验证的完整 artifact 引用。

来源：P01 的 vertex execution/version；P04 的重复执行和私有临时输出；P05 的 job/instance；P07 的授权、executor 与状态更新思想。

## 10. 验证、仲裁与 canonical bundle

### 10.1 两层验证

`TS-VERIFY-001` **MUST**：每个 submission 先经过协议公共验证，再进入插件领域验证。

公共验证至少检查：

- task/lease/attempt/fencing 身份一致；
- lease 时效与 submission 去重；
- required output ports 齐全；
- artifact 存在、不可变、digest 正确；
- artifact type/schema 与端口兼容；
- 插件和执行器版本可解析；
- request、submission 与 validator 的 environment contract 一致；
- 输出大小和安全边界未违规。

领域验证由插件决定，例如：

- factorization：所有因子乘积等于输入且满足定义域；
- Lean stub：proof artifact 在 request 绑定的固定 fixture/checker environment 下满足版本化判定；
- structured report stub：输出 schema、必要章节和引用关系满足验证器。

`TS-VERIFY-002` **MUST**：验证结果是独立对象，至少含 validator ID/version、输入 artifact digest、结论、理由代码、证据引用和时间。不得只在 task 上保存一个 `valid=true`。

### 10.2 验证策略

`TS-VERIFY-003` **MUST**：每种 task kind 显式声明 `verification_mode`，至少区分：

- `deterministic_exact`：单个候选可由确定性规则独立判定，例如 factorization 的乘积与定义域检查。
- `proof_checked`：候选携带可由固定版本 checker 重放的证明或 proof-like artifact，例如 Lean stub。
- `bounded_structural`：只能确定性验证 schema、必需字段、引用存在性和覆盖范围，不能据此宣称自由文本语义已经被证明。
- `latent_label_estimation`：没有 gold label 时，对多个离散观察标签做概率性真值估计；仅作为未来可选模式。

`TS-VERIFY-004` **MUST**：验证模式与 `selection_policy`/`aggregation_policy` 分离。`first_verified_bundle`、`replicated_equivalence`、quorum 和概率阈值回答的是“如何从候选中形成正式决定”，不能反过来定义候选本身为何正确。若采用 quorum，策略必须显式定义：

- `min_quorum`；
- 最大成功/错误/总 attempt 数；
- equivalence relation 或比较器版本；
- 不一致、无多数和耗尽预算后的终态；
- 同一 executor 是否允许计入多个独立票。

`TS-VERIFY-005` **MUST**：存在可接受成本的强 checker 时，V1 必须优先使用 `deterministic_exact` 或 `proof_checked`。多次返回值一致只能作为故障检测、可用性或附加审计证据，不能取代领域 checker。两个相同的错误答案不因一致而正确，一个通过完备 checker 的候选也不需要凑够多数票。

`TS-VERIFY-006` **MUST**：控制面共识与结果验证分离。Raft/PBFT 类机制最多证明多个协调器对同一事件顺序或确定性状态机执行达成一致；它们不证明 executor 提交的数学答案、Lean proof 或自然语言结论真实。

`TS-VERIFY-007` **MAY**：`latent_label_estimation` 只能用于满足下列前提的弱验证插件：候选先被转换为有限离散标签；同一批 item 有足够的交叠观察；观察者身份稳定；所选模型要求的条件独立性、item-observer interaction、类别空间和 classifier calibration 等假设被记录并接受。运行还必须持久化模型版本、类别先验、初始化、迭代/收敛信息、后验概率和不确定性。

`TS-VERIFY-008` **MUST**：不得把同一基础模型、相同 prompt、共享上下文或互相复制的多个 AI submission 默认计为独立观察者。若弱验证策略依赖独立性，必须记录用于支持独立性的 executor/model/prompt/tool provenance；无法支持时应降低置信度或拒绝使用该聚合策略。

`TS-VERIFY-009` **MUST**：未来启用统计弱验证时必须按数据生成过程选择并版本化算法，而不是提供一个含糊的“AI 共识分数”：

- Dawid-Skene 适用于离散类别和 observer confusion matrix；
- GLAD 只在 item difficulty × annotator ability 的交互假设和标签空间可接受时使用；
- MACE 只在“非 spam 时给出正确标签、spam strategy 与真值独立”等模型假设可接受时使用；
- CROWDLAB 还要求可用的 out-of-sample classifier probability 与校准/泛化证据。

这些算法的输出是带假设的 posterior/quality estimate，不是证明，也不能覆盖已有强 checker 的结论。

`TS-VERIFY-010` **MUST**：annotator ability、competence、quality score 和 item difficulty 必须限定到具体数据集/run、任务域、模型版本和样本窗口。它们不得直接提升为跨任务永久信誉，也不得未经独立结算政策审查就改变 token 奖励。

`TS-VERIFY-011` **MUST**：`proof_checked` validation 必须引用精确 `EnvironmentRef`、checker version 和 proof input digest。若无法重建与原验证等价的 namespace/import/library/fixture 环境，只能报告环境不可用或产生新的审计验证记录，不得静默换环境后覆盖原结论。

### 10.3 原子选择正式输出

`TS-CANON-001` **MUST**：task 的正式输出以 **canonical bundle** 为单位一次选择。多输出 task 不得只提交其中一部分后解锁后继。

`TS-CANON-002` **MUST**：canonical selection 满足以下不变量：

1. 每个 task 至多存在一个有效 selection。
2. selection 引用的所有 artifact 已通过所需验证。
3. selection 与 task 的 output-port 集合完整匹配。
4. selection、task 状态转换和后继就绪更新在同一事务语义中完成。
5. 重放相同决定不会产生第二次 canonical 或重复解锁。

`TS-CANON-003` **MUST**：一旦 canonical bundle 被正式依赖消费，不能被普通重试替换。若后来发现错误，必须通过显式 invalidation/compensation 协议产生新事件和影响分析，而不是原地覆盖。

`TS-CANON-004` **MUST**：合法但未胜出的结果标记为 `superseded` 或等价状态，保留用于审计和指标，不得进入正常完成结算。

`TS-CANON-005` **SHOULD**：canonical 选择策略与领域验证策略分离。验证器回答“是否有效/是否等价”，selection policy 回答“在有效候选中选哪个”。

### 10.4 最小验收条件

1. 两个并发 attempt 都产生有效结果时，只能成功创建一个 canonical selection。
2. canonical bundle 有两个端口而只提交一个时，事务失败且后继不解锁。
3. quorum 未满足时，即使某个候选结构合法，task 也不完成。
4. canonical 之后到达的有效迟到结果仅进入审计记录。
5. factorization 和 Lean stub 各自只凭一个通过固定领域 checker 的候选即可进入 selection，不要求重复答案一致。
6. structured report 的 schema/引用检查通过时，报告只能标记为“通过有界结构验证”，不得记录为“语义真值已证明”。
7. 使用错误或不同版本 verifier environment 检查 Lean stub 时，validation 明确失败或报告环境不匹配，不能归因于 proof 本身。
8. 未来统计弱验证报告必须能回答使用了 Dawid-Skene、GLAD、MACE 还是 CROWDLAB，以及该模型的输入前提是否满足。

来源：P01 的 execution selection；P04 的 first accepted/atomic rename；P05 的 application-specific validation、quorum 与 canonical result；P10/P11 的确定性复制状态机前提和控制面顺序保证；P12 的 latent class/observer error-rate 模型；P13 的 item difficulty/annotator ability；P14 的 spam/competence 模型；P15 的 classifier + annotator quality ensemble；P19 的 environment-correct proof checking。

## 11. 调度、能力匹配和慢任务处理

### 11.1 调度输入

`TS-SCHED-001` **MUST**：调度器只选择满足 hard requirements 的 executor。匹配至少考虑：

- plugin/task kind/version；
- 必需运行环境和依赖；
- CPU、内存、磁盘等资源下限；
- 输入可访问性或数据位置限制；
- deadline 与 executor 可用窗口；
- 故障模拟配置是否被允许。

`TS-SCHED-002` **SHOULD**：在满足硬约束的候选中，根据 hints、估计完成时间、数据局部性、历史成功率和当前负载排序。排序依据和最终选择理由应事件化或以确定性输入重建。

`TS-SCHED-003` **MUST**：调度器授予的是 lease，不是把 task 所有权永久转移给 executor。

### 11.2 慢任务与备份执行

`TS-SCHED-004` **SHOULD**：支持可配置的 `shadow attempt`，仅在满足触发条件时为同一 task 发放第二个并行 lease，例如：

- 运行时长超过同类任务分位数；
- 即将错过 deadline；
- executor heartbeat/进度异常；
- task 位于关键路径且备份成本可接受。

`TS-SCHED-005` **MUST**：shadow attempt 与普通重试使用相同的候选隔离、验证和 canonical 规则。不能因为它是备份任务就绕过验证。

`TS-SCHED-006` **MUST**：一个 attempt 胜出后，其他进行中的 attempt 可以被取消；如果无法取消，其结果按 losing/late submission 处理。取消不能删除已经发生的事件。

`TS-SCHED-007` **SHOULD**：记录 shadow attempt 的收益与代价：是否缩短关键路径、额外 work、胜出率和浪费资源。没有这些指标，实验无法判断推测执行是否值得。

### 11.3 两级调度与可解释分派

`TS-SCHED-008` **MAY**：未来把调度分为两级：协议核心/资源层给出可用 executor/resource candidates，插件级 scheduler 根据任务语义选择。V1 不需要实现 Mesos 式完整 resource-offer 循环，但接口设计不得迫使核心理解插件领域语义。

`TS-SCHED-009` **MUST**：V1 分派采用显式、可重放的最小 pipeline：

```text
ready task
  -> hard eligibility/capability filter
  -> policy ranking
  -> deterministic tie-break
  -> AllocationDecision
  -> LeaseIssued
```

`AllocationDecision` 至少记录候选集合摘要、被过滤原因、ranking policy/version、选中 executor、capability snapshot/digest 和 tie-break 输入。该决定只证明“为什么把任务交给此 executor”，不证明其未来结果正确。

`TS-SCHED-010` **SHOULD**：没有 executor 被选中时，记录结构化原因，例如 `all_busy`、`ineligible`、`unsupported_task_kind`、`environment_unavailable`、`policy_deferred` 或 `no_registered_executor`。恢复策略依据原因选择等待、重试、放宽 soft hint 或失败；不得把所有 no-match 都压成无信息的空结果。

`TS-SCHED-011` **MAY**：未来远端多执行者 runtime 可以实现 Contract Net 式 announcement/bid/award/refusal，但 V1 对本地 registry 使用 directed assignment 即可。任何 bidding/ranking 函数仍是版本化调度政策，P22 不提供可直接照搬的通用最优评分公式。

### 11.4 最小验收条件

1. 只有不满足 hint 的 executor 仍可执行；不满足 requirement 的 executor 永不获得 lease。
2. slow 故障触发 shadow attempt 时，两个候选都不能直接解锁下游。
3. shadow attempt 胜出和原 attempt 胜出两种情况均只产生一个 canonical bundle。
4. 能解释一次调度决定使用了哪些能力快照和排序输入。
5. 没有可用 executor 时，事件能区分“全部忙”“能力不匹配”“缺少环境”和“政策暂缓”。
6. 给定相同 ready queue、capability snapshot 和 policy version，V1 deterministic tie-break 选择相同 executor。

来源：P01 的 placement constraints/preferences；P03 的关键路径和 work-stealing 调度原则；P04 的 backup tasks；P05 的异构资源与 deadline；P07 的薄资源层、offer 和 executor 模型；P09 的异构 task、future、`wait` 和分层调度；P22 的 eligibility、task abstraction、award/refusal 和 no-bid 原因。

### 11.5 可复现实验与故障模型

`TS-SIM-001` **MUST**：每次实验创建版本化 `ExperimentRun`，并绑定不可变的 `SimulationProfile` digest。profile 至少记录模拟时钟语义、随机种子、故障类型与触发条件、延迟参数、executor population、插件/执行器版本和 ProtocolConfig digest。

`TS-SIM-002` **MUST**：offline、slow、executor_error、invalid_output、late_submission 等故障由实验包装层注入。插件领域逻辑和协议状态机只接收故障造成的正常协议事实，不得出现“如果正在模拟则绕过规则”的分支。

`TS-SIM-003` **MUST**：相同代码、fixture、配置和种子的重复运行应产生可比较的场景与决定序列；如果调度或时间推进存在有意非确定性，必须持久化实际随机选择和触发事件，而不能只保存 seed 后在 replay 时重新抽样。

`TS-SIM-004` **MUST**：state replay 重建的是某次实验已经发生的协议历史，不重新运行故障模型。重新模拟必须创建新的 `ExperimentRun`/run identity。

`TS-SIM-005` **SHOULD**：实验报告区分协议语义结论与性能模型结论。V1 本地 tick、mock delay 和 fault profile 可以验证恢复不变量，但不得外推为真实网络吞吐、生产延迟或 Byzantine 容错能力。

`TS-SIM-006` **SHOULD**：故障 profile 采用插件式/组合式边界，允许加入新场景而不修改协议核心；但 V1 不需要引入 SimGrid runtime 或构建完整网络、CPU、I/O 离散事件模型。

`TS-SIM-007` **MUST**：使用 fixture 或 benchmark 的实验必须记录 `fixture_set_id`/`benchmark_id`、版本、所选 case/problem IDs、split/分层规则、来源 digest 和对应 `EnvironmentRef`。synthetic fixture 也必须版本化；若采用 miniF2F 等外部 benchmark，不得只保存论文名或模糊的“使用某子集”。

最小验收条件：

1. 使用相同 profile/fixture/seed 重跑同一实验，故障触发位置和参数一致。
2. replay 期间 simulation wrapper 调用计数为零。
3. 故障注入开关变化不改变无故障路径的协议规则。
4. 报告明确标注 simulated tick，不把它称为真实秒或生产性能。
5. Lean stub 报告可精确列出 fixture set/version/case IDs；同名 fixture 内容变化会产生新 digest。

来源：P08 的可控、可观测、可重复 simulation，以及准确性、规模、扩展性和可用性之间的显式取舍；P09 的模拟与异构 AI workload 背景；P20 的 versioned benchmark、split 和问题覆盖；P21 的 library/toolchain 版本演进背景。

## 12. 事件、artifact 与确定性重放

### 12.1 事件是协议事实，不是调试日志

`TS-REPLAY-001` **MUST**：所有影响 authoritative state 的决定由 append-only event 表达。SQLite 当前状态可以作为投影和索引，但不能成为唯一历史来源。

至少需要覆盖以下事件族：

- task 创建、依赖绑定、ready/block 状态变化；
- expansion proposal 记录、接受和拒绝；
- lease 发放、续期、过期、撤销；
- attempt 启动、进度、终止和 submission；
- tool action/observation 与 environment binding；
- artifact 注册与 output-port binding；
- validation 请求与结论；
- canonical selection 或无法达成 canonical；
- 下游解锁、任务合并和根任务完成；
- settlement 资格、记账、撤销/补偿；
- 故障注入和人工政策决定。

`TS-REPLAY-002` **MUST**：event 至少包含：

```json
{
  "event_id": "evt-...",
  "event_type": "AttemptSubmitted",
  "event_schema_version": "1",
  "aggregate_type": "task",
  "aggregate_id": "task-...",
  "aggregate_seq": 7,
  "recorded_at": "...",
  "causation_event_id": "evt-...",
  "correlation_id": "run-...",
  "actor": {},
  "payload": {},
  "payload_digest": "sha256:..."
}
```

`TS-REPLAY-003` **MUST**：同一 aggregate 的 sequence 单调且唯一；重复命令通过 idempotency key 或等价机制不产生重复状态效果。

`TS-REPLAY-004` **MUST**：artifact 不可变且按内容摘要校验。event 只保存稳定引用和必要元数据，不依赖“某路径当前恰好是什么内容”。

`TS-REPLAY-005` **MUST**：非确定性输出完整持久化，包括 agent 拆分、executor 输出、外部 checker 结果和人工裁决。状态重放只重放已发生事实，不重新调用生成方。

`TS-REPLAY-006` **MUST**：恢复分为两种明确模式：

- `state replay`：从事件重建当时状态，禁止重新执行任务。
- `re-execution experiment`：作为新 run 使用旧输入重新执行，产生新的 run/correlation/attempt/artifact 身份。

二者不得混淆。

`TS-REPLAY-007` **SHOULD**：提供两类重放验证：

- 状态投影重建后与数据库快照一致；
- 审计报告能够沿 causation/lineage 解释根结果来自哪些 canonical artifact 和 attempt。

`TS-REPLAY-008` **MUST**：state replay 只恢复原有 validation 记录及其 `EnvironmentRef`，不重新运行 checker。audit verification 若要重新检查 proof/output，必须使用可证明等价的固定环境并生成新的 audit report/verification identity；新结果不得静默覆盖历史 validation。

`TS-REPLAY-009` **MUST**：审计可要求 action、observation、tool/version、输入输出 digest 和 raw submission，但不得要求 executor 在 replay 时重新生成隐藏 reasoning trace。缺少 chain-of-thought 不是重放失败；缺少会影响外部动作或正式结果的 tool observation 才是 provenance 缺口。

### 12.2 控制面日志的一致性边界

`TS-CONTROL-001` **MUST**：V1 明确采用单机单写者 event ledger，不宣称具备 Raft crash consensus 或 PBFT Byzantine consensus。全局 `event_seq`、hash chain 和幂等键是本地审计机制，不等价于多节点复制协议。

`TS-CONTROL-002` **MUST**：投影只能按已持久化的 authoritative event 顺序应用。对 V1 而言，“append 成功”是本地提交边界；SQLite/materialized view 的应用进度可以落后并重建，不能让尚未进入 ledger 的内存决定先成为权威状态。

`TS-CONTROL-003` **MUST**：lease fencing token、assignment epoch 和未来 coordinator epoch 都只拒绝陈旧授权。它们可借鉴 Raft term/view 的单调新旧判断，但不得被描述为已经实现 leader election 或 quorum commit。

`TS-CONTROL-004` **MAY**：若未来出现多个协调器，复制/共识层应放在 `EventLedger` 的提交边界之下，对版本化协议命令排序；上层 task、plugin、verification、canonical 和 settlement 语义保持不变。迁移前必须另写威胁模型，区分 crash fault 与 Byzantine fault。

`TS-CONTROL-005` **MUST**：未来即使采用 Raft/PBFT，复制状态机操作仍必须是确定性的，非确定性 AI/executor/checker 输出必须先作为带 digest 的外部 artifact/命令事实固定后再排序。不能让各 replica 独立调用 AI 并期待共识算法消除差异。

`TS-CONTROL-006` **MUST**：PBFT 的 `3f+1` replica 与 prepare/commit quorum 只在其故障模型、独立失效、认证消息、确定性服务和 view-change/checkpoint 前提下成立。不得把该数字直接移植为“需要几个 AI verifier”或“多少相同答案就是真”的经验阈值。

### 12.3 最小验收条件

1. 删除可重建投影后，仅凭 event ledger 和 artifact store 可恢复 task graph、attempt、canonical 和 settlement 状态。
2. 重放期间 executor/agent 调用计数为零。
3. 同一 submission 重报不会产生第二个 artifact binding、selection 或 credit。
4. 任一根输出可追溯到输入、插件版本、attempt、validator 和 selection 决定。
5. event append 后、SQLite 投影前模拟进程中断，重启可由 ledger 补齐投影且不产生重复效果。
6. 文档和实验报告不会把单机 hash chain、结果 quorum 或 checker 通过误称为 Raft/PBFT 共识。
7. audit verification 使用不同 environment 时产生新记录并明确标注环境差异，不改变原 validation。
8. 工具执行路径可以在没有 chain-of-thought 的情况下完成 state replay 和 lineage audit。

来源：P01 的 deterministic re-execution 与 lineage；P02 的 immutable objects、deterministic naming/memoization；P04 的执行记录和提交语义；P09 的 GCS/lineage 与自动重执行边界；P10 的 replicated log、term、commit/apply 和 snapshot；P11 的 deterministic state-machine replication、view、checkpoint 与 Byzantine quorum 前提；P18 的 action/observation trajectory；P19 的 environment-correct checker interaction。

## 13. 合并与递归完成

### 13.1 规范要求

`TS-MERGE-001` **MUST**：合并是显式 task kind 或显式插件操作，不能由协议核心按“把子结果拼起来”硬编码。

`TS-MERGE-002` **MUST**：merge 只能读取具名、类型兼容、已 canonical 化的输入 artifact。

`TS-MERGE-003` **MUST**：merge 本身也产生 attempt、候选 artifact、validation 和 canonical selection，不能因为它处在图的上层就绕过协议。

`TS-MERGE-004` **MUST**：父任务通过子图委派 expected output 时，父任务完成条件是预期输出已解析到合法 canonical bundle，而不是“所有直接子任务状态为完成”。这允许共享子图、可选分支和多级 continuation。

`TS-MERGE-005` **SHOULD**：插件声明 merge 的代数或顺序属性，例如是否交换、结合、幂等，供调度和验证使用；协议核心不得自行假定这些性质。

`TS-MERGE-006` **SHOULD**：若下游消费后上游 canonical 被显式判定无效，系统应能计算受影响的 lineage closure，并把重算或人工处置作为新事件记录。

`TS-MERGE-007` **MUST**：decomposer 生成的“合并步骤”必须落为版本化 `MergePlan`/merge handler contract，声明 required slots、顺序/代数属性和输出验证要求。prompt 中的最后答案、`EOQ` 标记或 controller 停止条件只能结束一次 executor 内部程序，不能直接把父任务标记为协议完成。

### 13.2 最小验收条件

1. 三类实验使用各自插件 merge 逻辑，协议核心没有领域分支。
2. merge 输入中出现 candidate 或 superseded artifact 时执行请求被拒绝。
3. merge 失败可重试，旧候选保留且只选一个 canonical。
4. 根结果审计报告能展示完整递归 lineage，而不只展示最终文件。
5. decomposer 提前输出 `EOQ` 但 required merge slots 未覆盖时，父任务不得完成。

来源：P01 的 DAG/data channels；P02 的 expected output/continuation；P06 的 workflow typed dataflow；P16 的 decomposer/controller、具名中间结果和 merge sub-task。

## 14. 结算：必须晚于验证和正式选择

P05 的重要启示不是“复制 BOINC 的 credit 数字”，而是计算完成不等于贡献成立。TokenShare 的结算必须以协议确认的有效贡献为依据。

### 14.1 规范要求

`TS-SETTLE-001` **MUST**：正常完成奖励至少满足：

```text
attempt 合法
AND required validation 通过
AND 其输出进入 canonical bundle 或被结算政策明确认定为有效辅助贡献
AND 没有重复记账
```

`TS-SETTLE-002` **MUST**：下列结果默认不获得正常完成奖励：

- invalid output；
- executor error；
- 未被请求的重复工作；
- 过期 lease 的迟到结果；
- 未通过 quorum 的孤立候选；
- 已被 canonical selection 排除的普通 losing attempt。

研究实验可以为“有效但未胜出的验证副本”设置独立、较低且显式的验证贡献政策，但不得把它伪装为任务完成奖励。

`TS-SETTLE-003` **MUST**：结算引用 task、attempt、artifact、validation 和 canonical selection 的稳定 ID，并以 append-only entry 记录。不得只修改一个可变余额而不保留来源。

`TS-SETTLE-004` **MUST**：同一结算依据具有唯一 idempotency key，重放和重试不能重复记账。

`TS-SETTLE-005` **SHOULD**：将结算至少分成以下可解释组成：

- 执行贡献；
- 验证贡献；
- 拆分/扩图贡献；
- 合并贡献；
- 失败、迟到或浪费的非奖励记录。

`TS-SETTLE-006` **SHOULD**：根任务失败或上游结果后来被显式 invalidation 时，不直接删除旧 entry，而是追加 reversal/adjustment，并保留原决定和补偿原因。

`TS-SETTLE-007` **MAY**：未来把关键路径贡献、质量、稀缺能力或资源成本纳入定价。V1 只需 sandbox 记账和可解释证据，不实现真实 token 支付。

`TS-SETTLE-008` **MUST**：概率性 observer reliability、latent label posterior 或 AI judge agreement 不是永久信誉事实。若未来用于弱验证结算，必须绑定具体模型版本、样本窗口、任务域、假设和不确定性；不得影响 factorization/Lean 等已由强 checker 裁决的正确性奖励。

### 14.2 最小验收条件

1. 同一 canonical selection 重放多次只生成一次结算。
2. late submission 和 invalid output 均不获得正常完成奖励。
3. 采用 quorum 的任务可以区分 canonical 执行贡献与有效验证副本贡献。
4. 任一余额变化都能追溯到具体事件和验证证据。

来源：P05 的 validation、canonical result 与 credit；P04 的 winning execution 语义；P12-P15 的 observer ability/competence/quality estimate 只在给定模型、数据、任务域与假设下成立。

## 15. 关键跨模块不变量

以下不变量是候选规范的核心，优先级高于具体类名和数据库布局。

### 15.1 任务图与动态状态

`TS-INV-005`：动态扩图只能由协议核心接受，扩图决定原子、可去重、可重放且不得形成环。

`TS-INV-018`：只有稳定、结构化、可独立调度和验证的 subgoal/proof state 可晋升为 `TaskUnit`；临时 thought、LM 自评和 executor working memory 不得自动进入 authoritative graph。

### 15.2 执行、授权与恢复

`TS-INV-004`：过期或被 fencing 的 lease 不得改变 authoritative state。

`TS-INV-006`：插件领域逻辑不得进入协议核心；协议持久化和结算逻辑不得由执行器决定。

`TS-INV-011`：所有协议解释所依赖的 schema、plugin、validator、merge policy 和 executor contract 版本必须显式。

`TS-INV-012`：故障恢复不得覆盖或删除旧 attempt；恢复通过新授权、新执行或事件重放完成。

`TS-INV-016`：executor/actor 的可变内存不是协议事实；影响恢复和结算的状态必须 artifact/event 化。

`TS-INV-019`：tool action/observation 是执行 provenance；自由文本 reasoning trace 不是协议真值，也不是 replay 的必需输入。

`TS-INV-020`：调度/分派只决定谁获准尝试执行，不能替代 submission validation 或 canonical selection。

### 15.3 验证、正式输出与可信边界

`TS-INV-001`：只有 canonical artifact 能成为下游正式输入。

`TS-INV-002`：attempt 成功不等于 task 成功；task 成功必须经过验证和 canonical selection。

`TS-INV-003`：每个 task 至多有一个有效 canonical selection，selection 以完整 output bundle 为单位。

`TS-INV-009`：artifact 内容不可变；修正通过新 artifact、新事件和显式选择完成。

`TS-INV-013`：强领域 checker 优先于多次返回值一致性；一致性不是正确性证明。

`TS-INV-014`：控制面事件排序、task 结果验证和 canonical selection 是三个不同决定，不得共用“共识”一词掩盖边界。

`TS-INV-017`：proof/checker 结论必须绑定不可变验证环境身份；环境不等价时不得复用原 validation 结论。

`TS-INV-021`：统计 consensus、ability、competence 和 quality score 必须绑定模型假设、任务域、数据窗口和不确定性，不得直接变成永久信誉或强 checker 任务的正确性依据。

### 15.4 重放、审计、实验与结算

`TS-INV-007`：非确定性输出必须持久化，state replay 不得重新调用产生方。

`TS-INV-008`：任何正式结果、图变更和结算都能沿事件 causation 与 artifact lineage 解释。

`TS-INV-010`：结算晚于所需验证，并具有幂等依据。

`TS-INV-015`：实验 profile、随机决定和故障触发必须可复查；state replay 不得重新执行 simulation。

## 16. V1 最小实现切片

候选机制很多，但 V1 不应同时实现二十二篇论文的完整系统。按依赖关系，建议分为三个层级。

### 16.1 V1 核心闭环（MUST）

1. 版本化 `TaskUnit`、`TaskEdge`、`Lease`、`Attempt`、`Artifact`、`Validation`、`CanonicalSelection`、`EnvironmentRef`、`Event` 和 `SettlementEntry`。
2. 派生树与执行 DAG 分离，typed named artifact edges 和 canonical-only binding。
3. 插件 descriptor、统一 execution request/submission，以及协议核心、插件和执行器之间的权限边界。
4. 基于 capability eligibility、确定性 policy/tie-break 和 `AllocationDecision` 发放 lease；assignment 不越过验证边界。
5. lease expiry、fencing、重试、五类故障结果和迟到结果审计。
6. 私有候选输出、公共验证、插件验证和原子 canonical bundle；验证模式与选择策略分离，强 checker 任务不依赖结果多数。
7. environment-sensitive validation 绑定不可变 `EnvironmentRef`/digest；Lean stub 的 checker fixture 与 validation 使用同一环境身份。
8. append-only event ledger、不可变 artifact 和零执行 state replay。
9. factorization、Lean stub、structured report stub 三条完整实验链。
10. 验证后 sandbox settlement 和幂等记账。

### 16.2 V1 应实现的实验能力（SHOULD）

1. 结构化 `ExpansionProposal`、expected output 与动态扩图，并设置 subgoal/task promotion guard：只有结构化、可调度、可验证的中间状态能进入 authoritative graph。
2. hard requirements/hints、executor capability snapshot，以及 tool action/observation provenance；隐藏 reasoning trace 不作为 replay 前置条件。
3. `deterministic_exact`、`proof_checked`、`bounded_structural` verification mode，以及独立的 `first_verified_bundle` selection policy；仅保留 future aggregation extension point。
4. 可配置 shadow attempt 与收益/浪费指标。
5. work、critical path、retry work、wasted work 指标。
6. projection rebuild 与完整 lineage audit report。
7. 版本化 `ExperimentRun`/`SimulationProfile`、固定 seed/fixture 和故障触发事件，保证本地实验可重复且 replay 不重新模拟。

### 16.3 后续扩展（MAY）

1. 真正的 work-stealing scheduler，以及 Mesos 式 resource offer、多框架公平性、配额和抢占。
2. Contract Net 式 announcement/bid/award 分布式协商；V1 只做本地 deterministic directed assignment。
3. Ray 式分布式 task/actor runtime、远端 worker pool 和 P2P；actor 状态仍不得替代协议 artifact/event。
4. 容器化执行、完整 CWL 兼容层，以及 ReAct 式生产工具 agent、外部 API action space 和长驻交互 controller。
5. ToT 式 BFS/DFS/search policy、LM state evaluator 和 branch/prune runtime；只能作为版本化插件策略。
6. Dawid-Skene、GLAD、MACE、CROWDLAB 等 `latent_label_estimation`/semantic reliability 模型，仅用于满足各自数据与独立性前提的弱验证插件。
7. 真实 LeanDojo/premise retrieval/best-first proof search、完整 mathlib toolchain 和 miniF2F 实际 benchmark 子集；采用 benchmark 时固定版本、problem IDs、split 和环境，不写入协议核心。
8. 多协调器 Raft/PBFT 复制；采用前另行定义 crash/Byzantine 威胁模型。
9. BOINC 式长期信誉、自适应复制、反作弊与公共网络，以及生产身份和真实 token 结算。

## 17. 不应从论文照搬的设计

| 机制域 | 不照搬的设计 | 原因 | 候选替代 |
|---|---|---|---|
| 任务图与求解 | 把所有计算强制写成 Map/Reduce | TokenShare 有证明、报告和递归拆分，不具有统一 shuffle 语义 | 通用 typed task DAG，MapReduce 可作为未来插件 |
| 任务图与求解 | 让执行代码任意 spawn 并直接改图 | 破坏协议审计、权限和确定性恢复 | 结构化 proposal + 核心原子接受 |
| 任务图与求解 | V1 实现完整 work stealing | 本地 PoC 的瓶颈首先是语义正确性，不是 deque 性能 | 先记录 work/span 和 ready queue，保留调度接口 |
| 任务图与求解 | 把每个 ToT thought 或 ReAct thought 变成 `TaskUnit` | 会导致图爆炸、不可验证状态、结算放大和隐藏 reasoning 泄漏 | 只有稳定、typed、可验证 subgoal 晋升为任务；其余保留为 attempt artifact |
| 任务图与求解 | 把 LM value/vote 当领域 validation | 自评只能引导搜索，不能证明候选正确 | state evaluator 只排序候选；正式结果仍走插件 checker/有界验证 |
| 执行与调度 | V1 实现完整 resource-offer 市场 | 当前没有多租户分布式集群 | 先做 requirements/hints、capability 和 lease |
| 执行与调度 | V1 实现 Contract Net 广播和竞价 | 本地原型没有分布式通信与动态市场，论文也把 ranking 留给 task-specific procedure | capability filter + deterministic policy + directed lease + no-match reason |
| 执行与调度 | 用当前插件代码解释所有历史数据 | 插件升级会破坏 replay | 记录 descriptor/validator/merge policy 版本 |
| 执行与调度 | executor 成功就直接完成 task | 绕过验证、canonical 和迟到处理 | submission -> validation -> selection 三阶段 |
| 执行与调度 | 把 Ray actor 内存作为长期任务状态 | actor checkpoint/恢复和隐式可变状态会绕开 event/artifact 权威边界 | actor 仅作 executor 内部优化，协议相关状态显式提交 |
| 验证与控制面 | 默认对所有任务做多副本 quorum | 强 checker 任务会产生无意义成本 | 每 task kind 显式验证策略 |
| 验证与控制面 | 因为已有 JSONL 就声称实现 Raft | 单机顺序日志没有 leader、复制、majority commit 和 term safety | V1 明示 single-writer；未来在 ledger 提交边界下加复制层 |
| 验证与控制面 | 把 PBFT `3f+1`/`2f+1` 当 AI verifier committee 配方 | PBFT 处理确定性状态机排序并依赖独立 Byzantine replica 假设，不处理语义真值 | validator 依据任务域设计；委员会模型必须另证独立性、比较器和威胁模型 |
| 验证与控制面 | 对 factorization/Lean 使用 Dawid-Skene | 已有明确 checker 时潜变量估计更弱、更贵且可能误导 | 单候选强验证；Dawid-Skene 只留给未来离散弱验证任务 |
| 验证与控制面 | 把 GLAD/MACE/CROWDLAB 分数直接当自由文本正确率或永久贡献者信誉 | 三者依赖不同标签空间、交叠数据、独立性、spam 或 classifier calibration 假设 | 只在满足前提的版本化弱验证插件中输出带不确定性的局部估计 |
| 重放与审计 | 重放时重新调用 agent | 非确定性结果会改变，无法审计 | 持久化原始输出和决定，重放只应用事实 |
| 重放与审计 | 用 Ray lineage 在 replay 中自动重跑 AI | AI/工具环境可能非确定，重跑结果不是原历史事实 | state replay 零执行；repair 或新实验使用新身份 |
| 重放与审计 | 强制保存 chain-of-thought 才允许 replay | reasoning trace 不等于外部事实，也不应成为审计正确性的单点依赖 | 保存 action、observation、tool/version、raw output 和 artifact lineage |
| 实验与证明 | 为追求“真实感”直接引入完整 SimGrid | V1 要验证协议闭环，不评估生产分布式性能；模型校准成本会遮蔽研究目标 | 自建最小 `SimulationProfile`/wrapper，固定 seed、故障和 tick 语义 |
| 实验与证明 | Lean stub 直接实现 LeanDojo/DPR/真实 proof search | 会把协议 PoC 扩成 theorem-proving 项目 | 固定 fixture checker + environment digest；真实 adapter 留作后续插件 |
| 实验与证明 | 把 miniF2F 完整 benchmark 当 V1 必需依赖 | V1 研究目标是协议闭环而非证明成功率，真实 benchmark 会引入环境与数据维护成本 | 先用版本化 synthetic fixture；若后续采用实际子集则固定版本和 problem IDs |
| 实验与证明 | 因为 mathlib 论文讨论 Lean library 就声称其提出 environment hash/replay | P21 提供 library/版本背景，不给出 TokenShare 所需的环境绑定算法 | environment-correct checker 主要参考 P19；TokenShare 自行定义 `EnvironmentRef` 合同 |

## 18. 端到端参考流程

以下流程说明各机制如何组合，不规定具体函数名。

```text
0. Create ExperimentRun
   -> bind ProtocolConfig + SimulationProfile + fixture/seed/benchmark/environment digests
   -> append ExperimentStarted

1. Create root TaskUnit
   -> register typed expected outputs
   -> append TaskCreated

2. Calculate readiness
   -> all required input ports bound to canonical artifacts
   -> append TaskBecameReady

3. Match executor
   -> filter hard requirements
   -> rank by hints/capability/deadline
   -> record AllocationDecision or structured no-match reason
   -> create Lease + fencing token
   -> append LeaseIssued

4. Execute privately
   -> create Attempt
   -> executor writes candidate artifacts
   -> tool executors persist action/observation provenance; hidden reasoning is not protocol truth
   -> executor submits port bindings and optional ExpansionProposal
   -> append AttemptSubmitted

5. Validate
   -> common protocol checks
   -> verify request/submission/checker EnvironmentRef agreement
   -> plugin-declared deterministic/proof/structural checks
   -> only weak-verification tasks may invoke versioned aggregation
   -> append ValidationCompleted

6. Decide
   -> atomically select complete canonical bundle
   -> append CanonicalSelected
   -> losing/late attempts remain audit-only

7. Expand or continue
   -> validate structured graph proposal
   -> atomically accept tasks/edges/expected outputs
   -> append GraphExpansionAccepted
   -> calculate newly ready tasks

8. Merge recursively
   -> merge task consumes canonical child artifacts
   -> goes through the same lease/attempt/validation/selection path

9. Settle
   -> derive eligible contributions from accepted evidence
   -> append idempotent SettlementEntries

10. Replay and audit
    -> rebuild projections without executor calls
    -> do not rerun simulation or resample random choices
    -> trace root artifact back through canonical selections and attempts
```

## 19. 冻结声明

截至 `candidate-baseline-v3`，第一部分形成一套统一候选架构：TokenShare 以“动态 typed task DAG + 私有多 attempt + 分层验证 + 唯一 canonical bundle + immutable artifacts + append-only events + 验证后结算”为协议骨架；递归拆分通过受控扩图表达，执行授权通过 lease/fencing 表达，分派通过 capability filter 和可解释的 `AllocationDecision` 表达，领域行为与搜索策略通过版本化插件表达。验证结论必须与选择决定、控制面排序和统计估计相互分离；proof-like 结果绑定精确环境，工具型执行保留 action/observation provenance，可复现实验固定 profile、fixture 和实际随机决定。真实分布式协商、长期信誉、通用搜索 runtime 和完整 Lean toolchain 均保持为后续扩展。

第一部分在进入现有设计比较前冻结。第二部分只能报告现有设计与这些候选要求的关系，不得静默改变上述要求。

## 第二部分：与现有主设计的对照

本部分保留 2026-06-22 整合前的对照快照，用来解释当时为什么需要修改主 TDD。2026-06-23
之后，主 TDD 已按第 24 到 26 节的推荐取舍完成整合；本部分中的“尚未覆盖”“偏差”“需裁决”
等表述不再代表当前主 TDD 的实时差距。

## 20. 对照范围与状态定义

第一部分完成 P01-P22 机制融合并冻结后，第二部分读取并对照以下现有材料：

- `2026-06-03-tokenshare-protocol-technical-design.md`：当前主 TDD。
- `2026-06-05-phase-1-minimal-object-field-spec.md`：已落地的基础对象、artifact 和 event envelope 规格。
- `2026-06-08-phase-2-minimal-field-state-event-spec.md`：任务图、lease、attempt、调度和恢复的细化规格。
- `2026-06-04-tokenshare-paper-module-map.md`：论文机制到项目模块的既有映射，用于检查概念误用。
- `feature_list.json`：当前阶段状态，用来区分“文档已有”与“代码已实现”。

本对照使用以下状态：

- **一致**：现有设计已明确表达候选机制的关键语义。
- **部分覆盖**：方向一致，但缺少字段、不变量、失败语义或验收标准。
- **遗漏**：现有主设计没有表达该机制。
- **偏差**：两者作出了不同选择，必须明确裁决。
- **主动延期**：现有主设计明确把机制放到 V1 以后，并非无意遗漏。

本节比较的是规范覆盖度，不等同于代码实现度。`feature_list.json` 显示 Phase 1、Phase 2 已完成，Phase 3 正在进行，Phase 4 至 Phase 7 尚未开始；因此主 TDD 中关于验证、canonical、扩图、合并、结算和完整重放的多数内容仍是目标设计，不应被误读为当前代码已有能力。

## 21. 总体结论

现有主 TDD 与独立候选规范在协议骨架上**高度同向**，不是两套互相竞争的架构。以下关键原则已经完整或接近完整地出现在主 TDD 中：

- 拆分树和执行依赖 DAG 分离。
- Ready 依赖 named canonical outputs，而不是父节点执行状态。
- 协议核心、任务插件、执行器三层边界。
- executor 只能提交候选，不能改图、绑定正式输出或结算。
- `TaskUnit`、`Lease`、`Attempt` 分离，过期 lease 使用 fencing 隔离。
- 通用验证后调用插件领域验证。
- `first_verified_bundle` 原子绑定唯一正式输出束。
- 结构化拆分提案经核心校验后原子扩图。
- immutable artifact、append-only event、状态重放不重新调用 AI。
- merge 依据固定 `MergePlan` 和 child canonical hashes。
- 迟到、无效和未请求重复结果默认不奖励，结算要求幂等。
- 已规划 `SimulationProfile`、故障包装层、实验 runner 和 metrics，方向符合 P08 的可重复实验方法。
- 已把 Ray 仅作为未来 runtime 参照，V1 不依赖 Ray，AI 原始输出必须先持久化为 artifact。
- 已采用单机 append-only JSONL 权威日志和可重建投影，没有误把 V1 描述成多协调器共识系统。
- 已有 `Attempt.environment_summary`、固定 Lean stub 环境摘要和版本化 checker 方向，为 environment-bound validation 提供了基础。
- Phase 2 scheduler 已经采用 capability filter + FIFO direct assignment，没有引入不必要的广播或竞价网络。

差异主要出现在协议语义是否足够严格，而不是组件名称。按机制归纳如下。

### 21.1 任务图与完成语义

- 主 TDD 已支持动态扩图，但没有 CIEL 式 expected output/future 委派语义，也没有为 child task、edge 和 expected output 规定确定性派生 ID。
- decomposer/handler 边界与现有插件分层一致，但尚未规定只有 typed、可独立调度且可验证的中间状态才能晋升为 `TaskUnit`；缺少该门槛会让搜索 thought 或 executor working memory 越过协议边界。
- `MergePlan` 已能约束 child slots，但 merge 是否必须走普通 task 的 lease、attempt、validation 和 canonical 生命周期仍不明确，也缺少 expected output 到子图最终输出的显式 resolution。

### 21.2 执行契约与分派

- 能力匹配尚未区分 CWL 式 hard requirements 与 soft hints，也没有在 lease/decision 中固定 capability snapshot、freshness 或 epoch。
- execution request、submission 与 validation 只有自由结构的 `environment_summary`，尚未共享不可变 `EnvironmentRef`/digest；这会使 proof/checker 结论在环境漂移后失去可比性。
- execution log 已规划 raw/parsed output 和 log artifact，但尚未把 action、observation、tool/version 与自由文本 reasoning 分成不同证据等级。
- Phase 2 已采用 capability filter 和直接指派，但 `AllocationDecision` 尚未完整记录候选、过滤原因、policy/tie-break 输入和 no-match reason；完整 Contract Net announcement/bid 不符合 V1 本地边界。

### 21.3 验证、选择与 proof-like 实验

- 主 TDD 有领域验证和 `first_verified_bundle`，但还没有把 `deterministic_exact`、`proof_checked`、`bounded_structural`、未来统计估计与 selection/aggregation policy 系统分开。
- canonical event 已存在，但 selection identity、完整 validation evidence、失效和补偿语义尚未固化。
- factorization 与 Lean stub 应坚持强 checker 优先；Dawid-Skene、GLAD、MACE、CROWDLAB 只能作为满足各自离散标签、交叠观察、difficulty/spam/classifier 等前提的未来插件，不能合并成通用 semantic score 或永久信誉。
- Lean stub 已有固定 checker/fixture 方向，但 benchmark、problem IDs、split 和环境身份仍需统一版本化。miniF2F 可指导 fixture 设计而不是成为 V1 必需依赖；mathlib 只提供 library/version 背景，environment-correct checking 的直接依据应归于 LeanDojo。

### 21.4 重放、控制面与实验有效性

- 主 TDD 允许丢失的确定性 artifact 按来源重执行，候选规范则要求 state replay 绝不执行计算；Ray lineage 也不能消除这一冲突，因为重新计算必须成为新 repair 或 experiment 事实。
- `SimulationProfile` 方向正确，但尚未固定 seed、模拟时钟、实际随机决定、故障触发、fixture identity 和“replay 不重新模拟”的验收条件。
- shadow attempt 已存在，但缺少 Cilk 式 work/span/critical-path、额外 work 和 wasted work 指标，因而无法判断推测执行是否真正改善关键路径。
- Raft/PBFT 只约束未来多协调器的事件排序，不验证 executor 答案；单机 hash chain、结果 quorum 与 checker 通过都不得被描述为控制面共识。

## 22. 分机制覆盖矩阵

### 22.1 对象、图和动态展开

| 候选要求 | 状态 | 现有设计证据 | 偏差或遗漏 |
|---|---|---|---|
| `TS-OBJ-001` 至 `003` | 部分覆盖 | 主 TDD 6.1-6.3 已有 task、lease、attempt、artifact、verification、contribution、settlement、event 等对象；阶段规格普遍含 `schema_version` | 缺少独立 `CanonicalSelection` identity；validator、merge policy 版本在所有相关对象中的传播未形成统一不变量 |
| `TS-OBJ-005` | 部分覆盖 | `Attempt.environment_summary` 和 Lean stub 固定环境摘要已存在 | 没有不可变 `EnvironmentRef`/digest 的最小 schema，也没有 request/submission/validation 一致性检查 |
| `TS-GRAPH-001`、`002`、`004`、`005` | 一致 | 主 TDD 5.2、6.1、9.1、15.1；Phase 2 `TaskGraph` 明确 decomposition/dependency、canonical-ready 和无环 | 无实质偏差 |
| `TS-GRAPH-003` | 部分覆盖 | `TaskRelation` 有 source output 和 target input；`TaskSchema` 负责 schema | 边本身没有 artifact type/schema snapshot；类型究竟属于端口定义还是边需要裁决 |
| `TS-GRAPH-006` | 遗漏 | 主 TDD 指标有节点数、深度、重复工作比和完成时间 | 没有 work、span/critical path、ready width、retry work、wasted work 的正式定义 |
| P09 动态 task/future 边界 | 一致或部分覆盖 | 主 TDD 已有结果驱动动态扩图、named artifact、ready calculation 和 future-like `ArtifactRef` | 没有 Ray 式大规模 runtime 需求；这是符合 V1 目标的主动收缩，不应因此引入 Ray 依赖 |
| `TS-EXPAND-001`、`002`、`004`、`006`、`007` | 一致或部分覆盖 | 主 TDD 有 `DecompositionProposal`、`ExpansionDecision`、`ExpansionCoordinator`、图限制和原子 `TASK_EXPANDED` | proposal 最小字段、插件可生成 task/artifact 类型白名单仍需 Phase 4 固化 |
| `TS-EXPAND-003` | 遗漏 | 现有设计有 idempotency key 和 proposal artifact | 没有 child task、edge、expected output 的确定性 ID 派生规则和 proposal digest 规则 |
| `TS-EXPAND-005` | 遗漏 | 现有设计使用 `MergePlan` 和 required outputs | 没有“任务自己发布 expected output，或显式委派给子图”的 future resolution 语义 |
| `TS-EXPAND-008` | 部分覆盖 | event ledger 和 expansion decision 使用幂等键 | 未定义逻辑等价 proposal 的 canonical form，也未规定重复提案如何返回同一子图 |
| `TS-EXPAND-009`、`010` | 部分覆盖 | `DecompositionProposal` 已包含子任务、依赖、required outputs 和插件来源；插件负责领域拆分 | 尚未要求每个 child 绑定 handler/version/typed ports，也未明示 thought/working memory 不得自动成为 `TaskUnit` |
| `TS-EXPAND-011` | 主动延期 | V1 没有 ToT/search runtime 目标 | 延期合理；需要裁决是否在 V1 schema 中预留 search policy/budget，还是完全留在未来插件内部 |

### 22.2 插件、执行器和执行身份

| 候选要求 | 状态 | 现有设计证据 | 偏差或遗漏 |
|---|---|---|---|
| `TS-PLUGIN-001`、`002`、`004`、`005` | 部分覆盖 | 主 TDD 6.2、8.2、Phase 3 计划已有 descriptor、schema、version 和只返回提案的边界 | descriptor 的 validator/merge policy ID 与版本传播未细化；typed ports 主要隐含在 `TaskSchema` 中 |
| `TS-PLUGIN-003` | 遗漏 | 现有 `required_capabilities` 只表达硬条件 | 没有 requirements 与 hints 的规范二分及未满足 hint 的审计语义 |
| `TS-EXEC-001` 至 `003` | 一致或部分覆盖 | 主 TDD 8.3 已定义统一 request/submission 和 executor 禁止事项 | request 的 required output ports、参数 digest、deadline/fault profile 仍需 Phase 3 字段规格固化 |
| `TS-EXEC-004` | 部分覆盖 | `ClientRecord` 有 executor version、capabilities、status、last_seen | 没有 capability epoch/freshness；lease 没有不可变 capability snapshot/digest |
| `TS-EXEC-005` | 一致 | 主 TDD 将远程服务和人类 worker 放到后续版本 | 无冲突 |
| `TS-EXEC-006`、`007` | 部分覆盖 | AI 原始输出必须 artifact 化；JSONL/ArtifactStore/SQLite 职责分离；Ray 仅作未来 runtime 参照 | 尚未明确 actor/session 可变状态不得成为协议事实，也未把控制状态、scheduler 和 artifact location 的接口边界写成 Phase 3 不变量 |
| `TS-EXEC-008` | 部分覆盖 | `ExecutionRequest` 目标字段包含环境摘要，`Attempt` 已有 `environment_summary` | 缺少环境 contract digest/ref、submission 回显和 validator 一致性拒绝规则；P19 表明这会直接影响 proof correctness 判断 |
| `TS-EXEC-009`、`010` | 部分覆盖/主动延期 | AI raw output 和 log artifact 边界已规划，生产工具 agent 不在 V1 | 应把 execution log 收紧为 action/observation/tool provenance，并明示 hidden reasoning 非 replay 必需；ReAct controller 延期合理 |
| `TS-ATTEMPT-001` | 偏差 | Phase 2 明确一个 lease 绑定一个 attempt，且 `Lease` 直接含 `attempt_id` | 候选基线允许一个 lease 对多个 attempt；两种基数不能同时作为规范 |
| `TS-ATTEMPT-002` 至 `006` | 一致或部分覆盖 | Phase 2 有 TTL、fencing、lease kind、attempt kind、失败类型和完整状态机 | executor capability snapshot 和 submission 审计政策未写入 lease 最小字段 |
| `TS-ATTEMPT-007`、`008` | 部分覆盖 | 主 TDD 区分 candidate/canonical artifact 并要求内容 hash | 没有 candidate 私有命名空间、临时文件到完整 artifact 的原子 publish/staging 规则 |

### 22.3 验证与正式输出

| 候选要求 | 状态 | 现有设计证据 | 偏差或遗漏 |
|---|---|---|---|
| `TS-VERIFY-001`、`002` | 一致 | `VerificationOrchestrator`、`VerificationResult`、`VerificationReport` 已区分公共检查和插件验证，并记录验证器信息 | Phase 4 仍需把 reason code、证据引用和输入 digest 变成最小字段 |
| `TS-VERIFY-003` 至 `006` | 主体遗漏、部分主动延期 | V1 有领域验证器和 `first_verified_bundle`；主 TDD 3.3 把 `quorum_consensus` 放在后续版本 | 没有把 `deterministic_exact`、`proof_checked`、`bounded_structural` 与 selection/aggregation policy 分开，也没有明确控制面共识不验证答案 |
| `TS-VERIFY-007`、`008` | 主动延期且边界未明示 | structured report 已声明弱验证，生产 AI/人类 worker 和完整 quorum 均不在 V1 | 延期合理；但应明确 P12-P15 模型只适用于各自声明的离散标签、交叠观察、独立性、difficulty/spam/classifier 等前提，不适用于强 checker 或直接评价自由文本 |
| `TS-VERIFY-009`、`010` | 主动延期且需要模型边界 | 主 TDD 没有实现 GLAD/MACE/CROWDLAB，符合 V1 数据与范围限制 | extension point 尚未区分 difficulty-aware、spam-aware、classifier-assisted 三类模型，也未禁止把局部 score 升格为永久信誉/结算权重 |
| `TS-VERIFY-011` | 部分覆盖 | Lean stub 目标包含固定环境摘要、版本化 fixture checker 和 proof artifact | validation 未绑定精确 environment identity；audit verification 换环境后的新记录/不覆盖规则未写明 |
| `TS-CANON-001`、`002` | 一致 | 主 TDD 明确正式输出束只能绑定一次，`CANONICAL_OUTPUTS_BOUND` 原子记录 | 应在 Phase 4 事件 payload 中补 selection ID、policy ID、完整端口集合和 input validation IDs |
| `TS-CANON-003` | 部分覆盖 | artifact 不可变、正式输出唯一 | 没有 canonical 被下游消费后的 invalidation/compensation 协议 |
| `TS-CANON-004` | 一致 | attempt 有 `Superseded`，迟到结果仅审计 | 无实质偏差 |
| `TS-CANON-005` | 部分覆盖 | 插件验证与 `first_verified_bundle` 已概念分离 | 尚未形成通用 selection policy interface，当前只有一个策略字符串 |

### 22.4 调度与慢任务

| 候选要求 | 状态 | 现有设计证据 | 偏差或遗漏 |
|---|---|---|---|
| `TS-SCHED-001`、`003` | 一致 | Phase 2 过滤硬能力并创建 lease/attempt | 资源下限、数据位置和 deadline feasibility 的统一 matcher 仍待后续字段扩展 |
| `TS-SCHED-002` | 部分覆盖 | 调度事件记录 matched capabilities、policy、reason 和 input summary | 没有 hints、数据局部性、历史成功率及 capability snapshot 的统一排序模型 |
| `TS-SCHED-004` 至 `006` | 部分覆盖 | 主 TDD 支持 `shadow_after`、shadow attempt、胜出后 supersede 和迟到隔离 | 触发只基于固定时间，没有同类分位数、关键路径或 heartbeat 进度策略 |
| `TS-SCHED-007` | 部分覆盖 | 有 duplicate work ratio 和 attempt count | 没有 shadow 胜出率、关键路径收益、额外 work 与 wasted work |
| `TS-SCHED-008` | 主动延期 | V1 不做 Mesos 式集群资源市场和多租户调度 | 这是合理的 V1 延期，不是阻塞缺口 |
| `TS-SCHED-009`、`010` | 部分覆盖 | Phase 2 已做 capability filter、FIFO policy、SchedulingDecision 和 lease | decision 没有完整候选/过滤摘要与 capability digest；无匹配时缺少 busy/ineligible/environment/policy 等结构化原因 |
| `TS-SCHED-011` | 主动延期 | V1 明确是本地 runtime，不做 HTTP worker pool/P2P | Contract Net 完整 announcement/bid 延期正确；Phase 3 仍需明确采用 deterministic directed assignment 而非隐含竞价 |
| `TS-SIM-001` 至 `007` | 部分覆盖 | 主 TDD 已有 `SimulationProfile`、`SimulationWrapper`、五类故障、tick 指标、fixture 和实验报告 | 缺少 profile 最小字段、seed/实际随机决定、模拟时钟语义、replay 零模拟调用、fixture/benchmark case identity 和“不得外推生产性能”的验收要求 |

### 22.5 重放、合并和结算

| 候选要求 | 状态 | 现有设计证据 | 偏差或遗漏 |
|---|---|---|---|
| `TS-REPLAY-001`、`004`、`005`、`007` | 一致或部分覆盖 | JSONL 是权威事实源；artifact 带 hash；AI 输出必须持久化；状态/审计重放分离 | lineage audit report 的固定输出格式未定义 |
| `TS-REPLAY-002`、`003` | 部分覆盖 | Phase 1 event 有全局 `event_seq`、event hash chain、correlation、causation 和 idempotency key | 候选要求 per-aggregate sequence 和 payload digest；现有设计使用全局序号与 event hash，需决定是否已足够 |
| `TS-REPLAY-006` | 偏差 | 主 TDD 10.2 允许确定性程序 artifact 丢失时按来源重执行 | 候选要求 state replay 绝不执行任务；重新计算必须成为新 run/repair 事实 |
| `TS-REPLAY-008`、`009` | 部分覆盖 | 状态重放与审计重放已分离，AI raw output/log 需要 artifact 化 | 未规定 audit re-verification 的环境等价、新 validation identity，也未明示 replay 只依赖 action/observation provenance 而非 chain-of-thought |
| `TS-CONTROL-001` 至 `003` | 一致或部分覆盖 | V1 是单机 JSONL 单写者、SQLite 可重建投影，并已有 fencing token 和全局 event sequence | 应明确本地 append/投影提交边界，以及这些机制不等价于 Raft term、leader 或 quorum commit |
| `TS-CONTROL-004` 至 `006` | 主动延期；旧映射冲突已纠正 | 主 TDD 明确不做真实分布式网络和生产 Byzantine 容错；论文映射已将 PBFT 改为未来控制面复制参考 | 延期正确；后续仍需防止把确定性控制面 quorum 重新解释为 AI/语义答案验证 |
| `TS-MERGE-001`、`002` | 一致 | `MergeCoordinator` 和插件 `MergeRule` 读取 canonical child outputs | 无实质偏差 |
| `TS-MERGE-003` | 表达不清 | 主 TDD 保存 `MergeRecord` 和 merge output，但没有明确 merge 是否创建 lease/attempt 并再次 validation/canonical | 可能形成普通执行路径之外的“特权计算路径” |
| `TS-MERGE-004` | 部分覆盖 | `MergePlan` 以 required slot 决定父节点何时合并 | 没有 expected output 到子图最终输出的显式 resolution；主要依赖直接 child slot |
| `TS-MERGE-005`、`006` | 遗漏 | 当前只声明 merge strategy 和 subtree pruning | 未声明交换/结合/幂等性质，也没有 upstream invalidation 后的 lineage closure 计算 |
| `TS-MERGE-007` | 部分覆盖 | `MergePlan` 有 required slots、策略和 child canonical hashes | 未明确 decomposer 的 `EOQ`/最后答案不等于协议完成，merge handler/version 与验证要求仍需 Phase 4/5 固化 |
| `TS-SETTLE-001` 至 `004` | 一致 | 只有 eligible contribution 结算；迟到、未请求重复默认不奖励；幂等 SettlementRecord | 建议补“selection/validation IDs 是结算证据”的字段级要求 |
| `TS-SETTLE-005` | 部分覆盖 | 已有完成、拆分和冗余验证贡献 | merge 贡献和独立验证贡献的统一 taxonomy 未完全固定 |
| `TS-SETTLE-006` | 遗漏 | 未结算贡献可 invalidated，已生成 SettlementRecord 为不可变事实 | 没有已结算后追加 reversal/adjustment 的政策和事件 |
| `TS-SETTLE-007` | 主动延期 | V1 明确仅 sandbox 结算 | 合理延期 |

## 23. 跨模块不变量对照

| 候选不变量 | 覆盖情况 | 说明 |
|---|---|---|
| `TS-INV-001` 至 `004` | 一致 | canonical-only dependency、attempt/task 分离、唯一 bundle、fencing 已明确 |
| `TS-INV-005` | 一致 | 结构化提案、核心原子扩图、无环与幂等边界已明确 |
| `TS-INV-006` | 一致 | 三层责任表对插件、核心和 executor 权限划分清楚 |
| `TS-INV-007` | 一致，但与确定性 artifact 恢复条款有边界冲突 | AI 不重算已明确；所有 task 是否都禁止在 state replay 重算仍需裁决 |
| `TS-INV-008` | 部分覆盖 | 有 causation、hash chain 和 merge child hashes；尚无固定 lineage audit 输出 |
| `TS-INV-009` | 部分覆盖 | artifact hash 和不可变正式记录已明确；失效后的新 artifact/新 selection 流程未定义 |
| `TS-INV-010` | 一致 | 验证后进入贡献资格，结算有幂等键 |
| `TS-INV-011` | 部分覆盖 | schema/plugin/executor version 已有；validator 和 merge policy 的端到端版本传播仍不完整 |
| `TS-INV-012` | 一致 | 新 lease/attempt 恢复，历史 attempt 保留 |
| `TS-INV-013`、`014` | 部分覆盖 | 主 TDD 有领域验证和 `first_verified_bundle`，且 PBFT/Raft 不在 V1 runtime | 尚未明示强 checker 高于结果一致性，也未系统区分控制面排序、结果验证和 canonical selection |
| `TS-INV-015` | 部分覆盖 | 已规划 SimulationProfile、fault wrapper 和报告 | seed、实际随机决定、时钟语义与 replay 零模拟调用未固化 |
| `TS-INV-016` | 部分覆盖 | AI 原始输出需 artifact 化，非确定性输出不可在 replay 重生 | 尚未覆盖未来 actor/长驻 session 的全部可变状态边界 |
| `TS-INV-017` | 部分覆盖 | Lean stub 已要求固定环境摘要和 checker fixture | 环境仍是自由结构摘要，缺少不可变 identity 与跨 request/submission/validation 的一致性约束 |
| `TS-INV-018` | 部分覆盖 | 扩图必须经结构化 proposal，原始 AI 文本不能直接改图 | 没有明确 durable subgoal promotion 条件，也没有禁止把 ToT thought/LM self-score 直接变成任务或验证 |
| `TS-INV-019` | 部分覆盖 | 已规划 raw output、parsed output 和 log artifact | action/observation provenance 与 free-form reasoning 的证据等级尚未区分 |
| `TS-INV-020` | 一致或部分覆盖 | Scheduler 只创建 lease，submission 仍需验证 | 应在 Phase 3/4 文档明确 allocation、validation、selection 三种 decision 类型和事件身份 |
| `TS-INV-021` | 主动延期且边界待补 | P12 已被定位为未来弱验证；V1 没有长期信誉系统 | P13-P15 的模型差异、适用数据和 score scope 尚未写入主 TDD extension contract |

## 24. 已整合的决策记录

以下事项原为项目负责人裁决清单。2026-06-23 起，推荐项已按 V1 可实现性、TokenShare
研究目标和 P01-P22 机制整合进主 TDD。列表保留为每项取舍的来源解释，不再是阻塞 Phase 3
开发的待办清单。

### 24.1 任务图、扩图与契约边界

这组裁决决定任务如何被描述、拆分、持久化并进入统一执行生命周期。

#### `DEC-P01P07-003`：是否引入 CIEL 式 expected output

- **方案 A**：新增最小 `ExpectedOutputRef`/resolution 语义，父任务可把命名输出委派给子图最终输出。
- **方案 B**：继续仅用直接 child slots 和 `MergePlan` 表达完成条件。
- **推荐**：采用方案 A，但只实现最小 future，不引入通用 workflow language。它能准确表达多级递归、共享子图和 continuation，并避免用“所有直接孩子完成”错误代替输出完成。
- **影响阶段**：Phase 4 的 proposal、graph update、ready calculation 和 replay schema。

#### `DEC-P01P07-005`：类型放在端口还是依赖边

- **方案 A**：类型只由版本化 `TaskSchema` 的 input/output ports 定义，edge 只保存端口名。
- **方案 B**：edge 同时保存 artifact type/schema snapshot。
- **推荐**：方案 B，但把 snapshot 定义为冗余审计信息；权威兼容性仍由固定版本的端口 schema 判定。这样旧图不依赖当前 registry 才能解释，但避免出现两个权威类型源。

#### `DEC-P01P07-004`：动态图 ID 是否确定性派生

- **方案 A**：child task、edge、expected output ID 由 accepted proposal 的 canonical digest 和逻辑位置确定性派生。
- **方案 B**：继续使用随机 ID，只依靠 event 持久化保证重放。
- **推荐**：方案 A。随机 ID 仍可重放，但对重复 proposal 去重、审计比较和幂等恢复更弱。
- **约束**：不能重新运行 agent 计算 digest；必须使用已保存 proposal artifact。

#### `DEC-P01P07-002`：动态扩图何时可进入 authoritative graph

- **方案 A**：只有来源 attempt 已验证并 canonical 后，扩图提案才可接受。
- **方案 B**：候选 attempt 可触发 speculative 子图，来源 canonical 后再转正。
- **推荐**：V1 采用方案 A。方案 B 会引入 speculative task、级联取消和浪费结算，超出当前本地 PoC 的必要范围。
- **现状**：主 TDD 已基本采用方案 A；候选规范保留了未来 speculative 路径。

#### `DEC-P01P07-006`：Requirements 与 Hints

- **方案 A**：Phase 3 同时定义 hard requirements 和 soft hints。
- **方案 B**：V1 只保留 `required_capabilities`。
- **推荐**：方案 A。字段和 matcher 成本很低，却能避免把偏好误当硬约束，也直接吸收 CWL、Dryad 和 Mesos 的共同经验。

#### `DEC-P01P07-009`：Candidate artifact 的发布协议

- **方案 A**：增加 staging -> hash/size verify -> immutable publish；未 publish 的路径不可形成 `ArtifactRef`。
- **方案 B**：直接写目标文件，依靠事后 hash 检查。
- **推荐**：方案 A，并在 Phase 3/4 前固化。否则进程中断可能让半文件进入 submission 和验证路径。

#### `DEC-P08P12-017`：未来 Ray/actor 状态边界

- **方案 A**：actor/长驻 AI session 只属于 executor 内部；影响协议的状态必须提交为 artifact/event。
- **方案 B**：允许协议恢复时重新连接 actor 并读取其内存作为权威状态。
- **推荐**：方案 A。它保持本地 executor、未来 Ray runtime 和 AI session 使用同一协议边界，也避免恢复依赖不可重放的进程内状态。

#### `DEC-P16P17-023`：哪些中间状态可以晋升为 `TaskUnit`

- **方案 A**：decomposer/ToT 产生的每个 thought、partial state 或候选分支都创建 `TaskUnit`。
- **方案 B**：只有具备稳定 schema、typed I/O、独立调度、明确 validator 和受控图关系的 durable subgoal/proof state 才能通过 `ExpansionProposal` 晋升；其他状态留在 attempt artifact。
- **方案 C**：所有搜索状态都保持 executor 私有，不允许 AI 产生动态子任务。
- **推荐**：方案 B。它保留 TokenShare 验证递归拆分的研究目标，同时避免图爆炸、不可验证 thought、结算放大和 chain-of-thought 依赖。
- **影响阶段**：Phase 4 expansion schema、Phase 5 settlement eligibility、Phase 6 structured report/Lean fixtures。

#### `DEC-P17-027`：V1 是否定义通用 search policy schema

- **方案 A**：协议核心现在定义 ToT 风格 generator/evaluator、BFS/DFS、breadth/depth/pruning 等通用对象和事件。
- **方案 B**：V1 只提供通用 budget/deadline、artifact 和 `ExpansionProposal` 边界；search policy 作为版本化插件私有实现，只有影响 authoritative graph 的最终决定进入协议。
- **方案 C**：完全禁止插件内部搜索和多候选。
- **推荐**：方案 B。TokenShare 需要验证“搜索结果如何安全进入协议”，不需要规定求解器如何搜索；未来如果实验把 search trajectory 本身作为研究对象，再新增专门 artifact/schema。
- **影响阶段**：Phase 3/4 contract 边界与 Phase 6 可选实验指标。

### 24.2 执行、验证与正式选择

这组裁决区分执行授权、审计证据、验证结论和正式输出，并确定 Lean stub 的实验边界。

#### `DEC-P01P07-001`：Lease 与 Attempt 的基数

- **方案 A**：一个 lease 严格对应一个 attempt，重启执行必须创建新 lease。
- **方案 B**：一个 lease 可授权多个 attempt，只要 fencing token 相同。
- **推荐**：方案 A，沿用 Phase 2。它让执行授权、迟到隔离、成本和审计一一对应，也避免 executor 在一个授权内自行重试而绕过协议。
- **接受后的动作**：把候选规范 `TS-ATTEMPT-001` 和对象关系修订为一对一。

#### `DEC-P19P21-021`：验证环境如何进入协议合同

- **方案 A**：继续只保存自由结构 `environment_summary`，由 validator 自行解释。
- **方案 B**：新增不可变 `EnvironmentRef`/digest；request 绑定、submission 回显、validation 引用并检查一致性，summary 只用于展示。
- **方案 C**：仅 Lean stub 使用固定字符串，其余 task 不定义环境合同。
- **推荐**：方案 B。P19 已证明细微 namespace/import 环境差异会让正确 proof 被误判；统一引用也能覆盖 deterministic executor、fixture checker 和未来容器环境，而不要求 V1 引入容器或真实 Lean。
- **影响阶段**：Phase 3 execution contract、Phase 4 verification、Phase 7 audit replay。

#### `DEC-P18-024`：执行审计是否要求 reasoning trace

- **方案 A**：要求 executor 保存完整 chain-of-thought，否则 submission 不可审计。
- **方案 B**：强制保存 action、observation、tool ID/version、输入输出 digest、raw/parsed output 和错误；reasoning trace 仅作为可选、受政策控制的 raw artifact。
- **方案 C**：只保存最终输出，不保存工具交互。
- **推荐**：方案 B。外部动作和观察是可核对事实，free-form reasoning 不是正确性证明，也不应成为 replay 的硬依赖；方案 C 无法解释工具型输出来源。
- **影响阶段**：Phase 3 `ExecutionSubmission`/log artifact、Phase 7 audit report。

#### `DEC-P01P07-007`：V1 验证策略的范围

- **方案 A**：统一采用“多次结果一致/多数”形成正式结果。
- **方案 B**：分离 `verification_mode` 与 `selection_policy`；V1 实现 `deterministic_exact`、`proof_checked`、`bounded_structural` 和 `first_verified_bundle`，只保留未来 aggregation extension point。
- **方案 C**：V1 同时实现 BOINC 式 quorum 和 Dawid-Skene latent-label estimation。
- **推荐**：方案 B。factorization 与 Lean stub 使用强 checker；structured report 只声明其有界结构验证范围，不把 schema/引用通过夸大为语义真值。方案 A 会把一致性错当正确性，方案 C 缺少适合的 V1 数据、独立观察者和研究必要性。

#### `DEC-P08P15-020`：Dawid-Skene/GLAD/MACE/CROWDLAB 是否进入 V1

- **方案 A**：选择其中一种算法，加入 factorization、Lean 和 structured report 的统一 canonical 决策。
- **方案 B**：不进入 V1；只保留未来离散弱验证插件的 extension point，并按所选模型要求交叠观察、独立性、item difficulty/spam/classifier calibration、模型版本、后验概率和不确定性。
- **方案 C**：V1 同时实现四种算法并比较。
- **推荐**：方案 B。前两个实验已有强 checker；structured report V1 也没有形成适合估计 observer confusion/ability/difficulty 或训练校准 classifier 的跨 item 离散标签数据集。方案 C 的结果主要测统计模型，不再聚焦协议闭环。

#### `DEC-P08P12-019`：PBFT 是否作为 verifier committee 阈值依据

- **方案 A**：直接以 `3f+1` committee、`2f+1` quorum 作为 AI/语义答案验真规则。
- **方案 B**：拒绝直接映射；PBFT 只作为未来确定性控制面复制参考，verifier committee 必须按任务验证模型另行论证。
- **推荐**：方案 B。PBFT 的阈值依赖确定性状态机、独立 Byzantine replica、认证消息和 view/checkpoint 等完整前提，不能证明多个相关 AI 返回的语义答案正确。

#### `DEC-P01P07-008`：CanonicalSelection 如何表示

- **方案 A**：新增独立持久化对象/表。
- **方案 B**：`CANONICAL_OUTPUTS_BOUND` event 就是逻辑 selection record，补充 `selection_id`、policy/version、bundle digest 和 validation IDs；SQLite 只投影。
- **推荐**：方案 B。候选规范要求的是独立身份和审计语义，不要求增加权威表；event-first 更符合当前架构。

#### `DEC-P19-022`：Lean stub 采用何种交互粒度

- **方案 A**：V1 保持 one-shot proof patch/candidate artifact，由版本化 fixture checker 返回 pass/error；可选保存模拟 proof state 和 diagnostics。
- **方案 B**：V1 实现 `initialize(theorem)`、`run_tactic(state,tactic)`、next/error/done 的多步 stub 状态机。
- **方案 C**：直接接入真实 LeanDojo/Lean/mathlib。
- **推荐**：方案 A。研究目标是验证协议能承载 proof-like artifact、失败诊断、递归子目标和 environment-bound checker，不是评估 tactic search。方案 B 可作为后续更逼真的 Lean adapter fixture；方案 C 明确超出 V1。
- **影响阶段**：Phase 6 Lean stub fixture 与实验验收。

#### `DEC-P20-025`：Lean stub 是否使用实际 miniF2F 子集

- **方案 A**：V1 下载并固定一个 miniF2F/Lean 子集，使用真实 statement/problem IDs 作为 fixture。
- **方案 B**：V1 使用仓库内版本化 synthetic proof fixtures，只借鉴 miniF2F 的难度/题型分层和版本固定方法。
- **方案 C**：两者都做，以 synthetic fixture 验证协议，以实际子集补充 benchmark 报告。
- **推荐**：方案 B。当前没有真实 Lean runtime，使用真实 miniF2F statement 不能增加 checker 真实性，反而引入数据版本、Lean 版本和形式化依赖；若未来接入真实 Lean，再采用方案 C。
- **影响阶段**：Phase 6 fixture 来源、实验报告和外部资料落库。

### 24.3 调度、能力与实验度量

这组裁决决定分派为何发生、历史能力如何解释，以及实验必须保存哪些可比较证据。

#### `DEC-P01P07-015`：Capability freshness

- **方案 A**：lease 保存 capability snapshot/digest；client capability 变化使用新 epoch。
- **方案 B**：lease 只引用当前 `ClientRecord`。
- **推荐**：方案 A。这样历史调度决定在 client 更新能力后仍可解释，成本只是一份小型不可变摘要。

#### `DEC-P22-026`：V1 采用完整协商还是直接指派

- **方案 A**：实现 task announcement、bid、award/refusal、expiration 的 Contract Net 消息循环。
- **方案 B**：本地 registry 中执行 hard eligibility filter、版本化排序、确定性 tie-break，记录 `AllocationDecision` 后直接发 lease；无匹配时记录结构化原因。
- **方案 C**：继续使用当前“第一个匹配 client”，不记录完整候选/过滤原因。
- **推荐**：方案 B。它吸收 P22 对 eligibility、assignment 和 no-bid diagnostics 的有效部分，同时保持 V1 非网络化边界；方案 A 会引入无研究必要性的分布式协商，方案 C 的审计证据不足。
- **影响阶段**：Phase 3 `ExecutorRegistry`/client status contract 和后续调度事件字段。

#### `DEC-P01P07-013`：Cilk 指标进入哪个阶段

- **方案 A**：Phase 6/7 增加 work、critical path、retry work、wasted work、shadow benefit 指标。
- **方案 B**：保持现有完成时间、节点数和重复比例。
- **推荐**：方案 A。没有 critical path 与 wasted work，很难判断递归拆分和 shadow execution 是否真正改善系统，而只能知道“最后跑完了”。

#### `DEC-P08P12-016`：实验可重复性字段是否进入 Phase 6 硬要求

- **方案 A**：`ExperimentRun` 固定 `SimulationProfile` digest、seed、clock semantics、fixture、fault triggers、ProtocolConfig/plugin/executor versions，并持久化实际随机决定。
- **方案 B**：只保存 profile 名称和最终指标。
- **推荐**：方案 A。TokenShare 是研究原型，无法复查实验条件会直接损害研究目标；这些字段属于实验基础设施，不扩大协议核心。

### 24.4 重放、控制面、合并与结算

这组裁决处理历史事实的恢复边界、未来控制面复制、递归完成路径和结算后的失效策略。

#### `DEC-P01P07-011`：State replay 遇到丢失的确定性 artifact

- **方案 A**：state replay 失败并报告缺失；若要重算，启动带新 run/attempt/artifact 身份的 repair/re-execution。
- **方案 B**：replay 可调用原确定性执行器，重建同 ID artifact。
- **推荐**：方案 A。即使数学结果确定，执行环境、代码版本和副作用也未必完全相同；在 replay 中隐式重算会把“历史事实恢复”与“再次实验”混为一谈。
- **整合结果**：2026-06-23 主 TDD 10.2 已改为方案 A；state replay、repair/re-execution 和新实验的身份边界已分离。

#### `DEC-P01P07-012`：Event 顺序模型

- **方案 A**：保留全局 `event_seq` + hash chain，不增加 aggregate sequence。
- **方案 B**：同时增加每 aggregate 单调 sequence/version。
- **推荐**：V1 采用方案 A，但每次对象状态转移 payload 必须携带 old/new state，并由单写者事务检查。真实并发写入或远程 coordinator 出现前再采用方案 B。
- **影响**：若采用 A，需要把候选 `TS-REPLAY-003` 降为 V1 的全局顺序不变量。

#### `DEC-P08P12-018`：Raft/PBFT 在路线图中的位置

- **方案 A**：V1 继续采用单机单写者 JSONL；未来多协调器出现时，在 EventLedger 提交边界下另设计 crash/Byzantine 复制层。
- **方案 B**：Phase 3/4 现在加入 leader、term/view 和 quorum commit 字段。
- **推荐**：方案 A。V1 没有多协调器故障问题，提前引入会遮蔽 TokenShare 真正要验证的拆分、验证、合并、结算和 replay 语义。

#### `DEC-P01P07-010`：Merge 是否走统一执行生命周期

- **方案 A**：merge 是普通 `TaskUnit`，必须经过 lease、attempt、candidate、validation 和 canonical。
- **方案 B**：`MergeCoordinator` 可在协议进程内直接调用插件，只有 `MergeRecord`，但 merge output 仍需验证和 canonical。
- **方案 C**：按插件声明选择 A 或 B。
- **推荐**：方案 A。它没有特权计算路径，故障、重试、贡献、版本和审计语义最统一；确定性本地 merge 仍可由 local executor 快速完成。
- **代价**：任务图节点和事件数量增加，需要明确 parent/output resolution。

#### `DEC-P01P07-014`：已结算贡献的失效处理

- **方案 A**：V1 就定义 reversal/adjustment event，但只在测试中使用 sandbox 数值。
- **方案 B**：V1 只允许根任务完成后一次性最终结算，不支持结算后 invalidation。
- **推荐**：方案 B 作为 V1 行为，同时在 schema 中保留 adjustment event extension point。V1 先保证 final settlement 的严格前置条件，减少补偿状态机范围。

## 25. 已采用的取舍顺序

这些决策存在字段、状态机和实验依赖；主 TDD 的整合按以下顺序完成：

1. **先冻结基础契约**：`DEC-P01P07-003`、`DEC-P01P07-005`、`DEC-P01P07-004`、`DEC-P01P07-006`、`DEC-P01P07-009`、`DEC-P01P07-015` 与 `DEC-P19P21-021`。它们决定 expected output、typed edge、确定性 ID、requirements/hints、artifact publish、capability snapshot 和环境身份，必须先于 Phase 3/4 字段冻结。
2. **再冻结执行与分派边界**：`DEC-P01P07-001`、`DEC-P18-024`、`DEC-P08P12-017` 与 `DEC-P22-026`。它们共同确定 lease/attempt 基数、审计证据、actor 私有状态和直接指派 decision envelope。
3. **随后确定扩图与递归完成**：`DEC-P01P07-002`、`DEC-P16P17-023`、`DEC-P17-027` 与 `DEC-P01P07-010`。它们决定 proposal 何时生效、哪些状态能成为任务、搜索策略是否进入核心，以及 merge 是否走统一执行生命周期。
4. **在实现验证前分清四类决定**：`DEC-P01P07-007`、`DEC-P08P15-020`、`DEC-P08P12-019` 与 `DEC-P01P07-008` 分别处理领域正确性、统计估计、控制面阈值误映射和正式选择；再用 `DEC-P19-022`、`DEC-P20-025` 固定 Lean stub 与 benchmark 边界。
5. **在重放规格冻结前解决历史事实边界**：`DEC-P01P07-011`、`DEC-P01P07-012` 与 `DEC-P08P12-018` 必须共同裁决，避免 state replay、event ordering 和未来 replicated commit 使用互相冲突的语义。
6. **最后固定研究指标和结算范围**：`DEC-P01P07-013`、`DEC-P08P12-016` 与 `DEC-P01P07-014` 分别决定并行性度量、实验可重复性字段和已结算贡献的失效策略。

## 26. 对主 TDD 的修订清单（已执行）

以下修订已在 2026-06-23 合并进主 TDD，后续阶段规格只需继续细化字段和测试，不应再把
本节作为并列任务清单。

### 26.1 Phase 3：插件、执行器与调度契约

- descriptor 固定 typed ports、requirements/hints、validator/merge policy version 和 capability snapshot；candidate artifact 使用 staging、hash/size verify 和 immutable publish。
- executor contract 明确 actor/session 内存不是协议状态，影响恢复的内容必须 artifact/event 化；execution log 保存 action、observation、tool/version 和输入输出 digest，hidden reasoning trace 不是协议真值或 replay 前置条件。
- execution request、submission、validation 和 audit verification 共享不可变 `EnvironmentRef`/digest，绑定同一 checker、fixture、toolchain、library 和配置身份。
- 本地分派采用 eligibility filter -> policy rank -> deterministic tie-break -> `AllocationDecision` -> lease，并记录 capability snapshot、候选过滤摘要和 no-match reason；完整 Contract Net bidding 保持延期。

### 26.2 Phase 4/5：扩图、验证、正式选择与合并

- 对象模型和主流程增加最小 expected output/resolution 语义；动态 child task、edge 和 expected output 使用 accepted proposal 派生的确定性 ID。
- durable state promotion guard 只允许 typed、可独立调度且可验证的 subgoal/proof state 通过 proposal 晋升为 `TaskUnit`；thought、value、vote 和 working memory 只作为 attempt artifact。
- verification mode、selection policy 与 aggregation model 分离，明确强 checker 优先、一致性不替代正确性；Dawid-Skene、GLAD、MACE、CROWDLAB 分别记录适用数据、模型版本、收敛/校准和不确定性，不作为三类 V1 实验的共同 canonical 算法。
- `CANONICAL_OUTPUTS_BOUND` 明确为带 selection identity、policy/version、完整 bundle 和 validation IDs 的逻辑 `CanonicalSelection`；同时说明 canonical invalidation 的影响分析和 settlement adjustment 是 V1 非目标还是 extension point。
- merge 使用普通 task/attempt/validation/canonical 生命周期；若选择特权路径，必须显式记录理由、版本和等价的审计约束。

### 26.3 Phase 6：实验与 fixture

- `ExperimentRun`/`SimulationProfile` 固定 seed、clock、fixture/benchmark identity、fault trigger 和实际随机决定，并规定 replay 不重新模拟。
- 指标加入 work、critical path、retry/wasted work 和 shadow benefit，区分协议正确性结论与模拟性能结论。
- Lean stub 明确采用 one-shot fixture checker 还是多步 proof-state stub，并固定 fixture/benchmark profile；真实 LeanDojo、mathlib 和 miniF2F 集成保持为后续插件工作。

### 26.4 Phase 7 与跨阶段文档：重放、控制面和来源

- 删除或改写“状态重放时可重执行确定性输出”的表述，严格区分 state replay、repair 和新实验。
- event/replay 章节明确 V1 是 single-writer ledger，local append、projection apply 与未来 replicated commit 是不同边界；Raft/PBFT 仅作为未来多协调器控制面扩展，不作为 executor 答案或 AI verifier committee 的正确性依据。
- 论文映射将 environment-correct proof checking 主要归于 P19；P21 只作为 mathlib/library/version 背景，不再声称其提出 environment hash/replay 机制。

## 27. 对照结论

现有主 TDD 与 P01-P22 独立推导出的协议骨架总体同向。已有设计已经正确建立协议核心、插件和执行器分层，隔离 candidate 与 canonical output，以受控 proposal 扩图，以 append-only event 和 immutable artifact 保存事实，并把真实分布式 runtime、生产 agent 系统和完整 Lean proving 留在 V1 之外。需要加强的不是组件数量，而是若干跨组件合同：expected output/resolution、requirements/hints、selection identity、verifier environment、durable state promotion、action/observation provenance、可解释分派和可复现实验字段。

论文提供的算法必须服从这些合同，而不能反向塑造协议核心。强 checker 仍是 factorization 和 Lean stub 的正确性来源；统计弱验证只在满足具体模型假设的离散任务中启用；ToT、ReAct 和 decomposer 属于插件或执行器策略；miniF2F 和 mathlib 约束 fixture 与环境版本；Contract Net 只贡献 eligibility、assignment 和 refusal 语义；Raft/PBFT 只约束未来控制面排序。由此，TokenShare V1 可以验证递归拆分、执行、验证、合并、结算和 replay 闭环，而不被任何一篇论文的完整系统范围拖走。

2026-06-23 起，本文已完成作为主 TDD 输入的职责。后续 Phase 3/4/5/6/7 规格应直接读取主
TDD 中的 V1 机制整合原则、阶段计划和范围外边界；本文只在需要追溯论文依据、原始冲突
或取舍理由时阅读。
