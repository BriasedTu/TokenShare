# Experiment 5 Fixed-Entry Identity Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Every production change must follow `superpowers:test-driven-development`; do not write production code before the corresponding RED test has failed for the expected reason.

**目标（Goal）：** 修复 Experiment 5 中“不同 provider config 复用同名 `entry_id` 时，condition 声明的 cohort member 与实际 provider/model/reasoning 发生分叉”的端到端身份漏洞，并保证首次执行、provider failover、协议 retry、恢复与 replay 都不能静默换成另一个 endpoint。

**架构（Architecture）：** 在 experiment layer 新增结构化 model-endpoint identity 与唯一公共 validator；preflight、condition、runner 和两个 paper adapter 共用该实现。通用 `AIAPIExecutor` 只增加 provider request/config 的通用一致性检查与安全 request-identity provenance，不导入 Experiment 5 cohort/member 策略。源 provider config digest、语义 endpoint identity digest 和 adapter 派生 execution config digest 分开记录。

**技术栈（Tech Stack）：** Python 3.12、frozen dataclasses、canonical JSON SHA-256、pytest、现有 `AIAPIExecutorConfig` / `PaperExperimentCondition` / `ArtifactStore` / scripted transports。

---

## 0. 执行边界与已验证根因

### 0.1 唯一权威

- 实验政策唯一权威：`Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`。
- frozen cohort：`benchmarks/paper/model_comparison_cohort.v1.json`，由 `model_cohort_digest` 锚定。
- local binding：批准的 preflight member plan，由 `provider_config_id + selected_entry_id`、source provider config digest 和 endpoint identity digest 锚定。
- 执行事实：持久化的 request、provider request identity、provenance、raw output、usage 和 model execution record。
- replay 只读取历史事实，不重新加载当前 provider config，不重新调用 provider。

### 0.2 根因数据流

```text
frozen cohort + local entry map + provider configs
  -> build_model_endpoint_cohort_preflight()
     正确验证 provider/model/reasoning/config
  -> expand_plan_conditions()
     只复制 member/entry/provider/model/reasoning/cohort digest
     丢失 provider_config_id 与 source config digest
  -> adapter _resolve_condition_entry_id()
     只比较 entry_id 字符串
  -> adapter _prepare_config()
     在调用方提供的 config 内按同名 entry 过滤
  -> ExecutionRequest
     provider_family 来自 config，而不是经过验证的 condition identity
  -> AIAPIExecutor
     正确调用错误 config 中的 SiliconFlow/Qwen entry
  -> provenance
     正确记录 SiliconFlow/Qwen，但 condition 仍声明 OpenAI/GPT/high
```

源码已经证明 `entry_id` 只在单个 `AIAPIExecutorConfig.entries` 内唯一；`load_model_entry_map()` 使用 `provider_config_id + entry_id` 定位 entry。因此 `entry_id` 不是全局身份。

### 0.3 只读复现证据

在系统临时目录、scripted transport、无真实 API 的条件下，给 OpenAI/GPT/high condition 传入同名 `gpt-entry` 的 SiliconFlow/Qwen config：

- Factorization：2 次 provider call；attempt 为 `siliconflow / Qwen/Qwen3.6-27B / gpt-entry`。
- Lean simple：2 次 provider call；身份同上。
- Lean lemma-DAG：2 次 provider call；身份同上。
- condition 在三条路径中仍为 `openai / gpt-5.6-sol / high`。

### 0.4 执行环境约束

当前 Lean `environment_digest` 把 fixture 的绝对 `project_root` 纳入哈希，frozen catalog 绑定 `E:\TokenEcnomic\TokenShare\fixtures\lean_proof_project`。外部 git worktree 会产生不同 environment digest，并造成 paper catalog / adapter / Lean lemma-graph 的环境假失败。

因此本计划在 `codex/exp5-fixed-entry-identity` 分支、原仓库绝对路径中执行。不得顺手修改 Lean environment digest 的可移植性；该问题属于独立范围。

---

## 1. 必须保持的身份模型

### 1.1 字段分层

稳定 cohort 逻辑身份：

- `model_cohort_id`
- `model_cohort_digest`
- `cohort_member_id`

稳定 suite binding 身份：

- `provider_config_id`
- `selected_entry_id`（condition 兼容字段仍名为 `model_entry_id`）
- `provider_family`
- `provider_model_id`
- `reasoning_profile_id`
- `model_endpoint_identity_digest`

批准配置快照：

- `source_provider_config_digest`：preflight 加载的原始、可能包含多个 entries 的 safe config digest；进入 condition/budget/retry/resume guard。

运行时 provenance：

- `prepared_execution_config_digest`：adapter 过滤为单 entry、覆盖 request limits、设置 `max_provider_attempts=1` 后的 config digest。
- 实际 provider request identity、attempt entry/model/provider、response resolved model 和 artifact refs。

不得用一个含义模糊的 `config_digest` 同时表示 source config 和 prepared config。

### 1.2 正式不变量

Planning / condition：

```text
condition.model_cohort_id == cohort.cohort_id
condition.model_cohort_digest == digest(frozen_cohort)
condition.cohort_member_id == selected_member.cohort_member_id
condition.provider_family == selected_member.provider_family
condition.provider_model_id == selected_member.provider_model_id
condition.reasoning_profile_id == selected_member.reasoning_profile_id
condition.provider_config_id == entry_map[member].provider_config_id
condition.model_entry_id == entry_map[member].entry_id
condition.source_provider_config_digest == preflight_member.config_digest
```

Config / entry：

```text
source_config.provider_family == condition.provider_family
source_config.config_digest == condition.source_provider_config_digest
selected_entry.entry_id == condition.model_entry_id
selected_entry.enabled is True
selected_entry.model == condition.provider_model_id
normalize_reasoning_identity(...) == condition.reasoning_profile_id
digest(canonical endpoint identity) == condition.model_endpoint_identity_digest
```

Prepared config / request：

```text
len(prepared_config.entries) == 1
prepared_config.entries[0] == validated selected entry
prepared_config.defaults.max_provider_attempts == 1
request.capability_snapshot.provider_family
  == request.hard_requirements.provider_family
  == condition.provider_family
provider request body.model == selected_entry.model
```

Execution / recovery：

```text
all provider attempts keep the same provider_config_id + selected_entry_id
retry/replacement keeps the original endpoint identity digest
source config drift blocks before provider call
replay performs zero provider calls
```

### 1.3 Reasoning normalization

OpenAI：

- `reasoning_effort` 缺失或 `None` -> `reasoning_profile_id="default"`，request body 不发送该字段。
- `reasoning_effort=""` 或纯空白 -> 配置错误；不得映射为 `default`。
- 非空字符串规范化后比较；正式 GPT member 必须为 `high`。

SiliconFlow：

- cohort v1 的逻辑 `reasoning_profile_id` 保持权威设计中的 `default`。
- `enable_thinking` 作为独立 effective reasoning control 持久化；不能被 `default` 标签吞掉。
- 当前 Qwen JSON-mode 可能隐式生成 `enable_thinking=false`，必须进入 provider request identity provenance。

正式 Experiment 5 对 request overrides 使用 provider-specific allowlist。未知键或可覆盖 model/reasoning 的键在 preflight 阶段阻断。

---

## 2. 实施任务

### Task 1: 新增公共 identity RED 测试

**文件：**

- Create: `tests/experiments/test_paper_model_identity.py`

- [ ] **Step 1.1：写 entry namespace RED 测试**

测试名：

```python
def test_same_entry_id_in_different_provider_configs_is_not_the_same_identity(): ...
```

构造正确 OpenAI/GPT/high config 与错误 SiliconFlow/Qwen config，两者都使用 `entry_id="gpt-entry"`。期望公共 validator 抛出 `PaperModelIdentityMismatch`，reasons 至少包含 provider/config namespace mismatch。

- [ ] **Step 1.2：写同 provider、同 entry、错误 model RED 测试**

```python
def test_fixed_entry_identity_rejects_same_entry_id_with_different_model(): ...
```

期望 reasons 包含 `provider_model_id_mismatch`，不能只靠 source digest 给出不可解释的总失败。

- [ ] **Step 1.3：写 reasoning normalization RED 测试**

```python
def test_reasoning_identity_treats_missing_as_default_and_rejects_empty_string(): ...
```

断言：缺失/`None` 得到 `default`；`""` 和纯空白抛出 `PaperModelIdentityMismatch`；OpenAI `high` 规范化为 `high`。

- [ ] **Step 1.4：写同 provider/model、错误 reasoning RED 测试**

```python
def test_fixed_entry_identity_rejects_reasoning_profile_drift(): ...
```

正确 identity 为 OpenAI/GPT/high，实际 config 为同 entry/model 但 `reasoning_effort="low"`；期望 `reasoning_profile_id_mismatch`。

- [ ] **Step 1.5：写 source config drift / retry RED 测试**

```python
def test_retry_rejects_changed_source_config_digest_before_rebinding_entry(): ...
```

第一次使用批准 config 构建 identity；第二次模拟 retry，仅修改安全 metadata 或非 selected entry，使 source config digest 改变。期望 `source_provider_config_digest_mismatch`。

- [ ] **Step 1.6：运行测试并确认 RED 原因**

Run：

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/test_paper_model_identity.py -q
```

Expected RED：当前没有 `tokenshare.experiments.paper_model_identity`，因此每个测试在加载预期 API 时失败于 `ModuleNotFoundError`。不得把 collection/import typo、fixture path 或真实 provider error 当成有效 RED。

**Task 1 停止点：** 保存 RED 输出；不创建 production module，不修改 adapter/executor。

### Task 2: 实现结构化 endpoint identity 与 condition 完整性

**文件：**

- Create: `src/tokenshare/experiments/paper_model_identity.py`
- Modify: `src/tokenshare/experiments/paper_models.py`
- Test: `tests/experiments/test_paper_model_identity.py`
- Test: `tests/experiments/test_paper_models.py`

- [ ] 定义 `PaperModelIdentityMismatch(ValueError)`，携带稳定 `reasons: tuple[str, ...]`。
- [ ] 定义 `PaperModelEndpointIdentity` 和 `ValidatedModelEndpointBinding` frozen dataclasses。
- [ ] 实现 `normalize_reasoning_identity()`、`build_model_endpoint_identity()`、`validate_fixed_entry_config_identity()`。
- [ ] 给 `PaperExperimentCondition` 增加 `provider_config_id`、`source_provider_config_digest`、`model_endpoint_identity_digest`。
- [ ] formal Exp5 condition 强制完整 identity tuple；Experiment 1-4 和 legacy scripted condition 保持兼容。
- [ ] 运行 Task 1/2 tests，确认 GREEN。

### Task 3: 让 preflight、runner 与 budget 传播同一 identity

**文件：**

- Modify: `src/tokenshare/experiments/paper_model_policy.py`
- Modify: `src/tokenshare/experiments/paper_runner.py`
- Test: `tests/experiments/test_paper_model_policy.py`
- Test: `tests/experiments/test_paper_budget.py`

- [ ] preflight 使用公共 reasoning/identity 构造器，不再保留独立 `_entry_reasoning_profile()` 语义。
- [ ] member plan 写入 sanitized endpoint identity、identity digest 和 source config digest。
- [ ] runner 完整复制 identity fields 到 formal Exp5 conditions。
- [ ] 验证 config drift 会改变 condition/budget digest，并使旧 approval digest 失效。
- [ ] 验证 Experiment 1-4 普通 `fixed_entry` 不要求 cohort identity。

### Task 4: Factorization 与 Lean provider-call 前阻断

**文件：**

- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Test: `tests/experiments/test_factorization_paper_adapter.py`
- Test: `tests/experiments/test_lean_paper_adapter.py`

- [ ] Factorization RED：同名 entry、错误 provider，`transport.calls == []`。
- [ ] Lean simple RED：同 provider/entry、错误 model，`transport.calls == []`。
- [ ] Lean lemma-DAG RED：provider/model 正确、reasoning 错误，`transport.calls == []`。
- [ ] Factorization 在创建 `ArtifactStore` 前调用公共 validator。
- [ ] Lean 在 v1/v2 分支前调用一次公共 validator。
- [ ] `_prepare_config()` 接收 validated binding，只保留一个 enabled entry，并固定 `max_provider_attempts=1`。
- [ ] 保持正确 OpenAI/SiliconFlow adapter regression 全绿。

### Task 5: 固定 failover、retry 与恢复身份

**文件：**

- Modify: `tests/experiments/test_paper_model_identity.py`
- Modify: `tests/experiments/test_factorization_paper_adapter.py`
- Modify: `tests/experiments/test_lean_paper_adapter.py`

- [x] 多-entry source config 过滤后只有 approved selected entry。
- [x] selected entry 返回 503 时，不得 failover 到 sibling model。
- [x] retry 使用原 identity 和同一 source config 可继续；同名 entry 但 config/model/reasoning 漂移时零调用失败。
- [x] replacement attempt 必须复用原 identity object，不从当前 config 重新按字符串解析。
- [x] replay guard 继续保持 zero-call。

### Task 6: 增加通用 executor request-identity provenance

**文件：**

- Modify: `src/tokenshare/executors/ai_api.py`
- Modify: `tests/executors/test_ai_api_executor_success.py`
- Modify: `tests/executors/test_ai_api_executor_failover.py`

- [x] request 的 provider hard requirement 与 config provider 不一致时，返回零调用结构化错误。
- [x] 每个 provider attempt 记录安全 `provider_request_identity.v2`：provider、entry、configured/requested model、reasoning controls、有效 request controls digest。
- [x] 不保存 prompt 正文副本、Authorization header 或 API key。
- [x] executor 不导入 experiment package，不出现 cohort/member/Experiment 5 常量。

### Task 7: Post-call identity audit 与 model execution records

**文件：**

- Modify: `src/tokenshare/experiments/paper_model_identity.py`
- Modify: `src/tokenshare/experiments/paper_models.py`
- Modify: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Modify: adapter tests

- [x] 实现 `validate_fixed_entry_submission_identity()`。
- [x] 定义稳定 `PaperModelExecutionRecord`，同时保存 expected identity 与 actual request/provenance refs。
- [x] 比较实际 configured/requested model/reasoning、attempt provider/entry/model 和 schema-aware raw resolved model；缺失 observed model 不能由请求事实替代。
- [x] resolved model mismatch 在调用后标记 `model_identity_mismatch`、`paper_eligible=false`，并停止该 condition 后续 AI units。
- [x] source provider config digest 与 prepared execution config digest 分字段记录。

### Task 8: 文档、code map 与状态同步

**文件：**

- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/2026-07-13-feat-011-paper-real-ai-experiments-implementation-plan.md`
- Modify: `Doc/TechnicalDocument/2026-06-28-phase-7-ai-api-executor-code-map.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`

- [x] 记录 entry namespace、三类 digest、reasoning normalization、retry/replay identity invariants。
- [x] Phase 7 code map 只记录通用 request identity provenance。
- [x] Phase 8 code map 记录 experiment validator、condition、runner 和 adapter 边界。
- [x] 状态文档记录每轮 RED/GREEN 和最终验证证据；不得在生产修复完成前把缺陷标记为完成。

### Task 9: 最终验证

```powershell
$env:PYTHONPATH='src'

conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/test_paper_model_identity.py -q

conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/test_paper_model_policy.py -q

conda run --no-capture-output -n tokenshare python -m pytest tests/experiments/test_factorization_paper_adapter.py tests/experiments/test_lean_paper_adapter.py -q

conda run --no-capture-output -n tokenshare python -m pytest tests/executors/test_ai_api_config.py tests/executors/test_ai_api_selector.py tests/executors/test_ai_api_executor_success.py tests/executors/test_ai_api_executor_failover.py -q

conda run --no-capture-output -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_lemma_graph_merge_policy.py -q

.\init.ps1

git diff --check
```

最终验证不得调用真实 AI API。

**Task 9 最终证据（2026-07-16）：** identity `11 passed in 0.50s`；model policy `10 passed in 73.01s`；Factorization + Lean adapters `30 passed in 114.93s`；executor config/selector/success/failover `24 passed in 0.91s`；Lean lemma-graph merge `14 passed in 18.06s`。清除 `TOKENSHARE_RUN_SILICONFLOW_SMOKE` 后完整 `.\init.ps1` collected 584 items，结果 `583 passed, 1 skipped in 376.93s`，skip 为默认关闭的真实 SiliconFlow smoke。feature JSON、Markdown fence/checklist、executor experiment-policy boundary、生产范围、窄 secret pattern、未跟踪文件 whitespace、`git diff --check` 和 `git diff --cached --check` 均通过。未调用真实 API；Tasks 1–9 完成。

### Task 10: 请求事实与响应事实 schema v2 根因修复（2026-07-17）

**根因：** `RawModelOutput.v1.model = provider response model or selected entry model` 把 configured/requested fact 与 observed response fact 写入同一字段；response 无 `model` 时，post-call validator 无法判断外层值来自 provider 还是本地 fallback，因而产生 false-positive matched。

**方案比较与决定：**

- 方案 A（只删除 fallback）改动最小，但继续让 `model` 同时暗示 requested/resolved，不能从数据模型阻止再次混淆，不采用。
- 方案 B（新增 requested/resolved 并保留兼容 `model`）可减少 reader 破坏，但兼容字段仍会被误用为 formal evidence，不采用为新写入格式。
- 方案 C（协调 schema v2）明确分开 configured/requested/response/resolved identity，旧 v1 只做保守读取，语义最清晰且可 fail closed；采用。代价是旧 reader 不认识 v2，必须升级 reader 后才能消费新 artifact，这是显式 schema version 变更而不是静默改变 v1。

**已实现字段与信任边界：**

- `configured_model` 只来自已校验 config entry；`requested_model` 只来自实际 provider request body；两者都只能证明请求侧事实。
- provider response 原始 `model` 分类为 `present/missing/null/empty/invalid_type`；仅非空字符串产生 `resolved_model`，其余保持 `null`，禁止从 entry、request、condition 或当前 config 回填。
- `RawModelOutput.v2` 删除通用外层 `model`，写 `configured_model,requested_model,resolved_model,response_model_status` 并保留原始 `raw_response_json`。
- `provider_request_identity.v2` 和 attempt record 分开写 configured/requested model；provenance 不保存 prompt、controls 明文、Authorization、API key 或其他 secret。
- `PaperAttemptResult.model` 继续作为 configured/display compatibility 字段，禁止参与 resolved-model audit。
- `PaperModelExecutionRecord.v2` 写 `requested_model`、nullable `resolved_model`、`response_model_status`。成功或 parse-failed response 缺 observed model 时写 `missing_resolved_model`；不同/未批准 alias 写 `resolved_model_mismatch`；二者均为 audit-stage `model_identity_mismatch`、paper-ineligible，并停止后续 units。
- provider error 无成功 raw response 时写 `identity_status=not_observed,response_model_status=unavailable,resolved_model=null`，保留原 provider failure kind，不把“没有观察机会”伪装成 response identity mismatch。
- replay schema-aware reader 只读历史 raw artifact：v1 忽略含义混杂的外层 `model`，只从 `raw_response_json.model` 保守恢复；v2 校验 configured/requested 必填且 persisted resolved/status 与原始 response 一致；不读当前 config/secret，不重写历史 artifact。
- 展示 fallback 必须在 presentation 层单独计算并标注来源，不能写回 raw/model-execution evidence 或 identity status。

**通用 executor / Experiment 5 边界：** executor 只忠实持久化通用 request/response/provenance facts；experiment identity validator 应用正式 condition 的 missing/mismatch policy；Factorization、Lean simple、Lean lemma-DAG adapter 执行 stop gate；report/replay 只消费持久化 evidence。cohort/member/Experiment 5 policy 未下沉到 executor。

**精确代码与测试文件：**

- Source：`src/tokenshare/executors/ai_api_artifacts.py`、`ai_api_transport.py`、`ai_api.py`、`ai_api_replay.py`；`src/tokenshare/experiments/paper_model_identity.py`、`paper_models.py`、`factorization_paper_adapter.py`、`lean_paper_adapter.py`。
- Tests：`tests/executors/test_ai_api_artifacts.py`、`test_ai_api_transport.py`、`test_ai_api_openai_transport.py`、`test_ai_api_executor_success.py`、`test_ai_api_executor_failover.py`、`test_ai_api_replay_guard.py`；`tests/experiments/test_paper_model_identity.py`、`test_paper_models.py`、`test_factorization_paper_adapter.py`、`test_lean_paper_adapter.py`。

**RED/GREEN 证据：** transport/executor/raw-schema 初始 `28 failed, 12 passed`，实现 v2 后 `40 passed`；identity/record/provider-error 为 `9 failed, 34 passed` 后 `43 passed`；三个 adapter missing-model stop gate 为 `3 failed, 30 passed` 后通过，补齐正常 formal paths 后 `35 passed in 63.47s`；replay reader 为 `1 failed, 2 passed` 后 `3 passed`；v2 schema hardening 为 `2 failed, 4 passed` 后 `6 passed`。executor 合并回归 `61 passed`，identity/schema 合并回归 `107 passed in 0.90s`，model policy + Lean merge `24 passed in 36.20s`。均使用 fake/scripted transport，未调用真实 API。

**最终验证：** 必须执行用户指定的 transport、executor、identity、adapter、model-policy、Lean merge targeted suites，随后执行 `.\init.ps1`、对抗复现、`git diff --check`、secret/临时文件/范围审计。若 response 缺 model 仍被标 matched，禁止完成或提交。

**Task 10 最终证据（2026-07-17）：** review hardening 追加三轮 RED/GREEN：`6 failed, 48 passed` → `54 passed`、`5 failed, 4 passed` → `9 passed`、`1 failed, 5 passed` → `6 passed`，封闭 usage requested-model 来源、provenance body/ref 版本、record 内部一致性、错误 requested-model 事实保留、wrapper schema、replay ref/body version 和 matched requested-model invariant；独立 code review 最终确认无 Critical/Important。用户指定 targeted 结果为 transport `19 passed`、executor/failover/replay `31 passed`、identity `21 passed`、Factorization/Lean adapters `35 passed`、model policy `10 passed`、Lean merge `14 passed`；额外 raw/record schema `37 passed`。系统临时目录对抗复现输出 response 无 `model`、`resolved_model=null`、`identity_status=model_identity_mismatch`、`mismatch_reasons=[missing_resolved_model]`、`paper_eligible=false`、fake calls 1。清除真实 smoke opt-in 后完整 `.\init.ps1` collected 638 items，结果 `637 passed, 1 skipped in 247.63s`；skip 为默认关闭的真实 SiliconFlow smoke。`git diff --check` / cached check、feature JSON、Markdown fences、executor boundary、ambiguous fallback、changed/untracked path、untracked whitespace、高置信 secret 和 nothing-staged 审计通过；changed/untracked 集合未包含 local config、outputs、cache、temp 或 secret。未调用真实 API，未暂存或提交。

---

## 3. 兼容性约束

- Experiment 1-4：普通 `fixed_entry` / baseline condition 不强制 Experiment 5 cohort fields。
- Scripted regression：继续允许非正式 helper 只传显式 entry ID；结果仍为 `paper_eligible=false`。
- Legacy strong/weak/mixed helper：不进入 formal schema，但本修复不删除其 regression compatibility。
- 正确 SiliconFlow/OpenAI 路径：保持现有 request/provenance 行为。
- Lean simple 与 lemma-DAG：共享外层 validator，禁止复制两套 identity 规则。
- AIAPIExecutor：只承担通用 request/config/provenance invariant，不解释 cohort policy。
- Future runner/pilot：必须消费批准的 identity object；不得从当前 config 重新按 entry 字符串解析。

## 4. 已批准决策

1. 采用结构化 identity/digest + experiment-layer 公共 validator + 通用 executor request provenance，不采用只在 adapter 增加散落字符串比较的方案。
2. Source provider config drift 使用严格策略：任一 safe config 内容变化都需要重新 plan/approve；endpoint identity digest 仅用于解释语义变化，不放宽批准快照。
3. SiliconFlow cohort v1 暂保留逻辑 `reasoning_profile_id="default"`，同时单独冻结和审计 `enable_thinking` 等 effective controls；如论文口径决定把 `enable_thinking=false` 命名为独立 profile，必须生成新 cohort version，不能静默改写 v1。
4. OpenAI response resolved model 默认 exact match；若 provider 只返回版本化 alias，必须在新 cohort/preflight evidence 中显式批准 alias，不允许字符串前缀猜测。

## 5. 范围外

- 不修改 `tokenshare.core`。
- 不修改 Lean catalog、`SplitRules.lean`、Lean split/merge 规则。
- 不修复 Lean environment digest 的绝对路径可移植性。
- 不调用真实 AI API，不生成正式论文实验结果。
- 不顺手实现尚不存在的完整 formal runner/resume orchestration；只提供其必须复用的 identity guard。
