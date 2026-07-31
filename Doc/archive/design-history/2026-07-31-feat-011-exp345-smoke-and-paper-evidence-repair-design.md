# feat-011 Experiment 3/4/5 smoke 与论文证据闭环修复设计

状态：代码与离线验收已完成；等待另行授权的真实 API smoke

日期：2026-07-31

权威口径：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`

## 1. 范围与边界

本轮只修复 Experiment 3、4、5 的 smoke 可运行性、正常研究流程 provenance、正式分母/missingness 和论文表图输出。不会运行 `pytest tests`、`.\init.ps1 -Full` 或其他全量测试；不会新增人为篡改、攻击输入、签名鉴权或安全 fuzzing。

真实 API smoke 会产生费用。本轮先完成代码、离线回归、plan/identity/replay/output-contract 验收；只有获得用户对本次付费调用的明确授权后才启动真实 11-root Exp3/4 或 8-root Exp5 smoke。

## 2. 已确认事实

1. 当前 Factorization 和 Lean 正常 paper adapter 均通过 `ProtocolRunCoordinator` 与 `ProtocolEngine` 执行完整协议生命周期；capturing/scripted transport 固定 paper-ineligible。
2. Exp3/4 历史 smoke 的 Windows 子进程句柄、`mappingproxy` 序列化、eligibility flag 与 launcher exit-code 阻断已有代码修复，当前定向回归通过，但尚无修复后的新真实 11-root smoke 证据。
3. Exp5 8-root smoke 不生成 `tokenshare.paper_model_endpoint_smoke_evidence.v2`；四个 tracked entry 的 `smoke_evidence_ref` 仍为空，正式 preflight 无法闭合。
4. 当前 Exp5 smoke evidence validator 只验证引用字段形状，不从 source suite 的 artifact index 解析并验证实际 bytes/hash。
5. `render_exp5_paper_artifacts()` 仅由测试直接调用；formal execute/replay 没有传入 renderer 所需 rows，因此生产 CLI 不会生成完整的 6 audit + 8 paper artifacts。
6. Exp5 identity mismatch 只在一个 root 完成后标记，不会停止同 condition 后续 roots；中途停止时未执行 roots 也未稳定物化为 `not_started`，会使正式 1,284 root 分母缩水。
7. transport 异常可能被上层泛化为 `executor_error`，部分 blocked 诊断可能使用历史 member id；这两项只修 taxonomy/identity 可观察性，不扩展安全范围。

## 3. 采用方案

采用针对现有边界的最小闭环，不重写 formal runner：

### 3.1 Artifact-backed Exp5 smoke evidence

- 8-root smoke 完成后，从该 smoke suite 的已提交 generation、attempt、model execution record、artifact index 和 suite identity 生成一个独立 evidence bundle。
- 每个 v3 cohort member 都必须覆盖 Factorization 与 Lean 各一个 root；能力、请求控制、configured/requested/resolved model identity 和实际 provider-attempt inventory 必须从持久化 evidence 派生。
- evidence ref 必须包含 source suite root 的只读定位、artifact id/path、真实 content hash、suite/plan/config/cohort/entry-map digest 和 member identity。
- formal preflight 接受显式 evidence bundle 输入并冻结其 digest；逐引用通过统一只读路径解析器定位 source suite，再使用 artifact inventory 与实际 bytes 校验，不能依赖手工填写的 `sha256:` 字符串。
- tracked entry map 不写入机器本地 runtime 路径；运行时 evidence 与 tracked model identity 保持分层。

### 3.2 Exp5 condition-local fail-stop 与完整分母

- root 仍按冻结顺序执行；每个 root 完成后立即应用 identity audit，再决定是否调度下一 root。
- resolved model 缺失或不匹配时，当前 root 保留真实 attempt/model record 并标记 `model_identity_mismatch`；停止该 condition 的新 provider 调用，但继续后续预注册 condition。
- 同 condition 其余 root 物化为带结构化 reason 的 `not_started` task/attempt/event/artifact evidence，保持 plan 中的总分母和 missingness。
- metrics 的 Exp5 分母必须以冻结 selection/condition inventory 为准，并对 persisted task inventory 做等值校验；不能以“当前落盘多少 task”替代 planned denominator。

### 3.3 正式输出与 replay 同源

- 从 `recompute_paper_formal_metrics()` 的持久化 evidence 派生并暴露 renderer 所需的六类规范 rows；execute 和 replay 调用同一组装函数。
- 生产 CLI/replay 必须生成并纳入 manifest：6 个 audit 文件与 8 个 paper 文件。
- replay 只读既有 provider artifacts，不调用 provider；相同 evidence 产生稳定 digest 与相同表图内容。
- 失败和 `not_started` 保留在 audit 分母中；paper 输出对不可用指标写 `null` 与 reason，不用默认成功值补齐。

### 3.4 启动与诊断

- 增加固定 v3 cohort/profile/config/digest 的 Exp5 PowerShell smoke launcher，先做 identity-only preflight，再要求全新绝对 output root，沿用 secret 注入与日志脱敏规则。
- transport taxonomy 保留底层 `timeout`、`connection_error`、`rate_limited`、provider/client error；只有 provider 前本地 executor 失败才使用 `executor_error`。
- blocked 诊断统一输出 canonical v3 cohort member id。

## 4. 反伪造不变量（正常研究流程）

- 不允许 scripted/mock/capturing fallback 变成 paper-eligible。
- 不允许 smoke/formal runner 根据 condition/fault/mode 常量直接填写成功、检测或恢复结果。
- 不允许 formal preflight 只凭 inline metadata 放行；必须解析并校验 source smoke artifact。
- 不允许 resume/replay 再次调用 provider。
- 不允许因失败、identity mismatch、budget stop 或未启动而缩小冻结分母。
- 不允许绕过 Factorization verifier 或固定 Lean checker 产生 accepted validity。

## 5. 验收

仅运行以下非全量验证：

1. 新增 RED 测试先证明每个缺口可复现，再实现 GREEN。
2. Exp3/4 worker-death、mappingproxy、launcher exit-code、formal evidence/metrics 定向回归。
3. Exp5 smoke evidence 生成与实际 artifact hash 验证；伪 inline ref 在正常 validator 中 fail closed。
4. Exp5 多-root identity mismatch 的 condition-local 停机、后续 condition 继续、planned/not_started 分母测试。
5. formal execute/replay 生成完整 6 audit + 8 paper artifacts，并验证 replay provider call count 为 0。
6. Exp3/4 与 Exp5 identity-only/plan-only 输出合同；`.\init.ps1` 快速档。
7. 真实 smoke 只在另行获得付费授权后执行；未执行时明确记录为剩余验收，不宣称真实 endpoint 已通过。

## 6. 实施结果

工作包 A/B/C 均已完成并经独立 Agent 复核；根集成相关集为 442 passed，Fast 为 456 passed/1 skipped。Exp3/4 离线身份、Windows worker-death、fault/ablation、metrics 和 report/replay 通过；Exp5 8-root identity-only、artifact-backed bundle validator、formal fail-stop、nullable missingness、production renderer/replay 与 PowerShell launcher 通过。未运行 Full 或真实 API，外部 endpoint 在线性与新的 smoke bundle 仍需下一阶段验证。
