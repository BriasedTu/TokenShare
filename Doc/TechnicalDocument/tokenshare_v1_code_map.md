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
| `factorization_paper_adapter.py`、`lean_paper_adapter.py` | 论文兼容薄壳：构造 `ProtocolRunRequest`、调用 coordinator、投影旧 shape；Task 11 各自增加 trace runtime adapter/execution bridge 与 parent-owned domain stage，使 Factorization parser/verifier 和 Lean parser/checker/canonical 仍由领域层拥有。 |
| `factorization_500_ai.py`、`lean_ai_benchmark.py`、`ai_profile.py` | 直接 benchmark/diagnostic；不是论文协议结果。 |

### 论文条件与身份

| 文件 | 职责 |
|---|---|
| `paper_models.py` | paper condition/result/budget/fault/eligibility schema 和 digest。 |
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
| `paper_dispatcher.py` | 把 paper case/scope 交给 system runtime。 |
| `paper_runtime_clock.py` | Task 9 冻结 logical source-latency 1x 与 online real-clock policy；trace 路径拒绝 real sleep/noop sleeper。 |
| `paper_formal_runner.py` | 正式/smoke suite orchestration、preflight、dispatch、checkpoint、resume/replay。 |
| `paper_formal_callbacks.py` | provider/executor callback 绑定。 |
| `paper_formal_evidence.py` | 正式 evidence store、manifest、checkpoint 和完整性校验；以 immutable protocol task id 到 case task-id closure 的显式映射闭合 canonical selection evidence，映射冲突 fail closed。 |
| `paper_formal_checkpoint.py` | generation v3 root-delta checkpoint、resume 与 terminal streaming SQLite compaction；保持逐 root 释放，避免全 suite outcome 常驻。 |
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
| `paper_direct_results.py` | Task 3 已接受的 canonical facts projector：从正式 runtime result、verified ledger binding、artifact 与 official typed-hook parser 构造 deep-immutable direct rows；以 preregistered root inventory 固定分母并显式生成 `not_started`，不从 gate bool/summary 自填成功。输出供后续 metric observation/projector registry 接线，当前不是 formal metrics/renderer 的替代入口。 |
| `paper_metrics.py` | evidence-derived 通用统计与 integrity validation。 |
| `paper_formal_metrics.py` | 正式 Exp1–5 指标表生产路径。 |
| `paper_report.py`、`paper_formal_report.py`、`paper_smoke_report.py` | 通用/正式/smoke 输出；smoke 永远 paper-ineligible。 |
| `paper_exp5_artifacts.py`、`paper_exp5_model_comparison.py`、`paper_exp5_statistics.py` | Exp5 v3 artifact、比较与统计。 |
| `paper_budget.py` | plan-only roots/units/attempt/token/cost/time/space 预算与门禁；Task 6 把完整 bank inventory 写入预算投影，Task 7 增加 provider-writing 硬预算 limits 与禁止 unlimited mode 的入口门禁。 |
| `paper_response_bank.py` | Task 6 complete semantic-slot inventory/zero-engine preflight；Task 8 acquisition orchestrator 按 paid receipt→prepare/admit→durable object→atomic reserve→dispatch intent→single send→terminal publish→settle 顺序运行，resume 只 dispatch missing，dispatch intent 后无 terminal 必须标记 ambiguous 且不自动重发，显式 reacquisition 绑定 prior attempt。 |
| `paper_budget_ledger.py` | Task 7 SQLite WAL atomic budget authority；Task 8 把 inventory winner、reservation、dispatch intent、ambiguous/terminal published 与 exactly-once settle 接入 acquisition/reconcile。expired receipt 只能闭合已发布状态，不能 reserve/dispatch；JSONL 仅为 committed rows 的审计导出。 |
| `paper_resource_accounting.py` | Task 7 按 frozen pricing 核算 terminal provider usage；usage 缺失时保留完整 token/CNY reservation upper，避免少计实际 acquisition spend。 |

正式规模和资源边界由 `paper_suite_scale.py`、`paper_budget.py`、`paper_formal_checkpoint.py` 与 `paper_formal_metrics.py` 共同约束：当前 Exp1–5 精确总量为 `6,384 roots / 40,520 units / 81,272 attempt upper`，最大单 condition 为 `100/1,000/1,000`；generation 逐 root delta、terminal SQLite streaming compaction、metrics lazy bundle mapping 和 JSONL/chunked scan 使内存按最大单 outcome/当前 bundle 定界，而不是把全 suite 同时载入。磁盘 forecast/compaction/safety reserve 必须写入 `run_budget.json` 并在 provider dispatch 前检查目标卷。

### EPD-027 实施进度与后续实验设施改造

2026-08-01 已冻结 Experiment 2–4 两阶段真实回答库设计。当前 35 个实施 Task 中 Task 0–11 已 accepted：Task 11 trace-backed executor / dual provenance commit=`1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`，严格 10 文件（6 production + 4 tests），含用户批准的 4 个最小 plan-out 接点；post-min trace final exit `0`、`10 passed in 3.58s`，parent commit 32、logical scheduler 5、Factorization + Lean targeted 2、recovery 39、descriptor 3 通过；6 production compile/diff/allowlist/secret-output 通过；final reviewer PASS；测试最小化删除 7 行 / 1 test。canonical 首次 tool timeout，cache 随后暴露 3 个 v2 descriptor 失败，修复后 3 nodeids pass；原 canonical 未取得完整成功退出，不能记录 canonical PASS。provider_calls=`0`，无 paid receipt。这个 `12/35` 状态不代表整条 paper pipeline 已实现：Task 12 queued/next，`paper_formal_runner.py` 尚不能以回答库输入完成后续正式 consumer/metrics/renderer/replay 接线。本次状态持久化未运行测试/Fast/Full/LeanAudit/network/provider，Full intentionally not run per user。

后续完整实施计划必须同时覆盖：

- Task 12 及后续 projector/registry/formal runner 接线，把已接受的 trace-backed executor 与双 provenance 消费纳入正式 Exp1–5 pipeline；
- `online_real_provider` 与 `real_model_trace_protocol_run` 两类 paper eligibility，禁止把 trace consumption 冒充当前 provider call；
- acquisition actual spend 与 per-condition trace attribution 两套资源账，以及 calls/tokens/CNY/in-flight 人民币 1,000 硬门；
- Experiment 2 缩小题集六 worker 档在线并发检查、Experiment 3 小型在线恢复检查；
- metrics/report/renderer/replay/audit 与 smoke/canary 身份迁移。

这是一项跨 `tokenshare.executors`、`tokenshare.experiments`、两插件 runtime adapter 和输出 schema 的全面设施修改，不得在 `paper_formal_runner.py` 内临时塞入读取文件的旁路，也不得复制一套协议生命周期。已接受的 ledger/hook/direct-result、immutable bank、semantic inventory/preflight、deterministic logical scheduler、parent-owned trace delivery commit ABI 与 Task 11 trace-backed executor / dual provenance 边界不改变 `ProtocolEngine` 状态机；projector registry 与 formal runner/metrics/renderer/replay 的正式 pipeline 接线属于后续 Task。后续继续先建 characterization/RED tests，并以唯一权威实验设计 EPD-027 为准。

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
