---
status: user_approved
document: slim_v2_agent_entry
scope: Slim V2 agent routing and boundaries
---

# TokenShare Slim V2 Agent 导引

本目录是后续 Slim V2 设计与实现 Agent 的唯一入口。根据 2026-08-20 用户决定，除非当前任务明确指定其他维护范围，所有后续实验设施的设计、实现和运行都默认进入 Slim V2，不需要用户重复声明。三份前置文档已获得用户批准并冻结，是 Slim V2 范围内的当前权威；它们不改写 Slim V2 之外的 V1/legacy 状态。常规流程仍由用户逐阶段批准设计规格和实施计划；用户显式启动 `slim_v2_stage_relay_protocol.md` 时，则由该协议规定的多 reviewer 审查、完成标准和 `approved_under_user_delegation` 状态履行这两次委托审批。两种流程都不能绕过 shared-code 修改所需的再次用户批准。

## 目标

Slim V2 要以最小设施完成：

- 为 Experiment 1 的正式运行和 Experiment 5 调用真实 AI API；Experiment 1 每个 root 先按现有协议终态完成，再立即用 Slim-local coverage tail 补齐该 root 尚未调度的 planned units，使每个 planned unit 都有唯一 per-unit trace；
- 调用现有 TokenShare 协议本体、Factorization 插件和真实 Lean 插件；
- 表达并运行 Experiment 1–5 的冻结场景；
- 让 Experiment 2–4 原样继承 Experiment 1 的任务切分、子任务语义、prompt 与依赖，并按 `case_id × source_repeat_id × planned_ai_unit_id` 精确复用 Experiment 1 的 protocol/coverage-tail trace；其中下游 `source_repeat_id=0`，与各实验自己的 `repeat_id` 分离；请求 ordinal 缺失时只回退到同一 trace 最后一个自然 attempt，不新增 AI 调用；
- 记录计算论文必须指标所需的最小原始 JSONL；
- 用冻结的官方普通价格表把 provider usage 换算为成本；reasoning token 只按 completion/output 计一次；
- 从普通输出文件夹枚举 JSONL 并生成 CSV/JSON 指标。

所有 roots 在 runner 层串行执行；Experiment 1 的 coverage tail 在当前 root terminal 之后、下一个 root start 之前完成，tail 时间与资源单列，不进入正常协议正文指标。`worker_count` 只控制单个 root 内的 AI-unit 并发。真实 API 边界按“单 provider entry 输入、raw/usage/latency/model 输出、薄 execution bridge”的能力设计，不要求复用旧 `AIAPIExecutor` 整类。

Slim V2 的非目标是论文级审计、防伪、复杂来源追踪、旧实验设施修复或生产级运行平台。不要重新引入 receipt、预算授权、digest/lineage closure、publication gate、evidence closure 或 paper eligibility。`slim_v2.pricing.2026-08-20` 只是成本换算常量，不是预算或门禁。

Experiment 2 的六档在线并发检查和 Experiment 3 的小型在线恢复检查已经退出 Slim V2；不要为它们选择 case、保留调用预算、实现 runner 或生成指标表。除 Experiment 2 的六个 worker 档外，Experiment 1、3、4、5 全部固定 `worker_count=10`。

## 唯一阅读顺序

后续明确属于 Slim V2 的任务，只按以下顺序进入：

1. `Doc/SlimV2/README.md`
2. `Doc/SlimV2/slim_v2_experiment_metrics_authority.md`
3. `Doc/SlimV2/slim_v2_system_integration_contract.md`
4. 当前 focus 已进入获批的设计或实现阶段时，完整阅读支持材料 `Doc/SlimV2/slim_v2_reuse_inventory.md`，然后才打开源码；纯讨论、指标或价格维护任务不必读取它。

复用清单只提供精确源码位置、公开符号和复用等级，不能覆盖前三份权威。价格来源摘要同样只在维护价格时读取。当前 baseline 中的共享公共接口可按接线合同定点审计；旧 paper/formal 实现只能按复用清单中的固定 archive SHA 和 allowlist 使用 `git show <40位SHA>:<path>` 定点只读。不得 checkout archive branch/tag、创建 archive 工作副本或递归展开旧目录来“了解历史”。

## 禁止阅读和禁止工作

- 不要广泛阅读旧 paper/formal pipeline 文档或实现来寻找“正确版本”。唯一例外是：在获批设计/实现中确实需要复用清单标为“只借局部逻辑/语义/结构”的条目时，只能对清单固定的 40 位 archive SHA、点名路径和点名符号执行 `git show`，并把阅读上下文限制在该符号及最小相邻定义。
- 不要读取 `Doc/archive/`、`Doc/TechnicalDocument/phase-1-6-archive/` 或历史计划。
- 不要读取 `local/` 历史运行目录、Rxx 输出/日志或 `TokenShareData` 历史实验输出。
- 不要试图修复、裁剪或复活旧实验设施。
- 不要使用旧 formal runner、旧实验 runner、旧 response-bank authority 或任何旧门禁补齐 Slim 数据。
- 固定 archive 只提供人工只读参考；Slim V2 runtime、测试和 CLI 不得 import、调用、动态加载或从文件路径读取 archive/legacy `paper_*` 实现。
- 不要重新引入 receipt、budget authority、digest、lineage、publication gate 或 evidence closure。
- 不要把久未更新的基线测试结果当成实验参数、接口或完成状态的判断依据；接口判断以当前允许的实际代码和 focused tests 为准。

若三份文档指出一个公共接口问题仍无法回答，停止扩大阅读范围，先向用户报告具体缺口。

## 允许工作的目录

三份前置文档已经冻结；后续获批的 Slim V2 工作，其常规写入范围仅为：

- `src/tokenshare/experiments/slim_v2/`
- `tests/experiments/slim_v2/`
- `Doc/SlimV2/`

`src/tokenshare/core/`、`src/tokenshare/local_runtime/`、Factorization/Lean plugin 与 executor 默认只读。只有三份文档已证明存在明确接口缺口、Slim-local adapter 无法解决，并再次获得用户批准，才能修改共享系统代码。

## 输出位置

推荐每次 run 写入：

```text
TokenShareData/outputs/slim_v2/<run_id>/
```

该目录只需要普通文件，例如 condition/root inventory、Experiment 1 protocol/coverage-tail per-unit trace JSONL、逐 root JSONL、tail resource summary、诊断 JSONL、reducer 生成的 CSV/JSON 和简短 run summary。reducer 必须能够只靠这个目录工作。

## 最小完成标准

- 真实 API 能调用，secret 不进入输出。
- Factorization 和 Lean 各至少跑通一个 root；Lean 使用真实 checker 和 root recheck。
- Experiment 1–5 的冻结 condition 参数都可表达。
- Experiment 1 每个 root 先冻结正常协议终态，再立即补齐 unscheduled planned units；每个 planned unit 只有一条标明 `protocol/coverage_tail` 的 trace，tail 不重复调用 protocol unit、不延长 root runtime，且在下一 root 前完成。
- Experiment 2–4 能严格按 `case_id × source_repeat_id × planned_ai_unit_id` 复用 Experiment 1 trace，普通字段核对一致，exact ordinal 缺失时确定性使用同 trace 最后自然 attempt，且运行期间 provider calls 为 0；Experiment 2 不使用独立 split profile。
- Experiment 3 能按 planned first-attempt units、ordinal 0 和冻结扰动公式执行五类 fault；正文 token/latency 资源明确标为 simulated trace-attributed。
- roots 串行，`root_start_at_ms`、`root_terminal_at_ms` 与 `runtime_wall_clock_ms` 满足冻结生命周期定义；Exp1 正文批次 wall-clock 使用 protocol runtime 之和，`trace_tail_wall_clock_ms/tokens/cost` 单列。
- Exp1/5 保存 provider 原始 usage、request start 与 `pricing_version/pricing_tier`，按指标权威的官方表计算成本；`reasoning_tokens` 不与 `completion_tokens` 重复相加。
- 原始 JSONL 字段足以计算全部必须指标。
- 单 root 失败仍写一行记录，不丢失固定分母。
- metrics reducer 能从普通输出文件夹生成 CSV/JSON。
- 不依赖任何旧 formal gate 或旧实验输出。

## 工作纪律

- 一次只实现一个明确 focus；不要同时重做 runner、hooks、数据 schema 和 reducer。
- 文档只做路由、边界和冻结合同，不复制完整系统规格。
- 发现指标字段没有实际来源时，先记录接口缺口；不得从旧 pipeline 偷取数据或自行改变指标。
- 在实现前先写该 focus 的 focused tests；真实 API 调用必须得到用户对相应执行的明确授权。
- Slim V2 默认不运行 Lean 专项测试、LeanAudit、全量 Lean catalog、`lake`/`lean` 回归或为了重新证明现有系统机制正确而执行的 Lean 测试。调度、worker、恢复和 reducer 优先用 Factorization、fake checker、固定 fixture 或静态合同测试；只有 Slim-local Lean 接线本身无法由这些轻量方法定位时，才允许在运行前记录理由和时间上限后执行最小单 case Lean smoke，禁止扩大成 suite。论文 representative/full 中权威要求的 Lean roots 是实验样本，不属于这里禁止的额外 verification。
- Thread/Process backend 不由用户预选；实施 Agent 必须先用 Factorization 的 k>1 focused tests 和 Lean 的 fake/固定 fixture 接线测试给出答案。worker death 的真实进程终止语义仍使用 Process。

## 后续顺序

1. 本目录三份前置文档已经用户批准并冻结（已完成）。
2. 编写 Slim V2 设计规格。
3. 用户批准设计规格；若用户已显式启动串行接力，则由接力协议的 Stage 1 委托审批收口。
4. 编写分 focus 的实施计划。
5. 用户批准实施计划后才开始写代码；若用户已显式启动串行接力，则由接力协议的 Stage 2 委托审批收口。

## 可选的串行新任务接力

若用户希望把设计、实施计划、代码监督和 representative 运行交给彼此上下文隔离的全新 Codex 任务串行执行，读取 `Doc/SlimV2/slim_v2_stage_relay_protocol.md`。该文档只定义阶段 owner、子 Agent、checkpoint、短交接胶囊、委托审批、30 分钟恢复 heartbeat 和下一任务创建流程；它不能覆盖本 README、指标权威或系统接线合同，也不能授权修改 shared code。未由用户显式选择接力流程时，不自动创建新任务或 heartbeat。
