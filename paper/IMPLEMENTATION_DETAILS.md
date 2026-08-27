# TokenShare 实现细节参考

更新时间：2026-08-25

## 用途

本文件是 `PAPER_WRITING_BRIEF.md` 的按需下钻材料。论文写作者只有在某个具体段落需要代码级依据时才读取相应部分；它不是启动必读文件，也不代替简报中的系统主线和写作准则。

这里保留实现位置、关键符号、定向验证和适用限制。当前源码和实际运行结果始终高于本文件；相关实现发生变化后，使用旧说明前必须重新核验。

## 当前核验基线

- 核验日期：2026-08-25。
- 核验 commit：`d25ebf860b18cd10731151a2bcc6a7586b62776b`。
- 核验时 `src/` 与 `tests/` 没有未提交差异；论文文件移动和协作文档变化不改变以下实现结论。
- 本次核验没有调用真实 provider，没有启动 Representative 或 Full，也没有运行 Lean 专项 suite、LeanAudit 或完整 catalog。

## 每个 root 由现有协议协调器完整驱动

### 可用于论文的实现结论

每个实验 root 都独立创建 artifact store、event ledger、`ProtocolEngine`、领域 plugin runtime 和 worker backend，然后通过现有 `ProtocolRunCoordinator.run_root()` 执行一次完整协议生命周期。Slim V2 负责冻结实验条件、装配这些对象、保存普通实验文件和投影论文指标，但没有另写一套平行协议状态机。

### 代码位置

- `src/tokenshare/experiments/slim_v2/runtime.py`
  - `run_root_slice()`：创建 engine/coordinator，并调用一次 `run_root()`。
  - `_build_root_assembly()`：按 root 装配 plugin、worker、store、ledger、在线或 fixed-trace 路径。
  - `execute_root_context()`：运行 root、保存协议快照并形成最终 root projection。
- `src/tokenshare/local_runtime/coordinator.py`
  - `ProtocolRunCoordinator.run_root()`、`_run_root()`：驱动注册、计划、调度、执行、验证、恢复与合并。
- `src/tokenshare/local_runtime/contracts.py`
  - `ProtocolRunRequest`、`ProtocolRunResult`、`ProtocolTaskPluginRuntime`：系统与 plugin 的公开生命周期边界。

### 定向验证

`tests/experiments/slim_v2/test_system_vertical.py -q`：`5 passed in 16.67s`。覆盖 Factorization 与 Lean fixed-DAG root 的 real-system vertical path、checker infrastructure failure 和结果投影。

### 限制

该事实不能证明真实分布式网络、区块链、开放节点、身份系统或恶意参与者防护已经实现，也不能提供任何实验成功率或性能结果。

## 两个领域 plugin 分别拥有拆分、验证和合并逻辑

### 可用于论文的实现结论

Factorization plugin 把候选除数空间划分为连续 range，对找到因数和未找到因数的 range result 进行确定性复核，并在 required slots 完整或已有有效 factor witness 时形成 merge。Lean plugin 根据固定 split plan 或 lemma DAG 建立 proof units；proof candidate 经过真实 Lean checker 后才能成为 canonical proof artifact，merge 后还要使用 root checker report。两者共享协议生命周期，但不共享领域正确性判断。

### 代码位置

- `src/tokenshare/plugins/factorization/runtime_adapter.py`
  - `FactorizationRuntimeAdapter.plan_root()`、`plan_units()`、`build_execution_request()`、`verify_submission()`、`build_merge()`。
  - `FactorizationExecutionBridge`：把 range executor 接入协议 execution submission。
- `src/tokenshare/plugins/lean_proof/runtime_adapter.py`
  - `LeanRuntimeAdapter.plan_root()`、`plan_units()`、`normalize_proof_submission()`、`verify_submission()`、`build_merge()`。
  - `LeanExecutionBridge`：把 proof candidate executor 接入 checker/canonical 路径。
- `src/tokenshare/experiments/slim_v2/execution.py`
  - `SlimLeanExecutionBridge`：在 fixed/provider candidate 路径与 Lean checker 之间提供 Exp4 verification structural route。

### 定向验证

同一 `test_system_vertical.py` 的 `5 passed` 覆盖两个领域的真实系统接线。该核验没有执行外部 Lean 环境 preflight 或完整 Lean catalog，因此可以说明 checker 接线规则，不能替代具体 Lean case 的真实实验结果。

## 在线回答与 fixed-trace 回答是两条明确分离的路径

### 可用于论文的实现结论

Experiment 1 和 5 使用在线 adapter。每个 protocol attempt 在 caller 内至多发送一次 provider 请求；额外 attempt 只能由协议 recovery 创建。调用前写 intent，响应和 terminal outcome 分别持久化，模型身份、usage、延迟和错误均保留。

Experiment 2–4 使用 fixed-trace adapter，只读取 `case × source repeat × planned unit` 对应的 Experiment 1 trace，校验领域语义和模型身份后重新进入同一 parser、verifier、recovery 和 merge 路径。requested ordinal 存在时精确命中，否则确定性选择该 trace 的最大自然 ordinal。该路径没有 transport 或 provider fallback。

### 代码位置

- `src/tokenshare/experiments/slim_v2/execution.py`
  - `ProviderSubmissionAdapter.execute()`。
  - `FixedTraceSubmissionAdapter.execute()`。
  - `reconstruct_fixed_trace_attempt()`。
- `src/tokenshare/experiments/slim_v2/provider.py`
  - `call_provider_once()`：intent 后至多一次请求，再保存 response/terminal。
  - `recover_interrupted_call()`：只读取既有事实，不再次请求 provider。
- `src/tokenshare/experiments/slim_v2/storage.py`
  - `RunStore.write_trace()`、`read_trace()`、`read_provider_terminal_result()`、`select_trace_attempt()`。

### 定向验证

- `tests/experiments/slim_v2/test_answer_paths.py -q`：`20 passed in 15.18s`。
- `tests/experiments/slim_v2/test_runtime_resume.py` 中 fixed trace 和 typed terminal 复用相关测试：`6 passed in 10.03s`。

上述验证使用本地 fixture，真实 provider 调用数为 0。它证明回答路径和复用边界，不证明某一模型已经取得特定实验结果。

## 恢复和故障处理从持久化事实继续

### 可用于论文的实现结论

Slim run store 保存 root result、protocol snapshot、Exp1 per-unit trace、provider intent/terminal/response。恢复扫描只接受已经提交的精确文件；已存在的 terminal response 可以离线重建，同一调用不会被重新发送。Exp1 snapshot 还保存正常协议 traces、coverage-tail targets 和可恢复的 tail requests。

Experiment 2–4 的故障和消融通过 scenario hooks、mechanism policy、现有 scheduler、thread/process worker backend 和领域 plugin 路径实施，不通过外部 runner 直接改写最终结果。worker death 使用真实子进程终止；其他 rate-fault 是预注册边界上的受控注入。

### 代码位置

- `src/tokenshare/experiments/slim_v2/runtime.py`
  - `materialize_protocol_traces()`、`run_coverage_tail()`、`resume_exp1_root_context()`、`resume_root_context()`。
- `src/tokenshare/experiments/slim_v2/storage.py`
  - `write_root_protocol_snapshot()`、`read_root_protocol_snapshot()`、`scan_resume()`。
- `src/tokenshare/experiments/slim_v2/scenarios.py`
  - `ScenarioHooks`、`ModeBlindChallengeController`、`Exp4StructuralRouteObserver`、`build_scenario()`。
- `src/tokenshare/local_runtime/coordinator.py`
  - `_recover()`、`_before_recovery_merge()`、`_finish_requeue()`。

### 定向验证

- `test_runtime_resume.py` 的重点恢复测试：`6 passed in 10.03s`。
- `test_scenarios.py::test_exp3_rate_faults_are_ordinal_zero_once_and_replacements_are_unmodified`：`5 passed in 22.91s`。

这些测试证明本地协议恢复和受控实验接线，不证明跨机器容灾、生产级故障恢复或 Byzantine fault tolerance。

## 论文指标从 root 运行事实投影和归约

### 可用于论文的实现结论

`project_root_result()` 将 `ProtocolRunResult`、ledger events、plugin checker/verifier facts、worker facts、trace、fault 和 ablation observations 归一为 typed root result。CLI 先冻结 profile 和 inventory，逐 root 执行或恢复，再调用 reducer。`reduce_run()` 只从 committed root results 和 frozen inventory 生成正式指标表，不从论文旧文字、设计计划或 legacy paper runner 推导结果。

### 代码位置

- `src/tokenshare/experiments/slim_v2/projector.py`
  - `project_root_result()`、`_factorization_result()`、`_lean_result()`、`_recovery_projection()`、`_fault_projection()`、`_ablation_projection()`。
- `src/tokenshare/experiments/slim_v2/cli.py`
  - `run_experiment()`、`_run_experiment_locked()`。
- `src/tokenshare/experiments/slim_v2/reducer.py`
  - `reduce_run()`、`reduce_exp5_with_exp1_v4_reference()`。
- `src/tokenshare/experiments/slim_v2/profiles.py`
  - `build_profile()`、`build_inventory()`、`build_plan()`、`downstream_trace_consumer_case_ids()`。

### 定向验证与限制

`test_system_vertical.py` 覆盖 root projection，`test_runtime_resume.py` 覆盖 committed typed facts 的复用。2026-08-27 的增量核验已读取并发布当前 Full run `slim-v2-full-flash-20260823-233000-b4c8e951` 的正式 reducer 产物；具体数值与统计结论必须从该 run 的 `metrics/` 重新读取，不能从本文件、旧实验段落或历史 Representative 转抄。

### 2026-08-27 reducer 缺失原因增量核验

`_reduce_exp1()` 在任一真实协议调用缺 provider usage 时，把 token/cost totals 保持为 `null + usage_missing`，不影响完整的 provider latency；`_reduce_exp5()` 在 parsed-unsubmitted 与 infrastructure-invalid 同 model cell 时保留 nonpass 的既有 N/A，同时把验证拒绝率与调用覆盖率标为 `infrastructure_invalid_root_present`。这两处只闭合 formal null 的科学原因，不改变公式、分母、schema 或 attempt 分类。

新增两个 fail-first 回归后，`test_reducer_golden.py -q` 为 `40 passed in 14.89s`，Slim 源码/测试 `compileall` exit 0。生产代码的全数据内存 dry-run通过153 formal occurrences；随后只执行一次离线 `reduce --run-dir`，发布11个正式 metrics 文件。发布前后全部非 metrics 文件计数/字节数/metadata fingerprint，以及21,260个 reducer/费用关键输入文件的内容 SHA完全一致；没有调用 provider、resume protocol、运行 Lean 或改写原始结果。

## 何时需要重新核验

以下变化可能使对应说明过期：

- shared coordinator、runtime contracts 或 protocol engine 生命周期变化；
- Factorization/Lean runtime、checker、validator、split plan 或 merge 逻辑变化；
- Slim provider、execution、trace、scenario、storage 或 resume 路径变化；
- root result schema、projector、profile、inventory、reducer 或指标权威变化；
- provider/model/pricing 配置变化，或新的正式实验 run 取代当前状态。

发现上述变化时，先核对当前代码或实际输出，再更新本文件和简报中受影响的总体描述。
