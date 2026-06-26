# Phase 5 Merge 讨论记录

日期：2026-06-25

状态：Phase 5 设计讨论记录。本文记录已经确认的 Phase 5 merge 主闭环决策和后续待讨论问题；本文不是最终字段规格，也不是实现计划。后续需要把确认后的字段、事件、SQLite projection 和 TDD 任务收束到 Phase 5 专用字段规格。

## 1. 已确认主轴

Phase 5 第一版以 **merge 主闭环** 为核心。

贡献和结算先只做最小可审计版本，不在第一版设计中扩大为完整激励系统。第一版重点是证明：child canonical outputs 如何按 `MergePlan` 合并回父节点，父节点 output resolution 如何被权威事件推进，以及根任务完成后如何生成一次性 sandbox settlement。

## 1.1 讨论方法

Phase 5 后续每个设计点都先查已有系统经验，再做 TokenShare 取舍。

执行要求：

1. 先查本地已落库参考仓库、主 TDD、P01-P22 候选机制记录和相关官方资料。
2. 如果联网资料会影响 Phase 5 设计、字段、事件、测试或文档，必须按 `Doc/agent-navigation.md` 的外部参考资料落库规则，先写入本地摘要和影响范围。
3. 讨论记录中区分三类内容：外部系统事实、对 TokenShare 的影响、当前建议 / 已确认决策。
4. 未经用户确认的推荐只能标为“建议”或“待确认”，不能写成已确认设计。

## 2. 已确认 merge 模式

采用普通 merge `TaskUnit` 模式。

流程口径：

1. `MergeCoordinator` 扫描 accepted `MergePlan`。
2. 只有所有 `required_slots` 都有 canonical child output 时，merge readiness 才成立。
3. readiness 成立后创建一个 merge `TaskUnit`。
4. merge `TaskUnit` 的输入是稳定的 `merge_input_bundle` artifact。
5. merge `TaskUnit` 正常走 `ExecutionRequest -> ExecutionSubmission -> VerificationReport -> CANONICAL_OUTPUTS_BOUND` 生命周期。
6. `MERGE_RECORDED` 记录 merge 事实、slot 覆盖、child canonical output hashes、merge policy identity、merge output digest 和 canonical selection。
7. `ExpectedOutputRef` 根据 merge canonical output 进入 resolved。
8. parent unit 之后才能完成或继续向上 merge。
9. `ContributionRecord` / `SettlementRecord` 第一版只做最小可审计：记录哪些 canonical / expand / merge 事实对最终 root 完成有贡献，并在根完成后一次性 sandbox settlement。

采用该模式的理由：

- 不引入协议内特权 merge 计算路径。
- merge 的失败、重试、迟到、验证、canonical、贡献和 replay 语义与普通任务一致。
- 事件数量会增加，但换来统一审计边界，符合 TokenShare V1 验证协议闭环的目标。
- 领域合并算法仍属于插件 `MergePolicy`；协议核心只编排 merge readiness、input bundle、slot/hash 审计和事件记录。

## 3. 已确认创建时机

采用 A 方案：**required slots 齐备后再创建 merge `TaskUnit`**。

第一版不在 Phase 4 `expand` batch 中预创建 blocked merge `TaskUnit`。

理由：

- Phase 4 `expand` batch 顺序已经冻结为 proposal、decision、merge plan、child units、child relations、final `TASK_EXPANDED`，Phase 5 不回改该边界。
- `MergePlan` 在 Phase 4 只是未来合并契约；真正 merge work 只有在 required child canonical outputs 齐备后才成为可执行任务。
- 避免 blocked merge task 提前进入任务图，减少 scheduler、projection 和 recovery 的额外状态分支。
- projection / replay 仍可解释 merge task 的来源：它由 accepted `MergePlan`、slot canonical facts 和 `MergeCoordinator` 事件派生。

## 4. 第一版边界

第一版只支持 `required_slots`。

以下能力不进入 Phase 5 第一版：

- `optional_slots`。
- late optional output。
- re-merge。
- partial merge / combiner / tree aggregation。
- 按交换律、结合律或幂等性质优化 merge。
- 生产级激励系统。
- 结算后 reversal / adjustment。
- 真实链上支付。
- 真实分布式 worker pool。

## 5. 后续仍需收束的问题

下列原待讨论点已在第 6-13 节确认：merge relation 形态、merge task 创建事件、`MERGE_RECORDED` 写入时机、`ExpectedOutputRef` resolution 边界、贡献 taxonomy、settlement batch、parent completion / upward trigger、subtree pruning 与 early-success 边界。

后续不是继续做方向取舍，而是把确认后的口径收束为 Phase 5 字段规格和 TDD 计划：

1. 写出 Phase 5 对象字段、event payload、idempotency key、batch 顺序、SQLite projection 和 replay inconsistency 规则。
2. 把 `merge_task_creation_batch`、`merge_resolution_batch`、`parent_completion_batch`、`settlement_batch`、`subtree_pruning_batch` 的半批次校验纳入 SQLite / replay 规格。
3. 明确 `MergeCoordinator`、`ContributionCoordinator`、`SettlementEngine` 的第一版 API 和测试顺序。
4. 保持第一版 all-required merge；`optional_slots`、`one_success`、partial merge、early terminal resolution、factorization early pruning 和 redundant verification contribution 延后到 Phase 5.1 或 Phase 6 插件实验设计。

## 6. 已确认：merge relation 形态

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 7 节。共同经验是：成熟系统通常区分任务执行顺序、数据输入绑定和高层 lineage / 查询视图。

采用以 C 为主的混合方案：

1. 新增独立 `MergeTaskLink` 逻辑对象或 `merge_task_links` index-only projection。
2. 权威来源来自 merge task 创建事件或 merge readiness / task-created batch，而不是 `TaskUnit.metadata` 或插件 payload。
3. `MergeTaskLink` 绑定：
   - `task_id`
   - `parent_unit_id`
   - `merge_plan_id`
   - `expansion_decision_id`
   - `merge_unit_id`
   - `merge_input_bundle_ref`
   - `merge_input_bundle_digest`
   - required slot bindings 摘要
4. required slot binding 至少记录：
   - `slot_key`
   - `source_child_unit_id`
   - `source_child_logical_key`
   - `source_output_name`
   - `canonical_selection_id`
   - `canonical_event_seq`
   - `canonical_output_ref`
   - `canonical_output_digest`
5. 如任务图遍历需要，可以额外记录一个窄义 `TaskRelation(kind=merge_of)`，表达 merge unit 属于某 parent / merge plan；但 `TaskRelation` 不承载 slot coverage、canonical hash 或 merge input bundle。

不建议：

- 不建议单纯扩展 `TaskRelation` 承载全部 merge 语义，因为这会把拓扑边、数据 lineage、slot coverage、canonical digest 和审计事实混在一起。
- 不建议只把关系写入 merge task payload / metadata，因为 metadata 已被 Phase 4 明确排除为 replay、merge readiness 或父节点完成判断的权威来源。

该设计口径后续需要在 Phase 5 字段规格中展开为对象字段、事件 payload、SQLite projection 和 replay 校验。

## 7. 已确认：merge task 创建事件

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 8 节。共同经验是：ready / waiting / queued 可以是 runtime 或 projection 状态，但 event-sourced 协议应把真正的 durable commitment 放在 work creation 和 input lineage 上。

采用以下口径：

1. 不新增独立 `MERGE_READY_RECORDED` 作为第一版必需事件。
2. `MergeCoordinator` 从 ledger / projection 派生 readiness：
   - accepted `MergePlan` 已可见。
   - 每个 `required_slot` 都绑定到 child `CANONICAL_OUTPUTS_BOUND`。
   - 对应 `merge_plan_id` 尚无有效 `MergeTaskLink`。
3. readiness 成立后先构造并保存稳定 `merge_input_bundle` artifact；该 artifact 在被 batch 引用前只是 staged input，不是权威协议事实。
4. 通过 `append_batch()` 写 `merge_task_creation_batch:{merge_plan_id}` 或等价 deterministic batch id。
5. batch 第一版固定包含：
   - `TASK_UNIT_CREATED`：创建 merge `TaskUnit`，初始 state 为 `Ready`，输入指向 `merge_input_bundle`。
   - 可选 `TASK_RELATION_CREATED(kind=merge_of)`：只用于图遍历，不承载 slot/hash 权威。
   - `MERGE_TASK_LINK_RECORDED`：最终 marker，记录 `MergeTaskLink`、slot bindings、canonical event seq、child output digests、bundle ref/digest 和 readiness reason。
6. `MERGE_TASK_LINK_RECORDED` 是 merge task creation batch 的语义完成标记；SQLite `merge_task_links` 只在看到该 marker 后暴露可消费 row。
7. 如果 coordinator 在 batch 前崩溃，replay 后再次扫描并重试；如果相同 batch 已写入，幂等返回既有 batch；如果出现半批次，projection / replay 必须报 ledger inconsistency。

不建议：

- 不建议单独写 `MERGE_READY_RECORDED` 后再写 task creation。它会制造一个没有 work commitment 的中间事实，增加 crash / retry / duplicate ready 的状态面。
- 不建议只写 `TASK_UNIT_CREATED`，把所有 link 和 slot bindings 藏在 task payload 里。这样会削弱已经确认的 `MergeTaskLink` 关系对象和审计查询能力。

该设计口径后续需要在 Phase 5 字段规格中展开为 batch 顺序、idempotency key、marker payload、SQLite projection 和 replay 半批次校验。

## 8. 已确认：`MERGE_RECORDED` 写入时机

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 9 节。共同经验是：执行尝试、验证 / terminal state、下游可消费输出承诺是不同层次；如果把 attempt 结果直接当成最终 merge fact，会给 replay、贡献和结算带来重复计数风险。

采用以下口径：

1. `MERGE_RECORDED` 第一版只作为 canonical-level merge commitment。
2. `MERGE_RECORDED` 只在 merge `TaskUnit` 已经写入 `CANONICAL_OUTPUTS_BOUND` 后写入。
3. 第一版不新增 attempt-level `MERGE_ATTEMPT_RECORDED`。普通 merge attempt 的审计已经由：
   - `EXECUTION_REQUEST_RECORDED`
   - `EXECUTION_SUBMISSION_RECORDED`
   - `VERIFICATION_RECORDED`
   - `CANONICAL_OUTPUTS_BOUND`
   覆盖。
4. `MERGE_RECORDED` payload 至少记录：
   - `task_id`
   - `parent_unit_id`
   - `merge_plan_id`
   - `merge_unit_id`
   - `merge_task_link_id`
   - `merge_input_bundle_ref`
   - `merge_input_bundle_digest`
   - `required_slot_bindings_digest`
   - `merge_policy_id`
   - `merge_policy_version`
   - `merge_policy_digest`
   - `canonical_selection_id`
   - `canonical_event_seq`
   - `selected_verification_report_id`
   - `selected_submission_id`
   - `selected_attempt_id`
   - `merge_output_bundle_digest`
   - `merge_output_refs`
5. idempotency key 使用 `merge_record:{merge_plan_id}:{merge_unit_id}:{canonical_selection_id}` 或等价 deterministic identity。
6. 同一 canonical commitment 重试应幂等返回；如果同一 key 对应不同 canonical selection、input bundle digest、slot bindings digest 或 merge output digest，必须冲突。
7. losing merge attempts、verification failed / rejected attempts、late submissions 和 retry attempts 不写 `MERGE_RECORDED`，也不进入 contribution / settlement 默认计数。
8. `MERGE_RECORDED` 后续作为 `ExpectedOutputRef` resolution、parent completion / upward merge、contribution / settlement 的输入事实；与 `ExpectedOutputRef` resolution 的 batch 边界按第 9 节执行。

不建议：

- 不建议在 `EXECUTION_SUBMISSION_RECORDED` 后写 `MERGE_RECORDED`，因为 submission 只是候选输出，尚未验证也未 canonical。
- 不建议在 `VERIFICATION_RECORDED(status=passed|accepted)` 后写 `MERGE_RECORDED`，因为 verification 只是 eligibility，不是 final canonical commitment；这会和 Phase 4 canonical input boundary 冲突。
- 不建议第一版新增 attempt-level `MERGE_ATTEMPT_RECORDED`，因为普通执行和验证事件已经覆盖了 attempt 审计，额外事件会增加重复计数和 replay 判断面。

该设计口径后续需要在 Phase 5 字段规格中展开为 event payload、idempotency key、冲突规则、SQLite projection，以及与 `ExpectedOutputRef` resolution 的 batch 校验。

## 9. 已确认：`ExpectedOutputRef` resolution 事件边界

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 3.3、3.4、8、9 节。共同经验是：future / output resolution 应作为可重放的 durable fact；但下游可消费事实不能暴露半完成的 fan-in 结果。

采用以下口径：

1. 新增独立 `EXPECTED_OUTPUT_RESOLVED` event。
2. `MERGE_RECORDED` 是 merge canonical commitment；`EXPECTED_OUTPUT_RESOLVED` 是 parent output future resolution commitment。二者表达不同协议事实，不合并成一个 event。
3. 对 `merge_plan_output` 第一版，`MERGE_RECORDED` 和该 merge record 所需的 `EXPECTED_OUTPUT_RESOLVED` events 必须通过同一个 atomic batch 提交：
   - batch id：`merge_resolution_batch:{merge_record_id}`。
   - batch 内先写 `MERGE_RECORDED`，再写一个或多个 `EXPECTED_OUTPUT_RESOLVED`。
4. replay / projection 只有看到完整 `merge_resolution_batch`，才把 merge record 和 resolved expected outputs 暴露为可消费事实。
5. `ExpectedOutputRef.resolution_status` 的权威持久状态第一版只有：
   - `expected`
   - `resolved`
6. `blocked` 只能是 projection / query hint，不作为权威 event 状态落账。原因是 blocked 通常可由依赖、失败或 timeout projection 推导，作为 authority 会扩大恢复和冲突面。
7. `EXPECTED_OUTPUT_RESOLVED` payload 至少记录：
   - `task_id`
   - `owner_unit_id`
   - `expected_output_id`
   - `expected_output_name`
   - `resolution_source_type=merge_record`
   - `merge_record_id`
   - `merge_plan_id`
   - `merge_unit_id`
   - `merge_canonical_selection_id`
   - `resolved_output_ref`
   - `resolved_output_digest`
   - `resolved_event_seq`
8. idempotency key 使用 `expected_output_resolved:{expected_output_id}:{merge_record_id}` 或等价 deterministic identity。
9. 如果同一 `expected_output_id` 已通过同一 `merge_record_id` 和同一 output digest resolved，重试幂等返回。
10. 如果同一 `expected_output_id` 试图通过不同 `merge_record_id`、不同 canonical selection 或不同 output digest resolved，必须冲突。
11. 如果 batch 中只有 `MERGE_RECORDED` 而缺少应有 `EXPECTED_OUTPUT_RESOLVED`，或只有 resolution event 而缺少对应 `MERGE_RECORDED`，projection / replay 必须报 ledger inconsistency，不得暴露半成品。

不建议：

- 不建议把 expected output resolution 直接塞进 `MERGE_RECORDED` payload。这样会把 merge fact 和 parent future resolution fact 混成一个不可独立查询、不可独立冲突检测的事件。
- 不建议把 `EXPECTED_OUTPUT_RESOLVED` 延后到另一个普通 append。那会产生“merge 已 canonical，但 parent output future 尚未 resolved”的 crash window，parent completion / upward merge 需要额外处理半状态。
- 不建议把 `blocked` 做成权威状态事件。第一版 all-required merge 已足够保守，blocked 作为 query hint 更符合可重放投影边界。

## 10. 已确认：`ContributionRecord` 最小 taxonomy

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 3.5、9 节。共同经验是：重复执行、backup task、retry 和 late result 不能直接变成奖励计数；贡献和指标应按被接受的 canonical / terminal facts 计。

采用以下口径：

1. Phase 5 第一版贡献类型只包含：
   - `complete_canonical`
   - `expand_canonical`
   - `merge_canonical`
2. 不采用 attempt-level 名称，例如 `complete_attempt`、`expand_attempt`、`merge_attempt`。原因是第一版结算依据是 canonical / accepted facts，不是所有 attempts。
3. redundant verification contribution 延后，不进入 Phase 5 第一版 settlement。普通 verification 仍作为 canonical evidence 被引用，但不单独计奖。
4. `complete_canonical`：
   - source facts：canonical attempt、accepted complete decision、`completion_batch`。
   - 初始 contribution state：`Eligible`。
   - 适用场景：叶子或不可继续拆分节点通过 accepted complete path 完成。
5. `expand_canonical`：
   - source facts：canonical attempt、accepted expand decision、`TASK_EXPANDED`。
   - 初始 contribution state：`Pending`。
   - 变为 `Eligible` 的条件：下游 output resolution / parent completion path 成功，证明这次 expand 对最终完成链路有效。
6. `merge_canonical`：
   - source facts：merge unit canonical attempt、`MERGE_RECORDED`。
   - 初始 contribution state：`Eligible`。
   - 适用场景：merge `TaskUnit` 的 canonical output 已被 `MERGE_RECORDED` 承诺，并进入 expected output resolution。
7. 每个 contribution 必须记录 canonical / verification evidence ids 和 event seqs，至少包含：
   - `task_id`
   - `unit_id`
   - `kind`
   - `source_attempt_id`
   - `canonical_selection_id`
   - `canonical_event_seq`
   - `verification_report_id`
   - `verification_event_seq`
   - `source_decision_id` 或 `merge_record_id`
   - `source_batch_id`
   - `source_terminal_event_seq`
8. contribution deterministic id 使用 `contribution:{kind}:{task_id}:{unit_id}:{canonical_selection_id}` 或等价 identity。
9. losing attempts、late submissions、verification failures、canonical losers、retry attempts 和 shadow attempts 可以保留审计，但默认不产生 contribution。

不建议：

- 不建议第一版按 attempt 建 contribution。attempt 是执行审计对象，不是结算权威对象；按 attempt 计数会直接引入重试 / 迟到 / shadow execution 双重奖励风险。
- 不建议第一版加入 redundant verification contribution。它需要验证质量、反作弊、agreement / disagreement 规则和预算分配，超出 Phase 5 merge 主闭环。

## 11. 已确认：`CONTRIBUTION_STATE_CHANGED` 与 root-level settlement batch

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 3.5、9 节。共同经验是：settlement / counters 必须避免 backup execution 或 retry double counting；event-sourced 系统应把一次 settlement 的边界写成一个可重放、可拒绝半批次的 commitment。

贡献事件边界采用以下口径：

1. 使用已有 `CONTRIBUTION_STATE_CHANGED` 表达 contribution 创建和状态推进。
2. 不新增单独 `CONTRIBUTION_RECORDED`。
3. contribution 创建通过以下 state change 表达：
   - `old_state = null -> Pending`
   - 或 `old_state = null -> Eligible`
4. 第一版 contribution 状态转换：
   - `Pending -> Eligible`
   - `Pending -> Invalidated`
   - `Eligible -> Invalidated`
   - `Eligible -> Settled`
5. `Settled` 只能由 settlement batch 推进；不能由普通 contribution coordinator 单独 append。

settlement 事件边界采用以下口径：

1. 使用 root-level settlement batch，不使用每个 contribution 一条独立 settlement event。
2. batch id 使用 `settlement_batch:{task_id}:{root_unit_id}:{root_completion_event_seq}` 或等价 deterministic identity。
3. batch 顺序：
   - events `1..n`：`CONTRIBUTION_STATE_CHANGED Eligible -> Settled`。
   - final marker：`SETTLEMENT_RECORDED`。
4. `SETTLEMENT_RECORDED` payload 至少记录：
   - `task_id`
   - `root_unit_id`
   - `root_completion_event_seq`
   - `settlement_policy_id`
   - `settlement_policy_version`
   - `root_budget`
   - `scale`
   - `total_reward`
   - `entry_count`
   - `settlement_entries_digest`
   - `settlement_entries_ref`，当 entries 较长时使用 artifact。
   - `settlement_summary`
5. SQLite 可以投影 `settlement_entries`，但 JSONL `settlement_batch` 是权威边界。
6. replay / projection 只有看到完整 batch 和 final `SETTLEMENT_RECORDED`，才把 contributions 暴露为 settled。
7. 同一 root completion 只能有一个有效 settlement batch；相同 payload 重试幂等，不同 policy / reward / entry digest 必须冲突。

不建议：

- 不建议每个 contribution 独立写 settled event 后再追加 settlement summary。这样会出现部分 settled、summary 缺失、crash 后重复结算的复杂窗口。
- 不建议另设 `CONTRIBUTION_RECORDED`。`CONTRIBUTION_STATE_CHANGED old_state=null` 已足够表达创建，还能保持状态机和投影简单。

## 12. 已确认：parent completion / upward trigger

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 3.3、3.4、8 节。共同经验是：fan-in 下游 task 的可触发条件可以从上游结果状态 / future resolution 派生；但 parent completion 应是单独 terminal commitment，不能被某个局部 merge resolution 偷偷代表。

采用以下口径：

1. Phase 5 第一版不启用 `WaitingForChildren`、`MergeReady`、`Merging` 作为权威 `TaskUnit` 状态，即使 enum 值已经存在或未来可能存在。
2. parent unit 在 required expected outputs resolved 前保持 `Processing`。
3. `merge_resolution_batch` 只负责 `MERGE_RECORDED` 和相关 `EXPECTED_OUTPUT_RESOLVED`；不负责 parent completion。
4. 当 owner unit 的 required expected outputs 全部为 `resolved` 后，写单独 batch：
   - batch id：`parent_completion_batch:{owner_unit_id}:{resolved_output_set_digest}`。
5. `parent_completion_batch` 至少包含：
   - `TASK_UNIT_STATE_CHANGED Processing -> Completed`
   - `CONTRIBUTION_STATE_CHANGED expand_canonical Pending -> Eligible`
6. `resolved_output_set_digest` 必须由 owner unit required expected outputs 的稳定排序和 resolved output digest 派生。
7. 如果一个 merge resolution 只解决 parent 的部分 expected outputs，不得推进 parent Completed，也不得把 `expand_canonical` 提前 Eligible。
8. 如果 parent 已完成，相同 resolved output set 重试幂等；不同 resolved output set 或缺失 required output 的 completion attempt 必须冲突或拒绝。

不建议：

- 不建议把 parent completion 放进 `merge_resolution_batch`。一个 merge record 可能只 resolve parent 的一个 expected output；把 completion 混进去会破坏多 expected output 的通用边界。
- 不建议第一版新增复杂 parent waiting / merging 状态。`ExpectedOutputRef` projection 已能回答等待原因，权威状态保持 `Processing -> Completed` 更容易 replay 和测试。

## 13. 已确认：subtree pruning 与 early-success 边界

本节已于 2026-06-25 讨论确认。

外部系统经验摘要见 `Doc/TechnicalDocument/2026-06-25-phase-5-external-systems-merge-notes.md` 第 3.4、3.6 节。共同经验是：多 trigger rule、partial aggregation、combiner、early success 会显著扩大状态空间；第一版应保守使用 all-required fan-in，把取消 / 剪枝放在 terminal commitment 之后。

第一版能力边界：

1. Phase 5 V1 仍然是 all-required merge。
2. 不实现 `one_success`、early terminal resolution、optional slots、partial merge、trigger rules 或 factorization early pruning。
3. factorization early pruning 延后到 Phase 5.1 或 Phase 6 插件实验设计。
4. 第一版 subtree pruning 只做 post-completion cancellation：父节点已经 completed 后，取消不再需要且尚未完成的 descendant work。

subtree pruning event 采用以下口径：

1. 使用已有 `TaskState.CANCELLED`；状态机已允许 `Ready/Processing/Blocked -> Cancelled`。
2. suggested batch id：`subtree_pruning_batch:{parent_unit_id}:{parent_completed_event_seq}`。
3. batch 顺序：
   - events `1..n`：`TASK_UNIT_STATE_CHANGED Ready/Processing/Blocked -> Cancelled`。
   - final marker：`SUBTREE_PRUNED`。
4. `SUBTREE_PRUNED` payload 至少记录：
   - `task_id`
   - `parent_unit_id`
   - `parent_completed_event_seq`
   - `pruning_policy_id`
   - `pruning_policy_version`
   - `policy_source_type`
   - `policy_source_id`
   - `cancelled_unit_count`
   - `cancelled_unit_ids_digest`
   - `preserved_completed_unit_count`
   - `reason`
5. pruning authority 必须来自插件声明的 versioned pruning policy，优先挂在 `MergePolicy` / `MergePlan` 或未来 terminal-resolution policy 上。
6. `complete decision` 不能自由声明 prune scope；它只能引用已经声明的 pruning policy 和 canonical evidence。
7. 已 completed 的 unit、已有 canonical output 的 unit、已经进入 settlement evidence 的 contribution source 不得被取消或回滚；必须保留 audit。
8. projection / replay 只有看到 final `SUBTREE_PRUNED`，才把 cancellation batch 视为完整 pruning 事实。

不建议：

- 不建议第一版实现 factorization early success。虽然因数分解任务可能自然存在“找到一个因子即可剪掉其他搜索分支”的优化，但它需要 terminal-resolution policy、partial-output correctness、奖励归属和 cancellation race 规则，不应混入 Phase 5 主闭环。
- 不建议让 complete decision 临时给出 prune scope。剪枝范围是策略权限问题，不是某次执行输出可以自由决定的运行时文本。
