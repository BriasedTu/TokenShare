"""把真实 metric computation trace 与 persisted lineage source index 逐 cell join。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tokenshare.experiments.paper_formal_evidence import (
    LineageSourceIndex,
    LineageSourceRecord,
)
from tokenshare.experiments.paper_metric_contract import (
    MemberDecision,
    MetricComputationTrace,
    MetricObservationBundle,
    PaperMetricContract,
    VerifiedMetricObservation,
    metric_cell_identity_from_membership,
    recompute_metric,
)
from tokenshare.experiments.paper_metric_registry import MetricTableDraft
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    ExternalBankObjectLocator,
    LedgerEventIdentitySnapshot,
)


@dataclass(frozen=True, kw_only=True)
class PaperMetricObservation:
    observation_id: str
    observation_digest: str
    metric_contract_id: str
    metric_contract_digest: str
    pipeline_profile_id: str
    pipeline_profile_digest: str
    source_index_id: str
    source_index_digest: str
    table_id: str
    row_kind: str
    row_key: Mapping[str, Any]
    row_identity_digest: str
    column_id: str
    metric_id: str
    formula_id: str
    numeric_value: Decimal | int | None
    null_reason: str | None
    publish_blocked: bool
    numerator_membership_ids: tuple[str, ...]
    denominator_inventory_ids: tuple[str, ...]
    audit_denominator_member_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    blocked_member_ids: tuple[str, ...]
    exclusion_reasons: Mapping[str, str]
    blocked_reasons: tuple[str, ...]
    row_facts: Mapping[str, Any]
    member_facts_by_id: Mapping[str, Mapping[str, Any]]
    verified_observations: tuple[VerifiedMetricObservation, ...]
    required_current_provider_roles: tuple[str, ...]
    required_source_bank_roles: tuple[str, ...]
    covered_current_provider_roles: tuple[str, ...]
    covered_source_bank_roles: tuple[str, ...]
    not_applicable_evidence_roles: tuple[str, ...]
    evidence_class: str
    direct_result_refs: tuple[Mapping[str, Any], ...]
    current_task_attempt_event_refs: tuple[LedgerEventIdentitySnapshot, ...]
    parser_verifier_checker_canonical_refs: tuple[
        ArtifactIdentitySnapshot | LedgerEventIdentitySnapshot, ...
    ]
    ledger_refs: tuple[LedgerEventIdentitySnapshot, ...]
    current_provider_object_refs: tuple[ArtifactIdentitySnapshot, ...]
    source_bank_object_locators: tuple[ExternalBankObjectLocator, ...]
    current_trace_wrappers: tuple[Mapping[str, Any], ...]
    trace_source_bindings: tuple[Mapping[str, Any], ...]
    paper_eligible: bool = False
    schema_version: str = "tokenshare.paper_metric_observation.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "tokenshare.paper_metric_observation.v1":
            raise ValueError("unsupported paper metric observation schema")
        for field_name in (
            "observation_id",
            "observation_digest",
            "metric_contract_digest",
            "pipeline_profile_digest",
            "source_index_id",
            "source_index_digest",
            "row_identity_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "metric_contract_id",
            "pipeline_profile_id",
            "table_id",
            "row_kind",
            "column_id",
            "metric_id",
            "formula_id",
            "evidence_class",
        ):
            _require_string(getattr(self, field_name), field_name)
        if type(self.publish_blocked) is not bool or self.paper_eligible is not False:
            raise ValueError("metric observation publication gates are invalid")
        if self.numeric_value is not None and self.null_reason is not None:
            raise ValueError("non-null metric observation cannot carry null reason")
        object.__setattr__(self, "row_key", MappingProxyType(dict(self.row_key)))
        object.__setattr__(self, "row_facts", MappingProxyType(dict(self.row_facts)))
        object.__setattr__(
            self,
            "member_facts_by_id",
            MappingProxyType(
                {
                    member_id: MappingProxyType(dict(facts))
                    for member_id, facts in self.member_facts_by_id.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "exclusion_reasons",
            MappingProxyType(dict(self.exclusion_reasons)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_observation_body(self),
            "observation_digest": self.observation_digest,
        }


@dataclass(frozen=True, kw_only=True)
class MetricObservationPublication:
    observations: tuple[PaperMetricObservation, ...]
    numeric_draft_cell_count: int
    blocked_table_ids: tuple[str, ...]
    observations_digest: str
    schema_version: str = "tokenshare.paper_metric_observation_publication.v1"

    @property
    def numeric_cell_coverage(self) -> Decimal:
        if self.numeric_draft_cell_count == 0:
            return Decimal(1)
        return Decimal(len(self.observations)) / Decimal(self.numeric_draft_cell_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "numeric_draft_cell_count": self.numeric_draft_cell_count,
            "observation_count": len(self.observations),
            "numeric_cell_coverage": str(self.numeric_cell_coverage),
            "blocked_table_ids": list(self.blocked_table_ids),
            "observations_digest": self.observations_digest,
        }


def materialize_metric_observations(
    *,
    contract: PaperMetricContract,
    table_drafts: Sequence[MetricTableDraft],
    computation_traces: Sequence[MetricComputationTrace],
    source_index: LineageSourceIndex,
    expected_source_input_identity_digest: str,
) -> MetricObservationPublication:
    """每个 draft numeric cell 恰好消费一个真实 evaluator trace。"""

    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if not isinstance(source_index, LineageSourceIndex):
        raise TypeError("source_index must be LineageSourceIndex")
    if source_index.input_identity_digest != expected_source_input_identity_digest:
        raise ValueError("lineage source input identity mismatch")
    trace_index: dict[
        tuple[str, str, str, str, str], MetricComputationTrace
    ] = {}
    for trace in computation_traces:
        if not isinstance(trace, MetricComputationTrace):
            raise TypeError("computation traces must be typed")
        if (
            trace.contract_id != contract.contract_id
            or trace.contract_digest != contract.contract_digest
            or trace.pipeline_profile_id != contract.pipeline_profile_id
            or trace.pipeline_profile_digest != contract.pipeline_profile_digest
        ):
            raise ValueError("metric computation trace contract identity mismatch")
        key = (
            trace.row_identity_digest,
            trace.table_id,
            trace.row_kind,
            trace.metric_id,
            trace.cell_identity_digest,
        )
        if key in trace_index:
            raise ValueError("duplicate metric computation trace cell identity")
        trace_index[key] = trace

    observations: list[PaperMetricObservation] = []
    numeric_count = 0
    for draft in table_drafts:
        if not isinstance(draft, MetricTableDraft):
            raise TypeError("table drafts must contain MetricTableDraft")
        for row in draft.rows:
            row_key = _row_key(row.source_row)
            bound_row_identity: str | None = None
            for cell in row.cells:
                numeric_count += 1
                metric_id = str(getattr(cell, "metric_id", ""))
                marker = metric_cell_identity_from_membership(
                    getattr(cell, "membership", None)
                )
                if marker is None:
                    raise ValueError("numeric draft cell has no canonical cell identity")
                row_identity_digest, cell_identity_digest = marker
                if bound_row_identity is None:
                    bound_row_identity = row_identity_digest
                elif bound_row_identity != row_identity_digest:
                    raise ValueError("numeric draft row crosses canonical row identity")
                key = (
                    row_identity_digest,
                    draft.table_id,
                    row.row_kind,
                    metric_id,
                    cell_identity_digest,
                )
                trace = trace_index.pop(key, None)
                if trace is None:
                    raise ValueError("numeric draft cell identity has no exact computation trace")
                observations.append(
                    _materialize_one(
                        contract=contract,
                        source_index=source_index,
                        row_key=row_key,
                        cell=cell,
                        trace=trace,
                    )
                )
    if trace_index:
        raise ValueError("metric computation trace has no numeric draft cell")
    canonical = tuple(observations)
    blocked = tuple(
        dict.fromkeys(
            observation.table_id
            for observation in canonical
            if observation.publish_blocked
        )
    )
    digest = _digest_json([value.to_dict() for value in canonical])
    return MetricObservationPublication(
        observations=canonical,
        numeric_draft_cell_count=numeric_count,
        blocked_table_ids=blocked,
        observations_digest=digest,
    )


def recompute_observation_value(
    contract: PaperMetricContract,
    observation: PaperMetricObservation,
) -> Decimal | int | None:
    """只从 observation 自带的完整 membership 独立调用既有 evaluator。"""

    if not isinstance(observation, PaperMetricObservation):
        raise TypeError("observation must be PaperMetricObservation")
    bundle = MetricObservationBundle(
        row_facts=observation.row_facts,
        member_ids=observation.denominator_inventory_ids,
        member_facts_by_id=observation.member_facts_by_id,
        verified_observations=observation.verified_observations,
        row_identity_digest=observation.row_identity_digest,
    )
    evaluation = recompute_metric(
        contract,
        observation.table_id,
        observation.metric_id,
        row_kind=observation.row_kind,
        bundle=bundle,
    )
    if observation.numeric_value is not None and (
        evaluation.value != observation.numeric_value or evaluation.publish_blocked
    ):
        raise ValueError("independent recomputation mismatch")
    return evaluation.value


def _materialize_one(
    *,
    contract: PaperMetricContract,
    source_index: LineageSourceIndex,
    row_key: Mapping[str, Any],
    cell: object,
    trace: MetricComputationTrace,
) -> PaperMetricObservation:
    metric = contract.require_metric(trace.table_id, trace.metric_id)
    cell_value = getattr(cell, "value", None)
    cell_reason = getattr(cell, "reason", None)
    cell_blocked = getattr(cell, "publish_blocked", None)
    if type(cell_blocked) is not bool:
        raise TypeError("numeric draft cell must expose publish_blocked bool")
    if cell_value is not None and cell_value != trace.evaluation.value:
        raise ValueError("numeric draft value differs from evaluator trace")

    source_records = _records_for_bundle(source_index, trace.bundle)
    evidence_classes = {value.evidence_class for value in source_records}
    if len(evidence_classes) > 1:
        raise ValueError("metric cell crosses lineage evidence classes")
    evidence_class = (
        next(iter(evidence_classes))
        if evidence_classes
        else _single_metric_evidence_class(metric.evidence_classes)
    )
    direct_refs = _unique_mappings(
        item for record in source_records for item in record.direct_result_refs
    )
    current_refs = _unique_typed(
        item
        for record in source_records
        for item in record.current_task_attempt_event_refs
    )
    parser_refs = _unique_typed(
        item
        for record in source_records
        for item in record.parser_verifier_checker_canonical_refs
    )
    ledger_refs = _unique_typed(
        item for record in source_records for item in record.ledger_refs
    )
    provider_refs = _unique_typed(
        item
        for record in source_records
        for item in record.current_provider_object_refs
    )
    locators = _unique_typed(
        item
        for record in source_records
        for item in record.source_bank_object_locators
    )
    wrappers = _unique_mappings(
        {
            field.name: _json_value(getattr(item, field.name))
            for field in fields(item)
        }
        for record in source_records
        for item in record.current_trace_wrappers
    )
    bindings = _unique_mappings(
        item.to_dict()
        for record in source_records
        for item in record.trace_source_bindings
    )
    current_roles, current_role_issue = _validate_provider_roles_per_identity(
        metric.required_current_provider_roles,
        trace.bundle,
        provider_refs,
        direct_refs=direct_refs,
        current_refs=current_refs,
    )
    source_roles, source_role_issue = _validate_source_roles_per_identity(
        metric.required_source_bank_roles,
        trace.bundle,
        locators,
    )
    missing_roles: tuple[str, ...]
    not_applicable: tuple[str, ...]
    if evidence_class == "online_real_provider":
        if locators:
            raise ValueError("online observation contains source bank locators")
        missing_roles = (() if current_role_issue is None else (current_role_issue,))
        not_applicable = ("source_bank_object_locators",)
    elif evidence_class == "real_model_trace_protocol_run":
        if provider_refs:
            raise ValueError("trace observation contains current provider object refs")
        missing_roles = (() if source_role_issue is None else (source_role_issue,))
        not_applicable = ("current_provider_object_refs",)
    else:
        missing_roles = ()
        not_applicable = ()

    null_reason = cell_reason
    publish_blocked = cell_blocked
    numeric_value = cell_value
    blocked_reasons = tuple(
        dict.fromkeys(
            reason
            for reason in (
                null_reason,
                *(decision.reason for decision in trace.evaluation.member_decisions),
            )
            if reason
        )
    )
    if missing_roles:
        null_reason = missing_roles[0]
        numeric_value = None
        publish_blocked = True
        blocked_reasons = tuple(dict.fromkeys((*blocked_reasons, null_reason)))

    exclusion_reasons = {
        decision.member_id: decision.reason or "excluded_by_membership"
        for decision in trace.evaluation.member_decisions
        if decision.member_id in trace.evaluation.excluded_member_ids
    }
    identity = {
        "schema_version": "tokenshare.paper_metric_observation_identity.v1",
        "contract_digest": contract.contract_digest,
        "profile_digest": contract.pipeline_profile_digest,
        "source_index_digest": source_index.index_digest,
        "table_id": trace.table_id,
        "row_kind": trace.row_kind,
        "row_identity_digest": trace.bundle.row_identity_digest,
        "column_id": trace.metric_id,
        "metric_id": trace.metric_id,
        "formula_id": trace.formula_id,
    }
    observation_id = _digest_json(identity)
    values = {
        "observation_id": observation_id,
        "metric_contract_id": contract.contract_id,
        "metric_contract_digest": contract.contract_digest,
        "pipeline_profile_id": contract.pipeline_profile_id,
        "pipeline_profile_digest": contract.pipeline_profile_digest,
        "source_index_id": source_index.index_id,
        "source_index_digest": source_index.index_digest,
        "table_id": trace.table_id,
        "row_kind": trace.row_kind,
        "row_key": row_key,
        "row_identity_digest": trace.bundle.row_identity_digest,
        "column_id": trace.metric_id,
        "metric_id": trace.metric_id,
        "formula_id": trace.formula_id,
        "numeric_value": numeric_value,
        "null_reason": null_reason,
        "publish_blocked": publish_blocked,
        "numerator_membership_ids": trace.evaluation.included_member_ids,
        "denominator_inventory_ids": trace.bundle.member_ids,
        "audit_denominator_member_ids": trace.evaluation.audit_denominator_member_ids,
        "excluded_member_ids": trace.evaluation.excluded_member_ids,
        "blocked_member_ids": trace.evaluation.blocked_member_ids,
        "exclusion_reasons": exclusion_reasons,
        "blocked_reasons": blocked_reasons,
        "row_facts": trace.bundle.row_facts,
        "member_facts_by_id": trace.bundle.member_facts_by_id,
        "verified_observations": trace.bundle.verified_observations,
        "required_current_provider_roles": metric.required_current_provider_roles,
        "required_source_bank_roles": metric.required_source_bank_roles,
        "covered_current_provider_roles": current_roles,
        "covered_source_bank_roles": source_roles,
        "not_applicable_evidence_roles": not_applicable,
        "evidence_class": evidence_class,
        "direct_result_refs": direct_refs,
        "current_task_attempt_event_refs": current_refs,
        "parser_verifier_checker_canonical_refs": parser_refs,
        "ledger_refs": ledger_refs,
        "current_provider_object_refs": provider_refs,
        "source_bank_object_locators": locators,
        "current_trace_wrappers": wrappers,
        "trace_source_bindings": bindings,
    }
    provisional = PaperMetricObservation(
        **values,
        observation_digest="sha256:" + "0" * 64,
    )
    return PaperMetricObservation(
        **values,
        observation_digest=_digest_json(_observation_body(provisional)),
    )


def _records_for_bundle(
    index: LineageSourceIndex,
    bundle: MetricObservationBundle,
) -> tuple[LineageSourceRecord, ...]:
    values: list[LineageSourceRecord] = []
    identity_fields = (
        "source_bank_entry_id",
        "current_attempt_id",
        "original_attempt_id",
        "replacement_attempt_id",
        "current_replacement_attempt_id",
        "provider_attempt_id",
        "attempt_id",
        "preregistered_root_run_id",
        "unit_id",
    )
    for member_id in bundle.member_ids:
        aliases = [member_id]
        facts = bundle.member_facts_by_id[member_id]
        aliases.extend(
            value
            for field_name in identity_fields
            if isinstance((value := facts.get(field_name)), str) and value
        )
        for alias in aliases:
            record = index.get(alias)
            if record is not None and record not in values:
                values.append(record)
    # attempt alias record 保留 attempt-scoped event；run-scoped provider artifacts
    # 由同一 direct snapshot 指向的 root record 提供。
    for record in tuple(values):
        for direct_ref in record.direct_result_refs:
            root_id = direct_ref.get("preregistered_root_run_id")
            root_record = index.get(root_id) if isinstance(root_id, str) else None
            if root_record is not None and root_record not in values:
                values.append(root_record)
    return tuple(values)


def _single_metric_evidence_class(values: Sequence[str]) -> str:
    paper_classes = tuple(
        value
        for value in values
        if value in {"online_real_provider", "real_model_trace_protocol_run"}
    )
    if len(paper_classes) == 1:
        return paper_classes[0]
    return "regression_only"


def _role_covered(required: str, actual_values: Any) -> bool:
    actual = set(actual_values)
    if required == "raw_output_or_provider_failure":
        return bool(actual & {required, "raw_output", "provider_failure"})
    if required == "usage_status":
        return bool(actual & {required, "usage"})
    return required in actual


def _semantic_role(role: str) -> str:
    if role in {"raw_output", "provider_failure"}:
        return "raw_output_or_provider_failure"
    if role == "usage":
        return "usage_status"
    return role


def _required_provider_identities(bundle: MetricObservationBundle) -> tuple[str, ...]:
    member_kinds = {
        "actual_provider_attempt",
        "actual_first_provider_attempt",
        "exp2_online_first_provider_attempt",
        "exp5_provider_attempt",
        "online_recovery_original_attempt",
        "online_recovery_replacement_attempt",
    }
    result: list[str] = []
    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        if facts.get("member_kind") not in member_kinds:
            continue
        identity = next(
            (
                facts[name]
                for name in (
                    "provider_attempt_id",
                    "current_attempt_id",
                    "replacement_attempt_id",
                    "original_attempt_id",
                    "attempt_id",
                )
                if isinstance(facts.get(name), str) and facts[name]
            ),
            member_id,
        )
        result.append(identity)
    return tuple(dict.fromkeys(result))


def _required_source_identities(bundle: MetricObservationBundle) -> tuple[str, ...]:
    member_kinds = {
        "committed_trace_consumption",
        "trace_consumption",
        "exp3_discarded_trace_consumption",
    }
    values = []
    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        if facts.get("member_kind") not in member_kinds:
            continue
        entry_id = facts.get("source_bank_entry_id")
        if isinstance(entry_id, str) and entry_id:
            values.append(entry_id)
    return tuple(dict.fromkeys(values))


def _validate_roles_per_identity(
    required_roles: Sequence[str],
    expected_identities: Sequence[str],
    values: Sequence[object],
    *,
    identity_attribute: str,
    role_attribute: str,
) -> tuple[tuple[str, ...], str | None]:
    if not required_roles:
        return (), None
    expected = tuple(expected_identities)
    if not expected:
        return (), f"missing_required_lineage:{required_roles[0]}"
    groups: dict[str, list[str]] = {identity: [] for identity in expected}
    for value in values:
        identity = getattr(value, identity_attribute)
        if identity not in groups:
            return (), f"invalid_lineage_identity:{identity}"
        groups[identity].append(_semantic_role(getattr(value, role_attribute)))
    for identity in expected:
        roles = groups[identity]
        for required in required_roles:
            count = roles.count(required)
            if count != 1:
                suffix = required if len(expected) == 1 else f"{identity}:{required}"
                return (), f"missing_required_lineage:{suffix}"
    return tuple(required_roles), None


def _validate_provider_roles_per_identity(
    required_roles: Sequence[str],
    bundle: MetricObservationBundle,
    values: Sequence[ArtifactIdentitySnapshot],
    *,
    direct_refs: Sequence[Mapping[str, Any]],
    current_refs: Sequence[LedgerEventIdentitySnapshot],
) -> tuple[tuple[str, ...], str | None]:
    if not required_roles:
        return (), None
    run_tasks, issue = _provider_run_tasks(
        bundle,
        direct_refs=direct_refs,
        current_refs=current_refs,
    )
    if issue is not None:
        return (), issue
    for value in values:
        task_id = run_tasks.get(value.source_execution_id)
        if task_id is None:
            return (), f"invalid_lineage_identity:{value.source_execution_id}"
        if value.source_task_id != task_id:
            return (), f"invalid_lineage_task:{value.source_execution_id}"
    return _validate_roles_per_identity(
        required_roles,
        tuple(run_tasks),
        values,
        identity_attribute="source_execution_id",
        role_attribute="source_role",
    )


def _provider_run_tasks(
    bundle: MetricObservationBundle,
    *,
    direct_refs: Sequence[Mapping[str, Any]],
    current_refs: Sequence[LedgerEventIdentitySnapshot],
) -> tuple[dict[str, str], str | None]:
    """用 direct execution binding 把 attempt-scoped 事实绑定到 run-scoped artifact。"""

    required_attempts = _required_provider_identities(bundle)
    if not required_attempts:
        return {}, "missing_required_lineage:provider_attempt"
    required = set(required_attempts)
    current_by_attempt: dict[str, list[LedgerEventIdentitySnapshot]] = {}
    current_payloads = tuple(ref.to_dict() for ref in current_refs)
    for ref in current_refs:
        if ref.object_id not in required:
            continue
        if ref.object_type != "Attempt":
            return {}, f"invalid_lineage_attempt:{ref.object_id}"
        current_by_attempt.setdefault(ref.object_id, []).append(ref)

    candidates: dict[str, set[tuple[str, str]]] = {
        attempt_id: set() for attempt_id in required_attempts
    }
    for direct_ref in direct_refs:
        binding = direct_ref.get("execution_binding")
        attempt_refs = direct_ref.get("attempt_refs")
        if not isinstance(binding, Mapping) or not isinstance(attempt_refs, Sequence):
            continue
        if isinstance(attempt_refs, (str, bytes, bytearray)):
            return {}, "invalid_lineage_ref:attempt_refs"
        run_id = binding.get("execution_id")
        task_id = binding.get("task_id")
        root_unit_id = binding.get("root_unit_id")
        if any(not isinstance(value, str) or not value for value in (run_id, task_id, root_unit_id)):
            return {}, "invalid_lineage_ref:execution_binding"
        binding_issue = _validate_persisted_execution_binding(direct_ref, binding)
        if binding_issue is not None:
            return {}, binding_issue
        binding_events = binding.get("events")
        for raw_ref in attempt_refs:
            if not isinstance(raw_ref, Mapping):
                return {}, "invalid_lineage_ref:attempt"
            attempt_id = raw_ref.get("object_id")
            if (
                raw_ref.get("schema_version")
                != "tokenshare.ledger_event_identity_snapshot.v1"
                or raw_ref.get("task_id") != task_id
                or not _is_identity_digest(raw_ref.get("event_hash"))
            ):
                return {}, f"invalid_lineage_ref:{attempt_id}"
            if dict(raw_ref) not in current_payloads:
                return {}, f"invalid_lineage_ref:{attempt_id}"
            if binding_events is not None and dict(raw_ref) not in binding_events:
                return {}, f"invalid_lineage_ref:{attempt_id}:binding_events"
            if raw_ref.get("object_type") == "Attempt" and attempt_id in required:
                candidates[str(attempt_id)].add((str(run_id), str(task_id)))
        if binding_events is not None:
            for attempt_id in required_attempts:
                if any(
                    value.to_dict() in binding_events
                    for value in current_by_attempt.get(attempt_id, ())
                ):
                    candidates[attempt_id].add((str(run_id), str(task_id)))

    run_tasks: dict[str, str] = {}
    for attempt_id in required_attempts:
        aliases = candidates[attempt_id]
        if len(aliases) != 1:
            return {}, f"missing_required_lineage:{attempt_id}:execution_binding"
        run_id, task_id = next(iter(aliases))
        previous = run_tasks.setdefault(run_id, task_id)
        if previous != task_id:
            return {}, f"invalid_lineage_task:{run_id}"
    return run_tasks, None


def _validate_persisted_execution_binding(
    direct_ref: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> str | None:
    """完整 canonical binding 出现时复核其 identity digest；最小 alias 仍由 event ref 锁定。"""

    canonical_fields = {
        "schema_version",
        "preregistered_root_run_id",
        "execution_id",
        "task_id",
        "root_unit_id",
        "ledger_digest",
        "events",
        "binding_digest",
    }
    if not {
        "schema_version",
        "preregistered_root_run_id",
        "ledger_digest",
        "events",
        "binding_digest",
    }.intersection(binding):
        return None
    if set(binding) != canonical_fields:
        return "invalid_lineage_ref:execution_binding"
    root_id = direct_ref.get("preregistered_root_run_id")
    if (
        binding.get("schema_version")
        != "tokenshare.direct_root_execution_binding.v1"
        or binding.get("preregistered_root_run_id") != root_id
        or not _is_identity_digest(binding.get("ledger_digest"))
        or not isinstance(binding.get("events"), Sequence)
        or isinstance(binding.get("events"), (str, bytes, bytearray))
    ):
        return "invalid_lineage_ref:execution_binding"
    body = {name: binding[name] for name in canonical_fields - {"binding_digest"}}
    if binding.get("ledger_digest") != _digest_json(binding["events"]):
        return "invalid_lineage_digest:ledger"
    if binding.get("binding_digest") != _digest_json(body):
        return "invalid_lineage_digest:execution_binding"
    return None


def _is_identity_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _validate_source_roles_per_identity(
    required_roles: Sequence[str],
    bundle: MetricObservationBundle,
    values: Sequence[ExternalBankObjectLocator],
) -> tuple[tuple[str, ...], str | None]:
    return _validate_roles_per_identity(
        required_roles,
        _required_source_identities(bundle),
        values,
        identity_attribute="entry_id",
        role_attribute="object_role",
    )


def _row_key(value: object) -> Mapping[str, Any]:
    if not is_dataclass(value):
        raise TypeError("metric draft row must be a dataclass")
    return {
        field.name: _json_value(getattr(value, field.name))
        for field in fields(value)
        if field.name not in {"cells", "paper_eligible", "audit_ratios"}
        and field.repr
    }


def _observation_body(value: PaperMetricObservation) -> dict[str, Any]:
    excluded = {"observation_digest"}
    return {
        field.name: _json_value(getattr(value, field.name))
        for field in fields(value)
        if field.name not in excluded
    }


def _unique_typed(values: Any) -> tuple[Any, ...]:
    by_digest: dict[str, Any] = {}
    for value in values:
        key = _digest_json(value.to_dict())
        previous = by_digest.setdefault(key, value)
        if previous != value:
            raise ValueError("metric observation typed ref identity conflict")
    return tuple(by_digest[key] for key in sorted(by_digest))


def _unique_mappings(values: Any) -> tuple[Mapping[str, Any], ...]:
    by_digest: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError("metric observation direct refs must be mappings")
        key = _digest_json(value)
        by_digest.setdefault(key, value)
    return tuple(by_digest[key] for key in sorted(by_digest))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if field.repr
        }
    raise TypeError(f"unsupported observation value: {type(value).__name__}")


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_digest(value: Any, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _require_string(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


__all__ = [
    "MetricObservationPublication",
    "PaperMetricObservation",
    "materialize_metric_observations",
    "recompute_observation_value",
]
