# EPD-027 最小交接摘要（2026-08-02）

> 本摘要是 Task 13 final review PASS 后的覆盖式状态快照。历史过程由 git history 保留；下述 accepted/queued 状态是当前权威。

## 当前状态

- Active feature：`feat-011`（`in-progress`）。
- Active focus：`paper-metric-contract-trace-bank-and-traceability-plan`（下一项 queued）。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 进度：Task 0–13 accepted（`14/35`）；Task 14 queued/next。
- 正式矩阵：**NO-GO**。Task 13 只闭合 standalone Experiment 2 trace/online metric observation projectors；Exp3–5 projector、registry、formal runner/renderer/replay 等正式接线尚未完成。

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
| 12 | accepted | `a1183c6645d067409b2cfbce54c909aaf4b569c1`，standalone Experiment 1 metric observations；含 Task 2 contract 最小 profile/digest 同步。 |
| 13 | accepted | `3f8d4ec2284d472896b706a3d94112f81be7e7c2`，standalone Experiment 2 trace/online metric observations。 |
| 14–17 | queued | Exp3–5 projector、registry 与后续 consumer 接线；Task 14 next。 |
| 18–26 | queued | evidence class、formal runner、lineage、render/report、pressure/replay、L3 planning、paid receipt validation。 |
| 27–34 | queued | bounded CLI、execution/publication gates、launcher/profile 迁移与 L1–L4 acceptance。 |

## Task 13 accepted 证据

- 代码提交：`3f8d4ec2284d472896b706a3d94112f81be7e7c2`（`feat(experiments): add Experiment 2 metric observations`）。
- 严格 2 文件：`src/tokenshare/experiments/paper_exp2_metrics.py` 与 `tests/experiments/test_paper_exp2_metrics.py`；无 allowlist 外改动、无 output/secret 文件。
- review 补充 RED=`3 failed`，对应 targeted 修复后 `3 passed`；canonical=`16 passed in 13.85s`；测试最小化后仍为 `16 passed in 12.30s`。
- production compile、diff-check、forbidden import/alias scan 与 allowlist 均通过；final reviewer=`PASS`。
- `provider/network calls=0`，无 paid receipt；正式矩阵仍为 **NO-GO**。
- 本状态持久化不重复运行测试、Fast、Full、LeanAudit、provider 或 network；Full 按用户指令 intentionally not run。

## Task 13 已建立的边界

- `build_exp2_trace_observations(...)` 与 `build_exp2_online_observations(...)` 是纯 projector，不 import formal runner/renderer，不修改 registry，也不复制协议状态机。
- trace 配对只使用 digest-bound case reference、repeat/sample 与 worker identity；失败 root 保留固定分母，speedup 仅在同 case/repeat/sample 两端成功且 logical makespan 为正时产生。
- trace slot/token/cost 只从 committed source attribution 累计；online 429/timeout union 按唯一 first attempt 去重，post-bank intersection/severe 规则在完整 main trace 前保持 `not_evaluated_pre_bank`。
- throughput 与旧 `efficiency` alias 继续禁用；输出仍为 observation draft，`paper_eligible=false`。

## 下一步与硬边界

1. 下一项仅实施 Task 14（standalone Experiment 3 projector）；不得越过严格串行顺序。
2. 继续先做 characterization/RED tests、实现、scoped validation、独立 review、测试压缩复审，再持久化 accepted。
3. Experiment 1/5 与 Exp2/3 online checks 的 provider 调用必须等待经 Task 26 校验的 paid receipt；当前 `provider_calls=0`。
4. 正式全量矩阵保持 **NO-GO**；不得把历史 smoke、scripted/capturing 或 trace consumption 冒充当次 `real_transport`。
5. 不 push、不 merge、不创建 PR。
