# Phase 4 代码与文档对应关系

| 项 | 值 |
|---|---|
| 日期 | 2026-06-24 |
| 对应 feature | `feat-005` - Phase 4 - Verification, Canonical Output, and Expansion |
| 上游规格 | `Doc/TechnicalDocument/2026-06-24-phase-4-verification-canonical-expansion-field-spec.md` |
| 上游讨论 | `Doc/TechnicalDocument/2026-06-24-phase-4-discussion-notes.md` |
| 目的 | 说明当前已实现的 Phase 4 Task 1/2/3/4/5/6/7/8/9/10 分别对应哪些事件 ledger、纯对象、状态机、ProtocolEngine flow、SQLite index-only projection、集成验证和测试边界；Phase 5 merge/settlement 不计入已实现范围。 |

## 1. 总体对应关系

当前 Phase 4 实现覆盖十块基础能力：

- Task 1：`LedgerEvent.v2` batch envelope 和 `EventLedger.append_batch()`。
- Task 2：verification / canonical / expansion 纯对象与纯校验 helper。
- Task 3：Attempt 和 TaskUnit 的最小状态机扩展。
- Task 4：`ProtocolEngine.record_verification()` verification flow。
- Task 5：`ProtocolEngine.bind_canonical_outputs()` canonical binding flow。
- Task 6：`ProtocolEngine.record_split_strategy_invocation()` split invocation audit flow。
- Task 7：`ProtocolEngine.record_complete_decision()` accepted complete path。
- Task 8：`ProtocolEngine.record_expand_decision()` accepted expand path atomic graph update。
- Task 9：SQLite Phase 4 index-only projection 和 completion / expansion batch 语义完整性检查。
- Task 10：Phase 4 code map、进度同步和最终集成验证。

| 规格内容 | 代码位置 | 说明 | 主要测试 |
|---|---|---|---|
| Phase 4 `EventType` | `src/tokenshare/storage/events.py` | 新增 `VERIFICATION_RECORDED`、`CANONICAL_OUTPUTS_BOUND`、`SPLIT_STRATEGY_INVOCATION_RECORDED`、`DECOMPOSITION_PROPOSAL_RECORDED`、`EXPANSION_DECISION_RECORDED`、`MERGE_PLAN_RECORDED`、`TASK_EXPANDED` 常量；只提供稳定 spelling，不实现 flow。 | `tests/storage/test_phase4_event_ledger_batch.py` |
| `LedgerEvent.v2` batch envelope | `src/tokenshare/storage/events.py` | 新增 `batch_id`、`batch_index`、`batch_size`；`LedgerEvent.v1` 读取和 hash 重算保持 schema-aware，不向历史 v1 hash 输入注入 v2 null batch fields。 | `tests/storage/test_phase4_event_ledger_batch.py`、`tests/storage/test_event_ledger.py` |
| `EventDraft` / `EventLedger.append_batch()` | `src/tokenshare/storage/events.py` | 作为 complete / expand 的本地原子 ledger append 基础设施；提供整批预校验、连续 event_seq/hash chain、identical retry 和 partial existing batch 拒绝。语义层 batch 完整性仍留给后续 replay/projection。 | `tests/storage/test_phase4_event_ledger_batch.py` |
| `VerificationReport` / `build_verification_report()` | `src/tokenshare/core/verification.py` | `eligible_for_canonical` 只从 report status 和 required layer status 派生；构造期拒绝非法 report status、非法 layer status、缺 required layer，以及 caller-provided eligible 绕过。 | `tests/core/test_phase4_models.py` |
| `CanonicalSelection` / `select_first_verified_bundle()` | `src/tokenshare/core/verification.py` | 实现 `first_verified_bundle` 的纯选择规则，按 eligible verification event sequence 排序，不读取或写入 ledger。 | `tests/core/test_phase4_models.py` |
| `SplitStrategyInvocation` / `SplitStrategyResult` | `src/tokenshare/core/expansion.py` | 校验 invocation status、successful result 摘要字段，以及 result 的 `complete` / `expand` 互斥 body；不调用插件、不保存 artifact。 | `tests/core/test_phase4_models.py` |
| `DecompositionProposal` | `src/tokenshare/core/expansion.py` | 校验六块 proposal 结构、required fields、child/edge/slot/expected output 引用、重复 target input、环、promotion guard。字段与引用校验在构造期完成；需要 graph/config 的 child count、depth、total unit 和 parent required output 覆盖由纯 helper 处理。 | `tests/core/test_phase4_models.py` |
| `validate_decomposition_proposal_limits()` | `src/tokenshare/core/expansion.py` | 用外部传入的 `ProtocolConfig`、parent depth、existing unit count、parent required outputs、strategy child limit 做上下文限制校验；不接触 `ProtocolEngine`、ledger 或 SQLite。 | `tests/core/test_phase4_models.py` |
| `ExpansionDecision` | `src/tokenshare/core/expansion.py` | 校验 accepted `complete` / `expand` decision 的互斥字段和必需引用；`expand.action_body` 顶层只能包含 `expand_evidence`，且 `expand_evidence` 必须包含 proposal / merge plan refs 与 child / relation / expected output / required slot counts；不验证 source invocation / canonical selection / descriptor digest，这些属于后续 flow。 | `tests/core/test_phase4_models.py` |
| `MergePlan` | `src/tokenshare/core/expansion.py` | 保存插件 merge policy 的实例契约；校验固定七块、header / merge_policy_ref / required slot / parent output mapping / hash recording / merge validation / plugin_payload 必填字段；Phase 4 只允许 required slots，不支持 optional slots。 | `tests/core/test_phase4_models.py` |
| `ExpectedOutputRef` | `src/tokenshare/core/expansion.py` | 从 accepted proposal 与 final `TASK_EXPANDED.event_seq` 派生 output future；ID 从 proposal、owner unit、output name、logical position 派生，`created_event_seq` 只作为可见边界字段；无法解析的 child output 或缺失 merge plan 会失败。 | `tests/core/test_phase4_models.py` |
| child initial state derivation | `src/tokenshare/core/expansion.py` | 根据未满足 `depends_on_output` 入边和 parent/canonical artifact input binding 派生 `Ready` / `Blocked`；缺失 parent canonical output 直接拒绝；同一 target input 不能同时由 binding 和 dependency edge 解析；插件 payload 不得指定 state / output resolution authority。 | `tests/core/test_phase4_models.py` |
| Phase 4 Attempt / TaskUnit transitions | `src/tokenshare/core/state_machines.py` | 开放 `Attempt.Submitted -> Verified`、`Attempt.Submitted -> Rejected`、`Attempt.Verified -> Canonical`、`TaskUnit.Processing -> Completed`；继续禁止 verification error 的 `Submitted -> Submitted` 自循环。 | `tests/core/test_state_machines.py` |
| `ProtocolEngine.record_verification()` | `src/tokenshare/protocol_engine.py` | 写 `VERIFICATION_RECORDED`；在落账前重建并校验 `VerificationReport` 派生 eligibility、bundle digest 和 attempt 绑定；`passed` / `accepted` 推进 `Attempt.Submitted -> Verified`，`rejected` 推进 `Submitted -> Rejected`，`error` 只写 report event。 | `tests/test_phase4_verification_flow.py` |
| `ProtocolEngine.bind_canonical_outputs()` | `src/tokenshare/protocol_engine.py` | 第一版只支持 `first_verified_bundle`；要求传入的 verification events 已在当前 ledger 落账且 `event_id` / `event_seq` / hash 匹配；从同 task/unit 且 eligible 的 `VERIFICATION_RECORDED` 中按 `event_seq` 选择最早 report，写唯一 `CANONICAL_OUTPUTS_BOUND`，selected attempt 推进 `Verified -> Canonical`，losing attempts 不改状态；已有同 task/unit canonical event 时同承诺幂等返回，不同承诺报冲突。 | `tests/test_phase4_canonical_flow.py` |
| `ProtocolEngine.record_split_strategy_invocation()` | `src/tokenshare/protocol_engine.py` | 只写 `SPLIT_STRATEGY_INVOCATION_RECORDED` 审计事件；`failed` / `invalid_result` 不写 decision、proposal、merge plan、TaskUnit/Attempt 状态或 graph mutation；`succeeded` 只保存 invocation 摘要、`result_action`、`result_digest`、错误摘要字段，不内联完整 `SplitStrategyResult` body。 | `tests/test_phase4_split_invocation_flow.py` |
| `ProtocolEngine.record_complete_decision()` | `src/tokenshare/protocol_engine.py` | 只接受 `action=complete`；写前从 ledger 和 descriptor artifact 校验 succeeded invocation、`CANONICAL_OUTPUTS_BOUND`、canonical bundle digest、plugin descriptor digest、descriptor 中声明的 `split_strategy_id`、params digest 和 scope 一致；按 canonical selection 的 `selected_verification_event_seq` 精确绑定 selected report / submission / attempt / bundle / validator policy；通过 `append_batch()` 写 `completion_batch:{expansion_decision_id}`，batch 第 1 条为 `EXPANSION_DECISION_RECORDED`，第 2 条为 `TASK_UNIT_STATE_CHANGED current -> Completed`；complete path 不写 proposal、merge plan、`TASK_EXPANDED` 或 child graph events。 | `tests/test_phase4_complete_flow.py` |
| `ProtocolEngine.record_expand_decision()` | `src/tokenshare/protocol_engine.py` | 只接受 `action=expand` 且 parent `TaskUnit` 必须是 `Processing`；先保存 proposal / merge plan staged artifacts，再从 ledger、canonical event 和 frozen descriptor artifact 校验 invocation、canonical selection、descriptor digest、strategy id、params digest 和 scope一致；复算 proposal / merge plan body digest，校验 `expand_evidence` counts 和 merge policy descriptor provenance 后构造 deterministic child `TaskUnit` / `TaskRelation`、派生 child 初始 state 和 `ExpectedOutputRef`，并通过 `append_batch()` 按 proposal、decision、merge plan、child units、child relations、`TASK_EXPANDED` 顺序原子落账。 | `tests/test_phase4_expand_flow.py` |
| canonical output replay/view injection | `src/tokenshare/core/task_graph.py` | `TaskGraph.canonical_outputs_by_unit_id` 是重建视图的 canonical output 注入入口；dependency satisfaction 优先使用该映射而不是 flow 返回的临时 `TaskUnit.canonical_output_refs`。 | `tests/test_phase4_canonical_flow.py`、`tests/core/test_task_graph.py` |
| SQLite Phase 4 index-only projection | `src/tokenshare/storage/sqlite_index.py` | `ledger_events` 投影暴露 `batch_id`、`batch_index`、`batch_size`；新增 `verification_reports`、`canonical_outputs`、`split_strategy_invocations`、`decomposition_proposals`、`expansion_decisions`、`merge_plans`、`expected_output_refs` 表。`canonical_outputs` 对 `(task_id, unit_id)` 唯一，重建时遇到不同 canonical commitment 报冲突；batch rebuild 校验 `batch_index` / `batch_size` / `event_seq` 连续且顺序一致；`completion_batch:{expansion_decision_id}` 必须是同 task/unit 的 decision + Completed state change；`expansion_batch:{expansion_decision_id}` 必须以 proposal、decision、merge plan、child units、child relations、final `TASK_EXPANDED` 成批出现，并校验 marker child/relation ids、merge plan decision id；proposal / merge plan / expected output refs 只有 final marker 存在才可见，缺少 artifact store 时不得静默丢失 expected output refs。 | `tests/storage/test_phase4_event_projection.py`、`tests/storage/test_sqlite_index.py`、`tests/storage/test_phase2_event_projection.py`、`tests/storage/test_phase3_event_projection.py` |

## 2. 当前不包含的实现

以下内容不属于当前 Phase 4 已实现范围：

- 独立 replay engine 中的 Phase 4 状态重放；当前只实现 SQLite index-only projection 的 rebuild 检查和查询表。
- Phase 5 merge、contribution、settlement。
- 真实网络 executor 或生产 AI API。

## 3. Review hardening 记录

2026-06-24 对 Task 1/2/3 的复查发现并修复了以下实现缺口：

- `DecompositionProposal` 不能只校验 schema version，必须拒绝缺少 required header / child / edge / expected output / merge slot 字段和无效引用。
- 需要纯 helper 覆盖 child count、depth、total unit 和 parent required output 覆盖这些带上下文的 proposal 限制。
- Phase 4 `EventType` 常量必须在 enum 中声明，不能依赖裸字符串。
- `ExpectedOutputRef` 不能静默生成 `child_unit_id=None` 的 child output，也不能在 `merge_plan_id=None` 时生成 merge output；ID 不得包含 `created_event_seq`。
- `VerificationReport` 必须拒绝非法 report status 和非法 layer status。
- completion batch 测试样例中的 TaskUnit 完成状态必须是 `Processing -> Completed`，不能使用 Attempt 的 `Submitted` 作为 `from_state`。

2026-06-25 对完整 Phase 4 的二次子线程复查发现并修复了以下实现缺口：

- `CanonicalSelection` 只能绑定当前 ledger 中已落账的 verification event，不能接受调用方伪造或来自其他 ledger 的 event。
- complete evidence 必须按 `selected_verification_event_seq` 精确绑定 selected report、submission、attempt、bundle 和 validator policy。
- `proposal_digest` / `merge_plan_digest` 必须复算 canonical body digest；计算时排除自引用 id/digest 字段。
- `ExpansionDecision(action=expand).action_body` 顶层必须只包含 `expand_evidence`；`expand_evidence` 必须使用固定字段，并由 engine 校验 count 摘要与 proposal / merge plan 一致。
- `MergePlan` 必须校验固定七块 schema、merge policy descriptor provenance、required slot、parent output mapping、hash recording、merge validation 和 plugin payload 字段。
- child input 不能同时由 input binding 与 dependency edge 双重解析；缺失 parent canonical output 时 proposal/child state 派生必须失败。
- `plugin_payload` 不得携带 state、canonical output 或 expected output resolution authority。
- SQLite projection 必须校验 batch `event_seq` 连续、completion state change 同 task/unit、`TASK_EXPANDED` marker 的 child/relation ids、merge plan decision id，并且缺少 artifact store 时不能静默丢失 `expected_output_refs`。

## 4. 当前源码文件清单

| 文件 | 当前真实内容 | 本 map 处理 |
|---|---|---|
| `src/tokenshare/storage/events.py` | Phase 1/2/3/4 event type；`LedgerEvent.v1/v2`；`EventDraft`；`EventLedger.append()` / `append_batch()`；hash chain verify | Task 1 ledger batch foundation 和 Phase 4 event constants。 |
| `src/tokenshare/core/verification.py` | `VerificationReport`、`CanonicalSelection`、`build_verification_report()`、`select_first_verified_bundle()`、`digest_json()` 和 layer invariant helper | Task 2 verification/canonical pure object rules。 |
| `src/tokenshare/core/expansion.py` | `SplitStrategyInvocation`、`SplitStrategyResult`、`DecompositionProposal`、`ExpansionDecision`、`MergePlan`、`ExpectedOutputRef`、`derive_child_initial_state()`、`validate_decomposition_proposal_limits()` | Task 2 expansion pure object rules。 |
| `src/tokenshare/core/state_machines.py` | TaskUnit/Lease/Attempt transitions；Phase 3 submission；Phase 4 verification/canonical/complete transitions | Task 3 state-machine changes。 |
| `src/tokenshare/protocol_engine.py` | Phase 2 scheduling/heartbeat/expiry flow；Phase 3 registry/request/submission flow；Phase 4 verification/canonical binding flow、split invocation audit flow、accepted complete path、accepted expand path；helper 可从 verification/canonical/split invocation event payload 和 descriptor artifact 重建前置事实，并构造 deterministic child graph batch | Task 4 / Task 5 / Task 6 / Task 7 / Task 8 flow。 |
| `src/tokenshare/core/task_graph.py` | TaskGraph rebuildable view；`canonical_outputs_by_unit_id` 注入 canonical output projection-like facts | Task 5 replay/view boundary。 |
| `src/tokenshare/storage/sqlite_index.py` | Phase 1/2/3/4 SQLite index-only projection；Phase 4 batch envelope 可见性、canonical conflict 检查、completion / expansion batch semantic consistency、accepted expansion-derived row gating 和 `ExpectedOutputRef` projection | Task 9 projection。 |

## 5. 当前测试文件清单

| 文件 | 当前覆盖内容 | 本 map 处理 |
|---|---|---|
| `tests/storage/test_phase4_event_ledger_batch.py` | v1 hash 兼容、v2 null batch fields、append_batch、completion batch、conflicting retry 不追加、Phase 4 EventType constants、TaskUnit `Processing -> Completed` 样例 | Task 1 + review hardening。 |
| `tests/core/test_phase4_models.py` | VerificationReport eligibility/invariants、schema version 固定、first verified selection、split result、expand evidence、proposal schema/reference/limit checks、child state derivation、MergePlan 七块 schema、ExpectedOutputRef | Task 2 + review hardening。 |
| `tests/core/test_state_machines.py` | Attempt `Submitted -> Verified/Rejected`、`Verified -> Canonical`、禁止 self-loop/direct canonical、TaskUnit `Processing -> Completed` | Task 3。 |
| `tests/core/test_task_graph.py` | Phase 2 graph invariants；本轮作为 expanded targeted verification 防回归 | 间接回归。 |
| `tests/storage/test_event_ledger.py` | Phase 1 ledger append/idempotency/hash behavior；本轮作为 storage targeted verification 防回归 | 间接回归。 |
| `tests/test_phase4_verification_flow.py` | `record_verification()` 写 report event、Submitted -> Verified/Rejected、`status=error` 无 attempt state change、caller-forged eligibility 拒绝、invalid candidate output 不 eligible | Task 4。 |
| `tests/test_phase4_canonical_flow.py` | `bind_canonical_outputs()` 只接受本 ledger 已记录 verification event、按 verification `event_seq` 选择 first verified bundle、canonical per task/unit 唯一、late report 不覆盖、losing attempt 保持 `Verified`、TaskGraph 通过 canonical projection 注入满足依赖 | Task 5。 |
| `tests/test_phase4_split_invocation_flow.py` | `record_split_strategy_invocation()` 的 failed / invalid_result audit-only、succeeded audit-only、只保存摘要/digest 而不内联完整 result body；确认不写 decision、proposal、merge plan、TaskUnit/Attempt state 或 child graph events | Task 6。 |
| `tests/test_phase4_complete_flow.py` | `record_complete_decision()` 的 completion batch 顺序、complete path 不写 proposal/merge plan/`TASK_EXPANDED`/child events、missing/failed invocation 拒绝、canonical/scope/descriptor/strategy mismatch 拒绝、同 payload batch 幂等和不同 payload 冲突、`completion_evidence` 内联并绑定 selected verification event/report/policy | Task 7。 |
| `tests/test_phase4_expand_flow.py` | `record_expand_decision()` 的 expansion batch 顺序、`TASK_EXPANDED` final marker、invalid proposal 无权威 event / graph mutation、staged artifact 非权威、同 payload 幂等、同 scope 不同 proposal/merge digest 冲突、missing/failed invocation 拒绝、canonical/descriptor/strategy/params mismatch 拒绝、parent Processing 要求、proposal/merge plan body digest、expand evidence count、merge policy descriptor digest、child unit/relation deterministic ID 和 child 初始 state 派生 | Task 8。 |
| `tests/storage/test_phase4_event_projection.py` | SQLite Phase 4 projection tables、split invocation audit rows、expansion-derived rows 只在 final `TASK_EXPANDED` 后可见、duplicate canonical conflict、incomplete completion / expansion batch inconsistency、batch event_seq 连续性、completion state 同 task/unit、TASK_EXPANDED marker child/relation id 一致、缺 artifact store 不丢 expected refs、accepted proposal + `TASK_EXPANDED` 派生 `expected_output_refs` | Task 9。 |

## 6. 验证记录

- Task 2/3 原始红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase4_models.py tests\core\test_state_machines.py -q` 失败，原因是 `tokenshare.core.verification` 尚不存在。
- Task 1 原始红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_phase4_event_ledger_batch.py -q` 失败，原因是 batch fields、`EventDraft` 和 `append_batch()` 尚不存在。
- 2026-06-24 review hardening 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase4_models.py tests\storage\test_phase4_event_ledger_batch.py -q` 失败，结果 `13 failed, 18 passed`；失败覆盖非法 verification status/layer status、proposal 缺字段和无效引用、缺 limit helper、ExpectedOutputRef `logical_position` / invalid ref、Phase 4 EventType 常量缺失、completion batch `Submitted -> Completed` 样例。
- 2026-06-24 review hardening 绿灯：同一命令通过，结果 `31 passed in 0.13s`。
- 2026-06-24 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase4_models.py tests\core\test_state_machines.py tests\core\test_task_graph.py tests\storage\test_event_ledger.py tests\storage\test_phase4_event_ledger_batch.py -q` 通过，结果 `42 passed in 0.16s`。
- 2026-06-24 完整启动验证：`.\init.ps1` 通过，pytest collected 64 items，结果 `64 passed in 0.74s`。
- 2026-06-25 Task 4/5 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_verification_flow.py tests\test_phase4_canonical_flow.py -q` 失败，结果 `13 failed`，失败原因集中在 `ProtocolEngine.record_verification()` 不存在。
- 2026-06-25 Task 4/5 绿灯：同一命令通过，结果 `14 passed in 0.34s`。
- 2026-06-25 Task 4/5 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_verification_flow.py tests\test_phase4_canonical_flow.py tests\test_phase3_execution_flow.py tests\core\test_task_graph.py -q` 通过，结果 `18 passed in 0.36s`。
- 2026-06-25 完整启动验证：`.\init.ps1` 通过，pytest collected 78 items，结果 `78 passed in 0.95s`。
- 2026-06-25 Task 6/7 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_split_invocation_flow.py tests\test_phase4_complete_flow.py -q` 失败，结果 `14 failed`，失败原因集中在 `ProtocolEngine.record_split_strategy_invocation()` 和 `ProtocolEngine.record_complete_decision()` 不存在。
- 2026-06-25 Task 6/7 绿灯：同一命令通过，结果 `14 passed in 0.78s`。
- 2026-06-25 Task 6/7 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_split_invocation_flow.py tests\test_phase4_complete_flow.py tests\test_phase4_canonical_flow.py tests\storage\test_phase4_event_ledger_batch.py -q` 通过，结果 `26 passed in 1.01s`。
- 2026-06-25 Task 6/7 完整启动验证：`.\init.ps1` 通过，pytest collected 92 items，结果 `92 passed in 1.71s`。
- 2026-06-25 Task 8 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_expand_flow.py -q` 失败，结果 `14 failed`，失败原因集中在 `ProtocolEngine.record_expand_decision()` 不存在。
- 2026-06-25 Task 8 绿灯：同一命令通过，结果 `14 passed in 1.14s`。
- 2026-06-25 Task 8 扩展定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_expand_flow.py tests\test_phase4_complete_flow.py tests\test_phase4_canonical_flow.py tests\core\test_task_graph.py -q` 通过，结果 `31 passed in 2.00s`。
- 2026-06-25 Task 8 完整启动验证：`.\init.ps1` 通过，pytest collected 106 items，结果 `106 passed in 3.02s`。
- 2026-06-25 Task 9 红灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_phase4_event_projection.py -q` 失败，结果 `7 failed`；失败原因是 Phase 4 SQLite tables、`artifact_store` 参数、duplicate canonical conflict 和 incomplete batch 检查尚不存在。
- 2026-06-25 Task 9 绿灯：同一命令通过，结果 `7 passed in 1.30s`。
- 2026-06-25 Task 9 定向验证：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\storage\test_sqlite_index.py tests\storage\test_phase2_event_projection.py tests\storage\test_phase3_event_projection.py tests\storage\test_phase4_event_projection.py -q` 通过，结果 `10 passed in 1.92s`。
- 2026-06-25 Task 10 完整集成验证：`.\init.ps1` 通过，pytest collected 113 items，结果 `113 passed in 4.35s`。
- 2026-06-25 Phase 4 hardening 红灯：complete/expand digest/provenance 定向套件新增 4 个预期失败；第二轮 hardening 定向套件新增 17 个目标失败；canonical caller-payload mutation 负例失败并错误选择 `attempt_tampered`。
- 2026-06-25 Phase 4 hardening 绿灯：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\test_phase4_complete_flow.py tests\test_phase4_expand_flow.py -q` 通过，结果 `28 passed in 1.92s`；hardening 定向通过，结果 `86 passed in 4.08s`；Phase 4 全相关定向通过，结果 `105 passed in 4.27s`；追加 canonical tamper 修复后相关定向通过，结果 `102 passed in 3.73s`；状态同步前 `.\init.ps1` 通过，pytest collected 132 items，结果 `132 passed in 5.82s`；状态同步后最终 `.\init.ps1` 通过，pytest collected 133 items，结果 `133 passed in 5.41s`。
- 2026-06-25 独立复验补强：三个只读子代理分别复审 ProtocolEngine verification/canonical/complete、pure models/expand、storage ledger/projection。storage 定向 `20 passed in 1.59s` 且无 P1/P2；ProtocolEngine 定向 `29 passed in 1.10s` 且无 P1/P2；pure models/expand 定向 `47 passed in 1.22s` 并发现 1 个 P2：`expand.action_body` 顶层可夹带额外字段。红灯 `$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\core\test_phase4_models.py::test_expansion_decision_rejects_expand_action_body_extra_fields -q` 失败，结果 `Failed: DID NOT RAISE`；修复后同一测试 `1 passed in 0.10s`，expand/model 定向 `48 passed in 1.27s`，完整 `.\init.ps1` 通过，pytest collected 134 items，结果 `134 passed in 4.88s`。
