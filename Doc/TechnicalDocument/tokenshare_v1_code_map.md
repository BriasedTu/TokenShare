# TokenShare V1 当前 Code Map

更新时间：2026-08-02

状态：当前总体代码归属权威。本文回答“改动应该放哪里、哪些边界不能跨”，不记录逐次修复历史。协议语义看 `tokenshare_v1_complete_spec.md`，论文实验参数看 `tokenshare_latest_real_plugin_experiment_design.md`。

## 总体依赖方向

```text
experiments ──> local_runtime ──> protocol_engine/core ──> storage
     │                │
     ├──> executors   └──> plugins
     └──> plugins
```

- `core` 与 `storage` 不导入具体插件、provider 或论文实验模块。
- `local_runtime` 协调协议生命周期，通过稳定 contract 调用 plugin/executor。
- `plugins` 拥有领域 split/parser/verifier/merge。
- `executors` 拥有执行配置、transport、输出 artifact 和 replay，不拥有论文 cohort/fault 策略。
- `experiments` 可以组装以上层，但不能伪造协议 event、canonical、merge 或 settlement。

## 根模块

| 文件 | 职责 |
|---|---|
| `src/tokenshare/protocol_engine.py` | 对 append-only ledger 执行状态转换、submission、heartbeat、recovery、merge/settlement 等应用流；ledger 是权威状态。 |
| `src/tokenshare/runtime_paths.py` | 仓库外 data root、实验/diagnostic/supervision 默认路径，以及旧仓库绝对、`outputs/...` 仓库相对、`runs/...` suite 相对路径的只读解析。 |
| `src/tokenshare/__init__.py` | 稳定 public exports；不要导出实验内部 helper。 |

`ProtocolEngine` 仍较大。未来拆分只能在 characterization tests 保护下按应用流拆 helper/service，并保持一个 ledger 写入权威；不要按行数机械搬运。

## `tokenshare.core`

| 文件 | 职责 |
|---|---|
| `models.py` | Task/Unit/Attempt/Lease/Artifact/Event 等协议数据对象与版本化序列化；Task 10 为 unit、lease、attempt 增加持久化 attempt ordinal。 |
| `state_machines.py` | 对象状态转换规则。 |
| `task_graph.py` | DAG、依赖和任务图不变量。 |
| `scheduling.py` | Ready unit 选择与调度纯规则。 |
| `leases.py` | lease/heartbeat/fencing 规则；Task 10 在每个 unit 上单调分配 attempt ordinal，并绑定 lease/attempt。 |
| `recovery.py` | 失败、过期、重试和 requeue 决策。 |
| `verification.py` | submission acceptance 与 verification 规则。 |
| `expansion.py` | 验证后的递归展开规则。 |
| `merge.py`、`merge_coordinator.py` | merge readiness、slot/dependency 与 root completion。 |
| `contribution.py` | contribution 与 sandbox settlement 计算。 |
| `registration.py` | plugin/executor descriptor 注册约束。 |

这里不能出现 `factorization`、Lean theorem、provider/model、paper experiment/fault 等领域判断。

## `tokenshare.storage`

| 文件 | 职责 |
|---|---|
| `events.py` | append-only JSONL event ledger、读取和校验；`VerifiedLedgerSnapshot` 与 `EventLedger.read_verified_snapshot()` 从同一份持久化 bytes 校验 event/hash chain，并冻结 bytes/events digest、count 与 tip identity；Task 10 增加 `TRACE_DELIVERY_COMMITTED.v1`。 |
| `artifacts.py` | content-addressed artifact、manifest、hash/URI 验证；Task 8 提供 temp flush/fsync/rename 与 commit marker 顺序，使外部 bank object 可在 crash 后确定性 reconcile；Task 9 为 Windows normal path 将含冒号 logical artifact id 映射为普通文件名，logical identity 保持不变。 |
| `sqlite_index.py` | 从 ledger 构建/重建 SQLite 查询索引；不是第二状态源；Task 10 materialize unit/lease/attempt ordinal 与 trace-delivery commit。 |

任何非确定性 executor 输出先落 artifact，再由 event 引用。Replay 读取持久化事实，不重新调用 executor/provider。

## `tokenshare.local_runtime`

| 文件 | 职责 |
|---|---|
| `contracts.py` | `ProtocolRunRequest`、execution scope、plugin hooks、worker/backend 等稳定接口；`ProtocolRunLedgerBinding` 把 run/task/root 绑定到 verified ledger snapshot；`RuntimeHookObservationV1` 是三 variant 的 closed typed envelope；Task 9 增加 typed logical schedule/checkpoint contract；Task 10 增加 `PreparedTraceDelivery`、`ParentCommitStores` 与 `TraceDeliveryAttempt`；Task 11 增加 `ParentTraceDeliveryStageContext`、`ParentStagedTraceDelivery` 与 core-neutral `ParentTraceDeliveryStager`。 |
| `coordinator.py` | 组装 scheduler/lease/executor/plugin/engine，推进完整本地协议生命周期；Task 9 由 frozen queue pop 统一推进 logical clock；Task 10 校验 delivery/lease/request/store binding 后由 parent 唯一 commit trace delivery；Task 11 注入 parent-only domain stager、验证 current artifact refs，并把 trace consumption 规范化为零 current provider spend 的普通 engine submission。 |
| `logical_scheduler.py` | Task 9 deterministic logical source-latency event queue、stable tie-break、checkpoint/resume 与六类 completion 闭合。 |
| `workers.py` | sequential/thread/process worker、liveness、真实 process death 与 capacity；Task 9 worker completion 返回 scheduled event；Task 10 worker 只返回 prepared delivery，不能写 parent stores。 |
| `process_worker_child.py` | Windows 独立子解释器 worker 的原子文件 handshake/result sidecar；Task 9 透传 typed scheduled completion；Task 10 透传 typed prepared delivery。 |
| `projection.py` | 从同一次 verified ledger snapshot 派生通用 run/unit/attempt 只读视图，并把 `ProtocolRunLedgerBinding` 放入正式 `ProtocolRunResult`；Task 10 投影 `TRACE_DELIVERY_COMMITTED.v1`。 |

worker backend 只报告执行和死亡事实；是否 retry/requeue 由协议规则决定。不要把 Windows process 退回 multiprocessing spawn pipe/Event 路径。

`RuntimeHookObservationV1` 的三个正式 variant 是 `EXPERIMENT_FAULT_INJECTED`、`EXPERIMENT_ABLATION_GATE_APPLIED`、`EXPERIMENT_PREMATURE_MERGE_ATTEMPTED`。正式 producer 分别位于 `paper_faults.py`、`paper_ablation.py` 与 `local_runtime/coordinator.py`；`paper_formal_metrics.py` 和 `paper_direct_results.py` 通过同一个 `RuntimeHookObservationV1.from_dict()` closed-schema parser 消费，不能把自由形状 summary dict 当作 hook 事实。

## `tokenshare.plugins.factorization`

| 文件组 | 职责 |
|---|---|
| `descriptor.py`、`schemas.py`、`models.py` | 插件版本、I/O schema 和领域对象。 |
| `split_strategy.py` | 确定性候选因子范围拆分；Exp2 的 20-way profile 也由版本化规则约束。 |
| `prompt_builder.py`、`validator.py` | AI request payload 与候选结果验证。 |
| `merge_policy.py` | factor witness OR-join；无 witness 的 prime/no-factor 结论要求完整 coverage。 |
| `runtime_adapter.py` | 把领域行为接入 local runtime contract。 |
| `fixtures.py` | 回归 fixture，不是论文正式结果。 |

## `tokenshare.plugins.lean_proof`

| 文件组 | 职责 |
|---|---|
| `environment.py`、`checker.py`、`preflight.py` | 固定 Lean/lake/toolchain/library 环境与真实 checker。 |
| `fixed_plan.py`、`split_strategy.py` | 校验预注册 fixed lemma-DAG；不从任意 theorem 自动发现完整引理图。 |
| `child_proof.py`、`prompt_builder.py`、`validator.py` | proof unit request、候选 proof 与 checker-backed 验证。 |
| `merge_policy.py`、`runtime_adapter.py` | dependency-aware proof assembly、root recheck 与 runtime bridge。 |
| `replay_evidence.py` | Lean evidence 重放检查。 |
| `descriptor.py`、`schemas.py`、`models.py` | 版本化描述与领域数据对象。 |

AI 只能生成预注册 proof unit 的候选内容，不能决定协议级拆分或绕过最终 Lean checker。

## `tokenshare.executors`

| 文件 | 职责 |
|---|---|
| `contracts.py`、`registry.py` | executor contract 与版本化注册。 |
| `deterministic.py`、`mock_ai.py` | 无网络回归执行器。 |
| `ai_api_config.py`、`ai_api_local_config.py` | tracked safe config 和本地 secret 注入；tracked config 只保存 `api_key_env`。 |
| `ai_api_selector.py` | entry/capability 选择。 |
| `ai_api_request_identity.py` | 唯一 prepared-request factory：冻结 canonical UTF-8 body bytes、normalized absolute endpoint、content type、admission profile 与稳定 inference request digest，并重算一致性后才允许 dispatch。 |
| `ai_api_transport.py` | DeepSeek/OpenAI-compatible/SiliconFlow transport；只消费已校验的 exact endpoint/bytes ABI，不二次序列化。 |
| `ai_api.py` | 执行请求、attempt/provenance/usage 收集；Task 8 acquisition 路径在 secret resolution/transport 前完成 receipt/mode/output-marker、prepared consistency、prompt admission、durable prepared artifact、inventory winner 与 budget reservation/dispatch-intent 门禁，同一 semantic-slot loser 不进入 transport；Task 11 的最小 plan-out 接点让 descriptor 同时声明 execution request v1/v2 compatibility。 |
| `ai_api_artifacts.py` | raw/parsed/failure/provenance/usage/model record artifact。 |
| `response_bank.py` | Task 5 immutable bank object/index/opaque external locator：规范化 manifest/inventory entry/current wrapper，校验 self-excluding entry identity、role 完整性、root marker 绑定，并在流式 hash 验证后解析 bank-internal object。 |
| `trace_backed.py` | Task 11 零 provider trace executor 与 parent stager：冻结 planned unit/replacement 到 immutable bank entry 的 `TraceSourceBinding`，流式校验外部对象并准备 delivery；parent-only 阶段写 current provenance、source trace attribution 与领域 parser/checker/canonical refs，缺 slot、binding 冲突或非 current artifact 时 fail closed。 |
| `ai_api_replay.py` | 从 artifact 或显式绑定的 external response bank 恢复结果，不重新调用 API；bank replay 要求相同 root binding。 |

Secret 只能进入当前进程环境和脱敏后的 transport；event/artifact/SQLite/log/config digest 不得保存 secret。

## `tokenshare.experiments`

### 通用回归与领域 adapter

| 文件组 | 职责 |
|---|---|
| `models.py`、`runner.py`、`report.py`、`metrics.py`、`simulation.py` | 早期通用实验/regression API；不能直接当论文指标。 |
| `factorization_adapter.py`、`lean_adapter.py` | 通用实验 adapter。 |
| `factorization_paper_adapter.py`、`lean_paper_adapter.py` | 论文兼容薄壳：构造 `ProtocolRunRequest`、调用 coordinator、投影旧 shape；Task 11 增加 trace runtime adapter/execution bridge 与 parent-owned domain stage；Task 19 正式接入 typed trace context、`TraceBackedParentStager`、logical scheduler 和公开 coordinator，使 Factorization parser/verifier 与 Lean parser/checker/canonical/merge/settlement 走正常生命周期，保留 source latency、ordinal replacement 且 current provider calls=0。 |
| `factorization_500_ai.py`、`lean_ai_benchmark.py`、`ai_profile.py` | 直接 benchmark/diagnostic；不是论文协议结果。 |

### 论文条件与身份

| 文件 | 职责 |
|---|---|
| `paper_models.py` | paper condition/result/budget/fault schema；Task 18 新增版本化 evidence classification/eligibility facts 与 evaluator：online 逐 executed unit 绑定 current real provider attempt/lifecycle，trace 逐 unit/replacement/entry 绑定 canonical manifest、source provenance、receipt creator且 current calls=0；Factor/Lean success 与 provider failure 按 canonical domain/terminal kind 使用不同 lifecycle truth table，旧 schema 不得升级。 |
| `paper_catalog.py`、`paper_factorization_catalog.py` | catalog 加载、manifest、selection 与 oracle/preflight。 |
| `paper_factorization_sampling.py` | Factorization 分层稳定评分、采样 profile 校验与 immutable catalog slice；不拥有全 suite 各实验题量。 |
| `paper_catalog_execution_view.py` | 冻结规划时 catalog view，供 execute/resume/replay 使用同一 body/digest。 |
| `paper_experiment_contracts.py` | 冻结 selection、execution context 和 contract digest。 |
| `paper_suite_scale.py` | 加载 `paper_suite_scale_profile.v1.json`，从不可变 catalog 按 difficulty 内稳定 hash 物化 Exp1–4 Factorization scopes，并绑定 active Exp5 v4 selection；缺失/旧 profile 不得静默回退。 |
| `paper_model_identity.py` | experiment-layer endpoint/reasoning identity 与 pre/post-call audit。 |
| `paper_model_policy.py` | Exp5 cohort/entry map/preflight；当前为四模型 v3。 |
| `paper_unit_commitments.py` | plan/condition/request/attempt 的 AI unit binding。 |

### 执行、故障与终态

| 文件 | 职责 |
|---|---|
| `paper_runner.py` | 展开 Exp1–5 condition/repeat/seed/selection 和 dispatcher plans。 |
| `paper_dispatcher.py` | 把 paper case/scope 交给 system runtime；Task 19 增加可选 typed trace context 的原样透传，默认 online/历史路径不变，不访问 coordinator 私有 ABI。 |
| `paper_runtime_clock.py` | Task 9 冻结 logical source-latency 1x 与 online real-clock policy；trace 路径拒绝 real sleep/noop sleeper。 |
| `paper_formal_runner.py` | 正式/smoke suite orchestration、preflight、dispatch、checkpoint、resume/replay；Task 19 在 engine/event 前完成 terminal bank/case digest preflight并走正常 Factor/Lean lifecycle；Task 22 以 external opaque locator 驱动 trace，按 condition/per-worker lane 聚合 logical runtime，保留 root-local source/current timing与真实并发。 |
| `paper_formal_callbacks.py` | provider/executor callback 绑定。 |
| `paper_formal_evidence.py` | 正式 evidence store、manifest、checkpoint 和完整性校验；Task 18 接入版本化 eligibility；Task 20 构造 protected `CanonicalLineageInput`/`LineageSourceIndex`；Task 22 保持 external source acquisition/current execution typed binding 分离并逐 root 流式写 evidence，不扫描或复制 private/global/raw source objects。 |
| `paper_formal_checkpoint.py` | generation v3 root-delta checkpoint、resume 与 terminal streaming SQLite compaction；Task 22 逐 root 释放 full outcome，并从真实 terminal、Task20 source-index/observations/manifest/output refs 与 Task21 renderer manifest/artifacts 重算 resume digest closure，mutation fail closed。 |
| `paper_faults.py`、`paper_workers.py` | 五类 rate-fault 与 worker-death 的预注册 hook/投影。 |
| `paper_exp1.py`、`paper_exp2_scalability.py`、`paper_exp3_fault_recovery.py`、`paper_exp4_ablation_runner.py` | 各实验的独立行为/指标 helper。 |
| `paper_ablation.py` | `FULL + 4` protocol mechanism policy。 |
| `paper_terminal_outcomes.py` | succeeded/failed/blocked/incomplete 终态语义。 |
| `paper_smoke.py` | smoke profile、identity 与非论文执行。 |
| `paper_exp5_smoke_evidence.py` | Exp5 v4 8-root smoke 的 artifact/event/hash-chain/identity evidence validator；raw LedgerEvent 保持原 envelope/hash，实验分类通过 task binding 关联。 |

`paper_formal_runner.py` 是当前最大风险热点。未来拆分优先提取纯 preflight、dispatch、evidence finalization 边界；任何拆分先锁 characterization tests，禁止复制一套生命周期。

### 指标与报告

| 文件 | 职责 |
|---|---|
| `paper_projection.py` | 从 system projection/events/artifacts 派生 task/attempt rows。 |
| `paper_direct_results.py` | Task 3 canonical facts projector：从正式 runtime result、verified ledger/artifact/parser facts 构造 deep-immutable direct rows；Task 23 增加严格 additive expanded-root merge 分支，仅在唯一 merge-unit canonical + `MERGE_RECORDED` + root terminal 的 full ArtifactRef/parent/ref/order commitment 完全匹配时接受 final；direct-root行为不变。 |
| `paper_historical_fixture.py`、`historical_real_factorization_single_leaf.py` | Task 23 历史真实 Factor single-leaf 离线回归：冻结最小脱敏 fixture/source digests，经正常 coordinator/parser/verifier/canonical/merge/ledger 与 central direct projector 运行；只标记 `regression_only`、`paper_eligible=false`，拒绝 range/trace-paper 语义且 provider=0。 |
| `paper_metric_contract.py` | Task 2 machine-readable metric contract loader/evaluator；Task 12 同步 current pipeline profile digest；Task 14 修正 Exp3 started/reassignment backlink；Task 20 以 scoped `MetricComputationTrace` 捕获真实 `MetricObservationBundle`、row/cell identity、membership 与 evaluation，不修改任何公式/null/invariant 语义。 |
| `paper_exp1_metrics.py` | Task 12 standalone Experiment 1 pure observation projector：按 domain/difficulty/topic/repeat 消费 canonical direct rows 与已物化 timing/provider facts，保留全部预注册 roots 的固定分母，wrong final 只计 completion，infra invalid 与资源缺失显式 null/block；不 import formal runner/renderer，不修改 registry，draft 默认 `paper_eligible=false`。 |
| `paper_exp2_metrics.py` | Task 13 standalone Experiment 2 pure trace/online observation projectors：按 digest-bound case ref、repeat/sample、worker 与 position stratum 投影固定分母、logical makespan、paired speedup/efficiency、committed trace slot/token/cost、调度利用率与 online first-attempt 429/timeout union；失败与 missingness 显式保留，完整 main trace 前 post-bank check 为 `not_evaluated_pre_bank`，不 import formal runner/renderer、不修改 registry，draft 默认 `paper_eligible=false`。 |
| `paper_exp3_metrics.py` | Task 14 standalone Experiment 3 pure trace/online recovery projectors：投影 fixed controlled-candidate denominator、完整 replacement/reassignment backlink、same-sample paired trace absolute overhead、discarded trace tokens、worker-death completeness/kill error，以及 ordered current-provider identity/usage/cost/wasted actual tokens；缺 provider object/role 时六个 online resource cells 统一 null/block，不 import formal runner/renderer，不复制协议状态机。 |
| `paper_exp4_metrics.py` | Task 15 standalone Experiment 4 pure pair/projector：按 domain/case/repeat/sample/replacement identity 将四个 ablation 各配对唯一 FULL，保留全部预注册失败与四 transition，输出 FULL−ablation success/completion loss、ablation−FULL trace resource delta 和四 mode 专项 denominator；duplicate ablation identity 全部保留但统一 null/block，不从 mode 名称合成 hook/outcome。 |
| `paper_exp5_metrics.py` | Task 16 standalone Experiment 5 pure quality/resources projector：直接使用 canonical `PaperModelEndpointIdentity` 官方 digest/fields，按 actual first attempts、checkable candidates 与全部 preregistered roots 投影冻结 denominator；以 enclosing protocol wall-clock、3 raw repeats 与 median/min/max/range 生成恰好两张 ordered payload/caption，禁止 pairwise/significance/ranking/retry/recovery/accepted-validity。 |
| `paper_metric_registry.py` | Task 17 contract-bound projector registry：把 8 张正式 table 唯一映射到 Tasks12–16 accepted projectors；Exp3 trace/online 分别薄调用 Task14，Exp5 quality/resources 共用一次 Task16 projection；逐 row-kind 精确核对 metric inventory，禁止在 registry 重算公式。 |
| `paper_metrics.py` | evidence-derived 通用统计与 integrity validation。 |
| `paper_formal_metrics.py` | Task 17 正式 Exp1–5 metric draft 发布路径；Task 20 内部构造 protected canonical lineage inputs、捕获真实 evaluator traces、join persisted source index，并原子发布 `paper_lineage_source_index.v1.jsonl`、`paper_metric_observations.v1.jsonl` 与 manifest；不保留旧公式或 renderer 派生。 |
| `paper_metric_observations.py` | Task 20 每 numeric cell 唯一 lineage materializer：按 row/cell digest 精确 join，保存公式/value/numerator/fixed denominator membership/excluded-null-blocked reasons，并逐 current execution 或 source entry 校验 typed roles；non-null 由既有 evaluator独立复算，缺 role 时 cell null/table blocked。 |
| `paper_traceability.py` | Task 24 deterministic traceability replay：仅接受 runner 生成的 protected persisted L4 descriptor，按 v2 manifest/CURRENT/generation/typed artifact-index exact closure fresh reload direct/current/source，复用 registry→materializer→renderer 独立重算 observations/tables/cell-lineage digests；derived inputs fail closed，输出与 report 共用 atomic generation，provider/source writes=0。 |
| `paper_online_checks.py` | Task 25 capability/Exp2/Exp3 online-check pure plan 与 typed evidence producer：冻结4/24-480/2-12 scope，消费官方 verifier/checker/requeue 与 callback persisted objects，资源输入仅来自 current provider payload；缺 role blocked/null，不计算指标公式、不创建receipt、不dispatch。 |
| `paper_paid_authorization.py` | Task 26 纯离线 external receipt validator：验证 canonical digest 与scope/plan/profile/budget/inventory/admission/selection/path/expiry/approval binding，派生new-run/resume marker并要求独立allow flag；无mint/approve/secret/reserve/dispatch API，synthetic tests不构成真实授权。 |
| `paper_report.py`、`paper_formal_report.py`、`paper_smoke_report.py` | 通用/正式/smoke 输出；Task 21 正式 report 只接受持久化 Task20 lineage/source-index/observation/manifest/digest 闭包，验证 exact 8 table completeness 后发布 renderer/result/eligibility/secret-audit refs；smoke 永远 paper-ineligible。 |
| `paper_exp5_artifacts.py`、`paper_exp5_model_comparison.py`、`paper_exp5_statistics.py` | Task 21 contract-only CSV/TEX renderer 位于 `paper_exp5_artifacts.py`：从 typed observations 生成 exact 8 CSV 与同 stem TEX、audit JSONL/manifest，保留 null/denominator/reason，整批 stage/promote/rollback；旧 pairwise/ranking/recovery/Markdown/PDF 输出已退役。其余为历史 Exp5 比较/统计 helper。 |
| `paper_budget.py` | plan-only roots/units/attempt/token/cost/time/space 预算与门禁；Task 6 把完整 bank inventory 写入预算投影，Task 7 增加 provider-writing 硬预算 limits 与禁止 unlimited mode 的入口门禁。 |
| `paper_response_bank.py` | Task 6 complete semantic-slot inventory/zero-engine preflight；Task 8 acquisition orchestrator 按 paid receipt→prepare/admit→durable object→atomic reserve→dispatch intent→single send→terminal publish→settle；Task 19 增加由旧 preflight 复用的纯 terminal completeness API 与 `case_id + case_record_digest + semantic slots` typed binding，planned row 无 terminal entry或跨 case source交换均 fail closed。 |
| `paper_budget_ledger.py` | Task 7 SQLite WAL atomic budget authority；Task 8 把 inventory winner、reservation、dispatch intent、ambiguous/terminal published 与 exactly-once settle 接入 acquisition/reconcile。expired receipt 只能闭合已发布状态，不能 reserve/dispatch；JSONL 仅为 committed rows 的审计导出。 |
| `paper_resource_accounting.py` | Task 7 按 frozen pricing 核算 terminal provider usage；usage 缺失时保留完整 token/CNY reservation upper，避免少计实际 acquisition spend。 |

正式规模和资源边界由 `paper_suite_scale.py`、`paper_budget.py`、`paper_formal_checkpoint.py` 与 `paper_formal_metrics.py` 共同约束：当前 Exp1–5 精确总量为 `6,384 roots / 40,520 units / 81,272 attempt upper`，最大单 condition 为 `100/1,000/1,000`；generation 逐 root delta、terminal SQLite streaming compaction、metrics lazy bundle mapping 和 JSONL/chunked scan 使内存按最大单 outcome/当前 bundle 定界，而不是把全 suite 同时载入。磁盘 forecast/compaction/safety reserve 必须写入 `run_budget.json` 并在 provider dispatch 前检查目标卷。

### EPD-027 实施进度与后续实验设施改造

2026-08-01 已冻结 Experiment 2–4 两阶段真实回答库设计。当前 35 个实施 Task 中 Task 0–26 已 accepted。Task 26 paid-receipt validator commit=`e9ab3d9c`；最终 `14 passed in 0.31s`，final reviewer PASS=`0/0/0`，provider/network calls=`0`。这个 `27/35` 状态不代表获得真实授权：Task 27 queued/next，当前仍无 user-provided/validated paid receipt，真实 dispatch 不存在；正式矩阵保持 **NO-GO**。Task 26 验收未运行 Fast/Full/LeanAudit/network/provider。

后续完整实施计划必须同时覆盖：

- Task 27 及后续 unified CLI/gates，保持 Task21 outputs、Task22 resume ABI、Task23 source hashes、Task24 replay digests、Task25 plan与Task26 receipt schema稳定；
- `online_real_provider` 与 `real_model_trace_protocol_run` 两类 paper eligibility，禁止把 trace consumption 冒充当前 provider call；
- acquisition actual spend 与 per-condition trace attribution 两套资源账，以及 calls/tokens/CNY/in-flight 人民币 1,000 硬门；
- Experiment 2 缩小题集六 worker 档在线并发检查、Experiment 3 小型在线恢复检查；
- metrics/report/renderer/replay/audit 与 smoke/canary 身份迁移。

这是一项跨 `tokenshare.executors`、`tokenshare.experiments`、两插件 runtime adapter 和输出 schema 的全面设施修改，不得在 `paper_formal_runner.py` 内临时塞入读取文件的旁路，也不得复制一套协议生命周期。已接受的 ledger/hook/direct-result、immutable bank、semantic inventory/preflight、deterministic logical scheduler、parent-owned trace delivery commit ABI、Task 11 trace-backed executor / dual provenance、Task 12–16 Exp1–5 projectors、Task 17 registry/formal metrics、Task 18 eligibility、Task 19 normal formal lifecycle、Task 20 cell lineage、Task 21 contract-only renderer、Task 22 external-bank streaming、Task 23 historical regression、Task 24 traceability replay 与 Task 25 online-check evidence 均不改变 `ProtocolEngine` 状态机；paid-readiness/CLI 属于后续 Task。后续继续先建 characterization/RED tests，并以唯一权威实验设计 EPD-027 为准。

指标不得使用固定协议时间、自填成功字段或丢失失败/未开始分母；所有汇总必须能回到逐 task/attempt/event/artifact。

### CLI

| 文件 | 默认输出类别 |
|---|---|
| `run_paper_experiments.py` | 必须显式指定全新 `--output-root`；`<data-root>/outputs/experiments/paper_v1` 仅作为 smoke 隔离边界与历史容器 |
| `run_all.py` | `<data-root>/outputs/experiments/` |
| `run_ai_profile.py` | `<data-root>/outputs/experiments/ai_profile` |
| `run_factorization_500_ai.py` | `<data-root>/outputs/experiments/factorization_500_ai` |
| `run_lean_ai_benchmark.py` | `<data-root>/outputs/experiments/lean_ai_50` |

通用 CLI 的显式输出参数优先于默认 data root；paper runner 必须显式指定全新 `--output-root`。

## 配置、数据与脚本

| 位置 | 内容 |
|---|---|
| `benchmarks/paper/` | tracked catalogs、selection、safe provider config、cohort、smoke profiles；active 规模为 `paper_suite_scale_profile.v1.json`，Exp5 active selection/smoke 为 v4，历史 v1/v3 文件只供 replay/provenance。 |
| `local/*.local.json` | gitignored secret/local config；不得归档或迁入 tracked 文档。 |
| `local/run_*_smoke.ps1` | 真实 smoke launcher；输出和 supervision 可使用外部绝对路径。 |
| `verification/` | Fast/Full runner、Fast manifest、Lean canary manifest。 |
| `TokenShareData/`（仓库同级） | 运行 outputs、diagnostics、supervision、迁移清单和备份。 |

## 修改检查表

- 改 schema/event/artifact：同步 spec、replay 与版本测试。
- 改协议生命周期：检查 core + engine + local runtime + 两插件影响。
- 改插件：不要把领域判断放进 core；Lean 变化按 verification profile 跑 canary/audit。
- 改 executor/provider：检查 secret 不落盘、provenance、replay、identity。
- 改 paper runner/metrics：检查真实 lifecycle coverage、失败分母、paper eligibility 和 output contract。
- 改代码后同步本文件；不要新增另一个“当前 code map”。
- 最终运行与风险相称的定向测试、`.\init.ps1` 和需要时的 `.\init.ps1 -Full`。

历史 Phase 7/8 code map 已移入 `Doc/archive/code-maps/`，只供 provenance。
