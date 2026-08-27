# Slim V2 Reducer Missing-Reason Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Full run 中两处合法 `null` 没有权威科学原因而阻断 metrics 发布的问题，并只复用现有 committed 运行事实生成正式汇总。

**Architecture:** 不改变任何指标公式、schema、provider、runner 或原始 run 数据。`reducer.py` 继续把缺失 usage 聚合为 `null`，但为 Exp1 token/cost totals 写入 `usage_missing`；Exp5 cell 同时含 parsed-unsubmitted 与 infrastructure-invalid 时，parsed-unsubmitted 的 nonpass 指标保持 N/A，而受设施无效影响的验证拒绝率与调用覆盖率写入 `infrastructure_invalid_root_present`。正式发布仍使用现有 staged/summary-last 原子流程。

**Tech Stack:** Python 3.12、pytest、SQLite/JSONL 普通 run store、PowerShell、Slim V2 reducer。

---

### Task 1: 固化两个生产阻断的 focused regression

**Files:**
- Modify: `tests/experiments/slim_v2/test_reducer_golden.py`

- [x] **Step 1: 写 Exp1 usage 缺失回归**

构造一个 provider latency 完整、但 `total_tokens` 与 `cost_estimate_cny` 缺失的 committed Exp1 root。调用真实 `reduce_run()`，要求 latency 保持精确，token/cost 为 `null + usage_missing`。

- [x] **Step 2: 写 Exp5 组合状态回归**

构造同一 model cell 内的 parsed-unsubmitted attempt 与 infrastructure-invalid root。调用真实 `reduce_run()`，要求 nonpass 指标继续为 `not_applicable_or_unavailable`，验证拒绝率和调用覆盖率为 `null + infrastructure_invalid_root_present`，且 actual call/token/cost 事实仍保留。

- [x] **Step 3: 运行两个新测试并观察预期 RED**

```powershell
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -k "exp1_missing_protocol_usage or exp5_parsed_unsubmitted_with_infrastructure_invalid" -q
```

预期：两个测试均在正式 null 校验处失败；Exp1 报 `actual_total_tokens` 缺科学原因，Exp5 报 `first_attempt_verification_rejection_rate` 缺科学原因。

### Task 2: 实施最小 reducer 修复

**Files:**
- Modify: `src/tokenshare/experiments/slim_v2/reducer.py`
- Test: `tests/experiments/slim_v2/test_reducer_golden.py`

- [x] **Step 1: 修复 Exp1 totals 原因写入**

对 `actual_total_tokens` 与 `actual_cost_estimate_cny` 使用与 Exp5 相同的 nullable total 写入模式：有限总值写 value；任一 required provider usage 缺失时调用 `_set_missing(..., "usage_missing")`。provider latency 聚合逻辑与已存在的精确值保持不变。

- [x] **Step 2: 修复 Exp5 组合分支**

只要 cell 含 infrastructure-invalid root，验证拒绝率与调用覆盖率都写 `infrastructure_invalid_root_present`；若同时含 parsed-unsubmitted，`first_attempt_nonpass_rate` 保持既有 N/A，不被设施原因覆盖。不得把 parsed-unsubmitted 计作 transport、parse 或 verification rejection。

- [x] **Step 3: 运行两个新测试并观察 GREEN**

运行 Task 1 的同一命令，预期 `2 passed`。

### Task 3: 风险驱动验证与现有 Full run 发布

**Files:**
- Modify: `Doc/SlimV2/2026-08-27-reducer-missing-reason-repair-plan.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `progress.md`
- Generate: `TokenShareData/outputs/slim_v2/slim-v2-full-flash-20260823-233000-b4c8e951/metrics/`

- [x] **Step 1: 运行 reducer focused suite**

```powershell
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/slim_v2/test_reducer_golden.py -q
conda run --no-capture-output -n tokenshare python -m compileall -q src/tokenshare/experiments/slim_v2 tests/experiments/slim_v2
```

- [x] **Step 2: 对现有 Full run 先做无写入全表 dry-run**

替换 `_stage_payloads` 为内存 sink，运行生产 `_reduce_run_staged()`；要求五表、153 formal occurrences 与 summary 全部完成，inventory counts 为 Exp1–5=`435/600/3726/2145/111`、references=`106`。

- [x] **Step 3: 冻结输入 fingerprint 并正式运行 reducer**

该 run 实际包含 1,088,134 个非 metrics 文件、6.65 GiB；逐文件内容 SHA 与本次 reducer 风险不成比例。实际采用两层证明：全部非 metrics 文件的 path/size/mtime 顺序无关 fingerprint，以及 inventory、roots、references、calls、responses、traces 与根文件共 21,260 个关键输入的内容 SHA-256 manifest。随后执行现有 CLI `reduce --run-dir`；不得执行 `run`、`run-all`、`representative`、`full` 或 `--resume`。

- [x] **Step 4: 核验发布与零实验重跑**

要求 `metrics/summary.json` 加 Exp1–5 JSONL/CSV 共 11 个正式文件；发布前后输入 manifest 完全相等，roots=`7017`、references=`106`、provider call journal 文件数不变、无 stage temp。检查 Exp1 hard 的 token/cost 为合法 `null + usage_missing`，Exp5 组合 cell 为权威原因。

- [x] **Step 5: 同步文档与最终检查**

记录实际 RED/GREEN、focused suite、dry-run、正式发布和 manifest 证据；更新 code map 与 `progress.md` 顶部 Slim V2 当前状态。最后运行：

```powershell
git diff --check
git status --short
```

不自动提交、push、merge 或创建 PR；保留工作区中用户已有的无关修改。

## 实施与验证证据（2026-08-27）

- RED：两个新测试均按预期失败，分别精确报 `exp1:cell:actual_total_tokens` 与 `exp5:model:first_attempt_verification_rejection_rate` 缺科学原因；不是 fixture、导入或环境误报。
- GREEN：同一 focused 命令为 `2 passed, 38 deselected`；完整 `test_reducer_golden.py -q` 为 `40 passed in 14.89s`，Slim 源码/测试 `compileall` exit 0。
- 未写入 dry-run：生产 reducer（只替换 staging sink）完成 153 formal occurrences，五表行数为 `12/88/684/648/3`，paper roots 为 `435/600/3726/2145/111`，references=`106`，待发布 payload=`11`，当时 `metrics` 仍不存在。
- 正式发布：只执行一次 `python -m tokenshare.experiments.slim_v2.cli reduce --run-dir ...`，exit 0；发布 `summary.json` 与 Exp1–5 各 JSONL/CSV 共 11 文件，stage temp=`0`。
- 修复 cell：Exp1 Factorization hard 的 provider latency 保留为 `172130472 ms`，token/cost totals 为 `null + usage_missing`；Exp5 `zai-org/GLM-5.2` cell 的 nonpass 保持 `null + not_applicable_or_unavailable`，验证拒绝率与调用覆盖率为 `null + infrastructure_invalid_root_present`。
- 输入不变：发布前后非 metrics 文件数/字节数均为 `1,088,134 / 7,143,234,452`，metadata fingerprints 均为 `9aa66fec8352982279de3e87fc1eae81` 与 `d59f593eeb7604374f05efecad4c142d`；21,260 个关键文件内容 manifest 均为 `96dc9ea143d7532c0e54482907bb12017247df4286a6656fbdc45f84705fea47`。roots=`7017`、references=`106`、calls=`5889`、responses=`2565`、traces=`1964` 均未变化。
- 本轮没有执行 protocol resume、provider、runtime、Lean、Representative 或 Full；没有改 schema、指标公式、shared code 或任何原始实验输入。
