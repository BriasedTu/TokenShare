# TokenShare / TokenMosaic 论文写作交接简报（第一版）

更新时间：2026-08-25

## 0. 这份简报的用途

这不是一份逐条修补当前论文的修改清单，也不是一份预先规定各章标题数量的目录方案。它描述的是论文最终必须呈现出的研究对象、系统事实、实验逻辑和论证标准。论文写作者应先以本简报建立一幅完整、准确的系统图景，再撰写第一版正文；作者随后会针对该版本提出质疑，论文写作者应根据质疑继续修订。

当前优先任务是让论文中关于协议、容错、应用和实验的核心章节与真实实现同步。Introduction、Related Work 等外围章节暂时不是第一轮工作的重点。即使外围章节存在明显问题，也应先记录，不要因此偏离核心章节去做全篇重构。

本简报由实现侧向论文写作侧交接。它有意停在“模块如何工作、模块之间如何发生作用、实验如何形成证据”的层次，不要求论文写作者理解具体代码、类名、字段名或文件组织。当前 `paper.tex` 只是旧稿和可复用文字的集合，不是实现事实或实验事实的权威来源。

本简报是论文侧与实现侧唯一默认共同基线，但不是论文写作能够取得信息的最高颗粒度。某个具体段落需要代码级依据时，先定点读取 `IMPLEMENTATION_DETAILS.md`；如果其中仍没有答案，在 `DETAIL_REQUESTS.md` 提出一个描述性问题，由实现侧在同一条目中回答。论文写作者不在启动时读取这两个按需文件，也不维护编号式请求或证据数据库。

当前实现说明核验于 2026-08-25，对应 commit `d25ebf860b18cd10731151a2bcc6a7586b62776b`；核验时 `src/` 与 `tests/` 没有未提交差异。之后若相关代码、配置、实验输出或指标口径变化，应先重新核验，再更新本简报中的总体事实。

## 1. 论文首先需要讲清楚的研究对象

TokenShare（论文中目前使用 TokenMosaic 这一名称，最终命名需由作者统一）研究的不是某一种领域任务的求解算法，而是一套组织异构执行能力完成复杂任务的协议及其实验实现。它把一个根任务转化为一个受协议约束的任务图：任务由领域插件确定性地分解，子任务被调度给执行者，每次执行形成独立 attempt，候选结果经过解析和领域验证后才可能成为 canonical result，失败的工作可在协议允许时被替换，全部必需结果满足合并条件后再递归形成根结果。

系统想回答的中心问题应当写成：

> 当一个复杂任务可以被分解、局部验证并重新组合时，能否用统一协议协调多个 AI 执行单元，使系统在模型回答不稳定、任务并发、执行失败和工作节点退出的条件下，仍然产生可验证的最终结果；这种能力在不同任务领域、不同并发规模、不同容错机制和不同模型端点上表现如何？

这个问题比“共享闲置 token”更接近当前实现能够验证的内容。共享 token economics 可以继续作为研究动机，但当前原型没有实现真实代币支付、钱包、智能合约、开放社区网络或激励相容性实验，因此不能把“去中心化经济机制有效”“陌生参与者会诚实贡献”或“已经形成社区 token 市场”写成论文已经证明的结论。

当前实现能够支撑的核心论证链是：

1. 复杂任务可以被领域插件转化为有明确依赖和输出契约的任务单元。
2. 协议可以统一管理任务单元的调度、租约、执行尝试、验证、canonical binding、失败恢复和合并。
3. 相同协议可以承载两类验证逻辑明显不同的应用：整数分解和 Lean 定理证明。
4. 容错能力不是口号，而是可以通过故障注入、真实进程终止和机制消融被分别观察。
5. 并发扩展、恢复开销和模型端点差异可以通过冻结的实验设计和一致的统计口径进行测量。

## 2. 必须区分的三层系统

论文不能把协议、领域插件和实验设施混写为同一个“系统模块”。三者的职责如下。

### 2.1 协议内核：规定任务如何流转

协议内核负责领域无关的生命周期。它维护任务图、任务单元状态、worker、lease、attempt、submission、verification record、canonical output、recovery 和 merge 等协议对象，并用 append-only event ledger 保存状态变化。它不理解“因数是否正确”或“Lean 证明能否通过编译”；它只要求插件给出规范化的分解、验证和合并动作。

一次 root 生命周期的基本逻辑是：

1. 注册根任务并冻结本次运行所使用的插件、执行器和协议配置。
2. 由领域插件生成任务计划，建立子任务及其依赖关系。
3. 找出当前可执行的任务单元，为其创建 attempt 和有期限的 lease，并按 worker capacity 分派。
4. 执行者返回原始输出；输出先经过领域 parser 转化为类型化候选，再经过 verifier 或 checker 判断。
5. 只有通过验证的 attempt 才能绑定为该任务单元的 canonical result。失败、超时、无返回或节点死亡不会直接产生 canonical result。
6. 当一个失败满足恢复条件且还有 retry allowance 时，协议创建 replacement attempt；旧 attempt 的迟到提交不能覆盖新的有效状态。
7. 当所有必需 canonical slots 满足合并条件时，插件执行合并；合并后的根结果还要经过独立的领域级检查。
8. 根任务最终被区分为：得到且通过独立检查的结果、没有形成最终结果、形成结果但领域检查不正确，或因基础设施错误而无法作为科学结果解释。

这里要特别写清楚：protocol completion 与 scientific correctness 不是同一概念。系统完成了状态机流程，不自动意味着答案正确；答案正确必须由领域级验证事实证明。

### 2.2 领域插件：决定怎样分解、验证和合并

领域插件是协议与具体任务之间的边界。插件拥有任务输入和输出契约、确定性分解规则、提示构造、结果 parser、局部 verifier/checker、合并逻辑和根结果检查。AI executor 只负责产生候选答案，不能自行决定协议级分解，不能宣布自己的输出已经正确，也不能绕过 canonical binding。

这一边界是论文的重要设计原则：共享协议只提供稳定的协调语义，任务知识由插件提供。论文应通过两个应用展示这一抽象如何落地，而不是把两个应用写成彼此无关的 demo。

### 2.3 Slim V2 实验层：操纵条件并形成论文证据

Slim V2 不是第二套协议。它是一个较薄的实验层，负责冻结 case、condition、repeat、worker 数、模型、故障和消融模式，然后为每个 root 装配现有协议内核、领域插件与执行路径。它还负责保存原始模型响应和普通实验结果，把协议事件、插件检查结果、worker facts 和资源使用投影为 root-level observation，最后按冻结口径聚合为论文表格。

因此，故障注入器、回答回放器、实验 profile、结果 projector 和统计 reducer 都属于实验设施，不应被描述成协议本体的常规组件。论文可以解释它们如何保证实验控制，但不能把实验中的特殊旁路写成生产协议默认行为。

## 3. 真实实现中的主要模块及其作用逻辑

### 3.1 冻结实验清单与 root 装配

实验首先把每个条件展开为明确的 root inventory。一个 root 对应一个确定的实验、条件、case 和 repeat，携带其 worker 数、重试上限、模型、故障或消融配置。执行时，每个 root 单独创建协议引擎、插件 runtime、worker backend、artifact store 和 event ledger，不在不同 root 之间共享可变运行状态。不同 root 串行执行；一个 root 内部的任务单元才根据 worker capacity 并行。

这种隔离让论文可以把 root 作为最基本的统计观测单位，也避免把前一个 case 的状态误带入下一个 case。实验计划还会在运行前计算 root 数、planned unit 数、最大协议 attempt 数、真实 provider 调用上界和存储上界；这些是运行边界，不是实验结果。

### 3.2 执行与回答适配

在线路径用于 Experiment 1 和 Experiment 5。每个协议 attempt 最多触发一次 provider 请求，调用层本身不做隐藏重试；是否再次尝试由协议 recovery 决定。系统保存调用开始事实、原始响应、模型身份、延迟和 usage，再把回答交给领域 parser。模型不匹配、响应 envelope 非法、网络错误和 parser rejection 都被保留为不同失败事实。

离线路径用于 Experiment 2–4。它按 case、来源 repeat 和 planned unit 精确读取 Experiment 1 已保存的 per-unit trace，校验领域语义和模型身份，再把来源回答重新送入同一协议、parser、verifier、recovery 和 merge 路径。优先使用与当前 attempt ordinal 相同的自然回答；若来源中没有该 ordinal，则确定性使用该 unit 最后一个自然 attempt。该路径不会退回在线 provider，也不会产生新的真实模型调用。

这种设计的科学意义是控制回答内容：Experiment 2–4 比较的是协议调度、故障和机制差异，而不是每次重新调用模型带来的回答随机性。论文必须把它称为 trace-backed replay 或 trace-attributed simulation，不能称为新的在线模型实验。

### 3.3 调度、并发和逻辑时间

在线实验使用真实 worker backend 和实际 wall-clock。单 worker 条件顺序执行；多 worker 条件在同一 root 内以固定 capacity 并发执行。系统记录任务的分派、完成、执行中集合和峰值并发。

Experiment 2–4 使用来源回答中记录的单元延迟推进 logical scheduler，从而在完全相同的回答和时延输入上比较不同 worker 数或不同协议条件。这里的 makespan 是逻辑调度结果，不是再次等待 provider 的实际墙钟时间。论文必须区分 actual wall-clock、source latency 和 simulated/logical makespan。

### 3.4 验证、canonical binding 和根结果复核

parser 只回答“输出能否被解释为符合领域 schema 的候选”，verifier/checker 才回答“候选是否满足领域正确性”。验证通过后，协议把对应 attempt 的输出绑定为该任务单元的 canonical result。后续依赖任务和 merge 只能消费 canonical result，不能直接消费任意 raw response。

合并也不是简单拼接。系统先检查 required slots 是否已经由正确的 canonical children 填满，插件再按照领域规则合成根候选，最后执行独立 root checker。论文应把 parser、local verification、canonical binding、merge gate 和 root recheck 写成连续但不同的正确性边界。

### 3.5 恢复与节点死亡

系统以 lease 和 attempt 区分一次任务与一次执行。no-return、executor error、验证拒绝或 lease expiry 可形成 recovery trigger；当 replacement 机制启用且重试额度尚未耗尽时，系统创建新的 attempt。已过期 attempt 的 late submission 会被拒绝，不能覆盖 replacement 的结果。

一般故障由实验 hook 在真实协议边界注入。worker death 则使用独立进程执行目标任务，并在预注册进度点真实终止进程；系统随后根据观察到的 worker outcome 进入恢复路径。因此，worker death 可以描述为真实进程故障实验，而其他 Experiment 3 条件应描述为受控故障注入。

### 3.6 持久化与恢复运行

协议内核继续使用本地 artifact store、append-only event ledger 和本地状态对象保存生命周期事实。Slim V2 在实验层另外保存普通 JSON/JSONL/CSV：冻结 inventory、每个 root 的协议快照和最终 observation、Exp1 per-unit trace、provider call journal、原始响应以及最终 metrics tables。

每个 provider call 先写唯一 intent，再至多发送一次请求，最后写 terminal outcome。若进程在 intent 后中断，恢复逻辑只读取已经存在的 response 或把结果记为未知 transport outcome，绝不为了“补齐记录”再次调用 provider。root 结果和 trace 使用幂等写入：相同内容可以跳过，已有同一身份但内容冲突时停止。这里的目标是防止 crash/resume 造成重复付费或重复事实，不是证明本地文件无法被恶意研究者篡改。

### 3.7 Root 投影与指标归约

projector 从同一次 root 的协议结果、事件、plugin facts、worker facts 和实验场景记录中形成结构化 observation。它明确区分 final result 是否存在、是否通过领域复核、失败发生在哪个阶段、失败是否属于 infrastructure invalid，以及哪些任务单元被计划、分派、完成、替换或留在 early-stop 之后。

reducer 只读取这些冻结 observation 和 inventory，按预注册分母计算 Experiment 1–5 的表格。失败 root 保留在分母中；缺失 usage 不按 0 处理；infrastructure-invalid root 会使相关科学 rate 在对应 cell 中失效，而不是被当作模型失败。需要不确定性时，采用以 case 为 cluster 的 bootstrap，避免把同一 case 的多个条件或 repeat 当作彼此独立样本。

## 4. 两个应用如何实例化同一协议

### 4.1 Integer Factorization

Factorization 插件从目标整数的候选除数区间出发，把从 2 到整数平方根的搜索空间确定性划分为若干连续 range。每个 range 是一个 AI task unit，模型返回“找到因数”或“该范围不存在因数”的结构化候选。

局部 verifier 对两类回答采用不同检查：若模型声称找到因数，就验证因数确实整除目标整数并位于指定 range；若模型声称没有因数，就对该 range 进行完整确定性复核。通过验证的 range result 才能成为 canonical result。如果某一 range 给出有效因数，系统可以按照插件定义的完成规则提前结束不再需要的搜索；如果没有找到因数，则必须覆盖所有 required ranges 后才能合并。根结果最终以因数乘积等领域条件独立复核。

这个应用展示的是宽任务分解、同层并发、局部可验证性、自然 early stop 和 required-slot merge。

### 4.2 Lean Theorem Proving

Lean 插件使用预注册的固定 theorem/lemma decomposition。简单题可以是直接 proof unit；复杂题使用固定 lemma DAG。AI 不负责发现协议级中间引理，也不能改变依赖图。某个 lemma unit 只有在其前置 lemma 已有 checker-accepted canonical proof 后才能获得依赖输入并执行。

模型生成 proof candidate 后，插件将其规范化，并在冻结的本地 Lean toolchain 和库环境中调用真实 checker。只有 checker 通过的 proof artifact 才能成为 canonical result。所有 required lemma slots 到齐后，插件按固定图结构合成根证明，并再次运行根级 Lean checker。

Lean 环境本身需要独立 preflight。若 toolchain、项目或关键输入不可用，该 root 属于 infrastructure invalid，而不是模型证明失败。这个应用展示的是有向依赖、串并行混合执行、外部工具验证、严格 canonical dependency 和根级复核。

两个应用的共同点应当作为论文主线：协议生命周期相同，领域分解、parser、verifier 和 merge 不同。论文不应简单地将它们写成两个“应用示例”，而应借此证明协议与领域逻辑之间的模块化边界。

## 5. 容错机制应怎样被表述

当前系统的容错主线可以归纳为四个相互衔接的层次：

1. **候选边界**：parser policy 阻止无法形成合法领域候选的 raw output 直接进入后续路径。
2. **正确性边界**：verification 阻止错误候选成为 canonical result。
3. **可用性边界**：requeue/replacement 在任务失败、无返回、超时或节点死亡后提供新的执行机会。
4. **组合边界**：merge gate 在 required canonical slots 不完整时阻止过早合并；slot integrity 始终保持启用，防止把错误 child 与错误 slot 配对。

Experiment 3 使用完整机制，研究它们在故障下能否检测问题、启动 replacement、恢复 required slots，并以多大时间和资源代价获得最终正确结果。Experiment 4 则通过关闭 verification、parser policy、requeue 和 merge gate 中的一项或两项，研究每项机制及机制组合的必要性。

fault tolerance 在当前论文中应当是一项核心系统性质，但除非作者把整篇论文的唯一中心贡献重新定义为容错，否则它不应被写成与整个协议并列的第二套系统。更自然的定位是：先给出协议的完整生命周期，再把恢复、验证和合并门控作为该生命周期中的关键机制集中解释，最后由 Experiment 3 和 4 验证。

当前故障模型是有限且预注册的。rate-fault 只有 false positive、false negative、no return、late submission 和 executor error；worker death 是单独条件。实现没有建立 Byzantine adversary、安全攻击者、恶意插件、恶意 provider、注入攻击或开放网络身份模型。论文可以声称对这些受控故障具有实验性恢复能力，不能泛化为 Byzantine fault tolerance 或生产级 adversarial security。

## 6. 五组实验的真实逻辑

### 6.1 Experiment 1：跨领域真实模型基线

研究问题：在不注入实验故障的正常协议下，真实模型回答能否通过 Factorization verifier 或 Lean checker，并形成正确根结果；表现如何随领域、难度和主题变化；实际时间、token 和成本是多少？

Full 设计包含 300 个 Factorization root 和 135 个 Lean root，共 435 个 root；worker count 为 10，repeat 为 0。当前前向模型是 DeepSeek `deepseek-v4-flash`。协议允许每个 AI unit 最多 3 个自然 attempts（首次加 2 次 replacement）。时间、usage 和 cost 来自真实 provider 调用。

Experiment 1 同时为 Experiment 2–4 提供固定回答。正常 root 先按协议运行；若自然 early stop 导致下游实验需要的 planned unit 没有被执行，则只对确实被下游消费的来源 root 执行 coverage tail，补取缺失 unit 的回答。coverage tail 不改变 root 最终结果，也不延长论文中该 root 的 protocol runtime；其调用、token、成本和成功情况单独记录。不能把 coverage-tail 回答伪装成正常协议执行结果。

### 6.2 Experiment 2：worker 扩展性

研究问题：在任务计划、模型回答和单元时延完全相同的情况下，提高同一 root 内的 worker capacity 能带来多大 logical speedup、效率和利用率变化？

Full 设计选择 50 个 hard Factorization case，worker count 为 1、3、7、10、30、50，每个条件 2 个 repeats，共 600 个 root。所有条件完整继承 Experiment 1 的相同 planned units 和 per-unit traces，不调用 provider。logical scheduler 使用来源 latency 推进任务，保留依赖、early stop 和协议状态机的影响。

因此，Experiment 2 证明的是受控 trace 条件下的协议级可扩展性，不是对真实远程网络、真实 50 个物理节点或新的在线 provider 并发实验的测量。结果应报告 speedup、parallel efficiency、worker utilization、completion/correctness 和资源倍率，并解释依赖关系与自然 early stop 为什么会限制线性扩展。

### 6.3 Experiment 3：故障恢复

研究问题：面对不同故障类型、故障比例和节点死亡，系统能否拦截错误、启动 replacement、恢复完整 canonical slots 并保持最终正确；恢复的时间和资源开销是多少？

Full 设计使用 50 个 Factorization case 和 3 个 Lean case，worker count 为 10。五类 rate-fault 只注入目标 unit 的 ordinal 0；Factorization 使用更完整的 rate sweep，Lean 使用 10%、50%、100%。另有死亡 worker 数量和 25%、50%、75% 终止进度条件。论文分母中有 3,726 个故障 root，另有 106 个无故障 auxiliary references 用于配对比较。

Experiment 3 不产生新的 provider 调用。回答和来源 usage 来自 Experiment 1，当前 repeat 下的 token 和 latency 在固定 seed 下加入确定性的约 ±10% 扰动，用于形成 paired simulated overhead。论文必须将这些量称为 simulated 或 trace-attributed，不能称为该条件实际消耗的 provider token、成本或墙钟时间。worker death 的进程终止本身是真实发生的，但其模型回答仍来自固定 trace。

### 6.4 Experiment 4：机制消融

研究问题：verification、parser policy、requeue 和 merge gate 各自阻止什么失败；关闭一项或两项机制时，错误是否会逃逸、任务是否会卡住、required slots 是否丢失、是否发生 premature merge；机制之间是否存在交互？

Full 设计包含 FULL、4 个单机制关闭模式和 6 个两两关闭模式，共 11 modes；65 个 case、3 个 repeats，共 2,145 个 root，worker count 为 10，不调用 provider。四类 challenge 是 invalid parsed candidate、parser-required canonical JSON、recoverable no-return 和 required-child delay。

challenge plan 在 mode 之前按 case 和 repeat 固定，injector 不可见当前 mode，因而不同 mode 接受相同挑战。机制关闭发生在该机制原本应被调用的实际结构边界，而不是先正常运行、最后再篡改 outcome。slot integrity 始终启用，不是本次消融变量。结果应同时报告 FULL 基线、单项下降、两项关闭效果和 pair interaction，而不是只列 11 个完成率。

### 6.5 Experiment 5：真实模型端点比较

研究问题：在相同 hard task、相同协议和无 replacement 的条件下，不同真实模型端点在最终正确性、first-attempt 结果类型、token、成本和完整 root wall-clock 上有何差异？

当前 Full 设计包含 28 个 hard Factorization case 和 9 个 hard Lean case，共 37 个 case；比较 3 个 SiliconFlow thinking endpoints：GLM-5.2、Qwen3-14B 和 MiniMax-M2.5。每个模型只运行 repeat 0，worker count 为 10，max retries 为 0，共 111 个 root、852 个 planned online calls。思考预算和最大输出上限均为 100,000 tokens。

Experiment 5 的三个模型都进行真实 API 调用，wall-clock 包含完整 root 生命周期。不能使用 first dispatch 到最后 response 的局部时间代替 root 时间。当前不做综合排名、pairwise significance 或人为加权总分。DeepSeek V4 Flash 可以作为单独 supplemental reference 比较部分质量和资源事实，但其 Exp1 历史结果不能构成正式的第四个 Experiment 5 endpoint，也不能用于 latency 排名。

## 7. 实验中“真实”“模拟”和“已完成”的严格边界

| 内容 | 性质 | 论文允许的表述 |
|---|---|---|
| Experiment 1 模型请求、响应、usage、延迟 | 真实 provider 调用 | actual / observed |
| Experiment 5 三模型请求、响应、usage、延迟 | 真实 provider 调用 | actual / observed |
| Experiment 2–4 回答内容 | Experiment 1 trace 回放 | fixed-response / trace-backed，不能称新的模型调用 |
| Experiment 2 的 makespan | 来源时延驱动的逻辑调度 | logical / simulated scheduling time |
| Experiment 3 token、latency、cost overhead | 来源事实加固定扰动 | simulated / trace-attributed |
| Experiment 3 worker death | 真实子进程终止，回答仍来自 trace | real process termination under trace-backed execution |
| Experiment 4 challenge 和 mechanism bypass | 真实协议边界上的受控实验操纵 | controlled challenge / structural ablation |

截至 2026-08-27，三模型 Flash Full run `slim-v2-full-flash-20260823-233000-b4c8e951` 已完成并发布正式 reducer 产物：7,017 个论文分母 root、106 个 Experiment 3 auxiliary references，以及 `summary.json` 和 Experiment 1–5 各 JSONL/CSV 共 11 个 metrics 文件。五表行数为 12、88、684、648、3；正式 summary 的 provider-call observation 为 Exp1=`2124`、Exp2=`0`、Exp3=`0`、Exp4=`0`、Exp5=`808`。这些是该 run 已持久化 journal 的计数，不是 2026-08-27 reducer 修复新增的调用。

发布前 reducer 曾因两个合法 `null` 缺科学原因而停止：Exp1 hard cell 有真实 provider latency 但部分调用没有 usage，Exp5 一个 model cell 同时含 parsed-unsubmitted 与 infrastructure-invalid root。最小修复没有补造 usage、没有把缺失当0，也没有重跑实验；正式表中 Exp1 hard token/cost totals 保持 `null + usage_missing`，相关 provider latency 仍为事实值，Exp5 nonpass 保持 N/A、相关科学率按 infrastructure-invalid 原因置空。发布前后 1,088,134 个非 metrics 输入文件的 metadata fingerprint 与 21,260 个 reducer/费用关键输入文件的内容 SHA 完全一致。因此，论文写作者现在可以读取该 run 的正式 metrics 做结果分析，但仍必须先核对每个 cell 的缺失原因、actual/logical/simulated 语义和 interval eligibility，再形成具体数值结论或排名。

## 8. 当前实现有与没有的能力

### 已经存在并可写入系统说明

- 本地可运行的协议状态机、任务图、worker/lease/attempt 生命周期、canonical binding、recovery、merge，以及 sandbox contribution/settlement 记录；后者不是现实支付，也不是当前 Slim V2 的主要实验指标。
- append-only event ledger 与 artifact store，用于系统自身的状态重建和结果引用。
- Factorization 与真实 Lean 两个领域插件；两者都使用确定性分解，AI 只生成候选结果。
- 真实 DeepSeek 和 SiliconFlow 请求路径，原始 response、模型身份、usage、latency 和错误持久化。
- Experiment 1 trace acquisition 及 Experiment 2–4 的零 provider fixed-trace 路径。
- 线程并发、逻辑时延调度、协议 replacement，以及真实进程终止的 worker-death 路径。
- 五类 rate-fault、四类 ablation challenge 和 V/P/R/M 四种机制的结构性消融。
- 普通文件形式的可恢复运行、root-level observation、五组实验 reducer 和统计输出。

### 不存在或不属于当前论文证据范围

- 真实区块链、链上 ledger、钱包、智能合约或真实 token 支付。
- 真实 P2P 网络、HTTP worker pool、开放社区节点或跨机器生产部署。
- 动态第三方插件市场、通用在线 theorem-proving 平台或完整用户界面。
- 由 AI 自主发现任意任务的协议级分解；当前分解由插件和冻结 case 决定。
- 对任意深度、按实时 worker 数动态变化的递归分解进行实证验证；当前两个应用分别使用冻结的 range partition 和固定 lemma DAG。协议对象允许层次与依赖关系，但当前实验 claim 必须受实际任务图限制。
- Byzantine fault tolerance、身份认证、反女巫、恶意参与者防护或安全攻击实验。
- 对激励相容、贡献公平、社区参与意愿或经济可持续性的实证证明。
- Experiment 2–4 的新在线模型调用或真实 provider wall-clock 并发数据。
- 未经过正式 metrics、缺失原因和 interval eligibility 核对的排名、显著性或综合结论。

论文中的 claim 强度必须受这张边界约束。可以把生产网络、经济激励和开放参与写成未来工作或更广阔愿景，但不能用当前原型实验替它们背书。

## 9. 核心章节最终应实现的内容目标

这部分只规定内容功能，不规定章号和小标题。

### 协议与系统部分

读者读完后应能回答：研究对象是什么；protocol 与 system 分别是什么；一个 root 如何从注册走到最终结果；task unit、worker、attempt 和 run 有何区别；插件和 executor 各自拥有什么权力；parser、verification、canonical binding、merge 和 root check 为什么不能合并成一个步骤；失败如何触发 replacement；持久化如何避免恢复时再次调用模型。

建议用一张总体图先展示“冻结任务与条件 → 插件分解 → 协议调度/执行 → parser/verification → canonical/recovery → merge/root check → observation/metrics”的主链，再用状态转换图或简洁伪代码解释关键生命周期。图和伪代码的目的不是复刻代码，而是让读者可以独立重建系统逻辑。

### 容错部分

读者读完后应能回答：系统承认哪些失败；检测边界在哪里；哪类失败可以 replacement；late submission 为什么无效；worker death 与普通错误注入有何区别；V/P/R/M 四种机制怎样分别维护候选合法性、正确性、可用性和组合完整性；当前保证在哪些假设下成立，哪些 adversarial 场景明确不在范围内。

### 应用与实验部分

读者读完后应能回答：为什么选择 Factorization 和 Lean；它们分别覆盖了哪种任务图与验证形态；五个实验各自验证哪一项设计目标；哪些实验调用真实模型，哪些复用 trace；时间和资源量是实际还是模拟；分母、失败和 infrastructure invalid 如何处理；每张表最终能支持哪一个 claim。

结果部分不能只堆表格。每组实验都应按同一逻辑闭环：research question → controlled setting → manipulation/baseline → metrics → result → interpretation → supported claim → limitation。

## 10. 从教授意见抽象出的长期写作准则

以下准则适用于之后每一次论文修改，不随当前目录结构变化而失效。

### 准则一：先确定论文要证明什么，再决定保留哪些文字

旧正文是材料，不是约束。每一节都必须服务于中心问题、设计目标或实验证据。不能因为某段已经写得很长就为它寻找位置，也不能用局部润色掩盖论证链没有闭合的问题。

### 准则二：建立单一且可追踪的因果链

论文整体必须能被压缩为：现实动机产生研究问题；研究问题被转化为明确的形式化问题和设计目标；设计目标决定协议机制；领域应用展示机制可实例化；实验逐一验证设计目标和 claim。任何概念如果无法放入这条链，要么删除，要么降为背景或未来工作。

### 准则三：以读者的认知负担设计结构

目录和小标题是阅读手册，不是作者的内部零件清单。标题应优先使用领域读者熟悉的高层功能词，并让相邻部分体现顺序、依赖或对照关系。不要把七八个实现步骤平铺成同一层级；应归纳为三到四个可记忆的概念模块，再在模块内部说明细节。若关系无法用标题表达，就用总体图、状态图或开头的 roadmap 补足。

### 准则四：严格区分协议、系统实现、领域插件和实验设施

协议描述参与者和对象必须遵守的规则；系统实现描述这些规则怎样被执行和持久化；插件描述领域分解与正确性；实验设施描述怎样操纵条件和测量结果。四类内容可以相邻，但不能互相冒充。

### 准则五：不仅介绍模块目的，还要解释模块之间如何发生作用

系统说明至少要达到“输入什么、依据什么规则转换、产生什么状态或输出、失败时转向哪里、下游依赖什么”的深度。只说“该模块用于提高可靠性”太浅；罗列类名、字段和函数又太深。论文需要的是可复现的行为逻辑。

### 准则六：贡献要有层级，不能让所有机制争夺中心位置

协议整体、领域抽象、容错机制和实验方法并非天然同级。应先确定主要贡献，再把容错、持久化、回放等放入支持主要贡献的位置。除非容错被重新定义为全文唯一中心，否则不要把它写成与整个系统并列的第二套架构。

### 准则七：实验必须与设计目标和 claim 一一对应

每个实验必须明确：为什么选择这些任务与条件；控制了什么；改变了什么；指标怎样回答研究问题；结果支持哪一条 claim；不能支持什么。没有对应 claim 的实验不应仅因已经实现就进入主文；没有实验或形式论证支持的设计目标不能被写成已经成立。

### 准则八：在论证链没有对齐前，不继续盲目增加实验

新增 case、模型、故障或指标之前，先确认它填补的是哪一个证据缺口。如果论文的 idea、协议描述和验证目标尚未一致，继续扩展实验只会增加无法解释的数据。

### 准则九：复现性依靠充分的行为说明，而不是代码倾倒

正文应提供足够的对象定义、状态转换、关键算法或伪代码、代表性例子、参数和假设，使读者即使不逐行阅读源码也能理解并重建实验。代码仓库和配置用于复查，不代替论文解释。涉及实际与模拟数据时，必须明确来源链和统计口径。

### 准则十：作者必须能够解释和捍卫每一句技术陈述

AI 生成文字只能作为草稿。写作者不得补造实现、实验数值或机制效果；无法确认的内容标记为 `[需作者确认]` 或保留为待填结果。作者应删除空洞、重复、陈旧和自己无法解释的表述，而不是让语言流畅掩盖事实错误。

### 准则十一：先交付清晰的书面理解，再进行大规模改写

每次重大修改前，论文写作者应先用简短文字复述本轮理解：要解决的写作问题、要支撑的 claim、准备使用的实现或实验事实、仍然存在的关键不确定性。如果这些理解与作者不同，应先提问，不要在错误方向上生成大量正文。

### 准则十二：结果、设计和计划必须使用不同语气

已经实现并核验的行为可用事实语气；已冻结但尚未运行的实验只能写为 design 或 protocol；历史 run 只能在其原配置范围内被引用；未来能力必须明确写为 future work。不得把“计划运行”“代码支持”“Representative 观察到”和“Full 已证明”混成同一种完成状态。

## 11. 论文写作者下一轮的工作方式

1. 完整阅读本简报后，先用不超过一页的篇幅复述系统主线、两个领域插件、五个实验及真实/模拟边界；不要先给作者一长串泛化建议。
2. 第一轮正文优先处理协议、容错、应用与实验设计，使其成为一份内部一致、可以被作者质疑的完整版本。暂时不以“全篇润色”为目标。
3. 可以复用 `paper.tex` 中已经准确的定义和表达，但不能继承与当前实现冲突的假设，例如开放不可信网络、真实 token settlement、AI 自主分解、Exp2–4 在线调用，或在未核对正式 metrics 时声称 Full 已支持某项具体结论。
4. 结果数值只从 `slim-v2-full-flash-20260823-233000-b4c8e951/metrics/` 及其冻结缺失原因取得；不编造数字，也不从旧实验段落或历史 Representative 移植数字。
5. 作者提出质疑后，优先修订被质疑的事实、因果链及其相邻段落。不要借一次局部质疑再次扩展成无关的全篇重构。
6. 只有当实现事实缺失会实质改变论文结论时，才向实现侧提出一个具体问题。论文写作者不需要管理源码或大量工程文件；本简报就是默认的实现交接面。

## 12. 第一版正文的最低验收问题

第一版完成后，应逐项检查：

- 不看代码的读者能否准确复述一次 root 从注册到最终结果的路径？
- 读者能否说明 protocol、plugin、executor 和 Slim V2 实验层的区别？
- Factorization 与 Lean 是否共同证明了同一协议抽象，而不只是两个案例？
- fault tolerance 是否被解释为具体检测、恢复和合并边界，而不是形容词？
- Experiment 1–5 是否各自只有一个清楚的主问题，并与设计目标对应？
- 所有 actual、logical、simulated、trace-backed 和 historical 数据是否被明确区分？
- 是否删除或降级了区块链、真实支付、开放社区、恶意攻击防护等当前没有证据支持的 claim？
- 是否把 current plan、Representative 和 Full 的完成状态严格区分？
- 每一个结果段落是否说明了结果能支持什么、不能支持什么？
- 作者能否用自己的语言解释全文的每一项核心机制和实验结论？

只要这些问题中仍有一个答案是否定的，就说明论文还没有达到本简报所定义的目标状态。
