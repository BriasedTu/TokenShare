"""把 Task 20 typed metric observations 渲染为论文数据表与审计清单。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
from typing import Any

from tokenshare.experiments.paper_metric_contract import PaperMetricContract
from tokenshare.experiments.paper_metric_observations import PaperMetricObservation
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    ExternalBankObjectLocator,
)


PAPER_TABLE_CSV_FILES = (
    "metrics/paper_table_feasibility.csv",
    "metrics/paper_plot_scalability.csv",
    "metrics/paper_table_scalability_online.csv",
    "metrics/paper_plot_robustness.csv",
    "metrics/paper_table_recovery_online.csv",
    "metrics/paper_table_ablation.csv",
    "metrics/paper_table_model_endpoint_quality.csv",
    "metrics/paper_table_model_endpoint_resources.csv",
)
PAPER_TABLE_TEX_FILES = tuple(path[:-4] + ".tex" for path in PAPER_TABLE_CSV_FILES)
PAPER_RENDERER_AUDIT_FILE = "audit/paper_metric_renderer_audit.v1.jsonl"
PAPER_RENDERER_MANIFEST_FILE = "audit/paper_metric_renderer_manifest.v1.json"

# 旧 Exp5 renderer 的 pairwise、accepted-validity、ranking/recovery 产物退出当前路径。
_RETIRED_OUTPUTS = (
    "metrics/exp5_model_overall.csv",
    "metrics/exp5_model_by_domain_topic.csv",
    "metrics/exp5_paired_comparisons.csv",
    "metrics/exp5_model_execution_records.jsonl",
    "metrics/exp5_order_and_concurrency.csv",
    "metrics/exp5_failure_taxonomy.csv",
    "metrics/paper_table_model_comparison.csv",
    "metrics/paper_table_model_endpoint_comparison.csv",
    "metrics/paper_table_exp5_pairwise.csv",
    "metrics/paper_table_exp5_ranking.csv",
    "metrics/paper_table_exp5_recovery.csv",
    "paper/exp5_model_overall.tex",
    "paper/exp5_model_by_domain_topic.tex",
    "paper/exp5_completion_validity.pdf",
    "paper/exp5_completion_validity.svg",
    "paper/exp5_tokens_latency.pdf",
    "paper/exp5_tokens_latency.svg",
    "paper/exp5_results_summary.md",
    "paper/exp5_failure_appendix.md",
)
_SAFE_REFERENCE_FIELDS = (
    "artifact_id",
    "event_id",
    "entry_id",
    "path",
    "uri",
    "content_hash",
    "record_digest",
    "digest",
    "object_digest",
    "object_role",
    "schema_version",
)
_UNSAFE_PAYLOAD_FIELDS = frozenset(
    {"raw_reasoning", "reasoning_content", "chain_of_thought", "raw_output", "raw_output_text"}
)
_ROW_KEY_FIELDS = {
    ("exp1_feasibility", "condition_summary"): (
        "domain", "paper_difficulty", "topic_family", "repeat_id"
    ),
    ("exp2_trace_scalability", "worker_repeat_observation"): (
        "preregistered_root_run_ids", "case_record_digests",
        "factor_position_quantiles", "position_stratum", "repeat_id", "worker_count",
    ),
    ("exp2_trace_scalability", "paired_worker_comparison"): (
        "pair_identity", "baseline_root_run_id", "compared_root_run_id", "position_stratum",
    ),
    ("exp2_trace_scalability", "repeat_summary"): (
        "worker_count", "repeat_id", "position_stratum",
    ),
    ("exp2_trace_scalability", "condition_summary"): (
        "worker_count", "position_stratum",
    ),
    ("exp2_online_concurrency", "worker_condition_summary"): (
        "worker_count", "case_position", "repeat_id",
    ),
    ("exp3_trace_robustness", "condition_observation"): (
        "condition_id", "fault_type", "repeat_id", "sample_slot_id",
    ),
    ("exp3_trace_robustness", "worker_death_summary"): (
        "condition_id", "fault_type", "repeat_id", "sample_slot_id",
    ),
    ("exp3_online_recovery", "online_recovery_summary"): (
        "recovery_source", "case_id", "repeat_id",
    ),
    ("exp4_ablation", "mode_summary"): (
        "domain", "repeat_id", "ablation_mode", "preregistered_root_run_ids",
        "ineligibility_reasons",
    ),
    ("exp4_ablation", "paired_transition"): (
        "pair_identity", "case_record_digest", "domain", "ablation_mode",
        "full_root_run_id", "ablation_root_run_id", "ineligibility_reasons",
    ),
    ("exp4_ablation", "mode_specific"): (
        "domain", "repeat_id", "ablation_mode", "ineligibility_reasons",
    ),
    ("exp5_quality", "model_summary"): (
        "model_arm_ids", "model_endpoint_identity", "ineligibility_reasons",
    ),
    ("exp5_resources", "model_summary"): (
        "model_arm_ids", "model_endpoint_identity", "ineligibility_reasons",
    ),
}


@dataclass(frozen=True, kw_only=True)
class PaperMetricArtifactResult:
    table_count: int
    observation_count: int
    table_refs: tuple[dict[str, str], ...]
    audit_ref: dict[str, str]
    manifest_ref: dict[str, str]
    captions: Mapping[str, str]
    paper_eligible: bool
    blocked_table_ids: tuple[str, ...]
    schema_version: str = "tokenshare.paper_metric_artifact_result.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "captions", MappingProxyType(dict(self.captions)))

    @property
    def artifact_refs(self) -> tuple[dict[str, str], ...]:
        return self.table_refs + (self.audit_ref, self.manifest_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_count": self.table_count,
            "observation_count": self.observation_count,
            "table_refs": [dict(ref) for ref in self.table_refs],
            "audit_ref": dict(self.audit_ref),
            "manifest_ref": dict(self.manifest_ref),
            "captions": dict(self.captions),
            "paper_eligible": self.paper_eligible,
            "blocked_table_ids": list(self.blocked_table_ids),
        }


def render_paper_metric_artifacts(
    *,
    output_root: str | Path,
    contract: PaperMetricContract,
    observations: Sequence[PaperMetricObservation],
    observations_digest: str,
) -> PaperMetricArtifactResult:
    """只遍历 contract allowlist 与 typed observations，不读取 draft/raw artifact。"""

    if not isinstance(contract, PaperMetricContract):
        raise TypeError("contract must be PaperMetricContract")
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise TypeError("observations must be a PaperMetricObservation collection")
    _require_digest(observations_digest, "observations_digest")
    _validate_contract_inventory(contract)
    canonical = _canonical_observations(
        contract,
        observations,
        observations_digest=observations_digest,
    )
    observed_table_ids = {item.table_id for item in canonical}
    expected_table_ids = {table.table_id for table in contract.tables}
    if observed_table_ids != expected_table_ids:
        raise ValueError("exact non-empty eight-table Task20 publication required")
    blocked_table_ids = tuple(
        table.table_id
        for table in contract.tables
        if any(
            item.table_id == table.table_id and item.publish_blocked
            for item in canonical
        )
    )
    paper_eligible = not blocked_table_ids

    captions = {
        table.table_id: _caption(contract, table.table_id)
        for table in contract.tables
    }
    contents: dict[str, str] = {}
    table_refs: list[dict[str, str]] = []
    table_manifest: list[dict[str, Any]] = []
    for table, csv_relative, tex_relative in zip(
        contract.tables,
        PAPER_TABLE_CSV_FILES,
        PAPER_TABLE_TEX_FILES,
        strict=True,
    ):
        table_observations = tuple(
            item for item in canonical if item.table_id == table.table_id
        )
        expected_by_row_kind = {
            row_kind: tuple(
                metric.metric_id
                for metric in contract.metrics
                if metric.table == table.table_id and metric.row_kind == row_kind
            )
            for row_kind in table.row_kinds
        }
        headers, rows = _table_rows(
            table.numeric_output_fields,
            expected_by_row_kind,
            table_observations,
        )
        csv_text = _csv_text(headers, rows)
        tex_text = _tex_fragment(headers, rows, captions[table.table_id])
        contents[csv_relative] = csv_text
        contents[tex_relative] = tex_text
        csv_ref = _content_ref(csv_relative, csv_text)
        tex_ref = _content_ref(tex_relative, tex_text)
        table_refs.extend((csv_ref, tex_ref))
        table_manifest.append(
            {
                "table_id": table.table_id,
                "csv_ref": csv_ref,
                "tex_ref": tex_ref,
                "caption": captions[table.table_id],
                "numeric_output_fields": list(table.numeric_output_fields),
                "row_count": len(rows),
                "observation_ids": [item.observation_id for item in table_observations],
                "observation_digests": [
                    item.observation_digest for item in table_observations
                ],
            }
        )

    audit_text = "".join(
        _canonical_json(_audit_record(item)) + "\n" for item in canonical
    )
    contents[PAPER_RENDERER_AUDIT_FILE] = audit_text
    audit_ref = _content_ref(PAPER_RENDERER_AUDIT_FILE, audit_text)
    manifest = {
        "schema_version": "tokenshare.paper_metric_renderer_manifest.v1",
        "metric_contract_id": contract.contract_id,
        "metric_contract_digest": contract.contract_digest,
        "pipeline_profile_id": contract.pipeline_profile_id,
        "pipeline_profile_digest": contract.pipeline_profile_digest,
        "source_observations_digest": observations_digest,
        "renderer_observation_identity_digest": _digest_json(
            [
                {
                    "observation_id": item.observation_id,
                    "observation_digest": item.observation_digest,
                }
                for item in canonical
            ]
        ),
        "observation_count": len(canonical),
        "table_count": len(contract.tables),
        "paper_eligible": paper_eligible,
        "blocked_table_ids": list(blocked_table_ids),
        "tables": table_manifest,
        "audit_ref": audit_ref,
        "provider_calls": 0,
        "input_boundary": "PaperMetricObservation_collection_plus_PaperMetricContract",
    }
    manifest_text = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    contents[PAPER_RENDERER_MANIFEST_FILE] = manifest_text
    root = Path(output_root)
    _promote_generation(root, contents)
    return PaperMetricArtifactResult(
        table_count=len(contract.tables),
        observation_count=len(canonical),
        table_refs=tuple(table_refs),
        audit_ref=audit_ref,
        manifest_ref=_content_ref(PAPER_RENDERER_MANIFEST_FILE, manifest_text),
        captions=captions,
        paper_eligible=paper_eligible,
        blocked_table_ids=blocked_table_ids,
    )


def _validate_contract_inventory(contract: PaperMetricContract) -> None:
    paths = tuple(table.output_path for table in contract.tables)
    if paths != PAPER_TABLE_CSV_FILES:
        raise ValueError("paper table inventory contract drift")
    if len(contract.tables) != 8 or contract.controls.exp5_summary_table_count != 2:
        raise ValueError("paper table inventory must contain exact current eight tables")
    if contract.controls.exp3_comparison_kind != "paired_trace_reference":
        raise ValueError("Exp3 comparison kind contract drift")


def _canonical_observations(
    contract: PaperMetricContract,
    observations: Sequence[PaperMetricObservation],
    *,
    observations_digest: str,
) -> tuple[PaperMetricObservation, ...]:
    table_order = {table.table_id: index for index, table in enumerate(contract.tables)}
    column_order = {
        table.table_id: {field: index for index, field in enumerate(table.numeric_output_fields)}
        for table in contract.tables
    }
    unique: set[tuple[str, str, str]] = set()
    values: list[PaperMetricObservation] = []
    for item in observations:
        if not isinstance(item, PaperMetricObservation):
            raise TypeError("renderer accepts only PaperMetricObservation values")
        if (
            item.metric_contract_id != contract.contract_id
            or item.metric_contract_digest != contract.contract_digest
        ):
            raise ValueError("observation contract digest mismatch")
        if (
            item.pipeline_profile_id != contract.pipeline_profile_id
            or item.pipeline_profile_digest != contract.pipeline_profile_digest
        ):
            raise ValueError("observation pipeline profile mismatch")
        table = contract.require_table(item.table_id)
        if item.row_kind not in table.row_kinds:
            raise ValueError("uncontracted observation row kind")
        if item.column_id not in table.numeric_output_fields:
            raise ValueError(f"uncontracted numeric output field: {item.column_id}")
        metric = contract.require_metric(item.table_id, item.metric_id)
        if (
            item.column_id != item.metric_id
            or item.formula_id != metric.formula_id
            or item.row_kind != metric.row_kind
        ):
            raise ValueError("observation metric identity mismatch")
        expected_row_fields = _ROW_KEY_FIELDS.get((item.table_id, item.row_kind))
        if expected_row_fields is None or set(item.row_key) != set(expected_row_fields):
            raise ValueError(
                f"contracted row key schema mismatch: {item.table_id}.{item.row_kind}"
            )
        body = _observation_body(item)
        _validate_no_unsafe_content(body, "observation")
        expected_identity = _digest_json(
            {
                "schema_version": "tokenshare.paper_metric_observation_identity.v1",
                "contract_digest": contract.contract_digest,
                "profile_digest": contract.pipeline_profile_digest,
                "source_index_digest": item.source_index_digest,
                "table_id": item.table_id,
                "row_kind": item.row_kind,
                "row_identity_digest": item.row_identity_digest,
                "column_id": item.column_id,
                "metric_id": item.metric_id,
                "formula_id": item.formula_id,
            }
        )
        if item.observation_id != expected_identity:
            raise ValueError("observation id mismatch")
        if item.observation_digest != _digest_json(body):
            raise ValueError("observation digest mismatch")
        _validate_observation_evidence(metric, item)
        if item.numeric_value is None:
            if not item.null_reason:
                raise ValueError("null observation requires an explicit reason")
        elif isinstance(item.numeric_value, bool) or not isinstance(
            item.numeric_value, (Decimal, int)
        ):
            raise TypeError("observation numeric value must be Decimal, int, or null")
        key = (item.table_id, item.row_identity_digest, item.column_id)
        if key in unique:
            raise ValueError("duplicate paper metric observation cell")
        unique.add(key)
        values.append(item)
    collection_body = [
        {**_observation_body(item), "observation_digest": item.observation_digest}
        for item in observations
    ]
    if observations_digest != _digest_json(collection_body):
        raise ValueError("observation collection digest mismatch")
    return tuple(
        sorted(
            values,
            key=lambda item: (
                table_order[item.table_id],
                item.row_identity_digest,
                item.row_kind,
                column_order[item.table_id][item.column_id],
                item.observation_id,
            ),
        )
    )


def _observation_body(item: PaperMetricObservation) -> dict[str, Any]:
    return {
        field.name: _json_value(getattr(item, field.name))
        for field in fields(item)
        if field.name != "observation_digest"
    }


def _validate_observation_evidence(metric: Any, item: PaperMetricObservation) -> None:
    if item.evidence_class not in metric.evidence_classes:
        raise ValueError("observation evidence class is not allowed by contract")
    required_current_provider_roles, required_source_bank_roles = (
        metric.required_roles_for(item.evidence_class)
    )
    if item.required_current_provider_roles != required_current_provider_roles:
        raise ValueError("required current provider roles contract mismatch")
    if item.required_source_bank_roles != required_source_bank_roles:
        raise ValueError("required source bank roles contract mismatch")

    if item.evidence_class == "online_real_provider":
        if item.source_bank_object_locators:
            raise ValueError("online observation contains source bank locators")
        current = _covered_compact_or_inline_roles(
            item,
            required_current_provider_roles,
            _covered_provider_roles,
            item.covered_current_provider_roles,
        )
        source: tuple[str, ...] = ()
        expected_not_applicable = ("source_bank_object_locators",)
    elif item.evidence_class == "real_model_trace_protocol_run":
        if item.current_provider_object_refs:
            raise ValueError("trace observation contains current provider object refs")
        current = ()
        source = _covered_compact_or_inline_roles(
            item,
            required_source_bank_roles,
            _covered_source_roles,
            item.covered_source_bank_roles,
        )
        expected_not_applicable = ("current_provider_object_refs",)
    else:
        current = ()
        source = ()
        expected_not_applicable = ()
    if item.covered_current_provider_roles != current:
        raise ValueError("covered current provider roles do not match typed refs")
    if item.covered_source_bank_roles != source:
        raise ValueError("covered source bank roles do not match typed refs")
    if item.not_applicable_evidence_roles != expected_not_applicable:
        raise ValueError("not-applicable evidence roles mismatch")


def _covered_roles_or_blocked(
    item: PaperMetricObservation,
    validator: Any,
) -> tuple[str, ...]:
    try:
        return validator(item)
    except ValueError:
        if (
            item.publish_blocked
            and item.numeric_value is None
            and isinstance(item.null_reason, str)
            and item.null_reason.startswith("missing_required_lineage:")
        ):
            return ()
        raise


def _covered_compact_or_inline_roles(
    item: PaperMetricObservation,
    required_roles: tuple[str, ...],
    inline_validator: Any,
    covered_roles: tuple[str, ...],
) -> tuple[str, ...]:
    """Compact observation 用 digest-bound source-record refs 承载 closure。"""

    missing_lineage_blocker = (
        item.publish_blocked
        and item.numeric_value is None
        and isinstance(item.null_reason, str)
        and (
            item.null_reason.startswith("missing_required_lineage:")
            or item.null_reason.startswith("missing_declared_root_lineage")
            or item.null_reason.startswith("missing_exact_root_source_facts")
            or item.null_reason.startswith("root_source_lineage_")
        )
    )
    if not item.lineage_source_record_refs:
        if not covered_roles and missing_lineage_blocker:
            return ()
        return _covered_roles_or_blocked(item, inline_validator)
    if not required_roles:
        if covered_roles:
            raise ValueError("covered lineage roles exist without contract roles")
        return ()
    if covered_roles == required_roles:
        return required_roles
    if not covered_roles and missing_lineage_blocker:
        return ()
    raise ValueError("compact observation covered lineage roles mismatch")


def _covered_provider_roles(item: PaperMetricObservation) -> tuple[str, ...]:
    refs = item.current_provider_object_refs
    if any(not isinstance(ref, ArtifactIdentitySnapshot) for ref in refs):
        raise TypeError("current provider refs must be typed snapshots")
    identities = _required_lineage_identities(item, provider=True)
    return _validate_roles_per_identity(
        item.required_current_provider_roles,
        identities,
        refs,
        identity_attribute="source_execution_id",
        role_attribute="source_role",
    )


def _covered_source_roles(item: PaperMetricObservation) -> tuple[str, ...]:
    refs = item.source_bank_object_locators
    if any(not isinstance(ref, ExternalBankObjectLocator) for ref in refs):
        raise TypeError("source bank refs must be typed locators")
    identities = _required_lineage_identities(item, provider=False)
    return _validate_roles_per_identity(
        item.required_source_bank_roles,
        identities,
        refs,
        identity_attribute="entry_id",
        role_attribute="object_role",
    )


def _required_lineage_identities(
    item: PaperMetricObservation,
    *,
    provider: bool,
) -> tuple[str, ...]:
    provider_kinds = {
        "actual_provider_attempt", "actual_first_provider_attempt",
        "exp2_online_first_provider_attempt", "exp5_provider_attempt",
        "online_recovery_original_attempt", "online_recovery_replacement_attempt",
    }
    source_kinds = {
        "committed_trace_consumption", "trace_consumption",
        "exp3_discarded_trace_consumption",
    }
    result: list[str] = []
    for member_id in item.denominator_inventory_ids:
        facts = item.member_facts_by_id.get(member_id, {})
        if provider and facts.get("member_kind") in provider_kinds:
            identity = next(
                (
                    facts[name]
                    for name in (
                        "provider_attempt_id", "current_attempt_id",
                        "replacement_attempt_id", "original_attempt_id", "attempt_id",
                    )
                    if isinstance(facts.get(name), str) and facts[name]
                ),
                member_id,
            )
            result.append(str(identity))
        elif not provider and facts.get("member_kind") in source_kinds:
            identity = facts.get("source_bank_entry_id")
            if isinstance(identity, str) and identity:
                result.append(identity)
    return tuple(dict.fromkeys(result))


def _validate_roles_per_identity(
    required_roles: Sequence[str],
    identities: Sequence[str],
    refs: Sequence[Any],
    *,
    identity_attribute: str,
    role_attribute: str,
) -> tuple[str, ...]:
    if not required_roles:
        return ()
    if not identities:
        raise ValueError(f"missing required lineage: {required_roles[0]}")
    groups: dict[str, list[str]] = {identity: [] for identity in identities}
    for ref in refs:
        identity = getattr(ref, identity_attribute)
        if identity not in groups:
            raise ValueError(f"invalid lineage identity: {identity}")
        role = getattr(ref, role_attribute)
        if role in {"raw_output", "provider_failure"}:
            role = "raw_output_or_provider_failure"
        elif role == "usage":
            role = "usage_status"
        groups[identity].append(role)
    for identity, roles in groups.items():
        for required in required_roles:
            if roles.count(required) != 1:
                raise ValueError(f"missing required lineage: {identity}:{required}")
    return tuple(required_roles)


def _validate_no_unsafe_content(value: Any, field_path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_path} contains a non-string field name")
            normalized = key.strip().lower().replace("-", "_")
            safe_reference_key = normalized.endswith("_ref")
            unsafe = (
                normalized in _UNSAFE_PAYLOAD_FIELDS
                or "chain_of_thought" in normalized
                or (
                    not safe_reference_key
                    and any(marker in normalized for marker in ("raw", "reasoning", "output", "token"))
                    and any(marker in normalized for marker in ("payload", "content", "text", "body"))
                )
            )
            if unsafe:
                raise ValueError(f"{field_path}.{key} contains raw reasoning/output payload")
            _validate_no_unsafe_content(nested, f"{field_path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_no_unsafe_content(nested, f"{field_path}[{index}]")
        return
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        final_field = field_path.rsplit(".", 1)[-1].split("[", 1)[0]
        safe_reference_label = final_field in {
            "artifact_type",
            "source_role",
            "object_role",
        }
        if not safe_reference_label and (
            normalized in _UNSAFE_PAYLOAD_FIELDS
            or "chain_of_thought" in normalized
            or any(
                marker in normalized
                for marker in (
                    "raw_reasoning",
                    "reasoning_content",
                    "raw_output_text",
                    "raw_output_payload",
                )
            )
        ):
            raise ValueError(f"{field_path} contains raw reasoning/output payload")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field_path} contains a non-finite value")


def _table_rows(
    numeric_fields: Sequence[str],
    expected_fields_by_row_kind: Mapping[str, Sequence[str]],
    observations: Sequence[PaperMetricObservation],
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
    row_keys: set[str] = set()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    observed_fields: dict[tuple[str, str], set[str]] = {}
    identities: dict[tuple[str, str], tuple[tuple[str, Any], ...]] = {}
    for item in observations:
        for key in item.row_key:
            if not isinstance(key, str) or not key:
                raise ValueError("observation row key names must be non-empty strings")
            if key == "row_kind" or key in numeric_fields:
                raise ValueError("observation row key collides with renderer column")
            row_keys.add(key)
        group_key = (item.row_identity_digest, item.row_kind)
        canonical_key = tuple(
            (key, _json_value(value)) for key, value in sorted(item.row_key.items())
        )
        previous = identities.setdefault(group_key, canonical_key)
        if previous != canonical_key:
            raise ValueError("row identity maps to conflicting row keys")
        row = grouped.setdefault(
            group_key,
            {"row_kind": item.row_kind, **dict(item.row_key)},
        )
        row[item.column_id] = item.numeric_value
        observed_fields.setdefault(group_key, set()).add(item.column_id)
    for group_key, actual_fields in observed_fields.items():
        expected_fields = set(expected_fields_by_row_kind[group_key[1]])
        if actual_fields != expected_fields:
            missing = sorted(expected_fields - actual_fields)
            raise ValueError(
                "missing contracted observation cell: " + ",".join(missing)
            )
    key_fields = tuple(sorted(row_keys))
    headers = ("row_kind", *key_fields, *numeric_fields)
    rows = tuple(
        {
            header: grouped[group_key].get(header)
            for header in headers
        }
        for group_key in sorted(grouped)
    )
    return headers, rows


def _caption(contract: PaperMetricContract, table_id: str) -> str:
    descriptions = {
        "exp1_feasibility": "Experiment 1 feasibility metrics from current online observations.",
        "exp2_trace_scalability": "Experiment 2 scalability from immutable real-model trace protocol runs.",
        "exp2_online_concurrency": "Experiment 2 preregistered actual online concurrency check.",
        "exp3_trace_robustness": (
            "Experiment 3 trace robustness; "
            f"comparison_kind={contract.controls.exp3_comparison_kind}; "
            "trace-reference timing is not per-condition actual online timing and "
            "not per-condition paid timing."
        ),
        "exp3_online_recovery": "Experiment 3 preregistered actual online recovery chains.",
        "exp4_ablation": "Experiment 4 paired protocol ablation observations.",
        "exp5_quality": (
            "Experiment 5 endpoint quality comparison; endpoint and serving profile are "
            "confounding factors, so this is not a pure model effect."
        ),
        "exp5_resources": (
            "Experiment 5 endpoint resources; endpoint and serving profile are confounding "
            "factors, so this is not a pure model effect."
        ),
    }
    caption = descriptions[table_id]
    table = contract.require_table(table_id)
    if any("cost" in field for field in table.numeric_output_fields):
        caption += " Cost estimate = usage × frozen-pricing estimate."
    return caption


def _audit_record(item: PaperMetricObservation) -> dict[str, Any]:
    # 显式字段投影避免调用 observation.to_dict() 或遍历 raw/reasoning payload。
    return {
        "schema_version": "tokenshare.paper_metric_renderer_audit_row.v1",
        "observation_id": item.observation_id,
        "observation_digest": item.observation_digest,
        "metric_contract_digest": item.metric_contract_digest,
        "source_index_id": item.source_index_id,
        "source_index_digest": item.source_index_digest,
        "table_id": item.table_id,
        "row_kind": item.row_kind,
        "row_key": _json_value(item.row_key),
        "row_identity_digest": item.row_identity_digest,
        "column_id": item.column_id,
        "metric_id": item.metric_id,
        "formula_id": item.formula_id,
        "numeric_value": _json_value(item.numeric_value),
        "null_reason": item.null_reason,
        "publish_blocked": item.publish_blocked,
        "numerator_membership_ids": list(item.numerator_membership_ids),
        "denominator_inventory_ids": list(item.denominator_inventory_ids),
        "audit_denominator_member_ids": list(item.audit_denominator_member_ids),
        "excluded_member_ids": list(item.excluded_member_ids),
        "blocked_member_ids": list(item.blocked_member_ids),
        "exclusion_reasons": dict(item.exclusion_reasons),
        "blocked_reasons": list(item.blocked_reasons),
        "required_current_provider_roles": list(item.required_current_provider_roles),
        "required_source_bank_roles": list(item.required_source_bank_roles),
        "covered_current_provider_roles": list(item.covered_current_provider_roles),
        "covered_source_bank_roles": list(item.covered_source_bank_roles),
        "not_applicable_evidence_roles": list(item.not_applicable_evidence_roles),
        "evidence_class": item.evidence_class,
        "artifact_and_event_refs": [
            *_safe_references(item.direct_result_refs),
            *_safe_references(item.current_task_attempt_event_refs),
            *_safe_references(item.parser_verifier_checker_canonical_refs),
            *_safe_references(item.ledger_refs),
            *_safe_references(item.current_provider_object_refs),
            *_safe_references(item.source_bank_object_locators),
        ],
    }


def _safe_references(values: Sequence[Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for value in values:
        projected: dict[str, Any] = {}
        for field in _SAFE_REFERENCE_FIELDS:
            if isinstance(value, Mapping):
                if field in value:
                    projected[field] = _json_value(value[field])
            elif hasattr(value, field):
                projected[field] = _json_value(getattr(value, field))
        if projected:
            refs.append(projected)
    return sorted(refs, key=_canonical_json)


def _csv_text(headers: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(headers), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _cell_text(row.get(field)) for field in headers})
    return output.getvalue()


def _tex_fragment(
    headers: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    caption: str,
) -> str:
    lines = [
        f"% caption: {caption}",
        "\\begin{tabular}{" + "l" * len(headers) + "}",
        " & ".join(_latex_escape(field) for field in headers) + r" \\",
        r"\hline",
    ]
    lines.extend(
        " & ".join(_latex_escape(_cell_text(row.get(field))) for field in headers)
        + r" \\" for row in rows
    )
    lines.append(r"\end{tabular}")
    return "\n".join(lines) + "\n"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return _canonical_json(_json_value(value))
    return str(value)


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


def _latex_escape(value: str) -> str:
    result = value
    for source, replacement in (
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
    ):
        result = result.replace(source, replacement)
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_json(value: Any) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _content_ref(relative_path: str, content: str) -> dict[str, str]:
    return {
        "path": relative_path,
        "content_hash": "sha256:" + sha256(content.encode("utf-8")).hexdigest(),
    }


def _promote_generation(root: Path, contents: Mapping[str, str]) -> None:
    """完整 staging 后再替换 owned files；异常时恢复上一代。"""

    expected = {
        *PAPER_TABLE_CSV_FILES,
        *PAPER_TABLE_TEX_FILES,
        PAPER_RENDERER_AUDIT_FILE,
        PAPER_RENDERER_MANIFEST_FILE,
    }
    if set(contents) != expected:
        raise ValueError("renderer generation does not contain the complete owned set")
    owned = tuple(sorted({*expected, *_RETIRED_OUTPUTS}))
    present_before = {
        relative_path for relative_path in owned if (root / relative_path).is_file()
    }
    for relative_path in owned:
        target = root / relative_path
        if target.exists() and not target.is_file():
            raise ValueError(f"renderer owned path is not a file: {relative_path}")

    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".paper-metric-stage-", dir=root))
    backup = Path(tempfile.mkdtemp(prefix=".paper-metric-backup-", dir=root))
    moved_to_backup: list[str] = []
    try:
        for relative_path, content in contents.items():
            _write_text(stage / relative_path, content)
        for relative_path in expected:
            staged = stage / relative_path
            if not staged.is_file():
                raise RuntimeError(f"staged renderer output is missing: {relative_path}")
            if _content_ref(relative_path, staged.read_text(encoding="utf-8")) != _content_ref(
                relative_path, contents[relative_path]
            ):
                raise RuntimeError(f"staged renderer output digest mismatch: {relative_path}")

        for relative_path in owned:
            target = root / relative_path
            if target.is_file():
                saved = backup / relative_path
                saved.parent.mkdir(parents=True, exist_ok=True)
                target.replace(saved)
                moved_to_backup.append(relative_path)

        promotion_order = tuple(
            sorted(expected - {PAPER_RENDERER_MANIFEST_FILE})
        ) + (PAPER_RENDERER_MANIFEST_FILE,)
        for relative_path in promotion_order:
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            (stage / relative_path).replace(target)
    except Exception:
        for relative_path in expected:
            target = root / relative_path
            if relative_path not in present_before and target.is_file():
                target.unlink()
        for relative_path in reversed(moved_to_backup):
            saved = backup / relative_path
            target = root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if saved.is_file():
                saved.replace(target)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def _require_digest(value: Any, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(path)


__all__ = [
    "PAPER_RENDERER_AUDIT_FILE",
    "PAPER_RENDERER_MANIFEST_FILE",
    "PAPER_TABLE_CSV_FILES",
    "PAPER_TABLE_TEX_FILES",
    "PaperMetricArtifactResult",
    "render_paper_metric_artifacts",
]
