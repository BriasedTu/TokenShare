# Phase 5 字段规格与 TDD 计划

## 元数据

| 项 | 内容 |
|---|---|
| 日期 | 2026-06-25 |
| 状态 | Field spec / TDD implementation-ready |
| 对应 feature | `feat-006` - Phase 5 - Merge, Contribution, and Sandbox Settlement |
| 上游依据 | `Doc/TechnicalDocument/2026-06-25-phase-5-merge-discussion-notes.md`、`Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md`、`Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md`、`Doc/TechnicalDocument/2026-06-24-phase-4-code-map.md`、`Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` |
| 目的 | 把 Phase 5 已确认的 merge、expected output resolution、contribution、sandbox settlement 和 post-completion pruning 设计收束成可直接指导后续 TDD 的对象字段、event payload、batch 边界、SQLite projection、replay inconsistency 规则和任务顺序。 |

本文是 Phase 5 开工规格。后续实现应以本文为 `feat-006` 的字段和测试依据；Phase 5 discussion notes 是决策 provenance，external systems notes 是外部系统经验备忘。若讨论记录中的字段细节与本文冲突，以本文为实现口径。

## 1. 本阶段目标

Phase 5 把 Phase 4 生成的 accepted `MergePlan`、child canonical outputs 和 `ExpectedOutputRef` 推进成父节点可完成、贡献可结算、replay 可审计的协议闭环：

```text
accepted MergePlan + TASK_EXPANDED visible
+ all required child canonical outputs
-> append_batch(merge_task_creation_batch:{merge_plan_id})
     1. TASK_UNIT_CREATED for merge TaskUnit
     2. optional TASK_RELATION_CREATED(kind=merge_of)
     3. MERGE_TASK_LINK_RECORDED
-> merge TaskUnit normal lifecycle:
     ExecutionRequest -> ExecutionSubmission -> VERIFICATION_RECORDED -> CANONICAL_OUTPUTS_BOUND
-> append_batch(merge_resolution_batch:{merge_record_id})
     1. MERGE_RECORDED
     2..n. EXPECTED_OUTPUT_RESOLVED
-> append_batch(parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest})
     1. TASK_UNIT_STATE_CHANGED Processing -> Completed
     2..n. CONTRIBUTION_STATE_CHANGED expand_canonical Pending -> Eligible
-> append_batch(settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq})
     1..n. CONTRIBUTION_STATE_CHANGED Eligible -> Settled
     final. SETTLEMENT_RECORDED
-> optional append_batch(subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq})
     1..n. TASK_UNIT_STATE_CHANGED Ready/Processing/Blocked -> Cancelled
     final. SUBTREE_PRUNED
```

Phase 5 必须实现四个可验证结果：

1. 所有 `MergePlan.required_slots` 都绑定到 child canonical outputs 后，才能创建普通 merge `TaskUnit`；创建事实必须带稳定 `merge_input_bundle`、slot bindings 和 `MergeTaskLink`。
2. merge `TaskUnit` 必须走普通 request / submission / verification / canonical 生命周期；`MERGE_RECORDED` 只能在 merge unit 已有 `CANONICAL_OUTPUTS_BOUND` 后写入。
3. `merge_plan_output` 的 `ExpectedOutputRef` resolution 必须和 `MERGE_RECORDED` 处于同一个 `merge_resolution_batch`；projection / replay 不暴露半批次。
4. contribution 和 settlement 只基于 canonical / accepted / root completed 权威事实计数，避免 retry、late submission、shadow execution 或 canonical loser 重复奖励。

## 2. 非目标

Phase 5 第一版不实现以下内容：

- 不实现真实链上支付、钱包、智能合约、真实代币余额或生产激励系统。
- 不实现真实分布式 worker pool、HTTP worker runtime、P2P runtime 或生产级权限系统。
- 不实现 `optional_slots`、late optional output、re-merge、partial merge、combiner、tree aggregation、`one_success` trigger、early terminal resolution 或 factorization early pruning。
- 不实现 redundant verification contribution，也不按所有 attempt 计奖。
- 不把 merge 算法写入协议核心；领域合并算法仍属于插件 `MergePolicy`，协议只编排 readiness、input bundle、slot/hash 审计、事件和 replay。
- 不回改 Phase 4 `expansion_batch`，也不在 expand 时预创建 blocked merge task。
- 不把 `TaskUnit.metadata`、`plugin_payload` 或自然语言正文作为 merge readiness、expected output resolution、contribution 或 settlement 的权威状态。

## 3. 本地参考项目借鉴点

本规格不新增联网资料。Phase 5 已使用的联网资料已经落库到 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md`，并记录来源 URL、访问日期、本地摘要和影响范围。

| 来源 | 借鉴点 | Phase 5 取舍 |
|---|---|---|
| Temporal event history / child workflow | durable history 和 replay 不重新执行 activity / workflow；child workflow 会增加事件数量，需有明确 ownership / lifecycle 理由。 | merge 作为普通 `TaskUnit`，接受更多事件，换取统一 retry、verification、canonical 和 replay 语义。 |
| Celery chord | header group 全部完成后触发 body callback。 | `MergePlan.required_slots` 类似 header；merge task 类似 body，但 TokenShare 必须绑定 canonical output hash，而不是只看完成状态。 |
| Dask futures / graphs | future 可作为下游 task 输入，高层 graph 是查询/优化视图。 | `ExpectedOutputRef` 是协议级 output future；resolution 由 event 重建，不藏在 metadata。 |
| Airflow trigger rules / XCom | 默认 all upstream success；数据传递和依赖状态分离。 | Phase 5 第一版只做 all-required canonical，输出用 artifact refs + bundle digest。 |
| MapReduce | backup tasks / re-execution 不能造成 counters double counting。 | contribution / settlement 只按 canonical / accepted identity 计，不按 attempt 计。 |
| Spark reduceByKey | combiner / tree reduce 依赖 associativity / commutativity。 | 第一版不做 partial merge 或 tree aggregation；插件声明更强 merge algebra 后再扩展。 |

## 4. 模块切分

Phase 5 后续实现应保持协议核心、插件、执行器和存储边界。

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/tokenshare/core/merge.py` | 新增 | `MergeTaskLink`、`RequiredSlotBinding`、`MergeRecord`、`ExpectedOutputResolution`、merge readiness 纯校验、merge input bundle digest helper。 |
| `src/tokenshare/core/contribution.py` | 新增 | `ContributionRecord`、`SettlementRecord`、`SettlementEntry`、`SubtreePruneRecord`、contribution state transition 纯规则、sandbox reward formula helper。 |
| `src/tokenshare/storage/events.py` | 修改 | 新增 Phase 5 event type constants。 |
| `src/tokenshare/storage/sqlite_index.py` | 修改 | 增加 Phase 5 index-only projection 表和 batch inconsistency 检查；更新 `expected_output_refs` 的 resolution projection。 |
| `src/tokenshare/protocol_engine.py` | 修改 | 增加 merge task creation、merge resolution、parent completion、subtree pruning 的应用服务方法；继续不执行插件 merge 算法。 |
| `src/tokenshare/experiments` | 不修改 | Phase 5 规格阶段不做实验 runner。 |
| `tests/core/test_phase5_models.py` | 新增 | 纯对象、schema version、digest、state transition、reward formula。 |
| `tests/test_phase5_merge_task_creation_flow.py` | 新增 | merge readiness、merge input bundle、creation batch、duplicate/conflict。 |
| `tests/test_phase5_merge_resolution_flow.py` | 新增 | `MERGE_RECORDED`、`EXPECTED_OUTPUT_RESOLVED`、batch 完整性、parent completion gate。 |
| `tests/test_phase5_contribution_settlement_flow.py` | 新增 | contribution 创建/推进、root settlement batch、no double settlement。 |
| `tests/test_phase5_subtree_pruning_flow.py` | 新增 | post-completion cancellation batch 和 `SUBTREE_PRUNED`。 |
| `tests/storage/test_phase5_event_projection.py` | 新增 | SQLite projection、半批次 inconsistency、expected output resolution 和 settlement 查询表。 |
| `tests/phase5_fixtures.py` | 新增 | Phase 4 completed expansion、child canonical outputs、merge canonical output、contribution / settlement fixtures。 |

`RootTaskRegistrar` 继续冻结为 Phase 1 legacy helper；Phase 5 编排入口放在 `ProtocolEngine` 或新的 coordinator 类中，不把 graph / scheduling / settlement 编排加回 registration 层。

## 5. Schema version 策略

| 对象或 payload | schema_version |
|---|---|
| `MergeTaskLink` | `phase5.merge_task_link.v1` |
| `RequiredSlotBinding` | `phase5.required_slot_binding.v1` |
| `MergeInputBundle` artifact | `phase5.merge_input_bundle.v1` |
| `MergeRecord` | `phase5.merge_record.v1` |
| `ExpectedOutputResolution` | `phase5.expected_output_resolution.v1` |
| `ContributionRecord` | `phase5.contribution_record.v1` |
| `SettlementRecord` | `phase5.settlement_record.v1` |
| `SettlementEntry` | `phase5.settlement_entry.v1` |
| `SubtreePruneRecord` | `phase5.subtree_prune_record.v1` |
| `MERGE_TASK_LINK_RECORDED` payload | `phase5.merge_task_link_record.v1` |
| `MERGE_RECORDED` payload | `phase5.merge_recorded.v1` |
| `EXPECTED_OUTPUT_RESOLVED` payload | `phase5.expected_output_resolved.v1` |
| `CONTRIBUTION_STATE_CHANGED` payload | `phase5.contribution_state_changed.v1` |
| `SETTLEMENT_RECORDED` payload | `phase5.settlement_recorded.v1` |
| `SUBTREE_PRUNED` payload | `phase5.subtree_pruned.v1` |
| merge `TASK_UNIT_CREATED` payload extension | `phase5.merge_task_unit_created.v1` |
| merge `TASK_RELATION_CREATED` payload extension | `phase5.merge_task_relation_created.v1` |

兼容要求：

- 继续使用 Phase 4 `LedgerEvent.v2` batch envelope，不新增 `LedgerEvent.v3`。
- Phase 5 不改变 Phase 4 `completion_batch` 和 `expansion_batch` 顺序。
- `resolved_event_seq`、`merge_record_event_seq`、`settlement_event_seq` 这类字段在 projection 中由 `LedgerEvent.event_seq` 派生；event payload 如果携带同名字段，rebuild 时必须校验它与 envelope 一致。实现第一版可以不把这些自引用 sequence 写入 payload。
- `correlation_id` 只用于 flow tracing，不能作为协议事实 id 或幂等 identity。

## 6. Phase 5 对象字段

### 6.1 `RequiredSlotBinding`

`RequiredSlotBinding` 是 merge readiness 的 slot-level 输入绑定事实。它来自 accepted `MergePlan.required_slots` 和 child `CANONICAL_OUTPUTS_BOUND`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.required_slot_binding.v1`。 |
| `slot_key` | string | 是 | 来自 `MergePlan.required_slots[].slot_key`。 |
| `slot_id` | string | 否 | 来自 Phase 4 proposal / merge slots；如果存在必须稳定。 |
| `source_child_logical_key` | string | 是 | 来自 required slot。 |
| `source_child_unit_id` | string | 是 | accepted expansion 派生的 child unit id。 |
| `source_output_name` | string | 是 | required child output name。 |
| `source_output_schema_digest` | string | 是 | 来自 required slot schema digest。 |
| `canonical_selection_id` | string | 是 | child unit canonical selection。 |
| `canonical_event_seq` | integer | 是 | child `CANONICAL_OUTPUTS_BOUND.event_seq`。 |
| `canonical_output_ref` | object | 是 | 对应 named output `ArtifactRef.to_dict()`。 |
| `canonical_output_digest` | string | 是 | 对应 artifact content hash 或 named output digest。 |
| `canonical_output_bundle_digest` | string | 是 | child canonical output bundle digest。 |
| `selected_verification_report_id` | string | 是 | canonical selection 选中的 verification report。 |
| `selected_attempt_id` | string | 是 | canonical selection 选中的 attempt。 |
| `binding_source` | string | 是 | 第一版固定 `canonical_output`。 |

约束：

- 所有 required slots 都必须有一条 binding，且 `slot_key` 不得重复。
- binding 必须使用 child canonical output，不能使用 submission、verification-only output、losing canonical candidate、late submission 或 executor 私有输出。
- `canonical_event_seq` 是 readiness 排序和 audit 字段，不参与修改 canonical selection。

### 6.2 `MergeInputBundle` artifact

`MergeInputBundle` 是 merge `TaskUnit` 的稳定输入 artifact。它可以在 batch 前 staged 保存，但只有被 `MERGE_TASK_LINK_RECORDED` 引用后才成为协议事实。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.merge_input_bundle.v1`。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | 被 merge 回来的 parent unit。 |
| `merge_plan_id` | string | 是 | accepted MergePlan。 |
| `expansion_decision_id` | string | 是 | 生成该 MergePlan 的 accepted expand decision。 |
| `merge_policy_ref` | object | 是 | 来自 `MergePlan.merge_policy_ref`，含 plugin / policy / params digest。 |
| `parent_output_mapping` | list[object] | 是 | 来自 `MergePlan.parent_output_mapping`。 |
| `required_slot_bindings` | list[RequiredSlotBinding] | 是 | 按 `slot_key` 升序稳定排序。 |
| `required_slot_bindings_digest` | string | 是 | 对排序后的 bindings canonical JSON 求 digest。 |
| `created_at` | string | 是 | UTC ISO 8601。 |
| `created_by` | object | 是 | `coordinator_id`、`coordinator_version`。 |

约束：

- `merge_input_bundle_digest` 必须等于 artifact content hash。
- bundle 内不放 raw model output、hidden reasoning 或 executor 私有内存。
- bundle 只描述输入和 policy identity，不执行领域 merge。

### 6.3 `MergeTaskLink`

`MergeTaskLink` 连接 parent unit、accepted MergePlan、merge TaskUnit、merge input bundle 和 required slot bindings。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.merge_task_link.v1`。 |
| `merge_task_link_id` | string | 是 | 建议 `merge_task_link:{merge_plan_id}`，同一 MergePlan 唯一。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | parent unit。 |
| `merge_plan_id` | string | 是 | accepted MergePlan。 |
| `expansion_decision_id` | string | 是 | accepted expand decision。 |
| `merge_unit_id` | string | 是 | 新建 merge TaskUnit id，建议从 `merge_plan_id` deterministic 派生。 |
| `merge_input_bundle_ref` | object | 是 | `ArtifactRef.to_dict()`。 |
| `merge_input_bundle_digest` | string | 是 | bundle content hash。 |
| `required_slot_bindings` | list[RequiredSlotBinding] | 是 | 完整 slot binding 摘要。 |
| `required_slot_bindings_digest` | string | 是 | bindings digest。 |
| `merge_policy_id` | string | 是 | 来自 MergePlan。 |
| `merge_policy_version` | string | 是 | 来自 MergePlan。 |
| `merge_policy_descriptor_digest` | string | 是 | 来自 MergePlan。 |
| `source_merge_plan_event_seq` | integer | 是 | `MERGE_PLAN_RECORDED.event_seq`。 |
| `source_task_expanded_event_seq` | integer | 是 | `TASK_EXPANDED.event_seq`。 |
| `optional_task_relation_id` | string/null | 否 | 若写 `TaskRelation(kind=merge_of)`，记录 relation id。 |
| `readiness_reason` | string | 是 | 第一版固定 `all_required_slots_canonical`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |
| `coordinator` | object | 是 | `coordinator_id`、`coordinator_version`。 |

约束：

- 同一 `merge_plan_id` 只能有一个有效 `MergeTaskLink`。
- 不把 slot coverage、canonical output hash 或 bundle digest 只放进 `TaskRelation` 或 metadata；`MergeTaskLink` 是 projection / replay 查询权威。

### 6.4 `MergeRecord`

`MergeRecord` 是 canonical-level merge commitment，只能在 merge `TaskUnit` 已绑定 canonical output 后写入。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.merge_record.v1`。 |
| `merge_record_id` | string | 是 | 建议 `merge_record:{merge_plan_id}:{merge_unit_id}:{canonical_selection_id}`。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | parent unit。 |
| `merge_plan_id` | string | 是 | accepted MergePlan。 |
| `merge_unit_id` | string | 是 | merge TaskUnit。 |
| `merge_task_link_id` | string | 是 | 来源 `MergeTaskLink`。 |
| `merge_input_bundle_ref` | object | 是 | 来源 bundle ref。 |
| `merge_input_bundle_digest` | string | 是 | 来源 bundle digest。 |
| `required_slot_bindings_digest` | string | 是 | 来源 bindings digest。 |
| `merge_policy_id` | string | 是 | merge policy。 |
| `merge_policy_version` | string | 是 | merge policy version。 |
| `merge_policy_descriptor_digest` | string | 是 | policy descriptor digest。 |
| `merge_policy_params_digest` | string | 是 | policy params digest。 |
| `canonical_selection_id` | string | 是 | merge unit canonical selection。 |
| `canonical_event_seq` | integer | 是 | merge unit `CANONICAL_OUTPUTS_BOUND.event_seq`。 |
| `selected_verification_report_id` | string | 是 | canonical selection 选中的 verification report。 |
| `selected_verification_event_seq` | integer | 是 | selected verification event seq。 |
| `selected_submission_id` | string | 是 | selected submission。 |
| `selected_submission_event_seq` | integer | 是 | selected submission event seq。 |
| `selected_attempt_id` | string | 是 | selected attempt。 |
| `merge_output_bundle_digest` | string | 是 | canonical merge output bundle digest。 |
| `merge_output_refs` | map[string, ArtifactRef] | 是 | canonical merge output refs。 |
| `parent_output_mapping_digest` | string | 是 | parent output mapping digest。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

约束：

- losing merge attempts、verification rejected/error attempts、late submissions 和 canonical losers 不写 `MERGE_RECORDED`。
- 对同一 `merge_plan_id` 只能有一个有效 `MergeRecord`；同 payload retry 幂等，不同 canonical selection、input bundle digest、slot bindings digest 或 output digest 必须冲突。

### 6.5 `ExpectedOutputResolution`

`ExpectedOutputResolution` 是 `EXPECTED_OUTPUT_RESOLVED` 的逻辑对象。Phase 5 第一版只承诺 `resolution_source_type=merge_record` 的 `merge_plan_output` resolution。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.expected_output_resolution.v1`。 |
| `expected_output_resolution_id` | string | 是 | 建议 `expected_output_resolved:{expected_output_id}:{merge_record_id}`。 |
| `task_id` | string | 是 | root task。 |
| `owner_unit_id` | string | 是 | 拥有该 expected output 的 parent unit。 |
| `expected_output_id` | string | 是 | Phase 4 `ExpectedOutputRef.expected_output_id`。 |
| `expected_output_name` | string | 是 | expected output name。 |
| `resolution_source_type` | string | 是 | 第一版固定 `merge_record`。 |
| `merge_record_id` | string | 是 | 来源 merge record。 |
| `merge_plan_id` | string | 是 | 来源 MergePlan。 |
| `merge_unit_id` | string | 是 | 来源 merge TaskUnit。 |
| `merge_canonical_selection_id` | string | 是 | merge unit canonical selection。 |
| `resolved_output_ref` | object | 是 | `ArtifactRef.to_dict()`。 |
| `resolved_output_digest` | string | 是 | resolved output content hash。 |
| `resolved_at` | string | 是 | UTC ISO 8601。 |

Projection 派生字段：

| 字段 | 来源 |
|---|---|
| `resolved_event_seq` | `EXPECTED_OUTPUT_RESOLVED.event_seq` |
| `resolved_batch_id` | `EXPECTED_OUTPUT_RESOLVED.batch_id` |
| `resolution_status` | 完整 `merge_resolution_batch` 可见后为 `resolved` |

约束：

- 同一 `expected_output_id` 只能 resolved 一次。
- 同一 `expected_output_id` 通过同一 `merge_record_id` 和相同 output digest retry 幂等。
- 不同 `merge_record_id`、不同 canonical selection 或不同 output digest 必须冲突。
- `blocked` 只能作为 SQLite / query hint，不作为权威 event 状态。

### 6.6 `ContributionRecord`

`ContributionRecord` 是 settlement 的计数对象。第一版只按 canonical / accepted facts 计，不按 attempt 计。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.contribution_record.v1`。 |
| `contribution_id` | string | 是 | 建议 `contribution:{kind}:{task_id}:{unit_id}:{canonical_selection_id}`。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | contribution source unit。 |
| `kind` | string | 是 | `complete_canonical`、`expand_canonical`、`merge_canonical`。 |
| `state` | string | 是 | `Pending`、`Eligible`、`Invalidated`、`Settled`。 |
| `source_attempt_id` | string | 是 | canonical attempt。 |
| `source_client_id` | string | 是 | attempt client / executor owner。 |
| `canonical_selection_id` | string | 是 | source canonical selection。 |
| `canonical_event_seq` | integer | 是 | source canonical event seq。 |
| `verification_report_id` | string | 是 | selected verification report。 |
| `verification_event_seq` | integer | 是 | selected verification event seq。 |
| `source_decision_id` | string/null | 条件 | complete / expand contribution 使用 accepted expansion decision。 |
| `merge_record_id` | string/null | 条件 | merge contribution 使用 `MergeRecord`。 |
| `source_batch_id` | string | 是 | `completion_batch`、`expansion_batch` 或 `merge_resolution_batch`。 |
| `source_terminal_event_seq` | integer | 是 | complete state event、`TASK_EXPANDED` marker 或 `MERGE_RECORDED.event_seq`。 |
| `reward_weight` | integer | 是 | 第一版默认 1；必须大于 0。 |
| `created_at` | string | 是 | UTC ISO 8601。 |
| `updated_at` | string | 是 | UTC ISO 8601。 |

第一版 contribution 类型：

- `complete_canonical`：source facts 是 canonical attempt、accepted complete decision、完整 `completion_batch`；初始 state 为 `Eligible`。
- `expand_canonical`：source facts 是 canonical attempt、accepted expand decision、完整 `expansion_batch`；初始 state 为 `Pending`。
- `merge_canonical`：source facts 是 merge unit canonical attempt、`MERGE_RECORDED`、完整 `merge_resolution_batch`；初始 state 为 `Eligible`。

状态转换：

```text
null -> Pending
null -> Eligible
Pending -> Eligible
Pending -> Invalidated
Eligible -> Invalidated
Eligible -> Settled
```

约束：

- `Settled` 只能由 `settlement_batch` 推进。
- losing attempts、late submissions、verification failures、canonical losers、retry attempts 和 shadow attempts 不产生 contribution。
- redundant verification contribution 不进入第一版。

### 6.7 `SettlementEntry` 和 `SettlementRecord`

`SettlementEntry` 是一次 root-level settlement 中的单项分配；`SettlementRecord` 是最终 marker。

`SettlementEntry` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.settlement_entry.v1`。 |
| `settlement_entry_id` | string | 是 | 建议 `settlement_entry:{settlement_record_id}:{contribution_id}`。 |
| `contribution_id` | string | 是 | 被结算 contribution。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | contribution source unit。 |
| `kind` | string | 是 | contribution kind。 |
| `source_client_id` | string | 是 | 本地 simulated client / executor owner。 |
| `reward_weight` | integer | 是 | 大于 0。 |
| `reward_units` | integer | 是 | sandbox reward integer units。 |
| `rounding_remainder_rank` | integer | 是 | remainder 分配排序位置。 |
| `reason` | string | 是 | 第一版 `eligible_contribution_at_root_completion`。 |

`SettlementRecord` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.settlement_record.v1`。 |
| `settlement_record_id` | string | 是 | 建议 `settlement:{task_id}:{root_unit_id}:{root_completion_event_seq}`。 |
| `task_id` | string | 是 | root task。 |
| `root_unit_id` | string | 是 | root unit。 |
| `root_completion_event_seq` | integer | 是 | root `TASK_UNIT_STATE_CHANGED -> Completed.event_seq`。 |
| `settlement_policy_id` | string | 是 | 第一版建议 `sandbox_equal_weight_v1`。 |
| `settlement_policy_version` | string | 是 | `v1`。 |
| `root_budget` | integer | 是 | sandbox integer reward budget。 |
| `scale` | string | 是 | 第一版 `"1"`，保留可审计字符串。 |
| `total_reward` | integer | 是 | 实际分配总额，第一版必须等于 `root_budget`。 |
| `entry_count` | integer | 是 | settlement entries 数量。 |
| `settlement_entries_digest` | string | 是 | entries canonical JSON digest。 |
| `settlement_entries_ref` | object | 是 | 完整 `SettlementEntry[]` artifact ref；第一版 `entry_count` 必须大于 0，因此该字段不得为 null。 |
| `settlement_summary` | object | 是 | kind count、client count、rounding summary。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

Sandbox reward formula：

```text
eligible_entries = Eligible contributions with source_terminal_event_seq <= root_completion_event_seq
total_weight = sum(reward_weight)
base_reward_i = floor(root_budget * reward_weight_i / total_weight)
remainder = root_budget - sum(base_reward_i)
sort entries by (contribution_id asc)
first remainder entries receive +1 reward unit
```

约束：

- `root_budget` 必须是非负整数。若 `root_budget=0`，所有 entry reward 为 0，仍可写 settlement marker。
- `entry_count` 必须大于 0；如果 root completed 但没有 eligible contribution，settlement engine 必须拒绝并暴露审计错误，因为 root completion 缺少可结算贡献来源。
- `settlement_entries_ref` 必须引用完整 `SettlementEntry[]` artifact；artifact canonical JSON digest 必须等于 `settlement_entries_digest`，数组长度必须等于 `entry_count`，每个 entry 的 `reward_units`、`reward_weight`、`contribution_id` 和 `source_client_id` 必须与同 batch 的 `Eligible -> Settled` contribution events 一致。
- replay / SQLite projection 不得只凭 `settlement_summary` 或 digest 构造 `settlement_entries`；缺失 `settlement_entries_ref`、artifact 缺失、digest 不匹配或 entries 与 state-change events 不一致，都必须作为 ledger inconsistency。
- 同一 `root_completion_event_seq` 只能有一个有效 `SETTLEMENT_RECORDED`。

### 6.8 `SubtreePruneRecord`

`SubtreePruneRecord` 是父节点完成后取消不再需要 descendant work 的 audit marker。第一版只做 post-completion cancellation。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase5.subtree_prune_record.v1`。 |
| `subtree_prune_id` | string | 是 | 建议 `subtree_pruned:{parent_unit_id}:{parent_completed_event_seq}`。 |
| `task_id` | string | 是 | root task。 |
| `parent_unit_id` | string | 是 | 已完成 parent unit。 |
| `parent_completed_event_seq` | integer | 是 | parent completion event seq。 |
| `pruning_policy_id` | string | 是 | plugin-declared policy。 |
| `pruning_policy_version` | string | 是 | policy version。 |
| `pruning_policy_plugin_id` | string | 是 | 声明该 pruning policy 的 plugin id。 |
| `pruning_policy_descriptor_digest` | string | 是 | 冻结 plugin descriptor 中该 pruning policy 所在 descriptor / policy snapshot digest。 |
| `policy_source_type` | string | 是 | `merge_policy`、`merge_plan` 或 future terminal-resolution policy。 |
| `policy_source_id` | string | 是 | policy source id。 |
| `policy_source_event_seq` | integer | 是 | `MERGE_PLAN_RECORDED`、`MERGE_RECORDED` 或未来 terminal-resolution policy event seq；用于 replay 校验 policy source 已落账。 |
| `cancelled_unit_count` | integer | 是 | cancelled descendant count。 |
| `cancelled_unit_ids_digest` | string | 是 | cancelled ids canonical JSON digest。 |
| `preserved_completed_unit_count` | integer | 是 | 已 completed / canonical preserved count。 |
| `reason` | string | 是 | 第一版 `parent_completed_post_completion_pruning`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

约束：

- 已 completed 的 unit、已有 canonical output 的 unit、已经进入 settlement evidence 的 contribution source 不得被取消或回滚。
- 如果没有 cancellable descendants，第一版不写 pruning batch。
- pruning policy 必须能从已落账 plugin descriptor / merge policy / merge plan provenance 中重建；调用方传入的自由 `pruning_policy_ref` 不能单独授权 pruning。

## 7. Event payload

### 7.1 `MERGE_TASK_LINK_RECORDED`

```text
event_type: MERGE_TASK_LINK_RECORDED
object_type: MergeTaskLink
object_id: merge_task_link_id
idempotency_key: merge_task_link:{merge_plan_id}
batch_id: merge_task_creation_batch:{merge_plan_id}
payload:
  schema_version
  merge_task_link
  task_id
  parent_unit_id
  merge_plan_id
  expansion_decision_id
  merge_unit_id
  merge_input_bundle_ref
  merge_input_bundle_digest
  required_slot_bindings_digest
  required_slot_count
  canonical_event_seqs
  readiness_reason
  created_at
```

### 7.2 `MERGE_RECORDED`

```text
event_type: MERGE_RECORDED
object_type: MergeRecord
object_id: merge_record_id
idempotency_key: merge_record:{merge_plan_id}:{merge_unit_id}:{canonical_selection_id}
batch_id: merge_resolution_batch:{merge_record_id}
payload:
  schema_version
  merge_record
  task_id
  parent_unit_id
  merge_plan_id
  merge_unit_id
  merge_task_link_id
  merge_input_bundle_ref
  merge_input_bundle_digest
  required_slot_bindings_digest
  merge_policy_id
  merge_policy_version
  merge_policy_descriptor_digest
  merge_policy_params_digest
  canonical_selection_id
  canonical_event_seq
  selected_verification_report_id
  selected_verification_event_seq
  selected_submission_id
  selected_submission_event_seq
  selected_attempt_id
  merge_output_bundle_digest
  merge_output_refs
  parent_output_mapping_digest
  created_at
```

### 7.3 `EXPECTED_OUTPUT_RESOLVED`

```text
event_type: EXPECTED_OUTPUT_RESOLVED
object_type: ExpectedOutputRef
object_id: expected_output_id
idempotency_key: expected_output_resolved:{expected_output_id}:{merge_record_id}
batch_id: merge_resolution_batch:{merge_record_id}
payload:
  schema_version
  expected_output_resolution
  task_id
  owner_unit_id
  expected_output_id
  expected_output_name
  resolution_source_type
  merge_record_id
  merge_plan_id
  merge_unit_id
  merge_canonical_selection_id
  resolved_output_ref
  resolved_output_digest
  resolved_at
```

`resolved_event_seq` 由 event envelope 派生。实现若把它写入 payload，rebuild 必须校验 `payload.resolved_event_seq == event.event_seq`。

### 7.4 `CONTRIBUTION_STATE_CHANGED`

```text
event_type: CONTRIBUTION_STATE_CHANGED
object_type: ContributionRecord
object_id: contribution_id
idempotency_key:
  contribution:create:{contribution_id}:{new_state}
  contribution:state:{contribution_id}:{old_state}:{new_state}:{source_event_seq}
payload:
  schema_version
  contribution
  old_state
  new_state
  reason
  task_id
  unit_id
  kind
  canonical_selection_id
  canonical_event_seq
  source_batch_id
  source_terminal_event_seq
  changed_at
```

### 7.5 `SETTLEMENT_RECORDED`

```text
event_type: SETTLEMENT_RECORDED
object_type: SettlementRecord
object_id: settlement_record_id
idempotency_key: settlement:{task_id}:{root_unit_id}:{root_completion_event_seq}
batch_id: settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}
payload:
  schema_version
  settlement_record
  task_id
  root_unit_id
  root_completion_event_seq
  settlement_policy_id
  settlement_policy_version
  root_budget
  scale
  total_reward
  entry_count
  settlement_entries_digest
  settlement_entries_ref
  settlement_summary
  created_at
```

### 7.6 `SUBTREE_PRUNED`

```text
event_type: SUBTREE_PRUNED
object_type: SubtreePruneRecord
object_id: subtree_prune_id
idempotency_key: subtree_pruned:{parent_unit_id}:{parent_completed_event_seq}
batch_id: subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq}
payload:
  schema_version
  subtree_prune_record
  task_id
  parent_unit_id
  parent_completed_event_seq
  pruning_policy_id
  pruning_policy_version
  pruning_policy_plugin_id
  pruning_policy_descriptor_digest
  policy_source_type
  policy_source_id
  policy_source_event_seq
  cancelled_unit_count
  cancelled_unit_ids_digest
  preserved_completed_unit_count
  reason
  created_at
```

## 8. Batch 边界和 inconsistency 规则

### 8.1 `merge_task_creation_batch:{merge_plan_id}`

固定顺序：

```text
1. TASK_UNIT_CREATED
2. optional TASK_RELATION_CREATED(kind=merge_of)
3. MERGE_TASK_LINK_RECORDED
```

规则：

- `MERGE_TASK_LINK_RECORDED` 是语义完成 marker；SQLite `merge_task_links` 只在看到 marker 后暴露 row。
- batch 必须包含且只包含一个 merge `TASK_UNIT_CREATED` 和一个 marker；`TASK_RELATION_CREATED(kind=merge_of)` 第一版最多一个。
- merge `TaskUnit` 初始 state 必须是 `Ready`。
- merge `TaskUnit` input 必须引用 `merge_input_bundle_ref`，不能只把 input 放进 metadata。
- readiness 必须同时看到 accepted `MERGE_PLAN_RECORDED` 与同一 expansion 的 final `TASK_EXPANDED` marker；只有裸 `MERGE_PLAN_RECORDED`、incomplete `expansion_batch` 或 projection 尚未暴露的 expansion-derived rows 时，不得创建 merge task。
- 缺 marker、多个 merge unit、多个 marker、relation 不匹配 parent / merge plan / merge unit、batch id 不等于 `merge_task_creation_batch:{merge_plan_id}`，都必须报 ledger inconsistency。

### 8.2 `merge_resolution_batch:{merge_record_id}`

固定顺序：

```text
1. MERGE_RECORDED
2..n. EXPECTED_OUTPUT_RESOLVED
```

规则：

- `MERGE_RECORDED` 必须引用已落账 merge unit `CANONICAL_OUTPUTS_BOUND`。
- resolution events 必须覆盖 `MergePlan.parent_output_mapping` 中该 merge record 负责的 required parent outputs。
- batch 中缺 `MERGE_RECORDED`、缺任一 required `EXPECTED_OUTPUT_RESOLVED`、出现 resolution source 不匹配、expected output id 重复或 output digest 与 merge output refs 不一致，都必须报 ledger inconsistency。
- projection 只有完整 batch 可见后，才暴露 `merge_records` 和 resolved expected outputs。
- parent completion 不进入本 batch。

### 8.3 `parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}`

固定顺序：

```text
1. TASK_UNIT_STATE_CHANGED Processing -> Completed
2..n. CONTRIBUTION_STATE_CHANGED expand_canonical Pending -> Eligible
```

规则：

- 只有 owner unit 所有 required expected outputs 都为 `resolved`，才能写 batch。
- `resolved_output_set_digest` 必须由 owner unit required expected outputs 的稳定排序和 resolved output digest 派生。
- 如果只 resolve 部分 expected outputs，不得推进 parent Completed，也不得把 `expand_canonical` 提前 Eligible。
- 如果 owner unit 已 completed，相同 resolved output set retry 幂等；不同 resolved output set 或缺 required output 必须冲突或拒绝。

### 8.4 `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}`

固定顺序：

```text
1..n. CONTRIBUTION_STATE_CHANGED Eligible -> Settled
final. SETTLEMENT_RECORDED
```

规则：

- `SETTLEMENT_RECORDED` 是 final marker。
- batch 必须 settle 全部符合条件的 Eligible contributions，不能部分 settle。
- `SETTLEMENT_RECORDED` 必须引用完整 settlement entries artifact；entries artifact digest、entry count、reward total 和同 batch settled contributions 必须互相一致。
- projection 只有看到完整 batch 和 final marker，才把 contributions 暴露为 `Settled`。
- 同一 root completion 只能有一个有效 settlement batch；同 payload retry 幂等，不同 policy / reward / entry digest 必须冲突。

### 8.5 `subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq}`

固定顺序：

```text
1..n. TASK_UNIT_STATE_CHANGED Ready/Processing/Blocked -> Cancelled
final. SUBTREE_PRUNED
```

规则：

- `SUBTREE_PRUNED` 是 final marker。
- 所有 cancelled unit 必须是 parent descendant，且不得 completed、canonical 或已进入 settlement evidence。
- 如果没有 cancellable descendants，不写 batch。
- 缺 marker、marker count 与 state events 不一致、取消非 descendant、取消 completed / canonical / settlement evidence source，都必须报 ledger inconsistency。

## 9. Idempotency 与冲突规则

| 事实 | 幂等 key | 幂等 retry | 冲突 |
|---|---|---|---|
| merge task creation batch | `merge_task_creation_batch:{merge_plan_id}` | 同 batch payload 返回既有 events | 不同 merge_unit_id、input bundle digest、slot bindings digest、marker payload |
| `MERGE_TASK_LINK_RECORDED` | `merge_task_link:{merge_plan_id}` | 同 link payload 返回既有 event | 同 merge plan 指向不同 merge unit 或不同 slot bindings |
| merge resolution batch | `merge_resolution_batch:{merge_record_id}` | 同 batch payload 返回既有 events | 缺 resolution、不同 merge output digest、不同 expected output set |
| `MERGE_RECORDED` | `merge_record:{merge_plan_id}:{merge_unit_id}:{canonical_selection_id}` | 同 merge record 返回既有 event | 同 merge plan 不同 canonical / input / output |
| `EXPECTED_OUTPUT_RESOLVED` | `expected_output_resolved:{expected_output_id}:{merge_record_id}` | 同 expected output + digest 返回既有 event | 同 expected output 不同 source 或 digest |
| contribution creation | `contribution:create:{contribution_id}:{new_state}` | 同 contribution 返回既有 event | 同 contribution id 不同 source fact、kind、state |
| contribution transition | `contribution:state:{contribution_id}:{old_state}:{new_state}:{source_event_seq}` | 同 transition 返回既有 event | 非法 state transition 或 stale old_state |
| settlement batch | `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}` | 同 entries / policy 返回既有 events | 不同 policy、budget、entries digest、reward |
| subtree pruning batch | `subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq}` | 同 cancellation set 返回既有 events | 不同 policy 或 cancellation set |

## 10. SQLite index-only projection

Phase 5 必须新增或扩展以下 projection。JSONL ledger 和 artifact files 仍是权威事实；SQLite 只做 rebuildable query view。

### 10.1 `merge_task_links`

| 列 | 说明 |
|---|---|
| `merge_task_link_id` primary key | link id。 |
| `task_id` | root task。 |
| `parent_unit_id` | parent unit。 |
| `merge_plan_id` unique | accepted MergePlan。 |
| `expansion_decision_id` | accepted expand decision。 |
| `merge_unit_id` unique | merge TaskUnit。 |
| `merge_input_bundle_artifact_id` | bundle artifact id。 |
| `merge_input_bundle_digest` | bundle digest。 |
| `required_slot_bindings_digest` | bindings digest。 |
| `required_slot_count` | slot count。 |
| `merge_policy_id` | policy id。 |
| `merge_policy_version` | policy version。 |
| `source_merge_plan_event_seq` | merge plan event seq。 |
| `source_task_expanded_event_seq` | task expanded event seq。 |
| `created_event_seq` | marker event seq。 |
| `batch_id` | creation batch id。 |
| `payload_json` | payload copy。 |

### 10.2 `merge_slot_bindings`

| 列 | 说明 |
|---|---|
| `merge_task_link_id` | link id。 |
| `slot_key` | slot key。 |
| `source_child_unit_id` | child unit。 |
| `source_output_name` | child output。 |
| `canonical_selection_id` | child canonical selection。 |
| `canonical_event_seq` | child canonical event seq。 |
| `canonical_output_digest` | child output digest。 |
| `canonical_output_bundle_digest` | child bundle digest。 |
| `payload_json` | binding payload。 |

Composite primary key：`(merge_task_link_id, slot_key)`。

### 10.3 `merge_records`

| 列 | 说明 |
|---|---|
| `merge_record_id` primary key | merge record id。 |
| `task_id` | root task。 |
| `parent_unit_id` | parent unit。 |
| `merge_plan_id` unique | one record per merge plan。 |
| `merge_unit_id` | merge unit。 |
| `merge_task_link_id` | link id。 |
| `canonical_selection_id` | merge canonical selection。 |
| `canonical_event_seq` | merge canonical event seq。 |
| `selected_attempt_id` | selected attempt。 |
| `selected_verification_report_id` | selected report。 |
| `merge_input_bundle_digest` | input digest。 |
| `required_slot_bindings_digest` | bindings digest。 |
| `merge_output_bundle_digest` | output bundle digest。 |
| `created_event_seq` | `MERGE_RECORDED.event_seq`。 |
| `batch_id` | resolution batch id。 |
| `visible` | complete batch visible flag。 |
| `payload_json` | payload copy。 |

### 10.4 `expected_output_resolutions`

| 列 | 说明 |
|---|---|
| `expected_output_resolution_id` primary key | resolution id。 |
| `expected_output_id` unique | expected output。 |
| `task_id` | root task。 |
| `owner_unit_id` | owner unit。 |
| `expected_output_name` | output name。 |
| `resolution_source_type` | `merge_record`。 |
| `merge_record_id` | merge record。 |
| `merge_plan_id` | merge plan。 |
| `merge_unit_id` | merge unit。 |
| `resolved_output_digest` | resolved digest。 |
| `resolved_event_seq` | event seq。 |
| `batch_id` | resolution batch id。 |
| `payload_json` | payload copy。 |

Rebuild 时还必须更新或覆盖现有 `expected_output_refs.resolution_status` 为 `resolved`，并设置 `expected_output_refs.resolved_event_seq`。

### 10.5 `contributions`

| 列 | 说明 |
|---|---|
| `contribution_id` primary key | contribution id。 |
| `task_id` | root task。 |
| `unit_id` | source unit。 |
| `kind` | contribution kind。 |
| `state` | current state。 |
| `source_client_id` | client / executor owner。 |
| `source_attempt_id` | source attempt。 |
| `canonical_selection_id` | canonical selection。 |
| `canonical_event_seq` | canonical event seq。 |
| `verification_report_id` | selected report。 |
| `verification_event_seq` | report event seq。 |
| `source_decision_id` | complete / expand decision。 |
| `merge_record_id` | merge record。 |
| `source_batch_id` | source batch。 |
| `source_terminal_event_seq` | terminal source event seq。 |
| `reward_weight` | reward weight。 |
| `created_event_seq` | null -> state event seq。 |
| `updated_event_seq` | latest state event seq。 |
| `settled_event_seq` | settlement transition seq。 |
| `payload_json` | latest payload copy。 |

### 10.6 `settlement_records` 和 `settlement_entries`

`settlement_records`：

| 列 | 说明 |
|---|---|
| `settlement_record_id` primary key | settlement id。 |
| `task_id` | root task。 |
| `root_unit_id` | root unit。 |
| `root_completion_event_seq` unique | root completion event seq。 |
| `settlement_policy_id` | policy id。 |
| `settlement_policy_version` | policy version。 |
| `root_budget` | budget。 |
| `scale` | scale。 |
| `total_reward` | total reward。 |
| `entry_count` | count。 |
| `settlement_entries_digest` | entries digest。 |
| `settlement_entries_artifact_id` | required artifact id。 |
| `recorded_event_seq` | marker event seq。 |
| `batch_id` | settlement batch id。 |
| `payload_json` | payload copy。 |

`settlement_entries`：

| 列 | 说明 |
|---|---|
| `settlement_entry_id` primary key | entry id。 |
| `settlement_record_id` | settlement id。 |
| `contribution_id` unique | contribution id。 |
| `task_id` | root task。 |
| `unit_id` | source unit。 |
| `kind` | contribution kind。 |
| `source_client_id` | client / executor owner。 |
| `reward_weight` | weight。 |
| `reward_units` | reward。 |
| `state_event_seq` | Eligible -> Settled event seq。 |
| `payload_json` | entry payload。 |

Rebuild 时必须从 `settlement_entries_ref` 读取完整 entries artifact，再校验：

- artifact digest 等于 `settlement_entries_digest`。
- entries 数量等于 `entry_count`。
- entries 的 `contribution_id` 集合等于同 batch 中 `Eligible -> Settled` events 的 contribution 集合。
- entries 的 `reward_units` 总和等于 `total_reward`，并等于 `root_budget`。
- 缺失 artifact、digest mismatch、entry 缺失、额外 entry 或 reward 不一致都必须让 rebuild 失败。

### 10.7 `subtree_prunes`

| 列 | 说明 |
|---|---|
| `subtree_prune_id` primary key | prune id。 |
| `task_id` | root task。 |
| `parent_unit_id` | parent unit。 |
| `parent_completed_event_seq` unique | parent completion event seq。 |
| `pruning_policy_id` | policy id。 |
| `pruning_policy_version` | policy version。 |
| `pruning_policy_plugin_id` | policy plugin id。 |
| `pruning_policy_descriptor_digest` | plugin descriptor / policy digest。 |
| `policy_source_type` | policy source type。 |
| `policy_source_id` | policy source id。 |
| `policy_source_event_seq` | policy source event seq。 |
| `cancelled_unit_count` | count。 |
| `cancelled_unit_ids_digest` | ids digest。 |
| `preserved_completed_unit_count` | preserved count。 |
| `recorded_event_seq` | marker event seq。 |
| `batch_id` | pruning batch id。 |
| `payload_json` | payload copy。 |

Projection 必须检查所有 Phase 5 batch 的 `batch_index`、`batch_size`、`event_seq` 连续性和语义顺序。半批次、重复权威事实或 payload 与 marker 不一致必须让 rebuild 失败。

## 11. Coordinator / ProtocolEngine API 草案

### 11.1 `MergeCoordinator.create_ready_merge_tasks()`

```text
create_ready_merge_tasks(
  task_id: str,
  graph: TaskGraph,
  merge_plan_events: list[LedgerEvent],
  expansion_batches: list[BatchView],
  canonical_events: list[LedgerEvent],
  now: str,
  coordinator_id: str,
  correlation_id: str,
) -> list[MergeTaskCreationFlowResult]
```

职责：

- 从完整 Phase 4 projection / ledger facts 派生 readiness。
- 对每个 visible accepted `MergePlan`，先校验其所在 `expansion_batch` 已完整且已有 final `TASK_EXPANDED` marker，再检查全部 required slots 是否有 child canonical output。
- 检查该 `merge_plan_id` 尚无有效 `MergeTaskLink`。
- 构造并 staged 保存 `MergeInputBundle` artifact。
- 通过 `append_batch()` 写 `merge_task_creation_batch:{merge_plan_id}`。

不做：

- 不执行插件 merge policy。
- 不生成 merge output。
- 不改变 parent unit state。

### 11.2 `ProtocolEngine.record_merge_resolution()`

```text
record_merge_resolution(
  merge_record: MergeRecord,
  expected_output_resolutions: list[ExpectedOutputResolution],
  correlation_id: str,
  causation_event_id: str | None = None,
) -> MergeResolutionFlowResult
```

职责：

- 校验 merge unit 已有 `CANONICAL_OUTPUTS_BOUND`。
- 校验 `MergeRecord` 与 `MergeTaskLink`、`MergeInputBundle`、canonical selection、selected verification report、merge output refs 一致。
- 校验 resolutions 覆盖 `MergePlan.parent_output_mapping` 的 required outputs。
- 通过 `append_batch()` 写 `merge_resolution_batch:{merge_record_id}`。

### 11.3 `ContributionCoordinator.record_canonical_contributions()`

```text
record_canonical_contributions(
  task_id: str,
  completion_batches: list[BatchView],
  expansion_batches: list[BatchView],
  merge_resolution_batches: list[BatchView],
  now: str,
  correlation_id: str,
) -> list[ContributionFlowResult]
```

职责：

- 从完整 `completion_batch` 创建 `complete_canonical` / `Eligible`。
- 从完整 `expansion_batch` 创建 `expand_canonical` / `Pending`。
- 从完整 `merge_resolution_batch` 创建 `merge_canonical` / `Eligible`。
- 同 contribution id retry 幂等，不按 attempt 重复创建。

### 11.4 `ProtocolEngine.record_parent_completion()`

```text
record_parent_completion(
  owner_unit: TaskUnit,
  expected_output_refs: list[ExpectedOutputRef],
  expected_output_resolutions: list[ExpectedOutputResolution],
  expand_contributions: list[ContributionRecord],
  now: str,
  correlation_id: str,
) -> ParentCompletionFlowResult
```

职责：

- 校验 owner unit 所有 required expected outputs resolved。
- 计算 `resolved_output_set_digest`。
- 写 `parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}`。
- 同批推进 owner `Processing -> Completed` 和对应 `expand_canonical Pending -> Eligible`。

### 11.5 `SettlementEngine.record_root_settlement()`

```text
record_root_settlement(
  task_id: str,
  root_unit_id: str,
  root_completion_event_seq: int,
  eligible_contributions: list[ContributionRecord],
  root_budget: int,
  settlement_policy_id: str,
  now: str,
  correlation_id: str,
) -> SettlementFlowResult
```

职责：

- 只选择 `source_terminal_event_seq <= root_completion_event_seq` 的 Eligible contributions。
- 使用 sandbox equal-weight formula 生成 entries。
- 保存完整 entries artifact，并在 `SETTLEMENT_RECORDED` 中引用 `settlement_entries_ref` 和 `settlement_entries_digest`。
- 写 root-level settlement batch，最终 marker 为 `SETTLEMENT_RECORDED`。

### 11.6 `ProtocolEngine.record_subtree_pruning()`

```text
record_subtree_pruning(
  parent_unit_id: str,
  parent_completed_event_seq: int,
  candidate_descendant_units: list[TaskUnit],
  pruning_policy_ref: dict,
  plugin_descriptor_events: list[LedgerEvent],
  policy_source_events: list[LedgerEvent],
  now: str,
  correlation_id: str,
) -> SubtreePruningFlowResult
```

职责：

- 校验 parent completed。
- 校验 pruning policy 来自已落账 plugin descriptor / policy source，且 descriptor digest 与 `pruning_policy_ref` 匹配。
- 只取消 Ready / Processing / Blocked descendant work。
- 保护 Completed、canonical output source 和 settlement evidence source。
- 有 cancellable units 时写 pruning batch；没有则返回 no-op result。

## 12. TDD 计划

所有命令在 PowerShell 下运行，先设置：

```powershell
$env:PYTHONPATH='src'
```

### Task 1: Phase 5 pure models, event constants, state rules

文件：

- 新增：`src/tokenshare/core/merge.py`
- 新增：`src/tokenshare/core/contribution.py`
- 修改：`src/tokenshare/storage/events.py`
- 测试：`tests/core/test_phase5_models.py`

红灯测试：

1. `test_phase5_event_type_constants_are_declared`
2. `test_required_slot_binding_requires_child_canonical_output`
3. `test_merge_task_link_digest_is_stable_and_rejects_duplicate_slots`
4. `test_merge_record_rejects_missing_canonical_commitment_fields`
5. `test_expected_output_resolution_is_merge_record_sourced_in_v1`
6. `test_contribution_state_machine_allows_only_phase5_transitions`
7. `test_sandbox_equal_weight_formula_distributes_remainder_deterministically`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py -q
```

预期红灯原因：Phase 5 core modules 和 event constants 不存在。

绿灯要求：

- 所有对象 `to_dict()` 输出稳定 schema version。
- digest helper 使用 canonical JSON。
- contribution transition 拒绝 `Settled -> *` 和普通 flow 的 `Eligible -> Settled`，settlement batch 之外不得 settle。
- reward formula 对相同输入稳定、总额等于 `root_budget`。

### Task 2: merge task creation batch

文件：

- 修改：`src/tokenshare/protocol_engine.py` 或新增 coordinator module。
- 测试：`tests/test_phase5_merge_task_creation_flow.py`

红灯测试：

1. `test_ready_merge_plan_creates_merge_task_link_and_ready_merge_unit_in_one_batch`
2. `test_merge_task_creation_requires_all_required_slots_canonical`
3. `test_merge_task_creation_uses_canonical_child_outputs_not_submissions`
4. `test_merge_input_bundle_is_staged_until_link_marker_records_it`
5. `test_merge_task_creation_requires_task_expanded_marker_visible`
6. `test_merge_task_creation_rejects_merge_plan_from_incomplete_expansion_batch`
7. `test_merge_task_creation_same_payload_is_idempotent`
8. `test_merge_task_creation_different_slot_binding_conflicts_without_new_task`
9. `test_merge_task_creation_batch_without_marker_is_projection_inconsistent`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_merge_task_creation_flow.py -q
```

绿灯要求：

- batch 顺序为 merge `TASK_UNIT_CREATED`、可选 relation、`MERGE_TASK_LINK_RECORDED`。
- merge unit state 为 `Ready`。
- `merge_input_bundle` artifact ref 和 digest 在 marker 中可审计。
- accepted `MergePlan` 必须来自完整可见 expansion batch；裸 `MERGE_PLAN_RECORDED` 或缺 `TASK_EXPANDED` 时不得写 merge task creation batch。
- 缺 slot 或使用非 canonical output 时不写任何权威 event。

### Task 3: merge resolution and expected output resolution batch

文件：

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase5_merge_resolution_flow.py`

红灯测试：

1. `test_merge_resolution_records_merge_then_expected_outputs_in_one_batch`
2. `test_merge_record_requires_merge_unit_canonical_outputs_bound`
3. `test_merge_resolution_rejects_losing_or_late_merge_attempt`
4. `test_merge_resolution_resolves_each_required_parent_output_once`
5. `test_merge_resolution_same_payload_is_idempotent`
6. `test_merge_resolution_different_output_digest_conflicts`
7. `test_incomplete_merge_resolution_batch_is_projection_inconsistent`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_merge_resolution_flow.py -q
```

绿灯要求：

- `MERGE_RECORDED` 只在 merge unit canonical event 后写。
- `EXPECTED_OUTPUT_RESOLVED` 覆盖 required parent output mapping。
- `expected_output_refs` projection 从 `expected` 更新为 `resolved`。
- parent completion 不在本 batch。

### Task 4: contribution creation

文件：

- 新增或修改：`src/tokenshare/core/contribution.py`
- 修改：`src/tokenshare/protocol_engine.py` 或新增 coordinator module。
- 测试：`tests/test_phase5_contribution_settlement_flow.py`

红灯测试：

1. `test_complete_batch_creates_complete_canonical_eligible_contribution`
2. `test_expansion_batch_creates_expand_canonical_pending_contribution`
3. `test_merge_resolution_batch_creates_merge_canonical_eligible_contribution`
4. `test_losing_attempts_and_canonical_losers_do_not_create_contribution`
5. `test_duplicate_contribution_creation_is_idempotent`
6. `test_contribution_creation_conflicts_on_different_source_fact`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_contribution_settlement_flow.py -q
```

绿灯要求：

- contribution id deterministic。
- initial state 与 kind 匹配。
- 不按 attempt / retry / late submission 重复创建。

### Task 5: parent completion batch

文件：

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase5_merge_resolution_flow.py`
- 测试：`tests/test_phase5_contribution_settlement_flow.py`

红灯测试：

1. `test_parent_completion_waits_for_all_required_expected_outputs_resolved`
2. `test_parent_completion_batch_completes_owner_and_promotes_expand_contribution`
3. `test_parent_completion_rejects_partial_expected_output_resolution`
4. `test_parent_completion_same_resolved_set_is_idempotent`
5. `test_parent_completion_different_resolved_set_conflicts`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py -q
```

绿灯要求：

- parent unit 在 required outputs resolved 前保持 `Processing`。
- `expand_canonical` 只在 parent completion batch 中从 `Pending` 到 `Eligible`。
- resolved output set digest 稳定。

### Task 6: root-level sandbox settlement batch

文件：

- 修改：`src/tokenshare/protocol_engine.py` 或新增 `SettlementEngine`。
- 测试：`tests/test_phase5_contribution_settlement_flow.py`

红灯测试：

1. `test_root_completion_settles_all_eligible_contributions_in_one_batch`
2. `test_settlement_batch_ends_with_settlement_recorded_marker`
3. `test_settlement_rejects_partial_settled_contributions`
4. `test_settlement_same_payload_is_idempotent`
5. `test_settlement_different_entries_digest_conflicts`
6. `test_settlement_does_not_include_pending_invalidated_or_late_contributions`
7. `test_root_completion_generates_exactly_one_settlement_record`
8. `test_settlement_requires_entries_artifact_ref`
9. `test_settlement_rejects_entries_artifact_digest_mismatch`
10. `test_settlement_entries_must_match_settled_contribution_events`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_contribution_settlement_flow.py -q
```

绿灯要求：

- all Eligible contributions at root completion are settled together。
- `SETTLEMENT_RECORDED` 是 final marker。
- no double settlement。
- reward sum equals root budget。
- `settlement_entries_ref` artifact 是 replay / projection 的唯一 entries 来源，不能只靠 summary 或 digest 重建 entries。

### Task 7: subtree pruning batch

文件：

- 修改：`src/tokenshare/protocol_engine.py`
- 测试：`tests/test_phase5_subtree_pruning_flow.py`

红灯测试：

1. `test_subtree_pruning_after_parent_completion_cancels_unfinished_descendants`
2. `test_subtree_pruning_preserves_completed_and_canonical_units`
3. `test_subtree_pruning_requires_plugin_declared_policy`
4. `test_subtree_pruning_same_payload_is_idempotent`
5. `test_subtree_pruning_different_cancelled_set_conflicts`
6. `test_subtree_pruning_batch_without_marker_is_projection_inconsistent`
7. `test_subtree_pruning_rejects_policy_without_descriptor_provenance`
8. `test_subtree_pruning_rejects_policy_source_event_mismatch`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\test_phase5_subtree_pruning_flow.py -q
```

绿灯要求：

- cancellation 使用现有 `TaskState.CANCELLED`。
- 不回滚 completed / canonical / settlement source。
- pruning policy 必须能用 plugin descriptor digest 和 policy source event seq 重建 provenance。
- no-op pruning 不写 batch。

### Task 8: SQLite projection, integration, and code map

文件：

- 修改：`src/tokenshare/storage/sqlite_index.py`
- 新增：`tests/storage/test_phase5_event_projection.py`
- 新增：`Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md`
- 修改：`Doc/agent-navigation.md`
- 修改：`feature_list.json`
- 修改：`progress.md`
- 修改：`session-handoff.md`

红灯测试：

1. `test_sqlite_rebuilds_merge_task_links_and_slot_bindings_only_after_marker`
2. `test_sqlite_rebuilds_merge_records_and_expected_output_resolutions_only_after_complete_batch`
3. `test_sqlite_updates_expected_output_refs_to_resolved`
4. `test_sqlite_rebuilds_contribution_state_machine`
5. `test_sqlite_rebuilds_settlement_records_and_entries_only_after_marker`
6. `test_sqlite_rebuilds_subtree_prunes_only_after_marker`
7. `test_sqlite_rejects_duplicate_merge_task_link_for_merge_plan`
8. `test_sqlite_rejects_duplicate_expected_output_resolution`
9. `test_sqlite_rejects_settlement_without_entries_artifact`
10. `test_sqlite_rejects_settlement_entries_digest_or_event_mismatch`
11. `test_sqlite_rejects_pruning_policy_without_descriptor_provenance`
12. `test_sqlite_rejects_incomplete_phase5_batches`
13. `test_phase5_complete_integration_merge_to_root_settlement`

命令：

```powershell
conda run -n tokenshare python -m pytest tests\storage\test_phase5_event_projection.py -q
conda run -n tokenshare python -m pytest tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py tests\test_phase5_subtree_pruning_flow.py tests\storage\test_phase5_event_projection.py -q
```

完整验证：

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
conda run -n tokenshare python -m compileall -x "reference_repos" .
$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests
.\init.ps1
```

绿灯要求：

- SQLite projection 能从 JSONL rebuild 全部 Phase 5 query tables。
- 半批次和 duplicate authority conflict 必须让 rebuild 失败。
- settlement entries 必须从 artifact rebuild，pruning provenance 必须从已落账 descriptor / policy source rebuild。
- 新增 `Doc/TechnicalDocument/2026-06-25-phase-5-code-map.md` 映射 spec、源码和测试。
- `feat-006` evidence 写入红绿验证证据后才能标记 done。

## 13. 实现顺序建议

建议按以下顺序执行：

1. 先实现 pure models、event constants 和 contribution state rules，给后续 flow 复用。
2. 再实现 merge task creation；它只依赖 Phase 4 `MergePlan`、child canonical outputs 和 `append_batch()`。
3. 再实现 merge resolution；它依赖 merge unit 完整 Phase 3 / Phase 4 生命周期。
4. 再实现 contribution creation；它从 complete / expand / merge 三类完整 batch 派生。
5. 再实现 parent completion；它依赖 expected output resolutions 和 pending expand contribution。
6. 再实现 root settlement；它依赖 eligible contributions 和 root completion event。
7. 最后实现 subtree pruning 和 SQLite projection，因为它们需要完整事件合约。

## 14. 自审清单

本文已固定以下实现前必须明确的工程规格：

- Phase 5 对象字段和 schema version。
- Phase 5 event type、event payload 和 idempotency key。
- 五类 Phase 5 batch 的顺序、marker 和 half-batch inconsistency 规则。
- merge readiness、merge input bundle、`MergeTaskLink`、`MERGE_RECORDED` 和 expected output resolution 的权威边界。
- contribution taxonomy、state transitions、root-level settlement batch、entries artifact replay 来源和 sandbox reward formula。
- post-completion subtree pruning authority、descriptor provenance 和 cancellation 限制。
- SQLite index-only projection 表、可见性 gate 和 rebuild conflict 规则。
- Coordinator / ProtocolEngine 第一版 API 草案。
- TDD 测试文件、测试名、命令、红灯原因和绿灯要求。

本文没有新增外部资料；使用的外部资料已经在 Phase 5 external systems notes 中本地落库。
