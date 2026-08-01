"""Experiment-layer ablation controls and evidence summaries.

该模块只描述论文实验如何关闭机制并统计证据，不改变协议 core、
插件 verifier/checker 或 executor 的默认安全行为。所有 ablation 都要求
先存在真实 provider attempt evidence，再在实验边界解释关闭机制后的影响。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from tokenshare.experiments.paper_models import (
    JsonObject,
    PaperAttemptResult,
    digest_json,
)
from tokenshare.local_runtime import (
    GateDirective,
    MergeContext,
    NoOpRuntimeHooks,
    ParserContext,
    ProtocolMechanismPolicy,
    RecoveryContext,
    RuntimeHookObservationV1,
    VerificationContext,
    build_experiment_ablation_gate_applied_observation,
)


ABLATION_SCOPE = "experiment_boundary"


class PaperAblationMode(str, Enum):
    FULL = "FULL"
    NO_PARSER_POLICY = "NO_PARSER_POLICY"
    NO_VERIFICATION = "NO_VERIFICATION"
    NO_REQUEUE = "NO_REQUEUE"
    NO_MERGE_GATE = "NO_MERGE_GATE"
    NO_SLOT_INTEGRITY = "NO_SLOT_INTEGRITY"


@dataclass(frozen=True, kw_only=True)
class PaperAblationProfile:
    mode: PaperAblationMode | str
    disabled_mechanisms: tuple[str, ...]
    expected_risk_flags: tuple[str, ...]
    scope: str = ABLATION_SCOPE
    requires_provider_attempt_before_ablation: bool = True
    system_default_behavior_changed: bool = False
    schema_version: str = "tokenshare.paper_ablation_profile.v1"

    def __post_init__(self) -> None:
        PaperAblationMode(self.mode)
        if self.scope != ABLATION_SCOPE:
            raise ValueError("ablation scope must be experiment_boundary")
        if self.requires_provider_attempt_before_ablation is not True:
            raise ValueError("requires_provider_attempt_before_ablation must be true")
        if self.system_default_behavior_changed is not False:
            raise ValueError("system_default_behavior_changed must be false")
        if not isinstance(self.disabled_mechanisms, tuple):
            object.__setattr__(
                self,
                "disabled_mechanisms",
                tuple(self.disabled_mechanisms),
            )
        if not isinstance(self.expected_risk_flags, tuple):
            object.__setattr__(
                self,
                "expected_risk_flags",
                tuple(self.expected_risk_flags),
            )
        for field_name, values in (
            ("disabled_mechanisms", self.disabled_mechanisms),
            ("expected_risk_flags", self.expected_risk_flags),
        ):
            for value in values:
                _require_non_empty(field_name, value)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "mode": _enum_value(self.mode),
            "disabled_mechanisms": list(self.disabled_mechanisms),
            "expected_risk_flags": list(self.expected_risk_flags),
            "scope": self.scope,
            "requires_provider_attempt_before_ablation": (
                self.requires_provider_attempt_before_ablation
            ),
            "system_default_behavior_changed": self.system_default_behavior_changed,
        }


@dataclass(frozen=True, kw_only=True)
class AblationAttemptCoverage:
    dependency_graph_digest: str
    expected_ai_unit_count: int
    covered_ai_unit_count: int
    missing_unit_ids: tuple[str, ...]
    invalid_attempts: tuple[JsonObject, ...]
    unit_attempts: tuple[JsonObject, ...]
    can_apply_ablation: bool
    reasons: tuple[str, ...]
    schema_version: str = "tokenshare.paper_ablation_attempt_coverage.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "dependency_graph_digest": self.dependency_graph_digest,
            "expected_ai_unit_count": self.expected_ai_unit_count,
            "covered_ai_unit_count": self.covered_ai_unit_count,
            "missing_unit_ids": list(self.missing_unit_ids),
            "invalid_attempts": [dict(item) for item in self.invalid_attempts],
            "unit_attempts": [dict(item) for item in self.unit_attempts],
            "can_apply_ablation": self.can_apply_ablation,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True)
class PaperAblationEvidenceRecord:
    condition_id: str
    repeat_id: int
    run_id: str
    task_id: str
    unit_id: str
    attempt_id: str
    ablation_mode: PaperAblationMode | str
    dependency_path: tuple[str, ...]
    disabled_mechanisms: tuple[str, ...]
    canonical_pollution: bool
    premature_merge: bool
    blocked_root: bool
    settlement_blocked: bool
    slot_integrity_violation: bool
    event_refs: tuple[JsonObject, ...] | list[JsonObject]
    artifact_refs: tuple[JsonObject, ...] | list[JsonObject]
    schema_version: str = "tokenshare.paper_ablation_evidence.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "condition_id",
            "run_id",
            "task_id",
            "unit_id",
            "attempt_id",
        ):
            _require_non_empty(field_name, getattr(self, field_name))
        if self.repeat_id < 0:
            raise ValueError("repeat_id must be non-negative")
        PaperAblationMode(self.ablation_mode)
        if not isinstance(self.dependency_path, tuple):
            object.__setattr__(self, "dependency_path", tuple(self.dependency_path))
        if not isinstance(self.disabled_mechanisms, tuple):
            object.__setattr__(
                self,
                "disabled_mechanisms",
                tuple(self.disabled_mechanisms),
            )
        if not isinstance(self.event_refs, tuple):
            object.__setattr__(self, "event_refs", tuple(self.event_refs))
        if not isinstance(self.artifact_refs, tuple):
            object.__setattr__(self, "artifact_refs", tuple(self.artifact_refs))
        for value in self.dependency_path:
            _require_non_empty("dependency_path", value)
        for value in self.disabled_mechanisms:
            _require_non_empty("disabled_mechanisms", value)
        if not self.event_refs:
            raise ValueError("event_refs are required for evidence-derived ablation metrics")
        if not self.artifact_refs:
            raise ValueError(
                "artifact_refs are required for evidence-derived ablation metrics"
            )
        for field_name in (
            "canonical_pollution",
            "premature_merge",
            "blocked_root",
            "settlement_blocked",
            "slot_integrity_violation",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean")
        _validate_event_refs(self.event_refs)
        _validate_artifact_refs(self.artifact_refs)

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "ablation_mode": _enum_value(self.ablation_mode),
            "dependency_path": list(self.dependency_path),
            "disabled_mechanisms": list(self.disabled_mechanisms),
            "canonical_pollution": self.canonical_pollution,
            "premature_merge": self.premature_merge,
            "blocked_root": self.blocked_root,
            "settlement_blocked": self.settlement_blocked,
            "slot_integrity_violation": self.slot_integrity_violation,
            "event_refs": [dict(item) for item in self.event_refs],
            "artifact_refs": [dict(item) for item in self.artifact_refs],
        }

    def _has_failure_metric(self) -> bool:
        return any(
            (
                self.canonical_pollution,
                self.premature_merge,
                self.blocked_root,
                self.settlement_blocked,
                self.slot_integrity_violation,
            )
        )


@dataclass(frozen=True, kw_only=True)
class PaperAblationSummary:
    record_count: int
    canonical_pollution_count: int
    premature_merge_count: int
    blocked_root_count: int
    settlement_blocked_count: int
    slot_integrity_violation_count: int
    affected_unit_ids: tuple[str, ...]
    by_ablation_mode: dict[str, JsonObject]
    schema_version: str = "tokenshare.paper_ablation_summary.v1"

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "record_count": self.record_count,
            "canonical_pollution_count": self.canonical_pollution_count,
            "premature_merge_count": self.premature_merge_count,
            "blocked_root_count": self.blocked_root_count,
            "settlement_blocked_count": self.settlement_blocked_count,
            "slot_integrity_violation_count": self.slot_integrity_violation_count,
            "affected_unit_ids": list(self.affected_unit_ids),
            "by_ablation_mode": {
                mode: dict(summary)
                for mode, summary in sorted(self.by_ablation_mode.items())
            },
        }


@dataclass(frozen=True, kw_only=True)
class PaperAblationRuntimeControls:
    """一个 ablation mode 对应的稳定 runtime policy/hooks。"""

    mode: PaperAblationMode
    mechanism_policy: ProtocolMechanismPolicy
    hooks: NoOpRuntimeHooks | "PaperAblationRuntimeHooks"


class PaperAblationRuntimeHooks(NoOpRuntimeHooks):
    """只在声明的稳定 gate 返回 directive，并保存实验观察。"""

    def __init__(self, mode: PaperAblationMode | str) -> None:
        self.mode = PaperAblationMode(mode)
        self._events: list[RuntimeHookObservationV1] = []

    @property
    def events(self) -> tuple[RuntimeHookObservationV1, ...]:
        return tuple(self._events)

    def before_parser(self, context: ParserContext) -> GateDirective | None:
        if self.mode != PaperAblationMode.NO_PARSER_POLICY:
            return None
        return self._directive(
            mechanism="parser_policy",
            protocol_event_refs=(),
            artifact_refs=_submission_artifact_refs(context.submission),
            hook_input=_hook_context_identity(context),
            bypass=True,
        )

    def before_verification(
        self,
        context: VerificationContext,
    ) -> GateDirective | None:
        if self.mode != PaperAblationMode.NO_VERIFICATION:
            return None
        return self._directive(
            mechanism="verification",
            protocol_event_refs=(),
            artifact_refs=_submission_artifact_refs(context.submission),
            hook_input=_hook_context_identity(context),
            bypass=True,
        )

    def before_requeue(self, context: RecoveryContext) -> GateDirective | None:
        if self.mode != PaperAblationMode.NO_REQUEUE:
            return None
        return self._directive(
            mechanism="requeue",
            protocol_event_refs=context.recovery_event_refs,
            hook_input=_hook_context_identity(context),
            stop=True,
        )

    def before_merge(self, context: MergeContext) -> GateDirective | None:
        if (
            self.mode == PaperAblationMode.NO_MERGE_GATE
            and not context.gate_satisfied
        ):
            return self._directive(
                mechanism="merge_gate",
                protocol_event_refs=context.protocol_event_refs,
                hook_input=_hook_context_identity(context),
                bypass=True,
            )
        if (
            self.mode == PaperAblationMode.NO_SLOT_INTEGRITY
            and context.gate_satisfied
        ):
            return self._directive(
                mechanism="slot_integrity",
                protocol_event_refs=context.protocol_event_refs,
                hook_input=_hook_context_identity(context),
                bypass=True,
            )
        return None

    def _directive(
        self,
        *,
        mechanism: str,
        protocol_event_refs: tuple[JsonObject, ...],
        artifact_refs: tuple[JsonObject, ...] = (),
        hook_input: JsonObject,
        bypass: bool = False,
        stop: bool = False,
    ) -> GateDirective:
        event = build_experiment_ablation_gate_applied_observation(
            ablation_mode=self.mode.value,
            disabled_mechanism=mechanism,
            protocol_event_refs=protocol_event_refs,
            artifact_refs=artifact_refs,
            hook_input=hook_input,
            hook_result={"bypass": bypass, "stop": stop},
        )
        self._events.append(event)
        return GateDirective(
            bypass=bypass,
            stop=stop,
            experiment_records=(event,),
        )


def _hook_context_identity(context: object) -> JsonObject:
    body: JsonObject = {}
    for owner_name in ("unit", "attempt", "lease"):
        owner = getattr(context, owner_name, None)
        for field_name in ("task_id", "unit_id", "attempt_id", "lease_id"):
            value = getattr(owner, field_name, None)
            if isinstance(value, str) and value:
                body[field_name] = value
    for field_name in ("trigger", "gate_satisfied"):
        value = getattr(context, field_name, None)
        if isinstance(value, (str, bool)):
            body[field_name] = value
    required = getattr(context, "required_child_unit_ids", None)
    if isinstance(required, tuple):
        body["required_child_unit_ids"] = list(required)
    return body


def _submission_artifact_refs(submission: object) -> tuple[JsonObject, ...]:
    refs: list[JsonObject] = []
    for field_name in (
        "raw_output_ref",
        "parsed_output_ref",
        "parse_failure_ref",
        "log_ref",
        "provenance_ref",
    ):
        value = getattr(submission, field_name, None)
        if value is not None and callable(getattr(value, "to_dict", None)):
            refs.append(dict(value.to_dict()))
    candidates = getattr(submission, "candidate_output_refs", None)
    if isinstance(candidates, dict):
        refs.extend(
            dict(value.to_dict())
            for value in candidates.values()
            if callable(getattr(value, "to_dict", None))
        )
    return tuple(refs)


def ablation_profile_for_mode(
    mode: PaperAblationMode | str,
) -> PaperAblationProfile:
    normalized = PaperAblationMode(mode)
    disabled, risks = _PROFILE_MAP[normalized]
    return PaperAblationProfile(
        mode=normalized,
        disabled_mechanisms=disabled,
        expected_risk_flags=risks,
    )


def ablation_modes() -> tuple[PaperAblationMode, ...]:
    """返回论文 Experiment 4 的五个正式模式。

    ``NO_SLOT_INTEGRITY`` 只保留为历史夹具兼容枚举，不进入正式条件、
    预算或报告矩阵。
    """

    return (
        PaperAblationMode.FULL,
        PaperAblationMode.NO_VERIFICATION,
        PaperAblationMode.NO_PARSER_POLICY,
        PaperAblationMode.NO_REQUEUE,
        PaperAblationMode.NO_MERGE_GATE,
    )


def runtime_controls_for_mode(
    mode: PaperAblationMode | str,
) -> PaperAblationRuntimeControls:
    """关闭且只关闭一个 runtime mechanism；FULL 保持 NoOp。"""

    normalized = PaperAblationMode(mode)
    disabled_field = {
        PaperAblationMode.FULL: None,
        PaperAblationMode.NO_PARSER_POLICY: "parser_policy_enabled",
        PaperAblationMode.NO_VERIFICATION: "verification_enabled",
        PaperAblationMode.NO_REQUEUE: "replacement_attempts_allowed",
        PaperAblationMode.NO_MERGE_GATE: "merge_gate_enabled",
        PaperAblationMode.NO_SLOT_INTEGRITY: "slot_integrity_enabled",
    }[normalized]
    values = {
        "parser_policy_enabled": True,
        "verification_enabled": True,
        "replacement_attempts_allowed": True,
        "merge_gate_enabled": True,
        "slot_integrity_enabled": True,
    }
    if disabled_field is not None:
        values[disabled_field] = False
    return PaperAblationRuntimeControls(
        mode=normalized,
        mechanism_policy=ProtocolMechanismPolicy(**values),
        hooks=(
            NoOpRuntimeHooks()
            if normalized == PaperAblationMode.FULL
            else PaperAblationRuntimeHooks(normalized)
        ),
    )


def validate_ablation_attempt_coverage(
    *,
    dependency_graph: JsonObject,
    attempts: tuple[PaperAttemptResult | JsonObject, ...]
    | list[PaperAttemptResult | JsonObject],
    artifact_inventory: tuple[JsonObject, ...] | list[JsonObject] | dict[str, JsonObject],
) -> AblationAttemptCoverage:
    graph_units = _unit_ids_from_graph(dependency_graph)
    graph_digest = str(dependency_graph.get("graph_digest") or "")
    if not graph_digest.startswith("sha256:"):
        raise ValueError("dependency_graph must include graph_digest")
    if _dependency_graph_digest(dependency_graph) != graph_digest:
        raise ValueError("dependency_graph graph_digest does not match content")
    inventory = _artifact_inventory(artifact_inventory)

    valid_by_unit: dict[str, JsonObject] = {}
    invalid_attempts: list[JsonObject] = []
    reasons: list[str] = []
    for attempt in attempts:
        body = _attempt_body(attempt)
        unit_id = str(body.get("unit_id") or "")
        attempt_id = str(body.get("attempt_id") or "")
        attempt_reasons = _provider_attempt_reasons(body, artifact_inventory=inventory)
        if not unit_id:
            attempt_reasons.append("missing_unit_id")
        if not attempt_id:
            attempt_reasons.append("missing_attempt_id")
        if unit_id and unit_id not in graph_units:
            attempt_reasons.append(f"unknown_unit:{unit_id}")
        if attempt_reasons:
            invalid_attempts.append(
                {
                    "unit_id": unit_id,
                    "attempt_id": attempt_id,
                    "reasons": attempt_reasons,
                }
            )
            reasons.extend(
                reason if reason.startswith("missing_provider_attempt:")
                else f"invalid_provider_attempt:{unit_id or 'unknown'}:{reason}"
                for reason in attempt_reasons
            )
            continue
        valid_by_unit.setdefault(
            unit_id,
            {
                "unit_id": unit_id,
                "attempt_id": attempt_id,
                "provider_attempt_index": body["provider_attempt_index"],
                "raw_output_ref": dict(body["raw_output_ref"]),
                "provenance_ref": dict(body["provenance_ref"]),
                "usage_ref": dict(body["usage_ref"]),
            },
        )

    missing_unit_ids = tuple(
        unit_id for unit_id in graph_units if unit_id not in valid_by_unit
    )
    reasons.extend(
        f"missing_provider_attempt:{unit_id}"
        for unit_id in missing_unit_ids
    )
    return AblationAttemptCoverage(
        dependency_graph_digest=graph_digest,
        expected_ai_unit_count=len(graph_units),
        covered_ai_unit_count=len(valid_by_unit),
        missing_unit_ids=missing_unit_ids,
        invalid_attempts=tuple(invalid_attempts),
        unit_attempts=tuple(valid_by_unit[unit_id] for unit_id in sorted(valid_by_unit)),
        can_apply_ablation=not missing_unit_ids and not invalid_attempts,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def summarize_ablation_evidence(
    records: tuple[PaperAblationEvidenceRecord | JsonObject, ...]
    | list[PaperAblationEvidenceRecord | JsonObject],
) -> PaperAblationSummary:
    bodies = tuple(_record_body(record) for record in records)
    by_mode: dict[str, JsonObject] = {
        mode.value: _empty_mode_summary()
        for mode in PaperAblationMode
    }
    for body in bodies:
        mode = PaperAblationMode(body["ablation_mode"]).value
        summary = by_mode[mode]
        summary["record_count"] += 1
        _add_flags(summary, body)
    return PaperAblationSummary(
        record_count=len(bodies),
        canonical_pollution_count=sum(1 for body in bodies if body["canonical_pollution"]),
        premature_merge_count=sum(1 for body in bodies if body["premature_merge"]),
        blocked_root_count=sum(1 for body in bodies if body["blocked_root"]),
        settlement_blocked_count=sum(1 for body in bodies if body["settlement_blocked"]),
        slot_integrity_violation_count=sum(
            1 for body in bodies if body["slot_integrity_violation"]
        ),
        affected_unit_ids=tuple(sorted({str(body["unit_id"]) for body in bodies})),
        by_ablation_mode=by_mode,
    )


_PROFILE_MAP: dict[PaperAblationMode, tuple[tuple[str, ...], tuple[str, ...]]] = {
    PaperAblationMode.FULL: ((), ()),
    PaperAblationMode.NO_PARSER_POLICY: (
        ("parser",),
        ("parse_failure_escape", "invalid_output_escape"),
    ),
    PaperAblationMode.NO_VERIFICATION: (
        ("verification",),
        ("canonical_pollution",),
    ),
    PaperAblationMode.NO_REQUEUE: (
        ("requeue",),
        ("blocked_root",),
    ),
    PaperAblationMode.NO_MERGE_GATE: (
        ("merge_gate",),
        ("premature_merge",),
    ),
    PaperAblationMode.NO_SLOT_INTEGRITY: (
        ("slot_integrity",),
        ("slot_integrity_violation", "settlement_blocked"),
    ),
}


def _provider_attempt_reasons(
    attempt: JsonObject,
    *,
    artifact_inventory: dict[str, JsonObject],
) -> list[str]:
    reasons: list[str] = []
    for field_name in ("provider", "model", "entry_id"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"missing_{field_name}")
    if not _non_negative_int(attempt.get("provider_attempt_index")):
        reasons.append("missing_provider_attempt_index")
    for field_name in ("started_at", "ended_at"):
        if not isinstance(attempt.get(field_name), str) or not attempt.get(field_name):
            reasons.append(f"missing_{field_name}")
    for field_name in ("latency_ms", "prompt_tokens", "completion_tokens"):
        if not _non_negative_int(attempt.get(field_name)):
            reasons.append(f"missing_{field_name}")
    if not _positive_int(attempt.get("total_tokens")):
        reasons.append("missing_total_tokens")
    if (
        _non_negative_int(attempt.get("prompt_tokens"))
        and _non_negative_int(attempt.get("completion_tokens"))
        and _positive_int(attempt.get("total_tokens"))
        and attempt["prompt_tokens"] + attempt["completion_tokens"] != attempt["total_tokens"]
    ):
        reasons.append("token_breakdown_mismatch")
    if not _non_negative_number(attempt.get("cost_estimate")):
        reasons.append("missing_cost_estimate")
    if attempt.get("paper_eligible") is not True:
        reasons.append("attempt_not_paper_eligible")
    _check_required_artifact_ref(
        attempt,
        "request_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"ExecutionRequest", "PromptPackage"},
        allowed_source_kinds=None,
    )
    _check_required_artifact_ref(
        attempt,
        "raw_output_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"RawModelOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "provenance_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"AIProviderCallProvenance"},
        allowed_source_kinds={"ai_api_executor"},
    )
    _check_required_artifact_ref(
        attempt,
        "usage_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"AIUsageSummary", "UsageSummary"},
        allowed_source_kinds=None,
    )
    has_parsed = _check_optional_artifact_ref(
        attempt,
        "parsed_output_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"ParsedModelOutput", "CandidateOutput"},
        allowed_source_kinds={"ai_api_executor"},
    )
    has_parse_failure = _check_optional_artifact_ref(
        attempt,
        "parse_failure_ref",
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types={"ParseFailureReport"},
        allowed_source_kinds={"ai_api_executor"},
    )
    if not (has_parsed or has_parse_failure):
        reasons.append("missing_parsed_output_or_parse_failure_ref")
    return reasons


def _check_required_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_optional_artifact_ref(
    attempt: JsonObject,
    field_name: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    ref = attempt.get(field_name)
    if ref is None:
        return False
    if not _has_complete_artifact_ref(ref):
        reasons.append(f"missing_{field_name}")
        return False
    return _check_artifact_evidence(
        ref,
        field_name,
        reasons,
        artifact_inventory=artifact_inventory,
        allowed_types=allowed_types,
        allowed_source_kinds=allowed_source_kinds,
    )


def _check_artifact_evidence(
    ref: JsonObject,
    field_name: str,
    reasons: list[str],
    *,
    artifact_inventory: dict[str, JsonObject],
    allowed_types: set[str],
    allowed_source_kinds: set[str] | None,
) -> bool:
    manifest = artifact_inventory.get(str(ref["artifact_id"]))
    verified = _ref_verified(ref, artifact_inventory)
    if not verified:
        reasons.append(f"unverified_{field_name}")
        return False
    if manifest is not None and manifest.get("artifact_type") not in allowed_types:
        reasons.append(f"invalid_{field_name}_type")
        verified = False
    if allowed_source_kinds is not None:
        source = manifest.get("source") if manifest is not None else None
        if not isinstance(source, dict) or source.get("kind") not in allowed_source_kinds:
            reasons.append(f"invalid_{field_name}_source")
            verified = False
    return verified


def _artifact_inventory(
    manifests: tuple[JsonObject, ...] | list[JsonObject] | dict[str, JsonObject],
) -> dict[str, JsonObject]:
    candidates = manifests.values() if isinstance(manifests, dict) else manifests
    inventory: dict[str, JsonObject] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and _has_complete_artifact_ref(candidate):
            inventory[str(candidate["artifact_id"])] = dict(candidate)
    return inventory


def _ref_verified(ref: JsonObject, inventory: dict[str, JsonObject]) -> bool:
    if not _has_complete_artifact_ref(ref):
        return False
    manifest = inventory.get(str(ref["artifact_id"]))
    if manifest is None:
        return False
    return (
        manifest.get("content_hash") == ref.get("content_hash")
        and manifest.get("artifact_type") == ref.get("artifact_type")
        and manifest.get("artifact_schema_id") == ref.get("artifact_schema_id")
    )


def _has_complete_artifact_ref(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = (
        "artifact_id",
        "artifact_type",
        "content_hash",
        "artifact_schema_id",
        "source",
    )
    if not all(value.get(field_name) for field_name in required):
        return False
    return (
        isinstance(value.get("source"), dict)
        and isinstance(value.get("content_hash"), str)
        and value["content_hash"].startswith("sha256:")
    )


def _validate_event_refs(event_refs: tuple[JsonObject, ...]) -> None:
    for event_ref in event_refs:
        if not isinstance(event_ref, dict) or not event_ref.get("event_id"):
            raise ValueError("event_refs must contain event_id")
        if not event_ref.get("event_type"):
            raise ValueError("event_refs must contain event_type")


def _validate_artifact_refs(artifact_refs: tuple[JsonObject, ...]) -> None:
    for artifact_ref in artifact_refs:
        if not _has_complete_artifact_ref(artifact_ref):
            raise ValueError("artifact_refs must contain complete artifact refs")


def _unit_ids_from_graph(dependency_graph: JsonObject) -> tuple[str, ...]:
    unit_ids = dependency_graph.get("unit_ids")
    if not isinstance(unit_ids, list) or not unit_ids:
        raise ValueError("dependency_graph must include unit_ids")
    normalized = tuple(str(unit_id) for unit_id in unit_ids)
    if any(not unit_id for unit_id in normalized):
        raise ValueError("dependency_graph unit_ids must be non-empty")
    return normalized


def _dependency_graph_digest(dependency_graph: JsonObject) -> str:
    body = {
        key: value
        for key, value in dependency_graph.items()
        if key != "graph_digest"
    }
    return digest_json(body)


def _attempt_body(attempt: PaperAttemptResult | JsonObject) -> JsonObject:
    if isinstance(attempt, PaperAttemptResult):
        return attempt.to_dict()
    if not isinstance(attempt, dict):
        raise ValueError("attempt must be a PaperAttemptResult or object")
    return dict(attempt)


def _record_body(record: PaperAblationEvidenceRecord | JsonObject) -> JsonObject:
    if isinstance(record, PaperAblationEvidenceRecord):
        return record.to_dict()
    if not isinstance(record, dict):
        raise ValueError("ablation evidence record must be an object")
    return PaperAblationEvidenceRecord(
        condition_id=str(record.get("condition_id") or ""),
        repeat_id=int(record.get("repeat_id", -1)),
        run_id=str(record.get("run_id") or ""),
        task_id=str(record.get("task_id") or ""),
        unit_id=str(record.get("unit_id") or ""),
        attempt_id=str(record.get("attempt_id") or ""),
        ablation_mode=record.get("ablation_mode"),
        dependency_path=tuple(record.get("dependency_path") or ()),
        disabled_mechanisms=tuple(record.get("disabled_mechanisms") or ()),
        canonical_pollution=_required_bool_from_mapping(
            record,
            "canonical_pollution",
        ),
        premature_merge=_required_bool_from_mapping(record, "premature_merge"),
        blocked_root=_required_bool_from_mapping(record, "blocked_root"),
        settlement_blocked=_required_bool_from_mapping(record, "settlement_blocked"),
        slot_integrity_violation=_required_bool_from_mapping(
            record,
            "slot_integrity_violation",
        ),
        event_refs=tuple(record.get("event_refs") or ()),
        artifact_refs=tuple(record.get("artifact_refs") or ()),
    ).to_dict()


def _empty_mode_summary() -> JsonObject:
    return {
        "record_count": 0,
        "canonical_pollution_count": 0,
        "premature_merge_count": 0,
        "blocked_root_count": 0,
        "settlement_blocked_count": 0,
        "slot_integrity_violation_count": 0,
    }


def _add_flags(summary: JsonObject, body: JsonObject) -> None:
    for flag_name in (
        "canonical_pollution",
        "premature_merge",
        "blocked_root",
        "settlement_blocked",
        "slot_integrity_violation",
    ):
        if body[flag_name]:
            summary[f"{flag_name}_count"] += 1


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _required_bool_from_mapping(value: JsonObject, field_name: str) -> bool:
    item = value.get(field_name)
    if not isinstance(item, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return item


def _non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _non_negative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and value >= 0
    )


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
