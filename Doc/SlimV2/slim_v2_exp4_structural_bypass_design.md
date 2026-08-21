---
status: approved_under_user_delegation_by_quorum
document: slim_v2_exp4_structural_bypass_design
scope: Experiment 4 structural local bypass design and Task 4 implementation contract
date: 2026-08-21
---

# Slim V2 Experiment 4 结构性局部旁路消融设计

**归属说明**：本文的设计、审核、权威同步和文档 checkpoint 不属于任何 Stage 或 Task；本文通过法定人数审核后的代码实施、focused verification 和 Experiment 4 后续推进归入 Task 4。
**上位权威**：`slim_v2_design_charter.md`、`slim_v2_experiment_metrics_authority.md`、`slim_v2_system_integration_contract.md`。
**用户授权**：用户已直接批准为四类消融设计结构性局部旁路，并允许为已证明的 shared-interface gap 制定和实施最小修改；所有重大实现决策仍须三 Agent 同证据多数票。
**审核结论**：首轮三名独立 reviewer 一致选择 `RECOVERY_MERGE_FIRST`，并以 `authorize=no` 指出 V/P 边界过晚、premature schema 伪称 checker 失败、projector 反推事实等问题。修订后同三名 reviewer 第二轮以 `3/3` 一致给出 `RECOVERY_MERGE_FIRST + authorize=yes`，范围内 `Critical/Important=0/0`；本文据此获批进入权威同步与 Task 4 实施。

## 1. 冻结结论

Experiment 4 不通过“先运行机制、再忽略结果”冒充消融，也不新建第二套 runner 或协议状态机。它在四个**实际调用之前**选择正常路径或结构性局部旁路，并继续复用同一 `Coordinator`、`ProtocolEngine`、任务图、事件账本、真实 plugin/checker 和 Slim projector。

| 机制 | 必须位于的真实边界 | 结构性旁路 | 真实性证据 |
|---|---|---|---|
| `V` Verification | Factorization `verify_submission` 前；Lean `normalize_proof_submission`/child checker 前 | 不调用 target child 的真实 verifier/checker；用明确的 synthetic verification provenance 满足 engine canonical 合同；独立 root checker不关闭 | 两领域 target child call count=0；synthetic provenance；真实 canonical/root facts |
| `P` Parser/policy | fixed trace raw artifact 已保存、`_parse_domain` 尚未调用 | 不调用 domain parser；把真实 raw ref 沿既有 submission/canonical 流程传递 | 两领域 target parser call count=0；raw/candidate/canonical refs 与 route observation |
| `R` Requeue/replacement | recovery decision 已持久化、replacement 尚未创建 | 保留 `retry_allowed=true` 决定，但不创建 replacement；形成真实 no-final/stuck | linked recovery、R route applied、无 replacement、无 final |
| `M` Merge gate | recovery decision 已持久化、任何 requeue/replacement 前 | 以当前真实 canonical children 计算 readiness；false 时真实调用一次 plugin `build_merge`，记录真实 plugin/root-check outcome并终止 | readiness=false、missing ids、typed plugin outcome、nullable root-check、无合成 child |

其余五个双消融均直接组合单消融路由。`{R,M}` 冻结为三票一致的 `RECOVERY_MERGE_FIRST`：同一 recovery boundary 先执行 M；M disabled 且 readiness=false 时 premature attempt 抢先终止，R 记为 `preempted_by_merge_first`，`stuck_due_to_no_requeue=false`。

## 2. 已证明的现状缺口

### 2.1 fixture 合同已纠正

Factorization composite 的 required targets 使用 `all_true_divisor_ranges`；不得再使用只选最后 range 的旧 fixture 口径。prime 仍选稳定排序最后 required range；Lean 选稳定排序最后 required terminal slot。

### 2.2 M 的 shared 调度时序缺口

当前 logical coordinator 的顺序是：

```text
recovery decision
  -> retry
  -> requeue
  -> dispatch/drain replacement
  -> normal merge readiness
```

所以 `REQUIRED_CHILD_DELAY × NO_MERGE_GATE` 到达既有 `before_merge(MergeContext)` 时 replacement 已完成，`gate_satisfied=true`。借 `before_requeue.stop` 会把单独 M 偷换成 `{R,M}`，并破坏 FULL。

2026-08-21 fresh focused evidence：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/slim_v2/test_scenarios.py tests/experiments/slim_v2/test_answer_paths.py -q
```

三名 reviewer 分别复跑/核对得到相同结论：`1 failed, 27 passed`；唯一失败是该 cell 实际 `merge_gate_satisfied=True`，冻结预期为真实 `false`。

### 2.3 P/V 的 answer-stage 边界缺口

这两个缺口优先 Slim-local 闭合，不修改共享 plugin 正常调用路径：

- P：`FixedTraceSubmissionAdapter.execute()` 当前在 coordinator `before_parser` 前已经通过 `_submission_from_content()` 调用 `_parse_domain()`；现有 policy 只是解析后覆盖 refs，不是真旁路。
- Lean V：`LeanExecutionBridge.execute()` 当前在 coordinator verification gate 前已经调用 `normalize_proof_submission()` 和真实 child checker；只跳过后续 `verify_submission()` 不能得到 checker call count=0。
- invalid-candidate challenge 当前若只在 coordinator `after_parsed_candidate_persisted` 注入，对 Lean checker 过晚。它必须移到 fixed adapter 完成 parse 后、Lean execution bridge normalization 前的 mode-blind controller。

### 2.4 现有 observation/projector 缺口

- shared `ExperimentPrematureMergeAttemptedPayloadV1` 强制 `root_check_passed: bool`；现代码在 plugin 先拒绝时仍硬编码 false，混淆“checker 未运行”和“checker 返回 false”。
- projector 以 `wrong_canonical and not verified_correct` 推断 checker rejection，以 mode/`processing` 等推断 R stuck，都会把证据缺失投成肯定事实。
- shared ablation-mode whitelist 不包含六个 pair；Slim V2 不扩大该 whitelist，也不让 shared core 认识 11 个实验 mode。Exp4 route evidence 改由 Slim-local typed observations 承载。

## 3. 目标、非目标与不变量

### 3.1 目标

1. 四个 single 均实际删除对应调用/调度行为，且能从真实 evidence 得到冻结指标。
2. 六个 pairs 每个只运行一个真实 root，不拼接两个 single 结果。
3. challenge controller 对 mode、disabled set、mechanism policy 完全不可见。
4. `FULL`、Exp1 trace 复用、65 roots×3 repeats、logical timing、provider calls=0 与 153 metric IDs 不变。
5. shared 修改只补 recovery-before-replacement 的可选通用接缝、typed incomplete-merge rejection 和 synthetic provenance 修正；默认 caller 不增加 hook 调用或 observation。

### 3.2 非目标

1. 不新建 runner、scheduler、协议状态机、event ledger 或结果 authority。
2. 不把 Exp4 mode/challenge 名写入 shared core/plugin contract。
3. 不合成 required child、canonical artifact、checker report 或 final。
4. 不把所有 plugin/infrastructure exception 归为实验 rejection。
5. 不恢复 legacy paper/formal pipeline，不调用 provider，不扩大 Lean 专项测试。

### 3.3 始终成立的不变量

- challenge identity 只由 `case_id × repeat_id` 决定；跨 mode 比较 `challenge_plan_id/family/target set/attempt rule`，不新增 digest authority。
- shared engine 仍唯一拥有 task/attempt/canonical/event state；Slim observation 不写协议状态。
- inventory 只提供配置身份与 observation 行壳，不证明 route 到达或执行。
- root checker 未到达时，`root_checker_reached=false` 且 `root_check_passed=null`。
- 非预期 exception 保持 `infrastructure_invalid`，不能伪装成 ablation failure。

## 4. 总体工程结构

```text
Exp1 fixed trace
      |
ModeBlindChallengeController
  raw transform / parsed-candidate transform / no-return
      |
FixedTraceSubmissionAdapter
  parser route: PARSE | RAW_PASSTHROUGH
      |
domain execution bridge
  Lean V route: NORMALIZE_AND_CHECK | UNCHECKED_CANDIDATE
  Factorization V route remains coordinator verification gate
      |
shared Coordinator / Engine / Ledger / Graph
      |
recovery-premerge optional seam -> Exp4StructuralRouteObserver
      |
actual events/artifacts/plugin outcomes + Slim typed route observations
      |
evidence-only projector -> frozen raw rows -> reducer
```

### 4.1 两类对象严格分离

- `ModeBlindChallengeController`：只接收 challenge plan、unit/attempt 和 fixed trace；负责 raw transform、parse 后 candidate transform、no-return/delay。其构造函数、字段和方法均无 mode/policy/disabled set。
- `Exp4StructuralRouteObserver`：只接收冻结 route flags、plugin runtime、artifact store 和 typed observation sink；不决定 challenge 内容。它记录 V/P/R/M 实际 route，并实现可选 recovery-premerge hook。

二者可以由 `ScenarioV1` 同时持有，但不能合并为能同时读取 challenge 与 mode 的控制器。

### 4.2 不引入通用拓扑 DSL

`scenarios.py` 保留显式 11-mode→`{V,P,R,M}` 集合映射，再翻译为四个 route flags 和既有 `ProtocolMechanismPolicy`。不创建动态节点注册、通用 DAG DSL 或另一套调度解释器。

## 5. 四类单消融方案

### 5.1 V：`NO_VERIFICATION`

#### Factorization 路径

1. fixed adapter 正常解析并完成 mode-blind invalid-candidate 变换。
2. coordinator 到 verification boundary 时，`verification_enabled=false`，不调用 `FactorizationRuntimeAdapter.verify_submission()`。
3. `_build_no_verification_report()` 使用固定 `validator_policy_id=runtime_ablation_no_verification.v1`、`domain_verifier_invoked=false` 和明确 synthetic provenance；不得继承真实 plugin validator ID。
4. 共享 engine 仍以该显式实验报告绑定真实 candidate refs。

#### Lean 路径

1. 新增 Slim-local `SlimLeanExecutionBridge`，正常 mode 委托现有 `LeanExecutionBridge`。
2. V disabled 的 proof unit 只调用 fixed candidate executor，不调用 `normalize_proof_submission()`/child checker；把真实 proof-candidate ref 映射到 execution request 所需的 `proof_artifact` candidate name，并记录 `unchecked_candidate` provenance。
3. 若 P 同时 disabled，bridge 收到 raw-only submission 后原样返回；coordinator 随后由 P route 映射 raw ref，再由 V synthetic report接受。任何 domain parser、Lean normalization 或 child checker都不得发生。
4. root/merge deterministic unit 仍走现有 Lean bridge；独立 root checker不因 V 关闭。

#### V 证据与指标

- `domain_child_checker_call_count`：Factorization verifier/Lean normalize child checker 的 target 调用；V disabled 必须 0，FULL 必须大于 0。
- `plugin_verify_submission_call_count` 与 `root_checker_call_count` 分列，禁止把三者合成一个含混 checker count。
- candidate independent label、canonical event、final/root checker事实决定 wrong-canonical 和 root rejection；synthetic report本身不等于“错误已接受”。

### 5.2 P：`NO_PARSER_POLICY`

1. `FixedTraceSubmissionAdapter` 先读取 fixed source 并持久化真实 raw artifact。
2. 在 `_parse_domain()` 调用之前读取不可变 `parser_route`。
3. `PARSE` 走当前正常实现；`RAW_PASSTHROUGH` 不调用 `_parse_domain()`，返回 `result_kind=succeeded`、含 raw ref、空 parsed/candidate refs和 raw-passthrough provenance 的 submission；`parse_result=bypassed` 写入 Slim `AttemptResultV1`/route observation，不伪装成 `ExecutionSubmission` 公共字段。
4. coordinator 的既有 P policy 把 raw ref 映射到 output contract required names，再继续实际启用的 V/R/M 路径。
5. parser-required challenge 仍由 mode-blind controller 在 raw content 上变换，controller 不知道 parser route。

P 的充分证据为 target parser call count=0、raw route observation、真实 raw/candidate/canonical refs。禁止先解析再替换 ref；禁止只从 `P` disabled 推出 `raw_only_exposed/accepted`。

### 5.3 R：`NO_REQUEUE`

1. 共享 recovery policy先真实记录 trigger 与 `retry_allowed=true` decision。
2. recovery-premerge seam若存在先按第 5.4 节运行；M 返回 WAIT 后才进入 R。
3. R disabled 时既有 `before_requeue`/policy path记录 `route_applied=true`，不创建 replacement attempt，不伪造 failed child。
4. coordinator 终止该 root 的可推进循环；root 保持真实 no-final 状态。

`stuck_due_to_no_requeue=true` 的充分且必要 join：同一 linkage 的 recovery `retry_allowed=true` + R route actually applied + replacement attempt不存在 + final不存在。`protocol_status=processing`、mode 名或 `retry_allowed=false` 均不能单独推出 stuck。

### 5.4 M：`NO_MERGE_GATE`

#### 5.4.1 shared 可选 recovery-premerge seam

新增一个**不同于正常 `before_merge`** 的可选通用 capability `before_recovery_merge(RecoveryMergeContext)`。`RecoveryMergeContext` 组合现有 `MergeContext` 的 parent/canonical/required/readiness/event事实，并显式增加 `recovered_attempt_id`、recovery trigger/decision 与 `recovery_event_refs`，不让 observer 从可变记忆反推 linkage：

- coordinator 仅在 hook 对象实际提供 callable capability 时才计算/调用；默认 `NoOpRuntimeHooks`、Exp1/2/3/5 与非 Slim caller 不实现它，因此无新增调用、observation 或 event。
- logical 路径：`_LogicalPendingRetry` 的 `_record_recovery()` 后、创建 `_LogicalPendingRequeue` 前。
- non-logical 路径：`_recover()` 的 `_record_recovery()` 后、`_finish_requeue()` 前。
- 两条路径使用同一内部 helper 和同一个 outer root/expanded-children/merge-plan snapshot；若 root 尚无 merge plan或 recovery decision不允许 retry，直接继续旧路径，不调用 hook。
- capability 不接收 experiment、mode、challenge 或 expected outcome；context 继续使用真实 parent/canonical children/required IDs/readiness/event refs。
- once-only key 固定为 `parent_unit_id × recovered_attempt_id`；重复调用属于 infrastructure error。

正常 `before_merge` 保持原调用次数和语义，不拿来兼任前置 seam。
Exp4 hook 的正常 `before_merge` 不再请求 bypass；M 的唯一绕行入口就是 recovery-premerge capability，防止 replacement 后的旧 late path重复或伪造观察。

#### 5.4.2 `RECOVERY_MERGE_FIRST`

所有 Exp4 mode 在上述 seam 先计算真实 readiness：

- M enabled：observer 记录 `gate_satisfied=false/missing ids` 后返回 WAIT；coordinator 再进入 R/requeue。FULL replacement 完成后仍走正常 merge，因此得到真实 false→true 而终态不变。
- M disabled 且 readiness=false：observer 用当前真实 canonical children调用一次真实 plugin `build_merge`，记录 outcome，返回 `GateDirective(stop=true)`；coordinator 不创建 replacement，也不进入正常 merge phase。
- M disabled 但 readiness=true：不构成 premature attempt；observer 记录 `plugin_outcome=not_attempted` 并返回 CONTINUE，coordinator 继续既有 recovery transition，随后只由正常 merge phase处理 ready state；observer 不执行 `MergeAction`，也不为 mode 凑计数。

#### 5.4.3 typed plugin rejection

在 `src/tokenshare/plugins/contracts.py` 增加通用 `IncompleteMergeInputError(ValueError)`。它不含 Exp4 字段，并保持现有 `ValueError` caller 兼容：

- Factorization 仅在真实 partial inputs 无法解析 parent output 的既有分支抛该类型；
- Lean 仅在 required checker-accepted inputs 不完整的既有分支抛该类型；它必须先按 `merge_plan.required_slots` 比较 required logical keys 与现有 checker-accepted keys，缺任一 required key立即抛该类型，不能落入后续 list comprehension 的 `KeyError`；
- observer 只捕获该类型并记录 `plugin_outcome=rejected_incomplete_input`；`KeyError`、`TypeError`、其他 `ValueError` 和任意未预期 exception 原样传播为 infrastructure-invalid。

#### 5.4.4 premature outcome v2

Exp4 不使用现有 shared `ExperimentPrematureMergeAttemptedPayloadV1` 判定科学结果。新建 Slim-local versioned `PrematureMergeObservationV2`，至少包含：

| 字段 | 语义 |
|---|---|
| `recovery_attempt_id` | once-only/linkage key |
| `gate_satisfied` | plugin readiness实际值 |
| `required_child_unit_ids` / `canonical_child_unit_ids` / `missing_required_slot_ids` | 当前图快照 |
| `plugin_merge_attempted` | 是否实际进入 `build_merge` |
| `plugin_outcome` | `not_attempted`、`rejected_incomplete_input` 或 `candidate_produced` |
| `plugin_error_kind` | typed rejection 名；无则 null |
| `root_checker_reached` | 是否有真实 plugin/root checker report |
| `root_check_passed` | reached时 bool；未到达为 null |
| `final_result_present` | 是否形成真实 protocol final；前置 probe 不直接创建 final，通常 false |
| `failure_stage` | `plugin_merge`、`root_checker`、`no_final` 或 null |

typed rejection 时固定为 `root_checker_reached=false/root_check_passed=null/final_result_present=false/failure_stage=plugin_merge`。

若 `build_merge` 返回 `MergeAction`，不得把 action 等同 `MERGED`，也不得用 readiness=false 调用当前 `MergeCoordinator`。observer 只记录 `candidate_produced`：Lean 仅在公开 `merge_result.root_checker_report` 实际存在时记录 reached/pass；Factorization 没有独立 checker report则 reached=false/pass=null。因为没有真实 protocol final，`final_result_present=false`，failure stage 为 `root_checker`（真实 rejected）或 `no_final`。权威 challenge预期两领域走 typed incomplete-input rejection；candidate-produced 属实际反例而非 infrastructure error，仍按真实字段报告。

## 6. 双消融组合

| 组合 | 同一真实 run 的边界顺序 | 专用 runner/分支 |
|---|---|---|
| `{V,P}` | raw passthrough → Lean不normalize/checker → coordinator synthetic V | 无 |
| `{V,R}` | V alternate route；若形成 retry-allowed recovery，再执行 R | 无 |
| `{V,M}` | V alternate route；若到 recovery-premerge，再执行 M | 无 |
| `{P,R}` | raw passthrough；若形成 retry-allowed recovery，再执行 R | 无 |
| `{P,M}` | raw passthrough；若到 recovery-premerge，再执行 M | 无 |
| `{R,M}` | recovery → M first；M premature attempt抢先，R=`preempted_by_merge_first` | 无第二 root；只需冻结 precedence |

并非每个 root 都必须让 disabled mechanism产生阳性结果；未到达边界按实际 opportunity 规则报告。每个 pair 仍有两个配置身份 observation 行，但 route result只能来自实际 evidence。

`{R,M}` 唯一合法 raw outcome：M attempt按实际记录；若它抢先终止，`stuck_due_to_no_requeue=false`、R `route_status=preempted_by_merge_first`。禁止同一 run 同时报 premature-M 与 stuck-R。

## 7. 指标与 projector 事实规则

| 字段/指标 | 唯一事实来源 | 禁止推导 |
|---|---|---|
| `wrong_canonical_accepted` | independently-invalid candidate与真实 canonical-selection event linkage | V mode或synthetic report存在 |
| `root_checker_rejected_after_wrong_canonical` | linked root-check report显示 reached=true/pass=false | `not verified_correct`、no-final |
| `raw_only_exposed` | P raw route实际到达并应用 | P disabled identity |
| `raw_only_accepted` | raw route ref进入真实 canonical-selection | raw exposed 或 P mode |
| `stuck_due_to_no_requeue` | retry-allowed recovery + R applied + no replacement + no final | `processing`、R mode、retry disallowed |
| `merge_gate_satisfied` | recovery-premerge readiness observation | premature payload存在或M mode |
| `premature_merge_attempted` | v2 `plugin_merge_attempted=true` | gate disabled配置 |
| `premature_merge_failed` | typed plugin rejection、真实 root checker rejection或attempt后真实 no-final | 固定 `root_check_passed=false` |
| correctness/completion | final artifact、root state、独立 checker | challenge expected label |
| attempts/resources | shared attempt/event和冻结 reducer | 另建 ablation 统计器 |

inventory 只创建 disabled-mechanism 行壳与适用性。实际 route evidence缺失时，字段为 `null` 并带 `missing_actual_route_evidence`；不能按预期填 true/false。`not_applicable` 才用于 challenge/mode确实不适用，不能掩盖本应到达却缺证据的 wiring error。

`premature_merge_failure_count` 仍按权威定义：实际 attempt 中 plugin rejection、真实 root checker rejection或未形成 final 的数量。`root_checker_rejected_after_wrong_canonical_count` 只计 checker明确拒绝，不把 plugin rejection/no-final并入。

## 8. 文件与状态所有权

### 8.1 Slim-local Task 4

- `scenarios.py`：显式 route mapping、mode-blind controller、structural observer、`RECOVERY_MERGE_FIRST` 与 local typed observations。
- `execution.py`：raw-before-parser route；Slim Lean V bridge；调用计数/route provenance。
- `runtime.py`：把 challenge controller与route observer分开接入，不解释指标。
- `schema.py`：Slim route/premature observation v2 与 nullable checker合同。
- `projector.py`：只做 evidence join；删除 mode/终态反推。
- `test_scenarios.py`：四 singles、六 pairs、两领域与 precedence。

### 8.2 获用户授权的最小 shared 范围

- `src/tokenshare/local_runtime/coordinator.py`：可选 recovery-premerge capability，两条 recovery 路径共用 helper；synthetic V validator provenance固定。
- `src/tokenshare/local_runtime/contracts.py`：仅在表达可选 hook capability需要时增加通用 protocol；不修改 legacy premature v1 来承载 Slim science。
- `src/tokenshare/plugins/contracts.py`：通用 `IncompleteMergeInputError(ValueError)`。
- Factorization/Lean `runtime_adapter.py`：把两个已经存在的“required input不完整”拒绝改抛上述 subclass；其余行为不变。
- shared focused tests：默认/NoOp零新增调用、logical/non-logical时序、typed exception兼容。

任何超出上述文件/接口的新 shared 需求都要重新三 Agent 投票；不得借本授权重构 plugin 或 coordinator。

## 9. 异常与终止语义

1. `IncompleteMergeInputError` 是预期实验 rejection；记录后该 scenario root终止，不继续 replacement。
2. 其他 exception 是 infrastructure-invalid并传播；不得 `except Exception` 归入 premature failure。
3. root checker未调用时 pass为 null；只有真实 report才可写 reached/pass。
4. `R` no-requeue只形成实验性 no-final/stuck分类，不伪改 task为成功/失败。
5. 每个 recovery attempt最多一次 premerge probe/premature attempt；normal merge不重复计数。
6. `build_merge` 返回 action只说明 plugin产生 candidate，不说明 core merge task、canonical root或final已经形成。

## 10. 风险驱动 focused verification

### 10.1 answer-stage 路由

1. Factorization V：target verifier count=0；FULL count>0；synthetic validator ID不冒充 plugin。
2. Lean V：target normalize/child-checker count=0；FULL count>0；root checker仍独立统计。
3. 两领域 P：target `_parse_domain` count=0；FULL count=1；raw ref/provenance真实。
4. `{V,P}`：raw直接进入 synthetic V，不经过 parser或Lean normalization/checker。

synthetic V 测试同时断言固定 validator/verifier identity、`domain_verifier_invoked=false`，以及 domain layer 的 reason/summary 明确为 synthetic bypass；真实 plugin ID/version只保留 output-contract provenance，不得被 projector解释为真实 domain checker已运行。

### 10.2 recovery/M 路由

1. logical与non-logical默认 caller：无 optional hook调用、无新 observation、replacement/canonical/final不变。
2. FULL `REQUIRED_CHILD_DELAY`：premerge false→replacement→normal merge true。
3. Factorization/Lean M：pre-replacement false、missing ids真实、`build_merge`恰一次、typed incomplete rejection、root checker未到达/null、replacement不存在。
4. R-only：premerge WAIT后R applied；retry allowed、无 replacement、无 final；不调用 `build_merge`。
5. `{R,M}`：M attempt恰一次、R status=`preempted_by_merge_first`、stuck false；删除旧双阳性断言。

### 10.3 projector 负测

分别构造：plugin rejected、root checker not reached、root checker reached/rejected、no-final、missing route evidence。证明 `not verified_correct`、mode、`processing` 均不能单独生成 checker rejection/stuck/premature字段。

### 10.4 回归范围

- 重新运行 `tests/experiments/slim_v2/test_scenarios.py tests/experiments/slim_v2/test_answer_paths.py -q`，必须全绿。
- 补最小 shared coordinator/contract/plugin focused tests，证明 exception仍是 `ValueError` subclass兼容。
- 对受影响路径做 Exp1/3/5 与非 Slim default compatibility focused tests；不跑全量 suite、LeanAudit、catalog或`lake/lean`。
- Exp4 provider/transport spy必须为0。

## 11. Task 4 实施切片与提交边界

1. 先写/改 focused tests，固定 P/V call-before-boundary、M timing、nullable checker、projector负规则和 RM互斥。
2. 实施 Slim P/V alternate routes和challenge controller前移，不触碰 shared plugin正常 bridge。
3. 实施最小 shared optional seam、typed exception和synthetic provenance；验证默认零影响。
4. 实施 Slim structural observer/premature v2与 evidence-only projector。
5. 运行四 singles、六 pairs、两领域focused tests和Exp4 preflight，再继续Task 4其他内容。

shared seam与typed exception单独提交；Slim route/projector随后提交。若默认兼容失败，回滚 shared提交并保留失败证据；禁止用 `before_requeue.stop` 或 mode-name projector作为临时替代。

## 12. 法定人数审批记录与后续门

- 首轮：三票均为 `RECOVERY_MERGE_FIRST + authorize=no`；共同 Critical/Important 已全部修订。
- 第二轮：三票均为 `RECOVERY_MERGE_FIRST + authorize=yes`；范围内 `Critical/Important=0/0`。
- 第二轮 Minor 已全部吸收：五个普通 pairs 计数修正；`parse_result` 移至 Slim observation；`plugin_outcome=not_attempted`；ready transition归还正常 merge phase；synthetic layer显式 bypass；`RecoveryMergeContext` 携带 recovery identity；Lean required-key precheck。

后续仍必须完成：

1. 把本文结论同步到 README、四份前置权威、design spec、reuse inventory、implementation plan、relay当前覆盖说明、agent navigation与progress。
2. 同步后跨 `Doc/SlimV2/` 检索并消除旧的“shared gap none”“M只需 before_merge”“checker false等于not reached”“RM双阳性”等表述，并由全新只读 reviewer做一致性复核。
3. 本轮只提交 docs-only checkpoint，绝不混入 Task 4 当前代码；随后重新唤起用户指定的同一 Task 4继续实施。
4. 设计批准只授权实施，不表示当前 WIP 或 focused tests 已通过；Task 4 必须按第 10 节取得 fresh 全绿证据。
