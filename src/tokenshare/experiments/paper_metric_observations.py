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
    lineage_source_record_refs: tuple[Mapping[str, str], ...] = ()
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
        lineage_refs = tuple(self.lineage_source_record_refs)
        for value in lineage_refs:
            if (
                not isinstance(value, Mapping)
                or set(value) != {"member_id", "record_digest"}
                or not isinstance(value.get("member_id"), str)
                or not value["member_id"]
            ):
                raise ValueError("metric observation lineage source record ref is invalid")
            _require_digest(value.get("record_digest"), "record_digest")
        if len({value["member_id"] for value in lineage_refs}) != len(lineage_refs):
            raise ValueError("duplicate metric observation lineage source record ref")
        object.__setattr__(
            self,
            "lineage_source_record_refs",
            tuple(MappingProxyType(dict(value)) for value in lineage_refs),
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
    digest = _digest_metric_observations(canonical)
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
    typed_lineage_issue = _typed_root_lineage_issue(
        source_index,
        trace.bundle,
    )
    evidence_classes = {value.evidence_class for value in source_records}
    if len(evidence_classes) > 1:
        raise ValueError("metric cell crosses lineage evidence classes")
    declared_evidence_class = trace.bundle.row_facts.get("evidence_class")
    if declared_evidence_class is None:
        declared_evidence_class = (
            next(iter(evidence_classes))
            if evidence_classes
            else _single_metric_evidence_class(metric.evidence_classes)
        )
    if (
        not isinstance(declared_evidence_class, str)
        or declared_evidence_class not in metric.evidence_classes
        or (
            evidence_classes
            and evidence_classes != {declared_evidence_class}
        )
    ):
        raise ValueError("metric lineage evidence class route mismatch")
    evidence_class = declared_evidence_class
    required_current_provider_roles, required_source_bank_roles = (
        metric.required_roles_for(evidence_class)
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
    required_source_identities = _required_source_identities(trace.bundle)
    source_identity_records = tuple(
        record
        for identity in required_source_identities
        for record in (source_index.get(identity),)
        if record is not None
    )
    locators = _unique_typed(
        item
        for record in source_identity_records
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
        required_current_provider_roles,
        trace.bundle,
        provider_refs,
        direct_refs=direct_refs,
        current_refs=current_refs,
    )
    source_roles, source_role_issue = _validate_source_roles_per_identity(
        required_source_bank_roles,
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
        missing_roles = tuple(
            value
            for value in (typed_lineage_issue, source_role_issue)
            if value is not None
        )
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
    lineage_source_record_refs = tuple(
        {
            "member_id": record.member_id,
            "record_digest": record.record_digest,
        }
        for record in source_records
    )
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
        "required_current_provider_roles": required_current_provider_roles,
        "required_source_bank_roles": required_source_bank_roles,
        "covered_current_provider_roles": current_roles,
        "covered_source_bank_roles": source_roles,
        "not_applicable_evidence_roles": not_applicable,
        "evidence_class": evidence_class,
        # 完整 closure 只在 digest-bound lineage index 保存一次。每个 cell
        # 先在内存中完成角色/identity 校验，再只持久化 record 引用，避免按
        # cell 复制 root closure 并在 Full 规模形成磁盘笛卡尔膨胀。
        "direct_result_refs": (),
        "current_task_attempt_event_refs": (),
        "parser_verifier_checker_canonical_refs": (),
        "ledger_refs": (),
        "current_provider_object_refs": (),
        "source_bank_object_locators": (),
        "current_trace_wrappers": (),
        "trace_source_bindings": (),
        "lineage_source_record_refs": lineage_source_record_refs,
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
    declared_root_ids: set[str] = set()
    strict_declared_root_ids: set[str] = set()
    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        if facts.get("member_kind") in {
            "exp2_preregistered_pair",
            "exp2_repeat_speedup_summary",
            "exp4_paired_root",
        }:
            root_ids = facts.get("lineage_root_run_ids")
            if not isinstance(root_ids, Sequence) or isinstance(
                root_ids, (str, bytes, bytearray)
            ):
                raise ValueError("typed metric lineage roots are missing")
            normalized_root_ids = tuple(root_ids)
            if (
                any(
                    not isinstance(value, str) or not value
                    for value in normalized_root_ids
                )
                or len(set(normalized_root_ids)) != len(normalized_root_ids)
            ):
                raise ValueError("typed metric lineage roots are invalid")
            declared_root_ids.update(normalized_root_ids)
            strict_declared_root_ids.update(normalized_root_ids)
        if facts.get("member_kind") not in {
            "preregistered_root",
            "exp5_preregistered_root",
        }:
            continue
        explicit = facts.get("preregistered_root_run_id")
        if isinstance(explicit, str) and explicit:
            declared_root_ids.add(explicit)
            continue
        record = index.get(member_id)
        if record is not None:
            roots = _record_root_ids(record)
            if roots == {member_id}:
                declared_root_ids.add(member_id)

    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        aliases = _stable_lineage_aliases(member_id, facts, declared_root_ids)
        for alias in aliases:
            record = index.get(alias)
            if record is not None and record not in values:
                values.append(record)

    # Root closure 只能由 bundle 明示的 root，或单一 typed alias 已证明的 root
    # 显式补入；禁止再把 alias 中的任意 direct ref 泛化成跨 root 扩张。
    for root_id in sorted(declared_root_ids):
        record = index.get(root_id)
        if root_id in strict_declared_root_ids:
            if record is None:
                # 缺失是 cell 级 lineage blocker，不应让整个 metrics runner
                # 退化为 runner_internal。已存在但跨 root/歧义仍硬拒绝。
                continue
            if _record_root_ids(record) != {root_id}:
                raise ValueError(
                    "declared metric lineage root is missing or ambiguous"
                )
        elif record is None:
            # 历史/合成 bundle 可以没有可解析的 root closure；它们会由
            # 后续 required-role 物化层 fail closed。只有明示 typed pair
            # 声明的 roots 才在此边界强制存在且唯一。
            continue
        if record not in values:
            values.append(record)
    resolved_root_ids = {
        root_id for record in values for root_id in _record_root_ids(record)
    }
    if declared_root_ids and not resolved_root_ids <= declared_root_ids:
        raise ValueError("lineage record crosses metric bundle root scope")
    if not declared_root_ids and len(resolved_root_ids) > 1:
        raise ValueError("lineage record has ambiguous metric bundle root scope")
    if not declared_root_ids and len(resolved_root_ids) == 1:
        root_id = next(iter(resolved_root_ids))
        record = index.get(root_id)
        if record is not None and record not in values:
            values.append(record)
    return tuple(values)


def _stable_lineage_aliases(
    member_id: str,
    facts: Mapping[str, Any],
    declared_root_ids: set[str],
) -> tuple[str, ...]:
    """只允许 typed stable identity 参与 lineage join。"""

    kind = facts.get("member_kind")
    aliases: list[str] = []
    if kind in {"preregistered_root", "exp5_preregistered_root"}:
        explicit = facts.get("preregistered_root_run_id")
        if isinstance(explicit, str) and explicit:
            aliases.append(explicit)
        elif member_id in declared_root_ids:
            aliases.append(member_id)
        source_entry_ids = facts.get("source_bank_entry_ids")
        if isinstance(source_entry_ids, Sequence) and not isinstance(
            source_entry_ids, (str, bytes, bytearray)
        ):
            aliases.extend(source_entry_ids)
    if kind in {
        "exp2_preregistered_pair",
        "exp2_repeat_speedup_summary",
        "exp4_paired_root",
    }:
        root_ids = facts.get("lineage_root_run_ids")
        if isinstance(root_ids, Sequence) and not isinstance(
            root_ids, (str, bytes, bytearray)
        ):
            aliases.extend(root_ids)
        source_entry_ids = facts.get("source_bank_entry_ids")
        if isinstance(source_entry_ids, Sequence) and not isinstance(
            source_entry_ids, (str, bytes, bytearray)
        ):
            aliases.extend(source_entry_ids)
    if kind in {
        "committed_trace_consumption",
        "trace_consumption",
        "exp3_discarded_trace_consumption",
    }:
        aliases.append(facts.get("source_bank_entry_id"))
    if kind in {
        "actual_provider_attempt",
        "actual_first_provider_attempt",
        "exp2_online_first_provider_attempt",
        "exp5_provider_attempt",
        "online_recovery_original_attempt",
        "online_recovery_replacement_attempt",
    }:
        # Typed provider member 的 member_id 是允许的稳定 attempt alias；
        # neutral persisted records 仍不会走这条路径。
        aliases.append(member_id)
        for field_name in (
            "provider_attempt_id",
            "current_attempt_id",
            "replacement_attempt_id",
            "original_attempt_id",
            "attempt_id",
        ):
            aliases.append(facts.get(field_name))
    return tuple(
        dict.fromkeys(
            value for value in aliases if isinstance(value, str) and value
        )
    )


def _record_root_ids(record: LineageSourceRecord) -> set[str]:
    return {
        root_id
        for direct_ref in record.direct_result_refs
        if isinstance(
            (root_id := direct_ref.get("preregistered_root_run_id")), str
        )
        and root_id
    }


def _typed_root_lineage_issue(
    index: LineageSourceIndex,
    bundle: MetricObservationBundle,
 ) -> str | None:
    """Typed pair 的 root/exact-consumption 不完整时返回 cell blocker。"""

    declared_root_ids: set[str] = set()
    facts_by_root: dict[str, Mapping[str, Any]] = {}
    requires_exact_root_entries = any(
        bundle.member_facts_by_id[member_id].get("member_kind")
        in {
            "exp2_preregistered_pair",
            "exp2_repeat_speedup_summary",
            "exp4_paired_root",
        }
        for member_id in bundle.member_ids
    )
    if not requires_exact_root_entries:
        return None
    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        if facts.get("member_kind") in {
            "exp2_preregistered_pair",
            "exp2_repeat_speedup_summary",
            "exp4_paired_root",
        }:
            root_ids = facts.get("lineage_root_run_ids")
            if not isinstance(root_ids, Sequence) or isinstance(
                root_ids, (str, bytes, bytearray)
            ):
                return "missing_declared_root_lineage"
            declared_root_ids.update(
                value for value in root_ids if isinstance(value, str) and value
            )
    for member_id in bundle.member_ids:
        facts = bundle.member_facts_by_id[member_id]
        if facts.get("member_kind") not in {
            "preregistered_root",
            "exp5_preregistered_root",
        } or "source_bank_entry_ids" not in facts:
            continue
        root_id = facts.get("preregistered_root_run_id")
        if not isinstance(root_id, str) or not root_id:
            root_id = member_id if member_id in declared_root_ids else None
        if not isinstance(root_id, str) or root_id not in declared_root_ids:
            return "root_source_lineage_lacks_declared_identity"
        previous = facts_by_root.setdefault(root_id, facts)
        if previous != facts:
            return "conflicting_root_source_lineage_facts"

    if set(facts_by_root) != declared_root_ids:
        return "missing_exact_root_source_facts"

    for root_id, facts in facts_by_root.items():
        claimed = facts.get("source_bank_entry_ids")
        if not isinstance(claimed, Sequence) or isinstance(
            claimed, (str, bytes, bytearray)
        ):
            return "invalid_root_source_lineage_entries"
        claimed_ids = tuple(claimed)
        if (
            any(not isinstance(value, str) or not value for value in claimed_ids)
            or len(set(claimed_ids)) != len(claimed_ids)
        ):
            return "invalid_root_source_lineage_entries"
        record = index.get(root_id)
        if record is None:
            return f"missing_declared_root_lineage:{root_id}"
        current_ids = tuple(
            dict.fromkeys(
                wrapper.entry_id for wrapper in record.current_trace_wrappers
            )
        )
        committed_ids = {
            consumption.entry_id
            for consumption in record.committed_trace_consumptions
        }
        frozen_binding_ids = {
            replacement.entry_id
            for binding in record.trace_source_bindings
            for replacement in binding.replacements
        }
        claimed_set = set(claimed_ids)
        # current wrappers 只表示每个 unit 的 terminal/current attempt；发生
        # replacement 时，持久化 usage 还会包含更早已 committed 的 slot。
        # claimed distinct 必须精确等于 digest-bound committed history；同时
        # 保持 current⊆committed⊆planned，不能把未消费的 planned slot 扩进分母。
        if (
            not current_ids
            or not committed_ids
            or claimed_set != committed_ids
            or not set(current_ids) <= committed_ids
            or not committed_ids <= frozen_binding_ids
        ):
            return f"root_source_lineage_mismatch:{root_id}"
    return None


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
        if facts.get("member_kind") in member_kinds:
            entry_id = facts.get("source_bank_entry_id")
            if isinstance(entry_id, str) and entry_id:
                values.append(entry_id)
        entry_ids = facts.get("source_bank_entry_ids")
        if isinstance(entry_ids, Sequence) and not isinstance(
            entry_ids, (str, bytes, bytearray)
        ):
            values.extend(
                entry_id
                for entry_id in entry_ids
                if isinstance(entry_id, str) and entry_id
            )
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


def _digest_metric_observations(
    observations: Sequence[PaperMetricObservation],
) -> str:
    """流式计算与既有 canonical JSON array 完全相同的集合摘要。"""

    digest = hashlib.sha256()
    digest.update(b"[")
    for index, observation in enumerate(observations):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(
                observation.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    digest.update(b"]")
    return "sha256:" + digest.hexdigest()


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
