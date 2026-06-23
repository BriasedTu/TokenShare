# Phase 3 插件与执行器字段规格草案

## 元数据

| 项目 | 内容 |
|---|---|
| 日期 | 2026-06-23 |
| 状态 | Draft / implementation-start ready |
| 对应 feature | `feat-004` - Phase 3 - Plugin and Executor Contracts |
| 上游设计依据 | `Doc/TechnicalDocument/2026-06-03-tokenshare-protocol-technical-design.md` 第 4.3、8、12、21 节；`Doc/TechnicalDocument/2026-06-08-phase-2-code-map.md` |
| 目的 | 记录 Phase 3 字段和机制讨论的当前结论，避免后续实现遗漏协议边界。 |

## 1. 当前已确认的设计取向

Phase 3 采用“执行闭环优先”的字段设计：先围绕 `ExecutionRequest` 与 `ExecutionSubmission` 定义统一执行契约，再把插件 descriptor、执行器 descriptor、分派解释、环境身份和 AI artifact 边界作为 request/submission 的组成部分或引用对象纳入。

本草案已经收束下一轮 TDD 和实现所需的关键字段抉择；它不实现代码，也不标记 `feat-004` 完成。

## 2. 非目标

Phase 3 不做以下事项：

- 不调用生产 AI API。
- 不实现真实分布式 executor 网络。
- 不执行 submission verification。
- 不绑定 canonical output。
- 不实现 expansion、merge 或 settlement。
- 不把 factorization、Lean stub 或 structured report stub 的领域规则硬编码进协议核心。

## 3. 三层持久化原则

本轮已确认：`ExecutionRequest` 和 `ExecutionSubmission` 本体保存为 artifact，JSONL event payload 只保存 `ArtifactRef` 和最小索引摘要。

三层分别是：

1. Artifact 内容：完整 `ExecutionRequest` / `ExecutionSubmission` JSON。
2. `ArtifactRef`：内容哈希、URI、schema、大小、创建时间。
3. Event payload：只放 request/submission 的 ref、digest 和必要索引字段。

这样可以保持 event ledger 短小、结构化、可 hash、可投影，同时让 replay 和 audit 可以按 artifact hash 找回完整 request/submission。

## 4. 已确认实施抉择

本节把字段讨论中已经收束的抉择固定下来，作为下一轮 TDD 和实现的默认依据。

| 抉择 | 结论 | 理由 |
|---|---|---|
| `AllocationDecision` 保存方式 | 内联进 `ExecutionRequest` artifact，作为执行时分派解释 snapshot；Phase 3 不单独 artifact 化。 | Phase 2 已有 `SchedulingDecision`，Phase 3 只需要让 request 携带更完整的 eligibility、排序、tie-break 和 no-match 摘要，避免第一版多一层对象和事件复杂度。 |
| `PluginDescriptor` / `ExecutorDescriptor` 保存方式 | descriptor 本体保存为 artifact；registry snapshot 和 request 中只保存 descriptor ref/digest 与必要查询摘要。 | 与 request/submission artifact 化原则一致，满足版本固定、replay 和 audit。 |
| executor status enum | 第一版使用 `Available`、`Busy`、`Offline`、`Disabled`。调度只把 `Available` 视为可分派，其它状态都必须产生明确 no-match reason。 | 替代 Phase 2 scheduler 中硬编码的 client availability 字符串，同时不提前引入生产 worker lifecycle。 |
| SQLite Phase 3 projection | 只投影索引，不保存完整 body；最小表为 `registry_snapshots`、`execution_requests`、`execution_submissions`、`executor_statuses`。 | 完整 request/submission/descriptor 从 artifact 读取，SQLite 继续保持可重建查询视图而不是权威状态源。 |

额外状态机抉择：`EXECUTION_REQUEST_RECORDED` 只记录 request 已发布，不推进 `Attempt` 状态；`EXECUTION_SUBMISSION_RECORDED` 成功记录后，再写 `ATTEMPT_STATE_CHANGED Running -> Submitted`。

## 5. 主要协议对象与 artifact 类型

| 名称 | 类型 | 是否字段 | 作用 |
|---|---|---|---|
| `PluginDescriptor` | artifact 内容类型 / dataclass 候选 | 否 | 描述插件 ID、版本、输入输出 schema、执行契约、验证/合并策略声明；registry 和 request 只保存 ref/digest。 |
| `PluginRegistry` | 组件 | 否 | 管理并冻结本轮 run 可用插件版本。 |
| `ExecutorDescriptor` | artifact 内容类型 / dataclass 候选 | 否 | 描述执行器 ID、类型、版本、能力、环境策略和状态枚举；registry 和 request 只保存 ref/digest。 |
| `ExecutorRegistry` | 组件 | 否 | 管理执行器 descriptor、client/executor status 和能力查询。 |
| `ExecutionRequest` | artifact 内容类型 | 否 | 协议授权 executor 执行某 attempt 的完整结构化请求。 |
| `ExecutionSubmission` | artifact 内容类型 | 否 | executor 对某 request 的完整结构化提交。 |
| `OutputContract` | 子对象 | 可作为 request 字段 | 描述必需命名输出、schema、raw/parsed/candidate/parse failure contract。 |
| `EnvironmentRef` | 子对象 / artifact ref 候选 | 可作为 request/submission 字段 | 描述不可变执行环境身份和 digest。 |
| `PromptPackage` | artifact 内容类型 | 否 | AI/mock AI 路径实际使用的 prompt 包。 |
| `RawModelOutput` | artifact 内容类型 | 否 | AI/mock AI 原始输出或错误文本。 |
| `ParsedModelOutput` | artifact 内容类型 | 否 | 从 raw output 解析出的结构化候选输出。 |
| `ParseFailureReport` | artifact 内容类型 | 否 | raw output 无法解析为候选输出时的结构化失败报告。 |
| `ProvenanceRecord` | artifact 内容类型 | 否 | action、observation、tool/version、输入输出 digest 和错误证据。 |

## 6. `PluginDescriptor` 字段草案

`PluginDescriptor` 描述插件能力，不执行任务、不写协议状态。Descriptor 本体必须保存为 artifact，registry snapshot 和 request 只保存 `descriptor_ref` / `descriptor_digest` 与必要查询摘要。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始建议 `phase3.plugin_descriptor.v1`。 |
| `plugin_id` | string | 是 | 插件稳定 ID。 |
| `plugin_version` | string | 是 | 本轮 run 固定使用的插件版本。 |
| `supported_task_types` | list[string] | 是 | 支持的 `TaskUnit.unit_type`。 |
| `input_contract` | object | 是 | 输入 artifact 和 schema 要求。 |
| `output_contracts` | map[string, OutputContract] | 是 | 按 task type 或 output profile 声明命名输出。 |
| `execution_contracts` | map[string, object] | 是 | 按 executor type 声明 hard requirements、soft hints、environment policy 和 output contract。 |
| `validator_policy_id` | string/null | 否 | 只声明 Phase 4 可能使用的验证策略，不在 Phase 3 执行验证。 |
| `merge_policy_id` | string/null | 否 | 只声明未来 merge 策略，不在 Phase 3 执行 merge。 |
| `metadata` | object | 否 | 小型结构化扩展。 |
| `descriptor_digest` | string | 是 | descriptor canonical JSON 的内容 digest，用于 freeze 和 replay。 |

## 7. `ExecutorDescriptor` 字段草案

`ExecutorDescriptor` 描述一个本地执行器实现或模拟执行器的能力。Descriptor 本体必须保存为 artifact，registry snapshot、request 和 projection 只保存 `descriptor_ref` / `descriptor_digest` 与必要查询摘要。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始建议 `phase3.executor_descriptor.v1`。 |
| `executor_id` | string | 是 | 执行器稳定 ID。 |
| `executor_type` | string | 是 | 例如 `mock_ai`、`deterministic_local`。 |
| `executor_version` | string | 是 | 执行器版本。 |
| `supported_request_schema_versions` | list[string] | 是 | 可处理的 `ExecutionRequest` schema version。 |
| `capabilities` | object | 是 | 能力声明，例如 output mode、工具、语言、运行环境。 |
| `environment_policy` | object | 是 | 支持或要求的执行环境约束。 |
| `status` | string | 是 | 第一版枚举固定为 `Available`、`Busy`、`Offline`、`Disabled`；只有 `Available` 可被调度。 |
| `metadata` | object | 否 | 小型结构化扩展。 |
| `descriptor_digest` | string | 是 | descriptor canonical JSON 的内容 digest。 |

## 8. Registry freeze 字段草案

`PluginRegistry` 和 `ExecutorRegistry` 是组件；需要持久化的是运行期 registry snapshot，而不是动态注册表对象本身。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始建议 `phase3.registry_snapshot.v1`。 |
| `registry_snapshot_id` | string | 是 | 本轮 run 的注册表快照 ID。 |
| `task_id` | string | 是 | 所属 root task。 |
| `plugin_entries` | list[object] | 是 | 每项至少包含 `plugin_id`、`plugin_version`、`descriptor_ref`、`descriptor_digest`。 |
| `executor_entries` | list[object] | 是 | 每项至少包含 `executor_id`、`executor_version`、`descriptor_ref`、`descriptor_digest`、`status`。 |
| `frozen_at` | string | 是 | UTC ISO 8601。 |
| `metadata` | object | 否 | 小型结构化扩展。 |

Replay 不应查询“当前 registry”；只能读取历史 registry snapshot、descriptor artifact/ref 和 event ledger。

## 9. `OutputContract` 字段草案

`OutputContract` 是 request/submission 之间的桥，说明 executor 应该产出什么，但不判断结果可信。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 初始建议 `phase3.output_contract.v1`。 |
| `output_contract_id` | string | 是 | 输出契约 ID。 |
| `required_outputs` | list[string] | 是 | 必需命名输出。 |
| `optional_outputs` | list[string] | 否 | 可选命名输出。 |
| `output_schema_refs` | map[string, object] | 是 | 每个命名输出对应的 schema ref 或 schema digest。 |
| `raw_output_policy` | object | 是 | 是否允许 raw text、最大大小、media type 等。 |
| `parsed_output_schema_ref` | object/null | 否 | 解析后结构化对象 schema。 |
| `candidate_bundle_schema_ref` | object/null | 否 | 候选输出 bundle schema。 |
| `parse_failure_schema_ref` | object/null | 否 | 解析失败报告 schema。 |

## 10. `ExecutionRequest` artifact 字段草案

Artifact schema 建议为 `phase3.execution_request.v1`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase3.execution_request.v1`。 |
| `request_id` | string | 是 | 请求 ID。 |
| `task_id` | string | 是 | 所属 root task。 |
| `unit_id` | string | 是 | 被执行的 unit。 |
| `attempt_id` | string | 是 | 被授权执行的 attempt。 |
| `lease_id` | string | 是 | 授权该 attempt 的 lease。 |
| `fencing_token` | string | 是 | 用于拒绝迟到或旧 lease submission。 |
| `plugin` | object | 是 | 至少包含 `plugin_id`、`plugin_version`、`plugin_descriptor_ref`、`plugin_descriptor_digest`。 |
| `executor` | object | 是 | 至少包含 `executor_id`、`executor_version`、`executor_descriptor_ref`、`executor_descriptor_digest`。 |
| `registry_snapshot_id` | string | 是 | 本轮固定 registry snapshot。 |
| `allocation_decision` | object | 是 | 内联分派解释 snapshot；应包含 eligibility、排序、tie-break 和 no-match 相关摘要；Phase 3 不单独 artifact 化。 |
| `capability_snapshot` | object | 是 | 调度当下 executor/client 能力快照。 |
| `task_unit_snapshot` | object | 是 | 执行时 `TaskUnit` 摘要。 |
| `input_artifact_refs` | map[string, ArtifactRef] | 是 | 执行输入快照。 |
| `output_contract` | OutputContract | 是 | 输出契约。 |
| `hard_requirements` | object | 是 | 不满足则不能分派。 |
| `soft_hints` | object | 否 | 只影响排序或 executor 提示。 |
| `environment_ref` | object | 是 | 不可变环境身份。 |
| `execution_instruction_ref` | ArtifactRef/null | 否 | 插件生成的执行说明。 |
| `prompt_package_ref` | ArtifactRef/null | 否 | AI/mock AI 路径使用的 prompt package；deterministic executor 可为空。 |
| `limits` | object | 是 | 时间、预算和资源限制。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

## 11. `ExecutionRequest` event payload 草案

新增 event type 建议：`EXECUTION_REQUEST_RECORDED`。

Event payload 不展开完整 request，只保存可投影摘要。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase3.execution_request_record.v1`。 |
| `request_id` | string | 是 | 请求 ID。 |
| `task_id` | string | 是 | 所属 root task。 |
| `unit_id` | string | 是 | 被执行的 unit。 |
| `attempt_id` | string | 是 | 对应 attempt。 |
| `lease_id` | string | 是 | 对应 lease。 |
| `request_ref` | ArtifactRef | 是 | 完整 request artifact ref。 |
| `request_digest` | string | 是 | request artifact 内容 digest。 |
| `plugin_id` | string | 是 | 查询摘要。 |
| `executor_id` | string | 是 | 查询摘要。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

`EXECUTION_REQUEST_RECORDED` 不推进 `Attempt` 状态；它只证明协议已发布一个可审计的执行请求 artifact。

## 12. `ExecutionSubmission` artifact 字段草案

Artifact schema 建议为 `phase3.execution_submission.v1`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase3.execution_submission.v1`。 |
| `submission_id` | string | 是 | 提交 ID。 |
| `request_id` | string | 是 | 对应 request。 |
| `task_id` | string | 是 | 所属 root task。 |
| `unit_id` | string | 是 | 被执行的 unit。 |
| `attempt_id` | string | 是 | 对应 attempt。 |
| `lease_id` | string | 是 | 对应 lease。 |
| `fencing_token` | string | 是 | 回显 fencing token。 |
| `executor_id` | string | 是 | 执行器 ID。 |
| `executor_version` | string | 是 | 执行器版本。 |
| `result_kind` | string | 是 | 建议包含 `succeeded`、`executor_error`、`invalid_output`、`parse_failed`、`late_submission`。 |
| `raw_output_ref` | ArtifactRef/null | 否 | 原始输出 artifact。 |
| `parsed_output_ref` | ArtifactRef/null | 否 | 解析后结构化输出 artifact。 |
| `candidate_output_refs` | map[string, ArtifactRef] | 否 | 候选命名输出 bundle。 |
| `parse_failure_ref` | ArtifactRef/null | 否 | 解析失败报告 artifact。 |
| `log_ref` | ArtifactRef/null | 否 | 执行日志引用。 |
| `environment_ref` | object | 是 | 回显 request 中的不可变环境身份。 |
| `environment_summary` | object | 是 | 实际运行环境摘要，不放 secrets。 |
| `provenance_ref` | ArtifactRef/null | 否 | action/observation/tool/version/input-output digest/error 证据。 |
| `usage_summary` | object | 否 | token、时间、成本或本地资源摘要。 |
| `error` | object/null | 否 | 结构化错误；长日志走 artifact。 |
| `submitted_at` | string | 是 | UTC ISO 8601。 |

`ExecutionSubmission` 不包含 `verified`、`canonical`、`merge_result` 等 Phase 4/5 字段。

## 13. `ExecutionSubmission` event payload 草案

新增 event type 建议：`EXECUTION_SUBMISSION_RECORDED`。

Event payload 不展开完整 submission，只保存可投影摘要。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `phase3.execution_submission_record.v1`。 |
| `submission_id` | string | 是 | 提交 ID。 |
| `request_id` | string | 是 | 对应 request。 |
| `task_id` | string | 是 | 所属 root task。 |
| `unit_id` | string | 是 | 被执行的 unit。 |
| `attempt_id` | string | 是 | 对应 attempt。 |
| `lease_id` | string | 是 | 对应 lease。 |
| `submission_ref` | ArtifactRef | 是 | 完整 submission artifact ref。 |
| `submission_digest` | string | 是 | submission artifact 内容 digest。 |
| `result_kind` | string | 是 | 恢复和指标所需的最小摘要。 |
| `submitted_at` | string | 是 | UTC ISO 8601。 |

## 14. Attempt 状态推进决策

本轮已确认：Phase 3 收到并记录 `ExecutionSubmission` 后，应将对应 `Attempt` 从 `Running` 推进到 `Submitted`。

约束：

- `Submitted` 只表示 executor 已交回统一提交。
- `Submitted` 不表示输出已验证。
- Phase 3 不进入 `Verifying`、`Verified`、`Canonical`。
- 后续 Phase 4 再由 verification flow 推进 `Submitted -> Verifying -> Verified/Rejected`。

建议事件顺序：

1. 保存 `ExecutionSubmission` artifact。
2. 写 `EXECUTION_SUBMISSION_RECORDED`，payload 只含 `submission_ref` 和摘要。
3. 仅当 submission 的 `task_id`、`unit_id`、`attempt_id`、`lease_id` 和 `fencing_token` 与当前 running attempt/lease 匹配时，写 `ATTEMPT_STATE_CHANGED`：`Running -> Submitted`；payload 中的 attempt snapshot 可引用 `raw_output_ref`、`parsed_output_ref`、`candidate_output_refs`、`log_ref` 等 artifact refs。

迟到、attempt/lease 不匹配或 fencing token 不匹配的提交仍可保存为 audit artifact 和 `EXECUTION_SUBMISSION_RECORDED`，但不得让已过期、已 superseded、已 terminal 或不匹配的 attempt 进入 `Submitted`。

## 15. AI artifact 边界

AI/mock AI 路径必须把非确定性文本保存为 artifact：

- `PromptPackage`：保存实际给 AI/mock AI 的 prompt 包、输入摘要、输出 schema、约束、seed/profile。
- `RawModelOutput`：保存模型原始文本或错误文本。
- `ParsedModelOutput`：保存从 raw output 解析出的结构化候选输出。
- `ParseFailureReport`：保存解析失败原因、raw output ref 和缺失/非法字段摘要。

Event payload 不嵌入长自然语言正文。

## 16. 环境与 provenance 边界

`EnvironmentRef` 和 provenance 必须支持 replay/audit，但不要求完整复现生产环境。

`EnvironmentRef` 建议包含：

- `environment_id`
- `environment_digest`
- `runtime`
- `tool_versions`
- `resource_limits`
- `fixture_profile_digest`
- `seed`
- `clock_policy`
- `created_at`
- `schema_version`

`ProvenanceRecord` 建议作为 artifact 保存 action、observation、tool/version、输入输出 digest 和错误摘要。隐藏 reasoning trace 不是协议真值，也不是 replay 前置条件。

## 17. SQLite Phase 3 projection

SQLite 仍然只做可重建查询视图，不保存完整 descriptor、request 或 submission body。最小 projection 表如下：

| 表名 | 来源事件 | 内容边界 |
|---|---|---|
| `registry_snapshots` | `REGISTRY_SNAPSHOT_RECORDED` | `registry_snapshot_id`、task、frozen time、plugin/executor entry 摘要和 descriptor digest。 |
| `execution_requests` | `EXECUTION_REQUEST_RECORDED` | `request_id`、task/unit/attempt/lease、request artifact id/digest、plugin/executor 摘要、created time。 |
| `execution_submissions` | `EXECUTION_SUBMISSION_RECORDED` | `submission_id`、request/task/unit/attempt/lease、submission artifact id/digest、`result_kind`、submitted time。 |
| `executor_statuses` | executor registration/status events | `executor_id`、version、status、descriptor digest、last updated time。 |

删除 SQLite 后，以上 projection 必须能从 JSONL event ledger 和 artifact refs 重建。实现不得从 SQLite 读取完整 request/submission body 作为权威事实。

## 18. 下一轮可直接开工的实现范围

下一个 agent 可以从本草案直接进入 TDD 和实现，但范围必须保持在 `feat-004`：

1. 新增 descriptor、registry snapshot、request/submission、environment、provenance 和 AI artifact 的 dataclass / helper。
2. 新增 `EXECUTION_REQUEST_RECORDED` 与 `EXECUTION_SUBMISSION_RECORDED` event type。
3. 保存 request/submission/descriptor artifact，并在 event 中只记录 ref、digest 和摘要。
4. 收到与当前 running attempt/lease/fencing token 匹配的有效 submission 后写 `ATTEMPT_STATE_CHANGED Running -> Submitted`。
5. 扩展 SQLite projection 的四张最小索引表。

不得实现 submission verification、canonical output binding、task expansion、merge、settlement、真实网络 executor 或生产 AI API。

## 19. 开工对齐备注

为避免实现时再次引入旧口径，Phase 3 开工时还必须同步以下代码对齐项：

1. `RootTaskRegistrar` 继续冻结为 Phase 1 legacy helper，不在其中扩展 Phase 3 request/submission 或 registry orchestration；如需新增编排，放到顶层 application service。
2. `Scheduler` 的 client availability 字符串已收束到 Phase 3 序列化状态 `Available`，并保留 Phase 2 legacy `active` 兼容入口；`ExecutorRegistry` 以 `Available`、`Busy`、`Offline`、`Disabled` 为第一版显式枚举。
3. `Attempt` 状态机需要开放 `Running -> Submitted`，并把 `submitted_at`、output artifact refs 和 late-submission 审计边界与 `EXECUTION_SUBMISSION_RECORDED` 对齐。
