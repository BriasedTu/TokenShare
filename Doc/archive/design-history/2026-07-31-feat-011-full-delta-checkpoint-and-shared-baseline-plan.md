# FEAT-011 Full Delta Checkpoint and Shared Baseline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** 把 formal condition checkpoint 改为 generation v3 immutable root/event delta chain，并在终态流式压成snapshot，使full-run写量O(N)、完整outcome常驻≤1、adapter已落盘或已commit的root在resume时都不重调provider，同时把Exp3 shared Exp1 reference降为一次验证索引加O(1) lookup。

**Architecture:** 新建 `paper_formal_checkpoint.py`专管v3 chain schema、逻辑record迭代和SQLite snapshot compaction；`FormalEvidenceStore`负责PENDING/CURRENT/condition/evidence manifest原子发布与v1/v2兼容；runner先完整load canonical，再分类/salvage adapter working tree，并逐root实验投影、在线汇总、checkpoint、O(1) token cleanup，terminal tail events写受约束event-only delta后compact。正式下游只消费v3 snapshot，running resume通过统一logical reader消费delta chain。

**Tech Stack:** Python 3.12、stdlib `json`/`pathlib`/`hashlib`/`sqlite3`/`tempfile`/`shutil`、pytest、`tracemalloc`。

---

### Task 1：冻结 generation v3 schema 与纯链 validator

**Files:**
- Create: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Create: `tests/experiments/test_paper_formal_checkpoint.py`

- [ ] **Step 1: 写generation v3 manifest shape与delta/snapshot互斥规则RED。**

测试构造first root delta、child root delta、可为空events的condition-closure delta和standalone snapshot；参数化缺key/额外key、delta compacted字段非空、snapshot parent非空、delta role/ordinal/anchor非法、event-only非event文件有record或第二条closure、文件set非六项，断言`ValueError`。

```python
@pytest.mark.parametrize("mutation", [
    lambda body: body.pop("generation_kind"),
    lambda body: body.__setitem__("extra", True),
    lambda body: body.__setitem__("parent_generation_id", "missing-parent"),
])
def test_snapshot_manifest_shape_fails_closed(tmp_path, mutation):
    body = valid_v3_snapshot_manifest(tmp_path)
    mutation(body)
    with pytest.raises(ValueError):
        validate_v3_generation_manifest(tmp_path, body)
```

- [ ] **Step 2: 运行RED。**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:PYTHONIOENCODING='utf-8'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_checkpoint.py -k "manifest_shape" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-schema-red
```

预期：模块或validator尚不存在而FAIL。

- [ ] **Step 3: 实现exact schema parser与typed descriptor。**

```python
V3_GENERATION_SCHEMA = "tokenshare.paper_checkpoint_generation.v3"
V3_RUN_FILES = (
    "run_manifest.json",
    "per_task_results.jsonl",
    "per_attempt_results.jsonl",
    "fault_injections.jsonl",
    "events/event_log.jsonl",
    "artifacts/artifact_index.jsonl",
)

@dataclass(frozen=True, slots=True)
class V3GenerationDescriptor:
    generation_id: str
    generation_kind: Literal["delta", "snapshot"]
    delta_role: Literal["root_outcome", "condition_tail_events"] | None
    selection_ordinal: int | None
    anchor_task_id: str | None
    manifest_digest: str
    parent_generation_id: str | None
    parent_generation_manifest_digest: str | None
    compacted_chain_digest: str | None
    compacted_generation_count: int
```

validator对exact keys、id/path、kind-specific nullability、六文件path/hash/size/record_count逐项检查。

- [ ] **Step 4: 写parent missing/tamper/cycle/duplicate-task RED。**

用3条乱序commit的root delta加1条event-only delta构造valid chain，再分别删除parent目录、修改parent manifest bytes、让head parent指回child、重复task id、第二条tail delta、错误anchor，断言fail closed且错误类别稳定。

- [ ] **Step 5: 实现oldest→newest chain iterator。**

```python
def iter_v3_delta_chain(run_root: Path, head: V3GenerationDescriptor,
                        *, expected_root_count: int) -> Iterator[V3GenerationDescriptor]:
    visited: set[str] = set()
    reversed_chain: list[V3GenerationDescriptor] = []
    current = head
    while True:
        if current.generation_id in visited:
            raise ValueError("checkpoint generation chain contains a cycle")
        visited.add(current.generation_id)
        reversed_chain.append(current)
        if len(reversed_chain) > expected_root_count + 1:
            raise ValueError("checkpoint generation chain exceeds frozen root count")
        if current.parent_generation_id is None:
            break
        current = load_exact_parent(run_root, current)
    yield from reversed(reversed_chain)
```

chain logical validator另用set验证task/attempt/event/fault/artifact identity、context和冻结selection唯一子集；running不要求commit顺序为selection前缀，terminal compactor按selection ordinal重建。

- [ ] **Step 6: 运行Task 1 tests至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_checkpoint.py -k "manifest_shape or parent or cycle or duplicate_task or delta_chain" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-schema-green
```

```powershell
git add src/tokenshare/experiments/paper_formal_checkpoint.py tests/experiments/test_paper_formal_checkpoint.py
git commit -m "feat: define formal checkpoint generation v3"
```

### Task 2：Immutable delta PENDING→CURRENT publication与repair

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_checkpoint.py`

- [ ] **Step 1: 写root delta publication各crash seam RED。**

root与condition-closure两种publication均参数化`target_written`、`intent_written`、`current_written`、`condition_manifest_written`、`inventory_refreshed`。调用resume repair后断言：有intent时exact target成为CURRENT且PENDING删除；无intent target保持orphan且不晋升；第三CURRENT值fail closed。

```python
@pytest.mark.parametrize("stage", [
    "intent_written", "current_written",
    "condition_manifest_written", "inventory_refreshed",
])
def test_v3_root_delta_repair_is_idempotent(tmp_path, monkeypatch, stage):
    store, condition = initialized_v3_store(tmp_path)
    monkeypatch.setattr(store, "_checkpoint_publication_hook",
                        raise_at(stage))
    with pytest.raises(SimulatedCrash):
        store.checkpoint_root(**root_checkpoint(condition, "case-1"))
    FormalEvidenceStore(tmp_path).repair_interrupted_checkpoint_publication()
    assert logical_task_ids(tmp_path, condition) == ("case-1",)
    assert not pending_path(tmp_path, condition).exists()
```

- [ ] **Step 2: 运行RED，确认旧v2 snapshot重写语义失败。**

- [ ] **Step 3: 增加PENDING v2 exact shape与两种delta target writer。**

PENDING必须包含publication kind、target/prior manifest+CURRENT digests和condition identity。`root_delta`带task/ordinal；`condition_tail_events`带null task与anchor/event digest。target generation只写当前root或closure的六文件；不读取或复制prior JSONL。

- [ ] **Step 4: 修改`checkpoint_root()`走v3 delta CAS。**

```python
with self._lock, _exclusive_output_root_lock(self.output_root):
    prior = load_current_head_if_present(run_root)
    ensure_task_not_already_committed(prior, task_body)
    target = write_v3_delta_generation(...)
    write_root_delta_intent(run_root, prior=prior, target=target, task_id=task_id)
    compare_and_swap_current(run_root, prior=prior, target=target)
    write_running_condition_manifest_v2(...)
    self._refresh_evidence_manifest(run_root=run_root)
    pending_path.unlink()
```

不再删除parent generation。exact duplicate terminal task返回幂等结果；内容或artifact drift拒绝。

- [ ] **Step 5: 实现repair对prior/target二态恢复。**

无PENDING orphan不晋升；CURRENT既非prior也非target、target parent不匹配或task drift均抛`ValueError`。

- [ ] **Step 6: 写并通过成功/失败/timeout/worker_died/not_started terminal duplicate tests。**

所有terminal task进入logical completed keys；相同重放provider spy为0，不同重放fail closed。

- [ ] **Step 7: 让`checkpoint_root()`返回O(1) validated commit token。**

token绑定condition/task/target generation id+manifest digest/CURRENT digest，只供同一进程随后exact adapter cleanup；cleanup验证head delta中刚提交task，不调用整链`_validate_run()`，也不再次refresh evidence manifest。spy断言500次checkpoint只有500次head验证、0次cleanup chain traversal/static manifest rehash。

- [ ] **Step 8: 运行Task 2 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_evidence.py -k "v3 and (delta or pending or current or duplicate or terminal_task)" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-delta
```

### Task 3：Evidence manifest v2递归delta/snapshot与v1/v2只读兼容

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写manifest v2 delta递归闭包RED。**

两condition各三delta；spy断言checkpoint condition A只hash A的新delta/condition manifest，不访问B或suite `rglob`。load必须沿A/B parent链校验六文件与artifact payload。

- [ ] **Step 2: 写tamper矩阵RED。**

分别覆盖missing parent、parent digest、cycle、delta额外文件声明、duplicate task/path/ref、CURRENT/head mismatch、condition logical count/digest/size drift、artifact payload hash/size，全部fail closed。

- [ ] **Step 3: 实现condition manifest v2增量字段和targeted inventory entry。**

running delta的count/size/commit-chain digest从validated prior manifest和新delta增量计算，`logical_records_digest`保持null直到selection-ordered snapshot；不遍历旧chain热路径。load仍完整递归chain。

- [ ] **Step 4: 写v1/v2历史目录字节不变RED。**

对v1 replay/report/resume写入口和v2 load/replay分别做递归`path→(bytes,mtime)`快照；closed v1 resume在disk preflight/finalization repair前只读load并返回，incomplete v1在任何写/provider前拒绝；两者`disk_usage`和provider spies均为0。纯`replay_paper_formal_suite()`同样保持不变。

- [ ] **Step 5: 实现schema分派。**

v1沿原exact file index只读；历史v1/v2 generation沿原validator；新manifest v2根据condition manifest schema进入v3 chain/snapshot validator。v3 adapter/compatibility trees明确是non-canonical，不出现在顶层递归闭包；archive/stale compatibility repair按schema分派，不再硬读v1 `files` 或用suite `rglob` exact-set判v2现场。任何v1 refresh/repair/report write在首个写操作前拒绝。

- [ ] **Step 6: 运行Task 3 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -k "manifest_v2 and (delta or snapshot or parent or v1 or v2 or read_only or tamper)" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-manifest
```

### Task 4：SQLite terminal snapshot compaction

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_checkpoint.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`

- [ ] **Step 1: 写terminal条件、逐字段等价和compaction crash RED。**

完整3-root乱序commit chain加唯一condition-closure delta可compact并按冻结selection order输出；缺root/closure缺失/selection identity错位/第二条closure拒绝。正常成功、负面terminal、budget/exception、worker death/not_started、blocked-suite补齐和Exp5 fail-stop的最后closure都自动触发。参数化`compaction_intent_written`、`current_snapshot_written`、`condition_terminal_written`、`inventory_terminal_written`，repair后CURRENT均指snapshot且parents只在commit后可删。

- [ ] **Step 2: 写SQLite初始化/异常清理RED。**

schema初始化、chain ingest、snapshot writer分别注入异常；断言connection先关闭、temp dir删除、原始异常不被Windows cleanup错误覆盖。

- [ ] **Step 3: 实现流式compactor。**

```python
with TemporaryDirectory(prefix=".tokenshare-v3-compact-",
                        dir=str(suite_root.parent)) as work:
    connection = sqlite3.connect(Path(work) / "records.sqlite3")
    try:
        initialize_compaction_schema(connection)
        for generation in iter_v3_delta_chain(...):
            ingest_delta_records(connection, generation, frozen_order)
        write_snapshot_files_from_cursors(connection, target_root)
        verify_snapshot_logical_equivalence(connection, target_root)
    finally:
        connection.close()
```

SQLite unique indexes覆盖task/attempt/event/fault/artifact merge identities；保存selection ordinal并与chain ingest order分离；snapshot文件按冻结task order逐行写、增量hash/record_count并原子replace，condition-tail `MERGE_GATE_*` events固定排在最后root既有events之后。

- [ ] **Step 4: 实现condition closure与terminal_snapshot PENDING publication。**

EvidenceStore提供`close_condition(events=...)`写唯一`condition_tail_events` delta；events可空或只含validated `MERGE_GATE_*`。每次root/closure checkpoint commit后，若terminal root分母已满且closure精确一条，立即触发snapshot，而不依赖scheduler正常return。snapshot manifest parent=null，提交compacted prior head/digest、chain digest/count。CURRENT切换、terminal condition manifest、inventory刷新、PENDING删除后，才按chain inventory删除exact old generation dirs。

- [ ] **Step 5: 写terminal immutability与orphan cleanup tests。**

snapshot CURRENT后新task/record drift拒绝；COMMITTED后清parents崩溃可重入；未被intent或chain引用的target snapshot不晋升。

- [ ] **Step 6: 写500-root O(N) bytes与compaction memory测试。**

使用小record但真实500 roots，writer计数断言：

```python
assert bytes_500 <= bytes_50 * 11 + FIXED_SNAPSHOT_ALLOWANCE
assert bytes_500 < terminal_snapshot_bytes * 4
assert peak_500 <= peak_50 * 1.25 + 32 * 1024**2
```

旧实现预期因约N/2倍snapshot重写而失败；新实现GREEN。

- [ ] **Step 7: 运行Task 4 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_evidence.py -k "snapshot or compaction or write_bytes or terminal_immutability" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-compact
```

### Task 5：Runner逐root在线checkpoint与compact accumulator

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_callbacks.py`
- Modify: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_callbacks.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写顺序与max-live-root RED。**

500个synthetic outcomes各带大payload及析构/live counter；断言调用顺序为dispatch/salvage→experiment projection→accumulate→checkpoint→token cleanup→release，完整outcome同时存活≤1，scheduler内部与返回strategy均不含outcome tuple、raw records或model payload集合。

- [ ] **Step 2: 写compact accumulator等价RED。**

把同一fixture分别送入旧批量reference helper和新accumulator，断言min start/max end、per-case parallel slots最大值、critical path sum/availability、dependency edges、provider latency三态/status/errors、throughput和paper metrics逐字段相等；再把future完成顺序打乱，结果仍按冻结selection order。

- [ ] **Step 3: 实现`ScheduledConditionAccumulator`。**

它只保存数字、ids、dependency edge compact dict、interval boundaries和stable scheduler event摘要，不保存task/attempt/artifact/adapter_result/raw response/model execution records。

```python
for case_id in case_ids:
    outcome = execute_case(case_id, worker_count)
    projected = apply_experiment_runtime(case_id, outcome)
    accumulator.observe(case_id, projected)
    continue_running = consume_case(case_id, projected)
    del projected, outcome
    if not continue_running:
        break
return accumulator.finish()
```

- [ ] **Step 4: runner统一在consume_case里checkpoint。**

Exp1–5全部先做experiment runtime投影；Exp5 fail-stop后未启动roots逐个写`not_started` terminal delta但不调用provider。scheduler末尾调用`close_condition()`，把才确定的`MERGE_GATE_*` events（可为空）写唯一受约束event-only closure delta，绝不回写last-root delta；EvidenceStore在最后一个必要commit内自动compact再audit result。normal、负面terminal、blocked-suite补齐、budget/exception、worker death、Exp5 early-stop都测closure精确一次且event不丢、不重。

- [ ] **Step 5: 写adapter working-tree分类与salvage crash matrix RED。**

构造`NO_CANONICAL_NO_WORKTREE`、`CANONICAL_COMMITTED_EXACT`、`ADAPTER_COMPLETE_UNCOMMITTED`、`ADAPTER_PARTIAL_OR_AMBIGUOUS`、`CANONICAL_WORKTREE_CONFLICT`。完整tree覆盖adapter最后文件后、projection中、target无intent、PENDING各阶段、CURRENT后cleanup前；resume provider spy均为0并得到同一canonical task。partial/conflict目录bytes+mtime不变、显式blocked、无negative task伪造。特别覆盖canonical已有case-1时仍salvage固定path里的case-2，不允许旧archive helper忽略。

- [ ] **Step 6: 实现load-first salvage顺序。**

`execute_paper_formal_suite()`在provider/callback前先做一次完整`FormalEvidenceStore.load()`；复用其validated terminal task-key set清理exact committed trees，再逐冻结case验证完整adapter六文件、provider/request provenance和payload hashes，重建持久化adapter result，重做纯确定性experiment projection并checkpoint。v3 archive只在salvage分类后处理已证明不属于冻结case的目录；partial/ambiguous永不移动或覆盖。

- [ ] **Step 7: 集成C O(1) cleanup。**

delta PENDING删除后使用`checkpoint_root()`返回的validated commit token调用exact adapter cleanup；token必须核对CURRENT digest，working tree仍要逐文件核内容/provenance与canonical一致，不能只凭terminal task key+path删除。不得每root重走logical chain或refresh manifest。resume用开头一次full load的冻结task set批量cleanup；partial/conflict两边保留并blocked。`_checkpoint_adapter_result`、`_checkpoint_exception`、`_checkpoint_budget_exhausted`全部覆盖；checkpoint/materialization/repair失败保留。copytree spy证明formal不发布compatibility view。

- [ ] **Step 8: 写每个root crash后的zero-retry与O(N) validation RED。**

参数化成功、失败、timeout、worker_died、not_started；在第1/250/500 root CURRENT后崩溃，resume provider spy只接收真正既无canonical也无完整working tree的roots，usage不重复。500-root spy断言resume只full-load一次，在线每root只验head/token，不产生N次full-chain validation或cleanup refresh。

- [ ] **Step 9: 运行Task 5 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_callbacks.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -k "online_checkpoint or accumulator or max_live_root or zero_retry or adapter_tree or salvage or commit_token or merge_gate or identity_fail_stop" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-online
```

### Task 6：统一logical readers接入usage/load/replay/shared-root/last-event

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_checkpoint.py`
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写v1/v2/v3 snapshot/delta logical reader等价RED。**

同一3-root logical evidence分别落为v2全量generation、v3 delta chain、v3 snapshot；task/attempt/event/fault/artifact yield顺序和canonical bodies完全相同。

- [ ] **Step 2: 修改`FormalEvidenceStore.load()`和completed keys。**

running v3 delta按链返回全部terminal task keys；terminal condition必须snapshot。集合仅保留identity，不保留run bundles。

- [ ] **Step 3: 修改usage/replay/audit/last-event。**

`_usage_from_current_checkpoints()`逐logical attempts累加；`_current_replay_run_evidence()`和condition audit使用同一reader；`_last_complete_event_ref()`把每个CURRENT chain作为不可拆分logical run，链任一events文件坏则整个候选作废。

- [ ] **Step 4: 修改shared-root builder消费snapshot/logical reader。**

source task/attempt/event/fault/artifact refs必须指向terminal snapshot中的稳定record refs；running delta source不能作为formal shared baseline。

- [ ] **Step 5: 修改`_last_complete_event_ref()`按logical run流式扫描。**

event-only head与普通delta head都从oldest→newest扫描parent chain，维护该CURRENT run最后一个`TASK_COMPLETED`，再在不同run间维护最大Windows path/稳定run order；外层直接迭代`glob`，不`sorted(glob)`物化全部paths。任一组成generation events文件malformed时整个logical run候选作废，不从剩余parents拼候选。覆盖parent有last event/head无task event、坏parent、坏head和大量CURRENT pointers。

- [ ] **Step 6: 运行Task 6 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -k "logical_reader or usage_from_current or replay or shared_root or last_complete_event" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-consumers
```

### Task 7：Exp3 validated shared baseline index

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写source scan计数RED。**

构造12个Exp1 source conditions和1000个Exp3 lookups；spy断言旧路径调用source condition validator约12,000次，新目标精确12次且provider calls为0。

- [ ] **Step 2: 写index integrity RED。**

source experiment非terminal、CURRENT非snapshot、case重复/缺失、usage不全、version/hash/identity drift均阻止index构建或lookup。

- [ ] **Step 3: 实现只读`ValidatedSharedRootReferenceIndex`。**

```python
@dataclass(frozen=True)
class ValidatedSharedRootReferenceIndex:
    source_experiment_id: str
    source_repeat_id: int
    entries: Mapping[str, Mapping[str, Any]]

    def resolve(self, case_id: str, *, expected_identity: Mapping[str, Any],
                expected_versions: Mapping[str, Any]) -> dict[str, Any]:
        reference = self.entries[case_id]
        validate_shared_reference(reference, expected_identity, expected_versions)
        return dict(reference)
```

index构建时每个Exp1 condition只读一次terminal snapshot，case id全局唯一。

- [ ] **Step 4: 在`_dispatch_formal_conditions()`懒构建并跨Exp3 callbacks共享。**

Exp1尚未terminal时fail dependency；resume每进程只重建一次；0%条件lookup不增加usage/provider count。

- [ ] **Step 5: 运行Task 7 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -k "shared_reference_index or shared_exp1 or source_scan_count" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-shared-index
```

### Task 8：Metrics/report与v2-v3逐字段等价

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_metrics.py`
- Modify: `src/tokenshare/experiments/paper_formal_report.py`
- Test: `tests/experiments/test_paper_formal_metrics.py`
- Test: `tests/experiments/test_paper_formal_report.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] **Step 1: 写metrics拒绝terminal-delta RED。**

formal terminal suite若condition CURRENT仍为delta，metrics/report必须fail closed；v3 snapshot和历史v2全量generation均可读。

- [ ] **Step 2: 写v2-v3逐字段等价fixture。**

同一Exp1–5小矩阵生成v2全量generation与v3 delta→snapshot。normalize只移除明确generation path/schema provenance，断言condition rows、experiment rows、usage、replay summary、model records、failure examples、eligibility逐字段相同，CSV/JSON metric值一致。

- [ ] **Step 3: 修改Lazy mapping current generation检查。**

`_current_generation()`解析manifest：v1/v2直接接受；v3仅接受snapshot。report persisted audit同样拒绝terminal delta。

- [ ] **Step 4: 运行Task 8 focused至GREEN并提交。**

```powershell
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py tests/experiments/test_paper_formal_runner.py -k "generation_v3 or snapshot or v2_v3_equivalence or terminal_delta" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-downstream
```

### Task 9：性能、恢复与文档集成验收

**Files:**
- Modify: `Doc/archive/code-maps/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `progress.md`
- Modify: `feature_list.json`
- Modify: `session-handoff.md`
- Verify: `tests/experiments/test_paper_formal_checkpoint.py`
- Verify: `tests/experiments/test_paper_formal_callbacks.py`
- Verify: `tests/experiments/test_paper_formal_evidence.py`
- Verify: `tests/experiments/test_paper_formal_runner.py`
- Verify: `tests/experiments/test_paper_formal_metrics.py`
- Verify: `tests/experiments/test_paper_formal_report.py`

- [ ] **Step 1: 运行六个定向文件。**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:PYTHONIOENCODING='utf-8'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_callbacks.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-v3-final
```

预期：全部PASS；无真实provider调用。

- [ ] **Step 2: 单独记录性能证据。**

记录50/500-root累计writer bytes、带大payload的tracemalloc peaks、完整outcome max-live、adapter-complete与canonical-committed resume provider call counts、full-load/chain-validator/cleanup-refresh调用次数、Exp1 source validator调用次数。另记录乱序completion snapshot order与snapshot前精确1个closure delta。阈值必须满足design，不以“机器未OOM”替代断言。

- [ ] **Step 3: 运行py_compile与scoped diff check。**

```powershell
conda run -n tokenshare python -m py_compile src/tokenshare/experiments/paper_formal_checkpoint.py src/tokenshare/experiments/paper_formal_callbacks.py src/tokenshare/experiments/paper_formal_evidence.py src/tokenshare/experiments/paper_formal_runner.py src/tokenshare/experiments/paper_formal_metrics.py src/tokenshare/experiments/paper_formal_report.py
git diff --check -- src/tokenshare/experiments/paper_formal_checkpoint.py src/tokenshare/experiments/paper_formal_callbacks.py src/tokenshare/experiments/paper_formal_evidence.py src/tokenshare/experiments/paper_formal_runner.py src/tokenshare/experiments/paper_formal_metrics.py src/tokenshare/experiments/paper_formal_report.py tests/experiments/test_paper_formal_checkpoint.py tests/experiments/test_paper_formal_callbacks.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py tests/experiments/test_paper_formal_metrics.py tests/experiments/test_paper_formal_report.py
```

- [ ] **Step 4: 运行默认Fast门。**

```powershell
.\init.ps1
```

不得运行`.\init.ps1 -Full`、真实API、full实验或LeanAudit。

- [ ] **Step 5: 更新code map与harness状态。**

记录generation v3 root/event delta schema、两类PENDING state machine、working-tree salvage分类、commit-token O(1) cleanup、v2 noncanonical adapter tree、v1 early readonly bypass、manifest v2递归语义、logical last-event坏链规则、online accumulator、snapshot consumers、shared baseline index及精确测试证据；保留“尚未跑full”的明确边界。

- [ ] **Step 6: 独立spec review和code quality review后提交。**

```powershell
git add src/tokenshare/experiments tests/experiments Doc/archive/code-maps/2026-06-29-phase-8-experiment-infrastructure-code-map.md progress.md feature_list.json session-handoff.md
git commit -m "feat: make formal checkpoints full-scale restartable"
```

提交前不得包含用户其他dirty worktree修改；如果无法隔离，只报告验证完成，不提交。
