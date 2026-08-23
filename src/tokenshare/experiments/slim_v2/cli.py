"""Slim V2 唯一命令行编排入口。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .case_source import load_cases
from .profiles import (
    ChallengePlanV1,
    EXP4_NO_REQUEUE_MODES,
    InventoryV1,
    PlanV1,
    build_inventory,
    build_plan,
    build_profile,
    coverage_tail_required_by_downstream,
    downstream_trace_consumer_case_ids,
    project_root_inventory_rows,
)
from .schema import (
    LEGACY_PRICING_VERSION,
    PRE_FLASH_CONFIGURED_MODEL,
    PRE_FLASH_EXP2_ROOT_KEY,
    PRE_FLASH_PROVIDER_ENTRY_ID,
    PRE_FLASH_REPRESENTATIVE_RUN_ID,
    AblationObservationV1,
    ProviderEntryViewV1,
    RootInventoryV1,
    RootResultV2,
    SlimRunConfigV1,
)
from .runtime import coverage_tail_blocked
from .storage import RunStore, scan_resume


_EXPERIMENT_ORDER = ("exp1", "exp2", "exp3", "exp4", "exp5")
_FIXED_SOURCE_EXPERIMENTS = frozenset({"exp2", "exp3", "exp4"})
_PRE_FLASH_LEGACY_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "profile_id",
        "experiment_ids",
        "source_run_dir",
        "exp1_provider_config_path",
        "exp5_provider_config_path",
        "local_secret_config_path",
        "pricing_version",
        "ordinary_parallel_backend_kind",
        "response_max_bytes",
        "reducer_workers",
    }
)
_REPO_ROOT = Path(__file__).parents[4]
_DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "TokenShareData" / "outputs" / "slim_v2"
_NEXT_ATOMIC_WRITE_BYTES = 64 * 1024
_MAX_HTTP_RESPONSE_BYTES = 16 * 1024**2
_TRACE_ATTEMPT_WRITE_BYTES = 64 * 1024
_ROOT_ARTIFACT_WRITE_BYTES = 256 * 1024
_PROVIDER_CALL_OVERHEAD_BYTES = 24 * 1024
_PROVIDER_ATOMIC_MULTIPLIER_NUMERATOR = 5
_PROVIDER_ATOMIC_MULTIPLIER_DENOMINATOR = 4


@dataclass(frozen=True, slots=True)
class _CliRootContext:
    """生产装配与测试 fake 共用的窄 root 边界。"""

    run_id: str
    profile_id: str
    inventory: RootInventoryV1
    root_input: Mapping[str, Any]
    run_store: RunStore
    source_run_dir: Path | None
    challenge_plan: ChallengePlanV1 | None
    is_reference: bool
    provider_entries: Mapping[str, ProviderEntryViewV1]
    max_retries: int
    continue_after_terminal_child_failure: bool
    protocol_execution_attempt_upper: int
    provider_call_upper: int
    coverage_tail_required_by_downstream: bool = True


@dataclass(frozen=True, slots=True)
class _CliServices:
    """CLI 只注入 root 装配、Exp1 tail 恢复与纯边界依赖。"""

    execute_root: Callable[[_CliRootContext], RootResultV2]
    resume_root: Callable[[_CliRootContext, Mapping[str, Any]], RootResultV2]
    reduce_run: Callable[[str | Path], Any]
    provider_preflight: Callable[
        [Sequence[str], Sequence[RootInventoryV1]],
        Mapping[str, ProviderEntryViewV1],
    ]
    disk_free_bytes: Callable[[Path], int]
    lean_environment_preflight: Callable[[Path], Mapping[str, Any]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenshare-slim-v2")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    _profile_and_run(plan)
    plan.add_argument("--representative-raw-response-p95-bytes", type=int)

    run = commands.add_parser("run")
    run.add_argument("--experiment", choices=_EXPERIMENT_ORDER, required=True)
    _profile_and_run(run)
    run.add_argument("--source-run-dir")
    run.add_argument("--resume", action="store_true")

    run_all = commands.add_parser("run-all")
    _profile_and_run(run_all)
    run_all.add_argument("--resume", action="store_true")

    reduce = commands.add_parser("reduce")
    reduce.add_argument("--run-dir", required=True)

    compare_exp5_v4 = commands.add_parser("compare-exp5-v4-reference")
    compare_exp5_v4.add_argument("--run-dir", required=True)
    compare_exp5_v4.add_argument("--source-run-dir", required=True)

    representative = commands.add_parser("representative")
    representative.add_argument("--run-id", required=True)
    representative.add_argument("--output-root")
    representative.add_argument("--resume", action="store_true")

    lean_environment = commands.add_parser("lean-environment-test")
    lean_environment.add_argument("--pass-path")
    return parser


def _profile_and_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile", choices=("representative", "full"), required=True
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root")


def _plan_payload(plan: PlanV1, run_id: str) -> dict[str, Any]:
    return {
        "profile_id": plan.profile_id,
        "run_id": run_id,
        "paper_root_count": plan.paper_root_count,
        "execution_root_count": plan.execution_root_count,
        "online_provider_call_upper": plan.online_provider_call_upper,
        "estimate": {
            "response_bytes": plan.estimated_response_bytes,
            "bytes": plan.estimate_bytes,
            "gib": plan.estimate_gib,
        },
        "hard_upper": {
            "response_bytes": plan.hard_response_bytes,
            "bytes": plan.hard_upper_bytes,
            "gib": plan.hard_upper_gib,
            "online_response_artifact_bytes": (
                plan.online_response_artifact_hard_upper_bytes
            ),
            "online_response_artifact_gib": (
                plan.online_response_artifact_hard_upper_gib
            ),
        },
        "per_root_free_space_margin": {
            "bytes": plan.per_root_free_space_margin_bytes,
            "gib": plan.per_root_free_space_margin_gib,
        },
        "experiments": {
            experiment_id: {
                "paper_root_count": item.paper_root_count,
                "reference_root_count": item.reference_root_count,
                "planned_first_attempt_ai_units": (
                    item.planned_first_attempt_ai_units
                ),
                "protocol_execution_attempt_upper": (
                    item.protocol_execution_attempt_upper
                ),
                "provider_call_upper": item.provider_call_upper,
            }
            for experiment_id, item in plan.experiments.items()
        },
        "exp3_references": {
            "planned_first_attempt_ai_units": (
                plan.exp3_reference_planned_first_attempt_ai_units
            ),
            "protocol_execution_attempt_upper": (
                plan.exp3_reference_protocol_execution_attempt_upper
            ),
        },
    }


def _production_execute_root(context: _CliRootContext) -> RootResultV2:
    # Task 6B 在 runtime 中实现生产装配；延迟导入保持 plan/reduce 纯离线。
    from . import runtime

    execute = getattr(runtime, "execute_root_context", None)
    if not callable(execute):
        raise RuntimeError("Slim V2 production root assembly is unavailable")
    return execute(context)


def _production_resume_root(
    context: _CliRootContext,
    protocol: Mapping[str, Any],
) -> RootResultV2:
    from . import runtime

    resume = getattr(runtime, "resume_root_context", None)
    if not callable(resume):
        raise RuntimeError("Slim V2 protocol resume is unavailable")
    return resume(context, protocol)


def _disk_free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


def _default_services() -> _CliServices:
    from .reducer import reduce_run
    from .lean_environment import validate_lean_environment_pass

    return _CliServices(
        execute_root=_production_execute_root,
        resume_root=_production_resume_root,
        reduce_run=reduce_run,
        provider_preflight=_preflight_provider_entries,
        disk_free_bytes=_disk_free_bytes,
        lean_environment_preflight=lambda root: validate_lean_environment_pass(root),
    )


def _provider_config_paths(
    experiment_ids: Sequence[str], repo_root: Path
) -> tuple[Path, ...]:
    paths: list[Path] = []
    if "exp1" in experiment_ids:
        paths.append(
            repo_root / "benchmarks" / "paper" / "exp1_baseline_provider_config.v3.json"
        )
    if "exp5" in experiment_ids:
        paths.append(
            repo_root / "benchmarks" / "paper" / "exp5_siliconflow_provider_config.v3.json"
        )
    return tuple(paths)


def _preflight_provider_entries(
    experiment_ids: Sequence[str],
    roots: Sequence[RootInventoryV1],
    *,
    repo_root: str | Path = _REPO_ROOT,
) -> dict[str, ProviderEntryViewV1]:
    """只校验运行身份与 secret；价格字段完全不参与启动判断。"""

    entries: dict[str, ProviderEntryViewV1] = {}
    for path in _provider_config_paths(experiment_ids, Path(repo_root)):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"provider config cannot be read: {path}") from exc
        provider_family = document.get("provider_family")
        raw_entries = document.get("entries")
        if not isinstance(provider_family, str) or not isinstance(raw_entries, list):
            raise RuntimeError(f"provider config inventory is invalid: {path}")
        for raw in raw_entries:
            if not isinstance(raw, Mapping) or raw.get("enabled") is not True:
                continue
            entry = ProviderEntryViewV1(
                provider_family=provider_family,
                entry_id=raw.get("entry_id"),
                base_url=raw.get("base_url"),
                endpoint=raw.get("endpoint"),
                api_key_env=raw.get("api_key_env"),
                configured_model=raw.get("model"),
                request_overrides=dict(raw.get("request_overrides") or {}),
                supports_json_mode=raw.get("supports_json_mode"),
            )
            entry.validate()
            entry_id = str(entry.entry_id)
            if entry_id in entries:
                raise RuntimeError(f"duplicate provider entry: {entry_id}")
            entries[entry_id] = entry

    _inject_local_secrets(Path(repo_root), entries)
    for entry in entries.values():
        if not os.environ.get(str(entry.api_key_env)):
            raise RuntimeError(
                f"missing provider secret/API key env var: {entry.api_key_env}"
            )

    for root in roots:
        entry = entries.get(str(root.provider_entry_id))
        if entry is None:
            raise RuntimeError(
                f"provider entry is absent for root {root.condition_id}: "
                f"{root.provider_entry_id}"
            )
        if entry.configured_model != root.configured_model:
            raise RuntimeError(
                f"provider model differs from root inventory: {root.condition_id}"
            )
    return entries


def _inject_local_secrets(
    repo_root: Path,
    entries: Mapping[str, ProviderEntryViewV1],
) -> None:
    """只把 gitignored 明文 key 注入 tracked entry 指定的进程 env。"""

    path = repo_root / "local" / "ai_api_smoke.local.json"
    if not path.is_file():
        return
    try:
        body = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local provider secret config cannot be read") from exc
    if not isinstance(body, Mapping):
        raise RuntimeError("local provider secret config must be an object")
    candidates = body.get("entries", [])
    if not isinstance(candidates, list):
        raise RuntimeError("local provider secret entries must be an array")
    by_env = {str(item.api_key_env): item for item in entries.values()}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise RuntimeError("local provider secret entry must be an object")
        secret = candidate.get("api_key")
        if not isinstance(secret, str) or not secret.strip():
            continue
        normalized = secret.strip().upper()
        if normalized.startswith("PASTE_") or normalized.startswith("REPLACE_"):
            continue
        entry = entries.get(str(candidate.get("entry_id")))
        if entry is None:
            entry = by_env.get(str(candidate.get("api_key_env")))
        if entry is not None:
            os.environ[str(entry.api_key_env)] = secret


def _case_inputs(repo_root: Path = _REPO_ROOT) -> dict[str, Mapping[str, Any]]:
    paths = (
        repo_root / "benchmarks" / "paper" / "factorization_catalog.v2.jsonl",
        repo_root / "benchmarks" / "paper" / "lean_lemma_graph_catalog.v1.jsonl",
    )
    cases: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        for item in load_cases(path):
            case_id = str(item["case_id"])
            if case_id in cases:
                raise RuntimeError(f"duplicate catalog case_id: {case_id}")
            cases[case_id] = item
    return cases


def _root_key(root: RootInventoryV1) -> tuple[str, str, str, int]:
    return (
        str(root.experiment_id),
        str(root.condition_id),
        str(root.case_id),
        int(root.repeat_id),
    )


def _selected_roots(
    inventory: InventoryV1,
    experiment_id: str,
) -> tuple[tuple[RootInventoryV1, bool], ...]:
    rows = project_root_inventory_rows(inventory)
    selected = [
        (root, False)
        for root in rows.roots
        if root.experiment_id == experiment_id
    ]
    if experiment_id == "exp3":
        selected.extend((root, True) for root in rows.exp3_references)
    keys = [_root_key(root) for root, _is_reference in selected]
    if len(keys) != len(set(keys)):
        raise RuntimeError(f"duplicate root dispatch identity in {experiment_id}")
    return tuple(selected)


def _challenge_map(inventory: InventoryV1) -> dict[str, ChallengePlanV1]:
    result = {item.challenge_plan_id: item for item in inventory.challenges}
    if len(result) != len(inventory.challenges):
        raise RuntimeError("duplicate Exp4 challenge plan identity")
    return result


def _root_caps(
    root: RootInventoryV1,
    profile_id: str,
) -> tuple[int, bool, int, int]:
    controls = build_profile(profile_id).experiments[str(root.experiment_id)]
    planned = len(root.planned_ai_unit_ids)
    if root.experiment_id == "exp3" and root.dead_worker_count is not None:
        protocol_upper = planned * 4
    elif root.experiment_id == "exp4" and root.mode in EXP4_NO_REQUEUE_MODES:
        protocol_upper = planned
    else:
        protocol_upper = planned * (controls.max_retries + 1)
    provider_upper = (
        protocol_upper
        if root.experiment_id == "exp1"
        else planned
        if root.experiment_id == "exp5"
        else 0
    )
    return (
        controls.max_retries,
        controls.continue_after_terminal_child_failure,
        protocol_upper,
        provider_upper,
    )


def _required_root_write_bytes(root: RootInventoryV1, profile_id: str) -> int:
    """按下一个 root 的冻结 attempt 上限估算其原子写空间。"""

    _retries, _continue, protocol_upper, provider_upper = _root_caps(
        root, profile_id
    )
    provider_call_bytes = (
        _PROVIDER_ATOMIC_MULTIPLIER_NUMERATOR
        * (2 * _MAX_HTTP_RESPONSE_BYTES + _PROVIDER_CALL_OVERHEAD_BYTES)
        // _PROVIDER_ATOMIC_MULTIPLIER_DENOMINATOR
    )
    return (
        _NEXT_ATOMIC_WRITE_BYTES
        + _ROOT_ARTIFACT_WRITE_BYTES
        + protocol_upper * _TRACE_ATTEMPT_WRITE_BYTES
        + provider_upper * provider_call_bytes
    )


def _run_lock_path(run_dir: Path) -> Path:
    return run_dir.parent / f".{run_dir.name}.slim-v2.lock"


def _pid_is_running(pid: int) -> bool:
    """用 Windows 进程句柄检查 owner，避免把 os.kill 用于 Windows。"""

    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _RunLock:
    """run 目录之外的最小互斥锁；只清理本 owner 的 token。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._token = uuid4().hex
        self._held = False

    def __enter__(self) -> "_RunLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                with self._path.open("x", encoding="utf-8") as handle:
                    json.dump({"pid": os.getpid(), "token": self._token}, handle)
                    handle.flush()
                self._held = True
                return self
            except FileExistsError:
                owner = self._read_owner()
                if owner is None or _pid_is_running(owner["pid"]):
                    raise RuntimeError(f"run lock is held: {self._path}")
                if not self._remove_stale_owner(owner["token"]):
                    continue

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._held:
            return
        try:
            try:
                owner = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return
            if isinstance(owner, Mapping) and owner.get("token") == self._token:
                self._path.unlink(missing_ok=True)
        finally:
            self._held = False

    def _read_owner(self) -> dict[str, int | str] | None:
        try:
            owner = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(owner, Mapping):
            return None
        pid = owner.get("pid")
        token = owner.get("token")
        if not isinstance(pid, int) or not isinstance(token, str) or not token:
            return None
        return {"pid": pid, "token": token}

    def _remove_stale_owner(self, token: str) -> bool:
        """删除前重读 token，避免在观察与回收间删除替换后的 live lock。"""

        current = self._read_owner()
        if current is None or current["token"] != token:
            return False
        if _pid_is_running(current["pid"]):
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        return True


def _validate_caps(
    *,
    profile_id: str,
    plan: PlanV1,
    selected: Mapping[str, tuple[tuple[RootInventoryV1, bool], ...]],
) -> None:
    provider_total = 0
    for experiment_id, roots in selected.items():
        paper_protocol = 0
        reference_protocol = 0
        provider = 0
        for root, is_reference in roots:
            _retries, _continue, protocol_upper, provider_upper = _root_caps(
                root, profile_id
            )
            if is_reference:
                reference_protocol += protocol_upper
            else:
                paper_protocol += protocol_upper
            provider += provider_upper
        expected = plan.experiments[experiment_id]
        if paper_protocol != expected.protocol_execution_attempt_upper:
            raise RuntimeError(f"{experiment_id} protocol attempt cap mismatch")
        if provider != expected.provider_call_upper:
            raise RuntimeError(f"{experiment_id} provider call cap mismatch")
        if experiment_id == "exp3" and reference_protocol != (
            plan.exp3_reference_protocol_execution_attempt_upper
        ):
            raise RuntimeError("exp3 reference attempt cap mismatch")
        provider_total += provider
    expected_total = sum(
        plan.experiments[item].provider_call_upper for item in selected
    )
    if provider_total != expected_total or provider_total > plan.online_provider_call_upper:
        raise RuntimeError("global provider call cap mismatch")


def _source_closure(
    roots: Sequence[RootInventoryV1],
    source_run_dir: Path,
    cases: Mapping[str, Mapping[str, Any]],
) -> None:
    """Exp2-4 只能消费显式 source 的 committed Exp1 V2 结果与 traces。"""

    source = RunStore(source_run_dir)
    referenced_case_ids = tuple(dict.fromkeys(str(root.case_id) for root in roots))
    try:
        source_roots_by_case: dict[str, RootInventoryV1] = {}
        for source_root in source.iter_root_inventory_rows("roots"):
            if source_root.experiment_id != "exp1" or source_root.repeat_id != 0:
                continue
            case_id = str(source_root.case_id)
            if case_id in source_roots_by_case:
                raise ValueError(f"duplicate source Exp1 root inventory for {case_id}")
            source_roots_by_case[case_id] = source_root
        for case_id in referenced_case_ids:
            source_root = source_roots_by_case.get(case_id)
            if source_root is None:
                raise ValueError(f"missing source Exp1 root inventory for {case_id}")
            source.read_root_result(
                "exp1",
                str(source_root.condition_id),
                case_id,
                0,
            )
    except (OSError, ValueError) as exc:
        raise RuntimeError("source Exp1 root result is missing or invalid") from exc

    for root in roots:
        for planned in root.planned_ai_unit_ids:
            try:
                trace = source.read_trace(str(root.case_id), 0, planned)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"source closure is incomplete for {root.case_id}:{planned}"
                ) from exc
            if (
                trace.case_id != root.case_id
                or trace.domain != root.domain
                or trace.provider_entry_id != root.provider_entry_id
                or trace.configured_model != root.configured_model
                or trace.requested_model != root.configured_model
                or trace.resolved_model != root.configured_model
            ):
                raise RuntimeError(
                    f"source trace identity/model mismatch for {root.case_id}:{planned}"
                )
            _validate_source_trace_semantics(root, trace, cases[str(root.case_id)])
            for attempt in trace.attempts:
                if attempt.raw_response_relative_path is not None:
                    try:
                        source.read_relative_response(
                            attempt.raw_response_relative_path
                        )
                    except (OSError, ValueError) as exc:
                        raise RuntimeError(
                            "source response closure is incomplete for "
                            f"{root.case_id}:{planned}"
                        ) from exc


def _validate_source_trace_semantics(
    root: RootInventoryV1,
    trace: Any,
    case: Mapping[str, Any],
) -> None:
    """用当前插件公开固定拆分规则确认复用 trace 的领域语义。"""

    try:
        if root.domain == "factorization":
            from tokenshare.plugins.factorization import partition_candidate_ranges

            split_params = case["split_params"]
            if not isinstance(split_params, Mapping):
                raise ValueError("factorization case lacks split_params")
            partition = partition_candidate_ranges(
                target_n=case["target_n"],
                requested_child_count=int(split_params["requested_child_count"]),
                max_children_per_unit=int(case["candidate_divisor_count"]),
                min_divisor=case["candidate_start"],
                max_divisor=case["candidate_end"],
            )
            expected = {
                f"range_{item.child_index}": item for item in partition.ranges
            }.get(str(trace.planned_ai_unit_id))
            if expected is None or (
                trace.candidate_start != int(expected.range_start)
                or trace.candidate_end != int(expected.range_end)
                or trace.lemma_node_id is not None
                or trace.dependency_path is not None
            ):
                raise ValueError("factorization trace range differs from fixed partition")
            return
        if root.domain == "lean":
            from tokenshare.plugins.lean_proof.fixed_plan import LeanFixedDecompositionPlan

            plan = LeanFixedDecompositionPlan.from_catalog_case(dict(case))
            planned = str(trace.planned_ai_unit_id)
            if planned not in plan.topological_order() or (
                trace.lemma_node_id != planned
                or trace.dependency_path != list(plan.dependency_path(planned))
                or trace.candidate_start is not None
                or trace.candidate_end is not None
            ):
                raise ValueError("Lean trace differs from fixed lemma-DAG plan")
            return
        raise ValueError(f"unsupported source trace domain: {root.domain}")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"source trace semantic mismatch for {root.case_id}:"
            f"{trace.planned_ai_unit_id}"
        ) from exc


def _started_without_protocol(store: RunStore, root: RootInventoryV1) -> bool:
    events = store.system_root_directory(*_root_key(root)) / "events.jsonl"
    return events.is_file() and events.stat().st_size > 0


def _protocol_tail_pending(store: RunStore, root: RootInventoryV1) -> bool:
    """只把 snapshot 中尚无 committed coverage trace 的 target 视为付费待办。"""

    snapshot = store.read_root_protocol_snapshot(*_root_key(root))
    if (
        snapshot.protocol_projection is not None
        and snapshot.protocol_projection.trace_tail_status
        == "not_required_by_downstream"
    ):
        return False
    summary = snapshot.protocol_result.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("protocol snapshot lacks typed summary")
    if coverage_tail_blocked(summary):
        return False
    observation = summary.get("runtime_observation")
    if not isinstance(observation, Mapping):
        raise ValueError("protocol snapshot lacks runtime observation")
    targets = observation.get("unscheduled_ai_unit_ids")
    if not isinstance(targets, list) or any(
        not isinstance(item, str) or not item for item in targets
    ):
        raise ValueError("protocol snapshot lacks typed tail targets")
    for target in targets:
        path = store.trace_path(str(root.case_id), 0, target)
        if not path.is_file():
            return True
        trace = store.read_trace(str(root.case_id), 0, target)
        if trace.trace_origin != "coverage_tail":
            raise ValueError("committed tail target trace has invalid origin")
    return False


def _protocol_tail_requires_provider(store: RunStore, root: RootInventoryV1) -> bool:
    """仅当缺失 trace 的当前 ordinal 没有既有 journal 时才需要 secret。"""

    snapshot = store.read_root_protocol_snapshot(*_root_key(root))
    if (
        snapshot.protocol_projection is not None
        and snapshot.protocol_projection.trace_tail_status
        == "not_required_by_downstream"
    ):
        return False
    summary = snapshot.protocol_result.get("summary")
    if not isinstance(summary, Mapping) or coverage_tail_blocked(summary):
        return False
    observation = summary.get("runtime_observation")
    targets = (
        observation.get("unscheduled_ai_unit_ids")
        if isinstance(observation, Mapping)
        else None
    )
    if not isinstance(targets, list):
        return True
    snapshot_tail_ids = {
        trace.planned_ai_unit_id
        for trace in snapshot.traces
        if trace.trace_origin == "coverage_tail"
    }
    for target in targets:
        if not isinstance(target, str) or not target:
            return True
        if store.trace_path(str(root.case_id), 0, target).is_file():
            continue
        if target in snapshot_tail_ids:
            continue
        request = snapshot.tail_requests.get(target)
        hints = request.get("soft_hints") if isinstance(request, Mapping) else None
        ordinal = request.get("attempt_ordinal") if isinstance(request, Mapping) else None
        if (
            not isinstance(hints, Mapping)
            or hints.get("planned_ai_unit_id") != target
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 0
        ):
            return True
        call_key = ":".join((*_root_key(root)[:3], str(root.repeat_id), target, str(ordinal)))
        if not (
            store.call_intent_path(call_key).is_file()
            or store.call_terminal_path(call_key).is_file()
        ):
            return True
    return False


def _journal_resume_entry(root: RootInventoryV1) -> ProviderEntryViewV1:
    """仅用于既有 journal 的离线恢复；不得用于发起 transport。"""

    return ProviderEntryViewV1(
        provider_family="deepseek",
        entry_id=root.provider_entry_id,
        base_url="https://resume.invalid",
        endpoint="/not-used",
        api_key_env="SLIM_V2_JOURNAL_RESUME_NOT_USED",
        configured_model=root.configured_model,
        request_overrides={},
        supports_json_mode=True,
    )


def _invalid_root_result(
    root: RootInventoryV1,
    challenge: ChallengePlanV1 | None,
    *,
    failure_kind: str = "infrastructure_invalid",
    failure_origin: str = "root_unavailable_before_protocol",
) -> RootResultV2:
    """把死亡 root 固化为固定分母中的 early infra-invalid。"""

    exp1 = root.experiment_id == "exp1"
    exp2 = root.experiment_id == "exp2"
    exp3 = root.experiment_id == "exp3"
    exp4 = root.experiment_id == "exp4"
    reasons = {
        path: f"{failure_kind}_before_protocol"
        for path in RootResultV2.nullable_leaf_paths()
    }
    reasons.update(
        {
            "root_start_at_ms": "protocol_lifecycle_not_started",
            "root_terminal_at_ms": "protocol_lifecycle_not_started",
            "runtime_wall_clock_ms": "protocol_lifecycle_not_started",
            "resolved_model": "model_resolution_not_reached",
        }
    )
    disabled = [
        AblationObservationV1(disabled_mechanism=name)
        for name in root.disabled_mechanisms
    ]
    result = RootResultV2(
        experiment_id=root.experiment_id,
        condition_id=root.condition_id,
        case_id=root.case_id,
        repeat_id=root.repeat_id,
        domain=root.domain,
        difficulty=root.difficulty,
        topic_family=root.topic_family,
        position_stratum=root.position_stratum,
        mode=root.mode,
        disabled_mechanisms=list(root.disabled_mechanisms),
        worker_count=root.worker_count,
        fault_type=root.fault_type,
        fault_rate=root.fault_rate,
        dead_worker_count=root.dead_worker_count,
        kill_progress_target_ratio=root.kill_progress_target_ratio,
        challenge_plan_id=root.challenge_plan_id,
        challenge_family=(challenge.challenge_family if challenge else None),
        challenge_target_planned_ai_unit_ids=(
            list(root.planned_ai_unit_ids[:1]) if exp4 else None
        ),
        challenge_attempt_ordinal_rule=(
            challenge.attempt_rule if challenge else None
        ),
        provider_family="siliconflow" if root.experiment_id == "exp5" else "deepseek",
        provider_entry_id=root.provider_entry_id,
        configured_model=root.configured_model,
        requested_model=root.configured_model,
        resolved_model=None,
        reasoning_mode="not_reached",
        root_start_at_ms=None,
        root_terminal_at_ms=None,
        runtime_wall_clock_ms=None,
        trace_tail_started_at_ms=None,
        trace_tail_terminal_at_ms=None,
        trace_tail_wall_clock_ms=0 if exp1 else None,
        trace_tail_status="not_needed" if exp1 else None,
        trace_tail_target_ai_unit_ids=[] if exp1 else None,
        trace_tail_recorded_ai_unit_ids=[] if exp1 else None,
        trace_tail_success_unit_count=0 if exp1 else None,
        trace_tail_failure_unit_count=0 if exp1 else None,
        trace_tail_provider_attempt_count=0 if exp1 else None,
        trace_tail_total_tokens=0 if exp1 else None,
        trace_tail_cost_estimate_cny=0.0 if exp1 else None,
        preflight_status="blocked",
        protocol_started=False,
        root_status="infrastructure_error",
        final_result_present=False,
        verified_correct=False,
        failure_stage="preflight",
        failure_kind=failure_kind,
        failure_origin=failure_origin,
        planned_ai_unit_ids=list(root.planned_ai_unit_ids),
        dispatched_ai_unit_ids=[],
        completed_ai_unit_ids=[],
        unscheduled_ai_unit_ids=list(root.planned_ai_unit_ids),
        in_flight_ai_unit_ids_at_witness=[] if exp2 else None,
        observed_peak_concurrency=0 if exp2 else None,
        worker_execution_facts=[],
        required_slot_count=0 if exp3 or exp4 else None,
        recovered_valid_canonical_slot_count=0 if exp3 or exp4 else None,
        attempts=[],
        fault_target_planned_ai_unit_ids=[] if exp3 else None,
        fault_target_count=0 if exp3 else None,
        fault_observations=[],
        recovery_observations=[],
        worker_death_observations=[],
        challenge_observations=[],
        ablation_observations=disabled,
        missing_reason=reasons,
        not_applicable_reason={},
    )
    result.validate()
    return result


def _validate_result_identity(
    result: RootResultV2,
    context: _CliRootContext,
) -> None:
    if not isinstance(result, RootResultV2):
        raise TypeError("root service must return RootResultV2")
    result.validate()
    if _root_key(result) != _root_key(context.inventory):
        raise RuntimeError("root result identity differs from dispatched inventory")


def _commit_result(context: _CliRootContext, result: RootResultV2) -> None:
    _validate_result_identity(result, context)
    if not context.is_reference:
        context.run_store.write_root_result(result)
        return
    context.run_store.write_exp3_reference_result(result)


def _reference_committed(store: RunStore, root: RootInventoryV1) -> bool:
    return store.exp3_reference_result_path(
        str(root.condition_id), str(root.case_id), int(root.repeat_id)
    ).is_file()


def _write_fresh_run_files(
    store: RunStore,
    config: SlimRunConfigV1,
    inventory: InventoryV1,
) -> None:
    rows = project_root_inventory_rows(inventory)
    selected = frozenset(config.experiment_ids)
    store.write_frozen_inventories(
        conditions=(
            item for item in inventory.conditions if item.experiment_id in selected
        ),
        roots=(item for item in rows.roots if item.experiment_id in selected),
        exp3_references=(
            rows.exp3_references if "exp3" in selected else ()
        ),
        exp4_challenges=(inventory.challenges if "exp4" in selected else ()),
    )
    store.write_run_config(asdict(config))


def _validate_resume_config(store: RunStore, config: SlimRunConfigV1) -> None:
    try:
        document = store.read_run_config()
        if set(document) != set(SlimRunConfigV1.field_names()):
            raise ValueError("run config fields differ from SlimRunConfigV1")
        frozen = SlimRunConfigV1(**document)
        frozen.validate()
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("resume run config cannot be read") from exc
    if asdict(frozen) != asdict(config):
        raise RuntimeError("resume run config differs from this command")


def _is_pre_flash_representative_resume_request(
    *,
    run_id: str,
    profile_id: str,
    experiment_ids: Sequence[str],
    source_run_dir: str | None,
) -> bool:
    """只识别法定人数冻结的旧 cohort；不能成为一般 config 兼容开关。"""

    return (
        run_id == PRE_FLASH_REPRESENTATIVE_RUN_ID
        and profile_id == "representative"
        and tuple(experiment_ids) == _EXPERIMENT_ORDER
        and source_run_dir is None
    )


def _validate_pre_flash_representative_resume_config(
    store: RunStore,
) -> None:
    """旧单值价格 config 仅可作为唯一 exact cohort 的冻结事实读取。"""

    expected = {
        "schema_version": "tokenshare.slim_v2.run_config.v1",
        "run_id": PRE_FLASH_REPRESENTATIVE_RUN_ID,
        "profile_id": "representative",
        "experiment_ids": list(_EXPERIMENT_ORDER),
        "source_run_dir": None,
        "exp1_provider_config_path": "benchmarks/paper/exp1_baseline_provider_config.v3.json",
        "exp5_provider_config_path": "benchmarks/paper/exp5_siliconflow_provider_config.v3.json",
        "local_secret_config_path": "local/ai_api_smoke.local.json",
        "pricing_version": LEGACY_PRICING_VERSION,
        "ordinary_parallel_backend_kind": "thread",
        "response_max_bytes": 16 * 1024 * 1024,
        "reducer_workers": 1,
    }
    try:
        document = store.read_run_config()
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("pre-Flash resume config cannot be read") from exc
    if set(document) != _PRE_FLASH_LEGACY_CONFIG_FIELDS or document != expected:
        raise RuntimeError("pre-Flash resume config is not the exact frozen cohort")


def _validate_resume_inventory(
    store: RunStore,
    selected: Mapping[str, tuple[tuple[RootInventoryV1, bool], ...]],
) -> None:
    """resume 仍以已冻结 ordinary inventory 为身份事实。"""

    try:
        frozen_roots = {
            _root_key(root): root for root in store.read_root_inventory_rows("roots")
        }
        frozen_references = {
            _root_key(root): root
            for root in store.read_root_inventory_rows("exp3_references")
        }
    except (OSError, ValueError) as exc:
        raise RuntimeError("resume inventory cannot be read") from exc
    for experiment_roots in selected.values():
        for root, is_reference in experiment_roots:
            frozen = (frozen_references if is_reference else frozen_roots).get(
                _root_key(root)
            )
            if frozen != root:
                raise RuntimeError(
                    f"resume inventory differs for {_root_key(root)}"
                )


def _pre_flash_frozen_selected(
    store: RunStore,
    selected: Mapping[str, tuple[tuple[RootInventoryV1, bool], ...]],
) -> dict[str, tuple[tuple[RootInventoryV1, bool], ...]]:
    """把唯一旧 cohort 的已冻结 Pro rows 装入恢复上下文，拒绝任何其他差异。"""

    try:
        frozen_roots = {
            _root_key(root): root for root in store.read_root_inventory_rows("roots")
        }
        frozen_references = {
            _root_key(root): root
            for root in store.read_root_inventory_rows("exp3_references")
        }
    except (OSError, ValueError) as exc:
        raise RuntimeError("pre-Flash resume inventory cannot be read") from exc
    expected_roots = {
        _root_key(root)
        for roots in selected.values()
        for root, is_reference in roots
        if not is_reference
    }
    expected_references = {
        _root_key(root)
        for roots in selected.values()
        for root, is_reference in roots
        if is_reference
    }
    if set(frozen_roots) != expected_roots or set(frozen_references) != expected_references:
        raise RuntimeError("pre-Flash resume inventory keyset is not exact")
    restored: dict[str, tuple[tuple[RootInventoryV1, bool], ...]] = {}
    for experiment_id, experiment_roots in selected.items():
        recovered: list[tuple[RootInventoryV1, bool]] = []
        for current, is_reference in experiment_roots:
            frozen = (frozen_references if is_reference else frozen_roots)[
                _root_key(current)
            ]
            expected = (
                current
                if current.experiment_id == "exp5"
                else replace(
                    current,
                    provider_entry_id=PRE_FLASH_PROVIDER_ENTRY_ID,
                    configured_model=PRE_FLASH_CONFIGURED_MODEL,
                )
            )
            if frozen != expected:
                raise RuntimeError(
                    f"pre-Flash resume inventory differs for {_root_key(current)}"
                )
            recovered.append((frozen, is_reference))
        restored[experiment_id] = tuple(recovered)
    return restored


def _validate_committed_results(
    store: RunStore,
    selected: Mapping[str, tuple[tuple[RootInventoryV1, bool], ...]],
    committed_root_keys: frozenset[tuple[str, str, str, int]],
) -> None:
    """ordinary result 只有 typed 读回成功后才能成为 skip 事实。"""

    selected_references = {
        _root_key(root)
        for roots in selected.values()
        for root, is_reference in roots
        if is_reference and _reference_committed(store, root)
    }
    typed_references: set[tuple[str, str, str, int]] = set()
    if selected_references:
        for inventory, result in store.iter_inventory_results("exp3_references"):
            key = _root_key(inventory)
            if key in selected_references and result is not None:
                typed_references.add(key)
        if typed_references != selected_references:
            raise ValueError("committed Exp3 reference result is not typed-readable")
    selected_paper = {
        _root_key(root)
        for roots in selected.values()
        for root, is_reference in roots
        if not is_reference and _root_key(root) in committed_root_keys
    }
    for key in selected_paper:
        store.read_root_result(*key)


def run_experiment(
    *,
    experiment_ids: Sequence[str],
    profile_id: str,
    run_id: str,
    output_root: str | None,
    source_run_dir: str | None,
    resume: bool,
    reduce_after: bool,
    services: _CliServices | None = None,
) -> int:
    """串行运行一次可变实验，持有同一 run 的外部锁直到 reducer 完成。"""

    run_dir = Path(output_root) / run_id if output_root else _DEFAULT_OUTPUT_ROOT / run_id
    with _RunLock(_run_lock_path(run_dir)):
        return _run_experiment_locked(
            experiment_ids=experiment_ids,
            profile_id=profile_id,
            run_id=run_id,
            output_root=output_root,
            source_run_dir=source_run_dir,
            resume=resume,
            reduce_after=reduce_after,
            services=services,
        )


def _run_experiment_locked(
    *,
    experiment_ids: Sequence[str],
    profile_id: str,
    run_id: str,
    output_root: str | None,
    source_run_dir: str | None,
    resume: bool,
    reduce_after: bool,
    services: _CliServices | None = None,
) -> int:
    services = services or _default_services()
    config = SlimRunConfigV1(
        run_id=run_id,
        profile_id=profile_id,
        experiment_ids=list(experiment_ids),
        source_run_dir=source_run_dir,
    )
    config.validate()
    inventory = build_inventory(profile_id)
    plan = build_plan(profile_id)
    run_dir = Path(output_root) / run_id if output_root else _DEFAULT_OUTPUT_ROOT / run_id
    store = RunStore(run_dir)
    if run_dir.exists() and not resume:
        raise RuntimeError(f"run directory already exists; use --resume: {run_dir}")
    if resume and not run_dir.is_dir():
        raise RuntimeError(f"resume run directory does not exist: {run_dir}")
    pre_flash_resume = resume and _is_pre_flash_representative_resume_request(
        run_id=run_id,
        profile_id=profile_id,
        experiment_ids=experiment_ids,
        source_run_dir=source_run_dir,
    )
    if resume:
        if pre_flash_resume:
            _validate_pre_flash_representative_resume_config(store)
        else:
            _validate_resume_config(store, config)

    cases = _case_inputs()
    challenges = _challenge_map(inventory)
    trace_consumer_case_ids = downstream_trace_consumer_case_ids(profile_id)
    resume_view = scan_resume(run_dir) if resume else None
    selected_by_experiment = {
        experiment_id: _selected_roots(inventory, experiment_id)
        for experiment_id in experiment_ids
    }
    if pre_flash_resume:
        selected_by_experiment = _pre_flash_frozen_selected(
            store,
            selected_by_experiment,
        )
    _validate_caps(
        profile_id=profile_id,
        plan=plan,
        selected=selected_by_experiment,
    )
    if resume:
        if not pre_flash_resume:
            _validate_resume_inventory(store, selected_by_experiment)
        assert resume_view is not None
        _validate_committed_results(
            store,
            selected_by_experiment,
            resume_view.committed_root_keys,
        )
        if pre_flash_resume:
            uncommitted_exp1 = [
                _root_key(root)
                for root, is_reference in selected_by_experiment["exp1"]
                if not is_reference
                and _root_key(root) not in resume_view.committed_root_keys
            ]
            if uncommitted_exp1:
                raise RuntimeError(
                    "pre-Flash resume cannot dispatch a new Experiment 1 provider call"
                )

    pending_write_roots: list[RootInventoryV1] = []
    pending_roots: list[RootInventoryV1] = []
    pending_provider_roots: list[RootInventoryV1] = []
    journal_resume_roots: list[RootInventoryV1] = []
    for experiment_id in experiment_ids:
        if experiment_id not in {"exp1", "exp5"}:
            continue
        for root, is_reference in selected_by_experiment[experiment_id]:
            key = _root_key(root)
            committed = (
                _reference_committed(store, root)
                if is_reference
                else resume_view is not None
                and key in resume_view.committed_root_keys
            )
            protocol_resume = (
                resume_view is not None and key in resume_view.protocol_root_keys
            )
            dead = resume and _started_without_protocol(store, root) and not protocol_resume
            if not committed:
                pending_write_roots.append(root)
                if not dead:
                    pending_roots.append(root)
                    if not protocol_resume:
                        pending_provider_roots.append(root)
                    elif experiment_id == "exp1" and _protocol_tail_pending(store, root):
                        if _protocol_tail_requires_provider(store, root):
                            pending_provider_roots.append(root)
                        else:
                            journal_resume_roots.append(root)

    for experiment_id in experiment_ids:
        if experiment_id in {"exp1", "exp5"}:
            continue
        for root, is_reference in selected_by_experiment[experiment_id]:
            committed = _reference_committed(store, root) if is_reference else (
                resume_view is not None
                and _root_key(root) in resume_view.committed_root_keys
            )
            protocol_resume = (
                resume_view is not None
                and _root_key(root) in resume_view.protocol_root_keys
            )
            dead = resume and _started_without_protocol(store, root) and not protocol_resume
            if not committed:
                pending_write_roots.append(root)
                if not dead:
                    pending_roots.append(root)

    lean_environment_invalid_keys: set[tuple[str, str, str, int]] = set()
    pending_lean_roots = [root for root in pending_roots if root.domain == "lean"]
    if pending_lean_roots:
        from .lean_environment import LeanEnvironmentInvalid

        try:
            services.lean_environment_preflight(_REPO_ROOT)
        except LeanEnvironmentInvalid:
            lean_environment_invalid_keys = {
                _root_key(root) for root in pending_lean_roots
            }
            pending_provider_roots = [
                root
                for root in pending_provider_roots
                if _root_key(root) not in lean_environment_invalid_keys
            ]

    if pending_write_roots:
        required_margin = _required_root_write_bytes(
            pending_write_roots[0], profile_id
        )
        free_bytes = services.disk_free_bytes(run_dir.parent)
        if free_bytes < required_margin:
            raise RuntimeError(
                f"insufficient disk space for next atomic root write: "
                f"need {required_margin}, have {free_bytes}"
            )
    provider_experiments = tuple(
        item
        for item in _EXPERIMENT_ORDER
        if any(root.experiment_id == item for root in pending_provider_roots)
    )
    provider_entries = (
        dict(services.provider_preflight(provider_experiments, pending_provider_roots))
        if pending_provider_roots
        else {}
    )
    for root in journal_resume_roots:
        provider_entries.setdefault(
            str(root.provider_entry_id), _journal_resume_entry(root)
        )

    if not resume:
        _write_fresh_run_files(store, config, inventory)

    blocked_condition_origins: dict[str, str] = {}
    pending_keys = {_root_key(root) for root in pending_roots}
    for experiment_id in experiment_ids:
        selected = selected_by_experiment[experiment_id]
        fixed_roots = [
            root for root, _reference in selected if _root_key(root) in pending_keys
        ]
        current_source = (
            run_dir
            if experiment_id in _FIXED_SOURCE_EXPERIMENTS
            and tuple(experiment_ids) == _EXPERIMENT_ORDER
            else Path(source_run_dir)
            if experiment_id in _FIXED_SOURCE_EXPERIMENTS
            else None
        )
        if experiment_id in _FIXED_SOURCE_EXPERIMENTS:
            if current_source is None:
                raise RuntimeError(f"{experiment_id} requires an explicit source run dir")
            roots_by_condition: dict[str, list[RootInventoryV1]] = {}
            for root in fixed_roots:
                if _root_key(root) in lean_environment_invalid_keys:
                    continue
                roots_by_condition.setdefault(str(root.condition_id), []).append(root)
            for condition_id, condition_roots in roots_by_condition.items():
                try:
                    _source_closure(condition_roots, current_source, cases)
                except RuntimeError:
                    blocked_condition_origins[condition_id] = "source_closure_invalid"

        for root, is_reference in selected:
            key = _root_key(root)
            if is_reference:
                if resume and _reference_committed(store, root):
                    continue
            elif resume_view is not None and key in resume_view.committed_root_keys:
                continue
            required_margin = _required_root_write_bytes(root, profile_id)
            free_bytes = services.disk_free_bytes(run_dir.parent)
            if free_bytes < required_margin:
                raise RuntimeError(
                    "insufficient disk space before next root write: "
                    f"need {required_margin}, have {free_bytes}"
                )
            challenge = (
                challenges.get(str(root.challenge_plan_id))
                if root.challenge_plan_id is not None
                else None
            )
            retries, continue_after_failure, protocol_cap, provider_cap = _root_caps(
                root, profile_id
            )
            context = _CliRootContext(
                run_id=run_id,
                profile_id=profile_id,
                inventory=root,
                root_input=cases[str(root.case_id)],
                run_store=store,
                source_run_dir=current_source,
                challenge_plan=challenge,
                is_reference=is_reference,
                provider_entries=provider_entries,
                max_retries=retries,
                continue_after_terminal_child_failure=continue_after_failure,
                protocol_execution_attempt_upper=protocol_cap,
                provider_call_upper=provider_cap,
                coverage_tail_required_by_downstream=coverage_tail_required_by_downstream(
                    root, trace_consumer_case_ids
                ),
            )
            if key in lean_environment_invalid_keys:
                result = _invalid_root_result(
                    root,
                    challenge,
                    failure_kind="infrastructure_invalid",
                    failure_origin="lean_environment_invalid",
                )
            elif str(root.condition_id) in blocked_condition_origins:
                result = _invalid_root_result(
                    root,
                    challenge,
                    failure_origin=blocked_condition_origins[str(root.condition_id)],
                )
            elif resume_view is not None and key in resume_view.protocol_root_keys:
                protocol = store.read_root_protocol(*key)
                result = services.resume_root(context, protocol)
            elif (
                pre_flash_resume
                and key == PRE_FLASH_EXP2_ROOT_KEY
                and _started_without_protocol(store, root)
            ):
                from .runtime import reproject_pre_flash_exp2_terminal_root_context

                result = reproject_pre_flash_exp2_terminal_root_context(context)
            elif resume and _started_without_protocol(store, root):
                result = _invalid_root_result(
                    root,
                    challenge,
                    failure_origin="resume_state_invalid",
                )
            else:
                result = services.execute_root(context)
            _commit_result(context, result)
            if result.failure_origin in {
                "provider_configuration_invalid",
                "provider_journal_conflict",
                "provider_model_mismatch",
            }:
                blocked_condition_origins[str(root.condition_id)] = str(
                    result.failure_origin
                )

    if reduce_after:
        services.reduce_run(run_dir)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    _services: _CliServices | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "lean-environment-test":
        from .lean_environment import run_lean_environment_test

        result = run_lean_environment_test(
            repository_root=_REPO_ROOT,
            pass_path=args.pass_path,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "plan":
        plan = build_plan(
            args.profile,
            representative_raw_response_p95_bytes=(
                args.representative_raw_response_p95_bytes
            ),
        )
        print(json.dumps(_plan_payload(plan, args.run_id), ensure_ascii=False))
        return 0
    if args.command == "compare-exp5-v4-reference":
        from .reducer import reduce_exp5_with_exp1_v4_reference

        summary = reduce_exp5_with_exp1_v4_reference(
            Path(args.run_dir), Path(args.source_run_dir)
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    services = _services or _default_services()
    if args.command == "reduce":
        services.reduce_run(Path(args.run_dir))
        return 0
    if args.command == "run":
        if args.experiment in _FIXED_SOURCE_EXPERIMENTS and not args.source_run_dir:
            parser.error(f"run --experiment {args.experiment} requires --source-run-dir")
        if args.experiment not in _FIXED_SOURCE_EXPERIMENTS and args.source_run_dir:
            parser.error("--source-run-dir is only valid for exp2, exp3, or exp4")
        return run_experiment(
            experiment_ids=(args.experiment,),
            profile_id=args.profile,
            run_id=args.run_id,
            output_root=args.output_root,
            source_run_dir=args.source_run_dir,
            resume=args.resume,
            reduce_after=False,
            services=services,
        )
    if args.command == "run-all":
        return run_experiment(
            experiment_ids=_EXPERIMENT_ORDER,
            profile_id=args.profile,
            run_id=args.run_id,
            output_root=args.output_root,
            source_run_dir=None,
            resume=args.resume,
            reduce_after=True,
            services=services,
        )
    if args.command == "representative":
        return run_experiment(
            experiment_ids=_EXPERIMENT_ORDER,
            profile_id="representative",
            run_id=args.run_id,
            output_root=args.output_root,
            source_run_dir=None,
            resume=args.resume,
            reduce_after=True,
            services=services,
        )
    raise AssertionError(f"unhandled command: {args.command}")


__all__ = ["main", "run_experiment"]


if __name__ == "__main__":
    raise SystemExit(main())
