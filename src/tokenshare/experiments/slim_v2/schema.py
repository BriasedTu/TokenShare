"""Slim V2 普通数据对象与字段级验证。

本模块只冻结 JSON 可序列化的数据合同，不装配 runtime、catalog、provider 或
reducer。字段上的 ``nullable`` / ``contract`` metadata 是 schema 事实来源；测试
再与独立 authority fixture 做精确集合比较。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field, fields, is_dataclass
from datetime import datetime, timedelta
from types import UnionType
from typing import Any, ClassVar, Mapping, Union, get_args, get_origin, get_type_hints

from . import SLIM_V2_SCHEMA_VERSION


RUN_CONFIG_SCHEMA_VERSION = "tokenshare.slim_v2.run_config.v1"
ROOT_INVENTORY_SCHEMA_VERSION = "tokenshare.slim_v2.root_inventory.v1"
UNIT_TRACE_SCHEMA_VERSION = "tokenshare.slim_v2.unit_trace.v1"
ROOT_RESULT_SCHEMA_VERSION = "tokenshare.slim_v2.root_result.v2"
# 前向 Experiment 1 与 Experiment 5 使用不同且不可变的价格版本。
PRICING_VERSION = "slim_v2.pricing.2026-08-23"
LEGACY_PRICING_VERSION = "slim_v2.pricing.2026-08-20"
PRICING_VERSIONS = {
    "exp1": PRICING_VERSION,
    "exp5": LEGACY_PRICING_VERSION,
}
# 该事实 cohort 只允许在 2026-08-23 前向切换后的窄恢复路径中读取。
PRE_FLASH_REPRESENTATIVE_RUN_ID = (
    "slim-v2-representative-real-20260822-144900-ef9128fe"
)
PRE_FLASH_EXP2_ROOT_KEY = (
    "exp2",
    "exp2|worker=1|repeat=0|position=late",
    "factor_v2_hard_138",
    0,
)
PRE_FLASH_PROVIDER_ENTRY_ID = "deepseek_v4_pro_exp1_baseline"
PRE_FLASH_CONFIGURED_MODEL = "deepseek-v4-pro"

_OPERATIONAL = "operational"
_CALL_STATES = frozenset({"not_started", "in_flight", "terminal"})
_TRACE_ORIGINS = frozenset({"protocol", "coverage_tail"})
_PROFILES = frozenset({"representative", "full"})
_EXPERIMENT_ORDER = ("exp1", "exp2", "exp3", "exp4", "exp5")
_EXPERIMENTS = frozenset(_EXPERIMENT_ORDER)
_EXP1_PROVIDER_CONFIG_PATH = (
    "benchmarks/paper/exp1_baseline_provider_config.v3.json"
)
_EXP5_PROVIDER_CONFIG_PATH = (
    "benchmarks/paper/exp5_siliconflow_provider_config.v3.json"
)
_LOCAL_SECRET_CONFIG_PATH = "local/ai_api_smoke.local.json"
_RESPONSE_MAX_BYTES = 16 * 1024 * 1024


class SchemaValidationError(ValueError):
    """普通 schema 数据不满足冻结合同时抛出。"""


def _schema_field(
    *,
    nullable: bool,
    default: Any = None,
    default_factory: Any | None = None,
    operational: bool = False,
):
    metadata = {
        "nullable": nullable,
        "contract": _OPERATIONAL if operational else "authority",
    }
    if default_factory is not None:
        return dataclass_field(default_factory=default_factory, metadata=metadata)
    return dataclass_field(default=default, metadata=metadata)


def _required(default: Any = None, *, operational: bool = False):
    return _schema_field(nullable=False, default=default, operational=operational)


def _required_factory(factory: Any, *, operational: bool = False):
    return _schema_field(
        nullable=False,
        default_factory=factory,
        operational=operational,
    )


def _nullable(*, operational: bool = False):
    return _schema_field(nullable=True, operational=operational)


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        non_none = [item for item in get_args(annotation) if item is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _nested_record_type(annotation: Any) -> tuple[type[Any] | None, bool]:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin is list:
        item_type = _unwrap_optional(get_args(annotation)[0])
        if isinstance(item_type, type) and is_dataclass(item_type):
            return item_type, True
        return None, False
    if isinstance(annotation, type) and is_dataclass(annotation):
        return annotation, False
    return None, False


def _leaf_contract(
    record_type: type[Any],
    *,
    authority_only: bool,
    prefix: str = "",
) -> list[tuple[str, bool]]:
    hints = get_type_hints(record_type)
    contract: list[tuple[str, bool]] = []
    for item in fields(record_type):
        if authority_only and item.metadata.get("contract") == _OPERATIONAL:
            continue
        nested_type, repeated = _nested_record_type(hints[item.name])
        if nested_type is not None:
            nested_prefix = f"{prefix}{item.name}{'[]' if repeated else ''}."
            contract.extend(
                _leaf_contract(
                    nested_type,
                    authority_only=authority_only,
                    prefix=nested_prefix,
                )
            )
            continue
        contract.append((f"{prefix}{item.name}", bool(item.metadata["nullable"])))
    return contract


class SchemaRecordV1:
    """为 dataclass schema 暴露稳定的叶路径和规则投影。"""

    @classmethod
    def normalized_leaf_paths(cls, *, authority_only: bool = False) -> tuple[str, ...]:
        return tuple(
            path
            for path, _nullable_value in _leaf_contract(
                cls,
                authority_only=authority_only,
            )
        )

    @classmethod
    def required_leaf_paths(cls) -> tuple[str, ...]:
        return tuple(
            path
            for path, nullable in _leaf_contract(cls, authority_only=False)
            if not nullable
        )

    @classmethod
    def nullable_leaf_paths(cls) -> tuple[str, ...]:
        return tuple(
            path
            for path, nullable in _leaf_contract(cls, authority_only=False)
            if nullable
        )

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls))


def _valid_reason(reasons: Mapping[str, str], field_path: str) -> bool:
    reason = reasons.get(field_path)
    return isinstance(reason, str) and bool(reason.strip())


def require_null_reason(
    field_path: str,
    value: Any,
    missing_reason: Mapping[str, str],
    not_applicable_reason: Mapping[str, str],
) -> None:
    """要求每个 ``null`` 都有缺失或不适用原因。"""

    if value is not None:
        return
    if _valid_reason(missing_reason, field_path):
        return
    if _valid_reason(not_applicable_reason, field_path):
        return
    raise SchemaValidationError(
        f"nullable field {field_path!r} requires missing_reason or "
        "not_applicable_reason"
    )


def _expanded_reasons(
    record: Any,
    *,
    prefix: str,
    inherited: Mapping[str, str],
) -> dict[str, str]:
    expanded = dict(inherited)
    local = getattr(record, "missing_reason", None)
    if not isinstance(local, Mapping):
        return expanded
    for key, value in local.items():
        if not isinstance(key, str):
            continue
        normalized = key if "." in key or "[]" in key else f"{prefix}{key}"
        expanded[normalized] = value
    return expanded


def _validate_record_values(
    record: Any,
    *,
    prefix: str,
    missing_reason: Mapping[str, str],
    not_applicable_reason: Mapping[str, str],
) -> None:
    hints = get_type_hints(type(record))
    local_missing = _expanded_reasons(
        record,
        prefix=prefix,
        inherited=missing_reason,
    )
    for item in fields(record):
        value = getattr(record, item.name)
        nested_type, repeated = _nested_record_type(hints[item.name])
        path = f"{prefix}{item.name}"
        if nested_type is not None:
            if value is None:
                if item.metadata["nullable"]:
                    require_null_reason(path, value, local_missing, not_applicable_reason)
                    continue
                raise SchemaValidationError(f"required field {path!r} is null")
            children = value if repeated else [value]
            child_prefix = f"{path}{'[]' if repeated else ''}."
            for child in children:
                _validate_record_values(
                    child,
                    prefix=child_prefix,
                    missing_reason=local_missing,
                    not_applicable_reason=not_applicable_reason,
                )
            continue
        if value is None:
            if item.metadata["nullable"]:
                require_null_reason(path, value, local_missing, not_applicable_reason)
            else:
                raise SchemaValidationError(f"required field {path!r} is null")


def _require_nonempty_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field_name} must be non-empty text")


def _require_nonnegative_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError(f"{field_name} must be a non-negative integer")


def _require_bool(value: Any, field_name: str) -> None:
    if not isinstance(value, bool):
        raise SchemaValidationError(f"{field_name} must be boolean")


def _validate_optional_bool(value: bool | None, field_name: str) -> None:
    if value is not None:
        _require_bool(value, field_name)


def _require_unit_ratio(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError(f"{field_name} must be a numeric ratio")
    if not 0 <= value <= 1:
        raise SchemaValidationError(f"{field_name} must be between 0 and 1")


def _validate_optional_ms(value: int | None, field_name: str) -> None:
    if value is not None:
        _require_nonnegative_int(value, field_name)


def _validate_rfc3339(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or "T" not in value:
        raise SchemaValidationError(
            f"{field_name} must be a complete RFC3339 datetime"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{field_name} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaValidationError(
            f"{field_name} must be an RFC3339 datetime with timezone"
        )
    if parsed.utcoffset() != timedelta(0):
        raise SchemaValidationError(f"{field_name} must be UTC")


@dataclass(slots=True)
class WorkerExecutionFactV1(SchemaRecordV1):
    worker_id: str | None = _required()
    started_at_ms: int | None = _required()
    ended_at_ms: int | None = _required()
    result_kind: str | None = _required()

    def validate(self) -> None:
        _require_nonempty_text(self.worker_id, "worker_id")
        _require_nonnegative_int(self.started_at_ms, "started_at_ms")
        _require_nonnegative_int(self.ended_at_ms, "ended_at_ms")
        if self.ended_at_ms < self.started_at_ms:
            raise SchemaValidationError("worker ended_at_ms precedes started_at_ms")
        _require_nonempty_text(self.result_kind, "result_kind")


@dataclass(slots=True)
class FaultObservationV1(SchemaRecordV1):
    attempt_id: str | None = _required()
    fault_type: str | None = _required()
    target_planned_ai_unit_id: str | None = _required()
    injected: bool | None = _required()
    reached_verification: bool | None = _nullable()
    independently_wrong: bool | None = _nullable()
    verifier_intercepted: bool | None = _nullable()
    escaped_to_canonical_or_root: bool | None = _nullable()
    discarded_total_tokens: int | None = _nullable()

    def validate(self) -> None:
        _require_nonempty_text(self.attempt_id, "fault observation attempt_id")
        _require_nonempty_text(self.fault_type, "fault observation fault_type")
        _require_nonempty_text(
            self.target_planned_ai_unit_id,
            "fault observation target_planned_ai_unit_id",
        )
        _require_bool(self.injected, "fault observation injected")
        for name in (
            "reached_verification",
            "independently_wrong",
            "verifier_intercepted",
            "escaped_to_canonical_or_root",
        ):
            _validate_optional_bool(getattr(self, name), f"fault observation {name}")
        if self.discarded_total_tokens is not None:
            _require_nonnegative_int(
                self.discarded_total_tokens,
                "fault observation discarded_total_tokens",
            )


@dataclass(slots=True)
class RecoveryObservationV1(SchemaRecordV1):
    original_attempt_id: str | None = _required()
    replacement_attempt_id: str | None = _required()
    replacement_started: bool | None = _required()
    replacement_succeeded: bool | None = _required()
    reassigned: bool | None = _required()
    fault_at_ms: int | None = _nullable()
    replacement_started_at_ms: int | None = _nullable()
    replacement_ended_at_ms: int | None = _nullable()

    def validate(self) -> None:
        _require_nonempty_text(
            self.original_attempt_id,
            "recovery observation original_attempt_id",
        )
        _require_nonempty_text(
            self.replacement_attempt_id,
            "recovery observation replacement_attempt_id",
        )
        for name in (
            "replacement_started",
            "replacement_succeeded",
            "reassigned",
        ):
            _require_bool(getattr(self, name), f"recovery observation {name}")
        for name in (
            "fault_at_ms",
            "replacement_started_at_ms",
            "replacement_ended_at_ms",
        ):
            _validate_optional_ms(getattr(self, name), f"recovery observation {name}")
        if (
            self.replacement_started_at_ms is not None
            and self.replacement_ended_at_ms is not None
            and self.replacement_ended_at_ms < self.replacement_started_at_ms
        ):
            raise SchemaValidationError(
                "recovery replacement ended_at_ms precedes started_at_ms"
            )


@dataclass(slots=True)
class WorkerDeathObservationV1(SchemaRecordV1):
    worker_id: str | None = _required()
    pid: int | None = _nullable()
    exit_code: int | None = _nullable()
    target_progress_ratio: float | None = _required()
    actual_progress_ratio: float | None = _required()
    original_attempt_id: str | None = _nullable()
    replacement_attempt_id: str | None = _nullable()

    def validate(self) -> None:
        _require_nonempty_text(self.worker_id, "worker death observation worker_id")
        for name in ("pid", "exit_code"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise SchemaValidationError(
                    f"worker death observation {name} must be an integer"
                )
        _require_unit_ratio(
            self.target_progress_ratio,
            "worker death observation target_progress_ratio",
        )
        _require_unit_ratio(
            self.actual_progress_ratio,
            "worker death observation actual_progress_ratio",
        )
        for name in ("original_attempt_id", "replacement_attempt_id"):
            value = getattr(self, name)
            if value is not None:
                _require_nonempty_text(value, f"worker death observation {name}")


@dataclass(slots=True)
class ChallengeObservationV1(SchemaRecordV1):
    challenge_plan_id: str | None = _required()
    challenge_family: str | None = _required()
    target_planned_ai_unit_id: str | None = _required()
    attempt_ordinal: int | None = _required()
    injection_boundary: str | None = _required()
    opportunity: bool | None = _required()
    injected: bool | None = _required()
    source_semantics_preserved: bool | None = _nullable()
    candidate_independent_label: str | None = _nullable()
    reached_verification: bool | None = _nullable()
    verifier_rejected: bool | None = _nullable()
    escaped_to_canonical_or_root: bool | None = _nullable()
    replacement_started: bool | None = _nullable()
    replacement_succeeded: bool | None = _nullable()
    valid_final_after_challenge: bool | None = _nullable()

    def validate(self) -> None:
        for name in (
            "challenge_plan_id",
            "challenge_family",
            "target_planned_ai_unit_id",
            "injection_boundary",
        ):
            _require_nonempty_text(
                getattr(self, name),
                f"challenge observation {name}",
            )
        _require_nonnegative_int(
            self.attempt_ordinal,
            "challenge observation attempt_ordinal",
        )
        for name in (
            "opportunity",
            "injected",
            "source_semantics_preserved",
            "reached_verification",
            "verifier_rejected",
            "escaped_to_canonical_or_root",
            "replacement_started",
            "replacement_succeeded",
            "valid_final_after_challenge",
        ):
            value = getattr(self, name)
            if name in {"opportunity", "injected"}:
                _require_bool(value, f"challenge observation {name}")
            else:
                _validate_optional_bool(value, f"challenge observation {name}")
        if self.candidate_independent_label is not None:
            _require_nonempty_text(
                self.candidate_independent_label,
                "challenge observation candidate_independent_label",
            )


@dataclass(frozen=True, slots=True)
class PrematureMergeObservationV2:
    """Slim-local recovery-premerge probe 的实际结果；checker 未到达保持 null。"""

    recovery_attempt_id: str
    gate_satisfied: bool
    required_child_unit_ids: tuple[str, ...]
    canonical_child_unit_ids: tuple[str, ...]
    missing_required_slot_ids: tuple[str, ...]
    plugin_merge_attempted: bool
    plugin_outcome: str
    plugin_error_kind: str | None
    root_checker_reached: bool
    root_check_passed: bool | None
    final_result_present: bool
    failure_stage: str | None
    schema_version: str = "tokenshare.slim_v2.premature_merge_observation.v2"

    def validate(self) -> None:
        _require_nonempty_text(self.recovery_attempt_id, "recovery_attempt_id")
        _require_bool(self.gate_satisfied, "gate_satisfied")
        _require_bool(self.plugin_merge_attempted, "plugin_merge_attempted")
        _require_bool(self.root_checker_reached, "root_checker_reached")
        _validate_optional_bool(self.root_check_passed, "root_check_passed")
        _require_bool(self.final_result_present, "final_result_present")
        if not self.root_checker_reached and self.root_check_passed is not None:
            raise SchemaValidationError(
                "checker-not-reached premature merge must keep root_check_passed null"
            )
        if self.plugin_outcome not in {
            "not_attempted",
            "rejected_incomplete_input",
            "candidate_produced",
        }:
            raise SchemaValidationError("invalid premature merge plugin_outcome")
        if self.failure_stage is not None and self.failure_stage not in {
            "plugin_merge",
            "root_checker",
            "no_final",
        }:
            raise SchemaValidationError("invalid premature merge failure_stage")


@dataclass(slots=True)
class AblationObservationV1(SchemaRecordV1):
    disabled_mechanism: str | None = _required()
    route_status: str | None = _nullable()
    domain_parser_call_count: int | None = _nullable()
    domain_child_checker_call_count: int | None = _nullable()
    plugin_verify_submission_call_count: int | None = _nullable()
    root_checker_call_count: int | None = _nullable()
    candidate_independent_label: str | None = _nullable()
    wrong_canonical_accepted: bool | None = _nullable()
    root_checker_reached: bool | None = _nullable()
    root_check_passed: bool | None = _nullable()
    root_checker_rejected_after_wrong_canonical: bool | None = _nullable()
    raw_only_exposed: bool | None = _nullable()
    raw_only_accepted: bool | None = _nullable()
    parse_result: str | None = _nullable()
    recovery_attempt_id: str | None = _nullable()
    recovery_retry_allowed: bool | None = _nullable()
    replacement_attempt_id: str | None = _nullable()
    stuck_due_to_no_requeue: bool | None = _nullable()
    merge_gate_satisfied: bool | None = _nullable()
    required_child_unit_ids: list[str] | None = _nullable()
    canonical_child_unit_ids: list[str] | None = _nullable()
    missing_required_slot_ids: list[str] | None = _nullable()
    plugin_merge_attempted: bool | None = _nullable()
    plugin_outcome: str | None = _nullable()
    plugin_error_kind: str | None = _nullable()
    premature_merge_attempted: bool | None = _nullable()
    premature_merge_failed: bool | None = _nullable()
    final_result_present: bool | None = _nullable()
    failure_stage: str | None = _nullable()

    def validate(self) -> None:
        _require_nonempty_text(
            self.disabled_mechanism,
            "ablation observation disabled_mechanism",
        )
        if self.candidate_independent_label is not None:
            _require_nonempty_text(
                self.candidate_independent_label,
                "ablation observation candidate_independent_label",
            )
        for name in (
            "route_status",
            "parse_result",
            "recovery_attempt_id",
            "replacement_attempt_id",
            "plugin_outcome",
            "plugin_error_kind",
            "failure_stage",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_nonempty_text(value, f"ablation observation {name}")
        for name in (
            "domain_parser_call_count",
            "domain_child_checker_call_count",
            "plugin_verify_submission_call_count",
            "root_checker_call_count",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative_int(value, f"ablation observation {name}")
        for name in (
            "wrong_canonical_accepted",
            "root_checker_reached",
            "root_check_passed",
            "root_checker_rejected_after_wrong_canonical",
            "raw_only_exposed",
            "raw_only_accepted",
            "recovery_retry_allowed",
            "stuck_due_to_no_requeue",
            "merge_gate_satisfied",
            "plugin_merge_attempted",
            "premature_merge_attempted",
            "premature_merge_failed",
            "final_result_present",
        ):
            _validate_optional_bool(
                getattr(self, name),
                f"ablation observation {name}",
            )
        for name in (
            "required_child_unit_ids",
            "canonical_child_unit_ids",
            "missing_required_slot_ids",
        ):
            value = getattr(self, name)
            if value is not None and any(
                not isinstance(item, str) or not item for item in value
            ):
                raise SchemaValidationError(
                    f"ablation observation {name} must contain text IDs"
                )
        if self.root_checker_reached is False and (
            self.root_check_passed is not None
            or self.root_checker_call_count != 0
        ):
            raise SchemaValidationError(
                "checker-not-reached ablation must keep pass null and call count zero"
            )


ACTUAL_PROVIDER_FIELDS = frozenset(
    {
        "provider_call_made",
        "http_status",
        "provider_latency_ms",
        "prompt_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "provider_request_started_at_utc",
        "pricing_version",
        "pricing_tier",
        "cost_estimate_cny",
    }
)

SOURCE_TRACE_FIELDS = frozenset(
    {
        "source_response_slot_id",
        "source_response_consumed",
        "source_attempt_ordinal",
        "source_attempt_fallback_used",
        "source_trace_origin",
        "source_result_kind",
        "source_case_id",
        "source_repeat_id",
        "source_planned_ai_unit_id",
        "source_unit_candidate_start",
        "source_unit_candidate_end",
        "source_lemma_node_id",
        "source_dependency_path",
        "source_latency_ms",
        "source_total_tokens",
        "source_cost_estimate_cny",
        "source_prompt_tokens",
        "source_prompt_cache_hit_tokens",
        "source_prompt_cache_miss_tokens",
        "source_completion_tokens",
        "source_reasoning_tokens",
        "source_pricing_version",
        "source_pricing_tier",
    }
)

SIMULATED_RESOURCE_FIELDS = frozenset(
    {
        "simulated_total_tokens",
        "simulated_latency_ms",
        "token_perturbation_factor",
        "network_perturbation_factor",
        "perturbation_seed",
        "perturbation_version",
    }
)


@dataclass(slots=True)
class AttemptResultV1(SchemaRecordV1):
    attempt_id: str | None = _required()
    unit_id: str | None = _required()
    planned_ai_unit_id: str | None = _required()
    attempt_ordinal: int | None = _required()
    trace_origin: str | None = _nullable()
    replacement_of_attempt_id: str | None = _nullable()
    recovery_trigger: str | None = _nullable()
    started_at_ms: int | None = _nullable()
    ended_at_ms: int | None = _nullable()
    result_kind: str | None = _required()
    provider_call_made: bool | None = _required()
    http_status: int | None = _nullable()
    provider_latency_ms: int | None = _nullable()
    raw_response_present: bool | None = _required()
    raw_response_relative_path: str | None = _nullable(operational=True)
    parse_result: str | None = _nullable()
    verifier_result: str | None = _nullable()
    checker_result: str | None = _nullable()
    canonical_accepted: bool | None = _required()
    prompt_tokens: int | None = _nullable()
    prompt_cache_hit_tokens: int | None = _nullable()
    prompt_cache_miss_tokens: int | None = _nullable()
    completion_tokens: int | None = _nullable()
    reasoning_tokens: int | None = _nullable()
    total_tokens: int | None = _nullable()
    provider_request_started_at_utc: str | None = _nullable()
    pricing_version: str | None = _nullable()
    pricing_tier: str | None = _nullable()
    cost_estimate_cny: float | None = _nullable()
    usage_status: str | None = _required()
    source_response_slot_id: str | None = _nullable()
    source_response_consumed: bool | None = _nullable()
    source_attempt_ordinal: int | None = _nullable()
    source_attempt_fallback_used: bool | None = _nullable()
    source_trace_origin: str | None = _nullable()
    source_result_kind: str | None = _nullable()
    source_case_id: str | None = _nullable()
    source_repeat_id: int | None = _nullable()
    source_planned_ai_unit_id: str | None = _nullable()
    source_unit_candidate_start: int | None = _nullable()
    source_unit_candidate_end: int | None = _nullable()
    source_lemma_node_id: str | None = _nullable()
    source_dependency_path: list[str] | None = _nullable()
    source_latency_ms: int | None = _nullable()
    source_total_tokens: int | None = _nullable()
    source_cost_estimate_cny: float | None = _nullable()
    source_prompt_tokens: int | None = _nullable()
    source_prompt_cache_hit_tokens: int | None = _nullable()
    source_prompt_cache_miss_tokens: int | None = _nullable()
    source_completion_tokens: int | None = _nullable()
    source_reasoning_tokens: int | None = _nullable()
    source_pricing_version: str | None = _nullable()
    source_pricing_tier: str | None = _nullable()
    simulated_total_tokens: int | None = _nullable()
    simulated_latency_ms: int | None = _nullable()
    token_perturbation_factor: float | None = _nullable()
    network_perturbation_factor: float | None = _nullable()
    perturbation_seed: int | None = _nullable()
    perturbation_version: str | None = _nullable()
    call_state: str | None = _required(operational=True)
    missing_reason: dict[str, str] = _required_factory(dict, operational=True)

    def _validate_fixed_source_selection(self, experiment_id: str) -> None:
        if self.source_response_consumed is not True:
            raise SchemaValidationError(
                f"{experiment_id} source_response_consumed must be true"
            )
        for name in (
            "source_response_slot_id",
            "source_result_kind",
            "source_case_id",
            "source_planned_ai_unit_id",
        ):
            _require_nonempty_text(getattr(self, name), name)
        _require_nonnegative_int(
            self.source_attempt_ordinal,
            "source_attempt_ordinal",
        )
        _require_bool(
            self.source_attempt_fallback_used,
            "source_attempt_fallback_used",
        )
        if self.source_trace_origin not in _TRACE_ORIGINS:
            raise SchemaValidationError(
                f"invalid source_trace_origin {self.source_trace_origin!r}"
            )
        if self.source_repeat_id != 0:
            raise SchemaValidationError("source_repeat_id must be 0")

    def _require_fields_null(
        self,
        experiment_id: str,
        field_names: frozenset[str],
        field_family: str,
    ) -> None:
        ordered_names = sorted(
            field_names,
            key=lambda name: (name != "source_response_slot_id", name),
        )
        for field_name in ordered_names:
            value = getattr(self, field_name)
            if value is not None:
                raise SchemaValidationError(
                    f"{experiment_id} {field_family} field {field_name} must be null"
                )
            require_null_reason(
                f"attempts[].{field_name}",
                value,
                self.missing_reason,
                {},
            )

    def _validate_exp3_simulated_resources(self) -> None:
        for field_name in (
            "token_perturbation_factor",
            "network_perturbation_factor",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not -0.10 <= value <= 0.10
            ):
                raise SchemaValidationError(
                    f"Exp3 {field_name} must be a value in [-0.10, 0.10]"
                )
        if self.perturbation_seed != 20260820:
            raise SchemaValidationError(
                "Exp3 perturbation_seed must equal 20260820"
            )
        if self.perturbation_version != "slim_v2.exp3_perturbation.v1":
            raise SchemaValidationError(
                "Exp3 perturbation_version must equal "
                "slim_v2.exp3_perturbation.v1"
            )

        if self.source_latency_ms is None:
            if self.simulated_latency_ms is not None:
                raise SchemaValidationError(
                    "Exp3 simulated_latency_ms must be null when "
                    "source_latency_ms is null"
                )
            require_null_reason(
                "attempts[].simulated_latency_ms",
                self.simulated_latency_ms,
                self.missing_reason,
                {},
            )
        else:
            if self.simulated_latency_ms is None:
                raise SchemaValidationError(
                    "Exp3 simulated_latency_ms is required when "
                    "source_latency_ms is present"
                )
            if (
                isinstance(self.simulated_latency_ms, bool)
                or not isinstance(self.simulated_latency_ms, int)
                or self.simulated_latency_ms < 1
            ):
                raise SchemaValidationError(
                    "Exp3 simulated_latency_ms must be a positive integer"
                )

        can_derive_total = self.source_total_tokens is not None or (
            self.source_prompt_tokens is not None
            and self.source_completion_tokens is not None
        )
        if can_derive_total:
            if self.simulated_total_tokens is None:
                raise SchemaValidationError(
                    "Exp3 simulated_total_tokens is required when source token "
                    "inputs are sufficient"
                )
            if (
                isinstance(self.simulated_total_tokens, bool)
                or not isinstance(self.simulated_total_tokens, int)
                or self.simulated_total_tokens < 1
            ):
                raise SchemaValidationError(
                    "Exp3 simulated_total_tokens must be a positive integer"
                )
        else:
            if self.simulated_total_tokens is not None:
                raise SchemaValidationError(
                    "Exp3 simulated_total_tokens must be null when source token "
                    "inputs are insufficient"
                )
            require_null_reason(
                "attempts[].simulated_total_tokens",
                self.simulated_total_tokens,
                self.missing_reason,
                {},
            )

    def validate_provider_mode(self, experiment_id: str) -> None:
        if experiment_id not in _EXPERIMENTS:
            raise SchemaValidationError(f"unknown experiment_id {experiment_id!r}")
        if self.call_state not in _CALL_STATES:
            raise SchemaValidationError(f"invalid call_state {self.call_state!r}")
        if experiment_id in {"exp1", "exp5"}:
            self._require_fields_null(
                experiment_id,
                SOURCE_TRACE_FIELDS,
                "replay-only source",
            )
            self._require_fields_null(
                experiment_id,
                SIMULATED_RESOURCE_FIELDS,
                "replay-only simulated",
            )
            return
        if self.call_state != "not_started":
            raise SchemaValidationError(
                f"{experiment_id} call_state must be 'not_started'"
            )
        if self.provider_call_made is not False:
            raise SchemaValidationError(
                f"{experiment_id} provider_call_made must be false"
            )
        for field_name in sorted(ACTUAL_PROVIDER_FIELDS - {"provider_call_made"}):
            value = getattr(self, field_name)
            if value is not None:
                raise SchemaValidationError(
                    f"{experiment_id} actual provider field {field_name} must be null"
                )
            require_null_reason(
                f"attempts[].{field_name}",
                value,
                self.missing_reason,
                {},
            )
        self._validate_fixed_source_selection(experiment_id)
        if experiment_id in {"exp2", "exp4"}:
            self._require_fields_null(
                experiment_id,
                SIMULATED_RESOURCE_FIELDS,
                "simulated resource",
            )
        else:
            self._validate_exp3_simulated_resources()

    def validate(self, *, experiment_id: str | None = None) -> None:
        _validate_record_values(
            self,
            prefix="attempts[].",
            missing_reason=self.missing_reason,
            not_applicable_reason={},
        )
        _require_nonempty_text(self.attempt_id, "attempt_id")
        _require_nonempty_text(self.unit_id, "unit_id")
        _require_nonempty_text(self.planned_ai_unit_id, "planned_ai_unit_id")
        _require_nonnegative_int(self.attempt_ordinal, "attempt_ordinal")
        _require_nonempty_text(self.result_kind, "result_kind")
        _require_bool(self.provider_call_made, "provider_call_made")
        _require_bool(self.raw_response_present, "raw_response_present")
        _require_bool(self.canonical_accepted, "canonical_accepted")
        _require_nonempty_text(self.usage_status, "usage_status")
        if self.call_state not in _CALL_STATES:
            raise SchemaValidationError(f"invalid call_state {self.call_state!r}")
        if self.trace_origin is not None and self.trace_origin not in _TRACE_ORIGINS:
            raise SchemaValidationError(f"invalid trace_origin {self.trace_origin!r}")
        for name in (
            "started_at_ms",
            "ended_at_ms",
            "provider_latency_ms",
            "source_latency_ms",
            "simulated_latency_ms",
        ):
            _validate_optional_ms(getattr(self, name), name)
        if (
            self.started_at_ms is not None
            and self.ended_at_ms is not None
            and self.ended_at_ms < self.started_at_ms
        ):
            raise SchemaValidationError("attempt ended_at_ms precedes started_at_ms")
        if self.source_repeat_id not in (None, 0):
            raise SchemaValidationError("source_repeat_id must be 0 when present")
        if self.source_trace_origin is not None and self.source_trace_origin not in _TRACE_ORIGINS:
            raise SchemaValidationError(
                f"invalid source_trace_origin {self.source_trace_origin!r}"
            )
        if (
            self.reasoning_tokens is not None
            and self.completion_tokens is not None
            and self.reasoning_tokens > self.completion_tokens
        ):
            raise SchemaValidationError("reasoning_tokens must be a completion subset")
        if (
            self.source_reasoning_tokens is not None
            and self.source_completion_tokens is not None
            and self.source_reasoning_tokens > self.source_completion_tokens
        ):
            raise SchemaValidationError(
                "source_reasoning_tokens must be a source completion subset"
            )
        _validate_rfc3339(
            self.provider_request_started_at_utc,
            "provider_request_started_at_utc",
        )
        if experiment_id is not None:
            self.validate_provider_mode(experiment_id)


@dataclass(slots=True)
class SlimRunConfigV1(SchemaRecordV1):
    schema_version: str = _required(RUN_CONFIG_SCHEMA_VERSION)
    run_id: str | None = _required()
    profile_id: str | None = _required()
    experiment_ids: list[str] = _required_factory(list)
    source_run_dir: str | None = _nullable()
    exp1_provider_config_path: str = _required(
        _EXP1_PROVIDER_CONFIG_PATH
    )
    exp5_provider_config_path: str = _required(
        _EXP5_PROVIDER_CONFIG_PATH
    )
    local_secret_config_path: str = _required(_LOCAL_SECRET_CONFIG_PATH)
    pricing_versions: dict[str, str] = _required_factory(
        lambda: dict(PRICING_VERSIONS)
    )
    ordinary_parallel_backend_kind: str = _required("thread")
    response_max_bytes: int = _required(_RESPONSE_MAX_BYTES)
    reducer_workers: int = _required(1)

    def validate(self) -> None:
        if self.schema_version != RUN_CONFIG_SCHEMA_VERSION:
            raise SchemaValidationError("invalid SlimRunConfigV1 schema_version")
        _require_nonempty_text(self.run_id, "run_id")
        if self.profile_id not in _PROFILES:
            raise SchemaValidationError(f"invalid profile_id {self.profile_id!r}")
        if not self.experiment_ids or any(item not in _EXPERIMENTS for item in self.experiment_ids):
            raise SchemaValidationError("experiment_ids must be a non-empty Exp1-5 subsequence")
        positions = [_EXPERIMENT_ORDER.index(item) for item in self.experiment_ids]
        if any(current <= previous for previous, current in zip(positions, positions[1:])):
            raise SchemaValidationError(
                "experiment_ids must be an ordered Exp1-5 subsequence"
            )
        if self.ordinary_parallel_backend_kind != "thread":
            raise SchemaValidationError(
                "ordinary_parallel_backend_kind must equal frozen value 'thread'"
            )
        frozen_paths = {
            "exp1_provider_config_path": _EXP1_PROVIDER_CONFIG_PATH,
            "exp5_provider_config_path": _EXP5_PROVIDER_CONFIG_PATH,
            "local_secret_config_path": _LOCAL_SECRET_CONFIG_PATH,
        }
        for field_name, expected in frozen_paths.items():
            if getattr(self, field_name) != expected:
                raise SchemaValidationError(f"{field_name} must equal its frozen path")
        if self.pricing_versions != PRICING_VERSIONS:
            raise SchemaValidationError(
                "pricing_versions must equal frozen Slim V2 mappings"
            )
        frozen_limits = {"response_max_bytes": _RESPONSE_MAX_BYTES}
        for field_name, expected in frozen_limits.items():
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value != expected:
                raise SchemaValidationError(
                    f"{field_name} must equal frozen value {expected}"
                )
        if (
            isinstance(self.reducer_workers, bool)
            or not isinstance(self.reducer_workers, int)
            or self.reducer_workers != 1
        ):
            raise SchemaValidationError("reducer_workers must equal frozen value 1")
        is_run_all = self.experiment_ids == list(_EXPERIMENT_ORDER)
        needs_external_source = any(
            item in {"exp2", "exp3", "exp4"} for item in self.experiment_ids
        ) and not is_run_all
        if needs_external_source:
            _require_nonempty_text(self.source_run_dir, "source_run_dir")
        elif self.source_run_dir is not None:
            raise SchemaValidationError(
                "source_run_dir must be null for Exp1/5-only or run-all config"
            )


@dataclass(slots=True)
class RootInventoryV1(SchemaRecordV1):
    schema_version: str = _required(ROOT_INVENTORY_SCHEMA_VERSION)
    experiment_id: str | None = _required()
    condition_id: str | None = _required()
    case_id: str | None = _required()
    repeat_id: int | None = _required()
    domain: str | None = _required()
    difficulty: str | None = _required()
    topic_family: str | None = _nullable()
    position_stratum: str | None = _nullable()
    worker_count: int | None = _required()
    mode: str | None = _nullable()
    disabled_mechanisms: list[str] = _required_factory(list)
    fault_type: str | None = _nullable()
    fault_rate: float | None = _nullable()
    dead_worker_count: int | None = _nullable()
    kill_progress_target_ratio: float | None = _nullable()
    provider_entry_id: str | None = _required()
    configured_model: str | None = _required()
    planned_ai_unit_ids: list[str] = _required_factory(list)
    challenge_plan_id: str | None = _nullable()

    def validate(self) -> None:
        if self.schema_version != ROOT_INVENTORY_SCHEMA_VERSION:
            raise SchemaValidationError("invalid RootInventoryV1 schema_version")
        if self.experiment_id not in _EXPERIMENTS:
            raise SchemaValidationError(f"invalid experiment_id {self.experiment_id!r}")
        for name in (
            "condition_id",
            "case_id",
            "domain",
            "difficulty",
            "provider_entry_id",
            "configured_model",
        ):
            _require_nonempty_text(getattr(self, name), name)
        _require_nonnegative_int(self.repeat_id, "repeat_id")
        if isinstance(self.worker_count, bool) or not isinstance(self.worker_count, int) or self.worker_count <= 0:
            raise SchemaValidationError("worker_count must be a positive integer")
        if len(self.planned_ai_unit_ids) != len(set(self.planned_ai_unit_ids)):
            raise SchemaValidationError("planned_ai_unit_ids must be unique")


@dataclass(slots=True)
class UnitTraceV1(SchemaRecordV1):
    schema_version: str = _required(UNIT_TRACE_SCHEMA_VERSION)
    case_id: str | None = _required()
    source_repeat_id: int = _required(0)
    planned_ai_unit_id: str | None = _required()
    domain: str | None = _required()
    trace_origin: str | None = _required()
    candidate_start: int | None = _nullable()
    candidate_end: int | None = _nullable()
    lemma_node_id: str | None = _nullable()
    dependency_path: list[str] | None = _nullable()
    provider_family: str | None = _required()
    provider_entry_id: str | None = _required()
    configured_model: str | None = _required()
    requested_model: str | None = _required()
    resolved_model: str | None = _required()
    attempts: list[AttemptResultV1] = _required_factory(list)

    def validate(self) -> None:
        if self.schema_version != UNIT_TRACE_SCHEMA_VERSION:
            raise SchemaValidationError("invalid UnitTraceV1 schema_version")
        for name in (
            "case_id",
            "planned_ai_unit_id",
            "domain",
            "provider_family",
            "provider_entry_id",
            "configured_model",
            "requested_model",
            "resolved_model",
        ):
            _require_nonempty_text(getattr(self, name), name)
        if self.source_repeat_id != 0:
            raise SchemaValidationError("UnitTraceV1 source_repeat_id must be 0")
        if self.trace_origin not in _TRACE_ORIGINS:
            raise SchemaValidationError(f"invalid trace_origin {self.trace_origin!r}")
        if not self.attempts:
            raise SchemaValidationError("UnitTraceV1 requires at least one natural attempt")
        if len(self.attempts) > 3:
            raise SchemaValidationError(
                "UnitTraceV1 permits at most 3 natural attempts"
            )
        ordinals = [attempt.attempt_ordinal for attempt in self.attempts]
        if ordinals != list(range(len(self.attempts))):
            raise SchemaValidationError(
                "UnitTraceV1 attempt ordinals must be contiguous from 0"
            )
        if any(attempt.trace_origin != self.trace_origin for attempt in self.attempts):
            raise SchemaValidationError("trace and attempt trace_origin must match")
        for attempt in self.attempts:
            attempt.validate(experiment_id="exp1")
        if self.domain == "factorization":
            if self.candidate_start is None or self.candidate_end is None:
                raise SchemaValidationError("Factorization trace requires candidate range")
            _require_nonnegative_int(self.candidate_start, "candidate_start")
            _require_nonnegative_int(self.candidate_end, "candidate_end")
            if self.candidate_end <= self.candidate_start:
                raise SchemaValidationError("candidate_end must exceed candidate_start")
            if self.lemma_node_id is not None or self.dependency_path is not None:
                raise SchemaValidationError("Factorization trace cannot carry Lean semantics")
        elif self.domain == "lean":
            if self.lemma_node_id is None or self.dependency_path is None:
                raise SchemaValidationError("Lean trace requires node/dependency semantics")
            if self.candidate_start is not None or self.candidate_end is not None:
                raise SchemaValidationError("Lean trace cannot carry Factorization semantics")
        else:
            raise SchemaValidationError(f"invalid trace domain {self.domain!r}")


@dataclass(slots=True)
class ProviderEntryViewV1(SchemaRecordV1):
    provider_family: str | None = _required()
    entry_id: str | None = _required()
    base_url: str | None = _required()
    endpoint: str | None = _required()
    api_key_env: str | None = _required()
    configured_model: str | None = _required()
    request_overrides: dict[str, Any] = _required_factory(dict)
    supports_json_mode: bool | None = _required()

    def validate(self) -> None:
        for name in (
            "provider_family",
            "entry_id",
            "base_url",
            "endpoint",
            "api_key_env",
            "configured_model",
        ):
            _require_nonempty_text(getattr(self, name), name)
        if not isinstance(self.supports_json_mode, bool):
            raise SchemaValidationError("supports_json_mode must be boolean")


@dataclass(slots=True)
class ProviderRequestControlV1(SchemaRecordV1):
    timeout_seconds: float | None = _required()
    max_tokens: int | None = _required()
    require_json_mode: bool | None = _required()

    def validate(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds,
            (int, float),
        ) or self.timeout_seconds <= 0:
            raise SchemaValidationError("timeout_seconds must be positive")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise SchemaValidationError("max_tokens must be a positive integer")
        if not isinstance(self.require_json_mode, bool):
            raise SchemaValidationError("require_json_mode must be boolean")


@dataclass(slots=True)
class ProviderCallResultV1(SchemaRecordV1):
    ok: bool | None = _required()
    content_text: str | None = _nullable()
    reasoning_content: str | None = _nullable()
    raw_response_json: Any | None = _nullable()
    prompt_tokens: int | None = _nullable()
    prompt_cache_hit_tokens: int | None = _nullable()
    prompt_cache_miss_tokens: int | None = _nullable()
    completion_tokens: int | None = _nullable()
    reasoning_tokens: int | None = _nullable()
    total_tokens: int | None = _nullable()
    provider_request_started_at_utc: str | None = _nullable()
    provider_latency_ms: int | None = _nullable()
    configured_model: str | None = _required()
    requested_model: str | None = _required()
    resolved_model: str | None = _nullable()
    provider_response_id: str | None = _nullable()
    finish_reason: str | None = _nullable()
    http_status: int | None = _nullable()
    error_kind: str | None = _nullable()
    error_message: str | None = _nullable()
    usage_status: str | None = _required()

    def validate(self) -> None:
        if not isinstance(self.ok, bool):
            raise SchemaValidationError("ok must be boolean")
        _require_nonempty_text(self.configured_model, "configured_model")
        _require_nonempty_text(self.requested_model, "requested_model")
        _require_nonempty_text(self.usage_status, "usage_status")
        _validate_optional_ms(self.provider_latency_ms, "provider_latency_ms")
        _validate_rfc3339(
            self.provider_request_started_at_utc,
            "provider_request_started_at_utc",
        )
        if self.ok:
            if not isinstance(self.content_text, str):
                raise SchemaValidationError("successful provider content_text must be text")
            if self.resolved_model is None:
                raise SchemaValidationError("successful provider result requires resolved_model")
        if (
            self.reasoning_tokens is not None
            and self.completion_tokens is not None
            and self.reasoning_tokens > self.completion_tokens
        ):
            raise SchemaValidationError("reasoning_tokens must be a completion subset")


@dataclass(slots=True)
class RootResultV2(SchemaRecordV1):
    SCHEMA_VERSION: ClassVar[str] = ROOT_RESULT_SCHEMA_VERSION

    experiment_id: str | None = _required()
    condition_id: str | None = _required()
    case_id: str | None = _required()
    repeat_id: int | None = _required()
    domain: str | None = _required()
    difficulty: str | None = _required()
    topic_family: str | None = _nullable()
    position_stratum: str | None = _nullable()
    mode: str | None = _nullable()
    disabled_mechanisms: list[str] = _required_factory(list)
    worker_count: int | None = _required()
    fault_type: str | None = _nullable()
    fault_rate: float | None = _nullable()
    dead_worker_count: int | None = _nullable()
    kill_progress_target_ratio: float | None = _nullable()
    challenge_plan_id: str | None = _nullable()
    challenge_family: str | None = _nullable()
    challenge_target_planned_ai_unit_ids: list[str] | None = _nullable()
    challenge_attempt_ordinal_rule: str | None = _nullable()
    provider_family: str | None = _required()
    provider_entry_id: str | None = _required()
    configured_model: str | None = _required()
    requested_model: str | None = _required()
    resolved_model: str | None = _nullable()
    reasoning_mode: str | None = _required()
    root_start_at_ms: int | None = _nullable()
    root_terminal_at_ms: int | None = _nullable()
    runtime_wall_clock_ms: int | None = _nullable()
    trace_tail_started_at_ms: int | None = _nullable()
    trace_tail_terminal_at_ms: int | None = _nullable()
    trace_tail_wall_clock_ms: int | None = _nullable()
    trace_tail_status: str | None = _nullable()
    trace_tail_target_ai_unit_ids: list[str] | None = _nullable()
    trace_tail_recorded_ai_unit_ids: list[str] | None = _nullable()
    trace_tail_success_unit_count: int | None = _nullable(operational=True)
    trace_tail_failure_unit_count: int | None = _nullable(operational=True)
    trace_tail_provider_attempt_count: int | None = _nullable()
    trace_tail_total_tokens: int | None = _nullable()
    trace_tail_cost_estimate_cny: float | None = _nullable()
    preflight_status: str | None = _required()
    protocol_started: bool | None = _required()
    root_status: str | None = _required()
    final_result_present: bool | None = _required()
    verified_correct: bool | None = _required()
    failure_stage: str | None = _nullable()
    failure_kind: str | None = _nullable()
    failure_origin: str | None = _nullable()
    planned_ai_unit_ids: list[str] = _required_factory(list)
    dispatched_ai_unit_ids: list[str] = _required_factory(list)
    completed_ai_unit_ids: list[str] = _required_factory(list)
    unscheduled_ai_unit_ids: list[str] = _required_factory(list)
    in_flight_ai_unit_ids_at_witness: list[str] | None = _nullable()
    observed_peak_concurrency: int | None = _nullable()
    worker_execution_facts: list[WorkerExecutionFactV1] = _required_factory(list)
    required_slot_count: int | None = _nullable()
    recovered_valid_canonical_slot_count: int | None = _nullable()
    attempts: list[AttemptResultV1] = _required_factory(list)
    fault_target_planned_ai_unit_ids: list[str] | None = _nullable()
    fault_target_count: int | None = _nullable()
    fault_observations: list[FaultObservationV1] = _required_factory(list)
    recovery_observations: list[RecoveryObservationV1] = _required_factory(list)
    worker_death_observations: list[WorkerDeathObservationV1] = _required_factory(list)
    challenge_observations: list[ChallengeObservationV1] = _required_factory(list)
    ablation_observations: list[AblationObservationV1] = _required_factory(list)
    missing_reason: dict[str, str] = _required_factory(dict)
    not_applicable_reason: dict[str, str] = _required_factory(dict)

    def _validate_lifecycle_boundaries(self) -> None:
        _require_bool(self.protocol_started, "protocol_started")
        _require_bool(self.final_result_present, "final_result_present")
        _require_bool(self.verified_correct, "verified_correct")
        if self.verified_correct is True and self.final_result_present is not True:
            raise SchemaValidationError(
                "verified_correct=true requires final_result_present=true"
            )
        timing_names = (
            "root_start_at_ms",
            "root_terminal_at_ms",
            "runtime_wall_clock_ms",
        )
        timing_values = tuple(getattr(self, name) for name in timing_names)
        if self.protocol_started is False:
            if any(value is not None for value in timing_values):
                raise SchemaValidationError(
                    "root timing fields must all be null with "
                    "protocol_lifecycle_not_started when protocol_started=false"
                )
            for name in timing_names:
                if self.missing_reason.get(name) != "protocol_lifecycle_not_started":
                    raise SchemaValidationError(
                        f"{name} missing_reason must be "
                        "protocol_lifecycle_not_started"
                    )
            if self.verified_correct is not False:
                raise SchemaValidationError(
                    "verified_correct must be false for early infrastructure_invalid"
                )
            if self.failure_kind == "incorrect_final":
                raise SchemaValidationError(
                    "protocol-not-started root cannot be classified incorrect_final"
                )
            if self.failure_kind != "infrastructure_invalid":
                raise SchemaValidationError(
                    "protocol-not-started root must be infrastructure_invalid"
                )
        else:
            if any(value is None for value in timing_values):
                raise SchemaValidationError(
                    "root timing fields must all be non-null when protocol_started=true"
                )
            for name, value in zip(timing_names, timing_values):
                _require_nonnegative_int(value, name)
            assert self.root_start_at_ms is not None
            assert self.root_terminal_at_ms is not None
            assert self.runtime_wall_clock_ms is not None
            if self.root_terminal_at_ms < self.root_start_at_ms:
                raise SchemaValidationError(
                    "root_terminal_at_ms precedes root_start_at_ms"
                )
            if self.runtime_wall_clock_ms != (
                self.root_terminal_at_ms - self.root_start_at_ms
            ):
                raise SchemaValidationError(
                    "runtime_wall_clock_ms must equal "
                    "root_terminal_at_ms-root_start_at_ms"
                )

        model_boundary_reached = bool(self.final_result_present) or any(
            attempt.raw_response_present is True
            or attempt.source_response_consumed is True
            for attempt in self.attempts
        )
        if self.resolved_model is None:
            if self.missing_reason.get("resolved_model") != "model_resolution_not_reached":
                raise SchemaValidationError(
                    "resolved_model missing_reason must be "
                    "model_resolution_not_reached"
                )
            if model_boundary_reached:
                raise SchemaValidationError(
                    "resolved_model is required after a response, fixed source, "
                    "or final result is observed"
                )
        else:
            _require_nonempty_text(self.resolved_model, "resolved_model")
        if self.protocol_started is False and self.final_result_present is not False:
            raise SchemaValidationError(
                "protocol-not-started root cannot have a final result"
            )
        if self.failure_kind is not None and self.failure_kind not in {
            "no_final",
            "incorrect_final",
            "infrastructure_invalid",
        }:
            raise SchemaValidationError("failure_kind must use the frozen top-level taxonomy")
        if self.failure_kind is not None and self.verified_correct is not False:
            raise SchemaValidationError(
                "failure_kind requires verified_correct=false"
            )
        if self.verified_correct is False and self.failure_kind is None:
            raise SchemaValidationError("failed root must carry failure_kind")
        if self.failure_kind is None and self.failure_origin is not None:
            raise SchemaValidationError("successful root cannot carry failure_origin")
        if self.failure_kind in {"no_final", "infrastructure_invalid"} and (
            not isinstance(self.failure_origin, str) or not self.failure_origin.strip()
        ):
            raise SchemaValidationError(
                "no_final and infrastructure_invalid require a non-empty failure_origin"
            )

    def _validate_tail_fields(self) -> None:
        tail_names = (
            "trace_tail_started_at_ms",
            "trace_tail_terminal_at_ms",
            "trace_tail_wall_clock_ms",
            "trace_tail_status",
            "trace_tail_target_ai_unit_ids",
            "trace_tail_recorded_ai_unit_ids",
            "trace_tail_success_unit_count",
            "trace_tail_failure_unit_count",
            "trace_tail_provider_attempt_count",
            "trace_tail_total_tokens",
            "trace_tail_cost_estimate_cny",
        )
        if self.experiment_id != "exp1":
            for name in tail_names:
                if getattr(self, name) is not None:
                    raise SchemaValidationError(
                        f"non-Exp1 tail field {name} must be null"
                    )
            return

        if self.trace_tail_target_ai_unit_ids is None:
            raise SchemaValidationError("Exp1 tail target fields are required")
        targets = self.trace_tail_target_ai_unit_ids
        if len(targets) != len(set(targets)):
            raise SchemaValidationError("Exp1 tail target IDs must be unique")
        if not targets:
            expected = (
                self.trace_tail_started_at_ms,
                self.trace_tail_terminal_at_ms,
                self.trace_tail_wall_clock_ms,
                self.trace_tail_status,
                self.trace_tail_recorded_ai_unit_ids,
                self.trace_tail_success_unit_count,
                self.trace_tail_failure_unit_count,
                self.trace_tail_provider_attempt_count,
                self.trace_tail_total_tokens,
                self.trace_tail_cost_estimate_cny,
            )
            if expected != (None, None, 0, "not_needed", [], 0, 0, 0, 0, 0):
                raise SchemaValidationError("Exp1 no-target tail fields are inconsistent")
            return

        if self.trace_tail_started_at_ms is None or self.trace_tail_terminal_at_ms is None:
            raise SchemaValidationError("Exp1 target tail requires start and terminal")
        _require_nonnegative_int(
            self.trace_tail_started_at_ms,
            "trace_tail_started_at_ms",
        )
        _require_nonnegative_int(
            self.trace_tail_terminal_at_ms,
            "trace_tail_terminal_at_ms",
        )
        _require_nonnegative_int(
            self.trace_tail_wall_clock_ms,
            "trace_tail_wall_clock_ms",
        )
        if self.trace_tail_terminal_at_ms < self.trace_tail_started_at_ms:
            raise SchemaValidationError("trace tail terminal precedes start")
        if self.trace_tail_wall_clock_ms != (
            self.trace_tail_terminal_at_ms - self.trace_tail_started_at_ms
        ):
            raise SchemaValidationError(
                "trace_tail_wall_clock_ms must equal tail terminal-start"
            )
        _require_nonempty_text(self.trace_tail_status, "trace_tail_status")
        if self.trace_tail_status == "not_needed":
            raise SchemaValidationError("targeted Exp1 tail cannot be not_needed")
        if self.trace_tail_recorded_ai_unit_ids != targets:
            raise SchemaValidationError(
                "Exp1 tail recorded IDs must exactly equal target IDs"
            )
        for name in (
            "trace_tail_success_unit_count",
            "trace_tail_failure_unit_count",
            "trace_tail_provider_attempt_count",
        ):
            _require_nonnegative_int(getattr(self, name), name)
        assert self.trace_tail_success_unit_count is not None
        assert self.trace_tail_failure_unit_count is not None
        assert self.trace_tail_provider_attempt_count is not None
        if (
            self.trace_tail_success_unit_count + self.trace_tail_failure_unit_count
            != len(targets)
        ):
            raise SchemaValidationError(
                "Exp1 tail success and failure counts must equal target count"
            )
        if self.trace_tail_provider_attempt_count > 3 * len(targets):
            raise SchemaValidationError(
                "Exp1 tail provider attempt count exceeds the natural retry cap"
            )
        if self.trace_tail_total_tokens is not None:
            _require_nonnegative_int(
                self.trace_tail_total_tokens,
                "trace_tail_total_tokens",
            )
        if self.trace_tail_cost_estimate_cny is not None and (
            isinstance(self.trace_tail_cost_estimate_cny, bool)
            or not isinstance(self.trace_tail_cost_estimate_cny, (int, float))
            or self.trace_tail_cost_estimate_cny < 0
        ):
            raise SchemaValidationError(
                "trace_tail_cost_estimate_cny must be non-negative"
            )

    def _validate_experiment_condition_fields(self) -> None:
        if self.experiment_id == "exp4":
            _require_nonempty_text(self.mode, "Exp4 mode")
            for name in (
                "challenge_plan_id",
                "challenge_family",
                "challenge_attempt_ordinal_rule",
            ):
                _require_nonempty_text(getattr(self, name), f"Exp4 {name}")
            if not self.challenge_target_planned_ai_unit_ids:
                raise SchemaValidationError(
                    "Exp4 challenge target IDs must be non-empty"
                )
            if len(self.challenge_target_planned_ai_unit_ids) != len(
                set(self.challenge_target_planned_ai_unit_ids)
            ):
                raise SchemaValidationError("Exp4 challenge target IDs must be unique")
            for name in (
                "fault_type",
                "fault_rate",
                "dead_worker_count",
                "kill_progress_target_ratio",
                "fault_target_planned_ai_unit_ids",
                "fault_target_count",
            ):
                if getattr(self, name) is not None:
                    raise SchemaValidationError(f"Exp4 {name} must be null")
            if self.fault_observations:
                raise SchemaValidationError("Exp4 fault observations must be empty")
            if len(self.ablation_observations) != len(self.disabled_mechanisms):
                raise SchemaValidationError(
                    "Exp4 ablation observations must match disabled mechanisms"
                )
        else:
            for name in (
                "challenge_plan_id",
                "challenge_family",
                "challenge_target_planned_ai_unit_ids",
                "challenge_attempt_ordinal_rule",
            ):
                if getattr(self, name) is not None:
                    raise SchemaValidationError(f"non-Exp4 {name} must be null")
            if self.challenge_observations or self.ablation_observations:
                raise SchemaValidationError(
                    "non-Exp4 challenge/ablation observations must be empty"
                )

    def _validate_source_identity(self, attempt: AttemptResultV1) -> None:
        if attempt.source_case_id != self.case_id:
            raise SchemaValidationError(
                "source_case_id must equal current root case_id"
            )
        if attempt.source_planned_ai_unit_id != attempt.planned_ai_unit_id:
            raise SchemaValidationError(
                "source_planned_ai_unit_id must equal current planned_ai_unit_id"
            )
        if self.domain == "factorization":
            if (
                attempt.source_unit_candidate_start is None
                or attempt.source_unit_candidate_end is None
                or attempt.source_lemma_node_id is not None
                or attempt.source_dependency_path is not None
            ):
                raise SchemaValidationError(
                    "Factorization source semantics require candidate range only"
                )
        elif self.domain == "lean":
            if (
                attempt.source_lemma_node_id is None
                or attempt.source_dependency_path is None
                or attempt.source_unit_candidate_start is not None
                or attempt.source_unit_candidate_end is not None
            ):
                raise SchemaValidationError(
                    "Lean source semantics require node/dependency fields only"
                )

    def validate(self) -> None:
        _validate_record_values(
            self,
            prefix="",
            missing_reason=self.missing_reason,
            not_applicable_reason=self.not_applicable_reason,
        )
        if self.experiment_id not in _EXPERIMENTS:
            raise SchemaValidationError(f"invalid experiment_id {self.experiment_id!r}")
        for name in (
            "condition_id",
            "case_id",
            "domain",
            "difficulty",
            "provider_family",
            "provider_entry_id",
            "configured_model",
            "requested_model",
            "reasoning_mode",
            "preflight_status",
            "root_status",
        ):
            _require_nonempty_text(getattr(self, name), name)
        _require_nonnegative_int(self.repeat_id, "repeat_id")
        if isinstance(self.worker_count, bool) or not isinstance(self.worker_count, int) or self.worker_count <= 0:
            raise SchemaValidationError("worker_count must be a positive integer")
        self._validate_lifecycle_boundaries()
        self._validate_tail_fields()
        self._validate_experiment_condition_fields()
        for fact in self.worker_execution_facts:
            fact.validate()
        for observation in self.fault_observations:
            observation.validate()
        for observation in self.recovery_observations:
            observation.validate()
        for observation in self.worker_death_observations:
            observation.validate()
        for observation in self.challenge_observations:
            observation.validate()
        for observation in self.ablation_observations:
            observation.validate()
        for attempt in self.attempts:
            attempt.validate(experiment_id=self.experiment_id)
            if self.experiment_id in {"exp2", "exp3", "exp4"}:
                self._validate_source_identity(attempt)


__all__ = [
    "ACTUAL_PROVIDER_FIELDS",
    "LEGACY_PRICING_VERSION",
    "PRE_FLASH_CONFIGURED_MODEL",
    "PRE_FLASH_EXP2_ROOT_KEY",
    "PRE_FLASH_PROVIDER_ENTRY_ID",
    "PRE_FLASH_REPRESENTATIVE_RUN_ID",
    "PRICING_VERSION",
    "PRICING_VERSIONS",
    "ROOT_INVENTORY_SCHEMA_VERSION",
    "ROOT_RESULT_SCHEMA_VERSION",
    "RUN_CONFIG_SCHEMA_VERSION",
    "SIMULATED_RESOURCE_FIELDS",
    "SLIM_V2_SCHEMA_VERSION",
    "SOURCE_TRACE_FIELDS",
    "UNIT_TRACE_SCHEMA_VERSION",
    "AttemptResultV1",
    "ProviderCallResultV1",
    "ProviderEntryViewV1",
    "ProviderRequestControlV1",
    "RootInventoryV1",
    "RootResultV2",
    "SchemaValidationError",
    "SlimRunConfigV1",
    "UnitTraceV1",
    "require_null_reason",
]
