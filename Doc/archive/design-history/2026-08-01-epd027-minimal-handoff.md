# EPD-027 最小交接摘要（2026-08-01）

> 本摘要是 2026-08-02 Task 4 综合 review follow-up 与用户继续修正授权后的 fresh 状态快照。Task 4 状态为 `needs_rework`；旧停止点只作历史 provenance，不再代表当前状态。

## 1. Active feature / focus

- Active feature：`FEAT-011`。
- Active focus：`EPD-027` 全面实验设施改造；目标链路为“真实 API / 真实模型 trace → canonical TokenShare runtime → 持久化证据 → direct results → 论文指标”。
- 工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- 权威实施计划：`Doc/archive/design-history/2026-08-01-feat-011-response-bank-paper-pipeline-implementation-plan.md`。
- 当前停止点：Task 0–3 已接受（`4/35`）；accepted count 不变。Task 4 是唯一 active focus，基本实现已存在，但未 accepted、未提交，当前 `needs_rework`。Task 5 保持 queued，不得进入。

## 2. Task 0–34 状态

| Task | 状态 | 摘要 |
|---:|---|---|
| 0 | accepted | 离线 network tripwire；已提交。 |
| 1 | accepted | 冻结 EPD-027 profile、离线批准与 paid authority 分离；已提交。 |
| 2 | accepted | 机器可执行 paper metric contract；已通过多轮双审并提交。 |
| 3 | accepted | canonical direct-results projector；两项通用前置与 Task 3 均已复审 PASS，Task 3 已提交为 `f9773944`。 |
| 4 | needs_rework | exact outbound bytes / request identity / admission / Lean prompt v2 基本实现存在，未 accepted/未提交；唯一 blocker 见下文。 |
| 5 | queued | immutable response-bank objects/index/opaque locator。 |
| 6 | queued | semantic slot inventory / zero-engine preflight。 |
| 7 | queued | SQLite WAL atomic budget authority。 |
| 8 | queued | acquire/publish/resume/reconcile bank entries。 |
| 9 | queued | deterministic logical source-latency scheduler。 |
| 10 | queued | attempt ordinal / parent-side worker commit ABI。 |
| 11 | queued | trace-backed executor / dual provenance。 |
| 12 | queued | standalone Exp1 projector。 |
| 13 | queued | standalone Exp2 projector。 |
| 14 | queued | standalone Exp3 projector。 |
| 15 | queued | standalone Exp4 projector。 |
| 16 | queued | standalone Exp5 projector。 |
| 17 | queued | projector registry / formal metrics integration。 |
| 18 | queued | evidence classes / acquisition eligibility。 |
| 19 | queued | formal runner / preflight / scheduler / normal lifecycle。 |
| 20 | queued | numeric-cell lineage observations。 |
| 21 | queued | contract-only render/report / claim audit。 |
| 22 | queued | external bank pressure path。 |
| 23 | queued | historical real success fixture / L2 path。 |
| 24 | queued | deterministic replay / cell-lineage recompute。 |
| 25 | queued | L3 online/capability run planning。 |
| 26 | queued | validate user-supplied paid receipts。 |
| 27 | queued | bounded pipeline CLI。 |
| 28 | queued | execution gate / publication gate split。 |
| 29 | queued | supersede legacy launchers / scoped new launchers。 |
| 30 | queued | four verification profiles。 |
| 31 | queued | execute/review L1 component acceptance。 |
| 32 | queued | execute/review L2 historical-real full path。 |
| 33 | queued | conditional L3 new-real smoke under paid receipt。 |
| 34 | queued | conditional L4 cell audit / docs / handoff。 |

## 3. 已完成任务：文件、命令、退出码、证据

### Bootstrap / EOL authority（Task 前置）

- Commit：`33024c67`、`3166a860`。
- 文件：`.gitattributes` 精确 EOL 规则；未采用宽泛规则。

### Task 0 — accepted

- Commit：`ed0b056a`。
- 文件：
  - `verification/pytest_network_tripwire.py`
  - `verification/sitecustomize.py`
  - `tests/test_paper_network_tripwire.py`
- 已知验证：Task0 focused 8 passed；transport 相关定向套件 42 passed；Fast 458 passed / 1 skipped。
- 命令族：`python -m pytest -p verification.pytest_network_tripwire ... -q`；`\.\init.ps1`。
- 退出码：0。
- 证据路径：上述测试文件、commit `ed0b056a`。

### Task 1 — accepted

- Commit：`78fbddaa`。
- 文件：
  - `benchmarks/paper/epd027_pipeline_profile.v1.json`
  - `src/tokenshare/experiments/paper_pipeline_profile.py`
  - `tests/experiments/test_paper_pipeline_profile.py`
  - `src/tokenshare/experiments/paper_budget.py`
  - `tests/experiments/test_paper_budget.py`
- 已知验证：focused 177 passed；canonical-root Task1 suite 203 passed；provider calls 0。
- 命令族：`python -m pytest -p verification.pytest_network_tripwire` 加上述 Task1 测试。
- 退出码：0。
- 证据路径：上述测试文件、commit `78fbddaa`。
- 冻结 digest：profile `sha256:1e704021...0aae`；budget `sha256:1898ca13...8c69`。

### Task 2 — accepted

- Commit：`d2b16f89714b1df70295cb0a1b32bced0146ce53`。
- 文件：
  - `benchmarks/paper/paper_metric_contract.v1.json`
  - `src/tokenshare/experiments/paper_metric_contract.py`
  - `tests/experiments/test_paper_metric_contract.py`
- 验证命令：`python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_metric_contract.py -q`。
- 退出码：0；最终已知 195 passed；provider calls 0。
- 其他验证：target compileall、`git diff --check`，退出码 0。
- 证据路径：上述测试文件、commit `d2b16f89`。

### Task 3 — accepted

- Owner：`task3_review_fixes`；commit author=`Braised Tu`。
- Reviewer：`task3_independent_review`；最终 verdict=`PASS`，无未解决 Critical/Important。
- Prerequisite commits：
  - `860b7c48`（通用 ledger-binding，7 files changed）：最终 focused `40 passed`，独立 review PASS，provider calls `0`。
  - `4ea293b1`（typed runtime-hook observation，18 files changed）：最终 `69 passed` + `286 passed`，独立 review PASS，provider calls `0`。
- Task commit：`f9773944`（`refactor(experiments): add evidence-gated direct results`）。
- Changed files（Task 3 commit，严格四文件）：
  - `src/tokenshare/experiments/paper_direct_results.py`（新）
  - `src/tokenshare/experiments/paper_models.py`（修改）
  - `tests/experiments/test_paper_direct_results.py`（新）
  - `tests/experiments/test_paper_models.py`（修改）
- Validation：canonical focused 命令族为 `conda run -n tokenshare python -m pytest -p verification.pytest_network_tripwire tests/experiments/test_paper_direct_results.py tests/experiments/test_paper_models.py -q`；最终退出码 `0`，`101 passed`，provider calls `0`。
- Evidence：`ProtocolRunLedgerBinding` 绑定 verified ledger bytes/events/tip；`RuntimeHookObservationV1.from_dict()` 关闭三类 hook schema；direct projector 从 canonical runtime facts 与预注册 inventory 生成固定分母结果。独立审查确认正式路径没有影子 TokenShare/影子状态机，禁用 `ProtocolEngine` 时不能铸造 paper-eligible success；手工 typed fixture 仅用于组件测试。
- Post-task 测试压缩：commit `66643084`（`test(experiments): compact Task 3 regressions`），仅修改 5 个已复审测试文件，production 零修改；test defs / 估算 cases 从 `77/153` 降至 `66/118`。canonical scoped 结果为 exit `0`、`118 passed in 5.55s`，provider calls=`0`；独立 reviewer=`PASS`，Critical/Important/Minor=`0/0/0`。
- 本次压缩未重复运行 Fast、Full 或 LeanAudit；上述 canonical 结果为压缩执行阶段已接受证据。
### Task 4 — needs_rework

- 当前实现：exact outbound bytes / request identity / admission / Lean prompt v2 基本实现已存在；Task 4 production/tests 仍为未暂存 dirty，没有 accepted commit。
- 实现证据：focused `125 passed`；canonical exit `1`，`323 passed / 39 failed in 326.47s`；provider calls=`0`，network tripwire loaded。
- 最小化证据：多轮失败子集最终仅剩 `tests/experiments/test_run_paper_experiments_cli.py::test_paper_cli_formal_capturing_e2e_writes_all_tables_without_real_usage`，exit `1`，`0 passed / 1 failed in 30.42s`。日志：`C:\Users\32133\AppData\Local\Temp\tokenshare_task4_unique_correction_nodeid.log`。
- 修正结果：reachable-artifact closure 缺 index row 已解决；新且唯一 blocker 是 `condition_results.jsonl.failed_root_count` 与 canonical recomputed condition summary 不一致。
- Review：综合 follow-up 曾 verdict=`BLOCKED`；用户随后明确授权继续同一 Task 4 修正，直到计划内 E2E GREEN 后再 review。无 production 计划外修改。
- 隔离 dirty：approved implementation plan 中有未暂存 36-line user override diff；它不属于 Task 4，不得暂存、修改或回退。
- 本轮未运行 Fast、Full、LeanAudit，未联网，未调用 provider。

### 最新仓库级 Fast 证据

- 2026-08-01 `.\init.ps1`：exit `0`，`468 passed, 1 skipped in 20.19s`；JSON/SQLite、harness、compileall 均通过。
- 状态同步时误把 `verification/run_verification.py` 当成无 pytest harness，实际再次进入 Fast：exit `0`，`468 passed, 1 skipped in 18.78s`。这是一次重复验证，不作为新增覆盖证据；后续不得再用该命令规避 Fast 门禁。
- 本次 Fast provider calls=`0`。Full、LeanAudit、真实 API 均未运行；Full 是按用户指令 intentionally not run。

## 4. 当前协作状态

- Task 4 是唯一 active focus，综合 review follow-up 曾给出 `BLOCKED`；用户已授权继续修正，当前状态为 `needs_rework`。未通过前不得进入 Task 5。
- Task 4 之前的 `READY_AFTER_TASK3` 只是实施前只读预检结论，已被当前 needs_rework 状态覆盖。

## 5. 当前风险 / 注意事项

1. Task 4 当前唯一 blocker 是 `failed_root_count`/canonical condition summary 不一致；已修正的 reachable-artifact index row 不再是当前 blocker。
2. Task 4 production/tests 尚未 accepted/未提交；状态提交只能包含四个 harness 文件，不能夹带 Task 4 实现、测试或 approved plan 的 36-line user override。
3. Task 0–3 只覆盖 `4/35`；Task 5 及以后保持 queued，`feat-011` 保持 `in-progress`，正式全量维持 NO-GO。
4. 未获用户提供且经 Task 26 校验的 paid receipt，不得调用真实 API；离线 implementation approval 不构成付费授权。

## 6. 下一批可直接派发任务

1. 保持 Task 4=`needs_rework` 和 accepted=`4/35`；不得把基本实现存在写成 accepted。
2. 继续解决 `condition_results.jsonl.failed_root_count` 与 canonical recomputed condition summary 的唯一不一致，直到计划内 E2E GREEN 后重新 review。
3. Task 4 计划内 E2E 与 review 通过前不得 accepted/提交 production/tests，不得进入 Task 5。
4. approved implementation plan 的隔离 36-line user override 保持未暂存原状，不得修改或回退。

## 7. 禁止命令与付费 API 门禁

- 禁止：`.\init.ps1 -Full`、`./init.sh --full`。
- 禁止：`.\init.ps1 -Full -LeanAudit`、`--lean-audit`、`--force-all-lean-audit`。
- 禁止：全量 `pytest tests`。
- 禁止：600-entry/大批 Lean 调度、正式 LeanAudit、force-all。
- 禁止：人工注入攻击、安全 fuzzing、恶意篡改防护扩展；仅做受信本地研究工作流的正确性与防旁路。
- 禁止：旧 smoke/launcher 直接启动正式实验；后续须按计划先 supersede 并建立 bounded launcher。
- 禁止：任何真实 provider dispatch，除非存在与所选 scope 精确匹配、通过 Task26 校验的用户提供 paid receipt。
- 离线 implementation approval 不能授权付费执行。
- 缺 receipt、缺 bank entry、超预算或证据不完整必须 fail closed，不得临时 scripted 生成。
- 预算硬停止线：人民币 1000 元；达到即停止新的 provider dispatch，已有证据正常收口。
- Experiment 1/5 保持真实 API；Experiment 2/3/4 按批准的两阶段 real-trace paired 设计；Exp2/Exp3 保留小型在线检查。
- 当前 state/docs 已同步到 Task 4 needs_rework；Task 5 保持 queued，不得自动调用 API。
