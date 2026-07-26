# 第四章代码—论文对齐修改指南

日期：2026-07-22  
状态：论文修改决策记录，不是论文正文，不是新的协议规格，也不替代实验设计

## 1. 文件目的

本文记录第四章方法论表述应如何与 TokenShare 当前实现及近期最小开发范围对齐。它解决的不是“第四章逐句应该怎么写”，而是以下编辑决策：

1. 哪些机制已经实现，可以继续作为第四章的方法组成；
2. 哪些机制只存在局部缺口，可以保留表述并用少量代码补齐；
3. 哪些机制的思想可以保留，但必须降低论文主张强度；
4. 哪些机制需要新增较大的状态、数据或执行流程，应当从第四章删除；
5. 修改完成后，如何检查论文没有继续承诺代码并不具备的能力。

本文只约束论文方法章节的代码对齐方式。实验矩阵、模型、题库、故障类型、指标和 paper eligibility 仍以 [`tokenshare_latest_real_plugin_experiment_design.md`](tokenshare_latest_real_plugin_experiment_design.md) 为唯一权威；paper runner 到系统 runtime 的迁移仍按 [`2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md`](2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md) 执行。

## 2. 总体修改原则

第四章不必只描述已经逐行冻结的代码。对于实现成本很低、边界清楚、不会改变系统架构的机制，论文可以保留，后续再补齐代码。判断标准不是“今天是否已经实现”，而是“为了使该主张成立，需要付出多大的系统代价”。

### 2.1 可以保留并小补代码的判定标准

一项机制原则上需要同时满足以下条件：

- 不新增新的持久化协议对象；
- 不新增一组跨阶段的 event family；
- 不修改 SQLite 主体 schema，或者只使用现有 event payload 字段；
- 不建立第二套状态机；
- 不改变 Factorization 和 Lean 两个插件的领域边界；
- 不要求新的实验矩阵、题库或统计口径；
- 改动集中在一个既有模块或一个小型 helper；
- 可以用少量定向测试证明，不依赖完整正式实验才能判断正确性。

“几行代码”或“一个文件”是成本尺度的直观描述，不是机械的文件数限制。若一个纯决策 helper 需要在 `ProtocolEngine` 中增加一次调用并补一份测试，它仍然属于局部小改；若一个看似只有一个新类，却要求修改对象、event、SQLite、runner、两个插件和报表，则属于大型机制。

### 2.2 应当从论文删除的判定标准

出现以下任一情况时，默认不继续补代码，而是删除或显著收窄论文主张：

- 需要新增协议级状态或新的持久化对象；
- 需要修改任务生成、验证、合并和结算多个阶段；
- 需要两个插件分别实现一套新的领域算法；
- 需要分布式锁、事务、共识、网络 worker 或生产安全设施；
- 只有运行新的大规模实验才能验证其行为；
- 该机制不是当前 Experiment 1–5 的必要前提；
- 该机制会延迟真实 AI 实验的完成。

## 3. 修改决策总表

| 机制或论文主张 | 当前基础 | 论文处理 | 允许的代码投入 |
| --- | --- | --- | --- |
| lease、attempt 与 fencing token 绑定 | 2026-07-23 小补丁后，submission/heartbeat 在 engine 写入边界使用 ledger 最新 attempt/lease snapshot；core 继续只负责纯规则 | 保留 | 不再扩展为分布式授权机制 |
| replacement 使用不同的 attempt-bound token | coordinator 按 run identity 和 schedule ordinal 生成不同 token；worker-death system integration 已显式断言 replacement lease/attempt/token 均不同 | 保留“每次重新调度使用不同 token”；不得写“全局单调递增”或“密码学随机” | 已完成局部不重复回归，不再扩展 token 语义 |
| 迟到或旧 lease 的 submission 不推进权威状态 | `SubmissionAcceptanceDecision` 现在针对 ledger 最新 attempt/lease 执行 active/deadline/fencing 判断；被拒绝 submission 只留审计记录 | 保留 | 不扩大成分布式 exactly-once |
| 被拒绝操作仍可留下审计证据 | append-only event ledger 已有基础 | 保留，并区分证据日志与权威状态投影 | 通常无需新增代码 |
| 每个 unit 只有一个 canonical output | 已有 deterministic eligibility、事件顺序和 SQLite 唯一约束 | 保留，但限定本地单协调器模型 | 不增加分布式 CAS |
| batch 的整体可见性 | 已有 batch envelope 和完整性投影检查 | 保留“完整 batch 才进入权威投影” | 不实现 JSONL 物理事务 |
| 插件授权的有界图扩展 | 已有 proposal、DAG、depth/count limits；Lean 使用预注册固定 lemma-DAG | 保留 | 不增加任意自动拆分 |
| merge 依赖 canonical child outputs | 已有 merge task、slot binding、verification、root recheck | 保留，按真实 merge task 流程描述 | 通常无需新增代码 |
| 事件重建与非确定性 artifact replay | 已有 ledger/SQLite 重建和 AI artifact 离线验证基础 | 保留有限 replay 主张 | 不提前实现完整 runtime resume |
| contribution eligibility 和 sandbox settlement | 已有离散贡献种类、状态与等权结算 | 保留最小离散记账 | 不增加精确价值评估 |
| 任意任务的无限或通用递归展开 | Factorization 合数余项递归未完成；Lean 只支持预注册固定图 | 删除 | 禁止为论文补大型通用递归机制 |
| 精确贡献统计、边际价值或因果归因 | 没有相应方法和实验基础 | 删除 | 禁止新增复杂 attribution/credit propagation |
| 多协调器并发、分布式事务与 exactly-once | V1 是本地单机研究原型 | 删除 | 禁止扩展分布式基础设施 |
| 从任意中断位置完整恢复外部执行 | 当前不具备完整 coordinator/runtime resume | 删除或降为 projection replay | Phase 9 不前置 |
| AI 自动发现任意任务或 theorem 的分解结构 | 当前拆分权威来自插件规则或预注册 plan | 删除 | 不增加通用自动 lemma discovery |

## 4. 可以保留并用少量代码补齐的机制

### 4.1 submission 的权威接受条件

第四章可以保留如下方法：submission 是否能推进状态，不只由 payload 格式决定，还取决于它是否仍然获得当前 attempt 和 lease 的授权。

建议最终条件为：

```text
attempt.state == Running
and lease.state == Active
and submission.task/unit/attempt/lease 与当前权威记录匹配
and submission.fencing_token == lease.fencing_token
and submission.submitted_at <= lease.expires_at
```

不满足条件时，submission 可以作为审计证据保存，但不能进入 verification、canonicalization 或 merge。

当前相关实现位置：

- [`src/tokenshare/core/recovery.py`](../../src/tokenshare/core/recovery.py)
- [`src/tokenshare/protocol_engine.py`](../../src/tokenshare/protocol_engine.py)
- [`src/tokenshare/core/leases.py`](../../src/tokenshare/core/leases.py)

这一机制边界局部，而且直接复用已有 `Attempt`、`Lease` 和 ledger projection，不要求建立新状态机。2026-07-23 小补丁已经让 submission/heartbeat 在 `ProtocolEngine` 写入边界恢复 ledger 最新 snapshot；Factorization、Lean、late recovery 和真实 OS worker-death 的 system runtime integration 已完成复核，因此可以在论文中保留这一受限主张。

### 4.2 replacement lease 的 attempt-bound fencing token

第四章可以写：

> 每次重新分派产生一个与新 lease 和 attempt 绑定的不同 fencing token；旧 token 对后续 attempt 不再具有推进权威状态的资格。

不要写：

> fencing token 在全系统范围内严格单调递增。

当前 coordinator 已按 run identity 与 schedule ordinal 生成 attempt-bound token，并将它持久化在已有 lease event 中。worker-death replacement 集成回归已经显式验证新 lease、attempt 和 fencing token 均不同；不必改成 UUID。全局单调语义则需要维护持久化 epoch、解决并发分配和恢复后的计数一致性，没有必要。

### 4.3 明确记录拒绝原因

若论文需要区分 `lease_not_active`、`attempt_not_running`、`fencing_token_mismatch` 和 `lease_deadline_exceeded`，可以继续使用现有 acceptance decision 中的稳定 `rejection_reason`。这是审计分类，不是新故障机制，也不应扩展 Experiment 3 已冻结的故障类型。

### 4.4 本地单写入决策

paper runtime 迁移已经完成，正常 FULL/fault/ablation 路径把协议状态写入集中到 `ProtocolRunCoordinator` 调用的 `ProtocolEngine`。该约束允许论文保留确定性 canonical selection 和顺序化状态推进，但必须把适用范围写清楚：这是本地单协调器模型，不是多节点并发共识。

这一项属于既定 runtime 迁移的执行约束，不应被包装成新的论文贡献。

## 5. 已有机制需要怎样收窄论文措辞

### 5.1 区分证据日志与权威状态

不建议继续使用下面这种表述：

> 如果一个操作不满足前置条件，则整个系统状态保持不变。

原因是无效操作仍可能作为审计事实追加到 ledger。建议在方法论上区分：

- $E_t$：截至时刻 $t$ 的 append-only evidence ledger；
- $Q_t$：从有效事件投影得到的权威协议状态。

可使用：

\[
E_{t+1}=E_t \mathbin{\Vert} e_o,
\]

\[
Q_{t+1}=
\begin{cases}
T_o(Q_t), & \text{if operation } o \text{ is accepted},\\
Q_t, & \text{otherwise}.
\end{cases}
\]

这里的重点不是创造一个代码中的全局 `Omega` 对象，而是抽象出“证据可以增长，权威状态只由被接受的操作推进”这一方法。

### 5.2 原子性改为逻辑 batch 完整性

不要写：

> 所有协议转换都作为物理原子事务写入。

应写：

> 对需要多事件共同表达的协议决定，事件携带同一 batch envelope；投影器只有在 batch 成员、顺序和终止标记完整时才承认其权威效果。

现有实现能够拒绝不完整 batch，但 JSONL 文件在进程中断时仍可能留下部分记录。该设计提供的是投影层的整体可见性，不是存储介质上的物理事务。

相关位置：

- [`src/tokenshare/storage/events.py`](../../src/tokenshare/storage/events.py)
- [`src/tokenshare/storage/sqlite_index.py`](../../src/tokenshare/storage/sqlite_index.py)

### 5.3 并发竞争改为本地顺序化选择

不要写多 worker 同时通过 CAS 争夺 canonical ownership。第四章应描述为：

> coordinator 按权威事件顺序评估满足 verification policy 的候选；第一个满足 eligibility 且成功绑定唯一约束的结果成为 canonical output。

这种方法足以支持当前本地实验。worker 可以并行生成候选，但协议写入与 canonical 决策由单协调器顺序化。

### 5.4 merge 按真实工作流描述

不要把 merge 简化为“父任务直接读取子答案并合并”。当前方法是：

```text
canonical child outputs
→ slot binding
→ 创建 merge TaskUnit
→ merge execution
→ verification/checker
→ merge output canonicalization
→ expected-output resolution
→ parent completion
```

这比直接合并多了一层可验证的 merge task，也是可以从代码中抽象出的真正方法论。

### 5.5 replay 限定为投影重建和 artifact 复核

第四章可以保留：

- append-only event ledger 的校验；
- SQLite 权威投影重建；
- 已持久化 AI request/provenance/raw/usage/result artifact 的复核；
- replay 不重新调用 AI provider。

第四章不应声称：

- coordinator 可以从任意指令位置恢复；
- 在途外部 API 请求可以 exactly-once 续接；
- worker/process 状态可以完整复原；
- 任意中断运行都能自动继续。

## 6. 递归展开应保留什么、删除什么

### 6.1 可以保留

第四章可以把方法表述为“插件授权的有界任务图扩展”：

1. 插件根据确定性规则或预注册 plan 构造 proposal；
2. proposal 明确 children、dependencies、expected outputs 和 merge plan；
3. 协议检查 DAG、深度、节点数量、slot 和资源界限；
4. 通过检查后，扩展事实才进入权威图投影。

Lean 可以作为预注册固定 lemma-DAG 的实例：catalog/脚本预先给出 nodes、edges 和 merge shape，Lean 插件负责校验和生成 certificate；AI 只处理被分派的 proof unit。

### 6.2 必须删除

第四章不得声称系统已经：

- 对任意任务自动寻找最优拆分；
- 不受限制地递归展开直到所有叶节点可解；
- 自动发现任意 Lean theorem 的全部中间引理；
- 对 Factorization 的所有 composite cofactor 自动继续生成下一层任务；
- 证明通用递归一定终止或一定提升效果。

实现这些能力会同时影响动态任务生成、终止条件、预算传播、重复检测、merge 传播、贡献结算和实验设计，不属于局部补代码。

## 7. 贡献与结算应保留什么、删除什么

### 7.1 已经存在的最小能力

当前代码并非完全没有贡献记录。已有：

- `complete_canonical`、`expand_canonical`、`merge_canonical` 三类离散 contribution；
- `Pending`、`Eligible`、`Invalidated`、`Settled` 状态；
- 根据 canonical/accepted protocol facts 创建记录；
- root 完成后的 sandbox settlement；
- 预设的等权分配。

相关位置：

- [`src/tokenshare/core/contribution.py`](../../src/tokenshare/core/contribution.py)
- [`src/tokenshare/protocol_engine.py`](../../src/tokenshare/protocol_engine.py)

因此第四章可以保留一种最小方法：系统只为已经 canonicalized、并被父任务完成或根任务结算路径实际接纳的离散协议事实建立 contribution eligibility。

### 7.2 必须删除的精确贡献主张

第四章不得声称系统已经：

- 估计每个 worker 的边际贡献；
- 计算某个中间结果对最终答案的因果影响；
- 根据答案质量、难度或稀缺性自动生成连续权重；
- 沿任意递归图传播或折扣贡献值；
- 在多个候选之间执行 Shapley value 或类似 credit attribution；
- 形成具有经济解释的真实市场价格、信誉或代币收益。

这些能力需要新的统计方法、数据、对照实验和结算模型，不能通过几行代码使论文主张成立。第四章应把现有机制称为“基于权威协议事实的离散 eligibility 与 sandbox allocation”，而不是“精确贡献测量”。

## 8. 应当整体舍弃的大型机制

以下机制不进入当前第四章，也不应为了保留文字而加入当前开发计划：

1. 任意任务的通用递归分解与自动终止；
2. 精确贡献统计、因果归因与复杂奖励传播；
3. 多 coordinator、跨进程或跨节点的分布式一致性；
4. JSONL 的跨文件物理事务和 exactly-once execution；
5. 完整 coordinator checkpoint、任意断点 resume 和在途 API 恢复；
6. AI 自主决定协议级分解结构；
7. 自动发现任意 Lean theorem 的 lemma-DAG；
8. 真实区块链、钱包、智能合约或代币支付；
9. 生产级身份、权限、反女巫或拜占庭容错；
10. 新的信誉、市场定价或博弈机制。

这些内容即使具有研究价值，也不是当前两个 proof-of-concept 和五组实验的必要条件。

## 9. 第四章的建议方法边界

第四章可以把 TokenShare 方法限定为：

> 一个基于事件证据的本地任务协调协议。它使用 lease-bound execution 隔离失效尝试，通过插件 parser/verifier/checker 建立结果资格，按权威事件顺序确定 canonical output，允许插件提交受资源界限约束的任务图扩展，将合并表示为独立可验证任务，并从被最终工作流接纳的离散协议事实生成 sandbox contribution eligibility。事件日志用于重建权威投影和复核已持久化的非确定性输出。

第四章不应把系统描述为：

> 一个能够自动分解任意任务、精确计算所有参与者贡献、在分布式网络中实现强事务一致性，并从任意故障位置完整恢复的通用经济平台。

前者能够与当前实现和近期局部补丁对齐；后者需要另一个研究项目。

## 10. 实际修改论文时的执行顺序

### 第一步：给第四章每个机制主张标级

- `A`：已有代码证据，保留；
- `B`：局部缺口，允许少量代码补齐后保留；
- `C`：思想可用，但必须降低措辞强度；
- `D`：实现成本过大，删除。

### 第二步：每个保留方法都回答四个问题

1. 输入的协议事实是什么？
2. 接受或拒绝条件是什么？
3. 接受后产生什么权威事实？
4. 该方法明确不保证什么？

如果一个段落只是在复述“系统会拆分、执行、验证、合并”，却没有回答上述问题，它仍然是流程介绍，不是方法论。

### 第三步：建立论文主张—代码证据表

每个关键主张至少映射到一个实现位置和一组测试。例如：

| 论文主张 | 主要实现证据 | 验证证据 |
| --- | --- | --- |
| 迟到提交不推进权威状态 | `core/recovery.py`、`protocol_engine.py` | submission/recovery 定向测试 |
| 不完整 batch 不进入投影 | `storage/events.py`、`storage/sqlite_index.py` | batch/projection tests |
| merge 只消费 canonical slots | `core/merge.py`、merge coordinator/engine | Phase 5 merge tests |
| Lean proof 由真实 checker 接受 | Lean checker/validator/merge policy | Lean canary 与增量 audit |
| replay 不重新调用 AI | AI replay verifier | replay provider-call-zero tests |

### 第四步：删除不受实验验证的结论

即使某个机制在概念上合理，也不能提前写出“提高效率”“保证正确性”“实现公平分配”之类结论。第四章只能给出机制和不变量；效果结论必须由正式实验数据支持。

## 11. 修改完成后的二次审核清单

- [ ] 没有把第四章写成第三章问题定义的重复；
- [ ] 没有用大量篇幅复述任务生命周期；
- [ ] 每个 subsection 都包含可检查的规则、不变量或证据关系；
- [ ] 没有提及其他 section 来代替本节自己的论证；
- [ ] 没有声称任意递归展开；
- [ ] 没有声称精确贡献或边际价值计算；
- [ ] 没有声称全局单调 fencing token；
- [ ] 没有声称所有 JSONL 写入物理原子；
- [ ] 没有声称分布式 CAS、exactly-once 或多 coordinator 一致性；
- [ ] 没有声称任意断点的完整 runtime resume；
- [ ] Lean 拆分始终写成预注册固定 plan 加插件校验；
- [ ] contribution 始终写成离散 eligibility 和 sandbox allocation；
- [ ] replay 始终写成权威投影重建与持久化 artifact 复核；
- [ ] 所有“保证”“确保”“唯一”“原子”“恢复”等强词都有明确适用范围；
- [ ] 所有实验效果结论都等待真实 AI 正式结果，而不是从机制设计直接推出。

## 12. 当前停止线

截至 2026-07-24，以下实现侧停止线已完成：

1. [x] 完成既定 paper runner → system runtime/`ProtocolEngine` 迁移；
2. [x] 完成并验证 submission active/deadline/fencing acceptance，并补上 ledger 最新 snapshot guard；
3. [x] 复核 coordinator 对每次 replacement 使用不同的 attempt-bound token；
4. [x] 完成实现与 code map 的第四章映射复核。
5. [x] formal planning/execution/resume/replay 共享冻结、版本化 catalog execution view，未弱化 canonical selection identity 校验；
6. [x] selected-unit pilot 作为通用 runtime execution scope 进入 coordinator/engine，只输出 partial observation，不由 paper adapter 推进生命周期；
7. [x] Factorization completion 使用插件声明的非对称语义：accepted factor witness 为 OR-join，no-factor/prime 为全部 required ranges 的 AND-join；通用 core 不识别领域结果。

不再为这些机制新增通用代码。后续仍需在最终论文稿中落实本文要求的收窄措辞，并完成真实 AI Experiment 1–5、指标生成、结果分析和发布级总门禁；当前实现测试不能替代真实-provider 论文结果。
