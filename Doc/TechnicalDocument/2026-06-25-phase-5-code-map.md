# Phase 5 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-26 |
| 最近更新 | 2026-06-27 Phase 5 hardening：错误 batch id rebuild 拒绝、settlement supplied set 精确匹配 ledger eligible set |
| 对应 feature | `feat-006` - Phase 5 - Merge, Contribution, and Sandbox Settlement |
| 上游规格 | `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md` |
| 当前范围 | Task 1 - pure models, event constants, state rules；Task 2 - merge task creation batch；Task 3 - merge resolution batch；Task 4 - contribution creation；Task 5 - parent completion batch；Task 6 - root-level sandbox settlement batch；Task 7 - subtree pruning batch；Task 8 - SQLite projection, integration, and code map |
| 目的 | 说明 Phase 5 已实现的纯对象、digest helper、event constants、contribution 状态规则、merge task creation coordinator、merge resolution batch、contribution creation coordinator、parent completion batch、root settlement batch、subtree pruning batch、SQLite Phase 5 projection / rebuild invariants 和测试映射；Phase 6 不计入当前已实现范围。 |

## 1. 当前实现范围

当前 Phase 5 已完成 Task 1、Task 2、Task 3、Task 4、Task 5、Task 6、Task 7 和 Task 8，覆盖以下基础能力：

- `src/tokenshare/core/merge.py`：merge 相关纯对象和稳定 digest helper。
- `src/tokenshare/core/contribution.py`：contribution / settlement / subtree pruning 纯对象、contribution 状态迁移规则、canonical contribution creation coordinator 和 sandbox settlement entries helper。
- `src/tokenshare/core/merge_coordinator.py`：merge task creation coordinator、batch 可见性 gate、staged merge input bundle 和 merge task link 组装。
- `src/tokenshare/protocol_engine.py`：Task 3 merge resolution flow 写 `merge_resolution_batch:{merge_record_id}`；Task 5 parent completion flow 写 `parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}`；Task 6 root settlement flow 写 `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}`；Task 7 subtree pruning flow 写 `subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq}`。
- `src/tokenshare/storage/sqlite_index.py`：Task 8 Phase 5 index-only projection，包含 merge / contribution / settlement / pruning 查询表，以及半批次、settlement artifact、pruning provenance 的 rebuild inconsistency 检查。
- `src/tokenshare/storage/events.py`：Phase 5 event type constants。
- `tests/core/test_phase5_models.py`：Task 1 红绿测试。
- `tests/test_phase5_merge_task_creation_flow.py`：Task 2 红绿测试。
- `tests/test_phase5_merge_resolution_flow.py`：Task 3 / Task 5 红绿测试。
- `tests/test_phase5_contribution_settlement_flow.py`：Task 4 / Task 5 / Task 6 红绿测试。
- `tests/test_phase5_subtree_pruning_flow.py`：Task 7 红绿测试。
- `tests/storage/test_phase5_event_projection.py`：Task 8 红绿测试和完整 merge -> parent completion -> root settlement projection integration。
- `tests/phase5_fixtures.py`：Task 2 夹具扩展，包含完整 / 不完整 expansion batch、标记可见性和 canonical output 类型变体。

2026-06-27 hardening 追加确认：Phase 5 仍为 done，但 rebuild / settlement 契约更严格。SQLite projection 对 Phase 5 关键 batch envelope 校验不再只看前缀，而是要求 batch id 与 marker payload 的权威对象一致；root settlement 不再容忍 caller supplied set 中多出的未落账 eligible contribution，必须精确等于 ledger 当前 eligible set。

| 规格内容 | 代码位置 | 说明 | 主要测试 |
|---|---|---|---|
| Phase 5 `EventType` constants | `src/tokenshare/storage/events.py` | 新增 `MERGE_TASK_LINK_RECORDED`、`MERGE_RECORDED`、`EXPECTED_OUTPUT_RESOLVED`、`CONTRIBUTION_STATE_CHANGED`、`SETTLEMENT_RECORDED`、`SUBTREE_PRUNED`；只固定 spelling，不实现事件 flow。 | `tests/core/test_phase5_models.py::test_phase5_event_type_constants_are_declared` |
| `RequiredSlotBinding` | `src/tokenshare/core/merge.py` | 固定 `schema_version=phase5.required_slot_binding.v1`；要求 `binding_source=canonical_output`、positive `canonical_event_seq`、非空 canonical commitment 字段，并拒绝显式非 canonical output artifact ref。 | `tests/core/test_phase5_models.py::test_required_slot_binding_requires_child_canonical_output` |
| `MergeTaskLink` | `src/tokenshare/core/merge.py` | 固定 `schema_version=phase5.merge_task_link.v1`；要求 `readiness_reason=all_required_slots_canonical`、positive source event seq、稳定排序 required slot bindings、拒绝重复 `slot_key`，并校验 `required_slot_bindings_digest`。 | `tests/core/test_phase5_models.py::test_merge_task_link_digest_is_stable_and_rejects_duplicate_slots` |
| merge digest helpers | `src/tokenshare/core/merge.py` | `digest_merge_task_link()` / `digest_required_slot_bindings()` / `digest_json()` 使用 sorted canonical JSON 和 `sha256:` 前缀，确保输入顺序变化不改变 digest。 | `tests/core/test_phase5_models.py::test_merge_task_link_digest_is_stable_and_rejects_duplicate_slots` |
| `MergeRecord` | `src/tokenshare/core/merge.py` | 固定 `schema_version=phase5.merge_record.v1`；要求 merge canonical commitment fields，包括 `canonical_selection_id`、canonical/verification/submission event seq、selected report/submission/attempt、merge output bundle 和 parent output mapping digest。 | `tests/core/test_phase5_models.py::test_merge_record_rejects_missing_canonical_commitment_fields` |
| `ExpectedOutputResolution` | `src/tokenshare/core/merge.py` | 固定 `schema_version=phase5.expected_output_resolution.v1`；Phase 5 v1 只允许 `resolution_source_type=merge_record`，并要求 merge record / merge plan / merge unit / canonical selection / resolved output ref 与 digest 字段。 | `tests/core/test_phase5_models.py::test_expected_output_resolution_is_merge_record_sourced_in_v1` |
| `ContributionRecord` | `src/tokenshare/core/contribution.py` | 固定 `schema_version=phase5.contribution_record.v1`；kind 限定为 `complete_canonical`、`expand_canonical`、`merge_canonical`；complete/expand 要求 `source_decision_id`，merge 要求 `merge_record_id`；事件 seq 和 `reward_weight` 必须为正。 | `tests/core/test_phase5_models.py::test_contribution_state_machine_allows_only_phase5_transitions` |
| contribution state transition | `src/tokenshare/core/contribution.py` | `transition_contribution()` 允许 `Pending -> Eligible`、`Pending -> Invalidated`、`Eligible -> Invalidated`、`Eligible -> Settled`；拒绝 `Settled -> *`；`Eligible -> Settled` 必须来自 `settlement_batch`。 | `tests/core/test_phase5_models.py::test_contribution_state_machine_allows_only_phase5_transitions` |
| `SettlementEntry` / `SettlementRecord` | `src/tokenshare/core/contribution.py` | 固定 schema version；`SettlementRecord.total_reward` 必须等于 `root_budget`，`entry_count` 必须大于 0，`settlement_entries_ref` 和 summary 必须是 object。 | `tests/core/test_phase5_models.py::test_sandbox_equal_weight_formula_distributes_remainder_deterministically` |
| sandbox settlement entries helper | `src/tokenshare/core/contribution.py` | `build_sandbox_equal_weight_settlement_entries()` 只纳入 `Eligible` 且 `source_terminal_event_seq <= root_completion_event_seq` 的 contributions，按 `contribution_id` 升序稳定分配 remainder，总 reward 等于 `root_budget`。 | `tests/core/test_phase5_models.py::test_sandbox_equal_weight_formula_distributes_remainder_deterministically` |
| settlement digest helpers | `src/tokenshare/core/contribution.py` | `digest_contribution()` / `digest_settlement_entries()` / `digest_json()` 使用 canonical JSON；settlement entries digest 按 `contribution_id` 稳定排序。 | `tests/core/test_phase5_models.py::test_sandbox_equal_weight_formula_distributes_remainder_deterministically` |
| `SubtreePruneRecord` | `src/tokenshare/core/contribution.py` | 固定 `schema_version=phase5.subtree_prune_record.v1`；要求 pruning policy descriptor provenance 字段、positive event seq、非负 cancelled / preserved count；第一版只允许 `merge_policy` / `merge_plan` source type。 | Task 1 通过对象构造覆盖，后续 flow 测试在 Task 7。 |
| merge task creation coordinator | `src/tokenshare/core/merge_coordinator.py` | `MergeCoordinator.create_ready_merge_tasks()` 只在 accepted `MERGE_PLAN_RECORDED` 所在 expansion batch 完整且 final `TASK_EXPANDED` 可见时创建 merge task；required slots 只绑定 child canonical outputs；`merge_input_bundle` 先 staged 保存，随后由 `MERGE_TASK_LINK_RECORDED` 引用成为协议事实。 | `tests/test_phase5_merge_task_creation_flow.py::test_ready_merge_plan_creates_merge_task_link_and_ready_merge_unit_in_one_batch` |
| merge task creation batch gating | `src/tokenshare/core/merge_coordinator.py` | 明确拒绝裸 `MERGE_PLAN_RECORDED`、incomplete `expansion_batch`、不可见 `TASK_EXPANDED`、缺 slot、非 canonical output、冲突 bundle content。 | `tests/test_phase5_merge_task_creation_flow.py::test_merge_task_creation_rejects_merge_plan_from_incomplete_expansion_batch` / `test_merge_task_creation_requires_task_expanded_marker_visible` / `test_merge_task_creation_rejects_candidate_outputs` |
| merge resolution batch | `src/tokenshare/protocol_engine.py` | `ProtocolEngine.record_merge_resolution()` 校验 merge unit 已有 canonical selection、merge task link / input bundle / parent output mapping 一致，并通过 `append_batch()` 写 `MERGE_RECORDED` 和 required `EXPECTED_OUTPUT_RESOLVED`。 | `tests/test_phase5_merge_resolution_flow.py` |
| contribution creation coordinator | `src/tokenshare/core/contribution.py` | `ContributionCoordinator.record_canonical_contributions()` 从完整 `completion_batch` 创建 `complete_canonical / Eligible`，从完整 `expansion_batch` 创建 `expand_canonical / Pending`，从完整 `merge_resolution_batch` 创建 `merge_canonical / Eligible`；source retry 幂等、同 contribution id 不同 source fact 冲突。 | `tests/test_phase5_contribution_settlement_flow.py` |
| parent completion batch | `src/tokenshare/protocol_engine.py` | `ProtocolEngine.record_parent_completion()` 只在 owner unit 所有传入 required `ExpectedOutputRef` 都有完整 `merge_resolution_batch` 中的 matching `EXPECTED_OUTPUT_RESOLVED` 后，计算稳定 `resolved_output_set_digest`，并通过 `parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}` 按固定顺序写 owner `Processing -> Completed` 和 `expand_canonical Pending -> Eligible`。相同 resolved set retry 幂等，不同 resolved set 冲突。 | `tests/test_phase5_merge_resolution_flow.py::test_parent_completion_*` / `tests/test_phase5_contribution_settlement_flow.py::test_parent_completion_batch_completes_owner_and_promotes_expand_contribution` |
| root settlement batch | `src/tokenshare/protocol_engine.py` | `ProtocolEngine.record_root_settlement()` 从 ledger 当前 contribution 状态重建 root completion 时的全部 eligible contribution 集合，拒绝 partial settlement，也拒绝 caller supplied set 中多出的未落账 eligible contribution；settlement supplied set 必须精确等于 ledger 当前 eligible set。随后通过 artifact-backed `SettlementEntry[]` 写 `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}`，先写全部 `Eligible -> Settled`，最后写唯一 `SETTLEMENT_RECORDED` marker；已有 batch 重放时必须从 `settlement_entries_ref` 读取 artifact 并校验 digest、entry_count、reward total 和同 batch settled events。 | `tests/test_phase5_contribution_settlement_flow.py::test_root_completion_settles_all_eligible_contributions_in_one_batch` / `test_settlement_rejects_partial_settled_contributions` / `test_settlement_rejects_extra_supplied_contribution_not_in_ledger` / `test_settlement_requires_entries_artifact_ref` / `test_settlement_rejects_entries_artifact_digest_mismatch` / `test_settlement_entries_must_match_settled_contribution_events` |
| subtree pruning batch | `src/tokenshare/protocol_engine.py` | `ProtocolEngine.record_subtree_pruning()` 在 parent completion 后取消未完成 descendant units，保留 completed / canonical / settled units；batch 顺序为全部 `TASK_UNIT_STATE_CHANGED -> Cancelled` 后接 final `SUBTREE_PRUNED` marker；policy 必须有 plugin descriptor provenance 和 `MERGE_PLAN_RECORDED` source event。 | `tests/test_phase5_subtree_pruning_flow.py` |
| SQLite Phase 5 projection tables | `src/tokenshare/storage/sqlite_index.py` | 新增 `merge_task_links`、`merge_slot_bindings`、`merge_records`、`expected_output_resolutions`、`contributions`、`settlement_records`、`settlement_entries`、`subtree_prunes`。`merge_task_links` / `merge_slot_bindings` 只在 `MERGE_TASK_LINK_RECORDED` marker 后可见；`merge_records` / `expected_output_resolutions` 只在完整 `merge_resolution_batch` 后可见；`expected_output_refs` 会随 resolution 更新为 `resolved`。 | `tests/storage/test_phase5_event_projection.py::test_sqlite_rebuilds_merge_task_links_and_slot_bindings_only_after_marker` / `test_sqlite_rebuilds_merge_records_and_expected_output_resolutions_only_after_complete_batch` / `test_sqlite_updates_expected_output_refs_to_resolved` |
| SQLite settlement rebuild hardening | `src/tokenshare/storage/sqlite_index.py` | `settlement_entries` 必须从 `settlement_entries_ref` artifact rebuild；projection 校验 artifact 存在、content digest、entries digest、entry_count、reward total、same-batch `settlement_entry` event 一致，以及 `settlement_batch` id 必须等于 `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}`。 | `tests/storage/test_phase5_event_projection.py::test_sqlite_rebuilds_settlement_records_and_entries_only_after_marker` / `test_sqlite_rejects_settlement_without_entries_artifact` / `test_sqlite_rejects_settlement_entries_digest_or_event_mismatch` / `test_sqlite_rejects_settlement_batch_id_mismatch` |
| SQLite pruning / incomplete batch hardening | `src/tokenshare/storage/sqlite_index.py` | `subtree_prunes` 只在 final `SUBTREE_PRUNED` marker 后可见；rebuild 拒绝缺 descriptor provenance 的 pruning policy，并拒绝 incomplete 或 batch id 与 marker payload 不一致的 `merge_task_creation_batch`、`merge_resolution_batch`、`settlement_batch`、`subtree_pruning_batch`。 | `tests/storage/test_phase5_event_projection.py::test_sqlite_rebuilds_subtree_prunes_only_after_marker` / `test_sqlite_rejects_pruning_policy_without_descriptor_provenance` / `test_sqlite_rejects_merge_task_creation_batch_id_mismatch` / `test_sqlite_rejects_merge_resolution_batch_id_mismatch` / `test_sqlite_rejects_subtree_pruning_batch_id_mismatch` / `test_sqlite_rejects_incomplete_phase5_batches` |

## 2. 当前不包含的实现

以下内容不属于当前 Task 1-8 已实现范围：

- Phase 6 experimental plugins、Phase 7 实验级 AI API executor、独立实验基础设施 / fault simulation / metrics、真实网络 executor、生产级 AI API 平台 / 多租户 provider 管理、真实链上结算。
- `optional_slots`、partial merge、late optional output、re-merge、tree aggregation、`one_success` early terminal resolution 和 factorization early pruning。
- 真实支付 / 链上 settlement；当前 `SettlementRecord` 仍是本地 sandbox reward projection。

## 3. 当前源码文件清单

| 文件 | 当前真实内容 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/core/merge.py` | `RequiredSlotBinding`、`MergeTaskLink`、`MergeRecord`、`ExpectedOutputResolution`、canonical JSON digest helpers 和 required slot duplicate 校验 | Task 1 merge pure models。 |
| `src/tokenshare/core/contribution.py` | `ContributionState`、`ContributionRecord`、`SettlementEntry`、`SettlementRecord`、`SubtreePruneRecord`、`transition_contribution()`、sandbox reward helper 和 digest helpers | Task 1 contribution / settlement pure models and rules。 |
| `src/tokenshare/core/merge_coordinator.py` | `BatchView`、`MergeTaskCreationFlowResult`、`MergeCoordinator.create_ready_merge_tasks()`、staged merge input bundle、merge task link 与 merge task unit 组装 | Task 2 merge task creation batch。 |
| `src/tokenshare/protocol_engine.py` | `MergeResolutionFlowResult`、`ParentCompletionFlowResult`、`SettlementFlowResult`、`SubtreePruningFlowResult`、`ProtocolEngine.record_merge_resolution()`、`ProtocolEngine.record_parent_completion()`、`ProtocolEngine.record_root_settlement()`、`ProtocolEngine.record_subtree_pruning()`，以及对应 batch consistency helpers | Task 3 merge resolution batch；Task 5 parent completion batch；Task 6 root settlement batch；Task 7 subtree pruning batch。 |
| `src/tokenshare/storage/sqlite_index.py` | Phase 1-4 既有 materialized index；新增 Phase 5 projection context、Phase 5 query tables、`expected_output_refs` resolved overlay、settlement artifact rebuild、pruning provenance validation 和 incomplete Phase 5 batch rejection | Task 8 SQLite projection, integration, and code map。 |
| `src/tokenshare/storage/events.py` | 既有 Phase 1/2/3/4 event type；新增 Phase 5 event constants；`LedgerEvent.v1/v2` 和 `EventLedger.append()` / `append_batch()` 未因 Phase 5 改变 | Task 1 event constants。 |

## 4. 当前测试文件清单

| 文件 | 当前覆盖内容 | 本 map 处理 |
|---|---|---|
| `tests/core/test_phase5_models.py` | Phase 5 event constants、RequiredSlotBinding canonical output 来源、MergeTaskLink digest 稳定和 duplicate slot 拒绝、MergeRecord canonical commitment fields、ExpectedOutputResolution v1 merge record source、ContributionRecord 状态机、sandbox reward formula 和 digest 稳定性 | Task 1 红绿测试。 |
| `tests/test_phase5_merge_task_creation_flow.py` | merge task creation batch、TASK_EXPANDED 可见性 gate、required slot canonical-only 绑定、staged merge input bundle、incomplete batch / missing marker / conflict / idempotency 回归 | Task 2 红绿测试。 |
| `tests/test_phase5_merge_resolution_flow.py` | merge resolution batch、merge unit canonical gate、losing/late merge attempt 拒绝、required parent output resolution 覆盖、idempotency/conflict、incomplete batch inconsistency，以及 parent completion 的 all-required gate、partial 拒绝、resolved set digest/idempotency/conflict | Task 3 / Task 5 红绿测试。 |
| `tests/test_phase5_contribution_settlement_flow.py` | complete / expand / merge canonical contribution 创建、losing/canonical-loser/late/shadow attempt 排除、source retry 幂等、不同 source fact 冲突、incomplete expansion batch 拒绝、parent completion batch 中 `expand_canonical Pending -> Eligible` 推进、root settlement batch、entries artifact hardening、partial settlement 拒绝、extra supplied eligible contribution 拒绝、idempotency / conflict 和 zero budget | Task 4 / Task 5 / Task 6 红绿测试；2026-06-27 settlement hardening 回归测试。 |
| `tests/test_phase5_subtree_pruning_flow.py` | parent completion 后 subtree pruning batch、preserve completed / canonical / settled units、plugin-declared policy provenance、idempotency / conflict、缺 marker / descriptor provenance / policy source mismatch 拒绝 | Task 7 红绿测试。 |
| `tests/storage/test_phase5_event_projection.py` | Phase 5 SQLite projection tables、marker-gated visibility、complete batch-only visibility、settlement entries artifact rebuild hardening、pruning provenance hardening、incomplete Phase 5 batch rejection、错误 Phase 5 batch id 拒绝、merge -> parent completion -> root settlement integration projection | Task 8 红绿测试；2026-06-27 batch id hardening 回归测试。 |
| `tests/phase5_fixtures.py` | Phase 5 merge creation context、canonical output 类型变体、incomplete batch / marker-less batch 夹具 | Task 2 夹具。 |

## 5. 验证记录

- 开工前基线：`.\init.ps1` 通过，pytest collected 134 items，结果 `134 passed`。
- Task 1 原始红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py -q` 失败，原因是 `ModuleNotFoundError: No module named 'tokenshare.core.contribution'`。
- Task 1 补充红灯：同一定向命令失败，结果 `1 failed, 6 passed`；新增 `RequiredSlotBinding` 负例暴露 `canonical_output_ref` 显式非 canonical output 未被拒绝。
- Task 1 定向绿灯：同一定向命令通过，结果 `7 passed in 0.13s`。
- 状态同步后定向复验：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py -q` 通过，结果 `7 passed in 0.09s`。
- Task 2 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py tests\test_phase5_merge_task_creation_flow.py -q` 失败，原因是 `RequiredSlotBinding` 仍容忍 `candidate_output`。
- Task 2 定向绿灯：同一定向命令通过，结果 `16 passed in 0.92s`。
- 状态同步后完整启动验证：`.\init.ps1` 通过，pytest collected 151 items，结果 `151 passed in 6.25s`。
- Task 3 merge resolution 当前工作树验证：开工前 `.\init.ps1` 通过，pytest collected 158 items，结果 `158 passed in 6.17s`；Task 3 targeted 已随 Task 4 Phase 5 targeted suite 复验。
- Task 4 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_contribution_settlement_flow.py -q` 失败，原因是 `ImportError: cannot import name 'ContributionCoordinator'`。
- Task 4 补充红灯：`tests\test_phase5_contribution_settlement_flow.py::test_incomplete_expansion_batch_does_not_create_contribution` 失败，原因是 incomplete expansion batch 只返回 generic incomplete batch error。
- Task 4 定向绿灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_contribution_settlement_flow.py -q` 通过，结果 `7 passed in 0.85s`。
- Task 4 Phase 5 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py -q` 通过，结果 `31 passed in 2.68s`。
- Task 4 完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 165 items，结果 `165 passed in 6.86s`。
- Task 5 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py -q` 失败，新增 parent completion 测试因 `ProtocolEngine` 缺少 `record_parent_completion()` 出现 5 个预期失败，原有 14 个目标测试通过。
- Task 5 定向绿灯：同一 targeted 命令通过，结果 `19 passed in 2.67s`。
- Task 5 完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 170 items，结果 `170 passed in 7.28s`。
- Task 6 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_contribution_settlement_flow.py -q` 失败，新增 11 个 root settlement 测试因 `ProtocolEngine` 缺少 `record_root_settlement()` 失败，原有 8 个目标测试通过。
- Task 6 定向绿灯：同一 targeted 命令通过，结果 `19 passed in 3.38s`。
- Task 6 Phase 5 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py -q` 通过，结果 `47 passed in 5.92s`。
- Task 6 状态同步前完整启动验证：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 181 items，结果 `181 passed in 10.32s`。
- Task 7 subtree pruning 定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_subtree_pruning_flow.py -q` 已在当前工作树通过；完整 Phase 5 targeted suite 随 Task 8 复验。
- Task 8 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_phase5_event_projection.py -q` 初次失败，暴露缺少 Phase 5 SQLite tables、`expected_output_refs` 未随 resolution 更新，以及 settlement / pruning / incomplete batch rebuild hardening 缺口。
- Task 8 定向绿灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_phase5_event_projection.py -q` 通过，结果 `17 passed in 5.71s`。
- Task 8 Phase 5 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py tests\test_phase5_subtree_pruning_flow.py tests\storage\test_phase5_event_projection.py -q` 通过，结果 `66 passed in 14.17s`。
- Task 8 完整验证：`conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"` 通过，输出 `feature-list-json-ok`；`conda run -n tokenshare python -m compileall -x "reference_repos" .` 通过；`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests` 通过，结果 `207 passed in 26.37s`；`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，结果 `207 passed in 22.06s`；状态同步后再次运行 `.\init.ps1` 通过，结果 `207 passed in 22.43s`。
- 2026-06-27 hardening 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_phase5_event_projection.py::test_sqlite_rejects_merge_resolution_batch_id_mismatch tests\test_phase5_contribution_settlement_flow.py::test_settlement_rejects_extra_supplied_contribution_not_in_ledger -q` 失败，两个新增用例均为 `DID NOT RAISE`，确认错误 `merge_resolution_batch` id 和 extra supplied settlement contribution 未被拒绝。
- 2026-06-27 hardening 定向绿灯：新增 settlement / pruning batch id mismatch projection 用例通过，结果 `2 passed in 0.65s`；Phase 5 定向套件 `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase5_models.py tests\test_phase5_merge_task_creation_flow.py tests\test_phase5_merge_resolution_flow.py tests\test_phase5_contribution_settlement_flow.py tests\test_phase5_subtree_pruning_flow.py tests\storage\test_phase5_event_projection.py -q` 通过，结果 `77 passed in 17.16s`。
- 2026-06-27 hardening 完整启动验证：`.\init.ps1` 通过，pytest collected 211 items，结果 `211 passed in 25.07s`。
