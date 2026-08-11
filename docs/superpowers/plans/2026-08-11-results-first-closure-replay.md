# Results-First Closure Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不重新 acquisition、provider dispatch 或执行129个 Exp1--4协议根的前提下，用同一 production resume/metric/terminal代码在scratch root快速重放收口，并在下一次完整smoke前穷尽Exp5与指标双域问题。

**Architecture:** 先修正 persisted attempt 的显式current-provider count优先级，并提供可对历史suite做只读扫描的typed preflight。未来完整run在协议执行前持久化digest-bound typed replay snapshot；收口失败后复制suite/checkpoint到scratch，用该snapshot调用现有 `execute_paper_formal_suite(..., resume=True)`，由completed task keys保证零redispatch并重建canonical closure。最终完整145-root smoke仍是唯一验收。

**Tech Stack:** Python 3.12、pytest、dataclasses、pickle+SHA-256 descriptor、JSON/JSONL、SQLite、TokenShare FormalEvidenceStore/EventLedger/ArtifactStore。

---

### Task 1: 修正 current-provider attempt 的显式字段优先级

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Modify as required by producer truth: `src/tokenshare/experiments/factorization_paper_adapter.py`
- Modify as required by producer truth: `src/tokenshare/experiments/lean_paper_adapter.py`
- Test: `tests/experiments/test_paper_formal_runner.py`
- Test: `tests/experiments/test_results_first_full_plan_smoke_e2e.py`

- [ ] **Step 1: 写参数化RED，区分trace显式0、online显式1与legacy缺字段**

```python
@pytest.mark.parametrize(
    ("attempt", "expected"),
    (
        ({"provider_attempt_count": 0, "provider_attempt_index": 3}, 0),
        ({"provider_attempt_count": 1, "provider_attempt_index": 1}, 1),
        ({"provider_attempt_index": 1, "provider": "deepseek"}, 1),
    ),
)
def test_persisted_provider_attempt_count_prefers_explicit_current_count(
    attempt: dict[str, object], expected: int
) -> None:
    assert formal_runner._persisted_provider_attempt_count(attempt) == expected
```

同时新增负例：显式count为负数、布尔值、online producer声称真实dispatch但写0、source-only identity混入current字段时必须fail-closed。

- [ ] **Step 2: 运行RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_paper_formal_runner.py -k "persisted_provider_attempt_count or trace_current_provider or online_provider_attempt"
```

Expected: 旧实现把第一项算成1，至少1个目标测试FAIL。

- [ ] **Step 3: 最小实现显式字段优先，legacy仅字段缺失时回退**

```python
def _persisted_provider_attempt_count(attempt: Mapping[str, Any]) -> int:
    if "provider_attempt_count" in attempt:
        value = attempt["provider_attempt_count"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("persisted provider attempt count is invalid")
        return value
    return _legacy_persisted_provider_attempt_count(attempt)
```

真实online producer必须在transport dispatch后持久化 `provider_attempt_count=1`；trace/bank消费必须持久化0。不得用ordinal、fault count、replacement count或source provider identity覆盖显式0。

- [ ] **Step 4: 运行GREEN与final-10只读实证**

Run目标pytest后，对只读final-10执行preflight扫描：129 CURRENT、817 attempts、显式count字段817/817、current sum=0、legacy row=0；同时保留ordinal-positive=177作为已排除旧错误证据。

- [ ] **Step 5: 不提交源代码**

本Task仅记录RED/GREEN输出；遵守Task6统一审查后单次提交策略，不stage、不commit。

### Task 2: 提供历史suite的快速只读closure preflight

**Files:**
- Modify: `src/tokenshare/experiments/run_paper_pipeline.py`
- Test: `tests/experiments/test_run_paper_pipeline.py`

- [ ] **Step 1: 写RED，要求从persisted CURRENT而非terminal自报值重算**

```python
def test_trace_closure_preflight_recomputes_current_provider_from_attempt_facts(
    completed_trace_suite: Path,
) -> None:
    result = pipeline.audit_results_first_trace_closure_source(
        suite_root=completed_trace_suite
    )
    assert result.condition_count == 129
    assert result.root_count == 129
    assert result.current_provider_attempt_count == 0
    assert result.legacy_attempt_row_count == 0
```

补充缺checkpoint、重复root、CURRENT/generation漂移、显式count非法、source/current混用负例。

- [ ] **Step 2: 运行RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run --no-capture-output -n tokenshare python -m pytest -q tests/experiments/test_run_paper_pipeline.py -k "trace_closure_preflight"
```

Expected: `audit_results_first_trace_closure_source` 尚不存在。

- [ ] **Step 3: 实现frozen typed结果与只读扫描**

```python
@dataclass(frozen=True)
class ResultsFirstTraceClosurePreflight:
    condition_count: int
    root_count: int
    checkpoint_count: int
    attempt_count: int
    current_provider_attempt_count: int
    legacy_attempt_row_count: int
    checkpoint_inventory_digest: str


def audit_results_first_trace_closure_source(
    *, suite_root: Path
) -> ResultsFirstTraceClosurePreflight:
    ...
```

实现必须复用FormalEvidenceStore manifest验证、checkpoint inventory digest、EventLedger hash chain和 `_persisted_provider_attempt_count`；不信任 `formal_runner_result.json.provider_attempt_count`。

- [ ] **Step 4: 运行GREEN并在final-10上执行8秒诊断**

Expected: `129/129 checkpoints`、`817 attempts`、`current=0`、`legacy=0`；原suite文件hash不变。

- [ ] **Step 5: 不提交源代码**

记录验证证据，等待两阶段review。

### Task 3: 持久化digest-bound typed closure replay snapshot

**Files:**
- Modify: `src/tokenshare/experiments/run_paper_pipeline.py`
- Modify: `tests/experiments/test_results_first_full_plan_smoke_e2e.py`
- Test: `tests/experiments/test_run_paper_pipeline.py`

- [ ] **Step 1: 写snapshot round-trip与tamper RED**

```python
def test_results_first_closure_replay_snapshot_round_trips_typed_authority(
    representative_authority: pipeline.ResultsFirstExecutionAuthority,
    tmp_path: Path,
) -> None:
    ref = pipeline.persist_results_first_closure_replay_snapshot(
        authority=representative_authority,
        output_root=tmp_path,
    )
    loaded = pipeline.load_results_first_closure_replay_snapshot(
        output_root=tmp_path,
        expected_ref=ref,
    )
    assert loaded.coverage_digest == representative_authority.coverage.coverage_digest
    assert loaded.full_budget_digest == representative_authority.full_budget.budget_digest
```

参数化篡改pickle bytes、descriptor digest、dispatch/catalog/budget/coverage/semantic inventory/hard-limit lineage，全部fail-closed。

- [ ] **Step 2: 运行RED**

Expected: snapshot类型与persist/load函数缺失。

- [ ] **Step 3: 实现snapshot、descriptor与严格loader**

```python
@dataclass(frozen=True)
class ResultsFirstClosureReplaySnapshot:
    schema_version: str
    full_dispatch_plans: tuple[PaperExperimentDispatchPlan, ...]
    catalog_manifest: PaperInputCatalogManifest
    full_budget: PaperBudgetResult
    ai_api_configs: Mapping[str, AIAPIExecutorConfig]
    coverage: FormalExecutionCoverage
    semantic_inventory_plan: RepresentativeSemanticInventoryPlan
    execution_budget_projection: PaperExecutionBudgetProjection
    hard_limits: Mapping[str, Any]
    suite_id: str
    snapshot_digest: str
```

使用受信本地pickle handle与JSON descriptor；descriptor记录pickle SHA-256及dispatch/catalog/budget/coverage/inventory/projection/hard-limit digests。loader要求exact type并与scratch suite的 `paper_dispatch_plans.json`、`input_catalog_manifest.json`、`run_budget.json` body digest逐项相等。

- [ ] **Step 4: E2E在长执行前持久化snapshot**

在materialized bundle绑定到service之后、acquisition/trace之前写入 `execution/closure-replay-snapshot/`。snapshot写入provider_calls必须为0，且不得包含secret。

- [ ] **Step 5: 运行round-trip、secret-scan和py_compile**

Expected: round-trip GREEN；descriptor/pickle/event/config中不出现注入secret；`git diff --check`通过。

### Task 4: scratch-root production resume快速重放

**Files:**
- Modify: `src/tokenshare/experiments/run_paper_pipeline.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_run_paper_pipeline.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写零dispatch production resume RED**

```python
def test_closure_replay_uses_production_resume_without_redispatch(
    completed_results_first_trace: Path,
    replay_snapshot_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        formal_runner,
        "dispatch_paper_case",
        lambda **_: pytest.fail("closure replay redispatched a protocol root"),
    )
    result = pipeline.resume_results_first_trace_closure_from_snapshot(
        source_trace_root=completed_results_first_trace,
        snapshot_root=replay_snapshot_root,
        scratch_output_root=tmp_path / "scratch",
        response_bank_resolver=_resolver_for_fixture(),
        transport=_FailFastTransport(),
    )
    assert result.task_count == 129
    assert result.provider_attempt_count == 0
```

- [ ] **Step 2: 运行RED**

Expected: resume helper或runner resume seam缺失。

- [ ] **Step 3: 实现安全scratch copy与派生清理白名单**

只允许复制source suite、sibling canonical checkpoint root和必要bank resolver refs到全新scratch。派生清理白名单精确为scratch内的 `formal_runner_result.json`、`traceability_replay_input_root.handle.pickle` 与 sibling `exp1-exp4-trace.traceability_replay_inputs/`；删除前逐个验证resolved path位于scratch或其明确sibling内。若 `suite_manifest.json` 仍含旧traceability ref，只允许在scratch移除该单一extra ref，随后调用 `FormalEvidenceStore(scratch)._refresh_evidence_manifest()`。不得删除condition results、experiment/suite manifests、CURRENT、generations、ledger、artifacts、PENDING或canonical checkpoints；若PENDING存在，必须由现有resume repair协调，不能手工删除。

- [ ] **Step 4: 用snapshot重建trace context并调用同一runner resume**

```python
def resume_results_first_trace_closure_from_snapshot(
    *,
    source_trace_root: Path,
    snapshot_root: Path,
    scratch_output_root: Path,
    response_bank_resolver: object,
    transport: object,
) -> PaperSuiteResult:
    snapshot = load_results_first_closure_replay_snapshot(...)
    preflight = audit_results_first_trace_closure_source(
        suite_root=source_trace_root
    )
    return _run_results_first_formal_subset(
        authority=snapshot.to_execution_authority(scratch_output_root),
        conditions=snapshot.trace_conditions,
        output_root=scratch_output_root,
        transport=transport,
        real_transport=False,
        trace_context=_TRACE_CONTEXT_BUILDER(
            inventory_plan=snapshot.semantic_inventory_plan,
            resolver=response_bank_resolver,
        ),
        suite_id=snapshot.suite_id,
        resume=True,
    )
```

为 `_run_results_first_formal_subset` 增加typed `resume: bool=False`并原样传给formal executor。恢复前后比较129个CURRENT/generation/checkpoint digests不变；网络、adapter、acquisition dispatch任一发生即失败。

- [ ] **Step 5: 验证完整收口输出**

断言status非blocked、129固定分母、Exp1/2/3/4=`12/6/81/30`、current provider0、8 official metric routes、source/current roles、correctness/completion、latency/token/cost、missing_count/reason全部由production materializer生成。目标时长小于15分钟。

### Task 5: Exp5 16条件与atomic ledger快速组合

**Files:**
- Test only unless a failure proves production defect: `tests/experiments/test_paper_formal_callbacks.py`
- Test only unless a failure proves production defect: `tests/experiments/test_run_paper_pipeline.py`

- [ ] **Step 1: 一次运行全部Exp5正式focused nodes**

覆盖full prepared inventory binding、runtime identity drift、body/config/request controls、intent ambiguous/no redispatch、missing usage upper-bound、success/provider failure、pre-intent abort、crash reconcile、16 conditions、四endpoint、usage delta、invalid terminal与missing spend。

- [ ] **Step 2: 增加一个16-condition聚合断言（若现有测试未同时覆盖）**

```python
assert len(exp5_terminal.condition_results) == 16
assert {row.endpoint_id for row in exp5_terminal.condition_results} == {
    "glm-5.2",
    "qwen3-14b",
    "minimax-m2.5",
    "deepseek-v3",
}
assert ledger_audit.ambiguous_count == 0
```

每个offline request只回答一次；错误回答不得重采或按correctness筛选。

- [ ] **Step 3: 运行组合并审计ledger**

Expected: current/total calls、spend、missing usage、upper bounds与terminal一致；任何unknown identity在secret/transport前失败。

### Task 6: 两阶段审查、快速门与最终完整smoke

**Files:**
- Review all Task6 production/test/docs changes
- Update after final success: `progress.md`
- Update after final success: `feature_list.json`
- Update after final success: `session-handoff.md`
- Update after final success: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`

- [ ] **Step 1: 独立规格审查**

审查fast replay是否使用同一production runner/metric/terminal；是否零redispatch；是否保持full/representative仅规模差；修复所有Critical/Important并复审至APPROVE。

- [ ] **Step 2: 独立质量审查**

审查scratch安全、pickle/descriptor digest、source/current provider域、legacy兼容、Windows路径和测试污染；修复所有Critical/Important并复审至APPROVE。

- [ ] **Step 3: 运行快速门**

顺序运行final-10只读preflight、snapshot round-trip、scratch production resume、Exp5组合、py_compile、secret scan、`git diff --check`。任何失败只重跑对应快速层，不启动完整E2E。

- [ ] **Step 4: 仅在快速门全绿后运行一次fresh完整offline E2E**

Expected: 145 roots、Exp1--5=`12/6/81/30/16`、359动态offline acquisition（以bundle为准）、Exp1--4 current provider0、Exp5四endpoint实际dispatch、terminal非blocked、所有指标与固定分母真实。

- [ ] **Step 5: 更新harness文档并统一提交Task6**

只在完整offline GREEN、两轮review通过、保护文件diff-check和secret scan通过后更新四个harness文档并创建一个Task6代码提交。不得把 `local/task6-full-plan-e2e-*` 或用户三个matrix8文件纳入commit。

- [ ] **Step 6: 主Agent执行最终真实付费smoke**

重新检查keys仅报告SET/UNSET；fresh authority/bundle/output root；hard budget/atomic ledger；30分钟heartbeat；acquisition terminal后运行Exp1--4 bank trace与Exp5 online；ambiguous只reconcile。最终审计145固定分母、四endpoint、fault/rate/death/ablation、Factor verifier、Lean checker和全部指标。
