# TokenShare Agent 导航

日期：2026-06-29

状态：工作流导航文档。本文只回答“新 AI 遇到问题应先看哪里、代码应放到哪个模块、外部参考资料应如何落库和使用”。本文不是协议设计规格，不覆盖 TDD，不包含下一步任务提示词。

## 1. 使用时机

新的 AI 在完成 `AGENTS.md` 的启动要求后，如果需要判断事实来源、模块归属、参考资料边界、联网资料落库方式或下一步阅读顺序，应阅读本文。

本文的定位是导航层：

- 不替代 `AGENTS.md` 的工作规则。
- 不替代 `feature_list.json` 的 feature 状态。
- 不替代 `progress.md` 和 `session-handoff.md` 的当前进展。
- 不替代 TDD 的协议设计。
- 不把 `reference_repos/` 变成 TokenShare runtime 依赖。
- 不允许把已经影响设计或代码的联网资料只停留在浏览器结果或聊天回答里。

## 2. 事实源优先级

后续 AI 应按以下顺序判断当前项目事实：

1. `AGENTS.md`：最高优先级工作规则、范围边界、验证和完成标准。
2. `feature_list.json`：当前 active track / active feature 和 feature 状态；2026-06-29 `feat-007` Lean 插件、`feat-008` Phase 7 AI API executor、`feat-009` Phase 8 实验基础设施均已完成，当前 active feature 是 `feat-010` Phase 9 replay / audit。
3. `progress.md`：最近进展、风险、验证证据、下一步。
4. `session-handoff.md`：跨会话恢复摘要，尤其是上一轮未解决问题。
5. `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`：当前实现导向设计；已吸收 P01-P22 候选机制的 V1 取舍。
6. `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`：Phase 1 最小对象字段、事件 envelope 和 SQLite 可重建索引规格；用于细化 feat-002。
7. `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`：Phase 2 最小对象、字段、状态机、事件顺序和 SQLite 投影规格；用于细化 feat-003。
8. `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md`：Phase 2 代码、规格章节和测试的对应关系；用于确认 feat-003 实现边界。
9. `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`：Phase 3 插件、执行器、执行请求、执行提交、artifact 边界和 attempt submission 状态推进草案；用于理解 feat-004 字段依据。
10. `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md`：Phase 3 代码、规格章节和测试的对应关系；用于确认 feat-004 实现边界。
11. `Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md`：Phase 4 字段规格与 TDD 计划；用于直接指导 feat-005 实现。
12. `Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`：Phase 4 当前 Task 1/2/3/4/5/6/7/8/9/10 代码、规格章节、projection 和测试的对应关系；用于确认已实现边界和 review hardening。
13. `Doc/TechnicalDocument/2026-06-24-phase-4-discussion-notes.md`：Phase 4 验证、正式输出、插件拆分策略、独立 `MergePlan` 和原子扩图讨论记录；用于追溯 feat-005 字段规格的讨论来源。
14. `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md`：Phase 5 merge、expected output resolution、contribution、settlement 和 pruning 字段规格 / TDD 计划；用于直接指导 feat-006 实现。
15. `Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md`：Phase 5 当前 Task 1 / Task 2 / Task 3 / Task 4 / Task 5 / Task 6 / Task 7 / Task 8 代码、规格章节、projection 和测试的对应关系；用于确认 feat-006 已实现边界和 Task 8 SQLite projection / integration 验证。
16. `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md`：Phase 5 merge 主闭环外部系统调研备忘；用于追溯外部系统经验，不是实现规格。
17. `Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md`：Phase 5 merge 讨论记录；当前已确认 merge 作为普通 `TaskUnit`，并采用 required slots 齐备后再创建 merge `TaskUnit` 的方案。
18. `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`：Phase 6 factorization 插件第一版字段规格 / TDD 计划；用于直接指导 factorization 插件实现。
19. `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-code-map.md`：Phase 6 factorization 插件第一切片 Task 1-13 source、tests、字段规格章节、验证证据、状态同步和协议边界映射；用于确认 Factorization 子范围已实现边界、plugin-owned prompt package wiring 和 plugin-owned AI output parse policy 入口。
20. `Doc/TechnicalDocument/2026-06-28-phase-6-lean-real-plugin-scope-change.md`：Phase 6 Lean 插件范围变更记录；用于覆盖旧 “Lean stub / synthetic fixture only / 完整 Lean theorem proving 属于 V1 外” 口径，后续 Lean 字段规格必须以它为准。
21. `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-tdd.md`：Phase 6 真实 Lean proof 插件 TDD 设计稿；用于直接指导 `lean_proof` 插件本体、结构化 theorem payload、Lean-side deterministic tactics、固定 toolchain / fixture project、checker artifact、split / merge、Phase 8 ready path 和红绿实现任务。
22. `Doc/TechnicalDocument/2026-06-29-phase-6-lean-toolchain-setup-notes.md`：Phase 6 Lean 工具链配置记录；用于确认本机 `elan` / Lean / lake 版本、下载校验、安装路径和 fixture project 构建证据。
23. `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md`：Phase 6 真实 Lean proof 插件当前实现映射；用于确认 Task 1-15 source、Lean helper、tests、协议边界、Phase 8 ready path、replay evidence guard 和验证证据。
24. `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`：Phase 6 factorization 插件第一版拆分算法和主 TDD 对齐讨论记录；用于追溯“插件主导候选因子搜索空间分区”、同一个整数分解插件、canonical output 驱动递归展开的已确认决策，不覆盖字段规格。
25. `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md`：Phase 7 实验级 AI API executor 字段规格草案；用于后续实现 SiliconFlow-only 第一版 adapter、均匀随机选择、有界 provider failover、插件拥有解析、artifact/provenance/secret/replay 边界。
26. `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`：Phase 7 实验级 AI API executor TDD 实施规划；用于按红绿步骤实现 config、descriptor、transport、selector、executor、parser bridge、secret redaction、replay guard、smoke gate 和状态同步。
27. `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`：Phase 7 AI API executor 代码、测试、字段规格章节、验证证据和协议边界映射；用于确认 feat-008 已实现边界。
28. `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.tex` / `.pdf`：最新真实插件实验设计；用于指导论文实验、Experiment 1-4、Phase 8 experiment runner、failure injection、ablation、metrics/report 和跨插件泛化验收；覆盖旧 toy / stub 实验口径。
29. `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-tdd.md`：Phase 8 实验基础设施 TDD 设计文稿；用于实现通用实验 runner、插件适配契约、故障模拟、消融、metrics/report、结构化本地输出和 Lean pending / ready 门禁。
30. `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`：Phase 8 第一版实验基础设施 source/tests/边界和验证证据映射；用于确认 `feat-009` 已实现边界、AI profile、Lean AI 50 benchmark 和后续扩展点。
31. `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md`：Phase 2 协调边界备忘录；用于提醒后续 agent 不要让 `RootTaskRegistrar` 继续承担状态机、调度或存储编排增长职责。
32. `README.md`：项目入口和稳定边界。
33. `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：论文、技术报告和本地 TeX/OCR 映射；用于追踪研究依据。
34. `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`：P01-P22 机制整合记录；用于追溯取舍理由，不覆盖主 TDD。
35. `Doc/TechnicalDocument/2026-06-02-tokenshare-protocol-kernel-revised-draft.md`：历史讨论稿；用于理解原因，不直接覆盖当前 TDD。
36. `reference_repos/`：外部参考源码；只能用于借鉴模式，不属于 TokenShare runtime。

如果两个文件冲突，应优先相信上面列表中更靠前的文件，并把冲突记录到 `progress.md` 或 `session-handoff.md`。

## 3. 遇到问题时看哪里

| 问题 | 首先阅读 | 辅助阅读 | 注意事项 |
|---|---|---|---|
| 当前要做哪个 feature / track | `feature_list.json` | `progress.md` | 当前 active feature 是 `feat-010` Phase 9 replay / audit；`feat-007` 真实 Lean proof plugin、`feat-008` Phase 7 AI API executor、`feat-009` Phase 8 已完成，只有发现回归或小范围集成缺口时才返回对应已完成 feature。 |
| 启动和验证怎么跑 | `AGENTS.md` | `init.ps1`、`init.sh`、`README.md` | 当前基线会运行 `compileall` 和 `pytest tests`。 |
| 读取或搜索仓库文件 | 本文第 4 节 | `AGENTS.md` | 默认使用 PowerShell，并显式指定 UTF-8；常规检索不要使用 `rg`。 |
| V1 做什么、不做什么 | `AGENTS.md` | `README.md`、TDD 第 3、4 节、`Doc/TechnicalDocument/2026-06-28-phase-6-lean-real-plugin-scope-change.md` | 不要扩大到真实区块链、真实分布式 runtime 或生产级 AI 平台；Phase 6 必须实现本地真实 Lean checker 驱动的形式化证明插件，但不做生产级 theorem-proving 平台、LeanDojo 训练/检索平台或动态 Lean 服务。实验级 AI API executor、实验基础设施、故障模拟、指标和 replay/audit 是后续独立 feature。 |
| 判断 P01-P22 机制如何进入 V1 | TDD 第 4.3、21 节 | `Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md`、论文映射 | 主 TDD 是实现口径；候选规范只用于追溯来源和被整合前的冲突。 |
| 技术栈是什么 | TDD 第 20 节 | `README.md`、`progress.md` | V1 是 Python 3.12+、SQLite、JSON、JSONL、本地文件系统。 |
| package layout 在哪里 | TDD 第 20.4 节 | `reference_repos/README.md`、`README.md` | 已创建 `src/tokenshare` 和镜像 `tests` 骨架。 |
| 具体代码应该放哪个模块 | 本文第 5 节 | TDD 第 5、6、10、11、20 节 | 先守住协议框架、插件、执行器三层边界。 |
| 判断 Phase 2 对象字段、状态机和事件顺序 | `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md` | `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md`、TDD 第 9、11、12 节 | 先区分 `TaskUnit`、`Lease`、`Attempt` 三条状态线，再实现调度、租约和事件投影。 |
| 判断 Phase 2 代码与测试覆盖 | `Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` | `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`、`tests/core/test_task_graph.py`、`tests/test_phase2_scheduling_flow.py` | feat-003 已实现最小 `TaskGraph`、状态机、scheduler、lease manager、event projection 和 event-backed flow；不要把 Phase 3+ 插件/executor/验证逻辑误认为已实现。 |
| 判断 Phase 2 编排入口和 `RootTaskRegistrar` 边界 | `Doc/TechnicalDocument/2026-06-07-phase-2-coordination-debt-memo.md` | `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`、本文第 5 节、TDD 第 5、7、9、12 节 | `RootTaskRegistrar` 是 Phase 1 临时协调器，不应继续承载 `TaskGraph`、`Scheduler`、`LeaseManager` 或 attempt 状态机增长。 |
| 判断 Phase 3 插件与执行器字段草案 | `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md` | 主 TDD 第 4.3、8、12、21 节；`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` | 字段草案确认 request/submission/descriptor artifact 化、`AllocationDecision` 内联、executor status 最小枚举、SQLite index-only projection；feat-004 已实现 submission 后推进 `Attempt.Running -> Submitted`，但不进入验证或 canonical。 |
| 判断 Phase 3 代码与测试覆盖 | `Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` | `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`、`tests/test_phase3_execution_flow.py`、`tests/storage/test_phase3_event_projection.py` | feat-004 已实现 registry freeze、统一 request/submission、mock AI executor、deterministic executor、Phase 3 event 和 SQLite index-only projection；不要把 Phase 4 验证/canonical/expansion 误认为已实现。 |
| 判断 Phase 4 验证、canonical 和 expansion 边界 | `Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md` | `Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`、`Doc/TechnicalDocument/2026-06-24-phase-4-discussion-notes.md`、主 TDD 第 4.3、8、9、10、11、12、21 节、`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` | 新规格是 feat-005 实现口径；Task 1/2/3/4/5/6/7/8/9/10 当前代码边界见 Phase 4 code map；`DecompositionProposal` 必须由插件版本化拆分策略直接生成；AI、executor 或客户端不能提供 expansion 候选拆分或临时提出协议级子任务；`MergePlan` 独立持久化。 |
| 判断 Phase 5 merge / contribution / settlement 实现边界 | `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md` | `Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md`、`Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md`、`Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md`、主 TDD 第 9、11、12、13、21 节、`Doc/TechnicalDocument/2026-06-22-p01-p12-tokenshare-candidate-mechanism-spec.md` 第 22.5、24.4 节、Phase 4 code map | Phase 5 字段规格是 feat-006 实现口径；它固定 `merge_task_creation_batch`、`merge_resolution_batch`、`parent_completion_batch`、`settlement_batch`、`subtree_pruning_batch`，以及 `MergeTaskLink`、`MergeRecord`、`ExpectedOutputResolution`、`ContributionRecord`、`SettlementRecord`、`SubtreePruneRecord` 和 SQLite projection / TDD 计划。`code map` 记录当前已实现 Task 1 / Task 2 / Task 3 / Task 4 / Task 5 / Task 6 / Task 7 / Task 8；讨论记录和调研备忘只用于追溯取舍。 |
| 判断 Phase 6 experimental plugins 实现范围 | `feature_list.json`、主 TDD 第 21 节 | `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`、`Doc/TechnicalDocument/2026-06-28-phase-6-lean-real-plugin-scope-change.md`、`Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-tdd.md`、`Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`、`src/tokenshare/plugins/contracts.py` | 2026-06-29 状态修正：Phase 6 只保留 factorization 和真实 Lean 形式化证明插件，且两者当前均已完成既定 V1 切片。structured report stub 已从开发计划剔除，历史文档或测试中的 `structured_report_stub` 只作为 provenance / 通用夹具命名。2026-06-28 用户确认：Lean 插件不再是 stub，必须实现本地真实 Lean checker 驱动的形式化证明能力，且拆分算法由插件内确定性规则自动生成子任务。2026-06-29 Lean TDD 固定：结构化 theorem payload 解决 context/import/namespace/变量边界，elaboration 和 deterministic decomposition policy 放在 Lean-side helper / metaprogram / tactic runner；Python 只做协议编排、artifact 和 checker/helper bridge。实验级 AI API executor 属于 `feat-008` / Phase 7；`SimulationProfile`、`SimulationWrapper`、`ExperimentRunner`、`MetricsCollector`、故障模拟和指标报告属于 `feat-009` / Phase 8。 |
| 判断 Phase 6 真实 Lean proof 插件字段规格 / TDD | `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-tdd.md` | `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md`、`Doc/TechnicalDocument/2026-06-29-phase-6-lean-toolchain-setup-notes.md`、`Doc/TechnicalDocument/2026-06-28-phase-6-lean-real-plugin-scope-change.md`、`Doc/TechnicalDocument/lean_proof_decomposition_rules.tex`、`src/tokenshare/plugins/contracts.py`、`src/tokenshare/executors/contracts.py`、`src/tokenshare/experiments/lean_adapter.py`、Phase 8 code map | 该 TDD 是 `lean_proof` 实现口径：新增 `src/tokenshare/plugins/lean_proof/`，固定 pinned Lean / lake toolchain 和 fixture project，结构化 theorem payload，Lean-side deterministic tactics / split certificate / merge skeleton，真实 checker report 和 `EnvironmentRef` artifact，direct proof 与 decomposition / merge fixture，以及 Phase 8 adapter ready path；当前 code map 记录 Task 1-15 已实现，包括 prompt/parser、child proof、merge proof、协议端到端、Phase 8 ready path 和 replay evidence guard；`lean_stub` 只能作为历史占位或 deprecated compatibility。 |
| 判断 Phase 6 factorization 插件字段规格 / TDD | `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md` | `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-code-map.md`、`Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`、主 TDD 第 4.3、7、8、12、14.1、21、23 节；`Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md`、`Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md`、`src/tokenshare/plugins/contracts.py` | 字段规格是 factorization 插件第一版实现口径；code map 是 Task 1-13 source、tests、字段规格章节、验证证据、状态同步和协议边界映射。当前已完成 prime / semiprime fixture 端到端闭环，使用插件主导 `candidate_range_partition.v1`、bounded `factor_search_range`、plugin-owned prompt package、plugin-owned AI output parse policy、deterministic `range_result` verification、all-required merge、现有 Phase 4/5 events；raw-only 不能作为 Factorization 成功输出；early success、sibling pruning、composite cofactor 完整递归 resolution 明确不属于第一切片。讨论记录只用于追溯取舍。 |
| 判断 Phase 7 AI API executor 边界 | `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md` | `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`、`Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`、`feature_list.json`、主 TDD 第 21 节、`src/tokenshare/executors/contracts.py`、`Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`、`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` | Phase 7 只实现实验级 AI API executor：第一版固定 SiliconFlow OpenAI-compatible chat completions adapter，通过统一 `ExecutionRequest` / `ExecutionSubmission` 调用真实模型并持久化 raw / parsed / parse failure / usage / latency / cost / error provenance。标准 executor config 只保存 `api_key_env`；真实 smoke 可从被 gitignore 的 `local/ai_api_smoke.local.json` 读取本地 key 并注入当前进程环境变量，secret 不写入 event、artifact、SQLite、日志或 config digest。均匀随机和自动换 API 只能作为同一 request 内有界 provider failover，不得创建协议级 retry、改任务图、绑定 canonical output 或决定奖励；replay 不得重新调用 AI API。code map 记录当前 feat-008 source/tests/验证证据和已实现边界；真实 SiliconFlow smoke test 默认跳过，必须显式启用。 |
| 判断最新实验设计 / 论文实验口径 | `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.tex` | `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.pdf`、Phase 6 factorization code map、Lean scope change、Lean code map、Phase 7 code map、Phase 8 code map | 实验必须围绕真实插件边界、真实 descriptor、真实 artifact、真实 parser/verifier/merge policy 和真实 protocol lifecycle；Experiment 4 必须使用真实 Lean proof plugin / Lean adapter，没有 checker logs 和 `EnvironmentRef` 时只能标记 blocked / pending，不能用 `lean_stub` 结果替代。 |
| 判断 Phase 8 实验基础设施、故障模拟与指标边界 | `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-tdd.md`、`Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`、`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.tex`、`feature_list.json`、主 TDD 第 21 节 | `Doc/agent-navigation.md`、`src/tokenshare/experiments/`、`tests/experiments/`、`src/tokenshare/plugins/contracts.py`、`src/tokenshare/executors/contracts.py` | `feat-009` / Phase 8 第一版已实现 `SimulationProfile`、`SimulationWrapper`、`ExperimentRunner`、`PluginExperimentAdapter`、factorization adapter、Lean adapter ready path、metrics/report 和默认 Experiment 1-4 suite。2026-06-30 追加 `run_ai_profile` / `run_all --run-ai-profile`，用于显式比较 deterministic vs AI API executor 的 raw/parsed/parse-failure、usage/cost/latency、provider/model、parser success 和 retry 指标；默认使用 fake transport，不要求联网或 secret。2026-07-02 追加 `run_lean_ai_benchmark`，用于 50 个当前 Lean helper 支持的 `P ∧ Q` / `P ↔ Q` proof 子任务，默认 scripted transport，`--real-transport` 才调用真实 SiliconFlow API。Lean adapter 默认运行真实 checker evidence；仍保留可注入 blocked preflight 回归路径。不得把 toy demo 或 stub Lean 结果作为通过证据，也不得把这些能力放入 Phase 6 插件、Phase 7 executor 或 Phase 9 replay core。 |
| 需要联网查找资料 | 本文第 6 节 | `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`、`reference_repos/README.md` | 被用于项目决策的外部资料必须本地落库并同步索引。 |
| 需要借鉴已有项目结构 | `reference_repos/README.md` | 对应外部源码目录 | 先拉取或更新本地浅克隆/sparse checkout；只能借鉴思路，不引入为 runtime 依赖，不复制大段实现。 |
| 需要设计对象字段 | `Doc/TechnicalDocument/2026-06-05-phase-1-minimal-object-field-spec.md`；Phase 2 使用 `Doc/TechnicalDocument/2026-06-08-phase-2-minimal-field-state-event-spec.md`；Phase 3 使用 `Doc/TechnicalDocument/2026-06-23-phase-3-plugin-executor-field-spec.md`；Phase 4 使用 `Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md`；Phase 5 使用 `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md`；Phase 6 factorization 使用 `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-field-spec.md`；Phase 7 AI API executor 使用 `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-field-spec.md` 和 `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-tdd-plan.md`；Phase 8 experiment infrastructure 使用 `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-tdd.md` | TDD 第 6、8、9、10、11、12、20、21、23 节；协议讨论稿第 6、7、10、11 节 | 先区分协议对象名、逻辑决策、字段名、artifact 类型、event payload、SQLite 表名和组件名，再写实现。 |
| 需要更新状态 | `progress.md` | `feature_list.json`、`session-handoff.md` | 没有验证证据，不要标记完成。 |

## 4. 工具与编码规则

本仓库在 Windows / PowerShell 环境中维护，中文文档很多。为了避免编码乱码、重复误判和无效上下文消耗，后续 agent 必须遵守以下工具约定：

- 读取 Markdown、JSON、脚本和代码时显式使用 UTF-8：`Get-Content -Encoding UTF8 <path>`。
- 搜索文本时使用 PowerShell：`Select-String -Path <files> -Pattern "<pattern>"`；多文件递归先用 `Get-ChildItem -Recurse` 选定范围，再传给 `Select-String`。
- 枚举文件时使用 PowerShell：`Get-ChildItem`，需要递归时加 `-Recurse`，需要隐藏文件时加 `-Force`。
- 常规仓库检索不要使用 `rg`。除非用户明确要求或 PowerShell 命令无法完成，否则不要把 `rg` 作为默认搜索工具。
- 写入 JSON 时保持 UTF-8，并先验证 JSON 可解析；例如用 `conda run -n tokenshare python -c "import json; ..."` 检查。
- Bash / WSL 仅用于运行 `./init.sh` 或用户明确要求的跨 shell 验证；不要用它替代 PowerShell 做常规仓库阅读和搜索。

推荐命令模板：

```powershell
Get-Content -Encoding UTF8 AGENTS.md
Get-ChildItem -Recurse -File Doc | Select-String -Pattern "外部参考资料"
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
```

## 5. 模块路由

当前 package layout 已确定为 `src/` layout；其中 `factorization/` 和 `lean_proof/` 是已实现的 Phase 6 真实插件目录，`lean_stub/` 是历史占位名 / deprecated compatibility，不能作为通过证据。structured report stub 已从 Phase 6 开发计划剔除；若后续保留 `structured_report_stub` 目录或测试名，只作为历史夹具 / contract 示例，不作为待开发插件目标。`experiments/` 是独立实验基础设施目录，当前已完成 Phase 8 第一版。

```text
src/
  tokenshare/
    core/
    storage/
    plugins/
      factorization/
      lean_proof/
      lean_stub/
    executors/
    replay/
    experiments/

tests/
  core/
  storage/
  plugins/
    factorization/
    lean_stub/
  executors/
  replay/
  experiments/
```

模块职责：

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `tokenshare.core` | 协议对象、状态枚举、任务图、状态机、协议配置和不变量。 | factorization/Lean/历史 structured report fixture 领域规则、文件系统细节、executor 调用细节。 |
| `tokenshare.storage` | `ArtifactStore`、JSONL `EventLedger`、SQLite materialized index、本地路径和内容哈希实现。 | 协议决策逻辑、插件验证规则。 |
| `tokenshare.plugins` | 插件契约、descriptor、factorization PoC、真实 Lean proof 插件；历史 structured report fixture 仅保留为旧测试/contract 示例时不得扩大为 Phase 6 目标。 | 协议状态机推进、租约调度、正式输出绑定；Lean 规则不得进入 `tokenshare.core`。 |
| `tokenshare.executors` | 执行器契约、mock executor、确定性程序执行器、实验级 AI API executor。 | 任务图修改、验证结论绑定、结算逻辑、生产级 AI 平台或 secret 持久化。 |
| `tokenshare.replay` | 状态重放、审计重放、replay consistency、no-double-settlement 检查。 | 重新调用 AI 或 executor 来生成历史输出。 |
| `tokenshare.experiments` | experiment runner、fault simulation、metrics/report、AI profile 和 Lean AI benchmark CLI。 | 协议核心不可替代的状态事实；不得在实验层重新定义插件验证权威或 replay 历史事实。 |
| `tests/*` | 与 `src/tokenshare/*` 镜像的单元、集成、故障和 replay 测试。 | 外部参考项目测试。 |

## 6. 外部参考资料落库与使用规则

联网查找资料后，如果资料被用于修改 TokenShare 的设计、代码、测试、README、feature 路线或其他项目文档，必须先完成本地落库和索引同步。只在最终回答或 TDD 里留下 URL 不算完成。

论文、技术报告和正式 PDF：

- 下载 PDF 或可复查的正式文本；如果已经转换/OCR，应把 TeX 或纯文本放入 `Doc/TechnicalDocument/tokenshare-paper-tex/`。
- 在 `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md` 中记录论文标题、来源 URL、本地文件路径、下载/转换日期、借鉴到的模块或设计点。
- 如果因版权、登录或访问限制不能保存全文，必须记录原因、可访问的元数据、本地摘要、访问日期和该资料影响了哪些项目文档；不要把无法复查的资料作为唯一依据。

开源项目和工程实现：

- 将被借鉴的项目浅克隆或 sparse checkout 到 `reference_repos/`，固定 commit 或 tag。
- 更新 `reference_repos/README.md`，记录上游仓库、commit、拉取范围、选择原因和观察重点。
- 不要把外部项目加入 TokenShare runtime dependency。
- 不要让 `pytest` 或 `compileall` 扫描外部项目；启动脚本已排除 `reference_repos/`。
- 不要复制大段外部代码；如果借鉴设计，应在 TDD 或 `progress.md` 中写清楚借鉴的是哪类边界思想。

普通在线文档、规范页面或博客：

- 如果只是回答用户临时问题，可以在回答中引用来源；如果进入项目设计或实现，必须在最相关的设计文档、`progress.md` 或 `session-handoff.md` 中记录来源 URL、访问日期、本地摘要和影响范围。
- 如果该在线文档对应可拉取仓库、版本化文档或 release tag，应优先保存对应仓库或 tag 到 `reference_repos/`，而不是只记录网页。

`reference_repos/` 是研究材料，不是项目源码。使用规则：

- 需要 package layout 思路时，先看 `reference_repos/README.md`。
- 可以打开外部项目源码观察命名、目录边界和测试组织。
- 如果 `reference_repos/README.md` 没有对应条目，先补齐本地拉取和索引，再基于该项目做设计判断。

## 7. 状态更新指引

当导航规则、模块归属、验证命令或外部参考资料边界变化时，应同步更新：

- `AGENTS.md`：只保留强制启动入口和最高优先级规则。
- 本文：维护事实源、模块路由和外部参考资料落库规则。
- `Doc/TechnicalDocument/2026-06-04-tokenshare-paper-module-map.md`：维护论文、技术报告、本地 TeX/OCR 和借鉴模块映射。
- `reference_repos/README.md`：维护外部项目本地拉取索引。
- `README.md`：维护人类入口和仓库地图。
- `progress.md` / `session-handoff.md`：记录本轮变化、验证证据和下一步。
