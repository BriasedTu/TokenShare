---
status: user_approved
document: slim_v2_system_integration_contract
scope: Minimal integration between Slim V2 and the existing TokenShare runtime
---

# Slim V2 系统对外接线合同

本文只回答一个问题：Slim V2 怎样以最简单、稳定的方式调用现有 TokenShare 系统本体？本文依据当前代码公共接口，不从旧 code map 猜测行为，也不提供可运行实现。用户已冻结：Experiment 1/5 使用真实 transport；Experiment 1 每个实际执行的 AI unit 保存最多三次自然 attempts 的 per-unit trace；Experiment 2–4 只按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 复用该 trace，不额外取得回答；Experiment 2/3 不再包含在线检查。

## 1. 系统边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Slim V2 runner | 枚举 Experiment 1–5 condition/root；创建每 root 独占的运行对象；为 Experiment 2–4 按冻结三元键选择 Experiment 1 回答；选择 worker/fault/mode/model；捕获异常；把一个固定分母 root 写成一行普通 JSONL | 不实现协议状态机，不替插件判断领域正确性 |
| `ProtocolEngine` | 注册 task/unit、lease/attempt、submission、verification、canonical、recovery、merge 等协议状态推进并写 event ledger | 不选择论文实验参数，不计算论文指标 |
| `ProtocolRunCoordinator` | 调用插件、worker backend 和 engine，驱动一个 root 到终态；返回 `ProtocolRunResult` 与 runtime observation | 不枚举实验矩阵，不生成最终 CSV/JSON 指标 |
| Factorization / Lean runtime adapter | 构造 root plan、确定性 split、AI request、领域 parser/verifier/checker、merge 与 root 正确性事实 | 不选择 provider cohort、worker 档位或论文 grouping |
| Slim real-provider caller + execution bridge | 仅在 Experiment 1 正式运行和 Experiment 5 中读取一个已冻结 provider entry，构造并发出真实请求，返回 raw response/failure、parse 结果、usage、provider request start、latency 与 model identity；小 bridge 再转换为系统 `ExecutionSubmission` | 不要求采用旧 `AIAPIExecutor` 整类或旧 selector，不为 Experiment 2–4 发出请求，不做领域正确性判定、价格选择或实验汇总 |
| Slim-local fixed-response executor | 从 Experiment 1 普通 per-unit trace JSONL 以 `case_id × source_repeat_id=0 × planned_ai_unit_id` 精确查找，核对普通 unit 语义字段；当前 ordinal 存在则精确读取，否则读取最后一个已有自然 attempt，并返回其 raw/result/usage/latency | 不联网，不按 condition 或实验 repeat 改写回答，不依赖旧 response-bank authority；ordinal fallback 不得补调用或改写 source 内容 |
| Slim V2 pricing projector | 使用指标权威第 1.4 节的普通静态价格表，把 provider 原始 usage、call start 和 model 映射成 `cost_estimate_cny/pricing_version/pricing_tier` | 不做预算、余额检查、审批、receipt、gate 或动态联网查价 |
| Slim V2 metrics reducer | 只枚举 `TokenShareData/outputs/slim_v2/<run_id>/` 的普通 JSONL，按第一份文档公式生成 CSV/JSON | 不重新运行协议、插件、checker 或 provider；不从旧实验目录补数据，不在报告阶段改写冻结价格版本 |

每个 root 必须创建新的 plugin runtime、bridge、worker backend、engine/coordinator 组合。不得跨 roots 复用带可变缓存的 `FactorizationRuntimeAdapter` 或 `LeanRuntimeAdapter`。

runner 必须串行执行 roots：上一个 root 写完 terminal 行之后，才能让下一个 root 进入协议生命周期。root 内部仍按 condition 的 `worker_count` 并行执行 AI units。

## 2. 推荐唯一入口

### 2.1 构造关系

Slim V2 对一个 root 的唯一入口是：

1. 在该 run 的普通输出目录下创建 `ArtifactStore` 与 `EventLedger`。
2. 用同一 `ProtocolConfig`、store 和 ledger 创建 `ProtocolEngine`。
3. 创建该 root 独占的 plugin runtime 与执行 bridge。
4. 根据 condition 创建 `SequentialWorkerBackend`、`ThreadWorkerBackend` 或 `ProcessWorkerBackend`；除 worker death 必须使用可终止进程外，Thread/Process 的选择由实施 Agent 用 focused tests 证明，不在本文预选。
5. 创建 `ProtocolRunCoordinator(engine, artifact_store, event_ledger, now, observation_clock)`。
6. 构造 `ProtocolRunRequest` 并只调用一次 `ProtocolRunCoordinator.run_root(request)`。
7. 立即从result、同一ledger/store、plugin runtime与hooks提取正常协议原始字段；若是Experiment 1，先把本root每个实际执行AI unit的普通语义输入和最多三个自然attempt归并为`trace_origin=protocol` per-unit trace。只有第6.2节由同profile Exp2–4 consumer closure选中的来源root才执行coverage tail，并为每个unscheduled planned unit写唯一`trace_origin=coverage_tail` trace；非来源root不写未调度unit trace。
8. 来源root的coverage tail完成或按attempt上限终止后写tail summary再追加root JSONL；非来源root直接写`not_required_by_downstream`、空tail IDs和零tail资源的完整root结果。两类root都保留完整协议字段，随后才开始下一root。

### 2.2 `ProtocolRunRequest` 构造要求

当前稳定字段为：

- `run_id`：Slim V2 生成的 root-run 唯一 ID；必须可反查 `experiment_id/condition_id/case_id/repeat_id`。
- `root_input`：原始 catalog case object，不把实验 condition 混入插件输入。
- `plugin_runtime`：该 root 独占的 `FactorizationRuntimeAdapter` 或 `LeanRuntimeAdapter`。
- `worker_backend`：持有对应 execution bridge。
- `mechanism_policy`：默认 `ProtocolMechanismPolicy()` 即 FULL；Experiment 4 根据指标权威的 11 个 modes 关闭零个、一个或两个布尔机制，六个双机制 mode 必须在同一次 root run 中同时关闭对应两项。
- `hooks`：FULL 使用 `NoOpRuntimeHooks`；Experiment 3/4 使用 Slim V2 自己的窄 hooks。
- `continue_after_terminal_child_failure`：按实验设计显式设置；不得依靠默认值隐藏失败。
- `execution_scope`：Experiment 1–5 全部使用默认 `whole_root`；Slim V2 已取消在线检查，不创建 selected-unit condition。
- `trace_delay_policy` 与 `logical_scheduler`：Experiment 1/5 当次真实 provider 运行使用 `online_real_time` 且 scheduler 必须为 `None`；Experiment 2–4 固定回答配对运行使用 `logical_source_latency_1x` 与 `LogicalSourceLatencyScheduler`。

违反 timing-policy 配对、worker capacity 非正数、capacity>1 但 backend 无 `execute_batch()` 等配置错误，会在进入正常 root 生命周期前抛出异常，属于接线错误。

### 2.3 `ProtocolRunResult` 的稳定字段

- `run_id`、`task_id`、`root_unit_id`。
- `status`：由 root unit state 小写投影，例如 `completed`、`failed`；selected scope 可为 `partial`。
- `event_refs`：该 task 的事件 ID、序号、类型引用，不含完整 payload。
- `artifact_refs`：result 投影发现并校验过的 artifact refs；不是按业务角色命名的 map。
- `summary`：包含 event/artifact 数、unit/attempt state counts、`units[]`、`attempts[]`，以及 `runtime_observation`；有 hooks 时包含 `runtime_hook_observations`。
- 正常候选取得失败耗尽时，`summary["terminal_failure"]` 固定为 `{"failure_stage": <stage>, "failure_origin": <origin>, "infrastructure_invalid": false}`。`origin` 只用 `model_parse_exhausted`、`model_verification_exhausted`、`provider_transport_exhausted` 或 `mixed_candidate_acquisition_failure`；Factorization 与 Lean 共享这一结构化终态，不抛普通 retry-limit 异常。
- Lean checker 在运行中返回 `environment_error`、`timeout` 或 `helper_error` 时，`summary["terminal_failure"]` 使用 `failure_stage="candidate_verification"`、`failure_origin="checker_environment_error"`、`infrastructure_invalid=true`，并立即停止当前 root，不创建下一模型 retry。
- `ledger_binding`：系统返回的运行绑定字段。Slim V2 不把它用于任何指标，也不围绕它增加门禁。

`ProtocolRunResult.status == "completed"` 只表示协议 root 完成，不能单独等同于论文“正确”。正确性必须读取第 4、5 节所述领域结果。

Slim projector 只把失败映射到冻结顶层 `failure_kind=no_final/incorrect_final/infrastructure_invalid`，并把上述细因复制到 nullable `failure_origin`；成功 root 的 `failure_origin=null`。condition fail-stop 与未知 runtime 异常的原细分原因也只能进入 `failure_origin`，不能成为新的顶层 `failure_kind`。

### 2.4 细粒度数据从哪里取

- `runtime_observation`：`result.summary["runtime_observation"]`。
- `units`、`attempts`：`result.summary["units"]`、`result.summary["attempts"]`，只含 ID/state/canonical artifact IDs 的摘要。
- `runtime_hook_observations`：`result.summary.get("runtime_hook_observations", [])`。
- 完整 submission、verification、recovery、canonical 与 merge payload：对 runner 已持有的同一 `EventLedger` 调用 `read_all()`，只选择 `event.task_id == result.task_id` 的 events。
- 完整 `ExecutionSubmission`：从 `EXECUTION_SUBMISSION_RECORDED.payload.submission_ref` 构造 `ArtifactRef`，再由同一 `ArtifactStore.read_bytes()` 读取 JSON。这里可以获得 raw/parsed/candidate refs、`usage_summary`、`error`、`result_kind`。
- provider 尝试与 latency：读取 submission 的 `provenance_ref` 所指 provider-call record 的 `attempts[]`；raw response 从 `raw_output_ref`；parse failure 从 `parse_failure_ref`。
- verification/checker：通用状态来自 `VERIFICATION_RECORDED.payload.verification_report`；Lean 的 checker 细节来自 `LeanRuntimeAdapter.checker_report_for_request()` 与 merge result 的 root checker report。

因此，不能只序列化 `ProtocolRunResult` 就宣称原始数据闭合；但不需要访问任何旧实验模块。

## 3. Factorization 接线

### 3.1 root input

`FactorizationRuntimeAdapter` 接受 catalog case object，至少满足：

- `schema_version="tokenshare.paper_factorization_case.v1"`；
- `case_id`、`target_n`、`candidate_start`、`candidate_end`、`split_params`；
- `split_params.strategy_id` 为 candidate-range partition；Experiment 2 必须与同一 case 的 Experiment 1 值完全相同；
- `candidate_start=2`，`candidate_end=isqrt(target_n)`，覆盖完整候选域；
- Experiment 2 不使用独立 split profile，必须完整继承同一 Experiment 1 case 的 `split_params`、子任务范围、`planned_ai_unit_id`、prompt 与依赖关系；其唯一实验变化是 `worker_count`。

### 3.2 runtime、split 与 AI request

- 构造 `FactorizationRuntimeAdapter(provider_family, seed, protocol_config, executor_requirements, max_tokens, timeout_seconds, lifecycle_clock)`。
- `plan_root()` 先创建确定性 root unit；root canonical 后由插件的 candidate-range strategy 生成 child ranges 和 merge plan。
- range unit 的 `build_execution_request()` 保存 `FactorSearchRangeInput`、instruction 与 prompt package；`soft_hints.planned_ai_unit_id` 为稳定的 `range_<child_index>`。
- Experiment 1/5 的 Slim real-provider caller 把返回文本交给 `parse_factorization_ai_output`；parser 是能力合同的一部分，不要求复用旧 executor 外壳。
- `FactorizationExecutionBridge(plugin_runtime=adapter, range_executor=provider_or_trace_bridge)` 只把 range unit 委托给所选执行 bridge；root 和 merge 由本地确定性逻辑执行。Experiment 1/5 的 bridge 使用 `parse_factorization_ai_output`，Experiment 2–4 的 bridge 使用 fixed-response trace。

### 3.3 parser、verifier、merge 与正确性

- parser 将模型文本转成 `RangeResult` candidate；parse 失败返回 `parse_failed`。
- `verify_submission()` 对 range 调用 `verify_range_result`，检查 factor witness 或完整 no-factor range；rejected candidate 进入协议 recovery。
- merge policy 是 factor-witness early completion 或全部 ranges 的 no-factor coverage；merge 生成 `factorization_result`，可解出时同时生成 `prime_factorization`。
- 最终正确性来源不是 provider 文本，而是：root `completed`、最终 `prime_factorization` artifact 可读取、`PrimeFactorizationResult.product_check_passed=true`，并且 reducer 对 `target_n` 做独立确定性结果检查。完成但独立检查错误必须记为 `incorrect_final`。

## 4. Lean 接线

### 4.1 root input 与固定 lemma DAG

正式 Lean case 使用 `schema_version="tokenshare.paper_lean_lemma_graph_case.v1"`，至少包含：

- `case_id`、`paper_difficulty`、`topic_family`；
- `root_theorem_payload`；
- 固定 `lemma_graph.nodes`、`dependency_edges`、`merge_plan_shape`；
- `expected_depth`、`expected_leaf_count`、`expected_ai_unit_count`；
- 与本地固定环境匹配的字段；
- `preflight_status` 不能是 `structured_blocked`。

`LeanFixedDecompositionPlan.from_catalog_case()` 校验图、拓扑、限制和 root/node theorem payload。AI 不决定协议级拆分。

### 4.2 runtime、AI request 与真实 checker

- 构造 `LeanRuntimeAdapter(provider_family, environment_manifest, checker=check_lean_proof, seed, protocol_config, executor_requirements, max_tokens, timeout_seconds, lifecycle_clock)`。
- `plan_root()` 构造 fixed lemma-DAG units；依赖节点只在前置 canonical proof 可用后执行。
- proof unit 的 request 包含 theorem payload、依赖 proof refs、prompt package、固定 environment；`soft_hints` 包含 `planned_ai_unit_id`、`lemma_node_id` 与 `dependency_path`。
- Experiment 1/5 的 Slim real-provider caller 把返回文本交给 `parse_lean_proof_candidate_ai_output`；parser 是能力合同的一部分，不要求复用旧 executor 外壳。
- `LeanExecutionBridge(plugin_runtime=adapter, proof_candidate_executor=provider_or_trace_bridge)` 将 proof units 委托给所选执行 bridge，随后调用 `normalize_proof_submission()`。Experiment 1/5 的 bridge 使用 `parse_lean_proof_candidate_ai_output`，Experiment 3/4 的 bridge 使用 fixed-response trace。
- 每个 proof candidate 必须由真实 `check_lean_proof` 在固定本地 Lean/lake/toolchain/library 环境中检查；只有 `LeanCheckerStatus.ACCEPTED` 的 artifact 才能提升为 canonical proof。

### 4.3 merge、root recheck 与正确性

- merge 按 fixed DAG 的 required slots/topological order 组装 root proof。
- 无论 child verification 是否在 Experiment 4 中关闭，merge policy 都会对组装后的 root theorem 调用真实 checker。
- 正确性唯一来源是 `adapter.merge_result.accepted == true` 且 `adapter.merge_result.root_checker_report.status == ACCEPTED`；普通 `result.status` 不能替代 root recheck。
- child checker rejection、root checker rejection和 checker 环境错误必须分别保存，不能都折叠成“模型错误”。

### 4.4 独立 Lean 环境 pass 与启动轻量校验

- 独立环境测试覆盖冻结 catalog 中全部 checker-backed cases 的全部 lemma nodes，并以 oracle proof 调用真实 checker；预注册 `structured_blocked` cases 只保留库存计数，不冒充 checker pass。测试可构建 Lean project并运行 Lean/lake，但只有全部 nodes 为 `accepted` 时才原子写入 `local/cache/slim_v2/lean_environment_pass.v1.json`，schema 固定为 `tokenshare.slim_v2.lean_environment_pass.v1`。
- pass 文件至少绑定 `status=passed`、catalog/environment 身份、checker-backed 与 structured-blocked case/node 计数、关键输入文件 digests，以及 `TokenShare/LemmaGraphOracle.olean` 与 `TokenShare/LemmaGraphCases.olean` 两个 compiled-object hashes。
- 每次启动 Lean 实验、且在任何 provider 调用之前，只执行轻量只读校验：读取 pass JSON，校验 schema/status/pass digest、当前关键输入 digests、两个 `.olean` 文件存在且 hash 匹配。该启动路径禁止运行 Lean、lake 或任何 subprocess，也不得在 pass 缺失/失效时现场重建环境。
- pass 缺失、不可读或任一绑定失效时，在 provider 前 fail closed：全部 pending Lean ordinary/reference roots 顶层记为 `infrastructure_invalid`、`failure_stage=preflight` 并保存具体 `failure_origin`；同 run 的 Factorization roots 继续，fixed-source closure 排除这些 Lean-invalid keys。修复环境并重新运行独立环境测试生成新 pass 后，才可按原预注册 inventory 重跑。运行中 checker 环境错误则沿用第 2.3 节的 `candidate_verification/checker_environment_error`，二者不得混同。

## 5. AI API 接线

### 5.1 local config 与 secret

- Slim real-provider caller 必须从 gitignored local JSON 读取 secret；可以轻量适配现有 `load_local_ai_api_config()`，但接线合同不要求复用其完整 config/selection/pricing 机制。默认输入位置可沿用 `local/ai_api_smoke.local.json`，或由后续设计冻结 Slim 自己的 gitignored path。
- tracked/safe config 只保存 `api_key_env`。local JSON 若含 `api_key`，Slim loader 必须把它移出 config body并仅注入当前进程环境变量。
- API key 只允许存在于当前进程环境和 transport 调用栈；不得写入 Slim JSONL、raw output、event、artifact metadata、日志或错误 message。
- placeholder key 会使 entry disabled；缺 key 不得自动换模型。

### 5.2 provider entry/model 固定

- Experiment 1/5 的每个 condition 必须在进入 root loop 前解析为恰好一个目标 provider entry；Slim real-provider caller直接接收该 entry，不运行跨 entry 随机选择、资格选择或 hash 选择。
- 为保证每个 condition 精确命中一个 endpoint，Slim V2 使用“单目标 entry view”或等价的单 entry 参数对象。Experiment 2–4 不创建 provider caller，也不调用任何 selector/transport。
- submission/raw provider record必须保存 `provider_family`、`entry_id`、configured/requested/resolved model；resolved model 缺失或不匹配时停止该 condition 尚未执行的 roots，并把已预注册但未执行 roots 写成 infrastructure-invalid 行。

### 5.3 request、response、usage 与 failure

- request 由插件生成 `ExecutionRequest`；AI executor要求 `prompt_package_ref`，并核对 `hard_requirements.provider_family`。
- 成功 raw artifact 包含 provider/entry/model、provider response ID、content、reasoning content、raw response JSON、finish reason 与 provider usage。
- `ExecutionSubmission.usage_summary` 或 Slim projector 的等价普通记录必须保留 provider attempt count、prompt/cache-hit/cache-miss/completion/reasoning/total tokens、cost estimate、currency、usage/cost status。`reasoning_tokens` 是 `completion_tokens` 的明细子集，禁止直接相加。
- provider call record 的每个 attempt 包含 result kind、provider request start UTC、latency、HTTP status、entry/model 与错误摘要。
- provider/transport failure 返回明确的 submission `result_kind`；parse failure返回 `parse_failed`、raw ref 和 parse-failure ref；两者都不是 Python 异常式“无记录失败”。
- Experiment 1 使用 `ProtocolConfig.max_retries=2` 并允许正常 replacement，使同一实际执行 AI unit 的自然 attempts 按 `attempt_ordinal` 保存到一条普通 per-unit trace，最多三个；Experiment 5 仍按指标权威保持每 AI unit 最多一次 provider attempt。

### 5.4 普通价格投影

- 价格版本按实际 attempt 固定：前向 Experiment 1 为 `slim_v2.pricing.2026-08-23` 的 Flash flat 表，Experiment 5 为 `slim_v2.pricing.2026-08-20` 的 SiliconFlow flat 表；数值和 reasoning 归属严格读取指标权威第 1.4 节，不复用旧 config pricing digest 或 budget authority。
- Flash 与 SiliconFlow 均写 `pricing_tier=flat`。Flash 的 `provider_request_started_at_utc` 仍保存为调用事实，但不选择峰/谷档；projector 保存 `pricing_version/pricing_tier/cost_estimate_cny`，reducer只求和。2026-08-23 前 exact run 已写入的 Pro tier/version 只可原样恢复，不能重价。
- DeepSeek 与存在独立 cache 价的 SiliconFlow endpoint 缺 cache hit/miss 分项时 cost 为 `null`；Qwen3-14B 没有独立 cache 价，全部 prompt tokens 按 input 价计算，不能把官网的未列价解释为免费。
- `reasoning_tokens` 只用于诊断拆分，已包含在 `completion_tokens` 中。实际成本与 Exp3 模拟成本都只对完整 completion/output 计一次。
- 前向 Flash 价格来源为用户直接记录 `Doc/SlimV2/slim_v2_flash_pricing_source_20260823.md`；`Doc/SlimV2/slim_v2_official_pricing_sources_20260820.md` 只保留 Experiment 5 与旧 Pro 事实。实现与 run 不联网更新价格。后续若用户批准新价格，只能创建新的普通 `pricing_version`，不能静默改写已经启动的 run。

## 6. 实验场景接线

### 6.1 worker count 与 logical scheduler

- worker=1：`SequentialWorkerBackend`。
- worker>1：协议要求 backend 提供 `execute_batch()`；`ThreadWorkerBackend(capacity=k)` 提供真实线程并发，`ProcessWorkerBackend(capacity=k)` 提供进程隔离和可终止 worker。
- Experiment 2 保留 `worker_count ∈ {1,3,7,10,30,50}`；Experiment 1、3、4、5 固定 `worker_count=10`。
- Experiment 1/5 当次真实运行：`trace_delay_policy="online_real_time"`，不创建 logical scheduler；wall-clock 来自 observation clock 和 worker facts。
- Experiment 2–4 固定回答运行：使用 `trace_delay_policy="logical_source_latency_1x"` 与 `LogicalSourceLatencyScheduler`；该时间只能称为固定回答运行时间。
- 除 worker death 的进程终止要求外，本文不决定 worker>1 使用 Thread 还是 Process。实施 Agent 必须用 Factorization 的真实 k>1 focused tests，加上 Lean adapter 的 fake checker、固定 fixture 或静态合同测试来选择 backend；默认不运行 Lean 专项 suite、LeanAudit、全量 catalog 或 `lake`/`lean` 回归。论文实验中的 Lean roots 仍由真实 checker 执行，但它们是实验样本，不是为了选择 backend 而增加的验证矩阵。未经上述轻量测试不得宣称任一后端是稳定合同。
- `runtime_observation` 直接提供 planned/dispatched/completed/unscheduled IDs、witness in-flight、worker facts、peak concurrency 与 runtime wall-clock。
- root loop 始终串行。`root_start_at_ms` 在 `run_root` 进入 root 协议生命周期、任何 AI unit 调度之前记录；`root_terminal_at_ms` 在 root 完成或失败终止后记录；`runtime_wall_clock_ms` 必须由两者相减。Experiment 1/5 使用真实时钟，Experiment 2–4 使用 logical scheduler 的同一逻辑时钟。worker 首次 `started_at` 只作诊断，不能替代 root start。Experiment 1来源root的coverage tail在terminal之后执行且下一个root尚未开始，非来源root不进入tail；正文跨-root wall-clock使用协议runtime之和，tail wall单列。

### 6.2 Experiment 1 coverage tail 与 Experiment 2–4 固定回答复用

- Experiment 1协议`run_root()`返回后，先把每个实际执行AI unit的普通语义输入与最多三个自然attempt写为`trace_origin=protocol` per-unit trace。Slim-local纯函数从同一冻结profile inventory派生Exp2、Exp3、Exp4（包含它们实际调度reference）消费trace的`case_id`并集；只有该集合的来源root可进入tail。Full当前inventory的该闭包为99，断言来自计算而不是常量；Exp5 V4 supplemental只读committed root result，绝不进入集合。来源root的有效`no_final/incorrect_final`或其他`terminal_failure.infrastructure_invalid=false`仍继续tail；只有`slim_condition_failure`、`slim_runtime_failure`或`terminal_failure.infrastructure_invalid=true`阻断。root start/terminal/runtime已冻结，不得被后续acquisition改写。
- 来源root随即从同一`runtime_observation.unscheduled_ai_unit_ids`生成tail target sequence，并减去已有protocol trace planned IDs。target保持冻结observation原始顺序，不按文本或数字重排；只处理本root，不缓存全局待办，不修改coordinator、plugin readiness、ProtocolEngine、canonical、merge或root result。非来源root跳过target/request产生，保留`unscheduled_ai_unit_ids`和protocol attempts，并写明确`not_required_by_downstream`状态；这一政策不因condition/runtime/infrastructure tail blocker改写为`not_needed`。
- 每个 tail target 使用原 plan 中相同的普通 unit input、prompt、依赖输入、provider entry/model 与 request controls，通过同一个 Slim real-provider caller取得自然 attempts。`attempt_ordinal` 从 0 开始；transport/parse/verifier/checker 未 accepted 时继续，首次 accepted 后停止，最多三个。parser/verifier/checker 只决定 tail 是否继续，不向已终止 root 提交 submission。
- 每个 tail target 写与同题同 key 的唯一 per-unit trace，`trace_origin=coverage_tail`。Lean 依赖 canonical 不可用时保存带原 `lemma_node_id/dependency_path`、`provider_call_made=false` 的 typed `pre_dispatch_failure`；其他 request 构造或 provider 调用失败同样保存明确失败 attempt/result kind，不得伪造回答。tail 完成的定义是全部 target 均已写出至少一个真实 attempt/failure record，而不是全部获得 accepted answer。
- 来源root的coverage tail完成或按上限终止后写普通tail summary，包含target/recorded IDs、真实wall-clock、provider attempt count、tokens、cost与status；调用数和资源只累计`provider_call_made=true` attempts。非来源root的相同RootResult字段固定为`not_required_by_downstream`、空IDs和零资源。来源root最终`RootResultV2.attempts[]`由protocol snapshot attempts加当前root已持久化tail trace attempts，按target原顺序和自然ordinal稳定重建；非来源root只保留protocol projection attempts。fresh/resume只从snapshot/base projection恢复：来源按三元trace key跳过已有unit并补缺失target，非来源不构造provider、不请求secret、不补tail；两者均不创建独立response bank。
- Experiment 1 正文 completion/correctness/timing/token/cost 只读取正常协议阶段。tail 的真实资源单列诊断；`actual_end_to_end_wall_clock_ms=Σ runtime_wall_clock_ms`，不能使用会夹入 root 间 tail 的跨-root `max-min`。
- Experiment 2–4不另行acquisition，也不创建附加repeat或自然trace attempts；它们对每个来源root的全部planned trace维持strict closure，任何缺失均fail-closed，并可同样消费`protocol`与`coverage_tail` traces。
- fixed-response executor 只能用 `case_id × source_repeat_id × planned_ai_unit_id` 精确匹配，且 Experiment 2–4 的 `source_repeat_id` 固定为 0。下游 root 的 `repeat_id`、`condition_id`、worker、fault、mode、attempt ordinal 和 backend 类型都不进入 lookup key。
- lookup 命中后，Factorization 必须逐项核对 `candidate_start/candidate_end`，Lean 必须逐项核对 `lemma_node_id/dependency_path`；只比较普通字段，不使用 hash、digest 或证据链。
- 同一三元键在所有 condition、原 attempt 与 replacement attempt 中指向同一条 per-unit trace。当前协议 ordinal 存在时选择同 ordinal 自然 attempt；不存在时选择该 trace 的最大/最后自然 ordinal。`source_attempt_ordinal` 保存实际选择，`source_attempt_fallback_used` 保存是否回退，`source_trace_origin` 保存 `protocol/coverage_tail`。消费记录可以多条，但选中 source 的 raw/result kind、latency、usage 和 cost 必须保持相同。
- last-attempt fallback 只复用内容，不改变下游当前 `attempt_ordinal`。因此 Experiment 3 replacement 即使复用同一最后回答，也按新的当前 ordinal 生成不同的确定性 token/latency 扰动。
- Experiment 2–4 的 `provider_call_made` 必须始终为 false；若出现 transport attempt，属于接线错误并停止该 condition。
- 缺键、同键多条 trace、trace 没有任何自然 attempt record、键与当前 root/unit 不一致、普通语义字段不一致或来源 provider/model 不符合 Experiment 1 冻结配置，均不得回退到实时 API，也不得换用其他 unit 的回答。只有“当前 ordinal 不存在”允许按上一条回退到同一 trace 的最后自然 attempt。
- Experiment 2 将同一任务计划、同一 trace attempts 与同一 source latency 输入容量分别为 1/3/7/10/30/50 的逻辑调度器；不得用 Experiment 1 总时间除以 worker count 代替实际 logical makespan、并发、利用率与提前停止计算。

### 6.3 Experiment 3 fault

Slim V2 自己实现窄 fault planner、perturbation generator 与 `RuntimeHooks`。对每个 root，以全部 planned first-attempt AI units 为 fault-rate 分母：

```text
target_count = ceil(fault_rate × planned_first_attempt_ai_unit_count)
```

`fault_rate>0` 且存在 planned units 时至少选择一个目标。目标按 `planned_ai_unit_id` 稳定排序后均匀选取；只在 `attempt_ordinal=0` 注入，replacement 不重复注入。

| Fault | 公共边界/指令 |
|---|---|
| `false_positive` | `after_parsed_candidate_persisted` 把 ordinal 0 替换为 schema-valid 但领域错误的 candidate，再让 verifier/checker 实际判断 |
| `false_negative` | verification 前把原本正确的 ordinal 0 candidate 强制送入 rejected 路径；replacement 不再施加该动作 |
| `no_return` | ordinal 0 source trace attempt 的模拟 token/latency 已消费，但不形成 submission；由 lease expiry/recovery 路径处理 |
| `late_submission` | ordinal 0 在 `lease_deadline+1ms` 提交，由 acceptance 规则拒绝，且不能覆盖 replacement |
| `executor_error` | ordinal 0 source trace attempt 的模拟 token/latency 已消费，随后返回明确 `executor_error` |

所有 replacement 使用未施加 fault 变换的 Experiment 1 source 内容。hook observation 只记录“实际做了什么”；intercepted、escaped、recovered 必须分别从 verification、canonical、recovery 与 root 结果派生。

每个 Experiment 3 模拟 attempt 都由 Slim-local perturbation generator 产生指标权威第 4.4 节冻结的 `δ_token` 与 `δ_network`。generator 固定 seed=`20260820`、version=`slim_v2.exp3_perturbation.v1`，身份只含 `case_id/planned_ai_unit_id/experiment_repeat_id/attempt_ordinal`，不得读取 fault type/rate、condition 或 worker。provider 的 `reasoning_tokens` 是 `completion_tokens` 子集；generator 必须先按指标权威归一化为不重叠的 nonreasoning/reasoning 分量，再扰动完整 completion，不能直接计算 `completion_tokens+reasoning_tokens`。逻辑调度器使用 `simulated_latency_ms` 重新推进整个 root；projector 同时保留 source 与 simulated 字段，且把 simulated 资源明确标为非真实 provider usage。模拟 cost 继承 source attempt 的价格版本、峰/谷档和未扰动 prompt/cache 分项，不按逻辑时钟重新选价。

### 6.4 worker death、replacement 与 requeue

- worker death 必须使用 `ProcessWorkerBackend` 与 `WorkerTerminationPolicy`，目标由 `planned_ai_unit_id` 选择。
- policy 固定 `termination_count_target=dead_worker_count`、`kill_point=progress_25/progress_50/progress_75`、`total_planned_ai_unit_count` 与进程 timeout。
- `WorkerExecutionFact` 提供 worker ID、PID、exit code、目标/实际 progress、开始/结束时间和 `worker_terminated` result kind。
- coordinator 在无 submission/executor failure/lease expiry/verification rejection 后记录 recovery；若允许 replacement，创建新 attempt。Slim V2 通过 `RECOVERY_ACTION_RECORDED`、attempt events 与 worker facts建立 original→replacement 链。
- 只出现 recovery decision 而没有新 attempt 时，`replacement_started=false`、`reassigned=false`。

### 6.5 Experiment 4 mechanism policy

| Mode | `ProtocolMechanismPolicy` 中设为 false 的字段 |
|---|---|
| `FULL` | 无；全部保持 true |
| `NO_VERIFICATION` | `verification_enabled` |
| `NO_PARSER_POLICY` | `parser_policy_enabled` |
| `NO_REQUEUE` | `replacement_attempts_allowed` |
| `NO_MERGE_GATE` | `merge_gate_enabled` |
| `NO_VERIFICATION__NO_PARSER_POLICY` | `verification_enabled`,`parser_policy_enabled` |
| `NO_VERIFICATION__NO_REQUEUE` | `verification_enabled`,`replacement_attempts_allowed` |
| `NO_VERIFICATION__NO_MERGE_GATE` | `verification_enabled`,`merge_gate_enabled` |
| `NO_PARSER_POLICY__NO_REQUEUE` | `parser_policy_enabled`,`replacement_attempts_allowed` |
| `NO_PARSER_POLICY__NO_MERGE_GATE` | `parser_policy_enabled`,`merge_gate_enabled` |
| `NO_REQUEUE__NO_MERGE_GATE` | `replacement_attempts_allowed`,`merge_gate_enabled` |

全部 11 个 modes 均设置 `ProtocolConfig.max_retries=1`；`slot_integrity_enabled` 始终为 true。六个双机制 mode 必须真实运行一次同时关闭两项的完整 root 生命周期，不得离线拼接两个单项结果。不得根据 mode 名称直接填充任何 observation 或 count。

policy布尔值只描述配置，不能证明实际机制被删除。结构路由还必须满足：

- P：`FixedTraceSubmissionAdapter` 保存 raw artifact后、调用 `_parse_domain` 前选择 `PARSE/RAW_PASSTHROUGH`；P disabled不得调用domain parser。
- V：Factorization由coordinator在`verify_submission`前跳过；Lean使用Slim-local bridge在`normalize_proof_submission`/child checker前跳过。synthetic verification使用固定bypass validator/verifier provenance，root checker仍独立运行。
- R：真实recovery decision记录且`retry_allowed=true`后、replacement创建前停止requeue。
- M：使用独立的optional recovery-premerge capability；不得复用normal `before_merge`或`before_requeue.stop`冒充。`{R,M}`固定`RECOVERY_MERGE_FIRST`，M抢先时R=`preempted_by_merge_first`且stuck=false。

### 6.6 Experiment 4 challenge 与观察

- Slim V2 在展开 11 个 modes 之前，按指标权威为每个 `case_id × repeat_id` 生成一次普通 challenge plan；同一个 plan 对象的 `challenge_plan_id`、family、target set 和 ordinal rule 在全部 modes 中复用。
- `ModeBlindChallengeController`只接收challenge plan、当前unit/attempt与已命中的Experiment 1 source trace，不接收或读取mode、`disabled_mechanisms`或`ProtocolMechanismPolicy`。`Exp4StructuralRouteObserver`可以读取冻结route flags，但不得决定challenge内容；两者不能合并为同时读取challenge与mode的控制器。
- raw transform位于domain parser前；invalid candidate transform位于fixed adapter parse后、Factorization verifier或Lean normalization/child checker前；recovery/merge边界以指标权威第5.4节和结构旁路设计为准。challenge plan、实际opportunity/injection与route outcome由root独占的Slim-local typed collector保存，run后由projector写入root JSONL。
- `challenge_observations[]` 必须记录真实到达边界和实际动作；不得从 mode 名、plan 存在或预期结果反填。verification、canonical、recovery 与 root checker 结果仍从同一 run 的公共 event/store/plugin 事实 join。
- FULL 不产生 disabled-mechanism身份行，但与其他 modes接收完全相同的challenge plan，并可留下M enabled的真实premerge false→true观察。单项和双项mode的`ablation_observations[]`身份行数量分别等于1和2；行存在只证明配置，`route_status/outcome`必须来自actual evidence。
- premature merge使用Slim-local `PrematureMergeObservationV2`，明确`plugin_outcome=not_attempted/rejected_incomplete_input/candidate_produced`、`root_checker_reached`、nullable `root_check_passed`与`final_result_present`。现有shared premature v1不作为Slim科学真值。

### 6.7 Experiment 5 model endpoint

- 每个 model condition 使用只含该目标 entry 的 config view；请求控制按第一份文档冻结。
- `ProtocolConfig.max_retries=0`，`ProtocolMechanismPolicy(replacement_attempts_allowed=false)`。
- Full固定为Factorization hard前28与Lean hard三个topic各前3，共37 roots/model，只运行`repeat_id=0`；GLM、Qwen、MiniMax三个thinking model×四stratum形成12 conditions、111 root-runs和852个planned ordinal-0调用上限。Experiment 1–4不因本项缩容改变。
- 每个 AI unit 的 ordinal=0 provider call 是唯一 provider attempt；自然早停造成的未调用 units保留为 planned-but-unscheduled。
- 三个 endpoint 都使用 `slim_v2.pricing.2026-08-20` 的 SiliconFlow 平价表；provider raw usage 中的 reasoning 只作 completion 子集诊断，不重复计价。
- 对已终止 protocol 中已经持久化的`2xx + raw + parsed` provider attempt，若`verifier_result/checker_result`均为null并带既有`not_applicable_or_unavailable`原因，reducer只能读取为未到达 verification 的不可评估事实：不得补造submission、verification、canonical或failure reason，不得重发provider/Lean调用，也不得改写root/call/response。它仍保留实际调用、coverage与资源事实；依赖“未通过”判断的Exp5 quality指标按指标权威第6.2.1节写null。

## 7. 最小原始字段到实际系统来源的映射

本节字段名与 `slim_v2_experiment_metrics_authority.md` 第 8 节完全一致。

普通 root result 的唯一可读写 schema 是 `tokenshare.slim_v2.root_result.v2`。`failure_origin` 必须出现但可为 `null`；reader、embedded protocol projection 与 reducer 均严格读取 v2。本项目不迁移或兼容旧 v1 结果，新的正式运行使用全新 run ID/目录；该版本切换不取消同一 v2 run 的 snapshot/trace/journal crash-resume。

| 原始字段 | 实际来源 |
|---|---|
| `experiment_id`,`condition_id`,`case_id`,`repeat_id`,`domain`,`difficulty`,`topic_family`,`position_stratum` | Slim V2 预注册 condition/root inventory；case 分层来自当前 catalog/selection |
| `mode`,`disabled_mechanisms`,`worker_count`,`fault_type`,`fault_rate`,`dead_worker_count`,`kill_progress_target_ratio` | Slim V2 condition；执行前写入，不从结果反猜；Exp4 disabled set 必须与 11-mode 表一致 |
| `challenge_plan_id`,`challenge_family`,`challenge_target_planned_ai_unit_ids`,`challenge_attempt_ordinal_rule` | Exp4 在 mode 展开前生成的普通 root challenge plan；同一 `case_id × repeat_id` 的 11 个 modes 复用相同值 |
| `provider_family`,`provider_entry_id`,`configured_model`,`requested_model`,`resolved_model`,`reasoning_mode` | Exp1/5：condition 的单-entry config + submission `usage_summary` + raw/provider-call record；Exp2–4：匹配到的 Experiment 1 回答记录 |
| `root_start_at_ms`,`root_terminal_at_ms`,`runtime_wall_clock_ms` | Slim runner在 `run_root` 协议生命周期入口与 terminal 返回/失败边界使用同一 clock记录；projector校验 `runtime=root_terminal-root_start`。公共 runtime observation 可作交叉读取，但 worker first-start 不能替代 root start |
| `trace_tail_started_at_ms`,`trace_tail_terminal_at_ms`,`trace_tail_wall_clock_ms`,`trace_tail_status` | Exp1 Slim runner 在 `run_root()` 返回后、下一个 root 开始前使用独立真实 clock 记录；projector校验 tail wall，且不得回写 root terminal/runtime |
| `trace_tail_target_ai_unit_ids`,`trace_tail_recorded_ai_unit_ids`,`trace_tail_provider_attempt_count`,`trace_tail_total_tokens`,`trace_tail_cost_estimate_cny` | 来源root的target来自正常协议`runtime_observation.unscheduled_ai_unit_ids`减已有protocol trace keys，recorded/resources来自本root coverage-tail trace聚合；非来源固定空IDs与零资源，状态为`not_required_by_downstream` |
| `preflight_status`,`protocol_started`,`root_status` | `preflight_status` 来自 Slim 调用前普通检查结果；`protocol_started` 由该 root 是否已经进入 `run_root` 并产生首个 task lifecycle event 判定；已启动 root 的 `root_status` 来自 `ProtocolRunResult.status`，异常细分由 `failure_kind/failure_origin` 表达，不创建新的 root-status taxonomy |
| `final_result_present` | root unit `completed` 且 root canonical/final plugin artifact可读取 |
| `verified_correct` | Factorization 独立确定性结果检查；Lean `merge_result.accepted` + root checker `ACCEPTED` |
| `failure_stage`,`failure_kind`,`failure_origin` | submission `result_kind/error`、parse failure、verification/checker report、`summary.terminal_failure`、recovery终态和 Slim 捕获的接线异常按最早失败边界归一化；顶层 kind 只允许三分类，细因只写 origin |
| `planned_ai_unit_ids`,`dispatched_ai_unit_ids`,`completed_ai_unit_ids`,`unscheduled_ai_unit_ids`,`in_flight_ai_unit_ids_at_witness`,`observed_peak_concurrency` | `result.summary.runtime_observation` 同名字段 |
| `worker_execution_facts[].worker_id`,`worker_execution_facts[].started_at_ms`,`worker_execution_facts[].ended_at_ms`,`worker_execution_facts[].result_kind` | `result.summary.runtime_observation.worker_execution_facts`；Slim projector 将现有 fact 时间字段规范化为 `*_at_ms` |
| `required_slot_count` | `adapter.planned_split_plan.merge_plan.required_slots` 的预注册数量 |
| `recovered_valid_canonical_slot_count` | required slot 对应 child unit 最终 canonical + accepted verification/checker 的交集计数 |
| `attempts[].attempt_id`,`attempts[].unit_id`,`attempts[].attempt_ordinal`,`attempts[].trace_origin` | protocol attempt 来自 `EXECUTION_REQUEST_RECORDED` request artifact和 attempt events，`trace_origin=protocol`；coverage-tail attempt 由 Slim-local tail runner生成并标为 `coverage_tail` |
| `attempts[].planned_ai_unit_id` | request `soft_hints.planned_ai_unit_id` |
| `attempts[].replacement_of_attempt_id`,`attempts[].recovery_trigger` | `RECOVERY_ACTION_RECORDED` 与随后新 attempt 的 unit/ordinal/causation 关联 |
| `attempts[].started_at_ms`,`attempts[].ended_at_ms` | attempt state events与对应 worker execution fact |
| `attempts[].result_kind`,`attempts[].provider_call_made`,`attempts[].http_status`,`attempts[].provider_latency_ms` | Exp1/5：完整 submission + provider-call record；Exp2–4：fixed-response submission，`provider_call_made=false`、HTTP/actual provider latency 为 null，来源 latency 写入 `source_latency_ms` |
| `attempts[].raw_response_present` | submission `raw_output_ref != null` 且 `ArtifactStore.verify/read_bytes` 成功 |
| `attempts[].parse_result` | 正常路径来自submission `result_kind`、`parsed_output_ref`、`parse_failure_ref`；P结构旁路的`bypassed`来自Slim parser-route observation，不伪装成公共`ExecutionSubmission`字段 |
| `attempts[].verifier_result` | `VERIFICATION_RECORDED.payload.verification_report.status` 与 domain layer reason |
| `attempts[].checker_result` | Lean `checker_report_for_request(request_id)`；merge/root 使用 `adapter.merge_result.root_checker_report`；Factorization 为 null |
| `attempts[].canonical_accepted` | `CANONICAL_OUTPUTS_BOUND` 对应 selected attempt/submission |
| `attempts[].prompt_tokens`,`attempts[].prompt_cache_hit_tokens`,`attempts[].prompt_cache_miss_tokens`,`attempts[].completion_tokens`,`attempts[].reasoning_tokens`,`attempts[].total_tokens` | Exp1/5：provider raw usage / 完整 submission `usage_summary`；projector校验 prompt cache 分项和 total 关系；reasoning 只作 completion 子集诊断；Exp2–4 当次 actual usage 为 null |
| `attempts[].provider_request_started_at_utc`,`attempts[].pricing_version`,`attempts[].pricing_tier`,`attempts[].cost_estimate_cny`,`attempts[].usage_status` | Exp1/5：provider-call record 的 request start + Slim V2 普通价格 projector；Exp2–4 当次 actual 字段为 null，来源 usage/cost 写入 `source_*` 字段 |
| `attempts[].source_response_slot_id`,`attempts[].source_response_consumed`,`attempts[].source_attempt_ordinal`,`attempts[].source_attempt_fallback_used`,`attempts[].source_trace_origin`,`attempts[].source_result_kind`,`attempts[].source_latency_ms`,`attempts[].source_total_tokens`,`attempts[].source_cost_estimate_cny` | Slim-local fixed-response executor 命中的 Experiment 1 per-unit trace及实际选中的自然 attempt；exact ordinal优先，否则同 trace 最大 ordinal fallback。`source_response_slot_id` 是普通文本 trace-attempt ID，不是 authority slot |
| `attempts[].source_prompt_tokens`,`attempts[].source_prompt_cache_hit_tokens`,`attempts[].source_prompt_cache_miss_tokens`,`attempts[].source_completion_tokens`,`attempts[].source_reasoning_tokens`,`attempts[].source_pricing_version`,`attempts[].source_pricing_tier` | Exp3：同一 source trace attempt 的原始 usage 与普通价格投影字段；perturbation generator 保持 prompt/cache 不变、只扰动完整 completion，并继承 source 价格版本/档位；reasoning 不重复加入 completion |
| `attempts[].source_case_id`,`attempts[].source_repeat_id`,`attempts[].source_planned_ai_unit_id` | fixed-response executor 返回的命中键；projector 必须核对当前 root `case_id`、固定 `source_repeat_id=0` 与 request `soft_hints.planned_ai_unit_id`；root `repeat_id` 不参与 lookup |
| `attempts[].source_unit_candidate_start`,`attempts[].source_unit_candidate_end`,`attempts[].source_lemma_node_id`,`attempts[].source_dependency_path` | Experiment 1 per-unit trace 的普通语义输入；projector按 domain 与当前 request逐字段比较，另一 domain 字段为 null |
| `attempts[].simulated_total_tokens`,`attempts[].simulated_latency_ms`,`attempts[].token_perturbation_factor`,`attempts[].network_perturbation_factor`,`attempts[].perturbation_seed`,`attempts[].perturbation_version` | Exp3 Slim-local perturbation generator + source trace；factor 保存 `δ_token/δ_network`，seed/version 固定，logical scheduler消费 simulated latency |
| `fault_target_planned_ai_unit_ids`,`fault_target_count` | Exp3 Slim-local fault planner在 dispatch 前从 planned first-attempt IDs 计算并写入 root condition record |
| `fault_observations[].attempt_id`,`fault_observations[].fault_type`,`fault_observations[].target_planned_ai_unit_id`,`fault_observations[].injected` | Slim-local hooks 返回的 `EXPERIMENT_FAULT_INJECTED` observation |
| `fault_observations[].reached_verification`,`fault_observations[].independently_wrong`,`fault_observations[].verifier_intercepted`,`fault_observations[].escaped_to_canonical_or_root` | fault observation 与 verification/canonical/root events 按 attempt join |
| `fault_observations[].discarded_total_tokens` | fault/attempt 与 source或actual usage、canonical exclusion 的 join |
| `recovery_observations[].original_attempt_id`,`recovery_observations[].replacement_attempt_id`,`recovery_observations[].replacement_started`,`recovery_observations[].replacement_succeeded`,`recovery_observations[].reassigned` | recovery event、original/replacement attempts 与 worker facts 的 Slim-local join |
| `recovery_observations[].fault_at_ms`,`recovery_observations[].replacement_started_at_ms`,`recovery_observations[].replacement_ended_at_ms` | fault observation、attempt state events 与 worker fact timestamps |
| `worker_death_observations[].worker_id`,`worker_death_observations[].pid`,`worker_death_observations[].exit_code`,`worker_death_observations[].target_progress_ratio`,`worker_death_observations[].actual_progress_ratio` | `WorkerExecutionFact` 的 worker/process/progress 字段 |
| `worker_death_observations[].original_attempt_id`,`worker_death_observations[].replacement_attempt_id` | worker-terminated fact 与 recovery/attempt events 的 join |
| `challenge_observations[].challenge_plan_id`,`challenge_observations[].challenge_family`,`challenge_observations[].target_planned_ai_unit_id`,`challenge_observations[].attempt_ordinal`,`challenge_observations[].injection_boundary`,`challenge_observations[].opportunity`,`challenge_observations[].injected` | mode-blind challenge plan + root 独占 Slim-local collector 的实际 hook/executor observations；不得从 mode 推导 |
| `challenge_observations[].source_semantics_preserved`,`challenge_observations[].candidate_independent_label`,`challenge_observations[].reached_verification`,`challenge_observations[].verifier_rejected`,`challenge_observations[].escaped_to_canonical_or_root` | injector 普通字段比较/独立领域检查结果，与 verification、canonical 和 root events 按 attempt join |
| `challenge_observations[].replacement_started`,`challenge_observations[].replacement_succeeded`,`challenge_observations[].valid_final_after_challenge` | challenge observation 与 recovery/new-attempt/final root 结果按 root/attempt join |
| `ablation_observations[].disabled_mechanism`,`ablation_observations[].route_status` | Slim condition只创建机制身份行；`route_status`来自Slim-local parser/V/R/premerge actual route observation，不使用shared mode whitelist |
| `ablation_observations[].domain_parser_call_count`,`ablation_observations[].domain_child_checker_call_count`,`ablation_observations[].plugin_verify_submission_call_count`,`ablation_observations[].root_checker_call_count` | Slim adapter/bridge与plugin spy的实际调用计数；child checker、verification wrapper与root checker分开 |
| `ablation_observations[].candidate_independent_label`,`ablation_observations[].wrong_canonical_accepted`,`ablation_observations[].root_checker_reached`,`ablation_observations[].root_check_passed`,`ablation_observations[].root_checker_rejected_after_wrong_canonical` | mode-blind independent label + canonical event +真实root checker report；未到达为false/null，不用`not verified_correct`替代rejection |
| `ablation_observations[].raw_only_exposed`,`ablation_observations[].raw_only_accepted`,`ablation_observations[].parse_result` | Slim P-route observation、raw/candidate refs与canonical event；不从mode常量生成 |
| `ablation_observations[].recovery_attempt_id`,`ablation_observations[].recovery_retry_allowed`,`ablation_observations[].replacement_attempt_id`,`ablation_observations[].stuck_due_to_no_requeue` | `RecoveryMergeContext`/recovery event、R actual route、replacement attempt与final的linkage；RM preempted不计stuck |
| `ablation_observations[].merge_gate_satisfied`,`ablation_observations[].required_child_unit_ids`,`ablation_observations[].canonical_child_unit_ids`,`ablation_observations[].missing_required_slot_ids`,`ablation_observations[].plugin_merge_attempted`,`ablation_observations[].plugin_outcome`,`ablation_observations[].plugin_error_kind`,`ablation_observations[].premature_merge_attempted`,`ablation_observations[].premature_merge_failed`,`ablation_observations[].final_result_present`,`ablation_observations[].failure_stage` | optional recovery-premerge context + Slim-local `PrematureMergeObservationV2`；typed plugin rejection与nullable checker明确分开 |
| `missing_reason`,`not_applicable_reason` | Slim extractor在相应公共来源缺失、ratio denominator=0 或 mode不适用时生成的明确枚举值 |

## 8. 错误处理合同

### 8.1 单个 root 如何返回并继续

以下属于实验结果，可写完该 root 行后继续下一 root：

- provider timeout/rate-limit/connection/provider error；
- provider 正常返回但 parser rejected；
- Factorization verifier rejected；
- Lean child checker或root checker rejected；
- retry/replacement 用尽；
- `no_return`、late rejection、预注册 worker death 未恢复；
- 协议按正常状态机得到 `failed` root。

每种情况都必须写固定身份、`root_status`、`failure_stage/kind`、已发生 attempts/usage/timing；不能因无 final result 跳过 JSONL 行。

Factorization 与 Lean 的 provider/transport、parser 或环境正常时 verifier/checker 的 retry exhaustion 都是有效 `no_final`，相应 `failure_origin` 按第 2.3 节保存，成功值为 0。它们不得因共享 coordinator 返回 `failed` 而升级为 infrastructure-invalid。

### 8.2 provider error

- 使用 submission `result_kind` 和 provider-call record 的 terminal attempt分类。
- 已发生 transport call 才令 `provider_call_made=true`；pre-dispatch config/secret失败为 false。
- 若 provider 返回 usage，即使最终失败也保存；缺 usage写 `usage_status` 和 null token/cost。

### 8.3 parse/checker rejection

- parse rejection：保留 raw，`parse_result=rejected`，未到 verifier/checker。
- verifier/checker rejection：保留 parsed candidate、verification/checker状态；若 policy 允许，由 coordinator 正常创建 replacement。
- Lean root checker rejection优先判为 `incorrect_final` 或 `no_final`，取决于是否仍形成可读取的最终 root result；不得只看 protocol `completed`。
- Lean checker 的 `environment_error`、`timeout`、`helper_error` 不是 proof rejection：首次出现即停止 root，不继续模型 retry，并投影为 `infrastructure_invalid`、`failure_origin=checker_environment_error`。
- 预注册 worker-death 导致 replacement 用尽是有效实验 `no_final`，保留 `failure_stage=child_execution`、`failure_origin=worker_death_exhausted`；不得误写为 provider transport 或未知设施异常。

### 8.4 coverage tail failure

- coverage-tail 的 provider/transport/parse/verifier/checker failure 按真实 attempt 保存，最多三个；它不改变已经冻结的 protocol root status、correctness 或 timing。
- 正常 parser/verifier/provider/mixed retry exhaustion 形成的有效 `no_final` 不阻断 coverage tail；只有上文统一 blocker predicate 中的设施终态才阻断。
- 某个 tail unit 无 accepted answer 不是理由去调用第四次、换模型或借其他 unit 回答；只把 tail status 记为 completed-with-failures。该 target 已有真实 failure record 后，仍算 coverage key 已形成，后续 fixed-response executor原样重放该 result kind。
- tail 输出写入失败、同一三元键将产生第二条 trace、或 target 并非正常协议的 unscheduled planned unit，属于接线错误；不得开始下一个 root。

### 8.5 必须停止当前 condition 或整次 run 的接线错误

以下不是模型/协议自然失败：

- catalog case schema、完整 candidate domain 或 Lean fixed DAG 无效；
- provider entry/model/request controls 与 condition 不匹配；resolved model 缺失或不匹配；
- API secret 配置错误、没有目标 eligible entry；
- timing policy 与 logical scheduler组合非法；
- worker capacity/backend capability不匹配；
- Experiment 2–4 出现真实 transport attempt，或 fixed-response lookup 不是严格 `case_id × source_repeat_id=0 × planned_ai_unit_id` 匹配；
- ArtifactStore/EventLedger 无法写入或读取刚写入的数据；
- plugin adapter、bridge、parser/checker 没有按本合同装配；
- FULL mode 出现 disabled-mechanism observation；
- 固定 trace 缺少三元键、同键存在冲突记录、trace 没有自然 attempt record、普通 unit 语义字段不一致或 source identity 不一致。当前 ordinal 不存在本身不是错误，必须回退到同一 trace 的最后自然 attempt。
- Lean 环境 pass 文件缺失、schema/status/pass digest 无效、关键输入 digest 漂移，或两个绑定 `.olean` 缺失/hash 不匹配；启动检查只能轻量读取，禁止调用 Lean/lake 自修复。

已预注册但因 condition fail-stop 未执行的 roots仍写 infrastructure-invalid 行。全局共享 catalog/config/store 无效时停止整次 run；单一 model condition 的 resolved-model mismatch 至少停止该 condition。

reducer 对每个 cell 必须输出 `preregistered_root_count`、`scientifically_valid_root_count`、`infrastructure_invalid_root_count`；预注册但缺 committed result 的 root 计入 infrastructure-invalid 并记 `missing_committed_root_result`，三者恒满足 valid + infra = preregistered。这三个库存计数是冻结 153 formal occurrences 之外的 mandatory diagnostics。只要 infra count 非 0，cell 的 completion/success 及相关科学 rate/effect 均为 `null`，原因固定为 `infrastructure_invalid_root_present`；任一端 infrastructure-invalid 的 pair 必须记为 ineligible，同时保留 planned/eligible/ineligible 数量和原因分布。已经持久化且输入完整的精确 attempt/fault/replacement 数量与 token/cost/wall-clock 资源事实仍须输出，不得被科学有效性 early return 清空。三个失败 breakdown 只按已提交 `failure_kind` 直接分组。所有正式 ratio 的 denominator 为 0 时写 `null + missing_reason=zero_denominator`；从模板 null 改写为数值时必须清除同 metric 的旧 missing/not-applicable reason。

## 9. 明确禁止的依赖

Slim V2 不得 import、调用或读取以下旧设施作为运行基础或补数来源：

- `paper_formal_runner.py`；
- paid receipt 或相关签发/检查模块；
- budget authority；
- response-bank authority；
- execution/publication gate；
- L1–L4 closure；
- paper eligibility；
- lineage/digest closure；
- 旧 paper/formal 输出目录、历史 Rxx 结果或日志。

Slim V2 只依赖本文列出的 core/local_runtime/plugin/executor/storage 公共接口，以及未来位于 `src/tokenshare/experiments/slim_v2/` 的最小 runner/hooks/extractor/reducer。

## 10. 最小调用流程（伪代码）

### 10.1 Factorization

```text
load one preregistered Factorization case
create ArtifactStore + EventLedger + ProtocolConfig + ProtocolEngine
create one FactorizationRuntimeAdapter for this root
if experiment is 1 or 5:
    resolve exactly one provider entry
    create slim real-provider caller + Factorization execution bridge
else if experiment is 2, 3 or 4:
    lookup Experiment 1 trace by exact (case_id, source_repeat_id=0, planned_ai_unit_id)
    compare ordinary unit semantic fields
    select exact current ordinal when present, otherwise the trace's last natural attempt
    create Slim fixed-response executor and record source ordinal/origin/fallback
    prohibit transport fallback
create FactorizationExecutionBridge(adapter, selected_executor)
create the condition's worker backend
result = ProtocolRunCoordinator(...).run_root(
    ProtocolRunRequest(root_input=case, plugin_runtime=adapter,
                       worker_backend=backend, mechanism_policy=policy,
                       hooks=hooks, trace_delay_policy=condition_timing_policy)
)
read result + this root's ledger/store + adapter final artifacts
if experiment is 1:
    atomically snapshot protocol projection and protocol-origin traces
    if case_id is in same-profile Exp2–4 trace-consumer closure:
        snapshot deterministic tail requests and known pre-dispatch coverage-tail traces
        enumerate result.runtime_observation.unscheduled_ai_unit_ids
        subtract units that already have a protocol trace
        acquire each remaining unit immediately, max three natural attempts
        append coverage-tail per-unit traces and a separate tail resource summary
        do not start the next root until tail completion
    else:
        persist base projection trace_tail_status=not_required_by_downstream,
        empty tail IDs/resources, and no tail material
perform independent factorization result check
append exactly one normalized root JSONL row
```

### 10.2 Lean

```text
load one preregistered fixed lemma-DAG Lean case and fixed environment manifest
create ArtifactStore + EventLedger + ProtocolConfig + ProtocolEngine
create one LeanRuntimeAdapter(checker=real check_lean_proof) for this root
if experiment is 1 or 5:
    resolve exactly one provider entry
    create slim real-provider caller + Lean execution bridge
else if experiment is 3 or 4:
    lookup Experiment 1 trace by exact (case_id, source_repeat_id=0, planned_ai_unit_id)
    compare lemma_node_id and dependency_path
    select exact current ordinal when present, otherwise the trace's last natural attempt
    create Slim fixed-response executor and record source ordinal/origin/fallback
    prohibit transport fallback
create LeanExecutionBridge(adapter, selected_executor)
create the condition's worker backend
result = ProtocolRunCoordinator(...).run_root(ProtocolRunRequest(...))
read child checker reports and adapter.merge_result.root_checker_report
if experiment is 1:
    atomically snapshot protocol projection and protocol-origin traces
    if case_id is in same-profile Exp2–4 trace-consumer closure:
        snapshot deterministic tail requests and known pre-dispatch coverage-tail traces
        acquire each unscheduled planned unit immediately into a coverage-tail trace
        preserve explicit dependency/request/provider failures instead of fabricating output
        append separate tail resource summary before the next root starts
    else:
        persist base projection trace_tail_status=not_required_by_downstream,
        empty tail IDs/resources, and no tail material
append exactly one normalized root JSONL row
```

## 11. 最小接口缺口

1. **固定 trace 主矩阵没有可用的 Slim 公共实现**：Experiment 2–4 已冻结为复用 Experiment 1 普通 per-unit trace JSONL，但当前公共接口中没有一个可直接采用、且不依赖旧实验设施的 executor。后续必须在 `src/tokenshare/experiments/slim_v2/` 实现最小只读 fixed-response executor，严格按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 匹配、核对普通 unit 语义字段、exact ordinal优先且缺失时回退同 trace 最后自然 attempt，并禁止 transport fallback；不得读取旧实现补位。
2. **Slim 真实 provider caller 尚未实现**：接线合同冻结的是单 entry 输入、真实 transport、raw/usage/latency/model 输出与小 execution bridge 能力，不要求复用旧 `AIAPIExecutor` 整类。后续可复用现有 provider-specific body builder、urllib transport 和 envelope parser，在 Slim 目录实现薄 caller/bridge；不得重新带入旧 selection、prepared identity、hard-deadline child 或 evidence/budget 依赖。
3. **adapter 并发保证不闭合**：两个 runtime adapter 都保存每 root 可变状态，并在源码中声明单次 run/顺序执行约束；Experiment 2 需要 worker>1，其余实验固定 worker=10。实施 Agent 必须用 Factorization 的真实 k>1 focused tests，以及 Lean adapter 的 fake checker、固定 fixture 或静态合同测试，对 `ThreadWorkerBackend` 与 `ProcessWorkerBackend` 的可行接法给出证据，再选择实现；不得为此运行 Lean 专项 suite、LeanAudit、全量 catalog 或 `lake`/`lean` 回归。本文不推荐或预选答案。worker death 因真实进程终止语义仍必须使用 `ProcessWorkerBackend`。
4. **结果不是一个扁平 DTO**：`ProtocolRunResult` 不直接暴露完整 submission、usage、verification/checker 和 recovery chain。数据并未丢失，可由公共 `EventLedger.read_all()` 与 `ArtifactStore.read_bytes()`取得；因此首版可在 Slim 目录实现只读 projector，无需修改 shared code。若用户要求单对象返回全部字段，才构成需要批准的 shared-interface 变更。
5. **condition 不属于 `ProtocolRunRequest`**：`condition_id`、fault rate、mode 等由 Slim runner和hook闭包持有；公共 request不保存这些实验标签。首版由 Slim JSONL 记录即可，不需要污染 core request。
6. **Experiment 1 coverage tail需要Slim-local选择**：当前coordinator正确地在root terminal后返回，不应修改。Slim runner先以同profile Exp2–4 consumer closure决定该root是否需要tail；只有来源root交给窄tail acquisition组件，接收同root plan、`unscheduled_ai_unit_ids`、已有protocol trace keys、provider caller与领域parser/verifier/checker，逐target写唯一`coverage_tail` trace和普通资源summary。非来源root写明确零资源状态。它不得创建protocol attempt/canonical/merge，也不得延长root runtime；崩溃恢复仅从已持久化snapshot/base projection与三元key判断，不引入response-bank authority。
7. **普通 pricing projector 需要 Slim-local 实现**：现有旧 pricing/config 路径绑定 selection digest 与预算设施；Slim V2 只需在自己的目录实现指标权威第 1.4 节静态表到 `cost_estimate_cny/pricing_version/pricing_tier` 的纯映射。它不得联网、查余额、预估预算或阻止运行。
8. **Experiment 4 recovery-premerge shared gap 已实证并获批**：当前coordinator只在replacement完成后进入normal merge readiness，所以`REQUIRED_CHILD_DELAY × NO_MERGE_GATE`无法观察真实false。用户已批准、三名reviewer以`3/3 RECOVERY_MERGE_FIRST + authorize=yes`冻结最小修复：新增携带recovery identity的optional `RecoveryMergeContext` capability，只在hook实际实现时于logical/non-logical recovery记录后、replacement前调用；normal `before_merge`不增调用。typed incomplete-input rejection为`plugins/contracts.py`中的`IncompleteMergeInputError(ValueError)`，两个plugin只替换既有required-input拒绝分支。P与Lean V在Slim-local adapter/bridge闭合；Exp4 mode、premature v2和projector不得进入shared。

## 12. 已冻结决策与实施期验证项

- 已冻结：Experiment 1每个root分为结构化`run_root()`协议终态与可选择的Slim-local coverage tail；同profile Exp2–4会消费trace的来源root才进入tail，来源有效`no_final`仍补tail，只有统一设施blocker阻断。tail只补该root unscheduled planned units并标记`trace_origin=coverage_tail`；非来源root以`not_required_by_downstream`和零tail资源结束。两者不修改coordinator或root terminal/runtime。
- 已冻结：Experiment 2–4 使用 Experiment 1 两阶段产生的唯一 per-unit traces，严格按 `case_id × source_repeat_id=0 × planned_ai_unit_id` 匹配；exact current ordinal优先，缺失时回退同 trace 最后自然 attempt，且不调用真实 provider。Experiment 3 扰动身份仍使用下游当前 ordinal。
- 已冻结：Experiment 2 不使用独立 20-way split，完整继承 Experiment 1 的 split、unit、prompt 与依赖；只改变 worker count，并由逻辑调度器实际计算结果。
- 已冻结：lookup 后按 domain 比较 Factorization range 或 Lean node/dependency 普通字段，不使用 hash、digest 或证据链。
- 已冻结：Experiment 3 fault target、ordinal 0 注入、五类动作、token/latency perturbation 与 simulated 指标严格按指标权威第 4.2–4.4 节；不得由旧 fault 代码反向定义。
- 已冻结：roots 串行，root timing 由协议生命周期开始/终止及其差值定义；worker first-start 不是 root start。Exp1 tail 位于两个 roots 之间，正文批次 wall-clock 使用 protocol runtime 之和，tail wall 单列。
- 已冻结：真实 provider 边界是能力合同，不要求采用旧 `AIAPIExecutor`；Slim 可用薄 caller/bridge 复用底层 transport/parser。
- 已冻结：前向 Experiment 1 使用 `slim_v2.pricing.2026-08-23` 的 `deepseek-v4-flash` flat 表，Experiment 5 使用 `slim_v2.pricing.2026-08-20` 的三个 SiliconFlow endpoint（GLM、Qwen、MiniMax）平价表；reasoning-as-completion-subset 口径按指标权威第 1.4 节，普通 pricing projector 不构成预算或 gate。2026-08-23 前 exact run 的 Pro 事实只原样保留，不作为前向默认值。
- 已冻结：除 Experiment 2 的六档 worker 外，Experiment 1、3、4、5 的 `worker_count=10`。
- 已冻结：Experiment 2/3 在线检查退出 Slim V2，不存在待选 case IDs。
- 已冻结：Experiment 4采用`slim_v2_exp4_structural_bypass_design.md`的四类实际调用前旁路；`{R,M}`为`RECOVERY_MERGE_FIRST`，M抢先时R-stuck=false；checker未到达为`reached=false/pass=null`，projector不从mode、processing或`not verified_correct`反推事实。
- 已冻结：Exp4 shared修改只限第11节第8项；default/NoOp/Exp1/2/3/5与非Slim caller没有optional capability时必须零新增hook调用、observation和event。任何扩大重新三 Agent投票。
- 实施期验证项：用户不预选 Thread 或 Process。实施 Agent 必须用 Factorization 的真实 k>1 focused tests 和 Lean adapter 的 fake checker、固定 fixture 或静态合同测试证明安全接法，并据证据选择；默认禁止 Lean 专项 suite、LeanAudit、全量 catalog 和 `lake`/`lean` 回归。轻量测试尚未通过时不得修改 shared runtime 来强行满足假设。
