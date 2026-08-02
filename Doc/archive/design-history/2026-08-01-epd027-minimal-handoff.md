# EPD-027 最小交接摘要（2026-08-02）

> 本摘要是 Task 10 final review PASS 后的覆盖式状态快照。历史过程由 git history 保留；下述 accepted/queued 状态是当前权威。

## 当前状态

- Active feature：`feat-011`（`in-progress`）。
- Active focus：`paper-metric-contract-trace-bank-and-traceability-plan`（下一项 queued）。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 进度：Task 0–10 accepted（`11/35`）；Task 11 queued/next。
- 正式矩阵：**NO-GO**。trace-backed executor、formal runner/metrics/renderer/replay 等后续接线尚未完成。

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
| 11–17 | queued | trace-backed executor / dual provenance 与 Exp1–5 projector、registry 接线；Task 11 next。 |
| 18–26 | queued | evidence class、formal runner、lineage、render/report、pressure/replay、L3 planning、paid receipt validation。 |
| 27–34 | queued | bounded CLI、execution/publication gates、launcher/profile 迁移与 L1–L4 acceptance。 |

## Task 10 accepted 证据

- 代码提交：`933ca71bd3aca9b62ad4f4b399f43a8f5d8092e1`（`feat(runtime): add parent-owned trace delivery commits`）。
- 严格 16 文件：11 个 production 文件与 5 个 tests 文件；无 allowlist 外改动、无 output/secret 文件。
- Production：`executors/contracts.py`；`core/models.py`、`core/leases.py`；`protocol_engine.py`；`storage/events.py`、`storage/sqlite_index.py`；`local_runtime/contracts.py`、`coordinator.py`、`workers.py`、`process_worker_child.py`、`projection.py`。
- Tests：`tests/core/test_phase1_models.py`、`tests/test_phase3_execution_flow.py`、`tests/local_runtime/test_submission_and_recovery.py`、`tests/local_runtime/test_trace_delivery_parent_commit.py`、`tests/storage/test_attempt_ordinal_migration.py`。
- Canonical accepted validation：exit `0`，`106 passed in 48.71s`；11 个 production 文件 `py_compile` exit `0`。
- Final reviewer：`PASS`；Task10 已 accepted。
- 验收后测试最小化：只改 2 个测试文件，删除 37 行 / 3 cases；最小验证 exit `0`，`35 passed in 10.61s`。
- 本状态持久化不重复运行测试、Fast、Full、LeanAudit、provider 或 network；Full 按用户指令 intentionally not run。
- `provider_calls=0`；无 paid receipt，禁止启动真实付费 API。

## Task 10 已建立的边界

- attempt ordinal 持久化到 unit/lease/attempt/request/event/index 路径，replacement/retry 不再靠易漂移的外部推断。
- worker 只准备 typed `PreparedTraceDelivery`；parent coordinator 在校验 run/task/unit/attempt/lease/request/store binding 后执行唯一 commit。
- parent commit 写入 `TRACE_DELIVERY_COMMITTED.v1`，并由 projection/index 恢复；process/thread/sequential backend 共用同一 parent-owned 边界。
- Task 10 没有实现 Task 11 trace-backed executor 或 paper eligibility 接线，不能把 `11/35` 写成完整 pipeline ready。

## 下一步与硬边界

1. 下一项仅实施 Task 11：trace-backed executor / dual provenance。
2. 继续先做 characterization/RED tests、实现、canonical scoped validation、独立 review、测试压缩复审，再持久化 accepted。
3. Experiment 1/5 与 Exp2/3 online checks 的 provider 调用必须等待经 Task 26 校验的 paid receipt；当前 `provider_calls=0`。
4. 正式全量矩阵保持 **NO-GO**；不得把历史 smoke、scripted/capturing 或 trace consumption 冒充当次 `real_transport`。
5. 不 push、不 merge、不创建 PR。
