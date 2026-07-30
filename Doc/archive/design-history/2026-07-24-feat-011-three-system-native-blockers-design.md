# Feat-011 三个 system-native 阻塞问题设计

> 状态：已实施并通过定向、Fast、Lean canary 与 Full 验证  
> 日期：2026-07-24  
> 范围：只处理 catalog execution view、selected-unit runtime scope、Factorization 非对称完成语义

## 1. 目标与边界

本轮保持正式执行链：

`paper runner → dispatch_paper_case → ProtocolRunRequest → ProtocolRunCoordinator → ProtocolEngine → ledger/artifact → paper projection`

实验层只冻结条件、选择和诊断执行范围，不直接推进协议状态。本设计不修改 provider timeout、retry、模型、prompt、catalog 样本量或实验矩阵，不增加安全工程、攻击防护或正式真实 API 调用。

## 2. 冻结 catalog execution view

新增版本化 `PaperCatalogExecutionView`，将以下信息作为 dispatch plan 的冻结 identity：

- view kind：manifest-backed、Experiment 3 或 Experiment 4；
- 原始 manifest digest；
- Exp1/2/5 所需 `task15_budget_input`、Task 14 readiness 和可选 worker preflight；
- Exp3/Exp4 已准备的专用 mapping view；
- canonical body digest。

planning 只从 `_gate_c_context()` 构造一次 view；`PaperExperimentDispatchPlan` 保存其 body/digest。formal execution 从 plan 中恢复同一 view，并以调用方提供的 manifest 仅校验 digest、重建 manifest-backed 方法，不从当前可变文件重新推导 Task 14/Exp3/Exp4 view。resume 通过 formal suite identity 中的 dispatch plan body 校验相同 view digest；replay-only 只读冻结 evidence。

`_validate_canonical_selection()` 保持不变，selection body/digest 必须由 planning 和 execution 的同一 view 得到。

## 3. selected-unit 是通用 runtime execution scope

新增版本化 `ProtocolExecutionScope`：

- `whole_root`：现有正式 FULL/fault/ablation 行为；
- `selected_ai_units`：只允许调度冻结的 planned AI-unit id，作为 pilot 诊断范围。

root registration、root deterministic execution、plugin split/expand 仍完整执行。child `TaskUnit.metadata` 保存通用 `planned_ai_unit_id`，coordinator 将允许集合传给 scheduler；指定 unit 仍产生真实 lease、attempt、request、submission、verification、canonical 事实。

selected scope 达到指定 unit 终态后返回 `partial` runtime observation。未选 sibling 保持原状态，不创建 provider attempt、不写 canonical；root 不 merge、不 completion、不 settlement。paper projection 将其明确标为 `partial`、`paper_eligible=false`。

Factorization/Lean paper adapter 的当前可达 selected 分支统一调用现有 coordinator helper。旧直接构造 request/submission/verification/merge 的生命周期代码从当前入口移除；若暂时保留历史 helper，也不得被 dispatcher/CLI 调用。

## 4. Factorization 非对称 completion readiness

新增领域无关、版本化 `MergeReadinessDecision`，由 plugin runtime 根据已经存在的 canonical child 和 terminal child failure 返回：

- `ready`：列出本次 merge 使用的 child unit；
- `wait`：证据尚不足；
- `failed`：证据已终止且不能完成。

通用 coordinator 不读取 `found_factor`。它只校验 decision 引用的是扩图产生的 child，并让 engine/merge coordinator 记录被选择的 canonical slot binding、merge、parent completion 和 settlement。

Factorization plugin 实现：

- 任一 verifier-accepted canonical `found_factor`：选择有效 witness slot，OR-ready；其他 terminal failure 不推翻；
- 没有 witness：仅当全部 required range canonical 且完整 coverage 时 AND-ready；
- 没有 witness且存在 terminal child failure：failed；
- raw、parse failure、verifier rejection 没有 canonical range output，不能成为 witness。

Factorization merge policy 升级为 v2：允许“单个有效 witness slot”或“全部 no-factor slots”两种输入形状；slot identity、range binding、factor/cofactor 和完整 no-factor coverage 校验保持严格。Lean runtime 返回现有 all-required decision，行为不变。

已经并发启动的 worker batch 仍先完整落账其结果，再评估 readiness；不实现 sibling cancellation，也不删除或改写已有 evidence。

## 5. 验证策略

每个问题先新增稳定失败的 RED，再改实现：

1. Exp1 Factorization→Lean execution view identity，以及 resume/replay drift；
2. 双领域 selected-unit coordinator/ledger/partial observation 和 dispatcher 调用证明；
3. Factorization witness+provider error、witness+verification rejection、无 witness failure、全 no-factor、伪 witness、并发 evidence。

随后运行用户指定影响测试、固定 Lean canary、Fast 和 Full。全部使用 scripted/capturing transport，真实 provider calls、tokens、cost 均为 0。
