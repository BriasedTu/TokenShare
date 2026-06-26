# Phase 5 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-26 |
| 对应 feature | `feat-006` - Phase 5 - Merge, Contribution, and Sandbox Settlement |
| 上游规格 | `Doc/TechnicalDocument/2026-06-25-phase-5-merge-contribution-settlement-field-spec.md` |
| 当前范围 | Task 1 - pure models, event constants, state rules；Task 2 - merge task creation batch |
| 目的 | 说明 Phase 5 已实现的纯对象、digest helper、event constants、contribution 状态规则、merge task creation coordinator、Task 2 batch 语义和测试映射；后续 merge resolution、contribution、settlement、pruning、SQLite projection、集成和 Phase 6 不计入当前已实现范围。 |

## 1. 当前实现范围

当前 Phase 5 已完成 Task 1 和 Task 2，覆盖以下基础能力：

- `src/tokenshare/core/merge.py`：merge 相关纯对象和稳定 digest helper。
- `src/tokenshare/core/contribution.py`：contribution / settlement / subtree pruning 纯对象、contribution 状态迁移规则和 sandbox settlement entries helper。
- `src/tokenshare/core/merge_coordinator.py`：merge task creation coordinator、batch 可见性 gate、staged merge input bundle 和 merge task link 组装。
- `src/tokenshare/storage/events.py`：Phase 5 event type constants。
- `tests/core/test_phase5_models.py`：Task 1 红绿测试。
- `tests/test_phase5_merge_task_creation_flow.py`：Task 2 红绿测试。
- `tests/phase5_fixtures.py`：Task 2 夹具扩展，包含完整 / 不完整 expansion batch、标记可见性和 canonical output 类型变体。

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

## 2. 当前不包含的实现

以下内容不属于当前 Task 1 + Task 2 已实现范围：

- `ProtocolEngine` / coordinator merge resolution flow。
- `MERGE_RECORDED`、`EXPECTED_OUTPUT_RESOLVED`、`CONTRIBUTION_STATE_CHANGED`、`SETTLEMENT_RECORDED`、`SUBTREE_PRUNED` 的实际 append / batch flow。
- SQLite Phase 5 projection、rebuild inconsistency 检查和查询表。
- Phase 5 integration tests、Phase 6 experiments、真实网络 executor、生产 AI API、真实链上结算。

## 3. 当前源码文件清单

| 文件 | 当前真实内容 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/core/merge.py` | `RequiredSlotBinding`、`MergeTaskLink`、`MergeRecord`、`ExpectedOutputResolution`、canonical JSON digest helpers 和 required slot duplicate 校验 | Task 1 merge pure models。 |
| `src/tokenshare/core/contribution.py` | `ContributionState`、`ContributionRecord`、`SettlementEntry`、`SettlementRecord`、`SubtreePruneRecord`、`transition_contribution()`、sandbox reward helper 和 digest helpers | Task 1 contribution / settlement pure models and rules。 |
| `src/tokenshare/core/merge_coordinator.py` | `BatchView`、`MergeTaskCreationFlowResult`、`MergeCoordinator.create_ready_merge_tasks()`、staged merge input bundle、merge task link 与 merge task unit 组装 | Task 2 merge task creation batch。 |
| `src/tokenshare/storage/events.py` | 既有 Phase 1/2/3/4 event type；新增 Phase 5 event constants；`LedgerEvent.v1/v2` 和 `EventLedger.append()` / `append_batch()` 未因 Task 1/2 改变 | Task 1 event constants。 |

## 4. 当前测试文件清单

| 文件 | 当前覆盖内容 | 本 map 处理 |
|---|---|---|
| `tests/core/test_phase5_models.py` | Phase 5 event constants、RequiredSlotBinding canonical output 来源、MergeTaskLink digest 稳定和 duplicate slot 拒绝、MergeRecord canonical commitment fields、ExpectedOutputResolution v1 merge record source、ContributionRecord 状态机、sandbox reward formula 和 digest 稳定性 | Task 1 红绿测试。 |
| `tests/test_phase5_merge_task_creation_flow.py` | merge task creation batch、TASK_EXPANDED 可见性 gate、required slot canonical-only 绑定、staged merge input bundle、incomplete batch / missing marker / conflict / idempotency 回归 | Task 2 红绿测试。 |
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
