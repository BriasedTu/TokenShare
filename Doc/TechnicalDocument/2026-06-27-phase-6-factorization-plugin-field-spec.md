# Phase 6 Factorization 插件字段规格 / TDD 计划

## 元数据

| 字段 | 值 |
|---|---|
| Feature | `feat-007` Phase 6 - Experimental Plugins |
| 子范围 | Factorization 插件第一版 |
| 状态 | Draft，implementation-ready |
| 创建日期 | 2026-06-27 |
| 最后更新 | 2026-06-27 |
| 主要依据 | `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-discussion-notes.md`、主 TDD 第 4.3 / 8 / 12 / 14.1 / 21 节、Phase 4 / Phase 5 字段规格 |

## 1. 概念审查结论

本轮复查后，Factorization 插件第一版可以进入字段设计和 TDD 计划。讨论稿中的核心口径已经足够明确：

1. 当前插件就是主 TDD 第 14.1 节的整数分解插件，不再拆出第二个 continuation 插件。
2. 插件主导候选因子搜索空间分区，AI / executor 只处理 bounded `factor_search_range`。
3. 插件验证 `found_factor` 与 `no_factor_in_range`，协议核心不理解整数分解数学规则。
4. 第一版使用 Phase 5 已有 all-required merge，`one_success`、optional slots、early terminal resolution 和 early pruning 不进入本切片。
5. 实验级 AI API executor 已拆到 `feat-008` / Phase 7，实验基础设施、故障模拟和 metrics 顺延到 `feat-009` / Phase 8；本规格只覆盖插件字段与可测试闭环。

需要显式写入规格的概念边界有两项：

1. 主 TDD 第 14.1 节的“任一子节点找到因数即可提前完成并剪枝”不是当前 all-required merge 能表达的行为。本规格把它列为未覆盖项，并要求测试证明第一版没有宣称 early success / pruning。
2. 现有 Phase 5 `ExpectedOutputResolution` 只能把 merge unit 的 canonical output 直接解析为 parent expected output。完整多层 prime factor tree 需要后续扩展 resolution 来源或 continuation resolution 语义。第一版端到端验收使用 prime 与 semiprime fixture：prime 通过完整 range no-factor 或小素数 direct complete，semiprime 通过 merge policy 验证 `d` 和 `N/d` 都为 prime 后解析为 `prime_factorization_result`。若余因子仍为 composite，插件可以产生 `nontrivial_factor_found` 审计输出，但第一版不把它解析成最终 `prime_factorization_result`。

## 2. 背景与问题

Phase 1-5 已经提供协议对象、artifact store、event ledger、插件/执行器 descriptor、verification/canonical、atomic expansion、merge task lifecycle、expected output resolution、contribution、settlement 和 subtree pruning。Phase 6 开始需要把这些通用协议能力落到具体插件上，先用整数分解验证“插件拥有领域规则，协议只记录可重放事实”的边界。

Factorization 插件要解决的不是高性能大数分解，而是本地可审计协议闭环：

- 将一个 `factor_integer(N)` 目标转化为稳定、无 gap、无 overlap 的候选因子范围。
- 让 executor 在固定范围内搜索，输出结构化 `range_result`。
- 让插件 deterministic verifier 重新检查结构化结果，尤其是 `no_factor_in_range` 不能只相信自然语言。
- 使用 Phase 4 `DecompositionProposal` / `MergePlan` 和 Phase 5 all-required merge 生成 parent expected output。
- 让 prime / semiprime fixture 完整走到 parent completion、contribution 和 sandbox settlement。

如果不先写本规格，后续实现容易出现三类错误：把拆分权交给 AI；把 factorization 私有状态塞进 `TaskUnit.plugin_payload` 当权威事实；把 early success / full recursion 口头宣称为已实现但没有协议字段和事件支持。

## 3. 范围

### 3.1 本规格范围内

- `tokenshare.plugins.factorization` 插件包的 descriptor、schema 常量、纯对象、split strategy、validator、merge policy、execution instruction helper 和 fixture。
- `factor_integer`、`factor_search_range` 和 `factorization_merge` 三类领域 unit 的 typed input / output contract。
- `candidate_range_partition.v1`：连续区间候选因子搜索空间分区。
- `range_result.v1`：`found_factor` / `no_factor_in_range` 结构化输出。
- `prime_certificate`、`nontrivial_factor_found`、`prime_factorization_result` 三类 merge output contract。
- 使用现有 Phase 3-5 event 和 projection，不新增协议 event type。
- prime 与 semiprime fixture 的端到端 flow 测试。

### 3.2 本规格范围外

- Pollard Rho、ECM、wheel factorization、自适应搜索树或概率分解算法。
- `one_success` merge、optional slots、partial merge、early terminal resolution。
- 找到一个因数后立即取消 sibling range 的 early pruning。
- composite cofactor 的完整多层 prime factor tree resolution。
- 实验级 AI API executor、fault simulation、`ExperimentRunner`、`MetricsCollector` 和 metrics report。
- 真实分布式 executor、生产级 AI API 平台、真实 Lean 或真实链上结算。

### 3.3 后续扩展保留

- `one_success` / early terminal resolution：允许某个 verified `found_factor` slot 提前解析 parent expected output。
- sibling pruning：在 early terminal resolution 后取消未完成 range children。
- recursive expected output resolution：允许 `nontrivial_factor_found(d, q)` 生成同插件递归 factor graph，并把最终 descendant result 解析回原 owner expected output。
- 更大范围的 proof certificate：对 `no_factor_in_range` 使用可重放证书替代 brute-force recheck。

## 4. 技术方案概览

第一版流程如下：

```text
factor_integer root / recursive unit
-> execution produces canonical factor_integer_subject
-> SPLIT_STRATEGY_INVOCATION_RECORDED
-> candidate_range_partition.v1 returns complete or expand
-> complete path:
     EXPANSION_DECISION_RECORDED(action=complete)
     TASK_UNIT_STATE_CHANGED -> Completed
-> expand path:
     DECOMPOSITION_PROPOSAL_RECORDED
     EXPANSION_DECISION_RECORDED(action=expand)
     MERGE_PLAN_RECORDED
     factor_search_range child TASK_UNIT_CREATED events
     TASK_EXPANDED
-> range children execute bounded search and submit range_result
-> plugin verifier records VerificationReport
-> CANONICAL_OUTPUTS_BOUND selects each range result
-> Phase 5 creates merge TaskUnit after all required slots canonical
-> merge TaskUnit canonical output becomes prime_certificate or prime_factorization_result
-> MERGE_RECORDED + EXPECTED_OUTPUT_RESOLVED
-> parent_completion_batch
-> root settlement batch
```

根 / 递归 `factor_integer` unit 的 canonical output 不直接等于最终答案。它是一个可审计的 `factor_integer_subject`，用于证明插件扩图消费的是正式事实而不是临时输入。最终答案通过 direct complete 的 `completed_output_refs` 或 merge path 的 `ExpectedOutputResolution` 暴露。

## 5. 模块切分

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/tokenshare/plugins/factorization/__init__.py` | 修改 | 导出 factorization 插件公共对象。 |
| `src/tokenshare/plugins/factorization/schemas.py` | 新增 | schema id、schema version、unit type、output name 和 policy id 常量。 |
| `src/tokenshare/plugins/factorization/models.py` | 新增 | `FactorIntegerSubject`、`CandidateRangePartitionParams`、`FactorSearchRangeInput`、`RangeResult`、`FactorizationMergeResult` 等纯对象和 digest helper。 |
| `src/tokenshare/plugins/factorization/descriptor.py` | 新增 | 构造 `PluginDescriptor`、`OutputContract` 和 `SplitStrategyContract`。 |
| `src/tokenshare/plugins/factorization/split_strategy.py` | 新增 | `candidate_range_partition.v1`，生成 `SplitStrategyResult`、`DecompositionProposal` 和 `MergePlan`。 |
| `src/tokenshare/plugins/factorization/validator.py` | 新增 | 结构化 output parser 和 domain verifier，不写协议状态。 |
| `src/tokenshare/plugins/factorization/merge_policy.py` | 新增 | all-required range results 合并，生成 merge output artifact body。 |
| `src/tokenshare/plugins/factorization/fixtures.py` | 新增 | prime / semiprime / invalid output fixture case。 |
| `tests/plugins/factorization/*.py` | 新增 | 插件纯对象、partition、verifier、split、merge policy 测试。 |
| `tests/test_phase6_factorization_flow.py` | 新增 | 使用现有 ProtocolEngine / Phase 5 flow 拼接端到端 fixture。 |

本阶段不改 `tokenshare.core` 领域规则，不在 `ProtocolEngine` 中硬编码 factorization，不新增 SQLite authority table。需要查询时优先使用已有 Phase 3-5 projection：registry、execution request/submission、verification、canonical outputs、split invocation、proposal、merge plan、merge task link、merge record、expected output resolution、contribution、settlement。

## 6. Schema version 策略

| 对象 / Artifact | `schema_version` |
|---|---|
| Root input artifact | `factorization.root_input.v1` |
| `FactorIntegerSubject` | `factorization.factor_integer_subject.v1` |
| `CandidateRangePartitionParams` | `factorization.candidate_range_partition_params.v1` |
| `CandidateRangeCoverageProof` | `factorization.candidate_range_coverage_proof.v1` |
| `FactorSearchRangeInput` | `factorization.factor_search_range_input.v1` |
| `FactorSearchInstruction` | `factorization.factor_search_instruction.v1` |
| `RangeResult` | `factorization.range_result.v1` |
| `FactorizationMergeResult` | `factorization.merge_result.v1` |
| `PrimeFactorizationResult` | `factorization.prime_factorization_result.v1` |
| Fixture case manifest | `factorization.fixture_case.v1` |

策略：

- artifact body 内使用完整 `schema_version`。
- `ArtifactRef.artifact_schema_id` 使用去掉版本的稳定 id，例如 `factorization.range_result`；`artifact_schema_version` 使用 `v1`。
- 所有 digest 使用 canonical JSON：`ensure_ascii=False`、`sort_keys=True`、紧凑 separator。
- `target_n`、`range_start`、`range_end`、`factor`、`cofactor` 和 prime factors 均以十进制字符串保存，避免 JSON number 精度语义不清。

## 7. 插件 descriptor 契约

固定插件身份：

```text
plugin_id: factorization
plugin_version: 0.1.0
```

`PluginDescriptor.supported_task_types`：

```text
root
factor_integer
factor_search_range
factorization_merge
```

其中 `root` 是现有 `TaskUnit.create_root()` 的兼容 unit type；插件通过 root input 和 `TaskSpec.split_strategy_id` 把它解释为 `factor_integer` 的根目标。后续 child / recursive unit 使用 `factor_integer` 或 `factor_search_range`。

`SplitStrategyContract`：

| 字段 | 值 |
|---|---|
| `split_strategy_id` | `factorization.candidate_range_partition.v1` |
| `allowed_unit_types` | `factor_search_range` |
| `validator_policy_id` | `factorization.range_result.validator.v1` |
| `merge_policy_id` | `factorization.all_required_range_merge.v1` |
| `max_children_per_expansion` | 由 strategy params 和 `ProtocolConfig.max_children_per_unit` 双重限制 |
| `durable_subgoal_policy` | 只允许 bounded range search 晋升为 child TaskUnit |
| `candidate_artifact_policy` | 必须保存结构化 `range_result`，raw text 只能作为 submission / log artifact |

`OutputContract`：

| contract id | unit type | required outputs |
|---|---|---|
| `factorization.factor_integer_subject.contract.v1` | `root` / `factor_integer` | `factor_integer_subject` |
| `factorization.range_result.contract.v1` | `factor_search_range` | `range_result` |
| `factorization.merge_result.contract.v1` | `factorization_merge` | `factorization_result` |

`execution_contracts` 至少声明：

- `deterministic_local`: 用于 fixture 和 merge policy helper。
- `mock_ai_bounded_search`: 允许 mock AI 返回结构化 `range_result`，但 verifier 必须 deterministic recheck。
- `environment_policy`: 本地 Python runtime，固定 seed 可为空，禁止访问网络作为第一版要求。

## 8. 对象字段

### 8.1 `RootInput`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.root_input.v1`。 |
| `target_n` | string | 是 | 十进制整数，第一版要求 `target_n >= 2`。 |
| `requested_output` | string | 是 | 第一版固定 `prime_factorization_result`。 |
| `case_label` | string | 否 | fixture / benchmark 标签，例如 `semiprime_21`。 |
| `input_digest` | string | 是 | root input canonical digest。 |

### 8.2 `FactorIntegerSubject`

`FactorIntegerSubject` 是 `factor_integer` unit 的 canonical output，用于驱动 split strategy。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.factor_integer_subject.v1`。 |
| `subject_id` | string | 是 | 建议 `factor_subject:{task_id}:{unit_id}:{target_n_digest}`。 |
| `task_id` | string | 是 | root task。 |
| `unit_id` | string | 是 | 当前 `factor_integer` unit。 |
| `target_n` | string | 是 | 目标整数。 |
| `target_n_digest` | string | 是 | `target_n` canonical digest。 |
| `source_kind` | string | 是 | `root_input`、`recursive_factor` 或 `merge_output`。 |
| `source_ref` | object | 是 | root input 或上游 artifact ref 摘要。 |
| `requested_output` | string | 是 | 第一版固定 `prime_factorization_result`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

约束：

- `target_n` 必须是无前导零的十进制整数，值大于等于 2。
- `source_ref` 只做 provenance，不是 output resolution authority。
- `FactorIntegerSubject` 可以由 deterministic executor 或 fixture executor 产生，再经普通 verification / canonical binding 成为 split strategy 输入。

### 8.3 `CandidateRangePartitionParams`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.candidate_range_partition_params.v1`。 |
| `strategy_id` | string | 是 | `factorization.candidate_range_partition.v1`。 |
| `target_n` | string | 是 | 目标整数。 |
| `min_divisor` | string | 是 | 第一版默认 `2`。 |
| `max_divisor` | string | 是 | 默认 `floor_sqrt(target_n)`。 |
| `requested_child_count` | integer | 是 | 调用方请求 range 数。 |
| `actual_child_count` | integer | 是 | 实际生成的非空 range 数。 |
| `range_policy` | string | 是 | 第一版固定 `contiguous`。 |
| `small_prime_precheck` | object | 是 | 小素数 / 空 domain 预检摘要；不能替代 range coverage 事实。 |
| `params_digest` | string | 是 | 排除自引用后 canonical digest。 |

约束：

- `requested_child_count >= 1`。
- `actual_child_count <= requested_child_count` 且 `actual_child_count <= max_children_per_unit`。
- 对非空 domain，`actual_child_count >= 1` 且每个 range 非空。
- 如果 `max_divisor < min_divisor`，strategy 只能返回 direct complete 或 invalid result，不能生成空 child。

### 8.4 `CandidateRangeCoverageProof`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.candidate_range_coverage_proof.v1`。 |
| `coverage_id` | string | 是 | 建议 `coverage:{target_n_digest}:{params_digest}`。 |
| `target_n` | string | 是 | 目标整数。 |
| `domain_start` | string | 是 | 第一版通常为 `2`。 |
| `domain_end` | string | 是 | `floor_sqrt(target_n)`。 |
| `range_count` | integer | 是 | range 数。 |
| `ranges_digest` | string | 是 | range 列表 canonical digest。 |
| `no_gap` | boolean | 是 | 必须为 true。 |
| `no_overlap` | boolean | 是 | 必须为 true。 |
| `full_domain_covered` | boolean | 是 | 必须为 true。 |
| `sqrt_bound_checked` | boolean | 是 | 必须为 true。 |
| `created_by_strategy_id` | string | 是 | `factorization.candidate_range_partition.v1`。 |

该对象内联进入 `DecompositionProposal.promotion_guard_evidence.factorization_coverage`，也可以另存 artifact 供长诊断使用。第一版无需单独协议 event。

### 8.5 `FactorSearchRangeInput`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.factor_search_range_input.v1`。 |
| `target_n` | string | 是 | 目标整数。 |
| `range_start` | string | 是 | 当前范围起点，包含。 |
| `range_end` | string | 是 | 当前范围终点，包含。 |
| `coverage_id` | string | 是 | 本次分区 coverage id。 |
| `child_index` | integer | 是 | 从 0 开始的稳定序号。 |
| `child_count` | integer | 是 | 本次 partition 实际 child 数。 |
| `partition_params_digest` | string | 是 | split params digest。 |
| `range_digest` | string | 是 | 当前 range body digest。 |

约束：

- `2 <= range_start <= range_end <= floor_sqrt(target_n)`。
- 同一 `coverage_id` 内 `(child_index, range_start, range_end)` 唯一。
- child `TaskUnit.plugin_payload` 可以携带该输入摘要，但完整输入必须作为 artifact 或 `input_bindings.constant` 进入 proposal。

### 8.6 `FactorSearchInstruction`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.factor_search_instruction.v1`。 |
| `instruction_id` | string | 是 | 建议 `factor_search_instruction:{request_id}`。 |
| `request_id` | string | 是 | `ExecutionRequest.request_id`。 |
| `unit_id` | string | 是 | range child unit。 |
| `target_n` | string | 是 | 目标整数。 |
| `range_start` | string | 是 | 起点。 |
| `range_end` | string | 是 | 终点。 |
| `output_schema_version` | string | 是 | `factorization.range_result.v1`。 |
| `allowed_result_kinds` | list[string] | 是 | `found_factor`、`no_factor_in_range`。 |
| `determinism_requirement` | string | 是 | 第一版固定 `range_recheckable`。 |

该 instruction 是 executor 输入提示，不是协议状态来源；权威事实仍来自 request/submission/verification/canonical events。

### 8.7 `RangeResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.range_result.v1`。 |
| `range_result_id` | string | 是 | 建议 `range_result:{unit_id}:{attempt_id}:{coverage_id}:{child_index}`。 |
| `result_kind` | string | 是 | `found_factor` 或 `no_factor_in_range`。 |
| `target_n` | string | 是 | 目标整数。 |
| `range_start` | string | 是 | 起点。 |
| `range_end` | string | 是 | 终点。 |
| `coverage_id` | string | 是 | coverage id。 |
| `child_index` | integer | 是 | range 序号。 |
| `partition_params_digest` | string | 是 | params digest。 |
| `found_factor` | string/null | 条件 | `found_factor` 时必填。 |
| `cofactor` | string/null | 条件 | `found_factor` 时填 `target_n / found_factor`。 |
| `checked_divisor_count` | integer | 是 | executor 声称检查的 divisor 数。 |
| `executor_summary` | object | 是 | 简短可审计摘要；不包含 hidden reasoning。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

约束：

- `found_factor` 时必须满足 `1 < found_factor < target_n`、在 range 内、且整除 `target_n`。
- `no_factor_in_range` 时 `found_factor` 和 `cofactor` 必须为 null。
- verifier 必须读取 child input 或 request snapshot 校验 `target_n`、range、`coverage_id`、`partition_params_digest` 一致。
- 第一版 verifier 对 `no_factor_in_range` 进行 deterministic brute-force recheck。

### 8.8 `FactorizationMergeResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.merge_result.v1`。 |
| `merge_result_id` | string | 是 | 建议 `factorization_merge:{merge_plan_id}:{merge_unit_id}`。 |
| `target_n` | string | 是 | 目标整数。 |
| `coverage_id` | string | 是 | coverage id。 |
| `partition_params_digest` | string | 是 | params digest。 |
| `result_kind` | string | 是 | `prime_certificate`、`prime_factorization_result` 或 `nontrivial_factor_found`。 |
| `range_result_count` | integer | 是 | consumed required slots 数。 |
| `required_slot_count` | integer | 是 | `MergePlan.required_slots` 数。 |
| `coverage_digest` | string | 是 | range coverage digest。 |
| `slot_result_digests` | list[string] | 是 | required slot canonical output digest，按 slot key 排序。 |
| `found_factor` | string/null | 条件 | `nontrivial_factor_found` 或 semiprime result 时填写。 |
| `cofactor` | string/null | 条件 | 与 `found_factor` 配对。 |
| `prime_factorization_ref` | object/null | 条件 | 可解析最终结果时指向 `PrimeFactorizationResult` artifact。 |
| `limitation_reason` | string/null | 否 | 例如 `composite_cofactor_requires_future_recursive_resolution`。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

第一版只有 `prime_certificate` 和 `prime_factorization_result` 可以映射为 parent required output。`nontrivial_factor_found` 是可审计中间事实，除 semiprime fixture 中能同时证明两个因子为 prime，否则不得通过 `ExpectedOutputResolution` 假装成为最终结果。

### 8.9 `PrimeFactorizationResult`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | `factorization.prime_factorization_result.v1`。 |
| `result_id` | string | 是 | 建议 `prime_factorization:{target_n_digest}:{factor_multiset_digest}`。 |
| `target_n` | string | 是 | 目标整数。 |
| `prime_factors` | list[object] | 是 | 每项含 `prime` 和 `exponent`。 |
| `factor_multiset_digest` | string | 是 | 按 prime 升序后的 factors digest。 |
| `product_check_passed` | boolean | 是 | 必须为 true。 |
| `primality_check_policy_id` | string | 是 | 第一版 `factorization.trial_division_primality.v1`。 |
| `source_kind` | string | 是 | `prime_certificate` 或 `semiprime_merge`。 |
| `source_merge_result_id` | string/null | 否 | merge path 时填写。 |
| `created_at` | string | 是 | UTC ISO 8601。 |

约束：

- `prime_factors` 必须按数值升序。
- 每个 `prime` 必须通过第一版 deterministic primality check。
- `product(prime ** exponent) == target_n`。

## 9. `DecompositionProposal` 映射

`proposal_header`：

- `plugin_id = factorization`
- `plugin_version = 0.1.0`
- `split_strategy_id = factorization.candidate_range_partition.v1`
- `split_strategy_params_digest = CandidateRangePartitionParams.params_digest`

`child_specs` 每个 range 一项：

```text
child_logical_key: range:{coverage_id}:{child_index}
unit_type: factor_search_range
input_bindings:
  range_input:
    kind: constant
    schema_version: factorization.factor_search_range_input.v1
    body: FactorSearchRangeInput
required_outputs:
  - range_result
output_contract_refs:
  range_result:
    schema_version: factorization.range_result.v1
validator_policy_id: factorization.range_result.validator.v1
required_capabilities:
  executor_type: deterministic_local or mock_ai
  bounded_factor_search: true
plugin_payload:
  range_digest
  coverage_id
  child_index
```

`dependency_edges`：第一版为空，所有 range 可并行。

`expected_outputs`：

```text
output_name: prime_factorization_result
resolution_kind: merge_plan_output
merge_slot_id: all_ranges
required: true
schema_ref: factorization.prime_factorization_result.v1
```

`merge_slots`：

- 每个 range child 一项 required slot。
- `child_output_name = range_result`。
- `missing_policy = block_merge`。

`promotion_guard_evidence` 除 Phase 4 通用字段外，增加 `factorization_coverage`：

```text
factorization_coverage:
  schema_version: factorization.candidate_range_coverage_proof.v1
  coverage_id
  domain_start
  domain_end
  range_count
  ranges_digest
  no_gap: true
  no_overlap: true
  full_domain_covered: true
  sqrt_bound_checked: true
```

## 10. `MergePlan` 映射

`merge_policy_ref`：

```text
plugin_id: factorization
plugin_version: 0.1.0
merge_policy_id: factorization.all_required_range_merge.v1
merge_policy_version: v1
merge_policy_descriptor_digest: plugin_descriptor_digest
merge_policy_params_digest: CandidateRangePartitionParams.params_digest
```

`required_slots`：

- `slot_key = range:{coverage_id}:{child_index}:range_result`
- `source_child_logical_key` 对应 range child。
- `source_output_name = range_result`。
- `required = true`。
- `missing_policy = block_merge`。

`parent_output_mapping`：

- 第一版只映射 `prime_factorization_result`。
- 如果 merge output 是 `nontrivial_factor_found` 且不能证明已得到完整 prime factorization，merge policy 不得生成 `ExpectedOutputResolution`。

`merge_validation_requirements`：

```text
all_required_slots_canonical: true
slot_schema_check_required: true
merged_output_schema_check_required: true
plugin_merge_validator_policy_id: factorization.merge_result.validator.v1
factorization_coverage_check_required: true
coverage_id_consistency_required: true
```

现有 `MergePlan` dataclass 只要求前三个 boolean 和 `plugin_merge_validator_policy_id`。额外 factorization 字段放入 `plugin_payload.plugin_defined_body.validation_requirements`，由插件 merge policy 校验，不要求协议核心理解。

## 11. 验证规则

### 11.1 `FactorIntegerSubject` verifier

- 校验 root input artifact 存在且 digest 正确。
- 校验 `target_n` 是合法十进制整数且 `target_n >= 2`。
- 校验 `requested_output = prime_factorization_result`。
- 校验 `source_ref` 与 root input 或上游 artifact 摘要一致。

### 11.2 `RangeResult` verifier

`found_factor`：

- `found_factor` 是十进制整数。
- `1 < found_factor < target_n`。
- `range_start <= found_factor <= range_end`。
- `target_n % found_factor == 0`。
- `cofactor == target_n / found_factor`。
- output 中 target/range/coverage/params digest 与 child input 一致。

`no_factor_in_range`：

- output 中不得携带 `found_factor` 或 `cofactor`。
- verifier 对 `[range_start, range_end]` 进行 deterministic recheck。
- 如果存在任一 divisor 整除 `target_n`，verification 必须 rejected，failure kind 为 `invalid_output`。

### 11.3 Merge result verifier

- 所有 required slots 都来自 child canonical outputs。
- required slot 数量等于 coverage range 数量。
- 所有 `coverage_id` 和 `partition_params_digest` 一致。
- ranges 完整覆盖 `[2, floor_sqrt(N)]`。
- 如果存在 verified factor，选择数值最小的 verified factor 作为 deterministic merge factor。
- 如果没有 factor 且 coverage 完整，生成 `prime_certificate`。
- 如果 factor 与 cofactor 都通过 deterministic primality check，生成 `prime_factorization_result`。
- 如果 factor 或 cofactor 仍为 composite，生成 `nontrivial_factor_found`，但第一版不得把它解析为 final parent output。

## 12. Completion / expansion 策略

`candidate_range_partition.v1` 的 action 选择：

| 情况 | action | 说明 |
|---|---|---|
| `target_n` 非法 | `invalid_result` invocation | 不写 proposal、decision 或图事件。 |
| `target_n` 为 2 或 3 | `complete` | direct complete，`completed_output_refs` 指向 `PrimeFactorizationResult([N])`。 |
| candidate domain 非空 | `expand` | 生成 range children 和 all-required merge plan。 |

第一版不在 split strategy 内对任意大 `target_n` 做完整 primality proof。对 prime fixture 的证明通过 range children 全部 `no_factor_in_range` 加 merge policy 完成。

## 13. Replay 与 artifact 边界

- 插件 split、verification、merge 都必须在原始运行时产生 artifact body 和 digest；replay 不重新调用插件逻辑补事实。
- 本规格不新增 event type。权威事实进入以下已有 events：
  - `REGISTRY_SNAPSHOT_RECORDED`
  - `EXECUTION_REQUEST_RECORDED`
  - `EXECUTION_SUBMISSION_RECORDED`
  - `VERIFICATION_RECORDED`
  - `CANONICAL_OUTPUTS_BOUND`
  - `SPLIT_STRATEGY_INVOCATION_RECORDED`
  - `DECOMPOSITION_PROPOSAL_RECORDED`
  - `EXPANSION_DECISION_RECORDED`
  - `MERGE_PLAN_RECORDED`
  - `TASK_EXPANDED`
  - `MERGE_TASK_LINK_RECORDED`
  - `MERGE_RECORDED`
  - `EXPECTED_OUTPUT_RESOLVED`
  - `CONTRIBUTION_STATE_CHANGED`
  - `SETTLEMENT_RECORDED`
- SQLite projection 继续 index-only；Factorization-specific fields 通过 artifact refs、payload JSON 和 existing projection rows 可审计，不作为新的权威表。
- 后续 Replay/Audit feature 可以增加“删除 SQLite 后重放同一 factorization run”的测试；Phase 6 只保证事件和 artifact 已经足够持久化。

## 14. 风险与缓解

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| 把 AI 输出当成拆分依据 | 破坏插件主导和 replay | 中 | split strategy 只消费 canonical subject；测试禁止 proposal 中出现 AI-authored child plan。 |
| `no_factor_in_range` 不可验证 | prime 结果不可信 | 中 | 第一版 deterministic brute-force recheck；fixture 范围控制在本地预算内。 |
| all-required merge 被误认为 early success | 与主 TDD 第 14.1 节冲突 | 高 | 文档和测试明确 early success/pruning 未实现；feature evidence 不宣称该能力。 |
| composite cofactor 被误解析成最终分解 | 输出语义错误 | 中 | merge policy 只有在所有 factors 均 prime 且 product check 通过时生成 `prime_factorization_result`。 |
| JSON 大整数精度不清 | verifier / executor 行为不一致 | 低 | 所有整数用 decimal string；模型构造时统一解析校验。 |
| 插件字段泄漏协议 authority | replay / projection 不一致 | 中 | `plugin_payload` 只放摘要和私有配置，不放 resolution status、canonical refs 或 state。 |

## 15. TDD 计划

### Task 1: schemas、pure models 和 descriptor

文件：

- `src/tokenshare/plugins/factorization/schemas.py`
- `src/tokenshare/plugins/factorization/models.py`
- `src/tokenshare/plugins/factorization/descriptor.py`
- `tests/plugins/factorization/test_factorization_schemas.py`

红灯测试：

1. `test_factorization_descriptor_declares_unit_types_contracts_and_policies`
2. `test_factor_integer_subject_rejects_invalid_decimal_integer`
3. `test_range_result_requires_found_factor_fields_only_for_found_factor`
4. `test_prime_factorization_result_requires_prime_factors_product_check`

绿灯要求：

- descriptor digest 稳定。
- schema version 全部显式。
- 大整数全部 decimal string。

### Task 2: candidate range partition

文件：

- `src/tokenshare/plugins/factorization/split_strategy.py`
- `tests/plugins/factorization/test_candidate_range_partition.py`

红灯测试：

1. `test_candidate_range_partition_covers_domain_without_gap_or_overlap`
2. `test_candidate_range_partition_is_deterministic_for_same_input`
3. `test_candidate_range_partition_respects_max_children_per_unit`
4. `test_candidate_range_partition_uses_non_empty_ranges`

绿灯要求：

- `coverage_id`、`ranges_digest`、`params_digest` 稳定。
- `actual_child_count` 对小 domain 自动收缩。

### Task 3: execution instruction and parser

文件：

- `src/tokenshare/plugins/factorization/validator.py`
- `tests/plugins/factorization/test_factorization_parser.py`

红灯测试：

1. `test_parse_range_result_accepts_structured_found_factor`
2. `test_parse_range_result_rejects_freeform_factor_claim`
3. `test_build_factor_search_instruction_contains_bounded_range_only`

绿灯要求：

- raw text 不成为 candidate output。
- parser 只输出 `RangeResult` 或 parse failure artifact body。

### Task 4: range verifier

文件：

- `src/tokenshare/plugins/factorization/validator.py`
- `tests/plugins/factorization/test_factorization_verifier.py`

红灯测试：

1. `test_found_factor_verifier_rejects_factor_outside_range`
2. `test_found_factor_verifier_rejects_non_divisor`
3. `test_range_verifier_rejects_target_or_coverage_mismatch`
4. `test_no_factor_verifier_rechecks_range_and_rejects_false_claim`

绿灯要求：

- verifier 返回可进入 `VerificationReport` 的 deterministic layer summary。
- rejected 输出不进入 canonical。

### Task 5: proposal and merge plan generation

文件：

- `src/tokenshare/plugins/factorization/split_strategy.py`
- `tests/plugins/factorization/test_factorization_split_strategy.py`

红灯测试：

1. `test_factorization_split_generates_only_factor_search_range_children`
2. `test_factorization_proposal_records_coverage_proof`
3. `test_factorization_merge_slots_match_children_one_to_one`
4. `test_factorization_proposal_contains_no_authoritative_resolution_in_plugin_payload`

绿灯要求：

- 生成的 `DecompositionProposal` 可被 Phase 4 `DecompositionProposal` dataclass 接受。
- 生成的 `MergePlan` 可被 Phase 4 `MergePlan` dataclass 接受。

### Task 6: all-required merge policy

文件：

- `src/tokenshare/plugins/factorization/merge_policy.py`
- `tests/plugins/factorization/test_factorization_merge_policy.py`

红灯测试：

1. `test_merge_policy_outputs_prime_certificate_when_all_ranges_have_no_factor`
2. `test_merge_policy_outputs_prime_factorization_for_semiprime_factor_pair`
3. `test_merge_policy_rejects_missing_or_duplicate_range_slot`
4. `test_merge_policy_does_not_resolve_composite_cofactor_as_final_result`

绿灯要求：

- semiprime fixture 输出 `PrimeFactorizationResult`。
- composite cofactor 输出 `nontrivial_factor_found` 和 limitation reason，但不生成 final result ref。

### Task 7: descriptor registry and no continuation plugin

文件：

- `tests/plugins/factorization/test_factorization_registry.py`

红灯测试：

1. `test_factorization_registry_freeze_includes_single_plugin_descriptor`
2. `test_factorization_descriptor_declares_recursive_policy_without_second_plugin`

绿灯要求：

- `PluginRegistry.freeze()` snapshot 中只有 `factorization@0.1.0` 负责 factorization。
- descriptor metadata 明确 first-slice recursion limitation。

### Task 8: prime fixture end-to-end flow

文件：

- `tests/test_phase6_factorization_flow.py`

红灯测试：

1. `test_factorization_prime_fixture_expands_ranges_merges_prime_certificate_and_settles`

绿灯要求：

- root / factor_integer subject canonical。
- range children all canonical `no_factor_in_range`。
- merge output resolves `prime_factorization_result`。
- parent completion、contribution、root settlement 全部写入现有 Phase 5 events。

### Task 9: semiprime fixture end-to-end flow

文件：

- `tests/test_phase6_factorization_flow.py`

红灯测试：

1. `test_factorization_semiprime_fixture_finds_factor_merges_final_result_and_settles`
2. `test_factorization_semiprime_flow_waits_for_all_required_ranges_before_merge`

绿灯要求：

- executor 只处理 bounded range。
- found factor range 和 no-factor ranges 都经 verifier。
- all-required merge 后输出 `PrimeFactorizationResult([p, q])`。

### Task 10: first-slice limitation tests

文件：

- `tests/test_phase6_factorization_limitations.py`

红灯测试：

1. `test_factorization_does_not_claim_early_success_before_all_required_ranges`
2. `test_factorization_does_not_prune_sibling_ranges_in_first_slice`
3. `test_factorization_composite_cofactor_requires_future_recursive_resolution`

绿灯要求：

- 文档、descriptor metadata 和测试断言一致。
- 当前实现不触发 Phase 5 subtree pruning 来模拟 early success。

### Task 11: code map and status sync

文件：

- `Doc/TechnicalDocument/2026-06-27-phase-6-factorization-plugin-code-map.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

绿灯要求：

- code map 覆盖新增 source / tests / 本规格章节。
- 状态 evidence 记录 targeted tests 和 `.\init.ps1` 输出。
- 未实现 Lean stub、structured report stub、feat-008 AI API executor、feat-009 infrastructure。

## 16. 自审清单

- 本规格只覆盖一个 feature 子范围：Factorization 插件第一版。
- 没有把 factorization 数学规则写入 `tokenshare.core`。
- 没有新增 event type 或 SQLite authority table。
- 没有把 AI / executor 输出作为 expansion proposal authority。
- 没有宣称 early success、sibling pruning 或完整 composite cofactor recursion 已实现。
- 每个 artifact / object 都有显式 schema version。
- TDD 任务可从红灯开始，并能复用现有 Phase 3-5 protocol flow。
