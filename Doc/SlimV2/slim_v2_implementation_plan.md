---
status: approved_under_user_delegation
document: slim_v2_implementation_plan
scope: Slim V2 system assembly and offline focused verification
owner: Stage 2 blueprint rewrite owner
created: 2026-08-21
last_updated: 2026-08-21
run_scope: representative_only
---

# TokenShare Slim V2 实施蓝图

本计划取代旧横向微任务工作分解。实施以六个风险驱动纵向里程碑推进，每个里程碑都交付一条可运行切片；测试只证明高风险业务行为和跨模块合同，不为内部 helper、DTO、配置或覆盖率制造施工步骤。

冻结勘误已经纳入本计划：Representative Experiment 1 为 19 planned units、真实调用上限 57，Experiment 5 上限 32，总上限 89。Full Experiment 3 为 rate 13,920、worker death 2,808、合计 16,728 planned units，attempt upper 为 `3×13,920 + 4×2,808 = 52,992`；其 106 个辅助 references 的 planned/upper 固定为 468/1,404。Full Experiment 4 每 mode/repeat 293 units、合计 9,669，attempt upper 为 15,822。其余 case IDs、root/condition 数、153 个 metric IDs、实验变量和 `run_scope` 不变。所有 planned/upper 值在运行时仍必须由逐 root inventory 求和，不用这些摘要常量覆盖实际 profile。

## 1. 目标、范围和非目标

目标只有一个：用最少的新代码，让冻结 Experiment 1–5 真实经过现有 TokenShare 系统本体、Factorization/Lean 插件和 checker，增量保存权威指标所需的普通数据，并由单一 reducer 生成结果。

实施范围：

- 每个论文 root 在正常执行中精确调用一次 `ProtocolRunCoordinator.run_root()`；
- Factorization 与 Lean 都通过现有 domain runtime/bridge、worker backend、`ProtocolEngine`、`EventLedger` 和 `ArtifactStore`；
- Experiment 1/5 使用同一 single-entry bounded provider caller；Experiment 2–4 只读 Experiment 1 trace，provider calls 固定为 0；
- 每个 root 串行，root 内并发由现有 worker backend 承担；
- 用普通原子 root/trace 文件和最小 provider terminal journal 支持恢复和防重复付费；
- reducer 只读一个 run 目录，输出指标权威冻结的 153 个 metric IDs；
- `representative` 与 `full` 使用完全相同的入口、装配、schema、恢复和 reducer。

明确非目标：

- 不复制或改写协议状态机、event ledger、artifact store、worker、recovery、canonical、merge、settlement 或领域 checker；
- 不建立 Slim task/attempt/retry/root 状态机、通用 workflow framework、service、daemon、HTTP API、数据库 authority 或插件框架；
- 不引入 budget、receipt、digest/lineage、evidence closure、response-bank authority、publication gate、paper eligibility；
- 不修改 shared core/local_runtime/plugin/executor，除非后续出现可复现的公共接口缺口并按接力协议取得授权；
- 不把 Thread 尚未证明解释成 Thread 已损坏；不把 bounded-process facade 当成默认建设项；
- 不运行真实 provider、representative/full、Lean 专项 suite、LeanAudit、全量 catalog、`lake` 或 `lean` 作为本实施阶段的验证；
- 不防御设计宪章第 0.1 节排除的人为伪造、恶意篡改、注入、恶意 provider/plugin 或攻击者模型。

## 2. 最终文件树、运行目录和公开入口

最终源码保持为一个薄实验包；已有文件优先校准，只有纵向闭环确实需要时才创建新文件。

```text
src/tokenshare/experiments/slim_v2/
├── __init__.py       # 公开版本和少量稳定导出
├── schema.py         # 冻结 inventory/result/trace/call 普通数据合同
├── case_source.py    # 当前 catalog 的 UTF-8 读取与显式 ID 选择
├── profiles.py       # full/representative 冻结 inventory 与逐 root plan 派生
├── storage.py        # run 目录、原子 root/trace、terminal-call journal、resume 扫描
├── provider.py       # single-entry bounded call、usage/model、冻结价格投影
├── execution.py      # provider/fixed answer 到公共 ExecutionSubmission 的薄适配
├── scenarios.py      # Exp2 scheduler、Exp3 hooks/death、Exp4 policy/challenge
├── runtime.py        # 每 root 公共对象装配、唯一 run_root 调用、Exp1 tail
├── projector.py      # 只读公共事实并生成 RootResultV1
├── reducer.py        # 流式统计内核和 Exp1–5 全部表
└── cli.py            # plan/run/run-all/reduce/representative 原子入口

tests/experiments/slim_v2/
├── fixtures/                    # 小型 Factorization、Lean fixed-DAG、provider、golden run
├── test_schema.py               # 已有 schema 合同，Task 2 校准
├── test_case_source.py          # 已有 catalog/ID 合同
├── test_profiles.py             # 已有 profile/inventory/派生上限
├── test_cli_plan.py             # 已有只读 plan
├── test_system_vertical.py      # Task 1 两领域真实系统纵链
├── test_answer_paths.py         # Task 3 provider/fixed/tail/Exp5
├── test_scenarios.py            # Task 4 Exp2–4、Thread/Process
├── test_reducer_golden.py       # Task 5 153 IDs 与统计 golden
└── test_cli_e2e.py              # Task 6 全离线原子命令/resume
```

允许在实施中合并重复测试或把 fixture 移到同目录，但不得为测试计数拆文件。若一个候选模块只有一个调用点且不能证明单一职责，优先并入上述拥有者，不新增文件。

最终 run 目录：

```text
TokenShareData/outputs/slim_v2/<run_id>/
├── run.json
├── inventory/
│   ├── conditions.jsonl
│   ├── roots.jsonl
│   ├── exp3_references.jsonl
│   └── exp4_challenges.jsonl
├── system/<experiment>/<root_key>/
│   ├── events.jsonl
│   └── artifacts/...
├── roots/<experiment>/<root_key>/
│   ├── protocol.json             # run_root 返回后的不可变普通投影
│   └── result.json               # tail/投影完成后的 committed root
├── traces/exp1/<case_id>/0/<planned_ai_unit_id>.json
├── calls/<call_key>.intent.json
├── calls/<call_key>.terminal.json
├── responses/<call_key>.json
├── references/exp3/<root_key>.json
└── metrics/
    ├── tables/*.jsonl
    ├── tables/*.csv
    └── summary.json
```

`system/` 是现有系统本体自己的 event/artifact 存储，不是 Slim journal，reducer 不读取。`protocol.json`必须包含从公共结果/ledger/store投影出的全部 protocol-origin unit 普通attempt与领域语义快照，使resume可以幂等重建缺失trace文件；它不是协议checkpoint或第二状态。Slim 不创建 `state/` 状态机目录、checkpoint 父链、CURRENT/PENDING、receipt 或 database。

最终 CLI：

```text
python -m tokenshare.experiments.slim_v2.cli plan --profile <representative|full> --run-id <id> [--output-root <dir>]
python -m tokenshare.experiments.slim_v2.cli run --experiment <exp1|exp2|exp3|exp4|exp5> --profile <p> --run-id <id> [--source-run-dir <dir>] [--output-root <dir>] [--resume]
python -m tokenshare.experiments.slim_v2.cli run-all --profile <p> --run-id <id> [--output-root <dir>] [--resume]
python -m tokenshare.experiments.slim_v2.cli reduce --run-dir <dir>
python -m tokenshare.experiments.slim_v2.cli representative --run-id <id> [--output-root <dir>] [--resume]
```

package-level re-export 只保留`SCHEMA_VERSION`。获支持的 module-qualified Python 调用点只保留`profiles.build_profile/build_inventory/build_plan`、`runtime.run_root_slice/run_coverage_tail`、`provider.call_provider_once`、`storage.select_trace_attempt`、`cli.run_experiment/main`和`reducer.reduce_run`；其余表内名称是模块合同类型或内部装配符号，不扩展成通用公共框架。没有 service 入口、后台 worker service、HTTP server 或第二个 runner。

Canonical condition ID编码集中冻结如下；ID只用于身份，任何行为必须读取结构化condition字段：

| 实验 | condition ID维度 |
|---|---|
| Exp1 | domain/stratum；不编码repeat |
| Exp2 | worker×repeat×position；不编码domain |
| Exp3 | 先domain+difficulty/topic stratum，再编码fault/rate/repeat或death/progress/repeat |
| Exp4 | 只编码mode×repeat |
| Exp5 | model×repeat×stratum |

## 3. 文件职责与公开符号

| 文件 | 单一职责 | 公开符号 | 拥有的 Slim-local 状态 | 明确不负责 |
|---|---|---|---|---|
| `__init__.py` | 包版本与稳定导出 | `SCHEMA_VERSION` | 无 | 不导入 CLI，不启动运行 |
| `schema.py` | 普通root/attempt/trace/provider数据结构和结构校验 | 既有或同类模块合同类型`RootInventoryV1`,`RootResultV1`,`AttemptResultV1`,`UnitTraceV1`,`ProviderEntryViewV1`,`ProviderCallResultV1`,`ProviderCallContextV1` | 无运行状态 | 不拥有profile plan，不推进root/attempt，不判断协议终态；这些类型不要求package-level re-export |
| `case_source.py` | 读取当前 catalog 并按冻结 ID 取 case | `load_cases`,`select_cases_by_ids` | 当前调用的流式读取状态 | 不运行 legacy selection，不做资格判断 |
| `profiles.py` | 生成冻结 condition/root/reference/challenge inventory 和 plan | 既有`ProfileV1`,`InventoryV1`,`PlanV1`及其组成类型；`build_profile`,`build_inventory`,`build_plan` | 只读 literal case IDs | 不把类型搬到schema，不读 secret，不创建 runtime/provider，不把 condition ID 当行为来源 |
| `storage.py` | 普通 run 文件写读、原子键和 resume 扫描 | `RunStore`,`ResumeView`,`SelectedTraceAttemptV1`,`scan_resume`,`select_trace_attempt` | committed root/trace key 与 call intent/terminal 文件 | 不判断协议 status，不替代 ledger，不 repair/compact |
| `provider.py` | 一次 bounded HTTP 调用和该 attempt 的 usage/model/cost 投影 | `ProviderEntryViewV1`,`call_provider_once`,`project_cost` | 仅当前调用栈；journal由注入的`RunStore`拥有 | 不内部 retry，不另建store，不选择 cohort，不做预算或领域验证 |
| `execution.py` | 把 online/fixed answer 转成系统 `ExecutionSubmission` | `ProviderSubmissionAdapter`,`FixedTraceSubmissionAdapter` | 当前 execution request 的临时输入 | 不创建协议 attempt，不决定 canonical/recovery |
| `scenarios.py` | 把冻结 Exp2–4 condition 翻译为既有 scheduler/hooks/policy/backend 参数 | `build_scenario`,`build_exp3_reference`,`build_challenge_plan` | 当前 root 的 fault/challenge observations | 不生成 root/canonical/recovery/death事实，不解析 condition ID |
| `runtime.py` | 组装一个 root、调用一次 `run_root`，并在 Exp1 terminal 后运行 tail | `RootAssembly`,`TailSummaryV1`,`run_root_slice`,`run_coverage_tail` | 当前 root 对象和 tail target 集合 | 不实现 engine/worker/checker/merge，不跨 roots 保留 plugin 状态 |
| `projector.py` | join 当前 root 的 result/ledger/store/plugin/hook事实 | `project_root_result` | 仅当前 root 的只读 join 缓冲 | 不制造缺失事实，不把 status 猜成正确性 |
| `reducer.py` | 从一个 run 目录生成 153-ID 指标表 | `reduce_run`,`metric_ids` | 当前 table/slice 的流式聚合与 bootstrap 样本 | 不读 system events/raw response，不调用 runtime/checker/provider |
| `cli.py` | 解析原子命令、串行 roots、依赖与 preflight | `run_experiment`,`main` | 当前命令上下文 | 不拥有协议状态，不隐式运行 Exp1，不提供 service |

`schema.py` 和 `profiles.py` 的已提交成果继续使用；Task 2 只删除无法映射到权威字段或最小恢复的通用抽象，不因重写蓝图丢弃 case IDs、condition IDs、153 metric-ID 合同或逐 root plan 派生能力。

## 4. 新模块之间的接口矩阵

| 上游 | 下游 | 输入类型 | 输出类型 | 调用符号 | 持久化副作用 |
|---|---|---|---|---|---|
| CLI | profiles | `profile_id,experiment_ids` | `ProfileV1,InventoryV1,PlanV1` | `build_profile/build_inventory/build_plan` | CLI 通过 `RunStore` 写 inventory |
| profiles + case source | runtime | `RootInventoryV1,catalog case` | `RootAssembly` | `run_root_slice` | 创建当前 root 的 `system/` |
| runtime | scenarios | `RootInventoryV1,ExecutionRequest` | scheduler/hooks/policy/backend参数 | `build_scenario` | 仅 observation 留在当前 root 内存 |
| runtime/worker | execution | `ExecutionRequest,answer source` | `ExecutionSubmission` | adapter `execute` | online 路径调用 provider；fixed 路径只读 trace |
| execution | provider | `ProviderEntryViewV1,prompt,controls,ProviderCallContextV1,RunStore` | `ProviderCallResultV1` | `call_provider_once` | 经注入的RunStore写intent、terminal、response原子文件 |
| execution | storage | 三元 trace key、当前 ordinal、unit 语义 | `SelectedTraceAttemptV1` | `select_trace_attempt` | 无；严格只读 |
| runtime | projector | `ProtocolRunResult,ledger,store,plugin,hooks,journals` | `RootResultV1` | `project_root_result` | 无；结果由 RunStore commit |
| runtime tail | storage/provider | unscheduled IDs、existing trace keys | `TailSummaryV1` | `run_coverage_tail` | 逐 unit trace、call journal；不写 system event |
| CLI | storage | inventory/root/trace/call对象 | committed keys / `ResumeView` | `RunStore`, `scan_resume` | 同目录 temp + atomic replace |
| reducer | storage | run目录和普通 JSON/JSONL | observation iterator | `RunStore.iter_*` | 只写 `metrics/` 输出 |

接口规则：传递 typed 普通对象，不传 global mutable registry；所有跨文件持久化都经 `RunStore`；只有 provider call journal 和 Exp1 coverage tail 是 Slim-local 非协议事实。`scenarios.py` 返回配置和 observation collector，不返回预填的运行结论。

## 5. 与现有 TokenShare 程序的接线矩阵

| 现有公共位置/符号 | Slim 调用点 | 输入 | 系统返回/真值 | Slim 边界 |
|---|---|---|---|---|
| `src/tokenshare/local_runtime/contracts.py` `ProtocolRunRequest`,`ProtocolRunResult` | `runtime.run_root_slice` | case、plugin runtime、worker、policy、hooks、scheduler | 一个 root 的公开请求/结果 | condition 标签只留 Slim inventory；不扩展 request |
| `src/tokenshare/local_runtime/coordinator.py` `ProtocolRunCoordinator.__init__`,`run_root` | `runtime.run_root_slice` | root 独占 engine/store/ledger 和 request | 完整 root 生命周期 | 正常路径每论文 root 恰好一次 `run_root` |
| `src/tokenshare/protocol_engine.py` `ProtocolEngine` | root 装配 | `ProtocolConfig,EventLedger,ArtifactStore` | task/unit/attempt/retry/recovery/canonical/merge/settlement 真值 | Slim 只配置、调用、读取、投影 |
| `src/tokenshare/storage/events.py` `EventLedger` | root 装配/projector | 当前 root events | append-only 协议事件 | 不复制 ledger，不把 ref/digest写进结果 |
| `src/tokenshare/storage/artifacts.py` `ArtifactStore` | root 装配/projector | submission/final artifact | 系统 artifact bytes | 只为取回本次 run 事实使用 ref |
| `src/tokenshare/local_runtime/workers.py` `SequentialWorkerBackend`,`ThreadWorkerBackend`,`ProcessWorkerBackend` | `runtime/scenarios` | execution adapter、capacity、termination policy | worker execution/death/liveness事实 | 不另写 worker loop；death才强制 Process |
| `src/tokenshare/local_runtime/logical_scheduler.py` `LogicalSourceLatencyScheduler` | Exp2–4 scenario | source latency、capacity | 逻辑时钟和 makespan | 不用外层除法伪造并发 |
| `src/tokenshare/local_runtime/contracts.py` `ProtocolMechanismPolicy`,`RuntimeHooks`,`WorkerTerminationPolicy` | Exp3/4 scenario | 冻结 fault/death/mode/challenge | 公共 hook/requeue/merge接缝 | observations只记录实际动作，不反填结果 |
| `src/tokenshare/plugins/factorization/runtime_adapter.py` `FactorizationRuntimeAdapter`,`FactorizationExecutionBridge` | root assembly | catalog case、answer adapter | split/parser/verifier/final product/merge真值 | Slim 不复制 range 计划、verifier 或 final product判断 |
| `src/tokenshare/plugins/lean_proof/fixed_plan.py` `LeanFixedDecompositionPlan` | Lean root assembly | catalog fixed DAG | planned lemma DAG | AI/Slim 不决定拆分 |
| `src/tokenshare/plugins/lean_proof/runtime_adapter.py` `LeanRuntimeAdapter`,`LeanExecutionBridge` | root assembly | fixed plan、answer adapter、checker | child checker、canonical、merge/root recheck真值 | Slim 不自报 proof acceptance |
| `src/tokenshare/executors/contracts.py` `ExecutionSubmission` | `execution` adapters | provider/fixed answer与parser输出 | worker/backend消费的系统 submission | 只做薄转换，不创建协议 attempt/retry |
| `src/tokenshare/executors/ai_api_transport.py` `build_deepseek_chat_body`,`build_siliconflow_chat_body`,`parse_deepseek_response`,`parse_siliconflow_response` | `provider.call_provider_once` | entry/prompt/controls或bounded raw JSON | provider-specific request body/parsed envelope | 复用builder/parser；旧UrlLib transport因无界read不直接复用，16 MiB bounded read由Slim实现 |
| `src/tokenshare/plugins/factorization/validator.py` `parse_factorization_ai_output` | provider/fixed submission adapter与tail | model content + unit语义 | Factorization parsed candidate | Slim不复制parser或从文本自报正确 |
| `src/tokenshare/plugins/lean_proof/prompt_builder.py` `parse_lean_proof_candidate_ai_output`；`src/tokenshare/plugins/lean_proof/checker.py` `check_lean_proof` | provider/fixed submission adapter与tail | model content、lemma request与固定Lean环境 | candidate/checker report | 正常root由Lean bridge拥有协议接线；tail只调用同一parse/check规则，不创建canonical |

已取得的公共接口实证作为 Task 1 起点而非替代 Gate：在 fake/local、无 provider、无 Lean/lake 下，Factorization 完整 protocol 链、Lean fixed-DAG checker/canonical/merge/root recheck和通用 coordinator 生命周期共 `4 passed in 10.86s`。Sequential 两领域 PoC 没有发现 shared 接口缺口，因此首选实现是薄 root assembly、fake submission 和只读 projector。

集中状态真值矩阵：

| 事实 | 唯一 owner | Slim 可以做 | Slim 禁止做 |
|---|---|---|---|
| root/task/attempt/retry/recovery/canonical/merge/settlement | `ProtocolEngine + EventLedger` | 配置、调用、读取、投影 | 生成第二套状态或更正 ledger |
| Factorization verifier/final product | Factorization runtime/plugin | 读取 accepted/final并独立确定性复核 | 从模型文本自报成功 |
| Lean child/root checker与merge acceptance | Lean runtime/checker | 读取 checker report/merge result | 用 protocol completed替代 accepted |
| worker execution/death/liveness | 现有 worker backend/runtime hooks | 配置冻结条件并采集 facts | 从 condition 名称生成 death/recovery |
| provider网络终态 | Slim最小 call journal | 记录intent/terminal/unknown，避免重复付费 | 创建协议attempt/retry或替代ledger |
| resume完成键 | 普通 committed root/trace文件 | 扫描并跳过已经落盘的工作 | 宣称协议状态或修改root status |
| Exp1 coverage tail | Slim-local唯一例外 | terminal后取得未调度unit trace | 写协议attempt/canonical/merge或改正文runtime |

## 6. Experiment 1–5 完整控制流

所有实验先由 `build_inventory()` 预注册 root，再由同一 `run_experiment()` 串行处理。每个论文 root 的系统路径都是：

```text
CLI/profile/case
  -> RootAssembly
  -> ProtocolRunCoordinator.run_root(request) exactly once
  -> domain execution bridge
  -> existing worker backend
  -> ProtocolEngine + EventLedger + ArtifactStore
  -> domain verifier/checker
  -> canonical/merge/root recheck
  -> read-only projector
  -> atomic ordinary root result
```

### Experiment 1

1. CLI解析唯一 DeepSeek entry并在condition开始前验证model/secret；root开始前不发请求。
2. worker执行 AI unit 时，`ProviderSubmissionAdapter` 才调用 `call_provider_once()`；caller一次请求无内部retry，协议`max_retries=2`决定是否产生下一自然ordinal。
3. 每个调用先写intent，返回后写response与terminal，再生成`ExecutionSubmission`交给现有runtime；parser/verifier/checker/canonical/merge均走既有边界。
4. `run_root()` terminal 后先原子保存不可变`protocol.json`；该文件内含重建全部protocol-origin traces所需的普通attempt和领域语义快照，再幂等物化各trace文件。
5. `run_coverage_tail()`只处理`unscheduled_ai_unit_ids - committed_trace_keys`，沿同一provider/parser/checker路径取得trace；最多三个自然attempt，Factorization verifier或Lean checker首次接受即停止。Tail attempt的`canonical_accepted`固定为null/false并带`not_applicable`，不得作为tail success条件。
6. tail完成后写`result.json`。tail不提交到已终止协议，不改变root status、correctness或runtime；下一个root在tail terminal后才开始。

外部调用时点只有步骤2和tail的实际目标。Full硬上限为5,910；Representative为57。恢复命中terminal call或committed trace时不重复调用。

### Experiment 2

1. atomic命令必须显式提供Exp1 source run；`run-all`也把当前Exp1目录作为显式内部参数传入。
2. 每case完整重建Exp1相同split、unit、prompt和依赖，只改变`worker_count∈{1,3,7,10,30,50}`。
3. `FixedTraceSubmissionAdapter`按`case_id × source_repeat_id=0 × planned_ai_unit_id`选择exact ordinal，缺失时只回退同trace最后自然ordinal，并核对Factorization range。
4. `LogicalSourceLatencyScheduler`与现有worker backend实际推进逻辑时间、并发、利用率和early stop；结果经相同`run_root`和projector落盘。

本实验从不构造provider caller；任何transport入口被调用都立即停止condition。provider calls精确为0，且不会隐式启动Experiment 1。

### Experiment 3

1. source lookup、logical scheduler和`run_root`与Experiment 2相同；Factorization/Lean普通语义字段都必须匹配。
2. rate-fault由Slim hook只在ordinal 0实施五种冻结动作；target、扰动和reference严格读取指标权威第4节。
3. replacement/requeue、verification rejection、canonical和root结果由现有engine/plugin产生；Slim只join observations。
4. worker death条件使用现有`ProcessWorkerBackend + WorkerTerminationPolicy`；PID/exit/progress/liveness和recovery事实来自backend/events。
5. 每个论文root和106个辅助reference分别落盘，reference不进入论文root分母；reference frozen planned/upper为468/1,404，并与其他上限一起从逐root inventory复算。

本实验不构造provider caller，无transport fallback，provider calls精确为0。Full current inventory为rate 13,920、death 2,808、planned 16,728、upper 52,992；Representative为42、16、58、190，均由逐root求和校验。

### Experiment 4

1. 在mode展开前按`case_id × repeat_id`生成challenge plan；同一plan传给全部11 modes。
2. injector签名不接收mode、disabled set或policy；只在权威指定边界对已选择fixed answer执行实际动作。
3. `ProtocolMechanismPolicy`在一个root内同时应用FULL、单机制或双机制配置；组合mode不能离线拼接。
4. `run_root`和既有engine/plugin决定verification、requeue、canonical、merge、root checker与最终状态；Slim observation不能从mode名推导。
5. preflight block、plan mismatch和missed opportunity按authority使相关cell为null；协议启动后的自然失败进入固定分母。

本实验不构造provider caller，无transport fallback，provider calls精确为0。Full每mode/repeat 293 units、planned 9,669、upper 15,822；Representative planned 209、upper 342，全部由结构化`disabled_mechanisms`计算，禁止解析`condition_id`子串。

### Experiment 5

1. 每model condition显式解析四个冻结SiliconFlow entry之一，按`ABCD/BDAC/CADB`顺序运行。
2. worker执行unit时才调用与Experiment 1相同的bounded caller和submission adapter；`max_retries=0,replacement_attempts_allowed=false`。
3. 现有Factorization/Lean parser、checker、canonical、merge/root recheck决定结果；configured/requested/resolved model写入普通attempt/root记录。
4. root结果原子落盘，不运行coverage tail。

外部调用只发生在实际调度的ordinal 0 unit；Full上限4,992，Representative上限32。全Full真实provider hard cap为10,902，Representative总cap为89。

## 7. Run目录和数据生命周期

writer/reader分工：

| 数据 | writer | reader | 原子键/完成判定 |
|---|---|---|---|
| inventory | CLI + profiles | runner/reducer | run创建时一次写完；root key集合冻结 |
| system events/artifacts | existing engine/store | projector | 系统自己的task/event/artifact身份 |
| root protocol投影 | runtime + projector | tail/resume/final projector | root key；`run_root`返回后原子文件存在 |
| Exp1 unit trace | runtime/tail + storage | Exp2–4 fixed adapter/reducer诊断 | `case_id × source_repeat_id=0 × planned_ai_unit_id` |
| provider call journal | provider + storage | journal-aware adapter/resume/projector | root key × unit × natural ordinal；terminal文件存在 |
| committed root result | runtime + storage | resume/reducer | `experiment_id × condition_id × case_id × repeat_id` |
| Exp3 reference | runtime + storage | reducer | `case_id × repeat_id`，与论文root分开 |
| metrics | reducer | 人/论文后处理 | table/slice/metric ID |

最小 journal 只有三类：root的不可变protocol投影与最终结果、Exp1 trace、provider call intent/terminal。它们记录“本地工作是否已经持久化”，不记录协议task/attempt状态机。

写入顺序：

1. caller接收`ProviderCallContextV1(call_key,root_key,planned_ai_unit_id,attempt_ordinal)`和注入的`RunStore`，provider send前由该store原子写intent；caller不创建第二个store；
2. 收到或捕获terminal后先由同一store写response（如有），再写terminal；
3. `run_root`返回后写含完整protocol-origin trace重建素材的`protocol.json`；
4. Exp1先由protocol投影幂等物化全部protocol-origin traces，再补真正unscheduled的tail traces，最后写`result.json`；其他实验直接写`result.json`；
5. 每个文件使用同目录temporary file、UTF-8 flush/close后replace；冲突主键不覆盖。

Resume只扫描：

- committed `result.json`：跳过整个root；
- Exp1 `protocol.json`但final result缺失：不重跑`run_root`；先从protocol投影幂等重建缺失的protocol-origin traces，再只对真正unscheduled且缺key的unit运行tail，最后提交result；
- committed trace key：不重复取得回答；
- terminal call：复用结果，不再次发请求；
- intent存在、terminal缺失：先检查当前进程；response存在时从已存response完成terminal；确认无活跃owner且无response时写`unknown_transport_outcome`，同一ordinal不重调。协议若仍允许replacement，只能由engine请求下一自然ordinal；
- 已开始但没有protocol投影的死亡root：不调用第二次`run_root`，写固定身份的`infrastructure_invalid`结果并继续未开始root。若要重做该样本，使用新run ID。

因此正常执行的每个论文root精确调用一次`run_root`，崩溃恢复也不会把同一root悄悄变成第二次系统运行。跳过文件只是本地调度决定，不是Slim宣布协议终态。

## 8. 依赖图与六个纵向实施阶段

```mermaid
flowchart LR
    T1["Task 1 现有系统本体纵向闭环"] --> T2["Task 2 profile/schema/普通输出"]
    T2 --> T3["Task 3 Exp1/5回答路径"]
    T3 --> T4["Task 4 Exp2-4系统场景"]
    T4 --> T5["Task 5 统一reducer"]
    T5 --> T6["Task 6 CLI/resume/readiness"]
```

逻辑上，golden fixture准备、文档核对和只读公共接口审计可与当前阶段内部实现并行；实际写入继续遵守 `one focus at a time`，不得同时打开第二条代码写路径。三个审查里程碑固定为：Task 1本体闭环、Task 4全部实验场景闭环、Task 6最终representative readiness。其余Task不要求逐微步骤双review或双commit。

当前实例按`slim_v2_stage_relay_protocol.md` §5.2执行replan-aware启动，并按relay §5.1让六个大型Task分别由六名连续、全新的顶层owner串行完成；不得由一个Stage 3总监督owner连续实现六项。当前relay只读输入的SHA256为`B7F6785957B0FD0EC5D35A4AE18A81715476A4DD6EFEDB1A78B8BD8B41FE0777`。

| 重规划前成果 | 新计划映射 | 启动裁决 |
|---|---|---|
| 旧Task 1 `schema.py/test_schema.py`，implementation `aaabca41`、evidence `4fb647b4` | 新Task 2可复用输入 | 必须先经新Task 1真实纵链校准；不等于新Task 2完成 |
| 旧Task 2 `case_source.py/profiles.py`与测试，implementation `13a25193`、evidence `794362fa` | 新Task 2可复用输入 | 保留提交，不reset/revert/cherry-pick复制 |
| 旧Task 3 dirty `profiles.py/test_profiles.py` | 新Task 2待审查草稿 | 已知focused通过但旧review未闭合，`not approved/not complete` |
| 旧Task 3 untracked `cli.py/test_cli_plan.py` | 新Task 2的plan草稿与新Task 6的CLI起点 | 原样保留，按新纵链取用，不按旧编号判完成 |

fresh Stage 3 Task 1/6 owner启动后先执行`Rebaseline existing implementation against approved six-task blueprint`，逐文件核对上述映射和当前diff；rebaseline只是开工审计，不新增第七个Task，也不能替代新Task 1。任何删除或大改必须由本规格和真实纵链事实证明，不得为了clean工作树删除成果。

### Task 1 现有系统本体纵向闭环

#### 1. 目标结果/可运行切片

用fake answer分别运行一个Factorization root和一个Lean fixed-DAG root，真实经过`run_root → domain bridge → existing worker → engine/ledger/store → verifier/checker → canonical/merge/root recheck`，然后只读投影普通root result。这是后续所有工作第一道Gate。

#### 2. 创建/修改文件

创建`runtime.py`,`projector.py`,`test_system_vertical.py`；test fixture内提供最小fake submission adapter。仅为`SCHEMA_VERSION`需要时最小修改`__init__.py`，不创建生产provider/fixed adapter，不修改shared文件。

#### 3. 导出的类、函数、CLI

`RootAssembly`,`run_root_slice`,`project_root_result`；fake adapter只是测试fixture内部符号，本Task不扩展CLI或package-level re-export。

#### 4. 输入/输出类型

输入：一个catalog case、显式`ProtocolConfig`、测试fixture提供的fake `ExecutionSubmission` adapter、worker backend。输出：公共`ProtocolRunResult`和已有`RootResultV1`的最小普通投影。

#### 5. 上游/下游及精确公共符号

上游使用`ProtocolRunRequest`,`ProtocolRunCoordinator.run_root`,`ProtocolEngine`,`EventLedger`,`ArtifactStore`,`SequentialWorkerBackend`,`ExecutionSubmission`；领域使用`FactorizationRuntimeAdapter/FactorizationExecutionBridge`和`LeanFixedDecompositionPlan/LeanRuntimeAdapter/LeanExecutionBridge`。下游仅为Task 2普通输出。

#### 6. 关键控制流/实现逻辑

每root创建独占store/ledger/engine/plugin/bridge/backend/coordinator；显式构造request；计数器断言`run_root`恰好一次；从result、同一ledger/store和plugin读取领域事实；Factorization检查final product，Lean检查child checker、merge acceptance和root recheck。

#### 7. 状态真值owner与失败作用域变化

所有协议事实仍由engine/ledger，领域事实仍由plugin/checker拥有。Slim projector只读。任一领域不能跑通即阻断Task 2；自然candidate rejection是可投影结果，公共接口或事实缺失才是Gate失败。

#### 8. 风险驱动验证场景与命令

验证两领域纵链、`run_root`一次、真实canonical/merge/root recheck、无shared修改、无provider/Lean二进制。复用已知4-pass审计作为基线，再运行：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_system_vertical.py -q
```

#### 9. 完成标准

两领域均产生可解释普通root结果；Sequential路径无shared接口缺口；里程碑review范围内Critical/Important关闭。当前owner随后严格按relay §5.1创建且只创建fresh Stage 3 Task 2/6 owner，确认其heartbeat active后结束；不得自行继续Task 2。

**Task 1完成证据与里程碑review（2026-08-21）**：

- fail-first为缺少`projector`模块，`1 error in 0.29s`；实现者最终focused验证为`3 passed in 10.19s`。
- spec reviewer独立复跑为`3 passed in 9.85s`，结论`Critical/Important/Minor=0/0/0`；implementation-quality reviewer独立复跑为`3 passed in 8.67s`，结论`Critical/Important/Minor=0/0/0`；shared interface gap=`none`。
- 真实成功Factorization root触发early merge：`planned=3/dispatched=2/completed=2`，`range_2`保持unscheduled；真实成功Lean fixed-DAG root经过child checker、merge与root recheck；真实自然rejection root可通过`run_root_slice`投影；每个root的`run_root`均恰好调用一次。
- provider/network=`0/0`；未运行representative/full、Lean专项suite、LeanAudit、catalog全量、`lake`或`lean`。

#### 10. 明确非目标

不创建生产`execution.py`、provider/fixed adapter、trace、resume、scenario、reducer或CLI；不证明Thread；不复制engine/ledger/storage/worker/verifier/checker/merge。

### Task 2 冻结profile、最小schema与普通输出

#### 1. 目标结果/可运行切片

保留已完成的schema原始字段合同、case IDs、profile、canonical condition IDs和plan派生成果；以Task 1真实纵链校准字段，只留下原子root/trace、completed-key扫描和最小provider terminal journal，能够把Task 1两root写入最终run布局并resume跳过。

#### 2. 创建/修改文件

修改`schema.py`,`case_source.py`,`profiles.py`,`cli.py`及已有四个测试文件；创建`storage.py`，按风险需要把输出合同并入`test_schema.py`或`test_profiles.py`，不为storage helper另设测试数量目标。

#### 3. 导出的类、函数、CLI

保留`build_profile/build_inventory/build_plan`；新增`RunStore`,`ResumeView`,`scan_resume`,`select_trace_attempt`；保留只读`plan`命令。

#### 4. 输入/输出类型

输入：`representative|full`、catalog、run ID、root/trace/call对象。输出：冻结inventory/plan、原子JSON/JSONL、committed key集合。

#### 5. 上游/下游及精确公共符号

上游为Task 1 `RootResultV1`和当前catalog；不新增shared依赖。下游为Task 3 caller/trace和Task 5 reducer。

#### 6. 关键控制流/实现逻辑

对现有schema做纵链事实校准：保留153个metric IDs的可计算性，并按指标权威第8节逐路径校准最小原始字段合同；删除不能映射到authority、Task 1公共事实或最小journal的generic framework/第二状态机字段。condition ID只作稳定身份，行为读取结构化字段。写inventory后逐root原子commit；resume扫描文件存在性，不执行repair/compaction。

#### 7. 状态真值owner与失败作用域变化

`RunStore`只拥有文件是否committed；协议状态仍由engine/ledger。重复相同key+相同内容可跳过，冲突内容停止run；schema/catalog全局错误停止run；单root自然失败仍写结果。

#### 8. 风险驱动验证场景与命令

覆盖UTF-8/catalog、schema跨模块合同、canonical condition ID维度、逐root派生常量、原子冲突、completed-key扫描和unknown terminal事实。当前profile/case/CLI plan基线为`16 passed in 1.05s`；校准后运行：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_schema.py tests/experiments/slim_v2/test_case_source.py tests/experiments/slim_v2/test_profiles.py tests/experiments/slim_v2/test_cli_plan.py -q
```

#### 9. 完成标准

153个metric IDs集合不变，指标权威第8节原始字段路径合同逐项闭合；Full/Representative全部冻结数值与逐root公式一致；陈旧Exp3/4 inventory常量不再出现在active正文；Task 1结果能写、读、skip。当前owner随后严格按relay §5.1创建且只创建fresh Stage 3 Task 3/6 owner并完成heartbeat交棒，不得自行继续Task 3。

**Task 2完成证据与独立review（2026-08-21）**：

- 继承的`schema.py/case_source.py/profiles.py/cli.py`成果已按Task 1真实`RootResultV1`纵链校准；新增`storage.py`后，`build_inventory → RootInventoryV1 → inventory/*.jsonl → typed read → Task 1 projector`闭合。Factorization planned IDs使用经catalog count校验的`range_N`，Lean使用`dependency_order`；四个冻结inventory逐文件原子写，重复同规范内容为`skipped`，冲突立即停止且不覆盖已committed文件。
- 首轮storage/inventory fail-first分别为`3 failed, 6 deselected in 0.29s`与`1 failed in 0.32s`；typed inventory纵链补强RED为`1 failed in 0.27s`。质量review发现的3个Important与3个Minor补强RED为`4 failed, 2 passed in 0.63s`，随后全部关闭。
- `SlimRunConfigV1`已收窄为设计规格7.2的12个普通字段，`ordinary_parallel_backend_kind="thread"`、`reducer_workers=1`和16 MiB response上限固定，不含日志轮转framework；`select_trace_attempt`只接收并验证typed连续自然ordinal的`UnitTraceV1`。resume只扫精确committed文件名，不repair、compaction或重建协议状态。
- 冻结profile复核：Full roots/executions=`7,554/7,660`；Exp3 rate/death/planned/upper=`13,920/2,808/16,728/52,992`，references=`468/1,404`；Exp4 planned/upper=`9,669/15,822`且每mode/repeat=`293`；Exp5顺序=`ABCD/BDAC/CADB`。Representative roots/executions/cap=`72/74/89`、Exp3=`42/16/58/190`、Exp4=`209/342`。Exp4 195个challenge的配额=`49/49/48/49`，Factorization composite/prime与Lean delay target均按authority冻结。
- spec reviewer最终独立复跑为`28 passed in 1.17s`，并重算Full/Representative inventory、153 metric records与168 authority leaves，结论`Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`；implementation-quality reviewer独立复跑为`28 passed in 1.18s`，结论`APPROVED`且`Critical/Important/Minor=0/0/0`。
- owner最终fresh focused验证为`28 passed in 1.39s`，`git diff --check`通过；provider/network=`0/0`，shared interface gap=`none`。未运行representative/full、Lean专项suite、LeanAudit、catalog全量、`lake`或`lean`。

#### 10. 明确非目标

不实现通用状态机、checkpoint generation authority、日志框架、固定句柄魔数、通用资源治理、provider或场景。

### Task 3 Experiment 1/5 回答路径

#### 1. 目标结果/可运行切片

用fake transport让Exp1和Exp5通过同一生产caller/ExecutionSubmission路径跑完整root；Exp1在protocol terminal后补coverage tail并产生可供Exp2–4读取的唯一trace；Exp5覆盖四endpoint且不运行tail。

#### 2. 创建/修改文件

创建`provider.py`,`execution.py`；修改`runtime.py`,`storage.py`,`projector.py`；创建`test_answer_paths.py`和小型provider fixtures。

#### 3. 导出的类、函数、CLI

`ProviderEntryViewV1`,`ProviderCallContextV1`,`call_provider_once`,`project_cost`,`ProviderSubmissionAdapter`,`FixedTraceSubmissionAdapter`,`select_trace_attempt`,`run_coverage_tail`。这些是module-qualified调用点/合同，CLI只在内部接线，本Task不增加package-level re-export或宣称完整命令。

#### 4. 输入/输出类型

输入：单entry、prompt/request controls、`ProviderCallContextV1`、注入的`RunStore`、`ExecutionRequest`、三元trace key。输出：`ProviderCallResultV1`,`ExecutionSubmission`,`UnitTraceV1`,`TailSummaryV1`。

#### 5. 上游/下游及精确公共符号

精确复用Chapter 5列出的四个body builder/envelope parser、两领域parser/checker和`ExecutionSubmission`；连接Task 1两领域bridge、Task 2 `RunStore`；下游为Task 4 fixed source。

#### 6. 关键控制流/实现逻辑

caller签名显式接收call context与`RunStore`，在send前经该store写intent，最多读取16MiB+1并在所有路径关闭response，写response/terminal且无内部retry；它不创建或拥有第二个store。价格按authority 1.4纯投影。fixed adapter只有trace选择，不包含transport。Exp1先原子commit含完整protocol-origin trace素材的`protocol.json`，再幂等物化protocol traces，tail只补真正未调度且缺key的unit；Exp5四entry走同caller、零replacement。

#### 7. 状态真值owner与失败作用域变化

provider网络终态仅由minimal journal记录；协议attempt/retry仍由engine。tail是唯一terminal后Slim-local工作，不改正文事实。transport/envelope/parse/checker失败作为真实attempt结果；model mismatch或journal冲突停止condition。

#### 8. 风险驱动验证场景与命令

覆盖single entry、16MiB+1、response close、terminal journal/unknown、无内部retry、secret不落盘、fixed trace零调用、tail不污染正文和四endpoint模型身份：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_answer_paths.py tests/experiments/slim_v2/test_system_vertical.py -q
```

#### 9. 完成标准

fake transport下Exp1/5两领域均完成生产路径；每个Exp1 planned unit至多一个trace key；tail前后root status/runtime相同；fixed selection没有可达transport fallback。当前owner随后严格按relay §5.1创建且只创建fresh Stage 3 Task 4/6 owner并完成heartbeat交棒，不得自行继续Task 4。

#### 10. 明确非目标

不调用真实provider，不实现selector、预算、response bank、hard deadline child或独立answer service。

**Task 3完成证据与独立review（2026-08-21）**：

- 新增`provider.py/execution.py`并校准`runtime.py/storage.py/projector.py`：DeepSeek与SiliconFlow共用显式`ProviderCallContextV1 + RunStore`的single-attempt caller，send前写intent，response最多读取16 MiB+1且全路径关闭；response/terminal、model/usage与冻结价格均为普通事实，没有内部retry、第二store、selector或transport fallback。Exp1控制固定为`600s/300000`，Exp5按用户后续覆盖固定为`600s/100000`；三个thinking entry的`thinking_budget=32768`保持不变。
- `ProviderSubmissionAdapter`经公共`ExecutionSubmission`、Factorization/Lean parser与真实bridge/checker跑完整root；`FixedTraceSubmissionAdapter`只消费typed Exp1 trace并实现exact/last-natural-attempt fallback，transport spy保持0。配置、模型身份或journal条件错误sticky后不重复caller/provider/journal；若root已经启动，现有coordinator仍返回terminal failed result，projector必须落`protocol_started=true, final_result_present=false, verified_correct=false`的失败行，保证任何已启动但无结果的题仍进入固定正确率分母。停止后续condition roots留给Task 6编排。
- Exp1先原子写包含完整protocol-origin trace重建素材的`protocol.json`，再幂等物化protocol traces；coverage tail强制读取terminal `ProtocolRunResult`的typed `unscheduled_ai_unit_ids`，只调用缺trace key的unit。tail attempt时间写入既有typed trace，resume从全部已落coverage-tail traces重建完整target、recorded、success/failure、attempt/token/cost与原始时间边界，不创建checkpoint或第二状态。正文root status、正确性、runtime与protocol bytes不受tail影响。Exp5四个冻结SiliconFlow entry走同一caller，`max_retries=0`、零replacement且不运行tail。
- 风险驱动fail-first依次捕获缺provider/execution模块、配置错误越过condition边界、tail语义/恢复target不一致、冻结controls缺失、tail timing未持久化及已启动失败root不可投影；所有范围内Critical/Important均由原实现者修复。`test_system_vertical.py`仅把Task 1 Factorization fixture改为第三range终止，使无tail纵链诚实保持`unscheduled=[]`；Task 3的91/8..9 tail fixture独立拥有自身语义。
- 用户覆盖Exp5 response上限前，spec reviewer独立复跑为`22 passed in 26.61s`、`SPEC_COMPLIANT`且`Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`；implementation-quality reviewer为`22 passed in 28.88s`、`APPROVED`且`Critical/Important/Minor=0/0/0`；owner fresh为`22 passed in 26.63s`。这些结果只作为Task 3主体纵链基线，不替代下述`max_tokens=100000`覆盖后的fresh证据。
- 用户随后明确把Exp5 response `max_tokens`由`32768`提高到`100000`，并要求代码与文档同步后才能交棒；A/B/C的`thinking_budget=32768`不变。四个Exp5参数用例先取得预期RED，随后targeted=`4 passed in 8.60s`、实现者双文件fresh=`22 passed in 26.82s`。参数覆盖首轮re-audit中，spec reviewer=`22 passed in 28.44s`、quality reviewer=`22 passed in 27.92s`，两者均确认代码、四endpoint与design/metrics authority参数一致且provider/network=`0/0`；各自唯一`Important=1`都是本段fresh证据当时尚未写入计划/progress。补录后原spec reviewer短复核=`SPEC_COMPLIANT`、原quality reviewer短复核=`APPROVED`，最终`Critical/Important/Minor/out_of_scope_by_user=0/0/0/0`；owner post-override fresh=`22 passed in 26.79s`。
- 当前conda环境未editable-install本仓库，所有focused命令仅为当前进程设置`PYTHONPATH=src`；裸命令会在collection前报`ModuleNotFoundError: tokenshare`，未为此修改共享环境或conftest。scoped compile、禁止依赖扫描与`git diff --check`均通过；shared interface gap=`none`。未运行真实provider、representative/full、Lean专项suite、LeanAudit、catalog全量、`lake`或`lean`。

### Task 4 Experiment 2–4 系统场景路径

#### 1. 目标结果/可运行切片

用Exp1 fake traces完整运行Exp2六worker、Exp3五fault/worker death/reference和Exp4 11-mode challenge，全部真实经过现有system/plugin/checker，provider calls为0。

#### 2. 创建/修改文件

创建`scenarios.py`,`test_scenarios.py`；按需修改`runtime.py`,`execution.py`,`projector.py`，不创建额外scenario framework。

#### 3. 导出的类、函数、CLI

`build_scenario`,`build_exp3_reference`,`build_challenge_plan`及最小hook/collector类；没有新的公开CLI。

#### 4. 输入/输出类型

输入：结构化`RootInventoryV1`、fixed trace source、planned units。输出：现有scheduler/policy/hooks/backend参数和真实observations，最终仍是`RootResultV1`。

#### 5. 上游/下游及精确公共符号

使用`LogicalSourceLatencyScheduler`,`RuntimeHooks`,`ProtocolMechanismPolicy`,`ProcessWorkerBackend`,`WorkerTerminationPolicy`以及engine recovery/requeue；普通并发先使用`ThreadWorkerBackend`做证据测试。

#### 6. 关键控制流/实现逻辑

先实测Thread在Factorization k>1和Lean fake fixed-DAG下的unit/attempt/canonical/checker事实完整性；通过即使用Thread。若Thread出现明确失败证据，先验证现有`ProcessWorkerBackend`能否直接闭合普通场景；只有Thread与现有Process普通场景都不能闭合时，才提出Slim-local bounded-process facade作为条件性补救并按接力规则审议。worker death直接使用Process。Exp3/4判断全部读取结构化字段，禁止解析condition ID。

#### 7. 状态真值owner与失败作用域变化

Slim只配置fault/challenge/mode并记录实际hook动作；recovery、requeue、death、canonical、root status由existing engine/backend/plugin拥有。source错配或transport attempt停止condition；实验性失败保留root行；Thread验收失败只触发backend选择证据，不表示shared系统失败。

#### 8. 风险驱动验证场景与命令

覆盖Thread事实完整性、Process真实death、Exp2 logical capacity/early stop、Exp3 ordinal0与replacement、Exp4真实组合消融/mode-blind、全部零transport：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios.py tests/experiments/slim_v2/test_answer_paths.py -q
```

#### 9. 完成标准

Exp2–4代表性root均一次`run_root`且0 provider calls；Thread选择有证据；death使用真实Process facts；Exp4六个双机制mode在单次root中真实同时生效；第二个里程碑review关闭范围内Critical/Important。当前owner随后严格按relay §5.1创建且只创建fresh Stage 3 Task 5/6 owner并完成heartbeat交棒，不得自行继续Task 5。

#### 10. 明确非目标

不预建bounded-process facade，不生成Slim canonical/recovery/worker/root状态，不增加fault/challenge，不运行真实Lean二进制或provider。

### Task 5 统一 reducer

#### 1. 目标结果/可运行切片

一个`reduce_run()`从golden run目录生成Experiment 1–5全部表和153个metric IDs，固定分母、配对、四端、bootstrap、null传播与tail隔离全部闭合。

#### 2. 创建/修改文件

创建`reducer.py`,`test_reducer_golden.py`及少量golden run fixtures；不为每个实验建立独立reducer模块。

#### 3. 导出的类、函数、CLI

`reduce_run`,`metric_ids`和少量纯统计函数；`reduce` CLI在Task 6接通。

#### 4. 输入/输出类型

输入：一个run目录的inventory、committed root results、Exp3 references。输出：`metrics/tables/*.jsonl`,`*.csv`,`summary.json`。

#### 5. 上游/下游及精确公共符号

上游只读Task 2 `RunStore.iter_*`；公式只以metrics authority为准。下游为Task 6 CLI与论文分析，不依赖runtime/plugin/provider。

#### 6. 关键控制流/实现逻辑

流式join inventory和results，按table/slice处理；rate重算固定分母；pair/quadruple保持case/repeat/arms不可拆；bootstrap固定10,000和seed 20260820；raw/system目录不可读；成功后原子写最终表。

#### 7. 状态真值owner与失败作用域变化

reducer只计算，不更改任何root/attempt。缺result仍留固定分母并输出null/reason；不适用为null而非0；单表公式错误阻断reduce，不回写run。

#### 8. 风险驱动验证场景与命令

用表驱动golden fixture统一覆盖153 IDs、固定分母、tail隔离、独立pair eligibility、六组quadruple、type-7 quantile、sample variance、bootstrap阈值和null传播：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q
```

#### 9. 完成标准

metric ID集合精确153/153；所有公式方向与authority一致；Exp2–4无actual provider consumption；reducer不导入或调用runtime/checker/provider且不加载raw/system全目录。当前owner随后严格按relay §5.1创建且只创建fresh Stage 3 Task 6/6 owner并完成heartbeat交棒，不得自行继续Task 6。

#### 10. 明确非目标

不建立formula graph、metric registry、publication eligibility、在线报告service或每实验独立框架。

### Task 6 CLI、resume与representative readiness

#### 1. 目标结果/可运行切片

接通全部原子CLI、run-all、resume、preflight和全离线E2E；通过后立即进入真实representative下一阶段，不继续扩建设施。

#### 2. 创建/修改文件

修改`cli.py`,`runtime.py`,`storage.py`,`projector.py`；创建`test_cli_e2e.py`，必要时复用全部前序fixtures。

#### 3. 导出的类、函数、CLI

`run_experiment`,`main`以及第2章冻结的`plan/run/run-all/reduce/representative`命令。

#### 4. 输入/输出类型

输入：profile、experiment、run ID、显式source目录、resume标志。输出：完整run目录、exit code、metrics文件和清晰failure scope。

#### 5. 上游/下游及精确公共符号

组合Tasks 1–5公开符号；最终系统调用仍只有`ProtocolRunCoordinator.run_root`，Exp2–4 source只能显式传入。

#### 6. 关键控制流/实现逻辑

preflight在secret/runtime前校验inventory、依赖、source closure、entry/model、磁盘和调用上限；roots串行；每root前检查下一root所需空间；resume扫描committed root/trace/terminal call；run-all按`exp1→exp2→exp3→exp4→exp5→reduce`并显式传source；representative调用相同内部路径。

这里的“磁盘和调用上限”只有运行安全含义：磁盘检查只防止下一次原子写耗尽空间；调用上限只核对逐root inventory派生的自然attempt hard cap、发现重复dispatch或计划越界，避免程序失控与意外重复付费。preflight不得读取或判断价格、余额或可支付性，不得创建`budget authority`，不得等待人工授权，不得检查`publication readiness`或`evidence completeness`，也不得把价格表缺失、变化或成本投影结果作为阻止实验的条件。价格问题只能使成本字段为`null + reason`或留下普通诊断，不能改变root/condition/run是否允许启动。

#### 7. 状态真值owner与失败作用域变化

CLI只拥有调度与退出码。单root自然失败继续；condition identity/source/model接线错停止condition并为剩余预注册roots写infra-invalid；catalog/run store/disk等全局错误安全停止run；resume跳过不是协议终态声明。

#### 8. 风险驱动验证场景与命令

覆盖原子命令、source显式/零fallback、roots串行、resume/unknown、防重复fake调用、89-call representative preflight、secret/磁盘preflight、全离线两领域E2E；另以合同/absence断言证明preflight不接受价格、余额、budget、人工批准、publication或evidence gate输入，成本投影缺失/变化不改变同一运行计划的启动结论：

```powershell
conda run -n tokenshare python -m pytest tests/experiments/slim_v2 -q
```

#### 9. 完成标准

Representative inventory为Exp1 4、Exp2 12、Exp3 8+2 references、Exp4 44、Exp5 4；论文roots 72、executions 74；provider cap 89；Exp2–4 0 calls；所有root一次run_root或按第7章诚实记录中断；preflight只实施运行安全检查且不存在价格/余额/budget/人工批准/publication/evidence gate；第三里程碑review关闭范围内Critical/Important。当前owner满足relay的Stage 3总完成标准后，按relay §5.1创建Stage 4 owner并完成heartbeat交棒；不得自行启动真实representative。

#### 10. 明确非目标

不在本Task调用真实provider、运行真实representative/full、增加UI/service、补充低风险覆盖或继续建设通用基础设施；不实现价格/余额审批、budget authority、人工授权门禁、publication readiness、evidence completeness，且不因价格表变化阻止实验。

## 9. 状态真值、失败作用域、恢复与资源边界

状态真值集中遵守第5章矩阵，每个Task的状态条款只是应用，不另建owner。失败作用域：

| 失败 | 捕获者 | 作用域 | 落盘/后续 |
|---|---|---|---|
| provider timeout/HTTP/envelope、parse/checker rejection、retry用尽 | execution/runtime | 当前root的实验结果 | 写attempt/root，继续下一root |
| Exp3 fault/death未恢复、Exp4 protocol-start后stuck/incorrect/root checker rejection | existing engine/plugin + projector | 当前root实验结果 | 固定分母保留，继续 |
| source缺键/重复/语义错、Exp2–4 transport attempt | fixed adapter/CLI | 当前condition | 当前及剩余root写infra-invalid后停止condition |
| provider resolved model错、secret/entry配置错 | preflight/provider | 当前model condition；共享配置错可升级run | 不换model，不隐式fallback |
| schema/catalog/inventory不闭合、run store不可写 | CLI/storage | 整次run | 安全停止，保留已committed文件 |
| 磁盘不足 | CLI preflight | 整次run | 当前root未开始则停止；释放空间后resume |
| intent无terminal且进程已死 | resume/provider journal | 当前ordinal未知 | 写unknown，不重调同ordinal |
| Thread事实不完整 | Task 4验收 | 当前backend选择 | 记录证据并先测现有Process普通场景；两者都不能闭合才审议条件性facade |

资源边界：

- roots并发恒为1；root内逻辑worker最大50，实际执行由现有backend capacity控制；
- Experiment 1有效provider in-flight不超过10，Experiment 5不超过3；provider caller没有额外线程池；
- worker death使用Process且每root结束后由backend生命周期关闭；普通路径优先Thread；
- 单HTTP response最大16MiB，读取`limit+1`即关闭；raw写独立文件，不复制进root/log；
- projector只保留当前root的events和必要join；root commit后释放plugin/store/ledger/backend引用；
- reducer只载入当前table/slice的root级观察和bootstrap标量，不读raw/system目录；
- 文件按次打开关闭，不设置与冻结实验无关的通用句柄治理框架；
- `plan`按当前inventory逐root计算provider/protocol/simulated attempt上限，并用Representative raw-response p95与16MiB response hard limit分别给出estimate/hard scenario；磁盘preflight依据下一root所需原子写空间计算，不复制陈旧总量；这些检查只限制技术资源和重复调用，不读取价格/余额、不产生budget或审批权威；价格表变化只影响普通成本投影，不能阻止运行；
- secret只存在于当前进程env和HTTP调用栈，不进入run目录、event/artifact metadata、error或命令输出；
- Representative与Full只替换profile数据，所有资源控制、runner、resume、provider和reducer路径完全相同。

## 10. 风险驱动验证矩阵

全局验证规则：核心业务行为、复杂边界和已知缺陷在有意义时先取得可解释的失败证据；机械字段、配置、literal inventory、原型和生成数据不强制TDD。不得为内部实现、覆盖率或预设测试数量拆测试；优先用参数化与golden fixture合并重复场景。

| 高风险 | 最小证据 | 所属Task | 不做什么 |
|---|---|---|---|
| 绕过系统本体 | Factorization + Lean fake各一条真实run_root纵链 | 1 | 不mock coordinator/engine后宣称闭环 |
| 文件格式/跨模块合同 | 153 metric IDs可计算性、authority §8原始字段路径合同、root/trace/call键、atomic conflict | 2 | 不逐DTO/helper制造RED |
| provider付费边界 | single entry、一次call、16MiB+1 close、terminal/unknown | 3 | 不调用真实provider，不做内部retry |
| fixed trace零调用 | exact/fallback/语义核对/transport spy=0 | 3–4 | 不提供fallback transport |
| tail污染正文 | protocol/tail timing、status、token/cost隔离 | 3 | 不创建tail协议attempt |
| Thread/Process | Thread两领域事实完整；death真实Process | 4 | 不预建process facade |
| Exp3恢复语义 | 五fault、ordinal0、replacement、death、reference | 4 | 不由Slim生成recovery/canonical |
| Exp4真实消融 | mode-blind plan、11 modes、六四端组合 | 4 | 不离线拼接双机制结果 |
| reducer统计 | 153 IDs、fixed denominator、pairs/quadruples/bootstrap/null | 5 | 不拆五套统计框架 |
| CLI与恢复 | 显式source、roots串行、unknown、防重复、89 cap、离线E2E | 6 | 不隐式运行Exp1或更换source |
| preflight膨胀 | preflight输入/输出absence合同；成本投影缺失或变化不改变启动结论 | 6 | 不增加价格/余额/budget/人工批准/publication/evidence gate |

review只在三个里程碑触发：

1. Task 1：本体闭环review，确认没有伪系统；
2. Task 4：全部实验场景闭环review，确认0-call、状态owner和Thread/Process；
3. Task 6：representative readiness review，确认数据、reducer、resume和资源边界。

每次里程碑至少一名spec reviewer和一名implementation-quality reviewer；只修复范围内Critical/Important。设计宪章排除的威胁标记`out_of_scope_by_user`并拒绝实施。无需每个内部步骤双review、双commit或证据commit；是否提交由当前用户授权和工作流决定，验证证据写入本计划与`progress.md`顶部即可。

## 11. 最终验收标准

实施完成必须同时满足：

- 六个Task按顺序完成，三个里程碑review的范围内Critical/Important为0；
- Task 1两领域真实系统纵链通过，shared interface gap保持`none`或按接力协议另行获批；
- 最终源码只包含第2章文件树及确有证据必要的条件性补救，不存在第二个runner、service或状态机；
- 所有论文root正常路径各一次`run_root`，Factorization/Lean真值来自现有plugin/checker；
- Experiment 2–4 provider calls精确为0，source显式、无transport fallback；
- Experiment 1 tail不修改正文runtime/status/attempt，Exp5不运行tail；
- schema与metrics authority保持153/153，reducer固定分母、pair/quadruple/bootstrap/null传播正确；
- Representative冻结为72论文roots、74 executions、provider hard cap 89；Full为7,554论文roots、7,660 executions、provider hard cap 10,902；Exp3/4 corrected upper均由逐root inventory公式得出；
- resume跳过committed root/trace/terminal call，不重复同ordinal付费；unknown transport诚实记录；
- `plan`、source/secret/model/disk preflight和全离线E2E通过；Representative与Full共享同一路径；preflight仅防技术失控、磁盘耗尽和重复调用，不包含价格/余额审批、budget authority、人工授权、publication readiness或evidence completeness，价格表变化不得阻止实验；
- 未运行真实provider、representative/full、Lean专项suite、LeanAudit、全量catalog、`lake`或`lean`；
- UTF-8、Markdown结构、旧常量、禁止依赖、公开接口符号和`git diff --check` focused verification通过；
- `progress.md`顶部记录旧横向计划暂停、Task 3参数勘误、Task 1公共接口实证、六Task当前状态和下一步。

### 本轮蓝图审查闭合

| 只读reviewer | 最终结论 | 复核重点 |
|---|---|---|
| spec coverage | `PASS; Critical=0, Important=0, Minor=0` | 11章/6Task/60模板项、153 metric IDs、冻结数值、状态真值、§5.1/§5.2接力 |
| blueprint usability | `PASS; Critical=0, Important=0` | 纵链可装配性、tail/caller/resume/backend/API owner、无第二状态机/runner/service |

两路review都把relay SHA256 `B7F6785957B0FD0EC5D35A4AE18A81715476A4DD6EFEDB1A78B8BD8B41FE0777`作为输入，并确认六个Task必须由六名连续fresh顶层owner逐棒完成，而不是交给同一个Stage 3总owner。本蓝图状态恢复为`approved_under_user_delegation`；这只表示实施蓝图可执行，不是runtime、representative或论文结果批准。
