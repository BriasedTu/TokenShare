# Lean Checker Verification Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Lean catalog 的 600 次隐式 subprocess preflight 改为内容寻址、fail-closed 的增量审计，同时保留真实 canary、正式 AI checker evidence 和最终强制全量门禁。

**Architecture:** `lean_catalog_audit.py` 负责 entry key、tracked manifest、增量/全量 audit 和原子 refresh；`paper_catalog.py` 只加载 catalog 并验证 manifest，不再隐式启动 Lean。Lean child/merge/paper adapter 接收显式 checker callable，普通回归使用带 spy 的 test fake，生产默认仍是真实 checker。`verification/run_verification.py` 成为 PowerShell/Bash 的单一 conda Python 入口，并实现 Fast、Full、LeanAudit 三档。

**Tech Stack:** Python 3.12、pytest 9、Lean 4.8/Lake 5、PowerShell、Bash、JSON/JSONL、SHA-256。

---

### Task 1: 内容寻址 entry 与 tracked manifest 纯模型

**Files:**
- Create: `src/tokenshare/experiments/lean_catalog_audit.py`
- Create: `tests/experiments/test_lean_catalog_audit.py`
- Modify: `src/tokenshare/plugins/lean_proof/checker.py`

- [ ] **Step 1: 写 entry key、manifest digest 和 coverage tamper RED**

```python
def test_preflight_entry_key_changes_with_every_checker_input() -> None:
    base = _entry()
    assert len({
        lean_preflight_entry_key(**{**base, field: value})
        for field, value in _mutations().items()
    }) == len(_mutations())

def test_manifest_rejects_missing_duplicate_and_nonaccepted_entries() -> None:
    with pytest.raises(ValueError, match="coverage"):
        validate_lean_catalog_preflight_manifest(_tampered_manifest())
```

- [ ] **Step 2: 运行 RED**

Run:

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_lean_catalog_audit.py -q
```

Expected: collection fails because `tokenshare.experiments.lean_catalog_audit` does not exist.

- [ ] **Step 3: 实现最小纯模型**

实现 `LeanCatalogPreflightEntry`、`LeanCatalogPreflightManifest`、`lean_preflight_entry_key()`、`manifest_digest()`、`validate_lean_catalog_preflight_manifest()`。entry key 固定包含：

```python
{
    "generated_source_digest": generated_source_digest,
    "oracle_proof_digest": oracle_proof_digest,
    "environment_digest": environment_digest,
    "checker_implementation_digest": checker_implementation_digest,
    "checker_mode": checker_mode,
    "resource_limits": resource_limits,
}
```

把 checker 中 `_render_lean_source()` 提升为稳定 public helper `render_lean_source()`，保留 private alias 兼容已有内部调用。

- [ ] **Step 4: 运行 GREEN 和 checker direct 回归**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_lean_catalog_audit.py tests/plugins/lean_proof/test_lean_checker_direct.py -q
```

Expected: all selected tests pass。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add src/tokenshare/experiments/lean_catalog_audit.py src/tokenshare/plugins/lean_proof/checker.py tests/experiments/test_lean_catalog_audit.py
git commit -m "feat: add lean catalog audit manifest model"
```

### Task 2: Catalog loader 与真实 audit 分离

**Files:**
- Modify: `src/tokenshare/experiments/paper_catalog.py`
- Modify: `tests/experiments/test_paper_catalog.py`
- Modify: `tests/experiments/test_lean_lemma_graph_catalog.py`
- Create: `benchmarks/paper/lean_checker_preflight.v1.json`

- [ ] **Step 1: 写普通 load 零 checker 调用和 stale fail-closed RED**

```python
def test_catalog_load_uses_matching_manifest_without_checker(monkeypatch) -> None:
    calls = 0
    monkeypatch.setattr(paper_catalog, "check_lean_proof", _counting_checker)
    manifest = load_paper_catalogs(...)
    assert manifest.lean_preflight_status == "passed"
    assert calls == 0

def test_catalog_load_rejects_stale_manifest_without_running_checker(...) -> None:
    with pytest.raises(ValueError, match="Lean catalog preflight manifest is stale"):
        load_paper_catalogs(..., lean_preflight_manifest_path=stale_path)
    assert calls == 0
```

- [ ] **Step 2: 运行 RED，确认当前 loader 真实调用 checker**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_catalog.py -k "matching_manifest_without_checker or stale_manifest" -q
```

Expected: new assertions fail because current loader calls `_run_lean_catalog_preflight()`。

- [ ] **Step 3: 实现 expected-entry 枚举与静态 manifest 校验**

`load_paper_catalogs()` 新增默认参数：

```python
lean_preflight_manifest_path: str | Path = Path(
    "benchmarks/paper/lean_checker_preflight.v1.json"
)
```

loader 对当前 direct cases 和可检查 graph nodes 重算 source/proof/environment/checker digests，验证 manifest coverage 并从 manifest 派生现有 `lean_preflight_summary` / `lean_lemma_graph_preflight_summary` shape。删除 loader 中的 `_LEAN_PREFLIGHT_CACHE` 和隐式 subprocess loops；保留显式 audit API 使用的 case-to-payload helpers。

- [ ] **Step 4: 生成初始 tracked manifest 后运行 GREEN**

先用一次性显式 audit API 生成 manifest；该动作只调用本地 Lean，provider calls 为 0。随后运行：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_paper_catalog.py tests/experiments/test_lean_lemma_graph_catalog.py -q
```

Expected: loader tests pass，并由 spy 证明普通 load checker calls 为 0。

- [ ] **Step 5: 提交 Task 2**

```powershell
git add src/tokenshare/experiments/paper_catalog.py tests/experiments/test_paper_catalog.py tests/experiments/test_lean_lemma_graph_catalog.py benchmarks/paper/lean_checker_preflight.v1.json
git commit -m "feat: separate lean catalog loading from audit"
```

### Task 3: 增量、force-all 与原子 refresh CLI

**Files:**
- Modify: `src/tokenshare/experiments/lean_catalog_audit.py`
- Modify: `tests/experiments/test_lean_catalog_audit.py`
- Create: `tests/experiments/test_lean_catalog_audit_cli.py`

- [ ] **Step 1: 写 reuse/recheck/force-all/atomic RED**

```python
def test_incremental_audit_rechecks_only_changed_entry(tmp_path) -> None:
    result = audit_lean_catalog(entries=_entries(one_changed=True), cache_root=tmp_path)
    assert result.reused_entry_count == 599
    assert result.rechecked_entry_count == 1

def test_force_all_rechecks_every_entry(tmp_path) -> None:
    result = audit_lean_catalog(entries=_entries(), cache_root=tmp_path, force_all=True)
    assert result.reused_entry_count == 0
    assert result.rechecked_entry_count == 600

def test_failed_refresh_preserves_last_good_manifest(tmp_path) -> None:
    before = manifest_path.read_bytes()
    with pytest.raises(LeanCatalogAuditError):
        refresh_manifest(..., checker=_rejecting_checker)
    assert manifest_path.read_bytes() == before
```

- [ ] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_lean_catalog_audit.py tests/experiments/test_lean_catalog_audit_cli.py -q
```

Expected: missing incremental audit/CLI APIs。

- [ ] **Step 3: 实现 audit result、gitignored cache 和 CLI**

CLI 固定支持：

```text
python -m tokenshare.experiments.lean_catalog_audit --verify
python -m tokenshare.experiments.lean_catalog_audit --verify --force-all
python -m tokenshare.experiments.lean_catalog_audit --refresh --force-all
```

cache root 使用 `local/cache/lean_checker/`；manifest refresh 使用同目录临时文件、flush/close 后 `Path.replace()`。输出稳定包含 `total_entry_count,reused_entry_count,rechecked_entry_count,canary_entry_count,invalidated_by,status,provider_calls_made=0`。

- [ ] **Step 4: 运行 GREEN 和无覆盖写入检查**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_lean_catalog_audit.py tests/experiments/test_lean_catalog_audit_cli.py -q
git status --short -- local outputs
```

Expected: tests pass；cache/audit output 不进入 tracked changes。

- [ ] **Step 5: 提交 Task 3**

```powershell
git add src/tokenshare/experiments/lean_catalog_audit.py tests/experiments/test_lean_catalog_audit.py tests/experiments/test_lean_catalog_audit_cli.py
git commit -m "feat: add incremental lean catalog audit"
```

### Task 4: Checker callable、spy fake 与真实 canary

**Files:**
- Modify: `src/tokenshare/plugins/lean_proof/checker.py`
- Modify: `src/tokenshare/plugins/lean_proof/child_proof.py`
- Modify: `src/tokenshare/plugins/lean_proof/merge_policy.py`
- Modify: `src/tokenshare/experiments/lean_paper_adapter.py`
- Create: `tests/support/lean_checker.py`
- Modify: `tests/experiments/test_lean_paper_adapter.py`
- Create: `tests/plugins/lean_proof/test_lean_checker_injection.py`
- Create: `verification/lean-canary-tests.txt`

- [ ] **Step 1: 写 checker spy 调用、mode 和 rejected pollution RED**

```python
def test_child_and_merge_use_injected_checker_and_expected_modes(...) -> None:
    checker = RecordingLeanChecker()
    run_lean_paper_case(..., checker=checker)
    assert checker.modes == [LeanCheckerMode.CHILD_PROOF, LeanCheckerMode.MERGE_PROOF]

def test_rejected_fake_checker_never_reaches_canonical_or_paper_eligible(...) -> None:
    result = run_lean_paper_case(..., checker=RecordingLeanChecker.reject_all())
    assert result.task_result.accepted_validity is False
    assert result.task_result.paper_eligible is False
```

- [ ] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_checker_injection.py -q
```

Expected: functions do not accept `checker=`。

- [ ] **Step 3: 实现 `LeanChecker` Protocol 和显式向下传递**

所有 public production entry 使用：

```python
checker: LeanChecker = check_lean_proof
```

并向 child/node/merge/root helper 逐层传递。`RecordingLeanChecker` 保存真实 schema-compatible report/log/proof artifacts，`command_summary.backend="test_fake"`。正式 CLI 不提供 backend 选择参数。

- [ ] **Step 4: 建立固定真实 canary manifest 并运行 GREEN**

`verification/lean-canary-tests.txt` 固定列出 direct accepted/rejected/sorry、helper import、child 和 merge/root nodeids。运行：

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_checker_injection.py tests/experiments/test_lean_paper_adapter.py -q
```

Expected: adapter regression 使用 fake；canary 文件中的 nodeids 仍默认真实 subprocess。

- [ ] **Step 5: 提交 Task 4**

```powershell
git add src/tokenshare/plugins/lean_proof/checker.py src/tokenshare/plugins/lean_proof/child_proof.py src/tokenshare/plugins/lean_proof/merge_policy.py src/tokenshare/experiments/lean_paper_adapter.py tests/support/lean_checker.py tests/plugins/lean_proof/test_lean_checker_injection.py tests/experiments/test_lean_paper_adapter.py verification/lean-canary-tests.txt
git commit -m "test: inject lean checker and keep real canaries"
```

### Task 5: Lake 环境一次 bootstrap 与 direct Lean

**Files:**
- Modify: `src/tokenshare/plugins/lean_proof/checker.py`
- Modify: `tests/plugins/lean_proof/test_lean_checker_direct.py`
- Create: `tests/plugins/lean_proof/test_lean_checker_environment_cache.py`

- [ ] **Step 1: 写 Lake/direct 等价、allowlist 和 cache isolation RED**

```python
def test_direct_checker_bootstraps_lake_environment_once(monkeypatch) -> None:
    check_lean_proof(_request("a"), ...)
    check_lean_proof(_request("b"), ...)
    assert bootstrap_calls == 1
    assert direct_lean_calls == 2

def test_bootstrap_cache_does_not_retain_secret(monkeypatch) -> None:
    env = prepared_lean_environment(...)
    assert "TOKENSHARE_TEST_SECRET" not in env
```

- [ ] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_checker_environment_cache.py -q
```

Expected: bootstrap/cache APIs missing。

- [ ] **Step 3: 实现 allowlisted Lake environment cache**

使用一次 `[lake_executable, "env", sys.executable, "-c", <json env script>]` 捕获 allowlist，cache key 为 `environment_digest + lake_executable + lean_executable + project_root`。每次执行把 allowlist 合并到当前 `os.environ` 后调用 `[lean_executable, generated_source]`；bootstrap 失败返回 `environment_error`，不回退系统 Lean。

- [ ] **Step 4: 运行真实等价与计时 GREEN**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/plugins/lean_proof/test_lean_checker_environment_cache.py tests/plugins/lean_proof/test_lean_checker_direct.py -q --durations=10
```

Expected: behavior passes；后续 real checker 调用不再经过 `lake env lean`。

- [ ] **Step 5: 提交 Task 5**

```powershell
git add src/tokenshare/plugins/lean_proof/checker.py tests/plugins/lean_proof/test_lean_checker_direct.py tests/plugins/lean_proof/test_lean_checker_environment_cache.py
git commit -m "perf: reuse lake environment for lean checker"
```

### Task 6: 单一验证入口与三档命令

**Files:**
- Create: `verification/run_verification.py`
- Modify: `init.ps1`
- Modify: `init.sh`
- Modify: `tests/test_init_verification_profiles.py`
- Modify: `verification/fast-tests.txt`

- [ ] **Step 1: 写单 conda 入口、LeanAudit 参数和 canary coverage RED**

```python
def test_startup_scripts_use_one_python_verification_entry() -> None:
    assert powershell.count("conda run") == 1
    assert bash.count('run -n "$CONDA_ENV" python') == 1

def test_profiles_expose_full_and_lean_audit() -> None:
    assert "[switch] $LeanAudit" in powershell
    assert "--lean-audit" in bash
    assert "verification/lean-canary-tests.txt" in runner
```

- [ ] **Step 2: 运行 RED**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/test_init_verification_profiles.py -q
```

Expected: scripts still contain four conda calls and no LeanAudit option。

- [ ] **Step 3: 实现 Python runner 与薄 shell wrappers**

runner 接受 `--mode fast|full`、`--lean-audit`、`--force-all-lean-audit`；完成 harness check、subprocess compileall、pytest 和 audit。Full pytest 运行完整 tests，但 catalog loader 只静态验证 manifest；真实 canary 由固定 nodeid manifest保证。PowerShell/Bash 各只调用一次 conda Python。

- [ ] **Step 4: 运行 profile GREEN 和 Fast**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/test_init_verification_profiles.py -q
.\init.ps1
```

Expected: profile tests pass；Fast 无真实 Lean checker 调用。

- [ ] **Step 5: 提交 Task 6**

```powershell
git add verification/run_verification.py verification/fast-tests.txt verification/lean-canary-tests.txt init.ps1 init.sh tests/test_init_verification_profiles.py
git commit -m "build: add lean-aware verification profiles"
```

### Task 7: 影响范围、全量审计与耗时验证

**Files:**
- Modify: `tests/experiments/test_lean_catalog_audit.py`
- Modify: `tests/experiments/test_lean_catalog_audit_cli.py`
- Modify: `benchmarks/paper/lean_checker_preflight.v1.json`

- [ ] **Step 1: 对 checker/toolchain/helper/单 entry/未知依赖写失效矩阵 RED**

每个 mutation 分别断言 `rechecked_entry_count` 和 `invalidated_by`；共享 checker/toolchain/helper/unknown 必须等于完整 coverage，单 entry 只等于其依赖闭包。

- [ ] **Step 2: 运行 RED 后补齐最小失效实现**

```powershell
$env:PYTHONPATH='src'
conda run -n tokenshare python -m pytest tests/experiments/test_lean_catalog_audit.py tests/experiments/test_lean_catalog_audit_cli.py -q
```

Expected: RED 先显示当前失效范围过宽或过窄；GREEN 后全部通过。

- [ ] **Step 3: 运行 Fast、Full 和强制 LeanAudit 并记录墙钟**

```powershell
Measure-Command { .\init.ps1 }
Measure-Command { .\init.ps1 -Full }
Measure-Command { .\init.ps1 -Full -LeanAudit -ForceAllLeanAudit }
```

Expected: 三档通过；force-all 输出完整 coverage、`reused_entry_count=0`、`provider_calls_made=0`。不得预设绝对秒数，记录实测。

- [ ] **Step 4: 提交 Task 7**

```powershell
git add tests/experiments/test_lean_catalog_audit.py tests/experiments/test_lean_catalog_audit_cli.py benchmarks/paper/lean_checker_preflight.v1.json
git commit -m "test: verify lean audit invalidation matrix"
```

### Task 8: 用通用方案重写迁移计划检查环节并同步状态

**Files:**
- Modify: `Doc/TechnicalDocument/2026-07-22-feat-011-system-runtime-paper-experiment-migration-plan.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `Doc/agent-navigation.md`
- Modify: `Doc/TechnicalDocument/tokenshare_v1_code_map.md`
- Modify: `feature_list.json`
- Modify: `progress.md`
- Modify: `session-handoff.md`

- [ ] **Step 1: 修改迁移计划的检查矩阵**

Task 1–4 使用 Fast/targeted；Task 5 使用 affected-entry audit + real canary；Task 6–9 使用 targeted + canary，除非摘要规则自动判定全量失效；Task 10 只保留一个最终门禁：

```powershell
.\init.ps1 -Full -LeanAudit -ForceAllLeanAudit
```

迁移计划只引用通用设计/CLI，不复制 entry key、失效算法或 checker backend 规则。

- [ ] **Step 2: 同步通用完成门槛和 code map**

AGENTS/README/navigation 说明：普通 feature 完成运行 Full；Lean catalog/plugin/toolchain 和论文发布运行 Full+LeanAudit；正式论文发布使用 force-all。code map 记录新模块、manifest、cache 和验证入口。

- [ ] **Step 3: 写验证证据和后续边界**

`feature_list.json`、`progress.md`、`session-handoff.md` 记录 Fast/Full/force-all 的测试数量、wall-clock、checker counts、provider calls=0、commit 和下一步迁移 Task 1。

- [ ] **Step 4: 最终文档/JSON/diff 检查**

```powershell
conda run -n tokenshare python -c "import json; from pathlib import Path; json.loads(Path('feature_list.json').read_text(encoding='utf-8')); print('feature-list-json-ok')"
git diff --check
git status --short
```

Expected: JSON/diff checks pass；未暂存无关用户改动。

- [ ] **Step 5: 提交本 feature 的状态同步**

只暂存本 feature 明确修改的文档和状态文件，检查 staged diff 后提交：

```powershell
git diff --cached --check
git commit -m "docs: apply lean verification profiles to migration plan"
```
