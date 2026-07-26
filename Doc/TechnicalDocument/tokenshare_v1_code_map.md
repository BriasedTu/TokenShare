# TokenShare V1 Code Map

日期：2026-07-23

状态：Phase 1-6 收敛后的默认 code map。本文以当前 `src/` 和 `tests/` 为准，映射 Phase 1-6 的实际实现。旧 Phase 1-6 code map 和字段规格已移动到 `Doc/TechnicalDocument/phase-1-6-archive/`，不再作为默认实现入口。

本文不把 Phase 7 AI API executor、Phase 8 experiment infrastructure 或 `feat-011` paper runner 作为 Phase 1-6 实现的一部分。相关目录在仓库中存在，但本 code map 只在“范围外但存在的代码”中说明，避免误读。

## 1. 顶层结构

```text
src/tokenshare/
  core/
  storage/
  plugins/
    factorization/
    lean_proof/
    lean_stub/
  executors/
  local_runtime/
  replay/
  experiments/
  protocol_engine.py

tests/
  core/
  storage/
  plugins/
    factorization/
    lean_proof/
    lean_stub/
  executors/
  local_runtime/
  experiments/
  replay/
  test_phase*_*.py
```

Phase 1-6 的核心代码在：

- `src/tokenshare/core/`
- `src/tokenshare/storage/`
- `src/tokenshare/plugins/contracts.py`
- `src/tokenshare/plugins/registry.py`
- `src/tokenshare/plugins/factorization/`
- `src/tokenshare/plugins/lean_proof/`
- `src/tokenshare/executors/contracts.py`
- `src/tokenshare/executors/registry.py`
- `src/tokenshare/executors/mock_ai.py`
- `src/tokenshare/executors/deterministic.py`
- `src/tokenshare/protocol_engine.py`

## 2. 协议对象和状态机

### `src/tokenshare/core/models.py`

职责：Phase 1-2 的基础协议对象和稳定 JSON 形状。

对象：

- `TaskState`
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
- `LeaseState`
  - `Active`
  - `Released`
  - `Expired`
  - `Revoked`
- `AttemptState`
  - `Created`
  - `Running`
  - `Submitted`
  - `Verifying`
  - `Verified`
  - `Canonical`
  - `Rejected`
  - `Failed`
  - `Superseded`
- `ArtifactRef`
- `ProtocolConfig`
- `TaskSpec`
- `TaskUnit`
- `TaskRelation`
- `ClientRecord`
- `Lease`
- `Attempt`

关键机制：

- 所有 dataclass 都通过 `to_dict()` 提供稳定 JSON body。
- `ArtifactRef.from_dict()` 用于从 event/artifact payload 重建引用。
- `ProtocolConfig.default()` 提供保守本地默认值。
- `TaskUnit.create_root()` 创建 root unit，初始状态为 `Ready`。
- `TaskUnit.state` 不承载 lease 或 verification 细节；lease 和 attempt 分别有独立状态线。

测试：

- `tests/core/test_phase1_models.py`
- `tests/test_phase1_root_registration.py`
- `tests/core/test_state_machines.py`

### `src/tokenshare/core/state_machines.py`

职责：纯状态机转换。

函数：

- `transition_task_unit()`
- `transition_lease()`
- `transition_attempt()`

关键机制：

- `TaskUnit` 允许 `Created -> Ready/Blocked`、`Blocked -> Ready`、`Ready -> Processing`、`Processing -> Ready/Failed/Completed` 和 cancellation。
- `Lease` 允许 `Active -> Active/Released/Expired/Revoked`。
- `Attempt` 允许 `Created -> Running/Superseded`、`Running -> Submitted/Failed/Superseded`、`Submitted -> Verified/Rejected/Failed`、`Verified -> Canonical`。
- `Running -> Submitted` 会写 raw/parsed/candidate/log artifact refs。
- `Submitted -> Rejected` 会记录 invalid output failure。
- `Submitted -> Failed` 用于 executor 已返回并记录 submission 后的失败恢复，保留 replay 连续的 `Running -> Submitted -> Failed` 链。
- `Verified -> Canonical` 只给 winner attempt 加 canonical metadata，不影响 loser attempts。

测试：

- `tests/core/test_state_machines.py`
- `tests/test_phase3_execution_flow.py`
- `tests/test_phase4_verification_flow.py`
- `tests/test_phase4_canonical_flow.py`

### `src/tokenshare/core/task_graph.py`

职责：可重建的任务图 view 和 ready 判断。

对象：

- `TaskGraph`

关键机制：

- 校验 unit key 与 `unit_id` 一致。
- 校验所有 unit 和 relation 属于同一 `task_id`。
- 校验 relation source / target unit 存在。
- 对 `depends_on_output` 强制 source output name 和 target input name。
- 禁止重复 target input binding。
- 校验 DAG 无环。
- `ready_unit_ids()` 只返回 state 为 `Ready` 且依赖 output 已 canonical 的 units。
- `canonical_outputs_by_unit_id` 可从 projection 注入，避免直接修改 historical `TaskUnit`。

测试：

- `tests/core/test_task_graph.py`
- `tests/test_phase2_scheduling_flow.py`
- `tests/test_phase4_expand_flow.py`

### `src/tokenshare/core/scheduling.py`

职责：纯调度决策。

对象：

- `SchedulingDecision`
- `Scheduler`

关键机制：

- FIFO ready queue 排序为 `(TaskUnit.created_at, unit_id)`。
- 如果 `ProtocolConfig.allow_shadow_execution=False`，已有 active lease 的 unit 不会再次调度。
- client status 接受 serialized Phase 3 status `Available`，也兼容 Phase 2 legacy `active`。
- required capabilities 必须被 client capabilities 覆盖。
- 输出 `SchedulingDecision`，不写 ledger。

测试：

- `tests/core/test_scheduler.py`
- `tests/test_phase2_scheduling_flow.py`

### `src/tokenshare/core/recovery.py`

职责：submission 接受性和通用 retry/recovery 的纯协议决策，不读写 storage，也不依赖 runtime/experiments。

对象：

- `SubmissionAcceptanceDecision`
- `RetryDecision`

函数：

- `evaluate_submission_acceptance()`
- `evaluate_retry()`

关键机制：

- submission 只有在 attempt=`Running`、lease=`Active`、task/unit/attempt/lease/fencing 全部一致且 `submitted_at <= expires_at` 时才 accepted。
- rejection reason 使用稳定字符串；拒绝决策本身不推进 attempt。
- retry rule 覆盖 `lease_expired`、`executor_error`、`parser_failure`、`verification_rejected`、`checker_rejected`、`no_return`，统一推导 retry allowed、TaskUnit next state、attempt terminal/superseded state、retry count 和 reason。
- `RetryDecision` 拒绝矛盾状态组合、bool/non-int count 和非 enum state；ProtocolEngine 还会按自身 `ProtocolConfig.max_retries` 重算 canonical decision。

测试：

- `tests/core/test_recovery.py`
- `tests/test_phase3_execution_flow.py`

### `src/tokenshare/core/leases.py`

职责：租约 claim、heartbeat、expiry 的纯规则。

对象：

- `LeaseClaim`
- `LeaseExpiryDecision`
- `LeaseManager`

关键机制：

- `claim()` 从 `SchedulingDecision` 创建 active `Lease`、created `Attempt` 和 running `Attempt`。
- `heartbeat()` 只允许 active lease 且 `now < expires_at`。
- `expire()` 只允许 active lease 且 `now >= expires_at`，要求 attempt 为 `Created` 或 `Running`，并先校验 lease/attempt/task unit 的 task、unit、attempt、lease 和 client 关联。
- expiry 会把 lease 置为 `Expired`，attempt 置为 `Superseded`。
- expiry 复用 `evaluate_retry(trigger="lease_expired")`，根据 retry count 与 `ProtocolConfig.max_retries` 推导 next task state：`Ready` 或 `Failed`。

测试：

- `tests/core/test_lease_manager.py`
- `tests/test_phase2_scheduling_flow.py`

### `src/tokenshare/core/registration.py`

职责：Phase 1 root task registration 兼容入口。

对象：

- `RootTaskRegistrationRequest`
- `RootTaskRegistrationResult`
- `RootTaskRegistrar`

关键机制：

- 保存 root input artifact。
- 创建 `TaskSpec`。
- 创建 root `TaskUnit`。
- 写 registration events。
- 不承担 Phase 2+ 调度、attempt、verification、merge 或 settlement 编排。

测试：

- `tests/test_phase1_root_registration.py`

## 3. Verification、Expansion、Merge、Contribution

### `src/tokenshare/core/verification.py`

职责：Phase 4 verification report 和 canonical selection 的纯规则。

对象：

- `VerificationReport`
- `CanonicalSelection`

函数：

- `build_verification_report()`
- `select_first_verified_bundle()`
- `digest_json()`

关键机制：

- required layers：
  - `schema_check`
  - `artifact_integrity_check`
  - `required_output_coverage_check`
  - `evidence_reference_check`
  - `plugin_domain_check`
  - `audit_check`
- allowed report statuses：
  - `passed`
  - `accepted`
  - `rejected`
  - `error`
- `eligible_for_canonical` 必须由 status 和 layer results 派生。
- `select_first_verified_bundle()` 按 verification event seq 最早者选择 canonical winner。

测试：

- `tests/core/test_phase4_models.py`
- `tests/test_phase4_verification_flow.py`
- `tests/test_phase4_canonical_flow.py`

### `src/tokenshare/core/expansion.py`

职责：Phase 4 split invocation、split result、decomposition proposal、expansion decision、merge plan 和 expected output ref 的纯对象规则。

对象：

- `SplitStrategyInvocation`
- `SplitStrategyResult`
- `DecompositionProposal`
- `ExpansionDecision`
- `MergePlan`
- `ExpectedOutputRef`

函数：

- `derive_child_initial_state()`
- `validate_decomposition_proposal_limits()`
- `digest_decomposition_proposal_body()`
- `digest_merge_plan_body()`

关键机制：

- `DecompositionProposal` 校验 header、child specs、dependency edges、expected outputs、merge slots 和 promotion guard。
- `ExpansionDecision` 校验 complete / expand evidence。
- `MergePlan` 校验 required slots、parent output mapping、hash requirements、validation requirements 和 plugin payload。
- `ExpectedOutputRef.from_expected_output()` 从 accepted proposal / expansion decision 派生 expected output identity。
- child 初始状态由协议根据 dependency / input binding 推导，插件不能直接指定权威 state。

测试：

- `tests/core/test_phase4_models.py`
- `tests/test_phase4_complete_flow.py`
- `tests/test_phase4_expand_flow.py`
- `tests/test_phase4_split_invocation_flow.py`

### `src/tokenshare/core/merge.py`

职责：Phase 5 merge 和 expected output resolution 的纯对象。

对象：

- `RequiredSlotBinding`
- `MergeTaskLink`
- `MergeRecord`
- `ExpectedOutputResolution`

函数：

- `digest_merge_task_link()`
- `digest_required_slot_bindings()`
- `digest_json()`

关键机制：

- `RequiredSlotBinding` 必须来自 `canonical_output`，且 canonical event seq 为正。
- `MergeTaskLink` v1 要求 `readiness_reason=all_required_slots_canonical`；v2 绑定版本化、领域无关的 readiness decision/digest，并要求实际 slot bindings 精确等于 decision 选中的 canonical unit 集。
- required slot bindings 会排序并 digest；重复 slot 会失败。
- `MergeRecord` 记录 merge unit canonical output 形成的 merge commitment。
- `ExpectedOutputResolution` v1 只支持 `resolution_source_type=merge_record`。

测试：

- `tests/core/test_phase5_models.py`
- `tests/test_phase5_merge_task_creation_flow.py`
- `tests/test_phase5_merge_resolution_flow.py`

### `src/tokenshare/core/merge_coordinator.py`

职责：当 required child slots 都 canonical 后创建 merge task。

对象：

- `BatchView`
- `MergeTaskCreationFlowResult`
- `MergeCoordinator`

函数：

- `MergeCoordinator.create_ready_merge_tasks()`

关键机制：

- 读取 visible `MERGE_PLAN_RECORDED` facts。
- 读取 child canonical output events。
- 构造 required slot bindings。
- 保存 merge input bundle artifact。
- 创建 merge `TaskUnit`。
- 可选创建 `TaskRelation(kind=merge_of)`。
- 以 `merge_task_creation_batch:{merge_plan_id}` 写入最终 marker `MERGE_TASK_LINK_RECORDED`。

测试：

- `tests/test_phase5_merge_task_creation_flow.py`

### `src/tokenshare/core/contribution.py`

职责：contribution、settlement、subtree pruning record 和 sandbox reward helper。

对象：

- `ContributionState`
- `ContributionRecord`
- `ContributionFlowResult`
- `ContributionCoordinator`
- `SettlementEntry`
- `SettlementRecord`
- `SubtreePruneRecord`

函数：

- `transition_contribution()`
- `build_sandbox_equal_weight_settlement_entries()`
- `digest_contribution()`
- `digest_settlement_entries()`
- `digest_json()`

关键机制：

- contribution 状态包括 `Eligible`、`Settled`、`Invalidated`。
- contribution 可以从 completion batch、expansion batch、merge resolution batch 派生。
- sandbox settlement entry 总额必须等于 root budget。
- subtree prune record 记录 cancelled units digest 和 policy source。

测试：

- `tests/core/test_phase5_models.py`
- `tests/test_phase5_contribution_settlement_flow.py`
- `tests/test_phase5_subtree_pruning_flow.py`

## 4. Storage

### `src/tokenshare/storage/artifacts.py`

职责：本地 artifact store。

对象：

- `ArtifactStore`

函数：

- `save_bytes()`
- `save_json()`
- `read_bytes()`
- `verify()`

关键机制：

- artifact body 写入本地 `artifacts/` 目录。
- manifest 单独保存。
- `ArtifactRef.content_hash` 使用 sha256。
- `verify()` 通过 ref 校验本地内容 hash。

测试：

- `tests/storage/test_artifact_store.py`
- 多个 plugin / protocol fixture flow tests。

### `src/tokenshare/storage/events.py`

职责：append-only JSONL event ledger。

对象：

- `EventType`
- `LedgerEvent`
- `EventDraft`
- `EventLedger`

函数：

- `EventLedger.append()`
- `EventLedger.append_batch()`
- `EventLedger.read_all()`
- `EventLedger.verify_hash_chain()`
- `utc_now()`

关键机制：

- `LedgerEvent.v2` includes batch fields。
- event hash 排除 `event_hash` 自身。
- `append()` 对同 idempotency key 做 payload signature dedupe，冲突时报错。
- `append_batch()` 提供连续 event seq、batch envelope 和 whole-batch idempotency。
- v1 event 仍可读取和 hash 验证。
- `EXECUTION_SUBMISSION_RECORD_SCHEMA_V1/V2` 固定 submission record payload 版本；当前 engine 写 v2，历史 v1 ledger 仍可读取。

测试：

- `tests/storage/test_event_ledger.py`
- `tests/storage/test_phase4_event_ledger_batch.py`

### `src/tokenshare/storage/sqlite_index.py`

职责：从 events 重建 SQLite materialized indexes。

对象：

- `SQLiteMaterializedIndex`

主要表：

```text
ledger_events
task_specs
task_units
task_relations
artifact_refs
client_records
leases
attempts
recovery_actions
registry_snapshots
execution_requests
execution_submissions
executor_statuses
verification_reports
canonical_outputs
split_strategy_invocations
decomposition_proposals
expansion_decisions
merge_plans
expected_output_refs
merge_task_links
merge_slot_bindings
merge_records
expected_output_resolutions
contributions
settlement_records
settlement_entries
subtree_prunes
```

关键机制：

- `rebuild_from_events()` 清空并重建所有表。
- `canonical_outputs` 对 `(task_id, unit_id)` 有唯一索引。
- `expansion_decisions` 对 `expansion_scope_hash` 有唯一索引。
- `_build_phase4_projection_context()` 校验 completion / expansion batch。
- `_build_phase5_projection_context()` 校验 merge task creation、merge resolution、parent completion、settlement 和 subtree pruning batch。
- expected output refs 只在 final `TASK_EXPANDED` 后可见。
- Phase 5 expected output refs 会根据 resolution context 更新 status。
- `execution_submissions` 投影 v2 `acceptance_status` / `rejection_reason`；历史 v1 payload 对应列保持 `NULL`。

测试：

- `tests/storage/test_sqlite_index.py`
- `tests/storage/test_phase2_event_projection.py`
- `tests/storage/test_phase3_event_projection.py`
- `tests/storage/test_phase4_event_projection.py`
- `tests/storage/test_phase5_event_projection.py`

## 5. Plugin and Executor Contracts

### `src/tokenshare/plugins/contracts.py`

职责：插件 descriptor 契约。

对象：

- `OutputContract`
- `SplitStrategyContract`
- `PluginDescriptor`

关键机制：

- `PluginDescriptor.descriptor_digest()` 从 canonical JSON 计算 digest。
- descriptor body 包括 input/output/execution/split/validator/merge metadata。
- `SplitStrategyContract` 明确 durable subgoal policy 和 candidate artifact policy。

测试：

- `tests/plugins/test_plugin_registry.py`
- `tests/plugins/factorization/test_factorization_registry.py`
- `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py`

### `src/tokenshare/plugins/registry.py`

职责：插件注册和 registry snapshot。

对象：

- `RegistrySnapshot`
- `PluginRegistry`

关键机制：

- `register()` 拒绝 freeze 后修改。
- exclusive task types 不允许被多个 plugin 声明。
- `freeze()` 将 plugin descriptor 保存为 artifact，并输出 registry snapshot。

测试：

- `tests/plugins/test_plugin_registry.py`

### `src/tokenshare/executors/contracts.py`

职责：Phase 3 execution contract。

对象：

- `ExecutorStatus`
- `EnvironmentRef`
- `ExecutorDescriptor`
- `PromptPackage`
- `ExecutionRequest`
- `ExecutionSubmission`

关键机制：

- `ExecutorDescriptor.descriptor_digest()` 从 canonical JSON 计算。
- `ExecutionRequest` 和 `ExecutionSubmission` 都是 artifact-backed body。
- `PromptPackage` 由插件构建，executor 不拥有 prompt schema。
- `EnvironmentRef` 用于固定 runtime / tool versions / resource limits。

测试：

- `tests/executors/test_executor_registry.py`
- `tests/executors/test_mock_ai_executor.py`
- `tests/executors/test_deterministic_executor.py`
- `tests/test_phase3_execution_flow.py`

### `src/tokenshare/executors/registry.py`

职责：执行器 registry、capability matching 和 freeze entries。

对象：

- `ExecutorRegistry`

关键机制：

- `register()` 注册 descriptor。
- `match_available()` 返回符合 hard requirements 和 status 的 descriptor。
- `no_match_reasons()` 给出不可匹配原因。
- `freeze_entries()` 保存 executor descriptor artifact。

测试：

- `tests/executors/test_executor_registry.py`

### `src/tokenshare/executors/mock_ai.py`

职责：本地 mock AI executor。

对象：

- `MockAIExecutorProfile`
- `MockAIExecutor`

关键机制：

- `execute()` 按 profile 返回 deterministic raw/parsed/candidate output refs。
- 用于 Phase 3/6 测试和 factorization fixture，不代表真实 AI API。

测试：

- `tests/executors/test_mock_ai_executor.py`

### `src/tokenshare/executors/deterministic.py`

职责：本地 deterministic executor wrapper。

对象：

- `DeterministicLocalExecutor`

关键机制：

- 包装 Python callable。
- 生成 `ExecutionSubmission`。
- 用于可重放的本地程序执行。

测试：

- `tests/executors/test_deterministic_executor.py`

## 6. Protocol Engine

### `src/tokenshare/protocol_engine.py`

职责：Phase 2-5 的 storage-writing application service。

Flow result 对象：

- `SchedulingFlowResult`
- `LeaseExpiryFlowResult`
- `LeaseHeartbeatFlowResult`
- `RegistrySnapshotFlowResult`
- `ExecutionRequestFlowResult`
- `ExecutionSubmissionFlowResult`
- `RecoveryFlowResult`
- `VerificationFlowResult`
- `CanonicalBindingFlowResult`
- `SplitStrategyInvocationFlowResult`
- `CompleteDecisionFlowResult`
- `ExpandDecisionFlowResult`
- `MergeResolutionFlowResult`
- `ParentCompletionFlowResult`
- `SettlementFlowResult`
- `SubtreePruningFlowResult`

公开方法：

- `record_registry_snapshot()`
- `record_execution_request()`
- `record_execution_submission()`
- `record_recovery_decision()`
- `record_verification()`
- `bind_canonical_outputs()`
- `record_split_strategy_invocation()`
- `record_complete_decision()`
- `record_expand_decision()`
- `record_merge_resolution()`
- `record_parent_completion()`
- `record_root_settlement()`
- `record_subtree_pruning()`
- `schedule_ready_unit()`
- `record_lease_heartbeat()`
- `record_lease_expiry()`

关键 event 写入：

- registry snapshot：`REGISTRY_SNAPSHOT_RECORDED`
- request：`EXECUTION_REQUEST_RECORDED`
- submission：`record_execution_submission()` 在 storage-writing boundary 从同一次 ledger read 恢复最新 `Attempt`/`Lease`，再调用 core 的纯 `evaluate_submission_acceptance()`；`EXECUTION_SUBMISSION_RECORDED` v2 始终显式记录 accepted/rejected 及稳定 reason，被拒绝的 stale submission 保留 artifact/event 但不推进 attempt，只有 accepted 才再写 `ATTEMPT_STATE_CHANGED Running -> Submitted`
- generic recovery：`recovery_batch:{recovery_action_id}` 原子写 terminal lease、按需写 attempt terminal/superseded、`RECOVERY_ACTION_RECORDED` 和 TaskUnit `Ready/Failed`；engine 使用自身 config 复核 retry decision。新 batch 必须绑定 ledger 最新 lease/attempt snapshot；已权威记录的匹配 terminal attempt 不重复写状态事件；exact duplicate 仍由完整 batch 签名保持幂等
- verification：`VERIFICATION_RECORDED`，根据 status 写 attempt state change
- canonical：`CANONICAL_OUTPUTS_BOUND` 和 winner attempt `Verified -> Canonical`
- split invocation：`SPLIT_STRATEGY_INVOCATION_RECORDED`
- complete：`completion_batch:{expansion_decision_id}`
- expand：`expansion_batch:{expansion_decision_id}`
- merge resolution：`merge_resolution_batch:{merge_plan_id}`
- parent completion：`parent_completion_batch:{parent_unit_id}`
- root settlement：`settlement_batch:{task_id}`
- subtree pruning：`subtree_pruning_batch:*`
- scheduling：`LEASE_STATE_CHANGED`、`ATTEMPT_STATE_CHANGED`、`TASK_UNIT_STATE_CHANGED`
- heartbeat：`record_lease_heartbeat()` 先从 ledger 恢复最新 lease；latest Active heartbeat 从最新 count/expiry 连续推进，Released/Expired 等 terminal lease 的 stale heartbeat 在纯 `LeaseManager` 规则处拒绝且不写新 event
- lease expiry：`record_lease_expiry()` 委托 generic recovery，generic recovery 统一调用 `LeaseManager.expire()` 校验 deadline，并在同一个 recovery batch 写 `LEASE_STATE_CHANGED Active -> Expired`、attempt superseded、recovery action 和 TaskUnit 结论

测试：

- `tests/test_phase2_scheduling_flow.py`
- `tests/test_phase3_execution_flow.py`
- `tests/test_phase4_verification_flow.py`
- `tests/test_phase4_canonical_flow.py`
- `tests/test_phase4_split_invocation_flow.py`
- `tests/test_phase4_complete_flow.py`
- `tests/test_phase4_expand_flow.py`
- `tests/test_phase5_merge_resolution_flow.py`
- `tests/test_phase5_contribution_settlement_flow.py`
- `tests/test_phase5_subtree_pruning_flow.py`

## 7. Factorization Plugin

### `src/tokenshare/plugins/factorization/schemas.py`

职责：factorization schema ids、versions、plugin constants 和 `schema_ref()`。

被使用于：

- descriptor
- models
- split strategy
- prompt builder
- validator
- fixture flow

测试：

- `tests/plugins/factorization/test_factorization_schemas.py`

### `src/tokenshare/plugins/factorization/models.py`

职责：factorization typed payloads。

主要对象：

- `RootInput`
- `FactorIntegerSubject`
- `CandidateRangePartitionParams`
- `CandidateRangeCoverageProof`
- `FactorSearchRangeInput`
- `FactorSearchInstruction`
- `RangeResult`
- `PrimeFactor`
- `PrimeFactorizationResult`

关键机制：

- decimal integer string normalization。
- digest fields 由 canonical JSON 派生。
- range input 记录 coverage id、child index/count、partition params digest。
- result schema 明确 `found_factor` 与 `no_factor_in_range` 两类。

测试：

- `tests/plugins/factorization/test_factorization_schemas.py`
- `tests/plugins/factorization/test_candidate_range_partition.py`

### `src/tokenshare/plugins/factorization/descriptor.py`

职责：构造 factorization plugin descriptor。

公开函数：

- `build_factorization_plugin_descriptor()`

声明：

- task types: `factor_integer`、`factor_search_range`、`factorization_merge`
- output contracts: `factor_integer_subject`、`range_result`、`factorization_result`
- split strategy: `candidate_range_partition`
- validator policy: range result validator
- merge policy: all-required range merge
- AI parse policy: raw persisted, parsed required, raw-only rejected

测试：

- `tests/plugins/factorization/test_factorization_registry.py`

### `src/tokenshare/plugins/factorization/split_strategy.py`

职责：candidate range partition 和 proposal / merge plan 生成。

对象：

- `CandidateRangePartitionResult`
- `FactorizationSplitPlanResult`
- `FactorizationSplitStrategyActionResult`

函数：

- `partition_candidate_ranges()`
- `build_factorization_split_strategy_result()`
- `build_factorization_split_plan()`

关键机制：

- partition domain 默认 `[2, floor_sqrt(target_n)]`。
- ranges 连续、非空、无 gap、无 overlap。
- `target_n in {2, 3}` 可以 direct complete。
- 其他情况生成 Phase 4 `DecompositionProposal` 和 `MergePlan`。
- 子任务全部是 `factor_search_range`。
- merge slots 全部 required。
- 不写 ledger，不调用 `ProtocolEngine`。

测试：

- `tests/plugins/factorization/test_candidate_range_partition.py`
- `tests/plugins/factorization/test_factorization_split_strategy.py`
- `tests/test_phase6_factorization_flow.py`
- `tests/test_phase6_factorization_limitations.py`

### `src/tokenshare/plugins/factorization/validator.py`

职责：range result parser 和 deterministic verifier。

对象：

- `RangeVerificationResult`
- `FactorizationAIParseResult`

函数：

- `parse_range_result()`
- `parse_factorization_ai_output()`
- `build_factor_search_instruction()`
- `verify_range_result()`
- `verify_factor_integer_subject()`

关键机制：

- raw-only 不允许作为 successful output。
- parser 只接受 structured JSON object。
- parser 在构造 `RangeResult` 前显式要求完整 schema 字段，禁止数据类默认值静默补齐缺失 `schema_version`。
- verifier 重新检查 child input alignment。
- `found_factor` 必须在 range 内并整除 target。
- `no_factor_in_range` 会在 budget 内 brute-force recheck。

测试：

- `tests/plugins/factorization/test_factorization_parser.py`
- `tests/plugins/factorization/test_factorization_verifier.py`
- `tests/plugins/factorization/test_factorization_ai_parse_policy.py`

### `src/tokenshare/plugins/factorization/prompt_builder.py`

职责：构造 factorization AI prompt package 和 parse policy bridge。

关键机制：

- prompt package 由插件拥有。
- prompt 包含 exact protocol-bound JSON field values。
- constraints 同时固定 `strict_json_only=true` 与 `requires_json_mode=true`，防止真实 Factorization 请求退化到普通文本模式。
- executor 不拥有 output schema。

测试：

- `tests/plugins/factorization/test_factorization_prompt_package.py`
- `tests/plugins/factorization/test_factorization_ai_parse_policy.py`

### `src/tokenshare/plugins/factorization/merge_policy.py`

职责：factorization witness-OR / no-factor-AND merge policy。

关键机制：

- parser/verifier accepted 且 canonical 的有效 factor witness 可以单 slot 进入 merge。
- 没有有效 witness 时，所有 required range slots 必须 accepted/canonical 且 coverage 完整，才能生成 no-factor/prime 结论。
- merge validator 重新检查 coverage、range result schema 和 factor correctness。
- 不做 sibling cancellation/pruning；已经启动或完成的 sibling evidence 保留。

测试：

- `tests/plugins/factorization/test_factorization_merge_policy.py`
- `tests/test_phase6_factorization_flow.py`

### `src/tokenshare/plugins/factorization/fixtures.py`

职责：factorization 端到端 fixture flow。

对象：

- `FixtureSubmission`
- `FixtureRangeExecution`
- `FactorizationFixtureFlowResult`

公开函数：

- `run_factorization_fixture_flow()`

关键机制：

- 组装 registry、root task、subject canonical、split / expand、range child execution、verification、canonical binding、merge task creation、merge resolution、parent completion 和 settlement。
- 作为 Phase 6 PoC 验证，不是协议核心。

测试：

- `tests/test_phase6_factorization_flow.py`
- `tests/test_phase6_factorization_limitations.py`

## 8. Lean Proof Plugin

### `src/tokenshare/plugins/lean_proof/schemas.py`

职责：Lean plugin schema ids、versions、plugin constants 和 `schema_ref()`。

测试：

- `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py`

### `src/tokenshare/plugins/lean_proof/models.py`

职责：Lean structured payloads。

对象：

- `LeanTheoremPayload`
- `LeanFixtureManifest`
- `LeanSplitCertificate`

函数：

- `canonical_json_digest()`

关键机制：

- theorem payload 记录 imports、namespace、open namespaces、options、parameters source、statement source、library context、decomposition policy、resource limits。
- split certificate 记录 parent payload ref、rule id、split kind、child goals、merge skeleton 和 digest。
- digest 由 canonical JSON 派生。

测试：

- `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py`
- `tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`
- `tests/plugins/lean_proof/test_lean_split_helper.py`

### `src/tokenshare/plugins/lean_proof/environment.py`

职责：Lean toolchain manifest 和 `EnvironmentRef` mapping。

对象：

- `LeanEnvironmentManifest`

函数：

- `LeanEnvironmentManifest.from_project()`
- `build_lean_environment_ref()`

关键机制：

- manifest 记录 Lean/lake executable、version、toolchain digest、lakefile digest、helper source digest、import set digest 和 resource limits。
- environment digest 必须与 checker/split request 的 `EnvironmentRef` 匹配。

测试：

- `tests/plugins/lean_proof/test_lean_environment.py`
- `tests/plugins/lean_proof/test_lean_preflight.py`

### `src/tokenshare/plugins/lean_proof/descriptor.py`

职责：构造真实 Lean proof plugin descriptor。

公开函数：

- `build_lean_proof_plugin_descriptor()`

声明：

- task types: `lean_theorem`、`lean_proof_subgoal`、`lean_proof_merge`
- output contracts:
  - theorem payload
  - proof artifact
  - merge result
- execution contracts:
  - deterministic Lean checker
  - Lean helper split
  - mock AI proof candidate
  - AI API proof candidate
- split strategy:
  - deterministic tactic split
- validator policy:
  - checker validator
- merge policy:
  - verified Lean merge
- metadata:
  - real checker required
  - lean stub not accepted
  - AI cannot decide decomposition
  - environment ref and checker logs required

测试：

- `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py`

### `src/tokenshare/plugins/lean_proof/checker.py`

职责：Lean proof checker subprocess bridge。

对象：

- `LeanChecker` callable protocol
- `LeanCheckerMode`
- `LeanCheckerStatus`
- `LeanCheckerRequest`
- `LeanCheckerReport`

函数：

- `check_lean_proof()`
- `render_lean_source()`
- `prepared_lean_environment()`

关键机制：

- 校验 request environment digest。
- 读取 theorem payload artifact 和 proof candidate artifact。
- 渲染 Lean source。
- 每个 environment digest 首次通过 `lake env` 捕获 allowlist 环境，后续直接调用 manifest 固定的 `lean.exe`；缓存不保存任意进程变量或 secret。
- 保存 generated source、stdout、stderr。
- accepted 时保存 proof artifact。
- 保存 checker report artifact。
- 拒绝 `sorry` / `admit`。

测试：

- `tests/plugins/lean_proof/test_lean_checker_direct.py`
- `tests/plugins/lean_proof/test_lean_checker_environment_cache.py`
- `tests/plugins/lean_proof/test_lean_checker_injection.py`
- `tests/plugins/lean_proof/test_lean_child_proof_flow.py`
- `tests/plugins/lean_proof/test_lean_merge_policy.py`

### `src/tokenshare/plugins/lean_proof/split_strategy.py`

职责：Lean split helper subprocess bridge 和 Phase 4 proposal / merge plan mapping。

对象：

- `LeanSplitHelperStatus`
- `LeanSplitHelperRequest`
- `LeanSplitHelperReport`
- `LeanSplitPlanResult`

函数：

- `run_lean_split_helper()`
- `build_lean_split_plan()`

关键机制：

- split helper 调用 fixture project 的 `TokenShare.Helper`。
- 从 stdout 提取 JSON split certificate。
- 保存 helper source、stdout、stderr、certificate、report artifacts。
- bridge checks 校验 rule policy、merge rule、parent elaboration 和 child payload digest。
- `build_lean_split_plan()` 拒绝 AI decomposition authority。
- child unit type 是 `lean_proof_subgoal`。
- merge policy 是 verified Lean merge。

测试：

- `tests/plugins/lean_proof/test_lean_split_helper.py`
- `tests/plugins/lean_proof/test_lean_split_strategy.py`
- `tests/test_phase6_lean_proof_flow.py`

### `src/tokenshare/plugins/lean_proof/child_proof.py`

职责：Lean child proof candidate 到 checker result 的桥接。

关键机制：

- 用 child theorem payload 和 proof candidate 调用 checker。
- accepted checker report、context digest 和 proof artifact 是 merge-ready 前提。

测试：

- `tests/plugins/lean_proof/test_lean_child_proof_flow.py`

### `src/tokenshare/plugins/lean_proof/merge_policy.py`

职责：Lean child proofs 到 parent/root proof 的 verified merge。

对象：

- `LeanProofMergeInput`
- `LeanProofMergeResult`

函数：

- `merge_lean_child_proofs()`

关键机制：

- required slots 必须 exact match。
- child proof 必须 accepted、merge-ready、environment match。
- 根据 split certificate merge skeleton 生成 root proof candidate。
- root proof 再次走 `check_lean_proof()`。
- accepted 时保存 `LeanMergeResult` artifact。

测试：

- `tests/plugins/lean_proof/test_lean_merge_policy.py`
- `tests/test_phase6_lean_proof_flow.py`

### `src/tokenshare/plugins/lean_proof/prompt_builder.py`

职责：Lean proof candidate prompt package 和 parser policy。

对象：

- `LeanProofCandidateAIParseResult`

函数：

- `build_lean_proof_candidate_prompt_package()`
- `parse_lean_proof_candidate_ai_output()`

关键机制：

- prompt 要求 exact proof candidate id。
- AI 只能返回 proof body JSON，不能返回 theorem/import/namespace/markdown/prose。
- parser 拒绝 placeholder 和 schema mismatch。
- raw output 必须持久化，但 raw-only 不能作为 success。

测试：

- `tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py`

### `src/tokenshare/plugins/lean_proof/validator.py`

职责：Lean checker report 到 Phase 4 verification layer summary 的映射。

对象：

- `LeanValidationResult`

函数：

- `verify_lean_checker_report()`

关键机制：

- accepted checker report 才映射为 passed plugin domain check。
- checker report 必须带 evidence refs。
- rejected / timeout / environment error 会转成 rejected layer summary。

测试：

- `tests/plugins/lean_proof/test_lean_validator.py`

### `src/tokenshare/plugins/lean_proof/preflight.py`

职责：Lean fixture project preflight。

对象：

- `LeanPreflightStatus`
- `LeanPreflightResult`

函数：

- `run_lean_preflight()`

关键机制：

- 检查 fixture project files。
- 检查 toolchain / lake project 可用性。
- 输出 structured preflight result。

测试：

- `tests/plugins/lean_proof/test_lean_preflight.py`

### `src/tokenshare/plugins/lean_proof/replay_evidence.py`

职责：Lean replay evidence guard。

对象：

- `LeanReplayEvidenceError`
- `LeanReplayEvidenceResult`

函数：

- `verify_lean_replay_evidence()`

关键机制：

- replay 时不重新运行 Lean checker。
- 校验历史 checker report / proof artifact / stdout / stderr / generated source refs 是否存在且 hash 匹配。

测试：

- `tests/plugins/lean_proof/test_lean_replay_evidence.py`

### `src/tokenshare/plugins/lean_proof/fixtures.py`

职责：Lean direct proof 和 decomposition/merge 协议 fixture flow。

关键机制：

- 创建 Lean environment manifest。
- 注册 plugin / executor。
- 保存 theorem payload。
- 运行 direct checker 或 split helper。
- 运行 child proof checker。
- 运行 merge policy root recheck。
- 将 evidence 接入 ProtocolEngine flow。

测试：

- `tests/test_phase6_lean_proof_flow.py`
- `tests/plugins/lean_proof/*`

### `src/tokenshare/experiments/lean_catalog_audit.py`

职责：把普通 Lean paper catalog 加载与真实 checker preflight 分离，维护内容寻址、可增量复用的 600-entry audit evidence。

关键机制：

- tracked manifest 位于 `benchmarks/paper/lean_checker_preflight.v1.json`；普通 `load_paper_catalogs()` 只验证 manifest 自摘要、完整 coverage、entry key、environment/checker digest 和 accepted 状态，不隐式调用 Lean。
- 每个 direct case / lemma-graph node 的 key 覆盖 generated source、oracle proof、environment、checker implementation、mode 和 resource limits。
- `--verify` 默认只重检失效 entry；`--refresh` 完整成功后原子替换 manifest；`--force-all` 忽略 entry cache。
- local cache 位于被 gitignore 的 `local/cache/lean_checker/`；所有 audit 路径 `provider_calls_made=0`。
- checker/entry rendering/toolchain/helper 变化全量失效；child/merge/runtime 调用链变化使用 spy、定向 assembly 测试和固定真实 canary，不重复无关 catalog entry。

测试：

- `tests/experiments/test_lean_catalog_audit.py`
- `tests/experiments/test_lean_catalog_audit_cli.py`
- `tests/experiments/test_paper_catalog.py`

## 9. Deprecated Compatibility

### `src/tokenshare/plugins/lean_stub/`

职责：历史占位 / compatibility package。

边界：

- 不能作为真实 Lean proof 成功证据。
- 不能作为 paper experiment passing evidence。
- 不应扩展为新 Phase 6 目标。

## 10. Tests by Phase

Startup / package layout：

- `tests/test_package_layout.py`
- `tests/local_runtime/test_runtime_boundaries.py`
- `tests/test_init_verification_profiles.py`
- `verification/run_verification.py`
- `verification/fast-tests.txt`
- `verification/lean-canary-tests.txt`
- `init.ps1`
- `init.sh`

`verification/run_verification.py` 是 PowerShell 与 Bash 共用的单一 Python 入口；两个 wrapper 只执行一次 `conda run`。`verification/fast-tests.txt` 是默认快速测试清单；`verification/lean-canary-tests.txt` 是与 catalog 总量无关的固定 Lean subprocess/contract/matrix bundle；`init.ps1 -Full` / `init.sh --full` 执行完整 `pytest tests`，但不默认重跑 600-entry audit。验证模式契约测试负责防止清单缺失、重复、指向不存在路径、wrapper 漂移或 LeanAudit 参数丢失。

Phase 1：

- `tests/core/test_phase1_models.py`
- `tests/test_phase1_root_registration.py`
- `tests/storage/test_artifact_store.py`
- `tests/storage/test_event_ledger.py`
- `tests/storage/test_sqlite_index.py`

Phase 2：

- `tests/phase2_fixtures.py`
- `tests/core/test_task_graph.py`
- `tests/core/test_scheduler.py`
- `tests/core/test_lease_manager.py`
- `tests/core/test_recovery.py`
- `tests/core/test_state_machines.py`
- `tests/test_phase2_scheduling_flow.py`
- `tests/storage/test_phase2_event_projection.py`

Phase 3：

- `tests/phase3_fixtures.py`
- `tests/plugins/test_plugin_registry.py`
- `tests/executors/test_executor_registry.py`
- `tests/executors/test_mock_ai_executor.py`
- `tests/executors/test_deterministic_executor.py`
- `tests/test_phase3_execution_flow.py`
- `tests/storage/test_phase3_event_projection.py`

Phase 4：

- `tests/core/test_phase4_models.py`
- `tests/storage/test_phase4_event_ledger_batch.py`
- `tests/test_phase4_verification_flow.py`
- `tests/test_phase4_canonical_flow.py`
- `tests/test_phase4_split_invocation_flow.py`
- `tests/test_phase4_complete_flow.py`
- `tests/test_phase4_expand_flow.py`
- `tests/storage/test_phase4_event_projection.py`

Phase 5：

- `tests/phase5_fixtures.py`
- `tests/core/test_phase5_models.py`
- `tests/test_phase5_merge_task_creation_flow.py`
- `tests/test_phase5_merge_resolution_flow.py`
- `tests/test_phase5_contribution_settlement_flow.py`
- `tests/test_phase5_subtree_pruning_flow.py`
- `tests/storage/test_phase5_event_projection.py`

Phase 6 factorization：

- `tests/plugins/factorization/test_factorization_schemas.py`
- `tests/plugins/factorization/test_factorization_registry.py`
- `tests/plugins/factorization/test_candidate_range_partition.py`
- `tests/plugins/factorization/test_factorization_split_strategy.py`
- `tests/plugins/factorization/test_factorization_parser.py`
- `tests/plugins/factorization/test_factorization_verifier.py`
- `tests/plugins/factorization/test_factorization_prompt_package.py`
- `tests/plugins/factorization/test_factorization_ai_parse_policy.py`
- `tests/plugins/factorization/test_factorization_merge_policy.py`
- `tests/test_phase6_factorization_flow.py`
- `tests/test_phase6_factorization_limitations.py`

Phase 6 Lean proof：

- `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py`
- `tests/plugins/lean_proof/test_lean_environment.py`
- `tests/plugins/lean_proof/test_lean_fixture_project_manifest.py`
- `tests/plugins/lean_proof/test_lean_checker_direct.py`
- `tests/plugins/lean_proof/test_lean_validator.py`
- `tests/plugins/lean_proof/test_lean_split_helper.py`
- `tests/plugins/lean_proof/test_lean_split_strategy.py`
- `tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py`
- `tests/plugins/lean_proof/test_lean_child_proof_flow.py`
- `tests/plugins/lean_proof/test_lean_merge_policy.py`
- `tests/plugins/lean_proof/test_lean_preflight.py`
- `tests/plugins/lean_proof/test_lean_replay_evidence.py`
- `tests/test_phase6_lean_proof_flow.py`

## 11. 范围外但当前存在的代码

以下代码在仓库中存在，但不属于本 Phase 1-6 code map 的权威范围：

Feat-011 system runtime 迁移 Task 1-9 与 Task 10 收口实现：

- `src/tokenshare/local_runtime/contracts.py`
  - 冻结本地应用协调层接口：`ProtocolRunRequest`、`ProtocolExecutionScope`、`ProtocolRunResult`、`ProtocolTaskPluginRuntime`、`MergeReadinessContext`、`MergeReadinessDecision`、`RuntimeHooks`、`NoOpRuntimeHooks`、`ProtocolMechanismPolicy`、`WorkerBackend`，以及 root/complete/expand/raw/parser/verification/recovery/merge/progress typed action/context/directive contracts。
  - `NoOpRuntimeHooks` 与全开启 mechanism policy 表示 FULL 默认语义；Task 7 起，非 FULL policy 在 parser、verification、replacement、merge gate、slot integrity 五个稳定 gate 中精确关闭一个机制。
  - `continue_after_terminal_child_failure` 默认 `false`；只有 Task 4 Factorization FULL 显式启用它来收集 sibling failure evidence，不能作为通用 runtime 的默认恢复策略。
  - runtime contract 可以依赖 core、storage 和 executor contract，但不得导入 experiment schema。
- `src/tokenshare/local_runtime/workers.py`
  - `SequentialWorkerBackend` 保持调用线程内 `capacity=1` 基线；`ThreadWorkerBackend` 以固定线程容量并发同一 root 的已调度 units；`ProcessWorkerBackend` 让每个 request 在独立 OS process 中执行，并可在 submission 交给 coordinator 前终止一次预注册目标 process。
  - backend 只记录含 unit/attempt/lease、worker/thread/process identity、pid/exitcode 和真实时间区间的 `WorkerExecutionFact`；`worker_terminated` 只是执行事实，不决定 lease expiry、retry 或 requeue。
  - submission identity 从 attempt identity 派生 Windows-safe digest；无 attempt identity 的最小 test fixture 才使用 backend-local ordinal fallback。
- `src/tokenshare/local_runtime/coordinator.py`
  - `ProtocolRunCoordinator.run_root()` 连接 root registration、registry snapshot、`ProtocolEngine.schedule_ready_unit()`、execution request/submission、verification、canonical、split/expand/complete、`MergeCoordinator`、merge resolution、parent completion、`ContributionCoordinator` 和 root settlement。
  - replacement 仍由 scheduler/lease 创建；coordinator 不调用 `transition_task_unit()`，也不直接 append protocol event。
  - FULL 机制仍是默认边界；Task 6 起，coordinator 会在 backend capacity 内逐个调用 engine scheduler，让每个 ready unit 先取得独立 lease/attempt/request，再批量执行。多容量 backend 必须显式实现 batch 边界；`worker_terminated` 在真实 lease deadline 进入 `record_lease_expiry()`，由 engine 写 `Expired`、`Superseded`、TaskUnit `Ready`，replacement 仍只由后续 scheduler/lease 创建。
  - Task 5 起 coordinator 会查询 graph 中依赖已具备 canonical output 的 Blocked unit，并调用 engine authority 记录 `Blocked -> Ready` 后再调度。其它非终态停滞仍 fail-closed。terminal child failure 默认立即抛出；若 plugin 显式继续 sibling，最终仍调用 engine authority 写 parent/root Failed。
  - 调度后必须使用 scheduler 实际选中的 unit 构造 attempt/request，不能假设它等于 ready list 首项；该约束支持多层 Lean DAG。
  - `ProtocolExecutionScope` 只限制 scheduler 可执行的已规划 unit，不改变插件 split/graph；partial run 保留所选 unit 的 engine ledger evidence，但不创建 root merge/completion/settlement，也不为 sibling 伪造状态。
  - coordinator 只消费版本化、领域无关的 `MergeReadinessDecision`。默认 fallback 仍是全部 required slots；领域插件可选择已满足条件的 canonical slots，core/coordinator 不识别 factor witness。
  - Task 7 gate 只在真实 runtime stage 生效：raw hook 位于 raw/provenance/usage artifacts 持久化之后；`NO_VERIFICATION` 跳过插件 verifier，但 verification/canonical evidence 仍由 engine 记录；`NO_REQUEUE` 只在 engine recovery event 落账后停止 replacement；merge hook 接收真实 canonical protocol event refs。hook observation 只进入 result summary，不拥有协议事件或状态迁移权威。
- `src/tokenshare/plugins/factorization/runtime_adapter.py`
  - `FactorizationRuntimeAdapter` 用既有 split strategy 生成 root、完整 range child 列表和 merge plan；plan/commitment 共用同一 `_build_split_action`，自定义 split config 不会漂移。
  - `FactorizationExecutionBridge` 复用 validator、prompt/parser/verifier 与 merge policy。range 使用固定 AI executor identity，root/merge 使用确定性 executor identity；request policy 固定 entry/model/reasoning 以及 source/prepared config digest，pre-call/post-call identity mismatch 均 fail closed。
  - verification 绑定真实 `ExecutionRequest.input_refs`；attempt refs 保留 request/raw/parsed/parse-failure/provenance/usage/model/candidate 的稳定去重并集，canonical candidate 不覆盖 parsed provenance。
  - bridge 只提供领域计划/执行/验证/合并规则；同 root worker capacity、recovery 和 lifecycle 由 `local_runtime`/`ProtocolEngine` 拥有。
  - 当前论文 runtime 策略为 `factorization.factor_witness_or_all_ranges.v2`：只从 parser/verifier accepted 且 canonical 的 range result 选择有效 factor witness；任一 witness 足以 ready，其他 sibling failure 不推翻它。没有 witness 时必须全部 required ranges accepted/canonical、slot/coverage 完整才 ready；否则 terminal failure 使 root failed。
- `src/tokenshare/experiments/factorization_paper_adapter.py`
  - FULL 路径只冻结条件/transport、构造 runtime adapter/backend、调用 coordinator 并把 ledger/artifact projection 转为 `FactorizationPaperRunResult`。
  - FULL 接受 runtime `completed`/`failed` 终态；selected-unit 接受明确 `partial` observation。scripted/capturing transport 永远 `paper_eligible=false`；当前 dispatcher 可达 selector 和 FULL/fault/ablation 全部走 system runtime。
- `src/tokenshare/experiments/paper_unit_commitments.py`
  - Factorization 与 Lean AI-unit commitment 都从对应 runtime plan 的真实 `TaskUnit` snapshot 与 request artifact commitments 派生，不再维护另一份手工 child / lemma-node 计划；structured-blocked Lean case 不生成可执行 binding。
- `src/tokenshare/protocol_engine.py`
  - `record_parent_failure()` 校验 child 是 direct child、当前 snapshot 为 Failed，且 ledger 中被引用的 child failure event 的 seq/hash/body 精确匹配；只有 engine 写 parent/root `Processing -> Failed` 标准状态事件。
  - `record_dependency_ready()` 只接受当前 graph 中已满足所有 `depends_on_output` canonical refs 的 Blocked unit，原子记录标准状态事件及 dependency source unit IDs/output refs/causation。
- `src/tokenshare/core/task_graph.py`
  - `activatable_unit_ids()` 只从 graph relation 与 canonical output snapshot 计算依赖可解阻集合；不含 Lean/factorization 领域规则，也不写事件。
- `src/tokenshare/plugins/lean_proof/fixed_plan.py`
  - `LeanFixedDecompositionPlan` 接收 catalog 预注册 node/edge/merge shape 并校验 DAG、唯一 root、node/edge/slot 完整性、depth/leaf/count、environment/oracle digest；统一生成既有 v2 certificate。AI 不参与拆分，也不声称通用 lemma discovery。
- `src/tokenshare/plugins/lean_proof/runtime_adapter.py`
  - `LeanRuntimeAdapter` / `LeanExecutionBridge` 统一规划 simple 与 v2 lemma-DAG protocol units，请求 AI proof candidate，经真实 checker accepted 后才提升 canonical proof artifact，并复用既有 merge policy 完成 dependency-aware assembly/root recheck。
- `src/tokenshare/experiments/lean_paper_adapter.py`
  - FULL 主路径只适配 case/config、调用 coordinator，并从 ledger/artifacts 投影旧 `LeanPaperRunResult`；selected-unit 同样通过 coordinator/checker 输出 partial observation，structured-blocked 保持零 provider attempt。当前 dispatcher 可达 selector 和 FULL/fault/ablation 全部走 system runtime。
- `src/tokenshare/experiments/paper_workers.py`、`src/tokenshare/experiments/paper_formal_callbacks.py`
  - Task 6 后 experiments 不再实例化 lease authority 或启动独立 progress harness。`paper_workers` 只冻结 generic dependency graph/kill selection，并从 backend facts + engine event refs 投影 worker-death artifact；缺 `Expired/Superseded/Ready/replacement lease` 任一证据即 fail-closed。
  - Exp2 callback 把 capacity 交给单 root runtime，并从 unit timestamps/dependencies 派生 parallelism、throughput、wall clock/critical path；不再用独立 root threads 或伪造 protocol lifecycle event。worker-death callback 只生成 `EXPERIMENT_WORKER_DEATH_*` 计划/观察事件。
- `src/tokenshare/experiments/paper_faults.py`、`src/tokenshare/experiments/paper_ablation.py`
  - `PaperFaultRuntimeHooks` 只在 `ArtifactStore.verify()` 通过 raw/provenance/usage refs 后，对冻结 target 的 unit 一次性注入五类权威 rate-fault；runtime record 保存 injection ID、primitive record、artifact refs 和 replacement requirement，并生成带协议/artifact 引用的 `EXPERIMENT_FAULT_INJECTED`。
  - `runtime_controls_for_mode()` 把 FULL 映射为全开启 policy + `NoOpRuntimeHooks`，其余五种模式各关闭一个 mechanism；`PaperAblationRuntimeHooks` 在稳定 gate 生成带 protocol/artifact refs 的 `EXPERIMENT_ABLATION_GATE_APPLIED` 观察。
- `src/tokenshare/experiments/paper_formal_runner.py`
  - Exp3 post-raw callback 适配 `RawOutputContext` 并调用 fault hook；runner 只观察实际 attempts/runtime records，不再再次调用 adapter 手工创建 replacement。Exp4 不再手写 stuck/requeue，fault/ablation/worker-death 观察统一使用 `EXPERIMENT_*` namespace。
  - Task 8 后 runner 只选择 condition/root、保存实验级 blocked/budget/error 与 checkpoint generation identity；正常协议结果来自 shared dispatcher/runtime projection。
- `src/tokenshare/local_runtime/projection.py`
  - `project_protocol_run()` 只从指定 task 的 ledger 事件和可验证 artifact refs 派生 `ProtocolRunResult`；event refs、artifact refs 与 summary 不形成第二状态机。
  - 损坏、缺失或 partial/malformed artifact ref 直接 fail-closed，不允许 completed result 静默少报证据。
- `src/tokenshare/experiments/paper_projection.py`
  - 从 `ProtocolRunResult`、权威 protocol events 和已验证 artifacts 只读派生 `PaperAttemptResult` / `PaperTaskResult`、lifecycle coverage、failure mapping 与 runtime generation identity。
  - projection 不调用 checker/verifier、不绑定 canonical、不创建 replacement、不推进 merge/completion；成功结果缺必要 lifecycle evidence 时 `paper_eligible=false`。
- `tests/local_runtime/test_runtime_boundaries.py`
  - 冻结 runtime/plugins→experiments、core→runtime/executors/storage 增量依赖和 experiments 协议状态写入边界。
  - 迁移前已有 5 条 core→storage 导入使用精确计数 allowlist 保留；该测试只禁止债务增长，不表示 core I/O 已完成迁移。
  - 通过静态 AST 检查禁止 experiments 导入/调用 `transition_task_unit`，或直接调用 `EventLedger.append()` 写协议状态事件。
- `tests/local_runtime/test_coordinator_full_lifecycle.py`
  - 用真实 engine/ledger/artifact store 覆盖 expand → children → merge → parent completion → settlement、direct-root complete、foreign-task canonical 隔离、dependency canonical 后 engine 解阻、Windows-safe IDs 和 projection integrity；monkeypatch 只作 engine call spy，不替换实现。
- `tests/local_runtime/test_submission_and_recovery.py`
  - 覆盖 executor exception、fencing/deadline rejection、verification rejection、replacement 调度、retry limit、child exhaustion，以及 FULL/非法 backend capacity/run-id fail-closed；Task 7 另证明 `NO_VERIFICATION` 保留 engine canonical authority、`NO_REQUEUE` 只在 recovery 后阻止 replacement。
- `tests/local_runtime/test_worker_death_recovery.py`
  - 证明同一 root children 在 thread capacity 下真实重叠，且每个 unit 都有 engine lease；真实 process death 后 coordinator PID 不变，ledger 产生 lease `Expired`、attempt `Superseded`、TaskUnit `Ready` 和不同 replacement lease。
- `tests/plugins/factorization/test_factorization_runtime_adapter.py`
  - 覆盖 runtime plan、真实 snapshot commitment、executor/request identity、verification input、artifact refs、merge/completion 与失败路径。
- `tests/local_runtime/test_factorization_runtime.py`
  - 使用真实 engine/ledger/coordinator 检查所有 range child 的 scheduler/lease/submission/verification/canonical lifecycle，以及 engine-owned root completion/failure。
- `tests/experiments/test_factorization_paper_adapter.py` 与 `tests/test_phase6_factorization_flow.py`
  - 覆盖 FULL 薄壳、coordinator 调用、paper eligibility、真实承诺与既有 Factorization 回归。
- `tests/plugins/lean_proof/test_lean_fixed_plan.py`、`tests/plugins/lean_proof/test_lean_runtime_adapter.py`
  - 覆盖 fixed-plan schema/validator/certificate、AI executor capability、protocol unit 与 dependency-edge 映射。
- `tests/local_runtime/test_lean_fixed_plan_runtime.py`、`tests/experiments/test_lean_paper_adapter.py`
  - 覆盖 simple/v2 FULL coordinator 生命周期、checker-before-canonical、dependency input/activation、merge/root recheck、paper result projection 与旧兼容回归。

### Feat-011 Task 2-7 历史迁移状态（historical）

本段记录 Task 2-7 当时的迁移进度，只作 historical provenance。纯 submission/retry 决策位于 `core/recovery.py`，权威 event 写入位于 `ProtocolEngine`，submission v2/SQLite projection 位于 storage；Factorization/Lean runtime bridge、同 root thread/process capacity、engine-owned worker-death recovery 和 fault/ablation typed hooks 均在相应 Task 完成。段末当时的“Task 8 尚未开始”已被下方 Task 8/9 当前映射覆盖。

### Feat-011 Task 8 当前映射

Task 8 已完成通用 dispatcher、ledger/artifact paper projection、experiment/protocol `record_scope` 与 checkpoint generation identity。`experiments/paper_projection.py` 只读派生 `PaperAttemptResult` / `PaperTaskResult`；`experiments/paper_dispatcher.py` 调用 `ProtocolRunCoordinator`；两个 paper adapter 保留领域观察摘要但不再作为公共状态权威；`experiments/paper_formal_runner.py` 只选择条件、记录实验级 blocked/budget/error 并保存 checkpoint identity。`local_runtime/coordinator.py` 在 terminal child failure 时仍经过稳定 merge hook 观察点，之后由 engine 正常记录父失败。`tests/experiments/test_paper_projection.py`、dispatcher/formal runner tests、两个 adapter tests 与 `tests/local_runtime/test_submission_and_recovery.py` 覆盖该边界；Lean checker spy 和固定 canary 证明投影没有替代 checker。上方 Task 2-7 段末“Task 8 尚未开始”仅是当时的历史状态，已由本段覆盖。

### Feat-011 Task 9 当前映射

Task 9 新增 `tests/integration/test_paper_protocol_runtime_integration.py`，从共享 dispatcher 验收 Factorization FULL 与 Lean fixed-plan FULL 的 registration、scheduler/lease/attempt、request/submission、verification/checker、canonical、expand、merge、contribution、completion 和 settlement event coverage；并在同一文件验证 verifier rejection、late submission、真实 OS worker death、六种 ablation 和 experiments 非协议写入权威。`experiments/paper_dispatcher.py` 只为 Lean 增加可选 checker 透传；默认正式路径仍使用真实 checker，Factorization 不接受 Lean checker。recovery/canonical/worker/merge 的系统权威没有移入 experiments。收尾修复 `experiments/lean_catalog_audit.py` 的纯 verify digest 漂移：零 recheck 且内容完全相同时复用原 manifest，真实变更仍生成新摘要。audit/CLI=`23 passed`，修复后 Task 9 最终精确 Verify=`191 passed in 143.75s`，固定 Lean canary=`11 passed in 101.74s`，最终增量 audit 复用 600 条且 digest 稳定、provider calls=0，最终 Fast=`330 passed, 1 skipped in 52.17s`。

### Feat-011 Task 10 收口映射（用户批准抽样验证）

Task 10 已把 `local_runtime`、Factorization/Lean runtime bridges 与 `paper_projection` 补入 README、导航和两份 code map；公开 adapter API 以 docstring 标记 deprecated，两个零引用的旧私有 artifact-ref helper 已删除。迁移新增 source/tests 的范围扫描只保留权威五类 `FaultInjectionRecord`、正常 `inspect.signature` 和 schema/hash/secret/replay correctness，不新增外部输入攻击或安全加固。静态边界门禁同时覆盖 `EventLedger.append()` 与 `append_batch()`，防止 experiments 直接写协议权威事件。2026-07-23 收口证据为 package import/compile、原唯一失败节点、Fast `330 passed, 1 skipped` 和确定性 150-entry Lean real-checker 抽样 `150/150 accepted`、provider calls=0。按用户明确决定，本轮未运行完整 923-node targeted、Full、Full+LeanAudit 或 600-entry force-all；抽样不写 tracked manifest，也不得被表述成发布级全量证据。

### Feat-011 正式运行补缝（2026-07-23）

- `experiments/paper_faults.py` 使用 `experiment_unit_id=case_id:planned_ai_unit_id` 把冻结 fault target 映射到 scheduler 实际创建的 protocol unit；五类 rate-fault 只在 raw/provenance/usage 已持久化后注入，后续 reject、lease expiry、recovery、requeue 和 replacement 由 `ProtocolEngine`/coordinator 产生。`late_submission` 按解析后的 ISO timestamp 比较，不能再受 `Z` 与 `+00:00` 字符串排序影响。
- `local_runtime/workers.py` 的 `ProcessWorkerBackend` 接受 `WorkerTerminationPolicy`，按 `termination_count_target` 终止预注册的 1 个或 3 个真实 executor processes，并把子进程已持久化的 executor/plugin state 回传父 coordinator。逻辑 unit 少于死亡进程数时，后续终止命中同一 unit 的 replacement process；adapter 只据计划死亡数配置足够的协议 retry budget。死亡 attempt 没有 submission；lease 到期后由 engine 写 `Expired/Superseded/Ready`，replacement 重新取得不同 lease/attempt/fencing token。`experiments/paper_workers.py` 为每个死亡 attempt 保存独立 artifact，并只从 backend facts 和 ledger events 投影实验记录。
- `ProtocolMechanismPolicy.slot_integrity_enabled` 由 coordinator 传给 plugin runtime merge。Factorization/Lean runtime 在 `NO_SLOT_INTEGRITY` 下故意把已 canonical 的 child proof/result 绑定到错误 required slot；plugin merge policy只关闭 slot-binding 检查，其余集合、领域验证与 Lean root checker 保持开启。Lean 固定 lemma-DAG 仍来自 catalog/脚本预注册，未改成 AI 或通用自动拆分。
- `experiments/paper_dispatcher.py` 接受非空 `selected_ai_unit_id`，把它作为 `ProtocolExecutionScope` 随 `ProtocolRunRequest` 交给 coordinator；正式 Exp1/Gate C pilot 只执行选中协议 unit并输出 partial/paper-ineligible observation，不执行 sibling，不产生 root merge/completion/settlement。
- `experiments/paper_catalog_execution_view.py` 提供 `tokenshare.paper_catalog_execution_view.v1`；dispatch plan v3 冻结 manifest/Exp3/Exp4 catalog view 的 body/digest，formal execution、resume/replay 从 plan 恢复同一对象，不再使用原始 manifest 重新计算不同 selection。
- 定向回归分三组为 `17 passed in 59.23s`、`27 passed in 37.36s`、`23 passed in 102.74s`，合计 67 项通过；Fast 为 `330 passed, 1 skipped in 20.34s`。本轮没有 provider call、Full、全量 Lean audit 或正式实验。

### Feat-011 三项 system-native 补缝（2026-07-24）

- dispatch plan v3 冻结 `PaperCatalogExecutionView.v1`，formal execution/resume/replay 恢复同一 body/digest；Exp1 从 Factorization 切到 Lean 时不再因 `task15_budget_input` 丢失而 selection drift。
- `ProtocolExecutionScope.v1` 让 selected-unit pilot 继续经过插件 plan/split、coordinator、scheduler/lease 和 `ProtocolEngine`，只返回 partial/paper-ineligible observation；未选 sibling 不执行，root 不 merge/complete/settle。
- `MergeReadinessDecision.v1` 为通用 plugin readiness contract；Factorization 使用 `factorization.factor_witness_or_all_ranges.v2`，有效 witness OR-ready，无 witness 时全部 required ranges AND-ready。merge task link v2 绑定 decision/digest 和实际选择的 canonical slots，core 不含 Factorization 领域判断。
- 最终验证：合并影响集 `336 passed`，execution-runner/CLI `50 passed`，证据回填后 Fast `330 passed, 1 skipped`，固定 Lean canary `11 passed`，Full `1324 passed, 1 skipped`。真实 provider calls/tokens/cost=`0/0/0`；未运行 force-all 或正式实验。

Phase 7 实验级 AI API executor source：

- `src/tokenshare/executors/ai_api.py`
- `src/tokenshare/executors/ai_api_config.py`
- `src/tokenshare/executors/ai_api_local_config.py`
- `src/tokenshare/executors/ai_api_replay.py`
- `src/tokenshare/executors/ai_api_selector.py`
- `src/tokenshare/executors/ai_api_transport.py`

这些文件仍使用 Phase 3 `ExecutionRequest` / `ExecutionSubmission` 契约，但真实 provider transport、local secret injection、model selection、raw persistence 和 replay guard 属于 Phase 7，而不是 Phase 1-6 协议内核。2026-07-19 起 JSON mode 对 SiliconFlow/OpenAI 均固定 `temperature=0`；SiliconFlow builder 还对所有支持 JSON mode 的 entry 固定写入 `response_format={"type":"json_object"}` 与 `enable_thinking=false`，并在 transport 前拒绝显式 `enable_thinking=true`。该 transport contract 不包含任何 experiment-specific condition expansion。

Phase 7 AI API executor tests：

- `tests/phase7_fixtures.py`
- `tests/executors/test_ai_api_config.py`
- `tests/executors/test_ai_api_descriptor.py`
- `tests/executors/test_ai_api_executor_failover.py`
- `tests/executors/test_ai_api_executor_parser.py`
- `tests/executors/test_ai_api_executor_success.py`
- `tests/executors/test_ai_api_local_config.py`
- `tests/executors/test_ai_api_replay_guard.py`
- `tests/executors/test_ai_api_selector.py`
- `tests/executors/test_ai_api_siliconflow_smoke.py`
- `tests/executors/test_ai_api_transport.py`
- `tests/test_phase7_ai_api_execution_flow.py`

Phase 8 / benchmark / paper experiment source：

- `src/tokenshare/experiments/adapters.py`
- `src/tokenshare/experiments/ai_profile.py`
- `src/tokenshare/experiments/factorization_500_ai.py`
- `src/tokenshare/experiments/factorization_adapter.py`
- `src/tokenshare/experiments/lean_adapter.py`
- `src/tokenshare/experiments/lean_ai_benchmark.py`
- `src/tokenshare/experiments/lean_paper_adapter.py`
- `src/tokenshare/experiments/metrics.py`
- `src/tokenshare/experiments/models.py`
- `src/tokenshare/experiments/paper_budget.py`
- `src/tokenshare/experiments/paper_catalog.py`
- `src/tokenshare/experiments/paper_metrics.py`
- `src/tokenshare/experiments/paper_model_identity.py`
- `src/tokenshare/experiments/paper_model_policy.py`
- `src/tokenshare/experiments/paper_models.py`
- `src/tokenshare/experiments/paper_report.py`
- `src/tokenshare/experiments/paper_runner.py`
- `src/tokenshare/experiments/paper_dispatcher.py`
- `src/tokenshare/experiments/paper_unit_commitments.py`
- `src/tokenshare/experiments/report.py`
- `src/tokenshare/experiments/run_ai_profile.py`
- `src/tokenshare/experiments/run_all.py`
- `src/tokenshare/experiments/run_factorization_500_ai.py`
- `src/tokenshare/experiments/run_lean_ai_benchmark.py`
- `src/tokenshare/experiments/run_paper_experiments.py`
- `src/tokenshare/experiments/runner.py`
- `src/tokenshare/experiments/simulation.py`

这些文件承载 Phase 8 regression infrastructure、AI profile、direct factorization 500、Lean AI 50 和后续 `feat-011` paper experiments。2026-07-18 Task 14 的 Lean 3×3 catalog/readiness、checker-backed oracle feasibility、paper runner plan-only budget input 和 zero-call boundary 也属于这个范围外实验层；它们可以调用 Phase 1-6 protocol/plugin/executor surfaces，但不能重新定义协议权威事实。当前 Task 14 manifest 冻结 9 个 executable cells、0 个 blocked cells、每格 15 个 checker-backed selected case IDs、合计 135 个 Lean roots；`benchmarks/paper/lean_lemma_graph_catalog.v1.jsonl` 保留 hard/frontier no-oracle structured-blocked regression rows，但这些 rows 不计入 selected slice 或 golden readiness。

2026-07-19 范围外实验层更新：`paper_dispatcher.py` 通过统一 `PaperExperimentModule` Protocol 加载 Exp1–5，并把单个 paper case 路由到 Factorization/Lean adapter；`paper_runner.py` 为各模块构造 formal catalog view、冻结 exact selections，并为 pilot-only 执行选择一个 case/AI unit；`paper_budget.py` 将 exact selections、AI-unit commitments、endpoint identity、request/hard limits 纳入审批 digest；`run_paper_experiments.py` formal plan-only 使用该 dispatcher并输出零调用计划。当前 semantic-repair digests 为 catalog `sha256:a3b3713663a7d564eb5440f4bbec714cf600b24a80539c6a990746184f11134f`、matrix `sha256:43f1a3ab54c735cf34f9438d8b04899976bfc2d11afc47073c5822f4de2f8255`、selection `sha256:4b1b08cc8893045aa6206461f3b27563943a76022a77b29934d02d5044225d24`，冻结 135 个 Lean roots / 570 个 AI units。上述内容不改变 Phase 1–6 协议对象、事件或状态机。

2026-07-19 Prompt H review hardening：所有 JSON-mode provider request 固定零 temperature；case/unit selector 强制独立 output root，并在 CLI/direct runner 两层拒绝 resolve 后与 canonical full-pilot tree 相等或祖先/子孙重叠；CLI 错误审计落到 sibling blocked root。incomplete Exp5 dispatch plan 带 `blocked/incomplete_model_cohort/paper_eligible_possible=false` 元数据，并且 combined suite 不让 optional P0-full Exp5 覆盖 planned P0-core Exp1。用户明确排除人为伪造/注入攻击模型，因此未在范围外实验层增加相应对抗逻辑。root-isolation TDD 为 `6 failed` -> `6 passed`，最终独立复审无 Critical/Important，Prompt H 已形成 reviewed integration checkpoint。最终全实验门禁为 `528 passed`，完整启动门禁收集 969 项并通过 `968 passed, 1 skipped`，最新零调用审计检查 553 个 call fields 与 10 个 suite attempt counters 并保持全部为 0；`feat-011` 仍为 `in-progress`。

2026-07-19 Prompt H integration-owner closeout：范围外实验层 general CLI 现在有一条不 monkeypatch execution callback 的 Exp2 E2E 回归，实际到达注册模块、Factorization adapter、AIAPIExecutor、capturing request 和 verifier/evidence。`paper_runner._exp3_catalog_view()` 将 Exp3 condition-level fault target identity 固定为 `case_id:planned_ai_unit_id`，消除跨 case 局部 range ID 碰撞；H 仍不执行非零 fault/worker death。Exp2 summary 把派生 `case_selection_digest` 与模块所有权 `selection_digest` 分离重算，保证共享 slice 比较不破坏 C–G 原契约。最终 experiments 为 `541 passed`，完整启动门禁收集 982 项并通过 `981 passed, 1 skipped`；H 范围 3569 个 provider-call fields 全为 0，capturing evidence 仍不可 paper-eligible，未产生真实 pilot/formal 论文结果。

2026-07-19 Prompt H exact-binding closeout：范围外实验层新增 `FrozenConditionSelectionBinding` 与 tuple-compatible `FrozenCaseSelectionBatch`，由 Exp1–5 在原完整 matrix freeze 内为每个 selection 同时写入完整 condition ID/digest；`PaperExperimentDispatchPlan` 升为 v2 并持久化显式 bindings。shared dispatcher、runner 和 CLI budget commitment 只按完整 identity 查找，不再把平行数组位置当作归属；duplicate/missing/extra/digest/wrong-selection 在 plan 期拒绝。该修复只补实验集成 identity contract，不改变 Phase 1–6 核心或 C–G 矩阵算法。专项复审无 Critical/Important/Minor；最终 targeted=`446 passed`、experiments=`543 passed`、impact=`211 passed, 1 skipped`、完整启动门禁=`983 passed, 1 skipped`（984 collected）。pytest-1452 H-scope/readiness audit 检查 774 个结构化 JSON/JSONL 的 4308 个 provider-call fields，全部为 0 且无解析遗漏；未产生 pilot/formal 论文结果，`feat-011` 仍为 `in-progress`。

2026-07-18 历史语义复核补充（已由 2026-07-19 semantic repair 覆盖）：当时的 manifest 只能证明 135 个唯一 case IDs 和 checker-backed 机械 readiness，八个 v2 passed cells 每格只有一个 canonical theorem/DAG 形状，且三个 hard checker pools 与对应 medium 模板相同，因此旧 digests 当时不得进入正式 Task 15。该 blocker 已通过上段新 digests、每格 15 个不同 semantic fingerprints、hard/medium 分离和逐格 golden evidence 修复；本段仅保留为历史 review provenance，不再表示当前 Gate C 状态。

Phase 8 / benchmark tests：

- `tests/experiments/test_ai_profile_suite.py`
- `tests/experiments/test_factorization_500_ai.py`
- `tests/experiments/test_lean_ai_benchmark.py`
- `tests/experiments/test_lean_paper_adapter.py`
- `tests/experiments/test_lean_task14_readiness.py`
- `tests/experiments/test_lean_lemma_graph_catalog.py`
- `tests/experiments/test_paper_budget.py`
- `tests/experiments/test_paper_catalog.py`
- `tests/experiments/test_paper_gate_c_dispatcher.py`
- `tests/experiments/test_paper_gate_c_structured_output.py`
- `tests/experiments/test_run_paper_experiments_cli.py`
- `tests/experiments/test_phase8_default_suite.py`
- `tests/experiments/test_phase8_models.py`
- `tests/experiments/test_phase8_runner_reports.py`
- `tests/experiments/test_phase8_simulation.py`
- `tests/experiments/test_run_all_cli.py`

Replay placeholder：

- `src/tokenshare/replay/__init__.py`
  - 当前 package placeholder；完整 Phase 9 replay/audit 已延后。

阅读这些代码时，应先看对应 Phase 7/8/feat-011 文档，不要把它们误认为 Phase 1-6 协议内核新增规则。

## 12. 当前验证命令

默认快速启动验证：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

完整验证：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1 -Full
```

Lean 增量审计：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1 -Full -LeanAudit
```

共享 checker/toolchain/helper 变化或正式发布前的显式全量审计：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1 -Full -LeanAudit -ForceAllLeanAudit
```

两个 wrapper 都只调用一次：

```text
conda run -n tokenshare python verification/run_verification.py --mode <fast|full> [...]
```

统一入口完成 JSON/SQLite、harness 和排除 `reference_repos/` 的 compileall；默认档随后读取 `verification/fast-tests.txt`，完整档运行 `pytest tests`。LeanAudit 默认增量，只有 force-all 才重检全部 600 个 entry。默认档用于启动和开发循环，完整档用于 feature 完成、提交/合并；Lean 相关变更和实验发布叠加对应 audit 档位。

2026-07-22 分层改造实测：Fast 首次为 290 passed、1 skipped（pytest 11.93 秒，约 28.5 秒墙钟），最终冷态复验同为 290 passed、1 skipped（pytest 25.30 秒，46.1 秒墙钟）；最终固定 Lean canary 为 11 passed（pytest 47.15 秒，runner 总墙钟 61.6 秒）；600-entry force-all audit 由约 441.7 秒降至 188.8 秒，600/600 accepted。全仓 Full 当前仍受 feat-011 迁移范围内 3 个既有 paper assertion drift 阻塞，不能把这三项改造范围外失败写成通过。

2026-07-22 system runtime 迁移 Task 2 实测：迁移计划指定的 Phase 2/3/4/storage 影响集为 61 passed；Fast 为 325 passed、1 skipped。Task 2 没有改 Lean catalog/checker/toolchain/helper，也没有发布实验结果，因此按分层计划未运行 Lean canary、600-entry LeanAudit 或 Full；上段既有 Full assertion drift 未在 Task 2 中处理。

2026-07-13 文档收敛前的基线验证结果：

```text
python-json-sqlite-ok
harness-files-ok
387 passed, 1 skipped in 170.77s
```

## 2026-07-25 Feat-011 Task A–11 当前映射

实验设施补全保持协议核心、plugin runtime 和 experiments 三层边界：

- `local_runtime/coordinator.py`/workers 提供真实 observation clock、worker execution facts、batch 后 plugin readiness、未调度 sibling early stop、process death/lease recovery 和稳定 ablation gate；experiments 只选择条件、注入预注册 hook 并投影事实。
- Factorization plugin 的 `factorization.exp2_contiguous_20way.v1` 是 Exp2 20-way 拆分的唯一领域所有者；`runtime_adapter.py` 只为该显式 profile 提升 child cap，并用真实 range 长度绑定 no-factor recheck。
- `experiments/paper_budget.py` 统一冻结 headline/supporting/actual root/AI-unit identity 和 replacement reserve；P0-core/P0-full actual roots=`52,514/54,413`，provider-attempt 上界=`715,558/730,210`。
- `experiments/paper_formal_runner.py`、`paper_formal_metrics.py`、`paper_formal_report.py` 形成正式证据/指标/报告单路径；Exp5 从持久化 v2 model execution record 严格连接 request/raw/provenance/usage，生成 endpoint comparison 和 provider-confounding 输出。
- Task 11 指定组合回归=`286 passed`，Fast=`331 passed, 1 skipped`，capturing provider calls/tokens/cost=`0/0/0` 且 `paper_eligible=false`。没有运行真实 formal 实验、Full、LeanAudit 或 force-all；该映射证明设施完成，不代表 `feat-011` 的论文结果已经完成。

## 2026-07-26 正式 AI timeout 控制

- 实验层共享常量 `paper_models.PAPER_FORMAL_AI_TIMEOUT_SECONDS=100` 统一驱动正式 Exp1–4 controls、formal CLI、Gate C 和 Exp5 preflight；tracked baseline/pilot 配置也固定为 100。Exp5 的三个 endpoint 只“彼此相等”不够，任一不是 100 都以 `formal_ai_timeout_seconds_mismatch` 整体阻塞。
- worker-death 外层 process guard 仍按请求 timeout 加 30 秒清理余量，故正式值为 130 秒；provider 响应上限仍是 100 秒。协议 lease、Lean checker、通用 executor/adapter fallback 和历史 benchmark timeout 均保持原值。
- 新 timeout 进入 config/profile/condition/budget identity，旧批准 digest 全部失效。验证为 timeout 直接影响集 `218 passed`、最终 Fast `331 passed, 1 skipped in 14.83s`；没有 provider、Full、LeanAudit 或正式实验调用。

## 2026-07-26 正式实验证据真实性与指标完整性门禁

- `src/tokenshare/experiments/paper_formal_runner.py`
  - checkpoint 不再复制 adapter/suite 的预判布尔值；attempt/task 资格由 real transport、whole-root protocol execution scope、持久化状态、evidence refs、artifact/event inventory 和 synthetic 禁止项计算。`_finalize_formal_manifests()` 只聚合已 checkpoint 的 condition/task/attempt evidence。
  - `_materialize_artifacts()` 保留原 artifact identity；持久化审计解析 task/attempt refs，无法回到本 generation 的 protocol event 或 artifact 时 fail closed。
- `src/tokenshare/experiments/paper_formal_metrics.py`
  - 顶层资格唯一聚合 condition rows 和各 active experiment rows，不再复制 suite 的旧 adapter 资格。Exp2 critical path 由 root registration、unit creation/dependency、attempt interval、canonical、merge gate/record、root completion evidence 构图；缺证据不回退到 wall clock、provider latency 或最长 attempt。关键路径完整性只作为 Exp2 专项资格门禁；Exp1/3/4/5 的 checker rejection、未恢复故障、消融失败或 provider/model failure 只要下层终局与专项 evidence 完整，仍是可进入正式分母的负面结果，不因缺少成功 merge/root-completion 路径而失格。
  - Exp2 同时输出 all-runs 与 rate-limit-excluded sensitivity，排除项按 persisted run id/condition id/reason 列出且不修改主视图。Exp3 worker-death 字段覆盖 task、repeat condition、aggregate 和 robustness CSV，并禁止 matched baseline 自引用。
  - Exp4 审计 runtime schema/condition/case/repeat/mode、目标 hook input/result/ref、attempt inventory、NO_MERGE_GATE 实际 premature merge 结果及同 case×repeat 合格 FULL 配对；零分母写 null/applicability。Exp5 以全部应有 provider-attempt 为 identity denominator，检查 missing/duplicate/orphan/mismatch 与 retry 展开。
- `src/tokenshare/experiments/paper_formal_report.py`
  - report 独立重读 suite/experiment/condition/task/attempt/event/artifact inventory 并解析 refs；任一 lower evidence 不合格、缺失或 capturing/regression 时删除/拒绝正式 paper report，只生成 `formal_regression_report.md`。
- `src/tokenshare/experiments/paper_ablation.py`
  - ablation hook observation 保存真实 hook input/result、artifact refs 和 protocol refs，空列表或仅凭 mode 名称不再表示“效果为零”。
- 反伪造回归集中在 `tests/experiments/test_paper_formal_runner.py`、`test_paper_formal_metrics.py`、`test_paper_formal_report.py` 以及 Exp2–5/Gate C/integration 文件。负面终局门禁 TDD 为 `4 failed, 1 passed`（RED）到 `5 passed`（GREEN），完整 metrics 文件为 `28 passed`；用户指定九文件最低验收为 `251 passed`，最终 Fast 为 `331 passed, 1 skipped`。capturing 始终 paper-ineligible；本轮没有真实 API、正式实验、Full 或 LeanAudit，故 `feat-011` 仍为 `in-progress`。
