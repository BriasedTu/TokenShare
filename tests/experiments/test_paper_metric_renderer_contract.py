from __future__ import annotations

import csv
from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path

import pytest

from tokenshare.experiments.paper_exp5_artifacts import (
    PAPER_RENDERER_AUDIT_FILE,
    render_paper_metric_artifacts,
)
from tokenshare.experiments.paper_metric_contract import load_paper_metric_contract
from tokenshare.experiments.paper_metric_observations import PaperMetricObservation
from tokenshare.experiments.paper_models import (
    ArtifactIdentitySnapshot,
    ExternalBankObjectLocator,
)


_DIGEST = "sha256:" + "1" * 64


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: object):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict) or hasattr(value, "items"):
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
    raise TypeError(type(value).__name__)


def _observation_body(value: PaperMetricObservation) -> dict[str, object]:
    return {
        field.name: _json_value(getattr(value, field.name))
        for field in fields(value)
        if field.name != "observation_digest"
    }


def _collection_digest(values: tuple[PaperMetricObservation, ...]) -> str:
    return _digest(
        [
            {**_observation_body(value), "observation_digest": value.observation_digest}
            for value in values
        ]
    )


def _resign(value: PaperMetricObservation) -> PaperMetricObservation:
    return replace(value, observation_digest=_digest(_observation_body(value)))


def _complete_publication() -> tuple[PaperMetricObservation, ...]:
    contract = load_paper_metric_contract()
    return tuple(
        _observation(table.table_id, field)
        for table in contract.tables
        for field in table.numeric_output_fields
    )


def _default_row_key(table_id: str, row_kind: str) -> dict[str, object]:
    values = {
        ("exp1_feasibility", "condition_summary"): {
            "domain": "factorization",
            "paper_difficulty": "hard",
            "topic_family": None,
            "repeat_id": 0,
        },
        ("exp2_trace_scalability", "worker_repeat_observation"): {
            "preregistered_root_run_ids": ["root-1"],
            "case_record_digests": ["sha256:" + "2" * 64],
            "factor_position_quantiles": ["q1"],
            "position_stratum": "q1",
            "repeat_id": 0,
            "worker_count": 1,
        },
        ("exp2_trace_scalability", "paired_worker_comparison"): {
            "pair_identity": ["case-1", 0, 1, 3, 0],
            "baseline_root_run_id": "root-w1",
            "compared_root_run_id": "root-w3",
            "position_stratum": "q1",
        },
        ("exp2_trace_scalability", "repeat_summary"): {
            "worker_count": 1,
            "repeat_id": 0,
            "position_stratum": "q1",
        },
        ("exp2_trace_scalability", "condition_summary"): {
            "worker_count": 1,
            "position_stratum": "q1",
        },
        ("exp2_online_concurrency", "worker_condition_summary"): {
            "worker_count": 1,
            "case_position": "first",
            "repeat_id": 0,
        },
        ("exp3_trace_robustness", "condition_observation"): {
            "condition_id": "trace-condition",
            "fault_type": "false_positive",
            "repeat_id": 0,
            "sample_slot_id": "sample-0",
        },
        ("exp3_trace_robustness", "worker_death_summary"): {
            "condition_id": "death-condition",
            "fault_type": "worker_death",
            "repeat_id": 0,
            "sample_slot_id": "sample-0",
        },
        ("exp3_online_recovery", "online_recovery_summary"): {
            "recovery_source": "verifier_rejection",
            "case_id": "task14-online-recovery",
            "repeat_id": 0,
        },
        ("exp4_ablation", "mode_summary"): {
            "domain": "factorization",
            "repeat_id": 0,
            "ablation_mode": "FULL",
            "preregistered_root_run_ids": ["root-1"],
            "ineligibility_reasons": [],
        },
        ("exp4_ablation", "paired_transition"): {
            "pair_identity": ["case-1", 0, 0, ["slot-0"], "NO_VERIFICATION"],
            "case_record_digest": "sha256:" + "3" * 64,
            "domain": "factorization",
            "ablation_mode": "NO_VERIFICATION",
            "full_root_run_id": "root-full",
            "ablation_root_run_id": "root-ablation",
            "ineligibility_reasons": [],
        },
        ("exp4_ablation", "mode_specific"): {
            "domain": "factorization",
            "repeat_id": 0,
            "ablation_mode": "NO_VERIFICATION",
            "ineligibility_reasons": [],
        },
        ("exp5_quality", "model_summary"): {
            "model_arm_ids": ["arm-1"],
            "model_endpoint_identity": None,
            "ineligibility_reasons": [],
        },
        ("exp5_resources", "model_summary"): {
            "model_arm_ids": ["arm-1"],
            "model_endpoint_identity": None,
            "ineligibility_reasons": [],
        },
    }
    return dict(values[(table_id, row_kind)])


def _provider_ref(role: str, member_id: str, index: int) -> ArtifactIdentitySnapshot:
    return ArtifactIdentitySnapshot(
        artifact_id=f"artifact-{member_id}-{index}",
        artifact_type="paper_evidence",
        uri=f"artifacts/{member_id}/{index}.json",
        content_hash="sha256:" + f"{index + 1:x}".zfill(64),
        size_bytes=1,
        media_type="application/json",
        artifact_schema_id="paper.evidence",
        artifact_schema_version="1",
        source_role=role,
        source_task_id="task-1",
        source_execution_id=member_id,
        created_at="2026-08-03T00:00:00Z",
    )


def _source_locator(role: str, member_id: str, index: int) -> ExternalBankObjectLocator:
    return ExternalBankObjectLocator(
        bank_root_id="bank-root",
        manifest_digest="sha256:" + "4" * 64,
        entry_id=member_id,
        object_role=role,
        object_digest="sha256:" + f"{index + 10:x}".zfill(64),
    )


def _observation(
    table_id: str,
    column_id: str,
    *,
    value: Decimal | int | None = Decimal("1.25"),
    null_reason: str | None = None,
    publish_blocked: bool = False,
    row_key: dict[str, object] | None = None,
    denominator: tuple[str, ...] = ("root-1", "root-2"),
    excluded: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    blocked_reasons: tuple[str, ...] = (),
    direct_result_refs: tuple[dict[str, object], ...] = (),
) -> PaperMetricObservation:
    contract = load_paper_metric_contract()
    metric = contract.require_metric(table_id, column_id)
    normalized_row_key = _default_row_key(table_id, metric.row_kind)
    if row_key is not None:
        normalized_row_key.update(row_key)
    row_digest = "sha256:" + sha256(
        f"{table_id}:{metric.row_kind}:{normalized_row_key}".encode()
    ).hexdigest()
    member_facts: dict[str, dict[str, object]] = {}
    provider_refs: list[ArtifactIdentitySnapshot] = []
    source_locators: list[ExternalBankObjectLocator] = []
    evidence_class = metric.evidence_classes[0]
    for member_id in denominator:
        if evidence_class == "online_real_provider":
            member_facts[member_id] = {
                "member_kind": "actual_provider_attempt",
                "provider_attempt_id": member_id,
            }
            provider_refs.extend(
                _provider_ref(role, member_id, index)
                for index, role in enumerate(metric.required_current_provider_roles)
            )
        elif evidence_class == "real_model_trace_protocol_run":
            member_facts[member_id] = {
                "member_kind": "trace_consumption",
                "source_bank_entry_id": member_id,
            }
            source_locators.extend(
                _source_locator(role, member_id, index)
                for index, role in enumerate(metric.required_source_bank_roles)
            )
    provisional = PaperMetricObservation(
        observation_id=_DIGEST,
        observation_digest=_DIGEST,
        metric_contract_id=contract.contract_id,
        metric_contract_digest=contract.contract_digest,
        pipeline_profile_id=contract.pipeline_profile_id,
        pipeline_profile_digest=contract.pipeline_profile_digest,
        source_index_id=_DIGEST,
        source_index_digest=_DIGEST,
        table_id=table_id,
        row_kind=metric.row_kind,
        row_key=normalized_row_key,
        row_identity_digest=row_digest,
        column_id=column_id,
        metric_id=column_id,
        formula_id=metric.formula_id,
        numeric_value=value,
        null_reason=null_reason,
        publish_blocked=publish_blocked,
        numerator_membership_ids=((denominator[0],) if value is not None else ()),
        denominator_inventory_ids=denominator,
        audit_denominator_member_ids=denominator,
        excluded_member_ids=excluded,
        blocked_member_ids=blocked,
        exclusion_reasons={member: "excluded_by_contract" for member in excluded},
        blocked_reasons=blocked_reasons,
        row_facts={},
        member_facts_by_id=member_facts,
        verified_observations=(),
        required_current_provider_roles=metric.required_current_provider_roles,
        required_source_bank_roles=metric.required_source_bank_roles,
        covered_current_provider_roles=metric.required_current_provider_roles,
        covered_source_bank_roles=metric.required_source_bank_roles,
        not_applicable_evidence_roles=(
            ("source_bank_object_locators",)
            if evidence_class == "online_real_provider"
            else ("current_provider_object_refs",)
        ),
        evidence_class=evidence_class,
        direct_result_refs=direct_result_refs,
        current_task_attempt_event_refs=(),
        parser_verifier_checker_canonical_refs=(),
        ledger_refs=(),
        current_provider_object_refs=tuple(provider_refs),
        source_bank_object_locators=tuple(source_locators),
        current_trace_wrappers=(),
        trace_source_bindings=(),
    )
    identity = {
        "schema_version": "tokenshare.paper_metric_observation_identity.v1",
        "contract_digest": contract.contract_digest,
        "profile_digest": contract.pipeline_profile_digest,
        "source_index_digest": provisional.source_index_digest,
        "table_id": table_id,
        "row_kind": metric.row_kind,
        "row_identity_digest": row_digest,
        "column_id": column_id,
        "metric_id": column_id,
        "formula_id": metric.formula_id,
    }
    identified = replace(provisional, observation_id=_digest(identity))
    return replace(identified, observation_digest=_digest(_observation_body(identified)))


def test_renderer_accepts_only_observations_allowed_by_contract(tmp_path: Path) -> None:
    contract = load_paper_metric_contract()
    observation = _observation("exp1_feasibility", "completion_rate")
    complete_publication = _complete_publication()

    result = render_paper_metric_artifacts(
        output_root=tmp_path,
        contract=contract,
        observations=complete_publication,
        observations_digest=_collection_digest(complete_publication),
    )

    assert result.table_count == 8
    partial = tuple(
        item
        for item in complete_publication
        if not (
            item.table_id == observation.table_id
            and item.column_id == observation.column_id
        )
    )
    with pytest.raises(ValueError, match="missing contracted observation cell"):
        render_paper_metric_artifacts(
            output_root=tmp_path / "partial",
            contract=contract,
            observations=partial,
            observations_digest=_collection_digest(partial),
        )
    with pytest.raises(ValueError, match="uncontracted numeric output field"):
        bad_column = replace(observation, column_id="accepted_validity_rate")
        bad_columns = tuple(
            bad_column
            if item.table_id == observation.table_id
            and item.column_id == observation.column_id
            else item
            for item in complete_publication
        )
        render_paper_metric_artifacts(
            output_root=tmp_path / "bad-column",
            contract=contract,
            observations=bad_columns,
            observations_digest=_collection_digest(bad_columns),
        )
    with pytest.raises(ValueError, match="contract digest"):
        bad_contract = replace(
            observation, metric_contract_digest="sha256:" + "9" * 64
        )
        bad_contracts = tuple(
            bad_contract
            if item.table_id == observation.table_id
            and item.column_id == observation.column_id
            else item
            for item in complete_publication
        )
        render_paper_metric_artifacts(
            output_root=tmp_path / "bad-contract",
            contract=contract,
            observations=bad_contracts,
            observations_digest=_collection_digest(bad_contracts),
        )


def test_renderer_preserves_null_denominator_and_reason(tmp_path: Path) -> None:
    contract = load_paper_metric_contract()
    observation = _observation(
        "exp3_online_recovery",
        "wasted_actual_tokens",
        value=None,
        null_reason="actual_usage_missing",
        publish_blocked=True,
        denominator=("attempt-1", "attempt-2"),
        excluded=("attempt-2",),
        blocked=("attempt-1",),
        blocked_reasons=("missing_current_usage_role",),
    )
    observations = tuple(
        observation
        if item.table_id == "exp3_online_recovery"
        and item.column_id == "wasted_actual_tokens"
        else item
        for item in _complete_publication()
    )

    result = render_paper_metric_artifacts(
        output_root=tmp_path,
        contract=contract,
        observations=observations,
        observations_digest=_collection_digest(observations),
    )

    with (tmp_path / "metrics" / "paper_table_recovery_online.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["wasted_actual_tokens"] == ""
    audit = next(
        json.loads(line)
        for line in (tmp_path / PAPER_RENDERER_AUDIT_FILE)
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["column_id"] == "wasted_actual_tokens"
    )
    assert audit["observation_id"] == observation.observation_id
    assert audit["null_reason"] == "actual_usage_missing"
    assert audit["denominator_inventory_ids"] == ["attempt-1", "attempt-2"]
    assert audit["excluded_member_ids"] == ["attempt-2"]
    assert audit["blocked_member_ids"] == ["attempt-1"]
    assert audit["blocked_reasons"] == ["missing_current_usage_role"]
    assert result.paper_eligible is False
    assert result.blocked_table_ids == ("exp3_online_recovery",)


def test_incomplete_or_empty_task20_publication_cannot_publish(tmp_path: Path) -> None:
    contract = load_paper_metric_contract()
    empty: tuple[PaperMetricObservation, ...] = ()
    subset = tuple(
        item for item in _complete_publication() if item.table_id != "exp5_resources"
    )

    for name, observations in (("empty", empty), ("subset", subset)):
        with pytest.raises(ValueError, match="exact non-empty eight-table"):
            render_paper_metric_artifacts(
                output_root=tmp_path / name,
                contract=contract,
                observations=observations,
                observations_digest=_collection_digest(observations),
            )
        assert not (tmp_path / name / "metrics").exists()


def test_renderer_rejects_tampered_observation_and_collection_digests(
    tmp_path: Path,
) -> None:
    contract = load_paper_metric_contract()
    observations = _complete_publication()
    tampered = (
        replace(observations[0], numeric_value=Decimal("999")),
        *observations[1:],
    )

    with pytest.raises(ValueError, match="observation digest mismatch"):
        render_paper_metric_artifacts(
            output_root=tmp_path / "value",
            contract=contract,
            observations=tampered,
            observations_digest=_collection_digest(tampered),
        )
    with pytest.raises(ValueError, match="collection digest mismatch"):
        render_paper_metric_artifacts(
            output_root=tmp_path / "collection",
            contract=contract,
            observations=observations,
            observations_digest=_DIGEST,
        )

    wrong_class = _resign(replace(observations[0], evidence_class="regression_only"))
    with pytest.raises(ValueError, match="evidence class"):
        render_paper_metric_artifacts(
            output_root=tmp_path / "class",
            contract=contract,
            observations=(wrong_class, *observations[1:]),
            observations_digest=_collection_digest((wrong_class, *observations[1:])),
        )
    wrong_roles = _resign(replace(observations[0], covered_current_provider_roles=()))
    with pytest.raises(ValueError, match="covered current provider roles"):
        render_paper_metric_artifacts(
            output_root=tmp_path / "roles",
            contract=contract,
            observations=(wrong_roles, *observations[1:]),
            observations_digest=_collection_digest((wrong_roles, *observations[1:])),
        )
