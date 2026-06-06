# TokenShare 论文到模块借鉴映射

来源文档：`2026-06-02-tokenshare-protocol-kernel-revised-draft.md`

本文件收录已经影响 TokenShare 设计的论文、技术报告或正式论文 PDF。Airflow、Argo Workflows、Temporal、Kubernetes、Ray 文档、Apache Beam、Nextflow、Chaos Mesh、OpenTelemetry GenAI、IMClaw 等为工程文档、规范或公开站点，不作为论文 OCR 条目收录。

论文 TeX 存放目录：`tokenshare-paper-tex/`

自 2026-06-06 起，任何联网查到并被用于修改 TokenShare 设计、代码、测试或文档的新论文/技术报告，都必须先下载或转换为本地可复查材料，再补充到本文件。新增条目至少记录：论文标题、来源 URL、本地文件路径、下载或转换日期、影响到的模块/章节，以及借鉴方式。无法合法保存全文时，必须记录原因、可访问元数据、本地摘要和影响范围。

## 模块到论文映射

| 模块/功能/逻辑 | TokenShare 的借鉴实现方式 | 对应论文 | 本地 TeX | 论文来源 |
|---|---|---|---|---|
| 任务图：递归拆分树与执行依赖 DAG | 将用户选择的递归拆分逻辑产生的任务结构，与实际执行依赖 DAG 分开建模；协议协调器而不是客户端负责把验证通过的结果展开为新 TaskUnit。 | P03: *Cilk: An Efficient Multithreaded Runtime System* | `tokenshare-paper-tex/P03_cilk_spawn_tree_precedence_dag.tex` | <https://publications.csail.mit.edu/lcs/pubs/pdf/MIT-LCS-TM-548.pdf> |
| 任务图：运行时动态展开 | 允许任务图随上游正式结果逐步展开，但每次图变更必须写入事件日志，恢复时重放已持久化的拆分结果，不重新调用非确定性 AI。 | P02: *CIEL: A Universal Execution Engine for Distributed Data-Flow Computing* | `tokenshare-paper-tex/P02_ciel.tex` | <https://www.usenix.org/events/nsdi11/tech/full_papers/Murray.pdf> |
| 任务图：数据流式依赖组织 | 将任务节点和 artifact 依赖表达成可调度数据流图，插件只声明依赖与输出，协议层负责调度和状态推进。 | P01: *Dryad: Distributed Data-Parallel Programs from Sequential Building Blocks* | `tokenshare-paper-tex/P01_dryad.tex` | 原草稿源：<https://www.microsoft.com/en-us/research/wp-content/uploads/2007/03/eurosys07.pdf>；下载源：<https://mihaibudiu.github.io/work/eurosys07.pdf> |
| 结果驱动拆分接口 | 将复杂任务拆成可替换的模块化子任务；TokenShare 的 adapter/decompose 接口固定插件版本、参数和拆分策略，再由协议记录实际展开计划。 | P16: *Decomposed Prompting: A Modular Approach for Solving Complex Tasks* | `tokenshare-paper-tex/P16_decomposed_prompting.tex` | <https://openreview.net/pdf?id=_nGgzQjzaRy> |
| 任务单元建模：中间 reasoning/proof state | 将 proof state、subgoal、repair attempt、intermediate reasoning state 作为可分配、可验证的 TaskUnit，而不是隐藏在一次长执行中。 | P17: *Tree of Thoughts: Deliberate Problem Solving with Large Language Models* | `tokenshare-paper-tex/P17_tree_of_thoughts.tex` | <https://papers.neurips.cc/paper_files/paper/2023/file/271db9922b8d1f4dd7aaef84ed5ac703-Paper-Conference.pdf> |
| 命名输出与正式版本绑定 | 多个 attempt 可以并存，但 Canonical output bundle 只能绑定一次；迟到结果只能保留为审计证据，不能覆盖已接受结果。 | P04: *MapReduce: Simplified Data Processing on Large Clusters* | `tokenshare-paper-tex/P04_mapreduce.tex` | <https://research.google.com/archive/mapreduce-osdi04.pdf> |
| 命名输出与正式版本绑定 | 为每个 vertex execution 保留版本，选择成功版本作为正式输出；TokenShare 采用 attempt/output version 与 canonical selection 的分离。 | P01: *Dryad: Distributed Data-Parallel Programs from Sequential Building Blocks* | `tokenshare-paper-tex/P01_dryad.tex` | 原草稿源：<https://www.microsoft.com/en-us/research/wp-content/uploads/2007/03/eurosys07.pdf>；下载源：<https://mihaibudiu.github.io/work/eurosys07.pdf> |
| 正式结果选择与冗余验证 | 将 canonical result、min quorum、迟到/失败实例上限用于未来非确定性结果的选择策略；第一版先保持确定性验证器。 | P05: *BOINC: A Platform for Volunteer Computing* | `tokenshare-paper-tex/P05_boinc_platform.tex` | <https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf> |
| 插件契约：typed input/output、依赖、requirements/hints | 插件声明输入、命名输出、依赖、硬要求与可选偏好；调度由协议协调器依据正式结果可用性决定。 | P06: *Common Workflow Language, v1.0* | `tokenshare-paper-tex/P06_common_workflow_language.tex` | <https://arxiv.org/pdf/2105.07028> |
| 拆分逻辑安全边界 | 避免将任意表达式直接提升为协议级拆分语言；复杂拆分逻辑放入版本化插件代码，日志记录策略 ID、参数和输出计划。 | P06: *Common Workflow Language, v1.0* | `tokenshare-paper-tex/P06_common_workflow_language.tex` | <https://arxiv.org/pdf/2105.07028> |
| 执行器契约：统一请求与提交信封 | 执行器边界保持小而稳定：接收任务描述、报告状态和结果；不同执行器内部调用格式可以不同。 | P07: *Mesos: A Platform for Fine-Grained Resource Sharing in the Data Center* | `tokenshare-paper-tex/P07_mesos.tex` | <https://mesos.apache.org/assets/papers/nsdi_mesos.pdf> |
| 调度能力与资源声明 | 支持 CPU、GPU、内存、自定义资源、环境 hash、标签等有限资源键；调度器只读能力声明，不理解任务域知识。 | P09: *Ray: A Distributed Framework for Emerging AI Applications* | `tokenshare-paper-tex/P09_ray.tex` | <https://www.usenix.org/system/files/osdi18-moritz.pdf> |
| 分布式运行时的未来扩展 | 未来 distributed runtime 可参考动态任务图、actor 和 future/object 引用，但第一版不依赖 Ray。 | P09: *Ray: A Distributed Framework for Emerging AI Applications* | `tokenshare-paper-tex/P09_ray.tex` | <https://www.usenix.org/system/files/osdi18-moritz.pdf> |
| 故障恢复：lineage 与 artifact 边界 | 对确定性程序可参考 lineage reconstruction/重执行；对 AI 输出必须持久化正式 artifact 与内容 hash，不能假定重执行等价。 | P02: *CIEL: A Universal Execution Engine for Distributed Data-Flow Computing*；P09: *Ray: A Distributed Framework for Emerging AI Applications* | `tokenshare-paper-tex/P02_ciel.tex`; `tokenshare-paper-tex/P09_ray.tex` | <https://www.usenix.org/events/nsdi11/tech/full_papers/Murray.pdf>; <https://www.usenix.org/system/files/osdi18-moritz.pdf> |
| 事件日志、重放和快照 | 采用 append-only event log、replay/snapshot 作为控制平面恢复基础；第一版不实现 Raft 复制或 leader election。 | P10: *In Search of an Understandable Consensus Algorithm (Extended Version)* | `tokenshare-paper-tex/P10_raft.tex` | <https://www.usenix.org/system/files/conference/atc14/atc14-paper-ongaro.pdf> |
| 重试与容错策略 | 有界重试、失败后重新执行、straggler 缓解和原子正式输出选择；AI 任务额外要求正式输出持久化。 | P04: *MapReduce: Simplified Data Processing on Large Clusters* | `tokenshare-paper-tex/P04_mapreduce.tex` | <https://research.google.com/archive/mapreduce-osdi04.pdf> |
| Verifier committee 的高风险阈值 | 未来高风险任务的 verifier committee 可以参考 `3f+1` 成员与 `2f+1` quorum；第一版不实现完整 PBFT 复制。 | P11: *Practical Byzantine Fault Tolerance* | `tokenshare-paper-tex/P11_pbft.tex` | 原草稿源：<https://www.usenix.org/conference/osdi-99/presentation/practical-byzantine-fault-tolerance>；下载源：<https://pdos.csail.mit.edu/6.824/papers/castro-practicalbft.pdf> |
| 贡献记录与结算 | credit 只在验证通过后发放；失败、迟到和重复执行都产生新 attempt，不覆盖历史；达到上限停止消耗资源。 | P05: *BOINC: A Platform for Volunteer Computing* | `tokenshare-paper-tex/P05_boinc_platform.tex` | <https://boinc.berkeley.edu/boinc_a_platform_for_volunteer_computing.pdf> |
| 语义任务：latent quality 与 contributor reliability | 用 EM 同时估计真实标签和观察者错误率，作为未来 semantic task 的 contributor reliability/latent quality 基础模型。 | P12: *Maximum Likelihood Estimation of Observer Error-Rates Using the EM Algorithm* | `tokenshare-paper-tex/P12_dawid_skene.tex` | 原草稿源：<https://doi.org/10.2307/2346806>；下载源：<https://crowdsourcing-class.org/readings/downloads/ml/EM.pdf> |
| 语义验证：任务难度与 worker 能力 | 未来 semantic verification 同时建模 item difficulty 与 annotator ability，而不是只按多数投票。 | P13: *Whose Vote Should Count More: Optimal Integration of Labels from Labelers of Unknown Expertise* | `tokenshare-paper-tex/P13_glad.tex` | <https://papers.nips.cc/paper_files/paper/2009/file/f899139df5e1059396431415e770c6dd-Paper.pdf> |
| 语义贡献者质量：低质/恶意标注者处理 | 估计 annotator competence，用于识别低质量、不稳定或恶意 semantic contributors。 | P14: *Learning Whom to Trust with MACE* | `tokenshare-paper-tex/P14_mace.tex` | <https://aclanthology.org/N13-1132.pdf> |
| 语义接受分数 | 结合多标注者标签与模型预测，输出 consensus label、label quality score 和 annotator quality，作为 future semantic acceptance score 的结构参照。 | P15: *CROWDLAB: Supervised Learning to Infer Consensus Labels and Quality Scores for Data with Multiple Annotators* | `tokenshare-paper-tex/P15_crowdlab.tex` | <https://arxiv.org/pdf/2210.06812> |
| AI 执行日志与工具 provenance | 将 reasoning trace 与 tool action 的交错过程记录为 execution log、tool metadata、environment hash 和 artifact 引用。 | P18: *ReAct: Synergizing Reasoning and Acting in Language Models* | `tokenshare-paper-tex/P18_react.tex` | <https://arxiv.org/pdf/2210.03629> |
| Lean 证明插件：proof state、premise retrieval、verifier | 未来 Lean adapter 可接入 proof state、premise retrieval 和 verifier/replay 的工作流。 | P19: *LeanDojo: Theorem Proving with Retrieval-Augmented Language Models* | `tokenshare-paper-tex/P19_leandojo.tex` | <https://proceedings.neurips.cc/paper_files/paper/2023/file/4441469427094f8873d0fecb0c4e1cee-Paper-Datasets_and_Benchmarks.pdf> |
| Lean 实验 benchmark | 使用 miniF2F 子集作为 Lean/formal theorem proving 实验 benchmark，而不是把 benchmark 写死进协议核心。 | P20: *miniF2F: a Cross-System Benchmark for Formal Olympiad-Level Mathematics* | `tokenshare-paper-tex/P20_minif2f.tex` | <https://arxiv.org/pdf/2109.00110> |
| Lean verifier 环境固定 | 固定 Lean/mathlib/lake 环境 hash，确保 proof replay 可重复。 | P21: *The Lean Mathematical Library* | `tokenshare-paper-tex/P21_lean_mathlib.tex` | 原草稿源：<https://doi.org/10.1145/3372885.3373824>；下载源：<https://leanprover-community.github.io/papers/mathlib-paper.pdf> |
| 路由/认领/指派机制 | 将 task announcement、bid、award/assignment 看作受更强协议约束的任务分配；TokenShare 额外加入 DAG guard、verification、ledger 和 delayed settlement。 | P22: *The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem Solver* | `tokenshare-paper-tex/P22_contract_net_protocol.tex` | 原草稿源：<https://doi.org/10.1109/TC.1980.1675516>；下载源：<https://www.reidgsmith.com/The_Contract_Net_Protocol_Dec-1980.pdf> |
| 本地协议实验与故障注入 | 将 offline、slow、invalid output、late submission 等故障配置与业务逻辑隔离，并保证实验参数可复现。 | P08: *Lowering Entry Barriers to Developing Custom Simulators of Distributed Applications and Platforms with SimGrid* | `tokenshare-paper-tex/P08_simgrid.tex` | 原草稿源：<https://hal.science/hal-04909441v1>；下载源：<https://www.osti.gov/servlets/purl/2538150> |

## 完整 TeX 索引

| ID | 论文 | 本地 TeX |
|---|---|---|
| P01 | *Dryad: Distributed Data-Parallel Programs from Sequential Building Blocks* | `tokenshare-paper-tex/P01_dryad.tex` |
| P02 | *CIEL: A Universal Execution Engine for Distributed Data-Flow Computing* | `tokenshare-paper-tex/P02_ciel.tex` |
| P03 | *Cilk: An Efficient Multithreaded Runtime System* | `tokenshare-paper-tex/P03_cilk_spawn_tree_precedence_dag.tex` |
| P04 | *MapReduce: Simplified Data Processing on Large Clusters* | `tokenshare-paper-tex/P04_mapreduce.tex` |
| P05 | *BOINC: A Platform for Volunteer Computing* | `tokenshare-paper-tex/P05_boinc_platform.tex` |
| P06 | *Common Workflow Language, v1.0* | `tokenshare-paper-tex/P06_common_workflow_language.tex` |
| P07 | *Mesos: A Platform for Fine-Grained Resource Sharing in the Data Center* | `tokenshare-paper-tex/P07_mesos.tex` |
| P08 | *Lowering Entry Barriers to Developing Custom Simulators of Distributed Applications and Platforms with SimGrid* | `tokenshare-paper-tex/P08_simgrid.tex` |
| P09 | *Ray: A Distributed Framework for Emerging AI Applications* | `tokenshare-paper-tex/P09_ray.tex` |
| P10 | *In Search of an Understandable Consensus Algorithm (Extended Version)* | `tokenshare-paper-tex/P10_raft.tex` |
| P11 | *Practical Byzantine Fault Tolerance* | `tokenshare-paper-tex/P11_pbft.tex` |
| P12 | *Maximum Likelihood Estimation of Observer Error-Rates Using the EM Algorithm* | `tokenshare-paper-tex/P12_dawid_skene.tex` |
| P13 | *Whose Vote Should Count More: Optimal Integration of Labels from Labelers of Unknown Expertise* | `tokenshare-paper-tex/P13_glad.tex` |
| P14 | *Learning Whom to Trust with MACE* | `tokenshare-paper-tex/P14_mace.tex` |
| P15 | *CROWDLAB: Supervised Learning to Infer Consensus Labels and Quality Scores for Data with Multiple Annotators* | `tokenshare-paper-tex/P15_crowdlab.tex` |
| P16 | *Decomposed Prompting: A Modular Approach for Solving Complex Tasks* | `tokenshare-paper-tex/P16_decomposed_prompting.tex` |
| P17 | *Tree of Thoughts: Deliberate Problem Solving with Large Language Models* | `tokenshare-paper-tex/P17_tree_of_thoughts.tex` |
| P18 | *ReAct: Synergizing Reasoning and Acting in Language Models* | `tokenshare-paper-tex/P18_react.tex` |
| P19 | *LeanDojo: Theorem Proving with Retrieval-Augmented Language Models* | `tokenshare-paper-tex/P19_leandojo.tex` |
| P20 | *miniF2F: a Cross-System Benchmark for Formal Olympiad-Level Mathematics* | `tokenshare-paper-tex/P20_minif2f.tex` |
| P21 | *The Lean Mathematical Library* | `tokenshare-paper-tex/P21_lean_mathlib.tex` |
| P22 | *The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem Solver* | `tokenshare-paper-tex/P22_contract_net_protocol.tex` |
