---
status: user_authorized_workflow
document: slim_v2_stage_relay_protocol
scope: Serial fresh-task handoff for Slim V2 design, planning, implementation, and representative execution
---

# Slim V2 串行新任务接力协议

本文只定义 Slim V2 如何在多个上下文彼此隔离的新 Codex 任务之间串行接力。它不定义实验参数、指标、系统接口或实现方案，不能覆盖四份冻结前置权威文档。

## 1. 为什么使用新任务接力

Slim V2 跨越设计、实施计划、代码实现和真实运行。让一个 Agent 从头工作到尾会持续累积对话、命令输出和调试历史，容易造成上下文膨胀与目标漂移。因此，Stage 1、Stage 2、Stage 4及以后每个大阶段由一个新的顶层 Codex 任务负责；Stage 3 的六个大型 implementation task 则进一步各由一个全新的顶层 Codex 任务负责，任何一个 Stage 3 owner 都不得监督或实施两个大型 task。每个顶层任务内部才使用临时子 Agent 做只读审查或边界明确的实现工作。

两类协作必须区分：

| 类型 | 用途 | 上下文 | 是否负责下一接力单元 |
|---|---|---|---|
| 顶层新任务 | 设计、计划、Stage 3单个大型task、representative监督等接力单元 | 新任务从空白对话开始，只读取明确文档和交接摘要 | 是；当前owner只能创建一个下一接力单元顶层任务 |
| 当前任务的子 Agent | 专项审查、单个 implementation task、故障定位 | 只接收该子任务所需的最小上下文 | 否；子 Agent 不得创建下一接力单元顶层任务或递归扩张团队 |

接力必须使用“创建全新任务”的能力。不要使用会继承完整已完成对话的 fork 代替空白任务。若下一接力单元任务已经由用户预先创建，可以向其发送消息；否则由当前 owner 创建新任务，并把完整初始 prompt 一次性写入新任务。

## 2. 固定仓库和任务环境

- 工作目录：`E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline`
- 分支：`codex/slim-v2-baseline`
- 所有接力单元在同一 local checkout 上串行工作；任一时刻只能有一个顶层接力单元写入。
- 创建下一接力单元任务时，必须选择该 Slim V2 saved project 的 **local environment**，不能让工具默认从仓库默认分支创建另一个普通 worktree。否则下一任务可能从 `main` 启动并看不到当前成果。
- 当前 owner 创建下一任务后不得继续修改文件。允许短暂读取下一任务状态，确认其已进入运行或明确失败，然后当前 owner 结束。
- 不 push、merge、创建 PR 或修改远端。阶段 checkpoint 只使用当前本地分支的小粒度 commit。

如果无法创建指向上述 local checkout 的全新任务，不得用 full-history fork 冒充。当前owner先按第7.1节执行代理授权法定人数；若裁决仍为`safe_no_action`或工具客观不可用，则把已生成的下一阶段prompt和证据写入最终报告并停止，不询问或等待用户手动创建。

## 3. 所有阶段共同遵守的启动顺序

每个新的阶段 owner 都必须从仓库事实重新建立上下文，不能把上一 Agent 的摘要当权威：

1. 确认仓库根目录、分支、HEAD 和工作树状态。
2. 完整阅读根目录 `AGENTS.md`。
3. 完整阅读 `Doc/SlimV2/README.md`。
4. 按 README 顺序阅读当前阶段要求的设计宪章、指标权威、接线合同、复用清单、已批准设计和实施计划。
5. 只读取 `progress.md` 顶部 Slim V2 状态和 `Doc/agent-navigation.md` 的 Slim V2 路由。
6. 不读取 legacy 状态、archive、Rxx 输出或历史实验数据；固定 archive 参考仍只允许复用清单中的 SHA + allowlist + `git show` 定点只读。
7. 核对本阶段输入文件存在、状态正确、没有未解决 Critical/Important 后才工作。

## 4. 四个固定阶段

### Stage 1：设计规格 owner

**唯一目标**：编写并收口 `Doc/SlimV2/slim_v2_design_spec.md`，不写实现代码。

**最高优先级**：以最快速度得到真实有效实验结果，只保留冻结指标不可缺少的功能。reviewer不得假设人为伪造、恶意篡改、注入攻击或其他设计宪章已排除的攻击者模型；此类意见由owner标记`out_of_scope_by_user`并拒绝实施，不进入Critical/Important计数。

**必须读取**：四份冻结前置权威、复用清单、本文和 progress 顶部。

**阶段内子 Agent**：设计草稿完成后并行分配三个只读 reviewer，分别检查：

1. Experiment 1–5 和指标/原始字段完整性；
2. 系统接线、真实 API、trace、worker/scheduler 的可实现性；
3. 精简性、representative、资源、Lean 测试裁剪和禁止依赖回流。

reviewer 不得改文件。Stage 1 owner 统一裁决、修复，并执行第二轮交叉审查。审查不能把设计宪章明确排除的攻击/防伪需求重新包装成“数据完整性”“安全性”“鲁棒性”或“论文严谨性”。

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

### Stage 3：实施与验证（六个大型 task 逐棒 owner）

**唯一目标**：按已批准计划完成 Slim V2 代码和离线/focused verification；不启动真实 representative provider 调用。

**必须读取**：README、指标权威、接线合同、复用清单、已批准设计、已批准实施计划、本文和 progress 顶部。

Stage 3 严格由获批实施计划中的六个大型 task 组成。每个大型 task 是一个独立顶层接力单元，分别由一个全新的 Stage 3 Task 1/6 至 Task 6/6 owner 负责；每位owner的唯一实施范围就是当前一个大型task。owner可按风险驱动验证矩阵使用边界明确的实现或只读review子Agent，但必须亲自检查diff和运行fresh verification，不能只相信子Agent摘要。任何owner不得提前实现下一大型task、继续担任下一大型task owner，或以“保持集成所有权”为由监督完六个task。Task之间的强制轮换和原子交棒按第5.1节执行。

若Stage 3启动前存在重规划前的已提交或未提交实现，首位owner先按第5.2节执行一次`rebaseline existing implementation`。该审计只把既有成果映射到新计划并保护工作树，不增加第七个task，也不能替代Task 1的纵向验收。

**每个大型task的完成标准**：

- 当前task在实施计划中点名的可运行纵向切片、风险驱动验证、review要求和完成证据全部闭合；
- 当前task范围内0个未解决Critical/Important；
- owner fresh检查diff、运行focused verification、更新progress顶部并创建当前task本地checkpoint commit；
- Task 1–5按第5.1节创建且只创建下一大型task的全新owner，确认其heartbeat active后结束；
- Task 6除完成本task外，还必须满足以下Stage 3总完成标准，然后创建Stage 4 owner。

**Task 6的Stage 3总完成标准**：

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

当前 owner 只有在本阶段或当前Stage 3大型task的Definition of Done全部满足后，才能按以下顺序接力：

1. 运行 fresh verification并读取完整退出状态。
2. 审查当前接力单元 diff，排除无关文件、secret、`local/` 输出和历史日志。
3. 更新 `progress.md` 顶部，至少记录：当前阶段/Stage 3 task编号、完成状态、HEAD、产物、验证命令与结果、未解决问题、下一接力单元。
4. 创建本地 checkpoint commit；不要 push。
5. 生成不超过约 2,000 个中文字符的“交接胶囊”。胶囊只写不可从仓库直接推导的信息：阶段结论、关键裁决、验证证据、真正未解决的问题和下一任务目标。
6. 通过 Codex 新建任务能力创建恰好一个下一接力单元顶层任务：Stage 3 Task 1–5创建下一编号的Stage 3 task owner，其他阶段或Stage 3 Task 6创建下一阶段owner。先列出 saved projects 并选择本地路径为 `E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline` 的项目，再以该 project 的 `local environment` 创建新任务，把第 6 节模板作为完整 initial prompt。不要为这次接力创建默认 worktree，也不要只创建空任务后依赖另一条消息补全目标。
7. 使用任务等待/状态能力做一次即时确认：下一任务 ID 已产生且状态为 active/in progress。若创建仍在 setup，只能等待或重新列出任务以取得真实 thread/task ID；不要把临时 client ID传给发送消息、读取或等待工具。
8. 等待下一任务报告 `RECOVERY_HEARTBEAT_ACTIVE`；在此之前保留当前任务 heartbeat，但不得继续修改仓库。确认后禁用当前 heartbeat，避免两个阶段重复唤起。
9. 将下一任务 ID、Stage 3 task编号（适用时）和heartbeat交接状态写入当前任务最终报告。此后当前owner不再编辑仓库、继续当前阶段工作或监督下一大型task。

若当前接力单元未完成或命中强制停止条件，禁止创建下一接力单元任务。原本需要用户授权/选择才能解除的 blocker 必须先执行第7.1节的代理授权法定人数流程；只有该流程裁决为安全不行动、客观条件仍缺失或问题仍不可闭合时，才在 progress 顶部和最终报告记录 blocker。

### 5.1 Stage 3 六个大型 task 的强制 owner 轮换

Stage 3 的六个大型task必须由六个连续、互不复用上下文的顶层owner串行完成，固定记为`Stage 3 Task 1/6`至`Stage 3 Task 6/6`。该划分是唯一Stage 3顶层接力链，不是六条并行分支，也不改变实施计划中的task语义。

1. 每位owner启动时只接收当前一个大型task的唯一目标、允许写入、权威引用、上一task checkpoint和不超过约2,000中文字符的交接胶囊；不得fork上一owner完整历史。
2. 每位owner必须作为当前task的主监督智能体：把具体实现分派给范围明确的子智能体，并把独立spec审计和implementation-quality review分派给与实现者分离的子智能体；owner保留范围裁决、冲突处理、集成、最终验证、checkpoint commit和接力责任，不得亲自包办全部实现与审计/review。
3. 当前owner只能实现、验证、审查和提交当前task。即使剩余上下文充足、下一task看似简单，也禁止继续实施、分派或监督下一task。
4. Task 1–5完成后，当前owner按第5节创建恰好一个下一编号的全新顶层Stage 3 owner；Task 6完成并满足Stage 3总完成标准后，创建Stage 4 owner。不得创建空的“总监督owner”悬在六个task之上。
5. 下一owner必须使用同一saved project的local environment、同一branch和当前working tree；不得创建默认worktree、full-history fork或复制/cherry-pick当前提交。
6. 每个owner拥有恰好一个名称包含`Slim V2 Stage 3 Task <K> recovery`的30分钟heartbeat。下一owner active/in progress且明确报告`RECOVERY_HEARTBEAT_ACTIVE`前，当前owner保留自己的heartbeat但停止写入；确认后立即禁用当前heartbeat。任一时刻只能有一个Stage 3实现写owner。
7. 每棒progress顶部和交接胶囊必须记录`stage=3`、`task_index=K/6`、当前task checkpoint SHA、允许/遗留dirty文件、verification、review结论、未解决问题、下一task唯一目标和两个heartbeat状态。旧task完成证据不能仅存在于聊天。
8. 创建下一棒时，第6节完整initial prompt及其中的交接胶囊必须显式传递第2项主监督与子智能体分工规则，不能只依赖下一owner自行从文档推导。
9. 从Task 1交至Task 6的每棒initial prompt/胶囊还必须持续传递Task 6预留preflight边界：只允许防止程序失控、磁盘耗尽、重复调用或重复付费的运行安全检查；禁止价格/余额审批、`budget authority`、人工授权、`publication readiness`和`evidence completeness`，价格表缺失或变化不得阻止实验。该边界只约束未来Task 6，不得前移为当前Task 2–5的实现范围。
10. 当前task未满足完成标准或命中第7节时，禁止创建下一task owner。heartbeat恢复只能恢复当前task，不能借自动唤起跨到下一task。

### 5.2 下游阶段已启动后的上游重规划接力

若设计或实施计划在下游Stage已经产生committed或dirty实现后重新开放，必须执行本小节，不能把普通Stage回退或重新开始当作清理工作树的理由。

1. 立即暂停旧下游owner并在`progress.md`顶部标记`superseded_by_replan`；不得标记completed。旧heartbeat必须禁用或删除，旧owner不得再按被替代计划写入。
2. 保留全部既有committed与dirty成果；禁止为了回到上游Stage而`reset`、`revert`、`stash`、删除未跟踪文件或复制/cherry-pick既有提交。上游新checkpoint可以成为旧下游提交的Git后继；Stage编号表示逻辑权威顺序，不要求Git历史倒退。
3. 上游owner必须生成旧task到新task的逐文件映射。旧编号、旧测试通过或旧review不能自动证明新task完成；兼容成果只能标为待新纵向事实校准的可复用输入。
4. 上游文档checkpoint只提交获准文档，不得混入未完成实现。dirty实现可以原样继承，但交接胶囊必须列出文件、性质、已知测试、未完成review和`not complete`状态。
5. 新下游owner必须使用同一saved project的local environment、同一branch和working tree，先执行`rebaseline existing implementation against revised plan`，再进入新计划第一个未闭合纵向task。rebaseline是开工审计，不新增task编号。
6. 任一时刻只有一个下游实现owner和一个active实现heartbeat。旧下游heartbeat禁用后才创建恰好一个fresh owner；fresh owner active并报告`RECOVERY_HEARTBEAT_ACTIVE`后，上游owner才禁用自己的heartbeat。
7. 强制交接胶囊至少包含branch/HEAD、上游checkpoint、既有实现commits、dirty清单、旧到新映射、verification与未review状态、下一focus、权限/禁止项、旧线程和heartbeat状态。
8. 若新权威要求删除或收窄已有实现，必须由获批文档和纵向系统事实证明；禁止仅为获得clean工作树而删除。

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
当前接力单元：Stage <N><若为Stage 3则填写 Task <K>/6>
唯一目标：<只写一个阶段目标>
run_scope：<representative_only 或 representative_then_full>

前一接力单元 checkpoint：
- previous_stage=<N-1；若当前为Stage 3 Task K且K>1则填写3>
- previous_task_index=<仅Stage 3填写上一棒J/6；其他填写not_applicable>
- previous_task_id=<真实 task/thread ID>
- branch=codex/slim-v2-baseline
- head=<完整 40 位 SHA>
- primary_artifacts=<文件路径列表>
- verification=<命令、exit code、pass/fail 数>

交接胶囊：
<不超过约 2,000 中文字符；不得粘贴整份设计、计划、diff 或日志>

严格执行接力协议中 Stage <N> 的职责、禁止项、子Agent规则和完成标准。你是当前接力单元的唯一顶层owner。若当前为Stage 3 Task <K>/6，你只负责该一个大型task，不得提前实施或监督Task <K+1>/6；不要修改当前接力单元范围外文件。

普通可逆实现选择由你依据冻结权威自行裁决，不要等待用户例行批准。只有接力协议第 7 节的强制停止条件才允许暂停。

本阶段完成后：

1. fresh verification；
2. 更新 progress 顶部；
3. 创建本地 checkpoint commit；
4. 创建恰好一个下一接力单元的全新顶层任务：Stage 3 Task 1–5创建Task <K+1>/6，Stage 3 Task 6或其他阶段创建下一Stage；
5. 确认下一任务 active/in progress 且已报告 `RECOVERY_HEARTBEAT_ACTIVE`；
6. 禁用当前任务 heartbeat 后结束本任务。

如果当前是接力协议定义的终点，则生成最终报告，不创建空任务。Stage 3不得把六个大型task交给同一顶层owner连续执行。
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

### 7.1 用户睡眠期间的代理授权法定人数（Quorum Delegation）

`2026-08-21`用户明确决定：本次Slim V2接力流程中，凡现有协议、AGENTS、设计、计划或实施发现原本要求“询问用户”“请求用户批准”“由用户选择”的事项，一律不等待或追问用户，改由独立子Agent法定人数作出代理授权。该决定在用户明确撤销前对后续所有Stage owner生效。

执行步骤固定如下：

1. owner先暂停争议动作，形成同一份最小证据包：待裁决问题、权威条款、事实证据、互斥方案、各方案是否改变实验/指标/数据集/provider/价格/fault/ablation/timing/shared接口、允许写入范围、回滚方式和验证命令；不得把偏好写成既定结论。
2. 同时分派三名全新、互相独立、只读的子Agent。三者必须完整阅读并遵守`$pua:pua`、设计宪章第0节和与问题直接相关的权威条款；prompt与证据包相同，不得看到另外两名的输出，不得改文件、运行真实provider或扩大实验。
3. 每名reviewer必须返回一个明确的`recommendation_id`、`authorize=yes|no`、精确授权动作/写入范围、理由、影响的冻结语义、风险与必需验证；不能只列选项或把决定退回用户。
4. owner只按实质动作归一化建议，不按措辞拆票。三票中至少两票给出相同`recommendation_id + authorize`时，该多数结论立即成为`approved_under_user_delegation_by_quorum`，owner直接执行，不询问用户许可。
5. 若三份建议实质上全部不同，立即分派第四名全新只读auditor。第四名读取相同证据包和三份原始意见，必须在三案中选择一案，或裁决`safe_no_action`；其结论为本轮绑定裁决。不得递归增加第五名，也不得把问题退回用户。
6. owner把reviewer任务ID、三票（以及适用时第四审计）、多数/审计结论、授权范围和验证证据写入当前设计/计划与`progress.md`顶部。这只是接力治理记录，不得进入Slim runtime成为approval gate、receipt、digest、lineage或publication state。
7. 若用户直接确认或法定人数结论改变了任何现行权威文件中的实验/指标语义、派生数值、公共接口、设计合同或验收标准，owner必须在继续下游task或交棒前同步修改全部受影响的Slim权威、设计、计划和当前progress正文；只在quorum/evidence备注中声明supersede、却保留相互冲突的权威正文，视为未完成。同步后必须跨`Doc/SlimV2/`检索旧表述并分派全新只读reviewer做二次一致性审查。该规则构成受影响`Doc/SlimV2/`文件的明确写入例外，但只用于消除已确认裁决造成的文档漂移，不扩大runtime/shared/provider、实验数据集、`run_scope`、调用上限、安全范围或远端/破坏性权限。

除上一条由用户明确授权的受影响Slim文档同步例外外，法定人数只能在用户已经授权的本地Slim V2任务边界内替代用户选择，不能覆盖system/developer指令、工具权限或法律/平台限制，也不能自行扩大`run_scope`、突破provider attempt上限、启用明确禁止的攻击/安全范围、push/merge/PR、删除/迁移用户数据或创建额外顶层接力链。对这些不可授权动作，reviewer只能选择现有范围内方案或`safe_no_action`，仍不得询问用户。

#### 7.1.1 当前 Experiment 4 结构旁路裁决（2026-08-21）

- 用户明确说明该设计、审核、权威同步和docs-only checkpoint不属于任何Stage/Task；获批代码实施归入既有Stage 3 Task 4。不得因此新建Task 4或改变六Task串行链。
- 用户直接批准为V/P/R/M与必要pair设计结构性局部旁路，并授权已证明shared gap的最小修复；同时要求重大决策使用三Agent投票且不询问用户。
- 首轮同证据三票均为`RECOVERY_MERGE_FIRST + authorize=no`；设计修正V/P真实调用边界、typed incomplete rejection、nullable root check、evidence-only projector和默认零影响shared seam后，第二轮三票均为`RECOVERY_MERGE_FIRST + authorize=yes`，范围内`Critical/Important=0/0`。
- 获批方案为`slim_v2_exp4_structural_bypass_design.md`。`{R,M}`中M抢先时R=`preempted_by_merge_first`且stuck=false；不得恢复旧双阳性断言。
- 本轮完成全部权威同步、fresh一致性审查和docs-only checkpoint后，必须重新唤起同一Task `01a0238e-b2db-7da1-9d6c-60bb12d79ecd`，发送checkpoint SHA与获批方案，要求其继续Task 4及其余内容；不得创建替代Task。

## 8. 上下文控制规则

- 顶层阶段 owner 不继承上一任务历史；只读取仓库、progress 顶部和短交接胶囊。
- reviewer/实现子 Agent使用最小、自包含 prompt，不要 full-history fork。
- 子 Agent返回结构化结论，不粘贴大段源码或日志。
- 只有阶段 owner 综合子 Agent结果；不得让子 Agent互相递归委派。
- 每阶段只保留一个 active focus。完成的研究和决定写入正式设计、计划、测试或 progress，不依赖聊天记忆。
- 日志和大输出留在文件/运行目录，交接只给路径、命令、退出码和摘要。
- Stage 1、2、4及以后若仍然过大，owner可以在阶段内按已批准边界使用新鲜子Agent，但不能产生第二条顶层接力链。Stage 3是明确例外：六个大型task必须按第5.1节形成同一条串行顶层接力链，每task强制更换owner。
- 测试默认排除 Lean 专项 suite、LeanAudit、全量 catalog 和 `lake`/`lean` 回归；不要为了“保险”重复证明系统本体。代表性/正式实验中的 Lean roots 仍按指标权威运行。

## 9. 30 分钟恢复 heartbeat

用户已经为整条接力链授权任务级恢复轮询。每个Stage owner或Stage 3大型task owner在读完启动文档后、开始任何长工作前，必须用Codex automation能力为**当前顶层任务**创建或更新恰好一个heartbeat：

- 名称包含`Slim V2 Stage <N> recovery`；Stage 3必须进一步包含task编号：`Slim V2 Stage 3 Task <K> recovery`；
- 每 30 分钟唤起一次，附着当前任务，不创建新的独立任务或新 worktree；
- 创建前先检查当前任务是否已有同名 heartbeat，存在则更新，不得创建重复项；
- heartbeat 在当前接力单元未完成期间保持启用；交棒时按第 5 节确认下一接力单元 heartbeat 后禁用当前项；终点阶段完成后直接禁用。

每次 heartbeat 唤起必须执行一次真正的恢复循环：

1. 读取 `progress.md` 顶部、当前接力单元产物、工作树状态和最近 terminal/命令输出，判断当前单元是否已经完成、仍在运行、意外中断或命中强制停止条件。
2. 若长命令仍存活，继续监督现有 session/process；使用短的有界等待持续读取输出，不启动重复命令。
3. 若 Agent turn、shell、provider transport 或网络连接中断，从最近已持久化 checkpoint/result key 恢复。重试真实 provider 前先检查进程、普通结果文件和 per-unit trace，避免把未知终态的既有调用盲目重复计费；任何重试仍受冻结 attempt 上限约束。
4. 若发现设施 bug，立即做系统性诊断，分派边界明确的新鲜子 Agent，并在同一次自动唤起中继续修复、验证和推进。
5. 不得只回复“仍在等待”“稍后再看”或只输出状态摘要。命中第7节且原本需要用户裁决时，必须在同一次唤起中启动或续完第7.1节法定人数流程；除非阶段已经完成、法定人数裁决`safe_no_action`或客观条件仍不可满足，本次唤起必须持续推进到当前可执行工作耗尽，不能主动结束并等待下一次30分钟唤起。
6. 若当前 Stage owner 已有一个活跃 turn 正在推进，heartbeat 不得建立第二条写路径、重复实验或重复 provider 调用；只确认现有工作仍活跃并让唯一 owner 继续。

heartbeat 的恢复 prompt 必须包含上述六项语义，并明确写出：`CONTINUE_THIS_WAKE; DO_NOT_WAIT_FOR_NEXT_HEARTBEAT`。这只是任务恢复机制，不是实验 budget、门禁、receipt 或 evidence 系统，也不得为此向 Slim runtime 增加代码。

可直接使用以下 heartbeat prompt；创建时把 `<N>`、`<阶段名称>` 和（Stage 3适用时）`<K>` 替换为当前值：

```text
这是 TokenShare Slim V2 Stage <N>（<阶段名称><若为Stage 3则填写 Task <K>/6>）的 30 分钟恢复 heartbeat。检查当前任务、progress 顶部、当前接力单元产物、工作树、最近 terminal/进程和持久化结果。如果当前接力单元未完成，立即从当前 checkpoint 恢复，并在本次唤起中持续执行所有现有可推进工作；命中原本需要用户裁决的强制停止条件时，立即执行第7.1节三名独立reviewer法定人数流程，三案全异再交第四auditor，不询问用户。不得只报告状态、不得主动等待下一次 heartbeat。已有命令或 owner turn 仍活跃时只继续监督，不建立第二条写路径。重试 provider 前先核对进程、results 和 per-unit trace，禁止盲目重复调用。默认不运行 Lean 专项测试、LeanAudit、全量 catalog 或 lake/lean 回归。当前接力单元完成时执行规定的 checkpoint/交棒或终点收口。

CONTINUE_THIS_WAKE; DO_NOT_WAIT_FOR_NEXT_HEARTBEAT
```

## 10. 第一棒强约束启动 prompt

文档阅读不能替代 prompt 内的硬约束。用户在 Stage 1 新任务中发送以下完整 prompt；Stage 1 owner 必须同时服从 prompt 与仓库权威，不能以“细节已在文档中”为由忽略这里的边界：

```text
请在以下现有本地 checkout 中启动 TokenShare Slim V2 串行接力流程：

工作目录：E:\TokenEcnomic\TokenShareWorktrees\slim-v2-baseline
固定分支：codex/slim-v2-baseline
环境：该 saved project 的 local environment

不得切换到 main，不得创建默认 worktree，不得 checkout archive，不得 push、merge 或创建 PR。以启动时该分支的当前 HEAD 为 checkpoint；先确认工作树状态，保留任何已有用户修改。

你是 Stage 1：Slim V2 设计规格 owner。本阶段唯一交付物是：

Doc/SlimV2/slim_v2_design_spec.md

本阶段只能编写和审查设计文档，不得编写 Slim V2 实现代码，不得启动 representative/full，不得调用真实 provider。

最高设计指示：Slim V2以最快速度得到真实有效的论文实验结果为唯一核心，只保留产生冻结指标不可缺少的功能。“真实有效”指真实provider、真实系统本体/插件/checker运行和正确统计口径，不表示需要证明本地文件无人伪造。任何新增组件都承担举证责任；不能指出它直接服务的冻结实验、必需字段或不可恢复故障时，禁止加入。

本项目假设受信本地研究环境。人为伪造、手工篡改、路径/链接/SQL/JSON/prompt/命令注入、恶意plugin/executor/provider envelope、签名鉴权、权限系统、安全fuzzing和攻击者模型全部明确排除。不得为这些威胁增加hash、digest、receipt、lineage、审计、sandbox或gate。Experiment 3/4的冻结fault/challenge只是论文实验变量，不得扩张成通用安全防护。reviewer若提出上述意见，必须标记out_of_scope_by_user并拒绝实施，不计入Critical/Important，也不能阻塞阶段完成。

一、启动阅读与权威顺序

必须完整阅读并按以下优先级执行：

1. AGENTS.md
2. Doc/SlimV2/README.md
3. Doc/SlimV2/slim_v2_stage_relay_protocol.md
4. Doc/SlimV2/slim_v2_design_charter.md
5. Doc/SlimV2/slim_v2_experiment_metrics_authority.md
6. Doc/SlimV2/slim_v2_system_integration_contract.md
7. Doc/SlimV2/slim_v2_reuse_inventory.md
8. progress.md 顶部 Slim V2 状态
9. Doc/agent-navigation.md 的 Slim V2 路由

指标权威决定论文实验和指标；接线合同决定系统公共接口；复用清单只能提供参考位置，不能反向定义设计。新增实验内容或指标在旧代码中不存在是正常情况，禁止为了迁就旧代码删减、改名或弱化权威要求。现有代码只能被选择性复用，不能成为设计规格的上位约束。

禁止广泛阅读 legacy paper/formal pipeline、Doc/archive、历史 Rxx、local 历史输出、TokenShareData 历史结果或旧 session-handoff。legacy 代码仅允许按 reuse inventory 固定 SHA、allowlist 路径和符号用 git show 定点只读。

二、Slim V2 不可改变的总目标

设计一个独立、最小、可运行的论文实验设施。它只负责：

1. 把冻结实验参数接入现有 TokenShare 系统本体；
2. 调用现有 Factorization 插件和真实 Lean 插件完成实验；
3. 在 Experiment 1 和 Experiment 5 执行必要的真实 AI API 调用；
4. 调度 Experiment 1–5 的冻结场景；
5. 保存计算全部论文指标所需的最小原始数据；
6. 从普通输出目录离线计算 CSV/JSON 指标；
7. 支持中断恢复和跳过已完成的普通样本。

这不是旧实验设施的重构，也不是生产平台。优先删除非必要抽象、门禁、authority 和审计层。设计不得因为“以后可能有用”加入权威实验没有要求的设施。

三、必须排除的设施

设计和未来实现都不得重新引入或依赖：

- budget authority、预算审批或预算门禁；
- receipt；
- hash/digest 防伪链；
- lineage/evidence closure；
- publication gate、paper eligibility；
- response-bank authority 或独立回答库；
- selection digest、prepared identity、hard-deadline child gate；
- 旧 paper/formal runner、旧 publication pipeline；
- 为证明数据未伪造而增加的审计系统；
- 任何会阻止实验运行的价格、余额或发布资格检查。

官方价格表只是普通静态成本换算常量，不是预算设施或门禁。Agent审查属于开发工作流，不得被实现成 Slim V2 runtime gate。

四、代码与复用边界

未来常规写入范围只能是：

- src/tokenshare/experiments/slim_v2/
- tests/experiments/slim_v2/
- Doc/SlimV2/

shared core、local_runtime、Factorization/Lean plugin、executor 和 storage 默认只读。设计必须优先使用 Slim-local adapter。只有权威合同证明存在明确接口缺口、Slim-local adapter 确实无法解决时，才能记录为强制停止问题；不得预先设计 shared-code 改造。

真实 API 只复用底层请求构造、HTTP transport、provider envelope 解析以及 usage/model 提取能力；不得复用臃肿的 AIAPIExecutor 整类，也不得继承其 selection、预算、prepared identity、hard deadline 或 evidence 依赖。设计一个薄的 single-entry provider caller/bridge：输入单一 provider entry、prompt 和请求控制，输出 raw response、usage、latency、错误及 configured/requested/resolved model。

系统 runtime、worker、scheduler、恢复逻辑和插件公共接口可以按接线合同复用。旧指标代码只能借公式或局部纯逻辑，不能继承 contract、receipt、lineage、publication gate 或完整旧 runner。

五、冻结实验语义

设计必须覆盖 Experiment 1–5 的全部冻结 condition、原始字段和指标，且提供“指标 → 原始字段 → 产生组件 → 输出文件”的完整映射。不得用现有代码缺失为理由删除指标。

以下语义不得重新讨论或改变：

1. 所有 roots 在 runner 层串行；worker_count 只控制单 root 内 AI-unit 并发。
2. 除 Experiment 2 外，Experiment 1、3、4、5 固定 worker_count=10；Experiment 2 固定使用 1/3/7/10/30/50。
3. Experiment 1 每个 root 分为正常协议阶段和紧随其后的 coverage tail。正常 run_root() terminal 后，tail 只补 unscheduled planned units，不重复调用 protocol units；trace_origin=protocol/coverage_tail。tail 完成后才能开始下一 root。
4. root_terminal_at_ms 在正常协议终止时冻结；coverage tail 不延长 Experiment 1 正文 runtime。tail wall-clock、tokens 和 cost 必须单列。
5. Experiment 2 不使用独立 split，必须原样继承 Experiment 1 的任务计划、子任务范围、planned_ai_unit_id、prompt 和依赖。
6. Experiment 2–4 只消费 Experiment 1 source_repeat_id=0 的普通 per-unit traces；来源键固定为 case_id × source_repeat_id × planned_ai_unit_id，并核对 Factorization range 或 Lean lemma/dependency 普通字段。
7. Experiment 2–4 provider calls 必须为 0。请求 ordinal 不存在时，确定性回退到同一 trace 最后一个自然 attempt；不得新增 AI 调用，不得换 unit，不得 transport fallback。
8. Experiment 2 使用同一批任务、回答和 source latency进入不同容量的逻辑调度器，不能用 Experiment 1 总时间除以 worker_count。
9. Experiment 3 严格使用权威规定的五类 fault、planned first-attempt 分母、ordinal 0 注入、replacement 和 seed=20260820 的 token/latency 扰动；资源必须标为 simulated trace-attributed，不能称为当次真实 provider usage。
10. Experiment 4 固定 FULL、四个单机制和六个双机制共 11 modes，使用 mode-blind 确定性 challenge；不得恢复旧 mode 或离线拼接双机制结果。
11. Experiment 5 使用冻结的四个 SiliconFlow endpoint、相同 hard roots、零重试和真实 provider usage。
12. 前向 Experiment 1 使用 `deepseek-v4-flash` 与 `slim_v2.pricing.2026-08-23` 的 flat 成本：cache-hit/cache-miss/output 分别为 `0.05/1.5/4.5 CNY per 1M tokens`；Experiment 5 保持 `slim_v2.pricing.2026-08-20` 的 SiliconFlow flat 成本。两者都只是普通成本换算常量，不形成价格门禁；reasoning_tokens 是 completion_tokens 子集，禁止重复计价。迁移前已启动 exact run 的 `deepseek-v4-pro` 峰/谷记录仅是不可改写的历史 cohort 事实，不得重标为 Flash 或前向价格规则。
13. 单 root 失败仍必须写结果行，固定分母不得删除失败、超时或未恢复 root。

六、原子运行与复用要求

Slim V2 必须支持原子化命令和组合命令：

- 单独运行 Experiment 1；
- 单独运行 Experiment 2、3 或 4，并显式选择某一组已完成的 Experiment 1 traces；
- 单独运行 Experiment 5；
- 一条命令按依赖顺序运行全部实验；
- 单独运行 reducer；
- 单独运行 representative；
- 在普通文本主键已经完成时安全 resume/skip。

Experiment 2–4 不得隐式启动 Experiment 1，也不得重新取得回答。依赖输入缺失时必须明确报告普通接线/输入错误，不能回退到旧输出或实时 API。

七、输出与资源约束

输出根固定设计为普通目录：

TokenShareData/outputs/slim_v2/<run_id>/

只使用普通 JSONL/JSON/CSV 和 responses/或等价的原始响应文件。reducer 必须只靠该 run 目录工作，不读取数据库 authority、旧实验输出或聊天上下文。

设计必须适合全量实验：

- roots 串行；
- 逐 root/attempt 增量写盘；
- 不把全量 responses、events 或结果一次性载入内存；
- reducer 支持流式/分块聚合，只有确有必要的分组状态驻留内存；
- 文件句柄、线程、进程和临时文件有明确生命周期；
- bounded queue/backpressure；
- 崩溃后按普通 case/condition/repeat/unit 主键恢复；
- 不重复调用已持久化的真实 provider unit；
- 单 root 失败不能导致整批结果丢失；
- 明确磁盘空间预估、响应文件布局、日志轮转/上限和 CPU/内存并发上限。

八、representative 强约束

representative 必须与 full 使用完全相同的 CLI、runner、adapter、provider caller、输出 schema、resume 和 reducer，只能通过 profile 缩小数据集/condition规模，禁止另写 representative runner 或 mock 掉 full 才会使用的关键路径。

设计必须冻结一个最低成本 profile：Experiment 1 至少两道 Factorization 和两道 Lean；Experiment 5 至少一道 Factorization；Experiment 2–4按依赖关系等比例缩小但仍覆盖各自关键 worker/fault/ablation路径。设计文档必须列出精确 representative condition/root 清单、选择规则、预期 provider-call 上限和通过标准，不能只写“选少量样本”。

representative 的目标是验证设施和接线，不要求模型答案全部正确。模型自然错误、checker rejection或低正确率是实验结果；缺行、错误 provider 调用、schema 不完整、trace 错配、resume 失败才是设施问题。

九、Lean 测试裁剪

默认禁止 Lean 专项 suite、LeanAudit、全量 Lean catalog 和 lake/lean 回归，也不要为了重新证明现有系统本体正确而运行 Lean 测试。并发、调度、恢复、输出和 reducer 优先用 Factorization、fake checker、固定 fixture 或静态合同测试。

只有 Slim-local Lean 接线无法通过轻量方法定位时，才允许预先记录理由与时间上限后运行最小单 case Lean smoke，禁止扩大为 suite。权威实验和 representative中要求的 Lean roots 仍是真实实验样本，不得从实验设计中删除。

十、设计规格必须包含的章节

slim_v2_design_spec.md 至少必须完整定义：

1. 目标、非目标和不可违反约束；
2. 最小总体架构及模块树；
3. 现有系统公共接口与 Slim-local adapter边界；
4. Experiment 1–5 各自的数据流、控制流和失败语义；
5. 真实 provider caller、fixed-response executor、coverage tail、logical scheduler、fault/challenge hooks、projector、sink、resume、pricing projector和 reducer；
6. CLI 命令、配置 schema、experiment/profile选择和依赖解析；
7. run目录布局、results/trace/response schema和普通主键；
8. 指标到原始字段的逐项追踪矩阵；
9. root/attempt/trace 状态机、崩溃恢复和幂等边界；
10. representative与 full profile的精确差异；
11. CPU、内存、磁盘、文件句柄和并发控制；
12. secret、provider model identity和错误记录边界；
13. 非 Lean focused verification策略；
14. 明确的实现顺序、模块依赖和验收标准；
15. 复用清单：直接复用、轻量适配、只借逻辑、禁止复用；
16. 所有尚存的真实接口缺口；没有缺口时明确写 none。

设计不得只有原则或方框图。每个组件必须写清输入、输出、所有者、持久化位置、调用顺序、失败行为和测试方式。不得留下 TODO、TBD、“后续决定”或由实施 Agent重新决定的实验语义。

十一、Stage 1 审查与委托审批

完成初稿后，并行分派三个只读子 Agent：

1. metrics reviewer：逐项核对 Experiment 1–5、所有必须指标、原始字段和统计口径；
2. integration reviewer：核对系统接线、真实 API、trace、worker/scheduler、恢复、原子 CLI 和资源可实现性；
3. slimness reviewer：核对最小化、禁止门禁、legacy 回流、representative、资源上限和 Lean 测试裁剪。

子 Agent不得修改文件。三个reviewer的prompt都必须引用设计宪章第0节，并明确禁止基于人为伪造、恶意篡改、注入攻击或其他范围外攻击者模型提出加固需求。你必须统一裁决并修复所有范围内发现，然后再做一次跨文档二审。0 个未解决的范围内Critical、0 个未解释的范围内Important后，才可把设计状态设为approved_under_user_delegation；`out_of_scope_by_user`意见不进入未解决计数。该审批只是文档工作流，不得实现为runtime gate。

普通、可逆且不改变冻结实验语义的设计选择由你依据权威自行裁决，不等待我例行批准。若冻结权威存在无法同时满足的实质冲突、必须修改 shared code、必须改变实验/指标/provider/价格/fault/ablation/timing语义，才按接力协议强制停止。

十二、30 分钟恢复 heartbeat

读完启动文件后、开始任何长工作前，为当前顶层任务创建或更新恰好一个附着当前任务的 30 分钟 recovery heartbeat，并在 commentary明确报告：

RECOVERY_HEARTBEAT_ACTIVE

heartbeat 必须使用接力协议第 9 节的完整恢复 prompt。每次自动唤起都必须检查当前进程、terminal、工作树、阶段产物、progress和持久化结果，然后在本次唤起中恢复并持续推进。不得只报告状态，不得主动等待下一次 heartbeat。已有 owner turn或长命令仍活跃时不得建立第二条写路径。重试 provider 前必须先检查进程、results和 per-unit trace，禁止因网络波动盲目重复调用。

十三、完成和自动接力

Stage 1 完成后必须：

1. 运行不包含 Lean 的 focused文档验证；
2. 更新 progress.md 顶部；
3. 创建本地 checkpoint commit，不 push；
4. 生成不超过约 2,000 个中文字符的交接胶囊；
5. 在同一个 saved project的 local environment中创建恰好一个全新的 Stage 2 顶层任务；不得使用继承完整历史的 fork；
6. 使用接力协议第 6 节模板，并把上述不可违反约束继续写入下一阶段 prompt，不能再次缩水成“请阅读文档”；
7. 确认 Stage 2 active/in progress且已报告 RECOVERY_HEARTBEAT_ACTIVE；
8. 禁用 Stage 1 heartbeat并结束当前任务。

Stage 2、3、4 必须继续按同一协议串行创建下一阶段的新任务和 heartbeat，不等待我做例行批准。每一棒的 prompt 都必须保留与本阶段相关的硬约束，不能只传文档路径。

run_scope=representative_only
```

如果用户已经明确授权 representative 通过后继续 Full，则把最后一行改为：

```text
run_scope=representative_then_full
```

若 prompt 与四份冻结前置权威的细节出现差异，实验/指标以指标权威为准，系统接口以接线合同为准，架构理念和最小化边界以设计宪章为准；prompt 中的最小化、禁止项、原子运行、资源、heartbeat、Lean 测试裁剪和接力要求不得被省略或弱化。
