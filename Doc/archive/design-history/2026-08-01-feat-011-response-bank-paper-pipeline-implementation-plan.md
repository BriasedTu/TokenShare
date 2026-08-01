# Feat-011 Response Bank Paper Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 EPD-027 的 exact outbound request identity、不可变 response bank、真实 acquisition 与 trace consumption 分账、完整 TokenShare 状态机 trace、Experiment 1–5 指标契约和逐 numeric cell 可追溯论文管线；用 component、冻结历史真实 artifact、新真实 496-call 上界 smoke、cell-lineage deterministic recomputation 四级验收逐层证明。

**Architecture:** Adapter 先构造 `PreparedOutboundRequest`，其唯一序列化 bytes 同时用于 artifact、digest 与 wire；approved acquisition 以 SQLite WAL 原子预算账本封存 success/provider-failure bank entries；current run 只持有 opaque `ExternalBankObjectLocator`，通过离散事件 clock 按 source latency 驱动正常 coordinator/lease/fault/death/requeue/parser/verifier/checker/canonical/merge/ledger；direct results 固定全部 preregistered denominator，projector 只生成 `PaperMetricObservation`，renderer 只消费 metric-contract allowlist，cell lineage 连接 numeric value 到全部 current/source evidence。

**Tech Stack:** Python 3、pytest、SQLite WAL、JSON/JSONL、PowerShell、现有 `ArtifactStore` / `ProtocolRunCoordinator` / paper adapters / formal evidence pipeline、固定本地 Lean/lake checker fixture。

---

**Status:** `approved-for-offline-implementation`

**Plan date:** 2026-08-01

**Repository root:** `E:\TokenEcnomic\TokenShare`

## 1. 原则、非执行声明与范围硬门

1. 本文件是独立 reviewers 判定初稿 NO-GO 后的重构版计划，不表示任何实现、验证、网络调用或论文结果已经完成。
2. 本轮计划作者只修改本文件；不得修改代码、测试、配置、authority、harness 或其他文档，不得调用网络、AI API 或付费实验。
3. 用户批准本计划只授权离线实现。任何 provider-writing action 还必须取得用户显式提供、digest/scope/expiry/fresh-output 绑定的 paid receipt，并同时传入 `--allow-provider-calls`；自动实施 agent 不得创建、推断或伪造 paid receipt。
4. 实施严格按 Task 0–34 单 Task 串行。Task 0–3 保留已经落盘的验收方式与证据；从 Task 4 起，每个 Task 使用一个 fresh `gpt-5.6-sol` developer 做 RED→最小实现→GREEN，默认 `reasoning_effort=high`，只有论文指标边界、正式全链路或真实 Critical 才升级 `ultra`；随后由一个 fresh 综合独立 reviewer 同时完成规格、质量、全链与影子 TokenShare 审查。Critical/Important 清零且该 Task accepted 后，才允许建议 commit 和下一 Task。
5. 不允许并行实现、批量 ownership 或“先集成后复审”。综合 reviewer 只读；审查或修正未通过时，修复必须回到当前 Task 的 developer，并由当前同一个综合 reviewer 做 follow-up，继续在同一 Task 内修正和复核直至 PASS；Task 未 accepted 绝不得进入下一 Task，不得引入第二 reviewer。
6. 离线 Task 的每条 RED/GREEN 命令必须显式加载最低层 network tripwire；tripwire 在 socket/urllib outbound 边界拒绝网络，且断言 provider calls=0。
7. 本轮只允许运行定向 pytest、四个 focused verification profiles、`.\init.ps1` Fast、一个 Lean fixture/最多两个 checker calls，以及在有效 paid receipt 下的冻结 L3 小型真实 smoke。
8. 明确禁止 `.\init.ps1 -Full`、全量 `pytest tests`、LeanAudit、force-all、600-entry/大批 Lean、正式 response-bank 全量 acquisition、Exp1/Exp5 full online、P0-core/P0-full 正式矩阵和人工攻击/篡改测试，除非用户未来另行授权。
9. L3/L4 因 receipt、secret、quota、预算或外部 provider 状态不足而 blocked 时，必须记录 `blocked`，不能算 passed。自动实施继续完成其余离线 Task 和 handoff，不等待或伪造授权。
10. 旧 evidence/raw/prompt v1/smoke 输出严格只读，不升级 evidence class、不改 hash、不补 provenance。旧 Exp3/4 2026-07-31 smoke 只作已知失败 negative；L2 正向 fixture 使用第 6.2 节指定的真实成功源。
11. `cost_estimate` 仅表示真实调用 usage × 冻结 pricing 的估算，不得称为 actual bill、实付账单或 provider invoice。
12. Full/Fast 限制与 `feat-011` 状态必须诚实：无 Full 仍保持 `in-progress` 和 formal `NO-GO`。

## 2. 权威、覆盖关系与状态模型

### 2.1 权威顺序

1. 用户本轮指令和根 `AGENTS.md`。
2. `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`，以 EPD-027 新两阶段条目覆盖同文档残留的 shared-Exp1 历史表述。
3. `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md` 的 EPD-013–027。
4. 用户批准后的 tracked profile digest；paid receipt 只授权其精确 scope，不改变设计。
5. 历史计划、old smoke 和旧 evidence 只供 provenance。

### 2.2 覆盖与 legacy triage

- Exp2–4 main 从旧 full-online 改为 `real_model_trace_protocol_run`；不得冒充当次 `real_transport`。
- Exp3 baseline 从 shared Exp1 actual 改为同 `case_id × sample_slot × replacement_slot` 的 `paired_trace_reference`。
- 删除 current-path `FormalEvidenceStore.build_shared_root_reference()`、runner `_ensure_exp3_shared_exp1_reference()` 与 shared-Exp1 专属 helper/schema/tests；不创建旧计划中的 `ValidatedSharedRootReferenceIndex`。
- 保留通用 root-delta/checkpoint/terminal compaction、`retain_outcomes=False` 和滚动磁盘预测；full-resource A 改为 `ValidatedResponseBankIndex + external ResponseBankLocator`，500-root 压力测 trace path，不递归扫描/复制 source raw。
- 旧 `local/run_exp1_exp4_v3_smoke.ps1`、`run_exp3_exp4_v3_smoke.ps1`、`run_exp5_v3_smoke.ps1` 都含 EPD-027 不允许的旧语义；改为 secret 前 fail-closed shim。新入口单独建立。

### 2.3 分离状态

实现/运行状态必须分别记录，禁止合并成一个 completed：

- `facility_offline_implemented`：Tasks 0–32 的离线实现与 L1/L2 验收均通过。
- `l3_new_real_smoke_verified` 或 `l3_new_real_smoke_blocked`：Task 33 的新真实 capability+online smoke。
- `l4_cell_traceability_verified` 或 `l4_cell_traceability_blocked`：Task 34 的逐 numeric cell audit 与二次 deterministic recomputation。
- `formal_matrix_no_go`：完整 bank、正式 Exp1/5 和正式矩阵未获单独授权/未完成时始终成立。

Blocked 不是 passed；`facility_offline_implemented` 也不表示 L3/L4 或论文结果完成。

## 3. 核心版本化契约

### 3.1 Exact body：只序列化一次

```python
@dataclass(frozen=True, kw_only=True)
class PreparedOutboundRequest:
    schema_version: Literal["tokenshare.prepared_outbound_request.v1"]
    body_obj: JsonObject
    body_bytes: bytes
    body_digest: str
    serialization_profile: Literal["canonical_json_utf8_v1"]
    normalized_absolute_endpoint: str
    provider_config_digest: str
    entry_id: str
    configured_model: str
    effective_controls_digest: str
    plugin_id: str
    plugin_version: str
    prompt_profile_id: str
    prompt_serialization_schema: str
    body_serialization_schema: str
    prompt_admission_profile_id: str
    prompt_admission_profile_version: int
    prompt_admission_profile_digest: str
    estimated_prompt_tokens: int
    case_id: str
    planned_ai_unit_id: str
    sample_slot_index: int
    replacement_slot: int
    inference_request_digest: str
```

`PreparedOutboundRequestFactory` 是全仓唯一构造入口；`AIAPIExecutor`、`ai_profile`、`factorization_500_ai`、Factor/Lean paper adapters、`run_paper_experiments` router 以及 budgeted/scripted/capturing wrappers 都不得手工构造 request dict 或绕过 factory。Factory 只 canonical serialize 一次；`validate_prepared_request()` 在 artifact、reserve 与 wire 前分别由 `body_obj` 重算 canonical bytes、由 bytes 重算 digest，并逐字节比对 `body_bytes/body_digest`，任一不一致 fail closed。ArtifactStore 保存这些 `body_bytes`；transport 直接发送相同 bytes，禁止再次 `json.dumps`；wire URL 必须与 `normalized_absolute_endpoint` 字节解码后的绝对 URL 完全相同，不能再拼 path 或重做 normalization。`inference_request_digest` 绑定 body digest、normalized absolute endpoint、provider config/entry/model/effective controls、plugin/prompt/body schemas、case/stable unit/sample/replacement；排除 condition、worker、request/attempt、lease/fence、clock、run path。

顺序固定为 `factory prepare → consistency validation → conservative prompt-token admission → prepared artifact → SQLite reserve → dispatch_intent → secret resolve → exact endpoint/body wire`。Prompt admission冻结为：

- id=`tokenshare.prompt_admission.utf8_byte_upper.v1`，version=`1`；
- canonical profile JSON=`{"algorithm":"estimated_prompt_tokens=utf8_byte_length(canonical_body_bytes)+8*message_count+16","canonical_body_schema":"canonical_json_utf8_v1","includes":["system","messages","canonical_body_overhead","unicode_utf8_byte_upper"],"profile_id":"tokenshare.prompt_admission.utf8_byte_upper.v1","version":1}`；
- digest=`sha256:e693ef1c9dbf50aff36aaae1b14f7029c5954c6708a6570182e69a7051685d49`；
- `canonical_body_bytes` 已包含 system/messages roles/content和canonical JSON结构/转义开销；UTF-8 byte length以“每byte至多一个token”作Unicode保守上界，再加每message 8 tokens及固定16 tokens provider framing；`estimated_prompt_tokens=len(body_bytes)+8*message_count+16 <= 32768`。

Profile、budget digest、inventory row、PreparedOutboundRequest和paid receipt identity都绑定该admission digest。算法/id/version/digest任一漂移必须改变所有下游digests，并在budget reserve、API-key env read与dispatch前拒绝。Reservation 的 completion ceiling 仍为 `300000`，不能拿总 token ceiling 代替 prompt admission。

Lean prompt v2 的 candidate id 由 theorem payload digest、stable planned unit、plugin/prompt version 派生，不含 request/attempt。Lean v1 只读 replay，旧 raw 不重写。

### 3.2 External bank locator：current suite 不持有 source ArtifactRef

```python
@dataclass(frozen=True, kw_only=True)
class ExternalBankObjectLocator:
    bank_root_id: str
    manifest_digest: str
    entry_id: str
    object_role: Literal[
        "request_body", "raw_output", "provider_failure", "provenance",
        "usage", "latency", "pricing", "acquisition_attempt", "model_record"
    ]
    object_digest: str
```

配套 schema 固定为：

- `ResponseBankManifest.v1`：`bank_root_id/manifest_digest/profile_digest/budget_digest/inventory_digest/provider_config_digest/entry_ids/object_role_schema/terminal_entry_count/created_by_paid_receipt_digest/root_binding_marker_digest`；`entry_ids` 唯一且 canonical sort。
- `ResponseBankInventoryRow.v1`：`inventory_entry_id = SHA256(canonical inventory row excluding inventory_entry_id)`；canonical preimage包含所有其余字段且只排除id自身，因此无自指。Loader必须重算并逐字节比较；相同row跨process/replay稳定，任一其余字段篡改都改变id并fail closed。`semantic_slot_key`精确绑定 `case_record_digest/planned_ai_unit_id/sample_slot_index/replacement_slot/provider_config_digest/prompt_profile_digest/prompt_admission_profile_digest/plugin_version`；row另存唯一 preregistered `inference_request_digest`。同一semantic slot不得出现第二个body/request digest。
- `ResponseBankEntry.v1`：唯一键=`(inventory_digest,inventory_entry_id)`，含 `inventory_entry_id/semantic_slot_key/inference_request_digest/entry_id/sample_slot_index/replacement_slot/terminal_kind/object_locators/acquisition_state_ref`；entry digest必须逐字段等于preregistered inventory row。Success 恰有一个 `raw_output` 且无 `provider_failure`，provider failure 反之；其余 request/provenance/usage-status/latency/pricing/acquisition/model roles 每 role 恰一，未知、重复、缺失 role 均拒绝。
- `CurrentTraceWrapper.v1`：只含 current run/task/unit/attempt、`attempt_ordinal`、binding/inference digest、entry id、按 role 的 locator digests、logical delivery timing、current parse/verifier/checker/canonical/ledger refs；不含 source path、URI、bank-owned `ArtifactRef` 或 source bytes。

Locator 不含 filesystem path、URI、`ArtifactRef` 或 content-hash pair。CLI 的 `--external-bank-root` 只作为 process-local resolver capability；`ResponseBankResolver` 先验证 root marker、root identity、manifest digest，再把 locator 解析为 bank-owned `ArtifactRef`，stream read 并逐块验证 `object_digest`。Root path永不写入 current artifact/event/SQLite。Replay 必须重新显式提供同一 root binding，验证 marker/manifest 后恢复 entry、attempt ordinal、全部 locator roles 与 logical start/finish/source latency；缺 root/object/role 时 blocked，不能在线补洞。Runner object-graph test 必须证明 source object 未被 `_materialize`、`copytree` 或 `ArtifactStore.save_*` 到 consumer tree。

### 3.3 Worker ABI、attempt ordinal 与 delivery records

- `ExecutionRequest` 在 dispatch 前带 core-neutral opaque source-binding digest 和持久化 `attempt_ordinal: int`；不出现 experiment/plugin/bank 类名。
- Ordinal 是 per-unit、zero-based、连续的 attempt 序号：unit 持久化初值 `last_attempt_ordinal=-1`；每个“创建新 attempt”的 initial claim 或 requeue-and-create replacement 原子 transition 恰好写 `last+1`，因此 initial=`0`、replacement=`1,2,...`；对已创建 replacement 的后续 dispatch 不二次递增。Projection/replay恢复相同值，绝不复用 run-global scheduler/order ordinal。`replacement_slot = attempt_ordinal`，不得从 attempt id 字符串、内存 counter 或 condition 推导。
- `current_fencing_token: str`，与现有协议类型一致。
- child 只返回 `PreparedTraceDelivery.v1`，不写 artifact/event/SQLite/consumption。Sequential/thread/process backend 均由 parent 先判 worker killed/normal、lease/fence，再 commit。

```python
@dataclass(frozen=True, kw_only=True)
class TraceDeliveryAttempt:
    attempt_id: str
    binding_digest: str
    status: Literal["started", "killed", "fenced"]
    event_ref: JsonObject

@dataclass(frozen=True, kw_only=True)
class TraceConsumptionCore:
    attempt_id: str
    binding_digest: str
    current_fencing_token: str
    status: Literal["delivered"]
    current_wrapper_ref: JsonObject
    parser_input_ref: JsonObject
    parser_result_ref: JsonObject
    current_provenance_ref: JsonObject
    verifier_checker_refs: tuple[JsonObject, ...]
    canonical_ref: JsonObject | None
    trace_attribution_refs: tuple[JsonObject, ...]

@dataclass(frozen=True, kw_only=True)
class TraceConsumptionRecord:
    core: TraceConsumptionCore
    committed_event_ref: JsonObject
```

`PreparedTraceDelivery.v1` 的 canonical fields 固定为：`schema_version/current_run_id/task_id/unit_id/attempt_id/attempt_ordinal/binding_digest/inference_request_digest/bank_root_id/manifest_digest/entry_id/source_terminal_kind/source_bank_object_locators/logical_start_ms/source_latency_ms/logical_finish_ms/parser_input_media_type/parser_input_digest/child_worker_id/child_completion_sequence/delivery_digest`。`source_bank_object_locators` 按 role canonical sort且满足第3.2节 uniqueness；`logical_finish_ms=start+latency`；delivery digest覆盖其余全部字段。Child 只可读 source stream 并在内存形成该值，zero side effects 由 artifact/event/SQLite before/after snapshot 证明。

Parent `commit_prepared_delivery(delivery, worker_completion, killed_worker_ids, active_lease, current_fencing_token, current_stores)` 输入必须同时验证 delivery digest、worker completion sequence、worker 未 killed、attempt/ordinal/binding、active lease 与 string fence。跨 ArtifactStore/JSONL/SQLite 不声称物理原子；可见性协议固定为：`validate lease/fence/kill → durable staged artifacts → append exactly one TRACE_DELIVERY_COMMITTED.v1 record → projection/SQLite derive`。该event的payload严格等于 canonical `TraceConsumptionCore` fields，只含staged wrapper/parser/provenance/verifier/checker/canonical/trace-attribution refs和consumption core；payload严禁 `committed_event_ref`、`event_seq`、`event_hash`。EventLedger先以该payload按现有外层event schema finalize sequence/hash；projection/replay随后从外层finalized event header派生 `TraceConsumptionRecord.committed_event_ref`。因此event hash preimage不包含event hash自身，payload也不反向引用未完成event。不得用多个可见 event batch 表示一次 delivery commit。Event append前的staged/orphan artifacts对projection不可见，可由startup reconcile/GC按digest清理；append后projection与SQLite只从该commit event幂等派生。Partial/corrupt ledger tail按现有ledger recovery fail closed，绝不猜测commit。Validation失败只追加既有attempt状态语义，不产生commit event/current wrapper/parse/consumption；resume/replay重复读同commit event不得重复ordinal、consumption或artifact visibility。

### 3.4 Deterministic logical source-latency 1x

Profile 固定 `trace_delay_policy=logical_source_latency_1x`。Trace main 不 real sleep，也不能用 noop sleeper 冒充 1x；coordinator 的 dispatch/worker-completion contract 直接消费 frozen priority queue，只有 scheduler 弹出的 completion event 才能进入 parent commit。共享 deterministic discrete-event clock/scheduler 驱动 lease/deadline、fault/death、early stop、retry/requeue、resume 和 event timestamp。每次 delivery 的 logical finish=`logical start + source_latency_ms`，stable tie-break 为 `(logical_time, event_priority, task_id, unit_id, attempt_ordinal)`；checkpoint保存queue/clock/sequence，resume后下一事件及全序一致。W1/Wk、early-stop取消未调度 sibling、deadline、death、retry都必须在同一 queue 上集成测试。Online class 使用 real monotonic/wall clock。

### 3.5 Direct result 与 infra-invalid denominator

```python
@dataclass(frozen=True, kw_only=True)
class PaperDirectRootResult:
    preregistered_root_run_id: str
    experiment_id: str
    condition_id: str
    preregistered_condition_ref: JsonObject
    condition_axes: JsonObject
    case_id: str
    preregistered_case_ref: JsonObject
    repeat_id: int
    evidence_class: str
    root_status: str
    final_result_ref: JsonObject | None
    final_result_reference_complete: bool
    independently_verified_correct: bool
    paper_evidence_complete: bool
    identity_consistent: bool
    end_to_end_verified_success: bool
    task_refs: tuple[JsonObject, ...]
    attempt_refs: tuple[JsonObject, ...]
    event_refs: tuple[JsonObject, ...]
    parser_refs: tuple[JsonObject, ...]
    verifier_checker_refs: tuple[JsonObject, ...]
    canonical_refs: tuple[JsonObject, ...]
    ledger_refs: tuple[JsonObject, ...]
    current_provider_object_refs: tuple[JsonObject, ...]
    source_bank_object_locators: tuple[ExternalBankObjectLocator, ...]
    actual_resource_book_ref: JsonObject | None
    trace_resource_book_ref: JsonObject | None
    ineligibility_reasons: tuple[str, ...]
```

Invariant：

```python
end_to_end_verified_success = (
    final_result_reference_complete
    and independently_verified_correct
    and paper_evidence_complete
    and identity_consistent
)
```

`preregistered_condition_ref` 必须包含 `schema_version/condition_manifest_digest/condition_record_digest/condition_axes_digest`；`condition_axes` 必须完整且键固定为 `domain,difficulty,topic_family,worker_count,sample_slot_index,fault_condition,death_condition,ablation_mode,model_endpoint_id`，不适用值显式为 null。Loader 重算 record/axes digest 并与 condition manifest 对应 row 逐字段比对；`condition_id` 仅为 opaque label，任何通过拆字符串/regex恢复 worker、fault、ablation、model等轴的实现都 fail closed。

`preregistered_case_ref` 必须包含 `schema_version/catalog_digest/case_record_digest/case_axes_digest/factor_position_quantile/position_stratum`；quantile/stratum只能从digest-bound catalog record读取。`case_id`同样只是opaque label；禁止解析case id、依赖catalog/selection顺序或用运行时排序重建position strata。Hard-50 selection重排后每个case仍必须映射同一record digest/quantile/stratum。

`final_result_reference_complete` 只在 final result `ArtifactRef`、content hash/size、terminal root event、canonical ref、merge ref（single-leaf 也必须有）彼此一致时为 true；completion rate 使用它，不再用弱 `final_result_exists`。每个 preregistered root 都有 direct row，包括 `not_started`。Evidence-complete experimental failure保留 false outcome。Infrastructure/identity invalid root 仍保留 planned denominator audit，但其 publish aggregate value 必须 `null`/`blocked`，不能计算一个看似可发布比例。

### 3.6 Numeric cell lineage

```python
@dataclass(frozen=True, kw_only=True)
class PaperMetricObservation:
    observation_id: str
    table_id: str
    row_key: JsonObject
    column_id: str
    metric_id: str
    formula_id: str
    numeric_value: int | float | None
    numerator_membership_ids: tuple[str, ...]
    denominator_inventory_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    exclusion_reasons: JsonObject
    null_reason: str | None
    direct_result_refs: tuple[JsonObject, ...]
    current_task_attempt_event_refs: tuple[JsonObject, ...]
    parser_verifier_checker_canonical_refs: tuple[JsonObject, ...]
    ledger_refs: tuple[JsonObject, ...]
    current_provider_object_refs: tuple[JsonObject, ...]
    source_bank_object_locators: tuple[ExternalBankObjectLocator, ...]
    not_applicable_evidence_roles: tuple[str, ...]
    paper_eligible: bool
```

每个 numeric cell，包括 count、denominator、rate、time、token、cost、range，都必须有 observation。Required-role matrix按 evidence class fail closed：

| Evidence class | `current_provider_object_refs` | `source_bank_object_locators` |
|---|---|---|
| `online_real_provider` | 必须覆盖每个 actual call 的 request body、raw-or-failure、provenance、usage-status、latency、pricing、provider attempt、model record；role唯一 | 必须为空，并在 `not_applicable_evidence_roles` 标 `source_bank_object_locators` |
| `real_model_trace_protocol_run` / capability trace | 必须为空，并标 `current_provider_object_refs` N/A；current lifecycle refs仍在其他字段 | 必须覆盖每个 consumed entry 的 request body、raw-or-failure、provenance、usage-status、latency、pricing、acquisition attempt、model record；role唯一 |
| `regression_only` / synthetic | 按 fixture contract列明实际存在 roles；缺 paper-required role使 paper eligible false | 仅当 fixture通过 external locator 消费时允许，仍不能升级 evidence class |

缺失 required role 与真正 N/A 分开：缺失导致 cell null/blocked，N/A 不伪造空 object。Task20必须分别有 online-only 与 trace-only lineage tests。

### 3.7 Acquisition ledger、filesystem commit 与 output binding

SQLite acquisition authority 的唯一键固定为 `(inventory_digest,inventory_entry_id)`，状态机只允许 `reserved → dispatch_intent → ambiguous|terminal_published → settled`；terminal success/provider-failure均走 `terminal_published`。`BEGIN IMMEDIATE` 内先读取preregistered inventory row，再验证申请的 `semantic_slot_key/inference_request_digest/prompt_admission_profile_digest` 完全一致，随后才reserve；same slot different digest的loser fail closed且不dispatch。`busy_timeout` 配有界指数 lock retry，唯一键冲突者读取 winner 状态而不 dispatch。每个 immutable object/entry/manifest 使用同目录 temp write→flush→file fsync→atomic rename→commit marker；startup先核对 marker/object digest与SQLite状态，再幂等 reconcile。测试逐一覆盖 reserve前后、dispatch-intent前后、send后未terminal、temp write、fsync、rename、commit marker、terminal_published与settle之间的崩溃窗口；两个进程同semantic slot最多一个真实 transport invocation。

Stable authorization receipt不包含run mode或marker digest。`receipt_digest = SHA256(canonical receipt excluding receipt_digest)`，receipt显式绑定`authorized_plan_digest/inventory_digest/prompt_admission_profile_digest/output_root_path_digest`。`new_run|resume` 是CLI invocation mode：同一未篡改receipt可先new-run再resume。New-run要求canonical output root不存在，并用create-new原子建立root；receipt完成验证后，marker由canonical `{receipt_digest,authorized_plan_digest,profile_digest,budget_digest,inventory_digest,prompt_admission_profile_digest,output_root_path_digest}` 唯一派生并写 `paid_output_binding.v1.json`。Resume允许root存在但要求marker逐字段/digest一致；receipt不引用marker，消除自指。冲突、部分目录、缺marker在secret/reserve/dispatch前拒绝。过期receipt只允许对其原matching marker/inventory执行pure reconcile/close existing state，禁止任何new reservation、reacquisition或provider dispatch。

## 4. Metrics-first 总矩阵

| Axis | Frozen metrics / formula | Direct inputs | Evidence producer → consumer → table | Cell-lineage/L gate |
|---|---|---|---|---|
| Global | `completion_rate=count(final_result_reference_complete)/all_preregistered`; E2E success uses four-boolean invariant | digest-bound condition refs/axes、all direct rows、final refs、evidence/identity flags | inventory+runner → direct projector → metric registry | infra-invalid retains denominator but publish cell null/blocked；L1/L4 |
| Exp1 | completion/E2E success、failure stage/kind、actual e2e wallclock/provider latency/tokens/cost estimate、eligibility | online direct rows + actual book | online executor → Exp1 projector → `paper_table_feasibility.csv` | actual call source refs；future selected-scope gate |
| Exp2 trace | logical `trace_replay_wall_clock_ms`、absolute `bank_slot_consumption`、`trace_attributed_tokens/cost`、paired speedup/efficiency/token multiplier/cost multiplier、completion/success、planned/executed/unscheduled/in-flight/peak/utilization | trace rows + current events + source usage/latency | trace scheduler/runner → Exp2 projector → `paper_plot_scalability.csv` | only both-success positive-time pairs；L2/L3 path；post-bank publication gate |
| Exp2 online | actual calls/tokens/cost estimate/wall/provider latency、per-worker 429-or-timeout union fraction、completion/success | current online rows | online checker → canary table | all six levels；union denominator=actual first attempts at that worker；L3 |
| Exp3 trace | controlled interception/escape、completion/success、replacement success、reassignment、discarded trace tokens、absolute overhead、kill error、worker-death result completeness | fault/death/replacement/current+source refs | hooks/runner → Exp3 projector → `paper_plot_robustness.csv` | `wall_overhead_ms=fault-reference`; token/cost also absolute；ratio audit-only另名；L1/L4 |
| Exp3 online | rejection→replacement 与 death→reassignment actual chains、actual calls/usage/cost estimate、wasted actual tokens | Task25 ordered current provider evidence → Task14 online projector → Task17 registry → Task20 observations → Task21 `paper_table_recovery_online.csv` | direct rows + ordered current provider evidence | fault/death event < new attempt < provider dispatch < raw/failure/provenance/usage/model record；L3/L4 |
| Exp4 | four paired transitions、success/completion loss、trace wall/token/cost delta、four mode-specific rates | FULL/ablation pairs including failures | hooks/runner → Exp4 projector → `paper_table_ablation.csv` | exact numerator/denominator/null rules；L1/L4 |
| Exp5 quality | first-attempt nonpass + mutually exclusive reasons、verification rejection、completion/E2E success | all prereg roots + first attempts | Exp5 online → Exp5 projector → quality table | provider failures included；endpoint/serving confounding caption |
| Exp5 resources | planned units、actual first calls/coverage、actual tokens/cost estimate、model×repeat enclosing e2e wallclock | model×repeat first dispatch and all roots terminal | Exp5 runner → Exp5 projector → resources table | wallclock is enclosing elapsed, never sum root clocks；3 raw + median/min/max/range |
| Eligibility | `online_real_provider` vs `real_model_trace_protocol_run`; regression/synthetic never paper eligible | receipt/manifest/source class/current calls | eligibility validator → report/formal gate | approved real full acquisition + complete manifest are necessary for trace paper eligibility |

### 4.1 Exp3/Exp4/Exp5 exact metric clarifications

- Exp3 absolute overheads:
  - `trace_replay_wall_clock_overhead_ms = fault_ms - reference_ms`
  - `trace_attributed_token_overhead = fault_tokens - reference_tokens`
  - `trace_attributed_cost_overhead = fault_cost - reference_cost`
  - Any ratio is audit-only and must be named `*_ratio_audit`, never substitute the paper overhead.
- `kill_progress_error_pp = 100 * (actual_ratio - target_ratio)` per death；condition reports signed mean and signed maximum numeric value. Observation error string only affects eligibility.
- `result_completeness_rate = recovered_valid_canonical_required_slots / preregistered_required_slots` only for worker death；rate-fault cell is null with `not_applicable_rate_fault`.
- Exp4 transitions are exactly `full_success_ablation_success`、`full_success_ablation_failure`、`full_failure_ablation_success`、`full_failure_ablation_failure`。Success/completion loss=`FULL - ablation`; resource delta=`ablation - FULL`。
- Exp4 mode denominators：NO_VERIFICATION wrong-canonical rate denominator=independently labeled invalid candidates；NO_PARSER_POLICY raw-only acceptance denominator=raw-only exposures；NO_REQUEUE stuck rate denominator=all preregistered roots in that mode；NO_MERGE_GATE premature failure rate denominator=premature merge attempts。Zero denominator always null + explicit reason。
- Exp5 first-nonpass reason priority：provider/transport failure → parse/schema unusable → verification/checker rejection。Exactly two model-summary tables；domain/topic/repeat/failure taxonomy只作 appendix/audit。
- Captions must state Exp3 `comparison_kind=paired_trace_reference` and Exp5 model comparisons are endpoint/serving-confounded, not pure model effects。
- Exp2 absolute resource cells：`bank_slot_consumption=count(committed TraceConsumptionRecord)`；`trace_attributed_tokens=sum(source usage total_tokens for each committed consumption)`；`trace_attributed_cost=sum(source cost_estimate attribution for each committed consumption)`。同一 source entry 被不同 condition 消费可产生多次 trace attribution，但 acquisition actual spend只在 bank ledger计一次。Paired multipliers分别为 `wk_trace_attributed_tokens/w1_trace_attributed_tokens` 与 `wk_trace_attributed_cost/w1_trace_attributed_cost`；仅同 case/repeat/sample且两边 evidence complete、分母>0时非 null。
- Exp3 online metric projector属于 Task14，不属于 Task25。`actual_provider_calls`、prompt/completion/total usage、`cost_estimate`、`wasted_actual_tokens`都从 current provider objects计算；每条恢复链要求 ordered refs `fault_or_death_event → newly_created_attempt(attempt_ordinal+1) → provider_dispatch → raw_or_failure → provenance → usage → model_record`，缺一项对应 cell null/blocked。

## 5. Frozen profile、预算与 paid authorization

### 5.1 Offline-approved profile values

Tracked `benchmarks/paper/epd027_pipeline_profile.v1.json` freezes：

- Exp2 online cases：early=`factor_v2_hard_034`、middle=`factor_v2_hard_122`、late=`factor_v2_hard_063`、no_factor=`factor_v2_hard_161`；workers=`1,3,7,10,30,50`；repeat 0；24 roots；480 first calls upper。
- Exp2 online完整 canonical condition sequence 固定为：`w1:{034,122,063,161} → w3:{034,122,063,161} → w7:{034,122,063,161} → w10:{034,122,063,161} → w30:{034,122,063,161} → w50:{034,122,063,161}`，花括号内顺序严格代表 early、middle、late、no_factor；每项 condition record 都含完整 axes/digest。`max_concurrent_roots=1`，确保 worker level只控制单 root 内最多20个AI units，不让四个 roots彼此并发混入 wall/provider 干扰。
- Exp3 online：`factor_v2_easy_109` false-positive 100%、worker 10、repeat 0、max retries 2、2 first units/6 calls upper；`factor_v2_easy_148` one worker death at 50%、worker 10、repeat 0、max retries 2、2 first units/6 calls upper。Total 2 roots/12 calls upper。
- L3 capability bank smoke：Factor `factor_v2_easy_109` 使用 deterministic one-range capability split、initial forced verification rejection + replacement；Lean `lean_v2_simple_pure_logic_direct_prop_01` 使用 one proof node、initial controlled checker rejection + replacement；2 roots、4 actual calls exact。Classification=`new_real_capability_smoke`、paper eligible=false。
- `trace_delay_policy=logical_source_latency_1x`。
- Exp2 per-worker `429_or_timeout_union_fraction <= 0.2`；union 按 attempt identity 去重，分母是该 worker 的 actual first provider attempts。
- Exp2 online/trace same-case intersection至少3对和 severe-divergence threshold只在完整 bank/main trace 的 publication gate计算，不是 pre-acquisition L3 条件。Frozen severe rule仍为 speedup ratio outside `[0.5,2.0]` 或一方 `>=1.1` 另一方 `<=0.9`；任何 worker severe 都阻止该 scalability claim。

### 5.2 L3 small-paid hard budget

Capability 4 + online checks 492=`496` provider calls upper；ambiguous reserve=`20`；总 hard calls=`516`。每 call reservation=`32768` prompt + `300000` completion=`332768` tokens，CNY=`1.898304`。因此：

- calls hard limit=`516`
- tokens hard limit=`171708288`
- CNY reservation hard limit=`979.524864`
- DeepSeek cumulative absolute hard stop仍为 CNY `1000.0`
- max in-flight acquisition/capability=`10`，Exp2 online最多=`50`
- `--unlimited-budget` 对任何 provider-writing mode无效

Budget authority 是 SQLite WAL；JSONL 只是审计导出。Actual `cost_estimate` 使用 provider usage × frozen pricing；usage missing 时保留 full reservation upper，不写零。

### 5.3 Paid receipt schema 与 scope

```python
@dataclass(frozen=True, kw_only=True)
class PaidExecutionReceipt:
    schema_version: Literal["tokenshare.paid_execution_receipt.v1"]
    receipt_digest: str
    scope: Literal[
        "epd027_l3_capability_and_online_checks",
        "epd027_full_bank_acquisition",
        "exp1_full_online",
        "exp5_capability_smoke",
        "exp5_full_online"
    ]
    authorized_plan_digest: str
    profile_digest: str
    budget_digest: str
    inventory_digest: str
    prompt_admission_profile_digest: str
    selected_experiments: tuple[str, ...]
    output_root_path_digest: str
    not_before: str
    expires_at: str
    user_approval_reference: str
```

`receipt_digest` 严格等于 receipt 去掉 `receipt_digest` 字段后的 canonical JSON SHA-256；任何实现都不得把digest字段自身或derived output marker放进preimage。Offline implementation approval不能生成或替代该 receipt。Provider CLI requires user-supplied receipt + `--allow-provider-calls` + invocation `--output-mode new-run|resume`; receipt scope、time、authorized plan、profile/budget/inventory/admission/selection/output path 必须全部匹配。Full bank、Exp1、Exp5 capability、Exp5 full online 各需独立 receipt；L3 receipt不得授权任何 formal run，Exp5 capability receipt不得授权 full online。Expired receipt仅可pure reconcile/close，不能new dispatch。

## 6. 四级验收：严格 entrance/exit

### 6.1 L1 — Component tests

**Entrance:** 对应 component Task 的两轮独立 review 已通过。

**Required path:** request bytes/identity、bank locator/index、SQLite budget、acquisition reconcile、logical scheduler、worker ABI、trace executor、direct schema、projectors、metric observations、renderer/CLI/gates/launchers 的定向 component/integration tests，全程 network tripwire。Tasks25–29 的全部 offline nodeids必须进入 L1；Task24 的 pure recomputation/component nodeids进入 L1，只有针对真实 artifact root 的 audit nodeids留在 L4。允许一个小型 synthetic/historical facility-level L3/L4 gate fixture证明控制流，但它不得设置正式 `l3_new_real_smoke_verified` 或 `l4_cell_traceability_verified`。

**Exit:** 所有 `paper-l1-components` profile nodeids pass；provider calls=0；没有 Critical/Important。Blocked/failing不是 pass。

### 6.2 L2 — 保存的真实历史 artifact 驱动当前完整路径

**Positive source:** `E:\TokenEcnomic\TokenShareData\outputs\experiments\heiyucode_gpt56_smoke_20260716`。该 source没有 `summary.passed` 字段，不得伪造。真实成功谓词固定为唯一 `per_number_results.jsonl` row 同时满足 `status="passed" && final_correctness=true && oracle_match=true`，并与 `batch_report.json` 的 `attempted_count=1/correct_count=1/failed_count=0/accuracy=1.0` 一致。绑定：case-row file digest=`sha256:e5a64df6b3a006a89a9f100823cf35473574ba90a86b4832f05422899386ec27`、batch report digest=`sha256:391556f8d3d59a6fa26d90dff30ee2fe7f40891c16705657d215226e56138ad3`、raw hash=`sha256:da5c0cff478969af32a84637593d031db3512ae65e8a7e43086155c7b4ce5b69`、provenance hash=`sha256:e7fd14cc4602c496be1a93921a8ff13d29310f9505b606bd452aaf704cb3c0e2`。Full source tree binding=`sha256_tree_v1`：对24个文件按 ordinal relative POSIX path排序，逐行 hash UTF-8 `path\0size\0sha256\n`，digest=`sha256:b3e61b280c635006a894719e5b6d816efdceab49c24f0c41d5d3db9711e46404`。

**Entrance:** L1 pass；tracked 最小脱敏包 `tests/fixtures/paper/historical_real_factorization_v1/` 已由 frozen hashes 导入并复核。

**Required path:** 历史 raw direct-factor payload 与当前 range/merge schema不兼容，因此冻结 dedicated `historical_real_factorization_single_leaf.v1` regression-only adapter。它通过 external locator读取原始 payload bytes（不改写、不包装成伪 provider response），把目标 `4733749` 建成一个 deterministic single-leaf root/unit，经当前正常 `ProtocolRunCoordinator → parser(direct factors) → verifier(product+primality+oracle) → canonical → explicit single-leaf merge → ledger → direct result → metric observation → regression table`。Adapter只能识别该 fixture manifest/digests且不能用于 paper catalogs；这只证明基础设施路径，不证明 formal range semantics、response-bank paper eligibility 或正式实验结论。Zero provider calls。

**Classification:** `regression_only`、`paper_eligible=false`，永远不能升级为 `real_model_trace_protocol_run` paper evidence。

**Negative source:** 2026-07-31 Exp3/4 smoke只作 expected-fail read-only replay，源 hash 前后相同；不能替代 positive source。

**Exit:** `paper-l2-historical-real` profile全 pass；positive predicate、case-row/batch/tree/raw/provenance digests全匹配；single-leaf path生成 terminal direct/metric/regression table；provider calls=0；source hashes一致。Blocked/failing不是 pass。

### 6.3 L3 — 新真实 4-call capability bank + 492-call online checks

**Entrance:** L1/L2 pass；用户提供有效 `epd027_l3_capability_and_online_checks` paid receipt；`new_run` absent-root/create-new marker或`resume` exact-marker binding、secret、quota、SQLite预算 preflight通过。

**Required path:** 4 real calls封存两 root initial/replacement entries；随后用这些新真实 entries 通过 logical 1x trace → normal state machine → direct → metric observations → tables。再运行 Exp2 24 roots/480 upper和Exp3 2 roots/12 upper online checks。

**Exit:** capability bank 4 terminal entries、两条 replacement trace链、Exp2全部六档/四 cases、Exp3两恢复链、per-worker 429/timeout union fraction<=0.2、evidence coverage=1.0、calls<=516、tokens<=171708288、CNY reserved+settled<=979.524864 且 absolute CNY<=1000。Provider receipt/source/model/usage/latency lineage完整。Blocked/budget-exhausted/failing不是 pass。

若 paid receipt 不存在，自动实施记录 `l3_new_real_smoke_blocked` 并继续 Task 33/34 的 blocked audit/handoff；不能创建 receipt、不能声称 L3 pass。

### 6.4 L4 — 每个 numeric cell 可追溯且 deterministic

**Entrance:** L3 pass；renderer output与metric contract digest冻结。

**Required path:** 对L3 capability/online所有 numeric cells逐一验证第3.6节 membership、完整 denominator、excluded/null reasons、direct/current refs以及 evidence-class-specific provider/source roles；Exp2 online和Exp3 online必须具备 current provider objects且source locator N/A，capability trace必须相反。Exp3 online recovery table走 Task14→17→20→21完整 projector/registry/observation/renderer 链；从 immutable inputs独立重算两次。

**Exit:** numeric cell coverage=1.0；每个 non-null value按formula重算相等；每个 null有reason和完整denominator；两次 `observations_digest`、`tables_digest`、`cell_lineage_digest`完全相同；provider calls=0。Blocked/failing不是 pass。

若 L3 blocked，L4 必须 `l4_cell_traceability_blocked`，可以继续验证离线 fixture lineage，但不得把它记为 L4 pass。

## 7. Preserve/refactor/create/delete map

### 7.1 Preserve

- `src/tokenshare/core/` 的协议边界；只允许 core-neutral attempt ordinal/fence type一致性变更。
- `src/tokenshare/experiments/paper_formal_checkpoint.py`、`paper_terminal_outcomes.py`、runner rolling disk forecast 的通用 checkpoint/streaming。
- `src/tokenshare/executors/ai_api_replay.py` 历史 replay；不把它伪装成 trace executor。
- `src/tokenshare/experiments/paper_model_identity.py` 的 endpoint/model join基础。

### 7.2 Refactor

- `src/tokenshare/executors/ai_api.py`、`src/tokenshare/executors/ai_api_transport.py`、`src/tokenshare/experiments/ai_profile.py`、`src/tokenshare/experiments/factorization_500_ai.py`、Factor/Lean paper adapters和run routers：唯一 prepared factory、prompt admission、exact endpoint/body wire、paid scope。
- `src/tokenshare/executors/contracts.py`、`src/tokenshare/core/models.py`、`src/tokenshare/core/leases.py`、`src/tokenshare/protocol_engine.py`、`src/tokenshare/storage/events.py`、`src/tokenshare/storage/sqlite_index.py`：opaque binding、per-unit attempt ordinal、event/schema migration、string fence，不含 paper/domain名。
- `src/tokenshare/local_runtime/contracts.py`、`src/tokenshare/local_runtime/coordinator.py`、`src/tokenshare/local_runtime/workers.py`、`src/tokenshare/local_runtime/process_worker_child.py`、`src/tokenshare/local_runtime/projection.py`：frozen completion queue、parent commit ABI、logical clock、ordinal replay。
- Factor/Lean runtime/paper adapters：stable planned unit、executor factory、Lean prompt v2。
- `paper_models.py`、formal evidence/runner/callbacks、budget、metrics/report/Exp5 artifacts、CLI与launchers。

### 7.3 Create

- Profiles/contracts：`benchmarks/paper/epd027_pipeline_profile.v1.json`、`paper_metric_contract.v1.json`
- Executors：`ai_api_request_identity.py`、`response_bank.py`、`trace_backed.py`
- Runtime：`src/tokenshare/local_runtime/logical_scheduler.py`
- Experiments：`paper_pipeline_profile.py`、`paper_direct_results.py`、`paper_response_bank.py`、`paper_budget_ledger.py`、`paper_resource_accounting.py`、`paper_exp1_metrics.py` … `paper_exp5_metrics.py`、`paper_metric_registry.py`、`paper_metric_observations.py`、`paper_online_checks.py`、`paper_traceability.py`、`run_paper_pipeline.py`
- Fixture：`tests/fixtures/paper/historical_real_factorization_v1/`
- Verification：`verification/pytest_network_tripwire.py` 和四个 profile manifests。
- Local future launchers：bank acquisition、trace matrix、L3 checks、Exp1 online、Exp5 capability、Exp5 full online。

### 7.4 Delete/current-path disable

- shared-Exp1 current writers/readers/helpers/tests；仅明确 historical decoder 可残留旧字段。
- Exp2 throughput/throughput_roots_per_second/efficiency aliases和main rate-limit sensitivity outputs。
- Exp3 generic detection/false-accept/recovery-rate/recovery-latency；trace main中的wasted actual tokens。
- Exp4 generic/duplicate aliases。
- Exp5 pairwise/significance/ranking/retry/recovery/accepted-validity正文输出和重复summary table。
- 三个旧 launcher 的 dispatch能力；文件保留fail-closed提示。

## 8. 严格串行 DAG

```text
0 network tripwire
 -> 1 profile/offline-vs-paid authority
 -> 2 metric contract
 -> 3 direct schema
 -> 4 prepared outbound + Lean v2
 -> 5 bank object/index/locator
 -> 6 slot inventory
 -> 7 SQLite budget authority
 -> 8 acquisition/publish/reconcile
 -> 9 logical scheduler
 -> 10 worker ABI + attempt ordinal
 -> 11 trace executor
 -> 12 Exp1 projector
 -> 13 Exp2 projector
 -> 14 Exp3 projector
 -> 15 Exp4 projector
 -> 16 Exp5 projector
 -> 17 metric registry/formal_metrics integration
 -> 18 evidence classes/eligibility
 -> 19 formal runner/preflight
 -> 20 metric observations/cell lineage
 -> 21 renderer/report
 -> 22 legacy resource migration
 -> 23 historical real fixture/L2 path
 -> 24 replay/determinism
 -> 25 online check plans + compliant direct/current evidence
 -> 26 paid receipt validator
 -> 27 CLI commands
 -> 28 execution/publication gates
 -> 29 launcher supersession
 -> 30 verification profiles
 -> 31 L1
 -> 32 L2
 -> 33 L3
 -> 34 L4 + docs/handoff
```

没有 parallel boundary。每条箭头都是硬依赖。Task 0–3 沿用已经落盘的历史验收证据；从 Task 4 起，每个 Task 只设一个同时承担规格、质量、全链与影子 TokenShare 检查的综合独立 reviewer gate；未 PASS 或未 accepted 时必须留在当前 Task 修正和复核，通过后才进入下一 Task。

## 9. Task execution template

每个 Task 必须包含：exact files、named RED tests、明确列出的完整 RED command及预期失败、最小实现、同一完整命令 GREEN、一个由同一综合独立 reviewer 同时完成规格/质量/全链/影子 TokenShare 检查的 review、suggested commit。所有离线 pytest 命令统一前缀：

```powershell
conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire
```

Tripwire Task 0 自己用 isolated bootstrap test证明网络拒绝；Task 0之后任何离线命令漏掉plugin都不算证据。

### 9.1 Task 4–34 审查、范围与耗时硬门（2026-08-02 user override）

本节自 Task 4 起覆盖本计划其他通用执行模板；Task 0–3 保持已经 accepted 的状态，不因本节重新打开。总监督者给实现或审查 Agent 的自包含 prompt 必须要求先读取本节，并把本节的范围、阻塞条件和 deferred 规则作为当前 Task 的验收前置。

1. **计划范围是硬边界。** 当前 Task 的 `Files`、目标和验收条件构成唯一写入与审查范围；不得把发现的通用架构改进隐含扩展为当前 Task 的 prerequisite。
2. **只有五类问题可以阻塞当前 Task：**
   - 会直接导致论文指标、固定分母或结论边界错误；
   - 实验设施复制协议状态机、验证、重试、合并或终态，形成影子 TokenShare；
   - 正式论文结果不是从真实 TokenShare 系统持久化的 event/artifact 投影得到；
   - replay 会重新调用 provider，或无法复用已经持久化的结果；
   - 当前 Task 计划列出的定向测试失败。
3. **默认登记为 `deferred_followup`、不得阻塞当前 Task：** deep immutability；closed schema 的全面加固；非正常输入的排列组合；duplicate/ordering/strict-type 的穷举；不影响当前正式路径的通用 typed API 重构；security、攻击者模型或 runner 输入防火墙。
4. **计划外 production 文件门禁。** 如果审查要求修改当前 Task `Files` 未列出的 production 文件，必须先判断正常正式路径是否确实无法工作：仍可正确工作时不得扩展范围，只记录 `deferred_followup`；确实阻塞时，必须先向用户报告计划外文件、阻塞原因和预计时间，不得静默实施。
5. **Agent 与复审预算。** 每个 Task 只允许一个实现 Agent、一个综合独立审查 Agent；该 reviewer 必须在同一次综合 review 中同时审规格、质量、全链与影子 TokenShare，并且只有当前 reviewer 可以做 follow-up，不得引入第二 reviewer。“最多一轮”只限制发起一次综合 review 和一次 follow-up 的调度节奏；follow-up 未 PASS 时保持打开，由当前 developer 与 reviewer 继续在同一 Task 内修正和复核，直至 Critical/Important 清零且该 Task PASS/accepted。“最多一轮”不得成为停止修复、降低 PASS 标准或越过当前 Task 的理由。Minor 不得延迟验收。
6. **时间门禁。** 普通 Task 目标耗时为 60–90 分钟。达到 90 分钟仍未 accepted 时，必须立即报告已完成内容、当前阻塞、是否发生范围扩张、计划外修改文件和继续完成的预计时间；未经用户确认不得继续扩大范围。
7. **验证去重。** 总监督者不得重新运行子 Agent 已返回精确命令、退出码和测试计数的同一测试。测试超时时优先拆分或从最小受影响范围重跑，不默认重跑整个大套件。
8. **验收后测试最小化限时 15 分钟。** 只合并明显重复的参数矩阵和等价断言；不再为压缩安排第二轮完整独立审查；只运行被修改的最小测试文件。超过 15 分钟时登记为里程碑测试整理任务，不得阻塞下一 Task。
9. **推理强度。** 实现 Agent 默认使用 `high`；只有论文指标边界、正式全链路或出现真实 Critical 时才升级 `ultra`。
10. **Task 3 不重新审查。** Task 3 已 accepted；本规则从 Task 4 直接生效。
11. **总监督持续推进。** 阶段性返回、状态报告或耗时报告只用于同步进度，不等于总监督停止；除非用户明确要求停止，或 Task 34 已完成收口，否则总监督者必须继续推动当前 Task 至 accepted，再按硬依赖推进下一 Task。

## 10. Implementation tasks

### Task 0: Bootstrap lowest-layer network tripwire

**Files:** Create `verification/pytest_network_tripwire.py`; create `tests/test_paper_network_tripwire.py`.

- [ ] RED tests：`test_tripwire_blocks_socket_create_connection`、`test_tripwire_blocks_urllib_urlopen`、`test_tripwire_reports_zero_provider_calls_when_unused`。用 isolated pytest subprocess 加载尚不存在的 plugin。
- [ ] RED command：

  ```powershell
  conda run -n tokenshare python -m pytest tests/test_paper_network_tripwire.py -q
  ```

  Expected：plugin import/behavior tests fail；不产生真实连接。
- [ ] Minimal implementation：pytest plugin 在 test start 前 patch `socket.create_connection`、`socket.socket.connect`、`urllib.request.urlopen` 与三个 production `UrlLib*Transport.post_chat_completion`，统一抛 `network tripwire: outbound provider access forbidden`；fake/capturing transport不受影响；session finish断言 production provider call count=0。
- [ ] GREEN：同命令全部 pass。再运行一条 isolated probe，预期 outbound call 被拒且无 server response。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：确认覆盖最低层和现有三个 transport；确认无环境变量/secret读取、不会误把 fake transport计为provider。
- [ ] Suggested commit：`git add verification/pytest_network_tripwire.py tests/test_paper_network_tripwire.py; git commit -m "test(verification): block network in paper offline tests"`

### Task 1: Freeze profile and separate offline approval from paid authority

**Dependencies:** Task 0.

**Files:** Create `benchmarks/paper/epd027_pipeline_profile.v1.json`、`src/tokenshare/experiments/paper_pipeline_profile.py`、`tests/experiments/test_paper_pipeline_profile.py`; modify `src/tokenshare/experiments/paper_budget.py`、`tests/experiments/test_paper_budget.py`.

- [ ] RED tests：`test_profile_freezes_four_plus_492_and_516_hard_calls`、`test_profile_freezes_logical_source_latency_1x`、`test_profile_freezes_exact_24_condition_sequence_and_max_concurrent_roots_one`、`test_profile_freezes_exact_exp3_cases`、`test_prompt_admission_profile_id_version_algorithm_and_digest_are_exact`、`test_admission_algorithm_drift_changes_profile_and_budget_digests`、`test_offline_plan_approval_cannot_authorize_provider_write`、`test_profile_digest_changes_for_any_paid_or_metric_control`。
- [ ] RED command：

  ```powershell
  conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_pipeline_profile.py tests/experiments/test_paper_budget.py -q
  ```

  Expected：module/profile absent；partial implementation must fail `offline plan approval is not a paid execution receipt`。
- [ ] Minimal implementation：写入第3.1/5节全部 exact admission/budget values和24项 canonical Exp2 condition records；loader复算admission canonical JSON digest、`4+480+12=496`、`496+20=516`、`516*332768=171708288`、`516*1.898304=979.524864`；budget digest显式绑定admission digest；验证 `max_concurrent_roots=1`、catalog case membership/position、Lean readiness case、DeepSeek v3 config。Offline approval只记录 plan/profile digest和`offline_implementation`，无 provider scope/expiry/output binding。
- [ ] GREEN：同命令 pass，provider calls=0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：逐值复算 profile；证明任何 provider-writing function拒绝 offline approval object。
- [ ] Suggested commit：`git add benchmarks/paper/epd027_pipeline_profile.v1.json src/tokenshare/experiments/paper_pipeline_profile.py src/tokenshare/experiments/paper_budget.py tests/experiments/test_paper_pipeline_profile.py tests/experiments/test_paper_budget.py; git commit -m "feat(experiments): freeze reviewed EPD-027 profile"`

### Task 2: Create machine-readable metric contract

**Dependencies:** Task 1.

**Files:** Create `benchmarks/paper/paper_metric_contract.v1.json`、`src/tokenshare/experiments/paper_metric_contract.py`、`tests/experiments/test_paper_metric_contract.py`.

- [ ] RED tests：`test_contract_covers_every_numeric_cell_in_metrics_matrix`、`test_exp2_absolute_bank_slots_trace_tokens_cost_and_paired_multipliers`、`test_exp3_trace_and_online_recovery_metrics_are_both_registered`、`test_exp3_overheads_are_absolute_differences`、`test_exp4_formulas_and_zero_denominators_are_exact`、`test_exp5_wallclock_is_model_repeat_enclosing_elapsed`、`test_contract_rejects_retired_aliases_and_renderer_only_metrics`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_contract.py -q`。Expected：file/module absent；incomplete row error names exact metric id。
- [ ] Minimal implementation：每项固定 `metric_id/formula_id/evidence_classes/required_current_provider_roles/required_source_bank_roles/row_scope/numerator/denominator/null/applicability/pair_key/producer/consumer/table/deprecated_aliases`；冻结 global infra-invalid publish-block、Exp2 absolute+paired resource metrics/union、Exp3 trace+online recovery table、Exp3 absolute overhead、Exp4 four modes、Exp5 two tables/enclosing elapsed/captions。
- [ ] GREEN：上述完整 RED command pass；provider calls=0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：逐项对照第4节；任一 numeric output field没有contract时loader fail closed。
- [ ] Suggested commit：`git add benchmarks/paper/paper_metric_contract.v1.json src/tokenshare/experiments/paper_metric_contract.py tests/experiments/test_paper_metric_contract.py; git commit -m "feat(metrics): add reviewed paper metric contract"`

### Task 3: Implement direct-result schema and fixed denominator

**Dependencies:** Task 2.

**Files:** Create `src/tokenshare/experiments/paper_direct_results.py`、`tests/experiments/test_paper_direct_results.py`; modify `src/tokenshare/experiments/paper_models.py`、`tests/experiments/test_paper_models.py`.

- [ ] RED tests：`test_every_preregistered_root_has_direct_row_including_not_started`、`test_condition_ref_and_axes_are_digest_bound_to_manifest`、`test_condition_id_is_opaque_and_never_parsed_for_axes`、`test_case_ref_binds_catalog_record_quantile_and_position_stratum`、`test_case_id_and_catalog_order_are_never_used_to_reconstruct_stratum`、`test_completion_requires_complete_final_result_reference`、`test_e2e_success_requires_all_four_boolean_gates`、`test_direct_row_splits_current_provider_refs_from_source_locators`、`test_infra_invalid_keeps_denominator_but_blocks_publish_aggregate`、`test_online_and_trace_resource_books_are_mutually_exclusive`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_direct_results.py tests/experiments/test_paper_models.py -q`。Expected：new schema absent；existing completion/accepted-validity coupling fails four-gate invariant。
- [ ] Minimal implementation：实现第3.5节dataclass/projector；manifest/catalog rows分别提供digest-bound condition/case refs、完整axes/quantile/stratum，禁用condition-id/case-id/order parser；observed missing生成`not_started`；只有完整 final artifact+terminal+canonical+merge refs才计completion；evidence-complete wrong final是completion true/success false；current provider/source locator分栏；infra invalid aggregate `{value:null,status:blocked,denominator_inventory_ids:[...]}`；历史schemas只读。
- [ ] GREEN：fixture correct/wrong/infra/not-started 4 roots reports audit denominator 4、publish blocked；provider calls=0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：检查所有refs/booleans；从inventory到aggregate手算并确认无root消失。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_direct_results.py src/tokenshare/experiments/paper_models.py tests/experiments/test_paper_direct_results.py tests/experiments/test_paper_models.py; git commit -m "refactor(experiments): add evidence-gated direct results"`

### Task 4: Prepare exact outbound bytes once and migrate Lean prompt v2

**Dependencies:** Task 3.

**Files:** Create `src/tokenshare/executors/ai_api_request_identity.py`、`tests/executors/test_ai_api_request_identity.py`; modify `src/tokenshare/executors/ai_api.py`、`src/tokenshare/executors/ai_api_transport.py`、`src/tokenshare/experiments/ai_profile.py`、`src/tokenshare/experiments/factorization_500_ai.py`、`src/tokenshare/experiments/lean_ai_benchmark.py`、`src/tokenshare/experiments/factorization_paper_adapter.py`、`src/tokenshare/experiments/lean_paper_adapter.py`、`src/tokenshare/experiments/run_ai_profile.py`、`src/tokenshare/experiments/run_factorization_500_ai.py`、`src/tokenshare/experiments/run_paper_experiments.py`、`src/tokenshare/plugins/lean_proof/prompt_builder.py`、`src/tokenshare/plugins/lean_proof/runtime_adapter.py`; modify `tests/phase7_fixtures.py`、`tests/test_phase7_ai_api_execution_flow.py`、`tests/executors/test_ai_api_executor_success.py`、`tests/executors/test_ai_api_executor_failover.py`、`tests/executors/test_ai_api_artifacts.py`、`tests/executors/test_ai_api_transport.py`、`tests/executors/test_ai_api_deepseek_transport.py`、`tests/executors/test_ai_api_openai_transport.py`、`tests/experiments/test_ai_profile_suite.py`、`tests/experiments/test_factorization_500_ai.py`、`tests/experiments/test_lean_ai_benchmark.py`、`tests/experiments/test_factorization_paper_adapter.py`、`tests/experiments/test_lean_paper_adapter.py`、`tests/experiments/test_run_paper_experiments_cli.py`、`tests/experiments/test_paper_gate_c_dispatcher.py`、`tests/experiments/test_paper_gate_c_structured_output.py`、`tests/integration/test_paper_protocol_runtime_integration.py`、`tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py`.

- [ ] RED tests：`test_factory_is_only_prepared_request_constructor_across_static_dispatch_abi_allowlist`、`test_allowlist_covers_all_production_router_wrapper_fixture_and_test_double_hits`、`test_unmigrated_historical_entrypoint_retires_before_secret_or_dispatch`、`test_all_legacy_kwargs_body_assertions_use_exact_prepared_bytes_and_endpoint_abi`、`test_failover_gate_c_and_protocol_integration_paths_use_prepared_abi`、`test_artifact_digest_and_wire_share_same_prepared_bytes_object`、`test_body_obj_bytes_digest_recompute_mismatch_fails_closed`、`test_wire_url_exactly_equals_normalized_absolute_endpoint`、`test_deepseek_and_openai_transports_accept_only_prepared_bytes_endpoint_abi`、`test_transport_does_not_serialize_prepared_body_again`、`test_prompt_admission_profile_fields_and_digest_are_bound`、`test_unicode_system_messages_and_canonical_overhead_use_frozen_upper_bound`、`test_prompt_admission_over_32768_or_algorithm_drift_blocks_before_reserve_secret_dispatch`、`test_ai_profile_factorization500_lean_benchmark_paper_router_and_scripted_wrappers_use_factory`、`test_volatile_request_attempt_lease_fence_time_worker_condition_do_not_change_digest`、`test_lean_v2_candidate_id_is_stable_across_attempts_and_v1_hash_is_unchanged`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/executors/test_ai_api_request_identity.py tests/executors/test_ai_api_executor_success.py tests/executors/test_ai_api_executor_failover.py tests/executors/test_ai_api_artifacts.py tests/executors/test_ai_api_transport.py tests/executors/test_ai_api_deepseek_transport.py tests/executors/test_ai_api_openai_transport.py tests/test_phase7_ai_api_execution_flow.py tests/experiments/test_ai_profile_suite.py tests/experiments/test_factorization_500_ai.py tests/experiments/test_lean_ai_benchmark.py tests/experiments/test_factorization_paper_adapter.py tests/experiments/test_lean_paper_adapter.py tests/experiments/test_run_paper_experiments_cli.py tests/experiments/test_paper_gate_c_dispatcher.py tests/experiments/test_paper_gate_c_structured_output.py tests/integration/test_paper_protocol_runtime_integration.py tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py -q`。Expected：static allowlist至少命中一个旧body-dict ABI或旧`kwargs['body']`断言；failover/Gate C/integration transport bytes/endpoint或admission digest不一致；超长/Unicode drift prompt到达reserve；Lean candidate id随request id变化。
- [ ] Minimal implementation：维护全仓dispatch ABI static allowlist，逐个迁移production/router/wrapper、`lean_ai_benchmark.py`、`tests/phase7_fixtures.py`及实际命中test doubles到唯一 factory；明确保留的historical entrypoint必须在secret/dispatch前fail-closed退役。Body builder returns `PreparedOutboundRequest`；canonical JSON UTF-8 serialization occurs once且三元一致性反复重算校验；冻结admission profile/Unicode算法在reserve/secret/dispatch前；artifact write before secret；DeepSeek/OpenAI/base transport只收 exact bytes + content type + normalized endpoint。Lean v2 stable；v1 raw immutable。
- [ ] GREEN：上述完整 RED command pass；所有旧`kwargs['body']`测试替换为exact `PreparedOutboundRequest.body_bytes + normalized_absolute_endpoint` ABI断言；spy order=`prepared_validated,prompt_admitted,prepared_persisted,budget_reserved,dispatch_intent,secret_resolved,transport_exact_endpoint_bytes`；provider network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：byte/digest/endpoint/version field audit；search transport for secondary JSON serialization and verify old raw hashes。
- [ ] Suggested commit：`git add src/tokenshare/executors/ai_api_request_identity.py src/tokenshare/executors/ai_api.py src/tokenshare/executors/ai_api_transport.py src/tokenshare/experiments/ai_profile.py src/tokenshare/experiments/factorization_500_ai.py src/tokenshare/experiments/lean_ai_benchmark.py src/tokenshare/experiments/factorization_paper_adapter.py src/tokenshare/experiments/lean_paper_adapter.py src/tokenshare/experiments/run_ai_profile.py src/tokenshare/experiments/run_factorization_500_ai.py src/tokenshare/experiments/run_paper_experiments.py src/tokenshare/plugins/lean_proof/prompt_builder.py src/tokenshare/plugins/lean_proof/runtime_adapter.py tests/phase7_fixtures.py tests/test_phase7_ai_api_execution_flow.py tests/executors/test_ai_api_request_identity.py tests/executors/test_ai_api_executor_success.py tests/executors/test_ai_api_executor_failover.py tests/executors/test_ai_api_artifacts.py tests/executors/test_ai_api_transport.py tests/executors/test_ai_api_deepseek_transport.py tests/executors/test_ai_api_openai_transport.py tests/experiments/test_ai_profile_suite.py tests/experiments/test_factorization_500_ai.py tests/experiments/test_lean_ai_benchmark.py tests/experiments/test_factorization_paper_adapter.py tests/experiments/test_lean_paper_adapter.py tests/experiments/test_run_paper_experiments_cli.py tests/experiments/test_paper_gate_c_dispatcher.py tests/experiments/test_paper_gate_c_structured_output.py tests/integration/test_paper_protocol_runtime_integration.py tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py; git commit -m "feat(executors): migrate all dispatch paths to prepared ABI"`

### Task 5: Implement immutable bank objects, index, and opaque external locator

**Dependencies:** Task 4.

**Files:** Create `src/tokenshare/executors/response_bank.py`、`tests/executors/test_response_bank.py`; modify `src/tokenshare/storage/artifacts.py`、`src/tokenshare/executors/ai_api_replay.py`、`tests/executors/test_ai_api_replay_guard.py`.

- [ ] RED tests：`test_manifest_inventory_entry_and_current_wrapper_v1_exact_fields`、`test_inventory_entry_id_recomputes_canonical_row_excluding_only_itself`、`test_inventory_entry_id_is_stable_across_load_and_replay`、`test_inventory_row_tamper_changes_recomputed_id_and_fails_closed`、`test_external_locator_has_no_path_uri_or_artifact_ref`、`test_external_root_is_process_local_and_marker_manifest_bound`、`test_locator_stream_reads_and_hashes_bank_internal_object`、`test_entry_roles_are_unique_complete_and_success_failure_exclusive`、`test_current_store_rejects_materializing_source_object`、`test_terminal_provider_failure_is_complete_without_raw`、`test_replay_requires_same_explicit_root_binding_and_restores_entry_ordinal_roles_timing`、`test_conflicting_terminal_entry_is_rejected`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/executors/test_response_bank.py -q`。Expected：module absent；any path/ArtifactRef locator field fails schema assertion。
- [ ] Minimal implementation：实现第3.2节 inventory-row self-excluding id serializer/recalculator、manifest/entry/current-wrapper/locator、bank-internal resolver/root marker、`ValidatedResponseBankIndex`、success/failure role invariants与replay binding；任何row tamper在index前fail closed；stream hash后才向consumer提供bytes；consumer wrapper只存locator digest/current refs/timing。
- [ ] GREEN：上述完整 RED command pass；consumer tree无source digest payload file；provider calls=0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：object schema和failure semantics；monkeypatch `_materialize/copytree/save_*` 证明runner不复制source。
- [ ] Suggested commit：`git add src/tokenshare/executors/response_bank.py src/tokenshare/storage/artifacts.py src/tokenshare/executors/ai_api_replay.py tests/executors/test_response_bank.py tests/executors/test_ai_api_replay_guard.py; git commit -m "feat(executors): add opaque external response-bank locator"`

### Task 6: Plan complete semantic slot inventory and zero-engine preflight

**Dependencies:** Task 5.

**Files:** Create `src/tokenshare/experiments/paper_response_bank.py`、`tests/experiments/test_paper_response_bank.py`; modify `src/tokenshare/experiments/paper_budget.py`、`tests/experiments/test_paper_budget.py`.

- [ ] RED tests：`test_every_inventory_row_has_unique_inventory_entry_id_and_semantic_slot_key`、`test_planner_computes_inventory_entry_id_from_canonical_row_excluding_id`、`test_planner_is_stable_for_identical_rows_and_rejects_tampered_precomputed_id`、`test_semantic_slot_key_binds_all_frozen_slot_axes_and_admission_digest`、`test_exp2_conditions_share_case_repeat_unit_slot_and_repeats_do_not`、`test_exp2_full_24_condition_sequence_and_max_concurrent_roots_one`、`test_exp3_rate_slots_zero_to_two_and_death_slots_zero_to_four`、`test_exp4_full_and_ablations_share_zero_to_one`、`test_same_semantic_slot_cannot_preregister_two_inference_digests`、`test_early_stop_does_not_prune_inventory`、`test_missing_slot_writes_preflight_blocked_record_and_zero_protocol_engine_events`、`test_terminal_provider_failure_counts_present`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_response_bank.py tests/experiments/test_paper_budget.py -q`。Expected：planner absent；missing fixture must show no task/lease/request/provider event after implementation。
- [ ] Minimal implementation：plan adapter-built exact bodies for all semantic sample/replacement slots；为每row写canonical `inventory_entry_id/semantic_slot_key/inference_request_digest/prompt_admission_profile_digest`，拒绝one-slot-two-digests；write roots/units/slots/model/concurrency/calls/tokens/CNY/disk estimates and all 24 Exp2 condition refs/axes in frozen order；validate `max_concurrent_roots=1`；complete validation happens before coordinator construction。Blocked preflight record uses separate preflight ledger/schema, not ProtocolEngine event ledger。
- [ ] GREEN：上述完整 RED command pass；plan-only provider calls=0；missing=1 produces one preflight blocked record and protocol event count=0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：recompute pairing/retry depths；prove condition/worker absent from digest and early stop does not alter expected inventory。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_response_bank.py src/tokenshare/experiments/paper_budget.py tests/experiments/test_paper_response_bank.py tests/experiments/test_paper_budget.py; git commit -m "feat(experiments): plan complete response-bank inventory"`

### Task 7: Make SQLite WAL the atomic budget authority

**Dependencies:** Task 6.

**Files:** Create `src/tokenshare/experiments/paper_budget_ledger.py`、`src/tokenshare/experiments/paper_resource_accounting.py`、`tests/experiments/test_paper_budget_ledger.py`; modify `src/tokenshare/experiments/paper_budget.py`.

- [ ] RED tests：`test_unique_key_is_inventory_digest_and_inventory_entry_id`、`test_transaction_recomputes_inventory_entry_id_excluding_self_before_reserve`、`test_transaction_rejects_tampered_or_unstable_inventory_row_id`、`test_transaction_rejects_request_digest_not_equal_preregistered_row`、`test_begin_immediate_atomically_checks_and_reserves_calls_tokens_cny`、`test_busy_timeout_and_bounded_lock_retry_fail_closed`、`test_two_processes_racing_same_semantic_slot_different_digest_only_registered_winner_reserves`、`test_state_machine_allows_only_reserved_dispatch_intent_ambiguous_or_terminal_published_settled`、`test_crash_reopen_preserves_inflight_reservation`、`test_terminal_entry_without_settle_reconciles_exactly_once`、`test_missing_usage_settles_full_upper`、`test_jsonl_is_export_not_authority`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_budget_ledger.py -q`。Expected：module absent；race fixture otherwise admits two reservations。
- [ ] Minimal implementation：实现第3.7节SQLite schema/state machine；`journal_mode=WAL`、`busy_timeout`、有界lock retry；`BEGIN IMMEDIATE`读取preregistered row并验证slot/request/admission digests，reads settled+reserved, checks calls/tokens/CNY, inserts `(inventory_digest,inventory_entry_id)` unique reservation and commits；terminal publish/ref keyed reconciliation idempotently moves reservation to settled；JSONL generated from committed rows only。No resend/no duplicate charge。
- [ ] Boundary tests use L3 `516/171708288/979.524864` and absolute CNY1000；provider cost field named `cost_estimate` with frozen pricing basis。
- [ ] GREEN：上述完整 RED command pass，包括two real local Python processes racing one final reservation；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：transaction/state arithmetic；kill process at reserve/publish/settle boundaries and reopen/reconcile。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_budget_ledger.py src/tokenshare/experiments/paper_resource_accounting.py src/tokenshare/experiments/paper_budget.py tests/experiments/test_paper_budget_ledger.py; git commit -m "feat(experiments): make SQLite the provider budget authority"`

### Task 8: Acquire, publish, resume, and reconcile bank entries

**Dependencies:** Task 7.

**Files:** Modify `src/tokenshare/executors/ai_api.py`、`src/tokenshare/experiments/paper_response_bank.py`、`src/tokenshare/experiments/paper_budget_ledger.py`、`src/tokenshare/storage/artifacts.py`; create `tests/experiments/test_paper_response_bank_acquisition.py`; modify `tests/executors/test_ai_api_executor_success.py`、`tests/executors/test_ai_api_executor_failover.py`、`tests/executors/test_ai_api_artifacts.py`、`tests/storage/test_artifact_store.py`.

- [ ] RED tests：`test_acquisition_orders_prepare_admit_persist_reserve_dispatch_terminal_publish_settle`、`test_success_and_each_provider_failure_taxonomy_are_terminal_entries`、`test_two_processes_same_semantic_slot_different_request_digest_only_registered_winner_invokes_transport`、`test_loser_digest_mismatch_fails_closed_before_secret_and_transport`、`test_resume_dispatches_only_missing_never_success_or_failure_terminal`、`test_crash_after_reserve_before_dispatch_intent_reconciles`、`test_crash_after_dispatch_intent_before_or_after_send_becomes_ambiguous`、`test_crash_during_temp_write_or_after_fsync_before_rename_ignores_partial_object`、`test_crash_after_rename_before_commit_marker_reconciles_object`、`test_crash_after_marker_before_terminal_published_reconciles_entry`、`test_crash_after_terminal_published_before_settle_reconciles_once`、`test_temp_flush_fsync_rename_and_commit_marker_are_durable_order`、`test_ambiguous_dispatch_needs_paid_scope_and_one_reserved_reacquisition`、`test_expired_receipt_can_reconcile_existing_terminal_but_never_reserve_or_dispatch`、`test_budget_exhaustion_closes_inflight_and_leaves_exact_missing_inventory`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_response_bank_acquisition.py tests/executors/test_ai_api_executor_success.py tests/executors/test_ai_api_executor_failover.py tests/executors/test_ai_api_artifacts.py tests/storage/test_artifact_store.py -q`。Expected：no acquisition orchestrator；publish-before-settle crash either resends or double charges until fixed。
- [ ] Minimal implementation：durable order=`paid receipt/invocation-mode/output marker validate → factory prepare+consistency → prompt admission → prepared artifact temp/fsync/rename/marker → BEGIN IMMEDIATE inventory-row equality+reserve → dispatch_intent → secret resolve → exact endpoint/body send once → response/failure/provenance/usage/latency/model objects durable commit → immutable terminal entry publish+marker → terminal_published → settle`。Startup按第3.7节reconcile；dispatch intent后未terminal=`ambiguous`; never automatic resend。One reacquisition requires unexpired remaining receipt scope/reserve and links prior ambiguous attempt；expired receipt only reconciles/closes already-published state。Same semantic-slot loser never invokes transport。
- [ ] Provider failure raw可空，但request/failure/provenance/usage-status/latency/pricing/acquisition/model refs完整。Secret只从env进入transport，artifact/log/SQLite/JSONL无secret。
- [ ] GREEN：上述完整 RED command pass；fake transport calls exactly missing+explicit reacquisition；network tripwire proves production transport unused。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：state machine/order/failure taxonomy；resume/reconcile/secret/object graph audit。
- [ ] Suggested commit：`git add src/tokenshare/executors/ai_api.py src/tokenshare/experiments/paper_response_bank.py src/tokenshare/experiments/paper_budget_ledger.py src/tokenshare/storage/artifacts.py tests/experiments/test_paper_response_bank_acquisition.py tests/executors/test_ai_api_executor_success.py tests/executors/test_ai_api_executor_failover.py tests/executors/test_ai_api_artifacts.py tests/storage/test_artifact_store.py; git commit -m "feat(experiments): acquire and reconcile immutable bank entries"`

### Task 9: Add deterministic logical source-latency scheduler

**Dependencies:** Task 8.

**Files:** Create `src/tokenshare/local_runtime/logical_scheduler.py`、`tests/local_runtime/test_logical_scheduler.py`、`tests/local_runtime/test_coordinator_logical_schedule.py`; modify `src/tokenshare/experiments/paper_runtime_clock.py`、`src/tokenshare/local_runtime/contracts.py`、`src/tokenshare/local_runtime/coordinator.py`、`src/tokenshare/local_runtime/workers.py`、`src/tokenshare/local_runtime/process_worker_child.py`、`tests/local_runtime/test_coordinator_full_lifecycle.py`、`tests/local_runtime/test_submission_and_recovery.py`、`tests/local_runtime/test_worker_death_recovery.py`.

- [ ] RED tests：`test_logical_one_x_delivery_finishes_at_start_plus_source_latency`、`test_coordinator_commits_only_queue_popped_completion`、`test_w1_and_wk_makespan_match_discrete_event_schedule`、`test_early_stop_retry_deadline_death_and_resume_share_frozen_queue`、`test_stable_tie_break_replays_identical_event_order`、`test_checkpoint_restores_next_completion_sequence`、`test_trace_policy_rejects_real_sleep_and_noop_sleeper`、`test_online_policy_uses_real_clock_not_logical`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/local_runtime/test_logical_scheduler.py tests/local_runtime/test_coordinator_logical_schedule.py tests/local_runtime/test_coordinator_full_lifecycle.py tests/local_runtime/test_submission_and_recovery.py tests/local_runtime/test_worker_death_recovery.py -q`。Expected：module absent；coordinator仍按child return顺序而非frozen queue commit；noop sleeper incorrectly reports zero elapsed until rejected。
- [ ] Minimal implementation：priority queue event scheduler with frozen tie-break；worker completion contract返回scheduled event而不直接commit；coordinator只弹queue并推进clock/commit，lease/deadline/fault/death/early-stop/retry/resume timestamps都来自queue；logical makespan not CPU duration。Trace requires policy exact `logical_source_latency_1x`; online path explicitly chooses real clock。
- [ ] GREEN：known latencies `[100,300,200]` yield w1=600ms and w2=300ms under frozen dispatch order；early stop/retry/late/fence/death/checkpoint resume矩阵与replay全序pass；no real sleep/network。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：1x semantics/tie-break；ensure all trace timestamps share scheduler and no wallclock leak。
- [ ] Suggested commit：`git add src/tokenshare/local_runtime/logical_scheduler.py src/tokenshare/experiments/paper_runtime_clock.py src/tokenshare/local_runtime/contracts.py src/tokenshare/local_runtime/coordinator.py src/tokenshare/local_runtime/workers.py src/tokenshare/local_runtime/process_worker_child.py tests/local_runtime/test_logical_scheduler.py tests/local_runtime/test_coordinator_logical_schedule.py tests/local_runtime/test_coordinator_full_lifecycle.py tests/local_runtime/test_submission_and_recovery.py tests/local_runtime/test_worker_death_recovery.py; git commit -m "feat(runtime): add deterministic trace event scheduler"`

### Task 10: Persist attempt ordinal and unify parent-side worker commit ABI

**Dependencies:** Task 9.

**Files:** Modify `src/tokenshare/executors/contracts.py`、`src/tokenshare/core/models.py`、`src/tokenshare/core/leases.py`、`src/tokenshare/protocol_engine.py`、`src/tokenshare/storage/events.py`、`src/tokenshare/storage/sqlite_index.py`、`src/tokenshare/local_runtime/contracts.py`、`src/tokenshare/local_runtime/coordinator.py`、`src/tokenshare/local_runtime/workers.py`、`src/tokenshare/local_runtime/process_worker_child.py`、`src/tokenshare/local_runtime/projection.py`; create `tests/local_runtime/test_trace_delivery_parent_commit.py`、`tests/storage/test_attempt_ordinal_migration.py`; modify `tests/core/test_phase1_models.py`、`tests/core/test_lease_manager.py`、`tests/test_phase3_execution_flow.py`、`tests/storage/test_event_ledger.py`、`tests/storage/test_sqlite_index.py`、`tests/local_runtime/test_submission_and_recovery.py`、`tests/local_runtime/test_worker_death_recovery.py`、`tests/local_runtime/test_runtime_boundaries.py`.

- [ ] RED tests：`test_per_unit_attempt_ordinal_is_zero_based_contiguous_and_not_global_schedule_ordinal`、`test_claim_or_requeue_new_attempt_atomically_persists_next_ordinal`、`test_event_sqlite_migration_replays_legacy_and_v2_rows`、`test_replay_restores_same_ordinal_and_replacement_slot`、`test_binding_is_opaque_and_present_before_dispatch`、`test_prepared_trace_delivery_v1_exact_fields_and_digest`、`test_child_delivery_has_zero_artifact_event_sqlite_side_effects`、`test_parent_stages_artifacts_then_appends_exactly_one_trace_delivery_committed_event`、`test_commit_event_payload_is_exact_trace_consumption_core_without_event_ref_seq_or_hash`、`test_event_hash_preimage_has_no_self_reference`、`test_projection_derives_committed_event_ref_from_finalized_outer_header`、`test_projection_and_sqlite_derive_visibility_only_from_commit_event`、`test_crash_at_each_stage_artifact_event_projection_boundary_never_exposes_false_consumption`、`test_orphan_staged_artifacts_are_invisible_and_reconcilable`、`test_partial_or_corrupt_event_tail_fails_closed_under_existing_ledger_recovery`、`test_commit_event_replay_is_idempotent_without_ordinal_or_consumption_duplication`、`test_parent_commits_only_after_worker_status_sequence_lease_and_string_fence_validation`、`test_sequential_thread_process_share_parent_commit_path`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/core/test_phase1_models.py tests/core/test_lease_manager.py tests/test_phase3_execution_flow.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/storage/test_attempt_ordinal_migration.py tests/local_runtime/test_submission_and_recovery.py tests/local_runtime/test_worker_death_recovery.py tests/local_runtime/test_runtime_boundaries.py tests/local_runtime/test_trace_delivery_parent_commit.py -q`。Expected：core/event/SQLite缺ordinal schema；child提前写side effects；parent缺completion sequence/fence gate。
- [ ] Minimal implementation：按第3.3节修改core model/lease/protocol event/SQLite schema与migration；per-unit initial0、每个new-attempt transition +1、replay稳定且不复用global ordinal；opaque binding pre-dispatch；exact `PreparedTraceDelivery.v1` serializer/digest；parent验证worker sequence/killed/lease/fence后durably stage artifacts，再以exact `TraceConsumptionCore` payload append one `TRACE_DELIVERY_COMMITTED.v1` visibility record；payload不含event ref/seq/hash，projection从finalized outer header派生committed ref并与SQLite幂等同步。No self-hash、no multi-visible-event batch；pre-event orphan invisible；partial tail fail closed。
- [ ] Replacement resolver uses persisted ordinal only。Killed/fenced create `TraceDeliveryAttempt` status records, never consumption。
- [ ] GREEN：sequential/thread/process matrix pass；process death after child prepare yields killed record, zero consumption；replay ordinal stable。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：ABI/types/event atomicity；inspect process boundary serialization and all backend call graphs。
- [ ] Suggested commit：`git add src/tokenshare/executors/contracts.py src/tokenshare/core/models.py src/tokenshare/core/leases.py src/tokenshare/protocol_engine.py src/tokenshare/storage/events.py src/tokenshare/storage/sqlite_index.py src/tokenshare/local_runtime/contracts.py src/tokenshare/local_runtime/coordinator.py src/tokenshare/local_runtime/workers.py src/tokenshare/local_runtime/process_worker_child.py src/tokenshare/local_runtime/projection.py tests/core/test_phase1_models.py tests/core/test_lease_manager.py tests/test_phase3_execution_flow.py tests/storage/test_event_ledger.py tests/storage/test_sqlite_index.py tests/storage/test_attempt_ordinal_migration.py tests/local_runtime/test_submission_and_recovery.py tests/local_runtime/test_worker_death_recovery.py tests/local_runtime/test_runtime_boundaries.py tests/local_runtime/test_trace_delivery_parent_commit.py; git commit -m "refactor(runtime): commit prepared deliveries in parent"`

### Task 11: Implement trace-backed executor and dual provenance

**Dependencies:** Task 10.

**Files:** Create `src/tokenshare/executors/trace_backed.py`、`tests/executors/test_trace_backed.py`; modify `src/tokenshare/experiments/factorization_paper_adapter.py`、`src/tokenshare/experiments/lean_paper_adapter.py`、`tests/experiments/test_factorization_paper_adapter.py`、`tests/experiments/test_lean_paper_adapter.py`.

- [ ] RED tests：`test_trace_executor_calls_provider_zero_and_streams_external_source`、`test_trace_success_and_provider_failure_stage_current_wrapper_with_complete_role_locators`、`test_delivery_attempt_started_killed_fenced_never_looks_delivered`、`test_only_single_trace_delivery_committed_event_makes_staged_refs_and_consumption_visible`、`test_trace_executor_commit_payload_never_contains_committed_event_ref_event_seq_or_event_hash`、`test_trace_consumption_record_gets_committed_ref_only_after_event_finalize`、`test_crash_before_each_staged_artifact_and_commit_event_boundary_replays_without_false_consumption`、`test_crash_after_commit_before_projection_replays_idempotently`、`test_retry_uses_persisted_attempt_ordinal_as_replacement_slot`、`test_resume_restores_entry_ordinal_roles_and_delivery_timing`、`test_regression_synthetic_source_cannot_be_paper_eligible`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/executors/test_trace_backed.py tests/experiments/test_factorization_paper_adapter.py tests/experiments/test_lean_paper_adapter.py -q`。Expected：module absent；source/current identity or worker-death consumption assertions fail in partial path。
- [ ] Minimal implementation：adapter freezes opaque binding before dispatch；executor resolves/stream-verifies external source, schedules logical latency, parses into `PreparedTraceDelivery`; parent stages wrapper/parser/provenance/usage/canonical refs，serializes exact `TraceConsumptionCore` as the sole commit payload，then projection uses finalized outer header to construct `TraceConsumptionRecord`。No event-header field in payload、no hash self-reference、no source raw copy、no source attempt identity reuse、no multi-event visibility batch、current provider calls=0。
- [ ] Evidence classification carried as source metadata but eligibility deferred to Task18；regression/synthetic hard false regardless completeness。
- [ ] GREEN：上述完整 RED command pass；object graph contains locator digest/current wrappers only；worker death uses next ordinal/slot。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：source/current/clock/ABI chain；Factor and one Lean checker trace from binding to parent commit。
- [ ] Suggested commit：`git add src/tokenshare/executors/trace_backed.py src/tokenshare/experiments/factorization_paper_adapter.py src/tokenshare/experiments/lean_paper_adapter.py tests/executors/test_trace_backed.py tests/experiments/test_factorization_paper_adapter.py tests/experiments/test_lean_paper_adapter.py; git commit -m "feat(executors): deliver external traces through parent commit"`

### Task 12: Implement standalone Exp1 projector only

**Dependencies:** Task 11.

**Files:** Create only `src/tokenshare/experiments/paper_exp1_metrics.py`、`tests/experiments/test_paper_exp1_metrics.py`.

- [ ] RED tests：`test_exp1_completion_requires_complete_final_reference_for_all_preregistered_roots`、`test_wrong_final_is_completion_not_success`、`test_infra_invalid_returns_blocked_null_publish_observation`、`test_actual_wall_latency_tokens_cost_estimate_missingness_is_explicit`、`test_exp1_has_no_accepted_validity_alias`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_exp1_metrics.py -q`。Expected：module absent。
- [ ] Minimal implementation：pure `build_exp1_observations(direct_rows, contract)`，不import formal runner/renderer，不修改registry。每个numeric value返回observation draft membership/denominator/null metadata。
- [ ] GREEN：correct/wrong/infra/not-started fixture hand-calculates expected cells；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：Exp1 contract；pure module/import boundary/missingness。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp1_metrics.py tests/experiments/test_paper_exp1_metrics.py; git commit -m "feat(metrics): add standalone Exp1 projector"`

### Task 13: Implement standalone Exp2 projector only

**Dependencies:** Task 12.

**Files:** Create only `src/tokenshare/experiments/paper_exp2_metrics.py`、`tests/experiments/test_paper_exp2_metrics.py`.

- [ ] RED tests：`test_speedup_requires_same_case_repeat_success_positive_times`、`test_failed_fast_root_remains_and_speedup_is_null`、`test_hard50_strata_come_only_from_digest_bound_case_refs`、`test_hard50_selection_reordering_does_not_change_quantile_or_position_stratum`、`test_case_id_shape_cannot_influence_stratum`、`test_logical_makespan_drives_trace_wallclock`、`test_absolute_bank_slot_consumption_trace_attributed_tokens_and_cost`、`test_paired_trace_token_and_cost_multipliers_use_w1_denominators`、`test_trace_parallel_efficiency`、`test_planned_executed_unscheduled_inflight_peak_utilization`、`test_online_429_timeout_union_uses_unique_first_attempts_per_worker`、`test_online_trace_intersection_and_severe_rule_are_post_bank_only`、`test_no_throughput_or_efficiency_alias`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_exp2_metrics.py -q`。Expected：module absent。
- [ ] Minimal implementation：pure main/online projectors。Main pair key=`case-record-digest×repeat×sample×w1/wk`；position strata/quantile只从validated `preregistered_case_ref`读取，不解析case id或依赖hard-50顺序；two repeat raw/min/max/relative difference；absolute slot/token/cost由committed consumptions逐项归因，paired multipliers只在w1分母正且两边完整时计算；online union=`count(unique first attempts with 429 or timeout)/actual first attempts at worker`。Intersection>=3/severe outputs are `not_evaluated_pre_bank` until complete main trace provided。
- [ ] GREEN：hand fixtures verify ratios、union de-dup、post-bank gate；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：Exp2 formulas/timing；failure retention and no external/provider assumptions。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp2_metrics.py tests/experiments/test_paper_exp2_metrics.py; git commit -m "feat(metrics): add standalone Exp2 projector"`

### Task 14: Implement standalone Exp3 projector only

**Dependencies:** Task 13.

**Files:** Create only `src/tokenshare/experiments/paper_exp3_metrics.py`、`tests/experiments/test_paper_exp3_metrics.py`.

- [ ] RED tests：`test_controlled_wrong_candidate_denominator_is_exact_boundary`、`test_reassignment_requires_started_linked_new_attempt`、`test_replacement_success_uses_started_replacements`、`test_discarded_tokens_require_source_usage_and_canonical_exclusion`、`test_absolute_overheads_are_fault_minus_paired_trace_reference`、`test_ratio_is_separately_named_audit_only`、`test_signed_kill_error_per_death_mean_max`、`test_result_completeness_worker_death_only`、`test_trace_has_no_wasted_actual_tokens`、`test_online_projector_requires_ordered_fault_or_death_new_attempt_dispatch_provider_chain`、`test_online_actual_calls_usage_cost_and_wasted_tokens_use_current_provider_objects`、`test_online_missing_provider_role_blocks_recovery_cell`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_exp3_metrics.py -q`。Expected：module absent。
- [ ] Minimal implementation：同一模块提供 pure trace projector 与 pure online recovery projector；trace使用same sample-slot reference、absolute ms/token/cost differences、ratio only `*_ratio_audit`、rate-fault completeness null；online严格验证有序 `fault/death → new ordinal attempt → dispatch → raw/failure → provenance → usage → model` 链并产生actual calls/prompt-completion-total usage/cost estimate/wasted actual tokens drafts；missing role使相关cell null/ineligible。
- [ ] GREEN：manual trace false-positive/death与online rejection/death fixtures pass；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：Exp3 denominator/reference/caption inputs；identity joins and null propagation。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp3_metrics.py tests/experiments/test_paper_exp3_metrics.py; git commit -m "feat(metrics): add standalone Exp3 projector"`

### Task 15: Implement standalone Exp4 projector only

**Dependencies:** Task 14.

**Files:** Create only `src/tokenshare/experiments/paper_exp4_metrics.py`、`tests/experiments/test_paper_exp4_metrics.py`.

- [ ] RED tests：`test_each_ablation_pairs_one_full_by_case_repeat_sample`、`test_four_transition_cells_keep_all_failures`、`test_success_completion_loss_and_resource_delta_signs`、`test_no_verification_denominator_and_zero_case`、`test_no_parser_policy_denominator_and_zero_case`、`test_no_requeue_denominator_and_zero_case`、`test_no_merge_gate_denominator_and_zero_case`、`test_retired_generic_aliases_absent`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_exp4_metrics.py -q`。Expected：module absent。
- [ ] Minimal implementation：pure pair/projector；四 transition counts；all prereg pairs retained；exact mode formulas from4.1；zero denominators null+reason；identity/resource incomplete pair retained but publish null。
- [ ] GREEN：four transition fixtures and each zero denominator pass；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：all modes/formulas；pair inventory and no hook inference from mode name alone。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp4_metrics.py tests/experiments/test_paper_exp4_metrics.py; git commit -m "feat(metrics): add standalone Exp4 projector"`

### Task 16: Implement standalone Exp5 projector only

**Dependencies:** Task 15.

**Files:** Create only `src/tokenshare/experiments/paper_exp5_metrics.py`、`tests/experiments/test_paper_exp5_metrics.py`.

- [ ] RED tests：`test_nonpass_denominator_includes_every_actual_first_attempt`、`test_reason_priority_is_mutually_exclusive`、`test_verification_rejection_denominator_is_checkable_only`、`test_all_preregistered_roots_drive_completion_success`、`test_model_repeat_wallclock_is_first_dispatch_to_all_roots_terminal`、`test_wallclock_never_sums_root_clocks`、`test_three_raw_repeats_and_median_min_max_range`、`test_exact_two_summary_table_payloads`、`test_endpoint_serving_confounding_caption_required`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_exp5_metrics.py -q`。Expected：module absent。
- [ ] Minimal implementation：pure quality/resources projector；provider failure first nonpass；max_retries=0；model×repeat enclosing elapsed=`max(root_terminal)-first_protocol_dispatch`；aggregate median+min/max/range with 3 raw values；exact two table payloads and confounding caption metadata。
- [ ] GREEN：overlapping root clocks prove enclosing elapsed differs from sum；all tests pass/network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：Exp5 denominators/wallclock/caption；no pairwise/significance/retry/recovery/accepted-validity outputs。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp5_metrics.py tests/experiments/test_paper_exp5_metrics.py; git commit -m "feat(metrics): add standalone Exp5 projector"`

### Task 17: Integrate projector registry and formal metrics serially

**Dependencies:** Tasks 12–16.

**Files:** Create `src/tokenshare/experiments/paper_metric_registry.py`、`tests/experiments/test_paper_metric_registry.py`; modify `src/tokenshare/experiments/paper_formal_metrics.py`、`tests/experiments/test_paper_formal_metrics.py`.

- [ ] RED tests：`test_registry_has_exactly_one_projector_per_experiment_table`、`test_registry_maps_exp3_trace_and_online_recovery_tables_to_task14_projectors`、`test_formal_metrics_delegates_without_rederiving_formula`、`test_registry_rejects_uncontracted_output_field`、`test_global_infra_invalid_blocks_publish_cells`、`test_retired_aliases_and_old_exp2_exp5_outputs_absent`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_registry.py tests/experiments/test_paper_formal_metrics.py -q`。Expected：registry absent；central module still derives old formulas/headers。
- [ ] Minimal implementation：registry maps contract table ids to Tasks12–16 pure functions，且Exp3 trace/online recovery两张表都指向Task14；central module only loads direct rows, invokes registry, validates contract field set and publishes intermediate metric drafts；remove old formula branches/sensitivity/pairwise aliases。
- [ ] GREEN：上述完整 RED command pass；all five experiment modules and both Exp3 projectors invoked through one registry；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：mapping/contract completeness；central file contains no duplicate metric arithmetic。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_metric_registry.py src/tokenshare/experiments/paper_formal_metrics.py tests/experiments/test_paper_metric_registry.py tests/experiments/test_paper_formal_metrics.py; git commit -m "refactor(metrics): register standalone paper projectors"`

### Task 18: Version evidence classes and enforce acquisition eligibility

**Dependencies:** Task 17.

**Files:** Modify `src/tokenshare/experiments/paper_models.py`、`src/tokenshare/experiments/paper_formal_evidence.py`、`tests/experiments/test_paper_models.py`、`tests/experiments/test_paper_formal_evidence.py`.

- [ ] RED tests：`test_online_class_requires_current_real_provider_attempt_per_executed_unit`、`test_trace_class_requires_current_calls_zero_and_dual_provenance`、`test_only_paid_full_acquisition_receipt_plus_complete_manifest_can_be_trace_paper_eligible`、`test_capability_historical_and_synthetic_sources_are_always_ineligible`、`test_provider_failure_entry_can_be_complete_source_evidence`、`test_historical_schema_cannot_upgrade_classification`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_models.py tests/experiments/test_paper_formal_evidence.py -q`。Expected：current classification only understands real transport/capturing and misclassifies trace/regression。
- [ ] Minimal implementation：new schema versions include source classification, paid receipt scope/digest, manifest completeness, current/source refs/calls, direct evidence/identity flags。Necessary trace eligibility=`source_class=approved_real_full_acquisition AND paid scope full_bank AND complete expected inventory AND current calls=0 AND full current lifecycle`；capability still paper false。
- [ ] GREEN：上述完整 RED command pass；regression fixture completes pipeline but paper false；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：classification truth table；old decoders read-only and no type-confusion between receipt classes。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_models.py src/tokenshare/experiments/paper_formal_evidence.py tests/experiments/test_paper_models.py tests/experiments/test_paper_formal_evidence.py; git commit -m "feat(experiments): enforce trace acquisition eligibility"`

### Task 19: Integrate formal runner with preflight, scheduler, and normal lifecycle

**Dependencies:** Task 18.

**Files:** Modify `src/tokenshare/experiments/paper_formal_runner.py`、`src/tokenshare/experiments/paper_formal_callbacks.py`、`tests/experiments/test_paper_formal_runner.py`、`tests/experiments/test_paper_formal_callbacks.py`.

- [ ] RED tests：`test_missing_bank_preflight_records_block_without_engine_task_lease_request_provider_events`、`test_trace_run_uses_normal_coordinator_fault_verifier_checker_merge_settlement`、`test_runner_uses_logical_scheduler_for_trace_and_real_clock_for_online`、`test_runner_object_graph_never_materializes_source`、`test_worker_death_parent_commit_and_ordinal_replacement_flow`、`test_trace_current_provider_calls_are_zero`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_callbacks.py -q`。Expected：runner lacks new path or emits engine events before bank completeness check；shared baseline path may still run。
- [ ] Minimal implementation：preflight constructs validated metadata index/locator before coordinator；missing writes only `paper_preflight_blocked.v1` outside protocol ledger then returns。Complete trace runs existing coordinator with injected clock/executor/hooks；no replay shortcut/no provider/no source materialization。
- [ ] GREEN：上述完整 RED command pass；one Factor + one Lean fixture traverses full lifecycle；missing case protocol events exactly 0。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：preflight/event boundary and full lifecycle；manual current/source object graph and worker death trace。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_formal_runner.py src/tokenshare/experiments/paper_formal_callbacks.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_callbacks.py; git commit -m "feat(experiments): run traces through normal formal lifecycle"`

### Task 20: Materialize one lineage observation for every numeric cell

**Dependencies:** Task 19.

**Files:** Create `src/tokenshare/experiments/paper_metric_observations.py`、`tests/experiments/test_paper_metric_observations.py`; modify `src/tokenshare/experiments/paper_formal_metrics.py`、`tests/experiments/test_paper_formal_metrics.py`.

- [ ] RED tests：`test_every_numeric_draft_becomes_one_observation`、`test_observation_has_formula_value_numerator_and_complete_denominator`、`test_observation_has_excluded_and_null_reasons`、`test_online_observation_requires_current_provider_roles_and_marks_source_locator_na`、`test_trace_observation_requires_source_locator_roles_and_marks_current_provider_na`、`test_exp3_online_recovery_drafts_become_observations`、`test_non_null_value_recomputes_from_membership`、`test_missing_required_role_blocks_cell_and_table`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_observations.py tests/experiments/test_paper_formal_metrics.py -q`。Expected：module absent；current metric rows have row-level refs only。
- [ ] Minimal implementation：implement 第3.6 dataclass/required-role matrix、canonical observation id/digest；builder joins direct refs/current task-attempt-events/parser-verifier-checker-canonical-ledger，并按evidence class分别填current provider refs或source bank locators与N/A roles。Exp3 online drafts必须完整进入。Count/denominator/range也各自有observation。Missing required lineage makes cell null and table blocked。
- [ ] GREEN：上述完整 RED command pass；fixture numeric cell coverage=1.0；network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：field/role completeness；randomly select cells and recompute solely from observation membership。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_metric_observations.py src/tokenshare/experiments/paper_formal_metrics.py tests/experiments/test_paper_metric_observations.py tests/experiments/test_paper_formal_metrics.py; git commit -m "feat(metrics): attach lineage to every numeric cell"`

### Task 21: Render/report only contract observations and audit claims

**Dependencies:** Task 20.

**Files:** Modify `src/tokenshare/experiments/paper_exp5_artifacts.py`、`src/tokenshare/experiments/paper_formal_report.py`、`tests/experiments/test_paper_exp5_artifacts.py`、`tests/experiments/test_paper_formal_report.py`; create `tests/experiments/test_paper_metric_renderer_contract.py`.

- [ ] RED tests：`test_renderer_accepts_only_observations_allowed_by_contract`、`test_renderer_preserves_null_denominator_and_reason`、`test_exact_current_table_inventory_includes_exp3_online_recovery_and_two_exp5_summaries`、`test_exp3_online_recovery_table_comes_from_task14_registry_observations`、`test_exp3_caption_says_paired_trace_reference_not_actual_online`、`test_exp5_caption_discloses_endpoint_and_serving_confounding`、`test_cost_claim_says_usage_pricing_estimate_not_actual_bill`、`test_renderer_never_reads_raw_reasoning`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_renderer_contract.py tests/experiments/test_paper_exp5_artifacts.py tests/experiments/test_paper_formal_report.py -q`。Expected：old outputs/claims and row-level refs fail exact inventory/caption tests。
- [ ] Minimal implementation：renderer input is observation collection；contract allowlist determines numeric columns；exact tables=Exp1 feasibility、Exp2 trace/online、Exp3 trace robustness、Exp3 online `paper_table_recovery_online.csv`、Exp4 ablation、Exp5 quality/resources；audit JSONL separate。No zero-fill、no uncontracted derived score。
- [ ] GREEN：上述完整 RED command pass；all caption/claim tests pass/network zero。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：table/header/caption inventory；one observation→CSV/TEX/report cell and unsafe raw scan。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_exp5_artifacts.py src/tokenshare/experiments/paper_formal_report.py tests/experiments/test_paper_metric_renderer_contract.py tests/experiments/test_paper_exp5_artifacts.py tests/experiments/test_paper_formal_report.py; git commit -m "refactor(report): render lineage-backed paper cells"`

### Task 22: Replace legacy full-resource A with external bank pressure path

**Dependencies:** Task 21.

**Files:** Modify `src/tokenshare/experiments/paper_formal_evidence.py`、`src/tokenshare/experiments/paper_formal_runner.py`、`src/tokenshare/experiments/paper_formal_checkpoint.py`、`tests/experiments/test_paper_formal_evidence.py`、`tests/experiments/test_paper_formal_runner.py`、`tests/experiments/test_paper_formal_checkpoint.py`; create `tests/experiments/test_paper_full_resource_trace.py`.

- [ ] RED tests：`test_current_path_has_no_shared_exp1_builder_or_schema`、`test_exp3_reference_is_same_sample_bank_not_exp1_actual`、`test_500_root_trace_max_live_full_outcomes_is_one`、`test_500_root_index_memory_depends_on_summaries_not_payload_bytes`、`test_500_root_consumer_has_no_source_object_copy`、`test_checkpoint_resume_terminal_and_observation_digests_match`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_full_resource_trace.py tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -q`。Expected：shared builder/current path still found；old scan/copy behavior fails pressure probes。
- [ ] Minimal implementation：delete current-path shared functions/writers；retain historical decoder；route via validated index/external locator；preserve root-delta/streaming/rolling forecast；500-root synthetic Factor-compatible source uses logical clock/no provider/no Lean。
- [ ] GREEN：上述完整 RED command pass；`max_live_full_outcomes<=1`、source hash unchanged、no copied raw/network。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：legacy deletion/preserved generics；memory/object graph/checkpoint replay。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_formal_evidence.py src/tokenshare/experiments/paper_formal_runner.py src/tokenshare/experiments/paper_formal_checkpoint.py tests/experiments/test_paper_full_resource_trace.py tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py; git commit -m "refactor(experiments): replace shared scans with external bank index"`

### Task 23: Import frozen historical real success fixture and prove L2 path

**Dependencies:** Task 22.

**Files:** Create `src/tokenshare/experiments/paper_historical_fixture.py`、`src/tokenshare/experiments/historical_real_factorization_single_leaf.py`、`tests/experiments/test_paper_historical_real_fixture.py`、`tests/fixtures/paper/historical_real_factorization_v1/fixture_manifest.json`、`tests/fixtures/paper/historical_real_factorization_v1/batch_report.json`、`tests/fixtures/paper/historical_real_factorization_v1/per_number_result.json`、`tests/fixtures/paper/historical_real_factorization_v1/raw_model_output.json`、`tests/fixtures/paper/historical_real_factorization_v1/provenance.json`、`tests/fixtures/paper/historical_real_factorization_v1/request.json`、`tests/fixtures/paper/historical_real_factorization_v1/usage.json`.

- [ ] RED tests：`test_real_success_predicate_uses_per_number_passed_correct_oracle_not_missing_summary_passed`、`test_case_row_batch_raw_provenance_and_full_tree_digests_are_exact`、`test_tracked_fixture_contains_minimal_sanitized_unmodified_payload_and_source_hashes`、`test_dedicated_single_leaf_adapter_runs_normal_coordinator_parser_verifier_canonical_merge_ledger_to_table`、`test_adapter_rejects_formal_range_catalog_and_never_claims_range_semantics`、`test_fixture_provider_calls_zero_and_classification_regression_only`、`test_fixture_can_never_be_trace_paper_eligible`、`test_20260731_exp34_is_negative_only_and_source_hash_unchanged`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_historical_real_fixture.py -q`。Expected：importer/fixture absent；do not substitute Exp3/4 negative source。
- [ ] Minimal implementation：read only指定source；按第6.2节真实row predicate与case-row/batch/tree/raw/provenance digests验证，绝不要求或写入不存在的`summary.passed`。Importer保留raw payload bytes不改写，仅脱敏另存所需metadata对象并记录source/object digests。Dedicated adapter只接受该fixture marker，把target建为single leaf并走current normal coordinator/direct-factor parser/product+primality+oracle verifier/canonical/explicit single-leaf merge/ledger/direct/observations/regression table；拒绝正式range catalog/condition。
- [ ] GREEN：同命令从tracked fixture运行，即使测试时不读external source也pass；provider calls=0；paper eligible=false；输出明确`facility_path_only/not_formal_range_semantics`。Negative smoke expected-fail and pre/post source inventory identical。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：source identity/hash/classification；sanitization and source→state machine→table trace。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_historical_fixture.py src/tokenshare/experiments/historical_real_factorization_single_leaf.py tests/experiments/test_paper_historical_real_fixture.py tests/fixtures/paper/historical_real_factorization_v1; git commit -m "test(experiments): freeze historical real L2 fixture"`

### Task 24: Make replay and cell-lineage recomputation deterministic

**Dependencies:** Task 23.

**Files:** Create `src/tokenshare/experiments/paper_traceability.py`、`tests/experiments/test_paper_traceability.py`; modify `src/tokenshare/experiments/paper_formal_runner.py`、`src/tokenshare/experiments/paper_formal_report.py`、`tests/experiments/test_paper_formal_runner.py`、`tests/experiments/test_paper_formal_report.py`.

- [ ] RED tests：`test_replay_never_calls_provider_or_writes_source_bank`、`test_pure_component_recomputation_uses_synthetic_fixture_for_l1`、`test_artifact_root_audit_is_separate_l4_nodeid`、`test_two_recomputations_match_observations_tables_and_lineage_digests`、`test_every_numeric_cell_recomputes_or_has_null_reason`、`test_exp3_online_recovery_cells_recompute_from_current_provider_roles`、`test_trace_cells_recompute_from_source_locator_roles`、`test_missing_external_object_blocks_without_online_fill`、`test_historical_negative_expected_fail_does_not_mutate_source`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_traceability.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_report.py -q`。Expected：no cell-level lineage digest or second recomputation equality。
- [ ] Minimal implementation：canonical sort observations by table/row/column/observation id；recompute from direct/current/source immutable inputs through registry/renderer；write `audit/paper_cell_lineage.jsonl` and digests；locator read-only。Missing object blocks，never acquisition。
- [ ] GREEN：same input twice yields three identical digests；network/provider zero；source hashes unchanged。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：determinism and completeness；select one cell per experiment and recalc without renderer state。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_traceability.py src/tokenshare/experiments/paper_formal_runner.py src/tokenshare/experiments/paper_formal_report.py tests/experiments/test_paper_traceability.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_report.py; git commit -m "feat(experiments): deterministically recompute cell lineage"`

### Task 25: Plan L3 online/capability runs and produce compliant direct/current evidence

**Dependencies:** Task 24.

**Files:** Create `src/tokenshare/experiments/paper_online_checks.py`、`tests/experiments/test_paper_online_checks.py`; modify `src/tokenshare/experiments/paper_formal_callbacks.py`、`tests/experiments/test_paper_formal_callbacks.py`.

- [ ] RED tests：`test_capability_plan_is_exact_factor_and_lean_initial_replacement_four_calls`、`test_exp2_plan_uses_frozen_24_sequence_and_max_concurrent_roots_one_with_480_upper`、`test_exp2_plan_emits_digest_bound_condition_refs_and_compliant_online_direct_rows`、`test_exp3_plan_is_two_roots_12_upper`、`test_exp3_producer_emits_fault_death_before_distinct_new_attempt_provider_raw_provenance_usage_model_refs`、`test_exp3_producer_records_actual_usage_cost_and_wasted_inputs_without_computing_metrics`、`test_task25_exports_no_metric_formula_or_post_bank_gate`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_online_checks.py tests/experiments/test_paper_formal_callbacks.py -q`。Expected：plan/evidence producer module absent，或现有online path缺digest-bound direct/current provider链并混入metric/gate逻辑。
- [ ] Minimal implementation：只实现pure plan与evidence producer contract；capability controlled initial rejection hooks preregistered且保留source raw；Exp2 producer输出24 ordered condition refs、`max_concurrent_roots=1`和current provider direct rows；Exp3 producer输出ordered fault/death/new-attempt/provider objects与actual resource inputs。所有公式仍由Tasks13/14，registry由17，observation由20，renderer由21，gates由28；Task25不得计算union/severe/wasted/cost cell。
- [ ] GREEN：synthetic plan/evidence fixtures pass/network zero；产物可被Task13/14消费，online rows never merge into main 600-root table。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：counts/condition/output schema；identity/time ordering且静态扫描无formula/gate实现。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_online_checks.py src/tokenshare/experiments/paper_formal_callbacks.py tests/experiments/test_paper_online_checks.py tests/experiments/test_paper_formal_callbacks.py; git commit -m "feat(experiments): plan L3 checks and emit online evidence"`

### Task 26: Validate user-supplied paid receipts without creating them

**Dependencies:** Task 25.

**Files:** Create `src/tokenshare/experiments/paper_paid_authorization.py`、`tests/experiments/test_paper_paid_authorization.py`.

- [ ] RED tests：`test_receipt_digest_hashes_canonical_receipt_excluding_receipt_digest`、`test_receipt_binds_authorized_plan_inventory_and_prompt_admission_digests`、`test_same_valid_receipt_supports_new_run_then_resume_invocation`、`test_new_run_requires_absent_root_and_derives_marker_after_receipt_validation`、`test_marker_digest_derives_only_receipt_plan_profile_budget_inventory_admission_path`、`test_resume_requires_existing_exact_marker`、`test_inventory_or_admission_change_rejects_receipt_before_secret_reserve_dispatch`、`test_partial_or_conflicting_root_rejected_before_secret_reserve_dispatch`、`test_expired_receipt_allows_pure_reconcile_close_but_never_new_dispatch`、`test_offline_approval_is_rejected`、`test_l3_receipt_cannot_authorize_full_bank_exp1_or_either_exp5_scope`、`test_exp5_capability_receipt_cannot_authorize_exp5_full_online`、`test_validator_has_no_command_or_function_to_mint_receipt`、`test_allow_provider_calls_flag_is_also_required`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_paid_authorization.py -q`。Expected：module absent；any permissive offline approval path fails。
- [ ] Minimal implementation：parse/validate第5.3与3.7 schema；receipt canonical digest只排除`receipt_digest`自身并绑定authorized plan/inventory/admission；clock validation uses explicit now parameter。CLI invocation mode独立于receipt：`new_run`在receipt验证后以create-new派生marker绑定不存在root，同一receipt的`resume`验证既有marker全部digests；expired只进入pure reconcile/close branch。Validator只读receipt且只能建立/验证marker。No `approve/create/mint/record-paid` API。
- [ ] GREEN：上述完整 RED command pass/network zero；validation failure happens before API-key env read and budget reserve。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：scope matrix；search CLI/module for receipt creation path and implicit authorization。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_paid_authorization.py tests/experiments/test_paper_paid_authorization.py; git commit -m "feat(experiments): validate scoped paid execution receipts"`

### Task 27: Add bounded pipeline CLI commands

**Dependencies:** Task 26.

**Files:** Create `src/tokenshare/experiments/run_paper_pipeline.py`、`tests/experiments/test_run_paper_pipeline.py`; modify `src/tokenshare/experiments/run_paper_experiments.py`、`tests/experiments/test_run_paper_experiments_cli.py` only for supersession routing.

- [ ] RED tests：one exact test per subcommand：`validate-profile`、`plan-bank`、`acquire-bank`、`audit-bank`、`run-trace`、`run-online-checks`、`run-exp1-online`、`run-exp5-capability-smoke`、`run-exp5-online`、`render`、`replay`、`audit-cell-lineage`、`validate-formal-execution-gate`、`validate-paper-publication-gate`；plus `test_provider_commands_require_receipt_allow_flag_and_output_mode`、`test_exp5_capability_and_full_scopes_are_not_interchangeable`、`test_external_bank_root_is_process_local_resolver_binding`、`test_unlimited_budget_rejected_for_provider_commands`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_run_paper_pipeline.py tests/experiments/test_run_paper_experiments_cli.py -q`。Expected：new module/subcommands absent。
- [ ] Minimal implementation：each thin subcommand delegates one existing service and prints versioned JSON `{status,scope,profile/budget/inventory/receipt/output-marker digests,evidence_class,provider_calls}`。Offline commands reject provider option；provider commands validate receipt→new/resume marker→prepared prompt admission→budget before secret。`--external-bank-root`仅传process-local resolver。两个gate parsers存在但policy在Task28。
- [ ] GREEN：all CLI fake/offline tests pass；provider network zero；unknown/missing args exit2 with no output mutation。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：command/schema matrix；argv/env/log secret and state-change boundaries。
- [ ] Suggested commit：`git add src/tokenshare/experiments/run_paper_pipeline.py src/tokenshare/experiments/run_paper_experiments.py tests/experiments/test_run_paper_pipeline.py tests/experiments/test_run_paper_experiments_cli.py; git commit -m "feat(experiments): add bounded EPD-027 pipeline CLI"`

### Task 28: Split formal execution gate from paper publication gate

**Dependencies:** Task 27.

**Files:** Create `src/tokenshare/experiments/paper_formal_gate.py`、`tests/experiments/test_paper_formal_gate.py`; modify `src/tokenshare/experiments/run_paper_pipeline.py`、`tests/experiments/test_run_paper_pipeline.py` to wire `validate-formal-execution-gate` and `validate-paper-publication-gate`.

- [ ] RED tests：`test_execution_gate_checks_only_prerequisites_available_before_selected_run`、`test_execution_gate_never_requires_l4_outputs_or_post_bank_severe`、`test_exp1_execution_requires_common_offline_and_exp1_receipt_not_bank_or_exp5`、`test_exp2_to_exp4_execution_requires_full_bank_receipt_plan_and_complete_inventory_not_future_metrics`、`test_exp5_capability_and_full_execution_scopes_are_distinct`、`test_publication_gate_runs_only_after_terminal_outputs`、`test_publication_gate_requires_formal_cell_audit_and_post_bank_intersection_severe_for_exp2`、`test_unselected_experiment_approval_is_not_required`、`test_facility_l1_mini_gate_cannot_become_formal_publication_pass`、`test_blocked_l3_or_l4_never_passes_publication`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_formal_gate.py tests/experiments/test_run_paper_pipeline.py -q`。Expected：parsers exist but execution/publication policies absent or cyclically require outputs before execution。
- [ ] Minimal implementation：`formal_execution_gate(selected_experiments, prerequisites)`只检查运行前可得的L1/L2、authority、profile/contract、selected receipt/budget/inventory/output binding以及full-bank completeness（若selected Exp2–4）；绝不要求本次尚未产出的formal L4、tables或post-bank severe。`paper_publication_gate(selected_experiments, terminal_evidence)`只在terminal outputs后检查selected evidence eligibility、formal cell audit/determinism和Exp2 online/main intersection>=3/no severe。Small facility gate fixtures仅标`facility_gate_verified`。Return exact blocked reasons and provider calls0。
- [ ] GREEN：两阶段truth table pass；execution gate可在空fresh output通过，publication gate对同一空output blocked；selected Exp1不询问Exp5，P0-full才聚合全部scope。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：pre/post truth table与无循环DAG；CLI binding、stage type、no side effects/provider。
- [ ] Suggested commit：`git add src/tokenshare/experiments/paper_formal_gate.py src/tokenshare/experiments/run_paper_pipeline.py tests/experiments/test_paper_formal_gate.py tests/experiments/test_run_paper_pipeline.py; git commit -m "feat(experiments): split execution and publication gates"`

### Task 29: Supersede old launchers and add scoped new launchers

**Dependencies:** Task 28.

**Files:** Create `local/run_epd027_l3_checks.ps1`、`local/run_epd027_bank_acquisition.ps1`、`local/run_epd027_trace_matrix.ps1`、`local/run_epd027_exp1_online.ps1`、`local/run_epd027_exp5_capability.ps1`、`local/run_epd027_exp5_online.ps1`; modify `local/run_exp1_exp4_v3_smoke.ps1`、`local/run_exp3_exp4_v3_smoke.ps1`、`local/run_exp5_v3_smoke.ps1`、`tests/experiments/test_smoke_launcher_supervision.py`、`tests/experiments/test_paper_smoke.py`.

- [ ] RED tests：`test_old_launchers_exit_two_before_secret_read`、`test_l3_launcher_requires_exact_paid_receipt_and_allow_switch`、`test_bank_exp1_exp5_scopes_are_not_interchangeable`、`test_exp1_launcher_routes_run_exp1_online`、`test_exp5_capability_and_online_launchers_require_distinct_scopes`、`test_new_launchers_never_pass_unlimited_budget`、`test_formal_launchers_run_execution_gate_before_dispatch_and_publication_gate_after_terminal`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_smoke_launcher_supervision.py tests/experiments/test_paper_smoke.py -q`。Expected：old scripts still dispatch/contain unlimited semantics；new scripts absent。
- [ ] Minimal implementation：old scripts print EPD-027 supersession and exit2 before env/key。New wrappers bind `new_run|resume` marker、scope、receipt path、explicit PowerShell `-AllowProviderCalls`; invoke exact CLI subcommand。Exp1 routes `run-exp1-online`；Exp5 capability/full分别routes `run-exp5-capability-smoke`/`run-exp5-online`并拒绝scope互换；formal launchers dispatch前execution gate、terminal后publication gate。Supervisor/log redaction reused；所有新provider launchers当前只实现/离线验收，不在本计划执行。
- [ ] GREEN：scripts parse in no-provider tests；old exit2；wrong receipts fail pre-secret/network。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：launcher/scope matrix；PowerShell argv quoting、secret timing、exit propagation/log redaction。
- [ ] Suggested commit：`git add local/run_epd027_l3_checks.ps1 local/run_epd027_bank_acquisition.ps1 local/run_epd027_trace_matrix.ps1 local/run_epd027_exp1_online.ps1 local/run_epd027_exp5_capability.ps1 local/run_epd027_exp5_online.ps1 local/run_exp1_exp4_v3_smoke.ps1 local/run_exp3_exp4_v3_smoke.ps1 local/run_exp5_v3_smoke.ps1 tests/experiments/test_smoke_launcher_supervision.py tests/experiments/test_paper_smoke.py; git commit -m "feat(experiments): replace legacy smoke launchers"`

### Task 30: Assemble four exact verification profiles

**Dependencies:** Task 29.

**Files:** Create `verification/profiles/paper-l1-components.txt`、`verification/profiles/paper-l2-historical-real.txt`、`verification/profiles/paper-l3-new-real-smoke-audit.txt`、`verification/profiles/paper-l4-cell-lineage.txt`; modify `verification/run_verification.py`、`init.ps1`、`init.sh`、`tests/test_init_verification_profiles.py`.

- [ ] RED tests：`test_four_named_profiles_have_exact_nonempty_nodeids`、`test_profiles_load_network_tripwire`、`test_l1_includes_all_offline_nodeids_from_tasks_0_through_29_including_task25_to_29`、`test_l1_includes_task24_pure_component_but_not_artifact_audit`、`test_l1_includes_one_lean_root_at_most_two_checker_calls`、`test_l2_runs_positive_historical_single_leaf_full_path`、`test_l3_audit_requires_terminal_real_output_or_reports_blocked_not_passed`、`test_l4_contains_task24_artifact_audit_and_requires_l3_pass_two_equal_recomputations`、`test_fast_never_claims_l1_to_l4`、`test_unknown_profile_or_outside_path_fails_closed`。
- [ ] RED command：`conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/test_init_verification_profiles.py -q`。Expected：profiles/`--profile` behavior absent。
- [ ] Minimal implementation：profile manifests list exact nodeids, not directories。L1 covers Tasks0–29全部offline component nodeids（含Tasks25–29）、Task24 pure component、small facility gate fixture和one Lean trace fixture/<=2 checker calls；L2 Task23 positive+negative；L3只audit supplied artifact root且不能dispatch；L4含Task24 artifact-root lineage/digest audit。`run_verification.py --profile NAME --artifact-root PATH` validates profile dir and prints profile digest/count/status；init optional focused profile preserves default Fast。
- [ ] GREEN：run four profile parser tests；each offline run loads tripwire；missing L3 artifact returns `blocked` nonzero, not pass。
- [ ] 综合 review（同一 reviewer 同时覆盖规格/质量/全链/影子 TokenShare）：nodeid/level mapping；prove Fast coverage is not substituted and no profile launches provider。
- [ ] Suggested commit：`git add verification/profiles verification/run_verification.py init.ps1 init.sh tests/test_init_verification_profiles.py; git commit -m "test(verification): add strict L1 to L4 profiles"`

### Task 31: Execute and review L1 component acceptance

**Dependencies:** Task 30.

**Files:** Create runtime output only at `local/verification/epd027-l1/`; no planned source edit.

- [ ] RED evidence：each implementation Task already captured its named failing test before code。Reject any Task lacking authentic RED record; do not manufacture a late failure。
- [ ] Run:

  ```powershell
  conda run -n tokenshare python verification/run_verification.py --profile paper-l1-components --artifact-root local/verification/epd027-l1
  ```

  Expected：Tasks0–29全部offline nodeids（明确含Tasks25–29）、Task24 pure component、两阶段gate mini fixture均pass；provider calls=0；one Lean root、checker calls<=2；profile result `l1_components=passed`，但不得设置formal L3/L4 pass。
- [ ] Run `.\init.ps1` Fast。Expected exit0；it is regression evidence only。
- [ ] Explicitly do not run Full、all-tests pytest、LeanAudit、force-all or large Lean。
- [ ] 一个综合独立 reviewer 同时覆盖规格、质量、全链与影子 TokenShare 检查：验证 L1 entrance/coverage/results，并选取 PreparedOutboundRequest→bank→budget→scheduler→parent commit→direct→observation chain 后复核 profile；任何 Critical/Important 返回 owning Task。
- [ ] Suggested commit：none for runtime evidence alone；defect fixes use owning Task commit after both reviews。

### Task 32: Execute and review L2 historical-real full-path acceptance

**Dependencies:** Task 31.

**Files:** Create runtime output only at `local/verification/epd027-l2/`; tracked fixture files under `tests/fixtures/paper/historical_real_factorization_v1/` were already committed by Task23 and are read-only here.

- [ ] Run:

  ```powershell
  conda run -n tokenshare python verification/run_verification.py --profile paper-l2-historical-real --artifact-root local/verification/epd027-l2
  ```

  Expected：positive source manifest records actual per-number predicate `status=passed/final_correctness=true/oracle_match=true`，and exact case-row/batch/tree/raw/provenance digests；unmodified saved raw payload经dedicated single-leaf adapter到terminal current protocol/direct/observations/regression table；provider calls=0；classification regression_only/paper eligible false/not formal range semantics。Negative Exp3/4 expected-fail leaves source hashes unchanged。Result=`l2_historical_real=passed` only when all hold。
- [ ] Run replay twice for L2 and require equal observation/table/lineage digests；this supports determinism but does not replace L4。
- [ ] 一个综合独立 reviewer 同时覆盖规格、质量、全链与影子 TokenShare 检查：验证使用 frozen positive source（不是 2026-07-31 negative），并人工追踪 source object→locator→current full lifecycle→one numeric cell；Blocked/fail 返回 owning Task。
- [ ] Suggested commit：none for ignored L2 outputs；fixture/source changes are forbidden here and must return to Task23。

### Task 33: Conditionally execute L3 new-real smoke under paid receipt

**Dependencies:** Task 32.

**Files:** Create runtime outputs only at `outputs/experiments/epd027-l3-capability-v1/`、`outputs/experiments/epd027-l3-exp2-online-v1/`、`outputs/experiments/epd027-l3-exp3-online-v1/`、`local/verification/epd027-l3/`; read user receipt only from `local/epd027_l3_paid_receipt.local.json`; no tracked source edit.

- [ ] Plan-only preflight always runs first with provider calls0：validate profile/authority/config/pricing、receipt presence/scope/expiry、`new_run` absent-root/create-new marker or `resume` exact existing marker、profile/budget/inventory/output-path digests、SQLite budget `516/171708288/979.524864` and absolute CNY1000。
- [ ] If user-supplied receipt is absent/invalid，invoke validation only, persist `l3_new_real_smoke_blocked` reason outside protocol ledger，assert provider calls0，skip provider launcher，continue Task34。Do not ask mid-run or create receipt；blocked is not pass。
- [ ] If valid，run:

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File local/run_epd027_l3_checks.ps1 -PaidReceipt local/epd027_l3_paid_receipt.local.json -OutputMode NewRun -AllowProviderCalls
  ```

  Expected：4 capability calls exactly (Factor initial/replacement + Lean initial/replacement), immutable complete capability manifest, capability trace through current full path/tables；Exp2 24 roots按frozen sequence、`max_concurrent_roots=1`、all six levels/480 upper；Exp3 2 roots/12 upper。Task25只产生compliant direct/current evidence；Task13计算per-worker union fraction<=0.2；Task14→17→20→21生成Exp3 recovery online actual calls/usage/cost estimate/wasted actual tokens和两条ordered chains。No pre-bank severe/intersection requirement。
- [ ] Audit with `paper-l3-new-real-smoke-audit` profile。Exit `l3_new_real_smoke_verified` only if all L3 criteria in6.3 pass and settled+reserved budget stays within all limits；budget/quota/provider failure yields blocked/budget-exhausted, not pass。
- [ ] 一个综合独立 reviewer 同时覆盖规格、质量、全链与影子 TokenShare 检查：验证 receipt/counts/threshold timing，并追踪两个 capability roots、全部 online evidence classes、budget reconciliation 与 secret scan。
- [ ] Suggested commit：none for paid/ignored outputs；any code fix returns to owning Task and invalidates/restarts fresh-output L3。

### Task 34: Execute L4 cell audit when eligible, then update docs/handoff

**Dependencies:** Task 33.

**Files:** Create runtime output only at `local/verification/epd027-l4/`; after status known modify `progress.md`、`feature_list.json`、`session-handoff.md`、`Doc/TechnicalDocument/tokenshare_v1_code_map.md`、`Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`、`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`、`Doc/agent-navigation.md` only if navigation changes.

- [ ] If L3 verified，run `paper-l4-cell-lineage` twice against the immutable L3 output。Expected numeric coverage=1.0；online cells require current provider roles/source N/A，trace cells require source locator roles/current provider N/A；Exp3 recovery online table完整进入；all non-null cells recompute；all null cells have full denominator/reason；two observation/table/lineage digests equal；provider calls0；status `l4_cell_traceability_verified`。
- [ ] If L3 blocked，write `l4_cell_traceability_blocked` with prerequisite reason。Optionally run offline L2 lineage diagnostics, but never label them L4 pass。
- [ ] Run `validate-formal-execution-gate` for selected future diagnostic scope and `validate-paper-publication-gate` against current terminal evidence，both provider calls0。Expected execution gate只报告运行前缺失的scope/receipt/bank prerequisites，不循环要求future L4；publication gate remains blocked for missing formal outputs/full acquisition/post-bank audit；do not dispatch。
- [ ] Update Chinese progress/feature/handoff with separate `facility_offline_implemented`、L3 verified|blocked、L4 verified|blocked、`formal_matrix_no_go`。Keep `feat-011` in-progress；record no Full/all-tests/LeanAudit/large Lean。
- [ ] Update code map for all source changes。Update latest design stale “EPD-027未实现” only to exact verified facility status；keep no formal results/full bank language。Append implementation evidence to decision log without rewriting history。
- [ ] Second documentation review：search current docs for shared Exp1 actual、old next smoke、old evidence class/metrics、actual bill wording；correct current prose or mark historical explicitly。
- [ ] Rerun affected four focused profiles where prerequisites exist and `.\init.ps1` Fast；blocked L3/L4 profiles remain blocked, not silently omitted。Do not run prohibited broad commands。
- [ ] 一个综合独立 reviewer 同时覆盖规格、质量、全链与影子 TokenShare 检查：检查全部 24 项要求和状态真实性，以及 source→state→direct→observation→table→cell lineage、commands/results/docs/formal gate；Critical/Important 必须清零。
- [ ] Suggested commit：`git add progress.md feature_list.json session-handoff.md Doc/TechnicalDocument/tokenshare_v1_code_map.md Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md Doc/agent-navigation.md; git commit -m "docs(experiments): record reviewed EPD-027 facility status"`

## 11. Artifact ownership, rollback, and recovery

| Artifact | Authority/mutability | Current consumer rule |
|---|---|---|
| Prepared body/request identity | acquisition content-addressed immutable | exact bytes to artifact/digest/wire |
| Bank internal ArtifactRefs/raw/failure/provenance/usage/latency/pricing | external bank only, immutable | current suite sees opaque locators, streamed+hashed |
| SQLite reservations/spend/acquisition state | WAL database, transactional authority | unique inventory+request key；JSONL export audit-only |
| Output binding marker | root-local create-new immutable marker | new-run atomically creates；resume exact digest match |
| Current attempts/events/delivery attempts/consumptions | current suite append-only | killed/fenced never consumption |
| Direct rows/metric observations/tables/lineage | deterministic derivations | regenerate from immutable inputs |
| Historical fixture | tracked sanitized regression-only | never paper eligible/never upgraded |
| Paid receipt | user-supplied local immutable input | exact scope/time/output; agent cannot mint |

- Rollback introduces no schema rewrite：disable new registry/entrypoints but preserve bank/ledger/evidence。Never delete or edit historical/source artifacts。
- Resume trusts root marker+inventory+SQLite+filesystem commit markers：terminal success/failure never resend；published-unsettled reconcile once；ambiguous requires separate remaining paid authority；missing only acquisition target。
- Trace resume restores checkpoint、attempt ordinal、logical event queue and same external manifest；two recomputations must match。
- New-run output roots avoid deleting old renderer files；resume只写matching marker-owned root。No destructive git/filesystem command is part of this plan。

## 12. Formal execution/publication gates and future-only run order

These are gate contracts, not current authorization：

1. `formal_execution_gate` runs before selected provider/trace execution and consumes only already-existing prerequisites：current authority/profile/metric/L1/L2，selected paid receipt/budget/inventory/output marker，and for Exp2–4 an already complete approved full bank。It never requires the selected run's future direct rows、formal L4、tables、post-bank intersection或severe result。
2. Exp1 execution requires its own `exp1_full_online` receipt；unselected Exp5/bank不相关。Exp5 capability/full online分别要求`exp5_capability_smoke`/`exp5_full_online`，不能互换。Exp2–4 full bank另需`epd027_full_bank_acquisition`。
3. `paper_publication_gate` only runs after selected formal executions terminal；it checks terminal evidence eligibility、fixed denominator、formal cell audit/deterministic digests and, for Exp2 claim, online/main same-case intersection>=3且无severe divergence。
4. L1可用small fixture验证两种gate控制流并记`facility_gate_verified`，但不能满足formal execution的paid prerequisites或publication的formal evidence。
5. P0-core selected set=Exp1+Exp2–4；P0-full再加Exp5。Any missing prerequisite returns blocked with exact reasons/provider calls0。
6. Ordering is acyclic：offline facility → execution prerequisites/gate → selected execution → cell audit/post-bank comparison → publication gate。Publication output绝不反向成为execution prerequisite。

No gate may shrink roots/repeats/modes、change model、use JIT bank fill、reuse expired receipt or convert regression/capability evidence to paper eligible。

## 13. Definition of Done and handoff

Offline facility DoD：Tasks0–32 completed/reviewed，L1/L2 passed，Fast passed，provider calls0 for offline paths，historical source immutable，status=`facility_offline_implemented`。

L3/L4 DoD are separate and only pass under sections6.3/6.4；otherwise exact blocked statuses。Formal matrix remains `NO-GO` in this implementation scope。

Handoff must record：branch/worktree/user dirty files；Task0–34 developer/综合 reviewer identities and findings；profile/authority/contract/fixture/inventory/budget/receipt/manifest digests；calls/tokens/CNY/reservation peak/terminal/missing/ambiguous counts；L1–L4 statuses；minimal Lean root/checker count；source hashes；prohibited broad commands not run；future selected-scope requirements。

## 14. Five highest execution risks

1. **Bytes/admission identity split:** factory绕过、artifact/digest/wire再次序列化、endpoint重拼、Lean request-derived id或reserve前未挡超长prompt都会破坏stable pairing/预算。
2. **Budget/commit double-spend:** 非原子SQLite unique/state或缺filesystem marker会让两个进程同digest双调用；published-unsettled若重发还会重复收费。
3. **Scheduler/worker false delivery:** coordinator不按frozen queue、child写side effect或parent先commit后判kill/fence会伪造时间/恢复；ordinal不持久化会取错replacement slot。
4. **Condition/source/class inflation:** 解析condition id、漏manifest axes、locator泄漏path/copy raw，或把historical/synthetic/capability升级为formal trace都会使denominator/provenance失真。
5. **Cell/gate drift:** online/source roles混栏、Exp3 online绕过projector链、执行门循环依赖future L4，或公式只在renderer修补，会生成无法证明的论文表。

## 15. Final read-only self-review

计划作者完成后只运行：

```powershell
$plan = 'Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md'
$text = Get-Content -LiteralPath $plan -Encoding UTF8 -Raw
$forbidden = @(('T'+'ODO'), ('T'+'BD'), ('sim'+'ilar task'), ('Can '+'run parallel'))
$hits = Select-String -LiteralPath $plan -Encoding UTF8 -Pattern $forbidden
if ($hits) { $hits; exit 1 }

$taskLines = Select-String -LiteralPath $plan -Encoding UTF8 -Pattern '^### Task [0-9]+:'
$taskNumbers = $taskLines | ForEach-Object {
  [int]([regex]::Match($_.Line, 'Task ([0-9]+):').Groups[1].Value)
}
if ($taskLines.Count -ne 35 -or (Compare-Object $taskNumbers (0..34))) {
  throw 'Task headings must be exactly Task 0 through Task 34'
}

$singleRefs = [regex]::Matches($text, 'Tasks? ([0-9]+)') |
  ForEach-Object { [int]$_.Groups[1].Value }
$rangeEnds = [regex]::Matches($text, 'Tasks? [0-9]+[–-]([0-9]+)') |
  ForEach-Object { [int]$_.Groups[1].Value }
$badRefs = @($singleRefs + $rangeEnds) | Where-Object { $_ -lt 0 -or $_ -gt 34 }
if ($badRefs) { throw "Out-of-range Task reference: $($badRefs -join ',')" }

$requiredAnchors = @(
  '^\*\*Status:\*\* `approved-for-offline-implementation`$',
  '^## 4\. Metrics-first 总矩阵$',
  '^## 6\. 四级验收：严格 entrance/exit$',
  '^## 7\. Preserve/refactor/create/delete map$',
  '^## 8\. 严格串行 DAG$',
  '^## 15\. Final read-only self-review$'
)
foreach ($anchor in $requiredAnchors) {
  if (-not (Select-String -LiteralPath $plan -Encoding UTF8 -Pattern $anchor)) {
    throw "Missing required anchor: $anchor"
  }
}

$secondReviewAnchors = @(
  'Tasks 0–32', 'Task 33 的新真实', 'Task 34 的逐 numeric cell',
  'preregistered_condition_ref', 'condition_axes', 'final_result_reference_complete',
  'paper_table_recovery_online.csv', 'current_provider_object_refs',
  'source_bank_object_locators', 'max_concurrent_roots=1',
  'sha256:b3e61b280c635006a894719e5b6d816efdceab49c24f0c41d5d3db9711e46404',
  'PreparedTraceDelivery.v1', 'PreparedOutboundRequestFactory',
  'ResponseBankManifest.v1', 'BEGIN IMMEDIATE', 'busy_timeout',
  'receipt_digest = SHA256(canonical receipt excluding receipt_digest)',
  'inventory_entry_id = SHA256(canonical inventory row excluding inventory_entry_id)',
  'semantic_slot_key', 'TRACE_DELIVERY_COMMITTED.v1', 'TraceConsumptionCore',
  'payload严禁 `committed_event_ref`、`event_seq`、`event_hash`',
  'tests/executors/test_ai_api_executor_failover.py',
  'tests/experiments/test_paper_gate_c_dispatcher.py',
  'tests/experiments/test_paper_gate_c_structured_output.py',
  'tests/integration/test_paper_protocol_runtime_integration.py',
  'preregistered_case_ref', 'tokenshare.prompt_admission.utf8_byte_upper.v1',
  'formal_execution_gate', 'paper_publication_gate',
  'run-exp1-online', 'run-exp5-capability-smoke', 'run-exp5-online',
  'Tasks25–29', 'src/tokenshare/core/models.py', 'src/tokenshare/storage/sqlite_index.py'
)
foreach ($anchor in $secondReviewAnchors) {
  if (-not $text.Contains($anchor)) { throw "Missing second-review closure anchor: $anchor" }
}

$duplicateHeadings = Get-Content -LiteralPath $plan -Encoding UTF8 |
  Where-Object { $_ -match '^#{1,6} ' } |
  Group-Object |
  Where-Object Count -gt 1
if ($duplicateHeadings) { $duplicateHeadings; exit 1 }

if ((4 + 480 + 12) -ne 496) { throw 'Paid call arithmetic mismatch' }
if ((496 + 20) -ne 516) { throw 'Hard call arithmetic mismatch' }
if ((516 * 332768) -ne 171708288) { throw 'Token arithmetic mismatch' }
if ([decimal](516 * 1.898304) -ne [decimal]979.524864) { throw 'CNY arithmetic mismatch' }

$subcommands = @(
  'validate-profile','plan-bank','acquire-bank','audit-bank','run-trace',
  'run-online-checks','run-exp1-online','run-exp5-capability-smoke','run-exp5-online',
  'render','replay','audit-cell-lineage','validate-formal-execution-gate',
  'validate-paper-publication-gate'
)
foreach ($name in $subcommands) {
  if ($text -notmatch [regex]::Escape($name)) { throw "Missing CLI subcommand: $name" }
}

git diff --check -- $plan
git status --short -- $plan
```

Expected：forbidden scan empty；Tasks精确0–34且所有引用不越界；anchors/CLI names齐全；headings无重复；预算算术一致；`git diff --check` exit0；status只报告本计划文件。禁止命令只允许出现在明确的“禁止/不得/do not run”语句中。
