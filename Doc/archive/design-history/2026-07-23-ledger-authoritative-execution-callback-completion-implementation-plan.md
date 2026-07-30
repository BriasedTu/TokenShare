# Ledger Authoritative Execution Callback Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 submission 与 heartbeat 在写协议事件前使用 ledger 最新 attempt/lease snapshot，阻止普通延迟回调把 terminal 状态重新推进，同时补齐 replacement fencing token 回归和论文—代码状态文档。

**Architecture:** `ProtocolEngine` 在 storage-writing boundary 读取一次 `EventLedger`，用 engine-private 反序列化 helper 把最新 `Attempt`/`Lease` snapshot 恢复为领域对象，再复用现有 `evaluate_submission_acceptance()`、`LeaseManager.heartbeat()` 和状态机。`tokenshare.core` 不读取 storage，SQLite 不增加 schema，experiments/runtime 不建立第二套权威状态。

**Tech Stack:** Python 3.12、dataclasses、JSONL `EventLedger`、SQLite materialized index、pytest、PowerShell、conda `tokenshare` 环境。

---

## 0. 执行约束

- 当前工作树包含大迁移的既有 modified/untracked 文件。所有既有改动都视为用户工作。
- 只修改本计划列出的文件；不要清理、reset、checkout、移动或格式化无关文件。
- 本计划不授权 git stage、commit、push 或 PR。若用户之后明确要求，再只处理本计划范围。
- 不联网，不调用真实 provider，不运行正式 Experiment 1–5。
- 不增加攻击、篡改、伪造、auth、signature、ACL、path/injection 或 security fuzzing。
- 不增加第六类 rate-fault，不修改 Experiment 1–5 矩阵。
- active feature 继续是 `feat-011`，不得新开独立 feature。

## 1. 文件结构

**Create:**

- `Doc/TechnicalDocument/2026-07-23-ledger-authoritative-execution-callback-completion-design.md`：已确认设计；实现完成后更新状态和验证证据。
- `Doc/TechnicalDocument/2026-07-23-ledger-authoritative-execution-callback-completion-implementation-plan.md`：本执行计划。

**Modify for behavior:**

- `src/tokenshare/protocol_engine.py`：恢复 ledger 最新 `Attempt`/`Lease` snapshot；submission 和 heartbeat 使用权威对象。
- `tests/test_phase2_scheduling_flow.py`：heartbeat 的 terminal stale snapshot 与连续 Active snapshot RED/GREEN。
- `tests/test_phase3_execution_flow.py`：recovery 后 stale submission 审计拒绝、非法状态边和 SQLite rebuild RED/GREEN。
- `tests/integration/test_paper_protocol_runtime_integration.py`：replacement lease 的 fencing token 显式不重复断言。

**Modify for documentation/state:**

- `Doc/TechnicalDocument/2026-07-22-chapter-4-code-paper-alignment-revision-guide.md`
- `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- `feature_list.json`
- `progress.md`
- `session-handoff.md`

## 2. Task 1：冻结 stale submission RED

**Files:**

- Modify: `tests/test_phase3_execution_flow.py`

- [x] **Step 1：增加测试依赖**

在现有 imports 中加入：

```python
import sqlite3

from tokenshare.storage.sqlite_index import SQLiteMaterializedIndex
```

- [x] **Step 2：写 recovery 后旧 submission 的失败测试**

在已有 late/expired submission tests 后新增：

```python
def test_phase3_submission_uses_latest_ledger_attempt_and_lease_snapshots(
    tmp_path,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
        artifact_store=store,
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-22T00:00:00Z",
        correlation_id="corr_schedule_stale_submission",
        decision_id="decision_stale_submission",
        lease_id="lease_stale_submission",
        attempt_id="attempt_stale_submission",
        fencing_token="fence_stale_submission",
    )
    engine.record_recovery_decision(
        decision=evaluate_retry(
            trigger="executor_error",
            retry_count=1,
            max_retries=make_config().max_retries,
        ),
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        task_unit=scheduled.task_unit,
        recovery_action_id="recovery_before_stale_submission",
        now="2026-07-22T00:01:00Z",
        correlation_id="corr_recovery_before_stale_submission",
    )
    submission = _submission_for(
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        submission_id="submission_after_recovery",
        submitted_at="2026-07-22T00:02:00Z",
    )

    result = engine.record_execution_submission(
        submission=submission,
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        correlation_id="corr_stale_submission_after_recovery",
    )

    assert result.acceptance_decision.acceptance_status == "rejected"
    assert result.acceptance_decision.rejection_reason == "attempt_not_running"
    assert result.attempt is None
    assert result.attempt_event is None
    attempt_edges = [
        (event.payload.get("old_state"), event.payload.get("new_state"))
        for event in ledger.read_all()
        if event.event_type == EventType.ATTEMPT_STATE_CHANGED
        and event.object_id == scheduled.attempt.attempt_id
    ]
    assert ("Running", "Submitted") not in attempt_edges

    index_path = tmp_path / "index.sqlite"
    SQLiteMaterializedIndex(
        index_path,
        artifact_store=store,
    ).rebuild_from_events(ledger.read_all())
    with sqlite3.connect(index_path) as connection:
        attempt_state = connection.execute(
            "select state from attempts where attempt_id = ?",
            (scheduled.attempt.attempt_id,),
        ).fetchone()
        lease_state = connection.execute(
            "select state from leases where lease_id = ?",
            (scheduled.lease.lease_id,),
        ).fetchone()
        submission_row = connection.execute(
            """
            select acceptance_status, rejection_reason
            from execution_submissions
            where submission_id = ?
            """,
            (submission.submission_id,),
        ).fetchone()

    assert attempt_state == ("Failed",)
    assert lease_state == ("Released",)
    assert submission_row == ("rejected", "attempt_not_running")
```

- [x] **Step 3：运行精确 RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/test_phase3_execution_flow.py::test_phase3_submission_uses_latest_ledger_attempt_and_lease_snapshots -q
```

Expected: FAIL；旧实现返回 `accepted`，或出现 `("Running", "Submitted")`，SQLite attempt 最终为 `Submitted`。

- [x] **Step 4：记录 RED 证据**

把精确失败摘要暂记到本计划 Task 1 下方的执行记录；此时不要修改 feature 状态为完成。

## 3. Task 2：实现 ledger-authoritative submission

**Files:**

- Modify: `src/tokenshare/protocol_engine.py`
- Test: `tests/test_phase3_execution_flow.py`
- Test: `tests/core/test_recovery.py`

- [x] **Step 1：让最新 snapshot helper 对 malformed payload fail closed**

把现有 `_latest_attempt_snapshot()` / `_latest_lease_snapshot()` 中“matching event payload 不是 dict 时返回 `None`”改为：

```python
def _latest_attempt_snapshot(
    events: Iterable[LedgerEvent],
    attempt_id: str,
) -> JsonObject | None:
    for event in reversed(tuple(events)):
        if (
            event.event_type == EventType.ATTEMPT_STATE_CHANGED
            and event.object_id == attempt_id
        ):
            attempt = event.payload.get("attempt")
            if not isinstance(attempt, dict):
                raise ValueError("latest attempt snapshot is malformed")
            return dict(attempt)
    return None


def _latest_lease_snapshot(
    events: Iterable[LedgerEvent],
    lease_id: str,
) -> JsonObject | None:
    for event in reversed(tuple(events)):
        if event.event_type == EventType.LEASE_STATE_CHANGED and event.object_id == lease_id:
            lease = event.payload.get("lease")
            if not isinstance(lease, dict):
                raise ValueError("latest lease snapshot is malformed")
            return dict(lease)
    return None
```

- [x] **Step 2：增加严格 snapshot 字段 helper**

在 `_latest_*_snapshot()` 附近增加：

```python
def _snapshot_required_string(snapshot: JsonObject, field_name: str) -> str:
    value = snapshot.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"latest snapshot field must be a non-empty string: {field_name}")
    return value


def _snapshot_optional_string(
    snapshot: JsonObject,
    field_name: str,
) -> str | None:
    value = snapshot.get(field_name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"latest snapshot field must be a string or null: {field_name}")
    return value


def _snapshot_json_object(snapshot: JsonObject, field_name: str) -> JsonObject:
    value = snapshot.get(field_name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"latest snapshot field must be an object: {field_name}")
    return dict(value)


def _snapshot_optional_artifact_ref(
    snapshot: JsonObject,
    field_name: str,
) -> ArtifactRef | None:
    value = snapshot.get(field_name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"latest snapshot artifact ref is malformed: {field_name}")
    return ArtifactRef.from_dict(dict(value))


def _snapshot_artifact_refs(
    snapshot: JsonObject,
    field_name: str,
) -> dict[str, ArtifactRef]:
    value = snapshot.get(field_name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"latest snapshot artifact refs are malformed: {field_name}")
    for output_name, ref_data in value.items():
        if not isinstance(output_name, str) or not isinstance(ref_data, dict):
            raise ValueError(
                f"latest snapshot artifact refs are malformed: {field_name}"
            )
    return _artifact_refs_from_dict(dict(value))
```

- [x] **Step 3：增加 `Attempt` / `Lease` engine-private 恢复 helper**

```python
def _attempt_from_snapshot(snapshot: JsonObject) -> Attempt:
    return Attempt(
        attempt_id=_snapshot_required_string(snapshot, "attempt_id"),
        task_id=_snapshot_required_string(snapshot, "task_id"),
        unit_id=_snapshot_required_string(snapshot, "unit_id"),
        lease_id=_snapshot_required_string(snapshot, "lease_id"),
        client_id=_snapshot_required_string(snapshot, "client_id"),
        state=AttemptState(
            _snapshot_required_string(snapshot, "state")
        ),
        attempt_kind=_snapshot_required_string(snapshot, "attempt_kind"),
        created_at=_snapshot_required_string(snapshot, "created_at"),
        started_at=_snapshot_optional_string(snapshot, "started_at"),
        submitted_at=_snapshot_optional_string(snapshot, "submitted_at"),
        finished_at=_snapshot_optional_string(snapshot, "finished_at"),
        environment_summary=_snapshot_json_object(
            snapshot,
            "environment_summary",
        ),
        input_artifact_refs=_snapshot_artifact_refs(
            snapshot,
            "input_artifact_refs",
        ),
        raw_output_ref=_snapshot_optional_artifact_ref(
            snapshot,
            "raw_output_ref",
        ),
        parsed_output_ref=_snapshot_optional_artifact_ref(
            snapshot,
            "parsed_output_ref",
        ),
        candidate_output_refs=_snapshot_artifact_refs(
            snapshot,
            "candidate_output_refs",
        ),
        log_ref=_snapshot_optional_artifact_ref(snapshot, "log_ref"),
        failure_kind=_snapshot_optional_string(snapshot, "failure_kind"),
        failure_reason=_snapshot_optional_string(snapshot, "failure_reason"),
        superseded_by_attempt_id=_snapshot_optional_string(
            snapshot,
            "superseded_by_attempt_id",
        ),
        metadata=_snapshot_json_object(snapshot, "metadata"),
        schema_version=_snapshot_required_string(snapshot, "schema_version"),
    )


def _lease_from_snapshot(snapshot: JsonObject) -> Lease:
    heartbeat_count = snapshot.get("heartbeat_count")
    if (
        isinstance(heartbeat_count, bool)
        or not isinstance(heartbeat_count, int)
        or heartbeat_count < 0
    ):
        raise ValueError(
            "latest snapshot field must be a non-negative integer: heartbeat_count"
        )
    return Lease(
        lease_id=_snapshot_required_string(snapshot, "lease_id"),
        task_id=_snapshot_required_string(snapshot, "task_id"),
        unit_id=_snapshot_required_string(snapshot, "unit_id"),
        attempt_id=_snapshot_required_string(snapshot, "attempt_id"),
        client_id=_snapshot_required_string(snapshot, "client_id"),
        state=LeaseState(_snapshot_required_string(snapshot, "state")),
        fencing_token=_snapshot_required_string(snapshot, "fencing_token"),
        issued_at=_snapshot_required_string(snapshot, "issued_at"),
        expires_at=_snapshot_required_string(snapshot, "expires_at"),
        last_heartbeat_at=_snapshot_optional_string(
            snapshot,
            "last_heartbeat_at",
        ),
        heartbeat_count=heartbeat_count,
        lease_kind=_snapshot_required_string(snapshot, "lease_kind"),
        terminated_at=_snapshot_optional_string(snapshot, "terminated_at"),
        terminated_reason=_snapshot_optional_string(
            snapshot,
            "terminated_reason",
        ),
        metadata=_snapshot_json_object(snapshot, "metadata"),
        schema_version=_snapshot_required_string(snapshot, "schema_version"),
    )


def _authoritative_attempt(
    events: Iterable[LedgerEvent],
    supplied_attempt: Attempt,
) -> Attempt:
    snapshot = _latest_attempt_snapshot(events, supplied_attempt.attempt_id)
    return supplied_attempt if snapshot is None else _attempt_from_snapshot(snapshot)


def _authoritative_lease(
    events: Iterable[LedgerEvent],
    supplied_lease: Lease,
) -> Lease:
    snapshot = _latest_lease_snapshot(events, supplied_lease.lease_id)
    return supplied_lease if snapshot is None else _lease_from_snapshot(snapshot)
```

- [x] **Step 4：让 submission 使用同一次 ledger read 的权威对象**

在 `record_execution_submission()` 中，把 acceptance 前半段改为：

```python
artifact_store = self._require_artifact_store()
current_events = self._event_ledger.read_all()
authoritative_attempt = _authoritative_attempt(current_events, attempt)
authoritative_lease = _authoritative_lease(current_events, lease)
acceptance_decision = evaluate_submission_acceptance(
    submission=submission,
    attempt=authoritative_attempt,
    lease=authoritative_lease,
)
```

accepted 分支必须改为从 `authoritative_attempt` transition：

```python
submitted_attempt = transition_attempt(
    authoritative_attempt,
    new_state=AttemptState.SUBMITTED,
    changed_at=submission.submitted_at,
    reason="execution_submission_recorded",
    environment_summary=submission.environment_summary,
    raw_output_ref=submission.raw_output_ref,
    parsed_output_ref=submission.parsed_output_ref,
    candidate_output_refs=submission.candidate_output_refs,
    log_ref=submission.log_ref,
)
```

attempt event 的 `old_state` 和 idempotency key 继续为 `Running -> Submitted`；不得用 supplied terminal/old state 拼 payload。

- [x] **Step 5：运行精确 GREEN 与相邻 acceptance tests**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/test_phase3_execution_flow.py::test_phase3_submission_uses_latest_ledger_attempt_and_lease_snapshots `
  tests/test_phase3_execution_flow.py::test_phase3_late_submission_is_rejected_audit_only_with_stable_reason `
  tests/test_phase3_execution_flow.py::test_phase3_submission_on_expired_lease_is_rejected_audit_only `
  tests/core/test_recovery.py -q
```

Expected: PASS；stale submission 为 `attempt_not_running`，late/direct tests 保持既有原因。

## 4. Task 3：冻结并修复 stale heartbeat

**Files:**

- Modify: `tests/test_phase2_scheduling_flow.py`
- Modify: `src/tokenshare/protocol_engine.py`

- [x] **Step 1：写 terminal stale heartbeat RED**

在 `tests/test_phase2_scheduling_flow.py` 增加：

```python
def test_phase2_stale_heartbeat_cannot_reactivate_released_lease(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-23T00:00:00Z",
        correlation_id="corr_schedule_stale_heartbeat",
        decision_id="decision_stale_heartbeat",
        lease_id="lease_stale_heartbeat",
        attempt_id="attempt_stale_heartbeat",
        fencing_token="fence_stale_heartbeat",
    )
    engine.record_recovery_decision(
        decision=evaluate_retry(
            trigger="executor_error",
            retry_count=1,
            max_retries=make_config().max_retries,
        ),
        attempt=scheduled.attempt,
        lease=scheduled.lease,
        task_unit=scheduled.task_unit,
        recovery_action_id="recovery_before_stale_heartbeat",
        now="2026-07-23T00:00:30Z",
        correlation_id="corr_recovery_before_stale_heartbeat",
    )
    before = ledger.read_all()

    with pytest.raises(
        ValueError,
        match="terminal lease cannot heartbeat: Released",
    ):
        engine.record_lease_heartbeat(
            lease=scheduled.lease,
            now="2026-07-23T00:01:00Z",
            correlation_id="corr_stale_heartbeat",
        )

    assert ledger.read_all() == before
```

为该文件补齐：

```python
import pytest

from tokenshare.core.recovery import evaluate_retry
```

- [x] **Step 2：写 latest Active heartbeat RED**

```python
def test_phase2_heartbeat_advances_from_latest_active_lease_snapshot(
    tmp_path,
) -> None:
    ledger = EventLedger(tmp_path / "events" / "task_demo.jsonl")
    engine = ProtocolEngine(
        event_ledger=ledger,
        protocol_config=make_config(),
    )
    unit = make_unit()
    scheduled = engine.schedule_ready_unit(
        graph=TaskGraph(
            task_id=unit.task_id,
            units={unit.unit_id: unit},
            relations=[],
        ),
        clients=[make_client()],
        now="2026-07-23T00:00:00Z",
        correlation_id="corr_schedule_heartbeat_chain",
        decision_id="decision_heartbeat_chain",
        lease_id="lease_heartbeat_chain",
        attempt_id="attempt_heartbeat_chain",
        fencing_token="fence_heartbeat_chain",
    )
    first = engine.record_lease_heartbeat(
        lease=scheduled.lease,
        now="2026-07-23T00:00:30Z",
        correlation_id="corr_heartbeat_1",
    )
    second = engine.record_lease_heartbeat(
        lease=scheduled.lease,
        now="2026-07-23T00:01:00Z",
        correlation_id="corr_heartbeat_2",
    )

    assert first.lease.heartbeat_count == 1
    assert second.lease.heartbeat_count == 2
    assert second.lease.expires_at > first.lease.expires_at
```

- [x] **Step 3：运行 heartbeat RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/test_phase2_scheduling_flow.py::test_phase2_stale_heartbeat_cannot_reactivate_released_lease `
  tests/test_phase2_scheduling_flow.py::test_phase2_heartbeat_advances_from_latest_active_lease_snapshot -q
```

Expected: FAIL；旧实现会重新写 Active，或第二次 heartbeat 因重复 count/idempotency key 冲突。

- [x] **Step 4：让 heartbeat 使用 authoritative lease**

把 `record_lease_heartbeat()` 开头改为：

```python
current_events = self._event_ledger.read_all()
authoritative_lease = _authoritative_lease(current_events, lease)
heartbeat_lease = self._lease_manager.heartbeat(
    authoritative_lease,
    now=now,
)
```

其余 event payload/idempotency 继续从 `heartbeat_lease` 派生。

- [x] **Step 5：运行 heartbeat GREEN**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/test_phase2_scheduling_flow.py -q
```

Expected: PASS；terminal stale heartbeat 零新增 event，连续 Active heartbeat count 为 1、2。

## 5. Task 4：补 replacement token 显式回归

**Files:**

- Modify: `tests/integration/test_paper_protocol_runtime_integration.py`

- [x] **Step 1：扩展 worker-death replacement 断言**

在现有：

```python
assert len(active_leases) == 2
assert active_leases[0]["lease_id"] != active_leases[1]["lease_id"]
```

后增加：

```python
assert active_leases[0]["attempt_id"] != active_leases[1]["attempt_id"]
assert active_leases[0]["fencing_token"] != active_leases[1]["fencing_token"]
```

- [x] **Step 2：运行精确集成节点**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/integration/test_paper_protocol_runtime_integration.py::test_real_os_worker_death_expires_lease_and_reassigns_same_unit -q
```

Expected: PASS；同一 unit 的 replacement lease/attempt/token 都不同。

- [x] **Step 3：确认没有扩大 token 主张**

搜索新增 diff，确认没有出现：

```text
UUID
globally monotonic
cryptographically random
global epoch
```

允许文档以否定句说明这些不保证；不得把它们加入实现。

## 6. Task 5：运行 shared impact verification

**Files:** no source edits unless a test reveals directly related regression.

- [x] **Step 1：运行 core/storage/runtime 定向测试**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/core/test_recovery.py `
  tests/core/test_lease_manager.py `
  tests/test_phase2_scheduling_flow.py `
  tests/test_phase3_execution_flow.py `
  tests/storage/test_phase3_event_projection.py `
  tests/local_runtime/test_submission_and_recovery.py -q
```

Expected: all passed。

- [x] **Step 2：运行双领域/recovery 集成影响面**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest `
  tests/integration/test_paper_protocol_runtime_integration.py::test_factorization_full_capturing_transport_covers_system_lifecycle `
  tests/integration/test_paper_protocol_runtime_integration.py::test_lean_fixed_plan_full_is_plugin_validated_and_engine_merged `
  tests/integration/test_paper_protocol_runtime_integration.py::test_verifier_rejection_and_late_submission_requeue_without_old_canonical `
  tests/integration/test_paper_protocol_runtime_integration.py::test_real_os_worker_death_expires_lease_and_reassigns_same_unit `
  tests/integration/test_paper_protocol_runtime_integration.py::test_experiments_package_has_no_direct_protocol_state_write_authority -q
```

Expected: all passed；capturing transport 仍 `paper_eligible=false`，provider calls 为 0。

- [x] **Step 3：运行固定 Lean canary**

Run:

```powershell
conda run -n tokenshare python verification/run_verification.py --mode full --only-lean-canary
```

Expected: fixed canary all passed；common JSON/SQLite/harness/compileall passed。

本补丁不修改 Lean catalog/checker/toolchain/source rendering，不运行 `--refresh --force-all`。

## 7. Task 6：同步论文对齐、code map 和状态

**Files:**

- Modify: `Doc/TechnicalDocument/2026-07-22-chapter-4-code-paper-alignment-revision-guide.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `Doc/TechnicalDocument/2026-06-29-phase-8-experiment-infrastructure-code-map.md`
- Modify: `Doc/TechnicalDocument/tokenshare_latest_real_plugin_experiment_design.md`
- Modify: `Doc/TechnicalDocument/2026-07-23-ledger-authoritative-execution-callback-completion-design.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [x] **Step 1：更新第四章对齐指南**

做以下精确修订：

- 把“Task 2/3 已实现最新权威 snapshot 判断”改成“2026-07-23 小补丁后，submission/heartbeat 在 engine 写入边界使用 ledger 最新 snapshot”；
- 把 `lease_inactive` 修正为 `lease_not_active`；
- 把“paper adapter 迁移完成后仍应复核”改成已经过 system runtime integration 复核；
- 把“paper runtime 迁移完成后”改为当前完成事实；
- 停止线记录实现已完成，但正式真实-provider矩阵和发布级总门禁仍未完成；
- 不增加分布式、全局 token 或安全保证。

- [x] **Step 2：更新 code map**

在 `tokenshare_v1_code_map.md` 的 `ProtocolEngine` / Phase 2–3 execution-recovery 区域记录：

```text
submission/heartbeat 在 storage-writing boundary 从 ledger 恢复最新 Attempt/Lease；
core acceptance/heartbeat 规则保持纯函数/纯状态规则；
stale submission 保留 audit event 但不推进 attempt；
terminal stale heartbeat 不产生新 lease event。
```

若 Phase 8 code map 仍描述 paper runtime recovery/lease authority，在对应 current-runtime 段增加同一事实；不要重写历史 regression 章节。

- [x] **Step 3：更新唯一权威实验设计的当前系统事实**

只更新 current system fact/status：

- system FULL path 已包含 latest-snapshot callback guard；
- Task 10 Fast 已通过但发布级 Full/600-entry force-all 不得虚报；
- 不改 Experiment 1–5 数量、模型、故障或指标口径。

- [x] **Step 4：更新设计文档状态**

实现验证完成后，把设计文档顶部状态改为：

```text
状态：已实现并验证
```

并在文末追加实际 RED、GREEN、targeted、Lean canary、Fast、Full 证据。

- [x] **Step 5：更新 feature/progress/handoff**

`feature_list.json`：

- 在 `feat011_system_runtime_migration_plan_2026_07_22` 下增加独立的 authoritative-callback completion evidence；
- 保持 `feature_status="in-progress"`；
- `provider_calls_made=0`；
- 不覆盖 Task 10 的 150-entry sampled evidence 或未运行 Full+LeanAudit 的历史事实。

`progress.md` / `session-handoff.md`：

- 中文记录根因、修复、RED/GREEN、验证命令和下一步；
- 明确这是正常时序一致性，不是攻击防护；
- 明确正式 Experiment 1–5 仍未运行。

- [x] **Step 6：文档和 JSON 自检**

Run:

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
```

Run:

```powershell
$paths = @(
  'Doc\TechnicalDocument\2026-07-22-chapter-4-code-paper-alignment-revision-guide.md',
  'Doc\TechnicalDocument\2026-07-23-ledger-authoritative-execution-callback-completion-design.md',
  'Doc\TechnicalDocument\2026-07-23-ledger-authoritative-execution-callback-completion-implementation-plan.md',
  'Doc\TechnicalDocument\tokenshare_v1_code_map.md',
  'Doc\TechnicalDocument\2026-06-29-phase-8-experiment-infrastructure-code-map.md',
  'Doc\TechnicalDocument\tokenshare_latest_real_plugin_experiment_design.md',
  'progress.md',
  'session-handoff.md'
)
foreach ($path in $paths) {
  $text = Get-Content -LiteralPath $path -Encoding UTF8 -Raw
  if (([regex]::Matches($text, '\x60{3}')).Count % 2 -ne 0) {
    throw "unbalanced Markdown fence: $path"
  }
}
Write-Output 'markdown-fences-ok'
```

Expected: `feature-list-json-ok`、`markdown-fences-ok`。

## 8. Task 7：最终门禁与收尾

**Files:** all files listed above.

- [x] **Step 1：运行 Fast**

Run:

```powershell
.\init.ps1
```

Expected: `Verification Complete`；JSON/SQLite、harness、compileall 和 fast tests 全部通过。

- [x] **Step 2：运行 Full**

Run:

```powershell
.\init.ps1 -Full
```

Expected: `Verification Complete`；完整 `pytest tests` 通过。默认关闭的真实 provider smoke 可以保持 skip。

本补丁不声称运行 `-LeanAudit` 或 600-entry force-all；fixed Lean canary 已在 Task 5 单独运行。

- [x] **Step 3：检查 diff scope**

Run:

```powershell
git diff --check
git status --short
```

Expected:

- `git diff --check` exit 0；允许既有 LF/CRLF warning，但不能有 whitespace error；
- changed/untracked 中没有本计划之外由本轮新建的源码、测试、输出、cache、secret 或临时文件；
- 不清理用户原有 89 项迁移改动。

- [x] **Step 4：最终同源检查**

搜索 `ProtocolEngine` 中所有 `record_*` lease/attempt callback：

```powershell
Get-Content -LiteralPath 'src\tokenshare\protocol_engine.py' -Encoding UTF8 |
  Select-String -Pattern 'def record_.*(submission|heartbeat)|_authoritative_(attempt|lease)'
```

确认：

- submission 与 heartbeat 已覆盖；
- recovery 仍使用既有 latest snapshot guard；
- 没有另一个当前 runtime 调用的同源 callback 继续从 terminal supplied object 写回 Active/Running；
- 若发现新同源正常时序漏洞，只在本设计范围内补 RED/GREEN；不要扩展人为攻击模型。

- [x] **Step 5：记录最终证据**

把 Task 1–7 的实际命令、pass/fail 数量、provider calls=0、未运行项写入：

- 本计划执行记录；
- design 文档验证记录；
- `feature_list.json`；
- `progress.md`；
- `session-handoff.md`。

只有这些记录与实际终端输出一致时，才能把本小补丁标为完成。

## 9. 执行记录

实施 agent 按顺序追加实际证据，不改写计划要求：

- RED：2026-07-23 精确节点按预期失败，旧实现返回
  `acceptance_status='accepted'`，断言期望 `rejected`；这证明测试命中了
  supplied stale `Attempt` / `Lease` 被错误当作权威状态的缺口。
- submission GREEN：精确 stale submission、late/expired acceptance 与 core recovery
  合计 `33 passed in 0.59s`；SQLite rebuild 保持 `Failed/Released`。
- heartbeat GREEN：两条 RED 分别命中 terminal lease 未拒绝和 count=1 幂等冲突；
  修复后 Phase 2 scheduling `4 passed in 0.37s`。
- replacement token：真实 OS worker-death 精确节点 `1 passed in 16.96s`；
  replacement lease/attempt/fencing token 均不同。
- targeted：core/storage/local-runtime `68 passed in 8.13s`。
- integration：Factorization/Lean/recovery/write-authority `5 passed in 43.38s`。
- Lean canary：`11 passed in 118.55s`，JSON/SQLite、harness、compileall 通过。
- Fast：最终文档回填后 `330 passed, 1 skipped in 15.92s`；回填前同为
  330/1，耗时 17.28s。
- Full：收集 1303 项，`1302 passed, 1 skipped in 1082.59s`。
- provider/API calls：0。
- 未运行项：600-entry `--refresh --force-all`、真实 provider、正式
  Experiment 1–5。
