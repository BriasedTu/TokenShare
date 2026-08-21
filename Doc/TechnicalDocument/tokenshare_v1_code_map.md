# TokenShare V1 当前 Code Map

更新时间：2026-08-22

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

## Slim V2 精简实验路径

Slim V2 当前入口与权威只在`Doc/SlimV2/`；运行代码位于`src/tokenshare/experiments/slim_v2/`，不得import旧`paper_*`/formal runner。Task 4–6的主要归属如下：

| 文件 | 职责 |
|---|---|
| `experiments/slim_v2/scenarios.py` | 组装Exp2六worker logical replay、Exp3 ordinal-0 fault/真实Process death/reference与Exp4 mode-blind 11-mode结构旁路；普通路径使用现有Thread/Sequential，death继续由真实`ProcessWorkerBackend`产生PID/exit/progress事实。 |
| `experiments/slim_v2/execution.py` | Exp1/provider与fixed-trace submission边界；Task 4增加parser前P route、Lean normalize/checker前Slim bridge以及Process结果透明prepare/export/ingest。 |
| `experiments/slim_v2/runtime.py` | 把scenario scheduler/policy/hooks/backend注入同一个`ProtocolRunCoordinator.run_root`；Task 6增加生产root装配、全实验typed protocol投影、Exp1 coverage tail/fresh resume与按root共享provider permit，不建立第二runner或状态机。 |
| `experiments/slim_v2/projector.py` | 从同一次system result、ledger、worker facts、plugin/checker与Slim route artifacts投影Exp2–4字段；只认actual evidence，未到达checker为`false/null/0`。 |
| `experiments/slim_v2/schema.py` | Slim root/attempt/fault/recovery/death/challenge/ablation typed schema与跨字段不变量。 |
| `experiments/slim_v2/storage.py` | 普通run文件与resume事实；Task 5增加逐行root/reference inventory-result join、独立Exp3 reference路径和Exp4 challenge inventory只读行；Task 6增加typed protocol/terminal/response恢复、公开run/reference writer与单层system root路径。 |
| `experiments/slim_v2/provider.py` | single-entry 16MiB bounded provider调用、usage/model/cost普通投影和intent/response/typed-terminal journal；恢复只读既有事实，dead intent写unknown且不重复同ordinal。 |
| `experiments/slim_v2/reducer.py` | 唯一Exp1–5离线统计内核；按table/slice流式归约，拥有153 occurrence/evidence合同、固定分母、pair/quadruple、type-7、sample variance、固定bootstrap、null finalizer及五表/summary staged atomic publish；不得导入runtime/checker/provider或读取raw/system目录。 |
| `experiments/slim_v2/cli.py` | 唯一实验编排入口；实现`plan/run/run-all/reduce/representative`、串行roots、原子单实验inventory、typed resume、显式source与语义closure、run lock、调用/磁盘安全preflight和condition失败作用域。 |
| `experiments/slim_v2/gui.py`、仓库根`run_slim_v2.cmd` | Windows薄参数窗口与双击入口；只把profile/experiment/run/source/resume确定性映射为同一CLI argv，并监督一个CLI子进程，不承载runner、preflight或恢复逻辑。 |
| `tests/experiments/slim_v2/test_scenarios.py` | 风险驱动覆盖Thread双域事实、Exp2六worker、Exp3五fault与12-cell真实Process death、Exp4四family×11 modes、default-zero与零provider/transport。 |
| `tests/experiments/slim_v2/test_reducer_golden.py` | 单一golden run风险驱动覆盖153/153、fixed denominator、tail、独立pair eligibility、六组四端、Exp5四端分类、bootstrap/null阈值、读取边界和晚失败/replace失败发布事务。 |
| `tests/experiments/slim_v2/test_cli_e2e.py`、`test_runtime_resume.py` | 全离线覆盖原子命令、source/failure scope、run lock、动态磁盘、全实验protocol、fresh resume、terminal/intent防重复与Exp5 provider permit。 |
| `tests/experiments/slim_v2/test_gui_launcher.py` | 覆盖2 profiles × Exp1–5/all参数映射、必要source、一个CLI子进程、零provider和launcher精确目标；不测试控件像素。 |

Task 4获批的shared例外仍保持最小：`local_runtime/contracts.py`定义optional`RecoveryMergeContext`，`local_runtime/coordinator.py`只在真实recovery记录后/replacement前调用capability并记录固定synthetic-V provenance；`plugins/contracts.py`的`IncompleteMergeInputError(ValueError)`只替换Factorization/Lean adapter原有incomplete-required-input异常分支。P、Lean-V、mode/challenge、premature-v2和projector全部留在Slim-local。

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
| `environment.py`、`checker.py`、`preflight.py` | 固定 Lean/lake/toolchain/library 环境与真实 checker。Task 33 的 environment ref 明确分开历史 authority、当前 runtime 与跨 checkout semantic digest，并在需要 authority bridge 时校验当前 fixture project。 |
| `semantic_authority.py` | Task 33 的版本化跨 checkout/EOL authority：先逐字节验证既有 v1 direct/graph/preflight authority，再按 `utf8_lf.v1` 验证当前 Lean fixture/checker 语义投影；真实内容漂移仍 fail closed。tracked sidecar 是 `benchmarks/paper/lean_environment_semantic_authority.v1.json`。 |
| `fixed_plan.py`、`split_strategy.py` | 校验预注册 fixed lemma-DAG；不从任意 theorem 自动发现完整引理图。Task 33 只在历史 plan authority 与当前 runtime digest 不同时写显式 `environment_authority_bridge` certificate，不改写旧 v1 authority。 |
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
| `ai_api.py` | 执行请求、attempt/provenance/usage 收集；Task 8 acquisition 路径在 secret resolution/transport 前完成 receipt/mode/output-marker、prepared consistency、prompt admission、durable prepared artifact、inventory winner 与 budget reservation/dispatch-intent 门禁，同一 semantic-slot loser 不进入 transport；Task 11 的最小 plan-out 接点让 descriptor 同时声明 execution request v1/v2 compatibility；Task 27 统一 prepared commit→reserve→resolve/observe→dispatch intent→actual count→transport 生命周期，显式发布 provider failure/terminal usage，并只用进程内 transient collector 做 secret 脱敏。 |
| `ai_api_artifacts.py` | raw/parsed/failure/provenance/usage/model record artifact。 |
| `response_bank.py` | Task 5 immutable bank object/index/opaque external locator：规范化 manifest/inventory entry/current wrapper，校验 self-excluding entry identity、role 完整性、root marker 绑定，并在流式 hash 验证后解析 bank-internal object；Task 27 继续保持 v1 bytes/digest 兼容，同时为多 semantic-slot acquisition 提供权威 canonical inventory 排序/摘要。 |
| `trace_backed.py` | Task 11 零 provider trace executor 与 parent stager：冻结 planned unit/replacement 到 immutable bank entry 的 `TraceSourceBinding`，流式校验外部对象并准备 delivery；parent-only 阶段写 current provenance、source trace attribution 与领域 parser/checker/canonical refs，缺 slot、binding 冲突或非 current artifact 时 fail closed。 |
| `ai_api_replay.py` | 从 artifact 或显式绑定的 external response bank 恢复结果，不重新调用 API；bank replay 要求相同 root binding。 |

Secret 只能进入当前进程环境和脱敏后的 transport；event/artifact/SQLite/log/config digest 不得保存 secret。

## `tokenshare.experiments`

### 通用回归与领域 adapter

| 文件组 | 职责 |
|---|---|
| `models.py`、`runner.py`、`report.py`、`metrics.py`、`simulation.py` | 早期通用实验/regression API；不能直接当论文指标。 |
| `factorization_adapter.py`、`lean_adapter.py` | 通用实验 adapter。 |
| `factorization_paper_adapter.py`、`lean_paper_adapter.py` | 论文兼容薄壳：构造 `ProtocolRunRequest`、调用 coordinator、投影旧 shape；Task 11 增加 trace runtime adapter/execution bridge 与 parent-owned domain stage；Task 19 正式接入 typed trace context、`TraceBackedParentStager`、logical scheduler 和公开 coordinator，使 Factorization parser/verifier 与 Lean parser/checker/canonical/merge/settlement 走正常生命周期；Task 27 删除 eager API-key snapshot，仅透传每 root transient collector/callback，Factor/Lean split/check/merge/requeue 仍由原生命周期拥有。 |
| `factorization_500_ai.py`、`lean_ai_benchmark.py`、`ai_profile.py` | 直接 benchmark/diagnostic；不是论文协议结果。 |

### 论文条件与身份

| 文件 | 职责 |
|---|---|
| `paper_models.py` | paper condition/result/budget/fault schema；Task 18 新增版本化 evidence classification/eligibility facts 与 evaluator：online 逐 executed unit 绑定 current real provider attempt/lifecycle，trace 逐 unit/replacement/entry 绑定 canonical manifest、source provenance、receipt creator且 current calls=0；Factor/Lean success 与 provider failure 按 canonical domain/terminal kind 使用不同 lifecycle truth table，旧 schema 不得升级。 |
| `paper_catalog.py`、`paper_factorization_catalog.py` | catalog 加载、manifest、selection 与 oracle/preflight；Lean 路径先验证 Task 33 semantic authority，再复用既有 v1 environment/checker authority。 |
| `paper_factorization_sampling.py` | Factorization 分层稳定评分、采样 profile 校验与 immutable catalog slice；不拥有全 suite 各实验题量。 |
| `paper_catalog_execution_view.py` | 冻结规划时 catalog view，供 execute/resume/replay 使用同一 body/digest。 |
| `paper_experiment_contracts.py` | 冻结 selection、execution context 和 contract digest。 |
| `paper_suite_scale.py` | 加载 `paper_suite_scale_profile.v1.json`，从不可变 catalog 按 difficulty 内稳定 hash 物化 Exp1–4 Factorization scopes，并绑定 active Exp5 v4 selection；缺失/旧 profile 不得静默回退。 |
| `paper_model_identity.py` | experiment-layer endpoint/reasoning identity 与 pre/post-call audit。 |
| `paper_model_policy.py` | Exp5 cohort/entry map/preflight；当前为四模型 v3。 |
| `paper_unit_commitments.py` | plan/condition/request/attempt 的 AI unit binding。 |
| `paper_pipeline_profile.py`、`lean_catalog_audit.py` | EPD-027 tracked profile 与 Lean catalog audit authority；Task 33 保留历史 raw/content provenance，同时用 semantic sidecar、catalog/selection/matrix commitments 验证当前 checkout。 |

### 执行、故障与终态

| 文件 | 职责 |
|---|---|
| `paper_runner.py` | 展开 Exp1–5 condition/repeat/seed/selection 和 dispatcher plans。 |
| `paper_dispatcher.py` | 把 paper case/scope 交给 system runtime；Task 19 增加可选 typed trace context 的原样透传，默认 online/历史路径不变，不访问 coordinator 私有 ABI。 |
| `paper_runtime_clock.py` | Task 9 冻结 logical source-latency 1x 与 online real-clock policy；trace 路径拒绝 real sleep/noop sleeper。 |
| `paper_formal_runner.py` | 正式/smoke suite orchestration、preflight、dispatch、checkpoint、resume/replay；Task 19 在 engine/event 前完成 terminal bank/case digest preflight并走正常 Factor/Lean lifecycle；Task 22 以 external opaque locator 驱动 trace，按 condition/per-worker lane 聚合 logical runtime；Task 27 增加 per-root callback factory、原生双域 online/trace 执行和 canonical direct/protected replay closure，online root 全局单并发但不改 condition 的协议 `worker_count`。 |
| `paper_formal_callbacks.py` | provider/executor 与协议 lifecycle callback 绑定；Task 27 composite 只观察/组合 official hooks，发布 success/failure/current refs，支持 pre-intent release、terminal settle 与 crash/reconcile，不复制 Exp3 fault/death/requeue 状态机。 |
| `paper_formal_evidence.py` | 正式 evidence store、manifest、checkpoint 和完整性校验；Task 18 接入版本化 eligibility；Task 20 构造 protected `CanonicalLineageInput`/`LineageSourceIndex`；Task 22 保持 external source acquisition/current execution typed binding 分离；Task 27 把 authoritative lineage rows 与 metric projection rows 分离，并在 comparison boundary 严格处理冻结 alias/identity。 |
| `paper_formal_checkpoint.py` | generation v3 root-delta checkpoint、resume 与 terminal streaming SQLite compaction；Task 22 逐 root 释放 full outcome，并从真实 terminal、Task20 source-index/observations/manifest/output refs 与 Task21 renderer manifest/artifacts 重算 resume digest closure，mutation fail closed。 |
| `paper_faults.py`、`paper_workers.py` | 五类 rate-fault 与 worker-death 的预注册 hook/投影。 |
| `paper_exp1.py`、`paper_exp2_scalability.py`、`paper_exp3_fault_recovery.py`、`paper_exp4_ablation_runner.py` | 各实验的独立行为/指标 helper。 |
| `paper_ablation.py` | `FULL + 4` protocol mechanism policy。 |
| `paper_terminal_outcomes.py` | succeeded/failed/blocked/incomplete 终态语义。 |
| `paper_smoke.py` | smoke profile、identity 与非论文执行；Task 27 抽出无副作用 Exp5 capability authority builder，legacy CLI 与统一 pipeline 共用同一权威身份，仍保持 smoke/pilot/regression-only。 |
| `paper_exp5_smoke_evidence.py` | Exp5 v4 8-root smoke 的 artifact/event/hash-chain/identity evidence validator；raw LedgerEvent 保持原 envelope/hash，实验分类通过 task binding 关联。 |

`paper_formal_runner.py` 是当前最大风险热点。未来拆分优先提取纯 preflight、dispatch、evidence finalization 边界；任何拆分先锁 characterization tests，禁止复制一套生命周期。

### 指标与报告

| 文件 | 职责 |
|---|---|
| `paper_projection.py` | 从 system projection/events/artifacts 派生 task/attempt rows。 |
| `paper_direct_results.py` | Task 3 canonical facts projector：从正式 runtime result、verified ledger/artifact/parser facts 构造 deep-immutable direct rows；Task 23 增加严格 additive expanded-root merge 分支；Task 27 的 resource-book artifact v2 支持同 root 多 entry 和可选 failed final，同时保持单 entry v1 bytes/digest 不变，并让 zero-success/failed/resume 保留固定分母。 |
| `paper_historical_fixture.py`、`historical_real_factorization_single_leaf.py` | Task 23 历史真实 Factor single-leaf 离线回归：冻结最小脱敏 fixture/source digests，经正常 coordinator/parser/verifier/canonical/merge/ledger 与 central direct projector 运行；只标记 `regression_only`、`paper_eligible=false`，拒绝 range/trace-paper 语义且 provider=0。 |
| `paper_metric_contract.py` | Task 2 machine-readable metric contract loader/evaluator；Task 12 同步 current pipeline profile digest；Task 14 修正 Exp3 started/reassignment backlink；Task 20 以 scoped `MetricComputationTrace` 捕获真实 `MetricObservationBundle`、row/cell identity、membership 与 evaluation，不修改任何公式/null/invariant 语义。 |
| `paper_exp1_metrics.py` | Task 12 standalone Experiment 1 pure observation projector：按 domain/difficulty/topic/repeat 消费 canonical direct rows 与已物化 timing/provider facts，保留全部预注册 roots 的固定分母，wrong final 只计 completion，infra invalid 与资源缺失显式 null/block；不 import formal runner/renderer，不修改 registry，draft 默认 `paper_eligible=false`。 |
| `paper_exp2_metrics.py` | Task 13 standalone Experiment 2 pure trace/online observation projectors：按 digest-bound case ref、repeat/sample、worker 与 position stratum 投影固定分母、logical makespan、paired speedup/efficiency、committed trace slot/token/cost、调度利用率与 online first-attempt 429/timeout union；失败与 missingness 显式保留，完整 main trace 前 post-bank check 为 `not_evaluated_pre_bank`，不 import formal runner/renderer、不修改 registry，draft 默认 `paper_eligible=false`。 |
| `paper_exp3_metrics.py` | Task 14 standalone Experiment 3 pure trace/online recovery projectors：投影 fixed controlled-candidate denominator、完整 replacement/reassignment backlink、same-sample paired trace absolute overhead、discarded trace tokens、worker-death completeness/kill error，以及 ordered current-provider identity/usage/cost/wasted actual tokens；缺 provider object/role 时六个 online resource cells 统一 null/block，不 import formal runner/renderer，不复制协议状态机。 |
| `paper_exp4_metrics.py` | Task 15 standalone Experiment 4 pure pair/projector：按 domain/case/repeat/sample/replacement identity 将四个 ablation 各配对唯一 FULL，保留全部预注册失败与四 transition，输出 FULL−ablation success/completion loss、ablation−FULL trace resource delta 和四 mode 专项 denominator；duplicate ablation identity 全部保留但统一 null/block，不从 mode 名称合成 hook/outcome。 |
| `paper_exp5_metrics.py` | Task 16 standalone Experiment 5 pure quality/resources projector：直接使用 canonical `PaperModelEndpointIdentity` 官方 digest/fields，按 actual first attempts、checkable candidates 与全部 preregistered roots 投影冻结 denominator；以 enclosing protocol wall-clock、3 raw repeats 与 median/min/max/range 生成恰好两张 ordered payload/caption，禁止 pairwise/significance/ranking/retry/recovery/accepted-validity。 |
| `paper_metric_registry.py` | Task 17 contract-bound projector registry：把 8 张正式 table 唯一映射到 Tasks12–16 accepted projectors；Exp3 trace/online 分别薄调用 Task14，Exp5 quality/resources 共用一次 Task16 projection；逐 row-kind 精确核对 metric inventory，禁止在 registry 重算公式。 |
| `paper_metrics.py` | evidence-derived 通用统计与 integrity validation。 |
| `paper_formal_metrics.py` | Task 17 正式 Exp1–5 metric draft 发布路径；Task 20 内部构造 protected canonical lineage inputs、捕获真实 evaluator traces、join persisted source index，并原子发布 lineage/observations/manifest；Task 27 只在 Exp1 alias 与 Exp2 trace/online metric view 做严格 identity bridge，authoritative rows/source index 保持原 ID，Exp3–5 对象和值不变。 |
| `paper_metric_observations.py` | Task 20 每 numeric cell 唯一 lineage materializer：按 row/cell digest 精确 join，保存公式/value/numerator/fixed denominator membership/excluded-null-blocked reasons，并逐 current execution 或 source entry 校验 typed roles；non-null 由既有 evaluator独立复算，缺 role 时 cell null/table blocked。 |
| `paper_traceability.py` | Task 24 deterministic traceability replay：仅接受 runner 生成的 protected persisted L4 descriptor，fresh reload并独立重算三类 digest；Task 27 闭合历史 `artifacts/<file>.bin` 与当前 `artifacts/<task>/<content-name>` 两种权威 generation，hash 必验且存在的 size 严格校验；zero-dispatch 只接受至少一个 exact `PaperDirectRootResult` 且全部 `not_started`。 |
| `paper_online_checks.py` | Task 25 capability/Exp2/Exp3 online-check pure plan 与 typed evidence producer：冻结4/24-480/2-12 scope，消费官方 verifier/checker/requeue 与 callback persisted objects，资源输入仅来自 current provider payload；缺 role blocked/null，不计算指标公式、不创建receipt、不dispatch。 |
| `paper_paid_authorization.py` | Task 26 纯离线 external receipt validator：验证 canonical digest 与scope/plan/profile/budget/inventory/admission/selection/path/expiry/approval binding，派生new-run/resume marker并要求独立allow flag；无mint/approve/secret/reserve/dispatch API，synthetic tests不构成真实授权。 |
| `paper_formal_gate.py` | Task 28 typed execution/publication gate。execution 只消费运行前 authority/receipt/bank prerequisites，不循环依赖未来 L4/table/post-bank；publication 只在 terminal evidence 后检查。缺 receipt/L1–L4/bank/terminal 时稳定 BLOCKED，facility/capability 永远不能升级 formal PASS。 |
| `paper_report.py`、`paper_formal_report.py`、`paper_smoke_report.py` | 通用/正式/smoke 输出；Task 21 正式 report 只接受持久化 Task20 lineage/source-index/observation/manifest/digest 闭包，验证 exact 8 table completeness 后发布 renderer/result/eligibility/secret-audit refs；smoke 永远 paper-ineligible。 |
| `paper_exp5_artifacts.py`、`paper_exp5_model_comparison.py`、`paper_exp5_statistics.py` | Task 21 contract-only CSV/TEX renderer 位于 `paper_exp5_artifacts.py`：从 typed observations 生成 exact 8 CSV 与同 stem TEX、audit JSONL/manifest，保留 null/denominator/reason，整批 stage/promote/rollback；旧 pairwise/ranking/recovery/Markdown/PDF 输出已退役。Task 33 让历史 Exp5 v3 parent 继续保留原 provenance，同时由 semantic sidecar 与 readiness commitments 校验当前 Lean source；其余为历史比较/统计 helper。 |
| `paper_budget.py` | plan-only roots/units/attempt/token/cost/time/space 预算与门禁；Task 6 把完整 bank inventory 写入预算投影，Task 7 增加 provider-writing 硬预算 limits 与禁止 unlimited mode 的入口门禁。 |
| `paper_response_bank.py` | Task 6 complete semantic-slot inventory/zero-engine preflight；Task 8 acquisition orchestrator；Task 19 terminal completeness/case binding；Task 27 增加 pure prepared-request acquisition plan、versioned terminal bank identity、全 inventory cross-binding 与 `acquire_all/reconcile`，fresh child 只初始化一次、resume 只重开验证，并保留历史 `PaidAcquisitionContext` API 兼容。 |
| `paper_budget_ledger.py` | Task 7 SQLite WAL atomic budget authority；Task 8 接入 acquisition/reconcile；Task 27 委托 shared canonical inventory digest，并增加 opt-in 持久 category policy：primary reservations≤496、ambiguous reacquisitions≤20、合计≤516，reopen drift fail closed，所有检查/写入在 `BEGIN IMMEDIATE` 内完成；无 policy 的 full-bank 行为兼容。 |
| `paper_resource_accounting.py` | Task 7 按 frozen pricing 核算 terminal provider usage；usage 缺失时保留完整 token/CNY reservation upper，避免少计实际 acquisition spend。 |

正式规模和资源边界由 `paper_suite_scale.py`、`paper_budget.py`、`paper_formal_checkpoint.py` 与 `paper_formal_metrics.py` 共同约束：当前 Exp1–5 精确总量为 `6,384 roots / 40,520 units / 81,272 attempt upper`，最大单 condition 为 `100/1,000/1,000`；generation 逐 root delta、terminal SQLite streaming compaction、metrics lazy bundle mapping 和 JSONL/chunked scan 使内存按最大单 outcome/当前 bundle 定界，而不是把全 suite 同时载入。磁盘 forecast/compaction/safety reserve 必须写入 `run_budget.json` 并在 provider dispatch 前检查目标卷。

### EPD-027 实施进度与后续实验设施改造

截至 2026-08-04，EPD-027 的离线设施状态是 `facility_offline_implemented`：request identity、不可变 response bank、SQLite budget/acquisition、trace-backed executor、双 provenance/evidence class、正式 projectors/renderer/replay、online-check plan、paid receipt validator、统一 pipeline、typed gates 和四级验证 profile 均已接入原 TokenShare 生命周期，没有复制影子状态机。`online_real_provider` 与 `real_model_trace_protocol_run` 的 schema/producer 已实现，不表示已经产生任何正式论文结果。

Task 33 的 owning fix commit=`a10e988d`（后续 ordinary-ref 修复=`7c9dff7c`）共覆盖 12 个 semantic-authority/certificate-bridge source/sidecar/test 文件：既有 v1 raw authority 保持不变，当前 checkout 通过 semantic projection 验证。Task 34 复核结果为 L1=`441 selectors / 882 passed`、L2=`8 passed`；因 `local/epd027_l3_paid_receipt.local.json` 不存在，L3=`l3_new_real_smoke_blocked`，L4=`l4_cell_traceability_blocked`。L4 的 public profile 只用既有 L3 diagnostic directory 证明缺 `suite_manifest.json` 时 fail closed；该目录不是 immutable L3 formal output，也不是 L4 replay input。

当前没有 user-provided、Task 26-validated 且 scope-matched 的 paid receipt；Task 34 execution/publication gate 都以 typed authority 返回 BLOCKED、没有 dispatch，provider/network=`0/0`。因此正式矩阵保持 **NO-GO**。`facility_offline_implemented`、L3/L4 BLOCKED 与 `formal_matrix_no_go` 是四个独立状态，任何 capability/facility/历史 evidence 都不能升级 formal publication PASS。

指标不得使用固定协议时间、自填成功字段或丢失失败/未开始分母；所有汇总必须能回到逐 task/attempt/event/artifact。

### CLI

| 文件 | 默认输出类别 |
|---|---|
| `run_paper_pipeline.py` | Task 27 统一 14 个正式命令的 parser/delegation/门禁入口；Task 28 接入 process-local typed execution/publication gate，Task 29 修正 selected-experiment receipt scope/terminal binding；unknown/missing 为 exit 2，正式资源阻塞为 JSON/exit 3；offline/provider、receipt/mode/secret/budget 顺序 fail closed。 |
| `run_paper_experiments.py` | legacy CLI 委托 Task 27 同一 authority/service map；必须显式指定全新 `--output-root`，历史默认目录只作 smoke 隔离边界与容器 |
| `run_all.py` | `<data-root>/outputs/experiments/` |
| `run_ai_profile.py` | `<data-root>/outputs/experiments/ai_profile` |
| `run_factorization_500_ai.py` | `<data-root>/outputs/experiments/factorization_500_ai` |
| `run_lean_ai_benchmark.py` | `<data-root>/outputs/experiments/lean_ai_50` |

通用 CLI 的显式输出参数优先于默认 data root；paper runner 必须显式指定全新 `--output-root`。

## 配置、数据与脚本

| 位置 | 内容 |
|---|---|
| `benchmarks/paper/` | tracked catalogs、selection、safe provider config、cohort、smoke/profile authority；active 规模为 `paper_suite_scale_profile.v1.json`，EPD-027 为 `epd027_pipeline_profile.v1.json`，Lean 跨 checkout authority sidecar 为 `lean_environment_semantic_authority.v1.json`。Exp5 active selection/smoke 为 v4，历史 v1/v3 文件只供 replay/provenance。 |
| `local/*.local.json` | gitignored secret/local config；不得归档或迁入 tracked 文档。 |
| `local/run_*_smoke.ps1` | 真实 smoke launcher；输出和 supervision 可使用外部绝对路径。 |
| `verification/` | Fast/Full runner、Fast/Lean canary manifest、provider/network tripwire，以及 EPD-027 L1/L2/L3/L4 四个 exact-node focused profile；L3/L4 prerequisite 不满足时必须显式 BLOCKED，不能静默跳过或写 PASS。 |
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
