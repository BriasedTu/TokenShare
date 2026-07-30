# feat-011 自检牵连问题修复设计

## 1. 范围

本修复只处理共享 Exp1 baseline 实现自检确认的三个正确性问题，不改变实验模型、请求参数、catalog、预算或故障类型，也不启动真实 API：

1. Exp1 source 为可信实验失败时，报告仍须先验证冻结的 task/attempt/event/artifact 引用和 source usage，之后才能把 baseline comparison 标记为不可用。
2. 一个 Exp3 condition 聚合多个 case 时，必须保留全部 Exp1 source condition provenance；旧单值字段只有在来源唯一时才可填写。
3. 同一 suite 同时执行 Exp1 和 Exp3 时，Exp1 必须排在 Exp3 之前；CLI 与 formal runner 均在创建输出身份或 dispatch 前拒绝逆序。

## 2. 证据校验与比较可用性分离

`failed_experimental` 是可信实验结果，不是跳过证据校验的理由。metrics 先完成以下检查：source task 身份和终态、attempt/event/artifact 引用、记录 hash、冻结 usage 与 source 记录重算值。只有完整性检查通过后，才根据 source root status 或 usage completeness 决定 `baseline_comparison_eligible`。

失败 source 允许没有 artifact，但冻结的空列表必须与 source task 一致；attempt 和 event 仍必须存在。source task 的 root status 必须与冻结 reference 一致并且是可接受终态，不再硬编码为 `completed`。

## 3. 多来源 provenance

Exp3 condition row 新增稳定排序的 `matched_baseline_condition_ids`。兼容字段 `matched_baseline_condition_id` 在来源恰好一个时保留该值，来源超过一个时写 `null`，避免把首个来源伪装成整个聚合 condition 的唯一基线。

## 4. 依赖顺序

不自动重排用户指定实验，因为重排会悄然改变执行身份。CLI 在创建 output root 前拒绝 `exp3,exp1`；formal runner 对直接调用者做同样校验；输出 identity 的 `cross_experiment_evidence_allowed` 也要求 Exp1 实际位于 Exp3 之前。Exp3-only smoke 的显式 baseline omission 不受影响。

## 5. 验证

按 TDD 先补三类 RED：失败 source 的坏引用必须被发现、多来源 row 不得输出错误单值、逆序必须在输出目录与 transport 前失败。实现后运行相关 metrics/runner/CLI 测试和 `init.ps1` Fast，不运行 Full、真实 API、全量 pytest 或 LeanAudit。
