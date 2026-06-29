# Phase 6 真实 Lean Proof 插件 Code Map

日期：2026-06-29

状态：`feat-007` 完整实现与审查硬化映射。本文记录真实 Lean proof plugin 的 source、Lean fixture project、测试、协议边界和验证证据。2026-06-29 审查硬化已关闭 `sorry` 接受、proof candidate schema 绑定、split policy / elaboration gate、unsupported intro merge、child proof artifact merge、真实 preflight 和 environment digest 稳定性问题。Phase 6 的 structured report stub 已从开发计划剔除；历史名称只作为 provenance / 通用夹具，不是剩余目标。

## 1. 已实现 source

| 文件 | 对应 TDD 任务 | 职责 |
|---|---|---|
| `src/tokenshare/plugins/lean_proof/__init__.py` | Task 1-15 | 导出真实 Lean 插件公共入口。 |
| `src/tokenshare/plugins/lean_proof/schemas.py` | Task 2 | 固定 `lean_proof@0.1.0`、task type、schema version、output name、validator policy、merge policy、split strategy id。 |
| `src/tokenshare/plugins/lean_proof/models.py` | Task 2 / 3 / 6 | `LeanTheoremPayload`、`LeanFixtureManifest`、`LeanSplitCertificate` 和 canonical JSON digest；payload digest 覆盖 imports / namespace / options / statement / policy / resource limits；split certificate 校验 child payload digest、rule id、split kind 和 evidence refs。 |
| `src/tokenshare/plugins/lean_proof/descriptor.py` | Task 2 / 8 / 13 | 构造 `PluginDescriptor`，声明真实 checker required、`lean_stub_allowed_as_success=false`、AI 不可决定 decomposition、split strategy、output contracts、execution contracts、AI proof candidate parser policy 和 Phase 8 ready capabilities。 |
| `src/tokenshare/plugins/lean_proof/environment.py` | Task 1 / review hardening | `LeanEnvironmentManifest` 与 `EnvironmentRef` 映射；记录 executable、Lean/lake version、toolchain/lake/helper/import digest、resource limits；`created_at` 作为 manifest 元数据保存但不进入稳定环境 digest。 |
| `src/tokenshare/plugins/lean_proof/preflight.py` | Task 1 | 检查 toolchain / fixture project；缺失时结构化 blocked，不误报 checker success。 |
| `src/tokenshare/plugins/lean_proof/checker.py` | Task 4 / 9 / 10 / review hardening | 调用固定 `lake env lean`，生成临时 Lean source，保存 generated source / stdout / stderr / checker report / accepted proof artifact；child proof 和 merge proof 复用同一真实 checker；proof candidate 必须满足 schema / `theorem_payload_digest` 绑定，`sorry` / `admit` 或 Lean sorry warning 一律 rejected 且不生成 proof artifact。 |
| `src/tokenshare/plugins/lean_proof/validator.py` | Task 5 | 将 `LeanCheckerReport` 映射为插件 domain validation result；accepted 且具备 EnvironmentRef / logs / proof artifact 才能进入 Phase 4 canonical path。 |
| `src/tokenshare/plugins/lean_proof/split_strategy.py` | Task 6 / 7 / review hardening | 调用固定 Lean helper 输出 versioned split certificate，持久化 generated source / stdout / stderr / certificate / report，并把 certificate child goals 映射为 Phase 4 `DecompositionProposal` / `MergePlan`；bridge 会对 supported certificate 重新做 Lean parent elaboration、`decomposition_policy.allowed_rules/max_depth/max_children`、supported merge rule 校验；unsupported certificate 的 report status 为 `unsupported`；显式拒绝 AI / executor output 作为 decomposition authority。 |
| `src/tokenshare/plugins/lean_proof/prompt_builder.py` | Task 8 / review hardening | 构造 proof candidate prompt package 和 plugin-owned parser；raw-only / malformed AI output 只产生 parse failure，不产生权威 proof artifact 或 task graph；AI 输出中含 `sorry` / `admit` proof placeholder 会被 parser 拒绝。 |
| `src/tokenshare/plugins/lean_proof/child_proof.py` | Task 9 | 根据 split certificate 校验 child theorem payload，调用真实 Lean checker 检查 child proof，并输出 merge-ready 证据。 |
| `src/tokenshare/plugins/lean_proof/merge_policy.py` | Task 10 / review hardening | 校验 all-required child proof refs、环境一致性、merge skeleton；从 accepted child proof artifact 读取 proof source，嵌入 root merge proof 的局部 `have`，再用真实 Lean checker 复验 root merge proof。 |
| `src/tokenshare/plugins/lean_proof/fixtures.py` | Task 3 / 11 / 12 | 固定 fixture project 路径和 manifest；实现 direct proof、invalid proof、decomposition / child proof / merge proof / settlement、partial child canonical gate、unsupported decomposition 的协议 E2E fixture。 |
| `src/tokenshare/plugins/lean_proof/replay_evidence.py` | Task 14 | Replay-time 只读 checker report / logs / proof artifact / EnvironmentRef，不调用 Lean subprocess；检测缺失 checker log、artifact hash mismatch、environment digest mismatch。 |
| `src/tokenshare/experiments/lean_adapter.py` | Task 13 / review hardening | Phase 8 ready path：默认先运行真实 Lean preflight，再运行真实 Lean direct proof 与 decomposition/merge fixtures；保留可注入 blocked preflight；将 checker evidence、environment manifest、event log 和 artifacts 写入实验输出。 |

## 2. Lean fixture project

| 路径 | 职责 |
|---|---|
| `fixtures/lean_proof_project/lean-toolchain` | 固定 `leanprover/lean4:v4.8.0`。 |
| `fixtures/lean_proof_project/lakefile.lean` | 固定 lake package `tokenshare_lean` 和 default `TokenShare` library。 |
| `fixtures/lean_proof_project/TokenShare.lean` | Lean library 入口，导入 helper 与 fixtures。 |
| `fixtures/lean_proof_project/TokenShare/Helper.lean` | helper project 入口，导入 split rules / merge policy，并暴露 split helper 输出函数。 |
| `fixtures/lean_proof_project/TokenShare/SplitRules.lean` | deterministic split certificate helper；当前可生成可 merge 的 conjunction / iff certificate；implication intro / forall intro 在未实现对应 verified merge 前输出 `unsupported_merge_rule`，其他不可覆盖形状输出 `unsupported_goal_shape`，均为 `lean_proof.split_certificate.v1` JSON。 |
| `fixtures/lean_proof_project/TokenShare/Merge.lean` | 固定 merge policy id 和 merge helper 入口。 |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Direct.lean` | direct proof fixture theorem。 |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Decomposition.lean` | decomposition / merge fixture theorem。 |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Unsupported.lean` | unsupported decomposition fixture theorem。 |
| `fixtures/lean_proof_project/TokenShare/Fixtures/Invalid.lean` | invalid proof target fixture。 |

## 3. Tests

| 测试文件 | 覆盖 |
|---|---|
| `tests/plugins/lean_proof/test_lean_preflight.py` | missing toolchain blocked gate、EnvironmentRef 字段/digest、environment digest mismatch。 |
| `tests/plugins/lean_proof/test_lean_descriptor_and_schemas.py` | descriptor、schema refs、structured theorem payload、payload digest、split/validator/merge policy metadata。 |
| `tests/plugins/lean_proof/test_lean_fixture_project_manifest.py` | fixture project 文件存在、fixture manifest、helper source digest drift。 |
| `tests/plugins/lean_proof/test_lean_checker_direct.py` | 真实 Lean direct proof accepted / invalid proof rejected，checker logs 和 proof artifacts 持久化；回归覆盖 `by sorry` 即使 Lean exit code 0 也 rejected，以及 proof candidate schema / payload digest 必填。 |
| `tests/plugins/lean_proof/test_lean_validator.py` | checker accepted/rejected 到 Phase 4 plugin domain check 的映射，缺环境/log/proof evidence rejected。 |
| `tests/plugins/lean_proof/test_lean_environment.py` | Environment digest 不随 manifest `created_at` 变化；`created_at` 仍保留在 manifest body 中。 |
| `tests/plugins/lean_proof/test_lean_split_helper.py` | Lean-side split helper 输出 conjunction / iff / unsupported 的 versioned JSON certificate；回归覆盖 unsupported status、policy disallow、parent elaboration failure，以及 intro/forall 未实现 verified merge 时不生成 supported split。 |
| `tests/plugins/lean_proof/test_lean_split_strategy.py` | Python split bridge 将 certificate 映射为 `DecompositionProposal` / `MergePlan`，持久化 child theorem payload，拒绝 AI output 作为拆分 authority，并拒绝缺 child payload digest 或超过 parent policy 的证书。 |
| `tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py` | plugin-owned proof prompt/parser；raw-only、malformed AI output、`sorry` / `admit` proof placeholder 不产生 canonical candidate。 |
| `tests/plugins/lean_proof/test_lean_child_proof_flow.py` | child theorem payload 与 certificate 绑定、child proof checker accepted/rejected、merge readiness。 |
| `tests/plugins/lean_proof/test_lean_merge_policy.py` | all-required child proof merge policy、environment consistency、root merge proof recheck、bad environment rejection；回归覆盖 root merge proof candidate 必须嵌入 child proof artifact source。 |
| `tests/plugins/lean_proof/test_lean_replay_evidence.py` | replay guard 不调用 Lean subprocess、缺 checker log artifact 失败、environment digest mismatch 失败、缺 log ref 失败。 |
| `tests/test_phase6_lean_proof_flow.py` | direct proof E2E canonical / complete / settlement，invalid proof 无 canonical 污染，decomposition / child proof / merge / settlement，partial child canonical 阻止 merge，unsupported decomposition 只记录 invalid-result audit。 |
| `tests/experiments/test_phase8_runner_reports.py` | Lean adapter direct proof ready path、decomposition ready path、可注入 blocked preflight，以及默认真实 preflight 在缺 toolchain 时 blocked。 |
| `tests/experiments/test_phase8_default_suite.py` | 默认 Experiment 4 中 Lean direct proof 和 Lean decomposition/merge 使用真实 checker evidence 通过，不再用 blocked / `lean_stub` 替代。 |

## 4. 验证证据

本轮 TDD red evidence：

- `tests\test_phase6_lean_proof_flow.py -q` 初始失败 4 个：direct proof 复用 root theorem payload lease 导致 `idempotency key conflict`；decomposition E2E rule id 与 Lean helper 单测固定输出不一致；unsupported decomposition 被错误记录为 succeeded split invocation。
- `tests\experiments\test_phase8_runner_reports.py` 新增 Lean ready-path 测试初始失败 3 个：默认 Lean adapter 仍返回 blocked，且不支持 `preflight_ready=False` 注入。
- `tests\plugins\lean_proof\test_lean_replay_evidence.py -q` 初始 collection error：缺少 `tokenshare.plugins.lean_proof.replay_evidence`。
- replay guard 首次绿化前发现测试直接篡改 checker report 文件会先触发 content-hash mismatch；测试改为通过 artifact store 写入 hash 一致但缺 log ref 的坏报告。

审查硬化 RED evidence：

- `tests\plugins\lean_proof\test_lean_checker_direct.py ... tests\experiments\test_phase8_runner_reports.py -q` 初始失败 11 个，覆盖 `by sorry` 被 accepted、缺 `theorem_payload_digest` 未拒绝、parser 接受 `sorry/admit`、unsupported split 仍标记 succeeded、policy 禁用仍拆分、unelaborable parent 仍拆分、forged certificate 超过 `max_children` 仍建 plan、merge proof 未嵌入 child proof source、environment digest 受 `created_at` 影响、Lean adapter 构造不支持真实 preflight 路径。

本轮 green evidence：

```text
$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\test_phase6_lean_proof_flow.py -q
5 passed in 8.96s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\plugins\lean_proof tests\test_phase6_lean_proof_flow.py -q
39 passed in 37.74s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\experiments\test_phase8_runner_reports.py::test_runner_runs_lean_direct_proof_with_real_checker_evidence tests\experiments\test_phase8_runner_reports.py::test_runner_runs_lean_decomposition_merge_with_lifecycle_coverage tests\experiments\test_phase8_runner_reports.py::test_runner_can_still_report_lean_blocked_when_preflight_is_injected -q
3 passed in 6.83s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\experiments -q
12 passed in 25.10s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\plugins\lean_proof\test_lean_replay_evidence.py -q
4 passed in 3.51s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\plugins\lean_proof tests\test_phase6_lean_proof_flow.py tests\experiments -q
55 passed in 65.42s

$env:PYTHONPATH='src'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\plugins\lean_proof\test_lean_checker_direct.py tests\plugins\lean_proof\test_lean_prompt_and_parse_policy.py tests\plugins\lean_proof\test_lean_split_helper.py tests\plugins\lean_proof\test_lean_split_strategy.py tests\plugins\lean_proof\test_lean_merge_policy.py tests\plugins\lean_proof\test_lean_environment.py tests\experiments\test_phase8_runner_reports.py -q
33 passed in 46.44s

$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; C:\Users\32133\anaconda3\envs\tokenshare\python.exe -m pytest tests\plugins\lean_proof tests\test_phase6_lean_proof_flow.py tests\experiments -q
63 passed in 81.67s

.\init.ps1
363 passed, 1 skipped in 111.49s
```

真实 Lean toolchain / fixture project evidence：

```text
Lean (version 4.8.0, x86_64-w64-windows-gnu, commit df668f00e6c0, Release)
Lake version 5.0.0-df668f0 (Lean version 4.8.0)
Build completed successfully.
```

## 5. 协议边界

- Lean 领域规则保留在 `tokenshare.plugins.lean_proof`，没有硬编码进 `tokenshare.core`。
- Python 侧不做 Lean theorem 语义解析，只处理结构化 payload、artifact、checker/helper bridge 和协议编排。
- AI / executor 输出不能定义 `DecompositionProposal`、`MergePlan`、canonical output 或 task graph；deterministic split certificate 来自固定 Lean helper，并由 Python bridge 复核 policy、elaboration 和 merge-rule 支持边界。
- Direct proof、child proof 和 merge proof 都通过固定本地 Lean checker 复验，checker report / logs / proof artifact / EnvironmentRef 持久化；`sorry` / `admit` 不允许成为 accepted proof。
- Replay evidence guard 只读取 artifact 和 digest，不调用 Lean / lake / executor / AI。

## 6. 剩余边界

`feat-007` 的 TDD Task 1-15 已实现并通过 targeted 组合验证，2026-06-29 审查硬化已关闭本文件顶部列出的安全和一致性缺口。V1 仍不包含生产级 theorem-proving 平台、LeanDojo 训练/检索、动态 Lean 服务、真实分布式 executor 网络或真实链上结算。Lean split helper 当前只对 conjunction / iff 产出可执行 merge plan；implication / forall 等未接入 verified merge 的规则以 structured `unsupported_decomposition` 审计返回，不作为失败的假阳性成功。任意 theorem 的不可覆盖情况同样返回 structured unsupported。
