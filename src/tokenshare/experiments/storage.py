"""TokenShare Experiments 普通文件写读、resume 扫描与 trace ordinal 选择。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass, replace
import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping
from urllib.parse import parse_qsl, quote, unquote, urlencode
from uuid import uuid4

from .schema import (
    ROOT_RESULT_SCHEMA_VERSION,
    AblationObservationV1,
    AttemptResultV1,
    ChallengeObservationV1,
    FaultObservationV1,
    ProviderCallResultV1,
    RecoveryObservationV1,
    RootInventoryV1,
    RootResultV2,
    UnitTraceV1,
    WorkerDeathObservationV1,
    WorkerExecutionFactV1,
)


RootKeyV1 = tuple[str, str, str, int]
TraceKeyV1 = tuple[str, int, str]
WriteDisposition = Literal["written", "skipped"]
PROTOCOL_SNAPSHOT_SCHEMA_VERSION = "slim_v2.protocol_material.v1"
PROVIDER_TERMINAL_SCHEMA_VERSION = "slim_v2.provider_terminal.v1"
_INVENTORIES = frozenset(
    {"conditions", "roots", "exp3_references", "exp4_challenges"}
)
_ROOT_NESTED_TYPES = {
    "worker_execution_facts": WorkerExecutionFactV1,
    "attempts": AttemptResultV1,
    "fault_observations": FaultObservationV1,
    "recovery_observations": RecoveryObservationV1,
    "worker_death_observations": WorkerDeathObservationV1,
    "challenge_observations": ChallengeObservationV1,
    "ablation_observations": AblationObservationV1,
}


class StorageConflictError(RuntimeError):
    """同一普通文本主键已经绑定不同规范内容。"""


@dataclass(frozen=True, slots=True)
class ResumeView:
    """只陈述 run 目录中已经 committed 的 ordinary-file 事实。"""

    committed_root_keys: frozenset[RootKeyV1]
    protocol_root_keys: frozenset[RootKeyV1]
    trace_keys: frozenset[TraceKeyV1]
    terminal_call_keys: frozenset[str]
    nonterminal_call_keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProtocolSnapshotV1:
    """可在新进程读回的 protocol 普通投影，不持有 runtime 对象。"""

    root_key: RootKeyV1
    protocol_result: Mapping[str, Any]
    traces: tuple[UnitTraceV1, ...]
    tail_requests: Mapping[str, Mapping[str, Any]]
    protocol_projection: RootResultV2 | None


@dataclass(frozen=True, slots=True)
class SelectedTraceAttemptV1:
    attempt: AttemptResultV1
    requested_attempt_ordinal: int
    source_attempt_ordinal: int
    source_attempt_fallback_used: bool


@dataclass(frozen=True, slots=True)
class Exp4ChallengeInventoryRowV1:
    """Reducer 所需的冻结 Exp4 challenge inventory 普通映射。"""

    challenge_plan_id: str
    case_id: str
    repeat_id: int
    challenge_family: str
    target_rule: str
    attempt_rule: str

    def validate(self) -> None:
        for name in (
            "challenge_plan_id", "case_id", "challenge_family",
            "target_rule", "attempt_rule",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Exp4 challenge inventory {name} must be text")
        _natural(self.repeat_id, "Exp4 challenge inventory repeat_id")


def _natural(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a natural ordinal")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def coverage_tail_blocked(summary: Mapping[str, Any]) -> bool:
    """仅设施/条件失败阻止 Exp1 coverage tail。"""

    terminal_failure = summary.get("terminal_failure")
    return (
        summary.get("experiments_condition_failure") is not None
        or summary.get("experiments_runtime_failure") is not None
        or (
            isinstance(terminal_failure, Mapping)
            and terminal_failure.get("infrastructure_invalid") is True
        )
    )


def _validate_protocol_snapshot_traces(
    *,
    experiment_id: str,
    case_id: str,
    protocol_result: Mapping[str, Any],
    traces: Iterable[UnitTraceV1],
    tail_request_keys: Iterable[str],
    protocol_projection_present: bool,
    tail_not_required_by_downstream: bool,
) -> None:
    """校验 Exp1 snapshot 中 protocol 与已知 pre-dispatch tail 事实。"""

    summary = protocol_result.get("summary")
    observation = (
        summary.get("runtime_observation")
        if isinstance(summary, Mapping)
        else None
    )
    unscheduled = (
        observation.get("unscheduled_ai_unit_ids")
        if isinstance(observation, Mapping)
        else None
    )
    unscheduled_ids = set(unscheduled) if isinstance(unscheduled, list) else set()
    request_keys = set(tail_request_keys)
    if not request_keys.issubset(unscheduled_ids):
        raise ValueError("protocol tail request is outside unscheduled identities")
    seen: set[str] = set()
    pre_dispatch_ids: set[str] = set()
    for trace in traces:
        if not isinstance(trace, UnitTraceV1):
            raise TypeError("protocol snapshot traces must be UnitTraceV1")
        trace.validate()
        planned = trace.planned_ai_unit_id
        if (
            trace.case_id != case_id
            or trace.source_repeat_id != 0
            or planned in seen
        ):
            raise ValueError("protocol snapshot trace identity is invalid")
        seen.add(str(planned))
        if trace.trace_origin == "protocol":
            continue
        if (
            trace.trace_origin != "coverage_tail"
            or planned not in unscheduled_ids
            or planned in request_keys
            or any(
                attempt.result_kind != "pre_dispatch_failure"
                or attempt.provider_call_made is not False
                for attempt in trace.attempts
            )
        ):
            raise ValueError("protocol snapshot coverage-tail trace is invalid")
        pre_dispatch_ids.add(str(planned))
    tail_material_prepared = (
        protocol_projection_present or bool(request_keys) or bool(pre_dispatch_ids)
    )
    if tail_not_required_by_downstream and (request_keys or pre_dispatch_ids):
        raise ValueError("not-required Exp1 tail snapshot cannot carry tail material")
    if (
        experiment_id == "exp1"
        and tail_material_prepared
        and not tail_not_required_by_downstream
        and not coverage_tail_blocked(
            summary if isinstance(summary, Mapping) else {}
        )
        and (request_keys | pre_dispatch_ids) != unscheduled_ids
    ):
        raise ValueError("protocol tail material does not close unscheduled identities")


def _segment(value: str) -> str:
    return quote(_text(value, "path identity"), safe="")


def _root_directory_name(key: RootKeyV1) -> str:
    experiment_id, condition_id, case_id, repeat_id = key
    return urlencode(
        (
            ("experiment", _text(experiment_id, "experiment_id")),
            ("condition", _text(condition_id, "condition_id")),
            ("case", _text(case_id, "case_id")),
            ("repeat", str(_natural(repeat_id, "repeat_id"))),
        ),
        quote_via=quote,
        safe="",
    )


def _root_key_from_directory(path: Path) -> RootKeyV1:
    values = dict(parse_qsl(path.name, keep_blank_values=True))
    if set(values) != {"experiment", "condition", "case", "repeat"}:
        raise ValueError(f"invalid ordinary root key path: {path}")
    try:
        repeat_id = int(values["repeat"])
    except ValueError as exc:
        raise ValueError(f"invalid ordinary root repeat path: {path}") from exc
    key = (
        _text(values["experiment"], "experiment_id"),
        _text(values["condition"], "condition_id"),
        _text(values["case"], "case_id"),
        _natural(repeat_id, "repeat_id"),
    )
    if unquote(path.parent.name) != key[0]:
        raise ValueError("root key experiment differs from its parent directory")
    return key


def _jsonable(value: Any) -> Any:
    return asdict(value) if is_dataclass(value) and not isinstance(value, type) else value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("ordinary file content must be JSON serializable") from exc


def _normalize_json(text: str) -> str:
    return _canonical_json(json.loads(text))


def _normalize_jsonl(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "".join(f"{_canonical_json(json.loads(line))}\n" for line in lines)


def _atomic_text(
    path: Path,
    canonical: str,
    normalizer: Any,
) -> WriteDisposition:
    if path.exists():
        try:
            existing = normalizer(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise StorageConflictError(f"invalid existing ordinary file: {path}") from exc
        if existing == canonical:
            return "skipped"
        raise StorageConflictError(f"conflicting ordinary file for key: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    remove_temporary = False
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical)
            stream.flush()
        # roots 串行；此处只防止正常流程在写 temp 期间已出现目标。
        if path.exists():
            if normalizer(path.read_text(encoding="utf-8")) == canonical:
                remove_temporary = True
                return "skipped"
            raise StorageConflictError(f"conflicting ordinary file for key: {path}")
        temporary.replace(path)
        remove_temporary = True
        return "written"
    finally:
        # 异常 temp 留作人工检查；resume 的精确文件名扫描会忽略它。
        if remove_temporary and temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> WriteDisposition:
    return _atomic_text(path, f"{_canonical_json(value)}\n", lambda text: f"{_normalize_json(text)}\n")


def _exact_values(record_type: type[Any], value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {item.name for item in fields(record_type)}
    if set(value) != expected:
        raise ValueError(f"invalid {record_type.__name__} ordinary fields")
    return dict(value)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"ordinary JSON must be an object: {path}")
    return value


def _root_result_from_document(document: Mapping[str, Any]) -> RootResultV2:
    values = dict(document)
    if values.pop("schema_version", None) != ROOT_RESULT_SCHEMA_VERSION:
        raise ValueError("invalid RootResultV2 ordinary schema_version")
    for name, record_type in _ROOT_NESTED_TYPES.items():
        nested = values.get(name)
        if not isinstance(nested, list):
            raise ValueError(f"RootResultV2 {name} must be an array")
        values[name] = [
            record_type(**_exact_values(record_type, item))
            for item in nested
            if isinstance(item, Mapping)
        ]
        if len(values[name]) != len(nested):
            raise ValueError(f"RootResultV2 {name} entries must be objects")
    result = RootResultV2(**_exact_values(RootResultV2, values))
    result.validate()
    return result


def _trace_from_document(document: Mapping[str, Any]) -> UnitTraceV1:
    values = dict(document)
    attempts = values.get("attempts")
    if not isinstance(attempts, list) or not all(isinstance(item, Mapping) for item in attempts):
        raise ValueError("UnitTraceV1 attempts must be an array of objects")
    values["attempts"] = [
        AttemptResultV1(**_exact_values(AttemptResultV1, item)) for item in attempts
    ]
    trace = UnitTraceV1(**_exact_values(UnitTraceV1, values))
    trace.validate()
    return trace


def _provider_result_from_document(
    document: Mapping[str, Any],
) -> ProviderCallResultV1:
    result = ProviderCallResultV1(
        **_exact_values(ProviderCallResultV1, document)
    )
    result.validate()
    return result


class RunStore:
    """一个 run 的 ordinary files；不判断任何协议状态。"""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    def root_directory(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> Path:
        root_key = (experiment_id, condition_id, case_id, repeat_id)
        return self.run_dir / "roots" / _segment(root_key[0]) / _root_directory_name(root_key)

    def system_root_directory(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> Path:
        root_key = (experiment_id, condition_id, case_id, repeat_id)
        return (
            self.run_dir
            / "system"
            / _segment(root_key[0])
            / _root_directory_name(root_key)
        )

    def root_result_path(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> Path:
        return self.root_directory(experiment_id, condition_id, case_id, repeat_id) / "result.json"

    def root_protocol_path(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> Path:
        return self.root_directory(experiment_id, condition_id, case_id, repeat_id) / "protocol.json"

    def run_config_path(self) -> Path:
        return self.run_dir / "run.json"

    def exp3_reference_result_path(
        self,
        condition_id: str,
        case_id: str,
        repeat_id: int,
    ) -> Path:
        """返回辅助 Exp3 reference 的独立普通结果路径。"""

        key = ("exp3", condition_id, case_id, repeat_id)
        return self.run_dir / "references" / "exp3" / _root_directory_name(key) / "result.json"

    def trace_path(self, case_id: str, source_repeat_id: int, unit_id: str) -> Path:
        return (
            self.run_dir / "traces" / "exp1" / _segment(case_id)
            / str(_natural(source_repeat_id, "source_repeat_id"))
            / f"{_segment(unit_id)}.json"
        )

    def call_intent_path(self, call_key: str) -> Path:
        return self.run_dir / "calls" / f"{_segment(call_key)}.intent.json"

    def call_terminal_path(self, call_key: str) -> Path:
        return self.run_dir / "calls" / f"{_segment(call_key)}.terminal.json"

    def response_path(self, call_key: str) -> Path:
        return self.run_dir / "responses" / f"{_segment(call_key)}.json"

    def inventory_path(self, name: str) -> Path:
        if name not in _INVENTORIES:
            raise ValueError(f"unknown experiments inventory: {name}")
        return self.run_dir / "inventory" / f"{name}.jsonl"

    def write_root_result(self, result: RootResultV2) -> WriteDisposition:
        result.validate()
        key = (result.experiment_id, result.condition_id, result.case_id, result.repeat_id)
        document = asdict(result)
        document["schema_version"] = ROOT_RESULT_SCHEMA_VERSION
        return _write_json(self.root_result_path(*key), document)

    def read_root_result(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> RootResultV2:
        key = (experiment_id, condition_id, case_id, repeat_id)
        result = _root_result_from_document(_read_object(self.root_result_path(*key)))
        if (result.experiment_id, result.condition_id, result.case_id, result.repeat_id) != key:
            raise ValueError("RootResultV2 identity differs from its ordinary path")
        return result

    def write_root_protocol(
        self,
        experiment_id: str,
        condition_id: str,
        case_id: str,
        repeat_id: int,
        protocol: Any,
    ) -> WriteDisposition:
        return _write_json(
            self.root_protocol_path(experiment_id, condition_id, case_id, repeat_id),
            protocol,
        )

    def read_root_protocol(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> dict[str, Any]:
        return _read_object(
            self.root_protocol_path(experiment_id, condition_id, case_id, repeat_id)
        )

    def write_root_protocol_snapshot(
        self,
        experiment_id: str,
        condition_id: str,
        case_id: str,
        repeat_id: int,
        *,
        protocol_result: Mapping[str, Any] | Any,
        traces: Iterable[UnitTraceV1],
        tail_requests: Mapping[str, Mapping[str, Any]] | None = None,
        protocol_projection: RootResultV2 | None = None,
    ) -> WriteDisposition:
        """冻结 protocol traces 与不可 dispatch 的 typed tail facts。"""

        key = (experiment_id, condition_id, case_id, repeat_id)
        trace_rows = tuple(traces)
        requests = dict(tail_requests or {})
        if experiment_id != "exp1" and (trace_rows or requests):
            raise ValueError("non-Exp1 protocol snapshot cannot contain tail material")
        if experiment_id != "exp1" and not isinstance(
            protocol_projection, RootResultV2
        ):
            raise TypeError("non-Exp1 protocol snapshot requires a typed projection")
        protocol_body = _jsonable(protocol_result)
        if not isinstance(protocol_body, Mapping):
            raise TypeError("protocol_result must be an object")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, Mapping)
            for key, value in requests.items()
        ):
            raise ValueError("protocol tail requests must be keyed objects")
        if protocol_projection is not None:
            protocol_projection.validate()
            projection_key = (
                protocol_projection.experiment_id,
                protocol_projection.condition_id,
                protocol_projection.case_id,
                protocol_projection.repeat_id,
            )
            if projection_key != key:
                raise ValueError("protocol projection identity differs from root")
        tail_not_required_by_downstream = bool(
            protocol_projection is not None
            and protocol_projection.experiment_id == "exp1"
            and protocol_projection.trace_tail_status == "not_required_by_downstream"
        )
        _validate_protocol_snapshot_traces(
            experiment_id=experiment_id,
            case_id=case_id,
            protocol_result=protocol_body,
            traces=trace_rows,
            tail_request_keys=requests,
            protocol_projection_present=protocol_projection is not None,
            tail_not_required_by_downstream=tail_not_required_by_downstream,
        )
        projection_body = None
        if protocol_projection is not None:
            projection_body = asdict(protocol_projection)
            projection_body["schema_version"] = ROOT_RESULT_SCHEMA_VERSION
        return self.write_root_protocol(
            *key,
            {
                "schema_version": PROTOCOL_SNAPSHOT_SCHEMA_VERSION,
                "root_key": list(key),
                "protocol_result": dict(protocol_body),
                "traces": [asdict(trace) for trace in trace_rows],
                "tail_requests": requests,
                "protocol_projection": projection_body,
            },
        )

    def read_root_protocol_snapshot(
        self, experiment_id: str, condition_id: str, case_id: str, repeat_id: int,
    ) -> ProtocolSnapshotV1:
        key = (experiment_id, condition_id, case_id, repeat_id)
        document = self.read_root_protocol(*key)
        if set(document) != {
            "schema_version", "root_key", "protocol_result", "traces",
            "tail_requests", "protocol_projection",
        }:
            raise ValueError("invalid protocol snapshot ordinary fields")
        if document["schema_version"] != PROTOCOL_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("invalid protocol snapshot schema_version")
        raw_key = document["root_key"]
        if not isinstance(raw_key, list) or tuple(raw_key) != key:
            raise ValueError("protocol snapshot identity differs from its ordinary path")
        protocol_result = document["protocol_result"]
        raw_traces = document["traces"]
        raw_requests = document["tail_requests"]
        raw_projection = document["protocol_projection"]
        if not isinstance(protocol_result, Mapping):
            raise ValueError("protocol snapshot result must be an object")
        if not isinstance(raw_traces, list) or not all(
            isinstance(item, Mapping) for item in raw_traces
        ):
            raise ValueError("protocol snapshot traces must be objects")
        if not isinstance(raw_requests, Mapping) or any(
            not isinstance(name, str)
            or not name
            or not isinstance(value, Mapping)
            for name, value in raw_requests.items()
        ):
            raise ValueError("protocol snapshot tail requests must be keyed objects")
        if raw_projection is not None and not isinstance(raw_projection, Mapping):
            raise ValueError("protocol snapshot projection must be an object or null")
        traces = tuple(_trace_from_document(item) for item in raw_traces)
        projection = (
            _root_result_from_document(raw_projection)
            if isinstance(raw_projection, Mapping)
            else None
        )
        _validate_protocol_snapshot_traces(
            experiment_id=experiment_id,
            case_id=case_id,
            protocol_result=protocol_result,
            traces=traces,
            tail_request_keys=raw_requests,
            protocol_projection_present=raw_projection is not None,
            tail_not_required_by_downstream=bool(
                projection is not None
                and projection.experiment_id == "exp1"
                and projection.trace_tail_status == "not_required_by_downstream"
            ),
        )
        if projection is not None and (
            projection.experiment_id,
            projection.condition_id,
            projection.case_id,
            projection.repeat_id,
        ) != key:
            raise ValueError("protocol snapshot projection identity differs")
        if experiment_id != "exp1" and (traces or raw_requests):
            raise ValueError("non-Exp1 protocol snapshot contains tail material")
        if experiment_id != "exp1" and projection is None:
            raise ValueError("non-Exp1 protocol snapshot lacks a typed projection")
        return ProtocolSnapshotV1(
            key,
            dict(protocol_result),
            traces,
            {str(name): dict(value) for name, value in raw_requests.items()},
            projection,
        )

    def write_trace(self, trace: UnitTraceV1) -> WriteDisposition:
        trace.validate()
        return _write_json(
            self.trace_path(trace.case_id, trace.source_repeat_id, trace.planned_ai_unit_id),
            trace,
        )

    def read_trace(self, case_id: str, repeat_id: int, unit_id: str) -> UnitTraceV1:
        trace = _trace_from_document(_read_object(self.trace_path(case_id, repeat_id, unit_id)))
        if (trace.case_id, trace.source_repeat_id, trace.planned_ai_unit_id) != (case_id, repeat_id, unit_id):
            raise ValueError("UnitTraceV1 identity differs from its ordinary path")
        return trace

    def write_call_intent(self, call_key: str, value: Any) -> WriteDisposition:
        return _write_json(self.call_intent_path(call_key), value)

    def write_call_terminal(self, call_key: str, value: Any) -> WriteDisposition:
        return _write_json(self.call_terminal_path(call_key), value)

    def read_call_intent(self, call_key: str) -> dict[str, Any]:
        value = _read_object(self.call_intent_path(call_key))
        if value.get("call_key") != call_key:
            raise ValueError("provider intent identity differs from its ordinary path")
        return value

    def read_call_terminal(self, call_key: str) -> dict[str, Any]:
        value = _read_object(self.call_terminal_path(call_key))
        if value.get("call_key") != call_key:
            raise ValueError("provider terminal identity differs from its ordinary path")
        return value

    def read_provider_terminal_result(
        self, call_key: str, *, context: Any,
    ) -> ProviderCallResultV1:
        """从冻结 terminal 投影与 responses/ raw 重建一次 caller 结果。"""

        document = _read_object(self.call_terminal_path(call_key))
        if set(document) != {
            "schema_version", "call_key", "root_key",
            "planned_ai_unit_id", "attempt_ordinal", "ok", "error_kind",
            "provider_call_made", "http_status", "resolved_model",
            "usage_status", "response_relative_path", "result",
        }:
            raise ValueError("provider terminal lacks a stable typed projection")
        expected = (
            PROVIDER_TERMINAL_SCHEMA_VERSION,
            call_key,
            list(getattr(context, "root_key", ())),
            getattr(context, "planned_ai_unit_id", None),
            getattr(context, "attempt_ordinal", None),
        )
        actual = (
            document["schema_version"],
            document["call_key"],
            document["root_key"],
            document["planned_ai_unit_id"],
            document["attempt_ordinal"],
        )
        if actual != expected:
            raise ValueError("provider terminal identity differs from request context")
        result = document["result"]
        if not isinstance(result, Mapping):
            raise ValueError("provider terminal result must be an object")
        projected = _provider_result_from_document(result)
        if (
            document["ok"] != projected.ok
            or document["error_kind"] != projected.error_kind
            or document["http_status"] != projected.http_status
            or document["resolved_model"] != projected.resolved_model
            or document["usage_status"] != projected.usage_status
        ):
            raise ValueError("provider terminal summary differs from typed result")
        response_relative_path = document["response_relative_path"]
        if response_relative_path is None:
            return projected
        if not isinstance(response_relative_path, str):
            raise ValueError("provider terminal response path must be text or null")
        response = self.read_relative_response(response_relative_path)
        hydrated = replace(projected, raw_response_json=response.get("body"))
        hydrated.validate()
        return hydrated

    def write_response(self, call_key: str, value: Any) -> WriteDisposition:
        return _write_json(self.response_path(call_key), value)

    def read_response(self, call_key: str) -> dict[str, Any]:
        return _read_object(self.response_path(call_key))

    def read_relative_response(self, relative_path: str) -> dict[str, Any]:
        path = self.run_dir / Path(relative_path)
        if path.parent != self.run_dir / "responses" or path.suffix != ".json":
            raise ValueError("response relative path must identify an experiment response file")
        return _read_object(path)

    def write_run_config(self, value: Mapping[str, Any]) -> WriteDisposition:
        return _write_json(self.run_config_path(), dict(value))

    def read_run_config(self) -> dict[str, Any]:
        return _read_object(self.run_config_path())

    def write_exp3_reference_result(
        self, result: RootResultV2,
    ) -> WriteDisposition:
        result.validate()
        if result.experiment_id != "exp3":
            raise ValueError("Exp3 reference writer requires an Exp3 result")
        document = asdict(result)
        document["schema_version"] = ROOT_RESULT_SCHEMA_VERSION
        return _write_json(
            self.exp3_reference_result_path(
                result.condition_id, result.case_id, result.repeat_id
            ),
            document,
        )

    def _write_inventory(self, name: str, rows: Iterable[Any]) -> WriteDisposition:
        canonical = "".join(f"{_canonical_json(row)}\n" for row in rows)
        return _atomic_text(self.inventory_path(name), canonical, _normalize_jsonl)

    def write_frozen_inventories(
        self,
        *,
        conditions: Iterable[Any],
        roots: Iterable[RootInventoryV1],
        exp3_references: Iterable[RootInventoryV1],
        exp4_challenges: Iterable[Any],
    ) -> dict[str, WriteDisposition]:
        """原子写四个冻结 inventory；任一主键冲突立即停止。"""

        rows = {
            "conditions": conditions,
            "roots": roots,
            "exp3_references": exp3_references,
            "exp4_challenges": exp4_challenges,
        }
        return {
            name: self._write_inventory(name, values) for name, values in rows.items()
        }

    def read_root_inventory_rows(
        self,
        name: Literal["roots", "exp3_references"],
    ) -> tuple[RootInventoryV1, ...]:
        """读回经 schema validate 的 root/reference inventory rows。"""

        if name not in {"roots", "exp3_references"}:
            raise ValueError(f"not a root inventory: {name}")
        return tuple(self.iter_root_inventory_rows(name))

    def iter_root_inventory_rows(
        self,
        name: Literal["roots", "exp3_references"] = "roots",
    ) -> Iterator[RootInventoryV1]:
        """逐行验证并产出 root/reference inventory，不一次载入全文件。"""

        if name not in {"roots", "exp3_references"}:
            raise ValueError(f"not a root inventory: {name}")
        with self.inventory_path(name).open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError(
                        f"{name}.jsonl line {line_number} must be an object"
                    )
                row = RootInventoryV1(**_exact_values(RootInventoryV1, value))
                row.validate()
                yield row

    def iter_inventory_results(
        self,
        name: Literal["roots", "exp3_references"] = "roots",
        *,
        experiment_id: str | None = None,
    ) -> Iterator[tuple[RootInventoryV1, RootResultV2 | None]]:
        """流式 join 冻结 inventory 与同主键 committed result。

        缺少 committed result 时仍产出 inventory identity 和 ``None``；这使
        reducer 能维持固定分母，而不会扫描 raw/system/artifact 目录。
        """

        for inventory in self.iter_root_inventory_rows(name):
            if experiment_id is not None and inventory.experiment_id != experiment_id:
                continue
            key = (
                str(inventory.experiment_id),
                str(inventory.condition_id),
                str(inventory.case_id),
                int(inventory.repeat_id),
            )
            path = (
                self.root_result_path(*key)
                if name == "roots"
                else self.exp3_reference_result_path(key[1], key[2], key[3])
            )
            if not path.is_file():
                yield inventory, None
                continue
            result = _root_result_from_document(_read_object(path))
            result_key = (
                result.experiment_id,
                result.condition_id,
                result.case_id,
                result.repeat_id,
            )
            if result_key != key:
                raise ValueError("RootResultV2 identity differs from its ordinary path")
            yield inventory, result

    def iter_exp4_challenge_rows(self) -> Iterator[Exp4ChallengeInventoryRowV1]:
        """逐行产出冻结 challenge plan；不导入 profile 或 runtime。"""

        name = "exp4_challenges"
        with self.inventory_path(name).open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError(
                        f"{name}.jsonl line {line_number} must be an object"
                    )
                row = Exp4ChallengeInventoryRowV1(
                    **_exact_values(Exp4ChallengeInventoryRowV1, value)
                )
                row.validate()
                yield row


def select_trace_attempt(
    trace: UnitTraceV1,
    requested_ordinal: int,
) -> SelectedTraceAttemptV1:
    """精确命中 requested ordinal，否则选择同 trace 最大自然 ordinal。"""

    if not isinstance(trace, UnitTraceV1):
        raise TypeError("select_trace_attempt requires a UnitTraceV1")
    trace.validate()
    requested = _natural(requested_ordinal, "requested_attempt_ordinal")
    indexed = {
        _natural(attempt.attempt_ordinal, "attempt_ordinal"): attempt
        for attempt in trace.attempts
    }
    selected = requested if requested in indexed else max(indexed)
    return SelectedTraceAttemptV1(
        indexed[selected],
        requested,
        selected,
        selected != requested,
    )


def scan_resume(run_dir: str | Path) -> ResumeView:
    """仅扫描精确 committed 文件名；temporary files 不是事实。"""

    base = Path(run_dir)
    roots = base / "roots"
    committed = {_root_key_from_directory(path.parent) for path in roots.glob("*/*/result.json") if path.is_file()}
    protocols = {_root_key_from_directory(path.parent) for path in roots.glob("*/*/protocol.json") if path.is_file()}
    trace_keys: set[TraceKeyV1] = set()
    trace_root = base / "traces" / "exp1"
    for path in trace_root.glob("*/*/*.json"):
        if path.is_file():
            case, repeat, filename = path.relative_to(trace_root).parts
            trace_keys.add((unquote(case), _natural(int(repeat), "source_repeat_id"), unquote(filename[:-5])))
    calls = base / "calls"
    terminals = {unquote(path.name[:-14]) for path in calls.glob("*.terminal.json") if path.is_file()}
    intents = {unquote(path.name[:-12]) for path in calls.glob("*.intent.json") if path.is_file()}
    return ResumeView(
        frozenset(committed),
        frozenset(protocols),
        frozenset(trace_keys),
        frozenset(terminals),
        frozenset(intents - terminals),
    )


__all__ = [
    "ProtocolSnapshotV1", "ResumeView", "RunStore", "SelectedTraceAttemptV1",
    "StorageConflictError",
    "coverage_tail_blocked", "scan_resume", "select_trace_attempt",
]
