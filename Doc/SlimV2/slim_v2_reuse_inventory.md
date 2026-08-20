# Slim V2 现有代码复用清单

状态：支持性审计清单（不是 Slim V2 设计规格）

审计日期：2026-08-20

固定参考 commit：`3489533e79cde05d6ae2a0c9f139785249f60f09`

固定参考 tag：`slim-v2-reference-20260820`

审计对象：上述 40 位 commit 中的源码；本文所有旧源码行号和符号位置均以该 commit 为准。tag 只是便于人类识别的本地别名；若 tag 与完整 SHA 出现任何不一致，立即停止并以完整 SHA 冲突处理，不得移动或覆盖 tag。

固定引用验证命令：

```powershell
git rev-parse "slim-v2-reference-20260820^{}"
# 必须精确输出 3489533e79cde05d6ae2a0c9f139785249f60f09
```

## 1. 这份清单解决什么问题

本文只回答四个问题：现有代码中什么能直接复用、什么只能做局部参考、什么应当重新写一个很小的版本、什么不得进入 Slim V2 依赖图。它供后续搭建 Agent 快速定位代码，不代替以下三个前置权威文档：论文实验与指标清单、系统对外接线契约、Slim V2 Agent 导引。

后续 Agent 不应先通读旧实验文档或 `paper_*` 设施。已进入获批设计/实现阶段时，应先读上述三个权威文档，再完整阅读本文，然后才打开源码。当前 baseline 中的“直接复用”公共文件可按当前工作树定点读取并用 focused tests 复核；固定 archive 中的 legacy 条目只能使用 `git show 3489533e79cde05d6ae2a0c9f139785249f60f09:<path>` 读取本文点名的路径和符号附近最小上下文。

不得 checkout 固定 archive、为其创建工作树、递归列出旧目录或 import 其中的模块。Slim V2 runtime、测试和 CLI 都不得依赖 archive commit/tag；archive 不是 Python import path，也不是运行时数据源。对“禁止复用”条目，即使路径存在也不得为了设计继续展开实现。

## 2. 已确定的减法边界

Slim V2 实验室默认不包含：

- 预算预测、预算授权、成本门禁或 spend reconciliation；
- receipt、hash、digest、lineage、evidence chain；
- completeness authority、publication eligibility、发布审批；
- response bank manifest、不可变 checkpoint 父链、canonical evidence repair；
- representative/full 的多级资格检查；
- 为防本地研究者主动伪造数据而设置的安全工程。

实验室只保留运行不可缺少的普通错误处理：API 超时、HTTP/网络错误、返回体解析错误、文件写入错误、单条任务失败记录。这些是错误语义，不是实验门禁。

系统本体内部现有的 event/artifact/SQLite 机制可以继续作为系统自己的实现细节。Slim V2 不复制、不检查、不聚合这些证据，也不把 ledger digest 或 artifact hash 写入实验结果行。实验室只使用系统公开运行结果中计算论文指标所需的字段。

## 3. 复用等级

| 等级 | 含义 | 后续 Agent 的动作 |
|---|---|---|
| 直接复用 | 边界较小，行为与 Slim V2 目标一致 | 直接 import，并只写薄适配层 |
| 轻量适配 | 核心行为可用，但配置或返回对象夹带旧设施概念 | 包一层小接口；不扩展原模块 |
| 只借局部逻辑 | 所在文件过大或依赖了门禁/证据链 | 仅用固定 SHA 的 `git show` 阅读指定函数和最小相邻定义，复制并化简必要算法；禁止 import 整模块 |
| 禁止复用 | 会把旧 formal/paper 设施重新带回来 | 不读取其实现来驱动设计，不建立依赖 |

“直接复用”优先表示复用当前 baseline 中通过审计的公开符号，不表示从 archive 复制整个文件到 Slim V2。对于系统本体的大模块，Slim V2 只从公开边界调用，把它视为外部黑盒。若 baseline 已包含点名公共文件/符号且 focused tests 通过，就使用 baseline 版本；只有明确缺失且获得批准时，才从固定 commit 选择性移植最小公共文件或提交。不得以此为理由移植 `paper_*` runner、gate、evidence、budget 或 response-bank 链。

## 4. 候选最小数据流

这只是帮助理解复用位置的候选拼接图，不是最终设计规格：

```text
权威实验条件/题目
        |
        v
Slim 调度循环 ---- online 条件 ----> Slim AI 调用封装 ----> 真实 provider
        |                                      |
        |                                      v
        |                                普通 response 文件
        |
        +---- trace 条件 -------> FolderResponseSource
        |
        v
系统公开运行入口（系统本体视为黑盒）
        |
        +---- Exp1 run_root terminal ----> 当前 root 的 coverage tail
        |                                  只补 unscheduled planned units
        |                                  写 trace_origin=coverage_tail
        v
每个 root 协议结果 + tail summary + 唯一 per-unit traces
        |
        v
按权威公式枚举并生成 metrics.json / metrics.csv
```

建议结果主键只用普通文本元组 `(experiment_id, condition_id, case_id, repeat_id)`。恢复时读取 `results.jsonl` 中已有终态主键并跳过，不引入 checkpoint generation、digest 或发布状态机。

## 5. 系统本体接线：复用公开入口，不复用 paper adapter

| 等级 | 文件与符号 | 作用 | 输入 | 输出 | Slim V2 用法与注意事项 |
|---|---|---|---|---|---|
| 直接复用 | `src/tokenshare/local_runtime/contracts.py:1672` `ProtocolRunRequest` | 描述一个 root run 的领域无关请求 | `run_id`、`root_input`、plugin runtime、worker backend；可选 mechanism policy、时序与 scheduler | 交给 coordinator 的请求对象 | 由接线权威文档固定字段映射。使用默认 `NoOpRuntimeHooks`，Slim V2 不配置实验门禁链 |
| 直接复用 | `src/tokenshare/local_runtime/contracts.py:882` `NoOpRuntimeHooks` | 关闭可选 runtime hook 行为 | 无额外输入 | 所有 hook 都不干预 | Slim V2 默认 hooks；只有权威实验明确需要 fault/ablation 时才换成最小场景 hook |
| 直接复用（黑盒） | `src/tokenshare/local_runtime/coordinator.py:674` `ProtocolRunCoordinator`；`:699` `run_root` | 驱动一次完整 root 生命周期 | `ProtocolRunRequest` | `ProtocolRunResult` | 只调用 `run_root`。不调用 formal runner，不把 coordinator 内部 ledger/artifact 校验复制到实验室 |
| 直接复用 | `src/tokenshare/local_runtime/contracts.py:199` `ProtocolTaskPluginRuntime` | 规定 plan、execution request、verify、merge、readiness 的插件边界 | root/unit/attempt/submission | 系统生命周期动作 | 系统接线文档应以此协议为底层事实，不为 Slim 再造第二套插件接口 |
| 轻量适配 | `src/tokenshare/local_runtime/contracts.py:1807` `ProtocolRunResult` | 暴露 root 状态与派生摘要 | coordinator 运行结果 | `status`、`summary` 等 | 只抽取指标权威文档要求的字段；忽略 `event_refs`、`artifact_refs`、`ledger_binding` |
| 直接复用（系统插件） | `src/tokenshare/plugins/factorization/runtime_adapter.py:102` `FactorizationRuntimeAdapter`；`:1239` `FactorizationExecutionBridge` | 把 factorization 题目接入系统协议生命周期 | factorization case、artifact store、range executor | plugin runtime 与 worker execution bridge | 它属于系统/任务插件层，不属于旧实验设施。通过接线文档给出的构造函数使用，不从 `factorization_paper_adapter.py` 间接调用 |
| 直接复用（系统插件） | `src/tokenshare/plugins/lean_proof/runtime_adapter.py:127` `LeanRuntimeAdapter`；`:1670` `LeanExecutionBridge` | 把 Lean 题目、拆分、检查与合并接入协议生命周期 | Lean case、固定 Lean 环境、proof executor | plugin runtime 与 worker execution bridge | 它属于系统/任务插件层。Lean checker 是实验语义的一部分，不是 paper publication gate |
| 禁止复用 | `src/tokenshare/experiments/factorization_paper_adapter.py`（4200 行） | 同时承担 provider、trace、fault、worker death、evidence、metrics 等多种职责 | 大量 paper/formal 对象 | paper run result 与证据 | 不 import；需要的系统能力直接从 plugin runtime、coordinator 和小型场景组件接线 |
| 禁止复用 | `src/tokenshare/experiments/lean_paper_adapter.py`（5884 行） | 同时承担 Lean runtime、trace、fault、oracle evidence、eligibility 等职责 | 大量 paper/formal 对象 | paper run result 与证据 | 不 import；这是结构性高耦合点之一 |

重要限制：`ProtocolRunResult.summary` 的精确字段仍应由“系统对外接线契约”权威化。本文不能猜测并冻结该契约。

`ProtocolRunCoordinator` 当前构造时仍要求 `ProtocolEngine`、`ArtifactStore`、`EventLedger`（`coordinator.py:677-692`），最终投影也会生成 ledger binding。若用户的“不要 hash/证据链”是指 Slim V2 对外不依赖、不检查、不汇总它们，则用 façade 屏蔽 refs/binding 即可；若目标是系统内部也绝对不生成，则必须另做一次 coordinator 持久化边界抽离。后者会改变系统本体，不应由 Slim 实验室设计擅自决定。

当前两个 plugin runtime 都在模块顶层从 `executors/ai_api.py` 导入 `build_ai_api_executor_descriptor`（factorization `src/tokenshare/plugins/factorization/runtime_adapter.py:23`，Lean `src/tokenshare/plugins/lean_proof/runtime_adapter.py:21`）。这个描述符函数本身很小，但所在文件有 2156 行。若 Slim V2 的目标只是建立一条独立轻量实验入口，可以暂时接受该系统内部依赖；若后续目标升级为物理删除旧代码，应先把 descriptor builder 移到独立小模块，再删除巨型 AI executor，不能直接删文件。

## 6. 真实 AI API 调用

结论：复用 provider-specific 的请求构造、HTTP transport 和返回 envelope 解析；不复用旧 `AIAPIExecutor` 整类。后者把一个 HTTP 调用扩大成 protocol request/submission、provider 选择、prepared identity、hard-deadline child、artifact provenance 与多种门禁，不适合作为 Slim V2 的边界。

| 等级 | 文件与符号 | 作用 | 输入 | 输出 | Slim V2 用法与注意事项 |
|---|---|---|---|---|---|
| 直接复用 | `src/tokenshare/executors/ai_api_transport.py:74` `build_siliconflow_chat_body`；`:141` `build_openai_chat_body` | 按 provider 构造 chat completion JSON body | provider entry、prompt、defaults、max tokens/soft hints、JSON mode | 普通 request dict | 保留当前已验证的 provider 差异；参数值由新的实验权威文档传入 |
| 轻量适配 | `src/tokenshare/executors/ai_api_transport.py:189` `build_deepseek_chat_body` | 构造官方 DeepSeek thinking 请求 | provider entry、prompt、limits、JSON mode | 普通 request dict | 当前硬编码 `thinking=enabled` 和 `reasoning_effort=high`；固定 profile 可直接用，若新权威需要其他模式则只放宽这里 |
| 直接复用 | `src/tokenshare/executors/ai_api_transport.py:390` `UrlLibSiliconFlowTransport`；`:441` `UrlLibOpenAITransport`；`:492` `UrlLibDeepSeekTransport` | 发送真实 bearer-auth HTTP POST | API key、body bytes、endpoint、content type、timeout | 带 `status_code/body/text` 的响应或明确 transport exception | 这是最小真实网络层；API key 只从环境变量读取，不写结果文件 |
| 直接复用 | `src/tokenshare/executors/ai_api_transport.py:234` `parse_siliconflow_response`；`:270` `parse_openai_response`；`:313` `parse_deepseek_response` | 统一解析 provider envelope 和 HTTP 错误 | transport response | `content_text`、可选 reasoning、finish reason、usage、raw JSON、resolved model、provider response id | 保留 raw response；业务 parser 失败不应触发第二次 provider 调用 |
| 直接复用 | `src/tokenshare/executors/ai_api_artifacts.py:30` `classify_response_model`；`:45` `build_raw_model_identity_fields` | 从真实响应读取 model 字段 | raw provider JSON 与 configured/requested model | model identity 普通字段 | 只保存字符串字段，不计算 identity hash，不把 model mismatch 变成 suite 级停止门禁 |
| 轻量适配 | `src/tokenshare/executors/ai_api_config.py:18` `AIAPIProviderEntry` | 保存 endpoint、model、API key env、request overrides | 小型配置 dict | provider entry | dataclass 可直接实例化；不需要 `AIAPIExecutorConfig` 的 selection policy、pricing 或 `config_digest` |
| 只借局部逻辑 | `src/tokenshare/executors/ai_api_local_config.py:26` `load_local_ai_api_config` | 从 gitignored local JSON 注入临时 secret env | local JSON | 旧 executor config | 只借“明文 key 不写回输出”的做法；Slim 配置只需要 provider/base URL/model/API-key env/请求参数 |
| 边界隔离 | `src/tokenshare/executors/contracts.py:131` `ExecutionRequest`；`:189` `ExecutionSubmission` | 系统 worker 的既有执行合约 | protocol request、artifact refs | protocol submission | `slim_ai.py` 不依赖它；只有连接系统 worker 的小 `SlimExecutionBridge` 把 `SlimAIResult` 转成 submission |
| 禁止复用 | `src/tokenshare/executors/ai_api.py:494` `AIAPIExecutor` | 把 provider 调用绑定到协议 submission、artifact provenance、selection 与 hard deadline | `ExecutionRequest`、artifact store、复杂 config | `ExecutionSubmission` 与多类 evidence artifact | 2156 行且职责过多；Slim V2 应新写薄调用函数，再由单独的小 execution bridge 适配系统 worker 接口 |
| 禁止复用 | `src/tokenshare/executors/ai_api_selector.py`、`ai_api_request_identity.py`、`ai_api_hard_deadline.py` | hash 选择、prepared identity、admission/hard-deadline child 流程 | 多种冻结身份与证据 | gate/evidence 对象 | 都不是完成真实 HTTP 调用的必要条件 |

建议新接口只包含：

```text
call_ai(entry, prompt, timeout_seconds, max_tokens, require_json_mode)
  -> SlimAIResult(
       ok, content_text, reasoning_content, raw_response_json,
       prompt_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens,
       completion_tokens, reasoning_tokens, total_tokens,
       provider_request_started_at_utc,
       latency_ms, configured_model, resolved_model,
       provider_response_id, finish_reason, error_kind, error_message)
```

默认每次只做一次 provider attempt；Experiment 1 是否创建 replacement 由协议的 `max_retries=2` 决定，不在 caller 内部暗重试。HTTP 返回已被 provider 接收、返回体不可用或业务 parser 失败时，caller 不自行再调用。caller 不读取 pricing、不计算 cost，也不产生 config/request/identity digest；独立的 Slim pricing projector 使用指标权威第 1.4 节普通静态表计算 cost。

实现时还需修正两个小风险：SiliconFlow/OpenAI parser 目前可能把非字符串 content（包括 `null`）转成字符串，Slim wrapper 应要求 content 为字符串；urllib timeout 是 socket I/O timeout，不是严格的总 wall-clock deadline，结果中应按实际 `perf_counter` 保存 elapsed latency，不再搭 hard-deadline evidence child。

## 7. 场景模拟、worker、故障与消融

| 等级 | 文件与符号 | 作用 | 输入 | 输出 | Slim V2 用法与注意事项 |
|---|---|---|---|---|---|
| 直接复用 | `src/tokenshare/local_runtime/workers.py:101` `SequentialWorkerBackend` | 单 worker 基准 | executor、提交时间函数 | worker submission/facts | 用于基线和非并发条件 |
| 直接复用 | `src/tokenshare/local_runtime/workers.py:209` `ThreadWorkerBackend` | 固定容量的同-root 并发执行 | executor、capacity、提交时间函数 | 保持 request 顺序的 batch outcomes 与 execution facts | 用于普通 worker 数条件；不要在 Slim 外层另套线程池来伪造系统并发 |
| 直接复用 | `src/tokenshare/local_runtime/workers.py:874` `execute_worker_batch` | 统一按 backend capacity 执行 request batch | worker backend、requests | `WorkerBatchOutcome` | coordinator 已调用；Slim 不另写 worker loop |
| 直接复用 | `src/tokenshare/local_runtime/logical_scheduler.py:227` `LogicalSourceLatencyScheduler`；`:415` `logical_source_latency_makespan_ms` | 用响应记录中的 source latency 推进逻辑时钟和估算 makespan | unit identity、latency、worker count | 逻辑完成顺序、`clock_ms`/makespan | 直接用于 trace 条件；不需要调用其 checkpoint/from-checkpoint 方法，也不写 scheduler digest |
| 直接复用 | `src/tokenshare/local_runtime/workers.py:355` `ProcessWorkerBackend` + `src/tokenshare/local_runtime/contracts.py:954` `WorkerTerminationPolicy` | 在独立进程运行 unit，并按 planned unit 与 25/50/75% progress 终止进程 | process-capable executor、capacity、target unit ids、kill point | worker-died outcome、execution facts、后续协议恢复 | 这是 worker-death 场景的真实系统能力；不复用 `paper_workers.py` 的 evidence/plan 封装 |
| 直接复用 | `src/tokenshare/core/recovery.py:118` `evaluate_retry` | 根据 trigger、retry count、max retries 决定 replacement | 普通 retry facts | `RetryDecision` | 使用系统已有恢复语义，不在 ScenarioBuilder 重算 |
| 直接复用 | `src/tokenshare/local_runtime/contracts.py:809-906` directives、`RuntimeHooks`、`NoOpRuntimeHooks` | 提供 raw/parsed/parser/verification/requeue/merge/progress 的稳定接缝 | runtime context | bypass/result-kind/worker directive 或无动作 | FULL 用 NoOp；fault/merge ablation 只实现最小必要 hook |
| 直接复用 | `src/tokenshare/local_runtime/contracts.py:910` `ProtocolMechanismPolicy` | 控制 parser policy、verification、replacement、merge 等协议机制 | 五个普通布尔值 | coordinator 使用的机制配置 | 这是 Experiment 4 的实验自变量，不是“允许实验能否启动”的外部门禁；前三类消融可直接映射，`NO_MERGE_GATE` 仍需一个很小的 `before_merge` hook |
| 只借局部逻辑 | `src/tokenshare/experiments/paper_faults.py:719` `select_fault_targets`；`:735` `select_fault_target_descriptors` | 旧 fault target 选择函数的输入/输出形状 | unit ids、rate、seed | target ids | 不复用旧 digest/随机抽样算法。当前权威固定按 `planned_ai_unit_id` 稳定排序后均匀选取，`target_count=ceil(rate×planned first-attempt units)`，正 rate 至少 1，且只注入 ordinal 0 |
| 只借语义 | `src/tokenshare/experiments/paper_faults.py:776` `inject_post_ai_fault`；`:1205` `_false_positive_payload`；`:1224` `_false_negative_payload` | 五类 rate-fault 的注入点与 payload 变换 | raw/parsed submission、fault type、lease/deadline | mutated/suppressed/late/error result | `inject_post_ai_fault` 绑定 artifact/digest/evidence，禁止 import；只把五类变换写进小型 scenario hook，并输出普通 fault 字段 |
| 禁止整模块复用 | `src/tokenshare/experiments/paper_faults.py`（1442 行） | fault hook、记录、artifact、target hash、applicability 混合 | paper attempt/evidence | 多层 record/ref | 新组件只需要 fault type、是否注入、是否检测、是否替换、相关 attempt id 和 token 数 |
| 禁止整模块复用 | `src/tokenshare/experiments/paper_ablation.py`（907 行） | mode、runtime hook、coverage、artifact/evidence 汇总混合 | paper attempts/refs | eligibility/evidence summary | 模式到 `ProtocolMechanismPolicy` 的布尔映射可抄取；coverage/summary/验证函数全部不进入 Slim |
| 禁止整模块复用 | `src/tokenshare/experiments/paper_workers.py`（960 行）、`paper_exp3_fault_recovery.py`（2984 行） | worker-death plan/evidence 与 Exp3 矩阵/验证/指标混合 | graph、events、manifests | 多层计划与审计记录 | 直接使用 `ProcessWorkerBackend` 和普通 condition 参数；不要复建 manifest/record authority |

场景层仍需新写一个很小的 `ScenarioConfig -> (worker_backend, logical_scheduler, mechanism_policy, fault_hook)` 映射函数。它只翻译权威条件，不判断“这组结果是否可发表”。Experiment 4 当前是 FULL、四个单机制和六个双机制共 11 modes；每个双机制 mode 必须在同一次 root run 中同时关闭对应两项，不能沿用旧四/五模式表。

五类 fault 存在一个必须明确的接缝：raw-output fault 发生在 executor 一侧，false-positive/false-negative 发生在 coordinator `RuntimeHooks` 一侧。Slim 需要一个共享计数状态的轻量 `ScenarioFaultBridge`，一端是 executor raw callback，另一端实现 parsed-candidate hooks；不得复用会生成 artifact/digest observation 的 `PaperFaultRuntimeHooks`。`NO_MERGE_GATE` 也只借 `paper_ablation.py:421-452` 的单机制映射，不能复用其 evidence hook。

## 8. 题目读取、选择与运行循环

| 等级 | 文件与符号 | 可借内容 | 为什么不整模块复用 | Slim V2 对应组件 |
|---|---|---|---|---|
| 只借局部逻辑 | `src/tokenshare/experiments/paper_catalog.py:471` `_load_jsonl` | UTF-8 JSONL 逐行读取的基本写法 | 私有函数位于 1715 行文件中；模块同时加载 catalog 审计、Lean preflight、digest 与分布门禁 | 新写约 15–30 行 `load_cases(path)`，只做 JSON 解码和必要字段读取 |
| 只借局部逻辑 | `src/tokenshare/experiments/paper_suite_scale.py:236` `select_factorization_cases` | 根据明确 case id/分层字段选择题目的思路 | 同文件还生成/验证 scale policy、source body 与 digest | 新写显式 `case_ids` 或简单字段过滤；选择规则必须来自指标/实验权威文档 |
| 只借结构 | `src/tokenshare/experiments/runner.py:26` `ExperimentRunner.run` | `case -> adapter -> result -> output` 的直线控制流 | 旧 `ExperimentCase`、profile、report 都绑定 Phase 8 语义和旧 manifest | 新写一个很小的 Slim 调度循环；不要继承旧 runner |
| 只借并发写法 | `src/tokenshare/experiments/factorization_500_ai.py:397`、`:425` | 顺序循环与 `ThreadPoolExecutor` 收集结果的基本模式 | 文件绑定 direct-500 题集、AI executor、旧 settings/report | 初版优先顺序运行；只有权威条件需要 worker 扩展性时才启用系统 worker backend，不用外层线程伪造系统并发 |

## 9. 普通文件输出与最小恢复

| 等级 | 文件与符号 | 可借内容 | 输入/输出 | Slim V2 用法与注意事项 |
|---|---|---|---|---|
| 只借局部逻辑 | `src/tokenshare/experiments/factorization_500_ai.py:460` `_write_current_outputs`；`:1257` `_write_jsonl`；`:1268` `_write_json` | UTF-8 JSONL/JSON 输出格式与 `ensure_ascii=False` | Python dict/list -> JSONL/JSON | 旧实现每完成一题就重写全部 JSONL，不适合大实验。Slim V2 应每个 root 完成后 append 一行 |
| 只借局部逻辑 | `src/tokenshare/experiments/factorization_500_ai.py:1244` `_write_summary_csv` | `csv.DictWriter` 的稳定列顺序写法 | 指标行 -> CSV | 列名必须由指标权威文档定义；不沿用 direct-500 的旧列集 |
| 只借局部逻辑 | `src/tokenshare/experiments/report.py:65` `_write_json`；`:72` `_write_summary_csv` | 很小的 JSON/CSV writer 结构 | metrics dict -> 文件 | 文件虽小，但入参模型和列名属于 Phase 8；复制简单 I/O 写法比 import 更干净 |
| 只借局部逻辑 | `src/tokenshare/experiments/paper_report.py:629` `_atomic_write_json`；`:643` `_atomic_write_text` | 临时文件后 `replace` 的原子汇总写法 | 最终 metrics/manifest -> JSON/CSV/text | 只抄 I/O helper；不要 import paper report |
| 只借思路 | `src/tokenshare/runtime_paths.py:45` `resolve_experiment_output_root` | 显式 `--output-dir` 优先、缺省输出根的思路 | CLI path -> `Path` | 原模块强制默认数据在仓库外；Slim V2 可直接要求显式输出目录，避免再引入路径政策 |
| 禁止复用 | `src/tokenshare/experiments/paper_formal_checkpoint.py`（1314 行）、`paper_formal_evidence.py`（7425 行） | formal checkpoint、父链、repair、canonical evidence | 大量 manifest/digest/evidence | Slim V2 不需要。恢复只扫描普通结果行的文本主键 |
| 禁止复用 | `src/tokenshare/storage/events.py:203-348` append/read API | 带 hash/idempotency 的系统 event ledger | ledger events -> chained JSONL | 它继续服务系统本体，但不得拿来当 Slim 结果 writer |

现有源码没有找到适合直接复用的“普通 JSONL 完成主键恢复器”。因此这里明确记录一个应新写的小组件：

- `append_result(row)`：每个 root 结束后向 `results.jsonl` 追加一行并 flush；
- `load_terminal_keys(path)`：启动时逐行读取，收集终态行的 `(experiment_id, condition_id, case_id, repeat_id)`；
- 调度循环遇到已有终态主键就跳过；失败行是否重跑由一个普通 CLI 参数决定；
- 不写 hash、不写 commit token、不维护 CURRENT/PENDING、无 compaction/repair/publication。

最简单且不容易出现跨文件半写入的布局，是每个 root 在 `results.jsonl` 追加一个自包含对象，内部嵌套该 root 的 `units`、`attempts`、`faults` 小数组；真实 raw response 单独写入同目录下 `responses/`，结果行只记相对路径。只有在单行确实过大时，才拆成 `roots.jsonl`、`attempts.jsonl` 等多个文件，不应为了“规范化”预先增加同步与恢复复杂度。

## 10. 指标公式、原始字段与聚合

最终指标必须只以 `Doc/SlimV2/slim_v2_experiment_metrics_authority.md` 为准。旧 paper 指标代码和旧实验设计只用来定位数学 helper，不能反向覆盖当前的 split、trace、fault、ablation、pricing、root timing、字段名或统计口径。

### 10.1 当前公式定位

| 范围 | 当前权威位置 | 复用代码时必须遵守的覆盖规则 |
|---|---|---|
| 统一分母、失败、root timing | 指标权威第 1.2 节 | 固定预注册 root 分母；roots 串行；root start 在任何 AI unit 调度前，terminal 在 root 完成/失败后；缺失不是 0 |
| 价格与 token | 指标权威第 1.4 节 | 固定 `slim_v2.pricing.2026-08-20`；DeepSeek 峰/谷、SiliconFlow 四模型平价表；reasoning 是 completion 子集，不重复计数 |
| Experiment 1 | 指标权威第 2 节 | DeepSeek 真实调用；每个 root 的正常 `run_root()` 结束后立即对 unscheduled planned units 执行 Slim-local coverage tail。protocol 与 tail trace origin 分开，tail 资源单列且不延长 root runtime |
| Experiment 2 | 指标权威第 3 节 | 取消独立 20-way split，完整继承 Exp1 plan；六个 worker 档用相同 source trace/latency 进逻辑调度器；repeat 离散口径以当前权威的 `(max-min)/mean` 为准 |
| Experiment 3 | 指标权威第 4 节 | 五类 fault 只注入 ordinal 0；按稳定排序均匀选 target；固定 seed 的 token/latency 扰动；模拟资源不得称为真实 provider usage |
| Experiment 4 | 指标权威第 5 节 | 11 modes、mode-blind challenge、pair/quadruple 与 interaction；五个正式 delta 名称无附加后缀 |
| Experiment 5 | 指标权威第 6 节 | 四个 SiliconFlow endpoint、三个 repeats、零 replacement；wall-clock 用完整 root lifecycle，不用 first dispatch 代替 root start |

当前权威保留 cost。实现使用独立的普通 pricing projector：保存 provider 原始 cache/prompt/completion usage、provider request start、`pricing_version/pricing_tier` 与 `cost_estimate_cny`，reducer 只聚合。API 调用、调度和恢复路径均不需要 budget、reservation 或 stop gate。

### 10.2 每个 root 结果行建议保存的最小字段族

这里列字段族而不冻结 JSON schema；最终命名由权威指标文档和接线契约确定。

| 字段族 | 必要字段 | 支持的指标 |
|---|---|---|
| 文本身份 | `experiment_id`、`condition_id`、`case_id`、`repeat_id`、domain、difficulty/topic、model、worker count、mode、fault type/rate | 分组、配对、恢复跳过 |
| root 结果 | root status、是否产生最终结果、领域正确性判断、failure stage/kind | completion、端到端正确率、failure breakdown |
| 时间 | `root_start_at_ms`、`root_terminal_at_ms`、`runtime_wall_clock_ms`；在线调用另存 `provider_request_started_at_utc` 与 `provider_latency_ms` | Exp1/5 真实时间、Exp2 speedup、Exp3/4 逻辑 delta、DeepSeek 峰/谷价格选择 |
| usage/价格 | 每 attempt 的 prompt/cache-hit/cache-miss/completion/reasoning/total tokens、`pricing_version/pricing_tier/cost_estimate_cny`；reasoning 是 completion 子集；标记 `actual` 或 `simulated trace-attributed` | token/cost totals、paired multiplier/delta、discarded resources；禁止 reasoning 双算 |
| unit/调度 | planned/executed/unscheduled unit count、observed peak concurrency、in-flight-at-witness、每 unit source latency | Exp2 解释性并发与自然早停 |
| attempt | `unit_id`、`attempt_id`、ordinal、result kind、是否 first/replacement、关联的 prior attempt/fault、是否 checkable/accepted/rejected/canonical | Exp3 replacement/reassignment、Exp5 first-attempt 指标 |
| fault/death | fault target/injected/type、known-wrong、detected、escaped/canonicalized、dead count、target/observed kill progress | Exp3 拦截/逃逸、worker death、kill-progress error |
| ablation | 11-mode 名称、`disabled_mechanisms`、最终 mechanism booleans、mode-blind challenge、wrong-canonical/raw-only/stuck/premature-merge 事件布尔或计数 | Exp4 专项指标、FULL 配对与四端 interaction |

`raw_response_json` 可以单独放在 `responses/` 下；`results.jsonl` 只保存相对文件路径。无需 artifact ref、content hash、manifest digest 或 lineage id。

provider 没返回 usage 时对应 token 字段必须保存为 `null`，不能当成 0；只有字段可由其余 usage 数学确定时才允许派生。普通关联字段如 `attempt_id`、`replacement_of_attempt_id`、`fault_id` 是计算指标所需的业务主键，不是 evidence lineage。

### 10.3 现有指标代码怎么借

| 等级 | 文件与符号 | 可借内容 | 决定 |
|---|---|---|---|
| 只借公式 | `src/tokenshare/experiments/metrics.py:144` `final_correctness_for_factorization` | 因子乘积等于目标数的最小判断 | 系统 plugin verifier/checker 仍是权威领域判断；不要让 reducer 重新发明验证语义 |
| 轻量适配 | `src/tokenshare/local_runtime/projection.py:75` `build_runtime_observation`；`:300` `_observed_peak_concurrency` | wall-clock、planned/dispatched/completed/unscheduled、in-flight、worker facts、峰值并发 | 前者可直接调用；后者是私有纯函数，后续可抽到小公共 helper |
| 只借公式 | `src/tokenshare/experiments/paper_metric_contract.py:2655` `_execute_formula` | count/sum/ratio/subtract/elapsed/median/pair ratio/efficiency/range/utilization 的纯数学实现 | 复制必要公式到普通 reducer；禁止 import contract engine |
| 只借局部逻辑 | `src/tokenshare/experiments/paper_metrics.py:1786` `_failure_stage_counts`；`:1795` `_rate` | failure group count 与安全除法 | 两个 helper 可化简重写；不使用该模块的旧 attempted denominator 聚合 |
| 只借字段与配对算法 | `paper_exp1_metrics.py:195`、`paper_exp2_metrics.py:437`、`paper_exp3_metrics.py:235`、`paper_exp4_metrics.py:294`、`paper_exp5_metrics.py` 的 projector | 当前指标名、分组键、配对方向和零分母规则 | 不 import；这些文件合计数千行，并依赖 metric contract、observation lineage、publication block |
| 禁止复用 | `paper_metric_contract.py`（2961 行）、`paper_metric_observations.py`（1198 行）、`paper_metric_registry.py`（471 行）、`paper_metrics.py`（1914 行） | formula graph、membership、role route、lineage、publication | Slim reducer 使用普通 Python group/count/sum/median 和清楚的 `None` 规则即可 |
| 只借输出写法 | `src/tokenshare/experiments/report.py:65-94`、`factorization_500_ai.py:1244-1275` | JSON/CSV 序列化 | 使用新的精简列集，不使用旧 report model |

`paper_exp*_metrics.py` 可以在实现时作为公式交叉核对材料，但不应成为运行时依赖。新 `metrics.py` 应只从 `results.jsonl` 和必要的普通 response 文件读取数据。

## 11. 暂定的 Slim V2 新代码预算

以下是当前审计发现的“确实缺失、但应当很小”的设施，不是鼓励继续搭框架：

| 新组件 | 预计规模 | 唯一职责 |
|---|---:|---|
| `slim_ai.py` | 约 130–220 行 | 一次真实 provider 调用，返回 raw text/JSON、usage、latency、model identity 与普通错误 |
| `case_source.py` | 约 20–50 行 | 读取权威题目 JSONL 并按显式 id/字段过滤 |
| `folder_response_source.py` | 约 50–100 行 | 从普通目录按三元文本主键读取唯一 trace；exact ordinal优先，缺失时选择同 trace 最后自然 attempt；不使用 manifest/digest、不调用 provider |
| `trace_tail.py` | 约 100–220 行 | 在每个 Exp1 `run_root()` terminal 后只补该 root 的 unscheduled planned units，写 `coverage_tail` trace 与独立 wall/token/cost summary；不修改 coordinator/root result |
| `result_sink.py` | 约 50–100 行 | append JSONL、读取终态主键、写最终 JSON/CSV |
| `run_slim.py` | 约 130–300 行 | 展开权威实验条件、调用系统入口；Exp1 在下一 root 前串接 coverage tail；写协议结果与 tail summary；不计算预算或资格 |
| `metrics.py` | 若保留当前全部 Exp1–5 指标约 250–600 行；新权威删减后应更小 | 枚举结果行并按明确公式聚合 |

这些规模是复杂度上限的提醒，不是设计承诺。三个前置权威文档已经获批并冻结；最终文件结构和接口仍须等 Slim V2 设计规格获用户批准后确定。

## 12. 明确禁止进入依赖图的旧设施

| 文件 | 当前规模 | 禁止原因 |
|---|---:|---|
| `src/tokenshare/experiments/paper_formal_runner.py` | 17340 行 | 一个运行器同时承担 plan、resume、budget、checkpoint、evidence、projection 与 publication，是当前最大结构性风险 |
| `src/tokenshare/experiments/run_paper_pipeline.py` | 12182 行 | pipeline command、preflight、publication 和跨阶段接线高度耦合 |
| `src/tokenshare/experiments/paper_formal_evidence.py` | 7425 行 | formal evidence/checkpoint authority，正是 Slim V2 要删除的设施 |
| `src/tokenshare/experiments/paper_response_bank.py` | 6817 行 | response bank manifest、identity、digest 与闭包验证；普通文件夹 source 足够 |
| `src/tokenshare/experiments/run_paper_experiments.py` | 5718 行 | 旧全实验入口，混合 catalog、条件、在线检查与报告 |
| `src/tokenshare/experiments/paper_budget.py` | 3237 行 | 预算预测、授权与审计不属于用户目标 |
| `src/tokenshare/executors/response_bank.py`、`trace_backed.py`、`ai_api_replay.py` | 974 / 1438 / 130 行 | 共同绑定 manifest/hash/trace lineage；不得为了离线响应复用而带回证据链 |

后续 Agent 若发现某个必需算法只存在于上述文件，应把算法缩写进新的单职责组件，并在评审中说明为什么不能从系统公开接口取得；不得直接 import 整个旧模块。

结构性结论很明确：问题不是某一个 bug，而是 orchestration、预算、checkpoint、证据、投影、发布逐步聚合进同一批 runner；factorization 与 Lean 的 paper adapter 又各自复制 provider/trace/fault/metrics 接线。Slim V2 应建立独立入口并绕开这些模块，而不是继续给它们加开关。只有新入口完成一条端到端路径后，才讨论物理删除旧文件；系统 core、plugin runtime、worker/scheduler 和 provider transport 不应随旧 paper 设施一起删掉。

## 13. 后续 Agent 快速工作顺序

1. 按顺序完整阅读三个已获批的前置权威文档；本文不能覆盖它们。
2. 完整阅读本文，先核对固定 40 位 SHA/tag，再确认第 2 节无门禁边界。
3. 按“系统对外接线契约”只打开第 5 节列出的当前 baseline 公开系统入口和插件 runtime，并先运行对应 focused tests。
4. 按论文指标清单确定每个 root 必须保存的最小原始字段。
5. 从本文“直接复用”项建立薄接线；对“只借局部逻辑”项只执行 `git show 3489533e79cde05d6ae2a0c9f139785249f60f09:<path>`，阅读点名符号和最小相邻定义，不 checkout、不 import 模块。
6. 先完成一条 case 的 `input -> system -> results.jsonl -> metrics` 垂直路径，再扩展实验条件。
7. 若新设计把 budget audit、receipt、hash/digest 验证、publication/eligibility gate 或 evidence completeness 再次加入 Slim 实验设施依赖图，视为范围回流并删除；系统本体内部已有字段不因此被擅自重写。

## 14. 实现时优先复用的测试锚点

这些测试只作为定位现有语义的快捷入口；Slim V2 应迁移行为断言，删除其中对 evidence/digest/publication 的断言。

| 能力 | 现有测试位置 |
|---|---|
| SiliconFlow/OpenAI/DeepSeek raw、usage、model、reasoning envelope | `tests/executors/test_ai_api_transport.py:193-245`；`test_ai_api_openai_transport.py:76-129`；`test_ai_api_deepseek_transport.py:99-132` |
| parser failure 不重试且 raw 保留、usage missing | `tests/executors/test_ai_api_executor_parser.py:27-58`；`test_ai_api_executor_success.py:953-990` |
| logical latency、稳定事件顺序 | `tests/local_runtime/test_logical_scheduler.py:17-158` |
| thread capacity、真实 process worker death/replacement | `tests/local_runtime/test_worker_death_recovery.py:184-365` |
| retry/requeue、no-return lease expiry | `tests/local_runtime/test_submission_and_recovery.py:339-522` |
| natural early stop | `tests/local_runtime/test_coordinator_logical_schedule.py:229-330` |
| 五类 fault 语义 | `tests/experiments/test_paper_faults.py:467-680` |
| no-verification/no-requeue/merge ablation | `tests/local_runtime/test_submission_and_recovery.py:636-715`；`tests/experiments/test_paper_ablation.py:39-108` |
| Exp1–5 指标分母、配对、零分母和方向 | `tests/experiments/test_paper_exp1_metrics.py` 至 `test_paper_exp5_metrics.py` 中对应 focused tests |
| 简单 JSONL/CSV 输出 | `tests/experiments/test_factorization_500_ai.py:87-135` |

## 15. 本次审计与验证边界

- 固定 archive commit 从共享脏工作树以 111 个文件的显式 allowlist 建立；没有新增 `local/`、`.vscode/`、receipt、日志、运行输出或历史待办计划。archive 不是 release，也不表示旧 paper/formal pipeline 已完成。
- archive 创建 tag 前运行两组离线 focused tests：公开 transport/runtime/plugin 接口 `88 passed`，稳定 archive 专项 `29 passed`，合计 `117 passed`。4 份依赖陈旧 readiness、已删除 API 或未闭合 worker-death/trace 期望的新增 WIP 测试没有进入 archive allowlist；原共享脏工作树中的副本未修改。
- `codex/slim-v2-baseline` 从 `main` 的 `c963d7f8b9cc2346267279170740910a42de54fd` 建立。清单点名的 39 个公开符号全部可从 baseline 导入；transport、logical scheduler、recovery、coordinator、worker death、Factorization runtime 和 Lean runtime focused suite 为 `81 passed`。
- 本清单中的 85 个 `path:line[-line]` 代码跨度均已对固定 archive SHA 执行 `git show` 越界/歧义审计，36 个关键公开符号的点名行也逐项匹配；Slim harness 定向测试为 `25 passed`。
- baseline 因此没有明确的直接复用公共接口缺口，本轮没有移植或修改任何 shared core/local_runtime/plugin/executor 文件。若后续获批实现测试暴露具体缺口，仍须按 README 的 shared-code 审批边界处理。
- 本轮没有调用真实 API，没有运行 R52/R53/R54、Full 或 LeanAudit，也没有读取历史运行证据。本文仍只是一份支持性复用清单，不冻结 Slim V2 的最终接口和设计规格。官方价格来源见 `Doc/SlimV2/slim_v2_official_pricing_sources_20260820.md`。
