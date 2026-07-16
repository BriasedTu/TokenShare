# Phase 6 真实 Lean Proof 插件 Code Map

日期：2026-06-29

状态：`feat-007` 完整实现与审查硬化映射。本文记录真实 Lean proof plugin 的 source、Lean fixture project、测试、协议边界和验证证据。2026-06-29 审查硬化已关闭 `sorry` 接受、proof candidate schema 绑定、split policy / elaboration gate、unsupported intro merge、child proof artifact merge、真实 preflight 和 environment digest 稳定性问题。Phase 6 的 structured report stub 已从开发计划剔除；历史名称只作为 provenance / 通用夹具，不是剩余目标。

## 1. 已实现 source

| 文件 | 对应 TDD 任务 | 职责 |
|---|---|---|
| `src/tokenshare/plugins/lean_proof/__init__.py` | Task 1-15 | 导出真实 Lean 插件公共入口。 |
| `src/tokenshare/plugins/lean_proof/schemas.py` | Task 2 | 固定 `lean_proof@0.1.0`、task type、schema version、output name、validator policy、merge policy、split strategy id。 |
| `src/tokenshare/plugins/lean_proof/models.py` | Task 2 / 3 / 6 / 2026-07-15 feat-011 lemma-DAG v2 / metadata retrofit | `LeanTheoremPayload`、`LeanFixtureManifest`、`LeanSplitCertificate`、`LeanLemmaGraphCertificate` 和 canonical JSON digest；payload digest 覆盖 imports / namespace / options / statement / policy / resource limits；v1 split certificate 校验 child payload digest、rule id、split kind 和 evidence refs；v2 lemma-DAG certificate 记录 `certificate_schema_version`、`topic_family`、`topic_family_version`、`construction_rule_id`、`oracle_package_group`、`proof_assembly_shape`、`root_node_id`、`lemma_nodes`、`dependency_edges`、`merge_nodes`、`environment_digest`、`oracle_proof_package_ref` / digest 和 `certificate_digest`，并拒绝非法 topic family、unsupported proof assembly shape、缺 construction / oracle provenance、structured-blocked frontier 携带 oracle package、缺 root、未知 dependency node、cycle、duplicate node id 和 environment mismatch；metadata 参与 canonical digest。 |
| `src/tokenshare/plugins/lean_proof/descriptor.py` | Task 2 / 8 / 13 | 构造 `PluginDescriptor`，声明真实 checker required、`lean_stub_allowed_as_success=false`、AI 不可决定 decomposition、split strategy、output contracts、execution contracts、AI proof candidate parser policy 和 Phase 8 ready capabilities。 |
| `src/tokenshare/plugins/lean_proof/environment.py` | Task 1 / review hardening | `LeanEnvironmentManifest` 与 `EnvironmentRef` 映射；记录 executable、Lean/lake version、toolchain/lake/helper/import digest、resource limits；`created_at` 作为 manifest 元数据保存但不进入稳定环境 digest。 |
| `src/tokenshare/plugins/lean_proof/preflight.py` | Task 1 | 检查 toolchain / fixture project；缺失时结构化 blocked，不误报 checker success。 |
| `src/tokenshare/plugins/lean_proof/checker.py` | Task 4 / 9 / 10 / review hardening | 调用固定 `lake env lean`，生成临时 Lean source，保存 generated source / stdout / stderr / checker report / accepted proof artifact；child proof 和 merge proof 复用同一真实 checker；proof candidate 必须满足 schema / `theorem_payload_digest` 绑定，`sorry` / `admit` 或 Lean sorry warning 一律 rejected 且不生成 proof artifact。 |
| `src/tokenshare/plugins/lean_proof/validator.py` | Task 5 | 将 `LeanCheckerReport` 映射为插件 domain validation result；accepted 且具备 EnvironmentRef / logs / proof artifact 才能进入 Phase 4 canonical path。 |
| `src/tokenshare/plugins/lean_proof/split_strategy.py` | Task 6 / 7 / review hardening / 2026-07-15 feat-011 lemma-DAG v2 / metadata retrofit / 2026-07-16 proof assembly contract | 调用固定 Lean helper 输出 versioned split certificate，持久化 generated source / stdout / stderr / certificate / report，并把 certificate child goals 映射为 Phase 4 `DecompositionProposal` / `MergePlan`；bridge 会对 supported v1 certificate 重新做 Lean parent elaboration、`decomposition_policy.allowed_rules/max_depth/max_children`、supported merge rule 校验；unsupported certificate 的 report status 为 `unsupported`；显式拒绝 AI / executor output 作为 decomposition authority。2026-07-15 起 `build_lean_split_plan()` 可接收 fixed oracle / deterministic catalog 生成的 `LeanLemmaGraphCertificate` v2，把每个 lemma/proof unit 映射为 `child_specs`，并把 DAG 转为 `DecompositionProposal.dependency_edges`；metadata 透传到 proposal promotion guard、child spec plugin payload、dependency edge plugin payload、required slot `slot_metadata` 和 merge plan plugin payload summary。2026-07-16 起 v2 `MergePlan.required_slots` / proposal expected output / parent output mapping 审计所有 lemma node proof slots，不再只要求 root slot，并声明 `proof_file_assembly_required` 与 `root_merge_proof_checker_required`。 |
| `src/tokenshare/plugins/lean_proof/prompt_builder.py` | Task 8 / review hardening | 构造 proof candidate prompt package 和 plugin-owned parser；raw-only / malformed AI output 只产生 parse failure，不产生权威 proof artifact 或 task graph；AI 输出中含 `sorry` / `admit` proof placeholder 会被 parser 拒绝。 |
| `src/tokenshare/plugins/lean_proof/child_proof.py` | Task 9 | 根据 split certificate 校验 child theorem payload，调用真实 Lean checker 检查 child proof，并输出 merge-ready 证据。 |
| `src/tokenshare/plugins/lean_proof/merge_policy.py` | Task 10 / review hardening / 2026-07-16 feat-011 lemma-DAG assembly | v1 路径校验 all-required child proof refs、环境一致性、merge skeleton；从 accepted child proof artifact 读取 proof source，嵌入 root merge proof 的局部 `have`，再用真实 Lean checker 复验 root merge proof。2026-07-16 新增并在 review 后加固 v2 lemma-DAG `LeanLemmaGraphProofInput` / `LeanLemmaGraphMergeResult` / `merge_lean_lemma_graph_proofs()`：先验证 exact node slot set、duplicate/missing/unexpected proof、slot/node/context/payload/environment digest、required-slot theorem payload metadata、artifact refs、accepted child checker report 和 proof artifact digest；随后按 certificate dependency edges 拓扑排序组装 proof file。无入边 node 使用已接受的 node proof artifact；有入边 node / root 不再直接 exact catalog root oracle proof，而是从 incoming dependency node 的 local theorem 推导，如 pure_logic 的 `P` + `P -> Q`、`Q` + `Q -> R`，function_set 的 intermediate subset，和 induction 的 `∀ n` chain。最后只对 assembled parent theorem payload 执行 `MERGE_PROOF` root recheck；accepted merge result artifact 记录 certificate / merge plan digest、metadata summary、node proof refs、node checker report refs、node proof digest bundle、root checker report ref、root proof candidate/artifact refs 和 root proof digest。 |
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
| `fixtures/lean_proof_project/TokenShare/LemmaGraphCases.lean` | 2026-07-15 feat-011 paper catalog oracle/preflight fixture；定义 `pure_logic` / `function_set` / `induction` 三个 medium lemma-DAG golden case 的 leaf / intermediate / root theorem package，供 fixed oracle package 复用。不是 Lean split helper 新规则。 |
| `fixtures/lean_proof_project/TokenShare/LemmaGraphOracle.lean` | 2026-07-15 feat-011 paper catalog fixed oracle package；导入 `LemmaGraphCases.lean` 并暴露 `pure_logic` / `function_set` / `induction` per-node oracle theorem，供 `paper_catalog.py` 的 checker-backed preflight 构造 proof candidate。不是 Lean plugin merge policy 或 adapter DAG execution。 |
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
| `tests/plugins/lean_proof/test_lean_lemma_graph_certificate.py` | 2026-07-15 feat-011 v2 lemma-DAG certificate schema：合法 certificate 可 parse / serialize / canonical digest；metadata roundtrip 覆盖 `topic_family`、`topic_family_version`、`construction_rule_id`、`oracle_package_group`、`proof_assembly_shape`；改变 `topic_family` 或 `construction_rule_id` 会改变 digest；非法 topic family、unsupported proof assembly shape、缺 construction / oracle provenance、缺 `root_node_id`、dependency edge 引用不存在 node、cycle、duplicate node id 和 environment digest mismatch 均拒绝；structured-blocked frontier metadata 可表达 no-oracle stress 且不携带 oracle package。 |
| `tests/plugins/lean_proof/test_lean_split_strategy.py` | Python split bridge 将 certificate 映射为 `DecompositionProposal` / `MergePlan`，持久化 child theorem payload，拒绝 AI output 作为拆分 authority，并拒绝缺 child payload digest 或超过 parent policy 的证书。2026-07-16 起覆盖 v2 lemma-DAG certificate 生成带 `dependency_edges` 的 proposal、v2 metadata 透传到 child specs / dependency edges / all-node required slots / merge plan payload、proposal 与 parent output mapping 公开所有 `merge_slot_keys`、induction proof assembly shape 可进入 proposal 且声明后续 root recheck requirement、v1 conjunction / iff 继续保持 `dependency_edges=[]`、unsupported theorem shape 继续 structured unsupported。 |
| `tests/plugins/lean_proof/test_lean_prompt_and_parse_policy.py` | plugin-owned proof prompt/parser；raw-only、malformed AI output、`sorry` / `admit` proof placeholder 不产生 canonical candidate。 |
| `tests/plugins/lean_proof/test_lean_child_proof_flow.py` | child theorem payload 与 certificate 绑定、child proof checker accepted/rejected、merge readiness。 |
| `tests/plugins/lean_proof/test_lean_merge_policy.py` | all-required child proof merge policy、environment consistency、root merge proof recheck、bad environment rejection；回归覆盖 root merge proof candidate 必须嵌入 child proof artifact source。 |
| `tests/plugins/lean_proof/test_lean_lemma_graph_merge_policy.py` | 2026-07-16 feat-011 v2 lemma-DAG proof-file assembly / root recheck：用 catalog oracle source 仅作为 proof candidate，先经真实 Lean checker `CHILD_PROOF` accepted，再由 `merge_lean_lemma_graph_proofs()` dependency-aware assembly；覆盖 pure_logic accepted root recheck、function_set / induction fixture path accepted recheck、missing / duplicate / unexpected proof、wrong slot key、wrong context digest、wrong theorem payload digest、wrong required-slot payload metadata、wrong environment digest、rejected checker report、missing proof artifact ref 和 structured-blocked no-oracle frontier rejection。review hardening 还断言 assembled proof 中出现 dependency-derived exact（如 `exact node_leaf_p_to_q node_leaf_p`、`exact node_leaf_q_to_r node_intermediate_q`、function_set intermediate exact、induction per-`n` chain），并断言不再使用 `TokenShare.LemmaGraphOracle.*root*` 作为最终 root 推导。 |
| `tests/plugins/lean_proof/test_lean_replay_evidence.py` | replay guard 不调用 Lean subprocess、缺 checker log artifact 失败、environment digest mismatch 失败、缺 log ref 失败。 |
| `tests/test_phase6_lean_proof_flow.py` | direct proof E2E canonical / complete / settlement，invalid proof 无 canonical 污染，decomposition / child proof / merge / settlement，partial child canonical 阻止 merge，unsupported decomposition 只记录 invalid-result audit。 |
| `tests/experiments/test_phase8_runner_reports.py` | Lean adapter direct proof ready path、decomposition ready path、可注入 blocked preflight，以及默认真实 preflight 在缺 toolchain 时 blocked。 |
| `tests/experiments/test_phase8_default_suite.py` | 默认 Experiment 4 中 Lean direct proof 和 Lean decomposition/merge 使用真实 checker evidence 通过，不再用 blocked / `lean_stub` 替代。 |
| `tests/experiments/test_lean_lemma_graph_catalog.py` | feat-011 paper catalog preflight test；验证 `pure_logic` / `function_set` / `induction` medium lemma-DAG catalog fixture 的 graph shape、oracle package file hash、environment digest、node proof source coverage、per-node checker-backed preflight summary，并覆盖 environment mismatch、oracle hash mismatch 和坏 node proof rejection。该测试覆盖实验题库资产，不表示 Lean plugin split/merge 已支持 recursive DAG。 |

## 4. 验证证据

本轮 TDD red evidence：

- `tests\test_phase6_lean_proof_flow.py -q` 初始失败 4 个：direct proof 复用 root theorem payload lease 导致 `idempotency key conflict`；decomposition E2E rule id 与 Lean helper 单测固定输出不一致；unsupported decomposition 被错误记录为 succeeded split invocation。
- `tests\experiments\test_phase8_runner_reports.py` 新增 Lean ready-path 测试初始失败 3 个：默认 Lean adapter 仍返回 blocked，且不支持 `preflight_ready=False` 注入。
- `tests\plugins\lean_proof\test_lean_replay_evidence.py -q` 初始 collection error：缺少 `tokenshare.plugins.lean_proof.replay_evidence`。
- replay guard 首次绿化前发现测试直接篡改 checker report 文件会先触发 content-hash mismatch；测试改为通过 artifact store 写入 hash 一致但缺 log ref 的坏报告。

审查硬化 RED evidence：

- `tests\plugins\lean_proof\test_lean_checker_direct.py ... tests\experiments\test_phase8_runner_reports.py -q` 初始失败 11 个，覆盖 `by sorry` 被 accepted、缺 `theorem_payload_digest` 未拒绝、parser 接受 `sorry/admit`、unsupported split 仍标记 succeeded、policy 禁用仍拆分、unelaborable parent 仍拆分、forged certificate 超过 `max_children` 仍建 plan、merge proof 未嵌入 child proof source、environment digest 受 `created_at` 影响、Lean adapter 构造不支持真实 preflight 路径。

2026-07-16 feat-011 Lean lemma-DAG proof-file assembly / root recheck evidence：

- 归档说明：当前 live path `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md` 不存在；实际 code map 位于本归档路径，因此本轮继续更新归档 code map。本轮只实现 Lean plugin 层 dependency-aware proof assembly / merge/root recheck，不修改 `tokenshare.core`、`SplitRules.lean`、paper adapter DAG execution、`AIAPIExecutor`、catalog manifest 或 runner full 3×3 matrix。
- Split plan contract：v2 `MergePlan.required_slots`、proposal `expected_outputs[].merge_slot_keys` 和 `parent_output_mapping[].merge_slot_keys` 现在覆盖 certificate 中所有 lemma node proof slots；required slot metadata 带 `node_id`、`root_node_id`、`context_digest`、`theorem_payload_digest`、topic / construction / oracle / proof assembly metadata 和 provenance。
- Merge implementation：新增 `LeanLemmaGraphProofInput`、`LeanLemmaGraphMergeResult` 和 `merge_lean_lemma_graph_proofs()`；每个 node proof 必须先有真实 Lean checker `CHILD_PROOF` accepted evidence 和 proof artifact ref，catalog oracle source 或 AI self-report 不能代替 checker evidence；root success 只来自 assembled root proof candidate 经 `check_lean_proof(..., MERGE_PROOF)` accepted。
- Integrity checks：duplicate / missing / unexpected node proof、wrong slot key、wrong context digest、wrong theorem payload digest、wrong required-slot theorem payload metadata、wrong checker environment digest、rejected checker report、missing proof artifact ref 和 `structured_blocked_no_oracle_frontier_stress.v1` 均拒绝 merge。
- RED targeted：`$env:PYTHONPATH='src'; conda run --no-capture-output -n tokenshare python -m pytest tests\plugins\lean_proof\test_lean_lemma_graph_merge_policy.py tests\plugins\lean_proof\test_lean_split_strategy.py -q` 失败 `16 failed, 8 passed`，失败点为缺 `LeanLemmaGraphProofInput` / `merge_lean_lemma_graph_proofs()`、v2 merge plan 仍 root-only、payload 仍带 `proof_file_assembly_not_implemented`。
- Hardening RED：新增 `test_wrong_required_slot_theorem_payload_digest_blocks_lemma_graph_merge` 后，单测失败 `DID NOT RAISE`，确认 merge policy 尚未校验 required slot metadata 的 `theorem_payload_digest`。
- GREEN targeted：`$env:PYTHONPATH='src'; conda run --no-capture-output -n tokenshare python -m pytest tests\plugins\lean_proof\test_lean_lemma_graph_merge_policy.py tests\plugins\lean_proof\test_lean_split_strategy.py -q` 通过，结果 `25 passed in 18.11s`。
- Lean plugin impact：`$env:PYTHONPATH='src'; conda run --no-capture-output -n tokenshare python -m pytest tests\plugins\lean_proof -q` 通过，结果 `78 passed in 47.59s`。
- Compile verification：`$env:PYTHONPATH='src'; conda run --no-capture-output -n tokenshare python -m compileall -x "reference_repos" src tests` 退出码 0。
- Whitespace check：`git diff --check` 退出码 0，仅有既有 LF/CRLF warning。
- Full startup verification：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 515 items，结果 `514 passed, 1 skipped in 206.57s`。
- 剩余边界：paper adapter DAG execution、真实 provider proof attempts、paper metrics/report/CSV、正式 Experiment 1-5 real API runs 和 hard/frontier oracle-backed 扩展仍未实现。

2026-07-15 feat-011 paper catalog medium lemma-DAG preflight evidence：

- 归档说明：当前 live path `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md` 不存在；实际 code map 位于本归档路径，因此本轮只更新归档 code map。新增 Lean fixture 只服务 paper catalog oracle/preflight，不修改 `SplitRules.lean`、`merge_policy.py`、Lean split helper 或 adapter DAG execution。
- RED `tests\experiments\test_lean_lemma_graph_catalog.py -q` 首次失败 4 项：medium root 仍是 shallow `P /\ Q`、environment digest mismatch 未拒绝、oracle package hash mismatch 未拒绝、坏 node oracle proof 未拒绝。
- GREEN targeted `tests\experiments\test_lean_lemma_graph_catalog.py -q` 通过，结果 `4 passed in 21.17s`。
- Paper catalog impact `tests\experiments\test_paper_catalog.py -q` 通过，结果 `12 passed in 39.20s`。
- Lean plugin impact `tests\plugins\lean_proof -q` 通过，结果 `45 passed in 34.59s`，确认新增 fixture 未改变现有 conjunction / iff split helper、checker、child proof、merge policy 和 replay evidence 行为。
- `conda run --no-capture-output -n tokenshare python -m compileall -x "reference_repos" src tests` 退出码 0。
- 完整启动验证 `powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 458 items，结果 `457 passed, 1 skipped in 196.17s`。

2026-07-15 feat-011 Lean plugin lemma-DAG certificate v2 / split proposal evidence：

- 归档说明：当前 live path `Doc/TechnicalDocument/2026-06-29-phase-6-lean-real-plugin-code-map.md` 不存在；实际 code map 位于本归档路径，因此本轮继续更新归档 code map。新增能力只扩展 Lean plugin data model 与 split proposal mapping，不修改 `tokenshare.core`、`SplitRules.lean`、`merge_policy.py` proof assembly、paper adapter 或 Task 7/8/9 fault / worker / ablation 基础设施。
- Baseline：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof -q` 在写新测试前通过，结果 `45 passed in 33.81s`。
- RED：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof\test_lean_lemma_graph_certificate.py tests\plugins\lean_proof\test_lean_split_strategy.py -q` 失败于无法从 `tokenshare.plugins.lean_proof.models` 导入 `LeanLemmaGraphCertificate`。
- GREEN targeted：同一 targeted 命令通过，结果 `14 passed in 8.98s`。
- Lean plugin impact：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof -q` 通过，结果 `54 passed in 37.67s`。
- Compile verification：`conda run -n tokenshare python -m compileall -x "reference_repos" src tests` 退出码 0。
- Whitespace check：`git diff --check` 退出码 0，仅有既有 LF/CRLF warning。
- 剩余边界：v2 certificate / proposal 现在可表达 recursive lemma-DAG 和 dependency edges，但 formal medium Lean paper claims 仍需要 multi-level Lean proof-file assembly、paper adapter DAG execution、真实 provider proof attempts、metrics/report/CSV 和正式 Experiment 1-5 real API runs。

2026-07-15 feat-011 LeanLemmaGraphCertificate metadata retrofit evidence：

- 归档说明：本轮只做 Lean plugin v2 certificate / proposal metadata 透传，不修改 `tokenshare.core`、`SplitRules.lean`、`merge_policy.py` proof assembly、paper adapter DAG execution、`AIAPIExecutor`、catalog cases 或 runner full 3×3 matrix。
- `LeanLemmaGraphCertificate` v2 新增并校验 `topic_family`、`topic_family_version`、`construction_rule_id`、`oracle_package_group`、`proof_assembly_shape`；topic family 只允许 `pure_logic` / `function_set` / `induction`；proof assembly shape 支持 `recursive_lemma_dag_required_slots.v1`、`recursive_induction_lemma_dag_required_slots.v1` 和 `structured_blocked_no_oracle_frontier_stress.v1`。
- Structured blocked frontier metadata 可表达 no-oracle stress，但若携带 `oracle_proof_package_ref` 会被拒绝；缺 `construction_rule_id` / `oracle_package_group`、非法 topic family、unsupported proof assembly shape 均被拒绝。
- Canonical certificate digest 覆盖新增 metadata；测试确认改变 `topic_family` 或 `construction_rule_id` 会改变 digest。
- `build_lean_split_plan()` v2 metadata 透传位置：`DecompositionProposal.promotion_guard_evidence`、`child_specs[].plugin_payload.summary/provenance`、`dependency_edges[].plugin_payload.summary/provenance`、root required slot `slot_metadata`、`MergePlan.plugin_payload.plugin_defined_body.summary`。
- Regression：v1 conjunction / iff split 继续保持 `dependency_edges=[]`；AI output 仍通过 `executor_decomposition_authority_ref` 拒绝；unsupported theorem shape 仍 structured unsupported。
- RED targeted：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof\test_lean_lemma_graph_certificate.py tests\plugins\lean_proof\test_lean_split_strategy.py -q` 失败 `12 failed, 12 passed`，失败点为缺 metadata 字段 / proposal payload propagation。
- GREEN targeted：同一 targeted 命令通过，结果 `24 passed in 9.51s`。
- Lean plugin impact：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof -q` 通过，结果 `64 passed in 42.70s`。
- Compile verification：`conda run -n tokenshare python -m compileall -x "reference_repos" src tests` 退出码 0。
- Whitespace check：`git diff --check` 退出码 0，仅有既有 LF/CRLF warning。
- Post-status verification：`feature-list-json-ok`；targeted Lean certificate/split tests 通过 `24 passed in 9.66s`；Lean plugin impact suite 通过 `64 passed in 41.40s`；`compileall -x "reference_repos" src tests` 退出码 0；`git diff --check` 退出码 0，仅有既有 LF/CRLF warning；完整启动验证 `powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，pytest collected 501 items，结果 `500 passed, 1 skipped in 215.33s`。
- 剩余边界：proof-file assembly、paper adapter DAG execution、真实 provider proof attempts、paper metrics/report/CSV 和正式 Experiment 1-5 real API runs 仍未实现。

2026-07-15 feat-011 first topic-family golden cases evidence：

- 归档说明：本轮只扩展 paper catalog oracle/preflight fixture 和 loader 支持，不修改 `tokenshare.core`、`AIAPIExecutor`、factorization plugin、`SplitRules.lean`、Lean plugin split/merge、proof-file assembly、paper adapter DAG execution 或 runner full 3×3 matrix。
- 新增 `function_set / medium_lemma_dag` case：`lean_v2_medium_function_set_dx_subset_chain_01`，本地 predicate set `D/E/F` subset chain，`expected_depth=3`、`expected_leaf_count=2`、`expected_ai_unit_count=4`。
- 新增 `induction / medium_lemma_dag` case：`lean_v2_medium_induction_nat_predicate_chain_01`，Nat predicate induction + `P -> Q -> R` chain，`expected_depth=3`、`expected_leaf_count=3`、`expected_ai_unit_count=5`。该 row 是 oracle-preflight golden；adapter DAG execution / induction split-merge 未实现前仍不能 paper-runnable。
- Oracle package hash：`sha256:4af791228bd24f4ea6d92fed511be8eee4f29d1ba8db5c6102968e743a8047ca`；checker environment digest：`sha256:cdc9de4cb0a8407f17ddd7cf423a3fb5640a4560c3b55bd6a70116f5a14de731`。
- RED targeted `tests\experiments\test_lean_lemma_graph_catalog.py tests\experiments\test_paper_catalog.py -q` 失败 11 项，失败点集中在缺 `function_set` / `induction` medium rows、topic counts 和 topic-filtered selection。
- GREEN targeted 同一命令通过，结果 `33 passed in 89.91s`。
- Budget regression：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\experiments\test_paper_budget.py -q` 通过，结果 `5 passed in 54.06s`。
- Lean plugin impact：`$env:PYTHONPATH='src'; conda run -n tokenshare python -m pytest tests\plugins\lean_proof -q` 通过，结果 `54 passed in 73.41s`。
- Compile verification：`conda run -n tokenshare python -m compileall -x "reference_repos" src tests` 退出码 0。
- Full startup verification：`powershell -ExecutionPolicy Bypass -File .\init.ps1` 通过，输出 `python-json-sqlite-ok`、`harness-files-ok`，pytest collected 491 items，结果 `490 passed, 1 skipped in 330.70s`。

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
- 2026-07-15 起，`LeanLemmaGraphCertificate` v2 可作为 fixed oracle package / deterministic catalog rule 产物进入 Lean plugin split bridge；它仍不是 AI output，且只表达 lemma-DAG scheduling/proposal contract，不绕过后续 per-node checker evidence。
- v2 certificate 的 topic/provenance/proof-assembly metadata 只停留在 Lean plugin payload / experiment catalog 层；`tokenshare.core` 不认识 `topic_family`、`construction_rule_id` 或 `proof_assembly_shape`。
- Direct proof、child proof 和 merge proof 都通过固定本地 Lean checker 复验，checker report / logs / proof artifact / EnvironmentRef 持久化；`sorry` / `admit` 不允许成为 accepted proof。
- Replay evidence guard 只读取 artifact 和 digest，不调用 Lean / lake / executor / AI。
- 2026-07-15 新增的 `LemmaGraphCases.lean` / `LemmaGraphOracle.lean` 是 paper catalog oracle/preflight fixture；它们不改变 Lean plugin 的 deterministic split certificate authority，也不让实验层绕过 checker。
- 2026-07-16 新增且 review-hardening 后的 lemma-DAG proof assembly 仍在 Lean plugin 层：catalog oracle source 只能作为 proof candidate，node proof 必须先经 `CHILD_PROOF` checker accepted；root success 只来自 dependency-edge assembled proof candidate 经 `MERGE_PROOF` checker accepted。对于有 incoming dependency 的 intermediate/root node，merge 不再直接使用该 node 自己的 oracle proof source，而是根据 dependency edges 从已命名的 incoming theorem local values 合成 proof body。Replay 只记录本次 checker evidence refs 和 digest，不重新调用 Lean 补事实。

## 6. 剩余边界

`feat-007` 的 TDD Task 1-15 已实现并通过 targeted 组合验证，2026-06-29 审查硬化已关闭本文件顶部列出的安全和一致性缺口。V1 仍不包含生产级 theorem-proving 平台、LeanDojo 训练/检索、动态 Lean 服务、真实分布式 executor 网络或真实链上结算。Lean split helper 当前只对 conjunction / iff 产出可执行 merge plan；implication / forall 等未接入 verified merge 的规则以 structured `unsupported_decomposition` 审计返回，不作为失败的假阳性成功。任意 theorem 的不可覆盖情况同样返回 structured unsupported。2026-07-15 的 medium lemma-DAG oracle/preflight fixture 已证明 `pure_logic`、`function_set`、`induction` 三个 topic-family golden cases 可由固定 Lean checker 验证；同日新增的 v2 lemma-DAG certificate / proposal mapping 已能把 fixed oracle / deterministic catalog rule 的 graph 转成 `DecompositionProposal.child_specs` 与 `dependency_edges`，并可透传 topic/provenance/proof-assembly metadata。2026-07-16 Lean plugin 层已实现并加固 dependency-aware proof-file assembly / root `MERGE_PROOF` recheck，root proof 现在从 dependency edges 推导而不是 exact root oracle；仍未实现正式 Lean medium real-AI run、paper metrics/report/CSV 或正式 Experiment 1-5 real API runs。
