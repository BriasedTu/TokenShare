# Phase 4 字段规格与 TDD 计划

## 元数据

| 项 | 内容 |
|---|---|
| 日期 | 2026-06-24 |
| 状态 | Field spec / TDD implementation-ready |
| 对应 feature | `feat-005` - Phase 4 - Verification, Canonical Output, and Expansion |
| 上游依据 | `Doc/TechnicalDocument/2026-06-24-phase-4-discussion-notes.md`、`Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md`、`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` |
| 目的 | 把 Phase 4 已完成的设计讨论收束成可直接指导代码实现的对象字段、event payload、SQLite projection、模块切分和 TDD 任务。 |

本文是 Phase 4 开工规格。后续实现应以本文为当前 feature 的字段和测试依据；主 TDD 仍是更高层协议设计来源，Phase 4 discussion notes 是本规格的讨论 provenance。

## 1. 本阶段目标

Phase 4 把 Phase 3 已持久化的 `ExecutionSubmission` 推进成可审计、可重放、可继续扩图或完成的协议事实链：

```text
ExecutionSubmission artifact
-> VERIFICATION_RECORDED
-> CANONICAL_OUTPUTS_BOUND
-> SPLIT_STRATEGY_INVOCATION_RECORDED
-> complete:
     append_batch(completion_batch:{expansion_decision_id})
       1. EXPANSION_DECISION_RECORDED(action=complete)
       2. TASK_UNIT_STATE_CHANGED(... -> Completed)
   expand:
     append_batch(expansion_batch:{expansion_decision_id})
       1. DECOMPOSITION_PROPOSAL_RECORDED
       2. EXPANSION_DECISION_RECORDED(action=expand)
       3. MERGE_PLAN_RECORDED
       4. child TASK_UNIT_CREATED...
       5. child TASK_RELATION_CREATED...
       6. TASK_EXPANDED
```

Phase 4 必须实现四个可验证结果：

1. 候选输出经过通用数据检查、证据覆盖检查和插件领域验证后，写入 `VerificationReport` 与 `VERIFICATION_RECORDED`。
2. 同一个 `TaskUnit` 只能绑定一个 canonical output bundle；`first_verified_bundle` 以 eligible `VERIFICATION_RECORDED.event_seq` 为排序权威。
3. 插件 split strategy 基于 canonical output 返回互斥 `complete` 或 `expand`；失败或非法返回只写 invocation audit，不写 decision 或图 mutation。
4. accepted `complete` 和 accepted `expand` 都使用 `EventLedger.append_batch()` 提交各自的权威事实；expand path 可先保存 proposal / merge plan artifact，但只有 batch 内 `*_RECORDED` event 才让 artifact 成为权威事实，无效扩图不得写出部分 child graph。

## 2. 非目标

Phase 4 不实现以下内容：

- 不实现真实 factorization、Lean 或 structured report 完整插件流程。
- 不调用生产 AI API。
- 不实现真实分布式 worker pool、HTTP runtime、P2P runtime 或链上结算。
- 不执行 Phase 5 的 merge、contribution、sandbox settlement、subtree pruning。
- 不让 executor、AI 输出、client 输入或自然语言正文临时定义协议级子任务。
- 不把 `MergePlan` 当成协议合并算法；Phase 4 只保存插件生成的合并实例契约。

## 3. 本地参考项目借鉴点

本规格只使用已落库到 `reference_repos/` 的本地参考项目，不新增联网资料。

| 参考项目 | 本地路径 | 借鉴点 | Phase 4 取舍 |
|---|---|---|---|
| Temporal Python SDK | `reference_repos/temporalio-sdk-python/` | worker、history、replay 边界分离；重放读取历史事实而不是重新执行 worker 产物。 | Phase 4 replay 只能消费 ledger event 与 artifact ref，不重新调用 verifier、executor 或 split strategy 补历史事实。 |
| Dagster | `reference_repos/dagster/python_modules/dagster/dagster/_core/` | `_core/events`、`_core/storage`、`_core/execution` 分层；事件日志条目携带结构化事件与错误摘要。 | Phase 4 event payload 只放结构化摘要和 artifact ref；验证/扩图执行逻辑不进入 storage 层。 |
| cwltool | `reference_repos/cwltool/cwltool/` | workflow / process / schema 静态检查、typed ports、循环检查。 | `DecompositionProposal` 必须用 typed input/output、schema ref、dependency edge 和无环检查描述子图；自然语言说明不能直接创建 `TaskUnit`。 |

这些参考项目只影响边界和测试组织，不成为 TokenShare runtime 依赖，也不复制其源码实现。

## 4. 模块切分

Phase 4 实现应保持小文件和层边界。

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/tokenshare/core/verification.py` | 新增 | `VerificationReport`、`CanonicalSelection`、通用候选输出检查结果、canonical selection 纯规则。 |
| `src/tokenshare/core/expansion.py` | 新增 | `SplitStrategyInvocation`、`SplitStrategyResult`、`DecompositionProposal`、`ExpansionDecision`、`MergePlan`、`ExpectedOutputRef`、proposal / merge plan / graph validation 纯规则。 |
| `src/tokenshare/core/state_machines.py` | 修改 | 放开 Phase 4 transition：`Submitted -> Verified`、`Submitted -> Rejected`、`Verified -> Canonical`、`Processing -> WaitingForChildren`、`Processing -> Completed`；verification `status=error` 不产生 `ATTEMPT_STATE_CHANGED` 自循环，只由 flow 返回原 `Submitted` attempt。 |
| `src/tokenshare/storage/events.py` | 修改 | 新增 Phase 4 event type；升级 `LedgerEvent` envelope 为兼容 v2；新增 `EventDraft` 与 `append_batch()`。 |
| `src/tokenshare/storage/sqlite_index.py` | 修改 | 增加 batch columns 和 Phase 4 index-only projection 表。 |
| `src/tokenshare/protocol_engine.py` | 修改 | 新增 verification、canonical binding、split invocation audit、complete decision、expand decision/batch 的应用服务方法。 |
| `tests/core/test_phase4_verification_models.py` | 新增 | 验证 report / canonical selection 纯模型和状态机边界。 |
| `tests/core/test_phase4_expansion_models.py` | 新增 | 验证 split result、proposal、merge plan、expected output ref 和扩图校验纯规则。 |
| `tests/storage/test_phase4_event_ledger_batch.py` | 新增 | 验证 batch envelope、连续 event_seq、hash chain、幂等和冲突。 |
| `tests/storage/test_phase4_event_projection.py` | 新增 | 验证 Phase 4 SQLite projection 与 expansion visibility。 |
| `tests/test_phase4_verification_canonical_flow.py` | 新增 | 验证从 submission 到 verification/canonical/attempt state 的端到端 flow。 |
| `tests/test_phase4_expansion_flow.py` | 新增 | 验证 split invocation、complete path、expand path 和 invalid expansion no mutation。 |
| `tests/phase4_fixtures.py` | 新增 | 提供 Phase 4 report、canonical bundle、proposal、merge plan 和 fake split strategy fixture。 |

`RootTaskRegistrar` 继续冻结为 Phase 1 legacy helper，不承载 Phase 4 编排。

## 5. Schema version 策略

Phase 4 引入以下 schema version 字符串：

| 对象 | schema_version |
|---|---|
| `LedgerEvent` 新 envelope | `LedgerEvent.v2` |
| `EventDraft` | 内存对象，不持久化 schema |
| `VerificationReport` | `phase4.verification_report.v1` |
| `CanonicalSelection` | `phase4.canonical_selection.v1` |
| `SplitStrategyInvocation` | `phase4.split_strategy_invocation.v1` |
| `SplitStrategyResult` | `phase4.split_strategy_result.v1` |
| `DecompositionProposal` | `phase4.decomposition_proposal.v1` |
| `ExpansionDecision` | `phase4.expansion_decision.v1` |
| `MergePlan` | `phase4.merge_plan.v1` |
| `ExpectedOutputRef` | `phase4.expected_output_ref.v1` |
| `VERIFICATION_RECORDED` payload | `phase4.verification_record.v1` |
| `CANONICAL_OUTPUTS_BOUND` payload | `phase4.canonical_outputs_bound.v1` |
| `SPLIT_STRATEGY_INVOCATION_RECORDED` payload | `phase4.split_strategy_invocation_record.v1` |
| `DECOMPOSITION_PROPOSAL_RECORDED` payload | `phase4.decomposition_proposal_record.v1` |
| `EXPANSION_DECISION_RECORDED` payload | `phase4.expansion_decision_record.v1` |
| `MERGE_PLAN_RECORDED` payload | `phase4.merge_plan_record.v1` |
| `TASK_EXPANDED` payload | `phase4.task_expanded.v1` |

兼容要求：

- `LedgerEvent.from_dict()` 必须能读取没有 batch fields 的历史 `LedgerEvent.v1`，并把 `batch_id`、`batch_index`、`batch_size` 视为 `None`。
- `LedgerEvent.from_dict()` 读取历史数据时必须保留原始 `schema_version`；历史 `LedgerEvent.v1` 虽然在内存属性上有 `batch_id=None`、`batch_index=None`、`batch_size=None`，但 `to_dict()` 为重算 hash 输出时不得注入这些新字段。
- `LedgerEvent.to_dict()` 必须 schema-version aware：`LedgerEvent.v1` 输出只包含 v1 envelope 字段；`LedgerEvent.v2` 输出包含 `batch_id`、`batch_index`、`batch_size`，非 batch v2 事件以 null 写入这些字段。
- `verify_hash_chain()` 必须用 schema-aware `to_dict()` 重算 hash，确保旧 JSONL hash chain 在 v2 reader 下继续通过。
- `EventLedger.append()` 写非 batch 事件时可使用 `LedgerEvent.v2`，但 batch fields 必须全部为 `None` 且进入 v2 hash。
- `EventLedger.append_batch()` 写 batch 事件时必须使用同一 `batch_id`，`batch_index` 从 1 到 `batch_size` 连续递增。

## 6. Phase 4 对象字段

### 6.1 `VerificationReport`

`VerificationReport` 是 artifact body 或 event payload 内联摘要的来源对象。第一版报告 body 可以直接进入 event payload；如果后续报告过大，再把完整 report artifact 化并在 event 中保留 ref。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.verification_report.v1`。 |
| `verification_report_id` | string | 是 | 稳定 ID，建议 `verification_report:{submission_id}:{validator_policy_id}` 或测试中显式传入。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | 被验证的 TaskUnit。 |
| `attempt_id` | string | 是 | 候选输出来源 attempt。 |
| `submission_id` | string | 是 | 候选输出来源 submission。 |
| `submission_event_seq` | integer | 是 | `EXECUTION_SUBMISSION_RECORDED` 的 ledger sequence；用于 provenance，不参与 canonical 排序。 |
| `candidate_output_bundle_digest` | string | 是 | 对 `candidate_output_refs` canonical JSON 的 digest。 |
| `candidate_output_refs` | map[string, ArtifactRef] | 是 | 候选 named output refs。 |
| `required_output_names` | list[string] | 是 | 从 output contract 得到的必需输出名。 |
| `output_contract_id` | string | 是 | Phase 3 output contract identity。 |
| `validator_policy_id` | string | 是 | 插件验证策略 ID。 |
| `plugin_id` | string | 是 | 来源插件。 |
| `plugin_version` | string | 是 | 来源插件版本。 |
| `plugin_descriptor_digest` | string | 是 | 冻结 descriptor digest。 |
| `status` | string | 是 | `passed`、`accepted`、`rejected`、`error`。第一版 canonical eligibility 只接受 `passed` / `accepted`。 |
| `eligible_for_canonical` | boolean | 是 | 派生字段，不能信任调用方输入；仅当 status 为 `passed` 或 `accepted` 且必需检查都通过时为 true。 |
| `layer_results` | object | 是 | 包含 `schema_check`、`artifact_integrity_check`、`required_output_coverage_check`、`evidence_reference_check`、`plugin_domain_check`、`audit_check`。 |
| `failure_summary` | object/null | 否 | 结构化失败摘要：`failure_kind`、`failed_layer`、`message`、`evidence_refs`。 |
| `verification_environment` | object | 是 | verifier runtime、tool versions、fixture profile digest、seed、clock policy。 |
| `verifier` | object | 是 | `verifier_id`、`verifier_version`、`verifier_kind`。 |
| `started_at` | string | 是 | UTC ISO 8601。 |
| `completed_at` | string | 是 | UTC ISO 8601；审计字段，不参与 canonical 排序。 |
| `metadata` | object | 否 | 小型结构化扩展。 |

`layer_results` 中每个 layer 使用统一子结构：

```text
status: passed | rejected | error | skipped
reason_code
summary
evidence_refs
checked_at
```

Phase 4 的 `error` 表示验证器自身异常、超时或环境错误，不能把 executor attempt 标为 `Rejected`。

`VerificationReport` 必须通过构造器、factory 或纯 helper 派生并校验 `eligible_for_canonical`，不得把调用方传入的布尔值直接落账。第一版 eligibility 规则：

- `status=error` 永远 `eligible_for_canonical=false`，且不推进 attempt 状态。
- `status=rejected` 必须 `eligible_for_canonical=false`。
- `status=passed|accepted` 只有在 `schema_check`、`artifact_integrity_check`、`required_output_coverage_check`、`evidence_reference_check`、`plugin_domain_check`、`audit_check` 全部为 `passed` 时才可 `eligible_for_canonical=true`。
- 如果缺少 required output、artifact digest 与 `ArtifactStore` 内容不匹配、required evidence ref 不存在或无法验证、plugin domain check rejected/error、audit check rejected/error，report 必须不可 canonical eligible，并记录 `failure_summary`。
- 如果调用方构造了 `eligible_for_canonical=true` 但任何 required layer 缺失、为 `rejected|error|skipped` 或 status 非 `passed|accepted`，构造/recording 必须抛 `ValueError`，不得写 `VERIFICATION_RECORDED`。
- `record_verification()` 可以接收已构造的 `VerificationReport`，但必须再次调用 report invariant 校验；更推荐实现 `build_verification_report()` 纯 helper，把 output contract、artifact store、candidate refs、evidence refs 和 plugin validator result 转成 report。

### 6.2 `CanonicalSelection`

`CanonicalSelection` 是 `CANONICAL_OUTPUTS_BOUND` 的逻辑对象。它不是投票结果，也不是多数阈值；第一版只实现 `first_verified_bundle`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.canonical_selection.v1`。 |
| `canonical_selection_id` | string | 是 | 建议 `canonical_selection:{task_id}:{unit_id}`，同一 unit 唯一。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | 被绑定正式输出的 TaskUnit。 |
| `selection_policy` | string | 是 | 第一版固定 `first_verified_bundle`。 |
| `selection_policy_version` | string | 是 | 第一版 `v1`。 |
| `selected_verification_report_id` | string | 是 | 被选 report。 |
| `selected_verification_event_seq` | integer | 是 | 排序权威。 |
| `selected_submission_id` | string | 是 | 来源 submission。 |
| `selected_submission_event_seq` | integer | 是 | provenance。 |
| `selected_attempt_id` | string | 是 | 被选 attempt。 |
| `canonical_output_bundle_digest` | string | 是 | 正式输出 bundle digest。 |
| `canonical_output_refs` | map[string, ArtifactRef] | 是 | 正式 named output refs。 |
| `eligible_report_ids_considered` | list[string] | 是 | 本次 selection 看到的 eligible reports。 |
| `selection_reason` | string | 是 | 例如 `earliest_eligible_verification_event_seq`。 |
| `bound_at` | string | 是 | UTC ISO 8601。 |
| `metadata` | object | 否 | 小型结构化扩展。 |

约束：

- 对 `(task_id, unit_id)` 只能有一个 accepted `CANONICAL_OUTPUTS_BOUND`。
- 已有 canonical selection 后，后续通过验证的 report 不得覆盖它。
- losing attempt 保持 `Verified`，不写 `Rejected`、`Failed` 或 `Superseded`。

### 6.3 `SplitStrategyInvocation`

`SplitStrategyInvocation` 记录一次插件 split strategy 调用事实。它是审计对象，不是 accepted expansion decision。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.split_strategy_invocation.v1`。 |
| `invocation_id` | string | 是 | 建议 `split_invocation:{expansion_scope_hash}:attempt:{invocation_attempt_no}`。 |
| `invocation_attempt_no` | integer | 是 | 同一 expansion scope 下从 1 开始递增。 |
| `expansion_scope_hash` | string | 是 | scope canonical JSON digest。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | parent unit。 |
| `canonical_selection_id` | string | 是 | 输入 canonical selection。 |
| `canonical_output_bundle_digest` | string | 是 | 输入 canonical bundle digest。 |
| `plugin_id` | string | 是 | 插件 ID。 |
| `plugin_version` | string | 是 | 插件版本。 |
| `plugin_descriptor_digest` | string | 是 | 冻结 descriptor digest。 |
| `split_strategy_id` | string | 是 | 必须存在于 descriptor `split_strategies`。 |
| `split_strategy_params_digest` | string | 是 | `TaskSpec.split_strategy_params` 或单位级参数 canonical JSON digest。 |
| `status` | string | 是 | `succeeded`、`failed`、`invalid_result`。 |
| `result_action` | string/null | 否 | `complete`、`expand` 或 null。 |
| `result_digest` | string/null | 否 | 成功且 result 可 canonical JSON 时填写。 |
| `error_kind` | string/null | 否 | `plugin_exception`、`timeout`、`invalid_result`、`contract_violation`、`environment_error`。 |
| `error_summary` | string/null | 否 | 短错误摘要。 |
| `started_at` | string | 是 | UTC ISO 8601。 |
| `completed_at` | string | 是 | UTC ISO 8601。 |
| `metadata` | object | 否 | 小型结构化扩展。 |

`SPLIT_STRATEGY_INVOCATION_RECORDED` 不保存完整 `SplitStrategyResult` body；完整 proposal / merge plan body 由后续 artifact 与 accepted events 固化。

### 6.4 `SplitStrategyResult`

`SplitStrategyResult` 是插件返回给协议的内存对象或 artifact 候选 body，不直接进入 ledger 成为权威事实。它有互斥 action。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.split_strategy_result.v1`。 |
| `action` | string | 是 | `complete` 或 `expand`。 |
| `expansion_scope_hash` | string | 是 | 与 invocation 输入一致。 |
| `split_strategy_identity` | object | 是 | plugin、descriptor digest、strategy id、params digest。 |
| `complete` | object/null | 条件 | action 为 `complete` 时必填。 |
| `expand` | object/null | 条件 | action 为 `expand` 时必填。 |
| `generation_evidence` | object | 是 | 插件生成摘要、策略版本、输入 digest。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

`complete` 子结构：

```text
completion_kind
validator_policy_id
verification_report_id
canonical_selection_id
canonical_output_bundle_digest
completed_output_refs
plugin_completion_summary
```

`expand` 子结构：

```text
decomposition_proposal_body
merge_plan_body
proposal_digest
merge_plan_digest
```

协议必须拒绝同时携带 complete 与 expand、action 与 body 不匹配、缺少 digest、scope 不一致或 descriptor strategy 不匹配的 result。

### 6.5 `DecompositionProposal`

`DecompositionProposal` 必须由插件版本化 split strategy 基于 canonical output 直接生成。它保存为 artifact 后，通过 `DECOMPOSITION_PROPOSAL_RECORDED` 成为 accepted expand batch 的一部分。

顶层固定六块：

```text
proposal_header
child_specs
dependency_edges
expected_outputs
merge_slots
promotion_guard_evidence
```

`proposal_header` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `proposal_id` | string | 是 | 建议 `decomposition_proposal:{expansion_scope_hash}:{proposal_digest}`。 |
| `proposal_schema_version` | string | 是 | `phase4.decomposition_proposal.v1`。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | 被展开 unit。 |
| `canonical_selection_id` | string | 是 | 输入 canonical selection。 |
| `canonical_output_bundle_digest` | string | 是 | 输入 canonical bundle digest。 |
| `plugin_id` | string | 是 | 插件 ID。 |
| `plugin_version` | string | 是 | 插件版本。 |
| `plugin_descriptor_digest` | string | 是 | 冻结 descriptor digest。 |
| `split_strategy_id` | string | 是 | descriptor 中声明的 strategy。 |
| `split_strategy_params_digest` | string | 是 | 参数 digest。 |
| `expansion_scope_hash` | string | 是 | scope digest。 |
| `proposal_digest` | string | 是 | proposal body canonical digest；计算时对 canonical proposal body 排除自引用的 `proposal_id` 与 `proposal_digest`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

`child_specs` 每项字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `child_logical_key` | string | 是 | proposal 内唯一逻辑 key。 |
| `unit_type` | string | 是 | 必须在 `SplitStrategyContract.allowed_unit_types` 内。 |
| `input_bindings` | map[string, object] | 是 | input port 到 parent output、artifact ref、constant 或 dependency output 的绑定。 |
| `required_outputs` | list[string] | 是 | child 必需 named outputs。 |
| `output_contract_refs` | map[string, object] | 是 | 每个 output 的 schema / contract ref。 |
| `validator_policy_id` | string | 是 | child 输出验证策略。 |
| `budget_limit` | number/null | 否 | 局部预算。 |
| `deadline` | string/null | 否 | 局部截止时间。 |
| `weight` | number | 是 | 贡献和调度权重，必须大于 0。 |
| `required_capabilities` | object | 是 | scheduler 可理解的能力约束。 |
| `plugin_payload` | object | 是 | 插件私有执行配置，不能保存 output resolution 权威状态。 |
| `promotion_guard_ref` | string/null | 否 | 指向 `promotion_guard_evidence` 中对应条目或 null。 |

`dependency_edges` 每项字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `edge_logical_key` | string | 是 | proposal 内唯一 edge key。 |
| `source_child_key` | string | 是 | 必须存在于 `child_specs`。 |
| `target_child_key` | string | 是 | 必须存在于 `child_specs`。 |
| `source_output_name` | string | 是 | source child 必须声明该 output。 |
| `target_input_name` | string | 是 | target child input port。 |
| `relation_type` | string | 是 | 第一版固定 `depends_on_output`。 |

`expected_outputs` 每项字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `output_name` | string | 是 | parent named output。 |
| `schema_ref` | object | 是 | 输出 schema ref。 |
| `resolution_kind` | string | 是 | `direct_parent_output`、`child_output`、`merge_plan_output`。 |
| `child_key` | string/null | 条件 | resolution 指向 child output 时必填。 |
| `child_output_name` | string/null | 条件 | resolution 指向 child output 时必填。 |
| `merge_slot_id` | string/null | 条件 | resolution 指向 merge plan output 时必填。 |
| `required` | boolean | 是 | 父输出是否必需。 |

`merge_slots` 每项字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `slot_id` | string | 是 | proposal 内唯一 slot。 |
| `child_key` | string | 是 | 必须存在于 `child_specs`。 |
| `child_output_name` | string | 是 | child 必须声明该 output。 |
| `schema_ref` | object | 是 | slot output schema。 |
| `required` | boolean | 是 | 第一版必须为 true。 |
| `missing_policy` | string | 是 | 第一版固定 `block_merge`。 |

`promotion_guard_evidence` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `typed_io_checked` | boolean | 是 | child I/O 已结构化。 |
| `independently_schedulable_checked` | boolean | 是 | child 可独立调度。 |
| `validator_policy_checked` | boolean | 是 | child 有 validator policy。 |
| `output_contract_checked` | boolean | 是 | child output contract 完整。 |
| `no_freeform_thought_checked` | boolean | 是 | 未把 thought / hidden reasoning / 临时草稿晋升为 TaskUnit。 |
| `max_depth_checked` | boolean | 是 | 深度限制已检查。 |
| `max_children_checked` | boolean | 是 | 子节点数量限制已检查。 |
| `evidence_ref` | ArtifactRef/null | 否 | 长诊断或插件证明材料。 |

协议校验必须覆盖：

- child logical key 唯一。
- edge source/target 均存在。
- `depends_on_output` relation 不重复绑定同一 target input。
- child graph 无环。
- child count 不超过 plugin strategy 和 `ProtocolConfig.max_children_per_unit`。
- parent depth + 1 不超过 `ProtocolConfig.max_depth`。
- 总 unit 数不超过 `ProtocolConfig.max_total_units`。
- `expected_outputs` 至少覆盖 parent 必需 output。
- required merge slots 都指向存在的 child output。
- promotion guard 全部通过。

### 6.6 `ExpansionDecision`

`ExpansionDecision` 是 accepted `complete` 或 `expand` 决策。它只能由协议在 split invocation 成功且校验通过后写入 ledger。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.expansion_decision.v1`。 |
| `expansion_decision_id` | string | 是 | 建议 `expansion_decision:{expansion_scope_hash}`。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | parent unit。 |
| `canonical_selection_id` | string | 是 | 输入 canonical selection。 |
| `canonical_output_bundle_digest` | string | 是 | 输入 canonical bundle digest。 |
| `expansion_scope_hash` | string | 是 | scope digest。 |
| `action` | string | 是 | `complete` 或 `expand`。 |
| `plugin_id` | string | 是 | 插件 ID。 |
| `plugin_version` | string | 是 | 插件版本。 |
| `plugin_descriptor_digest` | string | 是 | descriptor digest。 |
| `split_strategy_id` | string | 是 | strategy ID。 |
| `split_strategy_params_digest` | string | 是 | params digest。 |
| `source_invocation_id` | string | 是 | 成功 invocation。 |
| `proposal_id` | string/null | 条件 | action 为 expand 时必填。 |
| `proposal_digest` | string/null | 条件 | action 为 expand 时必填。 |
| `merge_plan_id` | string/null | 条件 | action 为 expand 时必填。 |
| `merge_plan_digest` | string/null | 条件 | action 为 expand 时必填。 |
| `action_body` | object | 是 | complete evidence 或 expand summary。 |
| `decided_at` | string | 是 | UTC ISO 8601。 |

`complete.action_body` 固定形状：

```text
completion_evidence:
  completion_kind
  validator_policy_id
  verification_report_id
  canonical_selection_id
  canonical_output_bundle_digest
  completed_output_refs
  plugin_completion_summary
```

`expand.action_body` 固定形状：

```text
expand_evidence:
  proposal_id
  proposal_digest
  merge_plan_id
  merge_plan_digest
  child_count
  relation_count
  expected_output_count
  required_merge_slot_count
```

### 6.7 `MergePlan`

`MergePlan` 是插件 `MergePolicy` 在一次 expansion 上生成的实例契约。第一版只支持 `required_slots`。

顶层固定七块：

```text
merge_plan_header
merge_policy_ref
required_slots
parent_output_mapping
hash_recording_requirements
merge_validation_requirements
plugin_payload
```

`merge_plan_header` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `merge_plan_id` | string | 是 | 建议 `merge_plan:{expansion_scope_hash}:{proposal_digest}:{merge_plan_digest}`。 |
| `merge_plan_schema_version` | string | 是 | `phase4.merge_plan.v1`。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | parent unit。 |
| `canonical_selection_id` | string | 是 | 输入 canonical selection。 |
| `decomposition_proposal_id` | string | 是 | 对应 proposal。 |
| `expansion_decision_id` | string | 是 | accepted expand decision。 |
| `created_by_plugin_id` | string | 是 | 插件 ID。 |
| `created_by_plugin_version` | string | 是 | 插件版本。 |
| `merge_plan_digest` | string | 是 | merge plan body canonical digest；计算时对 canonical merge plan body 排除自引用的 `merge_plan_id` 与 `merge_plan_digest`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

`merge_policy_ref` 字段：

```text
plugin_id
plugin_version
merge_policy_id
merge_policy_version
merge_policy_descriptor_digest
merge_policy_params_digest
```

`required_slots` 每项字段：

```text
slot_key
source_child_logical_key
source_child_unit_id
source_output_name
output_schema_ref
output_schema_digest
required: true
missing_policy: block_merge
```

`parent_output_mapping` 每项字段：

```text
parent_output_name
resolution_kind
merge_slot_keys
result_schema_ref
result_schema_digest
```

`hash_recording_requirements` 字段：

```text
record_child_canonical_output_digest
record_slot_source_artifact_digest
record_merge_input_bundle_digest
```

`merge_validation_requirements` 字段：

```text
all_required_slots_canonical
slot_schema_check_required
merged_output_schema_check_required
plugin_merge_validator_policy_id
```

`plugin_payload` 字段：

```text
plugin_defined_schema_ref
plugin_defined_body_digest
plugin_defined_body
```

Phase 4 不执行 merge，不解析自然语言合并规则，不支持 optional slots。

### 6.8 `ExpectedOutputRef`

`ExpectedOutputRef` 是协议级 output future / resolution projection 对象。第一版不新增单独 event；它从 accepted proposal 和最终 `TASK_EXPANDED` 派生。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase4.expected_output_ref.v1`。 |
| `expected_output_id` | string | 是 | 从 `source_proposal_id`、owner unit、output name 和 logical position 确定性派生。 |
| `task_id` | string | 是 | root task。 |
| `owner_unit_id` | string | 是 | 需要该 output 的 parent unit。 |
| `output_name` | string | 是 | parent named output。 |
| `schema_ref` | object | 是 | output schema。 |
| `resolution_kind` | string | 是 | `direct_parent_output`、`child_output`、`merge_plan_output`。 |
| `resolution_status` | string | 是 | Phase 4 初始 `expected`；后续可由 Phase 5 projection 改为 `blocked` 或 `resolved`。 |
| `child_unit_id` | string/null | 条件 | resolution 指向 child output 时填写。 |
| `child_output_name` | string/null | 条件 | resolution 指向 child output 时填写。 |
| `merge_plan_id` | string/null | 条件 | resolution 指向 merge plan output 时填写。 |
| `canonical_selection_id` | string | 是 | 来源 canonical selection。 |
| `canonical_output_bundle_digest` | string | 是 | 来源 canonical bundle。 |
| `source_proposal_id` | string | 是 | accepted proposal。 |
| `source_expansion_decision_id` | string | 是 | accepted expand decision。 |
| `created_event_seq` | integer | 是 | 使用 final `TASK_EXPANDED.event_seq` 作为可见边界。 |
| `resolved_event_seq` | integer/null | 否 | Phase 4 为 null。 |

## 7. Event type 与 payload

### 7.1 新增 `EventType`

Phase 4 在 `src/tokenshare/storage/events.py` 中新增：

```text
VERIFICATION_RECORDED
CANONICAL_OUTPUTS_BOUND
SPLIT_STRATEGY_INVOCATION_RECORDED
DECOMPOSITION_PROPOSAL_RECORDED
EXPANSION_DECISION_RECORDED
MERGE_PLAN_RECORDED
TASK_EXPANDED
```

### 7.2 `VERIFICATION_RECORDED`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `VerificationReport` |
| `object_id` | `verification_report_id` |
| `task_id` | report task |
| `idempotency_key` | `verification:{submission_id}:{validator_policy_id}` |

Payload 字段：

```text
schema_version
verification_report
verification_report_digest
status
eligible_for_canonical
task_id
unit_id
attempt_id
submission_id
submission_event_seq
candidate_output_bundle_digest
validator_policy_id
plugin_id
plugin_version
completed_at
```

写入后状态推进：

- `status=passed|accepted`：写 `ATTEMPT_STATE_CHANGED Submitted -> Verified`。
- `status=rejected`：写 `ATTEMPT_STATE_CHANGED Submitted -> Rejected`。
- `status=error`：不写 attempt state transition，attempt 保持 `Submitted`。

### 7.3 `CANONICAL_OUTPUTS_BOUND`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `CanonicalSelection` |
| `object_id` | `canonical_selection_id` |
| `task_id` | selection task |
| `idempotency_key` | `canonical_outputs:{task_id}:{unit_id}` |

Payload 字段：

```text
schema_version
canonical_selection
canonical_selection_digest
task_id
unit_id
selection_policy
selection_policy_version
selected_verification_report_id
selected_verification_event_seq
selected_submission_id
selected_submission_event_seq
selected_attempt_id
canonical_output_bundle_digest
canonical_output_refs
bound_at
```

写入后状态推进：

- 对 selected attempt 写 `ATTEMPT_STATE_CHANGED Verified -> Canonical`。
- 更新内存返回值中的 `TaskUnit.canonical_output_refs`，但是否写 `TASK_UNIT_STATE_CHANGED` 取决于后续 split result；Phase 4 不因 canonical binding 立即把 `TaskUnit` 置为 `Completed`。
- replay / projection 的权威来源是 `CANONICAL_OUTPUTS_BOUND` event，不是被 flow 临时返回的 mutated `TaskUnit` snapshot。
- 后续构造 `TaskGraph` 时，必须从 canonical events 或 SQLite `canonical_outputs` projection 注入 `TaskGraph.canonical_outputs_by_unit_id`；`TaskUnit.canonical_output_refs` 只能作为内存便利字段或 Phase 1/2 兼容 fallback，不能作为 replay 后依赖满足的唯一来源。

### 7.4 `SPLIT_STRATEGY_INVOCATION_RECORDED`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `SplitStrategyInvocation` |
| `object_id` | `invocation_id` |
| `task_id` | invocation task |
| `idempotency_key` | `split_invocation:{expansion_scope_hash}:attempt:{invocation_attempt_no}` |

Payload 字段：

```text
schema_version
invocation
task_id
unit_id
canonical_selection_id
canonical_output_bundle_digest
plugin_id
plugin_version
plugin_descriptor_digest
split_strategy_id
split_strategy_params_digest
status
result_action
result_digest
error_kind
error_summary
started_at
completed_at
```

`status=failed|invalid_result` 不写 decision、proposal、merge plan、TaskUnit state 或 graph mutation。

### 7.5 `DECOMPOSITION_PROPOSAL_RECORDED`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `DecompositionProposal` |
| `object_id` | `proposal_id` |
| `task_id` | proposal task |
| `idempotency_key` | `decomposition_proposal:{expansion_scope_hash}:{proposal_digest}` |
| `batch_id` | `expansion_batch:{expansion_decision_id}` |

Payload 字段：

```text
schema_version
proposal_id
task_id
parent_unit_id
canonical_selection_id
proposal_ref
proposal_digest
expansion_scope_hash
plugin_id
plugin_version
split_strategy_id
child_count
dependency_edge_count
expected_output_count
merge_slot_count
created_at
```

### 7.6 `EXPANSION_DECISION_RECORDED`

| envelope 字段 | complete 值 | expand 值 |
|---|---|---|
| `object_type` | `ExpansionDecision` | `ExpansionDecision` |
| `object_id` | `expansion_decision_id` | `expansion_decision_id` |
| `idempotency_key` | `expansion_decision:{expansion_scope_hash}` | `expansion_decision:{expansion_scope_hash}` |
| `batch_id` | `completion_batch:{expansion_decision_id}` | `expansion_batch:{expansion_decision_id}` |

Payload 字段：

```text
schema_version
expansion_decision
expansion_decision_digest
task_id
unit_id
canonical_selection_id
canonical_output_bundle_digest
expansion_scope_hash
action
source_invocation_id
proposal_id
proposal_digest
merge_plan_id
merge_plan_digest
action_body
decided_at
```

complete decision 必须与 `TASK_UNIT_STATE_CHANGED current -> Completed` 处在同一个 `completion_batch:{expansion_decision_id}` 中，且 decision 为 batch 第 1 条、state change 为 batch 第 2 条。缺少任一事件的 completion batch 是 ledger inconsistency；replay 不得只凭单条 complete decision 静默推导 completed。complete path 不写 proposal、merge plan、child graph 或 `TASK_EXPANDED`。

### 7.7 `MERGE_PLAN_RECORDED`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `MergePlan` |
| `object_id` | `merge_plan_id` |
| `idempotency_key` | `merge_plan:{expansion_scope_hash}:{proposal_digest}:{merge_plan_digest}` |
| `batch_id` | `expansion_batch:{expansion_decision_id}` |

Payload 字段：

```text
schema_version
merge_plan_id
task_id
parent_unit_id
canonical_selection_id
decomposition_proposal_id
expansion_decision_id
merge_plan_ref
merge_plan_digest
merge_policy_id
merge_policy_version
required_slot_count
parent_output_mapping_count
created_at
```

### 7.8 child `TASK_UNIT_CREATED`

Expand batch 中 child `TASK_UNIT_CREATED` 使用既有 event type，payload 仍包含 `task_unit`。新增约束：

- `idempotency_key=task_unit:create:{child_unit_id}`。
- `child_unit_id` 从 `proposal_digest`、`parent_unit_id`、`child_logical_key` 确定性派生。
- child `TaskUnit.parent_unit_id` 必须是 expanded parent。
- child `TaskUnit.depth = parent.depth + 1`。
- child 初始 state 根据 dependency 关系为 `Ready` 或 `Blocked`。
- child 初始 state 由协议算法派生，插件 payload、child spec 或自然语言说明不得直接指定状态。算法：
  - 如果 child 没有未满足的 `depends_on_output` 入边，且所有 parent/canonical artifact input bindings 已解析为现有 `ArtifactRef`、constant 或 parent canonical output ref，则初始为 `Ready`。
  - 如果存在任何 `relation_type=depends_on_output` 入边，且 source child output 尚无 canonical resolution，则初始为 `Blocked`。
  - 如果 input binding 引用缺失 parent canonical output、缺失 artifact、未知 child/port 或循环依赖，proposal validation 失败，不创建 child。
  - 同一个 target input 只能由一个 binding 或一条 dependency edge 解析；重复绑定必须拒绝。

### 7.9 child `TASK_RELATION_CREATED`

Expand batch 中 child `TASK_RELATION_CREATED` 使用既有 event type，payload 仍包含 `task_relation`。新增约束：

- `idempotency_key=task_relation:create:{relation_id}`。
- `relation_id` 从 `proposal_digest`、source/target logical key 和端口名确定性派生。
- 第一版 relation type 使用 `depends_on_output`。

### 7.10 `TASK_EXPANDED`

| envelope 字段 | 值 |
|---|---|
| `object_type` | `TaskExpansion` |
| `object_id` | `expansion_decision_id` |
| `idempotency_key` | `task_expanded:{expansion_decision_id}` |
| `batch_id` | `expansion_batch:{expansion_decision_id}` |

Payload 字段：

```text
schema_version
task_id
parent_unit_id
expansion_decision_id
canonical_selection_id
proposal_id
proposal_digest
merge_plan_id
merge_plan_digest
child_unit_ids
relation_ids
expected_output_ids
expanded_at
```

`TASK_EXPANDED` 必须是 expand batch 最后一条语义事件。SQLite projection / replay 只有看到它，才把 proposal、merge plan、expected output refs 视为可消费。

## 8. `EventLedger.append_batch()`

新增内存 helper：

```text
EventDraft:
  event_type
  object_type
  object_id
  payload
  idempotency_key
  task_id
  actor
  correlation_id
  causation_event_id
  occurred_at
```

API：

```text
EventLedger.append_batch(events: list[EventDraft], batch_id: str) -> tuple[LedgerEvent, ...]
```

实现约束：

- `events` 不能为空。
- 同一 batch 中 idempotency key 不能重复。
- 如果所有 draft 的 idempotency key 已存在，且 `batch_id`、batch order、event type、object identity、task、payload 均一致，则返回既有 events。
- 如果部分已存在或任一已存在 event 与 draft 不一致，必须抛出 `ValueError`，不得追加剩余事件。
- 新 batch 追加时预先计算 `batch_size`，每条 event 写 `batch_id`、`batch_index`、`batch_size`。
- batch 内 `event_seq` 连续，hash chain 连续。
- `verify_hash_chain()` 必须校验 v2 event 的 hash；无需把 batch 完整性放进 hash chain 校验，但可在 projection 中检查 `TASK_EXPANDED` 可见性。
- `append()` 继续作为单事件 API，写入 batch fields 为 null 的 event。
- `append_batch()` 同时用于 `completion_batch:{expansion_decision_id}` 和 `expansion_batch:{expansion_decision_id}`；complete batch 的 batch size 固定为 2，expand batch 的 batch size 至少为 6。
- `append_batch()` 必须先完成全批次 idempotency/conflict 校验和 hash 预计算，再一次性追加该 batch 的所有 JSONL 行并刷新文件句柄。V1 不声称抵抗断电级别的文件系统 torn write；如果 replay / SQLite rebuild 看到缺失成员、错误 `batch_index`、错误 `batch_size`、错误 event 顺序或缺少 complete/expand 语义终止事件，必须报告 ledger inconsistency，而不是静默把半批次投影为有效状态。

## 9. Idempotency 与 deterministic identity

| 对象 / event | idempotency key / ID |
|---|---|
| `VERIFICATION_RECORDED` | `verification:{submission_id}:{validator_policy_id}` |
| `CANONICAL_OUTPUTS_BOUND` | `canonical_outputs:{task_id}:{unit_id}` |
| `SPLIT_STRATEGY_INVOCATION_RECORDED` | `split_invocation:{expansion_scope_hash}:attempt:{invocation_attempt_no}` |
| `DECOMPOSITION_PROPOSAL_RECORDED` | `decomposition_proposal:{expansion_scope_hash}:{proposal_digest}` |
| `EXPANSION_DECISION_RECORDED` | `expansion_decision:{expansion_scope_hash}` |
| `MERGE_PLAN_RECORDED` | `merge_plan:{expansion_scope_hash}:{proposal_digest}:{merge_plan_digest}` |
| child `TASK_UNIT_CREATED` | `task_unit:create:{child_unit_id}` |
| child `TASK_RELATION_CREATED` | `task_relation:create:{relation_id}` |
| `TASK_EXPANDED` | `task_expanded:{expansion_decision_id}` |
| complete batch | `completion_batch:{expansion_decision_id}` |
| expand batch | `expansion_batch:{expansion_decision_id}` |

`expansion_scope_hash` 是以下字段 canonical JSON digest：

```text
task_id
parent_unit_id
canonical_selection_id
canonical_output_bundle_digest
plugin_id
plugin_version
plugin_descriptor_digest
split_strategy_id
split_strategy_params_digest
```

`correlation_id` 不进入 protocol fact identity，只做一次 flow tracing。

## 10. SQLite projection

### 10.1 `ledger_events` 增列

`ledger_events` 表新增：

```text
batch_id text
batch_index integer
batch_size integer
```

历史事件 rebuild 时这三列为 null。

### 10.2 `verification_reports`

```text
verification_report_id text primary key
task_id text
unit_id text
attempt_id text
submission_id text
submission_event_seq integer
verification_event_seq integer
candidate_output_bundle_digest text
status text
eligible_for_canonical integer
validator_policy_id text
plugin_id text
plugin_version text
completed_at text
payload_json text not null
```

来源：`VERIFICATION_RECORDED`。

### 10.3 `canonical_outputs`

```text
canonical_selection_id text primary key
task_id text
unit_id text
selected_verification_report_id text
selected_verification_event_seq integer
selected_submission_id text
selected_submission_event_seq integer
selected_attempt_id text
canonical_output_bundle_digest text
bound_at text
payload_json text not null
```

Projection 必须额外创建唯一索引或重建期冲突检查，确保 `(task_id, unit_id)` 唯一。SQLite 是索引，不是权威；如果 ledger 出现两个 canonical events，rebuild 应暴露冲突而不是静默覆盖。

### 10.4 `split_strategy_invocations`

```text
invocation_id text primary key
task_id text
unit_id text
canonical_selection_id text
expansion_scope_hash text
invocation_attempt_no integer
plugin_id text
plugin_version text
split_strategy_id text
status text
result_action text
result_digest text
error_kind text
completed_at text
payload_json text not null
```

来源：`SPLIT_STRATEGY_INVOCATION_RECORDED`。

### 10.5 `decomposition_proposals`

```text
proposal_id text primary key
task_id text
parent_unit_id text
canonical_selection_id text
expansion_scope_hash text
proposal_artifact_id text
proposal_digest text
plugin_id text
plugin_version text
split_strategy_id text
child_count integer
dependency_edge_count integer
expected_output_count integer
merge_slot_count integer
visible_after_task_expanded integer
payload_json text not null
```

来源：`DECOMPOSITION_PROPOSAL_RECORDED`，但 `visible_after_task_expanded` 只有同 batch 看到 final `TASK_EXPANDED` 后为 1。第一版可以在 rebuild 中先缓存 batch，看到 marker 后插入可见 row。

### 10.6 `expansion_decisions`

```text
expansion_decision_id text primary key
task_id text
unit_id text
canonical_selection_id text
expansion_scope_hash text
action text
source_invocation_id text
proposal_id text
merge_plan_id text
decided_at text
batch_id text
payload_json text not null
```

来源：`EXPANSION_DECISION_RECORDED`。同一 `expansion_scope_hash` 必须唯一。

### 10.7 `merge_plans`

```text
merge_plan_id text primary key
task_id text
parent_unit_id text
canonical_selection_id text
decomposition_proposal_id text
expansion_decision_id text
merge_plan_artifact_id text
merge_plan_digest text
merge_policy_id text
merge_policy_version text
required_slot_count integer
parent_output_mapping_count integer
visible_after_task_expanded integer
payload_json text not null
```

来源：`MERGE_PLAN_RECORDED`，可见性同 `decomposition_proposals`。

### 10.8 `expected_output_refs`

```text
expected_output_id text primary key
task_id text
owner_unit_id text
output_name text
resolution_kind text
resolution_status text
child_unit_id text
child_output_name text
merge_plan_id text
canonical_selection_id text
canonical_output_bundle_digest text
source_proposal_id text
source_expansion_decision_id text
created_event_seq integer
resolved_event_seq integer
payload_json text not null
```

来源：accepted proposal + final `TASK_EXPANDED` 派生。Phase 4 初始 `resolution_status=expected`，`resolved_event_seq=null`。

## 11. `ProtocolEngine` flow API

第一版在 `ProtocolEngine` 中新增以下方法和 result dataclass。实现时可以把纯规则放到 `core/verification.py` 和 `core/expansion.py`，`ProtocolEngine` 只负责 artifact/event 编排。

Accepted expansion decision 的共同前置校验必须由 `ProtocolEngine` 基于 ledger、artifact store 和 projection 重新加载事实完成，不能只相信传入的 `ExpansionDecision` 字段：

- `decision.source_invocation_id` 必须对应一条同 task/unit/scope 的 `SPLIT_STRATEGY_INVOCATION_RECORDED(status=succeeded)`，且 `result_action` 与 decision/action 一致。
- `decision.canonical_selection_id` 必须对应已落账 `CANONICAL_OUTPUTS_BOUND`，且 task、unit、`canonical_output_bundle_digest` 与 decision / invocation / proposal / merge plan 一致。
- `decision.plugin_descriptor_digest` 必须对应冻结 registry snapshot 或 descriptor artifact 中的同 plugin/version descriptor；`split_strategy_id` 必须存在于 descriptor `split_strategies`。
- `split_strategy_params_digest`、`expansion_scope_hash`、plugin identity、strategy id、canonical selection 和 canonical bundle digest 必须在 invocation、decision、proposal、merge plan 之间一致。
- missing invocation、failed/invalid invocation、missing canonical selection、descriptor digest mismatch、strategy id 不在 descriptor、scope mismatch 都必须在写 decision 前拒绝，且不得写 authoritative proposal/decision/merge plan/graph events。

### 11.1 `record_verification()`

```text
record_verification(
  report: VerificationReport,
  attempt: Attempt,
  correlation_id: str,
  causation_event_id: str | None = None,
) -> VerificationFlowResult
```

返回：

```text
report
event
attempt | None
attempt_event | None
```

规则：

- report 必须匹配 attempt 的 task/unit/attempt。
- attempt 必须是 `Submitted`，否则只允许显式 audit-only error path；第一版直接拒绝非法调用。
- report invariant 必须重新校验：`eligible_for_canonical` 为派生结论，status、layer results、required output coverage、artifact integrity、evidence refs、plugin domain 和 audit check 必须与第 6.1 节一致。
- `passed|accepted` 写 report event，再写 `Submitted -> Verified`。
- `rejected` 写 report event，再写 `Submitted -> Rejected`。
- `error` 写 report event，不写 attempt transition。

### 11.2 `bind_canonical_outputs()`

```text
bind_canonical_outputs(
  task_id: str,
  unit_id: str,
  verification_events: list[LedgerEvent],
  attempts_by_id: dict[str, Attempt],
  policy: str,
  now: str,
  correlation_id: str,
) -> CanonicalBindingFlowResult
```

返回：

```text
canonical_selection
event
attempt
attempt_event
```

规则：

- 第一版只接受 `policy=first_verified_bundle`。
- 从 `verification_events` 过滤同一 task/unit 且 `eligible_for_canonical=true` 的 reports。
- 按 `event_seq` 升序选择第一条。
- 如果 ledger 已有同 task/unit canonical event，幂等同 payload 返回已有 selection，不同 payload 报冲突。
- selected attempt 必须当前为 `Verified`，然后写 `Verified -> Canonical`。

### 11.3 `record_split_strategy_invocation()`

```text
record_split_strategy_invocation(
  invocation: SplitStrategyInvocation,
  correlation_id: str,
  causation_event_id: str | None = None,
) -> SplitStrategyInvocationFlowResult
```

只写 `SPLIT_STRATEGY_INVOCATION_RECORDED`。不保存 result body，不写 decision，不改 graph/state。

### 11.4 `record_complete_decision()`

```text
record_complete_decision(
  decision: ExpansionDecision,
  task_unit: TaskUnit,
  correlation_id: str,
  causation_event_id: str | None = None,
) -> CompleteDecisionFlowResult
```

规则：

- `decision.action` 必须是 `complete`。
- 必须通过第 11 节共同前置校验，引用成功 invocation、已绑定 canonical selection 和合法 descriptor strategy。
- 使用 `append_batch()` 写 `completion_batch:{expansion_decision_id}`。
- batch 第 1 条写 `EXPANSION_DECISION_RECORDED(action=complete)`，第 2 条写 `TASK_UNIT_STATE_CHANGED {current} -> Completed`。
- 不写 proposal、merge plan、child graph 或 `TASK_EXPANDED`。

### 11.5 `record_expand_decision()`

```text
record_expand_decision(
  decision: ExpansionDecision,
  proposal: DecompositionProposal,
  merge_plan: MergePlan,
  parent_unit: TaskUnit,
  graph: TaskGraph,
  correlation_id: str,
  causation_event_id: str | None = None,
) -> ExpandDecisionFlowResult
```

返回：

```text
decision
proposal_ref
merge_plan_ref
child_units
relations
expected_output_refs
events
```

规则：

- 保存 proposal artifact 和 merge plan artifact。
- 在内存中完成 proposal / merge plan / graph validation。
- 保存到 artifact store 但尚未进入 accepted batch 的 proposal / merge plan 只是 staged artifact，不是权威协议事实；只有 `DECOMPOSITION_PROPOSAL_RECORDED` / `MERGE_PLAN_RECORDED` 进入 accepted batch 后才可被 replay / projection 消费。
- 任一检查失败时，不写 proposal event、decision event、merge plan event、child events 或 `TASK_EXPANDED`；测试只断言没有 authoritative events / graph mutation，不要求磁盘上没有 staged artifact 文件。staged artifact 清理属于 storage hygiene，不是 Phase 4 协议不变量。
- 必须通过第 11 节共同前置校验，且 proposal、merge plan 与 decision 的 source invocation、canonical selection、descriptor digest、strategy id 和 scope 一致。
- 检查通过后构造 batch drafts，并调用 `append_batch()`。
- batch 顺序固定为 proposal、decision、merge plan、child units、child relations、`TASK_EXPANDED`。
- parent state 第一版保持 `Processing` 或由 `TASK_EXPANDED` 后的 projection 表达等待子节点；若实现选择写 `TASK_UNIT_STATE_CHANGED Processing -> WaitingForChildren`，必须作为单独已设计扩展事件进入 batch 顺序审查。本规格第一版不要求该额外事件，避免打乱冻结 batch。

## 12. 状态机变更

### 12.1 `Attempt`

新增合法 transition：

```text
Submitted -> Verified
Submitted -> Rejected
Verified -> Canonical
```

保持禁止：

```text
Submitted -> Canonical
Rejected -> Canonical
Verified -> Rejected
Canonical -> Rejected
```

`transition_attempt()` 需要在不同 target state 上写入：

- `Verified`：`finished_at=changed_at` 不设置 failure。
- `Rejected`：`finished_at=changed_at`，`failure_kind=invalid_output` 或 report failure kind。
- `Canonical`：保留 `finished_at`，metadata 可追加 `canonical_selection_id`、`canonical_output_bundle_digest`。

### 12.2 `TaskUnit`

新增合法 transition：

```text
Processing -> Completed
Processing -> WaitingForChildren
WaitingForChildren -> MergeReady
MergeReady -> Merging
Merging -> Completed
```

Phase 4 只需要 `Processing -> Completed`。`Processing -> WaitingForChildren` 可作为 expand 后显式状态推进，但第一版可先让 `TASK_EXPANDED` 作为等待子节点事实；如果实现写该 transition，必须有测试证明 event order 和 projection 不冲突。

## 13. TDD 计划

所有命令在 PowerShell 下运行，先设置：

```powershell
$env:PYTHONPATH='src'
```

### Task 1: Ledger batch envelope

**文件：**

- 修改：`src/tokenshare/storage/events.py`
- 测试：`tests/storage/test_phase4_event_ledger_batch.py`

**红灯测试：**

1. `test_append_batch_writes_contiguous_sequence_hash_chain_and_batch_fields`
2. `test_append_batch_returns_existing_events_for_identical_retry`
3. `test_append_batch_rejects_partial_or_conflicting_retry_without_appending`
4. `test_ledger_reads_legacy_events_without_batch_fields_as_null`
5. `test_existing_v1_hash_chain_still_verifies_after_v2_reader`
6. `test_completion_batch_records_decision_and_completed_state_atomically`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\storage\test_phase4_event_ledger_batch.py -q
```

**预期红灯原因：** `EventDraft` 和 `EventLedger.append_batch()` 不存在，`LedgerEvent` 没有 batch fields。

**绿灯要求：**

- batch event_seq 连续。
- `LedgerEvent.v2` batch fields 进入 `to_dict()` 和 hash；`LedgerEvent.v1` schema-aware `to_dict()` 不注入 batch null 字段。
- `verify_hash_chain()` 通过。
- 冲突 retry 不追加任何 event。
- `append_batch()` 对 expand / complete 两种 batch 都可用，且 existing identical retry 返回完整既有 batch。

### Task 2: Phase 4 object models and pure validation

**文件：**

- 新增：`src/tokenshare/core/verification.py`
- 新增：`src/tokenshare/core/expansion.py`
- 新增：`tests/phase4_fixtures.py`
- 测试：`tests/core/test_phase4_verification_models.py`
- 测试：`tests/core/test_phase4_expansion_models.py`

**红灯测试：**

1. `test_verification_report_digest_and_canonical_eligibility`
2. `test_verification_report_missing_required_output_is_not_eligible`
3. `test_verification_report_artifact_digest_mismatch_is_not_eligible`
4. `test_verification_report_missing_evidence_ref_is_not_eligible`
5. `test_verification_report_plugin_domain_rejected_is_not_eligible`
6. `test_verification_report_error_status_is_never_eligible`
7. `test_verification_report_rejects_eligible_true_when_required_layers_not_all_passed`
8. `test_first_verified_bundle_selects_lowest_verification_event_seq`
9. `test_split_strategy_result_requires_exactly_one_action_body`
10. `test_decomposition_proposal_rejects_freeform_child_and_duplicate_target_input`
11. `test_child_initial_state_is_derived_from_dependency_and_input_bindings_not_plugin_payload`
12. `test_merge_plan_requires_only_required_slots`
13. `test_expected_output_ref_is_derived_from_task_expanded_visibility`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\core\test_phase4_verification_models.py tests\core\test_phase4_expansion_models.py -q
```

**预期红灯原因：** Phase 4 dataclasses 和 validation helpers 不存在。

**绿灯要求：**

- 所有 dataclass `to_dict()` 输出稳定 schema version。
- digest helper 使用 canonical JSON。
- proposal validation 可在写事件前失败。
- `eligible_for_canonical` 只能由 validated layer results 派生，不能由调用方绕过。

### Task 3: State machine transitions

**文件：**

- 修改：`src/tokenshare/core/state_machines.py`
- 测试：`tests/core/test_state_machines.py`

**新增测试：**

1. `test_phase4_attempt_verification_and_canonical_transitions`
2. `test_phase4_attempt_rejects_direct_submitted_to_canonical`
3. `test_phase4_task_unit_can_complete_from_processing`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\core\test_state_machines.py -q
```

**预期红灯原因：** 当前 transition table 不允许 Phase 4 states。

**绿灯要求：**

- `Submitted -> Verified -> Canonical` 通过。
- `Submitted -> Rejected` 写 invalid output failure。
- 非法跳转继续抛 `ValueError`。

### Task 4: Verification flow

**文件：**

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase4_verification_canonical_flow.py`

**红灯测试：**

1. `test_record_passed_verification_writes_report_event_and_advances_attempt_to_verified`
2. `test_record_rejected_verification_advances_attempt_to_rejected`
3. `test_verification_error_records_event_without_attempt_state_change`
4. `test_record_verification_rejects_report_marked_eligible_when_required_layer_failed`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\test_phase4_verification_canonical_flow.py -q
```

**预期红灯原因：** `ProtocolEngine.record_verification()` 不存在。

**绿灯要求：**

- Event payload 不内联 long raw output。
- Passed / accepted report 的 `event_seq` 后续可被 canonical selection 使用。
- Error report 不把 executor attempt 错标为 failed 或 rejected。
- `record_verification()` 不能只落账 caller-provided report，必须执行 report invariant 校验。

### Task 5: Canonical binding flow

**文件：**

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase4_verification_canonical_flow.py`

**红灯测试：**

1. `test_bind_canonical_outputs_selects_earliest_eligible_verification_event_seq`
2. `test_bind_canonical_outputs_is_unique_per_task_unit`
3. `test_losing_verified_attempt_remains_verified`
4. `test_late_verified_bundle_does_not_replace_existing_canonical`
5. `test_task_graph_replay_uses_canonical_outputs_projection_not_mutated_task_unit_snapshot`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\test_phase4_verification_canonical_flow.py -q
```

**预期红灯原因：** `ProtocolEngine.bind_canonical_outputs()` 不存在。

**绿灯要求：**

- Selection 使用 verification event_seq，不使用 submitted_at 或 completed_at。
- 同 `(task_id, unit_id)` duplicate binding 冲突。
- selected attempt 进入 `Canonical`。
- 重建 `TaskGraph` 时从 canonical event / SQLite projection 注入 `canonical_outputs_by_unit_id`。

### Task 6: Split invocation audit

**文件：**

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase4_expansion_flow.py`

**红灯测试：**

1. `test_failed_split_strategy_invocation_is_audit_only`
2. `test_invalid_split_strategy_result_is_audit_only`
3. `test_succeeded_invocation_records_only_digest_and_action_not_full_result_body`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\test_phase4_expansion_flow.py -q
```

**预期红灯原因：** `SPLIT_STRATEGY_INVOCATION_RECORDED` flow 不存在。

**绿灯要求：**

- failed / invalid_result 只写 invocation event。
- 没有 `EXPANSION_DECISION_RECORDED`。
- 没有 proposal / merge plan artifact authoritative event。
- 没有 child graph event。

### Task 7: Complete path

**文件：**

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase4_expansion_flow.py`

**红灯测试：**

1. `test_complete_decision_records_decision_then_completes_task_unit_in_completion_batch`
2. `test_complete_decision_does_not_write_proposal_merge_plan_or_task_expanded`
3. `test_complete_decision_rejects_missing_or_failed_invocation`
4. `test_complete_decision_rejects_scope_or_descriptor_strategy_mismatch`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\test_phase4_expansion_flow.py -q
```

**预期红灯原因：** `ProtocolEngine.record_complete_decision()` 不存在，TaskUnit transition 未被 flow 使用。

**绿灯要求：**

- Event order 为 invocation、completion batch decision、completion batch task unit completed。
- `EXPANSION_DECISION_RECORDED.action=complete` payload 包含 inline `completion_evidence`。
- 不产生 expand batch、proposal、merge plan、child graph 或 `TASK_EXPANDED`。
- completion batch retry 必须全批幂等；半批次或 batch 内容不一致必须冲突。

### Task 8: Expand path atomic batch

**文件：**

- 修改：`src/tokenshare/protocol_engine.py`
- 修改：`src/tokenshare/core/expansion.py`
- 测试：`tests/test_phase4_expansion_flow.py`

**红灯测试：**

1. `test_expand_records_proposal_decision_merge_plan_child_graph_and_task_expanded_in_one_batch`
2. `test_invalid_expansion_does_not_mutate_graph_or_persist_authoritative_events`
3. `test_duplicate_expansion_same_batch_is_idempotent`
4. `test_duplicate_expansion_different_payload_conflicts_without_new_child_events`
5. `test_expand_rejects_missing_or_failed_invocation`
6. `test_expand_rejects_missing_canonical_selection_or_scope_mismatch`
7. `test_expand_rejects_strategy_id_not_declared_by_descriptor`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\test_phase4_expansion_flow.py -q
```

**预期红灯原因：** expand coordinator flow 和 `append_batch()` integration 不存在。

**绿灯要求：**

- Batch 顺序完全匹配本文第 1 节。
- `TASK_EXPANDED` 是 batch 最后一条。
- Invalid proposal 不保存 accepted proposal event，不写 child graph；允许存在未被 event 引用的 staged artifact 文件。
- child unit / relation IDs deterministic。
- source invocation、canonical selection、descriptor digest、strategy id 和 expansion scope 都必须从 ledger / artifact facts 校验一致。

### Task 9: SQLite Phase 4 projection

**文件：**

- 修改：`src/tokenshare/storage/sqlite_index.py`
- 测试：`tests/storage/test_phase4_event_projection.py`

**红灯测试：**

1. `test_sqlite_index_rebuilds_verification_and_canonical_outputs`
2. `test_sqlite_index_rebuilds_split_invocation_audit`
3. `test_sqlite_index_exposes_expansion_rows_only_after_task_expanded`
4. `test_sqlite_index_rejects_duplicate_canonical_outputs_for_same_unit`
5. `test_sqlite_index_rejects_incomplete_completion_batch`
6. `test_sqlite_index_rejects_incomplete_expansion_batch`

**命令：**

```powershell
conda run -n tokenshare python -m pytest tests\storage\test_phase4_event_projection.py -q
```

**预期红灯原因：** Phase 4 tables 和 projection handlers 不存在。

**绿灯要求：**

- `ledger_events` 包含 batch columns。
- Phase 4 tables 可从 JSONL events 重建。
- 缺 final `TASK_EXPANDED` 的 proposal / merge plan 不作为可消费 expansion row 暴露。
- 缺 `TASK_UNIT_STATE_CHANGED -> Completed` 的 `completion_batch` 必须报 ledger inconsistency，不能只凭 complete decision 推导完成。

### Task 10: Integration verification and code map

**文件：**

- 新增：`Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`
- 修改：`Doc/agent-navigation.md`
- 修改：`feature_list.json`
- 修改：`progress.md`
- 修改：`session-handoff.md`

**验证命令：**

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
conda run -n tokenshare python -m compileall -x "reference_repos" .
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests
.\init.ps1
```

**完成要求：**

- `Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md` 映射 Phase 4 spec、源码和测试。
- `feature_list.json` 的 feat-005 evidence 写入红绿验证证据。
- `progress.md` 和 `session-handoff.md` 记录 Phase 4 已实现范围、验证输出和剩余 Phase 5 边界。

## 14. 实现顺序建议

建议按以下顺序执行，避免被跨层问题卡住：

1. `append_batch()` 先行，因为 expand atomicity 依赖它。
2. Phase 4 pure dataclasses / validation helper 第二步完成，后续 flow 测试都复用这些对象。
3. 状态机 transition 第三步完成，verification / canonical flow 才能写 attempt events。
4. Verification 与 canonical binding 先闭环，再进入 expansion；expansion 不得消费单独 verification report。
5. Split invocation audit 先实现 failed / invalid_result，再实现 complete 和 expand accepted path。
6. SQLite projection 最后实现，因为它应从最终 event contract 重建索引。

## 15. 自审清单

本文已固定以下实现前必须明确的工程规格：

- Phase 4 对象字段和 schema version。
- Phase 4 event type、event payload 和 idempotency key。
- `LedgerEvent.v2` batch envelope 兼容策略。
- `EventLedger.append_batch()` 行为。
- SQLite 新表和列。
- `ProtocolEngine` 第一版 API。
- Attempt / TaskUnit 状态机变更。
- expand batch 顺序和 invalid expansion no mutation。
- TDD 测试文件、测试名、命令和红绿预期。

本文没有引入新的外部资料；使用的外部项目均已在 `reference_repos/README.md` 登记。
