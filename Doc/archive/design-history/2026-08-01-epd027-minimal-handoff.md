# EPD-027 最小交接摘要（2026-08-02）

> 本摘要是 Task 11 final review PASS 后的覆盖式状态快照。历史过程由 git history 保留；下述 accepted/queued 状态是当前权威。

## 当前状态

- Active feature：`feat-011`（`in-progress`）。
- Active focus：`paper-metric-contract-trace-bank-and-traceability-plan`（下一项 queued）。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 进度：Task 0–11 accepted（`12/35`）；Task 12 queued/next。
- 正式矩阵：**NO-GO**。Task 11 只闭合 trace-backed executor / dual provenance 与两个领域 adapter 的最小接点；formal runner/metrics/renderer/replay 等正式接线尚未完成。

## Task 0–34 压缩状态表

| Task | 状态 | 提交 / 下一范围 |
|---:|---|---|
| 0 | accepted | `ed0b056a`，offline network tripwire。 |
| 1 | accepted | `78fbddaa`，EPD-027 profile 与 paid authority 分离。 |
| 2 | accepted | `d2b16f89`，machine-readable paper metric contract。 |
| 3 | accepted | 前置 `860b7c48`、`4ea293b1`；Task `f9773944`；测试压缩 `66643084`。 |
| 4 | accepted | `0cbda1df`，prepared request identity/admission ABI。 |
| 5 | accepted | `58cda71f`，immutable response-bank primitives。 |
| 6 | accepted | `5f97034c`，semantic inventory / zero-engine preflight。 |
| 7 | accepted | `aadbd483`，SQLite WAL atomic budget authority。 |
| 8 | accepted | `6802918d`，durable acquisition/publish/resume/reconcile。 |
| 9 | accepted | `1109e852`，deterministic logical source-latency scheduler。 |
| 10 | accepted | `933ca71bd3aca9b62ad4f4b399f43a8f5d8092e1`，attempt ordinal 与 parent-owned trace delivery commit ABI。 |
| 11 | accepted | `1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`，trace-backed response-bank executor、dual provenance 与 Factorization/Lean adapter 接点。 |
| 12–17 | queued | Exp1–5 projector、registry 与后续 consumer 接线；Task 12 next。 |
| 18–26 | queued | evidence class、formal runner、lineage、render/report、pressure/replay、L3 planning、paid receipt validation。 |
| 27–34 | queued | bounded CLI、execution/publication gates、launcher/profile 迁移与 L1–L4 acceptance。 |

## Task 11 accepted 证据

- 代码提交：`1b5d098ebfdbf3a13ab1175a5708ddc057a1bddc`（`feat(executors): add trace-backed response-bank execution`）。
- 严格 10 文件：6 个 production 与 4 个 tests；无 allowlist 外改动、无 output/secret 文件。
- Production：`executors/trace_backed.py`、`executors/ai_api.py`、`experiments/factorization_paper_adapter.py`、`experiments/lean_paper_adapter.py`、`local_runtime/contracts.py`、`local_runtime/coordinator.py`。
- Tests：`tests/executors/test_trace_backed.py`、两个 paper adapter test 与 `tests/local_runtime/test_trace_delivery_parent_commit.py`。
- Final trace suite（测试最小化后）：exit `0`，`10 passed in 3.58s`；parent commit=`32 passed`；logical scheduler=`5 passed`；Factorization + Lean targeted=`2 passed`；recovery=`39 passed`；descriptor=`3 passed`。
- Canonical 命令第一次发生 tool timeout；随后 cache 暴露 3 个 v2 descriptor 失败，修复后对应 3 个 nodeids 通过。因原 canonical 命令没有取得完整成功退出，**不得记录 canonical PASS**。
- 6 个 production 文件 compile、diff-check、10-file allowlist 与 secret/output 检查通过；final reviewer=`PASS`。
- 测试最小化删除 7 行 / 1 test；`provider_calls=0`，无 paid receipt。
- 本状态持久化不重复运行测试、Fast、Full、LeanAudit、provider 或 network；Full 按用户指令 intentionally not run。

## Task 11 已建立的边界

- `TraceSourceBinding` 冻结 planned AI unit、replacement ordinal、bank entry 与 source evidence class；缺预注册 replacement 时 fail closed。
- `TraceBackedExecutor` 只流式读取/校验不可变 bank object，不调用 provider；worker 只形成 `PreparedTraceDelivery`。
- parent-only stager 产生当前 run parser/checker/canonical artifacts、current provenance 与 source trace attribution；coordinator 验证当前 artifact 后才提交 `TRACE_DELIVERY_COMMITTED.v1` 并形成普通 engine submission。
- Factorization/Lean 的 parser/verifier/checker 仍由各自 adapter 拥有；没有把领域判断硬编码进协议核心。
- 四个最小 plan-out 接点已获用户批准：`ai_api.py` 增加 v2 descriptor compatibility；`contracts.py`/`coordinator.py` 提供 typed parent stager 与当前 submission 接点；parent commit test 固定该 core-neutral ABI。它们是 Task 11 端到端闭环所需，不代表后续正式 pipeline 已接线。

## 下一步与硬边界

1. 下一项仅实施 Task 12；不得越过严格串行顺序。
2. 继续先做 characterization/RED tests、实现、scoped validation、独立 review、测试压缩复审，再持久化 accepted。
3. Experiment 1/5 与 Exp2/3 online checks 的 provider 调用必须等待经 Task 26 校验的 paid receipt；当前 `provider_calls=0`。
4. 正式全量矩阵保持 **NO-GO**；不得把历史 smoke、scripted/capturing 或 trace consumption 冒充当次 `real_transport`。
5. 不 push、不 merge、不创建 PR。
