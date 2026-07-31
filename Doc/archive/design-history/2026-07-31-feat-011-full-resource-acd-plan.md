# FEAT-011 Full Resource A/C/D Implementation Plan

> **历史计划 / superseded notice（2026-07-31）：** Task 1 中 generation-v2
> `attempts × max_tokens × 4` startup gate、disk estimate v2 与旧 47,762-root
> `439.85 GiB` reject 验收已由 calibrated disk estimate v3/rolling counters 替代；
> Task 3 的 manifest/generation v2 已由
> `2026-07-31-feat-011-full-delta-checkpoint-and-shared-baseline-{design,plan}.md`
> 的 generation v3 delta/terminal snapshot 替代。本文旧 checklist 仅保留设计历史，
> 不得作为当前执行入口；当前 D 契约见同名 design 文档的新版 D 章节。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` task-by-task. 本批由当前 agent inline 执行，不分派子 agent。

**Goal:** 让 formal full 的 evidence 索引、working tree 与磁盘容量在 P0-full 规模下可审计、资源有界且在 provider 前 fail closed。

**Architecture:** 层级 JSON manifest v2 只索引 static refs 与 condition inventory，递归验证由 condition chain 到 artifact payload；runner 在 canonical checkpoint 后精确删除 adapter case tree并取消 formal compatibility copy；budget 生成 versioned disk components，direct runner 统一执行卷容量门禁。

**Tech Stack:** Python 3、stdlib `json`/`pathlib`/`shutil`/`hashlib`、pytest、`tracemalloc`。

---

### Task 1：D — versioned disk estimate 与 direct-runner preflight

**Files:**
- Modify: `src/tokenshare/experiments/paper_budget.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_budget.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] 写预算 RED：断言 `disk_estimate.schema_version/policy/inputs/components`，总 `bytes` 等于 roots、units、attempt envelope、`attempts × max_tokens × 4` 和两个 fixed components 之和；P0-full estimate + headroom 大于 `439.85 GiB`。
- [ ] 写 runner RED：monkeypatch `shutil.disk_usage` 和 evidence/provider spies，验证 exact available 通过、少 1 byte 抛出带 required/available/components 的 `PaperInfrastructureBlockedError` 且 provider/evidence calls 为零。
- [ ] 写 resume/replay RED：v2/v1 manifest size 扣除后不低于零；`replay_only` 不调用 `disk_usage`。
- [ ] 运行上述节点并确认因旧 `2048/1024` 估算和缺 preflight 而失败。
- [ ] 在 `paper_budget.py` 增加固定 calibration policy 与 `_paper_disk_estimate()`；在 budget digest body 与 `PaperBudgetResult` 使用同一对象。

```python
_FORMAL_DISK_POLICY = {
    "schema_version": "tokenshare.paper_disk_calibration.v1",
    "p95_root_bytes": 262144,
    "p95_ai_unit_bytes": 65536,
    "p95_attempt_envelope_bytes": 98304,
    "utf8_bytes_per_token": 4,
    "fixed_manifest_bytes": 67108864,
    "fixed_temp_bytes": 536870912,
}

def _paper_disk_estimate(*, roots: int, ai_units: int,
                         attempts: int, max_tokens: int) -> JsonObject:
    components = {
        "root_evidence_bytes": roots * 262144,
        "ai_unit_evidence_bytes": ai_units * 65536,
        "provider_attempt_envelope_bytes": attempts * 98304,
        "token_payload_bytes": attempts * max_tokens * 4,
        "fixed_manifest_bytes": 67108864,
        "fixed_temp_bytes": 536870912,
    }
    return {"schema_version": "tokenshare.paper_disk_estimate.v2",
            "policy": dict(_FORMAL_DISK_POLICY), "components": components,
            "bytes": sum(components.values())}
```

- [ ] 在 runner 增加最近存在 volume root、existing canonical bytes、headroom 与 `_preflight_formal_disk_capacity()`；接入 `_validate_suite_inputs()` 后、EvidenceStore 前。

```python
remaining = max(0, int(estimate["bytes"]) - existing_canonical_bytes)
headroom = max((remaining + 3) // 4, 2 * 1024**3)
required = remaining + headroom
available = shutil.disk_usage(volume_root).free
if available < required:
    raise PaperInfrastructureBlockedError(
        "formal disk preflight failed",
        evidence_integrity=PaperEvidenceIntegrity.INVALID,
        failure_stage="disk_preflight",
        failure_kind="insufficient_disk_capacity",
        diagnostics={"required_bytes": required,
                     "available_bytes": available,
                     "components": estimate["components"]},
    )
```

- [ ] 运行 D focused 直到 GREEN，并记录 exact/one-byte/P0-full 数值。

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
conda run -n tokenshare python -m pytest tests/experiments/test_paper_budget.py tests/experiments/test_paper_formal_runner.py -k "disk_estimate or disk_preflight or replay_only" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-acd-d
```

### Task 2：C — formal no-copy 与 adapter case 精确回收

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] 写 RED：formal checkpoint 后 exact adapter case tree 删除且 `shutil.copytree` 未调用；smoke 仍生成 compatibility view。
- [ ] 写 RED：checkpoint 注入失败时 working tree 保留；resolve 后越出 `plan_root/runs/<condition>/<case>` 的 target 被拒绝且不删除。
- [ ] 写 resume RED：只有已有 canonical CURRENT 且含对应 task 的 case tree 被删；未 checkpoint sibling 保留。
- [ ] 运行节点确认旧实现保留 adapter tree并总是 copytree。
- [ ] 增加 canonical task 验证与 `_remove_checkpointed_adapter_tree()`；只在 checkpoint 成功后调用。

```python
def _remove_checkpointed_adapter_tree(*, adapter_root: Path,
                                      plan_root: Path,
                                      condition_id: str,
                                      case_id: str,
                                      canonical_run_root: Path) -> None:
    target = adapter_root.resolve(strict=False)
    expected = (plan_root / "runs" / condition_id / case_id).resolve(strict=False)
    runs_root = (plan_root / "runs").resolve(strict=False)
    if target != expected or target.parent.parent != runs_root:
        raise ValueError("adapter cleanup target is unsafe")
    _require_canonical_task_checkpoint(canonical_run_root, case_id)
    if target.is_dir():
        shutil.rmtree(target)
```

- [ ] `_publish_compatibility_view()` 对 `execution_classification=None` 直接返回；smoke 路径不变。

```python
if self.execution_classification is None:
    return
shutil.copytree(source, self.output_root, dirs_exist_ok=True)
```

- [ ] 在 resume 的 archive/load 前按 frozen bound plans 做 exact cleanup；不得扫描或删除未知树。
- [ ] 运行 C focused 直到 GREEN。

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_runner.py -k "adapter_tree or compatibility_view or checkpoint_failure or resume_cleanup" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-acd-c
```

### Task 3：A — evidence manifest v2 与 v1 只读分派

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_evidence.py`
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_evidence.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] 写 RED：新 initialize 生成 manifest v2；顶层只有 static refs/condition inventory，checkpoint 只替换当前 condition entry，spy 禁止 suite `rglob` 与其他 condition `_file_evidence`。
- [ ] 写参数化 RED：分别篡改 condition manifest、CURRENT、generation/run manifest、五 canonical files、artifact index 与 payload，load 必须 fail closed。
- [ ] 写 repair RED：generation v2 PENDING 与 experiment/suite finalization intents 完成后只刷新目标 condition/static refs并删除 intent。
- [ ] 写 v1 RED：完整 v1 load/replay 字节不变；completed v1 resume 直接返回；incomplete v1 resume 在写入/provider 前拒绝且 manifest 不升级。
- [ ] 运行 A 节点确认旧全树 v1 refresh/validation 失败。
- [ ] 实现 v2 schema 常量、static allowlist、condition inventory entry、targeted refresh 与版本分派 validator；保留最新 task-id 分区 artifact 复用语义。

```python
manifest_v2 = {
    "schema_version": "tokenshare.paper_evidence_manifest.v2",
    "static_files": static_refs_from_allowlist,
    "conditions": condition_inventory,
}

def _refresh_evidence_manifest(self, *, run_root: Path | None = None) -> None:
    if _existing_manifest_schema(self.output_root) == V1:
        raise ValueError("v1 evidence manifest is read-only")
    if run_root is not None:
        _replace_condition_entry(manifest_v2, _condition_entry(run_root))
    else:
        manifest_v2["static_files"] = _fixed_static_refs(self.output_root)
    _atomic_write_json(self.output_root / "evidence_manifest.json", manifest_v2)
```

- [ ] 修改 runner resume/finalization：v2 调 targeted/static refresh；v1 completed return，v1 incomplete write 拒绝。
- [ ] 运行 A focused 直到 GREEN，并复跑 task-id 分区 ArtifactRef 回归。

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -k "manifest_v2 or manifest_v1 or pending or artifact_ref or finalization_intent or v1_resume" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-acd-a
```

### Task 4：常量内存 final event ref

**Files:**
- Modify: `src/tokenshare/experiments/paper_formal_runner.py`
- Test: `tests/experiments/test_paper_formal_runner.py`

- [ ] 写 RED：两个 CURRENT 路径、多匹配事件冻结旧 WindowsPath/last-in-file 语义；monkeypatch `_read_jsonl_records` 禁止全文件 list。
- [ ] 写 `tracemalloc` RED：大 event file 相对小 file 的额外峰值保持固定阈值。
- [ ] 用逐行 parser 和两个 current-best 变量实现，路径比较继续使用 WindowsPath 语义。

```python
best_pointer: Path | None = None
best_event_path: Path | None = None
best_event: dict[str, Any] | None = None
for pointer_path in suite_root.glob("experiments/*/runs/*/*/CURRENT.json"):
    last_in_file = None
    with event_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if event.get("event_type") in COMPLETE_EVENT_TYPES:
                last_in_file = event
    if last_in_file is not None and (
        best_pointer is None or best_pointer < pointer_path
    ):
        best_pointer, best_event_path, best_event = (
            pointer_path, event_path, last_in_file
        )
```

- [ ] 运行语义与内存 focused 直到 GREEN。

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
conda run -n tokenshare python -m pytest tests/experiments/test_paper_formal_runner.py -k "last_complete_event_ref" -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-acd-event
```

### Task 5：集成验证与文档收口

**Files:**
- Modify: `Doc/archive/code-maps/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Verify: `tests/experiments/test_paper_budget.py`
- Verify: `tests/experiments/test_paper_formal_evidence.py`
- Verify: `tests/experiments/test_paper_formal_runner.py`

- [ ] 运行三个定向文件，外部 `--basetemp`，记录 passed 数与时间。

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
conda run -n tokenshare python -m pytest tests/experiments/test_paper_budget.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py -q --basetemp=E:\TokenEcnomic\TokenShareData\outputs\diagnostics\pytest-feat011-acd-final
```
- [ ] 读取测试输出中的 P0-full required/free、exact/one-byte、resume deduction 和 tracemalloc small/large 峰值。
- [ ] 运行三个生产模块 `py_compile` 与获准文件 scoped `git diff --check`。

```powershell
conda run -n tokenshare python -m py_compile src/tokenshare/experiments/paper_budget.py src/tokenshare/experiments/paper_formal_evidence.py src/tokenshare/experiments/paper_formal_runner.py
git diff --check -- src/tokenshare/experiments/paper_budget.py src/tokenshare/experiments/paper_formal_evidence.py src/tokenshare/experiments/paper_formal_runner.py tests/experiments/test_paper_budget.py tests/experiments/test_paper_formal_evidence.py tests/experiments/test_paper_formal_runner.py Doc/archive/design-history/2026-07-31-feat-011-full-resource-acd-design.md Doc/archive/design-history/2026-07-31-feat-011-full-resource-acd-plan.md Doc/archive/code-maps/2026-06-29-phase-8-experiment-infrastructure-code-map.md
```
- [ ] 更新 code map，记录 v2/v1、no-copy/cleanup、disk policy/preflight、常量内存语义和验证边界。
- [ ] 向根 agent 报告 RED→GREEN、精确证据、共享文件保护与未运行 Fast/API/Full/LeanAudit。
