---
status: user_authorized_workflow
document: slim_v2_stage_relay_protocol
scope: Serial fresh-task handoff for Slim V2 design, planning, implementation, and representative execution
---

# Slim V2 串行新任务接力协议

本文只定义 Slim V2 如何在多个上下文彼此隔离的新 Codex 任务之间串行接力。它不定义实验参数、指标、系统接口或实现方案，不能覆盖三份冻结权威文档。

## 1. 为什么使用新任务接力

Slim V2 跨越设计、实施计划、代码实现和真实运行。让一个 Agent 从头工作到尾会持续累积对话、命令输出和调试历史，容易造成上下文膨胀与目标漂移。因此，每个大阶段由一个新的顶层 Codex 任务负责；阶段内部才使用临时子 Agent 做只读审查或边界明确的实现任务。

两类协作必须区分：

| 类型 | 用途 | 上下文 | 是否负责下一阶段 |
|---|---|---|---|
| 顶层新任务 | 设计、计划、实施监督、representative 监督等阶段接力 | 新任务从空白对话开始，只读取明确文档和交接摘要 | 是；当前阶段 owner 只能创建一个下一阶段顶层任务 |
| 当前任务的子 Agent | 专项审查、单个 implementation task、故障定位 | 只接收该子任务所需的最小上下文 | 否；子 Agent 不得创建下一阶段顶层任务或递归扩张团队 |

阶段接力必须使用“创建全新任务”的能力。不要使用会继承完整已完成对话的 fork 代替空白任务。若下一阶段任务已经由用户预先创建，可以向其发送消息；否则由当前阶段 owner 创建新任务，并把完整初始 prompt 一次性写入新任务。

## 2. 固定仓库和任务环境

- 工作目录：`E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline`
- 分支：`codex/slim-v2-baseline`
- 所有阶段在同一 local checkout 上串行工作；任一时刻只能有一个顶层阶段任务写入。
- 创建下一阶段任务时，必须选择该 Slim V2 saved project 的 **local environment**，不能让工具默认从仓库默认分支创建另一个普通 worktree。否则下一任务可能从 `main` 启动并看不到当前阶段结果。
- 当前 owner 创建下一任务后不得继续修改文件。允许短暂读取下一任务状态，确认其已进入运行或明确失败，然后当前 owner 结束。
- 不 push、merge、创建 PR 或修改远端。阶段 checkpoint 只使用当前本地分支的小粒度 commit。

如果无法创建指向上述 local checkout 的全新任务，不得用 full-history fork 冒充。当前 owner 应把已生成的下一阶段 prompt 写入最终报告并停止，等待用户手动创建任务。

## 3. 所有阶段共同遵守的启动顺序

每个新的阶段 owner 都必须从仓库事实重新建立上下文，不能把上一 Agent 的摘要当权威：

1. 确认仓库根目录、分支、HEAD 和工作树状态。
2. 完整阅读根目录 `AGENTS.md`。
3. 完整阅读 `Doc/SlimV2/README.md`。
4. 按 README 顺序阅读当前阶段要求的指标权威、接线合同、复用清单、已批准设计和实施计划。
5. 只读取 `progress.md` 顶部 Slim V2 状态和 `Doc/agent-navigation.md` 的 Slim V2 路由。
6. 不读取 legacy 状态、archive、Rxx 输出或历史实验数据；固定 archive 参考仍只允许复用清单中的 SHA + allowlist + `git show` 定点只读。
7. 核对本阶段输入文件存在、状态正确、没有未解决 Critical/Important 后才工作。

## 4. 四个固定阶段

### Stage 1：设计规格 owner

**唯一目标**：编写并收口 `Doc/SlimV2/slim_v2_design_spec.md`，不写实现代码。

**必须读取**：三份冻结权威、复用清单、本文和 progress 顶部。

**阶段内子 Agent**：设计草稿完成后并行分配三个只读 reviewer，分别检查：

1. Experiment 1–5 和指标/原始字段完整性；
2. 系统接线、真实 API、trace、worker/scheduler 的可实现性；
3. 精简性、representative、资源、Lean 测试裁剪和禁止依赖回流。

reviewer 不得改文件。Stage 1 owner 统一裁决、修复，并执行第二轮交叉审查。

**完成标准**：

- 设计状态为 `approved_under_user_delegation`；
- 0 个未解决 Critical，0 个未解释 Important；
- 没有冻结决定被重新开放；
- 没有 budget、receipt、digest、lineage、response-bank authority、publication gate；
- 没有未经批准的 shared-code 修改；
- focused 文档检查通过并写入 progress 顶部；
- 形成本地阶段 commit。

完成后创建 Stage 2 新任务。

### Stage 2：实施计划 owner

**唯一目标**：依据已经批准的设计编写并收口 `Doc/SlimV2/slim_v2_implementation_plan.md`，不写实现代码。

**必须读取**：README、指标权威、接线合同、复用清单、已批准设计、本文和 progress 顶部。

计划必须按 vertical slice 拆分，包含精确文件、测试、命令、预期结果和依赖顺序；一次只允许一个明确 implementation focus。至少由两个只读 reviewer 分别审查 spec coverage 和任务/测试分解。

**完成标准**：

- 计划状态为 `approved_under_user_delegation`；
- 每一条设计要求都能映射到具体 task 和验证；
- verification 任务默认不包含 Lean 专项 suite、LeanAudit、全量 catalog 或 `lake`/`lean` 回归；Lean 接线优先映射到 fake、固定 fixture 或静态合同；
- 没有占位符、模糊“以后实现”或大而不可审查的 task；
- 0 个未解决 Critical，0 个未解释 Important；
- 文档检查通过并更新 progress 顶部；
- 形成本地阶段 commit。

完成后创建 Stage 3 新任务。

### Stage 3：实施与验证总监督 owner

**唯一目标**：按已批准计划完成 Slim V2 代码和离线/focused verification；不启动真实 representative provider 调用。

**必须读取**：README、指标权威、接线合同、复用清单、已批准设计、已批准实施计划、本文和 progress 顶部。

Stage 3 owner 保留集成所有权。每个 implementation task 使用一个新的实现子 Agent，严格串行；实现子 Agent完成后依次由新的 spec reviewer 和 code-quality reviewer 审查。多个实现子 Agent不得并行修改共享文件。owner 必须亲自检查 diff 和运行 fresh verification，不能只相信子 Agent摘要。

**完成标准**：

- 实施计划中的代码任务全部有测试和验证证据；
- Factorization k>1 worker、Lean fake/固定 fixture 接线、fixed response、coverage tail、Exp2/3/4 scenario、Exp5 fake transport、result sink/resume 和 reducer focused tests 通过；
- 没有运行 Lean 专项 suite、LeanAudit、全量 Lean catalog 或 `lake`/`lean` 回归；若确实执行了最小单 case Lean smoke，已在运行前记录轻量替代无法定位的理由和时间上限；
- Exp2–4 的 provider-call=0 有测试证明；
- 字段合同与指标覆盖检查通过；
- 0 个未解决 Critical/Important；
- 最终跨组件 reviewer 通过；
- progress 顶部和设计/计划实际偏差已同步；
- 形成本地阶段 commit，工作树没有本阶段未归属修改。

完成后创建 Stage 4 新任务。

### Stage 4：Representative 运行、监督与修复 owner

**唯一目标**：使用与 full 相同的管线运行 representative，持续监督，诊断设施性失败，并分派新鲜调试/修复子 Agent，直到 representative 通过或命中强制停止条件。

**必须读取**：README、指标权威、接线合同、已批准设计、已批准计划、本文、representative profile 和 progress 顶部。不得依赖 Stage 3 的对话历史。

Stage 4 owner 负责长命令和真实 provider 调用；子 Agent只处理一个已定位的 bug 或一组独立日志，不得获得 secret，不得自行启动另一套 representative。每个修复都必须经过轻量 regression test、spec review、quality review 和重新运行受影响的 representative cell。不得额外运行 Lean suite/LeanAudit/catalog；representative 中权威要求的 Lean root 属于实验数据，不是附加 verification。模型自然失败是实验结果，不当作设施 bug。

**Representative 设施通过标准**：

- 预期 condition 均可启动；
- 每个预注册 root 都有结果行；
- Factorization/Lean/model/checker 的自然失败被如实记录；
- 没有接线导致的 infrastructure-invalid；
- Exp1 protocol/coverage-tail traces 闭合；
- Exp2–4 provider calls 为 0；
- Exp5 endpoint/model identity正确；
- reducer 生成全部 representative 指标；
- 输出可从普通文本主键恢复，secret 未落盘。

Stage 4 默认是本协议的终点。如果启动时的用户授权明确包含 `run_scope=representative_then_full`，Stage 4 通过后必须创建新的 Stage 5 Full-run owner，而不是让已经积累大量运行/修复上下文的 Stage 4 继续跑 Full。如果授权为 `run_scope=representative_only`，Stage 4 到此结束。

## 5. 阶段完成后的原子接力顺序

当前 owner 只有在本阶段 Definition of Done 全部满足后，才能按以下顺序接力：

1. 运行 fresh verification并读取完整退出状态。
2. 审查本阶段 diff，排除无关文件、secret、`local/` 输出和历史日志。
3. 更新 `progress.md` 顶部，至少记录：当前阶段、完成状态、HEAD、产物、验证命令与结果、未解决问题、下一阶段。
4. 创建本地 checkpoint commit；不要 push。
5. 生成不超过约 2,000 个中文字符的“交接胶囊”。胶囊只写不可从仓库直接推导的信息：阶段结论、关键裁决、验证证据、真正未解决的问题和下一任务目标。
6. 通过 Codex 新建任务能力创建恰好一个下一阶段顶层任务：先列出 saved projects 并选择本地路径为 `E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline` 的项目，再以该 project 的 `local environment` 创建新任务，把第 6 节模板作为完整 initial prompt。不要为这次接力创建默认 worktree，也不要只创建空任务后依赖另一条消息补全目标。
7. 使用任务等待/状态能力做一次即时确认：下一任务 ID 已产生且状态为 active/in progress。若创建仍在 setup，只能等待或重新列出任务以取得真实 thread/task ID；不要把临时 client ID传给发送消息、读取或等待工具。
8. 等待下一任务报告 `RECOVERY_HEARTBEAT_ACTIVE`；在此之前保留当前任务 heartbeat，但不得继续修改仓库。确认后禁用当前 heartbeat，避免两个阶段重复唤起。
9. 将下一任务 ID和 heartbeat 交接状态写入当前任务最终报告。此后当前 owner 不再编辑仓库或继续阶段工作。

若本阶段未完成或命中强制停止条件，禁止创建下一阶段任务。必须在 progress 顶部和最终报告记录 blocker。

## 6. 下一阶段新任务初始 prompt 模板

当前 owner 必须替换所有尖括号占位符，再把完整内容直接用于创建新任务：

```text
你是 TokenShare Slim V2 串行接力流程的 Stage <N> owner。

工作目录固定为：
E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline

分支固定为：
codex/slim-v2-baseline

本任务是全新的空白上下文。不要依赖前一任务对话。首先完整阅读：

1. AGENTS.md
2. Doc/SlimV2/README.md
3. Doc/SlimV2/slim_v2_stage_relay_protocol.md
4. 接力协议中 Stage <N> 点名的权威/设计/计划文件
5. progress.md 顶部 Slim V2 当前状态
6. Doc/agent-navigation.md 的 Slim V2 路由

读完上述启动文件后，在进行任何长工作前，按接力协议第 9 节为当前顶层任务创建或更新唯一的 30 分钟恢复 heartbeat，并在 commentary 明确报告 `RECOVERY_HEARTBEAT_ACTIVE`。如果无法建立 heartbeat，命中强制停止条件，不要假装可以无人监督运行。

当前阶段：<阶段名称>
唯一目标：<只写一个阶段目标>
run_scope：<representative_only 或 representative_then_full>

前一阶段 checkpoint：
- previous_stage=<N-1>
- previous_task_id=<真实 task/thread ID>
- branch=codex/slim-v2-baseline
- head=<完整 40 位 SHA>
- primary_artifacts=<文件路径列表>
- verification=<命令、exit code、pass/fail 数>

交接胶囊：
<不超过约 2,000 中文字符；不得粘贴整份设计、计划、diff 或日志>

严格执行接力协议中 Stage <N> 的职责、禁止项、子 Agent规则和完成标准。你是本阶段唯一顶层 owner。不要提前执行下一阶段，也不要修改本阶段范围外文件。

普通可逆实现选择由你依据冻结权威自行裁决，不要等待用户例行批准。只有接力协议第 7 节的强制停止条件才允许暂停。

本阶段完成后：

1. fresh verification；
2. 更新 progress 顶部；
3. 创建本地 checkpoint commit；
4. 按本接力模板创建恰好一个 Stage <N+1> 全新顶层任务；
5. 确认下一任务 active/in progress 且已报告 `RECOVERY_HEARTBEAT_ACTIVE`；
6. 禁用当前任务 heartbeat 后结束本任务。

如果当前是接力协议定义的终点，则生成最终报告，不创建空任务。
```

新任务必须自己重新读取仓库文档。交接胶囊不能粘贴完整文档、长日志、整段命令输出或上一对话历史；这些内容会抵消新任务隔离上下文的意义。

## 7. 整条链统一的强制停止条件

以下情况必须停止当前阶段且不得创建下一任务：

1. 冻结权威文档之间存在无法同时满足的实质冲突。
2. 继续工作必须修改实验、指标、数据集、provider、价格、fault、ablation 或 timing 语义。
3. 必须修改 shared core/local_runtime/plugin/executor，且 Slim-local adapter 无法解决。
4. 需要删除、覆盖或迁移用户数据，或需要 push/merge/PR/远端变更。
5. 固定 archive SHA/tag 不一致。
6. 当前阶段的 Critical/Important 审查问题未关闭。
7. 真实运行阶段缺 credential、resolved model 不匹配、输出不可写或磁盘不足。
8. 实际 provider-call 计划超出冻结实验的 attempt 上限。
9. 同一设施 blocker 经过系统性诊断和三种有依据的修复尝试后仍未闭合。
10. 无法在指定 local checkout 创建下一阶段的全新任务。
11. 无法为当前 Stage owner 创建或更新唯一的 30 分钟恢复 heartbeat。

模型答案错误、checker rejection、正确率低和权威实验定义内的单 root/fault失败不属于停止条件。

## 8. 上下文控制规则

- 顶层阶段 owner 不继承上一任务历史；只读取仓库、progress 顶部和短交接胶囊。
- reviewer/实现子 Agent使用最小、自包含 prompt，不要 full-history fork。
- 子 Agent返回结构化结论，不粘贴大段源码或日志。
- 只有阶段 owner 综合子 Agent结果；不得让子 Agent互相递归委派。
- 每阶段只保留一个 active focus。完成的研究和决定写入正式设计、计划、测试或 progress，不依赖聊天记忆。
- 日志和大输出留在文件/运行目录，交接只给路径、命令、退出码和摘要。
- 如果某阶段仍然过大，owner 可以在阶段内按已经批准的 task边界使用新鲜子 Agent，但不能再产生第二条顶层接力链。
- 测试默认排除 Lean 专项 suite、LeanAudit、全量 catalog 和 `lake`/`lean` 回归；不要为了“保险”重复证明系统本体。代表性/正式实验中的 Lean roots 仍按指标权威运行。

## 9. 30 分钟恢复 heartbeat

用户已经为整条接力链授权任务级恢复轮询。每个 Stage owner 在读完启动文档后、开始任何长工作前，必须用 Codex automation 能力为**当前顶层任务**创建或更新恰好一个 heartbeat：

- 名称包含 `Slim V2 Stage <N> recovery`；
- 每 30 分钟唤起一次，附着当前任务，不创建新的独立任务或新 worktree；
- 创建前先检查当前任务是否已有同名 heartbeat，存在则更新，不得创建重复项；
- heartbeat 在本阶段未完成期间保持启用；交棒时按第 5 节确认下一阶段 heartbeat 后禁用当前项；终点阶段完成后直接禁用。

每次 heartbeat 唤起必须执行一次真正的恢复循环：

1. 读取 `progress.md` 顶部、本阶段产物、工作树状态和最近 terminal/命令输出，判断阶段是否已经完成、仍在运行、意外中断或命中强制停止条件。
2. 若长命令仍存活，继续监督现有 session/process；使用短的有界等待持续读取输出，不启动重复命令。
3. 若 Agent turn、shell、provider transport 或网络连接中断，从最近已持久化 checkpoint/result key 恢复。重试真实 provider 前先检查进程、普通结果文件和 per-unit trace，避免把未知终态的既有调用盲目重复计费；任何重试仍受冻结 attempt 上限约束。
4. 若发现设施 bug，立即做系统性诊断，分派边界明确的新鲜子 Agent，并在同一次自动唤起中继续修复、验证和推进。
5. 不得只回复“仍在等待”“稍后再看”或只输出状态摘要。除非阶段已经完成或命中第 7 节强制停止条件，本次唤起必须持续推进到当前可执行工作耗尽；不能主动结束并等待下一次 30 分钟唤起。
6. 若当前 Stage owner 已有一个活跃 turn 正在推进，heartbeat 不得建立第二条写路径、重复实验或重复 provider 调用；只确认现有工作仍活跃并让唯一 owner 继续。

heartbeat 的恢复 prompt 必须包含上述六项语义，并明确写出：`CONTINUE_THIS_WAKE; DO_NOT_WAIT_FOR_NEXT_HEARTBEAT`。这只是任务恢复机制，不是实验 budget、门禁、receipt 或 evidence 系统，也不得为此向 Slim runtime 增加代码。

可直接使用以下 heartbeat prompt；创建时把 `<N>` 和 `<阶段名称>` 替换为当前值：

```text
这是 TokenShare Slim V2 Stage <N>（<阶段名称>）的 30 分钟恢复 heartbeat。检查当前任务、progress 顶部、本阶段产物、工作树、最近 terminal/进程和持久化结果。如果阶段未完成且没有命中接力协议强制停止条件，立即从当前 checkpoint 恢复，并在本次唤起中持续执行所有现有可推进工作；不得只报告状态、不得主动等待下一次 heartbeat。已有命令或 owner turn 仍活跃时只继续监督，不建立第二条写路径。重试 provider 前先核对进程、results 和 per-unit trace，禁止盲目重复调用。默认不运行 Lean 专项测试、LeanAudit、全量 catalog 或 lake/lean 回归。阶段完成时执行规定的 checkpoint/交棒或终点收口。

CONTINUE_THIS_WAKE; DO_NOT_WAIT_FOR_NEXT_HEARTBEAT
```

## 10. 第一棒的最短启动方式

用户只需要在 Stage 1 新任务中发送：

```text
请在 E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline 开始 Slim V2 串行接力流程。

完整阅读 AGENTS.md、Doc/SlimV2/README.md 和 Doc/SlimV2/slim_v2_stage_relay_protocol.md，然后担任 Stage 1 设计规格 owner。开始长工作前，先为当前任务创建或更新唯一的 30 分钟恢复 heartbeat，并报告 RECOVERY_HEARTBEAT_ACTIVE；每次自动恢复都必须在本次唤起持续推进，不能只报状态后等待下一轮。严格按接力协议完成设计、只读子 Agent审查、委托审批、验证和本地 checkpoint；完成后自行创建并启动 Stage 2 的全新 Codex 任务。后续每一阶段都必须继续按同一接力协议创建下一阶段新任务和自己的 heartbeat，不要等待我做例行批准。默认不运行 Lean 专项测试、LeanAudit、全量 Lean catalog 或 lake/lean 回归；权威实验要求的 Lean roots 除外。

run_scope=representative_only
```

如果用户已经明确授权 representative 通过后继续 Full，则把最后一行改为：

```text
run_scope=representative_then_full
```

这段启动 prompt 只负责选择工作目录、第一阶段和最终运行范围；全部阶段职责、审查、交接和停止条件都以本文为准。
