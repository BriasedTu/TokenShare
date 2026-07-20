# Factorization 500 全量矩阵与预算免审批设计

日期：2026-07-20

状态：用户已批准设计方向，等待书面规格复核后进入实现。

## 1. 目标

把现有 30 道 Factorization 正式 catalog 扩展为 500 道冻结正式题目，并要求这 500 道题全部进入 Experiment 1-5 的每一个适用正式矩阵，不再从 Factorization catalog 抽取 3、5 或 10 道子集。

同时取消论文实验 CLI 的强制人工预算批准：系统仍计算、持久化和审计 budget digest、provider-attempt/token/cost 上界，但默认不再要求调用者传入 `--approve-budget-digest` 才能继续。免审批决定必须作为显式结构化记录写入预算和 suite evidence。

本设计不实现当前缺失的完整 formal suite orchestrator。它先冻结新的 catalog、selection、算术和预算策略，再生成一份独立 agent prompt，要求后续 agent 基于这些新事实补全 formal runner、Exp2/3/4/5 专项 runtime、checkpoint/resume、metrics/report。

## 2. 范围

本轮包含：

- 新增确定性 500 题 Factorization paper catalog 生成器。
- 新增 `benchmarks/paper/factorization_catalog.v2.jsonl`，保留 v1 作为历史回归输入。
- 更新 paper catalog loader 和 preflight，使 v2 必须恰好包含 500 个唯一 roots。
- 更新 Experiment 1-5 的 Factorization frozen selections，使每个实验跨其全部 difficulty conditions 后恰好覆盖 500 个 roots，且不按旧 3/5/10 task slice 裁剪。
- 更新所有冻结 root-run 算术、budget commitments、测试、权威设计、状态文档和 code map。
- 让 CLI 默认使用 v2 catalog。
- 让 CLI 默认免预算审批，同时保留完整预算计算和结构化 bypass record。
- 生成后续 formal infrastructure completion prompt。

本轮不包含：

- 调用真实 provider。
- 执行 87,844 个 root-runs。
- 实现完整 formal suite orchestrator。
- 改写协议 core、Factorization verifier 权威或 Lean checker 权威。
- 删除旧 30 题 v1 catalog 或旧 direct 500 benchmark。

## 3. Catalog 设计

### 3.1 版本和身份

新增：

```text
benchmarks/paper/factorization_catalog.v2.jsonl
```

v2 包含恰好 500 行，每行继续使用 paper Factorization case schema，并增加足以审计生成器版本和全量矩阵身份的 metadata。旧 `factorization_catalog.v1.jsonl` 保留，只能用于历史 regression，不再是 paper CLI 默认输入。

建议新增生成模块：

```text
src/tokenshare/experiments/paper_factorization_catalog.py
```

该模块提供纯确定性 API：

```python
generate_factorization_paper_cases(*, count: int = 500, seed: int) -> tuple[dict, ...]
write_factorization_paper_catalog(*, output_path: Path, count: int = 500, seed: int) -> str
```

第二个函数返回生成后 catalog digest。测试必须证明同一 seed 的逐字节输出稳定，不同 seed 会改变 catalog digest。

### 3.2 数值范围

所有 `target_n` 必须满足：

```text
1_000_000 <= target_n < 100_000_000_000
```

500 个 target 必须唯一，并覆盖百万、千万、亿、十亿、百亿五个数量级。每个 difficulty 都应覆盖多个数量级，不能把数值位数直接等同于 paper difficulty。

### 3.3 难度分布

固定分布：

```text
easy   = 167
medium = 167
hard   = 166
total  = 500
```

难度仍由候选搜索工作量主导：

```text
easy:   candidate_divisor_count in [8, 32]
medium: candidate_divisor_count in [33, 128]
hard:   candidate_divisor_count in [129, 512]
```

非 no-factor case 的已知较小质因数必须位于 `[candidate_start, candidate_end]`，并在 `early/middle/late` 三个 factor-position bucket 中近似均衡。hard 可保留少量确定性 prime/no-factor negative controls；它们仍执行完整 range split，正确结果为未发现非平凡因数。

### 3.4 拆分规则

所有 500 个 roots 都必须通过现有 Factorization plugin 的 deterministic candidate-range partition。生成器只写输入和 split parameters，不生成 AI 答案。

默认 child-count profile：

```text
easy:   requested_child_count = 2
medium: requested_child_count = 4
hard:   requested_child_count = 8
```

实际 children 必须由 `partition_candidate_ranges` 复算。catalog preflight 必须验证 child ranges 连续、无重叠、完整覆盖 candidate range。

### 3.5 Oracle 和可复算性

每行必须保存：

- `case_id`
- `target_n`
- `oracle_prime_factors`
- `candidate_start`
- `candidate_end`
- `candidate_divisor_count`
- `factor_position_quantile`
- `difficulty`
- `paper_difficulty`
- `split_params`
- `source_seed`
- `generator_version`
- `catalog_ordinal`

loader 必须检查：

- 500 个 case IDs 唯一。
- 500 个 target values 唯一。
- oracle factors 均为质数并精确相乘为 target。
- candidate range 合法且不超过 `floor_sqrt(target_n)`。
- difficulty/count 分布精确匹配冻结规格。
- target 数量级覆盖要求成立。
- 同一 catalog 文件的 canonical digest 稳定。

## 4. 五实验全量矩阵

500 道 Factorization roots 在每个实验中均为全量输入。difficulty-based 实验按 difficulty 分到三个 conditions，但三个 difficulty selections 的并集必须恰好是全部 500 个 IDs，且交集为空。

Lean 矩阵本轮保持当前冻结规格不变。

### 4.1 Experiment 1

```text
Factorization: 500 roots * 3 repeats = 1,500 root-runs
Lean:          135 roots * 3 repeats =   405 root-runs
Total:                                     1,905 root-runs
```

Factorization 三个 difficulty selections 分别包含 167、167、166 roots。

### 4.2 Experiment 2

每个 Factorization root 都在 worker levels `1,3,10,30` 下运行 5 次：

```text
Factorization: 500 * 4 * 5 = 10,000
Lean:                            300
Total:                        10,300 root-runs
```

不得继续使用每 difficulty 5-task Factorization batch。Lean 仍保留当前每 difficulty 5-task、2/2/1 topic-family slice。

### 4.3 Experiment 3

Rate-fault：

```text
Factorization: 500 * 5 fault types * 7 rates * 3 repeats = 52,500
Lean:                                                        180
Total:                                                    52,680
```

Worker death：

```text
Factorization: 500 * 2 death counts * 3 positions * 3 repeats = 9,000
Lean:                                                            54
Total:                                                        9,054
```

Experiment 3 合计：

```text
61,734 root-runs
```

每个 Factorization rate-fault 或 worker-death condition 的 frozen selection 必须包含全部 500 个 ordered IDs。fault target manifest 可以按预注册 rate 从这些 roots 展开的 AI units 中选择目标，但不得先把 roots 裁成 5 题。

### 4.4 Experiment 4

```text
Factorization: 500 * 6 modes * 3 repeats = 9,000
Lean:                                           270
Total:                                        9,270 root-runs
```

Factorization 每个 mode/repeat 跨三个 difficulty conditions 的 selections 必须合计覆盖全部 500 roots。

### 4.5 Experiment 5

```text
Factorization: 500 * 3 endpoints * 3 repeats = 4,500
Lean:                                               135
Total:                                            4,635 root-runs
```

每个 cohort member 必须运行相同的 500 个 Factorization roots、相同顺序和相同 selection digest family；不得继续使用每 difficulty 5-task Factorization slice。

### 4.6 总计

```text
P0-core, Exp1-4 = 83,209 root-runs
P0-full, Exp1-5 = 87,844 root-runs
```

唯一 roots 为：

```text
500 Factorization + 135 Lean = 635 unique roots
```

root-run 表示一个 root 在一个固定 condition/repeat 下的完整执行，不表示新增题目。Factorization root-run 数量也不等于 provider call 数量；每个 root 会按 split profile 展开为多个 AI units。

## 5. Selection 一致性

所有 experiment modules 必须继续返回显式 `FrozenConditionSelectionBinding`，不得恢复位置绑定。

新增全量约束：

- Exp1/2/4/5 的每个 Factorization difficulty selection 只能包含本 difficulty 的 cases。
- 同一实验中三个 difficulty selections 的并集必须等于 500 个 catalog IDs，且互不重叠。
- Exp2 的 worker/repeat、Exp4 的 mode/repeat、Exp5 的 endpoint/repeat 不得改变 ordered IDs 或 selection digest family。
- Exp3 每个 Factorization condition 使用相同的全部 500 roots；只有 fault target membership 随 rate/fault/seed 改变。
- plan-only budget 必须从实际 selections 复算新 root-run 数字，禁止硬编码旧 495/600/813/540/270。

## 6. 预算免审批设计

预算计算继续保留。变更的是启动授权，不是预算事实。

### 6.1 API

`plan_paper_suite` 和 `plan_exp1_pilot` 增加显式参数：

```python
budget_approval_required: bool = True
```

library API 默认继续要求批准，避免静默改变其他调用者。paper CLI 根据本项目当前策略显式传入 `False`。

当 `budget_approval_required=False`：

- `plan_only=False` 且 `approve_budget_digest=None` 不抛出 `PaperBudgetApprovalError`。
- 如果调用者仍显式提供了 approval digest，则 digest 不匹配仍然拒绝，避免把错误 digest 当作 bypass。
- budget digest 的计算体不包含 approval 状态，保证同一计划在 plan-only、批准运行和免审批运行之间具有相同 digest。

### 6.2 CLI

paper CLI 默认免审批直接继续。保留可选开关：

```text
--require-budget-approval
```

启用该开关时恢复现有行为：非 plan-only 必须提供匹配的 `--approve-budget-digest`。

### 6.3 结构化记录

`run_budget.json` 和 suite manifest 必须包含：

```json
{
  "budget_approval": {
    "approval_required": false,
    "approval_mode": "user_bypassed",
    "authorization_source": "project_policy",
    "budget_digest": "sha256:...",
    "provided_approval_digest": null
  }
}
```

plan-only 使用 `approval_mode="not_applicable_plan_only"`。显式批准使用 `approval_mode="digest_approved"`。启用 required 模式但缺失或 mismatch 时仍在 provider callback 前停止。

预算上界、observed attempts/tokens/cost 和可选 hard limits 继续持久化。免审批不得删除 budget digest，也不得将尚未执行的计划标记为真实结果。

## 7. 系统职责边界

Experiment layer 负责：

- 遍历 experiment、condition、case、repeat。
- 绑定冻结 selections。
- checkpoint/resume/replay。
- suite/run/task/attempt/event/artifact persistence。
- fault/worker/ablation runtime wrapper。
- metrics/report/paper eligibility。

协议、adapter 和插件负责单个 root 内部：

- deterministic split。
- AI-unit dependency 和 worker dispatch。
- AIAPIExecutor request。
- parser/verifier/checker。
- canonical/merge/root recheck。

本轮 catalog/matrix 变更不得把实验 condition/repeat 循环放进协议 core，也不得把 Factorization 答案预写进 executor callback。

## 8. TDD 和验证

实现必须先新增失败测试，至少覆盖：

1. v2 catalog 恰好 500 行、ID/target 唯一、167/167/166 分布。
2. target 范围与数量级覆盖。
3. oracle product、primality、candidate range 和 split coverage。
4. 同 seed 字节稳定、不同 seed digest 改变。
5. Exp1-5 新 root-run 算术分别为 1,905、10,300、61,734、9,270、4,635。
6. P0-core=83,209，P0-full=87,844。
7. 每个实验的 Factorization selection 没有旧 3/5/10 截断。
8. worker/mode/endpoint/repeat 间 500 题 ordered IDs 不漂移。
9. CLI 非 plan-only 在无 approval digest 时可通过 budget gate，并写 `user_bypassed`。
10. `--require-budget-approval` 缺 digest 或 mismatch 时仍零 provider calls。
11. plan-only 仍为零 provider calls，且不能产生 formal success evidence。
12. 旧 v1 catalog 仍可用于 regression，但 paper CLI 默认加载 v2。

实现完成后运行：

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_factorization_catalog.py
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_catalog.py tests/experiments/test_paper_budget.py
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_exp1_formal.py tests/experiments/test_paper_exp2_scalability.py tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_exp4_ablation_runner.py tests/experiments/test_paper_exp5_model_comparison.py
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_gate_c_dispatcher.py tests/experiments/test_run_paper_experiments_cli.py
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments
conda run --no-capture-output -n tokenshare python -m compileall -q src tests
.\init.ps1
.\init.ps1 -Full
git diff --check
```

不得调用真实 provider。

## 9. 后续设施补全 prompt

本轮完成后新增一份可直接交给另一 agent 的 prompt。该 prompt 必须以本设计和更新后的权威实验设计为事实源，并要求补齐：

- formal suite orchestrator；
- Exp1-5 全量 500 Factorization runtime；
- Exp2 worker scheduling/critical path；
- Exp3 post-AI fault、worker death、replacement attempts；
- Exp4 六模式 runtime；
- Exp5 fixed-entry cohort runtime；
- checkpoint/resume/replay；
- formal metrics/report/paper eligibility；
- capturing transport 离线全链验证；
- 不调用真实 provider；
- 默认预算免审批但保留预算/evidence 记录。

该 prompt 不得声称本轮 catalog/matrix 改动已经实现 formal runner，也不得把 plan-only 或 capturing evidence 标记为论文结果。
