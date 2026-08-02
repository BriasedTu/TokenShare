"""Contract-bound registry for the standalone Experiment 1-5 projectors."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any

from tokenshare.experiments.paper_exp1_metrics import build_exp1_observations
from tokenshare.experiments.paper_exp2_metrics import (
    build_exp2_online_observations,
    build_exp2_trace_observations,
)
from tokenshare.experiments.paper_exp3_metrics import (
    project_exp3_online_recovery,
    project_exp3_trace_condition,
)
from tokenshare.experiments.paper_exp4_metrics import build_exp4_observations
from tokenshare.experiments.paper_exp5_metrics import build_exp5_observations
from tokenshare.experiments.paper_metric_contract import (
    PaperMetricContract,
    load_paper_metric_contract,
)


GLOBAL_INFRA_INVALID_REASON = "global_infrastructure_invalid"
EXP5_INPUT_KEY = "experiment_5"


@dataclass(frozen=True, kw_only=True)
class DraftMetricRow:
    """A projector row tagged with its contracted row kind."""

    row_kind: str
    source_row: object

    @property
    def cells(self) -> tuple[object, ...]:
        value = getattr(self.source_row, "cells", None)
        if not isinstance(value, tuple):
            raise TypeError("projector row must expose tuple cells")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_kind": self.row_kind,
            "row": _json_value(self.source_row),
        }


@dataclass(frozen=True, kw_only=True)
class MetricTableDraft:
    """Intermediate payload whose target path and fields come from the contract."""

    table_id: str
    experiment_id: str
    output_path: str
    numeric_output_fields: tuple[str, ...]
    rows: tuple[DraftMetricRow, ...]
    projector_name: str
    recompute_only: bool = True
    paper_eligible: bool = False
    schema_version: str = "tokenshare.paper_metric_table_draft.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_id": self.table_id,
            "experiment_id": self.experiment_id,
            "target_output_path": self.output_path,
            "numeric_output_fields": list(self.numeric_output_fields),
            "projector_name": self.projector_name,
            "recompute_only": self.recompute_only,
            "paper_eligible": self.paper_eligible,
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(frozen=True, kw_only=True)
class ProjectorRegistration:
    table_id: str
    experiment_id: str
    output_path: str
    numeric_output_fields: tuple[str, ...]
    input_key: str
    projector: Callable[..., object]
    selector: Callable[[object], tuple[DraftMetricRow, ...]]
    sequence_projector: bool = False


class PaperMetricRegistry:
    """Serial dispatcher; metric arithmetic remains inside accepted projectors."""

    def __init__(
        self,
        contract: PaperMetricContract,
        registrations: Sequence[ProjectorRegistration],
    ) -> None:
        if not isinstance(contract, PaperMetricContract):
            raise TypeError("contract must be PaperMetricContract")
        normalized = tuple(registrations)
        table_ids = tuple(item.table_id for item in normalized)
        contracted_ids = tuple(table.table_id for table in contract.tables)
        if table_ids != contracted_ids or len(set(table_ids)) != len(table_ids):
            raise ValueError("registry must contain exactly one projector per contract table")
        self.contract = contract
        self.registrations = normalized

    @property
    def required_input_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.input_key for item in self.registrations))

    def require_registration(self, table_id: str) -> ProjectorRegistration:
        matches = tuple(item for item in self.registrations if item.table_id == table_id)
        if len(matches) != 1:
            raise ValueError(f"unknown projector registration: {table_id}")
        return matches[0]

    def project_table(
        self,
        table_id: str,
        payload: object,
        *,
        global_infra_invalid: bool = False,
    ) -> MetricTableDraft:
        registration = self.require_registration(table_id)
        projection = _invoke(registration, payload, self.contract)
        draft = _draft(registration, projection, global_infra_invalid)
        self.validate_draft(draft)
        return draft

    def project_all(
        self,
        inputs: Mapping[str, object],
        *,
        global_infra_invalid: bool = False,
    ) -> tuple[MetricTableDraft, ...]:
        if not isinstance(inputs, Mapping):
            raise TypeError("canonical projector inputs must be a mapping")
        required = self.required_input_keys
        if set(inputs) != set(required):
            raise ValueError("canonical projector input keys must exactly match registry")
        projections: dict[tuple[str, Callable[..., object]], object] = {}
        drafts: list[MetricTableDraft] = []
        for registration in self.registrations:
            cache_key = (registration.input_key, registration.projector)
            if cache_key not in projections:
                projections[cache_key] = _invoke(
                    registration,
                    inputs[registration.input_key],
                    self.contract,
                )
            draft = _draft(
                registration,
                projections[cache_key],
                global_infra_invalid,
            )
            self.validate_draft(draft)
            drafts.append(draft)
        return tuple(drafts)

    def validate_draft(self, draft: MetricTableDraft) -> None:
        if not isinstance(draft, MetricTableDraft):
            raise TypeError("draft must be MetricTableDraft")
        table = self.contract.require_table(draft.table_id)
        if draft.experiment_id != table.experiment_id:
            raise ValueError("metric draft experiment id contract drift")
        if draft.output_path != table.output_path:
            raise ValueError("metric draft output path contract drift")
        self.contract.validate_numeric_output_fields(
            draft.table_id,
            draft.numeric_output_fields,
        )
        for row in draft.rows:
            if row.row_kind not in table.row_kinds:
                raise ValueError(f"uncontracted metric row kind: {row.row_kind}")
            metric_ids = tuple(str(getattr(cell, "metric_id", "")) for cell in row.cells)
            expected = tuple(
                metric.metric_id
                for metric in self.contract.metrics
                if metric.table == draft.table_id and metric.row_kind == row.row_kind
            )
            self.contract.validate_registry_metric_keys(
                (draft.table_id, metric_id) for metric_id in metric_ids
            )
            if len(set(metric_ids)) != len(metric_ids):
                raise ValueError("duplicate projector cell field")
            if metric_ids != expected:
                raise ValueError(
                    f"projector cell field set contract drift: {draft.table_id}.{row.row_kind}"
                )


def load_paper_metric_registry(
    contract: PaperMetricContract | None = None,
) -> PaperMetricRegistry:
    current = contract or load_paper_metric_contract()
    specs = _registration_specs()
    registrations: list[ProjectorRegistration] = []
    for table in current.tables:
        try:
            input_key, projector, selector, sequence_projector = specs[table.table_id]
        except KeyError as exc:
            raise ValueError(f"missing projector registration: {table.table_id}") from exc
        registrations.append(
            ProjectorRegistration(
                table_id=table.table_id,
                experiment_id=table.experiment_id,
                output_path=table.output_path,
                numeric_output_fields=table.numeric_output_fields,
                input_key=input_key,
                projector=projector,
                selector=selector,
                sequence_projector=sequence_projector,
            )
        )
    if set(specs) != {table.table_id for table in current.tables}:
        raise ValueError("projector registry contains an uncontracted table")
    return PaperMetricRegistry(current, registrations)


def _registration_specs() -> Mapping[
    str,
    tuple[
        str,
        Callable[..., object],
        Callable[[object], tuple[DraftMetricRow, ...]],
        bool,
    ],
]:
    return MappingProxyType(
        {
            "exp1_feasibility": (
                "exp1_feasibility",
                build_exp1_observations,
                _select_exp1,
                False,
            ),
            "exp2_trace_scalability": (
                "exp2_trace_scalability",
                build_exp2_trace_observations,
                _select_exp2_trace,
                False,
            ),
            "exp2_online_concurrency": (
                "exp2_online_concurrency",
                build_exp2_online_observations,
                _select_exp2_online,
                False,
            ),
            "exp3_trace_robustness": (
                "exp3_trace_robustness",
                project_exp3_trace_condition,
                _select_exp3_trace,
                True,
            ),
            "exp3_online_recovery": (
                "exp3_online_recovery",
                project_exp3_online_recovery,
                _select_exp3_online,
                True,
            ),
            "exp4_ablation": (
                "exp4_ablation",
                build_exp4_observations,
                _select_exp4,
                False,
            ),
            "exp5_quality": (
                EXP5_INPUT_KEY,
                build_exp5_observations,
                _select_exp5_quality,
                False,
            ),
            "exp5_resources": (
                EXP5_INPUT_KEY,
                build_exp5_observations,
                _select_exp5_resources,
                False,
            ),
        }
    )


def _invoke(
    registration: ProjectorRegistration,
    payload: object,
    contract: PaperMetricContract,
) -> object:
    if registration.sequence_projector:
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise TypeError("sequence projector input must be a sequence")
        return tuple(registration.projector(item, contract) for item in payload)
    return registration.projector(payload, contract)


def _draft(
    registration: ProjectorRegistration,
    projection: object,
    global_infra_invalid: bool,
) -> MetricTableDraft:
    rows = registration.selector(projection)
    if global_infra_invalid:
        rows = tuple(_block_row(row) for row in rows)
    return MetricTableDraft(
        table_id=registration.table_id,
        experiment_id=registration.experiment_id,
        output_path=registration.output_path,
        numeric_output_fields=registration.numeric_output_fields,
        rows=rows,
        projector_name=(
            f"{registration.projector.__module__}.{registration.projector.__name__}"
        ),
    )


def _block_row(row: DraftMetricRow) -> DraftMetricRow:
    blocked_cells = tuple(
        replace(
            cell,
            value=None,
            reason=GLOBAL_INFRA_INVALID_REASON,
            publish_blocked=True,
            paper_eligible=False,
        )
        for cell in row.cells
    )
    return replace(row, source_row=replace(row.source_row, cells=blocked_cells))


def _select_exp1(value: object) -> tuple[DraftMetricRow, ...]:
    return _tag_rows("condition_summary", value)


def _select_exp2_trace(value: object) -> tuple[DraftMetricRow, ...]:
    return (
        *_tag_rows("worker_repeat_observation", getattr(value, "worker_repeat_rows")),
        *_tag_rows("paired_worker_comparison", getattr(value, "pair_rows")),
        *_tag_rows("repeat_summary", getattr(value, "repeat_summary_rows")),
        *_tag_rows("condition_summary", getattr(value, "condition_summary_rows")),
    )


def _select_exp2_online(value: object) -> tuple[DraftMetricRow, ...]:
    return _tag_rows("worker_condition_summary", getattr(value, "rows"))


def _select_exp3_trace(value: object) -> tuple[DraftMetricRow, ...]:
    rows: list[DraftMetricRow] = []
    worker_death_fields = (
        "kill_progress_error_signed_mean_pp",
        "kill_progress_error_signed_max_pp",
    )
    for source_row in _row_sequence(value):
        rows.append(
            _tag_filtered_row(
                "condition_observation",
                source_row,
                excluded_metric_ids=worker_death_fields,
            )
        )
        rows.append(
            _tag_filtered_row(
                "worker_death_summary",
                source_row,
                included_metric_ids=worker_death_fields,
            )
        )
    return tuple(rows)


def _select_exp3_online(value: object) -> tuple[DraftMetricRow, ...]:
    return _tag_rows("online_recovery_summary", value)


def _select_exp4(value: object) -> tuple[DraftMetricRow, ...]:
    return (
        *_tag_rows("mode_summary", getattr(value, "mode_summary_rows")),
        *_tag_rows("paired_transition", getattr(value, "pair_rows")),
        *_tag_rows("mode_specific", getattr(value, "mode_specific_rows")),
    )


def _select_exp5_quality(value: object) -> tuple[DraftMetricRow, ...]:
    return _select_exp5_table(value, "exp5_quality")


def _select_exp5_resources(value: object) -> tuple[DraftMetricRow, ...]:
    return _select_exp5_table(value, "exp5_resources")


def _select_exp5_table(value: object, table_id: str) -> tuple[DraftMetricRow, ...]:
    matches = tuple(
        payload
        for payload in getattr(value, "table_payloads")
        if getattr(payload, "table_id", None) == table_id
    )
    if len(matches) != 1:
        raise ValueError(f"Exp5 projector must provide exactly one {table_id} payload")
    return _tag_rows("model_summary", getattr(matches[0], "rows"))


def _tag_rows(row_kind: str, values: object) -> tuple[DraftMetricRow, ...]:
    return tuple(
        DraftMetricRow(row_kind=row_kind, source_row=row)
        for row in _row_sequence(values)
    )


def _tag_filtered_row(
    row_kind: str,
    source_row: object,
    *,
    included_metric_ids: tuple[str, ...] | None = None,
    excluded_metric_ids: tuple[str, ...] = (),
) -> DraftMetricRow:
    cells = getattr(source_row, "cells", None)
    if not isinstance(cells, tuple):
        raise TypeError("projector row must expose tuple cells")
    included = set(included_metric_ids) if included_metric_ids is not None else None
    excluded = set(excluded_metric_ids)
    selected = tuple(
        cell
        for cell in cells
        if (included is None or getattr(cell, "metric_id", None) in included)
        and getattr(cell, "metric_id", None) not in excluded
    )
    return DraftMetricRow(
        row_kind=row_kind,
        source_row=replace(source_row, cells=selected),
    )


def _row_sequence(values: object) -> Sequence[object]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError("projector selector requires a row sequence")
    return values


def _json_value(value: object) -> Any:
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
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if field.repr
        }
    raise TypeError(f"unsupported projector draft value: {type(value).__name__}")


__all__ = [
    "DraftMetricRow",
    "EXP5_INPUT_KEY",
    "GLOBAL_INFRA_INVALID_REASON",
    "MetricTableDraft",
    "PaperMetricRegistry",
    "ProjectorRegistration",
    "load_paper_metric_registry",
]
