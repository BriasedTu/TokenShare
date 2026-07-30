# TokenShare V1 当前 Code Map

更新时间：2026-07-30

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
| `models.py` | Task/Unit/Attempt/Lease/Artifact/Event 等协议数据对象与版本化序列化。 |
| `state_machines.py` | 对象状态转换规则。 |
| `task_graph.py` | DAG、依赖和任务图不变量。 |
| `scheduling.py` | Ready unit 选择与调度纯规则。 |
| `leases.py` | lease/heartbeat/fencing 规则。 |
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
| `events.py` | append-only JSONL event ledger、读取和校验。 |
| `artifacts.py` | content-addressed artifact、manifest、hash/URI 验证。 |
| `sqlite_index.py` | 从 ledger 构建/重建 SQLite 查询索引；不是第二状态源。 |

任何非确定性 executor 输出先落 artifact，再由 event 引用。Replay 读取持久化事实，不重新调用 executor/provider。

## `tokenshare.local_runtime`

| 文件 | 职责 |
|---|---|
| `contracts.py` | `ProtocolRunRequest`、execution scope、plugin hooks、worker/backend 等稳定接口。 |
| `coordinator.py` | 组装 scheduler/lease/executor/plugin/engine，推进完整本地协议生命周期。 |
| `workers.py` | sequential/thread/process worker、liveness、真实 process death 与 capacity。 |
| `process_worker_child.py` | Windows 独立子解释器 worker 的原子文件 handshake/result sidecar。 |
| `projection.py` | 从 ledger/artifacts 派生通用 run/unit/attempt 只读视图。 |

worker backend 只报告执行和死亡事实；是否 retry/requeue 由协议规则决定。不要把 Windows process 退回 multiprocessing spawn pipe/Event 路径。

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
| `ai_api_transport.py` | DeepSeek/OpenAI-compatible/SiliconFlow transport。 |
| `ai_api.py` | 执行请求、attempt/provenance/usage 收集。 |
| `ai_api_artifacts.py` | raw/parsed/failure/provenance/usage/model record artifact。 |
| `ai_api_replay.py` | 从 artifact 恢复结果，不重新调用 API。 |

Secret 只能进入当前进程环境和脱敏后的 transport；event/artifact/SQLite/log/config digest 不得保存 secret。

## `tokenshare.experiments`

### 通用回归与领域 adapter

| 文件组 | 职责 |
|---|---|
| `models.py`、`runner.py`、`report.py`、`metrics.py`、`simulation.py` | 早期通用实验/regression API；不能直接当论文指标。 |
| `factorization_adapter.py`、`lean_adapter.py` | 通用实验 adapter。 |
| `factorization_paper_adapter.py`、`lean_paper_adapter.py` | 论文兼容薄壳：构造 `ProtocolRunRequest`、调用 coordinator、投影旧 shape。 |
| `factorization_500_ai.py`、`lean_ai_benchmark.py`、`ai_profile.py` | 直接 benchmark/diagnostic；不是论文协议结果。 |

### 论文条件与身份

| 文件 | 职责 |
|---|---|
| `paper_models.py` | paper condition/result/budget/fault/eligibility schema 和 digest。 |
| `paper_catalog.py`、`paper_factorization_catalog.py` | catalog 加载、manifest、selection 与 oracle/preflight。 |
| `paper_catalog_execution_view.py` | 冻结规划时 catalog view，供 execute/resume/replay 使用同一 body/digest。 |
| `paper_experiment_contracts.py` | 冻结 selection、execution context 和 contract digest。 |
| `paper_model_identity.py` | experiment-layer endpoint/reasoning identity 与 pre/post-call audit。 |
| `paper_model_policy.py` | Exp5 cohort/entry map/preflight；当前为四模型 v3。 |
| `paper_unit_commitments.py` | plan/condition/request/attempt 的 AI unit binding。 |

### 执行、故障与终态

| 文件 | 职责 |
|---|---|
| `paper_runner.py` | 展开 Exp1–5 condition/repeat/seed/selection 和 dispatcher plans。 |
| `paper_dispatcher.py` | 把 paper case/scope 交给 system runtime。 |
| `paper_formal_runner.py` | 正式/smoke suite orchestration、preflight、dispatch、checkpoint、resume/replay。 |
| `paper_formal_callbacks.py` | provider/executor callback 绑定。 |
| `paper_formal_evidence.py` | 正式 evidence store、manifest、checkpoint 和完整性校验。 |
| `paper_faults.py`、`paper_workers.py` | 五类 rate-fault 与 worker-death 的预注册 hook/投影。 |
| `paper_exp1.py`、`paper_exp2_scalability.py`、`paper_exp3_fault_recovery.py`、`paper_exp4_ablation_runner.py` | 各实验的独立行为/指标 helper。 |
| `paper_ablation.py` | `FULL + 4` protocol mechanism policy。 |
| `paper_terminal_outcomes.py` | succeeded/failed/blocked/incomplete 终态语义。 |
| `paper_smoke.py` | smoke profile、identity 与非论文执行。 |

`paper_formal_runner.py` 是当前最大风险热点。未来拆分优先提取纯 preflight、dispatch、evidence finalization 边界；任何拆分先锁 characterization tests，禁止复制一套生命周期。

### 指标与报告

| 文件 | 职责 |
|---|---|
| `paper_projection.py` | 从 system projection/events/artifacts 派生 task/attempt rows。 |
| `paper_metrics.py` | evidence-derived 通用统计与 integrity validation。 |
| `paper_formal_metrics.py` | 正式 Exp1–5 指标表生产路径。 |
| `paper_report.py`、`paper_formal_report.py`、`paper_smoke_report.py` | 通用/正式/smoke 输出；smoke 永远 paper-ineligible。 |
| `paper_exp5_artifacts.py`、`paper_exp5_model_comparison.py`、`paper_exp5_statistics.py` | Exp5 v3 artifact、比较与统计。 |
| `paper_budget.py` | plan-only roots/units/attempt/token/cost/time/space 预算与门禁。 |

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
| `benchmarks/paper/` | tracked catalogs、selection、safe provider config、cohort、smoke profiles。 |
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
