# feat-011 实施计划

> 范围：只实施共享 Exp1 基线、双轴终态和 Exp3–4 smoke；保留当前 dirty worktree 与其他 Agent 的并行修改，不提交、不运行真实 API。

## Task 1：先锁定 Exp3 canonical plan 与预算

**测试文件**：

- `tests/test_paper_exp3_fault_recovery.py`
- `tests/test_paper_budget.py`

**生产文件**：

- `tokenshare/experiments/paper_exp3_fault_recovery.py`
- `tokenshare/experiments/paper_budget.py`

步骤：

1. 写 RED：断言 rate 列表不含 0、Exp3 roots 为 36,126、supporting 为 0、P0-core/full 为 46,478/48,377。
2. 单独运行上述测试，保存预期失败证据。
3. 修改 canonical constants、condition expansion、共享 Exp1 source mapping 和 budget composition。
4. 重跑至 GREEN，并确认旧历史 plan fixture 没有被重写。

## Task 2：实现共享 Exp1 证据引用

**测试文件**：

- `tests/test_paper_formal_evidence.py`
- `tests/test_paper_formal_runner.py`
- `tests/test_paper_exp3_fault_recovery.py`

**生产文件**：

- `tokenshare/experiments/paper_formal_evidence.py`
- `tokenshare/experiments/paper_formal_runner.py`
- `tokenshare/experiments/paper_exp3_fault_recovery.py`

步骤：

1. 写 RED：同一 `case_id` 跨 fault/repeat 得到同一 source reference；引用冻结 generation/hash/identity/usage；引用自身 usage 为零。
2. 写 RED：完整 Exp1 实验失败仍允许 Exp3 dispatch，但 comparison 不可用；missing/hash/schema/identity mismatch 在 dispatch 前阻断且不调用 provider。
3. 运行定向测试，保存失败证据。
4. 增加经过 hash/schema 校验的 current-generation reader 和 immutable shared-reference artifact。
5. 删除 dedicated worker-death baseline dispatch 路径，接入正式 Exp1→Exp3 allowlist。
6. 重跑至 GREEN。

## Task 3：实现双轴终态和 suite 闭合

**测试文件**：

- `tests/test_paper_terminal_outcomes.py`
- `tests/test_paper_formal_runner.py`

**生产文件**：

- `tokenshare/experiments/paper_terminal_outcomes.py`
- `tokenshare/experiments/paper_formal_runner.py`
- `tokenshare/experiments/paper_formal_evidence.py`

步骤：

1. 写 RED：稳定枚举、实验失败继续、基础设施失败停止后续 dispatch、未开始 roots 计数与 manifest 闭合。
2. 运行定向测试，保存失败证据。
3. 实现 outcome/evidence 两轴、typed infrastructure block 和 suite-boundary closure。
4. 限制 expected-runtime allowlist；未知异常只在 suite 边界分类并停止，不继续。
5. 重跑至 GREEN。

## Task 4：新增 Exp3–4 smoke profile 与 CLI 契约

**测试文件**：

- `tests/test_paper_smoke.py`
- `tests/test_run_paper_experiments.py`

**生产文件**：

- `benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json`
- `tokenshare/experiments/paper_smoke.py`
- `tokenshare/experiments/run_paper_experiments.py`

步骤：

1. 写 RED：profile 恰好解析 11 个 direct roots、supporting 为 0、baseline omission 三字段稳定，正式 scope 在 dispatch 前拒绝。
2. 写 RED：identity-only 从 canonical plan 计算 roots/AI/provider upper bound，跨实验证据只在 formal allowlist 开启。
3. 运行定向测试，保存失败证据。
4. 增加强类型 `baseline_policy`，创建 profile，更新 identity/report 输出。
5. 重跑至 GREEN，再实际执行一次 identity-only；将观察到的 identity 固化到 launcher 预检。

## Task 5：新增受监督 launcher

**测试文件**：

- `tests/test_exp3_exp4_v3_launcher_supervision.py`

**生产文件**：

- `local/run_exp3_exp4_v3_smoke.ps1`

步骤：

1. 写 RED：路径不可预存、先 identity-only、同 profile/config 真实 transport、复用监督 helper、禁止 resume/restart、预检 omission policy。
2. 运行定向测试，保存失败证据。
3. 基于现有 v3 launcher 模板实现脚本，但不执行。
4. 重跑静态测试至 GREEN。

## Task 6：metrics、report、文档与状态同步

**测试文件**：

- `tests/test_paper_formal_metrics.py`
- 相关 report/replay 定向测试

**生产/文档文件**：

- `tokenshare/experiments/paper_formal_metrics.py`
- `tokenshare/experiments/paper_smoke_report.py`
- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- `Doc/agent-navigation.md` 指向的 code map
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

步骤：

1. 写 RED：共享引用不增加 calls/tokens/cost，source usage 可审计，双轴状态在 report 中分栏。
2. 实现并重跑至 GREEN。
3. 更新权威实验口径、EPD supersession、code map 与 harness 状态，二次搜索修正仍把 Exp3 0%/dedicated baseline 当作当前设计的旧表述。

## Task 7：最终验证

按顺序执行：

1. 本 feature 的定向 pytest；
2. `powershell -ExecutionPolicy Bypass -File .\init.ps1`；
3. 新 profile 的 identity-only（不带真实 key、不 dispatch）；
4. secret scan；
5. run01–run04 的 mtime/hash 只读核对；
6. 审查 `git diff`，确认没有覆盖其他 Agent/用户修改；
7. 使用 verification-before-completion 与 requesting-code-review 复核证据。

禁止执行：真实 smoke launcher、真实 API、`init.ps1 -Full`、全量 pytest、LeanAudit、force-all LeanAudit。
