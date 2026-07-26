# TokenShare V1 完整说明

日期：2026-07-13

状态：Phase 1-6 收敛后的默认说明文档。本文接管旧 Phase 1-6 字段规格、TDD 讨论稿、scope change 和阶段说明的默认阅读入口。旧文档已移动到 `Doc/TechnicalDocument/phase-1-6-archive/`，只作为历史材料保留，不作为默认索引或新实现权威。

本文只覆盖 Phase 1-6 已实现的协议内核、存储、插件和执行器契约。Phase 7 AI API executor、Phase 8 experiment infrastructure 和当前 `feat-011` Paper Real AI Experiments 仍使用各自现有文档，尤其论文实验设计以 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md` 为唯一权威。

## 1. 项目在做什么

TokenShare 是一个本地研究原型，用 Python、SQLite、JSONL、JSON 和本地文件系统验证一种协议：大型任务可以被拆成可调度、可验证、可合并、可结算的子任务，并且每一步都能从 append-only event ledger 和 artifact 重新审计。

V1 不是生产网络，也不是链上系统。它要验证的是协议机制本身：

- `TaskSpec` 注册一个根任务。
- `TaskUnit` 组成可递归扩展的任务图。
- `Scheduler` 根据 ready 状态、依赖满足情况、能力匹配和 active lease 限制分派工作。
- `Lease` 用 fencing token 和 TTL 表示一个执行授权。
- `Attempt` 表示一次被 lease 授权的执行尝试。
- 执行器只接收 `ExecutionRequest` 并返回 `ExecutionSubmission`，不能改任务图。
- 插件声明 schema、split strategy、validator policy 和 merge policy。
- 候选输出必须先通过 verification，再绑定唯一 canonical output bundle。
- 拆分只能由版本化插件 split strategy 生成 `DecompositionProposal` / `MergePlan`，不能由 AI 或 executor 临时决定协议级子任务。
- merge、contribution、settlement 和 subtree pruning 都以事件批次写入，供 SQLite projection 和后续 replay/audit 使用。

当前 Phase 1-6 完成的两个真实插件目标是：

- `factorization`：整数分解插件，验证普通可拆分计算任务。
- `lean_proof`：真实 Lean 形式化证明插件，验证 proof artifact、checker evidence 和确定性 theorem split / merge。

`structured_report_stub` 已从 Phase 6 开发计划剔除。仓库中如仍出现 `structured_report_stub` 或 `lean_stub`，只能理解为历史 fixture / compatibility / provenance，不是通过证据，也不是后续开发目标。

## 2. 三层边界

TokenShare V1 必须保持三层边界。

### 2.1 协议内核

协议内核在 `src/tokenshare/core/`、`src/tokenshare/protocol_engine.py` 和 `src/tokenshare/storage/` 中实现。它负责：

- 定义通用对象：`TaskSpec`、`TaskUnit`、`TaskRelation`、`ClientRecord`、`Lease`、`Attempt`、`ArtifactRef`、`ProtocolConfig`。
- 定义通用状态机：`TaskUnit`、`Lease`、`Attempt`、`ContributionRecord`。
- 维护 `TaskGraph` 的节点、边、ready 判断、依赖满足和无环约束。
- 编排 storage-backed flow：调度、lease heartbeat / expiry、request/submission、verification、canonical binding、split invocation、complete/expand decision、merge resolution、parent completion、root settlement 和 subtree pruning。
- 写入 JSONL event ledger，并用 batch envelope 表达必须原子出现的协议事实组。
- 用 SQLite 生成可重建索引。

协议内核不负责：

- factorization 数学校验规则。
- Lean theorem parsing、proof checking 或 helper tactic 逻辑。
- AI prompt 文本、AI raw output 解释策略或 provider 调用。
- 动态插件市场、真实网络 worker pool、真实链上支付。

### 2.2 任务插件

任务插件在 `src/tokenshare/plugins/` 中实现。插件负责声明和执行领域规则：

- `PluginDescriptor`：插件身份、版本、支持的 task types、输入/输出 contract、execution contract、split strategies、validator policy、merge policy。
- split strategy：把某个 canonical output 解释成 `SplitStrategyResult`，并在 `expand` 时生成 `DecompositionProposal` 和 `MergePlan`。
- validator：把领域检查结果映射为 Phase 4 `plugin_domain_check`。
- merge policy：消费 required slot 的 canonical child outputs，生成 parent 输出或 merge output artifact。
- prompt / parse policy：如果需要 AI 候选输出，插件拥有 prompt package 结构和 raw output parser。

插件不能：

- 直接写 protocol ledger。
- 改 `TaskUnit.state`。
- 自己绑定 canonical output。
- 自己创建结算事实。
- 让 AI 输出定义任务图或 split strategy。

### 2.3 执行器

Phase 1-6 中的执行器契约在 `src/tokenshare/executors/contracts.py`、`deterministic.py`、`mock_ai.py` 和 `registry.py` 中实现。执行器负责：

- 接收 `ExecutionRequest`。
- 保存或返回 raw / parsed / candidate output refs。
- 返回 `ExecutionSubmission`。
- 声明 `ExecutorDescriptor`、状态和能力。

执行器不能：

- 决定 canonical output。
- 创建 `DecompositionProposal`。
- 改 `TaskGraph`。
- 决定 reward / settlement。

Phase 7 的真实 AI API executor 是实验级扩展，不属于本文 Phase 1-6 合并范围；但它必须继续遵守同一个 `ExecutionRequest` / `ExecutionSubmission` 边界。

## 3. 稳定对象和状态线

### 3.1 `ArtifactRef`

`ArtifactRef` 是协议看到的 artifact 引用，不是文件系统实现。它包含：

- `artifact_id`
- `artifact_type`
- `uri`
- `content_hash`
- `size_bytes`
- `media_type`
- `artifact_schema_id`
- `artifact_schema_version`
- `source`
- `metadata`
- `created_at`
- `schema_version`

真实文件保存、读取和 hash 校验由 `ArtifactStore` 实现。event payload 和 SQLite projection 应保存 refs、digest 和摘要，不保存长 raw text 或大模型输出全文。

### 3.2 `TaskSpec`

`TaskSpec` 是根任务注册快照。它绑定：

- `task_id`
- `plugin_id` / `plugin_version`
- `split_strategy_id` / `split_strategy_params`
- `root_input_ref`
- `ProtocolConfig`
- `root_budget` / `root_deadline`
- metadata 和创建时间

它不是运行时任务图本身。运行时可调度单位是 `TaskUnit`。

### 3.3 `TaskUnit`

`TaskUnit` 是任务图节点，字段包括：

- `unit_id`
- `task_id`
- `parent_unit_id`
- `depth`
- `unit_type`
- `state`
- `input_refs`
- `canonical_output_refs`
- `required_capabilities`
- `weight`
- `budget_limit`
- `deadline`
- `plugin_payload`
- `metadata`

`TaskUnit.state` 只表达节点级生命周期，不表达 lease 有效性，也不表达 verification 进度。当前状态枚举包括：

- `Created`
- `Blocked`
- `Ready`
- `Processing`
- `WaitingForChildren`
- `MergeReady`
- `Merging`
- `Completed`
- `MergeFailed`
- `Failed`
- `Cancelled`

当前实现允许的核心转换包括：

- `Created -> Ready`
- `Created -> Blocked`
- `Blocked -> Ready`
- `Ready -> Processing`
- `Processing -> Ready`
- `Processing -> Failed`
- `Processing -> Completed`
- `Ready | Processing | Blocked -> Cancelled`

Phase 5 的 parent completion 和 subtree pruning 会通过 event batch 写 `TASK_UNIT_STATE_CHANGED`。

### 3.4 `TaskRelation` 和 `TaskGraph`

`TaskRelation` 表达 `TaskUnit` 之间的边。当前关键关系是 `depends_on_output`：

- `source_unit_id`
- `source_output_name`
- `target_unit_id`
- `target_input_name`

`TaskGraph` 是可从 ledger / projection 重建的内存 view。它会：

- 校验所有 units 属于同一个 `task_id`。
- 校验关系的 source / target unit 存在。
- 对 `depends_on_output` 校验 source / target output name。
- 禁止重复 target input binding。
- 校验图无环。
- 注入 `canonical_outputs_by_unit_id` 后判断依赖是否满足。
- 根据 `Ready` 状态和依赖满足情况输出 `ready_unit_ids()`。

SQLite 不是权威状态；权威状态来自 event ledger 和 artifact refs。

### 3.5 `Lease`

`Lease` 是一个带 fencing token 的限时执行授权。字段包括：

- `lease_id`
- `task_id`
- `unit_id`
- `attempt_id`
- `client_id`
- `state`
- `fencing_token`
- `issued_at`
- `expires_at`
- `last_heartbeat_at`
- `heartbeat_count`
- `lease_kind`
- terminal reason / metadata

状态线：

- `Active -> Active`：heartbeat，刷新 TTL 和 heartbeat count。
- `Active -> Released`
- `Active -> Expired`
- `Active -> Revoked`

`LeaseManager.heartbeat()` 要求 lease 仍为 `Active`，并且 `now < expires_at`。`LeaseManager.expire()` 要求 `now >= expires_at`，并把相关 attempt supersede，再按 retry policy 让 `TaskUnit` 回到 `Ready` 或进入 `Failed`。

### 3.6 `Attempt`

`Attempt` 是一次被 lease 授权的执行尝试。它承载 execution、submission、verification 和 canonical 进度。字段包括：

- `attempt_id`
- `task_id`
- `unit_id`
- `lease_id`
- `client_id`
- `state`
- `attempt_kind`
- timestamps
- environment summary
- input/raw/parsed/candidate/log artifact refs
- failure kind / reason
- supersession 信息

状态线：

- `Created -> Running`
- `Created -> Superseded`
- `Running -> Submitted`
- `Running -> Failed`
- `Running -> Superseded`
- `Submitted -> Verified`
- `Submitted -> Rejected`
- `Verified -> Canonical`

verification `error` 不会写 `Submitted -> Submitted` 这种假进度。canonical loser 保持 `Verified`，不会被强行改成 rejected。

### 3.7 `ContributionRecord`

`ContributionRecord` 记录 canonical output、expand decision、merge result 等对最终结果的贡献。状态包括：

- `Eligible`
- `Settled`
- `Invalidated`

settlement 是 sandbox 结算，不是真实代币支付。当前结算使用 equal-weight sandbox helper，要求 settlement entry 总额等于 root budget，并通过 batch 写入 contribution state changes 和 `SETTLEMENT_RECORDED`。

## 4. 存储和事件机制

### 4.1 `ArtifactStore`

`ArtifactStore` 使用本地文件系统保存 artifact body，并写 manifest。它提供：

- `save_bytes()`
- `save_json()`
- `read_bytes()`
- `verify()`

保存 artifact 时生成 `ArtifactRef`，并带有内容 hash、schema id/version、source、metadata、media type 和创建时间。协议事件通常只记录 artifact ref、digest、summary 和少量索引字段。

### 4.2 `LedgerEvent`

`LedgerEvent` 是 JSONL event envelope。当前 `LedgerEvent.v2` 字段包括：

- `schema_version`
- `event_seq`
- `event_id`
- `event_type`
- `occurred_at`
- `task_id`
- `object_type`
- `object_id`
- `actor`
- `correlation_id`
- `causation_event_id`
- `idempotency_key`
- `payload`
- `prev_event_hash`
- `event_hash`
- `batch_id`
- `batch_index`
- `batch_size`

`event_hash` 由除自身外的字段计算。`EventLedger.verify_hash_chain()` 用 `event_seq`、`prev_event_hash` 和 canonical JSON hash 检查日志是否被篡改。

`idempotency_key` 是 retry dedupe，不是覆盖写。如果同一个 key 对应的 event type、object、task 或 canonical payload 不同，`EventLedger.append()` 会报 conflict。

### 4.3 `append_batch()`

`EventLedger.append_batch(events, batch_id)` 是 Phase 4+ 的本地原子落账基础。它保证：

- batch 不能为空。
- batch 内 idempotency key 不能重复。
- batch 事件获得连续 `event_seq`。
- 每个事件写入相同 `batch_id`。
- `batch_index` 从 1 到 `batch_size` 连续。
- 全批已存在且完全匹配时作为幂等 retry 返回旧事件。
- 部分已存在或 batch id 冲突时失败。

`append_batch()` 只做 envelope 和 storage 原子性；batch 语义由 `ProtocolEngine`、SQLite projection 和后续 replay/audit 检查。

### 4.4 Event types

当前 Phase 1-6 相关 event types：

```text
ARTIFACT_STORED
TASK_REGISTERED
TASK_UNIT_CREATED
TASK_RELATION_CREATED
CLIENT_REGISTERED
TASK_UNIT_STATE_CHANGED
CLIENT_STATE_CHANGED
LEASE_STATE_CHANGED
ATTEMPT_STATE_CHANGED
RECOVERY_ACTION_RECORDED
REGISTRY_SNAPSHOT_RECORDED
EXECUTION_REQUEST_RECORDED
EXECUTION_SUBMISSION_RECORDED
VERIFICATION_RECORDED
CANONICAL_OUTPUTS_BOUND
SPLIT_STRATEGY_INVOCATION_RECORDED
DECOMPOSITION_PROPOSAL_RECORDED
EXPANSION_DECISION_RECORDED
MERGE_PLAN_RECORDED
TASK_EXPANDED
MERGE_TASK_LINK_RECORDED
MERGE_RECORDED
EXPECTED_OUTPUT_RESOLVED
CONTRIBUTION_STATE_CHANGED
SETTLEMENT_RECORDED
SUBTREE_PRUNED
```

### 4.5 SQLite projection

`SQLiteMaterializedIndex.rebuild_from_events()` 会从 ledger events 重建索引表。SQLite 表不是权威，只是查询和审计辅助。当前 projection 会重建：

- `ledger_events`
- `task_specs`
- `task_units`
- `task_relations`
- `artifact_refs`
- `client_records`
- `leases`
- `attempts`
- `recovery_actions`
- `registry_snapshots`
- `execution_requests`
- `execution_submissions`
- `executor_statuses`
- `verification_reports`
- `canonical_outputs`
- `split_strategy_invocations`
- `decomposition_proposals`
- `expansion_decisions`
- `merge_plans`
- `expected_output_refs`
- `merge_task_links`
- `merge_slot_bindings`
- `merge_records`
- `expected_output_resolutions`
- `contributions`
- `settlement_records`
- `settlement_entries`
- `subtree_prunes`

projection 会校验若干 batch 语义：

- `completion_batch:{expansion_decision_id}` 必须包含 complete decision 和 `TASK_UNIT_STATE_CHANGED -> Completed`。
- `expansion_batch:{expansion_decision_id}` 必须包含 proposal、expand decision、merge plan、child unit events、relation events 和最终 `TASK_EXPANDED` marker。
- `merge_task_creation_batch:{merge_plan_id}` 必须以 `MERGE_TASK_LINK_RECORDED` 作为最终 marker。
- `merge_resolution_batch:{merge_plan_id}` 必须包含 `MERGE_RECORDED` 和 expected output resolutions。
- `parent_completion_batch:{parent_unit_id}` 必须包含 parent completed state change 和 contribution changes。
- `settlement_batch:{task_id}` 必须包含 contribution state changes 和 `SETTLEMENT_RECORDED`。
- `subtree_pruning_batch:{task_id}:{root_unit}` 必须包含 cancellation state changes 和 `SUBTREE_PRUNED`。

## 5. 协议执行机制

### 5.1 注册根任务

根任务注册由 `RootTaskRegistrar.register_root_task()` 处理：

1. 保存 root input artifact，得到 `ArtifactRef`。
2. 构造 `TaskSpec`。
3. 创建 root `TaskUnit`，初始状态为 `Ready`。
4. 写入注册相关事件。
5. 返回 registration result。

`RootTaskRegistrar` 是窄兼容入口，不负责调度、租约、attempt、verification、merge 或 settlement。Phase 2 之后的 storage-writing application flow 主要在 `ProtocolEngine` 中。

### 5.2 调度和 lease claim

`ProtocolEngine.schedule_ready_unit()` 的执行链：

1. 从 ledger 推导当前 active leases，防止 caller 漏传 active lease map 导致重复 claim。
2. 调用 `Scheduler.select_next()`。
3. `Scheduler` 按 `(TaskUnit.created_at, unit_id)` FIFO 遍历 `TaskGraph.ready_unit_ids()`。
4. 若 `allow_shadow_execution=False`，跳过已有 active lease 的 unit。
5. 检查 client status 和 capability。
6. 生成 `SchedulingDecision`。
7. `LeaseManager.claim()` 生成 `Lease`、`Created Attempt` 和 `Running Attempt`。
8. 写入 `LEASE_STATE_CHANGED`、两条 `ATTEMPT_STATE_CHANGED` 和 `TASK_UNIT_STATE_CHANGED Ready -> Processing`。

调度不会调用插件，也不会执行任务本身。

### 5.3 heartbeat 和 lease expiry

heartbeat：

1. `ProtocolEngine.record_lease_heartbeat()` 调用 `LeaseManager.heartbeat()`。
2. 只允许 active lease 且 `now < expires_at`。
3. 写入 `LEASE_STATE_CHANGED Active -> Active`。

expiry：

1. `ProtocolEngine.record_lease_expiry()` 调用 `LeaseManager.expire()`。
2. 只允许 active lease 且 `now >= expires_at`。
3. `Lease` 进入 `Expired`。
4. `Attempt` 进入 `Superseded`。
5. 根据 retry count 与 `ProtocolConfig.max_retries`，`TaskUnit` 回到 `Ready` 或进入 `Failed`。
6. 写入 `RECOVERY_ACTION_RECORDED` 和 state change events。

### 5.4 registry freeze

`PluginRegistry` 和 `ExecutorRegistry` 在一次 run 中冻结版本化 descriptor：

1. 插件和执行器先注册 descriptor。
2. freeze 时 descriptor body 被保存为 artifact。
3. `ProtocolEngine.record_registry_snapshot()` 写 `REGISTRY_SNAPSHOT_RECORDED`。
4. 后续 request、verification、split 和 merge 通过 descriptor digest 校验策略来源。

插件 registry 会拒绝多个插件声明同一 exclusive task type。Executor registry 会根据 capability 和 status 选择可用 executor。

### 5.5 execution request

`ProtocolEngine.record_execution_request()`：

1. 接收 `ExecutionRequest`。
2. 把 request body 持久化为 artifact。
3. 在 event payload 中记录 request ref、digest、plugin/executor 摘要和索引字段。
4. 写 `EXECUTION_REQUEST_RECORDED`。

`ExecutionRequest` 包含 request id、task/unit/attempt/lease、plugin/executor 信息、input refs、requested outputs、instruction refs、prompt package refs、allocation decision 和 metadata。

### 5.6 execution submission

`ProtocolEngine.record_execution_submission()`：

1. 接收 `ExecutionSubmission`。
2. 保存 submission artifact。
3. 写 `EXECUTION_SUBMISSION_RECORDED`。
4. 只有当 submission 的 task/unit/attempt/lease/fencing token 与当前 running attempt 匹配时，才写 `ATTEMPT_STATE_CHANGED Running -> Submitted`。
5. mismatch submission 保留为 audit-only event，不推进 attempt。

`ExecutionSubmission` 可以携带 raw output、parsed output、candidate output refs、log refs、environment summary、result kind 和 error summary。它不是 verification report。

### 5.7 verification

`ProtocolEngine.record_verification()`：

1. 读取已提交 attempt 和 submission 事实。
2. 重建或校验 `VerificationReport`。
3. `eligible_for_canonical` 必须由 status 和 required layers 派生，caller 不能伪造。
4. 写 `VERIFICATION_RECORDED`。
5. 如果 report `passed` 或 `accepted`，attempt 从 `Submitted` 到 `Verified`。
6. 如果 report `rejected`，attempt 从 `Submitted` 到 `Rejected`。
7. 如果 report `error`，不写 attempt state change。

通用 verification layers：

- `schema_check`
- `artifact_integrity_check`
- `required_output_coverage_check`
- `evidence_reference_check`
- `plugin_domain_check`
- `audit_check`

插件 domain validator 的输出进入 `plugin_domain_check`，但 verification report 的通用结构属于协议层。

### 5.8 canonical output binding

`ProtocolEngine.bind_canonical_outputs()`：

1. 收集某个 `TaskUnit` 已落账的 eligible verification reports。
2. 调用 `select_first_verified_bundle()`，按最小 verification event seq 选择 winner。
3. 写唯一 `CANONICAL_OUTPUTS_BOUND`。
4. 将 winner attempt 从 `Verified` 推进到 `Canonical`。
5. loser attempts 保持 `Verified`。

SQLite 的 `canonical_outputs` 表对 `(task_id, unit_id)` 有唯一约束。重复 binding 只能是完全相同 commitment 的幂等 retry；不同 commitment 必须失败。

### 5.9 split invocation

`ProtocolEngine.record_split_strategy_invocation()` 记录插件 split strategy 调用结果摘要：

- invocation id
- canonical selection id
- plugin id/version/descriptor digest
- split strategy id
- params digest
- expansion scope hash
- status
- result action / digest
- error kind / summary

`SPLIT_STRATEGY_INVOCATION_RECORDED` 是 audit event。失败或 invalid result 不会创建 proposal、merge plan、child units 或 task state mutation。

### 5.10 complete decision

如果插件 split strategy 决定当前 unit 已能完成，`ProtocolEngine.record_complete_decision()` 使用 `completion_batch:{expansion_decision_id}` 写两条事件：

1. `EXPANSION_DECISION_RECORDED(action=complete)`
2. `TASK_UNIT_STATE_CHANGED Processing -> Completed`

complete decision 不创建 `DecompositionProposal`、`MergePlan` 或 child graph。`action_body.completion_evidence` 内联记录 validator policy、verification report、canonical selection、canonical bundle digest、completed output refs 和 plugin completion summary。

### 5.11 expand decision

如果插件 split strategy 决定 expand，`ProtocolEngine.record_expand_decision()`：

1. 校验 source invocation、canonical selection、plugin descriptor digest、split strategy id、params digest 和 expansion scope。
2. 校验 staged proposal artifact 和 merge plan artifact。
3. 调用 `validate_decomposition_proposal_limits()`，检查 max depth、max children、max total units 等协议限制。
4. 从 proposal 构造 child `TaskUnit`，child 初始状态由协议根据依赖和 input binding 推导，插件 payload 不能直接指定 `Ready` / `Blocked`。
5. 从 proposal 构造 `TaskRelation`。
6. 使用 `expansion_batch:{expansion_decision_id}` 写：
   - `DECOMPOSITION_PROPOSAL_RECORDED`
   - `EXPANSION_DECISION_RECORDED(action=expand)`
   - `MERGE_PLAN_RECORDED`
   - child `TASK_UNIT_CREATED`
   - child `TASK_RELATION_CREATED`
   - final `TASK_EXPANDED`

projection 只有看到最终 `TASK_EXPANDED` marker 才把 proposal / merge plan / expected output refs 视为可消费。

### 5.12 merge task creation

`MergeCoordinator.create_ready_merge_tasks()` 根据 accepted `MergePlan` 和 child canonical outputs 判断 required slots 是否齐备：

1. 读取 visible merge plans。
2. 查找所有 required slots 的 child canonical output。
3. 构造 `RequiredSlotBinding`。
4. 保存 merge input bundle artifact。
5. 创建 merge `TaskUnit`。
6. 可选写 `TaskRelation(kind=merge_of)`。
7. 写 `MERGE_TASK_LINK_RECORDED`。

这些事件在 `merge_task_creation_batch:{merge_plan_id}` 中出现，最终 marker 是 `MERGE_TASK_LINK_RECORDED`。merge task 是普通 `TaskUnit`，后续仍走 execution、submission、verification、canonical binding。

### 5.13 merge resolution

merge unit 有 canonical output 后，`ProtocolEngine.record_merge_resolution()`：

1. 校验 merge task link、merge input bundle、required slot binding digest。
2. 校验 merge record 和 merge unit canonical event 一致。
3. 写 `MERGE_RECORDED`。
4. 为 parent expected outputs 写 `EXPECTED_OUTPUT_RESOLVED`。

这些事件在 `merge_resolution_batch:{merge_plan_id}` 中出现。`ExpectedOutputResolution` v1 只支持 `merge_record` 来源。

### 5.14 parent completion

`ProtocolEngine.record_parent_completion()`：

1. 校验 parent 所有 required expected outputs 已 resolution。
2. 写 parent `TASK_UNIT_STATE_CHANGED -> Completed`。
3. 根据 completion / expansion / merge resolution 批次生成 eligible contributions。
4. 写 `CONTRIBUTION_STATE_CHANGED`。

这些事件在 `parent_completion_batch:{parent_unit_id}` 中出现。

### 5.15 root settlement

`ProtocolEngine.record_root_settlement()`：

1. 校验 root unit 已 completed。
2. 从 ledger 读取当前 eligible contributions。
3. 要求 caller supplied eligible set 与 ledger 当前 eligible set 精确一致。
4. 按 sandbox formula 生成 `SettlementEntry`。
5. 写 contribution `Eligible -> Settled`。
6. 写 `SETTLEMENT_RECORDED`。

这些事件在 `settlement_batch:{task_id}` 中出现。V1 没有真实代币支付或链上转账。

### 5.16 subtree pruning

`ProtocolEngine.record_subtree_pruning()`：

1. 校验 pruning policy source，当前可从 merge plan policy 追溯。
2. 根据 subtree root 找 descendant units。
3. 排除已经有 canonical output 或 settlement evidence 的 units。
4. 对可取消 units 写 `TASK_UNIT_STATE_CHANGED -> Cancelled`。
5. 写 `SUBTREE_PRUNED`。

这些事件在 `subtree_pruning_batch:*` 中出现。当前 factorization 第一切片不把 subtree pruning 用作 early success / sibling pruning。

## 6. 插件契约

### 6.1 `PluginDescriptor`

`PluginDescriptor` 是插件冻结身份。字段包括：

- `plugin_id`
- `plugin_version`
- `supported_task_types`
- `input_contract`
- `output_contracts`
- `execution_contracts`
- `split_strategies`
- `validator_policy_id`
- `merge_policy_id`
- `metadata`
- `descriptor_digest`

descriptor digest 来自 canonical JSON body。`PluginRegistry.freeze()` 会保存 descriptor artifact，并写 registry snapshot。

### 6.2 `OutputContract`

`OutputContract` 声明某类输出需要哪些 output names、schema refs 和 raw output policy。raw output 可以允许保存，但是否权威由 contract 和插件 parser policy 决定。factorization 和 Lean 的 AI raw output 都不是权威输出。

### 6.3 `SplitStrategyContract`

`SplitStrategyContract` 声明：

- `split_strategy_id`
- params schema
- allowed child unit types
- child input / output contract refs
- validator policy id
- merge policy id
- durable subgoal policy
- candidate artifact policy
- max children limit

协议只接受 frozen descriptor 声明过的 split strategy。

## 7. Factorization 插件

### 7.1 目标

`factorization` 插件验证普通可拆分计算任务：把整数分解任务拆成 bounded candidate factor ranges，分别执行可重放的范围搜索，再由插件按有效 factor witness OR / 完整 no-factor coverage AND 的非对称策略合并结果。

当前第一切片支持：

- root `factor_integer`
- child `factor_search_range`
- merge `factorization_merge`
- candidate range partition
- range result parser
- deterministic range verifier
- verifier-gated factor-witness OR / no-factor-coverage AND merge
- prime / semiprime fixture E2E

当前第一切片明确不承诺：

- early success
- sibling pruning
- `one_success` merge
- composite cofactor 的完整递归 resolution

### 7.2 descriptor

`build_factorization_plugin_descriptor()` 声明：

- `PLUGIN_ID`
- `PLUGIN_VERSION`
- task types: `factor_integer`、`factor_search_range`、`factorization_merge`
- output contracts:
  - `factor_integer_subject`
  - `range_result`
  - `factorization_result`
- split strategy:
  - `candidate_range_partition.v1`
- validator policy:
  - range result validator
- merge policy:
  - all-required range merge
- AI parse policy:
  - raw output must be persisted
  - raw-only is not allowed
  - parsed `range_result` is required

### 7.3 root subject

root input 先被转成 `FactorIntegerSubject` canonical output。`verify_factor_integer_subject()` 会检查：

- subject `source_kind` 必须是 `root_input`。
- requested output 必须是 `prime_factorization_result`。
- `source_ref` 必须匹配 root input artifact ref。
- `target_n` 与 root input 一致。

### 7.4 range partition

`partition_candidate_ranges()`：

1. 规范化 `target_n`、`min_divisor`、`max_divisor`。
2. 默认 domain 是 `[2, floor_sqrt(target_n)]`。
3. 校验 `max_divisor <= floor_sqrt(target_n)`。
4. 根据 `requested_child_count`、`max_children_per_unit` 和 domain size 得到 `actual_child_count`。
5. 生成连续、非空、无 gap、无 overlap 的 `FactorSearchRangeInput`。
6. 生成 `CandidateRangeCoverageProof`。
7. 稳定记录 `params_digest`、`ranges_digest` 和 `coverage_id`。

该函数不写 event，不创建 `TaskUnit`，不调用 executor。

### 7.5 split plan

`build_factorization_split_strategy_result()`：

- 对 `target_n in {2, 3}` 返回 `complete`，生成直接 prime factorization result。
- 其他情况返回 `expand`，并通过 `build_factorization_split_plan()` 生成 proposal 和 merge plan。

`build_factorization_split_plan()`：

1. 要求完整 candidate domain `[2, floor_sqrt(target_n)]`。
2. 为每个 range 生成 child spec，unit type 是 `factor_search_range`。
3. 每个 child 需要 output `range_result`。
4. child capability 要求包括 `executor=mock_ai` 和 `bounded_factor_search=True`。
5. merge slots 与 ranges 一一对应，全部 required。
6. parent expected output 是 `prime_factorization_result`，resolution kind 是 `merge_plan_output`。
7. promotion guard 记录 coverage proof 和 no-freeform-thought 检查。

### 7.6 range execution 和 verification

`build_factor_search_instruction()` 生成 executor instruction，只包含 bounded range、target、schema、allowed result kinds 和 deterministic requirement。

`parse_range_result()` 只接受结构化 JSON object，不从自然语言抽取 factor。

`parse_factorization_ai_output()`：

- raw-only 或空输出返回 parse failure artifact body。
- 非 JSON object 返回 parse failure。
- 合法 `RangeResult` 返回 parsed artifact body 和 candidate output body。

`verify_range_result()`：

- 校验 schema envelope。
- 校验 child input 对齐：`target_n`、range bounds、`coverage_id`、`partition_params_digest`、`child_index`。
- 对 `found_factor` 重新检查范围、整除性和 cofactor。
- 对 `no_factor_in_range` 在 verifier budget 内 brute-force 检查不存在 divisor。

### 7.7 merge

factorization merge policy 当前为 `factorization.factor_witness_or_all_ranges.v2`。任一 `found_factor` 只有在 parser/verifier accepted、canonical、range/slot/contract 有效时才足以创建 merge task；其他 sibling 的 terminal failure 不推翻该 witness。没有有效 witness 时，必须等所有 required range child accepted/canonical 且 coverage 完整，才能生成 no-factor/prime 结论。merge 输出仍必须能形成 parent expected output resolution。本切片不做 sibling cancellation/pruning，已经产生的 sibling evidence 保留。

## 8. 真实 Lean proof 插件

### 8.1 目标

`lean_proof` 插件验证真实形式化证明任务。它不是 synthetic stub：

- theorem 必须是结构化 `LeanTheoremPayload`。
- proof candidate 必须是结构化 proof body artifact。
- checker 必须调用固定本地 Lean/lake/toolchain fixture project。
- checker stdout、stderr、generated source、proof artifact、checker report 和 `EnvironmentRef` 都要持久化。
- split 由 Lean-side helper / deterministic policy 输出 certificate，AI 不能决定 decomposition。
- merge policy 根据 split certificate 和 child proofs 生成 root proof，再用 Lean checker 重新检查。

### 8.2 Lean environment

`LeanEnvironmentManifest` 记录：

- fixture project root
- lean executable
- lake executable
- Lean / lake version
- toolchain file digest
- lakefile digest
- import set digest
- helper sources digest
- fixture profile digest
- resource limits
- environment digest

`build_lean_environment_ref()` 把 manifest 转成 executor `EnvironmentRef`。checker request 必须带 matching environment digest；不匹配会失败。

### 8.3 theorem payload

`LeanTheoremPayload` 结构化保存：

- theorem id / name
- imports
- namespace
- open namespaces
- options
- parameters source
- statement source
- theorem source / proof candidate ref
- library context
- decomposition policy
- resource limits
- payload digest

Python 不对 Lean 源码做自由文本语义拆分。拆分和 elaboration 检查由 Lean helper / subprocess bridge 完成。

### 8.4 checker

`check_lean_proof()`：

1. 校验 request environment digest 与 manifest 一致。
2. 读取 theorem payload artifact。
3. 读取 proof candidate artifact。
4. 校验 proof candidate schema、payload digest 和 proof candidate id。
5. 拒绝 `sorry` / `admit` 等 forbidden placeholder。
6. 渲染临时 Lean source。
7. 通过 `lake env lean <generated_source>` 调用本地 Lean。
8. 保存 generated source、stdout、stderr。
9. 若 accepted，保存 proof artifact。
10. 保存 checker report artifact。
11. 返回 `LeanCheckerReport`。

checker modes：

- `direct_proof`
- `child_proof`
- `merge_proof`

checker statuses：

- `accepted`
- `rejected`
- `timeout`
- `environment_error`
- `helper_error`

### 8.5 split helper

`run_lean_split_helper()`：

1. 校验 environment digest。
2. 读取 parent theorem payload。
3. 渲染 `TokenShareGeneratedSplit.lean`，调用 `TokenShare.Helper`。
4. 先 `lake build` helper project。
5. 调用 `lake env lean` 执行 helper source。
6. 从 stdout 提取 JSON certificate。
7. 保存 helper source、stdout、stderr、certificate、report artifacts。
8. 对 certificate 做 bridge checks：
   - rule 是否被 parent policy 允许。
   - merge rule 是否受支持。
   - parent goal 是否能 elaboration。
   - child context 和 payload digest 是否一致。

当前受支持的 merge rules：

- `lean_merge.conjunction_intro.v1`
- `lean_merge.iff_intro.v1`

当前 rule policy name 映射包括 conjunction、iff、implication intro、forall intro；但是否能创建完整 split plan 取决于 helper certificate、merge rule 和 fixture 支持。

### 8.6 Lean split plan

`build_lean_split_plan()`：

1. 拒绝 `executor_decomposition_authority_ref`，明确 AI output 不能定义 Lean decomposition。
2. 要求 split report 带 certificate artifact。
3. 拒绝 unsupported certificate。
4. 校验 certificate 与 parent policy。
5. 为每个 child goal 保存 `LeanChildTheoremPayload` artifact。
6. 生成 `DecompositionProposal`：
   - child unit type 是 `lean_proof_subgoal`
   - input binding 是 child theorem payload artifact ref
   - required output 是 proof artifact
   - required capability 是 deterministic local Lean checker
   - promotion guard 记录 split certificate ref/digest 和 rule id
7. 生成 `MergePlan`：
   - merge policy 是 verified Lean merge
   - required slots 对应 child proof artifacts
   - parent output mapping 指向 proof artifact
   - plugin payload 记录 merge skeleton digest 和 root merge proof checker requirement

### 8.7 proof candidate prompt / parser

Lean proof candidate parser 只接受结构化 proof candidate JSON。prompt builder 明确要求：

- exact `proof_candidate_id`
- proof body only
- 不要返回 import / namespace / theorem / markdown / prose
- proof source 不能包含 forbidden placeholder

raw output 必须持久化，但 raw-only 不能作为成功 proof output。

### 8.8 child proof 和 merge

child proof flow 会用 child theorem payload 和 proof candidate 调用 checker。`LeanChildProofResult` 只有在 checker accepted、context digest 匹配且 proof artifact 存在时才 merge-ready。

`merge_lean_child_proofs()`：

1. 校验 merge plan 使用 verified Lean merge policy。
2. required slots 必须全部提供且没有额外 slot。
3. child proof context、environment 和 checker report 必须匹配。
4. 根据 split certificate merge skeleton 生成 root proof candidate。
5. 用 `check_lean_proof()` 对 root theorem 重新检查。
6. accepted 时保存 `LeanMergeResult` artifact，其中记录 child proof refs、root checker report ref、root proof artifact ref 和 root proof digest。

## 9. 废弃和延后项

### 9.1 structured report stub

`structured_report_stub` 已从 Phase 6 开发计划剔除。它不能作为当前 paper experiment 的插件成功证据，也不应在新代码中作为 planned plugin 扩展。

### 9.2 lean_stub

`lean_stub` 只能作为历史 compatibility / deprecated fixture。真实 Lean proof plugin 必须使用 `lean_proof` 和本地 Lean checker evidence。

### 9.3 完整 replay / audit hardening

Phase 1-6 已实现 append-only event ledger、hash chain、SQLite rebuild 和 batch semantic checks。完整 Phase 9 replay / audit hardening 当前在 `feat-010` 中延后，不是开始最新真实 AI 论文实验前的必做项。

### 9.4 Phase 7+ 实验代码

Phase 7 AI API executor、Phase 8 experiment infrastructure 和 `feat-011` paper runner 不归入本文合并范围。它们可以复用 Phase 1-6 的协议边界，但实验设计和论文口径以最新实验设计文档为准。

## 10. 阅读入口

默认阅读顺序：

1. `AGENTS.md`
2. `Doc/agent-navigation.md`
3. `feature_list.json`
4. `progress.md`
5. `session-handoff.md`
6. 本文：Phase 1-6 协议完整说明
7. `Doc/TechnicalDocument/tokenshare_v1_code_map.md`：Phase 1-6 代码映射
8. 若涉及最新真实 AI 论文实验，再读 `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`

旧 Phase 1-6 文档已归档，不应作为新 agent 默认上下文。
