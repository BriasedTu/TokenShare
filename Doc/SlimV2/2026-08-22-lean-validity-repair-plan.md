# Slim V2 跨域失败语义与 Lean 有效性修复实施计划（2026-08-22）

## 状态与范围

- 状态：已实施并完成最终 focused 回归与文档复核。
- 目标：修复 Lean 候选提示词合同与 checker 环境，并统一修复 Lean/Factorization 的 retry、provider、验证失败语义和冻结指标污染问题。
- 保持不变：Lean catalog、pure_logic 的 leaf/root DAG、induction 单节点结构及预注册 blocked stress cases 均不改题、不改拆分。
- 验证原则：按风险驱动原则只运行定向 Python 测试和一次独立 Lean 环境测试；不调用 provider，不运行 representative/full/LeanAudit。

## 冻结科学语义

顶层 `RootResult.failure_kind` 保持现有论文三分类：`no_final`、`incorrect_final`、`infrastructure_invalid`。下表中的细分原因写入独立诊断字段，不得升级为新的顶层分类。

| 实际终态 | root failure_kind | 诊断原因 | 科学指标 |
|---|---|---|
| JSON 不符合冻结 parser 合同，重试耗尽 | `no_final` | `model_parse_exhausted` | 纳入，成功为 0 |
| 环境正常但 verifier/checker 拒绝，重试耗尽 | `no_final` | `model_verification_exhausted` | 纳入，成功为 0 |
| provider/transport 始终无可用输出 | `no_final` | `provider_transport_exhausted` | 纳入，成功为 0 |
| transport 与候选失败混合后耗尽 | `no_final` | `mixed_candidate_acquisition_failure` | 纳入，成功为 0 |
| Lean import/toolchain/project/checker 环境不可用 | `infrastructure_invalid` | `checker_environment_error` 或 preflight 细分 | 对应 cell 指标为 null，修复后按原 inventory 重跑 |
| store/ledger/冻结接线损坏或未知程序异常 | `infrastructure_invalid` | 对应设施诊断 | 对应 cell 指标为 null，修复后重跑 |

checker 的逐 attempt 终态保留为：`accepted`、`proof_rejected`、`environment_error`、`timeout`、`helper_error`、`not_reached`。基础设施类 checker 终态立即停止当前 root，不继续消耗模型 retry。

## 实施顺序

1. **全题族只读审计**：分别核对 pure_logic、induction、function_set 的 catalog、oracle、preflight、imports、source、compiled object 与 digest；明确 checker-backed 和预注册 blocked 集合。
2. **提示词合同**：加入完整 JSON 模板，显式要求 `schema_version` 字面量必须为 `lean_proof.proof_candidate.v1`，并以定向 prompt 测试锁定。
3. **独立环境测试与持久证书**：
   - 补齐默认 Lean project 对 `TokenShare.LemmaGraphOracle` 的构建依赖；
   - 新增独立命令，覆盖全部 checker-backed catalog nodes，以冻结 oracle proof 调用真实 checker；
   - 仅在全部通过时原子写入持久环境证书；
   - 实验启动只读取并轻量校验证书、输入 digest 和 `.olean` hash，不运行 Lean/lake；缺失或失效时在 provider 前停止 Lean root。
4. **checker 与 validator**：非零退出先识别环境诊断，再区分 proof rejection；validator/event 保留环境、timeout、helper 状态，不压成 rejected。
5. **projector 与结构化终态**：attempt 从公开 checker report 投影精确状态；parser 未到达 checker 为 `not_reached`。Lean/Factorization 的正常重试耗尽都返回结构化 `ProtocolRunResult`，不抛普通 `RuntimeError`，并由 projector 统一投影为顶层 `no_final` 加诊断原因；Lean 环境类 checker 终态立即终止 root并投影为顶层 `infrastructure_invalid`。
6. **reducer**：报告 `preregistered_root_count`、`scientifically_valid_root_count`、`infrastructure_invalid_root_count`；cell 含 infrastructure-invalid 时成功率及相关科学率为 null，并记录 `infrastructure_invalid_root_present`。配对任一端 infrastructure-invalid 即 ineligible，保留 planned/eligible/ineligible 数量。
7. **权威文档与地图**：同步设计宪章、指标权威、系统接线合同、设计规格、实施计划、复用清单、README、code map 与 progress；完成后逐份复读，排除旧口径和漂移。

## Exp1 coverage-tail 闭环

- 协议失败 taxonomy 与 coverage 取得是两个维度：Factorization/Lean 的有效 `no_final` 仍按冻结 `unscheduled_ai_unit_ids` 原顺序补齐 tail；只有 condition/runtime wiring failure 或 infrastructure-invalid terminal 阻断。
- execute、resume、projector 与 CLI 共用同一个 blocker predicate，避免 fresh/resume/source lookup 分叉。
- Lean DAG 上游没有 canonical proof 时，不伪造 dependency ref，也不跳过节点；保存带真实 `lemma_node_id/dependency_path`、`provider_call_made=false` 的 typed `pre_dispatch_failure` trace。
- `protocol.json` 在 tail material 准备后同时冻结 protocol projection/traces、确定性 tail requests 与已知 pre-dispatch tail traces；write/read 都校验与 unscheduled inventory 的集合闭包。恢复先物化全部已知 traces，再只执行剩余 requests。
- tail provider attempt/token/cost 只统计 `provider_call_made=true` 的 attempts；Exp2–4 的 fixed adapter 原样消费 protocol/coverage-tail 的成功或失败 result kind，不调用 provider。

## 定向验证

- Lean prompt/parser 定向测试。
- Lean checker/validator 状态分类定向测试。
- Slim projector/runtime retry 终态定向测试。
- Slim reducer cell-null 与 pair-ineligible golden 测试。
- 持久环境证书的生成、失效和启动 fail-closed 测试。
- 一次独立真实 Lean 环境测试，生成当前工作树的持久证书；随后验证实验启动路径只读证书且不调用 Lean/lake。

## 已完成证据

- 全题族审计：165 cases / 690 nodes；其中135 cases / 570 nodes为checker-backed，30 cases / 120 nodes为预注册`structured_blocked`，题库与拆分保持不变。
- 独立真实Lean环境测试：570/570 accepted，生成持久pass；实验启动轻量校验不启动Lean/lake。
- 跨层focused回归覆盖Lean+Factorization的parse/verification/provider/mixed exhaustion、checker环境立即停止、unknown runtime、Reducer cell/pair/Exp4 eligibility、fresh/resume coverage tail和fixed replay：跨层集合`140 passed`，完整场景文件`18 passed`，权威/初始化合同`25 passed`；相关源码`compileall`、pass轻量读取与`git diff --check`通过。未调用provider，未运行representative/full/LeanAudit。
