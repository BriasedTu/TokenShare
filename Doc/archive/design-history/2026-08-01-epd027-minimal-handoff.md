# EPD-027 最小交接摘要（2026-08-04）

> 本文件是覆盖式、可恢复的当前状态。历史 accepted 过程由 git history 保留；不要重新读取旧监督任务、session JSONL、子智能体对话或完整测试日志。

## 身份与状态

- 唯一工作树：`C:\Users\32133\.config\superpowers\worktrees\TokenShare\codex-feat-011-epd027-pipeline`。
- 分支：`codex/feat-011-epd027-pipeline`。
- Task 28 代码提交：`7338f177`（`feat(experiments): split execution and publication gates`；状态同步提交位于其后）。
- Active feature：`feat-011`，仍为 `in-progress`。
- Task 0–28 已 accepted（`29/35`）；Task 28 为 `accepted`，active writer=`0`。
- 不得重做 Task 0–27；不得提前进入 Task 29。
- 正式实验矩阵保持 **NO-GO**。
- Task 26 只完成 paid-receipt validator；当前没有用户提供且经校验的真实 paid receipt。
- Task 28 provider/network calls=`0/0`；fresh canonical=`87 passed, 0 failed/error in 0.71s`，`py_compile`/import-cycle/diff/trailing-whitespace/allowlist 均 exit `0`（`4.97s`），follow-up reviewer=`PASS 0C/0I`（I1/I2/I3 closed），post-min=`0删除/0合并/0 unused helper`且零修改、无需复跑。Fast/Full/LeanAudit 未运行。

## Task 27 已接受边界

- Task 27 的全部 plan-out 批准记录保留在下文；最终代码提交精确包含 27 个批准 code/test 文件并同步 code map，没有 Task 28、协议核心或 provider 配置越界。
- 已闭合 14 command production binding、native Factor/Lean runner/protected replay、authoritative/metric-view identity、multi-entry/failed/resume/zero-dispatch fixed denominator、response-bank acquisition、`496+20<=516` ledger、online callback/provider failure/crash-reconcile 与 transient secret 生命周期。
- 最终综合 reviewer 初轮仅余 `0C/1I`：正式磁盘预检的 `PaperInfrastructureBlockedError` 会穿透统一 CLI。精确捕获后输出单行 blocked JSON/exit `3`，follow-up PASS；非领域异常未被吞掉。
- Task 27 两个 gate subcommand 仅完成 parser/service wiring；execution/publication policy 严格留给 Task 28。

## Task 28 accepted

- 硬边界只有 4 文件：新增 `src/tokenshare/experiments/paper_formal_gate.py`、`tests/experiments/test_paper_formal_gate.py`；修改 `src/tokenshare/experiments/run_paper_pipeline.py`、`tests/experiments/test_run_paper_pipeline.py`。两次独立只读预检均判定 sufficient/minimal，无 PLAN_OUT，实际提交严格符合该边界。
- Task 28 已完成 receipt selected-experiment scope mapping 修复与 typed gate policy/pipeline 接线。fresh canonical=`87 passed, 0 failed/error in 0.71s`；非测试检查（`py_compile`、import-cycle、diff、trailing-whitespace、allowlist）全部 exit `0`（`4.97s`）；follow-up review=`PASS 0C/0I`，I1/I2/I3 closed。
- 冻结约束保持：gate 只消费 typed authority，不铸造 marker、不读 secret；execution gate 不依赖未来 L4/tables/post-bank；publication gate 从 protected replay inputs 调用公开 Exp2 projector；缺正式 receipt/L1–L4/bank/terminal 时稳定 BLOCKED；capability/facility 不升级 formal publication PASS。既有 Minor deferred 为 facility execution predicate 细节，以及 `run_paper_pipeline` gate stage 仍标 `offline_gate_parser_only`。
- post-accept test minimization=`0删除/0合并/0 unused helper`，零修改且无需复跑。remaining active writers=`0`；provider/network calls=`0/0`；无经 Task 26 校验的真实 paid receipt，正式矩阵维持 **NO-GO**。下一动作仅由监督者只读 Task 29 段落并冻结，尚未进入 Task 29。

### Task 27 第七文件计划外批准（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD-027 Task 27。
- `approved_files`：仅 `src/tokenshare/experiments/paper_formal_metrics.py`。
- `approval_reason`：正式 capturing E2E 的 persisted task/runtime/event/artifact lineage 已通过后，canonical Exp1 projector 仍因 authoritative direct row 使用 `exp1_real_ai_feasibility`、metric projector 只接受 `experiment_1` 而失败；当前 official metrics ABI 把同一 rows 同时用于 lineage lookup 与 metric projection。
- `impossible_without_change`：现有六文件只能改写 authoritative experiment identity、放宽 lineage/projector，或在 runner/CLI 复制 Task 20 metrics pipeline；三者都会破坏权威 evidence 或形成 shadow metrics system，无法合法跑通正常正式 metrics/replay 路径。
- `evidence`：最新 RED 为 `tests/experiments/test_run_paper_experiments_cli.py::test_paper_cli_formal_capturing_e2e_writes_all_tables_without_real_usage`，`1 failed in 17.49s`，异常 `ValueError: Exp1 projector only accepts experiment_1`；独立只读 scope audit 核对 `paper_formal_runner.py`、`paper_formal_metrics.py`、`paper_exp1_metrics.py`、`paper_formal_evidence.py` 与 `paper_traceability.py` 调用链后结论为 `APPROVE`。
- `minimum_scope`：只给 official metrics ABI 增加 kw-only separate metric-projection mapping（或同等最小接口）及严格等价校验；lineage/source-index 始终使用原 authoritative rows，registry 只消费 projection rows；runner 只从 official direct rows 确定性投影并传入。
- `forbidden_adjacent_changes`：不得修改 `_group_key`、official experiment identity、loader、lineage、eligibility、inventory/ledger/artifact schema、projector 算术、分母、题量、模型/profile、Task 28 gate、安全工程或 provider 路径；本批准不包含 `paper_traceability.py`、额外测试文件或任何第八文件。
- `verification_required`：定向重跑当前 failing E2E、受影响 protected loader/replay nodeid、既有 `test_paper_formal_metrics.py` 兼容性测试与一次 Task 27 canonical；provider/network calls 必须为 `0/0`。

### Task 27 第八文件计划外批准（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD-027 Task 27 / reviewer C2（兼顾 I4 missingness closure）。
- `approved_files`：仅 `src/tokenshare/experiments/paper_traceability.py`；配套测试只能追加到已批准的 `tests/experiments/test_paper_formal_runner.py`。
- `approval_reason`：official direct evidence contract 要求 `real_model_trace_protocol_run` 的 current-provider refs 为空并由 external source locator/trace resource book 供证，但 protected persist 当前无条件要求 current-provider snapshots 非空，合法 pure-trace closure 因而必然被拒。
- `impossible_without_change`：现有七文件只能伪造 current-provider snapshot、把 trace 错标 online、跳过 official persist/loader 或复制 traceability store；都会违反 evidence-class 互斥契约或形成 shadow system，无法合法跑通纯 Exp2–4 正常路径。
- `evidence`：独立只读 scope audit 核对 `paper_direct_results.py:583-610` 与 `paper_traceability.py:382-450,515-529`，确认 direct contract 与 persist 非空断言矛盾，且 external locator/current-evidence/digest 校验可继续完整保留。
- `minimum_scope`：只条件化 current-provider object closure：snapshots 非空时 `provided_files` keys 必须 exact-match 并执行既有逐项验证；snapshots 为空时 `provided_files` 必须严格为空，任何额外文件 fail closed。descriptor schema 不变。
- `forbidden_adjacent_changes`：不得允许 trace 携带 current-provider refs，不得允许 online 省略 provider objects；不得修改 source locator、trace resource book、current evidence、digest/type、lineage、paper eligibility、metrics、实验口径、Task 28、安全工程或 provider dispatch。
- `verification_required`：pure non-Exp1 trace protected persist→official load→metrics/replay 正常路径；空 snapshots + 非空 provided map 负例；online exact/missing/extra provider-object 回归；trace descriptor current-provider refs 为空、external refs 非空且 replay digests 一致；最终一次 Task 27 canonical，provider/network calls=`0/0`。

### Task 27 第九文件计划外批准（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：Task27-C2-trace-role-alias-boundary。
- `approved_files`：仅 `src/tokenshare/experiments/paper_formal_evidence.py`；负例仍只能追加到已批准的 `tests/experiments/test_paper_formal_runner.py`。
- `approval_reason`：Task 18 eligibility 要求 `CurrentTraceWrapper.locator_digests` 与 validated bank entry 的 native roles 原样一致，Task 20 direct contract 使用 normalized paper roles，而 persisted lineage closure 又对两者做原样 mapping equality；三层已冻结 ABI 在同一 digest 上仅角色名称不同。
- `impossible_without_change`：现有八文件若投影 wrapper/facts 会改变 eligibility report；若保留 native wrapper 则 persisted lineage 比较失败；若把 direct 改回 native roles则违反 paper direct contract。唯一同时看见两侧的 official 比较边界是 `paper_formal_evidence.py`。
- `evidence`：独立只读 scope audit 核对 `paper_models.py:2017-2031`、`paper_direct_results.py:73-85,600-610`、`paper_formal_evidence.py:5795-5816`，并确认 `paper_metric_observations.py` 与 `paper_exp5_artifacts.py` 已存在相同冻结别名语义但不服务 persisted-lineage closure。
- `minimum_scope`：仅新增本文件私有、纯比较 canonicalizer，在 `wrapper.locator_digests` 与 direct `locators_by_entry[...]` 比较视图中映射 `raw_output|provider_failure -> raw_output_or_provider_failure`、`usage -> usage_status`；不修改或重写任何持久化对象。digest 值必须完全相同，未列 role 原样保留并参与 exact equality，任何 alias collision 必须 fail closed。
- `forbidden_adjacent_changes`：不得修改 wrapper、eligibility facts/report、bank entry、direct locator 或其 digest；不得修改 root/manifest/entry/task/attempt/reference 可达性、eligibility、metrics、实验口径、Task 28 或 provider；不得接受未知别名、subset 比较、缺失 role 或覆盖式映射。
- `verification_required`：正例覆盖 native `raw_output`、`provider_failure` 与 `usage` 的 protected persist→official load→metrics/replay；负例覆盖 alias digest 不同、unknown、missing/extra role、alias collision；回归错误 bank root/manifest/entry/task/attempt/unreachable ref 仍拒绝，eligibility report 保持 native roles 与摘要/digest 不变；双冲突 nodeid 与 fresh Task 27 canonical，provider/network calls=`0/0`。

### Task 27 PLAN_OUT A：双域原生 online evidence producer（PLAN_OUT_APPROVAL / REJECTED，2026-08-03）

- `task_id`：EPD027-C2-native-online-evidence-producers。
- `approved_files`：仅 production `src/tokenshare/experiments/factorization_paper_adapter.py`、`src/tokenshare/experiments/lean_paper_adapter.py`；runner 与 runner test 沿用既有批准。
- `rejected_files`：不批准修改 `tests/experiments/test_factorization_paper_adapter.py`、`tests/experiments/test_lean_paper_adapter.py`；已批准的 `tests/experiments/test_paper_formal_runner.py` 可以直接覆盖双域原生 adapter→runner 正常链，额外测试文件不满足不可替代性标准。
- `approval_reason`：official online direct evidence 强制包含 `actual_resource_book` 与 exact identity-bound correctness verdict；当前 runner 固定传 `None` 并只按 artifact type 重标普通 verification/checker report，正常 Factor/Lean online 路径无法形成权威成功数据。
- `impossible_without_change`：runner 只能复制原生 store 已产生的事实，不能在事后合法生成 provider resource book，也不能替 verifier/checker 作出 direct correctness verdict；Factor adapter 已有 final oracle audit，Lean adapter 已有 root checker result/report/proof refs，但两者尚未持久化 official direct ABI artifacts。
- `minimum_scope`：两个 adapter 在各自原生 store 内使用 `paper_direct_results.py` 统一定义/校验的 builder 持久化完整 actual resource book 与 exact direct verdict；verdict 只无损投影既有 final oracle/checker 布尔值、source ref/digest 与 exact final identity，不重新调用或复制 verifier/checker。runner 只验证 manifest/bytes/schema/source identity并复制，删除 artifact 重标和 online `None` 路径。
- `forbidden_adjacent_changes`：不得新增独立 result/dataclass/schema builder，不得修改 verifier/checker、协议状态机、eligibility、metrics、题量/profile、provider dispatch、Task 28 或 correctness 算法；adapter 与 runner 不得各自复制 schema。
- `verification_required`：在已批准 runner test 中分别覆盖 Factor/Lean 正确、错误 final、无 final failure、exact verdict identity、actual book 全 attempt 可达、checker/verifier 调用次数不增加；双域离线 fake-real E2E、protected persist→official load→metrics/replay、既有 adapter 回归与 Task 27 canonical，provider/network=`0/0`。

### Task 27 PLAN_OUT B：root multi-entry / failed direct ABI（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD027-C3-I1-root-multientry-failed-direct-abi。
- `approved_files`：新增批准 production `src/tokenshare/experiments/paper_direct_results.py` 与 test `tests/experiments/test_paper_direct_results.py`；runner、`paper_formal_evidence.py` 与 runner test 沿用既有批准。
- `approval_reason`：official direct factory 当前全局禁止重复 role 并强制单 entry，runner 汇总多 unit/replacement locators 却只保存末个 wrapper；failed 路径把任意 artifact 当 final，resume 新建空 collector并跳过 completed roots，零 evidence 不投影，导致正式多 unit/replacement、failed、resume 与零成功路径无法保留权威闭包和固定分母。
- `minimum_scope`：role 唯一改为每 `entry_id/attempt` 内唯一，同一 role 可跨 entry且冲突 digest/缺失/额外 role继续 fail closed；direct row仍按 root，resource book 是 root-scoped container，成员按 `unit_id × attempt_id × entry_id/replacement_slot`，所有 replacement 可达，不增加论文分母。
- `schema_compatibility`：唯一允许的新 schema 是 multi-entry resource-book artifact v2；单 entry v1 bytes/digest/replay 必须不变。不得升级 direct-result、projection、inventory、`ExternalBankObjectLocator.v1` 或 `paper_models.py`。
- `failed_missingness_resume`：factory 接受 `final_result_ref=None`；有完整 failed/timeout/budget-exhausted facts 的 root 是实验 false 而非 infra-invalid，有 final 时仍要求 exact bound verdict；`not_started` 仅用于无 dispatch/persisted execution facts。resume 必须从已校验 `FormalEvidenceStore` facts 重建 collector后再跳过 completed roots；零成功/零 observed evidence仍对完整 inventory投影。
- `forbidden_adjacent_changes`：不得修改 root inventory/digest、projector 算术、eligibility/publication policy、实验规模、Task 28 或历史 evidence；不得以最后 wrapper、当前记录数或成功数充当完整 closure/分母，不得复制第二套 projector/collector。
- `verification_required`：`test_paper_direct_results.py` 锁定 v1 single-entry digest/backcompat、v2 per-entry completeness、optional-final、空 evidence固定分母及重复同-entry role拒绝；runner test覆盖多 unit+replacement、全失败、零 dispatch、interrupt→resume与 uninterrupted输出/digest等价、official loader/replay零 provider；最后 direct-results targeted、双域/multi-entry trace-online E2E、resume equivalence、protected loader/metrics/replay 与 Task 27 canonical，provider/network=`0/0`。

### Task 27 C1 official metric identity bridge（既有文件内范围批准，2026-08-03）

- `task_id`：C1-official-metric-identity-bridge；不新增文件，只继续修改已批准的 `paper_formal_metrics.py`、`paper_formal_runner.py` 与 `test_paper_formal_runner.py`。
- `approval_reason`：authoritative direct IDs `exp1_real_ai_feasibility` / `exp2_real_ai_scalability` 与 tracked legacy metric ABI `experiment_1` / `experiment_2_trace` / `experiment_2_online` 不同；直接把 frozen row 传入 official DTO/projector 会 fail closed，但改写 authoritative row 会破坏 provenance。
- `minimum_scope`：只扩展 ephemeral metric projection view。Exp2 trace 仅在 input key、authoritative Exp2 ID 与 `real_model_trace_protocol_run` 同时匹配时映射为 `experiment_2_trace`；Exp2 online 仅在同样严格匹配 `online_real_provider` 时映射为 `experiment_2_online`。除 `experiment_id` 外字段、root/order/count 必须 exact；runner 只从已验证 view 与 persisted producer facts 构造既有 official DTO。
- `authoritative_boundary`：authoritative rows 始终用于 protected input、lineage digest/source index；metric view 不持久化、不进入 lineage。Exp3–5 必须零 identity 转换。
- `forbidden_adjacent_changes`：不得修改 Exp1/Exp2 projector、metric contract、公式、分母、membership、threshold、eligibility、direct factory/inventory/schema；不得新增文件；不得仅按 Exp2 ID 而忽略 evidence class。
- `verification_required`：正例覆盖 Exp1、Exp2 trace/online；负例覆盖 wrong key、trace/online 交叉、wrong ID/evidence、删行/加行/重排/root/任一非 ID 字段变化；断言 canonical rows/digest/source index 仍含 authoritative ID，Exp3–5对象和值不变；运行 formal-metrics、Exp1/2 projector、protected loader/replay 与 Task 27 canonical，provider/network=`0/0`。

### Task 27 C4 acquire-bank production factory v2（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD027-Task27-C4-acquire-bank-production-factory-v2。
- `approved_files`：新增批准 production `src/tokenshare/executors/ai_api.py`、`src/tokenshare/executors/response_bank.py`、`src/tokenshare/experiments/paper_response_bank.py`；新增批准 existing tests `tests/executors/test_ai_api_executor_success.py`、`tests/executors/test_response_bank.py`、`tests/experiments/test_paper_response_bank.py`、`tests/experiments/test_paper_response_bank_acquisition.py`。pipeline、Factor/Lean adapters 与 pipeline test 沿用既有批准；不新增文件。
- `approval_reason`：production `acquire-bank` 尚无 `SemanticSlotCandidate` / `AcquisitionRequest` 构造链与 prepare-only service；planning 若复制 executor body/identity 逻辑会形成 shadow prepare path。Task 26 marker、acquisition orchestrator、terminal bank identity 与 inventory canonical ordering还存在不可绕过的 normal-path ABI冲突。
- `minimum_scope_ai_api`：只抽取 pure prepared-request helper并让既有 executor复用；helper不得读 secret、写 artifact或调用 transport，bytes/body digest/inference identity必须与 executor原路径完全一致，既有 artifact→secret→transport顺序不变。
- `minimum_scope_bank_identity`：`PreparedOutboundRequest.entry_id` 保持 provider-config identity；新 terminal bank ID只由 versioned canonical `{semantic_slot_key,inference_request_digest}` 派生，作为 row/entry/locator/manifest/index的 opaque唯一 identity。不得依赖 inventory id、manifest、run/attempt。不得改 v1 field shape、文件名、manifest digest或历史合法 bank reopen；新公式仅由新 producer/bundle loader严格校验，不得在全局 `from_dict` 强制历史 row满足新公式。shared canonical sort/digest必须与现有按 `inventory_entry_id` 算法逐字节等价。
- `minimum_scope_acquisition`：`paper_response_bank.py` 持久化 create-only typed acquisition plan bundle、exact prepared bytes/provenance、canonical inventory与独立 full-acquisition budget；Task26 schemaful paid marker是唯一 paid marker，orchestrator只消费/验证，不创建第二套。pipeline调用 `acquire_all()` 做startup reconcile；禁止 direct acquire loop与 ambiguous自动重发。batch仅 complete且无missing/ambiguous时在fresh child root调用一次既有 `initialize_response_bank`；resume已完成child只reopen/验证，不重复initialize。普通 child bank integrity marker不算第二个 paid marker。
- `budget_boundary`：full acquisition budget由 exact inventory/request ceilings推导并绑定receipt/marker/ledger；明确拒绝 L3 516-call budget作为full-bank budget，所有漂移在secret/transport前fail closed。
- `forbidden_adjacent_changes`：不得复制 provider body、插件 split/prompt/catalog、inventory builder、publisher或acquisition lifecycle；不得改 protocol、formal/smoke、schema v2、迁移、安全工程、provider/network范围；不得持久化 secret或 replay调用provider。
- `verification_required`：四个新增批准测试文件与 pipeline test覆盖 pure prepare零副作用与executor bytes一致、同provider多槽唯一terminal ID/旧v1 reopen、bundle/canonical inventory/replacement pairing、单Task26 marker、独立full budget、new/resume/crash/reconcile、complete child initialize exactly once及fake transport每批准request最多一次；最终 Task27 canonical，provider/network=`0/0`。普通Task不运行Fast/Full，Full留feature完成节点。

### Task 27 C4 ledger canonical inventory delegation（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD027-Task27-C4-ledger-canonical-inventory-delegation。
- `approved_files`：新增批准 production `src/tokenshare/experiments/paper_budget_ledger.py`；不批准修改 `tests/experiments/test_paper_budget_ledger.py`，只运行既有回归。新增行为由已批准的 `test_paper_response_bank_acquisition.py` 覆盖。
- `approval_reason`：budget ledger 的 preregister 与 reload validation 私自按 `(semantic_slot_key, inventory_entry_id)` 排序并重算 digest，而 response-bank v1 权威算法按 `inventory_entry_id` 排序；合法多行 full inventory 会在 reserve/secret/transport 前被错误拒绝，调用方预排序无法绕过 ledger 内部二次排序。
- `minimum_scope`：`paper_budget_ledger.py` 仅导入并在 preregister 与 reload validation 两处调用 shared `canonical_inventory_rows()` / `response_bank_inventory_digest()`；删除本地重复 sort/digest，禁止保留 fallback 双算法。
- `compatibility`：不得修改 SQLite schema、keys、row JSON、transaction、reserve/state/budget/reconcile/export；旧权威合法 ledger结果不变，旧非权威 digest不得被兼容放宽。
- `verification_required`：重跑两个 acquisition failing nodeid、完整 `test_paper_response_bank_acquisition.py` 与只读既有 `test_paper_budget_ledger.py`；provider/network=`0/0`。

### Task 27 budget-ledger test authority sync（PLAN_OUT_APPROVAL，2026-08-03）

- `approved_files`：新增批准 test `tests/experiments/test_paper_budget_ledger.py`，仅同步其 `_inventory_digest()` helper；不新增测试文件或 case。
- `approval_reason`：既有 helper 仍按旧 `(semantic_slot_key, inventory_entry_id)` 排序制造非权威 digest，导致正确的 production preregister 拒绝两个正常 fixture。更新 helper 是恢复测试权威前置条件，不是放宽 production。
- `minimum_scope`：导入 shared response-bank digest helper并让 `_inventory_digest(rows)` 直接委托；不得修改断言、cases、其他 helper或删除 `canonical_digest` 篡改负例。
- `forbidden_adjacent_changes`：不得兼容错误 legacy digest、排除 canonical tests或放宽 preregister。

### Task 27 F1 formal production factories（部分范围批准 / NEED_MORE_EVIDENCE，2026-08-03）

- `task_id`：EPD027-Task27-F1-formal-four-command-production-authority。
- `approved_commands`：先批准 `run-trace`、`run-exp1-online`、`run-exp5-online` 的 production factory closure；`run-online-checks` 继续 fail closed，等待独立 callback/service seam 申请。
- `approved_files`：沿用既有 `run_paper_pipeline.py`、`run_paper_experiments.py`、`paper_response_bank.py`、`test_run_paper_pipeline.py`、`test_run_paper_experiments_cli.py`、`test_paper_response_bank.py`；不新增文件。
- `rejected_files`：拒绝新建 `paper_formal_plan_bundle.py` 与 `test_paper_formal_plan_bundle.py`；tracked authorities + pure authority builder + Task26 digest binding 已足够，未证明新持久化 schema 不可替代。
- `minimum_scope`：`run_paper_experiments.py` 只抽 catalog/matrix/dispatch/budget/provider identity pure authority builder，旧 CLI 必须委托同一 helper并保持 digest parity。`paper_response_bank.py` 只增加 full `SemanticInventoryPlan` 严格反序列化与 validated external-bank→`PaperFormalTraceContext` builder；必须与 C4 acquisition bundle/bank manifest rows及inventory digest exact cross-check。pipeline只调用helper/loaders，不复制规划。
- `trace_boundary`：`run-trace` 必须使用显式 process-local external-bank resolver，在 coordinator 前完成 root/manifest/inventory/entry/condition/case/replacement completeness preflight；formal service `real_transport=False`、无secret/provider。external bank path不得进入formal input、artifact、event、SQLite或replay payload。
- `provider_boundary`：Exp1/Exp5 full 必须从实际authority推导并核验plan/inventory/budget/config、Task26 receipt/唯一marker、admission与bounded budget，capability/full scope不得互换，失败在secret/transport前。
- `run_online_checks_blocker`：`PaperOnlineProviderEvidenceCallback` 需要同时接 executor post-raw与protocol lifecycle hooks，才能产生controlled rejection/requeue与Task25权威evidence；当前production无调用点且formal runner无callback/hook seam。未获独立批准前不得让该命令调用provider或伪装可执行。
- `forbidden_adjacent_changes`：不得修改/复制provider body、catalog/split/prompt、budget/inventory算法，不得持久化external bank path/secret，不得实施Task28 policy。若要关闭online checks，必须另提精确callback/service plan-out。
- `verification_required`：既有pipeline/legacy CLI/response-bank tests直接走production map/factory，不替换factory；证明trace零provider、Exp1/Exp5 wrong scope/budget/authority在secret前拒绝、legacy helper digest parity与protected replay；provider/network=`0/0`。

### Task 27 F2 Exp5 capability smoke production authority（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD027-Task27-F2-exp5-capability-smoke-production-authority。
- `approved_files`：新增批准 production `src/tokenshare/experiments/paper_smoke.py`；pipeline、legacy CLI及两份CLI测试沿用既有批准。拒绝修改 `tests/experiments/test_paper_smoke.py`，只运行既有回归。
- `approval_reason`：legacy smoke CLI 已有 tracked v4 profile→canonical dispatch→resolved plan→exact budget→launch/recovery→Exp5 schedule→existing execute service 正常路径，pipeline factory stub是唯一缺口。
- `minimum_scope`：`paper_smoke.py` 只抽无副作用authority builder，legacy CLI与pipeline委托同一helper；不得改变smoke profile/schema、selector、budget、schedule或execution service。pipeline双重拒绝capability/full command kind与receipt scope互换。
- `eligibility_boundary`：capability smoke维持smoke/pilot/regression-only/not-paper-eligible，不得冒充Exp5 full或论文正式矩阵。
- `verification_required`：pipeline/legacy CLI测试直接走production factory，覆盖digest parity、scope mismatch在secret/transport前、fake transport受控与resume identity；只读运行既有paper_smoke回归，provider/network=`0/0`。

### Task 27 run-online-checks production callback/service（PLAN_OUT_APPROVAL，2026-08-03）

- `task_id`：EPD027-Task27-online-callback-final-amendment。
- `approved_files`：本语义范围精确覆盖 production `run_paper_pipeline.py`、`run_paper_experiments.py`、`paper_online_checks.py`、`paper_formal_callbacks.py`、`paper_formal_runner.py`、`paper_budget_ledger.py`、`executors/ai_api.py`、`factorization_paper_adapter.py`、`lean_paper_adapter.py`；tests `test_run_paper_pipeline.py`、`test_run_paper_experiments_cli.py`、`test_paper_online_checks.py`、`test_paper_formal_callbacks.py`、`test_paper_formal_runner.py`、`test_paper_budget_ledger.py`、`tests/executors/test_ai_api_executor_failover.py`。不批准/不需要 adapter tests、dispatcher/core/contracts 或独立 schema 文件。
- `approval_reason`：Task 25 只有 pure plan/evidence producers，production 无 dispatch/runtime service；pipeline factory保持stub。callback必须同时接 executor post-raw/provider-failure与protocol lifecycle hooks，才能让controlled rejection、fault/death、requeue/replacement与Task25 evidence来自真实持久化生命周期，而非事后伪造。
- `runner_boundary`：formal runner只新增 optional per-root callback factory并沿既有层级传到正常 `dispatch_paper_case`；composite只观察/组合official hooks。Exp3 mutation、worker death、verifier/requeue/parent commit继续由既有runner/coordinator/ProtocolEngine拥有；默认非online路径行为不变，禁止复制orchestration/direct/protected projector。
- `budget_policy`：tracked planned上限为 `4+480+12=496`，hard=`496+20=516`；20仅供已写dispatch-intent且终态未知的同slot paid reacquisition，不得预发或计expected/actual。ledger新增opt-in、持久化version/digest category policy，`BEGIN IMMEDIATE`内原子限制reservations≤496、reacquisitions≤20、combined≤516，同时维持calls/tokens/CNY。reopen policy drift fail closed；无policy的full-bank默认行为、SQLite rows与公共API兼容。
- `secret_dispatch_order`：固定为 prepared artifact commit → ledger `reserved` → `resolve_api_key()` / transport resolver → transient observer → ledger `dispatch_intent` → actual-call count → transport。pre-intent secret/local resolver/hook failure必须safe release，0 transport、0 provider-failure artifact、0 ambiguous；intent后无terminal才按ambiguous规则处理。禁止 intent-before-secret。
- `adapter_secret_scope`：Factor/Lean adapter仅删除eager all-key snapshot并贯穿per-root transient、lock-safe、deduplicated collector；不改split/check/merge/requeue/plugin/checker语义。必须同时删除 `ai_api.py` 的隐式env回读；唯一key读取点为上述 `resolve_api_key()`。collector、secret及repr永不持久化，repr必须redacted。root-end继续现有secret scan；actual_calls>0且collector空必须fail closed，零调用不得伪造scan pass或结束时补读env。
- `provider_failure_schema`：显式新增 `phase7.ai_provider_failure.v1` 与 `phase7.ai_provider_terminal_usage.v1`，复用call provenance v2；不得扩宽response-usage v1。只有真实transport terminal failure才依次持久化failure/provenance/nullable usage-status，调用failure hook，再publish terminal/settle；`ExecutionSubmission.raw_output_ref=None`，不进parser、不要求verified raw，taxonomy不变。0-call local/config/secret failure不得伪装provider attempt/failure evidence。missing usage可按upper bound结算，但报告actual保持null。
- `evidence_boundary`：paper_online_checks保持pure validator/producer、禁止dispatch；Task25 producer只消费persisted current refs，固定capability/Exp2/Exp3 denominator，不把estimate或ambiguous ledger rows计actual。provider failure不得删24/2 condition行或伪装success/pass。callback capture/rejection/requeue/death顺序必须来自official events/refs。
- `resume_crash`：pre-intent crash/reserved可safe release且当次不重发；dispatch-intent无terminal→ambiguous，禁止自动重发；terminal已提交→幂等publish/settle；settled no-op。额外paid reacquisition只能消耗20-call分类限额并保持slot identity。
- `forbidden_adjacent_changes`：不得修改adapter/core状态机、metrics/formulas/Task28 gates、provider model/config/taxonomy，不得新增direct transport loop、fake protocol events、env backfill或把516当primary可派发量；不执行真实provider/network。
- `verification_required`：七个批准测试文件覆盖production map/legacy authority、Task25 fixed denominator/failure missingness、callback composition/reconcile、runner双域及Lean v1/v2 secret/hook E2E、ledger category race/reopen/default backcompat、executor success/failure/zero-call生命周期与explicit schema；全部network tripwire/deterministic fake，provider/network=`0/0`。普通Task不运行Fast/Full/LeanAudit，最终纳入Task27 canonical与follow-up review。

### Task 27 zero-dispatch protected closure（既有文件内范围批准，2026-08-03）

- `task_id`：EPD027-Task27-B-zero-dispatch-protected-closure；不新增文件，只继续已批准 `paper_traceability.py` 与 `test_paper_formal_runner.py`。
- `approval_reason`：official direct projector允许空runtime evidence并对完整inventory产生 `not_started` rows；traceability层无条件拒绝空runtime使合法hard-limit-zero run无法persist/load/replay且丢失固定分母。该模块同时看到direct/runtime并拥有protected descriptor，是唯一正确边界。
- `minimum_scope`：runtime非空行为完全不变；runtime为空时必须递归找到至少一行且 `type(row) is PaperDirectRootResult`，并且所有行 `root_status == "not_started"`，才允许继续。empty/all-empty、无typed row、duck type、subclass、mixed或任一completed/failed/blocked/ineligible行均保留原 `TraceabilityBlockedError`。
- `forbidden_adjacent_changes`：不得生成runtime/evidence/artifact，不得改descriptor schema、provider/formal/source/loader closure；必须撤销 parked diagnostic message/path hunk并恢复合法artifact digest/size校验可达性；不得改runner/direct projector/metrics或新增文件。
- `verification_required`：2-root real_transport hard-limit-zero E2E断言adapter/provider=0、两条exact not_started、runtime=()、protected load/metrics/replay与分母=2；负例覆盖empty、duck/subclass、mixed/non-not_started、缺formal evidence、额外provider file、缺source resolver；复跑protected loader/replay/current-provider closure与Task27 canonical，provider/network=`0/0`。

## 已完成与未完成

- Task 27 已完成并提交为 `b5f340f5`。最终修改后 fresh canonical + network tripwire=`358 passed in 470.24s`；pipeline focused=`67 passed`；`py_compile`、`git diff --check` 通过。
- final reviewer=`PASS 0C/0I/0M`；原始 `2C/4I`、后续兼容/online callback 问题与最后 CLI blocked-domain I1 均关闭。
- post-accept test minimization 由子智能体完成：0 测试删除、0 合并，只删除 2 个未引用旧 transport helper；最小测试=`6 passed in 35.97s`。
- Task 28 已完成并提交为 `7338f177`；fresh canonical=`87 passed, 0 failed/error in 0.71s`，follow-up reviewer=`PASS 0C/0I`，post-min 无修改；当前状态为 accepted。

## 不可破坏边界

- runner 必须从原生 `EventLedger` / `ArtifactStore` 及正式 typed persisted facts 产生 canonical evidence，再走官方 `build_canonical_direct_evidence`、`project_paper_direct_results` 与 protected replay projection/persist。
- CLI 只能消费 runner 生成且可验证的 descriptor；不得自行构造 protected object、传裸路径、补空 rows、从普通 checkpoint/condition/fault 名称反推结果，或放宽 lineage/eligibility。
- 不得复制协议状态机、attempt/retry/death/verification/merge/terminal 生命周期，不得形成影子 TokenShare。
- 无真实有效 paid receipt 前禁止真实 API/provider/network；API key/env 存在不构成授权。
- replay 不得重新调用 provider。

## 下一任 Agent 的第一个精确动作

只读确认工作树/分支与 Task 28 两个提交；active writer=`0`。下一动作仅由监督者只读 Task 29 段落并冻结，尚未进入 Task 29。禁止重做 Task 0–28 或调用真实 provider/network。
## 2026-08-04 Task 29 acceptance addendum

EPD-027 的 Task 0–29 已 accepted（`30/35`）。Task 29 实现提交为 `555d9f3d0cb808b419ecbecfc59e37b2d1ace62d`（`feat(experiments): replace legacy smoke launchers`），严格使用 21 个批准文件和 10 项批准 plan-out。

已审定的离线 GREEN 证据为：canonical `49 passed`、pipeline affected `9`、CLI affected `3`、core runner+observation+gate `204`、official closure `3`（complete CNY ready，USD/missing usage blocked）、observation `17`、complete-CNY 正例 `1`、runner `158`；PS parse `9/9`、false/secret/unlimited、imports、pycompile、diff、allowlist 均通过。最终 review 为 `0 Critical/0 Important`，历史 `3C/2I`、false-switch 与 Exp5 zero-role 均已关闭。post-accept minimization 没有修改、删除或合并，验收时未重跑测试。

provider/network=`0/0`。没有 Task26 verified paid receipt，formal paper matrix 继续 `NO-GO`，不得真实 API。三个 deferred Minor 是 Task28 facility predicate、`offline_gate_parser_only`、以及 Task29 trace 顶层 result/wrapper 未暴露两份 receipt digest。

90 分钟 checkpoint 仅为历史；当前 active writer=`0` / accepted。下一动作仅为监督者只读 Task30 单段并冻结，尚未进入或实施 Task30。code map 依 Task 硬边界未修改，并登记为 milestone follow-up。

## Task 30 accepted

EPD-027 的 Task 0–30 已 accepted（`31/35`）。prestart commits=`2d7240f4`/`012fcc32`，90 分钟 checkpoint=`23a21146`，implementation commit=`9ec806b4f261a3c1b27968891eaa07cf274a2466`。范围严格为原 8 文件加唯一批准的第 9 文件 `tests/experiments/test_paper_full_resource_trace.py`，无第 10 文件或 production 扩张。

GREEN 精要：RED=`10/15`，canonical 最终=`25 passed`；profiles L1/L2/L3/L4=`441/8/2/2`，总计 `453 selectors/892 items`；L1 首次32、expanded855 passed、final composite full-run877 passed+2 precise repaired passed；Task19/23/24新增=`2/6/1`；L2 relative-root=`2 passed`；L3 missing-root blocked exit3、outside-root exit1、PowerShell/Bash exit3；overlap=`1 passed in 9.08s`；500-root=`1 passed in 1846.39s`。final review=`0C/0I`；minimizer `<3m`、零修改。

provider/network=`0/0`，无 verified paid receipt/secret，formal matrix 继续 **NO-GO**。deferred Minor 共4项：既有 Task28 facility predicate、`offline_gate_parser_only`、Task29 receipt digest 暴露，加 Task30 artifact audit 先于 pytest tripwire 激活但当前 stored replay 无 provider。code-map milestone follow-up 保留。

当前/下一步仅为 Task31 freeze-only，尚未实施 Task31；不得读取其计划正文或调用真实 API。

## Task 31 accepted

EPD-027 的 Task 0–31 已 accepted（`32/35`）。Task31 无 source/test/runtime evidence commit：planned exact empty directory 被 git 忽略，仅产生本次 state acceptance commit。初始 precondition 在 artifact directory 缺失时 fail closed、收集 `0 tests`；创建 planned exact directory 后权威 profile 仅真实运行一次。

L1 最终 exit=`0`，`882 passed / 0 failed / 0 errors`，`441` exact selectors，`2155.58s`，digest=`sha256:d3dd21ed9fa0c15452019c70c8a90e1fa8a7d45564951043b54aed01f399018c`，status=`passed`。coverage 覆盖 Tasks0–29（含25–29）、Task24 pure=`2` / artifact audit=`0` 与 two-stage gate；Lean root=`1` / checker=`1<=2`。唯一 Fast exit=`0`：`525 passed, 1 skipped in 38.84s`。

review=`0C/0I/0M`，确认链证据来自真实 production、无 shadow；minimizer=`N/A`、零修改。provider/network=`0/0`，无 verified paid receipt/secret；L3/L4 unset，formal matrix 继续 **NO-GO**。既有4项 deferred Minor 与 code-map milestone follow-up 不变。

当前/下一步仅为 Task32 freeze-only，尚未实施 Task32；不得读取其计划正文或调用真实 API。

## Task 32 accepted

EPD-027 的 Task 0–32 已 accepted（`33/35`）。Task32 为 runtime-only，无 source/test commit；授权 runtime 保留在 `local/verification/epd027-l2/{replay-a,replay-b,negative-exp34,l2-runtime-summary.json}`，工作树显示 `?? local/verification/`。profile exit=`0`：`8 passed in 8.41s`，status=`passed`，digest=`sha256:145059c...cef2a`。

positive source=`heiyucode_gpt56_smoke_20260716`；number=`4733749`，predicate=`passed/true/true`，factors=`1013×4673`；case/batch/tree/raw/provenance digests 已在 runtime summary 记录。真实链 terminal=`SETTLEMENT_RECORDED`、无 shadow，classification=`regression_only`/`paper=false`/not formal。negative expected-fail hash before=after=`sha256:7f2a...1c60`。

双 replay 使用独立 root；observations=`sha256:4b125...4192`、table=`sha256:c4f4...5ae9`、lineage=`sha256:9af8...a9a0` 与 ledger 相等，113 files inventory/content 相同。review=`0C/0I`；minimizer=`N/A`、零修改。provider/network=`0/0`（历史 source attempt 独立，不等于本次调用）。

无 verified paid receipt/secret，formal matrix 继续 **NO-GO**，L2 不替代 L4。deferred Minor 共5项：既有4项加 Task32 runtime 未 ignore housekeeping；code-map milestone follow-up 保留。

当前/下一步仅为 Task33 freeze-only，尚未实施 Task33；不得读取其计划正文或调用真实 API。

## Task 33–34 accepted / EPD-027 implementation complete

EPD-027 Task 0–34 已全部 accepted（`35/35`）。Task33 owning fix=`a10e988ddfc923cca28423dfd83af60a96863a4f`，follow-up fix=`7c9dff7c`；主实现 exact12：sidecar/semantic authority/environment/fixed_plan/catalog/audit/profile/Exp5 + 4 tests；PLAN_OUT 共3个 production（profile、Exp5、fixed_plan），无未批准文件。final review=`0C/0I`，minimizer=`NO_CHANGE`。

测试精要：module-missing RED exit2；targeted=`140/140 in 30.10s`，contracts=`12/12`，native receipt=`1/1 in 6.07s`，L1 Lean=`1/1 in 3.12s`；bridge RED0/1→GREEN1/1，fixedplan1/1，diffcheck0。唯一 Fast exit1=`524p/1f/1s in 83.83s`，L1 function count40!=39；最小合并后失败node1/1 0.25s、existing2/2 19.78s，reviewer明确无需重跑。

Task33 public CLI 仅一次 exit3/83.67s，因 receipt absent 正确 BLOCKED；budget=`516/171708288/979.524864/CNY1000`，blocked digest=`sha256:47598f070d41715b99a58034b223c3c18664fa9a9a970af059f0750acaf5b8f5`；provider/network=`0/0`，3 roots/marker/ledger absent，launcher/audit skipped；`local/verification/` runtime 保留。formal matrix 继续 **NO-GO**。

rejected cascade：official600 checker 曾 success516.23s，但方案弃用；Full 唯一一次 exit1=`2752p/86f/1s in 3826.97s`，未进入 LeanAudit、不重跑。Task34 也未运行 Full、LeanAudit 或 600-entry checker。

Task34 runtime=`local/verification/epd027-l4`。L3 已因 verified paid receipt missing 而 blocked，因此仅写 `l4_cell_traceability_blocked`；diagnostic root 非 formal/canonical L3 terminal output，不得 L4 PASS。blocked digest=`sha256:17ccfc2fc72184a9ee6151b910d462b5ba8014c8d55e9f9597931b3d6bbfa268`。execution/publication gates 均 `BLOCKED`、DAG 无环、无 dispatch/receipt；digests=`sha256:b1dfa6edf77b3a0982dc72ce09fde74f1fe1300d7f055520c95cfe7cc721de3c` / `sha256:7b49f79a7a22f63eb7bf9e3ba7056fd61c918b12deeed5070433a03113273d0c`。provider/network=`0/0`，provider attempts 为空。

profiles：L2=`8 passed`，L3/L4 均 exit3/blocked；首次 L1=`880 passed / 2 failed in 2834.24s` 暴露 Task33 bug，`7c9dff7c` 修复后 L1=`882 passed in 2807.21s`；Fast=`525 passed / 1 skipped in 75.85s`。Task34 review=`0C/0I`，Minor 仅既有 `offline_gate_parser_only` deferred；minimizer=`N/A`。计划内 tracked 修改仅 code map、experiment parameter decision log、latest real-plugin experiment design，无 production/test 修改。

deferred 保留 7 类：Task28 facility predicate detail；Task29 offline label 与 top-level receipt digests；Task30 artifact-audit/tripwire order；Task32 runtime gitignore；Task33 8个 EOL/stat-only housekeeping；ArtifactStore Windows long-marker `OSError22`。EPD-027 实施已 accepted，但 receipt 仍 absent，formal matrix 仍 **NO-GO**，不表示正式论文实验已完成。无下一 Task；未来只能在用户提供并通过 Task26 校验的 paid receipt 后进入 formal acquisition，不得自动调用 provider。
