# EPD-027 最小交接摘要（2026-08-02）

> 本摘要是 Task 12 final review PASS 后的覆盖式状态快照。历史过程由 git history 保留；下述 accepted/queued 状态是当前权威。

## 当前状态

- Active feature：`feat-011`（`in-progress`）。
- Active focus：`paper-metric-contract-trace-bank-and-traceability-plan`（下一项 queued）。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 进度：Task 0–12 accepted（`13/35`）；Task 13 queued/next。
- 正式矩阵：**NO-GO**。Task 12 只闭合 standalone Experiment 1 metric observation projector；Exp2–5 projector、registry、formal runner/renderer/replay 等正式接线尚未完成。

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
| 13–17 | queued | Exp2–5 projector、registry 与后续 consumer 接线；Task 13 next。 |
| 18–26 | queued | evidence class、formal runner、lineage、render/report、pressure/replay、L3 planning、paid receipt validation。 |
| 27–34 | queued | bounded CLI、execution/publication gates、launcher/profile 迁移与 L1–L4 acceptance。 |

## Task 12 accepted 证据

- 代码提交：`a1183c6645d067409b2cfbce54c909aaf4b569c1`（`feat(experiments): add Experiment 1 metric observations`）。
- 严格 5 文件：`paper_exp1_metrics.py`、对应 test，以及用户批准的 Task 2 contract 最小 plan-out 三文件；无 allowlist 外改动、无 output/secret 文件。
- Task 6 profile digest 更新使 Task 2 tracked contract 的正式 loader binding 漂移；真实 loader RED=`1 failed`。最小 plan-out 只把 `pipeline_profile_digest` 更新为 `sha256:e8e8c1f10b638607054c094f6fee409c62aade27ed01ca8645e9afdb79c14fb7`，并把派生 contract digest 更新为 `sha256:48882b8f97c54237654b6ebba9d22c290fa92b2b98b105ef567c57c674352929`，无指标语义变化。
- Task 2 contract suite=`196 passed`；Task 12 suite=`5 passed`；最小化后 combined=`201 passed in 18.17s`；final reviewer=`PASS`。
- `provider/network calls=0`，无 paid receipt；正式矩阵仍为 **NO-GO**。
- 本状态持久化不重复运行测试、Fast、Full、LeanAudit、provider 或 network；Full 按用户指令 intentionally not run。

## Task 12 已建立的边界

- `build_exp1_observations(direct_rows, contract)` 是纯 projector，不 import formal runner/renderer，不修改 registry，也不复制协议状态机。
- 按 domain、difficulty、Lean topic family 与 repeat 冻结 row scope；全部预注册 roots 留在固定分母，wrong final 计 completion 但不计 verified success，infra invalid 显式 null/block publication。
- wall-clock、provider latency、tokens 与 cost 只消费已物化 facts；缺失保持显式 missingness，不伪造为 0；输出仍为 observation draft，`paper_eligible=false`。
- Task 2 contract 变更仅修复当前 pipeline profile binding/digest drift；冻结字段、公式、分母、missingness 与 alias 禁令均未改变。

## 下一步与硬边界

1. 下一项仅实施 Task 13（standalone Experiment 2 projector）；不得越过严格串行顺序。
2. 继续先做 characterization/RED tests、实现、scoped validation、独立 review、测试压缩复审，再持久化 accepted。
3. Experiment 1/5 与 Exp2/3 online checks 的 provider 调用必须等待经 Task 26 校验的 paid receipt；当前 `provider_calls=0`。
4. 正式全量矩阵保持 **NO-GO**；不得把历史 smoke、scripted/capturing 或 trace consumption 冒充当次 `real_transport`。
5. 不 push、不 merge、不创建 PR。
