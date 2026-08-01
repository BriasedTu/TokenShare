"""Exp2--4 真实回答库的确定性 semantic-slot inventory 与零引擎预检。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from tokenshare.executors.ai_api_request_identity import (
    PreparedOutboundRequest,
    validate_prepared_request,
)
from tokenshare.executors.response_bank import (
    ResponseBankInventoryRow,
    canonical_digest,
    inventory_entry_id,
    semantic_slot_key,
)


_EXP2_WORKERS = (1, 3, 7, 10, 30, 50)
_EXP2_CASES = (
    ("early", "factor_v2_hard_034", 0),
    ("middle", "factor_v2_hard_122", 9),
    ("late", "factor_v2_hard_063", 1),
    ("no_factor", "factor_v2_hard_161", 44),
)
_EXP4_MODES = {
    "FULL",
    "NO_VERIFICATION",
    "NO_PARSER_POLICY",
    "NO_REQUEUE",
    "NO_MERGE_GATE",
}


@dataclass(frozen=True, kw_only=True)
class SemanticSlotCandidate:
    """一个 condition 对一个已由 adapter 构造的精确出站请求的引用。"""

    experiment_id: str
    condition_id: str
    condition_digest: str
    worker_count: int
    repeat_id: int
    fault_type: str
    ablation_mode: str
    case_id: str
    case_record_digest: str
    planned_ai_unit_id: str
    sample_slot_index: int
    replacement_slot: int
    prompt_profile_digest: str
    prepared_request: PreparedOutboundRequest
    terminal_kind: str | None = None
    precomputed_inventory_entry_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ResponseBankPreflightBlockedRecord:
    schema_version: str
    inventory_entry_id: str
    semantic_slot_key: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class SemanticInventoryPlan:
    schema_version: str
    inventory_digest: str
    rows: tuple[ResponseBankInventoryRow, ...]
    condition_refs: tuple[dict[str, Any], ...]
    exp2_online_condition_refs: tuple[dict[str, Any], ...]
    max_concurrent_roots: int
    expected_slot_count: int
    terminal_provider_failure_count: int
    terminal_success_count: int
    terminal_unacquired_count: int
    observed_early_stop_slot_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class InventoryPreflightResult:
    schema_version: str
    status: str
    blocked_records: tuple[ResponseBankPreflightBlockedRecord, ...]
    protocol_engine_event_count: int
    provider_call_count: int
    coordinator_constructed: bool
    coordinator: Any = None


def replacement_slots_for(
    *, experiment_id: str, fault_type: str, ablation_mode: str
) -> tuple[int, ...]:
    """返回冻结的 acquisition replacement 深度（含初始 slot 0）。"""

    if experiment_id == "exp3_real_ai_fault_recovery":
        return tuple(range(5 if fault_type == "worker_death" else 3))
    if experiment_id == "exp4_real_ai_protocol_ablation":
        if ablation_mode not in _EXP4_MODES:
            raise ValueError("Experiment 4 ablation mode drift")
        return (0, 1)
    return (0,)


def build_semantic_inventory(
    candidates: Sequence[SemanticSlotCandidate],
    *,
    exp2_online_conditions: Sequence[Mapping[str, Any] | Any] = (),
    max_concurrent_roots: int = 1,
    observed_early_stop_slot_keys: Sequence[str] = (),
) -> SemanticInventoryPlan:
    """仅规划并校验完整 inventory；不创建协议对象或发起 provider 调用。"""

    if max_concurrent_roots != 1:
        raise ValueError("max_concurrent_roots must remain 1")
    _validate_replacement_completeness(candidates)
    online_refs = _exp2_online_refs(exp2_online_conditions)
    rows_by_slot: dict[str, ResponseBankInventoryRow] = {}
    request_identity_by_slot: dict[str, tuple[str, str]] = {}
    terminal_kind_by_slot: dict[str, str | None] = {}
    sample_by_repeat: dict[tuple[Any, ...], int] = {}
    repeat_by_sample: dict[tuple[Any, ...], int] = {}
    condition_refs: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        prepared = validate_prepared_request(candidate.prepared_request)
        _validate_candidate_matches_prepared(candidate, prepared)
        repeat_context = _repeat_sample_context(candidate, prepared)
        repeat_key = (*repeat_context, candidate.repeat_id)
        if (
            repeat_key in sample_by_repeat
            and sample_by_repeat[repeat_key] != candidate.sample_slot_index
        ):
            raise ValueError("same repeat cannot map to multiple sample slots")
        sample_by_repeat[repeat_key] = candidate.sample_slot_index
        sample_key = (*repeat_context, candidate.sample_slot_index)
        if (
            sample_key in repeat_by_sample
            and repeat_by_sample[sample_key] != candidate.repeat_id
        ):
            raise ValueError("different repeats cannot share the same sample slot")
        repeat_by_sample[sample_key] = candidate.repeat_id
        slot_key = semantic_slot_key(
            case_record_digest=candidate.case_record_digest,
            planned_ai_unit_id=candidate.planned_ai_unit_id,
            sample_slot_index=candidate.sample_slot_index,
            replacement_slot=candidate.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest=candidate.prompt_profile_digest,
            prompt_admission_profile_digest=(
                prepared.prompt_admission_profile_digest
            ),
            plugin_version=prepared.plugin_version,
        )
        request_identity = (prepared.body_digest, prepared.inference_request_digest)
        previous_identity = request_identity_by_slot.setdefault(
            slot_key, request_identity
        )
        if previous_identity != request_identity:
            raise ValueError(
                "one semantic slot cannot preregister two inference/body digests"
            )

        provisional = ResponseBankInventoryRow(
            inventory_entry_id="",
            semantic_slot_key=slot_key,
            case_record_digest=candidate.case_record_digest,
            planned_ai_unit_id=candidate.planned_ai_unit_id,
            sample_slot_index=candidate.sample_slot_index,
            replacement_slot=candidate.replacement_slot,
            provider_config_digest=prepared.provider_config_digest,
            prompt_profile_digest=candidate.prompt_profile_digest,
            prompt_admission_profile_digest=(
                prepared.prompt_admission_profile_digest
            ),
            plugin_version=prepared.plugin_version,
            entry_id=prepared.entry_id,
            body_digest=prepared.body_digest,
            inference_request_digest=prepared.inference_request_digest,
        )
        computed_id = inventory_entry_id(provisional)
        if (
            candidate.precomputed_inventory_entry_id is not None
            and candidate.precomputed_inventory_entry_id != computed_id
        ):
            raise ValueError("precomputed inventory_entry_id mismatch")
        row = ResponseBankInventoryRow.from_dict(
            replace(provisional, inventory_entry_id=computed_id).to_dict()
        )
        previous_row = rows_by_slot.setdefault(slot_key, row)
        if previous_row != row:
            raise ValueError("one semantic slot maps to non-identical inventory rows")
        if (
            slot_key in terminal_kind_by_slot
            and terminal_kind_by_slot[slot_key] != candidate.terminal_kind
        ):
            raise ValueError("conflicting terminal_kind for semantic slot")
        terminal_kind_by_slot[slot_key] = candidate.terminal_kind
        _append_condition_ref(condition_refs, candidate, slot_key)

    rows = tuple(sorted(rows_by_slot.values(), key=lambda item: item.semantic_slot_key))
    terminal_values = tuple(terminal_kind_by_slot[row.semantic_slot_key] for row in rows)
    return SemanticInventoryPlan(
        schema_version="tokenshare.response_bank_semantic_inventory_plan.v1",
        inventory_digest=canonical_digest([row.to_dict() for row in rows]),
        rows=rows,
        condition_refs=tuple(condition_refs.values()),
        exp2_online_condition_refs=online_refs,
        max_concurrent_roots=1,
        expected_slot_count=len(rows),
        terminal_provider_failure_count=terminal_values.count("provider_failure"),
        terminal_success_count=terminal_values.count("success"),
        terminal_unacquired_count=terminal_values.count(None),
        # early-stop 是消费期观测，只记录而绝不参与 expected inventory 裁剪。
        observed_early_stop_slot_keys=tuple(observed_early_stop_slot_keys),
    )


def preflight_inventory_before_coordinator(
    *,
    plan: SemanticInventoryPlan,
    available_inventory_entry_ids: Sequence[str],
    coordinator_factory: Callable[[], Any],
) -> InventoryPreflightResult:
    """在 coordinator 构造前逐 slot 检查 inventory 完整性。"""

    available = set(available_inventory_entry_ids)
    missing_rows = tuple(
        row for row in plan.rows if row.inventory_entry_id not in available
    )
    if missing_rows:
        records = tuple(
            ResponseBankPreflightBlockedRecord(
                schema_version="tokenshare.response_bank_preflight_blocked.v1",
                inventory_entry_id=row.inventory_entry_id,
                semantic_slot_key=row.semantic_slot_key,
                reason="required semantic slot is missing",
            )
            for row in missing_rows
        )
        return InventoryPreflightResult(
            schema_version="tokenshare.response_bank_inventory_preflight.v1",
            status="blocked",
            blocked_records=records,
            protocol_engine_event_count=0,
            provider_call_count=0,
            coordinator_constructed=False,
        )
    coordinator = coordinator_factory()
    return InventoryPreflightResult(
        schema_version="tokenshare.response_bank_inventory_preflight.v1",
        status="ready",
        blocked_records=(),
        protocol_engine_event_count=0,
        provider_call_count=0,
        coordinator_constructed=True,
        coordinator=coordinator,
    )


def _append_condition_ref(
    refs: dict[str, dict[str, Any]],
    candidate: SemanticSlotCandidate,
    slot_key: str,
) -> None:
    value = refs.setdefault(
        candidate.condition_id,
        {
            "condition_id": candidate.condition_id,
            "condition_digest": candidate.condition_digest,
            "experiment_id": candidate.experiment_id,
            "worker_count": candidate.worker_count,
            "repeat_id": candidate.repeat_id,
            "fault_type": candidate.fault_type,
            "ablation_mode": candidate.ablation_mode,
            "semantic_slot_keys": [],
        },
    )
    if slot_key not in value["semantic_slot_keys"]:
        value["semantic_slot_keys"].append(slot_key)


def _validate_candidate_matches_prepared(
    candidate: SemanticSlotCandidate, prepared: PreparedOutboundRequest
) -> None:
    expected = (
        ("case_id", candidate.case_id, prepared.case_id),
        ("planned_ai_unit_id", candidate.planned_ai_unit_id, prepared.planned_ai_unit_id),
        ("sample_slot_index", candidate.sample_slot_index, prepared.sample_slot_index),
        ("replacement_slot", candidate.replacement_slot, prepared.replacement_slot),
    )
    for field_name, candidate_value, prepared_value in expected:
        if candidate_value != prepared_value:
            raise ValueError(f"candidate {field_name} does not match prepared request")
    if candidate.terminal_kind not in {None, "success", "provider_failure"}:
        raise ValueError("unsupported terminal kind")


def _repeat_sample_context(
    candidate: SemanticSlotCandidate,
    prepared: PreparedOutboundRequest,
) -> tuple[Any, ...]:
    # repeat/sample 与 replacement 独立；映射必须跨所有 replacement slot 成立。
    return (
        candidate.case_record_digest,
        candidate.planned_ai_unit_id,
        prepared.provider_config_digest,
        candidate.prompt_profile_digest,
        prepared.prompt_admission_profile_digest,
        prepared.plugin_version,
    )


def _validate_replacement_completeness(
    candidates: Sequence[SemanticSlotCandidate],
) -> None:
    slots_by_unit: dict[tuple[str, str, str, int], set[int]] = {}
    policy_by_unit: dict[tuple[str, str, str, int], tuple[str, str, str]] = {}
    for candidate in candidates:
        key = (
            candidate.condition_id,
            candidate.case_record_digest,
            candidate.planned_ai_unit_id,
            candidate.sample_slot_index,
        )
        slots_by_unit.setdefault(key, set()).add(candidate.replacement_slot)
        policy = (
            candidate.experiment_id,
            candidate.fault_type,
            candidate.ablation_mode,
        )
        previous = policy_by_unit.setdefault(key, policy)
        if previous != policy:
            raise ValueError("condition replacement policy drift")
    for key, actual_slots in slots_by_unit.items():
        experiment_id, fault_type, ablation_mode = policy_by_unit[key]
        expected_slots = set(
            replacement_slots_for(
                experiment_id=experiment_id,
                fault_type=fault_type,
                ablation_mode=ablation_mode,
            )
        )
        if actual_slots != expected_slots:
            raise ValueError(
                "incomplete replacement slots for preregistered semantic unit"
            )


def _exp2_online_refs(
    conditions: Sequence[Mapping[str, Any] | Any],
) -> tuple[dict[str, Any], ...]:
    if not conditions:
        return ()
    refs = tuple(_object_dict(item) for item in conditions)
    expected_axes = tuple(
        (worker, position, case_id, selection_index)
        for worker in _EXP2_WORKERS
        for position, case_id, selection_index in _EXP2_CASES
    )
    actual = tuple(
        (
            int(item["worker_count"]),
            str(item["case_position"]),
            str(item["case_id"]),
            int(item["active_selection_index"]),
        )
        for item in refs
    )
    if actual != expected_axes or len(refs) != 24:
        raise ValueError("Experiment 2 frozen 24-condition sequence drift")
    for item, (worker, position, case_id, selection_index) in zip(
        refs, expected_axes, strict=True
    ):
        expected_body = {
            "condition_id": f"epd027_exp2_online_w{worker}_{position}_r0",
            "experiment_id": "exp2_online_concurrency_check",
            "evidence_class": "online_real_provider",
            "worker_count": worker,
            "case_position": position,
            "case_id": case_id,
            "paper_difficulty": "hard",
            "active_selection_index": selection_index,
            "repeat_id": 0,
            "split_profile_id": "factorization.exp2_contiguous_20way.v1",
            "planned_first_ai_units": 20,
            "provider_calls_upper": 20,
            "max_concurrent_roots": 1,
        }
        body = {key: value for key, value in item.items() if key != "condition_digest"}
        if item.get("condition_digest") != canonical_digest(body):
            raise ValueError("Experiment 2 condition digest drift")
        if body != expected_body:
            raise ValueError("Experiment 2 frozen condition axes drift")
    return refs


def _object_dict(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError("condition reference must be a mapping or dataclass")


__all__ = [
    "InventoryPreflightResult",
    "ResponseBankPreflightBlockedRecord",
    "SemanticInventoryPlan",
    "SemanticSlotCandidate",
    "build_semantic_inventory",
    "preflight_inventory_before_coordinator",
    "replacement_slots_for",
]
