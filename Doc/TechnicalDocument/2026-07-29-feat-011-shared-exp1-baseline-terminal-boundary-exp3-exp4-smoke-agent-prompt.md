# FEAT-011：共享 Exp1 baseline、实验终态边界与 Exp3–4-only smoke 实施 Prompt

你在仓库 `E:\TokenEcnomic\TokenShare` 工作。

本任务是一次实验设计、实验基础设施和 smoke 入口的联合修复。必须完成代码、测试、文档和离线 identity 验证，但**不得启动任何真实 API 实验**。用户会在验收后自行启动新的 Exp3–4-only smoke。

## 一、最终目标

同时完成以下三个目标：

1. 正式 Experiment 3 不再重复执行无故障 baseline，而是按相同 `case_id` 复用正式 Experiment 1 已持久化的真实无故障 evidence。
2. 明确区分“真实实验结果失败”与“实验设施/身份完整性失败”，保证前者形成结构化终态并继续剩余预注册条件，后者 fail closed 但仍尽最大可能闭合 manifest/report。
3. 新增独立的 Exp3–4-only 真实 API smoke profile 与 launcher。这个 smoke 不执行 Exp1、Exp2，也不要求或导入 baseline；它只验证 Exp3 五类 rate-fault、worker-death、Exp4 五种 mode 能否真实执行、持久化必需 evidence 并形成明确终态。

不要把历史实现约束当成不可改变的实验原则。当前实验方案尚未最终冻结，本任务明确授权修改正式 Experiment 3 的 baseline 设计、condition/root 分母、budget/selection/profile identity 和相关文档。

## 二、工作纪律与禁止项

必须遵守：

- 先完整阅读 `AGENTS.md`。
- 完整阅读：
  - `Doc/agent-navigation.md`
  - `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
  - `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
  - `feature_list.json`
  - `progress.md`
  - `session-handoff.md`
  - `local/run_exp1_exp4_v3_smoke.ps1`
  - `local/invoke_native_process_with_logs.ps1`
- 只读检查历史现场：
  - `local/supervision/paper_smoke_exp1_exp4_v3_20260728T103927Z_run03`
  - `outputs/experiments/paper_smoke_exp1_exp4_v3_20260728T103927Z_run03`
  - `local/supervision/paper_smoke_exp1_exp4_v3_20260729T060859Z_run04`
  - `outputs/experiments/paper_smoke_exp1_exp4_v3_20260729T060859Z_run04`
- 当前 worktree 很脏，现有修改属于用户。先运行 `git status --short` 和定向 `git diff`，不得覆盖、回退或顺手格式化无关修改。
- 使用 TDD：每个行为变更先写 RED test，实际运行并确认因目标能力缺失而失败，然后才允许改生产代码。
- 文件编辑使用 `apply_patch`。
- PowerShell 读取中文文件必须显式 `-Encoding UTF8`。
- 更新代码后同步 code map。
- 所有新 smoke/regression evidence 固定 `paper_eligible=false`。

禁止：

- 不得运行 `.\init.ps1 -Full`、全量 pytest、全量 Lean、LeanAudit 或 force-all。
- 不得调用真实 API，不得启动 smoke，不得 resume/restart 任何历史 run。
- 不得修改 run01–run04 的任何 evidence、日志、checkpoint、manifest 或监督文件。
- 不得修改代理、`NO_PROXY`、网络、transport、model、prompt、catalog、题目、worker count、provider inflight limit、timeout、max tokens、provider retry 或 protocol retry 规则。
- 不得把 API key 或其片段写入命令、日志、process record、artifact、测试夹具、文档、Git 文件或最终回复。
- 不得用 blanket `except Exception: continue` 隐藏配置、identity、schema 或 invariant 错误。
- 不得把 unavailable/estimated 值写成 provider actual。
- 不得通过事后复制字段把一个执行伪装成另一个新的 provider/root execution。

## 三、已经确认的设计决策

### 3.1 正式 Exp3 共享 Exp1 baseline

采用以下唯一方案：

- 正式 Exp1 是 Exp3 的共享无故障 reference baseline。
- Exp3 不再为任何 rate-fault 或 worker-death 重新调用无故障 baseline API。
- Factorization 按同一 `case_id` 复用 Exp1 的全部 500 题结果。
- Lean Exp3 使用的固定 3-task slice 按同一 `case_id` 引用 Exp1 135 题中的对应结果。
- 同一个 Exp1 task evidence 可以被该 `case_id` 的所有 Exp3 fault type、fault rate、repeat、death count 和 kill position 共同引用。
- Exp3 仍保留两个 fault repeats，但两遍都引用 Exp1 的同一无故障 reference；不得宣称它们是 matched/paired repeats。
- 新报告语义必须使用 `shared_exp1_reference` / `shared_reference`，不能继续声称 `same-repeat matched baseline`。
- 对历史字段需要兼容时，可以保留 `matched_baseline_*` 作为兼容别名，但必须同时写清 `comparison_kind="shared_reference"`、source experiment/condition/task/repeat/seed 和 content hash。

正式 Exp3 的 0% 点不再是真实调度 condition：

- Factorization 实际 fault rates 改为 `1%, 5%, 10%, 25%, 50%, 100%`。
- Lean 实际 fault rates 改为 `10%, 50%, 100%`。
- 图表如需要 0% 点，从 Exp1 evidence 投影 reference row。
- reference row 必须注明 `source_experiment_id=exp1_real_ai_feasibility`，不计作新的 Exp3 root-run/provider call。

正式执行规模按唯一真实执行计数重新冻结：

- 删除 Factorization Exp3 零故障重复执行：`500 × 5 fault types × 2 repeats = 5,000 roots`。
- 删除 Lean Exp3 零故障重复执行：`3 × 5 fault types × 2 repeats = 30 roots`。
- 删除 worker-death dedicated supporting baselines：Factorization 1,000 + Lean 6 = 1,006 roots。
- 共删除 6,036 个重复真实 API root-runs。
- 新 Exp3 rate-fault roots：`30,090`。
- Exp3 worker-death roots保持 `6,036`。
- 新 Exp3 合计：`36,126`。
- 新 P0-core 唯一实际 root-runs：`46,478`。
- 新 P0-full 唯一实际 root-runs：`48,377`。
- 重新生成 condition、selection、execution-plan、budget identity 和 digest；不得沿用旧硬编码 digest。
- 保留 `execution_plan_digest` 和 `budget_digest` 绑定实际 `output_root` 的既有算法；不得恢复成跨 run 固定 digest。

共享 evidence 必须是可审计引用，不是伪造执行：

- baseline reference 至少冻结 source suite/run/generation、experiment、condition、case/task、repeat、seed、root status、provider/model/entry、request limits、catalog/split/plugin/parser/verifier/executor identity、task/attempt/event/artifact refs 和 source content hash。
- 共享引用本身 provider calls/tokens/cost 均为 0；指标使用 source Exp1 的真实 usage。
- 一个 Exp1 root 只能在全局唯一真实执行分母中计数一次。
- resume/replay 必须验证 reference hash 与 frozen identity，禁止重新调用 provider。
- Exp1 evidence 缺失、hash 不符、schema 损坏或语义身份不匹配属于设施完整性失败。
- Exp1 root 有完整终态 evidence 但结果为 checker/parser/provider failure，仍是可信的实验结果 evidence：Exp3 照常执行，但依赖成功 baseline 的 wall-clock/token/cost delta 写 `null`，并给出明确 unavailable reason。
- 增加独立的 `baseline_comparison_eligible` 或同等字段，避免把“fault recovery evidence 可用”和“baseline overhead comparison 可用”压成一个布尔值。

### 3.2 Exp3–4-only smoke 不要求 baseline

这是 smoke/regression 专用例外，不得扩散到正式实验：

- 新增版本化 profile，例如：
  - `benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json`
  - `suite_id=paper_smoke_exp3_exp4_v1`
- profile 只包含：
  - Exp3 `false_positive r100`
  - Exp3 `false_negative r100`
  - Exp3 `no_return r100`
  - Exp3 `late_submission r100`
  - Exp3 `executor_error r100`
  - Exp3 worker-death `dead1/p50/worker10`
  - Exp4 `FULL`
  - Exp4 `NO_VERIFICATION`
  - Exp4 `NO_PARSER_POLICY`
  - Exp4 `NO_REQUEUE`
  - Exp4 `NO_MERGE_GATE`
- 直接实际调度 roots 固定为 11，supporting baseline roots 固定为 0。
- 只使用 `factor_v2_easy_001`、`repeat_id=0`、`worker_count=10`。
- 不包含 Exp1、Exp2、Exp3 0% baseline、Exp5 或 pilot。
- smoke 不读取、不导入、不复制 run01–run04 的 Exp1 baseline。
- smoke 的 baseline/reference 字段写：
  - reference 为 `null`
  - `baseline_comparison_eligible=false`
  - `baseline_unavailable_reason="smoke_baseline_not_requested"`
- baseline 缺失不得阻止这 11 个 roots 调度。
- smoke 仍必须真实调用官方 DeepSeek 并保留真实 fault/recovery/ablation evidence。
- smoke 始终 `formal=false`、`pilot_only=true`、`regression_only=true`、`paper_eligible=false`。
- smoke 结果不得用于论文 baseline、overhead 或 paired comparison。
- profile/schema/runner 必须显式冻结 `baseline_policy="omitted_for_smoke_regression"` 或等价强类型字段；禁止根据“目录里刚好没有 Exp1”隐式降级。
- 正式/论文 scope 若尝试使用该 baseline policy，必须在 provider dispatch 前 fail closed。

新 smoke 的 provider/request 控制保持不变：

- 官方 DeepSeek
- model：`deepseek-v4-pro`
- entry：`deepseek_v4_pro_exp1_baseline`
- provider config：`benchmarks/paper/exp1_baseline_provider_config.v3.json`
- thinking：enabled
- `reasoning_effort=high`
- `timeout_seconds=600`
- `max_tokens=300000`
- `stream=false`
- max provider attempts per AI unit：1
- provider retry limit：0
- provider inflight limit：50
- unlimited budget 只表示无总费用硬上限，不改变 condition、selection、repeat、retry 或单次请求限制。

不要手算并硬编码新的 planned AI units/provider-attempt upper bound。让 canonical plan/budget 代码实际生成，再在测试和 launcher 里冻结经验证的值。任何 root/AI-unit/replacement reserve 分母必须能回溯到 selection 与 split commitments。

### 3.3 实验结果与设施故障采用双轴终态

根因：当前 runner 把 `root_status != completed` 当作 evidence 不完整，并在 supporting baseline 路径抛出 suite 级 `ValueError`。这把实验结果质量与实验设施健康混在一起。

实现显式双轴：

1. `outcome_status`
   - `succeeded`
   - `failed_experimental`
   - `blocked_dependency`
2. `evidence_integrity`
   - `complete`
   - `missing`
   - `invalid`
   - `corrupt`

允许使用等价命名，但必须是结构化类型/字段，不能靠异常消息字符串分类。

调度规则：

| 情况 | 分类 | 后续行为 |
|---|---|---|
| 成功输出 | succeeded + complete | 持久化并继续 |
| parser/checker rejection | failed_experimental + complete | 持久化并继续 |
| provider timeout/error/429/invalid output | failed_experimental + complete | 按原 retry 规则形成终态并继续 |
| injected no-return/late/executor-error 未恢复 | failed_experimental + complete | 持久化真实失败并继续 |
| worker death 已发生但 replacement 不完整 | failed_experimental + complete | 写 incomplete worker-death evidence，继续 |
| Exp1 baseline root 失败但 evidence 完整 | dependency result unavailable + complete | 正式 Exp3 继续，comparison fields unavailable |
| smoke baseline omitted by explicit policy | dependency intentionally omitted + complete | Exp3 smoke 继续 |
| provider config/model/request identity 漂移 | infrastructure blocked | 停止新 API 调用 |
| catalog/selection/budget/output identity 漂移 | infrastructure blocked | 停止新 API 调用 |
| baseline/evidence hash 或 schema 损坏 | infrastructure blocked | 停止新 API 调用 |
| 未分类内部异常 | infrastructure blocked | fail closed，不得伪装成模型失败 |

终态规则：

- `completed_with_failures`：所有预注册 roots 都得到可信终态，但其中存在真实实验失败。
- `blocked`：设施/identity 错误阻止继续调度；已开始 roots 保留，未开始 roots 明确记录。
- `incomplete`：只用于进程被强杀、机器中断或终态持久化本身失败，不能用作普通 checker/provider failure 的兜底。
- `not_started` 必须有原因，不能从“文件不存在”推测。
- 单个实验结果失败不得缩减 expected denominator。
- 发生设施 blocked 时不得继续付费调用，但应尽最大可能闭合 root/condition/experiment/suite manifest 和报告。

不要 broad catch：

- parser/checker/provider/retry/worker-death 等预期实验结果应通过正常 return/result envelope 传播，不应依赖异常。
- 只捕获明确 allowlist 的 runtime/executor 边界异常并转换为结构化实验结果。
- 配置、identity、catalog、selection、schema、artifact hash、invariant 错误不得被转换为实验失败。

建议新增一个聚焦模块，例如：

- `src/tokenshare/experiments/paper_terminal_outcomes.py`

用于定义终态枚举、result envelope 和 continuation policy；避免继续把更多分支塞进已经很大的 `paper_formal_runner.py`。如果现有类型模块有更合适的位置，可以复用，但必须保持协议 core、实验层和 executor 层边界。

## 四、需要检查和可能修改的文件

实施前先确认实际调用图，不得机械修改以下所有文件；只改真正需要的：

- `src/tokenshare/experiments/paper_exp1.py`
- `src/tokenshare/experiments/paper_exp3_fault_recovery.py`
- `src/tokenshare/experiments/paper_formal_runner.py`
- `src/tokenshare/experiments/paper_formal_evidence.py`
- `src/tokenshare/experiments/paper_formal_metrics.py`
- `src/tokenshare/experiments/paper_budget.py`
- `src/tokenshare/experiments/paper_smoke.py`
- `src/tokenshare/experiments/paper_smoke_report.py`
- `src/tokenshare/experiments/run_paper_experiments.py`
- 可能新增 `src/tokenshare/experiments/paper_terminal_outcomes.py`
- 新增 `benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json`
- 新增 `local/run_exp3_exp4_v3_smoke.ps1`
- 复用 `local/invoke_native_process_with_logs.ps1`，不要复制 secret 处理逻辑。

重点检查现有反模式：

- `paper_formal_runner.py` 中 dedicated worker-death baseline 强制 `root_status == completed` 后抛异常的路径。
- `_FormalConditionExecutionCallback` 如何区分 adapter outcome、exception 与 checkpoint。
- `paper_exp3_fault_recovery.py` 中 `reused_rate_fault_zero`、`dedicated_worker_death`、`additional_execution_required`、comparison seed 与 root-run counts。
- `paper_budget.py` 中 supporting baseline commitments 和实际 scheduled roots/AI units/provider upper bound。
- `run_paper_experiments.py` 中 `cross_experiment_evidence_allowed=False`。不得改成无条件 `True`；正式共享必须是按 Exp1→Exp3 purpose allowlist，smoke baseline omission则不需要跨实验 evidence。
- smoke 当前只对完整 Exp1–4 profile 执行 v3 provider config fail-closed；Exp3–4-only profile必须得到完全相同的 provider/model/request 校验。
- 顶层 `except (OSError, RuntimeError, ValueError)` 只打印失败但不闭合 manifests 的路径。

## 五、TDD：必须先出现的 RED

测试名可以调整，但行为必须全部覆盖。

### 5.1 Exp1 shared baseline 计划与分母

在 `tests/experiments/test_paper_exp3_fault_recovery.py`：

- `test_exp3_plan_reuses_exp1_shared_reference_without_zero_rate_conditions`
  - canonical Exp3 只包含正 fault rates；
  - baseline manifest/source 指向 Exp1；
  - additional baseline execution count 为 0；
  - 新 root counts 精确为 30,090 + 6,036。
- `test_exp3_conditions_across_repeats_share_one_exp1_case_reference`
  - 两个 repeat、多个 fault/death 条件指向同一 Exp1 case evidence；
  - comparison kind 是 shared reference；
  - 不要求 baseline repeat/seed 等于 fault repeat/seed。
- `test_exp3_shared_reference_rejects_case_catalog_model_or_request_drift`
  - case/catalog/split/model/config/request 任一漂移 fail closed。

在 `tests/experiments/test_paper_budget.py`：

- `test_exp3_shared_exp1_baseline_removes_all_duplicate_scheduled_roots_and_calls`
  - supporting baseline roots/AI units 为 0；
  - reference rows不增加 planned root/provider counts；
  - P0-core/P0-full 新总数正确；
  - budget digest自然变化。

### 5.2 双轴终态与继续调度

在 `tests/experiments/test_paper_formal_runner.py`：

- `test_failed_exp1_shared_baseline_keeps_exp3_dispatch_and_nulls_comparison`
- `test_checker_rejection_closes_root_condition_and_continues_next_condition`
- `test_provider_failure_closes_root_condition_and_continues_next_condition`
- `test_worker_death_incomplete_closes_manifests_and_continues_exp4`
- `test_identity_or_evidence_corruption_stops_new_dispatch_and_closes_blocked_suite`
- `test_unexpected_runner_error_records_remaining_roots_not_started`

断言不能只看函数返回值；必须检查持久化的：

- CURRENT/generation；
- per-task/per-attempt rows；
- condition/experiment/suite manifests；
- metrics/audit；
- provider call count；
- expected/started/completed/failed/blocked/not_started。

### 5.3 Exp3–4-only smoke

在 `tests/experiments/test_paper_smoke.py`、`test_run_paper_experiments_cli.py` 和 `test_smoke_launcher_supervision.py`：

- profile 精确解析 11 roots，只含 Exp3/Exp4；
- Exp3 0% baseline item 不存在；
- supporting baseline roots 为 0；
- smoke baseline policy 是显式 regression-only omission；
- baseline 缺失不阻止 11 roots dispatch；
- baseline 字段为 null/unavailable，不得伪造；
- provider v3 路径、digest、模型、endpoint、thinking、reasoning、600/300000、retry=0、max provider attempts=1、inflight=50 任一漂移均在 dispatch 前 blocked；
- formal scope 使用 smoke baseline omission policy 必须被拒绝；
- launcher identity-only 与真实命令都显式包含：
  - `--smoke-profile benchmarks/paper/paper_smoke_exp3_exp4_profile.v1.json`
  - `--ai-api-config benchmarks/paper/exp1_baseline_provider_config.v3.json`
  - 真实命令含 `--real-transport`
  - `--unlimited-budget`
- launcher 不含 Exp1、Exp2、Exp5、pilot、resume、Full、LeanAudit 或 force-all；
- secret 只在子进程环境注入，ArgumentList/command/prelaunch/process/log 均不含 secret。

使用 capturing/fake transport 做测试，绝不调用网络。

## 六、实现顺序

严格按以下顺序：

1. 阅读与调用图审计。
2. 写设计文档，明确正式 shared baseline 与 smoke baseline omission 是两个不同 scope。
3. 写第一组 RED tests并运行，保存准确失败原因。
4. 最小修改 Exp3 canonical plan与分母，使计划测试 GREEN。
5. 写 shared Exp1 evidence reference 的 RED/GREEN。
6. 写双轴终态与 continuation 的 RED/GREEN。
7. 写 Exp3–4-only profile/CLI 的 RED/GREEN。
8. 写 launcher 的 RED/GREEN。
9. 更新 metrics/report，使 unavailable 不被写成 0 或 actual。
10. 运行相关轻量回归。
11. 运行 Fast。
12. 同步权威设计、参数决策、code maps、feature/progress/handoff。
13. 输出离线 identity-only 结果与用户可复制的一行启动命令，但不实际启动。

## 七、轻量验证要求

禁止 Full。至少运行：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest -q tests/experiments/test_paper_exp3_fault_recovery.py tests/experiments/test_paper_budget.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_smoke.py tests/experiments/test_run_paper_experiments_cli.py tests/experiments/test_smoke_launcher_supervision.py
```

如果上述集合耗时过大，可以先按新测试节点分批 RED/GREEN，但交付前必须跑完整相关集合。

然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\init.ps1
```

Fast 预期必须包含：

- `python-json-sqlite-ok`
- `harness-files-ok`
- `compileall-ok`
- pytest summary 不含 `FAILED`

还必须运行新的离线 identity-only 命令，确认：

- suite/profile/catalog/selection/provider config identity；
- experiment ids 只含 Exp3/Exp4；
- direct/actual roots 为 11；
- supporting baseline roots 为 0；
- repeat `[0]`；
- worker counts只包含本 profile 实际使用值；
- request/provider controls正确；
- `paper_eligible=false`；
- execution-plan/budget digest 是当前新 output root 的 run-instance digest；
- provider calls 为 0。

identity-only 不能读取或要求 API key。

## 八、新 launcher 要求

新增 `local/run_exp3_exp4_v3_smoke.ps1`，接口继续使用：

```powershell
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$SupervisorRoot
)
```

要求：

- 三个 identity/path 必须此前不存在。
- 先运行 offline `--smoke-identity-only`。
- 冻结当前 output root 的 execution-plan/budget digest。
- 再用完全相同 profile/config 执行 real transport。
- 使用 `local/invoke_native_process_with_logs.ps1`。
- secret 不进入 ArgumentList、command text、process/prelaunch/log。
- prelaunch 明确写 `baseline_policy=omitted_for_smoke_regression`。
- 不提供 resume/restart 路径。
- 不修改原 `local/run_exp1_exp4_v3_smoke.ps1` 的历史语义；如需共享 helper，应保持旧 launcher 的回归测试通过。

交付时给用户一行启动模板，但不要执行：

```powershell
$stamp=[DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"); $runId="paper_smoke_exp3_exp4_v1_${stamp}_run01"; $outputRoot="outputs/experiments/$runId"; $supervisorRoot="local/supervision/$runId"; $scriptPath=(Resolve-Path ".\local\run_exp3_exp4_v3_smoke.ps1").Path; $launcher=Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$scriptPath,"-RunId",$runId,"-OutputRoot",$outputRoot,"-SupervisorRoot",$supervisorRoot) -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru; [pscustomobject]@{RunId=$runId;LauncherPid=$launcher.Id;OutputRoot=(Join-Path (Get-Location) $outputRoot);SupervisorRoot=(Join-Path (Get-Location) $supervisorRoot)}
```

这里的 `_run01` 只是新 Exp3–4-only smoke 系列的序号；UTC timestamp 仍保证每次 identity 唯一。若用户再次运行，应使用新的 timestamp，并可按用户习惯递增 suffix；永远不得复用已有 output/supervision root。

## 九、smoke 必需输出

用户不关心 smoke 的模型成功率或 baseline 比较，只关心设施能否跑通并产生必需 evidence。每个条件即使失败，也必须尽可能产生：

通用：

- condition/task/attempt terminal rows；
- request/raw/parsed/usage/provenance/model execution refs（若 provider 已 dispatch）；
- provider calls、retries、429、prompt/completion/reasoning/total tokens；
- usage missing；
- estimated cost与 provider actual billing availability分开；
- event/artifact refs；
- checkpoint/CURRENT/generation；
- failure stage/kind；
- secret scan；
- expected/started/completed/failed/blocked/not_started。

Exp3 rate-fault：

- frozen target selection；
- injection point；
- original与mutated output refs；
- applicability；
- detection/rejection；
- retry/requeue/reassignment；
- recovery完成或失败终态；
- no canonical pollution证据。

worker-death：

- 真实 process/PID facts；
- kill target/progress；
- dead attempt；
- lease expiry；
- recovery action；
- replacement attempt/process（若有）；
- coordinator continued；
- `recovery_completed`；
- `evidence_complete`；
- 未恢复时写 `tokenshare.paper_worker_death_incomplete.v1` 或其兼容新版本，不能抛异常中断 suite。

Exp4：

- FULL与四个 ablation mode 的真实 hook observation；
- applicability；
- parser/verifier/requeue/merge gate行为；
- completion/validity/failure；
- baseline/配对只限 Exp4 自己的 FULL 对照，不依赖 Exp1。

suite 终态：

- 即使某些 roots failed，剩余预注册条件仍执行；
- 所有条件完成调度后可为 `completed_with_failures`；
- 只有设施/identity 错误才停止新调用并标 `blocked`；
- reports与manifest必须闭合。

## 十、文档与状态同步

至少更新：

- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- `Doc/TechnicalDocument/tokenshare_experiment_parameter_decision_log.md`
- 新设计文档与实施计划
- `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- 如 CLI/launcher 导航变化，更新 `Doc/agent-navigation.md` 和 README
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

必须删除或修正以下旧表述：

- Exp3 baseline 必须 same repeat/seed 才能比较；
- worker-death 必须额外执行 dedicated no-kill baseline；
- P0 仍包含 1,006 supporting baseline roots；
- Exp3 的每种 fault type 都执行 0% condition；
- 单个 failed baseline 可以中断整个 suite；
- smoke 必须跑 Exp1/Exp2 才能运行 Exp3/Exp4。

同时保留历史说明：run01–run04 按当时设计执行，旧 evidence 不重写、不升级、不冒充新设计结果。

## 十一、最终交付格式

最终回复必须给出：

- 修改文件列表；
- RED 测试及预期失败证据；
- GREEN 与相关轻量测试结果；
- Fast 结果；
- 新正式 root-run/budget identity摘要；
- 新 Exp3–4-only smoke identity-only 摘要；
- 明确声明没有运行真实 API、Full、全量 pytest、LeanAudit 或 force-all；
- 新 profile 与 launcher 的绝对路径；
- 用户可复制的一行启动命令；
- 尚存风险或 unavailable 项；
- secret scan结果；
- 明确声明历史 run01–run04 未修改。

不得声称正式实验已经运行，也不得把 smoke 结果描述为论文结果。
