# Results-First 收口快速重放设计

## 目标

为 representative/full-plan smoke 增加一个仅用于中间诊断的快速验证路径：复用一次完整 Exp1--4 trace 已持久化的 immutable response bank、129 个 canonical direct checkpoints、协议 ledger 和 artifacts，只重建 canonical metrics、suite terminal 与 Exp5 前置验证。它用于在下一次长 E2E 前穷尽收口缺陷，不能替代最终 145-root results-first smoke。

## 权威边界

- 唯一生产语义仍由 `execute_results_first_experiments`、`execute_paper_formal_suite`、canonical direct finalizer、metric materializer 和 terminal validator 提供。
- full 与 representative 继续使用相同 `ResultsFirstExecutionAuthority`、runner、adapter、ProtocolEngine、Factor verifier、Lean checker、provider lifecycle 和 metric contract；只允许 typed coverage、budget projection 与 output root 不同。
- 原始 `local/task6-full-plan-e2e-*` 证据根只读。快速重放必须写入新的 scratch root。
- catalog、prepared request、model/provider/request identity、response-bank lineage、hard budget、ledger、fault-before-checker、correctness/completion 分母和 missingness 都不得旁路。
- receipt、L1--L4 publication closure、论文 evidence chain 和非指标 facility gates仍可在 full/representative 共用路径旁路，但不得改变指标输入或 terminal 真值。

## 方案选择

### 采用：scratch-root production resume

把已完成协议根的 suite evidence 与 canonical checkpoints复制到新的 scratch root，移除副本中可派生的 suite terminal、metric closure 和 traceability handle，然后调用现有 `resume=True` 路径。恢复逻辑必须验证 checkpoint inventory digest、event ledger hash chain、artifact refs 和 canonical evidence digest；所有129个 task key均已完成，因此 adapter dispatch 必须为零。随后由同一 production finalizer重建 metrics和terminal。

### 不采用：直接调用私有 finalizer

它更快，但会绕开 suite resume、usage totals、manifest finalization和terminal lifecycle，无法代表 full实验的真实收口路径。

### 不采用：仅 synthetic unit fixtures

它适合定位单字段错误，但已经多次遗漏跨129根、跨实验和source/current双域的集成问题。

## 数据流

1. 只读源：final E2E suite root、canonical checkpoint root、immutable bank、acquisition ledger。
2. scratch复制：复制suite evidence与checkpoint；bank对象保持内容寻址，只在scratch authority中引用。
3. 派生清理：只删除scratch中的 `formal_runner_result.json`、derived metric/traceability closure和允许重建的experiment/suite finalization文件；不得删除condition generations、current refs、ledger、artifact index或checkpoint。
4. resume恢复：`_restore_canonical_direct_checkpoints` 重建 collector；completed task keys阻止协议/adapter重新执行。
5. 收口重建：统一验证Exp1--4 current-provider=0、source-bank consumption、condition/root固定分母、canonical correctness/completion、latency/token/cost双域和 missing_count/reason。
6. Exp5快速验证：用同一 selected full authority 的16个conditions、四个正式endpoint和同一 atomic ledger factory运行offline transport；验证每个endpoint实际dispatch、usage missing上界和crash/reconcile语义。
7. 只有快速重放、Exp5组合和独立审查全部GREEN后，才允许新的完整offline E2E。

## 安全与失败语义

- transport和secret resolver必须fail-fast；任何网络调用立即失败。
- 重放必须断言 acquisition calls=0、adapter dispatch=0、current provider calls=0。
- checkpoint缺失、重复、digest漂移、未知route、current/source混用、ambiguous ledger或不完整metric lineage全部fail-closed。
- `provider_attempt_count`显式字段优先；只有字段缺失时才允许版本化legacy fallback。显式0不得被positive ordinal或source provider identity覆盖。
- 错误Factor答案、Lean checker rejection、NO_VERIFICATION false validity和fault replacement继续留在固定completion/correctness分母。

## 验证矩阵

- 129 checkpoints精确恢复：Exp1/2/3/4=`12/6/81/30`。
- 所有task/attempt current provider count为0；source bank consumption单独计量。
- condition result与suite terminal provider聚合不把attempt ordinal、fault或ablation尝试当真实provider调用。
- metric routes覆盖8个official experiment/evidence pair；unknown和mixed route拒绝。
- Exp1 trace的8个source roles可物化；current latency/token/cost保持`None + reason`。
- correctness、completion、missing_count/reason、protocol latency和source tokens/cost均由现有metric代码生成。
- Exp5 16 conditions覆盖四endpoint，ledger reserve/intent/terminal/reconcile完整，ambiguous不盲重试。
- scratch源证据未修改；`git diff --check`和受保护untracked清单不变。

## 最终验收

快速重放只是一道加速门。最终完成仍要求 fresh root 的完整 representative full-plan smoke 自然terminal成功：145 roots，Exp1--5=`12/6/81/30/16`，Exp1--4 current provider calls=0，Exp5四endpoint均实际dispatch，真实acquisition与atomic ledger完成，所有核心指标有真实值或合法`None + missing_count/reason`。之后 full只能修改typed规模选择，不得更换service、runner、adapter、metric或ledger路径。
