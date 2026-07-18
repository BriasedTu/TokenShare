# TokenShare V1 Code Map

日期：2026-07-13

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
- `Attempt` 允许 `Created -> Running/Superseded`、`Running -> Submitted/Failed/Superseded`、`Submitted -> Verified/Rejected`、`Verified -> Canonical`。
- `Running -> Submitted` 会写 raw/parsed/candidate/log artifact refs。
- `Submitted -> Rejected` 会记录 invalid output failure。
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

### `src/tokenshare/core/leases.py`

职责：租约 claim、heartbeat、expiry 的纯规则。

对象：

- `LeaseClaim`
- `LeaseExpiryDecision`
- `LeaseManager`

关键机制：

- `claim()` 从 `SchedulingDecision` 创建 active `Lease`、created `Attempt` 和 running `Attempt`。
- `heartbeat()` 只允许 active lease 且 `now < expires_at`。
- `expire()` 只允许 active lease 且 `now >= expires_at`，并要求 attempt 为 `Created` 或 `Running`。
- expiry 会把 lease 置为 `Expired`，attempt 置为 `Superseded`。
- 根据 retry count 与 `ProtocolConfig.max_retries` 推导 next task state：`Ready` 或 `Failed`。

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
- `MergeTaskLink` 要求 `readiness_reason=all_required_slots_canonical`。
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
- submission：`EXECUTION_SUBMISSION_RECORDED`，匹配时再写 `ATTEMPT_STATE_CHANGED Running -> Submitted`
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
- lease expiry：`LEASE_STATE_CHANGED`、`ATTEMPT_STATE_CHANGED`、`RECOVERY_ACTION_RECORDED`、`TASK_UNIT_STATE_CHANGED`

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
- executor 不拥有 output schema。

测试：

- `tests/plugins/factorization/test_factorization_prompt_package.py`
- `tests/plugins/factorization/test_factorization_ai_parse_policy.py`

### `src/tokenshare/plugins/factorization/merge_policy.py`

职责：factorization all-required merge policy。

关键机制：

- 所有 range slots canonical 后才能合并。
- merge validator 重新检查 coverage、range result schema 和 factor correctness。
- 第一切片不做 early success 或 sibling pruning。

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

- `LeanCheckerMode`
- `LeanCheckerStatus`
- `LeanCheckerRequest`
- `LeanCheckerReport`

函数：

- `check_lean_proof()`

关键机制：

- 校验 request environment digest。
- 读取 theorem payload artifact 和 proof candidate artifact。
- 渲染 Lean source。
- 调用 `lake env lean <generated_source>`。
- 保存 generated source、stdout、stderr。
- accepted 时保存 proof artifact。
- 保存 checker report artifact。
- 拒绝 `sorry` / `admit`。

测试：

- `tests/plugins/lean_proof/test_lean_checker_direct.py`
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
- `tests/test_init_verification_profiles.py`
- `verification/fast-tests.txt`
- `init.ps1`
- `init.sh`

`verification/fast-tests.txt` 是 PowerShell 与 Bash 共用的默认快速测试清单；两个启动脚本默认执行该 smoke suite，`init.ps1 -Full` / `init.sh --full` 才执行完整 `pytest tests`。验证模式契约测试负责防止清单缺失、重复、指向不存在路径或两个入口发生漂移。

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

Phase 7 实验级 AI API executor source：

- `src/tokenshare/executors/ai_api.py`
- `src/tokenshare/executors/ai_api_config.py`
- `src/tokenshare/executors/ai_api_local_config.py`
- `src/tokenshare/executors/ai_api_replay.py`
- `src/tokenshare/executors/ai_api_selector.py`
- `src/tokenshare/executors/ai_api_transport.py`

这些文件仍使用 Phase 3 `ExecutionRequest` / `ExecutionSubmission` 契约，但真实 provider transport、local secret injection、model selection、raw persistence 和 replay guard 属于 Phase 7，而不是 Phase 1-6 协议内核。

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

2026-07-18 语义复核补充：上述 manifest 当前只能证明 135 个唯一 case IDs 和 checker-backed 机械 readiness，不能证明 135 个语义不同 roots。八个 v2 passed cells 每格只有一个 canonical theorem/DAG 形状，三个 hard checker pools 与对应 medium 模板相同，hard/function_set 与 hard/induction 还缺少逐格端到端 golden evidence。因此 Task 14 当前为 `semantic_blocked`；旧 digests 不得进入正式 Task 15，需在 semantic catalog repair 后重算。

Phase 8 / benchmark tests：

- `tests/experiments/test_ai_profile_suite.py`
- `tests/experiments/test_factorization_500_ai.py`
- `tests/experiments/test_lean_ai_benchmark.py`
- `tests/experiments/test_lean_paper_adapter.py`
- `tests/experiments/test_lean_task14_readiness.py`
- `tests/experiments/test_lean_lemma_graph_catalog.py`
- `tests/experiments/test_paper_budget.py`
- `tests/experiments/test_paper_catalog.py`
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

两个档位都会运行：

```text
conda run -n tokenshare python -c "import json, sqlite3; print('python-json-sqlite-ok')"
conda run -n tokenshare python -m compileall -x "reference_repos" .
```

默认档随后读取 `verification/fast-tests.txt` 并运行无网络 smoke/regression suite；完整档随后运行 `PYTHONPATH=src conda run -n tokenshare python -m pytest tests`。默认档用于启动和开发循环，完整档用于 feature 完成、提交/合并和实验发布门禁。

2026-07-13 文档收敛前的基线验证结果：

```text
python-json-sqlite-ok
harness-files-ok
387 passed, 1 skipped in 170.77s
```
