# Phase 4 验证、正式输出与展开讨论记录

## 元数据

| 项 | 内容 |
|---|---|
| 日期 | 2026-06-24 |
| 状态 | Discussion notes / not implementation-start ready |
| 对应 feature | `feat-005` - Phase 4 - Verification, Canonical Output, and Expansion |
| 上游依据 | `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` 第 4.3、8、9、10、11、12、21 节；`Doc/TechnicalDocument/2026-06-23-phase-3-code-map.md` |
| 目的 | 记录 Phase 4 开工前已经确认的讨论结论，避免后续把 `expand` 简化成单个判定值，或遗漏结构化拆分提案和后续合并计划。 |

## 1. 当前确认的 Phase 4 主线

Phase 4 的主线不是只给 submission 打一个验证结果，而是把 Phase 3 已持久化的 `ExecutionSubmission` 推进成可审计、可重放、可合并的协议事实链。

当前确认的核心事实链如下。这里展示的是 `expand` 路径的必要事实；`complete`
路径在 `EXPANSION_DECISION_RECORDED` 后进入完成/向上汇合语义，不写 proposal、
merge plan 或图 mutation。

```text
ExecutionSubmission artifact
-> VERIFICATION_RECORDED
-> CANONICAL_OUTPUTS_BOUND
-> SPLIT_STRATEGY_INVOCATION_RECORDED(status=succeeded)
-> append_batch(expansion_batch:{expansion_decision_id})
   1. DECOMPOSITION_PROPOSAL_RECORDED
   2. EXPANSION_DECISION_RECORDED(action=expand)
   3. MERGE_PLAN_RECORDED
   4. child TASK_UNIT_CREATED...
   5. child TASK_RELATION_CREATED...
   6. TASK_EXPANDED
```

其中：

- `VERIFICATION_RECORDED` 记录候选输出束是否通过通用检查和插件领域验证。
- `CANONICAL_OUTPUTS_BOUND` 是 logical `CanonicalSelection`，记录唯一正式输出束的 selection identity、policy/version、bundle digest 和 validation IDs。
- `SPLIT_STRATEGY_INVOCATION_RECORDED` 是插件 split strategy 调用审计事件，记录成功、调用失败或非法返回；它不是 `ExpansionDecision`，不直接产生 proposal、merge plan、状态变化或图 mutation。
- `DECOMPOSITION_PROPOSAL_RECORDED` 保存结构化拆分提案 artifact 引用；提案描述 child units、依赖、expected outputs、merge slots 和插件来源。
- `EXPANSION_DECISION_RECORDED` 保存 `complete` / `expand` 判定、依据、插件版本和引用对象。
- `MERGE_PLAN_RECORDED` 保存父节点后续合并所需的独立合并契约。
- `TASK_EXPANDED` 是 expand batch 的最后语义完成标记；只在 proposal、decision、merge plan、child units、relations 和图约束全部通过并同批落账后写入；无效展开不得产生可接受的部分图 mutation。

## 2. 已确认结论

### 2.1 `decision=expand` 不是足够的协议事实

Phase 4 不能只记录一个 `decision=expand`。`expand` 必须有完整上下文：

- 来源 canonical output bundle。
- 已保存且可 hash 复查的 `DecompositionProposal` artifact。
- 引用该 proposal 的 `ExpansionDecision`。
- 独立持久化的 `MergePlan` artifact 和 `MERGE_PLAN_RECORDED` event。
- 由 proposal canonical digest 与逻辑位置确定性派生的 child unit、dependency edge 和 expected output ID。

否则 replay、audit、后续 merge 和结算都无法解释“为什么这些子任务存在、它们如何满足父任务输出、未来如何合并回父节点”。

### 2.2 `MergePlan` 独立持久化

已确认：`MergePlan` 不嵌入 `DecompositionProposal` 作为普通子字段，也不只存在于 `TASK_EXPANDED` payload 中。

Phase 4 应把 `MergePlan` 作为独立 artifact 保存，并写入独立的 `MERGE_PLAN_RECORDED` event。这样 Phase 5 的 `MergeCoordinator` 可以直接把 `MergePlan` 作为权威合并契约读取，而不需要从 proposal 内部解析隐含规则。

`MergePlan` 不是合并算法，也不是协议定义的领域合并规则。合并规则由插件的
`MergePolicy` / `merge_policy_id` 静态定义；`MergePlan` 是该插件规则在本次 expansion
上的实例化合并契约。它回答的是：

```text
这个 parent_unit 本次展开出的 child units，将来需要收集哪些 child canonical outputs，
用哪个 plugin merge policy 版本和参数合回哪个 parent output？
```

因此边界如下：

| 对象 | 所属层 | 作用 |
|---|---|---|
| `MergePolicy` / `merge_policy_id` | 插件 descriptor / 插件实现 | 定义某类任务如何做领域合并，例如 section 如何组装成 report、proof subgoal 如何合成父证明、factorization 子区间结果如何汇总。 |
| `MergePlan` | 插件生成、协议保存的 artifact | 固化某一次 expansion 的合并输入槽位、child output 引用边界、schema/hash 要求、parent output mapping 和所使用的 merge policy identity。 |
| 协议 / `MergeCoordinator` | 协议编排层 | 检查 slot 覆盖、引用存在性、schema/digest、child canonical output hash 记录要求和 replay 边界；Phase 5 才调用插件 merge policy 执行领域合并。 |

协议不得在 `MergePlan` 中生成自然语言合并规则、解释领域语义或实现插件合并算法。
协议只把 `MergePlan` 当作可审计、可重放、可查询的合并实例 manifest。

已确认：第一版 `MergePlan` 只支持 `required_slots`，不支持 `optional_slots`。
`optional_slots` 延后到 Phase 5 merge / contribution 讨论。理由是 optional slot 会引入
merge readiness、迟到 optional 输出、re-merge、质量提升和贡献结算语义；这些不应进入 Phase
4 的扩图契约。

第一版 `MergePlan` 使用以下顶层块：

```text
merge_plan_header:
  merge_plan_id
  merge_plan_schema_version
  task_id
  parent_unit_id
  canonical_selection_id
  decomposition_proposal_id
  expansion_decision_id
  created_by_plugin_id
  created_by_plugin_version
  merge_plan_digest
  created_at

merge_policy_ref:
  plugin_id
  plugin_version
  merge_policy_id
  merge_policy_version
  merge_policy_descriptor_digest
  merge_policy_params_digest

required_slots:
  - slot_key
    source_child_logical_key
    source_child_unit_id
    source_output_name
    output_schema_ref
    output_schema_digest
    required
    missing_policy

parent_output_mapping:
  - parent_output_name
    resolution_kind
    merge_slot_keys
    result_schema_ref
    result_schema_digest

hash_recording_requirements:
  record_child_canonical_output_digest
  record_slot_source_artifact_digest
  record_merge_input_bundle_digest

merge_validation_requirements:
  all_required_slots_canonical
  slot_schema_check_required
  merged_output_schema_check_required
  plugin_merge_validator_policy_id

plugin_payload:
  plugin_defined_schema_ref
  plugin_defined_body_digest
  plugin_defined_body
```

字段边界：

- `merge_plan_header` 绑定本次 expansion、proposal、decision 和 digest，防止漂浮 merge plan。
- `merge_policy_ref` 只引用插件静态 merge policy，不携带协议生成的合并规则正文。
- `required_slots` 是 Phase 4 唯一支持的 slot 类型；每个 slot 必须指向存在的 child logical key / child unit / child output name，`required=true`，`missing_policy=block_merge`。
- `parent_output_mapping` 描述这些 required slots 如何满足父节点 named output；协议只检查引用和 schema，领域合并由插件 policy 执行。
- `hash_recording_requirements` 固化 Phase 5 merge 时必须记录的 child canonical output hash、slot source artifact hash 和 merge input bundle hash。
- `merge_validation_requirements` 规定 merge 前后必须满足的结构校验和插件 merge validator policy。
- `plugin_payload` 是插件私有结构化配置；协议保存、hash、校验 schema，但不解释自然语言或领域语义。

第一版 merge readiness 规则也同步收窄：

```text
所有 required_slots 都绑定 canonical child output -> 后续 Phase 5 可以进入 merge。
任一 required_slot 缺失 canonical child output -> 不得进入 merge。
optional_slots 不存在于 Phase 4 MergePlan schema。
```

### 2.3 `DecompositionProposal` 的六个顶层块

已确认：协议不能理解自然语言，`DecompositionProposal` 必须使用结构化字段描述可扩图内容。自然语言只能作为说明、执行 hint 或插件 payload，不能直接创建任务图。

第一版 `DecompositionProposal` 采用六个顶层块：

| 块 | 回答的问题 | 协议可检查内容 |
|---|---|---|
| `proposal_header` | 这个 proposal 来源于哪一个父节点、canonical output 和插件 split strategy。 | proposal / task / parent unit / canonical selection / plugin / split strategy / digest / time 等身份字段是否齐备且与 invocation scope 匹配。 |
| `child_specs` | 要创建哪些子任务。 | 子任务类型、输入绑定、required outputs、validator policy、预算、深度和插件支持范围是否合法。 |
| `dependency_edges` | 子任务之间有哪些执行或数据依赖。 | source / target child 是否存在、source output 是否声明、target input 是否重复绑定、依赖图是否无环。 |
| `expected_outputs` | 父任务最终需要哪些正式输出，以及这些输出由什么 resolution 满足。 | 父任务 named output 是否有 resolution、schema 是否存在、resolution 是否指向 direct output、child output 或 merge plan。 |
| `merge_slots` | 后续 `MergePlan` 需要收集哪些子节点正式输出。 | slot 是否指向存在的 child output、required slot 是否覆盖、schema 是否兼容、允许缺失策略是否显式。 |
| `promotion_guard_evidence` | 为什么这些 child specs 可以晋升为协议 `TaskUnit`。 | typed I/O、可独立调度、validator policy、明确 output contract、深度/规模限制、非 free-form thought 等 guard 是否通过。 |

这六块的边界是：

- `proposal_header` 是 proposal 的身份、来源和 replay 锚点。
- `child_specs` 是创建 `TaskUnit` 的蓝图。
- `dependency_edges` 是子任务之间的等待关系和数据依赖。
- `expected_outputs` 是父节点输出 future / resolution 声明。
- `merge_slots` 是后续合并所需输入槽位的结构化草案。
- `promotion_guard_evidence` 是 durable subgoal 晋升检查的结构化摘要。

协议只理解这些结构化字段、artifact refs、schema refs、IDs、digests 和图不变量；领域含义由插件验证器判断。

建议的最小字段：

```yaml
proposal_header:
  proposal_id
  task_id
  parent_unit_id
  canonical_selection_id
  canonical_output_bundle_digest
  plugin_id
  plugin_version
  split_strategy_id
  split_strategy_params_digest
  proposal_digest
  created_at

child_specs:
  - child_logical_key
    unit_type
    input_bindings
    required_outputs
    output_contract_refs
    validator_policy_id
    budget_limit
    deadline
    weight
    plugin_payload
    promotion_guard_ref

dependency_edges:
  - edge_logical_key
    source_child_key
    target_child_key
    source_output_name
    target_input_name
    relation_type

expected_outputs:
  - output_name
    schema_ref
    resolution_kind: direct_parent_output | child_output | merge_plan_output
    child_key
    child_output_name
    merge_slot_id
    required

merge_slots:
  - slot_id
    child_key
    child_output_name
    schema_ref
    required
    missing_policy

promotion_guard_evidence:
  typed_io_checked
  independently_schedulable_checked
  validator_policy_checked
  output_contract_checked
  no_freeform_thought_checked
  max_depth_checked
  max_children_checked
  evidence_ref
```

已确认：`promotion_guard_evidence` 第一版内联在 `DecompositionProposal` 内，不单独
artifact 化。它与 proposal 生命周期强绑定；长解释、插件诊断或完整证明材料可以通过
`evidence_ref` 指向 artifact。

Durable subgoal 晋升规则：

- 只能晋升 typed I/O、可独立调度、有 validator policy、有明确 output contract 的 durable subgoal。
- free-form thought、隐藏 reasoning、临时草稿、自然语言“下一步建议”不得晋升为协议 `TaskUnit`。
- 协议只检查结构化 guard 和图约束；领域上“这个 child 是否合理”仍由插件策略负责。

### 2.4 拆分规则由插件制定，不交给 AI 临时提出

已确认：协议级拆分规则应在插件编写时进入插件，而不是由处理端 AI 在运行时临时提出。

运行时可以生成 `DecompositionProposal` artifact，但 proposal 必须由插件版本化拆分策略直接生成。Phase 4 不采用“AI / executor 先产出候选拆分 artifact，再由插件规范化为协议 proposal”的路径。AI 或 executor 不能凭自然语言自由决定“应该扩出哪些协议 `TaskUnit`”，也不能为 expansion 提供候选拆分方案。

责任边界如下：

| 角色 | 责任 |
|---|---|
| 用户 / `TaskSpec` | 选择插件、插件版本、根输入、插件 descriptor 已声明的 split strategy ID、插件 schema 允许的策略参数和运行限制；不提交拆分规则正文。 |
| 插件 | 定义拆分策略、合法 `unit_type`、端口 schema、validator policy、merge policy、durable subgoal 晋升条件，以及如何从 canonical output 直接生成结构化 proposal / merge plan 或返回 complete。 |
| AI / executor | 产生候选输出或执行结果；不拥有协议扩图权威，也不产生 expansion 候选拆分方案。 |
| 协议 / `ExpansionCoordinator` | 调用插件 split strategy，校验插件产物的结构、schema、图约束、规模限制和幂等边界，然后写入事件。 |

示例：

- factorization 插件可以规定每次把搜索区间切半，proposal 只包含两个子区间 task、它们的输出契约和后续 merge plan。
- 真实 Lean proof 插件的拆分策略可以更复杂，但规则仍应属于插件版本化实现，而不是由 AI 在运行时自由发明协议子任务。

### 2.5 `SplitStrategyResult` 同时覆盖 `complete` 和 `expand`

已确认：Phase 4 不让协议根据输出内容自行猜测节点是否完成，也不把 `complete` 和
`expand` 拆成两套来源。插件版本化 split strategy 面对已绑定 canonical output 时，必须返回
统一的 `SplitStrategyResult`。

`SplitStrategyResult` 是插件 split strategy 的确定性结果，但不是 ledger 事实。协议只有在
保存必要 artifact、写入事件并通过不变量检查后，才把其中的 `complete` 或 `expand` 变成
authoritative state / graph 事实。

第一版 `SplitStrategyResult` 使用互斥 action：

| action | 插件返回内容 | 协议后续动作 |
|---|---|---|
| `complete` | completion evidence、completed output refs、split strategy identity 和参数摘要。 | 通过 `EventLedger.append_batch()` 写 `completion_batch:{expansion_decision_id}`：第 1 条为 `EXPANSION_DECISION_RECORDED(action=complete)`，第 2 条为 `TASK_UNIT_STATE_CHANGED(current -> Completed)`；不写 `DecompositionProposal`、`MergePlan`、`TASK_EXPANDED` 或 child graph events。 |
| `expand` | `DecompositionProposal` body、`MergePlan` body、generation evidence、split strategy identity 和参数摘要。 | 保存 proposal / merge plan artifact；通过最小 `EventLedger.append_batch()` 写入 `DECOMPOSITION_PROPOSAL_RECORDED`、`EXPANSION_DECISION_RECORDED(action=expand)`、`MERGE_PLAN_RECORDED`、child `TASK_UNIT_CREATED`、child `TASK_RELATION_CREATED` 和最后的 `TASK_EXPANDED`。只有整批事实全部通过协议检查并落账后，expansion 才对 replay / projection 可见。 |

协议本体不理解领域上“为什么这样拆”，但必须检查“这个结果能不能作为协议事实落账”。因此，
插件负责领域拆分理由；协议负责来源、schema、digest、规模、无环、slot 覆盖、幂等和 replay
边界。

已确认：在当前 V1 范围内，协议只面对有确定结果的大型计算任务。因此，当 split strategy
返回 `action=complete` 且协议接受该 decision 后，当前 `TaskUnit` 必须通过
`completion_batch:{expansion_decision_id}` 与 decision 同批推进为 `Completed`。最小事件顺序为：

```text
SPLIT_STRATEGY_INVOCATION_RECORDED(status=succeeded)
append_batch(completion_batch:{expansion_decision_id})
  1. EXPANSION_DECISION_RECORDED(action=complete)
  2. TASK_UNIT_STATE_CHANGED(current -> Completed)
```

较早讨论中的“同一协调流程内写”必须按这里的 batch 语义理解：complete decision 和
completed state change 不是两个可独立接受的普通 append。若 replay / projection 只看到
complete decision 而缺少同一 `completion_batch` 内的 completed state change，必须报告 ledger
inconsistency，不能静默推导 `Completed`。

`complete` 路径不得写 `DECOMPOSITION_PROPOSAL_RECORDED`、`MERGE_PLAN_RECORDED`、
`TASK_EXPANDED`、child `TASK_UNIT_CREATED` 或 child `TASK_RELATION_CREATED`。
它只把已验证并绑定 canonical output 的当前节点收束为完成节点。若该节点有父节点，后续向父节点
汇合仍属于 Phase 5 merge / parent readiness 语义，不在 Phase 4 执行合并。

已确认：`complete` 路径的 `completion_evidence` 第一版内联在
`EXPANSION_DECISION_RECORDED(action=complete)` 的 `action_body` 中，不单独 artifact 化。
长诊断、大证明材料或插件调试输出仍应通过 artifact ref 保存。

第一版 `complete.action_body` 最小字段：

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

字段边界：

- `completion_kind` 表示完成类型；第一版至少支持 `direct_verified_output`，表示当前节点的 canonical output 已经直接满足该节点输出契约。
- `validator_policy_id` 指向插件验证策略，不在协议里生成领域验证规则。
- `verification_report_id` 指向 Phase 4 验证报告。
- `canonical_selection_id` 和 `canonical_output_bundle_digest` 锁定完成依据，避免 replay 时重新解释 executor 输出。
- `completed_output_refs` 列出本节点正式完成输出的 output name / artifact ref / artifact digest 摘要。
- `plugin_completion_summary` 是短结构化摘要；不得放长自然语言 reasoning 或隐藏推理链。

### 2.6 split strategy 调用失败只写审计事件

已确认：插件 split strategy 调用失败、超时、抛出异常或返回非法 `SplitStrategyResult` 时，
协议采用独立审计事件，而不是把失败编码进 `EXPANSION_DECISION_RECORDED`。

第一版事件建议命名为 `SPLIT_STRATEGY_INVOCATION_RECORDED`。它记录一次插件 split strategy
调用事实，但不是 authoritative expansion decision。事件 `status` 至少区分：

| status | 含义 | 协议后续动作 |
|---|---|---|
| `succeeded` | 插件调用返回了可解析的 `SplitStrategyResult`。 | 继续做协议校验；只有 accepted `complete` / `expand` 才能写 `EXPANSION_DECISION_RECORDED`。 |
| `failed` | 插件调用抛错、超时或执行失败。 | 只保留审计记录；不得写 proposal、merge plan、expansion decision、状态变化或图 mutation。 |
| `invalid_result` | 插件返回结果不能解析为互斥 `complete` / `expand`，或缺少必要字段。 | 只保留审计记录；不得写 proposal、merge plan、expansion decision、状态变化或图 mutation。 |

第一版 `SPLIT_STRATEGY_INVOCATION_RECORDED` 只保存调用摘要、digest 和错误摘要，不保存完整
`SplitStrategyResult` body。完整 `DecompositionProposal` 和 `MergePlan` body 仍由后续
artifact 与 `DECOMPOSITION_PROPOSAL_RECORDED` / `MERGE_PLAN_RECORDED` 保存；`complete`
路径的完成证据也应通过后续 accepted decision payload 或 artifact 引用表达。

建议的最小 invocation payload：

- `invocation_id`
- `task_id`
- `unit_id`
- `canonical_selection_id`
- `canonical_output_bundle_digest`
- `plugin_id`
- `plugin_version`
- `split_strategy_id`
- `split_strategy_version` 或 plugin descriptor digest
- `split_strategy_params_digest`
- `status`
- `result_action`：`complete` / `expand` / `null`
- `result_digest`：成功且可 canonical JSON 时填写，否则为 `null`
- `error_kind`：`plugin_exception` / `timeout` / `invalid_result` / `contract_mismatch` / `protocol_rejected` / `null`
- `error_summary`：短结构化摘要；长日志、trace 或插件诊断材料必须走 artifact
- `started_at`
- `finished_at`

建议的最小 `SplitStrategyResult` envelope：

- `schema_version`
- `result_id`
- `task_id`
- `unit_id`
- `canonical_selection_id`
- `plugin_id`
- `plugin_version`
- `split_strategy_id`
- `split_strategy_params_digest`
- `action`：`complete` / `expand`
- `action_body`：`complete` 或 `expand` 的互斥 body
- `generation_evidence`
- `created_at`

边界：

- `EXPANSION_DECISION_RECORDED` 只表达协议已经接受的 `complete` 或 `expand` 决策。
- `SPLIT_STRATEGY_INVOCATION_RECORDED(status=failed|invalid_result)` 不改变 `TaskUnit`、
  不改变 `Attempt`、不绑定新的 artifact 为权威 proposal，也不创建 child node / edge。
- 成功调用也不自动成为 ledger authoritative state；它只是后续校验、artifact 保存和 decision
  记录的审计前置。

### 2.7 split strategy 与 expansion 事件幂等键

已确认：业务层允许对同一个 expansion scope 多次调用插件 split strategy，并为每次调用写
独立的 `SPLIT_STRATEGY_INVOCATION_RECORDED` 审计事件；但同一个 scope 最终只能有一个
accepted `EXPANSION_DECISION_RECORDED`。

`expansion_scope` 是以下字段的 canonical JSON digest：

- `task_id`
- `parent_unit_id`
- `canonical_selection_id`
- `canonical_output_bundle_digest`
- `plugin_id`
- `plugin_version`
- `plugin_descriptor_digest`
- `split_strategy_id`
- `split_strategy_params_digest`

`correlation_id` 不进入 `expansion_scope`。它只串联一次流程里的事件，不决定协议事实身份。

幂等键规则：

| 事件 / 对象 | idempotency key | 说明 |
|---|---|---|
| `SPLIT_STRATEGY_INVOCATION_RECORDED` | `split_invocation:{expansion_scope_hash}:attempt:{invocation_attempt_no}` | 业务重试插件调用时递增 `invocation_attempt_no`，允许多条审计记录；同一 attempt 写 ledger 重试必须使用同一 key 和同一 payload。 |
| `DECOMPOSITION_PROPOSAL_RECORDED` | `decomposition_proposal:{expansion_scope_hash}:{proposal_digest}` | 同一 proposal digest 可幂等重写；同一 scope 出现不同 proposal digest 是 duplicate / conflict，不得成为第二个有效 proposal。 |
| `MERGE_PLAN_RECORDED` | `merge_plan:{expansion_scope_hash}:{proposal_digest}:{merge_plan_digest}` | 同一 merge plan digest 可幂等重写；同一 scope + proposal digest 出现不同 merge plan digest 是 duplicate / conflict。 |
| `EXPANSION_DECISION_RECORDED` | `expansion_decision:{expansion_scope_hash}` | accepted decision 对同一 scope 唯一；后续同 key 同 payload 是幂等重试，同 key 不同 payload 必须按 `EventLedger` 冲突处理。 |
| `TASK_EXPANDED` | `task_expanded:{expansion_decision_id}` | 图 mutation 必须从 accepted expand decision 派生，不能直接从 invocation 派生。 |
| child `TASK_UNIT_CREATED` | `task_unit:create:{child_unit_id}` | child unit ID 从 proposal digest、parent unit 和 child logical key 确定性派生。 |
| `TASK_RELATION_CREATED` | `task_relation:create:{relation_id}` | relation ID 从 proposal digest、source/target logical key 和端口名确定性派生。 |
| expand batch | `expansion_batch:{expansion_decision_id}` | `append_batch()` 的批次幂等键；同一 batch key 同 payload 是幂等重试，同 key 不同事件集合或不同 payload 必须冲突。 |

这组规则的含义：

- 插件调用失败、超时或返回非法结果后，可以用新的 `invocation_attempt_no` 重新调用插件并保留审计历史。
- accepted `complete` / `expand` 决策不带 `invocation_attempt_no`，因此同一 parent canonical output 不会因为多次调用而产生多个最终决策。
- proposal / merge plan 的 key 带 digest 是为了幂等保存同一 artifact，不是为了允许同一 scope 下多个 proposal 或多个 merge plan 竞争。
- `TASK_EXPANDED` 和 child graph events 必须在 accepted decision 之后、同一个 expand batch 内写入；重复扩图应被 `expansion_batch:{expansion_decision_id}`、`task_expanded:{expansion_decision_id}` 和确定性 child / relation ID 拦截。
- 现有 `EventLedger` 的语义保持不变：同 key 同 payload 返回旧 event；同 key 不同 payload 报冲突。

### 2.8 Phase 4 只建立 merge 契约，不执行实际 merge

Phase 4 需要生成和记录 `MergePlan`，但不实现 Phase 5 的实际合并执行、`MergeRecord`、贡献结算或 subtree pruning。

换句话说，Phase 4 要回答：

```text
如果这个父节点被展开，未来必须如何收集子节点正式输出并合并回父节点？
```

Phase 5 才回答：

```text
当子节点正式输出齐备时，如何执行 merge task、验证 merge output，并把结果 canonical 回父节点？
```

### 2.9 动态扩图只能消费正式事实

Phase 4 的 expansion 只能从已经通过验证并绑定 canonical 的输出束出发。未验证 submission、输掉 selection 的 attempt、迟到 submission、executor 私有内存、隐藏 reasoning trace、thought、vote 或 working memory 都不能直接创建 `TaskUnit`。

只有具备稳定 schema、typed I/O、可独立调度、明确 validator 和受控图关系的 durable subgoal / proof state 才能通过 `DecompositionProposal` 晋升为协议 `TaskUnit`。

### 2.10 原子扩图的含义

已确认：Phase 4 采用“轻量 batch append + `TASK_EXPANDED` 作为最终完成标记”。
这不是引入外部数据库事务、分布式共识或 SQLite 权威状态，而是在现有 JSONL
单机单写者模型上为扩图事实增加最小 `EventLedger.append_batch(events, batch_id)`
原语。

1. 先读取 canonical selection、proposal artifact、merge plan artifact 和当前 `TaskGraph` view。
2. 在内存中完成所有检查：schema、规模限制、ID 派生、引用存在性、无环、slot 覆盖、expected output resolution、重复 expansion 等。
3. 任一检查失败时，不写 proposal / decision / merge plan 事实事件，不写 child `TASK_UNIT_CREATED`，不写 `TASK_RELATION_CREATED`，不写 `TASK_EXPANDED`。
4. 检查全部通过后，`append_batch()` 预分配连续 `event_seq` 和 hash chain，按以下顺序落账：

```text
append_batch(expansion_batch:{expansion_decision_id})
  1. DECOMPOSITION_PROPOSAL_RECORDED
  2. EXPANSION_DECISION_RECORDED(action=expand)
  3. MERGE_PLAN_RECORDED
  4. child TASK_UNIT_CREATED...
  5. child TASK_RELATION_CREATED...
  6. TASK_EXPANDED
```

5. 批内事件使用同一 `batch_id`、同一 `correlation_id` 和确定性 batch 幂等键；单条事件仍保留自己的 `event_seq`、`event_hash`、`idempotency_key` 和 replay handler。第一版在 `LedgerEvent` envelope 中新增显式 batch 字段，`correlation_id` 只做流程追踪，不承担 batch identity。
6. `expansion_decision_id` 可以从 `expansion_scope_hash` 确定性派生，用作 object identity 和 batch key 的一部分；这不是预分配 ledger event。`EXPANSION_DECISION_RECORDED` 先于 `MERGE_PLAN_RECORDED` 写入，因此第一版 `MergePlan.merge_plan_header.expansion_decision_id` 在语义上引用的是已落账 decision，而不是预分配但尚未记录的 decision。
7. `TASK_EXPANDED` 是 batch 内最后一条语义完成标记。Projection / replay 只有看到该 marker，才把本次 expansion 视为完整可见；如果 batch 无法完整落账，不能暴露 accepted partial graph。

`append_batch()` 只解决本地事件账本的提交边界，不替代 artifact 存储、SQLite projection
或后续 replay 校验。Proposal / merge plan artifact 可以在 batch 前保存为普通 artifact；
只有对应 `*_RECORDED` event 进入 accepted batch 后，这些 artifact 才成为协议权威事实。

已确认：`append_batch()` 的批次身份进入 `LedgerEvent` envelope，而不是只放在 API 入参、
payload 摘要或 `correlation_id` 中。第一版最小 envelope 字段为：

```text
batch_id: string | null
batch_index: integer | null
batch_size: integer | null
```

字段边界：

- 非 batch 事件的 `batch_id` / `batch_index` / `batch_size` 均为 `null`。
- expand batch 使用 `batch_id=expansion_batch:{expansion_decision_id}`。
- batch 内事件必须连续落账；`batch_index` 从 1 到 `batch_size`，最后一条语义事件必须是 `TASK_EXPANDED`。
- 同一 `batch_id`、同一事件集合和同一 payload digest 是幂等重试；同一 `batch_id` 但事件集合、顺序或 payload 不同必须按冲突处理。
- `correlation_id` 只串联一次协调流程，不能作为 replay / projection 判断 batch 完整性的权威身份。
- 因 envelope 字段变化，Phase 4 实现应显式升级或兼容读取 `LedgerEvent` schema；历史无 batch 字段事件按 `null` 处理。

### 2.11 `first_verified_bundle` 的排序权威

已确认：`first_verified_bundle` 的排序权威是通过验证报告进入 ledger 的顺序，而不是 executor
submission time，也不是 verifier 的 `verification_completed_at` 时间戳。

第一版语义：

- 对同一个 `TaskUnit`，只有 `VERIFICATION_RECORDED(status=passed|accepted)` 且满足 eligibility 检查的候选输出束参与选择。
- `first_verified_bundle` 中的“first”指最早落账的 eligible `VERIFICATION_RECORDED.event_seq`。
- `CANONICAL_OUTPUTS_BOUND` 是最终承诺事实；它记录唯一 `CanonicalSelection`，并引用被选中的 `verification_report_id`、`verification_event_seq`、`submission_event_seq`、`attempt_id`、`bundle_digest` 和 selection policy/version。
- `verification_completed_at`、submission time、executor local clock 和 verifier local clock 只做审计字段，不参与 canonical 排序。
- 如果 canonical 已经绑定，后续通过验证的重复或迟到结果只能保留为审计/结算候选证据，不得覆盖已有 canonical output。

这样与现有 `EventLedger` 的递增 `event_seq`、幂等键和 hash chain 一致，也吸收了参考系统中“控制面接受/提交顺序成为状态机权威”的经验。

### 2.12 Attempt 状态与验证 / canonical / 重复 expansion 边界

已确认：`Attempt` 只表达执行尝试和该尝试候选输出的验证 / 正式选择结果，不承载后续
expansion 冲突或重复扩图状态。验证、canonical selection 和 expansion 的边界如下：

| 场景 | Attempt 状态 | 协议事件 / 审计边界 |
|---|---|---|
| 候选输出通过验证并具备参与 selection 资格 | `Submitted -> Verified` | 写 `VERIFICATION_RECORDED(status=passed|accepted)`；该事件的 `event_seq` 是 `first_verified_bundle` 排序权威。 |
| 候选输出 schema、artifact、证据覆盖或插件领域验证失败 | `Submitted -> Rejected` | 写 `VERIFICATION_RECORDED(status=rejected)`，记录结构化失败摘要和 evidence；该 attempt 可作为无效输出证据参与有界重试策略。 |
| 验证器自身异常、超时或验证环境错误，尚未形成候选输出结论 | 保持 `Submitted` | 写验证调用审计摘要或 `VERIFICATION_RECORDED(status=error)`；不得把 executor attempt 标为 `Failed` 或 `Rejected`，后续可以重新验证。 |
| 候选输出通过验证但输掉 canonical selection | 保持 `Verified` | losing bundle 是正常竞争结果，不写 `Rejected`、`Failed` 或 `Superseded`；其未被后续协议消费可由 `CANONICAL_OUTPUTS_BOUND` 唯一绑定事实推导。 |
| 被 `CANONICAL_OUTPUTS_BOUND` 选中 | `Verified -> Canonical` | 写 `CANONICAL_OUTPUTS_BOUND` 后可推进被选中 attempt 为 `Canonical`；后续 expansion 只能消费该 canonical bundle。 |
| 重复 proposal，同一 digest | 不改变 Attempt | proposal / merge plan artifact event 或 expand batch 幂等返回已有事实。 |
| 重复 proposal，不同 digest | 不改变 Attempt | 不写第二个 accepted proposal / decision / merge plan / graph mutation；如已有插件调用，最多保留 `SPLIT_STRATEGY_INVOCATION_RECORDED` 审计。 |
| 重复 expansion，同一 batch payload | 不改变 Attempt | `append_batch(batch_id)` 幂等返回已有 batch。 |
| 重复 expansion，不同 payload 或已有 `TASK_EXPANDED` 后再次扩图 | 不改变 Attempt | 不写 child graph events，不写第二个 `TASK_EXPANDED`；按 batch / decision / deterministic child ID 冲突处理。 |

第一版不强制使用 `Verifying` 中间状态。若验证是本地同步协调流程，`Submitted ->
Verified` / `Submitted -> Rejected` 足以表达结果；只有当验证本身在后续版本成为可租约、
可异步、可恢复的执行单元时，才启用 `Submitted -> Verifying -> Verified/Rejected`
的细分状态。

### 2.13 verification / canonical selection 对 expansion 的输入约束

已确认：Phase 4 expansion 只能从 `CANONICAL_OUTPUTS_BOUND` 之后开始。`VERIFICATION_RECORDED(status=passed|accepted)`
只证明某个候选输出束具备参与选择的资格；它本身不是 expansion gate。真正允许后续协议消费的输入，是已经由
`CANONICAL_OUTPUTS_BOUND` 绑定的 canonical output bundle。

Expansion coordinator 的第一版输入边界：

- `task_id` / `unit_id`
- `canonical_selection_id`
- `canonical_output_bundle_digest`
- canonical output refs / named outputs
- selected `verification_report_id` 和 `verification_event_seq`
- `submission_event_seq` / `attempt_id`，只作为 provenance 和审计字段
- plugin descriptor snapshot、`split_strategy_id`、schema-checked `split_strategy_params`
- 当前 `TaskGraph` view、深度限制、子节点数量限制、预算限制和已存在 expansion scope

禁止作为 expansion 输入：

- 未通过验证的 submission。
- 通过验证但输掉 canonical selection 的 bundle。
- canonical 已绑定后才迟到或才通过验证的 bundle。
- raw model output、executor 私有内存、隐藏 reasoning trace、thought、vote、working memory。
- 单独的 `VerificationReport`，除非它已被 `CANONICAL_OUTPUTS_BOUND` 选中。

因此，Phase 4 的职责边界是：

```text
Verification 证明“这个候选可用”。
Canonical selection 决定“哪个候选可被后续协议消费”。
Expansion 只消费 canonical selection 已绑定的结果。
```

### 2.14 `ExpectedOutputRef` 的存放形态

已确认：`ExpectedOutputRef` 是协议级 output future / resolution 对象，采用独立 dataclass
和 SQLite index-only projection；不要把权威 resolution 状态放进 `TaskUnit.plugin_payload`
或 `metadata`。

边界如下：

- `DecompositionProposal.expected_outputs` 是插件 split strategy 生成的声明，描述父节点 named output 将如何被满足。
- accepted expand batch 落账后，协议从 accepted proposal 和 `TASK_EXPANDED` 派生 `ExpectedOutputRef` projection。
- JSONL 权威事实仍是 accepted proposal artifact、`DECOMPOSITION_PROPOSAL_RECORDED`、accepted `EXPANSION_DECISION_RECORDED` 和最终 `TASK_EXPANDED`；SQLite 只保存可重建索引。
- Phase 4 第一版不新增单独 `EXPECTED_OUTPUT_REF_CREATED` event，避免扩展已冻结的 expand batch 事件顺序。
- `TaskUnit.plugin_payload` 可以保存插件私有执行配置，但不能承载协议权威 output resolution 状态。
- `TaskUnit.metadata` 只能保存展示或审计摘要，不能作为 replay、merge readiness 或父节点完成判断的权威来源。

第一版最小字段建议：

```text
expected_output_id
task_id
owner_unit_id
output_name
schema_ref
resolution_kind: direct_parent_output | child_output | merge_plan_output
resolution_status: expected | blocked | resolved
child_unit_id
child_output_name
merge_plan_id
canonical_selection_id
canonical_output_bundle_digest
source_proposal_id
source_expansion_decision_id
created_event_seq
resolved_event_seq
```

其中：

- `expected_output_id` 从 `source_proposal_id` / `owner_unit_id` / `output_name` / logical position 确定性派生。
- `created_event_seq` 指向 accepted expansion batch 中让该 ref 可见的事件序列；第一版可使用最终 `TASK_EXPANDED.event_seq` 作为可见边界。
- `resolved_event_seq` 在 Phase 4 可以保持 `null`；后续 Phase 5 merge / parent readiness 决定何时填充。
- `resolution_status` 不由插件私有字段直接修改，只能由 protocol projection 从 authoritative events 重建。

### 2.15 Phase 4 SQLite index-only projection 范围

已确认：Phase 4 一次加入本阶段一级事实的 SQLite index-only projection，并且第一版包含
`expected_output_refs`。SQLite 仍是可重建索引，不是权威状态；完整 body 仍从 artifact
读取，权威顺序仍来自 JSONL ledger。

第一版新增表：

| 表 | 作用 | 可见性 / 权威边界 |
|---|---|---|
| `verification_reports` | 查询验证报告、状态、关联 submission / attempt、验证 event sequence 和候选 bundle digest。 | 从 `VERIFICATION_RECORDED` 重建；完整报告 body 如较大应通过 artifact ref 读取。 |
| `canonical_outputs` | 查询每个 `TaskUnit` 唯一 canonical selection、bundle digest、selected verification report 和被选 attempt。 | 从 `CANONICAL_OUTPUTS_BOUND` 重建；对 `(task_id, unit_id)` 形成唯一投影约束，用于暴露账本矛盾。 |
| `split_strategy_invocations` | 查询插件 split strategy 调用审计、status、result_action、result_digest 和错误摘要。 | 从 `SPLIT_STRATEGY_INVOCATION_RECORDED` 重建；失败或 invalid_result 不推进状态。 |
| `decomposition_proposals` | 查询 accepted proposal artifact ref、proposal digest、scope 和来源 plugin / split strategy。 | 只有 `DECOMPOSITION_PROPOSAL_RECORDED` 进入 accepted expand batch 后才是权威 proposal。 |
| `expansion_decisions` | 查询同一 expansion scope 的唯一 accepted complete / expand decision。 | 从 `EXPANSION_DECISION_RECORDED` 重建；同 scope 多 decision 是冲突。 |
| `merge_plans` | 查询 accepted merge plan artifact ref、merge plan digest、merge policy identity 和 required slot 摘要。 | 只有 `MERGE_PLAN_RECORDED` 进入 accepted expand batch 后才是权威 merge plan。 |
| `expected_output_refs` | 查询父节点 named output future / resolution、resolution status、child output 或 merge plan 指向。 | 从 accepted proposal + final `TASK_EXPANDED` 派生；第一版不新增单独 event。 |

Expansion-derived rows 的可见性规则：

- `decomposition_proposals`、`merge_plans` 和 `expected_output_refs` 只有在同一 batch 最终
  `TASK_EXPANDED` 可见后，才对 replay / projection 视为完整 expansion 的可消费索引。
- 如果 ledger 中缺少 final `TASK_EXPANDED`，projection 可以保留低层 ledger event 查询记录，
  但不得把该 expansion 暴露为 accepted graph mutation 或可 merge 的 output resolution。
- 第一版不拆出 `required_slots` / `merge_slots` / `child_output_slots` 等细表；这些留给
  Phase 5 merge readiness 高频查询时再扩展。

## 3. Phase 4 开工前待定项状态

以下三项原待定问题已经确认，可以作为 Phase 4 TDD 和实现的输入：

1. SQLite projection 一次加入 Phase 4 一级事实表，并包含 `expected_output_refs`；expansion-derived rows 只在最终 `TASK_EXPANDED` 后可见。
2. Attempt 只推进验证 / canonical selection 结果：invalid output 走 `Rejected`，被选 canonical 走 `Canonical`，canonical loser 保持 `Verified`；重复 proposal / duplicate expansion 不改变 Attempt。
3. `append_batch()` 的 batch identity 进入 `LedgerEvent` envelope，第一版使用 `batch_id`、`batch_index`、`batch_size`；`correlation_id` 只做流程追踪。

## 4. 当前建议的下一步

Phase 4 设计收束项已具备进入 TDD / 实现的前置条件。下一步应先写 Phase 4 专用字段规格和测试计划，再进入代码实现；实现仍不得提前进入 Phase 5 merge、contribution、settlement 或 Phase 6 实验插件完整流程。

需要在实现计划中细化、但不再阻塞设计收束的事项包括：Phase 4 dataclass 字段、事件 payload 最小字段、SQLite 列名、测试夹具、`ProtocolEngine` / `VerificationOrchestrator` / `ExpansionCoordinator` 的代码切分，以及与既有 Phase 2/3 state machine 的兼容迁移。
